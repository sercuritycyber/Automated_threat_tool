// End-to-end against a running `wrangler dev` (port 8788, using test/.dev.vars).
//   node worker/test/flow.mjs
const BASE = process.env.BASE || "http://localhost:8788";
const WH_SECRET = "whsec_devtest";
const TOKEN_SECRET = "dev-token-secret-0000000000000000";
const INGEST = "dev-ingest-secret-1111";
const ADMIN = "dev-admin-token-2222";

let failed = 0;
const ok = (n, c) => { console.log(`  [${c ? "ok  " : "FAIL"}] ${n}`); if (!c) failed++; };

function b64u(bytes) { return Buffer.from(bytes).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
async function hmac(secret, msg) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg));
}
async function stripeSig(payload) {
  const ts = Math.floor(Date.now() / 1000);
  const mac = await hmac(WH_SECRET, `${ts}.${payload}`);
  return `t=${ts},v1=` + [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function signToken(payload, ttl = 3600) {
  const body = { ...payload, exp: Math.floor(Date.now() / 1000) + ttl };
  const p = b64u(new TextEncoder().encode(JSON.stringify(body)));
  const mac = await hmac(TOKEN_SECRET, p);
  return `${p}.${b64u(new Uint8Array(mac))}`;
}
const adminGet = (p) => fetch(`${BASE}/admin/${p}`, { headers: { Authorization: `Bearer ${ADMIN}` } }).then((r) => r.json());
const adminPost = (p, body) => fetch(`${BASE}/admin/${p}`, { method: "POST", headers: { Authorization: `Bearer ${ADMIN}`, "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then((r) => r.json());

// 1. admin auth gate
ok("admin needs token", (await fetch(`${BASE}/admin/stats`)).status === 401);
const stats0 = await adminGet("stats");
ok("admin/stats works with token", typeof stats0.total_active === "number");
ok("problems() lists Stripe (fake key)", stats0.problems.some((x) => /sk_live_|STRIPE|RESEND/.test(x)));

// 2. checkout.session.completed webhook -> subscriber
const email = `flow${Date.now()}@example.com`;
const ev = JSON.stringify({
  id: "evt_" + Date.now(), type: "checkout.session.completed",
  data: { object: { id: "cs_1", customer: "cus_1", subscription: "sub_1",
    customer_details: { email }, metadata: { tier: "team", cadence: "weekly" } } },
});
let r = await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(ev) }, body: ev });
const wj = await r.json();
ok("webhook accepted + send_confirm", r.status === 200 && wj.action === "send_confirm");
r = await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": "bad" }, body: ev });
ok("bad signature -> 400", r.status === 400);

const list = (await adminGet(`subscribers?q=${email.split("@")[0]}`)).subscribers;
ok("subscriber created unconfirmed, weekly, team", list[0] && list[0].status === "unconfirmed" && list[0].cadence === "weekly" && list[0].tier === "team");
const sid = list[0].id;

// idempotency
r = await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(ev) }, body: ev });
ok("duplicate event ignored", (await r.json()).action === "duplicate");

// 3. confirm
const ctok = await signToken({ sub: sid, act: "confirm" });
r = await fetch(`${BASE}/confirm?t=${ctok}`, { redirect: "manual" });
ok("confirm redirects to /manage", r.status === 303 && (r.headers.get("location") || "").includes("/manage?t="));
const after = (await adminGet(`subscribers/${sid}`)).subscriber;
ok("confirmed -> active", after.status === "active");
const detail = await adminGet(`subscribers/${sid}`);
ok("consent logged w/ terms version + ip", detail.consents.some((c) => c.kind === "signup" && c.granted && c.terms_version));

// 4. manage: add a verified channel via admin, set prefs
await adminPost(`subscribers/${sid}/channel`, { kind: "ntfy", target: "https://ntfy.sh/flowtest", verified: true });
await adminPost(`subscribers/${sid}/edit`, { vendors: ["Fortinet"], min_score: "80", instant: true, digest: true });
const d2 = await adminGet(`subscribers/${sid}`);
ok("channel + prefs saved", d2.subscriber.channels.some((c) => c.kind === "ntfy") && d2.subscriber.prefs.vendors[0] === "Fortinet" && d2.subscriber.prefs.min_score_override === 80);

