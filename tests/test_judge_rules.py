"""Adversarial tests for the judge's citation rules.

These do not ask the model anything. They feed judge.validate() verdicts that a
confident model would plausibly produce and assert the ceiling holds. A rule
that lives only in the prompt is a request; these are the enforcement.
"""

import pytest

from judge import validate

RESOLVED_NEWS = "https://www.hospitalitynet.org/news/4131793/orion.html"
UNRESOLVED_NEWS = "https://news.google.com/rss/articles/CBMiOPAQUE"
GROUNDED_DATED = "https://www.llpgroup.com/case-studies/corinthia-case-study/"
GROUNDED_UNDATED = "https://www.corinthia.com/en-gb/our-team/"
GROUNDED_UNRESOLVED = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/ABC"
CH_URL = "https://find-and-update.company-information.service.gov.uk/company/11874049"


@pytest.fixture
def bundle():
    return {
        "account": "Corinthia Hotels",
        "companies_house": {
            "company_number": "11874049",
            "source_url": CH_URL,
            "officers_url": f"{CH_URL}/officers",
            "finance_officers": [],
            "officers": [{"name": "FENECH, Clinton", "role": "director", "appointed_on": "2019-03-11"}],
        },
        "news": [
            {"title": "Orion completes acquisition", "url": RESOLVED_NEWS,
             "resolved_url": RESOLVED_NEWS, "url_status": "resolved", "name_match": "exact"},
            {"title": "Unresolvable story", "url": UNRESOLVED_NEWS,
             "resolved_url": None, "url_status": "redirect_unresolved", "name_match": "exact"},
            {"title": "Essex fish and chip shop", "url": "https://gazette.co.uk/chips",
             "resolved_url": "https://gazette.co.uk/chips", "url_status": "resolved",
             "name_match": "none"},
        ],
        "job_ads": [],
        "grounded_claims": [
            {"claim_type": "system_in_use", "date": "2019-08", "source_url": GROUNDED_DATED,
             "resolved_url": GROUNDED_DATED, "url_status": "resolved"},
            {"claim_type": "leadership_change", "date": None, "source_url": GROUNDED_UNDATED,
             "resolved_url": GROUNDED_UNDATED, "url_status": "resolved"},
            {"claim_type": "competitor", "date": "2024-01", "source_url": GROUNDED_UNRESOLVED,
             "resolved_url": None, "url_status": "redirect_unresolved"},
        ],
    }


def verdict_for(bundle, signal, **fields):
    clean, notes = validate({signal: fields}, bundle)
    return clean[signal]["verdict"], notes


def test_confirmed_survives_on_resolved_deterministic_news(bundle):
    """The ceiling must not be so tight that good evidence cannot pass."""
    got, _ = verdict_for(bundle, "restructuring", verdict="confirmed",
                         source_url=RESOLVED_NEWS, evidence_source="google_news")
    assert got == "confirmed"


def test_unresolved_url_cannot_support_confirmed(bundle):
    got, notes = verdict_for(bundle, "restructuring", verdict="confirmed",
                             source_url=UNRESOLVED_NEWS, evidence_source="google_news")
    assert got == "suspected"
    assert any("url_status not resolved" in n for n in notes)


def test_unresolved_url_still_supports_the_middle_verdict(bundle):
    """Degrade the claim, do not delete the lead."""
    got, _ = verdict_for(bundle, "restructuring", verdict="suspected",
                         source_url=UNRESOLVED_NEWS, evidence_source="google_news")
    assert got == "suspected"


def test_invented_url_collapses_to_unknown(bundle):
    got, notes = verdict_for(bundle, "system_in_use", verdict="confirmed",
                             source_url="https://plausible-but-invented.example.com/erp",
                             evidence_source="google_news")
    assert got == "unknown"
    assert any("not in bundle" in n for n in notes)


def test_name_match_none_article_cannot_be_cited(bundle):
    """Retrieved but not about this company. Citing it is not evidence."""
    got, notes = verdict_for(bundle, "restructuring", verdict="confirmed",
                             source_url="https://gazette.co.uk/chips",
                             evidence_source="google_news")
    assert got == "unknown"
    assert any("not in bundle" in n for n in notes)


def test_grounded_claim_with_date_and_resolved_url_may_confirm(bundle):
    """Rule 9's exception: specific, dated, resolvable."""
    got, _ = verdict_for(bundle, "system_in_use", verdict="confirmed",
                         source_url=GROUNDED_DATED, evidence_source="grounded_search")
    assert got == "confirmed"


