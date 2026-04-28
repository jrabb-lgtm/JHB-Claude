# Pre-Foreclosure List

This workflow pulls yesterday's Servicemembers cases from MassCourts.org (Land Court), finds the property address for each case, queries the ArcGIS REST API to get the owner name and mailing address, then pastes the results into the daily Google Sheet.

---

## Steps at a glance
1. Search MassCourts.org → Land Court → Servicemembers → filed yesterday
2. For each case, get the property address (top right of case page, or from images)
3. Query ArcGIS REST API by address to get owner name + mailing address
4. Paste results into the daily Google Sheet (same tab and column layout as probate)

---

## MassCourts.org Navigation

**Base URL:** `https://www.masscourts.org/eservices/`

Same session token rules as probate apply — never navigate directly to a `?x=` URL from outside the session.

### Searching for cases

On the search page:
- **Court Department:** Land Court
- **Case Type:** Servicemembers
- **Date filed:** File date (both from and to)

Submit the search and work through each result one by one.

### Getting the property address from a case

When you open a case, look for the property address in the **top right of the page**. It should be listed there directly.

If no address appears in the top right, open the case images (same Wicket AJAX pattern as probate — find the Image link in the docket and click by element ID). The address will be somewhere in the document.

All addresses will be in Massachusetts.

---

## ArcGIS REST API Lookup

**Run in Chrome browser via `javascript_tool`** — the Python sandbox cannot reach external URLs.

### Query by property address

```javascript
// Paste into javascript_tool in any open Chrome tab (e.g. Google Sheets)
(async () => {
  const addr = '123 MAIN ST'; // replace with property address, uppercase
  const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';
  const params = new URLSearchParams({
    where: `UPPER(SITE_ADDR) LIKE '${addr}%'`,
    outFields: 'OWNER1,OWNER2,SITE_ADDR,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP',
    f: 'json',
    resultRecordCount: '5'
  });
  try {
    const r = await fetch(`${endpoint}?${params}`);
    const data = await r.json();
    if (data.features && data.features.length > 0) {
      return { results: data.features.map(f => f.attributes) };
    }
  } catch(e) { return `Error: ${e.message}`; }
  return 'No match found';
})()
```

### Interpreting the result

The result gives you:
- `OWNER1` — owner name (use for cols C/D — split first/last if possible)
- `OWN_ADDR` — mailing street (col AA)
- `OWN_CITY` — mailing city (col AB)
- `OWN_STATE` — mailing state (col AC)
- `OWN_ZIP` — mailing zip (col AD)
- `SITE_ADDR` — confirms the matched property address (use for cols G-J)

### If no exact address match — 3-step fallback

ArcGIS sometimes stores addresses with double spaces (e.g. `"20  CLIFTWOOD ST"`) or abbreviated street names that don't match the court record. A full-name LIKE query silently misses these. Follow these steps in order:

**Step 1: Street number + city only (no street name)**

Searching by street number alone avoids double-space and abbreviation mismatches entirely. Review the results and pick the one where `SITE_ADDR` matches the street name from the court record.

```javascript
const streetNum = '20';    // just the number, no street name
const city = 'HAVERHILL'; // uppercase
const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';
const where = `UPPER(SITE_ADDR) LIKE '${streetNum} %' AND UPPER(CITY) = '${city}'`;
const url = `${endpoint}?where=${encodeURIComponent(where)}&outFields=SITE_ADDR,CITY,ZIP,OWNER1,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP&f=json&resultRecordCount=20`;
fetch(url).then(r => r.json()).then(data => {
  window._arcResult = data.features?.map(f => f.attributes) || [];
  console.log(JSON.stringify(window._arcResult));
});
```

**Step 2: Owner last name + city (only if Step 1 returns no useful match)**

⚠️ **This will fail if the property is held in a trust.** The trust name (e.g. "BRADLEY FAMILY NOMINEE TRUST") may or may not contain the defendant's last name. Do not rely on this step for trust-owned properties — go back to Step 1 with the street number.

