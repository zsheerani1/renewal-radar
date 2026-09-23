"""Ties research, cache and scoring together for one account or a batch.

Two engines run side by side. "legacy" is the original single grounded call.
"evidence" assembles a bundle from the deterministic sources and has it
classified into per-signal verdicts. The legacy path stays until the new one is
visibly better on the same accounts.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import bundle as bundle_module
import cache
import judge
import research
import scoring

log = logging.getLogger(__name__)

MAX_WORKERS = 4

LEGACY = "legacy"
EVIDENCE = "evidence"


def process_account(account: dict, force_refresh: bool = False) -> dict:
    name = account["name"]
    findings = None if force_refresh else cache.get(name)
    cached = findings is not None

    if findings is None:
        findings = research.research_account(name, account.get("email"))
        cache.put(name, findings)

    scored = scoring.score(findings)
    analysis = research.write_analysis(findings, scored)

    return {
        "row": account["row"],
        "name": name,
        "heat": scored["heat"],
        "analysis": analysis,
        "findings": findings,
        "scored": scored,
        "cached": cached,
    }


SIGNAL_LABELS = {
    "system_in_use": "System",
    "competitor_in_place": "Competitor",
    "maintenance_expiry": "Expiry",
    "leadership_change": "Leadership",
    "restructuring": "Restructuring",
    "hiring_signal": "Hiring",
    "stated_replacement_intent": "Replacement intent",
}


def _summarise(verdicts):
    """Assemble prose from confirmed facts only, in code. No second AI call."""
    parts = []
    for signal in ("leadership_change", "restructuring", "system_in_use", "competitor_in_place"):
        item = verdicts.get(signal) or {}
        if item.get("verdict") in ("confirmed", "likely", "suspected") and item.get("value"):
            parts.append(f"{SIGNAL_LABELS[signal]}: {item['value']} ({item['verdict']}).")
    return " ".join(parts) or "No qualifying signals found in the evidence gathered."


def process_account_evidence(account: dict, force_refresh: bool = False) -> dict:
    """Score from stored evidence unless explicitly refreshed.

    Re-fetching on every run made the same account score differently hour to
    hour, because the live sources moved underneath it. Replaying a stored
    observation makes a run reproducible; Force refresh is how you get new data.
    """
    name = account["name"]
    stored = None if force_refresh else cache.latest_observation(name)

    if stored:
        evidence = stored["bundle"]
        verdicts = stored["verdicts"]
        notes = []
        observed_at = stored["observed_at"]
    else:
        evidence = bundle_module.build(
            name, account.get("email"), account.get("country"), use_grounded=True
        )
        result = judge.judge(evidence)
        verdicts = result["verdicts"]
        notes = result["notes"]
        observed_at = cache.add_observation(name, evidence, verdicts)

    # Hiring volume is arithmetic, not judgement, so it is computed here rather
    # than asked for — it was the only verdict that moved between identical runs.
    verdicts["hiring_signal"] = scoring.hiring_signal(evidence.get("finance_role_counts"))
    scored = scoring.score_verdicts(verdicts)

    sources = []
    for signal, item in verdicts.items():
        if isinstance(item, dict) and item.get("source_url"):
            sources.append({"title": f"{SIGNAL_LABELS.get(signal, signal)}: {item['verdict']}",
                            "url": item["source_url"]})

    return {
        "row": account["row"],
        "name": name,
        "engine": EVIDENCE,
        "heat": scored["heat"],
        "evidence_grade": scored["evidence_grade"],
        "position": scored["position"],
        "months_since_trigger": scored["months_since_trigger"],
        # Analysis is the assembled confirmed-facts summary; the hook is the
        # spoken opener. Falling back to the hook here made the two identical.
        "analysis": _summarise(verdicts),
        "hook": verdicts.get("hook"),
        "reasons": [
            f"{SIGNAL_LABELS[s]}: {(verdicts.get(s) or {}).get('verdict', 'unknown')}"
            for s in SIGNAL_LABELS
        ],
        "verdicts": verdicts,
        "judge_notes": notes,
        "bundle": evidence,
        "observed_at": observed_at,
        "findings": {"sources": sources},
        "scored": scored,
        "cached": bool(stored),
        "needs_confirmation": evidence["companies_house"].get("needs_confirmation", False),
        "counters": evidence["counters"],
    }


def run_batch(accounts: list[dict], force_refresh: bool = False, on_result=None,
              engine: str = LEGACY) -> list[dict]:
    """Research accounts concurrently. Failures come back as rows with an error key."""
    processor = process_account_evidence if engine == EVIDENCE else process_account
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(processor, a, force_refresh): a for a in accounts}
        for future in as_completed(futures):
            account = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                # One retry: batch failures have been transient every time, and
                # the same account succeeded on its own moments later.
                log.warning("%s failed (%s), retrying once", account["name"], type(exc).__name__)
                try:
                    result = processor(account, force_refresh)
                    result["retried"] = True
                except Exception as retry_exc:
                    result = {
                        "row": account["row"],
                        "name": account["name"],
                        "heat": None,
                        "analysis": f"Research failed: {retry_exc}",
                        "error": str(retry_exc),
                    }
            results.append(result)
            if on_result:
                on_result(result)
    return sorted(results, key=lambda r: r["row"])
