"""Telegram channels: parsing posts of each format and the vacancy label. The posts are made up, but follow the
channels' layout; most channels are Russian-language, so the post samples are in Russian."""
from datetime import UTC, datetime

import pytest
from conftest import FakeHttp

from jobhunter import telegram as tg

WHEN = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def parse(channel, raw, post_id=101):
    return tg.PARSERS[channel](channel, post_id, WHEN, raw)


def test_youritjob():
    raw = ("<b>Senior Python Developer, Acme, Remote</b><br/>"
           "Зарплата: от 4000 до 5500 EUR<br/>Стек: Python, FastAPI, PostgreSQL<br/>"
           '<a href="https://acme.example/careers/python">Откликнуться</a> или @acme_hr<br/>#remote #python')
    (job,) = parse("youritjob", raw)
    assert (job.title, job.company) == ("Senior Python Developer", "Acme")
    assert "remote" in job.location
    assert job.salary.startswith("Зарплата")
    assert job.url == "https://acme.example/careers/python" and job.contact == "@acme_hr"
    assert job.post_url == "https://t.me/youritjob/101"


def test_youritjob_skips_digest():
    assert parse("youritjob", "<b>Дайджест вакансий за неделю</b><br/>…") == []


def test_af_job_board_collection():
    raw = ("<b>Acme</b> сейчас активно нанимает, работа удалённо из любой точки мира:<br/><br/>"
           "Python Developer<br/>💰 $4000–5000<br/>"
           '👉 <a href="https://acme.example/jobs/1">Apply</a><br/><br/>'
           "Data Engineer<br/>📍 Berlin<br/>💻 Office<br/>"
           '👉 <a href="https://acme.example/jobs/2">Apply</a>')
    py, data = parse("af_job_board", raw)
    assert (py.title, py.company, py.url) == ("Python Developer", "Acme", "https://acme.example/jobs/1")
    assert "remote" in py.location and "Worldwide" in py.location and py.salary == "$4000–5000"
    assert data.title == "Data Engineer" and "Berlin" in data.location and "onsite" in data.location


def test_habr_digest():
    # as in the channel: a link wraps "<Title> в <Company>"
    raw = ('Вакансии в бэкенде.<br/><br/><a href="https://u.habr.com/abc" target="_blank">Python-разработчик в Acme</a>.'
           ' Москва. Можно удаленно.<br/><br/><a href="https://u.habr.com/def">Go-разработчик в Beta</a>. Варшава.'
           "<br/>#remote")
    py, go = parse("habr_career", raw)
    assert (py.title, py.company, py.url) == ("Python-разработчик", "Acme", "https://u.habr.com/abc")
    assert not py.full_text and "remote" in py.location
    assert go.company == "Beta"


def test_zarubezhom_abroad_remote():
    raw = ("<b>Senior Python Developer в Acme</b><br/>📍 Удалённо (вне РФ и РБ)<br/>💎 Python, Django<br/>"
           'Узнать подробнее о вакансии можно <a href="https://acme.example/jobs/7">тут</a>')
    (job,) = parse("zarubezhom_jobs", raw)
    assert (job.title, job.company) == ("Senior Python Developer", "Acme")
    assert job.location == "Remote, worldwide"
    assert job.url == "https://acme.example/jobs/7"


def test_relocatesme_company_line():
    raw = ("<b>Backend Engineer (Python)</b><br/>Удалённо (можно за пределами РФ)<br/>"
           "Компания: Acme — платёжный сервис<br/>ЗП: 4000–5000 €<br/>"
           'Откликнуться: <a href="https://acme.example/careers/backend">ссылка</a>')
    (job,) = parse("relocatesme", raw)
    assert (job.title, job.company) == ("Backend Engineer (Python)", "Acme")
    assert job.salary == "4000–5000 €" and job.url == "https://acme.example/careers/backend"
    assert "worldwide" in job.location


def test_relocatesme_short_line_and_ads():
    raw = '<b>Python Developer</b> <a href="https://acme.example/jobs/9">→</a><br/>Senior · #hybrid · Warsaw'
    (job,) = parse("relocatesme", raw)
    assert job.company == "—" and job.location == "hybrid, Warsaw"
    assert parse("relocatesme", "<b>Курс по IELTS со скидкой</b><br/>Записывайтесь!") == []


def test_jtbl_domain_instead_of_company():
    raw = ("<b>Python Developer в Fintech</b><br/>#python #remote<br/>Требования: Python, Kafka<br/>"
           "Локация: 📍удалённо из любой точки мира<br/>Контакт для отклика: @fintech_hr")
    (job,) = parse("jtbl_vacancy", raw)
    assert job.company == "Fintech (company not named)"
    assert job.location == "Remote worldwide" and job.contact == "@fintech_hr"


