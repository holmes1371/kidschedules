"""Anthropic API agent for kids' event extraction.

This module handles the ONE judgment-heavy step in the pipeline:
reading email bodies and extracting structured event dicts.

Everything else (query building, filtering, deduping, rendering)
is handled by the deterministic Python scripts.
"""
from __future__ import annotations

import email.utils
import json
import os
import time
from pathlib import Path
from typing import Any

import anthropic


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is required.")
    return anthropic.Anthropic(api_key=api_key)


_ROSTER_PATH = Path(__file__).parent / "class_roster.json"


def _format_roster_prose(mapping: dict[str, dict]) -> str:
    """Format a roster mapping into a prose block for the extractor prompt.

    Pure function — no I/O. The formatter is kept separate from the loader
    so it can be unit-tested against a fabricated mapping without touching
    the filesystem. Sentence shape avoids gendered pronouns so adding a
    third kid later doesn't require rewording.

    Per-kid `activities` (list of strings) is optional. When present and
    non-empty, the names are appended as a clause on that kid's line and
    the tail attribution rule about activity providers applies. When
    absent or empty, the kid's line stops at the teacher and no extra
    routing pressure is added — useful for kids who have no committed
    activities yet and for unit-test fixtures.
    """
    lines = ["Teacher roster (current academic year):"]
    for name, info in mapping.items():
        base = (
            f"- {name} is in {info['grade']} grade at {info['school']}, "
            f"taught by {info['teacher']}"
        )
        activities = info.get("activities") or []
        if activities:
            lines.append(f"{base}; activities: {', '.join(activities)}.")
        else:
            lines.append(f"{base}.")
    lines.append("")
    lines.append(
        "If an email names a teacher without naming the kid, attribute "
        "events to that teacher's student. If an email names a grade "
        "level that matches a kid's grade, prefer that kid for the "
        "`child` field. If an email is from or mentions a listed "
        "activity provider for a kid, attribute those events to that kid."
    )
    return "\n".join(lines)


def _load_roster_prose(path: Path = _ROSTER_PATH) -> str:
    """Read the roster JSON and return the formatted prose block.

    Raises (FileNotFoundError / json.JSONDecodeError / KeyError) on any
    problem — the roster file is committed and its absence or corruption
    is a bug, not a condition to paper over with a silent fallback.
    """
    mapping = json.loads(path.read_text())
    return _format_roster_prose(mapping)


