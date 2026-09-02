// SecurityCyber CTI Alerts — Cloudflare Worker entrypoint.
import { config, problems, advisories, priceToTier, monthlyPence,
  paymentLink, paymentLinkUrl, canCheckout, buyableMap } from "./config.js";
import * as db from "./db.js";
import * as billing from "./billing.js";
import { send, verifyTarget, channelAvailability } from "./channels.js";
import { sign, verify, newId, BadToken, timingSafeEqual } from "./tokens.js";
import { page, landing, subscribeForm, managePage, simple, briefPage, paidPage, feedXml, LOGO_SVG, chanLabel } from "./pages.js";
import { legalHtml } from "./legal.js";
import { html, text, json, redirect, q, esc, clientIp, rateLimit, nowIso } from "./util.js";

const TERMS_VERSION = db.TERMS_VERSION_DEFAULT;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const cfg = config(env);
    try {
      return await route(request, env, ctx, url, path, cfg);
    } catch (e) {
      console.error("worker error", path, e && e.stack || e);
      return html(page(cfg, "Something went wrong",
        "<h1>Something went wrong</h1><p class=lede>Please try again, or email us.</p>"), 500);
    }
  },

  async scheduled(event, env, ctx) {
    const cfg = config(env);
    const D = env.DB;
    // 03:30 Sunday cron = retention; everything else = digest.
    // Matched on the time fields only — the day-of-week is written as SUN
    // (Cloudflare rejects `0`), and an exact-string compare would silently
    // turn every retention run into a second digest.
    const isRetention = /^\s*30\s+3\s/.test(event.cron || "");
    if (isRetention) {
      const a = await db.purgeUnconfirmed(D, cfg.retention_days_unconfirmed);
      const b = await db.purgeCancelled(D, cfg.retention_days_cancelled);
      const c = await db.purgeIntents(D, 48);
      await db.trimPostsLog(D, 500);
      await db.kvSet(D, "last_retention", nowIso());
      console.log(`retention: erased ${a} unconfirmed, ${b} cancelled, ${c} intents`);
    } else {
      const posts = await db.postsSince(D, cfg.digest_window_hours || 24);
      if (posts.length) {
        const subs = (await db.activeForDelivery(D)).filter((s) => s.status === "active");
        for (const s of subs) await deliverDigest(env, cfg, s, posts).catch((e) => console.error("digest", s.id, e));
      }
      await db.kvSet(D, "last_digest", nowIso());
      console.log(`digest: ${posts.length} posts to active subscribers`);
    }
  },
};