```javascript
const lastName = 'ORFANOS'; // defendant last name, uppercase
const city = 'HAVERHILL';
const where = `UPPER(OWNER1) LIKE '%${lastName}%' AND UPPER(CITY) = '${city}'`;
```

**Step 3: Owner last name only, no city (last resort)**

If the city is missing or mis-spelled in ArcGIS, drop the city filter:

```javascript
const where = `UPPER(OWNER1) LIKE '%${lastName}%'`;
```

**⚠️ Always use `encodeURIComponent()` on the where clause** — never URLSearchParams (encodes spaces as `+` which ArcGIS rejects).

**⚠️ If all steps fail**, discover the current URL:
1. Navigate to `https://gis.massgis.state.ma.us/arcgis/rest/services/Parcels/` in Chrome
2. Find the statewide parcel layer → copy its URL → append `/query`

---

## Cleaning Up Lead First/Last Name (Columns C & D)

Column E (Lead Owner Name) can stay exactly as ArcGIS returns it — messy is fine there. But columns C and D should be a clean human first and last name.

### Where to find the defendant name
On the MassCourts case details page, the party list shows defendants. There may be more than one (e.g., both people on a joint mortgage). Use `get_page_text` to read them.

### If there's only one defendant
Split it into first/last for C and D. Most names are straightforward — "John Smith" → C=John, D=Smith. Drop suffixes (Jr., III, etc.) from column D if they make it messy; they can stay in E.

### If there are multiple defendants
Pick the one whose name is closest to the ArcGIS `OWNER1` value. Compare by last name first — whichever defendant's last name appears in OWNER1, use that person for C and D. If it's still a tie, go with the first-listed defendant.

**Example:**
- Defendants: "John Smith" and "Mary Jones"
- ArcGIS OWNER1: "SMITH JOHN & JONES MARY"
- → Use "John Smith" (first match) → C=John, D=Smith
- → E stays as "SMITH JOHN & JONES MARY"

### ArcGIS name format
ArcGIS often returns names as LAST FIRST (e.g., "JOHNSON CAROL A") or combined with "&" for joint owners. When splitting for C/D, use the defendant name from MassCourts (which is in natural order) rather than trying to parse the ArcGIS string directly.

---

## Pasting Results into the Google Sheet

Same sheet and same date-based tab as probate:
- **URL:** `https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit`
- **Tab:** Today's date tab (e.g., `04/14/2026`)

For each case, use the same 35-column layout as probate. Key columns:

| Col | Value |
|-----|-------|
| A | Pull Date (today) |
| B | File Date (yesterday) |
| C | Owner first name (from OWNER1, split if possible) |
| D | Owner last name |
| G | Property street (SITE_ADDR) |
| H | Property city |
| I | MA |
| J | Property zip |
| K | Case number + " - Servicemembers" |
| S | "Yes" (Tax Foreclosure / pre-foreclosure flag) |
| AA | Mailing street (OWN_ADDR) |
| AB | Mailing city (OWN_CITY) |
| AC | Mailing state (OWN_STATE) |
| AD | Mailing zip (OWN_ZIP) |

Write rows via Apps Script (same pattern as probate) using `sheet.getLastRow() + 1` to append.

**⚠️ Critical column layout — do NOT put mailing address in L-O.** The 35-column row template for pre-foreclosure (0-indexed positions shown):

