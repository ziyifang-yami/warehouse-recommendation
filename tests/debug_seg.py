"""
Debug why segment query returns None on server.
Run: /opt/loc-recommendation/.venv/bin/python /tmp/debug_seg.py
"""
import os, sys, traceback
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('/opt/loc-recommendation/.env')

from sqlalchemy import create_engine, text
import pandas as pd

_user = os.environ.get("MYSQL_USER", "")
_pass = os.environ.get("MYSQL_PASS", "")
_host = os.environ.get("MYSQL_HOST", "rds.g3.yamibuy.net")
print(f"DB: {_user}@{_host}")

engine = create_engine(
    f"mysql+pymysql://{_user}:{_pass}@{_host}/yamibuy_wh?charset=utf8mb4",
    pool_pre_ping=True,
)

# Pick the most recently received SKU to test with
print("\n--- Finding a recent SKU ---")
with engine.connect() as c:
    df = pd.read_sql(text("""
        SELECT item_number, rec_Id, CONCAT('BT', rec_Id) AS bt
        FROM yamibuy_wh.wh_inbound_batch
        ORDER BY in_dtm DESC LIMIT 3
    """), c)
print(df.to_string())

sku = str(df.iloc[0]["item_number"])
print(f"\n--- Testing SKU: {sku} ---")

# Test 1: inventory query
print("\n[1] Inventory query (zone_id, fc_qty, sfc_qty)...")
try:
    sql_inv = text("""
        SELECT
          SUM(CASE WHEN sl.zone_id=1 AND sl.location_type IN (3,4)
                   THEN inv.quantity ELSE 0 END) AS fc_qty,
          SUM(CASE WHEN sl.zone_id=2
                   THEN inv.quantity ELSE 0 END) AS sfc_qty,
          MIN(CASE WHEN sl.zone_id=1 AND sl.location_type IN (3,4)
                        AND COALESCE(sli.expire_dtm, sl.expire_dtm) > 0
                   THEN COALESCE(sli.expire_dtm, sl.expire_dtm)
                   ELSE NULL END) AS fc_min_exp_dtm
        FROM yamibuy_wh.wh_inventory_transaction inv
        JOIN yamibuy_wh.wh_storage_location sl
          ON inv.location_no=sl.location_no
         AND inv.warehouse_number=sl.warehouse_number
        LEFT JOIN yamibuy_wh.wh_storage_location_item sli
          ON sl.location_no=sli.location_no
         AND sl.warehouse_number=sli.warehouse_number
         AND sli.item_number=inv.item_number
        WHERE inv.warehouse_number='002'
          AND inv.item_number=:sku
          AND inv.quantity>0
    """)
    with engine.connect() as c:
        df_inv = pd.read_sql(sql_inv, c, params={"sku": sku})
    print(df_inv.to_string())
except Exception as e:
    print(f"  ERROR: {traceback.format_exc()}")

# Test 2: order/segment query — check DB access
print("\n[2] Order DB access check...")
try:
    with engine.connect() as c:
        df_db = pd.read_sql(text("SHOW DATABASES"), c)
    dbs = df_db.iloc[:,0].tolist()
    has_order = any("order" in d.lower() for d in dbs)
    print(f"  Visible DBs (order-related): {[d for d in dbs if 'order' in d.lower()]}")
    print(f"  yamibuy_order accessible: {has_order}")
except Exception as e:
    print(f"  ERROR: {traceback.format_exc()}")

# Test 3: actual order query
print("\n[3] Order segment query...")
try:
    sql_seg = text("""
        SELECT
          COALESCE(SUM(od.quantity),0)        AS qty_30,
          COUNT(DISTINCT od.order_id)         AS order_lines_30,
          COUNT(DISTINCT CASE WHEN od.created_at >= UNIX_TIMESTAMP(NOW()-INTERVAL 90 DAY)
                              THEN DATE(FROM_UNIXTIME(od.created_at)) END) AS active_days_90
        FROM yamibuy_order.oc_order_detail od
        JOIN yamibuy_order.oc_order o ON o.order_id=od.order_id
        WHERE od.item_number=:sku
          AND od.created_at >= UNIX_TIMESTAMP(NOW()-INTERVAL 30 DAY)
          AND o.order_status NOT IN (7,8)
    """)
    with engine.connect() as c:
        df_seg = pd.read_sql(sql_seg, c, params={"sku": sku})
    print(df_seg.to_string())
    qty_30      = float(df_seg.iloc[0]["qty_30"] or 0)
    order_lines = int(df_seg.iloc[0]["order_lines_30"] or 0)
    active_days = int(df_seg.iloc[0]["active_days_90"] or 0)
    avg_daily   = qty_30 / 30.0
    if active_days >= 21 and order_lines >= 30:
        seg = "TOP"
    elif order_lines < 8:
        seg = "TAIL"
    else:
        seg = "MIDDLE"
    print(f"  → seg={seg}  qty_30={qty_30}  order_lines={order_lines}  active_days={active_days}  avg_daily={avg_daily:.2f}")
except Exception as e:
    print(f"  ERROR: {traceback.format_exc()}")

# Test 4: check if Athena/allocation cache table exists as alternative
print("\n[4] Check allocation cache tables...")
try:
    with engine.connect() as c:
        df_t = pd.read_sql(text("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name LIKE '%alloc%' OR table_name LIKE '%segment%'
               OR table_name LIKE '%sfc%' OR table_name LIKE '%d2s%'
            LIMIT 20
        """), c)
    print(df_t.to_string() if not df_t.empty else "  No allocation tables found in MySQL")
except Exception as e:
    print(f"  ERROR: {e}")
