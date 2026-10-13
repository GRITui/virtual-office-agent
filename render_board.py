#!/usr/bin/env python3
"""Render /office in headless Chrome, dump the boardLive canvas pixels + screenshots.
Usage: python3 render_board.py <tag> <width>
  tag   : before | after  (output suffix)
  width : 1280 | 390      (CSS viewport width)
Outputs (scratch dir):
  <tag>_<width>_full.png      full-page screenshot
  <tag>_<width>_board.png     board region crop (boardLive bounding box * dpr)
  <tag>_<width>_canvas.png    the 121x33 boardLive canvas decoded to PNG
  <tag>_<width>_grid.txt      121x33 char grid (color-classified) for diffing
"""
import asyncio, json, subprocess, time, urllib.request, sys, base64, os

SCRATCH = "/Users/grit/.hermes/profiles/pixel-art-designer/cache/scratch"
PORT = 9478
TOK = json.load(open("/Users/grit/.hermes/team-room/config.json"))["auth_token"]

def cdp_cmd(id_, method, **params):
    return {"id": id_, "method": method, "params": params}

async def main():
    tag, width = sys.argv[1], int(sys.argv[2])
    chrome = subprocess.Popen([
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless=new", f"--remote-debugging-port={PORT}",
        "--no-first-run", "--no-default-browser-check", "--disable-gpu",
        "--remote-allow-origins=*",
        f"--window-size={width},800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                if targets: break
            except Exception:
                time.sleep(0.5)
        assert targets, "chrome did not come up"
        page = next(t for t in targets if t.get("type") == "page")
        import websockets
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            mid = 0
            async def cmd(method, **params):
                nonlocal mid; mid += 1
                await ws.send(json.dumps(cdp_cmd(mid, method, **params)))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get("id") == mid:
                        return r.get("result", {})
            await cmd("Page.enable"); await cmd("Runtime.enable")
            await cmd("Page.addScriptToEvaluateOnNewDocument",
                      source=f"localStorage.setItem('tr_token',{json.dumps(TOK)});")
            await cmd("Page.navigate", url="http://localhost:8651/office")
            await asyncio.sleep(5)  # let /kanban load + drawBoard run
            # full-page screenshot
            shot = await cmd("Page.captureScreenshot", format="png",
                             captureBeyondViewport=True)
            open(f"{SCRATCH}/{tag}_{width}_full.png", "wb").write(base64.b64decode(shot["data"]))
            # board canvas pixels + geometry
            expr = """
(() => {
  const cv = document.getElementById('boardLive');
  const bb = cv.getBoundingClientRect();
  const scene = document.getElementById('office').getBoundingClientRect();
  return JSON.stringify({
    dpr: window.devicePixelRatio,
    canvas: cv.toDataURL('image/png'),
    bb: {x:bb.x, y:bb.y, w:bb.width, h:bb.height},
    scene: {x:scene.x, y:scene.y, w:scene.width, h:scene.height},
    innerW: window.innerWidth
  });
})()
"""
            r = await cmd("Runtime.evaluate", expression=expr, returnByValue=True)
            val = json.loads(r["result"]["value"])
            png = base64.b64decode(val["canvas"].split(",", 1)[1])
            open(f"{SCRATCH}/{tag}_{width}_canvas.png", "wb").write(png)
            json.dump(val, open(f"{SCRATCH}/{tag}_{width}_geom.json", "w"))
            # classify the 121x33 grid
            from PIL import Image
            im = Image.open(f"{SCRATCH}/{tag}_{width}_canvas.png").convert("RGB")
            W, H = im.size
            px = im.load()
            def cls(c):
                r, g, b = c
                if r > 200 and g > 200 and b > 200: return "W"      # white count
                if r > 200 and g > 150 and b < 120: return "T"     # todo yellow
                if abs(r-139) < 40 and abs(g-147) < 40 and abs(b-163) < 40: return "R"  # ready gray
                if r < 150 and g > 150 and b > 180: return "U"     # running blue
                if r > 190 and g < 130 and b < 130: return "B"     # blocked red
                if r < 170 and g > 170 and b < 150: return "D"     # done green
                if 40 < r < 90 and 45 < g < 80 and 55 < b < 100: return "g"  # card gray
                if r < 30 and g < 30 and b < 35: return "."        # dark bg
                return "?"
            lines = []
            for y in range(H):
                row = "".join(cls(px[x, y]) for x in range(W))
                lines.append(f"{y:2d} {row}")
            open(f"{SCRATCH}/{tag}_{width}_grid.txt", "w").write("\n".join(lines))
            print(f"[{tag} {width}] dpr={val['dpr']} canvas={W}x{H} "
                  f"bb=({val['bb']['x']:.1f},{val['bb']['y']:.1f},{val['bb']['w']:.1f}x{val['bb']['h']:.1f}) "
                  f"scene=({val['scene']['x']:.1f},{val['scene']['y']:.1f},{val['scene']['w']:.1f}x{val['scene']['h']:.1f})")
    finally:
        chrome.terminate()

asyncio.run(main())
