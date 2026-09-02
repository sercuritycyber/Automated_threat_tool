// Stripe — REST via fetch, webhook verify via Web Crypto. Ported from lib/billing.py.
import { priceToTier, priceId } from "./config.js";
import * as db from "./db.js";

const API = "https://api.stripe.com/v1";
const SIG_TOLERANCE = 300;

export class StripeError extends Error {}
export class WebhookError extends Error {}

// Stripe wants bracketed form encoding: a[b][c]=v
function formEncode(obj, prefix = "") {
  const parts = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}[${k}]` : k;
    if (v == null) continue;
    if (Array.isArray(v)) {
      v.forEach((item, i) => {
        if (item && typeof item === "object") parts.push(formEncode(item, `${key}[${i}]`));
        else parts.push(`${encodeURIComponent(`${key}[${i}]`)}=${encodeURIComponent(item)}`);
      });
    } else if (typeof v === "object") {
      parts.push(formEncode(v, key));
    } else if (typeof v === "boolean") {
      parts.push(`${encodeURIComponent(key)}=${v ? "true" : "false"}`);
    } else {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(v)}`);
    }
  }
  return parts.filter(Boolean).join("&");
}

async function req(env, method, path, data) {
  const key = env.STRIPE_SECRET_KEY;
  if (!key) throw new StripeError("STRIPE_SECRET_KEY not set");
  let r;
  try {
    r = await fetch(`${API}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: data ? formEncode(data) : undefined,
    });
  } catch (e) { throw new StripeError(`network: ${e}`); }
  const j = await r.json().catch(() => ({}));
  if (r.status >= 400) throw new StripeError(`HTTP ${r.status}: ${j.error?.message || ""}`);
  return j;
}

// Returns the recurring Stripe price id for a tier+cadence. Uses the id from
// CONFIG.tiers.<tier>.<cadence>_price_id if set; otherwise creates one product
// per tier and one recurring price per cadence on first use, caching both in
// D1 kv. So the site works end-to-end with only STRIPE_SECRET_KEY set — no
// need to pre-create products or paste price ids into wrangler.toml.
export async function ensureTierPrice(env, cfg, tier, cadence) {
  const configured = priceId(cfg, tier, cadence);
  if (configured) return configured;
  const t = cfg.tiers[tier];
  if (!t) throw new StripeError(`unknown tier '${tier}'`);
  const pence = Number(cadence === "weekly" ? t.weekly_pence : t.monthly_pence) || 0;
  if (!pence) throw new StripeError(`tier '${tier}' has no ${cadence}_pence in CONFIG`);
  const priceKey = `tier_price:${tier}:${cadence}`;
  const cached = await db.kvGet(env.DB, priceKey);
  if (cached) return cached;
  const prodKey = `tier_product:${tier}`;
  let productId = await db.kvGet(env.DB, prodKey);
  if (!productId) {
    const label = tier.charAt(0).toUpperCase() + tier.slice(1);
    const product = await req(env, "POST", "/products", { name: `${cfg.brand} CTI Alerts — ${label}` });
    productId = product.id;
    if (!productId) throw new StripeError("product created but has no id");
    await db.kvSet(env.DB, prodKey, productId);
  }
  const price = await req(env, "POST", "/prices", {
    product: productId,
    currency: "gbp",
    unit_amount: pence,
    recurring: { interval: cadence === "weekly" ? "week" : "month" },
  });
  if (!price.id) throw new StripeError("price created but has no id");
  await db.kvSet(env.DB, priceKey, price.id);
  return price.id;
}

export async function createCheckoutSession(env, cfg, { tier, cadence, email, successUrl, cancelUrl, clientRef }) {
  cadence = ["monthly", "weekly"].includes(cadence) ? cadence : "monthly";
  const pid = await ensureTierPrice(env, cfg, tier, cadence);
  const payload = {
    mode: "subscription",
    line_items: [{ price: pid, quantity: 1 }],
    success_url: successUrl,
    cancel_url: cancelUrl,
    allow_promotion_codes: true,
    billing_address_collection: "auto",
    subscription_data: { metadata: { tier, cadence } },
    metadata: { tier, cadence },
    consent_collection: { terms_of_service: "required" },
  };
  if (email) payload.customer_email = email;
  if (clientRef) payload.client_reference_id = clientRef;
  const s = await req(env, "POST", "/checkout/sessions", payload);
  return { id: s.id, url: s.url };
}

export async function portalSession(env, customerId, returnUrl) {
  const p = await req(env, "POST", "/billing_portal/sessions", { customer: customerId, return_url: returnUrl });
  return { url: p.url };
}

// ---- one-off "threat brief" purchase (Checkout in `payment` mode) --------
// Returns the price id to put in checkout line_items. Uses BRIEF_PRICE_ID /
// CONFIG.brief.price_id if set, otherwise creates a product+price once and
// caches its default_price in D1 kv.
export async function ensureBriefPrice(env, cfg) {
  const b = cfg.brief || {};
  const configured = env.BRIEF_PRICE_ID || b.price_id;
  if (configured) return configured;
  const cached = await db.kvGet(env.DB, "brief_price_id");
  if (cached) return cached;
  const product = await req(env, "POST", "/products", {
    name: b.name || `${cfg.brand} — on-demand threat brief`,
    default_price_data: {
      currency: b.currency || "gbp",
      unit_amount: Number(b.unit_amount || 4900),
    },
  });
  const priceId = product.default_price;
  if (!priceId) throw new StripeError("product created but has no default_price");
  await db.kvSet(env.DB, "brief_price_id", priceId);
  return priceId;
}

export async function createBriefCheckout(env, cfg, { email, successUrl, cancelUrl, clientRef }) {
  const price = await ensureBriefPrice(env, cfg);
  const payload = {
    mode: "payment",
    line_items: [{ price, quantity: 1 }],
    success_url: successUrl,
    cancel_url: cancelUrl,
    allow_promotion_codes: true,
    billing_address_collection: "auto",
    metadata: { item: "brief" },
    payment_intent_data: { metadata: { item: "brief" } },
  };
  if (email) payload.customer_email = email;
  if (clientRef) payload.client_reference_id = clientRef;
  const s = await req(env, "POST", "/checkout/sessions", payload);
  return { id: s.id, url: s.url };
}
export const getSubscription = (env, id) => req(env, "GET", `/subscriptions/${id}`);
export const cancelSubscription = (env, id) => req(env, "DELETE", `/subscriptions/${id}`);

// ---- webhook signature (t=…,v1=…) --------------------------------
export async function verifyWebhook(payload, sigHeader, secret) {
  if (!secret) throw new WebhookError("STRIPE_WEBHOOK_SECRET not set");
  const parts = Object.fromEntries((sigHeader || "").split(",").map((p) => p.split("=")).filter((a) => a.length === 2));
  const ts = parts.t, v1 = parts.v1;
  if (!ts || !v1) throw new WebhookError("malformed Stripe-Signature header");
  if (Math.abs(Date.now() / 1000 - Number(ts)) > SIG_TOLERANCE) throw new WebhookError("timestamp outside tolerance");
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  const expected = [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (expected.length !== v1.length || !timingEq(expected, v1)) throw new WebhookError("signature mismatch");
  try { return JSON.parse(payload); } catch (e) { throw new WebhookError(`bad JSON: ${e}`); }
}
function timingEq(a, b) { let d = 0; for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i); return d === 0; }

export const _formEncode = formEncode;  // test only

function priceFromSubObj(o) {
  try { return o.items.data[0].price.id; } catch { return null; }
}

// ---- event -> store ------------------------------------------
export async function handleEvent(env, cfg, event) {
  const D = env.DB;
  const eid = event.id || "";
  const etype = event.type || "";
  const obj = event.data?.object || {};
  const p2t = priceToTier(cfg);
  if (eid && await db.seenStripeEvent(D, eid)) return { action: "duplicate", type: etype };

  let result = { action: "ignored", type: etype };

  // ---- one-off purchase (Checkout `payment` mode) ----
  if (etype === "checkout.session.completed" && (obj.mode === "payment" || obj.metadata?.item === "brief")) {
    const email = obj.customer_details?.email || obj.customer_email || null;
    let subId = null;
    if (email) { const s = await db.getByEmail(D, email); if (s) subId = s.id; }
    const row = await db.recordPurchase(D, {
      stripe_session_id: obj.id,
      payment_intent: obj.payment_intent || null,
      email, subscriber_id: subId,
      item: obj.metadata?.item || "brief",
      amount_total: obj.amount_total ?? null,
      currency: obj.currency || null,
      status: obj.payment_status === "paid" ? "paid" : "pending",
    });
    await db.logEvent(D, "stripe:brief_purchased", subId, { session: obj.id, amount: obj.amount_total });
    if (eid) await db.markStripeEvent(D, eid);
    return { action: "brief_purchased", email, session_id: obj.id, purchase: row };
  }

  if (etype === "checkout.session.completed") {
    let email = obj.customer_details?.email || obj.customer_email;
    const customer = obj.customer, subscription = obj.subscription;
    let tier = obj.metadata?.tier, cadence = obj.metadata?.cadence;
    const clientRef = obj.client_reference_id;
    const intent = clientRef ? await db.getIntent(D, clientRef) : null;
    if (intent) { tier = tier || intent.tier; cadence = cadence || intent.cadence; email = email || intent.email; }
    if ((!tier || !cadence) && subscription) {
      try {
        const so = await getSubscription(env, subscription);
        const t = p2t[priceFromSubObj(so)];
        if (t) { tier = tier || t[0]; cadence = cadence || t[1]; }
      } catch { /* ignore */ }
    }
    tier = tier || "individual";
    cadence = ["monthly", "weekly"].includes(cadence) ? cadence : "monthly";
    if (email) {
      const sid = await db.createPending(D, { email, tier, cadence, source: "stripe", stripe_customer_id: customer });
      await db.setStripeIds(D, sid, { customer_id: customer, subscription_id: subscription });
      await db.setStatus(D, sid, "unconfirmed", "checkout complete");
      await db.logEvent(D, "stripe:checkout_completed", sid, { session: obj.id, cadence });
      let channel = null;
      if (intent?.channel_kind) {
        channel = { kind: intent.channel_kind, target: intent.channel_target || "" };
        if (channel.target) await db.addChannel(D, sid, channel.kind, channel.target, false, "signup");
        await db.consumeIntent(D, clientRef);
      }
      result = { action: "send_confirm", subscriber_id: sid, email, tier, cadence, channel };
    } else {
      result = { action: "error", detail: "no email on checkout session" };
    }
  } else if (etype === "customer.subscription.updated") {
    const sub = await db.getByStripeSubscription(D, obj.id) || await db.getByStripeCustomer(D, obj.customer);
    if (sub) {
      await db.setStripeIds(D, sub.id, { subscription_id: obj.id, period_end: obj.current_period_end });
      const st = obj.status;
      if (st === "past_due") { await db.setStatus(D, sub.id, "past_due", "stripe: past_due"); result = { action: "notify_owner", detail: `${sub.email} past_due` }; }
      else if (["canceled", "unpaid", "incomplete_expired"].includes(st)) { await db.setStatus(D, sub.id, "cancelled", `stripe: ${st}`); result = { action: "cancelled", subscriber_id: sub.id }; }
      else if (st === "active" && sub.status === "past_due") { await db.setStatus(D, sub.id, "active", "stripe: recovered"); result = { action: "recovered", subscriber_id: sub.id }; }
      else result = { action: "synced", subscriber_id: sub.id };
    }
  } else if (etype === "customer.subscription.deleted") {
    const sub = await db.getByStripeSubscription(D, obj.id) || await db.getByStripeCustomer(D, obj.customer);
    if (sub) { await db.setStatus(D, sub.id, "cancelled", "stripe: subscription deleted"); result = { action: "cancelled", subscriber_id: sub.id }; }
  } else if (etype === "invoice.payment_failed") {
    const sub = await db.getByStripeCustomer(D, obj.customer);
    if (sub) { await db.setStatus(D, sub.id, "past_due", "stripe: payment failed"); result = { action: "notify_owner", detail: `${sub.email} payment failed` }; }
  } else if (["invoice.paid", "invoice.payment_succeeded"].includes(etype)) {
    const sub = await db.getByStripeCustomer(D, obj.customer);
    if (sub && sub.status === "past_due") { await db.setStatus(D, sub.id, "active", "stripe: payment recovered"); result = { action: "recovered", subscriber_id: sub.id }; }
  }

  if (eid) await db.markStripeEvent(D, eid);
  return result;
}
