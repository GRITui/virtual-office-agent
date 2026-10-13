#!/usr/bin/env python3
"""Re-runnable gate: wall board stage labels (todo/ready/running/blocked/done).

Right-layer checks:
  1. Served /office == disk office.html (no drift) + label code present.
  2. The ACTUAL shipped board JS (extracted from the served HTML, not a
     mirror) run in Node with a canvas stub: drawBoard must paint
       - 5 header bars (COLX[i], 6, 18, 3) in COLHEAD colors
       - 5 stage labels (3px pixel font, COLHEAD color, centered in the
         18px column, y=1..3) — exact ink match with the glyph bitmaps
         (flat + crisp + no blur/AA), zero stray ink in the label band
       - counts at y=11 matching the DIGITS bitmaps (rgb(230,233,239))
       - card bars (cx0, 19+k*4, 18, 3) rgb(48,55,68) for k<min(n,3)
       - erase rect (2,6,117,25) in board bg; no multi-pixel fill over y<6
         (labels sit on the baked flat-dark band, never erased)
  3. Headless-Chrome CDP render of the LIVE /office at 1280x800 and
     390x844 (mobile):
       - all 5 labels: exact ink + exact COLHEAD color on the real canvas
       - counts: ink matches the live /kanban values (data path unchanged)
       - no scene shift: #env fills the .office box, #boardLive rect ==
         18.75% / 8.3333% / 62.99% / 19.6429% of the scene box (+-1.5px),
         canvas internal size still 121x33
       - screenshot evidence of the board region at both viewports
"""
import json, os, re, socket, subprocess, sys, time, urllib.request, urllib.error, base64
import asyncio

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OFFICE = os.path.join(BASE_DIR, "office.html")
LIVE = "http://127.0.0.1:8651"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FAIL = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail and not cond else ""))
    if not cond:
        FAIL.append(name)

# ---------- 1. served == disk ----------
disk = open(OFFICE, "rb").read()
with urllib.request.urlopen(LIVE + "/office", timeout=5) as r:
    served = r.read()
check("served /office == disk office.html (no drift)", served == disk,
      "served %d bytes vs disk %d" % (len(served), len(disk)))
s = served.decode()
for needle, why in [
    ("var LABELS", "LABELS table"),
    ("var GLYPHS", "GLYPHS table"),
    ("function drawLabel", "drawLabel"),
    ("drawLabel(c, lab,", "drawBoard draws the label"),
    ("E:[\"110\",\"100\",\"110\"]", "E glyph (DONE label)"),
]:
    check("served: " + why, needle in s)

