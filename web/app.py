"""Local job-hunter web UI: the run pipeline, the report archive, applications, sources.

Listens on 127.0.0.1 only: reports contain mail, applications and contacts, and they must not leave the machine.
Run: .venv\\Scripts\\python.exe -m web  (opens the browser; API docs — /docs).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobhunter import config, cover_letter, coverage, cvgen, industry, interview_prep, manual_apps, market_check, progress, relevance, vacancy_lookup
from jobhunter.models import norm_name

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


class ErrorDetail(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class Unprocessable(BaseModel):
    detail: list[ValidationIssue] | str = Field(
        description="A list of pydantic errors (the body does not match the schema) or a text to show in the UI "
                    "(bad regex, neither a link nor a vacancy text…)")


ERROR_TEXT = {400: "The request body could not be parsed (not JSON / not UTF-8)", 404: "Not found",
              409: "Conflicts with the current state: a run is / is not going on, the name is taken", 422: "Invalid data"}


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Endpoint error codes for OpenAPI: FastAPI itself describes only 422 and only as a list of pydantic errors,
    but endpoints also return their own 422 as a string (the UI shows it) — schemathesis checks both."""
    return {c: {"model": Unprocessable if c == 422 else ErrorDetail, "description": ERROR_TEXT[c]} for c in codes}


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


def _has_surrogate(value: object) -> bool:
    if isinstance(value, str):
        return any("\ud800" <= ch <= "\udfff" for ch in value)
    if isinstance(value, dict):
        return any(_has_surrogate(k) or _has_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_surrogate(v) for v in value)
    return False


class ApiModel(BaseModel):
    """A request body. JSON allows "\\ud800", a lone surrogate: a string with it cannot be encoded to UTF-8, and
    writing it to a file failed (500) or, worse, truncated the file. Rejected on input: 422, like any bad input."""

    @model_validator(mode="before")
    @classmethod
    def no_surrogates(cls, data: object) -> object:
        if _has_surrogate(data):
            raise ValueError("the text has broken characters (lone UTF-16 surrogates)")
        return data


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
async def no_cache_reports(request: Request, call_next):
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
       ("cv", "CV", "📄"), ("cover", "Cover letter", "✉️"), ("prep", "Interview prep", "🎤"), ("market", "CV vs market", "⚖️"), ("companies", "Companies", "🏢"),
       ("skills", "Skills", "🛠️"), ("sources", "Sources", "🌐")]


# --- starting a run ---------------------------------------------------------------------------

