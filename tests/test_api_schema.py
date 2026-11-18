"""Property-based check of the web API against its own OpenAPI schema (schemathesis on top of hypothesis).

schemathesis reads /openapi.json, generates requests itself (odd parameter values included) and checks:
no 500s, the response matches the schema (response_model), headers and codes are as declared.
Two checks:
- reads (GET) — as is, without the endless SSE stream;
- writes (POST/PUT/DELETE) — both valid bodies and deliberately invalid ones (negative mode: wrong schema, out of
  ge/le, empty strings with min_length) — 4xx expected, not 500. Every file these endpoints write is redirected
  to tmp (the `writes` fixture); the run starts no scripts (Runner._run is a stub), stop without a run is 409
  and kills nothing; no network and no Chrome: reading a vacancy by link and building a PDF are stubbed.
Data lives in a temp folder, as in test_web.py: no personal files needed, the test runs in CI too.
"""
import json

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings

import web.app as web
from jobhunter import config, cv_match, cvgen, levels, progress, relevance, vacancy_lookup

schema = schemathesis.openapi.from_asgi("/openapi.json", web.app)

READ_ONLY = (schema.include(method="GET")
             .exclude(path="/api/progress/stream"))

JOBS = [
    {"url": "https://example.com/1", "company": "Acme", "title": "Senior Python Developer",
     "text": "Python, PostgreSQL, Kafka. Payments platform.", "industries": ["fintech"],
     "remote": True, "salary": "€5000 - €7000", "channel": ""},
    {"url": "https://t.me/example_jobs/5", "company": "NDA", "title": "Backend Python Developer",
     "text": "Looking for a developer for an online store. Python, Redis.", "channel": "example_jobs"},
    {"url": "https://example.com/2", "company": "Globex Bank", "title": "Python Engineer", "text": "Python, SQL"},
]


SOURCES = {  # as main.py writes it: ATS sources carry the ats_mapping.json fields, the rest — only kind/jobs/error
    "Acme": {"kind": "ats", "jobs": 3, "error": "", "platform": "ashby", "slug": "acme", "found_at": "2026-10-02"},
    "Initech": {"kind": "ats", "jobs": 0, "error": "", "checked_at": "2026-11-01",
                "empty_board": "workable/initech", "platform": None},
    "Umbrella": {"kind": "career_page", "jobs": 0, "error": ""},
    "example-board": {"kind": "aggregator", "jobs": 0, "error": "HTTPError: 503"},
}


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "2026-11-17.json").write_text(json.dumps(JOBS[:1]), encoding="utf-8")
    (jobs / "2026-11-18.json").write_text(json.dumps(JOBS, ensure_ascii=False), encoding="utf-8")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "jobs_2026-11-18.html").write_text("<html></html>", encoding="utf-8")
    cv = tmp_path / "cv_out" / "2026-11-18"
    cv.mkdir(parents=True)
    (cv / "acme_python.pdf").write_bytes(b"%PDF-1.4")
    (cv / "acme_python.md").write_text("<!-- vacancy: Acme — Senior Python Developer — https://example.com/1\n",
                                       encoding="utf-8")
    (tmp_path / "sources_latest.json").write_text(json.dumps(SOURCES), encoding="utf-8")
    monkeypatch.setattr(config, "JOBS_ARCHIVE_DIR", jobs)
    monkeypatch.setattr(config, "MAIL_CACHE_FILE", tmp_path / "mail_cache.json")
    monkeypatch.setattr(config, "MANUAL_APPLICATIONS_FILE", tmp_path / "applications_manual.toml")
    monkeypatch.setattr(config, "MAPPING_FILE", tmp_path / "ats_mapping.json")
    monkeypatch.setattr(progress, "PROGRESS_FILE", tmp_path / "progress.json")
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setattr(web, "REPORTS", reports)
    monkeypatch.setattr(web, "SOURCES_FILE", tmp_path / "sources_latest.json")
    monkeypatch.setattr(web, "FREEHIRE_FILE", tmp_path / "freehire_latest.json")
    monkeypatch.setattr(web.cache, "WEB_CACHE_FILE", tmp_path / "web_cache.json")  # /api/companies, /api/skills… cache
    monkeypatch.setattr(web.cache, "_memory_cache", {})


