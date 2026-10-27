#!/usr/bin/env python3
"""agent_guard.py — detect the AGENTS.md / CLAUDE.md agent-hijack class on this host.

Defends against the Forever Security (Oct 2026) attack where a planted instruction
file in a working directory steers a coding agent into (a) redirecting its API base
URL to exfiltrate the OAuth token, (b) installing a rogue package, or (c) adding an
unknown MCP server. READ-ONLY: it inspects configs and risky directories, reports
findings, optionally alerts via CTI_ALERT_WEBHOOK, and returns a non-zero exit code
so a pipeline pre-flight can fail closed. It never modifies anything.

Exit: 0 = clean, 2 = findings, 1 = guard error.
Usage: agent_guard.py [--alert] [--json]
"""
import os, sys, json, re, glob
from pathlib import Path

HOME = Path.home()
REPO = Path(os.environ.get("CTI_ROOT", HOME / "Threatintel"))

# Base URLs that are legitimate. Anything else in an agent config = token-redirect risk.
ALLOWED_BASE = {
    "", "https://api.anthropic.com", "https://api.anthropic.com/",
    "https://api.openai.com", "https://api.openai.com/", "https://api.openai.com/v1",
    "https://api.openai.com/v1/",
}
BASE_KEYS = ("anthropic_base_url", "base_url", "baseurl", "anthropic_api_url", "openai_base_url")

CONFIG_FILES = [
    HOME / ".claude" / "settings.json",
    HOME / ".claude" / "settings.local.json",
    HOME / ".claude.json",
    REPO / ".claude" / "settings.json",
    REPO / ".claude" / "settings.local.json",
    HOME / ".codex" / "config.toml",
    HOME / ".config" / "codex" / "config.toml",
]
# Directories an agent might be launched in that a browser / other apps can write to.
RISKY_DIRS = [HOME / "Downloads", HOME / "Desktop"]
INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "CODEX.md", ".cursorrules")

# High-signal markers only — the tokens that distinguish an *attack* instruction
# file from a legitimate project one (which routinely says "npm install" etc.).
INJECTION_MARKERS = re.compile(
    r"ANTHROPIC_BASE_URL|function-color|--dangerously|bypass-approvals|launchctl|"
    r"curl\s+[^\n|]*\|\s*(ba)?sh|\.claude/settings\.json|codex/config\.toml",
    re.I,
)
IOC_PACKAGES = ("function-color",)


