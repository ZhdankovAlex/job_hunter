"""report.render / html_report.render: every section of the report and the summary on one set of vacancies."""
from datetime import UTC, date, datetime

import pytest

from jobhunter import config, geo, html_report, report
from jobhunter.models import Job
from jobhunter.telegram import TgJob

DAY = date(2026, 11, 12)


def job(title, company="Acme", **kw):
    return Job(company=company, title=title, location="Remote", url=f"https://{company.lower()}.example/{title}",
               source="test", posted=datetime(2026, 11, 10, tzinfo=UTC), description="Python, PostgreSQL", **kw)


def tg(title, **kw):
    return TgJob(channel="chan", posted=datetime(2026, 11, 11, tzinfo=UTC), title=title, company="Initech",
                 location="Remote", salary="", url="", contact="@hr", post_url=f"https://t.me/chan/{title}",
                 text="Python", **kw)


@pytest.fixture
def data(monkeypatch, use_settings):
    use_settings(hide_previous_rejections=True)
    monkeypatch.setattr(config, "PRIORITY_MAX_ROWS", 1)  # the second priority vacancy goes to the overflow list
    jobs = [
        job("Senior Python Developer"),
        job("Python Backend Engineer", "Hooli"),
        job("Python Engineer (check)", "Umbrella", manual_check="vacancy text unavailable"),
        job("Python Developer Berlin", "Globex", reloc_type=geo.RELOCATION, reloc_country="Germany"),
        job("Python Developer Madrid", "Soylent", reloc_type=geo.REMOTE_OK, reloc_country="Spain"),
        job("Python Lead", "Stark", location_check=True, score=3),
        job("Python Dev", "Wayne", location_check=True, score=0, language_mismatch_warning=True),
        job("Python Contractor", "Body Shop", staffing="outsourcing"),
        job("Python Entwickler", "Lokal", lang_required=True),
        job("Python Engineer again", "Vandelay", applied_status="Rejected"),
    ]
    tg_jobs = [tg("Python Developer TG", label="✅ fits"), tg("TG outsourcing", staffing="outsourcing"),
               tg("TG local", lang_required=True), tg("TG rejected", applied_status="Rejected")]
    stats = {
        "matched": 14, "in_applied": 1, "mapped": 5, "companies": 9, "fetched": 100,
        "rejected": [job("Python Onsite", "Massive", reject="onsite only", reject_quote="office 5 days"),
                     job("Python Old", "Vandelay", applied_status="Rejected", reject="old")],
        "must_report": [("Hooli", 3, 1, ""), ("Pied Piper", 2, 0, ""), ("Aviato", 0, 0, "no board")],
        "referral": [("Hooli", True), ("Raviga", False), ("Acme", True)],
        "freehire": {"enabled": True, "fetched": 5, "requests": 2, "cache_hits": 1, "totals": {"q1": 5},
                     "added": 2, "twins": 1, "described": 2, "candidates": 3, "dropped": 0,
                     "diagnosis": {"q2": "zeroed by the filter x=1"}},
        "salary": {"samples": 4, "summary": ["senior €6000"]},
    }
    return jobs, tg_jobs, stats


def test_markdown_has_every_section(data):
    jobs, tg_jobs, stats = data
    md = report.render(DAY, jobs, stats, tg_jobs)
    for title in ("# Appendix: all current vacancies", f"## {report.PRIORITY_TITLE} — 2 (up to 1 freshest shown)",
                  "Company priority 3 — 1", "Company priority 0 — 1",
                  "## Appendix: remaining vacancies by company", "## Appendix: Staffing/outsourcing — 2",
                  "## Appendix: Local language required — 2", "## Summary"):
        assert title in md, title
    assert "Telegram channels" in md
    summary = md[md.index("## Summary"):]
    assert "- Rejected before (separate section): 3" in summary  # job + tg + one dropped by filters
    assert "- Rejected by filters (fitting role): 1" in summary
    assert "Germany: 1 (confirmed relocation: 1), Spain: 1 (confirmed relocation: 0)" in summary
    assert "- Remote within a country that allows working from outside it: 1" in summary
    assert "- Pied Piper: vacancies found on the board — 2, passed the filters — 0 (no Python vacancies right now)" in summary
    assert "- Aviato: ATS board not found, see errors.log (reason: no board)" in summary
    assert "- Raviga: no Python vacancies right now (ATS board not found" in summary
    assert "Acme" not in summary.split("By company priority")[0].split("Dropped for language")[1]  # Acme is in the report
    assert "- freehire, query q2 returned 0: zeroed by the filter x=1" in summary
    assert "- Salary range base: 4 vacancies" in summary
    assert "- Marked ⚠️ language mismatch: 1" in summary
    assert "- Telegram channels: 1 vacancies, of them ✅ — 1" in summary


def test_markdown_without_telegram_and_extras(data):
    jobs, _, stats = data
    md = report.render(DAY, jobs, {**stats, "freehire": {"enabled": False}, "salary": None})
    assert "Telegram channels" not in md and "freehire" not in md and "Salary range base" not in md


def test_empty_report():
    md = report.render(DAY, [], {"matched": 0, "in_applied": 0, "mapped": 0, "companies": 0, "fetched": 0})
    assert "No new fitting vacancies." in md and "## Summary" in md


def test_html_has_every_section(data):
    jobs, tg_jobs, stats = data
    page = html_report.render(DAY, jobs, stats, tg_jobs)
    assert "<h2>Rejected before — 2</h2>" in page  # job + tg; the dropped one is not in the main table
    assert "<h2>Rejected by filters — 1 (shown 1)</h2>" in page
    assert "<h2>Staffing / outsourcing — 2</h2>" in page
    assert 'data-mode="prio"' in page  # Stark has priority 3
    assert "TG local" not in page and "Python Entwickler" not in page  # local language — not in the HTML tables
    assert page.count('<tr data-new=') == 8  # main 7 + 1 Telegram
