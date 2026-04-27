# Extract events from PDF newsletter attachments

ROADMAP item #33. Some teachers (school classrooms, room-parent
threads) send weekly/monthly newsletters as PDF *attachments*
rather than HTML body content. Today the pipeline only feeds the
email body to the agent, so dates buried in those PDFs are
silently dropped — Ellen never sees them as cards.

**Concrete example (committed at `fixtures/test/pdf_newsletter_third_grade.eml`).**
From `mlrohde@fcps.edu`, subject "Third Grade Newsletter &
Important Updates", a 1-page PDF with a 4-quadrant box layout
(Math / Social Studies / Reading / Writing) and a bottom block
of 5 dated events (Louise Archer Day, Spring Picture Day Make-Up,
2-Hour Early Release, Spring Break, No School). Pure-text
extraction (`pypdf`) flattens the box layout to a wall of
unrelated bullets — the Upcoming-Dates block is mixed with
unrelated curriculum bullets and the dates lose their context.

**Complexity: medium.** Three layers (gmail_client / agent /
main), a new content-block shape on the Anthropic call, and a
new sender-gating list. Cost-bounded by sender gating; failure
modes are all "skip-and-warn, don't fail the batch."

## Resolved decisions (2026-04-27 with Tom)

- **Approach: Anthropic native PDF (`document` content block).**
  Send the PDF bytes directly as
  `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}`
  alongside the existing text user-message. Claude handles
  layout natively (calendars, tables, columns, scanned pages).
  No new Python dependency. Cost: ~1.5–3k input tokens per
  page; ~$0.01–0.02 per typical 1–4 page newsletter on
  Sonnet 4.6. Per-PDF cost is small; PDF-bearing emails are
  rare; total expected impact <$1/month even at heavy use.
  Alternatives `pypdf` and `pdfplumber` were rejected for
  text-extraction-mangles-layout reasons documented above.
- **Sender gating: file-backed list `pdf_sender_domains.txt`
  at repo root, seeded with `fcps.edu`.** Mirrors the existing
  `protected_senders.txt` / `freemail_domains.txt` pattern.
  Loader at `scripts/pdf_sender_domains.py` reuses the
  `is_protected` matcher from `protected_senders.py` since the
  semantics are identical (bare-domain matches self+subdomains,
  `*suffix` patterns supported, `#` comments allowed). Only
  messages whose `From:` resolves to a domain in this list are
  considered for PDF extraction — Costco receipt PDFs in
  personal emails won't burn tokens.
- **Per-PDF size cap: 8MB.** The fixture is 121KB; school
  newsletters in practice are 100KB–2MB. 8MB is comfortably
  above the realistic max while well under Anthropic's hard
  limits (32MB / 100 pages). PDFs over 8MB are skipped with a
  warning — the email body still runs through the agent
  normally, so a too-large attachment degrades to today's
  behavior rather than failing the batch.
- **PDF-bearing emails always run batch-of-1.** Simpler API
  call shape (one set of document blocks per request), and it
  side-steps the edge case where a multi-PDF batch confuses
  the agent about which document belongs to which email.
  Implemented by treating PDF-bearing emails as honorary
  newsletter senders for the duration of the run; #17's
  `_plan_batches` already supports a per-email batch-of-1
  partition. No schema change to `sender_stats.json` — the
  PDF override is computed run-locally each cron, not learned.
