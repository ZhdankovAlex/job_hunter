"""Gamification of applications: streak (weekends don't break it), weekly goal, XP and ranks, next step, wins, badges."""
import json
import string
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jobhunter import applications_report as ar, gamification as g
from jobhunter.mail import APPLIED, INTERVIEW, OFFER, OTHER, REJECTED, Application, Event

MON = date(2026, 11, 2)  # a Monday
NO_CV = Path("no-such-cv-dir")


def ev(d: date, kind: str, subject: str = "", thread: str = "t1") -> Event:
    """An event at noon UTC; thread="" — a record from applications_manual.toml (our ping, a note)."""
    return Event(datetime(d.year, d.month, d.day, 12, tzinfo=UTC), kind, subject, "hr@example.com", thread)


def staged(*apps: Application) -> list[tuple[Application, str]]:
    return [(a, ar.funnel_stage(a)) for a in apps]


def day(n: int) -> date:
    return MON + timedelta(days=n)


# --- streak ---

def test_weekend_does_not_break_streak():
    days = {day(i) for i in (0, 1, 2, 3, 4, 7)}  # Mon–Fri, empty weekend, Mon
    assert g.streaks(days, day(7)) == (6, 6)


def test_missed_workday_breaks_streak():
    days = {day(0), day(1), day(3), day(4)}  # Wednesday is empty
    assert g.streaks(days, day(4)) == (2, 2)


def test_today_without_action_keeps_streak():
    """Today is not over yet: an empty today does not reset the streak."""
    days = {day(0), day(1), day(2)}
    assert g.streaks(days, day(3)) == (3, 3)
    assert g.streaks(days, day(4)) == (0, 3)  # but yesterday's gap does


def test_weekend_action_does_not_add_to_streak():
    assert g.streaks({day(4), day(5), day(7)}, day(7)) == (2, 2)


def test_silent_workdays():
    assert g.workdays_silent({day(4)}, day(7)) == 0  # Fri -> Mon: the weekend does not count
    assert g.workdays_silent({day(0)}, day(4)) == 3


# --- weekly goal and stars ---

def test_several_letters_from_one_company_a_day_is_one_application():
    acme = Application("Acme", [ev(day(0), APPLIED, "Applied"), ev(day(0), APPLIED, "Application received")])
    globex = Application("Globex", [ev(day(0), APPLIED)])
    led = g.ledger([acme, globex])
    assert g.week_counts(led) == {MON: 2}


def test_goal_weeks_and_five_stars():
    weeks = {MON + timedelta(weeks=i): 8 for i in range(5)} | {MON + timedelta(weeks=5): 3}
    assert g.goal_weeks(weeks, 8, MON + timedelta(weeks=5, days=2)) == (5, 5, 5)
    weeks[MON + timedelta(weeks=2)] = 7
    assert g.goal_weeks(weeks, 8, MON + timedelta(weeks=5, days=2)) == (4, 2, 2)


# --- XP and ranks ---

def test_xp_only_for_own_actions(tmp_path):
    """XP for applications (tailored — double) and pings; stages and rejections are employer decisions, no points."""
    (tmp_path / "2026-11-02").mkdir()
    (tmp_path / "2026-11-02" / "Acme_Python_Developer.md").write_text("cv", encoding="utf-8")
    acme = Application("Acme", [ev(day(0), APPLIED), ev(day(3), INTERVIEW, "Technical interview"),
                                ev(day(4), OFFER, "Offer")])
    globex = Application("Globex", [ev(day(0), APPLIED), ev(day(1), OTHER, "Pinged the recruiter", thread=""),
                                    ev(day(2), REJECTED)])
    parts = g.xp_breakdown(g.ledger([acme, globex]), staged(acme, globex), g.tailored_stems(tmp_path))
    assert parts == {"applications": 30, "pings": 5}  # Acme tailored 20 + Globex 10; a ping 5


def test_mail_letter_is_not_our_ping():
    """A recruiter's letter (other with a thread) is not our action and not a ping."""
    led = g.ledger([Application("Acme", [ev(day(0), OTHER, "Hi there")])])
    assert not led.pings and not led.action_days()


def test_cancelled_meeting_is_not_a_stage():
    led = g.ledger([Application("Acme", [ev(day(0), INTERVIEW, "Interview cancelled - Screen Call")])])
    assert not led.stages


