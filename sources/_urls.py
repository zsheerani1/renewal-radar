"""Resolve redirector URLs to publisher URLs, at ingest.

Google News and Vertex grounding both hand back opaque redirect tokens. Those
tokens expire, cannot be read by a human, and cannot be clicked in front of a
prospect — so an evidence trail built on them is not an evidence trail.

Resolution is best-effort by design. When it fails the item survives with
url_status=redirect_unresolved, which downstream may still use to support a
likely/suspected verdict but never a confirmed one.
"""

import json
import logging
import re
import threading
import time

import requests

import cache

log = logging.getLogger(__name__)

RESOLVED = "resolved"
REDIRECT_UNRESOLVED = "redirect_unresolved"
DEAD = "dead"

TIMEOUT = 20
MIN_INTERVAL = 0.4
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# Clears Google's consent interstitial, which otherwise swallows every redirect.
SOCS_COOKIE = "CAESHAgBEhJnd3NfMjAyNDA4MjctMF9SQzIaAmVuIAEaBgiA_LyaBg"

GOOGLE_NEWS_ARTICLE = re.compile(r"news\.google\.com/rss/articles/([^?/]+)")
VERTEX_REDIRECT = "vertexaisearch.cloud.google.com/grounding-api-redirect"
BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

# A publisher blocking our user agent is not a broken link: a human clicking it
# in a browser gets the article. Only genuinely missing pages count as dead.
BOT_BLOCKED = {401, 403, 406, 429}

_session_lock = threading.Lock()
_session = None
_last_request = 0.0


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers["User-Agent"] = USER_AGENT
            _session.cookies.set("SOCS", SOCS_COOKIE)
        return _session


def _throttle():
    global _last_request
    delay = MIN_INTERVAL - (time.monotonic() - _last_request)
    if delay > 0:
        time.sleep(delay)
    _last_request = time.monotonic()


def _classify(url, http_status, redirector):
    if not url:
        return REDIRECT_UNRESOLVED
    # Guarded: an empty redirector is a substring of every URL.
    if redirector and redirector in url:
        return REDIRECT_UNRESOLVED
    if http_status is None:
        return REDIRECT_UNRESOLVED
    if http_status < 400 or http_status in BOT_BLOCKED:
        return RESOLVED
    return DEAD


def _resolve_google_news(url):
    """Two hops: read the per-article signature, then ask Google's own endpoint.

    The article ID is no longer a base64-encoded URL, and following the redirect
    lands on a consent page, so this is the only route that yields a publisher URL.
    """
    match = GOOGLE_NEWS_ARTICLE.search(url)
    if not match:
        return None, None
    article_id = match.group(1)
    session = _get_session()

    _throttle()
    page = session.get(url, timeout=TIMEOUT)
    signature = re.search(r'data-n-a-sg="([^"]+)"', page.text)
    timestamp = re.search(r'data-n-a-ts="([^"]+)"', page.text)
    if not signature or not timestamp:
        log.debug("url resolve: no signature on Google News page")
        return None, page.status_code

    inner = json.dumps(
        [
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            article_id,
            int(timestamp.group(1)),
            signature.group(1),
        ]
    )
    _throttle()
    response = session.post(
        BATCHEXECUTE,
        params={"rpcids": "Fbv4je"},
        data={"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])},
        timeout=TIMEOUT,
    )
    found = re.findall(
        r'https?://(?!news\.google|www\.google|consent\.google)[^\\"\s]{12,400}', response.text
    )
    return (found[0] if found else None), response.status_code


def _follow(url):
    """Plain redirect following, for Vertex tokens and ordinary links."""
    _throttle()
    response = _get_session().get(url, allow_redirects=True, timeout=TIMEOUT)
    return response.url, response.status_code


def resolve_url(url, use_cache=True):
    """-> {original_url, resolved_url, url_status, http_status}."""
    if not url:
        return {"original_url": url, "resolved_url": None, "url_status": REDIRECT_UNRESOLVED,
                "http_status": None}

    if use_cache:
        stored = cache.get_url_resolution(url)
        if stored:
            return {"original_url": url, **stored}

    redirector = "news.google.com" if GOOGLE_NEWS_ARTICLE.search(url) else (
        "vertexaisearch.cloud.google.com" if VERTEX_REDIRECT in url else ""
    )

    try:
        if redirector == "news.google.com":
            resolved, http_status = _resolve_google_news(url)
            if resolved:
                # The batchexecute reply proves the link, but not that it loads.
                try:
                    resolved, http_status = _follow(resolved)
                except requests.RequestException:
                    http_status = None
        else:
            resolved, http_status = _follow(url)
    except requests.RequestException as exc:
        log.warning("url resolve: failed (%s)", type(exc).__name__)
        resolved, http_status = None, None

    result = {
        "resolved_url": resolved,
        "url_status": _classify(resolved, http_status, redirector),
        "http_status": http_status,
    }
    if use_cache and result["url_status"] != REDIRECT_UNRESOLVED:
        cache.put_url_resolution(url, **result)
    return {"original_url": url, **result}


def annotate(items, url_field="url", limit=None, should_resolve=None):
    """Attach resolution fields to a list of evidence items, in place.

    Resolution costs two HTTP round trips per Google News article, so callers
    cap it. Anything not attempted stays redirect_unresolved rather than being
    dropped or silently marked good.
    """
    attempted = 0
    for item in items:
        eligible = should_resolve(item) if should_resolve else True
        if not eligible or (limit is not None and attempted >= limit):
            item.setdefault("resolved_url", None)
            item.setdefault("url_status", REDIRECT_UNRESOLVED)
            item.setdefault("http_status", None)
            item.setdefault("original_url", item.get(url_field))
            continue
        attempted += 1
        item.update(resolve_url(item.get(url_field)))
    return items
