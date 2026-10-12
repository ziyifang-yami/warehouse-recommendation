"""
LOC Recommendation — FastAPI backend v2
Scan BT (BT{rec_Id}) or GT (GT…) → auto-resolve SKU → putaway decision.
No separate UPC scan step needed.

Run: uvicorn server:app --host 0.0.0.0 --port 8504
"""

import math
import os
import sqlite3
from datetime import date
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# ---------------------------------------------------------------------------
# DB engine
# ---------------------------------------------------------------------------
_skills_config = Path(__file__).resolve().parent.parent.parent / "skills" / "config"
if str(_skills_config) not in sys.path:
    sys.path.insert(0, str(_skills_config))

try:
    from settings import engine
except ImportError:
    _user = os.environ.get("MYSQL_USER", "")
    _pass = os.environ.get("MYSQL_PASS", "")
    _host = os.environ.get("MYSQL_HOST", "rds.g3.yamibuy.net")
    engine = create_engine(
        f"mysql+pymysql://{_user}:{_pass}@{_host}/yamibuy_wh?charset=utf8mb4",
        pool_pre_ping=True,
    )

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])

# ---------------------------------------------------------------------------
# Putaway config (mirrors NJ Allocation v4 config.py)
# ---------------------------------------------------------------------------
PUTAWAY_CFG = {
    "putaway_d2s": 30,   # MIDDLE: FC D2S > this → new goods to SFC
}

# Segment thresholds (mirrors refresh_sfc_cache.py / config.py)
_SEG_AOD_TOP  = 21    # active_order_days >= 21
_SEG_OL_TOP   = 30    # order_lines_30d   >= 30
_SEG_OL_TAIL  = 8     # order_lines_30d   < 8  → TAIL
_TBD_DAYS     = 30    # inv_days_30 < 30 or NaN → TBD

# Path to Allocation SQLite cache (same server, read-only)
_CACHE_DB = Path("/opt/inv-allocation/results/sfc_cache.db")


def _read_sku_from_cache(sku: str) -> dict:
    """
    Read segment inputs for one SKU from sfc_cache.db.
    Returns dict with keys: inv_days_30, order_lines_30d, active_order_days, avg_daily_orders
    Returns None if SKU not found or DB unavailable.
    """
    if not _CACHE_DB.exists():
        return None
    try:
        with sqlite3.connect(f"file:{_CACHE_DB}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute(
                "SELECT inv_days_30, order_lines_30d, active_order_days, avg_daily_orders "
                "FROM sales WHERE item_number=?", (sku,)
            ).fetchone()
            is_tbd = conn.execute(
                "SELECT 1 FROM tbd_skus WHERE item_number=?", (sku,)
            ).fetchone()
        if row is None:
            return None
        return {
            "inv_days_30":       row[0],
            "order_lines_30d":   row[1],
            "active_order_days": row[2],
            "avg_daily_orders":  row[3],
            "is_tbd":            is_tbd is not None,
        }
    except Exception:
        return None


def _classify_segment(cache: dict) -> str:
    """Apply our segment classification from cached sales data."""
    if cache is None:
        return None
    if cache["is_tbd"]:
        return "TBD"
    inv_days = cache["inv_days_30"]
    # TBD: new product (inv_days_30 < 30 or NaN)
    if inv_days is None or (isinstance(inv_days, float) and (inv_days != inv_days)):  # NaN
        return "TBD"
    try:
        if float(inv_days) < _TBD_DAYS:
            return "TBD"
    except Exception:
        return "TBD"
    ol  = float(cache["order_lines_30d"]  or 0)
    aod = float(cache["active_order_days"] or 0)
    if aod >= _SEG_AOD_TOP and ol >= _SEG_OL_TOP:
        return "TOP"
    if ol < _SEG_OL_TAIL:
        return "TAIL"
    return "MIDDLE"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _strip_i18n(val):
    if val and str(val).startswith("i18n##"):
        parts = str(val).split("##", 2)
        return parts[2] if len(parts) == 3 else val
    return val or ""


