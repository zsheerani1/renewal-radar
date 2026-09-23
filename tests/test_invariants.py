"""Collapse detectors.

Three times now a classifier has silently returned the same answer for every
input: accents stripped so every French article scored none, an empty redirector
substring so every URL read redirect_unresolved, and before that a filter that
marked everything matched. Each was found by a person reading output.

These tests fail instead. The rule they encode: when the inputs genuinely differ,
the classifications must not all be identical.
"""

import json
from pathlib import Path

import pytest

from sources._normalise import EXACT, FUZZY, NONE, classify_match
from sources._urls import DEAD, REDIRECT_UNRESOLVED, RESOLVED, _classify

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLES = ["bundle_corinthia.json", "bundle_hilton.json"]


def load_bundle(name):
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"{name} not captured")
    return json.loads(path.read_text())


def assert_not_collapsed(values, label):
    """Differing inputs must not all land on one classification."""
    distinct = set(values)
    assert len(distinct) > 1, (
        f"{label} collapsed: {len(values)} differing inputs all classified {distinct}. "
        "A filter or normaliser has failed silently."
    )


def test_name_match_does_not_collapse():
    """Inputs spanning exact, partial and unrelated must not all classify alike."""
    inputs = [
        ("Société Générale va supprimer 1 800 emplois", "Société Générale", "societegenerale"),
        ("Millennium & Copthorne board change", "Millennium Hotels", None),
        ("Essex fish and chip shop shortlisted", "Corinthia Hotels", "corinthia"),
        ("Corinthia sells big stake in Lisbon", "Corinthia Hotels", "corinthia"),
        ("Taiwan Semiconductor Trading Up 2.5%", "Hilton", "hilton"),
    ]
    assert_not_collapsed([classify_match(t, a, d) for t, a, d in inputs], "classify_match")


def test_url_status_does_not_collapse():
    inputs = [
        ("https://example.com/live", 200, ""),
        ("https://example.com/blocked", 403, ""),
        ("https://example.com/gone", 404, ""),
        ("https://news.google.com/rss/articles/CBMiabc", 200, "news.google.com"),
    ]
    assert_not_collapsed([_classify(u, s, r) for u, s, r in inputs], "_classify")


def test_url_classify_covers_every_state():
    """Each state must be reachable, not merely distinct from the others."""
    assert _classify("https://example.com/a", 200, "") == RESOLVED
    assert _classify("https://example.com/a", 404, "") == DEAD
    assert _classify(None, None, "") == REDIRECT_UNRESOLVED


@pytest.mark.parametrize("name", BUNDLES)
def test_bundle_source_status_not_collapsed(name):
    """A real bundle mixes reachable and unreachable sources."""
    bundle = load_bundle(name)
    statuses = [entry.get("status") for entry in bundle["sources"].values()]
    assert_not_collapsed(statuses, f"{name} source status")


@pytest.mark.parametrize("name", BUNDLES)
def test_bundle_name_match_not_collapsed(name):
    bundle = load_bundle(name)
    tags = [a.get("name_match") for a in bundle["news"]]
    if len(tags) < 2:
        pytest.skip("too few articles to judge collapse")
    assert_not_collapsed(tags, f"{name} name_match")


@pytest.mark.parametrize("name", BUNDLES)
def test_bundle_url_status_not_collapsed(name):
    """Some articles resolve and some are left unattempted; never all one way."""
    bundle = load_bundle(name)
    statuses = [a.get("url_status") for a in bundle["news"]]
    if len(statuses) < 2:
        pytest.skip("too few articles to judge collapse")
    assert_not_collapsed(statuses, f"{name} url_status")


@pytest.mark.parametrize("name", BUNDLES)
def test_bundle_match_rate_is_plausible(name):
    """A rate of exactly 0 or exactly 1 across many articles is the failure shape."""
    bundle = load_bundle(name)
    counters = bundle["counters"]
    retrieved = counters.get("articles_retrieved") or 0
    if retrieved < 5:
        pytest.skip("too few articles")
    rate = counters.get("match_rate")
    assert rate is not None
    assert 0 < rate <= 1, f"match_rate {rate} on {retrieved} articles looks like a collapse"
