#!/usr/bin/env python3
"""Migrate the team-room backend from 8650 to 8651 at a round boundary.

Prep (done separately, non-disruptive): config.json port=8651, room_server.py
'/' route now serves the new UI (webui/index.html).

This script waits for round_active=false on the OLD 8650 backend, then:
  1. point webui/index.html API base 8650 -> 8651
  2. stop the 8651 static file server (frees port 8651)
  3. SIGTERM the 8650 backend
  4. start the backend (reads config -> binds 8651)
  5. re-point tailscale serve 8650 -> 8651
  6. verify 8651 serves the new UI + API, 8650 is down
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

BASE_DIR = os.path.expanduser("~/.hermes/team-room")
WEBUI = os.path.join(BASE_DIR, "webui", "index.html")
OLD = "http://127.0.0.1:8650"
NEW = "http://127.0.0.1:8651"
DEADLINE_S = 1200


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def old_health():
    try:
        with urllib.request.urlopen(OLD + "/health", timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def round_active():
    h = old_health()
    return h.get("round_active") if h else None


def pgrep(pattern):
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def main():
    log("Waiting for a round boundary (round_active=false) on 8650...")
    t0 = time.time()
    while time.time() - t0 < DEADLINE_S:
        ra = round_active()
        if ra is False:
            log(f"round idle after {time.time()-t0:.0f}s — proceeding")
            break
        if ra is None:
            log("8650 not responding — assuming safe to proceed")
            break
        log(f"round active ({time.time()-t0:.0f}s) — waiting, will NOT kill mid-round")
        time.sleep(5)
    else:
        log("ABORT: round still active at deadline. Not migrating.")
        return 3

    # 1. webui API base -> 8651
    with open(WEBUI) as f:
        html = f.read()
    if 'const API = "http://127.0.0.1:8650"' in html:
        with open(WEBUI, "w") as f:
            f.write(html.replace('const API = "http://127.0.0.1:8650"',
                                 'const API = "http://127.0.0.1:8651"'))
        log("webui API base -> 8651")
    else:
        log("webui API base already != 8650 (skipping edit)")

    # 2. stop the 8651 static file server
    sp = pgrep("http.server 8651")
    if sp:
        log(f"stopping 8651 static server pid={sp}")
        os.kill(sp, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            if pgrep("http.server 8651") is None:
                break
    else:
        log("no 8651 static server found (already stopped)")

    # 3. SIGTERM the 8650 backend
    bp = pgrep("room_server.py serve")
    if bp:
        log(f"SIGTERM 8650 backend pid={bp}")
        os.kill(bp, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.5)
            if pgrep("room_server.py serve") is None:
                break
        else:
            log("old backend did not exit; SIGKILL")
            try:
                os.kill(bp, signal.SIGKILL)
            except ProcessLookupError:
                pass
    else:
        log("no 8650 backend found (already stopped)")

    # 4. start the backend (binds 8651 per config.json)
    server_log = os.path.join(BASE_DIR, "server.log")
    with open(server_log, "ab") as lf:
        subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "room_server.py"), "serve"],
                         cwd=BASE_DIR, stdout=lf, stderr=lf, start_new_session=True)
    log("waiting for 8651 /health...")
    up = False
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(NEW + "/health", timeout=5) as r:
                if json.loads(r.read().decode()).get("status") == "ok":
                    up = True
                    break
        except Exception:
            continue
    if not up:
        log("FAIL: 8651 did not come up. Check " + server_log)
        return 1
    log("8651 backend UP")

    # 5. re-point tailscale serve 8650 -> 8651
    try:
        subprocess.run(["tailscale", "serve", "--https=443", "off"],
                       capture_output=True, timeout=30)
        subprocess.run(["tailscale", "serve", "--bg", "8651"],
                       capture_output=True, timeout=30)
        st = subprocess.run(["tailscale", "serve", "status"],
                            capture_output=True, text=True).stdout.strip()
        log("tailscale re-pointed:\n" + st)
    except Exception as e:
        log(f"tailscale re-point FAILED: {e}")

    # 6. verify
    log("=== VERIFY ===")
    try:
        with urllib.request.urlopen(NEW + "/", timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
        log(f"8651/ serves new UI: mention-pop={'mention-pop' in body} "
            f"attach-row={'attach-row' in body}")
    except Exception as e:
        log(f"8651/ ERROR {e}")
    try:
        with urllib.request.urlopen(OLD + "/health", timeout=3) as r:
            log("8650 STILL UP (unexpected)")
    except Exception:
        log("8650 DOWN (expected)")
    log("MIGRATION COMPLETE — team-room live on 8651.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
