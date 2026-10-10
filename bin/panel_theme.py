"""SecurityCyber Admin Center — theme, brand mark, lock screen.

Imported by web/app.py. Keeps every legacy class name (.card .stat .tile .btn
.pill .flash .hero .tiles .bars .slots .tl …) so existing route bodies restyle
for free, and adds the branded shell + idle-lock UI.
"""

OPERATOR = "Charlie"
IDLE_SECS = 600  # 10 minutes — server-enforced; JS mirrors it as a courtesy

LOGO_SVG = (
    '<svg width="24" height="24" viewBox="0 0 48 48" fill="none" aria-hidden="true">'
    '<path d="M24 3 6 10v13c0 12 7.5 18.7 18 22 10.5-3.3 18-10 18-22V10L24 3Z" '
    'fill="#0d1420" stroke="#3b82f6" stroke-width="2"/>'
    '<circle cx="24" cy="21" r="7" fill="none" stroke="#e8eef6" stroke-width="2"/>'
    '<circle cx="24" cy="21" r="2.6" fill="#ef4444"/>'
    '<path d="M24 28v10M18 33h12" stroke="#3b82f6" stroke-width="2" stroke-linecap="round"/>'
    "</svg>"
)

# Line icons (24px grid, stroke = currentColor), inlined so the admin panel
# never loads anything from a third party.
_ICON_PATHS = {
    "hive": '<path d="M12 2.5l8.2 4.75v9.5L12 21.5l-8.2-4.75v-9.5z"/><path d="M12 8.2l3.3 1.9v3.8L12 15.8l-3.3-1.9v-3.8z"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
    "gauge": '<path d="M12 14l4-4"/><path d="M3.3 19a9 9 0 1 1 17.4 0"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
    "pen": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "radar": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12l6.4-6.4"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    "megaphone": '<path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    "card": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "rss": '<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="M21 2l-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    "terminal": '<path d="M4 17l6-6-6-6M12 19h8"/>',
    "play": '<path d="M6 4l14 8-14 8z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "zap": '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    "pause": '<path d="M10 4H6v16h4zM18 4h-4v16h4z"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "menu": '<path d="M3 6h18M3 12h18M3 18h18"/>',
    "map": '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z"/><path d="M9 4v14M15 6v14"/>',
}


def icon(name: str, cls: str = "ico") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{_ICON_PATHS.get(name, "")}</svg>')