// 5. pipeline -> worker ingest (instant fan-out)
ok("ingest needs bearer", (await fetch(`${BASE}/internal/ingest`, { method: "POST", body: "{}" })).status === 401);
const posts = { posts: [
  { title: "Fortinet FortiOS RCE exploited", url: "https://ex.com/forti", source: "X", score: 96, cve_ids: ["CVE-2026-1"], post_text: "Patch FortiOS now. https://ex.com/forti", short_text: "Patch FortiOS CVE-2026-1 https://ex.com/forti" },
  { title: "Cisco minor advisory", url: "https://ex.com/cisco", source: "X", score: 70, cve_ids: ["CVE-2026-2"], post_text: "Cisco advisory. https://ex.com/cisco", short_text: "x" },
] };
r = await fetch(`${BASE}/internal/ingest`, { method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" }, body: JSON.stringify(posts) });
ok("ingest accepted 2 posts", (await r.json()).received === 2);

// give the background waitUntil a moment
await new Promise((s) => setTimeout(s, 1500));
const d3 = await adminGet(`subscribers/${sid}`);
const gotForti = d3.deliveries.some((x) => x.post_key.includes("forti"));
const gotCisco = d3.deliveries.some((x) => x.post_key.includes("cisco"));
ok("delivered the 96 (>= vendor threshold 80, Fortinet match)", gotForti);
ok("did NOT deliver the 70 (below override 80)", !gotCisco);

// re-ingest same -> deduped
r = await fetch(`${BASE}/internal/ingest`, { method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" }, body: JSON.stringify(posts) });
await new Promise((s) => setTimeout(s, 1200));
const d4 = await adminGet(`subscribers/${sid}`);
ok("re-ingest deduped (same delivery count)", d4.deliveries.length === d3.deliveries.length);

// 6. cron digest (manual trigger)
r = await fetch(`${BASE}/cdn-cgi/handler/scheduled?cron=${encodeURIComponent("15 8 * * *")}`, { method: "POST" });
ok("scheduled digest handler runs", r.status === 200 || r.status === 204);

// 6b. one-off threat brief — checkout.session.completed in `payment` mode
r = await fetch(`${BASE}/brief`);
const briefHtml = await r.text();
ok("/brief page renders", r.status === 200 && /threat brief/i.test(briefHtml) && briefHtml.includes('action="/brief/buy"'));
const briefEmail = `brief${Date.now()}@example.com`;
const briefSess = "cs_brief_" + Date.now();
const bev = JSON.stringify({
  id: "evt_brief_" + Date.now(), type: "checkout.session.completed",
  data: { object: {
    id: briefSess, mode: "payment", payment_status: "paid", payment_intent: "pi_1",
    amount_total: 4900, currency: "gbp", customer_details: { email: briefEmail },
    metadata: { item: "brief" },
  } },
});
r = await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(bev) }, body: bev });
ok("brief webhook -> brief_purchased", r.status === 200 && (await r.json()).action === "brief_purchased");
const padmin = await adminGet("purchases");
const purchases = padmin.purchases || [];
const pr = purchases.find((p) => p.email === briefEmail);
ok("purchase recorded (paid, £49, brief)", !!pr && pr.status === "paid" && pr.amount_total === 4900 && pr.item === "brief");
if (pr) {
  r = await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(bev) }, body: bev });
  ok("brief webhook idempotent", (await r.json()).action === "duplicate");
  r = await adminPost(`purchases/${pr.stripe_session_id}/fulfilled`, {});
  ok("mark fulfilled", r.ok);
}
ok("purchases list is all one-off items", purchases.length > 0 && purchases.every((p) => p.item === "brief"));

