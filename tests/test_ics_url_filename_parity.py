"""Parity tests for the rendered `.ics` button URL and the files
`write_ics_files` actually writes to disk.

Three pieces must stay in lockstep for the per-event Add-to-calendar
button to work on Pages:

1. `main.py::step4_process_events` passes `--ics-out-dir docs/ics/`
   to `process_events.py` so the files land under the artifact root.
2. `process_events.write_ics_files(events, out_dir)` writes
   `{out_dir}/{event_id}.ics` per dated event.
3. `process_events.render_html(..., pages_url=X)` emits the href
   `https://{webcal_base}ics/{event_id}.ics`.

`build_ics` and `write_ics_files` already have direct unit tests, and
the rendered-href shape has a targeted HTML test. What was missing —
and what this file adds — is the cross-cutting parity: renderer's
path segment ↔ main.py's ics-out-dir ↔ writer's filename scheme.

These tests avoid calling `render_html` so they run cross-platform;
the renderer has a `%-d` strftime that is POSIX-only and not related
to .ics parity. The renderer's href format is pinned via source
inspection (regex over the process_events.py text) instead.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import process_events as pe  # noqa: E402


# The URL path segment between `webcal_base` and `<id>.ics`. Pinned
# here so a rename in one place (writer out-dir name or render href
# format string) immediately breaks parity.
ICS_PATH_SEGMENT = "ics"
ICS_NOW = dt.datetime(2026, 4, 14, 12, 0, tzinfo=dt.timezone.utc)


def _process_events_source() -> str:
    return (SCRIPTS_DIR / "process_events.py").read_text(encoding="utf-8")


# ── render-side href format string ──────────────────────────────────────


def test_render_href_format_matches_writer_filename_scheme():
    """The renderer builds the .ics href as
    `f"https://{webcal_base}ics/{ev['id']}.ics"`. Pin the literal
    `ics/` segment and the `{ev['id']}.ics` basename so a drift
    (e.g. `calendar/`, `{ev['id']}.vcs`, `{ev['slug']}.ics`) is caught
    at test time instead of on Pages."""
    source = _process_events_source()
    # Match any whitespace in the f-string; the literal parts are what
    # matter for parity.
    pattern = (
        r'f"https://\{webcal_base\}'
        + re.escape(ICS_PATH_SEGMENT)
        + r'/\{ev\[\'id\'\]\}\.ics"'
    )
    assert re.search(pattern, source), (
        f"Could not find the expected .ics href format in "
        f"process_events.py. The renderer's f-string must be "
        f"`f\"https://{{webcal_base}}{ICS_PATH_SEGMENT}/{{ev['id']}}.ics\"` "
        f"to stay parity with the writer's {{id}}.ics filename and "
        f"main.py's --ics-out-dir."
    )


def test_only_one_ics_href_format_in_render():
    """Defense-in-depth: exactly one `.ics` href-format string exists
    in the renderer. A leftover copy from a refactor — pointing at a
    different path segment or filename — would render for some events
    and 404 for others."""
    source = _process_events_source()
    matches = re.findall(r'f"https://\{webcal_base\}[^"]*\.ics"', source)
    assert len(matches) == 1, (
        f"Expected exactly one .ics href f-string, found {len(matches)}: "
        f"{matches!r}"
    )


# ── main.py → process_events.py --ics-out-dir wiring ────────────────────


def test_main_passes_ics_out_dir_with_matching_segment():
    """main.py::step4_process_events passes
    `--ics-out-dir {PAGES_OUTPUT_DIR}/ics` to process_events.py. The
    final path segment MUST equal the renderer's hardcoded segment.
    This reads main.py as text so the test catches a rename of the
    subdir without needing to run the whole orchestrator."""
    main_source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    # os.path.join(PAGES_OUTPUT_DIR, "ics") — pin the literal second
    # argument. The literal string is what must equal ICS_PATH_SEGMENT.
    m = re.search(
        r'os\.path\.join\(\s*PAGES_OUTPUT_DIR\s*,\s*"([^"]+)"\s*\)',
        main_source,
    )
    assert m is not None, (
        "main.py no longer constructs the ics out-dir as "
        "os.path.join(PAGES_OUTPUT_DIR, \"<segment>\"). If the "
        "expression shape changed, update this test — but make sure "
        "the new shape still produces a last-segment equal to the "
        "renderer's hardcoded `ics`."
    )
    assert m.group(1) == ICS_PATH_SEGMENT, (
        f"main.py passes --ics-out-dir ending in {m.group(1)!r} but the "
        f"renderer hardcodes {ICS_PATH_SEGMENT!r}. These must match or "
        f"the Add-to-calendar button 404s on Pages."
    )


# ── writer-side filename scheme ─────────────────────────────────────────


def test_writer_filename_is_event_id_dot_ics(tmp_path):
    """Writer filename is `{event_id}.ics`. Direct check against
    write_ics_files output — no fixture, so any platform-specific
    render quirk is out of scope."""
    events = [
        {
            "id": "",  # let build_ics compute the deterministic id
            "name": "Spring Concert",
            "date": "2026-04-23",
            "time": "7:00 PM",
            "child": "Isla",
            "location": "",
            "category": "School Activity",
            "source": "",
        },
        {
            "id": "",
            "name": "Book Report Due",
            "date": "2026-05-01",
            "time": "",
            "child": "",
            "location": "",
            "category": "Academic",
            "source": "",
        },
    ]
    # Stamp ids the way main.py does (via events_state.stamp_event_ids
    # upstream) — here we can derive them directly from _event_id.
    for ev in events:
        ev["id"] = pe._event_id(ev["name"], ev["date"], ev.get("child", ""))

    out_dir = tmp_path / ICS_PATH_SEGMENT
    out_dir.mkdir()
    count = pe.write_ics_files(events, str(out_dir), now=ICS_NOW)

    assert count == len(events)
    written = sorted(p.name for p in out_dir.iterdir())
    expected = sorted(f"{ev['id']}.ics" for ev in events)
    assert written == expected, (
        f"Writer filename drifted. Got {written!r}, expected {expected!r}. "
        f"The renderer points at `<id>.ics` — anything else 404s."
    )
    # Every stem is the 12-char sha1 prefix.
    for name in written:
        stem = name[:-len(".ics")]
        assert re.fullmatch(r"[0-9a-f]{12}", stem), (
            f"Unexpected .ics filename shape: {name!r}."
        )


# ── cross-cutting synthesis ─────────────────────────────────────────────


def test_render_format_and_writer_scheme_agree_on_id_basename(tmp_path):
    """Cross-cut: run the writer, simulate what the renderer would
    emit for the same event, and assert the href's on-disk layout
    matches the file that was actually written."""
    event = {
        "id": pe._event_id("Spring Concert", "2026-04-23", "Isla"),
        "name": "Spring Concert",
        "date": "2026-04-23",
        "time": "7:00 PM",
        "child": "Isla",
        "location": "",
        "category": "School Activity",
        "source": "",
    }
    out_dir = tmp_path / ICS_PATH_SEGMENT
    out_dir.mkdir()
    pe.write_ics_files([event], str(out_dir), now=ICS_NOW)

    webcal_base = pe._webcal_base("https://host.example/path/")
    # Simulate what the renderer would emit for this event.
    simulated_href = f"https://{webcal_base}{ICS_PATH_SEGMENT}/{event['id']}.ics"
    # Extract the last two path segments; they must equal the writer's
    # on-disk layout.
    tail = simulated_href.split(webcal_base, 1)[1]
    assert tail == f"{ICS_PATH_SEGMENT}/{event['id']}.ics"
    # And the file that tail points at must actually exist.
    assert (out_dir / f"{event['id']}.ics").is_file()
