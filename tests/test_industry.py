"""A vacancy's industry from its text: positives, false positives, the Companies page (jobhunter/industry.py)."""
import json
import time
from types import SimpleNamespace

import pytest

from jobhunter import industry
from tests.test_web import client  # noqa: F401  — web client fixture on empty data


@pytest.mark.parametrize("company, title, text, expected", [
    ("Acme Play", "Python Developer", "We build a B2C platform for online casinos and sportsbook operators.", "igaming"),
    ("Acme", "Senior Backend Engineer, Payments", "Join a fintech building payment processing for SMEs.", "fintech"),
    ("Acme Markets", "Python Developer", "A forex broker: trading platform and order books.", "trading"),
    ("Chainlabs", "Backend Engineer", "We build DeFi protocols and smart contracts on Ethereum.", "crypto"),
    ("ClickHouse", "Data Platform Engineer", "", "devtools"),
    ("Sec Co", "Python Engineer", "A cybersecurity company focused on threat detection and SIEM.", "security"),
    ("Shopco", "Backend Engineer", "The leading e-commerce marketplace for second-hand fashion.", "ecommerce"),
    ("Tripco", "Data Engineer", "We help travellers compare hotel prices; travel tech at scale.", "travel"),
    ("Learnly", "Backend Developer", "An edtech company: online courses and a learning platform for students.", "edtech"),
    ("Medly", "Backend Developer", "Digital health platform connecting patients with clinicians.", "healthtech"),
    ("Studio X", "Game Server Developer", "We are a game studio making multiplayer mobile games.", "gamedev"),
    ("Toolsy", "Backend Engineer", "A B2B SaaS platform for HR teams.", "b2b-saas"),
    ("SoftHouse", "Python Developer", "Outsourcing company; you will work on projects for our client in the US.", "outsource"),
])
def test_detects_industry(company, title, text, expected):
    assert expected in industry.classify(company, title, text)


@pytest.mark.parametrize("text, wrong", [
    # "bet" inside words and as a verb
    ("Communication between services; the alphabet of distributed systems. We bet on Python.", "igaming"),
    # gamification is not gamedev; a team player is not a player
    ("We use gamification to boost engagement. You are a team player.", "gamedev"),
    # data bank / food bank are not banks
    ("Maintain our data bank of documents; we volunteer at the local food bank.", "fintech"),
    # benefits: health insurance is not healthtech
    ("Benefits: private healthcare, health insurance, medical and dental coverage.", "healthtech"),
    # "travel required" is not travel; nor is a satellite's flight software
    ("Some travel required. You will write flight software for satellites.", "travel"),
    # an ordinary platform that mentions payments once
    ("Python backend for a logistics platform that also handles payments.", "fintech"),
    # an internal developer platform is not a devtools company
    ("Improve developer experience and our observability platform.", "devtools"),
])
def test_no_false_positive(text, wrong):
    assert wrong not in industry.classify("Acme", "Backend Engineer", text)


def test_other_when_nothing_matches():
    assert industry.classify("Acme", "Python Developer", "Django, PostgreSQL, Celery.") == ["other"]


def test_casino_games_are_not_gamedev():
    assert industry.classify("Slotsy", "Backend Developer", "We develop casino games and slot games for operators.") \
        == ["igaming"]


def test_known_company_wins_over_its_clients_list():
    """A description that lists the clients' industries does not change a known company's industry."""
    text = "Our customers are banks, crypto exchanges and online casinos."
    assert industry.classify("Stripe", "Backend Engineer", text) == ["fintech"]


def test_long_text_without_spaces_is_fast():
    start = time.perf_counter()
    industry.classify("Acme", "Engineer", "a" * 100_000)
    assert time.perf_counter() - start < 1


def _marks():
    return industry.Listed([
        SimpleNamespace(name="Acme Pay", industries=["fintech"], brands=["PayRocket"]),
        SimpleNamespace(name="Quiet Co", industries=["edtech"], brands=[]),
        SimpleNamespace(name="Plain Co", industries=[], brands=[]),
    ])


def test_companies_txt_marks_and_brands():
    marks = _marks()
    assert industry.classify("Acme Pay", "Engineer", "We build video games.", marks) == ["fintech"]
    # a post without a company, under a brand — the owner's industry
    assert industry.classify("NDA", "Backend Developer", "Join the PayRocket team.", marks) == ["fintech"]
    assert marks.owner("NDA", "Join the PayRocket team.") == "Acme Pay"
    assert marks.names == ["Acme Pay", "Quiet Co"]


def test_load_companies_reads_industries_and_brands(tmp_path):
    from jobhunter.companies import load_companies

    path = tmp_path / "companies.txt"
    path.write_text("Acme Pay|1|note|fintech, crypto|PayRocket\nAcme Pay|2||ecommerce\nPlain Co\n", encoding="utf-8")
    acme, plain = load_companies(path)
    assert (acme.score, acme.industries, acme.brands) == (2, ["fintech", "crypto", "ecommerce"], ["PayRocket"])
    assert (plain.industries, plain.brands) == ([], [])