def test_undated_grounded_claim_cannot_confirm(bundle):
    got, notes = verdict_for(bundle, "system_in_use", verdict="confirmed",
                             source_url=GROUNDED_UNDATED, evidence_source="grounded_search")
    assert got == "likely"
    assert any("lacks a date" in n for n in notes)


def test_unresolved_grounded_claim_cannot_confirm(bundle):
    got, _ = verdict_for(bundle, "competitor_in_place", verdict="confirmed",
                         source_url=GROUNDED_UNRESOLVED, evidence_source="grounded_search")
    assert got == "suspected"


def test_grounded_search_can_never_rule_out(bundle):
    got, notes = verdict_for(bundle, "competitor_in_place", verdict="ruled_out",
                             source_url=GROUNDED_DATED, evidence_source="grounded_search")
    assert got == "unknown"
    assert any("never rule out" in n for n in notes)


def test_ruled_out_needs_a_citation_at_all(bundle):
    got, _ = verdict_for(bundle, "competitor_in_place", verdict="ruled_out", source_url=None)
    assert got == "unknown"


def test_finance_role_claim_dropped_when_filing_does_not_say_so(bundle):
    clean, notes = validate(
        {"leadership_change": {"verdict": "confirmed", "source_url": f"{CH_URL}/officers",
                               "evidence_source": "companies_house", "is_finance_role": True,
                               "value": "director appointed"}},
        bundle,
    )
    assert clean["leadership_change"]["is_finance_role"] is False
    assert any("finance role claim dropped" in n for n in notes)


def test_invalid_verdict_string_becomes_unknown(bundle):
    got, _ = verdict_for(bundle, "maintenance_expiry", verdict="definitely", source_url=RESOLVED_NEWS)
    assert got == "unknown"


def test_missing_signal_becomes_unknown(bundle):
    clean, notes = validate({}, bundle)
    assert all(clean[s]["verdict"] == "unknown" for s in
               ("system_in_use", "competitor_in_place", "maintenance_expiry"))


def test_reasoning_is_truncated_to_twenty_words(bundle):
    clean, _ = validate(
        {"restructuring": {"verdict": "confirmed", "source_url": RESOLVED_NEWS,
                           "reasoning": " ".join(["word"] * 60)}},
        bundle,
    )
    assert len(clean["restructuring"]["reasoning"].split()) == 20


def test_hook_citing_inferred_expiry_is_stripped(bundle):
    clean, notes = validate(
        {"maintenance_expiry": {"verdict": "inferred", "value": "2027-03",
                                "source_url": GROUNDED_DATED},
         "hook": "With your SunSystems support ending March 2027, are you reviewing options?"},
        bundle,
    )
    assert clean["hook"] is None
    assert any("hook: dropped" in n for n in notes)


def test_hook_citing_suspected_competitor_is_stripped(bundle):
    clean, _ = validate(
        {"competitor_in_place": {"verdict": "suspected", "value": "Oracle Cloud ERP",
                                 "source_url": GROUNDED_DATED},
         "hook": "I saw you are moving to Oracle Cloud ERP, how is the rollout going?"},
        bundle,
    )
    assert clean["hook"] is None


def test_hook_citing_confirmed_leadership_change_is_kept(bundle):
    hook = "Congratulations on appointing Peter Roth as President of Hotel Operations."
    clean, _ = validate(
        {"leadership_change": {"verdict": "confirmed", "value": "Peter Roth appointed President",
                               "source_url": RESOLVED_NEWS, "evidence_source": "google_news"},
         "hook": hook},
        bundle,
    )
    assert clean["hook"] == hook


def test_generic_hook_survives_with_no_confirmed_signals(bundle):
    hook = "Are you reviewing your finance systems this year?"
    clean, _ = validate({"hook": hook}, bundle)
    assert clean["hook"] == hook


def test_hook_citing_downgraded_verdict_is_stripped(bundle):
    """The hook is vetted after step-downs, not against what the model claimed."""
    clean, notes = validate(
        {"system_in_use": {"verdict": "confirmed", "value": "Infor SunSystems",
                           "source_url": GROUNDED_UNDATED, "evidence_source": "grounded_search"},
         "hook": "How is Infor SunSystems working out for the finance team?"},
        bundle,
    )
    assert clean["system_in_use"]["verdict"] == "likely"
    assert clean["hook"] is None
