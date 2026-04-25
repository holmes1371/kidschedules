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
import datetime as dt
import email.utils
import json
import os
import subprocess
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import tldextract

import events_state as es
import newsletter_stats as ns
from gmail_client import GmailClient
from agent import _sender_key, extract_events, review_stripped_messages

# ── scripts/ modules shared with process_events.py / build_queries.py ──
# freemail_domains.load_freemail_domains lives alongside protected_senders
# in scripts/ because they're conceptually a pair (both drive per-sender
# render/block decisions). main.py imports it in-process to stamp
# sender_block_key on each candidate event.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from freemail_domains import load_freemail_domains  # noqa: E402


PAGES_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "docs")
FUTURE_EVENTS_PATH = os.path.join(PROJECT_ROOT, "future_events.json")
EVENTS_STATE_PATH = os.path.join(PROJECT_ROOT, "events_state.json")
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
IGNORED_SENDERS_PATH = os.path.join(PROJECT_ROOT, "ignored_senders.json")
PROTECTED_SENDERS_PATH = os.path.join(PROJECT_ROOT, "protected_senders.txt")
FREEMAIL_DOMAINS_PATH = os.path.join(PROJECT_ROOT, "freemail_domains.txt")
PRIOR_EVENTS_PATH = os.path.join(PROJECT_ROOT, "prior_events.json")
SENDER_STATS_PATH = os.path.join(PROJECT_ROOT, "sender_stats.json")


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
    args = [
        "--lookback-days", str(lookback_days),
        "--ignored-senders", IGNORED_SENDERS_PATH,
        "--protected-senders", PROTECTED_SENDERS_PATH,
    ]
    output = run_script("build_queries.py", args)
    config = json.loads(output)
    print(f"  Today: {config['today_human']}")
    print(f"  Email window: {config['email_window']['after']} → "
          f"{config['email_window']['before']}")
    excl = config["exclusions"]
    print(
        f"  Blocklist: {excl['blocklist_size']} senders excluded "
        f"({excl['blocklist_size_main']} hand-curated + "
        f"{excl['blocklist_size_auto']} auto + "
        f"{excl.get('blocklist_size_ignored_senders', 0)} UI-ignored)"
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
    """Run the configured Gmail searches and return results keyed by category.

    Query count is derived from ``config["queries"]`` so the log line
    stays accurate as templates are added or suppressed (e.g. the
    roster-driven ``kid_names`` template lands only when the roster has
    keys and ``--no-kid-names`` is off).
    """
    queries = config["queries"]
    print("\n" + "=" * 60)
    print(f"STEP 2: Searching Gmail ({len(queries)} queries)")
    print("=" * 60)
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


def _dedupe_by_thread(
    stubs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return at most one stub per distinct Gmail threadId, keeping the
    latest-by-Date message per thread.

    When a Gmail thread produces multiple search hits across the 5
    overlapping query templates (e.g. a dance-studio email that matches
    ``school_activities``, ``sports_extracurriculars``, and
    ``newsletters_calendars`` simultaneously), we only want the agent to
    see one representative — the latest reply, since that is typically
    the message carrying the operative date/decision. Pure function; no
    I/O. See design/dedupe-candidate-messages.md for the decision
    record and accepted-risk discussion.

    Sort key is the stub's ``headers["Date"]`` parsed by
    ``email.utils.parsedate_to_datetime``. Tie policy:

    - Parseable dates outrank unparseable ones — a reply with a good
      Date header beats a sibling with a malformed one even if the
      latter was encountered first.
    - Within the parseable set, the latest datetime wins.
    - Within the unparseable set (every stub in the thread had a bad
      Date header), the first-seen stub wins.

    Stubs with empty or missing ``threadId`` bypass grouping and are
    preserved as-is — Gmail always returns a threadId in practice, but
    the helper stays tolerant so a malformed stub never silently
    collapses a legitimate message.

    Output preserves first-encounter order of each group's slot: a
    thread whose first stub appeared at input index 0 precedes a
    thread whose first stub appeared at input index 3, regardless of
    which stub ultimately won each group.
    """
    def _parse(stub: dict[str, Any]) -> dt.datetime | None:
        raw = stub.get("headers", {}).get("Date", "")
        if not raw:
            return None
        try:
            return email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None

    # slots: first-encounter idx per distinct group (or per passthrough
    # stub), in order. state[slot] is the (stub, parsed_dt) pair for
    # that slot's current winner; parsed_dt is None for passthrough or
    # for an incumbent whose Date header is unparseable.
    slots: list[int] = []
    state: dict[int, tuple[dict[str, Any], dt.datetime | None]] = {}
    slot_by_tid: dict[str, int] = {}

    for idx, stub in enumerate(stubs):
        tid = stub.get("threadId") or ""
        if not tid:
            slots.append(idx)
            state[idx] = (stub, None)
            continue
        parsed = _parse(stub)
        if tid not in slot_by_tid:
            slot_by_tid[tid] = idx
            slots.append(idx)
            state[idx] = (stub, parsed)
            continue
        slot = slot_by_tid[tid]
        _incumbent, incumbent_dt = state[slot]
        # Replace only when the incoming stub has a better Date claim:
        # either it's parseable and the incumbent's isn't, or both are
        # parseable and the incoming one is strictly later. Equal
        # datetimes preserve first-seen; both-unparseable preserves
        # first-seen.
        if parsed is not None and (
            incumbent_dt is None or parsed > incumbent_dt
        ):
            state[slot] = (stub, parsed)

    return [state[s][0] for s in slots]


def step2b_read_promising(
    gmail: GmailClient,
    search_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Read full bodies for all unique messages across searches.

    Two dedup passes run before any full-body fetch: first by
    ``messageId`` (collapses the common case where Gmail returns the
    same message under multiple overlapping query templates), then by
    ``threadId`` via :func:`_dedupe_by_thread` (collapses the less
    common but still-expensive case where an active reply chain
    produces several distinct messages that each match the queries).
    Dropping the redundancy here — before the per-message
    ``read_message`` call — saves Gmail API quota alongside the
    downstream agent cost. See design/dedupe-candidate-messages.md.
    """
    print("\n" + "=" * 60)
    print("STEP 2b: Reading full message bodies")
    print("=" * 60)

    seen_ids: set[str] = set()
    emails_to_read: list[dict[str, Any]] = []
    total_stubs = 0

    for category, messages in search_results.items():
        total_stubs += len(messages)
        for msg in messages:
            mid = msg["messageId"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            emails_to_read.append(msg)

    unique_message_ids = len(emails_to_read)
    emails_to_read = _dedupe_by_thread(emails_to_read)

    print(
        f"  Collected {total_stubs} stub(s) across "
        f"{len(search_results)} queries"
    )
    print(f"  Unique messageIds: {unique_message_ids}")
    print(f"  After thread dedup: {len(emails_to_read)}")

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


def _now_iso() -> str:
    """Current local wall-clock time as an ISO 8601 string with offset."""
    return dt.datetime.now(ZoneInfo("America/New_York")).isoformat(
        timespec="seconds"
    )


def _reextract_eviction(
    message_id: str,
    state_path: str = EVENTS_STATE_PATH,
    now_iso: str | None = None,
) -> None:
    """Evict a Gmail message ID from events_state.json.

    Removes the message from processed_messages and purges every cached
    event whose source_message_id matches. Persists the result so the
    next step2c load sees the evicted state. The re-extraction itself
    happens naturally: with the message dropped from processed_messages,
    filter_unprocessed treats it as new and feeds it back to the agent.

    An unknown message_id is a warning, not a failure — fat-fingering a
    hex string should not fail the pipeline.
    """
    print("=" * 60)
    print(f"REEXTRACT: evicting message {message_id} from cache")
    print("=" * 60)

    if not os.path.exists(state_path):
        print(f"  No events_state.json at {state_path} — nothing to evict.")
        return

    state = es.load_state(state_path)

    msg_evicted = 0
    if message_id in state["processed_messages"]:
        del state["processed_messages"][message_id]
        msg_evicted = 1

    events_evicted = 0
    kept_events: dict[str, Any] = {}
    for eid, ev in state["events"].items():
        if ev.get("source_message_id") == message_id:
            events_evicted += 1
        else:
            kept_events[eid] = ev
    state["events"] = kept_events

    if msg_evicted == 0 and events_evicted == 0:
        print(
            f"  WARNING: no match in cache for {message_id} — no-op. "
            f"(Check the message ID; the digest alert lines print it verbatim.)"
        )
        return

    print(
        f"  Evicted {msg_evicted} processed_message entry and "
        f"{events_evicted} cached event(s)."
    )
    es.save_state(state_path, state, now_iso or _now_iso())


def _bootstrap_from_future_events(
    state: dict[str, Any], now_iso: str
) -> int:
    """One-time migration: seed an empty cache from future_events.json.

    Returns the number of events bootstrapped. No-op (returns 0) if the
    cache already has events or the legacy file is missing.
    """
    if state["events"] or not os.path.exists(FUTURE_EVENTS_PATH):
        return 0
    try:
        with open(FUTURE_EVENTS_PATH, "r", encoding="utf-8") as f:
            banked = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(banked, list) or not banked:
        return 0
    es.stamp_event_ids(banked)
    es.merge_events(state, banked, now_iso)
    return len(banked)


def step2c_load_cache_and_filter(
    full_emails: list[dict[str, Any]],
    state_path: str = EVENTS_STATE_PATH,
    today: dt.date | None = None,
    now_iso: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load the event cache, GC stale entries, filter out processed messages.

    Returns (state, new_emails). `new_emails` is the subset of `full_emails`
    whose messageId is not already in `state.processed_messages` — that's
    what step 3 should send to the agent. Cache statistics are logged.

    On first run after the cache was introduced, bootstraps from
    future_events.json so the accumulated far-future bank isn't lost.
    """
    print("\n" + "=" * 60)
    print("STEP 2c: Cache filter (skip already-processed messages)")
    print("=" * 60)

    state = es.load_state(state_path)
    bootstrapped = _bootstrap_from_future_events(
        state, now_iso or _now_iso(),
    )
    if bootstrapped:
        print(
            f"  Bootstrapped {bootstrapped} event(s) from future_events.json "
            f"(one-time migration)"
        )
    gc_counts = es.gc_state(state, today or dt.date.today())
    print(
        f"  Loaded cache: {len(state['processed_messages'])} processed "
        f"message(s), {len(state['events'])} event(s)"
    )
    print(
        f"  GC dropped: {gc_counts['messages_dropped']} message(s), "
        f"{gc_counts['events_dropped']} event(s)"
    )

    new_emails = es.filter_unprocessed(full_emails, state)
    cached = len(full_emails) - len(new_emails)
    print(f"  Total read: {len(full_emails)}")
    print(f"  Cached (skip agent): {cached}")
    print(f"  New (send to agent): {len(new_emails)}")
    return state, new_emails


def _compute_block_key(
    addr: str, domain: str, freemail: frozenset[str]
) -> str:
    """Decide what string the Ignore-sender button should submit.

    Freemail (consumer email) domains block one address at a time;
    institutional domains block the whole registrable domain. The
    ``freemail`` membership set comes from ``freemail_domains.txt`` —
    see design/sender-block-granularity.md for the full decision record.

    Returns:
        - ``""`` when the event has no attributable domain (the
          downstream render gate uses this emptiness as "no button").
        - The lowercased full address when ``domain`` is in ``freemail``
          and a non-empty address is available.
        - The domain itself otherwise — matches today's behavior for
          every institutional sender and the graceful-degrade case
          where the freemail list is empty or the address failed to
          parse.
    """
    if not domain:
        return ""
    if not addr or domain not in freemail:
        return domain
    return addr.strip().lower()


def _attach_sender_domains(
    candidates: list[dict[str, Any]],
    new_emails: list[dict[str, Any]],
    freemail: frozenset[str] | None = None,
) -> None:
    """Stamp each candidate with its sender's registrable domain and
    the block key the Ignore-sender button should submit.

    The agent echoes the source email's Message ID as
    `event["source_message_id"]` (enforced upstream in agent.py). Here we
    use that ID to look up the original From header in the batch we sent
    to the agent, parse the address, and run it through tldextract so
    multi-level TLDs like `greenfield.k12.ny.us` come out right.

    Two fields land on every event:

    - ``sender_domain`` — registrable domain, lowercased. Unchanged
      semantic from earlier schema versions; the protected-senders
      guard (``is_protected``) and sender grouping continue to key on
      this field.
    - ``sender_block_key`` — the string the Ignore-sender button submits
      (also used for the ``data-sender`` attribute). Equals the
      lowercased full address when ``sender_domain`` is a freemail
      provider (gmail.com, yahoo.com, etc.); equals ``sender_domain``
      otherwise. Empty when attribution fails.

    Any failure along the way — missing ID, missing From, malformed
    address, empty registered_domain — sets both fields to ``""``.
    Downstream the Ignore-sender button simply won't render for that
    event. Never raises. Matches the tolerant-parse posture in agent.py.

    ``freemail`` defaults to the committed ``freemail_domains.txt`` so
    most callers pass nothing; tests pass an explicit frozenset.

    Mutates ``candidates`` in place.
    """
    if freemail is None:
        freemail = load_freemail_domains(FREEMAIL_DOMAINS_PATH)
    from_by_id = {
        em.get("messageId", ""): em.get("from_", "")
        for em in new_emails
    }
    from_by_id.pop("", None)
    missing = 0
    for event in candidates:
        sid = event.get("source_message_id", "")
        from_header = from_by_id.get(sid, "")
        if not from_header:
            event["sender_domain"] = ""
            event["sender_block_key"] = ""
            missing += 1
            continue
        _, addr = email.utils.parseaddr(from_header)
        if not addr:
            event["sender_domain"] = ""
            event["sender_block_key"] = ""
            missing += 1
            continue
        extracted = tldextract.extract(addr)
        # `top_domain_under_public_suffix` is the forward-compatible name
        # (5.1+); the old `registered_domain` is deprecated for removal.
        domain = (extracted.top_domain_under_public_suffix or "").lower()
        event["sender_domain"] = domain
        event["sender_block_key"] = _compute_block_key(addr, domain, freemail)
        if not domain:
            missing += 1
    if missing:
        print(
            f"  WARNING: {missing} event(s) have no sender_domain "
            f"(Ignore-sender button will be omitted for those)"
        )


def _per_message_counts(
    new_emails: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[tuple[str, str, int]]:
    """Build (sender_key, message_id, count) triples for the stats update.

    Pure function — no I/O. One triple per message the agent saw this
    run, even when the agent emitted zero events for that message. The
    zero-event case is load-bearing: a newsletter that legitimately
    produced 0 events this week (quiet summer issue) contributes a 0
    to the rolling median, which is exactly what the outlier check
    needs to stay honest.

    Counts are derived from `candidates[*].source_message_id`; the
    sender key is pulled from the matching email's `from_` header via
    `agent._sender_key` so the key shape matches what the stats file
    stores (lowercased mailbox).

    Candidates whose `source_message_id` doesn't match any email in
    `new_emails` are ignored — they can't feed a per-sender count, and
    the upstream filter in `agent._filter_events_by_source_id` already
    warns on this case.
    """
    count_by_id: dict[str, int] = {}
    new_message_ids = {em.get("messageId", "") for em in new_emails}
    new_message_ids.discard("")
    for ev in candidates:
        mid = ev.get("source_message_id", "")
        if mid in new_message_ids:
            count_by_id[mid] = count_by_id.get(mid, 0) + 1

    triples: list[tuple[str, str, int]] = []
    for em in new_emails:
        mid = em.get("messageId", "")
        sender_key = _sender_key(em.get("from_", ""))
        triples.append((sender_key, mid, count_by_id.get(mid, 0)))
    return triples


def _print_outlier_alerts(alerts: list[dict[str, Any]]) -> None:
    """Print the STEP 3c outlier-alerts section to stdout.

    The banner is unconditional so an Actions-log reader can tell
    "stats were checked and clean" from "stats were skipped entirely".
    Each alert line echoes the message ID verbatim so Tom can paste it
    straight into a `--reextract` invocation.
    """
    print("\n" + "=" * 60)
    print("STEP 3c: Outlier alerts (possible under-extraction)")
    print("=" * 60)
    if not alerts:
        print("  No outlier alerts this run.")
        return
    for a in alerts:
        print(
            f"  ⚠️ {a['sender']} — message {a['message_id']} "
            f"yielded {a['current_count']} event(s); "
            f"prior median {a['prior_median']}, threshold {a['threshold']}"
        )
    print(
        "  (to re-run an under-extracted message: "
        "python main.py --reextract <MESSAGE_ID>)"
    )


def step3_extract_events(
    full_emails: list[dict[str, Any]],
    model: str,
    newsletter_senders: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Send emails to the Anthropic agent for event extraction.

    `newsletter_senders` is the set of lowercased sender mailboxes that
    the agent should batch one-per-API-call (see design/newsletter-
    robustness.md). `None` falls back to the default BATCH_SIZE batching
    for every email — used by any test that doesn't care about the
    partition and matches the kwarg default on `agent.extract_events`.
    """
    print("\n" + "=" * 60)
    print("STEP 3: Agent event extraction (Anthropic API)")
    print("=" * 60)
    print(f"  Model: {model}")
    print(f"  Emails to process: {len(full_emails)}")

    events, irrelevant_senders = extract_events(
        full_emails, model=model, newsletter_senders=newsletter_senders,
    )
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
                "--protected-senders", PROTECTED_SENDERS_PATH,
            ],
        )
    finally:
        try:
            os.unlink(suggestions_path)
        except OSError:
            pass


def step4_process_events(
    candidates: list[dict[str, Any]],
    pages_url: str = "",
    dry_run: bool = False,
    outlier_alerts: list[dict[str, Any]] | None = None,
    lookback_days: int = 60,
) -> tuple[str, str, dict, str, str]:
    """Run process_events.py and return (html, body_text, meta,
    digest_text, digest_html).

    Far-future events (beyond the 60-day display window) now persist
    in events_state.json alongside everything else, so this step no
    longer maintains a separate bank file.

    `outlier_alerts` (when non-empty) is serialised to a tempfile and
    forwarded as `--outlier-alerts`, which causes the weekly digest
    bodies to render an ⚠️ Possible under-extraction block. `None` or
    an empty list skips the flag so the digest degrades cleanly.

    `lookback_days` is forwarded as `--lookback-days` so the rendered
    page header ("{N} day lookback") reflects the actual Gmail-search
    window main() used upstream. Default 60 matches the workflow's
    default; only the `--lookback-days` dispatch-input path overrides it.
    """
    print("\n" + "=" * 60)
    print("STEP 4: Processing events (filter, dedupe, sort, render)")
    print("=" * 60)

    # Dump the exact input to process_events.py so dev_render.py can replay
    # it against the real data without hitting any APIs.
    try:
        os.makedirs(os.path.dirname(LAST_RUN_FIXTURE_PATH), exist_ok=True)
        with open(LAST_RUN_FIXTURE_PATH, "w", encoding="utf-8") as f:
            json.dump(candidates, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"  WARNING: could not write last_run fixture: {e}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(candidates, f, ensure_ascii=False)
        candidates_path = f.name

    body_path = candidates_path.replace(".json", "-body.txt")
    html_path = candidates_path.replace(".json", "-page.html")
    meta_path = candidates_path.replace(".json", "-meta.json")
    digest_text_path = candidates_path.replace(".json", "-digest.txt")
    digest_html_path = candidates_path.replace(".json", "-digest.html")

    alerts_path: str | None = None
    if outlier_alerts:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="-alerts.json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(outlier_alerts, f, ensure_ascii=False)
            alerts_path = f.name

    try:
        webhook_url = _load_webhook_url()
        script_args = [
            "--candidates", candidates_path,
            "--body-out", body_path,
            "--html-out", html_path,
            "--meta-out", meta_path,
            "--digest-text-out", digest_text_path,
            "--digest-html-out", digest_html_path,
            "--pages-url", pages_url,
            "--display-window-days", "60",
            "--lookback-days", str(lookback_days),
            "--webhook-url", webhook_url,
            "--ignored", IGNORED_EVENTS_PATH,
            "--protected-senders", PROTECTED_SENDERS_PATH,
            "--prior-events", PRIOR_EVENTS_PATH,
        ]
        if alerts_path:
            script_args += ["--outlier-alerts", alerts_path]
        # Per-event .ics files land in docs/ics/ for the Pages artifact to
        # pick up; skipped on dry-run to avoid churning the publish dir.
        if not dry_run:
            script_args += ["--ics-out-dir", os.path.join(PAGES_OUTPUT_DIR, "ics")]
        run_script("process_events.py", script_args)

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

        counts = meta["counts"]
        print(f"  Candidates in: {counts['candidates_in']}")
        print(f"  Displayed (next 60 days): {counts['future_dated']}")
        print(f"  Beyond window (kept in cache): {counts.get('banked_far_future', 0)}")
        print(f"  Undated: {counts['undated']}")
        print(f"  Dropped (past): {counts['dropped_past']}")
        print(f"  Dropped (ignored): {counts.get('dropped_ignored', 0)}")
        if meta["warnings"]:
            for w in meta["warnings"]:
                print(f"  WARNING: {w}")

        return html, body, meta, digest_text, digest_html
    finally:
        cleanup_paths = [candidates_path, body_path, html_path, meta_path,
                         digest_text_path, digest_html_path]
        if alerts_path:
            cleanup_paths.append(alerts_path)
        for p in cleanup_paths:
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
    parser.add_argument(
        "--reextract", type=str, default=None, metavar="MESSAGE_ID",
        help="Evict a Gmail message ID from events_state.json before the "
             "Gmail fetch so the next run re-extracts it. Use when the "
             "weekly digest flags a newsletter as under-extracted — paste "
             "the message ID verbatim from the alert line. Single ID per "
             "invocation; bulk form is out of scope."
    )
    args = parser.parse_args()

    if args.intentional_failure:
        raise RuntimeError(
            "Intentional failure triggered via --intentional-failure. "
            "This is a test of the GitHub Actions notification path."
        )

    # Pre-fetch eviction (if requested). Runs before any other work so the
    # on-disk state that step2c loads already reflects the eviction.
    if args.reextract:
        _reextract_eviction(args.reextract)

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

    # Step 2c: Load event cache + GC + filter out already-processed messages.
    # Runs even when full_emails is empty so GC still happens and cached
    # events still get rendered.
    state, new_emails = step2c_load_cache_and_filter(full_emails)
    now_iso = _now_iso()

    # Load sender stats BEFORE extraction so the newsletter set can be
    # passed to the agent (newsletter emails get batch-of-1, regulars
    # BATCH_SIZE). Missing/corrupt file → empty stats dict; see
    # newsletter_stats.load_stats.
    sender_stats = ns.load_stats(SENDER_STATS_PATH)

    if not new_emails:
        if not full_emails:
            print("\nNo emails found. Rendering from cache only.")
        else:
            print("\n  All messages cached — skipping agent extraction.")
        candidates: list[dict] = []
        irrelevant_senders: list[dict] = []
    else:
        # Step 3: Agent extraction (only for new, uncached messages).
        # Known newsletter senders get a batch-of-1 API call; everyone
        # else batches at BATCH_SIZE. See design/newsletter-robustness.md.
        candidates, irrelevant_senders = step3_extract_events(
            new_emails, model=args.model,
            newsletter_senders=ns.newsletter_senders(sender_stats),
        )
        # Attach sender_domain before caching so the Ignore-sender button
        # can render deterministically on future re-paints.
        _attach_sender_domains(candidates, new_emails)
        # Merge newly-extracted events into the cache and mark the
        # messages processed. Save before step 4 so the (expensive)
        # agent call is durable even if rendering fails.
        es.stamp_event_ids(candidates)
        es.merge_events(state, candidates, now_iso)
        es.mark_processed(
            state, [e["messageId"] for e in new_emails], now_iso,
        )

    # Persist cache. `last_updated_iso` reflects this run even when no new
    # emails were extracted — useful for observing GC-only runs.
    es.save_state(EVENTS_STATE_PATH, state, now_iso)

    # Step 3b: Feed agent-flagged senders into the auto-blocklist.
    if not args.dry_run:
        step3b_update_auto_blocklist(irrelevant_senders)
    else:
        print("\n  (dry-run: skipping auto-blocklist update)")

    # Step 3c: Fold this run into sender_stats, compute outlier alerts,
    # and print the alerts banner. Alerts are computed BEFORE the stats
    # update so prior_median reflects history only — folding in would
    # bias the threshold toward the current run's (potentially short)
    # counts. Zero-yield messages still contribute a 0 via
    # `_per_message_counts` so a quiet newsletter issue doesn't skew
    # the rolling window upward.
    per_message_counts = _per_message_counts(new_emails, candidates)
    alerts = ns.outlier_alerts(sender_stats, per_message_counts)
    if per_message_counts:
        ns.update_sender_counts(sender_stats, per_message_counts, now_iso)
        ns.classify_senders(sender_stats)
        if args.dry_run:
            print("\n  (dry-run: skipping sender_stats save)")
        else:
            ns.save_stats(SENDER_STATS_PATH, sender_stats, now_iso)
    _print_outlier_alerts(alerts)

    # Step 4: Process events — hand the merged cache as the candidate pool
    # so stable events from prior runs survive without a re-extraction.
    # Outlier alerts (if any) flow through to the weekly digest bodies;
    # on Wed/Sat runs the digest is suppressed upstream so the alert
    # tempfile is rendered but never surfaces to Gmail.
    pages_url = _load_pages_url()
    html, body, meta, digest_text, digest_html = step4_process_events(
        list(state["events"].values()), pages_url=pages_url,
        dry_run=args.dry_run,
        outlier_alerts=alerts,
        lookback_days=args.lookback_days,
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