# Build Report — SecurityCyber Admin Center & Marketing System

| | |
|---|---|
| **Report ID** | BR-2026-09-03-01 |
| **Date** | 2026-09-03 |
| **Author** | Claude Sonnet 5 (session `01DeiiZ6gU4KwZcDPSsG3uzx`) |
| **Environment** | Production VM `192.168.0.112` / `securitycybercti` (Fedora Cloud 44) |
| **Repository** | `github.com/sercuritycyber/Automated_threat_tool` — branch `master` |
| **Component** | `web/app.py` (control panel) · `bin/marketing_desk.py` (new) · `bin/publishers.py` |
| **Status** | ✅ SIGNED OFF — all checks pass, no known errors |

---

## 1. Scope

Turn the local operator panel from a maintenance dashboard into an **Admin Center**:
a single interactive system for running the CTI pipeline *and* managing the
marketing output it produces — compose, format, schedule, hand off to the
publishing system, or publish directly. Built as a product surface, not a
collection of scripts: one navigation, one design language, one persistence
layer, reversible actions, and a clean hand-off contract to the existing
pipeline.

## 2. What was delivered

### 2.1 Admin Center (`web/app.py`)
- Rebranded the panel **"SecurityCyber · Admin Center"** (browser title, sidebar,
  boot banner). Same FastAPI app, same localhost/Tailscale binding, same
  `cti-panel.service` unit — no operational change, no new dependency.
- New **Marketing** section in the primary navigation.
- Six JSON/HTML endpoints added under `/marketing`, all localhost-only,
  reusing the panel's existing auth boundary and `load_env()` credential path.

### 2.2 Marketing Desk (`bin/marketing_desk.py`, new module — 380 LOC)
| Capability | Detail |
|---|---|
| **Content pipeline** | Pulls every draft post from the local repo — `drafts/*.md`, `state/pending.json`, `state/decision.json` — deduplicated by source URL, newest first, priority-ordered (operator queue > latest analyst decision > draft history). |
| **Per-channel formatting** | LinkedIn, X, Bluesky, Mastodon, Threads, Instagram, Facebook. Hard character limits enforced and shown live; hashtags stripped where they do not belong; `#SECURITYCYBERUK` company plug appended on LinkedIn/Facebook; `short_text` substituted for the 280/300-char networks. |
| **Compose → hand-off** | Operator writes a post (headline, body, source URL, short version, schedule). **"Hand to the system"** appends it to `state/pending.json` — the same queue the Review page and `publish_linkedin.py --approve` consume. **"Publish to LinkedIn now"** posts straight to the company page and records it to `state/history.jsonl`. CVE ids auto-extracted. |
| **Editorial state** | Per-post status (draft / queued / scheduled / approved / posted / skip), schedule datetime, notes, and an editable base text — persisted to `state/marketing.json`. Inline edits re-derive every channel format. |
| **Performance panel** | Local content counts by status, items scheduled in the next 7 days, **live subscriber numbers from the CTI Worker `/admin` API**, and manual marketing KPIs (followers, impressions, leads, CAC, MRR). |
| **Budget** | Monthly figure, spend line-items, computed spent / remaining. |
| **Export** | One-click **upload pack**: a single JSON with every post rendered for every channel plus metadata — ready to feed an uploader or hand to an agency. |
| **Per-post actions** | Copy to clipboard, download `.txt` per channel. |

### 2.3 LinkedIn publishing (`bin/publishers.py`, `config/env`)
- LinkedIn author reverted to the **company page**
  (`urn:li:organization:119404462`) now that a valid token with
  `w_organization_social` is in place (LinkedIn app `78x51cb56q47bf`).
- Every LinkedIn post carries the **`#SECURITYCYBERUK`** company plug
  (`_li_with_plug()` — applied once, skipped if it would breach the char cap).
- Backlog of 4 unposted advisories published to the company page, spaced
  5 minutes apart, recorded to `state/history.jsonl`.

## 3. Verification & sign-off

| Check | Result |
|---|---|
| `python3 -m py_compile web/app.py` | ✅ pass |
| `python3 -c "import ast; ast.parse(...)"` on `bin/marketing_desk.py` | ✅ pass |
| `publishers.py` byte-compile + `_li_with_plug` / `_li_author` unit check | ✅ author → `urn:li:organization:119404462`; plug renders `#SECURITYCYBERUK` |
| Worker suite `npm test` (run.mjs + db.mjs) | ✅ pass (unchanged this build; last run green) |
| `cti-panel.service` restart | ✅ `active` |
| `GET /`, `/marketing`, `/marketing/api/{posts,kpis,bundle}` | ✅ all `200` |
| `POST /marketing/api/compose` (queue mode) | ✅ `{"queued": true}`, item landed in `state/pending.json`, then removed (QA cleanup) |
| LinkedIn org post — live create + delete probe | ✅ `201` create, `204` delete |
| Backlog post 1 (SonicWall SMA1000) | ✅ live: `linkedin.com/feed/update/urn:li:share:7501376939199397888` |
| `journalctl -u cti-panel` since restart | ✅ no errors / tracebacks |
| Test data left behind | ✅ none — QA post and probe posts removed |

