#!/usr/bin/env python3
import asyncio, json, subprocess, time, urllib.request, sys

PORT = 9479

async def main():
    print("launching chrome", flush=True)
    chrome = subprocess.Popen([
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless=new", f"--remote-debugging-port={PORT}",
        "--no-first-run", "--no-default-browser-check", "--disable-gpu",
        "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for i in range(40):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2))
                if targets:
                    break
            except Exception as e:
                pass
            time.sleep(0.5)
        print("targets:", bool(targets), flush=True)
        assert targets, "chrome did not come up"
        page = next(t for t in targets if t.get("type") == "page")
        print("ws url:", page["webSocketDebuggerUrl"], flush=True)
        import websockets
        print("connecting ws", flush=True)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            print("connected", flush=True)
            mid = 0
            async def cmd(method, **params):
                nonlocal mid
                mid += 1
                await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get("id") == mid:
                        return r.get("result", {})
            await cmd("Page.enable")
            await cmd("Runtime.enable")
            print("enabled", flush=True)
            await cmd("Page.navigate", url="http://localhost:8651/room")
            print("navigated", flush=True)
            await asyncio.sleep(3)
            r = await cmd("Runtime.evaluate", expression="document.title + ' | readyState=' + document.readyState",
                          returnByValue=True)
            print("EVAL:", r.get("result", {}).get("value"), flush=True)
    finally:
        chrome.terminate()

asyncio.run(main())
