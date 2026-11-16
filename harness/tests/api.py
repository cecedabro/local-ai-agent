"""Tiny client for the selftest: api.py METHOD PATH [JSON] against 127.0.0.1:$APP_PORT, token qtoken."""
import os
import sys
import time
import urllib.request

method, path = sys.argv[1], sys.argv[2]
data = sys.argv[3].encode() if len(sys.argv) > 3 else None
url = f"http://127.0.0.1:{os.environ['APP_PORT']}{path}"
for _ in range(100):
    try:
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": "Bearer qtoken", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(resp.read().decode())
        break
    except urllib.error.HTTPError:
        raise
    except OSError:
        time.sleep(0.1)
else:
    sys.exit(f"{url} not reachable")
