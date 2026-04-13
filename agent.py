"""Anthropic API agent for kids' event extraction.

This module handles the ONE judgment-heavy step in the pipeline:
reading email bodies and extracting structured event dicts.

Everything else (query building, filtering, deduping, rendering)
is handled by the deterministic Python scripts.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import anthropic


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is required.")
    return anthropic.Anthropic(api_key=api_key)


EXTRACTION_SYSTEM_PROMPT = """\
You are an assistant that extracts kids' events from email content for a
family with children at Louise Archer Elementary School (LAES) in Fairfax
County (FCPS). The family also has kids in swim team (HTM Sharks / Hunter
Mill), dance/ballet (Cuppett Performing Arts), and other extracurriculars.

You will receive a batch of email messages (subject, sender, date sent,
and body text). Your job is to identify ALL events, deadlines, and dates
relevant to children and return them as a JSON array.

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

5. **Appointments**: doctor, dentist, orthodontist, therapy, checkups

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
- "location": string — school name, office, address, field, URL for
  online submissions. Use "" if unknown.
- "category": string — exactly one of: "School Activity", "Appointment",
  "Academic Due Date", "Sports & Extracurriculars"
- "child": string — which child or grade if identifiable (e.g. "Isla",
  "6th grade AAP", "All LAES students"). Use "" if unknown.
- "source": string — brief label including sender name or newsletter
  title AND the email's sent date, e.g. "LAES PTA Sunbeam (Apr 6)"
  or "FCPS School Board Update (Apr 10)"

KEY RULES:
- Extract EVERY date you find, even if it seems minor. It is much better
  to include too many events than to miss one.
- No-school days and deadline dates are just as important as events.
- If a newsletter contains a calendar or list of upcoming dates, extract
  each one as a separate event dict.
- If the same event appears in multiple emails, emit it from each one —
  the downstream script handles deduplication.
- Do NOT filter out past events — the downstream script handles that.
- Do NOT skip items just because they seem administrative (yearbook
  deadlines, sign-up cutoffs, payment due dates). Parents need these.
- Output ONLY the JSON array, no other text. If no events found: []
"""


# Maximum emails per API call. Smaller batches = better attention to
# detail per email, at the cost of more API calls.
BATCH_SIZE = 15

# Retry config for transient API errors (overloaded, rate limits).
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds; doubles each retry


def _call_with_retry(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    user_message: str,
    batch_label: str,
) -> anthropic.types.Message:
    """Call the Anthropic API with exponential backoff on transient errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except (
            anthropic.OverloadedError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        ) as e:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            if attempt == MAX_RETRIES:
                print(f"FAILED after {MAX_RETRIES} attempts: {e}")
                raise
            print(f"\n    {batch_label} attempt {attempt} failed "
                  f"({type(e).__name__}), retrying in {delay}s ...")
            time.sleep(delay)
    # Unreachable, but keeps type checkers happy
    raise RuntimeError("Exhausted retries")


def extract_events(
    emails: list[dict[str, Any]],
    model: str = "claude-sonnet-4-6-20250415",
    max_tokens: int = 8192,
) -> list[dict[str, Any]]:
    """Send email content to Claude in batches and collect event dicts.

    Args:
        emails: list of dicts, each with keys: messageId, subject, from_,
                date_sent, body (all strings).
        model: Anthropic model to use. Sonnet is cost-effective for this.
        max_tokens: max response tokens per batch.

    Returns:
        list of event dicts ready for process_events.py
    """
    if not emails:
        return []

    # Split into batches
    batches = [
        emails[i : i + BATCH_SIZE]
        for i in range(0, len(emails), BATCH_SIZE)
    ]
    print(f"  Splitting {len(emails)} emails into {len(batches)} batch(es) "
          f"of up to {BATCH_SIZE}")

    all_events: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    client = _get_client()

    for batch_num, batch in enumerate(batches, 1):
        batch_label = f"Batch {batch_num}/{len(batches)}"
        print(f"  {batch_label}: "
              f"{len(batch)} emails ...", end=" ", flush=True)

        # Build the user message for this batch
        parts = []
        for i, email in enumerate(batch, 1):
            parts.append(
                f"--- EMAIL {i} ---\n"
                f"From: {email.get('from_', '')}\n"
                f"Date sent: {email.get('date_sent', '')}\n"
                f"Subject: {email.get('subject', '')}\n"
                f"Body:\n{email.get('body', '')}\n"
            )
        user_message = "\n".join(parts)

        try:
            response = _call_with_retry(
                client, model, max_tokens, user_message, batch_label
            )
        except Exception as e:
            print(f"SKIPPING {batch_label}: {e}")
            continue

        # Parse the response
        text = response.content[0].text.strip()

        # Handle ```json ... ``` wrapping
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]).strip()

        try:
            events = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"PARSE ERROR: {e}")
            print(f"  Raw response:\n{text[:500]}")
            continue

        if isinstance(events, dict) and "events" in events:
            events = events["events"]

        if not isinstance(events, list):
            print(f"WARNING: response is not a list: {type(events)}")
            continue

        usage = response.usage
        total_input_tokens += usage.input_tokens
        total_output_tokens += usage.output_tokens
        print(f"{len(events)} events extracted "
              f"({usage.input_tokens} in / {usage.output_tokens} out)")

        all_events.extend(events)

    print(f"  Total token usage: {total_input_tokens} input, "
          f"{total_output_tokens} output")

    return all_events
