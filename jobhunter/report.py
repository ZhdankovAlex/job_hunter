import re
from collections import Counter
from dataclasses import dataclass

from . import config, cv_match, geo, outreach, settings, tiers
from .keys import company_key
from .reasons import BULK, LOCATION_TEXT, Mark

# company priority from companies.txt (0–3) — stars before the name
FLAGS = {3: "★★★", 2: "★★", 1: "★", 0: ""}
SOURCE_LINKS = (
    "[Greenhouse](https://www.greenhouse.com), [Lever](https://www.lever.co), "
    "[Ashby](https://www.ashbyhq.com), [Workable](https://www.workable.com), "
    "[Arbeitnow](https://www.arbeitnow.com), [Remote OK](https://remoteok.com), "
    "[Himalayas](https://himalayas.app)"
)


def _was_rejected(job):
    """Whether to move the vacancy to the "Rejected before" section (settings.toml: hide_previous_rejections)."""
    if not settings.get().hide_previous_rejections:
        return False
    return any(w in f"{job.applied_status} {' '.join(job.history_marks)}".lower() for w in ("reject", "declin", "отказ"))


def _blocked(job):
    """The company is in an active process (interviews) — not for "New" and the top; rejections and silence do not block."""
    return bool(config.APPLIED_BLOCK_RX.search(f"{job.applied_status} {' '.join(job.history_marks)}"))


def sort_key(job):
    """Order in the appendixes: freshness, then score (no categories)."""
    ts = job.posted.timestamp() if job.posted else 0
    return (tiers.age_bucket(job), -job.score, job.language_mismatch_warning, -ts)


def section_key(job):
    """Order within a section: companies with "Rejected" in applications go last."""
    return (_was_rejected(job), *sort_key(job))


def _cell(text, limit=None):
    text = " ".join(str(text).split()).replace("|", "\\|")
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _marks(job):
    marks = []
    if job.is_new_today:
        marks.append("🆕")
    if job.salary_note:
        marks.append(f"💰 {job.salary_note}")
    if getattr(job, "levels_note", ""):
        marks.append(f"📊 [{job.levels_note}]({job.levels_url})")
    if cv_match.summary(job):
        marks.append(f"🧩 {cv_match.summary(job)}")
    if job.language_mismatch_warning:
        marks.append(Mark.PYTHON_NOT_PRIMARY.detail(job.mismatch_lang))
    if job.location_check and not job.reloc_type:
        marks.append("❓ remote tied to a country — check")
    if job.referral:
        marks.append("🤝 referral possible")
    marks += job.lang_marks
    if job.note.upper().startswith("VERIFY"):
        marks.append(f"🔍 {job.note}")
    if job.app_status:
        marks.append(f"📬 {job.app_status}")
    if job.applied_status:
        marks.append(f"ℹ️ already applied to this company: {job.applied_status}")
    marks += job.history_marks
    marks += _penalty_marks(job)
    if _was_rejected(job):
        marks.append("↓ rejected before")
    return "; ".join(marks)


PER_COMPANY_LIMIT = 5
PRIORITY_TITLE = "✅ Remote not tied to a specific country" + (
    f" / explicitly includes {config.HOME_COUNTRY}" if config.HOME_COUNTRY else "")
TABLE_HEAD = ["| Company | Vacancy | Location | Date | Link | Marks |", "|---|---|---|---|---|---|"]


def _table(jobs, overflow, max_rows=None):
    """A table with a per-company limit (and a row limit); the rest goes to overflow[company]."""
    jobs = sorted(jobs, key=section_key)
    by_company: dict = {}
    for j in jobs:
        by_company.setdefault(j.company, []).append(j)
    shown: set = set()
    extra: dict = {}
    for company, items in by_company.items():
        newest = sorted(items, key=lambda j: -(j.posted.timestamp() if j.posted else 0))
        shown.update(id(j) for j in newest[:PER_COMPANY_LIMIT])
        if newest[PER_COMPANY_LIMIT:]:
            extra[company] = len(newest) - PER_COMPANY_LIMIT
            overflow.setdefault(company, []).extend(newest[PER_COMPANY_LIMIT:])
    visible = [j for j in jobs if id(j) in shown]
    cut = 0
    if max_rows and len(visible) > max_rows:  # keep the freshest
        newest = sorted(visible, key=lambda j: -(j.posted.timestamp() if j.posted else 0))
        keep = {id(j) for j in newest[:max_rows]}
        for j in newest[max_rows:]:
            overflow.setdefault(j.company, []).append(j)
        cut = len(visible) - max_rows
        visible = [j for j in visible if id(j) in keep]
    last_row = {j.company: i for i, j in enumerate(visible)}
    lines = list(TABLE_HEAD)
    for i, j in enumerate(visible):
        company = f"{FLAGS[j.score]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| {company} | {_cell(j.title, 90)} | {_cell(j.location or 'Remote', 60)} | "
            f"{posted} | [{j.source}]({j.url}) | {_cell(_marks(j))} |"
        )
        if last_row[j.company] == i and j.company in extra:
            lines.append(f"| | + {extra[j.company]} more at {_cell(j.company)} → see appendix | | | | |")
    if cut:
        lines.append(f"| | + {cut} more older vacancies → see appendix | | | | |")
    lines.append("")
    return lines