@pytest.fixture(autouse=True)
def writes(data, tmp_path, monkeypatch):
    """Everything the write endpoints touch goes to tmp; processes and network are stubbed."""
    reports = tmp_path / "reports"
    monkeypatch.setattr(config, "REPORTS_DIR", reports)  # applications.build_page -> reports/applications.html
    monkeypatch.setattr(config, "MAIL_SYNC_FILE", tmp_path / "mail_sync.json")
    monkeypatch.setattr(relevance, "OVERRIDES_FILE", tmp_path / "profile_overrides.json")
    monkeypatch.setattr(progress, "HISTORY_FILE", tmp_path / "progress_history.json")
    monkeypatch.setattr(progress, "CONTROL_FILE", tmp_path / "progress_control.json")
    monkeypatch.setattr(levels, "CACHE_FILE", tmp_path / "levels_cache.json")
    monkeypatch.setattr(cv_match, "CACHE_FILE", tmp_path / "cv_text.json")  # CV PDF text cache
    monkeypatch.setattr(cvgen, "OUT_DIR", tmp_path / "cv_out")  # cover letters too: cover_letter.save puts them there
    runner = web.Runner(web.ROOT, tmp_path / "last_run.log")  # the log is in tmp
    monkeypatch.setattr(runner, "_run", lambda commands: None)  # the queue is accepted, scripts do not start
    monkeypatch.setattr(web, "runner", runner)

    def no_network(url, http=None):
        raise vacancy_lookup.VacancyNotFound(f"pages are not read in tests: {url}")
    monkeypatch.setattr(vacancy_lookup, "fetch", no_network)

    def fake_cv(company, title, text, url="", one_page=False, out_dir=None, **kwargs):  # Chrome builds the PDF — no Chrome in CI
        return {"pdf": "cv_out/x.pdf", "md": "cv_out/x.md", "pages": 1, "fit": 50, "missing": [], "techs": [],
                "topics": [], "picks": []}
    monkeypatch.setattr(cvgen, "build_cv", fake_cv)


@READ_ONLY.parametrize()
@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
def test_read_only_api_matches_schema(case):
    case.call_and_validate()


write_schema = schemathesis.openapi.from_asgi("/openapi.json", web.app)
write_schema.config.generation.update(modes=list(schemathesis.GenerationMode))  # valid and invalid bodies
WRITES = write_schema.include(method=["POST", "PUT", "DELETE"])
# rules the schema does not have: "a link or a vacancy text", "an applications run without mail has nothing
# to run", "pattern is a valid regex". The body is valid by the schema, and the server honestly answers 422. For
# these POSTs "a valid body is accepted" (ST202) is not checked — the rules are tested by hand below; "an invalid
# body is rejected" and "no 500" — as for everyone
URL_OR_TEXT = {"/api/cv", "/api/cover", "/api/prep"}
NOT_IN_SCHEMA = URL_OR_TEXT | {"/api/runs", "/api/skills"}


@WRITES.parametrize()
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
def test_write_api_rejects_bad_input(case):
    rule = case.operation.method.upper() == "POST" and case.operation.path in NOT_IN_SCHEMA
    skip = [schemathesis.checks.positive_data_acceptance] if rule else []
    case.call_and_validate(excluded_checks=skip)


def test_write_endpoints_are_covered():
    paths = {(op.method.upper(), op.path) for op in (r.ok() for r in WRITES.get_all_operations())}
    assert {("POST", "/api/runs"), ("POST", "/api/runs/current/skip"), ("POST", "/api/runs/current/stop"),
            ("POST", "/api/cv"), ("POST", "/api/cover"), ("POST", "/api/prep"), ("POST", "/api/skills"),
            ("PUT", "/api/skills/{name}"), ("DELETE", "/api/skills/{name}"),
            ("POST", "/api/applications/events")} <= paths


@pytest.mark.parametrize("path", sorted(URL_OR_TEXT))
def test_vacancy_needs_url_or_text(path):
    client = TestClient(web.app)
    assert client.post(path, json={}).status_code == 422
    assert client.post(path, json={"url": "https://example.com/nope"}).status_code == 404  # network is stubbed
    r = client.post(path, json={"url": "https://example.com/1"})  # from the vacancy archive in tmp
    assert r.status_code == 200, r.text


def test_runs_without_processes():
    """A run in the test: the queue is accepted, scripts do not start (Runner._run is stubbed); stop/skip without a run — 409."""
    client = TestClient(web.app)
    assert client.post("/api/runs", json={"kind": "applications", "skip": ["mail"]}).status_code == 422
    r = client.post("/api/runs", json={"kind": "main", "skip": ["telegram"]})
    assert r.status_code == 202 and r.json()["scripts"] == ["main.py --no-telegram"]
    web.runner.thread.join(1)
    assert client.post("/api/runs/current/stop").status_code == 409
    assert client.post("/api/runs/current/skip", json={"stage": "ats"}).status_code == 409
    assert web.runner.log.parent != config.ROOT  # the log is in tmp too


