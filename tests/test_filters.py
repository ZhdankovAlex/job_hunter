"""Vacancy selection: role, employment, location (settings — settings.example.toml)."""
import pytest

from jobhunter import filters
from jobhunter.models import Job


def make(title, location="Remote", description="Python, PostgreSQL. Full-time.", employment=""):
    return Job(company="Acme", title=title, location=location, url=f"https://acme.example/{title}",
               source="test", description=description, employment=employment)


@pytest.mark.parametrize("title", ["Senior Python Developer", "Backend Engineer (Python)", "Software Engineer"])
def test_backend_roles_pass(title):
    job = make(title)
    assert filters.apply(job), job.reject


@pytest.mark.parametrize("title", ["Frontend Engineer", "Junior Python Developer", "iOS Developer", "QA Engineer",
                                   "Sales Engineer"])
def test_excluded_roles(title):
    job = make(title)
    assert not filters.apply(job)
    assert job.reject == ""  # wrong role — not shown in "Rejected"


def test_part_time_is_rejected_with_reason():
    job = make("Python Developer", employment="Part-time")
    assert not filters.apply(job)
    assert job.reject.startswith("not full-time")


def test_no_url_is_skipped():
    job = make("Python Developer")
    job.url = ""
    assert not filters.apply(job)
