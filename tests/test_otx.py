"""AlienVault OTX ingest: pulses become candidate stories, and nothing that
isn't cleared for public release can get in.

The TLP gate is the one that matters: everything ingested can end up on
LinkedIn/Bluesky, so a GREEN/AMBER/RED pulse must never become a candidate.
No network: fetch_otx is exercised with a stubbed requests.get.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import ingest as I  # noqa: E402

fails = []


def chk(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        fails.append(name)


def pulse(**kw):
    p = {
        "id": "abc123", "name": "Melofee: new Linux implant variants", "tlp": "white",
        "created": "2026-10-11T07:08:57.961000", "modified": "2026-10-12T07:08:57",
        "description": "APT41 tooling   observed\nin the wild. Exploits CVE-2026-42016.",
        "references": ["https://example-research.test/melofee"],
        "adversary": "APT41", "malware_families": [{"display_name": "Melofee"}, "ShadowPad"],
        "attack_ids": [{"id": "T1014"}, "T1071.004"],
        "indicators": [{"type": "FileHash-SHA256", "indicator": "x" * 64}] * 6
                      + [{"type": "CVE", "indicator": "cve-2026-42018"}],
    }
    p.update(kw)
    return p


PUBLIC = {"white", "clear"}
it = I.otx_pulse_item(pulse(), "AlienVault OTX", 0.75, PUBLIC, 5)

# ---- shape the rest of ingest relies on --------------------------------
chk("item produced", it is not None, True)
chk("title", it["title"], "Melofee: new Linux implant variants")
chk("link prefers the original write-up", it["link"], "https://example-research.test/melofee")
chk("published is tz-aware", it["published"].tzinfo is not None, True)
chk("published uses created, not modified", it["published"],
    datetime(2026, 10, 11, 7, 8, 57, 961000, tzinfo=timezone.utc))
chk("source", (it["source"], it["source_weight"]), ("AlienVault OTX", 0.75))
chk("summary names the actor", "actor: APT41" in it["summary"], True)
chk("summary names malware (str and dict forms)", "malware: Melofee, ShadowPad" in it["summary"], True)
chk("typed CVE indicator surfaced for KEV scoring", "CVE-2026-42018" in it["summary"], True)
chk("CVE named in text surfaced", "CVE-2026-42016" in it["summary"], True)
chk("ATT&CK ids", "ATT&CK: T1014, T1071.004" in it["summary"], True)
chk("pulse URL kept for provenance", "otx.alienvault.com/pulse/abc123" in it["summary"], True)
chk("whitespace collapsed", "tooling observed in" in it["summary"], True)
chk("summary capped", len(I.otx_pulse_item(pulse(description="a " * 2000), "n", 1, PUBLIC, 5)["summary"]) <= 1200, True)
chk("no references -> pulse page", I.otx_pulse_item(pulse(references=[]), "n", 1, PUBLIC, 5)["link"],
    "https://otx.alienvault.com/pulse/abc123")
chk("non-URL reference ignored", I.otx_pulse_item(pulse(references=["see report"]), "n", 1, PUBLIC, 5)["link"],
    "https://otx.alienvault.com/pulse/abc123")

# ---- TLP gate: only public-release pulses -------------------------------
for tlp in ("green", "amber", "amber+strict", "red", "", None):
    chk(f"TLP {tlp!r} rejected", I.otx_pulse_item(pulse(tlp=tlp), "n", 1, PUBLIC, 5), None)
chk("TLP:CLEAR accepted", I.otx_pulse_item(pulse(tlp="clear"), "n", 1, PUBLIC, 5) is not None, True)
chk("TLP case-insensitive", I.otx_pulse_item(pulse(tlp="WHITE"), "n", 1, PUBLIC, 5) is not None, True)
chk("legacy 'TLP' key honoured", I.otx_pulse_item(pulse(tlp=None, TLP="white"), "n", 1, PUBLIC, 5) is not None, True)

# ---- quality / robustness ----------------------------------------------
chk("too few indicators rejected", I.otx_pulse_item(pulse(indicators=[{"type": "domain"}] * 4), "n", 1, PUBLIC, 5), None)
chk("bad created date rejected", I.otx_pulse_item(pulse(created="yesterday"), "n", 1, PUBLIC, 5), None)
chk("missing created rejected", I.otx_pulse_item({k: v for k, v in pulse().items() if k != "created"}, "n", 1, PUBLIC, 5), None)

# ---- fetch_otx: key handling, pagination, TLP accounting ----------------
chk("no key -> skipped, no request", I.fetch_otx("n", 1, 5, "", datetime.now(timezone.utc), PUBLIC, 5), [])


class Resp:
    def __init__(self, j):
        self._j = j

    def raise_for_status(self):
        pass

    def json(self):
        return self._j


calls = []
pages = [
    {"results": [pulse(id="p1"), pulse(id="p2", tlp="amber")], "next": "https://otx.alienvault.com/api/v1/pulses/subscribed?page=2"},
    {"results": [pulse(id="p3")], "next": None},
]


def fake_get(url, params=None, headers=None, timeout=None):
    calls.append((url, params, headers.get("X-OTX-API-KEY")))
    return Resp(pages[len(calls) - 1])


real_get = I.requests.get
I.requests.get = fake_get
try:
    got = I.fetch_otx("AlienVault OTX", 0.75, 5, "k3y", datetime(2026, 10, 10, tzinfo=timezone.utc), PUBLIC, 5)
finally:
    I.requests.get = real_get
chk("follows 'next' pagination", len(calls), 2)
chk("API key sent as header", calls[0][2], "k3y")
chk("lookback passed as modified_since", calls[0][1]["modified_since"], "2026-10-10T00:00:00")
chk("next page uses the URL's own query", calls[1][1], None)
chk("AMBER pulse dropped, public ones kept", [x["summary"].split("pulse/")[1][:2] for x in got], ["p1", "p3"])

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
