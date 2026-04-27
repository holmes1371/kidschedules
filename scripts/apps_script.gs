// Kids Schedule — Ignore webhook (Google Apps Script)
//
// One-time setup (see README for full instructions):
//   1. Create a Google Sheet called "Kids Schedule — Ignored Events".
//   2. Extensions → Apps Script, paste this file in.
//   3. Replace READ_SECRET below with a random string.
//      Put the same string in your GitHub repo secret IGNORE_READ_SECRET.
//   4. Deploy → New deployment → Web app
//        Execute as: Me
//        Who has access: Anyone
//      Copy the /exec URL into ignore_webhook_url.txt in the repo.
//
// Three tabs in the same spreadsheet:
//   "Ignored Events"  — one row per (timestamp, id, name, date, sender) the
//                       user dismissed. `sender` was added in the unignore-
//                       sender pass; pre-existing rows carry only the first
//                       four columns. Legacy rows are left as-is — they can
//                       only be cleared by individual unignore-event. Since
//                       ROADMAP #20 the sender column stores a block
//                       identifier — either a bare registrable domain
//                       (institutional) or a full lowercased email address
//                       (freemail). The payload key remains "sender".
//   "Ignored Senders" — one row per (timestamp, domain, source) — either
//                       the UI Ignore-sender button (source="auto-button")
//                       or a hand-seeded row (source="manual" by convention).
//                       The script treats every row as authoritative; the
//                       source column is informational. The "domain" column
//                       name is historical — since ROADMAP #20 it carries
//                       block identifiers (domain or full address).
//   "Completed Events" — one row per (timestamp, id, name, date) the user
//                       has marked complete via the schedule page checkbox
//                       (ROADMAP #32). Auto-created on first append; no
//                       sender column since completion is per-event with
//                       no "complete sender" sweep. The Sheet is the
//                       single source of truth — the cron job pulls
//                       these rows fresh into completed_events.json each
//                       run; that file is never committed.
//
// POST /exec        — action router. Body JSON: {"action": "...", ...}.
//   action="ignore" (also the default when action is absent — backward
//                   compat for the first-wave client):
//     {"id": "<12-hex>", "name": "...", "date": "...", "sender": "..."}
//     →  append to Ignored Events. `sender` is optional and stored only
//     when it validates as a block identifier (bare domain or full
//     address); invalid or missing values write as "".
//   action="unignore":
//     {"id": "<12-hex>"}  →  delete every Ignored Events row matching id.
//     Idempotent: returns 'ok' even if no row matched.
//   action="ignore_sender":
//     {"domain": "..."}  →  validate, lowercase, append to Ignored Senders
//     with source="auto-button". Wire-protocol key is "domain" for
//     backward compat; the value may be a bare domain or a full address.
//   action="unignore_sender":
//     {"domain": "..."}  →  delete every Ignored Senders row whose block
//     identifier matches AND every Ignored Events row where the sender
//     column matches. Match is lowercased exact equality on the whole
//     string, so an alice@gmail.com unignore does NOT match a gmail.com
//     row and vice versa (this is correct: address-level and domain-level
//     ignores unignore at the same level).
//     Idempotent: returns 'ok' even if no rows matched.
//   action="complete":  (ROADMAP #32)
//     {"id": "<12-hex>", "name": "...", "date": "..."}
//     →  append to Completed Events. Mirrors the "ignore" handler shape
//     minus the sender column. id is the 12-char sha1 event-id from the
//     rendered card.
//   action="uncomplete":  (ROADMAP #32)
//     {"id": "<12-hex>"}  →  delete every Completed Events row matching
//     id. Idempotent: returns 'ok' even if no row matched.
//
// GET  /exec — read route. The three list-shape kinds enumerated below
//   are unauthenticated (ROADMAP #34) so the schedule page's client-side
//   refresh sync can fetch them without exposing READ_SECRET in page JS.
//   Each row carries only (event_id, name, date) — the same metadata
//   already public on the rendered Pages page, so dropping the secret
//   on read costs nothing in confidentiality. Any kind NOT in this list
//   still requires `?secret=$READ_SECRET`.
//   (default) or ?kind=ignored          → Ignored Events JSON   (public)
//   ?kind=ignored_senders               → Ignored Senders JSON  (public)
//   ?kind=completed                     → Completed Events JSON (public, #32)

