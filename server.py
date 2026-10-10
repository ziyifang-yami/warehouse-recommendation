"""
Warehouse Recommendationommendation — FastAPI backend
Serves a single HTML page and two JSON API endpoints.
Run: uvicorn server:app --host 0.0.0.0 --port 8504
"""

import os
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
# DB helpers
# ---------------------------------------------------------------------------
def _strip_i18n(val):
    if val and str(val).startswith("i18n##"):
        parts = str(val).split("##", 2)
        return parts[2] if len(parts) == 3 else val
    return val or ""


def lookup_sku_from_upc(raw: str):
    sql = text("""
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
        df = pd.read_sql(sql, c, params={"v": raw.strip()})
    if df.empty:
        return None
    return {
        "item_number":   str(df.iloc[0]["item_number"]),
        "goods_name":    _strip_i18n(str(df.iloc[0]["goods_name"])),
        "goods_en_name": _strip_i18n(str(df.iloc[0]["goods_en_name"])),
    }


def lookup_batch(bt: str, sku: str):
    sql = text("""
        SELECT rec_Id,
               CONCAT('BT', rec_Id) AS batch_number,
               item_number, quantity,
               qc_ExpiredDate AS exp_dtm,
               CASE WHEN qc_ExpiredDate > 0
                     THEN DATE(FROM_UNIXTIME(qc_ExpiredDate))
                    ELSE NULL END AS exp_date,
               CONVERT_TZ(FROM_UNIXTIME(in_dtm),'UTC','America/New_York') AS recv_time_edt,
               in_user AS receiver
        FROM yamibuy_wh.wh_inbound_batch
        WHERE rec_Id      = CAST(SUBSTRING(:bt, 3) AS UNSIGNED)
          AND item_number = :sku
    """)
    with engine.connect() as c:
        df = pd.read_sql(sql, c, params={"bt": bt.strip(), "sku": sku.strip()})
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "batch_number": str(row["batch_number"]),
        "item_number":  str(row["item_number"]),
        "quantity":     int(row["quantity"]),
        "exp_date":     str(row["exp_date"]) if row["exp_date"] else None,
        "exp_dtm":      int(row["exp_dtm"]) if row["exp_dtm"] else 0,
        "recv_time":    str(row["recv_time_edt"])[:16] if row["recv_time_edt"] else None,
        "receiver":     str(row["receiver"]),
    }


def query_zone1_worst_exp(sku: str, wh: str = "002"):
    sql = text("""
        SELECT MIN(IF(sli.rec_id IS NULL, sl.expire_dtm, sli.expire_dtm)) AS v
        FROM yamibuy_wh.wh_inventory_transaction inv
        JOIN yamibuy_wh.wh_storage_location sl
            ON inv.location_no = sl.location_no
           AND inv.warehouse_number = sl.warehouse_number
        LEFT JOIN yamibuy_wh.wh_storage_location_item sli
            ON sl.location_no = sli.location_no
           AND sl.warehouse_number = sli.warehouse_number
           AND sli.item_number = inv.item_number
        WHERE inv.warehouse_number = :wh
          AND inv.item_number = :sku
          AND inv.quantity > 0
          AND sl.zone_id = 1
          AND sl.location_type IN (3,4)
          AND IF(sli.rec_id IS NULL, sl.expire_dtm, sli.expire_dtm) > 0
    """)
    with engine.connect() as c:
        df = pd.read_sql(sql, c, params={"wh": wh, "sku": sku})
    val = df.iloc[0]["v"] if not df.empty else None
    return date.fromtimestamp(int(val)).isoformat() if val else None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.get("/api/scan")
async def scan(bt: str, upc: str, lang: str = "en"):
    try:
        item = lookup_sku_from_upc(upc)
        if item is None:
            return JSONResponse({"error": "upc_not_found"})

        batch = lookup_batch(bt, item["item_number"])
        if batch is None:
            return JSONResponse({"error": "not_found"})

        # product name by language
        batch["product"] = item["goods_name"] if lang == "zh" else item["goods_en_name"]

        # zone recommendation
        rec = {"dest": "SFC", "reason": ""}
        if not batch["exp_dtm"] or batch["exp_dtm"] <= 0:
            rec = {"dest": "SFC", "reason": "No expiry date"}
        else:
            try:
                z1 = query_zone1_worst_exp(item["item_number"])
                inc = batch["exp_date"]
                if z1 is None:
                    rec = {"dest": "SFC", "reason": "No Zone 1 stock"}
                elif z1 < inc:
                    rec = {"dest": "NJFC", "reason": f"Zone 1 exp {z1} < this batch {inc}"}
                else:
                    rec = {"dest": "SFC",  "reason": f"Zone 1 exp {z1} ≥ this batch {inc}"}
            except Exception as e:
                rec = {"dest": "SFC", "reason": f"Zone query error: {e}"}

        batch["rec"] = rec
        return JSONResponse(batch)

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
<title>Warehouse Recommendation</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #f5f5f5;
  height: 100dvh;          /* dynamic viewport height — shrinks with keyboard */
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
  padding: 10px 14px 6px;
  border-bottom: 1px solid #e5e5e5;
  flex-shrink: 0;
}
.field-row { margin-bottom: 8px; }
.field-label {
  font-size: 0.7rem;
  color: #888;
  margin-bottom: 2px;
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
input[type=text]:focus { border-color: #1a73e8; background: #fff; }
input[type=text].confirmed { border-color: #34a853; background: #f6fbf7; }
input[type=text]:disabled { color: #aaa; background: #f0f0f0; }
.batch-tag {
  font-size: 0.72rem;
  color: #34a853;
  margin-top: 2px;
  height: 14px;
}

/* ── Result area — scrollable, fills remaining height ── */
#result-area {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
  -webkit-overflow-scrolling: touch;
}

/* Info card */
.info-card {
  background: #e8f0fe;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 0.82rem;
  line-height: 1.7;
  margin-bottom: 8px;
}
.info-card b { color: #333; }

/* Recommendation banner */
.rec-banner {
  border-radius: 10px;
  padding: 16px;
  text-align: center;
  margin-bottom: 8px;
}
.rec-banner.sfc   { background: #e8f5e9; border: 2px solid #34a853; }
.rec-banner.njfc  { background: #fde8e8; border: 2px solid #ea4335; }
.rec-dest {
  font-size: 1.8rem;
  font-weight: 800;
  letter-spacing: 1px;
}
.rec-banner.sfc  .rec-dest { color: #34a853; }
.rec-banner.njfc .rec-dest { color: #ea4335; }
.rec-reason { font-size: 0.75rem; color: #666; margin-top: 4px; }

/* Error */
.error-msg { color: #ea4335; font-size: 0.85rem; padding: 8px 0; }

/* Spinner */
.spinner { text-align: center; color: #888; padding: 20px; font-size: 0.9rem; }
</style>
</head>
<body>

<!-- Header -->
<div id="header">
  <span class="title">📦 <span id="app-title">Warehouse Recommendation</span></span>
  <select id="lang-sel" onchange="setLang(this.value)">
    <option value="en">EN</option>
    <option value="es">ES</option>
    <option value="zh">中文</option>
  </select>
</div>

<!-- Scan inputs -->
<div id="scan-area">
  <div class="field-row">
    <div class="field-label" id="lbl-batch">Batch Number</div>
    <input type="text" id="bt-input" placeholder="Scan batch barcode…" autocomplete="off"
           autocorrect="off" autocapitalize="off" spellcheck="false">
    <div class="batch-tag" id="batch-tag"></div>
  </div>
  <div class="field-row">
    <div class="field-label" id="lbl-upc">UPC / SKU</div>
    <input type="text" id="upc-input" placeholder="Scan UPC…" autocomplete="off"
           autocorrect="off" autocapitalize="off" spellcheck="false" disabled>
  </div>
</div>

<!-- Result -->
<div id="result-area"></div>

<script>
// ── i18n ──────────────────────────────────────────────────────────────────
const STRINGS = {
  en: {
    title: "Warehouse Recommendation", batch: "Batch Number", upc: "UPC / SKU",
    bt_ph: "Scan batch barcode…", upc_ph: "Scan UPC or SKU…",
    upc_first: "Set batch first", not_found: "❌ No record for this batch + SKU",
    upc_nf: "❌ UPC not found", loading: "Looking up…",
    rec: "PUT AWAY TO", lbl_batch: "Batch", lbl_sku: "SKU", lbl_qty: "Qty",
    lbl_product: "Product", lbl_exp: "Expiry", lbl_recv: "Recv'd", lbl_by: "By",
    no_exp: "No expiry date", no_z1: "No Zone 1 stock", db_err: "Zone 1 query failed",
  },
  es: {
    title: "Recomendación de Almacén", batch: "Número de Lote", upc: "UPC / SKU",
    bt_ph: "Escanear código BT…", upc_ph: "Escanear UPC o SKU…",
    upc_first: "Escanee lote primero", not_found: "❌ Sin registro para este lote + SKU",
    upc_nf: "❌ UPC no encontrado", loading: "Buscando…",
    rec: "ALMACENAR EN", lbl_batch: "Lote", lbl_sku: "SKU", lbl_qty: "Cant",
    lbl_product: "Producto", lbl_exp: "Vence", lbl_recv: "Recibido", lbl_by: "Por",
    no_exp: "Sin fecha", no_z1: "Sin stock Zona 1", db_err: "Error Zona 1",
  },
  zh: {
    title: "仓库推荐", batch: "批次号", upc: "UPC / SKU",
    bt_ph: "扫 Batch 条码…", upc_ph: "扫 UPC 或 SKU…",
    upc_first: "请先扫批次", not_found: "❌ 未找到该批次 + SKU",
    upc_nf: "❌ UPC 不存在", loading: "查询中…",
    rec: "上架至", lbl_batch: "批次", lbl_sku: "SKU", lbl_qty: "数量",
    lbl_product: "商品", lbl_exp: "到期日", lbl_recv: "收货", lbl_by: "收货人",
    no_exp: "无到期日", no_z1: "Zone 1 无库存", db_err: "Zone 1 查询失败",
  },
};
let lang = localStorage.getItem("loc_lang") || "en";
let S = STRINGS[lang];

function setLang(l) {
  lang = l;
  S = STRINGS[l];
  localStorage.setItem("loc_lang", l);
  applyStrings();
}

function applyStrings() {
  document.getElementById("app-title").textContent = S.title;
  document.getElementById("lbl-batch").textContent = S.batch;
  document.getElementById("lbl-upc").textContent   = S.upc;
  document.getElementById("bt-input").placeholder  = S.bt_ph;
  document.getElementById("upc-input").placeholder = S.upc_ph;
  document.getElementById("lang-sel").value = lang;
}
applyStrings();

// ── State ─────────────────────────────────────────────────────────────────
let btNumber = null;
let scanning = false;

const btInput  = document.getElementById("bt-input");
const upcInput = document.getElementById("upc-input");
const batchTag = document.getElementById("batch-tag");
const resultArea = document.getElementById("result-area");

// ── Scanner debounce ──────────────────────────────────────────────────────
// Scanners type very fast, then send Enter. Debounce handles both:
// - Hardware Enter key → immediate submit
// - Soft keyboard without Enter → 350ms idle triggers submit

function makeScanner(inp, onSubmit) {
  let timer = null;
  const DELAY = 350;

  inp.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      clearTimeout(timer);
      e.preventDefault();
      onSubmit(inp.value.trim());
      inp.value = "";
    }
  });

  inp.addEventListener("input", function() {
    clearTimeout(timer);
    if (inp.value.length >= 4) {
      timer = setTimeout(function() {
        const val = inp.value.trim();
        if (val.length >= 4) {
          onSubmit(val);
          inp.value = "";
        }
      }, DELAY);
    }
  });
}

// ── BT handler ───────────────────────────────────────────────────────────
makeScanner(btInput, function(val) {
  const raw = val.toUpperCase();
  if (!raw.startsWith("BT") || !/^\d+$/.test(raw.slice(2))) {
    showError("⚠ Invalid batch number (must start with BT)");
    return;
  }
  btNumber = raw;
  batchTag.textContent = "✓ " + raw;
  btInput.classList.add("confirmed");
  upcInput.disabled = false;
  upcInput.classList.remove("confirmed");
  resultArea.innerHTML = "";
  // Move cursor to UPC after BT scanned
  upcInput.focus();
});

// ── UPC handler ───────────────────────────────────────────────────────────
makeScanner(upcInput, function(val) {
  if (!btNumber) {
    showError(S.upc_first);
    return;
  }
  if (val.length < 4) return;
  upcInput.classList.remove("confirmed");
  doLookup(btNumber, val);
});

// ── Lookup ────────────────────────────────────────────────────────────────
async function doLookup(bt, upc) {
  if (scanning) return;
  scanning = true;
  resultArea.innerHTML = `<div class="spinner">${S.loading}</div>`;

  // Blur to hide keyboard so result is visible
  upcInput.blur();

  try {
    const url = `/api/scan?bt=${encodeURIComponent(bt)}&upc=${encodeURIComponent(upc)}&lang=${lang}`;
    const res = await fetch(url);
    const data = await res.json();

    if (data.error) {
      const msg = data.error === "not_found" ? S.not_found
                : data.error === "upc_not_found" ? S.upc_nf
                : "❌ " + data.error;
      upcInput.classList.remove("confirmed");
      showError(msg);
    } else {
      upcInput.classList.add("confirmed");
      showResult(data);
    }
  } catch(e) {
    showError("❌ Network error: " + e.message);
  } finally {
    scanning = false;
    upcInput.value = "";
    upcInput.blur();  // dismiss keyboard, no re-focus
  }
}

// ── Render result ─────────────────────────────────────────────────────────
function showResult(d) {
  const rec  = d.rec || {};
  const dest = rec.dest || "SFC";
  const cls  = dest === "NJFC" ? "njfc" : "sfc";
  const arrow = dest === "NJFC" ? "🔴" : "🟢";

  resultArea.innerHTML = `
    <div class="info-card">
      <b>${S.lbl_batch}:</b> ${d.batch_number} &nbsp;
      <b>${S.lbl_sku}:</b> ${d.item_number} &nbsp;
      <b>${S.lbl_qty}:</b> ${d.quantity}<br>
      <b>${S.lbl_product}:</b> ${d.product || "—"}<br>
      <b>${S.lbl_exp}:</b> ${d.exp_date||"—"} &nbsp;
      <b>${S.lbl_recv}:</b> ${d.recv_time||"—"} &nbsp;
      <b>${S.lbl_by}:</b> ${d.receiver||"—"}
    </div>
    <div class="rec-banner ${cls}">
      <div class="rec-dest">${arrow} ${S.rec} ${dest}</div>
      <div class="rec-reason">${rec.reason||""}</div>
    </div>`;
}

function showError(msg) {
  resultArea.innerHTML = `<div class="error-msg">${msg}</div>`;
}

// ── Initial focus ─────────────────────────────────────────────────────────
</script>
</body>
</html>
"""
