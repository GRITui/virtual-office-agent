// office3d.js — Three.js 3D office (composition layer).
// Runtime: Three.js r160 (ES module) + GLTFLoader. Served at /office3d.
// SOT for state/labels/day-night is ported from office.html (2D office).
import * as THREE from 'three';
import { GLTFLoader } from '/assets/office3d/GLTFLoader.js';

// ================= SOT constants (ported from office.html) =================
const TOKEN = localStorage.getItem('tr_token') || new URLSearchParams(location.search).get('token') || '';
const AUTH = {}; if (TOKEN) AUTH['Authorization'] = 'Bearer ' + TOKEN;
const DESK = { cols: 3, rows: 2, top_px: 47, cell_px: [64, 60], px_to_m: 0.05 };
const ORDER = ['todo', 'ready', 'running', 'blocked', 'done'];
const COLHEAD = { todo: 'Todo', ready: 'Ready', running: 'Running', blocked: 'Blocked', done: 'Done' };
const LABELS = { idle: 'Idle', thinking: 'Thinking', working: 'Working', typing: 'Typing', stuck: 'Stuck', done: 'Done' };
const ACCENTS = {
  'product-owner': [232, 185, 61], 'pixel-art-designer': [224, 93, 93], 'ux-ui': [93, 184, 224],
  'software-engineer': [126, 201, 111], 'qa-visual': [201, 143, 224], 'qa-journey': [224, 154, 93]
};
// SOT accents are sRGB. three.js r160's float Color(r,g,b) ctor stores them as-is
// (treated as linear, no sRGB->linearize) so they render ~gamma-2.2 brighter.
// Build via setHex (sRGB->linear) so materials/lights are correct in working space;
// getHexString() (sRGB default) round-trips the DOM swatches back to SOT sRGB.
function accentHex(name) {
  const [r, g, b] = ACCENTS[name];
  return (r << 16) | (g << 8) | b;
}
const TINT_KEYS = [ // [hour, r, g, b, alpha] flat ambient tint keyframes (wraps 24->0)
  [0.0, 10, 16, 40, 0.42], [5.0, 107, 74, 122, 0.24], [8.0, 185, 138, 90, 0.10],
  [12.0, 255, 242, 208, 0.03], [17.0, 185, 138, 90, 0.09], [19.5, 150, 70, 58, 0.22],
  [22.0, 16, 20, 46, 0.36], [24.0, 10, 16, 40, 0.42]
];
const DAY_MS = 3600000; // 1 real hour = 1 full scene day
const AGENT_ORDER = ['product-owner', 'pixel-art-designer', 'ux-ui', 'software-engineer', 'qa-visual', 'qa-journey'];
// anchor_px from assets/office3d/avatars/manifest.json (cell origin + (32,14))
const ANCHOR_PX = {
  'product-owner': [32, 61], 'pixel-art-designer': [96, 61], 'ux-ui': [160, 61],
  'software-engineer': [32, 121], 'qa-visual': [96, 121], 'qa-journey': [160, 121]
};
// desk 3D positions (from environment.glb desk_0..desk_5), by seat index
const DESK_XZ = [[0, 2.35], [3.2, 2.35], [6.4, 2.35], [0, 5.35], [3.2, 5.35], [6.4, 5.35]];
const clamp = (v, a, b) => v < a ? a : (v > b ? b : v);
// optional pinned scene hour for headless day/night verification (?hour=HH)
let _forceHour = (function () { const hh = new URLSearchParams(location.search).get('hour'); return hh ? parseFloat(hh) : null; })();

