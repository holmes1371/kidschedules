# iOS app v1

Backlog item #20. Issue filed but board status stays in "Todo" until Tom
is ready to start slice 2 — Tom explicitly held this back from In
Progress on 2026-05-25 so the design has time to settle.

Native SwiftUI app for Tom + Ellen + a few family members. Reads the
schedule feed, writes ignore/complete actions, surfaces a Home Screen
widget and push notifications, and optionally syncs to Apple Calendar
via EventKit. Single-tenant (Ellen's schedule, family views it). v2
multi-tenancy is explicitly out of scope; the cost analysis at the
bottom captures the wall.

## Status

Design draft. No code yet. Tom reviews this note before slice 2 (JSON
feed) starts. The open-question list at the bottom is the next thing
that needs Tom's input.

## v1 scope

(see issue for the slice checkbox list)

In:
- SwiftUI iOS app, iOS 17+ (Lock Screen widgets, latest SwiftUI APIs)
- TestFlight distribution to Tom + Ellen + ~3–5 family slots
- Read: schedule view, parity with current site UI
- Write: ignore/complete with offline queue (closes #8)
- Home Screen widget: next event (small + medium variants)
- Push notifications via OneSignal (or alternative — see below)
- EventKit sync as a settings-gated opt-in, writes to a dedicated calendar

Out:
- Android (not in audience)
- Apple Watch (defer)
- Per-kid filtering UI in the app (defer; #9 covers color-coding for the
  website which will inform app design)
- Multi-tenant / per-user Gmail OAuth (see "v1→v2 cost wall" below)
- App Store public listing

## Architecture

### v1 data flow

```
Gmail (Ellen)
  → existing GitHub Actions cron (unchanged)
    → process_events.py
      → docs/index.html  (existing — website)
      → docs/events.json (NEW, slice 2 — iOS feed)
      → docs/ics/<id>.ics (existing per-event "Add to calendar" button)
      → docs/calendar.ics (rejected — see "Calendar subscription rejected")

  iOS app
    ← GET docs/events.json (public read, polled on launch + foreground)
    → POST to Cloudflare Worker: {event_id, action, device_token, signature}
      → Worker persists state to (TBD: gist / GitHub API / KV)
      → next cron picks up state, re-renders

  Cron (post-rebuild)
    → OneSignal HTTP API: send push for "new this week" / "tomorrow digest"
      → APNs → iOS app
```

### Calendar subscription rejected

Considered publishing `docs/calendar.ics` as a subscription feed so iOS
Calendar would auto-pull every event. Rejected by Tom 2026-05-25: the
entire point of the schedule is to surface what *needs attention*, and
dumping every PTA bake sale into the main calendar is excessive clutter.
The per-event "Add to calendar" button (see `design/ics-export.md`)
stays as the user-curated path; the bulk subscription path is dropped.
EventKit sync inside the app (settings-gated, opt-in) covers users who
*do* want everything in Calendar.

### EmailSource pluggability — yes, and the abstraction is mostly already there

Tom asked 2026-05-25: can we build the backend so the Gmail extraction
piece could be swapped out later? Concrete answer: **yes, and it's the
right shape — but we don't need to do the formal refactor today.**

The pipeline is already cleanly partitioned. `gmail_client.py` is a
self-contained module; the only Gmail-specific concepts that leak into
`main.py` are (a) message IDs as cache keys (which any email source has
an equivalent for — message-id headers are universal), and (b) label
management for `gmail.modify`-based dedupe (which is `gmail.modify`-
specific but already replaceable by message-ID caching in
`events_state.json`, see `design/incremental-extraction.md`). Everything
downstream — agent extraction, dedupe, ICS rendering, HTML rendering,
the JSON feed slice 2 will add — operates on the abstract "list of
emails with bodies and metadata" shape, not on Gmail-specific objects.

The interface that `gmail_client.py` implicitly satisfies:

```python
class EmailSource(Protocol):
    def fetch_messages(self, since: datetime, queries: list[str]) -> list[Message]: ...
    def message_id(self, msg: Message) -> str: ...
    def body_text(self, msg: Message) -> str: ...
    def attachments(self, msg: Message) -> list[Attachment]: ...
    def headers(self, msg: Message) -> dict[str, str]: ...
    # optional, only used by gmail.modify path:
    def mark_processed(self, msg: Message) -> None: ...
```

Concrete implementations a future Tom could write, ranked by usefulness:

- `EllenGmailSource` — what we have today, wrapping `gmail_client.py`. ~10
  lines once the protocol exists.
- `IMAPEmailSource` — works for **any IMAP provider** including iCloud
  Mail (app-specific passwords, no OAuth), FastMail, custom domains,
  most ISPs. **Bypasses Google verification entirely** for non-Gmail
  users. Estimated ~150 lines using `imaplib`. **This is the sneaky
  multi-tenant path Tom should know about** — see v1→v2 section.
- `OutlookGraphSource` — Microsoft Graph API. Has its own verification
  story (Microsoft Partner Center), separate problem from Google's, and
  for personal Outlook accounts the bar is lower.
- `GoogleOAuthSource` — the future multi-tenant Gmail one, post-CASA.
  Same wire format as `EllenGmailSource`, different credential plumbing
  (per-user OAuth refresh tokens out of a KMS).
- `MboxEmailSource` — read a local `.mbox` file. Pure-test utility, but
  would make the test suite no longer need to mock Gmail.

**Recommendation: don't extract the formal `EmailSource` protocol now.**
Reason: today the abstraction exists *implicitly* — `gmail_client.py` is
already coherent and isolated. Adding the protocol now is YAGNI per
karpathy-guidelines #2 (Simplicity First) unless a second source is
actually about to be written. Document the interface here so future-Tom
knows the shape; do the extraction on the day a second source lands. Cost
to extract later: ~half a day, no downstream breakage because the
abstraction respects the seams that already exist.

### Push provider

Trade-offs:

- **OneSignal (recommended for v1).** Free up to 10K MAU (we'll be
  well under). Handles APNs token management server-side, exposes a
  simple HTTP API the cron hits. SDK well-maintained. iOS SDK adds
  ~2–3 MB to the app binary. Downside: third-party dependency, possible
  drift / vendor risk; if pricing changes we're stuck or migrating.
- **Firebase Cloud Messaging (FCM).** Free, Google-backed. SDK heavier
  (pulls in lots of Firebase). Configuration is famously painful.
  Reasonable second choice if OneSignal sours.
- **Self-hosted APNs via Cloudflare Worker.** Lowest dependency
  footprint. Worker holds the APNs auth key + device tokens in KV,
  signs payloads, hits Apple's HTTP/2 endpoint. Realistic effort: ~200
  lines of Worker code + KV setup + key management. Best long-term
  answer if the project survives 3+ years, premature for v1.

*Pick (recommendation): OneSignal. Tom confirms in next session.*

### Write endpoint

Trade-offs:

- **Cloudflare Worker (recommended).** Free tier covers our volume by
  4+ orders of magnitude. Custom subdomain. Worker accepts POST
  `{event_id: str, action: "ignore"|"complete", device_token: str,
  signature: str}`, validates the signature against an HMAC key shared
  with the app, and persists state. Closer to the v2-multi-tenant world
  (real auth boundary, real backend, real logs).
- **Extend Apps Script webhook.** Smallest delta — webhook already
  exists for the website's ignore-button (`scripts/apps_script.gs`).
  But Apps Script is brittle (concurrent-execution quotas, opaque
  failure modes), no real auth, hard to debug. Would have to live for
  years.

*Pick (recommendation): Cloudflare Worker. Tom confirms.*

#### Where written state lives — three sub-options

1. **Worker → GitHub API → commit to repo.** Worker writes directly to
   `ignored_events.json` / `completed_events.json` via the GitHub
   contents API. Pro: state lives where the cron reads it; no schema
   split. Con: slow per-write (~1–2 sec), commit noise.
2. **Worker → separate JSON gist.** Cron-side reads the gist at the
   start of each run. Pro: cleaner separation, commits stay clean.
   Con: extra moving part.
3. **Worker → Cloudflare KV.** Cron fetches KV via the CF API at the
   start of each run. Pro: fastest, cleanest. Con: cron now depends on
   a CF API key + an outage in CF could block the cron.

*Pick (recommendation): option 2 (gist). Lowest blast radius, easiest
to debug. Revisit if it gets annoying. Tom confirms.*

#### Auth model

Each iOS app install generates a UUID on first launch, stores it in
Keychain, and submits it to the Worker on first sync. Worker returns a
per-device HMAC secret that the app stores and uses to sign subsequent
writes. No user accounts; auth is per-device. Family members install
via TestFlight invite (already restricted to invited Apple IDs).
Defense in depth: Worker enforces per-device rate limits (10 writes /
min). Compromised device token can be revoked by deleting it from the
Worker's KV.

### JSON feed schema (slice 2)

`docs/events.json` published alongside the HTML. Same source data — both
written from `process_events.py` from the same in-memory event list.

```json
{
  "generated_at": "2026-05-25T10:15:00-04:00",
  "schema_version": 1,
  "events": [
    {
      "id": "abc123def456",
      "name": "Spring Concert",
      "date": "2026-06-03",
      "time": "7:00 PM",
      "time_range": {"start": "19:00", "end": "20:00"},
      "all_day": false,
      "kids": ["Sam", "Lily"],
      "location": "Pine Elementary Auditorium",
      "source_url": "https://...",
      "ignored": false,
      "completed": false,
      "is_new_this_week": true,
      "color_tag": "school"
    }
  ]
}
```

`id` is the existing stable `_event_id` (already used for ICS UIDs and
ignore-state keying). `schema_version` is explicit so the app can refuse
incompatible feeds. Optional fields omitted when not present rather than
emitted as null.

### EventKit sync model (in-app, settings-gated)

When the user enables Calendar Sync in app settings:

- App requests EventKit write permission.
- Idempotently creates a calendar named "Kids" (configurable) in the
  local source.
- On each feed fetch: compute target EventKit state, diff against
  existing.
- Dedupe key: store map `event_id → EKEvent.eventIdentifier` in app's
  local storage (Core Data, or just UserDefaults at this scale).
- Updates rewrite; deletes remove. Ignored/completed events delete from
  the calendar so it stays clean.

### Widget (Home Screen)

WidgetKit, small + medium. Reads from an App Group shared container
that the main app populates after each fetch. Content:

- **Small:** next dated event (name + relative date "Tomorrow" / "Fri")
- **Medium:** next 2–3 events with date + name

Refresh: background fetch on app launch + WidgetKit's own timeline
refresh (~hourly).

### Push triggers (cron-side)

Defined in `process_events.py` (or a new `scripts/push_triggers.py`):

1. **"New this week" push.** When a freshly extracted event lands
   within the next 7 days, fire a push when the cron finishes
   successfully. Body: `<kid name>: <event name> on <weekday>`.
   Throttle: max 3 pushes per cron run; batch beyond that into a
   digest.
2. **"Tomorrow digest" push.** Cron run that lands in a configured
   evening window (TBD) and finds dated events for tomorrow fires a
   digest push. Body: `Tomorrow: <event1>, <event2>, …`. Once per day
   max.
3. **Quiet hours.** No pushes between 10pm and 6am ET.

Sent via OneSignal HTTP API; the cron just POSTs the payload.

*Trigger set + quiet hours: Tom confirms.*

## v1→v2 cost wall: what scaling beyond family actually takes

Captured because Tom asked 2026-05-25 and the answer might inform a
future "is this worth pursuing?" decision. Numbers are estimates from
public information, not professional quotes.

### Gmail OAuth verification — the dealbreaker

Pipeline uses `gmail.modify` to label processed messages. That's a
**restricted scope**. To go beyond OAuth's test-user limit (100 users
with 7-day refresh-token expiry) requires:

- Google brand verification — domain ownership, privacy policy URL,
  terms of service, app demo video. Low cost, ~1–2 days of work.
- **CASA Tier 2 security assessment** by a Google-approved third party
  (Bishop Fox, NCC Group, Leviathan, etc.). Typical first-assessment
  cost: **$15K–$75K**. Annual re-assessment: **$5K–$15K**. Timeline:
  **2–6 months** end-to-end including remediation.

Workarounds within Google's world:

- Drop to `gmail.readonly` — lower verification bar (still verification,
  no CASA). Cost: lose label-based incremental extraction (see
  `design/incremental-extraction.md`); needs redesign so dedupe runs
  purely on message IDs cached in state. Doable.
- Workspace-only product — service accounts skip consumer-Gmail
  verification entirely. Different market (schools, daycare networks),
  probably not what Tom wants.

### The sneaky path: IMAP for non-Gmail providers

If the future-multi-tenant codebase implements `IMAPEmailSource` (see
"EmailSource pluggability" above), users on **iCloud Mail, Outlook,
Yahoo, FastMail, ProtonMail-Bridge, most ISPs, and any custom domain**
can be onboarded with an app-specific password — **no Google involved,
no verification needed, no CASA.** This works *today* for those users,
because Apple/Microsoft/etc. don't impose Google's restricted-scope
review. Implementation cost: ~150 lines of `imaplib`-based Python +
testing against ~3 real provider accounts.

The limitation: **doesn't help users on Gmail.** Gmail with an
app-specific password still routes through Google's auth, which is the
thing we're trying to dodge. So the IMAP path expands the addressable
market to "anyone not on Gmail" — about 65% of US email accounts.
Decent fallback for the "friends in the neighborhood" use case if those
neighbors happen to use iCloud or Outlook.

### Backend rewrite (independent of email source)

Current GitHub-Actions-cron + GitHub-Pages model is unbeatable for one
tenant and **fundamentally doesn't multi-tenant**, regardless of
EmailSource abstraction:

- Per-user accounts (Sign in with Apple/Google), per-user auth.
- KMS-encrypted per-user secrets storage for email credentials (refresh
  tokens, IMAP passwords) — they're credentials, can't sit in env vars
  or flat JSON.
- Per-user state in Postgres or SQLite (replaces flat
  `events_state.json` etc.).
- Per-user job scheduling — one cron iterating users with concurrency
  and failure isolation.
- Real hosting (Fly.io / Render / Railway) — $50–$200/mo at beta scale.
- Hard data-isolation: a leak of user A's events into user B's feed is
  catastrophic.

### Per-user personalization

Current `class_roster.json`, `blocklist.txt`, `protected_senders.txt`,
`pdf_sender_domains.txt`, `freemail_domains.txt`, and kid-name query
logic are hand-tuned for Ellen. Multi-tenant means each user runs an
onboarding flow to declare their kids/schools and build their own
blocklists, *or* a smarter classifier learns these per-user. Either is
real product work.

### Anthropic API costs

Currently small thanks to aggressive caching. At 100 users probably
**$100–$500/mo** on Sonnet; at 10K users **$5K–$50K/mo**. Manageable
for a beta, demands prompt-caching + batch-API revisits at scale.
Billing model needed unless eating the cost.

### Compliance / privacy / support

Processing personal email is GDPR/CCPA territory. Requires privacy
policy + terms + a real data-handling story. App Store review for a
Gmail/email-reading app gets scrutiny. Support is rough: when a user
reports "my schedule is wrong" the debug path involves looking at their
email, which is itself sensitive.

### Realistic effort

- **v1 family-app → 100-user TestFlight beta (Gmail+IMAP mix):** 3–6
  months solo full-time, or 9–18 months nights-and-weekends. **$20K–
  $80K** out-of-pocket if Gmail verification is in scope; **~$1K–$5K**
  if IMAP-only (skips CASA entirely).
- **100-user beta → App Store public:** another 3–6 months — Apple
  review, marketing site, support pipeline, billing.

### Pragmatic middle paths (no CASA, no rewrite)

- **BYO-fork.** Each tech-comfortable friend forks the repo, plugs in
  their own Gmail creds in GitHub secrets, gets their own Pages URL.
  iOS app accepts a configurable feed URL. Each user is their own
  developer; no verification needed. Friction: GitHub literacy
  required. Realistic for ~5 techy friends, not consumer.
- **"Run for them" via test users.** Tom runs separate cron workflows
  per family in one repo (one workflow per family, separate secrets per
  family), each producing `docs/<family>/events.json`. App accepts a
  family identifier at install. Tom's OAuth app covers each family as a
  test user (capped at 100, 7-day refresh-token problem unless in
  Production with each family individually verified). Friction: manual
  per-family onboarding.
- **IMAP for the non-Gmail neighbors.** Once `IMAPEmailSource` exists,
  iCloud / Outlook / FastMail / etc. neighbors can be added to the
  "run for them" model with zero Google involvement. Doesn't dodge the
  backend rewrite, but it does dodge the $50K verification bill.

**Honest assessment:** there is no "modest extra effort to scale to a
few neighbors" tier *with Gmail-using neighbors*. Gmail verification is
binary. For neighbors on non-Gmail providers, the IMAP path is real and
cheap. For Gmail neighbors, the choices are (a) BYO-fork, (b) "run for
them" inside the test-user cap, or (c) commit to investor-funded SaaS
scope.

## Open questions (Tom decides before slice 2 starts)

- Confirm Cloudflare Worker over extending Apps Script.
- Confirm OneSignal over self-hosted APNs.
- Confirm gist over GitHub-API-commit over Cloudflare-KV for write-state.
- Confirm push trigger set: new-this-week + tomorrow-digest, quiet
  hours 10pm–6am.
- Confirm EventKit calendar name ("Kids" recommended).
- Confirm widget content (next event small / next 2–3 medium).
- App branding: keep "kidschedules" or rebrand?
- Confirm EmailSource refactor is deferred (don't extract the protocol
  until a second source is actually about to be written).

## Commit plan (slice 2 onward — does not start until Tom approves this note)

Slice 2:
1. This design note (slice 1).
2. `docs/events.json` emitter in `scripts/process_events.py` + pytest
   fixtures + a snapshot test of the JSON output.
3. Issue close-out comment with SHA; manual-verification box stays
   unchecked until Tom confirms the feed renders correctly post-cron.

Slice 3+: separate design-note / commit plans drafted at the time, not
pre-baked here.

## Test fixtures

Slice 2 extends `fixtures/test/basic_mixed.json` (already covers timed
+ all-day events) — same input, new JSON snapshot in tests. No new
fixture file needed unless edge cases surface.
