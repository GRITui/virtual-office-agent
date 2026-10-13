#!/usr/bin/env python3
"""pixel-art-designer: per-agent lamp/monitor glow keyed off live turn/turn_delta.

t_7ecbf83a. Root cause: office.html state model was single-agent (one
activeAgent), so a second concurrent agent's SSE event clobbered the first —
only one lamp could ever light. Server runs up to max_concurrent_agents (3)
turns in parallel, each tagged with its own `agent`.

Fix:
  - per-agent state map (state.agents) + headline mirror for the single footer
  - per-agent .lamp element per slot; lamp class keyed off THAT agent's state:
    thinking=warm pulse, working=steady blue, typing=steady purple,
    stuck=red, done=brief green flash (entry expires after DONE_MS)
  - the `message` event (author = agent whose turn finished) is the per-agent
    done signal; round done/stopped is a safety net that flashes any turn
    still open
  - headline sync in render(): after a done entry expires, re-point the
    footer at the newest still-active agent (or clear it)

isStuck(state, now) keeps its 2-arg flat-headline shape (the verify gate's
extraction regex + node harness depend on it); per-agent stuck is computed
inline in stepFrames/render from the per-agent entry.

Atomic: every old block must match exactly once or the script aborts without
writing. office.html is static (read fresh per request) -> no restart needed.
"""
import sys, os

BASE = os.path.dirname(os.path.abspath(__file__))
OFFICE = os.path.join(BASE, "office.html")
html = open(OFFICE).read()
orig = html

EDITS = []

# 1a. CSS: .b-done bubble class (green) next to the other bubble states.
EDITS.append(("css b-done",
"""  .b-stuck{border-color:var(--bad); color:var(--bad)}
  .st{font-weight:700}""",
"""  .b-stuck{border-color:var(--bad); color:var(--bad)}
  .b-done{border-color:var(--good); color:var(--good)}
  .st{font-weight:700}"""))

# 1b. CSS: steady purple lamp for typing (flat/crisp stepped rings, no blur),
#     matching the --type palette color used by the typing bubble.
EDITS.append(("css lamp.type",
"""  .lamp.work .bulb{background:var(--work);
    box-shadow:0 0 0 2px rgba(93,184,224,.9), 0 0 0 5px rgba(93,184,224,.45), 0 0 0 8px rgba(93,184,224,.18)}""",
"""  .lamp.work .bulb{background:var(--work);
    box-shadow:0 0 0 2px rgba(93,184,224,.9), 0 0 0 5px rgba(93,184,224,.45), 0 0 0 8px rgba(93,184,224,.18)}
  .lamp.type .bulb{background:var(--type);
    box-shadow:0 0 0 2px rgba(201,143,224,.9), 0 0 0 5px rgba(201,143,224,.45), 0 0 0 8px rgba(201,143,224,.18)}"""))

# 1c. CSS: done flash fades fully out (was .2 residual with `forwards`).
EDITS.append(("css lampFlash fade",
"  @keyframes lampFlash{0%{opacity:1}100%{opacity:.2}}",
"  @keyframes lampFlash{0%{opacity:1}100%{opacity:0}}"))

