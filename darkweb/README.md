# Onion-address aggregator (`darkweb/`)

Crawls onion **index/search sources** and produces a maintained, deduped,
liveness-checked **list of valid .onion addresses** for forums like
BreachForums / XSS / Dread — so you can go and investigate them yourself. It is
an address-discovery aid; **it does not scrape or store forum content**.

Validation is a minimal liveness probe over Tor (is it up, HTTP status, page
`<title>` to help you identify it). Nothing from inside the forums is stored.

## CSAM is blocked, fail-closed, and cannot be disabled

The only category this tool blocks is child sexual abuse material — required by
UK law and simply right. It is enforced at every stage:

- an **onion blocklist** (addresses, or md5(`<addr>.onion`) hashes — Ahmia's
  abuse-blacklist format): blocked addresses are never fetched, stored, or shown;
- a **high-precision CSAM keyword filter** on discovered context and liveness
  titles. It deliberately does **not** trip on a bare `cp` (that appears in
  normal tech/forum talk and would destroy the intel feed you want);
- **fail-closed**: if the safety filter can't be built, the tool refuses to run;
  if a page can't be classified, nothing from it is stored.

Blocked addresses are recorded as `blocked` with **no context or title**, purged
of anything previously stored, and excluded from every list — kept only so they
are skipped on future runs. `config` can only *add* to the blocklist.

## How it works

1. **crawl** — fetch each enabled source over Tor, extract every v3 `.onion`
   with the label/context around it, tag by target forum name. Crawling stays
   **within a source's own host** (pagination/category pages), bounded by depth
   and page count. It never spiders into the discovered forum onions.
2. **checkup** — connect to each discovered onion's root over Tor, record
   up/down + HTTP status + `<title>`. CSAM-gated before anything is stored.
3. **list** — the aggregated addresses, filterable by tag / live-only, exportable
   as JSONL.

## Sources reality

Ahmia (clearnet, `ahmia.fi`) is the safe default — it excludes abuse content —
but it curates heavily and won't surface most criminal forums. To actually find
BreachForums/XSS/Dread mirrors you'll add verified mirror-list sources to
`sources.yaml`. **Only add v3 addresses/sources you have verified** — these are
heavily phishing-cloned.

## One-time setup

### Tor (already installed on VM 100)
```bash
python3 darkweb/harvest.py check     # {"IsTor":true,...}
```

### Hardened service (the collector gets NO shell)
The collector core imports no `subprocess`. It runs under the `systemd --user`
unit `cti-darkweb.service` with `NoExecPaths=/` and a narrow `ExecPaths`
whitelist (python + libs only): the process can start the interpreter but
**cannot exec `/usr/bin/bash`, `/usr/bin/sudo`, or anything else** — verified on
the host (exec of bash → `PermissionError`). This holds even though it runs as
the pipeline user. Only the Admin Center (trusted) starts the unit.
```bash
mkdir -p /mnt/data/threatintel/darkweb          # writable data dir (ReadWritePaths)
cp systemd/cti-darkweb.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cti-darkweb.timer
```

### Config
```bash
cp darkweb/sources.yaml.example darkweb/sources.yaml   # set targets + sources
# set `db:` to /mnt/data/threatintel/darkweb/onions.db (matches ReadWritePaths)
# optional CSAM blocklist files referenced from the safety: block
```
`sources.yaml` (and any `safety_blocklist_*.txt`) are gitignored — they may hold
verified onion addresses and must never be committed.

## Usage
```bash
python3 darkweb/harvest.py check
python3 darkweb/harvest.py add <onion-or-url> --tag sh-psa   # track one you found
python3 darkweb/harvest.py crawl
python3 darkweb/harvest.py checkup --limit 60 --stale 24
python3 darkweb/harvest.py list --tag breachforums --up --json state/onions.jsonl
python3 darkweb/harvest.py run        # crawl + checkup + list (timer path)
python3 darkweb/harvest.py alerts     # tracked sites that came back UP (down->up)
python3 darkweb/harvest.py history <onion>   # status-change timeline (rug-pull view)
python3 darkweb/harvest.py stats
```

## Status alerts (down → up)

When a tracked address that was **down** comes back **up**, the tool raises an
alert — a bright banner on the `/darkweb` desk (with "Acknowledge all") plus an
optional push to a **dedicated** ntfy topic (`alert.ntfy_url` in sources.yaml,
or `DARKWEB_ALERT_NTFY` env — never the pipeline's canary `ntfy_url`). Every
status change is also written to a `status_log`, so `harvest.py history <onion>`
prints the full up/down timeline (useful for an exit-scam / rug-pull theory).

## Internal blockchain watcher (`chainwatch.py`) — never public

Separate, internal-only tool: watches crypto addresses you supply and alerts on
large or imminent outflows (the signal that an exit-scam warchest is draining).
It never posts publicly.

- **No invented addresses** — you add verified/candidate ones; `add` validates +
  baselines them. BTC only (mempool.space, queried over Tor so our watchlist
  isn't leaked). **Monero can't be watched on-chain** and is refused.
- Alerts: `outflow` (balance dropped ≥ threshold), `pending-outflow` (a large
  spend sitting in the mempool — imminent), `activity` (new tx). Recorded in
  `chainwatch.db`, shown on the panel **Wallets** tab, and optionally pushed to a
  dedicated ntfy topic (`chainwatch.alert.ntfy_url` / `CHAINWATCH_ALERT_NTFY`) —
  never the pipeline canary.

```bash
cp darkweb/chainwatch.yaml.example darkweb/chainwatch.yaml
python3 darkweb/chainwatch.py add <btc-addr> --label shinyhunters-warchest --threshold-btc 0.25
python3 darkweb/chainwatch.py check      # run by cti-chainwatch.timer every 30m
python3 darkweb/chainwatch.py list
python3 darkweb/chainwatch.py alerts
```
`chainwatch.yaml` is gitignored (holds addresses under investigation).

## Admin Center (`/darkweb`)
- **Overview** — down→up alert banner, CSAM-blocked count, stats, controls
  (Check Tor, Crawl+validate, Re-check liveness), live addresses.
- **Addresses** — the full aggregated list (blocked ones never appear).
- **Sources** — targets + sources; enable/disable per source (override).
- **Activity** — collector log tail.

"Crawl + validate" triggers the hardened `cti-darkweb.service`, so manual runs
are sandboxed exactly like scheduled ones.

## Files
| file | role |
|---|---|
| `torfetch.py` | stdlib SOCKS5 → `http.client`/`ssl`; one capped, same-host-redirect GET over Tor |
| `parse.py` | `html.parser` → text/title/same-host links |
| `discover.py` | v3 onion extraction + target tagging + source-URL expansion |
| `safety.py` | CSAM filter (blocklist + high-precision terms), fail-closed |
| `store.py` | SQLite: `onions` / `sightings` / `tags`; `blocked` state hides + purges |
| `harvest.py` | CLI orchestrator (crawl/checkup/list), lock file, no shell |
| `bin/darkweb_desk.py` | Admin Center desk (trusted operator side) |
| `systemd/cti-darkweb.{service,timer}` | hardened, no-exec, dedicated user |
