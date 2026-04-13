"""Anthropic API agent for kids' event extraction.

This module handles the ONE judgment-heavy step in the pipeline:
reading email bodies and extracting structured event dicts.

Everything else (query building, filtering, deduping, rendering)
is handled by the deterministic Python scripts.
"""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is required.")
    return anthropic.Anthropic(api_key=api_key)


EXTRACTION_SYSTEM_PROMPT = """\
You are an assistant that extracts kids' events from email content.

You will receive a batch of email messages (subject, sender, date sent,
and body text). Your job is to identify any events related to children's
activities and return them as a JSON array.

For each event, output a dict with exactly these keys:
- "name": string — what the event is (e.g. "Spring Concert", "Dental Appointment")
- "date": string — ISO YYYY-MM-DD format. If the email says "next Tuesday" or
  "this Friday", resolve it to an actual date using the email's sent date.
  If the date truly cannot be determined, use "".
- "time": string — start time like "6:30 PM" or "All day". Use "" if unknown.
- "location": string — school name, address, field name, etc. Use "" if unknown.
- "category": string — exactly one of: "School Activity", "Appointment",
  "Academic Due Date", "Sports & Extracurriculars"
- "child": string — which child if identifiable. Use "" if unknown.
- "source": string — brief label like "Louise Archer newsletter" or
  "Dr. Smith appointment confirmation"

Rules:
- If a single email contains a multi-week schedule (e.g. monthly sports
  schedule, semester calendar), emit one dict per event.
- Do NOT filter out past events — the downstream script handles that.
- Do NOT deduplicate — the downstream script handles that.
- Be inclusive: if something might be a kids' event, include it.
- Do NOT include marketing, promotions, or adult-only events.
- Output ONLY the JSON array, no other text. If no events are found,
  output an empty array: []
"""


def extract_events(
    emails: list[dict[str, Any]],
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
) -> list[dict[str, Any]]:
    """Send email content to Claude and get back structured event dicts.

    Args:
        emails: list of dicts, each with keys: messageId, subject, from_,
                date_sent, body (all strings).
        model: Anthropic model to use. Sonnet is cost-effective for this.
        max_tokens: max response tokens.

    Returns:
        list of event dicts ready for process_events.py
    """
    if not emails:
        return []

    # Build the user message with all emails
    parts = []
    for i, email in enumerate(emails, 1):
        parts.append(
            f"--- EMAIL {i} ---\n"
            f"From: {email.get('from_', '')}\n"
            f"Date sent: {email.get('date_sent', '')}\n"
            f"Subject: {email.get('subject', '')}\n"
            f"Body:\n{email.get('body', '')}\n"
        )

    user_message = "\n".join(parts)

    client = _get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # Parse the response — Claude should return a JSON array
    text = response.content[0].text.strip()

    # Handle case where Claude wraps in ```json ... ```
    if text.startswith("```"):
        lines = text.split("\n")
        # Strip first and last lines (the ``` markers)
        text = "\n".join(lines[1:-1]).strip()

    try:
        events = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"WARNING: Failed to parse agent response as JSON: {e}")
        print(f"Raw response:\n{text[:500]}")
        return []

    if isinstance(events, dict) and "events" in events:
        events = events["events"]

    if not isinstance(events, list):
        print(f"WARNING: Agent response is not a list: {type(events)}")
        return []

    # Report token usage
    usage = response.usage
    print(
        f"Agent token usage: {usage.input_tokens} input, "
        f"{usage.output_tokens} output"
    )

    return events