// ================= scene / renderer / camera =================
const canvas = document.getElementById('gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 100);

// orbit camera (fixed cinematic default + drag-to-orbit with clamps)
const TARGET = new THREE.Vector3(4.8, 1.0, 3.9);
const sph = { radius: 12.5, phi: 60 * Math.PI / 180, theta: 0 }; // phi from +Y, theta azimuth
function updateCamera() {
  const sp = Math.sin(sph.phi), cp = Math.cos(sph.phi);
  camera.position.set(
    TARGET.x + sph.radius * sp * Math.sin(sph.theta),
    TARGET.y + sph.radius * cp,
    TARGET.z + sph.radius * sp * Math.cos(sph.theta)
  );
  camera.lookAt(TARGET);
}
let dragging = false, _px = 0, _py = 0;
canvas.addEventListener('pointerdown', e => { dragging = true; _px = e.clientX; _py = e.clientY; canvas.setPointerCapture(e.pointerId); });
window.addEventListener('pointermove', e => {
  if (!dragging) return;
  sph.theta -= (e.clientX - _px) * 0.005;
  sph.phi -= (e.clientY - _py) * 0.005;
  sph.theta = clamp(sph.theta, -75 * Math.PI / 180, 75 * Math.PI / 180);
  sph.phi = clamp(sph.phi, 22 * Math.PI / 180, 82 * Math.PI / 180);
  _px = e.clientX; _py = e.clientY; updateCamera();
});
window.addEventListener('pointerup', () => { dragging = false; });
canvas.addEventListener('wheel', e => { e.preventDefault(); sph.radius = clamp(sph.radius + e.deltaY * 0.012, 8, 19); updateCamera(); }, { passive: false });
function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h); camera.aspect = w / h;
  // pull the camera back on portrait (mobile) so the full 6-desk grid fits in frame
  const a = camera.aspect;
  sph.radius = clamp(12.5 / Math.min(1, a), 8, 26);
  camera.updateProjectionMatrix(); updateCamera();
}
window.addEventListener('resize', resize); resize(); updateCamera();

// ================= warm lighting rig =================
const hemi = new THREE.HemisphereLight(0xfff2df, 0x2a2018, 0.55); scene.add(hemi);
const sun = new THREE.DirectionalLight(0xfff0d8, 1.1);
sun.position.set(3.5, 9, 7); sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.camera.left = -7; sun.shadow.camera.right = 7;
sun.shadow.camera.top = 7; sun.shadow.camera.bottom = -7;
sun.shadow.camera.near = 1; sun.shadow.camera.far = 30;
sun.target.position.set(4.8, 0, 4); scene.add(sun); scene.add(sun.target);
const ambient = new THREE.AmbientLight(0xfff3e0, 0.35); scene.add(ambient);
// night tint overlay (a large inverted sphere is overkill; use scene.background + fog)
scene.fog = new THREE.Fog(0x0a0e14, 18, 40);

// ================= asset loading (GLTFLoader) =================
const loader = new GLTFLoader();
const avatars = {}; // id -> {root, armL, armR, head, lamp, glowMat, state, color, card, stream, pose, baseY}
let assetsLoaded = false;

function measureHeight(obj) {
  const box = new THREE.Box3().setFromObject(obj);
  return { min: box.min.clone(), max: box.max.clone(), h: box.max.y - box.min.y };
}
// desk_0's lamp child is named 'desk_lamp'; desks 1-5 are 'desk_lamp_1'..'_5'.
// getObjectByName('desk_lamp') only matches desk_0, so match by prefix instead.
function findLampNode(desk) {
  if (!desk) return null;
  for (let c = 0; c < desk.children.length; c++) {
    const nm = desk.children[c].name || '';
    if (nm.indexOf('desk_lamp') === 0) return desk.children[c];
  }
  return null;
}

