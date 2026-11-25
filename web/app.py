"""Local job-hunter web UI: the run pipeline, the report archive, applications, sources.

Listens on 127.0.0.1 only: reports contain mail, applications and contacts, and they must not leave the machine.
Run: .venv\\Scripts\\python.exe -m web  (opens the browser; API docs — /docs).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from jobhunter import (
    config,
    cover_letter,
    coverage,
    cv_base,
    cv_profile,
    cvgen,
    industry,
    interview_prep,
    manual_apps,
    market_check,
    progress,
    relevance,
    vacancy_lookup,
)
from jobhunter.models import norm_name
from web import cache
from web.models import (
    ApplicationEvent,
    BuiltCv,
    CompaniesReport,
    CoverRequest,
    CvExperience,
    CvExperienceUpdate,
    CvOther,
    CvOtherUpdate,
    CvRequest,
    CvResult,
    CvSummary,
    CvSummaryUpdate,
    MarketReport,
    NewSkill,
    Profile,
    ProfileUpdate,
    Report,
    RunRequest,
    RunStarted,
    SkillChange,
    SkillsReport,
    SkipRequest,
    SourcesReport,
    TopicsRequest,
    TopicsSuggestion,
    errors,
)
from web.runner import Runner, daily_scheduler

ROOT = config.ROOT
WEB = Path(__file__).parent
REPORTS = config.REPORTS_DIR
SOURCES_FILE = ROOT / "state" / "sources_latest.json"
FREEHIRE_FILE = ROOT / "state" / "freehire_latest.json"
RUN_LOG = ROOT / "last_run.log"
_REPORT_RX = re.compile(r"^jobs_(\d{4}-\d{2}-\d{2})\.html$")

app = FastAPI(
    title="job-hunter",
    description="Local UI for the job search script: run progress, reports, applications, sources. "
                "The script sends nothing out; the server listens on 127.0.0.1 only.",
    version="1.0",
)
templates = Jinja2Templates(directory=WEB / "templates")


class SafeJSONResponse(JSONResponse):
    """JSON that does not fail on lone surrogates: a normal response goes as is (readable non-ASCII text),
    and if a string cannot be encoded to UTF-8 — the same data with \\uXXXX escaping."""

    def render(self, content: Any) -> bytes:
        try:
            return super().render(content)
        except UnicodeEncodeError:
            return json.dumps(content, ensure_ascii=True, separators=(",", ":")).encode("ascii")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 like FastAPI's, but the errors echo the input: "\\ud800" in the body gave a 500 on serializing the response."""
    return SafeJSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)


@app.exception_handler(405)
async def method_not_allowed(request: Request, exc: Exception) -> JSONResponse:
    """405 with the full Allow. Starlette takes Allow from the first route with this path, and on /api/cv and
    /api/skills GET and POST are different routes: OPTIONS /api/cv answered "Allow: POST" though GET exists
    (found by schemathesis)."""
    from starlette.routing import Match, Route

    methods = sorted({m for r in app.routes if isinstance(r, Route) and r.methods
                      and r.matches(request.scope)[0] != Match.NONE for m in r.methods})
    return JSONResponse({"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": ", ".join(methods)})


app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
REPORTS.mkdir(exist_ok=True)
(ROOT / "cv_out").mkdir(exist_ok=True)
# reports refer to CVs as ../cv_out/…, so both folders sit side by side under /files
app.mount("/files/reports", StaticFiles(directory=REPORTS), name="reports")
app.mount("/files/cv_out", StaticFiles(directory=ROOT / "cv_out"), name="cv_out")


