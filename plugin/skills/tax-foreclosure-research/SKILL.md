---
name: tax-foreclosure-research
description: |
  Use this skill to research open MA Land Court tax lien cases and flag the best leads.
  Trigger for any of these:
  - "research the foreclosure cases", "flag interesting cases", "look up cases on masscourts"
  - "which cases are worth pursuing", "run the foreclosure research", "check the tax lien sheet"
  - Any request to investigate, score, or prioritize cases from the Open Tax Foreclosures spreadsheet
  
  This skill reads all cases from the Google Sheet, identifies unresearched candidates,
  looks them up on MassCourts.org, and flags HOT / WARM / WATCH leads based on
  distress signals (fractured ownership, missing defendants, deceased owners, no responses, etc.)
---

# Tax Foreclosure Research

**Run directly in the current conversation — do NOT spawn subagents.** All browser tools must be called from this conversation to maintain the MassCourts.org session.

---

## Overview

The goal is to find MA Land Court tax lien cases where the property owners are unreachable, deceased, or have fractured/heir ownership — situations where nobody is defending the case and the title is distressed enough that Joe Homebuyer can step in, solve the title problem, and buy at a discount.

The research has two phases:
1. **Pull candidates from the Google Sheet** — filter down to cases worth investigating
2. **Look up each candidate on MassCourts.org** — extract signals that indicate distress

---

## Phase 1 — Pull Candidates from the Google Sheet

**Spreadsheet ID:** `1JILovPzYUFTCqb1j2vC1Yj_-ioruvCh7jKgtRuVGHIY`

### Step 1 — Fetch all sheet data via the gviz endpoint

Navigate to the Google Sheet in a Chrome tab (or use one that's already open). Then use `javascript_tool` to fetch all tabs. This endpoint works when the user is already logged into Google:

```javascript
(async () => {
  const id = '1JILovPzYUFTCqb1j2vC1Yj_-ioruvCh7jKgtRuVGHIY';
  const tabNames = ["2011","6/2015","7/2015","8/2018","10/2018","11/2018","1/2019",
    "2/2019","3/2019","4/2019","06/2019","07/2019","08/2019","10/2019","11/2019",
    "1/2021","2/2021","3/2021","4/2021","5/2021","6/2021","12/2021","2/2022",
    "3/2022","4/2022","5/2022","6/2022","7/2022","8/2022","9/2022","10/2022",
    "11/2022","12/2022","1/2023","2/2023","3/2023","4/2023","1/2024","2/2024",
    "3/2024","4/2024","5/2024","6/2024","7/2024","8/2024","10/2024","11/2024","12/2024"];
  
  const results = {};
  for (const name of tabNames) {
    const resp = await fetch(`https://docs.google.com/spreadsheets/d/${id}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(name)}`);
    results[name] = await resp.text();
  }
  window._allSheetData = results;
  return Object.keys(results).length + ' tabs loaded';
})()
```

### Step 2 — Parse and filter candidates

Each row has these columns (0-indexed):
- 0: town (plaintiff municipality)
- 1: case number (e.g. "24 TL 000531")
- 2: case type ("Tax Lien")
- 3: date filed
- 4: description ("Tax Lien - one tax taking" or "two tax takings")
- 5: role ("Plaintiff")
- 6: empty
- 7: status ("Open" or "Reopen (RO)")
- 8: court ("Land Court Department")
- 9-12: notes columns (Jordan's research notes)

**Dead-end tags** — skip any case where the combined notes contain these phrases (they've already been evaluated and dismissed):
```
useless, answer filed, lives there, payment agreement, useless land,
out of buy box, out of buy area, outside buy area, sold, withdrawn,
not interested, complaint withdrawn, bank owned, heir lives there,
heirs live there, live there, living there, bk, juice not worth squeeze,
using lot, answer
```

**Candidate tiers** (research in this order):

1. **Priority 1 — Explicitly flagged**: notes contain "really good", "good one", "really interesting", "very interesting"
2. **Priority 2 — Backburner + two takings**: notes contain "backburner" AND description contains "two tax takings"
3. **Priority 3 — No notes, recent (2023–2024)**: completely unresearched, filed in last 2 years
4. **Priority 4 — No notes, older**: unresearched cases from earlier tabs
5. **Priority 5 — Backburner only**: notes contain "backburner" or "monitor" (Jordan thought these were interesting but deprioritized)

Parse with this JavaScript:
```javascript
function parseCSV(csv) {
  const rows = [];
  const lines = csv.split('\n').filter(l => l.trim());
  for (const line of lines) {
    const cols = [];
    let cur = '', inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (c === '"' && !inQ) inQ = true;
      else if (c === '"' && inQ) inQ = false;
      else if (c === ',' && !inQ) { cols.push(cur); cur = ''; }
      else cur += c;
    }
    cols.push(cur);
    rows.push(cols);
  }
  return rows;
}