# ---------- 2. shipped board JS in node with a canvas stub ----------
m = re.search(r"var COLX = \[[^\]]+\];.*?function drawBoard\(counts\)\{.*?\n\}", s, re.S)
check("extracted shipped board JS (COLX..drawBoard)", bool(m))
if m:
    block = m.group(0)
    harness = block + """
// ---- harness ----
var rects = [], grid = {}, cur = null;
var ctx = {
  clearRect: function(x,y,w,h){ rects.push({x:x,y:y,w:w,h:h,style:'clear'}); },
  fillRect: function(x,y,w,h){
    rects.push({x:x,y:y,w:w,h:h,style:cur});
    for(var yy=y; yy<y+h; yy++) for(var xx=x; xx<x+w; xx++) grid[xx+','+yy] = cur;
  },
  set fillStyle(v){ cur = v; },
  get fillStyle(){ return cur; }
};
var document = { getElementById: function(){ return { getContext: function(){ return ctx; } }; } };
function rgb(s){ var mm = s.match(/(\\d+),\\s*(\\d+),\\s*(\\d+)/); return [ +mm[1], +mm[2], +mm[3] ]; }
function eq(a,b){ return a[0]===b[0] && a[1]===b[1] && a[2]===b[2]; }

var COUNTS = { todo:1, ready:2, running:3, blocked:0, done:4 };
drawBoard(COUNTS);

var fails = [];
function bad(msg){ fails.push(msg); }

// erase rect
var erase = rects.filter(function(r){ return r.x===2 && r.y===6 && r.w===117 && r.h===25; });
if(erase.length !== 1 || !eq(rgb(erase[0].style), [12,15,20])) bad("erase rect (2,6,117,25) rgb(12,15,20) missing");
// no multi-pixel fill over y<6 (label band is baked-scene territory)
rects.forEach(function(r){ if(r.style!=='clear' && r.y < 6 && r.h > 1) bad("fill over label band: "+JSON.stringify(r)); });

ORDER.forEach(function(key, i){
  var cx0 = COLX[i], target = rgb(COLHEAD[key]);
  // header bar
  var bar = rects.filter(function(r){ return r.x===cx0 && r.y===6 && r.w===18 && r.h===3; });
  if(bar.length !== 1 || !eq(rgb(bar[0].style), target)) bad(key+": header bar missing/wrong color");
  // label: exact ink from the shipped bitmaps
  var lab = LABELS[key], lw = 3*lab.length + (lab.length-1), lx = cx0 + ((18-lw)>>1);
  if(lx < cx0 || lx + lw > cx0 + 18) bad(key+": label width "+lw+" escapes the 18px column");
  var exp = {};
  for(var ch=0; ch<lab.length; ch++){
    var g = GLYPHS[lab[ch]];
    if(!g) { bad(key+": missing glyph for '"+lab[ch]+"'"); continue; }
    for(var r=0;r<3;r++) for(var cc=0;cc<3;cc++) if(g[r][cc]==='1') exp[(lx+ch*4+cc)+','+(1+r)] = 1;
  }
  var ink = 0, badc = 0, extra = 0;
  for(var y=0; y<5; y++) for(var x=cx0; x<cx0+18; x++){
    var p = grid[x+','+y];
    if(p === undefined) continue;
    if(exp[x+','+y]){ ink++; if(!eq(rgb(p), target)) badc++; }
    else extra++;
  }
  if(ink !== Object.keys(exp).length) bad(key+": label ink "+ink+" != expected "+Object.keys(exp).length);
  if(badc > 0) bad(key+": label ink wrong color x"+badc);
  if(extra > 0) bad(key+": stray ink in label band x"+extra);
  // counts: exact DIGITS ink at y=11
  var n = COUNTS[key], str = String(n), cw = 3*str.length + (str.length-1), x0 = (cx0+9) - (cw>>1);
  var cexp = {};
  for(var d=0; d<str.length; d++){
    var dg = DIGITS[str[d]];
    for(var r=0;r<5;r++) for(var cc=0;cc<3;cc++) if(dg[r][cc]==='1') cexp[(x0+d*4+cc)+','+(11+r)] = 1;
  }
  var cink = 0, cbad = 0, cextra = 0;
  var BG = [12,15,20]; // board bg (erase fill) — background, not ink
  for(var y=10; y<17; y++) for(var x=cx0; x<cx0+18; x++){
    var p = grid[x+','+y];
    if(p === undefined) continue;
    if(cexp[x+','+y]){ cink++; if(!eq(rgb(p), [230,233,239])) cbad++; }
    else if(!eq(rgb(p), BG)) cextra++;
  }
  if(cink !== Object.keys(cexp).length) bad(key+": count ink "+cink+" != expected "+Object.keys(cexp).length+" for "+n);
  if(cbad > 0) bad(key+": count ink wrong color x"+cbad);
  if(cextra > 0) bad(key+": stray ink in count band x"+cextra);
  // card bars
  var card = rects.filter(function(r){ return r.x===cx0 && r.w===18 && r.h===3 && r.y>=19 && r.y<30; });
  var got = card.map(function(r){ return r.y; }).sort(function(a,b){ return a-b; });
  var want = [];
  for(var k=0; k<Math.min(n,3); k++) want.push(19 + k*4);
  if(JSON.stringify(got) !== JSON.stringify(want)) bad(key+": card bars at "+JSON.stringify(got)+" != "+JSON.stringify(want));
  card.forEach(function(r){ if(!eq(rgb(r.style), [48,55,68])) bad(key+": card bar wrong color"); });
});
if(fails.length) { console.log("BOARD_FAIL: " + fails.join("; ")); process.exit(1); }
console.log("BOARD_OK");
"""
    with open("/tmp/board_labels_harness.js", "w") as f:
        f.write(harness)
    r = subprocess.run(["node", "/tmp/board_labels_harness.js"], capture_output=True, text=True)
    check("node: shipped drawBoard — labels exact ink/color, counts, cards, erase",
          r.returncode == 0 and "BOARD_OK" in r.stdout, (r.stdout + r.stderr).strip())

