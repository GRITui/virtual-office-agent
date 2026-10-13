"""MVP-2: GET /messages?since=<id> — SSE reconnect resync filter.

Hermetic: temp-dir transcript, ephemeral loopback port, _call_agent faked
(same conventions as the rest of the suite). The seed transcript has one
message with id 1. /messages is auth-gated (MVP-3), so every HTTP read
carries the Bearer token via get_json_auth.
"""
from conftest import get_json_auth, post_json


def _post(base, token, text):
    code, body = post_json(base + "/message", {"text": text}, token=token)
    assert code == 200 and body["ok"] is True
    return body


def test_no_param_unchanged(http_room):
    """Without since the response is the full snapshot (backward compat)."""
    code, body = get_json_auth(http_room["base"] + "/messages",
                               token=http_room["token"])
    assert code == 200
    assert body["count"] == 1
    assert [m["id"] for m in body["messages"]] == [1]
    assert "since" not in body  # response shape unchanged without the param


def test_since_returns_only_greater(http_room):
    base, token = http_room["base"], http_room["token"]
    _post(base, token, "m2")  # id 2
    _post(base, token, "m3")  # id 3
    _post(base, token, "m4")  # id 4
    code, body = get_json_auth(base + "/messages?since=2", token=token)
    assert code == 200
    assert [m["id"] for m in body["messages"]] == [3, 4]
    assert body["count"] == 2
    assert body["since"] == 2
    code, body = get_json_auth(base + "/messages?since=1", token=token)
    assert [m["id"] for m in body["messages"]] == [2, 3, 4]
    code, body = get_json_auth(base + "/messages?since=0", token=token)
    assert [m["id"] for m in body["messages"]] == [1, 2, 3, 4]


def test_since_boundary_id_excluded(http_room):
    base, token = http_room["base"], http_room["token"]
    _post(base, token, "m2")  # id 2
    code, body = get_json_auth(base + "/messages?since=1", token=token)
    assert [m["id"] for m in body["messages"]] == [2]  # strict >
    code, body = get_json_auth(base + "/messages?since=2", token=token)
    assert body["messages"] == [] and body["count"] == 0  # boundary excluded


def test_since_beyond_last_empty(http_room):
    code, body = get_json_auth(http_room["base"] + "/messages?since=999",
                               token=http_room["token"])
    assert code == 200
    assert body["messages"] == [] and body["count"] == 0
    assert body["since"] == 999


def test_since_empty_param_is_full_snapshot(http_room):
    """?since= (empty) behaves like the absent param."""
    code, body = get_json_auth(http_room["base"] + "/messages?since=",
                               token=http_room["token"])
    assert code == 200
    assert body["count"] == 1
    assert "since" not in body


def test_since_invalid_400(http_room):
    for bad in ("abc", "1.5"):
        code, _ = get_json_auth(http_room["base"] + "/messages?since=" + bad,
                                token=http_room["token"])
        assert code == 400, bad


def test_since_with_q_combined(http_room):
    base, token = http_room["base"], http_room["token"]
    _post(base, token, "alpha work")  # id 2
    _post(base, token, "beta work")   # id 3
    _post(base, token, "alpha done")  # id 4
    code, body = get_json_auth(base + "/messages?since=1&q=alpha", token=token)
    assert code == 200
    assert [m["id"] for m in body["messages"]] == [2, 4]
    assert body["since"] == 1 and body["q"] == "alpha"


def test_since_method_on_preloaded_transcript(room_env):
    """Unit level: the filter over a large preloaded transcript."""
    # seed has id 1; post 49 more -> ids 2..50
    for i in range(2, 51):
        room_env.post("Krit", f"msg {i}")
    assert [m["id"] for m in room_env.since(40)] == list(range(41, 51))
    assert room_env.since(50) == []
    assert len(room_env.since(0)) == 50
    # text filter composes with the id filter
    got = room_env.since(40, q="msg 4")  # matches 41..49 (not 40, not 50)
    assert [m["id"] for m in got] == [41, 42, 43, 44, 45, 46, 47, 48, 49]
