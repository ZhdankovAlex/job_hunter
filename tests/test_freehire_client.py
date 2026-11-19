"""freehire: API client — cache, request limit, errors, meta.ignored_params, paging, diagnostics of zero results."""
import json

import pytest
from conftest import RouteHttp

from jobhunter import freehire, geo
from jobhunter.http import FetchError

SEARCH = freehire.BASE_URL + "/agent/jobs/search"


@pytest.fixture(autouse=True)
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(freehire, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(freehire, "SAVED_FILE", tmp_path / "state" / "freehire_jobs.json")
    return tmp_path


def body(data=(), total=0, **meta):
    return json.dumps({"data": list(data), "meta": {"total": total, **meta}})


def raw(slug, **over):
    return {"public_slug": slug, "title": "Python Developer", "company": "Acme", "company_slug": "acme",
            "skills": ["Python"], "url": f"https://acme.example/{slug}", "description": "short", **over}


# --- _get ---------------------------------------------------------------------------------------

def test_cache_saves_requests():
    http = RouteHttp(lambda m, u, p: body(total=5))
    a = freehire.Freehire(http, {})
    assert a._get("/jobs/search", {"q": "python"})["meta"]["total"] == 5
    b = freehire.Freehire(http, {})  # a new run, the cache is still fresh
    b._get("/jobs/search", {"q": "python"})
    assert len(http.calls) == 1 and b.cache_hits == 1 and b.requests == 0


def test_stale_cache_is_refetched(monkeypatch):
    http = RouteHttp(lambda m, u, p: body())
    freehire.Freehire(http, {"cache_hours": 1})._get("/jobs/search", {})
    real_time = freehire.time.time
    monkeypatch.setattr(freehire.time, "time", lambda: real_time() + 2 * 3600)
    freehire.Freehire(http, {"cache_hours": 1})._get("/jobs/search", {})
    assert len(http.calls) == 2


def test_budget_per_run():
    http = RouteHttp(lambda m, u, p: body())
    fh = freehire.Freehire(http, {"max_requests": 2})
    fh._get("/jobs/search", {"page": "1"})
    fh._get("/jobs/search", {"page": "2"})
    with pytest.raises(freehire.BudgetExceeded):
        fh._get("/jobs/search", {"page": "3"})
    assert len(http.calls) == 2


@pytest.mark.parametrize("answer, match", [((500, "boom"), "HTTP 500 boom"), ("<html>", "not JSON")])
def test_bad_answer_is_fetch_error(answer, match, files):
    fh = freehire.Freehire(RouteHttp(lambda m, u, p: answer), {})
    with pytest.raises(FetchError, match=match):
        fh._get("/jobs/search", {})
    assert not list(files.glob("cache/*.json"))  # an error answer is not cached


def test_ignored_params_stop_and_are_not_cached(files):
    http = RouteHttp(lambda m, u, p: body(ignored_params=["seniorty"]))
    with pytest.raises(freehire.IgnoredParamsError, match="seniorty"):
        freehire.Freehire(http, {})._get("/jobs/search", {"seniorty": "senior"})
    assert list((files / "cache").glob("*.json")) == []


# --- collect --------------------------------------------------------------------------------------

def test_collect_pages_dedupes_and_saves(files):
    def route(m, u, p):
        if p.get("skills") == "python":
            offset = int(p["offset"])
            return body([raw(f"py-{offset + i}") for i in range(100 if offset == 0 else 30)], total=130)
        return body([raw("py-0"), raw("go-1")], total=2)

    http = RouteHttp(route)
    settings = {
        "common": {"work_mode": "remote", "empty": ""},
        "pages_per_query": 5,
        "queries": [{"name": "python", "skills": "python"}, {"name": "backend", "category": "backend"}],
    }
    fh = freehire.Freehire(http, settings)
    jobs = fh.collect()
    assert len(jobs) == 131
    # the second page is offset 100, there is no third: total 130
    assert [c[2]["offset"] for c in http.calls] == ["0", "100", "0"]
    first = http.calls[0][2]
    assert first["work_mode"] == "remote" and "empty" not in first and first["limit"] == "100"
    assert "name" not in first and fh.query_params["python"] == {"work_mode": "remote", "skills": "python"}
    assert fh.totals == {"python": 130, "backend": 2}
    dup = next(j for j in jobs if j.extra["public_slug"] == "py-0")
    assert dup.extra["queries"] == ["python", "backend"]
    saved = json.loads((files / "state" / "freehire_jobs.json").read_text(encoding="utf-8"))
    assert len(saved) == 131


def test_collect_skips_relocation_queries_when_disabled(monkeypatch):
    monkeypatch.setattr(geo, "RELOCATION_ENABLED", False)
    http = RouteHttp(lambda m, u, p: body([raw("a")], total=1))
    jobs = freehire.Freehire(http, {"queries": [
        {"name": "reloc", "requires_relocation": True, "relocation": "true"},
        {"name": "remote", "work_mode": "remote"},
    ]}).collect()
    assert len(jobs) == 1 and len(http.calls) == 1 and http.calls[0][2]["work_mode"] == "remote"


def test_collect_keeps_what_it_got_on_error():
    def route(m, u, p):
        return body([raw(f"a-{i}") for i in range(100)], total=500) if p["offset"] == "0" else (502, "down")
    jobs = freehire.Freehire(RouteHttp(route), {"queries": [{"name": "q"}], "pages_per_query": 3}).collect()
    assert len(jobs) == 100


def test_collect_stops_on_skip_button(monkeypatch):
    monkeypatch.setattr(freehire.progress, "skip_requested", lambda: True)
    http = RouteHttp(lambda m, u, p: body())
    assert freehire.Freehire(http, {"queries": [{"name": "q"}]}).collect() == []
    assert http.calls == []


# --- fill_descriptions ----------------------------------------------------------------------------

def test_fill_descriptions_by_company_batches():
    long_text = "Full description. " * 100
    jobs = [freehire.to_job(raw(f"s{i}", company_slug=f"c{i:02d}"), "q") for i in range(12)]
    jobs.append(freehire.to_job(raw("no-company", company_slug=None), "q"))

    def route(m, u, p):
        asked = p["company_slug"].split(",")
        found = [raw(f"s{i}", description=long_text) for i in range(12) if f"c{i:02d}" in asked]
        return body([*found, raw("other", description="x")])

    http = RouteHttp(route)
    assert freehire.Freehire(http, {}).fill_descriptions(jobs) == 12
    assert len(http.calls) == 2  # companies in batches of 10
    assert http.calls[0][2]["company_slug"].count(",") == 9
    assert jobs[0].description.startswith("Full description.") and jobs[0].description.endswith("Skills: Python")
    assert jobs[0].extra["full_description"] is True
    assert jobs[-1].description.startswith("short") and not jobs[-1].extra["full_description"]


def test_fill_descriptions_survives_errors():
    jobs = [freehire.to_job(raw("s1"), "q")]
    assert freehire.Freehire(RouteHttp(lambda m, u, p: (500, "")), {}).fill_descriptions(jobs) == 0


# --- diagnose_empty -------------------------------------------------------------------------------

def _diag(route, params, common=None):
    fh = freehire.Freehire(RouteHttp(route), {"common": common or {}})
    fh.totals = {"q": 0, "fine": 7}
    fh.query_params = {"q": params, "fine": {"x": "1"}}
    fh.diagnose_empty()
    return fh


def test_diagnose_finds_culprit_and_facets():
    def route(m, u, p):
        if u.endswith("/jobs/facets"):
            return json.dumps({"data": {"facets": {"country": {"de": 5, "nl": 9, "pt": 1}}}})
        return body(total=0 if "country_exclude" in p else 40)

    fh = _diag(route, {"work_mode": "remote", "skills": "python", "country_exclude": "rs"},
               common={"work_mode": "remote"})
    text = fh.diagnosis["q"]
    assert text.startswith("zeroed by the filter country_exclude=rs")
    assert "nl (9), de (5), pt (1)" in text
    # query parameters are checked before the common ones: work_mode was never reached
    assert "+skills=python → 40; +country_exclude=rs → 0" in text and "+work_mode" not in text
    assert "fine" not in fh.diagnosis


def test_diagnose_no_single_culprit_and_unknown_facet():
    assert "no single filter" in _diag(lambda m, u, p: body(total=3), {"a": "1"}).diagnosis["q"]

    def route(m, u, p):
        if u.endswith("/jobs/facets"):
            return json.dumps({"data": {"facets": {}}})
        return body(total=0)
    text = _diag(route, {"b": "2"}).diagnosis["q"]  # other parameters — not from the first call's cache
    assert text.startswith("zeroed by the filter b=2. Steps")


def test_diagnose_reports_network_failure():
    assert "diagnostics not finished" in _diag(lambda m, u, p: (503, ""), {"a": "1"}).diagnosis["q"]
