# Probate List

This workflow pulls new Massachusetts probate cases from MassCourts.org for the file date, verifies the decedent owned real estate (via the bond document + ArcGIS parcel lookup), and adds qualifying cases to the daily Google Sheet with decedent and petitioner addresses.

**Key filter:** Only add a case to the sheet if the decedent or the petitioner owned the property where the decedent lived. Query ArcGIS on the decedent's address — if neither name appears in OWNER1 (directly or via a trust), skip the case (renting). No bond document needed. (Note: the ArcGIS layer has no OWNER2 field — only OWNER1.)

---

## Part 1: Pull New Cases

### Search settings on MassCourts.org

- **Base URL:** `https://www.masscourts.org/eservices/`
- **Court Department:** Probate and Family Court
- **Case Type:** Estates and Administration
- **Date filed:** File date (last business day — both from and to)

### ⚠️ SKIP Voluntary Statement cases
If the **Initiating Action** is **"Voluntary Statement"**, skip the case entirely — do not add it to the sheet. These are small estate affidavits and are not relevant to the daily list.

Only process cases where the Initiating Action is something other than "Voluntary Statement" (e.g., "Formal Probate of Will", "Informal Probate", "Late and Limited", "Filing of will of deceased no petition", etc.).

### For each non-Voluntary-Statement case:

Record the following directly from the **search results listing** — no need to open individual cases:

| Column | Header | What to record |
|--------|--------|----------------|
| A | Pull Date | Today's date |
| B | File Date | The file date searched |
| C | Lead First Name | **Decedent's first name** |
| D | Lead Last Name | **Decedent's last name** |
| E | Lead Owner Name | **Decedent's full name** — first name + " " + last name (e.g., "James Sweeney"). This is the person whose estate is being probated, i.e., the property owner. ⚠️ Do NOT put the petitioner's name here — the petitioner goes in col Z (see Part 2 column mapping). This mistake was made on 04/14/2026 for MI26P1846EA where "Carol A. Sweeney" (petitioner) was incorrectly entered instead of "James Sweeney" (decedent). |
| F | Lead Status | Leave blank |
| G | Lead Street | Leave blank (filled in Part 2) |
| H | Lead City | Leave blank (filled in Part 2) |
| I | Lead State | Leave blank (filled in Part 2) |
| J | Lead Zip | Leave blank (filled in Part 2) |
| K | Lead Notes | **Leave blank** — do NOT put case number here (case number has its own column R) |

---

## Part 2: Open Petition Images and Extract Addresses

**CRITICAL: You must open the petition image to get address data.** The MassCourts case details page only shows attorney office addresses — those are NOT what goes in the sheet. The petitioner's personal address is only available on the actual petition form.

### MPC Form Types
- **MPC 160** = Formal Probate (4+ pages). Always has a petitioner email field in Section 2.
- **MPC 3019** = Informal Probate (shorter, ~2 pages). Email field may be absent. If no email field is visible, leave column AG blank.
- The form type is printed in the upper right corner of page 1.

### What we need from each case:
1. **Decedent's street address** (from petition page 1, Section 1) → Lead Street, City, State, Zip (columns G-J)
2. **Petitioner's personal address and phone** (from petition page 1, Section 2) → Relative 1 Mailing Street, City, State, Zip, Phone (columns AA-AE)
3. **Petitioner's email** (from petition Section 2, if present) → Relative 1 Email (column AG)
4. For out-of-state decedents: the **MA property address** (from petition page 2, Section 4) → Lead Street columns instead

### MPC 160 Petition Form Structure:

**Section 1 - Information about the Decedent:**
- Name: First, Middle, Last
- **Street Address: [ADDRESS], [CITY/TOWN], [STATE], [ZIP]** ← This goes in Lead Street (G-J)
- "The Decedent was domiciled in [CITY], [STATE]"

**Section 2 - Information about the Petitioner:**
- Name: First, M.I., Last
- **Address: [ADDRESS], [CITY/TOWN], [STATE], [ZIP]** ← This goes in Relative 1 Mailing (AA-AD) AND Customer Street/City/State/Zip (L-O) — same data in both places
- **Primary Phone #: [PHONE]** ← This goes in Relative 1 Phone (AE)
- **Email: [EMAIL]** ← This goes in Relative 1 Email (AG). Only present on MPC 160 (formal) and some MPC 3019 (informal). If absent, leave AG blank.
- **"The Petitioner's interest in the estate is as follows...": [RELATIONSHIP]** ← This goes in **Probate Relationship (Q)**. It appears just below the email field. Examples: "Personal Representative named in a Will", "heir - brother", "surviving spouse", "child".
- **If a second petitioner block appears**, extract their name, address, phone, and email into **Relative 2** columns (AJ–AP). If no second petitioner, leave AJ–AP blank.

**Section 4 - Venue (page 2):**
- If "was domiciled in this county" is checked → decedent lived in MA, use Section 1 address
- If "was not domiciled in Massachusetts, but had property located in this county at:" → use the property address listed here for Lead Street instead of Section 1

### Steps per case:
1. Get case number from sheet column R (e.g., `BR26P0808EA`)
2. Search on MassCourts → navigate to case details → docket → check for "Image" link → click it
3. **⚠️ If no "Image" link is present in the docket row** (4th `<td>` has `<span> </span>` instead of `<a>Image</a>`), the petition is not publicly available. Copy the full row from the daily tab and paste it into the **"No Images" tab** of the Google Sheet. Leave G-J and AA-AG blank. These cases are re-checked at the start of each subsequent daily list run for up to 2 weeks — if an image appears, move the row back to the dated tab and fill in addresses. After 2 weeks with no image, delete from the No Images tab.
4. Screenshot the petition image → read Section 1 (decedent) and Section 2 (petitioner)
5. If decedent state ≠ MA, scroll to page 2 Section 4 for the MA property address
6. Update the Google Sheet: G-J for decedent address, AA-AE for petitioner address/phone, AG for petitioner email (if present)

### ⚠️ DO NOT use attorney addresses
The case details page shows attorney names and addresses. These are the lawyer's office address, NOT the petitioner's home address. **Always open the petition image.**

