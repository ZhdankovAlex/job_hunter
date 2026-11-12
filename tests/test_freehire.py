"""freehire.me: запись API -> Job, дешёвый предфильтр и разметка локации."""
import pytest

from jobhunter import freehire

TEXT = " We build a payments platform with Python, FastAPI, PostgreSQL and Kafka." * 25


def raw(**over):
    base = {
        "public_slug": "acme-python", "title": "Senior Python Developer", "company": "Acme",
        "location": "", "countries": ["PL"], "regions": ["europe"], "work_mode": "remote",
        "skills": ["Python", "PostgreSQL"], "url": "https://acme.example/jobs/1?utm_source=freehire&ref=x",
        "posted_at": "2026-11-10T10:00:00Z", "description": "Fully remote in Europe." + TEXT,
        "enrichment": {"category": "backend", "experience_years_min": 5, "employment_type": "full_time",
                       "salary_min": 60000, "salary_max": 80000, "salary_currency": "EUR", "salary_period": "year"},
    }
    return {**base, **over}


def test_to_job():
    job = freehire.to_job(raw(), "A")
    assert job.url == "https://acme.example/jobs/1?ref=x"  # utm-метки убраны, остальное — нет
    assert job.location == "PL (remote)" and job.remote is True
    assert job.salary == "EUR 60,000–80,000 / year"
    assert job.description.endswith("Skills: Python, PostgreSQL")
    assert job.extra["countries"] == ["pl"] and job.extra["queries"] == ["A"]
    assert job.extra["full_description"] is True


def test_short_description_is_marked_as_preview():
    assert freehire.to_job(raw(description="short"), "A").extra["full_description"] is False


@pytest.mark.parametrize("over,passes,reason", [
    ({}, True, ""),
    ({"title": "Frontend Developer"}, False, ""),
    ({"enrichment": {"category": "backend", "experience_years_min": 10}}, False, "опыт от 10 лет"),
    ({"enrichment": {"category": "backend", "employment_type": "part_time"}}, False, "не full-time"),
    ({"enrichment": {"category": "sales"}}, False, ""),
], ids=["ok", "frontend", "experience", "part-time", "not-engineering"])
def test_prefilter(over, passes, reason):
    job = freehire.to_job(raw(**over), "A")
    assert freehire.prefilter(job) is passes
    assert job.reject.startswith(reason)


def test_prepare_home_country_is_always_ok():
    job = freehire.to_job(raw(), "A")
    assert freehire.prepare(job), job.reject


def test_prepare_relocation_from_enrichment():
    job = freehire.to_job(raw(countries=["ES"], work_mode="onsite", location="Madrid",
                              description="Office in Madrid." + TEXT,
                              enrichment={"category": "backend", "relocation": "supported"}), "A")
    assert freehire.prepare(job), job.reject
    assert (job.reloc_country, job.reloc_type) == ("Spain", "релокация")


def test_prepare_rejects_foreign_onsite():
    job = freehire.to_job(raw(countries=["US"], work_mode="onsite", location="Austin, TX",
                              description="Onsite in Austin." + TEXT, enrichment={"category": "backend"}), "A")
    assert not freehire.prepare(job)
    assert job.reject.startswith("локация")
