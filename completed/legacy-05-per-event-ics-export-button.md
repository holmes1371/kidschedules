# 5. Per-event `.ics` export button — 52ebd73 … cc7ac82

RFC 5545 generator in `scripts/process_events.py` (`build_ics`) plus an "Add to calendar" anchor on every dated card. Three-step path, each pivot driven by a concrete iOS testing failure the prior version couldn't solve:

1. **Blob download (superseded).** Inline `data-ics` attribute + JS Blob handler. iOS Safari/Edge/Chrome all route `.ics` downloads through Files and offer Reminders as the default handler — wrong app, two taps in the wrong place.
2. **Hosted files + `webcal://` (superseded).** Per-event files at `docs/ics/{event_id}.ics` linked via `webcal://<pages-host>/…`. Fixed the handler routing (Calendar opens directly), but `webcal://` is iOS's *subscription* scheme — tapping a link opens the "Subscribe to calendar?" sheet and adds a remote feed, rather than importing the single event.
3. **Hosted files + `https://` + range parsing (final).** Plain `https://` anchor to the same `.ics` files; GitHub Pages serves `Content-Type: text/calendar`, so iOS recognizes the MIME and opens Calendar's single-event preview sheet ("Add Event"), pre-populated, no subscription step. Added `_parse_time_range` for strings like "2PM - 5PM" so camp-style events emit a real `DURATION:PT3H` instead of degrading to all-day.

Architecture: `write_ics_files()` wipes and repopulates `docs/ics/` on every non-dry-run; `_webcal_base(pages_url)` strips the scheme and returns the host+path used to build the anchor href (name is historical — it's now the base for the https link); render gracefully omits the button when `pages_url` is empty (dev preview). UID stays `{event_id}@kidschedules.holmes1371.github.io` (opaque — UIDs don't need to resolve); timed events get `DTSTART;TZID=America/New_York` + real duration + a single hand-coded `VTIMEZONE`; all-day events get `VALUE=DATE` with exclusive `DTEND`. No workflow change needed — `docs/` is rebuilt each run and uploaded via `actions/upload-pages-artifact`.

Commit trail: 52ebd73 (design note + ROADMAP insert) · e0a8aa6 (`build_ics` + helpers + snapshots + parser tests) · 8c060b9 (UI card — Blob version, superseded) · 2082da8 (pivot to hosted files + webcal://, superseded) · 1135405 (time-range parsing) · cc7ac82 (webcal:// → https:// swap, final).

Design note: `design/ics-export.md` (includes both pivot rationales).