class Runner:
    """One run at a time: scripts one after another in a background thread, output goes to last_run.log."""

    SCRIPTS = {
        "all": ["applications.py", "main.py"],  # like run_daily.bat: mail first, then vacancies
        "main": ["main.py"],
        "applications": ["applications.py"],
    }
    # what can be turned off in advance -> a main.py flag; "mail" removes applications.py from the queue
    SKIP_FLAGS = {"telegram": "--no-telegram", "freehire": "--no-freehire", "aggregators": "--no-aggregators"}

    def __init__(self):
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.queue: list[str] = []
        self.stopping = False

    def busy(self) -> bool:
        """Whether a run is going on: started from here or from a terminal / the Task Scheduler."""
        if self.thread and self.thread.is_alive():
            return True
        data = progress.read()
        return bool(data and progress.is_alive(data))

    def start(self, kind: str, skip: Sequence[str]) -> list[str]:
        """Starts the script queue; skip — stages turned off in advance. -> commands without the interpreter."""
        with self.lock:
            if self.busy():
                raise HTTPException(409, "A run is already going on — wait for it to end")
            scripts = [s for s in self.SCRIPTS[kind] if not ("mail" in skip and s == "applications.py")]
            if not scripts:
                raise HTTPException(422, "Nothing to run: mail is turned off and there are no other scripts in the queue")
            flags = [self.SKIP_FLAGS[k] for k in skip if k in self.SKIP_FLAGS]
            commands = [[s, *flags] if s == "main.py" else [s] for s in scripts]
            self.queue = [" ".join(c) for c in commands]
            self.stopping = False
            self.thread = threading.Thread(target=self._run, args=(commands,), daemon=True)
            self.thread.start()
            return list(self.queue)

    def _run(self, commands: list[list[str]]) -> None:
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        python = python if python.exists() else Path(sys.executable)
        with RUN_LOG.open("a", encoding="utf-8") as log:
            for i, command in enumerate(commands):
                log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(command)} (started from the web UI) ===\n")
                log.flush()
                # a separate process group: Ctrl+C in the server window does not interrupt the running run.
                # DETACHED_PROCESS is not used: with it, runs started from some shells
                # silently ended halfway. Restarting the server still interrupts the run — see docs/web-ui.md.
                # if, not a conditional expression: mypy narrows sys.platform only in an if statement,
                # and CREATE_NEW_PROCESS_GROUP exists only on Windows (CI checks under Linux)
                if sys.platform == "win32":
                    detached = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    detached = 0
                self.process = subprocess.Popen(
                    [str(python), *command], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", **({progress.CONTINUE_RUN_ENV: "1"} if i else {})},
                    creationflags=detached,
                    start_new_session=sys.platform != "win32",
                )
                self.process.wait()
                self.queue.pop(0)
                if self.stopping:  # "Stop run": the next scripts of the queue are not started
                    log.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} stopped from the web UI ===\n")
                    break
        self.queue = []
        self.process = None
        warm_caches()  # a new report — recompute "Market" and "Skills" before anyone opens them

    def stop(self) -> int:
        """A full stop: the queue is cleared, the running script ends together with its child processes.

        Works for a run from a terminal / the Task Scheduler too (pid from progress.json). -> pid of the stopped one.
        What this run collected is not saved: the reports stay from the previous run.
        """
        with self.lock:
            data = progress.read()
            pid = self.process.pid if self.process and self.process.poll() is None else None
            if pid is None and data and progress.is_alive(data):
                pid = data["pid"]
            if pid is None:
                raise HTTPException(409, "No run is going on")
            self.stopping = True
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                try:
                    os.killpg(pid, signal.SIGTERM)  # start_new_session: group = pid
                except OSError:
                    os.kill(pid, signal.SIGTERM)
            return pid


runner = Runner()

SCHEDULER_FILE = ROOT / "state" / "scheduler.json"  # date of the last scheduled run


def _daily_scheduler(at: str) -> None:
    """Once a day at `at` (HH:MM, container time / TZ) — the "everything" run, like run_daily.bat.

    Turned on with the JOBHUNTER_DAILY_AT variable (in docker-compose.yml); usually not needed on Windows —
    there are the Task Scheduler and run_daily.bat. A missed time (the server was off) is caught up
    if there was no scheduled run today yet; if a run is already going on, it waits for it to end.
    """
    import time

    while True:
        now = datetime.now()
        try:
            last = json.loads(SCHEDULER_FILE.read_text(encoding="utf-8")).get("last", "")
        except (OSError, ValueError):
            last = ""
        if now.strftime("%H:%M") >= at and last != now.date().isoformat() and not runner.busy():
            try:
                runner.start("all", [])
                SCHEDULER_FILE.write_text(json.dumps({"last": now.date().isoformat()}), encoding="utf-8")
            except HTTPException:
                pass  # a run is already going on — try again in a minute
        time.sleep(60)


if os.environ.get("JOBHUNTER_DAILY_AT"):
    threading.Thread(target=_daily_scheduler, args=(os.environ["JOBHUNTER_DAILY_AT"],), daemon=True).start()


class RunRequest(ApiModel):
    """What to run."""
    kind: str = Field("all", description="all — mail, then vacancies (like run_daily.bat); main — only "
                                          "vacancies; applications — only mail", pattern="^(all|main|applications)$")
    skip: list[Literal["mail", "telegram", "freehire", "aggregators"]] = Field(
        default_factory=list, description="Stages turned off in advance: mail — do not run applications.py; "
                                          "telegram / freehire / aggregators — the --no-… flags of main.py")


class RunStarted(BaseModel):
    scripts: list[str] = Field(description="Commands in start order")


