"""Fetch completed-event rows from the Apps Script web app and write them
to `completed_events.json` in the runner's working directory.

Companion to `sync_ignored_senders.py`. The Google Sheet "Completed Events"
tab is the single source of truth (ROADMAP #32); this helper is a thin
fetch+normalize+write layer so the rendering pipeline can read a fast
local cache instead of hitting Apps Script during the build itself.

The cache file is per-run ephemeral — the workflow writes it into the
runner's working directory before `main.py` executes, `process_events.py`
reads it via `--completed`, and the runner is torn down afterward.
`completed_events.json` is NEVER committed to the repo.

CLI:
    python scripts/sync_completed_events.py \\
        --url https://script.google.com/macros/s/.../exec \\
        --secret "$IGNORE_READ_SECRET" \\
        --out completed_events.json

Exit is always 0 on network / parse failure — the existing cache file
is left untouched so the pipeline degrades gracefully. Matches the
existing `sync_ignored_senders.py` posture.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

# Same shape as the apps_script.gs id-validation regex. Applied after
# trimming. The 12-hex form comes from sha1[:12] in events_state.py and
# scripts/process_events.py — both modules MUST hash identically (a parity
# test in tests/test_events_state.py pins this).
ID_RE = re.compile(r"^[a-f0-9]{12}$")


def normalize_rows(rows: list[dict]) -> list[dict]:
    """Trim id, drop invalid rows, dedup first-wins, sort by id.

    Apps Script already dedups on GET, so this is defensive against
    direct-sheet-read paths and against manual edits that slip in a
    trailing space or a casing variant. Mirrors normalize_rows in
    sync_ignored_senders.py one-for-one apart from the validation key.
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
            "completed_at": str(row.get("completed_at") or ""),
        }
    return [seen[k] for k in sorted(seen)]


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
    qs = urllib.parse.urlencode({"secret": secret, "kind": "completed"})
    full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"
    try:
        with urllib.request.urlopen(full_url, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as err:  # noqa: BLE001
        print(f"sync_completed_events: fetch failed — {err}", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(
            "sync_completed_events: response was not a JSON list — keeping cache.",
            file=sys.stderr,
        )
        return None
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Apps Script /exec URL")
    ap.add_argument("--secret", required=True, help="IGNORE_READ_SECRET value")
    ap.add_argument("--out", required=True, help="path to completed_events.json")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    rows = _fetch(args.url, args.secret, args.timeout)
    if rows is None:
        return 0  # graceful degrade; cache untouched

    normalized = normalize_rows(rows)
    changed = write_if_changed(args.out, normalized)
    n = len(normalized)
    if changed:
        print(f"Synced {n} completed event(s) to {args.out}.")
    else:
        print(f"No changes ({n} completed event(s) on disk).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
