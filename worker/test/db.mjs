// Data-layer unit tests — run db.js against an in-process SQLite via a tiny
// D1-shaped adapter (node:sqlite). No wrangler needed.
//   node worker/test/db.mjs
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import * as db from "../src/db.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const schema = readFileSync(join(HERE, "..", "schema.sql"), "utf8");

// --- D1 adapter over node:sqlite -----------------------------------------
class Stmt {
  constructor(sdb, sql, params = []) { this.sdb = sdb; this.sql = sql; this.params = params; }
  bind(...p) { return new Stmt(this.sdb, this.sql, p); }
  _s() { return this.sdb.prepare(this.sql); }
  async first(col) {
    const row = this._s().get(...this.params) ?? null;
    return col != null && row ? row[col] : row;
  }
  async all() { return { results: this._s().all(...this.params), success: true, meta: {} }; }
  async run() {
    const r = this._s().run(...this.params);
    return { success: true, meta: { changes: r.changes, last_row_id: Number(r.lastInsertRowid) } };
  }
}
class D1 { constructor(sdb) { this.sdb = sdb; } prepare(sql) { return new Stmt(this.sdb, sql); } }

const sdb = new DatabaseSync(":memory:");
sdb.exec(schema);
const D = new D1(sdb);
const backdate = (id, days) =>
  sdb.prepare("UPDATE subscribers SET created_at=? WHERE id=?")
    .run(new Date(Date.now() - days * 86400000).toISOString(), id);
const setCol = (id, col, val) => sdb.prepare(`UPDATE subscribers SET ${col}=? WHERE id=?`).run(val, id);

let failed = 0;
const ok = (n, c) => { console.log(`  [${c ? "ok  " : "FAIL"}] ${n}`); if (!c) failed++; };

// --- schema + createPending --------------------------------------------
const tables = sdb.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map((r) => r.name);
ok("schema applies", ["subscribers", "channels", "prefs", "consents", "deliveries", "signup_intents", "purchases"]
  .every((t) => tables.includes(t)));

const id1 = await db.createPending(D, { email: "  Free@Example.com ", tier: "individual", source: "stripe" });
const s1 = await db.get(D, id1);
ok("createPending lowercases + trims email", s1.email === "free@example.com");
ok("createPending starts pending_payment", s1.status === "pending_payment");
ok("createPending auto-adds email channel + prefs", s1.channels.some((c) => c.kind === "email") && !!s1.prefs);

// --- purgeUnconfirmed: never deletes a paying subscriber ---------------
const idFree = await db.createPending(D, { email: "lapsed-free@example.com", tier: "individual", source: "stripe" });
const idPaidCus = await db.createPending(D, { email: "paid-cus@example.com", tier: "team", source: "stripe" });
const idPaidSub = await db.createPending(D, { email: "paid-sub@example.com", tier: "team", source: "stripe" });
const idKeep = await db.createPending(D, { email: "securitycyber_cti@securitycyber.uk", tier: "team", source: "stripe" });
setCol(idPaidCus, "stripe_customer_id", "cus_live_1");
setCol(idPaidSub, "stripe_subscription_id", "sub_live_1");
for (const i of [idFree, idPaidCus, idPaidSub, idKeep]) { await db.setStatus(D, i, "unconfirmed", "test"); backdate(i, 30); }

const purged = await db.purgeUnconfirmed(D, 7, ["securitycyber_cti@securitycyber.uk"]);
ok("purgeUnconfirmed removed the never-paid sign-up", purged === 1 && !(await db.get(D, idFree)));
ok("purgeUnconfirmed KEPT the sub with a Stripe customer id", !!(await db.get(D, idPaidCus)));
ok("purgeUnconfirmed KEPT the sub with a Stripe subscription id", !!(await db.get(D, idPaidSub)));
ok("purgeUnconfirmed KEPT the forever-free address", !!(await db.get(D, idKeep)));

