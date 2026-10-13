#!/usr/bin/env python3
"""Verify drill-in hit regions (t_7049c92e) on the SERVED office.html.
Checks: (1) served==disk + block present, (2) Node: coverage, no overlaps,
center-click resolution for every rect, (3) cardIdAt mapping."""
import json, re, subprocess, sys, urllib.request, os

TOKEN = json.load(open(os.path.expanduser("~/.hermes/team-room/config.json")))["auth_token"]
req = urllib.request.Request("http://localhost:8651/office",
                             headers={"Authorization": f"Bearer {TOKEN}"})
served = urllib.request.urlopen(req).read().decode()
disk = open(os.path.expanduser("~/.hermes/team-room/office.html")).read()

assert "HITREGIONS" in served, "block not served"
assert served == disk, f"served != disk ({len(served)} vs {len(disk)})"
print(f"OK served==disk, {len(served)}b, HITREGIONS present")

# extract constants + block from the SERVED source
def grab(pat):
    m = re.search(pat, served)
    assert m, f"pattern not found: {pat}"
    return m.group(1)

agents = grab(r'var AGENTS = (\[[^\]]+\]);')
colx = grab(r'var COLX = (\[[^\]]+\]);')
order = grab(r'var ORDER = (\[[^\]]+\]);')
block = served[served.index("var BOARD_X"):served.index("// ---- pin-pop")]

js = f"""
var AGENTS = {agents};
var COLX = {colx};
var ORDER = {order};
var lastBoardCols = {{ todo:["t_todo_1","t_todo_2","t_todo_3","t_todo_4"],
                      ready:["t_ready_1"], running:["t_run_1","t_run_2"],
                      blocked:[], done:["t_done_1","t_done_2","t_done_3","t_done_4","t_done_5"] }};
{block}
var out = {{}};
// 1. coverage: 6 agent rects + 5 stage + 15 card
out.n_agents = Object.keys(HITREGIONS.agents).length;
out.n_stage = HITREGIONS.cards.filter(r=>r.kind==="stage").length;
out.n_card = HITREGIONS.cards.filter(r=>r.kind==="card").length;
// 2. no overlaps (pairwise, strict interiors)
function ov(a,b){{ return a.x < b.x+b.w && b.x < a.x+a.w && a.y < b.y+b.h && b.y < a.y+a.h; }}
var all = [];
Object.keys(HITREGIONS.agents).forEach(n=>all.push(HITREGIONS.agents[n]));
HITREGIONS.cards.forEach(r=>all.push(r));
var overlaps = [];
for(var i=0;i<all.length;i++)for(var j=i+1;j<all.length;j++)
  if(ov(all[i],all[j])) overlaps.push([i,j,all[i].kind,all[j].kind]);
// same-kind overlaps are the real bug (cross-kind nesting is by design:
// card inside stage inside slot). Count same-kind only.
out.overlaps_same_kind = overlaps.filter(o=>o[2]===o[3]).length;
out.overlaps_cross_kind = overlaps.length - out.overlaps_same_kind;
// 3. center-click resolution
var fails = [];
Object.keys(HITREGIONS.agents).forEach(n=>{{
  var a = HITREGIONS.agents[n];
  // click at the agent's BODY center (y=104, below the board) -> agent
  var r = hitTest(a.x + a.w/2, 104);
  if(!r || r.kind!=="agent" || r.agent!==n) fails.push("agent "+n+": "+JSON.stringify(r));
}});
HITREGIONS.cards.forEach(r=>{{
  if(r.kind!=="card") return;
  var hit = hitTest(r.x + r.w/2, r.y + r.h/2);
  if(!hit || hit.kind!=="card" || hit.stage!==r.stage || hit.row!==r.row)
    fails.push("card "+r.stage+" r"+r.row+": "+JSON.stringify(hit));
  else {{
    var expect = lastBoardCols[r.stage][r.row] || null; // rows past the count draw no bar
    if(hit.card_id !== expect) fails.push("card_id "+r.stage+" r"+r.row+": "+hit.card_id);
  }}
}});
out.center_fails = fails;
// 4. stage-cell centers resolve to stage (not clobbered by card bars)
HITREGIONS.cards.filter(r=>r.kind==="stage").forEach(r=>{{
  var hit = hitTest(r.x + r.w/2, r.y + 2);  // top strip, above first bar row
  if(!hit || hit.kind!=="stage" || hit.stage!==r.stage)
    fails.push("stage "+r.stage+": "+JSON.stringify(hit));
}});
out.center_fails = fails;
// 5. out-of-scene click -> null
out.miss = hitTest(192, 0) === null; // x=192 is outside the 192-wide scene (slots cover 0..191)
console.log(JSON.stringify(out));
"""
r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
if r.returncode != 0:
    print("NODE FAIL:\n", r.stderr); sys.exit(1)
res = json.loads(r.stdout)
ok = (res["n_agents"] == 6 and res["n_stage"] == 5 and res["n_card"] == 15
      and res["overlaps_same_kind"] == 0 and not res["center_fails"] and res["miss"])
print(json.dumps(res, indent=1))
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