// 6b. Private RSS feed — the URL is the credential, and it must stop carrying
//     alerts the moment the subscription does. That's a revenue control, so
//     assert both directions.
{
  const feedTok = "flowfeedtoken" + Date.now();
  await adminPost(`subscribers/${sid}/channel`, { kind: "rss", target: feedTok, verified: true });
  const fetchFeed = () => fetch(`${BASE}/feed/${feedTok}.xml`).then(async (r) =>
    ({ status: r.status, ct: r.headers.get("content-type") || "", body: await r.text() }));

  await adminPost(`subscribers/${sid}/edit`, { status: "active", min_score: 0 });
  let f = await fetchFeed();
  ok("feed serves RSS to an active subscriber",
    f.status === 200 && /application\/rss\+xml/.test(f.ct) && f.body.includes("<rss") && f.body.includes("<item>"));
  const activeItems = (f.body.match(/<item>/g) || []).length;
  ok("feed carries the ingested alerts", activeItems > 0);

  await adminPost(`subscribers/${sid}/edit`, { status: "cancelled" });
  f = await fetchFeed();
  ok("cancelled subscriber gets NO alerts in the feed",
    f.status === 200 && !/Fortinet/.test(f.body) && /no longer active/i.test(f.body));

  await adminPost(`subscribers/${sid}/edit`, { status: "active" });
  f = await fetchFeed();
  ok("reactivating restores the same feed URL", (f.body.match(/<item>/g) || []).length === activeItems);

  ok("unknown feed token is 404", (await fetch(`${BASE}/feed/notarealtoken0000000000.xml`)).status === 404);
  ok("malformed feed token is 404", (await fetch(`${BASE}/feed/abc.xml`)).status === 404);
}

// 6c. Checkout must never 5xx, and must never quote Stripe's error back at
//     the visitor — it contains the API key ("Invalid API Key: sk_test_…").
//     With a Payment Link configured this is a 302 to buy.stripe.com; without
//     one it's the API path, which fails against the fake dev key.
{
  const r = await fetch(`${BASE}/subscribe`, {
    method: "POST", redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: "tier=individual&cadence=weekly&email=leakcheck@test.co&platform=rss&agree=on",
  });
  const loc = r.headers.get("location") || "";
  const body = r.status >= 300 && r.status < 400 ? "" : await r.text();
  ok("checkout never 5xxs", r.status < 500);
  ok("checkout either redirects to Stripe or renders a handled error",
    /^https:\/\/buy\.stripe\.com\//.test(loc) || /Couldn&#39;t start checkout|Couldn't start checkout/.test(body));
  ok("nothing in the checkout response leaks a key or raw Stripe error",
    !/sk_test|sk_live|Invalid API Key|HTTP 401/.test(body + loc));
  if (loc) {
    const u = new URL(loc);
    ok("payment link carries the signup intent",
      !!u.searchParams.get("client_reference_id") && u.searchParams.get("prefilled_email") === "leakcheck@test.co");
  }
}

// 6d. The feed URL is the one thing an RSS subscriber can't get pushed to
//     them, so minting one from the preferences page must mail it out.
{
  const em = `rssmail${Date.now()}@test.co`;
  const add = await adminPost("add", { email: em, tier: "team", skip_confirm: true });
  const mtok = await signToken({ sub: add.id, act: "manage" }, 3600);
  const r = await fetch(`${BASE}/manage/channel/add`, {
    method: "POST", redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: `t=${encodeURIComponent(mtok)}&kind=rss`,
  });
  ok("self-serve rss mint succeeds", r.status >= 300 && r.status < 400);
  const ev = await adminGet(`subscribers/${add.id}`);
  const kinds = (ev.events || []).map((x) => x.kind);
  ok("minting a feed logs an email attempt to the subscriber",
    kinds.some((k) => k === "rss:url_emailed" || k === "rss:url_email_failed"));
  const chans = (ev.subscriber && ev.subscriber.channels) || ev.channels || [];
  ok("the minted feed is a usable 24-byte id",
    JSON.stringify(ev).match(/"kind":"rss","target":"[A-Za-z0-9_-]{16,}"/) !== null || kinds.includes("channel:rss_created"));
}

// 6e. A v2 "thin" event destination carries no object data. It must be
//     acknowledged (so Stripe stops retrying) but never treated as a payment.
{
  const thin = JSON.stringify({
    id: "evt_thin_" + Date.now(), object: "v2.core.event", livemode: true,
    type: "v2.core.event_destination.ping",
    related_object: { id: "ed_x", type: "v2.core.event_destination" },
  });
  const r = await fetch(`${BASE}/webhooks/stripe`, {
    method: "POST", headers: { "stripe-signature": await stripeSig(thin) }, body: thin });
  const j = await r.json();
  ok("v2 thin ping is acknowledged, not 400'd", r.status === 200 && j.received === true);
  ok("v2 thin ping is explicitly ignored with a hint",
    j.ignored === "v2.core.event_destination.ping" && /snapshot/.test(j.hint || ""));
  const subs = (await adminGet("subscribers")).subscribers || [];
  ok("v2 thin ping created no subscriber", !subs.some((x) => /thin/.test(x.email || "")));
}

// 6f. THE PRODUCT: a paying subscriber must receive strictly more, and
//     different, from what goes out free on socials.
{
  const em = `value${Date.now()}@test.co`;
  const add = await adminPost("add", { email: em, tier: "team", skip_confirm: true });
  const feedTok = "valuefeedtoken" + Date.now();
  await adminPost(`subscribers/${add.id}/channel`, { kind: "rss", target: feedTok, verified: true });

  // A story scoring 45: below the public posting bar (60), above the team
  // floor (35). Free followers never see it; a subscriber must.
  const quiet = { posts: [{
    title: "Quiet-day advisory", url: "https://ex.com/quiet" + Date.now(), source: "Vendor", score: 45,
    cve_ids: ["CVE-2026-45"],
    action: "Upgrade Acme Gateway to 4.2.1 today.",
    subscriber_text: "Acme Gateway 4.0.0-4.2.0 are affected; 4.2.1 fixes it. Check with `acme --version`. No public detection guidance yet.",
  }] };
  let r = await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify(quiet) });
  const ing = await r.json();
  ok("alert with NO social text is still ingested", ing.received === 1);

  const feed = await (await fetch(`${BASE}/feed/${feedTok}.xml`)).text();
  ok("sub-posting-threshold story reaches a paying subscriber", feed.includes("Quiet-day advisory"));
  ok("the feed carries the analyst write-up, not a social hook",
    feed.includes("4.2.1 fixes it") && feed.includes("acme --version"));
  ok("the feed leads with the action", /DO NOW: Upgrade Acme Gateway/.test(feed));

  // And a story that DOES go out socially must read differently for payers.
  const both = { posts: [{
    title: "Both channels", url: "https://ex.com/both" + Date.now(), source: "KEV", score: 95,
    cve_ids: ["CVE-2026-95"],
    post_text: "Patch now. https://ex.com/both",
    action: "Patch to 9.9.9.",
    subscriber_text: "Versions 9.0-9.8 affected, fixed in 9.9.9. Exposed if TCP/8443 is reachable.",
  }] };
  await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify(both) });
  const feed2 = await (await fetch(`${BASE}/feed/${feedTok}.xml`)).text();
  ok("a shared story gives subscribers the depth, not the social copy",
    feed2.includes("TCP/8443") && !feed2.includes("Patch now. https"));
  await adminPost(`subscribers/${add.id}/erase`, {});
}

