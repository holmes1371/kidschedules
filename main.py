#!/usr/bin/env python3
"""Kids Schedule — GitHub Actions orchestrator.

Runs the full pipeline:
  1. build_queries.py → date windows + Gmail query strings
  2. Gmail API searches → message stubs
  3. Gmail API reads → full email bodies for promising messages
  4. Anthropic agent → candidate event dicts (judgment step)
  5. process_events.py → rendered HTML page + metadata
  6. Write docs/index.html → workflow uploads docs/ as a Pages artifact

Usage:
  python main.py                    # normal run
  python main.py --dry-run          # skip publishing
  python main.py --lookback-days 90 # wider search window
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from gmail_client import GmailClient
from agent import extract_events, review_stripped_messages


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "docs")
FUTURE_EVENTS_PATH = os.path.join(PROJECT_ROOT, "future_events.json")
LAST_RUN_FIXTURE_PATH = os.path.join(
    PROJECT_ROOT, "fixtures", "last_run_candidates.json"
)
WEBHOOK_URL_PATH = os.path.join(PROJECT_ROOT, "ignore_webhook_url.txt")
PAGES_URL_PATH = os.path.join(PROJECT_ROOT, "pages_url.txt")
IGNORED_EVENTS_PATH = os.path.join(PROJECT_ROOT, "ignored_events.json")
BLOCKLIST_PATH = os.path.join(PROJECT_ROOT, "blocklist.txt")
AUTO_BLOCKLIST_PATH = os.path.join(PROJECT_ROOT, "blocklist_auto.txt")
AUTO_BLOCKLIST_AUDIT_PATH = os.path.join(
    PROJECT_ROOT, "blocklist_auto_audit.jsonl"
)


def _load_webhook_url() -> str:
    """Return the Apps Script webhook URL committed to the repo, or ''."""
    if not os.path.exists(WEBHOOK_URL_PATH):
        return ""
    try:
        with open(WEBHOOK_URL_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _load_pages_url() -> str:
    """Return the GitHub Pages URL committed to the repo, or ''."""
    if not os.path.exists(PAGES_URL_PATH):
        return ""
    try:
        with open(PAGES_URL_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def should_create_draft(args) -> bool:
    """The single decision gate for creating a Gmail digest draft.

    Default is no-draft. Explicit opt-in required via --create-draft or
    the CREATE_DRAFT=1 env var (used by the scheduled workflow trigger).
    --dry-run always suppresses. This is the only place the decision lives.
    """
    if args.dry_run:
        return False
    if args.create_draft:
        return True
    if os.environ.get("CREATE_DRAFT") == "1":
        return True
    return False


def run_script(script_name: str, args: list[str] | None = None) -> str:
    """Run a script from the scripts/ directory and return its stdout."""
    script_path = os.path.join(PROJECT_ROOT, "scripts", script_name)
    cmd = [sys.executable, script_path] + (args or [])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    if result.stderr:
        print(f"[{script_name} stderr]: {result.stderr.strip()}")
    return result.stdout


def step1_build_queries(lookback_days: int) -> dict[str, Any]:
    """Run build_queries.py and return the parsed JSON config."""
    print("=" * 60)
    print("STEP 1: Building queries and date windows")
    print("=" * 60)
    args = ["--lookback-days", str(lookback_days)]
    output = run_script("build_queries.py", args)
    config = json.loads(output)
    print(f"  Today: {config['today_human']}")
    print(f"  Email window: {config['email_window']['after']} → "
          f"{config['email_window']['before']}")
    excl = config["exclusions"]
    print(
        f"  Blocklist: {excl['blocklist_size']} senders excluded "
        f"({excl['blocklist_size_main']} hand-curated + "
        f"{excl['blocklist_size_auto']} auto)"
    )
    print(f"  Filter audit: {config['filter_audit']['reason']}")
    return config


def step1b_filter_audit(
    gmail: GmailClient,
    config: dict[str, Any],
    model: str,
    lookback_days: int,
) -> dict[str, Any]:
    """Run the filter audit if due. Returns (possibly updated) config."""
    audit = config["filter_audit"]
    if not audit["due"]:
        print("\n  Filter audit: not due, skipping.")
        return config

    print("\n" + "=" * 60)
    print("STEP 1b: Filter audit (blocklist health check)")
    print("=" * 60)
    print(f"  Reason: {audit['reason']}")

    queries = config["queries"]
    loose_queries = config["loose_queries"]
    max_results = config["max_results_per_query"]

    # Run tight (filtered) searches
    print("  Running tight (filtered) searches ...")
    tight_results: dict[str, list] = {}
    for name, query in queries.items():
        tight_results[name] = gmail.search_messages(query, max_results=max_results)

    # Run loose (unfiltered) searches
    print("  Running loose (unfiltered) searches ...")
    loose_results: dict[str, list] = {}
    for name, query in loose_queries.items():
        loose_results[name] = gmail.search_messages(query, max_results=max_results)

    # Write to temp files and run diff script
    tight_path = os.path.join(tempfile.gettempdir(), "kids-audit-tight.json")
    loose_path = os.path.join(tempfile.gettempdir(), "kids-audit-loose.json")
    diff_path = os.path.join(tempfile.gettempdir(), "kids-audit-diff.json")

    with open(tight_path, "w", encoding="utf-8") as f:
        json.dump(tight_results, f, ensure_ascii=False)
    with open(loose_path, "w", encoding="utf-8") as f:
        json.dump(loose_results, f, ensure_ascii=False)

    run_script("diff_search_results.py", [
        "--loose", loose_path,
        "--tight", tight_path,
        "--out", diff_path,
    ])

    with open(diff_path, "r") as f:
        diff_report = json.load(f)

    stripped_total = diff_report["totals"]["stripped"]
    print(f"  Filter stripped {stripped_total} messages "
          f"(loose: {diff_report['totals']['loose']}, "
          f"tight: {diff_report['totals']['tight']})")

    if stripped_total == 0:
        print("  No messages stripped — blocklist is clean.")
    else:
        # Agent reviews the stripped messages
        audit_result = review_stripped_messages(diff_report, model=model)
        unblock = audit_result.get("senders_to_unblock", [])

        if unblock:
            # Print recommendations only — do NOT modify blocklist.txt
            # (code changes should come from humans, not the bot)
            print("  ⚠️ Filter audit recommends removing these senders from blocklist:")
            for sender in unblock:
                print(f"    - {sender}")
            print("  (no changes made — edit blocklist.txt manually to apply)")
        else:
            print("  Audit reviewed stripped messages — no false positives.")

    # Stamp the audit regardless
    run_script("mark_filter_audit.py", [])
    print("  Filter audit complete and stamped.")

    # Clean up temp files
    for p in [tight_path, loose_path, diff_path]:
        try:
            os.unlink(p)
        except OSError:
            pass

    return config


def step2_search_gmail(
    gmail: GmailClient, config: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Run all 5 Gmail searches and return results keyed by category."""
    print("\n" + "=" * 60)
    print("STEP 2: Searching Gmail (5 queries)")
    print("=" * 60)
    queries = config["queries"]
    max_results = config["max_results_per_query"]
    all_results: dict[str, list[dict[str, Any]]] = {}

    for name, query in queries.items():
        print(f"  Searching: {name} ...", end=" ", flush=True)
        results = gmail.search_messages(query, max_results=max_results)
        all_results[name] = results
        print(f"{len(results)} messages")

    total = sum(len(v) for v in all_results.values())
    print(f"  Total messages across all searches: {total}")
    return all_results


