// Runtime config = wrangler.toml [vars] merged over these defaults.
// `env.CONFIG` is a JSON string (tiers / prices / limits).

const DEFAULTS = {
  live: false,
  // "link_only" = sell strictly through Stripe Payment Links; a cadence with
  // no link is simply not offered. "auto" falls back to an API-created
  // Checkout Session (which needs STRIPE_SECRET_KEY).
  checkout_mode: "link_only",
  // Delivery kinds to hide from signup regardless of secrets, e.g. ["slack"].
  // Anything listed here shows as "unavailable" and is rejected server-side.
  channels_disabled: [],
  // Stripe retries a failed card for days; cut access once it has tried this
  // many times. 2 keeps a bank blip from costing you a customer.
  cancel_after_failed_payments: 2,
  // Shared Discord channel: post a story here once it clears this score. A
  // community feed, so a single fixed bar — not a per-subscriber one.
  discord_channel_min_score: 50,
  // Permanently-active free subscribers: the brand's own address, plus any
  // test address. Never lapse, never billed. Also settable via
  // FOREVER_FREE_EMAILS (comma list) in wrangler.toml [vars].
  forever_free_emails: ["securitycyber_cti@securitycyber.uk"],
  rate_limit_per_min: 20,
  retention_days_cancelled: 180,
  retention_days_unconfirmed: 7,
  digest_window_hours: 24,
  // The digest EMAIL only carries score >= this OR an actionable category
  // (exploitation / breach / campaign / supply-chain / fraud-trend /
  // exec-protection). Low-signal items (news, minor advisories) still land
  // in the private RSS feed above the tier floor — just not the inbox.
  digest_min_score: 30,
  // The private RSS feed carries everything above a subscriber's floor; the
  // daily digest email is the top slice of that, so it stays readable on a
  // heavy day. Raise if subscribers want the whole list in the email too.
  digest_max_items: 25,
  brief: {
    link: "https://buy.stripe.com/bJe4gy5dRa9z8IX3Pf3ks02",  // Stripe Payment Link (one-off)
    price_id: "",            // set to skip the auto-created product
    name: "SecurityCyber — on-demand threat brief",
    currency: "gbp",
    unit_amount: 4900,       // pence
    display: "£49",
    blurb: "A one-off, analyst-written deep dive on a vulnerability or incident of your choosing — impact, exposure, detection and response — delivered by email within two working days.",
  },
  tiers: {
    // Free plan — self-serve and forever. £0 Stripe Payment Link: Stripe runs
    // the sign-up page (email, no card, no charge) and fires
    // checkout.session.completed, so the same webhook path provisions it. The
    // signup intent carries tier=free; there's no recurring price and, with
    // promo_days 0, no Worker-managed term either — nothing ever lapses.
    // A capped Individual: only the genuinely urgent items interrupt
    // (instant_min_score 90); everything above the floor still lands in the
    // daily digest and private feed.
    //
    // Timed trials / comps are separate: grant them per-account with
    // `POST /admin/add {tier:"individual", days:N}` — that sets term_ends_at and
    // the daily lifecycle cron lapses the account when it passes. Set
    // promo_days > 0 on any tier to make every signup to it an N-day promo.
    free: {
      // 7-day free trial of the full Individual experience. Self-serve via the
      // £0 Stripe Payment Link; promo_days sets term_ends_at at activation and
      // the daily lifecycle cron lapses the account to 'cancelled' when it passes.
      monthly_link: "https://buy.stripe.com/eVqbJ07lZ4Pf8IXfxX3ks04",
      weekly_link: "",
      monthly_price_id: "", weekly_price_id: "",
      monthly_display: "Free trial", weekly_display: "",
      monthly_pence: 0, weekly_pence: 0,
      free: true,
      promo_days: 7,
      blurb: "A 7-day run of the full Individual plan — instant alerts plus the daily digest, to email, RSS, Discord, Telegram or ntfy. No card. Keep it going for £8/month or £2/week.",
      instant_min_score: 70, attach_json: false,
      min_score: 15,
      channels: ["email", "rss", "discord", "telegram", "ntfy"], max_channels: 3,
    },
    individual: {
      // Stripe Payment Links — preferred. When set, "Continue to payment"
      // redirects straight here with ?client_reference_id=<signup intent>, so
      // the webhook can still tie the payment back to the chosen tier,
      // cadence and delivery channel. Blank falls back to an API-created
      // Checkout Session (needs STRIPE_SECRET_KEY).
      monthly_link: "https://buy.stripe.com/aFa3cu6hVdlLf7lbhH3ks01",
      weekly_link: "https://buy.stripe.com/28EdR8dKn5Tj1gv0D33ks00",
      monthly_price_id: "", weekly_price_id: "",
      monthly_display: "£8 / month", weekly_display: "£2 / week",
      monthly_pence: 800, weekly_pence: 200,
      blurb: "Instant alerts for anything scoring 90+, plus a daily digest.",
      instant_min_score: 70, attach_json: false,
      // min_score is the floor below which a story never reaches them. 15 keeps
      // out only pure noise; everything with a real security angle flows into
      // the digest and private feed, and the subscriber raises their own
      // threshold if they want less. instant_min_score is what INTERRUPTS.
      min_score: 15,
      channels: ["email", "rss", "discord", "telegram", "ntfy"], max_channels: 3,
    },
    team: {
      monthly_link: "https://buy.stripe.com/bJe5kC8q32H77ETdpP3ks03",
      weekly_link: "",
      monthly_price_id: "", weekly_price_id: "",
      // Team is monthly only. Weekly billing is offered on Individual alone
      // (the £2 plan); weekly_pence 0 removes it everywhere — pricing page,
      // signup form, and the missing-link advisories.
      monthly_display: "£18 / month", weekly_display: "",
      monthly_pence: 1800, weekly_pence: 0,
      blurb: "Everything scoring 75+ instantly, a daily digest, machine-readable JSON, Slack and Discord.",
      instant_min_score: 55, attach_json: true,
      min_score: 10,
      channels: ["email", "rss", "discord", "discord_webhook", "slack", "telegram", "webhook", "ntfy"], max_channels: 8,
    },
  },
};

