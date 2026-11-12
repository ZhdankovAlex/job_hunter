"""Резюме под вакансию (cvgen, база — cv/base.example.toml) и сборка отчёта (report, html_report)."""
from datetime import date, datetime, timezone

import pytest

from jobhunter import cvgen, html_report, report
from jobhunter.models import Job

VACANCY = ("We are looking for a Senior Software Engineer. Requirements: Python, PostgreSQL, Redis, Docker, "
           "Kafka. You will design services that process millions of tasks a day.")


@pytest.fixture(scope="module")
def base():
    return cvgen.load_base()


def test_build_markdown_for_vacancy(base):
    md = cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY)
    assert md.startswith("<!-- вакансия: Acme")  # служебная пометка: под какую вакансию собрано
    assert "\n# " in md and "## Education" in md
    assert "Python" in md and "PostgreSQL" in md
    assert "Senior Software Engineer (Backend)" in md  # заголовок подстроен под название вакансии


def test_one_page_variant_is_shorter(base):
    full = cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY)
    short = cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY, limits=cvgen.Limits(compact=True))
    assert len(short) <= len(full)


def test_picks_explain_choice(base):
    picks = []
    cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY, picks=picks)
    assert picks, "должны записываться выбранные пункты и почему"


def test_markdown_to_html(base):
    html = cvgen.markdown_to_html(cvgen.build_markdown(base, "Acme", "Backend Engineer", VACANCY))
    assert "<h1>" in html and "</html>" in html


@pytest.mark.parametrize("title,expected_role", [
    ("Software Engineer, Payments", "Senior Software Engineer (Backend)"),
    ("Office Manager", "Senior Backend Engineer"),  # нет совпадения — заголовок как в базе
])
def test_headline_for(title, expected_role):
    assert cvgen.headline_for(title, "Senior Backend Engineer | Python · PostgreSQL").startswith(expected_role)


def test_slug():
    assert cvgen.slug("Acme / Senior Python Developer (Remote)") == "Acme_Senior_Python_Developer_Remote"


# --- отчёт -----------------------------------------------------------------------------------

def job(title, company="Acme", **kw):
    return Job(company=company, title=title, location="Remote", url=f"https://{company.lower()}.example/{title}",
               source="test", posted=datetime(2026, 11, 10, tzinfo=timezone.utc),
               description="Python, PostgreSQL", **kw)


STATS = {"matched": 3, "in_applied": 0, "mapped": 1, "companies": 2, "fetched": 10, "rejected": []}


def test_report_sections():
    jobs = [job("Senior Python Developer"), job("AI Engineer (Python / LLM)", "Globex", staffing="чёрный список"),
            job("Backend Engineer", "Beta", manual_check="текст вакансии недоступен")]
    md = report.render(date(2026, 11, 12), jobs, dict(STATS))
    assert md.startswith("# Вакансии за 2026-11-12")
    assert "Senior Python Developer" in md
    # аутсорс — в отдельном приложении, после основных разделов
    assert md.index("Senior Python Developer") < md.index("Globex")
    assert "Backend Engineer" in md


def test_html_report_renders():
    page = html_report.render(date(2026, 11, 12), [job("Senior Python Developer")], dict(STATS))
    assert "<html" in page.lower() and "Senior Python Developer" in page
