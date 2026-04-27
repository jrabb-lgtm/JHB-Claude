/**
 * Joe Homebuyer Daily List — Apps Script Web App
 *
 * SETUP INSTRUCTIONS
 * ──────────────────
 * 1. Open your Google Sheet:
 *    https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit
 * 2. Extensions → Apps Script
 * 3. Paste this entire file into the editor (replace any existing code)
 * 4. Click the floppy-disk Save icon (or Ctrl+S)
 * 5. Click Deploy → New Deployment
 *    - Type: Web app
 *    - Execute as: Me
 *    - Who has access: Anyone  (the APPS_SCRIPT_SECRET acts as auth)
 * 6. Copy the deployment URL → set as APPS_SCRIPT_URL env var on the Mac Mini
 * 7. Pick a strong random secret → set as APPS_SCRIPT_SECRET env var on both sides
 *
 * ACTIONS HANDLED
 * ───────────────
 *   create_tab     — create a dated sheet tab + write headers
 *   clear_tab      — clear all data rows (keep header) from a named tab
 *   append_rows    — append data rows to a named tab
 *   append_skipped — append a row to the "Skipped" tab (audit trail)
 *   send_email     — send a plain-text summary email via MailApp
 *
 * SECURITY
 * ────────
 * Every request must include { "secret": "YOUR_SECRET_HERE" }.
 * Requests with a missing or wrong secret return HTTP 403.
 */

// ─── Configuration ──────────────────────────────────────────────────────────
var SPREADSHEET_ID    = "1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw";
var SECRET            = PropertiesService.getScriptProperties().getProperty("APPS_SCRIPT_SECRET") || "";
var SEEN_FC_TAB_NAME  = "Seen Foreclosures";
var SEEN_FC_HEADERS   = ["address_key", "pull_date", "address"];

// Column headers for the main daily-list tabs (42 columns, A–AP)
var MAIN_HEADERS = [
  "Pull Date","File Date","Lead First Name","Lead Last Name","Lead Owner Name",
  "Lead Status","Lead Street","Lead City","Lead State","Lead Zip","Lead Notes",
  "Customer Street","Customer City","Customer State","Customer Zip","Sales Date",
  "Probate Type","Probate Case #","Tax Foreclosure","Preforeclosure Case","Auction Date",
  "Campaign Lists","Lead Record Type","Owner 1 Phone","Owner 1 Deceased","Owner 1 Notes",
  "Relative 1 Name","Relative 1 Mailing Street","Relative 1 Mailing City",
  "Relative 1 Mailing State","Relative 1 Mailing Zip","Relative 1 Phone",
  "Relative 1 CNAM","Relative 1 Email","Relative 1 Relationship","Relative 1 Notes",
  "Relative 2 Name","Relative 2 Mailing Street","Relative 2 Mailing City",
  "Relative 2 Mailing State","Relative 2 Mailing Zip","Relative 2 Phone",
  "Relative 2 Email"
];

var SKIPPED_HEADERS = ["Pull Date","Case Number","Case Type","Owner","Reason","File Date"];
var NO_IMAGE_HEADERS = ["Pull Date","File Date","Case Number","County","Notes"];

// ─── Entry point ─────────────────────────────────────────────────────────────

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);

    // ── Auth check ──────────────────────────────────────────────────────────
    if (SECRET && payload.secret !== SECRET) {
      return jsonResponse({ status: "error", message: "Unauthorized" }, 403);
    }

    var action = payload.action;
    var ss     = SpreadsheetApp.openById(SPREADSHEET_ID);

    // ── Dispatch ────────────────────────────────────────────────────────────
    if (action === "create_tab") {
      return handleCreateTab(ss, payload);
    }
    if (action === "clear_tab") {
      return handleClearTab(ss, payload);
    }
    if (action === "append_rows") {
      return handleAppendRows(ss, payload);
    }
    if (action === "append_skipped") {
      return handleAppendSkipped(ss, payload);
    }
    if (action === "send_email") {
      return handleSendEmail(payload);
    }
    if (action === "get_seen_foreclosures") {
      return handleGetSeenForeclosures(ss);
    }
    if (action === "append_seen_foreclosures") {
      return handleAppendSeenForeclosures(ss, payload);
    }
    if (action === "get_fc_marker") {
      return handleGetFcMarker(ss);
    }
    if (action === "set_fc_marker") {
      return handleSetFcMarker(ss, payload);
    }
    if (action === "get_all_case_numbers") {
      return handleGetAllCaseNumbers(ss);
    }
    if (action === "get_no_images_rows") {
      return handleGetNoImagesRows(ss);
    }
    if (action === "prune_no_images") {
      return handlePruneNoImages(ss, payload);
    }

    return jsonResponse({ status: "error", message: "Unknown action: " + action });

  } catch (err) {
    return jsonResponse({ status: "error", message: err.toString() });
  }
}