class Report(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    file: str = Field(description="File name: open /files/reports/<file>")
    size_kb: int
    built: str = Field(description="Build time, HH:MM")
    md: bool = Field(description="A Markdown version sits next to it")


class BuiltCv(BaseModel):
    name: str = Field(description="\"Company — Role\" from the first line of the .md, otherwise the file name")
    vacancy: str = Field(description="Link to the vacancy or ''")
    pdf: str
    md: str = Field(description="Link to the Markdown draft or ''")
    one_page: bool
    mtime: float
    built: str = Field(description="DD.MM HH:MM")


class Skill(BaseModel):
    name: str
    pattern: str = Field(description="Regular expression (case-insensitive)")
    base_weight: int = Field(description="Weight from profile.toml")
    weight: int = Field(description="Weight with the edit from profile_overrides.json")
    disabled: bool
    added: bool = Field(description="Your own skill, added from the UI")
    matches: int = Field(description="Fitting vacancies of the last report that mention the skill")
    share: int = Field(description="% of fitting vacancies")
    invalid: bool = Field(False, description="The pattern does not compile")


class SkillsReport(BaseModel):
    skills: list[Skill]
    relevant: int = Field(description="Fitting vacancies in the last report")
    day: str = Field(description="Day of the last report or ''")


class MarketFit(BaseModel):
    median: int | None = Field(description="Median CV-to-vacancy match, %; null — not measured")
    share80: int = Field(description="% of vacancies with a match of 80% or more")


class MarketKeyword(BaseModel):
    name: str
    count: int
    share: int = Field(description="% of fitting vacancies where the technology is a key one")
    in_cv: Literal["pdf", "base", ""] = Field(description="pdf — in the PDF CV, base — only in the base, '' — nowhere")
    partial: str = Field(description="A caveat: present, but partly (AWS: only S3) or ''")


class MarketTitle(BaseModel):
    name: str
    count: int
    share: int
    in_headline: bool = Field(description="The phrase is in the CV headline")


class MarketLevel(BaseModel):
    name: str
    count: int
    share: int


class MarketHygiene(BaseModel):
    stop_words: list[str] = Field(description="Filler words in experience bullets")
    numbers_by_job: dict[str, int] = Field(description="Company -> bullets with numbers")
    pages: dict[str, int] = Field(description="PDF -> pages")
    bullets: int
    with_numbers: int
    with_verb: int = Field(description="Bullets starting with an action verb")
    long: list[str] = Field(description="Too long bullets (the beginning)")
    avg_words: int
    no_numbers: list[str] = Field(description="Bullets without numbers (the beginning)")
    contacts: dict[str, bool] = Field(description="Contact -> found in the PDF")


class MarketReport(BaseModel):
    """Without vacancies in the archive — only day='', total=0, relevant=0 and advice."""
    day: str
    total: int = Field(description="Vacancies of the day")
    relevant: int = Field(description="Fitting by title and with a text")
    fit: MarketFit | None = None
    keywords: list[MarketKeyword] = []
    titles: list[MarketTitle] = []
    seniority: list[MarketLevel] = []
    headline: str = Field("", description="CV headlines joined with \" / \"")
    hygiene: MarketHygiene | None = None
    days: list[str] = Field([], description="Archive days, newest first")
    advice: list[str] = Field(description="Conclusions: what to fix in the CV")


class Source(BaseModel):
    """Common fields; the rest comes from state/sources_latest.json and ats_mapping.json as is (ATS: platform, slug…)."""
    model_config = ConfigDict(extra="allow")

    name: str
    kind: str = Field(description="ats / aggregator / site / career_page / …")
    jobs: int = Field(description="Vacancies the source gave")
    error: str = Field(description="Error or ''")
    reason: str = Field(description="Why there are no vacancies in the report (a gaps.groups key) or ''")
    detail: str
    checked: str = Field(description="When the board was checked, if it matters for the reason, or ''")
    platform: str | None = Field(None, description="ATS platform; null — board not found")
    slug: str | None = None


class SourceKind(BaseModel):
    sources: int
    jobs: int
    empty: int = Field(description="No vacancies and no error")
    errors: int


class FreehireQuery(BaseModel):
    name: str
    title: str
    params: dict[str, Any]
    total: int | None = Field(description="Vacancies on the site; null — before the first run")
    fetched: int | None = Field(description="Fetched; null — before the first run")
    skipped: bool = Field(description="The query is off (relocation, for example)")


class GapGroup(BaseModel):
    key: str
    title: str
    hint: str
    count: int


class GapRow(BaseModel):
    name: str
    reason: str
    detail: str
    checked: str


class Gaps(BaseModel):
    groups: list[GapGroup]
    rows: list[GapRow]
    reports: int | None = Field(description="Reports the companies were looked up in")


class SourcesReport(BaseModel):
    built: str = Field(description="Run time DD.MM HH:MM or ''")
    rows: list[Source]
    kinds: dict[str, SourceKind]
    telegram: list[str]
    freehire: list[FreehireQuery]
    gaps: Gaps


class SkipRequest(ApiModel):
    """Which stage of the running run to skip."""
    stage: str = Field(description="Stage key from /api/progress (stages[].key); the stage must be running and skippable")


# --- data for the pages ------------------------------------------------------------------------

def progress_payload() -> dict | None:
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


def report_list() -> list[dict]:
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


def sources_payload() -> dict:
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
    kinds: dict[str, dict] = {}
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


def freehire_queries(settings: dict) -> list[dict]:
    """freehire queries: from state/freehire_latest.json, and before the first run — from settings.toml without results."""
    try:
        return json.loads(FREEHIRE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        skip = ("name", "title")
        return [{"name": q["name"], "title": q.get("title", q["name"]), "total": None, "fetched": None,
                 "params": {k: v for k, v in q.items() if k not in skip}, "skipped": False}
                for q in settings.get("queries", [])]


def recent_cvs(limit: int = 40) -> list[dict]:
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


def page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, f"{name}.html",
                                      {"nav": NAV, "active": name, "v": _static_version(), **context})


# --- pages -------------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/pipeline")


@app.get("/pipeline", response_class=HTMLResponse, include_in_schema=False)
def pipeline_page(request: Request):
    return page(request, "pipeline")


@app.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def reports_page(request: Request, file: str = ""):
    reports = report_list()
    if file and not any(r["file"] == file for r in reports):
        raise HTTPException(404, "No such report")
    current = file or (reports[0]["file"] if reports else "")
    return page(request, "reports", reports=reports, current=current)


@app.get("/applications", response_class=HTMLResponse, include_in_schema=False)
def applications_page(request: Request, company: str = "", title: str = "", url: str = "", kind: str = ""):
    exists = (REPORTS / "applications.html").exists()
    prefill = {"company": company, "title": title, "url": url, "kind": kind or ("applied" if url else "")}
    return page(request, "applications", exists=exists, prefill=prefill, kinds=manual_apps.KINDS,
                companies=application_companies())


@app.get("/cv", response_class=HTMLResponse, include_in_schema=False)
def cv_page(request: Request):
    return page(request, "cv", cvs=recent_cvs())


@app.get("/cover", response_class=HTMLResponse, include_in_schema=False)
def cover_page(request: Request):
    return page(request, "cover")


# Slow page computations ("Market" analyses all vacancies of a report, "Skills" searches every skill in their
# texts) are kept both in memory and on disk: after a server restart the page opens at once.
# The key is the names and modification times of the input files; a file changed — recompute.
WEB_CACHE_FILE = ROOT / "state" / "web_cache.json"
_cache_lock = threading.Lock()
_memory_cache: dict[str, dict] = {}


def _cached(name: str, key: list, compute):
    """The cached value if the key matches; otherwise compute() and a write to the cache."""
    key = json.loads(json.dumps(key, default=str))  # tuple -> list, Path -> str: comparable with what was read from disk
    with _cache_lock:
        if not _memory_cache and WEB_CACHE_FILE.exists():
            try:
                _memory_cache.update(json.loads(WEB_CACHE_FILE.read_text(encoding="utf-8")))
            except ValueError:
                pass
        entry = _memory_cache.get(name)
        if entry and entry.get("key") == key:
            return entry["value"]
    value = compute()
    with _cache_lock:
        _memory_cache[name] = {"key": key, "value": value}
        WEB_CACHE_FILE.parent.mkdir(exist_ok=True)
        WEB_CACHE_FILE.write_text(json.dumps(_memory_cache, ensure_ascii=False, default=str), encoding="utf-8")
    return value


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0


def market_report(day: str = "") -> dict:
    """market_check.analyze + conclusions; recomputed (~10 s) only if the day's vacancies or the CV changed."""
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    path = next((f for f in files if f.stem == day), files[-1] if files else None)
    cv_files = [config.CV_BASE_FILE, *(Path(p) for p in market_check.geo.S.get("cv", {}).get("files", []))]
    key = [path and path.name, path and path.stat().st_mtime, *(_mtime(f) for f in cv_files)]

    def compute():
        report = market_check.analyze(path.stem if path else "")
        report["advice"] = market_check.advice(report)
        return report

    # a report for another day is opened rarely — it replaces the last one; the last one has the "market" key
    return _cached("market" if path == (files[-1] if files else None) else "market:other", key, compute)


def companies_report() -> dict:
    """Companies by industry (industry.companies_overview) over all archive days; vacancies without repeats.

    Recomputed only when the archive files, companies.txt, applications (the mail cache,
    applications_manual.toml) or the industry rules change; board links from ats_mapping.json — on every request.
    """
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    # a run rewrites ats_mapping.json all the time — board links are added per request, not cached
    deps = [config.COMPANIES_FILE, config.SETTINGS_FILE, config.MAIL_CACHE_FILE, config.MANUAL_APPLICATIONS_FILE,
            Path(industry.__file__)]
    key = [[f.name, f.stat().st_mtime] for f in files] + [_mtime(f) for f in deps]

    def compute() -> dict:
        from jobhunter.applications_report import StatusIndex

        unique: dict[str, dict] = {}
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

    data = _cached("companies_by_industry", key, compute)
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
    key = [_mtime(config.MANUAL_APPLICATIONS_FILE), _mtime(config.MAIL_CACHE_FILE)]
    return _cached("companies", key, manual_apps.companies)


def warm_caches() -> None:
    """Computes "Market", "Skills" and the Applications hints in advance — in the background at server start and after a run."""
    for fn in (application_companies, market_report, companies_report, skills_payload):
        try:
            fn()
        except Exception as e:  # warming up is not essential: the page computes it itself and shows the error
            print(f"cache warm-up {fn.__name__}: {e}", file=sys.stderr)


@app.get("/market", response_class=HTMLResponse, include_in_schema=False)
def market_page(request: Request, day: str = ""):
    return page(request, "market", r=market_report(day))


@app.get("/api/market", response_model=MarketReport, summary="CV vs market",
         description="A local ATS checker: over the fitting vacancies of a report (state/jobs/<date>.json) — which "
                     "technologies are required and where they are in the CV (pdf / base only / nowhere), what the "
                     "vacancies are called and whether the CV headline matches, experience bullet hygiene and conclusions.")
def api_market(day: str = ""):
    return market_report(day)


class CompanyJob(BaseModel):
    title: str
    url: str
    day: str


class CompanyRow(BaseModel):
    name: str
    industries: list[str]
    by: str = Field(description="Where the industry comes from: `list` (known companies / companies.txt) or `vacancies`")
    vacancies: int = Field(description="Vacancies over all days, without repeats by title")
    last: str = Field(description="The last day with a vacancy or ''")
    status: str = Field(description="Application status or ''")
    board: str = Field(description="Link to the ATS board or ''")
    jobs: list[CompanyJob]


class SkillShare(BaseModel):
    name: str
    share: int = Field(description="% of the industry's vacancies")


class IndustryMarket(BaseModel):
    industry: str
    label: str
    vacancies: int
    remote_share: int | None = Field(description="% remote among vacancies with a known format; null — unknown")
    salaries: int = Field(description="Vacancies with a salary range")
    salary_median: float | None = Field(description="Median of the range middle, EUR/month; null — fewer than 3 ranges")
    skills: list[SkillShare]


class CompaniesReport(BaseModel):
    companies: list[CompanyRow]
    counts: dict[str, int] = Field(description="Companies per industry code")
    labels: dict[str, str] = Field(description="Industry code -> label")
    vacancies: int = Field(description="Archive vacancies without repeats")
    market: list[IndustryMarket]


@app.get("/companies", response_class=HTMLResponse, include_in_schema=False)
def companies_page(request: Request, industry: str = "", empty: bool = True):
    data = companies_report()
    rows = [c for c in data["companies"] if (not industry or industry in c["industries"]) and (c["vacancies"] or empty)]
    return page(request, "companies", d=data, rows=rows, industry=industry, empty=empty)


@app.get("/api/companies", response_model=CompaniesReport, summary="Companies by industry",
         description="Companies from the vacancy archive and the industry lists: industries, vacancies over all days, "
                     "the last day, application status, board link; companies without vacancies — to search by hand; "
                     "and the market by industry (vacancies, remote share, median salary, top technologies).")
def api_companies():
    return companies_report()


@app.get("/sources", response_class=HTMLResponse, include_in_schema=False)
def sources_page(request: Request):
    return page(request, "sources", **sources_payload())


# --- API ---------------------------------------------------------------------------------------

@app.get("/api/progress", summary="Run state",
         description="Contents of state/progress.json: stages, current source, counters, event feed, "
                     "errors; plus eta_total (seconds to the end based on past runs) and busy. "
                     "If there were no runs yet — null.")
def api_progress():
    # no response_model: progress.json is written by another process (the run), its fields change together with
    # the stages — a strict schema would give false failures (in schemathesis, and a 500 instead of data)
    return progress_payload()


@app.get("/api/progress/stream", summary="Run state stream (SSE)",
         description="Server-Sent Events: every change of progress.json is sent as a `progress` event "
                     "(at most once a second). The pipeline page listens to this stream.")
async def api_progress_stream(request: Request):
    async def events():
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
def api_run(body: RunRequest):
    return RunStarted(scripts=runner.start(body.kind, body.skip))


@app.post("/api/runs/current/skip", responses=errors(400, 409), status_code=202, summary="Skip a stage of the running run",
          description="Asks the run to stop the current stage: its loop checks the request between steps "
                      "(usually within a second or two), what was collected so far goes on. 409 — no run, "
                      "the stage is no longer running or cannot be skipped.")
def api_skip(body: SkipRequest):
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


@app.post("/api/runs/current/stop", responses=errors(409), status_code=202, summary="Stop the whole run",
          description="Ends the running script and does not start the next ones in the queue (the \"everything\" run: mail, then "
                      "vacancies). What this run collected is lost — the reports stay from the previous one. 409 — no run.")
def api_stop():
    return {"pid": runner.stop()}


@app.get("/api/reports", response_model=list[Report], summary="Report list",
         description="Reports reports/jobs_YYYY-MM-DD.html, newest first. Open: /files/reports/<file>.")
def api_reports():
    return report_list()


@app.get("/api/sources", response_model=SourcesReport, summary="Sources of the last run",
         description="For every source: kind (ats / aggregator / site / career_page), how many vacancies "
                     "it gave, error; for ATS — platform and slug. Plus Telegram channels, freehire queries "
                     "and `gaps` — companies without vacancies in the report, with the reason (board not found, empty, none matching…).")
def api_sources():
    return sources_payload()


# --- a CV for a vacancy -------------------------------------------------------------------------

class CvRequest(ApiModel):
    """Which vacancy to build a CV for: a link from a report, any link, or pasted text."""
    url: str = Field("", description="Link to the vacancy. First looked up in saved reports (state/jobs/), "
                                     "otherwise the page is read (robots.txt, 1 request/s)")
    date: str = Field("", description="Date YYYY-MM-DD of the report the button was clicked in — looked up there first")
    text: str = Field("", description="Vacancy text; if set, the link is not read")
    company: str = Field("", description="Company (refines or replaces the one found)")
    title: str = Field("", description="Vacancy title (refines or replaces the one found)")
    one_page: bool = Field(False, strict=True, description="One-page variant: shrinks until it fits")


class CvPick(BaseModel):
    section: str = Field(description="Summary or a company from the experience")
    text: str
    pin: bool = Field(description="The bullet is pinned in the base (present in every version)")
    techs: list[str] = Field(description="Vacancy technologies found in the bullet")
    topics: list[str] = Field(description="Vacancy topics shared with the bullet")
    similarity: float = Field(description="Text similarity of the bullet to the vacancy (TF-IDF, 0–1)")
    score: float = Field(description="Total score: technology 2, topic 1, similarity up to 4")


class CvResult(BaseModel):
    company: str
    title: str
    source: str = Field(description="Where the vacancy came from: report date, page — from the page, text — pasted")
    pdf: str = Field(description="Link to the PDF")
    md: str = Field(description="Link to the Markdown draft (it can be edited and rebuilt: cvgen.py --render)")
    pages: int
    one_page: bool
    fit: int = Field(description="Fit % of the CV to the vacancy")
    missing: list[str] = Field(description="Key vacancy skills missing from the CV")
    techs: list[str]
    topics: list[str]
    picks: list[CvPick] = Field(description="Picked bullets and why")


def resolve_job(body) -> dict:
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
def api_cv(body: CvRequest):
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


class CoverRequest(CvRequest):
    style: Literal["short", "full"] = Field("short", description="short — bulleted, full — in paragraphs")


@app.post("/api/cover", responses=errors(400, 404, 422), summary="Cover letter for a vacancy",
          description="From cv/base.toml ([summary] — achievements, [cover] — template and soft skills) for the vacancy text; "
                      "invents nothing. Saved to cv_out/<date>/…_cover_<style>.txt. "
                      "The vacancy is given as for /api/cv (link or text).")
def api_cover(body: CoverRequest):
    job = resolve_job(body)
    result = cover_letter.build(body.company or job.get("company") or "Company",
                                body.title or job.get("title") or "Vacancy", job["text"], body.style)
    path = cover_letter.save(result, job.get("url", ""))
    return {**result, "source": job.get("found_in", ""), "file": f"/files/{path.relative_to(ROOT).as_posix()}"}


@app.get("/api/cv", response_model=list[BuiltCv], summary="Built CVs", description="PDFs from cv_out/, newest first (up to 40).")
def api_cv_list():
    return recent_cvs()


# --- profile skills (relevance score) ---------------------------------------------------------

def skills_payload() -> dict:
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
    key = [files[-1].name if files else "", _mtime(files[-1]) if files else 0]
    counts = dict(_cached("skills", key, lambda: {"relevant": len(load_texts()), "matches": {}}))
    matches = dict(counts["matches"])
    missing = [r["pattern"] for r in rows if r["pattern"] not in matches]
    if missing:
        matches.update({p: count(p) for p in missing})
        counts = {**counts, "matches": matches}
        with _cache_lock:
            _memory_cache["skills"] = {"key": json.loads(json.dumps(key)), "value": counts}
            WEB_CACHE_FILE.write_text(json.dumps(_memory_cache, ensure_ascii=False, default=str), encoding="utf-8")
    relevant = counts["relevant"]
    for row in rows:
        n = matches.get(row["pattern"])
        row["matches"] = n or 0
        if n is None:
            row["invalid"] = True
        row["share"] = round(100 * row["matches"] / relevant) if relevant else 0
    return {"skills": rows, "relevant": relevant, "day": files[-1].stem if files else ""}


class SkillChange(ApiModel):
    """A skill edit: weight and / or turning off. Applies from the next run."""
    weight: int | None = Field(None, ge=-30, le=30, description="New weight; negative is a penalty for the skill in a vacancy")
    disabled: bool | None = Field(None, strict=True, description="true — the skill does not count in the score")

    @field_validator("weight", mode="before")
    @classmethod
    def weight_not_bool(cls, value: object) -> object:
        return _not_bool(value)


def _not_bool(value: object) -> object:
    """A weight is a number. Pydantic in lax mode would take true as 1, and strict=True would reject 3.0 too (an
    integer by JSON Schema) — so only bool is cut off."""
    if isinstance(value, bool):
        raise ValueError("the weight is an integer, not true/false")
    return value


class NewSkill(ApiModel):
    """Your own skill: name, regular expression (case-insensitive) and weight."""
    name: str = Field(min_length=1, max_length=60)
    pattern: str = Field(min_length=1, description="Regular expression, searched in the vacancy title and text")
    weight: int = Field(2, ge=-30, le=30)

    @field_validator("weight", mode="before")
    @classmethod
    def weight_not_bool(cls, value: object) -> object:
        return _not_bool(value)


@app.get("/skills", response_class=HTMLResponse, include_in_schema=False)
def skills_page(request: Request):
    return page(request, "skills", **skills_payload())


@app.get("/api/skills", response_model=SkillsReport, summary="Profile skills",
         description="Skills from profile.toml with edits from profile_overrides.json and the share of fitting vacancies "
                     "of the last report where the skill appears.")
def api_skills():
    return skills_payload()


@app.put("/api/skills/{name}", responses=errors(400, 404), summary="Change a skill",
         description="Weight and / or turning off. Written to profile_overrides.json (profile.toml does not change); "
                     "vacancy scores are recomputed on the next run.")
def api_skill_change(name: str, body: SkillChange):
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


@app.post("/api/skills", responses=errors(400, 409, 422), status_code=201, summary="Add your own skill",
          description="A new skill with a regular expression and a weight (in profile_overrides.json). 422 — an invalid "
                      "regex, 409 — the name already exists.")
def api_skill_add(body: NewSkill):
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


@app.delete("/api/skills/{name}", summary="Reset an edit or delete your own skill",
            description="Your own skill is deleted; a skill from profile.toml gets its weight and on/off state reset.")
def api_skill_reset(name: str):
    over = relevance.read_overrides()
    over["skills"].pop(name, None)
    over["added"] = [a for a in over["added"] if a["name"] != name]
    relevance.write_overrides(over)
    return {"name": name}


# --- application events ------------------------------------------------------------------------

class ApplicationEvent(ApiModel):
    """An application event: written to applications_manual.toml, the applications page is rebuilt from the letter cache."""
    company: str = Field(min_length=1, description="Company — as in the report, the event is merged with letters by it")
    kind: Literal["applied", "interview", "rejected", "declined", "offer", "other"] = Field(
        description="applied — applied, interview — a stage, rejected — rejected, declined — declined by you, offer — offer, "
                    "other — a note")
    note: str = Field("", description="What happened")
    url: str = Field("", description="Link to the vacancy: reports will mark it \"🔁 already applied\"")
    source: str = Field("", description="Where: company site, LinkedIn, Telegram…")
    date: datetime | None = Field(None, description="Event time; now by default")

    @field_validator("date", mode="before")
    @classmethod
    def iso_date(cls, value: object) -> object:
        """Only an ISO string, as in the schema: pydantic would also take a number (Unix seconds) — "0" would become 1970."""
        if value is not None and not isinstance(value, str | datetime):
            raise ValueError("the date is an ISO 8601 string, e.g. 2026-11-18T10:00")
        return value


@app.post("/api/applications/events", responses=errors(400, 422), status_code=201, summary="Add an application event",
          description="Appends a block to applications_manual.toml and "
                      "rebuilds reports/applications.html from the letter cache — without requests to Gmail.")
def api_application_event(body: ApplicationEvent):
    import applications
    try:
        event = manual_apps.add_event(body.company, body.kind, body.note, body.url, body.source, body.date)
    except ValueError as e:
        raise HTTPException(422, str(e))
    applications.build_page()
    return event


# --- interview prep ----------------------------------------------------------------------------

@app.get("/prep", response_class=HTMLResponse, include_in_schema=False)
def prep_page(request: Request, url: str = "", date: str = ""):
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
async def prep_page_text(request: Request):
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


@app.post("/api/prep", responses=errors(400, 404, 422), summary="Interview prep",
          description="From the facts in cv/base.toml for the vacancy: which requirements you meet and how to prove it, what is missing, "
                      "a tell-me-about-yourself draft (EN), stories for behavioural questions, an HR checklist, questions to "
                      "the company, the levels.fyi median. The vacancy is given as for /api/cv (link or text).")
def api_prep(body: CvRequest):
    job = resolve_job(body)
    return interview_prep.build(body.company or job.get("company") or "Company", body.title or job.get("title") or "Vacancy",
                                job["text"], job.get("location", ""))


# "Market" and "Skills" are computed in advance so the first open after a restart does not wait ~10 s
# (turned off in tests: a background computation on real data would interfere with the patched paths)
if not os.environ.get("JOBHUNTER_NO_WARM"):
    threading.Thread(target=warm_caches, name="warm-caches", daemon=True).start()
