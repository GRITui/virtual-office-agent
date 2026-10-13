"""Drill-in backend: card_id threaded into turn_state + /state + the
turn/turn_delta SSE events, and the kanban event naming the agent whose
card changed.

The suite's default hermetic room has NO kanban DB (active_card_id -> None),
so these tests build a real temp kanban DB (the columns read_kanban_board
reads) and point a room at it. The kanban-event test drives the real
_kanban_watch loop against the temp DB.
"""
import sqlite3
import threading
import time

import room_server
from conftest import build_room, drain, events, get_json_auth, wait_round


def make_kanban_db(path, tasks):
    """Create a minimal kanban DB with exactly the columns read_kanban_board
    reads (tasks + task_links + task_runs)."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, assignee TEXT, "
        "status TEXT, priority INTEGER, created_by TEXT, created_at TEXT, "
        "started_at TEXT, completed_at TEXT, block_kind TEXT, "
        "consecutive_failures INTEGER)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    # task_runs needs an `id` PK: read_kanban_board's latest-run query does
    # `WHERE id IN (SELECT MAX(id) FROM task_runs GROUP BY task_id)`.
    con.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, "
        "outcome TEXT, summary TEXT, ended_at TEXT, status TEXT)")
    for t in tasks:
        con.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (t["id"], t.get("title", t["id"]), t["assignee"], t["status"],
             t.get("priority", 0), t.get("created_by", "Krit"),
             t.get("created_at", "2026-01-01T00:00:00+00:00"),
             t.get("started_at"), t.get("completed_at"), t.get("block_kind"),
             t.get("consecutive_failures", 0)))
    con.commit()
    con.close()


def set_status(db_path, task_id, status):
    con = sqlite3.connect(db_path)
    con.execute("UPDATE tasks SET status = ? WHERE id = ?",
                (status, task_id))
    con.commit()
    con.close()


def room_with_kanban(tmp_path, monkeypatch, tasks):
    db = str(tmp_path / "kanban.db")
    make_kanban_db(db, tasks)
    room = build_room(tmp_path, monkeypatch, {"kanban_db": db})
    return room, db


# ---------- unit: active_card_id + _record_turn card_id ----------

def test_active_card_id(tmp_path, monkeypatch):
    room, _ = room_with_kanban(tmp_path, monkeypatch, [
        {"id": "t_a", "assignee": "alpha", "status": "running"},
        {"id": "t_b", "assignee": "beta", "status": "ready"},
    ])
    try:
        assert room.active_card_id("alpha") == "t_a"
        assert room.active_card_id("beta") is None  # not running
        assert room.active_card_id("gamma") is None  # no card
    finally:
        room._kanban_stop.set()


def test_record_turn_stores_and_persists_card_id(tmp_path, monkeypatch):
    room, _ = room_with_kanban(tmp_path, monkeypatch, [
        {"id": "t_a", "assignee": "alpha", "status": "running"},
    ])
    try:
        room._record_turn("alpha", "thinking", card_id="t_a")
        assert room.turn_state["alpha"]["card_id"] == "t_a"
        # a delta that doesn't restate card_id keeps the existing one
        room._record_turn("alpha", "typing")
        assert room.turn_state["alpha"]["card_id"] == "t_a"
        # a later delta can restate it
        room._record_turn("alpha", "working", card_id="t_z")
        assert room.turn_state["alpha"]["card_id"] == "t_z"
        # post -> cleared (card_id gone with the entry)
        room._record_turn("alpha", None)
        assert "alpha" not in room.turn_state
    finally:
        room._kanban_stop.set()


# ---------- SSE: turn + turn_delta carry card_id ----------

def test_turn_event_carries_card_id(tmp_path, monkeypatch):
    room, _ = room_with_kanban(tmp_path, monkeypatch, [
        {"id": "t_a", "assignee": "alpha", "status": "running"},
    ])
    try:
        q = room.add_client()
        assert room.start_round(["alpha"]) is True
        evs = events(room, q)
        turn = [e for e in evs if e["type"] == "turn"]
        assert turn and turn[0]["agent"] == "alpha"
        assert turn[0]["card_id"] == "t_a"
    finally:
        room._kanban_stop.set()


class _FakeHeaders:
    def __init__(self, ctype):
        self._ctype = ctype

    def get(self, key, default=None):
        return self._ctype if key == "Content-Type" else default


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines
        self.headers = _FakeHeaders("text/event-stream")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_turn_delta_carries_card_id(tmp_path, monkeypatch):
    room, _ = room_with_kanban(tmp_path, monkeypatch, [
        {"id": "t_a", "assignee": "alpha", "status": "running"},
    ])
    try:
        lines = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
            b"event: hermes.tool.progress\n",
            b'data: {"message":"running tool"}\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n',
            b"data: [DONE]\n",
        ]
        # Use the REAL streaming parser (drop the fixture fake) with a faked
        # urlopen — no live API call.
        monkeypatch.delattr(room, "_call_agent")
        monkeypatch.setattr(room_server.urllib.request, "urlopen",
                            lambda req, timeout=None: _FakeStream(lines))
        q = room.add_client()
        text = room._call_agent("alpha")
        assert text == "Hel"
        deltas = [e for e in drain(q, 1) if e["type"] == "turn_delta"]
        assert len(deltas) == 3  # text, tool, reasoning
        assert all(d["card_id"] == "t_a" for d in deltas)
        assert all(d["agent"] == "alpha" for d in deltas)
    finally:
        room._kanban_stop.set()


# ---------- /state snapshot: card_id per active agent ----------

def test_state_shows_card_id(tmp_path, monkeypatch):
    room, _ = room_with_kanban(tmp_path, monkeypatch, [
        {"id": "t_a", "assignee": "alpha", "status": "running"},
    ])
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              room_server.make_handler(room))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    token = room.auth_token
    try:
        # Make the turn take a moment so it is in flight when we snapshot.
        room._call_agent = lambda name, prompt=None: (
            time.sleep(0.5), f"ok from {name}")[1]
        room.start_round(["alpha"])
        snap = None
        deadline = time.time() + 3
        while time.time() < deadline:
            code, snap = get_json_auth(base + "/state", token=token)
            assert code == 200
            if snap["round_active"] and "alpha" in snap["turns"]:
                break
            time.sleep(0.02)
        assert snap["round_active"] is True
        assert "alpha" in snap["turns"]
        assert snap["turns"]["alpha"]["card_id"] == "t_a"
        assert wait_round(room, timeout=5)
        code, snap = get_json_auth(base + "/state", token=token)
        assert code == 200 and snap["turns"] == {}
    finally:
        room._kanban_stop.set()
        srv.shutdown()
        srv.server_close()


# ---------- kanban event names the agent whose card changed ----------

def test_kanban_event_names_agent(tmp_path, monkeypatch):
    room, db = room_with_kanban(tmp_path, monkeypatch, [
        {"id": "t_a", "assignee": "alpha", "status": "ready"},
    ])
    q = room.add_client()
    try:
        # Let the watcher record its baseline (alpha=ready), then flip the
        # card to running; the next poll must name alpha.
        time.sleep(0.5)
        set_status(db, "t_a", "running")
        deadline = time.time() + 6
        kanban = None
        while time.time() < deadline and kanban is None:
            for e in drain(q, 0.3):
                if e.get("type") == "kanban" and e.get("changed"):
                    kanban = e
                    break
        assert kanban is not None, "no kanban change event within 6s"
        assert "alpha" in kanban["agents"]
        assert "t_a" in kanban["changed_cards"]
    finally:
        room._kanban_stop.set()