const CHANNEL_KINDS = ["email", "rss", "discord", "discord_webhook", "slack", "telegram", "webhook", "ntfy"];
const STATUSES = ["pending_payment", "unconfirmed", "active", "paused", "past_due", "cancelled"];
const CADENCES = ["monthly", "weekly"];

function deepMerge(base, over) {
  const out = { ...base };
  for (const [k, v] of Object.entries(over || {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) && typeof out[k] === "object"
      ? deepMerge(out[k], v) : v;
  }
  return out;
}

// SecurityCyber's brand motto (Charlie, 2026-09-25). Full version on brand
// pages and emails; the short line on posts. Keep in step with bin/publishers.py.
export const MOTTO = "You don't have an intelligence problem, you have an ingestion problem. "
  + "You don't have an information problem, you have a triage problem.";
export const MOTTO_SHORT = "You don't have an information problem. You have a triage problem.";

export function config(env) {
  let over = {};
  try { over = env.CONFIG ? JSON.parse(env.CONFIG) : {}; } catch { over = {}; }
  const c = deepMerge(DEFAULTS, over);
  // normalise tiers onto their defaults
  for (const name of Object.keys(DEFAULTS.tiers)) {
    c.tiers[name] = deepMerge(DEFAULTS.tiers[name], (over.tiers || {})[name] || {});
  }
  c.brief = deepMerge(DEFAULTS.brief, over.brief || {});
  c.brand = env.BRAND || "SecurityCyber";
  c.site_url = (env.SITE_URL || "").replace(/\/$/, "");
  c.main_site = env.MAIN_SITE || "https://securitycyber.uk";
  // Same GA4 property as securitycyber.uk, so a visit is one journey across both.
  // GA_MEASUREMENT_ID="" disables it.
  c.ga_measurement_id = env.GA_MEASUREMENT_ID ?? "G-F43K0Y370J";
  c.from_email = env.FROM_EMAIL || "";
  c.contact_email = env.CONTACT_EMAIL || env.SUPPORT_EMAIL || "";
  c.support_email = env.SUPPORT_EMAIL || env.CONTACT_EMAIL || "";
  c.company_legal_name = env.COMPANY_LEGAL_NAME || "";
  c.ico_reg = env.ICO_REG || "";
  // ico_pending: registration is filed but the number has not arrived — lets
  // go-live proceed without displaying a fake reg number. Clear it and set
  // ICO_REG once the ICO issues one.
  c.ico_pending = !!over.ico_pending;
  if (env.FOREVER_FREE_EMAILS)
    c.forever_free_emails = [...new Set([...(c.forever_free_emails || []),
      ...env.FOREVER_FREE_EMAILS.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean)])];
  c.forever_free_emails = (c.forever_free_emails || []).map((x) => String(x).toLowerCase());
  c.terms_version = env.TERMS_VERSION || "2026-09-02";
  return c;
}