// Allow GET for quick health-check (no secret required)
function doGet(e) {
  return ContentService.createTextOutput(
    JSON.stringify({ status: "ok", message: "Daily List web app is running" })
  ).setMimeType(ContentService.MimeType.JSON);
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/**
 * Create a new sheet tab (if it doesn't exist) and write headers to row 1.
 * Payload: { tabName: "04/20/2026" }
 */
function handleCreateTab(ss, payload) {
  var tabName = payload.tabName;
  if (!tabName) {
    return jsonResponse({ status: "error", message: "tabName is required" });
  }

  var existing = ss.getSheets().find(function(s) { return s.getName() === tabName; });
  if (existing) {
    return jsonResponse({ status: "ok", message: "Tab already exists: " + tabName });
  }

  // Insert at position 0 (leftmost) so today's tab is first
  var sheet = ss.insertSheet(tabName, 0);
  sheet.getRange(1, 1, 1, MAIN_HEADERS.length).setValues([MAIN_HEADERS]);

  // Freeze header row, bold it, and set column widths for readability
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, MAIN_HEADERS.length).setFontWeight("bold");

  // Format zip code columns as plain text so leading zeros (e.g. 02771) are preserved.
  // Google Sheets auto-converts numeric-looking strings to numbers, stripping leading zeros.
  // Columns (1-based): J=10 (Lead Zip), O=15 (Customer Zip),
  //                    AE=31 (Rel1 Mailing Zip), AO=41 (Rel2 Mailing Zip)
  var ZIP_COLS = [10, 15, 31, 41];
  ZIP_COLS.forEach(function(col) {
    sheet.getRange(2, col, sheet.getMaxRows() - 1, 1).setNumberFormat('@');
  });

  // Auto-resize first ~10 columns
  for (var c = 1; c <= Math.min(10, MAIN_HEADERS.length); c++) {
    sheet.autoResizeColumn(c);
  }

  SpreadsheetApp.flush();
  return jsonResponse({ status: "ok", message: "Created tab: " + tabName });
}

/**
 * Clear all data rows (row 2 onward) from a named tab, keeping the header.
 * Payload: { tabName: "04/20/2026" }
 */
function handleClearTab(ss, payload) {
  var tabName = payload.tabName;
  if (!tabName) {
    return jsonResponse({ status: "error", message: "tabName is required" });
  }

  var sheet = ss.getSheets().find(function(s) { return s.getName() === tabName; });
  if (!sheet) {
    return jsonResponse({ status: "ok", message: "Tab not found (nothing to clear): " + tabName });
  }

  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.getRange(2, 1, lastRow - 1, sheet.getLastColumn()).clearContent();
    SpreadsheetApp.flush();
    return jsonResponse({ status: "ok", message: "Cleared " + (lastRow - 1) + " rows from " + tabName });
  }

  return jsonResponse({ status: "ok", message: "Tab already empty: " + tabName });
}

/**
 * Append rows to a named tab.
 * Payload: { tabName: "04/20/2026", rows: [[col1, col2, ...], ...] }
 *
 * - Creates the tab + headers if it doesn't exist yet.
 * - Writes in one batch for efficiency.
 * - Handles "No Images" tab (creates with its own headers on first use).
 */
