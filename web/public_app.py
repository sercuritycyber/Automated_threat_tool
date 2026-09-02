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
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
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
CSS = """
:root{--bg:#fbfbfd;--ink:#1a1f28;--dim:#5b6472;--line:#e3e6ec;--accent:#2f6fed;
--good:#1f9d55;--bad:#d64545;--card:#fff;--radius:12px}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#0e1116;--ink:#e6edf3;--dim:#9aa4b2;--line:#232a35;--card:#161b22;--accent:#4f8bff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent)}
.wrap{max-width:760px;margin:0 auto;padding:32px 20px 64px}
header .brand{font-weight:800;font-size:18px;letter-spacing:-.01em;text-decoration:none;color:var(--ink)}
header .brand span{color:var(--accent)}
h1{font-size:30px;line-height:1.2;letter-spacing:-.02em;margin:.3em 0}
h2{font-size:20px;margin:1.6em 0 .5em}
h3{font-size:16px;margin:1.3em 0 .4em}
.lede{font-size:18px;color:var(--dim)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:20px 22px;margin:16px 0}
.tiers{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:620px){.tiers{grid-template-columns:1fr}}
.tier{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:20px}
.tier h3{margin-top:0}
.price{font-size:26px;font-weight:800}
.btn{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:9px;
padding:11px 20px;font:inherit;font-weight:650;cursor:pointer;text-decoration:none}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--ink)}
.btn.bad{background:var(--bad)}
.btn:hover{filter:brightness(1.06)}
label{display:block;margin:14px 0 5px;font-weight:600;font-size:14px}
label small{font-weight:400;color:var(--dim)}
input[type=text],input[type=email],input[type=url],textarea,select{width:100%;background:var(--bg);
border:1px solid var(--line);color:var(--ink);border-radius:9px;padding:10px 12px;font:inherit}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:650;border:1px solid var(--line)}
.pill.good{color:var(--good);border-color:var(--good)}
.pill.bad{color:var(--bad);border-color:var(--bad)}
.flash{background:rgba(47,111,237,.1);border:1px solid var(--accent);border-radius:9px;padding:12px 14px;margin:14px 0}
.flash.bad{background:rgba(214,69,69,.1);border-color:var(--bad)}
.flash.good{background:rgba(31,157,85,.1);border-color:var(--good)}
table{border-collapse:collapse;width:100%;font-size:14px;margin:10px 0}
td,th{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
code{background:var(--bg);border:1px solid var(--line);border-radius:5px;padding:1px 5px;font-size:.9em}
hr{border:0;border-top:1px solid var(--line);margin:22px 0}
footer{margin-top:48px;padding-top:18px;border-top:1px solid var(--line);color:var(--dim);font-size:13px}
footer a{color:var(--dim)}
.muted{color:var(--dim);font-size:14px}
"""


