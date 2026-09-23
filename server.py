"""Starlette backend for the web front end.

Three routes: upload a workbook, stream results as each account completes, download
the amended file. All the real work lives in the modules this imports.
"""

import io
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import cache
import excel_io
import pipeline

STATIC = Path(__file__).parent / "static"

# Single-user localhost tool, so in-process state is enough. No database by design.
SESSIONS: dict[str, dict] = {}


async def index(request):
    return Response((STATIC / "index.html").read_text(), media_type="text/html")


async def upload(request):
    form = await request.form()
    upload_file = form.get("file")
    if upload_file is None:
        return JSONResponse({"error": "No file was uploaded."}, status_code=400)

    raw = await upload_file.read()
    try:
        workbook = excel_io.load(io.BytesIO(raw))
        accounts = excel_io.read_accounts(workbook)
    except excel_io.TemplateError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "That file could not be read as an .xlsx workbook."}, status_code=400)

    token = uuid.uuid4().hex
    SESSIONS[token] = {"workbook": workbook, "accounts": accounts, "output": None}
    return JSONResponse({"session": token, "accounts": accounts, "filename": upload_file.filename})


def _stream_results(session: dict, force_refresh: bool, engine: str = pipeline.LEGACY):
    accounts = session["accounts"]
    results = []
    processor = (
        pipeline.process_account_evidence if engine == pipeline.EVIDENCE else pipeline.process_account
    )

    def frame(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    with ThreadPoolExecutor(max_workers=pipeline.MAX_WORKERS) as pool:
        futures = {pool.submit(processor, a, force_refresh): a for a in accounts}
        for future in as_completed(futures):
            account = futures[future]
            try:
                try:
                    result = future.result()
                except Exception:
                    # One retry before giving up: batch failures have been
                    # transient, and the account succeeds on its own moments later.
                    result = processor(account, force_refresh)
                    result["retried"] = True
                payload = {
                    "row": result["row"],
                    "name": result["name"],
                    "engine": result.get("engine", pipeline.LEGACY),
                    "heat": result["heat"],
                    "analysis": result["analysis"],
                    "reasons": result.get("reasons") or result["scored"]["reasons"],
                    "sources": [
                        s for s in result["findings"].get("sources", [])
                        if isinstance(s, dict) and s.get("url")
                    ],
                    "findings": result["findings"],
                    "cached": result["cached"],
                    "evidence_grade": result.get("evidence_grade"),
                    "position": result.get("position"),
                    "verdicts": result.get("verdicts"),
                    "judge_notes": result.get("judge_notes"),
                    "counters": result.get("counters"),
                    "needs_confirmation": result.get("needs_confirmation", False),
                    "rank_score": (result.get("scored") or {}).get("rank_score"),
                    "scored": result.get("scored"),
                    "hook": result.get("hook"),
                    "observed_at": result.get("observed_at"),
                    "retried": result.get("retried", False),
                }
            except Exception as exc:
                payload = {
                    "row": account["row"],
                    "name": account["name"],
                    "heat": None,
                    "error": str(exc),
                }
            results.append(payload)
            yield frame("result", payload)

    # Success is "the account was processed", not "it has a heat score": the
    # evidence engine has no score until scoring is rewritten against verdicts.
    succeeded = [r for r in results if not r.get("error")]
    scored = [r for r in succeeded if r.get("heat")]
    if scored:
        if engine == pipeline.EVIDENCE:
            excel_io.write_verdict_results(
                session["workbook"],
                {r["row"]: r for r in scored},
            )
        else:
            excel_io.write_results(
                session["workbook"],
                {r["row"]: {"heat": r["heat"], "analysis": r["analysis"]} for r in scored},
            )
        session["output"] = excel_io.to_bytes(session["workbook"])

    yield frame("done", {
        "completed": len(succeeded),
        "failed": len(results) - len(succeeded),
        "scored": len(scored),
    })


async def run(request):
    session = SESSIONS.get(request.path_params["token"])
    if session is None:
        return JSONResponse({"error": "Session expired. Upload the file again."}, status_code=404)

    force_refresh = request.query_params.get("force") == "1"
    # ?engine=evidence runs the new bundle+judge path. Default stays legacy.
    engine = pipeline.EVIDENCE if request.query_params.get("engine") == "evidence" else pipeline.LEGACY
    return StreamingResponse(
        _stream_results(session, force_refresh, engine),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def confirm_company(request):
    """A human settling an ambiguous Companies House match. Permanent."""
    body = await request.json()
    account = (body.get("account") or "").strip()
    company_number = (body.get("company_number") or "").strip()
    if not account or not company_number:
        return JSONResponse({"error": "account and company_number are required."}, status_code=400)
    if not re.fullmatch(r"[A-Za-z0-9]{6,10}", company_number):
        return JSONResponse(
            {"error": "Company number should be 6-10 letters or digits, e.g. 11874049."},
            status_code=400,
        )

    cache.confirm_resolution(account, company_number.upper(), body.get("matched_name"), user="user")
    stored = cache.get_resolution(account)
    return JSONResponse({"confirmed": stored})


async def download(request):
    session = SESSIONS.get(request.path_params["token"])
    if session is None or not session.get("output"):
        return JSONResponse({"error": "Nothing to download yet."}, status_code=404)
    return Response(
        session["output"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="renewal-radar-results.xlsx"'},
    )


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/upload", upload, methods=["POST"]),
        Route("/api/run/{token}", run),
        Route("/api/confirm-company", confirm_company, methods=["POST"]),
        Route("/api/download/{token}", download),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
)


if __name__ == "__main__":
     uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