const IGNORED_EVENTS_SHEET_NAME   = 'Ignored Events';
const IGNORED_SENDERS_SHEET_NAME  = 'Ignored Senders';
const COMPLETED_EVENTS_SHEET_NAME = 'Completed Events';
const READ_SECRET = 'REPLACE_ME_WITH_RANDOM_STRING';

// Accepts either a bare registrable domain ("fcps.edu") or a full
// address ("alice@fcps.edu"). Strictly broader than the pre-#20
// DOMAIN_RE so first-wave clients that still send domain-only
// payloads continue to validate.
const SENDER_RE = /^(?:[^\s@]+@)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/;

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);
    const action = String(payload.action || 'ignore');
    if (action === 'ignore')          return _handleIgnore(payload);
    if (action === 'unignore')        return _handleUnignore(payload);
    if (action === 'ignore_sender')   return _handleIgnoreSender(payload);
    if (action === 'unignore_sender') return _handleUnignoreSender(payload);
    if (action === 'complete')        return _handleComplete(payload);
    if (action === 'uncomplete')      return _handleUncomplete(payload);
    return _text('bad action');
  } catch (err) {
    return _text('err: ' + err.message);
  }
}

function doGet(e) {
  // ROADMAP #34: the three list-shape reads are unauthenticated. Each
  // row is just (event_id, name, date) — the same metadata already
  // public on the rendered Pages page — so dropping the secret on read
  // costs nothing in confidentiality and lets the client-side refresh
  // sync (which can't carry a secret without leaking it in page JS)
  // pull state directly. POSTs and any future GET kinds keep the
  // existing secret gate; the early-allow only covers the specific
  // kinds enumerated below. Existing CI cron callers that still pass
  // `&secret=...` continue to work — the param is simply ignored
  // along the public path.
  const kind = String((e.parameter && e.parameter.kind) || 'ignored');
  const PUBLIC_KINDS = ['ignored', 'ignored_senders', 'completed'];
  if (PUBLIC_KINDS.indexOf(kind) === -1) {
    if (!e.parameter || e.parameter.secret !== READ_SECRET) {
      return _text('unauthorized');
    }
  }
  if (kind === 'ignored')         return _listIgnoredEvents();
  if (kind === 'ignored_senders') return _listIgnoredSenders();
  if (kind === 'completed')       return _listCompletedEvents();
  return _text('bad kind');
}

// ─── POST handlers ────────────────────────────────────────────────────────

function _handleIgnore(payload) {
  const id = String(payload.id || '').trim();
  if (!/^[a-f0-9]{12}$/.test(id)) return _text('bad id');
  const name = String(payload.name || '').slice(0, 200);
  const date = String(payload.date || '').slice(0, 20);
  const senderRaw = String(payload.sender || '').trim().toLowerCase();
  const sender = SENDER_RE.test(senderRaw) ? senderRaw : '';
  _getIgnoredEventsSheet().appendRow(
    [new Date().toISOString(), id, name, date, sender]
  );
  return _text('ok');
}

function _handleUnignore(payload) {
  const id = String(payload.id || '').trim();
  if (!/^[a-f0-9]{12}$/.test(id)) return _text('bad id');
  const sheet = _getIgnoredEventsSheet();
  const data = sheet.getDataRange().getValues();
  // Iterate bottom-up so row indices don't shift as we delete.
  // Sheet rows are 1-indexed; data array is 0-indexed.
  for (let i = data.length - 1; i >= 0; i--) {
    if (String(data[i][1] || '').trim() === id) {
      sheet.deleteRow(i + 1);
    }
  }
  return _text('ok');
}

function _handleIgnoreSender(payload) {
  // Payload key stays "domain" (wire-protocol compat); the value is a
  // block identifier — bare registrable domain or full address.
  const domain = String(payload.domain || '').trim().toLowerCase();
  if (!SENDER_RE.test(domain)) return _text('bad domain');
  _getIgnoredSendersSheet().appendRow(
    [new Date().toISOString(), domain, 'auto-button']
  );
  return _text('ok');
}

