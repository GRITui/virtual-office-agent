#!/usr/bin/env python3
"""Verify the agent->card linkage in the 3D office feed (t_cd72c684).

The drill panel can only show WHICH card a stuck/working agent is on if the
/office-state agents[] payload carries card_id + card_title + card_state.
This gate checks, against the LIVE server:

  1. /office-state: every agents[] row carries the three card fields.
  2. Agent->card cross-check: for every ACTIVE agent (thinking/working/
     typing/stuck) whose assignee has a running card on the team-room
     board, the payload's card_id/card_title/card_state must equal that
     card's id/title/status (read-only DB read — the same source
     Room.active_card_id uses).
  3. Served 3D client: /office3d page + /assets/office3d/office3d.js are
     served byte-identical to disk and contain the card-block renderer
     (the panel must render the card, not start at 'elapsed').
  4. office SSE event (best-effort): subscribe to /events and, if a
     snapshot event fires within the window, assert its agents[] rows
     carry the same three card fields (broadcast_office reuses
     office_snapshot, so /office-state and the SSE push cannot drift).

Writes a JSON artifact: verify_office3d_card.json (next to this script).
Exit 0 = all gates green.
"""
import json, os, sqlite3, sys, time, urllib.parse, urllib.request, urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE_DIR, "config.json")))
TOKEN = CFG["auth_token"]
PORT = CFG.get("port", 8651)
DB = CFG.get("kanban_db")
BASE = "http://localhost:%d" % PORT
ACTIVE = ("thinking", "working", "typing", "stuck")
CARD_FIELDS = ("card_id", "card_title", "card_state")


