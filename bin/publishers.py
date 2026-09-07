#!/usr/bin/env python3
"""Social publishers — one function per network, a common result shape.

Every provider function has the signature

    post(text: str, creds: dict, settings: dict, short_text: str | None) -> dict

and returns {"provider", "ok", "id", "url", "error"}.  `creds` is a flat dict
(usually os.environ merged with config/env).  Nothing here decides *whether* to
post — that's the publisher's job — these just execute one post each.

Char-limited networks (X, Bluesky) fall back to `short_text` if given, else an
auto-derived teaser (hook line + source URL).
"""
from __future__ import annotations

import base64
import hashlib
import json
import hmac
import re
import secrets
import smtplib
import ssl
import time
import urllib.parse
from datetime import datetime, timezone
from email.message import EmailMessage

import requests

UA = "BlueTeamCTI/1.0 (+https://securitycyber.uk)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _res(provider: str, ok: bool, *, id: str = "", url: str = "", error: str = "") -> dict:
    return {"provider": provider, "ok": ok, "id": id, "url": url, "error": error}


def _first_url(text: str) -> str:
    m = re.search(r"https?://\S+", text)
    return m.group(0).rstrip(").,") if m else ""


def teaser(text: str, limit: int, short_text: str | None = None) -> str:
    """A version of the post that fits `limit` characters."""
    if short_text and len(short_text) <= limit:
        return short_text
    if len(text) <= limit:
        return text
    hook = text.strip().split("\n", 1)[0].strip()
    url = _first_url(text)
    tail = f"\n\n{url}" if url else ""
    room = limit - len(tail)
    if len(hook) > room:
        hook = hook[: max(0, room - 1)].rstrip() + "…"
    return (hook + tail)[:limit]


# ---------------------------------------------------------------- LinkedIn
_LI_USERINFO = "https://api.linkedin.com/v2/userinfo"
_LI_POSTS = "https://api.linkedin.com/rest/posts"
_LI_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
_URN_RE = re.compile(r"^urn:li:(person|organization):[A-Za-z0-9_-]+$")
_LI_DEFAULT_VERSION = "202509"


def _li_versions(pinned: str) -> list[str]:
    out = [pinned] if pinned else []
    now = datetime.now(timezone.utc)
    for back in range(12, -2, -1):
        y, m = now.year, now.month - back
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        v = f"{y}{m:02d}"
        if v not in out:
            out.append(v)
    return out


