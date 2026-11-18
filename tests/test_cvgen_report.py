"""A CV for a vacancy (cvgen, base — cv/base.example.toml) and building the report (report, html_report)."""
import re
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
    assert md.startswith("<!-- vacancy: Acme")  # a service note: which vacancy the CV was built for
    assert "\n# " in md and "## Education" in md
    assert "Python" in md and "PostgreSQL" in md
    assert "Senior Software Engineer (Backend)" in md  # the headline adapted to the vacancy title


def test_one_page_variant_is_shorter(base):
    full = cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY)
    short = cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY, limits=cvgen.Limits(compact=True))
    assert len(short) <= len(full)


def test_picks_explain_choice(base):
    picks = []
    cvgen.build_markdown(base, "Acme", "Senior Software Engineer", VACANCY, picks=picks)
    assert picks, "the picked bullets and the reasons must be recorded"


PROJECT = {"name": "job-hunter", "kind": "open-source", "link": "https://github.com/acme/job-hunter",
           "intro": "A job search pipeline.", "stack": ["Python", "FastAPI"], "requires": ["scraping"],
           "bullets": [{"text": "Aggregates vacancies from 9 ATS APIs.", "topics": ["scraping"]},
                       {"text": "Property-based API tests.", "topics": ["quality"]}]}
SCRAPING = VACANCY + " You will build web scrapers and integrations with ATS platforms (Greenhouse, Lever)."


def test_project_only_for_scraping_vacancies(base):
    """A pet project goes into the CV only for vacancies of its topic (requires): ATS, scrapers. A regular backend or
    AI vacancy and the base CVs do not get it — there it would mix with work experience."""
    b = {**base, "projects": [PROJECT]}
    assert "scraping" in cvgen.Vacancy("Backend Engineer", SCRAPING).topics
    for md in (cvgen.build_markdown(b, "Acme", "Senior Software Engineer", VACANCY),
               cvgen.build_markdown(b, "Acme", "AI Engineer", "LLM, RAG, AI agents, Python."),
               cvgen.build_markdown(b, "", "", "", generic="backend")):
        assert "## Projects" not in md and "job-hunter" not in md
    picks: list = []
    md = cvgen.build_markdown(b, "Acme", "Senior Python Developer", SCRAPING, picks=picks)
    assert md.index("## Experience") < md.index("## Projects") < md.index("## Education")
    assert "### job-hunter — open-source" in md and "9 ATS" in md and "_Python · FastAPI_" in md
    assert any(p["section"] == "job-hunter" for p in picks)
    html = cvgen.markdown_to_html(md)
    assert 'href="https://github.com/acme/job-hunter"' in html
    short = cvgen.build_markdown(b, "Acme", "Senior Python Developer", SCRAPING, limits=cvgen.Limits(compact=True))
    assert "## Projects" in short and "Property-based" not in short  # one-page: a single bullet


def test_topic_regexes_linear_on_long_text():
    import time
    start = time.perf_counter()
    cvgen.Vacancy("x", "a" * 100_000)
    assert time.perf_counter() - start < 1


def test_markdown_to_html(base):
    html = cvgen.markdown_to_html(cvgen.build_markdown(base, "Acme", "Backend Engineer", VACANCY))
    assert "<h1>" in html and "</html>" in html


@pytest.mark.parametrize("title,expected_role", [
    ("Software Engineer, Payments", "Senior Software Engineer (Backend)"),
    ("Office Manager", "Senior Backend Engineer"),  # no match — the headline as in the base
])
def test_headline_for(title, expected_role):
    assert cvgen.headline_for(title, "Senior Backend Engineer | Python · PostgreSQL").startswith(expected_role)


def test_slug():
    assert cvgen.slug("Acme / Senior Python Developer (Remote)") == "Acme_Senior_Python_Developer_Remote"


# --- report -------------------------------------------------------------------------------------

def job(title, company="Acme", **kw):
    return Job(company=company, title=title, location="Remote", url=f"https://{company.lower()}.example/{title}",
               source="test", posted=datetime(2026, 11, 10, tzinfo=timezone.utc),
               description="Python, PostgreSQL", **kw)


STATS = {"matched": 3, "in_applied": 0, "mapped": 1, "companies": 2, "fetched": 10, "rejected": []}


def test_report_sections():
    jobs = [job("Senior Python Developer"), job("AI Engineer (Python / LLM)", "Globex", staffing="blacklist"),
            job("Backend Engineer", "Beta", manual_check="vacancy text unavailable")]
    md = report.render(date(2026, 11, 12), jobs, dict(STATS))
    assert md.startswith("# Vacancies for 2026-11-12")
    assert "Senior Python Developer" in md
    # outsourcing — in a separate appendix, after the main sections
    assert md.index("Senior Python Developer") < md.index("Globex")
    assert "Backend Engineer" in md


def test_html_report_renders():
    page = html_report.render(date(2026, 11, 12), [job("Senior Python Developer")], dict(STATS))
    assert "<html" in page.lower() and "Senior Python Developer" in page


def test_html_table_numeric_headers_align_with_cells():
    """Numbers in cells are right-aligned (td.n). The header of that column must be th.n too, otherwise in a wide
    table it sits on the left while the numbers sit on the right edge, under the next column's header (it happened in
    "Most missing skills": key-skill counts read as Nice-to-have, and the "Key" column looked empty)."""
    row = '<tr><td>Kafka</td><td class="n">5 (10%)</td><td class="n">—</td></tr>'
    html = html_report._table(["Skill", "Key", "Nice-to-have"], [row], num=(1, 2))
    assert '<th>Skill</th><th class="n">Key</th><th class="n">Nice-to-have</th>' in html
    assert re.search(r"th\.n\b[^{]*\{[^}]*text-align:\s*right", html_report._CSS)

    page = html_report.render(date(2026, 11, 12), [job("Senior Python Developer")], dict(STATS))
    for table in re.findall(r"<table.*?</table>", page, re.S):
        heads = re.findall(r"<th( class=\"n\")?>", table)
        first = re.search(r"<tbody><tr>(.*?)</tr>", table, re.S)
        if not first:
            continue
        cells = re.findall(r"<td( class=\"n\")?[ >]", first.group(1))
        for i, cell in enumerate(cells[:len(heads)]):
            assert bool(cell) == bool(heads[i]), f"column {i}: td.n and th.n differ"