CSS = """
:root{
  --bg:#0a0e14;--bg2:#0d1420;--panel:#121a26;--panel2:#182234;--line:#233248;
  --fg:#e8eef6;--dim:#8b9bb0;--faint:#5c6b80;
  --accent:#3b82f6;--accent2:#2563eb;--cyber:#ef4444;
  --good:#34d399;--warn:#fbbf24;--bad:#f87171;--radius:12px;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 30px rgba(0,0,0,.28)}
*{box-sizing:border-box}
body{margin:0;color:var(--fg);
 background:radial-gradient(1100px 480px at 100% -12%,rgba(59,130,246,.09),transparent),
  radial-gradient(820px 460px at -12% 112%,rgba(239,68,68,.055),transparent),var(--bg);
 font:14px/1.6 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{color:#fff}
.wrap{display:flex;min-height:100vh}
.side{width:226px;flex-shrink:0;background:linear-gradient(180deg,var(--panel),var(--bg2));
 border-right:1px solid var(--line);display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.brandbox{padding:19px 18px 15px;border-bottom:1px solid var(--line)}
.wm{display:flex;align-items:center;gap:9px;font-weight:800;font-size:16px}
.wm .c{color:var(--cyber)}
.brandbox small{display:block;color:var(--faint);font-weight:600;font-size:10px;
 text-transform:uppercase;letter-spacing:.16em;margin-top:7px}
.nav{padding:9px 0;overflow-y:auto;flex:1}
.nav a{display:block;padding:8px 18px;color:var(--dim);font-size:13.5px;font-weight:500;
 border-left:2px solid transparent}
.nav a:hover{background:var(--panel2);color:var(--fg);text-decoration:none}
.nav a.on{border-left-color:var(--accent);color:#fff;font-weight:600;
 background:linear-gradient(90deg,rgba(59,130,246,.16),transparent)}
.side .who{padding:11px 18px;border-top:1px solid var(--line);font-size:11px;color:var(--faint)}
.side .who b{color:var(--dim)}
.col{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{display:flex;align-items:center;gap:14px;padding:11px 34px;border-bottom:1px solid var(--line);
 background:rgba(10,14,20,.72);backdrop-filter:blur(9px);position:sticky;top:0;z-index:20}
.topbar .clk{font-variant-numeric:tabular-nums;color:var(--dim);font-size:13px}
.topbar .beat{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 0 0 var(--good);
 animation:pulse 2.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(52,211,153,.5)}70%{box-shadow:0 0 0 8px transparent}100%{box-shadow:0 0 0 0 transparent}}
.topbar .sp{flex:1}
.topbar .idle{font-size:11.5px;color:var(--faint)}.topbar .idle b{color:var(--warn);font-variant-numeric:tabular-nums}
.topbar form{margin:0}
.lockbtn{background:transparent;border:1px solid var(--line);color:var(--dim);border-radius:8px;
 padding:5px 12px;font:inherit;font-size:12px;font-weight:700;cursor:pointer}
.lockbtn:hover{border-color:var(--cyber);color:var(--cyber)}
.main{padding:24px 34px 64px;max-width:1080px}
h1{font-size:21px;font-weight:700;margin:2px 0 6px;letter-spacing:-.01em}
h1+.sub-h{color:var(--faint);font-size:13px;margin:0 0 20px}
h2{font-size:11.5px;margin:28px 0 10px;color:var(--faint);text-transform:uppercase;letter-spacing:.11em;font-weight:700}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
 padding:16px 18px;margin-bottom:14px;box-shadow:var(--shadow)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
@media(max-width:920px){.grid2{grid-template-columns:1fr}}
.stat,.tile{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:14px}
.stat .n,.tile .n{font-size:23px;font-weight:800;line-height:1.1;letter-spacing:-.02em}
.stat .l,.tile .l{color:var(--faint);font-size:10.5px;margin-top:4px;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(134px,1fr));gap:10px;margin-bottom:16px}
.tile.good{border-color:rgba(52,211,153,.4)}.tile.warn{border-color:rgba(251,191,36,.45)}.tile.bad{border-color:rgba(248,113,113,.5)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--faint);font-weight:700;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em}
tr:hover td{background:var(--panel2)}
input[type=text],input[type=number],input[type=password],input[type=email],input[type=datetime-local],textarea,select{
 width:100%;background:var(--bg2);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:9px 11px;font:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(59,130,246,.15)}
textarea{min-height:120px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
label{display:block;margin:12px 0 4px;font-size:12.5px;color:var(--dim)}label small{color:var(--faint)}
.btn{display:inline-block;background:linear-gradient(180deg,var(--accent),var(--accent2));color:#fff;border:0;
 border-radius:9px;padding:9px 16px;font:inherit;font-weight:700;cursor:pointer;box-shadow:0 1px 0 rgba(255,255,255,.08) inset}
.btn:hover{filter:brightness(1.08);text-decoration:none}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--fg);box-shadow:none}
.btn.ghost:hover{border-color:var(--accent);color:#fff}
.btn.good{background:linear-gradient(180deg,#34d399,#10b981)}
.btn.bad{background:linear-gradient(180deg,#f87171,#ef4444)}
.btn.sm{padding:5px 10px;font-size:12px;border-radius:7px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.02em}
.pill.good{background:rgba(52,211,153,.16);color:var(--good)}
.pill.warn{background:rgba(251,191,36,.16);color:var(--warn)}
.pill.bad{background:rgba(248,113,113,.16);color:var(--bad)}
.pill.dim{background:var(--panel2);color:var(--dim)}
.flash{background:rgba(59,130,246,.13);border:1px solid rgba(59,130,246,.4);border-radius:10px;
 padding:11px 15px;margin-bottom:18px;font-size:13px}
pre{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px;overflow:auto;
 font-size:12.5px;white-space:pre-wrap;word-break:break-word}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.dim{color:var(--dim)}
.hero{display:flex;flex-wrap:wrap;gap:18px;align-items:center;justify-content:space-between;
 background:linear-gradient(135deg,var(--panel),var(--panel2));border:1px solid var(--line);
 border-radius:16px;padding:20px 24px;margin-bottom:16px;box-shadow:var(--shadow)}
.hero .state{font-size:19px;font-weight:800;display:flex;align-items:center;gap:10px;letter-spacing:-.01em}
.hero .sub{color:var(--dim);font-size:12.5px;margin-top:4px}
.beacon{width:11px;height:11px;border-radius:50%;flex-shrink:0;animation:pulse 2.4s infinite}
.count{font-variant-numeric:tabular-nums;font-weight:800}
.bars{display:flex;align-items:flex-end;gap:5px;height:64px;padding-top:4px}
.bars .b{flex:1;background:var(--panel2);border-radius:3px 3px 0 0;min-height:3px;position:relative}
.bars .b.has{background:var(--accent)}
.bars .b span{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:10px;color:var(--faint)}
.slots{display:flex;gap:6px;flex-wrap:wrap}
.slot{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;border:1px solid var(--line);color:var(--faint)}
.slot.past{opacity:.4}.slot.next{border-color:var(--accent);color:#fff;background:rgba(59,130,246,.2)}
.tl{list-style:none;margin:0;padding:0}
.tl li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:12.5px;align-items:baseline}
.tl li:last-child{border-bottom:0}
.tl .t{color:var(--faint);white-space:nowrap;font-size:11.5px;min-width:64px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg label{margin:0}.seg span{padding:6px 12px;font-size:12px;cursor:pointer;color:var(--dim)}

/* --- 2026-09 refresh: grouped nav, icons, tidier tiles and forms, mobile --- */
.ico{width:16px;height:16px;flex-shrink:0}
.nav .grp{padding:16px 18px 5px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
 color:var(--faint);font-weight:700}
.nav .grp:first-child{padding-top:8px}
.nav a{display:flex;align-items:center;gap:10px;padding:7px 18px;margin:1px 10px;border-radius:8px;
 border-left:0}
.nav a .ico{opacity:.7}
.nav a:hover .ico,.nav a.on .ico{opacity:1}
.nav a.on{background:rgba(59,130,246,.16);color:#fff;box-shadow:inset 2px 0 0 var(--accent)}
.stat .n,.tile .n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:clamp(17px,1.7vw,23px)}
.tile{transition:border-color .15s,transform .15s}.tile:hover{transform:translateY(-1px)}
.grid{align-items:end}
input[type=number]{-moz-appearance:textfield;appearance:textfield}
input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
input[type=checkbox],input[type=radio]{accent-color:var(--accent);width:15px;height:15px;vertical-align:-2px;margin:0 6px 0 0}
select{appearance:none;background-image:linear-gradient(45deg,transparent 50%,var(--dim) 50%),
 linear-gradient(135deg,var(--dim) 50%,transparent 50%);background-position:calc(100% - 17px) 55%,calc(100% - 12px) 55%;
 background-size:5px 5px;background-repeat:no-repeat;padding-right:32px}
.btn{display:inline-flex;align-items:center;gap:7px}
.btn .ico{width:15px;height:15px}
.card:hover{border-color:#2b3d57}
.burger{display:none;background:transparent;border:1px solid var(--line);color:var(--fg);border-radius:8px;
 padding:5px 7px;cursor:pointer;align-items:center}
.navt{display:none}
@media(max-width:900px){
 .side{position:fixed;left:0;top:0;z-index:40;transform:translateX(-100%);transition:transform .2s;
  box-shadow:var(--shadow)}
 .navt:checked~.wrap .side{transform:none}
 .burger{display:inline-flex}
 .topbar{padding:10px 16px}.main{padding:18px 16px 56px}
 .topbar .idle{display:none}
}
.topbar .clk,.topbar .idle{white-space:nowrap}
"""