---

## Part 2.5: Ownership Verification via ArcGIS

**Purpose:** Only include a case if the decedent (or the petitioner) owned the property where the decedent lived. This filters out renters. No bond document needed — just query ArcGIS on the decedent's address from Section 1.

---

### The check (one query per case)

After reading the petition, take the **decedent's address from Section 1** and query ArcGIS to see who owns that parcel.

**⚠️ Confirmed field names (verified against service metadata):**
- **`OWNER2` does NOT exist** in this layer — do not include it in `outFields` or `where` clauses.
- **`OWN_ADDR`, `OWN_CITY`, `OWN_STATE`, `OWN_ZIP` DO exist** and return the owner's mailing address. Always include them.
- Full set of valid useful fields: `SITE_ADDR`, `CITY`, `ZIP`, `OWNER1`, `OWN_ADDR`, `OWN_CITY`, `OWN_STATE`, `OWN_ZIP`
- **`ZIP` (property zip) is sometimes null** — use `OWN_ZIP` as a fallback if needed.
- **Do NOT use the `navigate` tool for ArcGIS queries** — it double-encodes spaces (`%20` → `%2520`). Always use `javascript_tool` with `URLSearchParams` + `fetch()`.
- **Condo buildings**: Individual unit addresses (e.g., `35 AUDUBON RD #208`) have no parcel entry — only the building-level address (`35 AUDUBON RD`) exists, typically owned by a realty trust (e.g., `35 AUDUBON REALTY TRUST`). If the trust name doesn't contain the decedent's or petitioner's last name → skip.

**Standard query pattern — use this for all ArcGIS lookups:**

```javascript
(async () => {
  const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';
  const params = new URLSearchParams({
    where: "SITE_ADDR = '29 WHITCOMB TER'",   // replace with actual address, uppercase
    outFields: 'SITE_ADDR,CITY,ZIP,OWNER1,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP',
    f: 'json',
    resultRecordCount: '5'
  });
  const r = await fetch(`${endpoint}?${params}`);
  const data = await r.json();
  return JSON.stringify(data.features?.map(f => f.attributes));
})()
```

This returns both the **property address** (`SITE_ADDR`, `CITY`, `ZIP`) and the **owner's mailing address** (`OWN_ADDR`, `OWN_CITY`, `OWN_STATE`, `OWN_ZIP`) in one query.

### Decision logic

Check the returned `OWNER1` against **both** the decedent's last name and the petitioner's last name. Also check for trust patterns — MA trusts are named after the person, the street, or both. (Note: there is no `OWNER2` field in this layer.)

| ArcGIS result | Decision |
|---------------|----------|
| `OWNER1` contains decedent's last name | ✅ **Include** — owned directly |
| `OWNER1` contains petitioner's last name | ✅ **Include** — petitioner owns it |
| `OWNER1` contains either last name + a trust suffix (e.g. `MILLETTE LIV T`, `SMITH REALTY T`, `SMITH FAMILY T`, `SMITH RT`, `SMITH REV T`, `MILLETTE TR`) | ✅ **Include** — personal trust named after them |
| `OWNER1` contains the street name or street number from the decedent's address + a trust suffix (e.g. `20 HEMENWAY RT`, `HEMENWAY COURT T`) | ✅ **Include** — realty trust named after the property |
| `OWNER1` is a completely unrelated person or entity | ❌ **Skip** — decedent was renting |
| No features returned (address not found) | → Try name-based fallback, then bond check (see below) |

**Common MA trust suffixes to recognize:** `T`, `RT`, `LIV T`, `REV T`, `REALTY T`, `FAMILY T`, `NOMINEE T`, `TR`

### If the address returns no match

**Step 1: Try a name-based fallback** using `javascript_tool` fetch:

```javascript
(async () => {
  const lastName = 'SMITH';   // decedent's last name, uppercase
  const zip = '02642';        // from Property Information box on case details page
  const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';
  const params = new URLSearchParams({
    where: `UPPER(OWNER1) LIKE '%${lastName}%' AND ZIP = '${zip}'`,
    outFields: 'SITE_ADDR,CITY,ZIP,OWNER1,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP',
    f: 'json',
    resultRecordCount: '10'
  });
  const r = await fetch(`${endpoint}?${params}`);
  const data = await r.json();
  return JSON.stringify(data.features?.map(f => f.attributes));
})()
```

If the name-based query returns a match → apply normal trust/owner logic above.

**Step 2: If name-based query also returns nothing → check the bond image (last resort)**

ArcGIS sometimes has no record for condo units or recently transferred parcels. In these cases, open the **Bond without Sureties** image from the docket and check the "Estimated Value of Real Estate" field:

- **$0.00 real estate → renter → skip the case.** Do NOT add to sheet.
- **Any dollar amount > $0 → likely owner → include the case.**

**⚠️ Do NOT use Google search results as ownership confirmation.** Finding "condos for sale at this address" only proves the building has owner-occupied units — it does NOT confirm the decedent owned one. The bond is the only reliable last-resort check.

**How to open the bond image:**

The bond is typically File Ref #6 in the docket. Use JS to find the row and click its Image link (the Image column is off-screen to the right — coordinate clicking won't work):

```javascript
// Find the Bond row and click its Image link
const bondRow = Array.from(document.querySelectorAll('tr'))
  .find(r => r.textContent.includes('Bond without Sureties'));
bondRow.querySelector('a').dispatchEvent(
  new MouseEvent('click', {bubbles: true, cancelable: true})
);
```

The bond image **opens in a new tab** — check the tabs list after clicking. Wait 3 seconds, then take a screenshot to read the "Estimated Value of Real Estate" field.

**⚠️ If queries return errors**, navigate to `https://gis.massgis.state.ma.us/arcgis/rest/services/Parcels/` in Chrome to find the current service URL.

---

## Part 3: "Filing of will of deceased no petition" Cases

These cases are filed when someone deposits a will with the court but does **not** open a full probate estate. There is no MPC petition form — the first docket image is the Will itself.

**The Will may list an old address** that the decedent no longer owned. Do not use it as the property address. Instead, use ArcGIS to find what the decedent actually owned at the time of death.

