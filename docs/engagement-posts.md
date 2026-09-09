# Engagement posts — teach one technique, drive one click

**What this is:** thought-leadership / educational posts for the SecurityCyber
company page (LinkedIn, Bluesky, Mastodon). Each one teaches a real detection in
a few lines, then points at something on securitycyber.uk — the **Packet Hunter**
drill or the **CTI** 7-day trial. These are *not* CTI alerts and *not* the
pure-promo autoposter bank (`bin/marketing_content.json`); post them by hand,
roughly one a week, spaced away from the promo slots.

**Positioning (the whole company runs on this):** the free social feed is the
hook — major CVSS 9.0+ criticals only, high-level, delayed a few hours. The paid
feed (£2/week, email + Discord) is the operational tool — every vector, the full
"do this now" with build numbers and hunting queries, pushed instantly. Every
emergency the analyst posts publicly ends with a line that the paid feed already
had the full brief. The 7-day trial (no card) lets a defender see the gap. When
you write CTA copy, drive to the trial, not a "free plan".

**House rules** (same as the marketing voice): British spelling, short lines,
facts only, no fabricated metrics, no "comment below" bait. One clear link.
3–4 real hashtags. Anchors: `#CyberSecurity #ThreatHunting #BlueTeam
#SECURITYCYBERUK`.

**Links to rotate:**
- Packet Hunter drill → `https://securitycyber.uk/packet-hunter/`
- CTI (free plan, no card) → `https://securitycyber.uk/cti`

---

## 1. certutil.exe — the download that isn't a download

certutil.exe is a signed Microsoft binary for certificate work. Attackers use it
to pull payloads straight past application allow-listing.

The command line is the tell. `-urlcache`, `-f`, `-split` or `-decode` on
certutil is almost never an admin managing certificates:

```
DeviceProcessEvents
| where FileName =~ "certutil.exe"
| where ProcessCommandLine has_any ("urlcache", "-decode", "-split")
| join kind=inner (DeviceNetworkEvents) on DeviceId
| where Timestamp between (DownloadTime .. DownloadTime + 1h)
| project DeviceName, ProcessCommandLine, RemoteIP, RemotePort
```

Isolate the RemoteIP, enrich it, block the infrastructure. The same hunt covers
the other download LOLBins — bitsadmin, mshta, regsvr32 /i:http, curl on modern
Windows.

We built this exact scenario into Packet Hunter — spot the certutil callback in
20 lines of endpoint traffic before the timer runs out:
https://securitycyber.uk/packet-hunter/

#CyberSecurity #ThreatHunting #BlueTeam #SECURITYCYBERUK

---

## 2. The beacon that jitters

Junior analysts are taught to hunt C2 by looking for a perfectly regular
callback — every 300 seconds, on the dot. Real malware authors know that, so
they add jitter: the target interval plus or minus a random percentage.

Zero-variance checks miss it. What still gives it away:

- bucket the gaps between connections — a jittered beacon clusters tightly
  around one value (say, 90% of gaps land in an 8–12 minute band)
- the request sizes are near-identical every time
- it runs for hours to one destination

Human browsing never clusters like that. RITA and Zeek's interval-histogram
approach both score jittered beacons directly.

Can you find the jittered beacon in four hours of netflow? It's the hard round in
Packet Hunter: https://securitycyber.uk/packet-hunter/

#CyberSecurity #ThreatHunting #BlueTeam #DFIR

---

## 3. Not every long hex subdomain is a DNS tunnel

Hunt DNS tunnelling on label length and entropy alone and you drown in false
positives. Akamai, CloudFront, Azure and Windows telemetry all resolve long
hex and base32 subdomains legitimately, all day.

The order of operations matters:

1. Allowlist the known-good parent domains first — and maintain that list.
2. On what's left, score on label length, entropy, unique-subdomain count per
   parent, and query cadence. Tunnels are steady.
3. Enrich the survivors: domain age, registrar, hosting ASN.

A domain registered last week that's answering thousands of unique long-label
queries at a fixed rate is a tunnel. A fifteen-year-old Akamai domain is not.

We turned this into a drill — real CDN noise as decoys, one genuine tunnel:
https://securitycyber.uk/packet-hunter/