def _greeting(hh: int) -> str:
    return ("Good evening" if hh >= 17 else "Good afternoon" if hh >= 12
            else "Good morning" if hh >= 5 else "Still up")


def shell(title, body, nav_html, flash_html, locked_enabled, now):
    """Full page. `now` is a datetime in the panel's local tz (BST)."""
    greet = f"{_greeting(now.hour)}, {OPERATOR}"
    lock_ui = (
        '<span class="idle">auto-lock in <b id="idleT">10:00</b></span>'
        f'<form method="post" action="/lock"><button class="lockbtn">{icon("lock")} Lock</button></form>'
    ) if locked_enabled else '<span class="idle" style="color:var(--faint)">panel is open — set PANEL_PASSPHRASE to lock it</span>'
    idle_js = f"""<script>
(function(){{
 if(!{str(bool(locked_enabled)).lower()})return;
 var secs={IDLE_SECS},left=secs,el=document.getElementById('idleT');
 function fmt(s){{s=Math.max(0,s|0);return (s/60|0)+':'+('0'+(s%60)).slice(-2);}}
 function tick(){{left--;if(el)el.textContent=fmt(left);if(left<=0){{location='/unlock?next='+encodeURIComponent(location.pathname+location.search);}}}}
 setInterval(tick,1000);
 ['click','keydown','mousemove','scroll'].forEach(function(ev){{document.addEventListener(ev,function(){{left=secs;}},{{passive:true}});}});
}})();
setInterval(function(){{var c=document.querySelector('.clk time');if(c)c.textContent=new Date().toLocaleTimeString('en-GB',{{hour:'2-digit',minute:'2-digit',second:'2-digit'}});}},1000);
</script>"""
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title} · SecurityCyber</title><style>{CSS}</style></head><body>
<input type=checkbox id=navt class=navt>
<div class=wrap>
 <aside class=side>
  <div class=brandbox><div class=wm>{LOGO_SVG}<span>Security<span class=c>Cyber</span></span></div>
   <small>Admin Center</small></div>
  <nav class=nav>{nav_html}</nav>
  <div class=who>Operator&nbsp;·&nbsp;<b>{OPERATOR}</b><br>{greet}</div>
 </aside>
 <div class=col>
  <div class=topbar><label for=navt class=burger aria-label="Menu">{icon("menu")}</label><span class=beat></span>
   <span class=clk><time>{now.strftime('%H:%M:%S')}</time> BST</span>
   <span class=sp></span>{lock_ui}</div>
  <main class=main><h1>{title}</h1>{flash_html}{body}</main>
 </div>