function handleAppendRows(ss, payload) {
  var tabName = payload.tabName;
  var rows    = payload.rows;

  if (!tabName || !rows || !rows.length) {
    return jsonResponse({ status: "ok", message: "Nothing to append" });
  }

  var sheet = ss.getSheets().find(function(s) { return s.getName() === tabName; });

  if (!sheet) {
    // Auto-create the tab if it wasn't pre-created
    sheet = ss.insertSheet(tabName, 0);
    var headers = (tabName === "No Images") ? NO_IMAGE_HEADERS : MAIN_HEADERS;
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold");
    SpreadsheetApp.flush();
  }

  var lastRow = sheet.getLastRow();
  var numCols = MAIN_HEADERS.length;

  // Pad / trim each row to exactly numCols columns
  var normalized = rows.map(function(row) {
    var r = row.slice(0, numCols);
    while (r.length < numCols) r.push("");
    return r;
  });

  sheet.getRange(lastRow + 1, 1, normalized.length, numCols).setValues(normalized);
  SpreadsheetApp.flush();

  return jsonResponse({ status: "ok", message: "Appended " + normalized.length + " rows to " + tabName });
}

/**
 * Append a row to the persistent "Skipped" audit tab.
 * Payload: { row: [caseNum, caseType, owner, reason, fileDate] }
 */
function handleAppendSkipped(ss, payload) {
  var row     = payload.row || [];
  var tabName = "Skipped";

  var sheet = ss.getSheets().find(function(s) { return s.getName() === tabName; });
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
    sheet.getRange(1, 1, 1, SKIPPED_HEADERS.length).setValues([SKIPPED_HEADERS]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, SKIPPED_HEADERS.length).setFontWeight("bold");
  }

  // Prepend today's date as first column
  var today = Utilities.formatDate(new Date(), "America/New_York", "MM/dd/yyyy");
  var fullRow = [today].concat(row);

  var lastRow = sheet.getLastRow();
  sheet.getRange(lastRow + 1, 1, 1, fullRow.length).setValues([fullRow]);
  SpreadsheetApp.flush();

  return jsonResponse({ status: "ok", message: "Appended skipped row" });
}

/**
 * Send a plain-text summary email.
 * Payload: { to: "email@example.com", subject: "...", body: "..." }
 */
function handleSendEmail(payload) {
  var to      = payload.to      || "";
  var subject = payload.subject || "Daily List Run Complete";
  var body    = payload.body    || "";

  if (!to) {
    return jsonResponse({ status: "error", message: "Recipient 'to' is required" });
  }

  MailApp.sendEmail({
    to:      to,
    subject: subject,
    body:    body,
  });

  return jsonResponse({ status: "ok", message: "Email sent to " + to });
}

/**
 * Return all rows from the "Seen Foreclosures" tab as an array of objects.
 * Used by the Python script on startup to seed the dedup set.
 * Response: { status: "ok", rows: [{address_key, pull_date, address}, ...] }
 */
function handleGetSeenForeclosures(ss) {
  var sheet = ss.getSheets().find(function(s) { return s.getName() === SEEN_FC_TAB_NAME; });
  if (!sheet || sheet.getLastRow() < 2) {
    return jsonResponse({ status: "ok", rows: [] });
  }
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, 3).getValues();
  var rows = data.map(function(r) {
    return { address_key: r[0], pull_date: r[1], address: r[2] };
  });
  return jsonResponse({ status: "ok", rows: rows });
}

/**
 * Append new entries to the permanent "Seen Foreclosures" tab.
 * Auto-creates the tab with headers if it doesn't exist.
 * Payload: { rows: [[address_key, pull_date, address], ...] }
 */