export function priceToTier(c) {
  const out = {};
  for (const [name, t] of Object.entries(c.tiers)) {
    if (t.monthly_price_id) out[t.monthly_price_id] = [name, "monthly"];
    if (t.weekly_price_id) out[t.weekly_price_id] = [name, "weekly"];
  }
  return out;
}

export function priceId(c, tier, cadence) {
  return (c.tiers[tier] || {})[`${cadence}_price_id`] || "";
}

// The Stripe Payment Link for a tier+cadence, if one is configured.
export function paymentLink(c, tier, cadence) {
  return (c.tiers[tier] || {})[`${cadence}_link`] || "";
}

// Attach our signup intent to a Payment Link. Stripe passes
// `client_reference_id` straight through to checkout.session.completed, which
// is how the webhook recovers the tier, cadence and delivery channel the
// customer chose before we sent them to Stripe. `prefilled_email` saves them
// retyping the address we already validated.
export function paymentLinkUrl(link, { clientRef, email } = {}) {
  if (!link) return "";
  let u;
  try { u = new URL(link); } catch { return ""; }
  if (clientRef) u.searchParams.set("client_reference_id", clientRef);
  if (email) u.searchParams.set("prefilled_email", email);
  return u.toString();
}

// Can this tier+cadence be bought right now?
//   checkout_mode "link_only" — only if it has a Payment Link. Use this once
//                               you're selling purely via links, so a cadence
//                               you haven't made a link for is never offered.
//   checkout_mode "auto"      — link if present, else an API Checkout Session.
export function canCheckout(c, env, tier, cadence) {
  if (paymentLink(c, tier, cadence)) return true;
  if (c.checkout_mode === "link_only") return false;
  return !!env.STRIPE_SECRET_KEY;
}

// { individual: {monthly:true, weekly:false}, team: {...} } — what the
// storefront may actually offer. Keeps the pricing page and the signup form
// from disagreeing about what's for sale.
export function buyableMap(c, env) {
  const out = {};
  for (const tier of Object.keys(c.tiers)) {
    out[tier] = {
      monthly: Number(c.tiers[tier].monthly_pence) > 0 && canCheckout(c, env, tier, "monthly"),
      weekly: Number(c.tiers[tier].weekly_pence) > 0 && canCheckout(c, env, tier, "weekly"),
    };
  }
  return out;
}

export function monthlyPence(c, tier, cadence) {
  const t = c.tiers[tier] || {};
  const p = t[`${cadence}_pence`] || 0;
  return cadence === "weekly" ? Math.round((p * 52) / 12) : p;
}

// Are we actually taking real money right now?
//
// CONFIG.live is a statement of intent; it does NOT gate anything once you
// sell through Stripe Payment Links, because a link charges in whatever mode
// it was created in no matter what this Worker thinks. So the honest signal is
// the Stripe key: an sk_live_ key means live cards, live money, live
// obligations — regardless of CONFIG.live. Treat either as live and never
// reassure the operator they're in test mode when they aren't.
export function takingRealMoney(c, env) {
  return !!c.live || /^sk_live_/.test(env.STRIPE_SECRET_KEY || "");
}

