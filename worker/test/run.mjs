// Pure-logic unit checks — run with plain Node (has global Web Crypto).
//   node worker/test/run.mjs
import { sign, verify, newId, BadToken, timingSafeEqual } from "../src/tokens.js";
import { config, priceToTier, priceId, monthlyPence, problems } from "../src/config.js";
import { verifyTarget, teaser, channelAvailability } from "../src/channels.js";
import { landing, subscribeForm } from "../src/pages.js";
import * as billing from "../src/billing.js";
import { mdToHtml, esc } from "../src/util.js";
import { legalHtml } from "../src/legal.js";

let failed = 0;
const ok = (name, cond) => { console.log(`  [${cond ? "ok  " : "FAIL"}] ${name}`); if (!cond) failed++; };

const env = { SUBS_TOKEN_SECRET: "test-secret-000000000000000000", STRIPE_WEBHOOK_SECRET: "whsec_test" };

// ---- tokens ----
const t = await sign(env, { sub: 7, act: "manage" }, 60);
const d = await verify(env, t, "manage");
ok("token round-trips", d.sub === 7);
try { await verify(env, t, "confirm"); ok("action enforced", false); } catch (e) { ok("action enforced", e instanceof BadToken); }
try { await verify(env, t.slice(0, -2) + "xx", "manage"); ok("tamper rejected", false); } catch (e) { ok("tamper rejected", e instanceof BadToken); }
try { await verify(env, await sign(env, { a: 1 }, -10)); ok("expiry enforced", false); } catch (e) { ok("expiry enforced", e instanceof BadToken); }
ok("newId is url-safe + long", /^[A-Za-z0-9_-]{20,}$/.test(newId()));
ok("timingSafeEqual", timingSafeEqual("abc", "abc") && !timingSafeEqual("abc", "abd") && !timingSafeEqual("abc", "ab"));

// ---- config ----
const cenv = { CONFIG: JSON.stringify({ tiers: { individual: { monthly_price_id: "price_im", weekly_price_id: "price_iw", weekly_pence: 200 }, team: { monthly_price_id: "price_tm" } } }), BRAND: "SecurityCyber", SITE_URL: "https://cti.securitycyber.uk/", FROM_EMAIL: "x@y.z", CONTACT_EMAIL: "x@y.z", COMPANY_LEGAL_NAME: "Test Co", ICO_REG: "ZA1" };
const c = config(cenv);
ok("config merges tiers", c.tiers.individual.monthly_price_id === "price_im" && c.tiers.team.monthly_display === "£18 / month");
ok("site_url trailing slash stripped", c.site_url === "https://cti.securitycyber.uk");
const p2t = priceToTier(c);
ok("priceToTier maps all prices", p2t.price_im[0] === "individual" && p2t.price_iw[1] === "weekly" && p2t.price_tm[0] === "team");
ok("priceId lookup", priceId(c, "individual", "weekly") === "price_iw");
ok("monthlyPence normalises weekly", monthlyPence(c, "individual", "weekly") === Math.round(200 * 52 / 12));
ok("problems() clean when configured", problems(c, { RESEND_API_KEY: "re_x", STRIPE_SECRET_KEY: "sk_test_x" }).length === 0);
ok("problems() flags missing resend", problems(c, {}).some((x) => /RESEND/.test(x)));
const cNoIco = config({ ...cenv, ICO_REG: "" });
ok("problems() does NOT block test-mode deploy on missing ICO",
  problems(cNoIco, { RESEND_API_KEY: "re_x", STRIPE_SECRET_KEY: "sk_test_x" }).length === 0);
ok("problems() DOES block go-live on missing ICO",
  problems(config({ ...cenv, ICO_REG: "", CONFIG: JSON.stringify({ ...JSON.parse(cenv.CONFIG), live: true }) }),
    { RESEND_API_KEY: "re_x", STRIPE_SECRET_KEY: "sk_live_x", STRIPE_WEBHOOK_SECRET: "whsec_x" })
    .some((x) => /ICO/.test(x)));

// ---- storefront renders WITHOUT Stripe price ids (they auto-create) ----
// Regression: the pricing + cadence options used to be gated on *_price_id,
// so with blank ids the subscribe form rendered zero purchase options.
const cNoPrice = config({ ...cenv, CONFIG: JSON.stringify({ tiers: {} }) });
const landHtml = landing(cNoPrice);
ok("landing shows monthly AND weekly price", landHtml.includes("£8 / month") && landHtml.includes("£2 / week"));
ok("landing shows the team tier + one-off brief", landHtml.includes("£18 / month") && landHtml.includes("£49"));
const subHtml = subscribeForm(cNoPrice, "individual", "", "", channelAvailability({ RESEND_API_KEY: "re_x" }, cNoPrice));
ok("subscribe form offers both cadences", /value="monthly"/.test(subHtml) && /value="weekly"/.test(subHtml));
ok("subscribe form has a submit path", subHtml.includes('action="/subscribe"'));