@app.middleware("http")
async def no_cache_reports(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Reports are rebuilt under the same name (applications.html after every letter).

    Without Cache-Control the browser decides how long to keep a copy, and the "Applications" iframe
    showed a stale page. no-cache means an ETag check every time: an unchanged file is a short 304.
    """
    response = await call_next(request)
    if request.url.path.startswith("/files/reports/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# (key, label, icon) — the icon is visible in the collapsed left panel too
# (address, label, emoji icon; the same icons as the report buttons: 📬 applications, 📄 CV, 🎤 prep)
NAV = [("pipeline", "Pipeline", "⚙️"), ("reports", "Reports", "📋"), ("applications", "Applications", "📬"),
       ("progress", "Progress", "🏆"), ("profile", "Profile", "👤"),
       ("cv", "CV", "📄"), ("cover", "Cover letter", "✉️"), ("prep", "Interview prep", "🎤"), ("market", "CV vs market", "⚖️"), ("companies", "Companies", "🏢"),
       ("skills", "Skills", "🛠️"), ("sources", "Sources", "🌐")]


# --- starting a run ---------------------------------------------------------------------------

# after the queue — warm_caches: a new report, recompute "Market" and "Skills" before anyone opens them
runner = Runner(ROOT, RUN_LOG, on_finish=lambda: warm_caches())

SCHEDULER_FILE = ROOT / "state" / "scheduler.json"  # date of the last scheduled run

if os.environ.get("JOBHUNTER_DAILY_AT"):
    threading.Thread(target=daily_scheduler, args=(runner, os.environ["JOBHUNTER_DAILY_AT"], SCHEDULER_FILE),
                     daemon=True).start()


# --- data for the pages ------------------------------------------------------------------------

def progress_payload() -> dict[str, Any] | None:
    """progress.json + whether the process is alive + ETA of the whole run + the script queue."""
    data = progress.read()
    if data is None:
        return None
    if data["status"] == progress.RUNNING and not progress.is_alive(data):
        data["status"] = "interrupted"
    data["eta_total"] = progress.eta_total(data)
    data["queue"] = list(runner.queue)
    data["busy"] = runner.busy()
    return data


def report_list() -> list[dict[str, Any]]:
    """Reports by date, newest first: date, file, size, whether there is a Markdown version."""
    rows: list[dict[str, Any]] = []
    for path in REPORTS.glob("jobs_*.html"):
        m = _REPORT_RX.match(path.name)
        if m:
            stat = path.stat()
            rows.append({"date": m.group(1), "file": path.name, "size_kb": stat.st_size // 1024,
                         "built": datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M"),
                         "md": (REPORTS / path.name.replace(".html", ".md")).exists()})
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def sources_payload() -> dict[str, Any]:
    """Sources of the last run + Telegram channels + freehire queries from the settings."""
    try:
        sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sources = {}
    built = datetime.fromtimestamp(SOURCES_FILE.stat().st_mtime) if SOURCES_FILE.exists() else None
    from jobhunter import geo
    gaps = coverage.gaps(sources)
    why = {g["name"]: g for g in gaps["rows"]}  # why a source gave no vacancies to the report
    rows = [{"name": k, **v, **{f: why.get(k, {}).get(f, "") for f in ("reason", "detail", "checked")}}
            for k, v in sources.items()]
    rows.sort(key=lambda r: (-r["jobs"], r["name"].lower()))
    kinds: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = kinds.setdefault(r["kind"], {"sources": 0, "jobs": 0, "empty": 0, "errors": 0})
        k["sources"] += 1
        k["jobs"] += r["jobs"]
        k["empty"] += r["jobs"] == 0 and not r.get("error")
        k["errors"] += bool(r.get("error"))
    return {
        "built": built.strftime("%d.%m %H:%M") if built else "",
        "rows": rows, "kinds": kinds,
        "telegram": list(config.TELEGRAM_CHANNELS) if config.TELEGRAM_ENABLED else [],
        "freehire": freehire_queries(geo.S.get("freehire", {})),
        "gaps": gaps,
    }


def freehire_queries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """freehire queries: from state/freehire_latest.json, and before the first run — from settings.toml without results."""
    try:
        queries: list[dict[str, Any]] = json.loads(FREEHIRE_FILE.read_text(encoding="utf-8"))
        return queries
    except (OSError, ValueError):
        skip = ("name", "title")
        return [{"name": q["name"], "title": q.get("title", q["name"]), "total": None, "fetched": None,
                 "params": {k: v for k, v in q.items() if k not in skip}, "skipped": False}
                for q in settings.get("queries", [])]


def recent_cvs(limit: int = 40) -> list[dict[str, Any]]:
    """Built CVs (PDFs in cv_out/), newest first."""
    rows: list[dict[str, Any]] = []
    for pdf in (ROOT / "cv_out").rglob("*.pdf"):
        stat = pdf.stat()
        rel = pdf.relative_to(ROOT).as_posix()
        md = pdf.with_suffix(".md")
        # the first line of the .md: "<!-- vacancy: Company — Role — https://…" (cvgen.build_markdown)
        head = md.read_text(encoding="utf-8")[:600] if md.exists() else ""
        m = re.search(r"<!-- vacancy: (.+?) — (.+?)(?: — (https?://\S+))?\s*$", head, re.M)
        rows.append({"name": f"{m.group(1)} — {m.group(2)}" if m else pdf.stem.replace("_", " "),
                     "vacancy": (m.group(3) or "") if m else "", "pdf": f"/files/{rel}",
                     "md": f"/files/{md.relative_to(ROOT).as_posix()}" if md.exists() else "",
                     "one_page": pdf.stem.endswith("_1p"), "mtime": stat.st_mtime,
                     "built": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m %H:%M")})
    return sorted(rows, key=lambda r: -r["mtime"])[:limit]


def _static_version() -> str:
    """A ?v= mark for CSS / JS: the time of the last edit in web/static — the browser does not keep old styles cached."""
    return str(int(max((f.stat().st_mtime for f in (WEB / "static").rglob("*.*")), default=0)))


def page(request: Request, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, f"{name}.html",
                                      {"nav": NAV, "active": name, "v": _static_version(), **context})


# --- pages -------------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/pipeline")


@app.get("/pipeline", response_class=HTMLResponse, include_in_schema=False)
def pipeline_page(request: Request) -> HTMLResponse:
    return page(request, "pipeline")


@app.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def reports_page(request: Request, file: str = "") -> HTMLResponse:
    reports = report_list()
    if file and not any(r["file"] == file for r in reports):
        raise HTTPException(404, "No such report")
    current = file or (reports[0]["file"] if reports else "")
    return page(request, "reports", reports=reports, current=current)


@app.get("/applications", response_class=HTMLResponse, include_in_schema=False)
def applications_page(request: Request, company: str = "", title: str = "", url: str = "", kind: str = "") -> HTMLResponse:
    exists = (REPORTS / "applications.html").exists()
    prefill = {"company": company, "title": title, "url": url, "kind": kind or ("applied" if url else "")}
    return page(request, "applications", exists=exists, prefill=prefill, kinds=manual_apps.KINDS,
                companies=application_companies())


@app.get("/progress", response_class=HTMLResponse, include_in_schema=False)
def progress_page(request: Request) -> HTMLResponse:
    """Rank, badges and activity — reports/progress.html, built together with the Applications page."""
    return page(request, "progress", exists=(REPORTS / "progress.html").exists())


@app.get("/cv", response_class=HTMLResponse, include_in_schema=False)
def cv_page(request: Request) -> HTMLResponse:
    return page(request, "cv", cvs=recent_cvs())


@app.get("/cover", response_class=HTMLResponse, include_in_schema=False)
def cover_page(request: Request) -> HTMLResponse:
    return page(request, "cover")


def market_report(day: str = "") -> dict[str, Any]:
    """market_check.analyze + conclusions; recomputed (~10 s) only if the day's vacancies or the CV changed."""
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    path = next((f for f in files if f.stem == day), files[-1] if files else None)
    cv_files = [config.CV_BASE_FILE, *(Path(p) for p in market_check.geo.S.get("cv", {}).get("files", []))]
    key = [path and path.name, path and path.stat().st_mtime, *(cache.mtime(f) for f in cv_files)]

    def compute() -> dict[str, Any]:
        report: dict[str, Any] = market_check.analyze(path.stem if path else "")
        report["advice"] = market_check.advice(report)
        return report

    # a report for another day is opened rarely — it replaces the last one; the last one has the "market" key
    report: dict[str, Any] = cache.cached("market" if path == (files[-1] if files else None) else "market:other", key, compute)
    return report


def companies_report() -> dict[str, Any]:
    """Companies by industry (industry.companies_overview) over all archive days; vacancies without repeats.

    Recomputed only when the archive files, companies.txt, applications (the mail cache,
    applications_manual.toml) or the industry rules change; board links from ats_mapping.json — on every request.
    """
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    # a run rewrites ats_mapping.json all the time — board links are added per request, not cached
    deps = [config.COMPANIES_FILE, config.SETTINGS_FILE, config.MAIL_CACHE_FILE, config.MANUAL_APPLICATIONS_FILE,
            Path(industry.__file__)]
    key = [[f.name, f.stat().st_mtime] for f in files] + [cache.mtime(f) for f in deps]

    def compute() -> dict[str, Any]:
        from jobhunter.applications_report import StatusIndex

        unique: dict[str, dict[str, Any]] = {}
        for f in files:
            for job in json.loads(f.read_text(encoding="utf-8")):
                job["day"] = f.stem
                unique[job.get("url") or f"{job.get('company')}|{job.get('title')}"] = job
        for job in unique.values():
            job["industries"] = job.get("industries") or industry.classify(
                job.get("company", ""), job.get("title", ""), job.get("text", ""))
        try:
            index: StatusIndex | None = StatusIndex()
        except Exception:  # a broken mail cache — the summary is useful without statuses too
            index = None

        def status(name: str) -> str:
            return index.lookup(name) if index else ""
        companies = industry.companies_overview(unique.values(), status, {})
        counts = {ind: 0 for ind in [*industry.INDUSTRIES, "other"]}
        for c in companies:
            for ind in c["industries"]:
                counts[ind] += 1
        return {"companies": companies, "counts": counts, "labels": industry.LABELS, "vacancies": len(unique),
                "market": industry.market_by_industry(unique.values())}

    data: dict[str, Any] = cache.cached("companies_by_industry", key, compute)
    try:
        mapping = json.loads(config.MAPPING_FILE.read_text(encoding="utf-8")) if config.MAPPING_FILE.exists() else {}
    except ValueError:  # a run is writing the file right now
        mapping = {}
    boards = industry.board_links(mapping)
    for c in data["companies"]:
        c["board"] = boards.get(norm_name(c["name"]), "")
    return data


def application_companies() -> list[str]:
    """Company hints in the Applications form (parsing all letters takes ~1 s) — until letters or applications change."""
    key = [cache.mtime(config.MANUAL_APPLICATIONS_FILE), cache.mtime(config.MAIL_CACHE_FILE)]
    companies: list[str] = cache.cached("companies", key, manual_apps.companies)
    return companies


def warm_caches() -> None:
    """Computes "Market", "Skills" and the Applications hints in advance — in the background at server start and after a run."""
    for fn in (application_companies, market_report, companies_report, skills_payload):
        try:
            fn()
        except Exception as e:  # warming up is not essential: the page computes it itself and shows the error
            print(f"cache warm-up {fn.__name__}: {e}", file=sys.stderr)


@app.get("/market", response_class=HTMLResponse, include_in_schema=False)
def market_page(request: Request, day: str = "") -> HTMLResponse:
    return page(request, "market", r=market_report(day))


@app.get("/api/market", response_model=MarketReport, summary="CV vs market",
         description="A local ATS checker: over the fitting vacancies of a report (state/jobs/<date>.json) — which "
                     "technologies are required and where they are in the CV (pdf / base only / nowhere), what the "
                     "vacancies are called and whether the CV headline matches, experience bullet hygiene and conclusions.")
def api_market(day: str = "") -> dict[str, Any]:
    return market_report(day)


@app.get("/companies", response_class=HTMLResponse, include_in_schema=False)
def companies_page(request: Request, industry: str = "", empty: bool = True) -> HTMLResponse:
    data = companies_report()
    rows = [c for c in data["companies"] if (not industry or industry in c["industries"]) and (c["vacancies"] or empty)]
    return page(request, "companies", d=data, rows=rows, industry=industry, empty=empty)


@app.get("/api/companies", response_model=CompaniesReport, summary="Companies by industry",
         description="Companies from the vacancy archive and the industry lists: industries, vacancies over all days, "
                     "the last day, application status, board link; companies without vacancies — to search by hand; "
                     "and the market by industry (vacancies, remote share, median salary, top technologies).")
def api_companies() -> dict[str, Any]:
    return companies_report()


@app.get("/sources", response_class=HTMLResponse, include_in_schema=False)
def sources_page(request: Request) -> HTMLResponse:
    return page(request, "sources", **sources_payload())


# --- API ---------------------------------------------------------------------------------------
# Without response_model, FastAPI turns the return annotation into the response model. Endpoints without a model
# of their own set response_model=None: the annotation is for mypy only, the response is not validated, the schema stays.

@app.get("/api/progress", response_model=None, summary="Run state",
         description="Contents of state/progress.json: stages, current source, counters, event feed, "
                     "errors; plus eta_total (seconds to the end based on past runs) and busy. "
                     "If there were no runs yet — null.")
def api_progress() -> dict[str, Any] | None:
    # no response_model: progress.json is written by another process (the run), its fields change together with
    # the stages — a strict schema would give false failures (in schemathesis, and a 500 instead of data)
    return progress_payload()


@app.get("/api/progress/stream", summary="Run state stream (SSE)",
         description="Server-Sent Events: every change of progress.json is sent as a `progress` event "
                     "(at most once a second). The pipeline page listens to this stream.")
async def api_progress_stream(request: Request) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        last = None
        while not await request.is_disconnected():
            data = progress_payload()
            text = json.dumps(data, ensure_ascii=False)
            if text != last:
                last = text
                yield f"event: progress\ndata: {text}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/runs", responses=errors(400, 409, 422), response_model=RunStarted, status_code=202, summary="Start a run",
          description="Starts the scripts in the background (output goes to last_run.log). 409 if a run is already going on, "
                      "including one started from a terminal or the Task Scheduler.")