def _li_author(token: str, creds: dict, log) -> str:
    pinned = (creds.get("LINKEDIN_AUTHOR_URN") or "").strip()
    sub = None
    try:
        r = requests.get(_LI_USERINFO, headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if r.ok:
            sub = r.json().get("sub")
    except requests.RequestException as e:
        log(f"linkedin: /userinfo failed ({e})")
    if pinned and _URN_RE.match(pinned):
        if pinned.startswith("urn:li:organization:"):
            return pinned
        if sub and pinned != f"urn:li:person:{sub}":
            log(f"linkedin: pinned URN {pinned} != token ({sub}) — using the token's own")
            return f"urn:li:person:{sub}"
        return pinned
    if sub:
        return f"urn:li:person:{sub}"
    raise RuntimeError("no valid LINKEDIN_AUTHOR_URN and /userinfo failed")


def _li_refresh(creds: dict, log):
    cid, csec, rtok = (creds.get("LINKEDIN_CLIENT_ID"), creds.get("LINKEDIN_CLIENT_SECRET"),
                       creds.get("LINKEDIN_REFRESH_TOKEN"))
    if not (cid and csec and rtok):
        return None
    log("linkedin: access token rejected — refreshing")
    r = requests.post(_LI_TOKEN, data={"grant_type": "refresh_token", "refresh_token": rtok,
                                       "client_id": cid, "client_secret": csec}, timeout=20)
    r.raise_for_status()
    return r.json().get("access_token")




# Posting as Charlie's personal profile until the company-page token has
# w_organization_social — so every post plugs the company page explicitly.
LI_COMPANY_PLUG = (
    "\n\n\u2014\u2014\u2014\n"
    "SecurityCyber \u00b7 UK threat intelligence for small teams \u00b7 #SECURITYCYBERUK"
)



LI_CARD = {
    "article": {
        "source": "https://securitycyber.uk/cti",
        "title": "SecurityCyber CTI \u2014 threat intelligence alerts for defenders",
        "description": ("Public advisories cross-referenced against CISA KEV, a plain-English "
                        "brief and an impact score on each one. Free plan, no card."),
    }
}


def _li_with_plug(text: str) -> str:
    """Append the company-page plug once, staying under LinkedIn's hard cap."""
    t = (text or "").rstrip()
    if "#SECURITYCYBERUK" in t:
        return t
    combined = t + LI_COMPANY_PLUG
    return combined if len(combined) <= 2950 else t


def post_linkedin(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    token = creds.get("LINKEDIN_ACCESS_TOKEN")
    if not token:
        return _res("linkedin", False, error="no LINKEDIN_ACCESS_TOKEN")
    pub = settings.get("publish", {})
    pinned = str(pub.get("linkedin_api_version", _LI_DEFAULT_VERSION))
    visibility = pub.get("visibility", "PUBLIC")

    def _do(tok, version):
        payload = {
            "author": _li_author(tok, creds, log), "commentary": _li_with_plug(text), "visibility": visibility,
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
            "content": LI_CARD,
        }
        return requests.post(_LI_POSTS, headers={
            "Authorization": f"Bearer {tok}", "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0", "LinkedIn-Version": version,
        }, json=payload, timeout=30)

    resp = None
    for version in _li_versions(pinned):
        resp = _do(token, version)
        if resp.status_code == 401:
            new = _li_refresh(creds, log)
            if new:
                token = new
                creds["LINKEDIN_ACCESS_TOKEN"] = new
                resp = _do(new, version)
        if resp.status_code == 426:
            continue
        break
    if resp is None or resp.status_code not in (200, 201):
        code = resp.status_code if resp is not None else "?"
        body = resp.text[:400] if resp is not None else "no response"
        return _res("linkedin", False, error=f"HTTP {code}: {body}")
    urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id") or ""
    url = f"https://www.linkedin.com/feed/update/{urn}/" if urn.startswith("urn:li:") else ""
    return _res("linkedin", True, id=urn, url=url)


# ---------------------------------------------------------------- Mastodon
def post_mastodon(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    base = (creds.get("MASTODON_BASE_URL") or "").rstrip("/")
    token = creds.get("MASTODON_ACCESS_TOKEN")
    if not (base and token):
        return _res("mastodon", False, error="need MASTODON_BASE_URL + MASTODON_ACCESS_TOKEN")
    vis = settings.get("publish", {}).get("mastodon_visibility", "public")
    body = text if len(text) <= 500 else teaser(text, 500, short_text)
    try:
        r = requests.post(f"{base}/api/v1/statuses",
                          headers={"Authorization": f"Bearer {token}", "User-Agent": UA,
                                   "Idempotency-Key": hashlib.sha256(text.encode()).hexdigest()},
                          data={"status": body, "visibility": vis}, timeout=30)
    except requests.RequestException as e:
        return _res("mastodon", False, error=str(e)[:300])
    if r.status_code >= 300:
        return _res("mastodon", False, error=f"HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    return _res("mastodon", True, id=str(j.get("id", "")), url=j.get("url", ""))


# ---------------------------------------------------------------- Bluesky
def _bsky_facets(text: str) -> list[dict]:
    facets = []
    for m in re.finditer(r"https?://[^\s\]]+", text):
        b = text[: m.start()].encode("utf-8")
        e = text[: m.end()].encode("utf-8")
        facets.append({
            "index": {"byteStart": len(b), "byteEnd": len(e)},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0).rstrip(").,")}],
        })
    return facets


def post_bluesky(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    handle = (creds.get("BLUESKY_HANDLE") or "").lstrip("@")
    pw = creds.get("BLUESKY_APP_PASSWORD")
    pds = (creds.get("BLUESKY_PDS_URL") or "https://bsky.social").rstrip("/")
    if not (handle and pw):
        return _res("bluesky", False, error="need BLUESKY_HANDLE + BLUESKY_APP_PASSWORD")
    try:
        s = requests.post(f"{pds}/xrpc/com.atproto.server.createSession",
                          json={"identifier": handle, "password": pw}, timeout=30)
        if not s.ok:
            return _res("bluesky", False, error=f"session HTTP {s.status_code}: {s.text[:200]}")
        sess = s.json()
        jwt, did = sess["accessJwt"], sess["did"]
        body = teaser(text, 300, short_text)          # 300-grapheme hard limit
        record = {"$type": "app.bsky.feed.post", "text": body, "createdAt": _now_iso(), "langs": ["en"]}
        facets = _bsky_facets(body)
        if facets:
            record["facets"] = facets
        r = requests.post(f"{pds}/xrpc/com.atproto.repo.createRecord",
                          headers={"Authorization": f"Bearer {jwt}"},
                          json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
                          timeout=30)
    except requests.RequestException as e:
        return _res("bluesky", False, error=str(e)[:300])
    if not r.ok:
        return _res("bluesky", False, error=f"HTTP {r.status_code}: {r.text[:300]}")
    uri = r.json().get("uri", "")
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else ""
    return _res("bluesky", True, id=uri, url=url)


# ---------------------------------------------------------------- X / Twitter
def _pct(s) -> str:
    return urllib.parse.quote(str(s), safe="~")


def _oauth1_header(method: str, url: str, ck: str, cs: str, at: str, ats: str) -> str:
    oauth = {
        "oauth_consumer_key": ck, "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
        "oauth_token": at, "oauth_version": "1.0",
    }
    param_str = "&".join(f"{_pct(k)}={_pct(oauth[k])}" for k in sorted(oauth))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    signing_key = f"{_pct(cs)}&{_pct(ats)}"
    sig = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def post_x(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    ck, cs = creds.get("X_API_KEY"), creds.get("X_API_SECRET")
    at, ats = creds.get("X_ACCESS_TOKEN"), creds.get("X_ACCESS_SECRET")
    if not all([ck, cs, at, ats]):
        return _res("x", False, error="need X_API_KEY/SECRET + X_ACCESS_TOKEN/SECRET")
    limit = int(settings.get("publish", {}).get("x_char_limit", 280))
    body = text if len(text) <= limit else teaser(text, limit, short_text)
    url = "https://api.twitter.com/2/tweets"
    try:
        r = requests.post(url, headers={"Authorization": _oauth1_header("POST", url, ck, cs, at, ats),
                                        "Content-Type": "application/json", "User-Agent": UA},
                          json={"text": body}, timeout=30)
    except requests.RequestException as e:
        return _res("x", False, error=str(e)[:300])
    if r.status_code >= 300:
        return _res("x", False, error=f"HTTP {r.status_code}: {r.text[:300]}")
    tid = (r.json().get("data") or {}).get("id", "")
    return _res("x", True, id=tid, url=f"https://x.com/i/web/status/{tid}" if tid else "")


# ---------------------------------------------------------------- Email (SMTP)
def post_email(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    host = creds.get("EMAIL_SMTP_HOST")
    user = creds.get("EMAIL_SMTP_USER")
    pw = creds.get("EMAIL_SMTP_PASSWORD")
    sender = creds.get("EMAIL_FROM") or user
    to = [a.strip() for a in re.split(r"[,\s;]+", creds.get("EMAIL_TO") or "") if a.strip()]
    if not (host and sender and to):
        return _res("email", False, error="need EMAIL_SMTP_HOST + EMAIL_FROM + EMAIL_TO")
    port = int(creds.get("EMAIL_SMTP_PORT") or 587)
    prefix = settings.get("publish", {}).get("email_subject_prefix", "[Blue Team CTI]")
    subject = f"{prefix} {text.strip().splitlines()[0][:150]}".strip()
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context()) as s:
                if user and pw:
                    s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                try:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                except smtplib.SMTPNotSupportedError:
                    pass
                if user and pw:
                    s.login(user, pw)
                s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        return _res("email", False, error=f"{type(e).__name__}: {e}"[:300])
    return _res("email", True, id=msg["Subject"], url="", error="")


# ---------------------------------------------------------------- registry

# ---------------------------------------------------------------- Telegram (channel)
def post_telegram(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    token = creds.get("TELEGRAM_BOT_TOKEN")
    chat = creds.get("TELEGRAM_CHANNEL") or creds.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return _res("telegram", False, error="need TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL")
    body = text if len(text) <= 4096 else teaser(text, 4000, short_text)
    payload = {
        "chat_id": chat, "text": body,
        # force our link-preview card (our OG image) rather than the source URL's
        "link_preview_options": json.dumps({"url": "https://securitycyber.uk/cti",
                                            "prefer_large_media": True}),
    }
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data=payload, headers={"User-Agent": UA}, timeout=30)
    except requests.RequestException as e:
        return _res("telegram", False, error=str(e)[:300])
    j = r.json() if r.content else {}
    if not j.get("ok"):
        return _res("telegram", False, error=f"HTTP {r.status_code}: {j.get('description') or r.text[:200]}")
    res = j.get("result", {})
    mid = res.get("message_id", "")
    cu = str(chat).lstrip("@")
    url = f"https://t.me/{cu}/{mid}" if cu and not cu.startswith("-") else ""
    return _res("telegram", True, id=str(mid), url=url)


PROVIDERS = {
    "linkedin": post_linkedin,
    "mastodon": post_mastodon,
    "bluesky": post_bluesky,
    "x": post_x,
    "email": post_email,
    "telegram": post_telegram,
}

PROVIDER_LABELS = {"linkedin": "LinkedIn", "mastodon": "Mastodon", "bluesky": "Bluesky",
                   "x": "X / Twitter", "email": "Email", "telegram": "Telegram"}

# (env key, label, hint) — drives the Secrets page and the credential check
PROVIDER_KEYS: dict[str, list[tuple[str, str, str]]] = {
    "linkedin": [
        ("LINKEDIN_ACCESS_TOKEN", "Access token", "scope: w_member_social + openid profile"),
        ("LINKEDIN_AUTHOR_URN", "Author URN", "optional — urn:li:person:… or urn:li:organization:…"),
        ("LINKEDIN_CLIENT_ID", "OAuth client ID", "optional — enables auto token refresh"),
        ("LINKEDIN_CLIENT_SECRET", "OAuth client secret", "optional"),
        ("LINKEDIN_REFRESH_TOKEN", "OAuth refresh token", "optional"),
    ],
    "mastodon": [
        ("MASTODON_BASE_URL", "Instance URL", "e.g. https://infosec.exchange"),
        ("MASTODON_ACCESS_TOKEN", "Access token", "Preferences → Development → your app → access token"),
    ],
    "bluesky": [
        ("BLUESKY_HANDLE", "Handle", "e.g. blueteampod.bsky.social"),
        ("BLUESKY_APP_PASSWORD", "App password", "Settings → Privacy and security → App passwords"),
        ("BLUESKY_PDS_URL", "PDS URL", "optional — default https://bsky.social"),
    ],
    "x": [
        ("X_API_KEY", "API key", "developer.x.com → your app → Keys and tokens"),
        ("X_API_SECRET", "API key secret", ""),
        ("X_ACCESS_TOKEN", "Access token", "must have Read and write permission"),
        ("X_ACCESS_SECRET", "Access token secret", ""),
    ],
    "telegram": [
        ("TELEGRAM_BOT_TOKEN", "Bot token", "from @BotFather"),
        ("TELEGRAM_CHANNEL", "Channel", "@username or numeric id — the bot must be a channel admin"),
    ],
    "email": [
        ("EMAIL_SMTP_HOST", "SMTP host", "e.g. smtp.gmail.com"),
        ("EMAIL_SMTP_PORT", "SMTP port", "587 STARTTLS (default) or 465 SSL"),
        ("EMAIL_SMTP_USER", "SMTP username", "usually the full email address"),
        ("EMAIL_SMTP_PASSWORD", "SMTP password", "an app password for Gmail / Outlook"),
        ("EMAIL_FROM", "From address", "defaults to the SMTP username"),
        ("EMAIL_TO", "Recipients", "comma-separated — who gets each post"),
    ],
}

# which env keys, if present, mean "this provider is configured"
_REQUIRED = {
    "linkedin": ["LINKEDIN_ACCESS_TOKEN"],
    "mastodon": ["MASTODON_BASE_URL", "MASTODON_ACCESS_TOKEN"],
    "bluesky": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "x": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"],
    "email": ["EMAIL_SMTP_HOST", "EMAIL_TO"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL"],
}


def configured(creds: dict) -> list[str]:
    return [p for p, keys in _REQUIRED.items() if all(creds.get(k) for k in keys)]


def resolve_targets(settings: dict, creds: dict, only=None) -> list[str]:
    want = list(only) if only else list(settings.get("publish", {}).get("targets") or ["linkedin"])
    return [t for t in want if t in PROVIDERS]


def post_all(text: str, creds: dict, settings: dict, *, short_text: str | None = None,
             only=None, log=print) -> list[dict]:
    """Post `text` to every resolved target. Returns one result dict per target."""
    out = []
    for name in resolve_targets(settings, creds, only):
        fn = PROVIDERS[name]
        try:
            out.append(fn(text, creds, settings, short_text, log))
        except Exception as e:  # noqa — a broken provider must not kill the others
            out.append(_res(name, False, error=f"{type(e).__name__}: {e}"[:300]))
    return out


if __name__ == "__main__":  # quick credential check
    import json
    import os
    print(json.dumps({"configured": configured(dict(os.environ))}, indent=2))
