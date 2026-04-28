---
name: daily-list
description: |
  Trigger this skill for any of these phrases:
  - "run the daily list", "do the daily list", "run the lists" → runs all counties (BA, BR, DU, ES, FR, MI, NA, NO, PL, SU, WO) plus Pre-Foreclosure and Tax Lien in a single agent
---

# Daily List

**Run directly in the current conversation — do NOT spawn subagents or use the Agent tool.** All browser tools (`switch_browser`, `javascript_tool`, `navigate`, `computer`, `get_page_text`, `read_page`) must be called directly from this conversation. Spawning a subagent loses browser context and forces a reconnect from scratch every time.

## Computer Use Policy

**Computer use is allowed ONLY during canvas screenshot reads** (Step 4d of the per-county loop). All other phases use `javascript_tool`, `navigate`, `get_page_text`, `read_page`, and Apps Script only. Never screenshot-and-click for navigation or sheet operations.

---

## How to run

### Trigger: "run the daily list"

1. Calculate dates (see Date Logic)
2. Connect to Chrome (`switch_browser`) — tell user: "🔵 Connecting — click Connect in Chrome"
3. Set up the Google Sheet tab for today (see Sheet Setup)
4. **For each county (BA, BR, DU, ES, FR, MI, NA, NO, PL, SU, WO) — single pass:**
   - Navigate to county results page (EA + file date search via Wicket double-hop)
   - Collect ALL case hrefs upfront (including pagination) before processing any case
   - For each qualifying case: navigate to case → render PDF canvas → screenshot → extract data → **immediately append row to CSV file** → move to next case
   - After finishing the county: clear sessionStorage
   (see MassCourts Workflow)
5. **Bulk ArcGIS ownership check** — read CSV, build one OR query with all addresses → filter to cases where decedent owns the property (see Phase 4 — ArcGIS)
6. **Write qualified rows to Google Sheet** via clipboard paste — no Apps Script needed (see Sheet Writing)
7. **No-Images tab:** Write any cases that failed to render to a separate "No Images" tab via Apps Script (see No-Images Tab)
8. Run Pre-Foreclosure list (see Pre-Foreclosure Workflow)
9. Run Tax Lien list (see Tax Lien Workflow)
10. Run Foreclosure Auction list (see Foreclosure Auction Workflow)

---

## Date Logic

- **Pull date** = today's date (tab name)
- **File date range** = `fromDate` through `toDate` (both inclusive) — used for ALL lists (MassCourts, pre-foreclosure, tax lien, foreclosure auctions)

Calculate at the start of each run:

```python
from datetime import date, timedelta

# Federal holidays — update yearly
HOLIDAYS = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 5, 25),  # Memorial Day
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 10, 12), # Columbus Day
    date(2026, 11, 11), # Veterans Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}

def get_date_range(today=None):
    if today is None:
        today = date.today()
    dow = today.weekday()  # 0=Mon, 6=Sun

    if dow == 6:  # Sunday → grab Friday + Saturday
        return today - timedelta(2), today - timedelta(1)

    if dow == 0:  # Monday → grab Friday + Saturday + Sunday
        return today - timedelta(3), today - timedelta(1)

    yesterday = today - timedelta(1)

    # Tuesday after a Monday holiday → grab Friday through Monday
    if dow == 1 and yesterday in HOLIDAYS:
        return today - timedelta(4), yesterday

    # Any other weekday after a holiday → grab day-before-holiday + holiday
    if yesterday in HOLIDAYS:
        return yesterday - timedelta(1), yesterday

    # Normal weekday → just yesterday
    return yesterday, yesterday

from_date, to_date = get_date_range()
from_date_str = from_date.strftime('%m/%d/%Y')
to_date_str   = to_date.strftime('%m/%d/%Y')
print(f"Date range: {from_date_str} → {to_date_str}")
```

Use `from_date_str` / `to_date_str` consistently everywhere a date is needed.

---

## Sheet Setup

**Sheet:** `https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit`

**⚡ Always use Apps Script for tab creation — do NOT attempt browser clicks.** Tab rename via `contenteditable` is unreliable, context menus fail with coordinate clicks, and the Google Sheets REST API returns 403 "unregistered callers". Apps Script is the only reliable path.

### Primary method — Utilities script (pre-authorized, no OAuth needed)

Script URL: `https://script.google.com/home/projects/1ptroIio5F4VJXcSWh-XxxXyeNDIEnHEynWygyLcw1nGJYIZd4EcPyM3x/edit`

Navigate there, then inject the function via Monaco API (faster than typing):

```javascript
// Inject code — replace MM/DD/YYYY with today's pull date before running
monaco.editor.getModels()[0].setValue(`
function writeHeaders() {
  var ss = SpreadsheetApp.openById("1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw");
  var tabName = "MM/DD/YYYY";
  var existing = ss.getSheets().find(function(s) { return s.getName() === tabName; });
  if (existing) { Logger.log("Tab already exists"); return; }
  var newSheet = ss.insertSheet(tabName, 0);
  var headers = ["Pull Date","File Date","Lead First Name","Lead Last Name","Lead Owner Name","Lead Status","Lead Street","Lead City","Lead State","Lead Zip","Lead Notes","Customer Street","Customer City","Customer State","Customer Zip","Sales Date","Probate Type","Probate Case #","Tax Foreclosure","Auction Date","Campaign Lists","Lead Record Type","Owner 1 Phone","Owner 1 Deceased","Owner 1 Notes","Relative 1 Name","Relative 1 Mailing Street","Relative 1 Mailing City","Relative 1 Mailing State","Relative 1 Mailing Zip","Relative 1 Phone","Relative 1 CNAM","Relative 1 Email","Relative 1 Relationship","Relative 1 Notes","Relative 2 Name","Relative 2 Mailing Street","Relative 2 Mailing City","Relative 2 Mailing State","Relative 2 Mailing Zip","Relative 2 Phone","Relative 2 Email"];
  newSheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  SpreadsheetApp.flush();
  Logger.log("Done");
}
`);
```

