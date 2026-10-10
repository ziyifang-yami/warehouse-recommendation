"""
LOC Recommendation — mobile-optimised
Scan BT → Scan UPC → instant result → auto-focus back to BT → repeat.
"""

import os
from datetime import date
from pathlib import Path
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
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
    DB_AVAILABLE = True
except ImportError:
    _user = os.environ.get("MYSQL_USER", "")
    _pass = os.environ.get("MYSQL_PASS", "")
    _host = os.environ.get("MYSQL_HOST", "rds.g3.yamibuy.net")
    engine = create_engine(
        f"mysql+pymysql://{_user}:{_pass}@{_host}/yamibuy_wh?charset=utf8mb4",
        pool_pre_ping=True,
    ) if _user and _pass else None
    DB_AVAILABLE = bool(engine)

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------
LANGS = {"EN": "en", "ES": "es", "中": "zh"}

T = {
    "title":         {"en": "LOC Rec",        "es": "Rec Ubicación", "zh": "货位推荐"},
    "bt_ph":         {"en": "BT number…",     "es": "Número BT…",    "zh": "BT 批次号…"},
    "upc_ph":        {"en": "UPC / SKU…",     "es": "UPC / SKU…",    "zh": "UPC / SKU…"},
    "bt_invalid":    {"en": "⚠ Invalid BT",   "es": "⚠ BT inválido", "zh": "⚠ BT 格式错误"},
    "bt_first":      {"en": "Scan BT first",  "es": "Escanee BT primero", "zh": "请先扫 BT"},
    "not_found":     {"en": "❌ Not found",    "es": "❌ No encontrado", "zh": "❌ 未找到"},
    "upc_nf":        {"en": "❌ UPC unknown",  "es": "❌ UPC desconocido", "zh": "❌ UPC 不存在"},
    "db_warn":       {"en": "⚠ DB offline",   "es": "⚠ BD sin conexión", "zh": "⚠ 数据库未连接"},
    "lbl_batch":     {"en": "Batch",  "es": "Lote",     "zh": "批次"},
    "lbl_sku":       {"en": "SKU",    "es": "SKU",      "zh": "SKU"},
    "lbl_qty":       {"en": "Qty",    "es": "Cant",     "zh": "数量"},
    "lbl_exp":       {"en": "Expiry", "es": "Vence",    "zh": "到期日"},
    "lbl_recv":      {"en": "Recv'd", "es": "Recibido", "zh": "收货"},
    "lbl_by":        {"en": "By",     "es": "Por",      "zh": "收货人"},
    "lbl_product":   {"en": "Product","es": "Producto", "zh": "商品"},
    "rec_header":    {"en": "PUT AWAY TO",  "es": "ALMACENAR EN", "zh": "上架至"},
    "rec_no_exp":    {"en": "No expiry — defaulting to SFC", "es": "Sin vencimiento — SFC por defecto", "zh": "无到期日，默认 SFC"},
    "rec_no_z1":     {"en": "No Zone 1 stock → SFC", "es": "Sin stock Zona 1 → SFC", "zh": "Zone 1 无库存，走 SFC"},
    "rec_njfc_why":  {"en": "Zone 1 exp {z1} < this batch {inc}", "es": "Zona 1 vence {z1} < este lote {inc}", "zh": "Zone 1 到期 {z1}，早于本批次 {inc}"},
    "rec_sfc_why":   {"en": "Zone 1 exp {z1} ≥ this batch {inc}", "es": "Zona 1 vence {z1} ≥ este lote {inc}", "zh": "Zone 1 到期 {z1}，晚于本批次 {inc}"},
    "rec_err":       {"en": "⚠ Could not query Zone 1", "es": "⚠ Error Zona 1", "zh": "⚠ Zone 1 查询失败"},
}

def t(key):
    return T.get(key, {}).get(st.session_state.get("lang", "en"), key)

