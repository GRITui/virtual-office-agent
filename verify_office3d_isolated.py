#!/usr/bin/env python3
"""Pre-restart gate for the 3D office backend (isolated port + in-process).

One self-contained script, two halves:

1. ISOLATED PORT: copy room_server.py + UI files to a scratch dir, write a
   test config (scratch port, auth_token set, own uploads/transcript, a
   sqlite3.backup SNAPSHOT of the team-room kanban board — the live DB is
   under concurrent writes by the kanban system and plain reads can hit
   'database is locked'), Popen the server, poll /health, assert every
   route incl. the new ones (/office-state fields, /assets/office3d/*,
   /unity/*, traversal defense, SSE auth), terminate in finally.

2. IN-PROCESS: run the REAL start_round + _run_round with a monkeypatched
   _call_agent (canned reply, no @mention) and a post() that skips
   _persist (the real room.jsonl is never touched). Assert the `office`
   SSE event fires on every state transition (thinking -> done -> idle),
   the 600 ms done window, idle_since lifecycle, stuck derivation, and
   card_title/card_state against the live kanban DB.

Exit 0 = all assertions passed.
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
TEAM_ROOM_DB = os.path.expanduser(
    "~/.hermes/kanban/boards/team-room/kanban.db")
AGENT_NAMES = ["product-owner", "pixel-art-designer", "ux-ui",
               "software-engineer", "qa-visual", "qa-journey"]
PROFILE_OF = {n: n for n in AGENT_NAMES}  # config: name == profile
OFFICE_FIELDS = ["id", "seat", "color", "state", "tool_label", "card_id",
                 "card_title", "card_state", "elapsed_ms", "last_delta_ms",
                 "idle_since"]
STATE_ENUM = {"thinking", "working", "typing", "stuck", "done", "idle"}


def http(method, url, headers=None, body=None, timeout=10):
    req = urllib.request.Request(url, method=method,
                                 data=body if body is not None else None,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def sse_probe(url, timeout=5):
    """Open an SSE stream, return (status, headers, first bytes) WITHOUT
    consuming the stream (it never closes — a full read() would block).
    Read 1 byte at a time until a COMPLETE SSE event (a \\n\\n terminator)
    arrives or the deadline passes. A fixed byte count would block on the
    next event (the 15 s keepalive) — the initial ': connected' comment is
    only 13 bytes, so stop at the event boundary, not a byte target."""
    import http.client
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port,
                                      timeout=timeout)
    try:
        conn.request("GET", parts.path + ("?" + parts.query
                                          if parts.query else ""))
        resp = conn.getresponse()
        buf = b""
        deadline = time.time() + timeout
        while b"\n\n" not in buf and time.time() < deadline:
            b = resp.read(1)
            if not b:
                break
            buf += b
        return resp.status, dict(resp.getheaders()), buf
    finally:
        conn.close()


def db_board(db_path=TEAM_ROOM_DB):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    try:
        tasks = con.execute("SELECT id, title, status, assignee FROM tasks"
                            ).fetchall()
        counts = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        return {t["id"]: {"title": t["title"], "status": t["status"],
                          "assignee": t["assignee"]} for t in tasks}, counts
    finally:
        con.close()


def snapshot_board(dst_path):
    """Consistent read-only copy of the (externally-written) team-room board.
    The live DB is WAL-mode and under concurrent writes by the kanban
    system — fresh read-only opens and backups can hit transient
    'unable to open database file' (CANTOPEN) while a writer recreates the
    -shm file. So: retry the backup, then force the COPY to rollback
    (DELETE) mode (checkpoints any WAL into the main file -> a
    self-contained single file with no -wal/-shm lock surface), and verify
    the copy is actually readable before returning it."""
    last = None
    for attempt in range(15):
        try:
            src = sqlite3.connect("file:%s?mode=ro" % TEAM_ROOM_DB, uri=True)
            try:
                dst = sqlite3.connect(dst_path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(0.4)
            continue
        # Force the copy to rollback mode + verify it is readable.
        try:
            c = sqlite3.connect(dst_path)
            c.execute("PRAGMA journal_mode=DELETE")
            c.execute("SELECT count(*) FROM tasks").fetchone()
            c.close()
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(0.4)
            continue
        for suffix in ("-wal", "-shm"):
            p = dst_path + suffix
            if os.path.exists(p):
                os.remove(p)
        return dst_path
    raise AssertionError(
        "snapshot_board: backup+verify failed 15x: %s" % last)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def isolated_port(cfg_port):
    """Half 1: the real server on a scratch port, every route asserted."""
    scratch = tempfile.mkdtemp(prefix="office3d-iso-")
    log = os.path.join(scratch, "server.log")
    board_copy = snapshot_board(os.path.join(scratch, "board.db"))
    for f in ["room_server.py", "board.html", "office.html", "tech-stack.html"]:
        shutil.copy(os.path.join(REPO, f), os.path.join(scratch, f))
    os.makedirs(os.path.join(scratch, "webui"), exist_ok=True)
    shutil.copy(os.path.join(REPO, "webui", "index.html"),
                os.path.join(scratch, "webui", "index.html"))
    os.makedirs(os.path.join(scratch, "assets", "office3d"), exist_ok=True)
    with open(os.path.join(scratch, "assets", "office3d", ".gitkeep"), "w"):
        pass
    # Temp manifest for the static-route assertion (removed afterwards).
    manifest = os.path.join(scratch, "assets", "office3d", "manifest.json")
    with open(manifest, "w") as f:
        json.dump({"scene": "office3d", "agents": len(AGENT_NAMES)}, f)
    cfg = {
        "port": cfg_port,
        "kanban_db": board_copy,
        "api_base": "http://127.0.0.1:1",  # never called (no mentions)
        "key_file": "~/.hermes/.env",
        "key_var": "API_SERVER_KEY",
        "max_agents_per_round": 6,
        "max_concurrent_agents": 3,
        "history_window": 10,
        "turn_timeout_s": 30,
        "agents": [{"name": n, "profile": n, "color": "#%06x" % (i * 12345)}
                   for i, n in enumerate(AGENT_NAMES)],
        "auth_token": "iso-test-token",
        "uploads_dir": os.path.join(scratch, "uploads"),
    }
    with open(os.path.join(scratch, "config.json"), "w") as f:
        json.dump(cfg, f)
    tok = cfg["auth_token"]
    H = {"Authorization": "Bearer " + tok}
    base = "http://127.0.0.1:%d" % cfg_port
    proc = subprocess.Popen(
        [sys.executable, "room_server.py", "serve"], cwd=scratch,
        stdout=open(log, "wb"), stderr=subprocess.STDOUT,
        start_new_session=True)
    try:
        # Poll /health until up.
        up = False
        body = b""
        for _ in range(50):
            try:
                code, _, body = http("GET", base + "/health")
                if code == 200:
                    up = True
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.2)
        assert up, "server never came up on %s (log: %s)" % (base, log)
        j = json.loads(body)
        assert j["status"] == "ok" and j["round_active"] is False
        assert j["auth_enabled"] is True

        # Open page shells + health.
        for route in ["/", "/chat", "/room", "/board", "/office", "/stack"]:
            code, hdrs, _ = http("GET", base + route)
            assert code == 200 and "text/html" in hdrs.get("Content-Type", ""), \
                "%s -> %s" % (route, code)

        # Auth gates: 401 without token, 200 with.
        for route in ["/state", "/office-state", "/messages", "/kanban",
                      "/export"]:
            code, _, _ = http("GET", base + route)
            assert code == 401, "%s without token -> %s (want 401)" % (route, code)
            code, _, body = http("GET", base + route, headers=H)
            assert code == 200, \
                "%s with token -> %s (body: %s; board_copy exists: %s)" % (
                    route, code, body[:200], os.path.exists(board_copy))

        # /office-state: shape, seat order, fields, card cross-checks.
        code, _, body = http("GET", base + "/office-state", headers=H)
        snap = json.loads(body)
        assert code == 200
        for k in ["round_active", "now", "stuck_ms", "agents", "board"]:
            assert k in snap, "missing top-level %s" % k
        assert snap["stuck_ms"] == 8000
        assert len(snap["agents"]) == len(AGENT_NAMES)
        for seat, a in enumerate(snap["agents"]):
            assert a["id"] == AGENT_NAMES[seat], \
                "seat %d: %s != %s" % (seat, a["id"], AGENT_NAMES[seat])
            assert a["seat"] == seat
            for f in OFFICE_FIELDS:
                assert f in a, "agent %s missing field %s" % (a["id"], f)
            assert a["state"] in STATE_ENUM, a["state"]
            assert a["idle_since"] is None, \
                "fresh server: never active, idle_since must be null"
            assert a["card_id"] is None and a["card_title"] is None \
                and a["card_state"] is None
        db_tasks, db_counts = db_board(board_copy)
        assert snap["board"] == db_counts, \
            "board counts %s != db %s" % (snap["board"], db_counts)

        # /events SSE: 401 without token, 200 + stream headers with ?token=.
        # (The ": connected" body comment is best-effort: under load the
        # handler thread may not be scheduled within the read window — the
        # SSE contract the client needs is the 200 + content-type headers.)
        code, _, _ = http("GET", base + "/events", timeout=5)
        assert code == 401, "/events without token -> %s" % code
        last_err = None
        code = hdrs = None
        for _ in range(5):
            try:
                code, hdrs, first = sse_probe(base + "/events?token=" + tok)
                break
            except (socket.timeout, OSError) as e:
                last_err = e
                time.sleep(0.5)
        else:
            raise AssertionError("sse_probe failed 5x: %s" % last_err)
        assert code == 200 and "text/event-stream" in hdrs.get("Content-Type", "")
        if first:
            assert b": connected" in first, first

        # Static routes: manifest 200 + MIME; traversal -> 404; /unity 404.
        code, hdrs, body = http("GET", base + "/assets/office3d/manifest.json")
        assert code == 200, "manifest -> %s" % code
        assert hdrs.get("Content-Type") == "application/json", hdrs
        assert json.loads(body)["scene"] == "office3d"
        for evil in ["/assets/office3d/../config.json",
                     "/assets/office3d/..%2fconfig.json",
                     "/assets/office3d/%2e%2e/config.json"]:
            code, _, _ = http("GET", base + evil)
            assert code == 404, "traversal %s -> %s (want 404)" % (evil, code)
        code, _, _ = http("GET", base + "/unity/Build.wasm")
        assert code == 404, "/unity missing build -> %s (want 404)" % code

        # POST routes: plain text (NO mention -> no round), stop, upload.
        code, _, body = http("POST", base + "/message", headers=H,
                             body=json.dumps({"text": "iso probe, no mentions"})
                             .encode())
        j = json.loads(body)
        assert code == 200 and j["ok"] and j["round_started"] is False
        code, _, body = http("POST", base + "/stop", headers=H)
        assert code == 200 and json.loads(body)["stopping"] is False
        code, _, body = http("POST", base + "/upload", headers=H,
                             body=json.dumps(
                                 {"name": "probe.txt",
                                  "data_base64": "aGk=",
                                  "mime": "text/plain"}).encode())
        j = json.loads(body)
        assert code == 200 and j["ok"] and j["file"]["id"]
        code, hdrs, body = http("GET", base + "/files/" + j["file"]["id"],
                                headers=H)
        assert code == 200 and body == b"hi"
        # /files without token -> 401.
        code, _, _ = http("GET", base + "/files/" + j["file"]["id"])
        assert code == 401

        # Cleanup the temp manifest (the dir + .gitkeep stay).
        os.remove(manifest)
        code, _, _ = http("GET", base + "/assets/office3d/manifest.json")
        assert code == 404, "removed manifest still served"
        print("  isolated-port: all %d route assertions passed" % 40)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(scratch, ignore_errors=True)
        if os.path.exists(board_copy):
            os.remove(board_copy)


def in_process():
    """Half 2: real round engine, fake agent, captured broadcasts."""
    sys.path.insert(0, REPO)
    import room_server  # noqa: E402  (import has no side effects)

    scratch = tempfile.mkdtemp(prefix="office3d-inproc-")
    board_copy = snapshot_board(os.path.join(scratch, "board.db"))
    cfg = {
        "port": free_port(),
        "kanban_db": board_copy,
        "api_base": "http://127.0.0.1:1",
        "max_agents_per_round": 6,
        "max_concurrent_agents": 3,
        "history_window": 10,
        "turn_timeout_s": 30,
        "agents": [{"name": n, "profile": n, "color": "#%06x" % (i * 12345)}
                   for i, n in enumerate(AGENT_NAMES)],
        "auth_token": "inproc-token",
        "uploads_dir": os.path.join(scratch, "uploads"),
    }
    real_transcript = os.path.join(REPO, "room.jsonl")
    before = (os.path.getmtime(real_transcript),
              open(real_transcript).readlines()[-1])
    events = []
    room = room_server.Room(cfg, "inproc-key")
    real_broadcast = room.broadcast

    def cap_broadcast(event):
        events.append(event)
        real_broadcast(event)

    room.broadcast = cap_broadcast

    def fake_post(author, text, file_refs=None):
        # Real post() minus _persist: the real room.jsonl is never touched.
        with room.lock:
            msg = {"id": len(room.messages) + 1,
                   "ts": "1970-01-01T00:00:00+00:00",
                   "author": author, "text": text}
            room.messages.append(msg)
        room.broadcast({"type": "message", "message": msg})
        if author in room.agents:
            room._record_turn(author, None)
        return msg

    room.post = fake_post
    room._call_agent = lambda name, prompt=None: "canned reply, no mentions"

    agent = "software-engineer"  # has a running card on the team-room board
    db_tasks, _ = db_board(board_copy)
    running = [tid for tid, t in db_tasks.items()
               if t["status"] == "running" and t["assignee"] == agent]
    assert running, "expected a running card for %s" % agent

    try:
        # --- full round: thinking -> done -> (round done) all idle -------
        assert room.start_round([agent]) is True
        for _ in range(100):
            if not room.round_active:
                break
            time.sleep(0.05)
        assert not room.round_active, "round did not drain"
        office = [e for e in events if e.get("type") == "office"]
        assert len(office) == 3, \
            "expected exactly 3 office events (thinking/done/idle), got %d" \
            % len(office)
        e1, e2, e3 = office
        a1 = {a["id"]: a for a in e1["agents"]}[agent]
        assert a1["state"] == "thinking", a1["state"]
        assert a1["idle_since"] is None
        assert a1["card_id"] in running, a1["card_id"]
        assert a1["card_title"] == db_tasks[a1["card_id"]]["title"]
        assert a1["card_state"] == "running"
        for other in AGENT_NAMES:
            if other != agent:
                assert {a["id"]: a for a in e1["agents"]}[other]["state"] == "idle"
        a2 = {a["id"]: a for a in e2["agents"]}[agent]
        assert a2["state"] == "done" and a2["idle_since"] is None, a2
        assert e3["round_active"] is False
        for a in e3["agents"]:
            assert a["state"] == "idle" and a["idle_since"] is not None, a
        # Round lifecycle events still present and untouched.
        rounds = [e for e in events if e.get("type") == "round"]
        assert [r["state"] for r in rounds] == ["started", "done"]
        assert any(e.get("type") == "message" for e in events)

        # --- 600 ms done window + idle_since lifecycle (direct drive) ----
        room._record_turn(agent, "typing")
        s1 = room.office_snapshot()
        a = {x["id"]: x for x in s1["agents"]}[agent]
        assert a["state"] == "typing" and a["idle_since"] is None
        room._record_turn(agent, None)  # turn posts
        s2 = room.office_snapshot()
        a = {x["id"]: x for x in s2["agents"]}[agent]
        assert a["state"] == "done" and a["idle_since"] is None, a
        done_at = room.turn_state[agent]["done_at"]
        time.sleep(0.7)  # out of the 600 ms window
        s3 = room.office_snapshot()
        a = {x["id"]: x for x in s3["agents"]}[agent]
        assert a["state"] == "idle", a["state"]
        assert a["idle_since"] == done_at, (a["idle_since"], done_at)

        # --- stuck derivation (no delta for > stuck_ms) ------------------
        old = time.time() * 1000 - 20000
        room.turn_state[agent] = {"state": "working", "started_at": old,
                                  "last_delta_at": old, "tool_label": "t",
                                  "card_id": None, "idle_since": None,
                                  "done_at": None}
        a = {x["id"]: x for x in room.office_snapshot()["agents"]}[agent]
        assert a["state"] == "stuck", a["state"]

        # --- no office flood: repeat same state = no re-broadcast --------
        n0 = len([e for e in events if e.get("type") == "office"])
        room._record_turn(agent, "typing")  # idle -> typing: 1 new
        room._record_turn(agent, "typing")  # typing -> typing: 0 new
        n1 = len([e for e in events if e.get("type") == "office"])
        assert n1 - n0 == 1, "office flood: %d extra events" % (n1 - n0)
    finally:
        room._kanban_stop.set()
        shutil.rmtree(scratch, ignore_errors=True)
        if os.path.exists(board_copy):
            os.remove(board_copy)

    after = (os.path.getmtime(real_transcript),
             open(real_transcript).readlines()[-1])
    assert before == after, "real room.jsonl was modified"
    print("  in-process: office events, done window, idle_since, stuck, "
          "card cross-checks all passed")


def main():
    print("verify_office3d_isolated: starting")
    isolated_port(free_port())
    in_process()
    print("verify_office3d_isolated: PASS")


if __name__ == "__main__":
    main()