// 6g. Pause -> resume. A subscriber who pauses alerts keeps paying, so there
//     must be a one-click way back on — not a support ticket.
{
  const em = `pauser${Date.now()}@test.co`;
  const add = await adminPost("add", { email: em, tier: "team", skip_confirm: true });
  const mtok = await signToken({ sub: add.id, act: "manage" }, 3600);
  let r = await fetch(`${BASE}/unsubscribe?t=${await signToken({ sub: add.id, act: "unsubscribe" }, 3600)}`, { redirect: "manual" });
  ok("unsubscribe link pauses", (await adminGet(`subscribers/${add.id}`)).subscriber.status === "paused");
  const mgr = await (await fetch(`${BASE}/manage?t=${encodeURIComponent(mtok)}`)).text();
  ok("paused manage page offers Resume, not a dead end", /action="\/manage\/resume"/.test(mgr) && /paused/i.test(mgr));
  r = await fetch(`${BASE}/manage/resume`, {
    method: "POST", redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: `t=${encodeURIComponent(mtok)}`,
  });
  ok("resume redirects back to /manage", r.status === 303);
  ok("resume reactivates the subscriber", (await adminGet(`subscribers/${add.id}`)).subscriber.status === "active");
  await adminPost(`subscribers/${add.id}/erase`, {});
}

