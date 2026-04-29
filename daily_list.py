#!/usr/bin/env python3
from __future__ import annotations
"""
Joe Homebuyer Daily List — Automated Runner
Runs Mon–Fri at 6 AM via launchd on Mac Mini.

Architecture:
  - 4 parallel Playwright browser contexts (each = independent persistent session)
  - Context 1 (ctx1): BA, BR, DU, ES, FR  → then Pre-Foreclosure + Tax Lien
  - Context 2 (ctx2): NA, NO, PL, SU, WO
  - Context 3 (ctx3): MI  (Middlesex alone — large county)
  - Context 4 (ctx4): Foreclosure Auctions only (masspublicnotices.org)
  - PDFs downloaded with authenticated session, rendered to PNG via pymupdf
  - Claude Haiku API reads each PDF page image and extracts structured fields
  - ArcGIS lookups run directly in Python (no browser needed)
  - Results POSTed to Apps Script web app → Google Sheet
  - Summary email sent via Apps Script when done

Required env vars (set in ~/.zshrc or launchd plist):
  ANTHROPIC_API_KEY      — from console.anthropic.com
  APPS_SCRIPT_URL        — deployed web app URL (see apps_script_webapp.js)
  APPS_SCRIPT_SECRET     — shared secret token for the web app
  TWOCAPTCHA_API_KEY     — optional: https://2captcha.com (auto-solves reCAPTCHA)
"""

import asyncio
import anthropic
import base64
import csv
import io
import subprocess
import os

# Auto-sync from GitHub before running
_repo = os.path.expanduser("~/JHB-Claude")
if os.path.isdir(os.path.join(_repo, ".git")):
    subprocess.run(["git", "-C", _repo, "pull", "origin", "main", "--quiet"], check=False)
import json
import logging
import os
import re
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, quote

import fitz          # pymupdf  — install: pip install pymupdf
import requests
from playwright.async_api import async_playwright, BrowserContext, Page

# ---------------------------------------------------------------------------
# Stealth JS — patches fingerprinting flags checked by bot-detection.
# Applied to every new page via context.add_init_script().
# ---------------------------------------------------------------------------
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
Object.defineProperty(navigator, 'permissions', {
  get: () => ({query: () => Promise.resolve({state: 'granted'})})
});
"""

# ── Logging ─────────────────────────────────────────────────────────────────
_log_stream = io.StringIO()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path.home() / "daily_list.log"),
        logging.StreamHandler(_log_stream),
    ],
)
log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
APPS_SCRIPT_URL    = os.environ.get("APPS_SCRIPT_URL", "")
APPS_SCRIPT_SECRET = os.environ.get("APPS_SCRIPT_SECRET", "")
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")

NOTIFICATION_EMAIL = "jrabb@joehomebuyer.com"
SPREADSHEET_ID     = "1JS--FPwrBR0Qt3GalZe-_xAaLT0Hgr_3P_sVttUdGMw"
OUTPUT_DIR         = Path.home() / "daily_list_runs"

# County groups — one list per browser context
COUNTY_GROUPS = [
    ["BA", "BR", "DU", "ES", "FR"],  # ctx1 — also runs Pre-Foreclosure + Tax Lien
    ["NA", "NO", "PL", "SU", "WO"],  # ctx2
    ["MI"],                           # ctx3 — Middlesex alone (large)
    [],                               # ctx4 — Foreclosure Auctions only
    [],                               # ctx5 — 2-week sweep + No Images (starts May 1 2026)
]

# Which context index handles each special workflow
PRE_FC_CTX_IDX      = 0
TAX_LIEN_CTX_IDX    = 0
FC_AUCTIONS_CTX_IDX = 3
SWEEP_CTX_IDX       = 4

# Division codes for each county's Probate & Family Court division
COUNTY_DIV = {
    "BA": "PF02_DIV", "BR": "PF04_DIV", "DU": "PF05_DIV", "ES": "PF06_DIV",
    "FR": "PF01_DIV", "MI": "PF09_DIV", "NA": "PF10_DIV", "NO": "PF11_DIV",
    "PL": "PF12_DIV", "SU": "PF13_DIV", "WO": "PF14_DIV",
}

ARCGIS_ENDPOINT = (
    "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services"
    "/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
)

# ── Date logic ───────────────────────────────────────────────────────────────
# Update HOLIDAYS list each December for the coming year
HOLIDAYS = {
    date(2026, 1, 1),  date(2026, 1, 19),  date(2026, 2, 16),
    date(2026, 5, 25), date(2026, 7, 3),   date(2026, 9, 7),
    date(2026, 10, 12),date(2026, 11, 11), date(2026, 11, 26),
    date(2026, 12, 25),
}

def get_date_range(today: date = None):
    """Return (from_date, to_date) for the file date search."""
    if today is None:
        today = date.today()
    dow = today.weekday()
    if dow == 6:  # Sunday
        return today - timedelta(2), today - timedelta(1)
    if dow == 0:  # Monday
        return today - timedelta(3), today - timedelta(1)
    yesterday = today - timedelta(1)
    if dow == 1 and yesterday in HOLIDAYS:       # Tuesday after Monday holiday
        return today - timedelta(4), yesterday
    if yesterday in HOLIDAYS:                    # Day after any other holiday
        return yesterday - timedelta(1), yesterday
    return yesterday, yesterday                  # Normal weekday

def fmt_date(d: date) -> str:
    return d.strftime("%m/%d/%Y")

def fmt_zip(z) -> str:
    """Zero-pad Massachusetts zip codes that arrive without a leading zero.
    ArcGIS ZIP / OWN_ZIP fields return integers (e.g. 2301 not 02301).
    AI extraction sometimes does the same. Empty strings pass through unchanged.
    """
    s = str(z or "").strip()
    return s.zfill(5) if s and s.isdigit() else s

def fmt_phone(p) -> str:
    """Normalize a phone number to xxx-xxx-xxxx format.
    Strips all non-digit characters, then formats as NXX-NXX-XXXX.
    10-digit numbers only; anything else is returned as-is (cleaned of non-digits).
    """
    digits = re.sub(r"\D", "", str(p or ""))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]          # strip leading country code
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits  # return raw digits if not a standard 10-digit US number

# ── Name helpers ─────────────────────────────────────────────────────────────

def clean_last_name(last_name: str) -> str:
    """Strip generational suffixes (Jr, Sr, II, III, IV, V) from a last name."""
    cleaned = re.sub(r'\b(JR\.?|SR\.?|II|III|IV|V)\b\.?', '', last_name.upper())
    return cleaned.strip().rstrip(".,")

def extract_party_name(case_text: str, role: str) -> str:
    """
    Extract the party name for 'role' (Plaintiff/Defendant) from MassCourts body text.

    Supports two page formats:

    Old format (pre-April 2026 redesign):
        Defendant(s) : 469269  Date: 04/21/2026
        JOHN DOE
        The number is a party-entry sequence ID added by the court system.
        We detect that pattern and fall through to the next line to get the actual name.

    New format (post-April 2026 redesign):
        Clark, Daniel J. - Defendant
        Names appear as "NAME - Role" in the Party section.

    Also strips the "Pay " prefix that MassCourts sometimes prepends.
    """
    # ── NEW format: "NAME - Role" (MassCourts redesign, ~April 2026) ──
    # Restrict search to after "Party Information" to avoid false matches in the case title.
    party_section = case_text
    party_start = re.search(r"Party\s+Information", case_text, re.IGNORECASE)
    if party_start:
        party_section = case_text[party_start.start():]

    # Each party entry is on its own line in Playwright's inner_text output, e.g.:
    #   "Clark, Daniel J. - Defendant"
    # Use ^ (MULTILINE) so we only match at the start of a line, and [^\n] so we
    # don't cross line boundaries.  Non-greedy +? picks the minimum name before " - Role".
    m_new = re.search(
        rf"^([^\n]+?)\s+-\s+{role}\b",
        party_section, re.IGNORECASE | re.MULTILINE,
    )
    if m_new:
        raw = m_new.group(1).strip()
        raw = re.sub(r"^Pay\s+", "", raw)
        return raw

    # ── OLD format: "Role(s) : [sequence_id\n]NAME" ──
    m = re.search(rf"{role}\s*(?:\(s\))?\s*:\s*([^\n]+)", case_text, re.IGNORECASE)
    if not m:
        return ""
    raw = m.group(1).strip()
    raw = re.sub(r"^Pay\s+", "", raw)
    # If the captured text is a party-entry sequence number + date (e.g. "469269 Date: 04/21/2026"),
    # the real name is on the next line — search after the match end.
    if re.match(r"^\d+\s+Date:\s+\d", raw):
        after = case_text[m.end():]
        name_line = re.search(r"^\s*([A-Za-z][^\n]+)", after, re.MULTILINE)
        raw = name_line.group(1).strip() if name_line else ""
    return raw

def split_owner_name(owner: str, natural_order: bool = False) -> tuple[str, str]:
    """
    Split an owner/defendant name into (first, last).

    ArcGIS format (default):  "SMITH JOHN"  → ("John", "Smith")
                              "SMITH JOHN W" → ("John W", "Smith")
    Natural order (court):    "John Smith"  → ("John", "Smith")
                              "SMITH, JOHN" → ("John", "Smith")  [LAST, FIRST]
    """
    if not owner:
        return "", ""

    owner = owner.strip()

    # Handle "LAST, FIRST" court format (comma-separated)
    if "," in owner:
        parts = [p.strip() for p in owner.split(",", 1)]
        last  = parts[0].title()
        first = parts[1].title() if len(parts) > 1 else ""
        return first, last

    parts = owner.split()
    if len(parts) == 1:
        return "", parts[0].title()

    if natural_order:
        # "John Smith" or "John W Smith" → first=everything but last, last=last word
        first = " ".join(parts[:-1]).title()
        last  = parts[-1].title()
    else:
        # ArcGIS "LAST FIRST" or "LAST FIRST MIDDLE"
        last  = parts[0].title()
        first = " ".join(parts[1:]).title()

    return first, last

# ── ArcGIS lookup ────────────────────────────────────────────────────────────

def arcgis_query(where: str, record_count: int = 5) -> list[dict]:
    """Run an ArcGIS REST query and return feature attributes."""
    # NOTE: OWNER2 was removed from MA ArcGIS schema in 2026 — do not request it.
    params = {
        "where": where,
        "outFields": "OWNER1,SITE_ADDR,OWN_ADDR,OWN_CITY,OWN_STATE,OWN_ZIP,CITY,ZIP",
        "f": "json",
        "resultRecordCount": str(record_count),
    }
    # Use quote_via=quote so spaces become %20, not + (ArcGIS rejects + encoding)
    url = f"{ARCGIS_ENDPOINT}?{urlencode(params, quote_via=quote)}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            log.warning(f"ArcGIS API error: {data['error']}")
            return []
        return [f["attributes"] for f in data.get("features", [])]
    except Exception as e:
        log.warning(f"ArcGIS error: {e}")
        return []

def arcgis_by_address(street: str, city: str = None, zip_code: str = None) -> Optional[dict]:
    """
    Look up a property by street address using a layered fallback strategy.

    ZIP code is preferred over city name wherever possible — ZIPs are unique and
    unambiguous, while city names can be village names that ArcGIS doesn't recognise
    (e.g. "Marstons Mills" is stored as CITY="BARNSTABLE" in ArcGIS).

    Steps:
      1. Full SITE_ADDR LIKE + ZIP           (best: precise address + correct geography)
      2. Full SITE_ADDR LIKE + city          (fallback when ZIP missing/null in ArcGIS)
      3. House# + 2-word street frag + ZIP   (handles suffix abbreviation: RD vs ROAD)
      4. House# + 2-word street frag + city  (no ZIP available)
      5. House# + 1-word street frag + ZIP   (e.g. just "LAKE" when "LAKE RD" fails)
      6. House# + 1-word street frag + city
      7. Full SITE_ADDR LIKE, no geography   (last resort — no city or ZIP filter)
    """
    if not street:
        return None

    street = street.strip().upper()
    parts  = street.split()

    # Strip parenthetical neighbourhood qualifiers from city
    # e.g. "Falmouth (North)" → "Falmouth"
    if city:
        city = re.sub(r'\s*\([^)]+\)\s*$', '', city.strip()).strip() or city.strip()

    zip5 = str(zip_code).strip()[:5] if zip_code else ""

    def _prefer_zip(results: list[dict]) -> Optional[dict]:
        if not results:
            return None
        if zip5:
            for r in results:
                if str(r.get("ZIP") or "").startswith(zip5):
                    return r
        return results[0]

    city_upper = city.strip().upper() if city else ""
    num        = parts[0] if parts and parts[0].isdigit() else ""
    words      = parts[1:] if num else parts  # street name words (no house number)

    def _q(where):
        return arcgis_query(where, record_count=10)

    # Steps 1 & 2: full address match, ZIP then city
    if zip5:
        hit = _prefer_zip(_q(f"UPPER(SITE_ADDR) LIKE '{street}%' AND UPPER(ZIP) LIKE '{zip5}%'"))
        if hit:
            return hit
    if city_upper:
        hit = _prefer_zip(_q(f"UPPER(SITE_ADDR) LIKE '{street}%' AND UPPER(CITY) = '{city_upper}'"))
        if hit:
            return hit

    # Steps 3-6: house number + street fragment (handles suffix variations RD/ROAD/ST/STREET)
    if num and words:
        for n_words in ([2, 1] if len(words) >= 2 else [1]):
            frag = " ".join(words[:n_words])
            if zip5:
                hit = _prefer_zip(_q(f"UPPER(SITE_ADDR) LIKE '{num} {frag}%' AND UPPER(ZIP) LIKE '{zip5}%'"))
                if hit:
                    return hit
            if city_upper:
                hit = _prefer_zip(_q(f"UPPER(SITE_ADDR) LIKE '{num} {frag}%' AND UPPER(CITY) = '{city_upper}'"))
                if hit:
                    return hit

    # Step 7: full address with no geography filter — last resort
    if zip5 or city_upper:
        hit = _prefer_zip(_q(f"UPPER(SITE_ADDR) LIKE '{street}%'"))
        if hit:
            return hit

    return None

def arcgis_by_owner(owner: str, city: str = None) -> Optional[dict]:
    """Look up by owner name (fallback for tax lien / parcels with no street number)."""
    if not owner:
        return None
    last = clean_last_name(owner.strip().upper().split()[0])
    where = f"UPPER(OWNER1) LIKE '%{last}%'"
    if city:
        # Strip parenthetical neighborhood qualifiers before querying ArcGIS
        city = re.sub(r'\s*\([^)]+\)\s*$', '', city.strip()).strip() or city.strip()
        # Try with city first
        where_city = where + f" AND UPPER(CITY) = '{city.strip().upper()}'"
        results = arcgis_query(where_city, record_count=10)
        if results:
            return results[0]
    # Fallback: name only
    results = arcgis_query(where, record_count=10)
    return results[0] if results else None

# ── Claude API PDF extraction ─────────────────────────────────────────────────

def pdf_bytes_to_png(pdf_bytes: bytes, page_num: int = 0) -> bytes:
    """Render a PDF page to PNG bytes using pymupdf (2× scale for OCR quality)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_num >= len(doc):
        raise IndexError(f"PDF has {len(doc)} page(s), requested page {page_num}")
    page = doc[page_num]
    mat  = fitz.Matrix(2.0, 2.0)
    pix  = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")