# ---------- 3. live CDP render at 1280 + 390 ----------
cfg = json.load(open(os.path.join(BASE_DIR, "config.json")))
TOKEN = cfg["auth_token"]

def kanban_counts():
    req = urllib.request.Request(LIVE + "/kanban", headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.load(r)
    counts = {"todo": 0, "ready": 0, "running": 0, "blocked": 0, "done": 0}
    for t in d.get("tasks", []):
        if t.get("status") in counts:
            counts[t["status"]] += 1
    if d.get("status_counts"):
        for k in counts:
            if d["status_counts"].get(k) is not None:
                counts[k] = d["status_counts"][k]
    return counts

MEASURE_JS = r"""
(function(LIVE){
  var cv = document.getElementById('boardLive');
  var c = cv.getContext('2d');
  var W = cv.width, H = cv.height;
  var img = c.getImageData(0,0,W,H).data;
  function px(x,y){ var i=(y*W+x)*4; return [img[i],img[i+1],img[i+2],img[i+3]]; }
  function rgb(s){ var m=s.match(/(\d+),\s*(\d+),\s*(\d+)/); return [ +m[1],+m[2],+m[3] ]; }
  function eq(a,b){ return a[0]===b[0]&&a[1]===b[1]&&a[2]===b[2]; }
  function rect(el){ var b=el.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; }
  var out = { canvas:{w:W,h:H},
    office:rect(document.querySelector('.office')),
    env:rect(document.getElementById('env')),
    envNat:{w:document.getElementById('env').naturalWidth,h:document.getElementById('env').naturalHeight},
    boardLive:rect(cv), cols:{} };
  ORDER.forEach(function(key,i){
    var cx0 = COLX[i], target = rgb(COLHEAD[key]);
    var lab = LABELS[key], lw = 3*lab.length + (lab.length-1), lx = cx0 + ((18-lw)>>1);
    var exp = {};
    for(var ch=0; ch<lab.length; ch++){
      var g = GLYPHS[lab[ch]];
      if(!g) { out.cols[key] = {glyphMissing: lab[ch]}; return; }
      for(var r=0;r<3;r++) for(var cc=0;cc<3;cc++) if(g[r][cc]==='1') exp[(lx+ch*4+cc)+'|'+(1+r)] = 1;
    }
    var labelInk=0, labelBad=0, extra=0;
    for(var y=0;y<5;y++) for(var x=cx0;x<cx0+18;x++){
      var p = px(x,y); if(p[3]===0) continue;
      if(exp[x+'|'+y]){ labelInk++; if(!eq(p,target)) labelBad++; }
      else extra++;
    }
    var n = LIVE[key], str = String(n), cw = 3*str.length + (str.length-1), x0 = (cx0+9) - (cw>>1);
    var cexp = {};
    for(var d=0; d<str.length; d++){
      var dg = DIGITS[str[d]];
      for(var r=0;r<5;r++) for(var cc=0;cc<3;cc++) if(dg[r][cc]==='1') cexp[(x0+d*4+cc)+'|'+(11+r)] = 1;
    }
    var countInk=0, countBad=0, cextra=0;
    var INK = rgb('rgb(230,233,239)'), BG = [12,15,20]; // BG = erase fill, not ink
    for(var y=10;y<17;y++) for(var x=cx0;x<cx0+18;x++){
      var p = px(x,y); if(p[3]===0) continue;
      if(cexp[x+'|'+y]){ countInk++; if(!eq(p,INK)) countBad++; }
      else if(!eq(p,BG)) cextra++;
    }
    var barOk = true;
    for(var y=6;y<9;y++) for(var x=cx0;x<cx0+18;x++){ var p=px(x,y); if(p[3]===0 || !eq(p,target)) barOk=false; }
    var cardOk = true, CARD = rgb('rgb(48,55,68)');
    for(var k=0;k<3;k++) for(var y=19+k*4;y<22+k*4;y++) for(var x=cx0;x<cx0+18;x++){
      var p=px(x,y), should = k < Math.min(n,3);
      if(should){ if(p[3]===0 || !eq(p,CARD)) cardOk=false; }
      else { if(p[3]!==0 && !eq(p,BG)) cardOk=false; }
    }
    out.cols[key] = { labelInk:labelInk, expLabelInk:Object.keys(exp).length, labelBad:labelBad,
      extraLabelInk:extra, countInk:countInk, expCountInk:Object.keys(cexp).length,
      countBad:countBad, extraCountInk:cextra, barOk:barOk, cardOk:cardOk,
      labelX:lx, labelW:lw, withinCol: lx>=cx0 && lx+lw<=cx0+18, n:n };
  });
  return out;
})
"""

INK_JS = r"""
(function(){
  var cv = document.getElementById('boardLive');
  var c = cv.getContext('2d');
  var W = cv.width;
  var img = c.getImageData(0,0,W,cv.height).data;
  var n = 0;
  for(var y=1;y<4;y++) for(var x=0;x<W;x++){ var i=(y*W+x)*4; if(img[i+3]!==0) n++; }
  return n;
})()
"""

async def cdp_session(port, live_counts):
    import websockets
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=5))
    page = next(t for t in targets if t["type"] == "page")
    results = {}
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        _id = 0
        async def send(method, **params):
            nonlocal _id
            _id += 1
            mid = _id
            await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(method + " -> " + json.dumps(msg["error"]))
                    return msg.get("result", {})
        await send("Page.enable")
        await send("Runtime.enable")
        seed = ("try{localStorage.setItem('tr_token', %s);"
                "window.prompt=function(){return %s;};window.alert=function(){};}catch(e){}") % (
            json.dumps(TOKEN), json.dumps(TOKEN))
        await send("Page.addScriptToEvaluateOnNewDocument", source=seed)

        async def shoot(w, h, mobile, shot_path):
            await send("Emulation.setDeviceMetricsOverride", width=w, height=h,
                       deviceScaleFactor=1, mobile=mobile)
            await send("Page.navigate", url=LIVE + "/office")
            await asyncio.sleep(3.5)
            # wait for the board to draw (loadKanban fetch -> drawBoard)
            for _ in range(30):
                res = await send("Runtime.evaluate", expression=INK_JS, returnByValue=True)
                if res.get("result", {}).get("value", 0) > 0:
                    break
                await asyncio.sleep(0.5)
            res = await send("Runtime.evaluate",
                             expression=MEASURE_JS + "(" + json.dumps(live_counts) + ")",
                             returnByValue=True)
            val = res.get("result", {}).get("value")
            if val is None:
                raise RuntimeError("measure returned null: " + json.dumps(res))
            # evidence screenshot of the board region
            b = val["boardLive"]
            clip = {"x": max(0, b["x"] - 10), "y": max(0, b["y"] - 10),
                    "width": min(w - max(0, b["x"] - 10), b["w"] + 20),
                    "height": min(h - max(0, b["y"] - 10), b["h"] + 20), "scale": 1}
            shot = await send("Page.captureScreenshot", format="png", clip=clip)
            open(shot_path, "wb").write(base64.b64decode(shot["data"]))
            return val
        results["desktop"] = await shoot(1280, 800, False,
                                         os.path.join(BASE_DIR, "verify_board_labels_desktop.png"))
        results["mobile"] = await shoot(390, 844, True,
                                        os.path.join(BASE_DIR, "verify_board_labels_mobile.png"))
    return results

