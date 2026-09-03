// Small helpers: HTML escaping, responses, client IP, in-memory rate limiting.

export function esc(x) {
  return String(x == null ? "" : x)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
  "Content-Security-Policy":
    "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; script-src 'self' 'unsafe-inline'; " +
    // Stripe hosted Checkout (API sessions) + Stripe Payment Links (buy.stripe.com):
    // "Continue to payment" is a POST to /subscribe that 303-redirects to one of
    // these, and some browsers still check form-action against the redirect target.
    "form-action 'self' https://checkout.stripe.com https://buy.stripe.com; frame-ancestors 'none'; base-uri 'none'",
};

export function html(body, status = 200, extra = {}) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", ...SECURITY_HEADERS, ...extra },
  });
}

export function text(body, status = 200, extra = {}) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", ...SECURITY_HEADERS, ...extra },
  });
}

export function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...SECURITY_HEADERS, ...extra },
  });
}

export function redirect(location, status = 303) {
  return new Response(null, { status, headers: { Location: location, ...SECURITY_HEADERS } });
}

export function q(s) {
  return encodeURIComponent(String(s == null ? "" : s));
}

export function clientIp(request) {
  return request.headers.get("cf-connecting-ip") ||
    (request.headers.get("x-forwarded-for") || "").split(",")[0].trim() || "?";
}

// in-memory fixed-window limiter (per isolate — best-effort, like the main site)
const buckets = new Map();
export function rateLimit(key, limit, windowMs = 60000) {
  const now = Date.now();
  let b = buckets.get(key);
  if (!b || now > b.reset) b = { count: 0, reset: now + windowMs };
  b.count++;
  buckets.set(key, b);
  if (buckets.size > 5000) for (const [k, v] of buckets) if (now > v.reset) buckets.delete(k);
  return { ok: b.count <= limit, retryAfter: Math.ceil((b.reset - now) / 1000) };
}

export function nowIso() {
  return new Date().toISOString();
}

export function isoDaysAgo(days) {
  return new Date(Date.now() - days * 86400000).toISOString();
}

// tiny markdown -> HTML (headings, para, ul, links, bold, code, hr, tables)
export function mdToHtml(src) {
  const out = [];
  let inUl = false, inTbl = false;
  const inline = (s) =>
    esc(s)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" rel="noopener">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const raw of src.split("\n")) {
    const ln = raw.replace(/\s+$/, "");
    if (inUl && !/^[-*] /.test(ln)) { out.push("</ul>"); inUl = false; }
    if (inTbl && !ln.includes("|")) { out.push("</tbody></table>"); inTbl = false; }
    if (!ln) continue;
    if (ln.startsWith("### ")) out.push(`<h3>${inline(ln.slice(4))}</h3>`);
    else if (ln.startsWith("## ")) out.push(`<h2>${inline(ln.slice(3))}</h2>`);
    else if (ln.startsWith("# ")) out.push(`<h1>${inline(ln.slice(2))}</h1>`);
    else if (ln.startsWith("---")) out.push("<hr>");
    else if (/^[-*] /.test(ln)) { if (!inUl) { out.push("<ul>"); inUl = true; } out.push(`<li>${inline(ln.slice(2))}</li>`); }
    else if (ln.includes("|")) {
      const cells = ln.trim().replace(/^\||\|$/g, "").split("|").map((s) => s.trim());
      if (/^[-: ]+$/.test(cells.join(""))) continue;
      if (!inTbl) { out.push("<table><tbody>"); inTbl = true; }
      out.push("<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>");
    } else out.push(`<p>${inline(ln)}</p>`);
  }
  if (inUl) out.push("</ul>");
  if (inTbl) out.push("</tbody></table>");
  return out.join("\n");
}
