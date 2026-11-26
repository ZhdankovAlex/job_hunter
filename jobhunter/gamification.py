"""Gamification of the job search: rank and XP, a streak of workdays, the next step of the week, wins, a badge collection.

Only data that is already there: application events (letters from the cache + applications_manual.toml), CVs built
in cv_out/ and vacancies of the latest report (state/jobs/). No network. Rewards are only for your own actions
(an application, a tailored application, a ping to a recruiter); employer decisions (a stage, an offer, a rejection)
are memorable events in "Recent wins", without points and without "the goal is an offer". There is no daily quota,
weekends don't break the streak.
"""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from . import applications_report as ar, config, mail, progress_charts as pc, settings
from .cvgen import slug
from .html_report import _CSS, THEME_JS, WEB_URL, _e, plural, short
from .keys import company_key

CV_DIR = config.ROOT / "cv_out"  # tailored CVs: cv_out/<date>/<Company>_<Role>.md
JOBS_DIR = config.JOBS_ARCHIVE_DIR  # vacancies of every report: state/jobs/<date>.json
FRESH_DAYS = 3  # a rejection or a stage within this many days is "fresh"
SOON_DAYS = 7  # an interview within this many days is "soon"
PING_AFTER_DAYS = 7  # waiting for an answer longer than this — worth a follow-up
LEAD_FIT = 80  # report vacancies with this Fit or higher, where you haven't applied — leads to look at
WINS_DAYS = 30  # "Recent wins" — within this period
WINS_LIMIT = 5
SHOWCASE = 8  # badges on the main screen; the rest are under "All badges"

# XP only for your own actions. Interview prep (/prep) is not saved to disk — nothing to award it for.
XP = {"apply": 10, "apply_tailored": 20, "ping": 5}
# thresholds: a few months of steady search land in the middle of the scale
RANKS = [(0, "Scout"), (400, "Organizer"), (1200, "Active candidate"), (3000, "Strategist"),
         (6000, "Search expert")]
STAGE_NAMES = {ar.SCREEN: "screening", ar.TECH: "technical", ar.FINAL: "final", ar.OFFER: "offer"}


def local_day(when: datetime) -> date:
    """The day by this computer's clock. A typo date (0001-01-01) — as is: Windows can't convert it to a zone before 1970."""
    try:
        return when.astimezone().date() if when.tzinfo else when.date()
    except (OSError, OverflowError, ValueError):
        return when.date()


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def tailored_stems(cv_dir: Path) -> set[str]:
    """Names of tailored CVs (without extension, lower case): "acme_python_developer"."""
    if not cv_dir.exists():
        return set()
    return {p.stem.lower() for p in cv_dir.rglob("*") if p.suffix in (".md", ".html", ".pdf")}


def has_tailored_cv(company: str, stems: set[str]) -> bool:
    prefix = slug(company).lower() + "_"
    return prefix != "cv_" and any(s.startswith(prefix) for s in stems)


@dataclass
class Ledger:
    """Events by day: one company on one day counts once, however many letters came."""
    applies: dict[date, set[str]] = field(default_factory=lambda: defaultdict(set))
    pings: dict[date, set[str]] = field(default_factory=lambda: defaultdict(set))
    stages: dict[date, set[str]] = field(default_factory=lambda: defaultdict(set))
    rejects: dict[date, set[str]] = field(default_factory=lambda: defaultdict(set))
    messages: dict[date, set[str]] = field(default_factory=lambda: defaultdict(set))  # correspondence: letters and pings

    def action_days(self) -> set[date]:
        return {d for book in (self.applies, self.pings, self.stages) for d, names in book.items() if names}


def ledger(apps: list[mail.Application]) -> Ledger:
    led = Ledger()
    for app in apps:
        cancelled = ar._cancelled_series(app.events)
        for e in app.events:
            d = local_day(e.when)
            if d.year < 2000:  # a typo date: otherwise the streak and weeks would count from year 1
                continue
            if e.kind == mail.APPLIED:
                led.applies[d].add(app.company)
            elif e.kind == mail.OTHER:
                led.messages[d].add(app.company)
                if not e.thread:  # a ping or a letter to a recruiter — written by hand
                    led.pings[d].add(app.company)
            elif e.kind in (mail.INTERVIEW, mail.OFFER) and e not in cancelled and not ar._CANCELLED_RX.search(e.subject):
                led.stages[d].add(app.company)
            elif e.kind == mail.REJECTED:
                led.rejects[d].add(app.company)
    return led


def is_workday(d: date) -> bool:
    return d.weekday() < 5


def _streak_walk(action_days: set[date], today: date) -> tuple[int, list[date]]:
    """(current streak, record days: [i] — the day the best streak first reached i + 1)."""
    records: list[date] = []
    if not action_days:
        return 0, records
    run = 0
    d = min(action_days)
    while d <= today:
        if is_workday(d):
            if d in action_days:
                run += 1
                if run > len(records):
                    records.append(d)
            elif d != today:
                run = 0
        d += timedelta(days=1)
    return run, records


def workdays_silent(action_days: set[date], today: date) -> int:
    """How many workdays in a row before today (not including it) had no action at all."""
    n, d = 0, today - timedelta(days=1)
    first = min(action_days, default=today)
    while d >= first and d not in action_days:
        n += is_workday(d)
        d -= timedelta(days=1)
    return n


def week_counts(led: Ledger) -> dict[date, int]:
    """Monday of the week -> applications that week (companies per day)."""
    weeks: dict[date, int] = defaultdict(int)
    for d, names in led.applies.items():
        weeks[monday(d)] += len(names)
    return weeks


