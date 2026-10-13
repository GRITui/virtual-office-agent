"""Round engine: single-agent round, multi-agent batch, cascade hand-off,
stop_flag, greeting mode, mid-round merge. _call_agent is always faked."""
import time
from collections import Counter

from conftest import events


def test_single_agent_round(room_env):
    q = room_env.add_client()
    assert room_env.start_round(["alpha"]) is True
    evs = events(room_env, q)
    authors = [m["author"] for m in room_env.messages]
    assert authors.count("alpha") == 1
    started = [e for e in evs if e["type"] == "round"
               and e["state"] == "started"]
    assert started and started[0]["agents"] == ["alpha"]
    turn = [e for e in evs if e["type"] == "turn"]
    assert len(turn) == 1
    assert turn[0]["agent"] == "alpha"
    assert turn[0]["state"] == "thinking"
    # threaded active card (None here: the hermetic room has no kanban DB)
    assert "card_id" in turn[0] and turn[0]["card_id"] is None
    msg = [e for e in evs if e["type"] == "message"]
    assert msg and msg[0]["message"]["author"] == "alpha"
    assert msg[0]["message"]["text"] == "ok from alpha"
    # persisted to the TEMP transcript (seed + one reply), never the live one
    import room_server
    with open(room_server.TRANSCRIPT_PATH) as f:
        assert len([l for l in f if l.strip()]) == 2


def test_multi_agent_batch(room_env):
    q = room_env.add_client()
    assert room_env.start_round(["alpha", "beta", "gamma"]) is True
    evs = events(room_env, q)
    c = Counter(m["author"] for m in room_env.messages
                if m["author"] in ("alpha", "beta", "gamma"))
    assert c == {"alpha": 1, "beta": 1, "gamma": 1}  # each speaks exactly once
    started = [e for e in evs if e["type"] == "round"
               and e["state"] == "started"]
    assert started[0]["agents"] == ["alpha", "beta", "gamma"]
    # one dispatch batch of 3 (conc=3): three thinking turns
    assert sorted(e["agent"] for e in evs if e["type"] == "turn") == \
        ["alpha", "beta", "gamma"]


def test_cascade_handoff(room_env):
    replies = {"alpha": "ok, passing to @beta", "beta": "done"}
    order = []

    def fake(name, prompt=None):
        order.append(name)
        return replies[name]

    room_env._call_agent = fake
    q = room_env.add_client()
    assert room_env.start_round(["alpha"]) is True
    events(room_env, q)
    assert order == ["alpha", "beta"]  # beta only via the hand-off
    authors = [m["author"] for m in room_env.messages]
    assert authors.index("alpha") < authors.index("beta")


def test_stop_flag(room_env):
    def fake(name, prompt=None):
        if name == "alpha":
            assert room_env.stop_round() is True
        return "alpha done @beta"

    room_env._call_agent = fake
    q = room_env.add_client()
    assert room_env.start_round(["alpha"]) is True
    evs = events(room_env, q)
    authors = [m["author"] for m in room_env.messages]
    assert "beta" not in authors  # cascade suppressed after stop
    assert any(e["type"] == "round" and e["state"] == "stopping" for e in evs)
    # Pre-existing quirk: _run_round resets stop_flag BEFORE the final
    # broadcast, so a stopped round still reports state "done".
    assert [e["state"] for e in evs if e["type"] == "round"][-1] == "done"


def test_greeting_mode_no_cascade(room_env):
    prompts = []

    def fake(name, prompt=None):
        prompts.append(prompt)
        return "hello! @beta"

    room_env._call_agent = fake
    q = room_env.add_client()
    assert room_env.start_round(["alpha"], greet=True) is True
    events(room_env, q)
    authors = [m["author"] for m in room_env.messages]
    assert "beta" not in authors  # a greeting must not cascade a round
    assert "GREETING MODE" in prompts[0]


def test_mid_round_merge(room_env):
    def fake(name, prompt=None):
        if name == "alpha":
            time.sleep(0.3)
        return f"hi {name}"

    room_env._call_agent = fake
    q = room_env.add_client()
    assert room_env.start_round(["alpha"]) is True
    time.sleep(0.1)  # alpha's turn is in flight
    assert room_env.start_round(["beta"]) is False  # merged, not a new round
    events(room_env, q)
    authors = [m["author"] for m in room_env.messages]
    assert "alpha" in authors and "beta" in authors
