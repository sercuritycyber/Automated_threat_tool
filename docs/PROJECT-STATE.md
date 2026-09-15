# SecurityCyber CTI — project state & handoff

**For:** another Claude Code CLI (or Antigravity) picking up work on this VM.
**Written:** 2026-09-03 by the Claude session that did the build-out.
**Repo:** `git@github.com:sercuritycyber/Automated_threat_tool.git` — cloned at
`~/Threatintel` on this VM (`192.168.0.112`, `Securitycybercti`, Fedora 44).

---

## 1. What this project is

Two things that share a codebase:

1. **CTI pipeline** (`bin/`, `prompts/`, `lib/`) — every 4h: pull ~19 security
   feeds + CISA KEV → `bin/ingest.py` scores + clusters → `claude -p` analyst
   (`prompts/analyst.md`) writes `state/decision.json` with `posts[]` (social)
   and `alerts[]` (paid subscriber briefs) → `bin/publish_linkedin.py` posts to
   socials, `bin/push_to_worker.py` sends `alerts[]` to the Cloudflare Worker.
   Fallback: `bin/analyst_fallback.py` runs if `claude -p` fails (token limit).

2. **Paid CTI alert subscription service** — a **Cloudflare Worker** at
   `worker/` (route `cti.securitycyber.uk`), NOT on this VM. D1 database,
   Resend email, Stripe Payment Links, per-subscriber RSS feeds, Discord,
   Telegram, ntfy, webhook, Slack. Deployed with `npx wrangler deploy` from
   `worker/`. The pipeline feeds it; it owns all subscriber state + delivery.

3. **Owner dashboard** (`web/app.py`) — FastAPI, `127.0.0.1:8799`, this VM.
   `/service` page shows Worker health, secrets, subscribers, blockers.

---

## 2. This VM — production host (as of 2026-09-03)

| | |
|---|---|
| Access | `ssh securitycyber@192.168.0.112` (key `ssh-key-2026-09-01.key`, was in repo root, now gitignored) |
| OS | Fedora Cloud 44, kernel 6.19, glibc 2.43, **5 GB disk (~3 GB free)**, 4.7 GB RAM, 8 vCPU |
| Runtime | podman 5.8, crun 1.27, **no docker** (podman-docker not installed) |
| Python | 3.14 system; pipeline uses `~/Threatintel/.venv` |
| claude CLI | `~/.local/bin/claude` (2.1.259), OAuth-authed (`~/.claude.json`) |

### 2026-09-03: MIGRATION COMPLETE — VM is sole production

- Verified harvest run on the VM: `decision=post`, real analyst
  (`fallback=None`), 12 min. Correctly skipped SonicWall/Switchvox/JFrog
  (already in `history.jsonl`), found 1 genuinely-new post-worthy item, 25
  subscriber alerts.
- **`push_to_worker` → 200 `{"received":25}`** — Charlie fixed the
  INGEST_SECRET. Worker `posts_log` now has the alerts with full
  `subscriber_text`. **Subscriber delivery works end to end.**
- **Laptop `cti-harvester.timer` + `cti-panel.service`: STOPPED and disabled.**
  Laptop repo stays as cold backup.
- Timer on the VM: next run 08:02 UTC, every 4h, linger on.

**LinkedIn caveat:** that run posted ONE item to Charlie's **personal**
LinkedIn (`urn:li:share:7501177104982704128`) — `publish.dry_run` was `false`
and `LINKEDIN_AUTHOR_URN` in `config/env` is still the person URN. Re-set
`dry_run: true` on the VM. Un-pause only after the org URN + Community
Management API scope (§4.6).

### 2026-09-03: VM RECOVERED — moved onto the 100 GB disk

Charlie rebooted the VM (cleared the read-only — the btrfs I/O error was
transient; `write_io_errs` stopped climbing, no new errors since boot) and
added a **100 GB disk `/dev/sdb`, ext4, mounted `/mnt/data`**.

Moved onto `/mnt/data` so the 5 GB root can't fill or take the pipeline down
again (all via rsync + symlink, verified):
  - `~/.local` (claude CLI, ~1 GB) → `/mnt/data/home/.local`
  - `~/.claude` + `~/.claude.json` → `/mnt/data/home/`
  - `~/Threatintel/{state,logs,drafts}` → `/mnt/data/threatintel/`
  - podman storage graphroot → `/mnt/data/containers/storage`
    (`~/.config/containers/storage.conf`)
Root is back to ~2.3 GB used / 2.3 GB free. `history.jsonl` (8 lines) intact.

Harvester timer + panel **re-enabled on the VM**, linger on, folder-trust
re-asserted. A verification harvest is running.

**Container build now has room** — retry once this run confirms clean
(still needs the SELinux fix, PROJECT.md §3).

### 2026-09-03: VM ROOT FS FAILED (resolved above)

Minutes after cutover the VM's btrfs root went **read-only** from disk I/O
errors:

    BTRFS: error (device sda3) in write_all_supers: errno=-5 IO failure
    BTRFS info: forced readonly
    [/dev/sda3].write_io_errs  16
    remounting read-write after error is not allowed

