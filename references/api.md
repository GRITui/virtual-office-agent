# team-room HTTP API

`room_server.py` — stdlib-only, loopback-only round coordinator + web UI server.
It is SEPARATE from the Hermes API server (8642) that actually runs the agents.

## Port

**Config-driven — never assume.** The server binds
`127.0.0.1:<cfg.get("port", 8650)>`; the live instance currently runs on
**8651** (see `config.json` in the repo root). Read `config.json` (or probe
`/health`) before wiring a client. `config.json` lives in the script's own
directory (`BASE_DIR`), so each directory of server files is one instance.

## Auth

- `auth_token` is read ONCE at startup (`Room.__init__`). A falsy/absent token
  means auth is OFF (every gate returns true). Flipping it on requires a
  config change + server restart.
- **Bearer header** for all data routes: `Authorization: Bearer <token>`
  (constant-time compare). Missing/wrong token → `401 {"error":"unauthorized"}`.
- **SSE exception:** `EventSource` cannot set request headers, so `/events`
  authenticates via `?token=<token>` (same constant-time compare).
- OPEN (no auth): `/health`, the page shells (`/`, `/chat`, `/room`, `/board`,
  `/office`, `/stack`), and the static content routes
  (`/assets/office3d/*`, `/unity/*`).

## Route table

### GET

| Route | Auth | Returns |
|---|---|---|
| `/` | open | `webui/index.html` (combined UI: office left / chat right) |
| `/chat` | open | same file as `/` |
| `/room` | open | same file — canonical URL |
| `/board` | open | `board.html` (kanban wall) |
| `/office` | open | `office.html` (2D office scene) |
| `/stack` | open | `tech-stack.html` |
| `/health` | open | `{status, round_active, auth_enabled}` |
| `/state` | Bearer | initial-state-sync snapshot (see below) |
| `/office-state` | Bearer | 3D office feed (see below) |
| `/messages` | Bearer | transcript, optional `?q=` text filter |
| `/messages?since=<id>` | Bearer | messages with id > since (SSE reconnect resync) |
| `/messages?before=<id>&limit=<n>` | Bearer | newest-first page ending at `before` (inclusive): `{messages, state, q, count, order:"newest_first", limit, has_more, before?}` |
| `/export` | Bearer | `?format=md` → markdown transcript; default → `{messages, state}` |
| `/events` | `?token=` | SSE stream (event set below) |
| `/kanban` | Bearer | read-only board: `{total, status_counts, tasks[], agents{color map}, source}` |
| `/files/<id>` | Bearer | uploaded file bytes (`Content-Disposition` inline for images) |
| `/assets/office3d/*` | open | static file from `assets/office3d/` (3D scene assets) |
| `/unity/*` | open | static file from `build/office3d-webgl/` (Unity WebGL build) |

### POST (all Bearer)

| Route | Body | Returns |
|---|---|---|
| `/message` | `{text, greet?, files?}` | `{ok, round_started, mentions, file_refs}` |
| `/stop` | — | `{ok, stopping}` |
| `/upload` | `{name, data_base64, mime}` | `{ok, file{id, name, size, mime, path}}` |

`/message` with @mentions starts a round (or merges into the running one).
`greet: true` runs a single-agent greeting round (no tools, no hand-off).

### `/state` shape

```json
{
  "round_active": false,
  "greet_mode": false,
  "pending": [],
  "spoken": [],
  "turns": {
    "<agent>": {"state": "working", "started_at": 1791883600000,
                "last_delta_at": 1791883601000, "tool_label": "terminal",
                "card_id": "t_abc123"}
  }
}
```

### `/office-state` shape (3D office feed)

One row per configured agent, in seat order. Single source of truth:
`Room.office_snapshot()` — the SAME payload the `office` SSE event carries, so
the poll and push surfaces can never drift.