// ================= contrast / outline fixes (asset-side, QA gate t_de12c231) =================
// Root cause: flat pastel PBR colors + ACES tone mapping wash the board columns and
// avatar/desk edges into the cream background at 390px. Fixes: darken board bg,
// saturate the 5 columns, add subtle dark edge lines (desks) + inverted-hull outlines
// (avatars). Outline opacity is day/night-aware (stronger by day).
const outlineMats = []; // { mat, day, night }
function applyBoardContrast(root) {
  const mats = {};
  root.traverse(o => { if (o.isMesh && o.material && o.material.name) mats[o.material.name] = o.material; });
  // darken board background so the 5 columns read as discrete items (gaps become dark dividers)
  if (mats['board_bg']) mats['board_bg'].color.setHex(0x232833);
  // boost column saturation + value separation (pastels compress to a uniform mid-gray under ACES)
  const COL = { col_0: 0xf2b01e, col_1: 0x6b8cae, col_2: 0x2e86e0, col_3: 0xe04848, col_4: 0x52b04a };
  for (const k in COL) if (mats[k]) mats[k].color.setHex(COL[k]);
}
function addDeskOutlines(root) {
  const mat = new THREE.LineBasicMaterial({ color: 0x3a2a1e, transparent: true, opacity: 0.5 });
  outlineMats.push({ mat, day: 0.55, night: 0.28 });
  const meshes = [];
  root.traverse(o => { if (o.isMesh && o.material && o.material.name === 'wood_top') meshes.push(o); });
  for (const m of meshes) m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry, 24), mat));
}
function addAvatarOutline(root) {
  const mat = new THREE.MeshBasicMaterial({ color: 0x3a2a1e, side: THREE.BackSide, transparent: true, opacity: 0.8 });
  outlineMats.push({ mat, day: 0.85, night: 0.5 });
  // collect first, then add shells — adding a Mesh child mid-traverse would recurse into it (stack overflow)
  const meshes = [];
  root.traverse(o => { if (o.isMesh && !o.userData.isOutline) meshes.push(o); });
  for (const m of meshes) {
    const shell = new THREE.Mesh(m.geometry, mat);
    shell.scale.setScalar(1.04);
    shell.userData.isOutline = true;
    m.add(shell);
  }
}
function updateOutlineOpacity() {
  const d = sceneState().daylight;
  for (const o of outlineMats) o.mat.opacity = o.night + (o.day - o.night) * d;
}

async function loadAssets() {
  // environment (room, desks, wall board, window, clock, plant)
  const env = await loader.loadAsync('/assets/office3d/environment.glb');
  env.scene.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  scene.add(env.scene);
  applyBoardContrast(env.scene);
  addDeskOutlines(env.scene);
  // capture desk-lamp world positions (desk_0..desk_5 each have a desk_lamp child;
  // names are 'desk_lamp' / 'desk_lamp_1'..'_5' — matched by prefix, not exact name)
  const lampPos = [];
  for (let i = 0; i < 6; i++) {
    const desk = env.scene.getObjectByName('desk_' + i);
    const lamp = findLampNode(desk);
    if (lamp) { lamp.getWorldPosition(_v1); lampPos.push(_v1.clone()); }
    else lampPos.push(new THREE.Vector3(DESK_XZ[i][0], 0.95, DESK_XZ[i][1] - 0.15));
  }
  // avatars
  for (let i = 0; i < AGENT_ORDER.length; i++) {
    const name = AGENT_ORDER[i];
    const g = await loader.loadAsync('/assets/office3d/avatars/' + name + '.glb');
    const root = g.scene;
    const m = measureHeight(root);
    const scale = 1.15 / (m.h || 1.15);
    root.scale.setScalar(scale);
    const [ax, ay] = ANCHOR_PX[name];
    const x3 = ax * DESK.px_to_m, z3 = ay * DESK.px_to_m;
    root.position.set(x3, -m.min.y * scale, z3); // feet at y=0
    root.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    scene.add(root);
    addAvatarOutline(root);
    const armL = root.getObjectByName('armL'), armR = root.getObjectByName('armR'), head = root.getObjectByName('head');
    // state glow: a small emissive disc at the desk (profile-colored) + a point lamp
    const glowMat = new THREE.MeshBasicMaterial({ color: new THREE.Color().setHex(accentHex(name)), transparent: true, opacity: 0.0 });
    const glow = new THREE.Mesh(new THREE.CircleGeometry(0.16, 20), glowMat);
    glow.rotation.x = -Math.PI / 2; glow.position.set(DESK_XZ[i][0], 0.02, DESK_XZ[i][1]);
    scene.add(glow);
    const lamp = new THREE.PointLight(new THREE.Color().setHex(accentHex(name)), 0.0, 4.5, 2);
    lamp.position.copy(lampPos[i]); scene.add(lamp);
    // lamp head emissive (t_4e215b38): the 'shade' primitive is ONE shared material
    // (mat[6]) across all 6 desks, so clone it per desk and drive its emissive by
    // state — the lamp HEAD carries the state color (not a detached floor pool).
    // toneMapped:false keeps the SOT state color crisp (same wash fix as t_c15ff62e).
    let headMat = null;
    const lampNode = findLampNode(env.scene.getObjectByName('desk_' + i));
    if (lampNode) lampNode.traverse(o => {
      if (o.isMesh && o.material && o.material.name === 'shade' && !headMat) {
        headMat = o.material.clone(); headMat.toneMapped = false; o.material = headMat;
      }
    });
    avatars[name] = {
      root, armL, armR, head, lamp, glow, glowMat, headMat,
      state: 'idle', color: new THREE.Color().setHex(accentHex(name)),
      card: '', card_id: null, card_state: null, tool_label: null,
      stream: [], pose: { aL: 0, aR: 0, h: 0 }, baseY: root.position.y,
      anchor3: new THREE.Vector3(x3, 1.25, z3) // label anchor (above head)
    };
  }
  assetsLoaded = true;
}
const _v1 = new THREE.Vector3();