Not a space issue (51% used). The virtual disk or the Proxmox storage behind
it is failing writes. **The VM pipeline is dead until this is fixed** —
`sudo reboot` from the Proxmox console first (clears it if transient), then
check the host: storage-pool free space, SMART/health of the backing disk,
the VM disk config. This VM almost certainly needs a **new, larger, healthy
disk** — see also §3 on the disk being too small.

The **laptop** `cti-harvester.timer` + `cti-panel.service` were re-enabled so
CTI coverage doesn't lapse. `state/history.jsonl` + `seen.jsonl` are intact on
the laptop; they were copied to the VM before the failure so nothing is lost.
Once the VM disk is healthy: re-clone, restore state, re-run the native setup
(§2), then stop the laptop again.

The **agent-bus poller** (`bin/agent_bus_poll.sh`, `deploy/agent-poll.*`,
`agent-bus/PROMPT-claude.md`) is committed and ready but NOT deployed — the VM
was read-only when I tried. Deploy it on both machines once the VM is back
(VM: `AGENT=claude BUS=~/agent-bus`; laptop: same plus
`BUS_REMOTE=securitycyber@securitycybercti:agent-bus`).

### 2026-09-03: laptop cutover (SUPERSEDED by the FS failure above)

The laptop's `cti-harvester.timer` + `cti-panel.service` are **stopped and
disabled**. The laptop repo at `~/Threatintel` stays as a cold backup (do not
delete). `state/history.jsonl` + `state/seen.jsonl` (the "already posted"
memory) were copied to the VM before cutover, so the VM analyst won't re-post
PaperCut / Langflow / JFrog / SonicWall / Switchvox / BGP-hijack. **This VM is
now the only pipeline.**

### Deployed NOW — native systemd (working, verified)

- `~/.config/systemd/user/cti-harvester.{service,timer}` — every 4h
  (`00,04,08,12,16,20`). Service PATH carries `.venv/bin` + `~/.local/bin`.
- `~/.config/systemd/user/cti-panel.service` — dashboard, always on.
- `loginctl enable-linger securitycyber` → **runs with nobody logged in**.
- **First real run succeeded**: `decision=post`, 3 LinkedIn posts (dry-run),
  **27 subscriber alerts** with full distinct briefs (score 15–100). ~9 min,
  213 MB peak.
- **Root cause of an earlier stall**: `claude -p` hangs on the folder-trust
  dialog headless. Fixed by setting `hasTrustDialogAccepted: true` for
  `/home/securitycyber/Threatintel` in `~/.claude.json`. If you re-clone
  elsewhere, do this again.

### LinkedIn is PAUSED on this VM

`config/settings.yaml` → `publish.dry_run: true`. Charlie wants posts on the
**company page** `linkedin.com/company/security-cyber-uk`, not his personal
profile. Un-pause only after the org URN + `w_organization_social` scope are
sorted (see §4).

---

## 3. THE CONTAINER TASK (what Charlie wants next — not done)

Charlie wants the pipeline running as a **container/Docker unit**, not native.
Files are ready: `deploy/Containerfile`, `deploy/threatintel.container`,
`deploy/threatintel-panel.container`, `deploy/threatintel.timer`,
`deploy/README.md`.

### BLOCKER — `podman build` and even `podman run <base>` fail

    $ podman run --rm registry.fedoraproject.org/fedora-minimal:44 echo hi
    (no output — container produces nothing)

    $ podman build ... (Containerfile STEP 2, microdnf):
    /bin/sh: error while loading shared libraries: /lib64/libc.so.6:
      cannot apply additional memory protection after relocation: Permission denied

**DIAGNOSIS — CONFIRMED: it is SELinux.**

    $ sudo setenforce 0 && podman run --rm alpine:3.20 echo OK  →  "OK"   (works)
    $ sudo setenforce 1 && podman run --rm alpine:3.20 echo OK  →  (nothing)

`ausearch -m AVC -ts recent` shows `<no matches>` — the denial is
dontaudit-suppressed (likely `execmem`/`execmod` on `container_t`, which
glibc 2.41+ needs for RELRO). Did NOT fix it: `--security-opt
seccomp=unconfined`, `--isolation=chroot`, `--security-opt label=disable`
attempts; there is no `container_use_execmem` boolean on this system.

**The fix is a SELinux-policy decision for Charlie (a Claude session was
blocked from making it):**
  1. FIRST TRY: `sudo dnf update -y container-selinux` then retry — often a
     stale policy vs a new glibc.
  2. If still failing, scope it (do NOT leave global permissive):
     `sudo semanage permissive -a container_t` — only the container domain
     goes permissive, the rest of the host stays enforcing.
  3. Or generate a targeted module:
     `sudo semodule -DB` (turn off dontaudit) → reproduce → 
     `sudo ausearch -m AVC -ts recent | audit2allow -M podman-glibc241` →
     `sudo semodule -i podman-glibc241.pp` → `sudo semodule -B`.