def extract_probate_page1(client: anthropic.Anthropic, png_bytes: bytes) -> dict:
    """Call Claude Haiku to extract probate petition page 1 fields (MPC 150 or MPC 160)."""
    img_b64 = base64.standard_b64encode(png_bytes).decode()
    prompt = (
        "This is page 1 of a Massachusetts probate court petition — it may be an MPC 150 "
        "(Petition for Informal Probate and/or Appointment of Personal Representative) OR "
        "an MPC 160 (Petition for Formal Adjudication). Both forms contain the same core "
        "information but in slightly different sections.\n\n"
        "Extract EXACTLY the following fields as a JSON object. "
        "Use empty string if a field is blank or not present.\n\n"
        "Fields:\n"
        "  decedent_first         — First (given) name of decedent ONLY. "
        "Example: if the form shows 'Nancy Heslin' return 'Nancy'; "
        "if it shows 'HESLIN, NANCY' (last-first format) return 'Nancy'.\n"
        "  decedent_last          — Last (family/surname) name of decedent ONLY. "
        "Example: if the form shows 'Nancy Heslin' return 'Heslin'; "
        "if it shows 'HESLIN, NANCY' return 'Heslin'. Never leave this blank if a surname is visible.\n"
        "  decedent_street        — Street address of decedent's domicile. On MPC 160 this is "
        "in Section 1; on MPC 150 it is in Section 2 (labeled 'Decedent's Domicile' or "
        "'Domicile at Time of Death'). Look for a street number + street name.\n"
        "  decedent_city          — City/town of decedent's domicile\n"
        "  decedent_state         — State of decedent's domicile (often MA)\n"
        "  decedent_zip           — Zip code of decedent's domicile\n"
        "  petitioner1_name       — Full name of first petitioner\n"
        "  petitioner1_street     — Petitioner 1 mailing street\n"
        "  petitioner1_city       — Petitioner 1 city\n"
        "  petitioner1_state      — Petitioner 1 state\n"
        "  petitioner1_zip        — Petitioner 1 zip\n"
        "  petitioner1_phone      — Petitioner 1 primary phone\n"
        "  petitioner1_email      — Petitioner 1 email (empty if blank)\n"
        "  petitioner1_relation   — Petitioner 1 relationship to decedent\n"
        "  petitioner2_name       — Second petitioner full name (empty if none)\n"
        "  petitioner2_street     — (empty if none)\n"
        "  petitioner2_city       — (empty if none)\n"
        "  petitioner2_state      — (empty if none)\n"
        "  petitioner2_zip        — (empty if none)\n"
        "  petitioner2_phone      — (empty if none)\n"
        "  petitioner2_email      — (empty if none)\n"
        "  petitioner2_relation   — (empty if none)\n\n"
        "Return ONLY a JSON object, no explanation, no markdown."
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_claude_json(resp.content[0].text, "page1")

def extract_probate_page2(client: anthropic.Anthropic, png_bytes: bytes, county_name: str = "") -> dict:
    """Call Claude Haiku to extract MPC 160 page 2 venue checkbox."""
    img_b64 = base64.standard_b64encode(png_bytes).decode()
    county_hint = (
        f" This case is filed in {county_name} County, Massachusetts."
        f" The venue property address MUST be in {county_name} County."
        f" Do NOT use the decedent's personal home address (which may be in a different county)"
        f" as the venue_property_address."
    ) if county_name else ""
    prompt = (
        "This is page 2 of a Massachusetts probate court petition (MPC 150 or MPC 160)."
        f"{county_hint} "
        "Find the Venue section — it may be labeled Section 3, Section 4, or Section 5 "
        "depending on the form type. It contains two checkboxes about where the decedent "
        "was domiciled or owned property.\n\n"
        "Extract:\n"
        "  venue_domiciled        — true if the checkbox 'was domiciled in this county' "
        "(or similar wording about domicile) is checked, else false\n"
        "  venue_property_address — if the checkbox 'had property located in this county at:' "
        "(or similar wording about property location) is checked, extract the full address "
        "that follows; otherwise empty string. This address must be in the county named above.\n\n"
        "IMPORTANT: If you cannot clearly identify which checkbox is checked, default to "
        "venue_domiciled=true and venue_property_address=''.\n\n"
        "Return ONLY a JSON object."
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    result = _parse_claude_json(resp.content[0].text, "page2")
    # Default to domiciled=True if extraction fails
    if not result:
        return {"venue_domiciled": True, "venue_property_address": ""}
    return result

def extract_complaint_fields(client: anthropic.Anthropic, png_bytes: bytes, case_type: str) -> dict:
    """Extract fields from a tax lien or pre-foreclosure complaint PDF image."""
    img_b64 = base64.standard_b64encode(png_bytes).decode()
    if case_type == "tax_lien":
        prompt = (
            "This is a Massachusetts Tax Lien complaint (a court filing to collect overdue property taxes).\n\n"
            "TASK: Find the line that says 'Assessed to:' or 'Assessed To:' in the document and extract "
            "ONLY the person or entity name that appears after that label.\n"
            "IMPORTANT: Do NOT extract the case number, filing date, tax amount, lien amount, or any other "
            "field — ONLY the name following 'Assessed to:'.\n\n"
            "Also extract:\n"
            "  property_street — property street address (may lack a street number if undeveloped land)\n"
            "  property_city   — property city or town\n"
            "  property_zip    — zip code if visible, else empty string\n\n"
            'Return ONLY valid JSON, e.g.: {"assessed_to": "SMITH JOHN", '
            '"property_street": "123 Main St", "property_city": "Springfield", "property_zip": "01234"}'
        )
    else:  # pre-foreclosure / servicemembers
        prompt = (
            "This is a Massachusetts Servicemembers Civil Relief Act complaint document.\n\n"
            "TASK: Extract the defendant's name from the party list at the top of the complaint "
            "(the person being sued — usually labeled 'Defendant' or listed after the 'v.').\n"
            "Also extract:\n"
            "  property_street — street address of the mortgaged property\n"
            "  property_city   — city or town of the property\n"
            "  property_zip    — zip code if visible, else empty string\n\n"
            'Return ONLY valid JSON, e.g.: {"defendant_name": "John Smith", '
            '"property_street": "123 Main St", "property_city": "Springfield", "property_zip": "01234"}'
        )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_claude_json(resp.content[0].text, case_type)

def _parse_claude_json(text: str, label: str) -> dict:
    """Strip markdown fences and parse JSON from Claude response."""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        log.warning(f"Claude {label} parse error: {text[:200]}")
        return {}

# ── MassCourts browser helpers ────────────────────────────────────────────────
MASSCOURTS_BASE = "https://www.masscourts.org/eservices/"

async def set_dept_and_div(page: Page, dept_fragment: str, div_code: str) -> bool:
    """
    Set department and division dropdowns on a MassCourts search page.

    Key timing: after firing the dept change event, the div select loads via AJAX.
    We use wait_for_function with a 10s timeout to detect when the div option appears,
    which naturally handles the ~2s AJAX delay documented in SKILL.md.

    Returns True if both selects were set successfully.
    """
    # ── Find dept select ──────────────────────────────────────────────────────
    dept_info = await page.evaluate(f"""
        (() => {{
            const sel = Array.from(document.querySelectorAll('select'))
                .find(s => Array.from(s.options).some(o => o.value.includes('{dept_fragment}')));
            if (!sel) return null;
            const opt = Array.from(sel.options).find(o => o.value.includes('{dept_fragment}'));
            return {{ name: sel.name || '', id: sel.id || '', val: opt ? opt.value : null }};
        }})()
    """)
    if not dept_info or not dept_info.get("val"):
        log.warning(f"  Dept select not found for '{dept_fragment}'")
        return False

    dept_css = f'select[name="{dept_info["name"]}"]' if dept_info["name"] else f'select#{dept_info["id"]}'

    # ── Set dept (Playwright native fires proper events) ──────────────────────
    try:
        await page.select_option(dept_css, value=dept_info["val"])
        log.info(f"  Set dept → {dept_info['val']}")
    except Exception as e:
        log.warning(f"  select_option(dept) failed ({e}), using dispatchEvent fallback")
        await page.evaluate(f"""
            const s = document.querySelector('{dept_css}');
            if (s) {{ s.value = '{dept_info['val']}'; s.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        """)

    # ── Wait for div select to populate with the target county option ─────────
    # SKILL.md: div select loads via AJAX ~2s after dept change
    try:
        await page.wait_for_function(
            """(code) => {
                // CRITICAL: find div select by '_DIV' in option values, NOT by 'PF' prefix
                // (dept select PF_DEPT also has options starting with PF — wrong target)
                const s = Array.from(document.querySelectorAll('select'))
                    .find(sel => Array.from(sel.options).some(o => o.value.includes('_DIV')));
                return s && Array.from(s.options).some(o => o.value.includes(code));
            }""",
            arg=div_code,
            timeout=12000,
        )
    except Exception:
        log.warning(f"  Div select with '{div_code}' not ready after 12s — continuing")
        await page.wait_for_timeout(2500)

    # ── Find and set div select ───────────────────────────────────────────────
    div_info = await page.evaluate(f"""
        (() => {{
            // Use includes('_DIV') — do NOT use startsWith('PF') which matches dept too
            const sel = Array.from(document.querySelectorAll('select'))
                .find(s => Array.from(s.options).some(o => o.value.includes('_DIV')));
            if (!sel) return null;
            const opt = Array.from(sel.options).find(o => o.value.includes('{div_code}'));
            return {{ name: sel.name || '', id: sel.id || '', val: opt ? opt.value : null }};
        }})()
    """)
    if not div_info or not div_info.get("val"):
        log.warning(f"  Div option '{div_code}' not found")
        return False

    div_css = f'select[name="{div_info["name"]}"]' if div_info["name"] else f'select#{div_info["id"]}'
    try:
        await page.select_option(div_css, value=div_info["val"])
        log.info(f"  Set div  → {div_info['val']}")
    except Exception as e:
        log.warning(f"  select_option(div) failed ({e}), using dispatchEvent fallback")
        await page.evaluate(f"""
            const s = Array.from(document.querySelectorAll('select'))
                .find(sel => Array.from(sel.options).some(o => o.value.includes('{div_code}')));
            if (s) {{
                const opt = Array.from(s.options).find(o => o.value.includes('{div_code}'));
                if (opt) {{ s.value = opt.value; s.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            }}
        """)

    # ── Wait for Wicket AJAX to produce navigation links ──────────────────────
    try:
        await page.wait_for_function(
            """() => document.querySelectorAll('a[href^="?x="]').length > 0""",
            timeout=8000,
        )
        count = await page.evaluate("document.querySelectorAll('a[href^=\"?x=\"]').length")
        log.info(f"  {count} ?x= link(s) appeared after div change")
    except Exception:
        log.warning("  No ?x= links appeared after div change (Wicket AJAX may be slow)")
        await page.wait_for_timeout(2500)

    return True


async def set_land_court_dept_and_div(page: Page) -> bool:
    """
    Select Land Court dept + division on MassCourts search page.

    Land Court is statewide (single division), so the approach is:
    1. Find the department select and pick the option whose TEXT includes 'Land Court'
       OR whose value includes 'LC' (value-based fallback).
    2. Wait for the division select to populate via AJAX.
    3. Auto-pick the first non-blank division option (statewide = only one choice).

    This is more robust than guessing the exact value string (LC_DEPT / LC_DIV).
    """
    # ── Find + set dept select ───────────────────────────────────────────────
    dept_info = await page.evaluate("""
        (() => {
            const sel = Array.from(document.querySelectorAll('select'))
                .find(s => Array.from(s.options).some(o =>
                    o.text.toLowerCase().includes('land court') ||
                    o.value.toUpperCase().startsWith('LC')
                ));
            if (!sel) return null;
            const opt = Array.from(sel.options).find(o =>
                o.text.toLowerCase().includes('land court') ||
                o.value.toUpperCase().startsWith('LC')
            );
            // Log all dept options so we can see what's there
            const allOpts = Array.from(sel.options).map(o => o.value + '=' + o.text.trim());
            return { name: sel.name || '', id: sel.id || '', val: opt ? opt.value : null, allOpts };
        })()
    """)
    if not dept_info or not dept_info.get("val"):
        log.warning("  Land Court: dept select not found by text or 'LC' value prefix")
        return False

    log.info(f"  Land Court dept options: {dept_info.get('allOpts', [])}")
    dept_css = f'select[name="{dept_info["name"]}"]' if dept_info["name"] else f'select#{dept_info["id"]}'
    try:
        await page.select_option(dept_css, value=dept_info["val"])
        log.info(f"  Set Land Court dept → {dept_info['val']}")
    except Exception as e:
        log.warning(f"  select_option(dept) failed ({e}), using dispatchEvent fallback")
        await page.evaluate(f"""
            const s = document.querySelector('{dept_css}');
            if (s) {{ s.value = '{dept_info['val']}'; s.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        """)

    # ── Wait for div select to populate ──────────────────────────────────────
    await page.wait_for_timeout(3000)

    # ── Find div select and pick first non-blank option ───────────────────────
    div_info = await page.evaluate("""
        (() => {
            // Find any select with options containing '_DIV' in value
            const sel = Array.from(document.querySelectorAll('select'))
                .find(s => Array.from(s.options).some(o => o.value.includes('_DIV')));
            if (!sel) return null;
            // Pick first non-blank, non-placeholder option
            const opts = Array.from(sel.options).filter(o => o.value && o.value.trim() !== '');
            const opt = opts[0] || null;
            const allOpts = Array.from(sel.options).map(o => o.value + '=' + o.text.trim());
            return { name: sel.name || '', id: sel.id || '', val: opt ? opt.value : null, allOpts };
        })()
    """)
    if not div_info or not div_info.get("val"):
        log.warning("  Land Court: div select not found or no options available")
        return False

    log.info(f"  Land Court div options: {div_info.get('allOpts', [])}")
    div_css = f'select[name="{div_info["name"]}"]' if div_info["name"] else f'select#{div_info["id"]}'
    try:
        await page.select_option(div_css, value=div_info["val"])
        log.info(f"  Set Land Court div → {div_info['val']}")
    except Exception as e:
        log.warning(f"  select_option(div) failed ({e}), using dispatchEvent fallback")
        await page.evaluate(f"""
            const s = document.querySelector('{div_css}');
            if (s) {{ s.value = '{div_info['val']}'; s.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        """)

    # Wait for Wicket AJAX nav links
    try:
        await page.wait_for_function(
            """() => document.querySelectorAll('a[href^="?x="]').length > 0""",
            timeout=8000,
        )
        count = await page.evaluate("document.querySelectorAll('a[href^=\"?x=\"]').length")
        log.info(f"  {count} ?x= link(s) appeared after Land Court div change")
    except Exception:
        log.warning("  No ?x= links appeared after Land Court div change")
        await page.wait_for_timeout(2500)

    return True


async def get_search_href(page: Page) -> Optional[str]:
    """Return the Wicket 'Search' navigation link href, or None."""
    result = await page.evaluate("""
        (() => {
            const qx = Array.from(document.querySelectorAll('a[href^="?x="]'));
            let a = qx.find(el => el.textContent.trim() === 'Search');
            if (a) return { href: a.getAttribute('href'), how: 'exact' };
            a = qx.find(el => el.textContent.trim().toLowerCase() === 'search');
            if (a) return { href: a.getAttribute('href'), how: 'ci' };
            if (qx.length > 0) {
                return { href: null, how: 'none',
                    qxTexts: qx.map(el => el.textContent.trim()),
                    url: window.location.href.slice(-80) };
            }
            return { href: null, how: 'none', qxTexts: [],
                url: window.location.href.slice(-80),
                allLinks: Array.from(document.querySelectorAll('a')).slice(0, 12)
                    .map(a => ({ t: a.textContent.trim(), h: (a.getAttribute('href') || '').slice(0, 40) }))
                    .filter(a => a.t) };
        })()
    """)
    if isinstance(result, dict) and result.get("href"):
        log.info(f"  Search href ({result.get('how')}): {result['href'][:50]}")
        return result["href"]
    if isinstance(result, dict):
        log.info(
            f"  No Search href | url=...{result.get('url','?')} "
            f"| ?x= links: {result.get('qxTexts', [])} "
            f"| allLinks: {[l['t'] for l in result.get('allLinks', [])]}"
        )
    return None


async def solve_recaptcha_v2(page: Page) -> Optional[str]:
    """
    Auto-solve reCAPTCHA v2 via 2captcha.com.
    Set TWOCAPTCHA_API_KEY env var. Cost ~$1–3/1000 solves.
    Returns g-recaptcha-response token or None on failure.
    """
    if not TWOCAPTCHA_API_KEY:
        log.warning("  TWOCAPTCHA_API_KEY not set — cannot auto-solve reCAPTCHA")
        return None

    site_key = await page.evaluate("""
        (() => {
            const div = document.querySelector('.g-recaptcha, [data-sitekey]');
            if (div && div.getAttribute('data-sitekey')) return div.getAttribute('data-sitekey');
            const iframe = document.querySelector('iframe[src*="recaptcha"]');
            if (iframe) {
                const m = iframe.src.match(/[?&]k=([^&]+)/);
                if (m) return m[1];
            }
            return null;
        })()
    """)
    if not site_key:
        log.warning("  2captcha: Could not find reCAPTCHA site key on page")
        return None

    page_url = page.url
    log.info(f"  2captcha: Submitting job — site_key={site_key[:20]}...")
    try:
        resp = requests.post("https://2captcha.com/in.php", data={
            "key": TWOCAPTCHA_API_KEY,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }, timeout=30)
        result = resp.json()
        if result.get("status") != 1:
            log.warning(f"  2captcha submission failed: {result}")
            return None
        captcha_id = result["request"]
        log.info(f"  2captcha: Job {captcha_id} queued (~30s)...")
    except Exception as e:
        log.error(f"  2captcha submission error: {e}")
        return None

    for attempt in range(60):  # 60 × 5s = 300s max
        await asyncio.sleep(5)
        try:
            resp = requests.get("https://2captcha.com/res.php", params={
                "key": TWOCAPTCHA_API_KEY, "action": "get",
                "id": captcha_id, "json": 1,
            }, timeout=15)
            result = resp.json()
            if result.get("status") == 1:
                log.info(f"  2captcha: Solved in ~{(attempt + 1) * 5}s ✓")
                return result["request"]
            elif result.get("request") == "CAPCHA_NOT_READY":
                continue
            else:
                log.warning(f"  2captcha error: {result}")
                return None
        except Exception as e:
            log.warning(f"  2captcha poll error: {e}")
    log.error("  2captcha: Timed out after 300s")
    return None


async def handle_masscourts_captcha(page: Page):
    """
    Handle the MassCourts welcome/CAPTCHA gate.

    The gate appears at search.page.79 (no redirect). Two variants:
      a) Gate WITHOUT reCAPTCHA — auto-clicks "Click Here" immediately.
      b) Gate WITH reCAPTCHA    — tries 2captcha auto-solve; falls back to manual wait.

    After clearing the gate, re-navigates to search.page.79 to confirm the session
    cookie is set and the actual search page loads.
    """
    # Give the page a moment to fully render before checking
    await page.wait_for_timeout(1500)

    body_text = await page.inner_text("body")
    has_recaptcha   = "not a robot" in body_text.lower()
    is_welcome_gate = "Trial Court Case Access" in body_text or "Click Here" in body_text

    if not has_recaptcha and not is_welcome_gate:
        return  # Normal page

    if not has_recaptcha:
        # Welcome gate — no CAPTCHA, click through
        log.info("MassCourts: Welcome gate detected — clicking 'Click Here' automatically")
        clicked = await page.evaluate("""
            (() => {
                const link = Array.from(document.querySelectorAll('a'))
                    .find(a => a.textContent.trim() === 'Click Here');
                if (link) { link.click(); return true; }
                return false;
            })()
        """)
        if clicked:
            await page.wait_for_timeout(4000)
            # Re-navigate to confirm session cookie is active
            body_after = await page.inner_text("body")
            if "Trial Court Case Access" not in body_after and "Click Here" not in body_after:
                log.info("  Gate cleared ✓")
                return
            # Cookie may not have taken — navigate directly
            await page.goto(f"{MASSCOURTS_BASE}search.page.79")
            await page.wait_for_timeout(3000)
        return

    # ── reCAPTCHA gate ────────────────────────────────────────────────────────
    log.info("MassCourts: reCAPTCHA detected — attempting 2captcha auto-solve")
    token = await solve_recaptcha_v2(page)

    if token:
        await page.evaluate(f"""
            (() => {{
                const ta = document.getElementById('g-recaptcha-response');
                if (ta) {{
                    ta.style.display = 'block';
                    ta.innerHTML = '{token}';
                    ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
                const widget = document.querySelector('.g-recaptcha');
                const cbName = widget ? widget.getAttribute('data-callback') : null;
                if (cbName && window[cbName]) {{
                    try {{ window[cbName]('{token}'); }} catch(e) {{}}
                }}
            }})()
        """)
        await page.wait_for_timeout(2000)
        await page.evaluate("""
            (() => {
                const link = Array.from(document.querySelectorAll('a'))
                    .find(a => a.textContent.trim() === 'Click Here');
                if (link) link.click();
            })()
        """)
        await page.wait_for_timeout(4000)

        body_check = await page.inner_text("body")
        if "not a robot" not in body_check.lower() and "Trial Court Case Access" not in body_check:
            log.info("  reCAPTCHA auto-solved ✓")
            await page.goto(f"{MASSCOURTS_BASE}search.page.79")
            await page.wait_for_timeout(3000)
            return
        log.warning("  Token injection did not clear gate — falling back to manual wait")

    # Manual fallback
    log.info("=" * 60)
    log.info("ACTION REQUIRED: MassCourts reCAPTCHA cannot be auto-solved.")
    log.info("Open the Chromium window, check 'I'm not a robot', then click 'Click Here'.")
    log.info("Script will detect when the gate is cleared (waiting up to 120s)...")
    log.info("=" * 60)
    for _ in range(60):
        await page.wait_for_timeout(2000)
        try:
            body_text = await page.inner_text("body")
            if "not a robot" not in body_text.lower() and "Trial Court Case Access" not in body_text:
                log.info("  Gate cleared manually ✓")
                return
        except Exception:
            pass
    log.error("  reCAPTCHA not solved within 120s — navigation may fail for this context")


async def navigate_to_case_type_tab(page: Page, dept_fragment: str, div_code: str) -> bool:
    """
    Navigate to the Case Type search tab using Wicket multi-hop.

    Wicket requires dept+div to be set on EVERY hop page before the Case Type
    tab link appears. BA and ES counties reliably require 3+ hops.

    Pattern:
      1. Go to search.page.79, set dept+div → Search link appears → navigate (hop 1)
      2. On hop 1 result page, set dept+div AGAIN → new Search link → navigate (hop 2)
      3. 'Case Type' tab should appear on hop 2 result page.
      4. If not (BA/ES), repeat until it appears (up to 12 attempts).

    Returns True on success, False after exhausting retries.
    """
    start_url = f"{MASSCOURTS_BASE}search.page.79"
    await page.goto(start_url)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    await handle_masscourts_captcha(page)

    last_url   = None
    stuck_count = 0

    for attempt in range(14):
        current_url = page.url
        log.info(f"  [hop {attempt}] ...{current_url[-60:]}")

        # Stuck detection — same URL 3 times in a row → restart from search.page.79
        if current_url == last_url:
            stuck_count += 1
            if stuck_count >= 3:
                log.warning(f"  Stuck on same URL for {stuck_count} attempts — resetting to search.page.79")
                await page.goto(start_url)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(3000)
                await handle_masscourts_captcha(page)
                stuck_count = 0
                last_url = None
                continue
        else:
            last_url    = current_url
            stuck_count = 0

        # Check if Case Type tab is already present BEFORE setting dept/div
        case_type_href = await _get_case_type_href(page)
        if case_type_href:
            ct_url = _abs_url(case_type_href)
            await page.goto(ct_url)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3500)
            # SKILL.md: wait 3s after navigating Case Type href before checking caseCd
            if await page.query_selector('select[name="caseCd"]'):
                log.info(f"  ✓ Case Type tab reached on hop {attempt}")
                return True
            log.warning("  Case Type href navigated but caseCd select not found — retrying")
            continue

        # Set dept+div (triggers Wicket AJAX)
        await set_dept_and_div(page, dept_fragment, div_code)

        # Check again — Case Type tab might appear after dept/div AJAX
        case_type_href = await _get_case_type_href(page)
        if case_type_href:
            ct_url = _abs_url(case_type_href)
            await page.goto(ct_url)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3500)
            if await page.query_selector('select[name="caseCd"]'):
                log.info(f"  ✓ Case Type tab reached on hop {attempt} (post dept/div)")
                return True

        # Get Search link and advance Wicket state
        href = await get_search_href(page)
        if not href:
            log.warning(f"  No Search href on hop {attempt} — retrying")
            await page.wait_for_timeout(2500)
            continue

        full_url = _abs_url(href)
        log.info(f"  → {full_url[:80]}")
        await page.goto(full_url)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)
        await handle_masscourts_captcha(page)

    log.error(f"  Could not reach Case Type tab after 14 attempts for {div_code}")
    return False