// ================= state machine (SOT port from office.html) =================
const POSE = {
  idle:     { aL: 0.0,  aR: 0.0,  h: 0.0 },
  thinking: { aL: 0.0,  aR: -1.05, h: -0.15 },
  working:  { aL: -0.55, aR: -0.55, h: 0.0 },
  typing:   { aL: -0.7,  aR: -0.7,  h: 0.05 },
  stuck:    { aL: 0.4,  aR: 0.4,  h: 0.32 },
  done:     { aL: -0.15, aR: -0.15, h: -0.08 }
};
const ACTIVE = ['thinking', 'working', 'typing', 'stuck'];
// 'done' is transient (server holds it OFFICE_DONE_MS=600ms after a turn posts):
// a short lamp/glow flash matching the 2D done-flash, NOT a persistent active lamp.
const DONE_FLASH_MS = 600;
const state = { roundActive: false, board: {} };

function anyActive() { return AGENT_ORDER.some(n => avatars[n] && ACTIVE.includes(avatars[n].state)); }
function tintAt(hour) {
  let i = 0;
  while (i < TINT_KEYS.length - 1 && TINT_KEYS[i + 1][0] < hour) i++;
  const a = TINT_KEYS[i], b = TINT_KEYS[Math.min(i + 1, TINT_KEYS.length - 1)];
  const t = (hour - a[0]) / ((b[0] - a[0]) || 1);
  const L = p => a[p] + (b[p] - a[p]) * t;
  return { r: L(1), g: L(2), b: L(3), a: a[4] + (b[4] - a[4]) * t };
}
function sceneState(now) {
  now = now || Date.now();
  const hour = (_forceHour != null) ? _forceHour : ((now % DAY_MS) / DAY_MS) * 24;
  const daylight = (Math.cos((hour - 12) * Math.PI / 12) + 1) / 2;
  const darkness = 1 - daylight;
  const lampOn = clamp((darkness - 0.40) / 0.40, 0, 1);
  const live = (state.roundActive || anyActive()) ? 0.15 : 0;
  const glow = clamp(0.10 + 0.85 * lampOn + live, 0, 1);
  return { hour, daylight, darkness, lampOn, tint: tintAt(hour), glow };
}

function applyState(s) {
  if (!s || !s.agents) return;
  state.roundActive = !!s.round_active;
  state.board = s.board || {};
  for (const a of s.agents) {
    const av = avatars[a.id]; if (!av) continue;
    const prev = av.state;
    av.state = a.state;
    if (a.color) av.color.set(a.color);
    av.card = a.card_title || a.card_id || '';
    av.card_id = a.card_id; av.card_state = a.card_state; av.tool_label = a.tool_label;
    av.elapsed_ms = a.elapsed_ms;
    av.pose = POSE[a.state] || POSE.idle;
    // 'done' transition: stamp the flash start (render loop decays it over DONE_FLASH_MS)
    if (a.state === 'done' && prev !== 'done') av.doneAt = Date.now();
    if (a.state !== 'done') av.doneAt = 0;
    // glow disc: profile-colored, on when active (done flash added per-frame in animate)
    av.glowMat.color.set(a.color || '#888');
    av.glowMat.opacity = ACTIVE.includes(a.state) ? 0.55 : 0.0;
  }
  applyLighting();
  updateBoardCounts();
  if (drill.open) renderPanel();
}

