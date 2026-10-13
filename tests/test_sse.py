"""SSE frame shape and event payloads: message / round / turn / turn_delta.
Streaming tests exercise the REAL _call_agent parse path with a faked
urllib.request.urlopen (no live API contact)."""
import json

import room_server
from conftest import drain, events


def test_frame_shape(room_env):
    q = room_env.add_client()
    ev = {"type": "message", "message": {"id": 1, "author": "x", "text": "y"}}
    room_env.broadcast(ev)
    raw = q.get(timeout=1)
    assert raw.startswith("data: ") and raw.endswith("\n\n")
    assert json.loads(raw[len("data: "):-2]) == ev


def test_message_event(room_env):
    q = room_env.add_client()
    msg = room_env.post("Krit", "hello")
    evs = drain(q, 1)
    assert evs and evs[0]["type"] == "message"
    assert evs[0]["message"]["id"] == msg["id"]
    assert evs[0]["message"]["author"] == "Krit"
    assert evs[0]["message"]["text"] == "hello"


def test_round_and_turn_events(room_env):
    q = room_env.add_client()
    room_env.start_round(["alpha"])
    evs = events(room_env, q)
    round_states = [e["state"] for e in evs if e["type"] == "round"]
    assert round_states[0] == "started"
    assert round_states[-1] in ("done", "stopped")
    turns = [e for e in evs if e["type"] == "turn"]
    assert len(turns) == 1
    assert turns[0]["agent"] == "alpha"
    assert turns[0]["state"] == "thinking"
    # threaded active card (None here: the hermetic room has no kanban DB)
    assert "card_id" in turns[0] and turns[0]["card_id"] is None


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


def test_turn_delta_streaming(room_env, monkeypatch):
    lines = [
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
        b"event: hermes.tool.progress\n",
        b'data: {"message":"running tool"}\n',
        b'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n',
        b"data: [DONE]\n",
    ]
    # Use the real streaming parser (drop the fixture fake), but feed it
    # a faked urlopen — no live API call.
    monkeypatch.delattr(room_env, "_call_agent")
    monkeypatch.setattr(room_server.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeStream(lines))
    q = room_env.add_client()
    text = room_env._call_agent("alpha")
    assert text == "Hello"
    deltas = [e for e in drain(q, 1) if e["type"] == "turn_delta"]
    assert [(d["kind"], d["delta"]) for d in deltas] == [
        ("text", "Hel"), ("text", "lo"),
        ("tool", "running tool"),
        ("reasoning", "hmm"),
    ]
    assert all(d["agent"] == "alpha" for d in deltas)


def test_call_agent_non_streaming_fallback(room_env, monkeypatch):
    blob = json.dumps(
        {"choices": [{"message": {"content": "whole reply"}}]}).encode()

    class _FakeJson:
        headers = _FakeHeaders("application/json")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return blob

    monkeypatch.delattr(room_env, "_call_agent")
    monkeypatch.setattr(room_server.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeJson())
    assert room_env._call_agent("alpha") == "whole reply"
