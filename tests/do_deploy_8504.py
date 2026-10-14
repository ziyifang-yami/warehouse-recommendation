import subprocess, time

HOST = "root@184.33.17.231"
SSH_OPTS = ["-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5"]

def ssh(cmd, timeout=20):
    r = subprocess.run(["ssh"] + SSH_OPTS + [HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def scp(local, remote, timeout=20):
    r = subprocess.run(["scp"] + SSH_OPTS + [local, f"{HOST}:{remote}"],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stderr.strip()

# 1. Upload
print("Uploading server.py...")
rc, err = scp(r"c:\Project-Z\warehouse-recommendation\server.py",
              "/opt/loc-recommendation/server.py")
print(f"  rc={rc}" + (f" | {err}" if err else ""))
if rc != 0:
    exit(1)

# 2. Restart service
print("Restarting loc-recommendation service...")
out, err, rc = ssh("systemctl restart loc-recommendation")
print(f"  rc={rc}" + (f" | {err}" if err else ""))

# 3. Poll
print("Waiting for service to come up on port 8504...")
for i in range(8):
    time.sleep(3)
    out, _, _ = ssh("ss -tlnp | grep 8504")
    print(f"  [{i+1}] {out[:80]}")
    if "8504" in out:
        print("✅ Service is UP on port 8504")
        break
else:
    print("⚠ Port not detected — checking status:")
    out, err, _ = ssh("systemctl status loc-recommendation --no-pager | tail -15")
    print(out)

# 4. Quick HTTP check
print("HTTP healthcheck...")
out, err, _ = ssh("curl -s -o /dev/null -w '%{http_code}' http://localhost:8504/")
print(f"  HTTP status: {out}")
