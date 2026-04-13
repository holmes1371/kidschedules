#!/usr/bin/env python3
"""Kids Schedule — GitHub Actions orchestrator.

Runs the full pipeline:
  1. build_queries.py → date windows + Gmail query strings
  2. Gmail API searches → message stubs
  3. Gmail API reads → full email bodies for promising messages
  4. Anthropic agent → candidate event dicts (judgment step)
  5. process_events.py → rendered HTML page + metadata
  6. Commit index.html to gh-pages branch → GitHub Pages serves it

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
    print(f"  Blocklist: {config['exclusions']['blocklist_size']} senders excluded")
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

    with open(tight_path, "w") as f:
        json.dump(tight_results, f)
    with open(loose_path, "w") as f:
        json.dump(loose_results, f)

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
            # Remove senders from blocklist
            blocklist_path = os.path.join(PROJECT_ROOT, "blocklist.txt")
            with open(blocklist_path, "r") as f:
                lines = f.readlines()
            removed = []
            new_lines = []
            for line in lines:
                stripped_line = line.strip()
                if stripped_line in unblock:
                    removed.append(stripped_line)
                else:
                    new_lines.append(line)
            if removed:
                with open(blocklist_path, "w") as f:
                    f.writelines(new_lines)
                print(f"  Removed from blocklist: {removed}")

                # Rebuild queries with updated blocklist
                print("  Rebuilding queries with updated blocklist ...")
                config = step1_build_queries(lookback_days)

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
) -> list[dict[str, Any]]:
    """Send emails to the Anthropic agent for event extraction."""
    print("\n" + "=" * 60)
    print("STEP 3: Agent event extraction (Anthropic API)")
    print("=" * 60)
    print(f"  Model: {model}")
    print(f"  Emails to process: {len(full_emails)}")

    events = extract_events(full_emails, model=model)
    print(f"  Candidate events extracted: {len(events)}")
    return events


def step4_process_events(
    candidates: list[dict[str, Any]],
) -> tuple[str, str, dict]:
    """Run process_events.py and return (html, body_text, meta)."""
    print("\n" + "=" * 60)
    print("STEP 4: Processing events (filter, dedupe, sort, render)")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(candidates, f)
        candidates_path = f.name

    body_path = candidates_path.replace(".json", "-body.txt")
    html_path = candidates_path.replace(".json", "-page.html")
    meta_path = candidates_path.replace(".json", "-meta.json")

    try:
        run_script(
            "process_events.py",
            [
                "--candidates", candidates_path,
                "--body-out", body_path,
                "--html-out", html_path,
                "--meta-out", meta_path,
            ],
        )

        with open(body_path, "r", encoding="utf-8") as f:
            body = f.read()
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        counts = meta["counts"]
        print(f"  Candidates in: {counts['candidates_in']}")
        print(f"  Future dated: {counts['future_dated']}")
        print(f"  Undated: {counts['undated']}")
        print(f"  Dropped (past): {counts['dropped_past']}")
        if meta["warnings"]:
            for w in meta["warnings"]:
                print(f"  WARNING: {w}")

        return html, body, meta
    finally:
        for p in [candidates_path, body_path, html_path, meta_path]:
            try:
                os.unlink(p)
            except OSError:
                pass


def step5_publish(html: str, meta: dict, dry_run: bool) -> None:
    """Write index.html + dated archive copy to docs/ for GitHub Pages."""
    print("\n" + "=" * 60)
    print("STEP 5: Publishing to GitHub Pages")
    print("=" * 60)

    today_iso = meta["today_iso"]  # e.g. "2026-04-13"

    if dry_run:
        print("  DRY RUN — skipping publish")
        print(f"  Would write docs/index.html + docs/{today_iso}.html")
        return

    os.makedirs(PAGES_OUTPUT_DIR, exist_ok=True)

    # Write the current schedule as index.html
    index_path = os.path.join(PAGES_OUTPUT_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Wrote docs/index.html ({len(html)} chars)")

    # Write a dated archive copy (e.g. docs/2026-04-13.html)
    archive_path = os.path.join(PAGES_OUTPUT_DIR, f"{today_iso}.html")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Wrote docs/{today_iso}.html (archive copy)")

    # Rebuild the archive index page
    run_script("build_archive_index.py", ["--docs-dir", PAGES_OUTPUT_DIR])

    # .nojekyll so GitHub Pages serves raw HTML
    nojekyll = os.path.join(PAGES_OUTPUT_DIR, ".nojekyll")
    if not os.path.exists(nojekyll):
        with open(nojekyll, "w") as f:
            pass
        print("  Created docs/.nojekyll")

    print(f"  Subject: {meta['subject']}")


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
    args = parser.parse_args()

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
    else:
        # Step 3: Agent extraction
        candidates = step3_extract_events(full_emails, model=args.model)

    # Step 4: Process events
    html, body, meta = step4_process_events(candidates)

    # Step 5: Publish
    step5_publish(html, meta, args.dry_run)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"  Events: {meta['counts']['future_dated']} dated, "
          f"{meta['counts']['undated']} undated")
    return 0


if __name__ == "__main__":
    sys.exit(main())