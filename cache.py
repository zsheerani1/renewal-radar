"""SQLite findings cache. Keeps repeat runs (and demos) instant."""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "cache.sqlite"


def _connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS findings ("
        "  name TEXT PRIMARY KEY,"
        "  findings_json TEXT NOT NULL,"
        "  researched_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS observations ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  account TEXT NOT NULL,"
        "  observed_at TEXT NOT NULL,"
        "  bundle_json TEXT NOT NULL,"
        "  verdicts_json TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS observations_account ON observations (account, id DESC)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS url_resolutions ("
        "  original_url TEXT PRIMARY KEY,"
        "  resolved_url TEXT,"
        "  url_status TEXT NOT NULL,"
        "  http_status INTEGER,"
        "  resolved_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS resolutions ("
        "  account TEXT PRIMARY KEY,"
        "  company_number TEXT,"
        "  matched_name TEXT,"
        "  confidence TEXT NOT NULL,"
        "  confirmed_by TEXT,"
        "  resolved_at TEXT NOT NULL)"
    )
    return conn


def get(name: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT findings_json FROM findings WHERE name = ?", (name,)).fetchone()
    return json.loads(row[0]) if row else None


def put(name: str, findings: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO findings (name, findings_json, researched_at) VALUES (?, ?, ?)",
            (name, json.dumps(findings), findings.get("researched_at", "")),
        )


def clear() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM findings")


def get_resolution(account: str) -> dict | None:
    """The stored Companies House entity for an account, if one was ever settled."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT company_number, matched_name, confidence, confirmed_by, resolved_at"
            " FROM resolutions WHERE account = ?",
            (account.strip().lower(),),
        ).fetchone()
    if not row:
        return None
    return dict(zip(("company_number", "matched_name", "confidence", "confirmed_by", "resolved_at"), row))


def put_resolution(account, company_number, matched_name, confidence, confirmed_by=None) -> None:
    """Settle an account's entity so it is never re-resolved.

    Only confident automatic matches are stored. An unresolved account is left
    unstored on purpose, so a user correction or a better scorer can still fix it.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO resolutions"
            " (account, company_number, matched_name, confidence, confirmed_by, resolved_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                account.strip().lower(),
                company_number,
                matched_name,
                confidence,
                confirmed_by,
                date.today().isoformat(),
            ),
        )


def get_url_resolution(url: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT resolved_url, url_status, http_status FROM url_resolutions WHERE original_url = ?",
            (url,),
        ).fetchone()
    if not row:
        return None
    return dict(zip(("resolved_url", "url_status", "http_status"), row))


def put_url_resolution(url, resolved_url, url_status, http_status) -> None:
    """Resolving a Google News link costs two round trips. Only ever do it once."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO url_resolutions"
            " (original_url, resolved_url, url_status, http_status, resolved_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (url, resolved_url, url_status, http_status, date.today().isoformat()),
        )


def confirm_resolution(account, company_number, matched_name=None, user="user") -> None:
    """A human correction. Permanent, and outranks anything the scorer decides."""
    put_resolution(account, company_number, matched_name, "high", confirmed_by=user)


# --- observations: append-only evidence history ------------------------------

def add_observation(account: str, bundle: dict, verdicts: dict) -> str:
    """Record what was found this run. Never updates — re-runs build history.

    Storing the bundle and the verdicts rather than the score means a later
    change to the scoring rules re-scores past observations instead of
    invalidating them.
    """
    observed_at = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO observations (account, observed_at, bundle_json, verdicts_json)"
            " VALUES (?, ?, ?, ?)",
            (account.strip().lower(), observed_at, json.dumps(bundle, default=str),
             json.dumps(verdicts, default=str)),
        )
    return observed_at


def latest_observation(account: str) -> dict | None:
    """Most recent stored evidence for an account, or None if never observed."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT observed_at, bundle_json, verdicts_json FROM observations"
            " WHERE account = ? ORDER BY id DESC LIMIT 1",
            (account.strip().lower(),),
        ).fetchone()
    if not row:
        return None
    return {
        "observed_at": row[0],
        "bundle": json.loads(row[1]),
        "verdicts": json.loads(row[2]),
    }


def observation_history(account: str, limit: int = 50) -> list[dict]:
    """Observed_at timestamps for an account, newest first, for trend."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, observed_at FROM observations WHERE account = ?"
            " ORDER BY id DESC LIMIT ?",
            (account.strip().lower(), limit),
        ).fetchall()
    return [{"id": r[0], "observed_at": r[1]} for r in rows]