def test_job_python():
    raw = ("#вакансия #python<br/><b>Acme</b><br/><b>Senior Python Developer</b><br/>"
           "Зарплата 4500–6000 EUR<br/>Формат работы: удалённо из любой точки мира<br/>"
           '<a href="https://acme.example/vacancy/1">Подробнее</a>')
    (job,) = parse("job_python", raw)
    assert (job.company, job.title) == ("Acme", "Senior Python Developer")
    assert "worldwide" in job.location and job.salary.startswith("Зарплата")
    assert job.url == "https://acme.example/vacancy/1"


def test_pythonrabota_and_telegraph():
    raw = ('<b>Python Developer</b> | <a href="https://telegra.ph/Python-Developer-11-10">Смотреть вакансию</a><br/>'
           "Формат: удалённо<br/>Компания: Acme<br/>ЗП: 3000–4000 $")
    (job,) = parse("pythonrabota", raw)
    assert (job.title, job.company, job.salary) == ("Python Developer", "Acme", "3000–4000 $")
    assert not job.full_text
    page = {"result": {"content": [{"tag": "p", "children": ["Локация: удалённо, Польша"]},
                                   {"tag": "p", "children": ["Стек: Python, asyncio"]}]}}
    import json
    tg.fill_telegraph(FakeHttp({"https://api.telegra.ph/getPage/Python-Developer-11-10": json.dumps(page)}), job)
    assert job.full_text and "asyncio" in job.text
    assert job.location.startswith("Remote, Польша")


def test_offer_inside():
    # as in the channel: a link wraps the bold title, " в <Company>" is a separate <b>
    raw = ('<a href="https://jobs.lever.co/acme/1" target="_blank"><b>Python Engineer</b></a><b> в Acme</b><br/><br/>'
           '<tg-emoji emoji-id="1"><i class="emoji" style="x"><b>🤩</b></i></tg-emoji> Где угодно<br/><br/>Делают платежи')
    (job,) = parse("JobOfferInside", raw)
    assert (job.title, job.company, job.url) == ("Python Engineer", "Acme", "https://jobs.lever.co/acme/1")
    assert job.location == "Remote, Worldwide"


def test_hiddengurus_needs_company():
    raw = ("<b>Senior Python Developer</b><br/>🏢 Компания: Acme<br/>💰 ЗП: 4000–5000 $<br/>"
           "🌍 Формат: Удалённо (РФ)<br/>📩 Отклик: @acme_hr")
    (job,) = parse("hiddengurus", raw)
    assert job.company == "Acme" and job.salary == "4000–5000 $" and job.contact == "@acme_hr"
    assert "Russia" in job.location
    assert parse("hiddengurus", "<b>Как копить на пенсию</b><br/>Статья") == []


def test_opento_digest():
    raw = ('<b>Senior Python Developer / Acme</b> <a href="https://wantapply.com/a?x=1&amp;amp;utm=tg">→</a><br/>'
           "Remote, Europe. Salary: €5000 - €6000<br/>"
           '<b>Middle+ / Senior Backend Engineer / Beta</b> <a href="https://wantapply.com/b">→</a><br/>Warsaw')
    a, b = parse("opento_dev", raw)
    assert (a.title, a.company, a.location) == ("Senior Python Developer", "Acme", "Remote, Europe")
    assert a.salary.startswith("Salary: €5000") and a.url == "https://wantapply.com/a?x=1&utm=tg"
    assert (b.title, b.company) == ("Middle+ / Senior Backend Engineer", "Beta")  # "/" inside the title
    assert not a.full_text  # a digest: the post has only a line about the vacancy


def test_fetch_posts_reads_page_and_cutoff():
    def message(n, when, text):
        return (f'<div class="tgme_widget_message_wrap"><div data-post="chan/{n}">'
                f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>'
                f'<time datetime="{when}" class="time">12:00</time></div></div>')
    page = message(1, "2026-10-01T10:00:00+00:00", "old") + message(2, "2026-11-10T10:00:00+00:00", "<b>new</b>")
    posts = tg.fetch_posts(FakeHttp({"https://t.me/s/chan": page}), "chan", datetime(2026, 11, 1, tzinfo=UTC))
    assert [(p[0], p[2]) for p in posts] == [(2, "<b>new</b>")]


@pytest.mark.parametrize("location,label", [
    ("Remote, Poland", "✅"), ("Warsaw, office", "✅ Poland"), ("remote", "❓ remote, region not stated"),
])
def test_classify_labels(location, label):
    job = tg.TgJob(channel="c", posted=WHEN, title="Senior Python Developer", company="Acme", location=location,
                   salary="", url="", contact="", post_url="", text="Python, FastAPI, PostgreSQL. Full-time.")
    assert tg.classify(job)
    assert job.label.startswith(label)


