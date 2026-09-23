"""URL resolution tests.

The live test is marked `network` and skipped by default: it proves resolved
URLs are genuinely fetchable, which is the whole claim being made, but it hits
Google and a publisher so it must not run in a normal test pass.

    pytest -m network tests/test_urls.py
"""

import pytest

from sources._urls import DEAD, REDIRECT_UNRESOLVED, RESOLVED, _classify, resolve_url


def test_classify_treats_bot_blocks_as_resolved():
    """A 403 to our user agent is still a link a human can click."""
    assert _classify("https://maltaceos.mt/article", 403, "vertexaisearch.cloud.google.com") == RESOLVED
    assert _classify("https://example.com/a", 200, "") == RESOLVED


def test_classify_marks_missing_pages_dead():
    assert _classify("https://example.com/gone", 404, "") == DEAD
    assert _classify("https://example.com/gone", 410, "") == DEAD


def test_classify_unresolved_when_still_on_redirector():
    assert _classify(
        "https://news.google.com/rss/articles/CBMiabc", 200, "news.google.com"
    ) == REDIRECT_UNRESOLVED
    assert _classify(None, None, "news.google.com") == REDIRECT_UNRESOLVED


def test_empty_url_is_unresolved():
    result = resolve_url("")
    assert result["url_status"] == REDIRECT_UNRESOLVED
    assert result["resolved_url"] is None


@pytest.mark.network
def test_resolved_google_news_urls_are_fetchable():
    """A sample of resolved URLs must return 200 on a plain GET."""
    import requests

    from sources import google_news

    result = google_news.search_news(
        "Corinthia Hotels", country="mt", domain="corinthia.com", resolve_limit=5
    )
    resolved = [a for a in result.data if a.get("url_status") == RESOLVED]
    assert resolved, "no URLs resolved at all"

    ok = 0
    for article in resolved[:5]:
        url = article["resolved_url"]
        assert "news.google.com" not in url
        response = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            ok += 1
    assert ok, "no resolved URL returned 200 on a plain GET"