function handleAppendSeenForeclosures(ss, payload) {
  var rows = payload.rows || [];
  if (!rows.length) {
    return jsonResponse({ status: "ok", message: "Nothing to append" });
  }

  var sheet = ss.getSheets().find(function(s) { return s.getName() === SEEN_FC_TAB_NAME; });
  if (!sheet) {
    sheet = ss.insertSheet(SEEN_FC_TAB_NAME);
    sheet.getRange(1, 1, 1, SEEN_FC_HEADERS.length).setValues([SEEN_FC_HEADERS]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, SEEN_FC_HEADERS.length).setFontWeight("bold");
    SpreadsheetApp.flush();
  }

  var lastRow = sheet.getLastRow();
  sheet.getRange(lastRow + 1, 1, rows.length, 3).setValues(rows);
  SpreadsheetApp.flush();
  return jsonResponse({ status: "ok", message: "Appended " + rows.length + " seen foreclosure entries" });
}

/**
 * Return the marker text stored in the "Last Run Foreclosure" tab (cell A2).
 * The Python script uses this to know where to stop paginating on the next run.
 * Response: { status: "ok", marker: "Boston Globe, The Tuesday, April 21 2026 ..." }
 */
function handleGetFcMarker(ss) {
  var sheet = ss.getSheets().find(function(s) { return s.getName() === "Last Run Foreclosure"; });
  if (!sheet || sheet.getLastRow() < 2) {
    return jsonResponse({ status: "ok", marker: "" });
  }
  var marker = sheet.getRange(2, 1).getValue().toString().trim();
  return jsonResponse({ status: "ok", marker: marker });
}

/**
 * Overwrite the "Last Run Foreclosure" tab with a new marker text (cell A2).
 * Called at the end of each successful FC run. User can manually clear row 2
 * to force the next run to do a full scan.
 * Payload: { marker: "Boston Globe, The Tuesday, April 21 2026 ..." }
 */
function handleSetFcMarker(ss, payload) {
  var marker = (payload.marker || "").toString().trim();
  var tabName = "Last Run Foreclosure";

  var sheet = ss.getSheets().find(function(s) { return s.getName() === tabName; });
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
    sheet.getRange(1, 1).setValue("last_run_first_row");
    sheet.getRange(1, 1).setFontWeight("bold");
    sheet.setFrozenRows(1);
  }

  // Always overwrite A2 — one cell, one marker
  sheet.getRange(2, 1).setValue(marker);
  SpreadsheetApp.flush();
  return jsonResponse({ status: "ok", message: "FC marker saved" });
}

/**
 * Return all case numbers from every dated daily-list tab (MM/DD/YYYY format).
 * Reads three columns:
 *   K (col 11) Lead Notes         -- "26 SM 001234 - Servicemembers"
 *   R (col 18) Probate Case #     -- "WO26P1234EA"
 *   S (col 19) Tax Foreclosure    -- "26 TL 001234"
 * The sweep agent uses this to skip cases already in the sheet.
 * Response: { status: "ok", case_numbers: ["WO26P1234EA", "26 SM 001234 - Servicemembers", ...] }
 */
function handleGetAllCaseNumbers(ss) {
  var caseNums = [];
  var datePattern = /^\d{2}\/\d{2}\/\d{4}$/;
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var s = sheets[i];
    if (!datePattern.test(s.getName())) continue;
    var lastRow = s.getLastRow();
    if (lastRow < 2) continue;
    var numRows = lastRow - 1;
    var kVals = s.getRange(2, 11, numRows, 1).getValues();  // K = Lead Notes
    var rVals = s.getRange(2, 18, numRows, 1).getValues();  // R = Probate Case #
    var sVals = s.getRange(2, 19, numRows, 1).getValues();  // S = Tax Foreclosure
    for (var j = 0; j < numRows; j++) {
      var k = (kVals[j][0] || "").toString().trim();
      var r = (rVals[j][0] || "").toString().trim();
      var sv = (sVals[j][0] || "").toString().trim();
      if (k)  caseNums.push(k);
      if (r)  caseNums.push(r);
      if (sv) caseNums.push(sv);
    }
  }
  return jsonResponse({ status: "ok", case_numbers: caseNums });
}

/**
 * Return all rows from the "No Images" tab as an array of objects.
 * Response: { status: "ok", rows: [{pull_date, file_date, case_num, county, notes}, ...] }
 */
