# SecurityCyber production handover

Owner: Charlie Collins. Be concise and accurate; no fabricated claims.
This is a production CTI pipeline and owner panel. Do not delete existing files.
Preserve backups and local changes. Read docs/CODEX-HANDOVER.md before changes.

- config/env contains secrets: never print or commit it, or pass it to model processes.
- config/settings.yaml is live, untracked configuration. Never overwrite it from Git.
- Scheduled flow: bin/run.sh -> ingest -> selected analyst -> subscribers -> socials.
- Do not trigger run.sh or publishing scripts to test an analyst: they send real messages.
- Test the analyst with a separate --output path, preferably the staging snapshot.
- Take state/.lock before changing pipeline configuration/code/state; back up affected files.
- Codex analyst uses ChatGPT authentication, restricted tools, and public web verification.
  The host validates JSON and performs writes; model access to production secrets is unnecessary.
- No autonomous marketing team or Night Shift revival. Reddit remains human-only.
- Log SSH actions locally in ~/securitycyber-growth/ssh_actions.log without secrets.
- Existing engine implementations remain available for rollback; never remove credentials as cleanup.
