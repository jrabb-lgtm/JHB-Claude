# Joe Homebuyer — Claude Memory

## Machines & Sync Rules

- **Jordan's MacBook (this machine):** Source of truth. All code changes, skill updates, and config changes originate here and get pushed to GitHub.
- **Seth (Mac Mini):** Pulls from GitHub. Runs the daily list and scheduled tasks. May ONLY write to `seth-memory/` — never any other file, never `git push` directly.

**Flow:** MacBook → GitHub → Seth. One direction only (except seth-memory/).

**Token:** Stored at `~/.jhb_github_token`. Never hardcode it in any file that goes to GitHub.

---

## Active Projects

1. **Daily List** — most critical. Python script (`daily_list.py`) scrapes all MA probate counties daily at 4am. All sub-workflows (probate, pre-foreclosure, tax-lien, foreclosure-auction) are internal to this script. The `daily-list` skill triggers it.

2. **Tax Foreclosure Research** — researches open MA Land Court tax lien cases on MassCourts.org and flags HOT / WARM / WATCH leads in the Google Sheet.

3. **Quo Integration** — nightly call report emailed to Jordan at 8pm. Two accounts: main acquisition team (this machine, 26 inboxes) and cold callers (Seth — Kelsey, Rebecca, Rick).

---

## Seth Memory Review

Seth writes learnings to `seth-memory/` in the repo. The nightly sync commits and pushes them automatically.

When I see new seth-memory entries:
- **No conflicts with Jordan's laptop setup** → apply the changes directly.
- **Conflicts exist** → surface them to Jordan and wait for a decision before applying.

---

## GitHub Sync
- **Repo:** https://github.com/jrabb-lgtm/JHB-Claude
- **Local path (MacBook):** ~/Documents/Claude/Projects/Python Daily List
- **Auto-sync:** Every 5 minutes via LaunchAgent (com.joehomebuyer.claude-sync)

⚠️ **HARD RULE — Seth must NEVER push to GitHub** except for `seth-memory/`. Seth must NEVER run `git push`, `git commit`, or edit any files outside of `seth-memory/`. All code changes originate on Jordan's MacBook only.

**Seth's persistent memory:** `~/Documents/Claude/Projects/Python Daily List/seth-memory/`

---

## Probate / Daily List

- **Run command:** `rdl` (alias for `python3 ~/daily-list/daily_list.py`)
- **Google Sheet:** https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit
- **Apps Script URL:** https://script.google.com/macros/s/AKfycbx2nAPJS1yXZ1EaWge49JHkk80XQdtpFk9-ybNoPK0rqKPghCRZMCDgnQFoUCRShBXkPQ/exec
- **Notification email:** jrabb@joehomebuyer.com
- **Schedule:** Weekdays at 4:09am (NopeCHA handles reCAPTCHA automatically — never stop to ask Jordan to solve it)

**Script Architecture:**
- 4 parallel Playwright browser contexts scraping all MA counties
- PDFs rendered to PNG via pymupdf, read by Claude Haiku
- ArcGIS lookups for geocoding
- Results POSTed to Apps Script → Google Sheet
- Summary email sent on completion

**Probate Workflow (manual lookups):**
When Jordan asks to look up a case: go to masscourts.org, search by case number (e.g. NO26P0954EA), open the Petition for Formal Probate PDF, scroll to page 2, find the MA property address in Section 4 (Venue), update column H of the matching row in the Google Sheet.

Case prefix → county: NO=Norfolk, MI=Middlesex, ES=Essex, SU=Suffolk, WO=Worcester, BA=Barnstable

---

## Quo — Nightly Call Report

### Account 1 (Main — this machine)
Connected via Quo MCP connector. 26 inboxes across the acquisition team.

**Scheduled task:** `nightly-agent-call-report` — runs every night at 8:00 PM
**Report sent to:** jrabb@joehomebuyer.com
**Subject line:** 📞 Nightly Call Report — [Date]

**Agent → Inbox mapping:**
- **Omar Pulido:** Omar Primary (PNr4Rl7OwU), Omar 2 (PNAskTruD5), Omar 3 (PNtdslR8cq), Jeff 1–5, 7–8, 11
- **Tyanne (Dispo NE):** Tyanne (PN9WO5K2YS), Tyanne 1 (PNhdmAGaj1), Tyanne 2 (PNpLVtzGWf)
- **Jason Perez:** Jason 1 (PNCXjf30P3), Jason 2 (PNHBtJHzqT)
- **Nicole Chabra:** Nicole 1 (PNoT8z7dyC)
- **Tim Newcombe:** Tim (PNATEA5eQc)
- **Shared:** PPL (PNgRdIu8MO), PPC (PN5ai8rg1h), SEO (PNwuaK7E9l), Dispo (PNpowgr1Sm), Blue Oak Office (PNXuBbwaAE), HL Parking (PN4cdq8v06)

**Report color coding:** 🟢 = 30+ dials, 🟡 = 15–29, 🔴 = under 15

### Account 2 (Cold Callers — Seth)
Separate Quo workspace. Connect the Quo MCP on Seth using the cold caller account credentials.

**Team:** Kelsey Castillo, Rebecca Bautista, Rick Pimentel
**Inboxes:** Ky 1 (+17819726864), Ky 2 (+17814626741), Ky 3 (+17816946395), Ky 4 (+19786984676)
**Scheduled task:** Same nightly-agent-call-report at 8:00 PM — subject: "📞 Nightly Call Report — Cold Callers — [Date]"

---

## Environment Variables (set in ~/.zshrc)
- `ANTHROPIC_API_KEY` — Claude API (console.anthropic.com)
- `APPS_SCRIPT_URL` — deployed Apps Script web app
- `APPS_SCRIPT_SECRET` — shared auth secret
- `TWOCAPTCHA_API_KEY` — optional, auto-solves reCAPTCHA on MassCourts.org

---

## Jordan's Preferences
- Doesn't want long explanations — just do the thing
- Comfortable with Terminal for running commands
- MacBook is primary dev machine; Seth is the always-on worker
- iCloud Desktop sync is enabled (occasionally causes file deletion issues)
- Two Quo workspaces: main account (26 inboxes, acquisition team) + cold caller account (Seth)
