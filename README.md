# virtual-office-agent (team-room)

Local multi-agent chat service that multiplexes Hermes profiles into one
@mention-gated chat channel, with a live web UI (chat + kanban board + office view).

Loopback-only by design. Remote access via `tailscale serve` (tailnet-only, auto-TLS).

## Quickstart
1. `cp config.example.json config.json`
2. Edit `config.json`:
   - `port` — loopback port the server binds (e.g. `8651`)
   - `kanban_db` — absolute path to your team-room board's `kanban.db`
   - `api_base` / `key_file` / `key_var` — the Hermes API server it calls
   - `auth_token` — set a strong random token (gates the HTTP API)
3. `python3 room_server.py serve`
4. Open `http://127.0.0.1:<port>/` (chat), `/board`, `/office`, `/room`.

## Layout
- `room_server.py` — backend: round engine, HTTP+SSE API, serves the UI
- `config.json` — runtime config (gitignored; see `config.example.json`)
- `webui/` — chat UI (`index.html`) + kanban view
- `board.html` / `room.html` / `office.html` / `tech-stack.html` — UI shells
- `tests/` — pytest suite
- `verify_*.py` / `qa_*.py` — live-verification & QA scripts (drive a headless browser)
- `restart_s4_safely.py` — round-boundary restart helper

## Notes
- Mention-gated: agents answer ONLY @mentions; a plain message triggers no round.
- The Hermes API (`api_base`) stays on loopback — never expose it.