def _relocation_section(reloc, overflow):
    """🌍: subsections by country, confirmed relocation first within each."""
    lines = [f"## 🌍 Relocation and remote by country — {len(reloc)}", ""]
    for country in [*settings.get().relocation_countries, geo.OTHER]:
        group = [j for j in reloc if j.reloc_country == country]
        if not group:
            continue
        group.sort(key=lambda j: (_was_rejected(j), geo.TYPE_ORDER.index(j.reloc_type), sort_key(j)))
        lines += [f"### {country} — {len(group)}", ""]
        lines += [
            "| Company | Vacancy | Country / city | Kind | Relocation marker | Salary | Link | Marks |",
            "|---|---|---|---|---|---|---|---|",
        ]
        per_company: Counter[str] = Counter()
        hidden: Counter[str] = Counter()
        for j in group:
            per_company[j.company] += 1
            if per_company[j.company] > PER_COMPANY_LIMIT:
                hidden[j.company] += 1
                overflow.setdefault(j.company, []).append(j)
                continue
            quote = f'"{_cell(j.reloc_quote)}"' if j.reloc_quote else "—"
            lines.append(
                f"| {_cell(j.company)} | {_cell(j.title, 90)} | {_cell(j.location, 60)} | {j.reloc_type} | "
                f"{quote} | {_cell(j.salary or '—', 30)} | [{j.source}]({j.url}) | {_cell(_marks(j))} |"
            )
        for company, n in hidden.items():
            lines.append(f"| | + {n} more at {_cell(company)} → see appendix | | | | | | |")
        lines.append("")
    return lines


def _status(job):
    if hasattr(job, "channel"):  # Telegram
        return f"❓ check by hand: {job.manual_check}" if job.manual_check else job.label
    if job.manual_check:
        return f"❓ check by hand: {job.manual_check}"
    if job.reloc_type:
        return f"🌍 {job.reloc_country}: {job.reloc_type}"
    if job.location_check:
        return "❓ remote within a country"
    return f"✅ {config.HOME_COUNTRY}" if config.HOME_RX.search(job.location or "") else "✅ remote"


PENALTY_NAMES = {
    "experience_7": "7 years of experience required", "experience_8": "8+ years of experience required", "level": "Staff/Principal level",
    "role": "related role", "language": "primary language is not Python",
    "ops_heavy": "by the text — DevOps/infrastructure, not product work",
}


def _penalty_marks(job):
    return [f"⬇ penalty: {PENALTY_NAMES.get(p, p)}" for p in job.penalties]


SKILL_GAPS_TOP = 15


def skill_gaps(jobs, tg_jobs) -> dict:
    """Which skills are missing most often in fitting vacancies (Backend and ML / AI).

    -> {"total": N, "core": [(skill, how many vacancies)], "nice": [...]}: over your vacancies, not the whole market.
    """
    pool = [j for j in [*jobs, *tg_jobs] if j.tier in (tiers.BACKEND, tiers.ML)]
    core: Counter[str] = Counter()
    nice: Counter[str] = Counter()
    for j in pool:
        core.update(set(j.cv_core_missing))
        nice.update(set(j.cv_nice_missing) - set(j.cv_core_missing))
    return {"total": len(pool), "core": core.most_common(SKILL_GAPS_TOP), "nice": nice.most_common(SKILL_GAPS_TOP)}