def step2b_read_promising(
    gmail: GmailClient,
    search_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Read full bodies for all unique messages across searches."""
    print("\n" + "=" * 60)
    print("STEP 2b: Reading full message bodies")
    print("=" * 60)

    seen_ids: set[str] = set()
    emails_to_read: list[dict[str, Any]] = []

    for category, messages in search_results.items():
        for msg in messages:
            mid = msg["messageId"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            emails_to_read.append(msg)

    print(f"  Unique messages to read: {len(emails_to_read)}")

    full_emails: list[dict[str, Any]] = []
    for i, msg in enumerate(emails_to_read, 1):
        subject = msg.get("headers", {}).get("Subject", "(no subject)")
        print(f"  [{i}/{len(emails_to_read)}] {subject[:70]}")
        try:
            full = gmail.read_message(msg["messageId"])
            full_emails.append({
                "messageId": full["messageId"],
                "from_": full["headers"].get("From", ""),
                "date_sent": full["headers"].get("Date", ""),
                "subject": full["headers"].get("Subject", ""),
                "body": full["body"][:10000],
            })
        except Exception as e:
            print(f"    WARNING: Failed to read message {msg['messageId']}: {e}")

    print(f"  Successfully read: {len(full_emails)} messages")
    return full_emails


def step3_extract_events(
    full_emails: list[dict[str, Any]],
    model: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Send emails to the Anthropic agent for event extraction."""
    print("\n" + "=" * 60)
    print("STEP 3: Agent event extraction (Anthropic API)")
    print("=" * 60)
    print(f"  Model: {model}")
    print(f"  Emails to process: {len(full_emails)}")

    events, irrelevant_senders = extract_events(full_emails, model=model)
    print(f"  Candidate events extracted: {len(events)}")
    print(f"  Irrelevant sender suggestions: {len(irrelevant_senders)}")
    return events, irrelevant_senders


def step3b_update_auto_blocklist(
    irrelevant_senders: list[dict[str, Any]],
) -> None:
    """Merge agent-flagged senders into blocklist_auto.txt with guardrails.

    Always runs (even when the agent flagged zero senders) so the audit log
    records one line per pipeline run — empty suggestion lists are valuable
    signal too.
    """
    print("\n" + "=" * 60)
    if irrelevant_senders:
        print("STEP 3b: Auto-blocklist update")
    else:
        print("STEP 3b: Auto-blocklist update — no suggestions this run")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(irrelevant_senders, f, ensure_ascii=False)
        suggestions_path = f.name
    try:
        run_script(
            "update_auto_blocklist.py",
            [
                "--suggestions", suggestions_path,
                "--auto-blocklist", AUTO_BLOCKLIST_PATH,
                "--main-blocklist", BLOCKLIST_PATH,
                "--audit-log", AUTO_BLOCKLIST_AUDIT_PATH,
            ],
        )
    finally:
        try:
            os.unlink(suggestions_path)
        except OSError:
            pass


def _load_event_bank() -> list[dict[str, Any]]:
    """Load the persistent far-future event bank."""
    if not os.path.exists(FUTURE_EVENTS_PATH):
        return []
    try:
        with open(FUTURE_EVENTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_event_bank(events: list[dict[str, Any]]) -> None:
    """Save far-future events to the persistent bank."""
    with open(FUTURE_EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(events)} event(s) to future_events.json")


def step4_process_events(
    candidates: list[dict[str, Any]],
    pages_url: str = "",
) -> tuple[str, str, dict, str, str]:
    """Run process_events.py and return (html, body_text, meta,
    digest_text, digest_html).

    Merges in any previously banked far-future events, then saves
    newly banked events back to future_events.json.
    """
    print("\n" + "=" * 60)
    print("STEP 4: Processing events (filter, dedupe, sort, render)")
    print("=" * 60)

    # Load previously banked far-future events and merge them in
    banked_prev = _load_event_bank()
    if banked_prev:
        print(f"  Loaded {len(banked_prev)} event(s) from future_events.json")
    all_candidates = candidates + banked_prev

    # Dump the exact input to process_events.py so dev_render.py can replay
    # it against the real data without hitting any APIs.
    try:
        os.makedirs(os.path.dirname(LAST_RUN_FIXTURE_PATH), exist_ok=True)
        with open(LAST_RUN_FIXTURE_PATH, "w", encoding="utf-8") as f:
            json.dump(all_candidates, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"  WARNING: could not write last_run fixture: {e}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(all_candidates, f, ensure_ascii=False)
        candidates_path = f.name

    body_path = candidates_path.replace(".json", "-body.txt")
    html_path = candidates_path.replace(".json", "-page.html")
    meta_path = candidates_path.replace(".json", "-meta.json")
    banked_path = candidates_path.replace(".json", "-banked.json")
    digest_text_path = candidates_path.replace(".json", "-digest.txt")
    digest_html_path = candidates_path.replace(".json", "-digest.html")

    try:
        webhook_url = _load_webhook_url()
        run_script(
            "process_events.py",
            [
                "--candidates", candidates_path,
                "--body-out", body_path,
                "--html-out", html_path,
                "--meta-out", meta_path,
                "--banked-out", banked_path,
                "--digest-text-out", digest_text_path,
                "--digest-html-out", digest_html_path,
                "--pages-url", pages_url,
                "--display-window-days", "60",
                "--webhook-url", webhook_url,
                "--ignored", IGNORED_EVENTS_PATH,
            ],
        )

        with open(body_path, "r", encoding="utf-8") as f:
            body = f.read()
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        with open(digest_text_path, "r", encoding="utf-8") as f:
            digest_text = f.read()
        with open(digest_html_path, "r", encoding="utf-8") as f:
            digest_html = f.read()

        # Save newly banked far-future events
        banked_new = []
        if os.path.exists(banked_path):
            with open(banked_path, "r", encoding="utf-8") as f:
                banked_new = json.load(f)
        _save_event_bank(banked_new)

        counts = meta["counts"]
        print(f"  Candidates in: {counts['candidates_in']}")
        print(f"  Displayed (next 60 days): {counts['future_dated']}")
        print(f"  Banked (beyond 60 days): {counts.get('banked_far_future', 0)}")
        print(f"  Undated: {counts['undated']}")
        print(f"  Dropped (past): {counts['dropped_past']}")
        print(f"  Dropped (ignored): {counts.get('dropped_ignored', 0)}")
        if meta["warnings"]:
            for w in meta["warnings"]:
                print(f"  WARNING: {w}")

        return html, body, meta, digest_text, digest_html
    finally:
        for p in [candidates_path, body_path, html_path, meta_path,
                  banked_path, digest_text_path, digest_html_path]:
            try:
                os.unlink(p)
            except OSError:
                pass


def step5_publish(html: str, meta: dict, dry_run: bool) -> None:
    """Write index.html to docs/ for the workflow to upload as a Pages artifact."""
    print("\n" + "=" * 60)
    print("STEP 5: Publishing to GitHub Pages")
    print("=" * 60)

    if dry_run:
        print("  DRY RUN — skipping publish")
        print("  Would write docs/index.html")
        return

    os.makedirs(PAGES_OUTPUT_DIR, exist_ok=True)

    index_path = os.path.join(PAGES_OUTPUT_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Wrote docs/index.html ({len(html)} chars)")

    # .nojekyll so GitHub Pages serves raw HTML (harmless with Actions-based
    # deploy, preserved in case the deploy source is ever switched back).
    nojekyll = os.path.join(PAGES_OUTPUT_DIR, ".nojekyll")
    if not os.path.exists(nojekyll):
        with open(nojekyll, "w") as f:
            pass
        print("  Created docs/.nojekyll")

    print(f"  Subject: {meta['subject']}")


def step6_create_draft(
    gmail: GmailClient,
    meta: dict,
    digest_text: str,
    digest_html: str,
    actually_create: bool,
) -> None:
    """Preview the weekly digest and (if gated on) create a Gmail draft.

    Always logs a plain-text preview so local/manual runs can eyeball the
    draft content without touching Gmail. If the gate is off, we stop
    after the preview. Empty-week (this_week_count == 0) also short-
    circuits — a "nothing this week" draft is spam by another name.
    """
    print("\n" + "=" * 60)
    print("STEP 6: Weekly Gmail digest draft")
    print("=" * 60)
    subject = meta["digest"]["subject"]
    this_week_count = meta["digest"]["this_week_count"]
    print(f"  Subject: {subject}")
    print(f"  Events this week: {this_week_count}")
    print("  --- digest preview (plain text) ---")
    for line in digest_text.splitlines():
        print(f"  {line}")
    print("  --- end preview ---")

    if not actually_create:
        print("  Draft gate = False — not creating a Gmail draft.")
        return
    if this_week_count == 0:
        print("  No events this week — skipping draft (empty-week guard).")
        return

    result = gmail.create_draft(
        subject=subject,
        body=digest_html,
        content_type="text/html",
        text_alternative=digest_text,
    )
    print(f"  Draft created: {result['draftId']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kids Schedule — search Gmail, extract events, publish to GitHub Pages."
    )
    parser.add_argument(
        "--lookback-days", type=int, default=60,
        help="How many days of received email to search (default: 60)."
    )
    parser.add_argument(
        "--model", type=str, default="claude-sonnet-4-6",
        help="Anthropic model for event extraction."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the full pipeline but skip publishing."
    )
    parser.add_argument(
        "--intentional-failure", action="store_true",
        help="Raise immediately to verify the Actions failure-notification "
             "path end-to-end. Does no real work."
    )
    parser.add_argument(
        "--create-draft", action="store_true",
        help="Create a weekly Gmail digest draft. Default is OFF so manual "
             "and local runs do not spam Ellen's drafts folder. The "
             "scheduled workflow run passes CREATE_DRAFT=1 to flip this on."
    )
    args = parser.parse_args()

    if args.intentional_failure:
        raise RuntimeError(
            "Intentional failure triggered via --intentional-failure. "
            "This is a test of the GitHub Actions notification path."
        )

    # Step 1: Build queries
    config = step1_build_queries(args.lookback_days)

    # Step 1b: Filter audit (if due)
    gmail = GmailClient()
    profile = gmail.get_profile()
    print(f"  Authenticated as: {profile.get('emailAddress')}")
    config = step1b_filter_audit(gmail, config, args.model, args.lookback_days)

    # Step 2: Search Gmail
    search_results = step2_search_gmail(gmail, config)

    # Step 2b: Read promising messages
    full_emails = step2b_read_promising(gmail, search_results)

    if not full_emails:
        print("\nNo emails found. The page will note an empty run.")
        candidates: list[dict] = []
        irrelevant_senders: list[dict] = []
    else:
        # Step 3: Agent extraction
        candidates, irrelevant_senders = step3_extract_events(
            full_emails, model=args.model
        )

    # Step 3b: Feed agent-flagged senders into the auto-blocklist.
    if not args.dry_run:
        step3b_update_auto_blocklist(irrelevant_senders)
    else:
        print("\n  (dry-run: skipping auto-blocklist update)")

    # Step 4: Process events
    pages_url = _load_pages_url()
    html, body, meta, digest_text, digest_html = step4_process_events(
        candidates, pages_url=pages_url,
    )

    # Step 5: Publish
    step5_publish(html, meta, args.dry_run)

    # Step 6: Weekly Gmail digest draft (heavily gated — see should_create_draft)
    step6_create_draft(
        gmail, meta, digest_text, digest_html,
        actually_create=should_create_draft(args),
    )

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  Events: {meta['counts']['future_dated']} dated, "
          f"{meta['counts']['undated']} undated")
    return 0


if __name__ == "__main__":
    sys.exit(main())