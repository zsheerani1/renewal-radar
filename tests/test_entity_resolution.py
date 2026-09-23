"""Resolution tests against the real Companies House candidate lists.

Hilton coming back low/none is the correct answer, not a failure: the register
genuinely contains many unrelated companies called Hilton, and Hilton Worldwide's
UK entity is not among them.
"""

import json
from pathlib import Path

import pytest

from sources._normalise import brand_token, normalise_name
from sources.entity_resolution import HIGH, LOW, MEDIUM, NONE, resolve, score_candidate

FIXTURES = Path(__file__).parent / "fixtures"


def load(slug):
    return json.loads((FIXTURES / f"ch_{slug}.json").read_text())["candidates"]


def test_normalise_strips_legal_forms():
    assert normalise_name("CORINTHIA HOTELS (UK) LIMITED") == "corinthia hotels"


def test_brand_token_from_email_and_domain():
    assert brand_token("mfrench@corinthia.com") == "corinthia"
    assert brand_token("www.hilton.com") == "hilton"
    assert brand_token(None) is None


def test_corinthians_do_not_match_corinthia():
    """The football clubs are the reason substring matching is banned."""
    club = {"title": "CHIPPERFIELD CORINTHIANS FOOTBALL CLUB LIMITED", "company_status": "active"}
    assert score_candidate("Corinthia Hotels", club, "corinthia") == 0.0


def test_corinthia_resolves_to_the_uk_subsidiary():
    result = resolve("Corinthia Hotels", load("corinthia_hotels"), "mfrench@corinthia.com")
    assert result["confidence"] in (HIGH, MEDIUM)
    assert result["company_number"] == "11874049"
    assert "CORINTHIA HOTELS (UK) LIMITED" == result["matched_name"]


def test_hilton_is_not_resolved():
    """Many unrelated Hiltons, none dominant. Unknown beats wrong."""
    result = resolve("Hilton", load("hilton"), "hilton.com")
    assert result["confidence"] in (LOW, NONE)
    assert result["company_number"] is None
    assert result["matched_name"] is None


def test_hilton_still_returns_candidates_for_a_human():
    result = resolve("Hilton", load("hilton"), "hilton.com")
    assert len(result["candidates"]) > 1


def test_dissolved_company_is_penalised():
    active = {"title": "CORINTHIA HOTELS (UK) LIMITED", "company_status": "active"}
    dissolved = {"title": "CORINTHIA HOTELS (UK) LIMITED", "company_status": "dissolved"}
    assert score_candidate("Corinthia Hotels", active) > score_candidate("Corinthia Hotels", dissolved)


def test_no_candidates_is_none_confidence():
    result = resolve("Corinthia Hotels", [], "corinthia.com")
    assert result["confidence"] == NONE
    assert result["company_number"] is None


def test_unrelated_candidates_score_nothing():
    result = resolve("Aman Resorts", load("hilton"), "aman.com")
    assert result["confidence"] == NONE


@pytest.mark.parametrize("slug,account", [
    ("corinthia_hotels", "Corinthia Hotels"),
    ("hilton", "Hilton"),
    ("millennium_hotels", "Millennium Hotels"),
])
def test_accepted_resolution_always_has_a_number(slug, account):
    result = resolve(account, load(slug))
    if result["confidence"] in (HIGH, MEDIUM):
        assert result["company_number"]
    else:
        assert result["company_number"] is None