---

### Step 1: Get the zip from the Property Information box

On the case details page, look at the **top right corner** — there is a "Property Information" box showing a zip code (and sometimes city). Note this zip — it narrows the ArcGIS search to the right area.

---

### Step 2: Open the Will and read it for clues

Open the Will image from the docket (same Wicket AJAX pattern as petition images). You need to read it for two things:

1. **Named executor / personal representative** — look for:
   - "I appoint [NAME] as my Personal Representative"
   - "I nominate [NAME] as Executor"
   - "I authorize my Personal Representative to sell my property at..."
   - If multiple co-executors are named, use the first one listed
   - This person is your contact — use their name for col Z (petitioner/executor name). Col E should still be the decedent's full name (first + last).

2. **Address clues** — the Will may mention a property address (e.g., "my residence at 20 Hemenway Court"). Even if it may be outdated, note it as a hint to cross-reference with ArcGIS results.

---

### Step 3: Query ArcGIS by decedent name + zip

Use the decedent's last name and the zip from the Property Information box. This finds what they actually own regardless of what address the Will lists.

Run in Chrome via `javascript_tool`:

```javascript
(async () => {
  const lastName = 'MILLETTE'; // decedent's last name, uppercase
  const zip = '02642';         // from Property Information box on case details page
  const endpoint = 'https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query';
  const params = new URLSearchParams({
    where: `UPPER(OWNER1) LIKE '%${lastName}%' AND ZIP = '${zip}'`,
    outFields: 'SITE_ADDR,CITY,ZIP,OWNER1,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP',
    f: 'json',
    resultRecordCount: '10'
  });
  const r = await fetch(`${endpoint}?${params}`);
  const data = await r.json();
  return JSON.stringify(data.features?.map(f => f.attributes));
})()
```

Apply the same trust matching rules as Part 2.5 — the decedent's last name may appear directly in OWNER1, or via a trust suffix (`LIV T`, `RT`, `REALTY T`, etc.), or the street address may appear in a realty trust name. Cross-reference any address mentioned in the Will to confirm you have the right parcel. (Note: there is no `OWNER2` field.)

- Match found → **include**, use `SITE_ADDR` from ArcGIS for cols G-J
- No match → **skip** the case

---

### Step 4: What goes in the sheet

| Column | Value |
|--------|-------|
| C | Decedent first name |
| D | Decedent last name |
| E | **Executor name from the Will** — NOT "No Petitioner, On File" |
| G-J | Property address from ArcGIS `SITE_ADDR` (not from the Will) |
| K | Case# + " - Filing of will of deceased no petition" |
| Q | "Filing of Will" |
| R | Case number |
| Z | Executor name (same as E) |
| AA-AD | Executor mailing address — query ArcGIS by executor's last name if needed |
| AE | Blank (no petition form) |
| AG | Blank (no petition form) |

---

## Google Sheet Details

- **Sheet ID:** `1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw`
- **Tab name:** Today's pull date (e.g., `04/13/2026`)
- **URL:** `https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit`

### Full column layout (row 1 = headers, data starts row 2):

| Col | Header | Source |
|-----|--------|--------|
| A | Pull Date | Today's date |
| B | File Date | Last business day |
| C | Lead First Name | Decedent first name |
| D | Lead Last Name | Decedent last name |
| E | Lead Owner Name | Petitioner's full name (the relative/personal representative) — NOT the decedent |
| F | Lead Status | Leave blank |
| **G** | **Lead Street** | **Decedent street address (from petition Section 1)** |
| **H** | **Lead City** | **Decedent city (from petition Section 1)** |
| **I** | **Lead State** | **Decedent state (from petition Section 1)** |
| **J** | **Lead Zip** | **Decedent zip (from petition Section 1)** |
| K | Lead Notes | **Leave blank** — do NOT put case number here |
| **L** | **Customer Street** | **Petitioner mailing street** (same as Relative 1 Mailing, col AA) |
| **M** | **Customer City** | **Petitioner mailing city** |
| **N** | **Customer State** | **Petitioner mailing state** |
| **O** | **Customer Zip** | **Petitioner mailing zip** |
| **P** | **Probate Type** | **Initiating action from MassCourts** (e.g., "Formal Adjudication of Intestacy and Appointment of Personal Representative") |
| **Q** | **Probate Relationship** | **Petitioner's relationship** from petition Section 2 (see below) |
| **R** | **Probate Case #** | **Case number** (e.g., `NO26P0982EA`) |
| S | Tax Foreclosure | |
| T | Auction Date | |
| U | Campaign Lists | |
| V | Lead Record Type | |
| W | Owner 1 Phone | |
| X | Owner 1 Deceased | |
| Y | Owner 1 Notes | |
| Z | Relative 1 Name | Petitioner name (auto-filled from pull) |
| **AA** | **Relative 1 Mailing Street** | **Petitioner street (from petition Section 2)** |
| **AB** | **Relative 1 Mailing City** | **Petitioner city (from petition Section 2)** |
| **AC** | **Relative 1 Mailing State** | **Petitioner state (from petition Section 2)** |
| **AD** | **Relative 1 Mailing Zip** | **Petitioner zip (from petition Section 2)** |
| **AE** | **Relative 1 Phone** | **Petitioner phone (from petition Section 2)** |
| AF | Relative 1 CNAM | |
| **AG** | **Relative 1 Email** | **Petitioner email (from petition Section 2, if present)** |
| AH | Relative 1 Relationship | |
| AI | Relative 1 Notes | |
| **AJ** | **Relative 2 Name** | **Second petitioner name (if present)** |
| **AK** | **Relative 2 Mailing Street** | **Second petitioner street** |
| **AL** | **Relative 2 Mailing City** | **Second petitioner city** |
| **AM** | **Relative 2 Mailing State** | **Second petitioner state** |
| **AN** | **Relative 2 Mailing Zip** | **Second petitioner zip** |
| **AO** | **Relative 2 Phone** | **Second petitioner phone** |
| **AP** | **Relative 2 Email** | **Second petitioner email** |

