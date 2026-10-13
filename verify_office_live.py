#!/usr/bin/env python3
"""Re-runnable gate for the live office scene (pixel-art-designer).

Right-layer checks:
  1. room_server.py compiles and has an /office route.
  2. office.html markup: 6 agent slots, /events EventSource, inlined
     environment PNG + 36 character frames (6 x (2 idle + 4 type)),
     the state machine, all activity labels, stuck detection,
     wall-board CTA (postMessage + /board fallback), live board canvas.
  3. The ACTUAL shipped JS (extracted from office.html, not a mirror):
     a. state machine driven in Node with the REAL /events shapes:
        round started -> turn(thinking) -> turn_delta reasoning/tool/text
        -> agent switch -> round done, plus isStuck() timing.
     b. frame engine (stepFrames) driven in Node with DOM stubs:
        typing -> type frames advance; thinking -> idle alternates;
        stuck -> FROZEN (src unchanged); inactive -> idle alternates.
  4. board canvas geometry matches the baked scene layout (COLX + offsets
     derived from the composer constants).
  5. office.html is servable (static server on a test port -> 200).
"""
import os, re, sys, subprocess, tempfile, textwrap, http.server, socketserver, threading, time, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OFFICE = os.path.join(BASE, "office.html")
SERVER = os.path.join(BASE, "room_server.py")
FAIL = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(name)

# 1. server compiles + has the route
r = subprocess.run([sys.executable, "-m", "py_compile", SERVER], capture_output=True, text=True)
check("room_server.py compiles", r.returncode == 0, r.stderr.strip())
src = open(SERVER).read()
check("/office route present", 'path == "/office"' in src and "OFFICE_HTML_PATH" in src)

# 2. office.html markup
html = open(OFFICE).read()
for a in ["product-owner","pixel-art-designer","ux-ui","software-engineer","qa-visual","qa-journey"]:
    check("slot: " + a, a in html)
check("EventSource /events", 'EventSource("/events")' in html)
check("inlined environment png", "id=\"env\"" in html and "data:image/png;base64," in html)
check("state machine exposed", "function applyEvent" in html and "function initialState" in html and "function isStuck" in html)
for lbl in ["thinking","working","typing","stuck?"]:
    check("label: " + lbl, lbl in html)
check("stuck threshold", "STUCK_MS" in html)
check("live blocked flag element", 'class="flag"' in html)
check("flag keyed off /kanban", 'fetch("/kanban")' in html and 'classList.add("blocked")' in html)
check("wall board CTA", 'id="boardCta"' in html)
check("CTA posts to parent", 'postMessage({ type: "team-room:open-kanban" }' in html)
check("CTA standalone fallback -> /board", 'location.href = "/board"' in html)
check("parent listener in combined page",
      'team-room:open-kanban' in open(os.path.join(BASE, "webui", "index.html")).read())
check("live board canvas", 'id="boardLive"' in html and 'width="121" height="33"' in html)
check("board redraw from /kanban", "function drawBoard" in html and "setInterval(loadKanban, 15000)" in html)
# 36 inlined character frames: 6 agents x (2 idle + 4 type)
m = re.search(r"var FRAMES = (\{.*?\});\nvar TYPE_MS", html, re.S)
check("FRAMES table inlined", bool(m))
if m:
    import json as _json
    frames = _json.loads(m.group(1))
    ok = len(frames) == 6 and all(
        len(v.get("idle", [])) == 2 and len(v.get("type", [])) == 4
        and all(x.startswith("iVBORw0KGgo") for x in v["idle"] + v["type"])
        for v in frames.values())
    check("36 frames: 6 agents x (2 idle + 4 type), all base64 png", ok,
          "agents=%d" % len(frames))

