#!/usr/bin/env python3
"""Public subscriber site for the Blue Team CTI alert service.

Separate ASGI app from the owner console (web/app.py). Deployed behind Caddy
(auto-TLS). No cookies. Signed URL tokens are the only auth. Every mutating
route is rate-limited; webhooks are signature-verified.

Run:  uvicorn web.public_app:app --host 127.0.0.1 --port 8080
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

import billing            # noqa: E402
import channels as CH     # noqa: E402
import ratelimit          # noqa: E402
import subsconfig         # noqa: E402
import subscribers as S   # noqa: E402
import tokens             # noqa: E402

LEGAL = ROOT / "docs" / "legal"
TERMS_VERSION = S.TERMS_VERSION

app = FastAPI(title="Blue Team CTI — alerts", docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------------------- helpers
def cfg() -> dict:
    return subsconfig.load()


def creds() -> dict:
    d = dict(os.environ)
    envf = ROOT / "config" / "env"
    if envf.exists():
        for ln in envf.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d.setdefault(k.strip(), v.strip())
    c = cfg()
    d["SUBS_FROM_EMAIL"] = c.get("from_email") or d.get("EMAIL_FROM", "")
    d["SUBS_REPLY_TO"] = c.get("reply_to") or c.get("support_email") or ""
    return d


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def client_ip(req: Request) -> str:
    xff = req.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return req.client.host if req.client else "?"


def rl(req: Request, bucket: str, limit: int | None = None) -> None:
    lim = limit or int(cfg().get("rate_limit_per_min", 20))
    key = f"{bucket}:{client_ip(req)}"
    if not ratelimit.allow(key, lim, 60):
        raise _TooMany(ratelimit.retry_after(key))


class _TooMany(Exception):
    def __init__(self, retry: int):
        self.retry = retry


@app.exception_handler(_TooMany)
def _rl_handler(request: Request, exc: _TooMany):
    return PlainTextResponse("Too many requests — slow down.", status_code=429,
                             headers={"Retry-After": str(exc.retry)})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; script-src 'self' 'unsafe-inline'; "
        "form-action 'self' https://checkout.stripe.com; frame-ancestors 'none'; base-uri 'none'")
    return resp


def notify_owner(msg: str) -> None:
    try:
        subprocess.run([sys.executable, str(ROOT / "bin" / "notify.py"), msg, "high"],
                       cwd=ROOT, timeout=15, capture_output=True)
    except Exception:  # noqa
        pass


def send_email(to: str, subject: str, body: str, list_unsub: str = "") -> dict:
    c = creds()
    if list_unsub:
        c = dict(c, _list_unsub=f"<{list_unsub}>")
    return CH.send("email", to, subject, body, creds=c)


# --------------------------------------------------------------------------- tiny markdown
def md_to_html(text: str) -> str:
    out, in_ul, in_tbl = [], False, False

    def inline(s: str) -> str:
        s = e(s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" rel="noopener">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    for raw in text.splitlines():
        ln = raw.rstrip()
        if in_ul and not ln.startswith(("- ", "* ")):
            out.append("</ul>")
            in_ul = False
        if in_tbl and "|" not in ln:
            out.append("</tbody></table>")
            in_tbl = False
        if not ln:
            continue
        if ln.startswith("### "):
            out.append(f"<h3>{inline(ln[4:])}</h3>")
        elif ln.startswith("## "):
            out.append(f"<h2>{inline(ln[3:])}</h2>")
        elif ln.startswith("# "):
            out.append(f"<h1>{inline(ln[2:])}</h1>")
        elif ln.startswith("---"):
            out.append("<hr>")
        elif ln.startswith(("- ", "* ")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(ln[2:])}</li>")
        elif "|" in ln:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_tbl:
                out.append("<table><tbody>")
                in_tbl = True
            tag = "th" if not any(t.startswith("<t") for t in out[-3:]) else "td"
            out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
        else:
            out.append(f"<p>{inline(ln)}</p>")
    if in_ul:
        out.append("</ul>")
    if in_tbl:
        out.append("</tbody></table>")
    return "\n".join(out)


def render_legal(name: str) -> str | None:
    f = LEGAL / f"{name}.md"
    if not f.exists():
        return None
    c = cfg()
    subs = {
        "{{brand}}": c.get("brand_name", "Blue Team CTI"),
        "{{legal_name}}": c.get("company_legal_name") or c.get("brand_name", "the operator"),
        "{{ico}}": c.get("ico_registration") or "(registration pending)",
        "{{contact_email}}": c.get("contact_email") or c.get("support_email") or "",
        "{{site}}": c.get("site_url", ""),
        "{{terms_version}}": TERMS_VERSION,
        "{{retention_cancelled}}": str(c.get("retention_days_cancelled", 180)),
        "{{retention_unconfirmed}}": str(c.get("retention_days_unconfirmed", 7)),
    }
    txt = f.read_text()
    for k, v in subs.items():
        txt = txt.replace(k, v)
    return md_to_html(txt)


# --------------------------------------------------------------------------- layout
# SecurityCyber brand system (securitycyber.uk) — dark, blue defensive accent,
# Syne / DM Sans / JetBrains Mono, the lightning "Z" mark.
LOGO_SVG = (
    '<svg viewBox="0 0 48 46" width="26" height="26" fill="none" aria-hidden="true">'
    '<path fill="#3b82f6" d="M25.946 44.938c-.664.845-2.021.375-2.021-.698V33.937a2.26 2.26 0 0 0-2.262-2.262'
    'H10.287c-.92 0-1.456-1.04-.92-1.788l7.48-10.471c1.07-1.497 0-3.578-1.842-3.578H1.237c-.92 0-1.456-1.04-.92-1.788'
    'L10.013.474c.214-.297.556-.474.92-.474h28.894c.92 0 1.456 1.04.92 1.788l-7.48 10.471c-1.07 1.498 0 3.579 1.842 3.579'
    'h11.377c.943 0 1.473 1.088.89 1.83L25.947 44.94z"/></svg>')

MAIN_SITE = "https://securitycyber.uk"

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;1,400&family=JetBrains+Mono:wght@400;500&display=swap');
:root{
--bg:#0d1422;--bg2:#121c2e;--bg3:#0a1120;--bg4:#162035;
--line:#1e2d45;--line-dim:rgba(30,45,69,.8);--line-hi:rgba(59,130,246,.3);
--ink:#e0e8f5;--dim:#7d92b0;--faint:#3a4f6a;--white:#f0f6ff;
--accent:#3b82f6;--accent-dark:#2563eb;--accent-dim:rgba(59,130,246,.12);
--brand:#dc2626;--good:#22c55e;--bad:#f87171;--warn:#fbbf24;
--display:'Syne',sans-serif;--body:'DM Sans',system-ui,sans-serif;--mono:'JetBrains Mono',monospace;
--radius:8px;--radius-lg:14px}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:15px/1.7 var(--body);-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none;transition:color .2s}a:hover{color:var(--accent-dark)}
.wrap{max-width:780px;margin:0 auto;padding:0 20px 72px}
nav{display:flex;align-items:center;justify-content:space-between;height:66px;margin-bottom:28px;
border-bottom:1px solid var(--line-dim)}
nav .brand{display:flex;align-items:center;gap:9px;font-family:var(--display);font-weight:800;
font-size:17px;color:var(--white);letter-spacing:-.01em}
nav .brand b span{color:var(--brand);font-weight:800}
nav .links a{font-size:.86rem;color:var(--dim);margin-left:18px}nav .links a:hover{color:var(--white)}
h1{font-family:var(--display);font-weight:800;font-size:31px;line-height:1.18;letter-spacing:-.02em;
color:var(--white);margin:.4em 0}
h2{font-family:var(--display);font-weight:700;font-size:19px;color:var(--white);margin:1.8em 0 .6em}
h3{font-family:var(--display);font-weight:700;font-size:15px;color:var(--white);margin:0 0 .5em}
p{margin:.6em 0}
.lede{font-size:17px;color:var(--dim);line-height:1.6}
.muted{color:var(--dim);font-size:14px}
.card{background:var(--bg2);border:1px solid var(--line-dim);border-radius:var(--radius-lg);
padding:22px;margin:16px 0}
.tiers{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0}
@media(max-width:640px){.tiers{grid-template-columns:1fr}}
.tier{background:var(--bg2);border:1px solid var(--line-dim);border-radius:var(--radius-lg);padding:22px;
position:relative;overflow:hidden}
.tier::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--accent);
transform:scaleX(0);transform-origin:left;transition:transform .3s}
.tier:hover::before{transform:scaleX(1)}
.tier .p1{font-family:var(--display);font-size:24px;font-weight:800;color:var(--white)}
.tier .p2{font-size:13px;color:var(--dim)}
.tier ul{list-style:none;margin:12px 0;font-size:13.5px;color:var(--dim)}
.tier ul li{padding:3px 0 3px 18px;position:relative}
.tier ul li::before{content:'▸';position:absolute;left:0;color:var(--accent)}
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--accent);color:#fff;border:0;
border-radius:var(--radius);padding:11px 24px;font:600 .9rem var(--body);cursor:pointer;text-decoration:none;
transition:all .2s;box-shadow:0 4px 16px rgba(59,130,246,.22)}
.btn:hover{background:var(--accent-dark);color:#fff;transform:translateY(-2px);box-shadow:0 8px 24px rgba(59,130,246,.32)}
.btn.ghost{background:var(--accent-dim);color:var(--ink);border:1px solid var(--line-hi);box-shadow:none}
.btn.ghost:hover{background:rgba(59,130,246,.16);color:var(--white)}
.btn.bad{background:transparent;border:1px solid var(--bad);color:var(--bad);box-shadow:none}
.btn.bad:hover{background:rgba(248,113,113,.1);color:var(--bad)}
label{display:block;margin:15px 0 5px;font-weight:600;font-size:13.5px;color:var(--ink)}
label small{font-weight:400;color:var(--dim)}
input[type=text],input[type=email],input[type=url],input[type=number],textarea,select{
width:100%;background:var(--bg3);border:1px solid var(--line);color:var(--ink);border-radius:var(--radius);
padding:10px 12px;font:14px var(--body)}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.seg label{margin:0;cursor:pointer}
.seg label span{display:block;padding:9px 16px;font-size:13.5px;font-weight:600;color:var(--dim)}
.seg input{position:absolute;opacity:0}
.seg input:checked + span{background:var(--accent);color:#fff}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:650;
border:1px solid var(--line);color:var(--dim)}
.pill.good{color:var(--good);border-color:rgba(34,197,94,.4)}
.pill.bad{color:var(--bad);border-color:rgba(248,113,113,.4)}
.flash{background:var(--accent-dim);border:1px solid var(--line-hi);border-radius:var(--radius);
padding:12px 15px;margin:14px 0;font-size:14px}
.flash.bad{background:rgba(248,113,113,.1);border-color:rgba(248,113,113,.4)}
.flash.good{background:rgba(34,197,94,.1);border-color:rgba(34,197,94,.4)}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:10px 0}
td,th{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
code{background:var(--bg3);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font:.85em var(--mono)}
hr{border:0;border-top:1px solid var(--line-dim);margin:24px 0}
footer{margin-top:52px;padding-top:22px;border-top:1px solid var(--line-dim);color:var(--faint);font-size:12.5px}
footer .fb{display:flex;align-items:center;gap:8px;font-family:var(--display);font-weight:700;
color:var(--white);margin-bottom:8px}
footer .fb b span{color:var(--brand)}
footer a{color:var(--dim)}footer a:hover{color:var(--accent)}
"""


