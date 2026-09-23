"""Assemble everything the deterministic sources found, before any AI sees it.

This is the evidence trail. The judge reads only this, and a user checking a
verdict reads this. So it carries not just what was found but how well each
source answered: a source that was rate-limited, keyless or unresolved is
recorded as such, because "we could not look" and "there is nothing there" must
never collapse into the same empty list.
"""

import logging
from datetime import date, datetime

from sources import adzuna, companies_house, google_news, grounded_search
from sources._normalise import NONE as NO_MATCH
from sources._result import OK, UNRESOLVED

log = logging.getLogger(__name__)

RECENT_MONTHS = 24
MAX_NEWS = 40
MAX_OFFICERS = 25
MAX_FILINGS = 25

FINANCE_ROLE_WORDS = (
    "financ", "cfo", "treasur", "account", "controller", "audit",
)

# Filing categories that indicate something changed. Confirmation statements and
# routine address filings are noise for this purpose.
NOTABLE_CATEGORIES = {
    "insolvency", "mortgage", "capital", "officers", "resolution",
    "persons-with-significant-control", "change-of-name", "reregistration",
    "liquidation", "gazette", "accounts",
}


def _months_ago(value, today=None):
    """Whole months between an ISO date string and today. None if unparseable."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            when = datetime.strptime(value[:10] if fmt == "%Y-%m-%d" else value[:7], fmt).date()
        except ValueError:
            continue
        today = today or date.today()
        return (today.year - when.year) * 12 + (today.month - when.month)
    return None


def _is_finance_role(officer):
    text = f"{officer.get('role') or ''} {officer.get('occupation') or ''}".lower()
    return any(word in text for word in FINANCE_ROLE_WORDS)


def _source_entry(result, **extra):
    return {
        "status": result.status,
        "detail": result.detail,
        "meta": result.meta,
        **extra,
    }


def _companies_house_section(account, domain, sources):
    resolution = companies_house.resolve_entity(account, domain)
    sources["companies_house"] = _source_entry(resolution)

    data = resolution.data or {}
    section = {
        "company_number": data.get("company_number"),
        "matched_name": data.get("matched_name"),
        "resolution_confidence": data.get("confidence"),
        "confirmed_by": data.get("confirmed_by"),
        # Hilton is correctly unresolved. The UI must ask a human to confirm the
        # company number rather than show an empty Companies House section.
        "needs_confirmation": resolution.status == UNRESOLVED,
        "candidates": data.get("candidates", []) if resolution.status == UNRESOLVED else [],
        "officers": [],
        "finance_officers": [],
        "notable_filings": [],
    }

    number = data.get("company_number")
    if resolution.status != OK or not number:
        return section

    # Companies House evidence is a filing, not a web page, so give the judge a
    # real citable URL. These are first-party and stable, not redirect tokens.
    public = f"https://find-and-update.company-information.service.gov.uk/company/{number}"
    section["source_url"] = public
    section["officers_url"] = f"{public}/officers"
    section["filings_url"] = f"{public}/filing-history"
    section["url_status"] = "resolved"

    officers = companies_house.get_officers(number)
    sources["companies_house_officers"] = _source_entry(officers)
    if officers.status == OK:
        enriched = []
        for officer in officers.data:
            appointed = _months_ago(officer.get("appointed_on"))
            resigned = _months_ago(officer.get("resigned_on"))
            enriched.append({
                **officer,
                "appointed_months_ago": appointed,
                "resigned_months_ago": resigned,
                "recent_change": bool(
                    (appointed is not None and appointed <= RECENT_MONTHS)
                    or (resigned is not None and resigned <= RECENT_MONTHS)
                ),
            })
        # Companies House does not label a director "CFO", so a finance-only
        # filter would usually be empty. Recent appointments and resignations are
        # the leadership signal regardless of the role string.
        section["officers"] = enriched[:MAX_OFFICERS]
        section["finance_officers"] = [o for o in enriched if _is_finance_role(o)][:MAX_OFFICERS]

    filings = companies_house.get_filing_history(number)
    sources["companies_house_filings"] = _source_entry(filings)
    if filings.status == OK:
        notable = [
            {**f, "months_ago": _months_ago(f.get("date"))}
            for f in filings.data
            if f.get("category") in NOTABLE_CATEGORIES
        ]
        section["notable_filings"] = notable[:MAX_FILINGS]

    return section


def build(account, domain=None, country=None, use_grounded=True, today=None):
    """Assemble the evidence bundle for one account. No judgements are made here."""
    today = today or date.today()
    code = adzuna.country_for(country, domain)
    sources = {}

    companies_house_section = _companies_house_section(account, domain, sources)

    news = google_news.search_news(account, country=code, domain=domain)
    sources["google_news"] = _source_entry(news)

    job_ads = adzuna.find_system_mentions(account, country=country, domain=domain)
    sources["adzuna"] = _source_entry(job_ads)

    role_counts = adzuna.count_finance_roles(account, country=country, domain=domain)
    sources["adzuna_finance_roles"] = _source_entry(role_counts)

    if use_grounded:
        claims = grounded_search.find_claims(account, domain=domain)
        sources["grounded_search"] = _source_entry(claims)
        grounded_claims = claims.data
    else:
        sources["grounded_search"] = {"status": "skipped", "detail": "grounded search disabled", "meta": {}}
        grounded_claims = []

    articles = [
        {**a, "months_ago": _months_ago(_rss_date(a.get("seendate")), today)}
        for a in news.data[:MAX_NEWS]
    ]

    return {
        "account": account,
        "domain": domain,
        "country": code,
        "assembled_at": today.isoformat(),
        "sources": sources,
        "companies_house": companies_house_section,
        "news": articles,
        "job_ads": job_ads.data,
        "finance_role_counts": role_counts.data,
        "grounded_claims": grounded_claims,
        "counters": {
            "articles_retrieved": news.meta.get("articles_retrieved", 0),
            "articles_matched": news.meta.get("articles_matched", 0),
            "articles_unmatched": sum(1 for a in articles if a.get("name_match") == NO_MATCH),
            "urls_resolved": news.meta.get("urls_resolved", 0),
            "match_rate": news.meta.get("match_rate"),
            "needs_review": news.meta.get("needs_review", False),
            "job_ads": len(job_ads.data),
            "grounded_claims": len(grounded_claims),
        },
    }


def _rss_date(value):
    """'Mon, 19 Jan 2026 08:00:00 GMT' -> '2026-01-19'."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:16].strip(), "%a, %d %b %Y").date().isoformat()
    except ValueError:
        return value[:10]
