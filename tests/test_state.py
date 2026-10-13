"""Initial-state-sync: GET /state + per-agent in-flight turn tracking.

A fresh office client shows 'idle' until the next turn_delta SSE. The fix:
the server tracks per-agent in-flight turn state (turn_state) and exposes an
authed GET /state snapshot; the client fetches it once on load to seed the
scene so a round already in flight lights up immediately.

Hermetic: temp-dir transcript, ephemeral loopback port, _call_agent faked
(same conventions as the rest of the suite). /state is auth-gated like
/messages + /kanban.
"""
import time

from conftest import get_json_auth, post_json, wait_round


def test_state_idle_shape(http_room):
    """Idle room: /state returns the snapshot shape with no in-flight turns."""
    code, body = get_json_auth(http_room["base"] + "/state",
                               token=http_room["token"])
    assert code == 200
    assert body["round_active"] is False
    assert body["turns"] == {}
    assert body["pending"] == []
    assert body["spoken"] == []
    assert "greet_mode" in body


def test_state_requires_auth(http_room):
    """No token -> 401 (auth-gated like /messages + /kanban)."""
    code, _ = get_json_auth(http_room["base"] + "/state", token=None)
    assert code == 401


def test_record_turn_tracks_and_clears(room_env):
    """Unit level: _record_turn mirrors the client's applyEvent semantics."""
    room = room_env
    # dispatch -> thinking
    room._record_turn("alpha", "thinking")
    assert room.turn_state["alpha"]["state"] == "thinking"
    started = room.turn_state["alpha"]["started_at"]
    # tool delta -> working + tool_label
    room._record_turn("alpha", "working", tool_label="terminal: ls -la")
    assert room.turn_state["alpha"]["state"] == "working"
    assert room.turn_state["alpha"]["tool_label"] == "terminal: ls -la"
    # started_at preserved across deltas
    assert room.turn_state["alpha"]["started_at"] == started
    # reasoning delta -> thinking, tool_label persists
    room._record_turn("alpha", "thinking")
    assert room.turn_state["alpha"]["state"] == "thinking"
    assert room.turn_state["alpha"]["tool_label"] == "terminal: ls -la"
    # text delta -> typing
    room._record_turn("alpha", "typing")
    assert room.turn_state["alpha"]["state"] == "typing"
    # post -> cleared
    room._record_turn("alpha", None)
    assert "alpha" not in room.turn_state
    # timestamps are epoch ms (the client's Date.now() scale)
    room._record_turn("beta", "thinking")
    ts = room.turn_state["beta"]["last_delta_at"]
    assert abs(ts - time.time() * 1000.0) < 5000


def test_state_reflects_inflight_turn(http_room):
    """Mid-round: /state shows the in-flight agent with its live state."""
    room = http_room["room"]
    base, token = http_room["base"], http_room["token"]
    # Make the turn take a moment so it is in flight when we snapshot.
    room._call_agent = lambda name, prompt=None: (
        time.sleep(0.5), f"ok from {name}")[1]
    # Start a single-agent round by mentioning alpha.
    code, body = post_json(base + "/message", {"text": "@alpha ping"},
                           token=token)
    assert code == 200 and body["ok"] is True
    # Poll /state until the turn is in flight (round_active + alpha in turns).
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
    t = snap["turns"]["alpha"]
    assert t["state"] in ("thinking", "working", "typing")
    assert t["started_at"] > 0 and t["last_delta_at"] >= t["started_at"]
    # Wait for the round to finish; the turn must be cleared.
    assert wait_round(room, timeout=5)
    code, snap = get_json_auth(base + "/state", token=token)
    assert code == 200
    assert snap["round_active"] is False
    assert snap["turns"] == {}