def test_ranks_are_neutral_and_grow():
    assert g.rank(0) == ("Scout", "Organizer", 400)
    assert g.rank(2175)[:2] == ("Active candidate", "Strategist")
    assert g.rank(10**6) == ("Search expert", None, 0)
    assert g.rank_progress(800) == 0.5
    assert g.rank_progress(10**6) == 1.0
    names = " ".join(n for _, n in g.RANKS).lower()
    assert not any(w in names for w in ("junior", "middle", "senior", "lead", "offer"))
    assert [t for t, _ in g.RANKS] == sorted(t for t, _ in g.RANKS)


def test_no_pressure_messages():
    """Not a single template about XP for a stage or a rejection; an offer is not a goal."""
    text = " ".join(t for ts in g.TEMPLATES.values() for t in ts)
    assert "+5 XP" not in text and "+XP" not in text
    assert all(b.name != "Offer" for b in g.badges(dict.fromkeys(KEYS, 0)))


def test_plural_and_short_date():
    assert g.plural(1, "application") == "1 application"
    assert g.plural(3, "company", "companies") == "3 companies"
    assert g.short(date(2026, 11, 5)) == "Nov 5"


# --- badge collection ---

KEYS = ["tailored", "screens", "techs", "finals", "best_streak", "goal_run", "rejections", "icebreakers", "rate"]


def badge(game: g.Game, name: str) -> g.Badge:
    return next(b for b in game.badges if b.name == name)