// ---------------------------------------------------------------- router
async function route(request, env, ctx, url, path, cfg) {
  const m = request.method;
  const D = env.DB;

  if (path === "/logo.svg")
    return new Response(LOGO_SVG, { headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" } });
  if (path === "/healthz") {
    try { await db.stats(D); return text("ok"); }
    catch (e) { console.error("healthz", e); return text("degraded", 503); }
  }
  if (path === "/robots.txt")
    return text("User-agent: *\nAllow: /$\nAllow: /subscribe\nAllow: /brief\nAllow: /privacy\nAllow: /terms\nAllow: /cookies\n"
      + "Disallow: /manage\nDisallow: /confirm\nDisallow: /unsubscribe\nDisallow: /welcome\nDisallow: /brief/thanks\n"
      + "Disallow: /channel\nDisallow: /webhooks\nDisallow: /internal\nDisallow: /admin\nDisallow: /account\nDisallow: /feed\n");

  // ---- private RSS feed ----
  // /feed/<token>.xml — the token is the subscriber's rss channel target, so
  // the URL IS the credential (readers can't send auth headers). Access is
  // re-checked against subscription status on every poll: stop paying and the
  // feed stops carrying alerts.
  if (path.startsWith("/feed/") && m === "GET") return rssFeed(request, env, cfg, path);

  // ---- legal ----
  if (["/privacy", "/terms", "/cookies"].includes(path) && m === "GET") {
    const L = legalHtml(cfg, path.slice(1));
    return L ? html(page(cfg, L.title, L.html)) : html(page(cfg, "Not found", "<h1>Not found</h1>"), 404);
  }

  if (!cfg_enabled(env) && !path.startsWith("/webhooks") && !path.startsWith("/internal") && !path.startsWith("/admin")) {
    return html(page(cfg, "Not open yet",
      "<h1>Not open yet</h1><p class=lede>The SecurityCyber CTI alert service isn't taking subscribers right now.</p>"));
  }

  // ---- landing ----
  if (path === "/" && m === "GET")
    return html(landing(cfg, url.searchParams.get("m") || "", url.searchParams.get("k") || "", buyable(cfg, env)));

  if (path === "/account" && m === "POST") return account(request, env, cfg);

  // ---- subscribe ----
  if (path === "/subscribe" && m === "GET") {
    const tier = url.searchParams.get("tier") || "individual";
    if (!cfg.tiers[tier]) return redirect("/?m=" + q("That plan isn't available."));
    const t = cfg.tiers[tier];
    // Checkout is live when the tier has a price in pence AND there's some way
    // to take the money — a Payment Link for either cadence, or a secret key
    // for an API session. Blank Stripe price ids must NOT hide the form.
    const priced = Number(t.monthly_pence) > 0 || Number(t.weekly_pence) > 0;
    const payable = canCheckout(cfg, env, tier, "monthly") || canCheckout(cfg, env, tier, "weekly");
    if (!priced || !payable)
      return html(simple(cfg, "Almost there", "Checkout isn't switched on yet",
        "Billing for this plan is being finalised — check back shortly."));
    // ?cadence= carries the monthly/weekly choice made on the pricing cards.
    const pre = url.searchParams.get("cadence");
    return html(subscribeForm(cfg, tier, url.searchParams.get("m") || "", url.searchParams.get("k") || "",
      channelAvailability(env, cfg), ["monthly", "weekly"].includes(pre) ? pre : "", buyable(cfg, env)));
  }
  if (path === "/subscribe" && m === "POST") return subscribeSubmit(request, env, cfg);

  // Where the subscription Payment Links redirect after payment. NOT the
  // webhook — that's /webhooks/stripe, which Stripe's servers call directly.
  if (path === "/welcome" && m === "GET") return html(paidPage(cfg));

  // ---- one-off threat brief (Checkout `payment` mode) ----
  if (path === "/brief" && m === "GET") {
    if (!buyable(cfg, env).__brief)
      return html(simple(cfg, "Almost there", "Not available right now",
        "One-off briefs aren't on sale at the moment — check back shortly."));
    return html(briefPage(cfg, url.searchParams.get("m") || "", url.searchParams.get("k") || ""));
  }
  if (path === "/brief/buy" && m === "POST") return briefBuy(request, env, cfg);
  // Where the one-off brief Payment Link redirects after payment.
  if (path === "/brief/thanks" && m === "GET")
    return html(paidPage(cfg, {
      heading: "Payment received",
      lede: "Thank you — your threat brief is booked in.",
      steps: [
        ["Check your email", "A receipt is on its way to the address you paid with."],
        ["Tell us more, if you want to", "Reply to that receipt with anything else you'd like the brief to cover — your stack, what's worrying you, how deep to go."],
        ["We write it", "An analyst-written brief, researched from the primary sources, emailed to you — usually within two working days."],
      ],
      helpTitle: "No receipt after a few minutes?",
      helpBody: "Check spam or junk first. Your payment is safe either way — get in touch and we'll sort it.",
    }));

  // ---- webhooks ----
  if (path === "/webhooks/stripe" && m === "POST") return stripeWebhook(request, env, cfg);
  if (path === "/webhooks/telegram" && m === "POST") return telegramWebhook(request, env, cfg);

  // ---- confirm ----
  if (path === "/confirm" && m === "GET") return confirm(request, env, cfg, url);

  // ---- manage ----
  if (path === "/manage" && m === "GET") return managePageRoute(request, env, cfg, url);
  if (path === "/manage/prefs" && m === "POST") return managePrefs(request, env, cfg);
  if (path === "/manage/channel/add" && m === "POST") return manageChannelAdd(request, env, cfg);
  if (path === "/manage/channel/remove" && m === "POST") return manageChannelRemove(request, env, cfg);
  if (path === "/manage/test" && m === "POST") return manageTest(request, env, cfg, ctx);
  if (path === "/manage/portal" && m === "POST") return managePortal(request, env, cfg);
  if (path === "/manage/export" && m === "GET") return manageExport(request, env, cfg, url);
  if (path === "/manage/delete" && m === "POST") return manageDelete(request, env, cfg);
  if (path === "/unsubscribe" && m === "GET") return unsubscribe(request, env, cfg, url);
  if (path === "/channel/telegram" && m === "GET") return telegramLink(request, env, cfg, url);

  // ---- pipeline -> worker ----
  if (path === "/internal/ingest" && m === "POST") return internalIngest(request, env, cfg, ctx);

  // ---- owner admin API ----
  if (path.startsWith("/admin/")) return adminApi(request, env, cfg, url, path);

  return html(page(cfg, "Not found", "<h1>Not found</h1>"), 404);
}

function cfg_enabled(env) {
  // service is "enabled" unless explicitly turned off via CONFIG {"enabled": false}
  try { const c = env.CONFIG ? JSON.parse(env.CONFIG) : {}; return c.enabled !== false; } catch { return true; }
}

// What the storefront may offer, per tier+cadence, plus the one-off brief.
// In checkout_mode "link_only" that means "has a Stripe Payment Link".
function buyable(cfg, env) {
  const map = buyableMap(cfg, env);
  map.__brief = !!(cfg.brief || {}).link
    || (cfg.checkout_mode !== "link_only" && !!env.STRIPE_SECRET_KEY);
  return map;
}

function rl(request, bucket, cfg, limit) {
  const r = rateLimit(`${bucket}:${clientIp(request)}`, limit || cfg.rate_limit_per_min || 20);
  return r.ok ? null : text("Too many requests — slow down.", 429, { "Retry-After": String(r.retryAfter) });
}

async function form(request) {
  const fd = await request.formData();
  const o = {};
  for (const [k, v] of fd.entries()) o[k] = typeof v === "string" ? v : "";
  return o;
}

async function notifyOwner(env, cfg, msg) {
  // ntfy if configured, else nothing (owner also sees it in the panel)
  if (env.OWNER_NTFY_URL) {
    try { await fetch(env.OWNER_NTFY_URL, { method: "POST", body: `CTI: ${msg}` }); } catch { /* */ }
  }
}

// ---------------------------------------------------------------- account link
async function account(request, env, cfg) {
  const over = rl(request, "account", cfg, 5); if (over) return over;
  const f = await form(request);
  const sub = await db.getByEmail(env.DB, (f.email || "").trim().toLowerCase());
  if (sub && ["active", "paused", "past_due", "unconfirmed"].includes(sub.status)) {
    const t = await sign(env, { sub: sub.id, act: "manage" }, 90 * 86400);
    const u = await sign(env, { sub: sub.id, act: "unsubscribe" }, 90 * 86400);
    await send(env, cfg, "email", sub.email, "Your SecurityCyber CTI preference link",
      `Manage your alert preferences or unsubscribe:\n\n${cfg.site_url}/manage?t=${t}\n\nIf you didn't ask for this, ignore it.`,
      { listUnsub: `${cfg.site_url}/unsubscribe?t=${u}` });
  }
  return redirect("/?m=" + q("If that address has an account, a link is on its way."));
}

// ---------------------------------------------------------------- private RSS
// Statuses that still entitle a subscriber to alerts. `past_due` keeps working
// briefly on purpose — Stripe retries a failed card for a few days and we'd
// rather not cut off a good customer over a bank blip; `cancelled` does not.
const FEED_OK = ["active"];
const FEED_GRACE = ["past_due"];

async function rssFeed(request, env, cfg, path) {
  const over = rl(request, "feed", cfg, 120); if (over) return over;
  const token = path.slice("/feed/".length).replace(/\.(xml|rss)$/i, "");
  const xml = (body, status = 200, cache = "private, max-age=300") =>
    new Response(body, { status, headers: {
      "content-type": "application/rss+xml; charset=utf-8",
      "cache-control": cache,
      "x-robots-tag": "noindex, nofollow",
    } });

  if (!/^[A-Za-z0-9_-]{16,}$/.test(token)) return xml("not found", 404, "no-store");
  const sub = await db.subscriberByFeedToken(env.DB, token);
  // Unknown token: 404, and never say whether it merely lapsed.
  if (!sub) return xml("not found", 404, "no-store");

  const selfUrl = `${cfg.site_url}/feed/${token}.xml`;
  const entitled = FEED_OK.includes(sub.status) || FEED_GRACE.includes(sub.status);
  if (!entitled) {
    // A dead feed is confusing in a reader, so say why in an item instead of
    // 402-ing into a silent red icon.
    return xml(feedXml(cfg, [{
      post_key: `lapsed:${sub.id}:${sub.status}`,
      title: `Your ${cfg.brand} CTI subscription is no longer active`,
      url: `${cfg.site_url}/account`,
      seen_at: nowIso(),
      post_text: `This feed has stopped updating because the subscription is ${sub.status}. `
        + `Restart it at ${cfg.site_url}/account and alerts resume immediately — the feed URL stays the same.`,
    }], { selfUrl, title: `${cfg.brand} CTI Alerts — inactive`, note: "Subscription inactive." }), 200, "no-store");
  }

  const prefs = await db.getPrefs(env.DB, sub.id);
  const tier = cfg.tiers[sub.tier] || {};
  const floor = prefs?.min_score_override ?? tier.instant_min_score ?? 0;
  const vendors = (prefs?.vendors || []).map((v) => v.toLowerCase()).filter(Boolean);
  let posts = await db.postsForFeed(env.DB, 60);
  posts = posts.filter((p) => (p.score ?? 0) >= floor).filter((p) => {
    if (!vendors.length) return true;
    const hay = `${p.title} ${p.post_text} ${p.source}`.toLowerCase();
    return vendors.some((v) => hay.includes(v));
  }).slice(0, 50);

  const note = `Alerts scoring ${floor}+ for ${sub.email}`
    + (vendors.length ? `, filtered to ${vendors.join(", ")}` : "")
    + (FEED_GRACE.includes(sub.status) ? " — payment needs attention, see /account" : "");
  return xml(feedXml(cfg, posts, { selfUrl, title: `${cfg.brand} CTI Alerts`, note }));
}

// ---------------------------------------------------------------- subscribe
async function subscribeSubmit(request, env, cfg) {
  const over = rl(request, "subscribe", cfg, 10); if (over) return over;
  const f = await form(request);
  const tier = f.tier, email = (f.email || "").trim().toLowerCase();
  if (!cfg.tiers[tier]) return redirect("/?m=" + q("That plan isn't available."));
  if (!f.agree) return redirect(`/subscribe?tier=${tier}&m=` + q("Please accept the terms to continue.") + "&k=bad");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return redirect(`/subscribe?tier=${tier}&m=` + q("That email doesn't look right.") + "&k=bad");
  let cadence = ["monthly", "weekly"].includes(f.cadence) ? f.cadence : "monthly";
  const tcfg = cfg.tiers[tier];
  let platform = (tcfg.channels || []).includes(f.platform) ? f.platform : "email";
  // Don't take money for a delivery route that can't run yet.
  const avail = channelAvailability(env, cfg);
  if (!(avail[platform] || { ok: true }).ok)
    return redirect(`/subscribe?tier=${tier}&m=` + q(`${chanLabel(platform)} is ${avail[platform].why}. Pick another delivery method.`) + "&k=bad");
  let target = "";
  if (platform === "email") target = email;
  // rss: we mint the feed id ourselves — it becomes the secret in the URL.
  else if (platform === "rss") target = newId(24);
  else if (platform !== "telegram") {
    const [ok, why] = verifyTarget(platform, f.target || "");
    if (!ok) return redirect(`/subscribe?tier=${tier}&m=` + q(why) + "&k=bad");
    target = (f.target || "").trim();
  }
  const intentTok = newId();
  await db.createIntent(env.DB, {
    token: intentTok, email, tier, cadence,
    channel_kind: platform === "telegram" ? null : platform,
    channel_target: target || null, ip: clientIp(request), user_agent: request.headers.get("user-agent") || "",
  });
  await db.logConsent(env.DB, null, "terms", true, {
    ip: clientIp(request), ua: request.headers.get("user-agent") || "",
    terms_version: cfg.terms_version, detail: `accepted at signup (${email})`,
  });
  // Prefer a Stripe Payment Link when one is configured: no API call, no
  // secret key needed, and the intent still rides along on the query string.
  const link = paymentLinkUrl(paymentLink(cfg, tier, cadence), { clientRef: intentTok, email });
  if (link) {
    await db.logEvent(env.DB, "checkout:payment_link", null, { tier, cadence, email });
    return redirect(link);
  }
  try {
    const sess = await billing.createCheckoutSession(env, cfg, {
      tier, cadence, email,
      successUrl: `${cfg.site_url}/welcome`,
      cancelUrl: `${cfg.site_url}/subscribe?tier=${tier}&m=` + q("Checkout cancelled — no charge made."),
      clientRef: intentTok,
    });
    return redirect(sess.url);
  } catch (e) {
    // Never surface the Stripe error verbatim — it quotes the API key back
    // ("Invalid API Key provided: sk_test_…"). Log it, page the owner, and
    // show the visitor something they can act on.
    console.error("checkout", e);
    await notifyOwner(env, cfg, `checkout failed for ${email} (${tier}/${cadence}): ${e}`);
    return html(simple(cfg, "Checkout error", "Couldn't start checkout",
      "Something went wrong on our side setting up the payment — you have not been charged. "
      + "Please try again in a moment.",
      `<p><a href='/subscribe?tier=${tier}'>Try again</a> · `
      + `<a href='mailto:${esc(cfg.support_email)}'>tell us</a></p>`));
  }
}

// ---------------------------------------------------------------- one-off brief
async function briefBuy(request, env, cfg) {
  const over = rl(request, "brief", cfg, 10); if (over) return over;
  const f = await form(request);
  const email = (f.email || "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email))
    return redirect("/brief?m=" + q("That email doesn't look right.") + "&k=bad");
  if (f.topic) await db.kvSet(env.DB, "brief_topic:" + email, String(f.topic).slice(0, 500));
  // Payment Link first. There's no signup intent for a one-off, so the email
  // is the only thing to carry over — the webhook matches the purchase on the
  // address Stripe collects.
  const bLink = paymentLinkUrl((cfg.brief || {}).link, { email });
  if (bLink) {
    await db.logEvent(env.DB, "checkout:brief_payment_link", null, { email });
    return redirect(bLink);
  }
  try {
    const sess = await billing.createBriefCheckout(env, cfg, {
      email,
      successUrl: `${cfg.site_url}/brief/thanks`,
      cancelUrl: `${cfg.site_url}/brief?m=` + q("Checkout cancelled — no charge made."),
    });
    return redirect(sess.url);
  } catch (e) {
    console.error("brief checkout", e);
    await notifyOwner(env, cfg, `brief checkout failed for ${email}: ${e}`);
    return html(simple(cfg, "Checkout error", "Couldn't start checkout",
      "Something went wrong on our side setting up the payment — you have not been charged. "
      + "Please try again in a moment.",
      `<p><a href='/brief'>Try again</a> · <a href='mailto:${esc(cfg.support_email)}'>tell us</a></p>`));
  }
}

// ---------------------------------------------------------------- stripe webhook
async function stripeWebhook(request, env, cfg) {
  const payload = await request.text();
  const sig = request.headers.get("stripe-signature") || "";
  let event;
  try { event = await billing.verifyWebhook(payload, sig, env.STRIPE_WEBHOOK_SECRET); }
  catch (e) {
    // Unauthenticated endpoint: log the reason, don't publish it. It used to
    // answer "STRIPE_WEBHOOK_SECRET not set" to anyone who POSTed here.
    console.error("stripe webhook rejected", e);
    if (!env.STRIPE_WEBHOOK_SECRET) await notifyOwner(env, cfg, "Stripe webhook rejected: STRIPE_WEBHOOK_SECRET is not set");
    return json({ error: "invalid signature" }, 400);
  }
  let res;
  try { res = await billing.handleEvent(env, cfg, event); }
  catch (e) { console.error("handleEvent", e); await notifyOwner(env, cfg, `webhook handler error: ${e}`); return json({ received: true, handler_error: String(e).slice(0, 200) }); }

  if (res.action === "send_confirm") {
    const t = await sign(env, { sub: res.subscriber_id, act: "confirm" }, 14 * 86400);
    const where = res.channel ? chanLabel(res.channel.kind) : "email";
    await send(env, cfg, "email", res.email, "Confirm your SecurityCyber CTI alerts",
      `Thanks for subscribing — ${res.tier} plan, billed ${res.cadence}.\n\n` +
      `Confirm your email to switch your alerts on:\n\n${cfg.site_url}/confirm?t=${t}\n\n` +
      `Your alerts will go to: ${where}.\nThis link works for 14 days. If you didn't subscribe, ignore this and email ${cfg.contact_email}.`);
    await db.logEvent(env.DB, "confirm_email_sent", res.subscriber_id, { cadence: res.cadence });
  } else if (res.action === "brief_purchased") {
    const topic = res.email ? await db.kvGet(env.DB, "brief_topic:" + res.email) : null;
    const amount = res.purchase?.amount_total != null
      ? `${(res.purchase.currency || "gbp").toUpperCase()} ${(res.purchase.amount_total / 100).toFixed(2)}` : "";
    if (res.email) {
      await send(env, cfg, "email", res.email, "Your SecurityCyber threat brief — receipt",
        `Thanks — payment received${amount ? ` (${amount})` : ""}.\n\n` +
        `We'll research and send your threat brief by email, usually within two working days.\n` +
        (topic ? `\nYou asked us to cover: ${topic}\n` : `\nReply to this email to tell us what you'd like the brief to cover.\n`) +
        `\nQuestions: ${cfg.contact_email}`);
    }
    await notifyOwner(env, cfg, `threat brief purchased${res.email ? " by " + res.email : ""}${topic ? " — " + topic : ""}`);
  } else if (res.action === "notify_owner") {
    await notifyOwner(env, cfg, res.detail || "payment issue");
  } else if (["cancelled", "recovered"].includes(res.action)) {
    await notifyOwner(env, cfg, `subscriber ${res.action}`);
  }
  return json({ received: true, action: res.action });
}

// ---------------------------------------------------------------- confirm
async function confirm(request, env, cfg, url) {
  const over = rl(request, "confirm", cfg, 15); if (over) return over;
  let data;
  try { data = await verify(env, url.searchParams.get("t") || "", "confirm"); }
  catch (e) { return html(simple(cfg, "Link problem", "That link didn't work", `${e}. Use “Already subscribed?” on the home page for a fresh one.`)); }
  const sub = await db.get(env.DB, data.sub);
  if (!sub) return html(simple(cfg, "Not found", "Account not found", "It may have been removed."));
  let note = "";
  if (sub.status !== "cancelled") {
    await db.setStatus(env.DB, sub.id, "active", "email confirmed", clientIp(request));
    await db.logConsent(env.DB, sub.id, "signup", true, {
      ip: clientIp(request), ua: request.headers.get("user-agent") || "",
      terms_version: cfg.terms_version, detail: "double opt-in via /confirm",
    });
    for (const ch of sub.channels) {
      if (ch.kind === "email" || ch.verified) continue;
      // rss is pull, not push — there's nothing to test-ping. Mark it good and
      // email the URL below instead.
      if (ch.kind === "rss") { await db.verifyChannel(env.DB, ch.id); continue; }
      const r = await send(env, cfg, ch.kind, ch.target, "SecurityCyber CTI — you're connected",
        "This channel is now linked. Real alerts will arrive here.", { priority: "default" });
      if (r.ok) await db.verifyChannel(env.DB, ch.id);
      else note = ` We couldn't reach your ${chanLabel(ch.kind)} — check it on the next page.`;
    }
    // A feed URL is useless until the subscriber actually has it, and it's the
    // one channel we can't push to — so mail it out the moment they're live.
    await emailFeedUrl(env, cfg, sub);
    await notifyOwner(env, cfg, `new subscriber confirmed (${sub.tier}, ${sub.cadence})`);
  }
  const mtok = await sign(env, { sub: sub.id, act: "manage" }, 90 * 86400);
  return redirect(`/manage?t=${mtok}&m=` + q("You're in — alerts are live." + note) + (note ? "&k=bad" : "&k=good"));
}

// Send the subscriber their private feed URL. Called on confirmation and
// whenever they mint a new feed, because RSS is the one channel we can't
// deliver to until they've pasted the URL into a reader.
async function emailFeedUrl(env, cfg, sub) {
  const rss = (sub.channels || []).find((c) => c.kind === "rss");
  if (!rss) return null;
  const url = `${cfg.site_url}/feed/${rss.target}.xml`;
  const r = await send(env, cfg, "email", sub.email, `Your private ${cfg.brand} CTI feed URL`,
    `Your alerts are live.\n\nAdd this address to your RSS reader — Feedly, Inoreader, NetNewsWire, `
    + `Thunderbird, anything that takes a feed URL:\n\n${url}\n\n`
    + `Keep it to yourself: the link is the key, so anyone who has it can read your alerts. `
    + `If it ever leaks, remove the feed on your preferences page and add a new one — you'll get a fresh URL.\n\n`
    + `It updates for as long as your subscription is active.\n\n— ${cfg.brand}`,
    {});
  await db.logEvent(env.DB, r.ok ? "rss:url_emailed" : "rss:url_email_failed", sub.id, { error: r.error || "" });
  return r;
}

// ---------------------------------------------------------------- manage
async function manageSub(env, url) {
  try {
    const data = await verify(env, url.searchParams.get("t") || url.__t || "", "manage");
    return [await db.get(env.DB, data.sub), url.searchParams.get("t") || url.__t];
  } catch { return [null, ""]; }
}
async function manageSubFromForm(env, tok) {
  try { const data = await verify(env, tok, "manage"); return [await db.get(env.DB, data.sub), tok]; }
  catch { return [null, ""]; }
}

async function managePageRoute(request, env, cfg, url) {
  const over = rl(request, "manage", cfg, 60); if (over) return over;
  const [sub, tok] = await manageSub(env, url);
  if (!sub) return html(simple(cfg, "Link problem", "Link expired", "Use “Already subscribed?” on the home page for a fresh link."));
  return html(managePage(cfg, sub, tok, url.searchParams.get("m") || "", url.searchParams.get("k") || ""));
}

async function managePrefs(request, env, cfg) {
  const over = rl(request, "manage", cfg, 60); if (over) return over;
  const f = await form(request);
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub) return redirect("/?m=" + q("Link expired"));
  const vendors = (f.vendors || "").split(/[,\n]/).map((s) => s.trim()).filter(Boolean).slice(0, 40);
  let mso;
  const ms = (f.min_score || "").trim();
  mso = /^\d+$/.test(ms) ? Math.max(0, Math.min(100, Number(ms))) : null;
  await db.setPrefs(env.DB, sub.id, { vendors, min_score_override: mso, instant: !!f.instant, digest: !!f.digest });
  return redirect(`/manage?t=${tok}&m=` + q("Saved.") + "&k=good");
}

async function manageChannelAdd(request, env, cfg) {
  const over = rl(request, "manage", cfg, 60); if (over) return over;
  const f = await form(request);
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub) return redirect("/?m=" + q("Link expired"));
  const tcfg = cfg.tiers[sub.tier] || {};
  if (!(tcfg.channels || []).includes(f.kind) || f.kind === "telegram")
    return redirect(`/manage?t=${tok}&m=` + q("That channel isn't available on your plan.") + "&k=bad");
  if (sub.channels.filter((c) => c.kind !== "email").length >= (tcfg.max_channels || 2))
    return redirect(`/manage?t=${tok}&m=` + q("You've reached your plan's channel limit.") + "&k=bad");
  // rss has no user-supplied target: mint the feed id, and it's live at once
  // (nothing to test-ping — the reader pulls it).
  if (f.kind === "rss") {
    if (sub.channels.some((c) => c.kind === "rss"))
      return redirect(`/manage?t=${tok}&m=` + q("You already have a feed URL.") + "&k=bad");
    const feedId = newId(24);
    await db.addChannel(env.DB, sub.id, "rss", feedId, true, "self-serve");
    await db.logEvent(env.DB, "channel:rss_created", sub.id, {});
    // Mail it too, so it isn't lost the moment they close the tab.
    const sent = await emailFeedUrl(env, cfg, await db.get(env.DB, sub.id));
    return redirect(`/manage?t=${tok}&m=` + q("Your private feed URL is ready — it's below, and we've"
      + (sent && sent.ok ? " emailed it to you." : " tried to email it to you.")));
  }
  const [ok, why] = verifyTarget(f.kind, f.target || "");
  if (!ok) return redirect(`/manage?t=${tok}&m=` + q(why) + "&k=bad");
  const r = await send(env, cfg, f.kind, (f.target || "").trim(), "SecurityCyber CTI — channel connected",
    "This channel is now connected. You'll receive alerts here.", { priority: "default" });
  await db.addChannel(env.DB, sub.id, f.kind, (f.target || "").trim(), r.ok);
  const msg = r.ok ? "Channel added and test message delivered." : `Added, but the test failed: ${r.error}`;
  return redirect(`/manage?t=${tok}&m=` + q(msg) + (r.ok ? "&k=good" : "&k=bad"));
}

async function manageChannelRemove(request, env, cfg) {
  const f = await form(request);
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub) return redirect("/?m=" + q("Link expired"));
  if (sub.channels.some((c) => String(c.id) === String(f.cid) && c.kind !== "email"))
    await db.removeChannel(env.DB, Number(f.cid));
  return redirect(`/manage?t=${tok}&m=` + q("Channel removed."));
}

