// HTML rendering — SecurityCyber brand (securitycyber.uk look).
import { esc } from "./util.js";
import { monthlyPence } from "./config.js";

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
.tiers{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0}@media(max-width:640px){.tiers{grid-template-columns:1fr}}
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
hr{border:0;border-top:1px solid var(--line-dim);margin:24px 0}
footer{margin-top:52px;padding-top:22px;border-top:1px solid var(--line-dim);color:var(--faint);font-size:12.5px}
footer .fb{display:flex;align-items:center;gap:8px;font-family:var(--display);font-weight:700;color:var(--white);margin-bottom:8px}
footer .fb b span{color:var(--brand)}footer a{color:var(--dim)}footer a:hover{color:var(--accent)}
`;

export function page(cfg, title, body, flash = "", flashKind = "") {
  const fl = flash ? `<div class="flash ${flashKind}">${esc(flash)}</div>` : "";
  const ico = cfg.ico_reg ? ` · ICO registration ${esc(cfg.ico_reg)}` : "";
  const legal = cfg.company_legal_name || "SecurityCyber";
  const contact = cfg.contact_email || "securitycyber_cti@securitycyber.uk";
  return `<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="index,follow"><meta name=color-scheme content="dark">
<title>${esc(title)} · SecurityCyber CTI</title><link rel=icon href="/logo.svg">
<style>${CSS}</style></head><body><div class=wrap>
<nav><a class=brand href="/">${LOGO_SVG}<b>Security<span>Cyber</span></b></a>
<span class=links><a href="${esc(cfg.main_site)}">Main site</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a></span></nav>
${fl}${body}
<footer><div class=fb>${LOGO_SVG}<b>Security<span>Cyber</span></b> · CTI Alerts</div>
${esc(legal)}${ico} · Independent UK cyber-security advisory.<br>
<a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · <a href="/cookies">Cookies</a> ·
<a href="mailto:${esc(contact)}">${esc(contact)}</a> · <a href="${esc(cfg.main_site)}">securitycyber.uk</a></footer>
</div></body></html>`;
}

export const CHAN_META = {
  email: ["Email", "the address to send alerts to", "you@example.com"],
  discord: ["Discord", "a channel webhook URL (Server Settings → Integrations → Webhooks → New Webhook → Copy URL)", "https://discord.com/api/webhooks/…"],
  slack: ["Slack", "an incoming-webhook URL (api.slack.com/messaging/webhooks)", "https://hooks.slack.com/services/…"],
  telegram: ["Telegram", "nothing here — after payment you'll get a one-tap link to our bot", ""],
  webhook: ["Webhook", "an https:// URL we POST JSON to", "https://example.com/hook"],
  ntfy: ["ntfy", "a full topic URL", "https://ntfy.sh/my-topic"],
};
export const chanLabel = (k) => (CHAN_META[k] || [k])[0];

export function landing(cfg, flash = "", flashKind = "") {
  let tiers = "";
  for (const name of ["individual", "team"]) {
    const t = cfg.tiers[name]; if (!t) continue;
    const chans = (t.channels || []).map(chanLabel).join(", ");
    const hasM = !!t.monthly_price_id, hasW = !!t.weekly_price_id;
    let price;
    if (hasM && hasW) price = `${esc(t.monthly_display)} <span class=p2>or ${esc(t.weekly_display)}</span>`;
    else if (hasW) price = esc(t.weekly_display);
    else price = esc(t.monthly_display);
    tiers += `<div class=tier><h3>${name[0].toUpperCase() + name.slice(1)}</h3>
<div class=p1>${price}</div>
<p class=muted style="margin-top:8px">${esc(t.blurb || "")}</p>
<ul><li>Instant alerts at impact score ≥ ${esc(t.instant_min_score)}</li>
<li>Daily digest of the last 24 hours</li>
${t.attach_json ? "<li>Machine-readable JSON attached to every alert</li>" : ""}
<li>Send to: ${esc(chans)}</li></ul>
<a class=btn href="/subscribe?tier=${name}">Choose ${name[0].toUpperCase() + name.slice(1)}</a></div>`;
  }
  const body = `
<h1>Vulnerability alerts that tell you what to do</h1>
<p class=lede>We watch vendor advisories, the CISA KEV catalog and reputable security research,
cut the noise, and send you only the ones that matter for defenders — each with the CVE,
what's affected, and the single action to take.</p>
<div class=tiers>${tiers}</div>
<p class=muted>Weekly or monthly billing, cancel anytime. Pick your platform —
Discord, Slack, Telegram, email, a webhook or ntfy — during sign-up.</p>
<h2>How it works</h2>
<div class=card><p>Every few hours an automated pipeline pulls the major security feeds,
cross-references the CISA Known Exploited Vulnerabilities catalog, removes near-duplicates,
and scores each story for real-world impact. Anything that clears your threshold lands on
your chosen platform within minutes — with the CVE, CVSS, affected versions and the fix.</p>
<p class=muted>Informational only. It aggregates public advisories and is not a substitute for
your own vulnerability management. See the <a href="/terms">terms</a>.</p></div>
<h2>Already subscribed?</h2>
<div class=card><form method=post action="/account">
<label>Your email <small>— we'll send your preference link</small></label>
<input type=email name=email required placeholder="you@example.com">
<div class=row style="margin-top:12px"><button class="btn ghost">Email me my link</button></div>
</form></div>`;
  return page(cfg, "Vulnerability alerts for defenders", body, flash, flashKind);
}

export function subscribeForm(cfg, tier, flash = "", flashKind = "") {
  const t = cfg.tiers[tier];
  const allowed = (t.channels || []).filter((x) => CHAN_META[x]);
  const hasM = !!t.monthly_price_id, hasW = !!t.weekly_price_id;
  let cadOpts = "";
  for (const [cad, avail, disp] of [["monthly", hasM, t.monthly_display], ["weekly", hasW, t.weekly_display]]) {
    if (!avail) continue;
    const chk = (cad === "monthly" && hasM) || (cad === "weekly" && !hasM) ? " checked" : "";
    cadOpts += `<label><input type=radio name=cadence value="${cad}"${chk}><span>${esc(disp)}</span></label>`;
  }
  const platOpts = allowed.map((p) =>
    `<option value="${p}" data-hint="${esc(CHAN_META[p][1])}" data-ph="${esc(CHAN_META[p][2])}">${esc(CHAN_META[p][0])}</option>`).join("");
  const contact = cfg.contact_email || "securitycyber_cti@securitycyber.uk";
  const body = `
