"""Scoring against judged verdicts. `unknown` never earns a point."""

from datetime import date

import pytest

from scoring import (
    ELEVATED, GRADE_A, GRADE_B, GRADE_C, INSUFFICIENT, NORMAL,
    evidence_grade, hiring_signal, position, score_verdicts,
)

TODAY = date(2026, 9, 23)


def verdicts(**overrides):
    base = {s: {"verdict": "unknown"} for s in (
        "system_in_use", "competitor_in_place", "maintenance_expiry",
        "leadership_change", "restructuring", "stated_replacement_intent",
    )}
    base["hiring_signal"] = {"verdict": INSUFFICIENT}
    base.update(overrides)
    return base


# --- hiring signal: arithmetic, with a minimum data requirement ---

def test_one_month_of_counts_is_insufficient_not_normal():
    """The bug this rule exists for: one data point read as 'nothing unusual'."""
    result = hiring_signal({"2026-09": 1}, TODAY)
    assert result["verdict"] == INSUFFICIENT
    assert result["verdict"] != NORMAL
    assert result["months_observed"] == 1


def test_two_months_still_insufficient():
    assert hiring_signal({"2026-08": 2, "2026-09": 1}, TODAY)["verdict"] == INSUFFICIENT


def test_three_months_is_enough_to_judge():
    assert hiring_signal({"2026-07": 1, "2026-08": 1, "2026-09": 1}, TODAY)["verdict"] != INSUFFICIENT


def test_elevated_needs_double_baseline_and_three_roles():
    counts = {"2026-07": 2, "2026-08": 2, "2026-09": 2, "2026-01": 1}
    result = hiring_signal(counts, TODAY)
    assert result["verdict"] == ELEVATED
    assert result["last_6m"] == 6 and result["baseline"] == 1


def test_two_roles_is_not_elevated_however_big_the_ratio():
    counts = {"2026-07": 1, "2026-08": 1, "2026-09": 0, "2026-06": 0}
    assert hiring_signal(counts, TODAY)["verdict"] == NORMAL


def test_steady_hiring_is_normal():
    counts = {"2026-08": 2, "2026-09": 2, "2026-07": 2, "2026-02": 3, "2026-01": 3}
    assert hiring_signal(counts, TODAY)["verdict"] == NORMAL


def test_empty_counts_is_insufficient():
    assert hiring_signal({}, TODAY)["verdict"] == INSUFFICIENT


# --- the cases named in the spec ---

def test_all_unknown_scores_one_grade_c():
    result = score_verdicts(verdicts(), TODAY)
    assert result["heat"] == 1
    assert result["evidence_grade"] == GRADE_C


def test_confirmed_expiry_inside_three_months_earns_criteria_and_urgency():
    """Expiry is half a point as a criterion but still a full point of urgency."""
    result = score_verdicts(verdicts(
        maintenance_expiry={"verdict": "confirmed", "value": "2026-11",
                            "evidence_source": "google_news"},
    ), TODAY)
    assert result["components"]["renewal_window"] == 0.5
    assert result["urgency"] == 1.0


def test_inferred_expiry_earns_only_a_quarter():
    result = score_verdicts(verdicts(
        maintenance_expiry={"verdict": "inferred", "value": "2027-03"},
    ), TODAY)
    assert result["components"]["renewal_window"] == 0.25
    assert result["urgency"] == 0.0


def test_competitor_live_and_healthy_is_hard_but_not_worthless():
    result = score_verdicts(verdicts(
        competitor_in_place={"verdict": "confirmed", "value": "Oracle Cloud ERP",
                             "project_status": "live", "evidence_source": "google_news"},
    ), TODAY)
    assert result["components"]["competitive_position"] == 0.25


def test_troubled_competitor_project_is_the_best_position():
    result = score_verdicts(verdicts(
        competitor_in_place={"verdict": "confirmed", "value": "Oracle",
                             "project_status": "troubled", "evidence_source": "google_news"},
    ), TODAY)
    assert result["components"]["competitive_position"] == 1.0


def test_incumbent_beats_an_account_we_know_nothing_about():
    """Ignorance must not outrank evidence of our own footprint."""
    incumbent = score_verdicts(verdicts(
        system_in_use={"verdict": "confirmed", "value": "Infor SunSystems"},
    ), TODAY)["components"]["competitive_position"]
    unknown = score_verdicts(verdicts(), TODAY)["components"]["competitive_position"]
    assert incumbent == 0.5
    assert unknown == 0.25
    assert incumbent > unknown


def test_absence_of_a_competitor_is_not_a_buying_signal():
    """A settled account scores no better than one we simply have not read."""
    none_found = score_verdicts(verdicts(
        competitor_in_place={"verdict": "unknown", "project_status": "none_found"},
    ), TODAY)["components"]["competitive_position"]
    assert none_found == 0.25


def test_troubled_competitor_outranks_an_unknown_account():
    """Ordering holds on criteria. Note heat ties at the floor: a lone troubled
    competitor scores 1.0 and an empty account 0.25, and both clamp to heat 1."""
    troubled = score_verdicts(verdicts(
        competitor_in_place={"verdict": "confirmed", "value": "Oracle",
                             "project_status": "troubled", "evidence_source": "google_news"},
    ), TODAY)
    unknown = score_verdicts(verdicts(), TODAY)
    assert troubled["criteria"] > unknown["criteria"]
    assert troubled["components"]["competitive_position"] == 1.0
    assert unknown["components"]["competitive_position"] == 0.25


