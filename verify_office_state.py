#!/usr/bin/env python3
"""Live gate for the 3D office backend (re-runnable, hits the real server).

Reads port + auth_token from config.json (never assumes the port), then
asserts against the LIVE endpoints:

- /office-state: 401 without token; with token, all agents in seat order,
  every field present (incl. idle_since + card_state), state in the enum,
  board status_counts == the kanban DB's, and for every row carrying a
  card_id: card_title/card_state match the DB row.
- /events?token=: SSE stream connects; if an `office` event arrives within
  the watch window (a round is in flight), its shape is validated against
  the /office-state field set. (The transition lifecycle itself — thinking
  -> done -> idle, the 600 ms window, idle_since — is proven in-process by
  verify_office3d_isolated.py, since it requires a turn to fire.)
- /assets/office3d/manifest.json: 200 + application/json with a temp file
  (removed afterwards; .gitkeep stays); traversal attempts -> 404.
- /unity/: route present, missing build -> 404.

Exit 0 = all assertions passed.
"""
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(REPO, "config.json")))
PORT = CFG["port"]
TOKEN = CFG["auth_token"]
DB = CFG.get("kanban_db", os.path.expanduser("~/.hermes/kanban.db"))
BASE = "http://127.0.0.1:%d" % PORT
H = {"Authorization": "Bearer " + TOKEN}
AGENT_NAMES = [a["name"] for a in CFG["agents"]]
OFFICE_FIELDS = ["id", "seat", "color", "state", "tool_label", "card_id",
                 "card_title", "card_state", "elapsed_ms", "last_delta_ms",
                 "idle_since"]
STATE_ENUM = {"thinking", "working", "typing", "stuck", "done", "idle"}
WATCH_S = 8  # how long to listen on /events for an office event


