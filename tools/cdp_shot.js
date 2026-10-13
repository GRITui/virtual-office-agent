#!/usr/bin/env node
// CDP screenshot driver: navigate, wait for window.__RENDER_DONE, capture.
// Usage: node cdp_shot.js <url> <out.png> [width] [height] [timeout_ms]
const http = require('http');
const fs = require('fs');

const [,, url, out, w = '1200', h = '700', timeoutMs = '30000'] = process.argv;
const W = parseInt(w), H = parseInt(h), TMO = parseInt(timeoutMs);
const PORT = 9333;

function getJSON(path) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path }, (r) => {
      let d = '';
      r.on('data', (c) => (d += c));
      r.on('end', () => res(JSON.parse(d)));
    }).on('error', rej);
  });
}

let idc = 0;
function cdp(ws, method, params = {}) {
  return new Promise((res, rej) => {
    const id = ++idc;
    const t = setTimeout(() => rej(new Error('timeout ' + method)), TMO);
    function once(ev) {
      if (ev.data === undefined) return;
      const m = JSON.parse(ev.data);
      if (m.id === id) {
        ws.removeEventListener('message', once);
        clearTimeout(t);
        if (m.error) rej(new Error(method + ': ' + JSON.stringify(m.error)));
        else res(m.result);
      }
    }
    ws.addEventListener('message', once);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // find a page target
  let target = null;
  for (let i = 0; i < 20 && !target; i++) {
    const list = await getJSON('/json/list');
    target = list.find((t) => t.type === 'page');
    if (!target) await sleep(300);
  }
  if (!target) throw new Error('no page target');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  await cdp(ws, 'Page.enable');
  await cdp(ws, 'Runtime.enable');
  await cdp(ws, 'Emulation.setDeviceMetricsOverride',
            { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await cdp(ws, 'Page.navigate', { url });

  // poll for render done
  let done = false, dbg = '', err = '';
  const deadline = Date.now() + TMO;
  while (Date.now() < deadline) {
    await sleep(250);
    const r = await cdp(ws, 'Runtime.evaluate', {
      expression: 'JSON.stringify({d:!!window.__RENDER_DONE,e:window.__RENDER_ERR||"",dbg:window.__dbg?document.getElementById("dbg").textContent:""})',
      returnByValue: true,
    });
    const v = JSON.parse(r.result.value);
    dbg = v.dbg; err = v.e;
    if (v.d) { done = true; break; }
    if (v.e) { break; }
  }

  // give the compositor a beat, then capture
  await sleep(300);
  const shot = await cdp(ws, 'Page.captureScreenshot',
                         { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(out, Buffer.from(shot.data, 'base64'));
  console.log('DONE=' + done + ' ERR=' + err);
  console.log('DBG>>>\n' + dbg + '\n<<<DBG');
  console.log('saved ' + out);
  ws.close();
  process.exit(done ? 0 : 2);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