function applyLighting() {
  const st = sceneState();
  // night: base lights drop near-zero so the profile-colored desk lamps dominate
  sun.intensity = 0.02 + 1.25 * st.daylight;
  hemi.intensity = 0.06 + 0.5 * st.daylight;
  ambient.intensity = 0.05 + 0.32 * st.daylight;
  const t = st.tint;
  const bg = new THREE.Color(t.r / 255, t.g / 255, t.b / 255).lerp(new THREE.Color(0.98, 0.95, 0.89), st.daylight * 0.9);
  scene.background = bg;
  scene.fog.color.copy(bg);
  for (const n of AGENT_ORDER) {
    const av = avatars[n]; if (!av) continue;
    const active = ACTIVE.includes(av.state);
    // active lamps bright; idle lamps ride the night glow (auto-on at dusk)
    av._lampBase = (active ? 2.2 : 0.0) + st.glow * (active ? 0.4 : 0.7);
    av.lamp.intensity = av._lampBase;
    av.lamp.color.copy(av.color);
    av.glowMat.color.copy(av.color);
    // lamp head emissive carries the state color (t_4e215b38): active = bright
    // profile accent, idle = off (the PointLight provides the warm ambient).
    // Bound here so head + lamp + floor pool stay in sync on every path.
    if (av.headMat) {
      av._headBase = (active ? 1.7 : 0.0) + st.glow * (active ? 0.35 : 0.0);
      av.headMat.emissiveIntensity = av._headBase;
      av.headMat.emissive.copy(av.color);
    }
  }
  updateOutlineOpacity();
}

async function fetchState() {
  try {
    const r = await fetch('/office-state', { headers: AUTH });
    if (!r.ok) throw 'HTTP ' + r.status;
    applyState(await r.json());
  } catch (e) { dbg('state feed: ' + e); }
}
function connectSSE() {
  try {
    const es = new EventSource('/events' + (TOKEN ? '?token=' + encodeURIComponent(TOKEN) : ''));
    es.addEventListener('message', e => {
      let ev; try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (ev.type === 'office') applyState(ev);
      else if (ev.type === 'turn_delta') onTurnDelta(ev);
    });
  } catch (e) { dbg('sse: ' + e); }
}
function onTurnDelta(ev) {
  const av = avatars[ev.agent]; if (!av) return;
  av.stream.push({ kind: ev.kind, delta: ev.delta, t: Date.now() });
  if (av.stream.length > 60) av.stream.shift();
  if (drill.open && drill.id === ev.agent) renderPanel();
}

// ================= HUD (DOM overlays) =================
const dbgEl = document.getElementById('dbg');
let dbgBuf = '';
function dbg(msg) { dbgBuf = String(msg).slice(0, 300); if (dbgEl) dbgEl.textContent = dbgBuf; }
const drill = { open: false, id: null };
const sceneClockEl = document.getElementById('sceneClock');
const headlineEl = document.getElementById('statusHeadline');
const boardEl = document.getElementById('boardCounts');
const labelsEl = document.getElementById('labels');
const panelEl = document.getElementById('drillPanel');

// ---- board counts (top-left) ----
const BC_COLORS = { todo: '#9a8f7d', ready: '#5db8e0', running: '#7ec96f', blocked: '#e05d5d', done: '#c9b48a' };
function updateBoardCounts() {
  boardEl.innerHTML = '';
  for (const st of ORDER) {
    const n = state.board[st] || 0;
    const d = document.createElement('div'); d.className = 'bc';
    d.innerHTML = '<span class="dot" style="background:' + BC_COLORS[st] + '"></span>' + COLHEAD[st] + ' <span class="n">' + n + '</span>';
    boardEl.appendChild(d);
  }
}