def page(title: str, body: str, flash: str = "", flash_kind: str = "") -> HTMLResponse:
    c = cfg()
    fl = f'<div class="flash {flash_kind}">{e(flash)}</div>' if flash else ""
    foot_ico = f' · ICO registration {e(c["ico_registration"])}' if c.get("ico_registration") else ""
    legal_name = c.get("company_legal_name") or "SecurityCyber"
    contact = c.get("contact_email") or c.get("support_email") or "charlie@securitycyber.uk"
    return HTMLResponse(f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="index,follow">
<meta name=color-scheme content="dark">
<title>{e(title)} · SecurityCyber CTI</title>
<link rel=icon href="/logo.svg">
<style>{CSS}</style></head><body><div class=wrap>
<nav>
  <a class=brand href="/">{LOGO_SVG}<b>Security<span>Cyber</span></b></a>
  <span class=links><a href="{MAIN_SITE}">Main site</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a></span>
</nav>
{fl}{body}
<footer>
<div class=fb>{LOGO_SVG}<b>Security<span>Cyber</span></b> · CTI Alerts</div>
{e(legal_name)}{foot_ico} · Independent UK cyber-security advisory.<br>
<a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/cookies">Cookies</a> ·
<a href="mailto:{e(contact)}">{e(contact)}</a> · <a href="{MAIN_SITE}">securitycyber.uk</a>
</footer>
</div></body></html>""")


@app.get("/logo.svg")
def logo_svg():
    return Response(LOGO_SVG, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


def flash_from(req: Request) -> tuple[str, str]:
    return req.query_params.get("m", ""), req.query_params.get("k", "")


# --------------------------------------------------------------------------- landing
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    c = cfg()
    if not c.get("enabled"):
        return page("Not open yet",
                    "<h1>Not open yet</h1><p class=lede>The Blue Team CTI alert service "
                    "isn't taking subscribers at the moment. Check back soon.</p>")
    m, k = flash_from(request)
    tiers_html = ""
    for name in ("individual", "team"):
        t = c["tiers"].get(name, {})
        chans = ", ".join(_chan_label(x) for x in t.get("channels", []))
        has_wk = bool(t.get("weekly_price_id"))
        has_mo = bool(t.get("monthly_price_id"))
        wk, mo = t.get("weekly_display", ""), t.get("monthly_display", "")
        if has_mo and has_wk:
            price = f"{e(mo)} <span class=p2>or {e(wk)}</span>"
        elif has_wk and not has_mo:
            price = e(wk)
        else:
            price = e(mo)
        tiers_html += f"""<div class=tier>
<h3>{e(name.title())}</h3>
<div class=p1>{price}</div>
<p class=muted style="margin-top:8px">{e(t.get('blurb',''))}</p>
<ul>
<li>Instant alerts at impact score ≥ {e(t.get('instant_min_score','?'))}</li>
<li>{'Daily digest of the last 24 hours' if t.get('daily_digest') else 'Instant only'}</li>
{'<li>Machine-readable JSON attached to every alert</li>' if t.get('attach_json') else ''}
<li>Send to: {chans}</li>
</ul>
<a class=btn href="/subscribe?tier={name}">Choose {name.title()}</a>
</div>"""
    body = f"""
<h1>Vulnerability alerts that tell you what to do</h1>
<p class=lede>We watch vendor advisories, the CISA KEV catalog and reputable
security research, cut the noise, and send you only the ones that matter for
defenders — each with the CVE, what's affected, and the single action to take.</p>

<div class=tiers>{tiers_html}</div>
<p class=muted>Weekly or monthly billing, cancel anytime. Pick your platform —
Discord, Slack, Telegram, email, a webhook or ntfy — during sign-up.</p>

<h2>How it works</h2>
<div class=card>
<p>Every few hours an automated pipeline pulls the major security feeds,
cross-references the CISA Known Exploited Vulnerabilities catalog, removes
near-duplicates, and scores each story for real-world impact. Anything that
clears your threshold lands on your chosen platform within minutes — with the
CVE, CVSS, affected versions and the fix.</p>
<p class=muted>Informational only. It aggregates public advisories and is not a
substitute for your own vulnerability management. See the <a href="/terms">terms</a>.</p>
</div>

<h2>Already subscribed?</h2>
<div class=card>
<form method=post action="/account">
<label>Your email <small>— we'll send your preference link</small></label>
<input type=email name=email required placeholder="you@example.com">
<div class=row style="margin-top:12px"><button class="btn ghost">Email me my link</button></div>
</form>
</div>
"""
    return page("Vulnerability alerts for defenders", body, m, k)


_CHAN_META = {
    "email": ("Email", "the address to send alerts to", "you@example.com"),
    "discord": ("Discord", "a channel webhook URL (Server Settings → Integrations → Webhooks → New Webhook → Copy URL)",
                "https://discord.com/api/webhooks/…"),
    "slack": ("Slack", "an incoming-webhook URL (api.slack.com/messaging/webhooks)",
              "https://hooks.slack.com/services/…"),
    "telegram": ("Telegram", "nothing here — after payment you'll get a one-tap link to our bot", ""),
    "webhook": ("Webhook", "an https:// URL we POST JSON to", "https://example.com/hook"),
    "ntfy": ("ntfy", "a full topic URL", "https://ntfy.sh/my-topic"),
}


def _chan_label(k: str) -> str:
    return _CHAN_META.get(k, (k.title(),))[0]


@app.post("/account")
def account_link(request: Request, email: str = Form("")):
    rl(request, "account", 5)
    sub = S.get_by_email(email.strip().lower())
    # always respond the same way (don't leak who's a subscriber)
    if sub and sub["status"] in ("active", "paused", "past_due", "unconfirmed"):
        t = tokens.sign({"sub": sub["id"], "act": "manage"}, ttl=90 * 86400)
        url = f"{cfg().get('site_url', '')}/manage?t={t}"
        send_email(sub["email"], "Your Blue Team CTI preference link",
                   f"Manage your alert preferences or unsubscribe:\n\n{url}\n\n"
                   f"If you didn't ask for this, ignore it.",
                   list_unsub=f"{cfg().get('site_url','')}/unsubscribe?t="
                              + tokens.sign({'sub': sub['id'], 'act': 'unsubscribe'}, ttl=90 * 86400))
    return RedirectResponse("/?m=" + _q("If that address has an account, a link is on its way."), 303)


# --------------------------------------------------------------------------- subscribe / stripe
def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


@app.get("/subscribe", response_class=HTMLResponse)
def subscribe_form(request: Request, tier: str = "individual"):
    rl(request, "subscribe", 20)
    c = cfg()
    if not c.get("enabled") or tier not in c["tiers"]:
        return RedirectResponse("/?m=" + _q("That plan isn't available."), 303)
    m, k = flash_from(request)
    t = c["tiers"][tier]
    allowed = [x for x in t.get("channels", []) if x in _CHAN_META]
    has_wk, has_mo = bool(t.get("weekly_price_id")), bool(t.get("monthly_price_id"))
    if not (has_wk or has_mo):
        return page("Almost there",
                    "<h1>Checkout isn't switched on yet</h1>"
                    "<p class=lede>Billing for this plan is being finalised — check back shortly, "
                    "or use <a href='/'>“Already subscribed?”</a> if you have an account.</p>")

    cad_opts = ""
    for cad, avail, disp in (("monthly", has_mo, t.get("monthly_display", "")),
                             ("weekly", has_wk, t.get("weekly_display", ""))):
        if not avail:
            continue
        chk = " checked" if (cad == "monthly" and has_mo) or (cad == "weekly" and not has_mo) else ""
        cad_opts += (f'<label><input type=radio name=cadence value="{cad}"{chk}>'
                     f'<span>{e(disp)}</span></label>')

    plat_opts = "".join(
        f'<option value="{p}" data-hint="{e(_CHAN_META[p][1])}" data-ph="{e(_CHAN_META[p][2])}">'
        f'{e(_CHAN_META[p][0])}</option>' for p in allowed)

    contact = c.get("contact_email") or "charlie@securitycyber.uk"
    body = f"""
<h1>Subscribe — {tier.title()}</h1>
<p class=lede>{e(t.get('blurb',''))}</p>
<form method=post action="/subscribe" id=sf>
<input type=hidden name=tier value="{tier}">

<label>Billing</label>
<div class=seg>{cad_opts}</div>

<label>Your email <small>— your account + where the confirmation goes</small></label>
<input type=email name=email required placeholder="you@example.com">

<label>Send alerts to</label>
<select name=platform id=plat>{plat_opts}</select>
<p class=muted id=hint style="margin:6px 0 0"></p>
<input type=text name=target id=tgt style="margin-top:8px">

<label style="font-weight:400;margin-top:18px" class=row>
<input type=checkbox name=agree required style="width:auto">
&nbsp;I agree to the <a href="/terms" target=_blank>Terms</a> and
<a href="/privacy" target=_blank>Privacy Policy</a>, and I want alerts to start straight away.
</label>

<div class=row style="margin-top:16px">
<button class=btn>Continue to payment →</button>
<a class="btn ghost" href="/">Back</a>
</div>
<p class=muted style="margin-top:10px">Card details are handled by Stripe — they never touch our servers.
Questions: <a href="mailto:{e(contact)}">{e(contact)}</a>.</p>
</form>
<script>
(function(){{
var sel=document.getElementById('plat'),hint=document.getElementById('hint'),tgt=document.getElementById('tgt');
function upd(){{var o=sel.options[sel.selectedIndex];hint.textContent=o.getAttribute('data-hint')||'';
var ph=o.getAttribute('data-ph')||'';tgt.placeholder=ph;
if(sel.value==='telegram'){{tgt.style.display='none';tgt.required=false;}}else{{tgt.style.display='';tgt.required=true;}}}}
sel.addEventListener('change',upd);upd();
}})();
</script>
"""
    return page(f"Subscribe — {tier.title()}", body, m, k)


@app.post("/subscribe")
def subscribe_submit(request: Request, tier: str = Form("individual"), cadence: str = Form("monthly"),
                     email: str = Form(""), platform: str = Form("email"), target: str = Form(""),
                     agree: str = Form("")):
    rl(request, "subscribe", 10)
    c = cfg()
    email = email.strip().lower()
    if not c.get("enabled") or tier not in c["tiers"]:
        return RedirectResponse("/?m=" + _q("That plan isn't available."), 303)
    if not agree:
        return RedirectResponse(f"/subscribe?tier={tier}&m=" + _q("Please accept the terms to continue.") + "&k=bad", 303)
    if "@" not in email or "." not in email.split("@")[-1]:
        return RedirectResponse(f"/subscribe?tier={tier}&m=" + _q("That email doesn't look right.") + "&k=bad", 303)
    cadence = cadence if cadence in ("monthly", "weekly") else "monthly"
    tcfg = c["tiers"][tier]
    if platform not in tcfg.get("channels", []):
        platform = "email"
    ch_target = ""
    if platform == "email":
        ch_target = email
    elif platform != "telegram":
        ok, why = CH.verify_target(platform, target)
        if not ok:
            return RedirectResponse(f"/subscribe?tier={tier}&m=" + _q(why) + "&k=bad", 303)
        ch_target = target.strip()

    intent_tok = tokens.new_id()
    S.create_intent(intent_tok, email, tier, cadence,
                    None if platform == "telegram" else platform,
                    ch_target or None, ip=client_ip(request),
                    user_agent=request.headers.get("user-agent", ""))
    S.log_consent(None, "terms", True, ip=client_ip(request),
                  user_agent=request.headers.get("user-agent", ""), terms_version=TERMS_VERSION,
                  detail=f"accepted at signup ({email})")

    site = c.get("site_url", "")
    try:
        sess = billing.create_checkout_session(
            tier, cadence, email,
            success_url=f"{site}/welcome",
            cancel_url=f"{site}/subscribe?tier={tier}&m=" + _q("Checkout cancelled — no charge made."),
            client_reference=intent_tok)
    except billing.StripeError as ex:
        return page("Checkout error",
                    f"<h1>Couldn't start checkout</h1><p class=muted>{e(str(ex))}</p>"
                    f"<p><a href='/subscribe?tier={tier}'>Try again</a></p>")
    return RedirectResponse(sess["url"], 303)


@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request):
    return page("Payment received",
                "<h1>Payment received — one more step</h1>"
                "<p class=lede>Check your inbox for a confirmation email and click the link "
                "to switch your alerts on. It should arrive within a minute.</p>"
                "<p class=muted>Didn't get it? Check spam, or use "
                "<a href='/'>“Already subscribed?”</a> on the home page to resend.</p>")


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook(payload, sig)
    except billing.WebhookError as ex:
        return JSONResponse({"error": str(ex)}, status_code=400)
    try:
        res = billing.handle_event(event)
    except Exception as ex:  # noqa — never 500 a webhook; log + 200 so Stripe doesn't hammer
        notify_owner(f"CTI: Stripe webhook handler error: {ex}")
        return JSONResponse({"received": True, "handler_error": str(ex)[:200]})

    act = res.get("action")
    if act == "send_confirm":
        sid, email, tier = res["subscriber_id"], res["email"], res["tier"]
        cad = res.get("cadence", "monthly")
        ch = res.get("channel")
        t = tokens.sign({"sub": sid, "act": "confirm"}, ttl=14 * 86400)
        site = cfg().get("site_url", "")
        where = (_chan_label(ch["kind"]) if ch else "email")
        send_email(email, "Confirm your SecurityCyber CTI alerts",
                   f"Thanks for subscribing — {tier.title()} plan, billed {cad}.\n\n"
                   f"Confirm your email to switch your alerts on:\n\n"
                   f"{site}/confirm?t={t}\n\n"
                   f"Your alerts will go to: {where}.\n"
                   f"This link works for 14 days. If you didn't subscribe, ignore this "
                   f"and email {cfg().get('contact_email','') or 'charlie@securitycyber.uk'} — "
                   f"you won't be charged again.")
        S.log_event("confirm_email_sent", sid, {"cadence": cad, "channel": ch["kind"] if ch else "email"})
    elif act == "notify_owner":
        notify_owner("CTI billing: " + res.get("detail", "payment issue"))
    elif act in ("cancelled", "recovered"):
        notify_owner(f"CTI billing: subscriber {act}")
    return JSONResponse({"received": True, "action": act})


# --------------------------------------------------------------------------- confirm
@app.get("/confirm", response_class=HTMLResponse)
def confirm(request: Request, t: str = ""):
    rl(request, "confirm", 15)
    try:
        data = tokens.verify(t, "confirm")
    except tokens.BadToken as ex:
        return page("Link problem", f"<h1>That link didn't work</h1><p class=muted>{e(str(ex))}. "
                    "Use “Already subscribed?” on the home page to get a fresh one.</p>")
    sid = int(data["sub"])
    sub = S.get(sid)
    if not sub:
        return page("Not found", "<h1>Account not found</h1><p>It may have been removed.</p>")
    note = ""
    if sub["status"] not in ("cancelled",):
        S.set_status(sid, "active", "email confirmed", ip=client_ip(request))
        S.log_consent(sid, "signup", True, ip=client_ip(request),
                      user_agent=request.headers.get("user-agent", ""), terms_version=TERMS_VERSION,
                      detail="double opt-in via /confirm")
        # test-ping the channel they picked at signup so it lands verified
        for ch in sub["channels"]:
            if ch["kind"] == "email" or ch["verified"]:
                continue
            r = CH.send(ch["kind"], ch["target"], "SecurityCyber CTI — you're connected",
                        "This channel is now linked. Real alerts will arrive here.",
                        creds=creds(), priority="default")
            if r["ok"]:
                S.verify_channel(ch["id"])
            else:
                note = f" We couldn't reach your {_chan_label(ch['kind'])} — check it on the next page."
        notify_owner(f"CTI: new subscriber confirmed ({sub['tier']}, {sub.get('cadence','monthly')})")
    mtok = tokens.sign({"sub": sid, "act": "manage"}, ttl=90 * 86400)
    return RedirectResponse(f"/manage?t={mtok}&m=" + _q("You're in — alerts are live." + note)
                            + ("&k=bad" if note else "&k=good"), 303)


# --------------------------------------------------------------------------- manage
def _manage_sub(t: str) -> tuple[dict | None, str]:
    try:
        data = tokens.verify(t, "manage")
    except tokens.BadToken:
        return None, ""
    return S.get(int(data["sub"])), t


@app.get("/manage", response_class=HTMLResponse)
def manage(request: Request, t: str = ""):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return page("Link problem", "<h1>Link expired</h1><p class=muted>Use "
                    "“Already subscribed?” on the home page for a fresh link.</p>")
    m, k = flash_from(request)
    c = cfg()
    tier_cfg = c["tiers"].get(sub["tier"], {})
    prefs = sub["prefs"]
    allowed = tier_cfg.get("channels", [])
    site = c.get("site_url", "")

    ch_rows = ""
    for ch in sub["channels"]:
        v = '<span class="pill good">verified</span>' if ch["verified"] else '<span class="pill bad">unverified</span>'
        rm = (f'<form method=post action="/manage/channel/remove" style="display:inline">'
              f'<input type=hidden name=t value="{e(tok)}"><input type=hidden name=cid value="{ch["id"]}">'
              f'<button class="btn ghost" style="padding:4px 10px;font-size:13px">remove</button></form>'
              ) if ch["kind"] != "email" else ""
        ch_rows += f"<tr><td>{e(ch['kind'])}</td><td class=muted>{e(_short(ch['target']))}</td><td>{v} {rm}</td></tr>"

    add_opts = "".join(
        f'<option value="{x}" data-hint="{e(_CHAN_META.get(x, (x,"",""))[1])}" '
        f'data-ph="{e(_CHAN_META.get(x, (x,"",""))[2])}">{e(_chan_label(x))}</option>'
        for x in allowed if x not in ("email", "telegram"))
    unsub_link = f"{site}/unsubscribe?t=" + tokens.sign({"sub": sub["id"], "act": "unsubscribe"}, ttl=90 * 86400)

    portal_btn = ""
    if sub.get("stripe_customer_id"):
        portal_btn = (f'<form method=post action="/manage/portal"><input type=hidden name=t value="{e(tok)}">'
                      f'<button class="btn ghost">Manage billing / payment method</button></form>')

    body = f"""
<h1>Your alert preferences</h1>
<p class=muted>Plan: <b>{e(sub['tier'].title())}</b> ({e(sub.get('cadence','monthly'))}) ·
Status: <b>{e(sub['status'])}</b> · {e(sub['email'])}</p>

<h2>Channels</h2>
<div class=card>
<table><tbody><tr><th>type</th><th>where</th><th></th></tr>{ch_rows}</tbody></table>
<form method=post action="/manage/channel/add" style="margin-top:12px" id=af>
<input type=hidden name=t value="{e(tok)}">
<div class=row>
<select name=kind id=ak style="max-width:150px">{add_opts}</select>
<input type=text name=target id=at style="flex:1;min-width:200px">
<button class=btn>Add</button>
</div>
<p class=muted id=ah style="margin-top:8px"></p>
<p class=muted>For <b>Telegram</b>, <a href="/channel/telegram?t={e(tok)}">use the one-tap bot link →</a></p>
</form>
<script>
(function(){{var s=document.getElementById('ak'),h=document.getElementById('ah'),t=document.getElementById('at');
function u(){{var o=s.options[s.selectedIndex];h.textContent=o.getAttribute('data-hint')||'';
t.placeholder=o.getAttribute('data-ph')||'';}}s.addEventListener('change',u);u();}})();
</script>
</div>

<h2>What you get alerted about</h2>
<div class=card>
<form method=post action="/manage/prefs">
<input type=hidden name=t value="{e(tok)}">
<label>Vendors / keywords <small>— comma-separated. Leave blank for everything. e.g. Fortinet, Cisco, VMware</small></label>
<input type=text name=vendors value="{e(', '.join(prefs['vendors']))}">
<label>Only alert me at score ≥ <small>— blank = your plan default ({e(tier_cfg.get('instant_min_score','?'))})</small></label>
<input type=text name=min_score value="{e('' if prefs['min_score_override'] is None else prefs['min_score_override'])}" style="max-width:120px">
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=instant {'checked' if prefs['instant'] else ''}> Instant alerts</label>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=digest {'checked' if prefs['digest'] else ''}> Daily digest</label>
<div style="margin-top:12px"><button class=btn>Save</button></div>
</form>
</div>

<h2>Account</h2>
<div class=card>
<div class=row>
<form method=post action="/manage/test"><input type=hidden name=t value="{e(tok)}">
<button class="btn ghost">Send me a test alert</button></form>
{portal_btn}
<a class="btn ghost" href="/manage/export?t={e(tok)}">Download my data</a>
<a class="btn ghost" href="{e(unsub_link)}">Pause all alerts</a>
</div>
<form method=post action="/manage/delete" style="margin-top:14px"
 onsubmit="return confirm('Delete your account and all your data? If you have a paid subscription this also cancels it. This cannot be undone.')">
<input type=hidden name=t value="{e(tok)}">
<button class="btn bad">Delete my account &amp; data</button>
</form>
</div>
"""
    return page("Preferences", body, m, k)


def _short(s: str) -> str:
    s = s or ""
    if len(s) <= 44:
        return s
    return s[:28] + "…" + s[-10:]


@app.post("/manage/prefs")
def manage_prefs(request: Request, t: str = Form(""), vendors: str = Form(""),
                 min_score: str = Form(""), instant: str = Form(""), digest: str = Form("")):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return RedirectResponse("/?m=" + _q("Link expired"), 303)
    vlist = [v.strip() for v in re.split(r"[,\n]", vendors) if v.strip()][:40]
    mso = None
    if min_score.strip().isdigit():
        mso = max(0, min(100, int(min_score.strip())))
    S.set_prefs(sub["id"], vendors=vlist, min_score_override=mso,
                instant=bool(instant), digest=bool(digest))
    return RedirectResponse(f"/manage?t={tok}&m=" + _q("Saved.") + "&k=good", 303)


@app.post("/manage/channel/add")
def manage_channel_add(request: Request, t: str = Form(""), kind: str = Form(""), target: str = Form("")):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return RedirectResponse("/?m=" + _q("Link expired"), 303)
    tier_cfg = cfg()["tiers"].get(sub["tier"], {})
    if kind not in tier_cfg.get("channels", []) or kind == "telegram":
        return RedirectResponse(f"/manage?t={tok}&m=" + _q("That channel isn't available on your plan.") + "&k=bad", 303)
    if len([c for c in sub["channels"] if c["kind"] != "email"]) >= int(tier_cfg.get("max_channels", 2)):
        return RedirectResponse(f"/manage?t={tok}&m=" + _q("You've reached your plan's channel limit.") + "&k=bad", 303)
    ok, why = CH.verify_target(kind, target)
    if not ok:
        return RedirectResponse(f"/manage?t={tok}&m=" + _q(why) + "&k=bad", 303)
    # test-ping non-email channels before marking verified
    r = CH.send(kind, target.strip(), "Blue Team CTI — channel connected",
                "This channel is now connected. You'll receive alerts here.", creds=creds(), priority="default")
    S.add_channel(sub["id"], kind, target.strip(), verified=r["ok"])
    msg = "Channel added and test message delivered." if r["ok"] else f"Added, but the test failed: {r['error']}"
    return RedirectResponse(f"/manage?t={tok}&m=" + _q(msg) + ("&k=good" if r["ok"] else "&k=bad"), 303)


@app.post("/manage/channel/remove")
def manage_channel_remove(request: Request, t: str = Form(""), cid: int = Form(0)):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return RedirectResponse("/?m=" + _q("Link expired"), 303)
    if any(c["id"] == cid and c["kind"] != "email" for c in sub["channels"]):
        S.remove_channel(cid)
    return RedirectResponse(f"/manage?t={tok}&m=" + _q("Channel removed."), 303)


@app.post("/manage/test")
def manage_test(request: Request, t: str = Form("")):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return RedirectResponse("/?m=" + _q("Link expired"), 303)
    subprocess.Popen([sys.executable, str(ROOT / "bin" / "deliver.py"), "--test", str(sub["id"])],
                     cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return RedirectResponse(f"/manage?t={tok}&m=" + _q("Test alert sent to your verified channels.") + "&k=good", 303)


@app.post("/manage/portal")
def manage_portal(request: Request, t: str = Form("")):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub or not sub.get("stripe_customer_id"):
        return RedirectResponse("/?m=" + _q("No billing account found"), 303)
    try:
        p = billing.portal_session(sub["stripe_customer_id"], f"{cfg().get('site_url','')}/manage?t={tok}")
    except billing.StripeError as ex:
        return RedirectResponse(f"/manage?t={tok}&m=" + _q(f"Billing portal error: {ex}") + "&k=bad", 303)
    return RedirectResponse(p["url"], 303)


@app.get("/manage/export")
def manage_export(request: Request, t: str = ""):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return PlainTextResponse("Link expired", status_code=403)
    data = S.export_subscriber(sub["id"])
    S.log_event("dsar_export", sub["id"], ip=client_ip(request))
    return Response(json.dumps(data, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=my-blueteamcti-data.json"})


@app.post("/manage/delete", response_class=HTMLResponse)
def manage_delete(request: Request, t: str = Form("")):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return page("Link problem", "<h1>Link expired</h1>")
    if sub.get("stripe_subscription_id"):
        try:
            billing.cancel_subscription(sub["stripe_subscription_id"])
        except billing.StripeError as ex:
            notify_owner(f"CTI: could not cancel Stripe sub on delete for #{sub['id']}: {ex}")
            return page("Delete blocked",
                        "<h1>Delete blocked</h1>"
                        "<p class=lede>We couldn't cancel the active Stripe subscription, so we kept your account "
                        "record intact to avoid billing or data loss.</p>"
                        "<p class=muted>Please use the billing portal or contact support to resolve the subscription "
                        "before deleting your account.</p>"
                        "<p><a href='/manage?t=" + e(tok) + "'>Back to preferences</a></p>")
    S.erase_subscriber(sub["id"], "self-service deletion")
    notify_owner("CTI: a subscriber deleted their account")
    return page("Deleted",
                "<h1>Your account is deleted</h1><p class=lede>All your personal data has been "
                "removed and any subscription cancelled. Sorry to see you go.</p>"
                "<p><a href='/'>Home</a></p>")


# --------------------------------------------------------------------------- unsubscribe
@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(request: Request, t: str = ""):
    try:
        data = tokens.verify(t, "unsubscribe")
    except tokens.BadToken:
        return page("Link problem", "<h1>That unsubscribe link didn't work</h1>"
                    "<p class=muted>Email us and we'll sort it.</p>")
    sub = S.get(int(data["sub"]))
    if sub and sub["status"] in ("active", "past_due"):
        S.set_status(sub["id"], "paused", "unsubscribed via link", ip=client_ip(request))
        S.log_consent(sub["id"], "signup", False, ip=client_ip(request),
                      user_agent=request.headers.get("user-agent", ""), detail="unsubscribe link")
    mtok = tokens.sign({"sub": sub["id"], "act": "manage"}, ttl=90 * 86400) if sub else ""
    return page("Alerts paused",
                "<h1>Alerts paused</h1>"
                "<p class=lede>You won't get any more alerts. Your subscription and billing are "
                "unchanged.</p>"
                + (f"<p><a class=btn href='/manage?t={e(mtok)}'>Resume or manage billing</a></p>" if mtok else ""))


# --------------------------------------------------------------------------- telegram linking
@app.get("/channel/telegram", response_class=HTMLResponse)
def telegram_link(request: Request, t: str = ""):
    rl(request, "manage", 60)
    sub, tok = _manage_sub(t)
    if not sub:
        return page("Link problem", "<h1>Link expired</h1>")
    bot = os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    if not bot:
        return page("Telegram not set up", "<h1>Telegram isn't available yet</h1>"
                    "<p class=muted>The operator hasn't configured the alert bot.</p>")
    link_tok = tokens.sign({"sub": sub["id"], "act": "tg_link"}, ttl=3600)
    url = f"https://t.me/{bot}?start={link_tok}"
    return page("Connect Telegram",
                f"<h1>Connect Telegram</h1>"
                f"<p class=lede>Open the bot and press <b>Start</b> — you'll be linked automatically.</p>"
                f"<p><a class=btn href='{e(url)}' rel=noopener>Open @{e(bot)}</a></p>"
                f"<p class=muted>Link valid for 1 hour. Then return to "
                f"<a href='/manage?t={e(tok)}'>your preferences</a>.</p>")


@app.post("/webhooks/telegram")
async def telegram_webhook(request: Request):
    rl(request, "tg_hook", 60)
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if secret and request.headers.get("x-telegram-bot-api-secret-token") != secret:
        return JSONResponse({"ok": False}, status_code=403)
    try:
        upd = await request.json()
    except Exception:  # noqa
        return JSONResponse({"ok": True})
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if text.startswith("/start") and chat_id:
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        try:
            data = tokens.verify(payload, "tg_link")
            sid = int(data["sub"])
            if S.get(sid):
                S.add_channel(sid, "telegram", chat_id, verified=True, label="telegram")
                _tg_reply(tok, chat_id, "✅ Linked. You'll get Blue Team CTI alerts here.")
                S.log_event("channel:add:telegram", sid)
                return JSONResponse({"ok": True})
        except tokens.BadToken:
            pass
        _tg_reply(tok, chat_id, "This link is invalid or expired. Get a fresh one from your "
                                "Blue Team CTI preferences page.")
    return JSONResponse({"ok": True})


def _tg_reply(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token:
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception:  # noqa
        pass


# --------------------------------------------------------------------------- legal + misc
@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    h = render_legal("privacy-policy")
    return page("Privacy Policy", h or "<h1>Privacy Policy</h1><p>Coming soon.</p>")


@app.get("/terms", response_class=HTMLResponse)
def terms():
    h = render_legal("terms")
    return page("Terms of Service", h or "<h1>Terms</h1><p>Coming soon.</p>")


@app.get("/cookies", response_class=HTMLResponse)
def cookies():
    h = render_legal("cookies")
    return page("Cookies", h or "<h1>Cookies</h1><p>This site sets no cookies.</p>")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    try:
        S.ensure_schema()
        return PlainTextResponse("ok")
    except Exception as ex:  # noqa
        return PlainTextResponse(f"db error: {ex}", status_code=500)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /manage\nDisallow: /confirm\n"
                             "Disallow: /webhooks\nDisallow: /channel\n")


if __name__ == "__main__":
    import uvicorn
    bind = cfg().get("public_bind", "127.0.0.1:8080")
    host, _, port = bind.partition(":")
    print(f"Blue Team CTI public site → http://{bind}")
    uvicorn.run(app, host=host or "127.0.0.1", port=int(port or "8080"), log_level="warning")
