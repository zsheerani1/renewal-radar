"""Pure scoring logic. No IO, no network — safe to unit test in isolation.

Two scorers live here. `score` serves the legacy findings path. `score_verdicts`
serves the evidence path and works only from judged verdicts, where `unknown`
never earns a point.
"""

import math
from datetime import date

INSUFFICIENT = "insufficient_data"
ELEVATED = "elevated"
NORMAL = "normal"

INCUMBENT = "infor sunsystems"
COMPETITOR_SYSTEMS = (
    "oracle", "sap", "netsuite", "workday", "dynamics", "sage", "unit4",
    "agresso", "epicor", "peoplesoft", "coda", "xero", "quickbooks",
)

DETERMINISTIC_SOURCES = {"companies_house", "adzuna", "google_news"}

GRADE_A, GRADE_B, GRADE_C = "A", "B", "C"


def months_until(expiry: str, today: date | None = None) -> int:
    """Whole months from today to an expiry given as 'YYYY-MM'.

    Negative when the date has already passed.
    """
    today = today or date.today()
    year, month = (int(part) for part in expiry.split("-"))
    return (year - today.year) * 12 + (month - today.month)


def score(findings: dict, today: date | None = None) -> dict:
    """Turn a Findings dict into a heat score, per the build blueprint's rulebook.

    C1/C2/C3 are criteria points, U1/U2 are urgency points. An account overdue
    on maintenance (up to 3 months past expiry) still counts as both C1 and U1 —
    a lapsed contract is not a cold signal.
    """
    expiry = findings.get("maintenance_expiry")
    months = months_until(expiry, today) if expiry else None

    c1 = months is not None and -3 <= months <= 12
    c2 = findings.get("change_signal") is True
    c3 = (not findings.get("competitor_live")) or findings.get("competitor_troubled") is True

    u1 = months is not None and -3 <= months <= 3
    u2 = findings.get("change_urgency_stated") is True

    criteria = int(c1) + int(c2) + int(c3)
    urgency = int(u1) + int(u2)
    heat = min(5, max(1, criteria + urgency))

    reasons = []
    if c1:
        reasons.append("Maintenance expiry within 12 months" if months >= 0 else "Maintenance expiry has lapsed")
    if c2:
        reasons.append("Change signal found")
    if c3:
        if findings.get("competitor_troubled"):
            reasons.append("Competitor implementation reported troubled")
        elif not findings.get("competitor_live"):
            reasons.append("No live competitor system found")
    if u1:
        reasons.append("Expiry within 3 months — urgent")
    if u2:
        reasons.append("Urgency explicitly stated in source")
    if not reasons:
        reasons.append("No qualifying signals found")

    return {
        "heat": heat,
        "criteria": criteria,
        "urgency": urgency,
        "flags": {"c1": c1, "c2": c2, "c3": c3, "u1": u1, "u2": u2},
        "reasons": reasons,
    }


def _month_key(value):
    return (value or "")[:7]


def hiring_signal(counts, today=None, window=6):
    """Finance hiring volume, computed not judged.

    One month of data is not a quiet market, it is an unread one — so a short
    history returns insufficient_data rather than normal. "We don't know" and
    "nothing unusual" are different answers and must not share a label.
    """
    counts = counts or {}
    today = today or date.today()

    months_observed = len([m for m in counts if m])
    if months_observed < 3:
        return {
            "verdict": INSUFFICIENT,
            "last_6m": sum(counts.values()),
            "baseline": None,
            "months_observed": months_observed,
        }

    def months_back(n):
        year, month = today.year, today.month - n
        while month <= 0:
            month += 12
            year -= 1
        return f"{year:04d}-{month:02d}"

    recent_keys = {months_back(n) for n in range(window)}
    baseline_keys = {months_back(n) for n in range(window, window * 2)}

    recent = sum(v for k, v in counts.items() if _month_key(k) in recent_keys)
    baseline = sum(v for k, v in counts.items() if _month_key(k) in baseline_keys)

    elevated = recent >= 3 and recent >= 2 * baseline
    return {
        "verdict": ELEVATED if elevated else NORMAL,
        "last_6m": recent,
        "baseline": baseline,
        "months_observed": months_observed,
    }


def position(verdicts):
    """Who holds this account's finance system, judged from the system verdict."""
    value = ((verdicts.get("system_in_use") or {}).get("value") or "").lower()
    if not value:
        return "unknown"
    if INCUMBENT in value or "sunsystems" in value:
        return "incumbent"
    if any(name in value for name in COMPETITOR_SYSTEMS):
        return "competitor_held"
    return "unknown"


