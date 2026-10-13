import asyncio, json, subprocess, time, urllib.request, socket

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

PORT = free_port()
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu",
    f"--remote-debugging-port={PORT}", "--window-size=1280,800",
    "--user-data-dir=/tmp/qa_journey_chrome", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def get_ws():
    import websockets
    for _ in range(40):
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2)
            tabs = json.load(r)
            page = next((t for t in tabs if t.get("type")=="page"), tabs[0])
            ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
            return ws
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("no CDP target")

async def send(ws, id_, method, params=None):
    await ws.send(json.dumps({"id": id_, "method": method, "params": params or {}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == id_:
            return m.get("result", {})

async def main():
    ws = await get_ws()
    await send(ws, 1, "Page.enable")
    await send(ws, 2, "Runtime.enable")
    await send(ws, 3, "Page.navigate", {"url": "http://127.0.0.1:8651/office"})
    await asyncio.sleep(3.0)  # let 120ms render loop + /events connect settle
    expr = """(() => {
      const st = document.getElementById('status');
      const conn = document.getElementById('conn');
      const lamps = [...document.querySelectorAll('.lamp')].map(l => l.className);
      const bubbles = [...document.querySelectorAll('.bubble')].map(b => b.className);
      return JSON.stringify({
        title: document.title,
        status: st ? st.textContent : null,
        conn: conn ? conn.textContent : null,
        lampCount: lamps.length,
        lamps: lamps,
        litLamps: lamps.filter(c => /think|work|stuck|done/.test(c)).length,
        bubbles: bubbles
      });
    })()"""
    r = await send(ws, 4, "Runtime.evaluate", {"expression": expr, "returnByValue": True})
    print("OFFICE_RENDER:", r.get("result", {}).get("value"))
    await ws.close()

try:
    asyncio.run(main())
finally:
    proc.terminate()
