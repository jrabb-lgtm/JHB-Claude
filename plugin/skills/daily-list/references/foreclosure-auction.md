# Foreclosure Auction Workflow

Source: `https://www.masspublicnotices.org/`

These are properties where the foreclosure auction date has already been set and publicly noticed — the step **after** Pre-Foreclosure (Servicemembers). Real property only. Unlike probate and pre-foreclosure, this source is a newspaper notice aggregator, not a court system.

---

## Step 1 — Search

Navigate to `https://www.masspublicnotices.org/`. Wait for page load. If the welcome/CAPTCHA page appears, wait up to 10 seconds for the extension to auto-solve, then click "Click Here" to reach the search form:

```javascript
const link = Array.from(document.querySelectorAll('a')).find(a => a.textContent.trim() === 'Click Here');
if (link) link.click();
```

Set search fields and submit:

```javascript
document.getElementById('ctl00_ContentPlaceHolder1_as1_txtSearch').value = 'auction';
document.getElementById('ctl00_ContentPlaceHolder1_as1_txtDateFrom').value = 'FROM_DATE'; // from_date_str
document.getElementById('ctl00_ContentPlaceHolder1_as1_txtDateTo').value = 'TO_DATE';     // to_date_str
document.getElementById('ctl00_ContentPlaceHolder1_as1_btnGo1').click();
```

Wait 3 seconds for results.

⚠️ **When `fromDate ≠ toDate` (Mondays, Sundays, post-holiday):** The search covers multiple days, so expect more total results (multiply the typical ~50–70 per day by number of days in the range). Paginate through ALL pages as usual — the same deduplication logic via "Seen Foreclosures" handles any repeats.

---

## Step 2 — Collect qualifying notices from all pages

Results show 10 per page. VIEW buttons follow this ID pattern:
`ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctl03_btnView` through `ctl12_btnView`

Read snippet text for each row. **Include** if snippet contains:
- `MORTGAGEE'S NOTICE OF SALE`
- `MORTGAGEE'S SALE OF REAL ESTATE`
- `MORTGAGEE'S NOTICE OF SALE OF REAL ESTATE`
- `SALE OF REAL ESTATE` (with "Power of Sale" language)

**Skip** if snippet contains:
- `PODS`, `StorageTreasures`, `storage unit`, `self-storage` → storage auctions
- `Chapter 255 Section 17`, `F/V `, `vessel`, `boat` → maritime liens
- `motor vehicle`, `automobile`, `car auction` → vehicle auctions
- `UNITED STATES DISTRICT COURT` with admiralty context → admiralty sales

Also extract the owner name from each qualifying snippet using: `given by [NAME] to [BANK]` — save as ArcGIS fallback.

Paginate through all result pages:
```javascript
// After reading each page, find and click the next page button
const nextBtn = Array.from(document.querySelectorAll('input[type="submit"]'))
  .find(b => b.value === '>' || b.value === 'Next');
if (nextBtn) nextBtn.click();
```

Collect all qualifying VIEW button IDs before processing any individual notice.

---

## Data Storage — Accumulate to outputs folder

**Save each foreclosure entry immediately after processing it** — do not hold everything in memory. Use the outputs folder (not `/tmp/`) so data survives if a session context reset forces a new conversation.

```python
import csv, os, glob

# Find outputs folder dynamically (session name changes every session)
outputs_dir = glob.glob('/sessions/*/mnt/outputs')[0]
csv_path = os.path.join(outputs_dir, 'fc_MMDDYYYY.csv')  # replace MMDDYYYY with pull date

file_exists = os.path.exists(csv_path)
with open(csv_path, 'a', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'owner','ownerFirst','ownerLast','propStreet','propCity','propState','propZip',
        'auctionDate','ownAddr','ownCity','ownState','ownZip'
    ])
    if not file_exists:
        writer.writeheader()
    writer.writerow({
        'owner': owner, 'ownerFirst': first, 'ownerLast': last,
        'propStreet': propStreet, 'propCity': propCity, 'propState': 'MA', 'propZip': propZip,
        'auctionDate': auctionDate,
        'ownAddr': ownAddr, 'ownCity': ownCity, 'ownState': ownState, 'ownZip': ownZip
    })
print(f"Saved {owner} to {csv_path}")
```

