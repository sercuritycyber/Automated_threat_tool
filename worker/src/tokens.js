// Signed, expiring, action-scoped URL tokens (Web Crypto HMAC-SHA256).
//   const t = await sign(env, { sub: 42, act: "manage" }, 90*86400);
//   const data = await verify(env, t, "manage");   // throws BadToken

export class BadToken extends Error {}

function b64uEncode(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64uDecode(str) {
  const s = str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4);
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function key(env) {
  const secret = env.SUBS_TOKEN_SECRET;
  if (!secret) throw new Error("SUBS_TOKEN_SECRET not set");
  return crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function sign(env, payload, ttlSec = 7 * 86400) {
  const body = { ...payload, exp: Math.floor(Date.now() / 1000) + Math.floor(ttlSec) };
  const p = b64uEncode(new TextEncoder().encode(JSON.stringify(body)));
  const mac = await crypto.subtle.sign("HMAC", await key(env), new TextEncoder().encode(p));
  return `${p}.${b64uEncode(new Uint8Array(mac))}`;
}

export async function verify(env, token, expectAct = null) {
  if (typeof token !== "string" || (token.match(/\./g) || []).length !== 1)
    throw new BadToken("malformed token");
  const [p, sig] = token.split(".");
  const ok = await crypto.subtle.verify(
    "HMAC", await key(env), b64uDecode(sig), new TextEncoder().encode(p));
  if (!ok) throw new BadToken("bad signature");
  let data;
  try { data = JSON.parse(new TextDecoder().decode(b64uDecode(p))); }
  catch { throw new BadToken("bad payload"); }
  if ((data.exp || 0) < Math.floor(Date.now() / 1000)) throw new BadToken("expired");
  if (expectAct != null && data.act !== expectAct) throw new BadToken("wrong action");
  return data;
}

export function newId(nbytes = 18) {
  return b64uEncode(crypto.getRandomValues(new Uint8Array(nbytes)));
}

// constant-time compare for bearer tokens / shared secrets
export function timingSafeEqual(a, b) {
  const ab = new TextEncoder().encode(a || "");
  const bb = new TextEncoder().encode(b || "");
  if (ab.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ab.length; i++) diff |= ab[i] ^ bb[i];
  return diff === 0;
}

export { b64uEncode, b64uDecode };
