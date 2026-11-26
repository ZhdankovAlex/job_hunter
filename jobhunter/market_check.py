""""CV vs market": a local ATS checker over the vacancies of a report.

Compares the CV not with an abstract skill list but with the fitting vacancies of a specific day
(state/jobs/<date>.json): which technologies the market requires and whether they are in the PDF CV and in the
cv/base.toml base, what the vacancies are called and whether the CV headline matches, and simple hygiene
of experience bullets (numbers, a verb at the start, length). Sends nothing anywhere.
"""
from __future__ import annotations

import json
import re
import statistics
import tomllib
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from . import config, cv_match, settings

# which vacancies count as "your market": the role by its title
RELEVANT_TITLE_RX = re.compile(r"back[\s-]?end|python|software engineer|software developer|platform engineer|"
                               r"\bai engineer|\bml engineer|llm|full[\s-]?stack", re.I)
# what roles are called: phrase -> regex over the vacancy title
TITLE_PHRASES = {
    "Backend Engineer": r"back[\s-]?end (?:software )?engineer",
    "Backend Developer": r"back[\s-]?end developer",
    "Python Developer": r"python (?:back[\s-]?end )?developer",
    "Python Engineer": r"python (?:back[\s-]?end )?engineer",
    "Software Engineer": r"software engineer",
    "Software Developer": r"software developer",
    "Platform Engineer": r"platform engineer",
    "AI Engineer": r"\b(?:ai|ml|llm) engineer",
    "Full Stack": r"full[\s-]?stack",
}
SENIORITY = {"Senior": r"\bsenior\b|\bsr\.?\b", "Lead": r"\blead\b", "Staff": r"\bstaff\b",
             "Middle": r"\bmiddle\b|\bmid\b", "Principal": r"\bprincipal\b"}
ACTION_VERBS = set("""architected built designed developed reduced cut migrated introduced automated separated
implemented led rebuilt integrated delivered created improved optimized launched owned raised replaced moved
scaled wrote set established drove""".split())
_NUMBER_RX = re.compile(r"\d")
# words that make a CV "sound like AI" or like a list of duties (a checklist from the @zerotooffer channel)
_STOP_RX = re.compile(r"\b(?:helped|(?<!the )supported|assisted|leveraged|leveraging|utilized|utilizing|various|basic|"
                      r"responsible for|worked on|participated in|spearheaded|cutting-edge|seamless(?:ly)?|"
                      r"robust|synergy|dynamic environment|passionate)\b", re.I)
SAME_FORM_SHARE = 0.8  # share of "verb at the start" bullets — above it all bullets have one form
NUMBERS_PER_JOB_SOFT = 4  # more bullets with numbers per workplace may look inflated
BULLET_WORDS_MAX = 45  # longer — the bullet is hard to read, a candidate for shortening
TOP_KEYWORDS = 30


def _cv_texts() -> tuple[str, str, list[Path]]:
    """(PDF CV text, cv/base.toml base text, paths to the PDFs) — the same that cv_match reads."""
    cv = settings.get().cv
    profile = cv_match.CvProfile(cv.model_dump())
    pdf_text = "\n".join(t for _, t in profile.cvs)
    base_file = config.CV_BASE_FILE
    base_text = base_file.read_text(encoding="utf-8") if base_file.exists() else ""
    return pdf_text, base_text, [Path(p) for p in cv.files]


def _bullets(base_text: str) -> list[str]:
    """Experience bullets from the base, without **bold** markup."""
    try:
        base = tomllib.loads(base_text)
    except tomllib.TOMLDecodeError:
        return []
    return [re.sub(r"\*\*", "", b["text"]) for job in base.get("experience", []) for b in job.get("bullets", [])
            if not b.get("generic")]


def hygiene(base_text: str, pdfs: list[Path], pdf_text: str) -> dict:
    """CV checks: PDF pages, bullets with numbers / a verb / short ones, contacts, headline."""
    from pypdf import PdfReader

    bullets = _bullets(base_text)
    first_words = [re.sub(r"[^a-z]", "", b.split()[0].lower()) if b.split() else "" for b in bullets]
    words = [len(b.split()) for b in bullets]
    pages = {}
    for path in pdfs:
        if path.exists():
            pages[path.name] = len(PdfReader(str(path)).pages)
    try:
        base = tomllib.loads(base_text)
    except tomllib.TOMLDecodeError:
        base = {}
    texts = bullets + [a["text"] for a in base.get("summary", {}).get("achievements", [])]
    stop = sorted({m.group(0).lower() for t in texts for m in _STOP_RX.finditer(t)})
    # numbers per workplace: "results in numbers" is good, but numbers everywhere look inflated
    numbers_by_job = {job["company"]: sum(bool(_NUMBER_RX.search(b["text"])) for b in job.get("bullets", [])
                                          if not b.get("generic"))
                      for job in base.get("experience", [])}
    return {
        "stop_words": stop,
        "numbers_by_job": numbers_by_job,
        "pages": pages,
        "bullets": len(bullets),
        "with_numbers": sum(bool(_NUMBER_RX.search(b)) for b in bullets),
        "with_verb": sum(w in ACTION_VERBS for w in first_words),
        "long": [b[:90] + "…" for b, n in zip(bullets, words) if n > BULLET_WORDS_MAX],
        "avg_words": round(statistics.mean(words)) if words else 0,
        "no_numbers": [b[:90] + "…" for b in bullets if not _NUMBER_RX.search(b)],
        "contacts": {
            "email": bool(re.search(r"[\w.+-]+@[\w-]+\.\w+", pdf_text)),
            "LinkedIn": "linkedin.com/in/" in pdf_text.lower(),
            "phone": bool(re.search(r"\+\d[\d\s()-]{7,}", pdf_text)),
            "time zone / location": bool(re.search(r"\b(?:CET|CEST|GMT|UTC|[A-Z]{2,4}T)\b|time zone", pdf_text)
                                           or config.HOME_RX.search(pdf_text)),
        },
    }


