#!/usr/bin/env python3
"""qa-journey liveness capture — read-only /events tap on live 8651.
Logs every SSE data: line with monotonic + wall timestamps to liveness_capture.jsonl.
Exits on round done/stopped or after 1500s. No side effects."""
import json, time, urllib.request, sys

URL = "http://127.0.0.1:8651/events"
OUT = "/Users/grit/.hermes/team-room/liveness_capture.jsonl"
t0 = time.monotonic()
wall0 = time.time()
n = 0
with urllib.request.urlopen(URL, timeout=1500) as r, open(OUT, "w") as f:
    f.write(json.dumps({"ev": "capture_start", "wall": wall0}) + "\n")
    for raw in r:
        dt = time.monotonic() - t0
        if dt > 1500:
            break
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except Exception:
            payload = {"raw": line}
        rec = {"t": round(dt, 3), "wall": time.time(), "ev": payload}
        f.write(json.dumps(rec) + "\n")
        f.flush()
        n += 1
        etype = payload.get("type") if isinstance(payload, dict) else None
        if etype == "round" and payload.get("state") in ("done", "stopped", "stopping"):
            f.write(json.dumps({"ev": "capture_end", "t": round(dt, 3), "reason": f"round {payload.get('state')}"}) + "\n")
            break
    else:
        f.write(json.dumps({"ev": "capture_end", "t": round(dt, 3), "reason": "timeout/stream-close"}) + "\n")
print(f"captured {n} events in {round(time.monotonic()-t0,1)}s -> {OUT}")