// ---- channel availability (graceful degradation) ----
const availNone = channelAvailability({}, cNoPrice);
ok("email unavailable without Resend", !availNone.email.ok && /email/i.test(availNone.email.why));
ok("telegram unavailable without a bot", !availNone.telegram.ok);
ok("discord always available (subscriber supplies the webhook)", availNone.discord.ok);
const availOn = channelAvailability({ RESEND_API_KEY: "re_x", TELEGRAM_BOT_TOKEN: "t", TELEGRAM_BOT_USERNAME: "b" }, cNoPrice);
ok("email + telegram available once configured", availOn.email.ok && availOn.telegram.ok);
ok("CONFIG.channels_disabled forces a kind off",
  !channelAvailability({ RESEND_API_KEY: "re_x" }, config({ ...cenv, CONFIG: JSON.stringify({ channels_disabled: ["slack"] }) })).slack.ok);
ok("unavailable channels render disabled on the form",
  subscribeForm(cNoPrice, "individual", "", "", availNone).includes("unavailable"));

// ---- channels ----
ok("verifyTarget discord ok", verifyTarget("discord", "https://discord.com/api/webhooks/123/abc")[0]);
ok("verifyTarget discord bad", !verifyTarget("discord", "https://evil.com/x")[0]);
ok("verifyTarget slack", verifyTarget("slack", "https://hooks.slack.com/services/x")[0]);
ok("verifyTarget email", verifyTarget("email", "a@b.co")[0] && !verifyTarget("email", "nope")[0]);
ok("verifyTarget telegram numeric", verifyTarget("telegram", "12345")[0] && !verifyTarget("telegram", "abc")[0]);
const long = "If you run X, patch now. ".repeat(20) + "https://example.com/story";
const tz = teaser(long, 280, "");
ok("teaser fits + keeps url", tz.length <= 280 && tz.includes("https://example.com/story"));
ok("teaser prefers short_text", teaser(long, 280, "short version https://ex.co") === "short version https://ex.co");

// ---- billing ----
const enc = billing._formEncode({ a: { b: "c" }, items: [{ price: "p", quantity: 1 }], ok: true });
ok("stripe form encoding", enc.includes("a%5Bb%5D=c") && enc.includes("items%5B0%5D%5Bprice%5D=p") && enc.includes("ok=true"));
ok("stripe recurring form encoding", billing._formEncode({ recurring: { interval: "week" } }) === "recurring%5Binterval%5D=week");
// ensureTierPrice short-circuits to the configured id (no network)
ok("ensureTierPrice uses configured price id",
  (await billing.ensureTierPrice({}, c, "individual", "weekly")) === "price_iw");
// verifyWebhook: sign a payload the way Stripe does
async function stripeSig(payload, secret) {
  const ts = Math.floor(Date.now() / 1000);
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  return `t=${ts},v1=` + [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
const body = JSON.stringify({ id: "evt_1", type: "ping" });
const good = await stripeSig(body, "whsec_test");
const ev = await billing.verifyWebhook(body, good, "whsec_test");
ok("stripe webhook verifies", ev.id === "evt_1");
try { await billing.verifyWebhook(body, await stripeSig(body, "wrong"), "whsec_test"); ok("stripe bad sig rejected", false); }
catch (e) { ok("stripe bad sig rejected", e instanceof billing.WebhookError); }
try { await billing.verifyWebhook(body, "garbage", "whsec_test"); ok("stripe malformed sig rejected", false); }
catch (e) { ok("stripe malformed sig rejected", e instanceof billing.WebhookError); }
// one-off checkout payload shape (blueprint: line_items[{price,quantity}], mode:payment)
const briefEnc = billing._formEncode({
  mode: "payment", line_items: [{ price: "price_123", quantity: 1 }],
  success_url: "https://x/ok", cancel_url: "https://x/no", metadata: { item: "brief" },
});
ok("one-off checkout: mode=payment + line_items", briefEnc.includes("mode=payment")
  && briefEnc.includes("line_items%5B0%5D%5Bprice%5D=price_123")
  && briefEnc.includes("line_items%5B0%5D%5Bquantity%5D=1")
  && briefEnc.includes("metadata%5Bitem%5D=brief"));

// ---- markdown / legal ----
ok("mdToHtml headings+links", mdToHtml("# Hi\n[x](https://y.z)").includes("<h1>Hi</h1>") && mdToHtml("[x](https://y.z)").includes('href="https://y.z"'));
const L = legalHtml(c, "privacy");
ok("legal renders + fills tokens", L.html.includes("Test Co") && L.html.includes("ZA1") && !L.html.includes("{{"));
ok("legal unknown -> null", legalHtml(c, "nope") === null);

console.log(failed ? `\n❌ ${failed} failed` : "\n✅ worker unit checks passed");
process.exit(failed ? 1 : 0);