def analyze(day: str = "") -> dict:
    """The "CV vs market" report over the vacancies of the day (by default — of the last report).

    Returns:
        {day, total, relevant, fit: {median, share80}, keywords: [...], titles: [...], seniority: [...],
         headline, hygiene}. keywords: technology, share of vacancies where it is key, where it is in the CV
         ("pdf" — in the PDF CV, "base" — only in the base, "" — nowhere).
    """
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"))
    path = next((f for f in files if f.stem == day), files[-1] if files else None)
    if path is None:
        return {"day": "", "total": 0, "relevant": 0}
    jobs = json.loads(path.read_text(encoding="utf-8"))
    relevant = [j for j in jobs if RELEVANT_TITLE_RX.search(j["title"]) and j.get("text")]
    pdf_text, base_text, pdfs = _cv_texts()
    profile = cv_match.CvProfile(settings.get().cv.model_dump())

    core: Counter[str] = Counter()
    fits = []
    for job in relevant:
        holder = type("Job", (), {"title": job["title"], "company": job["company"], "mismatch_lang": ""})()
        profile.analyze(holder, job["text"])
        fits.append(holder.cv_fit)
        core.update(n for n in [*holder.cv_core_have, *holder.cv_core_missing] if n in cv_match.TECH_RX)

    def where(name: str) -> str:
        rx = cv_match.TECH_RX[name]
        alias = cv_match.CV_ALIASES.get(name)
        if rx.search(pdf_text) or (alias and re.search(alias, pdf_text, re.I)):
            return "pdf"
        if rx.search(base_text) or (alias and re.search(alias, base_text, re.I)):
            return "base"
        return ""

    n = len(relevant) or 1
    keywords = [{"name": name, "count": c, "share": round(100 * c / n), "in_cv": where(name),
                 "partial": settings.get().cv.partial.get(name, "")}  # AWS: S3 only — present, but with a caveat
                for name, c in core.most_common(TOP_KEYWORDS)]
    titles: Counter[str] = Counter()
    levels: Counter[str] = Counter()
    for job in relevant:
        for phrase, rx in TITLE_PHRASES.items():
            if re.search(rx, job["title"], re.I):
                titles[phrase] += 1
        for level, rx in SENIORITY.items():
            if re.search(rx, job["title"], re.I):
                levels[level] += 1
    try:
        headlines = tomllib.loads(base_text).get("headline", {})
    except tomllib.TOMLDecodeError:
        headlines = {}
    headline = " / ".join(headlines.values())
    measured = [f for f in fits if f >= 0]
    return {
        "day": path.stem, "total": len(jobs), "relevant": len(relevant),
        "fit": {"median": round(statistics.median(measured)) if measured else None,
                "share80": round(100 * sum(f >= 80 for f in measured) / len(measured)) if measured else 0},
        "keywords": keywords,
        "titles": [{"name": k, "count": v, "share": round(100 * v / n),
                    "in_headline": bool(re.search(TITLE_PHRASES[k], headline, re.I))} for k, v in titles.most_common()],
        "seniority": [{"name": k, "count": v, "share": round(100 * v / n)} for k, v in levels.most_common()],
        "headline": headline,
        "hygiene": hygiene(base_text, pdfs, pdf_text),
        "days": [f.stem for f in reversed(files)],
    }


def advice(report: dict) -> list[str]:
    """Conclusions for the page: what to fix in the CV, most useful first (the order is ADVICE_RULES)."""
    return [text for rule in ADVICE_RULES if (text := rule(report))]


# role names: the CV generator substitutes honest backend synonyms itself (cvgen.HEADLINE_ROLES),
# the rest (AI Engineer, Full Stack, Platform) is not a rename, but a different profile
ROLE_SYNONYMS = {"Software Engineer", "Software Developer", "Python Developer", "Python Engineer",
                 "Backend Developer", "Backend Engineer"}


def _missing_titles(report: dict, same_role: bool) -> list[dict]:
    """Frequent (≥10%) vacancy titles missing from the CV headline: synonyms of your role or other roles."""
    return [t for t in report.get("titles", [])
            if not t["in_headline"] and t["share"] >= 10 and (t["name"] in ROLE_SYNONYMS) == same_role]


