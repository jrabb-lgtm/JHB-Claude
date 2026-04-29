# Joe Homebuyer — Claude Memory

## Who I Am
I'm the Claude agent for Joe Homebuyer. I run on two Macs (Mac Mini at the office + Jordan's personal machine) and stay in sync via GitHub.

## The Core Jobs
1. **Daily probate list** — runs every weekday at 4am via scheduled task
2. **Nightly Quo call report** — runs every night at 8pm, emails Jordan a performance summary of all agents

---

## GitHub Sync
- **Repo:** https://github.com/jrabb-lgtm/JHB-Claude
- **Local path:** ~/Documents/Claude/Projects/Python Daily List
- **Auto-sync:** Nightly via LaunchAgent (com.joehomebuyer.claude-sync)

⚠️ **HARD RULE — Mac Mini (Seth) must NEVER push to GitHub** except for one exception: the `seth-memory/` folder. Seth may write memory files to `~/Documents/Claude/Projects/Python Daily List/seth-memory/` and the nightly sync will commit and push them automatically. Seth must NEVER run `git push`, `git commit`, or edit any files outside of `seth-memory/`. All code changes (`daily_list.py`, `apps_script_webapp.js`, `CLAUDE.md`, `sync.sh`, etc.) originate on Jordan's personal machine only.

**Seth's persistent memory lives at:** `~/Documents/Claude/Projects/Python Daily List/seth-memory/`
- Write memory files here using the same format as the Cowork memory system (frontmatter + body)
- Keep `seth-memory/MEMORY.md` updated as an index
- The nightly sync commits and pushes this folder to GitHub automatically so learnings survive across sessions

---

## Probate / Daily List

- **Run command:** `rdl` (alias for `python3 ~/daily-list/daily_list.py`)
- **Google Sheet:** https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit
- **Apps Script URL:** https://script.google.com/macros/s/AKfycbx2nAPJS1yXZ1EaWge49JHkk80XQdtpFk9-ybNoPK0rqKPghCRZMCDgnQFoUCRShBXkPQ/exec
- **Apps Script project:** "Claude Daily List" on script.google.com
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

### Account 2 (Cold Callers — Mac Mini)
Separate Quo workspace. Connect the Quo MCP on the Mac Mini using the cold caller account credentials.

**Team:** Kelsey Castillo, Rebecca Bautista, Rick Pimentel
**Inboxes:** Ky 1 (+17819726864), Ky 2 (+17814626741), Ky 3 (+17816946395), Ky 4 (+19786984676)
**Scheduled task to set up:** Same nightly-agent-call-report task at 8:00 PM — same format, same recipient (jrabb@joehomebuyer.com), subject: "📞 Nightly Call Report — Cold Callers — [Date]"

**To set up on Mac Mini:** Connect Quo (cold caller account) → say "set up the nightly call report" and follow the same steps used on the main machine.

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
- Uses Mac Mini as primary work machine
- iCloud Desktop sync is enabled (occasionally causes file deletion issues)
- Two Quo workspaces: main account (26 inboxes, acquisition team) + cold caller account (Mac Mini)
