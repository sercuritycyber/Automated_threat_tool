// Discord membership + delivery for the SecurityCyber server.
//
// Why this shape:
//   Discord gives a bot no way to find someone by username, and there is no
//   "send an add request" API. The supported way to put a person in your
//   server is OAuth2 with the `guilds.join` scope: they approve once, and the
//   bot adds them with PUT /guilds/{guild}/members/{user}. That is a click
//   instead of typing a username, and it hands us a verified user id.
//
//   Alerts then go out as bot DMs, not to a shared channel. A shared channel
//   would send every member the same firehose — it cannot honour a
//   subscriber's score threshold or vendor filter. DMs keep all of that, and
//   a bot may DM anyone who shares a guild with it, which membership
//   guarantees.
//
//   Access follows payment: when a subscription lapses we drop the subscriber
//   role (or remove them, if configured), the same way the RSS feed stops.

const API = "https://discord.com/api/v10";
const UA = "SecurityCyber-CTI/1.0 (+https://securitycyber.uk)";

export class DiscordError extends Error {}

// Everything we need to run the membership flow at all.
export function discordConfigured(env) {
  return !!(env.DISCORD_CLIENT_ID && env.DISCORD_CLIENT_SECRET
    && env.DISCORD_BOT_TOKEN && env.DISCORD_GUILD_ID);
}

async function api(env, method, path, body, { asBot = true, token = "" } = {}) {
  const auth = asBot ? `Bot ${env.DISCORD_BOT_TOKEN}` : `Bearer ${token}`;
  let r;
  try {
    r = await fetch(`${API}${path}`, {
      method,
      headers: {
        Authorization: auth,
        "Content-Type": "application/json",
        "User-Agent": UA,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) { throw new DiscordError(`network: ${e}`); }
  if (r.status === 204) return {};
  const text = await r.text();
  let j = {};
  try { j = text ? JSON.parse(text) : {}; } catch { /* non-JSON */ }
  if (r.status >= 400) throw new DiscordError(`HTTP ${r.status}: ${(j.message || text || "").slice(0, 200)}`);
  return j;
}

// Step 1 — where we send the subscriber to approve.
// `state` ties the callback back to the subscriber; it must be unguessable.
export function authorizeUrl(env, redirectUri, state) {
  const u = new URL("https://discord.com/oauth2/authorize");
  u.searchParams.set("client_id", env.DISCORD_CLIENT_ID);
  u.searchParams.set("redirect_uri", redirectUri);
  u.searchParams.set("response_type", "code");
  // identify = who they are; guilds.join = let the bot add them.
  u.searchParams.set("scope", "identify guilds.join");
  u.searchParams.set("state", state);
  u.searchParams.set("prompt", "consent");
  return u.toString();
}

// Step 2 — swap the callback code for a user access token.
export async function exchangeCode(env, code, redirectUri) {
  const body = new URLSearchParams({
    client_id: env.DISCORD_CLIENT_ID,
    client_secret: env.DISCORD_CLIENT_SECRET,
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
  });
  let r;
  try {
    r = await fetch(`${API}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA },
      body,
    });
  } catch (e) { throw new DiscordError(`network: ${e}`); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new DiscordError(`token exchange failed: ${j.error_description || j.error || r.status}`);
  return j.access_token;
}

export async function currentUser(env, accessToken) {
  const u = await api(env, "GET", "/users/@me", undefined, { asBot: false, token: accessToken });
  return { id: u.id, username: u.username, global_name: u.global_name || u.username };
}

// Step 3 — put them in the server. Idempotent: Discord answers 204 with no
// body when they were already a member, which we treat as success.
export async function addToGuild(env, userId, accessToken) {
  const payload = { access_token: accessToken };
  if (env.DISCORD_SUBSCRIBER_ROLE_ID) payload.roles = [env.DISCORD_SUBSCRIBER_ROLE_ID];
  await api(env, "PUT", `/guilds/${env.DISCORD_GUILD_ID}/members/${userId}`, payload);
  return true;
}

// Access control, mirroring how a lapsed RSS feed stops carrying alerts.
export async function grantSubscriberRole(env, userId) {
  if (!env.DISCORD_SUBSCRIBER_ROLE_ID) return false;
  await api(env, "PUT", `/guilds/${env.DISCORD_GUILD_ID}/members/${userId}/roles/${env.DISCORD_SUBSCRIBER_ROLE_ID}`);
  return true;
}
export async function revokeSubscriberRole(env, userId) {
  if (!env.DISCORD_SUBSCRIBER_ROLE_ID) return false;
  await api(env, "DELETE", `/guilds/${env.DISCORD_GUILD_ID}/members/${userId}/roles/${env.DISCORD_SUBSCRIBER_ROLE_ID}`);
  return true;
}
export async function removeFromGuild(env, userId) {
  await api(env, "DELETE", `/guilds/${env.DISCORD_GUILD_ID}/members/${userId}`);
  return true;
}

// Delivery — a DM, so each subscriber's own threshold and vendor filter still
// apply. Opening a DM channel is cheap and Discord dedupes it per recipient.
export async function dm(env, userId, content) {
  const ch = await api(env, "POST", "/users/@me/channels", { recipient_id: userId });
  if (!ch.id) throw new DiscordError("could not open a DM channel");
  const msg = await api(env, "POST", `/channels/${ch.id}/messages`, { content });
  return msg.id || "";
}