async function manageTest(request, env, cfg, ctx) {
  const over = rl(request, "manage", cfg, 10); if (over) return over;
  const f = await form(request);
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub) return redirect("/?m=" + q("Link expired"));
  const demo = {
    post_key: "test:" + nowIso(), title: "Test alert — SecurityCyber CTI", score: 100, cve_ids: [], url: "",
    post_text: "This is a test alert.\n\nIf you're reading this, your channel is wired up correctly and you'll get real alerts here.",
  };
  ctx.waitUntil(deliverInstant(env, cfg, sub, [demo], "test"));
  return redirect(`/manage?t=${tok}&m=` + q("Test alert sent to your verified channels.") + "&k=good");
}

async function managePortal(request, env, cfg) {
  const f = await form(request);
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub || !sub.stripe_customer_id) return redirect("/?m=" + q("No billing account found"));
  try {
    const p = await billing.portalSession(env, sub.stripe_customer_id, `${cfg.site_url}/manage?t=${tok}`);
    return redirect(p.url);
  } catch (e) { return redirect(`/manage?t=${tok}&m=` + q(`Billing portal error: ${e}`) + "&k=bad"); }
}

async function manageExport(request, env, cfg, url) {
  const [sub] = await manageSub(env, url);
  if (!sub) return text("Link expired", 403);
  const data = await db.exportSubscriber(env.DB, sub.id);
  await db.logEvent(env.DB, "dsar_export", sub.id, null, clientIp(request));
  return new Response(JSON.stringify(data, null, 2), {
    headers: { "Content-Type": "application/json", "Content-Disposition": "attachment; filename=my-securitycyber-cti-data.json" },
  });
}

