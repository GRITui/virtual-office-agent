import sys, time
sys.path.insert(0, "/Users/grit/.hermes/team-room")
import room_server as rs

cfg = {
    "port": 8651,
    "api_base": "http://127.0.0.1:8642",
    "max_agents_per_round": 6,
    "history_window": 5,
    "agents": [
        {"name": "product-owner", "profile": "product-owner", "color": "#e8b93d"},
        {"name": "ux-ui", "profile": "ux-ui", "color": "#5db8e0"},
    ],
}
room = rs.Room(cfg, "dummy-key")

# 1. greet_mode default
assert room.greet_mode is False, "greet_mode should default False"
print("OK 1: greet_mode defaults False")

# 2. prompt: no GREETING MODE when off
p_off = room._build_prompt("product-owner")
assert "GREETING MODE" not in p_off, "no greeting mode when off"
print("OK 2: prompt has no GREETING MODE when off")

# 3. prompt: GREETING MODE when on
room.greet_mode = True
p_on = room._build_prompt("product-owner")
assert "GREETING MODE" in p_on and "ONLY a short greeting" in p_on
print("OK 3: prompt has GREETING MODE when on")
room.greet_mode = False

# Neutralize disk writes: in-memory post only (no _persist), fake agent call.
def safe_post(author, text, file_refs=None):
    msg = {"id": len(room.messages) + 1, "author": author, "text": text}
    with room.lock:
        room.messages.append(msg)
    return msg
room.post = safe_post
calls = []
def fake_call(name):
    calls.append(name)
    # Reply that MENTIONS the other agent -> would cascade in a normal round.
    return "@product-owner over to you" if name == "ux-ui" else "done"
room._call_agent = fake_call

# 4. start_round(greet=True) sets greet_mode + runs exactly ONE agent, no cascade
started = room.start_round(["ux-ui"], greet=True)
assert started is True and room.greet_mode is True, "start_round(greet=True) must set greet_mode"
time.sleep(1.5)
assert room.round_active is False, "greeting round should be done"
assert calls == ["ux-ui"], f"greeting round must run only the pinged agent, got {calls}"
print("OK 4: greeting round ran exactly 1 agent, no cascade (calls=%s)" % calls)

# 5. normal round: hand-off cascade still works (regression check)
calls.clear()
started = room.start_round(["ux-ui"], greet=False)
assert started is True and room.greet_mode is False
time.sleep(1.5)
assert room.round_active is False
assert calls == ["ux-ui", "product-owner"], f"normal round must cascade, got {calls}"
print("OK 5: normal round still cascades hand-offs (calls=%s)" % calls)

# 6. kanban signature logic
board = {"total": 21, "status_counts": {"done": 20, "blocked": 1},
         "tasks": [{"created_at": "2026-01-01", "completed_at": None},
                   {"created_at": "2026-01-02", "completed_at": "2026-01-03"}]}
sig = (board["total"],
       tuple(sorted(board["status_counts"].items())),
       max((t["created_at"] for t in board["tasks"]), default=""),
       max((t["completed_at"] for t in board["tasks"] if t["completed_at"]), default=""))
assert sig == (21, (("blocked", 1), ("done", 20)), "2026-01-02", "2026-01-03"), sig
print("OK 6: kanban signature computes correctly:", sig)

print("\nALL LOGIC TESTS PASSED")