# 3a. run the ACTUAL shipped state machine in node against the real event shapes
m = re.search(r"var AGENTS = .*?function isStuck\(state, now\)\{.*?\n\}", html, re.S)
check("extracted state machine from office.html", bool(m))
if m:
    sm = m.group(0)
    test = textwrap.dedent("""
        %s
        module.exports = { initialState, applyEvent, isStuck };
    """) % sm
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(test); smjs = f.name
    harness = textwrap.dedent("""
        const m = require(%r);
        const A = (s,e,n)=>m.applyEvent(s,e,n);
        let s = m.initialState();
        const now = 1000000;
        s = A(s, {type:"round", state:"started", agents:["product-owner"]}, now);
        if(s.roundActive!==true) throw "roundActive not set";
        s = A(s, {type:"turn", agent:"product-owner", state:"thinking"}, now);
        if(s.activeAgent!=="product-owner"||s.activeState!=="thinking") throw "turn/thinking wrong: "+JSON.stringify(s);
        s = A(s, {type:"turn_delta", agent:"product-owner", kind:"reasoning", delta:"hmm"}, now+50);
        if(s.activeState!=="thinking"||s.lastDeltaAt!==now+50) throw "reasoning wrong";
        s = A(s, {type:"turn_delta", agent:"product-owner", kind:"tool", delta:"terminal: ls -la"}, now+120);
        if(s.activeState!=="working"||s.toolLabel!=="terminal: ls -la") throw "tool wrong: "+JSON.stringify(s);
        s = A(s, {type:"turn_delta", agent:"product-owner", kind:"text", delta:"ok"}, now+200);
        if(s.activeState!=="typing") throw "text wrong";
        s = A(s, {type:"turn", agent:"qa-journey", state:"thinking"}, now+300);
        if(s.activeAgent!=="qa-journey") throw "agent switch failed";
        s = A(s, {type:"round", state:"done"}, now+400);
        if(s.activeAgent!==null||s.activeState!==null||s.roundActive!==false) throw "round done not cleared: "+JSON.stringify(s);
        let t = m.initialState();
        t = A(t, {type:"turn", agent:"ux-ui", state:"thinking"}, 1000000);
        if(m.isStuck(t, 1009000) !== true)  throw "isStuck should be true after 9s (STUCK_MS=8000)";
        if(m.isStuck(t, 1000100) !== false) throw "isStuck should be false right after a delta";
        console.log("SM_OK");
    """) % smjs
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(harness); hjs = f.name
    r = subprocess.run(["node", hjs], capture_output=True, text=True)
    check("node: real JS state machine transitions", r.returncode == 0 and "SM_OK" in r.stdout, r.stderr.strip())
    for p in (smjs, hjs):
        try: os.unlink(p)
        except OSError: pass