def test_bad_text_does_not_wipe_files():
    """A lone surrogate in JSON ("\\ud800") used to wipe applications_manual.toml: write_text truncated the file
    and failed on encoding. Now it is a 422 on input, and writing to the file encodes the text before opening it."""
    from jobhunter import manual_apps

    client = TestClient(web.app)
    assert client.post("/api/applications/events", json={"company": "Acme", "kind": "applied"}).status_code == 201
    before = config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8")
    bad = '{"company": "Globex", "kind": "other", "note": "' + chr(92) + 'ud800"}'
    r = client.post("/api/applications/events", content=bad.encode(), headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    with pytest.raises(ValueError):
        manual_apps.add_event("Globex", "other", chr(0xD800))
    assert config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8") == before
    assert client.post("/api/skills", json={"name": "Go", "pattern": "golang"}).status_code == 201
    assert client.post("/api/skills", json={"name": "go", "pattern": "go"}).status_code == 409
    assert client.post("/api/skills", json={"name": "Rust", "pattern": "(rust"}).status_code == 422
    with pytest.raises(UnicodeEncodeError):
        relevance.write_overrides({"skills": {}, "added": [{"name": chr(0xD800)}]})
    assert relevance.read_overrides()["added"][0]["name"] == "Go"


def test_del_char_in_note_is_valid_toml():
    """DEL (\\x7f) in a note: json.dumps does not escape it, TOML forbids it — the event was not written."""
    client = TestClient(web.app)
    r = client.post("/api/applications/events", json={"company": "Acme", "kind": "other", "note": "a" + chr(0x7F)})
    assert r.status_code == 201, r.text


def test_strict_input_types():
    """bool is only true/false, a weight is not a bool, an event date is only an ISO string."""
    client = TestClient(web.app)
    assert client.post("/api/skills", json={"name": "Elixir", "pattern": "elixir", "weight": True}).status_code == 422
    assert client.post("/api/applications/events", json={"company": "Acme", "kind": "applied", "date": 0}).status_code == 422
    assert client.post("/api/cv", json={"text": "Python", "one_page": "yes"}).status_code == 422


def test_link_filter_is_linear():
    """The link filter in cv_match is linear on a long word without spaces (it was O(n²): 65 KB — 9 s)."""
    import time
    from types import SimpleNamespace

    profile = cv_match.CvProfile({})
    start = time.perf_counter()
    profile._unknown_terms(SimpleNamespace(company="Acme", title="Engineer"), "Uses Kafka " + "a" * 100_000)
    assert time.perf_counter() - start < 2


def test_method_not_allowed_lists_every_method():
    """GET and POST /api/cv are different routes: Allow used to name only the first one."""
    r = TestClient(web.app).delete("/api/cv")
    assert r.status_code == 405
    assert {"GET", "POST"} <= set(r.headers["allow"].split(", "))


def test_old_event_date_does_not_break_page():
    """A date before 1970: on Windows datetime.astimezone() failed with OSError — the applications page gave a 500
    on every build until the event was removed from the file by hand."""
    r = TestClient(web.app).post("/api/applications/events",
                                 json={"company": "Acme", "kind": "applied", "date": "0001-01-01T00:00:00"})
    assert r.status_code == 201, r.text
    assert (config.REPORTS_DIR / "applications.html").exists()


def test_read_only_endpoints_are_covered():
    """A new GET endpoint joins the check by itself; these must not drop out of it by accident."""
    paths = {op.path for op in (r.ok() for r in READ_ONLY.get_all_operations())}
    assert {"/api/market", "/api/companies", "/api/progress", "/api/reports", "/api/sources",
            "/api/cv", "/api/skills"} <= paths
    assert "/api/progress/stream" not in paths


def test_market_without_archive(tmp_path, monkeypatch):
    """analyze() without vacancies returns only day/total/relevant: the model must not require the rest."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(config, "JOBS_ARCHIVE_DIR", empty)
    r = TestClient(web.app).get("/api/market")
    assert r.status_code == 200
    assert r.json()["total"] == 0 and r.json()["keywords"] == [] and r.json()["hygiene"] is None