#CyberSecurity #ThreatHunting #BlueTeam #SECURITYCYBERUK

---

## 4. Workstations don't mount each other's admin shares

One of the cleanest lateral-movement signals in a Windows estate: a single
workstation opening SMB sessions to many peers it has never spoken to, inside a
few minutes.

Servers fan out like that. A laptop does not.

```
DeviceNetworkEvents
| where RemotePort == 445
| where InitiatingProcessFileName !in ("System")
| summarize peers = dcount(RemoteIP) by DeviceId, bin(Timestamp, 5m)
| where peers > 10
```

Confirm with Windows Security event 5145 — `ADMIN$`, `C$` or `IPC$` in the share
name, from a non-admin host, is the smoking gun.

This is a Packet Hunter round: pick the SMB sweep out of a normal day of
internal traffic. https://securitycyber.uk/packet-hunter/

#CyberSecurity #ThreatHunting #BlueTeam #SECURITYCYBERUK

---

## 5. Exfiltration has a shape

You don't always get a signature. You do get a shape:

- bytes out much greater than bytes in — a one-way upload
- destination is a bare IP address, with no DNS lookup before it
- no referring page load — nobody browsed there

```
DeviceNetworkEvents
| where RemoteUrl matches regex @"^\d{1,3}(\.\d{1,3}){3}"
| summarize sent = sum(SentBytes), recv = sum(ReceivedBytes) by RemoteIP
| where sent > 5000000 and sent > recv * 10
```

The hard part isn't the query — it's having the destination context to know the
IP is bad before you block it. That's the enrichment layer a threat-intel feed
gives you.

SecurityCyber CTI is that layer for UK small teams — every advisory that matters,
the impact score, the one action. Free plan, no card: https://securitycyber.uk/cti

#CyberSecurity #ThreatIntelligence #BlueTeam #SECURITYCYBERUK

---

## 6. Every host has an honest TTL

Passive fingerprinting is an underused hunt. Every real host emits a consistent
IP TTL (64, 128 or 255 minus hop count) and a consistent TCP window and MSS.

A packet claiming a host's source IP but carrying a TTL or window that doesn't
match that host's other packets in the same conversation is spoofed or injected.

Rule of thumb: within one flow, a TTL delta greater than about 5 between packets
from the same source is worth a look. It's also how you catch some on-path
injection and rogue devices cloning an IP.

We hid one spoofed packet in a three-host session — can you find it before the
timer? https://securitycyber.uk/packet-hunter/

#CyberSecurity #ThreatHunting #BlueTeam #NetworkSecurity

---

## 7. You don't have an information problem

Every week brings dozens of "critical" advisories. Two or three of them actually
touch software you run and are being exploited right now. The rest is noise.

Missing the one that matters is how breaches happen — and reading the whole
firehose to find it is a full-time job most small teams can't staff.

SecurityCyber CTI does the triage. Every few hours it reads the public advisory
stream, cross-checks each item against CISA's Known Exploited Vulnerabilities
catalogue, and sends you a plain-English brief on the ones that matter — impact
score, who's affected, the single action to take. To email, Slack, Discord,
Telegram or a private RSS feed.

Free plan, £0, no card: https://securitycyber.uk/cti

#CyberSecurity #ThreatIntelligence #BlueTeam #SECURITYCYBERUK

---

## Notes on the post you drafted

Your certutil draft was solid on the technical substance. Three changes in
version 1 above:

- **Cut the length by half.** LinkedIn shows ~2 lines before "see more" — the
  hook has to carry it. The scenario framing (Tier-1 alert, Workstation-Alpha)
  is good context but doesn't need to be in the post.
- **Added a link.** The original had no call to action at all — nothing for an
  interested reader to click. Every post should land somewhere on the site.
- **Dropped "drop them in the comments 👇" and trimmed to 4 hashtags.** Seven
  hashtags reads as reach-farming; the company voice is straighter than that. If
  you want engagement, end on a genuine question instead: "What's your first
  filter when you hunt LotL binaries?" reads better than "drop them below".

The KQL in your draft also had the `project` in step 1 stripping the columns the
later steps needed — version 1's query keeps `ProcessCommandLine` through the
join.