# 3b. run the ACTUAL shipped frame engine (stepFrames) in node with DOM stubs
m = re.search(r"function stepFrames\(now\)\{.*?\n\}", html, re.S)
check("extracted frame engine from office.html", bool(m))
if m:
    fe = m.group(0)
    harness = textwrap.dedent("""
        // DOM stubs
        function FakeImg(){ this._src = "data:image/png;base64,INITIAL"; }
        FakeImg.prototype.getAttribute = function(k){ return k === "src" ? this._src : null; };
        FakeImg.prototype.setAttribute = function(k, v){ if(k === "src") this._src = v; };
        var figs = {}, slots = {};
        var FRAMES = {};
        var AGENTS = ["product-owner","pixel-art-designer","ux-ui","software-engineer","qa-visual","qa-journey"];
        AGENTS.forEach(function(n){
          FRAMES[n] = { idle: ["I0_"+n, "I1_"+n], type: ["T0_"+n, "T1_"+n, "T2_"+n, "T3_"+n] };
          figs[n] = new FakeImg();
        });
        var anim = {};
        AGENTS.forEach(function(n){ anim[n] = { set:"idle", idx:0, last:0 }; });
        function initialState(){
          return { activeAgent:null, activeState:null, toolLabel:null,
                   startedAt:null, lastDeltaAt:null, roundActive:false };
        }
        function isStuck(state, now){
          return !!(state.activeState && state.lastDeltaAt && (now - state.lastDeltaAt) > 8000);
        }
        var state = initialState();
        var TYPE_MS = 150, IDLE_MS = 700, CALM_MS = 900;
        %s
        function urlOf(n){ return figs[n].getAttribute("src").replace("data:image/png;base64,",""); }
        // typing: type frames must advance
        state = { activeAgent:"ux-ui", activeState:"typing", startedAt:1000, lastDeltaAt:1000 };
        stepFrames(1000);
        var seen = new Set();
        for(var t = 1000; t <= 1000 + 150*4 + 200; t += 150){ stepFrames(t); seen.add(urlOf("ux-ui")); }
        if(!/^T/.test(urlOf("ux-ui"))) throw "typing agent not on type frames: "+urlOf("ux-ui");
        if(seen.size < 4) throw "type frames did not advance (saw "+seen.size+" distinct)";
        // thinking: idle frames alternate, never type frames
        state = { activeAgent:"ux-ui", activeState:"thinking", startedAt:2000, lastDeltaAt:2000 };
        anim["ux-ui"] = { set:"idle", idx:0, last:0 };
        for(var t = 2000; t <= 2000 + 700*3 + 100; t += 700){ stepFrames(t); }
        if(!/^I/.test(urlOf("ux-ui"))) throw "thinking agent left idle frames: "+urlOf("ux-ui");
        // stuck: FROZEN — src must not change across ticks
        state = { activeAgent:"ux-ui", activeState:"typing", startedAt:3000, lastDeltaAt:3000 };
        anim["ux-ui"] = { set:"type", idx:1, last:3000 };
        stepFrames(3000);
        var frozen = figs["ux-ui"].getAttribute("src");
        for(var t = 3000 + 8001; t <= 3000 + 8001 + 1500; t += 150) stepFrames(t);
        if(figs["ux-ui"].getAttribute("src") !== frozen) throw "stuck agent must freeze, src changed";
        // inactive: idle alternates (calm)
        state = { activeAgent:null, activeState:null, startedAt:null, lastDeltaAt:null, roundActive:false };
        anim["qa-journey"] = { set:"idle", idx:0, last:0 };
        for(var t = 5000; t <= 5000 + 900*2 + 100; t += 900) stepFrames(t);
        if(!/^I/.test(urlOf("qa-journey"))) throw "inactive agent left idle frames: "+urlOf("qa-journey");
        console.log("FE_OK");
    """) % fe
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(harness); fjs = f.name
    r = subprocess.run(["node", fjs], capture_output=True, text=True)
    check("node: real JS frame engine (type/idle/freeze/calm)", r.returncode == 0 and "FE_OK" in r.stdout, r.stderr.strip())
    try: os.unlink(fjs)
    except OSError: pass