Save via the "Save project to Drive" button (NOT Ctrl+S — unreliable in Monaco):
```javascript
const b = Array.from(document.querySelectorAll('button'))
  .find(b => b.getAttribute('aria-label') === 'Save project to Drive');
b.click();
```

Then click Run (▶). Check execution log for "Done" or "Tab already exists".

### If Apps Script OAuth popup is blocked

Chrome extension blocks the popup on first run. Override `window.open` **before** clicking "Review permissions" to force in-tab navigation:

```javascript
const origOpen = window.open.bind(window);
window.open = function(url, ...args) {
  if (url && url.includes('accounts.google.com')) {
    window.location.href = url;
    return null;
  }
  return origOpen.apply(this, [url, ...args]);
};
```

After OAuth completes, navigate back to the script URL and run again.

---

## MassCourts Workflow

**County division codes:**
- BA=PF02_DIV, BR=PF04_DIV, DU=PF05_DIV, ES=PF06_DIV
- FR=PF01_DIV, MI=PF09_DIV, NA=PF10_DIV, NO=PF11_DIV
- PL=PF12_DIV, SU=PF13_DIV, WO=PF14_DIV

---

### Step 1 — Navigate to county results page (Wicket double-hop)

Always start fresh from search.page.79 for each county:
```
https://www.masscourts.org/eservices/search.page.79
```

**⚠️ CRITICAL: Div select detection.** The dept select also has options starting with 'PF' (e.g. `PF_DEPT`). Always find the div select by looking for options containing `_DIV`, NOT options starting with `PF`:
```javascript
// CORRECT — finds division select
const divSel = Array.from(document.querySelectorAll('select')).find(s => Array.from(s.options).some(o => o.value.includes('_DIV')));

// WRONG — matches dept select (PF_DEPT starts with 'PF')
// const divSel = ... o.value.trim().startsWith('PF') ...
```

Set department to Probate:
```javascript
const deptSel = Array.from(document.querySelectorAll('select')).find(s => Array.from(s.options).some(o => o.value.includes('PF_DEPT')));
deptSel.value = Array.from(deptSel.options).find(o => o.value.includes('PF_DEPT')).value;
deptSel.dispatchEvent(new Event('change', {bubbles:true}));
```
Wait for div selector to appear, then set division:
```javascript
const divSel = Array.from(document.querySelectorAll('select')).find(s => Array.from(s.options).some(o => o.value.includes('_DIV')));
divSel.value = Array.from(divSel.options).find(o => o.value.includes('PF02_DIV')).value; // replace PF02 with target county code
divSel.dispatchEvent(new Event('change', {bubbles:true}));
```

**⚠️ Wicket double-hop required.** Get the Search link href, navigate to it (first hop = empty page), set dept AND division AGAIN on that empty page, get the NEW Search link href, navigate to that (second hop = page with tabs). Then find and navigate to the Case Type tab href.

**⚠️ CRITICAL: Set BOTH dept and div on EVERY hop page.** Dept resets on navigation. If you only set div, the second hop won't have the right session state and the Case Type tabs won't appear.

**⚠️ TIMING — dept → div requires a 2-second wait.** After firing `dispatchEvent('change')` on the dept select, the div select loads via AJAX. Wait ~2 seconds before trying to find/set the div select or it will come back null. Similarly, wait ~2 seconds after setting div before fetching the updated Search href.

```javascript
// After EACH hop — always set dept first, WAIT ~2s, then set div, WAIT ~2s, then get Search href
const deptSel = Array.from(document.querySelectorAll('select')).find(s => Array.from(s.options).some(o => o.value.includes('PF_DEPT')));
if (deptSel) {
  deptSel.value = Array.from(deptSel.options).find(o => o.value.includes('PF_DEPT')).value;
  deptSel.dispatchEvent(new Event('change', {bubbles:true}));
}
// ⏱ wait ~2 seconds here (computer wait tool) before finding divSel
const divSel = Array.from(document.querySelectorAll('select')).find(s => Array.from(s.options).some(o => o.value.includes('_DIV')));
if (divSel) {
  divSel.value = Array.from(divSel.options).find(o => o.value.includes('PF02_DIV')).value;
  divSel.dispatchEvent(new Event('change', {bubbles:true}));
}
// ⏱ wait ~2 seconds here before fetching Search href
// Get Search link href and navigate to it
```

After hop 2 (search.page.X.2), find the Case Type tab by its text content:
```javascript
const caseTypeLink = Array.from(document.querySelectorAll('a[href^="?x="]')).find(a => a.textContent.trim() === 'Case Type');
caseTypeLink ? caseTypeLink.getAttribute('href') : 'Case Type tab not found';
```
**Always find the Case Type tab by text content "Case Type"** — never guess by link position/index. Navigate to that href, then **wait 3 seconds** for the page to fully load, then verify `document.querySelector('select[name="caseCd"]')` exists before submitting. The caseCd select is not present immediately after navigation.

**If Case Type tab is NOT visible after hop 2** (only "Searchcurrently selected" and "Results" appear), the dept wasn't set on a prior hop. Set dept+div again on this page, navigate the updated Search link for a 3rd hop, then check for tabs again. Keep repeating until the Case Type tab appears — it always eventually shows up if dept+div are set correctly on every page. **BA and ES counties both reliably require 3+ hops.**

---

### Step 2 — Submit EA search and save results URL

