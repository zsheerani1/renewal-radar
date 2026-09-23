"""Workbook read/write. openpyxl only — pandas would strip the template's formatting."""

import io
import math
from copy import copy

from openpyxl import load_workbook
from openpyxl.styles import Alignment, PatternFill
from openpyxl.utils import get_column_letter

SHEET = "Sheet1"
HEADERS = ["Client Name", "Client Contact", "Contact Email", "Heat Score", "Analysis"]

HEAT_FILLS = {
    1: "BDD7EE",
    2: "DDEBF7",
    3: "FFF2CC",
    4: "FCE4D6",
    5: "F8CBAD",
}


class TemplateError(Exception):
    pass


def load(source):
    wb = load_workbook(source)
    if SHEET not in wb.sheetnames:
        raise TemplateError(f"Workbook has no sheet named '{SHEET}'. Found: {', '.join(wb.sheetnames)}")
    ws = wb[SHEET]
    # A completed sheet carries the verdict columns, so accept it too: re-running
    # last week's output is a normal thing to do.
    row = [(ws.cell(1, i + 1).value or "").strip() for i in range(len(VERDICT_HEADERS))]
    if row[: len(HEADERS)] == HEADERS or row == VERDICT_HEADERS:
        return wb
    raise TemplateError(
        f"Row 1 headers must start with {HEADERS}. Found: {row[: len(HEADERS)]}"
    )


def read_accounts(wb) -> list[dict]:
    ws = wb[SHEET]
    accounts = []
    for row in range(2, ws.max_row + 1):
        name = ws.cell(row, 1).value
        if not name or not str(name).strip():
            continue
        accounts.append(
            {
                "row": row,
                "name": str(name).strip(),
                "contact": ws.cell(row, 2).value,
                "email": ws.cell(row, 3).value,
            }
        )
    if not accounts:
        raise TemplateError("Column A contains no client names.")
    return accounts


def write_results(wb, results: dict[int, dict]) -> None:
    """results maps row number -> {'heat': int, 'analysis': str}."""
    ws = wb[SHEET]
    for row, result in results.items():
        heat_cell = ws.cell(row, 4)
        heat_cell.value = result["heat"]
        heat_cell.alignment = Alignment(horizontal="center")
        fill = HEAT_FILLS.get(result["heat"])
        if fill:
            heat_cell.fill = PatternFill("solid", start_color=fill, end_color=fill)
        analysis_cell = ws.cell(row, 5)
        analysis_cell.value = result["analysis"]
        analysis_cell.alignment = Alignment(wrap_text=True, vertical="top")


def to_bytes(wb) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# --- evidence engine output -------------------------------------------------

# Analysis stays where the template had it so the file remains recognisable;
# the verdict columns are appended after it.
VERDICT_HEADERS = [
    "Client Name", "Client Contact", "Contact Email", "Heat Score", "Evidence Grade",
    "Position", "System In Use", "System Verdict", "Competitor", "Competitor Verdict",
    "Expiry", "Expiry Verdict", "Leadership Change", "Change Date", "Restructuring",
    "Hiring Signal", "Replacement Intent", "Hook", "Analysis", "Sources",
]

CONFIRMED_FILL = "D5E8D4"   # green
PARTIAL_FILL = "FFF0C1"     # amber - likely / suspected / inferred
UNKNOWN_FILL = "F2F2F2"     # grey, and the cell is left empty
INSUFFICIENT_FILL = "DEE6F5"  # blue-grey, deliberately unlike "normal"

PARTIAL_VERDICTS = {"likely", "suspected", "inferred"}
GRADE_FILLS = {"A": "D5E8D4", "B": "FFF0C1", "C": "F2F2F2"}

COLUMN_WIDTHS = {
    1: 30, 2: 18, 3: 26, 4: 11, 5: 14, 6: 16, 7: 22, 8: 14, 9: 20, 10: 17,
    11: 12, 12: 14, 13: 46, 14: 13, 15: 46, 16: 16, 17: 18, 18: 42, 19: 60, 20: 52,
}


MAX_CELL_TEXT = 120


def _truncate(text, limit=MAX_CELL_TEXT):
    """Keep rows readable. The full text stays in the UI expand view."""
    if not text:
        return text
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _one_decimal(value):
    """Half-up to one place: round(0.25, 1) is 0.2 in Python, which hides the gap."""
    return math.floor(value * 10 + 0.5) / 10


def _verdict_cell(ws, row, column, verdict, text=None):
    """Colour by confidence. Unknown renders visually empty: a grey blank says
    'nothing established' without the word 'unknown' filling the sheet."""
    cell = ws.cell(row, column)
    cell.alignment = Alignment(vertical="top", wrap_text=True)

    if verdict == "confirmed":
        cell.value = text if text is not None else "confirmed"
        fill = CONFIRMED_FILL
    elif verdict in PARTIAL_VERDICTS:
        cell.value = text if text is not None else verdict
        fill = PARTIAL_FILL
    elif verdict == "insufficient_data":
        cell.value = text if text is not None else "insufficient data"
        fill = INSUFFICIENT_FILL
    elif verdict in ("elevated", "normal"):
        cell.value = text if text is not None else verdict
        fill = CONFIRMED_FILL if verdict == "elevated" else None
    else:
        cell.value = None
        fill = UNKNOWN_FILL

    if fill:
        cell.fill = PatternFill("solid", start_color=fill, end_color=fill)
    return cell


