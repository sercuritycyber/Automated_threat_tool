# Dark-web collector (`darkweb/`)

Targeted collection of **onion forum pages you have configured** — over Tor,
into a local SQLite DB, cross-referenced against a watchlist and your
**honeypot IOCs** so honeypot telemetry can be tied to market/forum activity
(e.g. a credential the honeypot captured turning up for sale, or an attacking
IP being discussed).

It is an **investigation aid for the honeynet desk**, not part of the
publishing pipeline. Nothing here posts, publishes, or writes to `decision.json`.

## Design guardrails (why it's built this way)

- **Targeted, not a crawler.** It fetches exactly the paths in `sites.yaml` and
  never follows links off the page. Links it finds are stored for a human to
  triage, not auto-fetched. This keeps it out of unknown/illegal hidden services.
- **Text only, size-capped.** Non-text content types and large bodies are
  refused, so it never pulls binary dumps or media.
- **No shell / no exec.** The collector core (`torfetch`, `parse`, `match`,
  `store`, `harvest`) imports no `subprocess` and runs no shell. In production it
  runs under `cti-darkweb.service` as a dedicated locked-down `darkweb` user
  whose `SystemCallFilter` blocks `execve`/`execveat` — the process that ingests
  untrusted onion HTML *cannot* launch a program even if it were exploited.
  Only the Admin Center (trusted operator side) spawns it.
- **Circuit isolation.** Each fetch uses a random SOCKS username/password, so
  with Tor's `IsolateSOCKSAuth` each request rides its own circuit.
- **No secrets in config.** Session cookies for authenticated boards come from
  environment variables named in `sites.yaml` (`cookie_env`), never the file.

## Legal / OPSEC

Passive collection of forum text for defence is the purpose. Do not download
data dumps, only monitor boards you're authorised to access, and be aware of the
law where you operate. Onion addresses for these forums churn constantly and are
heavily cloned for phishing — **only paste v3 addresses you have verified**, or
a phishing clone could harvest a session cookie you supply.

## One-time setup

### 1. Tor on the VM
```bash
sudo dnf install -y tor            # Fedora (VM 100)
sudo systemctl enable --now tor    # SOCKS proxy on 127.0.0.1:9050
# verify:
python3 darkweb/torfetch.py --check
```

### 2. Dedicated locked-down user + hardened service
```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin darkweb
sudo usermod -aG securitycyber darkweb          # read the repo + honeyintel exports
sudo cp systemd/cti-darkweb.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cti-darkweb.timer
# let the panel (securitycyber) trigger a manual run:
echo 'securitycyber ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block cti-darkweb.service' \
  | sudo tee /etc/sudoers.d/darkweb
```

### 3. Config
```bash
cp darkweb/sites.yaml.example darkweb/sites.yaml       # add VERIFIED onion addresses
cp darkweb/watchlist.yaml.example darkweb/watchlist.yaml
# point sites.yaml `ioc_feeds` at honeyintel's latest report CSVs
# export any session cookies referenced by cookie_env (systemd EnvironmentFile, not the yaml)
```

## Usage (CLI)
```bash
python3 darkweb/harvest.py check      # is Tor up and exiting?
python3 darkweb/harvest.py collect    # fetch every enabled site/path
python3 darkweb/harvest.py match      # scan latest snapshots for watchlist/IOC hits
python3 darkweb/harvest.py report --since 24 --json state/darkweb_hits.jsonl
python3 darkweb/harvest.py run        # collect + match + report (the timer path)
python3 darkweb/harvest.py stats
```

## Admin Center (human oversight + override)

`/darkweb` in the panel:
- **Overview** — Tor/DB stats, controls (Check Tor, Collect now, Re-match).
- **Hits** — everything the matcher flagged, newest first, with defanged
  URLs/indicators and a **Mark reviewed** button. This is the investigation view.
- **Sites** — the registry with last-seen status; **enable/disable** per site is
  the operational override. Addresses are edited in the file (sensitive), not here.
- **Activity** — tail of the collector log.

"Collect now" triggers the hardened `cti-darkweb.service` so manual runs are
sandboxed exactly like the scheduled ones.

## Files
| file | role |
|---|---|
| `torfetch.py` | stdlib SOCKS5 client → `http.client`/`ssl`; one capped, text-only, same-host-redirect GET over Tor |
| `parse.py` | `html.parser` → text + title + same-host link inventory |
| `store.py` | SQLite: `pages` / `snapshots` (sha256 dedup) / `hits` |
| `match.py` | watchlist + honeypot-IOC indicators, false-positive guards |
| `harvest.py` | CLI orchestrator, lock file, no shell |
| `bin/darkweb_desk.py` | Admin Center desk (trusted operator side) |
| `systemd/cti-darkweb.{service,timer}` | hardened, no-exec, dedicated user |