def _skill_gaps_section(jobs, tg_jobs):
    gaps = skill_gaps(jobs, tg_jobs)
    if not gaps["total"]:
        return []
    total = gaps["total"]
    lines = [f"## 📊 Skills missing most often — over {total} fitting vacancies (Backend, ML / AI)", "",
             "| Skill | Key (not in the CV) | Nice-to-have |", "|---|---|---|"]
    nice = dict(gaps["nice"])
    for name, n in gaps["core"]:
        extra = nice.pop(name, 0)
        lines.append(f"| {name} | {n} ({round(100 * n / total)}%) | {f'{extra} ({round(100 * extra / total)}%)' if extra else '—'} |")
    for name, n in list(nice.items())[:5]:
        lines.append(f"| {name} | — | {n} ({round(100 * n / total)}%) |")
    lines += ["", "Key — the skill is required without reservations, nice-to-have — \"is a plus\". "
              "Terms outside the dictionary (Odoo, ERP…) count too if a vacancy mentions them 3+ times.", ""]
    return lines


def _applications_block(stats):
    """📬 Applications: a summary by status and who to follow up with (mail + Telegram stories)."""
    digest = stats.get("applications")
    if not digest:
        return []
    c = digest["counts"]
    lines = [
        "## 📬 Applications",
        "",
        f"🟢 in stages {c.get('active', 0) + c.get('offer', 0)} · 🟡 waiting for an answer {c.get('waiting', 0)} · "
        f"🟠 declined by you {c.get('declined', 0)} · 🔴 rejected {c.get('rejected', 0)} — "
        "details in reports/applications.html",
        "",
    ]
    if digest.get("warning"):
        lines += [f"> ⚠️ **{_cell(digest['warning'])}**", ""]
    return lines