At the start of the foreclosure phase, check if a CSV from today already exists — if so, skip notices already captured (resume support):
```python
import csv, glob, os
outputs_dir = glob.glob('/sessions/*/mnt/outputs')[0]
csv_path = os.path.join(outputs_dir, 'fc_MMDDYYYY.csv')
already_done = set()
if os.path.exists(csv_path):
    with open(csv_path) as f:
        already_done = {r['propStreet'] + r['propCity'] for r in csv.DictReader(f)}
print(f"Already have {len(already_done)} entries — will skip those")
```

---

## Cross-Run Deduplication — "Seen Foreclosures" Tab

**By law, foreclosure auction notices must be published 3 consecutive weeks.** This means every weekly run will encounter the same properties from the prior 1–2 weeks. Only upload a property the first time it appears.

The sheet has a persistent **"Seen Foreclosures"** tab (in the Daily List Claude spreadsheet) with columns:
- **A: Address Key** — `{street}|{city}|{zip}` (lowercase, normalized)
- **B: Pull Date** — date first seen (MM/DD/YYYY)
- **C: Property Address** — human-readable, for reference

### Step 0 — At the start of the foreclosure phase: load seen list + purge old entries

Run this Apps Script function before processing any notices:

```javascript
function loadAndPurgeSeenForeclosures() {
  var ss = SpreadsheetApp.openById("1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw");
  var sheet = ss.getSheetByName("Seen Foreclosures");
  if (!sheet) { Logger.log("Seen Foreclosures tab missing — create it first"); return; }

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) { Logger.log("Seen list is empty"); return; }

  var data = sheet.getRange(2, 1, lastRow - 1, 3).getValues();
  var cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 21); // purge entries older than 21 days

  var toKeep = data.filter(function(row) {
    var pullDate = new Date(row[1]);
    return pullDate >= cutoff;
  });

  var purgeCount = data.length - toKeep.length;
  sheet.getRange(2, 1, lastRow - 1, 3).clearContent();
  if (toKeep.length > 0) {
    sheet.getRange(2, 1, toKeep.length, 3).setValues(toKeep);
  }

  SpreadsheetApp.flush();
  Logger.log("Purged " + purgeCount + " old entries. " + toKeep.length + " remaining.");

  // Return seen keys as JSON string for use in Python
  var keys = toKeep.map(function(r) { return r[0]; });
  Logger.log("SEEN_KEYS:" + JSON.stringify(keys));
}
```

After running, read the `SEEN_KEYS:` line from the execution log and parse it in Python:
```python
# seen_keys comes from the Apps Script log output
seen = set(seen_keys)  # e.g. {"25 oak street|peabody|01960", ...}
print(f"Loaded {len(seen)} seen addresses")
```

### Per-notice deduplication check

After extracting property address from each notice, before doing any ArcGIS lookup:
```python
# Normalize the address key
key = f"{propStreet.strip().lower()}|{propCity.strip().lower()}|{propZip.strip()}"
if key in seen:
    print(f"SKIP (already seen): {propStreet}, {propCity}")
    # continue to next notice
else:
    seen.add(key)  # mark as seen for this run too
    # proceed with ArcGIS lookup and add to upload list
```

### After uploading new rows — append to seen tab

```javascript
function appendSeenForeclosures(newEntries) {
  // newEntries = array of [addressKey, pullDate, propertyAddress]
  var ss = SpreadsheetApp.openById("1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw");
  var sheet = ss.getSheetByName("Seen Foreclosures");
  var nextRow = sheet.getLastRow() + 1;
  sheet.getRange(nextRow, 1, newEntries.length, 3).setValues(newEntries);
  SpreadsheetApp.flush();
  Logger.log("Appended " + newEntries.length + " new entries to Seen Foreclosures");
}
```

Call it after writing to the daily sheet:
```javascript
// Build entries array from uploaded rows
var newEntries = uploadedRows.map(function(row) {
  var street = row[6]; // column G = Lead Street
  var city = row[7];   // column H = Lead City
  var zip = row[9];    // column J = Lead Zip
  var key = (street + '|' + city + '|' + zip).toLowerCase();
  return [key, "MM/DD/YYYY", street + ', ' + city + ' ' + zip];
});
appendSeenForeclosures(newEntries);
```

---

## Step 3 — Per-notice: read full text

### 3a. Click VIEW

```javascript
document.getElementById('ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctlXX_btnView').click();
// Replace ctlXX with ctl03–ctl12 for rows 1–10
```

Wait 3 seconds. Take a screenshot immediately to bring the page to the forefront.

### 3b. Handle reCAPTCHA (first notice only)

**The reCAPTCHA is per-session — once solved, all subsequent notices load freely.**