def api_run(body: RunRequest) -> RunStarted:
    return RunStarted(scripts=runner.start(body.kind, body.skip))


@app.post("/api/runs/current/skip", response_model=None, responses=errors(400, 409), status_code=202, summary="Skip a stage of the running run",
          description="Asks the run to stop the current stage: its loop checks the request between steps "
                      "(usually within a second or two), what was collected so far goes on. 409 — no run, "
                      "the stage is no longer running or cannot be skipped.")
def api_skip(body: SkipRequest) -> dict[str, str]:
    data = progress.read()
    if not data or not progress.is_alive(data):
        raise HTTPException(409, "No run is going on")
    stage = next((s for s in data["stages"] if s["key"] == body.stage), None)
    if not stage or stage["status"] != progress.RUNNING:
        raise HTTPException(409, "This stage is not running now")
    if not stage.get("skippable"):
        raise HTTPException(409, "This stage cannot be skipped on the fly")
    progress.request_skip(body.stage, data["pid"])
    return {"stage": body.stage}


@app.post("/api/runs/current/stop", response_model=None, responses=errors(409), status_code=202, summary="Stop the whole run",
          description="Ends the running script and does not start the next ones in the queue (the \"everything\" run: mail, then "
                      "vacancies). What this run collected is lost — the reports stay from the previous one. 409 — no run.")
