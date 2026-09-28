# Codex production integration — 2026-09-28

Production host: securitycyber@192.168.0.112, repository ~/Threatintel.
Baseline commit: 6e2ce14. Installed codex-cli: 0.158.0, authenticated with ChatGPT.

## Changes

bin/analyst_codex.py invokes the existing CLI with public web access, shell/integration
and agent tools disabled, read-only sandbox, and an environment allowlist excluding
config/env credentials. User CLI configuration/rules are ignored for reproducibility.
The VM's existing ChatGPT login is used; no API key or Anthropic bridge is required.
The installed CLI's default model is used unless analyst.codex_model is explicitly set.
A call has a 600-second timeout (panel rewrite/draft calls use 120/240 seconds).
Inputs, event logs and diagnostics are retained in logs/codex-runs/ directories.

The host parses completed output, checks shape and subscriber-source URLs, runs social
provenance validation, and atomically writes the decision. A run without successful web results is
marked codex-summary, given low confidence, and uses existing backup public-review guards.
A successful web result is not proof every claim was verified; prompt and operator review still matter.

run.sh supports engine=codex. Failure takes deterministic subscriber-only fallback;
it does not invoke paid API providers or Claude. A failed deterministic fallback now
stops the run rather than risking reuse of a prior decision. Run-log deletion is disabled.
Panel draft/rewrite actions follow the selected engine; Settings exposes the engine selector.
Legacy code and credentials are retained for rollback.

## Safe validation

Do not run the whole publisher to test this. Stage public candidates and prompt separately,
then invoke analyst_codex.py there. --output can also target a review file distinct from
state/decision.json. Tests in tests/test_codex_analyst.py cover credential filtering,
JSON validation, source provenance and summary-only review behavior.

## Rollback

Set analyst.engine back to claude under state/.lock, preserving all other YAML fields.
The panel Settings selector also provides the legacy engine. Full pre-change files and
settings are preserved under ~/codex-handover-20260928/backups/. Restoring panel code
requires restarting only cti-panel.service. Do not copy old decision/queue snapshots
back over newer live state. Existing Claude auth/subscription may no longer be usable.

## Code-review findings not folded into the engine migration

1. URL deduplication and KEV-as-story support are already implemented (7fc12c7, 6e2ce14).
   Queue dedupe is exact-URL, not robust cross-outlet same-event matching.
2. _stale() accepts same-day deadline wording even after the deadline; its existing test
   explicitly allows 'until 08:00 UTC today' at 15:00 UTC. Current age ceiling is 36 hours.
3. Queue dedupe runs when writing waiting items, not across every immediate/due path.
4. CISA Advisories, CISA News and Google Project Zero feeds exist; no dedicated Android/
   Pixel bulletin or CISA resource-update feed was found in the current feed configuration.
5. Existing generic decision sanitization is permissive on malformed shapes and leaves
   subscriber alerts untouched. The new Codex adapter validates before state writes.
6. Manual posting may drain due queued items. No manual posting was run during handover.

These are code observations, not confirmation of the news claims in the supplied handover.
No OpenRouter key revocation or billing-setting changes were performed.