```javascript
// Correct 35-column pre-foreclosure row
// Positions: 0=A ... 10=K ... 17=R ... 18=S ... 25=Z ... 26=AA ... 29=AD
var row = [
  pullDate,       // A (0)  Pull Date
  fileDate,       // B (1)  File Date
  firstName,      // C (2)  Owner first name
  lastName,       // D (3)  Owner last name
  owner1,         // E (4)  Full ArcGIS OWNER1
  "",             // F (5)  Lead Status — blank
  propStreet,     // G (6)  Property street (SITE_ADDR)
  propCity,       // H (7)  Property city
  "MA",           // I (8)  Property state
  propZip,        // J (9)  Property zip
  caseNum + " - Servicemembers", // K (10) Lead Notes — case number here
  "",             // L (11) Customer Street — BLANK for pre-foreclosure
  "",             // M (12) Customer City — BLANK
  "",             // N (13) Customer State — BLANK
  "",             // O (14) Customer Zip — BLANK
  "",             // P (15) Sales Date
  "",             // Q (16) Probate Type
  "",             // R (17) Probate Case #
  "Yes",          // S (18) Tax Foreclosure flag
  "",             // T (19) Auction Date
  "",             // U (20) Campaign Lists
  "",             // V (21) Lead Record Type
  "",             // W (22) Owner 1 Phone
  "",             // X (23) Owner 1 Deceased
  "",             // Y (24) Owner 1 Notes
  "",             // Z (25) Relative 1 Name — blank for pre-foreclosure
  ownAddr,        // AA (26) Relative 1 Mailing Street — OWN_ADDR goes HERE
  ownCity,        // AB (27) Relative 1 Mailing City — OWN_CITY
  ownState,       // AC (28) Relative 1 Mailing State — OWN_STATE
  ownZip,         // AD (29) Relative 1 Mailing Zip — OWN_ZIP
  "",             // AE (30) Relative 1 Phone
  "",             // AF (31) Relative 1 CNAM
  "",             // AG (32) Relative 1 Email
  "",             // AH (33) Relative 1 Relationship
  ""              // AI (34) Relative 1 Notes
];
```

---

## Skipped Tab & Late-Case Detection

### When to write to the Skipped tab
Any Servicemembers case that was looked up but not added to the daily sheet must go in the **Skipped** tab:
- ArcGIS returned no match and address couldn't be resolved
- Any other intentional pass

**Columns to write:**

| Column | Value |
|--------|-------|
| A | Case Number |
| B | Case Type ("Servicemembers") |
| C | Owner Name (if known) |
| D | Skip Reason (e.g., "No ArcGIS match", "No address found") |
| E | File Date |

### 2-Week Late-Case Sweep

At the start of each daily list, re-pull Servicemembers cases from prior dates and cross-check against all three tabs:
- **Any dated daily tab** → already processed, skip
- **Skipped tab** → deliberately passed, skip
- **Not in any tab** → genuinely late-appearing case; process fresh

---

## Common Issues & Fixes

| Problem | Fix |
|---------|-----|
| No address shown in top right | Open case images and scan for address |
| No match by full address | Use 3-step fallback: (1) street number + city only — bypasses double-space/abbreviation issues; (2) owner last name + city; (3) owner last name only. Do NOT rely on name fallback for trust-owned properties. |
| ArcGIS double-space in SITE_ADDR | Some parcels stored as "20  CLIFTWOOD ST" (two spaces). LIKE '20 CLIFTWOOD%' returns nothing. Always try street-number-only query first. |
| Property held in trust | Trust name (e.g. "BRADLEY FAMILY NOMINEE TRUST") may not contain defendant's last name. Owner-name fallback is unreliable. Use street number + city (Step 1) instead. |
| ArcGIS endpoint errors | Try the fallback endpoint; if both fail, navigate to MassGIS services page to find current URL |
| Multiple results for same address | Pick the one where SITE_ADDR most closely matches — usually the first result |

---

## Quick Reference

```
1. MassCourts → Land Court → Servicemembers → file date
2. For each case:
   → Get property address (top right or images)
   → Run ArcGIS address query in Chrome javascript_tool
   → Extract OWNER1 + OWN_ADDR/OWN_CITY/OWN_STATE/OWN_ZIP
3. Append to Google Sheet via Apps Script:
   → Owner name → C/D
   → Property address → G-J
   → Mailing address → AA-AD
   → "Yes" → S (pre-foreclosure flag)
```
