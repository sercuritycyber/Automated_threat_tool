// Pure-logic unit checks — run with plain Node (has global Web Crypto).
//   node worker/test/run.mjs
import { sign, verify, newId, BadToken, timingSafeEqual } from "../src/tokens.js";
import { config, priceToTier, priceId, monthlyPence, problems } from "../src/config.js";
import { verifyTarget, teaser, channelAvailability } from "../src/channels.js";
import { landing, subscribeForm, feedXml } from "../src/pages.js";
import { alertEmailHtml, alertEmailText } from "../src/emailtpl.js";
import { discordConfigured } from "../src/discord.js";
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
// A "clean" env now needs the webhook + token secrets: without them people
// can pay and never be provisioned, which is a blocker, not a nicety.
const OKENV = { RESEND_API_KEY: "re_x", STRIPE_SECRET_KEY: "sk_test_x",
  STRIPE_WEBHOOK_SECRET: "whsec_x", SUBS_TOKEN_SECRET: "tok_x" };
ok("problems() clean when configured", problems(c, OKENV).length === 0);
ok("problems() flags missing resend", problems(c, {}).some((x) => /RESEND/.test(x)));
const cNoIco = config({ ...cenv, ICO_REG: "" });
ok("problems() does NOT block test-mode deploy on missing ICO",
  problems(cNoIco, OKENV).length === 0);
