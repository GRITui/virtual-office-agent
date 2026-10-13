#!/usr/bin/env python3
"""MVP-5 rendered-DOM verify (t_f9dc9ccf): transcript image chip thumbnail + click behavior.
Room's bar is the rendered DOM, not the file. Run AFTER the round-boundary restart."""
import asyncio, json, subprocess, time, urllib.request, sys

PORT = 9477
TOK = json.load(open("/Users/grit/.hermes/team-room/config.json"))["auth_token"]

def cdp_cmd(id_, method, **params):
    return {"id": id_, "method": method, "params": params}

async def main():
    chrome = subprocess.Popen([
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless=new", f"--remote-debugging-port={PORT}",
        "--no-first-run", "--no-default-browser-check", "--disable-gpu",
        "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for CDP
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
            # seed token BEFORE app JS runs -> blocking prompt() never fires
            await cmd("Page.addScriptToEvaluateOnNewDocument",
                      source=f"localStorage.setItem('tr_token',{json.dumps(TOK)});")
            await cmd("Page.navigate", url="http://localhost:8651/room")
            await asyncio.sleep(6)  # let /messages load + thumbnails fetch
            expr = """
(async () => {
  const out = {};
  // find the transcript chip for the known upload
  const chips = [...document.querySelectorAll('.filechip')];
  out.chipCount = chips.length;
  const img = chips.find(c => c.textContent.includes('IMG_3014.png'));
  if (!img) { out.err = 'no IMG_3014 chip; chips=' + chips.map(c=>c.textContent).join('|'); return JSON.stringify(out); }
  const th = img.querySelector('img.fthumb');
  out.thumb = th ? {src: th.src.slice(0, 20), w: th.naturalWidth, h: th.naturalHeight, complete: th.complete} : null;
  // stub window.open, click the chip, capture
  let opened = null; window.open = (u) => { opened = u; return null; };
  img.click();
  await new Promise(r => setTimeout(r, 80));  // app handler is async; window.open fires post-await
  out.opened = opened ? opened.slice(0, 12) : null;
  // non-image chip (up_test.txt) if present
  const txt = chips.find(c => c.textContent.includes('up_test.txt'));
  out.textChip = txt ? txt.className : null;
  out.scrollW = document.documentElement.scrollWidth;
  out.innerW = window.innerWidth;
  return JSON.stringify(out);
})()
"""
            r = await cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
            print("DOM:", r.get("result", {}).get("value"))
            # console errors
            expr2 = "JSON.stringify(window.__errs||[])"
            r2 = await cmd("Runtime.evaluate", expression=expr2, returnByValue=True)
            print("ERRS:", r2.get("result", {}).get("value"))
    finally:
        chrome.terminate()

asyncio.run(main())