_EXTRACTION_BASE_PROMPT = """\
You are an assistant that extracts kids' events from email content for a
family with children at Louise Archer Elementary School (LAES) in Fairfax
County (FCPS). The family also has kids in swim team (HTM Sharks / Hunter
Mill), dance/ballet (Cuppett Performing Arts), and other extracurriculars.

You will receive a batch of email messages. Each message includes a
Message ID, sender, date sent, subject, and body text. Your job is to
identify ALL events, deadlines, and dates relevant to children and
return them as a JSON array.

WHAT TO EXTRACT — be thorough and extract ALL of these:

1. **School events**: field trips, concerts, assemblies, picture days,
   STEAM fairs, open houses, science fairs, art shows, book fairs,
   school carnivals, talent shows, graduation ceremonies

2. **No-school days and closures**: teacher workdays, holidays,
   election days, weather closures, early dismissals, delayed openings,
   half days — these are CRITICAL, do not miss them

3. **Spirit days and awareness days**: Purple Up day, spirit week themes,
   pajama day, crazy hair day, Red Ribbon Week, etc.

4. **Deadlines and due dates**: yearbook photo submissions, yearbook
   sales deadlines, permission slip due dates, field trip payment
   deadlines, form submissions, sign-up deadlines, t-shirt design
   contest deadlines, volunteer sign-ups, fundraiser deadlines

5. **Kids' appointments ONLY**: doctor, dentist, orthodontist, therapy,
   checkups — but ONLY if the appointment is clearly for a child (e.g.
   pediatric, mentions a child's name). Skip adult-only appointments
   such as a parent's personal doctor visit or house cleaning services.

6. **Sports and extracurriculars**: swim team practices/meets/deadlines,
   dance recitals/rehearsals/camps, soccer games/practices, Girls on
   the Run events, club meetings (ASL club, etc.), tryouts, tournaments

7. **PTA events**: International Night, PTA meetings, fundraiser events,
   community events hosted by the school

8. **Newsletter calendar items**: monthly calendars embedded in school
   newsletters often list 5-10+ dates — extract EVERY date from them

For each event, output a dict with exactly these keys:
- "name": string — descriptive name (e.g. "NO SCHOOL — Election Day",
  "Purple Up! Day", "Yearbook Photos Submission Deadline",
  "International Night Booth Sign-Up Deadline")
- "date": string — ISO YYYY-MM-DD format. If the email says "next
  Tuesday" or "this Friday", resolve it to an actual calendar date
  using the email's sent date. If truly unknown, use "".
- "time": string — "6:30 PM", "All day", "All day (deadline)",
  "School hours", etc. Use "" only if completely unknown.
- "location": string — school name, office, address, field, or URL for
  online submissions. Use "" if unknown.
  IF the email contains a URL the user would tap to attend or complete
  the event (signup form, waiver, livestream, RSVP, e-signature link,
  Google Form, PandaDoc/DocuSign link, etc.), include the URL VERBATIM
  in this field. Do NOT summarize as "see link", "(form link)",
  "(PandaDoc link)", "(Google Form)", or similar paraphrase — the
  literal URL is what the user clicks. Mix with venue text when both
  apply. Examples:
    GOOD: "Online (https://app.pandadoc.com/document/abc123)"
    BAD:  "Online (PandaDoc link)"
    GOOD: "https://docs.google.com/forms/d/e/.../viewform"
    BAD:  "Online (Google Form)"
    GOOD: "School cafeteria — sign in at https://myschoolbucks.com"
    BAD:  "School cafeteria (MySchoolBucks)"
- "category": string — exactly one of: "School Activity", "Appointment",
  "Academic Due Date", "Sports & Extracurriculars"
- "child": string — which child or grade if identifiable (e.g. "Isla",
  "6th grade AAP", "All LAES students"). Use "" if unknown.
- "source": string — brief label including sender name or newsletter
  title AND the email's sent date, e.g. "LAES PTA Sunbeam (Apr 6)"
  or "FCPS School Board Update (Apr 10)".
  IMPORTANT — "the email's sent date" means the date THIS specific
  email was sent: the value on the "Date sent:" line at the top of
  the email block in your input. NOT a date mentioned in the email
  body, even if the body references or rolls up an older newsletter.
  If today's email is a reminder that quotes "as I mentioned in my
  March 15 newsletter, Chess Camp Session 1 ends Jun 26", the source
  date is TODAY (this email's sent date), not "Mar 15". The user
  reads the source date as "when did this information arrive in my
  inbox" — getting it wrong makes today's reminder look like a
  weeks-old email and surfaces confusing "why is this old email just
  showing up?" questions.
    GOOD: "LAES PTA Sunbeam (Apr 26)"   [today's reminder email]
    BAD:  "LAES PTA Sunbeam (Mar 15)"   [date referenced inside today's email]
- "source_message_id": string — the exact Message ID of the email this
  event was drawn from. Copy the value verbatim from the "Message ID:"
  line at the top of that email block. If the event synthesizes details
  from multiple emails, pick the one that contained the dated details
  (or omit the event rather than guess). Format is a 16-character hex
  string; do not invent, truncate, or edit it.

KEY RULES:
- Extract EVERY date you find, even if it seems minor. It is much better
  to include too many events than to miss one.
- SKIP events that are clearly for adults only, NOT for children. Examples:
  a parent's personal doctor appointment, house cleaning, home repairs,
  auto service, adult social events. If in doubt whether a child is
  involved, include it — but a generic "Doctor Appointment — Ellen" or
  "House Cleaning Appointment" with no mention of kids should be excluded.
- No-school days and deadline dates are just as important as events.
- If a newsletter contains a calendar or list of upcoming dates, extract
  each one as a separate event dict.
- If the same event appears in multiple emails, emit it from each one —
  the downstream script handles deduplication.
- Do NOT filter out past events — the downstream script handles that.
- Do NOT skip items just because they seem administrative (yearbook
  deadlines, sign-up cutoffs, payment due dates). Parents need these.

IRRELEVANT SENDER FLAGGING (second deliverable):

In addition to extracting events, identify any sender in this batch
whose emails were entirely adult- or work-related and would NEVER
produce kids' events. This feeds an auto-blocklist so noisy senders
get filtered next run.

Rules:
- Only flag senders that produced ZERO kids' events in this batch.
- Only flag with confidence "high" if you are sure the sender is
  adult-only (work calendars, legal recruiting, personal finance,
  adult appointments, etc.).
- NEVER flag: schools (fcps.edu, any PTA/PTSA), extracurricular
  providers (Cuppett, HTM Sharks, jackrabbittech, teamsnap,
  signupgenius, myschoolbucks, lifetouch, school photo vendors),
  any medical/dental/therapy provider that could see a child, or
  the parents' personal email addresses (ellen.n.holmes@gmail.com,
  thomas.holmes1371@gmail.com). Parent emails are how self-notes
  about kids' activities arrive ("Everly volleyball / 8-9am May 4-8");
  one off-topic email from a parent (a tax note, a personal errand)
  is NOT grounds to flag — the gating layer rejects parent-address
  flags anyway, but keep them out of the suggestions list to keep
  the audit log clean.
- Use the exact sender address as it appears in the email header
  (e.g. "appointments@calendly.com"), NOT a bare domain.
- Echo the email's Message ID as "source_message_id" — the same field
  events carry. The blocklist gating layer uses this to require
  corroboration across distinct emails before promoting an address
  to the auto-blocklist; one flag without a source_message_id is
  malformed and gets dropped at the gate.
- When in doubt, omit. Leaving off a sender is always safe.

OUTPUT FORMAT — return a single JSON object:

{
  "events": [ ...event dicts... ],
  "irrelevant_senders": [
    {
      "from": "appointments@calendly.com",
      "source_message_id": "18f1a2b3c4d5e6f7",
      "reason": "adult work calendar confirmation",
      "confidence": "high"
    }
  ]
}

If no events: "events": []. If no irrelevant senders: "irrelevant_senders": [].
Output ONLY the JSON object, no other text.
"""


