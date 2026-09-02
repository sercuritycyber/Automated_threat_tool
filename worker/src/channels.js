// Per-subscriber delivery channels. Ported from lib/channels.py.
//   await send(env, cfg, kind, target, subject, body, { jsonPayload, priority, listUnsub })
import * as discord from "./discord.js";

const UA = "SecurityCyber-CTI/1.0 (+https://securitycyber.uk)";

const res = (kind, ok, ref = "", error = "") => ({ kind, ok, ref, error });

function teaser(text, limit, short) {
  if (short && short.length <= limit) return short;
  if (text.length <= limit) return text;
  const hook = text.trim().split("\n")[0].trim();
  const m = text.match(/https?:\/\/\S+/);
  const url = m ? m[0].replace(/[).,]+$/, "") : "";
  const tail = url ? `\n\n${url}` : "";
  const room = limit - tail.length;
  return ((hook.length > room ? hook.slice(0, Math.max(0, room - 1)).trimEnd() + "…" : hook) + tail).slice(0, limit);
}

// ---- availability -------------------------------------------------------
// Which delivery kinds can actually work right now. email needs Resend and
// telegram needs the bot; the rest only need a URL/topic the subscriber
// supplies, so they're always on unless CONFIG.channels_disabled says no.
// Drives the greyed-out "unavailable" options on the signup form and the
// server-side reject, so a half-configured box degrades instead of erroring.
export function channelAvailability(env, cfg) {
  const off = new Set(cfg.channels_disabled || []);
  const mk = (ok, why = "") => ({ ok, why });
  const out = {
    email: env.RESEND_API_KEY ? mk(true) : mk(false, "email delivery isn't switched on yet"),
    telegram: (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_BOT_USERNAME)
      ? mk(true) : mk(false, "the Telegram bot isn't connected yet"),
    // Pull, not push: we mint the URL, the reader fetches it. Nothing to configure.
    rss: mk(true),
    // Join the SecurityCyber Discord in one click; alerts arrive as bot DMs.
    discord: discord.discordConfigured(env)
      ? mk(true) : mk(false, "our Discord isn't connected yet"),
    // Advanced: push into a server the subscriber runs themselves.
    discord_webhook: mk(true),
    slack: mk(true),
    webhook: mk(true),
    ntfy: mk(true),
  };
  for (const k of Object.keys(out)) if (off.has(k)) out[k] = mk(false, "temporarily unavailable");
  return out;
}

// ---- Resend (email) ----------------------------------------------------
async function sendEmail(env, cfg, to, subject, body, jsonPayload, listUnsub, htmlBody) {
  if (!env.RESEND_API_KEY) return res("email", false, "", "RESEND_API_KEY not set");
  const from = `${cfg.brand} CTI <${cfg.from_email}>`;
  const payload = {
    from, to: [to], subject,
    text: body,
    html: htmlBody || undefined,
    reply_to: cfg.support_email || undefined,
  };
  if (listUnsub) payload.headers = { "List-Unsubscribe": `<${listUnsub}>`, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click" };
  if (jsonPayload) payload.attachments = [{
    filename: "cti.json",
    content: btoa(unescape(encodeURIComponent(JSON.stringify(jsonPayload, null, 2)))),
  }];
  try {
    const r = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) return res("email", false, "", `HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
    return res("email", true, ((await r.json()).id) || to);
  } catch (e) { return res("email", false, "", String(e).slice(0, 200)); }
}

// ---- Discord ---------------------------------------------------------
async function sendDiscord(url, subject, body) {
  if (!/^https:\/\/(discord|discordapp)\.com\/api\/webhooks\//.test(url || ""))
    return res("discord", false, "", "not a discord.com/api/webhooks URL");
  let content = `**${subject}**\n${body}`;
  if (content.length > 2000) content = content.slice(0, 1970).trimEnd() + "\n…";
  try {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json", "User-Agent": UA },
      body: JSON.stringify({ content, flags: 4, allowed_mentions: { parse: [] } }),
    });
    return r.ok ? res("discord", true, "posted") : res("discord", false, "", `HTTP ${r.status}: ${(await r.text()).slice(0, 150)}`);
  } catch (e) { return res("discord", false, "", String(e).slice(0, 200)); }
}

// ---- Slack ----------------------------------------------------------
async function sendSlack(url, subject, body) {
  if (!/^https:\/\/hooks\.slack\.com\//.test(url || ""))
    return res("slack", false, "", "not a hooks.slack.com incoming-webhook URL");
  try {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json", "User-Agent": UA },
      body: JSON.stringify({ text: `*${subject}*\n${body}`, unfurl_links: false, unfurl_media: false }),
    });
    return r.ok ? res("slack", true, "posted") : res("slack", false, "", `HTTP ${r.status}: ${(await r.text()).slice(0, 150)}`);
  } catch (e) { return res("slack", false, "", String(e).slice(0, 200)); }
}

// ---- Telegram -----------------------------------------------------
function tgEscape(s) { return String(s || "").replace(/([_*\[\]()~`>#+\-=|{}.!])/g, "\\$1"); }
async function sendTelegram(env, chatId, subject, body) {
  const tok = env.TELEGRAM_BOT_TOKEN;
  if (!tok) return res("telegram", false, "", "TELEGRAM_BOT_TOKEN not set");
  const api = `https://api.telegram.org/bot${tok}/sendMessage`;
  try {
    let r = await fetch(api, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId, text: `*${tgEscape(subject)}*\n\n${tgEscape(body)}`.slice(0, 4096),
        parse_mode: "MarkdownV2", disable_web_page_preview: true,
      }),
    });
    if (r.ok) return res("telegram", true, "sent");
    r = await fetch(api, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: `${subject}\n\n${body}`.slice(0, 4096), disable_web_page_preview: true }),
    });
    return r.ok ? res("telegram", true, "plain") : res("telegram", false, "", `HTTP ${r.status}: ${(await r.text()).slice(0, 150)}`);
  } catch (e) { return res("telegram", false, "", String(e).slice(0, 200)); }
}

