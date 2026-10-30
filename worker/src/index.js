import {route as advisoryRoute} from "./advisories.js";
// SecurityCyber CTI Alerts — Cloudflare Worker entrypoint.
import { config, problems, advisories, priceToTier, monthlyPence,
  paymentLink, paymentLinkUrl, canCheckout, buyableMap, takingRealMoney, MOTTO_SHORT } from "./config.js";
import * as db from "./db.js";
import * as billing from "./billing.js";
import * as discord from "./discord.js";
import { alertEmailHtml } from "./emailtpl.js";
import { send, verifyTarget, channelAvailability } from "./channels.js";
import { sign, verify, newId, BadToken, timingSafeEqual } from "./tokens.js";
import { page, landing, subscribeForm, managePage, simple, briefPage, paidPage, feedXml, LOGO_SVG, chanLabel, freeLanding } from "./pages.js";
import { legalHtml } from "./legal.js";
import * as taxii from "./taxii.js";
import { html, text, json, redirect, q, esc, clientIp, rateLimit, nowIso } from "./util.js";

const TERMS_VERSION = db.TERMS_VERSION_DEFAULT;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const cfg = config(env);
    try {
      const advisoryResponse = await advisoryRoute(request, env, cfg, url);
      if (advisoryResponse) return advisoryResponse;
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
    // Subscriber lifecycle and digests are retired.
    if (cfg.free_mode) return;
    // 03:30 Sunday cron = retention; everything else = digest.
    // Matched on the time fields only — the day-of-week is written as SUN
    // (Cloudflare rejects `0`), and an exact-string compare would silently
    // turn every retention run into a second digest.
    const isRetention = /^\s*30\s+3\s/.test(event.cron || "");
    // Monthly free-for-life draw: 08:00 UTC on the 1st. Entry is automatic —
    // anyone who signed up last month on any tier, including free, is in the
    // pool. See db.pickMonthlyDrawWinners for why that matters legally.
    const isMonthlyDraw = /^\s*0\s+8\s+1\s/.test(event.cron || "");
    if (isMonthlyDraw) {
      try {
        const result = await db.pickMonthlyDrawWinners(D, cfg.forever_free_emails, 5);
        for (const w of result.winners) {
          await send(env, cfg, "email", w.email,
            "You've won a free-for-life SecurityCyber CTI subscription",
            `Good news — your address was picked in this month's free-for-life draw.\n\n`
            + `Your account is now on the Team plan, permanently, at no cost. Nothing to do — `
            + `it's already active.\n\nEveryone who signs up (any plan, including the free one) `
            + `is automatically entered into next month's draw too.`);
        }
        if (result.winners.length) {
          await discord.postToChannel(env,
            `🎉 This month's free-for-life draw: ${result.winners.length} subscriber(s) just got `
            + `bumped to the Team plan, permanently, at no cost. Sign up (any plan — free included) `
            + `for a chance to be next month's winner. <${cfg.site_url}>`);
        }
        console.log(`monthly draw: ${result.winners.length}/${result.pool_size} eligible picked for ${result.month}`);
      } catch (e) { console.error("monthly draw", e); }
    }
    if (isRetention) {
      await db.ensureForeverFree(D, cfg.forever_free_emails).catch((e) => console.error("forever-free", e));
      const a = await db.purgeUnconfirmed(D, cfg.retention_days_unconfirmed, cfg.forever_free_emails);
      const b = await db.purgeCancelled(D, cfg.retention_days_cancelled, cfg.forever_free_emails);
      const c = await db.purgeIntents(D, 48);
      await db.trimPostsLog(D, 2000);
      await db.kvSet(D, "last_retention", nowIso());
      // Paying customers stuck in "unconfirmed" are never deleted — flag them.
      const stuck = await db.paidUnconfirmed(D);
      if (stuck.length) {
        await notifyOwner(env, cfg,
          `${stuck.length} paid subscriber(s) never completed confirmation and are getting nothing: `
          + stuck.map((s) => s.email).join(", ")
          + ` — activate them from the panel (/subscribers) or check the address Stripe has.`);
      }
      console.log(`retention: erased ${a} unconfirmed, ${b} cancelled, ${c} intents; ${stuck.length} paid-unconfirmed flagged`);
    } else {
      // The clock for everything Stripe isn't watching — runs before the digest
      // so a just-lapsed free trial doesn't get one more email.
      await runLifecycle(env, cfg).catch((e) => console.error("lifecycle", e));
      const posts = await db.postsSince(D, cfg.digest_window_hours || 24);
      if (posts.length) {
        const subs = (await db.activeForDelivery(D)).filter(canDeliver);
        for (const s of subs) await deliverDigest(env, cfg, s, posts).catch((e) => console.error("digest", s.id, e));
      }
      await db.kvSet(D, "last_digest", nowIso());
      await remindPaidUnconfirmed(env, cfg);
      console.log(`digest: ${posts.length} posts to active subscribers`);
    }
  },
};

