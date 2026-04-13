#!/usr/bin/env python3
"""Build an archive index page listing all past schedule files.

Scans the docs/ directory for files matching the pattern
YYYY-MM-DD.html, sorts them newest-first, and renders an
archive index page.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")


def find_archives(docs_dir: str) -> list[tuple[dt.date, str]]:
    """Return (date, filename) pairs sorted newest-first."""
    archives = []
    if not os.path.isdir(docs_dir):
        return archives
    for fname in os.listdir(docs_dir):
        m = DATE_PATTERN.match(fname)
        if m:
            try:
                d = dt.date.fromisoformat(m.group(1))
                archives.append((d, fname))
            except ValueError:
                pass
    archives.sort(key=lambda x: x[0], reverse=True)
    return archives


def render_archive_index(archives: list[tuple[dt.date, str]]) -> str:
    if not archives:
        rows = '    <p class="empty">No archived schedules yet.</p>'
    else:
        items = []
        for d, fname in archives:
            label = d.strftime("%B %-d, %Y")
            day = d.strftime("%A")
            items.append(
                f'    <a href="{fname}" class="archive-link">'
                f'<span class="date">{label}</span>'
                f'<span class="day">{day}</span></a>'
            )
        rows = "\n".join(items)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kids' Schedule — Archive</title>
  <style>
    :root {{
      --bg: #fafafa;
      --surface: #ffffff;
      --text: #202124;
      --text-secondary: #5f6368;
      --border: #e0e0e0;
      --accent: #1a73e8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                   "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    .header {{
      background: var(--accent);
      color: white;
      padding: 1.5rem 1rem;
      text-align: center;
    }}
    .header h1 {{ font-size: 1.5rem; font-weight: 600; }}
    .header .subtitle {{ font-size: 0.85rem; opacity: 0.85; margin-top: 0.25rem; }}
    .nav {{
      display: flex;
      justify-content: center;
      gap: 1.5rem;
      padding: 0.75rem 1rem;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      font-size: 0.85rem;
    }}
    .nav a {{ color: var(--accent); text-decoration: none; }}
    .nav a:hover {{ text-decoration: underline; }}
    .container {{
      max-width: 640px;
      margin: 1.5rem auto;
      padding: 0 1rem;
    }}
    .archive-link {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--surface);
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin-bottom: 0.5rem;
      box-shadow: 0 1px 2px rgba(0,0,0,0.06);
      text-decoration: none;
      color: var(--text);
      border-left: 4px solid var(--accent);
    }}
    .archive-link:hover {{
      background: #f0f4ff;
    }}
    .archive-link .date {{
      font-weight: 600;
    }}
    .archive-link .day {{
      color: var(--text-secondary);
      font-size: 0.85rem;
    }}
    .empty {{
      text-align: center;
      padding: 3rem 1rem;
      color: var(--text-secondary);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #1a1a1a;
        --surface: #2d2d2d;
        --text: #e8eaed;
        --text-secondary: #9aa0a6;
        --border: #3c4043;
        --accent: #8ab4f8;
      }}
      .archive-link:hover {{ background: #333; }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <h1>Kids' Schedule</h1>
    <div class="subtitle">Past Schedules</div>
  </div>
  <div class="nav">
    <a href="index.html">&larr; Current Schedule</a>
  </div>
  <div class="container">
{rows}
  </div>
</body>
</html>
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--docs-dir", required=True,
                   help="Path to the docs/ directory.")
    p.add_argument("--out", default=None,
                   help="Write archive index here (default: docs/archive.html).")
    args = p.parse_args()

    archives = find_archives(args.docs_dir)
    html = render_archive_index(archives)

    out_path = args.out or os.path.join(args.docs_dir, "archive.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Archive index: {len(archives)} entries → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