_BATCH_SELECT = """
    SELECT b.rec_Id,
           CONCAT('BT', b.rec_Id)                             AS batch_number,
           b.group_batch_id,
           b.inbound_number,
           b.item_number,
           b.quantity,
           b.qc_ExpiredDate                                   AS exp_dtm,
           CASE WHEN b.qc_ExpiredDate > 0
                THEN DATE(FROM_UNIXTIME(b.qc_ExpiredDate))
                ELSE NULL END                                 AS exp_date,
           CONVERT_TZ(FROM_UNIXTIME(b.in_dtm),
                      'UTC','America/New_York')               AS recv_time_edt,
           b.in_user                                         AS receiver,
           COALESCE(NULLIF(g.goods_ename,''), g.goods_name)  AS goods_en_name,
           g.goods_name
    FROM yamibuy_wh.wh_inbound_batch b
    LEFT JOIN yamibuy_im.im_item i   ON i.item_number = b.item_number
    LEFT JOIN yamibuy_master.xysc_goods g ON g.goods_id = i.goods_id
"""

def lookup_by_bt(bt_str: str):
    """BT{rec_Id} → single row."""
    rec_id = int(bt_str[2:])
    sql = text(_BATCH_SELECT + "WHERE b.rec_Id = :rid LIMIT 1")
    with engine.connect() as c:
        df = pd.read_sql(sql, c, params={"rid": rec_id})
    return None if df.empty else df.iloc[0]


def lookup_by_gt(gt_str: str):
    """GT… → single row."""
    sql = text(_BATCH_SELECT + "WHERE b.group_batch_id = :gt LIMIT 1")
    with engine.connect() as c:
        df = pd.read_sql(sql, c, params={"gt": gt_str})
    return None if df.empty else df.iloc[0]


def query_sku_inventory(sku: str, wh: str = "002") -> dict:
    """
    Fetch live inventory + segment for putaway decision.
    - Segment: from sfc_cache.db (our own TOP/MIDDLE/TAIL/TBD classification)
    - FC D2S:  live FC qty / avg_daily_orders from cache
    - SFC qty: live from MySQL WH inventory
    """
    result = {
        "seg": None, "fc_d2s": None, "sfc_qty": 0.0,
        "fc_min_exp_date": None, "need_minimal_exp": 0,
        "avg_daily_orders": 0.0,
    }

    # ── Step 1: segment + avg_daily from SQLite cache ─────────────────────
    cache = _read_sku_from_cache(sku)
    result["seg"] = _classify_segment(cache)
    if cache:
        result["avg_daily_orders"] = float(cache.get("avg_daily_orders") or 0)

    # ── Step 2: live FC qty, SFC qty, FC oldest expiry from MySQL ─────────
    try:
        sql_inv = text("""
            SELECT
              SUM(CASE WHEN sl.zone_id=1 AND sl.location_type IN (3,4)
                       THEN inv.quantity ELSE 0 END)           AS fc_qty,
              SUM(CASE WHEN sl.zone_id=2
                       THEN inv.quantity ELSE 0 END)           AS sfc_qty,
              MIN(CASE WHEN sl.zone_id=1 AND sl.location_type IN (3,4)
                            AND COALESCE(sli.expire_dtm, sl.expire_dtm) > 0
                       THEN COALESCE(sli.expire_dtm, sl.expire_dtm)
                       ELSE NULL END)                          AS fc_min_exp_dtm
            FROM yamibuy_wh.wh_inventory_transaction inv
            JOIN yamibuy_wh.wh_storage_location sl
              ON inv.location_no=sl.location_no
             AND inv.warehouse_number=sl.warehouse_number
            LEFT JOIN yamibuy_wh.wh_storage_location_item sli
              ON sl.location_no=sli.location_no
             AND sl.warehouse_number=sli.warehouse_number
             AND sli.item_number=inv.item_number
            WHERE inv.warehouse_number=:wh
              AND inv.item_number=:sku
              AND inv.quantity>0
        """)
        with engine.connect() as c:
            df_inv = pd.read_sql(sql_inv, c, params={"wh": wh, "sku": sku})
        if not df_inv.empty:
            fc_qty = float(df_inv.iloc[0]["fc_qty"] or 0)
            result["sfc_qty"] = float(df_inv.iloc[0]["sfc_qty"] or 0)
            min_exp = df_inv.iloc[0]["fc_min_exp_dtm"]
            if min_exp and int(min_exp) > 0:
                result["fc_min_exp_date"] = date.fromtimestamp(int(min_exp)).isoformat()
                result["need_minimal_exp"] = 1
        else:
            fc_qty = 0.0
    except Exception:
        fc_qty = 0.0

    # ── Step 3: FC D2S = fc_qty / avg_daily_orders ────────────────────────
    adl = result["avg_daily_orders"]
    if adl and adl > 0 and fc_qty > 0:
        result["fc_d2s"] = round(fc_qty / adl, 1)

    return result


