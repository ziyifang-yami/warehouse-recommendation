import subprocess, time

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5"]

def ssh(cmd, timeout=25):
    r = subprocess.run(["ssh"] + SSH_OPTS + [HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def scp(local, remote, timeout=15):
    r = subprocess.run(["scp"] + SSH_OPTS + [local, f"{HOST}:{remote}"],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode

rc = scp(r"c:\Project-Z\warehouse-recommendation\debug_seg.py", "/tmp/debug_seg.py")
print(f"Upload: {rc}")

ssh("nohup /opt/loc-recommendation/.venv/bin/python /tmp/debug_seg.py > /tmp/debug_seg_out.txt 2>&1 &")
print("Running... waiting 15s")
time.sleep(15)

out, err, rc = ssh("cat /tmp/debug_seg_out.txt")
print(out)
if err:
    print("STDERR:", err[:300])
