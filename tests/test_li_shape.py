"""Guarantee every LinkedIn post carries a working route to the site.

The first-comment API 403s on our organization token, so a comment-only link
strategy silently shipped dead-end posts. These lock in the fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from publishers import _li_shape          # noqa: E402
from marketing_post import _delink        # noqa: E402

fails = []


def chk(name, got, want):
    ok = (want in got) if isinstance(want, str) else want(got)
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)
        print("   got:", repr(got))


t = "Hook.\n\nBody.\n\n#CyberSecurity #ThreatIntel #BlueTeam #VulnerabilityManagement #SysAdmin"
o = _li_shape(t)
chk("5 tags trimmed to 3", o, lambda x: x.count("#") == 3)
chk("body preserved", o, "Body.")
chk("link hint injected when absent", o, "cti.securitycyber.uk")
chk("hint sits above the tag line", o,
    lambda x: x.index("cti.securitycyber.uk") < x.index("#CyberSecurity"))

o2 = _li_shape("Hook.\n\nBody: cti.securitycyber.uk\n\n#CyberSecurity #ThreatIntel #MSP")
chk("existing domain not duplicated", o2, lambda x: x.count("cti.securitycyber.uk") == 1)
chk("three tags left alone", o2, lambda x: x.count("#") == 3)

o3 = _li_shape("Hook.\n\nWe track this daily.\n\n(Link to subscribe in the comments.)"
               "\n\n#CyberSecurity #ThreatIntel #BlueTeam")
chk("dead comment promise removed", o3, lambda x: "in the comments" not in x.lower())
chk("promise replaced by a real route", o3, "cti.securitycyber.uk")

o4 = _li_shape("Hook.\n\nBody with no tags.")
chk("no tag line invented", o4, lambda x: "#" not in x)
chk("hint still added without tags", o4, "cti.securitycyber.uk")

o5 = _li_shape("Full write-up and source: link in the comments\n\nBody."
               "\n\n#CyberSecurity #ThreatIntel #ICS")
chk("promise stripped mid-post", o5, lambda x: "in the comments" not in x.lower())

o6 = _li_shape("Hook.\n\nBody.\n\n#CyberSecurity\n#ThreatIntel\n#BlueTeam\n#MSP")
chk("multi-line tag block capped at 3", o6, lambda x: x.count("#") == 3)

# _delink: scheme goes, domain stays — no scheme means no reach penalty, but
# the reader still has somewhere to go.
chk("scheme stripped, path kept",
    _delink("Start free: https://securitycyber.uk/cti"), "securitycyber.uk/cti")
chk("no scheme left behind",
    _delink("Start free: https://securitycyber.uk/cti"), lambda x: "http" not in x)
chk("www stripped too",
    _delink("See https://www.securitycyber.uk/cti"), lambda x: "www." not in x)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILURES: {', '.join(fails)}")
sys.exit(1 if fails else 0)
