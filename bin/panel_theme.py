"""SecurityCyber Admin Center — theme, brand mark, lock screen.

Imported by web/app.py. Keeps every legacy class name (.card .stat .tile .btn
.pill .flash .hero .tiles .bars .slots .tl …) so existing route bodies restyle
for free, and adds the branded shell + idle-lock UI.

Layout and visual language follow AlienVault OTX (navy top bar with
letter-spaced nav, light canvas, white panels, #001aff accent, uppercase
labels, zebra tables, outlined badges, navy footer), so moving between OTX
and this panel feels like one workspace. SecurityCyber branding only — no
OTX/LevelBlue names or marks. Colours were sampled from OTX's own pages.
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
  /* OTX palette (sampled): navy chrome, light canvas, royal-blue accent */
  --nav:#05052f;--nav2:#11114a;--navfg:#ffffff;--navdim:#b8bdd9;
  --bg:#f6f9fa;--bg2:#f6f9fa;--panel:#ffffff;--panel2:#f6f9fa;--line:#dee2e6;
  --fg:#212529;--dim:#5f6870;--faint:#7d868e;
  --accent:#001aff;--accent2:#0013c2;--cyber:#dc3545;
  --good:#1f8f3a;--warn:#b26a00;--bad:#c82333;--radius:4px;
  --shadow:0 1px 2px rgba(5,5,47,.06)}
*{box-sizing:border-box}
body{margin:0;color:var(--fg);background:var(--bg);
 font:14px/1.6 "DM Sans",ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 -webkit-font-smoothing:antialiased;display:flex;flex-direction:column;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* ---- top bar (OTX: navy, white brand, letter-spaced nav, utilities right) ---- */
.otx-top{background:var(--nav);color:var(--navfg);display:flex;align-items:center;gap:6px;
 padding:0 18px;height:52px;position:sticky;top:0;z-index:30}
.otx-top .wm{display:flex;align-items:center;gap:9px;font-weight:700;font-size:16px;color:#fff;
 margin-right:18px;white-space:nowrap}
.otx-top .wm .c{color:#ff6b78}
.otx-top .wm small{font-weight:500;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--navdim);margin-left:4px}
.nav{display:flex;align-items:stretch;height:52px}
.nav .dd{position:relative;display:flex}
.nav .dt{display:flex;align-items:center;gap:6px;padding:0 14px;color:#fff;font-size:13px;letter-spacing:1px;white-space:nowrap;
 cursor:pointer;background:none;border:0;font-family:inherit;border-bottom:3px solid transparent;border-top:3px solid transparent}
.nav .dt:after{content:"";border:4px solid transparent;border-top-color:var(--navdim);margin-top:4px}
.nav .dd.on .dt{border-bottom-color:var(--accent);color:#fff}
.nav .dd:hover .dt,.nav .dd:focus-within .dt{background:var(--nav2)}
.nav .dm{display:none;position:absolute;top:52px;left:0;min-width:220px;background:#fff;border:1px solid var(--line);
 border-top:3px solid var(--accent);box-shadow:0 8px 24px rgba(5,5,47,.18);padding:6px 0;z-index:40}
.nav .dd:hover .dm,.nav .dd:focus-within .dm{display:block}
.nav .dm a{display:flex;align-items:center;gap:10px;padding:8px 16px;color:var(--fg);font-size:13.5px;white-space:nowrap}
.nav .dm a .ico{color:var(--faint)}
.nav .dm a:hover{background:var(--panel2);color:var(--accent);text-decoration:none}
.nav .dm a:hover .ico{color:var(--accent)}
.nav .dm a.on{color:var(--accent);font-weight:700;box-shadow:inset 3px 0 0 var(--accent)}
.nav .dm a.on .ico{color:var(--accent)}
.otx-top .sp{flex:1}
.otx-top .util{display:flex;align-items:center;gap:14px;font-size:12.5px;letter-spacing:1px;color:var(--navdim);white-space:nowrap}
.otx-top .util .sep{opacity:.5}
.otx-top .beat{width:8px;height:8px;border-radius:50%;background:#3ddc84;animation:pulse 2.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(61,220,132,.55)}70%{box-shadow:0 0 0 8px transparent}100%{box-shadow:0 0 0 0 transparent}}
@media(prefers-reduced-motion:reduce){.otx-top .beat,.beacon{animation:none}}
.clk{font-variant-numeric:tabular-nums;color:#fff}
.idle b{color:#ffc857;font-variant-numeric:tabular-nums;font-weight:600}
.otx-top form{margin:0}
.lockbtn{background:transparent;border:0;color:#fff;font:inherit;font-size:12.5px;letter-spacing:1px;cursor:pointer;
 display:inline-flex;align-items:center;gap:6px;padding:4px 0}
.lockbtn:hover{color:#ffb3ba}
.burger{display:none;background:transparent;border:1px solid rgba(255,255,255,.3);color:#fff;border-radius:3px;
 padding:5px 7px;cursor:pointer;align-items:center;margin-right:8px}
.navt{display:none}

/* ---- page body: white panel on light canvas, like an OTX pulse page ---- */
.col{flex:1;display:flex;flex-direction:column}
.main{background:var(--panel);margin:0;padding:26px 34px 56px;flex:1;width:100%;max-width:1320px;align-self:center;
 border-left:1px solid var(--line);border-right:1px solid var(--line)}
h1{font-size:28px;font-weight:400;margin:0 0 6px;letter-spacing:.02em;color:var(--fg)}
h1+.sub-h{color:var(--faint);font-size:13px;margin:0 0 20px}
h2{font-size:12px;margin:28px 0 10px;color:var(--fg);text-transform:uppercase;letter-spacing:1px;font-weight:700}
.otx-foot{background:var(--nav);color:#fff;text-align:center;padding:14px 18px;font-size:11.5px;
 letter-spacing:.3em;text-transform:uppercase}
.otx-foot .sep{margin:0 14px;opacity:.6}

.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
 padding:16px 18px;margin-bottom:14px;box-shadow:var(--shadow)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;align-items:end}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
@media(max-width:920px){.grid2{grid-template-columns:1fr}}
.stat,.tile{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:14px;border-top:3px solid var(--line)}
.stat .n,.tile .n{font-weight:500;line-height:1.1;color:var(--fg);white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis;font-size:clamp(18px,1.8vw,26px)}
.stat .l,.tile .l{color:var(--dim);font-size:10.5px;margin-top:6px;text-transform:uppercase;letter-spacing:1px;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(134px,1fr));gap:10px;margin-bottom:16px}
.tile.good{border-top-color:#28a745}.tile.warn{border-top-color:#f0ad4e}.tile.bad{border-top-color:#dc3545}

/* tables: OTX indicator table — uppercase spaced headers, zebra rows */
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;vertical-align:top}
th{color:var(--fg);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:1px;
 border-bottom:2px solid var(--fg);white-space:nowrap}
td{border-bottom:1px solid transparent}
tbody tr:nth-child(even) td,table tr:nth-child(even) td{background:var(--panel2)}
tr:hover td{background:#eef1ff}
table:has(tbody tr) {border-bottom:1px solid var(--line)}

input:not([type]),input[type=text],input[type=url],input[type=number],input[type=password],input[type=email],input[type=datetime-local],input[type=date],input[type=search],textarea,select{
 width:100%;background:#fff;border:1px solid #b9c0c7;color:var(--fg);border-radius:2px;padding:8px 10px;font:inherit}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,26,255,.12)}
textarea{min-height:120px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
label{display:block;margin:12px 0 4px;font-size:11.5px;color:var(--fg);text-transform:uppercase;letter-spacing:1px;font-weight:600}
label small{color:var(--faint);text-transform:none;letter-spacing:0;font-weight:400}
/* checkbox/radio labels ("clear", toggles) read as plain text, not field headings */
label:has(>input[type=checkbox]),label:has(>input[type=radio]){text-transform:none;letter-spacing:0;font-weight:400;font-size:12.5px;color:var(--dim)}
input::placeholder{color:#9aa2a9}

/* buttons: OTX small solid blue rectangles */
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--accent);color:#fff;border:1px solid var(--accent);
 border-radius:2px;padding:7px 14px;font:inherit;font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--accent2);border-color:var(--accent2);text-decoration:none;color:#fff}
.btn.ghost{background:#fff;border:1px solid var(--accent);color:var(--accent)}
.btn.ghost:hover{background:#eef1ff;color:var(--accent2)}
.btn.good{background:#28a745;border-color:#28a745}.btn.good:hover{background:#1f8f3a}
.btn.bad{background:#dc3545;border-color:#dc3545}.btn.bad:hover{background:#c82333}
.btn.sm{padding:4px 9px;font-size:12px}
.btn .ico{width:15px;height:15px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}

/* pills: OTX outlined metadata badges (CREATED / MODIFIED / TLP) */
.pill{display:inline-block;padding:0 6px;border-radius:2px;font-size:10.5px;font-weight:600;letter-spacing:.06em;
 text-transform:uppercase;border:1px solid currentColor;line-height:17px}
.pill.good{color:#1f8f3a;background:#e7f5ea}
.pill.warn{color:#a45f00;background:#fff3e0}
.pill.bad{color:#c82333;background:#fdecee}
.pill.dim{color:var(--dim);background:#f1f3f5}
.flash{background:#eef1ff;border:1px solid #b3bcff;border-left:4px solid var(--accent);border-radius:2px;
 padding:10px 14px;margin-bottom:18px;font-size:13px}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:2px;padding:13px;overflow:auto;
 font-size:12.5px;white-space:pre-wrap;word-break:break-word}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.dim{color:var(--dim)}

/* hero: OTX's "endpoint security" strip — light band with an accent edge */
.hero{display:flex;flex-wrap:wrap;gap:18px;align-items:center;justify-content:space-between;
 background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--accent);
 border-radius:var(--radius);padding:16px 20px;margin-bottom:16px}
.hero .state{font-size:18px;font-weight:600;display:flex;align-items:center;gap:10px}
.hero .sub{color:var(--dim);font-size:12.5px;margin-top:4px}
.beacon{width:11px;height:11px;border-radius:50%;flex-shrink:0;animation:pulse 2.4s infinite}
.count{font-variant-numeric:tabular-nums;font-weight:700}

/* bars: OTX's solid multi-colour indicator charts */
.bars{display:flex;align-items:flex-end;gap:4px;height:64px;padding-top:4px}
.bars .b{flex:1;background:#e9edf0;min-height:3px;position:relative}
.bars .b.has{background:#4a3fbf}
.bars .b.has:nth-child(5n+1){background:#00b8c4}.bars .b.has:nth-child(5n+2){background:#4a3fbf}
.bars .b.has:nth-child(5n+3){background:#009a44}.bars .b.has:nth-child(5n+4){background:#d4c41c}
.bars .b.has:nth-child(5n){background:#14134a}
.bars .b span{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:10px;color:var(--faint)}
.slots{display:flex;gap:6px;flex-wrap:wrap}
.slot{padding:1px 8px;border-radius:2px;font-size:11px;font-weight:600;border:1px solid var(--line);color:var(--dim);letter-spacing:.04em}
.slot.past{opacity:.45}.slot.next{border-color:var(--accent);color:#fff;background:var(--accent)}
.tl{list-style:none;margin:0;padding:0}
.tl li{display:flex;gap:10px;padding:8px 6px;font-size:12.5px;align-items:baseline}
.tl li:nth-child(even){background:var(--panel2)}
.tl .t{color:var(--faint);white-space:nowrap;font-size:11.5px;min-width:64px}
.seg{display:inline-flex;border:1px solid var(--accent);border-radius:2px;overflow:hidden}
.seg label{margin:0;text-transform:none;letter-spacing:0}.seg span{padding:6px 12px;font-size:12px;cursor:pointer;color:var(--accent)}
.ico{width:16px;height:16px;flex-shrink:0}

/* OTX tab strip: active tab is a solid accent block, others plain accent text.
   Shared by the desk modules' own tab classes. */
.otx-tabs,.hn-tabs,.dw-tabs{display:flex;gap:0;flex-wrap:wrap;border-bottom:1px solid var(--line);margin:0 0 18px}
.otx-tabs a,.hn-tabs a,.dw-tabs a{padding:10px 20px;font-size:15px;color:var(--accent);border:0;border-radius:0;background:none}
.otx-tabs a.on,.hn-tabs a.on,.dw-tabs a.on{background:var(--accent);color:#fff}
.otx-tabs a:hover,.hn-tabs a:hover,.dw-tabs a:hover{text-decoration:none;background:#eef1ff}
.otx-tabs a.on:hover,.hn-tabs a.on:hover,.dw-tabs a.on:hover{background:var(--accent2)}

input[type=number]{-moz-appearance:textfield;appearance:textfield}
input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
input[type=checkbox],input[type=radio]{accent-color:var(--accent);width:15px;height:15px;vertical-align:-2px;margin:0 6px 0 0}
select{appearance:none;background-image:linear-gradient(45deg,transparent 50%,var(--dim) 50%),
 linear-gradient(135deg,var(--dim) 50%,transparent 50%);background-position:calc(100% - 17px) 55%,calc(100% - 12px) 55%;
 background-size:5px 5px;background-repeat:no-repeat;padding-right:32px}

@media(max-width:1100px){.otx-top .util .idle,.otx-top .util .sep{display:none}}
@media(max-width:900px){
 .burger{display:inline-flex}
 .nav{display:none;position:absolute;top:52px;left:0;right:0;height:auto;flex-direction:column;background:var(--nav);
  padding:6px 0 10px;box-shadow:0 8px 24px rgba(5,5,47,.3)}
 .navt:checked~.otx-top .nav{display:flex}
 .nav .dd{flex-direction:column}
 .nav .dt{height:auto;padding:10px 18px 4px;border:0;font-size:11px;color:var(--navdim);cursor:default}
 .nav .dt:after{display:none}
 .nav .dm{display:block;position:static;border:0;box-shadow:none;background:transparent;padding:0}
 .nav .dm a{color:#fff;padding:7px 28px}.nav .dm a .ico{color:var(--navdim)}
 .nav .dm a:hover,.nav .dm a.on{background:var(--nav2);color:#fff}
 .otx-top .util .clk{display:none}
 .main{padding:18px 16px 48px;border:0}
 h1{font-size:23px}
}
"""


