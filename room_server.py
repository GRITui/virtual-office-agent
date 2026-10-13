#!/usr/bin/env python3
"""team-room: a local shared channel where Hermes agent profiles talk to each other.

Stdlib-only. Loopback only. Single source of truth: the JSONL transcript.

Sprint 4 additions (gap closure vs Open WebUI):
  - Auth: bearer-token gate on ALL routes carrying room data — writes
    (/message, /stop, /upload), reads (/messages, /kanban, /export, /files),
    and the SSE stream (/events via ?token= query param, constant-time
    compare — EventSource cannot set headers). Only when config has
    "auth_token"; absent = auth disabled, fully backward compatible.
    /health and the static page shells stay open (liveness / UI hosts).
  - Streaming: _call_agent opens stream:true, relays content deltas +
    hermes.tool.progress as `turn_delta` SSE events on /events.
  - File ingestion: POST /upload persists to uploads/<id>; /message carries
    file_refs; _build_prompt injects the path (text files inlined).
  - History: GET /messages?q=<substr> (search) and GET /export?format=md|json.

Usage:
  python3 room_server.py serve            # run the room server (default)
  python3 room_server.py say "text"       # post a human message (mentions trigger agents)
  python3 room_server.py tail             # follow the transcript
  python3 room_server.py agents           # list configured agents
"""

import base64
import json
import os
import queue
import re
import secrets
import signal
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TRANSCRIPT_PATH = os.path.join(BASE_DIR, "room.jsonl")
HTML_PATH = os.path.join(BASE_DIR, "room.html")
BOARD_HTML_PATH = os.path.join(BASE_DIR, "board.html")
WEBUI_CHAT_HTML_PATH = os.path.join(BASE_DIR, "webui", "index.html")
OFFICE_HTML_PATH = os.path.join(BASE_DIR, "office.html")
STACK_HTML_PATH = os.path.join(BASE_DIR, "tech-stack.html")
# 3D office host page (open page shell, like /office): reads tr_token from
# localStorage and iframes /unity/index.html?token=<tr_token>. Created by
# the Unity tooling card; served at /office3d.
OFFICE3D_HTML_PATH = os.path.join(BASE_DIR, "office3d.html")
# Read-only Hermes kanban board. Overridable via config "kanban_db".
KANBAN_DB_DEFAULT = os.path.expanduser("~/.hermes/kanban.db")
# 3D office feed: a turn open with no delta for this long reads "stuck".
# Matches the office client's STUCK_MS so the 2D + 3D scenes agree.
OFFICE_STUCK_MS = 8000
# 3D office feed: after a turn posts, the agent reports "done" for this
# window (matches office.html's done-flash: state "done" on the message
# event, deleted 600 ms later). After it, /office-state reports "idle"
# with idle_since = the post time.
OFFICE_DONE_MS = 600
# 3D office static content (OPEN, no auth — same as the page shells):
# /assets/office3d/* -> assets/office3d/ (glb/json/png scene assets),
# /unity/* -> build/office3d-webgl/ (Unity WebGL build output).
OFFICE3D_ASSETS_DIR = os.path.join(BASE_DIR, "assets", "office3d")
UNITY_BUILD_DIR = os.path.join(BASE_DIR, "build", "office3d-webgl")
# MIME map for the static routes (default: application/octet-stream).
STATIC_MIME = {
    ".glb": "model/gltf-binary",
    ".json": "application/json",
    ".png": "image/png",
    ".html": "text/html",
    ".js": "application/javascript",
    ".wasm": "application/wasm",
    ".data": "application/octet-stream",
}

HUMAN = "Krit"
ALL_TOKENS = {"all", "everyone"}
MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")

# Files at or under this size are inlined into the agent prompt (text only).
MAX_INLINE_BYTES = 16000
TEXT_EXTS = (".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".py", ".js",
             ".ts", ".jsx", ".tsx", ".csv", ".html", ".css", ".xml", ".toml",
             ".ini", ".log", ".sh", ".sql", ".c", ".h", ".cpp", ".rs", ".go")
TEXT_MIMES = ("text/", "application/json", "application/xml", "application/x-yaml",
              "application/yaml", "application/javascript", "application/x-python",
              "application/csv", "application/x-sh")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def read_kanban_board(db_path):
    """Read-only snapshot of the Hermes kanban board. Opens the DB in ?mode=ro
    so the web UI can never write to it. Returns {total, status_counts, tasks}
    where each task carries parent/child links and its latest run outcome."""
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    try:
        tasks = con.execute(
            """SELECT id, title, assignee, status, priority,
                       created_by, created_at, completed_at,
                       block_kind, consecutive_failures
                FROM tasks ORDER BY created_at DESC"""
        ).fetchall()
        links = con.execute(
            "SELECT parent_id, child_id FROM task_links"
        ).fetchall()
        runs = con.execute(
            """SELECT task_id, outcome, summary, ended_at, status
               FROM task_runs
               WHERE id IN (SELECT MAX(id) FROM task_runs GROUP BY task_id)"""
        ).fetchall()

        parent_of, children = {}, {}
        for r in links:
            parent_of.setdefault(r["child_id"], []).append(r["parent_id"])
            children.setdefault(r["parent_id"], []).append(r["child_id"])
        run_by_task = {r["task_id"]: r for r in runs}

        status_counts, out = {}, []
        for t in tasks:
            tid = t["id"]
            status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
            run = run_by_task.get(tid)
            out.append({
                "id": tid,
                "title": t["title"],
                "assignee": t["assignee"],
                "status": t["status"],
                "priority": t["priority"],
                "created_by": t["created_by"],
                "created_at": t["created_at"],
                "completed_at": t["completed_at"],
                "block_kind": t["block_kind"],
                "consecutive_failures": t["consecutive_failures"],
                "parents": parent_of.get(tid, []),
                "children": children.get(tid, []),
                "latest_run": (
                    {"outcome": run["outcome"], "summary": run["summary"],
                     "ended_at": run["ended_at"], "run_status": run["status"]}
                    if run else None
                ),
            })
        return {"total": len(out), "status_counts": status_counts, "tasks": out}
    finally:
        con.close()


