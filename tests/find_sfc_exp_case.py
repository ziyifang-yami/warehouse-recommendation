"""
Find a real example where:
  - SKU is MIDDLE segment
  - FC D2S <= 30
  - SFC has stock with exp date
  - Most recent inbound batch has exp date <= SFC min exp date
  (i.e. new goods expire earlier than SFC stock → should go to FC)
"""
import os, sys, sqlite3, subprocess, time

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5"]

SCRIPT = r"""
import os, sys, sqlite3
os.chdir('/opt/inv-allocation')
sys.path.insert(0, '/opt/inv-allocation')

import pymysql
from dotenv import load_dotenv
load_dotenv('/opt/inv-allocation/.env')

conn = pymysql.connect(
    host=os.environ['MYSQL_HOST'], user=os.environ['MYSQL_USER'],
    password=os.environ['MYSQL_PASS'], port=int(os.environ.get('MYSQL_PORT','3306')),
    charset='utf8mb4'
)

# Step 1: get MIDDLE SKUs with FC D2S <= 30 from cache
db = '/opt/inv-allocation/results/sfc_cache.db'
with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
    rows = c.execute("""
        SELECT s.item_number, s.avg_daily_orders, s.order_lines_30d, s.active_order_days
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

if not middle_skus:
    print("No MIDDLE SKUs found")
    sys.exit(0)

ph = ','.join(['%s'] * len(middle_skus))

# Step 2: get inventory — FC qty, SFC qty, FC min exp, SFC min exp
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT
            inv.item_number,
            SUM(CASE WHEN sl.zone_id != 2 AND sl.location_type IN (3,4) THEN inv.quantity ELSE 0 END) AS fc_qty,
            SUM(CASE WHEN sl.zone_id  = 2 AND sl.location_type IN (3,4) THEN inv.quantity ELSE 0 END) AS sfc_qty,
            MIN(CASE WHEN sl.zone_id != 2 AND sl.location_type IN (3,4)
                          AND COALESCE(sli.expire_dtm, sl.expire_dtm) > 0
                     THEN COALESCE(sli.expire_dtm, sl.expire_dtm) ELSE NULL END) AS fc_min_exp,
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
        HAVING sfc_qty > 0 AND sfc_min_exp IS NOT NULL AND fc_min_exp IS NOT NULL
    """, middle_skus)
    inv_rows = cur.fetchall()

print(f"SKUs with SFC stock + exp data: {len(inv_rows)}")

# Filter: FC D2S <= 30
from datetime import date
candidates = []
for r in inv_rows:
    sku, fc_qty, sfc_qty, fc_min_exp, sfc_min_exp = r
    adl = adl_map.get(sku, 0)
    if adl <= 0:
        continue
    fc_d2s = fc_qty / adl
    if fc_d2s > 30:
        continue
    candidates.append((sku, fc_qty, sfc_qty, fc_d2s, fc_min_exp, sfc_min_exp))

print(f"MIDDLE + FC D2S<=30 + SFC has exp: {len(candidates)}")

# Step 3: find most recent inbound with exp date
if not candidates:
    print("No matching candidates found.")
    sys.exit(0)

cand_skus = [c[0] for c in candidates]
ph2 = ','.join(['%s'] * len(cand_skus))
with conn.cursor() as cur:
    cur.execute(f"""
        SELECT item_number,
               CONCAT('BT', rec_Id) AS bt,
               group_batch_id AS gt,
               qc_ExpiredDate AS exp_dtm,
               DATE(FROM_UNIXTIME(qc_ExpiredDate)) AS exp_date,
               in_dtm
        FROM yamibuy_wh.wh_inbound_batch
        WHERE item_number IN ({ph2})
          AND qc_ExpiredDate > 0
        ORDER BY in_dtm DESC
    """, cand_skus)
    bt_rows = cur.fetchall()

# Map: sku -> most recent BT with exp
bt_map = {{}}
for r in bt_rows:
    sku = r[0]
    if sku not in bt_map:
        bt_map[sku] = r

print(f"\n{'SKU':<15} {'BT':<12} {'GT':<20} {'new_exp':<12} {'fc_d2s':<8} {'sfc_min_exp':<12} {'TRIGGER'}")
print("-"*100)
found = 0
for (sku, fc_qty, sfc_qty, fc_d2s, fc_min_exp, sfc_min_exp) in candidates:
    if sku not in bt_map:
        continue
    bt_row = bt_map[sku]
    new_exp_dtm = bt_row[3]
    new_exp = bt_row[4]
    if not new_exp_dtm or int(new_exp_dtm) <= 0:
        continue
    from datetime import date as _date
    new_exp_d = _date.fromtimestamp(int(new_exp_dtm))
    sfc_min_d = _date.fromtimestamp(int(sfc_min_exp))
    trigger = "✅ FC (FEFO)" if new_exp_d <= sfc_min_d else "→ SFC"
    if new_exp_d <= sfc_min_d:
        print(f"{sku:<15} {bt_row[1]:<12} {str(bt_row[2] or ''):<20} {str(new_exp_d):<12} {fc_d2s:<8.1f} {str(sfc_min_d):<12} {trigger}")
        found += 1
        if found >= 10:
            break

if found == 0:
    print("No example found where new_exp <= sfc_min_exp")
    print("\nSample of candidates (showing new_exp vs sfc_min_exp):")
    count = 0
    for (sku, fc_qty, sfc_qty, fc_d2s, fc_min_exp, sfc_min_exp) in candidates[:20]:
        if sku not in bt_map:
            continue
        bt_row = bt_map[sku]
        new_exp_dtm = bt_row[3]
        if not new_exp_dtm or int(new_exp_dtm) <= 0:
            continue
        new_exp_d = _date.fromtimestamp(int(new_exp_dtm))
        sfc_min_d = _date.fromtimestamp(int(sfc_min_exp))
        print(f"  SKU={sku} BT={bt_row[1]} new_exp={new_exp_d} sfc_min={sfc_min_d} fc_d2s={fc_d2s:.1f}")
        count += 1
        if count >= 10:
            break

conn.close()
"""

# Write and run
with open('/tmp/find_case.py', 'w', encoding='utf-8') as f:
    f.write(SCRIPT)

r1 = subprocess.run(['scp'] + SSH_OPTS + ['/tmp/find_case.py', f'{HOST}:/tmp/find_case.py'],
                    capture_output=True, text=True, timeout=15)
print('Upload:', r1.returncode)

r2 = subprocess.run(['ssh'] + SSH_OPTS + [HOST,
    'nohup /opt/inv-allocation/.venv/bin/python /tmp/find_case.py > /tmp/find_case_out.txt 2>&1 &'],
    capture_output=True, text=True, timeout=15)

print('Waiting 30s...')
time.sleep(30)

r3 = subprocess.run(['ssh'] + SSH_OPTS + [HOST, 'cat /tmp/find_case_out.txt'],
                    capture_output=True, text=True, timeout=20)
print(r3.stdout)
if r3.stderr:
    print('ERR:', r3.stderr[:300])