def get(path):
    req = urllib.request.Request(BASE + path,
                                 headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode()


def running_card(profile):
    """Most recently started running card for the assignee (read-only)."""
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        row = con.execute(
            "SELECT id, title, status FROM tasks WHERE assignee = ? "
            "AND status = 'running' ORDER BY started_at DESC, created_at DESC "
            "LIMIT 1", (profile,)).fetchone()
        return dict(id=row[0], title=row[1], status=row[2]) if row else None
    finally:
        con.close()


def main():
    out = {"port": PORT, "checks": {}, "agents": []}

    # ---- 1. /office-state carries the card fields on every row ----
    snap = json.loads(get("/office-state"))
    agents = snap["agents"]
    assert agents, "no agents in /office-state"
    missing = [(a["id"], f) for a in agents for f in CARD_FIELDS if f not in a]
    assert not missing, "agents[] rows missing card fields: %s" % missing
    out["checks"]["payload_fields"] = "ok (%d agents x 3 fields)" % len(agents)

    # ---- 2. agent->card cross-check vs the board DB ----
    mismatches, checked = [], 0
    for a in agents:
        row = dict(id=a["id"], state=a["state"], card_id=a.get("card_id"),
                   card_title=a.get("card_title"), card_state=a.get("card_state"))
        out["agents"].append(row)
        if a["state"] not in ACTIVE:
            continue
        db_card = running_card(a["id"])
        if db_card is None:
            continue  # stale card from turn start — no equality gate
        checked += 1
        if (a["card_id"], a["card_title"], a["card_state"]) != \
           (db_card["id"], db_card["title"], db_card["status"]):
            mismatches.append((a["id"], a["card_id"], db_card["id"]))
    assert not mismatches, "agent->card mismatch vs board DB: %s" % mismatches
    out["checks"]["agent_card_crosscheck"] = (
        "ok (%d active agents with a running card matched the DB)" % checked)

    # ---- 2b. In-process: drive the REAL office_snapshot() with a seeded
    # turn (deterministic — the live cross-check above is vacuous when the
    # office is idle, so this proves the card threading regardless of
    # round timing). Import has no side effects (main() is guarded).
    import threading
    sys.path.insert(0, BASE_DIR)
    import room_server
    room = room_server.Room.__new__(room_server.Room)
    room.cfg = CFG
    room.agents = {a["name"]: a for a in CFG["agents"]}
    room.agent_order = [a["name"] for a in CFG["agents"]]
    room.lock = threading.RLock()
    room.round_active = True
    room.turn_state = {}
    # Seed a working turn on the first agent, linked to a real board task:
    # a running card if any agent has one, else the newest task on the board.
    board = room_server.read_kanban_board(DB)
    tasks = board["tasks"]
    assert tasks, "board DB has no tasks"
    seed_card = None
    for t in tasks:
        if t["status"] == "running":
            seed_card = t
            break
    if seed_card is None:
        seed_card = tasks[0]
    target = room.agent_order[0]
    now_ms = time.time() * 1000.0
    room.turn_state[target] = {
        "state": "working", "started_at": now_ms - 5000,
        "last_delta_at": now_ms - 1000, "tool_label": None,
        "card_id": seed_card["id"], "idle_since": None, "done_at": None,
    }
    snap2 = room.office_snapshot()
    row2 = next(a for a in snap2["agents"] if a["id"] == target)
    assert row2["card_id"] == seed_card["id"], \
        "office_snapshot did not thread card_id: %r" % row2["card_id"]
    assert row2["card_title"] == seed_card["title"], \
        "office_snapshot did not resolve card_title: %r" % row2["card_title"]
    assert row2["card_state"] == seed_card["status"], \
        "office_snapshot did not resolve card_state: %r" % row2["card_state"]
    out["checks"]["snapshot_inprocess"] = (
        "ok (real office_snapshot: %s -> %s %r)" %
        (target, seed_card["id"], seed_card["title"][:40]))

    # ---- 3. served 3D client == disk + card-block renderer present ----
    served_page = get("/office3d")
    disk_page = open(os.path.join(BASE_DIR, "office3d.html")).read()
    assert served_page == disk_page, "served /office3d != disk"
    served_js = get("/assets/office3d/office3d.js")
    disk_js = open(os.path.join(BASE_DIR, "assets", "office3d", "office3d.js")).read()
    assert served_js == disk_js, "served office3d.js != disk"
    for marker in ('av.card = a.card_title || a.card_id',
                   'class="card"', "av.card_state", "av.card_id"):
        assert marker in served_js, "card-block renderer missing: %r" % marker
    out["checks"]["served_client"] = "ok (page + js byte-identical, card renderer present)"

    # ---- 4. office SSE event carries the same fields (best-effort) ----
    sse_ok, sse_note = False, "no office event within window (transitions only)"
    try:
        req = urllib.request.Request(
            BASE + "/events?token=" + urllib.parse.quote(TOKEN),
            headers={"Authorization": "Bearer " + TOKEN})
        with urllib.request.urlopen(req, timeout=25) as r:
            buf, deadline = b"", time.time() + 25
            while time.time() < deadline and not sse_ok:
                line = r.readline()
                if not line:
                    break
                if not line.startswith(b"data: "):
                    continue
                ev = json.loads(line[6:])
                if ev.get("type") != "office" or "agents" not in ev:
                    continue
                bad = [x["id"] for x in ev["agents"] if any(f not in x for f in CARD_FIELDS)]
                assert not bad, "office SSE agents[] missing card fields: %s" % bad
                sse_ok = True
                sse_note = "ok (office event, %d agents, fields present)" % len(ev["agents"])
    except (urllib.error.URLError, TimeoutError, OSError):
        pass  # SSE is push-only; absence of a transition in the window is not a failure
    out["checks"]["office_sse"] = sse_note

    artifact = os.path.join(BASE_DIR, "verify_office3d_card.json")
    json.dump(out, open(artifact, "w"), indent=2)
    print(json.dumps(out["checks"], indent=2))
    print("artifact: %s" % artifact)
    print("PASS")


if __name__ == "__main__":
    main()
