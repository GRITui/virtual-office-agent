import asyncio, json, os, sys, urllib.request
import websockets

TOKEN = json.load(open(os.path.expanduser("~/.hermes/team-room/config.json")))["auth_token"]
BASE = "http://127.0.0.1:8651"

MEASURE_JS = r"""
(function(){
  function r(el){ if(!el) return null; var b=el.getBoundingClientRect(); return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}; }
  var out = {};
  out.viewport = {w:innerWidth, h:innerHeight, scrollW:document.documentElement.scrollWidth, clientW:document.documentElement.clientWidth, overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth+1};
  var op = document.querySelector('.office-pane');
  var cp = document.querySelector('.chat-pane');
  out.officePane = r(op); out.chatPane = r(cp);
  var row2 = document.querySelector('.row2');
  out.row2 = r(row2);
  if(row2){ out.row2children = Array.prototype.map.call(row2.children, function(c){ return {tag:c.tagName, id:c.id, cls:(c.className||'').toString().slice(0,30), rect:r(c)}; }); }
  var input = document.getElementById('input'); out.input = r(input);
  var pill = document.getElementById('pill'); out.pill = r(pill); out.pillText = pill ? pill.textContent : null;
  var humanWho = document.querySelector('.msg.human .who');
  out.humanWhoColor = humanWho ? getComputedStyle(humanWho).color : null;
  var mj = document.getElementById('mentionJump');
  out.mentionJump = mj ? {present:true, text:mj.textContent.trim(), has:mj.classList.contains('has'), count:(document.getElementById('mentionCount')||{}).textContent} : {present:false};
  out.mentionsMeCount = document.querySelectorAll('.msg.mentions-me').length;
  out.msgCount = document.querySelectorAll('.msg').length;
  var fr = document.querySelector('.office-pane iframe') || document.querySelector('iframe');
  if(fr && fr.contentDocument){
    var d = fr.contentDocument;
    out.officeScene = r(d.querySelector('.office'));
    var env = d.querySelector('#env');
    out.env = env ? {rect:r(env), natW:env.naturalWidth, natH:env.naturalHeight, dispW:Math.round(env.getBoundingClientRect().width)} : null;
    var boardLive = d.querySelector('#boardLive');
    out.boardLive = boardLive ? {rect:r(boardLive), w:boardLive.width, h:boardLive.height} : null;
    var boardCta = d.querySelector('#boardCta');
    out.boardCta = boardCta ? {rect:r(boardCta), display:getComputedStyle(boardCta).display} : null;
    var figs = d.querySelectorAll('.fig');
    out.figCount = figs.length;
    out.figRects = Array.prototype.map.call(figs, function(f){ return r(f); });
    var status = d.querySelector('#status');
    out.officeStatus = status ? status.textContent.trim() : null;
  } else {
    out.officeIframe = {present: !!fr, contentDoc: !!(fr && fr.contentDocument)};
  }
  return out;
})()
"""

async def main():
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9334/json"))
    page = next(t for t in targets if t["type"] == "page")
    ws_url = page["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=None) as ws:
        _id = 0
        async def send(method, **params):
            nonlocal _id
            _id += 1; mid = _id
            await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg: raise RuntimeError(method + " -> " + json.dumps(msg["error"]))
                    return msg.get("result", {})
        await send("Page.enable"); await send("Runtime.enable")
        seed = "try{localStorage.setItem('tr_token', %s);window.prompt=function(){return %s;};window.alert=function(){};}catch(e){}" % (json.dumps(TOKEN), json.dumps(TOKEN))
        await send("Page.addScriptToEvaluateOnNewDocument", source=seed)

        async def shoot(w, h, mobile):
            await send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=mobile)
            await send("Page.navigate", url=BASE + "/room")
            await asyncio.sleep(4.5)
            res = await send("Runtime.evaluate", expression=MEASURE_JS, returnByValue=True)
            return res.get("result", {}).get("value")

        print("=== DESKTOP 1280x900 ===")
        print(json.dumps(await shoot(1280, 900, False), indent=1))
        print("=== MOBILE 390x844 ===")
        print(json.dumps(await shoot(390, 844, True), indent=1))

asyncio.run(main())
