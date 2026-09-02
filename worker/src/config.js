// Runtime config = wrangler.toml [vars] merged over these defaults.
// `env.CONFIG` is a JSON string (tiers / prices / limits).

const DEFAULTS = {
  live: false,
  rate_limit_per_min: 20,
  retention_days_cancelled: 180,
  retention_days_unconfirmed: 7,
  digest_window_hours: 24,
  brief: {
    price_id: "",            // set to skip the auto-created product
    name: "SecurityCyber — on-demand threat brief",
    currency: "gbp",
    unit_amount: 4900,       // pence
    display: "£49",
    blurb: "A one-off, analyst-written deep dive on a vulnerability or incident of your choosing — impact, exposure, detection and response — delivered by email within two working days.",
  },
  tiers: {
    individual: {
      monthly_price_id: "", weekly_price_id: "",
      monthly_display: "£8 / month", weekly_display: "£2 / week",
      monthly_pence: 800, weekly_pence: 200,
      blurb: "Instant alerts for anything scoring 90+, plus a daily digest.",
      instant_min_score: 90, attach_json: false,
      channels: ["email", "discord", "telegram", "ntfy"], max_channels: 2,
    },
    team: {
      monthly_price_id: "", weekly_price_id: "",
      monthly_display: "£18 / month", weekly_display: "£5 / week",
      monthly_pence: 1800, weekly_pence: 500,
      blurb: "Everything scoring 75+ instantly, a daily digest, machine-readable JSON, Slack and Discord.",
      instant_min_score: 75, attach_json: true,
      channels: ["email", "discord", "slack", "telegram", "webhook", "ntfy"], max_channels: 6,
    },
  },
};

const CHANNEL_KINDS = ["email", "discord", "slack", "telegram", "webhook", "ntfy"];
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
  c.from_email = env.FROM_EMAIL || "";
  c.contact_email = env.CONTACT_EMAIL || env.SUPPORT_EMAIL || "";
  c.support_email = env.SUPPORT_EMAIL || env.CONTACT_EMAIL || "";
  c.company_legal_name = env.COMPANY_LEGAL_NAME || "";
  c.ico_reg = env.ICO_REG || "";
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

export function monthlyPence(c, tier, cadence) {
  const t = c.tiers[tier] || {};
  const p = t[`${cadence}_pence`] || 0;
  return cadence === "weekly" ? Math.round((p * 52) / 12) : p;
}

// go-live blockers — empty array = ready
// Hard blockers — the site can't function correctly (or lawfully, once live)
// until these are resolved. Deploying the Worker is never blocked by this.
export function problems(c, env) {
  const out = [];
  if (!c.from_email) out.push("FROM_EMAIL var is not set");
  if (!c.contact_email) out.push("CONTACT_EMAIL var is not set (privacy policy)");
  if (!c.company_legal_name) out.push("COMPANY_LEGAL_NAME var is not set (terms)");
  for (const [name, t] of Object.entries(c.tiers)) {
    if (!t.monthly_price_id && !t.weekly_price_id) out.push(`tier '${name}' has no Stripe price id`);
  }
  if (!env.RESEND_API_KEY) out.push("RESEND_API_KEY secret is not set");
  if (c.live) {
    // going live = taking real money for a service that holds a customer list
    if (!c.ico_reg) out.push("live=true but ICO_REG is not set — you must register with the ICO before taking real subscribers");
    if (!/^sk_live_/.test(env.STRIPE_SECRET_KEY || "")) out.push("live=true but STRIPE_SECRET_KEY is not sk_live_");
    if (!env.STRIPE_WEBHOOK_SECRET) out.push("STRIPE_WEBHOOK_SECRET secret is not set");
  } else if (!env.STRIPE_SECRET_KEY) {
    out.push("STRIPE_SECRET_KEY secret is not set (use sk_test_ while live=false)");
  }
  return out;
}

// Non-blocking things to sort before flipping CONFIG.live to true.
export function advisories(c) {
  const out = [];
  if (!c.live && !c.ico_reg)
    out.push("ICO registration not set yet — required before CONFIG.live: true. Deploy + test now, register at ico.org.uk, then paste ICO_REG.");
  if (!c.live) out.push("Stripe is in TEST mode (CONFIG.live: false) — no real charges. Run a full test checkout before going live.");
  return out;
}

export { CHANNEL_KINDS, STATUSES, CADENCES, DEFAULTS };