def test_companies_overview(monkeypatch):
    monkeypatch.setattr(industry, "SETTINGS_COMPANIES", {"devtools": ["Toolbox"]})
    jobs = [
        {"company": "Acme Pay", "title": "Backend Engineer", "url": "https://a.example/1", "day": "2026-01-02",
         "industries": ["fintech"]},
        # the same title from another source — one vacancy; the latest day wins
        {"company": "Acme Pay", "title": "Backend Engineer", "url": "https://b.example/1", "day": "2026-01-05",
         "industries": ["fintech"]},
        {"company": "NDA", "title": "Python Developer", "text": "Join the PayRocket team.", "day": "2026-01-03"},
        {"company": "Shopco", "title": "Engineer", "day": "2026-01-01", "industries": ["ecommerce"]},
    ]
    mapping = {"Shopco": {"platform": "lever", "slug": "shopco"}}
    rows = {r["name"]: r for r in industry.companies_overview(
        jobs, lambda name: "applied" if name == "Shopco" else "", mapping, _marks())}
    acme = rows["Acme Pay"]
    assert (acme["vacancies"], acme["last"], acme["by"], acme["industries"]) == (2, "2026-01-05", "list", ["fintech"])
    assert rows["Shopco"]["board"] == "https://jobs.lever.co/shopco"
    assert rows["Shopco"]["by"] == "vacancies"
    # without vacancies — from settings and companies.txt marks, to search by hand; built-in examples are not listed
    assert rows["Toolbox"]["vacancies"] == 0 and rows["Quiet Co"]["vacancies"] == 0
    assert "Stripe" not in rows
    order = [r["name"] for r in industry.companies_overview(jobs, lambda n: "applied" if n == "Shopco" else "",
                                                             mapping, _marks())]
    assert order.index("Acme Pay") < order.index("Shopco") < order.index("Quiet Co")


def test_market_by_industry():
    jobs = [{"company": f"C{i}", "title": "Engineer", "industries": ["fintech"], "remote": i % 2 == 0,
             "salary": "€4000–6000 / month", "text": "Python, Kafka"} for i in range(4)]
    jobs.append({"company": "C0", "title": "Engineer", "industries": ["fintech"]})  # a repeat
    (row,) = industry.market_by_industry(jobs, industry.Listed([]))
    assert (row["industry"], row["vacancies"], row["remote_share"]) == ("fintech", 4, 50)
    assert row["salaries"] == 4 and row["salary_median"]
    assert {"name": "Python", "share": 100} in row["skills"]


def test_companies_page_and_filter(client):  # noqa: F811
    http, _, jobs_dir = client
    (jobs_dir / "2026-01-05.json").write_text(json.dumps([
        {"company": "Acme Play", "title": "Python Developer", "url": "https://a.example/1",
         "text": "A platform for online casinos and sportsbook operators."},
        {"company": "Shopco", "title": "Backend Engineer", "url": "https://s.example/1",
         "text": "The leading e-commerce marketplace."},
    ]), encoding="utf-8")
    page = http.get("/companies").text
    assert "Acme Play" in page and "Shopco" in page  # all industries by default
    page = http.get("/companies?industry=ecommerce").text
    assert "Shopco" in page and "Acme Play" not in page
    data = http.get("/api/companies").json()
    assert data["vacancies"] == 2 and data["counts"]["igaming"] == 1


def test_companies_page_is_fast_while_run_rewrites_inputs(client, monkeypatch):  # noqa: F811
    """A run rewrites the mail cache and the archive — the page serves the previous summary at once,
    the recompute runs in the background.

    Before, every request during a run classified the whole archive again (minutes on real data).
    """
    import threading

    import web.cache

    http, _, jobs_dir = client
    (jobs_dir / "2026-11-16.json").write_text(json.dumps([
        {"url": "u1", "company": "Globex", "title": "Python Developer", "text": "Fintech"}]), encoding="utf-8")
    assert http.get("/api/companies").json()["vacancies"] == 1

    release, started = threading.Event(), threading.Event()
    overview = industry.companies_overview

    def slow_overview(*args, **kwargs):  # a long recompute: waits until the test lets it go
        started.set()
        assert release.wait(10)
        return overview(*args, **kwargs)
    monkeypatch.setattr(industry, "companies_overview", slow_overview)
    (jobs_dir / "2026-11-17.json").write_text(json.dumps([  # a run added a day — the cache key changed
        {"url": "u2", "company": "Acme", "title": "Python Developer", "text": "Django"}]), encoding="utf-8")

    answer: list[dict] = []
    request = threading.Thread(target=lambda: answer.append(http.get("/api/companies").json()))
    request.start()
    request.join(5)
    try:
        assert answer and answer[0]["vacancies"] == 1  # the previous summary, no wait for the recompute
        assert started.wait(5)  # while the recompute runs in the background
        assert http.get("/companies").status_code == 200  # a second request neither waits nor starts another one
    finally:
        release.set()
        request.join(10)
    web.cache.wait_refresh("companies_by_industry", 10)
    assert http.get("/api/companies").json()["vacancies"] == 2  # recomputed
