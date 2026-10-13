#!/usr/bin/env python3
# ux-ui — rendered-DOM verify for the mobile composer fix + Swift chrome proof.
# Drives a headless Chrome via CDP, seeds the auth token via prompt() override,
# and measures the live / page at mobile + desktop.
import asyncio, json, subprocess, time, urllib.request, os, signal, base64

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9401
URL = "http://127.0.0.1:8651/"
PROFILE = "/tmp/tr_cdp_profile_uxui"
TOKEN = open("/tmp/tr_token.txt").read().strip()

MEASURE = r"""
(() => {
  const r = (el) => { if(!el) return null; const b = el.getBoundingClientRect();
    return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}; };
  const cs = (el) => el ? getComputedStyle(el) : null;
  const input = document.querySelector('#input');
  const pill = document.querySelector('#pill');
  const compTop = document.querySelector('.comp-top');
  const row2 = document.querySelector('.row2');
  const header = document.querySelector('header');
  const composer = document.querySelector('.composer');
  const attach = document.querySelector('#attachBtn');
  const send = document.querySelector('#send');
  const vis = (el) => el ? (el.offsetParent !== null && el.getBoundingClientRect().width > 0) : null;
  const hcs = cs(header), ccs = cs(composer);
  return JSON.stringify({
    attach: r(attach), send: r(send),
    attachVisible: vis(attach), sendVisible: vis(send),
    viewport: {w: innerWidth, h: innerHeight},
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    hOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    input: r(input),
    pill: r(pill),
    compTop: r(compTop),
    row2: r(row2),
    pillParentClass: pill ? pill.parentElement.className : null,
    pillInCompTop: !!(compTop && pill && compTop.contains(pill)),
    pillAboveRow2: (compTop && row2) ? (compTop.getBoundingClientRect().bottom <= row2.getBoundingClientRect().top + 1) : null,
    headerBackdrop: hcs ? (hcs.backdropFilter || hcs.webkitBackdropFilter || 'none') : null,
    composerBackdrop: ccs ? (ccs.backdropFilter || ccs.webkitBackdropFilter || 'none') : null,
    inputFlex: input ? cs(input).flex : null,
    msgCount: document.querySelectorAll('.msg').length
  });
})()
"""

def http_json(path):
    with urllib.request.urlopen("http://127.0.0.1:%d/%s" % (PORT, path), timeout=5) as r:
        return json.load(r)

async def cdp(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if "error" in msg:
                raise RuntimeError("%s -> %s" % (method, msg["error"]))
            return msg.get("result", {})

async def measure_viewport(ws, w, h, label):
    mid = 1000
    await cdp(ws, mid, "Emulation.setDeviceMetricsOverride",
              {"width": w, "height": h, "deviceScaleFactor": 2, "mobile": w < 500})
    mid += 1
    await cdp(ws, mid, "Page.navigate", {"url": URL})
    # wait for load + render + a beat for SSE
    await asyncio.sleep(3.5)
    mid += 1
    res = await cdp(ws, mid, "Runtime.evaluate",
                    {"expression": MEASURE, "returnByValue": True})
    val = res.get("result", {}).get("value", "")
    data = json.loads(val)
    print("=== %s (%dx%d) ===" % (label, w, h))
    print(json.dumps(data, indent=2))
    return data

async def main():
    if os.path.exists(PROFILE):
        subprocess.run(["rm", "-rf", PROFILE])
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--remote-debugging-port=%d" % PORT,
         "--user-data-dir=%s" % PROFILE, "--no-first-run", "--no-default-browser-check",
         "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        # wait for CDP
        ver = None
        for _ in range(40):
            try:
                ver = http_json("json/version"); break
            except Exception:
                time.sleep(0.5)
        if not ver:
            print("CDP did not come up"); return
        print("chrome:", ver.get("Browser"))
        targets = http_json("json/list")
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            print("no page target"); return
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            mid = 1
            await cdp(ws, mid, "Page.enable"); mid += 1
            await cdp(ws, mid, "Runtime.enable"); mid += 1
            # seed token BEFORE page scripts run -> blocking prompt() never fires
            await cdp(ws, mid, "Page.addScriptToEvaluateOnNewDocument",
                      {"source": "window.prompt = () => %s;" % json.dumps(TOKEN)}); mid += 1
            mob = await measure_viewport(ws, 390, 844, "MOBILE")
            # mobile screenshot for visual evidence (still at 390x844)
            shot = await cdp(ws, 9000, "Page.captureScreenshot",
                             {"format": "png", "captureBeyondViewport": False})
            open("/tmp/tr_mobile.png", "wb").write(base64.b64decode(shot["data"]))
            print("screenshot saved: /tmp/tr_mobile.png")
            desk = await measure_viewport(ws, 1280, 800, "DESKTOP")
            print("=== VERDICTS ===")
            for tag, d in (("mobile", mob), ("desktop", desk)):
                ok_input = d["input"] and d["input"]["w"] >= 200
                print("%s: input_w=%s hOverflow=%s pillInCompTop=%s pillAboveRow2=%s headerGlass=%s composerGlass=%s" % (
                    tag, d["input"]["w"] if d["input"] else None, d["hOverflow"],
                    d["pillInCompTop"], d["pillAboveRow2"],
                    "blur" in (d["headerBackdrop"] or ""), "blur" in (d["composerBackdrop"] or "")))
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

asyncio.run(main())