def test_badges_from_history():
    rejects = [Application(f"Co{i}", [ev(day(0), APPLIED), ev(day(1), REJECTED)]) for i in range(10)]
    week = Application("Acme", [ev(day(i), APPLIED) for i in range(5)] + [ev(day(5), INTERVIEW, "Final interview with CTO")])
    game = g.build(day(7), staged(*rejects, week), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    assert badge(game, "Unbreakable").earned and badge(game, "On fire").earned
    assert badge(game, "Finalist").earned and badge(game, "Sniper").earned
    assert not badge(game, "Icebreaker").earned and not badge(game, "Armor").earned
    # the day earned: the 10th rejection — Tue, the final — Sat, the fifth workday of the streak — Fri
    assert badge(game, "Unbreakable").earned_on == day(1)
    assert badge(game, "Finalist").earned_on == day(5) and badge(game, "On fire").earned_on == day(4)


def test_collection_has_three_rarities_per_track():
    game = g.build(day(0), [], goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    assert len({b.name for b in game.badges}) == len(game.badges) == 3 * len(g.TRACKS) + len(g.SINGLES)
    assert {b.rarity for b in game.badges} == {0, 1, 2}
    for key, _, _, levels in g.TRACKS:  # within a track the target grows from common to legendary
        assert [t for _, t in levels] == sorted(t for _, t in levels), key


def test_showcase_is_limited_and_mixes_earned_and_nearest():
    stats = dict.fromkeys(KEYS, 0) | {"tailored": 30, "screens": 9, "techs": 2, "rejections": 39, "best_streak": 1}
    all_badges = g.badges(stats)
    shown = g.showcase(all_badges)
    assert len(shown) == g.SHOWCASE == 8
    assert sum(b.earned for b in shown) == 4
    near = [b for b in shown if not b.earned]
    assert near[0].name == "Armor" and near[1].name == "Wanted candidate"  # 39 of 40, 9 of 10
    assert len(g.showcase(all_badges[:3])) == 3


def test_page_hides_rest_of_collection():
    html = g.badges_html(g.build(day(0), [], goal=8, cv_dir=NO_CV, jobs_dir=NO_CV))
    shown, rest = html.split("<details>", 1)
    assert shown.count('<li class="badge') == 8
    assert "All badges (0 of 21)" in rest and rest.count('<li class="badge') == 21 - 8


def rate_goal(apps: list[Application]) -> g.Badge:
    return badge(g.build(day(2), staged(*apps), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV), "On target")


def test_conversion_goal_needs_enough_applications():
    few = [Application(f"Co{i}", [ev(day(0), APPLIED), ev(day(1), INTERVIEW, "Screen call")]) for i in range(5)]
    assert rate_goal(few).value == 0  # 5 applications — too few for a percentage
    many = few + [Application(f"Ignored{i}", [ev(day(0), APPLIED)]) for i in range(15)]
    assert (rate_goal(many).value, rate_goal(many).earned) == (25, True)  # 5 screenings of 20 applications


def test_icebreakers_need_our_ping_and_a_reply():
    ping = ev(day(0), OTHER, "Wrote to the recruiter", thread="")
    assert g.icebreakers([Application("Acme", [ping])]) == 0
    assert g.icebreakers([Application("Acme", [ev(day(0), OTHER, "Recruiter wrote"), ping])]) == 0  # the reply came earlier
    replied = Application("Acme", [ping, ev(day(2), OTHER, "Re: Python role")])
    assert g.icebreakers([replied, Application("Globex", [ping, ev(day(3), INTERVIEW, "Call")])]) == 2


def test_progress_page_blocks_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(g, "CV_DIR", tmp_path)
    monkeypatch.setattr(g, "JOBS_DIR", tmp_path)
    page = g.page(day(0), [])
    order = ['class="game hero"', "<h2>Next step</h2>", "<h2>Recent wins</h2>", 'class="game ach"',
             '<details class="game"><summary>Activity over 8 weeks']
    assert [page.index(x) for x in order] == sorted(page.index(x) for x in order)
    assert "Offer" not in page and 'href="/progress"' not in page
    assert "jobhunter.progress.badges" in page  # the "new" mark — via localStorage


# --- the next step of the week ---

def test_next_step_rules_by_priority():
    soon = ("Acme", day(3))
    leads = ("2026-11-02", ["Globex", "Initech"])
    step = g.next_step(day(1), soon, ["Globex"], leads, 8, 2)
    assert step.kind == "interview" and "Acme" in step.title and step.link.endswith("/prep")
    step = g.next_step(day(1), None, ["Co1", "Co2", "Co3", "Co4"], leads, 8, 2)
    assert step.kind == "ping" and "3 companies" in step.title and "Co1, Co2, Co3" in step.detail
    step = g.next_step(day(1), None, [], leads, 8, 2)
    assert step.kind == "leads" and "Globex, Initech" in step.detail and step.link.endswith("jobs_2026-11-02.html")
    assert "Nov 2" in step.title
    step = g.next_step(day(1), None, [], None, 8, 2)
    assert step.kind == "goal" and "6 applications" in step.title
    assert g.next_step(day(1), None, [], None, 8, 9).title.startswith("Weekly goal met")


def test_waiting_long_only_waiting_and_silent_over_week():
    old = Application("Old", [ev(day(0), APPLIED)])
    fresh = Application("Fresh", [ev(day(6), APPLIED)])
    pinged = Application("Pinged", [ev(day(0), APPLIED), ev(day(6), OTHER, "Ping", thread="")])
    staged_ = Application("Stage", [ev(day(0), APPLIED), ev(day(1), INTERVIEW, "Screen call")])
    rejected = Application("No", [ev(day(0), APPLIED), ev(day(1), REJECTED)])
    assert g.waiting_long([old, fresh, pinged, staged_, rejected], day(10)) == ["Old"]
    assert g.waiting_long([old], day(20)) == []  # longer than timeout_days() — already counts as a rejection


def test_fit_leads_skip_applied_and_weak(tmp_path):
    jobs = [{"company": "Acme Inc", "fit": 90}, {"company": "Globex", "fit": 85}, {"company": "Globex", "fit": 81},
            {"company": "Initech", "fit": 95}, {"company": "Weak", "fit": 60}]
    (tmp_path / "2026-11-01.json").write_text("[]", encoding="utf-8")
    (tmp_path / "2026-11-02.json").write_text(json.dumps(jobs), encoding="utf-8")
    applied = [Application("Acme", [ev(day(0), APPLIED)])]
    assert g.fit_leads(applied, tmp_path) == ("2026-11-02", ["Initech", "Globex"])
    assert g.fit_leads(applied, tmp_path / "missing") is None


# --- recent wins ---

def test_wins_by_significance_and_limit():
    apps = [
        Application("Scr1", [ev(day(0), APPLIED), ev(day(8), INTERVIEW, "Screen call")]),
        Application("Scr2", [ev(day(0), APPLIED), ev(day(9), INTERVIEW, "Screen call")]),
        Application("Fin", [ev(day(0), APPLIED), ev(day(2), INTERVIEW, "Final interview")]),
        Application("Offerco", [ev(day(0), APPLIED), ev(day(3), OFFER, "Offer letter")]),
        Application("Tech", [ev(day(0), APPLIED), ev(day(4), INTERVIEW, "Technical interview")]),
        Application("Old", [ev(day(-60), APPLIED), ev(day(-50), INTERVIEW, "Final interview")]),
    ]
    game = g.build(day(10), staged(*apps), goal=5, cv_dir=NO_CV, jobs_dir=NO_CV)
    texts = [w.text for w in game.wins]
    assert len(texts) == g.WINS_LIMIT
    assert texts[:4] == ["Offer from Offerco", "Final with Fin", "Technical round with Tech",
                         "Screening with Scr2"]  # one screening — the freshest
    assert all("Old" not in t for t in texts)  # older than 30 days
    assert texts[4].startswith("Badge")


def test_wins_empty_page_has_hint():
    html = g.wins_html(g.build(day(0), [], goal=8, cv_dir=NO_CV, jobs_dir=NO_CV))
    assert "No events in 30 days yet" in html


# --- message of the day and quotes ---

def test_message_is_deterministic_and_uses_real_names():
    apps = [Application("Acme", [ev(day(0), APPLIED), ev(day(1), REJECTED)])]
    a = g.build(day(2), staged(*apps), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    b = g.build(day(2), staged(*apps), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    assert a.message == b.message
    assert "Acme" in a.message  # a fresh rejection is urgent, more important than the streak and the goal


def test_upcoming_interview_from_note_and_calendar_subject():
    note = Application("Acme", [ev(day(0), APPLIED), ev(day(1), INTERVIEW, "Tech round moved from 03.11 to 12.11", thread="")])
    cal = Application("Globex", [ev(day(1), INTERVIEW, "Invitation: Interview @ Wed Nov 11, 2026 2pm")])
    assert g.upcoming_interview([note, cal], day(7)) == ("Globex", date(2026, 11, 11))
    gone = Application("Globex", [*cal.events, ev(day(2), REJECTED)])
    assert g.upcoming_interview([note, gone], day(7)) == ("Acme", date(2026, 11, 12))
    game = g.build(day(9), staged(note), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    assert "Acme" in game.message


def test_message_templates_format_in_every_situation():
    """Every template is built from the build context: no typos in the placeholders."""
    ctx = {k: "x" for t in g.TEMPLATES.values() for s in t for k in _fields(s)}
    for situation, templates in g.TEMPLATES.items():
        for t in templates:
            assert t.format(**ctx), situation
    assert set(g.URGENT) <= set(g.TEMPLATES)


def _fields(s: str) -> list[str]:
    return [f for _, f, _, _ in string.Formatter().parse(s) if f]


def test_quote_every_few_days():
    shown = [g.quote_of_day(day(i)) for i in range(30)]
    assert sum(q is not None for q in shown) == 10
    assert 30 <= len(g.QUOTES) <= 50 and len(set(g.QUOTES)) == len(g.QUOTES)


def test_heatmap_is_eight_weeks_and_future_is_empty():
    game = g.build(day(2), staged(Application("Acme", [ev(day(1), APPLIED)])), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    assert len(game.heat) == 8 and all(len(w) == 7 for w in game.heat)
    assert game.heat[-1][1] == (day(1), 1) and game.heat[-1][3] is None


def test_applications_page_has_no_game_block_and_funnel_on_top(monkeypatch, tmp_path):
    """Progress lives only on its own page; Applications has the conversion (funnel) on top, no duplicates."""
    monkeypatch.setattr(g, "CV_DIR", tmp_path)
    apps = [Application("Acme", [ev(day(0), APPLIED)])]
    page = ar.render(day(1), apps, [], "me@example.com")
    assert 'class="game"' not in page and "Week:" not in page
    body = page[page.index("<body>"):]
    assert body.index('class="funnel"') < body.index('class="sub"') < body.index('class="bar"')


def test_typo_date_does_not_break_page():
    """A typo date 0001-01-01 from applications_manual.toml: Windows can't convert it to the local zone."""
    old = Event(datetime(1, 1, 1, 12, tzinfo=UTC), APPLIED, "", "manual", "")
    game = g.build(day(0), staged(Application("Acme", [old])), goal=8, cv_dir=NO_CV, jobs_dir=NO_CV)
    assert game.week_n == 0
