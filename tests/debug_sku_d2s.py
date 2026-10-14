import subprocess, time

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5"]

script = """
import sqlite3, os
os.chdir('/opt/inv-allocation')

db = '/opt/inv-allocation/results/sfc_cache.db'
sku = '1017029631'

with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
    # Check sales table
    row = conn.execute(
        'SELECT item_number, inv_days_30, order_lines_30d, active_order_days, avg_daily_orders '
        'FROM sales WHERE item_number=?', (sku,)
    ).fetchone()
    print('sales row:', row)

    # Check tbd_skus
    tbd = conn.execute('SELECT 1 FROM tbd_skus WHERE item_number=?', (sku,)).fetchone()
    print('in tbd_skus:', tbd is not None)

    # Check fc2sfc_triggers for fc_qty
    trig = conn.execute(
        'SELECT trigger_type, fc_qty, sfc_qty, fc_d2s, avg_daily_orders, seg '
        'FROM fc2sfc_triggers WHERE item_number=?', (sku,)
    ).fetchall()
    print('fc2sfc_triggers:', trig)
"""

# Write script to temp file
with open('/tmp/dbg_d2s.py', 'w') as f:
    f.write(script)

r1 = subprocess.run(['scp'] + SSH_OPTS + ['/tmp/dbg_d2s.py', f'{HOST}:/tmp/dbg_d2s.py'],
                    capture_output=True, text=True, timeout=15)
print('Upload:', r1.returncode)

r2 = subprocess.run(['ssh'] + SSH_OPTS + [HOST, 'nohup /opt/inv-allocation/.venv/bin/python /tmp/dbg_d2s.py > /tmp/dbg_d2s_out.txt 2>&1 &'],
                    capture_output=True, text=True, timeout=15)
time.sleep(8)

r3 = subprocess.run(['ssh'] + SSH_OPTS + [HOST, 'cat /tmp/dbg_d2s_out.txt'],
                    capture_output=True, text=True, timeout=15)
print(r3.stdout)
if r3.stderr:
    print('ERR:', r3.stderr[:200])