# 2. State machine: single-agent -> per-agent map + headline mirror + done signal.
EDITS.append(("state machine",
"""// ---- pure state machine (exposed for the verify gate; runs the real event shapes) ----
function initialState(){
  return { activeAgent:null, activeState:null, toolLabel:null,
           startedAt:null, lastDeltaAt:null, roundActive:false };
}
function applyEvent(state, evt, now){
  if(!evt || typeof evt !== "object") return state;
  switch(evt.type){
    case "round":
      if(evt.state === "started") state.roundActive = true;
      else if(evt.state === "done" || evt.state === "stopped"){
        state.activeAgent = null; state.activeState = null;
        state.toolLabel = null; state.roundActive = false;
      }
      return state;
    case "turn":
      if(evt.state === "thinking"){
        state.activeAgent = evt.agent; state.activeState = "thinking";
        state.startedAt = now; state.lastDeltaAt = now; state.toolLabel = null;
      }
      return state;
    case "turn_delta":
      if(state.activeAgent !== evt.agent){
        state.activeAgent = evt.agent; state.startedAt = now; state.toolLabel = null;
      }
      if(evt.kind === "reasoning") state.activeState = "thinking";
      else if(evt.kind === "tool"){ state.activeState = "working";
        state.toolLabel = String(evt.delta || "").slice(0, 42); }
      else if(evt.kind === "text") state.activeState = "typing";
      state.lastDeltaAt = now;
      return state;
    default:
      return state;
  }
}
function isStuck(state, now){
  return !!(state.activeState && state.lastDeltaAt && (now - state.lastDeltaAt) > STUCK_MS);
}""",
"""// ---- pure state machine (PER-AGENT; exposed for the verify gate; real event shapes) ----
// Concurrency: up to max_concurrent_agents turns run in parallel, so the scene
// tracks a PER-AGENT state map (not a single activeAgent) — multiple lamps can
// be lit at once. The headline fields (activeAgent/activeState/...) mirror the
// most-recently-active agent for the single footer status line. The `message`
// event (author = the agent whose turn finished) is the per-agent done signal.
function initialState(){
  return { agents:{}, roundActive:false,
           activeAgent:null, activeState:null, toolLabel:null,
           startedAt:null, lastDeltaAt:null };
}
function _headline(state, agent){
  var ag = state.agents[agent];
  state.activeAgent = agent;
  state.activeState = ag ? ag.state : null;
  state.toolLabel = ag ? ag.toolLabel : null;
  state.startedAt = ag ? ag.startedAt : null;
  state.lastDeltaAt = ag ? ag.lastDeltaAt : null;
}
function applyEvent(state, evt, now){
  if(!evt || typeof evt !== "object") return state;
  switch(evt.type){
    case "round":
      if(evt.state === "started") state.roundActive = true;
      else if(evt.state === "done" || evt.state === "stopped"){
        // safety net: any turn still open flashes done (normally each agent's
        // own message event already did this)
        for(var n in state.agents){
          var ag2 = state.agents[n];
          if(ag2.state !== "done"){ ag2.state = "done"; ag2.lastDeltaAt = now; }
        }
        state.activeAgent = null; state.activeState = null;
        state.toolLabel = null; state.startedAt = null; state.lastDeltaAt = null;
        state.roundActive = false;
      }
      return state;
    case "turn":
      if(evt.state === "thinking"){
        state.agents[evt.agent] = { state:"thinking", startedAt:now, lastDeltaAt:now, toolLabel:null };
        _headline(state, evt.agent);
      }
      return state;
    case "turn_delta":
      var ag = state.agents[evt.agent];
      if(!ag) ag = state.agents[evt.agent] = { state:"thinking", startedAt:now, lastDeltaAt:now, toolLabel:null };
      if(evt.kind === "reasoning") ag.state = "thinking";
      else if(evt.kind === "tool"){ ag.state = "working";
        ag.toolLabel = String(evt.delta || "").slice(0, 42); }
      else if(evt.kind === "text") ag.state = "typing";
      ag.lastDeltaAt = now;
      _headline(state, evt.agent);
      return state;
    case "message":
      // an agent's turn just finished (the round engine posts its reply) ->
      // brief done-flash, then the agent idles. Per-agent done signal.
      var who = (evt.message && evt.message.author) || evt.agent;
      if(who && state.agents[who] && state.agents[who].state !== "done"){
        state.agents[who].state = "done";
        state.agents[who].lastDeltaAt = now;
        _headline(state, who);
      }
      return state;
    default:
      return state;
  }
}
function isStuck(state, now){
  return !!(state.activeState && state.lastDeltaAt && (now - state.lastDeltaAt) > STUCK_MS);
}"""))

