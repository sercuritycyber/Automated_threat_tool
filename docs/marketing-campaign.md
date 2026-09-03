# SecurityCyber CTI — zero-budget launch campaign

**Written:** 2026-09-03
**Goal:** first 10 paying subscribers (Individual or Team) at zero ad spend.
**Constraint:** every channel below is something we already own and run. No paid
media, no new tooling, no new subscriptions.

---

## 1. The core idea: the product is the marketing

The free social feed is not a teaser we bolt marketing onto — it **is** the
funnel. Every automated post the pipeline publishes is a live demo of the triage
that paid subscribers get in full. So the campaign is mostly:

1. Make the free feed visibly good and consistent.
2. Put a clear, low-friction path from each free post to `cti.securitycyber.uk`.
3. Add a small amount of human, founder-voice content on top.

If the feed is good, it sells itself. If it isn't, no amount of promotion helps.

**The funnel**

```
free social post  →  securitycyber.uk/cti  →  cti.securitycyber.uk  →  free-tier or paid signup
(LinkedIn/Masto/    (the new marketing      (pricing + channel        (Stripe)
 Bluesky/X, Discord,  page — what it is,      picker)
 site CVE feed)       who it's for)
```

---

## 2. Assets we already have (the whole toolbox)

| Asset | Role in the campaign |
|---|---|
| **`cti.securitycyber.uk`** | Conversion page — pricing, channel picker, Stripe |
| **`securitycyber.uk/cti`** (new) | Top-of-funnel explainer, SEO landing, nav-linked from every page |
| **securitycyber.uk blog** (`/blog`) | Long-form: launch post + monthly "what we caught" retro |
| **CTI pipeline auto-posting** | LinkedIn, Mastodon, Bluesky, X — already wired in `bin/publishers.py` |
| **LinkedIn company page** `security-cyber-uk` | The launch announcement + weekly founder commentary |
| **Discord `SecurityCyberUK`** | `#general` public (social feed mirror), subscribers-only channel = the paid demo people can see exists |
| **Public + private RSS** | "Subscribe in your reader" is a zero-friction ask; private feed is a paid feature to show off |
| **Resend email** | Launch email to anyone who's ever contacted `securitycyber.uk`; the branded alert template is itself a sales asset (screenshot it) |
| **Free tier** | Lead magnet — capture email, deliver real value, upsell on cadence |
| **GitHub org `sercuritycyber`** | Repo README badge + link; the `Automated_threat_tool` repo is credibility |
| **TryHackMe `opseccharlie` (Top 3%)** | Founder credibility, linked from every profile |
| **Personal LinkedIn `charlie-collins-cc`** | Reshare company posts with a personal note (2–5× the reach of a page post) |
| **Reddit accounts** | r/msp, r/cybersecurity, r/blueteamsec, r/sysadmin — *value-first* comments, occasional Show-and-Tell |
| **`securitycyber.uk/games`, `/resources`, `/cve`** | Existing organic traffic — every page now carries the CTI nav link |

---

## 3. Pre-launch checklist (do before announcing)