async def navigate_to_land_court_case_type_tab(page: Page) -> bool:
    """
    Like navigate_to_case_type_tab but uses set_land_court_dept_and_div (text-based,
    auto-selects first division) instead of the value-fragment approach.
    Called by run_pre_foreclosure and run_tax_lien.
    """
    start_url = f"{MASSCOURTS_BASE}search.page.79"
    await page.goto(start_url)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    await handle_masscourts_captcha(page)

    last_url    = None
    stuck_count = 0

    for attempt in range(14):
        current_url = page.url
        log.info(f"  [LC hop {attempt}] ...{current_url[-60:]}")

        if current_url == last_url:
            stuck_count += 1
            if stuck_count >= 3:
                log.warning(f"  LC stuck — resetting to search.page.79")
                await page.goto(start_url)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(3000)
                await handle_masscourts_captcha(page)
                stuck_count = 0
                last_url = None
                continue
        else:
            last_url    = current_url
            stuck_count = 0

        case_type_href = await _get_case_type_href(page)
        if case_type_href:
            ct_url = _abs_url(case_type_href)
            await page.goto(ct_url)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3500)
            if await page.query_selector('select[name="caseCd"]'):
                log.info(f"  ✓ Land Court Case Type tab reached on hop {attempt}")
                return True
            log.warning("  LC Case Type href navigated but caseCd not found — retrying")
            continue

        await set_land_court_dept_and_div(page)

        case_type_href = await _get_case_type_href(page)
        if case_type_href:
            ct_url = _abs_url(case_type_href)
            await page.goto(ct_url)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3500)
            if await page.query_selector('select[name="caseCd"]'):
                log.info(f"  ✓ Land Court Case Type tab reached on hop {attempt} (post dept/div)")
                return True

        href = await get_search_href(page)
        if not href:
            log.warning(f"  LC: No Search href on hop {attempt} — retrying")
            await page.wait_for_timeout(2500)
            continue

        full_url = _abs_url(href)
        log.info(f"  LC → {full_url[:80]}")
        await page.goto(full_url)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)
        await handle_masscourts_captcha(page)

    log.error("  Could not reach Land Court Case Type tab after 14 attempts")
    return False


def _abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{MASSCOURTS_BASE}{href}"


async def _get_case_type_href(page: Page) -> Optional[str]:
    """Return the Case Type tab href if it's present, else None."""
    return await page.evaluate("""
        (() => {
            const link = Array.from(document.querySelectorAll('a[href^="?x="]'))
                .find(a => a.textContent.trim() === 'Case Type');
            return link ? link.getAttribute('href') : null;
        })()
    """)


async def fetch_pdf_bytes(page: Page, pdf_url: str, retries: int = 2) -> Optional[bytes]:
    """
    Download PDF bytes using the authenticated browser session.
    Retries up to `retries` times with a short wait on failure.
    """
    if not pdf_url:
        return None

    original = pdf_url
    if pdf_url.startswith("/"):
        pdf_url = f"https://www.masscourts.org{pdf_url}"
    elif not pdf_url.startswith("http"):
        pdf_url = f"https://www.masscourts.org/eservices/{pdf_url}"
    if pdf_url != original:
        log.info(f"  PDF URL resolved: {original[:60]} → {pdf_url[:80]}")

    for attempt in range(retries + 1):
        try:
            response = await page.context.request.get(pdf_url)
            if response.ok:
                body = await response.body()
                log.info(f"  PDF downloaded: {len(body):,} bytes (attempt {attempt + 1})")
                return body
            log.warning(f"  PDF fetch returned {response.status} (attempt {attempt + 1}): {pdf_url[:80]}")
        except Exception as e:
            log.warning(f"  PDF fetch error attempt {attempt + 1} ({type(e).__name__}): {e}")
        if attempt < retries:
            await page.wait_for_timeout(3000)

    log.error(f"  PDF download failed after {retries + 1} attempts: {pdf_url[:80]}")
    return None


async def get_image_url(page: Page) -> Optional[str]:
    """
    Click the 'Image' link for the petition document and capture the window.open URL.

    Strategy:
      1. Look for a docket row whose description contains a petition keyword
         ("Petition for Informal", "Petition for Formal", "MPC 150", "MPC 160").
         Click that row's Image link.
      2. Fall back to the FIRST Image link on the page if no petition row is found.

    This handles cases like BR26P0899EA where the petition is at image #6 rather
    than image #1 (image #1 was a Certificate of Death with no PDF).
    """
    await page.evaluate("""
        window._capturedPdfUrl = null;
        window.open = function(url) { window._capturedPdfUrl = url; return null; };
    """)

    # Count all Image links so we can detect zero-image cases quickly.
    imgs = await page.evaluate("""
        Array.from(document.querySelectorAll('a'))
            .filter(a => a.textContent.trim() === 'Image').length
    """)
    if imgs == 0:
        return None

    # Try to find a petition row first.
    # MassCourts docket: each row is a <tr>; the description cell is usually the
    # second <td>.  We walk up from each Image link to its containing <tr> and
    # check whether that row's text contains a petition keyword.
    petition_keywords = [
        "Petition for Informal",
        "Petition for Formal",
        "MPC 150",
        "MPC 160",
        "Petition for Prob",   # covers "Petition for Probate of Will"
        "Petition for Appoint",
    ]
    js_keywords = json.dumps(petition_keywords)

    clicked = await page.evaluate(f"""
        (function() {{
            const keywords = {js_keywords};
            const imageLinks = Array.from(document.querySelectorAll('a'))
                .filter(a => a.textContent.trim() === 'Image');

            // Walk up to find a <tr> ancestor, then check its text for petition keywords.
            for (const link of imageLinks) {{
                let el = link.parentElement;
                while (el && el.tagName !== 'TR') el = el.parentElement;
                if (!el) continue;
                const rowText = el.textContent || '';
                if (keywords.some(kw => rowText.indexOf(kw) !== -1)) {{
                    link.click();
                    return 'petition';
                }}
            }}
            // Fallback: click first Image link.
            imageLinks[0].click();
            return 'first';
        }})()
    """)

    log.info(f"  get_image_url: selected '{clicked}' image link (of {imgs} total)")
    await page.wait_for_timeout(4000)
    return await page.evaluate("window._capturedPdfUrl")

# ── Row builders ──────────────────────────────────────────────────────────────

def build_probate_row(
    pull_date: str, file_date: str,
    case_num: str, initiating_action: str,
    p1: dict, p2: dict, venue: dict,
    arcgis: Optional[dict],
    dec_first: str, dec_last: str,
) -> list:
    """Build a 42-column row for a probate case."""
    # Petitioner 1 info (mailing address — goes in L-O and AA-AD)
    pet1_name  = p1.get("petitioner1_name", "")
    pet1_str   = p1.get("petitioner1_street", "")
    pet1_city  = p1.get("petitioner1_city", "")
    pet1_state = p1.get("petitioner1_state", "")
    pet1_zip   = p1.get("petitioner1_zip", "")
    pet1_phone = fmt_phone(p1.get("petitioner1_phone", ""))
    pet1_email = p1.get("petitioner1_email", "")
    pet1_rel   = p1.get("petitioner1_relation", "")

    pet2_name  = p2.get("petitioner2_name", "")   if p2 else ""
    pet2_str   = p2.get("petitioner2_street", "")  if p2 else ""
    pet2_city  = p2.get("petitioner2_city", "")   if p2 else ""
    pet2_state = p2.get("petitioner2_state", "")  if p2 else ""
    pet2_zip   = p2.get("petitioner2_zip", "")    if p2 else ""
    pet2_phone = fmt_phone(p2.get("petitioner2_phone", ""))  if p2 else ""
    pet2_email = p2.get("petitioner2_email", "")  if p2 else ""

    # E = OWNER1 from ArcGIS — the actual property owner (decedent, spouse, or family trust)
    arcgis_owner = arcgis.get("OWNER1", "") if arcgis else f"{dec_first} {dec_last}".strip()

    # G-J: MA property address from the petition PDF.
    # If venue_domiciled (decedent lived in MA), use decedent's address from page 1.
    # Otherwise use the venue property address from page 2.
    # ArcGIS SITE_ADDR is used only as a last-resort fallback when PDF extraction yields nothing.
    if venue.get("venue_domiciled"):
        prop_street = p1.get("decedent_street", "")
        prop_city   = p1.get("decedent_city", "")
        prop_state  = p1.get("decedent_state", "MA") or "MA"
        prop_zip    = p1.get("decedent_zip", "")
    else:
        raw_addr = venue.get("venue_property_address", "")
        parts = [x.strip() for x in raw_addr.split(",")] if raw_addr else []
        prop_street = parts[0] if parts else ""
        prop_city   = parts[1] if len(parts) > 1 else ""
        prop_state  = "MA"
        prop_zip    = ""

    # Fallback 1: venue extraction returned no address (often MPC 150 form misread).
    # Use decedent's page-1 address — better than blank G-J.
    if not prop_street:
        prop_street = p1.get("decedent_street", "")
        if not prop_city:
            prop_city = p1.get("decedent_city", "")
        if not prop_zip:
            prop_zip = p1.get("decedent_zip", "")

    # Fallback 2: PDF gave us nothing at all — use ArcGIS SITE_ADDR/CITY.
    # Covers trust petitions and cases where decedent had no MA address in the form.
    if not prop_street and arcgis:
        prop_street = arcgis.get("SITE_ADDR", "")
        if not prop_city:
            prop_city = arcgis.get("CITY", "").title()
        if not prop_zip:
            prop_zip = str(arcgis.get("ZIP") or "")

    return [
        pull_date,           # A  Pull Date
        file_date,           # B  File Date
        dec_first,           # C  Lead First Name
        dec_last,            # D  Lead Last Name
        arcgis_owner,        # E  Lead Owner Name — OWNER1 from ArcGIS (actual property owner)
        "",                  # F  Lead Status
        prop_street,         # G  Lead Street  — MA property address from petition PDF
        prop_city,           # H  Lead City
        prop_state,          # I  Lead State
        fmt_zip(prop_zip),   # J  Lead Zip
        "",                  # K  Lead Notes — blank for all lists
        pet1_str,            # L  Customer Street — petitioner mailing address
        pet1_city,           # M  Customer City
        pet1_state,          # N  Customer State
        fmt_zip(pet1_zip),   # O  Customer Zip
        "",                  # P  Sales Date
        initiating_action,   # Q  Probate Type
        case_num,            # R  Probate Case #
        "",                  # S  Tax Foreclosure
        "",                  # T  Preforeclosure Case — blank for probate rows
        "",                  # U  Auction Date
        "",                  # V  Campaign Lists
        "",                  # W  Lead Record Type
        "",                  # X  Owner 1 Phone
        "",                  # Y  Owner 1 Deceased
        "",                  # Z  Owner 1 Notes
        pet1_name,           # AA Relative 1 Name
        pet1_str,            # AB Relative 1 Mailing Street
        pet1_city,           # AC Relative 1 Mailing City
        pet1_state,          # AD Relative 1 Mailing State
        fmt_zip(pet1_zip),   # AE Relative 1 Mailing Zip
        pet1_phone,          # AF Relative 1 Phone
        "",                  # AG Relative 1 CNAM
        pet1_email,          # AH Relative 1 Email
        pet1_rel,            # AI Relative 1 Relationship
        "",                  # AJ Relative 1 Notes
        pet2_name,           # AK Relative 2 Name
        pet2_str,            # AL Relative 2 Mailing Street
        pet2_city,           # AM Relative 2 Mailing City
        pet2_state,          # AN Relative 2 Mailing State
        pet2_zip,            # AO Relative 2 Mailing Zip
        pet2_phone,          # AP Relative 2 Phone
        pet2_email,          # AQ Relative 2 Email
    ]

def build_servicemembers_row(
    pull_date: str, file_date: str, case_num: str,
    first: str, last: str, owner1: str,
    prop_street: str, prop_city: str, prop_zip: str,
    own_addr: str, own_city: str, own_state: str, own_zip: str,
) -> list:
    row = [""] * 43
    row[0]  = pull_date
    row[1]  = file_date
    row[2]  = first
    row[3]  = last
    row[4]  = owner1
    row[6]  = prop_street
    row[7]  = prop_city
    row[8]  = "MA"
    row[9]  = fmt_zip(prop_zip)
    # row[10] = Lead Notes — intentionally blank for pre-foreclosure rows
    # L-O (indices 11-14): owner mailing address from ArcGIS
    row[11] = own_addr
    row[12] = own_city
    row[13] = own_state
    row[14] = fmt_zip(own_zip)
    row[19] = case_num   # T: Preforeclosure Case
    return row

def build_tax_lien_row(
    pull_date: str, file_date: str, case_num: str,
    first: str, last: str, owner1: str,
    prop_street: str, prop_city: str, prop_zip: str,
    own_addr: str, own_city: str, own_state: str, own_zip: str,
) -> list:
    row = build_servicemembers_row(
        pull_date, file_date, case_num,
        first, last, owner1,
        prop_street, prop_city, prop_zip,
        own_addr, own_city, own_state, own_zip,
    )
    # S (index 18): Tax Foreclosure column — holds the tax lien case number
    row[18] = case_num
    row[19] = ""   # T: Preforeclosure Case — blank for tax lien rows
    return row

def build_foreclosure_row(
    pull_date: str, notice_date: str,
    owner_first: str, owner_last: str,
    prop_street: str, prop_city: str, prop_zip: str,
    auction_date: str,
    own_name: str,
    own_addr: str, own_city: str, own_state: str, own_zip: str,
) -> list:
    # Strip time from auction_date ("May 12, 2026 at 4:00 PM" → "May 12, 2026")
    auction_date_clean = re.split(r"\s+at\s+", auction_date, maxsplit=1)[0].strip()

    row = [""] * 43
    row[0]  = pull_date
    row[1]  = notice_date
    row[2]  = owner_first
    row[3]  = owner_last
    row[4]  = own_name          # E  Lead Owner Name — OWNER1 from ArcGIS
    row[6]  = prop_street
    row[7]  = prop_city
    row[8]  = "MA"
    row[9]  = fmt_zip(prop_zip)
    # row[10] = "" — Lead Notes blank for all non-probate lists
    # L-O (indices 11-14): owner mailing address from ArcGIS
    row[11] = own_addr          # L  Customer Street
    row[12] = own_city          # M  Customer City
    row[13] = own_state         # N  Customer State
    row[14] = fmt_zip(own_zip)  # O  Customer Zip
    # row[18] = "" — Tax Foreclosure column only for tax liens
    # row[19] = "" — Preforeclosure Case column only for servicemembers rows
    row[20] = auction_date_clean  # U  Auction Date — date only, no time
    return row