// ---- name labels (projected from 3D anchors) ----
const labelEls = {};
function buildLabels() {
  labelsEl.innerHTML = '';
  for (const n of AGENT_ORDER) {
    const d = document.createElement('div'); d.className = 'lbl'; d.id = 'lbl-' + n;
    d.innerHTML = '<span class="lamp"></span><span class="swatch"></span><span class="nm"></span><span class="st"></span>';
    d.addEventListener('click', () => openDrill(n));
    labelsEl.appendChild(d); labelEls[n] = d;
  }
}
const _pv = new THREE.Vector3();
// short names for narrow viewports (full name in title + drill panel)
const SHORT_NAME = {
  'product-owner': 'PO', 'pixel-art-designer': 'PAD', 'ux-ui': 'UX',
  'software-engineer': 'SE', 'qa-visual': 'QA-V', 'qa-journey': 'QA-J'
};
const _pos = {}; // n -> {x, y, w, h, behind, lift}
function updateLabels() {
  const narrow = innerWidth <= 520;
  for (const n of AGENT_ORDER) {
    const av = avatars[n], el = labelEls[n]; if (!av || !el) continue;
    _pv.copy(av.anchor3).project(camera);
    const behind = _pv.z > 1;
    const x0 = (_pv.x * 0.5 + 0.5) * innerWidth, y = (-_pv.y * 0.5 + 0.5) * innerHeight;
    // keep the pill within the viewport (it is centered on x via translate(-50%))
    const w = el.offsetWidth || 120;
    const x = clamp(x0, w / 2 + 4, innerWidth - w / 2 - 4);
    _pos[n] = { x, y, w, h: el.offsetHeight || 20, behind, lift: 0 };
    const active = ACTIVE.includes(av.state);
    el.querySelector('.swatch').style.background = '#' + av.color.getHexString();
    el.querySelector('.nm').textContent = narrow ? (SHORT_NAME[n] || n) : n;
    el.title = n;
    el.querySelector('.st').textContent = LABELS[av.state] || av.state;
    el.querySelector('.lamp').className = 'lamp' + (active ? ' on' : '');
    el.querySelector('.lamp').style.background = active ? '#' + av.color.getHexString() : '';
    el.classList.toggle('active', drill.open && drill.id === n);
  }
  // collision avoidance: lift pills that overlap horizontally (crowded rows)
  const vis = AGENT_ORDER.filter(n => _pos[n] && !_pos[n].behind);
  vis.sort((a, b) => _pos[a].x - _pos[b].x);
  for (let i = 1; i < vis.length; i++) {
    const p = _pos[vis[i]];
    for (let j = 0; j < i; j++) {
      const q = _pos[vis[j]];
      if (p.x - p.w / 2 >= q.x + q.w / 2 - 4) continue;        // no horizontal overlap
      if (Math.abs(p.y - q.y) >= Math.max(p.h, q.h)) continue; // no vertical overlap
      p.lift = Math.max(p.lift, (p.y - p.h) - (q.y - q.h) + 6); // sit above q with 6px gap
    }
  }
  for (const n of AGENT_ORDER) {
    const p = _pos[n], el = labelEls[n]; if (!p || !el) continue;
    const lift = Math.min(p.lift, Math.max(0, p.y - p.h - 2)); // never above viewport top
    el.style.transform = 'translate(-50%,-100%) translate(' + p.x.toFixed(1) + 'px,' + (p.y - lift).toFixed(1) + 'px)';
    el.style.opacity = p.behind ? '0' : '1';
    el.style.display = p.behind ? 'none' : 'flex';
  }
}

