"""Shared HTTP behaviour: one retry on 5xx or 429, never raise, never log credentials."""

import logging
import time

import requests

from ._result import ERROR, OK, RATE_LIMITED

log = logging.getLogger(__name__)

TIMEOUT = 20
USER_AGENT = "sunsystems-radar/0.1"
RATE_LIMIT_BACKOFF = 6.0


def get_json(url, *, params=None, auth=None, source=""):
    """GET and parse JSON. Returns (payload_or_None, status)."""
    response, status = get_response(url, params=params, auth=auth, source=source)
    if status != OK:
        return None, status
    try:
        return response.json(), OK
    except ValueError:
        log.warning("%s: response was not valid JSON (%d bytes)", source, len(response.content))
        return None, ERROR


def get_bytes(url, *, params=None, auth=None, source=""):
    """GET raw bytes, for feeds and anything else that isn't JSON."""
    response, status = get_response(url, params=params, auth=auth, source=source)
    return (response.content if status == OK else None), status


def get_response(url, *, params=None, auth=None, source=""):
    """One retry on 5xx or 429. Never raises. Returns (response_or_None, status).

    Exceptions are logged by class name only: requests puts the full URL in its
    messages, which for Adzuna would mean writing the API key to the log.
    """
    for attempt in (1, 2):
        try:
            response = requests.get(
                url,
                params=params,
                auth=auth,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as exc:
            log.warning("%s: request failed (%s)", source, type(exc).__name__)
            return None, ERROR


        if response.status_code == 429:
            if attempt == 1:
                log.warning("%s: HTTP 429 rate limited — backing off %.0fs", source, RATE_LIMIT_BACKOFF)
                time.sleep(RATE_LIMIT_BACKOFF)
                continue
            log.warning("%s: HTTP 429 rate limited — giving up", source)
            return None, RATE_LIMITED

        if response.status_code >= 500:
            log.warning(
                "%s: HTTP %s%s",
                source,
                response.status_code,
                " — retrying" if attempt == 1 else " — giving up",
            )
            continue

        if response.status_code != 200:
            log.warning("%s: HTTP %s", source, response.status_code)
            return None, ERROR

        return response, OK

    return None, ERROR
