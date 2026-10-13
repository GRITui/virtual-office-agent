#!/usr/bin/env python3
"""Verify the drill-in panel (t_bb332f8e) on the SERVED office.html.
Checks: (1) served==disk + panel markers present, (2) Node: stream buffer caps,
open/close panel state, panel HTML builders (agent: live stream + current card +
stuck escape hatch; card: detail + latest run + live cross-ref), and the unified
click handler routing (agent -> agent panel, card -> card panel, stage -> board)."""
import json, re, subprocess, sys, urllib.request, os

TOKEN = json.load(open(os.path.expanduser("~/.hermes/team-room/config.json")))["auth_token"]
req = urllib.request.Request("http://localhost:8651/office",
                             headers={"Authorization": f"Bearer {TOKEN}"})
served = urllib.request.urlopen(req).read().decode()
disk = open(os.path.expanduser("~/.hermes/team-room/office.html")).read()

for marker in ["id=\"drillPanel\"", "openAgentPanel", "openCardPanel",
               "live turn stream", "what you can do", "function renderPanel"]:
    assert marker in served, f"marker not served: {marker}"
assert served == disk, f"served != disk ({len(served)} vs {len(disk)})"
print(f"OK served==disk, {len(served)}b, panel markers present")

def grab(pat):
    m = re.search(pat, served)
    assert m, f"pattern not found: {pat}"
    return m.group(1)

agents = grab(r'var AGENTS = (\[[^\]]+\]);')
colx = grab(r'var COLX = (\[[^\]]+\]);')
order = grab(r'var ORDER = (\[[^\]]+\]);')
stuck_ms = grab(r'var STUCK_MS = (\d+);')
# DESK + deskOrigin: the pixel-art-designer's 2x3 grid geometry (shared file;
# the board block's HITREGIONS IIFE calls deskOrigin, so it must be in scope)
desk = served[served.index("var DESK = "):served.index("// ---- pure state machine")]
esc_fmt = served[served.index("function esc(s)"):served.index("function render(now)")]
is_stuck = served[served.index("function isStuck"):served.index("// expose for the headless verify gate (node)")]
board = served[served.index("var BOARD_X"):served.index("// ---- pin-pop")]
panel = served[served.index("// ---- drill-in panel (t_bb332f8e)"):served.index("// ---- day/night ambient cycle")]
click = served[served.index("// ---- drill-in (t_bb332f8e): click an agent slot"):served.index("// ---- render loop")]