# ---------------------------------------------------------------------------
# Putaway decision engine (mirrors logic/putaway.py decide_putaway)
# ---------------------------------------------------------------------------
# Translated reasons per lang (rule → {lang: reason})
_REASONS = {
    "TBD_KEEP": {
        "en": "New product (<30d in stock) — hold at receiving zone until classified",
        "es": "Producto nuevo (<30d en inventario) — mantener en zona de recepción",
        "zh": "新品（在库不足30天）— 暂留收货区，待分类后再上架",
    },
    "TOP_FC": {
        "en": "TOP SKU — always stock in FC",
        "es": "SKU TOP — siempre almacenar en FC",
        "zh": "TOP 商品 — 始终上架至 FC",
    },
    "TAIL_SFC": {
        "en": "TAIL SKU — always stock in SFC",
        "es": "SKU TAIL — siempre almacenar en SFC",
        "zh": "TAIL 商品 — 始终上架至 SFC",
    },
    "MID_EXP_EARLIER_FC": {
        "en": "New goods expire earlier than FC stock — put in FC first (FEFO)",
        "es": "Nuevos bienes vencen antes que el stock FC — poner en FC primero (FEFO)",
        "zh": "新货到期日早于 FC 现有库存 — 先入 FC（先进先出）",
    },
    "MID_FC_D2S_HIGH": {
        "en": "FC has sufficient stock (D2S>{d}d) — put new goods in SFC",
        "es": "FC tiene stock suficiente (D2S>{d}d) — poner nuevos bienes en SFC",
        "zh": "FC 库存充足（D2S>{d}天）— 新货上架至 SFC",
    },
    "MID_SFC_HAS_STOCK": {
        "en": "FC needs replenishment but SFC has stock ({q} units) — put in SFC, wait for T2",
        "es": "FC necesita reabastecimiento pero SFC tiene stock ({q} uds) — poner en SFC, esperar T2",
        "zh": "FC 需要补货，但 SFC 有库存（{q} 件）— 先入 SFC，等待 T2 补货",
    },
    "MID_SFC_EMPTY": {
        "en": "FC needs replenishment and SFC is empty — put directly in FC",
        "es": "FC necesita reabastecimiento y SFC está vacío — poner directamente en FC",
        "zh": "FC 需要补货且 SFC 无库存 — 直接上架至 FC",
    },
    "NO_SEG": {
        "en": "No segment data — defaulting to SFC",
        "es": "Sin datos de segmento — usando SFC por defecto",
        "zh": "无分类数据 — 默认上架至 SFC",
    },
}
def _sf(v, d=0.0):
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except Exception:
        return d


def decide_putaway(seg, fc_d2s, sfc_qty, need_exp, new_exp_date,
                   fc_min_exp_date, cfg, lang="en") -> dict:
    putaway_d2s = cfg.get("putaway_d2s", 30)
    fc_d2s  = _sf(fc_d2s)
    sfc_qty = _sf(sfc_qty)

    def _r(rule, **kw):
        tmpl = _REASONS.get(rule, {}).get(lang) or _REASONS.get(rule, {}).get("en", rule)
        return tmpl.format(**kw)

    if seg == "TBD":
        return {"dest": "No need to Change Zone", "rule": "TBD_KEEP",
                "reason": _r("TBD_KEEP"), "color": "yellow"}

    if seg == "TOP":
        return {"dest": "FC", "rule": "TOP_FC",
                "reason": _r("TOP_FC"), "color": "red"}

    if seg in ("TAIL", "BOTTOM"):
        return {"dest": "SFC", "rule": "TAIL_SFC",
                "reason": _r("TAIL_SFC"), "color": "green"}

    # MIDDLE
    if (need_exp == 1 and new_exp_date and fc_min_exp_date
            and new_exp_date < fc_min_exp_date):
        return {"dest": "FC", "rule": "MID_EXP_EARLIER_FC",
                "reason": _r("MID_EXP_EARLIER_FC"), "color": "red"}

    if fc_d2s > putaway_d2s:
        return {"dest": "SFC", "rule": "MID_FC_D2S_HIGH",
                "reason": _r("MID_FC_D2S_HIGH", d=f"{fc_d2s:.0f}"), "color": "green"}

    if sfc_qty > 0:
        return {"dest": "SFC", "rule": "MID_SFC_HAS_STOCK",
                "reason": _r("MID_SFC_HAS_STOCK", q=f"{sfc_qty:.0f}"), "color": "green"}

    return {"dest": "FC", "rule": "MID_SFC_EMPTY",
            "reason": _r("MID_SFC_EMPTY"), "color": "red"}