# Final prompt with roster prose appended. Module import will fail loudly if
# class_roster.json is missing or malformed — intentional; the file is
# committed and silent drift would degrade extractions invisibly.
EXTRACTION_SYSTEM_PROMPT = _EXTRACTION_BASE_PROMPT + "\n" + _load_roster_prose()


AUDIT_SYSTEM_PROMPT = """\
You are auditing a blocklist used to filter email senders out of a kids'
schedule pipeline. The family has children at Louise Archer Elementary
(LAES / FCPS), HTM Sharks swim, Cuppett Performing Arts dance, and other
kids' extracurriculars.

You will receive a batch of messages that the blocklist FILTERED OUT
(from/subject/date/snippet). Your job is to identify any false positives —
messages that were filtered but actually look like they might be about a
child's event, appointment, deadline, or activity.

Lean conservative: most filtered mail really is junk. Only flag a sender
for unblocking if the message has clear kids-event relevance (mentions a
school, team, child by name, or specific kid activity).

Return ONLY a JSON object (no markdown, no commentary) with this shape:

{
  "decisions": [
    {
      "subject": "...",
      "from": "...",
      "verdict": "keep_blocked" | "unblock",
      "reason": "short rationale"
    }
  ],
  "senders_to_unblock": ["domain-or-address", ...]
}

The senders_to_unblock list should contain every distinct sender that had
at least one "unblock" decision. Use the most specific form that would
match (e.g. "campaigns@doublegood.com" or "m.lifetouch.com"), not a
bare parent domain, to avoid collateral damage.
"""

# Maximum emails per API call. Smaller batches = better attention to
# detail per email, at the cost of more API calls.
BATCH_SIZE = 10


def _sender_key(from_header: str) -> str:
    """Return the lowercased mailbox portion of a `From` header.

    Used by `_plan_batches` to compare each email's sender against the
    newsletter set that `main.py` builds from `sender_stats.json`. The
    shape has to match the key `newsletter_stats.py` stores — lowercased
    mailbox, parsed via `email.utils.parseaddr` so
    `"Foo <foo@bar.com>"` collapses to `"foo@bar.com"`.

    Returns `""` when the header is missing or unparseable; callers
    treat empty-key as "not in the newsletter set" — the safe default
    (batched at `BATCH_SIZE`).
    """
    _, addr = email.utils.parseaddr(from_header or "")
    return addr.lower()


