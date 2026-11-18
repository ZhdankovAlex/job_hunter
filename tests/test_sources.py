"""Parsing sources on saved page samples — no network."""
from types import SimpleNamespace

from conftest import FakeHttp, fixture_text

from jobhunter import ats, filters, sites


def test_findev_reads_jobposting_and_skips_pages_without_it():
    http = FakeHttp({
        "https://fin.dev/career/open-positions/": fixture_text("findev_list.html"),
        "https://fin.dev/career/open-positions/python-developer-1": fixture_text("findev_job.html"),
        "https://fin.dev/career/open-positions/broken-2": "<html>no markup</html>",
    })
    jobs = list(sites.findev(http))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Python Developer (Middle/Senior)"
    assert job.location == "Remote: Spain, Poland"
    assert job.remote is True
    assert job.posted.date().isoformat() == "2026-06-30"
    assert "Python" in job.description and "<b>" not in job.description


def test_career_page_links_prefix_skips_menu_ads():
    """"Hire Python Developers" in the menu is an ad, not a vacancy; links keeps only /careers/…"""
    base = "https://globex.example/careers/"
    http = FakeHttp({
        base: fixture_text("career_page.html"),
        base + "ai-engineer-python-llm/": fixture_text("career_job.html"),
        base + "backend-python/": fixture_text("career_job.html"),
    })
    company = SimpleNamespace(name="Globex", score=1, note="")
    jobs = sites.career_page(http, company, base, "Remote", base)
    titles = {j.title for j in jobs}
    assert "AI Engineer (Python / LLM)" in titles
    assert "Senior Backend Engineer (Python)" in titles  # the title from the aria-label of a button without text
    assert not any("Hire" in t for t in titles)
    assert "https://globex.example/hire-python-developers-in-48-hours/" not in http.calls
    assert all(j.location == "Remote" for j in jobs)


def test_career_page_without_prefix_keeps_old_behaviour():
    base = "https://globex.example/careers/"
    http = FakeHttp({base: fixture_text("career_page.html")})
    sites.career_page(http, SimpleNamespace(name="X", score=1, note=""), base)
    assert "https://globex.example/hire-python-developers-in-48-hours/" in http.calls


def test_teamtailor_rss():
    jobs = list(ats._teamtailor(fixture_text("teamtailor.rss"), "Initech"))
    assert [j.title for j in jobs] == ["Python Developer", "Office Manager"]
    py, office = jobs
    assert py.location == "Lisbon, Portugal; Europe (remote)"
    assert py.remote is True
    assert py.posted.year == 2026
    assert office.posted is None and office.remote is None


def test_teamtailor_python_job_passes_filters():
    job = next(ats._teamtailor(fixture_text("teamtailor.rss"), "Initech"))
    job.description += " Full-time. We are looking for a Python backend developer."
    assert filters.apply(job), job.reject


def test_manual_jobs_company_override(monkeypatch):
    """[manual_jobs]: a job on an agency site gets an explicit company — the application status is matched by it."""
    from jobhunter import geo

    plain = "https://acme.example/careers/python"
    agency = "https://agency.example/job/lead-engineer/"
    monkeypatch.setitem(geo.S, "manual_jobs", {"urls": [
        plain, {"url": agency, "company": "Agency Client", "location": "Remote"}]})
    page = "<html><head><title>Lead Engineer - Agency</title></head><body>Python, remote</body></html>"
    jobs = list(sites.manual_jobs(FakeHttp({plain: page, agency: page})))
    assert [(j.company, j.url) for j in jobs] == [("Acme", plain), ("Agency Client", agency)]
    assert jobs[1].title == "Lead Engineer"  # " - Agency" is the site name, cut off
    assert (jobs[1].location, jobs[1].remote, jobs[1].source) == ("Remote", True, "manual")
    assert jobs[0].location == ""  # no location in the entry — as before


def test_fake_and_real_http_satisfy_protocol():
    """HttpClient is a structural type: both Http and FakeHttp fit, though neither inherits from it.

    isinstance with runtime_checkable only checks that the methods exist; mypy compares signatures (conftest.py).
    """
    from jobhunter.http import Http, HttpClient

    assert isinstance(Http(), HttpClient)
    assert isinstance(FakeHttp({}), HttpClient)
    assert not isinstance(object(), HttpClient)
    assert not issubclass(FakeHttp, Http)
    assert hasattr(Http, "post") and not hasattr(FakeHttp, "post")  # the Workday source needs HttpPostClient
