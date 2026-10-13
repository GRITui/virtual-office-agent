# 3D office composition — UX/UI direction (t_9af2fada)

Owner: ux-ui. Scope: 3D layout + camera framing, HUD (Unity UI Canvas), day/night lighting palette.
SOT: office.html (DESK/TINT_KEYS/drawBoard/drill panel), config.json (agents + colors), PO correction 2026-10-13 (office = Unity; HUD = Unity UI Canvas).
Coordinate SOT (set by lead, do not re-litigate): right-handed Y-up; 192x168 px @ 0.05 m/px; room 9.6 m (x) x 8.4 m (z); back wall z=0 (2D top); camera on +Z looking -Z; X3 = x_px*0.05, Z3 = y_px*0.05.

## 1. 3D layout
- Room shell: x 0..9.6, z 0..8.4, floor y=0. Back wall z=0, side walls x=0/9.6, front open (camera side).
- Desk grid (SOT office.html DESK): 2 rows x 3 cols, cell 64x60 px -> 3.2 x 3.0 m. Cell origin i: x=(i%3)*3.2, z=2.35+floor(i/3)*3.0. Desk top height 0.95 m. Seat order = config.json agents array (0 product-office..5 qa-journey).
- Desk props per cell (px offsets from cell origin): lamp (10,24) -> named transform Lamp_<profile>; mug (16,28); laptop on desktop.
- Avatar anchor per profile: cell origin + (32,14) px -> (x3, 0, z3); root at floor, facing +Z.
- Wall kanban board (pixel env card builds geometry; placement here): back wall, centered on desks. 2D board spans x 36..157 px -> x 1.8..7.85 m (width 6.05 m); mounted y 1.9..3.3 m (above avatar heads ~1.8 m), z 0.06 (off the wall). 5 columns [todo, ready, running, blocked, done], headers colored per COLHEAD (todo #e8b93d, ready #8b93a3, running #5db8e0, blocked #e05d5d, done #7ec96f), labels TODO/RDY/RUN/BLK/DONE, up to 3 card bars per column (rgb(48,55,68)).
- Name labels: UI Canvas (section 2), NOT 3D billboards — projected from anchor at y 1.85 m.

## 2. Camera framing
- Orbit target "RoomCenter": (4.8, 1.0, 4.2) (room center, desk height).
- Default cinematic pose: camera (4.8, 7.2, 13.8), look-at (4.8, 0.9, 4.2) — elevated 3/4 view, whole room + wall board in frame. FOV 40 deg.
- Orbit clamps: azimuth +/-50 deg (wall board stays in frame), elevation 15..65 deg, distance 7..16 m. No pan (composition is fixed).
- Input: left-drag orbit 0.35 deg/px; wheel/pinch zoom x1.08 per step (clamped); double-click/double-tap resets to cinematic pose; inertia damping 0.90/frame. Mobile: 1-finger orbit, 2-finger pinch.
- Legibility bar: at the default pose on a 1280-px-wide render, name labels >= 12 px and wall-board column labels readable.

## 3. HUD (Unity UI Canvas — PO correction: not DOM-on-WebGL)
Canvas: Screen Space - Overlay; Canvas Scaler reference 1280x720, match 0.5.
- Name labels: 6 UI pills, per-frame WorldToScreenPoint from the 3D anchor (y 1.85 m). Pill: profile-color bg, #1A1A1A text (team-room SOT), 12 px bold, name + state dot. Hidden when behind camera or when that agent's drill panel is open. Click opens the drill panel.
- Drill panel (port of office.html t_bb332f8e):
  - Desktop (>=521 px): right side, width min(380px, 44vw), full height, radius 10px 0 0 10px, slide-in 0.16 s.
  - Mobile (<=520 px): bottom sheet, height min(66vh, 560px), radius 12px 12px 0 0, sheet-up 0.18 s, grab handle in head.
  - Head: title 13 px bold; state chip (stuck=bad, work, think=busy, done=good); close button.
  - Body: sections (h3 10 px uppercase ls .08em); kv rows (k 96 px dim / v ink); stream (ts + kind[tool|reasoning|text] + text, last 50 lines, autoscroll).
  - Data: /office-state agent fields (card_id, card_title, card_state, tool_label, elapsed_ms, last_delta_ms) + SSE turn stream (turn/turn_delta/message — same events as the 2D office).
- Board counts: UI panel top-right (mirrors 2D position ~top 8%), 5 columns with live counts from /office-state.board (fallback /kanban), COLHEAD colors, up to 3 card bars per column. Click -> postMessage to host page -> host opens /board in a new tab.
- Status footer: bottom bar: dot (green when round active) + status text (headline = most-recently-active agent; "N - agent - state - elapsed" when >1 concurrent; else "round active - waiting for a turn" / "idle - no active round") + scene clock (day/night, 1 real hour = 1 scene day) + real clock.

## 4. Day/night lighting (replaces flat TINT_KEYS)
Clock (same as 2D office, so both surfaces agree): hour = (now_ms % 3600000) / 3600000 * 24.
- Sun (DirectionalLight):
  - 0-5 h night: off. 5-8 h dawn: #e8a06a, intensity 0.3->1.1, elevation 8->30 deg.
  - 8-17 h day: #fff2d0, intensity 1.1->2.0 (peak 12 h), elevation 30->60->30 deg.
  - 17-19.5 h dusk: #d88a5a, intensity 1.1->0.3, elevation 30->8 deg. 19.5-24 h: off.
  - Azimuth sweeps -60->+60 deg over 5->19.5 h (sun crosses the room left->right).
- Ambient/hemisphere (lerp at TINT_KEYS hours 0/5/8/12/17/19.5/22/24):
  - night sky #0a1028 ground #05070c int 0.30; dawn sky #6b4a7a int 0.45; day sky #fff2d0 ground #8a7a5a int 0.55; dusk sky #96463a int 0.40.
- Desk lamps (PointLight Lamp_<profile>): color = profile color; intensity = glow * 2.0 with glow = clamp(0.10 + 0.85*lampOn + live, 0, 1) (ported from office.html sceneState; lampOn = clamp((darkness-0.40)/0.40, 0, 1), darkness = 1-(cos((hour-12)*PI/12)+1)/2, live = 0.15 when round active); range 2.5 m, decay 2.
- Background: solid lerp #0a1028 (night) -> #2a241c (day), same keyframe hours.
- Profile color legibility: avatar baseColor = profile color (config.json SOT); emissive = profile color * 0.18 so identity reads at night; lamp light is profile-colored so each desk glows its own hue (matches 2D per-agent lamp glow).

## 5. Ownership / integration
- ux-ui (this card): this spec, HUD (Unity UI Canvas), day/night rig (DayNightCycle.cs), camera framing (CameraRig.cs).
- SE (t_34b001b1 / t_3d90d98d / t_f1796dd6): unity/ project + Office.unity (room shell, desks, slots, lamps, AssetLoader, build pipeline, office3d.html host), OfficeStateBinder, backend routes.
- Pixel (t_4b7651ed / t_86542f7a / t_289161d6): GLB avatars, environment (incl. wall board geometry), 12 clips/avatar.
- Scene contract for my scripts: named transforms Avatar_<profile>, Lamp_<profile>, RoomCenter (4.8, 1.0, 4.2); /office-state + /events wired per the SE cards.
