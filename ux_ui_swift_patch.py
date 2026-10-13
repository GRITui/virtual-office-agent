#!/usr/bin/env python3
# ux-ui — mobile composer fix (pill -> own status line) + Swift-style chrome proof
# (token sheet: frosted glass, soft radii, spring motion, SF type scale) on header + composer.
# One-shot patch: asserts each old string exists exactly once, applies, writes back.
import sys, io

PATH = "/Users/grit/.hermes/team-room/webui/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

R = []

# 1) token sheet (replace the single-line :root)
R.append((
":root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--text:#e6e9ef;--dim:#8b93a7;--accent:#3b82f6;--human:#22d3ee;}",
""":root{
  /* base palette */
  --bg:#0f1115;--panel:#171a21;--line:#262b36;--text:#e6e9ef;--dim:#8b93a7;--accent:#3b82f6;--human:#22d3ee;
  /* swift-style chrome: frosted glass, soft radii, spring motion, SF type scale */
  --glass:rgba(23,26,33,.66);--glass-strong:rgba(15,17,21,.84);--blur:16px;
  --r-s:8px;--r-m:12px;--r-l:16px;--r-pill:999px;
  --sh-s:0 1px 2px rgba(0,0,0,.35);--sh-m:0 6px 20px rgba(0,0,0,.45);--sh-l:0 18px 48px rgba(0,0,0,.6);
  --spring:cubic-bezier(.34,1.56,.64,1);--ease:cubic-bezier(.22,1,.36,1);--d1:120ms;--d2:220ms;
  --f-11:11px;--f-12:12px;--f-13:13px;--f-14:14px;--f-16:16px;--f-18:18px;--f-20:20px;
}"""
))

# 2) header -> frosted glass
R.append((
"header{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}",
"header{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--glass-strong);backdrop-filter:blur(var(--blur)) saturate(1.15);-webkit-backdrop-filter:blur(var(--blur)) saturate(1.15);box-shadow:var(--sh-s)}"
))

# 3) header title -> SF scale
R.append((
"header .title{font-weight:700}",
"header .title{font-weight:700;font-size:var(--f-16);letter-spacing:-.2px}"
))

# 4) nav links -> pill radius + ease
R.append((
".nav a{color:var(--dim);text-decoration:none;font-size:13px;padding:4px 10px;border-radius:6px}",
".nav a{color:var(--dim);text-decoration:none;font-size:var(--f-13);padding:5px 11px;border-radius:var(--r-pill);transition:color var(--d1) var(--ease),background var(--d1) var(--ease)}"
))

# 5) pill -> token radius + ellipsis guard + ease
R.append((
".pill{font-size:12px;padding:3px 10px;border-radius:20px;border:1px solid var(--line);color:var(--dim);white-space:nowrap}",
".pill{font-size:var(--f-12);padding:3px 10px;border-radius:var(--r-pill);border:1px solid var(--line);color:var(--dim);white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis;transition:background var(--d1) var(--ease),color var(--d1) var(--ease)}"
))

# 6) composer -> frosted glass (shadow up, it sits at the bottom)
R.append((
".composer{border-top:1px solid var(--line);background:var(--panel);padding:10px 14px;position:relative}",
".composer{border-top:1px solid var(--line);background:var(--glass-strong);backdrop-filter:blur(var(--blur)) saturate(1.15);-webkit-backdrop-filter:blur(var(--blur)) saturate(1.15);padding:10px 14px;position:relative;box-shadow:0 -6px 20px rgba(0,0,0,.35)}"
))

# 7) insert .comp-top status line above .row2
R.append((
".row2{display:flex;gap:8px;align-items:stretch}",
""".comp-top{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.comp-top .pill{align-self:center}
.row2{display:flex;gap:8px;align-items:stretch}"""
))

# 8) base button -> token radius + spring
R.append((
"button{background:#20242e;border:1px solid var(--line);color:var(--text);border-radius:8px;height:38px;padding:0 14px;cursor:pointer;font:inherit}",
"button{background:#20242e;border:1px solid var(--line);color:var(--text);border-radius:var(--r-s);height:38px;padding:0 14px;cursor:pointer;font:inherit;transition:transform var(--d1) var(--spring),background var(--d1) var(--ease),box-shadow var(--d1) var(--ease)}"
))

# 9) button hover/active -> spring lift
R.append((
"button:hover{background:#2a3140}",
"button:hover{background:#2a3140;transform:translateY(-1px);box-shadow:var(--sh-s)}\nbutton:active{transform:translateY(0);box-shadow:none}"
))

# 10) hint -> SF scale
R.append((
".hint{color:var(--dim);font-size:11px;margin-top:6px}",
".hint{color:var(--dim);font-size:var(--f-11);margin-top:6px}"
))

# 11) drop the old in-row pill centering (pill now lives in .comp-top)
R.append((
".row2 .pill{align-self:center}",
""
))

# 12) kanban overlay -> frosted backdrop
R.append((
".kanban-overlay{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.72)}",
".kanban-overlay{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.55);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}"
))

# 13) overlay head -> frosted glass
R.append((
".ko-head{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line)}",
".ko-head{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:var(--glass-strong);backdrop-filter:blur(var(--blur));-webkit-backdrop-filter:blur(var(--blur));border-bottom:1px solid var(--line);box-shadow:var(--sh-s)}"
))

# 14) HTML: move the pill out of .row2 into its own .comp-top status line
R.append((
"""        <div class="row2">
          <button id="attachBtn" title="Attach file">\U0001F4CE</button>
          <textarea class="input" id="input" placeholder="Message the room \u2014 @ to mention, paste an image, or attach a file"></textarea>
          <span class="pill" id="pill">idle</span>
          <button class="send" id="send">Send</button>
        </div>""",
"""        <div class="comp-top"><span class="pill" id="pill">idle</span></div>
        <div class="row2">
          <button id="attachBtn" title="Attach file">\U0001F4CE</button>
          <textarea class="input" id="input" placeholder="Message the room \u2014 @ to mention, paste an image, or attach a file"></textarea>
          <button class="send" id="send">Send</button>
        </div>"""
))

for i, (old, new) in enumerate(R, 1):
    n = src.count(old)
    if n != 1:
        print("FAIL rule %d: found %d occurrence(s), expected 1" % (i, n))
        print("  old[:80] = %r" % old[:80])
        sys.exit(1)
    src = src.replace(old, new)

with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)

print("OK: applied %d rules" % len(R))
print("new size = %d bytes" % len(src.encode("utf-8")))
print("pill count = %d (expect 1)" % src.count('id="pill"'))
print("comp-top count = %d (expect 1)" % src.count('class="comp-top"'))
print("backdrop-filter count = %d (expect >=4)" % src.count("backdrop-filter"))
