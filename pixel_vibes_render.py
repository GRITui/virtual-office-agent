#!/usr/bin/env python3
"""Render the live /office scene at desktop + mobile, measure geometry, screenshot.
Closes the v2-verify card with fresh evidence + gives a baseline for the Office Vibes work.
"""
import asyncio, json, subprocess, time, urllib.request, base64, os, sys
import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9444
OUT = os.path.dirname(os.path.abspath(__file__))
URL = "http://127.0.0.1:8651/office"

def http_json(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return json.loads(r.read())

async def cdp(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})

async def main():
    prof = f"/tmp/pixel_vibes_{int(time.time())}"
    proc = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # wait for CDP
    target = None
    for _ in range(50):
        time.sleep(0.3)
        try:
            ts = http_json("/json")
            target = next((t for t in ts if t.get("type") == "page"), None)
            if target: break
        except Exception:
            pass
    if not target:
        print("CDP target not up"); proc.kill(); return
    ws_url = target["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=None) as ws:
        mid = 0
        async def call(method, params=None):
            nonlocal mid; mid += 1
            return await cdp(ws, mid, method, params)
        await call("Page.enable"); await call("Runtime.enable")
        # navigate
        await call("Page.navigate", {"url": URL})
        await asyncio.sleep(3.0)  # let scene + frames + board load
        # measure scene geometry (desktop)
        measure_js = r"""
        (() => {
          const o = document.querySelector('.office');
          const b = document.getElementById('boardLive');
          const figs = [...document.querySelectorAll('.fig')];
          const r = (el) => { if(!el) return null; const x=el.getBoundingClientRect();
            return {w:Math.round(x.width), h:Math.round(x.height), x:Math.round(x.x), y:Math.round(x.y)}; };
          return {
            title: document.title,
            office: r(o),
            board: r(b),
            figCount: figs.length,
            figs: figs.map(f => { const x=f.getBoundingClientRect();
              return {w:Math.round(x.width), h:Math.round(x.height), x:Math.round(x.x), y:Math.round(x.y)}; }),
            status: (document.getElementById('status')||{}).textContent || '',
            clock: (document.getElementById('clock')||{}).textContent || '',
            scrollW: document.documentElement.scrollWidth,
            innerW: window.innerWidth
          };
        })()
        """
        res = await call("Runtime.evaluate", {"expression": measure_js, "returnByValue": True})
        desktop = res.get("result", {}).get("value", {})
        print("=== DESKTOP (1280) ===")
        print(json.dumps(desktop, indent=1))
        shot = await call("Page.captureScreenshot", {"format": "png"})
        open(f"{OUT}/vibes_desktop.png", "wb").write(base64.b64decode(shot["data"]))
        print("saved vibes_desktop.png")
        # mobile: override device metrics to 390x844
        await call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        await asyncio.sleep(1.5)
        res2 = await call("Runtime.evaluate", {"expression": measure_js, "returnByValue": True})
        mobile = res2.get("result", {}).get("value", {})
        print("=== MOBILE (390) ===")
        print(json.dumps(mobile, indent=1))
        shot2 = await call("Page.captureScreenshot", {"format": "png"})
        open(f"{OUT}/vibes_mobile.png", "wb").write(base64.b64decode(shot2["data"]))
        print("saved vibes_mobile.png")
    proc.kill()
    print("DONE")

asyncio.run(main())
