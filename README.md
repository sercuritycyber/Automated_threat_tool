# Blue Team Podcast — Automated CTI → Social

Unattended cyber-threat-intelligence pipeline. Every few hours it pulls the major
security feeds, cross-references the CISA KEV catalog, uses **Claude Code**
(`claude -p`, no separate LLM API key) to verify and score the stories worth
telling defenders about, then drafts and publishes them to your channels —
**LinkedIn, Mastodon, Bluesky, X, or email**, any combination.

Multi-post: a quiet day is one post or none; a Patch-Tuesday meltdown goes out in
full — stories scoring ≥ 90 ignore the daily cap. Or write your own on the
**Compose** page and send it anywhere.

No Hermes. No OpenAI/Anthropic API billing. Just the `claude` CLI you already
have, driven by a systemd timer — with a local web control panel on top.

```
 systemd timer ──▶ bin/run.sh                          web/app.py  ──▶  http://127.0.0.1:8799
                     │                                  (dashboard · review · compose · settings
   1. ingest  ───────┤  bin/ingest.py                    · feeds · secrets · logs · run-now)
                     │        └─▶ state/candidates.json          ▲
   2. analyst ───────┤  claude -p prompts/analyst.md              │ reads/writes the same files
                     │        └─▶ state/decision.json  (posts[])  │
   3. publish ───────┘  bin/publish_linkedin.py ──▶ bin/publishers.py
        mode=auto   → posts straight away, up to publish.max_per_day (severe bypasses it)
        mode=review → holds every draft in state/pending.json for you to approve
```

## The web control panel

```
python3 web/app.py        # → http://127.0.0.1:8799   (localhost only, no external assets)
```

| Page | What it does |
|---|---|
| **Dashboard** | operations view: system-state banner (LIVE / DRY-RUN / ARMED) with a live countdown to the next scheduled run, stat tiles (posts today vs cap, 7-day count, avg score, queue depth), today's run slots, a 14-day activity chart, recent posts, a run-history timeline, and quick actions — **Run now**, **Fire top candidate**, **Go live / Pause**, **Clear test data**. Soft-refreshes every 60s |
| **Review queue** | every held post as its own card — edit the text and the short (X/Bluesky) version, pick which networks it goes to, then **Approve & post**, **Save**, **Rewrite**, or **Reject** per item, or **Approve all** |
| **Compose** | write a post yourself, pick networks, **Post now** or **Hold for review**. Bypasses the analyst, schedule, threshold and daily cap — it's your channel |
| **Candidates** | the current `state/candidates.json` as a table; **Draft & review** on any row hands that story to `claude -p` now and queues the draft — bypasses the schedule and the daily cap |
| **Drafts** | every generated post, archived |
| **Settings** | brand/voice, lookback, `min_score`, publish mode, **dry-run switch**, **which networks to post to**, daily cap + severity override + runaway guard, per-network options, schedule, notifications — writes `config/settings.yaml` |
| **Feeds** | add / remove / enable / disable / re-weight sources, **Test all feeds** — writes `config/feeds.yaml` |
| **Secrets** | credentials for each network (LinkedIn, Mastodon, Bluesky, X, email), write-only + masked — writes `config/env` (chmod 600); per-network **Test** button |
| **Logs** | every run log, newest tailed inline |

## Why this is better than a single `claude -p "scrape everything"` prompt

| Problem with the naive version | What this does instead |
|---|---|
| LLM "find the feeds" every run → non-reproducible, wastes tokens, misses feeds | Fixed `config/feeds.yaml`, fetched in parallel by Python |
| Dedup by `grep`-ing a text file → breaks on tracking params, same story from 5 sites reposted | URL normalisation + CVE/title clustering + `recently_posted` shown to the analyst |
| Post text passed as a shell arg → quotes/newlines/emoji corrupt the command | Analyst writes `state/decision.json`; publisher reads it. Zero shell interpolation of content |
| No objective signal, pure vibes scoring | CISA KEV catalog cross-reference (+ "added in last N days" + ransomware flag) feeds a transparent pre-score |
| Fires and posts immediately, no undo | `dry_run: true` by default — drafts are archived to `drafts/` for review before you flip the switch |
| Deprecated `/v2/ugcPosts` endpoint | Versioned `/rest/posts` API, OpenID `userinfo` for the author URN, refresh-token support |
| Silent failures | `flock` single-run lock, per-run logs in `logs/`, `state/last_run.json`, optional ntfy alerts |
| cron | systemd **user** timer: `Persistent=true` catch-up, `RandomizedDelaySec`, journald |

## Setup

```bash
cd ~/Threatintel

# 1. deps — pipeline needs requests + pyyaml (usually already there);
#    the panel also needs fastapi + uvicorn + python-multipart
python3 -m pip install --user -r requirements.txt   # or: pip install --break-system-packages -r requirements.txt

# 2. start the panel and do everything else from the browser
python3 web/app.py            # → http://127.0.0.1:8799
#   - Secrets  : paste your LinkedIn access token (or run without one — it just won't post)
#   - Settings : leave dry-run ON and mode = review for now
#   - Dashboard: click "Run pipeline now", watch Logs, review the draft

# 3. when the drafts look right: Settings → turn dry-run OFF.
#    Keep mode = review to approve each post by hand, or set it to auto.

# 4. schedule it
systemd/install.sh            # installs the 6-hourly timer AND the panel as a service
```

Prefer the CLI? `bin/run.sh` does the same thing; `config/env` + `config/settings.yaml`
hold everything the panel edits.

## LinkedIn token

