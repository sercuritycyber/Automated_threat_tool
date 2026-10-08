// HTML rendering — SecurityCyber brand (securitycyber.uk look).
import { esc } from "./util.js";

// Every channel carries a link back to the CTI page — LinkedIn/Discord render
// its OG card (our image), RSS readers show it as the feed logo.
export const PROMO_URL = "https://securitycyber.uk/cti";
export const PROMO_IMG = "https://securitycyber.uk/og-image.png";
import { monthlyPence, paymentLink, MOTTO } from "./config.js";

export const LOGO_SVG =
  '<svg viewBox="0 0 48 46" width="26" height="26" fill="none" aria-hidden="true">' +
  '<path fill="#3b82f6" d="M25.946 44.938c-.664.845-2.021.375-2.021-.698V33.937a2.26 2.26 0 0 0-2.262-2.262' +
  'H10.287c-.92 0-1.456-1.04-.92-1.788l7.48-10.471c1.07-1.497 0-3.578-1.842-3.578H1.237c-.92 0-1.456-1.04-.92-1.788' +
  'L10.013.474c.214-.297.556-.474.92-.474h28.894c.92 0 1.456 1.04.92 1.788l-7.48 10.471c-1.07 1.498 0 3.579 1.842 3.579' +
  'h11.377c.943 0 1.473 1.088.89 1.83L25.947 44.94z"/></svg>';

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;1,400&family=JetBrains+Mono:wght@400;500&display=swap');
:root{--bg:#0d1422;--bg2:#121c2e;--bg3:#0a1120;--line:#1e2d45;--line-dim:rgba(30,45,69,.8);--line-hi:rgba(59,130,246,.3);
--ink:#e0e8f5;--dim:#7d92b0;--faint:#3a4f6a;--white:#f0f6ff;--accent:#3b82f6;--accent-dark:#2563eb;--accent-dim:rgba(59,130,246,.12);
--brand:#dc2626;--good:#22c55e;--bad:#f87171;--display:'Syne',sans-serif;--body:'DM Sans',system-ui,sans-serif;--mono:'JetBrains Mono',monospace;
--radius:8px;--radius-lg:14px}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:15px/1.7 var(--body);-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none;transition:color .2s}a:hover{color:var(--accent-dark)}
.wrap{max-width:780px;margin:0 auto;padding:0 20px 72px}
nav{display:flex;align-items:center;justify-content:space-between;height:66px;margin-bottom:28px;border-bottom:1px solid var(--line-dim)}
nav .brand{display:flex;align-items:center;gap:9px;font-family:var(--display);font-weight:800;font-size:17px;color:var(--white);letter-spacing:-.01em}
nav .brand b span{color:var(--brand)}
nav .links a{font-size:.86rem;color:var(--dim);margin-left:18px}nav .links a:hover{color:var(--white)}
h1{font-family:var(--display);font-weight:800;font-size:31px;line-height:1.18;letter-spacing:-.02em;color:var(--white);margin:.4em 0}
h2{font-family:var(--display);font-weight:700;font-size:19px;color:var(--white);margin:1.8em 0 .6em}
h3{font-family:var(--display);font-weight:700;font-size:15px;color:var(--white);margin:0 0 .5em}
p{margin:.6em 0}.lede{font-size:17px;color:var(--dim);line-height:1.6}.muted{color:var(--dim);font-size:14px}
.card{background:var(--bg2);border:1px solid var(--line-dim);border-radius:var(--radius-lg);padding:22px;margin:16px 0}
.tiers{display:grid;grid-template-columns:repeat(auto-fit,minmax(224px,1fr));gap:16px;margin:20px 0}
.billtoggle{display:inline-flex;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:999px;padding:3px;margin:14px 0 2px}
.bt{background:none;border:0;color:var(--dim);font:600 .84rem var(--body);padding:7px 18px;border-radius:999px;cursor:pointer;transition:all .18s}
.bt.on{background:var(--accent);color:#fff;box-shadow:0 2px 10px rgba(59,130,246,.3)}
.bt:not(.on):hover{color:var(--white)}
.paid{text-align:center;padding:18px 0 4px}
.paid h1{font-size:33px}
.tick{width:58px;height:58px;margin:0 auto 8px;border-radius:50%;display:flex;align-items:center;
justify-content:center;font-size:30px;color:#fff;background:var(--good);
box-shadow:0 6px 22px rgba(34,197,94,.32)}
.tier{background:var(--bg2);border:1px solid var(--line-dim);border-radius:var(--radius-lg);padding:22px;position:relative;overflow:hidden}
.tier::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--accent);transform:scaleX(0);transform-origin:left;transition:transform .3s}
.tier:hover::before{transform:scaleX(1)}
.tier .p1{font-family:var(--display);font-size:24px;font-weight:800;color:var(--white)}
.tier .p2{font-size:13px;color:var(--dim)}
.tier ul{list-style:none;margin:12px 0;font-size:13.5px;color:var(--dim)}
.tier ul li{padding:3px 0 3px 18px;position:relative}.tier ul li::before{content:'▸';position:absolute;left:0;color:var(--accent)}
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--accent);color:#fff;border:0;border-radius:var(--radius);padding:11px 24px;
font:600 .9rem var(--body);cursor:pointer;text-decoration:none;transition:all .2s;box-shadow:0 4px 16px rgba(59,130,246,.22)}
.btn:hover{background:var(--accent-dark);color:#fff;transform:translateY(-2px);box-shadow:0 8px 24px rgba(59,130,246,.32)}
.btn.ghost{background:var(--accent-dim);color:var(--ink);border:1px solid var(--line-hi);box-shadow:none}
.btn.ghost:hover{background:rgba(59,130,246,.16);color:var(--white)}
.btn.bad{background:transparent;border:1px solid var(--bad);color:var(--bad);box-shadow:none}
label{display:block;margin:15px 0 5px;font-weight:600;font-size:13.5px;color:var(--ink)}label small{font-weight:400;color:var(--dim)}
input[type=text],input[type=email],input[type=url],input[type=number],textarea,select{width:100%;background:var(--bg3);border:1px solid var(--line);
color:var(--ink);border-radius:var(--radius);padding:10px 12px;font:14px var(--body)}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.seg label{margin:0;cursor:pointer}.seg label span{display:block;padding:9px 16px;font-size:13.5px;font-weight:600;color:var(--dim)}
.seg input{position:absolute;opacity:0}.seg input:checked + span{background:var(--accent);color:#fff}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:650;border:1px solid var(--line);color:var(--dim)}
.pill.good{color:var(--good);border-color:rgba(34,197,94,.4)}.pill.bad{color:var(--bad);border-color:rgba(248,113,113,.4)}
.flash{background:var(--accent-dim);border:1px solid var(--line-hi);border-radius:var(--radius);padding:12px 15px;margin:14px 0;font-size:14px}
.flash.bad{background:rgba(248,113,113,.1);border-color:rgba(248,113,113,.4)}.flash.good{background:rgba(34,197,94,.1);border-color:rgba(34,197,94,.4)}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:10px 0}
td,th{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
code{background:var(--bg3);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font:.85em var(--mono)}
code.feedurl{display:inline-block;word-break:break-all;white-space:normal;color:var(--accent);padding:5px 8px;line-height:1.5}
hr{border:0;border-top:1px solid var(--line-dim);margin:24px 0}
footer{margin-top:52px;padding-top:22px;border-top:1px solid var(--line-dim);color:var(--faint);font-size:12.5px}
footer .fb{display:flex;align-items:center;gap:8px;font-family:var(--display);font-weight:700;color:var(--white);margin-bottom:8px}
footer .fb b span{color:var(--brand)}footer a{color:var(--dim)}footer a:hover{color:var(--accent)}

/* --- marketing landing --- */
.eyebrow{font:600 12px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:10px}
.hero h1{font-size:38px;margin:.15em 0}
@media(max-width:640px){.hero h1{font-size:29px}}
.hero .lede{font-size:18px;max-width:38em}
.cta{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0 12px}
.creds{display:flex;gap:8px 20px;flex-wrap:wrap;margin-top:22px;font:12.5px var(--mono);color:var(--dim)}
.creds span{display:inline-flex;align-items:center;gap:6px}
.creds span::before{content:'▸';color:var(--accent)}
.section{margin:44px 0;padding-top:36px;border-top:1px solid var(--line-dim)}
.section > h2{margin-top:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.grid2{grid-template-columns:1fr}}
.steps{counter-reset:s;display:grid;gap:14px}
.step{background:var(--bg2);border:1px solid var(--line-dim);border-radius:var(--radius-lg);padding:18px 20px 18px 56px;position:relative}
.step::before{counter-increment:s;content:counter(s);position:absolute;left:16px;top:16px;width:26px;height:26px;
border-radius:50%;background:var(--accent-dim);border:1px solid var(--line-hi);color:var(--accent);
font:700 13px var(--mono);display:flex;align-items:center;justify-content:center}
.step b{color:var(--white);font-family:var(--display)}
.sample{background:var(--bg3);border:1px solid var(--line);border-radius:var(--radius-lg);padding:20px;
font:13.5px/1.6 var(--mono);white-space:pre-wrap;color:var(--ink)}
.sample .h{color:var(--white);font-weight:600}
.sample .tag{color:var(--dim)}
.faq details{border:1px solid var(--line-dim);border-radius:var(--radius);padding:12px 16px;margin:8px 0;background:var(--bg2)}
.faq summary{cursor:pointer;font-weight:600;color:var(--white);font-family:var(--display);font-size:14.5px}
.faq details[open] summary{margin-bottom:8px}
.faq p{margin:.3em 0;color:var(--dim);font-size:14px}
.cta-band{background:linear-gradient(135deg,rgba(59,130,246,.1),transparent);border:1px solid var(--line-hi);
border-radius:var(--radius-lg);padding:32px;text-align:center;margin:44px 0}
.cta-band h2{border:0;margin:0 0 .4em}
.who{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.who div{background:var(--bg2);border:1px solid var(--line-dim);border-radius:var(--radius);padding:14px;font-size:13.5px}
.who b{color:var(--white);font-family:var(--display);display:block;margin-bottom:3px}
`;

// GA4 in consent mode, same as securitycyber.uk: loads everywhere, analytics
// cookies stay denied until the visitor accepts. Consent is a cookie on
// .securitycyber.uk (not localStorage) so one "Accept" covers both sites.
// Signed account tokens travel in ?t= (confirm/manage/unsubscribe) — the page
// URL and referrer are rebuilt from an allowlist so a token can never reach GA.
function gaHead(cfg) {
  const id = cfg.ga_measurement_id || "";
  if (!/^G-[A-Z0-9]+$/.test(id)) return "";
  return `<script async src="https://www.googletagmanager.com/gtag/js?id=${id}"></script>
<script>(function(){window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}window.gtag=gtag;
var ok=/(?:^|; )sc_cookie_ok=1(?:;|$)/.test(document.cookie);
gtag('consent','default',{analytics_storage:ok?'granted':'denied',ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});
var KEEP=['tier','cadence','utm_source','utm_medium','utm_campaign','utm_term','utm_content'];
function clean(u){try{var x=new URL(u),k=new URLSearchParams();KEEP.forEach(function(p){var v=x.searchParams.get(p);if(v)k.set(p,v);});var s=k.toString();return x.origin+x.pathname+(s?'?'+s:'');}catch(e){return '';}}
gtag('js',new Date());
gtag('config','${id}',{page_location:clean(location.href),page_referrer:document.referrer?clean(document.referrer):''});
var q=new URLSearchParams(location.search);
if(location.pathname==='/welcome')gtag('event','sign_up');
if(location.pathname==='/subscribe'&&q.get('k')==='bad')gtag('event','signup_error',{tier:q.get('tier')||'',reason:(q.get('m')||'').slice(0,100)});
})();</script>`;
}

function consentBanner(cfg) {
  if (!/^G-[A-Z0-9]+$/.test(cfg.ga_measurement_id || "")) return "";
  return `<div id=ck style="display:none;position:fixed;left:12px;right:12px;bottom:12px;max-width:560px;margin:0 auto;z-index:50;background:var(--card,#111827);border:1px solid var(--line,#1f2937);border-radius:10px;padding:12px 14px;font-size:14px;align-items:center;gap:12px">
<span style="flex:1">We use a Google Analytics cookie to see which pages help people, only if you accept. <a href="/cookies">Cookies</a>.</span>
<button class=btn type=button id=ckok style="padding:6px 14px">Accept</button></div>
<script>(function(){var b=document.getElementById('ck');if(/(?:^|; )sc_cookie_ok=1(?:;|$)/.test(document.cookie))return;b.style.display='flex';
document.getElementById('ckok').onclick=function(){var d=/(^|\\.)securitycyber\\.uk$/.test(location.hostname)?'; Domain=.securitycyber.uk':'';
document.cookie='sc_cookie_ok=1; Path=/; Max-Age=31536000; SameSite=Lax; Secure'+d;
if(window.gtag)gtag('consent','update',{analytics_storage:'granted'});b.style.display='none';};})();</script>`;
}

export function page(cfg, title, body, flash = "", flashKind = "", noindex = false) {
  const fl = flash ? `<div class="flash ${flashKind}">${esc(flash)}</div>` : "";
  const ico = cfg.ico_reg ? ` · ICO registration ${esc(cfg.ico_reg)}` : "";
  const legal = cfg.company_legal_name || "SecurityCyber";
  const contact = cfg.contact_email || "securitycyber_cti@securitycyber.uk";
  const robots = noindex ? "noindex,nofollow" : "index,follow";
  return `<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="${robots}"><meta name=color-scheme content="dark">
<title>${esc(title)} · SecurityCyber CTI</title><link rel=icon href="/logo.svg">
<style>${CSS}</style>${gaHead(cfg)}</head><body><div class=wrap>
<nav><a class=brand href="/">${LOGO_SVG}<b>Security<span>Cyber</span></b> <span class=tag style="font:400 12px var(--mono);color:var(--dim)">/ CTI</span></a>
${cfg.free_mode ? `<span class=links>
<a href="${esc(cfg.main_site)}">Home</a>
<a href="/">Free CTI</a>
<a href="${esc(cfg.main_site)}/blog">Blog</a>
<a href="${esc(cfg.main_site)}/games">Games</a>
<a href="${esc(discordUrl(cfg))}">Discord</a></span></nav>` : `<span class=links>
<a href="${esc(cfg.main_site)}/services">Services</a>
<a href="${esc(cfg.main_site)}/blue-team">Blue Team</a>
<a href="${esc(cfg.main_site)}/cve">CVE Feed</a>
<a href="/">Alerts</a>
<a href="${esc(cfg.main_site)}/contact">Contact</a></span></nav>`}
${fl}${body}
<footer><div class=fb>${LOGO_SVG}<b>Security<span>Cyber</span></b> · CTI Alerts</div>
<p style="margin:6px 0 10px;font-style:italic">${esc(MOTTO)}</p>
${esc(legal)}${ico} · ${cfg.free_mode ? "Free threat intel from Charlie Collins&rsquo; personal blue-team space." : "Independent UK blue-team security advisory."}<br>
<a href="${esc(cfg.main_site)}">securitycyber.uk</a> ·
${cfg.free_mode ? `<a href="${esc(discordUrl(cfg))}">Discord</a>` : `<a href="${esc(cfg.main_site)}/services">Services</a>`} ·
<a href="https://www.youtube.com/@OpsecCharlie" target="_blank" rel="noopener noreferrer">YouTube</a> ·
<a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/cookies">Cookies</a> ·
<a href="mailto:${esc(contact)}">${esc(contact)}</a></footer>
</div>${consentBanner(cfg)}</body></html>`;
}

export const CHAN_META = {
  email: ["Email", "nothing more to type — alerts go to the email above", ""],
  rss: ["RSS feed", "nothing here — we'll mint you a private feed URL for your reader", ""],
  discord: ["Discord", "nothing here — you'll get a one-click link to join our Discord, and alerts arrive as a DM", ""],
  discord_webhook: ["Discord (your own server)", "a channel webhook URL (Server Settings → Integrations → Webhooks → New Webhook → Copy URL)", "https://discord.com/api/webhooks/…"],
  slack: ["Slack", "an incoming-webhook URL (api.slack.com/messaging/webhooks)", "https://hooks.slack.com/services/…"],
  telegram: ["Telegram", "nothing here — once you confirm your email you'll get a one-tap link to our bot", ""],
  webhook: ["Webhook", "an https:// URL we POST JSON to", "https://example.com/hook"],
  ntfy: ["ntfy", "a full topic URL", "https://ntfy.sh/my-topic"],
};
export const chanLabel = (k) => (CHAN_META[k] || [k])[0];

// Where "join the Discord" goes: the public invite, else the main site's
// Discord section (which carries the invite once it's set there).
export const discordUrl = (cfg) => cfg.discord_invite || `${cfg.main_site}/about#discord`;

// Free-mode landing: the feed itself, no plans, no sign-up.
export function freeLanding(cfg, posts = [], flash = "", flashKind = "") {
  const age = (iso) => {
    const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (!(m >= 0)) return "";
    return m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
  };
  const items = posts.map((p) => {
    const src = /^https?:\/\//i.test(p.source_url || "") ? p.source_url : "";
    const text = String(p.text || "").replace(/\s*https:\/\/cti\.securitycyber\.uk\/?\s*$/, "");
    return `<div class=card><div class=muted style="font:500 12px var(--mono)">score ${esc(p.score)} · ${esc(age(p.posted_at))}</div>
<h3 style="margin-top:6px">${esc(p.title)}</h3><p class=muted>${esc(text)}</p>
${src ? `<a href="${esc(src)}" target="_blank" rel="noopener noreferrer" style="font:500 13px var(--mono)">primary source →</a>` : ""}</div>`;
  }).join("\n");
  const body = `<section class=hero><div class=eyebrow>Cyber threat intelligence · free</div>
<h1>The feed is free. No sign-up, no paywall.</h1>
<p class=lede>Scored, source-linked alerts with a defensive takeaway on each — open to everyone. Read the full feed on securitycyber.uk, and talk it through in the Discord.</p>
<p style="margin-top:18px;display:flex;gap:10px;flex-wrap:wrap">
<a class=btn href="${esc(cfg.main_site)}/cti">Read the full feed</a>
<a class="btn ghost" href="${esc(discordUrl(cfg))}">Join the Discord</a></p></section>
<h2>Latest alerts</h2>
${items || `<div class=card><p class=muted>The feed is quiet right now — check <a href="${esc(cfg.main_site)}/cti">securitycyber.uk/cti</a>.</p></div>`}
<div class="cta-band" style="margin-top:28px"><h2>Talk about it in the Discord</h2>
<p class=muted>Seen it in your own logs? Got a detection for it? The Discord is open to everyone, and new alerts are posted in its shared alerts channel too.</p>
<p style="margin-top:14px"><a class=btn href="${esc(discordUrl(cfg))}">Join the Discord</a></p></div>`;
  return page(cfg, "Free CTI feed", body, flash, flashKind);
}

export function landing(cfg, flash = "", flashKind = "", buyable = null) {
  const cap = (s) => s[0].toUpperCase() + s.slice(1);
  let tiers = "";
  for (const name of ["free", "individual", "team"]) {
    const t = cfg.tiers[name]; if (!t) continue;
    const chans = (t.channels || []).map(chanLabel).join(" · ");
    // Free plan: no cadence toggle, no price to swap — one static card that the
    // billing toggle never hides (data-cads covers both periods).
    if (t.free) {
      if (!paymentLink(cfg, name, "monthly")) continue;
      tiers += `<div class=tier data-cads="monthly weekly"><h3>Free trial</h3>
<div class=p1>7 days <span class=p2>free · no card</span></div>
<p class=muted style="margin-top:8px">${esc(t.blurb || "")}</p>
<ul>
<li>The full paid feed for a week — instant alerts scoring ${Number(t.instant_min_score) || 70}+ and the daily digest</li>
<li>Includes the weekly honeynet brief from our own sensors</li>
<li>Goes to ${esc(chans)}</li>
<li>No card, no charge. It stops after 7 days unless you upgrade</li>
<li>Keep it going from £2/week — your feed URL and preferences carry over</li>
</ul>
<a class="btn ghost" href="/subscribe?tier=free">Start the trial →</a></div>`;
      continue;
    }
    // Only advertise a cadence we can actually take money for, so the pricing
    // page and the signup form never disagree about what's on sale.
    const b = buyable ? (buyable[name] || {}) : { monthly: true, weekly: true };
    const hasM = Number(t.monthly_pence) > 0 && b.monthly !== false;
    const hasW = Number(t.weekly_pence) > 0 && b.weekly !== false;
    if (!hasM && !hasW) continue;
    // Both cadences live in the card; the toggle above swaps which is shown,
    // so the whole decision happens here instead of on the next page.
    // Only emit a cadence that actually has a price, so the toggle can never
    // reveal an empty one.
    const perM = hasM ? `<span class=per data-cad=monthly>${esc(t.monthly_display)}</span>` : "";
    const perW = hasW ? `<span class=per data-cad=weekly>${esc(t.weekly_display)}</span>` : "";
    const alt = hasM && hasW
      ? `<div class=p2><span data-cad=monthly>that's ${esc(t.weekly_display)} billed weekly instead</span>`
        + `<span data-cad=weekly hidden>that's ${esc(t.monthly_display)} billed monthly instead</span></div>`
      : "";
    // data-cads lists the periods this plan is actually sold on, so the
    // toggle can hide the whole card rather than leave an empty price on a
    // monthly-only plan.
    const cads = [hasM ? "monthly" : "", hasW ? "weekly" : ""].filter(Boolean).join(" ");
    tiers += `<div class=tier data-cads="${cads}"><h3>${cap(name)}</h3>
<div class=p1>${perM}${perW}</div>${alt}
<p class=muted style="margin-top:8px">${esc(t.blurb || "")}</p>
<ul>
<li>Only what clears the bar — nothing routine, nothing you can't act on</li>
<li>Delivered the moment it's confirmed, plus a once-a-day recap</li>
<li>Weekly honeynet brief from our own sensors — included, no extra cost</li>
${t.attach_json ? "<li>Machine-readable JSON with every alert — pipe it straight into your tooling</li>" : ""}
<li>Filter to your stack: name the vendors you run, ignore the rest</li>
<li>Goes to ${esc(chans)}</li>
</ul>
<a class="btn buy" data-tier="${name}" href="/subscribe?tier=${name}&cadence=monthly">Choose ${cap(name)} →</a></div>`;
  }
  const b = cfg.brief || {};
  const briefBuyable = buyable ? buyable.__brief !== false : true;
  const briefCard = !briefBuyable ? "" : `<div class=tier><h3>One-off brief</h3>
<div class=p1>${esc(b.display || "")} <span class=p2>one payment</span></div>
<p class=muted style="margin-top:8px">Not ready to subscribe? Buy a single deep dive on the
vulnerability or incident that's on your mind right now.</p>
<ul>
<li>One topic, researched from the primary sources</li>
<li>Impact, exposure, detection guidance and a response checklist</li>
<li>Written for your environment, delivered by email in ~2 working days</li>
</ul>
<a class="btn ghost" href="/brief">Buy a brief →</a></div>`;

  const body = `
<div class=hero>
<div class=eyebrow>SecurityCyber · Threat Intelligence</div>
<h1>Know what actually matters this week — in a two-minute read.</h1>
<p class=lede>Dozens of "critical" advisories land every week; only a few are worth your
attention. We do the reading, the cross-checking and the "so what", and send you just those —
with the one thing to do — to your inbox, Slack, Discord, Telegram or a private feed.
Situational awareness for people who take security seriously, whether that's a SOC, a
side-of-desk role, or a home lab.</p>
<div class=cta>
<a class=btn href="#pricing">See plans &amp; pricing</a>
<a class="btn ghost" href="#how">How it works</a>
</div>
<div class=creds>
<span>Run by a working blue-teamer — ISC2 Certified in Cybersecurity</span>
<span>SOC &amp; detection focus</span>
<span>Threat-informed, hands-on</span>
<span>UK-based · UK data protection</span>
<span>Anthropic CVP verified (Defense Access)</span>
</div>
</div>

<div class=section>
<h2>You don't have an information problem. You have a triage problem.</h2>
<div class=grid2>
<div class=card><b class=h style="font-family:var(--display);color:var(--white)">The firehose</b>
<p class=muted>Vendor bulletins, mailing lists, news sites, Twitter/Mastodon threads, the NVD.
Hundreds of items a week, most of it noise, some of it wrong, and it never stops.</p></div>
<div class=card><b class=h style="font-family:var(--display);color:var(--white)">The real question</b>
<p class=muted>Of everything published today: what is actually being exploited, does it touch
software <em>you</em> run, and what do you do about it before end of day? That's the only
question worth answering — and it takes time you don't have.</p></div>
</div>
<p class=muted style="margin-top:14px">We answer it for you, continuously, and only tell you
when the answer is "yes — act".</p>
</div>

<div class=section id=how>
<h2>How it works</h2>
<p class=muted>An AI analyst does the reading — continuously, across every source, at a volume no
human could sustain — held to the same discipline a good human analyst would follow: check the
primary source, never invent a fact, say "not stated" rather than guess.</p>
<div class=steps>
<div class=step><b>It watches, around the clock.</b>
<p class=muted>Vendor advisories, national catalogues and reputable research — the sources a
good analyst would check — pulled in continuously, de-duplicated so the same story from five
outlets counts once.</p></div>
<div class=step><b>It verifies against the primary source.</b>
<p class=muted>Every candidate is checked back to the vendor bulletin or the researcher's
write-up. No headline-only alerts, no invented CVEs or version numbers. If the facts don't
hold up, it doesn't go out.</p></div>
<div class=step><b>It scores it for real-world impact.</b>
<p class=muted>Confirmed exploitation in the wild, presence on CISA's Known Exploited
Vulnerabilities catalogue, how widely the software is deployed, blast radius through
supply chains and identity providers, whether a fix exists. Vibes don't get a vote.</p></div>
<div class=step><b>You get the ones that clear your bar — and nothing else.</b>
<p class=muted>Delivered to your channel within minutes of confirmation, in a fixed, scannable
format, plus a once-a-day digest. Set the impact threshold and the vendors you care about;
everything below the line stays out of your day.</p></div>
</div>
<p class=muted style="margin-top:14px">The method is ours. The output is yours to act on —
sourced, dated and linked back to the original every time.</p>
</div>

<div class=section>
<h2>What actually lands in your channel</h2>
<p class=muted>A worked-through brief, not a link dump. Same shape every time so your team knows
exactly where to look. Illustrative example:</p>
<div class=sample><span class=h>If you run internet-facing &lt;product&gt;, patch to &lt;version&gt; today — a pre-auth flaw is being exploited in the wild.</span>

• <span class=tag>Vulnerability:</span> CVE-YYYY-NNNNN (CVSS 9.x) — what it is, in one line
• <span class=tag>Affected:</span> product + version ranges; the exact exposure condition
• <span class=tag>Do now:</span> the single patch or mitigation, with the version numbers

Who's exploiting it and per which source, and what a defender should hunt for right now.

&lt;link to the vendor advisory&gt;
</div>
<p class=muted style="margin-top:12px">Every fact is attributed. We never invent a CVE, a
version, a CVSS or an indicator — if the source doesn't say it, it's not in the alert.</p>
</div>

<div class=section id=honeynet>
<h2>Honeynet intelligence — our own sensors, included in every plan</h2>
<p class=muted>Alongside the news-driven alerts, we run our own internet-facing honeypots:
SSH and Telnet sensors plus Windows RDP/SMB hosts, collected in Microsoft Sentinel. Once a
week every subscriber gets a first-party honeynet brief built from that telemetry — not
republished from another vendor's feed. It's part of the subscription at no extra cost,
including the free trial.</p>
<div class=grid2>
<div class=card><b class=h style="font-family:var(--display);color:var(--white)">What's in the brief</b>
<p class=muted>Attack volume and how far attackers got through the kill chain, the
campaigns we saw, the credentials that actually worked, payload and C2 hosts, the IPs
spraying Windows logons, and what to hunt for. Indicators are defanged.</p></div>
<div class=card><b class=h style="font-family:var(--display);color:var(--white)">How it's made</b>
<p class=muted>Counted and clustered by code, straight from the sensor data — no model reads
the logs, so no figure is estimated or invented. When the sensors had a gap in the week,
the brief says so.</p></div>
</div>
</div>

<div class=section>
<h2>Why not just follow a free feed?</h2>
<div class=grid2>
<div class=card><b class=h style="font-family:var(--display);color:var(--white)">A free feed gives you everything</b>
<p class=muted>…and leaves the triage, the verification and the "what do we do" to you. That's
the expensive part, and it's exactly the part that gets skipped on a busy week.</p></div>
<div class=card><b class=h style="font-family:var(--display);color:var(--white)">This gives you the decision</b>
<p class=muted>Filtered to your stack, checked by someone who does detection for a living,
with the action spelled out. If a quiet week means one alert, you get one alert. If it's a
bad week, you get the full picture — fast.</p></div>
</div>
</div>

<div class=section>
<h2>Who it's for</h2>
<div class=who>
<div><b>Solo &amp; side-of-desk defenders</b>The one person holding the pager — situational awareness without living in feeds.</div>
<div><b>Home labbers &amp; learners</b>Follow what's actually being exploited, week by week, with the reasoning spelled out.</div>
<div><b>Small IT teams</b>No dedicated security hire, still on the hook for it.</div>
<div><b>MSPs, vCISOs &amp; consultants</b>One filtered stream to stay current across every client's stack.</div>
</div>
</div>

<div class=section id=roadmap>
<h2>Where it's heading</h2>
<p class=muted>The direction we're building in — shared openly, not a set of promises or dates.
The core is live today; these just make the situational-awareness picture fuller.</p>
<div class=steps>
<div class=step><b>A weekly "what mattered" recap.</b>
<p class=muted>A short retrospective so you can take in the week at a glance, not only the day's alerts.</p></div>
<div class=step><b>Exploit-likelihood scoring.</b>
<p class=muted>Alongside severity and real-world exploitation, a read on how likely something is to be exploited next.</p></div>
<div class=step><b>Tell us your stack once.</b>
<p class=muted>Saved product-watches, so the feed quietly tunes itself to what you actually run.</p></div>
<div class=step><b>Public CVE reference pages.</b>
<p class=muted>Clean, linkable write-ups of the vulnerabilities that matter — free to read and share.</p></div>
<div class=step><b>More first-hand sensor coverage.</b>
<p class=muted>Growing the honeynet so more of what you read comes from our own sensors, not reposted feeds.</p></div>
</div>
</div>

<div class=section id=pricing>
<h2>Plans</h2>
<div class=billtoggle role=group aria-label="Billing period">
<button type=button class="bt on" data-cad=monthly aria-pressed=true>Monthly</button>
<button type=button class=bt data-cad=weekly aria-pressed=false>Weekly</button>
</div>
<div class=tiers>${tiers}${briefCard}</div>
<p class=muted style="margin-top:12px">Start with a 7-day free trial — no card, the full paid feed for a week.
Keep it going any time. Paid plans: weekly or monthly, cancel anytime, no contract.
Card handled by Stripe — it never touches our servers. Prices in GBP, no VAT.</p>
</div>
<script>(function(){
var cad='monthly';
function apply(){
  document.querySelectorAll('#pricing [data-cad]').forEach(function(el){
    if(el.classList.contains('bt')){
      var on=el.dataset.cad===cad;el.classList.toggle('on',on);el.setAttribute('aria-pressed',on);
    }else{ el.hidden = el.dataset.cad!==cad; }
  });
  // A plan not sold on the selected period disappears entirely — better than
  // showing a card with no price on it.
  document.querySelectorAll('#pricing .tier[data-cads]').forEach(function(card){
    card.hidden = card.dataset.cads.split(' ').indexOf(cad) === -1;
  });
  document.querySelectorAll('#pricing a.buy').forEach(function(a){
    a.href='/subscribe?tier='+a.dataset.tier+'&cadence='+cad;
  });
}
document.querySelectorAll('#pricing .bt').forEach(function(b){
  b.addEventListener('click',function(){cad=b.dataset.cad;apply();});
});
apply();
})();</script>

<div class=section id=faq>
<h2>Questions</h2>
<div class=faq>
<details><summary>Do you send me everything you find?</summary>
<p>No — the opposite. You set an impact threshold and we only send what clears it. Most days
that's nothing or one item. The daily digest catches anything just below your line.</p></details>
<details><summary>I only care about Fortinet, Cisco and VMware. Can I filter?</summary>
<p>Yes. Name the vendors or keywords that matter to your estate and we drop everything else.
Change it any time from your preferences page.</p></details>
<details><summary>Which channels can I use?</summary>
<p>Email, Discord, Slack, Telegram, ntfy, or a plain webhook (which covers Microsoft Teams and
most SOAR/ticketing tools). Team plan can use several at once.</p></details>
<details><summary>Who writes the alerts — is it AI?</summary>
<p>Yes, honestly: an AI analyst does the first pass — reading every source, cross-checking
CISA KEV, scoring impact, and drafting the brief — built, tuned and supervised by Charlie
Collins, a working blue-teamer and ISC2 Certified in Cybersecurity. It runs to a strict rule:
facts only from the source, never an invented CVE, version number or exploitation claim — if a
detail isn't stated, the brief says so rather than guessing. That discipline is what lets one
person run analyst-grade coverage around the clock instead of the one or two stories a day a
single human could write up.</p></details>
<details><summary>How fast is "instant"?</summary>
<p>Minutes from the point a story is confirmed against the source, not minutes from when it
was first published somewhere. We'd rather be right than first.</p></details>
<details><summary>Is my data safe?</summary>
<p>We hold your email, your channel destination and your filter preferences — nothing more.
No card data (that's Stripe). Hosted on Cloudflare, UK data protection applies, and you can
export or delete everything yourself with one click. See the <a href="/privacy">privacy policy</a>.</p></details>
<details><summary>Can I cancel?</summary>
<p>Any time, from the billing portal linked in your preferences. Access runs to the end of the
period you've paid for.</p></details>
<details><summary>Is this a substitute for vulnerability scanning / patch management?</summary>
<p>No. It's an early-warning and prioritisation layer on top of what you already do. See the
<a href="/terms">terms</a>.</p></details>
</div>
</div>

<div class=cta-band>
<h2>Stop reading feeds. Start getting told.</h2>
<p class=muted style="max-width:34em;margin:0 auto 18px">Pick a plan, choose where it goes,
and the next exploited vulnerability that touches your stack will find you — not the other way round.</p>
<a class=btn href="#pricing">Choose a plan →</a>
</div>

<div class=section>
<h3>Already subscribed?</h3>
<div class=card><form method=post action="/account">
<label>Your email <small>— we'll send your preferences &amp; unsubscribe link</small></label>
<input type=email name=email required placeholder="you@example.com">
<div class=row style="margin-top:12px"><button class="btn ghost">Email me my link</button></div>
</form></div>
</div>`;
  return page(cfg, "Threat intelligence alerts for defenders", body, flash, flashKind);
}

// ---- private RSS feed (RSS 2.0) ---------------------------------------
// XML needs its own escaper: esc() leaves quotes/apostrophes alone, which is
// fine in HTML text nodes but not inside XML attributes or CDATA-free content.
function xesc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&apos;")
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");  // XML 1.0 forbids these
}
const rfc822 = (iso) => { const d = new Date(iso || Date.now()); return (isNaN(d) ? new Date() : d).toUTCString(); };

export function feedXml(cfg, posts, { selfUrl, title, note = "" } = {}) {
  const items = posts.map((p) => {
    const cves = Array.isArray(p.cve_ids) ? p.cve_ids : [];
    // Paid feed: the analyst's subscriber write-up, not the social hook.
    const desc = (p.subscriber_text || p.post_text || p.short_text || "").trim();
    const act = (p.action || "").trim();
    return `<item>
<title>${xesc(p.title || cves.join(", ") || "Threat alert")}</title>
<link>${xesc(p.url || cfg.site_url)}</link>
<guid isPermaLink="false">${xesc(p.post_key)}</guid>
<pubDate>${rfc822(p.seen_at)}</pubDate>
${p.source ? `<source url="${xesc(cfg.site_url)}">${xesc(p.source)}</source>` : ""}
${cves.map((c) => `<category>${xesc(c)}</category>`).join("")}
<description>${xesc((act ? `DO NOW: ${act}\n\n` : "") + desc
      + (p.score != null ? `\n\nImpact score: ${p.score}/100` : "")
      + (p.url ? `\nSource: ${p.url}` : "")
      + `\n\nSecurityCyber CTI — ${PROMO_URL}`)}</description>
</item>`;
  }).join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>${xesc(title || `${cfg.brand} CTI Alerts`)}</title>
<link>${xesc(PROMO_URL)}</link>
<image><url>${xesc(PROMO_IMG)}</url><title>${xesc(title || `${cfg.brand} CTI Alerts`)}</title><link>${xesc(PROMO_URL)}</link></image>
<description>${xesc(note || "Prioritised threat intelligence for defenders — only what clears the bar.")}</description>
<language>en-gb</language>
<lastBuildDate>${rfc822(posts[0]?.seen_at)}</lastBuildDate>
<ttl>30</ttl>
${selfUrl ? `<atom:link href="${xesc(selfUrl)}" rel="self" type="application/rss+xml"/>` : ""}
${items}
</channel>
</rss>`;
}

export function subscribeForm(cfg, tier, flash = "", flashKind = "", avail = null, preCadence = "", buyable = null) {
  const t = cfg.tiers[tier];
  const b = buyable ? (buyable[tier] || {}) : { monthly: true, weekly: true };
  const allowed = (t.channels || []).filter((x) => CHAN_META[x]);
  // A cadence is offered when it has a price in pence. The Stripe price id is
  // optional — billing.ensureTierPrice() creates one on the first checkout.
  const hasM = Number(t.monthly_pence) > 0 && b.monthly !== false;
  const hasW = Number(t.weekly_pence) > 0 && b.weekly !== false;
  let cadOpts = "";
  // Honour the cadence picked on the pricing cards, else default to monthly.
  const want = ["monthly", "weekly"].includes(preCadence) ? preCadence
    : (hasM ? "monthly" : "weekly");
  for (const [cad, on, disp] of [["monthly", hasM, t.monthly_display], ["weekly", hasW, t.weekly_display]]) {
    if (!on) continue;
    const chk = cad === want ? " checked" : "";
    cadOpts += `<label><input type=radio name=cadence value="${cad}"${chk}><span>${esc(disp)}</span></label>`;
  }
  const platOpts = allowed.map((p) => {
    const a = avail ? (avail[p] || { ok: true }) : { ok: true };
    const label = a.ok ? CHAN_META[p][0] : `${CHAN_META[p][0]} — unavailable`;
    return `<option value="${p}"${a.ok ? "" : " disabled"} data-hint="${esc(a.ok ? CHAN_META[p][1] : a.why)}" `
      + `data-ph="${esc(CHAN_META[p][2])}">${esc(label)}</option>`;
  }).join("");
  const contact = cfg.contact_email || "securitycyber_cti@securitycyber.uk";
  const isFree = !!t.free;
  const body = `
<h1>${isFree ? "Start your 7-day free trial" : `Subscribe — ${tier[0].toUpperCase() + tier.slice(1)}`}</h1>
<p class=lede>${esc(t.blurb || "")}</p>
<form method=post action="/subscribe" id=sf>
<input type=hidden name=tier value="${tier}">
${cadOpts ? `<label>Billing</label><div class=seg>${cadOpts}</div>` : ""}
<label>Your email <small>— your account + where the confirmation goes</small></label>
<input type=email name=email required placeholder="you@example.com">
<label>Send alerts to</label>
<select name=platform id=plat>${platOpts}</select>
<p class=muted id=hint style="margin:6px 0 0"></p>
<input type=text name=target id=tgt style="margin-top:8px">
<label style="font-weight:400;margin-top:18px" class=row>
<input type=checkbox name=agree required style="width:auto">&nbsp;I agree to the
<a href="/terms" target=_blank>Terms</a> and <a href="/privacy" target=_blank>Privacy Policy</a>,
and I want alerts to start straight away.</label>
<div class=row style="margin-top:16px"><button class=btn>${isFree ? "Start the free trial →" : "Continue to payment →"}</button>
<a class="btn ghost" href="/">Back</a></div>
<p class=muted style="margin-top:10px">${isFree
    ? "7-day free trial — no card, no payment details, just your email. Confirm it and alerts start. It stops after 7 days unless you upgrade — nothing happens automatically."
    : "Card details are handled by Stripe — they never touch our servers."}
Questions: <a href="mailto:${esc(contact)}">${esc(contact)}</a>.</p>
</form>
<script>(function(){var s=document.getElementById('plat'),h=document.getElementById('hint'),t=document.getElementById('tgt');
function u(){var o=s.options[s.selectedIndex];h.textContent=o.getAttribute('data-hint')||'';t.placeholder=o.getAttribute('data-ph')||'';
// email reuses the account address above (the server ignores this box for it);
// telegram, discord and rss need nothing typed either. Hide the box for those.
var noInput=(s.value==='email'||s.value==='telegram'||s.value==='rss'||s.value==='discord');
if(noInput){t.style.display='none';t.required=false;t.value='';}else{t.style.display='';t.required=true;}}
s.addEventListener('change',u);u();})();</script>`;
  return page(cfg, `Subscribe — ${tier}`, body, flash, flashKind);
}

export function managePage(cfg, sub, tok, flash = "", flashKind = "") {
  const tierCfg = cfg.tiers[sub.tier] || {};
  const p = sub.prefs;
  const short = (s) => (s && s.length > 44 ? s.slice(0, 28) + "…" + s.slice(-10) : s || "");
  const chRows = sub.channels.map((ch) => {
    const v = ch.verified ? '<span class="pill good">verified</span>' : '<span class="pill bad">unverified</span>';
    const rm = ch.kind === "email" ? "" :
      `<form method=post action="/manage/channel/remove" style="display:inline"><input type=hidden name=t value="${esc(tok)}">
<input type=hidden name=cid value="${ch.id}"><button class="btn ghost" style="padding:4px 10px;font-size:13px">remove</button></form>`;
    // For rss the target is the feed secret — show the full URL to copy, not a
    // truncated id, since pasting it into a reader is the whole point.
    const where = ch.kind === "rss"
      ? `<code class=feedurl>${esc(cfg.site_url)}/feed/${esc(ch.target)}.xml</code>`
      : `<span class=muted>${esc(short(ch.target))}</span>`;
    return `<tr><td>${esc(chanLabel(ch.kind))}</td><td>${where}</td><td>${v} ${rm}</td></tr>`;
  }).join("");
  const hasRss = sub.channels.some((ch) => ch.kind === "rss");
  const rssNote = hasRss ? `<p class=muted style="margin-top:10px">Paste that feed URL into any reader
(Feedly, Inoreader, NetNewsWire, Thunderbird…). It's private to you — treat it like a password, and it
keeps updating for as long as the subscription is active.</p>` : "";
  const addOpts = (tierCfg.channels || [])
    .filter((x) => !["email", "telegram", "discord"].includes(x) && CHAN_META[x])
    .filter((x) => !(x === "rss" && hasRss))   // one feed per subscriber
    .map((x) => `<option value="${x}" data-hint="${esc(CHAN_META[x][1])}" data-ph="${esc(CHAN_META[x][2])}">${esc(chanLabel(x))}</option>`).join("");
  const portal = sub.stripe_customer_id ?
    `<form method=post action="/manage/portal"><input type=hidden name=t value="${esc(tok)}">
<button class="btn ghost">Manage billing / payment method</button></form>` : "";
  const paused = sub.status === "paused";
  const pastDue = sub.status === "past_due";
  const resumeForm = `<form method=post action="/manage/resume" style="display:inline">
<input type=hidden name=t value="${esc(tok)}"><button class=btn>Resume alerts</button></form>`;
  const statusBanner = paused
    ? `<div class="flash bad">Alerts are <b>paused</b> — you're still subscribed and billed, but nothing is being
sent. ${resumeForm}</div>`
    : pastDue
      ? `<div class="flash bad">Your last payment failed. Alerts keep coming for a few days while your bank
retries — update your card in the billing portal below to avoid interruption.</div>`
      : "";
  const body = `
<h1>Your alert preferences</h1>
<p class=muted>Plan: <b>${esc(sub.tier[0].toUpperCase() + sub.tier.slice(1))}</b> (${esc(sub.cadence)}) ·
Status: <b>${esc(sub.status)}</b> · ${esc(sub.email)}</p>
${statusBanner}
<h2>Channels</h2>
<div class=card><table><tbody><tr><th>type</th><th>where</th><th></th></tr>${chRows}</tbody></table>${rssNote}
<form method=post action="/manage/channel/add" style="margin-top:12px"><input type=hidden name=t value="${esc(tok)}">
<div class=row><select name=kind id=ak style="max-width:150px">${addOpts}</select>
<input type=text name=target id=at style="flex:1;min-width:200px"><button class=btn>Add</button></div>
<p class=muted id=ah style="margin-top:8px"></p>
<p class=muted>For <b>Telegram</b>, <a href="/channel/telegram?t=${esc(tok)}">use the one-tap bot link →</a></p>
<p class=muted>For <b>Discord</b>, <a href="/channel/discord?t=${esc(tok)}">connect your account →</a>
— one click adds you to our server and alerts arrive as a DM. Nothing to copy or paste.</p></form>
<script>(function(){var s=document.getElementById('ak'),h=document.getElementById('ah'),t=document.getElementById('at');
function u(){if(!s)return;var o=s.options[s.selectedIndex];h.textContent=o.getAttribute('data-hint')||'';t.placeholder=o.getAttribute('data-ph')||'';}
if(s){s.addEventListener('change',u);u();}})();</script></div>
<h2>What you get alerted about</h2>
<div class=card><form method=post action="/manage/prefs"><input type=hidden name=t value="${esc(tok)}">
<label>Vendors / keywords <small>— comma-separated. Blank = everything. e.g. Fortinet, Cisco, VMware</small></label>
<input type=text name=vendors value="${esc(p.vendors.join(", "))}">
<label>Only alert me at score ≥ <small>— blank = your plan's defaults: everything from
${esc(tierCfg.min_score ?? 0)} reaches your digest and feed, ${esc(tierCfg.instant_min_score)}+ interrupts you.
Setting a number here uses it for both (it can't go below ${esc(tierCfg.min_score ?? 0)}).</small></label>
<input type=text name=min_score value="${esc(p.min_score_override ?? "")}" style="max-width:120px">
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=instant ${p.instant ? "checked" : ""}> Instant alerts</label>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=digest ${p.digest ? "checked" : ""}> Daily digest</label>
<div style="margin-top:12px"><button class=btn>Save</button></div></form></div>
<h2>Account</h2>
<div class=card><div class=row>
<form method=post action="/manage/test"><input type=hidden name=t value="${esc(tok)}"><button class="btn ghost">Send me a test alert</button></form>
${portal}
<a class="btn ghost" href="/manage/export?t=${esc(tok)}">Download my data</a>
${paused ? resumeForm : `<a class="btn ghost" href="/unsubscribe?t=${esc(tok)}">Pause all alerts</a>`}</div>
<form method=post action="/manage/delete" style="margin-top:14px"
 onsubmit="return confirm('Delete your account and all your data? If you have a paid subscription this also cancels it. This cannot be undone.')">
<input type=hidden name=t value="${esc(tok)}"><button class="btn bad">Delete my account &amp; data</button></form></div>`;
  return page(cfg, "Preferences", body, flash, flashKind, true);   // noindex — token-gated
}

export function simple(cfg, title, heading, lede, extra = "") {
  // transactional / link-gated pages — keep them out of search indexes
  return page(cfg, title, `<h1>${esc(heading)}</h1><p class=lede>${esc(lede)}</p>${extra}`, "", "", true);
}

// Where a Payment Link drops the customer after a successful payment.
// Written for the gap it has to survive: Stripe redirects the browser
// immediately, but our webhook may land a second or two later, so the
// confirmation email might not have sent yet. Never say "we've emailed you"
// as though it already happened.
const DEFAULT_PAID_STEPS = [
  ["Check your email", "We're sending a confirmation link to the address you paid with. It usually arrives within a minute — occasionally a little longer at busy times."],
  ["Click the link in it", "That switches your alerts on and takes you to your preferences, where you can set your impact threshold, filter to the vendors you actually run, and change where alerts are delivered."],
  ["That's it", "The next thing that clears your bar comes to you. Nothing routine, nothing you can't act on."],
];

export function paidPage(cfg, opts = {}) {
  const support = cfg.support_email || cfg.contact_email || "";
  const body = `
<div class=paid>
<div class=tick aria-hidden="true">✓</div>
<h1>${esc(opts.heading || "Payment received")}</h1>
<p class=lede>${esc(opts.lede || "Thank you — you're set up. One quick step and your alerts start.")}</p>
</div>

<div class=steps style="margin-top:26px">
${(opts.steps || DEFAULT_PAID_STEPS).map(([h, b]) =>
    `<div class=step><h3>${esc(h)}</h3><p class=muted>${esc(b)}</p></div>`).join("\n")}
</div>

<div class=card style="margin-top:24px">
<h3>${esc(opts.helpTitle || "Nothing after a few minutes?")}</h3>
<p class=muted style="margin-top:6px">${opts.helpBody ? esc(opts.helpBody)
    : `Check your spam or junk folder first — then use <b>Already subscribed?</b> on the
<a href="/">home page</a> to have the link sent again. Your payment is safe either way;
nothing is lost.`}</p>
${support ? `<p class=muted>Still stuck: <a href="mailto:${esc(support)}">${esc(support)}</a></p>` : ""}
</div>

<div class=row style="margin-top:20px">
<a class=btn href="/">Back to SecurityCyber CTI</a>
<a class="btn ghost" href="${esc(cfg.main_site)}">securitycyber.uk</a>
</div>`;
  return page(cfg, opts.heading || "Payment received", body, "", "", true);
}

export function briefPage(cfg, flash = "", flashKind = "") {
  const b = cfg.brief || {};
  const contact = cfg.contact_email || "securitycyber_cti@securitycyber.uk";
  const body = `
<h1>On-demand threat brief — ${esc(b.display || "")}</h1>
<p class=lede>${esc(b.blurb || "")}</p>
<div class=card>
<h3>What you get</h3>
<ul class=muted style="list-style:none;padding:0">
<li>▸ One vulnerability or incident of your choosing, researched from the primary sources</li>
<li>▸ Impact, affected versions, exploitation status, and the exact fix</li>
<li>▸ Detection guidance — what to hunt for, log sources, indicators</li>
<li>▸ A short response checklist for your environment</li>
<li>▸ Delivered as a PDF by email, usually within two working days</li>
</ul>
<p class=muted>One-off payment, no subscription. Card handled by Stripe — it never touches our servers.</p>
</div>
<form method=post action="/brief/buy">
<label>Your email <small>— where the brief is delivered</small></label>
<input type=email name=email required placeholder="you@example.com">
<label>What should the brief cover? <small>— optional, you can also reply to the receipt</small></label>
<input type=text name=topic placeholder="e.g. CVE-2026-… on our Fortinet estate">
<div class=row style="margin-top:16px">
<button class=btn>Buy the brief — ${esc(b.display || "")} →</button>
<a class="btn ghost" href="/">Back</a></div>
<p class=muted style="margin-top:10px">Questions first? <a href="mailto:${esc(contact)}">${esc(contact)}</a></p>
</form>`;
  return page(cfg, "On-demand threat brief", body, flash, flashKind);
}