### Key data mapping:
- **Petition Section 1 (Decedent info)** → columns G, H, I, J (decedent address)
- **Petition Section 2 (Petitioner 1)** → columns AA, AB, AC, AD (mailing address), AE (phone), AG (email), **also L, M, N, O** (same petitioner mailing address duplicated)
- **Petition Section 2 (Petitioner 2, if present)** → columns AJ–AP (name, street, city, state, zip, phone, email). Leave blank if only one petitioner.
- **Petition Section 2 relationship field** → column Q (Probate Relationship)
- **MassCourts initiating action** → column P (Probate Type)
- **Case number** → column R only (do NOT also put it in K)
- **DO NOT use attorney addresses** from the case details page — those are the lawyer's office, not the petitioner's home

### Case number prefixes → Counties:
`BA` = Barnstable, `BR` = Bristol, `DU` = Dukes, `ES` = Essex, `FR` = Franklin, `HD` = Hampden, `HS` = Hampshire, `MI` = Middlesex, `NO` = Norfolk, `SU` = Suffolk, `WO` = Worcester

### Navigating to cells:
Use URL fragment navigation — most reliable method:
```
https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit#gid={GID}&range=G2
```
**Wait at least 4 seconds** after URL navigation before typing. Take a screenshot to confirm the cell is selected.

### Typing into cells:
- Type the value, then press `Tab` (as a separate key action) to move to the next column
- **DO NOT embed tab characters in the type text** — they become literal tabs inside the cell
- Press `Enter` after the last value in a row to confirm

### Zip codes with leading zeros:
Massachusetts zip codes start with 0. Prefix with apostrophe: `'02356` — Sheets treats it as text and displays `02356`.

---

## MassCourts.org Navigation

### Critical: Session-bound URL tokens
MassCourts uses one-time server-side session tokens in `?x=...` URL parameters:
- Never navigate directly to a `?x=` URL from outside the session
- Always navigate *within* the active session using `window.location.href = '?x=...'` or `.click()`
- Tokens expire after use

---

### ✅ WORKING SEARCH WORKFLOW (Verified 04/12/2026)

MassCourts updated to `search.page.79`. The **Name** tab requires a last name, but the **Case Type** tab does NOT — use it to search by file date + case type with no name needed.

**Step 1: Navigate to the search page**
```
https://www.masscourts.org/eservices/search.page.79
```
The page loads the court selector (Department / Division / Location) AND the full search form on the same page. If the dropdowns are already set to the right county, skip to Step 3.

**Step 2: Select the court (if needed)**

The department/division/location dropdowns are in `form#id1e02`. Change them if needed:
- Department → Probate and Family Court (`PF_DEPT`)
- Division → e.g. Middlesex County Probate and Family Court (`PF09_DIV`)
- Location → auto-populates after selecting division

After selecting Location, trigger its `onchange` (Wicket AJAX) to reload the search form for that court.

**Step 3: Click the "Case Type" search tab**

The search form has tabs: **Name | Case Type | Case Number | Ticket/Citation #**

The **Name** tab requires a last name. Click **Case Type** instead — it loads a form with ONLY: File Date Range, Case Type, City, Status, Party Type — **no name required**.

⚠️ **Wicket tab navigation — always use `MouseEvent` dispatch, NOT `.click()`**

MassCourts tabs are Wicket AJAX components. A plain `.click()` call does NOT reliably trigger the tab switch (coordinate-based clicks also fail due to DPR mismatch). Always dispatch a full `MouseEvent`. This applies to ALL tabs on the search form (Case Type, Case Number, etc.):

```javascript
// General pattern for switching ANY tab on MassCourts (verified 04/14/2026):
const tab = Array.from(document.querySelectorAll('a'))
  .find(a => a.textContent.trim() === 'Case Type'); // swap name for other tabs
tab.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
// Verify the form switched:
// Case Type tab   → document.querySelector('select[name="caseCd"]') should exist
// Case Number tab → document.querySelector('input[name="caseDscr"]') should exist
```

> **Tip:** If you accidentally submit the Name form first and get "Last Name and First Name or Company Name is required", the page refreshes to `search.page.79.1`. The Case Type tab is still there — dispatch MouseEvent on it again.

**Step 4: Set Case Type + File Date, then submit**

```javascript
// Set Case Type = Estates and Administration
const caseTypeSelect = document.querySelector('select[name="caseCd"]');
Array.from(caseTypeSelect.options).forEach(o => o.selected = false);
const ea = Array.from(caseTypeSelect.options).find(o => o.value.trim() === 'EA');
if (ea) ea.selected = true;

// Set File Date Range (use the file date you are searching for)
document.querySelector('input[name="fileDateRange:dateInputBegin"]').value = '04/10/2026';
document.querySelector('input[name="fileDateRange:dateInputEnd"]').value = '04/10/2026';

// Submit
document.querySelector('input[type="submit"][name="submitLink"]').click();
```

**Step 5: Read results**

Results load in the same tab at `searchresults.page`. Use `get_page_text`. Each case appears multiple times (once per party). Filter to unique case numbers; skip Voluntary Statement initiating actions.

**Paginate through all pages:**
```javascript
// Find page 2, 3, etc. link by its text:
const page2 = Array.from(document.querySelectorAll('a[id]'))
  .find(a => a.textContent?.trim() === '2');
if (page2) page2.click();
// Then get_page_text again
```

**Repeat for each county:** Go back to `search.page.79`, change Division to the next county, click the Location dropdown to trigger reload, then redo Steps 3–5.

---

### Searching by case number (for individual lookups)

Use the **"Case Number" tab** on the search form — switch to it using the MouseEvent dispatch pattern (see Step 3 above), then fill the `caseDscr` input and submit. This is the fastest way to pull up a specific case.

```javascript
// Step 1: Switch to Case Number tab (MouseEvent dispatch required — plain .click() won't work)
const caseNumTab = Array.from(document.querySelectorAll('a'))
  .find(a => a.textContent.trim() === 'Case Number');
caseNumTab.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));

// Step 2: Verify the tab switched — input named 'caseDscr' should now exist
// Then fill and submit:
document.querySelector('input[name="caseDscr"]').value = 'ES26P1080EA';
document.querySelector('input[type="submit"][name="submitLink"]').click();
```

