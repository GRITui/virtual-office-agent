"""Stuck-lamp gating (t_f6904af5): /office-state must not flag 'stuck' for a
no-card turn (reap to idle) nor for a healthy long tool call (keep 'working').
Only a card-backed turn with no tool in flight and no delta past the
threshold is 'stuck'.
"""
import time


NOW = lambda: time.time() * 1000.0
STALE = 20000  # 20s ago, well past OFFICE_STUCK_MS (8s)
FRESH = 1000   # 1s ago, within threshold


def _snap_state(room, agent):
    snap = room.office_snapshot()
    for a in snap["agents"]:
        if a["id"] == agent:
            return a
    raise AssertionError("%s not in snapshot" % agent)


def _set(room, agent, state, last, tool_label=None, card_id=None,
         idle_since=None):
    room.turn_state[agent] = {
        "state": state, "started_at": NOW() - 60000,
        "last_delta_at": last, "tool_label": tool_label,
        "card_id": card_id, "idle_since": idle_since, "done_at": None,
    }


def test_no_card_stale_reaped_to_idle(room_env):
    """No card + no delta > 8s -> idle (reaped), NOT stuck. The bug case."""
    room = room_env
    _set(room, "alpha", "working", NOW() - STALE)
    a = _snap_state(room, "alpha")
    assert a["state"] == "idle", a
    assert a["idle_since"] is not None
    assert a["state"] != "stuck"


def test_no_card_fresh_stays_active(room_env):
    """No card + recent delta -> stays active (not reaped, not stuck)."""
    room = room_env
    _set(room, "alpha", "working", NOW() - FRESH)
    a = _snap_state(room, "alpha")
    assert a["state"] == "working", a
    assert a["idle_since"] is None


def test_long_tool_call_not_stuck(room_env):
    """Card set + tool in flight + no delta > 8s -> 'working', NOT stuck.
    A healthy long tool call (build/install) reads 'working <tool>'."""
    room = room_env
    _set(room, "alpha", "working", NOW() - STALE,
         tool_label="terminal: npm install", card_id="t_abc123")
    a = _snap_state(room, "alpha")
    assert a["state"] == "working", a
    assert a["state"] != "stuck"
    assert a["tool_label"] == "terminal: npm install"


def test_genuine_stuck(room_env):
    """Card set + no tool in flight + no delta > 8s -> 'stuck'."""
    room = room_env
    _set(room, "alpha", "thinking", NOW() - STALE, card_id="t_abc123")
    a = _snap_state(room, "alpha")
    assert a["state"] == "stuck", a


def test_card_fresh_not_stuck(room_env):
    """Card set + recent delta -> raw state (not stuck)."""
    room = room_env
    _set(room, "alpha", "working", NOW() - FRESH, card_id="t_abc123")
    a = _snap_state(room, "alpha")
    assert a["state"] == "working", a


def test_round_end_idle_regression(room_env):
    """Round-end idle entry keeps its server-side idle_since (regression)."""
    room = room_env
    _set(room, "alpha", "idle", None, idle_since=NOW() - 30000)
    a = _snap_state(room, "alpha")
    assert a["state"] == "idle", a
    assert a["idle_since"] is not None