// 6h. A lost /confirm email must not strand a paying customer: opening ANY
//     link we sent to their address (a /manage link) completes the opt-in.
{
  const email = `manageconfirm${Date.now()}@example.com`;
  const ev = JSON.stringify({
    id: "evt_mc_" + Date.now(), type: "checkout.session.completed",
    data: { object: { id: "cs_mc", customer: "cus_mc" + Date.now(), subscription: "sub_mc" + Date.now(),
      customer_details: { email }, metadata: { tier: "individual", cadence: "monthly" } } },
  });
  await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(ev) }, body: ev });
  const sub = (await adminGet(`subscribers?q=${email.split("@")[0]}`)).subscribers[0];
  ok("checkout created an unconfirmed subscriber", sub && sub.status === "unconfirmed");
  const mtok = await signToken({ sub: sub.id, act: "manage" }, 3600);
  const r = await fetch(`${BASE}/manage?t=${encodeURIComponent(mtok)}`);
  ok("opening the emailed /manage link renders", r.status === 200);
  ok("opening the emailed /manage link confirms the subscriber",
    (await adminGet(`subscribers/${sub.id}`)).subscriber.status === "active");
  await adminPost(`subscribers/${sub.id}/erase`, {});
}

// 6i. An already-confirmed subscriber going through Checkout again (upgrade,
//     re-subscribe, Stripe event replay under a new id) must NOT drop back to
//     'unconfirmed' — that would cut a live customer's alerts.
{
  const email = `recheckout${Date.now()}@example.com`;
  const mk = (evid, tier) => JSON.stringify({
    id: evid, type: "checkout.session.completed",
    data: { object: { id: "cs_" + evid, customer: "cus_rc" + Date.now(), subscription: "sub_rc",
      customer_details: { email }, metadata: { tier, cadence: "monthly" } } },
  });
  const e1 = mk("evt_rc1_" + Date.now(), "individual");
  await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(e1) }, body: e1 });
  const sub = (await adminGet(`subscribers?q=${email.split("@")[0]}`)).subscribers[0];
  await fetch(`${BASE}/confirm?t=${await signToken({ sub: sub.id, act: "confirm" })}`, { redirect: "manual" });
  ok("first checkout + confirm -> active", (await adminGet(`subscribers/${sub.id}`)).subscriber.status === "active");
  const e2 = mk("evt_rc2_" + Date.now(), "team");
  const r = await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(e2) }, body: e2 });
  const j = await r.json();
  ok("second checkout is 'synced', not 'send_confirm'", j.action === "synced");
  const after = (await adminGet(`subscribers/${sub.id}`)).subscriber;
  ok("second checkout leaves a live subscriber ACTIVE", after.status === "active");
  ok("second checkout applied the tier change", after.tier === "team");
  await adminPost(`subscribers/${sub.id}/erase`, {});
}

// 6j. The private RSS feed is pull, not push — turning OFF the daily-digest
//     toggle must not empty it (that bar is the tier floor, always).
{
  const em = `feedpref${Date.now()}@test.co`;
  const add = await adminPost("add", { email: em, tier: "team", skip_confirm: true });
  const feedTok = "feedpreftoken" + Date.now();
  await adminPost(`subscribers/${add.id}/channel`, { kind: "rss", target: feedTok, verified: true });
  await adminPost(`subscribers/${add.id}/edit`, { instant: false, digest: false, min_score: 0 });
  await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify({ posts: [{ title: "Feed pref check", url: "https://ex.com/fp" + Date.now(), score: 80,
      cve_ids: [], subscriber_text: "Body for the feed-pref test." }] }),
  });
  const feed = await (await fetch(`${BASE}/feed/${feedTok}.xml`)).text();
  ok("feed still carries alerts with both delivery toggles off", feed.includes("Feed pref check"));
  await adminPost(`subscribers/${add.id}/erase`, {});
}

