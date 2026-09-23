"""GDELT article search. No key, no auth.

Hard limit: one request every 5 seconds across the whole process.

sourcelang is deliberately left open. Filtering to English would lose the French
and Maltese coverage that matters most for this account list.
"""

import logging
import threading
import time
from urllib.parse import urlparse

from ._http import get_json
from ._result import OK, SourceResult

log = logging.getLogger(__name__)

API = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL = 5.0

# ANDed against the company name. A bare name query returns whatever the world
# published that day; these are the only kinds of story worth scoring on.
CONTEXT_TERMS = [
    "CFO",
    '"chief financial officer"',
    '"finance director"',
    "restructuring",
    "acquisition",
    "merger",
    "redundancies",
    "ERP",
    '"finance transformation"',
]

_throttle_lock = threading.Lock()
_last_request = 0.0


def _wait_turn():
    """Serialise every caller onto one request per 5 seconds.

    The sleep deliberately happens while holding the lock: concurrent workers
    queue behind it rather than all waking at once and firing together.
    """
    global _last_request
    with _throttle_lock:
        delay = MIN_INTERVAL - (time.monotonic() - _last_request)
        if delay > 0:
            log.debug("gdelt: throttling %.1fs", delay)
            time.sleep(delay)
        _last_request = time.monotonic()


def build_query(company, terms=None, themes=None):
    """'"Corinthia Hotels" (CFO OR restructuring OR ...)' plus any theme filters.

    The name is quoted because unquoted multi-word queries match each word
    separately and the results are worthless.
    """
    query = f'"{company}"'
    terms = CONTEXT_TERMS if terms is None else terms
    if terms:
        query += " (" + " OR ".join(terms) + ")"
    for theme in themes or []:
        query += f" theme:{theme}"
    return query


def search_news(company, timespan="12m", maxrecords=50, terms=None, themes=None):
    """One request per account. Returns a SourceResult wrapping the articles."""
    query = build_query(company, terms, themes)
    _wait_turn()
    data, status = get_json(
        API,
        params={
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": maxrecords,
            "timespan": timespan,
            "sort": "datedesc",
        },
        source="gdelt",
    )
    if status != OK or not data:
        return SourceResult(status=status, data=[], detail=query)

    articles = []
    for item in data.get("articles") or []:
        url = item.get("url") or ""
        articles.append(
            {
                "title": item.get("title"),
                "url": url,
                "domain": item.get("domain") or urlparse(url).netloc,
                "seendate": item.get("seendate"),
                "language": item.get("language"),
                "sourcecountry": item.get("sourcecountry"),
            }
        )
    return SourceResult(status=OK, data=articles, detail=query)
