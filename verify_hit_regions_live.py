import asyncio, json, urllib.request
import websockets

TOKEN = json.load(open("/Users/grit/.hermes/team-room/config.json"))["auth_token"]
BASE = "http://127.0.0.1:8651"

async def main():
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9335/json"))
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        _id = 0
        async def send(method, **params):
            nonlocal _id
            _id += 1; mid = _id
            await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg: raise RuntimeError(method + " " + json.dumps(msg["error"]))
                    return msg.get("result", {})
        await send("Page.enable"); await send("Runtime.enable")
        seed = "try{localStorage.setItem('tr_token', %s);window.prompt=function(){return %s;};window.alert=function(){};}catch(e){}" % (json.dumps(TOKEN), json.dumps(TOKEN))
        await send("Page.addScriptToEvaluateOnNewDocument", source=seed)
        await send("Page.navigate", url=BASE + "/office")
        await asyncio.sleep(4.5)
        expr = """JSON.stringify({
          regions: typeof window.__HITREGIONS,
          hitTest: typeof window.__hitTest,
          n_agents: window.__HITREGIONS ? Object.keys(window.__HITREGIONS.agents).length : -1,
          n_cards: window.__HITREGIONS ? window.__HITREGIONS.cards.length : -1,
          agent_click: window.__hitTest(16, 104),
          card_click: window.__hitTest(49, 34.5),
          stage_click: window.__hitTest(48, 16),
          slots: document.querySelectorAll('.slot').length,
          env_loaded: (document.querySelector('.office img') || {}).naturalWidth || 0,
          board_canvas: (document.getElementById('boardLive') || {}).width
        })"""
        res = await send("Runtime.evaluate", expression=expr, returnByValue=True)
        print(res["result"]["value"])

asyncio.run(main())