def api_stop() -> dict[str, int]:
    return {"pid": runner.stop()}


@app.get("/api/reports", response_model=list[Report], summary="Report list",
         description="Reports reports/jobs_YYYY-MM-DD.html, newest first. Open: /files/reports/<file>.")
def api_reports() -> list[dict[str, Any]]:
    return report_list()


@app.get("/api/sources", response_model=SourcesReport, summary="Sources of the last run",
         description="For every source: kind (ats / aggregator / site / career_page), how many vacancies "
                     "it gave, error; for ATS — platform and slug. Plus Telegram channels, freehire queries "
                     "and `gaps` — companies without vacancies in the report, with the reason (board not found, empty, none matching…).")
def api_sources() -> dict[str, Any]:
    return sources_payload()


# --- a CV for a vacancy -------------------------------------------------------------------------


def resolve_job(body: CvRequest) -> dict[str, Any]:
    """The vacancy from the request: pasted text, otherwise by link — from the reports, otherwise from the page."""
    if body.text.strip():
        return {"url": body.url, "company": body.company, "title": body.title, "text": body.text, "found_in": "text"}
    if not body.url.strip():
        raise HTTPException(422, "A link to the vacancy or its text is required")
    job = vacancy_lookup.archived(body.url, body.date)
    if job is None:
        try:
            job = vacancy_lookup.fetch(body.url)
        except vacancy_lookup.VacancyNotFound as e:
            raise HTTPException(404, f"Vacancy not found: {e}")
    return job