The search results page shows the **Initiating Action** column directly — record that value for column **P (Probate Type)**.

Then click the case number link to open Case Details, and use the Image link workaround (above) to open the petition PDF and read the petitioner relationship for column **Q (Probate Relationship)**.

**IMPORTANT (fallback):** The submit button ID is `idf9f` on some versions of the page (NOT `id148e` as previously documented). If the above selector fails, try `document.getElementById('idf9f').click()`.

### Switching Court Division (county)

**We search 11 counties only. Skip Berkshire, Hampden, and Hampshire.**

Before searching, make sure the correct county is selected in the dropdown:

```javascript
// Court Division dropdown values (11 counties we search):
// BA = PF02_DIV, BR = PF04_DIV, DU = PF05_DIV, ES = PF06_DIV
// FR = PF01_DIV, MI = PF09_DIV, Nantucket = PF10_DIV
// NO = PF11_DIV, Plymouth = PF12_DIV, SU = PF13_DIV, WO = PF14_DIV
//
// SKIP: Berkshire (PF03_DIV), Hampden (PF07_DIV), Hampshire (PF08_DIV)
const selects = document.querySelectorAll('select');
const divSel = Array.from(selects).find(s =>
  Array.from(s.options).some(o => o.text.includes('Middlesex'))
);
divSel.value = 'PF09_DIV  ';  // Note trailing spaces
divSel.dispatchEvent(new Event('change', {bubbles: true}));
```

### Navigating to case details from search results

```javascript
const links = Array.from(document.querySelectorAll('a'));
const caseLink = links.find(a => a.textContent.includes('BR26P0808EA'));
if (caseLink) {
  window.location.href = caseLink.getAttribute('href');
}
```

If search returns "No Records Found," the case may be too newly filed to be indexed.

### Navigating away from case details

**CRITICAL:** Case details pages have a `beforeunload` handler that blocks navigation. You MUST disable it first:

```javascript
window.onbeforeunload = null;
const searchLink = Array.from(document.querySelectorAll('a'))
  .find(a => a.textContent.trim() === 'Search');
window.location.href = searchLink.getAttribute('href');
```

### Opening the petition image

