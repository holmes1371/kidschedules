#!/usr/bin/env python3
"""Render the schedule HTML from a fixture file — no Gmail, no API calls.

Use this to iterate on the HTML/CSS output without hitting the Gmail API
or spending money on the Anthropic agent. It runs process_events.py with
a saved candidates fixture and writes the result to docs/dev_preview.html.

Usage:
    python scripts/dev_render.py
    python scripts/dev_render.py --fixture fixtures/last_run_candidates.json
    python scripts/dev_render.py --open    # open the output in your browser
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FIXTURE = os.path.join(PROJECT_ROOT, "fixtures", "sample_candidates.json")
PROCESS_EVENTS = os.path.join(PROJECT_ROOT, "scripts", "process_events.py")
OUTPUT_HTML = os.path.join(PROJECT_ROOT, "docs", "dev_preview.html")
OUTPUT_META = os.path.join(PROJECT_ROOT, "docs", "dev_preview-meta.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", default=DEFAULT_FIXTURE,
        help=f"Path to candidate events JSON (default: {DEFAULT_FIXTURE})."
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Open the rendered preview in your default browser."
    )
    args = parser.parse_args()

    if not os.path.exists(args.fixture):
        sys.stderr.write(f"ERROR: fixture not found: {args.fixture}\n")
        return 1

    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)

    cmd = [
        sys.executable, PROCESS_EVENTS,
        "--candidates", args.fixture,
        "--html-out", OUTPUT_HTML,
        "--meta-out", OUTPUT_META,
        "--display-window-days", "60",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode

    print(f"Wrote {OUTPUT_HTML}")
    if args.open:
        webbrowser.open(f"file://{OUTPUT_HTML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