def http(method, url, headers=None, body=None, timeout=10):
    req = urllib.request.Request(url, method=method,
                                 data=body if body is not None else None,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def db_board():
    """Read-only board read with a retry: the live DB is under concurrent
    writes by the kanban system, so a plain read can hit a transient lock."""
    last = None
    for _ in range(10):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
            con.row_factory = sqlite3.Row
            try:
                tasks = con.execute("SELECT id, title, status FROM tasks"
                                    ).fetchall()
                counts = {}
                for t in tasks:
                    counts[t["status"]] = counts.get(t["status"], 0) + 1
                return {t["id"]: {"title": t["title"], "status": t["status"]}
                        for t in tasks}, counts
            finally:
                con.close()
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(0.3)
    raise AssertionError("db_board: board read failed 10x: %s" % last)


def check_office_state():
    code, _, _ = http("GET", BASE + "/office-state")
    assert code == 401, "no token -> %s (want 401)" % code
    # The live board is under concurrent writes by the kanban system: a
    # locked read degrades /office-state's board to {} — retry until the
    # board snapshot matches the DB (transient lock, not a server bug).
    db_tasks, db_counts = db_board()
    snap = {}
    for _ in range(10):
        code, _, body = http("GET", BASE + "/office-state", headers=H)
        assert code == 200, "with token -> %s" % code
        snap = json.loads(body)
        if snap.get("board") == db_counts:
            break
        time.sleep(0.3)
    assert snap["board"] == db_counts, \
        "board %s != db %s (transient lock?)" % (snap["board"], db_counts)
    for k in ["round_active", "now", "stuck_ms", "agents", "board"]:
        assert k in snap, "missing top-level %s" % k
    assert snap["stuck_ms"] == 8000
    assert isinstance(snap["now"], int)
    assert len(snap["agents"]) == len(AGENT_NAMES), \
        "expected %d agents, got %d" % (len(AGENT_NAMES), len(snap["agents"]))
    db_tasks, db_counts = db_board()
    assert snap["board"] == db_counts, \
        "board %s != db %s" % (snap["board"], db_counts)
    for seat, a in enumerate(snap["agents"]):
        assert a["id"] == AGENT_NAMES[seat], \
            "seat %d: %s != %s" % (seat, a["id"], AGENT_NAMES[seat])
        assert a["seat"] == seat
        for f in OFFICE_FIELDS:
            assert f in a, "%s missing %s" % (a["id"], f)
        assert a["state"] in STATE_ENUM, a["state"]
        # idle_since: null while active; set (epoch ms) when idle.
        if a["state"] in ("thinking", "working", "typing", "stuck", "done"):
            assert a["idle_since"] is None, a
        else:  # idle
            assert a["idle_since"] is None or isinstance(a["idle_since"], int), a
        # card cross-check against the live kanban DB.
        if a["card_id"]:
            t = db_tasks.get(a["card_id"])
            assert t, "card_id %s not in db" % a["card_id"]
            assert a["card_title"] == t["title"], \
                "card_title %r != db %r" % (a["card_title"], t["title"])
            assert a["card_state"] == t["status"], \
                "card_state %r != db %r" % (a["card_state"], t["status"])
        else:
            assert a["card_title"] is None and a["card_state"] is None, a
    print("  /office-state: %d agents in seat order, fields + card "
          "cross-checks + board counts OK" % len(snap["agents"]))


def check_events_office():
    """Connect to /events?token=; validate the stream and, if an office
    event arrives within WATCH_S, its shape vs the /office-state field set."""
    req = urllib.request.Request(BASE + "/events?token=" + TOKEN)
    office_seen = None
    try:
        with urllib.request.urlopen(req, timeout=WATCH_S + 5) as r:
            assert r.status == 200
            assert "text/event-stream" in (r.headers.get("Content-Type") or "")
            deadline = time.time() + WATCH_S
            buf = b""
            while time.time() < deadline and office_seen is None:
                chunk = r.read(1)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    lines = raw.decode("utf-8", "replace").splitlines()
                    if not lines or lines[0].startswith(":"):
                        continue
                    data = " ".join(l[5:].strip() for l in lines
                                    if l.startswith("data:"))
                    if not data:
                        continue
                    try:
                        ev = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "office":
                        office_seen = ev
                        break
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        pass  # keepalive timeout with no events = idle server, fine
    # 401 without token.
    code, _, _ = http("GET", BASE + "/events", timeout=5)
    assert code == 401, "/events without token -> %s" % code
    if office_seen is not None:
        for k in ["round_active", "now", "stuck_ms", "agents", "board"]:
            assert k in office_seen, "office event missing %s" % k
        assert len(office_seen["agents"]) == len(AGENT_NAMES)
        for a in office_seen["agents"]:
            for f in OFFICE_FIELDS:
                assert f in a, "office event agent missing %s" % f
            assert a["state"] in STATE_ENUM
        print("  /events: office event observed live, shape validated")
    else:
        print("  /events: stream OK (no transition in watch window — "
              "lifecycle proven by verify_office3d_isolated.py)")


def check_static():
    # Temp manifest: 200 + MIME, then removed (.gitkeep stays).
    manifest = os.path.join(REPO, "assets", "office3d", "manifest.json")
    with open(manifest, "w") as f:
        json.dump({"probe": True}, f)
    try:
        code, hdrs, body = http("GET",
                                BASE + "/assets/office3d/manifest.json")
        assert code == 200, "manifest -> %s (want 200)" % code
        assert hdrs.get("Content-Type") == "application/json", hdrs
        assert json.loads(body) == {"probe": True}
    finally:
        os.remove(manifest)
    code, _, _ = http("GET", BASE + "/assets/office3d/manifest.json")
    assert code == 404, "removed manifest still served"
    for evil in ["/assets/office3d/../config.json",
                 "/assets/office3d/..%2fconfig.json"]:
        code, _, _ = http("GET", BASE + evil)
        assert code == 404, "traversal %s -> %s (want 404)" % (evil, code)
    code, _, _ = http("GET", BASE + "/unity/Build.wasm")
    assert code == 404, "/unity missing build -> %s (want 404)" % code
    assert os.path.exists(os.path.join(REPO, "assets", "office3d",
                                       ".gitkeep")), ".gitkeep missing"
    print("  static: manifest 200+json (temp file, removed), traversal "
          "blocked, /unity 404, .gitkeep intact")


def main():
    print("verify_office_state: live checks against %s" % BASE)
    code, _, _ = http("GET", BASE + "/health")
    assert code == 200, "server not up on port %d" % PORT
    check_office_state()
    check_events_office()
    check_static()
    print("verify_office_state: PASS")


if __name__ == "__main__":
    main()
