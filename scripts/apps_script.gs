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
// Two endpoints:
//   POST /exec       — called by the static page's Ignore button. Public.
//                      Body: {"id": "<12-hex>", "name": "...", "date": "..."}
//   GET  /exec?secret=...  — called by the weekly GitHub Actions workflow.
//                            Returns the current ignore list as JSON.

const SHEET_NAME = 'Ignored Events';
const READ_SECRET = 'REPLACE_ME_WITH_RANDOM_STRING';

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);
    const id = String(payload.id || '').trim();
    if (!/^[a-f0-9]{12}$/.test(id)) {
      return _text('bad id');
    }
    const name = String(payload.name || '').slice(0, 200);
    const date = String(payload.date || '').slice(0, 20);
    const sheet = _getSheet();
    sheet.appendRow([new Date().toISOString(), id, name, date]);
    return _text('ok');
  } catch (err) {
    return _text('err: ' + err.message);
  }
}

function doGet(e) {
  if (!e.parameter || e.parameter.secret !== READ_SECRET) {
    return _text('unauthorized');
  }
  const sheet = _getSheet();
  const data = sheet.getDataRange().getValues();
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
  const out = Object.keys(seen).map(function (k) { return seen[k]; });
  return ContentService
      .createTextOutput(JSON.stringify(out))
      .setMimeType(ContentService.MimeType.JSON);
}

function _getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
}

function _text(s) {
  return ContentService
      .createTextOutput(s)
      .setMimeType(ContentService.MimeType.TEXT);
}
