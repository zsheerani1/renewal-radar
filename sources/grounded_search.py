"""Grounded Gemini search, demoted to a source.

It returns claims with URLs like any other source. It does not judge, and
downstream it is weaker evidence than a filing or a dated job ad: it may support
likely/suspected, may support confirmed only with a specific dated claim and a
resolvable URL, and may never support ruled_out.

Reuses research.py's client rather than building its own — two clients racing to
exist is what closed the transport mid-request once already.
"""

import logging
from datetime import date
from pathlib import Path

from google.genai import types

import research
from . import _urls
from ._result import ERROR, OK, RATE_LIMITED, SourceResult

log = logging.getLogger(__name__)

PROMPT = Path(__file__).parent.parent / "prompts" / "grounded_claims.txt"
MAX_SEARCHES = 6
RESOLVE_LIMIT = 10

CLAIM_TYPES = {
    "system_in_use",
    "competitor",
    "maintenance_expiry",
    "leadership_change",
    "restructuring",
    "replacement_intent",
    "other",
}


def _valid(claim):
    """A claim without a real URL is not evidence, whatever it says."""
    if not isinstance(claim, dict):
        return False
    url = (claim.get("source_url") or "").strip()
    return url.startswith(("http://", "https://")) and bool((claim.get("statement") or "").strip())


def find_claims(company, domain=None, today=None):
    today = today or date.today()
    domain_hint = f"Email domain of the contact: {domain} — use it to confirm you have the right company." if domain else ""
    prompt = PROMPT.read_text().format(
        today=today.isoformat(), account=company, domain_hint=domain_hint
    )

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0,
        max_output_tokens=8000,
    )

    try:
        response = research._generate(research.RESEARCH_MODEL, prompt, config)
    except research.ResearchError as exc:
        message = str(exc)
        status = RATE_LIMITED if "quota" in message.lower() else ERROR
        return SourceResult(status=status, data=[], detail=message)

    try:
        parsed = research.extract_json(response.text or "")
    except Exception as exc:
        log.warning("grounded_search: could not parse response (%s)", type(exc).__name__)
        return SourceResult(status=ERROR, data=[], detail="unparseable JSON")

    claims = []
    for claim in parsed.get("claims") or []:
        if not _valid(claim):
            continue
        claim_type = claim.get("claim_type")
        claims.append(
            {
                "claim_type": claim_type if claim_type in CLAIM_TYPES else "other",
                "statement": claim.get("statement"),
                "value": claim.get("value"),
                "date": claim.get("date"),
                "source_url": claim.get("source_url"),
                "source_title": claim.get("source_title"),
                "source": "grounded_search",
            }
        )

    # Vertex hands back expiring redirect tokens, so resolve before storing.
    _urls.annotate(claims, url_field="source_url", limit=RESOLVE_LIMIT)

    dropped = len(parsed.get("claims") or []) - len(claims)
    if dropped:
        log.info("grounded_search: dropped %d claim(s) with no usable source URL", dropped)

    resolved = sum(1 for c in claims if c.get("url_status") == _urls.RESOLVED)
    return SourceResult(
        status=OK,
        data=claims,
        detail=f"{dropped} claim(s) dropped for missing URL",
        meta={"claims": len(claims), "urls_resolved": resolved, "claims_dropped": dropped},
    )
