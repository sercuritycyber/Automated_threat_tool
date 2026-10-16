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




# Posts now go out as the company page (urn:li:organization:…) and the analyst
# writes its own gap + plug lines, so the old appended plug block is gone.

_TAG_RE = re.compile(r"#\w+")
LI_MAX_TAGS = 3
# Scheme-less on purpose. LinkedIn suppresses reach on posts carrying a
# detectable outbound link; a bare domain is not auto-linked, so the post keeps
# its distribution and the reader still gets the route. The full clickable URL
# goes in the first comment when the token is ever allowed to post one.
LI_LINK_HINT = "cti.securitycyber.uk"
# SecurityCyber's brand motto (Charlie, 2026-09-25). The full version lives on
# the brand pages (websites, emails); posts carry the short line, placed just
# above the hashtags on every long-form network. Idempotent.
MOTTO_FULL = ("You don't have an intelligence problem, you have an ingestion problem. "
              "You don't have an information problem, you have a triage problem.")
MOTTO = "You don't have an information problem. You have a triage problem."
# Networks whose posts are built from a ~260-300 char teaser: the motto would
# crowd out the alert itself, so they are the one exception.
_SHORT_NETWORKS = {"bluesky", "x"}


def add_motto(text: str) -> str:
    """Append MOTTO above the trailing hashtag block (if any), once."""
    t = (text or "").rstrip()
    if not t or MOTTO.lower() in t.lower():
        return t
    lines = t.splitlines()
    tail: list[str] = []
    while lines and (not lines[-1].strip() or _is_tag_line(lines[-1])):
        ln = lines.pop()
        if ln.strip():
            tail.insert(0, ln)
    out = "\n".join(lines).rstrip() + "\n\n" + MOTTO
    return out + ("\n\n" + "\n".join(tail) if tail else "")
# Copy that promises a first comment we cannot currently create.
_COMMENT_PROMISE = re.compile(
    r"[(\u2014-]*\s*\b(?:link|links|full write-up|source)\b[^\n.]{0,40}"
    r"in the comments\.?\s*\)?", re.I)


_PAREN_OPEN = re.compile(r"\s*\(\s*")
_PAREN_CLOSE = re.compile(r"\s*\)")


def _li_deparen(text: str) -> str:
    """Replace "(...)" with ", ...". LinkedIn's commentary renderer silently
    truncates a post at the first literal "(" — confirmed 2026-09-15 across
    every recent `breaking`-lane post (live posts read back via the Posts API,
    and the public page itself, both cut off right before the first paren; the
    API still returns 201, so nothing warns us). Root cause looks like the
    little-text-format mention parser (`@[text](urn)`) treating a bare "(" as
    the start of an annotation target and swallowing the rest on failure to
    close it — but whatever LinkedIn is doing, never sending a literal "(" is
    the only reliable fix from our side.
    """
    t = _PAREN_CLOSE.sub("", _PAREN_OPEN.sub(", ", text))
    return re.sub(r",\s*,", ",", t)


def _li_shape(text: str, link_hint: str = LI_LINK_HINT) -> str:
    """Last-mile deterministic shaping, so a model slip cannot cost us reach.

    * strips copy promising a first comment (the API 403s on this token, so the
      promise would be a lie and the post a dead end)
    * de-parens the body — see `_li_deparen` (LinkedIn truncates on "(")
    * guarantees the body names `link_hint`, so there is always a route to the
      site even with no comment
    * trims hashtags past LI_MAX_TAGS (3 reaches further than 5; more is
      down-ranked as tag-stuffing)
    * collapses runs of blank lines
    Wording is otherwise never rewritten (beyond the paren swap above).
    """
    t = _COMMENT_PROMISE.sub("", (text or "").rstrip()).rstrip()
    t = _li_deparen(t)

    # peel the trailing hashtag block off, if there is one
    lines = t.splitlines()
    tail: list[str] = []
    while lines and (not lines[-1].strip() or _is_tag_line(lines[-1])):
        ln = lines.pop()
        if ln.strip():
            tail.insert(0, ln)
    body = "\n".join(lines).rstrip()

    keep: list[str] = []
    seen: set[str] = set()
    for tag in _TAG_RE.findall(" ".join(tail)):
        if tag.lower() not in seen:
            seen.add(tag.lower())
            keep.append(tag)
    keep = keep[:LI_MAX_TAGS]

    if link_hint and link_hint.lower() not in body.lower():
        body = body.rstrip() + f"\n\n\u2192 {link_hint}"
    if MOTTO.lower() not in body.lower():
        body = body.rstrip() + "\n\n" + MOTTO

    out = body if not keep else body.rstrip() + "\n\n" + " ".join(keep)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _is_tag_line(line: str) -> bool:
    toks = line.split()
    return bool(toks) and all(tok.startswith("#") for tok in toks)