def _titles_list(titles: list[dict]) -> str:
    return ", ".join(f'"{t["name"]}" ({t["share"]}%)' for t in titles)


def _keywords(report: dict, min_share: int, keep: Callable[[dict], object]) -> list[dict]:
    return [k for k in report.get("keywords", []) if keep(k) and k["share"] >= min_share]


def _keywords_list(keywords: list[dict]) -> str:
    return ", ".join(f"{k['name']} ({k['share']}%)" for k in keywords)


def _advice_same_role(report: dict) -> str | None:
    same_role = _missing_titles(report, same_role=True)
    if not same_role:
        return None
    return (f"Your role is also called this, but your headline does not say so: {_titles_list(same_role)}. Recruiters "
            f"and ATS search by role title. Fix your LinkedIn headline (people find you by it) and the base PDFs, for "
            f"example: \"Senior Backend / {same_role[0]['name']} | Python · PostgreSQL · …\". The \"📄 CV\" button "
            "already does this: the headline follows the vacancy title.")


def _advice_other_role(report: dict) -> str | None:
    other_role = _missing_titles(report, same_role=False)
    if not other_role:
        return None
    return (f"Frequent, but different roles: {_titles_list(other_role)}. No need to rename yourself. If you have "
            "related experience (for example, RAG / LLM for AI roles), you can apply to backend positions in such "
            "teams: the CV generator puts an LLM / RAG headline for AI vacancies.")


def _advice_in_base(report: dict) -> str | None:
    in_base = _keywords(report, 5, lambda k: k["in_cv"] == "base")
    if not in_base:
        return None
    return (f"In the cv/base.toml base, but not in the PDF CV: {_keywords_list(in_base)} — rebuild the base CVs "
            "(cvgen.py --base …).")


def _advice_gaps(report: dict) -> str | None:
    gaps = _keywords(report, 10, lambda k: not k["in_cv"])
    if not gaps:
        return None
    return (f"Gaps — often required, but missing from the CV: {_keywords_list(gaps)}. This is about experience, not "
            "wording: if you have it — add it to the base, if not — these are candidates to learn.")


def _advice_partial(report: dict) -> str | None:
    partial = _keywords(report, 10, lambda k: k["partial"])
    if not partial:
        return None
    return "Partly present: " + ", ".join(f"{k['name']} — {k['partial']} ({k['share']}%)" for k in partial) + "."


def _advice_pages(report: dict) -> str | None:
    long_cvs = [name for name, pages in report.get("hygiene", {}).get("pages", {}).items() if pages > 1]
    if not long_cvs:
        return None
    return (f"The PDF CV is longer than one page ({', '.join(long_cvs)}). For the Western market with 6 years "
            "of experience one page is better — the \"1 page\" button in the report and on the CV page builds it.")


def _advice_stop_words(report: dict) -> str | None:
    words = report.get("hygiene", {}).get("stop_words")
    if not words:
        return None
    return ("Words that make the CV sound like a list of duties or like AI: "
            + ", ".join(f"\"{w}\"" for w in words) + " — replace them with a concrete action and result.")


def _advice_same_form(report: dict) -> str | None:
    h = report.get("hygiene", {})
    if not (h.get("bullets") and h["with_verb"] >= SAME_FORM_SHARE * h["bullets"]):
        return None
    return (f"All bullets have one form: {h['with_verb']} of {h['bullets']} start with a past-tense verb. "
            "Sameness gives away a template (and AI) — start some bullets with the result or the problem, "
            "add a couple of short ones.")


def _advice_numbers(report: dict) -> str | None:
    """Numbers in bullets: too many for one workplace — or, if there is no such workplace, too few overall."""
    h = report.get("hygiene", {})
    heavy = [f"{job} ({n})" for job, n in h.get("numbers_by_job", {}).items() if n > NUMBERS_PER_JOB_SOFT]
    if heavy:
        return (f"Numbers in almost every bullet: {', '.join(heavy)} bullets with numbers. A measurable result is "
                "strong, but numbers everywhere can look inflated: keep the weightiest, and in the rest show the scale "
                "or who uses it. (Opinions differ: some advice says \"numbers everywhere\", some \"no more "
                "than two or three per workplace\".)")
    if h.get("bullets") and h["with_numbers"] < h["bullets"] / 2:
        return (f"Few bullets with a measurable result: {h['with_numbers']} of {h['bullets']} — where there is a "
                "result (time, volume, share), show it.")
    return None


def _advice_long(report: dict) -> str | None:
    long = report.get("hygiene", {}).get("long")
    if not long:
        return None
    return (f"Long bullets (more than {BULLET_WORDS_MAX} words): {len(long)} — readers spend 5 seconds on them, "
            "one idea per bullet is better.")


# advice rules, most useful first: role title, skills, bullet hygiene; a rule returns the text or None
ADVICE_RULES: tuple[Callable[[dict], str | None], ...] = (
    _advice_same_role, _advice_other_role,
    _advice_in_base, _advice_gaps, _advice_partial,
    _advice_pages, _advice_stop_words, _advice_same_form, _advice_numbers, _advice_long,
)