def _value_or_blank(item):
    """Only show a value when the verdict earned one."""
    verdict = (item or {}).get("verdict")
    if verdict in ("unknown", None):
        return None
    return (item or {}).get("value")


def ensure_verdict_headers(wb) -> None:
    ws = wb[SHEET]
    template_header = ws.cell(1, 1)
    for index, title in enumerate(VERDICT_HEADERS, start=1):
        cell = ws.cell(1, index)
        if cell.value != title:
            cell.value = title
            # Match whatever the template already used for its own headers.
            cell.font = copy(template_header.font)
            cell.fill = copy(template_header.fill)
            cell.alignment = Alignment(horizontal="left", vertical="bottom", wrap_text=True)
    for column, width in COLUMN_WIDTHS.items():
        ws.column_dimensions[get_column_letter(column)].width = width
    ws.freeze_panes = "B2"


def write_verdict_results(wb, results: dict[int, dict]) -> None:
    """results maps row -> {'scored':..., 'verdicts':..., 'analysis':..., 'sources':[...]}"""
    ensure_verdict_headers(wb)
    ws = wb[SHEET]

    for row, result in results.items():
        scored = result.get("scored") or {}
        verdicts = result.get("verdicts") or {}

        heat = scored.get("heat")
        rank = scored.get("rank_score")
        heat_cell = ws.cell(row, 4)
        # Below 2 the integer badge cannot separate a real lead from an unread
        # account, so show one decimal there. Still numeric, still sortable.
        heat_cell.value = _one_decimal(rank) if (rank is not None and rank < 2) else heat
        heat_cell.alignment = Alignment(horizontal="center")
        fill = HEAT_FILLS.get(heat)
        if fill:
            heat_cell.fill = PatternFill("solid", start_color=fill, end_color=fill)

        grade = scored.get("evidence_grade")
        grade_cell = ws.cell(row, 5)
        grade_cell.value = grade
        grade_cell.alignment = Alignment(horizontal="center")
        if grade in GRADE_FILLS:
            grade_cell.fill = PatternFill("solid", start_color=GRADE_FILLS[grade],
                                          end_color=GRADE_FILLS[grade])

        ws.cell(row, 6).value = scored.get("position")

        system = verdicts.get("system_in_use") or {}
        ws.cell(row, 7).value = _value_or_blank(system)
        _verdict_cell(ws, row, 8, system.get("verdict"))

        competitor = verdicts.get("competitor_in_place") or {}
        ws.cell(row, 9).value = _value_or_blank(competitor)
        _verdict_cell(ws, row, 10, competitor.get("verdict"))

        expiry = verdicts.get("maintenance_expiry") or {}
        ws.cell(row, 11).value = _value_or_blank(expiry)
        _verdict_cell(ws, row, 12, expiry.get("verdict"))

        leadership = verdicts.get("leadership_change") or {}
        _verdict_cell(ws, row, 13, leadership.get("verdict"), _truncate(_value_or_blank(leadership)))
        ws.cell(row, 14).value = leadership.get("event_date") if leadership.get("verdict") == "confirmed" else None

        restructuring = verdicts.get("restructuring") or {}
        _verdict_cell(ws, row, 15, restructuring.get("verdict"), _truncate(_value_or_blank(restructuring)))

        hiring = verdicts.get("hiring_signal") or {}
        _verdict_cell(ws, row, 16, hiring.get("verdict"))

        intent = verdicts.get("stated_replacement_intent") or {}
        _verdict_cell(ws, row, 17, intent.get("verdict"), _truncate(intent.get("quote")))

        hook_cell = ws.cell(row, 18)
        hook_cell.value = result.get("hook") or verdicts.get("hook")
        hook_cell.alignment = Alignment(wrap_text=True, vertical="top")

        analysis_cell = ws.cell(row, 19)
        analysis_cell.value = result.get("analysis")
        analysis_cell.alignment = Alignment(wrap_text=True, vertical="top")

        sources_cell = ws.cell(row, 20)
        urls = []
        for source in result.get("sources") or []:
            url = source.get("url") if isinstance(source, dict) else source
            if url and url not in urls:
                urls.append(url)
        sources_cell.value = "\n".join(urls) or None
        sources_cell.alignment = Alignment(wrap_text=True, vertical="top")

        # Without this the account name sits at the bottom of a tall row while
        # its verdicts sit at the top, and the row cannot be read across.
        for column in range(1, len(VERDICT_HEADERS) + 1):
            cell = ws.cell(row, column)
            existing = cell.alignment
            cell.alignment = Alignment(
                horizontal=existing.horizontal,
                vertical="top",
                wrap_text=existing.wrap_text,
            )