def _li_comment(token: str, author: str, share_urn: str, text: str, version: str, log) -> bool:
    """Post `text` as the first comment on a share. Non-fatal: the main post has
    already succeeded, so a comment failure is logged, not raised.

    Uses the legacy unversioned `/v2/socialActions` surface, not `/rest/`: our
    token only carries the older `w_organization_social` / `w_member_social`
    scopes, not the newer `w_organization_social_feed` that `/rest/socialActions`
    requires for writes (confirmed live — `/rest/` 403s ACCESS_DENIED with this
    token, `/v2/` accepts the identical payload with 201). `version` is unused
    here (no LinkedIn-Version header on `/v2/`) but kept in the signature so the
    call site doesn't need to change.
    """
    if not (text and share_urn.startswith("urn:li:")):
        return False
    from urllib.parse import quote
    url = f"https://api.linkedin.com/v2/socialActions/{quote(share_urn, safe='')}/comments"
    try:
        r = requests.post(url, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }, json={"actor": author, "object": share_urn, "message": {"text": text[:1200]}}, timeout=20)
        if r.status_code in (200, 201):
            return True
        if r.status_code == 403:
            log("linkedin: first comment DENIED (403) — token scope no longer "
                "covers this. The post still carries the domain in its body, so "
                "the route is intact; re-check granted scopes with introspectToken.")
        else:
            log(f"linkedin: first comment failed HTTP {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        log(f"linkedin: first comment error ({e})")
    return False


def post_linkedin(text: str, creds: dict, settings: dict, short_text=None, log=print, *, comment=None) -> dict:
    token = creds.get("LINKEDIN_ACCESS_TOKEN")
    if not token:
        return _res("linkedin", False, error="no LINKEDIN_ACCESS_TOKEN")
    pub = settings.get("publish", {})
    pinned = str(pub.get("linkedin_api_version", _LI_DEFAULT_VERSION))
    visibility = pub.get("visibility", "PUBLIC")

    def _do(tok, version):
        # Pure text, no link-preview card: a raw URL or an outbound card in the
        # body throttles reach. The source + subscribe links go in the first
        # comment (see `comment`). The analyst's own plug line is already in `text`.
        payload = {
            "author": _li_author(tok, creds, log), "commentary": _li_shape(text), "visibility": visibility,
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
        }
        return requests.post(_LI_POSTS, headers={
            "Authorization": f"Bearer {tok}", "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0", "LinkedIn-Version": version,
        }, json=payload, timeout=30)

    resp = None
    used_version = pinned
    for version in _li_versions(pinned):
        used_version = version
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
    if comment and urn.startswith("urn:li:"):
        try:
            author = _li_author(token, creds, log)
            if _li_comment(token, author, urn, comment, used_version, log):
                log("linkedin: link posted as first comment")
        except Exception as e:  # noqa — never let a comment failure sink a good post
            log(f"linkedin: first comment skipped ({e})")
    return _res("linkedin", True, id=urn, url=url)


def post_linkedin_personal(text: str, creds: dict, settings: dict, short_text=None, log=print,
                           *, comment=None) -> dict:
    """Post to the OPERATOR'S OWN LinkedIn profile (urn:li:person) instead of the
    company page. Same token and shaping as post_linkedin, but LINKEDIN_AUTHOR_URN
    is dropped so _li_author resolves the token's own member. The result is tagged
    provider="linkedin-personal" so history/targets can tell a personal op post from
    a company CTI post. The automated CTI flow never selects this target — it is
    operator-only (enforced in publish_linkedin.py), so CTI stays company-page-only."""
    personal = dict(creds)
    personal.pop("LINKEDIN_AUTHOR_URN", None)
    r = post_linkedin(text, personal, settings, short_text, log, comment=comment)
    r["provider"] = "linkedin-personal"
    return r


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


# ---------------------------------------------------------------- Discord (general/community channel)
def post_discord(text: str, creds: dict, settings: dict, short_text=None, log=print) -> dict:
    """Mirror a post to the public/community Discord channel via its incoming
    webhook. This is the growth channel — separate from the Worker's own
    DISCORD_ALERT_WEBHOOK, which is the paid-subscriber intel broadcast."""
    url = creds.get("DISCORD_WEBHOOK_GENERAL")
    if not url:
        return _res("discord", False, error="no DISCORD_WEBHOOK_GENERAL")
    # LinkedIn's post_text keeps the domain bare (no scheme) to dodge its own
    # outbound-link penalty — Discord has no such penalty, so give the plug
    # line back its scheme here so it actually renders as a clickable link.
    body = re.sub(r"(?<![\w./])cti\.securitycyber\.uk\b(?!/)", "https://cti.securitycyber.uk", text)
    body = body if len(body) <= 2000 else teaser(body, 1970, short_text)
    try:
        # ?wait=true makes Discord return the created message (with its id)
        # instead of a bare 204 — without it a duplicate can never be found
        # and deleted after the fact (see the 2026-09-16 CVE-2026-90553
        # incident: 4 copies posted in one run, 3 had to be cleaned up by
        # hand on Discord because no message id had ever been recorded).
        r = requests.post(f"{url}?wait=true",
                          json={"content": body, "allowed_mentions": {"parse": []}},
                          headers={"User-Agent": UA}, timeout=20)
    except requests.RequestException as e:
        return _res("discord", False, error=str(e)[:300])
    if r.status_code not in (200, 204):
        return _res("discord", False, error=f"HTTP {r.status_code}: {r.text[:200]}")
    msg = r.json() if r.content else {}
    return _res("discord", True, id=msg.get("id", ""), url="")


# ---------------------------------------------------------------- Facebook (page)
_FB_GRAPH = "https://graph.facebook.com"


def _fb_comment(ver: str, post_id: str, text: str, token: str, log) -> bool:
    """Add the first comment on a FB post (where links go — FB throttles
    link-in-body). Non-fatal: the post already succeeded."""
    if not (text and post_id):
        return False
    try:
        r = requests.post(f"{_FB_GRAPH}/{ver}/{post_id}/comments",
                          data={"message": text, "access_token": token}, timeout=25)
        if r.ok:
            return True
        log(f"facebook: first comment failed HTTP {r.status_code}: "
            f"{r.text[:160].replace(str(token), '<redacted>')}")
    except requests.RequestException as e:
        log(f"facebook: first comment error ({str(e)[:120]})")
    return False


def post_facebook(text: str, creds: dict, settings: dict, short_text=None, log=print,
                  *, comment=None) -> dict:
    """Post to a Facebook Page feed via the Graph API. Needs FB_PAGE_ID and a page
    (or system-user) access token FB_PAGE_ACCESS_TOKEN with pages_manage_posts.

    Links: if a first `comment` is given, the body is posted link-free and the link
    rides in the first comment (FB throttles link-in-body — better reach). Otherwise,
    a URL in the text is passed as `link` so the post still carries its source as a
    preview card. The token is read from creds (config/env) and never logged."""
    page_id = (creds.get("FB_PAGE_ID") or "").strip()
    token = creds.get("FB_PAGE_ACCESS_TOKEN")
    if not (page_id and token):
        return _res("facebook", False, error="need FB_PAGE_ID + FB_PAGE_ACCESS_TOKEN")
    ver = str(settings.get("publish", {}).get("facebook_api_version", "v21.0"))
    data = {"message": text, "access_token": token}
    link = _first_url(text)
    if link and not comment:          # no first comment → keep the source as a body preview
        data["link"] = link
    try:
        r = requests.post(f"{_FB_GRAPH}/{ver}/{page_id}/feed", data=data, timeout=30)
    except requests.RequestException as e:
        return _res("facebook", False, error=str(e)[:300])
    if not r.ok:
        # Graph errors echo the request; never surface the token in the message.
        body = r.text[:300].replace(str(token), "<redacted>")
        return _res("facebook", False, error=f"HTTP {r.status_code}: {body}")
    pid = (r.json() or {}).get("id", "")
    url = f"https://www.facebook.com/{pid}" if pid else ""
    if comment and pid:
        _fb_comment(ver, pid, comment, token, log)
    return _res("facebook", True, id=pid, url=url)


PROVIDERS = {
    "linkedin": post_linkedin,
    "linkedin-personal": post_linkedin_personal,
    "facebook": post_facebook,
    "mastodon": post_mastodon,
    "bluesky": post_bluesky,
    "x": post_x,
    "email": post_email,
    "telegram": post_telegram,
    "discord": post_discord,
}

PROVIDER_LABELS = {"linkedin": "LinkedIn", "linkedin-personal": "LinkedIn (personal)",
                   "facebook": "Facebook (page)", "mastodon": "Mastodon", "bluesky": "Bluesky",
                   "x": "X / Twitter", "email": "Email", "telegram": "Telegram",
                   "discord": "Discord"}

# (env key, label, hint) — drives the Secrets page and the credential check
PROVIDER_KEYS: dict[str, list[tuple[str, str, str]]] = {
    "linkedin": [
        ("LINKEDIN_ACCESS_TOKEN", "Access token", "scope: w_member_social + openid profile"),
        ("LINKEDIN_AUTHOR_URN", "Author URN", "optional — urn:li:person:… or urn:li:organization:…"),
        ("LINKEDIN_CLIENT_ID", "OAuth client ID", "optional — enables auto token refresh"),
        ("LINKEDIN_CLIENT_SECRET", "OAuth client secret", "optional"),
        ("LINKEDIN_REFRESH_TOKEN", "OAuth refresh token", "optional"),
    ],
    "linkedin-personal": [
        ("LINKEDIN_ACCESS_TOKEN", "Access token", "shares the LinkedIn token; posts as YOU (the token's "
                                                  "own member), ignoring LINKEDIN_AUTHOR_URN. Needs w_member_social."),
    ],
    "facebook": [
        ("FB_PAGE_ID", "Page ID", "the numeric Facebook Page ID (Page → About → Page transparency)"),
        ("FB_PAGE_ACCESS_TOKEN", "Page access token", "page or system-user token with pages_manage_posts + "
                                                      "pages_read_engagement. Rotate if ever exposed."),
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
    "discord": [
        ("DISCORD_WEBHOOK_GENERAL", "General channel webhook", "Server Settings → Integrations → Webhooks"),
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
    "linkedin-personal": ["LINKEDIN_ACCESS_TOKEN"],
    "facebook": ["FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN"],
    "mastodon": ["MASTODON_BASE_URL", "MASTODON_ACCESS_TOKEN"],
    "bluesky": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    "x": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"],
    "email": ["EMAIL_SMTP_HOST", "EMAIL_TO"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL"],
    "discord": ["DISCORD_WEBHOOK_GENERAL"],
}


def configured(creds: dict) -> list[str]:
    return [p for p, keys in _REQUIRED.items() if all(creds.get(k) for k in keys)]


def resolve_targets(settings: dict, creds: dict, only=None) -> list[str]:
    want = list(only) if only else list(settings.get("publish", {}).get("targets") or ["linkedin"])
    return [t for t in want if t in PROVIDERS]


def post_all(text: str, creds: dict, settings: dict, *, short_text: str | None = None,
             comment: str | None = None, only=None, log=print) -> list[dict]:
    """Post `text` to every resolved target. Returns one result dict per target.
    `comment` (if given) is posted as the first comment on LinkedIn — that's
    where the links go."""
    out = []
    for name in resolve_targets(settings, creds, only):
        fn = PROVIDERS[name]
        try:
            if name in ("linkedin", "linkedin-personal"):   # _li_shape adds the motto (above the tags)
                out.append(fn(text, creds, settings, short_text, log, comment=comment))
            elif name == "facebook":  # motto in the body; link/source rides in the first comment
                out.append(fn(add_motto(text), creds, settings, short_text, log, comment=comment))
            elif name in _SHORT_NETWORKS:
                out.append(fn(text, creds, settings, short_text, log))
            else:
                out.append(fn(add_motto(text), creds, settings, short_text, log))
        except Exception as e:  # noqa — a broken provider must not kill the others
            out.append(_res(name, False, error=f"{type(e).__name__}: {e}"[:300]))
    return out


if __name__ == "__main__":  # quick credential check
    import json
    import os
    print(json.dumps({"configured": configured(dict(os.environ))}, indent=2))