// ---- generic webhook -------------------------------------------
async function sendWebhook(env, url, subject, body, jsonPayload) {
  if (!/^https?:\/\//.test(url || "")) return res("webhook", false, "", "not a URL");
  const data = JSON.stringify({ brand: "SecurityCyber CTI", subject, text: body, ts: new Date().toISOString(), payload: jsonPayload || null });
  const headers = { "Content-Type": "application/json", "User-Agent": UA };
  if (env.SUBS_WEBHOOK_SIGNING_SECRET) {
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.SUBS_WEBHOOK_SIGNING_SECRET),
      { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
    headers["X-CTI-Signature"] = "sha256=" + [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  try {
    const r = await fetch(url, { method: "POST", headers, body: data });
    return r.ok ? res("webhook", true, `HTTP ${r.status}`) : res("webhook", false, "", `HTTP ${r.status}`);
  } catch (e) { return res("webhook", false, "", String(e).slice(0, 200)); }
}

// ntfy carries the title in an HTTP HEADER, which must be ASCII — an em-dash
// or middle-dot in an alert title threw a TypeError and killed the delivery.
function asciiHeader(s) {
  return String(s || "")
    .replace(/[\u2010-\u2015]/g, "-").replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"').replace(/\u2026/g, "...").replace(/\u00B7/g, "-")
    // eslint-disable-next-line no-control-regex
    .replace(/[^\x20-\x7E]/g, "");
}

// ---- ntfy -------------------------------------------------------
async function sendNtfy(url, subject, body, priority) {
  if (!/^https?:\/\/[^/]+\/.+/.test(url || "")) return res("ntfy", false, "", "not a topic URL");
  const pr = { instant: "high", digest: "default", default: "default" }[priority] || "default";
  try {
    const r = await fetch(url, {
      method: "POST", body,
      headers: { Title: asciiHeader(subject).slice(0, 250), Priority: pr, Tags: "warning,shield", "User-Agent": UA },
    });
    return r.ok ? res("ntfy", true, "posted") : res("ntfy", false, "", `HTTP ${r.status}`);
  } catch (e) { return res("ntfy", false, "", String(e).slice(0, 200)); }
}

// ---- dispatch -------------------------------------------------
export async function send(env, cfg, kind, target, subject, body, opts = {}) {
  const { jsonPayload = null, priority = "default", listUnsub = "", shortText = "", html = "" } = opts;
  switch (kind) {
    case "email": return sendEmail(env, cfg, target, subject, body, jsonPayload, listUnsub, html);
    case "discord": {
      // target is their Discord user id — DM it, so their own score threshold
      // and vendor filter still decide what they receive.
      try {
        const id = await discord.dm(env, target, `**${subject}**\n${teaser(body, 1800, shortText)}`);
        return res("discord", true, id);
      } catch (e) { return res("discord", false, "", String(e).slice(0, 200)); }
    }
    case "discord_webhook": return sendDiscord(target, subject, teaser(body, 1900, shortText));
    case "slack": return sendSlack(target, subject, body);
    case "telegram": return sendTelegram(env, target, subject, teaser(body, 3900, shortText));
    case "webhook": return sendWebhook(env, target, subject, body, jsonPayload);
    case "ntfy": return sendNtfy(target, subject, body, priority);
    // rss is pull, not push: the item is already in posts_log and appears the
    // next time the reader polls /feed/<token>.xml. Report success so the
    // delivery is recorded and not retried.
    case "rss": return res("rss", true, "feed");
    default: return res(kind, false, "", `unknown channel '${kind}'`);
  }
}

export function verifyTarget(kind, target) {
  target = (target || "").trim();
  switch (kind) {
    case "email": return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(target) ? [true, ""] : [false, "not a valid email address"];
    // discord targets are Discord user ids (snowflakes) captured via OAuth —
    // never typed in, so this only guards against corruption.
    case "discord": return /^\d{15,25}$/.test(target) ? [true, ""] : [false, "connect Discord with the button instead"];
    case "discord_webhook": return /^https:\/\/(discord|discordapp)\.com\/api\/webhooks\/\d+\//.test(target)
      ? [true, ""] : [false, "must be a Discord webhook URL (Server Settings → Integrations → Webhooks)"];
    case "slack": return target.startsWith("https://hooks.slack.com/") ? [true, ""] : [false, "must be a Slack incoming-webhook URL"];
    case "telegram": return /^-?\d+$/.test(target) ? [true, ""] : [false, "expected a numeric chat id — use the bot link"];
    case "webhook": return /^https:\/\//.test(target) ? [true, ""] : [false, "must be an https:// URL"];
    case "ntfy": return /^https?:\/\/[^/]+\/.+/.test(target) ? [true, ""] : [false, "must be a full ntfy topic URL"];
    // rss targets are the feed token WE mint, never user input — so any
    // non-empty opaque id is valid here.
    case "rss": return /^[A-Za-z0-9_-]{16,}$/.test(target) ? [true, ""] : [false, "invalid feed id"];
    default: return [false, `unknown channel '${kind}'`];
  }
}

export { teaser };
