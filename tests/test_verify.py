"""Проверка вакансии по полному тексту (verify.check): скрыть, оштрафовать или отметить для ручной проверки."""
import pytest

from jobhunter import verify
from jobhunter.models import Job

# достаточно длинное описание, чтобы текст считался полным (MIN_TEXT_CHARS)
BODY = " We build a payments platform with Python, FastAPI, PostgreSQL and Kafka." * 12


def check(text, location="Remote", title="Senior Python Developer", full=True):
    job = Job(company="Acme", title=title, location=location, url="https://acme.example/1", source="test",
              description=text)
    verify.check(job, text, full)
    return job


@pytest.mark.parametrize("text,location,reason", [
    ("This role is onsite in our Berlin office 5 days a week.", "Berlin", verify.ONSITE_REASON),
    ("You must be authorized to work in the United States.", "Remote", "привязка к стране"),
    ("Requirements: strong Java and Spring Boot experience.", "Remote", "JVM / Spring"),
    ("Удалённая работа только из России. Python, Django.", "Удалённо", "удалёнка только из России"),
    ("Train AI models as a freelance AI trainer, paid per task.", "Remote", "AI trainer"),
], ids=["onsite", "us-work-authorization", "jvm", "russia-only", "ai-trainer"])
def test_rejects(text, location, reason):
    job = check(text + BODY, location)
    assert job.reject.startswith(reason)


def test_reject_has_quote():
    job = check("This role is onsite in our Berlin office 5 days a week." + BODY, "Berlin")
    assert "onsite" in job.reject_quote.lower()


def test_remote_europe_passes_clean():
    job = check("Fully remote, work from anywhere in Europe." + BODY)
    assert (job.reject, job.manual_check, job.penalties) == ("", "", [])


@pytest.mark.parametrize("years,penalty", [(7, "experience_7"), (9, "experience_8")])
def test_experience_is_a_penalty_not_a_reject(years, penalty):
    job = check(f"Fully remote in Europe. {years}+ years of experience with Python." + BODY)
    assert job.reject == ""
    assert penalty in job.penalties


def test_short_text_needs_manual_check():
    job = check("Python", full=True)
    assert job.manual_check == "текст вакансии недоступен"


def test_remote_without_region_needs_manual_check():
    job = check(BODY)
    assert job.manual_check.startswith("регион remote не указан")