async function manageDelete(request, env, cfg) {
  const f = await form(request);
  const [sub] = await manageSubFromForm(env, f.t);
  if (!sub) return html(simple(cfg, "Link problem", "Link expired", ""));
  if (sub.stripe_subscription_id) { try { await billing.cancelSubscription(env, sub.stripe_subscription_id); } catch (e) { console.error("cancel on delete", e); } }
  await db.eraseSubscriber(env.DB, sub.id, "self-service deletion");
  await notifyOwner(env, cfg, "a subscriber deleted their account");
  return html(simple(cfg, "Deleted", "Your account is deleted",
    "All your personal data has been removed and any subscription cancelled. Sorry to see you go.",
    "<p><a href='/'>Home</a></p>"));
}

async function unsubscribe(request, env, cfg, url) {
  let data;
  try { data = await verify(env, url.searchParams.get("t") || "", "unsubscribe"); }
  catch { return html(simple(cfg, "Link problem", "That unsubscribe link didn't work", "Email us and we'll sort it.")); }
  const sub = await db.get(env.DB, data.sub);
  if (sub && ["active", "past_due"].includes(sub.status)) {
    await db.setStatus(env.DB, sub.id, "paused", "unsubscribed via link", clientIp(request));
    await db.logConsent(env.DB, sub.id, "signup", false, { ip: clientIp(request), detail: "unsubscribe link" });
  }
  const mtok = sub ? await sign(env, { sub: sub.id, act: "manage" }, 90 * 86400) : "";
  return html(simple(cfg, "Alerts paused", "Alerts paused",
    "You won't get any more alerts. Your subscription and billing are unchanged.",
    mtok ? `<p><a class=btn href='/manage?t=${mtok}'>Resume or manage billing</a></p>` : ""));
}