// go-live blockers — empty array = ready
// Hard blockers — the site can't function correctly (or lawfully, once live)
// until these are resolved. Deploying the Worker is never blocked by this.
export function problems(c, env) {
  const out = [];
  if (!c.from_email) out.push("FROM_EMAIL var is not set");
  if (!c.contact_email) out.push("CONTACT_EMAIL var is not set (privacy policy)");
  if (!c.company_legal_name) out.push("COMPANY_LEGAL_NAME var is not set (terms)");
  // Tier price ids are optional — billing.ensureTierPrice() creates them on
  // first checkout when they're blank. Only the Stripe key + webhook matter.
  if (!env.RESEND_API_KEY) out.push("RESEND_API_KEY secret is not set");

  // However the customer pays — Payment Link or API session — Stripe tells us
  // about it exactly one way: the webhook. Without the signing secret we
  // reject every event, so people are charged and never provisioned. This is
  // the single most important secret and it is ALWAYS required.
  if (!env.STRIPE_WEBHOOK_SECRET)
    out.push("STRIPE_WEBHOOK_SECRET is not set — payments will be taken but NO subscriber will ever be created");
  // Signed confirm / preferences / unsubscribe links can't be made without it.
  if (!env.SUBS_TOKEN_SECRET)
    out.push("SUBS_TOKEN_SECRET is not set — confirmation and preferences links will fail");

  const linkOnly = c.checkout_mode === "link_only";
  if (linkOnly) {
    const gaps = [];
    for (const [name, t] of Object.entries(c.tiers)) {
      for (const cad of ["monthly", "weekly"]) {
        if (Number(t[`${cad}_pence`]) > 0 && !t[`${cad}_link`]) gaps.push(`${name}/${cad}`);
      }
    }
    if (gaps.length === Object.keys(c.tiers).length * 2)
      out.push("checkout_mode is link_only but no Stripe Payment Links are set — nothing can be bought");
  } else if (!env.STRIPE_SECRET_KEY) {
    out.push("STRIPE_SECRET_KEY secret is not set (use sk_test_ while live=false)");
  }

  // Taking real money = holding a real customer list, which in the UK needs
  // ICO registration. Keyed off the live Stripe key, not CONFIG.live, so this
  // can't be dodged by leaving a flag switched off.
  if (takingRealMoney(c, env) && !c.ico_reg && !c.ico_pending)
    out.push("Live Stripe key in use — ICO_REG must be set. You are taking real money and holding real customer data; UK registration is required (ico.org.uk, £40-52/yr).");
  if (c.live && !linkOnly && !env.STRIPE_SECRET_KEY)
    out.push("live=true but STRIPE_SECRET_KEY is not set");
  return out;
}

// Non-blocking things to sort before flipping CONFIG.live to true.
export function advisories(c, env = {}) {
  const out = [];
  const live = takingRealMoney(c, env);
  const keyLive = /^sk_live_/.test(env.STRIPE_SECRET_KEY || "");
  // The dangerous case: a live key while CONFIG.live says otherwise. Saying
  // "test mode, no real charges" there would be a lie that costs someone money.
  if (keyLive && !c.live)
    out.push("CAUTION: the Stripe key is sk_live_ but CONFIG.live is false. Payment Links charge in whatever mode they were created in, so CONFIG.live gates nothing — treat this as LIVE. Real cards will be charged.");
  else if (live) out.push("LIVE — real cards are being charged.");
  else if (env.STRIPE_SECRET_KEY) out.push("Stripe key is sk_test_ — no real charges. Run a full test checkout before switching to a live key.");
  if (!c.ico_reg) {
    if (c.ico_pending)
      out.push("ICO registration is in progress — paste ICO_REG and remove ico_pending once the number arrives (legally required for a live customer list).");
    else if (!live)
      out.push("ICO registration not set yet — required before you take real money. Register at ico.org.uk, then paste ICO_REG.");
  }
  if (c.checkout_mode === "link_only") {
    const gaps = [];
    for (const [name, t] of Object.entries(c.tiers)) {
      for (const cad of ["monthly", "weekly"]) {
        if (Number(t[`${cad}_pence`]) > 0 && !t[`${cad}_link`]) gaps.push(`${name} ${cad}`);
      }
    }
    if (gaps.length)
      out.push(`No Stripe Payment Link for: ${gaps.join(", ")} — those options are hidden from the pricing page until you add one.`);
    if (!(c.brief || {}).link)
      out.push("No Payment Link for the one-off brief — the /brief page is hidden.");
    out.push("Each Payment Link must be set to redirect to "
      + `${c.site_url || "<site>"}/welcome after payment (Stripe → the link → After payment → Redirect), `
      + "otherwise buyers land on Stripe's own receipt page and miss the confirmation step.");
  } else {
    const noPrice = Object.entries(c.tiers).filter(([, t]) => !t.monthly_price_id && !t.weekly_price_id).map(([n]) => n);
    if (noPrice.length) out.push(`tiers [${noPrice.join(", ")}] have no Stripe price id — a product + price will be auto-created on the first checkout.`);
  }
  return out;
}

export { CHANNEL_KINDS, STATUSES, CADENCES, DEFAULTS };
