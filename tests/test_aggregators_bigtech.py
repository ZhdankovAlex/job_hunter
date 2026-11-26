"""Aggregators with an open API (aggregators.py) and big-company career sites (bigtech.py). No network."""
import json
import logging
import re
from datetime import UTC, datetime

import pytest
from conftest import RouteHttp

from jobhunter import aggregators, bigtech, config

# --- aggregators -----------------------------------------------------------------------------


def test_arbeitnow_follows_next_link():
    def route(method, url, params):
        page = params["page"]
        return json.dumps({"data": [{"company_name": "Acme", "title": f"Python Dev {page}", "url": f"u{page}",
                                     "tags": ["python", "django"], "job_types": ["full time"], "remote": True,
                                     "created_at": 1762770000}],
                           "links": {"next": "more" if page < 2 else None}})

    http = RouteHttp(route)
    jobs = aggregators.fetch("arbeitnow", http)
    assert [j.title for j in jobs] == ["Python Dev 1", "Python Dev 2"]
    assert jobs[0].remote is True and jobs[0].employment == "full time" and "python, django" in jobs[0].description
    assert jobs[0].posted is not None and len(http.calls) == 2


def test_remoteok_skips_legal_notice():
    data = [{"legal": "terms"}, {"company": "Acme", "position": "Backend Engineer", "url": "https://remoteok.com/1",
                                 "tags": ["python"], "epoch": 1762770000}]
    jobs = aggregators.fetch("remoteok", RouteHttp(lambda *a: json.dumps(data)))
    assert [(j.company, j.title, j.remote) for j in jobs] == [("Acme", "Backend Engineer", True)]


@pytest.mark.parametrize(("low", "high", "expected"), [
    (None, None, ""),
    (90000, 120000, "USD 90,000–120,000 / year"),
    (100000, 100000, "USD 100,000 / year"),
    (None, 80000, "USD 80,000 / year"),
])
def test_himalayas_salary(low, high, expected):
    assert aggregators._himalayas_salary({"minSalary": low, "maxSalary": high, "currency": "USD"}) == expected


def test_himalayas_location_and_salary(monkeypatch):
    monkeypatch.setattr(config, "HIMALAYAS_QUERIES", [{"q": "python"}])
    data = {"jobs": [
        {"companyName": "Acme", "title": "Python Engineer", "applicationLink": "https://a",
         "locationRestrictions": ["Portugal", "Poland"], "minSalary": 50000, "maxSalary": 70000,
         "currency": "EUR", "salaryPeriod": "year", "employmentType": "Full Time"},
        {"companyName": "Globex", "title": "Backend Engineer", "guid": "https://g"},
    ]}
    http = RouteHttp(lambda *a: json.dumps(data))
    jobs = aggregators.fetch("himalayas", http)
    assert [(j.location, j.url, j.salary) for j in jobs] == [
        ("Portugal, Poland", "https://a", "EUR 50,000–70,000 / year"), ("Worldwide", "https://g", "")]
    assert http.calls[0][2] == {"q": "python", "sort": "recent"}


@pytest.mark.parametrize("answer", [(500, ""), "<html>not JSON</html>"])
def test_aggregator_errors_are_warnings(answer, caplog):
    with caplog.at_level(logging.WARNING):
        assert aggregators.fetch("remoteok", RouteHttp(lambda *a: answer)) == []
    assert "aggregator remoteok" in caplog.text


# --- bigtech: the order of opening vacancies ---------------------------------------------------

@pytest.fixture
def home(monkeypatch):
    """A home country is set ([home] in settings.toml); the example settings leave it empty."""
    monkeypatch.setattr(bigtech.c, "HOME_COUNTRY", "Portugal")
    monkeypatch.setattr(bigtech.c, "HOME_RX", re.compile(r"portugal|lisbon", re.I))


def test_priority_home_then_remote_then_europe_then_fresh(home):
    old, new = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 10, tzinfo=UTC)
    places = [("Austin, US", new), ("Europe", new), ("Remote", old), ("Lisbon, Portugal", old),
              ("Austin, US", old), ("Austin, US", None)]
    order = sorted(places, key=lambda x: bigtech._priority(*x))
    assert order == [("Lisbon, Portugal", old), ("Remote", old), ("Europe", new),
                     ("Austin, US", new), ("Austin, US", old), ("Austin, US", None)]


def test_pick_filters_titles_and_limits(home, use_settings):
    use_settings(bigtech={"details_per_company": 2})
    cards = {i: {"title": t, "location": loc, "posted": None} for i, (t, loc) in enumerate([
        ("Senior Python Engineer", "Austin, US"), ("Account Executive", "Lisbon, Portugal"),
        ("Backend Engineer", "Lisbon, Portugal"), ("Software Engineer, Backend", "Remote")])}
    assert [c["title"] for c in bigtech._pick(cards)] == ["Backend Engineer", "Software Engineer, Backend"]


