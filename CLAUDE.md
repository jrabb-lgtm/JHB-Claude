# Joe Homebuyer — Claude Memory

## Who I Am
I'm Seth, the Claude agent for Joe Homebuyer's daily probate list workflow. I run on two Macs (Mac Mini at the office + Jordan's personal machine) and stay in sync via GitHub.

## The Core Job
Run the daily probate list every weekday morning. The script (`daily_list.py`) scrapes Massachusetts probate court filings, extracts property addresses, and posts them to a Google Sheet.

## Key Details
- **Run command:** `rdl` (alias for `python3 ~/daily-list/daily_list.py`)
- **Google Sheet:** `https://docs.google.com/spreadsheets/d/1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw/edit`
- **Apps Script URL:** `https://script.google.com/macros/s/AKfycbx2nAPJS1yXZ1EaWge49JHkk80XQdtpFk9-ybNoPK0rqKPghCRZMCDgnQFoUCRShBXkPQ/exec`
- **Apps Script project:** "Claude Daily List" on script.google.com
- **Notification email:** jrabb@joehomebuyer.com

## GitHub Repo
- **URL:** https://github.com/jrabb-lgtm/JHB-Claude
- **Local path:** `~/Documents/Claude/Projects/Python Daily List`
- **Auto-sync:** Nightly via LaunchAgent (`com.joehomebuyer.claude-sync`)

## Environment Variables (set in ~/.zshrc)
- `ANTHROPIC_API_KEY` — Claude API (console.anthropic.com)
- `APPS_SCRIPT_URL` — deployed Apps Script web app
- `APPS_SCRIPT_SECRET` — shared auth secret (also set in Apps Script project properties)
- `TWOCAPTCHA_API_KEY` — optional, auto-solves reCAPTCHA on MassCourts.org

## Probate Workflow (manual lookups)
When Jordan asks me to look up a case:
1. Go to masscourts.org, search by case number (e.g., `NO26P0954EA`)
2. Open the Petition for Formal Probate PDF
3. Scroll to **page 2**, find the MA property address in Section 4 (Venue)
4. Update **column H** of the matching row in the Google Sheet
- Case prefix → county: NO=Norfolk, MI=Middlesex, ES=Essex, SU=Suffolk, WO=Worcester, BA=Barnstable

## Script Architecture
- 4 parallel Playwright browser contexts scraping all MA counties
- PDFs rendered to PNG via pymupdf, read by Claude Haiku
- ArcGIS lookups for geocoding
- Results POSTed to Apps Script → Google Sheet
- Summary email sent on completion

## Jordan's Preferences
- Doesn't want long explanations — just do the thing
- Comfortable with Terminal for running commands
- Uses Mac Mini as primary work machine
- iCloud Desktop sync is enabled (occasionally causes file deletion issues)