def _new_section(jobs, tg_jobs):
    """🆕 Vacancies not seen in past runs (state/seen.json)."""
    new = [j for j in jobs if j.is_new_today and not _blocked(j)]
    new.sort(key=tiers.sort_key)
    new_tg = sorted((j for j in tg_jobs if j.is_new_today and not _blocked(j)), key=tiers.sort_key)
    lines = [f"## 🆕 New in the last day — {len(new) + len(new_tg)}", ""]
    if not new and not new_tg:
        return [*lines, "No new vacancies since the last run.", ""]
    lines += [
        "| Score | Company | Vacancy | Location | Status | Date | Source | Link | Missing / marks |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for j in new:
        company = f"{FLAGS[j.score]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        marks = [m for m in _marks(j).split("; ") if m and m != "🆕" and not m.startswith("❓")]
        lines.append(
            f"| **{j.relevance}** | {company} | {_cell(j.title, 90)} | {_cell(j.location or 'Remote', 60)} | "
            f"{_status(j)} | {posted} | {_source(j)} | [original]({j.url}) | {_cell('; '.join([_missing(j), *marks]))} |"
        )
    for j in new_tg:
        posted = j.posted.date().isoformat() if j.posted else "—"
        link = f"[Apply]({j.url})" if j.url else f"{j.contact or '—'} · [post]({j.post_url})"
        lines.append(
            f"| **{j.relevance}** | {_cell(j.company)} | {_cell(j.title, 90)} | {_cell(j.location or '—', 60)} | "
            f"{j.label} | {posted} | {_source(j)} | {link} | {_cell('; '.join([_missing(j), *j.marks]))} |"
        )
    lines.append("")
    return lines


def _source(job):
    """Vacancy sources: freehire, ATS, aggregator or Telegram channel."""
    if hasattr(job, "channel"):
        return f"telegram @{job.channel}"
    return " + ".join(job.sources or [job.source])


def _missing(job, limit=6):
    """Missing key skills (for the Markdown report tables)."""
    names = list(getattr(job, "cv_core_missing", []))[:limit]
    return ("missing: " + ", ".join(names)) if names else "all key skills present"


def _skills(job, limit=6):
    """Matched skills in one line (the heaviest first)."""
    names = job.matched_skills[:limit]
    more = len(job.matched_skills) - len(names)
    return ", ".join(names) + (f" +{more}" if more > 0 else "")


def _ranked(jobs, tg_jobs):
    """All vacancies (ATS + Telegram) by score, descending; without companies in an active process (interviews)."""
    rows = [(j, j.url, _status(j), _marks(j)) for j in jobs if not _blocked(j)]
    rows += [
        (j, j.url or j.post_url, _status(j),
         "; ".join([*([f"💰 {j.salary_note}"] if j.salary_note else []),
                    *([f"📊 [{j.levels_note}]({j.levels_url})"] if j.levels_note else []),
                    *([f"🧩 {cv_match.summary(j)}"] if cv_match.summary(j) else []), *j.marks, *_penalty_marks(j)]))
        for j in tg_jobs if not _blocked(j)
    ]
    rows.sort(key=lambda r: tiers.sort_key(r[0]))
    return rows


def _top_section(jobs, tg_jobs):
    """🎯 Top-N by relevance to the CV (profile.toml), at most top_per_company per company."""
    limit = settings.get().top_limit
    per_company_limit = settings.get().top_per_company
    picked = []
    per_company: Counter[str] = Counter()
    for row in _ranked(jobs, tg_jobs):
        key = company_key(row[0].company) or row[0].company
        if per_company[key] >= per_company_limit:
            continue
        per_company[key] += 1
        picked.append(row)
        if len(picked) == limit:
            break
    lines = [f"## 🎯 Top {limit} by relevance to the CV — {len(picked)}", ""]
    if not picked:
        return [*lines, "No fitting vacancies.", ""]
    lines += [
        "| # | Score | Company | Vacancy | Location | Status | Date | Source | Link | Missing | Marks |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, (j, link, status, marks) in enumerate(picked, 1):
        company = f"{FLAGS[getattr(j, 'score', 0)]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        marks = "; ".join(m for m in marks.split("; ") if m and not m.startswith("❓"))
        lines.append(
            f"| {n} | **{j.relevance}** | {company} | {_cell(j.title, 80)} | {_cell(j.location or 'Remote', 50)} | "
            f"{status} | {posted} | {_source(j)} | [original]({link}) | {_cell(_missing(j))} | {_cell(marks)} |"
        )
    lines.append("")
    return lines


TOP_REVIEW_LIMIT = 15
TOP_BLOCKING_PENALTIES = {"level", "language", "experience_7", "experience_8"}


def _why(job):
    """One line "why it fits": the main matched skills and the location condition."""
    location = job.location or ""
    if config.HOME_RX.search(location):
        place = f"work in your country of residence ({config.HOME_COUNTRY})"
    elif re.search(r"world|global|anywhere", location, re.I):
        place = "remote worldwide"
    elif re.search(r"europe|emea|\beu\b", location, re.I):
        place = "remote in Europe"
    else:
        place = "remote not tied to a country"
    if getattr(job, "reloc_type", "") == geo.RELOCATION:
        place = f"relocation: {job.reloc_country}"
    extras = ["priority company"] if getattr(job, "score", 0) >= 2 else []
    return ", ".join([*job.matched_skills[:3], place, *extras])


def pick_review(jobs, tg_jobs):
    """Vacancies for "Top to review": [(job, link)], at most TOP_REVIEW_LIMIT."""
    def eligible(job):
        return not (job.manual_check or _blocked(job) or TOP_BLOCKING_PENALTIES & set(job.penalties))

    # ✅ remote / home country, or 🌍 with confirmed relocation
    rows = [(j, j.url) for j in jobs if eligible(j)
            and (j.reloc_type == geo.RELOCATION or (not j.location_check and not j.reloc_type))]
    rows += [(j, j.url or j.post_url) for j in tg_jobs if eligible(j) and j.label.startswith("✅")]
    rows.sort(key=lambda r: tiers.sort_key(r[0]))
    picked = []
    per_company: Counter[str] = Counter()
    for job, link in rows:
        key = company_key(job.company) or job.company
        if per_company[key] >= 2:
            continue
        per_company[key] += 1
        picked.append((job, link))
        if len(picked) == TOP_REVIEW_LIMIT:
            break
    return picked


def _review_section(jobs, tg_jobs):
    """⭐ Up to 15 vacancies: location confirmed by the text, no penalties, the text was read."""
    picked = pick_review(jobs, tg_jobs)
    lines = [f"## ⭐ Top to review — {len(picked)}", ""]
    if not picked:
        return [*lines, "No vacancies have passed all checks yet.", ""]
    lines += [
        "| # | Category | Score | Company | Vacancy | Location | Status | Date | Source | Link | Why it fits | Match with the CV |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, (j, link) in enumerate(picked, 1):
        company = f"{FLAGS[getattr(j, 'score', 0)]} {_cell(j.company)}".strip()
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| {n} | {tiers.NAMES[j.tier]} | **{j.relevance}** | {company} | {_cell(j.title, 80)} | "
            f"{_cell(j.location or 'Remote', 50)} | "
            f"{_status(j)} | {posted} | {_source(j)} | [original]({link}) | {_cell(_why(j))} | "
            f"{_cell(cv_match.summary(j))} |"
        )
    lines.append("")
    return lines + _outreach_section(picked)


def _outreach_section(picked):
    """✉️ Who to write to about the top vacancies: LinkedIn people search and a message draft.

    Links are only built, the script does not visit LinkedIn; the user sends messages themselves.
    """
    lines = ["### ✉️ Write to a real person", "",
             "The links open a LinkedIn people search; the draft is a note to a connection request (up to 300 characters).", ""]
    for n, (j, _) in enumerate(picked, 1):
        links = [f"[{label}]({url})" for label, url in outreach.search_links(j.company)]
        if outreach.telegram_contact(j):
            links.insert(0, f"[post author in Telegram]({outreach.telegram_contact(j)})")
        text = getattr(j, "description", "") or getattr(j, "text", "")
        direct = [f"[{label}]({href})" for _, label, href in outreach.contacts(text, getattr(j, "channel", ""))]
        lines += [f"{n}. **{_cell(j.company) or '—'}** — {_cell(j.title, 80)}: {' · '.join(links) or 'company not named'}",
                  *([f"   📇 contacts from the vacancy: {' · '.join(direct)}"] if direct else []),
                  f"   > ✉️ to a recruiter: {outreach.draft(j)}",
                  "   >",
                  f"   > 🤝 to an engineer, referral: {outreach.referral_draft(j)}", ""]
    return lines


def _manual_section(jobs):
    """❓ Vacancies that cannot be confirmed by the text — they do not get into the top."""
    lines = [f"## ❓ Check by hand — {len(jobs)}", ""]
    lines += ["| Score | Company | Vacancy | Location | Why ❓ | Date | Link | Marks |", "|---|---|---|---|---|---|---|---|"]
    for j in sorted(jobs, key=tiers.sort_key):
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| {j.relevance} | {_cell(j.company)} | {_cell(j.title, 80)} | {_cell(j.location or 'Remote', 50)} | "
            f"{j.manual_check} | {posted} | [{_source(j)}]({j.url}) | {_cell(_marks(j))} |"
        )
    lines.append("")
    return lines


REFUSED_PER_COMPANY = 4


def _previously_rejected_section(jobs, tg_jobs, filtered):
    """Companies with a "Rejected" status in applications — separately, outside the main sections and the top.

    filtered — their vacancies that also did not pass the filters (the reason is in the last column).
    """
    rows = [(j, j.url, "") for j in jobs] + [(j, j.url or j.post_url, "") for j in tg_jobs]
    rows += [(j, j.url or getattr(j, "post_url", ""), j.reject) for j in filtered]
    rows.sort(key=lambda r: (r[0].company.lower(), bool(r[2]), -r[0].relevance))
    lines = [f"## Rejected before — {len({r[0].company for r in rows})} companies, {len(rows)} vacancies", ""]
    lines += [
        "| Company | Vacancy | Location | Date | Link | Applications | Filters |",
        "|---|---|---|---|---|---|---|",
    ]
    per_company: Counter[str] = Counter()
    for j, link, reason in rows:
        per_company[j.company] += 1
        if per_company[j.company] > REFUSED_PER_COMPANY:
            continue
        posted = j.posted.date().isoformat() if j.posted else "—"
        history = "; ".join(filter(None, [j.applied_status, *j.history_marks]))
        lines.append(
            f"| {_cell(j.company)} | {_cell(j.title, 80)} | {_cell(j.location or 'Remote', 50)} | {posted} | "
            f"[{_source(j)}]({link}) | {_cell(history)} | {('did not pass: ' + reason) if reason else 'passed'} |"
        )
    hidden = sum(max(0, n - REFUSED_PER_COMPANY) for n in per_company.values())
    if hidden:
        lines.append(f"| | + {hidden} more vacancies of these companies (at most {REFUSED_PER_COMPANY} per company) | | | | | |")
    lines.append("")
    return lines


REJECTED_LIMIT = 50
REJECTED_PER_BULK_REASON = 3  # "by the source label" and "older than N days" are bulk reasons


def pick_rejected(rejected):
    """Up to REJECTED_LIMIT rejected vacancies: rules by the text first, then bulk reasons."""
    def is_bulk(job):
        return getattr(job, "reject_code", "") in BULK

    # rejected are already sorted by the raw score; rules by the text are shown first
    bulk_rows = []
    bulk: Counter[str] = Counter()
    for j in filter(is_bulk, rejected):
        if bulk[j.reject] < REJECTED_PER_BULK_REASON:
            bulk[j.reject] += 1
            bulk_rows.append(j)
    # location rejects from the text are the most important rule, they go first
    by_text = sorted(
        (j for j in rejected if not is_bulk(j)),
        key=lambda j: (getattr(j, "reject_code", "") not in LOCATION_TEXT, -j.relevance),
    )
    return by_text[: REJECTED_LIMIT - len(bulk_rows)] + bulk_rows


def _rejected_section(rejected):
    """Vacancies of a fitting role hidden by the filters: the reason and a quote from the text."""
    shown = pick_rejected(rejected)
    by_reason = Counter(re.sub(r"\d+", "N", j.reject) for j in rejected)
    lines = [f"## Rejected by filters — {len(rejected)} (shown {len(shown)})", ""]
    lines += ["By reason: " + "; ".join(f"{k} — {v}" for k, v in by_reason.most_common()), ""]
    lines += ["| Raw score | Company | Vacancy | Reason | Quote | Link |", "|---|---|---|---|---|---|"]
    for j in shown:
        link = j.url or getattr(j, "post_url", "")
        quote = f'"{_cell(j.reject_quote, 110)}"' if j.reject_quote else "—"
        lines.append(
            f"| {j.relevance} | {_cell(j.company)} | {_cell(j.title, 80)} | {j.reject} | {quote} | "
            f"[{_source(j)}]({link}) |"
        )
    lines.append("")
    return lines


def _tg_sort_key(job):
    ts = job.posted.timestamp() if job.posted else 0
    return (_was_rejected(job), not job.label.startswith("✅"), -ts)


def _telegram_section(tg_jobs, stats):
    lines = [f"## Telegram channels — {len(tg_jobs)}", ""]
    for channel in stats.get("tg_failed", []):
        lines += [f"> ⚠️ Could not read the channel @{channel} (see errors.log).", ""]
    if not tg_jobs:
        return [*lines, "No fitting vacancies.", ""]
    lines += [
        "| Channel | Date | Title | Company | Location / format | Salary | Link or contact | Marks |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for j in sorted(tg_jobs, key=_tg_sort_key):
        parts = [f"[Apply]({j.url})"] if j.url else []
        if j.contact or not j.url:
            parts.append(j.contact or "contact not found")
        link = " · ".join([*parts, f"[post]({j.post_url})"])
        marks = [j.label, *j.marks]
        if j.applied_status:
            marks.append(f"ℹ️ already applied to this company: {j.applied_status}")
        marks += j.history_marks
        if _was_rejected(j):
            marks.append("↓ rejected before")
        posted = j.posted.date().isoformat() if j.posted else "—"
        lines.append(
            f"| @{j.channel} | {posted} | {_cell(j.title, 90)} | {_cell(j.company, 40)} | "
            f"{_cell(j.location or '—', 60)} | {_cell(j.salary or '—', 30)} | {link} | {_cell('; '.join(marks))} |"
        )
    lines.append("")
    return lines


def _compact_appendix(title, groups, link_of, suffix_of=lambda j: ""):
    """A compact list by company: title — location — link."""
    lines = [f"## {title}", ""]
    for company in sorted(groups, key=str.lower):
        lines += [f"**{_cell(company)}**" + suffix_of(groups[company][0]), ""]
        for j in groups[company]:
            lines.append(f"- {_cell(j.title, 90)} — {_cell(j.location or 'Remote', 60)} — [link]({link_of(j)})")
        lines.append("")
    return lines


@dataclass
class _Split:
    """Report vacancies split into sections (render takes from here what goes where)."""

    jobs: list  # the main report: without "local language required", outsourcing and "Rejected before"
    tg: list  # Telegram, filtered the same way
    lang_blocked: list  # 🚫 local language required — a separate appendix
    staffing: list  # outsourcing — a separate appendix
    refused: list  # companies with a "Rejected" status — a section at the end, not in the main sections or the top
    refused_tg: list
    refused_filtered: list  # dropped by filters at companies with a "Rejected" status
    filtered_out: list  # dropped by filters (the rest)
    manual: list  # ❓ check by hand
    # everything that passed the location filter without a ❓ mark: home country,
    # Worldwide/Global/EMEA/Europe, or just "Remote" without a region
    priority: list
    reloc: list  # 🌍 relocation and remote within a country that allows working from outside it
    rest: list  # with a ❓ location mark — by company priority


def _partition(items: list, pred) -> tuple[list, list]:
    """(matching, the rest), order kept."""
    yes: list = []
    no: list = []
    for j in items:
        (yes if pred(j) else no).append(j)
    return yes, no


def _split(jobs: list, tg_jobs: list | None, rejected: list) -> _Split:
    lang_jobs, rest_jobs = _partition(sorted(jobs, key=sort_key), lambda j: j.lang_required)
    lang_tg, rest_tg = _partition(tg_jobs or [], lambda j: j.lang_required)
    staff_jobs, rest_jobs = _partition(rest_jobs, lambda j: j.staffing)
    staff_tg, rest_tg = _partition(rest_tg, lambda j: j.staffing)
    refused, main = _partition(rest_jobs, _was_rejected)
    refused_tg, tg = _partition(rest_tg, _was_rejected)
    refused_filtered, filtered_out = _partition(rejected, _was_rejected)
    return _Split(
        jobs=main, tg=tg, lang_blocked=lang_jobs + lang_tg, staffing=staff_jobs + staff_tg,
        refused=refused, refused_tg=refused_tg, refused_filtered=refused_filtered, filtered_out=filtered_out,
        manual=[j for j in main if j.manual_check],
        priority=[j for j in main if not j.location_check and not j.reloc_type and not j.manual_check],
        reloc=[j for j in main if j.reloc_type],
        rest=[j for j in main if j.location_check and not j.reloc_type],
    )


def _header(day, jobs: list) -> list[str]:
    lines = [f"# Vacancies for {day.isoformat()}", ""]
    if not jobs:
        lines += ["No new fitting vacancies.", ""]
    return lines


def _by_company(jobs: list) -> dict:
    groups: dict = {}
    for j in jobs:
        groups.setdefault(j.company, []).append(j)
    return groups


def _appendix(s: _Split, stats: dict, with_tg: bool) -> list[str]:
    """Appendix: all current vacancies by group, then outsourcing, local language, "Rejected before", dropped."""
    overflow: dict = {}  # what did not fit into the tables — a compact list by company
    lines = ["---", "", "# Appendix: all current vacancies", ""]
    if s.priority:
        shown = min(len(s.priority), config.PRIORITY_MAX_ROWS)
        lines += [f"## {PRIORITY_TITLE} — {len(s.priority)} (up to {shown} freshest shown)", ""]
        lines += _table(s.priority, overflow, config.PRIORITY_MAX_ROWS)
    if s.manual:
        lines += _manual_section(s.manual)
    if s.reloc:
        lines += _relocation_section(s.reloc, overflow)
    for score in (3, 2, 1, 0):
        group = [j for j in s.rest if j.score == score]
        if group:
            lines += [f"## {f'{FLAGS[score]} Company priority {score}'.strip()} — {len(group)}", ""]
            lines += _table(group, overflow)
    if with_tg:
        lines += _telegram_section(s.tg, stats)
    if overflow:
        lines += _compact_appendix("Appendix: remaining vacancies by company", overflow, lambda j: j.url)
    if s.staffing:
        lines += _compact_appendix(
            f"Appendix: Staffing/outsourcing — {len(s.staffing)}", _by_company(s.staffing),
            lambda j: j.url or j.post_url, lambda j: f" ({j.staffing})",
        )
    if s.lang_blocked:
        lines += _compact_appendix(
            f"Appendix: Local language required — {len(s.lang_blocked)}", _by_company(s.lang_blocked),
            lambda j: j.url or j.post_url,
        )
    if s.refused or s.refused_tg or s.refused_filtered:
        lines += _previously_rejected_section(s.refused, s.refused_tg, s.refused_filtered)
    if s.filtered_out:
        lines += _rejected_section(s.filtered_out)
    return lines


def _companies_lines(stats: dict, shown: list) -> list[str]:
    """Companies tracked separately (must_report, referral): how many found and passed the filters."""
    lines = []
    for name, found, passed, reason in stats.get("must_report", []):
        if reason:
            lines.append(f"- {name}: ATS board not found, see errors.log (reason: {reason})")
        else:
            lines.append(f"- {name}: vacancies found on the board — {found}, passed the filters — {passed}"
                         + ("" if passed else " (no Python vacancies right now)"))
    reported = {company_key(row[0]) for row in stats.get("must_report", [])}
    for name, has_board in stats.get("referral", []):
        if company_key(name) in reported:
            continue
        if not any(company_key(j.company) == company_key(name) for j in shown):
            lines.append(f"- {name}: no Python vacancies right now"
                         + ("" if has_board else " (ATS board not found — check the career site by hand)"))
    return lines


def _freehire_lines(stats: dict) -> list[str]:
    """Summary lines about freehire — if it was in the run."""
    lines = []
    if (fh := stats.get("freehire")) and fh["enabled"]:
        lines.append(
            f"- freehire: received {fh['fetched']} (network requests {fh['requests']}, from cache {fh['cache_hits']}; "
            "by query: " + ", ".join(f"{k} — {v}" for k, v in fh["totals"].items()) + "), "
            f"new added {fh['added']}, matched other sources {fh['twins']}, "
            f"full texts received {fh['described']} of {fh.get('candidates', 0)}, "
            f"failed the check {fh['dropped']}")
        lines += [f"- freehire, query {name} returned 0: {text}" for name, text in fh.get("diagnosis", {}).items()]
    return lines


def _salary_lines(stats: dict) -> list[str]:
    if (sal := stats.get("salary")) and sal["samples"]:
        return [f"- Salary range base: {sal['samples']} vacancies with a stated salary (EUR/month, medians). "
                + " | ".join(sal["summary"])]
    return []


def _summary(s: _Split, stats: dict, with_tg: bool) -> list[str]:
    by_score = Counter(j.score for j in s.jobs)
    by_country = Counter(j.reloc_country for j in s.reloc)
    confirmed = Counter(j.reloc_country for j in s.reloc if j.reloc_type == geo.RELOCATION)
    refused_total = len(s.refused) + len(s.refused_tg) + len(s.refused_filtered)
    return [
        "## Summary",
        "",
        f"- Found in total (passed the filter): **{stats['matched']}**",
        f"- Already applied (hidden): {stats['in_applied']}",
        f"- Duplicates merged (one vacancy in several locations): {stats.get('merged', 0)}",
        *_freehire_lines(stats),
        f"- Rejected before (separate section): {refused_total}",
        f"- ❓ Check by hand: {len(s.manual)}",
        f"- Rejected by filters (fitting role): {len(s.filtered_out)} — full list in state/rejected_latest.tsv",
        *_salary_lines(stats),
        f"- Dropped as older than {stats.get('max_age_days', 30)} days: {stats.get('stale', 0)}",
        f"- Moved to Staffing/outsourcing: {len(s.staffing)}",
        f"- In the main report: **{len(s.jobs)}**, of them first seen today: "
        f"{sum(j.is_new_today for j in s.jobs)}",
        f"- {PRIORITY_TITLE} (confirmed by the text): **{len(s.priority)}** vacancies",
        f"- In the 🌍 section: **{len(s.reloc)}** vacancies"
        + (" — " + ", ".join(
            f"{c}: {n} (confirmed relocation: {confirmed.get(c, 0)})" for c, n in by_country.items()
        ) if s.reloc else ""),
        f"- Remote within a country that allows working from outside it: "
        f"{sum(j.reloc_type == geo.REMOTE_OK for j in s.reloc)}",
        f"- Dropped for language (🚫 local language required): {len(s.lang_blocked)}",
        *_companies_lines(stats, [*s.jobs, *s.tg]),
        "- By company priority: " + ", ".join(f"{n} → {by_score.get(n, 0)}" for n in (3, 2, 1, 0)),
        f"- Marked ⚠️ language mismatch: {sum(j.language_mismatch_warning for j in s.jobs)}",
        *(
            [f"- Telegram channels: {len(s.tg)} vacancies, of them ✅ — "
             f"{sum(j.label.startswith('✅') for j in s.tg)}"]
            if with_tg else []
        ),
        f"- Companies with a found ATS board: {stats['mapped']} of {stats['companies']} "
        f"(the rest are in errors.log)",
        f"- Vacancies looked at before the filter: {stats['fetched']}",
        "",
        f"Sources: {SOURCE_LINKS}.",
        "",
    ]


def render(day, jobs, stats, tg_jobs=None):
    """Markdown report: header, applications, "New", top etc., an appendix with all vacancies, a summary."""
    s = _split(jobs, tg_jobs, stats.get("rejected", []))
    lines = _header(day, s.jobs)
    lines += _applications_block(stats)
    lines += _new_section(s.jobs, s.tg)
    lines += _review_section(s.jobs, s.tg)
    lines += _skill_gaps_section(s.jobs, s.tg)
    lines += _top_section(s.jobs, s.tg)
    lines += _appendix(s, stats, tg_jobs is not None)
    lines += _summary(s, stats, tg_jobs is not None)
    return "\n".join(lines)