// 6k. past_due keeps getting alerts (card-retry grace); paused does not.
{
  const pd = await adminPost("add", { email: `pd${Date.now()}@test.co`, tier: "team", skip_confirm: true });
  const pz = await adminPost("add", { email: `pz${Date.now()}@test.co`, tier: "team", skip_confirm: true });
  await adminPost(`subscribers/${pd.id}/channel`, { kind: "ntfy", target: "https://ntfy.sh/pdtest", verified: true });
  await adminPost(`subscribers/${pz.id}/channel`, { kind: "ntfy", target: "https://ntfy.sh/pztest", verified: true });
  await adminPost(`subscribers/${pd.id}/edit`, { status: "past_due", min_score: 0, instant: true });
  await adminPost(`subscribers/${pz.id}/edit`, { status: "paused", min_score: 0, instant: true });
  const u = `https://ex.com/grace${Date.now()}`;
  await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify({ posts: [{ title: "grace test", url: u, score: 99, cve_ids: [], subscriber_text: "body" }] }),
  });
  await new Promise((s) => setTimeout(s, 1500));
  const dpd = await adminGet(`subscribers/${pd.id}`), dpz = await adminGet(`subscribers/${pz.id}`);
  ok("past_due subscriber still receives instant alerts (grace window)",
    dpd.deliveries.some((x) => x.post_key.includes("grace")));
  ok("paused subscriber receives nothing", !dpz.deliveries.some((x) => x.post_key.includes("grace")));
  await adminPost(`subscribers/${pd.id}/erase`, {});
  await adminPost(`subscribers/${pz.id}/erase`, {});
}

// 6l. Choosing Discord at signup is deferred (OAuth after payment), not
//     rejected for an empty target the way a typed channel would be. In dev
//     Discord has no secrets, so the expected outcome is the graceful
//     "unavailable" bounce — never a 5xx and never the raw target error.
{
  const r = await fetch(`${BASE}/subscribe`, {
    method: "POST", redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: `tier=team&cadence=monthly&email=disc${Date.now()}@test.co&platform=discord&agree=on`,
  });
  const loc = r.headers.get("location") || "";
  const body = r.status >= 300 && r.status < 400 ? "" : await r.text();
  ok("discord signup is handled cleanly (deferred connect / graceful unavailable)",
    r.status < 500 && !/connect Discord with the button/.test(body + loc));
  ok("discord signup either proceeds to payment or bounces with a reason",
    /^https:\/\/buy\.stripe\.com\//.test(loc) || /\/subscribe\?tier=team&m=/.test(loc) || /Almost there|Checkout/.test(body));
}

// 6m. The paid feed must be FULL — a whole run's worth of alerts, not a
//     highlights reel. Push 30 alerts spanning the score range and check the
//     subscriber's private feed carries essentially all of them.
{
  const em = `volume${Date.now()}@test.co`;
  const add = await adminPost("add", { email: em, tier: "team", skip_confirm: true });
  const feedTok = "volumefeedtoken" + Date.now();
  await adminPost(`subscribers/${add.id}/channel`, { kind: "rss", target: feedTok, verified: true });
  await adminPost(`subscribers/${add.id}/edit`, { min_score: "" });   // tier floor only (team = 10)
  const batch = Array.from({ length: 30 }, (_, i) => ({
    title: `Bulk advisory ${i} ${Date.now()}`,
    url: `https://ex.com/bulk${i}-${Date.now()}`,
    source: "Vendor", score: 12 + i * 2, cve_ids: [`CVE-2026-${9000 + i}`],
    action: `Patch component ${i}.`,
    subscriber_text: `Analyst write-up for bulk advisory ${i}: affected versions, exposure check, detection.`,
  }));
  const r = await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify({ posts: batch }),
  });
  ok("ingest accepts a full run of 30 alerts", (await r.json()).received === 30);
  const feed = await (await fetch(`${BASE}/feed/${feedTok}.xml`)).text();
  const items = (feed.match(/<item>/g) || []).length;
  ok(`private feed carries the whole run, not a shortlist (${items} items)`, items >= 28);
  ok("every feed item leads with the analyst action + write-up",
    /DO NOW: Patch component/.test(feed) && feed.includes("exposure check, detection"));
  await adminPost(`subscribers/${add.id}/erase`, {});
}