# ── CSV helpers ───────────────────────────────────────────────────────────────
HEADERS = [
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
    "Relative 2 Email",
]

def csv_append(path: Path, row: list):
    exists = path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(HEADERS)
        w.writerow(row)

def csv_read_all(path: Path) -> list[list]:
    if not path.exists():
        return []
    with open(path) as f:
        reader = csv.reader(f)
        rows = list(reader)
    return rows[1:] if rows else []  # skip header

# ── Apps Script / Sheet writing ───────────────────────────────────────────────

def apps_script_post(payload: dict) -> bool:
    """POST a payload to the deployed Apps Script web app."""
    if not APPS_SCRIPT_URL:
        log.warning("APPS_SCRIPT_URL not set — skipping sheet write")
        return False
    try:
        payload["secret"] = APPS_SCRIPT_SECRET
        r = requests.post(APPS_SCRIPT_URL, json=payload, timeout=60)
        log.info(f"Apps Script HTTP {r.status_code} | action={payload.get('action')} | body={r.text[:300]!r}")
        result = r.json()
        if result.get("status") != "ok":
            log.warning(f"Apps Script error: {result}")
            return False
        return True
    except Exception as e:
        log.error(f"Apps Script POST failed: {e} | url={APPS_SCRIPT_URL[:60]} | response={getattr(r,'text','N/A')[:200]!r}")
        return False

def create_sheet_tab(tab_name: str):
    apps_script_post({"action": "create_tab", "tabName": tab_name})

def clear_sheet_tab(tab_name: str):
    """Clear all data rows (row 2+) from a tab, keeping the header. Safe to call on re-runs."""
    result = apps_script_post({"action": "clear_tab", "tabName": tab_name})
    log.info(f"Cleared tab '{tab_name}' before writing")

def write_rows_to_sheet(tab_name: str, rows: list[list]):
    if not rows:
        return
    for i in range(0, len(rows), 50):
        batch = rows[i:i+50]
        apps_script_post({"action": "append_rows", "tabName": tab_name, "rows": batch})
        log.info(f"  Wrote batch {i}–{i+len(batch)} to tab '{tab_name}'")

def write_no_image_row(pull_date: str, file_date: str, case_num: str, county: str):
    row = [""] * 42
    row[0]  = pull_date
    row[1]  = file_date
    row[10] = case_num
    row[17] = case_num
    row[16] = county
    apps_script_post({"action": "append_rows", "tabName": "No Images", "rows": [row]})

def write_skipped_row(case_num: str, case_type: str, owner: str, reason: str, file_date: str):
    apps_script_post({
        "action": "append_skipped",
        "row": [case_num, case_type, owner, reason, file_date],
    })

def send_notification_email(subject: str, body: str):
    apps_script_post({
        "action": "send_email",
        "to": NOTIFICATION_EMAIL,
        "subject": subject,
        "body": body,
    })

# ── MassCourts probate worker ─────────────────────────────────────────────────

async def run_probate_counties(
    context: BrowserContext,
    counties: list[str],
    from_date: date,
    to_date: date,
    csv_path: Path,
    client: anthropic.Anthropic,
    skip_cases: set[str] | None = None,
):
    """Process EA probate cases for a list of counties.
    skip_cases: if provided, case numbers in this set are skipped (used by sweep agent).
    """
    if not counties:
        return

    page = await context.new_page()
    from_str = fmt_date(from_date)
    to_str   = fmt_date(to_date)
    pull_str = fmt_date(date.today())
    file_str = f"{from_str}–{to_str}" if from_str != to_str else from_str

    for county in counties:
        log.info(f"[{county}] Starting probate search {from_str} → {to_str}")
        div_code = COUNTY_DIV[county]

        ok = await navigate_to_case_type_tab(page, "PF_DEPT", div_code)
        if not ok:
            log.error(f"[{county}] Could not reach Case Type tab — skipping county")
            continue

        # Wait for caseCd select (SKILL.md: not present immediately after navigation)
        try:
            await page.wait_for_selector('select[name="caseCd"]', timeout=10000)
        except Exception:
            log.error(f"[{county}] caseCd select never appeared — skipping county")
            continue

        # Submit EA search
        await page.evaluate(f"""
            const sel = document.querySelector('select[name="caseCd"]');
            Array.from(sel.options).forEach(o => o.selected = false);
            Array.from(sel.options).find(o => o.value.trim() === 'EA').selected = true;
            document.querySelector('input[name="fileDateRange:dateInputBegin"]').value = '{from_str}';
            document.querySelector('input[name="fileDateRange:dateInputEnd"]').value = '{to_str}';
            document.querySelector('input[type="submit"][name="submitLink"]').click();
        """)
        await page.wait_for_timeout(3000)

        results_url = page.url
        log.info(f"[{county}] Results URL: {results_url[-60:]}")

        # Collect ALL case hrefs upfront across all pages (SKILL.md: tokens expire on navigate)
        all_cases: dict[str, str] = {}
        page_num = 1

        while True:
            page_text = await page.inner_text("body")

            # Strip "Pay " prefix from party names before extracting case numbers
            # (MassCourts prepends "Pay" button labels in some result rows)
            case_nums = re.findall(r"[A-Z]{2}\d{2}P\d+EA", page_text)

            # Collect all hrefs in one JS call per page
            page_hrefs = await page.evaluate(f"""
                (() => {{
                    const nums = {json.dumps(case_nums)};
                    const result = {{}};
                    nums.forEach(cn => {{
                        const a = Array.from(document.querySelectorAll('a'))
                            .find(el => el.textContent.trim() === cn);
                        if (a) result[cn] = a.getAttribute('href');
                    }});
                    return result;
                }})()
            """)
            for cn, href in (page_hrefs or {}).items():
                if cn not in all_cases and href:
                    all_cases[cn] = href

            # Paginate: find ">" link (Wicket AJAX — get href and navigate directly)
            next_href = await page.evaluate("""
                (() => {
                    const links = Array.from(document.querySelectorAll('a[href^="?x="]'));
                    const next = links.find(a => {
                        const t = a.textContent.trim();
                        return t === '>' || t === 'Next' || t === '»';
                    });
                    return next ? next.getAttribute('href') : null;
                })()
            """)
            if not next_href:
                break
            page_num += 1
            log.info(f"[{county}] Paginating to page {page_num}")
            await page.goto(_abs_url(next_href))
            await page.wait_for_timeout(2500)

        log.info(f"[{county}] Found {len(all_cases)} cases")

        for case_num, case_href in all_cases.items():
            if skip_cases and case_num in skip_cases:
                log.info(f"[{county}] SWEEP SKIP {case_num}: already in sheet")
                continue
            try:
                await process_probate_case(
                    page, context, case_num, case_href,
                    pull_str, file_str, csv_path, client, county=county,
                )
            except Exception as e:
                log.error(f"[{county}] Error on {case_num}: {e}")
                traceback.print_exc()

    await page.close()


async def process_probate_case(
    page: Page,
    context: BrowserContext,
    case_num: str,
    case_href: str,
    pull_str: str,
    file_str: str,
    csv_path: Path,
    client: anthropic.Anthropic,
    county: str = "",
):
    """Navigate to a probate case, read PDF, extract data, write to CSV."""
    log.info(f"  Processing {case_num}")
    await page.goto(_abs_url(case_href))
    await page.wait_for_timeout(2500)

    page_text = await page.inner_text("body")

    # Extract actual file date for this case
    fd_match = re.search(r"File\s*Date\s*[:\s]+(\d{1,2}/\d{1,2}/\d{4})", page_text, re.IGNORECASE)
    actual_file_str = fd_match.group(1).strip() if fd_match else file_str

    # Parse initiating action
    ia_match = re.search(r"Initiating Action\s*[:\s]+([^\n]+)", page_text)
    initiating_action = ia_match.group(1).strip() if ia_match else ""

    # Skip voluntary and will-only filings
    skip_phrases = ["Voluntary Statement", "Filing of will of deceased no petition"]
    if any(p.lower() in initiating_action.lower() for p in skip_phrases):
        log.info(f"  SKIP {case_num}: {initiating_action}")
        return

    # Get PDF URL (via window.open interception)
    pdf_url = await get_image_url(page)
    if not pdf_url:
        # SKILL.md: Closed same-day cases may have zero Image links — record as NO_IMAGE
        log.warning(f"  {case_num}: No image found — writing to No Images tab")
        write_no_image_row(pull_str, actual_file_str, case_num, county)
        return

    pdf_bytes = await fetch_pdf_bytes(page, pdf_url)
    if not pdf_bytes:
        log.warning(f"  {case_num}: Could not download PDF — writing to No Images tab")
        write_no_image_row(pull_str, actual_file_str, case_num, county)
        return

    # Extract page 1
    try:
        p1_png  = pdf_bytes_to_png(pdf_bytes, 0)
        p1_data = extract_probate_page1(client, p1_png)
    except Exception as e:
        log.error(f"  {case_num}: PDF/Claude error on page 1: {e}")
        return

    # Extract page 2 (venue) — gracefully handle single-page PDFs
    try:
        p2_png  = pdf_bytes_to_png(pdf_bytes, 1)
        _county_names = {
            "BA": "Barnstable", "BR": "Bristol",  "DU": "Dukes",
            "ES": "Essex",      "FR": "Franklin",  "MI": "Middlesex",
            "NA": "Nantucket",  "NO": "Norfolk",   "PL": "Plymouth",
            "SU": "Suffolk",    "WO": "Worcester",
        }
        p2_data = extract_probate_page2(client, p2_png, _county_names.get(county, ""))
    except Exception as e:
        log.warning(f"  {case_num}: Page 2 error (defaulting to domiciled): {e}")
        p2_data = {"venue_domiciled": True, "venue_property_address": ""}

    dec_first = p1_data.get("decedent_first", "").strip()
    dec_last  = p1_data.get("decedent_last", "").strip()
    # Fallback: if AI returned full name in dec_first and left dec_last blank, split it.
    if dec_first and not dec_last and " " in dec_first:
        parts     = dec_first.split(None, 1)
        dec_first = parts[0]
        dec_last  = parts[1]
    pet1_str  = p1_data.get("petitioner1_street", "")
    pet1_city = p1_data.get("petitioner1_city", "")

    _dec_st   = p1_data.get("decedent_street", "")
    _v_dom    = p2_data.get("venue_domiciled")
    _v_addr   = p2_data.get("venue_property_address", "")
    if not _dec_st or (not _v_dom and not _v_addr):
        log.info(f"  {case_num}: addr debug — dec_street='{_dec_st}' dec_city='{p1_data.get('decedent_city','')}' "
                 f"venue_domiciled={_v_dom} venue_addr='{_v_addr}'")

    # ── ArcGIS ownership check ────────────────────────────────────────────────
    # Determine the property address to query (decedent's domicile or venue property)
    if p2_data.get("venue_domiciled"):
        prop_street = p1_data.get("decedent_street", "")
        prop_city   = p1_data.get("decedent_city", "")
        prop_zip    = p1_data.get("decedent_zip", "")
    else:
        addr  = p2_data.get("venue_property_address", "")
        parts = [x.strip() for x in addr.split(",")]
        prop_street = parts[0] if parts else ""
        prop_city   = parts[1] if len(parts) > 1 else ""
        prop_zip    = ""

    arcgis_result = arcgis_by_address(prop_street, prop_city, prop_zip) if prop_street else None

    # Collect all names to check (decedent + petitioners) — used in every lookup below.
    names_to_check = [dec_last]
    for key in ("petitioner1_name", "petitioner2_name"):
        full_name = p1_data.get(key, "")
        if full_name:
            last = full_name.strip().split()[-1] if full_name.strip() else ""
            if last:
                names_to_check.append(last)

    def _owner_matches(result: dict) -> bool:
        o = (result.get("OWNER1") or "").upper()
        return any(clean_last_name(n) and clean_last_name(n) in o for n in names_to_check)

    # If address lookup returned a result but the owner name doesn't match, reset it —
    # the AI may have extracted the wrong address (e.g. decedent's rented home instead
    # of the venue property they own).  Fallbacks below will try to find the right parcel.
    if arcgis_result and not _owner_matches(arcgis_result):
        log.info(f"  {case_num}: address lookup returned '{arcgis_result.get('OWNER1')}' "
                 f"at {arcgis_result.get('SITE_ADDR')}, {arcgis_result.get('CITY')} "
                 f"— owner mismatch, trying fallbacks")
        arcgis_result = None

    # Fallback A: owner name + city
    if not arcgis_result and dec_last:
        arcgis_result = arcgis_by_owner(dec_last, prop_city or None)
        if arcgis_result and not _owner_matches(arcgis_result):
            arcgis_result = None  # name lookup returned wrong person

    # Fallback B: ZIP + house number + owner last name.
    # Catches cases where the AI extracted the wrong street name but we have a valid ZIP
    # (e.g. petitioner's zip) and know the house number.  Tries every ZIP in the petition.
    if not arcgis_result:
        street_parts = prop_street.strip().upper().split() if prop_street else []
        street_num   = street_parts[0] if street_parts and street_parts[0].isdigit() else ""
        zip_candidates = list(dict.fromkeys(filter(None, [
            prop_zip,
            p1_data.get("petitioner1_zip", ""),
            p1_data.get("petitioner2_zip", ""),
            p1_data.get("decedent_zip", ""),
        ])))
        for z in zip_candidates:
            z = str(z).strip()[:5]
            if not z:
                continue
            for name in names_to_check:
                clean = clean_last_name(name)
                if not clean or len(clean) < 3:
                    continue
                where = (
                    f"UPPER(SITE_ADDR) LIKE '{street_num} %'"
                    f" AND UPPER(ZIP) LIKE '{z}%'"
                    f" AND UPPER(OWNER1) LIKE '%{clean}%'"
                )
                hits = arcgis_query(where, record_count=3)
                if hits:
                    arcgis_result = hits[0]
                    log.info(f"  {case_num}: ZIP+number fallback matched "
                             f"{hits[0].get('SITE_ADDR')}, {hits[0].get('CITY')} "
                             f"(owner: {hits[0].get('OWNER1')}) via zip={z}")
                    break
            if arcgis_result:
                break

    # Fallback C: house number + first street word + owner name, no geography filter.
    # Handles parcels where ArcGIS has NULL ZIP *and* the city is a village name
    # that ArcGIS doesn't recognise (e.g. "Marstons Mills" stored as "BARNSTABLE").
    # Using the first street word ("LITTLE") narrows the result to ~1 parcel vs 10+
    # for house-number-only, making this reliable even without a city or ZIP filter.
    if not arcgis_result and street_num:
        street_words = prop_street.strip().upper().split()[1:] if prop_street else []
        first_word   = street_words[0] if street_words else ""
        for name in names_to_check:
            clean = clean_last_name(name)
            if not clean or len(clean) < 3:
                continue
            addr_frag = f"{street_num} {first_word}" if first_word else street_num
            where = (
                f"UPPER(SITE_ADDR) LIKE '{addr_frag}%'"
                f" AND UPPER(OWNER1) LIKE '%{clean}%'"
            )
            hits = arcgis_query(where, record_count=5)
            if hits:
                arcgis_result = hits[0]
                log.info(f"  {case_num}: house+owner fallback matched "
                         f"{hits[0].get('SITE_ADDR')}, {hits[0].get('CITY')} "
                         f"(owner: {hits[0].get('OWNER1')})")
                break

    if not arcgis_result:
        log.warning(f"  {case_num}: No ArcGIS match — skipping (likely renter)")
        write_skipped_row(case_num, "Probate", dec_last, "No ArcGIS match", actual_file_str)
        return

    # Final ownership check (catches edge cases where fallback B returned a hit
    # but the name match was only partial / coincidental)
    owner1 = arcgis_result.get("OWNER1", "")
    owner1_upper = owner1.upper()
    is_owner = _owner_matches(arcgis_result)

    if not is_owner:
        log.info(f"  {case_num}: OWNER1='{owner1}' — no name match, skipping (renter)")
        write_skipped_row(case_num, "Probate", dec_last, f"Renter — OWNER1={owner1}", actual_file_str)
        return

    log.info(f"  {case_num}: OWNER1='{owner1}' matched — including")

    row = build_probate_row(
        pull_str, actual_file_str, case_num, initiating_action,
        p1_data, p1_data,   # p1_data contains both petitioner1_* and petitioner2_* keys
        p2_data, arcgis_result,
        dec_first, dec_last,
    )
    csv_append(csv_path, row)
    # Log the address from the built row (index 6/7), not the caller's prop_street/prop_city
    # which may be empty when Fallback 1 inside build_probate_row filled the address instead.
    _log_street = row[6] if len(row) > 6 else ""
    _log_city   = row[7] if len(row) > 7 else ""
    log.info(f"  {case_num}: {dec_first} {dec_last} → {_log_street}, {_log_city} ✓")

# ── Pre-foreclosure (Servicemembers) worker ───────────────────────────────────