- Take screenshot to bring CAPTCHA to forefront
- Wait up to 15 seconds for extension to auto-solve
- If the extension does not solve it: tell the user **"Please click 'I'm not a robot' in Chrome"** — they only need to do this once per daily run
- After the CAPTCHA is solved, click "View Notice":

```javascript
Array.from(document.querySelectorAll('input[type="submit"], button'))
  .find(b => (b.value || b.textContent || '').trim() === 'View Notice')?.click();
```

All notices after the first skip the CAPTCHA entirely.

### 3c. Read notice text

Use `get_page_text`. Extract using these confirmed patterns (verified against live notices 04/17/2026):

**Owner name:**
> Pattern: `given by [NAME] to [BANK]`
> Example: "given by Paul R. Dacey II to Newburyport Five Cents Savings Bank"

**Auction date + time:**
> Pattern: `at public auction at [TIME], on [DATE] on the mortgaged premises`
> Example: "at public auction at 10:00 AM, on May 14, 2026 on the mortgaged premises"
> Also seen: "at public auction on [DATE] at [TIME]" — check for both orderings

**Property address:**
> Pattern: `being known as [ADDRESS], being all`
> Example: "being known as 17 Collins Street, Salisbury, MA"
> Also: "at or upon the mortgaged premises, [ADDRESS]"
> Also: "premises known as [ADDRESS]"

⚠️ **Truncated notices:** Some notices show "Web display limited to 1,000 characters." In that case, a PDF tab opens automatically. Switch to that tab (`tabId` of the PDFDocument.aspx tab) and use `get_page_text` there for the full text.

⚠️ **OCR hyphenation:** Notice text is converted from PDF and contains line-break hyphens (e.g. "mort-gage", "Newburyp-ort"). Strip these when parsing names and addresses.

### 3d. ArcGIS lookup

Use property address → ArcGIS LIKE query to get: `OWNER1`, `OWN_ADDR`, `OWN_CITY`, `OWN_STATE`, `OWN_ZIP`.

Same LIKE + zip method as probate workflow:
```javascript
const condition = `UPPER(SITE_ADDR) LIKE '${streetNumAndPrefix}%'`;
// Then filter by ZIP to disambiguate
```

### 3e. Navigate back

```javascript
history.back();
// Wait 2 seconds, then proceed to next notice
```

---

## Step 4 — Sheet columns

| Column | Field | Value |
|--------|-------|-------|
| A | Pull Date | Today's date |
| B | File Date | Notice publication date |
| C | Lead First Name | Owner first name |
| D | Lead Last Name | Owner last name |
| G | Lead Street | Property street |
| H | Lead City | Property city |
| I | Lead State | Property state |
| J | Lead Zip | Property zip |
| K | Lead Notes | "Foreclosure Auction" |
| S | Tax Foreclosure | "Yes" |
| T | **Auction Date** | e.g. "May 14, 2026 at 10:00 AM" |
| AA | Relative 1 Mailing Street | OWN_ADDR from ArcGIS |
| AB | Relative 1 Mailing City | OWN_CITY from ArcGIS |
| AC | Relative 1 Mailing State | OWN_STATE from ArcGIS |
| AD | Relative 1 Mailing Zip | OWN_ZIP from ArcGIS |

---

## Common Issues

| Problem | Fix |
|---------|-----|
| reCAPTCHA on first detail page | Take screenshot to bring to forefront. Wait 15s for extension. If not solved, ask user to click "I'm not a robot" — once per session only. |
| Property address not in "being known as" pattern | Also scan for "at or upon the mortgaged premises, [ADDRESS]", "premises known as [ADDRESS]", or "located at [ADDRESS]" |
| Auction date in different format | Also check: "on the [Nth] day of [Month], [YYYY]" or "on [Month] [D], [YYYY] at [TIME]" |
| Notice truncated to 1,000 chars | Switch to PDF tab (PDFDocument.aspx) that auto-opens alongside the detail page |
| Same property in multiple newspapers | Deduplicate by address — keep only the first occurrence |
| Results show 50–70 notices | Normal for Fridays. After filtering for "MORTGAGEE'S", expect ~10–20 real property auctions |
| history.back() loses search results | Re-run the search from scratch: set `txtSearch`, `txtDateFrom`, `txtDateTo`, click `btnGo1` |
| OCR strips zip code from address | Address line often reads "17 Collins Street, Salisbury, MA" with no zip — get zip from ArcGIS result |
