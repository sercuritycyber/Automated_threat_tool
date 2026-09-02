// D1 data layer — subscriber store. Ported from lib/subscribers.py.
import { nowIso, isoDaysAgo } from "./util.js";

export const TERMS_VERSION_DEFAULT = "2026-09-02";

// ---- thin D1 helpers -------------------------------------------------------
const one = (db, sql, ...a) => db.prepare(sql).bind(...a).first();
const many = async (db, sql, ...a) => (await db.prepare(sql).bind(...a).all()).results || [];
const run = (db, sql, ...a) => db.prepare(sql).bind(...a).run();

function vendorsLoad(s) {
  try { return (JSON.parse(s || "[]") || []).map((x) => String(x).trim()).filter(Boolean); }
  catch { return []; }
}
function mask(s) {
  s = s || "";
  if (s.includes("@")) { const [u, d] = s.split("@"); return `${u.slice(0, 2)}***@${d}`; }
  return s.length > 9 ? s.slice(0, 6) + "…" : s;
}

// ---- subscribers ----------------------------------------------------------
export async function createPending(db, { email, tier, cadence = "monthly", source = "stripe",
                                          stripe_customer_id = null, name = null }) {
  email = email.trim().toLowerCase();
  cadence = ["monthly", "weekly"].includes(cadence) ? cadence : "monthly";
  const existing = await getByEmail(db, email);
  if (existing) {
    await run(db,
      `UPDATE subscribers SET tier=?, cadence=?, source=?,
        stripe_customer_id=COALESCE(?, stripe_customer_id),
        status=CASE WHEN status='cancelled' THEN 'pending_payment' ELSE status END WHERE id=?`,
      tier, cadence, source, stripe_customer_id, existing.id);
    return existing.id;
  }
  const r = await run(db,
    `INSERT INTO subscribers(email, name, tier, cadence, status, source, stripe_customer_id, created_at)
     VALUES (?, ?, ?, ?, 'pending_payment', ?, ?, ?)`,
    email, name, tier, cadence, source, stripe_customer_id, nowIso());
  const id = r.meta.last_row_id;
  await run(db, "INSERT INTO prefs(subscriber_id, updated_at) VALUES (?, ?)", id, nowIso());
  await run(db, "INSERT INTO channels(subscriber_id, kind, target, verified, created_at) VALUES (?, 'email', ?, 0, ?)",
    id, email, nowIso());
  await logEvent(db, "created", id, { tier, source });
  return id;
}

export async function get(db, id, hydrate = true) {
  const s = await one(db, "SELECT * FROM subscribers WHERE id = ?", id);
  return hydrate ? hydrateSub(db, s) : s;
}
export async function getByEmail(db, email, hydrate = false) {
  const s = await one(db, "SELECT * FROM subscribers WHERE email = ?", email.trim().toLowerCase());
  return hydrate ? hydrateSub(db, s) : s;
}
export const getByStripeCustomer = (db, cid) =>
  one(db, "SELECT * FROM subscribers WHERE stripe_customer_id = ?", cid);
export const getByStripeSubscription = (db, sid) =>
  one(db, "SELECT * FROM subscribers WHERE stripe_subscription_id = ?", sid);

async function hydrateSub(db, s) {
  if (!s) return null;
  s.channels = await channelsFor(db, s.id);
  s.prefs = await getPrefs(db, s.id);
  return s;
}

export function listSubscribers(db, { status, tier, qstr, limit = 500, offset = 0 } = {}) {
  const where = [], args = [];
  if (status) { where.push("status = ?"); args.push(status); }
  if (tier) { where.push("tier = ?"); args.push(tier); }
  if (qstr) { where.push("(email LIKE ? OR name LIKE ? OR notes LIKE ?)"); args.push(`%${qstr}%`, `%${qstr}%`, `%${qstr}%`); }
  let sql = "SELECT * FROM subscribers";
  if (where.length) sql += " WHERE " + where.join(" AND ");
  sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?";
  args.push(limit, offset);
  return many(db, sql, ...args);
}

export async function setStatus(db, id, status, reason = "", ip = null) {
  let extra = "";
  const args = [status];
  if (status === "active") { extra = ", confirmed_at = COALESCE(confirmed_at, ?)"; args.push(nowIso()); }
  else if (status === "cancelled") { extra = ", cancelled_at = ?"; args.push(nowIso()); }
  args.push(id);
  await run(db, `UPDATE subscribers SET status = ?${extra} WHERE id = ?`, ...args);
  await logEvent(db, "status:" + status, id, { reason }, ip);
}