def _flatten(obj, path=""):
    """Yield (dotted_key, value) for every scalar in a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, obj


def _load(path: Path):
    text = path.read_text(errors="replace")
    if path.suffix == ".toml":
        try:
            import tomllib
            return tomllib.loads(text)
        except Exception:
            return {"__raw__": text}
    try:
        return json.loads(text)
    except Exception:
        return {"__raw__": text}


def check_configs():
    findings = []
    for p in CONFIG_FILES:
        if not p.exists():
            continue
        data = _load(p)
        raw = data.get("__raw__") if isinstance(data, dict) else None
        if raw is not None:
            # unparseable — fall back to a regex sweep of the raw text
            for m in re.finditer(r"(?i)(anthropic_base_url|base_url)\s*[=:]\s*['\"]?([^'\"\n,}]+)", raw):
                val = m.group(2).strip()
                if val.rstrip("/") not in {a.rstrip("/") for a in ALLOWED_BASE}:
                    findings.append((p, "rogue base URL (raw)", f"{m.group(1)}={val}"))
            continue
        for key, val in _flatten(data):
            leaf = key.split(".")[-1].split("[")[0].lower()
            if leaf in BASE_KEYS and isinstance(val, str):
                if val.rstrip("/") not in {a.rstrip("/") for a in ALLOWED_BASE}:
                    findings.append((p, "rogue API base URL -> token-redirect risk", f"{key} = {val}"))
            if leaf in ("command", "args", "url") and isinstance(val, str):
                if IOC_PACKAGES[0] in val or re.search(r"--dangerously|bypass-approvals|launchctl", val, re.I):
                    findings.append((p, "suspicious MCP/command entry", f"{key} = {val[:120]}"))
        # unexpected MCP servers (report names for human review, not auto-trusted)
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            for name, spec in servers.items():
                blob = json.dumps(spec)
                if IOC_PACKAGES[0] in blob or re.search(r"--dangerously|bypass-approvals|launchctl", blob, re.I):
                    findings.append((p, "suspicious mcpServers entry", f"{name}: {blob[:120]}"))
    return findings


def scan_instruction_files():
    findings = []
    seen = set()
    for d in RISKY_DIRS:
        if not d.exists():
            continue
        for name in INSTRUCTION_NAMES:
            for hit in glob.glob(str(d / "**" / name), recursive=True):
                hp = Path(hit)
                if hp in seen:
                    continue
                seen.add(hp)
                try:
                    txt = hp.read_text(errors="replace")
                except Exception:
                    continue
                markers = sorted({m.group(0).lower() for m in INJECTION_MARKERS.finditer(txt)})
                if markers:
                    findings.append((hp, "instruction file with injection markers", ", ".join(markers[:6])))
    return findings


def check_codex_trust():
    """Flag a Codex config that trusts a broad path (e.g. $HOME), which makes every
    folder under it — including ~/Downloads — trusted for project config. That is the
    precondition the Codex variant of this attack needs."""
    findings = []
    broad = {str(HOME), str(HOME) + "/", os.path.expanduser("~"), "/", "~"}
    for p in (HOME / ".codex" / "config.toml", HOME / ".config" / "codex" / "config.toml"):
        if not p.exists():
            continue
        data = _load(p)
        projects = data.get("projects") if isinstance(data, dict) else None
        if isinstance(projects, dict):
            for proj, spec in projects.items():
                lvl = spec.get("trust_level") if isinstance(spec, dict) else None
                if lvl == "trusted" and proj in broad:
                    findings.append((p, "Codex trusts a broad path (includes Downloads) -> raises hijack risk",
                                     f'projects."{proj}" trust_level=trusted'))
    return findings


def alert(findings):
    url = os.environ.get("CTI_ALERT_WEBHOOK", "").strip()
    if not url:
        return "no CTI_ALERT_WEBHOOK set"
    try:
        import requests
    except Exception:
        return "requests unavailable"
    lines = [f"- {p}: {what} ({detail})" for p, what, detail in findings]
    body = "agent_guard: possible AGENTS.md/CLAUDE.md agent-hijack indicators\n" + "\n".join(lines)
    try:
        if "discord.com/api/webhooks" in url:
            r = requests.post(url, json={"content": body[:1900]}, timeout=15)
        elif "ntfy.sh/" in url or "/ntfy" in url:
            r = requests.post(url, data=body.encode()[:3900],
                              headers={"Title": "agent_guard finding", "Priority": "high"}, timeout=15)
        else:
            r = requests.post(url, json={"text": body[:3000]}, timeout=15)
        return f"alert HTTP {r.status_code}"
    except Exception as e:
        return f"alert failed: {e}"


def main():
    do_alert = "--alert" in sys.argv
    as_json = "--json" in sys.argv
    try:
        findings = check_configs() + scan_instruction_files() + check_codex_trust()
    except Exception as e:  # never let the guard itself crash a pipeline hard
        print(f"agent_guard: internal error: {e}", file=sys.stderr)
        return 1

    out = [{"path": str(p), "issue": what, "detail": detail} for p, what, detail in findings]
    if as_json:
        print(json.dumps({"clean": not findings, "findings": out}, indent=2))
    else:
        if findings:
            print(f"agent_guard: {len(findings)} FINDING(S):")
            for f in out:
                print(f"  [!] {f['path']}: {f['issue']} ({f['detail']})")
        else:
            print("agent_guard: clean — no rogue base URL, MCP, or injected instruction file found.")
    if findings and do_alert:
        print("agent_guard:", alert(findings))
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