def goal_weeks(weeks: dict[date, int], goal: int, today: date) -> tuple[int, int, int]:
    """(weeks with the goal met, the best and the current run of such weeks in a row) — over the whole history."""
    if not weeks:
        return 0, 0, 0
    total = run = best = 0
    w, last = min(weeks), monday(today)
    while w <= last:
        if weeks.get(w, 0) >= goal:
            total, run = total + 1, run + 1
            best = max(best, run)
        elif w != last:  # the current week is still going — falling short does not break the run
            run = 0
        w += timedelta(days=7)
    return total, best, run


def _goal_run_records(led: Ledger, goal: int, today: date) -> list[date]:
    """[i] — the day the run of goal weeks first reached i + 1 (the day the week reached the goal)."""
    reached: dict[date, date] = {}  # Monday -> the day the goal was reached
    count: dict[date, int] = defaultdict(int)
    for d in sorted(led.applies):
        w = monday(d)
        count[w] += len(led.applies[d])
        if count[w] >= goal and w not in reached:
            reached[w] = d
    records: list[date] = []
    if not count:
        return records
    run, w, last = 0, min(count), monday(today)
    while w <= last:
        if w in reached:
            run += 1
            if run > len(records):
                records.append(reached[w])
        elif w != last:
            run = 0
        w += timedelta(days=7)
    return records


def xp_breakdown(led: Ledger, staged: list[tuple[mail.Application, str]], stems: set[str]) -> dict[str, int]:
    """XP only for your own actions: an application 10, tailored (there is a CV in cv_out) 20, a ping 5."""
    tailored = {app.company for app, _ in staged if has_tailored_cv(app.company, stems)}
    applies = sum(XP["apply_tailored"] if n in tailored else XP["apply"] for names in led.applies.values() for n in names)
    return {"applications": applies, "pings": XP["ping"] * sum(len(n) for n in led.pings.values())}


def rank(xp: int) -> tuple[str, str | None, int]:
    """-> (rank, the next rank or None, XP left to it)."""
    current = RANKS[0][1]
    for threshold, name in RANKS:
        if xp >= threshold:
            current = name
        else:
            return current, name, threshold - xp
    return current, None, 0


def rank_progress(xp: int) -> float:
    lows = [t for t, _ in RANKS if t <= xp]
    highs = [t for t, _ in RANKS if t > xp]
    if not highs:
        return 1.0
    low = lows[-1]
    return (xp - low) / (highs[0] - low)


def _first_reply_after_ping(app: mail.Application) -> date | None:
    pings = [e.when for e in app.events if e.kind == mail.OTHER and not e.thread]
    if not pings:
        return None
    replies = [e.when for e in app.events if e.when > min(pings)
               and (e.thread or e.kind in (mail.INTERVIEW, mail.OFFER, mail.REJECTED))]
    return local_day(min(replies)) if replies else None


_DATE_DMY_RX = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d.])")  # 25.11, 25.11.2026
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
# "@ Wed Nov 25, 2026 2pm" — the subject of a calendar invitation
_DATE_EN_RX = re.compile(rf"\b({'|'.join(_MONTHS)})[a-z]*\.? (\d{{1,2}})(?:, (\d{{4}}))?\b", re.I)
RECENT_STAGE_DAYS = 30  # interview dates are looked for in letters and stage notes of this period


def _dates_in(text: str, base: date) -> list[date]:
    """Dates from a text; the year, if not given, is the nearest one forward from the letter date."""
    raw = [(int(m[1]), int(m[2]), m[3]) for m in _DATE_DMY_RX.finditer(text)]
    raw += [(int(m[2]), _MONTHS.index(m[1][:3].lower()) + 1, m[3]) for m in _DATE_EN_RX.finditer(text)]
    out = []
    for dd, mm, yy in raw:
        year = (int(yy) + 2000 if len(yy) == 2 else int(yy)) if yy else base.year + (mm < base.month)
        try:
            out.append(date(year, mm, dd))
        except ValueError:
            continue
    return out


def upcoming_interview(apps: list[mail.Application], today: date) -> tuple[str, date] | None:
    """The nearest interview within SOON_DAYS: a stage dated in the future or a date in a letter subject / stage note.

    "Tech round moved from 16.11 to 25.11", "Invitation: … @ Wed Nov 25, 2026" -> Nov 25.
    Companies whose last event is a rejection (by letter or by you) are skipped.
    """
    found: list[tuple[date, str]] = []
    for app in apps:
        meaningful = [e for e in app.ordered if e.kind != mail.OTHER]
        if not meaningful or meaningful[-1].kind in (mail.REJECTED, mail.DECLINED):
            continue
        for e in meaningful:
            base = local_day(e.when)
            if e.kind != mail.INTERVIEW or ar._CANCELLED_RX.search(e.subject) or (today - base).days > RECENT_STAGE_DAYS:
                continue
            days = [base, *_dates_in(e.subject, base)]
            found += [(d, app.company) for d in days if today <= d <= today + timedelta(days=SOON_DAYS)]
    if not found:
        return None
    d, company = min(found)
    return company, d


def waiting_long(apps: list[mail.Application], today: date) -> list[str]:
    """Companies with the "waiting for an answer" status and silence longer than PING_AFTER_DAYS — the longest first.

    The status is as on the Applications page, but without the applied-index marks (they are not passed here)."""
    now = datetime.combine(today, time(12), tzinfo=UTC)
    out = []
    for app in apps:
        status, _ = ar.effective_status(app, "", now)
        silent = (now - app.last.when).days
        if status == ar.WAITING and silent > PING_AFTER_DAYS:
            out.append((silent, app.company))
    return [c for _, c in sorted(out, key=lambda x: (-x[0], x[1]))]


