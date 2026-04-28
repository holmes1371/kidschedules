"""Fetch ignored-event rows from the Apps Script web app and write them
to `ignored_events.json` in the runner's working directory.

Companion to `sync_completed_events.py` and `sync_ignored_senders.py`.
The Google Sheet "Ignored Events" tab is the single source of truth
(ROADMAP #6 / #7); this helper is a thin fetch+normalize+write layer
so the rendering pipeline can read a fast local cache instead of hitting
Apps Script during the build itself.

Promoted from an inline bash step in `weekly-schedule.yml` as part of
ROADMAP #37 (auto-GC the Ignored + Completed sheets). The promotion is a
prerequisite for testing the past-dated lazy filter under pytest; the
filter itself lives in `_drop_past_dated` below.

The cache file is per-run ephemeral — the workflow writes it into the
runner's working directory before `main.py` executes, `process_events.py`
reads it via `--ignored`, and the runner is torn down afterward.
`ignored_events.json` is NEVER committed to the repo.

CLI:
    python scripts/sync_ignored_events.py \\
        --url https://script.google.com/macros/s/.../exec \\
        --secret "$IGNORE_READ_SECRET" \\
        --out ignored_events.json

Exit is always 0 on network / parse failure — the existing cache file
is left untouched so the pipeline degrades gracefully. Matches the
existing `sync_completed_events.py` / `sync_ignored_senders.py` posture.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request

# Same shape as the apps_script.gs id-validation regex. Applied after
# trimming. The 12-hex form comes from sha1[:12] in events_state.py and
# scripts/process_events.py.
ID_RE = re.compile(r"^[a-f0-9]{12}$")


def normalize_rows(rows: list[dict]) -> list[dict]:
    """Trim id, drop invalid rows, dedup first-wins, sort by id.

    Apps Script already dedups on GET, so this is defensive against
    direct-sheet-read paths and against manual edits that slip in a
    trailing space or a casing variant. Mirrors normalize_rows in
    sync_completed_events.py one-for-one apart from the field surface
    (ignored_at vs completed_at).
    """
    seen: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("id")
        if not isinstance(raw, str):
            continue
        eid = raw.strip().lower()
        if not ID_RE.match(eid):
            continue
        if eid in seen:
            continue  # first-wins
        seen[eid] = {
            "id": eid,
            "name": str(row.get("name") or ""),
            "date": str(row.get("date") or ""),
            "ignored_at": str(row.get("ignored_at") or ""),
        }
    return [seen[k] for k in sorted(seen)]


def _drop_past_dated(rows: list[dict], today: dt.date) -> list[dict]:
    """ROADMAP #37 Tier 1. Drop rows whose `date` parses as ISO-8601 and
    is strictly before `today`. Rows with empty / malformed / unparseable
    `date` pass through defensively — they may be undated events that
    legitimately have no date column. Mirrors events_state.gc_state's
    past-dated rule one-for-one.

    `today` is passed in (no dt.date.today() call inside the helper) so
    callers can freeze it for deterministic tests."""
    kept: list[dict] = []
    for row in rows:
        raw = (row.get("date") or "").strip()
        try:
            d = dt.date.fromisoformat(raw)
        except ValueError:
            kept.append(row)
            continue
        if d >= today:
            kept.append(row)
        # else: past-dated, drop
    return kept


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
    qs = urllib.parse.urlencode({"secret": secret, "kind": "ignored"})
    full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"
    try:
        with urllib.request.urlopen(full_url, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as err:  # noqa: BLE001
        print(f"sync_ignored_events: fetch failed — {err}", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(
            "sync_ignored_events: response was not a JSON list — keeping cache.",
            file=sys.stderr,
        )
        return None
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Apps Script /exec URL")
    ap.add_argument("--secret", required=True, help="IGNORE_READ_SECRET value")
    ap.add_argument("--out", required=True, help="path to ignored_events.json")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    rows = _fetch(args.url, args.secret, args.timeout)
    if rows is None:
        return 0  # graceful degrade; cache untouched

    normalized = normalize_rows(rows)
    today = dt.date.today()
    fresh = _drop_past_dated(normalized, today)
    dropped = len(normalized) - len(fresh)
    changed = write_if_changed(args.out, fresh)
    n = len(fresh)
    suffix = f" ({dropped} past-dated dropped)" if dropped else ""
    if changed:
        print(f"Synced {n} ignored event(s) to {args.out}{suffix}.")
    else:
        print(f"No changes ({n} ignored event(s) on disk){suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