```javascript
const sel = document.querySelector('select[name="caseCd"]');
Array.from(sel.options).forEach(o => o.selected = false);
Array.from(sel.options).find(o => o.value.trim() === 'EA').selected = true;
document.querySelector('input[name="fileDateRange:dateInputBegin"]').value = 'FROM_DATE'; // from_date_str
document.querySelector('input[name="fileDateRange:dateInputEnd"]').value = 'TO_DATE';     // to_date_str
document.querySelector('input[type="submit"][name="submitLink"]').click();
```

After results load, **save the results page URL immediately**:
```javascript
sessionStorage.setItem('resultsUrl_XX', window.location.href); // XX = county code e.g. BA, WO
sessionStorage.getItem('resultsUrl_XX'); // verify
```

This is the URL you navigate back to after each case render. **Do NOT use `history.back()`** — it is unreliable in Wicket.

---

### Step 3 — Read results and collect case hrefs

Use `get_page_text`. Deduplicate by case number. **Skip:**
- Initiating Action = "Voluntary Statement"
- Initiating Action = "Filing of will of deceased no petition"

**⚠️ CRITICAL — Collect ALL case hrefs upfront before processing any cases.**
Wicket `?x=` pagination tokens expire the moment you navigate away. If you navigate to a case first and then try to return to page 2, the token is dead. Instead:
1. Read page 1, collect all qualifying case hrefs immediately in one JS call
2. If there's a page 2, navigate to it RIGHT NOW and collect all hrefs from that page too
3. Only after all hrefs are stored should you start processing individual cases

```javascript
// Collect all hrefs on the current results page in one call
const caseNums = ['WO26P1234EA', 'WO26P1235EA']; // from get_page_text
const caseLinks = {};
caseNums.forEach(cn => {
  const a = Array.from(document.querySelectorAll('a')).find(el => el.textContent.trim() === cn);
  if (a) caseLinks[cn] = a.getAttribute('href');
});
sessionStorage.setItem('caseLinks_XX', JSON.stringify(caseLinks)); // XX = county code
JSON.stringify(caseLinks); // verify
```

