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


def test_cv_list_refreshes_after_build(client):
    """After a build "Built CVs" updates without a reload: cv.js takes the #cv-built section from a fresh /cv
    (also when the list was empty — then the table replaces the empty state)."""
    http, _, _ = client
    assert 'id="cv-built"' in http.get("/cv").text
    js = http.get("/static/cv.js").text
    assert "getElementById('cv-built')" in js and "await refreshList()" in js


def test_foreign_host_rejected(client):
    """DNS rebinding: evil.test resolves to 127.0.0.1 and the browser sends Host: evil.test — rejected."""
    c, _, _ = client
    assert c.get("/progress", headers={"host": "evil.test"}).status_code == 400
    assert c.get("/progress", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_cross_site_post_rejected(client):
    c, _, _ = client
    r = c.post("/api/runs/current/stop", headers={"origin": "https://evil.test"})
    assert r.status_code == 403
    r = c.post("/api/runs/current/stop", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_post_passes(client):
    """Own Origin reaches the handler: no run going — 409."""
    c, _, _ = client
    headers = {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765", "sec-fetch-site": "same-origin"}
    assert c.post("/api/runs/current/stop", headers=headers).status_code == 409


@pytest.mark.parametrize("url", ["foo", "ftp://example.com/job", "https://", "http://[bad"])
def test_bad_vacancy_url_is_422_not_500(client, url):
    """A link without http(s) never reaches Http: 422 with a clear message (was a 500 from requests.MissingSchema)."""
    c, _, _ = client
    for path in ("/api/cv", "/api/prep", "/api/cover"):
        r = c.post(path, json={"url": url})
        assert r.status_code == 422, (path, r.text)
        assert "http://" in r.json()["detail"]
    page = c.get("/prep", params={"url": url})
    assert page.status_code == 200 and "http://" in page.text


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765/api/runs", "http://169.254.169.254/latest/meta-data",
                                 "http://192.168.1.1/", "http://router.lan/", "http://[::1]/", "http://0.0.0.0/"])
def test_vacancy_url_in_local_network_is_422(client, monkeypatch, url):
    """A link to this computer or the LAN is not read — 422 before any request (DNS is faked in conftest)."""
    c, web, _ = client
    fetched = []
    monkeypatch.setattr(web.vacancy_lookup, "fetch", lambda u, http=None: fetched.append(u))
    for path in ("/api/cv", "/api/prep", "/api/cover"):
        r = c.post(path, json={"url": url})
        assert r.status_code == 422, (path, r.text)
        assert "local network" in r.json()["detail"]
    page = c.get("/prep", params={"url": url})
    assert page.status_code == 200 and "local network" in page.text
    assert fetched == []


def test_vacancy_lookup_fetch_is_public_only(monkeypatch):
    from jobhunter import vacancy_lookup

    with pytest.raises(vacancy_lookup.VacancyNotFound, match="local network"):
        vacancy_lookup.fetch("http://10.0.0.1/job")
