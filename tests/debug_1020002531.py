import os, sys
os.chdir('/opt/inv-allocation')
sys.path.insert(0, '/opt/inv-allocation')
import pymysql
from dotenv import load_dotenv
from datetime import date
load_dotenv('/opt/inv-allocation/.env')

conn = pymysql.connect(
    host=os.environ['MYSQL_HOST'], user=os.environ['MYSQL_USER'],
    password=os.environ['MYSQL_PASS'], port=int(os.environ.get('MYSQL_PORT','3306')),
    charset='utf8mb4'
)
sku = '1020002531'

with conn.cursor() as cur:
    # 1. need_minimal_exp
    cur.execute("SELECT need_minimal_exp FROM yamibuy_im.im_item_extend WHERE item_number=%s LIMIT 1", (sku,))
    r = cur.fetchone()
    print(f"need_minimal_exp: {r}")

    # 2. SFC inventory with exp
    cur.execute("""
        SELECT inv.location_no, sl.zone_id, sl.location_type,
               COALESCE(sli.expire_dtm, sl.expire_dtm) AS exp_dtm,
               inv.quantity
        FROM yamibuy_wh.wh_inventory_transaction inv
        JOIN yamibuy_wh.wh_storage_location sl
          ON inv.location_no=sl.location_no AND inv.warehouse_number=sl.warehouse_number
        LEFT JOIN yamibuy_wh.wh_storage_location_item sli
          ON sl.location_no=sli.location_no AND sl.warehouse_number=sli.warehouse_number
         AND sli.item_number=inv.item_number
        WHERE inv.warehouse_number='002' AND inv.item_number=%s AND inv.quantity>0
          AND sl.location_type IN (3,4)
        ORDER BY sl.zone_id, exp_dtm
    """, (sku,))
    rows = cur.fetchall()
    print(f"\nInventory rows ({len(rows)}):")
    for r in rows:
        exp = date.fromtimestamp(int(r[3])).isoformat() if r[3] and int(r[3]) > 0 else None
        print(f"  loc={r[0]} zone={r[1]} type={r[2]} exp={exp} qty={r[4]}")

    # 3. SFC min exp from aggregated query
    cur.execute("""
        SELECT
          MIN(CASE WHEN sl.zone_id=2 AND sl.location_type IN (3,4)
                        AND COALESCE(sli.expire_dtm, sl.expire_dtm) > 0
                   THEN COALESCE(sli.expire_dtm, sl.expire_dtm) ELSE NULL END) AS sfc_min_exp_dtm
        FROM yamibuy_wh.wh_inventory_transaction inv
        JOIN yamibuy_wh.wh_storage_location sl
          ON inv.location_no=sl.location_no AND inv.warehouse_number=sl.warehouse_number
        LEFT JOIN yamibuy_wh.wh_storage_location_item sli
          ON sl.location_no=sli.location_no AND sl.warehouse_number=sli.warehouse_number
         AND sli.item_number=inv.item_number
        WHERE inv.warehouse_number='002' AND inv.item_number=%s AND inv.quantity>0
          AND sl.location_type IN (3,4)
    """, (sku,))
    r = cur.fetchone()
    val = r[0] if r else None
    print(f"\nsfc_min_exp_dtm raw: {val}")
    if val and int(val) > 0:
        print(f"sfc_min_exp_date: {date.fromtimestamp(int(val)).isoformat()}")
    else:
        print("sfc_min_exp_date: None")

conn.close()
