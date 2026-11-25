"""Smoke test of the web UI on empty data — as on a clean machine: pages open, the cache works."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import web.app as web
    from jobhunter import config

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(config, "JOBS_ARCHIVE_DIR", jobs)
    monkeypatch.setattr(config, "MAIL_CACHE_FILE", tmp_path / "mail_cache.json")
    monkeypatch.setattr(config, "MANUAL_APPLICATIONS_FILE", tmp_path / "applications_manual.toml")
    monkeypatch.setattr(web.cache, "WEB_CACHE_FILE", tmp_path / "web_cache.json")
    monkeypatch.setattr(web.cache, "_memory_cache", {})
    return TestClient(web.app), web, jobs


@pytest.mark.parametrize("path", ["/pipeline", "/applications", "/progress", "/skills", "/api/skills", "/cv", "/cover", "/prep",
                                  "/reports", "/market", "/api/market", "/companies", "/api/companies"])
def test_pages_open_without_data(client, path):
    http, _, _ = client
    assert http.get(path).status_code == 200


def test_skills_counts_are_cached_per_pattern(client):
    http, web, jobs = client
    (jobs / "2026-11-12.json").write_text(json.dumps([
        {"title": "Python Backend Developer", "company": "A", "text": "Python and PostgreSQL"},
        {"title": "Python Developer", "company": "B", "text": "Python, Kafka"},
    ]), encoding="utf-8")
    first = http.get("/api/skills").json()
    assert first["relevant"] == 2
    python = next(s for s in first["skills"] if s["name"] == "Python")
    assert python["matches"] == 2 and python["share"] == 100
    cached = web.cache._memory_cache["skills"]["value"]["matches"]
    assert python["pattern"] in cached
    assert http.get("/api/skills").json() == first  # the second time — from the cache, the same answer
    assert json.loads(web.cache.WEB_CACHE_FILE.read_text(encoding="utf-8"))["skills"]["value"]["relevant"] == 2


def test_reports_are_not_cached_by_browser(client):
    """Reports are rebuilt under the same name: the browser must check with the server every time."""
    http, _, _ = client
    assert http.get("/files/reports/no-such-report.html").headers["cache-control"] == "no-cache"
    assert "cache-control" not in http.get("/pipeline").headers


@pytest.mark.parametrize("path", ["/profile", "/cv", "/cover", "/prep", "/market", "/companies", "/skills", "/sources"])
def test_page_head_rendered(client, path):
    """The ui.page_head macro: a page starts with the standard header with h1 — on empty data too."""
    http, _, _ = client
    main = http.get(path).text.split('<main class="page">', 1)[1].lstrip()
    assert main.startswith('<header class="page-head">'), main[:200]
    assert "<h1>" in main.split("</header>", 1)[0]


@pytest.mark.parametrize("path", ["/market"])  # "Applications" and "Progress" read reports/ — not substituted here
def test_empty_state_without_data(client, path):
    """No data — the ui.empty state with a hint on what to do, not a bare line."""
    http, _, _ = client
    assert '<div class="empty">' in http.get(path).text