def _plan_batches(
    emails: list[dict[str, Any]],
    newsletter_senders: set[str] | None,
) -> list[list[dict[str, Any]]]:
    """Partition `emails` into per-API-call batches.

    Pure function — no I/O. Separated from `extract_events` so the
    batching logic can be unit-tested without mocking the Anthropic SDK.

    When `newsletter_senders` is `None` (default), behaves like the
    prior inline split: a single partition chunked at `BATCH_SIZE`. This
    preserves bit-for-bit behavior for callers that don't opt in.

    When provided, emails whose sender key (lowercased mailbox from the
    `From` header) is in the set go into batches of size 1; everyone
    else goes into chunks of `BATCH_SIZE`. Newsletter batches are
    ordered FIRST so a parse failure on a cheap regular batch does not
    gate the expensive newsletter work — if the run aborts midway, the
    high-yield messages are already extracted.
    """
    if newsletter_senders is None:
        return [
            emails[i : i + BATCH_SIZE]
            for i in range(0, len(emails), BATCH_SIZE)
        ]

    newsletter_emails: list[dict[str, Any]] = []
    regular_emails: list[dict[str, Any]] = []
    for e in emails:
        if _sender_key(e.get("from_", "")) in newsletter_senders:
            newsletter_emails.append(e)
        else:
            regular_emails.append(e)

    batches: list[list[dict[str, Any]]] = [[e] for e in newsletter_emails]
    batches.extend(
        regular_emails[i : i + BATCH_SIZE]
        for i in range(0, len(regular_emails), BATCH_SIZE)
    )
    return batches

# Retry config for transient API errors (overloaded, rate limits).
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds; doubles each retry


def _call_with_retry(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    user_message: str,
    batch_label: str,
    system_prompt: str | None = None,
) -> anthropic.types.Message:
    """Call the Anthropic API with exponential backoff on transient errors.

    system_prompt defaults to EXTRACTION_SYSTEM_PROMPT for back-compat with
    existing event-extraction callers. The blocklist audit passes its own.
    """
    if system_prompt is None:
        system_prompt = EXTRACTION_SYSTEM_PROMPT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except (
            anthropic.RateLimitError,       # 429
            anthropic.InternalServerError,  # 500
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        ) as e:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            if attempt == MAX_RETRIES:
                print(f"FAILED after {MAX_RETRIES} attempts: {e}")
                raise
            print(f"\n    {batch_label} attempt {attempt} failed "
                  f"({type(e).__name__}), retrying in {delay}s ...")
            time.sleep(delay)
        except anthropic.APIStatusError as e:
            # Retry on 529 (overloaded), but not on 400/401/403 etc.
            if e.status_code == 529:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                if attempt == MAX_RETRIES:
                    print(f"FAILED after {MAX_RETRIES} attempts: {e}")
                    raise
                print(f"\n    {batch_label} attempt {attempt} failed "
                      f"(overloaded 529), retrying in {delay}s ...")
                time.sleep(delay)
            else:
                raise
    # Unreachable, but keeps type checkers happy
    raise RuntimeError("Exhausted retries")


def _parse_json_response(text: str) -> dict[str, list] | None:
    """Parse the model's response into {'events': [...], 'irrelevant_senders': [...]}.

    Accepts either the current dict shape or a legacy bare-list shape
    (treated as events-only, no irrelevant senders). Returns None on
    unrecoverable parse failure.
    """
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        # Common failure mode: the model emits a valid JSON value followed by
        # extra content (trailing commentary, a second object, a half-written
        # continuation). raw_decode parses the first value and ignores the
        # rest — if that works, we log and move on instead of forcing a
        # repair round-trip.
        try:
            result, end = json.JSONDecoder().raw_decode(text)
            trailing = text[end:].strip()
            if trailing:
                print(
                    f"  WARNING: recovered via raw_decode; "
                    f"ignored {len(trailing)} trailing char(s)"
                )
        except json.JSONDecodeError:
            print(f"PARSE ERROR: {e}")
            print(f"  Raw response (first 500 chars):\n{text[:500]}")
            return None

    if isinstance(result, list):
        return {"events": result, "irrelevant_senders": []}

    if isinstance(result, dict):
        events = result.get("events", [])
        senders = result.get("irrelevant_senders", [])
        if not isinstance(events, list):
            print(f"WARNING: 'events' is not a list: {type(events)}")
            events = []
        if not isinstance(senders, list):
            print(f"WARNING: 'irrelevant_senders' is not a list: {type(senders)}")
            senders = []
        return {"events": events, "irrelevant_senders": senders}

    print(f"WARNING: response is not a dict or list: {type(result)}")
    return None