# 3. Slot build: instantiate a per-agent .lamp element + lamps map.
EDITS.append(("slot lamp element",
"""var slots = {}, figs = {};
var anim = {};
AGENTS.forEach(function(name, i){
  var el = document.createElement("div");
  el.className = "slot";
  el.style.left = (i * 16.6667) + "%";
  el.innerHTML = '<div class="flag" title="blocked">&#9873;</div>' +
    '<img class="fig" alt="' + name + '" src="data:image/png;base64,' + FRAMES[name].idle[0] + '">' +
    '<div class="bubble"></div><div class="name"></div>';
  el.querySelector(".name").textContent = name;
  office.appendChild(el);
  slots[name] = el;
  figs[name] = el.querySelector(".fig");
  anim[name] = { set:"idle", idx:0, last:0 };
});""",
"""var slots = {}, figs = {}, lamps = {};
var anim = {};
AGENTS.forEach(function(name, i){
  var el = document.createElement("div");
  el.className = "slot";
  el.style.left = (i * 16.6667) + "%";
  el.innerHTML = '<div class="lamp"><div class="bulb"></div></div>' +
    '<div class="flag" title="blocked">&#9873;</div>' +
    '<img class="fig" alt="' + name + '" src="data:image/png;base64,' + FRAMES[name].idle[0] + '">' +
    '<div class="bubble"></div><div class="name"></div>';
  el.querySelector(".name").textContent = name;
  office.appendChild(el);
  slots[name] = el;
  figs[name] = el.querySelector(".fig");
  lamps[name] = el.querySelector(".lamp");
  anim[name] = { set:"idle", idx:0, last:0 };
});"""))

# 4. stepFrames: per-agent frame-swap + per-agent lamp driving.
#    Self-contained (no helper calls) so the gate's node harness can extract
#    and run it unchanged; `lamps` is guarded for harnesses without the DOM map.
EDITS.append(("stepFrames per-agent + lamp",
"""// ---- frame-swap animation (working vs stuck must be distinguishable) ----
function stepFrames(now){
  AGENTS.forEach(function(name){
    var a = anim[name];
    var active = (state.activeAgent === name);
    var stuck = active && isStuck(state, now);
    var set, ms;
    if(active && !stuck && state.activeState === "typing"){ set = "type"; ms = TYPE_MS; }
    else if(active && !stuck){ set = "idle"; ms = IDLE_MS; }
    else if(active){ ms = 0; } // stuck: freeze on the current frame
    else { set = "idle"; ms = CALM_MS; }
    if(ms === 0) return;
    if(set !== a.set){ a.set = set; a.idx = 0; a.last = now; }
    if(now - a.last >= ms){ a.idx = (a.idx + 1) % FRAMES[name][set].length; a.last = now; }
    var url = "data:image/png;base64," + FRAMES[name][set][a.idx];
    if(figs[name].getAttribute("src") !== url) figs[name].setAttribute("src", url);
  });
}""",
"""// ---- frame-swap + per-agent lamp (working vs stuck must be distinguishable) ----
// Per-agent state: the entry from the per-agent map, falling back to the flat
// headline for legacy single-agent state shapes (the node gate harness).
function stepFrames(now){
  AGENTS.forEach(function(name){
    var a = anim[name];
    var ag = (state.agents && state.agents[name]) || (state.activeAgent === name ? state : null);
    var active = !!(ag && ag.state && ag.state !== "done");
    var stuck = active && !!(ag.lastDeltaAt && (now - ag.lastDeltaAt) > STUCK_MS);
    var set, ms;
    if(active && !stuck && ag.state === "typing"){ set = "type"; ms = TYPE_MS; }
    else if(active && !stuck){ set = "idle"; ms = IDLE_MS; }
    else if(active){ ms = 0; } // stuck: freeze on the current frame
    else { set = "idle"; ms = CALM_MS; }
    if(ms !== 0){
      if(set !== a.set){ a.set = set; a.idx = 0; a.last = now; }
      if(now - a.last >= ms){ a.idx = (a.idx + 1) % FRAMES[name][set].length; a.last = now; }
      var url = "data:image/png;base64," + FRAMES[name][set][a.idx];
      if(figs[name].getAttribute("src") !== url) figs[name].setAttribute("src", url);
    }
    // per-agent lamp keyed off THIS agent's own state — >1 can be lit at once:
    // thinking=warm pulse, working=steady blue, typing=steady purple,
    // stuck=red, done=brief green flash (entry expires after DONE_MS).
    if(typeof lamps !== "undefined" && lamps[name]){
      var cls = "lamp";
      if(stuck) cls += " stuck";
      else if(ag && ag.state === "thinking") cls += " think";
      else if(ag && ag.state === "working") cls += " work";
      else if(ag && ag.state === "typing") cls += " type";
      else if(ag && ag.state === "done") cls += " done";
      if(lamps[name].className !== cls) lamps[name].className = cls;
      if(ag && ag.state === "done" && now - ag.lastDeltaAt > DONE_MS) delete state.agents[name];
    }
  });
}"""))

