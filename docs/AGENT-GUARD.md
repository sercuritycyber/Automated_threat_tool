# agent_guard — AGENTS.md / CLAUDE.md agent-hijack detector

Defends against the Forever Security (Oct 2026) attack class: a planted instruction
file (`AGENTS.md` for Codex, `CLAUDE.md` for Claude Code) in a directory an agent
starts in gets read and followed, and can redirect the agent's API base URL to steal
the OAuth token, install a rogue package, or add a hostile MCP server. There is no
vendor patch — it's a trust-boundary problem, so we detect it ourselves.

See the public write-up: https://securitycyber.uk/blog/downloads-folder-can-hijack-your-ai-coding-agent

## What `bin/agent_guard.py` checks (read-only)

- **Rogue API base URL** in any agent config (`~/.claude/settings.json`,
  `~/.claude.json`, `<repo>/.claude/settings*.json`, `~/.codex/config.toml`,
  `~/.config/codex/config.toml`) — anything not on the allowlist is a token-redirect
  risk, the Claude Code variant's whole move.
- **Suspicious MCP servers / commands** containing the IOC package, `--dangerously`,
  `bypass-approvals` or `launchctl`.
- **Injected instruction files** (`AGENTS.md`/`CLAUDE.md`/…) in browser-writable dirs
  (`~/Downloads`, `~/Desktop`) that carry high-signal attack markers.
- **Broad Codex trust** — a `[projects."…"] trust_level = "trusted"` on `$HOME` or
  `/`, which makes Downloads trusted for project config (the Codex variant's
  precondition).

It never modifies anything. Exit codes: `0` clean, `2` findings, `1` internal error.

## Usage

```
python3 bin/agent_guard.py            # human-readable
python3 bin/agent_guard.py --json     # machine-readable
python3 bin/agent_guard.py --alert    # also POST findings to $CTI_ALERT_WEBHOOK
```

## Pipeline pre-flight (opt-in)

`bin/run.sh` runs the guard before ingest **only** when `AGENT_GUARD=1` (off by
default, so it can never break a run on deploy). With `AGENT_GUARD_STRICT=1` a finding
aborts the run; otherwise it warns, alerts, and continues.

```
AGENT_GUARD=1 AGENT_GUARD_STRICT=1   # fail the run closed on any finding
AGENT_GUARD=1                        # warn + alert, keep running (recommended first)
```

## Standing monitor (VM)

`deploy/agent-guard.service` + `.timer` run the guard every 30 min on the CTI VM and
alert via `CTI_ALERT_WEBHOOK`. Install as a user unit:

```
cp deploy/agent-guard.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-guard.timer
```

## Known hardening item (not auto-fixed)

As of 2026-10-27, `~/.codex/config.toml` has `[projects."/home/charlie"] trust_level
= "trusted"`, which trusts every folder under home — including `~/Downloads` — for
project config. Narrow that to the specific repos you actually run Codex in. The guard
only flags it; changing the file is the operator's call.