async def _lc_submit_and_collect_cases(
    page: Page,
    case_type_val: str,
    case_type_label: str,
    case_regex: str,
    from_str: str,
    to_str: str,
) -> tuple[dict[str, str], str]:
    """
    Submit a Land Court case type search and collect all case hrefs across pages.
    Returns (case_hrefs_dict, results_url).
    """
    # Fill the Land Court search form and submit.
    # Dispatch 'change' on the select so Wicket's component model updates before submit.
    await page.evaluate(f"""
        const sel = document.querySelector('select[name="caseCd"]');
        if (sel) {{
            sel.value = '{case_type_val}';
            sel.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        const begin = document.querySelector('input[name="fileDateRange:dateInputBegin"]');
        const end   = document.querySelector('input[name="fileDateRange:dateInputEnd"]');
        if (begin) begin.value = '{from_str}';
        if (end)   end.value   = '{to_str}';
        const btn = document.querySelector('input[type="submit"][name="submitLink"]');
        if (btn) btn.click();
    """)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        await page.wait_for_timeout(5000)

    results_url  = page.url
    log.info(f"  {case_type_label}: results URL — {results_url[-80:]}")

    # Debug: log a snippet of the page so we can see what came back after submit.
    _dbg_text = await page.inner_text("body")
    log.info(f"  {case_type_label}: page snippet after submit — {_dbg_text[:300]!r}")

    case_hrefs: dict[str, str] = {}
    page_num = 1
    case_re = re.compile(case_regex)

    while True:
        # Grab ALL ?x= links whose visible text matches the case-number pattern.
        # This is more robust than regex-on-inner-text because it works regardless
        # of whitespace or DOM rendering quirks.
        page_hrefs = await page.evaluate(f"""
            (() => {{
                const pat = new RegExp({json.dumps(case_regex)});
                const result = {{}};
                document.querySelectorAll('a[href^="?x="]').forEach(a => {{
                    const t = a.textContent.trim();
                    if (pat.test(t)) result[t] = a.getAttribute('href');
                }});
                return result;
            }})()
        """)
        for cn, href in (page_hrefs or {}).items():
            if cn not in case_hrefs and href:
                case_hrefs[cn] = href

        next_href = await page.evaluate("""
            (() => {
                const links = Array.from(document.querySelectorAll('a[href^="?x="]'));
                const next = links.find(a => {
                    const t = a.textContent.trim();
                    return t === '>' || t === 'Next' || t === '»';
                });
                return next ? next.getAttribute('href') : null;
            })()
        """)
        if not next_href:
            break
        page_num += 1
        log.info(f"  {case_type_label}: paginating to page {page_num}")
        await page.goto(_abs_url(next_href))
        await page.wait_for_timeout(3000)

    log.info(f"  {case_type_label}: Found {len(case_hrefs)} cases")
    return case_hrefs, results_url


async def run_pre_foreclosure(
    context: BrowserContext,
    from_date: date,
    to_date: date,
    csv_path: Path,
    client: anthropic.Anthropic,
    skip_cases: set[str] | None = None,
):
    log.info("Starting Pre-Foreclosure (Servicemembers) run")
    page     = await context.new_page()
    pull_str = fmt_date(date.today())
    from_str = fmt_date(from_date)
    to_str   = fmt_date(to_date)
    file_str = f"{from_str}–{to_str}" if from_str != to_str else from_str
    log.info(f"Pre-Foreclosure date range: {from_str} → {to_str}")

    ok = await navigate_to_land_court_case_type_tab(page)
    if not ok:
        log.error("Pre-foreclosure: Could not reach Land Court Case Type tab")
        await page.close()
        return

    try:
        await page.wait_for_selector('select[name="caseCd"]', timeout=10000)
    except Exception:
        log.error("Pre-foreclosure: caseCd select never appeared")
        await page.close()
        return

    sm_val = await page.evaluate("""
        (() => {
            const sel = document.querySelector('select[name="caseCd"]');
            const opt = Array.from(sel.options)
                .find(o => o.text.includes('Servicemember') || o.value === 'SM');
            return opt ? opt.value : null;
        })()
    """)
    if not sm_val:
        avail = await page.evaluate("""
            Array.from(document.querySelector('select[name="caseCd"]').options)
                .map(o => o.value + '=' + o.text.trim()).slice(0, 20)
        """)
        log.error(f"Could not find Servicemembers option. Available: {avail}")
        await page.close()
        return

    log.info(f"Pre-Foreclosure: using caseCd value={sm_val!r}")
    case_hrefs, results_url = await _lc_submit_and_collect_cases(
        page, sm_val, "Pre-fc", r"\d{2} SM \d+", from_str, to_str,
    )
    if not case_hrefs:
        log.info("Pre-foreclosure: No Servicemembers cases found")
        await page.close()
        return

    for case_num, href in case_hrefs.items():
        if skip_cases and case_num in skip_cases:
            log.info(f"  SWEEP SKIP {case_num}: already in sheet")
            continue
        try:
            await page.goto(_abs_url(href))
            await page.wait_for_timeout(2500)
            case_text = await page.inner_text("body")

            fd_match = re.search(r"File\s*Date\s*[:\s]+(\d{1,2}/\d{1,2}/\d{4})", case_text, re.IGNORECASE)
            case_file_str = fd_match.group(1).strip() if fd_match else file_str

            # Detect municipality-filed SM complaints (tax-lien content in an SM case).
            # Municipalities file SM cases as part of the tax-lien foreclosure process.
            # If the plaintiff is "City of X" or "Town of X", use the tax lien prompt
            # so the AI extracts "Assessed to:" correctly instead of garbling it.
            plaintiff_name  = extract_party_name(case_text, "Plaintiff")
            is_muni_tl      = bool(re.match(r"(?:City|Town)\s+of\b", plaintiff_name, re.IGNORECASE))
            if is_muni_tl:
                log.info(f"  {case_num}: Municipality-filed SM (tax lien content) — plaintiff: {plaintiff_name!r}")

            # Try to extract property address from page text first.
            # New format (April 2026 redesign): "Property Information\n123 Main St\nBoston"
            addr_match = re.search(
                r"Property\s+Information\s*\n([0-9]+[^\n]+)\n([A-Za-z][^\n]+)",
                case_text, re.IGNORECASE,
            )
            if not addr_match:
                # Old format: "Property Address: 123 Main St, Boston, MA"
                addr_match = re.search(
                    r"(?:Property\s+Address|Property)[:\s]+([0-9]+\s+[A-Za-z0-9\s]+?),\s*([A-Za-z ]+),?\s*MA",
                    case_text, re.IGNORECASE,
                )
            if addr_match:
                prop_street = addr_match.group(1).strip()
                prop_city   = addr_match.group(2).strip()
                prop_zip    = ""
                pdf_fields  = {}
            else:
                # Fall back to complaint PDF — use tax_lien prompt for municipality cases
                pdf_url = await get_image_url(page)
                prop_street, prop_city, prop_zip = "", "", ""
                pdf_fields = {}
                if pdf_url:
                    pdf_bytes = await fetch_pdf_bytes(page, pdf_url)
                    if pdf_bytes:
                        try:
                            png       = pdf_bytes_to_png(pdf_bytes, 0)
                            prompt_t  = "tax_lien" if is_muni_tl else "servicemembers"
                            pdf_fields = extract_complaint_fields(client, png, prompt_t)
                            prop_street = pdf_fields.get("property_street", "")
                            prop_city   = pdf_fields.get("property_city", "")
                            prop_zip    = pdf_fields.get("property_zip", "")
                        except Exception as e:
                            log.warning(f"  {case_num} complaint extraction error: {e}")

            if not prop_street:
                log.warning(f"  {case_num}: No property address — skipping")
                write_skipped_row(case_num, "Servicemembers", "", "No address found", case_file_str)
                continue

            # Get defendant name — uses extract_party_name() which handles the MassCourts
            # "Defendant(s) : 469269  Date: 04/21/2026\nJOHN DOE" format and the
            # "Defendant Filing Fee: $240.00" false-match and "Pay " prefix issues.
            defendant_name = extract_party_name(case_text, "Defendant")

            # ArcGIS 3-step: address first, then owner name fallback.
            arc = arcgis_by_address(prop_street, prop_city, prop_zip)
            if not arc and defendant_name:
                arc = arcgis_by_owner(defendant_name, prop_city)
            if not arc:
                log.warning(f"  {case_num}: No ArcGIS match for {prop_street}, {prop_city} — including anyway")

            owner1 = arc.get("OWNER1", "") if arc else ""

            if is_muni_tl:
                # For municipality-filed SM cases: use "Assessed to" from PDF (the property owner),
                # or fall back to the defendant name / ArcGIS OWNER1.
                assessed_to = pdf_fields.get("assessed_to", "") or defendant_name or owner1
                first, last = split_owner_name(
                    assessed_to,
                    natural_order=bool(assessed_to and "," not in assessed_to),
                )
                row = build_tax_lien_row(
                    pull_str, case_file_str, case_num,
                    first, last, (arc.get("OWNER1", assessed_to) if arc else assessed_to),
                    prop_street, prop_city, (str(arc.get("ZIP") or prop_zip or "") if arc else (prop_zip or "")),
                    ((arc.get("OWN_ADDR") or "") if arc else ""),
                    ((arc.get("OWN_CITY") or "") if arc else ""),
                    ((arc.get("OWN_STATE") or "") if arc else ""),
                    (str(arc.get("OWN_ZIP") or "") if arc else ""),
                )
            else:
                # Standard servicemembers case: prefer defendant name (court record)
                name_source = defendant_name if defendant_name else owner1
                # Defendant names from MassCourts may be "LAST, FIRST" or "FIRST LAST"
                first, last = split_owner_name(name_source, natural_order=("," not in name_source))
                row = build_servicemembers_row(
                    pull_str, case_file_str, case_num,
                    first, last, owner1,
                    prop_street, prop_city, (str(arc.get("ZIP") or prop_zip or "") if arc else (prop_zip or "")),
                    ((arc.get("OWN_ADDR") or "") if arc else ""),
                    ((arc.get("OWN_CITY") or "") if arc else ""),
                    ((arc.get("OWN_STATE") or "") if arc else ""),
                    (str(arc.get("OWN_ZIP") or "") if arc else ""),
                )

            csv_append(csv_path, row)
            log.info(f"  {case_num}: {first} {last} → {prop_street}, {prop_city} ✓")

        except Exception as e:
            log.error(f"  Pre-fc error on {case_num}: {e}")

        # Navigate back to results (Wicket-safe — no history.back())
        try:
            await page.goto(results_url)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    await page.close()
    log.info("Pre-Foreclosure complete")

# ── Tax Lien worker ───────────────────────────────────────────────────────────

async def run_tax_lien(
    context: BrowserContext,
    from_date: date,
    to_date: date,
    csv_path: Path,
    client: anthropic.Anthropic,
    skip_cases: set[str] | None = None,
):
    log.info("Starting Tax Lien run")
    page     = await context.new_page()
    pull_str = fmt_date(date.today())
    from_str = fmt_date(from_date)
    to_str   = fmt_date(to_date)
    file_str = f"{from_str}–{to_str}" if from_str != to_str else from_str
    log.info(f"Tax Lien date range: {from_str} → {to_str}")

    ok = await navigate_to_land_court_case_type_tab(page)
    if not ok:
        log.error("Tax lien: Could not reach Land Court Case Type tab")
        await page.close()
        return

    try:
        await page.wait_for_selector('select[name="caseCd"]', timeout=10000)
    except Exception:
        log.error("Tax lien: caseCd select never appeared")
        await page.close()
        return

    tl_val = await page.evaluate("""
        (() => {
            const sel = document.querySelector('select[name="caseCd"]');
            const opt = Array.from(sel.options)
                .find(o => o.text.includes('Tax Lien') || o.value === 'TL');
            return opt ? opt.value : null;
        })()
    """)
    if not tl_val:
        avail = await page.evaluate("""
            Array.from(document.querySelector('select[name="caseCd"]').options)
                .map(o => o.value + '=' + o.text.trim()).slice(0, 20)
        """)
        log.error(f"Tax Lien option not found. Available: {avail}")
        await page.close()
        return

    case_hrefs, results_url = await _lc_submit_and_collect_cases(
        page, tl_val, "Tax Lien", r"\d{2} TL \d+", from_str, to_str,
    )
    if not case_hrefs:
        log.info("Tax Lien: No cases found")
        await page.close()
        return

    for case_num, href in case_hrefs.items():
        if skip_cases and case_num in skip_cases:
            log.info(f"  SWEEP SKIP {case_num}: already in sheet")
            continue
        try:
            await page.goto(_abs_url(href))
            await page.wait_for_timeout(2500)
            case_text = await page.inner_text("body")

            fd_match = re.search(r"File\s*Date\s*[:\s]+(\d{1,2}/\d{1,2}/\d{4})", case_text, re.IGNORECASE)
            case_file_str = fd_match.group(1).strip() if fd_match else file_str

            pdf_url = await get_image_url(page)
            if not pdf_url:
                log.warning(f"  {case_num}: No complaint image")
                write_skipped_row(case_num, "Tax Lien", "", "No complaint image", case_file_str)
                continue

            pdf_bytes = await fetch_pdf_bytes(page, pdf_url)
            if not pdf_bytes:
                write_skipped_row(case_num, "Tax Lien", "", "PDF download failed", case_file_str)
                continue

            png    = pdf_bytes_to_png(pdf_bytes, 0)
            fields = extract_complaint_fields(client, png, "tax_lien")

            assessed_to = fields.get("assessed_to", "")
            prop_street = fields.get("property_street", "")
            prop_city   = fields.get("property_city", "")
            prop_zip    = fields.get("property_zip", "")

            # ArcGIS: by address if there's a street number, else by owner
            has_number = bool(re.match(r"\d+", prop_street.strip())) if prop_street else False
            if has_number:
                arc = arcgis_by_address(prop_street, prop_city, prop_zip)
            else:
                arc = arcgis_by_owner(assessed_to, prop_city) if assessed_to else None

            if not arc:
                log.warning(f"  {case_num}: No ArcGIS match — including anyway")
                # Tax lien defendants ARE property owners (the municipality is collecting
                # delinquent taxes from them). Include the lead with empty mailing fields.

            owner1 = arc.get("OWNER1", assessed_to) if arc else assessed_to
            # assessed_to is in natural order (from the document); owner1 is LAST FIRST (ArcGIS)
            first, last = split_owner_name(
                assessed_to if assessed_to else owner1,
                natural_order=bool(assessed_to and "," not in assessed_to),
            )

            row = build_tax_lien_row(
                pull_str, case_file_str, case_num,
                first, last, owner1,
                prop_street, prop_city, (str(arc.get("ZIP") or prop_zip or "") if arc else (prop_zip or "")),
                ((arc.get("OWN_ADDR") or "") if arc else ""),
                ((arc.get("OWN_CITY") or "") if arc else ""),
                ((arc.get("OWN_STATE") or "") if arc else ""),
                (str(arc.get("OWN_ZIP") or "") if arc else ""),
            )
            csv_append(csv_path, row)
            log.info(f"  {case_num}: {assessed_to} → {prop_street}, {prop_city} ✓")

        except Exception as e:
            log.error(f"  Tax lien error on {case_num}: {e}")

        try:
            await page.goto(results_url)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    await page.close()
    log.info("Tax Lien complete")

# ── Foreclosure Auction worker ────────────────────────────────────────────────
def load_seen_foreclosures() -> set[str]:
    """Load seen address keys from the Google Sheet 'Seen Foreclosures' tab.
    The sheet is the single source of truth — no local file is used so that
    Jordan can manage the list directly (delete rows from bad runs, etc.).
    Returns an empty set if the sheet is unreachable (Apps Script not deployed yet).
    """
    seen = _apps_script_get_seen()
    log.info(f"  Seen foreclosures: {len(seen)} entries loaded from sheet")
    return seen

def _apps_script_get_fc_marker() -> str:
    """Fetch the first-row marker text stored by the previous successful FC run.
    Returns empty string if the tab is empty or the sheet is unavailable."""
    if not APPS_SCRIPT_URL:
        return ""
    try:
        payload = {"action": "get_fc_marker", "secret": APPS_SCRIPT_SECRET}
        r = requests.post(APPS_SCRIPT_URL, json=payload, timeout=15)
        data = r.json()
        if data.get("status") == "ok":
            return data.get("marker", "")
    except Exception as e:
        log.warning(f"  Could not load FC marker from sheet: {e}")
    return ""

def _apps_script_set_fc_marker(marker_text: str):
    """Overwrite the 'Last Run Foreclosure' tab with today's first-row marker text."""
    if not APPS_SCRIPT_URL:
        return
    try:
        payload = {"action": "set_fc_marker", "marker": marker_text, "secret": APPS_SCRIPT_SECRET}
        requests.post(APPS_SCRIPT_URL, json=payload, timeout=15)
    except Exception as e:
        log.warning(f"  Could not save FC marker to sheet: {e}")

def _apps_script_get_seen() -> set[str]:
    """Fetch seen address_keys from the Google Sheet 'Seen Foreclosures' tab."""
    if not APPS_SCRIPT_URL:
        return set()
    try:
        payload = {"action": "get_seen_foreclosures", "secret": APPS_SCRIPT_SECRET}
        r = requests.post(APPS_SCRIPT_URL, json=payload, timeout=30)
        data = r.json()
        if data.get("status") == "ok":
            rows = data.get("rows", [])
            return {row["address_key"] for row in rows if row.get("address_key")}
    except Exception as e:
        log.warning(f"  Could not load seen foreclosures from sheet: {e}")
    return set()

def save_seen_foreclosures(new_entries: list[tuple[str, str, str]]):
    """Append new seen foreclosure entries to the Google Sheet 'Seen Foreclosures' tab.
    Sheet is the single source of truth — Jordan can delete rows from bad runs directly.
    """
    if not new_entries:
        return
    try:
        sheet_rows = [[key, pull_date, address] for key, pull_date, address in new_entries]
        ok = apps_script_post({"action": "append_seen_foreclosures", "rows": sheet_rows})
        if ok:
            log.info(f"  Saved {len(new_entries)} new seen foreclosure(s) to Seen Foreclosures tab")
        else:
            log.warning(f"  Could not save seen foreclosures to sheet (Apps Script may need redeployment)")
    except Exception as e:
        log.warning(f"  Could not save seen foreclosures to sheet: {e}")


# ── Sweep agent helpers (sheet is source of truth — no local files) ───────────

def get_all_sheet_case_numbers() -> set[str]:
    """Read every case number stored in the Google Sheet across all dated daily tabs.
    Returns a normalised set so the sweep can skip already-processed cases.

    Case numbers live in three columns:
      K (index 10) Lead Notes  — "26 SM 001234 - Servicemembers" / "26 TL 001234 - Tax Lien"
      R (index 17) Probate Case # — "WO26P1234EA"
      S (index 18) Tax Foreclosure — "26 TL 001234" (raw case num for tax lien rows)
    """
    if not APPS_SCRIPT_URL:
        return set()
    try:
        payload = {"action": "get_all_case_numbers", "secret": APPS_SCRIPT_SECRET}
        r = requests.post(APPS_SCRIPT_URL, json=payload, timeout=60)
        data = r.json()
        if data.get("status") != "ok":
            log.warning(f"  get_all_case_numbers failed: {data.get('message')}")
            return set()
        seen: set[str] = set()
        for item in data.get("case_numbers", []):
            raw = str(item or "").strip()
            if not raw:
                continue
            # "26 SM 001234 - Servicemembers" → "26 SM 001234"
            normalized = re.sub(r'\s*[-\u2013]\s*(?:Servicemembers|Tax Lien)\s*$', '', raw, flags=re.IGNORECASE)
            seen.add(normalized.strip())
        log.info(f"  Sweep: loaded {len(seen)} case numbers from sheet")
        return seen
    except Exception as e:
        log.warning(f"  Could not load case numbers from sheet: {e}")
        return set()