# 3c. run the ACTUAL shipped loadState (initial-state-sync) in node with stubs
# A fresh load must seed the scene from a /state snapshot: per-agent lamps
# light up and the headline picks the most-recently-active agent.
m = re.search(r"function loadState\(\)\{.*?\n\}", html, re.S)
check("extracted loadState (initial-state-sync) from office.html", bool(m))
if m:
    ls = m.group(0)
    harness = textwrap.dedent("""
        // stubs for the browser globals loadState touches
        var state = { agents:{}, roundActive:false, activeAgent:null, activeState:null,
                      toolLabel:null, startedAt:null, lastDeltaAt:null };
        var slots = {};
        ["product-owner","pixel-art-designer","ux-ui","software-engineer","qa-visual","qa-journey"]
          .forEach(function(n){ slots[n] = {}; });
        var renderCalls = 0;
        function render(){ renderCalls++; }
        function authHeaders(){ return {}; }
        function promptForToken(){ return false; }
        var PAYLOAD = {
          round_active: true,
          turns: {
            "product-owner": { state:"working", started_at:1000, last_delta_at:1500, tool_label:"terminal: ls -la" },
            "qa-journey":    { state:"thinking", started_at:1200, last_delta_at:2000, tool_label:null }
          }
        };
        var fetched = 0;
        function fetch(url, opts){
          fetched++;
          if(url !== "/state") throw "loadState must fetch /state, got " + url;
          return Promise.resolve({ status:200,
            json: function(){ return Promise.resolve(PAYLOAD); } });
        }
        %s
        loadState();
        setTimeout(function(){
          if(fetched !== 1) throw "fetch not called once";
          if(state.roundActive !== true) throw "roundActive not seeded";
          if(Object.keys(state.agents).length !== 2)
            throw "expected 2 agents, got " + Object.keys(state.agents).length;
          var po = state.agents["product-owner"];
          if(!po || po.state !== "working" || po.toolLabel !== "terminal: ls -la"
             || po.startedAt !== 1000 || po.lastDeltaAt !== 1500)
            throw "product-owner seed wrong: " + JSON.stringify(po);
          var qj = state.agents["qa-journey"];
          if(!qj || qj.state !== "thinking" || qj.toolLabel !== null)
            throw "qa-journey seed wrong: " + JSON.stringify(qj);
          if(state.activeAgent !== "qa-journey")
            throw "headline should be most-recent (qa-journey), got " + state.activeAgent;
          if(state.activeState !== "thinking") throw "headline state wrong: " + state.activeState;
          if(renderCalls < 1) throw "render not called after seeding";
          console.log("LS_OK");
        }, 20);
    """) % ls
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(harness); ljs = f.name
    r = subprocess.run(["node", ljs], capture_output=True, text=True)
    check("node: real JS loadState seeds scene from /state (initial-state-sync)",
          r.returncode == 0 and "LS_OK" in r.stdout, r.stderr.strip())
    try: os.unlink(ljs)
    except OSError: pass

# 4. board canvas geometry matches the baked scene (composer constants)
# composer v2: board (36,14)-(156,46) in a 192x168 scene; col x0 = 40 + i*29, col_w 24;
# count at canvas-local (cx0-25, 11); cards at (cx0-34, 19+k*4) size 20x3.
# canvas origin = board origin (36,14). Internal layout identical to v1.
m = re.search(r"var COLX = \[([0-9, ]+)\];", html)
check("COLX matches composer layout", m and m.group(1).replace(" ", "") == "40,69,98,127",
      m.group(1) if m else "missing")
# v2 deep-office layout: 192x168 scene, chars at y=96 (57.1429%), board at y=14 (8.3333%)
for needle, why in [
    ("aspect-ratio:192/168", "scene aspect 192/168 (deep office)"),
    ("top:57.1429%", "char overlay at y=96/168"),
    ("top:8.3333%", "board overlay at y=14/168"),
    ("height:19.6429%", "board overlay height 33/168"),
]:
    check("v2 layout: " + why, needle in html)
for needle, why in [
    ("c.fillRect(cx0 - 29, 10, 9, 7)", "count erase box (native x cx-4..cx+4, y 18..24)"),
    ("c.fillRect(cx0 - 35, 18, 22, 13)", "card erase box (native x cx0+1..cx0+22, y 26..38)"),
    ("drawCount(c, n, cx0 - 25, 11)", "count draw at native (cx0+11, 19)"),
    ("c.fillRect(cx0 - 34, 19 + k * 4, 20, 3)", "card bars at native (cx0+2, 27+k*4) 20x3"),
]:
    check("board geometry: " + why, needle in html)

# 5. office.html is servable
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=BASE, **k)
    def log_message(self, *a): pass
try:
    httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    with urllib.request.urlopen("http://127.0.0.1:%d/office.html" % port, timeout=5) as resp:
        body = resp.read().decode()
        check("office.html served 200 + is the office page",
              resp.status == 200 and "team-room" in body and "applyEvent" in body and "boardCta" in body)
    httpd.shutdown()
except Exception as e:
    check("office.html served", False, str(e))

print("\n%d failed" % len(FAIL) if FAIL else "\nALL GREEN (office scene gate)")
sys.exit(1 if FAIL else 0)
