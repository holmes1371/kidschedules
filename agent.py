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
- Output ONLY the JSON array, no other text. If no events found: []
"""


AUDIT_SYSTEM_PROMPT = """\
You are reviewing emails that a blocklist filter removed from a kids'
schedule search. Your job is to decide whether any of these stripped
messages contain legitimate kids' events that the filter incorrectly hid.

You will receive a list of stripped messages with their sender, subject,
date, and snippet. For each one, classify it as either:
- "keep_filtered": correctly blocked (marketing, news, adult content, etc.)
- "unblock": this looks like it could contain a real kids' event and
  the sender should be removed from the blocklist

Output a JSON object:
{
  "decisions": [
    {"messageId": "...", "subject": "...", "from": "...", "verdict": "keep_filtered" or "unblock", "reason": "brief explanation"}
  ],
  "senders_to_unblock": ["domain1.com", "addr@domain2.com"]
}

The "senders_to_unblock" list should contain the sender domains or
addresses that should be REMOVED from the blocklist because they send
legitimate kids' content. Only include senders where you are reasonably
confident the filter is hiding real events.

NEVER recommend unblocking these (they are known kids-event senders
already in the safe list): fcps.edu, any *pta.org, jackrabbittech.com,
teamsnap.com, signupgenius.com, myschoolbucks.com, lifetouch.com,
or real medical provider domains.

Output ONLY the JSON object, no other text.
"""


# Maximum emails per API call. Smaller batches = better attention to
# detail per email, at the cost of more API calls.
BATCH_SIZE = 10

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


def _parse_json_response(text: str) -> list[dict] | None:
    """Try to parse a JSON array from the model's response text.

    Handles ```json wrapping and returns None on failure.
    """
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"PARSE ERROR: {e}")
        print(f"  Raw response (first 500 chars):\n{text[:500]}")
        return None

    if isinstance(result, dict) and "events" in result:
        result = result["events"]

    if not isinstance(result, list):
        print(f"WARNING: response is not a list: {type(result)}")
        return None

    return result


def extract_events(
    emails: list[dict[str, Any]],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 16384,
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
        events = _parse_json_response(text)

        # If parse failed, retry once asking the model to fix it
        if events is None:
            print(f"  Retrying {batch_label} with JSON repair prompt...")
            try:
                repair_response = _call_with_retry(
                    client, model, max_tokens,
                    f"Your previous response was not valid JSON. Here is what you returned:\n\n"
                    f"{text[:3000]}\n\n"
                    f"Please return ONLY a valid JSON array of event dicts. No markdown, no explanation.",
                    f"{batch_label} (repair)",
                )
                text2 = repair_response.content[0].text.strip()
                events = _parse_json_response(text2)
                if events is not None:
                    usage2 = repair_response.usage
                    total_input_tokens += usage2.input_tokens
                    total_output_tokens += usage2.output_tokens
                    print(f"  Repair succeeded!")
            except Exception as e2:
                print(f"  Repair also failed: {e2}")

        if events is None:
            print(f"  SKIPPING {batch_label}: could not parse JSON")
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
            client, model, max_tokens, user_message, "Audit review"
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

    # Defensive: the audit currently reuses EXTRACTION_SYSTEM_PROMPT, so the
    # model may reply with a list instead of the expected dict. Coerce to the
    # empty-recommendations shape rather than crashing the pipeline.
    if not isinstance(result, dict):
        print(
            f"  Audit returned unexpected shape ({type(result).__name__}); "
            f"treating as no recommendations."
        )
        return {"decisions": [], "senders_to_unblock": []}

    result.setdefault("decisions", [])
    result.setdefault("senders_to_unblock", [])
    return result