@app.post("/api/cv", responses=errors(400, 404, 422), response_model=CvResult, summary="Build a CV for a vacancy",
          description="Picks achievements, experience bullets and skills from cv/base.toml for the vacancy text "
                      "(technologies, topics, text similarity) and builds a PDF in cv_out/<date>/. "
                      "Invents nothing — only selects and reorders. 404 — the vacancy was not found.")
def api_cv(body: CvRequest) -> CvResult:
    job = resolve_job(body)
    company = body.company or job.get("company") or "Company"
    title = body.title or job.get("title") or "Vacancy"
    result = cvgen.build_cv(company, title, job["text"], job.get("url", ""), one_page=body.one_page)
    return CvResult(
        company=company, title=title, source=job.get("found_in", ""),
        pdf=f"/files/{result['pdf']}", md=f"/files/{result['md']}", pages=result["pages"],
        one_page=body.one_page, fit=result["fit"], missing=result["missing"], techs=result["techs"],
        topics=result["topics"], picks=result["picks"],
    )


@app.post("/api/cover", response_model=None, responses=errors(400, 404, 422), summary="Cover letter for a vacancy",
          description="From cv/base.toml ([summary] — achievements, [cover] — template and soft skills) for the vacancy text; "
                      "invents nothing. Saved to cv_out/<date>/…_cover_<style>.txt. "
                      "The vacancy is given as for /api/cv (link or text).")