function handleGetNoImagesRows(ss) {
  var sheet = getSheet(ss, "No Images");
  if (!sheet || sheet.getLastRow() < 2) {
    return jsonResponse({ status: "ok", rows: [] });
  }
  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(2, 1, lastRow - 1, 5).getValues();
  var rows = [];
  for (var i = 0; i < data.length; i++) {
    rows.push({
      pull_date:  data[i][0] ? data[i][0].toString().trim() : "",
      file_date:  data[i][1] ? data[i][1].toString().trim() : "",
      case_num:   data[i][2] ? data[i][2].toString().trim() : "",
      county:     data[i][3] ? data[i][3].toString().trim() : "",
      notes:      data[i][4] ? data[i][4].toString().trim() : ""
    });
  }
  return jsonResponse({ status: "ok", rows: rows });
}

/**
 * Prune the "No Images" tab:
 *   - Delete rows where pull_date is before cutoff_date (format MM/DD/YYYY)
 *   - Delete rows whose case_num appears in resolved_cases list
 * Payload: { cutoff_date: "MM/DD/YYYY", resolved_cases: ["WO26P1234EA", ...] }
 */
function handlePruneNoImages(ss, payload) {
  var sheet = getSheet(ss, "No Images");
  if (!sheet || sheet.getLastRow() < 2) {
    return jsonResponse({ status: "ok", message: "No Images tab empty or missing" });
  }

  var cutoffStr     = payload.cutoff_date  || "";
  var resolvedSet   = {};
  var resolvedArr   = payload.resolved_cases || [];
  for (var i = 0; i < resolvedArr.length; i++) {
    resolvedSet[resolvedArr[i].toString().trim()] = true;
  }

  // Parse cutoff date
  var cutoffDate = cutoffStr ? new Date(cutoffStr) : null;

  // Collect row indices to delete (1-based, descending so deletion doesn't shift indices)
  var lastRow = sheet.getLastRow();
  var toDelete = [];
  for (var r = 2; r <= lastRow; r++) {
    var pullDateVal = sheet.getRange(r, 1).getValue();
    var caseNumVal  = sheet.getRange(r, 3).getValue().toString().trim();

    var deleteRow = false;

    // Delete if resolved
    if (resolvedSet[caseNumVal]) {
      deleteRow = true;
    }

    // Delete if older than cutoff
    if (!deleteRow && cutoffDate && pullDateVal) {
      var rowDate = pullDateVal instanceof Date ? pullDateVal : new Date(pullDateVal);
      if (!isNaN(rowDate) && rowDate < cutoffDate) {
        deleteRow = true;
      }
    }

    if (deleteRow) {
      toDelete.push(r);
    }
  }

  // Delete from bottom to top
  for (var d = toDelete.length - 1; d >= 0; d--) {
    sheet.deleteRow(toDelete[d]);
  }

  SpreadsheetApp.flush();
  return jsonResponse({ status: "ok", message: "Pruned " + toDelete.length + " rows from No Images tab" });
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function getSheet(ss, name) {
  return ss.getSheets().find(function(s) { return s.getName() === name; }) || null;
}

function jsonResponse(data, httpCode) {
  var output = ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
  // Apps Script doPost can't set HTTP status codes — always returns 200.
  // The Python script checks { "status": "ok" } in the JSON body instead.
  return output;
}

// ─── One-time setup helper (run manually from the Apps Script editor) ─────────

/**
 * Run this function ONCE from the Apps Script editor to store the shared secret
 * in Script Properties (more secure than hardcoding it in the code).
 *
 * Steps:
 *   1. Replace YOUR_SECRET_HERE with your actual secret
 *   2. Select this function from the Run dropdown
 *   3. Click Run
 *   4. Delete or comment out the function afterward
 */
function storeSecret() {
  PropertiesService.getScriptProperties().setProperty(
    "APPS_SCRIPT_SECRET", "YOUR_SECRET_HERE"
  );
  Logger.log("Secret stored in Script Properties.");
}