// ---------------------------------------------------------------- telegram
async function telegramLink(request, env, cfg, url) {
  const [sub, tok] = await manageSub(env, url);
  if (!sub) return html(simple(cfg, "Link problem", "Link expired", ""));
  const bot = (env.TELEGRAM_BOT_USERNAME || "").replace(/^@/, "");
  if (!bot) return html(simple(cfg, "Telegram not set up", "Telegram isn't available yet", "The operator hasn't configured the alert bot."));
  const linkTok = await sign(env, { sub: sub.id, act: "tg_link" }, 3600);
  const u = `https://t.me/${bot}?start=${linkTok}`;
  return html(page(cfg, "Connect Telegram",
    `<h1>Connect Telegram</h1><p class=lede>Open the bot and press <b>Start</b> — you'll be linked automatically.</p>
<p><a class=btn href='${esc(u)}' rel=noopener>Open @${esc(bot)}</a></p>
<p class=muted>Link valid for 1 hour. Then return to <a href='/manage?t=${esc(tok)}'>your preferences</a>.</p>`,
    "", "", true));
}

async function telegramWebhook(request, env, cfg) {
  const over = rl(request, "tg_hook", cfg, 60); if (over) return over;
  if (env.TELEGRAM_WEBHOOK_SECRET && request.headers.get("x-telegram-bot-api-secret-token") !== env.TELEGRAM_WEBHOOK_SECRET)
    return json({ ok: false }, 403);
  let upd; try { upd = await request.json(); } catch { return json({ ok: true }); }
  const msg = upd.message || upd.edited_message || {};
  const t = (msg.text || "").trim();
  const chatId = String((msg.chat || {}).id || "");
  if (t.startsWith("/start") && chatId) {
    const payload = t.split(/\s+/, 2)[1] || "";
    try {
      const data = await verify(env, payload, "tg_link");
      const sub = await db.get(env.DB, data.sub, false);
      if (sub) {
        await db.addChannel(env.DB, sub.id, "telegram", chatId, true, "telegram");
        await tgReply(env, chatId, "✅ Linked. You'll get SecurityCyber CTI alerts here.");
        return json({ ok: true });
      }
    } catch { /* fall through */ }
    await tgReply(env, chatId, "This link is invalid or expired. Get a fresh one from your SecurityCyber CTI preferences page.");
  }
  return json({ ok: true });
}
async function tgReply(env, chatId, tx) {
  if (!env.TELEGRAM_BOT_TOKEN) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chat_id: chatId, text: tx }) });
  } catch { /* */ }
}