// ---------------------------------------------------------------- router
async function route(request, env, ctx, url, path, cfg) {
  const m = request.method;
  const D = env.DB;
  const retired = ["subscribe", "account", "welcome", "brief", "confirm", "manage", "unsubscribe", "channel", "feed", "webhooks"];
  if (retired.includes(path.split("/")[1]) || path.startsWith("/admin/"))
    return json({ error: "retired", message: "CTI is free at / and /api/alerts. No account or payment is required." }, 410);

  if (path === "/logo.svg")
    return new Response(LOGO_SVG, { headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" } });
  if (path === "/healthz") {
    try { await db.stats(D); return text("ok"); }
    catch (e) { console.error("healthz", e); return text("degraded", 503); }
  }
  if (path === "/robots.txt")
    return text("User-agent: *\nAllow: /\nDisallow: /internal/\nDisallow: /admin/\nSitemap: " + cfg.site_url + "/sitemap.xml\n");

  // ---- private RSS feed ----
  // /feed/<token>.xml — the token is the subscriber's rss channel target, so
  // the URL IS the credential (readers can't send auth headers). Access is
  // re-checked against subscription status on every poll: stop paying and the
  // feed stops carrying alerts.
  if (path.startsWith("/feed/") && m === "GET") return rssFeed(request, env, cfg, path);

  // ---- legal ----
  if (["/privacy", "/terms", "/cookies"].includes(path) && m === "GET") {
    const L = legalHtml(cfg, path.slice(1));
    // Free mode: the documents still describe the closed paid service, so say so up top.
    const note = cfg.free_mode
      ? `<div class=card><p><b>The paid service has closed.</b> Since 8 October 2026 the CTI feed is free for everyone, with no sign-up, payment or account. The billing, subscription and one-off brief sections below apply only to purchases made before then. Questions: <a href="mailto:${esc(cfg.contact_email || "securitycyber_cti@securitycyber.uk")}">${esc(cfg.contact_email || "securitycyber_cti@securitycyber.uk")}</a>.</p></div>`
      : "";
    return L ? html(page(cfg, L.title, note + L.html)) : html(page(cfg, "Not found", "<h1>Not found</h1>"), 404);
  }

  if (!cfg_enabled(env) && !path.startsWith("/webhooks") && !path.startsWith("/internal") && !path.startsWith("/admin")) {
    return html(page(cfg, "Not open yet",
      "<h1>Not open yet</h1><p class=lede>The SecurityCyber CTI alert service isn't taking subscribers right now.</p>"));
  }

  // ---- free mode: the feed is open, nothing to buy or sign up for ----
  if (cfg.free_mode) {
    if (path === "/" && m === "GET") {
      // Full alerts (same stream + bar as the Discord channel) — the posts'
      // gap line promises the full write-up is here. Public feed as fallback.
      let posts = [];
      try { posts = await db.listAlertFeed(D, 20, Number(cfg.discord_channel_min_score ?? 50)); }
      catch (e) { console.error("free landing alerts", e); }
      if (!posts.length) {
        try { posts = await db.listPublicFeed(D, 15); } catch (e) { console.error("free landing feed", e); }
      }
      return html(freeLanding(cfg, posts, url.searchParams.get("m") || "", url.searchParams.get("k") || ""));
    }
    if (["/subscribe", "/account", "/welcome", "/brief", "/brief/buy", "/brief/thanks"].includes(path))
      return redirect("/?m=" + q("The CTI feed is free now — no sign-up or payment needed."));
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
    // The free plan has no price in pence — it's gated purely on having a
    // (£0) Payment Link, so it must not be hidden by the "priced" check.
    const isFree = !!t.free;
    const priced = isFree || Number(t.monthly_pence) > 0 || Number(t.weekly_pence) > 0;
    const payable = isFree
      ? !!paymentLink(cfg, tier, "monthly")
      : canCheckout(cfg, env, tier, "monthly") || canCheckout(cfg, env, tier, "weekly");
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
  // Free mode: Stripe is decommissioned (and its old signing secret leaked into
  // public git history), so the endpoint is gone. Answer quietly: verifying and
  // emailing the owner on every POST would let anyone flood the owner's inbox.
  if (path === "/webhooks/stripe" && cfg.free_mode) return json({ error: "gone" }, 410);
  if (path === "/webhooks/stripe" && m === "POST") return stripeWebhook(request, env, cfg);
  if (path === "/webhooks/telegram" && m === "POST") return telegramWebhook(request, env, cfg);

  // ---- confirm ----
  if (path === "/confirm" && m === "GET") return confirm(request, env, cfg, url);

  // ---- manage ----
  if (path === "/manage" && m === "GET") return managePageRoute(request, env, cfg, url);
  if (path === "/manage/resume" && m === "POST") return manageResume(request, env, cfg);
  if (path === "/manage/prefs" && m === "POST") return managePrefs(request, env, cfg);
  if (path === "/manage/channel/add" && m === "POST") return manageChannelAdd(request, env, cfg);
  if (path === "/manage/channel/remove" && m === "POST") return manageChannelRemove(request, env, cfg);
  if (path === "/manage/test" && m === "POST") return manageTest(request, env, cfg, ctx);
  if (path === "/manage/portal" && m === "POST") return managePortal(request, env, cfg);
  if (path === "/manage/export" && m === "GET") return manageExport(request, env, cfg, url);
  if (path === "/manage/delete" && m === "POST") return manageDelete(request, env, cfg);
  if (path === "/unsubscribe" && m === "GET") return unsubscribe(request, env, cfg, url);
  if (path === "/channel/telegram" && m === "GET") return telegramLink(request, env, cfg, url);
  if (path === "/channel/discord" && m === "GET") return discordConnect(request, env, cfg, url);
  if (path === "/channel/discord/callback" && m === "GET") return discordCallback(request, env, cfg, url);

  // ---- pipeline -> worker ----
  if (path === "/internal/ingest" && m === "POST") return internalIngest(request, env, cfg, ctx);

  // Records a post at the moment it actually goes live on LinkedIn — used by
  // both the automated pipeline and manual one-off posts, so it catches
  // everything the decision.json/posts_log path can miss (held/skipped
  // drafts, or a manually-fired post that never went through a full run).
  if (path === "/internal/stix" && m === "POST") {
    const auth = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (!env.INGEST_SECRET || !timingSafeEqual(auth, env.INGEST_SECRET)) return json({ error: "unauthorized" }, 401);
    let body; try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
    const n = await taxii.ingest(D, body);
    return json({ ok: true, ingested: n });
  }
  if (path === "/taxii2" || path.startsWith("/taxii2/")) {
    const ct = {"Content-Type":"application/taxii+json;version=2.1"};
    if (m !== "GET") return json({title:"Read-only TAXII collection"},405,{...ct,Allow:"GET"});
    const accept=request.headers.get("accept") || "*/*";
    if (!accept.includes("*/*") && !accept.includes("application/taxii+json")) return json({title:"TAXII 2.1 media type required"},406,ct);
    return taxii.handle(D, cfg, url, path);
  }

  if (path === "/internal/public-feed" && m === "POST") {
    const auth = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (!env.INGEST_SECRET || !timingSafeEqual(auth, env.INGEST_SECRET)) return json({ error: "unauthorized" }, 401);
    let body; try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
    if (!body.title || !body.text) return json({ error: "title and text required" }, 400);
    await db.logPublicFeed(D, body);
    return json({ ok: true });
  }

  // Public, no auth — this is what securitycyber.uk's own feed page reads.
  if (path === "/api/public-feed" && m === "GET") {
    const limit = Math.min(Number(url.searchParams.get("limit")) || 20, 50);
    const rows = await db.listPublicFeed(D, limit);
    return json({ posts: rows }, 200, {
      "Access-Control-Allow-Origin": "https://securitycyber.uk",
      "Cache-Control": "public, max-age=120",
    });
  }

  // Free mode only: the full alert stream, mirroring the Discord alerts
  // channel (same score bar). Off in paid mode — it carries the write-ups.
  if (path === "/api/alerts" && m === "GET" && cfg.free_mode) {
    const limit = Math.min(Number(url.searchParams.get("limit")) || 50, 100);
    const bar = Number(cfg.discord_channel_min_score ?? 50);
    const rows = await db.listAlertFeed(D, limit, bar);
    const alerts = rows.map((r) => {
      let cves = []; try { cves = JSON.parse(r.cve_ids || "[]"); } catch { /* */ }
      return { title: r.title, url: r.url, source: r.source, score: r.score, cve_ids: cves,
        action: r.action || "", category: r.category || "", text: r.text || "", seen_at: r.seen_at };
    });
    return json({ alerts, min_score: bar }, 200, {
      "Access-Control-Allow-Origin": "https://securitycyber.uk",
      "Cache-Control": "public, max-age=120",
    });
  }

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
// Push delivery (instant + digest) follows the same rule as the feed: an
// active subscriber, or one in the card-retry grace window. `paused` (the
// subscriber pressed "pause all alerts") and everything else get nothing.
const DELIVER_STATUSES = ["active", "past_due"];
const canDeliver = (s) => DELIVER_STATUSES.includes(s.status);

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

  // Reuse wants() rather than re-deriving the thresholds here. A second copy
  // of this logic silently kept the feed on the *instant* bar, so everything
  // between the tier floor and that bar — the extra a subscriber is paying
  // for — never appeared in their reader. "feed" mode = the tier floor (or
  // their override) + vendor filter, and NOT the instant/digest on-off
  // toggles: the feed is pull, so polling it is the opt-in.
  const tier = cfg.tiers[sub.tier] || {};
  sub.prefs = await db.getPrefs(env.DB, sub.id);
  const vendors = (sub.prefs.vendors || []).filter(Boolean);
  const posts = (await db.postsForFeed(env.DB, 300))
    .filter((p) => wants(p, sub, tier, "feed"))
    .slice(0, 120);
  const floor = sub.prefs.min_score_override == null
    ? Number(tier.min_score ?? 0)
    : Math.max(Number(tier.min_score ?? 0), Number(sub.prefs.min_score_override));

  const note = `Alerts scoring ${floor}+ for ${sub.email}`
    + (vendors.length ? `, filtered to ${vendors.join(", ")}` : "")
    + (FEED_GRACE.includes(sub.status) ? " — payment needs attention, see /account" : "");
  return xml(feedXml(cfg, posts, { selfUrl, title: `${cfg.brand} CTI Alerts`, note }));
}

// ---------------------------------------------------------------- subscribe
async function sendConfirmEmail(env, cfg, res) {
  const t = await sign(env, { sub: res.subscriber_id, act: "confirm" }, 14 * 86400);
  const where = res.channel ? chanLabel(res.channel.kind) : "email";
  await send(env, cfg, "email", res.email, "Confirm your SecurityCyber CTI alerts",
    `Thanks for subscribing — ${cfg.tiers[res.tier]?.free ? "the free feed" : `${res.tier} plan, billed ${res.cadence}`}.\n\n` +
    `Confirm your email to switch your alerts on:\n\n${cfg.site_url}/confirm?t=${t}\n\n` +
    `Your alerts will go to: ${where}.\nThis link works for 14 days. If you didn't subscribe, ignore this and email ${cfg.contact_email}.`);
  await db.logEvent(env.DB, "confirm_email_sent", res.subscriber_id, { cadence: res.cadence });
}

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
    return redirect(`/subscribe?tier=${tier}&m=` + q(`${chanLabel(platform)} isn't available — ${avail[platform].why}. Pick another delivery method.`) + "&k=bad");
  let target = "";
  if (platform === "email") target = email;
  // rss: we mint the feed id ourselves — it becomes the secret in the URL.
  else if (platform === "rss") target = newId(24);
  // telegram + discord are connected AFTER payment (bot deep-link / OAuth) —
  // there's nothing for the subscriber to type here, so don't demand a target.
  else if (platform !== "telegram" && platform !== "discord") {
    const [ok, why] = verifyTarget(platform, f.target || "");
    if (!ok) return redirect(`/subscribe?tier=${tier}&m=` + q(why) + "&k=bad");
    target = (f.target || "").trim();
  }
  const intentTok = newId();
  await db.createIntent(env.DB, {
    token: intentTok, email, tier, cadence,
    // Both keep their kind (even with no target yet) so the confirm mail and
    // prefs page know to offer the one-click connect for whichever it is.
    channel_kind: platform,
    channel_target: target || null, ip: clientIp(request), user_agent: request.headers.get("user-agent") || "",
  });
  await db.logConsent(env.DB, null, "terms", true, {
    ip: clientIp(request), ua: request.headers.get("user-agent") || "",
    terms_version: cfg.terms_version, detail: `accepted at signup (${email})`,
  });
  // A genuinely free tier has nothing to pay — routing it through Stripe
  // anyway means inheriting its checkout, tax and address-collection
  // machinery for no reason, which is real signup friction for zero benefit.
  // Activate straight from the intent we already created.
  if (tcfg.free) {
    const intent = await db.getIntent(env.DB, intentTok);
    const res = await billing.activateFromIntent(env.DB, {
      email, tier, cadence, intent, source: "free",
      eventTag: "free:signup",
    });
    if (res.action === "send_confirm") await sendConfirmEmail(env, cfg, res);
    return redirect(`${cfg.site_url}/welcome`);
  }
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
    else await notifyOwner(env, cfg,
      "Stripe webhook signature rejected — the STRIPE_WEBHOOK_SECRET on this Worker does not match the "
      + "signing secret of the endpoint Stripe is calling. Copy it from that endpoint's page and run: "
      + "npx wrangler secret put STRIPE_WEBHOOK_SECRET");
    return json({ error: "invalid signature" }, 400);
  }

  // Stripe's newer "event destinations" can be created with a THIN payload,
  // which carries only an event id — no data.object — so nothing here could
  // ever build a subscriber from one. Acknowledge it so Stripe stops retrying,
  // but say loudly what's wrong: the alternative is a destination that looks
  // healthy in the dashboard while provisioning silently never happens.
  const etype = event.type || "";
  if (etype.startsWith("v2.") || event.object === "v2.core.event" || !event.data?.object) {
    const msg = `Stripe endpoint is misconfigured: received "${etype}". This Worker needs the classic `
      + `snapshot payload with v1 events — checkout.session.completed, customer.subscription.updated, `
      + `customer.subscription.deleted, invoice.payment_failed, invoice.paid, invoice.payment_succeeded. `
      + `A "thin" v2 destination carries no object data, so no subscriber can ever be created from it.`;
    console.warn(msg);
    if (!etype.endsWith(".ping")) await notifyOwner(env, cfg, msg);
    return json({ received: true, ignored: etype, hint: "use a snapshot payload with v1 events" });
  }

  let res;
  try { res = await billing.handleEvent(env, cfg, event); }
  catch (e) { console.error("handleEvent", e); await notifyOwner(env, cfg, `webhook handler error: ${e}`); return json({ received: true, handler_error: String(e).slice(0, 200) }); }

  if (res.action === "send_confirm") {
    await sendConfirmEmail(env, cfg, res);
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
    // Tell the subscriber too — a failed charge is recoverable if they update
    // the card before Stripe gives up.
    if (res.subscriber_id) {
      const s = await db.get(env.DB, res.subscriber_id);
      if (s && s.email) {
        const mtok = await sign(env, { sub: s.id, act: "manage" }, 30 * 86400).catch(() => "");
        await send(env, cfg, "email", s.email, "Payment problem on your SecurityCyber CTI subscription",
          `Your last payment didn't go through, so your subscription is on hold while Stripe retries.\n\n`
          + `Update your card to avoid interruption:\n${cfg.site_url}/manage?t=${mtok}\n\n`
          + `Your alerts keep running for now. If the retries also fail your subscription is cancelled — `
          + `no charge, and you can restart any time.\n\n— ${cfg.brand}`).catch((e) => console.error("dunning email", e));
      }
    }
  } else if (["cancelled", "recovered"].includes(res.action)) {
    await notifyOwner(env, cfg, `subscriber ${res.action}` + (res.detail ? ` — ${res.detail}` : ""));
    // Discord access follows payment, exactly as the RSS feed does: lose the
    // subscriber role on cancellation, get it back on recovery.
    if (res.subscriber_id) {
      const s = await db.get(env.DB, res.subscriber_id);
      if (s) await discordAccess(env, cfg, s, res.action === "recovered");
      if (s && s.email && res.action === "cancelled") {
        const payFail = /failed payment/i.test(res.detail || "");
        await send(env, cfg, "email", s.email,
          payFail ? "Your SecurityCyber CTI subscription has ended" : "Your SecurityCyber CTI subscription is cancelled",
          (payFail
            ? `We couldn't take payment after several tries, so your subscription has ended and your alerts have stopped. `
              + `Nothing more will be charged.\n\n`
            : `Your subscription is cancelled and your alerts have stopped. Nothing more will be charged.\n\n`)
          + `Start again any time — your preferences and feed URL come back with you:\n${cfg.site_url}/subscribe\n\n`
          + `— ${cfg.brand}`).catch((e) => console.error("cancel email", e));
      }
    }
  }
  return json({ received: true, action: res.action });
}

// ---------------------------------------------------------------- confirm
// Statuses where the double opt-in hasn't happened yet.
const NEEDS_CONFIRM = ["unconfirmed", "pending_payment"];

// Switch a paid-but-unconfirmed subscriber on. Callers MUST only invoke this
// for a subscriber currently in NEEDS_CONFIRM. Following any link we emailed
// them — the /confirm link OR a /manage link — proves control of the address,
// which is all double opt-in requires; both entry points land here so the
// side effects (consent log, channel pings, feed URL email, owner ping)
// happen exactly once.
async function activateSubscriber(env, cfg, request, sub, detail) {
  let note = "";
  await db.setStatus(env.DB, sub.id, "active", detail, clientIp(request));
  await db.logConsent(env.DB, sub.id, "signup", true, {
    ip: clientIp(request), ua: request.headers.get("user-agent") || "",
    terms_version: cfg.terms_version, detail,
  });
  for (const ch of sub.channels) {
    if (ch.verified) continue;
    // The email channel: verify it here only if email is their CHOSEN delivery
    // method (label 'signup'). Clicking the confirm link that was sent to this
    // address is the proof of control — there's nothing else to test-ping.
    // An 'account'-labelled email row stays unverified: it's just the login id
    // for someone who picked RSS/Discord/Telegram, and alerts must not double
    // up on a channel they didn't ask for.
    if (ch.kind === "email") { if (ch.label === "signup") await db.verifyChannel(env.DB, ch.id); continue; }
    // rss is pull, not push — there's nothing to test-ping. Mark it good and
    // email the URL below instead.
    if (ch.kind === "rss") { await db.verifyChannel(env.DB, ch.id); continue; }
    // discord / telegram picked at signup but not yet connected (no target):
    // there's nothing to ping — email them the one-click connect link instead.
    if ((ch.kind === "discord" || ch.kind === "telegram") && !ch.target) {
      await emailConnectLink(env, cfg, await db.get(env.DB, sub.id), ch.kind).catch((e) => console.error("connect link", e));
      continue;
    }
    const r = await send(env, cfg, ch.kind, ch.target, "SecurityCyber CTI — you're connected",
      "This channel is now linked. Real alerts will arrive here.", { priority: "default" });
    if (r.ok) await db.verifyChannel(env.DB, ch.id);
    else note = ` We couldn't reach your ${chanLabel(ch.kind)} — check it on the next page.`;
  }
  // Free-promo tier: start the trial clock now (at activation, not signup, so a
  // slow email confirm doesn't eat trial days). Stripe-billed subscribers never
  // carry a Worker term — Stripe is their clock.
  const pd = promoDays(cfg, sub.tier);
  if (pd > 0 && !sub.stripe_subscription_id) {
    await db.setTermEnd(env.DB, sub.id, daysFromNow(pd));
    await db.logEvent(env.DB, "promo:started", sub.id, { days: pd, ends: daysFromNow(pd) });
  }
  // A feed URL is useless until the subscriber actually has it, and it's the
  // one channel we can't push to — so mail it out the moment they're live.
  await emailFeedUrl(env, cfg, await db.get(env.DB, sub.id));
  await notifyOwner(env, cfg, `new subscriber confirmed (${sub.tier}, ${sub.cadence})${pd > 0 ? ` — free ${pd}d trial` : ""}`);
  return { note };
}

async function confirm(request, env, cfg, url) {
  const over = rl(request, "confirm", cfg, 15); if (over) return over;
  let data;
  try { data = await verify(env, url.searchParams.get("t") || "", "confirm"); }
  catch (e) { return html(simple(cfg, "Link problem", "That link didn't work", `${e}. Use “Already subscribed?” on the home page for a fresh one.`)); }
  const sub = await db.get(env.DB, data.sub);
  if (!sub) return html(simple(cfg, "Not found", "Account not found", "It may have been removed."));
  const mtok = await sign(env, { sub: sub.id, act: "manage" }, 90 * 86400);
  if (sub.status === "cancelled")
    return redirect(`/manage?t=${mtok}&m=` + q("This subscription was cancelled — start a new one from the home page.") + "&k=bad");
  // Already confirmed (link clicked twice, forwarded, prefetched): don't re-run
  // the side effects — just take them to their preferences.
  if (!NEEDS_CONFIRM.includes(sub.status))
    return redirect(`/manage?t=${mtok}&m=` + q("You're already confirmed — alerts are live.") + "&k=good");
  const { note } = await activateSubscriber(env, cfg, request, sub, "double opt-in via /confirm");
  return redirect(`/manage?t=${mtok}&m=` + q("You're in — alerts are live." + note) + (note ? "&k=bad" : "&k=good"));
}

// Digest cron calls this once a day: a paying subscriber who still hasn't
// confirmed gets the confirm email again, up to 3 times (kv-tracked), so a
// spam-foldered first attempt isn't the end of it. purgeUnconfirmed never
// touches them, and the retention run also pages the owner.
async function remindPaidUnconfirmed(env, cfg) {
  const stuck = await db.paidUnconfirmed(env.DB).catch((e) => (console.error("paidUnconfirmed", e), []));
  for (const row of stuck) {
    const key = `confirm_resend:${row.id}`;
    const n = Number((await db.kvGet(env.DB, key)) || 0);
    if (n >= 3) continue;
    const t = await sign(env, { sub: row.id, act: "confirm" }, 14 * 86400).catch(() => "");
    if (!t) continue;
    const r = await send(env, cfg, "email", row.email, "Confirm your SecurityCyber CTI alerts (reminder)",
      `You subscribed to SecurityCyber CTI (${row.tier} plan) but haven't confirmed your email yet, `
      + `so your alerts aren't switched on.\n\nConfirm now — one click:\n\n${cfg.site_url}/confirm?t=${t}\n\n`
      + `You are being billed for this subscription. If you didn't mean to subscribe, reply to this email `
      + `and we'll cancel and refund it. Questions: ${cfg.contact_email}`);
    await db.kvSet(env.DB, key, String(n + 1));
    await db.logEvent(env.DB, r.ok ? "confirm_reminder_sent" : "confirm_reminder_failed", row.id, { attempt: n + 1 });
  }
}

// How long a tier's free window runs, in days. Anything > 0 means the tier is a
// timed promo: the clock starts at activation and the daily lifecycle cron
// lapses the account when term_ends_at passes.
function promoDays(cfg, tier) {
  return Number((cfg.tiers[tier] || {}).promo_days || 0);
}
const daysFromNow = (d) => new Date(Date.now() + d * 86400000).toISOString();

// ---------------------------------------------------------------- lifecycle cron
// Stripe is the clock for card subscribers — it knows weekly vs monthly, takes
// the payment and fires webhooks. This is the clock for everyone Stripe isn't
// watching, plus a safety net for webhooks that never arrived. Runs once a day
// on the digest cron (before the digest).
//   1. Free-promo / timed-comp accounts whose term_ends_at has passed -> lapse
//      to 'cancelled' (alerts + feed stop; the feed shows the upgrade prompt)
//      and email them a way to keep it.
//   2. Stripe-billed accounts whose period end is well overdue -> pull the live
//      subscription and reconcile status + period end, in case a renew/cancel
//      webhook was missed. This is the self-healing "did the recharge actually
//      happen?" check.
async function runLifecycle(env, cfg) {
  const D = env.DB;
  let lapsed = 0, reconciled = 0;

  for (const s of await db.expiredTerms(D, cfg.forever_free_emails).catch(() => [])) {
    await db.setStatus(D, s.id, "cancelled", `${s.source || "promo"} term expired`);
    await db.setTermEnd(D, s.id, null);
    await db.logEvent(D, "lifecycle:term_expired", s.id, { source: s.source, was: s.status });
    lapsed++;
    const hydrated = await db.get(D, s.id);
    await discordAccess(env, cfg, hydrated, false).catch(() => {});
    const t = await sign(env, { sub: s.id, act: "manage" }, 30 * 86400).catch(() => "");
    const back = t ? `${cfg.site_url}/manage?t=${t}` : `${cfg.site_url}/`;
    await send(env, cfg, "email", s.email, `Your ${cfg.brand} CTI free week has ended`,
      `Your free ${promoDays(cfg, s.tier) || 7}-day run of ${cfg.brand} CTI alerts is up, so alerts and your private feed have stopped.\n\n`
      + `Keep them going — Individual is £8/month or £2/week:\n\n${cfg.site_url}/subscribe?tier=individual\n\n`
      + `Your preferences and feed URL are still here if you come back:\n${back}\n\n`
      + `Nothing happens automatically — you have not been charged.`,
      { priority: "default" }).catch(() => {});
    await notifyOwner(env, cfg, `free-trial ended: ${s.email} (added ${String(s.created_at).slice(0, 10)}) — not converted`);
  }

  if (env.STRIPE_SECRET_KEY) {
    for (const s of await db.staleStripe(D, 2, 50).catch(() => [])) {
      let so;
      try { so = await billing.getSubscription(env, s.stripe_subscription_id); }
      catch (e) { console.error("lifecycle reconcile", s.id, e); continue; }
      const st = so.status;
      if (so.current_period_end) await db.setStripeIds(D, s.id, { period_end: so.current_period_end });
      if (["canceled", "unpaid", "incomplete_expired"].includes(st) && s.status !== "cancelled") {
        await db.setStatus(D, s.id, "cancelled", `lifecycle: stripe reports ${st}`);
        await discordAccess(env, cfg, await db.get(D, s.id), false).catch(() => {});
        await notifyOwner(env, cfg, `reconcile: ${s.email} cancelled (stripe: ${st}, webhook was missed)`);
        reconciled++;
      } else if (st === "past_due" && s.status === "active") {
        await db.setStatus(D, s.id, "past_due", "lifecycle: stripe reports past_due");
        reconciled++;
      } else if (["active", "trialing"].includes(st) && s.status === "past_due") {
        await db.setStatus(D, s.id, "active", "lifecycle: stripe recovered");
        reconciled++;
      }
    }
  }

  if (lapsed || reconciled) await db.logEvent(D, "lifecycle:run", null, { lapsed, reconciled });
  console.log(`lifecycle: ${lapsed} term(s) lapsed, ${reconciled} stripe sub(s) reconciled`);
}

// ---------------------------------------------------------------- Discord
// One click, no username typing: Discord has no way for a bot to look up a
// user by name or send an add request, so we use OAuth2 `guilds.join`. They
// approve once, the bot adds them to the server, and alerts then arrive as
// bot DMs — which keeps each subscriber's own score threshold and vendor
// filter working (a shared channel could not).
const discordRedirect = (cfg) => `${cfg.site_url}/channel/discord/callback`;

async function discordConnect(request, env, cfg, url) {
  const over = rl(request, "discord", cfg, 20); if (over) return over;
  const tok = url.searchParams.get("t") || "";
  const [sub] = await manageSub(env, url);
  if (!sub) return html(simple(cfg, "Link problem", "That link didn't work",
    "Use “Already subscribed?” on the home page for a fresh one."));
  if (!discord.discordConfigured(env))
    return html(simple(cfg, "Not available", "Discord isn't connected yet",
      "Our Discord isn't set up for subscribers yet — pick another delivery method for now."));

  // The OAuth `state` must be unguessable and must survive the round trip, so
  // it is a signed, short-lived token naming this subscriber.
  const state = await sign(env, { sub: sub.id, act: "discord" }, 900);
  await db.kvSet(env.DB, `discord_state:${sub.id}`, tok);   // to return them to /manage
  return redirect(discord.authorizeUrl(env, discordRedirect(cfg), state));
}

async function discordCallback(request, env, cfg, url) {
  const over = rl(request, "discord", cfg, 20); if (over) return over;
  const code = url.searchParams.get("code") || "";
  const state = url.searchParams.get("state") || "";
  if (url.searchParams.get("error"))
    return html(simple(cfg, "Not connected", "Discord wasn't connected",
      "You cancelled at Discord, so nothing changed. You can try again from your preferences."));

  let data;
  try { data = await verify(env, state, "discord"); }
  catch { return html(simple(cfg, "Link problem", "That link expired", "Start again from your preferences.")); }
  const sub = await db.get(env.DB, data.sub);
  if (!sub) return html(simple(cfg, "Not found", "Account not found", "It may have been removed."));
  const back = (await db.kvGet(env.DB, `discord_state:${sub.id}`)) || "";

  try {
    const accessToken = await discord.exchangeCode(env, code, discordRedirect(cfg));
    const me = await discord.currentUser(env, accessToken);
    await discord.addToGuild(env, me.id, accessToken);
    // Store the user id as the channel target — DMs go to it, and it's how we
    // revoke access later if they stop paying. Drop the empty-target placeholder
    // row left by the signup so there's exactly one discord channel.
    const placeholder = (sub.channels || []).find((c) => c.kind === "discord" && !c.target);
    if (placeholder) await db.removeChannel(env.DB, placeholder.id);
    await db.addChannel(env.DB, sub.id, "discord", me.id, true, `discord:${me.username}`);
    await db.logEvent(env.DB, "channel:discord_joined", sub.id, { user: me.username });
    const r = await send(env, cfg, "discord", me.id, `${cfg.brand} CTI — you're connected`,
      "You're in. Alerts that clear your threshold will arrive here as a DM.", {});
    const note = r.ok ? "" : " (we couldn't DM you yet — check that you allow DMs from server members)";
    return redirect(`/manage?t=${encodeURIComponent(back)}&m=`
      + q(`Discord connected as ${me.global_name}. You've been added to our server${note}.`)
      + (r.ok ? "&k=good" : "&k=bad"));
  } catch (e) {
    console.error("discord connect", e);
    await notifyOwner(env, cfg, `Discord connect failed for subscriber ${sub.id}: ${e}`);
    return html(simple(cfg, "Couldn't connect", "Discord didn't connect",
      "Something went wrong adding you to our server. Nothing was charged and nothing changed — "
      + "please try again from your preferences, or pick another delivery method."));
  }
}

// Access follows payment, like the RSS feed: drop the subscriber role when a
// subscription stops, restore it when it resumes. Never throws into the
// caller — losing Discord access must not break status handling.
async function discordAccess(env, cfg, sub, grant) {
  if (!discord.discordConfigured(env)) return;
  const ch = (sub.channels || []).find((c) => c.kind === "discord" && c.target);
  if (!ch) return;
  try {
    if (grant) await discord.grantSubscriberRole(env, ch.target);
    else if (env.DISCORD_REMOVE_ON_LAPSE === "1") await discord.removeFromGuild(env, ch.target);
    else await discord.revokeSubscriberRole(env, ch.target);
    await db.logEvent(env.DB, grant ? "discord:access_granted" : "discord:access_revoked", sub.id, {});
  } catch (e) { console.error("discord access", e); }
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

// Discord / Telegram are connected after checkout — the subscriber has to do a
// one-time OAuth (Discord) or open a bot deep-link (Telegram). Nothing pushes
// them there on its own, so on confirmation we email a single link that starts
// it. Without this a Discord/Telegram signup confirms and then just sits there.
async function emailConnectLink(env, cfg, sub, kind) {
  const ch = (sub.channels || []).find((c) => c.kind === kind);
  if (!ch || ch.target) return null;   // already connected
  const t = await sign(env, { sub: sub.id, act: "manage" }, 30 * 86400).catch(() => "");
  if (!t) return null;
  const link = `${cfg.site_url}/channel/${kind}?t=${encodeURIComponent(t)}`;
  const label = kind === "discord" ? "Discord" : "Telegram";
  const how = kind === "discord"
    ? "It opens Discord, asks you to authorise once, then adds you to our subscribers server and switches on alerts."
    : "It opens Telegram and starts a chat with our bot — send /start and alerts begin.";
  const r = await send(env, cfg, "email", sub.email, `Connect your ${label} — one click`,
    `Your ${cfg.brand} CTI subscription is live. One thing left: connect ${label} so alerts can reach you.\n\n`
    + `${link}\n\n${how}\n\n`
    + `The link is tied to your account and works for 30 days. `
    + `You can also do this any time from your preferences page.\n\n— ${cfg.brand}`,
    {});
  await db.logEvent(env.DB, r.ok ? `${kind}:connect_link_emailed` : `${kind}:connect_link_failed`, sub.id, { error: r.error || "" });
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
  let [sub, tok] = await manageSub(env, url);
  if (!sub) return html(simple(cfg, "Link problem", "Link expired", "Use “Already subscribed?” on the home page for a fresh link."));
  let extra = "";
  if (NEEDS_CONFIRM.includes(sub.status)) {
    // Reaching this page means they opened a link sent to their address — that
    // is the double opt-in. Complete it here so a lost /confirm email can't
    // strand a paying customer.
    const { note } = await activateSubscriber(env, cfg, request, sub, "confirmed by opening emailed preferences link");
    sub = await db.get(env.DB, sub.id);
    extra = " Email confirmed — alerts are live." + note;
  }
  return html(managePage(cfg, sub, tok, (url.searchParams.get("m") || "") + extra, url.searchParams.get("k") || ""));
}

async function manageResume(request, env, cfg) {
  const over = rl(request, "manage", cfg, 60); if (over) return over;
  const f = await form(request);
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub) return redirect("/?m=" + q("Link expired"));
  if (sub.status === "paused") {
    await db.setStatus(env.DB, sub.id, "active", "resumed via preferences", clientIp(request));
    await db.logConsent(env.DB, sub.id, "signup", true, { ip: clientIp(request), detail: "resumed alerts via /manage" });
    const s = await db.get(env.DB, sub.id);
    if (s) await discordAccess(env, cfg, s, true);
    return redirect(`/manage?t=${tok}&m=` + q("Alerts resumed — you're live again.") + "&k=good");
  }
  if (NEEDS_CONFIRM.includes(sub.status))
    return redirect(`/manage?t=${tok}`);   // managePageRoute will confirm them
  const msg = sub.status === "cancelled"
    ? "This subscription was cancelled — start a new one from the home page."
    : `Nothing to resume — alerts are already ${sub.status}.`;
  return redirect(`/manage?t=${tok}&m=` + q(msg) + (sub.status === "active" ? "&k=good" : "&k=bad"));
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
  const [sub, tok] = await manageSubFromForm(env, f.t);
  if (!sub) return html(simple(cfg, "Link problem", "Link expired", ""));
  if (sub.stripe_subscription_id) {
    try {
      await billing.cancelSubscription(env, sub.stripe_subscription_id);
    } catch (e) {
      console.error("cancel on delete", e);
      await notifyOwner(env, cfg, `delete blocked: could not cancel Stripe subscription for subscriber ${sub.id}: ${e}`);
      return html(simple(cfg, "Delete blocked", "Delete blocked",
        "We couldn't cancel the active Stripe subscription, so we kept your account record intact to avoid billing or data loss.",
        `<p><a class="btn" href="/manage?t=${tok}">Back to preferences</a></p>`));
    }
  }
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

  // Bot added to / promoted in a channel — capture the chat id so broadcast can
  // be switched on without hunting for the numeric id.
  const cm = upd.my_chat_member;
  if (cm && cm.chat && (cm.chat.type === "channel" || cm.chat.type === "supergroup")) {
    const st = cm.new_chat_member && cm.new_chat_member.status;
    if (st === "administrator" || st === "creator") {
      await db.kvSet(env.DB, "tg_channel_id", String(cm.chat.id)).catch(() => {});
      await db.kvSet(env.DB, "tg_channel_title", cm.chat.title || "").catch(() => {});
      await notifyOwner(env, cfg,
        `Telegram: the bot is now an admin of "${cm.chat.title}" (chat id ${cm.chat.id}). `
        + `Broadcast can be switched on.`).catch(() => {});
    } else if (["left", "kicked", "member", "restricted"].includes(st)) {
      await notifyOwner(env, cfg,
        `Telegram: the bot lost posting rights on "${cm.chat.title}" (status ${st}).`).catch(() => {});
    }
    return json({ ok: true });
  }

  const msg = upd.message || upd.edited_message || {};
  const t = (msg.text || "").trim();
  const chatId = String((msg.chat || {}).id || "");
  if (t.startsWith("/start") && chatId) {
    const payload = t.split(/\s+/, 2)[1] || "";
    try {
      const data = await verify(env, payload, "tg_link");
      const sub = await db.get(env.DB, data.sub, false);
      if (sub) {
        // Drop the empty-target placeholder row left by the signup so there's
        // exactly one telegram channel, same as the Discord OAuth callback.
        const placeholder = (sub.channels || []).find((c) => c.kind === "telegram" && !c.target);
        if (placeholder) await db.removeChannel(env.DB, placeholder.id);
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
  // Accept an alert that carries EITHER the paid write-up or the social text.
  // Filtering on post_text alone silently dropped every subscriber-only alert.
  const posts = (body.posts || []).filter((p) =>
    (p.subscriber_text || "").trim() || (p.post_text || "").trim());

  for (const p of posts) {
    p.post_key = normKey(p);
    await db.logPost(env.DB, p);
  }
  // instant fan-out (in the background so the pipeline call returns fast)
  ctx.waitUntil((async () => {
    const subs = cfg.free_mode ? [] : (await db.activeForDelivery(env.DB)).filter(canDeliver);
    for (const s of subs) await deliverInstant(env, cfg, s, posts, "instant").catch((e) => console.error("instant", s.id, e));
    // Shared subscriber channel on Discord. Everyone in the channel sees the
    // same feed, so it uses one fixed threshold, not anyone's personal filter.
    // Per-subscriber filtering is the DM / RSS / email path above.
    if (env.DISCORD_ALERT_WEBHOOK) {
      const bar = Number(cfg.discord_channel_min_score ?? 50);
      for (const p of posts) {
        if (Number(p.score || 0) < bar) continue;
        if (await db.kvGet(env.DB, "dch:" + p.post_key)) continue;   // once per story, across runs
        const r = await discord.postToChannel(env, discordChannelPost(cfg, p));
        if (r.ok) await db.kvSet(env.DB, "dch:" + p.post_key, nowIso());
        else console.error("discord channel post", r.error);
      }
    }
  })());
  return json({ received: posts.length });
}

// The shared-channel version of an alert: the action line, the depth, the CVEs,
// the source. Same substance as a DM, formatted for a public channel.
function discordChannelPost(cfg, p) {
  const cves = (p.cve_ids || []).join(", ");
  const parts = [
    `**${p.title || "Threat alert"}**${p.score != null ? `  ·  impact ${p.score}/100` : ""}`,
    (p.action || "").trim() ? `**Do now:** ${p.action.trim()}` : "",
    (p.subscriber_text || p.post_text || "").trim(),
    cves ? `CVE: ${cves}` : "",
    p.url ? `<${p.url}>` : "",
    `*${MOTTO_SHORT}*`,
    "https://securitycyber.uk/cti",
  ].filter(Boolean);
  return parts.join("\n\n");
}

function normKey(p) {
  const u = (p.url || "").split("?")[0].replace(/\/$/, "").toLowerCase();
  return u || "t:" + (p.title || p.post_text || "").slice(0, 60);
}

// ---------------------------------------------------------------- delivery
function vendorMatch(post, vendors) {
  if (!vendors || !vendors.length) return true;
  // Vendor names and product versions live in subscriber_text, so a filter
  // that only read post_text would miss the very stories it was set up for.
  const hay = ((post.title || "") + " " + (post.subscriber_text || "") + " "
    + (post.post_text || "") + " " + (post.cve_ids || []).join(" ")).toLowerCase();
  return vendors.some((v) => hay.includes(v.toLowerCase()));
}
// Two bars, deliberately distinct:
//   tierCfg.min_score          — below this a story never reaches them at all.
//                                Set BELOW the public posting threshold, so a
//                                subscriber always sees strictly more than the
//                                free social feed, never less.
//   tierCfg.instant_min_score  — at or above this it interrupts them now;
//                                between the two it still arrives in the daily
//                                digest and their private feed.
// A subscriber's own min_score_override replaces the bar in both modes, but
// can never take them below the tier floor.
function wants(post, sub, tierCfg, mode) {
  const p = sub.prefs;
  if (mode === "instant" && !p.instant) return false;
  if (mode === "digest" && !p.digest) return false;
  // mode "feed": no on/off toggle — the private RSS feed always carries
  // everything from the floor up; the reader polling it is the opt-in.
  const score = Number(post.score || 0);
  const floor = Number(tierCfg.min_score ?? 0);
  const override = p.min_score_override == null ? null : Number(p.min_score_override);
  const bar = override != null
    ? Math.max(floor, override)
    : (mode === "instant" ? Math.max(floor, Number(tierCfg.instant_min_score ?? 70)) : floor);
  if (score < bar) return false;
  return vendorMatch(post, p.vendors);
}

// What a paying subscriber actually receives. The analyst writes
// `subscriber_text` (versions, exposure check, action, detection) specifically
// for them; `post_text` is the public social hook and is only a fallback for
// older pipeline output. Never send the social copy when depth exists — that
// is the whole reason someone is paying.
function alertBody(post) {
  const deep = (post.subscriber_text || "").trim();
  const act = (post.action || "").trim();
  if (deep) {
    let body = deep;
    if (act && !deep.toLowerCase().includes(act.toLowerCase().slice(0, 24))) body = `${act}\n\n${deep}`;
    if (post.url && !body.includes(post.url)) body += `\n\nSource: ${post.url}`;
    return body;
  }
  return post.post_text || "";
}

// Channels a deliverable subscriber actually receives on: verified + allowed by
// their tier. The email row is special — it's created as the account identifier
// (label 'account') and only becomes a delivery channel when email is the
// chosen platform (label 'signup', verified on confirm). Legacy rows (no label)
// are treated as a delivery channel too, so subscribers created before that
// distinction existed still get their email alerts.
function deliverableChannels(sub, tierCfg) {
  const allowed = new Set(tierCfg.channels || []);
  const chans = sub.channels || [];
  // Legacy safety net: subscribers created before the email row carried a
  // label had verified=0 on it and would now get nothing. Treat their own
  // address as deliverable — but only if there's no other verified channel,
  // so someone who deliberately picked RSS/Discord doesn't also start getting
  // email. New signups don't need this: the email row is verified on confirm.
  const hasOther = chans.some((c) => c.verified && c.kind !== "email" && allowed.has(c.kind));
  return chans.filter((c) => {
    if (!allowed.has(c.kind) || !c.target) return false;
    if (c.verified) return true;
    return c.kind === "email" && c.target === sub.email && c.label == null && !hasOther;
  });
}

async function deliverInstant(env, cfg, sub, posts, mode) {
  const tierCfg = cfg.tiers[sub.tier] || {};
  const verified = deliverableChannels(sub, tierCfg);
  if (!verified.length) return;
  for (const post of posts) {
    if (mode !== "test" && !wants(post, sub, tierCfg, "instant")) continue;
    if (mode !== "test" && await db.alreadyDelivered(env.DB, sub.id, post.post_key)) continue;
    // A subscriber-only alert may have neither a title nor social copy.
    const subject = (post.title || (post.post_text || post.subscriber_text || "").split("\n")[0]
      || "Threat alert").slice(0, 180);
    const jsonp = tierCfg.attach_json ? { posts: [post] } : null;
    const mtok = await sign(env, { sub: sub.id, act: "manage" }, 90 * 86400).catch(() => "");
    const links = mtok ? { manage: `${cfg.site_url}/manage?t=${mtok}`,
      unsubscribe: `${cfg.site_url}/unsubscribe?t=${mtok}` } : {};
    for (const ch of verified) {
      const r = await send(env, cfg, ch.kind, ch.target, subject, alertBody(post), {
        priority: "instant", shortText: post.short_text || "",
        html: ch.kind === "email" ? alertEmailHtml(cfg, post, links) : "",
        listUnsub: ch.kind === "email" ? links.unsubscribe : "",
        jsonPayload: ["email", "webhook"].includes(ch.kind) ? jsonp : null,
      });
      await db.recordDelivery(env.DB, sub.id, post.post_key, ch.kind, mode, r.ok, r.ok ? null : r.error);
    }
  }
}

async function deliverDigest(env, cfg, sub, allPosts) {
  const tierCfg = cfg.tiers[sub.tier] || {};
  const verified = deliverableChannels(sub, tierCfg);
  if (!verified.length) return;
  const ACTIONABLE = new Set(["exploitation", "breach", "campaign", "supply-chain",
                             "fraud-trend", "exec-protection", "malware", "ttp"]);
  const digestFloor = Number(cfg.digest_min_score ?? 30);
  let matched = allPosts
    .filter((p) => wants(p, sub, tierCfg, "digest"))
    .filter((p) => Number(p.score || 0) >= digestFloor || ACTIONABLE.has(p.category || ""))
    .sort((a, b) => b.score - a.score);
  if (!matched.length) return;
  // Per-item, not one-digest-per-day: keep only what this subscriber has never
  // been sent (by instant or an earlier digest). Lets the digest run several
  // times a day and still never repeat an item.
  const seen = [];
  for (const p of matched) {
    if (!(await db.alreadyDelivered(env.DB, sub.id, p.post_key))) seen.push(p);
  }
  if (!seen.length) return;
  matched = seen;
  // The private feed carries everything; the digest is the top slice so the
  // email stays scannable even on a 100-item day.
  const DIGEST_CAP = Number(cfg.digest_max_items ?? 25);
  const shown = matched.slice(0, DIGEST_CAP);
  const overflow = matched.length - shown.length;
  const feedCh = sub.channels.find((c) => c.kind === "rss");
  const feedUrl = feedCh ? `${cfg.site_url}/feed/${feedCh.target}.xml` : `${cfg.site_url}/manage`;
  const subject = `${cfg.brand} CTI — daily digest · ${matched.length} item(s)`;
  const lines = [`${matched.length} item(s) from the last 24 hours, highest impact first.`];
  if (overflow > 0) lines.push(`Showing the top ${shown.length} — the other ${overflow} are in your feed: ${feedUrl}`);
  lines.push("");
  for (const p of shown) {
    // Lead with the action a subscriber is paying for, not the social teaser.
    lines.push(`• [${p.score}] ${p.title || (p.post_text || "").split("\n")[0]}`);
    if ((p.action || "").trim()) lines.push(`  DO NOW: ${p.action.trim()}`);
    if ((p.cve_ids || []).length) lines.push(`  ${(p.cve_ids || []).join(", ")}`);
    if (p.url) lines.push(`  ${p.url}`);
    lines.push("");
  }
  if (overflow > 0) lines.push(`+ ${overflow} more lower-scoring item(s) in your feed: ${feedUrl}`, "");
  lines.push("— Change vendors, channels or unsubscribe from your preference link.");
  const body = lines.join("\n");
  const jsonp = tierCfg.attach_json ? { posts: matched } : null;
  let anyOk = false;
  for (const ch of verified) {
    const r = await send(env, cfg, ch.kind, ch.target, subject, body, {
      priority: "digest", jsonPayload: ["email", "webhook"].includes(ch.kind) ? jsonp : null,
    });
    anyOk = anyOk || r.ok;
  }
  if (anyOk) {
    // mark every item in this digest as delivered so the next digest / an
    // instant for the same story does not re-send it
    for (const p of matched) {
      await db.recordDelivery(env.DB, sub.id, p.post_key, "digest", "digest", true, null).catch(() => {});
    }
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
      ...s, mrr_pence: mrr, problems: problems(cfg, env), advisories: advisories(cfg, env),
      // Presence only — never the values. Lets the owner dashboard show which
      // secrets are actually reaching the running Worker.
      secrets: Object.fromEntries(["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "RESEND_API_KEY",
        "SUBS_TOKEN_SECRET", "INGEST_SECRET", "ADMIN_TOKEN", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME", "TELEGRAM_WEBHOOK_SECRET"].map((k) => [k, !!env[k]])),
      channels: channelAvailability(env, cfg),
      live: !!cfg.live,
      taking_real_money: takingRealMoney(cfg, env),
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
      // Extend / shorten / clear a free-promo or timed-comp window. An ISO
      // string sets it; "" or null clears it (make it permanent); a positive
      // `term_days` is shorthand for "that many days from now".
      if (b.term_days != null && Number(b.term_days) > 0)
        await db.setTermEnd(D, id, daysFromNow(Number(b.term_days)));
      else if (b.term_ends_at !== undefined)
        await db.setTermEnd(D, id, b.term_ends_at || null);
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
  // POST /admin/stripe/links — set every Payment Link's after-payment redirect
  // (buyers otherwise land on Stripe's receipt and miss /welcome). Re-runnable.
  // POST /admin/digest — run the subscriber digest now (same logic as the cron)
  if (seg[1] === "digest" && request.method === "POST") {
    const posts = await db.postsSince(env.DB, cfg.digest_window_hours || 24);
    if (!posts.length) return json({ ran: true, posts: 0, delivered: 0 });
    const subs = cfg.free_mode ? [] : (await db.activeForDelivery(env.DB)).filter(canDeliver);
    let delivered = 0;
    for (const su of subs) {
      try { await deliverDigest(env, cfg, su, posts); delivered++; }
      catch (e) { console.error("manual digest", su.id, e); }
    }
    await db.kvSet(env.DB, "last_digest", nowIso());
    return json({ ran: true, posts: posts.length, subscribers: subs.length, delivered });
  }

  if (seg[1] === "stripe" && seg[2] === "links" && request.method === "POST") {
    const welcome = `${cfg.site_url}/welcome`;
    const thanks = `${cfg.site_url}/brief/thanks`;
    const briefUrl = (cfg.brief && cfg.brief.link) || "";
    let links;
    try { links = (await billing.listPaymentLinks(env)).data || []; }
    catch (e) {
      return json({ error: String(e).includes("STRIPE_SECRET_KEY")
        ? "STRIPE_SECRET_KEY is not set on the Worker — set it (wrangler secret put) or configure the redirects in the Stripe dashboard."
        : `stripe: ${e}` }, 502);
    }
    const out = [];
    for (const l of links) {
      const want = (briefUrl && l.url === briefUrl) ? thanks : welcome;
      const cur = l.after_completion?.type === "redirect" ? (l.after_completion.redirect?.url || null) : null;
      if (cur === want) { out.push({ url: l.url, redirect: cur, changed: false }); continue; }
      try {
        await billing.setPaymentLinkRedirect(env, l.id, want);
        out.push({ url: l.url, redirect: want, changed: true, was: cur || "(stripe receipt page)" });
      } catch (e) { out.push({ url: l.url, error: String(e).slice(0, 150) }); }
    }
    return json({ updated: out.filter((x) => x.changed).length, links: out });
  }

  if (seg[1] === "add" && request.method === "POST") {
    const b = await request.json().catch(() => ({}));
    if (!/@/.test(b.email || "")) return json({ error: "bad email" }, 400);
    const tier = cfg.tiers[b.tier] ? b.tier : "individual";
    // tier "free" is the promo — timed, unbilled; anything else added here is a
    // comp (individual/team access, no charge, no timer unless `days` is given).
    const isPromo = promoDays(cfg, tier) > 0;
    const days = Number(b.days) > 0 ? Number(b.days) : (isPromo ? promoDays(cfg, tier) : 0);
    const id = await db.createPending(D, { email: b.email, tier, cadence: b.cadence || "monthly",
      source: isPromo ? "promo" : "comp" });
    await db.setNotes(D, id, (b.note || (isPromo ? "free promo" : "manual add"))
      + (days > 0 ? ` — ${days}d term` : ""));
    // Operator-added subscribers get email delivery by default (operator vouches
    // for the address). Pass channel:"none" to add them without a live channel.
    if (b.channel !== "none")
      await db.addChannel(D, id, "email", String(b.email).trim().toLowerCase(), true, "signup");
    if (b.skip_confirm) {
      await db.setStatus(D, id, "active", "operator manual add");
      await db.logConsent(D, id, "signup", true, { detail: "operator add, confirm skipped" });
      // No activateSubscriber() on this path, so start the term from now.
      if (days > 0) await db.setTermEnd(D, id, daysFromNow(days));
    } else {
      await db.setStatus(D, id, "unconfirmed", "manual add");
      // A promo term starts at confirmation (activateSubscriber) so a slow
      // confirm doesn't eat trial days. A non-promo timed comp has no
      // activation hook, so its clock has to start now.
      if (days > 0 && !isPromo) await db.setTermEnd(D, id, daysFromNow(days));
    }
    return json({ ok: true, id });
  }
  return json({ error: "unknown admin route" }, 404);
}