1. Create an app at <https://www.linkedin.com/developers/apps>, add the
   **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**
   products.
2. Do the 3-legged OAuth flow for scopes `openid profile w_member_social`.
3. Put the access token in `config/env` as `LINKEDIN_ACCESS_TOKEN`.
   Tokens last ~60 days; add `LINKEDIN_CLIENT_ID/SECRET/REFRESH_TOKEN` and the
   publisher will refresh automatically on a 401 and write the new token back.

To post to a **company page** instead of your profile, set
`LINKEDIN_AUTHOR_URN=urn:li:organization:<id>` and use a token with
`w_organization_social`.

## Other networks

Add credentials on the **Secrets** page, then tick the network in
**Settings → Post to**. `bin/publishers.py` has one function per network.

| Network | Needs | Notes |
|---|---|---|
| **Mastodon** | instance URL + access token (Preferences → Development) | full post; `mastodon_visibility` setting |
| **Bluesky** | handle + app password (Settings → App passwords) | 300-char limit — posts the `short_text` / an auto-trimmed teaser |
| **X / Twitter** | API key/secret + access token/secret, app set to *Read and write* | OAuth 1.0a; 280-char teaser; needs a paid tier for write |
| **Email** | SMTP host/port/user/password, `EMAIL_FROM`, `EMAIL_TO` (comma list) | subject = the hook line + `email_subject_prefix`; use SES/Resend/SendGrid for volume |

Every enabled network with credentials gets each post. A network with no
credentials is skipped, not an error.

## Tuning

Everything below is in the panel (Settings / Feeds), or edit the files directly:

| Want to… | Panel | File |
|---|---|---|
| add/remove/disable sources | Feeds | `config/feeds.yaml` |
| change cadence | Settings → Schedule | `systemd/cti-harvester.timer` → `OnCalendar=` |
| raise/lower the publish bar | Settings → Min score | `config/settings.yaml` → `scoring.min_score` (65 default; 80 = KEV-only) |
| choose which networks to post to | Settings → Post to | `config/settings.yaml` → `publish.targets` (list: linkedin, mastodon, bluesky, x, email) |
| cap posts per day | Settings → Max posts per day | `config/settings.yaml` → `publish.max_per_day` (default 5; mid-tier extras held for review) |
| let a severe story ignore the cap | Settings → Severity override score | `config/settings.yaml` → `publish.severity_override_score` (default 90; a story ≥ this always posts) |
| stop a runaway analyst run | Settings → Runaway guard | `config/settings.yaml` → `publish.runaway_guard` (default 20 auto-posts/run; the rest are held) |
| push a specific story out now | Candidates → **Draft & review**, or **Compose** | — (skips the schedule, threshold and daily cap; still needs one approval) |
| widen the story window | Settings → Lookback | `config/settings.yaml` → `ingest.lookback_hours` |
| auto-post vs approve-first | Settings → Mode | `config/settings.yaml` → `publish.mode` |
| change the voice / scoring rubric | — | `prompts/analyst.md` |
| get pinged on failures | Settings → Notifications | `config/settings.yaml` → `notify.ntfy_url` |

## Operations

```bash
systemd/install.sh                 # install / update the units + panel (safe to re-run)
systemd/install.sh status          # what's running + next scheduled run
systemd/install.sh uninstall       # remove the units (keeps config/, state/, logs/)
sudo loginctl enable-linger $USER  # let the timer fire while you're logged out

systemctl --user list-timers cti-harvester.timer
systemctl --user start cti-harvester.service       # run the harvest now
journalctl --user -u cti-harvester.service -f      # follow the harvest
journalctl --user -u cti-panel.service -f          # follow the web panel
ls -t logs/ ; cat state/last_run.json              # last outcome
tail state/runs.jsonl                               # run history (feeds the dashboard)
tail state/history.jsonl                            # what's been posted
```

## Files

```
web/app.py               the control panel (FastAPI, single file, localhost only)
config/feeds.yaml        source list + per-source weight + enabled flag
config/settings.yaml     all runtime knobs (non-secret)
config/env               secrets (git-ignored, chmod 600)
lib/feedparse.py         stdlib RSS 2.0 / Atom parser
bin/ingest.py            stage 1 — deterministic feeds + KEV + dedup
prompts/analyst.md       stage 2 — the algorithm handed to `claude -p` (writes posts[])
bin/publish_linkedin.py  stage 3 — classify posts[], cap/hold logic, archive, record
bin/publishers.py        one post() per network: linkedin / mastodon / bluesky / x / email
bin/notify.py            ntfy helper
docs/RND.md              productisation / paid-tier notes (not built)
bin/runlog.py            appends a run summary to state/runs.jsonl (dashboard timeline)
bin/postlog.py           rolling-24h post counter (daily cap)
bin/lint_post.py         post-quality linter + gate
bin/run.sh               orchestrator (lock, logging, stage wiring)
systemd/                 user timer + panel service + installer
state/                   candidates.json, decision.json, pending.json, history.jsonl,
                         seen.jsonl, last_run.json
drafts/                  every generated post, kept for review
```

## Where "Hermes" lives (for reference)

Hermes wasn't on this machine. It's in **Turso**, org `hermess`, group `default`,
region `aws-eu-west-1` — databases `hermes`, `hemress-brain`, `hermessecuritycyber`.
No social-media API keys were found anywhere on the laptop (checked the CF Workers,
n8n, and the old GODSEYE notes). If the LinkedIn credentials are in one of those
Turso DBs, pull them and paste them into the panel's **Secrets** page. The Turso
group admin token is on `godseye-vm:~/godseye/claude_token_turso`.
