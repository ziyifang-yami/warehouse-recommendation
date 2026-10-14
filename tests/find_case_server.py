import os, sys, sqlite3
os.chdir('/opt/inv-allocation')
sys.path.insert(0, '/opt/inv-allocation')

import pymysql
from dotenv import load_dotenv
from datetime import date as _date
load_dotenv('/opt/inv-allocation/.env')

conn = pymysql.connect(
    host=os.environ['MYSQL_HOST'], user=os.environ['MYSQL_USER'],
    password=os.environ['MYSQL_PASS'], port=int(os.environ.get('MYSQL_PORT','3306')),
    charset='utf8mb4'
)

# Step 1: MIDDLE SKUs from cache
db = '/opt/inv-allocation/results/sfc_cache.db'
with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
    rows = c.execute("""
        SELECT s.item_number, s.avg_daily_orders
        FROM sales s
        WHERE s.avg_daily_orders > 0
          AND s.order_lines_30d >= 8
          AND NOT (s.active_order_days >= 21 AND s.order_lines_30d >= 30)
          AND s.item_number NOT IN (SELECT item_number FROM tbd_skus)
        LIMIT 500
    """).fetchall()

middle_skus = [r[0] for r in rows]
adl_map = {r[0]: r[1] for r in rows}
print(f"MIDDLE candidates: {len(middle_skus)}")

ph = ','.join(['%s'] * len(middle_skus))

# Step 2: inventory — FC qty, SFC qty, SFC min exp
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT
            inv.item_number,
            SUM(CASE WHEN sl.zone_id != 2 AND sl.location_type IN (3,4) THEN inv.quantity ELSE 0 END) AS fc_qty,
            SUM(CASE WHEN sl.zone_id  = 2 AND sl.location_type IN (3,4) THEN inv.quantity ELSE 0 END) AS sfc_qty,
            MIN(CASE WHEN sl.zone_id  = 2 AND sl.location_type IN (3,4)
                          AND COALESCE(sli.expire_dtm, sl.expire_dtm) > 0
                     THEN COALESCE(sli.expire_dtm, sl.expire_dtm) ELSE NULL END) AS sfc_min_exp
        FROM yamibuy_wh.wh_inventory_transaction inv
        JOIN yamibuy_wh.wh_storage_location sl
          ON inv.location_no=sl.location_no AND inv.warehouse_number=sl.warehouse_number
        LEFT JOIN yamibuy_wh.wh_storage_location_item sli
          ON sl.location_no=sli.location_no AND sl.warehouse_number=sli.warehouse_number
         AND sli.item_number=inv.item_number
        WHERE inv.warehouse_number='002'
          AND inv.item_number IN ({ph})
          AND inv.quantity > 0
          AND sl.location_type IN (3,4)
        GROUP BY inv.item_number
        HAVING sfc_qty > 0 AND sfc_min_exp IS NOT NULL
    """, middle_skus)
    inv_rows = cur.fetchall()

print(f"SKUs with SFC stock + exp: {len(inv_rows)}")

# Filter FC D2S <= 30
candidates = []
for r in inv_rows:
    sku, fc_qty, sfc_qty, sfc_min_exp = r
    adl = adl_map.get(sku, 0)
    if adl <= 0:
        continue
    fc_d2s = float(fc_qty) / float(adl)
    if fc_d2s > 30:
        continue
    candidates.append((sku, fc_qty, sfc_qty, fc_d2s, sfc_min_exp))

print(f"MIDDLE + FC D2S<=30 + SFC has exp: {len(candidates)}")

if not candidates:
    print("No matching candidates found.")
    sys.exit(0)

# Step 3: most recent inbound with exp
cand_skus = [c[0] for c in candidates]
ph2 = ','.join(['%s'] * len(cand_skus))
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT item_number, CONCAT('BT', rec_Id) AS bt, group_batch_id AS gt,
               qc_ExpiredDate AS exp_dtm, DATE(FROM_UNIXTIME(qc_ExpiredDate)) AS exp_date
        FROM yamibuy_wh.wh_inbound_batch
        WHERE item_number IN ({ph2}) AND qc_ExpiredDate > 0
        ORDER BY in_dtm DESC
    """, cand_skus)
    bt_rows = cur.fetchall()

bt_map = {}
for r in bt_rows:
    if r[0] not in bt_map:
        bt_map[r[0]] = r

# Show results
print(f"\n{'SKU':<15} {'BT':<12} {'new_exp':<12} {'sfc_min_exp':<14} {'fc_d2s':<8} {'TRIGGER'}")
print("-"*80)
found = 0
samples = []
for (sku, fc_qty, sfc_qty, fc_d2s, sfc_min_exp) in candidates:
    if sku not in bt_map:
        continue
    bt_row = bt_map[sku]
    exp_dtm = bt_row[3]
    if not exp_dtm or int(exp_dtm) <= 0:
        continue
    new_d = _date.fromtimestamp(int(exp_dtm))
    sfc_d = _date.fromtimestamp(int(sfc_min_exp))
    trigger = "✅ FC (FEFO)" if new_d <= sfc_d else "→ SFC"
    row = f"{sku:<15} {bt_row[1]:<12} {str(new_d):<12} {str(sfc_d):<14} {fc_d2s:<8.1f} {trigger}"
    if new_d <= sfc_d:
        print(row)
        found += 1
        if found >= 10:
            break
    else:
        samples.append(row)

if found == 0:
    print("No example found where new_exp <= sfc_min_exp")
    print("\nSample of near-misses (new_exp vs sfc_min_exp):")
    for s in samples[:10]:
        print(" ", s)

conn.close()