def api_cover(body: CoverRequest) -> dict[str, Any]:
    job = resolve_job(body)
    result = cover_letter.build(body.company or job.get("company") or "Company",
                                body.title or job.get("title") or "Vacancy", job["text"], body.style)
    path = cover_letter.save(result, job.get("url", ""))
    return {**result, "source": job.get("found_in", ""), "file": f"/files/{path.relative_to(ROOT).as_posix()}"}


@app.get("/api/cv", response_model=list[BuiltCv], summary="Built CVs", description="PDFs from cv_out/, newest first (up to 40).")
def api_cv_list() -> list[dict[str, Any]]:
    return recent_cvs()


# --- profile skills (relevance score) ---------------------------------------------------------

def skills_payload() -> dict[str, Any]:
    """Skills with edits + in how many fitting vacancies of the last report each one appears."""
    import re as _re

    rows = relevance.skill_table()
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    texts: list[str] | None = None

    def load_texts() -> list[str]:
        nonlocal texts
        if texts is None:
            jobs = json.loads(files[-1].read_text(encoding="utf-8")) if files else []
            texts = [f"{j['title']}\n{j.get('text', '')}" for j in jobs
                     if market_check.RELEVANT_TITLE_RX.search(j["title"])]
        return texts

    def count(pattern: str) -> int | None:
        try:
            rx = _re.compile(pattern, _re.I)
        except _re.error:
            return None
        return sum(bool(rx.search(t)) for t in load_texts())

    # vacancy counts per pattern are cached for the last report: changing a weight or turning a skill off
    # does not change the pattern and does not trigger a recount; a new skill is counted alone (~0.2 s), the rest come from the cache
    key = [files[-1].name if files else "", cache.mtime(files[-1]) if files else 0]
    counts = dict(cache.cached("skills", key, lambda: {"relevant": len(load_texts()), "matches": {}}))
    matches = dict(counts["matches"])
    missing = [r["pattern"] for r in rows if r["pattern"] not in matches]
    if missing:
        matches.update({p: count(p) for p in missing})
        counts = {**counts, "matches": matches}
        cache.put("skills", key, counts)
    relevant = counts["relevant"]
    for row in rows:
        n = matches.get(row["pattern"])
        row["matches"] = n or 0
        if n is None:
            row["invalid"] = True
        row["share"] = round(100 * row["matches"] / relevant) if relevant else 0
    return {"skills": rows, "relevant": relevant, "day": files[-1].stem if files else ""}


@app.get("/skills", response_class=HTMLResponse, include_in_schema=False)
def skills_page(request: Request) -> HTMLResponse:
    return page(request, "skills", **skills_payload())


@app.get("/api/skills", response_model=SkillsReport, summary="Profile skills",
         description="Skills from profile.toml with edits from profile_overrides.json and the share of fitting vacancies "
                     "of the last report where the skill appears.")
def api_skills() -> dict[str, Any]:
    return skills_payload()


@app.put("/api/skills/{name}", response_model=None, responses=errors(400, 404), summary="Change a skill",
         description="Weight and / or turning off. Written to profile_overrides.json (profile.toml does not change); "
                     "vacancy scores are recomputed on the next run.")
