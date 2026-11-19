"""The whole main.run without network: one company on Greenhouse via FakeHttp, everything written goes to tmp.

Guards the split of run() into stages: the same order of progress stages, the reports and the vacancies JSON.
"""
import argparse
import builtins
import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import main
from jobhunter import config, levels, progress, salary
from jobhunter.ats import AtsClient
from jobhunter.models import Company
from tests.conftest import FakeHttp

BOARD = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"


def _board() -> str:
    posted = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    jobs = [
        {"title": "Senior Python Developer", "absolute_url": "https://example.com/jobs/1",
         "location": {"name": "Remote"}, "first_published": posted,
         "content": "<p>We build backend services in Python, FastAPI and PostgreSQL. Fully remote.</p>"},
        {"title": "Office Manager", "absolute_url": "https://example.com/jobs/2",
         "location": {"name": "London"}, "first_published": posted, "content": "<p>Office.</p>"},
    ]
    return json.dumps({"jobs": jobs})


@pytest.fixture
def no_writes_outside(tmp_path, monkeypatch):
    """A write outside tmp_path is an error at once: the run must not touch the real state/ and reports/."""
    def guard(path) -> None:
        p = pathlib.Path(path).resolve()
        if not p.is_relative_to(tmp_path.resolve()):
            raise AssertionError(f"write outside tmp: {p}")

    real_write_text, real_path_open, real_open = pathlib.Path.write_text, pathlib.Path.open, builtins.open

    def write_text(self, *a, **kw):
        guard(self)
        return real_write_text(self, *a, **kw)

    def path_open(self, mode="r", *a, **kw):
        if any(m in mode for m in "wax+"):
            guard(self)
        return real_path_open(self, mode, *a, **kw)

    def open_(file, mode="r", *a, **kw):
        if isinstance(file, (str, pathlib.Path)) and any(m in mode for m in "wax+"):
            guard(file)
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", write_text)
    monkeypatch.setattr(pathlib.Path, "open", path_open)
    monkeypatch.setattr(builtins, "open", open_)


@pytest.fixture
def isolated(tmp_path, monkeypatch, no_writes_outside):
    (tmp_path / "state").mkdir()
    (tmp_path / "ats_mapping.json").write_text(json.dumps({"Acme": {"platform": "greenhouse", "slug": "acme"}}),
                                               encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "JOBS_ARCHIVE_DIR", tmp_path / "jobs")
    monkeypatch.setattr(config, "MAPPING_FILE", tmp_path / "ats_mapping.json")
    monkeypatch.setattr(config, "SEEN_FILE", tmp_path / "state" / "seen.json")
    monkeypatch.setattr(main, "SOURCES_FILE", tmp_path / "state" / "sources_latest.json")
    monkeypatch.setattr(main, "AtsClient", lambda http: AtsClient(http, tmp_path / "ats_mapping.json"))
    monkeypatch.setattr(salary, "SAMPLES_FILE", tmp_path / "state" / "salary_samples.json")
    monkeypatch.setattr(levels, "CACHE_FILE", tmp_path / "state" / "levels_cache.json")
    monkeypatch.setattr(main, "load_companies", lambda: [Company("Acme", score=1)])
    http = FakeHttp({BOARD: _board()})
    monkeypatch.setattr(main, "Http", lambda: http)
    return tmp_path, http


def test_run_offline(isolated, monkeypatch):
    tmp_path, http = isolated
    stages: list[str] = []
    monkeypatch.setattr(progress, "start", lambda key, total=0, note="": stages.append(key))
    monkeypatch.setattr(progress, "skip", lambda key, note="": stages.append(f"-{key}"))
    args = argparse.Namespace(only="", limit=None, rediscover=False, no_aggregators=True, no_freehire=True,
                              no_telegram=True, no_applied=True)

    main.run(args)

    assert stages == ["companies", "-freehire", "sources", "filter", "freehire_texts", "sheets",
                      "-telegram", "-telegram_check", "scoring", "-cv", "reports"]
    assert http.calls[0] == BOARD  # after it — only levels.fyi for the vacancy country (FakeHttp answers 404)
    jobs = json.loads((tmp_path / "state" / "jobs_latest.json").read_text(encoding="utf-8"))
    assert [j["title"] for j in jobs] == ["Senior Python Developer"]
    day = datetime.now().date().isoformat()  # run() uses date.today()
    assert (tmp_path / "jobs" / f"{day}.json").exists()
    html = (tmp_path / "reports" / "latest.html").read_text(encoding="utf-8")
    assert "Senior Python Developer" in html and "Office Manager" not in html
    assert (tmp_path / "reports" / f"jobs_{day}.md").exists()


def test_run_job_without_description(isolated):
    """A vacancy with an empty description does not crash the run: Job has no `text` field (only TG vacancies do)."""
    tmp_path, http = isolated
    board = json.loads(_board())
    board["jobs"][0]["content"] = ""
    http.pages[BOARD] = json.dumps(board)
    args = argparse.Namespace(only="", limit=None, rediscover=False, no_aggregators=True, no_freehire=True,
                              no_telegram=True, no_applied=True)

    main.run(args)

    jobs = json.loads((tmp_path / "state" / "jobs_latest.json").read_text(encoding="utf-8"))
    assert [j["title"] for j in jobs] == ["Senior Python Developer"]