def page(title: str, body: str, flash: str = "", flash_kind: str = "") -> HTMLResponse:
    c = cfg()
    fl = f'<div class="flash {flash_kind}">{e(flash)}</div>' if flash else ""
    foot_ico = f' · ICO reg {e(c["ico_registration"])}' if c.get("ico_registration") else ""
    legal_name = c.get("company_legal_name") or c.get("brand_name", "")
    return HTMLResponse(f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="index,follow">
<title>{e(title)} · {e(c.get('brand_name','Blue Team CTI'))}</title>
<style>{CSS}</style></head><body><div class=wrap>
<header><a class=brand href="/">Blue Team <span>CTI</span></a></header>
{fl}{body}
<footer>{e(legal_name)}{foot_ico} · <a href="/privacy">Privacy</a> ·
<a href="/terms">Terms</a> · <a href="/cookies">Cookies</a> ·
<a href="mailto:{e(c.get('contact_email') or c.get('support_email') or '')}">Contact</a></footer>
</div></body></html>""")


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
        chans = ", ".join(t.get("channels", []))
        tiers_html += f"""<div class=tier>
<h3>{e(name.title())}</h3>
<div class=price>{e(t.get('display_price','—'))}</div>
<p class=muted>{e(t.get('blurb',''))}</p>
<ul class=muted>
<li>Instant alerts at score ≥ {e(t.get('instant_min_score','?'))}</li>
<li>{'Daily digest' if t.get('daily_digest') else 'No digest'}</li>
{'<li>Machine-readable JSON attached</li>' if t.get('attach_json') else ''}
<li>Channels: {e(chans)}</li>
</ul>
<a class=btn href="/subscribe?tier={name}">Subscribe</a>
</div>"""
    body = f"""
<h1>Vulnerability alerts that tell you what to do</h1>
<p class=lede>We watch vendor advisories, the CISA KEV catalog and reputable
security research, cut the noise, and send you the handful that matter for
defenders — each with the CVE, what's affected, and the one action to take.</p>

<div class=tiers>{tiers_html}</div>

<h2>How it works</h2>
<div class=card>
<p>Every few hours an automated pipeline pulls the major security feeds,
cross-references the CISA Known Exploited Vulnerabilities catalog, removes
near-duplicates, and scores each story for real-world impact. Anything that
clears your threshold is sent straight to your channels — email, Slack, Telegram,
a webhook, or ntfy.</p>
<p class=muted>Informational only. It aggregates public advisories and is not a
substitute for your own vulnerability management. See the <a href="/terms">terms</a>.</p>
</div>

<h2>Already subscribed?</h2>
<div class=card>
<form method=post action="/account">
<label>Your email <small>— we'll send your preference link</small></label>
<input type=email name=email required placeholder="you@example.com">
<div class=row style="margin-top:12px"><button class=btn ghost>Email me my link</button></div>
</form>
</div>
"""
    return page("Vulnerability alerts for defenders", body, m, k)


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


@app.get("/subscribe")
def subscribe(request: Request, tier: str = "individual"):
    rl(request, "subscribe", 10)
    c = cfg()
    if not c.get("enabled") or tier not in c["tiers"]:
        return RedirectResponse("/?m=" + _q("That plan isn't available."), 303)
    probs = [p for p in subsconfig.problems() if "enabled" not in p and "ico_registration" not in p]
    if probs:
        return page("Not quite ready",
                    "<h1>Checkout isn't live yet</h1><p class=lede>We're still setting up "
                    "billing. Please check back shortly.</p>"
                    + ("<p class=muted>Owner: " + e("; ".join(probs)) + "</p>"
                       if request.query_params.get("debug") else ""))
    site = c.get("site_url", "")
    try:
        sess = billing.create_checkout_session(
            tier, None,
            success_url=f"{site}/welcome?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{site}/?m=" + _q("Checkout cancelled — no charge made."))
    except billing.StripeError as ex:
        return page("Checkout error",
                    f"<h1>Couldn't start checkout</h1><p class=muted>{e(str(ex))}</p>"
                    "<p><a href='/'>Back</a></p>")
    return RedirectResponse(sess["url"], 303)


@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request, session_id: str = ""):
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
        t = tokens.sign({"sub": sid, "act": "confirm"}, ttl=14 * 86400)
        site = cfg().get("site_url", "")
        send_email(email, "Confirm your Blue Team CTI alerts",
                   f"Thanks for subscribing to the {tier} plan.\n\n"
                   f"Confirm your email to switch your alerts on:\n\n"
                   f"{site}/confirm?t={t}\n\n"
                   f"This link works for 14 days. If you didn't subscribe, ignore this "
                   f"and email {cfg().get('contact_email','')} — you won't be charged again.")
        S.log_event("confirm_email_sent", sid)
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
    if sub["status"] not in ("cancelled",):
        S.set_status(sid, "active", "email confirmed", ip=client_ip(request))
        S.log_consent(sid, "signup", True, ip=client_ip(request),
                      user_agent=request.headers.get("user-agent", ""), terms_version=TERMS_VERSION,
                      detail="double opt-in via /confirm")
        notify_owner(f"CTI: new subscriber confirmed ({sub['tier']})")
    mtok = tokens.sign({"sub": sid, "act": "manage"}, ttl=90 * 86400)
    return RedirectResponse(f"/manage?t={mtok}&m=" + _q("You're in. Set up your channels below."), 303)


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

    add_opts = "".join(f"<option value={x}>{x}</option>" for x in allowed if x not in ("email",))
    unsub_link = f"{site}/unsubscribe?t=" + tokens.sign({"sub": sub["id"], "act": "unsubscribe"}, ttl=90 * 86400)

    portal_btn = ""
    if sub.get("stripe_customer_id"):
        portal_btn = (f'<form method=post action="/manage/portal"><input type=hidden name=t value="{e(tok)}">'
                      f'<button class="btn ghost">Manage billing / payment method</button></form>')

    body = f"""
<h1>Your alert preferences</h1>
<p class=muted>Plan: <b>{e(sub['tier'])}</b> · Status: <b>{e(sub['status'])}</b> · {e(sub['email'])}</p>

<h2>Channels</h2>
<div class=card>
<table><tbody><tr><th>type</th><th>where</th><th></th></tr>{ch_rows}</tbody></table>
<form method=post action="/manage/channel/add" style="margin-top:12px">
<input type=hidden name=t value="{e(tok)}">
<div class=row>
<select name=kind style="max-width:160px">{add_opts}</select>
<input type=text name=target placeholder="webhook URL / ntfy topic URL / Slack webhook URL">
<button class=btn>Add</button>
</div>
<p class=muted style="margin-top:8px">For <b>Telegram</b>, <a href="/channel/telegram?t={e(tok)}">use the bot link</a>.
For <b>Slack</b>, paste an <a href="https://api.slack.com/messaging/webhooks" rel=noopener>incoming-webhook URL</a>.</p>
</form>
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