From the case details page, find the petition docket entry (usually entry #1, "Petition for Informal Probate" or "Petition for Formal Probate") and check its docket row for an "Image" link.

**Check for image availability first:**
Each docket row has 4 `<td>` cells. The 4th cell either contains:
- `<a href="...">Image</a>` → image is available, click it
- `<span>\n</span>` (empty span) → **no image publicly available**. Leave address/phone/email fields blank for this case and move on.

Docket image links use Wicket AJAX framework:
```javascript
// Find and click the image link — the ID varies per case
document.getElementById('id1798').click();
```
This opens the PDF/image in a new tab. Check `tabs_context_mcp` to get the new tab's ID.

**⚠️ Image column is off-screen to the right (verified 04/14/2026):** The "Image Avail." column renders at CSS x ≈ 2157px, which is outside the visible viewport (1568px wide). You cannot see or click it directly. Use this JS workaround instead — it clicks the first Image link regardless of where it renders:

```javascript
// Click the petition image (File Ref 1 — first Image link in the docket)
const imageAnchors = Array.from(document.querySelectorAll('a'))
  .filter(a => a.textContent.trim() === 'Image');
if (imageAnchors.length > 0) {
  const evt = new MouseEvent('click', {bubbles: true, cancelable: true, view: window});
  imageAnchors[0].dispatchEvent(evt);
}
// For the 2nd or 3rd document: use imageAnchors[1], imageAnchors[2], etc.
```

After dispatching the click, call `tabs_context_mcp` to find the new tab that opened with the PDF.

**⚠️ Sub-tab navigation warning:** The "Party", "Docket", and "Disposition" sub-tab links use session-bound `?x=` tokens that expire. Clicking them after a delay will give an Internal Server Error. Only "All Information" (the default case details page) is safe. If you need to re-read the docket, navigate fresh from `search.page`.

### Reading case details
Use `get_page_text` on the case details page — returns all content including the full docket.

### Scrolling PDF to page 2
```
scroll at coordinate (400, 400), direction: down, amount: 3
// Tab must be at least 1568px wide
```

### ⚠️ PDF scroll freeze (Claude extension active)

When the Claude browser extension is active in the tab group, Chrome's built-in PDF viewer freezes and scroll does not work. Use this blob URL workaround to open the PDF in a clean new tab with no extension overlay:

```javascript
// Run this in the PDF tab — fetches bytes and opens in new unextended tab
(async () => {
  const response = await fetch(window.location.href);
  const buf = await response.arrayBuffer();
  const blob = new Blob([new Uint8Array(buf)], {type: 'application/pdf'});
  window.open(URL.createObjectURL(blob), '_blank');
  return 'PDF: ' + buf.byteLength + ' bytes';
})()
```

The new tab has no extension overlay and can be scrolled normally.

---

## Fast Data Entry via Clipboard (Recommended for Bulk Row Entry)

When all cases have been researched, use the **clipboard trick** to paste entire rows at once.

### How it works

`document.execCommand('copy')` requires a user gesture (transient activation). Calling it from plain CDP JS fails. Fix: register a click handler **before** clicking, so the copy runs *inside* the click event, which counts as a valid gesture.

### Pasting a full row (35 columns)

```javascript
// Build tab-separated row — blank strings for unused columns
// Column order: A B C D E F G H I J K L M N O P Q R S T U V W X Y Z AA AB AC AD AE AF AG AH AI
const row = [
  "04/13/2026",          // A Pull Date
  "04/10/2026",          // B File Date
  "Francis",             // C Lead First Name
  "Carotenuto",          // D Lead Last Name
  "Laura A. Carotenuto", // E Lead Owner Name
  "",                    // F Lead Status (blank)
  "10 Devon Dr APT 327", // G Lead Street
  "Acton",               // H Lead City
  "MA",                  // I Lead State
  "01720",               // J Lead Zip
  "MI26P1775EA - Informal Probate of Will w/ Appointment of PR", // K Lead Notes
  "","","","","",        // L-P Customer address + Sales Date (blank)
  "Informal Probate",    // Q Probate Type
  "MI26P1775EA",         // R Probate Case #
  "","","","","","","",  // S-Y blank
  "Laura A. Carotenuto", // Z Relative 1 Name
  "1314 Gilman Rd",      // AA Rel1 Mailing Street
  "Hinesburg",           // AB Rel1 Mailing City
  "VT",                  // AC Rel1 Mailing State
  "05461",               // AD Rel1 Mailing Zip
  "(802) 598-7270",      // AE Rel1 Phone
  "",                    // AF Rel1 CNAM (blank)
  "lauracarotenuto69@gmail.com", // AG Rel1 Email
  "","",                 // AH-AI blank
].join('\t');

window._ch = function() {
  document.removeEventListener('click', window._ch, true);
  const ta = document.createElement('textarea');
  ta.value = row;
  ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0.01;z-index:9999';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  window._clipOk = document.execCommand('copy');
  document.body.removeChild(ta);
};
document.addEventListener('click', window._ch, true);
```

Then navigate to the target row's A cell via URL (`#gid={GID}&range=A2`), wait 3 sec, click at the cell to trigger the handler, verify `window._clipOk === true`, then `Ctrl+V`.

> **Row CSS coordinates:** Row 1 ≈ y=153, row 2 ≈ y=162, row 3 ≈ y=171. Column A center ≈ x=96. Each row is ~9px in CSS.

---

## Apps Script: Bulk Data Entry (All Cases at Once)

When all cases for a day have been researched, Apps Script can write the entire batch in one shot — faster than row-by-row clipboard entry.

### How to open Apps Script

In the Google Sheet tab: **Extensions → Apps Script**. This opens the Apps Script editor in a new tab.

**Saved project:** `1ptroIio5F4VJXcSWh-XxxXyeNDIEnHEynWygyLcw1nGJYIZd4EcPyM3x`
Direct URL: `https://script.google.com/home/projects/1ptroIio5F4VJXcSWh-XxxXyeNDIEnHEynWygyLcw1nGJYIZd4EcPyM3x/edit`

### ⚠️ Setting the script content

**Monaco IS accessible** via `monaco.editor.getEditors()[0].getModel().setValue(newCode)` when run from a `javascript_tool` call in the Apps Script tab itself. This is the fastest method — no clipboard tricks needed.

```javascript
// Inject code directly into the Monaco editor
const newCode = `function myFunction() { ... }`;
monaco.editor.getEditors()[0].getModel().setValue(newCode);
```

Then `Ctrl+S` to save. Before clicking Run, **verify the function dropdown** shows your new function name — if it still shows a deleted function name, the run will fail with "Attempted to execute X, but it was deleted." Update the dropdown first.

**Fallback — clipboard trick** (if Monaco inject fails):
1. In the Apps Script tab, set up the click handler to write the full script to clipboard (same pattern as above)
2. Click in the Monaco editor area — this triggers the handler and writes to clipboard
3. The Monaco editor should now have focus from the click
4. Press `Ctrl+A` then `Ctrl+V` via the computer tool

After pasting the script, press `Ctrl+S` to save, then click **Run**.

---

## Apps Script: Batch Address Sweep (Filling Existing Rows)

This is a different pattern from bulk data entry. Use this when rows are already in the sheet (from a prior daily list pull) but columns G–J and AA–AE are blank and need to be filled from petition PDFs.

**The lookup column is R (Probate Case #), not D or K.**

### Petitioner address goes in TWO places — always fill both:
- **Customer Street/City/State/Zip (L–O)** — petitioner mailing address
- **Relative 1 Mailing Street/City/State/Zip (AA–AD)** — same data, different columns

Prior scripts that only filled one of these leave the sheet half-populated. A complete sweep script writes both in a single pass.

### Sheet may be missing some MassCourts cases
The Google Sheet tab is populated by a separate automated process. It may not include every case that MassCourts shows for that date — this is normal. When doing a sweep, if a case number isn't found in column R, log it as "NOT FOUND" and skip it. Don't treat missing rows as errors.

### No Images tab: expected behavior
- After a sweep, **append** any `NO_IMAGES_STILL` cases to the No Images tab (check for duplicates first using column A)
- When processing brand-new cases, **zero deletions from the No Images tab is correct** — new cases were never in that tab to begin with
- Only delete from No Images when re-sweeping cases that were previously added there and now have images

### GID reference (verify before hardcoding — digits transpose easily):
| Tab | GID |
|-----|-----|
| 04/17/2026 | 553535089 |
| No Images | 507801610 |

Always confirm with: `Logger.log(ss.getSheets().map(s => s.getName()+" gid:"+s.getSheetId()))`

### Batch sweep script template:

```javascript
function batchAddressSweep() {
  var ss = SpreadsheetApp.openById("1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw");
  var dailySheet = ss.getSheetByName("04/17/2026"); // update date
  var noImagesSheet = ss.getSheetByName("No Images");

  var headers = dailySheet.getDataRange().getValues()[0];
  var colMap = {};
  headers.forEach(function(h, i) { colMap[(h||"").toString().trim()] = i; });
  var caseNumCol = colMap["Probate Case #"];

  // Column indices (1-based for getRange):
  // G=7 H=8 I=9 J=10  L=12 M=13 N=14 O=15  Z=26 AA=27 AB=28 AC=29 AD=30 AE=31

  var cases = [ /* embed your collected case data here */ ];
  var dailyData = dailySheet.getDataRange().getValues();

  cases.forEach(function(c) {
    if (c.status !== "COMPLETE") return;
    var row = -1;
    for (var r = 1; r < dailyData.length; r++) {
      if ((dailyData[r][caseNumCol]||"").toString().trim() === c.caseNum) { row = r; break; }
    }
    if (row < 0) { Logger.log("NOT FOUND: " + c.caseNum); return; }
    var R = row + 1;
    dailySheet.getRange(R, 7).setValue(c.propertyStreet || "");    // Lead Street
    dailySheet.getRange(R, 8).setValue(c.propertyCity || "");      // Lead City
    dailySheet.getRange(R, 9).setValue(c.propertyState || "");     // Lead State
    dailySheet.getRange(R, 10).setValue(c.propertyZip || "");      // Lead Zip
    dailySheet.getRange(R, 12).setValue(c.petitionerStreet || ""); // Customer Street
    dailySheet.getRange(R, 13).setValue(c.petitionerCity || "");   // Customer City
    dailySheet.getRange(R, 14).setValue(c.petitionerState || "");  // Customer State
    dailySheet.getRange(R, 15).setValue(c.petitionerZip || "");    // Customer Zip
    dailySheet.getRange(R, 26).setValue(c.petitionerName || "");   // Relative 1 Name
    dailySheet.getRange(R, 27).setValue(c.petitionerStreet || ""); // Relative 1 Mailing Street
    dailySheet.getRange(R, 28).setValue(c.petitionerCity || "");   // Relative 1 Mailing City
    dailySheet.getRange(R, 29).setValue(c.petitionerState || "");  // Relative 1 Mailing State
    dailySheet.getRange(R, 30).setValue(c.petitionerZip || "");    // Relative 1 Mailing Zip
    dailySheet.getRange(R, 31).setValue(c.petitionerPhone || "");  // Relative 1 Phone
    Logger.log("Updated: " + c.caseNum);
  });

  // Append NO_IMAGES_STILL cases (dedup check)
  var niData = noImagesSheet.getDataRange().getValues();
  var existingNI = {};
  for (var i = 1; i < niData.length; i++) {
    existingNI[(niData[i][0]||"").toString().trim()] = true;
  }
  cases.forEach(function(c) {
    if (c.status !== "NO_IMAGES_STILL") return;
    if (!existingNI[c.caseNum]) {
      noImagesSheet.appendRow([c.caseNum, c.county, (c.decedentFirst||"")+" "+(c.decedentLast||""), "NO_IMAGE", "", ""]);
      Logger.log("Added to No Images: " + c.caseNum);
    }
  });
  Logger.log("Done.");
}
```

### Script template

The sheet has 42 columns (A–AP). The `r()` helper function maps the sparse columns:

```javascript
function writeProbateData() {
  var ss = SpreadsheetApp.openById("1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw");
  // Find the tab by sheet ID (each dated tab has a unique numeric ID)
  var sheet = ss.getSheets().find(function(s){ return s.getSheetId() === TAB_SHEET_ID; });
  sheet.getRange(2, 1, sheet.getLastRow() + 1, 42).clearContent();

  // r() args: a=PullDate, b=FileDate, c=FirstName, d=LastName, e=OwnerName,
  //           g=Street, h=City, i=State, j=Zip, k=Notes,
  //           q=ProbateType, rr=CaseNum,
  //           z=Rel1Name, aa=Rel1Street, ab=Rel1City, ac=Rel1State,
  //           ad=Rel1Zip, ae=Rel1Phone, ag=Rel1Email,
  //           aj=Rel2Name, ak=Rel2Street, al=Rel2City, am=Rel2State,
  //           an=Rel2Zip, ao=Rel2Phone, ap=Rel2Email
  // Cols F, L-P, S-Y, AF, AH-AI are left blank ("").
  // Rel2 cols (AJ-AP) are blank when only one petitioner.
  function r(a,b,c,d,e,g,h,i,j,k,q,rr,z,aa,ab,ac,ad,ae,ag,aj,ak,al,am,an,ao,ap){
    aj=aj||""; ak=ak||""; al=al||""; am=am||""; an=an||""; ao=ao||""; ap=ap||"";
    return [a,b,c,d,e,"",g,h,i,j,k,"","","","","",q,rr,"","","","","","","",z,aa,ab,ac,ad,ae,"",ag,"","",aj,ak,al,am,an,ao,ap];
  }

  var main = [
    r("MM/DD/YYYY","MM/DD/YYYY","First","Last","Petitioner Name","Street","City","MA","Zip","CaseNum - Initiating Action","Probate Type","CaseNum","Petitioner Name","Street","City","MA","Zip","(###) ###-####","email@example.com"),
    // For a case with two petitioners, pass all 7 Rel2 args:
    // r(...19 args...,"Rel2 Name","Rel2 Street","Rel2 City","MA","Rel2 Zip","(###) ###-####","rel2@email.com"),
    // ... more rows
  ];

  sheet.getRange(2, 1, main.length, 42).setValues(main);
  // Preserve leading zeros on zip code columns (J=10, AD=30, AN=40)
  sheet.getRange(2, 10, main.length, 1).setNumberFormat("@");
  sheet.getRange(2, 30, main.length, 1).setNumberFormat("@");
  sheet.getRange(2, 40, main.length, 1).setNumberFormat("@");

  // No Images tab
  var niSheet = ss.getSheetByName("No Images") || ss.insertSheet("No Images");
  niSheet.getRange(1, 1, 1, 42).setValues([sheet.getRange(1, 1, 1, 42).getValues()[0]]);
  var noImg = [
    r("MM/DD/YYYY","MM/DD/YYYY","First","Last","Petitioner Name","","","","","CaseNum - Initiating Action","Probate Type","CaseNum","Petitioner Name","","","","","",""),
    // ... more no-image rows
  ];
  niSheet.getRange(2, 1, noImg.length, 42).setValues(noImg);

  SpreadsheetApp.flush();
  Logger.log("Done: " + main.length + " main rows, " + noImg.length + " no-image rows");
}
```

**Finding the tab's sheet ID:** Navigate to the dated tab in Google Sheets; the `#gid=XXXXXXXX` in the URL is the numeric sheet ID to use for `getSheetId() === XXXXXXXX`.

### Running the script

Click **Run** in the toolbar. Two dialogs may appear on first run:

1. **"You're currently signed in as jrabb@joehomebuyer.com"** panel (right side) — find and click the **OK** button:
   ```javascript
   const okBtn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'OK');
   const rect = okBtn.getBoundingClientRect();
   // click at {x: rect.left + rect.width/2, y: rect.top + rect.height/2}
   ```

2. **"Authorization required"** dialog — click **"Review permissions"**, then complete the Google OAuth flow to grant Sheets access. This only happens once per script project.

After authorization, subsequent runs execute immediately. Check the **Execution log** at the bottom for: `Info: Done: N main rows, N no-image rows`.

---

## Skipped Tab & Late-Case Detection

### When to write to the Skipped tab
Any case that was looked up on MassCourts but deliberately not added to the daily sheet must go in the **Skipped** tab. This includes:
- ArcGIS confirmed renting (unrelated owner, bond $0, no match)
- Voluntary Statement cases (small estate affidavits — never qualify)
- Any other intentional pass

**Columns to write:**

| Column | Value |
|--------|-------|
| A | Case Number |
| B | County |
| C | Decedent Name |
| D | Skip Reason (e.g., "Renting - ArcGIS unrelated owner", "Voluntary Statement", "Bond $0") |
| E | File Date |

Without this, there's no way to distinguish "we skipped this" from "this appeared late on MassCourts."

---

### 2-Week Late-Case Sweep

Run this at the start of each daily list to catch cases that were uploaded to MassCourts after the original pull date.

**For each date being re-swept:**
1. Pull all cases from MassCourts for that date
2. For each case number, check all three tabs:
   - **Any dated daily tab** → already processed and qualified, skip
   - **Skipped tab** → deliberately passed on, skip
   - **No Images tab** → was waiting; check if an image has appeared now
3. **No Images cases with a new image** → open the petition, extract addresses, move the row to the dated tab, remove from No Images
4. **Case not found in any tab** → genuinely late-appearing case; run it through the full workflow (ArcGIS check, petition PDF, etc.)

After 2 weeks of re-sweeping with no image appearing, delete the case from the No Images tab.

---

## Common Issues & Fixes

| Problem | Fix |
|---------|-----|
| Internal Server Error on `?x=` URL | Navigated to session token from outside session. Go back to search and start fresh. |
| "Leave site?" dialog blocks navigation | Set `window.onbeforeunload = null` before navigating away from case details. |
| Clicking case link in results doesn't work | Use `window.location.href = link.getAttribute('href')` instead of clicking coordinates. |
| Submit button doesn't work | Button ID is `idf9f`, NOT `id148e`. |
| "Case Number" tab won't switch | Don't bother — `caseDscr` hidden input works regardless of active tab. |
| Case not found on MassCourts | May be too newly filed. Note as "No Records Found" and skip. |
| Zip code loses leading zero | Prefix with apostrophe: type `'02356` to display as `02356`. |
| Tab character lands in cell as text | Use separate `key: Tab` actions between typed values, not embedded `\t` in type text. |
| Clipboard paste doesn't work from plain JS | `execCommand('copy')` and `navigator.clipboard.writeText()` both require a user gesture (transient activation) and fail when called from plain CDP JS. Fix: register a click handler first, then click — the copy runs inside the click event and works. See "Fast Data Entry via Clipboard" section above. |
| Typing after URL nav doesn't register | Wait 4+ seconds AND screenshot to confirm cell is selected before typing. |
| Ctrl+F doesn't open search in Google Sheets | Use URL range navigation instead. |
| PDF scroll freezes / Claude extension active | The Chrome extension overlay freezes the PDF viewer. Use the blob URL workaround: fetch the PDF bytes via JS, create a blob URL, open it in a new tab (no extension overlay). See blob URL code below. |
| PDF scroll doesn't work normally | Scroll at (400, 400). Tab must be 1568px+ wide. |
| Sub-tab links (Docket / Party / Disposition) give Internal Server Error | Session token is stale. These sub-tab `?x=` links expire. Only "All Information" tab (the default page load) is safe. If you need docket data, re-navigate from fresh search. |
| No petition image available | Copy row to "No Images" tab. Re-check at the start of each daily list run for up to 2 weeks. If image appears, move row back to dated tab and fill in addresses. Delete after 2 weeks with no image. |
| New PDF tab doesn't appear immediately | Do another action then check `tabs_context_mcp`. |
| Multiple rows with same case number | Verify by county (col C) and name (cols F/G). |

---

## Quick Reference: Full Workflow per Case

```
1. Get case number from sheet column K (e.g., BR26P0808EA)

2. Go to masscourts.org search
   → Switch Court Division dropdown if needed
   → document.getElementById('caseDscr').value = 'BR26P0808EA'
   → document.getElementById('idf9f').click()

3. Navigate to case details
   → Find case link in results, use window.location.href
   → Use get_page_text to read case details and docket

4. Open the petition image
   → Find "Petition for Informal/Formal Probate" docket entry
   → Click its "Image" link (Wicket AJAX, opens new tab)
   → Take screenshot of petition

5. Read the petition (MPC 160 or MPC 3019 form):
   → Section 1: Decedent address → Lead Street/City/State/Zip (G-J)
   → Section 2: Petitioner address → Relative 1 Mailing Street/City/State/Zip (AA-AD)
   → Section 2: Petitioner phone → Relative 1 Phone (AE)
   → Section 2: Petitioner email → Relative 1 Email (AG) [if present]
   → Section 4 (page 2): If out-of-state, use MA property address for Lead Street instead

6. Query ArcGIS on the decedent's Section 1 address (run in Chrome javascript_tool):
   → OWNER1/OWNER2 contains decedent's OR petitioner's last name → INCLUDE
   → OWNER1/OWNER2 looks like their trust → INCLUDE
   → Unrelated owner, or no result → try name-based fallback query
   → Still nothing → SKIP (renting), do not add to sheet

7. Navigate back to search (set window.onbeforeunload = null first)

8. In Google Sheet (only for included cases):
   → Navigate via URL #gid={gid}&range=G{row}
   → Wait 4 sec, verify cell, type decedent address (or ArcGIS address if Case B)
   → Tab to H, I, J for city/state/zip
   → Navigate to AA{row} for petitioner address
   → Type street, Tab, city, Tab, state, Tab, 'zip, Tab, phone
   → Navigate to AG{row} for petitioner email (if found on petition)
```

---

## Notes

The assistant working on this project goes by **Seth**.