const stuck = await db.paidUnconfirmed(D);
const stuckEmails = stuck.map((r) => r.email).sort();
ok("paidUnconfirmed lists exactly the paid-but-unconfirmed accounts",
  JSON.stringify(stuckEmails) === JSON.stringify(["paid-cus@example.com", "paid-sub@example.com"]));

// --- activeForDelivery: active + paused + past_due, nothing else -------
const mk = async (email, status) => {
  const i = await db.createPending(D, { email, tier: "team", source: "stripe" });
  await db.setStatus(D, i, status, "test");
  return i;
};
await mk("d-active@example.com", "active");
await mk("d-paused@example.com", "paused");
await mk("d-pastdue@example.com", "past_due");
await mk("d-cancelled@example.com", "cancelled");
const forDelivery = (await db.activeForDelivery(D)).map((s) => s.status).sort();
ok("activeForDelivery returns active/paused/past_due only",
  JSON.stringify([...new Set(forDelivery)].sort()) === JSON.stringify(["active", "past_due", "paused"]));
ok("activeForDelivery excludes cancelled + unconfirmed",
  !forDelivery.includes("cancelled") && !forDelivery.includes("unconfirmed"));

// --- plan term: expiredTerms / staleStripe ----------------------------
const past = new Date(Date.now() - 86400000).toISOString();
const future = new Date(Date.now() + 5 * 86400000).toISOString();

const idPromoDue = await db.createPending(D, { email: "promo-due@example.com", tier: "free", source: "promo" });
const idPromoLive = await db.createPending(D, { email: "promo-live@example.com", tier: "free", source: "promo" });
const idPromoPaid = await db.createPending(D, { email: "promo-paid@example.com", tier: "free", source: "promo" });
const idPromoKeep = await db.createPending(D, { email: "securitycyber_cti@securitycyber.uk", tier: "free", source: "promo" });
for (const i of [idPromoDue, idPromoLive, idPromoPaid, idPromoKeep]) await db.setStatus(D, i, "active", "test");
await db.setTermEnd(D, idPromoDue, past);
await db.setTermEnd(D, idPromoLive, future);
await db.setTermEnd(D, idPromoPaid, past);
setCol(idPromoPaid, "stripe_subscription_id", "sub_promo_1");   // converted to paid mid-trial

const expired = (await db.expiredTerms(D, ["securitycyber_cti@securitycyber.uk"])).map((r) => r.email).sort();
ok("expiredTerms returns only the lapsed, non-Stripe, non-forever-free promo",
  JSON.stringify(expired) === JSON.stringify(["promo-due@example.com"]));
await db.setTermEnd(D, idPromoPaid, null);   // conversion to paid drops the promo clock

const idStaleStripe = await db.createPending(D, { email: "stale-stripe@example.com", tier: "individual", source: "stripe" });
const idFreshStripe = await db.createPending(D, { email: "fresh-stripe@example.com", tier: "individual", source: "stripe" });
for (const i of [idStaleStripe, idFreshStripe]) await db.setStatus(D, i, "active", "test");
setCol(idStaleStripe, "stripe_subscription_id", "sub_stale_1");
setCol(idFreshStripe, "stripe_subscription_id", "sub_fresh_1");
setCol(idStaleStripe, "current_period_end", Math.floor(Date.now() / 1000) - 4 * 86400);
setCol(idFreshStripe, "current_period_end", Math.floor(Date.now() / 1000) + 4 * 86400);
const stale = (await db.staleStripe(D, 2, 50)).map((r) => r.email);
ok("staleStripe flags the overdue Stripe sub, not the fresh one",
  stale.includes("stale-stripe@example.com") && !stale.includes("fresh-stripe@example.com"));

const promoStats = await db.stats(D);
ok("stats counts live promo terms + those expiring within 3 days",
  promoStats.promo_terms === 2 && promoStats.promo_expiring_3d === 1);

console.log(failed ? `\n❌ ${failed} failed` : "\n✅ db unit checks passed");
process.exit(failed ? 1 : 0);
