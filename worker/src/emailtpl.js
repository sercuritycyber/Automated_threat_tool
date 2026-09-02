// HTML email for a threat alert. Adapted from the SecurityCyber template in
// gemini-code-1788380060390.html (repo root) — same look, made dynamic.
//
// Email clients strip <style> unreliably and ignore most modern CSS, so this
// uses a table layout with inline styles and no flexbox/grid. Every value is
// escaped: alert text is analyst-written but passes through here as data.

import { esc } from "./util.js";

const C = {
  bg: "#0b1120", card: "#151f32", border: "#24344d",
  text: "#f1f5f9", muted: "#94a3b8", red: "#ef4444",
  badgeCritBg: "#7f1d1d", badgeCritTx: "#fca5a5",
  badgeKevBg: "#991b1b", badgeKevTx: "#fecaca",
};

// splits "line one\nline two" into <p> paragraphs, escaped
function paras(txt) {
  return String(txt || "").split(/\n{2,}/).map((p) =>
    `<p style="margin:0 0 12px">${esc(p.trim()).replace(/\n/g, "<br>")}</p>`).join("");
}

function badge(text, bg, tx) {
  return `<span style="display:inline-block;font-size:11px;font-weight:700;padding:4px 10px;`
    + `border-radius:4px;text-transform:uppercase;letter-spacing:.5px;background:${bg};color:${tx};`
    + `margin-right:6px">${esc(text)}</span>`;
}

// alert: { title, url, source, published, score, cve_ids[], action, subscriber_text|body, kev }
// links: { manage, unsubscribe }
export function alertEmailHtml(cfg, alert, links = {}) {
  const score = Number(alert.score ?? 0);
  const sev = score >= 90 ? "CRITICAL" : score >= 70 ? "HIGH" : score >= 40 ? "MODERATE" : "ADVISORY";
  const badges = [badge(sev, C.badgeCritBg, C.badgeCritTx)];
  if (alert.kev || /\bKEV\b/i.test(alert.source || "")) badges.push(badge("CISA KEV", C.badgeKevBg, C.badgeKevTx));
  const cves = (alert.cve_ids || []).filter(Boolean);

  const grid = [
    ["Impact score", `${score}/100`],
    ["Severity", sev],
    cves.length ? ["CVE", cves.join(", ")] : null,
    alert.source ? ["Source", alert.source] : null,
  ].filter(Boolean);

  let gridRows = "";
  for (let i = 0; i < grid.length; i += 2) {
    const cells = grid.slice(i, i + 2).map(([k, v]) =>
      `<td width="50%" style="padding:6px" valign="top"><table width="100%" cellpadding="0" cellspacing="0"
         style="background:${C.card};border:1px solid ${C.border};border-radius:6px"><tr><td style="padding:12px 16px">
         <div style="font-size:11px;color:${C.muted};text-transform:uppercase;margin-bottom:4px">${esc(k)}</div>
         <div style="font-size:14px;font-weight:600;color:${C.text}">${esc(v)}</div>
       </td></tr></table></td>`).join("");
    gridRows += `<tr>${cells}${grid.slice(i, i + 2).length === 1 ? '<td width="50%"></td>' : ""}</tr>`;
  }

  const body = (alert.subscriber_text || alert.body || alert.post_text || "").trim();
  const action = (alert.action || "").trim();
  const manage = links.manage || `${cfg.site_url}/`;
  const unsub = links.unsubscribe || "";
  const meta = [alert.published, alert.source].filter(Boolean).join("  ·  ");

  return `<!doctype html><html><body style="margin:0;padding:20px;background:${C.bg};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:${C.text}">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;margin:0 auto;background:${C.bg};
  border:1px solid ${C.border};border-radius:8px">
  <tr><td style="padding:24px 32px;background:#080d18;border-bottom:2px solid ${C.red}">
    <table width="100%"><tr>
      <td style="font-size:24px;font-weight:800;color:#fff">Security<span style="color:${C.red}">Cyber</span></td>
      <td align="right" style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:${C.muted};font-weight:600">
        ${esc(cfg.brand || "SecurityCyber")} CTI</td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:22px 32px;border-bottom:1px solid ${C.border};
    background:linear-gradient(180deg,rgba(239,68,68,.10) 0%,rgba(21,31,50,0) 100%)">
    <div style="margin-bottom:12px">${badges.join("")}</div>
    <h1 style="font-size:20px;font-weight:700;margin:0 0 8px;line-height:1.3;color:#fff">${esc(alert.title || "Threat alert")}</h1>
    ${meta ? `<p style="font-size:13px;color:${C.muted};margin:0">${esc(meta)}</p>` : ""}
  </td></tr>

  <tr><td style="padding:24px 32px">
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 -6px 12px">${gridRows}</table>

    ${action ? `<div style="font-size:13px;text-transform:uppercase;letter-spacing:1px;color:${C.red};
      font-weight:700;margin:16px 0 8px">Do this now</div>
      <div style="background:${C.card};border-left:3px solid ${C.red};padding:14px 16px;border-radius:0 6px 6px 0;
      font-size:14px;line-height:1.6;color:#cbd5e1;margin-bottom:20px">${esc(action)}</div>` : ""}

    <div style="font-size:13px;text-transform:uppercase;letter-spacing:1px;color:${C.red};
      font-weight:700;margin:16px 0 8px">Analyst brief</div>
    <div style="background:${C.card};border-left:3px solid ${C.red};padding:16px;border-radius:0 6px 6px 0;
      font-size:14px;line-height:1.6;color:#cbd5e1;margin-bottom:20px">${paras(body)}</div>

    ${alert.url ? `<div style="margin-top:20px"><a href="${esc(alert.url)}" style="display:inline-block;
      background:${C.red};color:#fff;text-decoration:none;font-weight:600;font-size:14px;padding:12px 24px;
      border-radius:6px">Read the source →</a></div>` : ""}
  </td></tr>

  <tr><td style="padding:22px 32px;background:#080d18;border-top:1px solid ${C.border};text-align:center">
    <p style="font-size:11px;color:#64748b;margin:0;line-height:1.6">
      ${esc(cfg.brand || "SecurityCyber")} · Independent UK blue-team security advisory<br>
      Informational only — an aggregation of public advisories, not professional advice.<br>
      <a href="${esc(manage)}" style="color:${C.muted}">Preferences</a>
      ${unsub ? ` · <a href="${esc(unsub)}" style="color:${C.muted}">Unsubscribe</a>` : ""}
    </p>
  </td></tr>
</table>
</body></html>`;
}

// Plain-text fallback (every HTML email needs one).
export function alertEmailText(cfg, alert, links = {}) {
  const lines = [];
  lines.push(alert.title || "Threat alert");
  if (alert.score != null) lines.push(`Impact: ${alert.score}/100`);
  if ((alert.cve_ids || []).length) lines.push(`CVE: ${alert.cve_ids.join(", ")}`);
  lines.push("");
  if ((alert.action || "").trim()) { lines.push(`DO NOW: ${alert.action.trim()}`); lines.push(""); }
  lines.push((alert.subscriber_text || alert.body || alert.post_text || "").trim());
  if (alert.url) { lines.push(""); lines.push(`Source: ${alert.url}`); }
  lines.push("");
  lines.push(`— ${cfg.brand || "SecurityCyber"} CTI`);
  if (links.manage) lines.push(`Preferences: ${links.manage}`);
  if (links.unsubscribe) lines.push(`Unsubscribe: ${links.unsubscribe}`);
  return lines.join("\n");
}