<h1>Subscribe — ${tier[0].toUpperCase() + tier.slice(1)}</h1>
<p class=lede>${esc(t.blurb || "")}</p>
<form method=post action="/subscribe" id=sf>
<input type=hidden name=tier value="${tier}">
<label>Billing</label><div class=seg>${cadOpts}</div>
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
<div class=row style="margin-top:16px"><button class=btn>Continue to payment →</button>
<a class="btn ghost" href="/">Back</a></div>
<p class=muted style="margin-top:10px">Card details are handled by Stripe — they never touch our servers.
Questions: <a href="mailto:${esc(contact)}">${esc(contact)}</a>.</p>
</form>
<script>(function(){var s=document.getElementById('plat'),h=document.getElementById('hint'),t=document.getElementById('tgt');
function u(){var o=s.options[s.selectedIndex];h.textContent=o.getAttribute('data-hint')||'';t.placeholder=o.getAttribute('data-ph')||'';
if(s.value==='telegram'){t.style.display='none';t.required=false;}else{t.style.display='';t.required=true;}}
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
    return `<tr><td>${esc(chanLabel(ch.kind))}</td><td class=muted>${esc(short(ch.target))}</td><td>${v} ${rm}</td></tr>`;
  }).join("");
  const addOpts = (tierCfg.channels || []).filter((x) => x !== "email" && x !== "telegram" && CHAN_META[x])
    .map((x) => `<option value="${x}" data-hint="${esc(CHAN_META[x][1])}" data-ph="${esc(CHAN_META[x][2])}">${esc(chanLabel(x))}</option>`).join("");
  const portal = sub.stripe_customer_id ?
    `<form method=post action="/manage/portal"><input type=hidden name=t value="${esc(tok)}">
<button class="btn ghost">Manage billing / payment method</button></form>` : "";
  const body = `
<h1>Your alert preferences</h1>
<p class=muted>Plan: <b>${esc(sub.tier[0].toUpperCase() + sub.tier.slice(1))}</b> (${esc(sub.cadence)}) ·
Status: <b>${esc(sub.status)}</b> · ${esc(sub.email)}</p>
<h2>Channels</h2>
<div class=card><table><tbody><tr><th>type</th><th>where</th><th></th></tr>${chRows}</tbody></table>
<form method=post action="/manage/channel/add" style="margin-top:12px"><input type=hidden name=t value="${esc(tok)}">
<div class=row><select name=kind id=ak style="max-width:150px">${addOpts}</select>
<input type=text name=target id=at style="flex:1;min-width:200px"><button class=btn>Add</button></div>
<p class=muted id=ah style="margin-top:8px"></p>
<p class=muted>For <b>Telegram</b>, <a href="/channel/telegram?t=${esc(tok)}">use the one-tap bot link →</a></p></form>
<script>(function(){var s=document.getElementById('ak'),h=document.getElementById('ah'),t=document.getElementById('at');
function u(){if(!s)return;var o=s.options[s.selectedIndex];h.textContent=o.getAttribute('data-hint')||'';t.placeholder=o.getAttribute('data-ph')||'';}
if(s){s.addEventListener('change',u);u();}})();</script></div>
<h2>What you get alerted about</h2>
<div class=card><form method=post action="/manage/prefs"><input type=hidden name=t value="${esc(tok)}">
<label>Vendors / keywords <small>— comma-separated. Blank = everything. e.g. Fortinet, Cisco, VMware</small></label>
<input type=text name=vendors value="${esc(p.vendors.join(", "))}">
<label>Only alert me at score ≥ <small>— blank = your plan default (${esc(tierCfg.instant_min_score)})</small></label>
<input type=text name=min_score value="${esc(p.min_score_override ?? "")}" style="max-width:120px">
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=instant ${p.instant ? "checked" : ""}> Instant alerts</label>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=digest ${p.digest ? "checked" : ""}> Daily digest</label>
<div style="margin-top:12px"><button class=btn>Save</button></div></form></div>
<h2>Account</h2>
<div class=card><div class=row>
<form method=post action="/manage/test"><input type=hidden name=t value="${esc(tok)}"><button class="btn ghost">Send me a test alert</button></form>
${portal}
<a class="btn ghost" href="/manage/export?t=${esc(tok)}">Download my data</a>
<a class="btn ghost" href="/unsubscribe?t=${esc(tok)}">Pause all alerts</a></div>
<form method=post action="/manage/delete" style="margin-top:14px"
 onsubmit="return confirm('Delete your account and all your data? If you have a paid subscription this also cancels it. This cannot be undone.')">
<input type=hidden name=t value="${esc(tok)}"><button class="btn bad">Delete my account &amp; data</button></form></div>`;
  return page(cfg, "Preferences", body, flash, flashKind);
}

export function simple(cfg, title, heading, lede, extra = "") {
  return page(cfg, title, `<h1>${esc(heading)}</h1><p class=lede>${esc(lede)}</p>${extra}`);
}