**⚠️ Pagination — do NOT use JS click on page numbers (Wicket AJAX, won't fire).** Use `read_page` with `filter: "interactive"` to find the link with text `"2"` or `">"` (not "Go to page 2"), get its href, and navigate to the full URL immediately. Collect hrefs from that page and merge into `caseLinks_XX` before touching any case.

---

### Step 4 — Per-case loop: render + screenshot + extract

For each qualifying case href from Step 3:

**4a. Navigate to case detail:**
```javascript
window.location.href = 'https://www.masscourts.org/eservices/' + caseHref;
```

**4b. Note decedent and petitioner names** from page text (`get_page_text`) before rendering.

**4c. Render PDF to canvas** — multi-step background approach (avoids 45s CDP timeout).

⚠️ **NEVER use `async/await` at the top level of `javascript_tool` calls — it blocks the CDP connection and times out.** All async operations must use `.then()` chains and return immediately. Poll for completion in follow-up calls.

**Sub-step 4c-1: Intercept `window.open` and click Image link**
```javascript
window._capturedUrl = null;
window._fetchDone = false;
window._fetchError = null;
window._blobUrl = null;
window._canvasReady = false;
window._canvasError = null;
window.open = function(url) { window._capturedUrl = url; return null; };
var imgs = Array.from(document.querySelectorAll('a')).filter(a => a.textContent.trim() === 'Image');
if (!imgs.length) { 'NO_IMAGE' } else { imgs[0].click(); 'clicked' }
```
Wait **4 seconds** for Wicket AJAX to fire `window.open`.

**⚠️ Closed/same-day cases may have zero Image links at all.** Cases with status "Closed" that were filed and disposed on the same day sometimes have no Image links anywhere in the docket — not just a failed click, but genuinely absent. If `imgs.length === 0`, record immediately as `NO_IMAGE` without retrying. Do NOT attempt to navigate docket rows individually.

**Sub-step 4c-2: Check URL captured, start background fetch → blob**
```javascript
if (!window._capturedUrl) { 'NO_URL' } else {
  fetch(window._capturedUrl, {credentials: 'include'})
    .then(function(r) { return r.arrayBuffer(); })
    .then(function(buf) {
      window._pdfByteCount = buf.byteLength;
      window._blobUrl = URL.createObjectURL(new Blob([buf], {type: 'application/pdf'}));
      window._fetchDone = true;
    })
    .catch(function(e) { window._fetchError = e.message; });
  'fetching'
}
```
Wait **6–8 seconds** for fetch to complete (~600KB PDF).

**Sub-step 4c-3: Define render function + load pdf.js**
```javascript
if (!window._fetchDone) { 'still fetching, error=' + window._fetchError }
else {
  // Define render function — call with pageNum (1 or 2)
  window._renderPage = function(pageNum) {
    var old = document.getElementById('activeCanvas');
    if (old) old.remove();
    window._canvasReady = false;
    pdfjsLib.getDocument({ url: window._blobUrl, disableWorker: true }).promise
      .then(function(pdf) { return pdf.getPage(pageNum || 1); })
      .then(function(page) {
        var vp = page.getViewport({ scale: 1.5 });
        var c = document.createElement('canvas');
        c.id = 'activeCanvas'; c.width = vp.width; c.height = vp.height;
        // Fit entire page in viewport — do NOT use width:100vw (cuts off bottom half)
        c.style.cssText = 'position:fixed;top:0;left:0;max-height:100vh;max-width:100vw;width:auto;height:auto;z-index:99999;background:white;';
        document.body.appendChild(c);
        return page.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
      })
      .then(function() { window._canvasReady = true; })
      .catch(function(e) { window._canvasError = e.message; });
  };
  // NOTE: window.pdfjsLib persists within a page session but is cleared on every navigation to a new case URL.
  // Always check window.pdfjsLib first — saves ~8s CDN load when rendering page 2 of the same case.
  if (window.pdfjsLib) { window._renderPage(1); 'rendering page 1' }
  else {
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
    s.onload = function() {
      // disableWorker=true: no CDN worker needed, runs in main thread — avoids second CDN timeout
      pdfjsLib.GlobalWorkerOptions.workerSrc = '';
      window._renderPage(1);
    };
    document.head.appendChild(s);
    'loading pdfjs then rendering'
  }
}
```
Wait **10–15 seconds** for pdf.js load + page render.

**Sub-step 4c-4: Check canvas ready**
```javascript
window._canvasReady ? 'ready' : ('not yet, error=' + window._canvasError)
```
If not ready after 15s, wait another 10s and check again.

**⚠️ `_canvasReady` flag is unreliable — do NOT block on it indefinitely.** The `.then(() => { window._canvasReady = true; })` promise sometimes never resolves even when the canvas is fully painted. After ~25 seconds total, check `!!document.getElementById('activeCanvas')` — if the canvas element exists in the DOM, take the screenshot regardless of the flag. Only treat it as a true failure if `window._canvasError` is set.

**4d. Screenshot page 1 and extract petitioner + decedent data** (computer use allowed here):

Take screenshot. The **entire page 1** is visible with the fit-to-screen CSS. Extract:

- **Section 1 (Decedent info, item 1):** Street address, city, state, zip → decedent's domicile address
- **Section 2 (Petitioner info, item 2):** Full name, mailing address (street, city, state, zip), primary phone, email (if filled), relationship to decedent

Then render page 2 to read the venue checkbox:
```javascript
window._renderPage(2);
'rendering page 2'
```
Wait **10 seconds**, check `window._canvasReady`, then screenshot page 2. Extract:
- **Section 4 (Venue):** Which box is ticked:
  - ☑ "was domiciled in this county" → property address = Section 1 decedent street address
  - ☑ "was not domiciled in Massachusetts, but had property located in this county at:" → use the address that follows

Remove canvas after both reads:
```javascript
document.getElementById('activeCanvas').remove();
```

**4e. Immediately write the extracted row to the CSV file** (do this BEFORE navigating away):

```python
import csv, os, glob
# Find outputs folder dynamically — session name changes every session
outputs_dir = glob.glob('/sessions/*/mnt/outputs')[0]
csv_path = os.path.join(outputs_dir, 'daily_list_MMDDYYYY.csv')  # replace MMDDYYYY with pull date
file_exists = os.path.exists(csv_path)
with open(csv_path, 'a', newline='') as f:
    writer = csv.writer(f)
    if not file_exists:
        writer.writerow(['caseNum','county','decedentFirst','decedentLast','initiatingAction',
                         'propertyStreet','propertyCity','propertyState','propertyZip',
                         'petitionerName','petitionerStreet','petitionerCity','petitionerState',
                         'petitionerZip','petitionerPhone','petitionerEmail','relationship','renderStatus'])
    writer.writerow([caseNum, county, decFirst, decLast, action,
                     propStreet, propCity, propState, propZip,
                     petName, petStreet, petCity, petState,
                     petZip, petPhone, petEmail, relationship, 'OK'])
print(f"Wrote {caseNum} to CSV")
```

Use `renderStatus='NO_IMAGE'` or `renderStatus='ERROR:...'` for failed cases. **This runs via Bash tool** — the data is on disk immediately and survives any crash or context reset.

**4f. Navigate back to collected hrefs** — use the pre-collected `caseLinks_XX` from sessionStorage to get the next case href. Do NOT navigate back to the results URL.

**4g. Repeat** for next case in the county.

---

### Step 5 — After each county: clear sessionStorage

All case data is already written to the CSV file per-case (Step 4e). Just clear sessionStorage to free space:

```javascript
Object.keys(sessionStorage).filter(k => k.startsWith('canvas_')).forEach(k => sessionStorage.removeItem(k));
sessionStorage.removeItem('caseLinks_XX'); // XX = county code
// Verify cleared:
Object.keys(sessionStorage).filter(k => k.startsWith('canvas_')).length; // should be 0
```

To confirm the CSV is building correctly, check row count after each county:
```bash
wc -l $(python3 -c "import glob; print(glob.glob('/sessions/*/mnt/outputs')[0])")/daily_list_MMDDYYYY.csv
```

---

## Phase 4 — Bulk ArcGIS ownership check

After all 11 counties are done, read the CSV file and run a single bulk ownership query.

```python
import csv, glob, os
outputs_dir = glob.glob('/sessions/*/mnt/outputs')[0]
csv_path = os.path.join(outputs_dir, 'daily_list_MMDDYYYY.csv')  # replace MMDDYYYY with pull date
rows = []
with open(csv_path) as f:
    rows = list(csv.DictReader(f))
# Filter to cases with a property address and successful render
addressable = [r for r in rows if r['propertyStreet'] and r['renderStatus'] == 'OK']
print(f"{len(addressable)} cases to check, {len(rows) - len(addressable)} skipped")
```

Build one multi-condition OR query using all property addresses.

**Address normalization — strip the street suffix and use LIKE for fuzzy matching:**
- Strip the suffix word entirely (ST, STREET, RD, ROAD, DR, DRIVE, LN, LANE, AVE, AVENUE, BLVD, etc.)
- Use `SITE_ADDR LIKE '57 NICHOLS%'` instead of `SITE_ADDR = '57 NICHOLS ST'`
- This catches double-spaces, spelled-out suffixes, trailing punctuation, and extra qualifiers (e.g. "LAKE DR EAST")
- Also handles court record typos — `LIKE '425 RESE%'` still matches "RESERVOIR"

**Match on zip (not city) to disambiguate multi-city hits:**
- Zip codes are standardized; city names vary wildly (Dorchester vs Boston, N Attleboro vs North Attleborough, Brant Rock vs Marshfield)
- After getting results from the LIKE query, filter by `ZIP = petition_zip` to pick the right parcel
- If petition zip is missing, fall back to city matching as a last resort

**⚠️ ArcGIS field schema (verified 04/2026):** The city field is `CITY` (not `TOWN` — that field no longer exists). `OWNER2` also no longer exists. Valid fields: `SITE_ADDR`, `CITY`, `ZIP`, `OWNER1`, `OWN_ADDR`, `OWN_CITY`, `OWN_STATE`, `OWN_ZIP`.

**⚠️ Always use `encodeURIComponent()` for the `where` clause — never `URLSearchParams`.** `URLSearchParams` encodes spaces as `+` instead of `%20`, which ArcGIS rejects with "Invalid query parameters."

**⚠️ Run ArcGIS queries from Chrome (`javascript_tool` on `script.google.com`), not Python.** Python sandbox ArcGIS fetches can time out mid-session. Use `console.log()` to retrieve results if the return value is blocked by the extension.

```javascript
// Correct pattern — manual encodeURIComponent, no URLSearchParams
const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';

function stripSuffix(street) {
  return street.toUpperCase()
    .replace(/\b(STREET|AVENUE|BOULEVARD|DRIVE|COURT|PLACE|CIRCLE|TRAIL|WAY|ROAD|LANE|ST|AVE|BLVD|DR|CT|PL|CIR|TRL|RD|LN)\b.*$/, '')
    .trim();
}
const conditions = [
  "UPPER(SITE_ADDR) LIKE '57 NICHOLS%'",
  "UPPER(SITE_ADDR) LIKE '158 COOPER%'",
  // one per case — stripped address prefix + wildcard
].join(' OR ');
const url = `${endpoint}?where=${encodeURIComponent(conditions)}&outFields=SITE_ADDR,CITY,ZIP,OWNER1,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP&f=json&resultRecordCount=100`;
fetch(url)
  .then(r => r.json())
  .then(data => {
    window._arcgisResults = data.features?.map(f => f.attributes) || [];
    console.log('ArcGIS count:', window._arcgisResults.length);
  });
```

**Match each result back to its case:**
1. Filter results where `SITE_ADDR` starts with the stripped address prefix
2. Among those, prefer the one where `ZIP` matches the petition zip — most reliable disambiguation
3. If petition zip is missing, fall back to city name match
4. If multiple matches remain after zip filter, pick the first (likely same parcel, different unit)

**Before matching — strip generational suffixes from the decedent's last name:**
```python
import re
def clean_last_name(last_name):
    # Remove Jr., Sr., II, III, IV, V and trailing punctuation
    return re.sub(r'\b(JR\.?|SR\.?|II|III|IV|V)\b\.?', '', last_name.upper()).strip().rstrip('.,')
# e.g. "Davidson Jr." → "DAVIDSON", "Smith Sr" → "SMITH"
```

**Ownership decision:**
- OWNER1 contains the cleaned last name → **INCLUDE** — covers the decedent directly, a spouse, or a family trust (any family ownership is a valid lead)
- Completely unrelated owner → **SKIP** (decedent was a renter)
- No ArcGIS result → run fallback query by name + zip/city, then **SKIP** if still no match

**Individual fallback queries (for misses only):**
```javascript
// Step 1: Name + zip
where: "UPPER(OWNER1) LIKE '%LASTNAME%' AND ZIP = 'XXXXX'"
// Step 2: Name + city — ALWAYS try this if step 1 returns nothing
//   Do NOT skip this just because the petition has a zip.
//   Some MA municipalities (e.g. Provincetown) have ZIP = null in ArcGIS,
//   so AND ZIP = '02657' will return 0 results even with a valid petition zip.
where: "UPPER(OWNER1) LIKE '%LASTNAME%' AND CITY = 'CITYNAME'"
```

Only cases that pass the ownership check proceed to Sheet Writing.

---

## No-Images Tab

After Phase 4, append any cases with `NO_IMAGE`, `NO_URL`, or `ERROR:` render status to a single persistent tab called **"No Images"**. This tab accumulates entries across all daily runs so the team can periodically check whether images have appeared for older cases.

**Never create a dated "No Images - MM/DD/YYYY" tab.** There is one tab, one running tally.

Use Apps Script to create the tab if it doesn't exist, then append rows:

```javascript
function appendNoImages() {
  var ss = SpreadsheetApp.openById("1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw");
  var tabName = "No Images";
  var sheet = ss.getSheets().find(function(s) { return s.getName() === tabName; });
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
    sheet.getRange(1, 1, 1, 6).setValues([["Pull Date", "Case Number", "County", "Decedent Name", "Property Address", "Error"]]);
  }
  // Append one row per no-image case — replace with actual values
  var rows = [
    ["MM/DD/YYYY", "XX26P1234EA", "NO", "Smith, John", "123 Main St, Norwood MA 02062", "NO_IMAGE"],
    // ...one entry per failed case
  ];
  var lastRow = sheet.getLastRow();
  sheet.getRange(lastRow + 1, 1, rows.length, 6).setValues(rows);
  SpreadsheetApp.flush();
  Logger.log("Appended " + rows.length + " rows");
}
```

**If all cases rendered successfully**, skip this step entirely — nothing to append.

**Checking for resolved images:** On any subsequent daily run, glance at the existing "No Images" rows before appending. If a case now has a rendered image (re-navigate and check), note "RESOLVED - MM/DD/YYYY" in the Error column.

---

## Pre-Foreclosure Workflow

Navigate to `https://www.masscourts.org/eservices/search.page.79`

Change Department to **Land Court**. Switch to **Case Type tab**. Set Case Type = **Servicemembers**, date range = `from_date_str` through `to_date_str`. Submit.

For each case:
1. Open case details — property address is in top-right of page
2. If no address shown, open case image (Wicket AJAX) and scan
3. ArcGIS query by address → get OWNER1, OWN_ADDR/CITY/STATE/ZIP
4. Sheet columns: A=pull date, B=file date, C/D=owner name split, G-J=property address, K=case number+" - Servicemembers", S="Yes", AA-AD=owner mailing address (OWN_ADDR/OWN_CITY/OWN_STATE/OWN_ZIP from ArcGIS)
   - **K (Lead Notes): case number + " - Servicemembers"** — e.g. `26 SM 001259 - Servicemembers`
   - **L-O (Customer Street/City/State/Zip): leave blank** — do not put mailing address here for pre-foreclosure
   - **AA-AD (Relative 1 Mailing Street/City/State/Zip): owner mailing address** — OWN_ADDR/OWN_CITY/OWN_STATE/OWN_ZIP from ArcGIS goes here

---

## Tax Lien Workflow

Navigate to `https://www.masscourts.org/eservices/search.page.79`

Land Court, **Case Type tab**, Case Type = **Tax Lien**, date range = `from_date_str` through `to_date_str`. Submit.

For each case:
1. Open complaint PDF from docket (Wicket AJAX image link)
2. Find **"Assessed to:"** → owner name; find property address
3. If address has no street number → ArcGIS query by owner name instead of address
4. Sheet columns: A=pull date, B=file date, C/D=owner name, G-J=property address, K=casenum+" - Tax Lien", S="Yes", AA-AD=mailing address from ArcGIS

---

## Foreclosure Auction Workflow

Navigate to `https://www.masspublicnotices.org/`

Search for "auction", date range = `from_date_str` through `to_date_str`. Filter results to real property only (include "MORTGAGEE'S" notices, skip storage/boat/vehicle auctions). For each qualifying notice: click VIEW → handle reCAPTCHA on first notice only (once per session) → read full text → extract property address, auction date, owner name → ArcGIS lookup for mailing address.

Sheet columns: A=pull date, B=file date, C/D=owner name, G-J=property address, K="Foreclosure Auction", S="Yes", **T=auction date**, AA-AD=mailing address from ArcGIS

See `references/foreclosure-auction.md` for full workflow details.

---

## Sheet Writing

**No Apps Script needed — use clipboard paste directly into the sheet.**

### Step 1 — Build the TSV string from qualified rows

After ArcGIS filtering, build a tab-separated string of all rows to paste. Column order must match the sheet headers exactly (42 columns):

```python
import csv

def build_tsv_row(pull_dt, file_dt, f_name, l_name, owner_name,
                  street, city, state, zip_, notes,
                  prob_type, prob_rel, case_num,
                  rel_name, rel_street, rel_city, rel_state, rel_zip,
                  rel_phone, rel_email,
                  rel2_name='', rel2_street='', rel2_city='', rel2_state='', rel2_zip='',
                  rel2_phone='', rel2_email=''):
    # 42 columns — empty strings for unused columns
    cols = [
        pull_dt, file_dt, f_name, l_name, owner_name, '',  # A-F
        street, city, state, zip_, notes,                   # G-K
        rel_street, rel_city, rel_state, rel_zip, '',       # L-P (Customer address = petitioner)
        prob_type, prob_rel, case_num, '', '', '', '', '', '',  # Q-Y
        rel_name, rel_street, rel_city, rel_state, rel_zip, # Z-AD (Relative 1)
        rel_phone, '', rel_email, '', '',                   # AE-AI
        rel2_name, rel2_street, rel2_city, rel2_state, rel2_zip,  # AJ-AN (Relative 2)
        rel2_phone, rel2_email                              # AO-AP
    ]
    return '\t'.join(str(c) for c in cols)

tsv_rows = []
for row in qualified_rows:
    tsv_rows.append(build_tsv_row(...))  # fill in from ArcGIS-filtered CSV data

tsv_data = '\n'.join(tsv_rows)
print(f"Ready to paste {len(tsv_rows)} rows")
print(tsv_data[:500])  # preview first row
```

### Step 2 — Navigate to the sheet tab, cell A2

```
https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit#gid=GID_HERE&range=A2
```

Get the GID for today's tab first:
```javascript
// Run this on the sheet page to find the tab's gid
Array.from(document.querySelectorAll('.docs-sheet-tab')).map(t => ({
  name: t.querySelector('.docs-sheet-tab-name')?.textContent,
  gid: new URLSearchParams(t.querySelector('a')?.href?.split('#')[1]).get('gid')
}));
```

Wait 3 seconds, take screenshot to confirm A2 is selected.

### Step 3 — Paste via clipboard

```javascript
navigator.clipboard.writeText(TSV_DATA_HERE).then(() => 'copied');
```

Then press `Ctrl+V`. Google Sheets splits tab-separated values across columns and newlines across rows automatically.

**⚠️ Zip codes:** After paste, zip code columns (J and AD) may lose leading zeros. Re-format those columns as plain text if needed.

**⚠️ Verify row count** after paste matches expected qualified case count.

**Apps Script is still used for:** tab creation at start of run, and No-Images tab creation. For data writes, always use clipboard paste.

---

## Google Sheets Navigation Rules

- **Never click from screenshot coordinates** — use `getBoundingClientRect()` for any click targets
- **Single cell navigation:** URL fragment `#gid={gid}&range=A2` — never use the Name Box (typing in it puts text in the active cell)
- **Wait 3-4 seconds** after URL navigation before doing anything; take screenshot to confirm
- **Context menus:** Shift+F10 then arrow keys + Enter — never coordinate clicks
- **Zip codes:** prefix with apostrophe (`'02356`) to preserve leading zeros

---

## Common Issues

| Problem | Fix |
|---------|-----|
| MassCourts CAPTCHA appears | A CAPTCHA extension auto-solves it — wait up to 10 seconds and retry several times before giving up. Never stop and ask the user to intervene. |
| MassCourts welcome page shows "Click Here" after CAPTCHA | The page does NOT auto-advance. Use JS to click the "Click Here" link: `Array.from(document.querySelectorAll('a')).find(a => a.textContent.trim() === 'Click Here')?.click()` — then wait 3–4 seconds for the search page to load. |
| "Last Name required" on MassCourts | You're on the Name tab — switch to Case Type tab using MouseEvent dispatch |
| Case Type tab won't switch | Dispatch `new MouseEvent('click', {bubbles:true, cancelable:true, view:window})` on the `<a>` element |
| PDF scroll freezes | Claude extension active — use blob URL workaround to open in new tab for viewing |
| Submit button fails | Try `document.getElementById('idf9f').click()` |
| Session token error on `?x=` URL | Navigated stale token — go back to search.page.79 and start fresh |
| "Leave site?" blocks navigation | `window.onbeforeunload = null` before navigating |
| Paste lands in wrong cell | Always verify Name Box shows target cell before Ctrl+V |
| Pagination page 2 click does nothing | Wicket AJAX — use `read_page` to get the "Go to page 2" href, then `navigate` directly to that full URL |
| Wicket double-hop gives empty page | Normal — set div again on the empty page, get new Search href, navigate again |
| Apps Script auth popup blocked | Override `window.open` before clicking "Review permissions" to navigate in-tab (see Sheet Setup → OAuth section) |
| Tab rename via contenteditable fails | Don't try — use Apps Script `insertSheet(tabName, 0)` instead |
| Google Sheets REST API returns 403 | "Unregistered callers" — do not use the REST API, use Apps Script only |
| Monaco setValue() doesn't save | Ctrl+S unreliable — click the "Save project to Drive" button by aria-label instead |
| New bound script needs OAuth every time | Use the pre-authorized Utilities script (1ptroIio5F4VJXcSWh...) instead of Extensions → Apps Script |
| history.back() stays on same page | Unreliable — navigate directly to saved `sessionStorage.getItem('resultsUrl_XX')` instead |
| Div select finds dept select instead | Use `o.value.includes('_DIV')` NOT `o.value.trim().startsWith('PF')` — PF_DEPT also starts with PF |
| Case Type tab missing after hop 2 | Dept wasn't set on a prior hop — set BOTH dept+div on every hop page, then re-navigate |
| Extension blocks `?x=` in JS return values | Cannot return href/outerHTML containing `?x=` query strings — store on window object instead |
| Extension blocks base64 return values | Cannot return base64 strings — store on window object, retrieve in chunks via console.log |
| async/await in javascript_tool times out | CDP 45s limit — use `.then()` chains only, return immediately, poll completion in follow-up calls |
| pdf.js worker CDN hangs | Use `disableWorker: true` + `workerSrc = ''` — runs in main thread, no CDN worker needed |
| PDF URL (`?x=`) is single-use token | Cannot re-fetch after navigating away — fetch as ArrayBuffer → Blob → createObjectURL first |
| Canvas shows only top of page | Do NOT use `width:100vw;height:auto` — use `max-height:100vh;max-width:100vw;width:auto;height:auto` to fit full page |
| Petitioner info not on page 2 | Section 2 (petitioner) is on PAGE 1. Section 4 (venue) is on PAGE 2. Render both pages separately |
| Case detail page has no address/zip | Normal — MassCourts case details don't show decedent address. Use petition canvas or ArcGIS fallback |
| Wicket Case Type tab + submit in same JS call | Causes Internal Server Error — navigate Case Type tab href first, THEN submit separately |
| PDF.js canvas renders blank (0 chars text) | Normal — petitions are scanned images, not text PDFs. Canvas rendering still works; blank text is expected |
| Canvas render fails / ERROR: in sessionStorage | Network timeout or expired session token — record in No-Images tab for manual review. Do NOT fall back to ArcGIS |
| sessionStorage quota exceeded (~10MB) | Clear canvas_ entries after each county (Step 5). ~5 counties worth of canvases fills the quota |
| Image link href="#" returns no PDF URL | Do NOT use getAttribute('href'). Intercept window.open before clicking Image — Wicket fires window.open(PDF_URL) via AJAX |
| ArcGIS returns no match for address | Run name+zip fallback, then name+city. If still no match, skip — decedent is likely a renter |
| Canvas displays but address is cut off | Increase scale from 1.5 to 2.0 in `page.getViewport({ scale: 2.0 })` for higher resolution |
| Pagination `?x=` token expires immediately | Collect ALL case hrefs from ALL pages before processing any case — tokens die the moment you navigate away |
| resultsUrl_XX returns wrong page mid-county | After many navigations, stored results URL may degrade and return stale content — re-run full search from search.page.79 |
| Triple-hop needed for Case Type tab | ES (and possibly others) may require 3+ hop cycles. BA has been observed to show the Case Type tab after the very first dept+div set on search.page.79 — no extra hops needed. Do not assume BA always needs 3 hops; proceed to Case Type as soon as the tab appears. |
| pdf.js CDN takes 8s on every new case | window.pdfjsLib is cleared on navigation — always include the if/else check to reuse or reload; saves ~8s when pdfjsLib is cached |
| divSel comes back null after setting dept | Div select loads via AJAX after dept change — wait 2 seconds after dept dispatchEvent before querying for divSel |
| caseCd select missing after navigating Case Type href | Normal — wait 3 full seconds after navigation before checking for caseCd; it is not present immediately |
| Each search.page.79 load gets a new page number | Expected Wicket behavior (e.g. page.166, page.167) — session tokens from prior page numbers are dead, always start fresh |
| `_canvasReady` flag never becomes true | The promise sometimes doesn't resolve even when canvas is fully painted. After ~25s total, check `!!document.getElementById('activeCanvas')` — if it exists, take the screenshot anyway. Only treat as failure if `window._canvasError` is set. |
| Closed case has zero Image links | Same-day filed+disposed cases (status "Closed") sometimes have no Image links at all in the docket. Record immediately as NO_IMAGE — do not retry or navigate docket rows. |
| "Pay" prefix on decedent name in results | `get_page_text` picks up "Pay" button labels prepended to party names (e.g. "Pay Thumith, Robert Louise"). Strip any leading "Pay " token when parsing decedent names from the results page. |
| ArcGIS `TOWN` field returns "Invalid field" error | Field was renamed to `CITY` (verified 04/2026). Update all queries to use `CITY`. `OWNER2` also no longer exists — remove from outFields. |
| ArcGIS returns "Invalid query parameters" with URLSearchParams | `URLSearchParams` encodes spaces as `+`; ArcGIS requires `%20`. Always use `encodeURIComponent()` on the `where` string directly. |
| ArcGIS LIKE query misses a known address (pre-foreclosure) | Use 3-step fallback: (1) street number + city only — `SITE_ADDR LIKE '20 %' AND CITY = 'HAVERHILL'` — bypasses double-space and abbreviation mismatches; (2) owner last name + city; (3) owner last name only. Step 1 is most reliable and handles trust-owned properties where the owner name won't match the defendant. |
| Pre-foreclosure: property held in trust | Owner-name fallback unreliable — trust name (e.g. "BRADLEY FAMILY NOMINEE TRUST") may not match defendant. Always try street-number-only query (Step 1) first. |
| history.back() in pre-foreclosure case loop | Unlike probate, history.back() works reliably for returning from a Servicemembers case to the results page. No need to save/restore resultsUrl for pre-foreclosure. |
| Pre-foreclosure result count shows e.g. "25 of 25" but only 9 unique cases | The results table shows one row per party, not per case. Multiple defendants/plaintiffs per case inflate the count. Always deduplicate by case number. |
| Rhino runtime deprecation banner blocks script | If V8 is already enabled in Project Settings, the banner is cosmetic. Click Dismiss and run again — it will execute successfully. |
| Apps Script "Rhino runtime" warning persists after enabling V8 | V8 may be enabled in settings but the banner reappears. Dismiss it each time; the function still runs on V8. |

---

## Reference Files

Full detailed workflows are in:
- `references/probate.md` — complete probate case workflow
- `references/pre-foreclosure.md` — pre-foreclosure workflow
- `references/tax-lien.md` — tax lien workflow
- `references/foreclosure-auction.md` — foreclosure auction workflow (masspublicnotices.org)

---

## MassCourts Site Redesign — April 2026

MassCourts updated their site appearance in late April 2026. Two things changed that affect pre-foreclosure/SM case parsing:

### 1. Party names now appear as "NAME - Role" (not "Role : ID\nNAME")

**Old format (pre-April 2026):**
```
Defendant(s) : 469269  Date: 04/21/2026
CLARK, DANIEL J
```

**New format (post-April 2026):**
```
Clark, Daniel J. - Defendant
```

When extracting defendant/plaintiff names from `innerText`, use:
```javascript
// Get party section (after "Party Information" header)
var body = document.body.innerText;
var partyIdx = body.indexOf('Party Information');
var partySection = partyIdx >= 0 ? body.substring(partyIdx) : body;

// Find "NAME - Defendant" on its own line (m flag = multiline, i flag = case-insensitive)
var match = partySection.match(/^([^\n]+?)\s+-\s+Defendant\b/mi);
var defendantName = match ? match[1].trim() : '';

// Find "NAME - Plaintiff" on its own line
var matchP = partySection.match(/^([^\n]+?)\s+-\s+Plaintiff\b/mi);
var plaintiffName = matchP ? matchP[1].trim() : '';
```

The `^` with `m` flag anchors to line starts — each party is on its own line in `innerText`.

### 2. Property address now shown directly on the case detail page

The redesigned case detail page shows a **Property Information** box in the top right with the street and city on separate lines — no need to open the complaint PDF just to get the address for SM/pre-foreclosure cases.

**innerText structure:**
```
Property Information
42 Warebrook Village
Ware
```

Extract with:
```javascript
var propMatch = body.match(/Property\s+Information\s*\n([0-9]+[^\n]+)\n([A-Za-z][^\n]+)/i);
var propStreet = propMatch ? propMatch[1].trim() : '';
var propCity   = propMatch ? propMatch[2].trim() : '';
```

Fall back to the complaint PDF only if this box is absent or the address is missing.

### Common Issues (additions for April 2026 redesign)

| Problem | Fix |
|---------|-----|
| SM defendant name comes back empty after April 2026 | MassCourts redesign changed party format. Use `^([^\n]+?)\s+-\s+Defendant\b` with `mi` flags on the party section of `innerText`. |
| Property address missing from SM case detail page | New page has a "Property Information\nSTREET\nCITY" block in top right. Parse that before falling back to the PDF. |
| `daily_list.py` `extract_party_name` returning empty for SM cases | Fixed 04/27/2026 — updated to detect new "NAME - Role" format with `re.MULTILINE` before falling back to old sequence-ID format. |