// ---- drill panel ----
function openDrill(id) { drill.open = true; drill.id = id; panelEl.classList.add('open'); renderPanel(); }
function closeDrill() { drill.open = false; drill.id = null; panelEl.classList.remove('open'); }
document.getElementById('drillClose').addEventListener('click', closeDrill);
function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function renderPanel() {
  const av = avatars[drill.id]; if (!av) return;
  document.getElementById('drillSwatch').style.background = '#' + av.color.getHexString();
  document.getElementById('drillName').textContent = drill.id;
  const chip = document.getElementById('drillChip');
  chip.textContent = LABELS[av.state] || av.state;
  chip.style.background = ACTIVE.includes(av.state) ? '#fff6df' : '#fff';
  chip.style.borderColor = '#' + av.color.getHexString();
  let html = '';
  if (av.card) {
    html += '<div class="card"><div class="t">' + esc(av.card) + '</div>' +
      (av.card_state ? '<div style="color:var(--ink-soft);font-size:11px;margin-top:3px">' + esc(av.card_state) + '</div>' : '') +
      (av.card_id ? '<div style="color:var(--ink-soft);font-size:10px;margin-top:2px">card ' + esc(av.card_id) + '</div>' : '') + '</div>';
  }
  if (av.tool_label) html += '<div class="row"><span class="k">tool</span><span class="v">' + esc(av.tool_label) + '</span></div>';
  if (av.elapsed_ms) html += '<div class="row"><span class="k">elapsed</span><span class="v">' + Math.round(av.elapsed_ms / 1000) + 's</span></div>';
  if (av.stream.length) {
    const lines = av.stream.slice(-14).map(s => {
      const tag = s.kind === 'tool' ? '⚒' : (s.kind === 'reasoning' ? '◌' : '·');
      return tag + ' ' + esc(s.delta).replace(/\n/g, ' ').slice(0, 160);
    }).join('\n');
    html += '<div class="stream"><span class="h">live stream</span>' + lines + '</div>';
  }
  // Action row: an active agent (esp. stuck / no-card) must never open a
  // dead-end panel. Stop un-sticks the turn; transcript/board give context —
  // mirrors the 2D office's stuck escape hatch (t_f6904af5).
  if (ACTIVE.includes(av.state)) {
    html += '<div class="actions">' +
      '<button id="stopAgent" class="act stop">Stop</button>' +
      '<a class="act" href="/chat" target="_top">transcript</a>' +
      '<a class="act" href="/board" target="_top">board</a>' +
      '</div>';
  }
  const body = document.getElementById('drillBody');
  body.innerHTML = html || '<div class="row"><span class="v" style="color:var(--ink-soft)">no active card</span></div>';
  const stopBtn = document.getElementById('stopAgent');
  if (stopBtn) stopBtn.addEventListener('click', () => stopAgent());
}
// Stop the current round (un-sticks a stuck / no-card turn). POST /stop is the
// only stop endpoint; it no-ops (stopping:false) when no round is active.
async function stopAgent() {
  const btn = document.getElementById('stopAgent');
  if (btn) { btn.disabled = true; btn.textContent = 'Stopping…'; }
  try {
    const r = await fetch('/stop', { method: 'POST', headers: AUTH });
    let j = {}; try { j = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok || !j.ok) throw 'HTTP ' + r.status;
    dbg('stop: ' + (j.stopping ? 'round stopping' : 'no active round'));
    // The office SSE event re-renders the panel on round-end; poll once as a
    // fallback in case SSE is disconnected so the panel leaves the dead end.
    setTimeout(fetchState, 300);
  } catch (e) {
    dbg('stop: ' + e);
    if (btn) { btn.disabled = false; btn.textContent = 'Stop'; }
  }
}

