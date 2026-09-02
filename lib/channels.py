#!/usr/bin/env python3
"""Per-subscriber delivery channels.

    send(kind, target, subject, body, *, json_payload=None, creds=None,
         settings=None, priority="default") -> {"kind","ok","ref","error"}

`target` is whatever identifies the destination for that kind:
  email     -> recipient address
  slack     -> an incoming-webhook URL
  telegram  -> a numeric chat id (bot token comes from creds)
  webhook   -> a URL to POST JSON to
  ntfy      -> a full topic URL (https://ntfy.sh/mytopic)

Nothing here decides *who* gets *what* — that's bin/deliver.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

import requests

UA = "BlueTeamCTI-alerts/1.0 (+https://securitycyber.uk)"


def _res(kind: str, ok: bool, ref: str = "", error: str = "") -> dict:
    return {"kind": kind, "ok": ok, "ref": ref, "error": error}


def _creds(creds: dict | None) -> dict:
    return creds if creds is not None else dict(os.environ)


# ------------------------------------------------------------------ email
def _send_email(to: str, subject: str, body: str, creds: dict,
                json_payload: dict | None = None, attach_name: str = "cti.json") -> dict:
    host = creds.get("EMAIL_SMTP_HOST")
    user = creds.get("EMAIL_SMTP_USER")
    pw = creds.get("EMAIL_SMTP_PASSWORD")
    sender = creds.get("SUBS_FROM_EMAIL") or creds.get("EMAIL_FROM") or user
    reply_to = creds.get("SUBS_REPLY_TO")
    if not (host and sender and to):
        return _res("email", False, error="need EMAIL_SMTP_HOST + a from address + recipient")
    port = int(creds.get("EMAIL_SMTP_PORT") or 587)
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["List-Unsubscribe"] = creds.get("_list_unsub", "") or ""
    if not msg["List-Unsubscribe"]:
        del msg["List-Unsubscribe"]
    msg.set_content(body)
    if json_payload is not None:
        msg.add_attachment(json.dumps(json_payload, indent=2).encode(),
                           maintype="application", subtype="json", filename=attach_name)
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
    return _res("email", True, ref=to)


# ------------------------------------------------------------------ slack
def _send_slack(webhook_url: str, subject: str, body: str) -> dict:
    if not re.match(r"https://hooks\.slack\.com/", webhook_url or ""):
        return _res("slack", False, error="not a hooks.slack.com incoming-webhook URL")
    payload = {"text": f"*{subject}*\n{body}", "unfurl_links": False, "unfurl_media": False}
    try:
        r = requests.post(webhook_url, json=payload, headers={"User-Agent": UA}, timeout=20)
    except requests.RequestException as e:
        return _res("slack", False, error=str(e)[:200])
    return _res("slack", r.ok, ref="posted") if r.ok \
        else _res("slack", False, error=f"HTTP {r.status_code}: {r.text[:150]}")


# ------------------------------------------------------------------ telegram
def _send_telegram(chat_id: str, subject: str, body: str, creds: dict) -> dict:
    tok = creds.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        return _res("telegram", False, error="TELEGRAM_BOT_TOKEN not set")
    text = f"*{_tg_escape(subject)}*\n\n{_tg_escape(body)}"
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat_id, "text": text[:4096],
                                "parse_mode": "MarkdownV2", "disable_web_page_preview": True},
                          timeout=20)
    except requests.RequestException as e:
        return _res("telegram", False, error=str(e)[:200])
    if r.ok:
        return _res("telegram", True, ref=str((r.json().get("result") or {}).get("message_id", "")))
    # retry without markdown if the escaping upset it
    try:
        r2 = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                           json={"chat_id": chat_id, "text": f"{subject}\n\n{body}"[:4096],
                                 "disable_web_page_preview": True}, timeout=20)
        if r2.ok:
            return _res("telegram", True, ref="plain")
    except requests.RequestException:
        pass
    return _res("telegram", False, error=f"HTTP {r.status_code}: {r.text[:150]}")


def _tg_escape(s: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", s or "")


# ------------------------------------------------------------------ discord
def _send_discord(webhook_url: str, subject: str, body: str) -> dict:
    if not re.match(r"https://(discord|discordapp)\.com/api/webhooks/", webhook_url or ""):
        return _res("discord", False, error="not a discord.com/api/webhooks URL")
    content = f"**{subject}**\n{body}"
    if len(content) > 2000:
        content = content[:1970].rstrip() + "\n…"
    try:
        r = requests.post(webhook_url, json={"content": content, "flags": 4,
                                             "allowed_mentions": {"parse": []}},
                          headers={"User-Agent": UA}, timeout=20)
    except requests.RequestException as e:
        return _res("discord", False, error=str(e)[:200])
    return _res("discord", r.ok, ref="posted") if r.ok \
        else _res("discord", False, error=f"HTTP {r.status_code}: {r.text[:150]}")


# ------------------------------------------------------------------ generic webhook
def _send_webhook(url: str, subject: str, body: str, json_payload: dict | None, creds: dict) -> dict:
    if not re.match(r"https?://", url or ""):
        return _res("webhook", False, error="not a URL")
    data = {"brand": "Blue Team CTI", "subject": subject, "text": body,
            "ts": datetime.now(timezone.utc).isoformat(), "payload": json_payload}
    raw = json.dumps(data).encode()
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    secret = creds.get("SUBS_WEBHOOK_SIGNING_SECRET")
    if secret:
        headers["X-CTI-Signature"] = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    try:
        r = requests.post(url, data=raw, headers=headers, timeout=20)
    except requests.RequestException as e:
        return _res("webhook", False, error=str(e)[:200])
    return _res("webhook", r.ok, ref=f"HTTP {r.status_code}") if r.ok \
        else _res("webhook", False, error=f"HTTP {r.status_code}: {r.text[:150]}")


# ------------------------------------------------------------------ ntfy
def _send_ntfy(topic_url: str, subject: str, body: str, priority: str) -> dict:
    if not re.match(r"https?://", topic_url or ""):
        return _res("ntfy", False, error="not a topic URL")
    pr = {"instant": "high", "digest": "default", "default": "default", "high": "high"}.get(priority, "default")
    try:
        r = requests.post(topic_url, data=body.encode("utf-8"),
                          headers={"Title": subject[:250], "Priority": pr,
                                   "Tags": "warning,shield", "User-Agent": UA}, timeout=20)
    except requests.RequestException as e:
        return _res("ntfy", False, error=str(e)[:200])
    return _res("ntfy", r.ok, ref="posted") if r.ok \
        else _res("ntfy", False, error=f"HTTP {r.status_code}: {r.text[:150]}")


# ------------------------------------------------------------------ dispatch
def send(kind: str, target: str, subject: str, body: str, *, json_payload: dict | None = None,
         creds: dict | None = None, settings: dict | None = None, priority: str = "default") -> dict:
    c = _creds(creds)
    if kind == "email":
        return _send_email(target, subject, body, c, json_payload)
    if kind == "slack":
        return _send_slack(target, subject, body)
    if kind == "telegram":
        return _send_telegram(target, subject, body, c)
    if kind == "discord":
        return _send_discord(target, subject, body)
    if kind == "webhook":
        return _send_webhook(target, subject, body, json_payload, c)
    if kind == "ntfy":
        return _send_ntfy(target, subject, body, priority)
    return _res(kind, False, error=f"unknown channel kind '{kind}'")


def verify_target(kind: str, target: str, creds: dict | None = None) -> tuple[bool, str]:
    """Light validation used when a subscriber adds a channel."""
    target = (target or "").strip()
    if kind == "email":
        ok = bool(re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", target))
        return ok, "" if ok else "not a valid email address"
    if kind == "slack":
        ok = target.startswith("https://hooks.slack.com/")
        return ok, "" if ok else "must be a Slack incoming-webhook URL (https://hooks.slack.com/…)"
    if kind == "discord":
        ok = bool(re.match(r"https://(discord|discordapp)\.com/api/webhooks/\d+/", target))
        return ok, "" if ok else "must be a Discord webhook URL (Server Settings → Integrations → Webhooks)"
    if kind == "telegram":
        ok = bool(re.match(r"-?\d+$", target))
        return ok, "" if ok else "expected a numeric chat id — use the bot link to get yours"
    if kind == "webhook":
        ok = bool(re.match(r"https://", target))
        return ok, "" if ok else "must be an https:// URL"
    if kind == "ntfy":
        ok = bool(re.match(r"https?://[^/]+/.+", target))
        return ok, "" if ok else "must be a full ntfy topic URL"
    return False, f"unknown channel kind '{kind}'"


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        print(send(sys.argv[1], sys.argv[2], "CTI test", "This is a test alert from Blue Team CTI."))