const deadEnds = ['useless','answer filed','lives there','payment agreement',
  'useless land','out of buy box','out of buy area','outside buy area','sold',
  'withdrawn','not interested','complaint withdrawn','bank owned','heir lives there',
  'heirs live there','live there','living there','bk','juice not worth squeeze',
  'using lot','answer'];

const allCases = [];
for (const [tab, csv] of Object.entries(window._allSheetData)) {
  parseCSV(csv).forEach(cols => {
    const notes = [cols[9],cols[10],cols[11],cols[12]].filter(n=>n).join(' ').toLowerCase();
    const isDead = deadEnds.some(d => notes.includes(d));
    if (!isDead) {
      allCases.push({
        tab, town: cols[0], caseNum: cols[1], date: cols[3],
        desc: cols[4], status: cols[7], notes
      });
    }
  });
}
window._candidates = allCases;
allCases.length + ' candidates (dead ends removed)';
```

---

## Phase 2 — MassCourts.org Case Lookup

### Navigation pattern

**⚠️ CRITICAL: Always use spaces in case numbers.** Enter "24 TL 000531" not "24TL000531" — without spaces returns no results.

**Step 1 — Set up Land Court search:**
```javascript
// Navigate to masscourts.org/eservices/search.page first, then:
const sel = document.querySelector('select[name="sdeptCd"]');
sel.value = 'LC_DEPT   ';  // Note: trailing spaces are required
sel.dispatchEvent(new Event('change', {bubbles: true}));
document.querySelector('form').submit();
```

**Step 2 — Get Case Number tab URL** (after form submits and page reloads):
```javascript
const link = Array.from(document.querySelectorAll('a')).find(a => a.textContent.trim() === 'Case Number');
window._caseNumTabUrl = link ? link.href : null;
link ? 'got url' : 'not found';
```
Navigate directly to that URL.

**Step 3 — Search for a case:**
```javascript
const i = document.querySelector('input[type="text"]');
i.value = '24 TL 000531';  // Always with spaces
i.dispatchEvent(new Event('input', {bubbles: true}));
Array.from(document.querySelectorAll('input[type="submit"]')).find(b => b.value === 'Search').click();
```
Wait 3 seconds, then get the case detail link from results:
```javascript
const link = Array.from(document.querySelectorAll('a')).find(a => a.textContent.match(/\d+ TL \d+/));
link ? link.href : 'no results';
```
Navigate to that link.

**Step 4 — Read the full case page:**
```javascript
document.body.innerText  // Read everything at once
```

**⚠️ Session links expire fast.** The `?x=` tokens in MassCourts URLs are single-use and expire quickly. If you navigate away and try to come back to a case detail URL, you'll get a server error. Always re-search for the next case rather than trying to reuse old links.

**⚠️ After researching each case, go back to the search page fresh:**
Navigate to `https://www.masscourts.org/eservices/search.page`, select Land Court, submit, get the Case Number tab URL again. The session resets between cases.

---

## What to Extract from Each Case

Read `document.body.innerText` and pull these key pieces:

