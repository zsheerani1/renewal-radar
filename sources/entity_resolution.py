"""Pick the right Companies House entity for an account, or admit we cannot.

Pure functions: no network, no database. The whole point is that this is the
part we can prove correct, because getting it wrong attaches a real company
number and real filed dates to the wrong company — which then grades as the
most trustworthy evidence in the system.

The governing rule is that a wrong answer costs far more than no answer.
"""

from ._normalise import brand_token, matches, normalise_name, squash, tokens  # noqa: F401

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
NONE = "none"

MIN_SCORE = 0.55          # below this a candidate is not plausible at all
PLAUSIBLE_SCORE = 0.55    # counts toward the ambiguity check
CLEAR_MARGIN = 0.15       # top must beat the runner-up by this to be trusted

DISSOLVED_STATUSES = {"dissolved", "liquidation", "converted-closed", "closed", "removed"}


def _whole_word_match(token, candidate_tokens):
    """Whole-word only.

    'corinthia' must not match 'corinthians' — the Chipperfield Corinthians
    football clubs are not the Corinthia hotel group.
    """
    return token in candidate_tokens


def score_candidate(account, candidate, domain_token=None):
    """0.0 - 1.0ish. Higher is a better match for this account."""
    account_tokens = tokens(account)
    candidate_tokens = tokens(candidate.get("title"))
    if not account_tokens or not candidate_tokens:
        return 0.0

    matched = sum(1 for t in account_tokens if _whole_word_match(t, candidate_tokens))
    coverage = matched / len(account_tokens)
    if coverage == 0:
        return 0.0

    score = coverage * 0.6

    account_norm = " ".join(account_tokens)
    candidate_norm = " ".join(candidate_tokens)

    if candidate_norm == account_norm:
        score += 0.25
    elif candidate_norm.startswith(account_norm):
        score += 0.20

    if domain_token and _whole_word_match(domain_token, candidate_tokens):
        score += 0.15

    # Words the account name cannot explain: "abbey decorating contractors".
    extra = len([t for t in candidate_tokens if t not in account_tokens])
    score -= min(0.30, extra * 0.10)

    status = (candidate.get("company_status") or "").lower()
    if status in DISSOLVED_STATUSES:
        score -= 0.35
    elif status == "active":
        score += 0.05

    return max(0.0, round(score, 4))


def resolve(account, candidates, domain=None):
    """Return {company_number, matched_name, confidence, scores, candidates}.

    Confidence is deliberately conservative. A single-word account name like
    "Hilton" matches every company whose name merely contains that word, so the
    top score being high is not evidence that it is the right company — only a
    clear margin over the runner-up is.
    """
    domain_token = brand_token(domain)
    scored = sorted(
        (
            {**candidate, "score": score_candidate(account, candidate, domain_token)}
            for candidate in candidates or []
        ),
        key=lambda c: c["score"],
        reverse=True,
    )

    if not scored or scored[0]["score"] < MIN_SCORE:
        return {
            "company_number": None,
            "matched_name": None,
            "confidence": NONE,
            "candidates": scored[:10],
        }

    top = scored[0]
    runner_up = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = top["score"] - runner_up
    plausible = [c for c in scored if c["score"] >= PLAUSIBLE_SCORE]

    top_tokens = tokens(top.get("title"))
    account_norm = " ".join(tokens(account))
    exact = " ".join(top_tokens) == account_norm
    domain_hit = bool(domain_token and _whole_word_match(domain_token, top_tokens))

    if margin < CLEAR_MARGIN and len(plausible) > 1:
        # Several companies fit the name equally well. Picking one is a guess.
        confidence = LOW
    elif (exact or domain_hit) and margin >= CLEAR_MARGIN:
        confidence = HIGH
    elif margin >= CLEAR_MARGIN:
        confidence = MEDIUM
    else:
        confidence = LOW

    accepted = confidence in (HIGH, MEDIUM)
    return {
        "company_number": top["company_number"] if accepted else None,
        "matched_name": top["title"] if accepted else None,
        "confidence": confidence,
        "margin": round(margin, 4),
        "candidates": scored[:10],
    }