// ---------------------------------------------------------------- pipeline -> worker
async function internalIngest(request, env, cfg, ctx) {
  const auth = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!env.INGEST_SECRET || !timingSafeEqual(auth, env.INGEST_SECRET)) return json({ error: "unauthorized" }, 401);
  let body; try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  const posts = (body.posts || []).filter((p) => (p.post_text || "").trim());
  for (const p of posts) {
    p.post_key = normKey(p);
    await db.logPost(env.DB, p);
  }
  // instant fan-out (in the background so the pipeline call returns fast)
  ctx.waitUntil((async () => {
    const subs = (await db.activeForDelivery(env.DB)).filter((s) => s.status === "active");
    for (const s of subs) await deliverInstant(env, cfg, s, posts, "instant").catch((e) => console.error("instant", s.id, e));
  })());
  return json({ received: posts.length });
}

function normKey(p) {
  const u = (p.url || "").split("?")[0].replace(/\/$/, "").toLowerCase();
  return u || "t:" + (p.title || p.post_text || "").slice(0, 60);
}

// ---------------------------------------------------------------- delivery
function vendorMatch(post, vendors) {
  if (!vendors || !vendors.length) return true;
  const hay = ((post.title || "") + " " + (post.post_text || "") + " " + (post.cve_ids || []).join(" ")).toLowerCase();
  return vendors.some((v) => hay.includes(v.toLowerCase()));
}
function wants(post, sub, tierCfg, mode) {
  const p = sub.prefs;
  if (mode === "instant" && !p.instant) return false;
  if (mode === "digest" && !p.digest) return false;
  let thr = p.min_score_override;
  if (thr == null) thr = mode === "instant" ? (tierCfg.instant_min_score || 90) : 0;
  if (Number(post.score || 0) < Number(thr)) return false;
  return vendorMatch(post, p.vendors);
}