# 5. render: headline sync + per-agent bubbles + footer count for concurrent agents.
EDITS.append(("render per-agent",
"""function render(now){
  stepFrames(now);
  var stuck = isStuck(state, now);
  AGENTS.forEach(function(name){
    var el = slots[name];
    var on = (state.activeAgent === name);
    el.classList.toggle("on", on);
    var b = el.querySelector(".bubble");
    if(!on){ b.className = "bubble"; b.innerHTML = ""; return; }
    var cls = "bubble";
    var label;
    if(stuck){ cls += " b-stuck"; label = '<span class="st">stuck?</span> no delta ' + fmtElapsed(now - state.lastDeltaAt); }
    else if(state.activeState === "working"){ cls += " b-work";
      label = '<span class="st">&#9881; working</span>' + (state.toolLabel ? ' <span class="tool">' + esc(state.toolLabel) + '</span>' : ''); }
    else if(state.activeState === "typing"){ cls += " b-type"; label = '<span class="st">typing</span> ' + fmtElapsed(now - state.startedAt); }
    else { cls += " b-think"; label = '<span class="st">thinking</span> ' + fmtElapsed(now - state.startedAt); }
    b.className = cls; b.innerHTML = label;
  });
  // footer
  dot.classList.toggle("live", !!state.activeState);
  conn.textContent = "live"; conn.className = "pill live";
  if(state.activeAgent){
    var w = stuck ? "stuck?" : (state.activeState || "active");
    statusEl.innerHTML = "<b>" + esc(state.activeAgent) + "</b> &middot; " + w +
      (state.activeState === "working" && state.toolLabel ? " &middot; " + esc(state.toolLabel) : "") +
      " &middot; " + fmtElapsed(now - (state.startedAt || now));
    clockEl.textContent = "last delta " + fmtElapsed(now - (state.lastDeltaAt || now)) + " ago";
  } else {
    statusEl.innerHTML = state.roundActive ? "round active &middot; waiting for a turn" : "idle &mdash; no active round";
    clockEl.textContent = "";
  }
}""",
"""function render(now){
  stepFrames(now);
  // headline sync: if the headline entry expired (done flash finished), re-point
  // the footer at the newest still-active agent — or clear it. While a done
  // entry is still flashing, the footer keeps echoing "X · done".
  var head = state.agents && state.agents[state.activeAgent];
  if(state.activeAgent && !head){
    var best = null, bestName = null;
    for(var n in state.agents){
      var e = state.agents[n];
      if(e.state && e.state !== "done" && (!best || (e.lastDeltaAt||0) > (best.lastDeltaAt||0))){ best = e; bestName = n; }
    }
    if(bestName){
      state.activeAgent = bestName; state.activeState = best.state;
      state.toolLabel = best.toolLabel; state.startedAt = best.startedAt; state.lastDeltaAt = best.lastDeltaAt;
    } else {
      state.activeAgent = null; state.activeState = null; state.toolLabel = null;
      state.startedAt = null; state.lastDeltaAt = null;
    }
  }
  var nActive = 0;
  if(state.agents) for(var n2 in state.agents){
    var e2 = state.agents[n2];
    if(e2.state && e2.state !== "done") nActive++;
  }
  AGENTS.forEach(function(name){
    var el = slots[name];
    var ag = (state.agents && state.agents[name]) || (state.activeAgent === name ? state : null);
    var on = !!(ag && ag.state);
    el.classList.toggle("on", on);
    var b = el.querySelector(".bubble");
    if(!on){ b.className = "bubble"; b.innerHTML = ""; return; }
    var stuck = !!(ag.state !== "done" && ag.lastDeltaAt && (now - ag.lastDeltaAt) > STUCK_MS);
    var cls = "bubble";
    var label;
    if(stuck){ cls += " b-stuck"; label = '<span class="st">stuck?</span> no delta ' + fmtElapsed(now - ag.lastDeltaAt); }
    else if(ag.state === "working"){ cls += " b-work";
      label = '<span class="st">&#9881; working</span>' + (ag.toolLabel ? ' <span class="tool">' + esc(ag.toolLabel) + '</span>' : ''); }
    else if(ag.state === "typing"){ cls += " b-type"; label = '<span class="st">typing</span> ' + fmtElapsed(now - ag.startedAt); }
    else if(ag.state === "done"){ cls += " b-done"; label = '<span class="st">done</span>'; }
    else { cls += " b-think"; label = '<span class="st">thinking</span> ' + fmtElapsed(now - ag.startedAt); }
    b.className = cls; b.innerHTML = label;
  });
  // footer: headline = most-recently-active agent; show a count when >1 concurrent
  dot.classList.toggle("live", !!state.activeState);
  conn.textContent = "live"; conn.className = "pill live";
  if(state.activeAgent){
    var hag = state.agents[state.activeAgent];
    var hstuck = !!(hag && hag.state !== "done" && hag.lastDeltaAt && (now - hag.lastDeltaAt) > STUCK_MS);
    var w = hstuck ? "stuck?" : (hag ? hag.state : "active");
    var count = nActive > 1 ? "<b>" + nActive + " agents</b> active &middot; " : "";
    statusEl.innerHTML = count + "<b>" + esc(state.activeAgent) + "</b> &middot; " + w +
      (w === "working" && hag && hag.toolLabel ? " &middot; " + esc(hag.toolLabel) : "") +
      " &middot; " + fmtElapsed(now - (hag ? hag.startedAt : now));
    clockEl.textContent = "last delta " + fmtElapsed(now - (hag ? hag.lastDeltaAt : now)) + " ago";
  } else {
    statusEl.innerHTML = state.roundActive ? "round active &middot; waiting for a turn" : "idle &mdash; no active round";
    clockEl.textContent = "";
  }
}"""))

# 6. sceneState: liveness keyed off the per-agent map, not the single headline.
EDITS.append(("sceneState liveness",
"  var live = (state.roundActive || state.activeAgent) ? 0.15 : 0;",
"  var live = (state.roundActive || Object.keys(state.agents).length) ? 0.15 : 0;"))

# Apply with uniqueness assertions.
failed = []
for name, old, new in EDITS:
    c = html.count(old)
    if c != 1:
        failed.append((name, c))
        continue
    html = html.replace(old, new, 1)

if failed:
    for name, c in failed:
        print("ABORT (no write): %s matched %d times (want 1)" % (name, c))
    sys.exit(1)

open(OFFICE, "w").write(html)
print("office.html: %d -> %d bytes, %d edits applied" % (len(orig), len(html), len(EDITS)))
for name, _, _ in EDITS:
    print("  ok:", name)
