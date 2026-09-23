from datetime import date

from scoring import score

TODAY = date(2026, 9, 12)


def base(**overrides):
    f = {
        "maintenance_expiry": None,
        "change_signal": False,
        "change_urgency_stated": False,
        "competitor_live": False,
        "competitor_troubled": False,
    }
    f.update(overrides)
    return f


def test_no_signals_floors_at_one():
    r = score(base(), TODAY)
    assert r["heat"] == 1
    assert r["criteria"] == 1  # a clear field (C3) is the only point a blank account earns
    assert r["urgency"] == 0


def test_expiry_unknown_caps_at_three():
    r = score(base(change_signal=True), TODAY)
    assert r["heat"] <= 3


def test_expiry_within_year_scores_c1():
    r = score(base(maintenance_expiry="2027-06"), TODAY)
    assert r["flags"]["c1"] is True
    assert r["flags"]["u1"] is False


def test_expiry_within_three_months_scores_u1_too():
    r = score(base(maintenance_expiry="2026-11"), TODAY)
    assert r["flags"]["c1"] is True
    assert r["flags"]["u1"] is True


def test_expiry_overdue_within_three_months_still_counts():
    r = score(base(maintenance_expiry="2026-07"), TODAY)
    assert r["flags"]["c1"] is True
    assert r["flags"]["u1"] is True


def test_expiry_overdue_beyond_three_months_only_c1():
    r = score(base(maintenance_expiry="2026-01"), TODAY)
    assert r["flags"]["c1"] is False
    assert r["flags"]["u1"] is False


def test_competitor_live_and_healthy_rules_out_c3():
    r = score(base(competitor_live=True, competitor_troubled=False), TODAY)
    assert r["flags"]["c3"] is False


def test_competitor_live_but_troubled_counts_c3():
    r = score(base(competitor_live=True, competitor_troubled=True), TODAY)
    assert r["flags"]["c3"] is True


def test_all_signals_caps_at_five():
    r = score(
        base(
            maintenance_expiry="2026-10",
            change_signal=True,
            change_urgency_stated=True,
            competitor_live=True,
            competitor_troubled=True,
        ),
        TODAY,
    )
    assert r["heat"] == 5
