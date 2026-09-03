# Build Report — CVEFeed Integration & Intel Desk

| | |
|---|---|
| **Report ID** | BR-2026-09-03-02 |
| **Date** | 2026-09-03 |
| **Author** | Claude Sonnet 5 (session `01DeiiZ6gU4KwZcDPSsG3uzx`) |
| **Environment** | Production VM `192.168.0.112` / `securitycybercti` |
| **Repository** | `github.com/sercuritycyber/Automated_threat_tool` — `master` |
| **Commits** | `07f5a9a` (ingest source) · `8a8c62e` (Intel Desk) |
| **Status** | ✅ SIGNED OFF — live, verified, no known errors |

---

## 1. Context — "clone their repo, rip it all out, migrate what you can"

`github.com/cvefeed/cvefeed.io` was cloned to the VM and inspected.
**There is no code to migrate.** The repo is 124 KB — a `README.md` and a
GitHub issue tracker. The README states outright: *"Is it ever gonna be
open-sourced? Yes I'll open-source cvefeed.io once I believe it's more than a
beta."* The platform is closed-source SaaS. (The clone was moved out of the
Threatintel working tree to `~/cvefeed.io-clone` so it cannot pollute git.)

**What is real and valuable is their API**, for which the operator now holds a
key with the `vulnerabilities` scope. So the migration is: consume their data,
and rebuild the useful parts of their *experience* on our stack, tuned for our
audience.

**Their model vs ours.** CVEFeed sells raw CVE intelligence + a query language
to security researchers and pentesters (the ASM / VM market). Ours is an
analyst-curated alert service for UK SMEs with no security team — plain
English, "what do I do", delivered to the channel they already use. We do not
compete on being a CVE database; we compete on turning the data into an action.
The integration reflects that: CVEFeed feeds the machine, our analyst layer and
distribution are the product.

## 2. Delivered

### 2.1 CVEFeed as an ingest source (`bin/ingest.py`, `config/feeds.yaml`)
- `fetch_cvefeed()` calls `GET https://cvefeed.io/api/vulnerability/`
  (`ordering=-published`, 200 rows) with `CVEFEED_API_KEY` (in `config/env`,
  untracked). Keeps items **CVSS ≥ 7.5 OR on CISA KEV**; the existing lookback
  window, URL-dedup, KEV cross-reference and clustering apply to it exactly as
  to every RSS source.
- `feeds.yaml` gains a `type: api` / `provider: cvefeed` entry; `main()` now
  splits RSS vs API feeds and submits both to the same thread pool.
- **Verified:** first run pulled 2 fresh high-severity CVEs; candidate count
  48 → 60 for the window.

### 2.2 Intel Desk (`web/app.py` `/intel`, `bin/cve_desk.py` — new)
On-demand CVE research inside the Admin Center. The CVEFeed key stays
server-side; the browser only talks to our panel.

| Input | Behaviour |
|---|---|
| `CVE-2026-1234` | Full record — CVSS, severity, CVSS version, CISA KEV flag, source, publish date, link. |
| `sonicwall`, `elementor`, any keyword | CVEQL `description ~ "…"` contains-search. |
| `cvss_score >= 9.8`, `description ~ "rce" AND severity = "CRITICAL"` | Raw CVEQL, run as-is. |
| **"Draft an alert →"** on any result | Pulls the full record, pre-fills a compose draft (CVE, CVSS, KEV, "confirm exposure / apply fix", source line) and hands it to the pipeline hold queue via the existing Marketing hand-off. |

- 60-second result cache + graceful HTTP 429 handling (CVEQL is rate-limited
  per plan — free tier is 10/min).
- New nav entry **Intel Desk**, between Candidates and Drafts.

## 3. Verification & sign-off

| Check | Result |
|---|---|
| `ast.parse` `bin/cve_desk.py`, `py_compile` `web/app.py`, `bin/ingest.py` | ✅ pass |
| `python3 bin/ingest.py` with the key live | ✅ `CVEFeed: 2 items (CVSS ≥ 7.5 or KEV)`, 60 candidates written |
| `GET /intel` | ✅ `200` |
| `GET /intel/api/search?q=CVE-2026-9744` | ✅ full record returned |
| `GET /intel/api/search?q=sonicwall` | ✅ 5 SonicWall CVEs, correct CVSS/severity |
| `GET /intel/api/search?q=cvss_score >= 9.8` | ✅ CVEQL, count 2601 |
| 429 path | ✅ returns "try again in N seconds", not a raw error |
| `POST /intel/api/draft {id:…}` | ✅ `{"queued": true}` — draft landed in `state/pending.json`, test row removed |
| Panel service after restart | ✅ `active`, 0 errors |
| Test data left behind | ✅ none |

**Sign-off:** CVEFeed ingest source and Intel Desk are live on the production
VM, all checks pass, no known errors.

## 4. Roadmap — "make ours the market leader"

Not one build; a direction. In priority order, each an increment on what now
exists:

1. **Subscriber product watch** (worker) — the worker already filters alerts by
   vendor/product per subscriber. Surface it: a subscriber picks the products
   they run; CVEFeed's `source_identifier` + product data drives a "your stack"
   view in their feed. This is CVEFeed's project-subscription feature, but
   pointed at *defenders acting*, not researchers browsing.
2. **Public CVE pages** (site) — `securitycyber.uk/cve/CVE-2026-1234` rendered
   from the CVEFeed record + our brief when we have one. SEO + a reason to link
   us. Their per-CVE detail page, in our voice.
3. **Weekly "what actually mattered" digest** — a Sunday roll-up from the week's
   candidates: the 5 CVEs that were exploited / KEV-listed / hit widely-run
   software, each with the one action. Free on the socials, full in the paid
   feed. This is the editorial layer CVEFeed does not have.
4. **EPSS + exploitation signal** — CVEFeed exposes EPSS; fold it into the
   pre-score so "likely to be exploited" ranks alongside "is on KEV".
5. **Operator CVEQL saved searches** — let the operator pin CVEQL queries in the
   Intel Desk (e.g. "critical + my client's vendors this week") with a one-click
   "draft alerts for all".

Items 1–3 are the differentiators; 4–5 are polish. None require CVEFeed's
source code — only their API, which we now consume.
