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


def test_career_page_skips_malformed_link():
    """A link with the host in brackets (http://['website']/) makes urljoin raise ValueError; skip only that link."""
    base = "https://acme.example/careers/"
    page = ('<a href="http://[\'website\']/">Python Developer</a>'
            '<a href="/careers/backend-python/">Senior Backend Engineer (Python)</a>')
    http = FakeHttp({base: page, base + "backend-python/": fixture_text("career_job.html")})
    jobs = sites.career_page(http, SimpleNamespace(name="Acme", score=1, note=""), base, "Remote")
    assert [j.url for j in jobs] == [base + "backend-python/"]


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


def test_uzum_reads_next_flight_payload_and_filters_titles():
    """Uzum: jobs and texts live in the Next.js stream; a description is a "$1d" chunk reference or inline JSON."""
    url = "https://people.uzum.com/career/ru/vacancies?page=10"
    http = FakeHttp({url: fixture_text("uzum_list.html")})
    jobs = {j.title: j for j in sites.uzum(http)}
    assert set(jobs) == {"Senior Python Developer (Uzum Bank)", "Backend Engineer (Go)"}  # the operator role is skipped
    py = jobs["Senior Python Developer (Uzum Bank)"]
    assert py.company == "Uzum" and py.source == "uzum"
    assert py.location == "Remote; Tashkent, Uzbekistan" and py.remote is True
    assert py.url == "https://people.uzum.com/career/ru/vacancies/101"
    assert py.posted is not None and py.posted.date().isoformat() == "2026-11-12"
    assert "FastAPI" in py.description and "Kafka" in py.description and "<li>" not in py.description
    go = jobs["Backend Engineer (Go)"]
    assert go.location == "Hybrid; Tashkent, Uzbekistan" and go.remote is None and go.posted is None
    assert "гибрид" in go.description
    assert http.calls == [url]  # everything is in one response: job pages are not opened


def test_uzum_asks_for_more_when_total_exceeds_page():
    page = fixture_text("uzum_list.html").replace('total\\":3', 'total\\":200')
    base = "https://people.uzum.com/career/ru/vacancies"
    http = FakeHttp({f"{base}?page=10": page, f"{base}?page=14": page})
    assert len(list(sites.uzum(http))) == 2
    assert http.calls == [f"{base}?page=10", f"{base}?page=14"]


def test_yango_follows_cursor_on_own_host_and_reads_vacancy_page():
    """Yango: "next" points to an internal host, so only the cursor is taken; text comes from the job page."""
    api = "https://yango.com/career/api/publications"
    http = FakeHttp({
        api: fixture_text("yango_list_1.json"),
        api + "?cursor=CUR2": fixture_text("yango_list_2.json"),
        "https://yango.com/career/vacancy/101": "<html><body><h1>Senior Python Developer</h1>"
                                                "<p>FastAPI, PostgreSQL</p></body></html>",
        "https://yango.com/career/vacancy/103": "<html><body><p>Go backend</p></body></html>",
    })
    jobs = {j.title: j for j in sites.yango(http)}
    assert set(jobs) == {"Senior Python Developer (Uzbekistan)", "Backend Engineer"}  # support is skipped
    py = jobs["Senior Python Developer (Uzbekistan)"]
    assert py.company == "Yango" and py.source == "yango"
    assert py.location == "Remote; Tashkent" and py.remote is True
    assert py.url == "https://yango.com/career/vacancy/101" and "FastAPI" in py.description
    assert jobs["Backend Engineer"].location == "Almaty" and jobs["Backend Engineer"].remote is None
    assert "https://yango.com/career/vacancy/102" not in http.calls
    assert not any("internal.example" in u for u in http.calls)
