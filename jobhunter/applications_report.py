"""HTML page with application statuses from mail correspondence (in the style of the vacancy report)."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from urllib.parse import quote

from . import geo, mail
from .html_report import _CSS, _JS, THEME_JS, _e, _table

# Final statuses and row colours
OFFER, ACTIVE, WAITING, DECLINED, REJECTED = "offer", "active", "waiting", "declined", "rejected"
STATUS = {  # status -> (label, row CSS class)
    OFFER: ("🎉 Offer", "c-green"),
    ACTIVE: ("🟢 In stages", "c-green"),
    WAITING: ("🟡 Waiting for an answer", "c-yellow"),
    DECLINED: ("🟠 Declined by you", "c-orange"),
    REJECTED: ("🔴 Rejected", "c-red"),
}
# No answer for longer than this means a rejection: two weeks of silence after an application or a stage is no longer waiting
TIMEOUT_DAYS = int(geo.S.get("mail", {}).get("timeout_days", 14))

# statuses from applications_manual.toml (a company can have several rows: "Applied, Rejected"); Russian words are for older files
_SHEET_STAGE_RX = re.compile(r"interview|offer|stage|собес|интервью|тестов|оффер|этап", re.I)
_SHEET_DECLINED_RX = re.compile(r"declin|отказался|отказалась", re.I)
_SHEET_REJECTED_RX = re.compile(r"^(?:reject|отказ\b)", re.I)
_SHEET_IGNORED_RX = re.compile(r"ignor|игнор", re.I)
KIND_NAMES = {
    mail.OFFER: "offer", mail.INTERVIEW: "stage", mail.APPLIED: "applied", mail.REJECTED: "rejected", mail.DECLINED: "declined by you", mail.OTHER: "letter",
}
# Funnel: how far you got with a company. The stage of a letter/note is taken from words in the subject; one stage brings
# a batch of letters (invitation, confirmation, reminder), so we count the furthest stage, not the letters.
SCREEN, TECH, FINAL = "screen", "tech", "final"
FUNNEL = [("applied", "Applied"), (SCREEN, "screening"), (TECH, "technical"), (FINAL, "final"), (OFFER, "offer")]
_FUNNEL_RANK = {k: i for i, (k, _) in enumerate(FUNNEL)}
_CANCELLED_RX = re.compile(r"cancel|отмен", re.I)
_PLAN_RX = re.compile(r"дальше|далее|\bnext\b|потом|→", re.I)
_FINAL_RX = re.compile(r"финал|\bfinal\b|\bcto\b|\bceo\b|\bvp\b|директор|founder|основател|offer call", re.I)
_TECH_RX = re.compile(
    r"\btech(?:nical)?\b|техни|техинтерв|техсобес|техскрин|live[\s-]?cod|лайв[\s-]?код|coding|system design|алгоритм|algorithm|тестов|take[\s-]?home|"
    r"home ?task|assignment|разбор тз|(?<![а-яё])тз(?![а-яё])|code review|engineering interview|"
    r"with (?:the )?team|с командой|проджект|руководител|team ?lead|тимлид|hiring manager|\b2nd\b|\bsecond\b|"
    r"\b3rd\b|\bthird\b|1-?й раунд|2-?й раунд",
    re.I,
)


SERIES_GAP_DAYS = 14  # letters about one stage (picking a slot, confirmation, reminder) come in a tight series


def _cancelled_series(events) -> list:
    """Stage letters that never happened: a series of letters about a stage whose last one is a cancellation.

    Example: "Select availability for Screen Call" → "Confirmation" → "Reminder" → "Interview cancelled".
    Series are separated by a gap of more than SERIES_GAP_DAYS days. Events written by hand (applications_manual.toml,
    they have no letter) are never dropped: this is what the owner knows for sure.
    """
    series, current, last = [], [], None
    for e in sorted((e for e in events if e.kind == mail.INTERVIEW), key=lambda e: e.when):
        if last and (e.when - last).days > SERIES_GAP_DAYS:
            series.append(current)
            current = []
        current.append(e)
        last = e.when
    if current:
        series.append(current)
    return [e for s in series if _CANCELLED_RX.search(s[-1].subject) for e in s if e.thread]


def _base_status(app) -> str:
    """The last meaningful event, without meeting cancellation letters: "Meeting cancelled" after a rejection is not a new stage."""
    meaningful = [e for e in app.ordered if e.kind != mail.OTHER
                  and not (e.kind == mail.INTERVIEW and _CANCELLED_RX.search(e.subject))]
    return meaningful[-1].kind if meaningful else app.status


def funnel_stage(app, in_sheet: str = "") -> str:
    """The furthest stage with a company: applied / screen / tech / final / offer."""
    best = "applied"
    cancelled = _cancelled_series(app.events)

    def bump(stage):
        nonlocal best
        if _FUNNEL_RANK[stage] > _FUNNEL_RANK[best]:
            best = stage

    for e in app.events:
        if e.kind == mail.OFFER:
            bump(OFFER)
        elif e.kind == mail.INTERVIEW and e not in cancelled:
            done = _PLAN_RX.split(e.subject, 1)[0]  # "…; next: take-home task" is a plan, not a stage passed
            bump(FINAL if _FINAL_RX.search(done) else TECH if _TECH_RX.search(done) else SCREEN)
    for s in (in_sheet or "").split(","):
        s = s.strip()
        if re.search(r"offer|оффер", s, re.I):
            bump(OFFER)
        elif re.search(r"тестов|техни", s, re.I):
            bump(TECH)
        elif _SHEET_STAGE_RX.search(s):
            bump(SCREEN)
    return best


def funnel_line(stages: list[str]) -> str:
    """"Applied 127 → screening 25 (20%) → …": how many companies got at least to a stage; % — of the applications."""
    total = len(stages)
    parts = []
    for key, label in FUNNEL:
        n = sum(_FUNNEL_RANK[s] >= _FUNNEL_RANK[key] for s in stages)
        pct = f" <span class=small>({n / total:.0%})</span>" if total and key != "applied" else ""
        parts.append(f"{label} <b>{n}</b>{pct}")
    return " → ".join(parts)


_CSS_COLORS = """
/* status colors — from the tokens.css palette; orange ("declined by you") is a mix of --warn and --bad */
.c-green { background: var(--ok-soft); } .c-yellow { background: var(--warn-soft); }
.c-orange { background: color-mix(in srgb, color-mix(in srgb, var(--warn), var(--bad)) 16%, transparent); }
.c-red { background: var(--bad-soft); }
tbody tr.c-green:hover, tbody tr.c-yellow:hover, tbody tr.c-orange:hover, tbody tr.c-red:hover { filter: brightness(.97); }
.legend span { display: inline-block; padding: 1px 8px; border-radius: var(--r-pill); margin-right: 6px; font-size: var(--fs-sm); }
.funnel { font-size: var(--fs-lg); margin: 0 0 var(--sp-2); }
"""


def _thread_link(address: str, thread: str) -> str:
    return f"https://mail.google.com/mail/?authuser={quote(address)}#all/{thread}"


def _last_cell(event, address: str) -> str:
    """The last event: a link to the letter or (for Telegram / LinkedIn) the text and the source."""
    if event.thread:
        return f'<a href="{_e(_thread_link(address, event.thread))}" target="_blank" rel="noopener">{_e(event.subject[:90])}</a>'
    return f"{_e(event.subject[:120])} <i>({_e(event.sender)})</i>"


def effective_status(app, in_applied: str, now: datetime) -> tuple[str, str]:
    """Final company status -> (status, explanation).

    🟢 in stages: a stage invitation by mail, or an interview / take-home task in applications_manual.toml;
    🟡 waiting for an answer: there was an application, no answer yet, but no more than TIMEOUT_DAYS have passed;
    🟠 declined by you: the last event in applications_manual.toml is declined;
    🔴 rejected: a rejection letter, "Rejected" in applications_manual.toml, or silence longer than TIMEOUT_DAYS.
    A rejection as the very last event outweighs a stage from the file. A new application by mail after a rejection letter is a new attempt: it waits for an answer again.
    """
    sheet = [s.strip() for s in in_applied.split(",") if s.strip()] if in_applied else []
    silent = max(0, (now - app.last.when).days)  # an event in the future (a scheduled meeting) is not silence
    if app.status == mail.OFFER:
        return OFFER, ""
    if app.status == mail.DECLINED:  # a new application or stage after declining makes the process alive again
        return DECLINED, ""
    stage_rows = [s for s in sheet if _SHEET_STAGE_RX.search(s)]
    # company statuses have no dates, so a rejection as the very last event outweighs a stage
    if stage_rows and app.last.kind != mail.REJECTED:
        return ACTIVE, f"by applications: {stage_rows[0]}"
    rejects = [e.when for e in app.events if e.kind == mail.REJECTED]
    reapplied = bool(rejects) and any(e.kind == mail.APPLIED and e.when > max(rejects) for e in app.events)
    base, why = _base_status(app), ""
    if reapplied:
        # a new process starts with the first application after the last rejection; stages before it
        # (for example, a cancelled call about another vacancy) do not belong to it
        restart = min(e.when for e in app.events if e.kind == mail.APPLIED and e.when > max(rejects))
        dropped = _cancelled_series(app.events)
        later = [e for e in app.events if e.when > restart and e.kind == mail.INTERVIEW and e not in dropped]
        base, why = (mail.INTERVIEW if later else mail.APPLIED), "applied again after a rejection"
    elif any(_SHEET_DECLINED_RX.search(s) for s in sheet):
        return DECLINED, ""
    elif any(_SHEET_REJECTED_RX.search(s) for s in sheet):
        return REJECTED, "marked in applications_manual.toml"
    if base == mail.REJECTED:
        return REJECTED, "rejection in the correspondence"
    if silent > TIMEOUT_DAYS:
        after = "a stage" if base == mail.INTERVIEW else "the application"
        return REJECTED, f"no answer for {silent} days after {after}"
    if base == mail.INTERVIEW:
        return ACTIVE, why
    if not why and any(_SHEET_IGNORED_RX.search(s) for s in sheet):
        why = "marked \"Ignored\""
    return WAITING, why or f"{silent} days without an answer"


class StatusIndex:
    """Application statuses by company — for marks in the vacancy report and the "📬 Applications" block.

    Built from the local mail cache and applications_manual.toml, without requests to Gmail
    (applications.py updates the cache).
    """

    NUDGE_AFTER_DAYS = 5  # longer silence after a stage — time to follow up

    def __init__(self, sheet=None):
        import json

        from . import config
        from .keys import company_key

        self._key = company_key
        messages = []
        if config.MAIL_CACHE_FILE.exists():
            messages = list(json.loads(config.MAIL_CACHE_FILE.read_text(encoding="utf-8")).values())
        apps, _ = mail.summarize(messages)
        self.now = datetime.now(UTC)
        self.items = []  # (company key, app, status, explanation)
        for app in apps:
            in_applied = sheet.company_statuses(app.company) if sheet else ""
            status, why = effective_status(app, in_applied, self.now)
            self.items.append((company_key(app.company), app, status, why))

    def lookup(self, company: str) -> str:
        """"🔴 Rejected — rejection in the correspondence, 03.09", or '' — no applications to this company."""
        key = self._key(company)
        if len(key) < 3:
            return ""
        for k, app, status, why in self.items:
            if k == key or (min(len(k), len(key)) >= 4 and (k.startswith(key) or key.startswith(k))):
                return f"{STATUS[status][0]}{f' — {why}' if why else ''}, {app.last.when:%d.%m}"
        return ""

    def digest(self) -> dict:
        """Counters by status and who it is time to follow up with."""
        nudge = []
        for _, app, status, _why in self.items:
            silent = max(0, (self.now - app.last.when).days)
            if status == ACTIVE and silent >= self.NUDGE_AFTER_DAYS:
                nudge.append((app.company, silent, app.last.subject[:90]))
        counts = {s: sum(1 for *_, st, _w in self.items if st == s) for s in STATUS}
        return {
            "nudge": sorted(nudge, key=lambda x: -x[1]), "counts": counts,
            "warning": mail.stale_warning(),
        }


def render(day: date, apps: list, unknown: list, address: str, sheet=None, warning: str = "") -> str:
    now = datetime.now(UTC)
    order = list(STATUS)
    items = []
    for app in apps:
        in_applied = sheet.company_statuses(app.company) if sheet else ""
        status, why = effective_status(app, in_applied, now)
        items.append((app, in_applied, status, why))
    # live ones on top: stages, waiting; rejections below; freshest first within each
    # the freshest events on top; by status — a click on the column header or the filter buttons
    items.sort(key=lambda x: -x[0].last.when.timestamp())
    rows, stages = [], []
    counts: dict[str, int] = {}
    stage_names = dict(FUNNEL) | {"applied": "applied"}
    for app, in_applied, status, why in items:
        stage = funnel_stage(app, in_applied)
        stages.append(stage)
        label, css = STATUS[status]
        counts[status] = counts.get(status, 0) + 1
        # days of silence matter only for live processes: waiting (after TIMEOUT_DAYS — a rejection) and stages (follow up)
        silent = max(0, (now - app.last.when).days) if status in (ACTIVE, WAITING) else None
        def kind_name(e):  # "stage", but a meeting cancellation letter is "cancelled"
            return "cancelled" if e.kind == mail.INTERVIEW and _CANCELLED_RX.search(e.subject) else KIND_NAMES[e.kind]
        history = " → ".join(f"{kind_name(e)} {e.when:%d.%m}" for e in app.ordered if e.kind != mail.OTHER)
        rows.append(
            f'<tr class="{css}" data-{status}="1">'
            f"<td><b>{_e(app.company)}</b></td>"
            f'<td data-sort="{order.index(status)}"><b>{_e(label)}</b>'
            f'{f"<br><span class=small>{_e(why)}</span>" if why else ""}</td>'
            f'<td data-sort="{_FUNNEL_RANK[stage]}">{_e(stage_names[stage])}</td>'
            f'<td data-sort="{app.first:%Y%m%d}">{app.first:%Y-%m-%d}</td>'
            f'<td data-sort="{app.last.when:%Y%m%d}">{app.last.when:%Y-%m-%d}</td>'
            f'<td class="n" data-sort="{-1 if silent is None else silent}">{"" if silent is None else silent}</td>'
            f'<td class="small">{_e(history)}</td>'
            f'<td class="small">{_last_cell(app.last, address)}</td>'
            "</tr>"
        )
    unknown_rows = [
        f'<tr><td>{datetime.fromtimestamp(m["date"], tz=UTC):%Y-%m-%d}</td><td>{_e(KIND_NAMES[m["kind"]])}</td>'
        f'<td class="small">{_e(m["from"])}</td>'
        f'<td><a href="{_e(_thread_link(address, m["thread"]))}" target="_blank" rel="noopener">{_e(m["subject"][:110])}</a></td></tr>'
        for m in sorted(unknown, key=lambda m: -m["date"])
    ]
    buttons = [("all", f"All ({len(rows)})")]
    buttons += [(s, f"{STATUS[s][0]} ({counts[s]})") for s in order if counts.get(s)]
    legend = (
        '<div class="sub legend">'
        '<span class="c-green">green — stages, offer</span>'
        f'<span class="c-yellow">yellow — waiting for an answer, up to {TIMEOUT_DAYS} days</span>'
        '<span class="c-orange">orange — declined by you</span>'
        f'<span class="c-red">red — rejected: a letter, a mark or {TIMEOUT_DAYS}+ days of silence</span>'
        "</div>"
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Applications — {day.isoformat()}</title>{THEME_JS}<style>{_CSS}{_CSS_COLORS}</style></head><body>",
        f"<h1>Applications from correspondence — {day.isoformat()}</h1>",
        f'<div class="warning">⚠️ {_e(warning)}</div>' if warning else "",
        # on top — conversion: how many companies got to each stage (progress and goals — on the Progress page)
        f'<div class="funnel" title="How many companies got at least to a stage. The stage comes from words in the letter subject or note: '
        f'tech / technical / live coding / take-home / with the team — technical; final / CTO / founder — final; the rest — screening">'
        f"📊 {funnel_line(stages)}</div>",
        f'<div class="sub">Mail {_e(address)} and applications_manual.toml (Telegram, LinkedIn) · companies {len(rows)} · '
        f"status — from correspondence and manual events · mail is only read, "
        f"nothing was sent</div>",
        legend,
        '<div class="bar">',
        *(f'<button data-mode="{m}"{" class=on" if m == "all" else ""}>{_e(t)}</button>' for m, t in buttons),
        '<input id="q" type="search" placeholder="Search by company, letter subject…"><span id="count" class="count"></span></div>',
        _table(["Company", "Status", "Got to", "First event", "Last event", "Days of silence", "History",
                "Last event"], rows, "main"),
    ]
    if unknown_rows:
        parts += [
            f"<details><summary><h2>Company not identified — {len(unknown_rows)}</h2></summary>",
            '<p class="small">The letters look like an application / stage / rejection, but the company is visible neither in the sender nor in the subject. '
            "Set the name in settings.toml → [mail.company_aliases], or open the letter with the link.</p>",
            _table(["Date", "Kind", "Sender", "Subject"], unknown_rows), "</details>",
        ]
    parts += [f"<script>{_JS}</script></body></html>"]
    return "\n".join(parts)