# ---------------------------------------------------------------------------
# Page config + CSS
# ---------------------------------------------------------------------------
st.set_page_config(page_title="LOC Rec", page_icon="📦", layout="centered",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
/* No padding, no scroll */
.block-container {
    padding: 0.6rem 0.8rem 0.5rem !important;
    max-width: 480px !important;
}
html, body, [class*="css"] { font-size: 14px !important; }
/* Kill default top margin on first element */
.element-container:first-child { margin-top: 0 !important; }
/* Compact inputs */
.stTextInput > label { display: none !important; }
.stTextInput > div > div > input {
    font-size: 1rem !important;
    padding: 0.4rem 0.6rem !important;
    height: 2.4rem !important;
}
/* Compact buttons */
.stButton > button {
    padding: 0.2rem 0.6rem !important;
    font-size: 0.78rem !important;
    min-height: 1.8rem !important;
}
/* No divider margin */
hr { margin: 0.3rem 0 !important; }
/* Hide streamlit menu & footer */
#MainMenu, footer, header { visibility: hidden !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
DEFAULTS = {
    "lang": "en",
    "bt_number": None,
    "batch_result": None,
    "focus": "bt",        # "bt" | "upc"  — which field should get focus
    "bt_err": False,
    "upc_pending": None,
    "last_error": None,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Header: title + st.selectbox (tiny, right-aligned)
# ---------------------------------------------------------------------------
_title_col, _lang_col = st.columns([8, 1])
with _title_col:
    st.markdown(
        f"<p style='font-size:1rem;font-weight:700;margin:0;padding-top:4px'>📦 {t('title')}</p>",
        unsafe_allow_html=True,
    )
with _lang_col:
    _lang_labels = list(LANGS.keys())
    _lang_codes  = list(LANGS.values())
    _chosen = st.selectbox(
        "🌐",
        _lang_labels,
        index=_lang_codes.index(st.session_state.lang),
        key="lang_sel",
        label_visibility="collapsed",
    )
    if LANGS[_chosen] != st.session_state.lang:
        st.session_state.lang = LANGS[_chosen]
        st.rerun()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _strip_i18n(val):
    if val and str(val).startswith("i18n##"):
        parts = str(val).split("##", 2)
        return parts[2] if len(parts) == 3 else val
    return val or ""

def lookup_sku_from_upc(raw):
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
        "goods_name":    _strip_i18n(df.iloc[0]["goods_name"]),
        "goods_en_name": _strip_i18n(df.iloc[0]["goods_en_name"]),
    }

def lookup_batch(bt, sku):
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
        return pd.read_sql(sql, c, params={"bt": bt.strip(), "sku": sku.strip()})

def query_zone1_worst_exp(sku, wh="002"):
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
    return date.fromtimestamp(int(val)) if val else None

# ---------------------------------------------------------------------------
# on_change handlers
# ---------------------------------------------------------------------------
def _on_bt():
    raw = st.session_state.get("_bt_field", "").strip().upper()
    if not raw:
        return
    if raw.startswith("BT") and raw[2:].isdigit():
        st.session_state.bt_number    = raw
        st.session_state.batch_result = None
        st.session_state.last_error   = None
        st.session_state.focus        = "upc"   # ← after BT, jump to UPC
        st.session_state["_bt_field"] = ""
    else:
        st.session_state.bt_err = True

def _on_upc():
    raw = st.session_state.get("_upc_field", "").strip()
    if not raw or len(raw) < 5:
        return
    st.session_state.upc_pending       = raw
    st.session_state["_upc_field"]     = ""
    st.session_state.focus             = "bt"   # ← after UPC, jump back to BT

# ---------------------------------------------------------------------------
# JS: focus a specific input by placeholder
# ---------------------------------------------------------------------------
# JS: focus + auto-submit on scan (blur-based, works on Android Chrome)
# Barcode scanners type fast then send Enter. On mobile, simulated
# KeyboardEvents don't reliably trigger Streamlit on_change.
# Strategy: debounce the input event, then blur() the field.
# Streamlit fires on_change on blur, so this is the most reliable approach.
# ---------------------------------------------------------------------------
def _focus_js(placeholder):
    safe = placeholder.replace("'", "\\'")
    return f"""<script>
(function(){{
  var _t = null;
  function autoSubmit(inp){{
    // blur triggers Streamlit on_change reliably on mobile
    inp.blur();
    // re-focus after a short delay so next scan lands in the right field
    setTimeout(function(){{ inp.focus(); }}, 50);
  }}
  function attach(inp){{
    if(inp._auto_attached) return;
    inp._auto_attached = true;
    inp.addEventListener('input', function(){{
      clearTimeout(_t);
      if(inp.value.length >= 5){{
        // 280ms: enough time for scanner to finish sending all chars
        _t = setTimeout(function(){{ autoSubmit(inp); }}, 280);
      }}
    }});
    // Also handle physical Enter key (USB/Bluetooth scanners on desktop)
    inp.addEventListener('keydown', function(e){{
      if(e.key === 'Enter'){{
        clearTimeout(_t);
      }}
    }});
  }}
  function go(){{
    const doc = window.parent.document;
    const el = doc.querySelector("input[placeholder='{safe}']");
    if(el){{ el.focus(); el.select(); attach(el); }}
    else {{ setTimeout(go, 120); }}
  }}
  go();
}})();
</script>"""

# ---------------------------------------------------------------------------
# BT row — full width, no badge column
# ---------------------------------------------------------------------------
bt_ph = t("bt_ph")
st.text_input("bt", placeholder=bt_ph, key="_bt_field",
              label_visibility="collapsed", on_change=_on_bt)

if st.session_state.bt_err:
    st.caption(t("bt_invalid"))
    st.session_state.bt_err = False

if st.session_state.bt_number:
    st.caption(f"✓ {st.session_state.bt_number}")

# ---------------------------------------------------------------------------
# UPC row (always shown, greyed-out hint when BT not set)
# ---------------------------------------------------------------------------
upc_ph = t("upc_ph")
if st.session_state.bt_number:
    st.text_input("upc", placeholder=upc_ph, key="_upc_field",
                  label_visibility="collapsed", on_change=_on_upc)
else:
    st.markdown(
        f"<input disabled placeholder='{upc_ph}' "
        f"style='width:100%;padding:0.4rem 0.6rem;font-size:1rem;"
        f"border:1px solid #ddd;border-radius:4px;color:#aaa;background:#f9f9f9'>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Autofocus — fires after every rerun, targets whichever field should be active
# ---------------------------------------------------------------------------
focus_ph = bt_ph if st.session_state.focus == "bt" else upc_ph
components.html(_focus_js(focus_ph), height=0)

# ---------------------------------------------------------------------------
# Process pending UPC lookup
# ---------------------------------------------------------------------------
if st.session_state.upc_pending and DB_AVAILABLE:
    raw = st.session_state.upc_pending
    st.session_state.upc_pending = None
    try:
        item_info = lookup_sku_from_upc(raw)
        if item_info is None:
            st.session_state.last_error   = t("upc_nf")
            st.session_state.batch_result = None
        else:
            df = lookup_batch(st.session_state.bt_number, item_info["item_number"])
            if df.empty:
                st.session_state.last_error   = t("not_found")
                st.session_state.batch_result = None
            else:
                df["goods_name"]    = item_info["goods_name"]
                df["goods_en_name"] = item_info["goods_en_name"]
                st.session_state.batch_result = df
                st.session_state.last_error   = None
    except Exception as exc:
        st.session_state.last_error   = f"DB error: {exc}"
        st.session_state.batch_result = None

if not DB_AVAILABLE:
    st.caption(t("db_warn"))

# ---------------------------------------------------------------------------
# Error banner
# ---------------------------------------------------------------------------
if st.session_state.last_error:
    st.error(st.session_state.last_error)

# ---------------------------------------------------------------------------
# Result card (replaces previous on every new scan)
# ---------------------------------------------------------------------------
if st.session_state.batch_result is not None:
    row  = st.session_state.batch_result.iloc[0]
    lang = st.session_state.lang
    product_name = (row.get("goods_name","—") if lang == "zh"
                    else row.get("goods_en_name", row.get("goods_name","—")))
    exp  = str(row["exp_date"])      if row["exp_date"]      else "—"
    recv = str(row["recv_time_edt"])[:16] if row["recv_time_edt"] else "—"

    st.markdown(f"""
<div style="background:#f0f4ff;border-radius:6px;padding:8px 12px;
            font-size:12.5px;line-height:1.75;margin-top:4px">
  <b>{t('lbl_batch')}:</b> {row['batch_number']} &nbsp;
  <b>{t('lbl_sku')}:</b> {row['item_number']} &nbsp;
  <b>{t('lbl_qty')}:</b> {row['quantity']}<br>
  <b>{t('lbl_product')}:</b> {product_name}<br>
  <b>{t('lbl_exp')}:</b> {exp} &nbsp;
  <b>{t('lbl_recv')}:</b> {recv} &nbsp;
  <b>{t('lbl_by')}:</b> {row['receiver']}
</div>""", unsafe_allow_html=True)

    # ── Putaway recommendation ──────────────────────────────────────────
    incoming_exp = row.get("exp_date")
    incoming_dtm = row.get("exp_dtm")

    def _banner(dest, reason):
        bg, border = ("#fde8e8","#dc3545") if dest == "NJFC" else ("#e8f5e9","#28a745")
        st.markdown(f"""
<div style="background:{bg};border:2px solid {border};border-radius:8px;
            padding:14px;margin:6px 0;text-align:center">
  <div style="font-size:1.6rem;font-weight:800;color:{border};letter-spacing:1px">
    {t('rec_header')} {dest}
  </div>
  <div style="font-size:0.78rem;color:#555;margin-top:4px">{reason}</div>
</div>""", unsafe_allow_html=True)

    if not incoming_dtm or int(incoming_dtm) <= 0:
        _banner("SFC", t("rec_no_exp"))
    elif DB_AVAILABLE:
        try:
            z1 = query_zone1_worst_exp(row["item_number"])
            if z1 is None:
                _banner("SFC", t("rec_no_z1"))
            elif z1 < incoming_exp:
                _banner("NJFC", t("rec_njfc_why").format(z1=z1, inc=incoming_exp))
            else:
                _banner("SFC",  t("rec_sfc_why").format(z1=z1, inc=incoming_exp))
        except Exception as exc:
            st.warning(f"{t('rec_err')}: {exc}")