```json
{
  "round_active": true,
  "now": 1791883601234,
  "stuck_ms": 8000,
  "agents": [
    {"id": "product-owner", "seat": 0, "color": "#e8b93d",
     "state": "working", "tool_label": "terminal",
     "card_id": "t_abc123", "card_title": "…", "card_state": "running",
     "elapsed_ms": 42000, "last_delta_ms": 1791883601000,
     "idle_since": null}
  ],
  "board": {"ready": 2, "running": 1, "done": 5}
}
```

- **state enum:** `thinking | working | typing | stuck | done | idle`.
  `stuck` is server-derived: a turn open with no delta for `stuck_ms`
  (8000 ms, matches the 2D client's STUCK_MS).
- **`done`**: 600 ms window after the turn posts (matches office.html's
  done-flash). Inside the window the row reports `state:"done"`,
  `idle_since:null`; after it, `state:"idle"` with `idle_since` = the post
  time.
- **`idle_since`** (epoch ms, **server time**): when the agent last LEFT an
  active state — `null` while active; set at the turn post (done-window
  expiry) and at round done/stopped (all agents → idle). Idle-duration
  behaviors in the scene must ride on this, not client clocks. `null` also
  means "never active since server start" (no reference point).
- **`card_id` / `card_title` / `card_state`**: the kanban card the agent is
  working (status `running`, read-only lookup at dispatch) + its title and
  current status from the same read-only board read.
- **`board`**: board `status_counts` (same read as `/kanban`).
- Timestamps are epoch ms. `elapsed_ms` = now − turn start (0 when idle).

### Static routes (`/assets/office3d/*`, `/unity/*`)

Open static content (no auth — same as the page shells). The subpath is
URL-decoded, resolved with `realpath`, and must stay inside the target dir
(same traversal defense as `/files/`); anything else → 404.

| Ext | Content-Type |
|---|---|
| `.glb` | `model/gltf-binary` |
| `.json` | `application/json` |
| `.png` | `image/png` |
| `.html` | `text/html` |
| `.js` | `application/javascript` |
| `.wasm` | `application/wasm` |
| `.data` | `application/octet-stream` |
| other | `application/octet-stream` |

`assets/office3d/` exists in git via `.gitkeep`; `build/office3d-webgl/`
appears when the Unity WebGL build lands.

## `/events` SSE — full event set

All events are `data: {json}\n\n` lines. A client that only handles
`message`/`round`/`turn` misses the streaming deltas, the board updates, and
the 3D office feed.

| type | When | Shape |
|---|---|---|
| `round` | round lifecycle | `{type, state: "started"\|"done"\|"stopped"\|"stopping", agents? (list on started)}` |
| `turn` | agent turn begins (one-shot per agent) | `{type, agent, state: "thinking", card_id}` |
| `turn_delta` | each streamed delta | `{type, agent, kind: "tool"\|"text"\|"reasoning", delta, card_id}` — `reasoning` is the thinking pill only, never message text |
| `message` | a message posts (agent or human) | `{type, message: {id, ts, author, text, file_refs?}}` |
| `kanban` | board changed (3 s watcher, change-signal only) | `{type, changed: true, total, changed_cards: [id], agents: [name]}` — client re-fetches `/kanban` for the data |
| `office` | every per-agent state transition (thinking/working/typing/done) and round done/stopped (all agents → idle) | `{type, …full /office-state snapshot}` — same shape as `GET /office-state` |

No ticker, no poller behind `office`: it fires from the turn-state lifecycle
itself. Keepalive: a `: keepalive` comment line every 15 s of silence.

## Notes

- UI files are read fresh per request (HTML edits go live without restart);
  a NEW route only exists after a server restart (the route table is in code).
- Restarting mid-round kills in-flight agent turns — only restart when
  `/health` says `round_active: false` (see skill `team-room-office`,
  `references/web-server.md`, for the armed-waiter pattern).
- The kanban board is read-only (`?mode=ro`); there are no write routes.
