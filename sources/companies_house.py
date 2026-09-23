"""Companies House: filed facts about UK-registered companies.

Queried for every account regardless of where the group is headquartered. An
international group's UK subsidiary is itself a registered company, and that
subsidiary's finance director is a real signal for a UK reseller — so a Maltese
or French parent is no reason to skip the search.
"""

import logging
import os
import threading

from dotenv import load_dotenv

import cache
from . import entity_resolution
from ._http import get_json
from ._result import NO_KEY, OK, UNRESOLVED, SourceResult

load_dotenv()
log = logging.getLogger(__name__)

BASE = "https://api.company-information.service.gov.uk"
SEARCH_LIMIT = 20

_resolved_lock = threading.Lock()


def _auth():
    key = os.environ.get("COMPANIES_HOUSE_KEY")
    if not key:
        return None
    return (key, "")


def search_company(name, limit=SEARCH_LIMIT):
    """Candidate registered companies for a name.

    Deliberately wide: searching "Hilton" should surface HILTON UK HOTELS
    LIMITED and its siblings, not just an exact-name match.
    """
    auth = _auth()
    if auth is None:
        log.warning("companies_house: COMPANIES_HOUSE_KEY not set — skipping")
        return SourceResult(status=NO_KEY, data=[], detail="COMPANIES_HOUSE_KEY not set")

    data, status = get_json(
        f"{BASE}/search/companies",
        params={"q": name, "items_per_page": limit},
        auth=auth,
        source="companies_house",
    )
    if status != OK or not data:
        return SourceResult(status=status, data=[])

    candidates = [
        {
            "company_number": item.get("company_number"),
            "title": item.get("title"),
            "company_status": item.get("company_status"),
            "company_type": item.get("company_type"),
            "date_of_creation": item.get("date_of_creation"),
            "address": item.get("address_snippet"),
        }
        for item in (data.get("items") or [])[:limit]
    ]
    return SourceResult(status=OK, data=candidates)


def resolve_entity(account, domain=None):
    """Settle which registered company an account is, or report that we cannot.

    Returns a SourceResult whose data is the resolution. Status is UNRESOLVED
    when no candidate is clearly best: the candidate list still comes back for a
    human to choose from, but no company number is handed downstream. Guessing
    would attach real filed dates to the wrong company and grade them as the
    most trustworthy evidence we have.
    """
    key = account.strip().lower()
    with _resolved_lock:
        stored = cache.get_resolution(key)
        if stored and stored.get("company_number"):
            return SourceResult(
                status=OK,
                data={
                    "company_number": stored["company_number"],
                    "matched_name": stored["matched_name"],
                    "confidence": stored["confidence"],
                    "confirmed_by": stored["confirmed_by"],
                    "candidates": [],
                },
                detail=f"stored {stored['resolved_at']}"
                + (f", confirmed by {stored['confirmed_by']}" if stored["confirmed_by"] else ""),
            )

        found = search_company(account)
        if found.status != OK:
            return SourceResult(status=found.status, data={}, detail=found.detail)

        resolution = entity_resolution.resolve(account, found.data, domain)
        confidence = resolution["confidence"]

        if confidence in (entity_resolution.HIGH, entity_resolution.MEDIUM):
            cache.put_resolution(
                key, resolution["company_number"], resolution["matched_name"], confidence
            )
            return SourceResult(status=OK, data=resolution, detail=f"{confidence} confidence")

        return SourceResult(
            status=UNRESOLVED,
            data=resolution,
            detail=f"{confidence} confidence — {len(resolution['candidates'])} candidates, none clearly best",
        )


def get_officers(company_number):
    """Every officer on file. Filtering for finance roles happens downstream."""
    auth = _auth()
    if auth is None:
        return SourceResult(status=NO_KEY, data=[], detail="COMPANIES_HOUSE_KEY not set")
    if not company_number:
        return SourceResult(status=OK, data=[])

    data, status = get_json(
        f"{BASE}/company/{company_number}/officers",
        params={"items_per_page": 100},
        auth=auth,
        source="companies_house",
    )
    if status != OK or not data:
        return SourceResult(status=status, data=[])

    officers = [
        {
            "name": item.get("name"),
            "role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on"),
            "resigned_on": item.get("resigned_on"),
            "occupation": (item.get("occupation") or "").strip() or None,
            "nationality": item.get("nationality"),
        }
        for item in (data.get("items") or [])
    ]
    return SourceResult(status=OK, data=officers)


def get_filing_history(company_number):
    auth = _auth()
    if auth is None:
        return SourceResult(status=NO_KEY, data=[], detail="COMPANIES_HOUSE_KEY not set")
    if not company_number:
        return SourceResult(status=OK, data=[])

    data, status = get_json(
        f"{BASE}/company/{company_number}/filing-history",
        params={"items_per_page": 100},
        auth=auth,
        source="companies_house",
    )
    if status != OK or not data:
        return SourceResult(status=status, data=[])

    filings = [
        {
            "type": item.get("type"),
            "date": item.get("date"),
            "category": item.get("category"),
            "description": item.get("description"),
        }
        for item in (data.get("items") or [])
    ]
    return SourceResult(status=OK, data=filings)