async function deliverInstant(env, cfg, sub, posts, mode) {
  const tierCfg = cfg.tiers[sub.tier] || {};
  const allowed = new Set(tierCfg.channels || []);
  const verified = sub.channels.filter((c) => c.verified && allowed.has(c.kind));
  if (!verified.length) return;
  for (const post of posts) {
    if (mode !== "test" && !wants(post, sub, tierCfg, "instant")) continue;
    if (mode !== "test" && await db.alreadyDelivered(env.DB, sub.id, post.post_key)) continue;
    const subject = (post.title || (post.post_text || "").split("\n")[0]).slice(0, 180);
    const jsonp = tierCfg.attach_json ? { posts: [post] } : null;
    for (const ch of verified) {
      const r = await send(env, cfg, ch.kind, ch.target, subject, post.post_text || "", {
        priority: "instant", shortText: post.short_text || "",
        jsonPayload: ["email", "webhook"].includes(ch.kind) ? jsonp : null,
      });
      await db.recordDelivery(env.DB, sub.id, post.post_key, ch.kind, mode, r.ok, r.ok ? null : r.error);
    }
  }
}

async function deliverDigest(env, cfg, sub, allPosts) {
  const tierCfg = cfg.tiers[sub.tier] || {};
  const allowed = new Set(tierCfg.channels || []);
  const verified = sub.channels.filter((c) => c.verified && allowed.has(c.kind));
  if (!verified.length) return;
  const matched = allPosts.filter((p) => wants(p, sub, tierCfg, "digest"));
  if (!matched.length) return;
  const dayKey = `digest:${new Date().toISOString().slice(0, 10)}`;
  if (await db.alreadyDelivered(env.DB, sub.id, dayKey)) return;
  const subject = `${cfg.brand} CTI — daily digest · ${matched.length} item(s)`;
  const lines = [`${matched.length} item(s) from the last 24 hours, highest impact first.`, ""];
  for (const p of matched.sort((a, b) => b.score - a.score)) {
    lines.push(`• [${p.score}] ${p.short_text || (p.post_text || "").split("\n")[0]}`);
    if ((p.cve_ids || []).length) lines.push(`  ${(p.cve_ids || []).join(", ")}`);
    if (p.url) lines.push(`  ${p.url}`);
    lines.push("");
  }
  lines.push("— Change vendors, channels or unsubscribe from your preference link.");
  const body = lines.join("\n");
  const jsonp = tierCfg.attach_json ? { posts: matched } : null;
  for (const ch of verified) {
    const r = await send(env, cfg, ch.kind, ch.target, subject, body, {
      priority: "digest", jsonPayload: ["email", "webhook"].includes(ch.kind) ? jsonp : null,
    });
    await db.recordDelivery(env.DB, sub.id, dayKey, ch.kind, "digest", r.ok, r.ok ? null : r.error);
  }
}

