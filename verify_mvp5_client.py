#!/usr/bin/env python3
"""MVP-5 client-render verify (t_f9dc9ccf) — token-independent, robust.

Stubs EventSource + fetch BEFORE the app JS runs (quiet init: no SSE 401
retry loop, no live token needed). Then injects a synthetic file_ref into
the real rendered /room page and verifies the client logic end-to-end:
  - image/* ref  -> chip gets a real <img class="fthumb"> (naturalWidth>0)
                    from the mocked authed /files fetch, title "Open image"
  - non-image ref -> chip keeps the pin, title "Download <name>"
  - click image chip  -> window.open(blobURL, "_blank")
  - click text chip   -> <a download> created with the blob href + filename
"""
import asyncio, json, subprocess, time, urllib.request

PORT = 9481
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
           "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def cdp_cmd(id_, method, **params):
    return {"id": id_, "method": method, "params": params}


# Installed on the page before its scripts run. Stubs the network + SSE so
# init is quiet and token-free; /files/* returns real blobs.
PRELOAD = r"""
(() => {
  const pngB64 = %s;
  function b64ToBytes(b64){
    const bin = atob(b64); const u8 = new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)u8[i]=bin.charCodeAt(i); return u8;
  }
  const origFetch = window.fetch;
  window.fetch = (input, init) => {
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    if (url.indexOf('/files/img1') !== -1) {
      const blob = new Blob([b64ToBytes(pngB64)], {type:'image/png'});
      return Promise.resolve(new Response(blob, {status:200,
        headers:{'Content-Type':'image/png'}}));
    }
    if (url.indexOf('/files/txt1') !== -1) {
      const blob = new Blob([new TextEncoder().encode('mock text body')],
        {type:'text/plain'});
      return Promise.resolve(new Response(blob, {status:200,
        headers:{'Content-Type':'text/plain'}}));
    }
    if (url.indexOf('/health') !== -1) {
      return Promise.resolve(new Response(
        JSON.stringify({status:'ok',round_active:false,auth_enabled:true}),
        {status:200, headers:{'Content-Type':'application/json'}}));
    }
    if (url.indexOf('/messages') !== -1) {
      return Promise.resolve(new Response(
        JSON.stringify({messages:[],state:{round_active:false,
          pending:[],spoken:[]}}),
        {status:200, headers:{'Content-Type':'application/json'}}));
    }
    if (url.indexOf('/kanban') !== -1 || url.indexOf('/state') !== -1) {
      return Promise.resolve(new Response(JSON.stringify({}),
        {status:200, headers:{'Content-Type':'application/json'}}));
    }
    return origFetch.apply(window, [input, init]);
  };
  // No SSE retry loop: EventSource is a no-op that never fires.
  window.EventSource = class {
    constructor(){ this.readyState = 0; }
    close(){}
    addEventListener(){}
    removeEventListener(){}
  };
  localStorage.setItem('tr_token', 'dummy-token');
  return 'preload-ok';
})()
""" % json.dumps(PNG_B64)


async def main():
    chrome = subprocess.Popen([
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--headless=new", f"--remote-debugging-port={PORT}",
        "--no-first-run", "--no-default-browser-check", "--disable-gpu",
        "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json", timeout=2))
                if targets:
                    break
            except Exception:
                time.sleep(0.5)
        assert targets, "chrome did not come up"
        page = next(t for t in targets if t.get("type") == "page")
        import websockets
        async with websockets.connect(page["webSocketDebuggerUrl"],
                                      max_size=None) as ws:
            mid = 0

            async def cmd(method, timeout=15, **params):
                nonlocal mid
                mid += 1
                await ws.send(json.dumps(cdp_cmd(mid, method, **params)))
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        r = json.loads(await asyncio.wait_for(
                            ws.recv(), timeout=timeout))
                    except asyncio.TimeoutError:
                        return {"__timeout__": method}
                    if r.get("id") == mid:
                        return r.get("result", {})
                return {"__timeout__": method}

            async def ev(expression, awaitPromise=False, timeout=15):
                r = await cmd("Runtime.evaluate", expression=expression,
                              returnByValue=True, awaitPromise=awaitPromise,
                              timeout=timeout)
                if "__timeout__" in r:
                    return "<timeout>"
                res = r.get("result", {})
                if "exceptionDetails" in r:
                    return "<exception: " + str(
                        r["exceptionDetails"].get("exception", {}).get(
                            "description", ""))[:200] + ">"
                return res.get("value")

            await cmd("Page.enable")
            await cmd("Runtime.enable")
            await cmd("Page.addScriptToEvaluateOnNewDocument",
                      source=PRELOAD)
            await cmd("Page.navigate", url="http://localhost:8651/room")
            await asyncio.sleep(3)
            print("TITLE:", await ev("document.title"), flush=True)
            print("READY:", await ev("document.readyState"), flush=True)

            inject = r"""
(() => {
  if (typeof addMsg !== 'function') return 'NO-addMsg';
  const msg = {id: 999999, author: 'Krit', ts: new Date().toISOString(),
    text: 'mvp5 client check',
    file_refs: [
      {id:'img1', name:'test.png', mime:'image/png'},
      {id:'txt1', name:'notes.txt', mime:'text/plain'}
    ]};
  addMsg(msg, false);
  return 'injected';
})()
"""
            print("INJECT:", await ev(inject), flush=True)
            await asyncio.sleep(1.5)

            state = r"""
(() => {
  const out = {};
  const chips = [...document.querySelectorAll('.filechip')];
  out.chipCount = chips.length;
  const img = chips.find(c => c.textContent.includes('test.png'));
  const txt = chips.find(c => c.textContent.includes('notes.txt'));
  out.imgChip = img ? {
    cls: img.className, title: img.title,
    thumb: (() => { const t = img.querySelector('img.fthumb');
      return t ? {src: t.src.slice(0,15), w: t.naturalWidth,
                  h: t.naturalHeight, complete: t.complete} : null; })(),
    placeholderGone: !img.querySelector('.fthumb-ph')
  } : null;
  out.txtChip = txt ? {cls: txt.className, title: txt.title,
    hasPin: !!txt.querySelector('.ic'),
    hasThumb: !!txt.querySelector('img.fthumb')} : null;
  return JSON.stringify(out);
})()
"""
            print("STATE:", await ev(state), flush=True)

            click_img = r"""
(async () => {
  let opened = null; window.open = (u) => { opened = u; return null; };
  const img = [...document.querySelectorAll('.filechip')]
    .find(c => c.textContent.includes('test.png'));
  if (!img) return JSON.stringify({err:'no img chip'});
  img.click();
  await new Promise(r => setTimeout(r, 200));
  return JSON.stringify({opened: opened ? opened.slice(0, 20) : null});
})()
"""
            print("CLICK_IMG:", await ev(click_img, awaitPromise=True),
                  flush=True)

            click_txt = r"""
(async () => {
  let dl = null;
  const origClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function(){
    dl = {href: (this.href||'').slice(0,20), download: this.download};
    return;
  };
  const txt = [...document.querySelectorAll('.filechip')]
    .find(c => c.textContent.includes('notes.txt'));
  if (!txt) return JSON.stringify({err:'no txt chip'});
  txt.click();
  await new Promise(r => setTimeout(r, 200));
  HTMLAnchorElement.prototype.click = origClick;
  return JSON.stringify(dl);
})()
"""
            print("CLICK_TXT:", await ev(click_txt, awaitPromise=True),
                  flush=True)
    finally:
        chrome.terminate()


asyncio.run(main())