@pytest.mark.parametrize(("text", "days"), [
    ("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3), ("Posted 30+ Days Ago", 30)])
def test_workday_posted(text, days):
    posted = bigtech._workday_posted(text)
    assert posted is not None and (datetime.now(UTC) - posted).days == days


def test_workday_posted_unknown():
    assert bigtech._workday_posted("") is None and bigtech._workday_posted("soon") is None


@pytest.fixture
def one_query(use_settings):
    use_settings(bigtech={"queries": ["python"], "pages": 2})


# --- bigtech: sources --------------------------------------------------------------------------

def test_microsoft_dedupes_cards_and_skips_broken_details(home, one_query, caplog):
    position = {"id": 1, "name": "Senior Backend Engineer", "locations": ["Lisbon, Portugal"],
                "postedTs": 1762770000, "workLocationOption": "onsite"}
    other = {"id": 2, "name": "Software Engineer - Python", "locations": ["Remote"], "workLocationOption": "remote"}

    def route(method, url, params):
        if url.endswith("/search"):  # both searches (python and the home country) return vacancy 1
            return json.dumps({"data": {"positions": [position, other] if params["query"] else [position]}})
        if params["position_id"] == 1:
            return json.dumps({"data": {"jobDescription": "<p>C# and Python</p>", "publicUrl": "https://ms/1"}})
        return "not json"

    http = RouteHttp(route)
    with caplog.at_level(logging.WARNING):
        jobs = list(bigtech.microsoft(http))
    assert [(j.title, j.url, j.description) for j in jobs] == [
        ("Senior Backend Engineer", "https://ms/1", "C# and Python")]
    assert "microsoft 2" in caplog.text
    searches = [p for _, url, p in http.calls if url.endswith("/search")]
    assert len(searches) == 2  # fewer than 10 on a page: the next page is not requested


def test_workday_search_by_post_then_details(one_query):
    source = bigtech.workday("Acme", "acme.wd5", "acme", "External")
    api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External"

    def route(method, url, data):
        if method == "POST":
            assert url == f"{api}/jobs" and data["searchText"] == "python"
            return json.dumps({"jobPostings": [
                {"externalPath": "/job/1", "title": "Python Engineer", "locationsText": "Remote", "postedOn": "Posted Today"},
                {"externalPath": "/job/2", "title": "Recruiter", "locationsText": "Lisbon"},
                {"title": "no link"},
            ]})
        assert url == f"{api}/job/1"
        return json.dumps({"jobPostingInfo": {"title": "Senior Python Engineer", "location": "Lisbon, Portugal",
                                              "additionalLocations": ["Remote, Europe"], "timeType": "Full time",
                                              "jobDescription": "<p>FastAPI</p>", "startDate": "2026-11-10"}})

    jobs = list(source(RouteHttp(route)))
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.title, job.location, job.remote, job.employment) == (
        "Senior Python Engineer", "Lisbon, Portugal; Remote, Europe", True, "Full time")
    assert job.url == "https://acme.wd5.myworkdayjobs.com/External/job/1"


def test_workday_http_error(one_query):
    source = bigtech.workday("Acme", "acme.wd5", "acme", "External")
    with pytest.raises(bigtech.FetchError, match="workday Acme: HTTP 403"):
        list(source(RouteHttp(lambda *a: (403, ""))))


def test_oracle_reads_requisitions_and_details(one_query):
    def route(method, url, params):
        if url.endswith("/recruitingCEJobRequisitions"):
            return json.dumps({"items": [{"requisitionList": [
                {"Id": "77", "Title": "Python Developer", "PrimaryLocation": "Lisbon, Portugal",
                 "secondaryLocations": [{"Name": "Remote"}], "PostedDate": "2026-11-10", "WorkplaceType": ""}]}]})
        assert 'Id="77"' in params["finder"]
        return json.dumps({"items": [{"ExternalDescriptionStr": "<p>About</p>", "ExternalQualificationsStr": "Python",
                                      "WorkplaceType": "Remote", "JobSchedule": "Full time"}]})

    jobs = list(bigtech.oracle(RouteHttp(route)))
    assert [(j.title, j.location, j.remote, j.employment) for j in jobs] == [
        ("Python Developer", "Lisbon, Portugal; Remote", True, "Full time")]
    assert "About" in jobs[0].description and "Python" in jobs[0].description


def test_amazon_dedupes_and_parses_date(one_query):
    found = [{"id_icims": "1", "title": "Software Development Engineer, Python", "normalized_location": "Remote, US",
              "posted_date": "November 10, 2026", "job_path": "/en/jobs/1", "description": "Build",
              "basic_qualifications": "Python"},
             {"id_icims": "2", "title": "Warehouse Associate"},
             {"id_icims": "1", "title": "Software Development Engineer, Python", "posted_date": "bad"}]
    http = RouteHttp(lambda *a: json.dumps({"jobs": found}))
    jobs = list(bigtech.amazon(http))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://www.amazon.jobs/en/jobs/1" and job.remote is True
    assert job.posted == datetime(2026, 11, 10, tzinfo=UTC) and job.description == "Build\nPython\n"
    assert len(http.calls) == 1


def test_workday_companies_from_settings_become_sources():
    assert "workday:NVIDIA" in bigtech.sources()
