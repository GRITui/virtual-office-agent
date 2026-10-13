import asyncio, json, urllib.request, base64
import websockets

TOKEN = "8LgWmS3xT9vQzR5wYpL2nB7cF4aJ6kDsVTb"
BASE = "http://127.0.0.1:8651"

async def main():
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9334/json"))
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

        async def shot(w, h, mobile, path):
            await send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2, mobile=mobile)
            await send("Page.navigate", url=BASE + "/room")
            await asyncio.sleep(4.5)
            res = await send("Page.captureScreenshot", format="png")
            open(path, "wb").write(base64.b64decode(res["data"]))
            print("wrote", path)

        await shot(1280, 900, False, "/tmp/qa_room_desktop.png")
        await shot(390, 844, True, "/tmp/qa_room_mobile.png")

asyncio.run(main())
