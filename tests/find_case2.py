"""
Find SKUs where:
  - MIDDLE segment, FC D2S <= 30, SFC has stock with exp date
  - Most recent inbound: new_exp < sfc_min_exp  (strictly earlier)
  - need_minimal_exp = 1
"""
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
        LIMIT 2000
    """).fetchall()

middle_skus = [r[0] for r in rows]
adl_map = {r[0]: r[1] for r in rows}
print(f"MIDDLE candidates: {len(middle_skus)}")

ph = ','.join(['%s'] * len(middle_skus))

# Step 2: need_minimal_exp=1 SKUs only
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT item_number FROM yamibuy_im.im_item_extend
        WHERE need_minimal_exp=1 AND item_number IN ({ph})
    """, middle_skus)
    exp_skus = set(str(r[0]) for r in cur.fetchall())
print(f"With need_minimal_exp=1: {len(exp_skus)}")
if not exp_skus:
    print("None found"); sys.exit(0)

exp_list = list(exp_skus)
ph2 = ','.join(['%s'] * len(exp_list))

# Step 3: inventory — FC qty, SFC qty, SFC min exp
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT
            inv.item_number,
            SUM(CASE WHEN sl.zone_id!=2 AND sl.location_type IN (3,4) THEN inv.quantity ELSE 0 END) AS fc_qty,
            SUM(CASE WHEN sl.zone_id=2  AND sl.location_type IN (3,4) THEN inv.quantity ELSE 0 END) AS sfc_qty,
            MIN(CASE WHEN sl.zone_id=2  AND sl.location_type IN (3,4)
                          AND COALESCE(sli.expire_dtm, sl.expire_dtm) > 0
                     THEN COALESCE(sli.expire_dtm, sl.expire_dtm) ELSE NULL END) AS sfc_min_exp
        FROM yamibuy_wh.wh_inventory_transaction inv
        JOIN yamibuy_wh.wh_storage_location sl
          ON inv.location_no=sl.location_no AND inv.warehouse_number=sl.warehouse_number
        LEFT JOIN yamibuy_wh.wh_storage_location_item sli
          ON sl.location_no=sli.location_no AND sl.warehouse_number=sli.warehouse_number
         AND sli.item_number=inv.item_number
        WHERE inv.warehouse_number='002' AND inv.item_number IN ({ph2})
          AND inv.quantity>0 AND sl.location_type IN (3,4)
        GROUP BY inv.item_number
        HAVING sfc_qty>0 AND sfc_min_exp IS NOT NULL
    """, exp_list)
    inv_rows = cur.fetchall()

print(f"With SFC stock + exp: {len(inv_rows)}")

# Filter FC D2S <= 30
candidates = []
for r in inv_rows:
    sku, fc_qty, sfc_qty, sfc_min_exp = r
    adl = adl_map.get(sku, 0)
    if adl <= 0: continue
    fc_d2s = float(fc_qty) / float(adl)
    if fc_d2s > 30: continue
    candidates.append((sku, float(fc_qty), float(sfc_qty), fc_d2s, sfc_min_exp))

print(f"FC D2S<=30: {len(candidates)}")
if not candidates: print("None"); sys.exit(0)

# Step 4: most recent inbound with exp
cand_skus = [c[0] for c in candidates]
ph3 = ','.join(['%s'] * len(cand_skus))
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT item_number, CONCAT('BT', rec_Id) AS bt, group_batch_id AS gt,
               qc_ExpiredDate AS exp_dtm
        FROM yamibuy_wh.wh_inbound_batch
        WHERE item_number IN ({ph3}) AND qc_ExpiredDate > 0
        ORDER BY in_dtm DESC
    """, cand_skus)
    bt_rows = cur.fetchall()

bt_map = {}
for r in bt_rows:
    if r[0] not in bt_map:
        bt_map[r[0]] = r

# Find new_exp < sfc_min_exp
print(f"\n{'SKU':<15} {'BT':<12} {'new_exp':<12} {'sfc_min_exp':<14} {'fc_d2s':<8} TRIGGER")
print("-"*75)
found = 0
samples = []
for (sku, fc_qty, sfc_qty, fc_d2s, sfc_min_exp) in candidates:
    if sku not in bt_map: continue
    bt_row = bt_map[sku]
    exp_dtm = bt_row[3]
    if not exp_dtm or int(exp_dtm) <= 0: continue
    new_d = _date.fromtimestamp(int(exp_dtm))
    sfc_d = _date.fromtimestamp(int(sfc_min_exp))
    if new_d < sfc_d:
        print(f"{sku:<15} {bt_row[1]:<12} {str(new_d):<12} {str(sfc_d):<14} {fc_d2s:<8.1f} ✅ FC (FEFO)")
        found += 1
        if found >= 10: break
    else:
        samples.append(f"{sku:<15} {bt_row[1]:<12} {str(new_d):<12} {str(sfc_d):<14} {fc_d2s:<8.1f} → SFC (new>=sfc)")

if found == 0:
    print("No strict new_exp < sfc_min_exp found")
    print("\nNear-misses (new_exp vs sfc_min_exp):")
    for s in samples[:10]: print(" ", s)

conn.close()