// ---- status footer + scene clock ----
const hintEl = document.querySelector('#statusFooter .hint');
const HINT_FULL = 'drag to orbit \u00b7 click an agent to drill in';
const HINT_SHORT = 'drag to orbit \u00b7 tap to drill'; // narrow viewports: one line
function updateSceneClock() {
  hintEl.textContent = innerWidth <= 520 ? HINT_SHORT : HINT_FULL;
  const st = sceneState();
  const h = Math.floor(st.hour), m = Math.floor((st.hour % 1) * 60);
  const icon = st.darkness > 0.5 ? '\u263D' : '\u2600';
  const label = icon + ' ' + (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
  if (sceneClockEl.textContent !== label) sceneClockEl.textContent = label;
  const act = AGENT_ORDER.filter(n => avatars[n] && ACTIVE.includes(avatars[n].state)).length;
  headlineEl.textContent = state.roundActive ? ('Round in progress · ' + act + ' active') : (act ? (act + ' active') : 'Office idle');
}

// ================= render loop + boot =================
const _X = new THREE.Vector3(1, 0, 0);
const _q = new THREE.Quaternion(), _tq = new THREE.Quaternion();
let lastLightKey = '';
function animate() {
  requestAnimationFrame(animate);
  const t = performance.now() * 0.001;
  const nowMs = Date.now();
  for (const n of AGENT_ORDER) {
    const av = avatars[n]; if (!av) continue;
    // 'done' transient flash: decay over DONE_FLASH_MS, then expire to idle locally
    let doneFlash = 0;
    if (av.state === 'done' && av.doneAt) {
      const el = nowMs - av.doneAt;
      if (el < DONE_FLASH_MS) doneFlash = 1 - el / DONE_FLASH_MS;
      else { av.state = 'idle'; av.pose = POSE.idle; av.doneAt = 0; }
    }
    av._doneFlash = doneFlash;
    // smooth pose toward target (arm/head rotations about X)
    av._aL += (av.pose.aL - av._aL) * 0.14;
    av._aR += (av.pose.aR - av._aR) * 0.14;
    av._h += (av.pose.h - av._h) * 0.14;
    if (av.armL) { _q.setFromAxisAngle(_X, av._aL); _tq.copy(av.armL._base).multiply(_q); av.armL.quaternion.slerp(_tq, 0.5); }
    if (av.armR) { _q.setFromAxisAngle(_X, av._aR); _tq.copy(av.armR._base).multiply(_q); av.armR.quaternion.slerp(_tq, 0.5); }
    if (av.head) { _q.setFromAxisAngle(_X, av._h); _tq.copy(av.head._base).multiply(_q); av.head.quaternion.slerp(_tq, 0.5); }
    // idle/thinking bob
    if (av.state === 'idle' || av.state === 'thinking') av.root.position.y = av.baseY + Math.sin(t * 1.3 + n.length * 0.7) * 0.012;
    else av.root.position.y = av.baseY;
    // glow disc: subtle floor pool (the lamp HEAD carries the state, t_4e215b38)
    av.glowMat.opacity = (ACTIVE.includes(av.state) ? 0.28 : 0.0) + doneFlash * 0.4;
  }
  // day/night lighting: recompute only when the quantized state changes (cheap)
  const st = sceneState();
  const key = Math.round(st.tint.r) + ',' + Math.round(st.tint.g) + ',' + Math.round(st.tint.b) + ',' + Math.round(st.glow * 100) + ',' + Math.round(st.daylight * 100);
  if (key !== lastLightKey) { lastLightKey = key; applyLighting(); }
  // 'done' lamp pulse: decays with the flash, set (not added) so it never accumulates
  for (const n of AGENT_ORDER) {
    const av = avatars[n]; if (!av) continue;
    av.lamp.intensity = (av._lampBase || 0) + (av._doneFlash || 0) * 1.9;
    if (av.headMat) av.headMat.emissiveIntensity = (av._headBase || 0) + (av._doneFlash || 0) * 1.6;
  }
  updateLabels();
  renderer.render(scene, camera);
  if (!window.__RENDER_DONE && assetsLoaded) {
    window.__RENDER_DONE = true;
    dbg('rendered: env + ' + AGENT_ORDER.length + ' avatars, ' + renderer.info.render.calls + ' draw calls');
  }
}

// ---- boot ----
buildLabels();
updateBoardCounts();
updateSceneClock();
setInterval(updateSceneClock, 1000);
loadAssets().then(() => {
  // store default arm/head quaternions for pose deltas
  for (const n of AGENT_ORDER) {
    const av = avatars[n];
    av._aL = 0; av._aR = 0; av._h = 0;
    if (av.armL) av.armL._base = av.armL.quaternion.clone();
    if (av.armR) av.armR._base = av.armR.quaternion.clone();
    if (av.head) av.head._base = av.head.quaternion.clone();
  }
  applyLighting();
  fetchState(); connectSSE();
  animate();
}).catch(e => { dbg('load: ' + e); window.__RENDER_ERR = String(e); });

// ---- CDP debug handle (inspection / camera override / force state) ----
window.__O3D = {
  get scene() { return scene; }, get camera() { return camera; }, get renderer() { return renderer; },
  get avatars() { return avatars; }, get state() { return state; },
  setCam: function (x, y, z, tx, ty, tz, fov) {
    if (x != null) camera.position.set(x, y, z);
    if (tx != null) { TARGET.set(tx, ty, tz); }
    if (fov) { camera.fov = fov; camera.updateProjectionMatrix(); }
    updateCamera();
  },
  setSph: function (radius, phiDeg, thetaDeg) {
    if (radius) sph.radius = radius;
    if (phiDeg != null) sph.phi = phiDeg * Math.PI / 180;
    if (thetaDeg != null) sph.theta = thetaDeg * Math.PI / 180;
    updateCamera();
  },
  forceState: function (id, st) { const a = avatars[id]; if (a) { a.state = st; a.pose = POSE[st] || POSE.idle; a.doneAt = (st === 'done') ? Date.now() : 0; applyLighting(); } },
  forceHour: function (h) { _forceHour = h; },
  get ready() { return !!window.__RENDER_DONE; }
};
