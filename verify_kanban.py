#!/usr/bin/env python3
"""Live verify for the read-only kanban frontend. Run AFTER the server is
restarted (the running process must have the new /kanban + /board routes
loaded). Hits the live endpoints and asserts the board matches the DB.

Usage: python3 verify_kanban.py
Exit 0 on success.
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8650"

kb = json.load(urllib.request.urlopen(BASE + "/kanban", timeout=10))
assert kb["total"] == 21, kb["total"]
assert kb["status_counts"] == {"done": 20, "blocked": 1}, kb["status_counts"]
assert sum(kb["status_counts"].values()) == kb["total"]
assert "agents" in kb and len(kb["agents"]) == 6, kb.get("agents")
assert all(isinstance(t.get("parents"), list) for t in kb["tasks"])

resp = urllib.request.urlopen(BASE + "/board", timeout=10)
html = resp.read()
assert resp.status == 200 and b"kanban" in html and b"/kanban" in html

print("PASS /kanban total=%d counts=%s agents=%d; /board 200 (%d bytes)"
      % (kb["total"], kb["status_counts"], len(kb["agents"]), len(html)))
