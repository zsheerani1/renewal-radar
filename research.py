"""Gemini calls, JSON extraction, schema validation.

Two calls per account: a grounded research call that returns Findings JSON, and a
cheap ungrounded call that writes the Analysis paragraph. Keeping them separate
stops prose quality and research accuracy contaminating each other.
"""

import json
import os
import threading
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

load_dotenv()

PROMPTS = Path(__file__).parent / "prompts"
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "gemini-flash-latest")
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "gemini-flash-latest")

REQUIRED_FIELDS = {
    "current_system": (str, type(None)),
    "system_confidence": (str,),
    "system_evidence": (str, type(None)),
    "maintenance_expiry": (str, type(None)),
    "expiry_basis": (str,),
    "expiry_evidence": (str, type(None)),
    "change_signal": (bool,),
    "change_summary": (str, type(None)),
    "change_urgency_stated": (bool,),
    "competitor_live": (bool,),
    "competitor_troubled": (bool,),
    "competitor_summary": (str, type(None)),
    "sources": (list,),
}


class ResearchError(Exception):
    pass


_client_lock = threading.Lock()
_client_instance = None


def _client():
    """Exactly one Client, ever.

    Worker threads racing to build their own means the losers get garbage-collected,
    and their teardown closes transport state the surviving client is still using.
    """
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            key = os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise ResearchError(
                    "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key."
                )
            _client_instance = genai.Client(api_key=key)
    return _client_instance


def _generate(model: str, contents, config):
    """Call Gemini, turning the SDK's wall-of-JSON errors into one readable line."""
    try:
        return _client().models.generate_content(model=model, contents=contents, config=config)
    except genai_errors.ClientError as exc:
        code = getattr(exc, "code", None)
        if code == 429:
            raise ResearchError(
                "Gemini quota exceeded. Grounded web search requires a billed API key — "
                "enable billing in Google AI Studio, then try again."
            ) from exc
        if code == 404:
            raise ResearchError(f"Model '{model}' is not available to this API key.") from exc
        if code in (401, 403):
            raise ResearchError("Gemini rejected the API key. Check GOOGLE_API_KEY in .env.") from exc
        raise ResearchError(f"Gemini API error {code}.") from exc


def extract_json(text: str) -> dict:
    if not text:
        raise ResearchError("Model returned an empty response.")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ResearchError(f"No JSON object found in response: {text[:200]}")
    return json.loads(text[start : end + 1])


def validate(findings: dict) -> dict:
    """Normalise and check a Findings dict before it reaches the scorer."""
    for field, allowed in REQUIRED_FIELDS.items():
        if field not in findings:
            raise ResearchError(f"Findings missing required field '{field}'")
        if not isinstance(findings[field], allowed):
            raise ResearchError(f"Field '{field}' has type {type(findings[field]).__name__}")

    expiry = findings["maintenance_expiry"]
    if isinstance(expiry, str):
        cleaned = expiry.strip()
        if cleaned.lower() in {"", "null", "none", "unknown"}:
            findings["maintenance_expiry"] = None
        else:
            parts = cleaned.split("-")
            if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                raise ResearchError(f"maintenance_expiry is not YYYY-MM: {expiry!r}")
            findings["maintenance_expiry"] = f"{int(parts[0]):04d}-{int(parts[1]):02d}"

    # An uncited claim is a hallucination risk, so demote it rather than trust it.
    if not findings["sources"]:
        findings["change_signal"] = False
        findings["change_urgency_stated"] = False
        findings["competitor_troubled"] = False
        findings["maintenance_expiry"] = None

    return findings


def _grounding_sources(response) -> list[dict]:
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
    except (AttributeError, IndexError):
        return []
    return [
        {"title": c.web.title, "url": c.web.uri}
        for c in chunks
        if getattr(c, "web", None) and c.web.uri
    ]


def research_account(name: str, email: str | None = None, today: date | None = None) -> dict:
    today = (today or date.today()).isoformat()
    disambiguator = ""
    if email and "@" in str(email):
        domain = str(email).split("@")[-1].strip()
        disambiguator = f"The account's contact email domain is {domain} — use it to confirm you have the right entity."

    prompt = (PROMPTS / "research.txt").read_text().format(
        today=today, account=name, disambiguator=disambiguator
    )
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0,
    )

    contents = prompt
    last_error = None
    for attempt in range(2):
        response = _generate(RESEARCH_MODEL, contents, config)
        try:
            findings = validate(extract_json(response.text))
        except (ResearchError, json.JSONDecodeError) as exc:
            last_error = exc
            contents = (
                prompt
                + "\n\nYour last response was not valid JSON matching the schema. "
                + "Return only the JSON object."
            )
            continue

        findings["account"] = name
        findings["researched_at"] = today
        grounded = _grounding_sources(response)
        if grounded:
            known = {s.get("url") for s in findings["sources"] if isinstance(s, dict)}
            findings["sources"] += [s for s in grounded if s["url"] not in known]
        return findings

    raise ResearchError(f"Research failed for {name}: {last_error}")


def write_analysis(findings: dict, scored: dict) -> str:
    prompt = (PROMPTS / "analysis.txt").read_text().format(
        account=findings.get("account", ""),
        heat=scored["heat"],
        findings=json.dumps(findings, indent=2),
        reasons="\n".join(f"- {r}" for r in scored["reasons"]),
    )
    response = _generate(
        ANALYSIS_MODEL,
        prompt,
        types.GenerateContentConfig(temperature=0.3, max_output_tokens=4000),
    )
    return (response.text or "").strip()