@pytest.mark.parametrize("location,text", [
    ("Berlin, office", "Python developer"),
    ("Remote", "Python developer. Только для граждан РФ, удалённо из России."),
    ("Remote", "Frontend developer, React"),
], ids=["foreign-office", "russia-only", "not-python-role"])
def test_classify_skips(location, text):
    job = tg.TgJob(channel="c", posted=WHEN, title="Developer" if "Frontend" not in text else "Frontend Developer",
                   company="Acme", location=location, salary="", url="", contact="", post_url="", text=text)
    assert not tg.classify(job)


# --- collect: going through the channels ---------------------------------------------------------


def _tg_job(channel, post_id, when, raw):
    title, company = raw.split("|")
    return [tg.TgJob(channel=channel, posted=when, title=title, company=company, location="Remote", salary="",
                     url=f"https://agg.example/{post_id}" if company == "Agg" else f"https://acme.example/{post_id}",
                     contact="", post_url=f"https://t.me/{channel}/{post_id}")]


def test_collect(monkeypatch, tmp_path, caplog, use_settings):
    """A failing channel, forwarded posts, a broken post, duplicates across channels and the aggregator link check."""
    from jobhunter import progress

    monkeypatch.setattr(progress, "PROGRESS_FILE", tmp_path / "progress.json")
    use_settings(telegram={"channels": ["down", "one", "two"], "aggregator_hosts": ["agg.example"]})
    older, newer = datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 5, tzinfo=UTC)
    posts = {
        "one": [(1, older, "Python Dev|Acme", False), (2, newer, "Forward|Other", True), (3, newer, "boom", False)],
        "two": [(4, newer, "Python  dev|ACME", False), (5, newer, "Backend|Agg", False)],
    }

    def fetch_posts(http, channel, cutoff):
        if channel == "down":
            raise tg.FetchError("HTTP 500")
        return posts[channel]

    monkeypatch.setattr(tg, "fetch_posts", fetch_posts)
    monkeypatch.setattr(tg, "PARSERS", {})  # every channel uses the default parser
    monkeypatch.setattr(tg, "parse_youritjob", _tg_job)
    monkeypatch.setattr(tg, "classify", lambda job: True)
    jobs, failed = tg.collect(FakeHttp({}))
    assert failed == ["down"]
    assert "could not parse the post" in caplog.text  # the "boom" post is skipped, the rest is read
    by_company = {j.company: j for j in jobs}
    assert set(by_company) == {"ACME", "Agg"}  # the forwarded post is skipped, the Acme duplicate merged
    assert by_company["ACME"].posted == newer  # of the duplicates, the freshest
    assert by_company["Agg"].marks[-1] == "❌ link does not open (HTTP 404)"


@pytest.mark.parametrize("title,location,text,label", [
    ("Senior Python Developer", "Berlin, office", "Python, FastAPI. Full-time. We offer relocation support to Germany.",
     "🌍 office in another country"),
    ("Senior Python Developer", "Berlin, hybrid", "Python, FastAPI. Full-time.", "❓ hybrid in another country"),
    ("Senior Python Developer", "Remote, Germany", "Python, FastAPI. Full-time.", "❓ remote, tied to another country"),
], ids=["relocation", "hybrid", "remote-other-country"])
def test_classify_outside_home(title, location, text, label, use_settings):
    use_settings(country_bound_remote=True)
    job = tg.TgJob(channel="c", posted=WHEN, title=title, company="Acme", location=location,
                   salary="", url="", contact="", post_url="", text=text)
    assert tg.classify(job)
    assert job.label.startswith(label)


def test_classify_marks_other_primary_language():
    job = tg.TgJob(channel="c", posted=WHEN, title="Senior Go Backend Developer", company="Acme", location="Remote",
                   salary="", url="", contact="", post_url="", text="Go, Python for scripts, PostgreSQL. Full-time.")
    assert tg.classify(job)
    assert any("Python not primary language" in m for m in job.marks)


def test_classify_skips_part_time_and_country_bound_off(monkeypatch, use_settings):
    def job(location, text, title="Python Developer"):
        return tg.TgJob(channel="c", posted=WHEN, title=title, company="Acme", location=location,
                        salary="", url="", contact="", post_url="", text=text)

    assert not tg.classify(job("Remote", "Python, Django.", "Python Developer (part-time)"))
    use_settings(country_bound_remote=False)
    assert not tg.classify(job("Remote, Germany", "Python, Django. Full-time."))
