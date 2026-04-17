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
// Two tabs in the same spreadsheet:
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
//
// GET  /exec?secret=... — read route. Gated by READ_SECRET.
//   (default) or ?kind=ignored          → Ignored Events JSON
//   ?kind=ignored_senders               → Ignored Senders JSON

const IGNORED_EVENTS_SHEET_NAME  = 'Ignored Events';
const IGNORED_SENDERS_SHEET_NAME = 'Ignored Senders';
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
    return _text('bad action');
  } catch (err) {
    return _text('err: ' + err.message);
  }
}

function doGet(e) {
  if (!e.parameter || e.parameter.secret !== READ_SECRET) {
    return _text('unauthorized');
  }
  const kind = String(e.parameter.kind || 'ignored');
  if (kind === 'ignored')         return _listIgnoredEvents();
  if (kind === 'ignored_senders') return _listIgnoredSenders();
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
