"""Fetch ignored-sender rows from the Apps Script web app and write them
to `ignored_senders.json` in the repo root.

Design: the Google Sheet "Ignored Senders" tab is the single source of
truth. This helper is a thin fetch+normalize+write layer so the Gmail
pipeline can read a fast local cache instead of hitting Apps Script on
every run. The file is committed to the repo alongside `ignored_events.json`
so diffs surface ignored-sender churn in git history.

Companion to the existing inline "Sync ignored events" workflow step —
split out into a Python module here because the normalization logic
(lowercasing, regex filtering, first-wins dedup, stable sort, no-op
short-circuit) is non-trivial enough to warrant unit tests.

CLI:
    python scripts/sync_ignored_senders.py \\
        --url https://script.google.com/macros/s/.../exec \\
        --secret "$IGNORE_READ_SECRET" \\
        --out ignored_senders.json

Exit is always 0 on network / parse failure — the existing cache file
is left untouched so the pipeline degrades gracefully. Matches the
existing ignored-events step's posture ("keep existing cache on
fetch failure, don't zero it out").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

# Same shape as apps_script.gs DOMAIN_RE. Applied after lowercasing so
# casing-only variants don't fail.
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


def normalize_rows(rows: list[dict]) -> list[dict]:
    """Lowercase + trim domain, drop invalid rows, dedup first-wins, sort.

    Apps Script already dedups on GET, so this is defensive against
    direct-sheet-read paths and against manual edits that slip in a
    trailing space or a casing variant.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("domain")
        if not isinstance(raw, str):
            continue
        domain = raw.strip().lower()
        if not DOMAIN_RE.match(domain):
            continue
        if domain in seen:
            continue  # first-wins
        seen[domain] = {
            "domain": domain,
            "source": str(row.get("source") or ""),
            "timestamp": str(row.get("timestamp") or ""),
        }
    return [seen[d] for d in sorted(seen)]


def _serialize(rows: list[dict]) -> str:
    """Canonical on-disk form: indent=2, trailing newline, no escaping."""
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def write_if_changed(path: str, rows: list[dict]) -> bool:
    """Write `rows` to `path`. Skip the write (and return False) if the
    file already exists with identical serialized content."""
    payload = _serialize(rows)
    try:
        with open(path, encoding="utf-8") as f:
            current = f.read()
    except FileNotFoundError:
        current = None
    if current == payload:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)
    return True


def _fetch(url: str, secret: str, timeout: float) -> list | None:
    qs = urllib.parse.urlencode({"secret": secret, "kind": "ignored_senders"})
    full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"
    try:
        with urllib.request.urlopen(full_url, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as err:  # noqa: BLE001
        print(f"sync_ignored_senders: fetch failed — {err}", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(
            "sync_ignored_senders: response was not a JSON list — keeping cache.",
            file=sys.stderr,
        )
        return None
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Apps Script /exec URL")
    ap.add_argument("--secret", required=True, help="IGNORE_READ_SECRET value")
    ap.add_argument("--out", required=True, help="path to ignored_senders.json")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    rows = _fetch(args.url, args.secret, args.timeout)
    if rows is None:
        return 0  # graceful degrade; cache untouched

    normalized = normalize_rows(rows)
    changed = write_if_changed(args.out, normalized)
    n = len(normalized)
    if changed:
        print(f"Synced {n} ignored sender(s) to {args.out}.")
    else:
        print(f"No changes ({n} ignored sender(s) on disk).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
