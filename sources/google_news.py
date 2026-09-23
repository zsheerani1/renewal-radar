"""Google News RSS. No key, no auth.

Locale-aware on purpose: a French account searched through a UK English locale
returns almost nothing, and the French and Maltese accounts are the ones this
tool keeps failing on.

Returns the same record shape GDELT returned, so the bundle schema is unchanged.
"""

import logging
import threading
import time
from urllib.parse import urlparse

import feedparser

from . import _urls
from ._http import get_bytes
from ._normalise import NONE as NO_MATCH
from ._normalise import brand_token, classify_match
from ._result import NO_COVERAGE, OK, SourceResult

log = logging.getLogger(__name__)

ENDPOINT = "https://news.google.com/rss/search"
MIN_INTERVAL = 1.0
DEFAULT_COUNTRY = "gb"

# Below this, a run that returned articles is suspect rather than empty.
LOW_MATCH_RATE = 0.20
RESOLVE_LIMIT = 12

# country -> (hl, gl, ceid)
LOCALES = {
    "gb": ("en-GB", "GB", "GB:en"),
    "us": ("en-US", "US", "US:en"),
    "ie": ("en-IE", "IE", "IE:en"),
    "au": ("en-AU", "AU", "AU:en"),
    "nz": ("en-NZ", "NZ", "NZ:en"),
    "ca": ("en-CA", "CA", "CA:en"),
    "za": ("en-ZA", "ZA", "ZA:en"),
    "in": ("en-IN", "IN", "IN:en"),
    "sg": ("en-SG", "SG", "SG:en"),
    "mt": ("en-GB", "MT", "MT:en"),
    "ae": ("en-AE", "AE", "AE:en"),
    "fr": ("fr", "FR", "FR:fr"),
    "be": ("fr", "BE", "BE:fr"),
    "ch": ("de", "CH", "CH:de"),
    "de": ("de", "DE", "DE:de"),
    "at": ("de", "AT", "AT:de"),
    "es": ("es", "ES", "ES:es"),
    "mx": ("es-419", "MX", "MX:es-419"),
    "it": ("it", "IT", "IT:it"),
    "nl": ("nl", "NL", "NL:nl"),
    "pt": ("pt-PT", "PT", "PT:pt-150"),
    "br": ("pt-BR", "BR", "BR:pt-419"),
    "pl": ("pl", "PL", "PL:pl"),
    "jp": ("ja", "JP", "JP:ja"),
}

CONTEXT_TERMS = {
    "en": [
        "CFO", '"chief financial officer"', '"finance director"',
        "restructuring", "acquisition", "merger", "redundancies",
        '"finance transformation"',
    ],
    "fr": [
        "CFO", '"directeur financier"', '"directrice financière"', "DAF",
        "restructuration", "acquisition", "fusion", "licenciements",
        '"transformation financière"',
    ],
    "de": [
        "CFO", "Finanzvorstand", '"kaufmännischer Leiter"',
        "Umstrukturierung", "Übernahme", "Fusion", "Stellenabbau",
        '"Finanztransformation"',
    ],
    "es": [
        "CFO", '"director financiero"', '"directora financiera"',
        "reestructuración", "adquisición", "fusión", "despidos",
        '"transformación financiera"',
    ],
    "it": [
        "CFO", '"direttore finanziario"', '"direttrice finanziaria"',
        "ristrutturazione", "acquisizione", "fusione", "licenziamenti",
        '"trasformazione finanziaria"',
    ],
}

_throttle_lock = threading.Lock()
_last_request = 0.0


def _wait_turn():
    """Google publishes no rate limit for this feed. Be a good citizen anyway."""
    global _last_request
    with _throttle_lock:
        delay = MIN_INTERVAL - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()


def locale_for(country):
    code = (country or DEFAULT_COUNTRY).lower()
    if code in LOCALES:
        return LOCALES[code]
    if len(code) == 2:
        # Google News covers far more countries than are listed above; an
        # English locale for the right country beats the wrong country.
        return ("en", code.upper(), f"{code.upper()}:en")
    return None


def terms_for(language):
    return CONTEXT_TERMS.get((language or "en").split("-")[0], CONTEXT_TERMS["en"])


def build_query(company, terms):
    return f'"{company}" (' + " OR ".join(terms) + ")"


def search_news(company, country=None, when="1y", terms=None, domain=None,
                resolve_limit=RESOLVE_LIMIT):
    """One request per account, in that account's own locale and language.

    Every article is kept. Each is tagged name_match exact/fuzzy/none and the
    counts are reported, so a normalisation fault shows up as a collapsed match
    rate rather than as an account that quietly appears to have no news.
    """
    locale = locale_for(country)
    if locale is None:
        return SourceResult(status=NO_COVERAGE, data=[], detail=f"no locale for '{country}'")

    hl, gl, ceid = locale
    query = build_query(company, terms if terms is not None else terms_for(hl))
    if when:
        query += f" when:{when}"

    _wait_turn()
    content, status = get_bytes(
        ENDPOINT,
        params={"q": query, "hl": hl, "gl": gl, "ceid": ceid},
        source="google_news",
    )
    if status != OK or not content:
        return SourceResult(status=status, data=[], detail=query)

    token = brand_token(domain)
    feed = feedparser.parse(content)
    articles = []
    for entry in feed.entries:
        link = entry.get("link") or ""
        source_name = (entry.get("source") or {}).get("title")
        title = entry.get("title")
        articles.append(
            {
                "title": title,
                "url": link,
                "domain": urlparse((entry.get("source") or {}).get("href") or link).netloc,
                "seendate": entry.get("published"),
                "source": source_name,
                "language": hl,
                "sourcecountry": gl,
                "name_match": classify_match(f"{title or ''} {source_name or ''}", company, token),
            }
        )

    # Only articles that actually name the account are worth two round trips.
    _urls.annotate(
        articles,
        limit=resolve_limit,
        should_resolve=lambda a: a["name_match"] != NO_MATCH,
    )

    retrieved = len(articles)
    matched = sum(1 for a in articles if a["name_match"] != NO_MATCH)
    resolved = sum(1 for a in articles if a.get("url_status") == _urls.RESOLVED)
    rate = (matched / retrieved) if retrieved else None
    needs_review = bool(retrieved and rate < LOW_MATCH_RATE)

    if needs_review:
        log.warning(
            "google_news: %s — only %d of %d articles name the account (%.0f%%). "
            "Flagging for review; this is not evidence of no news.",
            company, matched, retrieved, rate * 100,
        )
    else:
        log.info("google_news: %s — %d/%d articles matched", company, matched, retrieved)

    return SourceResult(
        status=OK,
        data=articles,
        detail=f"{query}  [hl={hl} gl={gl} ceid={ceid}]",
        meta={
            "articles_retrieved": retrieved,
            "articles_matched": matched,
            "urls_resolved": resolved,
            "match_rate": round(rate, 3) if rate is not None else None,
            "needs_review": needs_review,
        },
    )
