import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Company:
    name: str
    score: int = 0  # company priority (0–3) from companies.txt
    note: str = ""


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str
    posted: datetime | None = None
    description: str = ""
    remote: bool | None = None
    score: int = 0  # company priority (0–3) from companies.txt
    note: str = ""
    # filled in by the filters
    location_check: bool = False
    language_mismatch_warning: bool = False
    mismatch_lang: str = ""
    applied_status: str = ""  # statuses of applications to this company (applications_manual.toml)
    salary: str = ""
    employment: str = ""  # employment type as the source gave it (Full-time, Contract, …)
    relevance: int = 0  # score by profile.toml
    matched_skills: list = field(default_factory=list)
    # relocation / remote tied to a country (geo.py)
    reloc_country: str = ""
    reloc_type: str = ""
    reloc_quote: str = ""
    # language filter (language.py)
    lang_marks: list = field(default_factory=list)
    lang_required: bool = False
    referral: bool = False
    staffing: str = ""  # why the vacancy was moved to the Staffing appendix
    history_marks: list = field(default_factory=list)
    app_status: str = ""  # status of the application to this company by mail / Telegram (applications_report)
    is_new_today: bool = False
    flags: list = field(default_factory=list)
    # check by the full text (verify.py)
    reject: str = ""  # why the vacancy is hidden
    reject_quote: str = ""  # quote from the text the rule fired on
    manual_check: str = ""  # status ❓: why a manual check is needed
    penalties: list = field(default_factory=list)  # level / experience_7 / role / language
    salary_note: str = ""  # salary range: stated or estimated from the base (salary.py)
    levels_note: str = ""  # median for the vacancy country from levels.fyi (levels.py)
    levels_url: str = ""
    # matching against the CV (cv_match.py)
    cv_have: list = field(default_factory=list)
    cv_missing: list = field(default_factory=list)
    cv_mismatch: list = field(default_factory=list)
    cv_partial: list = field(default_factory=list)
    cv_full: bool = False
    cv_pick: str = ""
    cv_fit: int = -1  # Fit % of the vacancy and the CV (cv_match.fit_percent); -1 — not computed
    grade: str = ""  # junior / middle / senior / lead — from the title or required experience (salary.grade)
    cv_file: str = ""  # PDF CV for the vacancy built automatically (cvgen.auto_build), path from the project root
    cv_core_have: list = field(default_factory=list)
    cv_core_missing: list = field(default_factory=list)
    cv_nice_have: list = field(default_factory=list)
    cv_nice_missing: list = field(default_factory=list)
    tier: int = 4  # category: 1 backend, 2 ML, 3 DevOps/platform, 4 other (tiers.py)
    sources: list = field(default_factory=list)  # all sources where the vacancy was seen
    extra: dict = field(default_factory=dict)  # raw freehire fields (enrichment, collections, …)


def norm_name(name):
    return re.sub(r"[^a-z0-9а-яё]", "", name.lower())


def html_to_text(raw):
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse sends HTML escaped twice
    text = re.sub(r"(?i)<(?:br|/p|/li|/h\d|/div|/ul)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text).strip()


def parse_date(value):
    """ISO string, unix seconds or milliseconds -> aware datetime (UTC)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            if value > 1e11:
                value /= 1000
            return datetime.fromtimestamp(value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