def evidence_grade(verdicts):
    """A heat 3 on grade C evidence is not the same lead as a heat 3 on grade A."""
    confirmed = [
        (signal, item) for signal, item in verdicts.items()
        if isinstance(item, dict) and item.get("verdict") == "confirmed"
    ]
    deterministic = [
        s for s, item in confirmed if item.get("evidence_source") in DETERMINISTIC_SOURCES
    ]
    if len(deterministic) >= 2:
        return GRADE_A
    if confirmed:
        return GRADE_B
    return GRADE_C


def months_since_trigger(verdicts, today=None):
    """Freshest confirmed change event, for sorting."""
    ages = []
    for signal in ("leadership_change", "restructuring"):
        item = verdicts.get(signal) or {}
        if item.get("verdict") != "confirmed":
            continue
        months = _months_from(item.get("event_date"), today)
        if months is not None:
            ages.append(months)
    return min(ages) if ages else None


def _months_from(value, today=None):
    if not value:
        return None
    try:
        return months_until(_month_key(value), today) * -1
    except (ValueError, IndexError):
        return None


def _round_half_up(value):
    """2.5 -> 3. Python's round() is banker's rounding and would give 2."""
    return int(math.floor(value + 0.5))


def competitive_position(verdicts):
    """How winnable is this account, given who holds the finance system.

    Absence of a competitor is not a buying signal. An account nobody has
    displaced is usually settled, so ignorance must not outrank evidence: a
    troubled competitor project is the best lead on the board, and an account we
    know nothing about is the weakest.
    """
    competitor = verdicts.get("competitor_in_place") or {}
    verdict = competitor.get("verdict")
    project = competitor.get("project_status")

    if verdict == "confirmed" and project == "troubled":
        return 1.0, "competitor project troubled - takeover target"
    if verdict in ("confirmed", "suspected") and project != "none_found":
        return 0.25, f"{competitor.get('value') or 'competitor'} in place - displacement"
    if position(verdicts) == "incumbent":
        return 0.5, "our system already in place - upgrade or expand"
    return 0.25, "no competitor evidenced - likely settled"


def score_verdicts(verdicts, today=None):
    """Heat from judged verdicts. `unknown` never earns a point.

    Expiry is weighted at half a point because it is almost never findable; it
    stays in the model because it is decisive when it IS found, and urgency still
    pays a full point for an expiry inside three months.
    """
    today = today or date.today()

    expiry = verdicts.get("maintenance_expiry") or {}
    expiry_months = None
    if expiry.get("value"):
        try:
            expiry_months = months_until(_month_key(expiry["value"]), today)
        except (ValueError, IndexError):
            expiry_months = None
    in_window = expiry_months is not None and 0 <= expiry_months <= 12

    reasons = []
    renewal_window = 0.0
    if in_window and expiry.get("verdict") == "confirmed":
        renewal_window = 0.5
        reasons.append(f"maintenance expiry confirmed in {expiry_months}m")
    elif in_window and expiry.get("verdict") == "inferred":
        # Half of confirmed, keeping the same ratio the original spec used.
        renewal_window = 0.25
        reasons.append(f"maintenance expiry inferred in {expiry_months}m")

    # Leadership and restructuring stack: two dated changes in a year is a
    # stronger signal than either alone, and the old cap hid that.
    components = {"renewal_window": renewal_window}
    for signal in ("leadership_change", "restructuring"):
        item = verdicts.get(signal) or {}
        age = _months_from(item.get("event_date"), today)
        earned = 0.0
        if item.get("verdict") == "confirmed" and age is not None and 0 <= age <= 12:
            earned = 1.0
            reasons.append(f"{signal.replace('_', ' ')} confirmed {age}m ago")
        components[signal] = earned

    hiring = verdicts.get("hiring_signal") or {}
    components["hiring"] = 0.5 if hiring.get("verdict") == ELEVATED else 0.0
    if components["hiring"]:
        reasons.append("elevated finance hiring")

    weight, why = competitive_position(verdicts)
    components["competitive_position"] = weight
    reasons.append(why)

    urgency = 0.0
    if expiry.get("verdict") == "confirmed" and expiry_months is not None and 0 <= expiry_months <= 3:
        urgency += 1.0
        reasons.append("expiry within 3 months")
    if (verdicts.get("stated_replacement_intent") or {}).get("verdict") == "confirmed":
        urgency += 1.0
        reasons.append("replacement intent stated")

    criteria = sum(components.values())
    raw = criteria + urgency
    heat = min(5, max(1, _round_half_up(raw)))

    return {
        "heat": heat,
        # The 1-5 badge floors at 1, so a troubled-competitor account (1.0) and an
        # unread one (0.25) display identically. Rank on the raw total instead.
        "rank_score": round(raw, 2),
        "criteria": round(criteria, 2),
        "urgency": round(urgency, 2),
        "components": components,
        "evidence_grade": evidence_grade(verdicts),
        "position": position(verdicts),
        "months_since_trigger": months_since_trigger(verdicts, today),
        "reasons": reasons,
    }
