"""ATS: parsing platform responses and finding a company board (AtsClient) — no network, on FakeHttp."""
import json

import pytest
from conftest import FakeHttp, fixture_text

from jobhunter import ats, companies
from jobhunter.models import Company

# --- parsing responses -------------------------------------------------------------------------


def test_greenhouse():
    data = {"jobs": [{"title": "Backend Engineer", "location": {"name": "Remote, EU"},
                      "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "first_published": "2026-11-01",
                      "content": "&lt;p&gt;Python&lt;/p&gt;"}]}
    (job,) = ats._greenhouse(data, "Acme")
    assert (job.title, job.location, job.source) == ("Backend Engineer", "Remote, EU", "greenhouse")
    assert "Python" in job.description and "<p>" not in job.description


def test_lever_collects_lists_and_remote():
    data = [{"text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/1", "createdAt": 1793000000000,
             "workplaceType": "remote", "categories": {"allLocations": ["Warsaw", "Remote"], "commitment": "Full-time"},
             "descriptionPlain": "About us", "lists": [{"text": "Requirements", "content": "<li>asyncio</li>"}],
             "additionalPlain": "Benefits"}]
    (job,) = ats._lever(data, "Acme")
    assert job.location == "Warsaw; Remote" and job.remote is True and job.employment == "Full-time"
    assert "Requirements" in job.description and "asyncio" in job.description and "Benefits" in job.description


def test_ashby_skips_unlisted_and_trusts_workplace_type():
    data = {"jobs": [
        {"title": "Hidden", "isListed": False},
        {"title": "Backend", "location": "Berlin", "secondaryLocations": [{"location": "Remote"}],
         "jobUrl": "https://jobs.ashbyhq.com/acme/1", "workplaceType": "Hybrid", "isRemote": True,
         "descriptionPlain": "Python"},
    ]}
    (job,) = ats._ashby(data, "Acme")
    assert job.title == "Backend" and job.location == "Berlin; Remote"
    assert job.remote is False  # isRemote=true for a hybrid — trust workplaceType


def test_workable_merges_countries_of_one_job():
    """Workable returns a vacancy for several countries as separate records — merged by url."""
    row = {"title": "Python Developer", "url": "https://apply.workable.com/acme/j/1", "telecommuting": True,
           "description": "Python"}
    data = {"jobs": [{**row, "country": "Kazakhstan"}, {**row, "country": "Poland"}]}
    (job,) = ats._workable(data, "Acme")
    assert job.location == "Kazakhstan; Poland (remote)"


def test_recruitee_and_breezy_mark_remote():
    (rec,) = ats._recruitee({"offers": [{"title": "Dev", "city": "Riga", "country": "Latvia", "remote": True,
                                          "careers_url": "https://acme.recruitee.com/o/dev"}]}, "Acme")
    assert rec.location == "Riga, Latvia (remote)" and rec.remote is True
    (br,) = ats._breezy([{"name": "Dev", "url": "https://acme.breezy.hr/p/1",
                          "location": {"name": "Warsaw, Poland", "is_remote": True}}], "Acme")
    assert br.location == "Warsaw, Poland (remote)"


def test_rippling_merges_locations():
    data = [{"uuid": "u1", "name": " Backend ", "url": "https://ats.rippling.com/acme/jobs/u1",
             "workLocation": {"label": "Remote (Europe)"}},
            {"uuid": "u1", "name": "Backend", "url": "https://ats.rippling.com/acme/jobs/u1",
             "workLocation": {"label": "Warsaw"}}]
    (job,) = ats._rippling(data, "Acme")
    assert job.title == "Backend" and job.location == "Remote (Europe); Warsaw" and job.remote is True


# --- finding and updating the board ------------------------------------------------------------

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LEVER = "https://api.lever.co/v0/postings/{}"
LEVER_EU = "https://api.eu.lever.co/v0/postings/{}"


@pytest.fixture
def client(tmp_path):
    def make(pages, mapping=None):
        path = tmp_path / "ats_mapping.json"
        if mapping is not None:
            path.write_text(json.dumps(mapping), encoding="utf-8")
        return ats.AtsClient(FakeHttp(pages), path)
    return make


def lever_json(title="Python Developer"):
    return json.dumps([{"text": title, "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {}}])


def test_discover_finds_board_and_remembers_it(client):
    c = client({LEVER.format("acme"): lever_json()})
    jobs = c.jobs_for(Company("Acme", score=3, note="a note"))
    assert [j.title for j in jobs] == ["Python Developer"]
    assert (jobs[0].score, jobs[0].note) == (3, "a note")
    assert c.mapping["Acme"]["platform"] == "lever" and c.mapping["Acme"]["slug"] == "acme"


def test_empty_board_is_not_a_find(client):
    c = client({GREENHOUSE.format("acme"): json.dumps({"jobs": []})})
    assert c.jobs_for(Company("Acme")) == []
    assert c.mapping["Acme"]["platform"] is None
    assert c.mapping["Acme"]["empty_board"] == "greenhouse/acme"


def test_lever_eu_fallback(client):
    c = client({LEVER_EU.format("acme"): lever_json()}, {"Acme": {"platform": "lever", "slug": "acme"}})
    assert len(c.jobs_for(Company("Acme"))) == 1


def test_teamtailor_own_domain(client):
    c = client({"https://career.initech.example/jobs.rss": fixture_text("teamtailor.rss")},
               {"Initech": {"platform": "teamtailor", "slug": "career.initech.example"}})
    assert len(c.jobs_for(Company("Initech"))) == 2


def test_board_gone_and_not_found_again(client):
    c = client({}, {"Acme": {"platform": "greenhouse", "slug": "acme"}})
    assert c.jobs_for(Company("Acme")) == []
    assert c.mapping["Acme"]["gone"] == "greenhouse/acme"
    assert "is gone" in c.failures["Acme"]


def test_board_moved_is_fixed_silently(client):
    c = client({LEVER.format("acme"): lever_json()}, {"Acme": {"platform": "greenhouse", "slug": "acme"}})
    assert len(c.jobs_for(Company("Acme"))) == 1
    assert c.mapping["Acme"]["platform"] == "lever"


def test_custom_and_manual_entries_are_not_fetched(client):
    c = client({}, {"A": {"platform": "custom"}, "B": {"platform": None, "manual": True}})
    assert c.jobs_for(Company("A")) == [] and c.jobs_for(Company("B")) == []
    assert c.http.calls == []


def test_save_keeps_concurrent_edits(client, tmp_path):
    """Editing ats_mapping.json by hand during a run is not overwritten by saving."""
    c = client({LEVER.format("acme"): lever_json()}, {"Old": {"platform": None}})
    c.jobs_for(Company("Acme"))
    path = tmp_path / "ats_mapping.json"
    path.write_text(json.dumps({"Old": {"platform": None}, "Manual": {"platform": "custom"}}), encoding="utf-8")
    c.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert {"Old", "Manual", "Acme"} <= set(saved)


def test_slug_candidates():
    assert companies.slug_candidates("Grafana Labs")[:3] == ["grafanalabs", "grafana-labs", "grafana"]