def read_key(cfg):
    path = os.path.expanduser(cfg["key_file"])
    var = cfg["key_var"]
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(var + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{var} not found in {path}")


def is_text_file(meta):
    mime = (meta.get("mime") or "").lower()
    if mime.startswith(TEXT_MIMES) or mime in TEXT_MIMES:
        return True
    name = (meta.get("name") or "").lower()
    return name.endswith(TEXT_EXTS)


class Room:
    """Transcript + round engine. All mutation under self.lock."""

    def __init__(self, cfg, key):
        self.cfg = cfg
        self.key = key
        self.agents = {a["name"]: a for a in cfg["agents"]}
        self.agent_order = [a["name"] for a in cfg["agents"]]
        self.lock = threading.RLock()
        self.messages = []
        self._load_transcript()
        self.round_active = False
        self.pending = []          # agent names queued for the current round
        self.spoken = set()        # agents that already spoke this round
        self.stop_flag = False
        self.next_round = []       # mentions from human posts that arrived mid-round
        self.clients = set()       # SSE client queues
        self.round_thread = None
        # Per-agent in-flight turn state, for the /state initial-state-sync
        # snapshot. Mirrors what the office client derives from turn/turn_delta
        # SSE so a fresh client can seed its scene on load (see _record_turn).
        # agent -> {state, started_at, last_delta_at, tool_label} (epoch ms).
        self.turn_state = {}
        # Sprint 4: auth + files
        self.auth_token = cfg.get("auth_token")  # None/absent = auth disabled
        self.uploads_dir = cfg.get("uploads_dir") or os.path.join(BASE_DIR, "uploads")
        os.makedirs(self.uploads_dir, exist_ok=True)
        self.file_index_path = os.path.join(self.uploads_dir, "index.json")
        self.files = {}            # id -> {id, name, size, mime, path}
        self._load_file_index()
        # Greeting mode: a single-agent round where the addressed agent
        # replies with ONLY a greeting (no tools, no hand-off, no work).
        self.greet_mode = False
        # Kanban-change watcher: the Hermes kanban DB is written externally,
        # so the server doesn't know when it changes. This daemon polls it
        # and broadcasts a `kanban` SSE event on change so the in-office
        # board can live-update (the client re-fetches /kanban for the data).
        self._kanban_stop = threading.Event()
        self._kanban_thread = threading.Thread(target=self._kanban_watch,
                                               daemon=True)
        self._kanban_thread.start()

    # ---------- transcript ----------

    def _load_transcript(self):
        if not os.path.exists(TRANSCRIPT_PATH):
            return
        with open(TRANSCRIPT_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        self.messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    def _persist(self, msg):
        with open(TRANSCRIPT_PATH, "a") as f:
            f.write(json.dumps(msg) + "\n")

    def post(self, author, text, file_refs=None):
        # Build the message INSIDE the lock: `id` derives from
        # len(self.messages), and concurrent posts (parallel turn batches)
        # must not race on it — duplicate ids would break since/page resync.
        with self.lock:
            msg = {
                "id": len(self.messages) + 1,
                "ts": datetime.now(timezone.utc).isoformat(),
                "author": author,
                "text": text,
            }
            if file_refs:
                msg["file_refs"] = file_refs
            self.messages.append(msg)
            self._persist(msg)
        self.broadcast({"type": "message", "message": msg})
        # The agent's turn just posted: clear its in-flight turn state so the
        # /state snapshot no longer shows it as active. No-op for the human
        # (Krit is never tracked in turn_state).
        if author in self.agents:
            self._record_turn(author, None)
        return msg

    def snapshot(self):
        with self.lock:
            return list(self.messages)

    def search(self, q):
        """Return messages whose text contains q (case-insensitive)."""
        q = (q or "").lower().strip()
        if not q:
            return self.snapshot()
        with self.lock:
            return [m for m in self.messages if q in m.get("text", "").lower()]

    def since(self, since_id, q=None):
        """MVP-2: messages with id > since_id (SSE reconnect resync).

        Strictly greater — the client passes the last confirmed id.
        `q` composes the same case-insensitive text filter as search().
        """
        q = (q or "").lower().strip()
        with self.lock:
            msgs = [m for m in self.messages if m.get("id", 0) > since_id]
        if q:
            msgs = [m for m in msgs if q in m.get("text", "").lower()]
        return msgs

    def page(self, before_id=None, q=None):
        """MVP-4: newest-first list of messages with id <= before_id (all if
        None), optionally text-filtered by q. No limit — the caller slices the
        page. A pagination page "ends at" `before_id` (inclusive upper bound),
        so /messages?before=200&limit=50 yields ids 151..200 newest-first."""
        with self.lock:
            msgs = list(self.messages)
        if before_id is not None:
            msgs = [m for m in msgs if m.get("id", 0) <= before_id]
        q = (q or "").lower().strip()
        if q:
            msgs = [m for m in msgs if q in m.get("text", "").lower()]
        msgs.sort(key=lambda m: m.get("id", 0), reverse=True)
        return msgs

    def export_markdown(self):
        with self.lock:
            msgs = list(self.messages)
        out = ["# team-room transcript", ""]
        for m in msgs:
            ts = m.get("ts", "")
            out.append(f"## {m.get('author', '?')} — {ts}")
            out.append("")
            out.append(m.get("text", ""))
            out.append("")
            for fr in m.get("file_refs", []) or []:
                nm = fr.get("name") if isinstance(fr, dict) else fr
                out.append(f"> [file] {nm}")
        return "\n".join(out)

    # ---------- files (Sprint 4) ----------

    def _load_file_index(self):
        if os.path.exists(self.file_index_path):
            try:
                with open(self.file_index_path) as f:
                    self.files = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.files = {}

    def _save_file_index(self):
        tmp = self.file_index_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.files, f)
        os.replace(tmp, self.file_index_path)

    def add_file(self, name, data, mime=""):
        fid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)
        path = os.path.join(self.uploads_dir, fid)
        with open(path, "wb") as f:
            f.write(data)
        meta = {"id": fid, "name": name, "size": len(data), "mime": mime, "path": path}
        with self.lock:
            self.files[fid] = meta
            self._save_file_index()
        return meta

    def get_file(self, fid):
        with self.lock:
            m = self.files.get(fid)
            return dict(m) if m else None

    # ---------- mentions ----------

    def parse_mentions(self, text):
        """Return ordered, de-duplicated agent names mentioned in text."""
        found = []
        for m in MENTION_RE.finditer(text):
            token = m.group(1).lower()
            if token in ALL_TOKENS:
                names = list(self.agent_order)
            elif token in self.agents:
                names = [token]
            else:
                continue
            for n in names:
                if n not in found:
                    found.append(n)
        return found

    # ---------- round engine ----------

    def start_round(self, names, source="human", greet=False):
        with self.lock:
            if self.round_active:
                # merge into the running round where possible
                for n in names:
                    if n not in self.spoken and n not in self.pending:
                        self.pending.append(n)
                return False
            self.round_active = True
            self.greet_mode = greet
            self.spoken = set()
            self.stop_flag = False
            self.pending = [n for n in names if n not in self.spoken]
            self.next_round = []
        self.broadcast({"type": "round", "state": "started", "agents": self.pending})
        self.round_thread = threading.Thread(target=self._run_round, daemon=True)
        self.round_thread.start()
        return True

    def _run_round(self):
        # Greeting rounds run exactly one agent (the pinged one) and never
        # cascade into hand-offs — the agent replies with a greeting only.
        cap = 1 if self.greet_mode else self.cfg.get("max_agents_per_round", 6)
        # Concurrency knob: up to `conc` agent turns run in parallel per
        # dispatch batch (default 3). cap stays the per-round cap — a
        # 6-agent round with conc=3 dispatches two batches of 3.
        try:
            conc = max(1, int(self.cfg.get("max_concurrent_agents", 3)))
        except (TypeError, ValueError):
            conc = 3
        try:
            while True:
                batch = []
                with self.lock:
                    if self.stop_flag or len(self.spoken) >= cap:
                        break
                    while self.pending and len(batch) < conc:
                        agent = self.pending.pop(0)
                        if agent in self.spoken:
                            continue
                        self.spoken.add(agent)
                        batch.append(agent)
                    if not batch:
                        # No pending agents: drain mentions that arrived
                        # mid-round, then retry or end the round.
                        self.pending = self.next_round
                        self.next_round = []
                        if not self.pending:
                            break
                        continue
                    for a in batch:
                        # Thread the active card so the turn event + /state
                        # snapshot name 'what card is agent a on right now'.
                        cid = self.active_card_id(self.agents[a]["profile"])
                        self.broadcast(
                            {"type": "turn", "agent": a, "state": "thinking",
                             "card_id": cid})
                        self._record_turn(a, "thinking", card_id=cid)
                    # Snapshot the transcript at DISPATCH time: every agent
                    # in this batch sees the room as it was when the batch
                    # was dispatched — not mid-round replies from siblings.
                    prompts = {a: self._build_prompt(a) for a in batch}
                if len(batch) == 1:
                    # Single-agent round: stay sequential — mention
                    # cascades resolve one agent at a time, as before.
                    a = batch[0]
                    try:
                        reply = self._call_agent(a, prompts[a])
                    except Exception as e:  # noqa: BLE001
                        reply = f"(turn failed: {e})"
                    self.post(a, reply)
                    self._cascade(reply, cap)
                else:
                    # Multi-agent batch: run the turns in parallel.
                    with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                        futs = {pool.submit(self._call_agent, a, prompts[a]): a
                                for a in batch}
                        for fut in as_completed(futs):
                            a = futs[fut]
                            try:
                                reply = fut.result()
                            except Exception as e:  # noqa: BLE001
                                reply = f"(turn failed: {e})"
                            self.post(a, reply)
                            self._cascade(reply, cap)
        finally:
            now_ms = time.time() * 1000.0
            with self.lock:
                self.round_active = False
                self.pending = []
                self.stop_flag = False
                # Round done/stopped: every agent leaves its active state —
                # idle_since = now (server time) so the scene's idle-duration
                # behaviors ride on server time, not client clocks.
                for name in self.agent_order:
                    self.turn_state[name] = {
                        "state": "idle",
                        "started_at": None,
                        "last_delta_at": None,
                        "tool_label": None,
                        "card_id": None,
                        "idle_since": now_ms,
                        "done_at": None,
                    }
            self.broadcast({"type": "round", "state": "stopped" if self.stop_flag else "done"})
            self.broadcast_office()

    def _cascade(self, reply, cap):
        """Hand-offs: mentions inside a reply join the round's pending queue
        (suppressed in greeting mode — a greeting must not cascade a round).
        Cascades dispatch only after the current batch completes, so they
        stay sequential."""
        if self.greet_mode:
            return
        for n in self.parse_mentions(reply):
            with self.lock:
                if (not self.stop_flag and n not in self.spoken
                        and n not in self.pending
                        and len(self.spoken) < cap):
                    self.pending.append(n)

    def _call_agent(self, name, prompt=None):
        """Call an agent with streaming; relay deltas as turn_delta SSE events.

        Returns the full accumulated text (the round engine posts it at the end).
        Falls back to a single JSON response if the API does not stream.
        `prompt` is the dispatch-time snapshot built by the round engine;
        when None it is built here (single-agent / legacy path).
        """
        a = self.agents[name]
        profile = a["profile"]
        # Thread the active card so every turn_delta names 'what card is
        # agent name on right now' (drill-in panel + celebration beat).
        card_id = self.active_card_id(profile)
        base = self.cfg["api_base"].rstrip("/")
        url = f"{base}/p/{profile}/v1/chat/completions"
        if prompt is None:
            prompt = self._build_prompt(name)
        body = json.dumps({
            "model": profile,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
        )
        timeout = self.cfg.get("turn_timeout_s", 300)
        parts = []
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in ctype:
                # Non-streaming fallback: whole JSON blob.
                data = json.load(r)
                text = (data.get("choices", [{}])[0].get("message", {})
                        .get("content") or "").strip()
                return text or "(no reply)"
            last_event = None
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    last_event = line[len("event:"):].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                # Named tool-progress events (event: hermes.tool.progress).
                if last_event and "tool" in last_event.lower():
                    msg = obj.get("message") or obj.get("data") or obj.get("text") or ""
                    if isinstance(msg, (dict, list)):
                        msg = json.dumps(msg)
                    if msg:
                        self.broadcast(
                            {"type": "turn_delta", "agent": name,
                             "kind": "tool", "delta": str(msg),
                             "card_id": card_id})
                        self._record_turn(name, "working",
                                          tool_label=str(msg)[:42],
                                          card_id=card_id)
                    last_event = None
                    continue
                last_event = None
                # Content delta + reasoning delta (Sprint 4 contract:
                # reasoning is surfaced as a tagged delta for the thinking
                # pill only — it is NOT part of the posted message text).
                content = ""
                reasoning = ""
                try:
                    delta = obj["choices"][0].get("delta", {})
                    content = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                except (KeyError, IndexError, TypeError):
                    pass
                if reasoning:
                    self.broadcast(
                        {"type": "turn_delta", "agent": name,
                         "kind": "reasoning", "delta": reasoning,
                         "card_id": card_id})
                    self._record_turn(name, "thinking", card_id=card_id)
                if content:
                    parts.append(content)
                    self.broadcast(
                        {"type": "turn_delta", "agent": name,
                         "kind": "text", "delta": content,
                         "card_id": card_id})
                    self._record_turn(name, "typing", card_id=card_id)
        text = "".join(parts).strip()
        return text or "(no reply)"

    def _build_prompt(self, name):
        window = self.cfg.get("history_window", 60)
        others = [n for n in self.agent_order if n != name]
        with self.lock:
            recent = self.messages[-window:]
            files_snapshot = dict(self.files)
        lines = []
        for m in recent:
            who = m["author"]
            txt = m["text"][:2000]
            lines.append(f"[{who}] {txt}")
            # Sprint 4: inject referenced files (path + inline content for text).
            for fr in m.get("file_refs", []) or []:
                fid = fr if isinstance(fr, str) else (fr or {}).get("id")
                meta = files_snapshot.get(fid)
                if not meta:
                    continue
                note = f"  [file] {meta['name']} ({meta['size']} bytes) at {meta['path']}"
                if is_text_file(meta) and meta["size"] <= MAX_INLINE_BYTES:
                    try:
                        with open(meta["path"], "r", encoding="utf-8",
                                  errors="replace") as f:
                            content = f.read(MAX_INLINE_BYTES)
                        note += "\n  [content]\n" + content
                    except OSError:
                        pass
                lines.append(note)
        transcript = "\n".join(lines) if lines else "(empty room)"
        prompt = (
            f'You are {name} in "team-room" — a shared channel with the human ({HUMAN}) '
            f"and the other agents: {', '.join(others)}.\n\n"
            f"Room transcript so far (oldest first):\n{transcript}\n\n"
            f"You were just addressed. Reply once as {name}, in your own role and voice.\n"
            "Rules:\n"
            "- Keep the reply under 300 words.\n"
            "- Hand off by @mentioning another agent (e.g. @pixel-art-designer); @all addresses everyone. "
            "Agents only respond when mentioned, and each speaks at most once per round.\n"
            "- Do not repeat what is already in the transcript.\n"
            "- Files attached to a message are listed with a [file] line and a local path; "
            "text files also include their [content]. Read them with your tools if needed.\n"
            "- Push back when a decision in the room is wrong or risky: one concrete reason + one better alternative. "
            "If overruled, log the risk and move on — the human's call is final. Never sycophantic.\n"
            "- Think quietly, speak less: no process narration, no hedges — conclusion plus the facts that back it, precise over verbose.\n"
            "- Use your tools if the task requires it."
        )
        if self.greet_mode:
            prompt += (
                "\nGREETING MODE: you were pinged for a greeting only. "
                "Reply with ONLY a short greeting (1-2 sentences) in your role "
                "voice. Do NOT do any other work, do NOT use tools, and do NOT "
                "hand off or @mention anyone.\n"
            )
        return prompt

    def stop_round(self):
        with self.lock:
            if not self.round_active:
                return False
            self.stop_flag = True
        self.broadcast({"type": "round", "state": "stopping"})
        return True

    def active_card_id(self, profile):
        """Card id the given profile is working right now (status=running),
        or None. Read-only kanban lookup: the drill-in panel and the
        turn/turn_delta events need 'what card is agent X on right now'.
        Returns the most recently started running card for that assignee."""
        db_path = self.cfg.get("kanban_db", KANBAN_DB_DEFAULT)
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
            try:
                row = con.execute(
                    "SELECT id FROM tasks WHERE assignee = ? "
                    "AND status = 'running' "
                    "ORDER BY started_at DESC, created_at DESC LIMIT 1",
                    (profile,)).fetchone()
                return row[0] if row else None
            finally:
                con.close()
        except Exception:
            return None

    def _record_turn(self, agent, state, tool_label=None, card_id=None):
        """Track per-agent in-flight turn state for the /state snapshot.

        Mirrors what the office client derives from turn/turn_delta SSE, so a
        fresh client can seed its scene on load and reflect live state
        mid-round instead of showing 'idle' until the next turn_delta.
        `state=None` means the turn just posted: the entry is NOT dropped —
        it becomes state="done" + done_at, and /office-state reports "done"
        for OFFICE_DONE_MS, then "idle" (idle_since = the post time).
        Timestamps are epoch ms (the client's Date.now()); `tool_label` and
        `card_id` persist across reasoning/text deltas until replaced.
        On every real state transition (thinking/working/typing/done) the
        full /office-state snapshot is broadcast as an `office` SSE event —
        the 3D scene's push channel (no ticker, no poller).
        """
        now = time.time() * 1000.0
        with self.lock:
            cur = self.turn_state.get(agent)
            if state is None:
                if cur is None:
                    return
                self.turn_state[agent] = {
                    "state": "done",
                    "started_at": cur.get("started_at"),
                    "last_delta_at": cur.get("last_delta_at"),
                    "tool_label": cur.get("tool_label"),
                    "card_id": cur.get("card_id"),
                    "idle_since": None,
                    "done_at": now,
                }
                changed = True
            else:
                # A fresh turn never inherits the previous one's clock or
                # labels: after "done"/"idle" (or no entry) it starts clean.
                if cur is None or cur.get("state") in ("done", "idle"):
                    started = now
                    label = tool_label
                    cid = card_id
                else:
                    started = cur["started_at"]
                    label = tool_label if tool_label is not None else cur["tool_label"]
                    cid = card_id if card_id is not None else cur["card_id"]
                self.turn_state[agent] = {
                    "state": state,
                    "started_at": started,
                    "last_delta_at": now,
                    "tool_label": label,
                    "card_id": cid,
                    "idle_since": None,
                    "done_at": None,
                }
                changed = cur is None or cur.get("state") != state
        if changed:
            self.broadcast_office()

    def office_snapshot(self):
        """Full /office-state payload — the single source of truth for the
        3D office feed. Served by GET /office-state AND broadcast as the
        `office` SSE event, so the two surfaces can never drift.

        Per-agent row (seat order): id, seat, color, state, tool_label,
        card_id, card_title, card_state, elapsed_ms, last_delta_ms,
        idle_since. State enum: thinking/working/typing/stuck/done/idle.
        `idle_since` (epoch ms, server time) is when the agent last LEFT an
        active state — null while active, set at the turn post (done window
        expiry) and at round done/stopped. Idle-duration behaviors in the
        scene must ride on this, not client clocks.
        """
        now = time.time() * 1000.0
        with self.lock:
            turns = {a: dict(t) for a, t in self.turn_state.items()}
            round_active = self.round_active
        db_path = self.cfg.get("kanban_db", KANBAN_DB_DEFAULT)
        try:
            board = read_kanban_board(db_path)
        except sqlite3.Error:
            board = {"status_counts": {}, "tasks": []}
        task_by_id = {t["id"]: t for t in board.get("tasks", [])}
        agents_out = []
        for seat, name in enumerate(self.agent_order):
            meta = self.agents.get(name, {})
            t = turns.get(name)
            if t is None:
                state, tool_label, card_id, last = "idle", None, None, None
                elapsed, idle_since = 0, None
            else:
                tool_label = t.get("tool_label")
                card_id = t.get("card_id")
                last = t.get("last_delta_at")
                raw = t.get("state") or "working"
                if raw == "done":
                    # 600 ms done-flash window after the turn posts.
                    done_at = t.get("done_at")
                    if done_at is not None and (now - done_at) <= OFFICE_DONE_MS:
                        state, idle_since = "done", None
                    else:
                        state, idle_since = "idle", done_at
                else:
                    # Active states (thinking/working/typing) + round-end
                    # idle. Two gates before "stuck" (t_f6904af5):
                    #   (a) open turn — only a card-backed turn can be
                    #       "stuck working". A turn with no card + no delta
                    #       past the threshold is stale/abandoned, so it is
                    #       reaped to idle (idle_since = last activity), not
                    #       flagged stuck.
                    #   (b) no tool in flight — a long tool call
                    #       (build/install/network) with no intermediate
                    #       delta is healthy "working <tool>", not stuck;
                    #       only flag stuck when tool_label is None.
                    stale = last is not None and (now - last) > OFFICE_STUCK_MS
                    if raw == "idle":
                        # Round-end / posted idle: carry the server-side
                        # idle_since (set at round done/stopped).
                        state, idle_since = raw, t.get("idle_since")
                    elif card_id is None:
                        state = "idle" if stale else raw
                        idle_since = last if stale else None
                    else:
                        state = "stuck" if (stale and tool_label is None) else raw
                        idle_since = None
                started = t.get("started_at")
                elapsed = int(now - started) if started else 0
            task = task_by_id.get(card_id) if card_id else None
            agents_out.append({
                "id": name,
                "seat": seat,
                "color": meta.get("color"),
                "state": state,
                "tool_label": tool_label,
                "card_id": card_id,
                "card_title": task.get("title") if task else None,
                "card_state": task.get("status") if task else None,
                "elapsed_ms": elapsed,
                "last_delta_ms": last,
                "idle_since": idle_since,
            })
        return {
            "round_active": round_active,
            "now": int(now),
            "stuck_ms": OFFICE_STUCK_MS,
            "agents": agents_out,
            "board": board.get("status_counts", {}),
        }

    def broadcast_office(self):
        """Push the full /office-state snapshot as an `office` SSE event.
        Fired on every per-agent state transition (thinking/working/
        typing/done) and on round done/stopped (all agents -> idle).
        Defensive: a feed failure must never break the turn lifecycle."""
        try:
            self.broadcast({"type": "office", **self.office_snapshot()})
        except Exception:  # noqa: BLE001
            pass

    # ---------- SSE ----------

    def broadcast(self, event):
        payload = f"data: {json.dumps(event)}\n\n"
        dead = []
        for q in list(self.clients):
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            self.clients.discard(q)

    def add_client(self):
        q = queue.Queue()
        self.clients.add(q)
        return q

    def drop_client(self, q):
        self.clients.discard(q)

    # ---------- kanban-change watcher (live in-office board) ----------

    def _kanban_watch(self):
        """Poll the (externally-written) Hermes kanban DB; broadcast a
        `kanban` SSE event when the board changes so the in-office board
        live-updates. The event is a change signal only — the client
        re-fetches /kanban for the full board (keeps the read logic in one
        place). It also names the agent(s) whose card changed (drill-in
        panel) via `agents` + `changed_cards`. First pass just records the
        baseline (no spurious event)."""
        db_path = self.cfg.get("kanban_db", KANBAN_DB_DEFAULT)
        last = None
        last_cards = {}  # card_id -> status, for per-card change detection
        while not self._kanban_stop.is_set():
            try:
                board = read_kanban_board(db_path)
                cards = {t["id"]: t["status"] for t in board["tasks"]}
                sig = (
                    board["total"],
                    tuple(sorted(board["status_counts"].items())),
                    max((t["created_at"] for t in board["tasks"]), default=""),
                    max((t["completed_at"] for t in board["tasks"]
                         if t["completed_at"]), default=""),
                )
                if sig != last:
                    if last is not None:
                        # Name the agent(s) whose card changed so the
                        # drill-in panel can update the right desk.
                        changed = [tid for tid, st in cards.items()
                                   if last_cards.get(tid) != st]
                        changed_set = set(changed)
                        agents = sorted({t["assignee"] for t in board["tasks"]
                                         if t["id"] in changed_set
                                         and t["assignee"]})
                        self.broadcast(
                            {"type": "kanban", "changed": True,
                             "total": board["total"],
                             "changed_cards": changed,
                             "agents": agents})
                    last = sig
                last_cards = cards
            except Exception:
                pass
            self._kanban_stop.wait(3)


def make_handler(room):
    class Handler(BaseHTTPRequestHandler):
        server_version = "team-room/1.0"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        # ---- Sprint 4: auth (MVP-3: gates all data routes, reads included) ----
        def _authed(self):
            if not room.auth_token:
                return True
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            # Constant-time compare (no timing side-channel on the token).
            return secrets.compare_digest(auth[len("Bearer "):], room.auth_token)

        def _query_token_ok(self, query):
            # EventSource cannot set request headers: accept ?token= as the
            # auth path for the SSE stream (constant-time compare).
            if not room.auth_token:
                return True
            tok = (query.get("token") or [""])[0]
            return bool(tok) and secrets.compare_digest(tok, room.auth_token)

        def _require_auth(self):
            if self._authed():
                return True
            self._json({"error": "unauthorized"}, 401)
            return False

        def _split(self):
            parts = urllib.parse.urlsplit(self.path)
            return parts.path, urllib.parse.parse_qs(parts.query)

        def _serve_static(self, prefix, base_dir, path):
            """Serve a file under base_dir for paths starting with `prefix`.
            OPEN (no auth — static content, same as the page shells). The
            subpath is URL-decoded, resolved with realpath, and must stay
            inside base_dir (same traversal defense as /files/)."""
            sub = urllib.parse.unquote(path[len(prefix):].lstrip("/"))
            real = os.path.realpath(os.path.join(base_dir, sub))
            base = os.path.realpath(base_dir)
            if not real.startswith(base + os.sep) or not os.path.isfile(real):
                self._json({"error": "not found"}, 404)
                return
            with open(real, "rb") as f:
                data = f.read()
            ext = os.path.splitext(real)[1].lower()
            mime = STATIC_MIME.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, query = self._split()
            if path == "/":
                with open(WEBUI_CHAT_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/chat":
                with open(WEBUI_CHAT_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/health":
                self._json({"status": "ok", "round_active": room.round_active,
                            "auth_enabled": bool(room.auth_token)})
            elif path == "/state":
                # Initial-state-sync: a fresh office client fetches this on
                # load to seed the scene with the live round/turn state
                # (per-agent in-flight turns) instead of showing 'idle' until
                # the next turn_delta SSE. Authed like /kanban + /messages.
                if not self._require_auth():
                    return
                with room.lock:
                    snap = {
                        "round_active": room.round_active,
                        "greet_mode": room.greet_mode,
                        "pending": list(room.pending),
                        "spoken": sorted(room.spoken),
                        "turns": {a: dict(t)
                                  for a, t in room.turn_state.items()},
                    }
                self._json(snap)
            elif path == "/office-state":
                # 3D office feed: one row per configured agent in seat order,
                # with a normalized state enum the scene binds animation to
                # (idle/thinking/working/stuck/done) + server-time idle_since.
                # Reuses the read-only board for card titles + states. Authed
                # like /state. Single source of truth: office_snapshot() —
                # the same payload the `office` SSE event carries.
                if not self._require_auth():
                    return
                self._json(room.office_snapshot())
            elif path == "/messages":
                if not self._require_auth():
                    return
                q = (query.get("q") or [""])[0]
                since_raw = (query.get("since") or [""])[0].strip()
                since_id = None
                if since_raw:
                    try:
                        since_id = int(since_raw)
                    except ValueError:
                        self._json({"error": "since must be an integer"}, 400)
                        return
                before_raw = (query.get("before") or [""])[0].strip()
                before_id = None
                if before_raw:
                    try:
                        before_id = int(before_raw)
                    except ValueError:
                        self._json({"error": "before must be an integer"}, 400)
                        return
                limit_raw = (query.get("limit") or [""])[0].strip()
                limit = 100
                if limit_raw:
                    try:
                        limit = int(limit_raw)
                    except ValueError:
                        self._json({"error": "limit must be an integer"}, 400)
                        return
                    if limit < 1:
                        self._json({"error": "limit must be >= 1"}, 400)
                        return
                with room.lock:
                    state = {
                        "round_active": room.round_active,
                        "pending": list(room.pending),
                        "spoken": sorted(room.spoken),
                    }
                if before_id is not None or limit_raw != "":
                    # MVP-4: transcript pagination — newest-first page ending at
                    # `before` (inclusive). No before/limit => full snapshot below
                    # (backward compat for /export and existing clients).
                    full = room.page(before_id, q)
                    messages = full[:limit]
                    resp = {"messages": messages, "state": state,
                            "q": q or None, "count": len(messages),
                            "order": "newest_first", "limit": limit,
                            "has_more": len(full) > limit}
                    if before_id is not None:
                        resp["before"] = before_id
                    self._json(resp)
                    return
                if since_id is not None:
                    messages = room.since(since_id, q)
                else:
                    messages = room.search(q) if q else room.snapshot()
                resp = {"messages": messages, "state": state,
                        "q": q or None, "count": len(messages)}
                if since_id is not None:
                    resp["since"] = since_id
                self._json(resp)
            elif path == "/export":
                if not self._require_auth():
                    return
                fmt = (query.get("format") or ["json"])[0].lower()
                if fmt == "md":
                    self._send(200, room.export_markdown(),
                               "text/markdown; charset=utf-8")
                else:
                    with room.lock:
                        state = {
                            "round_active": room.round_active,
                            "pending": list(room.pending),
                            "spoken": sorted(room.spoken),
                        }
                    self._json({"messages": room.snapshot(), "state": state})
            elif path == "/events":
                # EventSource cannot set headers: auth via ?token= only.
                if not self._query_token_ok(query):
                    self._json({"error": "unauthorized"}, 401)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                q = room.add_client()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            payload = q.get(timeout=15)
                        except queue.Empty:
                            payload = ": keepalive\n\n"
                        try:
                            self.wfile.write(payload.encode())
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                finally:
                    room.drop_client(q)
            elif path == "/board":
                with open(BOARD_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/office":
                with open(OFFICE_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/stack":
                with open(STACK_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/office3d":
                # 3D office host page (open page shell, like /office): reads
                # tr_token from localStorage and iframes /unity/index.html.
                # The Unity tooling card creates office3d.html — until it
                # lands, return a clean 404 (not a crash).
                if not os.path.exists(OFFICE3D_HTML_PATH):
                    self._json({"error": "office3d.html not built yet"}, 404)
                    return
                with open(OFFICE3D_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/room":
                # The video-game combined page (office left / chat right).
                # Same file as /chat — /room is its canonical URL.
                with open(WEBUI_CHAT_HTML_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/kanban":
                if not self._require_auth():
                    return
                db_path = room.cfg.get("kanban_db", KANBAN_DB_DEFAULT)
                try:
                    board = read_kanban_board(db_path)
                except sqlite3.Error as e:
                    self._json({"error": "kanban read failed", "detail": str(e)}, 500)
                    return
                board["agents"] = {n: a["color"] for n, a in room.agents.items()}
                board["source"] = db_path
                self._json(board)
            elif path.startswith("/assets/office3d/"):
                # 3D office scene assets (OPEN static content).
                self._serve_static("/assets/office3d/", OFFICE3D_ASSETS_DIR, path)
            elif path.startswith("/unity/"):
                # Unity WebGL build output (OPEN static content).
                self._serve_static("/unity/", UNITY_BUILD_DIR, path)
            elif path.startswith("/files/"):
                # MVP-5: serve an uploaded file. Authed (same token gate as the
                # POST routes). The id is used ONLY as a key into the file index
                # (never as a filesystem path) -> no path traversal. The stored
                # path is resolved and must stay inside uploads_dir (defense in
                # depth against a tampered index.json).
                if not self._require_auth():
                    return
                fid = path[len("/files/"):]
                meta = room.get_file(fid)
                if not meta:
                    self._json({"error": "not found"}, 404)
                    return
                real = os.path.realpath(meta.get("path", ""))
                updir = os.path.realpath(room.uploads_dir)
                if not real.startswith(updir + os.sep) or not os.path.isfile(real):
                    self._json({"error": "not found"}, 404)
                    return
                with open(real, "rb") as f:
                    data = f.read()
                mime = (meta.get("mime") or "application/octet-stream")
                fname = (meta.get("name") or fid).replace('"', "")
                disp = "inline" if mime.startswith("image/") else "attachment"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition",
                                 f"{disp}; filename=\"{fname}\"")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path, _ = self._split()
            if path == "/message":
                if not self._require_auth():
                    return
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    text = (body.get("text") or "").strip()
                    greet = bool(body.get("greet"))
                except json.JSONDecodeError:
                    self._json({"error": "bad json"}, 400)
                    return
                if not text:
                    self._json({"error": "empty message"}, 400)
                    return
                # Sprint 4: resolve file refs to {id, name}.
                file_refs = None
                raw_files = body.get("files")
                if raw_files:
                    file_refs = []
                    for fid in raw_files:
                        meta = room.get_file(fid)
                        if meta:
                            file_refs.append({"id": meta["id"], "name": meta["name"],
                                              "mime": meta.get("mime", "")})
                room.post(HUMAN, text, file_refs=file_refs)
                mentions = room.parse_mentions(text)
                started = False
                if mentions:
                    with room.lock:
                        if room.round_active:
                            for n in mentions:
                                if n not in room.spoken and n not in room.pending:
                                    room.pending.append(n)
                            started = True
                        else:
                            started = True
                    if started and not room.round_active:
                        room.start_round(mentions, greet=greet)
                self._json({"ok": True, "round_started": started,
                            "mentions": mentions, "file_refs": file_refs})
            elif path == "/stop":
                if not self._require_auth():
                    return
                self._json({"ok": True, "stopping": room.stop_round()})
            elif path == "/upload":
                if not self._require_auth():
                    return
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._json({"error": "bad json"}, 400)
                    return
                name = (body.get("name") or "upload.bin").strip() or "upload.bin"
                # Sanitize the stored name (keep the basename, no path traversal).
                name = os.path.basename(name)
                data_b64 = body.get("data_base64") or ""
                mime = (body.get("mime") or "").strip()
                if not data_b64:
                    self._json({"error": "missing data_base64"}, 400)
                    return
                try:
                    data = base64.b64decode(data_b64, validate=False)
                except Exception:  # noqa: BLE001
                    self._json({"error": "bad base64"}, 400)
                    return
                meta = room.add_file(name, data, mime)
                self._json({"ok": True, "file": meta})
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def cmd_serve():
    cfg = load_config()
    key = read_key(cfg)
    room = Room(cfg, key)
    httpd = ThreadingHTTPServer(("127.0.0.1", cfg.get("port", 8650)), make_handler(room))
    httpd.daemon_threads = True
    auth = "auth ON" if room.auth_token else "auth OFF"
    print(f"team-room listening on http://127.0.0.1:{cfg.get('port', 8650)} "
          f"({len(room.agents)} agents, {len(room.messages)} prior messages, {auth})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def _post(text, files=None):
    cfg = load_config()
    url = f"http://127.0.0.1:{cfg.get('port', 8650)}/message"
    payload = {"text": text}
    if files:
        payload["files"] = files
    headers = {"Content-Type": "application/json"}
    if cfg.get("auth_token"):
        headers["Authorization"] = f"Bearer {cfg['auth_token']}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.read().decode())


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "serve":
        cmd_serve()
        return
    cmd = sys.argv[1]
    if cmd == "say":
        if len(sys.argv) < 3:
            raise SystemExit('usage: room_server.py say "text"')
        _post(" ".join(sys.argv[2:]))
    elif cmd == "agents":
        for a in load_config()["agents"]:
            print(f"{a['name']}\t{a['profile']}\t{a.get('color', '')}")
    elif cmd == "tail":
        if os.path.exists(TRANSCRIPT_PATH):
            with open(TRANSCRIPT_PATH) as f:
                for line in f:
                    print(line.rstrip())
        else:
            print("(transcript empty)")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
