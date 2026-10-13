#!/usr/bin/env python3
"""Safe go-live for the Sprint 4 team-room backend.

The NEW room_server.py is on disk at /Users/grit/.hermes/team-room/ but the
RUNNING process (started before the file was updated) has the OLD code, so
/kanban and /board 404. This script restarts the server ONLY at a round
boundary (round_active=false), so it never kills an in-flight agent turn.

Idempotent + aborts if a round is still active at the deadline. Run it when
the room is idle:

    python3 restart_s4_safely.py

It: (1) waits for round_active=false, (2) restarts the service THROUGH
launchd (kickstart -k if the ai.hermes.teamroom job is loaded, else bootstrap
the plist) so KeepAlive/RunAtLoad supervision is preserved — a nohup/Popen
restart would orphan the service and make launchd crash-loop on the held
port, (3) verifies /kanban + /board + /room are live and auth is ON (health
flag + 401 on a gated route without a token).
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

LIVE = "/Users/grit/.hermes/team-room"
BASE = "http://127.0.0.1:8651"
# MVP-3: reads are auth-gated too — verification requests carry the token.
CONFIG = os.path.join(LIVE, "config.json")
# 30 min: the round boundary can be minutes away (6 agents in a round), and
# the script must outlive the round, not abort mid-wait.
DEADLINE_S = 1800
# Start the new server with the SAME interpreter as the running one
# (homebrew 3.14), not the interpreter running this script.
PY314 = "/opt/homebrew/bin/python3"
# launchd supervision (MVP-1). The room is a supervised LaunchAgent; restarts
# go through launchd so KeepAlive/RunAtLoad stay intact.
LAUNCHD_LABEL = "ai.hermes.teamroom"
PLIST = "/Users/grit/Library/LaunchAgents/ai.hermes.teamroom.plist"


def get(path, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode("utf-8", "replace")


def auth_token():
    try:
        with open(CONFIG) as f:
            return json.load(f).get("auth_token")
    except (OSError, json.JSONDecodeError):
        return None


def round_active():
    try:
        _, b = get("/health")
        return json.loads(b).get("round_active", False)
    except Exception:
        return None  # server down


def find_pid():
    out = subprocess.run(["pgrep", "-f", "room_server.py serve"],
                         capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def main():
    print("Waiting for a round boundary (round_active=false)...")
    t0 = time.time()
    while time.time() - t0 < DEADLINE_S:
        ra = round_active()
        if ra is False:
            print(f"  round idle after {time.time()-t0:.1f}s — proceeding")
            break
        if ra is None:
            print("  server not responding — nothing to restart")
            return 0
        print(f"  round still active ({time.time()-t0:.0f}s) — waiting, will NOT kill mid-round")
        time.sleep(5)
    else:
        print("ABORT: round still active at deadline. Refusing to kill an in-flight turn.")
        return 3

    # Restart THROUGH launchd so supervision (KeepAlive/RunAtLoad) is
    # preserved. A nohup/Popen restart here would orphan the service: launchd's
    # KeepAlive would then crash-loop on the port the orphan holds. kickstart -k
    # is safe because we only reach this point at a round boundary (no in-flight
    # turn). If the job isn't loaded (e.g. bootout'd), bootstrap it.
    uid = os.getuid()
    loaded = subprocess.run(["launchctl", "list"],
                            capture_output=True, text=True).stdout
    log = os.path.join(LIVE, "server.log")
    if LAUNCHD_LABEL in loaded:
        print(f"launchd job loaded; kickstart -k {LAUNCHD_LABEL}")
        r = subprocess.run(["launchctl", "kickstart", "-k",
                            f"gui/{uid}/{LAUNCHD_LABEL}"],
                           capture_output=True, text=True)
        print(f"  kickstart rc={r.returncode} err={r.stderr.strip()!r}")
    else:
        print(f"launchd job NOT loaded; bootstrap {PLIST}")
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", PLIST],
                           capture_output=True, text=True)
        print(f"  bootstrap rc={r.returncode} err={r.stderr.strip()!r}")

    print("Waiting for new server /health...")
    up = False
    for _ in range(40):
        time.sleep(0.5)
        try:
            _, b = get("/health")
            h = json.loads(b)
            if h.get("status") == "ok":
                up = True
                break
        except Exception:
            continue
    if not up:
        print("FAIL: new server did not come up. Check", log)
        return 1
    print(f"  new server pid={find_pid()}")

    # Verify the Sprint 4 routes are now live. /room must serve the COMBINED
    # page (office-pane marker) — the old room.html has no such marker, so a
    # stale serve would fail this check.
    ok = True
    tok = auth_token()
    # (path, marker, token-or-None): /kanban is MVP-3 auth-gated, so its
    # check carries the Bearer token; /board and /room are static shells.
    for path, want, use_tok in ((("/kanban", "total", tok),
                                 ("/board", "kanban", None),
                                 ("/room", "office-pane", None))):
        try:
            s, b = get(path, use_tok)
            good = s == 200 and want in b
            print(f"  {path} -> {s} {'OK' if good else 'BAD'}")
            ok = ok and good
        except Exception as e:
            print(f"  {path} -> ERROR {e}")
            ok = False
    # Verify auth is ON after the restart: /health flag + 401 on a gated
    # route without a token. Without this, GO-LIVE COMPLETE could mean
    # "up but still ungated on the tailnet."
    try:
        s, b = get("/health")
        auth_on = json.loads(b).get("auth_enabled") is True
        print(f"  /health auth_enabled -> {auth_on}")
        ok = ok and auth_on
    except Exception as e:
        print(f"  /health -> ERROR {e}")
        ok = False
    try:
        req = urllib.request.Request(BASE + "/upload", method="POST")
        urllib.request.urlopen(req, timeout=5)
        print("  /upload no-token -> 200 BAD (auth not gating)")
        ok = False
    except urllib.error.HTTPError as e:
        good = e.code == 401
        print(f"  /upload no-token -> {e.code} {'OK' if good else 'BAD'}")
        ok = ok and good
    except Exception as e:
        print(f"  /upload no-token -> ERROR {e}")
        ok = False
    # MVP-3: verify the READ gate too — /messages must 401 without a token
    # and 200 with one (a write-only gate would still leak the transcript).
    try:
        urllib.request.urlopen(BASE + "/messages", timeout=5)
        print("  /messages no-token -> 200 BAD (reads not gated)")
        ok = False
    except urllib.error.HTTPError as e:
        good = e.code == 401
        print(f"  /messages no-token -> {e.code} {'OK' if good else 'BAD'}")
        ok = ok and good
    except Exception as e:
        print(f"  /messages no-token -> ERROR {e}")
        ok = False
    try:
        s, b = get("/messages", tok)
        good = s == 200 and "messages" in b
        print(f"  /messages with-token -> {s} {'OK' if good else 'BAD'}")
        ok = ok and good
    except Exception as e:
        print(f"  /messages with-token -> ERROR {e}")
        ok = False
    print("GO-LIVE COMPLETE — Sprint 4 backend + MVP-3 auth (all reads gated) + S5 (greet mode, kanban SSE) live on 8651." if ok
          else "GO-LIVE INCOMPLETE — check routes above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