</div>{idle_js}</body></html>"""


def lock_screen(next_url: str, bad: bool) -> str:
    err = '<p style="color:var(--bad);font-size:13px;margin:10px 0 0">That\'s not it. Try again.</p>' if bad else ''
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Locked · SecurityCyber</title><style>{CSS}
.lk{{min-height:100vh;display:grid;place-items:center;padding:20px}}
.lkbox{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:34px 34px 28px;
 width:100%;max-width:360px;box-shadow:var(--shadow);text-align:center}}
.lkbox .wm{{justify-content:center;font-size:19px;margin-bottom:6px}}
.lkbox p.d{{color:var(--faint);font-size:12.5px;margin:4px 0 20px}}
.lkbox input{{text-align:center;font-size:16px;letter-spacing:.05em;padding:12px}}
.lkbox .btn{{width:100%;margin-top:12px;padding:12px}}
</style></head><body>
<div class=lk><form class=lkbox method=post action=/unlock>
 <div class=wm>{LOGO_SVG}<span>Security<span class=c>Cyber</span></span></div>
 <p class=d>Say the word, {OPERATOR}.</p>
 <input type=password name=word autofocus autocomplete=off placeholder="passphrase">
 <input type=hidden name=next value="{next_url}">
 <button class=btn>Unlock</button>{err}
</form></div></body></html>"""