def test_hiring_only_case_earns_half_a_point():
    result = score_verdicts(verdicts(hiring_signal={"verdict": ELEVATED}), TODAY)
    assert result["components"]["hiring"] == 0.5


def test_change_signals_stack_rather_than_collapsing():
    """Two dated changes in a year beat either alone. The old cap hid that."""
    result = score_verdicts(verdicts(
        leadership_change={"verdict": "confirmed", "event_date": "2026-07",
                           "evidence_source": "companies_house"},
        restructuring={"verdict": "confirmed", "event_date": "2026-04",
                       "evidence_source": "google_news"},
        hiring_signal={"verdict": ELEVATED},
    ), TODAY)
    assert result["components"]["leadership_change"] == 1.0
    assert result["components"]["restructuring"] == 1.0
    assert result["components"]["hiring"] == 0.5

    one_change = score_verdicts(verdicts(
        leadership_change={"verdict": "confirmed", "event_date": "2026-07"},
    ), TODAY)
    assert result["heat"] > one_change["heat"]


def test_undated_confirmed_change_earns_nothing():
    result = score_verdicts(verdicts(
        leadership_change={"verdict": "confirmed", "event_date": None},
    ), TODAY)
    assert result["components"]["leadership_change"] == 0.0


def test_change_older_than_twelve_months_earns_nothing():
    result = score_verdicts(verdicts(
        restructuring={"verdict": "confirmed", "event_date": "2024-01"},
    ), TODAY)
    assert result["components"]["restructuring"] == 0.0


def test_half_points_round_up_not_to_even():
    """2.5 must become 3; banker's rounding would give 2."""
    result = score_verdicts(verdicts(
        system_in_use={"verdict": "confirmed", "value": "Infor SunSystems"},
        leadership_change={"verdict": "confirmed", "event_date": "2026-07"},
        restructuring={"verdict": "confirmed", "event_date": "2026-05"},
    ), TODAY)
    assert result["criteria"] == 2.5
    assert result["heat"] == 3


# --- grade and provenance ---

def test_grade_a_needs_two_confirmed_from_deterministic_sources():
    assert evidence_grade(verdicts(
        leadership_change={"verdict": "confirmed", "evidence_source": "companies_house"},
        restructuring={"verdict": "confirmed", "evidence_source": "google_news"},
    )) == GRADE_A


def test_confirmed_resting_only_on_grounded_search_caps_at_b():
    assert evidence_grade(verdicts(
        system_in_use={"verdict": "confirmed", "evidence_source": "grounded_search"},
        competitor_in_place={"verdict": "confirmed", "evidence_source": "grounded_search"},
    )) == GRADE_B


def test_single_deterministic_confirmed_is_grade_b():
    assert evidence_grade(verdicts(
        restructuring={"verdict": "confirmed", "evidence_source": "google_news"},
    )) == GRADE_B


def test_nothing_confirmed_is_grade_c():
    assert evidence_grade(verdicts(
        system_in_use={"verdict": "likely", "evidence_source": "google_news"},
    )) == GRADE_C


def test_grade_accompanies_heat_it_does_not_replace_it():
    result = score_verdicts(verdicts(
        leadership_change={"verdict": "confirmed", "event_date": "2026-08",
                           "evidence_source": "companies_house"},
    ), TODAY)
    assert result["heat"] >= 1 and result["evidence_grade"] in (GRADE_A, GRADE_B, GRADE_C)


def test_position_identifies_incumbent_and_competitor_held():
    assert position(verdicts(system_in_use={"verdict": "confirmed", "value": "Infor SunSystems"})) == "incumbent"
    assert position(verdicts(system_in_use={"verdict": "confirmed", "value": "Oracle EPM Cloud"})) == "competitor_held"
    assert position(verdicts()) == "unknown"


def test_heat_is_clamped_between_one_and_five():
    everything = score_verdicts(verdicts(
        maintenance_expiry={"verdict": "confirmed", "value": "2026-10"},
        leadership_change={"verdict": "confirmed", "event_date": "2026-08"},
        restructuring={"verdict": "confirmed", "event_date": "2026-06"},
        hiring_signal={"verdict": ELEVATED},
        competitor_in_place={"verdict": "confirmed", "value": "Oracle",
                             "project_status": "troubled"},
        stated_replacement_intent={"verdict": "confirmed", "quote": "replacing SunSystems"},
    ), TODAY)
    assert everything["heat"] == 5
    assert score_verdicts(verdicts(), TODAY)["heat"] == 1


def test_expiry_plus_one_change_no_longer_reaches_five():
    """Under the old weights this was a 5. Expiry no longer dominates."""
    result = score_verdicts(verdicts(
        maintenance_expiry={"verdict": "confirmed", "value": "2026-10"},
        leadership_change={"verdict": "confirmed", "event_date": "2026-08"},
        stated_replacement_intent={"verdict": "confirmed", "quote": "replacing SunSystems"},
    ), TODAY)
    assert result["heat"] == 4
