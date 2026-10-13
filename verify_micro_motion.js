// Headless verify gate for the office.html micro-motion (pin-pop / flicker / ambience).
// RIGHT LAYER: extracts the <script> from the LIVE served page (127.0.0.1:8651/office),
// node --check's it, loads it in Node with DOM stubs, and drives the real SSE event
// shapes through the shipped state machine + the shipped diff/pin logic.
// Exit 0 = pass. No mirrors: the code under test is the shipped code.
"use strict";
const fs = require("fs");
const http = require("http");
const { execSync } = require("child_process");

const BASE = "http://127.0.0.1:8651";
let failures = 0;
function ok(name, cond, extra){
  if(cond) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (extra ? " — " + extra : "")); }
}
function get(url){
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ""; r.on("data", d => b += d); r.on("end", () => res({ code: r.statusCode, body: b })); }).on("error", rej);
  });
}

(async () => {
  // ---- 1. live wire: /office serves the micro-motion code ----
  const page = await get(BASE + "/office");
  ok("wire /office 200", page.code === 200);
  const html = page.body;
  ok("served html has pin-pop (spawnPops/diffLands)", html.indexOf("spawnPops") !== -1 && html.indexOf("diffLands") !== -1);
  ok("served html has monitor flicker (flickerLamp + keyframes)", html.indexOf("flickerLamp") !== -1 && html.indexOf("monitorFlicker") !== -1);
  ok("served html has idle ambience (drawAmbience: steam + plant)", html.indexOf("drawAmbience") !== -1 && html.indexOf("PLANTS") !== -1);
  ok("served html ties kanban SSE to re-fetch (pin-pop trigger)", html.indexOf('evt.type === "kanban"') !== -1 && html.indexOf("evt.changed") !== -1);
  ok("served html ties turn_delta tool to flicker", html.indexOf('evt.kind === "tool"') !== -1 && html.indexOf("flickerLamp(evt.agent)") !== -1);

  const m = html.match(/<script>([\s\S]*)<\/script>/);
  ok("script block extracted", !!m);
  const js = m[1];
  const tmp = "/tmp/office_extracted_micro_motion.js";
  fs.writeFileSync(tmp, js);

  // ---- 2. syntax: shipped script parses ----
  try { execSync("node --check " + tmp, { stdio: "pipe" }); ok("syntax node --check", true); }
  catch (e) { ok("syntax node --check", false, String(e.stderr || e).slice(0, 200)); }

  // ---- 3. load the shipped script in Node with DOM stubs ----
  const board = (await get(BASE + "/kanban")).body;
  const boardPayload = JSON.parse(board);

  function ctxStub(){
    return { fillRect(){}, clearRect(){}, fillStyle: "", globalAlpha: 1,
      getImageData(){ return { data: new Uint8ClampedArray(4 * 121 * 33) }; } };
  }
  function el(){
    return {
      className: "", style: {}, children: [], _attrs: {}, _listeners: {},
      classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
      innerHTML: "", textContent: "",
      appendChild(c){ this.children.push(c); },
      querySelector(){ return el(); },
      addEventListener(t, f){ (this._listeners[t] = this._listeners[t] || []).push(f); },
      getAttribute(k){ return this._attrs[k] || null; },
      setAttribute(k, v){ this._attrs[k] = v; },
      getContext(){ return ctxStub(); },
      offsetWidth: 0
    };
  }
  const elements = {};
  global.document = {
    getElementById(id){ if(!elements[id]) elements[id] = el(); return elements[id]; },
    createElement(){ return el(); }
  };
  global.window = global;
  global.module = { exports: {} };           // indirect eval runs in global scope; give it a module
  global.location = { href: "" };
  global.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(boardPayload) });
  global.setInterval = () => 1;               // deterministic: no timers fire
  global.requestAnimationFrame = () => 1;
  global.EventSource = function(){ this.onopen = null; this.onmessage = null; this.onerror = null; };

  let loaded = true, loadErr = "";
  try { (0, eval)(js); } catch (e) { loaded = false; loadErr = String(e && e.stack || e).slice(0, 300); }
  ok("shipped script loads headless (DOM stubs)", loaded, loadErr);
  if(!loaded) process.exit(1);

  const api = global.module.exports;
  ok("module.exports exposes state machine + diff", !!(api.initialState && api.applyEvent && api.isStuck && api.buildCols && api.diffLands && api.ORDER && api.COLX));

  // ---- 4. state machine: real event shapes, full transition sequence ----
  const T0 = 1700000000000; // non-zero base (0 is falsy — isStuck guard pitfall)
  let s = api.initialState();
  s = api.applyEvent(s, { type: "round", state: "started", agents: [] }, T0);
  ok("round started -> roundActive", s.roundActive === true);

  s = api.applyEvent(s, { type: "turn", agent: "pixel-art-designer", state: "thinking" }, T0 + 100);
  ok("turn thinking -> agent thinking", s.agents["pixel-art-designer"] && s.agents["pixel-art-designer"].state === "thinking");

  s = api.applyEvent(s, { type: "turn_delta", agent: "pixel-art-designer", kind: "reasoning", delta: "hmm" }, T0 + 500);
  ok("turn_delta reasoning -> stays thinking, lastDeltaAt bumped",
     s.agents["pixel-art-designer"].state === "thinking" && s.agents["pixel-art-designer"].lastDeltaAt === T0 + 500);

  s = api.applyEvent(s, { type: "turn_delta", agent: "pixel-art-designer", kind: "tool", delta: "terminal: curl -s /kanban" }, T0 + 900);
  ok("turn_delta tool -> working + toolLabel",
     s.agents["pixel-art-designer"].state === "working" && s.agents["pixel-art-designer"].toolLabel === "terminal: curl -s /kanban");

  s = api.applyEvent(s, { type: "turn_delta", agent: "pixel-art-designer", kind: "text", delta: "ok" }, T0 + 1200);
  ok("turn_delta text -> typing", s.agents["pixel-art-designer"].state === "typing");

  s = api.applyEvent(s, { type: "turn", agent: "qa-visual", state: "thinking" }, T0 + 1300);
  ok("second agent turn -> both active, headline switches",
     s.agents["pixel-art-designer"] && s.agents["qa-visual"] && s.activeAgent === "qa-visual");

  s = api.applyEvent(s, { type: "message", message: { author: "pixel-art-designer" } }, T0 + 1400);
  ok("message -> that agent done", s.agents["pixel-art-designer"].state === "done");

  s = api.applyEvent(s, { type: "round", state: "done", agents: [] }, T0 + 1500);
  ok("round done -> all cleared",
     Object.keys(s.agents).length === 0 && s.activeAgent === null && s.roundActive === false);

  // isStuck both ways (stale -> true, fresh -> false)
  let st = api.initialState();
  st = api.applyEvent(st, { type: "turn", agent: "qa-journey", state: "thinking" }, T0);
  ok("isStuck stale -> true", api.isStuck(st, T0 + 9000, "qa-journey") === true);
  st = api.applyEvent(st, { type: "turn_delta", agent: "qa-journey", kind: "reasoning", delta: "x" }, T0 + 8900);
  ok("isStuck fresh -> false", api.isStuck(st, T0 + 9000, "qa-journey") === false);

  // ---- 5. pin-pop diff: cards landing on the wall board ----
  // Synthetic board with known counts (live board's columns are full — a new
  // card lands beyond the 3 visible bars and must NOT pop; that is tested below).
  const mk = (id, status) => ({ id, status });
  const prevTasks = [mk("t_r1", "running"), mk("t_t1", "todo")];
  const prev = api.buildCols(prevTasks);
  ok("buildCols from synthetic board", prev.running.length === 1 && prev.todo.length === 1);

  // new task lands in running col row 1 (after t_r1)
  const cur1 = api.buildCols(prevTasks.concat([mk("t_r2", "running")]));
  let lands = api.diffLands(prev, cur1);
  ok("new task -> exactly one land in running row 1",
     lands.length === 1 && lands[0].col === "running" && lands[0].row === 1 &&
     lands[0].x === api.COLX[2] + 9 && lands[0].y === 19 + 1 * 4);

  // no change -> no lands
  ok("no change -> no lands", api.diffLands(prev, api.buildCols(prevTasks)).length === 0);

  // card moves todo -> running: lands in the new column (row 1)
  const moved = [mk("t_r1", "running"), mk("t_t1", "running")];
  lands = api.diffLands(prev, api.buildCols(moved));
  ok("card move todo->running -> lands in running only (row 1)",
     lands.length === 1 && lands[0].col === "running" && lands[0].row === 1);

  // card beyond visible row 2 -> no pop (board draws max 3 card bars)
  const deep = api.buildCols([mk("t_a", "todo"), mk("t_b", "todo"), mk("t_c", "todo"), mk("t_d", "todo")]);
  const deepLands = api.diffLands(api.buildCols([]), deep);
  ok("4 new cards -> only rows 0..2 pop", deepLands.length === 3 && deepLands.every(l => l.row < 3));

  // live board: full running column (5 tasks) + one more -> no visible pop
  const livePrev = api.buildCols(boardPayload.tasks);
  const liveFull = api.buildCols(boardPayload.tasks.concat([mk("t_new1", "running")]));
  const liveLands = api.diffLands(livePrev, liveFull);
  ok("live board full column -> new card beyond row 2 does not pop", liveLands.every(l => l.row < 3));

  console.log(failures === 0 ? "ALL PASS — micro-motion gate green" : failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.log("FAIL harness error: " + e); process.exit(1); });