# ---------------------------------------------------------------------------
# API — scan_id: scan BT or GT directly, no UPC needed
# ---------------------------------------------------------------------------
@app.get("/api/scan_id")
async def scan_id(id: str, lang: str = "en"):
    """Scan BT{rec_Id} or GT{…} — resolves SKU automatically."""
    raw = id.strip().upper()
    try:
        if raw.startswith("BT") and raw[2:].isdigit():
            row = lookup_by_bt(raw)
        elif raw.startswith("GT"):
            row = lookup_by_gt(raw)
        else:
            return JSONResponse({"error": "invalid_id", "msg": "Must start with BT or GT"})

        if row is None:
            return JSONResponse({"error": "not_found"})

        sku      = str(row["item_number"])
        exp_date = str(row["exp_date"]) if row.get("exp_date") and str(row.get("exp_date")) not in ("None", "NaT", "") else None
        gt       = str(row.get("group_batch_id") or "")

        product = (_strip_i18n(str(row.get("goods_name") or ""))
                   if lang == "zh"
                   else _strip_i18n(str(row.get("goods_en_name") or "")))
        if not product:
            product = _strip_i18n(str(row.get("goods_name") or ""))

        inv = query_sku_inventory(sku)

        rec = decide_putaway(
            seg=inv["seg"], fc_d2s=inv["fc_d2s"], sfc_qty=inv["sfc_qty"],
            need_exp=inv["need_minimal_exp"], new_exp_date=exp_date,
            fc_min_exp_date=inv["fc_min_exp_date"], cfg=PUTAWAY_CFG, lang=lang,
        ) if inv["seg"] else {
            "dest": "SFC", "rule": "NO_SEG",
            "reason": _REASONS["NO_SEG"].get(lang, _REASONS["NO_SEG"]["en"]),
            "color": "green",
        }

        return JSONResponse({
            "batch_number":   str(row["batch_number"]),
            "group_batch_id": gt,
            "item_number":    sku,
            "quantity":       int(row["quantity"]),
            "product":        product,
            "exp_date":       exp_date,
            "recv_time":      str(row["recv_time_edt"])[:16] if row.get("recv_time_edt") and str(row.get("recv_time_edt")) not in ("None","NaT") else None,
            "receiver":       str(row.get("receiver") or ""),
            "seg":            inv["seg"] or "—",
            "fc_d2s":         inv["fc_d2s"],
            "sfc_qty":        inv["sfc_qty"],
            "rec":            rec,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# API — legacy: BT + UPC → putaway decision
# ---------------------------------------------------------------------------
@app.get("/api/scan")
async def scan(bt: str, upc: str, lang: str = "en"):
    """
    bt  = BT{rec_Id} or GT{…}
    upc = UPC barcode or item_number (SKU)
    Resolves SKU from UPC, matches against the batch row, returns putaway decision.
    """
    raw_bt = bt.strip().upper()
    try:
        # Step 1: resolve UPC → SKU + product name
        sql_upc = text("""
            SELECT u.item_number,
                   g.goods_name,
                   COALESCE(NULLIF(g.goods_ename,''), g.goods_name) AS goods_en_name
            FROM yamibuy_im.im_item_upc u
            JOIN yamibuy_im.im_item i        ON i.item_number = u.item_number
            JOIN yamibuy_master.xysc_goods g ON g.goods_id    = i.goods_id
            WHERE u.upc = :v OR u.item_number = :v
            LIMIT 1
        """)
        with engine.connect() as c:
            df_upc = pd.read_sql(sql_upc, c, params={"v": upc.strip()})
        if df_upc.empty:
            return JSONResponse({"error": "upc_not_found"})
        item_number = str(df_upc.iloc[0]["item_number"])

        # Step 2: look up batch row by BT or GT, verify SKU matches
        if raw_bt.startswith("BT") and raw_bt[2:].isdigit():
            row = lookup_by_bt(raw_bt)
        elif raw_bt.startswith("GT"):
            row = lookup_by_gt(raw_bt)
        else:
            return JSONResponse({"error": "invalid_bt",
                                 "msg": "Batch must start with BT or GT"})

        if row is None:
            return JSONResponse({"error": "not_found"})

        # SKU mismatch — the UPC doesn't belong to this batch
        if str(row["item_number"]) != item_number:
            return JSONResponse({"error": "sku_mismatch",
                                 "msg": f"UPC resolves to SKU {item_number} but batch is SKU {row['item_number']}"})

        exp_date = str(row["exp_date"]) if row.get("exp_date") else None
        gt       = str(row.get("group_batch_id") or "")

        product = (_strip_i18n(str(df_upc.iloc[0]["goods_name"] or "")) if lang == "zh"
                   else _strip_i18n(str(df_upc.iloc[0]["goods_en_name"] or "")))
        if not product:
            product = _strip_i18n(str(df_upc.iloc[0]["goods_name"] or ""))

        # Step 3: get live inventory context
        inv = query_sku_inventory(item_number)

        # Step 4: putaway decision
        rec = decide_putaway(
            seg             = inv["seg"],
            fc_d2s          = inv["fc_d2s"],
            sfc_qty         = inv["sfc_qty"],
            need_exp        = inv["need_minimal_exp"],
            new_exp_date    = exp_date,
            fc_min_exp_date = inv["fc_min_exp_date"],
            cfg             = PUTAWAY_CFG,
        ) if inv["seg"] else {
            "dest": "SFC", "rule": "NO_SEG",
            "reason": "No segment data — defaulting to SFC", "color": "green"
        }

        return JSONResponse({
            "batch_number":  str(row["batch_number"]),
            "group_batch_id": gt,
            "item_number":   item_number,
            "quantity":      int(row["quantity"]),
            "product":       product,
            "exp_date":      exp_date,
            "recv_time":     str(row["recv_time_edt"])[:16] if row.get("recv_time_edt") else None,
            "receiver":      str(row.get("receiver") or ""),
            "seg":           inv["seg"] or "—",
            "fc_d2s":        inv["fc_d2s"],
            "sfc_qty":       inv["sfc_qty"],
            "rec":           rec,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Single-page HTML app
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_PAGE)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Putaway Guide</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #f5f5f5;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Header ── */
#header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  padding: 10px 14px;
  border-bottom: 1px solid #e5e5e5;
  flex-shrink: 0;
}
#header .title { font-size: 1rem; font-weight: 700; }
#lang-sel {
  font-size: 0.78rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  padding: 4px 6px;
  background: #fff;
  cursor: pointer;
}

/* ── Scan area ── */
#scan-area {
  background: #fff;
  padding: 10px 14px 8px;
  border-bottom: 1px solid #e5e5e5;
  flex-shrink: 0;
}
.field-label {
  font-size: 0.7rem;
  color: #888;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
input[type=text] {
  width: 100%;
  font-size: 1rem;
  padding: 10px 12px;
  border: 1.5px solid #ddd;
  border-radius: 8px;
  outline: none;
  background: #fafafa;
  transition: border-color 0.15s;
}
input[type=text]:focus  { border-color: #1a73e8; background: #fff; }
input[type=text].ok     { border-color: #34a853; background: #f6fbf7; }
input[type=text].err    { border-color: #ea4335; background: #fef7f7; }

/* ── Result ── */
#result-area {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
  -webkit-overflow-scrolling: touch;
}

.info-card {
  background: #e8f0fe;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 0.82rem;
  line-height: 1.8;
  margin-bottom: 8px;
}
.info-card b { color: #333; }
.tag {
  display: inline-block;
  font-size: 0.72rem;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 10px;
  margin-left: 4px;
  vertical-align: middle;
}
.tag-top  { background:#fde8e8; color:#c0392b; }
.tag-mid  { background:#fff3cd; color:#856404; }
.tag-tail { background:#e8f0fe; color:#1a5276; }
.tag-tbd  { background:#f3e5f5; color:#6a1b9a; }

.rec-banner {
  border-radius: 10px;
  padding: 16px;
  text-align: center;
  margin-bottom: 8px;
}
.rec-banner.green  { background:#e8f5e9; border:2px solid #34a853; }
.rec-banner.red    { background:#fde8e8; border:2px solid #ea4335; }
.rec-banner.yellow { background:#fff8e1; border:2px solid #f0a500; }
.rec-dest {
  font-size: 1.8rem;
  font-weight: 800;
  letter-spacing: 1px;
}
.rec-banner.green  .rec-dest { color: #34a853; }
.rec-banner.red    .rec-dest { color: #ea4335; }
.rec-banner.yellow .rec-dest { color: #f0a500; }
.rec-rule   { font-size: 0.72rem; font-weight: 700; color: #555; margin-top: 4px; }
.rec-reason { font-size: 0.72rem; color: #666; margin-top: 2px; }

.error-msg { color: #ea4335; font-size: 0.85rem; padding: 8px 0; }
.spinner   { text-align:center; color:#888; padding:20px; font-size:0.9rem; }
</style>
</head>
<body>

<div id="header">
  <span class="title">📦 <span id="app-title">Putaway Guide</span></span>
  <select id="lang-sel" onchange="setLang(this.value)">
    <option value="en">EN</option>
    <option value="es">ES</option>
    <option value="zh">中文</option>
  </select>
</div>

<div id="scan-area">
  <div class="field-label" id="lbl-scan">BT / GT Number</div>
  <input type="text" id="scan-input" placeholder="Scan BT or GT barcode…"
         autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
</div>

<div id="result-area"></div>

<script>
const STRINGS = {
  en: {
    title:     "Putaway Guide",
    scan_lbl:  "BT / GT Number",
    scan_ph:   "Scan BT or GT barcode…",
    invalid:   "⚠ Must start with BT or GT",
    not_found: "❌ Not found",
    loading:   "Looking up…",
    rec:       "PUT AWAY TO",
    lbl_bt: "BT", lbl_gt: "GT", lbl_sku: "SKU", lbl_qty: "Qty",
    lbl_product: "Product", lbl_exp: "Expiry", lbl_recv: "Received by",
    lbl_seg: "Segment", lbl_d2s: "FC D2S", lbl_sfc: "SFC Qty",
    no_data: "—",
  },
  es: {
    title:     "Guía de Almacenamiento",
    scan_lbl:  "Número BT / GT",
    scan_ph:   "Escanear BT o GT…",
    invalid:   "⚠ Debe empezar con BT o GT",
    not_found: "❌ No encontrado",
    loading:   "Buscando…",
    rec:       "ALMACENAR EN",
    lbl_bt: "BT", lbl_gt: "GT", lbl_sku: "SKU", lbl_qty: "Cant",
    lbl_product: "Producto", lbl_exp: "Vence", lbl_recv: "Recibido por",
    lbl_seg: "Segmento", lbl_d2s: "FC D2S", lbl_sfc: "Cant. SFC",
    no_data: "—",
  },
  zh: {
    title:     "上架指引",
    scan_lbl:  "BT / GT 批次号",
    scan_ph:   "扫 BT 或 GT 条码…",
    invalid:   "⚠ 需以 BT 或 GT 开头",
    not_found: "❌ 未找到",
    loading:   "查询中…",
    rec:       "上架至",
    lbl_bt: "批次(BT)", lbl_gt: "上架组(GT)", lbl_sku: "商品编号",
    lbl_qty: "数量",
    lbl_product: "商品名称", lbl_exp: "到期日", lbl_recv: "收货人",
    lbl_seg: "销售分类", lbl_d2s: "FC 库存天", lbl_sfc: "SFC 库存",
    no_data: "—",
  },
};

let lang = localStorage.getItem("loc_lang") || "en";
let S = STRINGS[lang];

function setLang(l) {
  lang = l; S = STRINGS[l];
  localStorage.setItem("loc_lang", l);
  applyStrings();
}
function applyStrings() {
  document.getElementById("app-title").textContent   = S.title;
  document.getElementById("lbl-scan").textContent    = S.scan_lbl;
  document.getElementById("scan-input").placeholder  = S.scan_ph;
  document.getElementById("lang-sel").value          = lang;
}
applyStrings();

const scanInput  = document.getElementById("scan-input");
const resultArea = document.getElementById("result-area");
let scanning = false;

// ── Scanner: hardware Enter OR 350ms idle after ≥4 chars ─────────────────
let _t = null;
scanInput.addEventListener("keydown", function(e) {
  if (e.key === "Enter") {
    clearTimeout(_t);
    e.preventDefault();
    submit(scanInput.value.trim());
    scanInput.value = "";
  }
});
scanInput.addEventListener("input", function() {
  clearTimeout(_t);
  if (scanInput.value.length >= 4) {
    _t = setTimeout(function() {
      const v = scanInput.value.trim();
      if (v.length >= 4) { submit(v); scanInput.value = ""; }
    }, 350);
  }
});

// ── Submit ────────────────────────────────────────────────────────────────
async function submit(raw) {
  const val = raw.toUpperCase();
  const isBT = val.startsWith("BT") && /^\d+$/.test(val.slice(2));
  const isGT = val.startsWith("GT");
  if (!isBT && !isGT) { showError(S.invalid); return; }

  if (scanning) return;
  scanning = true;
  scanInput.classList.remove("ok", "err");
  resultArea.innerHTML = `<div class="spinner">${S.loading}</div>`;
  scanInput.blur();

  try {
    const url = `/api/scan_id?id=${encodeURIComponent(val)}&lang=${lang}`;
    const res = await fetch(url);
    const d   = await res.json();

    if (d.error) {
      scanInput.classList.add("err");
      showError(d.error === "not_found" ? S.not_found : "❌ " + (d.msg || d.error));
    } else {
      scanInput.classList.add("ok");
      showResult(d);
    }
  } catch(e) {
    showError("❌ Network error: " + e.message);
  } finally {
    scanning = false;
    setTimeout(() => { scanInput.focus(); }, 100);
  }
}

// ── Render ────────────────────────────────────────────────────────────────
function segTag(seg) {
  if (!seg || seg === "—") return "";
  const cls = {TOP:"tag-top", MIDDLE:"tag-mid", TAIL:"tag-tail", TBD:"tag-tbd"}[seg] || "";
  return `<span class="tag ${cls}">${seg}</span>`;
}

function showResult(d) {
  const rec    = d.rec || {};
  const color  = rec.color || "green";
  const dest   = rec.dest  || "SFC";
  const rule   = rec.rule  || "";
  const reason = rec.reason|| "";
  const arrow  = color === "green" ? "🟢" : color === "yellow" ? "🟡" : "🔴";
  // For yellow (TBD), show dest directly without "PUT AWAY TO" prefix
  const bannerText = color === "yellow" ? dest : `${S.rec} ${dest}`;
  const d2s    = d.fc_d2s != null ? d.fc_d2s + "d" : "—";
  const sfc    = d.sfc_qty != null ? Math.round(d.sfc_qty) : "—";
  const gt     = d.group_batch_id || "—";

  resultArea.innerHTML = `
    <div class="info-card">
      <b>${S.lbl_bt}:</b> ${d.batch_number}
      ${gt !== "—" ? `&nbsp;<b>${S.lbl_gt}:</b> ${gt}` : ""}
      &nbsp;<b>${S.lbl_sku}:</b> ${d.item_number}<br>
      <b>${S.lbl_product}:</b> ${d.product || "—"}<br>
      <b>${S.lbl_qty}:</b> ${d.quantity}
      &nbsp;<b>${S.lbl_exp}:</b> ${d.exp_date||"—"}
      &nbsp;<b>${S.lbl_recv}:</b> ${d.recv_time||"—"} ${d.receiver||""}<br>
      <b>${S.lbl_seg}:</b> ${segTag(d.seg)}
      &nbsp;<b>${S.lbl_d2s}:</b> ${d2s}
      &nbsp;<b>${S.lbl_sfc}:</b> ${sfc}
    </div>
    <div class="rec-banner ${color}">
      <div class="rec-dest">${arrow} ${bannerText}</div>
      <div class="rec-rule">${rule}</div>
      <div class="rec-reason">${reason}</div>
    </div>`;
}

function showError(msg) {
  resultArea.innerHTML = `<div class="error-msg">${msg}</div>`;
}

// ── Auto-focus on load ────────────────────────────────────────────────────
window.addEventListener("load", () => scanInput.focus());
</script>
</body>
</html>
"""