function _handleUnignoreSender(payload) {
  const domain = String(payload.domain || '').trim().toLowerCase();
  if (!SENDER_RE.test(domain)) return _text('bad domain');
  // Delete the domain row(s) from Ignored Senders.
  const sendersSheet = _getIgnoredSendersSheet();
  const sendersData = sendersSheet.getDataRange().getValues();
  for (let i = sendersData.length - 1; i >= 0; i--) {
    if (String(sendersData[i][1] || '').trim().toLowerCase() === domain) {
      sendersSheet.deleteRow(i + 1);
    }
  }
  // Bulk-delete Ignored Events rows tagged with this sender. Column 5
  // (index 4) holds the sender string; legacy 4-column rows return '' and
  // are skipped by design (see design/unignore-sender.md).
  const eventsSheet = _getIgnoredEventsSheet();
  const eventsData = eventsSheet.getDataRange().getValues();
  for (let i = eventsData.length - 1; i >= 0; i--) {
    if (String(eventsData[i][4] || '').trim().toLowerCase() === domain) {
      eventsSheet.deleteRow(i + 1);
    }
  }
  return _text('ok');
}

// ROADMAP #32 — Completed Events. Same id-validated append/delete shape
// as Ignored Events but with a four-column row (no sender). Sheet is the
// single source of truth; the cron job pulls these rows via doGet to
// regenerate completed_events.json each run.

function _handleComplete(payload) {
  const id = String(payload.id || '').trim();
  if (!/^[a-f0-9]{12}$/.test(id)) return _text('bad id');
  const name = String(payload.name || '').slice(0, 200);
  const date = String(payload.date || '').slice(0, 20);
  _getCompletedEventsSheet().appendRow(
    [new Date().toISOString(), id, name, date]
  );
  return _text('ok');
}

function _handleUncomplete(payload) {
  const id = String(payload.id || '').trim();
  if (!/^[a-f0-9]{12}$/.test(id)) return _text('bad id');
  const sheet = _getCompletedEventsSheet();
  const data = sheet.getDataRange().getValues();
  // Bottom-up to keep row indices stable across the loop.
  for (let i = data.length - 1; i >= 0; i--) {
    if (String(data[i][1] || '').trim() === id) {
      sheet.deleteRow(i + 1);
    }
  }
  return _text('ok');
}

// ─── GET handlers ─────────────────────────────────────────────────────────

function _listIgnoredEvents() {
  const data = _getIgnoredEventsSheet().getDataRange().getValues();
  const seen = {};
  for (let i = 0; i < data.length; i++) {
    const id = String(data[i][1] || '').trim();
    if (!/^[a-f0-9]{12}$/.test(id)) continue;
    if (!seen[id]) {
      seen[id] = {
        id: id,
        name: String(data[i][2] || ''),
        date: String(data[i][3] || ''),
        ignored_at: String(data[i][0] || '')
      };
    }
  }
  return _json(Object.keys(seen).map(function (k) { return seen[k]; }));
}

function _listIgnoredSenders() {
  const data = _getIgnoredSendersSheet().getDataRange().getValues();
  const seen = {};
  for (let i = 0; i < data.length; i++) {
    const domain = String(data[i][1] || '').trim().toLowerCase();
    if (!SENDER_RE.test(domain)) continue;
    if (!seen[domain]) {
      seen[domain] = {
        timestamp: String(data[i][0] || ''),
        domain: domain,
        source: String(data[i][2] || '')
      };
    }
  }
  return _json(Object.keys(seen).map(function (k) { return seen[k]; }));
}

function _listCompletedEvents() {
  const data = _getCompletedEventsSheet().getDataRange().getValues();
  const seen = {};
  for (let i = 0; i < data.length; i++) {
    const id = String(data[i][1] || '').trim();
    if (!/^[a-f0-9]{12}$/.test(id)) continue;
    if (!seen[id]) {
      seen[id] = {
        id: id,
        name: String(data[i][2] || ''),
        date: String(data[i][3] || ''),
        completed_at: String(data[i][0] || '')
      };
    }
  }
  return _json(Object.keys(seen).map(function (k) { return seen[k]; }));
}

// ─── helpers ──────────────────────────────────────────────────────────────

function _getIgnoredEventsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(IGNORED_EVENTS_SHEET_NAME)
      || ss.insertSheet(IGNORED_EVENTS_SHEET_NAME);
}

function _getIgnoredSendersSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(IGNORED_SENDERS_SHEET_NAME)
      || ss.insertSheet(IGNORED_SENDERS_SHEET_NAME);
}

function _getCompletedEventsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(COMPLETED_EVENTS_SHEET_NAME)
      || ss.insertSheet(COMPLETED_EVENTS_SHEET_NAME);
}

function _text(s) {
  return ContentService
      .createTextOutput(s)
      .setMimeType(ContentService.MimeType.TEXT);
}

function _json(obj) {
  return ContentService
      .createTextOutput(JSON.stringify(obj))
      .setMimeType(ContentService.MimeType.JSON);
}