def _same_company(a: str, b: str) -> bool:
    """Like the "already applied" mark in the report: the company key or a common prefix of 4+ letters."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def fit_leads(apps: list[mail.Application], jobs_dir: Path) -> tuple[str, list[str]] | None:
    """(the day of the latest report, companies with Fit ≥ LEAD_FIT where you haven't applied — best Fit first) or None."""
    files = sorted(jobs_dir.glob("????-??-??.json")) if jobs_dir.exists() else []
    if not files:
        return None
    try:
        jobs = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    applied = [k for k in (company_key(a.company) for a in apps) if len(k) >= 3]
    seen: set[str] = set()
    out = []
    for j in sorted((j for j in jobs if isinstance(j, dict)), key=lambda j: -(j.get("fit") or 0)):
        name = (j.get("company") or "").strip()
        key = company_key(name)
        if (j.get("fit") or 0) < LEAD_FIT or not name or key in seen or any(_same_company(key, k) for k in applied):
            continue
        seen.add(key)
        out.append(name)
    return (files[-1].stem, out) if out else None


# --- The next step of the week ----------------------------------------------------------------


@dataclass
class NextStep:
    """One challenge for the current situation: what to do and where to click."""
    kind: str  # interview / ping / leads / goal
    icon: str
    title: str
    detail: str = ""
    link: str = ""
    link_text: str = ""


def _names(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def next_step(today: date, soon: tuple[str, date] | None, waiting: list[str], leads: tuple[str, list[str]] | None,
              goal: int, week_n: int) -> NextStep:
    """Rules by priority, the first that fits: an interview soon -> waiting long for an answer -> strong vacancies
    in the report -> the weekly application goal."""
    if soon:
        company, when = soon
        left = (when - today).days
        on = "today" if left == 0 else "tomorrow" if left == 1 else f"on {short(when)}"
        return NextStep("interview", "🎤", f"Prepare for the interview with {company} {on}",
                        "Questions about the vacancy, stories about your projects and questions for the team.",
                        f"{WEB_URL}/prep", "Open interview prep")
    if waiting:
        k = min(3, len(waiting))
        return NextStep("ping", "📨", f"Follow up: {plural(k, 'company', 'companies')} silent "
                        f"for more than {PING_AFTER_DAYS} days",
                        _names(waiting, k) + ". A short letter to the recruiter — then log the ping on Applications.",
                        f"{WEB_URL}/applications", "Open Applications")
    if leads:
        day, companies = leads
        return NextStep("leads", "🔎", f"Look at 3 strong vacancies from the {short(date.fromisoformat(day))} report",
                        f"Fit ≥ {LEAD_FIT}%, no applications there yet: {_names(companies)}. "
                        f"In the report — the \"Fit ≥ {LEAD_FIT}%\" button.",
                        f"{WEB_URL}/reports?file=jobs_{day}.html", "Open the report")
    left = max(0, goal - week_n)
    if left == 0:
        return NextStep("goal", "⭐", f"Weekly goal met: {week_n} of {goal}",
                        "Now quality: a tailored application where the match is the strongest.")
    return NextStep("goal", "🎯", f"{plural(left, 'application')} left to the weekly goal",
                    f"{week_n} of {goal} so far. A tailored application with a CV from cv_out gives twice the XP.",
                    f"{WEB_URL}/reports", "Open reports")


# --- Message of the day ----------------------------------------------------------------------
# Situation -> templates. Only real numbers and companies from the data are filled in.
TEMPLATES: dict[str, list[str]] = {
    "soon_interview": [
        "{interview_when}: the interview with {interview_company}. Refresh your project stories and questions for them.",
        "The interview with {interview_company} is {interview_left}. A couple of practice tasks tonight and you're in shape.",
        "{interview_company} expects you {interview_on}. Reread the vacancy and your CV for it.",
        "Interview with {interview_company} {interview_on}. Prepare three questions for the team: you're choosing too.",
    ],
    "fresh_stage": [
        "A fresh stage with {stage_company}: you got to the {stage_name} stage. A great sign.",
        "{stage_company} moves you forward — {stage_name} already. Write down what they asked while you remember.",
        "A stage with {stage_company} is experience. What from the talk is worth brushing up for the next one?",
        "{stage_company}: {stage_name}. A good moment to note what worked in the application.",
    ],
    "fresh_reject": [
        "{reject_company} said no. That's about their vacancy, not about you.",
        "A rejection from {reject_company} — one less company that didn't fit. Next application: for a specific role.",
        "{reject_company} — a miss. {rejections_text} behind you, and none of them stopped the search.",
        "A rejection from {reject_company} is a reason to look at fresh vacancies: somewhere the match is stronger.",
    ],
    "silence": [
        "Yesterday was quiet. One tailored application today brings the rhythm back.",
        "A day without actions happens. Start small: ping a recruiter who has been silent for a while.",
        "A one-day pause is not a failure. Today one useful step is enough.",
    ],
    "long_silence": [
        "{silence_text} without actions. No need to catch up on everything — one application and the streak is back.",
        "A pause of {silence_text}. You can come back any day, one step is enough.",
        "A pause of {silence_text}. Look at fresh vacancies in the report: one will surely catch your eye.",
    ],
    "monday": [
        "A new week, the goal is {goal_text}. Starting with one good application is half the job.",
        "Monday: the week counter is at zero. Your record is {best_week_text} a week.",
    ],
    "streak": [
        "A streak of {streak_text} in a row. One action today is enough to keep it going.",
        "{streak_text} in a row with something done. That's what a systematic search looks like.",
        "A streak of {streak_text}. The best one was {best_streak_text}.",
    ],
    "goal_close": [
        "{left_text} left to the weekly goal. Doable today.",
        "The week is almost done: {week_n} of {goal}. {left_text} more and the ⭐ is yours.",
        "{left_text} left to the star of the week. Pick vacancies where the match is the strongest.",
    ],
    "goal_done": [
        "Weekly goal met: {week_n} of {goal}. The ⭐ is yours — from here quality matters more than numbers.",
        "{week_text} this week — the goal is closed. Time to prepare for interviews.",
        "The week counts. Now you can put effort into one strong application for a dream vacancy.",
    ],
    "near_record": [
        "The weekly record is {best_week_text}, now {week_n}. {to_record_text} to the record.",
        "{to_record_text} more and this is your best week.",
    ],
    "record": [
        "This week is your record: {week_text}. Keep it up.",
    ],
    "near_goal": [
        "Badge \"{goal_name}\" ({goal_rarity}): {goal_value} of {goal_target}. Almost there.",
        "The \"{goal_name}\" badge is within reach: {goal_value} of {goal_target}. Needed: {goal_about}.",
    ],
    "near_rank": [
        "{need_xp} XP to the \"{next_rank}\" rank. One tailored application is 20.",
        "{need_xp} XP more and you're \"{next_rank}\".",
    ],
    "generic": [
        "You're {rank}: {xp} XP over the whole search. Every tailored application is +20.",
        "{week_text} of {goal} this week. The quality of an application matters more than the number.",
        "An application for a specific vacancy gives twice the XP — and twice the chances.",
        "Stars for weeks with the goal met: {stars}.",
        "A job search is a marathon. Today one useful step is enough.",
    ],
}
URGENT = ("soon_interview", "fresh_stage", "fresh_reject")

QUOTES = [
    "Slow and steady wins the race.",
    "Little by little, a little becomes a lot.",
    "Constant dripping wears away the stone.",
    "Nothing ventured, nothing gained.",
    "The eyes are afraid, but the hands do the work.",
    "Well begun is half done.",
    "The only one who makes no mistakes is the one who does nothing.",
    "Better to try and regret than to regret not trying.",
    "A journey of a thousand miles begins with a single step. — Lao Tzu",
    "Falling is no shame. Staying down is.",
    "Failure is simply the opportunity to begin again, this time more intelligently. — Henry Ford",
    "Success is going from failure to failure without losing enthusiasm.",
    "Do what you must, come what may.",
    "Those who want to find a way, those who don't find an excuse.",
    "Don't be afraid of going slowly, be afraid of standing still.",
    "Sleep on it: the morning is wiser than the evening.",
    "Measure twice, cut once.",
    "One good application beats ten sent blindly.",
    "A rejection is an answer to a question, not a verdict.",
    "Every \"no\" brings you closer to a \"yes\".",
    "An interview is a conversation of two sides: you're choosing too.",
    "A strong CV is one written for the vacancy, not about everything at once.",
    "The market is not about you personally. It's about a match at the right moment.",
    "Rhythm beats bursts.",
    "A small step every workday is hundreds of steps a year.",
    "Your experience hasn't gone anywhere. The right people just haven't seen it yet.",
    "Good code is written in iterations. A good search too.",
    "No answer is an answer too. Move on.",
    "Today's application is tomorrow's interview.",
    "Mistakes in an interview are a free lesson before the next one.",
    "Don't compare your first step with someone else's tenth.",
    "Weekends are for rest. The streak doesn't break because of them.",
    "The best time to send an application was yesterday. The next best is today.",
    "Calm is a skill too. Especially in a technical round.",
    "What matters is not how many applications went out, but how many hit the target.",
    "Every interview makes the next one easier.",
    "If it's hard, you're climbing uphill.",
    "Done is better than perfect.",
    "Luck favors the prepared.",
]
QUOTE_EVERY_DAYS = 3


def message_of_day(ctx: dict, situations: list[str], today: date) -> str:
    """A template from the situations that fit today; urgent ones (an interview, a fresh stage, a rejection) first.

    The choice is deterministic by date: a page rebuilt later in the day shows the same message.
    """
    urgent = [s for s in situations if s in URGENT]
    pool_keys = urgent or [s for s in situations if s != "generic"] or ["generic"]
    pool = [t for s in pool_keys for t in TEMPLATES[s]]
    return random.Random(today.toordinal()).choice(pool).format(**ctx)


def quote_of_day(today: date) -> str | None:
    """A quote once every QUOTE_EVERY_DAYS days; the order cycles by date."""
    n = today.toordinal()
    return QUOTES[(n // QUOTE_EVERY_DAYS) % len(QUOTES)] if n % QUOTE_EVERY_DAYS == 0 else None


# --- Badges ---------------------------------------------------------------------------------

RARITIES = ["common", "rare", "legendary"]  # the 1st, 2nd and 3rd tier of a track
MIN_APPLIES_FOR_RATE = 20  # conversion is counted once there are enough applications for a percentage


@dataclass
class Badge:
    """A collection badge: value of target; met — earned (earned_on — when, if it can be computed)."""
    rarity: int  # index in RARITIES
    icon: str
    name: str
    about: str  # what is needed, with the target value
    value: int
    target: int
    unit: str = ""  # "%" for the conversion
    earned_on: date | None = None

    @property
    def earned(self) -> bool:
        return self.value >= self.target

    @property
    def ratio(self) -> float:
        return min(1.0, self.value / self.target) if self.target else 1.0


# A track: icon, what is counted (about N), and three tiers (name, target) — common, rare, legendary
TRACKS: list[tuple[str, str, str, list[tuple[str, int]]]] = [
    # tailored CVs — how precisely we hit the role: a match -> a bullseye -> product–market fit
    ("tailored", "🧩", "applications with a tailored CV: {n}",
     [("Match", 5), ("Bullseye", 20), ("Product–market fit", 50)]),
    ("screens", "📞", "companies where you got to screening: {n}",
     [("First contact", 3), ("Wanted candidate", 10), ("In demand", 30)]),
    ("techs", "🎯", "companies where you got to a technical round: {n}",
     [("Sniper", 1), ("Sharpshooter", 5), ("Techie", 12)]),
    ("best_streak", "🔥", "workdays in a row with actions: {n}", [("On fire", 5), ("Rhythm", 10), ("Marathoner", 20)]),
    ("goal_run", "⭐", "weeks in a row with the goal met: {n}",
     [("Two stars", 2), ("Three stars", 3), ("Five stars", 5)]),
    ("rejections", "💪", "rejections survived: {n}", [("Unbreakable", 10), ("Armor", 40), ("Titan", 100)]),
]
# One-off badges: (rarity, what is counted, icon, name, what is needed, target, unit)
SINGLES: list[tuple[int, str, str, str, str, int, str]] = [
    (0, "icebreakers", "🧊", "Icebreaker", "you wrote to a recruiter first and they answered", 1, ""),
    (1, "rate", "📈", "On target",
     f"application → screening conversion of {{n}}% or more (with {MIN_APPLIES_FOR_RATE}+ applications)", 15, "%"),
    (2, "finals", "🏁", "Finalist", "got to a final stage", 1, ""),
]


def _stage_days(led: Ledger) -> dict[str, list[date]]:
    """Company -> stage days in order."""
    out: dict[str, list[date]] = defaultdict(list)
    for d in sorted(led.stages):
        for n in led.stages[d]:
            out[n].append(d)
    return out


def goal_stats(led: Ledger, staged: list[tuple[mail.Application, str]], stems: set[str], today: date,
               goal: int) -> tuple[dict[str, int], dict[str, list[date]]]:
    """(badge counters over the whole history, days: [i] — when the counter reached i + 1).

    Stage days are approximate: screening — the first stage with a company, technical — the second, final — the last;
    a stage known only from the applied index (no letter or note) has no date. The conversion has no days."""
    rank_of = {s: i for i, (s, _) in enumerate(ar.FUNNEL)}
    tailored = {app.company for app, _ in staged if has_tailored_cv(app.company, stems)}
    applied = {n for names in led.applies.values() for n in names}
    days_of = _stage_days(led)
    reached: dict[str, list[date]] = {"screens": [], "techs": [], "finals": []}
    counts = {"screens": 0, "techs": 0, "finals": 0}
    for app, stage in staged:
        r, days = rank_of[stage], days_of.get(app.company, [])
        for key, need, pick in (("screens", ar.SCREEN, 0), ("techs", ar.TECH, 1), ("finals", ar.FINAL, -1)):
            if r >= rank_of[need]:
                counts[key] += 1
                if days:
                    reached[key].append(days[min(pick, len(days) - 1)] if pick >= 0 else days[-1])
    first_reject: dict[str, date] = {}
    for d in sorted(led.rejects):
        for n in led.rejects[d]:
            first_reject.setdefault(n, d)
    ice = [d for d in (_first_reply_after_ping(a) for a, _ in staged) if d]
    _, streak_records = _streak_walk(led.action_days(), today)
    run_records = _goal_run_records(led, goal, today)
    stats = {
        "tailored": sum(n in tailored for names in led.applies.values() for n in names),
        **counts,
        "best_streak": len(streak_records),
        "goal_run": len(run_records),
        "rejections": len(first_reject),
        "icebreakers": len(ice),
        "rate": round(100 * counts["screens"] / len(applied)) if len(applied) >= MIN_APPLIES_FOR_RATE else 0,
    }
    dates = {
        "tailored": [d for d in sorted(led.applies) for n in led.applies[d] if n in tailored],
        **{k: sorted(v) for k, v in reached.items()},
        "best_streak": streak_records, "goal_run": run_records,
        "rejections": sorted(first_reject.values()), "icebreakers": sorted(ice), "rate": [],
    }
    return stats, dates


def badges(stats: dict[str, int], dates: dict[str, list[date]] | None = None) -> list[Badge]:
    """The collection: three tiers in every track (common, rare, legendary), plus one-off badges."""
    dates = dates or {}

    def on(key: str, target: int) -> date | None:
        got = dates.get(key, [])
        return got[target - 1] if stats[key] >= target and len(got) >= target else None

    out = [Badge(i, icon, name, about.format(n=target), stats[key], target, earned_on=on(key, target))
           for key, icon, about, levels in TRACKS for i, (name, target) in enumerate(levels)]
    out += [Badge(r, icon, name, about.format(n=target), stats[key], target, unit, on(key, target))
            for r, key, icon, name, about, target, unit in SINGLES]
    return out


def showcase(all_badges: list[Badge], limit: int = SHOWCASE) -> list[Badge]:
    """Badges on the main screen: recently earned and the closest to earning — half and half, the shortfall is topped up."""
    earned = sorted((b for b in all_badges if b.earned),
                    key=lambda b: (b.earned_on or date.min, b.rarity), reverse=True)
    near = sorted((b for b in all_badges if not b.earned), key=lambda b: (-b.ratio, b.rarity, b.target))
    half = limit // 2
    take_e = min(len(earned), max(half, limit - len(near)))
    take_n = min(len(near), limit - take_e)
    return earned[:take_e] + near[:take_n]


# --- Recent wins --------------------------------------------------------------------------------


@dataclass
class Win:
    weight: int  # lower — more important
    day: date
    icon: str
    text: str


def recent_wins(today: date, led: Ledger, staged: list[tuple[mail.Application, str]], got: list[Badge],
                weeks: dict[date, int], streak_records: list[date], limit: int = WINS_LIMIT) -> list[Win]:
    """Events within WINS_DAYS by significance: an offer, a final, a technical round, screening, a new badge, a weekly
    and a streak record. One of each kind (up to two badges), so that five screenings don't hide the rest."""
    since = today - timedelta(days=WINS_DAYS)
    days_of = _stage_days(led)
    wins: list[Win] = []
    stage_win = {ar.OFFER: (0, "🏆", "Offer from {}"), ar.FINAL: (1, "🏁", "Final with {}"),
                 ar.TECH: (2, "🎯", "Technical round with {}"), ar.SCREEN: (3, "📞", "Screening with {}")}
    for app, stage in staged:
        days = days_of.get(app.company, [])
        if stage in stage_win and days and since <= days[-1] <= today:
            w, icon, text = stage_win[stage]
            wins.append(Win(w, days[-1], icon, text.format(app.company)))
    wins += [Win(4, b.earned_on, b.icon, f"Badge \"{b.name}\" ({RARITIES[b.rarity]})")
             for b in got if b.earned_on and since <= b.earned_on <= today]
    if weeks:
        best = max(weeks.values())
        start = min(x for x in weeks if weeks[x] == best)  # the first week with this record
        if best > 0 and since <= start <= today:
            wins.append(Win(5, min(start + timedelta(days=6), today), "📈",
                            f"Weekly record: {plural(best, 'application')}"))
    if len(streak_records) >= 2 and since <= streak_records[-1] <= today:
        n = len(streak_records)
        wins.append(Win(6, streak_records[-1], "🔥", f"Streak record: {plural(n, 'workday')}"))
    wins.sort(key=lambda x: (x.weight, -x.day.toordinal()))
    out: list[Win] = []
    for x in wins:
        if sum(y.weight == x.weight for y in out) < (2 if x.weight == 4 else 1):
            out.append(x)
    return out[:limit]


# --- Assembly --------------------------------------------------------------------------------


@dataclass
class Game:
    today: date
    goal: int
    week_n: int
    best_week: int
    best_week_start: date | None
    stars: int
    streak: int
    best_streak: int
    xp: int
    xp_parts: dict[str, int]
    rank: str
    next_rank: str | None
    need_xp: int
    progress: float
    heat: list[list[pc.HexDay | None]]  # weeks (columns) × days Mon–Sun; None — the day hasn't come yet
    daily: list[dict]  # events by day over 2 × the longest chart period (to compare with the previous one)
    badges: list[Badge]
    step: NextStep
    wins: list[Win]
    message: str
    quote: str | None


def build(today: date, staged: list[tuple[mail.Application, str]], goal: int | None = None,
          cv_dir: Path | None = None, jobs_dir: Path | None = None) -> Game:
    """staged — companies and the furthest stage with each (as in the funnel of the Applications page).
    goal — the weekly goal; None — [gamification] weekly_goal from settings.toml."""
    if goal is None:
        goal = settings.get().gamification.weekly_goal
    apps = [a for a, _ in staged]
    led = ledger(apps)
    days = led.action_days()
    streak, streak_records = _streak_walk(days, today)
    best_streak = len(streak_records)
    weeks = week_counts(led)
    this_week = monday(today)
    week_n = weeks.get(this_week, 0)
    best_week = max(weeks.values(), default=0)
    stars, _, _ = goal_weeks(weeks, goal, today)
    stems = tailored_stems(cv_dir or CV_DIR)
    parts = xp_breakdown(led, staged, stems)
    xp = sum(parts.values())
    rank_name, next_rank, need = rank(xp)
    stats, dates = goal_stats(led, staged, stems, today, goal)
    rejections = stats["rejections"]
    got = badges(stats, dates)

    soon = upcoming_interview(apps, today)
    step = next_step(today, soon, waiting_long(apps, today), fit_leads(apps, jobs_dir or JOBS_DIR), goal, week_n)

    # --- situations for the message of the day ---
    ctx: dict = {
        "goal": goal, "goal_text": plural(goal, "application"), "week_n": week_n,
        "week_text": plural(week_n, "application"),
        "best_week_text": plural(best_week, "application"),
        "left_text": plural(goal - week_n, "application"),
        "streak_text": plural(streak, "workday"),
        "best_streak_text": plural(best_streak, "day"),
        "xp": xp, "rank": rank_name, "next_rank": next_rank or "", "need_xp": need, "stars": stars,
        "rejections": rejections, "rejections_text": plural(rejections, "rejection"),
    }
    situations = ["generic"]
    silent = workdays_silent(days, today) if days else 0
    if silent >= 3:
        situations.append("long_silence")
        ctx["silence_text"] = plural(silent, "workday")
    elif silent >= 1:
        situations.append("silence")
    if today.weekday() == 0 and week_n == 0:
        situations.append("monday")
    if streak >= 2:
        situations.append("streak")
    if week_n >= goal:
        situations.append("goal_done")
    elif goal - week_n <= 2:
        situations.append("goal_close")
    if 0 < week_n == best_week:
        situations.append("record")
    elif week_n > 0 and best_week - week_n <= 2:
        situations.append("near_record")
        ctx["to_record_text"] = plural(best_week - week_n + 1, "application")
    close = sorted((b for b in got if not b.earned and b.value > 0 and b.value >= 0.8 * b.target),
                   key=lambda b: b.rarity)
    if close:
        b = close[0]  # the easiest of the almost earned
        situations.append("near_goal")
        ctx |= {"goal_name": f"{b.icon} {b.name}", "goal_rarity": RARITIES[b.rarity], "goal_about": b.about,
                "goal_value": f"{b.value}{b.unit}", "goal_target": f"{b.target}{b.unit}"}
    if next_rank and need <= 50:
        situations.append("near_rank")
    recent = [today - timedelta(days=i) for i in range(FRESH_DAYS)]
    rejected = [n for d in recent for n in sorted(led.rejects.get(d, ()))]
    if rejected:
        situations.append("fresh_reject")
        ctx["reject_company"] = rejected[0]
    stage_of = {a.company: s for a, s in staged}
    staged_now = [n for d in recent for n in sorted(led.stages.get(d, ())) if stage_of.get(n) in STAGE_NAMES]
    if staged_now:
        situations.append("fresh_stage")
        ctx["stage_company"] = staged_now[0]
        ctx["stage_name"] = STAGE_NAMES[stage_of[staged_now[0]]]
    if soon:
        situations.append("soon_interview")
        company, when = soon
        left = (when - today).days
        ctx["interview_company"] = company
        ctx["interview_when"] = "Today" if left == 0 else "Tomorrow" if left == 1 else short(when)
        ctx["interview_on"] = "today" if left == 0 else "tomorrow" if left == 1 else f"on {short(when)}"
        ctx["interview_left"] = "today" if left == 0 else "in " + plural(left, "day")

    return Game(
        today=today, goal=goal, week_n=week_n, best_week=best_week,
        best_week_start=max(weeks, key=lambda w: (weeks[w], w)) if weeks else None, stars=stars,
        streak=streak, best_streak=best_streak, xp=xp, xp_parts=parts, rank=rank_name, next_rank=next_rank,
        need_xp=need, progress=rank_progress(xp), heat=pc.hex_grid(led, today),
        daily=pc.daily(led, today, 2 * max(pc.PERIODS)), badges=got, step=step,
        wins=recent_wins(today, led, staged, got, weeks, streak_records),
        message=message_of_day(ctx, situations, today), quote=quote_of_day(today),
    )


# --- HTML ----------------------------------------------------------------------------------


CSS = """
/* blocks — like .card of the UI (web/static/app.css): card background, border, radius, spacing from the scale */
.game { background: var(--card); border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: var(--sh-1);
        padding: var(--sp-4); margin: 0 0 var(--sp-4); }
.game h2 { font-size: var(--fs-lg); font-weight: var(--fw-medium); margin: 0 0 var(--sp-3); }
.game .meter { height: 6px; background: var(--track); border-radius: var(--r-pill); overflow: hidden;
               margin-top: var(--sp-1); }
.game .meter > div { height: 100%; background: var(--accent); }
.game .meter.goal > div { background: var(--ok); }
/* hero: rank and XP, streak, message of the day */
.game.hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--sp-3) var(--sp-5); align-items: start; }
.game.hero .rank { font-size: var(--fs-xl); font-weight: var(--fw-bold); }
.game.hero .xp { max-width: 360px; }
.game.hero .streak { text-align: right; }
.game.hero .streak[data-tip]::after { left: auto; right: 0; text-align: left; }  /* at the right edge — to the left */
.game.hero .streak b { font-size: var(--fs-xl); }
.game.hero .msg { grid-column: 1 / -1; font-size: var(--fs-lg); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.game.hero .quote { color: var(--muted); font-style: italic; font-size: var(--fs-sm); margin-left: var(--sp-1); }
/* next step */
.game.next .title { font-size: var(--fs-lg); font-weight: var(--fw-medium); }
.game.next .detail { margin: var(--sp-1) 0 var(--sp-3); }
.game.next .go { display: inline-flex; align-items: center; height: var(--ctl-h); padding: 0 var(--ctl-px);
                 border-radius: var(--r-md); background: var(--accent); color: var(--on-accent); text-decoration: none;
                 font-weight: var(--fw-medium); }
.game.next .week { margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.game.next .week .meter { max-width: 360px; }
/* wins */
.game .wins { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--sp-2); }
.game .wins li { display: flex; gap: var(--sp-2); align-items: baseline; }
.game .wins .when { margin-left: auto; white-space: nowrap; }
/* badge collection */
.game .badges { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--sp-2);
                grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
.game .badge { border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-2) var(--sp-3); }
.game .badge .icon { font-size: var(--fs-lg); }
.game .badge.off .icon { filter: grayscale(1); opacity: .5; }
.game .badge .meter { height: 4px; }
.game .badge .tag.r1 { background: var(--accent-soft); color: var(--accent); }
.game .badge .tag.r2 { background: var(--warn-soft); color: var(--warn); }
.game .badge .tag.new { display: none; }
.game .badge.fresh { border-color: var(--accent); box-shadow: var(--focus); }
.game .badge.fresh .tag.new { display: inline-block; }
.game details { margin-top: var(--sp-3); }
.game details > summary { cursor: pointer; color: var(--accent); }
.game details > .badges { margin-top: var(--sp-3); }
details.game > summary { font-weight: var(--fw-medium); }
@media (max-width: 600px) {
  .game.hero { grid-template-columns: 1fr; }
  .game.hero .streak { text-align: left; }
  .game .badges { grid-template-columns: 1fr; }
}
"""

# "new" on badges earned since the page was last opened: the list of earned ones is in the browser's localStorage.
# The first visit marks nothing. The page from reports/ in the /progress frame has the same origin as the UI.
SCRIPT = """<script>
try {
  const KEY = 'jobhunter.progress.badges';
  const got = [...document.querySelectorAll('.badge[data-badge]:not(.off)')];
  const seen = JSON.parse(localStorage.getItem(KEY) || 'null');
  if (Array.isArray(seen)) got.forEach(el => { if (!seen.includes(el.dataset.badge)) el.classList.add('fresh'); });
  localStorage.setItem(KEY, JSON.stringify(got.map(el => el.dataset.badge)));
} catch (e) { /* no localStorage — just no "new" mark */ }
</script>"""

def hero_html(g: Game) -> str:
    """The main block: rank, XP with a bar to the next one, streak, message of the day."""
    xp_title = ("Experience over the whole search — only for your own actions: "
                + ", ".join(f"{k} {v}" for k, v in g.xp_parts.items())
                + ". An application 10 XP, tailored (there is a CV in cv_out) 20, a ping to a recruiter 5. "
                "Stages, offers and rejections are employer decisions: they are in Recent wins, not in XP")
    next_line = f"{g.need_xp} XP to \"{_e(g.next_rank)}\"" if g.next_rank else "the highest rank"
    streak_title = ("Workdays in a row with at least one action: an application, a stage or a ping to a recruiter. "
                    f"Saturday and Sunday don't break the streak. The best streak is {g.best_streak}.")
    quote = f'<span class="quote">"{_e(g.quote)}"</span>' if g.quote else ""
    return (
        '<section class="game hero">'
        f'<div class="xp" data-tip="{_e(xp_title)}" tabindex="0"><div class="rank">🎖️ {_e(g.rank)}</div>'
        f'<div class="meter"><div style="width:{round(100 * g.progress)}%"></div></div>'
        f'<span class="small">{g.xp} XP · {next_line}</span></div>'
        f'<div class="streak" data-tip="{_e(streak_title)}" tabindex="0">🔥 <b>{g.streak}</b>'
        f'<div class="small">{"workday" if g.streak == 1 else "workdays"} in a row</div></div>'
        f'<div class="msg">💬 {_e(g.message)}{quote}</div>'
        "</section>"
    )


def next_html(g: Game) -> str:
    """One next step and the weekly application goal."""
    s = g.step
    link = f'<a class="go" href="{_e(s.link)}" target="_top">{_e(s.link_text)}</a>' if s.link else ""
    detail = f'<div class="detail small">{_e(s.detail)}</div>' if s.detail else ""
    goal_pct = min(100, round(100 * g.week_n / g.goal)) if g.goal else 100
    goal_title = (f"Applications this week (one company per day — one application). "
                  f"Reach {g.goal} and the week gets a ⭐.")
    week = ("" if s.kind == "goal" else
            f'<div class="week small" data-tip="{_e(goal_title)}" tabindex="0">Week: <b>{g.week_n} of {g.goal}</b> '
            f'applications<div class="meter goal"><div style="width:{goal_pct}%"></div></div></div>')
    if s.kind == "goal":
        link += (f'<div class="meter goal" style="max-width:360px;margin-top:var(--sp-3)">'
                 f'<div style="width:{goal_pct}%"></div></div>')
    return (
        '<section class="game next"><h2>Next step</h2>'
        f'<div class="title">{s.icon} {_e(s.title)}</div>{detail}{link}{week}</section>'
    )


def wins_html(g: Game) -> str:
    if g.wins:
        items = "".join(f'<li><span>{w.icon}</span><span>{_e(w.text)}</span>'
                        f'<span class="when small">{short(w.day)}</span></li>' for w in g.wins)
        body = f'<ul class="wins">{items}</ul>'
    else:
        body = f'<div class="small">No events in {WINS_DAYS} days yet — the next application may be the first.</div>'
    return f'<section class="game"><h2>Recent wins</h2>{body}</section>'


def _badge_html(b: Badge) -> str:
    pct = round(100 * b.ratio)
    state = (f"✅ {b.earned_on.isoformat()}" if b.earned_on else "✅ earned") if b.earned else \
        f"{b.value}{b.unit} of {b.target}{b.unit}"
    meter = "" if b.earned else f'<div class="meter"><div style="width:{pct}%"></div></div>'
    return (f'<li class="badge{"" if b.earned else " off"}" data-badge="{_e(b.name)}" data-tip="{_e(b.about)}" tabindex="0">'
            f'<span class="icon">{b.icon}</span> <b>{_e(b.name)}</b> '
            f'<span class="tag r{b.rarity}">{RARITIES[b.rarity]}</span> <span class="tag new">new</span>'
            f'<div class="small">{_e(b.about)}</div><div class="small">{state}</div>{meter}</li>')


def badges_html(g: Game) -> str:
    """The collection: up to SHOWCASE badges on the main screen, the rest under a collapsed "All badges"."""
    shown = showcase(g.badges)
    rest = [b for b in g.badges if b not in shown]
    rest.sort(key=lambda b: (not b.earned, b.rarity, -b.ratio))
    done = sum(b.earned for b in g.badges)
    more = (f'<details><summary>All badges ({done} of {len(g.badges)})</summary>'
            f'<ul class="badges">{"".join(_badge_html(b) for b in rest)}</ul></details>') if rest else ""
    return (f'<section class="game ach"><h2>Badges · {done} of {len(g.badges)}</h2>'
            f'<ul class="badges">{"".join(_badge_html(b) for b in shown)}</ul>{more}</section>')


def activity_html(g: Game) -> str:
    """The hex map over pc.HEX_WEEKS weeks and the summary (the streak is in the main block): busiest day, goal weeks, weekly record."""
    extra = [("Weeks with the goal ⭐", f'{g.stars} <span class="small">({g.goal}+ applications)</span>')]
    if g.best_week_start:
        end = g.best_week_start + timedelta(days=6)
        extra.append(("Weekly record", f"{_e(plural(g.best_week, 'application'))} "
                                       f'<span class="small">{short(g.best_week_start)}–{short(end)}</span>'))
    return pc.hexes_html(g.heat, g.today, extra)


def page(today: date, staged: list[tuple[mail.Application, str]]) -> str:
    """reports/progress.html — the Progress page (in the web UI — an item of the left panel)."""
    g = build(today, staged)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Progress — {today.isoformat()}</title>{THEME_JS}<style>{_CSS}{CSS}{pc.CSS}</style></head><body>"
        f"<h1>Search progress — {today.isoformat()}</h1>"
        f"{hero_html(g)}{next_html(g)}{pc.trend_html(g.daily)}{activity_html(g)}{wins_html(g)}{badges_html(g)}"
        f"{SCRIPT}{pc.SCRIPT}</body></html>"
    )