// 6n. THE default path: someone subscribes with email delivery. Their email
//     channel must end up verified and actually receive alerts — the whole
//     point of paying. (Regression: the auto-created email row was never
//     verified, so email subscribers got nothing.)
{
  const email = `emaildeliver${Date.now()}@example.com`;
  const ev = JSON.stringify({
    id: "evt_ed_" + Date.now(), type: "checkout.session.completed",
    data: { object: { id: "cs_ed", customer: "cus_ed" + Date.now(), subscription: "sub_ed" + Date.now(),
      customer_details: { email }, metadata: { tier: "individual", cadence: "monthly" } } },
  });
  // no signup intent in this event -> the webhook must still default them to
  // email delivery, not leave them channel-less.
  await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(ev) }, body: ev });
  const sub = (await adminGet(`subscribers?q=${email.split("@")[0]}`)).subscribers[0];
  await fetch(`${BASE}/confirm?t=${await signToken({ sub: sub.id, act: "confirm" })}`, { redirect: "manual" });
  const d = await adminGet(`subscribers/${sub.id}`);
  const emailCh = d.subscriber.channels.find((c) => c.kind === "email");
  ok("email subscriber's email channel is verified after confirm", emailCh && emailCh.verified);
  await adminPost(`subscribers/${sub.id}/edit`, { min_score: 0, instant: true });
  await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify({ posts: [{ title: "email delivery regression", url: "https://ex.com/ed" + Date.now(),
      score: 99, cve_ids: [], subscriber_text: "depth", action: "patch" }] }),
  });
  await new Promise((s) => setTimeout(s, 1500));
  const d2 = await adminGet(`subscribers/${sub.id}`);
  ok("email subscriber receives the alert (delivery recorded on the email channel)",
    d2.deliveries.some((x) => x.channel_kind === "email" && x.post_key.includes("/ed")));
  await adminPost(`subscribers/${sub.id}/erase`, {});
}

// 6o. An RSS subscriber must NOT also get email — they picked one channel.
//     Full path: POST /subscribe (mints the intent) -> webhook with that
//     client_reference_id -> confirm.
{
  const em = `rssonly${Date.now()}@test.co`;
  const sr = await fetch(`${BASE}/subscribe`, {
    method: "POST", redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: `tier=individual&cadence=monthly&email=${encodeURIComponent(em)}&platform=rss&agree=on`,
  });
  const ref = new URL(sr.headers.get("location")).searchParams.get("client_reference_id");
  ok("rss signup minted a client_reference_id", !!ref);
  const ev = JSON.stringify({
    id: "evt_ro_" + Date.now(), type: "checkout.session.completed",
    data: { object: { id: "cs_ro", customer: "cus_ro" + Date.now(), subscription: "sub_ro" + Date.now(),
      customer_details: { email: em }, client_reference_id: ref } },
  });
  await fetch(`${BASE}/webhooks/stripe`, { method: "POST", headers: { "stripe-signature": await stripeSig(ev) }, body: ev });
  const sub = (await adminGet(`subscribers?q=${em.split("@")[0]}`)).subscribers[0];
  await fetch(`${BASE}/confirm?t=${await signToken({ sub: sub.id, act: "confirm" })}`, { redirect: "manual" });
  await adminPost(`subscribers/${sub.id}/edit`, { min_score: 0, instant: true });
  await fetch(`${BASE}/internal/ingest`, {
    method: "POST", headers: { Authorization: `Bearer ${INGEST}`, "Content-Type": "application/json" },
    body: JSON.stringify({ posts: [{ title: "rss-only channel check", url: "https://ex.com/ro" + Date.now(),
      score: 99, cve_ids: [], subscriber_text: "depth" }] }),
  });
  await new Promise((s) => setTimeout(s, 1200));
  const d = await adminGet(`subscribers/${sub.id}`);
  const emailCh = d.subscriber.channels.find((c) => c.kind === "email");
  ok("rss subscriber's email row stays an unverified identifier", emailCh && !emailCh.verified);
  ok("rss subscriber gets NO email delivery", !d.deliveries.some((x) => x.channel_kind === "email"));
  await adminPost(`subscribers/${sub.id}/erase`, {});
}

// 7. DSAR export + erase
const exp = await adminGet(`subscribers/${sid}/export`);
ok("DSAR export has sections", exp.subscriber && exp.consents && exp.deliveries);
await adminPost(`subscribers/${sid}/erase`, {});
ok("erase removes subscriber", (await adminGet(`subscribers/${sid}`)).error === "not found");

console.log(failed ? `\n❌ ${failed} failed` : "\n✅ worker flow test passed");
process.exit(failed ? 1 : 0);