def get_no_images_rows_from_sheet() -> list[dict]:
    """Return all rows from the 'No Images' tab.
    Each row: {pull_date, file_date, case_num, county, notes}
    """
    if not APPS_SCRIPT_URL:
        return []
    try:
        payload = {"action": "get_no_images_rows", "secret": APPS_SCRIPT_SECRET}
        r = requests.post(APPS_SCRIPT_URL, json=payload, timeout=30)
        data = r.json()
        if data.get("status") == "ok":
            return data.get("rows", [])
    except Exception as e:
        log.warning(f"  Could not load No Images rows: {e}")
    return []


def prune_no_images_sheet(cutoff_str: str, resolved_cases: list[str]):
    """Delete rows from the 'No Images' tab:
    - All rows with pull_date before cutoff_str (older than 14 days)
    - Rows whose case_num is in resolved_cases (image now found)
    """
    if not APPS_SCRIPT_URL:
        return
    try:
        payload = {
            "action": "prune_no_images",
            "cutoff_date": cutoff_str,
            "resolved_cases": resolved_cases,
            "secret": APPS_SCRIPT_SECRET,
        }
        ok = apps_script_post(payload)
        if ok:
            log.info(f"  No Images tab pruned (cutoff={cutoff_str}, resolved={len(resolved_cases)})")
    except Exception as e:
        log.warning(f"  Could not prune No Images tab: {e}")


async def run_sweep_agent(
    context: BrowserContext,
    tab_name: str,
    csv_path: Path,
    client: anthropic.Anthropic,
):
    """Context 5: 2-week sweep for missing cases + No Images resolution check.

    Gated to May 1, 2026 and later. Before that date, this agent does nothing.

    Sweep logic:
    1. Pull all case numbers from the Google Sheet (source of truth, no local files).
    2. Search MassCourts for the past 14 calendar days for ALL case types and counties.
    3. Skip any case already in the sheet — only process genuinely missing ones.
    4. Write new rows to today's dated tab via the shared CSV path.

    No Images logic:
    1. Read the 'No Images' tab from the sheet.
    2. Delete rows older than 14 days.
    3. For rows within 14 days: navigate to the case on MassCourts and check for an Image link.
    4. If image now exists: run full extraction and append to today's tab. Mark case resolved.
    """
    today = date.today()

    log.info("Sweep agent: starting")
    from_date = today - timedelta(days=13)   # 14 calendar days including today
    to_date   = today - timedelta(days=1)    # stop at yesterday (today's run handles today)
    from_str  = fmt_date(from_date)
    to_str    = fmt_date(to_date)
    pull_str  = fmt_date(today)

    # ── Part 1: Get all case numbers already in the sheet ──────────────────────
    seen_cases = get_all_sheet_case_numbers()

    # ── Part 2: Sweep MassCourts for missing cases ─────────────────────────────
    log.info(f"Sweep: searching {from_str} → {to_str} (skip set: {len(seen_cases)} cases)")

    # All 11 counties for probate sweep
    all_counties = list(COUNTY_DIV.keys())
    try:
        await run_probate_counties(
            context, all_counties, from_date, to_date, csv_path, client,
            skip_cases=seen_cases,
        )
    except Exception as e:
        log.error(f"Sweep probate failed: {e}")

    try:
        await run_pre_foreclosure(
            context, from_date, to_date, csv_path, client,
            skip_cases=seen_cases,
        )
    except Exception as e:
        log.error(f"Sweep pre-foreclosure failed: {e}")

    try:
        await run_tax_lien(
            context, from_date, to_date, csv_path, client,
            skip_cases=seen_cases,
        )
    except Exception as e:
        log.error(f"Sweep tax lien failed: {e}")

    # ── Part 3: No Images check ────────────────────────────────────────────────
    log.info("Sweep: checking No Images tab for resolved cases")
    cutoff      = today - timedelta(days=14)
    cutoff_str  = fmt_date(cutoff)
    no_img_rows = get_no_images_rows_from_sheet()
    resolved    = []

    if no_img_rows:
        page = await context.new_page()
        try:
            for row_data in no_img_rows:
                pull_date_str = str(row_data.get("pull_date", "")).strip()
                case_num      = str(row_data.get("case_num", "")).strip()
                county        = str(row_data.get("county", "")).strip()
                file_date_str = str(row_data.get("file_date", "")).strip()

                if not case_num:
                    continue

                # Parse pull_date — skip if older than 14 days (will be pruned)
                try:
                    pd = datetime.strptime(pull_date_str, "%m/%d/%Y").date()
                    if pd < cutoff:
                        continue  # old row — the prune step will remove it
                except ValueError:
                    continue

                # Navigate to case and check for Image link
                try:
                    search_url = "https://www.masscourts.org/eservices/search.page.79"
                    await page.goto(search_url)
                    await page.wait_for_timeout(2000)

                    # Search by case number directly via Name tab
                    # (faster than re-doing full date search just to check one case)
                    search_by_case_url = (
                        "https://www.masscourts.org/eservices/"
                        f"?x=vxlRKzJcMJlCMjkDVz5hbOHkMjkDA*t2AJlAEi9M0kQ-"
                        f"&case_number={case_num}"
                    )
                    # Navigate directly to the case if we have a known case number pattern
                    # Use the probate-division-specific URL if we know the county
                    div_code = COUNTY_DIV.get(county, "")
                    if div_code:
                        ok = await navigate_to_case_type_tab(page, "PF_DEPT", div_code)
                        if ok:
                            # Switch to the Case Number tab and search
                            await page.evaluate(f"""
                                (() => {{
                                    const nameInput = document.querySelector('input[name="lastName"]');
                                    if (nameInput) nameInput.value = '{case_num}';
                                    const submit = document.querySelector('input[type="submit"][name="submitLink"]');
                                    if (submit) submit.click();
                                }})()
                            """)
                            await page.wait_for_timeout(3000)

                    # Find a direct link to this case number
                    case_link = await page.evaluate(f"""
                        (() => {{
                            const a = Array.from(document.querySelectorAll('a'))
                                .find(el => el.textContent.trim() === '{case_num}');
                            return a ? a.getAttribute('href') : null;
                        }})()
                    """)

                    if not case_link:
                        log.info(f"  No Images: {case_num} — case not found in search, leaving")
                        continue

                    await page.goto(_abs_url(case_link))
                    await page.wait_for_timeout(2500)

                    # Check if there's an Image link
                    has_image = await page.evaluate("""
                        Array.from(document.querySelectorAll('a'))
                            .some(a => a.textContent.trim() === 'Image')
                    """)

                    if has_image:
                        log.info(f"  No Images: {case_num} — image now available! Processing...")
                        case_href = case_link
                        file_str  = file_date_str
                        try:
                            await process_probate_case(
                                page, context, case_num, case_href,
                                pull_str, file_str, csv_path, client, county=county,
                            )
                            resolved.append(case_num)
                            log.info(f"  No Images: {case_num} resolved ✓")
                        except Exception as e:
                            log.error(f"  No Images: {case_num} processing failed: {e}")
                    else:
                        log.info(f"  No Images: {case_num} — still no image")

                except Exception as e:
                    log.error(f"  No Images: error checking {case_num}: {e}")
        finally:
            await page.close()

    # Prune the No Images tab: delete old rows + resolved cases
    prune_no_images_sheet(cutoff_str, resolved)
    log.info(f"Sweep agent complete — resolved {len(resolved)} No Images case(s)")


async def _fc_run_search(page: Page, from_str: str, to_str: str) -> bool:
    """
    Navigate to masspublicnotices.org and run the 'auction mortgage' search (AND mode).
    Returns True if the search results page loaded.
    Date range is yesterday → today to match the manual search process.
    """
    await page.goto("https://www.masspublicnotices.org/")
    await page.wait_for_timeout(6000)

    body_text = await page.inner_text("body")
    if "Click Here" in body_text or "I'm not a robot" in body_text.lower():
        log.info("masspublicnotices.org: Welcome/CAPTCHA page detected")
        clicked = await page.evaluate("""
            (() => {
                const link = Array.from(document.querySelectorAll('a'))
                    .find(a => a.textContent.trim() === 'Click Here');
                if (link) { link.click(); return true; }
                return false;
            })()
        """)
        if not clicked:
            # Try 2captcha auto-solve first
            log.info("masspublicnotices.org: attempting 2captcha auto-solve...")
            token = await solve_recaptcha_v2(page)
            if token:
                await page.evaluate(f"""
                    const ta = document.getElementById('g-recaptcha-response');
                    if (ta) {{ ta.style.display = 'block'; ta.value = {json.dumps(token)}; }}
                    const link = Array.from(document.querySelectorAll('a'))
                        .find(a => a.textContent.trim() === 'Click Here');
                    if (link) link.click();
                """)
                await page.wait_for_timeout(3000)
            else:
                # Fall back to manual
                log.warning("=" * 60)
                log.warning("ACTION REQUIRED: masspublicnotices.org CAPTCHA!")
                log.warning("Look for the Chromium window and solve the CAPTCHA, then click 'Click Here'.")
                log.warning("Waiting up to 5 minutes...")
                log.warning("=" * 60)
                send_notification_email(
                    subject="⚠️ Daily List — CAPTCHA needed for Foreclosure Auctions",
                    body=(
                        "The daily list script needs you to solve a CAPTCHA on masspublicnotices.org.\n\n"
                        "1. Open the Chromium browser window that just launched.\n"
                        "2. Solve the 'I'm not a robot' CAPTCHA.\n"
                        "3. Click 'Click Here' to proceed.\n\n"
                        "The script will continue automatically once the CAPTCHA is cleared.\n"
                        "You have 5 minutes before the foreclosure section times out."
                    ),
                )
                for _ in range(150):  # 150 × 2s = 5 minutes
                    await page.wait_for_timeout(2000)
                    body = await page.inner_text("body")
                    if "Click Here" not in body and "I'm not a robot" not in body.lower():
                        log.info("masspublicnotices.org: CAPTCHA cleared ✓")
                        break
        await page.wait_for_timeout(3000)

    form_present = await page.evaluate(
        "!!document.getElementById('ctl00_ContentPlaceHolder1_as1_txtSearch')"
    )
    if not form_present:
        log.warning(f"Foreclosure: search form not found — URL: {page.url}")
        return False

    await page.evaluate(f"""
        document.getElementById('ctl00_ContentPlaceHolder1_as1_txtSearch').value = 'auction mortgage';
        // Set search mode to AND (All Words must appear) — same as manual search
        const radio = document.querySelector('input[name="ctl00$ContentPlaceHolder1$as1$rdoType"][value="AND"]');
        if (radio) radio.checked = true;
        document.getElementById('ctl00_ContentPlaceHolder1_as1_txtDateFrom').value = '{from_str}';
        document.getElementById('ctl00_ContentPlaceHolder1_as1_txtDateTo').value = '{to_str}';
        document.getElementById('ctl00_ContentPlaceHolder1_as1_btnGo1').click();
    """)
    await page.wait_for_timeout(4000)

    # Set results per page to 50 to minimise the number of pages we need to iterate.
    # The dropdown triggers an ASP.NET postback so we wait for the updated grid.
    per_page_set = await page.evaluate("""
        (() => {
            const ddl = document.getElementById(
                'ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctl01_ddlPerPage');
            if (ddl && ddl.value !== '50') {
                ddl.value = '50';
                ddl.dispatchEvent(new Event('change'));
                return true;
            }
            return false;
        })()
    """)
    if per_page_set:
        await page.wait_for_timeout(3000)
        log.info("  Foreclosure: set results per-page to 50")

    return True


async def _fc_navigate_to_page(page: Page, from_str: str, to_str: str, target_page: int) -> bool:
    """Re-run search and paginate to a specific page number (1-based).
    Uses the top-pager btnNext image button (input[type='image']) to advance pages.
    """
    ok = await _fc_run_search(page, from_str, to_str)
    if not ok or target_page <= 1:
        return ok
    for _ in range(target_page - 1):
        clicked = await page.evaluate("""
            (() => {
                const btn = document.getElementById(
                    'ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctl01_btnNext');
                if (btn) { btn.click(); return true; }
                return false;
            })()
        """)
        if not clicked:
            log.warning("  Foreclosure: btnNext not found during page navigation")
            return False
        await page.wait_for_timeout(3000)
    return True