**Other options if the SELinux fix is unwanted:**
  4. base the image on
     `debian:12-slim` / `python:3.12-slim` (glibc 2.36, no mseal) instead of
     Fedora. The `claude` binary must still run there — test
     `podman run --rm -v ~/.local/share/claude:/c python:3.12-slim /c/versions/2.1.259 --version`.
     If claude won't run on Debian glibc, bake it via its installer in a
     Debian image and test again. (Debian glibc 2.36 sidesteps the RELRO
     mprotect that trips the SELinux denial — this may just work.)
  5. `podman-docker` for the `docker` command: `sudo dnf install -y podman-docker`.

**Also: disk.** 3 GB free is marginal for an image + rebuilds. Ask Charlie to
resize the Proxmox disk (Hardware → Disk → Resize → +20 GB, then on the VM:
`sudo growpart /dev/sda 3 && sudo btrfs filesystem resize max /`).

Once the image builds:

    mkdir -p ~/.config/containers/systemd
    cp deploy/threatintel.container deploy/threatintel-panel.container deploy/threatintel.timer ~/.config/containers/systemd/
    podman build -t threatintel:latest -f deploy/Containerfile ~/Threatintel
    systemctl --user daemon-reload
    systemctl --user disable --now cti-harvester.timer cti-panel.service   # retire native
    systemctl --user start threatintel.timer threatintel-panel.service
    # verify a run, then commit any Containerfile changes

---

## 4. Open blockers (need Charlie or Cloudflare/Stripe/LinkedIn)

1. **`push_to_worker` → HTTP 401.** The Worker's `INGEST_SECRET` ≠
   `~/Threatintel/config/env`'s `CTI_INGEST_SECRET`
   (`oANkLhLEiv_6qvM5U1FnFPZH2NJVs6MZCXZyZU92fs8`). **This is the only thing
   stopping the 27 briefs/run reaching subscribers.**
   Fix: `cd worker && npx wrangler secret put INGEST_SECRET` → that value.
2. **Worker not on latest + D1 migration pending:**
   `npx wrangler d1 execute cti-subs --remote --file migrations/0001_subscriber_depth.sql`
   then `npx wrangler deploy`.
3. **Stripe webhook secret mismatch** (separate from #1): the Worker's
   `STRIPE_WEBHOOK_SECRET` must match the Stripe endpoint
   `we_1UBJNlCCcmRAgupcAi1OLf4S` (snapshot payload, secret redacted — see
   `config/env` on the VM; this was a historical mismatch, resolved).
   Until fixed, paid customers are charged and never provisioned.
4. **Discord secrets not set on the Worker:** `DISCORD_ALERT_WEBHOOK`,
   `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`,
   `DISCORD_GUILD_ID`. Bot least-priv install link (NOT `permissions=8`):
   `https://discord.com/oauth2/authorize?client_id=1544797243498827847&permissions=268454915&integration_type=0&scope=bot`
5. **Rotate** the Discord webhook + bot creds — they were pasted in chat.
6. **LinkedIn company page:** get numeric org id from
   `linkedin.com/company/security-cyber-uk/admin/`, set
   `LINKEDIN_AUTHOR_URN=urn:li:organization:NNNN` in `config/env`, request the
   **Community Management API** product for `w_organization_social`. Then
   un-pause (`publish.dry_run: false` on the VM).
7. **ICO registration** — live Stripe key = real money + real customer list;
   UK law requires it. Register (ico.org.uk, £40-52/yr) or drop to `sk_test_`.
8. Antigravity/Gemini CLI not yet installed on the VM — Charlie will do it;
   point it at `~/agent-bus/README.md`.

---

## 5. Key facts / gotchas

- **`config/settings.yaml`** carries Charlie's canarytoken as
  `notify.ntfy_url` — do NOT fetch/POST it. NEVER `git checkout`/`git stash`
  it. It's uncommitted on purpose.
- **`config/env`** is chmod 600, gitignored — real secrets. Never commit.
- Worker `worker/` deploys ONLY `src/index.js` + its imports. No `[assets]`
  block — `web/` (the panel) must never reach the Worker.
- Worker name is `cti-alerts`. Route `cti.securitycyber.uk`. D1 `cti-subs`.
- Cron day-of-week: Cloudflare rejects `0` for Sunday — use `SUN`.
- Tests: `node worker/test/run.mjs` (88), `node worker/test/flow.mjs` (46,
  needs `npx wrangler dev --local --port 8788`). Both green at last commit.
- The pipeline's tier floors are low on purpose (Individual 15, Team 10) so
  subscribers get essentially everything real; their own threshold filters.
- Commit trailer: `Co-Authored-By: Claude ... <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_01DeiiZ6gU4KwZcDPSsG3uzx`.
- Don't push to main of other repos, force-push, or merge. Branch first for
  anything non-trivial.

---

## 6. Recent commits (newest first)

    a29776a deploy: self-contained Fedora-based container image + panel Quadlet
    125f9e1 docs: agent-bus — Claude/Antigravity handoff protocol
    969efe3 analyst: carry confidence on each alert too
    b082354 deploy: VM deployment — native systemd + podman Quadlet for later
    05c0f42 pipeline: analyst fallback for token limits, two-tier verification
    f223519 worker: Discord channel + one-time invites, HTML email, forever-free
    02a562c pipeline+worker: make the paid feed worth paying for
