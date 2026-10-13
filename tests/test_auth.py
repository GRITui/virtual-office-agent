"""Auth: 401 without token / 200 with token on /message, /stop, /upload,
plus the auth-disabled (backward compatible) path. Real HTTP over loopback
on an ephemeral port; _call_agent is faked in the fixtures."""
from conftest import post_json


def test_message_401_without_token(http_room):
    code, body = post_json(http_room["base"] + "/message", {"text": "hi"})
    assert code == 401 and body["error"] == "unauthorized"


def test_message_401_wrong_token(http_room):
    code, _ = post_json(http_room["base"] + "/message",
                        {"text": "hi"}, token="wrong")
    assert code == 401


def test_message_200_with_token(http_room):
    code, body = post_json(http_room["base"] + "/message",
                           {"text": "hello room"}, token=http_room["token"])
    assert code == 200 and body["ok"] is True
    assert http_room["room"].messages[-1]["text"] == "hello room"


def test_stop_401_without_token(http_room):
    code, _ = post_json(http_room["base"] + "/stop", {})
    assert code == 401


def test_stop_200_with_token(http_room):
    code, body = post_json(http_room["base"] + "/stop", {},
                           token=http_room["token"])
    assert code == 200 and body["ok"] is True


def test_upload_401_without_token(http_room):
    code, _ = post_json(http_room["base"] + "/upload",
                        {"name": "a.txt", "data_base64": "aGk="})
    assert code == 401


def test_upload_200_with_token(http_room):
    code, body = post_json(http_room["base"] + "/upload",
                           {"name": "a.txt", "data_base64": "aGk="},
                           token=http_room["token"])
    assert code == 200 and body["ok"] is True
    assert body["file"]["name"] == "a.txt" and body["file"]["size"] == 2


def test_message_200_when_auth_disabled(http_room_noauth):
    # No auth_token in config -> gate is off (backward compatible).
    code, body = post_json(http_room_noauth["base"] + "/message",
                           {"text": "hi"})
    assert code == 200 and body["ok"] is True


def test_message_empty_400(http_room):
    code, body = post_json(http_room["base"] + "/message",
                           {"text": "   "}, token=http_room["token"])
    assert code == 400


# ---------- MVP-3: auth on ALL reads (/messages, /kanban, /export, /events) ----------
import sqlite3
import urllib.error
import urllib.request

from conftest import get_json_auth, get_raw


def _make_kanban_db(path):
    """Minimal hermetic kanban DB matching read_kanban_board's schema."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, assignee TEXT,"
        " status TEXT, priority INTEGER, created_by TEXT, created_at INTEGER,"
        " completed_at INTEGER, block_kind TEXT, consecutive_failures INTEGER)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT,"
        " outcome TEXT, summary TEXT, ended_at INTEGER, status TEXT)")
    con.execute(
        "INSERT INTO tasks (id, title, assignee, status, priority, created_by,"
        " created_at, completed_at, block_kind, consecutive_failures)"
        " VALUES ('t1', 'seed task', 'alpha', 'todo', 1, 'Krit', 1, NULL, NULL, 0)")
    con.commit()
    con.close()


def test_messages_401_without_token(http_room):
    code, body = get_json_auth(http_room["base"] + "/messages")
    assert code == 401 and body["error"] == "unauthorized"


def test_messages_401_wrong_token(http_room):
    code, _ = get_json_auth(http_room["base"] + "/messages", token="wrong")
    assert code == 401


def test_messages_200_with_token(http_room):
    code, body = get_json_auth(http_room["base"] + "/messages",
                               token=http_room["token"])
    assert code == 200
    assert body["count"] == 1
    assert body["messages"][0]["text"] == "seed message"


def test_messages_search_401_without_token(http_room):
    code, _ = get_json_auth(http_room["base"] + "/messages?q=seed")
    assert code == 401


def test_messages_search_200_with_token(http_room):
    code, body = get_json_auth(http_room["base"] + "/messages?q=seed",
                               token=http_room["token"])
    assert code == 200 and body["count"] == 1


def test_kanban_401_without_token(http_room):
    code, body = get_json_auth(http_room["base"] + "/kanban")
    assert code == 401 and body["error"] == "unauthorized"


def test_kanban_200_with_token(http_room):
    _make_kanban_db(http_room["room"].cfg["kanban_db"])
    code, body = get_json_auth(http_room["base"] + "/kanban",
                               token=http_room["token"])
    assert code == 200
    assert body["total"] == 1 and body["tasks"][0]["id"] == "t1"


def test_export_json_401_without_token(http_room):
    code, _ = get_json_auth(http_room["base"] + "/export?format=json")
    assert code == 401


def test_export_json_200_with_token(http_room):
    code, body = get_json_auth(http_room["base"] + "/export?format=json",
                               token=http_room["token"])
    assert code == 200 and len(body["messages"]) == 1


def test_export_md_401_without_token(http_room):
    code, _, _ = get_raw(http_room["base"] + "/export?format=md")
    assert code == 401


def test_export_md_200_with_token(http_room):
    code, headers, body = get_raw(http_room["base"] + "/export?format=md",
                                  token=http_room["token"])
    assert code == 200
    assert "text/markdown" in (headers.get("Content-Type") or "")
    assert b"team-room transcript" in body


def test_events_401_without_token(http_room):
    # EventSource cannot set headers — no ?token= -> 401 before the stream.
    try:
        urllib.request.urlopen(http_room["base"] + "/events", timeout=10)
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_events_401_wrong_token(http_room):
    try:
        urllib.request.urlopen(
            http_room["base"] + "/events?token=wrong", timeout=10)
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_events_200_with_token(http_room):
    req = urllib.request.Request(
        http_room["base"] + "/events?token=" + http_room["token"])
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert "text/event-stream" in (r.headers.get("Content-Type") or "")
        line = r.readline().decode("utf-8", "replace")
        assert line.startswith(": connected")


def test_health_stays_open(http_room):
    # Liveness probe must remain unauthenticated.
    code, body = get_json_auth(http_room["base"] + "/health")
    assert code == 200 and body["auth_enabled"] is True


def test_reads_200_when_auth_disabled(http_room_noauth):
    # Backward compatible: no auth_token -> all reads open.
    code, body = get_json_auth(http_room_noauth["base"] + "/messages")
    assert code == 200 and body["count"] == 1
    req = urllib.request.Request(http_room_noauth["base"] + "/events")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert r.readline().decode("utf-8", "replace").startswith(": connected")