export async function setStripeIds(db, id, { customer_id = null, subscription_id = null, period_end = null } = {}) {
  await run(db,
    `UPDATE subscribers SET stripe_customer_id = COALESCE(?, stripe_customer_id),
       stripe_subscription_id = COALESCE(?, stripe_subscription_id),
       current_period_end = COALESCE(?, current_period_end) WHERE id = ?`,
    customer_id, subscription_id, period_end, id);
}
export const setCadence = (db, id, cad) =>
  ["monthly", "weekly"].includes(cad) ? run(db, "UPDATE subscribers SET cadence=? WHERE id=?", cad, id) : null;
export const setTier = (db, id, tier) => run(db, "UPDATE subscribers SET tier=? WHERE id=?", tier, id);
export const setNotes = (db, id, notes) => run(db, "UPDATE subscribers SET notes=? WHERE id=?", notes, id);

// ---- channels -----------------------------------------------------------
export const channelsFor = (db, id) =>
  many(db, "SELECT * FROM channels WHERE subscriber_id = ? ORDER BY id", id);

export async function addChannel(db, id, kind, target, verified = false, label = null) {
  target = target.trim();
  await run(db,
    `INSERT INTO channels(subscriber_id, kind, target, label, verified, created_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(subscriber_id, kind, target) DO UPDATE SET verified=excluded.verified, label=excluded.label`,
    id, kind, target, label, verified ? 1 : 0, nowIso());
  const row = await one(db, "SELECT id FROM channels WHERE subscriber_id=? AND kind=? AND target=?", id, kind, target);
  await logEvent(db, "channel:add:" + kind, id, { target: mask(target) });
  return row ? row.id : 0;
}
export const verifyChannel = (db, cid) => run(db, "UPDATE channels SET verified = 1 WHERE id = ?", cid);
export const removeChannel = (db, cid) => run(db, "DELETE FROM channels WHERE id = ?", cid);

// ---- prefs ------------------------------------------------------------
export async function getPrefs(db, id) {
  let r = await one(db, "SELECT * FROM prefs WHERE subscriber_id = ?", id);
  if (!r) { await run(db, "INSERT INTO prefs(subscriber_id, updated_at) VALUES (?, ?)", id, nowIso()); r = {}; }
  return {
    vendors: vendorsLoad(r.vendors),
    min_score_override: r.min_score_override ?? null,
    instant: r.instant == null ? true : !!r.instant,
    digest: r.digest == null ? true : !!r.digest,
  };
}
export async function setPrefs(db, id, { vendors, min_score_override, instant, digest } = {}) {
  const cur = await getPrefs(db, id);
  await run(db,
    "UPDATE prefs SET vendors=?, min_score_override=?, instant=?, digest=?, updated_at=? WHERE subscriber_id=?",
    JSON.stringify(vendors ?? cur.vendors),
    min_score_override === undefined ? cur.min_score_override : min_score_override,
    (instant ?? cur.instant) ? 1 : 0, (digest ?? cur.digest) ? 1 : 0, nowIso(), id);
  await logEvent(db, "prefs:update", id);
}