- **Source-date directive (#31) preserved verbatim.** Email's
  sent date is still the source date, regardless of the PDF's
  edition label. The fixture's PDF reads "EDITION: MARCH 25TH,
  2026" but the email was also sent Mar 25 — the directive
  doesn't need to special-case PDFs because the rule already
  says "the date THIS specific email was sent," not "any date
  visible in the input."

## Architecture

```
1. step2_search_gmail            (unchanged — Gmail query layer)
2. step2b_read_promising:
   - For each promising message:
     - gmail.read_message() returns body as today + a NEW
       `pdfs: list[bytes]` field (always populated; empty
       list when no PDF parts).
     - PDFs >8MB are silently dropped at fetch time with a
       warning; smaller PDFs are decoded into bytes.
3. main.py wiring:
   - Load pdf_sender_domains.txt once at run start.
   - For each email coming out of step2b, drop the `pdfs` list
     (set to []) when the sender's domain doesn't match the
     gating list. Non-school PDFs never reach the agent.
4. step3_extract_events / agent.extract_events:
   - When an email's `pdfs` list is non-empty, the agent puts
     it in its own batch-of-1 (#17's batching infrastructure
     already handles per-email partitions).
   - The user-message body stays as today; a `document` block
     is added to the content list for each PDF byte string.
   - Multi-block content shape:
       content = [
         {"type": "document",
          "source": {"type": "base64",
                     "media_type": "application/pdf",
                     "data": <b64>}},
         ...one per PDF...,
         {"type": "text", "text": <existing user_message>},
       ]
   - For batches with no PDFs, the call shape stays as today
     (string content) — no churn on the common path.
5. Prompt directive (extends `_EXTRACTION_BASE_PROMPT`):
   "If the email includes a PDF attachment, extract dates
    from it the same way you would the email body. Source
    date is the email's sent date (#31 directive), NOT the
    PDF's edition label or any date label inside the PDF."
6. Cache (#4) / dedupe (#21) / source-date (#31) /
   newsletter-classifier (#17) all unchanged — same
   messageId-keyed cache, same per-thread dedupe upstream,
   same prompt-pinned source-date rule, same per-sender
   stats updating.
```

## Design Q&A

**Q1 — Why not run all PDF emails through batch-of-1
unconditionally rather than the per-email `pdfs`-non-empty
check?** That would mean the classifier's "newsletter" set
(promoted via #17's stats over time) and the PDF-eligibility
set (computed per-run from the gating file) would conflate.
The classifier's promotion is statistical; PDF-eligibility is
deterministic from the file. Keep them separate so a teacher
who sometimes sends a body-only email and sometimes attaches
a PDF gets the right batching for each — body-only batches
freely with siblings, PDF-bearing forces batch-of-1.

**Q2 — Should the `pdfs` list be carried through to the
events_state cache for inspection later?** No. The cache
stores the agent's *output* (events). PDF bytes are
upstream input that doesn't need to survive the run. If a
test extraction was wrong, the message is re-extractable via
`--reextract <messageId>` (the existing eviction path), which
re-fetches Gmail (including the attachment) fresh from the
API.

**Q3 — What about `messages.attachments.get` vs inline body
data?** Gmail returns small attachments inline in
`part.body.data` (base64) and large ones (>5MB-ish) as a
reference with `part.body.attachmentId`. The implementation
must handle both: use the inline data when present, otherwise
make a second API call to fetch the bytes. Both paths are
covered by tests with hand-crafted Gmail-response stubs.

**Q4 — What if Anthropic returns an error on a PDF (e.g.
malformed/encrypted)?** The whole batch retries once via the
existing `_call_with_retry` infrastructure. If it fails again
the batch fails — but since PDFs always batch-of-1, only that
one email's events are lost for the run. Cache (#4) is NOT
updated on failure (the message stays "unprocessed" so the
next cron retries). Same posture as today's body-only
extraction failure.

**Q5 — What about PDFs with multiple pages and irrelevant
content (e.g. teacher-of-the-week shoutout, classroom photos,
back-of-newsletter ads)?** The agent already filters
non-event content from email bodies; PDFs work the same way.
The fixture's PDF has 4 quadrants of curriculum bullets that
correctly extract as zero events because they have no dates.
The bottom box's 5 dated events are the only events surfaced.

**Q6 — Subject keyword gating?** No — sender-domain gating is
sufficient. A teacher's email with subject "Quick question
about volleyball" and no PDF attachment still flows through
the body-only path. A teacher's email with a PDF attachment
flows through the PDF path. The gate is "is this a school
domain?", not "does this email look newsletter-shaped?".

## Test fixtures

`fixtures/test/pdf_newsletter_third_grade.eml` — full .eml
saved verbatim. Tests parse it with stdlib `email` to simulate
the Gmail-API response shape. The fixture is a *sample*, not
a template — different teachers will format differently
(layout, page count, curriculum bullets vs lunch menus, etc.).
Tests should pin behavior on the fixture but not assume all
PDFs follow this shape.

New tests:

- `tests/test_pdf_sender_domains.py` — loader (file present,
  missing, malformed), `is_pdf_sender` matching cases (bare
  domain, subdomain, mismatch, empty sender, address sender).
- `tests/test_gmail_client.py` (additions) — `read_message`
  returns `pdfs` key (empty list, single inline PDF, single
  reference-style PDF requiring a second fetch, oversized PDF
  skipped with warning, multiple PDFs).
- `tests/test_agent.py` (additions) — content-block shape
  when emails have PDFs, prompt directive pinned (literal
  phrases including "PDF attachment", "edition label"),
  batch-of-1 forcing for PDF-bearing emails, `messages.create`
  called with list-of-blocks instead of string.
- `tests/test_main.py` (additions) — sender gating: `mlrohde
  @fcps.edu` keeps PDFs, `random@gmail.com` drops them; file
  missing → empty list → all PDFs dropped (defensive default).

No live Anthropic API calls in any test — all use mocked
clients matching the existing test posture.

## Responsibility table

| Concern | Workflow | gmail_client.py | agent.py | main.py |
|---|---|---|---|---|
| Detect PDF MIME parts | — | ✅ | — | — |
| Fetch attachment bytes (inline/reference) | — | ✅ | — | — |
| Per-PDF size cap (8MB) | — | ✅ | — | — |
| Sender-domain gating | — | — | — | ✅ |
| Force batch-of-1 for PDF emails | — | — | ✅ via `_plan_batches` partition | ✅ via newsletter-set merge |
| Build `document` content block | — | — | ✅ | — |
| Prompt directive | — | — | ✅ | — |
| `pdf_sender_domains.txt` load | — | — | — | ✅ |

## Commit plan

5 commits at natural boundaries:

1. **Design note + ROADMAP `[~]` flip + .eml fixture +
   `pdf_sender_domains.txt` seed + `scripts/pdf_sender_domains.py`
   loader + its unit tests.** This commit. The data file +
   loader are tiny and naturally land with the design — the
   matcher delegates to `protected_senders.is_protected` so
   no new matching code lands.
2. **`gmail_client.py` PDF attachment fetch + 8MB cap + tests.**
   `read_message` grows a `pdfs: list[bytes]` field; tests pin
   the inline-vs-reference paths and the oversized skip.
3. **`agent.py` content-block plumbing + prompt directive +
   tests.** Switch to list-of-blocks when an email has PDFs,
   prepend document blocks before the text block, force
   batch-of-1 for PDF-bearing emails, extend prompt with the
   directive. Tests pin block shape, prompt phrases, batching.
4. **`main.py` sender gating + step2b/step3 wiring + tests.**
   Load `pdf_sender_domains.txt`, drop the `pdfs` list on
   non-school senders before agent extraction. Tests pin the
   gate from both directions.
5. **ROADMAP close-out: SHAs, leave `[~]` until Tom verifies
   live on a real cron tick that pulls a teacher PDF.**

## Live verification checklist (when feature is deployed)

- (a) Trigger a `workflow_dispatch test_output=true` run after
  the next teacher PDF email arrives; testpage shows the
  expected events from the PDF (e.g. the upcoming-dates block).
- (b) `events_state.json` (state branch) carries new events
  with `source_message_id` matching the teacher email's ID.
- (c) Costco-receipt-style personal-account PDFs do NOT
  produce events (sender gating worked).
- (d) Cost telemetry: a non-test cron run with at least one
  teacher PDF email shows the expected token bump in the
  workflow log (`extract_events` already logs per-batch token
  usage).

## Open for future work (explicit non-goals)

- **OCR for image-only / scanned PDFs.** Anthropic's PDF
  reader uses vision under the hood, so most "scanned" PDFs
  already work — but if a sender uploads an image of a paper
  newsletter into a PDF wrapper, results may degrade. Revisit
  only if a real teacher hits this case in practice.
- **Subject-keyword gating.** Sender-only gating is the
  starting point; if a teacher posts non-newsletter PDFs
  frequently (e.g. permission-slip blank forms), we can add
  a subject keyword filter as a separate gate. No need
  pre-emptively.
- **Multi-page page-range cost optimization.** All pages of
  a PDF are sent today; if a 20-page newsletter shows up
  with all the dates on page 1, we'd be paying for 19
  unrelated pages. The `pypdf` page-count + first-page
  preview is a possible optimization; not worth building
  pre-emptively.
- **Caching the PDF bytes themselves.** Cache (#4) keys on
  messageId; if the same message is re-extracted via
  `--reextract`, the PDF gets re-fetched from Gmail. Cheap
  enough that bytes-caching adds complexity without value.