### Header information
- **Case title**: "18 TL 001617 Town of Westford v. Healy, Marion S., et al."
- **Status**: Open
- **File date**: When the complaint was filed
- **Property address**: Listed in top-right "Property Information" box — street + town + RECORD/REGISTER
- **Case judge**: If blank, no judge assigned yet (early stage — good)
- **Next event**: If blank, nothing scheduled (stalled — good)

### Party list — the most important signal
After "Party Information", list every defendant and note whether they have a "Party Attorney" listed beneath their name. No attorney = no response filed. Read the pattern:

```
Healy, Marion S. - Defendant
Party Attorney
More Party Information        ← If "Party Attorney" is immediately followed by "More Party Information"
                                 with nothing in between, they have NO attorney = NO response
```

vs.

```
Some Defendant - Defendant
Party Attorney
Attorney Smith, John         ← Has attorney = filed response
```

Count total defendants. Multiple defendants with the same surname = heir/estate situation.

### Docket entries — secondary signals
Look for these key phrases in the docket section:

| Docket entry | What it means |
|---|---|
| "Letter of Diligent Search Filed" | Court couldn't locate/serve the defendant — they're missing |
| "Appointment of [Examiner] returned without action" | Title examiner refused the case — title is extremely complex or clouded |
| "Notice of Status Review of the Docket" | A judge is noticing the case has been stalled and is watching it |
| "Response to checklist filed" | Plaintiff (town) is still actively pushing the case |
| Long gap between last docket entry and today | Case is just sitting — nobody is pushing it |
| Complaint eFiled / Amended | Recent activity, case is still moving |

**One "returned without action"** = complex title. **Two or more** = extremely complex title — this is rare and significant.

---

## Flagging Criteria

Score each case and assign a flag level:

### 🔴 HOT — Work this immediately
Any combination of 2+ of these signals:
- Multiple defendants with no attorneys (fractured ownership, nobody responding)
- Trust as a defendant (original owner likely deceased)
- "Letter of Diligent Search Filed" (defendant is missing/unreachable)
- 2+ title examiners returned without action (severely clouded title)
- Filed 3+ years ago, status still Open, no judge, no next event
- Multiple surnames among defendants (multi-family or cross-generation heirs)

**Example profile:** 16 defendants all named Healy/Mahoney, a Revocable Trust as defendant, filed 2018, still Open, no judge, no next event, no attorneys for anyone → 🔴 HOT

### 🟡 WARM — Keep watching, dig deeper
- Single defendant, no attorney, case filed 2+ years ago and stalled
- "Notice of Status Review" issued by judge (court is watching, something may happen soon)
- Complex title (1 examiner returned without action) but case is otherwise quiet
- Corporate defendant with diligent search filed (company dissolved or missing)

