"""Shared fixtures: hermetic team-room instances in a temp dir.

Nothing here touches the live room:
- the transcript path is monkeypatched to a tmp file (own room.jsonl),
- the HTTP server binds 127.0.0.1:0 (ephemeral port, never 8651),
- uploads_dir points into the tmp dir,
- _call_agent is always replaced with a fake, so no live Hermes API call
  can happen (the streaming-parse tests re-enable the real method but
  monkeypatch urllib.request.urlopen with a fake response).
"""
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

TEAM_ROOM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TEAM_ROOM_DIR not in sys.path:
    sys.path.insert(0, TEAM_ROOM_DIR)

import room_server  # noqa: E402

AGENTS = [
    {"name": "alpha", "profile": "alpha", "color": "#ffffff"},
    {"name": "beta", "profile": "beta", "color": "#000000"},
    {"name": "gamma", "profile": "gamma", "color": "#808080"},
]
TOKEN = "test-token"


def make_cfg(tmp_path, **over):
    cfg = {
        "port": 0,
        # Nonexistent DB: the kanban watcher hits the except path and idles.
        "kanban_db": str(tmp_path / "kanban.db"),
        # Never contacted: _call_agent is faked in every fixture.
        "api_base": "http://127.0.0.1:9",
        "key_file": str(tmp_path / "nokey.env"),
        "key_var": "API_SERVER_KEY",
        "max_agents_per_round": 6,
        "max_concurrent_agents": 3,
        "history_window": 60,
        "turn_timeout_s": 5,
        "agents": AGENTS,
        "auth_token": TOKEN,
        "uploads_dir": str(tmp_path / "uploads"),
    }
    cfg.update(over)
    return cfg


def build_room(tmp_path, monkeypatch, cfg_over=None):
    cfg = make_cfg(tmp_path, **(cfg_over or {}))
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    transcript = tmp_path / "room.jsonl"
    transcript.write_text(json.dumps(
        {"id": 1, "ts": "2026-01-01T00:00:00+00:00", "author": "Krit",
         "text": "seed message"}) + "\n")
    monkeypatch.setattr(room_server, "TRANSCRIPT_PATH", str(transcript))
    monkeypatch.setattr(room_server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    room = room_server.Room(cfg, "test-key")
    # No live agent turns, ever:
    room._call_agent = lambda name, prompt=None: f"ok from {name}"
    return room


@pytest.fixture
def room_env(tmp_path, monkeypatch):
    room = build_room(tmp_path, monkeypatch)
    yield room
    room._kanban_stop.set()


@pytest.fixture
def room_env_noauth(tmp_path, monkeypatch):
    room = build_room(tmp_path, monkeypatch, {"auth_token": None})
    yield room
    room._kanban_stop.set()


@pytest.fixture
def http_room(room_env):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              room_server.make_handler(room_env))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield {"room": room_env,
           "base": f"http://127.0.0.1:{srv.server_address[1]}",
           "token": room_env.auth_token}
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def http_room_noauth(room_env_noauth):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              room_server.make_handler(room_env_noauth))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield {"room": room_env_noauth,
           "base": f"http://127.0.0.1:{srv.server_address[1]}",
           "token": None}
    srv.shutdown()
    srv.server_close()


# ---------- helpers (tests import these from conftest) ----------

def post_json(url, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def get_json_auth(url, token=None):
    """GET with optional Bearer token; returns (status, parsed-json) even on 4xx."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def get_raw(url, token=None):
    """GET with optional Bearer token; returns (status, headers, body-bytes)."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def wait_round(room, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with room.lock:
            active = room.round_active
        if not active:
            return True
        time.sleep(0.02)
    return False


def drain(q, timeout=1.0):
    """Read all pending SSE payloads off a client queue; parse the JSON body.

    Payloads have the exact SSE frame shape: `data: {json}\\n\\n`.
    """
    out = []
    while True:
        try:
            raw = q.get(timeout=timeout)
        except queue.Empty:
            break
        out.append(json.loads(raw[len("data: "):].strip()))
    return out


def events(room, q, settle=0.15):
    """Wait for the round to end, let the final broadcast land, drain."""
    assert wait_round(room), "round did not finish in time"
    time.sleep(settle)
    return drain(q, 0.5)