def api_skill_change(name: str, body: SkillChange) -> dict[str, Any]:
    if not any(r["name"] == name for r in relevance.skill_table()):
        raise HTTPException(404, "No such skill")
    over = relevance.read_overrides()
    change = over["skills"].setdefault(name, {})
    if body.weight is not None:
        change["weight"] = body.weight
    if body.disabled is not None:
        change["disabled"] = body.disabled
    relevance.write_overrides(over)
    return next(r for r in relevance.skill_table() if r["name"] == name)


@app.post("/api/skills", response_model=None, responses=errors(400, 409, 422), status_code=201, summary="Add your own skill",
          description="A new skill with a regular expression and a weight (in profile_overrides.json). 422 — an invalid "
                      "regex, 409 — the name already exists.")
def api_skill_add(body: NewSkill) -> NewSkill:
    import re as _re
    try:
        _re.compile(body.pattern, _re.I)
    except _re.error as e:
        raise HTTPException(422, f"Invalid regular expression: {e}")
    if any(r["name"].lower() == body.name.lower() for r in relevance.skill_table()):
        raise HTTPException(409, "A skill with this name already exists")
    over = relevance.read_overrides()
    over["added"].append(body.model_dump())
    relevance.write_overrides(over)
    return body


@app.delete("/api/skills/{name}", response_model=None, summary="Reset an edit or delete your own skill",
            description="Your own skill is deleted; a skill from profile.toml gets its weight and on/off state reset.")
def api_skill_reset(name: str) -> dict[str, str]:
    over = relevance.read_overrides()
    over["skills"].pop(name, None)
    over["added"] = [a for a in over["added"] if a["name"] != name]
    relevance.write_overrides(over)
    return {"name": name}


# --- application events ------------------------------------------------------------------------


@app.post("/api/applications/events", response_model=None, responses=errors(400, 422), status_code=201, summary="Add an application event",
          description="Appends a block to applications_manual.toml and "
                      "rebuilds reports/applications.html from the letter cache — without requests to Gmail.")
def api_application_event(body: ApplicationEvent) -> dict[str, Any]:
    import applications
    try:
        event = manual_apps.add_event(body.company, body.kind, body.note, body.url, body.source, body.date)
    except ValueError as e:
        raise HTTPException(422, str(e))
    applications.build_page()
    return event


# --- profile: the CV base ---------------------------------------------------------------------

@app.get("/profile", response_class=HTMLResponse, include_in_schema=False)
def profile_page(request: Request) -> HTMLResponse:
    """Name, contacts, links, Summary, experience and education from cv/base.toml — what every CV is built from."""
    return page(request, "profile", p=cv_profile.read(), summary=cv_base.read_summary(),
                experience=cv_base.read_experience(), other=cv_base.read_other())


@app.get("/api/profile", response_model=Profile, summary="Profile for the CV header",
         description="The name, contacts and links keys from cv/base.toml; contacts is split into email, phone and location.")
def api_profile() -> dict[str, Any]:
    return cv_profile.read()


@app.put("/api/profile", response_model=Profile, responses=errors(400, 422), summary="Save the profile",
         description="Replaces only the name, contacts and links lines in cv/base.toml — the rest of the file and its "
                     "comments are untouched. No base.toml of your own — it is created from base.example.toml. "
                     "Visible in the next CV.")
def api_profile_save(body: ProfileUpdate) -> dict[str, Any]:
    try:
        return cv_profile.save(body.name, body.email, body.phone, body.location,
                               [link.model_dump() for link in body.links])
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/cv-base/summary", response_model=CvSummary, summary="Summary and achievements from the CV base",
         description="[summary] from cv/base.toml: intro, closing, max_achievements and [[summary.achievements]].")
def api_cv_summary() -> dict[str, Any]:
    return cv_base.read_summary()


@app.put("/api/cv-base/summary", response_model=CvSummary, responses=errors(400, 422), summary="Save the Summary",
         description="Items are matched with the file by position: only changed values are written, comments stay "
                     "(tomlkit). Visible in the next CV, cover letter and interview prep.")
def api_cv_summary_save(body: CvSummaryUpdate) -> dict[str, Any]:
    try:
        return cv_base.save_summary(body.intro, body.closing, body.max_achievements,
                                    [a.model_dump() for a in body.achievements])
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/cv-base/other", response_model=CvOther, summary="Education, languages, publications",
         description="[education], [languages] and [publications] from cv/base.toml — the bottom sections of the CV.")
