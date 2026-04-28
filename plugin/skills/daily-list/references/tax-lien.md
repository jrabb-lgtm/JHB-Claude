# Tax Lien List

This workflow pulls yesterday's Tax Lien cases from MassCourts.org (Land Court), extracts the owner name and property address from the complaint document, queries the ArcGIS REST API to get the mailing address, and pastes results into the daily Google Sheet.

---

## Steps at a glance
1. Search MassCourts.org → Land Court → Tax Lien → filed yesterday
2. For each case, open the complaint and find "Assessed to:" (owner name) and property address
3. Query ArcGIS REST API by address (or by name if no street number) to get the mailing address
4. Paste results into the daily Google Sheet

---

## MassCourts.org Navigation

**Base URL:** `https://www.masscourts.org/eservices/`

Same session token rules apply — never navigate directly to a `?x=` URL from outside an active session.

### Search settings
- **Court Department:** Land Court
- **Case Type:** Tax Lien
- **Date filed:** File date (both from and to)

Work through each result one by one.

---

## Reading the Complaint

Unlike probate and pre-foreclosure, tax lien cases do **not** show the address in the top right. Open the complaint document from the docket using the same Wicket AJAX pattern as probate:

```javascript
document.getElementById('idXXXX').click(); // replace with actual image link ID
```

This opens the complaint PDF in a new tab. Check `tabs_context_mcp` for the new tab ID.

### What to look for in the complaint

Every town files these slightly differently, but the key fields are always present:

**Owner name:** Look for `Assessed to:` — the name that follows is the owner. Note: this is sometimes an heir rather than the current owner, which is fine — record it as-is.

**Property address:** Also in the complaint body.

**Important — land without a street number:** Some parcels (undeveloped land) have no street number — the address will be something like `Main St` or `Lot on Oak Ave` with no number. When this happens, **query ArcGIS by owner name** instead of address.

---

## ArcGIS REST API Lookup

**Run in Chrome browser via `javascript_tool`** — the Python sandbox cannot reach external URLs.

### Query by address (standard case)

```javascript
(async () => {
  const addr = '123 MAIN ST'; // property address from complaint, uppercase
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

### Query by owner name (when no street number)

When the property address has no street number, query by the `Assessed to` name instead:

```javascript
(async () => {
  const name = 'SMITH JOHN'; // "Assessed to" name from complaint, uppercase — try both "FIRST LAST" and "LAST FIRST"
  const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';
  const params = new URLSearchParams({
    where: `UPPER(OWNER1) LIKE '%${name}%'`,
    outFields: 'OWNER1,OWNER2,SITE_ADDR,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP',
    f: 'json',
    resultRecordCount: '10'
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

If the full name doesn't match, retry with just the last name (and optionally add a town filter).

**⚠️ If both endpoints fail**, navigate to `https://gis.massgis.state.ma.us/arcgis/rest/services/Parcels/` in Chrome to find the current URL.

---

## Cleaning Up Lead First/Last Name (Columns C & D)

Column E (Lead Owner Name) can stay exactly as ArcGIS returns it — messy is fine there. But columns C and D should be a clean human first and last name.

### Where to find the defendant name
The "Assessed to:" field in the complaint gives the owner name. The MassCourts case details party list also shows defendants. There may be more than one. Use `get_page_text` to read all parties.

### If there's only one defendant
Split into first/last for C and D. Drop suffixes (Jr., III, etc.) from D if they clutter it; they can stay in E.

### If there are multiple defendants
Pick the one whose name is closest to the ArcGIS `OWNER1` value. Compare by last name — whichever defendant's last name appears in OWNER1, use that person for C and D. If it's still a tie, go with the first-listed defendant.

**Example:**
- Defendants: "Robert Kane" and "Susan Kane"
- ArcGIS OWNER1: "KANE ROBERT & SUSAN"
- → Use "Robert Kane" (first match) → C=Robert, D=Kane
- → E stays as "KANE ROBERT & SUSAN"

### ArcGIS name format
ArcGIS often returns names as LAST FIRST (e.g., "KANE ROBERT") or combined with "&" for joint owners. When splitting for C/D, use the defendant name from MassCourts (natural order) rather than parsing the ArcGIS string directly.

---

## Pasting Results into the Google Sheet

Same sheet and same date-based tab as probate and pre-foreclosure:
- **URL:** `https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit`
- **Tab:** Today's date tab

For each case, use the same 35-column layout. Key columns:

| Col | Value |
|-----|-------|
| A | Pull Date (today) |
| B | File Date (yesterday) |
| C | Owner first name (from "Assessed to:", split if possible) |
| D | Owner last name |
| G | Property street |
| H | Property city |
| I | MA |
| J | Property zip |
| K | Case number + " - Tax Lien" |
| S | "Yes" (Tax Foreclosure flag) |
| AA | Mailing street (OWN_ADDR from ArcGIS) |
| AB | Mailing city (OWN_CITY) |
| AC | Mailing state (OWN_STATE) |
| AD | Mailing zip (OWN_ZIP) |

Write rows via Apps Script using `sheet.getLastRow() + 1` to append after probate and pre-foreclosure rows.

---

## Skipped Tab & Late-Case Detection

### When to write to the Skipped tab
Any Tax Lien case that was looked up but not added to the daily sheet must go in the **Skipped** tab:
- ArcGIS returned no match by address or name
- Complaint unreadable and address couldn't be resolved
- Any other intentional pass

**Columns to write:**

| Column | Value |
|--------|-------|
| A | Case Number |
| B | Case Type ("Tax Lien") |
| C | Owner Name (from "Assessed to:", if found) |
| D | Skip Reason (e.g., "No ArcGIS match", "No address in complaint") |
| E | File Date |

### 2-Week Late-Case Sweep

At the start of each daily list, re-pull Tax Lien cases from prior dates and cross-check against all three tabs:
- **Any dated daily tab** → already processed, skip
- **Skipped tab** → deliberately passed, skip
- **Not in any tab** → genuinely late-appearing case; process fresh

---

## Common Issues & Fixes

| Problem | Fix |
|---------|-----|
| Can't find "Assessed to:" | Scan the full complaint — formatting varies by town. Look for owner name near the property description |
| Property address has no street number | Switch to name-based ArcGIS query |
| Name in complaint is an heir, not owner | Record the heir name as-is — it's the assessed party |
| No ArcGIS match by address or name | Try partial last name only; if still no match, leave AA-AD blank |
| Complaint hard to read | Use `get_page_text` on the PDF tab to extract all text |

---

## Quick Reference

```
1. MassCourts → Land Court → Tax Lien → file date
2. For each case:
   → Open complaint PDF (Wicket AJAX image link)
   → Find "Assessed to:" → owner name
   → Find property address
   → If address has no street number → ArcGIS query by owner name
   → Otherwise → ArcGIS query by SITE_ADDR
   → Extract OWN_ADDR/OWN_CITY/OWN_STATE/OWN_ZIP for mailing address
3. Append to Google Sheet via Apps Script:
   → Owner name → C/D
   → Property address → G-J
   → Mailing address → AA-AD
   → "Yes" → S (tax foreclosure flag)
```