js = r"""
// ---- DOM stubs ----
function el(name){
  var classes = {};
  return {
    _name: name,
    classList: {
      add: function(c){ classes[c]=1; },
      remove: function(c){ delete classes[c]; },
      contains: function(c){ return !!classes[c]; },
      _has: function(c){ return !!classes[c]; }
    },
    _classes: classes,
    setAttribute: function(k,v){ this._attr = this._attr||{}; this._attr[k]=v; },
    addEventListener: function(t,fn){ this._listeners = this._listeners||{}; (this._listeners[t]=this._listeners[t]||[]).push(fn); },
    textContent: "", innerHTML: "",
    querySelector: function(){ return { scrollTop: 0 }; },
    querySelectorAll: function(){ return { forEach: function(){} }; },
    getBoundingClientRect: function(){ return { left:0, top:0, width:192, height:168 }; }
  };
}
var els = {};
["drillPanel","drillTitle","drillChip","drillBody","drillClose","office"].forEach(function(n){ els[n]=el(n); });
var office = els.office;
var document = {
  getElementById: function(n){ return els[n]; },
  addEventListener: function(){},
  body: el("body")
};
var window = { parent: null };
window.parent = window;   // standalone: openBoardOverlay -> location.href
var location = { href: "" };
// test fixtures
var state = { agents: {
  "product-owner": { state:"thinking", startedAt: Date.now()-5000, lastDeltaAt: Date.now()-5000, toolLabel:"terminal", card_id:"t_bb332f8e" },
  "ux-ui":         { state:"working",  startedAt: Date.now()-90000, lastDeltaAt: Date.now()-90000, toolLabel:"read_file", card_id:"t_stuck_1" }
}};
var lastKanbanTasks = [
  { id:"t_bb332f8e", title:"Drill-in panel", status:"running", assignee:"ux-ui", priority:5,
    created_at: Date.now()-3600000, parents:[], children:[],
    latest_run:{ outcome:"running", ended_at:null, summary:"in flight" } },
  { id:"t_stuck_1", title:"Stuck card", status:"running", assignee:"ux-ui", priority:3,
    created_at: Date.now()-7200000, block_kind:"needs_input", consecutive_failures:2,
    parents:["t_bb332f8e"], children:[], latest_run:{ outcome:"blocked", ended_at:Date.now()-60000, summary:"waiting on input" } }
];
var slots = {};
var lastBoardCols = { todo:["t_todo_1","t_todo_2"], ready:["t_ready_1"],
                      running:["t_run_1"], blocked:[], done:["t_done_1"] };
""" + esc_fmt + "\n" + is_stuck + "\n" + \
    "var AGENTS = " + agents + ";\nvar COLX = " + colx + ";\nvar ORDER = " + order + ";\nvar STUCK_MS = " + stuck_ms + ";\n" + \
    desk + "\n" + board + "\n" + panel + "\n" + click + r"""
var out = {};
// 1. stream buffer: tools cap at STREAM_TOOLS_MAX, reasoning/text accumulate
for(var i=0;i<60;i++) pushStream("ux-ui","tool","d"+i);
pushStream("ux-ui","reasoning","hello ");
pushStream("ux-ui","reasoning","world");
pushStream("ux-ui","text","resp");
out.tools_len = turnStream["ux-ui"].tools.length;
out.tools_capped = out.tools_len === STREAM_TOOLS_MAX;
out.tools_newest = turnStream["ux-ui"].tools[turnStream["ux-ui"].tools.length-1].label;   // d59
out.reasoning = turnStream["ux-ui"].reasoning;   // "hello world"
out.text = turnStream["ux-ui"].text;             // "resp"
// 2. open agent panel (active agent) -> open, mode agent, html has live stream + current card
openAgentPanel("product-owner");
out.agent_open = drill.open; out.agent_mode = drill.mode; out.agent_name = drill.agent;
out.agent_panel_open_cls = els.drillPanel._classes["open"] === 1;
out.agent_html_stream = els.drillBody.innerHTML.indexOf("live turn stream") >= 0;
out.agent_html_card = els.drillBody.innerHTML.indexOf("t_bb332f8e") >= 0;
out.agent_html_now = els.drillBody.innerHTML.indexOf(">now<") >= 0;
// 3. open agent panel (STUCK agent) -> why-stuck + what you can do
openAgentPanel("ux-ui");
out.stuck_html = els.drillBody.innerHTML.indexOf("why stuck") >= 0;
out.stuck_actions = els.drillBody.innerHTML.indexOf("what you can do") >= 0;
out.stuck_chip = els.drillChip.innerHTML.indexOf("stuck?") >= 0;
// 4. open card panel -> detail + latest run + live cross-ref
openCardPanel("t_stuck_1");
out.card_mode = drill.mode; out.card_id = drill.card_id;
out.card_html_detail = els.drillBody.innerHTML.indexOf(">detail<") >= 0;
out.card_html_block = els.drillBody.innerHTML.indexOf("needs_input") >= 0;
out.card_html_run = els.drillBody.innerHTML.indexOf("blocked") >= 0;
out.card_html_live = els.drillBody.innerHTML.indexOf("working on it") >= 0;
out.card_title = els.drillTitle.textContent;
// 5. close panel
closePanel();
out.closed = !drill.open && els.drillPanel._classes["open"] === undefined;
// 6. click handler routing (office.getBoundingClientRect 192x168 -> clientX/Y == scene coords)
var clickFn = office._listeners["click"][0];
function clickAt(x,y){ clickAt._loc=""; clickAt._pm=null;
  location.href=""; window.__pm=null;
  clickAt._called=[];
  clickFn({ clientX:x, clientY:y });
}
// agent center (product-owner) — region center (geometry-agnostic: 1x6 or 2x3)
var a = HITREGIONS.agents["product-owner"];
clickAt(a.x + a.w/2, a.y + a.h/2);
out.click_agent = drill.mode === "agent" && drill.agent === "product-owner";
// card center: pick a card rect in a stage that has a card at that row
var cardRect = null, expectCardId = null;
HITREGIONS.cards.forEach(function(r){
  if(r.kind !== "card") return;
  var id = (lastBoardCols[r.stage] && lastBoardCols[r.stage][r.row]) || null;
  if(id && !cardRect){ cardRect = r; expectCardId = id; }
});
if(cardRect){
  clickAt(cardRect.x + cardRect.w/2, cardRect.y + cardRect.h/2);
  out.click_card = drill.mode === "card" && drill.card_id === expectCardId;
} else { out.click_card = "no card with id"; }
// stage center (top strip) -> board overlay
var stageRect = HITREGIONS.cards.filter(function(r){ return r.kind==="stage"; })[0];
clickAt(stageRect.x + stageRect.w/2, stageRect.y + 2);
out.click_stage_board = location.href === "/board";
// out-of-scene click -> early return (no open). agent regions cover the whole
// scene (y 0..168), so the only "miss" is outside the 192x168 scene.
closePanel();
clickAt(200, 0);   // clientX=200 -> scene x=200 (>=192) -> handler returns early
out.click_miss_closed = !drill.open;
console.log(JSON.stringify(out));
process.exit(0);   // unblock: the panel block's setInterval would otherwise keep node alive
"""
r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
if r.returncode != 0:
    print("NODE FAIL:\n", r.stderr); sys.exit(1)
res = json.loads(r.stdout)
ok = (res["tools_capped"] and res["tools_newest"] == "d59"
      and res["reasoning"] == "hello world" and res["text"] == "resp"
      and res["agent_open"] and res["agent_mode"] == "agent" and res["agent_panel_open_cls"]
      and res["agent_html_stream"] and res["agent_html_card"] and res["agent_html_now"]
      and res["stuck_html"] and res["stuck_actions"] and res["stuck_chip"]
      and res["card_mode"] == "card" and res["card_html_detail"] and res["card_html_block"]
      and res["card_html_run"] and res["card_html_live"] and res["card_title"] == "Stuck card"
      and res["closed"] and res["click_agent"] and res["click_card"] is True
      and res["click_stage_board"] and res["click_miss_closed"])
print(json.dumps(res, indent=1))
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