def api_cv_other() -> dict[str, Any]:
    return cv_base.read_other()


@app.put("/api/cv-base/other", response_model=CvOther, responses=errors(400, 422),
         summary="Save education, languages, publications",
         description="Only these values change (tomlkit), comments and the rest of the base stay. An empty thesis "
                     "removes the thesis_ai key.")
def api_cv_other_save(body: CvOtherUpdate) -> dict[str, Any]:
    try:
        return cv_base.save_other(body.education.model_dump(), body.languages, body.publications)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/cv-base/topics", response_model=TopicsSuggestion, responses=errors(400),
          summary="Topics for an item text",
          description="A hint in the Profile editor: which topics from TOPICS (cvgen.py) the generator would find in "
                      "the text. Writes nothing.")
def api_cv_topics(body: TopicsRequest) -> dict[str, list[str]]:
    return {"topics": cv_base.suggest_topics(body.text)}


@app.get("/api/cv-base/experience", response_model=CvExperience, summary="Experience from the CV base",
         description="[[experience]] from cv/base.toml with [[experience.bullets]].")
def api_cv_experience() -> dict[str, Any]:
    return cv_base.read_experience()


@app.put("/api/cv-base/experience", response_model=CvExperience, responses=errors(400, 422),
         summary="Save the experience",
         description="Workplaces and bullets are matched with the file by position: only changed values are written, "
                     "comments stay (tomlkit). Visible in the next CV, cover letter and interview prep.")
def api_cv_experience_save(body: CvExperienceUpdate) -> dict[str, Any]:
    try:
        return cv_base.save_experience([j.model_dump() for j in body.jobs])
    except ValueError as e:
        raise HTTPException(422, str(e))


# --- interview prep ----------------------------------------------------------------------------

@app.get("/prep", response_class=HTMLResponse, include_in_schema=False)
def prep_page(request: Request, url: str = "", date: str = "") -> HTMLResponse:
    """Interview prep: with ?url= (the "🎤" button in the report) the materials are built at once."""
    result, error = None, ""
    if url:
        try:
            job = resolve_job(CvRequest(url=url, date=date))
            result = interview_prep.build(job.get("company") or "Company", job.get("title") or "Vacancy",
                                          job["text"], job.get("location", ""))
            result["url"] = url
        except HTTPException as e:
            error = e.detail
    return page(request, "prep", r=result, error=error, url=url)


@app.post("/prep", response_class=HTMLResponse, include_in_schema=False)
async def prep_page_text(request: Request) -> HTMLResponse:
    """Prep from pasted text — for vacancies without a link (a PDF from a recruiter, correspondence).

    The form is parsed by hand (urlencoded): python-multipart is not needed for it."""
    from urllib.parse import parse_qs

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8")).items()}
    text = form.get("text", "").strip()
    result, error = None, ""
    if text:
        result = interview_prep.build(form.get("company", "").strip() or "Company",
                                      form.get("title", "").strip() or "Vacancy", text, form.get("location", "").strip())
        result["url"] = ""
    else:
        error = "Paste the vacancy text"
    return page(request, "prep", r=result, error=error, url="", form=form)


@app.post("/api/prep", response_model=None, responses=errors(400, 404, 422), summary="Interview prep",
          description="From the facts in cv/base.toml for the vacancy: which requirements you meet and how to prove it, what is missing, "
                      "a tell-me-about-yourself draft (EN), stories for behavioural questions, an HR checklist, questions to "
                      "the company, the levels.fyi median. The vacancy is given as for /api/cv (link or text).")
def api_prep(body: CvRequest) -> dict[str, Any]:
    job = resolve_job(body)
    return interview_prep.build(body.company or job.get("company") or "Company", body.title or job.get("title") or "Vacancy",
                                job["text"], job.get("location", ""))


# "Market" and "Skills" are computed in advance so the first open after a restart does not wait ~10 s
# (turned off in tests: a background computation on real data would interfere with the patched paths)
if not os.environ.get("JOBHUNTER_NO_WARM"):
    threading.Thread(target=warm_caches, name="warm-caches", daemon=True).start()
