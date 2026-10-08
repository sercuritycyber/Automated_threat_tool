# Security

## Reporting a vulnerability

Please report security issues privately, not in a public issue:

- **GitHub:** the **Security → Report a vulnerability** button on this repository
- **Email:** charlie@securitycyber.uk

Include what you found, how to reproduce it, and the impact you expect. You'll
get an acknowledgement, and credit in the fix if you'd like it.

In scope: the code in this repository. Reports about securitycyber.uk and
cti.securitycyber.uk are welcome too.

## Credentials

This repository ships **no working credentials**. Anything in the history that
looks like one is revoked, a test placeholder, or a tripwire that alerts when
it's used.

Every deployment uses its own keys: put them in `config/env` (git-ignored,
`chmod 600`) and in your own Cloudflare Worker secrets. Never commit them.
Secret scanning and push protection are enabled on this repository.

## Running your own

Start in dry-run and review mode, treat feed content as untrusted input, and
keep the analyst on its restricted tool set. See the safety rules in
[`docs/SETUP.md`](docs/SETUP.md#safety-rules-the-pipeline-enforces-keep-them).
