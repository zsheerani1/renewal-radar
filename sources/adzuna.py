"""Adzuna job ads.

Country is per account, not hardcoded: an account's ads live in its own
country's index, and Adzuna has no index at all for some countries.

Free tier is a few hundred calls a month, so both public functions read from a
single cached fetch per account.
"""

import logging
import os
import re
import threading
from collections import Counter
from datetime import date, timedelta

from dotenv import load_dotenv

from ._http import get_json
from ._result import NO_COVERAGE, NO_KEY, OK, SourceResult

load_dotenv()
log = logging.getLogger(__name__)

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
RESULTS_PER_PAGE = 50
MAX_DAYS_OLD = 365
DEFAULT_COUNTRY = "gb"

# Adzuna publishes one index per country. Anything outside this set has no
# coverage at all, which is a different answer from "no ads found".
SUPPORTED_COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
    "in", "it", "mx", "nl", "nz", "pl", "sg", "us", "za",
}

TLD_TO_COUNTRY = {
    "uk": "gb", "gb": "gb", "fr": "fr", "de": "de", "es": "es", "it": "it",
    "nl": "nl", "be": "be", "at": "at", "ch": "ch", "pl": "pl", "br": "br",
    "ca": "ca", "au": "au", "nz": "nz", "in": "in", "sg": "sg", "za": "za",
    "mx": "mx", "us": "us",
    # Present so they resolve to a real country and get NO_COVERAGE rather
    # than silently falling back to the UK index.
    "mt": "mt", "ae": "ae", "sa": "sa", "jp": "jp", "cn": "cn", "hk": "hk",
    "th": "th", "id": "id", "my": "my", "pt": "pt", "ie": "ie", "se": "se",
    "no": "no", "dk": "dk", "fi": "fi", "gr": "gr", "tr": "tr", "ru": "ru",
}

COUNTRY_NAMES = {
    "united kingdom": "gb", "uk": "gb", "great britain": "gb", "england": "gb",
    "scotland": "gb", "wales": "gb", "northern ireland": "gb",
    "france": "fr", "germany": "de", "spain": "es", "italy": "it",
    "netherlands": "nl", "belgium": "be", "austria": "at", "switzerland": "ch",
    "poland": "pl", "brazil": "br", "canada": "ca", "australia": "au",
    "new zealand": "nz", "india": "in", "singapore": "sg",
    "south africa": "za", "mexico": "mx",
    "united states": "us", "usa": "us", "us": "us",
    "malta": "mt", "ireland": "ie", "portugal": "pt", "uae": "ae",
}

SYSTEM_PATTERNS = [
    ("Infor SunSystems", r"\bsun\s?systems\b"),
    ("Infor", r"\binfor\b"),
    ("Oracle", r"\boracle\b"),
    ("SAP", r"\bsap\b"),
    ("NetSuite", r"\bnetsuite\b"),
    ("Workday", r"\bworkday\b"),
    ("Microsoft Dynamics", r"\b(?:microsoft\s+)?dynamics(?:\s+365)?\b"),
    ("Sage", r"\bsage\b"),
    ("Unit4 Agresso", r"\b(?:agresso|unit\s?4)\b"),
    ("Epicor", r"\bepicor\b"),
    ("Coda Financials", r"\bcoda\s+financials\b"),
    ("Xero", r"\bxero\b"),
    ("QuickBooks", r"\bquickbooks\b"),
]

FINANCE_TITLE_PATTERN = re.compile(
    r"\b(financ\w*|account\w*|controller|treasur\w*|fp&a|audit\w*|"
    r"bookkeep\w*|payroll|ledger|cfo)\b",
    re.I,
)

_fetch_lock = threading.Lock()
_ads_cache: dict[tuple, SourceResult] = {}


def country_for(country=None, domain=None):
    """Country column wins, then the email domain's TLD, then the UK."""
    if country:
        code = COUNTRY_NAMES.get(country.strip().lower())
        if code:
            return code
        if len(country.strip()) == 2:
            return country.strip().lower()
    if domain:
        tld = domain.split("@")[-1].strip().lower().rstrip(".").split(".")[-1]
        if tld in TLD_TO_COUNTRY:
            return TLD_TO_COUNTRY[tld]
    return DEFAULT_COUNTRY


def _credentials():
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return None
    return app_id, app_key


def detect_systems(text):
    """Finance systems named in a block of text. Pure string matching, no inference."""
    found = [label for label, pattern in SYSTEM_PATTERNS if re.search(pattern, text or "", re.I)]
    if "Infor SunSystems" in found and "Infor" in found:
        found.remove("Infor")
    return found


def _fetch_ads(company, country):
    """One HTTP call per company per run, always over the full window.

    Callers narrow the date range themselves rather than passing one in: a
    per-caller window would key the cache differently and spend a second call.
    The lock is held across the request, not just the cache lookup, so two
    workers asking for the same company cannot both spend quota on it.
    """
    key = (company.strip().lower(), country)
    with _fetch_lock:
        if key in _ads_cache:
            return _ads_cache[key]

        if country not in SUPPORTED_COUNTRIES:
            log.info("adzuna: no index for country '%s' — skipping %s", country, company)
            result = SourceResult(status=NO_COVERAGE, data=[], detail=f"no Adzuna index for '{country}'")
            _ads_cache[key] = result
            return result

        credentials = _credentials()
        if credentials is None:
            log.warning("adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping")
            result = SourceResult(status=NO_KEY, data=[], detail="ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
            _ads_cache[key] = result
            return result

        app_id, app_key = credentials
        data, status = get_json(
            API.format(country=country),
            params={
                "app_id": app_id,
                "app_key": app_key,
                "what_phrase": company,
                "results_per_page": RESULTS_PER_PAGE,
                "max_days_old": MAX_DAYS_OLD,
                "content-type": "application/json",
            },
            source="adzuna",
        )

        ads = [
            {
                "title": item.get("title"),
                "url": item.get("redirect_url"),
                "created": item.get("created"),
                "company": (item.get("company") or {}).get("display_name"),
                "location": (item.get("location") or {}).get("display_name"),
                "description": item.get("description"),
            }
            for item in ((data or {}).get("results") or [])
        ]
        result = SourceResult(status=status, data=ads, detail=f"country={country}")
        _ads_cache[key] = result
        return result


def find_system_mentions(company, country=None, domain=None):
    """Ads whose title or description names a finance system."""
    code = country_for(country, domain)
    result = _fetch_ads(company, code)
    matches = []
    for ad in result.data:
        systems = detect_systems(f"{ad.get('title') or ''} {ad.get('description') or ''}")
        if systems:
            matches.append({**ad, "systems_mentioned": systems})
    return SourceResult(status=result.status, data=matches, detail=result.detail)


def count_finance_roles(company, months=12, country=None, domain=None):
    """Finance-titled postings bucketed by month, for a hiring-volume signal."""
    code = country_for(country, domain)
    result = _fetch_ads(company, code)
    cutoff = (date.today() - timedelta(days=months * 31)).isoformat()[:7]
    counts = Counter()
    for ad in result.data:
        if not FINANCE_TITLE_PATTERN.search(ad.get("title") or ""):
            continue
        month = (ad.get("created") or "")[:7]
        if month and month >= cutoff:
            counts[month] += 1
    return SourceResult(status=result.status, data=dict(sorted(counts.items())), detail=result.detail)