**Sign-off:** the Admin Center and Marketing system build passes all checks
with no known errors and is live on the production VM.

## 4. Data & contracts

- **New persistence:** `state/marketing.json` — `{posts, kpis, budget, composed}`.
  Local file, same directory the CLI pipeline already uses. No schema migration.
- **Hand-off contract:** composed posts enter `state/pending.json` `queue[]` in
  the exact shape the pipeline already expects (`title, url, post_text,
  short_text, score, cve_ids, held_because`). No pipeline code changed to
  accept them.
- **Reversibility:** every write is a local JSON file; no destructive action.
  `config/env` change backed up to `config/env.bak.<epoch>`.

## 5. Commits (this build)

| Commit | Summary |
|---|---|
| `01986ca` | panel: Marketing Desk — content console wired into the dashboard |
| `746315b` | publishers: plug `#SECURITYCYBERUK` on LinkedIn posts |
| `<this>` | panel: Admin Center rebrand + compose→hand-off + build report |

(Context commits earlier the same day: `bb52595` £0 free-plan billing fix,
`11dc0c5` worker access-window + lifecycle cron, `f71fbbe` Discord/Telegram
connect email, `1ae5ee8` interim personal-profile LinkedIn.)

## 6. Follow-ups (not blocking)

1. Deploy laptop SSH fix so the laptop checkout can push again (currently VM is
   the only push path).
2. Site repo (`SecurityCyber.uk`): merge `cti-page` → `main`; clean the broken
   favicon files in `public/` before the next site deploy.
3. ICO registration before onboarding paying customers (legal).
4. Rotate the exposed credentials in this session's transcript once stable.
5. Marketing Desk: add an outbound scheduler that auto-publishes scheduled
   posts at their slot (currently schedule is advisory — the operator publishes).

---

## Revision 2 — Operations Console (2026-09-03, later)

**Change:** the dashboard home (`/`) is now **Operations** — a live command
surface, not a static render.

| Added | Detail |
|---|---|
| **Command bar** | Run pipeline · Arm/Pause posting · Approve next queued (posts the top hold-queue item) · Sync Stripe links (calls the worker endpoint) · Marketing desk · Review queue — each fires inline with a toast, no page navigation. |
| **Live status strip** | Polls `GET /api/pulse` every 20 s: pipeline state + last/next run, LinkedIn (posts today / this week, target profile, **access-token days remaining**), subscribers (active / pending), alerts sent 24 h, hold-queue depth. Cells colour by health. |
| **Needs-attention panel** | Surfaces: last run errored, LinkedIn token expiring < 14 d, no network configured, paid subscriber never confirmed, posts waiting in the hold queue, delivery-failed posts queued for manual. Shows "✓ Nothing needs your attention" when clear. |
| New module | `bin/admin_center.py` — `pulse()` aggregator (5-min cached LinkedIn token introspection) + `console_head()` renderer. |
| New routes | `GET /api/pulse`, `POST /action/approve-next`, `POST /action/sync-stripe-links`. |

### Incident during deploy (resolved)
- First attempt placed `admin_center.py` in `web/` (not on `sys.path`) → panel
  crash-looped `ModuleNotFoundError`. Moved to `bin/` (same as
  `marketing_desk.py`).
- Port `8799` was then held by an **orphaned `pasta` network helper** (leftover
  from an earlier container port-forward test), so uvicorn could not bind and
  systemd hit its restart limit. Killed the orphan (pid 261319), reset the
  failed unit, restarted clean.
- **No data affected.** Cleared one stale `state/manual_post.md` (the Elementor
  advisory whose delivery failed before the org token existed — since posted).

### Verification (Rev 2)
| Check | Result |
|---|---|
| `ast.parse` `bin/admin_center.py`, `py_compile` `web/app.py` | ✅ pass |
| Panel service | ✅ `active`, `Application startup complete`, 0 errors post-restart |
| `GET /` , `/api/pulse` , `/marketing` | ✅ `200` |
| `/api/pulse` payload | ✅ pipeline=live, LinkedIn=6 posts/24h · token 59 d left · target "company page", 1 active subscriber, queue 0 |
| Console head rendered into `/` | ✅ command bar + strip + attention panel present |
| Backlog (4 advisories) | ✅ all live on the company page: SonicWall, JFrog, IT-support, Switchvox |

**Sign-off (Rev 2):** Operations Console live on the production VM, all checks
pass, no known errors. Commits `01986ca` → `eb253f8`.
