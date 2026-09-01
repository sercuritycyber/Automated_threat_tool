# Blue Team Podcast — Automated CTI → LinkedIn

Unattended cyber-threat-intelligence pipeline. Every 6 hours it pulls the major
security feeds, cross-references the CISA KEV catalog, uses **Claude Code**
(`claude -p`, no separate LLM API key) to verify and score the top stories, and
— if something clears the impact bar — drafts and publishes a defender-focused
LinkedIn post.

No Hermes. No OpenAI/Anthropic API billing. Just the `claude` CLI you already
have, driven by a systemd timer.

```
 systemd timer ──▶ bin/run.sh
                     │
   1. ingest  ───────┤  bin/ingest.py      deterministic: feeds + KEV + dedup + pre-score
                     │        └─▶ state/candidates.json
   2. analyst ───────┤  claude -p prompts/analyst.md   verify (WebFetch) + score + draft
                     │        └─▶ state/decision.json
   3. publish ───────┘  bin/publish_linkedin.py        archive draft, post, update state
                              └─▶ drafts/*.md , state/history.jsonl , state/seen.jsonl
```

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

# 1. python deps (requests + pyyaml already present; nothing else needed —
#    the RSS parser is stdlib-only in lib/feedparse.py)
python3 -c 'import requests, yaml'      # should be silent

# 2. secrets
cp config/env.example config/env && chmod 600 config/env
#    edit config/env — at minimum LINKEDIN_ACCESS_TOKEN (scope: w_member_social,
#    plus openid+profile if you want the author URN auto-detected)

# 3. dry run it once (no posting, writes a draft)
bin/run.sh
cat drafts/*.md | tail -n 40
cat state/decision.json

# 4. when the drafts look right, go live
#    edit config/settings.yaml -> publish.dry_run: false   (or CTI_DRY_RUN=false in config/env)

# 5. schedule it
systemd/install.sh
```

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

## Tuning

| Want to… | Edit |
|---|---|
| add/remove sources | `config/feeds.yaml` |
| change cadence | `systemd/cti-harvester.timer` → `OnCalendar=` |
| raise/lower the publish bar | `config/settings.yaml` → `scoring.min_score` |
| widen the story window | `config/settings.yaml` → `ingest.lookback_hours` |
| change the voice / rubric | `prompts/analyst.md` |
| get pinged on failures | `config/settings.yaml` → `notify.ntfy_url` |

## Operations

```bash
systemctl --user list-timers cti-harvester.timer
systemctl --user start cti-harvester.service      # run now
journalctl --user -u cti-harvester.service -f     # follow
ls -t logs/ ; cat state/last_run.json             # last outcome
tail state/history.jsonl                           # what's been posted
```

## Files

```
config/feeds.yaml        source list + per-source weight
config/settings.yaml     all runtime knobs (non-secret)
config/env               secrets (git-ignored)
lib/feedparse.py         stdlib RSS 2.0 / Atom parser
bin/ingest.py            stage 1 — deterministic
prompts/analyst.md       stage 2 — the algorithm handed to `claude -p`
bin/publish_linkedin.py  stage 3 — archive + post + record
bin/notify.py            ntfy helper
bin/run.sh               orchestrator (lock, logging, stage wiring)
systemd/                 user timer + installer
state/                   candidates.json, decision.json, history.jsonl, seen.jsonl, last_run.json
drafts/                  every generated post, kept for review
```