// A live Stripe key means real money and real customer data, whatever
// CONFIG.live says — so ICO becomes a blocker and nothing may claim "test mode".
{
  const { takingRealMoney, advisories } = await import("../src/config.js");
  const liveKey = { ...OKENV, STRIPE_SECRET_KEY: "sk_live_x" };
  ok("sk_live_ counts as taking real money even when CONFIG.live is false",
    takingRealMoney(cNoIco, liveKey) && !cNoIco.live);
  ok("sk_live_ + no ICO is a blocker",
    problems(cNoIco, liveKey).some((x) => /ICO/.test(x)));
  const adv = advisories(cNoIco, liveKey);
  ok("advisory warns about the live-key / CONFIG.live mismatch",
    adv.some((x) => /CAUTION/.test(x) && /sk_live_/.test(x)));
  ok("advisory never claims test mode while the key is live",
    !adv.some((x) => /TEST mode|no real charges/i.test(x)));
  ok("sk_test_ still reports as test",
    advisories(cNoIco, OKENV).some((x) => /sk_test_|no real charges/i.test(x)));
}
ok("problems() DOES block go-live on missing ICO",
  problems(config({ ...cenv, ICO_REG: "", CONFIG: JSON.stringify({ ...JSON.parse(cenv.CONFIG), live: true }) }),
    { ...OKENV, STRIPE_SECRET_KEY: "sk_live_x" })
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
// Pricing is decided on ONE page: the cards carry both cadences + a toggle,
// and the CTA passes the choice through to /subscribe.
ok("pricing has a monthly/weekly toggle", landHtml.includes("billtoggle") && landHtml.includes('data-cad=weekly'));
ok("buy links carry the chosen cadence", landHtml.includes("cadence=monthly"));
ok("subscribe preselects the cadence from the link",
  /value="weekly" checked/.test(subscribeForm(cNoPrice, "individual", "", "", null, "weekly"))
  && /value="monthly" checked/.test(subscribeForm(cNoPrice, "individual", "", "", null, "monthly")));

// ---- channel availability (graceful degradation) ----
const availNone = channelAvailability({}, cNoPrice);
ok("email unavailable without Resend", !availNone.email.ok && /email/i.test(availNone.email.why));
ok("telegram unavailable without a bot", !availNone.telegram.ok);
// Discord is now "join our server, get DMs" — it needs our app + bot, so it
// is unavailable until those secrets exist. The old push-to-your-own-server
// behaviour lives on as discord_webhook, which needs nothing from us.
ok("discord unavailable until our app+bot are configured", !availNone.discord.ok);
ok("discord available once the app+bot exist",
  channelAvailability({ DISCORD_CLIENT_ID: "1", DISCORD_CLIENT_SECRET: "2",
    DISCORD_BOT_TOKEN: "3", DISCORD_GUILD_ID: "4" }, cNoPrice).discord.ok);
ok("discord_webhook needs nothing from us", availNone.discord_webhook.ok);
const availOn = channelAvailability({ RESEND_API_KEY: "re_x", TELEGRAM_BOT_TOKEN: "t", TELEGRAM_BOT_USERNAME: "b" }, cNoPrice);
ok("email + telegram available once configured", availOn.email.ok && availOn.telegram.ok);
ok("CONFIG.channels_disabled forces a kind off",
  !channelAvailability({ RESEND_API_KEY: "re_x" }, config({ ...cenv, CONFIG: JSON.stringify({ channels_disabled: ["slack"] }) })).slack.ok);
ok("unavailable channels render disabled on the form",
  subscribeForm(cNoPrice, "individual", "", "", availNone).includes("unavailable"));

// ---- Stripe Payment Links ----
{
  const { paymentLink, paymentLinkUrl, canCheckout, buyableMap } = await import("../src/config.js");
  const LINK = "https://buy.stripe.com/aFa3cu6hVdlLf7lbhH3ks01";
  ok("payment link is read from the tier", paymentLink(cNoPrice, "individual", "monthly") === LINK);
  // client_reference_id is how the webhook recovers tier/cadence/channel.
  const u = new URL(paymentLinkUrl(LINK, { clientRef: "intent_abc", email: "a@b.co" }));
  ok("link carries client_reference_id", u.searchParams.get("client_reference_id") === "intent_abc");
  ok("link prefills the email", u.searchParams.get("prefilled_email") === "a@b.co");
  ok("link keeps its original path", u.origin + u.pathname === LINK);
  ok("no link -> no url", paymentLinkUrl("", { clientRef: "x" }) === "");
  ok("garbage link doesn't throw", paymentLinkUrl("not a url", { clientRef: "x" }) === "");

  // link_only: a cadence without a link is NOT sellable even with a secret key
  const lo = config({ ...cenv, CONFIG: JSON.stringify({ checkout_mode: "link_only" }) });
  ok("link_only sells a cadence that has a link", canCheckout(lo, {}, "individual", "monthly"));
  ok("link_only refuses team weekly (no link yet)", !canCheckout(lo, { STRIPE_SECRET_KEY: "sk_test_x" }, "team", "weekly"));
  const bm = buyableMap(lo, {});
  ok("buyableMap matches the configured links",
    bm.individual.monthly && bm.individual.weekly && bm.team.monthly && !bm.team.weekly);
  // auto mode still falls back to an API session
  const au = config({ ...cenv, CONFIG: JSON.stringify({ checkout_mode: "auto" }) });
  ok("auto mode falls back to the API when there's no link",
    canCheckout(au, { STRIPE_SECRET_KEY: "sk_test_x" }, "team", "weekly"));

  // the storefront must not advertise what it can't sell
  const loLanding = landing(lo, "", "", buyableMap(lo, {}));
  ok("pricing page shows the one weekly offer and no other",
    (loLanding.match(/data-cad=weekly>/g) || []).length === 1 && loLanding.includes("£2 / week"));
  ok("Team is monthly only — no weekly price anywhere",
    loLanding.includes("£18 / month") && !loLanding.includes("£5 / week"));
  ok("monthly-only card is tagged so the toggle can hide it",
    /data-cads="monthly"/.test(loLanding) && /data-cads="monthly weekly"/.test(loLanding));
  const loForm = subscribeForm(lo, "team", "", "", null, "", buyableMap(lo, {}));
  ok("Team signup form offers monthly only",
    /value="monthly"/.test(loForm) && !/value="weekly"/.test(loForm));
  const loForm2 = subscribeForm(lo, "individual", "", "", null, "", buyableMap(lo, {}));
  ok("Individual signup form still offers both",
    /value="monthly"/.test(loForm2) && /value="weekly"/.test(loForm2));

  // the webhook secret is now the single critical dependency
  ok("missing STRIPE_WEBHOOK_SECRET is a blocker",
    problems(lo, { RESEND_API_KEY: "re_x", SUBS_TOKEN_SECRET: "s" })
      .some((x) => /STRIPE_WEBHOOK_SECRET/.test(x)));
  ok("link_only does NOT demand STRIPE_SECRET_KEY",
    !problems(lo, { RESEND_API_KEY: "re_x", SUBS_TOKEN_SECRET: "s", STRIPE_WEBHOOK_SECRET: "whsec_x" })
      .some((x) => /STRIPE_SECRET_KEY/.test(x)));
}

// ---- HTML alert email ----
{
  const al = { title: "Acme <RCE> \"now\"", url: "https://ex.co/a?x=1&y=2", source: "CISA KEV feed",
    score: 96, cve_ids: ["CVE-2026-1"], action: "Patch to 4.2.1.",
    subscriber_text: "Line one.\n\nLine two with <tag> & ampersand." };
  const h = alertEmailHtml(cNoPrice, al, { manage: "https://x/manage?t=abc", unsubscribe: "https://x/u?t=abc" });
  ok("alert email is a full HTML doc", h.startsWith("<!doctype html>") && h.includes("</html>"));
  ok("alert email escapes hostile alert text",
    h.includes("Acme &lt;RCE&gt;") && h.includes("&amp; ampersand") && !/<tag>/.test(h));
  ok("alert email shows score, CVE, action and both links",
    h.includes("96/100") && h.includes("CVE-2026-1") && h.includes("Patch to 4.2.1")
    && h.includes("https://x/manage?t=abc") && h.includes("https://x/u?t=abc"));
  ok("alert email flags KEV from the source", h.includes("CISA KEV"));
  const t = alertEmailText(cNoPrice, al, { manage: "https://x/m" });
  ok("alert email has a plain-text part", t.includes("DO NOW: Patch to 4.2.1") && t.includes("Line two"));
}

// ---- Discord membership ----
ok("discord needs the app + bot to be configured",
  !discordConfigured({}) && discordConfigured({ DISCORD_CLIENT_ID: "1", DISCORD_CLIENT_SECRET: "2", DISCORD_BOT_TOKEN: "3", DISCORD_GUILD_ID: "4" }));

// ---- forever-free config ----
{
  const c1 = config({ ...cenv });
  ok("forever-free defaults to the CTI address",
    c1.forever_free_emails.includes("securitycyber_cti@securitycyber.uk"));
  const c2 = config({ ...cenv, FOREVER_FREE_EMAILS: "a@b.co, C@D.CO" });
  ok("FOREVER_FREE_EMAILS merges + lowercases",
    c2.forever_free_emails.includes("a@b.co") && c2.forever_free_emails.includes("c@d.co"));
}

// ---- private RSS feed ----
const feedPosts = [
  { post_key: "p1", title: "Fortinet <RCE> & \"chaos\"", url: "https://ex.co/a?x=1&y=2", source: "Vendor",
    score: 96, cve_ids: ["CVE-2026-1111"], post_text: "Patch now.", seen_at: "2026-09-02T10:00:00Z" },
  { post_key: "p2", title: "Second", url: "https://ex.co/b", source: "KEV", score: 80, cve_ids: [],
    post_text: "Body", seen_at: "2026-09-01T10:00:00Z" },
];
const fx = feedXml(cNoPrice, feedPosts, { selfUrl: "https://cti.example/feed/abc.xml" });
ok("feed is well-formed RSS 2.0", fx.startsWith("<?xml") && fx.includes('<rss version="2.0"') && fx.trimEnd().endsWith("</rss>"));
ok("feed has one item per post", (fx.match(/<item>/g) || []).length === 2);
// Every & must open a real entity — otherwise the reader sees malformed XML.
ok("feed escapes XML metacharacters",
  fx.includes("&lt;RCE&gt;") && fx.includes("&quot;chaos&quot;")
  && fx.includes("https://ex.co/a?x=1&amp;y=2")
  && !/&(?!(amp|lt|gt|quot|apos);)/.test(fx));
ok("feed carries guid, pubDate and self link",
  fx.includes('<guid isPermaLink="false">p1</guid>') && fx.includes("<pubDate>") && fx.includes('rel="self"'));
ok("feed exposes CVE categories + score", fx.includes("<category>CVE-2026-1111</category>") && fx.includes("96/100"));
ok("empty feed still valid", feedXml(cNoPrice, []).includes("</channel>"));
ok("rss is an offered channel", cNoPrice.tiers.individual.channels.includes("rss"));
ok("rss target accepts a minted id, rejects junk",
  verifyTarget("rss", "abcdefghijklmnopqrstuvwx")[0] && !verifyTarget("rss", "short")[0]);
ok("rss is always available (nothing to configure)", channelAvailability({}, cNoPrice).rss.ok);

// ---- channels ----
// discord targets are user-id snowflakes captured via OAuth, never typed
ok("verifyTarget discord takes a user id", verifyTarget("discord", "123456789012345678")[0]);
ok("verifyTarget discord rejects a pasted URL", !verifyTarget("discord", "https://discord.com/api/webhooks/1/a")[0]);
ok("verifyTarget discord_webhook ok", verifyTarget("discord_webhook", "https://discord.com/api/webhooks/123/abc")[0]);
ok("verifyTarget discord_webhook bad", !verifyTarget("discord_webhook", "https://evil.com/x")[0]);
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