async def _fc_process_notice(page: Page, btn_id: str, pull_str: str, seen: set) -> Optional[dict]:
    """Click a VIEW button and extract notice data. Returns dict or None if skipped."""
    if not await page.evaluate(f"!!document.getElementById('{btn_id}')"):
        log.warning(f"  Foreclosure: btn {btn_id} not found")
        return None

    await page.evaluate(f"document.getElementById('{btn_id}').click()")
    await page.wait_for_timeout(4000)

    # -----------------------------------------------------------------------
    # The Details.aspx page has a reCAPTCHA (id="recaptcha", class="recaptcha",
    # NO data-callback) that gates the "View Notice" form submission.
    # The correct flow: solve CAPTCHA → set g-recaptcha-response.value → click
    # "View Notice" (which IS the form submit button that carries the token).
    # There is only ONE CAPTCHA step; "View Notice" IS the submission.
    # -----------------------------------------------------------------------
    body_check = await page.inner_text("body")
    if "I'm not a robot" in body_check or "CAPTCHA" in body_check.upper():
        log.info("  Foreclosure notice CAPTCHA detected — solving via 2captcha...")
        token = await solve_recaptcha_v2(page)
        if token:
            # Set token value in the hidden textarea so it's included in the POST
            await page.evaluate(f"document.getElementById('g-recaptcha-response').value = {json.dumps(token)}")
            log.info("  Foreclosure: CAPTCHA token set in g-recaptcha-response")
        else:
            log.warning("  Foreclosure notice CAPTCHA: 2captcha failed — emailing + waiting up to 3 min")
            send_notification_email(
                subject="⚠️ Daily List — CAPTCHA needed for Foreclosure Notice",
                body=(
                    "The daily list script needs you to solve a reCAPTCHA on masspublicnotices.org.\n\n"
                    "1. Find the Chromium browser window (check your taskbar/Dock).\n"
                    "2. Click the 'I'm not a robot' checkbox.\n"
                    "3. The script will continue automatically once cleared.\n\n"
                    "You only need to do this ONCE per daily run."
                ),
            )
            for _ in range(90):  # 90 × 2s = 3 minutes
                await page.wait_for_timeout(2000)
                body_check = await page.inner_text("body")
                if "I'm not a robot" not in body_check and "CAPTCHA" not in body_check.upper():
                    log.info("  Foreclosure notice CAPTCHA cleared manually ✓")
                    break
            else:
                # Manual solve didn't happen — try 2captcha one more time
                log.info("  Foreclosure notice CAPTCHA: retrying 2captcha after manual wait...")
                token = await solve_recaptcha_v2(page)
                if token:
                    await page.evaluate(f"document.getElementById('g-recaptcha-response').value = {json.dumps(token)}")
                    log.info("  Foreclosure: CAPTCHA token set (retry) in g-recaptcha-response")

    # Click "View Notice" — this submits the aspnetForm with g-recaptcha-response.
    # Important: querySelector('input[type="submit"]') returns Print (first in DOM),
    # so we must target View Notice specifically by its value.
    view_notice_clicked = await page.evaluate("""
        (() => {
            const btn = Array.from(document.querySelectorAll('input[type="submit"]'))
                .find(b => b.value === 'View Notice');
            if (btn) { btn.click(); return true; }
            return false;
        })()
    """)
    log.info(f"  Foreclosure: 'View Notice' {'clicked' if view_notice_clicked else 'not found'} | URL={page.url!r}")
    if not view_notice_clicked:
        return None

    # Wait for the page to reload with the notice content
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        await page.wait_for_timeout(6000)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(3000)

    # Use text_content (not inner_text) to get ALL text including hidden elements
    notice_text = await page.evaluate("document.body.innerText || document.body.textContent || ''")
    # If the text is suspiciously short, the notice body may not have loaded yet — wait more
    if len(notice_text) < 1200:
        log.debug(f"  Foreclosure: short page text ({len(notice_text)} chars) — waiting 5s more...")
        await page.wait_for_timeout(5000)
        notice_text = await page.evaluate("document.body.innerText || document.body.textContent || ''")
    # Strip OCR line-break hyphens
    notice_text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", notice_text)

    # The site truncates displayed text at 1,000 chars — download the linked PDF for full notice text.
    # Every notice detail page has a PDF link with the complete legal text (address, auction date, etc.)
    pdf_link = await page.evaluate("""
        (() => {
            const a = Array.from(document.querySelectorAll('a[href]'))
                .find(el => /\\.pdf/i.test(el.href));
            return a ? a.href : null;
        })()
    """)
    if pdf_link:
        try:
            log.debug(f"  Foreclosure: downloading notice PDF: {pdf_link}")
            pdf_resp = await page.context.request.get(pdf_link, timeout=30000)
            if pdf_resp.ok:
                pdf_bytes = await pdf_resp.body()
                pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                pdf_text = "\n".join(p.get_text() for p in pdf_doc)
                pdf_doc.close()
                if pdf_text.strip():
                    # Augment — the page text has metadata, PDF has the full legal notice
                    notice_text = notice_text + "\n\n" + pdf_text
                    log.info(f"  Foreclosure: augmented notice with PDF text ({len(pdf_text)} chars)")
        except Exception as _pdf_err:
            log.warning(f"  Foreclosure: PDF download/extract failed: {_pdf_err}")

    # Confirm this is a mortgagee real-estate sale notice (full text check).
    # This filter was previously applied to the search-result snippet (wrong place)
    # because the snippet is truncated and never contains these phrases.
    MORTGAGEE_PHRASES = [
        "MORTGAGEE'S NOTICE OF SALE",
        "MORTGAGEE'S SALE OF REAL ESTATE",
        "SALE OF REAL ESTATE",
        "NOTICE OF MORTGAGEE'S SALE",
        "PUBLIC AUCTION",          # broad fallback for notices with non-standard titles
    ]
    notice_upper = notice_text.upper()
    if not any(p in notice_upper for p in MORTGAGEE_PHRASES):
        log.info(f"  Foreclosure: not a mortgagee sale notice — skipping (text len={len(notice_text)})")
        log.info(f"  Foreclosure: page URL={page.url!r}")
        log.info(f"  Foreclosure: notice text[0:600]={notice_text[:600]!r}")
        log.info(f"  Foreclosure: notice text[600:1400]={notice_text[600:1400]!r}")
        return None

    # Parse owner name: "given by NAME to BANK"
    # Handles two formats:
    #   "given by Alexander G. Cestaro to The Cape Cod Five..."  (simple)
    #   "given by: NAME to BANK"                                  (colon variant)
    #   "given by Brian M. McKay, Bristol County, MA, to Bank"   (geo qualifier between name and "to")
    # Strategy: match everything between "given by" and ", to BANK" / " to BANK",
    # then strip any geographic qualifier after the first comma in the captured text.
    owner_match = re.search(r"given by:?\s+(.+?)[,\s]+to\s+[A-Z]", notice_text, re.IGNORECASE)
    if owner_match:
        raw_name   = owner_match.group(1).strip()
        owner_name = raw_name.split(",")[0].strip()   # drop ", Bristol County, ..." suffix
    else:
        owner_name = ""

    # Parse auction date — try multiple patterns
    auction_date = ""
    _MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    _DATE   = rf"({_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}(?:,?\s+at\s+\d+:\d+\s*[APM]+)?)"
    auction_patterns = [
        # "at public auction at LOCATION, on DATE, on the mortgaged"
        (r"at public auction at\s+(.+?),?\s+on\s+(.+?),?\s+on the mortgaged", lambda m: f"{m.group(2).strip()} at {m.group(1).strip()}"),
        # "at public auction on DATE at LOCATION, on the mortgaged"
        (r"at public auction on\s+(.+?)\s+at\s+(.+?)\s+on the mortgaged", lambda m: f"{m.group(1).strip()} at {m.group(2).strip()}"),
        # "sell at public auction on DATE"
        (rf"sell at public auction on\s+{_DATE}", lambda m: m.group(1).strip()),
        # "public auction to be held on DATE"
        (rf"public auction to be held on\s+{_DATE}", lambda m: m.group(1).strip()),
        # "auction on DATE" or "auction scheduled for DATE"
        (rf"auction (?:on|scheduled for)\s+{_DATE}", lambda m: m.group(1).strip()),
        # "will be sold [at/by public auction] on DATE"  (covers both "at" and "by")
        (rf"will be sold\s+(?:(?:at|by)\s+public\s+auction\s+)?on\s+{_DATE}", lambda m: m.group(1).strip()),
        # "to be sold at public auction on DATE"
        (rf"to be sold (?:at|by) public auction on\s+{_DATE}", lambda m: m.group(1).strip()),
        # "held at public auction on DATE"
        (rf"held (?:at|by) public auction on\s+{_DATE}", lambda m: m.group(1).strip()),
        # Generic: month-date anywhere within ~120 chars after "auction" (crosses sentence boundaries)
        (rf"auction[\s\S]{{0,120}}?(\b{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}})", lambda m: m.group(1).strip()),
    ]
    for pat, extractor in auction_patterns:
        m = re.search(pat, notice_text, re.IGNORECASE)
        if m:
            try:
                auction_date = extractor(m)
                break
            except Exception:
                continue
    if not auction_date:
        snippet = notice_text[:400].replace("\n", " ")
        log.warning(f"  Foreclosure: could not parse auction date from notice — snippet: {snippet!r}")

    # Parse property address — try multiple patterns
    # County suffix helper: optionally consume "Middlesex County," etc. between city and state.
    # All city-terminated patterns end with this instead of a bare (?:Massachusetts|MA).
    _ST = r"(?:,\s*[A-Za-z]+ County)?(?:,\s*)?(?:Massachusetts|MA)"

    prop_street, prop_city, prop_zip = "", "", ""
    for pattern in [
        # Address BEFORE the notice title (e.g. "10 Lookout Avenue, Natick, Massachusetts\nMORTGAGEE'S SALE OF REAL ESTATE")
        r"([0-9][^,\n]+?(?:,\s*(?:Unit|Apt|Suite|Ste|Floor|Fl|#)\s*[\w#.-]+)?),\s*([A-Za-z][A-Za-z ()]+?)" + _ST + r"\s+(?:MORTGAGEE'?S?\s+)?(?:NOTICE\s+OF\s+)?(?:MORTGAGEE'?S?\s+)?SALE\s+OF\s+REAL\s+ESTATE",
        # "NOTICE OF MORTGAGEE'S SALE OF REAL ESTATE   783 Washington Street, Unit 1, Boston, MA 02124"
        # Header format: address appears immediately after the title, possibly with a unit number
        r"SALE OF REAL ESTATE\s+([0-9][^,\n]+?(?:,\s*(?:Unit|Apt|Suite|Ste|Floor|Fl|#)\s*[\w#.-]+)?),\s*([A-Za-z][A-Za-z ()]+?)" + _ST,
        # "Premises: 81 High Street, West Springfield, MA"  (Boston Globe/Herald format)
        # Unit-aware: handles "Premises: 60 Tufts Street, Unit 6, Somerville, MA"
        r"[Pp]remises[:\s]+([0-9][^,\n]+?(?:,\s*(?:Unit|Apt|Suite|Ste|Floor|Fl|#)\s*[\w#.-]+)?),\s*([A-Za-z][A-Za-z ()]+?)" + _ST,
        # "being known as" / "premises known as"
        r"being known as\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"premises known as\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"at or upon the mortgaged premises,\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"located at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"property\s+(?:located\s+)?at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # "upon the premises located at / known as"
        r"upon the premises[,\s]+(?:located\s+at\s+|known\s+as\s+)?([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # "land and the buildings thereon situated at"
        r"(?:land|buildings)[^,\n]{0,60}situated\s+at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"(?:land\s+)?(?:and\s+premises\s+)?(?:situated|situate[d]?)\s+at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # Additional patterns for varied notice formats
        r"known\s+as\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"situate[d]?\s+(?:in|at)\s+[A-Za-z ]+,\s*(?:formerly\s+)?([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"(?:situate[d]?\s+at|situate[d]?\s+and\s+being\s+at)\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"mortgaged\s+premises\s+(?:described\s+as\s+)?(?:follows[:\s]+)?([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"(?:the\s+)?(?:said\s+)?(?:real\s+)?property\s+(?:is\s+)?(?:more\s+particularly\s+)?(?:described\s+as\s+)?(?:follows[:\s]+)?([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"(?:described|referred to)\s+as\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"address(?:ed)?\s+(?:as\s+)?([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        r"sold\s+at\s+public\s+auction\s+at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # "at [time] on [date], at 123 Main St, Springfield, MA" — auction date/time before address
        r"on\s+[A-Za-z]+,?\s+[A-Za-z]+ \d{1,2},\s*\d{4}[^.]{0,60}?at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # "at the time and place of sale at 123 Main St"
        r"place of sale\s+at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # "the property at 123 Main St" / "said property at 123 Main St"
        r"(?:the|said)\s+property\s+at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # "sale will be held at 123 Main St"
        r"sale\s+will\s+be\s+held\s+at\s+([0-9][^,\n]+?),\s*([A-Za-z ()]+?)" + _ST,
        # Catch-all: "NUMBER STREETNAME, CITY, MA ZIP" — requires zip to avoid false positives
        # House number must be 1-4 digits (not a year like 2026) and NOT followed by a preposition
        r"([1-9][0-9]{0,3})\s+(?!(?:at|in|on|of|the|a|an)\b)([A-Za-z][^,\n]{3,50}?),\s*([A-Za-z ()]{3,40}?),\s*(?:Massachusetts|MA)\s*0\d{4}",
    ]:
        pm = re.search(pattern, notice_text, re.IGNORECASE)
        if pm:
            # For the catch-all (3 groups), assemble differently
            if pm.lastindex == 3:
                candidate_street = f"{pm.group(1).strip()} {pm.group(2).strip()}"
                candidate_city   = pm.group(3).strip()
            else:
                candidate_street = pm.group(1).strip()
                candidate_city   = pm.group(2).strip()

            # Reject if street starts with a year (19xx / 20xx) — bad match
            if re.match(r'(?:19|20)\d{2}\b', candidate_street):
                log.debug(f"  Foreclosure: address rejected (year prefix): {candidate_street!r}")
                continue
            # Reject if a preposition immediately follows the house number
            if re.match(r'\d+\s+(?:at|in|on|of|the|a|an)\b', candidate_street, re.IGNORECASE):
                log.debug(f"  Foreclosure: address rejected (preposition after number): {candidate_street!r}")
                continue

            # Unit post-processing: if the "city" looks like a unit designation
            # (e.g. "Unit 1", "#36-16", "Apt 2B"), the pattern matched too early.
            # Expand the street to include the unit and search for the real city.
            if re.match(r'^(?:Unit|Apt|Suite|Ste|Floor|Fl|#)', candidate_city, re.IGNORECASE):
                city_after_unit = re.search(
                    re.escape(candidate_city) + r',\s*([A-Za-z][A-Za-z ()]+?),\s*(?:[A-Za-z]+ County,\s*)?(?:Massachusetts|MA)',
                    notice_text, re.IGNORECASE
                )
                if city_after_unit:
                    candidate_street = f"{candidate_street}, {candidate_city}"
                    candidate_city   = city_after_unit.group(1).strip()
                    log.debug(f"  Foreclosure: unit corrected → street={candidate_street!r} city={candidate_city!r}")
                else:
                    log.debug(f"  Foreclosure: city looks like unit ({candidate_city!r}), no real city found — skipping match")
                    continue

            # Parenthetical city post-processing: "Dorchester (Boston)" — city group stops at "("
            # but the parenthetical is part of the address.  Extend if the text immediately
            # after the captured city has "(Word)" before the next comma / state.
            paren_ext = re.search(
                re.escape(candidate_city) + r'\s*(\([A-Za-z ]+\))',
                notice_text, re.IGNORECASE
            )
            if paren_ext:
                candidate_city = f"{candidate_city} {paren_ext.group(1).strip()}"
                log.debug(f"  Foreclosure: parenthetical city → {candidate_city!r}")

            prop_street = candidate_street
            prop_city   = candidate_city
            # Try to grab zip from after "MA" (also allow county between city and MA)
            if not prop_zip:
                zip_m = re.search(
                    re.escape(prop_city) + r'(?:,\s*[A-Za-z]+ County)?,?\s*(?:Massachusetts\s*|MA\s*)(0\d{4})',
                    notice_text, re.IGNORECASE
                )
                if zip_m:
                    prop_zip = zip_m.group(1)
            break

    if not prop_street:
        # Log three windows so we can identify the address format in any notice length.
        meta_snippet = notice_text[:300].replace("\n", " ").strip()
        mid_snippet  = notice_text[1000:3000].replace("\n", " ").strip() if len(notice_text) > 1000 else ""
        pdf_snippet  = notice_text[3000:5000].replace("\n", " ").strip() if len(notice_text) > 3000 else notice_text.replace("\n", " ").strip()
        log.warning(f"  Foreclosure: could not parse property address from notice")
        log.warning(f"    [meta 0-300]:    {meta_snippet!r}")
        log.warning(f"    [mid 1000-3000]: {mid_snippet!r}")
        log.warning(f"    [pdf 3000-5000]: {pdf_snippet!r}")
        return None

    key = f"{prop_street.strip().lower()}|{prop_city.strip().lower()}|{prop_zip}"
    if key in seen:
        log.info(f"  SKIP (seen): {prop_street}, {prop_city}")
        return None
    seen.add(key)

    notice_date_match = re.search(
        r"(?:published|dated)[^\n]*?(\d{1,2}/\d{1,2}/\d{4})", notice_text, re.IGNORECASE
    )
    notice_date = notice_date_match.group(1) if notice_date_match else pull_str

    return {
        "owner_name": owner_name, "prop_street": prop_street,
        "prop_city": prop_city,   "prop_zip": prop_zip,
        "auction_date": auction_date, "notice_date": notice_date,
        "key": key,
    }


async def run_foreclosure_auctions(
    context: BrowserContext,
    from_date: date,
    to_date: date,
    csv_path: Path,
):
    log.info("Starting Foreclosure Auction run")
    seen = load_seen_foreclosures()
    page     = await context.new_page()
    pull_str = fmt_date(date.today())
    today    = date.today()

    # Use the same date range as probate/SM/TL so all list types cover the same window.
    # fc_to extends to today to catch notices posted on the run day itself (late uploads).
    fc_from  = from_date   # e.g. Friday on a Sunday run, or just yesterday on a weekday run
    fc_to    = today
    from_str = fmt_date(fc_from)
    to_str   = fmt_date(fc_to)
    log.info(f"Foreclosure date range: {from_str} → {to_str} (from_date→today)")

    MORTGAGEE_UPPER = [
        "MORTGAGEE'S NOTICE OF SALE",
        "MORTGAGEE'S SALE OF REAL ESTATE",
        "MORTGAGEE'S NOTICE OF SALE OF REAL ESTATE",
        "SALE OF REAL ESTATE",
    ]
    SKIP_PHRASES = [
        "PODS", "StorageTreasures", "storage unit", "self-storage",
        "Chapter 255 Section 17", "F/V ", "vessel", "boat",
        "motor vehicle", "automobile", "car auction",
        "UNITED STATES DISTRICT COURT",
    ]

    new_seen:      list[tuple[str, str, str]] = []
    total_processed = 0

    # First pass: collect ALL qualifying btn_ids across ALL pages with their page numbers.
    # We store (page_num, btn_id) so we can efficiently navigate back to the right page.
    ok = await _fc_run_search(page, from_str, to_str)
    if not ok:
        log.error("Foreclosure: Could not load search results — skipping")
        await page.close()
        return

    qualifying_by_page: list[tuple[int, list[str]]] = []
    current_page_num = 1
    today_marker     = ""   # first row text captured this run — saved to sheet on success

    # Load the marker row text saved by the previous successful run.
    # When we encounter this exact row in the search results we know we've
    # reached where we left off and stop paginating. User can clear the
    # "Last Run Foreclosure" tab in the sheet to force a full scan.
    fc_marker = _apps_script_get_fc_marker()
    if fc_marker:
        log.info(f"  FC marker loaded ({len(fc_marker)} chars) — will stop when found")
    else:
        log.info("  No FC marker found — full scan")

    while True:
        # Collect ALL btn_ids in a single JS round-trip (ctl03–ctl102).
        # On page 1 we also capture the very first row's text as today_marker so we
        # can save it at the end and let tomorrow's run know where to stop.
        # If a previous-run marker is set we compare each row against it and stop
        # adding notices once we hit the marker row.
        page_result = await page.evaluate(f"""
            (() => {{
                const skipPhrases = {json.dumps(SKIP_PHRASES)};
                const fcMarker    = {json.dumps(fc_marker)};   // "" → no early stop
                const isPage1     = {json.dumps(current_page_num == 1)};

                const all        = [];
                const qualifying = [];
                let   markerHit  = false;
                let   firstRowText = '';

                for (let i = 3; i < 103; i++) {{
                    const ctl   = 'ctl' + String(i).padStart(2, '0');
                    const btnId = 'ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_' + ctl + '_btnView';
                    const btn   = document.getElementById(btnId);
                    if (!btn) continue;
                    const row  = btn.closest('tr');
                    if (!row)  continue;

                    // The VIEW button lives in the publication-header <tr> (publication
                    // name + date only).  The notice text snippet is in the *next* <tr>
                    // (a detail row).  Combine both so the marker contains actual
                    // notice-specific text, not just the generic header.
                    const detailRow = row.nextElementSibling;
                    const rawText   = (row.innerText || '') + ' ' + (detailRow ? detailRow.innerText || '' : '');
                    const normText  = rawText.slice(0, 800).trim().replace(/\\s+/g, ' ');
                    if (!normText) continue;

                    // Capture first row on page 1 as today's marker
                    if (isPage1 && all.length === 0) firstRowText = normText;

                    // Check for marker hit — use the full saved marker for comparison
                    // (not just the first 200 chars) so that multiple notices sharing a
                    // common date/header prefix don't all trigger a false early stop.
                    // startsWith handles old shorter markers gracefully.
                    if (fcMarker && normText.startsWith(fcMarker)) {{
                        markerHit = true;
                        break;
                    }}

                    all.push(btnId);
                    if (!skipPhrases.some(p => normText.toLowerCase().includes(p.toLowerCase()))) {{
                        qualifying.push(btnId);
                    }}
                }}
                return {{total: all.length, qualifying, markerHit, firstRowText}};
            }})()
        """)
        page_qualifying  = page_result.get("qualifying",   [])    if page_result else []
        page_total       = page_result.get("total",        0)     if page_result else 0
        marker_hit       = page_result.get("markerHit",    False) if page_result else False
        first_row_text   = page_result.get("firstRowText", "")    if page_result else ""

        # Capture today's marker from the very first row on page 1
        if current_page_num == 1 and first_row_text:
            today_marker = first_row_text
            log.info(f"  FC today_marker captured: {today_marker[:80]!r}…")

        log.info(f"  Foreclosure page {current_page_num}: {page_total} total, "
                 f"{len(page_qualifying)} qualifying, markerHit={marker_hit}")
        if page_qualifying:
            qualifying_by_page.append((current_page_num, page_qualifying))

        # Marker hit → we've reached the row where the previous run started.
        # Everything from here on was already pulled. Stop paginating.
        if marker_hit:
            log.info("  Foreclosure: marker row found — reached previous run's starting point, stopping")
            break

        # Detect next page by parsing "Page X of Y Pages" — image buttons are always
        # present in the DOM even on the last page, so we compare X vs Y instead.
        page_info = await page.evaluate("""
            (() => {
                const m = document.body.innerText.match(/Page (\\d+) of (\\d+) Pages?/i);
                return m ? {cur: parseInt(m[1]), total: parseInt(m[2])} : null;
            })()
        """)
        if not page_info or page_info["cur"] >= page_info["total"]:
            log.info(f"  Foreclosure: last page ({page_info}), stopping pagination")
            break

        current_page_num += 1
        log.info(f"  Foreclosure: paginating to page {current_page_num} of {page_info['total']}")
        await page.evaluate("""
            const btn = document.getElementById(
                'ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctl01_btnNext');
            if (btn) btn.click();
        """)
        await page.wait_for_timeout(3000)

    # Second pass: process each qualifying notice, navigating back to the right page
    last_processed_page = None

    for page_num, btn_ids in qualifying_by_page:
        for i, btn_id in enumerate(btn_ids):
            # Navigate to the page containing this btn_id (skip if already there)
            if last_processed_page != page_num:
                await _fc_navigate_to_page(page, from_str, to_str, page_num)
                last_processed_page = page_num

            try:
                result = await _fc_process_notice(page, btn_id, pull_str, seen)
                if result:
                    prop_street  = result["prop_street"]
                    prop_city    = result["prop_city"]
                    prop_zip     = result["prop_zip"]
                    owner_name   = result["owner_name"]
                    auction_date = result["auction_date"]
                    notice_date  = result["notice_date"]
                    key          = result["key"]

                    arc      = arcgis_by_address(prop_street, prop_city, prop_zip)
                    own_name  = (arc.get("OWNER1") or "")    if arc else ""
                    own_addr  = (arc.get("OWN_ADDR") or "")  if arc else ""
                    own_city  = (arc.get("OWN_CITY") or "")  if arc else ""
                    own_state = (arc.get("OWN_STATE") or "") if arc else ""
                    own_zip   = str(arc.get("OWN_ZIP") or "") if arc else ""
                    if arc and not prop_zip:
                        prop_zip = str(arc.get("ZIP") or "")

                    # Split "First [Middle] Last" → first=First, last=Last (drop middle initial).
                    # Multi-person names ("John A. Smith and Jane Doe") → keep first person only.
                    _raw_name = owner_name
                    if re.search(r"\s+(?:and|&)\s+", _raw_name, re.IGNORECASE):
                        _raw_name = re.split(r"\s+(?:and|&)\s+", _raw_name, flags=re.IGNORECASE)[0].strip()
                    name_parts = _raw_name.split() if _raw_name else []
                    # Remove middle initials (single letter, optionally followed by a period)
                    core_parts = [p for p in name_parts if not re.match(r"^[A-Za-z]\.?$", p)]
                    if len(core_parts) >= 2:
                        owner_first = core_parts[0].title()
                        owner_last  = core_parts[-1].title()
                    elif len(core_parts) == 1:
                        owner_first = ""
                        owner_last  = core_parts[0].title()
                    else:
                        owner_first = name_parts[0].title() if name_parts else ""
                        owner_last  = name_parts[-1].title() if len(name_parts) > 1 else ""

                    row = build_foreclosure_row(
                        pull_str, notice_date,
                        owner_first, owner_last,
                        prop_street, prop_city, prop_zip,
                        auction_date,
                        own_name,
                        own_addr, own_city, own_state, own_zip,
                    )
                    csv_append(csv_path, row)
                    new_seen.append((key, today.isoformat(), f"{prop_street}, {prop_city}"))
                    total_processed += 1
                    log.info(f"  Foreclosure ✓: {owner_name} → {prop_street}, {prop_city} (auction: {auction_date})")

            except Exception as e:
                log.error(f"  Foreclosure notice error: {e}")

            # Navigate back to the current page so the next btn_id is reachable
            if i < len(btn_ids) - 1:
                await _fc_navigate_to_page(page, from_str, to_str, page_num)

    save_seen_foreclosures(new_seen)

    # Save today's first-row marker to the sheet so the next run knows where to stop.
    # If the user deletes the "Last Run Foreclosure" tab data, the next run does a full scan.
    if today_marker:
        _apps_script_set_fc_marker(today_marker)
        log.info(f"  FC marker saved for next run: {today_marker[:80]!r}…")

    await page.close()
    log.info(f"Foreclosure Auction complete — {total_processed} new entries processed")

# ── Main orchestrator ─────────────────────────────────────────────────────────

# ── Post-run QA pass ──────────────────────────────────────────────────────────

# Column indices (0-based) matching HEADERS
_QA_IDX = {
    "pull_date":   0,   # A
    "file_date":   1,   # B
    "first":       2,   # C  Lead First Name
    "last":        3,   # D  Lead Last Name
    "owner":       4,   # E  Lead Owner Name
    "street":      6,   # G  Lead Street
    "city":        7,   # H  Lead City
    "zip":         9,   # J  Lead Zip
    "probate_type":16,  # Q  Probate Type
    "probate_case":17,  # R  Probate Case #
    "tax_fc":      18,  # S  Tax Foreclosure
    "pre_fc":      19,  # T  Preforeclosure Case
    "auction_date":20,  # U  Auction Date
}

def _qa_row_type(row: list) -> str:
    """Identify what kind of row this is."""
    def g(i): return row[i].strip() if i < len(row) else ""
    if g(_QA_IDX["probate_case"]): return "probate"
    if g(_QA_IDX["pre_fc"]):       return "servicemembers"
    if g(_QA_IDX["tax_fc"]):       return "tax_lien"
    # Foreclosure rows have property address + auction date col but no case number
    return "foreclosure"

def _qa_issues(row: list, row_type: str) -> list[str]:
    """Return a list of human-readable issue descriptions for this row."""
    def g(i): return row[i].strip() if i < len(row) else ""
    issues = []
    first, last = g(_QA_IDX["first"]), g(_QA_IDX["last"])
    owner  = g(_QA_IDX["owner"])
    street = g(_QA_IDX["street"])
    city   = g(_QA_IDX["city"])

    if row_type == "probate":
        if not first and not last:       issues.append("missing decedent name (C+D)")
        elif not last:                   issues.append("missing decedent last name (D)")
        elif not first:                  issues.append("missing decedent first name (C)")
        if not street:                   issues.append("missing property address (G)")
        if not city:                     issues.append("missing city (H)")
        # Detect sequence-ID bleed: first name is all digits or "NNN Date:"
        if first and re.match(r"^\d", first): issues.append(f"C looks like a sequence ID: {first!r}")

    elif row_type == "servicemembers":
        if not first and not last:       issues.append("missing defendant name (C+D)")
        if first and re.match(r"^\d", first): issues.append(f"C looks like a sequence ID: {first!r}")
        if not street:                   issues.append("missing property address (G)")

    elif row_type == "tax_lien":
        if not owner:                    issues.append("missing owner (E)")
        if not street:                   issues.append("missing property address (G)")

    elif row_type == "foreclosure":
        if not first and not last:       issues.append("missing grantor name (C+D)")
        if not street:                   issues.append("missing property address (G)")
        if not g(_QA_IDX["auction_date"]): issues.append("missing auction date (U)")

    return issues


def run_qa_pass(
    all_rows: list[list],
    run_log: str,
    client: anthropic.Anthropic,
    tab_name: str,
    pull_str: str,
):
    """
    Review today's rows for quality issues, call Claude Sonnet for analysis,
    and return (issue_count, qa_email_body). Results go in the email only — no sheet tab.
    """
    log.info("=" * 60)
    log.info("QA pass starting...")

    # ── 1. Identify problem rows ──────────────────────────────────────────────
    problem_rows: list[dict] = []
    for sheet_row_num, row in enumerate(all_rows, start=2):  # sheet row 2 = first data row
        row_type = _qa_row_type(row)
        issues   = _qa_issues(row, row_type)
        if issues:
            def g(i): return row[i].strip() if i < len(row) else ""
            problem_rows.append({
                "sheet_row":  sheet_row_num,
                "row_type":   row_type,
                "issues":     issues,
                "name":       f"{g(_QA_IDX['first'])} {g(_QA_IDX['last'])}".strip() or g(_QA_IDX["owner"]),
                "address":    f"{g(_QA_IDX['street'])}, {g(_QA_IDX['city'])}".strip(", "),
                "case_or_id": g(_QA_IDX["probate_case"]) or g(_QA_IDX["pre_fc"]) or g(_QA_IDX["tax_fc"]) or "",
                "auction":    g(_QA_IDX["auction_date"]),
                "row_data":   row,
            })

    log.info(f"QA: {len(problem_rows)} rows with issues out of {len(all_rows)} total")

    if not problem_rows:
        log.info("QA: all rows look clean — nothing to review")
        return 0, ""

    # ── 2. Build a compact summary to send to Claude Sonnet ──────────────────
    MAX_LOG_CHARS = 8000   # keep token cost reasonable
    log_snippet   = run_log[-MAX_LOG_CHARS:] if len(run_log) > MAX_LOG_CHARS else run_log

    issues_text = "\n".join(
        f"  Row {p['sheet_row']} [{p['row_type']}] {p['name']} | {p['address']} | "
        f"case={p['case_or_id'] or 'n/a'} auction={p['auction'] or 'blank'} | "
        f"issues: {'; '.join(p['issues'])}"
        for p in problem_rows
    )

    prompt = f"""You are reviewing the output of the Joe Homebuyer daily list automation script.
Today's run date: {pull_str}
Total rows generated: {len(all_rows)}
Rows with quality issues: {len(problem_rows)}

PROBLEM ROWS:
{issues_text}

TAIL OF RUN LOG (last {MAX_LOG_CHARS} chars):
{log_snippet}

For each problem row, do the following:
1. Explain WHY the field is likely missing (e.g. MassCourts party-name format, notice phrasing, ArcGIS lookup failure, etc.)
2. If the issue is a known bug that was already patched in this session, note that it will be fixed on the next run.
3. If the issue requires manual attention or a new code fix, flag it clearly.

Format your response as a JSON array where each element is:
{{
  "sheet_row": <int>,
  "root_cause": "<brief explanation>",
  "fix_type": "next_run_auto" | "needs_manual" | "needs_code_fix",
  "suggested_action": "<what to do>"
}}
Return ONLY the JSON array, no markdown fences."""

    try:
        log.info("QA: sending problem rows to Claude Sonnet for analysis...")
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        qa_analysis_text = resp.content[0].text.strip()
        qa_analysis = json.loads(qa_analysis_text)
        log.info(f"QA: Claude Sonnet returned analysis for {len(qa_analysis)} rows")
    except Exception as e:
        log.warning(f"QA: Claude Sonnet analysis failed: {e}")
        qa_analysis = []

    # ── 3. Build a lookup from sheet_row → analysis ───────────────────────────
    analysis_by_row = {item["sheet_row"]: item for item in qa_analysis if isinstance(item, dict)}

    # ── 4. Categorize rows for email digest (no sheet tab written) ───────────
    needs_manual: list[dict] = []
    needs_code_fix: list[dict] = []

    for p in problem_rows:
        sr  = p["sheet_row"]
        ana = analysis_by_row.get(sr, {})
        fix_type = ana.get("fix_type", "unknown")
        if fix_type == "needs_manual":
            needs_manual.append({**p, **ana})
        elif fix_type == "needs_code_fix":
            needs_code_fix.append({**p, **ana})

    # ── 5. Build email digest ─────────────────────────────────────────────────
    auto_fix_count   = sum(1 for p in problem_rows
                           if analysis_by_row.get(p["sheet_row"], {}).get("fix_type") == "next_run_auto")
    manual_count     = len(needs_manual)
    code_fix_count   = len(needs_code_fix)

    email_lines = [
        f"QA pass complete for {pull_str}.",
        f"  {len(problem_rows)} rows had issues",
        f"  {auto_fix_count} will be fixed automatically on next run",
        f"  {manual_count} need manual review",
        f"  {code_fix_count} need a code fix",
        "",
        f"QA detail tab: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit",
        "",
    ]

    if needs_manual:
        email_lines.append("NEEDS MANUAL ATTENTION:")
        for p in needs_manual:
            email_lines.append(
                f"  Row {p['sheet_row']} [{p['row_type']}] {p['name']} | {p['address']}\n"
                f"    Issues: {'; '.join(p['issues'])}\n"
                f"    Action: {p.get('suggested_action', '')}"
            )
        email_lines.append("")

    if needs_code_fix:
        email_lines.append("NEEDS CODE FIX:")
        for p in needs_code_fix:
            email_lines.append(
                f"  Row {p['sheet_row']} [{p['row_type']}] {p['name']}\n"
                f"    Issues: {'; '.join(p['issues'])}\n"
                f"    Root cause: {p.get('root_cause', '')}"
            )

    return len(problem_rows), "\n".join(email_lines)


async def main():
    today    = date.today()
    pull_str = fmt_date(today)
    from_date, to_date = get_date_range(today)

    log.info("=" * 60)
    log.info(f"Daily List starting — pull date {pull_str}")
    log.info(f"File date range: {fmt_date(from_date)} → {fmt_date(to_date)}")
    log.info("=" * 60)

    OUTPUT_DIR.mkdir(exist_ok=True)
    date_tag  = today.strftime("%m%d%Y")
    n_contexts = len(COUNTY_GROUPS)
    csv_paths  = [OUTPUT_DIR / f"group{i+1}_{date_tag}.csv" for i in range(n_contexts)]
    tab_name   = pull_str

    # Delete today's CSV files from any prior run so we don't double-upload rows.
    # The CSVs use append mode, so stale files accumulate if not cleared here.
    for p in csv_paths:
        if p.exists():
            p.unlink()
            log.info(f"Deleted stale CSV from prior run: {p.name}")

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set. Exiting.")
        sys.exit(1)

    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    errors: list[str] = []

    # Persistent profile dirs — one per context. Cookies persist so CAPTCHA is
    # only required once per context (subsequent runs reuse the session cookie).
    profile_dirs = [
        Path.home() / ".masscourts_profiles" / f"ctx{i+1}" for i in range(n_contexts)
    ]
    for p in profile_dirs:
        p.mkdir(parents=True, exist_ok=True)

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--no-session-restore",
    ]

    async with async_playwright() as pw:
        create_sheet_tab(tab_name)
        clear_sheet_tab(tab_name)  # Wipe any previous run's rows so we don't duplicate

        async def run_group(idx: int):
            # Stagger launches: ctx1=0s, ctx2=15s, ctx3=30s, ctx4=45s
            await asyncio.sleep(idx * 15)
            counties = COUNTY_GROUPS[idx]
            csv_path = csv_paths[idx]
            log.info(f"[ctx{idx+1}] Launching — counties: {counties or ['(special workflows only)']}")

            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dirs[idx]),
                headless=False,
                args=launch_args,
                user_agent=ua,
            )
            await ctx.add_init_script(STEALTH_JS)

            try:
                try:
                    await run_probate_counties(ctx, counties, from_date, to_date, csv_path, client)
                except Exception as e:
                    msg = f"ctx{idx+1} ({counties}) probate failed: {e}"
                    log.error(msg); errors.append(msg)

                if idx == PRE_FC_CTX_IDX:
                    try:
                        await run_pre_foreclosure(ctx, from_date, to_date, csv_path, client)
                    except Exception as e:
                        errors.append(f"Pre-foreclosure failed: {e}")

                if idx == TAX_LIEN_CTX_IDX:
                    try:
                        await run_tax_lien(ctx, from_date, to_date, csv_path, client)
                    except Exception as e:
                        errors.append(f"Tax lien failed: {e}")

                if idx == FC_AUCTIONS_CTX_IDX:
                    try:
                        await run_foreclosure_auctions(ctx, from_date, to_date, csv_path)
                    except Exception as e:
                        errors.append(f"Foreclosure auctions failed: {e}")

                if idx == SWEEP_CTX_IDX:
                    try:
                        await run_sweep_agent(ctx, tab_name, csv_path, client)
                    except Exception as e:
                        errors.append(f"Sweep agent failed: {e}")
            finally:
                await ctx.close()

        await asyncio.gather(*[run_group(i) for i in range(n_contexts)])

    # Merge all CSVs and write to Google Sheet
    log.info("Writing results to Google Sheet...")
    all_rows: list[list] = []
    for csv_path in csv_paths:
        rows = csv_read_all(csv_path)
        all_rows.extend(rows)
    log.info(f"Total rows to upload: {len(all_rows)}")
    write_rows_to_sheet(tab_name, all_rows)

    # ── QA pass ───────────────────────────────────────────────────────────────
    run_log_so_far = _log_stream.getvalue()
    try:
        qa_issue_count, qa_email_body = run_qa_pass(
            all_rows, run_log_so_far, client, tab_name, pull_str
        )
    except Exception as e:
        log.error(f"QA pass failed: {e}")
        qa_issue_count, qa_email_body = 0, f"QA pass failed with error: {e}"

    # Send summary email
    qa_flag = f" | 🔍 {qa_issue_count} QA issue(s)" if qa_issue_count else ""
    status = ("✅ Completed" if not errors else f"⚠️ Completed with {len(errors)} error(s)") + qa_flag
    body_lines = [
        f"Daily list run complete for {pull_str}.",
        f"File date range: {fmt_date(from_date)} – {fmt_date(to_date)}",
        f"Total rows written: {len(all_rows)}",
        "",
    ]
    if errors:
        body_lines += ["Errors:", *[f"  • {e}" for e in errors]]
    if qa_email_body:
        body_lines += ["", "─" * 60, qa_email_body]
    body_lines.append(f"\nhttps://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")

    # Append any new Seth memory written today
    seth_memory_dir = Path.home() / "Documents/Claude/Projects/Python Daily List/seth-memory"
    if seth_memory_dir.exists():
        today_str = date.today().strftime("%Y-%m-%d")
        new_memory_files = [
            f for f in seth_memory_dir.glob("*.md")
            if f.name != "MEMORY.md" and date.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d") == today_str
        ]
        if new_memory_files:
            body_lines += ["", "─" * 60, "📝 SETH MEMORY UPDATES TODAY:"]
            for mf in sorted(new_memory_files):
                body_lines += [f"\n— {mf.name} —", mf.read_text().strip()]

    # Append run log for debugging (truncated to last 50k chars to stay within email limits)
    run_log = _log_stream.getvalue()
    if run_log:
        MAX_LOG = 50_000
        if len(run_log) > MAX_LOG:
            run_log = f"[...truncated, showing last {MAX_LOG} chars...]\n" + run_log[-MAX_LOG:]
        body_lines += [
            "",
            "─" * 60,
            "FULL RUN LOG:",
            "─" * 60,
            run_log,
        ]

    send_notification_email(
        subject=f"Joe Homebuyer Daily List — {status} ({pull_str})",
        body="\n".join(body_lines),
    )
    log.info("Done.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