// ---------------------------------------------------------------- admin API (owner panel)
async function adminApi(request, env, cfg, url, path) {
  const auth = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!env.ADMIN_TOKEN || !timingSafeEqual(auth, env.ADMIN_TOKEN)) return json({ error: "unauthorized" }, 401);
  const D = env.DB;
  const seg = path.split("/").filter(Boolean); // ["admin", ...]

  if (seg[1] === "stats") {
    const s = await db.stats(D);
    let mrr = 0;
    for (const r of s.active) mrr += r.n * monthlyPence(cfg, r.tier, r.cadence || "monthly");
    return json({
      ...s, mrr_pence: mrr, problems: problems(cfg, env), advisories: advisories(cfg),
      // Presence only — never the values. Lets the owner dashboard show which
      // secrets are actually reaching the running Worker.
      secrets: Object.fromEntries(["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "RESEND_API_KEY",
        "SUBS_TOKEN_SECRET", "INGEST_SECRET", "ADMIN_TOKEN", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME", "TELEGRAM_WEBHOOK_SECRET"].map((k) => [k, !!env[k]])),
      channels: channelAvailability(env, cfg),
      live: !!cfg.live,
      stripe_mode: /^sk_live_/.test(env.STRIPE_SECRET_KEY || "") ? "live"
        : (env.STRIPE_SECRET_KEY ? "test" : "unset"),
      briefs_24h: await db.purchaseCount24h(D),
      last_digest: await db.kvGet(D, "last_digest"), last_retention: await db.kvGet(D, "last_retention"),
    });
  }
  if (seg[1] === "purchases" && request.method === "GET") {
    const rows = await db.listPurchases(D, 200);
    for (const r of rows) r.topic = r.email ? await db.kvGet(D, "brief_topic:" + r.email) : null;
    return json({ purchases: rows });
  }
  if (seg[1] === "purchases" && seg[2] && request.method === "POST" && seg[3] === "fulfilled") {
    await db.markPurchaseFulfilled(D, seg[2]);
    return json({ ok: true });
  }
  if (seg[1] === "subscribers" && request.method === "GET" && !seg[2]) {
    return json({ subscribers: await db.listSubscribers(D, { status: url.searchParams.get("status") || undefined, qstr: url.searchParams.get("q") || undefined }) });
  }
  if (seg[1] === "subscribers" && seg[2] && request.method === "GET") {
    if (seg[3] === "export") return json(await db.exportSubscriber(D, Number(seg[2])));
    const s = await db.get(D, Number(seg[2]));
    if (!s) return json({ error: "not found" }, 404);
    return json({ subscriber: s, consents: await db.consentsFor(D, s.id), deliveries: await db.deliveriesFor(D, s.id, 30), events: await db.eventsFor(D, s.id, 40) });
  }
  if (seg[1] === "subscribers" && seg[2] && request.method === "POST") {
    const b = await request.json().catch(() => ({}));
    const id = Number(seg[2]);
    if (seg[3] === "erase") {
      const s = await db.get(D, id, false);
      if (s && s.stripe_subscription_id) { try { await billing.cancelSubscription(env, s.stripe_subscription_id); } catch { /* */ } }
      await db.eraseSubscriber(D, id, "operator DSAR erasure");
      return json({ ok: true });
    }
    if (seg[3] === "edit") {
      if (b.tier && cfg.tiers[b.tier]) await db.setTier(D, id, b.tier);
      if (b.status) await db.setStatus(D, id, b.status, "operator edit");
      if (b.notes != null) await db.setNotes(D, id, b.notes);
      await db.setPrefs(D, id, {
        vendors: b.vendors, min_score_override: b.min_score === "" ? null : (b.min_score != null ? Number(b.min_score) : undefined),
        instant: b.instant, digest: b.digest,
      });
      return json({ ok: true });
    }
    if (seg[3] === "channel") {
      if (b.remove) await db.removeChannel(D, Number(b.remove));
      else if (b.kind && b.target) await db.addChannel(D, id, b.kind, b.target, b.verified !== false, "operator");
      return json({ ok: true });
    }
    if (seg[3] === "test") {
      const s = await db.get(D, id);
      if (s) await deliverInstant(env, cfg, s, [{ post_key: "test:" + nowIso(), title: "Test alert", score: 100, cve_ids: [], post_text: "Operator test alert." }], "test");
      return json({ ok: true });
    }
  }
  // ---- read-only D1 browser for the owner dashboard ----
  // GET /admin/db            -> table names + row counts
  // GET /admin/db/<table>    -> ?limit=&offset= rows, newest first where possible
  if (seg[1] === "db" && request.method === "GET" && !seg[2]) {
    const { results } = await D.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' ORDER BY name").all();
    const tables = [];
    for (const r of results || []) {
      const c = await D.prepare(`SELECT COUNT(*) n FROM "${r.name}"`).first().catch(() => ({ n: null }));
      tables.push({ name: r.name, rows: c?.n ?? null });
    }
    return json({ tables });
  }
  if (seg[1] === "db" && seg[2] && request.method === "GET") {
    const { results } = await D.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%'").all();
    const names = (results || []).map((r) => r.name);
    if (!names.includes(seg[2])) return json({ error: "unknown table" }, 404);   // no injection: whitelisted
    const limit = Math.min(Number(url.searchParams.get("limit")) || 100, 500);
    const offset = Math.max(Number(url.searchParams.get("offset")) || 0, 0);
    const cols = (await D.prepare(`PRAGMA table_info("${seg[2]}")`).all()).results || [];
    const order = cols.some((c) => c.name === "id") ? 'ORDER BY "id" DESC' : "";
    const rows = (await D.prepare(`SELECT * FROM "${seg[2]}" ${order} LIMIT ? OFFSET ?`).bind(limit, offset).all()).results || [];
    const total = (await D.prepare(`SELECT COUNT(*) n FROM "${seg[2]}"`).first())?.n ?? null;
    return json({ table: seg[2], columns: cols.map((c) => c.name), rows, total, limit, offset });
  }
  if (seg[1] === "add" && request.method === "POST") {
    const b = await request.json().catch(() => ({}));
    if (!/@/.test(b.email || "")) return json({ error: "bad email" }, 400);
    const id = await db.createPending(D, { email: b.email, tier: b.tier || "individual", cadence: b.cadence || "monthly", source: "comp" });
    await db.setNotes(D, id, b.note || "manual add");
    if (b.skip_confirm) { await db.setStatus(D, id, "active", "operator manual add"); await db.logConsent(D, id, "signup", true, { detail: "operator add, confirm skipped" }); }
    else await db.setStatus(D, id, "unconfirmed", "manual add");
    return json({ ok: true, id });
  }
  return json({ error: "unknown admin route" }, 404);
}
