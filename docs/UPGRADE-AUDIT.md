# Deterministic free CTI upgrade audit — 2026-10-30

Baseline: the supplied `Automated_threat_tool-master (1).zip`. All 159 archived
files matched `origin/master` at `c7aaad8`. Work is isolated from the older dirty
`panel-otx-look` checkout. No live config, credentials or production state were
copied into this upgrade.

## Findings addressed

- Validation failed open; the scheduled decision validation now stops delivery on
  failure. A freshness marker rejects success-without-new-output before the
  honeynet merge can change the decision timestamp.
- Empty alerts incorrectly fell back to social posts. Only an absent alerts field
  enables the legacy fallback.
- Paid/account routes and scheduled subscriber lifecycle remained reachable in
  free mode. They are retired; TAXII and authenticated IOC ingestion remain open
  at their existing paths. Historical records and rollback adapters are retained.
- Primary publication depended on AI. The archive now derives directly from
  validated ingest records, with an idempotent durable outbox and explicit source
  provenance. AI can only add separate operator-review notes.
- TAXII timestamp-only cursors dropped indicators sharing a batch timestamp.
  Composite timestamp/ID cursors and a limit+1 lookahead fix this.
- TAXII updates did not advance the incremental timestamp. Newer versions now do;
  stale/replayed versions do not overwrite newer indicators.
- TAXII storage errors were reported as an empty successful feed. They now return
  a retriable 503. Negative/invalid limits and malformed cursors are rejected.

## Validation and boundaries

Automated validation covers Python source packaging, outbox retry/idempotency,
secret rejection, AI schema/web-evidence boundaries, Worker unit/database tests,
archive request routing and authentication, content hashes, escaped source text,
RSS, search, STIX report exports, existing TAXII discovery/collection paths,
manifest, filtering, same-timestamp pagination and error handling.

The environment prevented binding a local TCP listener (`EPERM`) and could not
resolve PyPI to install independent `stix2` / `taxii2-client` validators. Tests
exercise the real Worker handler using Request/Response objects and SQLite, but
an external-client network round trip and independent STIX schema validation are
not claimed. Existing live Sentinel/OpenCTI/TAXII clients were not contacted.

The existing collection still retains latest versions only. Full pattern grammar
validation and historical STIX version retention are not added. New advisory
bundles contain reports, source-mentioned vulnerabilities and a generic course of
action, never inferred malicious indicators. AI notes are local and explicitly
not human verification; the public source record remains source-reported.

Deployment requires migration 0003 followed by Worker and host installation. No
production migration, deployment or live publishing was performed during audit.
Rollback restores prior code while leaving the additive table and local outbox
intact; do not replace current queues or live configuration with old snapshots.

Final checks passed: advisory Python tests (4), Codex guard tests (6), standalone
grounding and social-pacing checks, the complete Worker unit/database/advisory
suite, and expanded TAXII tests including newer-update visibility, stale replay
protection and available-version discovery. Bash syntax, Python compilation and
Git whitespace checks passed. Tracked executable modes were preserved from Git
after extracting the ZIP.