def _greeting(hh: int) -> str:
    return ("Good evening" if hh >= 17 else "Good afternoon" if hh >= 12
            else "Good morning" if hh >= 5 else "Still up")


def nav_menu(groups, active: str, esc) -> str:
    """Top-bar dropdowns, one per NAV group (OTX-style). `groups` is
    [(group, [(path, label, icon), ...]), ...]; the group holding the active
    page is underlined in the bar. Dropdowns open on hover and on keyboard
    focus (:focus-within), so they work without JavaScript."""
    out = []
    for group, items in groups:
        on = any(p == active for p, _, _ in items)
        links = "".join(
            f'<a href="{p}" class="{"on" if p == active else ""}">{icon(ic)}<span>{esc(label)}</span></a>'
            for p, label, ic in items)
        out.append(f'<div class="dd{" on" if on else ""}"><button class=dt type=button>{esc(group)}</button>'
                   f'<div class=dm>{links}</div></div>')
    return "".join(out)


def shell(title, body, nav_html, flash_html, locked_enabled, now):
    """Full page. `now` is a datetime in the panel's local tz (BST)."""
    greet = f"{_greeting(now.hour)}, {OPERATOR}"
    lock_ui = (
        '<span class=sep>|</span><span class="idle">AUTO-LOCK <b id="idleT">10:00</b></span>'
        f'<span class=sep>|</span><form method="post" action="/lock"><button class="lockbtn">{icon("lock")} LOCK</button></form>'
    ) if locked_enabled else ('<span class=sep>|</span><span class="idle" title="No PANEL_PASSPHRASE set, so the panel never locks">'
            'UNLOCKED</span>')
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
<header class=otx-top>
 <label for=navt class=burger aria-label="Menu">{icon("menu")}</label>
 <div class=wm>{LOGO_SVG}<span>Security<span class=c>Cyber</span></span><small>Admin Center</small></div>
 <nav class=nav aria-label="Main">{nav_html}</nav>
 <span class=sp></span>
 <div class=util><span class=beat></span><span class=clk><time>{now.strftime('%H:%M:%S')}</time> BST</span>{lock_ui}</div>