### 🟢 WATCH — Not urgent but worth monitoring
- Recently filed (< 18 months), defendant hasn't responded yet
- Backburner with no other signals found
- Needs more research (can't determine status from docket alone)

---

## Output Format

After researching each batch of cases, present findings like this:

---

### 🔴 HOT LEADS

**[Case Number] — [Town] — [Property Address]**
- **Defendants:** [list names, note if Trust/Corp/multiple surnames]
- **Filed:** [date] — [X years ago]
- **No responses:** All defendants unrepresented
- **Key signals:** [bullet the specific red flags found]
- **Recommended action:** [what to do next — who to call, what to research]

---

### 🟡 WARM LEADS

[Same format]

---

### 🟢 WATCH LIST

[Same format, briefer]

---

### ⚪ ALREADY RESEARCHED / SKIP

[Brief list of cases researched but no flags]

---

## Phase 3 — Deep Research on HOT Cases

Once a case is flagged HOT, run this deeper workflow. The goal is to answer three questions: **Is there real equity? Who actually controls each share of ownership? What is the minimum number of calls to get a deal done?**

The defendant list often looks overwhelming — 10, 15, even 20 names. But the real number of conversations needed is almost always much smaller once you trace who inherited what and who controls which share. That tracing process is what Phase 3 is about.

---

### Step 1 — Open the complaint image: get assessed value and tax taking year

On the MassCourts case page, the first docket entry ("Complaint filed") has an Image link. Open it — the complaint is required by law to list:

- **Assessed value of the property**
- **Tax taking year** — the year the town first took the tax lien (when you start your interest calculation)

These two numbers tell you immediately whether there's equity worth pursuing. Get them before going any further.

---

### Step 2 — Check the title report: deed history and outstanding balance

The title examiner's report is another Image link in the docket (typically filed 2–4 months after the complaint). Open it and note:

- **Who the deed is to and when** — a deed from the 1950s–1970s with nothing recorded since means the property has been in one name for decades, completely untouched
- **Whether it's a sole deed or joint deed** — if the deed is to one person only, their surviving spouse inherited everything (not co-ownership), and from there it passed by intestate law to the children. Don't assume co-ownership.
- **Any mortgages, liens, or subsequent transfers** — if there's nothing after the original deed, the only debt is the tax lien
- **The actual outstanding tax balance** — the title report often includes this, which is more precise than estimating

**The key insight from the title report:** A clean deed with no subsequent activity means the only thing standing between Joe Homebuyer and the property is the tax lien and the heir situation. No bank to deal with. No other creditors. Just the family.

---

### Step 3 — Calculate the equity

**Massachusetts tax accrual rates:**
- Before 2023: **16% per year**
- 2023 and after: **8% per year**

**How to estimate taxes owed:**
1. Get the tax taking year from the complaint
2. Get the annual tax bill from the title report (or estimate: assessed value × town tax rate)
3. Compound: annual bill × 1.16/year through 2022, then × 1.08/year from 2023 on
4. Compare to assessed value

**Land is often the best case.** Raw land accrues taxes slowly — a $216k assessed parcel might only carry $2,000–$4,000/year in taxes. After 10 years with interest, you might owe $40–60k against $216k assessed value. That's real equity. "Useless land" in Jordan's notes means that specific parcel was judged worthless (wetland, landlocked, unbuildable) — but buildable or sellable land with low taxes and solid assessed value is a strong opportunity.

**Rule of thumb:** If estimated taxes owed (with interest) are under 50% of assessed value, there's enough equity to pursue.

---

### Step 4 — Trace the inheritance chain with obituaries

The defendant list tells you who the court served. What it doesn't tell you is how the ownership actually flowed from the original deed holder down to the present. Obituaries are how you reconstruct that chain.

**Start with the original deed holder** (from the title report):
- Search `"[first name] [last name]" obituary [town]` in Google or Newspapers.com
- Read who survived them: spouse, children, stepchildren

**Apply Massachusetts intestate succession rules** to trace ownership forward:
1. **Surviving spouse inherits first** — if the deed was to one person only and they were married, the spouse inherited 100%
2. **Spouse dies** → passes equally to children by intestate law (50/50, 33/33/33, etc.)
3. **A child dies** → their share passes to their own heirs (spouse, then children)
4. Each generation that dies without dealing with the property adds another layer of heirs

**What signals a person is deceased in the defendant list:**
- A Revocable Trust named after them is listed as a defendant (e.g. "The Daniel S. Hanley Revocable Inter Vivos Trust") — they created the Trust while alive and it now holds their interest
- They're listed among a group of siblings but appear elderly based on the obit dates
- Their name appears in another obit as "the late [name]"

**Keep tracing** — don't stop at the first obit. If a person in the first obit is also deceased (e.g. "brother of the late Edward S. Hanley"), find their obit too. Each deceased defendant needs their own obit or probate to understand who controls their share.

---

### Step 5 — Search MassCourts Probate for deceased defendants with Trusts

When a **Revocable Trust** is named as a defendant, the Trust creator almost certainly died and filed a probate. Their will is the key document — it tells you exactly who controls the Trust and the estate.

**How to search:**
1. Go to `masscourts.org/eservices/search.page`
2. Select **Probate and Family Court** as the department
3. Select the correct county division (Westford → Middlesex; use the county where the person lived)
4. Switch to the **Name** tab, enter the last name and first name
5. Look for an Estate (EA) case filed around the time of their death

**What to look for in the will (once you find it):**

- **Is it a pour-over will?** — "All the rest, residue and remainder... I give to the Trustees of THE [NAME] REVOCABLE INTER VIVOS TRUST AGREEMENT" means everything flows into the Trust. The Trustee controls the estate's property interest.
- **Who is named Executrix/Executor?** — This is your contact. They legally control the estate and can sign off on a deal. The spouse is usually named first; adult children are named as backups.
- **Who are the backup Co-Executors?** — If the primary Executrix has also died or is unreachable, the backups step in. These are also valid contacts.

**Practical example (Westford / Daniel S. Hanley):**
- Trust named as defendant → search Middlesex Probate for "Hanley, Daniel S."
- Will found: pour-over will, everything to his Revocable Trust
- **Executrix: Mary L. Hanley** (his wife) — she is your primary contact for his 50%
- **Backup Co-Executors: Scott S. Hanley and Elizabeth A. Hanley** (his son and daughter) — secondary contacts if Mary is unreachable

One phone call to Mary L. Hanley covers Daniel's entire share of the property, regardless of how many of his children and grandchildren appear in the defendant list.

---

### Step 6 — Identify the real number of calls needed

After tracing the chain, map out who actually controls each fractional interest:

| Share | Controlled by | How to reach |
|---|---|---|
| Daniel's 50% | Mary L. Hanley (Executrix + Trustee) | Find her phone via WhitePages |
| Edward's 50% | Unknown — need Edward's obit/probate | Search Middlesex Probate for "Hanley, Edward S." |

The large defendant list collapses to a small number of actual decision-makers once you know who controls each branch. In most cases you're looking at 2–4 real conversations, not 16.

---

### Step 7 — Assess the path to a deal

| Question | Where to find the answer |
|---|---|
| Is there equity? | Assessed value (complaint) vs. taxes owed (title report + interest calc) |
| Who owned it originally? | Deed in title report |
| How did it pass down? | Intestate succession rules + obit chain |
| Who controls each share? | Most recent heir's obit + probate will |
| Is the Trust's Executrix still alive? | Check if they appear in subsequent obits |
| Is anyone living there? | Google Street View |
| Is the town pushing the case? | Recent docket entries |

---

## Recommended Next Steps After Flagging

For each HOT lead:
1. **Run Phase 3** — complaint image (equity check) → title report (deed + balance) → obit chain → probate search for any Trust defendants
2. **Identify the 2–4 real decision-makers** and find their phone numbers (WhitePages, BeenVerified)
3. **Google Street View** the property — look for occupancy, condition, neglect
4. **Check masslandrecords.com** if the title report flagged anything unusual about the deed chain

---

## Notes on the Spreadsheet's Note System

Jordan uses these shorthand notes — understand them before flagging:

| Note | Meaning |
|---|---|
| useless / useless land | Skip — not viable |
| answer filed | Skip — defendant responded |
| lives there / heir lives there | Skip — someone is occupying |
| payment agreement / payment plan | Skip — deal already in progress |
| out of buy box / out of buy area | Skip — wrong geography or price |
| bk | Skip — bankruptcy filed |
| sold | Skip — property sold |
| backburner | Potentially interesting, not urgent |
| monitor | Worth watching for developments |
| salesforce | Already in CRM/pipeline |
| miro | Flagged in Miro board |
| [phone number in notes] | Jordan already made contact |

---

## Efficiency Tips

- **Batch the gviz fetch** — load all 48 tabs in one async loop (takes ~10s) rather than one at a time
- **Check the case number tab URL once per session** — the session stays valid for several minutes, so you can search multiple cases before refreshing
- **Read `document.body.innerText` in full** — the "All Information" tab on MassCourts already shows both party info AND docket in one page load
- **Don't try to use the Docket tab link** — it requires a fresh session and usually returns an Internal Server Error if you navigate away first
- **Start with Priority 1 cases** — they're few in number and Jordan already thought they were good; confirming them with docket data takes minutes and gives you quick wins to report