// ---- consent / events ------------------------------------------------
export function logConsent(db, id, kind, granted, { ip = null, ua = null, terms_version = null, detail = null } = {}) {
  return run(db,
    "INSERT INTO consents(subscriber_id, kind, granted, ts, ip, user_agent, terms_version, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    id, kind, granted ? 1 : 0, nowIso(), ip, ua, terms_version || TERMS_VERSION_DEFAULT, detail);
}
export const consentsFor = (db, id) =>
  many(db, "SELECT * FROM consents WHERE subscriber_id = ? ORDER BY ts", id);

export function logEvent(db, kind, id = null, detail = null, ip = null) {
  return run(db, "INSERT INTO events(subscriber_id, ts, kind, detail, ip) VALUES (?, ?, ?, ?, ?)",
    id, nowIso(), kind, detail == null ? null : JSON.stringify(detail), ip);
}
export const eventsFor = (db, id, limit = 60) =>
  many(db, "SELECT * FROM events WHERE subscriber_id = ? ORDER BY ts DESC LIMIT ?", id, limit);

// ---- deliveries -----------------------------------------------------
export async function alreadyDelivered(db, id, postKey) {
  return !!(await one(db, "SELECT 1 FROM deliveries WHERE subscriber_id=? AND post_key=? AND ok=1 LIMIT 1", id, postKey));
}
export const recordDelivery = (db, id, postKey, channelKind, mode, ok = true, error = null) =>
  run(db, "INSERT INTO deliveries(subscriber_id, post_key, channel_kind, mode, sent_at, ok, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
    id, postKey, channelKind, mode, nowIso(), ok ? 1 : 0, error);
export const deliveriesFor = (db, id, limit = 40) =>
  many(db, "SELECT * FROM deliveries WHERE subscriber_id = ? ORDER BY sent_at DESC LIMIT ?", id, limit);

// ---- stripe idempotency --------------------------------------------
export async function seenStripeEvent(db, eid) {
  return !!(await one(db, "SELECT 1 FROM stripe_events WHERE id = ?", eid));
}
export const markStripeEvent = (db, eid) =>
  run(db, "INSERT INTO stripe_events(id, ts) VALUES (?, ?) ON CONFLICT(id) DO NOTHING", eid, nowIso());

// ---- signup intents ------------------------------------------------
export const createIntent = (db, i) =>
  run(db, `INSERT INTO signup_intents(token, email, tier, cadence, channel_kind, channel_target, ip, user_agent, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    i.token, i.email.trim().toLowerCase(), i.tier, i.cadence, i.channel_kind || null,
    i.channel_target || null, i.ip || null, i.user_agent || null, nowIso());
export const getIntent = (db, token) =>
  one(db, "SELECT * FROM signup_intents WHERE token = ? AND consumed = 0", token);
export const consumeIntent = (db, token) =>
  run(db, "UPDATE signup_intents SET consumed = 1 WHERE token = ?", token);
export async function purgeIntents(db, hours = 48) {
  const r = await run(db, "DELETE FROM signup_intents WHERE created_at < ? OR consumed = 1", isoDaysAgo(hours / 24));
  return r.meta.changes || 0;
}

// ---- kv + posts_log ----------------------------------------------
export async function kvGet(db, key, dflt = null) {
  const r = await one(db, "SELECT value FROM kv WHERE key = ?", key);
  return r ? r.value : dflt;
}
export const kvSet = (db, key, value) =>
  run(db, "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", key, String(value));

export async function logPost(db, p) {
  await run(db,
    `INSERT INTO posts_log(post_key, title, url, source, score, cve_ids, post_text, short_text,
                           subscriber_text, action, seen_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(post_key) DO UPDATE SET
       -- a later run may add depth to a story we first saw as a bare post
       subscriber_text = COALESCE(NULLIF(excluded.subscriber_text, ''), posts_log.subscriber_text),
       action          = COALESCE(NULLIF(excluded.action, ''), posts_log.action)`,
    p.post_key, p.title || "", p.url || "", p.source || "", p.score || 0,
    JSON.stringify(p.cve_ids || []), p.post_text || "", p.short_text || "",
    p.subscriber_text || "", p.action || "", nowIso());
}
export async function postsSince(db, hours) {
  const rows = await many(db, "SELECT * FROM posts_log WHERE seen_at >= ? ORDER BY score DESC", isoDaysAgo(hours / 24));
  return rows.map((r) => ({ ...r, cve_ids: vendorsLoad(r.cve_ids) }));
}
export const trimPostsLog = (db, keep = 500) =>
  run(db, "DELETE FROM posts_log WHERE post_key NOT IN (SELECT post_key FROM posts_log ORDER BY seen_at DESC LIMIT ?)", keep);

// ---- private RSS feeds -------------------------------------------
// A feed URL is /feed/<token>.xml where <token> is the `target` of the
// subscriber's rss channel row. Resolving it through that row means revoking a
// feed is just deleting the row — there's no separate token table or expiry.
export async function postsForFeed(db, limit = 50) {
  // Newest first: a reader wants chronology, unlike the digest which ranks by score.
  const rows = await many(db, "SELECT * FROM posts_log ORDER BY seen_at DESC LIMIT ?", limit);
  return rows.map((r) => ({ ...r, cve_ids: vendorsLoad(r.cve_ids) }));
}
export const subscriberByFeedToken = (db, token) =>
  one(db, `SELECT s.* FROM subscribers s JOIN channels c ON c.subscriber_id = s.id
           WHERE c.kind = 'rss' AND c.target = ?`, token);
export const feedTokenFor = (db, sid) =>
  one(db, "SELECT target FROM channels WHERE subscriber_id = ? AND kind = 'rss' LIMIT 1", sid);

// ---- one-off purchases (paid threat briefs) -----------------------
export async function recordPurchase(db, p) {
  await run(db,
    `INSERT INTO purchases(stripe_session_id, payment_intent, email, subscriber_id, item,
       amount_total, currency, status, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(stripe_session_id) DO UPDATE SET
       payment_intent=excluded.payment_intent, amount_total=excluded.amount_total,
       currency=excluded.currency, status=excluded.status`,
    p.stripe_session_id, p.payment_intent || null, p.email || null, p.subscriber_id || null,
    p.item || "brief", p.amount_total ?? null, p.currency || null, p.status || "paid", nowIso());
  return one(db, "SELECT * FROM purchases WHERE stripe_session_id = ?", p.stripe_session_id);
}
export const getPurchaseBySession = (db, sid) =>
  one(db, "SELECT * FROM purchases WHERE stripe_session_id = ?", sid);
export async function markPurchaseFulfilled(db, sid) {
  await run(db, "UPDATE purchases SET status='fulfilled', fulfilled_at=? WHERE stripe_session_id=?", nowIso(), sid);
}
export const listPurchases = (db, limit = 200) =>
  many(db, "SELECT * FROM purchases ORDER BY created_at DESC LIMIT ?", limit);
export async function purchaseCount24h(db) {
  return (await one(db, "SELECT COUNT(*) n FROM purchases WHERE status IN ('paid','fulfilled') AND created_at > ?",
    isoDaysAgo(1))).n;
}

// ---- delivery feed ----------------------------------------------
export async function activeForDelivery(db) {
  const subs = await many(db, "SELECT * FROM subscribers WHERE status IN ('active','paused')");
  return Promise.all(subs.map((s) => hydrateSub(db, s)));
}

// ---- DSAR ------------------------------------------------------
export async function exportSubscriber(db, id) {
  const s = await get(db, id, false);
  if (!s) return {};
  return {
    subscriber: s,
    channels: await channelsFor(db, id),
    prefs: await getPrefs(db, id),
    consents: await consentsFor(db, id),
    deliveries: await deliveriesFor(db, id, 10000),
    events: await eventsFor(db, id, 10000),
    exported_at: nowIso(),
  };
}
export async function eraseSubscriber(db, id, reason = "DSAR erasure") {
  const s = await get(db, id, false) || {};
  await run(db, "DELETE FROM subscribers WHERE id = ?", id);
  await run(db, "DELETE FROM consents WHERE subscriber_id = ?", id);
  await run(db, "DELETE FROM events WHERE subscriber_id = ?", id);
  await run(db, "INSERT INTO events(subscriber_id, ts, kind, detail, ip) VALUES (NULL, ?, 'erased', ?, NULL)",
    nowIso(), JSON.stringify({ reason, tier: s.tier, had_stripe: !!s.stripe_customer_id }));
}

// ---- retention ------------------------------------------------
export async function purgeUnconfirmed(db, days) {
  const ids = (await many(db,
    "SELECT id FROM subscribers WHERE status IN ('pending_payment','unconfirmed') AND created_at < ?",
    isoDaysAgo(days))).map((r) => r.id);
  for (const id of ids) await eraseSubscriber(db, id, `retention: unconfirmed > ${days}d`);
  return ids.length;
}
export async function purgeCancelled(db, days) {
  const ids = (await many(db,
    "SELECT id FROM subscribers WHERE status='cancelled' AND COALESCE(cancelled_at, created_at) < ?",
    isoDaysAgo(days))).map((r) => r.id);
  for (const id of ids) await eraseSubscriber(db, id, `retention: cancelled > ${days}d`);
  return ids.length;
}

// ---- stats ------------------------------------------------
export async function stats(db) {
  const rows = await many(db, "SELECT status, tier, cadence, COUNT(*) n FROM subscribers GROUP BY status, tier, cadence");
  const by_status = {}, active = [];
  for (const r of rows) {
    by_status[r.status] = (by_status[r.status] || 0) + r.n;
    if (["active", "paused", "past_due"].includes(r.status)) active.push(r);
  }
  const sent24 = (await one(db, "SELECT COUNT(*) n FROM deliveries WHERE ok=1 AND sent_at > ?", isoDaysAgo(1))).n;
  return { by_status, active, total_active: active.reduce((a, r) => a + r.n, 0), sent_24h: sent24 };
}

export { mask };