def free_port():
    for p in (9345, 9346, 9347, 9348):
        sk = socket.socket()
        try:
            sk.bind(("127.0.0.1", p))
            sk.close()
            return p
        except OSError:
            continue
    raise RuntimeError("no free port")

def assert_viewport(name, v, live_counts):
    check(name + ": canvas internal 121x33 (unchanged)",
          v["canvas"]["w"] == 121 and v["canvas"]["h"] == 33, str(v["canvas"]))
    off = v["office"]
    bl = v["boardLive"]
    env = v["env"]
    check(name + ": no scene shift — #env fills scene box",
          abs(env["x"] - off["x"]) <= 1.5 and abs(env["y"] - off["y"]) <= 1.5
          and abs(env["w"] - off["w"]) <= 1.5 and abs(env["h"] - off["h"]) <= 1.5,
          "env=%s office=%s" % (env, off))
    check(name + ": no scene shift — #boardLive at 18.75%/8.3333% 62.99%x19.6429%",
          abs(bl["x"] - off["x"] - 0.1875 * off["w"]) <= 1.5
          and abs(bl["y"] - off["y"] - 0.083333 * off["h"]) <= 1.5
          and abs(bl["w"] - 0.6299 * off["w"]) <= 1.5
          and abs(bl["h"] - 0.196429 * off["h"]) <= 1.5,
          "boardLive=%s office=%s" % (bl, off))
    check(name + ": env natural 192x168", v["envNat"] == {"w": 192, "h": 168}, str(v["envNat"]))
    for key in ("todo", "ready", "running", "blocked", "done"):
        col = v["cols"].get(key)
        if not col or "glyphMissing" in col:
            check(name + ": label " + key, False, str(col))
            continue
        check(name + ": label %s legible (exact ink %d, COLHEAD color, in column, crisp)"
              % (key, col["expLabelInk"]),
              col["labelInk"] == col["expLabelInk"] and col["expLabelInk"] > 0
              and col["labelBad"] == 0 and col["extraLabelInk"] == 0 and col["withinCol"],
              str(col))
        check(name + ": count %s unchanged (ink %d == live /kanban %d)" % (key, col["expCountInk"], col["n"]),
              col["countInk"] == col["expCountInk"] and col["countBad"] == 0
              and col["extraCountInk"] == 0 and col["n"] == live_counts[key],
              str(col))
        check(name + ": header bar + card bars %s" % key, col["barOk"] and col["cardOk"], str(col))

def main():
    live_counts = kanban_counts()
    print("live /kanban counts: %s" % live_counts)
    port = free_port()
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", "--remote-debugging-port=%d" % port,
         "--no-first-run", "--no-default-browser-check", "--disable-gpu",
         "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("headless chrome did not come up")
        results = asyncio.run(cdp_session(port, live_counts))
        assert_viewport("desktop 1280", results["desktop"], live_counts)
        assert_viewport("mobile 390", results["mobile"], live_counts)
        check("screenshot evidence written (desktop + mobile)",
              os.path.exists(os.path.join(BASE_DIR, "verify_board_labels_desktop.png"))
              and os.path.exists(os.path.join(BASE_DIR, "verify_board_labels_mobile.png")))
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()

    print("\n%d failed" % len(FAIL) if FAIL else "\nALL GREEN (wall board labels gate)")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
