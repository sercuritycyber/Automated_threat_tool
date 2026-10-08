# Build your own CTI pipeline

This guide takes you from a fresh clone to your own scheduled threat-intel feed:
it reads the security news, has an AI analyst verify and score it, and drafts
(or publishes) posts for defenders. Everything beyond the core pipeline is
optional and listed in step 8.

It's free to use under the [AGPL-3.0](../LICENSE); see
[`LICENSING.md`](../LICENSING.md) for commercial use.

```
systemd timer ─▶ bin/run.sh
   1. ingest   bin/ingest.py       feeds + CISA KEV  ─▶ state/candidates.json
   2. analyst  claude / codex / …  verify + score    ─▶ state/decision.json
   3. publish  bin/publish_linkedin.py ─▶ LinkedIn · Mastodon · Bluesky · X · email
   4. (optional) push alerts to your own alert service (worker/)
web/app.py ─▶ http://127.0.0.1:8799   control panel: review, compose, settings, logs
```

## 1. What you need

- **Linux with systemd** for scheduling (user units, so no root needed).
  macOS or Windows can run the pieces by hand, but the installer assumes systemd.
- **Python 3** with `pip`.
- **An analyst engine.** At least one of these, logged in on the same machine:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code) — the `claude`
    CLI, the default engine. `systemd/install.sh` refuses to install without it.
  - OpenAI Codex CLI (ChatGPT plan), set `analyst.engine: codex`.
  - Gemini via Antigravity (`GEMINI_API_KEY`), set `analyst.engine: antigravity`.
  - Any OpenAI-compatible API (OpenRouter, OpenAI, Mistral, …) as a backup:
    add the key to `config/env` and **pin a model id** in
    `analyst.backup_providers` (blank model = provider skipped).
- **Accounts for the networks you want to post to** (step 6). None are needed
  to try it out: with no credentials it just drafts.
- **Only for the optional alert service:** a Cloudflare account, Node.js and
  `npx wrangler`.

## 2. Install

```bash
git clone https://github.com/sercuritycyber/Automated_threat_tool.git ~/Threatintel
cd ~/Threatintel
python3 -m pip install --user -r requirements.txt
```

## 3. Configure

```bash
cp config/settings.yaml.example config/settings.yaml
cp config/env.example config/env && chmod 600 config/env
```

- **`config/settings.yaml`** — behaviour. Set `brand.name`, `brand.site` and
  `brand.audience` to your own. The example ships safe: `publish.dry_run: true`
  (nothing is published) and `publish.mode: review` (every post waits for your
  approval). Change `notify.ntfy_url` to your own [ntfy](https://ntfy.sh) topic,
  or remove it.
- **`config/env`** — secrets (git-ignored, keep it `chmod 600`). Leave
  everything blank for now.
- **`config/feeds.yaml`** — the sources. Works as shipped; add or remove feeds
  any time (or use the panel's **Feeds** page).

## 4. First run (dry run)

```bash
CTI_DRY_RUN=1 bin/run.sh
```

This ingests, runs the analyst and drafts posts **without publishing anything**.
Check:

- `logs/` — the run log
- `state/candidates.json` — what was collected and pre-scored
- `state/decision.json` — what the analyst picked, with its score breakdown
- `drafts/` — the drafted posts

If the analyst stage fails, the log says which engine it tried. Make sure that
CLI is logged in for the same user.

## 5. The control panel

```bash
python3 web/app.py      # → http://127.0.0.1:8799 (binds to localhost only)
```

Review and edit drafts, compose your own posts, tune settings and feeds, paste
secrets (write-only, masked), read logs and **Run now**. It's local-only by
design: put it behind a VPN or Tailscale rather than exposing it to the internet.

## 6. Connect your networks

Add credentials on the panel's **Secrets** page (or in `config/env`), then tick
the network in **Settings → Post to**. Per-network setup (LinkedIn OAuth,
Mastodon, Bluesky, X, email) is in the main [README](../README.md#linkedin-token).
A network without credentials is skipped, not an error.

When the drafts look right, set `publish.dry_run: false`. Keep
`publish.mode: review` to approve each post by hand, or change it to post
automatically.

## 7. Schedule it

```bash
systemd/install.sh            # installs the harvester timer + the panel service
systemd/install.sh status     # check
loginctl enable-linger $USER  # keep it running when you're logged out (needs sudo on some distros)
```

Change the cadence in `systemd/cti-harvester.timer` (`OnCalendar=`) and re-run
the installer. Prefer containers? See [`deploy/README.md`](../deploy/README.md)
for the podman Quadlet setup.

## 8. Optional add-ons

| Add-on | What it does | Start here |
|---|---|---|
| **Alert service** (`worker/`) | A Cloudflare Worker + D1 that receives every alert, serves a public feed (`/api/public-feed`, `/api/alerts`), posts to a Discord channel, and can deliver per-subscriber email / RSS / Telegram / ntfy. Set `CONFIG.free_mode: true` for an open feed, or configure Stripe for a paid one. | [`worker/README.md`](../worker/README.md), then set `CTI_WORKER_URL` and `CTI_INGEST_SECRET` in `config/env`. Without them, step 4 of the run is skipped. |
| **Honeynet bridge** | Turns weekly reports from your own honeypots into one post and one alert per week. Needs a separate honeypot-reporting service producing those reports; it does nothing without them. | `bin/honeynet_bridge.py --preview` |
| **Dark-web address aggregator** (`darkweb/`) | Builds a liveness-checked list of .onion addresses for investigation. Needs Tor. CSAM blocking is fail-closed and can't be disabled. Don't publish live .onion addresses. | [`darkweb/README.md`](../darkweb/README.md) |
| **Promo autoposter** | Occasional promo posts from `bin/marketing_content.json`. Off unless `marketing.enabled: true`. | `bin/marketing_post.py` |
| **Backups, retention, emergency sweep, daily audit** | Extra systemd timers in `systemd/`. | the matching `systemd/cti-*.service` |

## 9. Make it yours

The voice, scoring rubric and post structure live in
[`prompts/analyst.md`](../prompts/analyst.md); edit freely.

Branding isn't fully config-driven yet: the SecurityCyber name and
`securitycyber.uk` / `cti.securitycyber.uk` addresses are written into several
scripts, the analyst prompt, the systemd units and the worker. Replace them with
your own:

```bash
grep -rnI "securitycyber\|SecurityCyber" bin prompts lib web systemd worker/src worker/wrangler.toml config/*.example
```

In particular, the analyst prompt and `bin/post_run_audit.py` expect each post's
plug line to end with your feed's domain, and `bin/publishers.py` keeps that
domain bare (no `https://`) on LinkedIn so the post isn't throttled.

## Safety rules the pipeline enforces (keep them)

- **Facts only.** The analyst must verify every CVE, version and exploitation
  claim against the source. Output from backup engines is checked for invented
  CVEs, and their public posts wait for approval.
- **Untrusted input.** Feed text is hostile by default; the Claude analyst runs
  with restricted tools (`config/claude-untrusted.json`).
- **No shell interpolation of content.** The analyst writes JSON and the
  publisher reads it.
- **Single run at a time** (`flock` on `state/.lock`), with per-run logs and
  `state/last_run.json`.
- **Never test by running the publisher.** `bin/publish_linkedin.py` and
  `bin/run.sh` without `CTI_DRY_RUN=1` publish for real.

## Licence

AGPL-3.0, or a commercial licence from the author. See
[`LICENSING.md`](../LICENSING.md). Contributions:
[`CONTRIBUTING.md`](../CONTRIBUTING.md).