- [ ] `securitycyber.uk/cti` live (this PR) and linked in nav + footer sitewide
- [ ] `cti.securitycyber.uk` `CONFIG.live = true`, Stripe in live mode, ICO number set
- [ ] Discord subscriber role + OAuth callback working (blocker #4 in PROJECT-STATE)
- [ ] `INGEST_SECRET` matched so briefs actually reach subscribers (blocker #1)
- [ ] Fix Discord channel name typo: `threat-intellegance-subscribers` → `threat-intelligence-subscribers` (and its topic)
- [ ] Pipeline `publish.dry_run = false` **only** once the LinkedIn **company** URN
      is set (never post the launch from the personal profile by accident)
- [ ] 5–10 good alerts already in the public feed history, so a new visitor sees a track record, not an empty channel
- [ ] Screenshots ready: branded email brief (have 2), Discord feed, cti.json attachment, the `/cti` page hero
- [ ] Brand banner image finalised (`Gemini_Generated_Image_zfdy1tzfdy1tzfdy(1).jpg`)

---

## 4. Launch week (Week 1)

| Day | Channel | Action |
|---|---|---|
| **Mon** | LinkedIn company page + personal reshare | The launch post (draft: `drafts/linkedin-promo-2026-09-03.md`). 4 images: banner, 2× email brief, Discord feed. Pin it. |
| **Mon** | securitycyber.uk/blog | Long-form launch post: "Why we built a CTI feed for businesses that can't hire an analyst" — the problem, the method (CISA KEV cross-ref, dedup, primary-source verification), what an alert looks like, pricing. Canonical, links to `/cti`. |
| **Mon** | Mastodon + Bluesky (manual, founder voice) | Shorter variant from the promo draft. These communities dislike corporate tone — lead with the method, not the price. |
| **Tue** | Resend email | One plain-text email to prior `securitycyber.uk` contacts + anyone on the free tier: "We launched the paid feed. Here's what's different from the free posts you already get." One link. |
| **Tue** | Discord `#general` | Announcement + a genuinely useful pinned message explaining the free vs paid split and how to join the subscribers channel. |
| **Wed** | Reddit r/msp | Text post in the weekly self-promo / "what are you using" thread — framed for MSPs: "one filtered CTI stream to brief every client from". No hard sell; answer every comment. |
| **Thu** | X/Twitter | Thread: 6 tweets walking through one real recent alert the pipeline caught, ending with the link. |
| **Fri** | LinkedIn personal | "Launch week retro" — honest numbers (signups, feedback), what surprised us. Builds-in-public tone travels well. |
| **Sun** | — | Automated Sunday retention cron runs; check delivery logs, fix anything that failed silently. |

---

## 5. Ongoing cadence (Week 2+, ~2 hrs/week)

**Automated (already running, no work):**
- Every-4h pipeline → LinkedIn / Mastodon / Bluesky / X free posts
- Daily subscriber digest 08:15 UTC

**Weekly (manual, founder voice — batch on one afternoon):**
1. **One "week in review" post** (LinkedIn + Mastodon): the 2–3 items that
   actually mattered this week, one line each, "paid subscribers got the full
   brief + the action within minutes." Screenshot one real (redacted if needed)
   alert.
2. **One value post** with no CTA: a detection tip, a KEV explainer, a "how we
   score impact" breakdown. Pure goodwill — this is what makes the CTA posts land.
3. **Reply to 3–5 threads** on Reddit / LinkedIn / Mastodon where someone asks
   "how do you keep up with advisories" — answer properly, mention the feed once
   if it's genuinely relevant.

**Monthly:**
- Blog retro: "What SecurityCyber CTI flagged in <month>" — the KEV adds, the
  big ones, a couple we deliberately *skipped* and why (shows judgement). Great
  evergreen SEO, links to `/cti`.
- Update the `/cti` page "illustrative example" with a real recent one.

---

## 6. Content templates

### 6a. Free social post → CTA footer (add to the pipeline post template)
Every automated post already ends with the source link. Add one line, rotated:
```
— triaged by SecurityCyber CTI · full brief + the action, to your inbox or Slack: cti.securitycyber.uk
```
Keep it to the daily-cap posts, not every item, so it doesn't read as spam.

### 6b. Weekly "week in review" (LinkedIn / Mastodon)
```
This week in the feed — the ones that actually mattered:

1. <CVE / incident> — <one line>. On CISA KEV. If you run <product>, the action was <x>.
2. <…>
3. <…>

~30 other items came through. These three are what a defender needed to act on.
Paid subscribers got the full brief and the single action within minutes of us
confirming each one — to email, Slack, Discord or their RSS reader.

cti.securitycyber.uk · from £2/week
```

### 6c. Reddit (value-first, no link in the body — link in a comment if asked)
```
Title: How we cut the advisory firehose down to "what do I actually do today"

We run a small pipeline that pulls ~19 security feeds every few hours, cross-refs
each item against CISA's KEV catalogue, de-dupes the same story from five outlets,
and writes a short brief per real threat — what it is, who's affected, the one
action, what to hunt for. Happy to share the approach / the scoring logic if useful.
```

### 6d. Cold-ish outreach (existing contacts only — never scraped lists)
```
Subject: the paid CTI feed is live

You've probably seen our threat posts on LinkedIn. Those are the free tier — one
or two headlines a day. The paid feed is the analyst write-up on all 20–40 items:
the ones that matter to your stack, the single action, delivered where your team
already works. From £2/week, cancel anytime. cti.securitycyber.uk
```

---

## 7. What NOT to do

- No paid ads, no "growth hacks", no scraped email lists, no engagement pods.
- Don't post every pipeline item with a CTA — it burns the feed's credibility.
- Don't overstate: it's *informational aggregation with triage*, not a managed
  service or a guarantee. The terms page says this; the marketing must match.
- Don't launch the LinkedIn post from the personal profile (company URN first).
- Don't promise "instant" — say "minutes from confirmation, not from first publish".

---

## 8. Metrics (all free to track)

| Metric | Source | Target, month 1 |
|---|---|---|
| `/cti` page views | GA4 (`G-F43K0Y370J`) | 300 |
| `cti.securitycyber.uk` visits | Cloudflare Worker analytics / observability | 150 |
| Free-tier signups | D1 `subscribers` where tier is free | 40 |
| Paid signups | D1 `subscribers` + Stripe | 10 |
| Free → paid conversion | derived | 10–15% |
| Social post → site clicks | UTM `?utm_source=li` etc. on the CTA links | — |

Add `?ref=li` / `?ref=blog` / `?ref=discord` to the CTA URLs so the Worker can
log which channel converts, then double down on that one.

---

## 9. One-line pitch bank (reuse everywhere)

- "The two CVEs this week that can actually hurt you — on your screen before you go looking."
- "A threat-intelligence analyst costs £50k a year. This is that function, as a feed."
- "You don't have an information problem. You have a triage problem."
- "Threats get in. Good defence gets them out."
- "Stop reading feeds. Start getting told."