</header>
<div class=col><main class=main><h1>{title}</h1>{flash_html}{body}</main></div>
<footer class=otx-foot>SecurityCyber Admin Center<span class=sep>|</span>Operator {OPERATOR}<span class=sep>|</span>{greet}</footer>
{idle_js}</body></html>"""


def lock_screen(next_url: str, bad: bool) -> str:
    err = '<p style="color:var(--bad);font-size:13px;margin:10px 0 0">That\'s not it. Try again.</p>' if bad else ''
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Locked · SecurityCyber</title><style>{CSS}
body{{background:var(--nav)}}
.lk{{min-height:100vh;display:grid;place-items:center;padding:20px}}
.lkbox{{background:#fff;border-top:4px solid var(--accent);border-radius:2px;padding:34px 34px 28px;
 width:100%;max-width:360px;box-shadow:0 20px 50px rgba(0,0,0,.35);text-align:center}}
.lkbox .wm{{display:flex;justify-content:center;align-items:center;gap:9px;font-weight:700;font-size:19px;margin-bottom:6px}}
.lkbox .wm .c{{color:var(--cyber)}}
.lkbox p.d{{color:var(--dim);font-size:11.5px;margin:4px 0 20px;text-transform:uppercase;letter-spacing:1px}}
.lkbox input{{text-align:center;font-size:16px;letter-spacing:.05em;padding:12px}}
.lkbox .btn{{width:100%;margin-top:12px;padding:11px;justify-content:center}}
</style></head><body>
<div class=lk><form class=lkbox method=post action=/unlock>
 <div class=wm>{LOGO_SVG}<span>Security<span class=c>Cyber</span></span></div>
 <p class=d>Say the word, {OPERATOR}.</p>
 <input type=password name=word autofocus autocomplete=off placeholder="passphrase">
 <input type=hidden name=next value="{next_url}">
 <button class=btn>Unlock</button>{err}
</form></div></body></html>"""
