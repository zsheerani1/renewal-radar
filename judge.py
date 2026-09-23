"""Classify an evidence bundle into per-signal verdicts.

One Gemini call, no search grounding: the judge reads only what the sources
already found. Everything the prompt asks for is also enforced here, because a
prompt is a request and this is the last point where a wrong answer is cheap.
"""

import json
import logging
import re
from datetime import date
from pathlib import Path

from google.genai import types

import research
from sources._normalise import NONE as NO_MATCH
from sources._normalise import squash, tokens

log = logging.getLogger(__name__)

PROMPT = Path(__file__).parent / "prompts" / "judge.txt"

FINANCE_WORDS = ("financ", "cfo", "treasur", "account", "controller", "audit")

VERDICTS = {
    "system_in_use": {"confirmed", "likely", "unknown"},
    "competitor_in_place": {"confirmed", "suspected", "ruled_out", "unknown"},
    "maintenance_expiry": {"confirmed", "inferred", "unknown"},
    "leadership_change": {"confirmed", "unknown"},
    "restructuring": {"confirmed", "suspected", "unknown"},
    "stated_replacement_intent": {"confirmed", "unknown"},
}

# What a verdict falls back to when its evidence cannot carry it.
DOWNGRADE = {
    "system_in_use": {"confirmed": "likely", "likely": "unknown"},
    "competitor_in_place": {"confirmed": "suspected", "ruled_out": "unknown", "suspected": "unknown"},
    "maintenance_expiry": {"confirmed": "inferred", "inferred": "unknown"},
    "leadership_change": {"confirmed": "unknown"},
    "restructuring": {"confirmed": "suspected", "suspected": "unknown"},
    "stated_replacement_intent": {"confirmed": "unknown"},
}

SIGNALS_NEEDING_URL = [
    "system_in_use", "competitor_in_place", "maintenance_expiry",
    "leadership_change", "restructuring", "stated_replacement_intent",
]


class JudgeError(Exception):
    pass


def citable_urls(bundle):
    """URLs the judge is allowed to cite, mapped to their url_status.

    Articles tagged name_match "none" are excluded outright: they were retrieved
    but do not name this account, so they are not evidence about it.
    """
    allowed = {}

    companies_house = bundle.get("companies_house") or {}
    for key in ("source_url", "officers_url", "filings_url"):
        if companies_house.get(key):
            allowed[companies_house[key]] = "resolved"

    for article in bundle.get("news") or []:
        if article.get("name_match") == NO_MATCH:
            continue
        status = article.get("url_status") or "redirect_unresolved"
        for url in (article.get("resolved_url"), article.get("url"), article.get("original_url")):
            if url:
                allowed[url] = status

    for ad in bundle.get("job_ads") or []:
        if ad.get("url"):
            allowed[ad["url"]] = "resolved"

    for claim in bundle.get("grounded_claims") or []:
        status = claim.get("url_status") or "redirect_unresolved"
        for url in (claim.get("resolved_url"), claim.get("source_url"), claim.get("original_url")):
            if url:
                allowed[url] = status

    return allowed


def _downgrade(signal, verdict, why, notes):
    new = DOWNGRADE.get(signal, {}).get(verdict, "unknown")
    notes.append(f"{signal}: {verdict} -> {new} ({why})")
    return new


def validate(verdicts, bundle):
    """Enforce the citation rules in code. Returns (verdicts, notes)."""
    allowed = citable_urls(bundle)
    grounded_claims = {}
    for claim in bundle.get("grounded_claims") or []:
        for url in (claim.get("resolved_url"), claim.get("source_url")):
            if url:
                grounded_claims[url] = claim

    notes = []
    clean = {}

    for signal, permitted in VERDICTS.items():
        item = verdicts.get(signal)
        if not isinstance(item, dict):
            clean[signal] = {"verdict": "unknown", "reasoning": "signal missing from model output"}
            notes.append(f"{signal}: missing -> unknown")
            continue

        verdict = item.get("verdict")
        if verdict not in permitted:
            notes.append(f"{signal}: invalid verdict {verdict!r} -> unknown")
            verdict = "unknown"

        if signal in SIGNALS_NEEDING_URL and verdict != "unknown":
            url = item.get("source_url")
            if not url or url not in allowed:
                # No citation is different from a weak one. A claim whose source
                # is not in the bundle has no evidence at all, so it collapses
                # rather than stepping down a rung and surviving as "suspected".
                notes.append(f"{signal}: {verdict} -> unknown (cited URL not in bundle)")
                verdict = "unknown"
            else:
                if allowed[url] != "resolved" and verdict == "confirmed":
                    verdict = _downgrade(signal, verdict, "url_status not resolved", notes)
                claim = grounded_claims.get(url)
                if claim:
                    if verdict == "ruled_out":
                        verdict = _downgrade(signal, verdict, "grounded_search can never rule out", notes)
                    elif verdict == "confirmed" and not (
                        claim.get("date") and claim.get("url_status") == "resolved"
                    ):
                        verdict = _downgrade(
                            signal, verdict, "grounded claim lacks a date or a resolved URL", notes
                        )

        if signal == "leadership_change" and item.get("is_finance_role"):
            # Companies House never labels a director as finance. Only the role
            # or occupation string may assert it.
            officers = (bundle.get("companies_house") or {}).get("finance_officers") or []
            if item.get("evidence_source") == "companies_house" and not officers:
                item["is_finance_role"] = False
                notes.append("leadership_change: finance role claim dropped, filing does not say so")

        reasoning = " ".join((item.get("reasoning") or "").split()[:20])
        clean[signal] = {**item, "verdict": verdict, "reasoning": reasoning}

    clean["hook"] = _vet_hook(verdicts.get("hook"), clean, notes)
    return clean, notes


MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _hook_cites(hook, value):
    """Does the hook lean on this specific claim?

    Dates are checked by year and month name too, because a hook says
    "ending March 2027" where the verdict says "2027-03".
    """
    if not hook or not value:
        return False
    haystack = squash(hook)
    text = str(value).strip()

    date_match = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if date_match:
        month = MONTH_NAMES[int(date_match.group(2)) - 1]
        return date_match.group(1) in haystack or month in haystack

    return any(len(token) > 3 and token in haystack for token in tokens(text))


def _vet_hook(hook, clean, notes):
    """The hook is the line a rep says out loud, so it may rest only on confirmed
    signals. Anything softer gets laundered into confidence the evidence has not
    earned. A hook citing nothing specific is generic, and allowed."""
    if not isinstance(hook, str) or not hook.strip():
        return None

    for signal, item in clean.items():
        if not isinstance(item, dict) or item.get("verdict") == "confirmed":
            continue
        for field in ("value", "quote"):
            if _hook_cites(hook, item.get(field)):
                notes.append(
                    f"hook: dropped, cites {signal} at '{item.get('verdict')}' not confirmed"
                )
                return None
    return hook


DETERMINISTIC = {"companies_house", "adzuna", "google_news"}


def _rank_key(item):
    """Deterministic source > dated > resolved > recent.

    Citation choice was drifting between identical runs because the judge picked
    freely among equally valid sources. Ranking here makes the preferred citation
    a property of the evidence, not of the sampler.
    """
    source = item.get("evidence_source") or item.get("source_kind") or ""
    months = item.get("months_ago")
    return (
        0 if source in DETERMINISTIC else 1,
        0 if item.get("date") else 1,
        0 if item.get("url_status") == "resolved" else 1,
        months if isinstance(months, int) else 999,
    )


def rank_evidence(items):
    """Return items ordered best-citation-first, each stamped with its rank."""
    ordered = sorted(items, key=_rank_key)
    return [{**item, "rank": index + 1} for index, item in enumerate(ordered)]


def _render(bundle):
    """Trim the bundle to what the judge needs, keeping every status visible."""
    companies_house = bundle.get("companies_house") or {}
    return json.dumps(
        {
            "source_status": {k: v.get("status") for k, v in (bundle.get("sources") or {}).items()},
            "counters": bundle.get("counters"),
            "companies_house": {
                "resolution_confidence": companies_house.get("resolution_confidence"),
                "needs_confirmation": companies_house.get("needs_confirmation"),
                "matched_name": companies_house.get("matched_name"),
                "company_number": companies_house.get("company_number"),
                "source_url": companies_house.get("source_url"),
                "officers_url": companies_house.get("officers_url"),
                "filings_url": companies_house.get("filings_url"),
                "officers": companies_house.get("officers"),
                "finance_officers": companies_house.get("finance_officers"),
                "notable_filings": companies_house.get("notable_filings"),
            },
            "news": rank_evidence([
                {
                    "title": a.get("title"),
                    "source": a.get("source"),
                    "date": a.get("seendate"),
                    "months_ago": a.get("months_ago"),
                    "name_match": a.get("name_match"),
                    "url_status": a.get("url_status"),
                    "evidence_source": "google_news",
                    "source_url": a.get("resolved_url") or a.get("url"),
                }
                for a in (bundle.get("news") or [])
            ]),
            "job_ads": bundle.get("job_ads"),
            "finance_role_counts": bundle.get("finance_role_counts"),
            "grounded_claims": rank_evidence([
                {
                    **{k: v for k, v in claim.items() if k not in ("original_url", "resolved_url")},
                    # Show the publisher URL, not the expiring Vertex token.
                    "source_url": claim.get("resolved_url") or claim.get("source_url"),
                    "evidence_source": "grounded_search",
                }
                for claim in (bundle.get("grounded_claims") or [])
            ]),
        },
        indent=1,
        ensure_ascii=False,
    )


def judge(bundle, today=None):
    """-> {"verdicts": {...}, "notes": [...]}. Raises JudgeError if unusable."""
    today = today or date.today()
    prompt = PROMPT.read_text().format(
        today=today.isoformat(), account=bundle.get("account"), bundle=_render(bundle)
    )
    config = types.GenerateContentConfig(temperature=0, max_output_tokens=8000)

    contents = prompt
    for attempt in (1, 2):
        response = research._generate(research.RESEARCH_MODEL, contents, config)
        try:
            parsed = research.extract_json(response.text or "")
            break
        except Exception:
            if attempt == 2:
                raise JudgeError("judge did not return valid JSON")
            contents = prompt + "\n\nYour last response was not valid JSON. Return only the JSON object."
    else:
        raise JudgeError("judge did not return valid JSON")

    verdicts, notes = validate(parsed, bundle)
    if notes:
        log.info("judge: %s — %d correction(s): %s", bundle.get("account"), len(notes), "; ".join(notes))
    return {"verdicts": verdicts, "notes": notes}