def _filter_events_by_source_id(
    events: list[dict[str, Any]],
    batch_message_ids: set[str],
) -> list[dict[str, Any]]:
    """Drop events whose source_message_id is missing or not in the batch.

    The agent is instructed to echo back the Message ID of the email each
    event came from. If the model omits the field or hallucinates an ID,
    we cannot map the event back to a sender — the Ignore-sender button
    would be wrong — so the event is dropped with a warning. Matches the
    tolerant-parse posture elsewhere in this module: warn, don't crash.
    """
    kept: list[dict[str, Any]] = []
    dropped_missing = 0
    dropped_unknown = 0
    for event in events:
        sid = event.get("source_message_id", "")
        if not isinstance(sid, str) or not sid:
            dropped_missing += 1
            continue
        if sid not in batch_message_ids:
            dropped_unknown += 1
            continue
        kept.append(event)
    if dropped_missing:
        print(
            f"  WARNING: dropped {dropped_missing} event(s) with missing "
            f"source_message_id"
        )
    if dropped_unknown:
        print(
            f"  WARNING: dropped {dropped_unknown} event(s) whose "
            f"source_message_id did not match any email in the batch"
        )
    return kept


def extract_events(
    emails: list[dict[str, Any]],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 16384,
    newsletter_senders: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Send email content to Claude in batches and collect event dicts.

    Args:
        emails: list of dicts, each with keys: messageId, subject, from_,
                date_sent, body (all strings).
        model: Anthropic model to use. Sonnet is cost-effective for this.
        max_tokens: max response tokens per batch.
        newsletter_senders: optional set of lowercased sender mailboxes
            (as produced by `_sender_key`) that should each run in a
            batch of size 1 instead of sharing a batch with `BATCH_SIZE`
            other emails. Newsletter batches run before regular ones.
            `None` (default) preserves the prior behavior of batching
            all emails at `BATCH_SIZE`.

    Returns:
        (events, irrelevant_senders) — events are ready for
        process_events.py; irrelevant_senders feeds update_auto_blocklist.py.
    """
    if not emails:
        return [], []

    batches = _plan_batches(emails, newsletter_senders)
    print(f"  Splitting {len(emails)} emails into {len(batches)} batch(es) "
          f"of up to {BATCH_SIZE}")

    all_events: list[dict[str, Any]] = []
    all_irrelevant: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    client = _get_client()

    for batch_num, batch in enumerate(batches, 1):
        batch_label = f"Batch {batch_num}/{len(batches)}"
        print(f"  {batch_label}: "
              f"{len(batch)} emails ...", end=" ", flush=True)

        # Build the user message for this batch. The Message ID line is
        # what the model echoes back in each event's source_message_id,
        # so downstream Python can map events → original senders.
        parts = []
        for i, email in enumerate(batch, 1):
            parts.append(
                f"--- EMAIL {i} ---\n"
                f"Message ID: {email.get('messageId', '')}\n"
                f"From: {email.get('from_', '')}\n"
                f"Date sent: {email.get('date_sent', '')}\n"
                f"Subject: {email.get('subject', '')}\n"
                f"Body:\n{email.get('body', '')}\n"
            )
        user_message = "\n".join(parts)
        batch_message_ids = {
            email.get("messageId", "") for email in batch
        }
        batch_message_ids.discard("")

        # No try/except here: _call_with_retry already absorbs transient
        # errors (429/500/503/529/connection/timeout) with backoff. Anything
        # past that is a real failure (auth, persistent 5xx, unexpected
        # status) and must fail the pipeline so the GitHub Actions run
        # surfaces the push notification.
        response = _call_with_retry(
            client, model, max_tokens, user_message, batch_label
        )

        # Parse the response
        text = response.content[0].text.strip()
        parsed = _parse_json_response(text)

        # If parse failed, retry once asking the model to fix it. We re-send
        # the full email batch alongside the broken response — without it the
        # model has no context and tends to reply "please share the emails",
        # wasting the repair round-trip.
        if parsed is None:
            print(f"  Retrying {batch_label} with JSON repair prompt...")
            try:
                repair_response = _call_with_retry(
                    client, model, max_tokens,
                    f"Your previous response to the email batch below was not "
                    f"valid JSON. Here is what you returned:\n\n"
                    f"{text[:3000]}\n\n"
                    f"And here are the original emails you were extracting "
                    f"from:\n\n"
                    f"{user_message}\n\n"
                    f"Please return ONLY a valid JSON object with keys "
                    f"'events' and 'irrelevant_senders'. No markdown, no "
                    f"explanation, nothing after the closing brace.",
                    f"{batch_label} (repair)",
                )
                text2 = repair_response.content[0].text.strip()
                parsed = _parse_json_response(text2)
                if parsed is not None:
                    usage2 = repair_response.usage
                    total_input_tokens += usage2.input_tokens
                    total_output_tokens += usage2.output_tokens
                    print(f"  Repair succeeded!")
            except Exception as e2:
                print(f"  Repair also failed: {e2}")

        if parsed is None:
            print(f"  SKIPPING {batch_label}: could not parse JSON")
            continue

        events = parsed["events"]
        irrelevant = parsed["irrelevant_senders"]
        events = _filter_events_by_source_id(events, batch_message_ids)
        usage = response.usage
        total_input_tokens += usage.input_tokens
        total_output_tokens += usage.output_tokens
        print(f"{len(events)} events, {len(irrelevant)} sender(s) flagged "
              f"({usage.input_tokens} in / {usage.output_tokens} out)")

        all_events.extend(events)
        all_irrelevant.extend(irrelevant)

    print(f"  Total token usage: {total_input_tokens} input, "
          f"{total_output_tokens} output")

    return all_events, all_irrelevant


def review_stripped_messages(
    diff_report: dict[str, Any],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Review messages stripped by the blocklist filter.

    Args:
        diff_report: parsed JSON from diff_search_results.py
        model: Anthropic model to use.
        max_tokens: max response tokens.

    Returns:
        dict with "decisions" and "senders_to_unblock" lists.
    """
    # Collect all stripped messages across categories
    stripped = []
    for cat, data in diff_report.get("categories", {}).items():
        for msg in data.get("stripped_messages", []):
            stripped.append({**msg, "category": cat})

    if not stripped:
        print("  No stripped messages to review.")
        return {"decisions": [], "senders_to_unblock": []}

    print(f"  Reviewing {len(stripped)} stripped messages ...")

    # Build the user message
    parts = []
    for i, msg in enumerate(stripped, 1):
        parts.append(
            f"--- STRIPPED MESSAGE {i} (category: {msg['category']}) ---\n"
            f"From: {msg.get('from', '')}\n"
            f"Subject: {msg.get('subject', '')}\n"
            f"Date: {msg.get('date', '')}\n"
            f"Snippet: {msg.get('snippet', '')}\n"
        )
    user_message = "\n".join(parts)

    client = _get_client()
    try:
        response = _call_with_retry(
            client, model, max_tokens, user_message, "Audit review",
            system_prompt=AUDIT_SYSTEM_PROMPT,
        )
    except Exception as e:
        print(f"  Audit review failed: {e}")
        return {"decisions": [], "senders_to_unblock": []}

    text = response.content[0].text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  Audit parse error: {e}")
        return {"decisions": [], "senders_to_unblock": []}

    usage = response.usage
    print(
        f"  Audit review tokens: {usage.input_tokens} in / "
        f"{usage.output_tokens} out"
    )

    # Belt-and-suspenders: if the model ignores the schema and returns
    # something other than a dict, treat it as no recommendations rather
    # than crashing the pipeline.
    if not isinstance(result, dict):
        print(
            f"  Audit returned unexpected shape ({type(result).__name__}); "
            f"treating as no recommendations."
        )
        return {"decisions": [], "senders_to_unblock": []}

    result.setdefault("decisions", [])
    result.setdefault("senders_to_unblock", [])
    return result