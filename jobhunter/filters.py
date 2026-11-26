"""Vacancy selection: keywords, location, primary language."""

from . import config as c, geo, language, settings, verify
from .stack import language_mismatch_text


def role_ok(title, text, relaxed=False):
    """One role filter for all sections (ATS, aggregators, Telegram).

    relaxed — for priority companies: any engineering role with python in the text fits.
    """
    if c.ROLE_EXCLUDE_RX.search(title) or c.TITLE_EXCLUDE_RX.search(title):
        return False
    full = f"{title}\n{text}"
    if c.OPS_ROLE_RX.search(title) and not c.CORE_TITLE_RX.search(title):
        return bool(c.PYTHON_RX.search(full))  # DevOps / SRE — category 3, only with Python
    if relaxed and c.PRIORITY_ROLE_RX.search(title):
        return bool(c.PYTHON_RX.search(full))
    if c.ROLE_CORE_RX.search(title):
        return bool(c.KEYWORD_RX.search(full) or c.TG_KEYWORD_RX.search(full))
    if c.ROLE_AIML_RX.search(title) or c.ROLE_PLATFORM_RX.search(title) or c.ROLE_PENALTY_RX.search(title):
        return bool(c.PYTHON_RX.search(full))
    return False


def title_candidate(title):
    """A rough check by the title alone — is it worth opening the vacancy page."""
    if c.ROLE_EXCLUDE_RX.search(title) or c.TITLE_EXCLUDE_RX.search(title):
        return False
    if c.OPS_ROLE_RX.search(title) and not c.CORE_TITLE_RX.search(title):
        return True
    return bool(
        c.ROLE_CORE_RX.search(title) or c.ROLE_AIML_RX.search(title) or c.ROLE_PLATFORM_RX.search(title)
        or c.ROLE_PENALTY_RX.search(title)
    )


def full_time(title, employment, text):
    """Full-time employee or contract; part-time, freelance, hourly pay and internships are not."""
    if c.NOT_FULL_TIME_RX.search(f"{title} {employment}") or c.NOT_A_JOB_TITLE_RX.search(title):
        return False
    return not (c.HOURLY_PAY_RX.search(text) or c.NOT_A_JOB_TEXT_RX.search(text))


def residency_blocked(text, location):
    """"must reside in …" / "not providing relocation", unless the home country or EU is next to it."""
    if c.RESIDENCY_OK_RX.search(location):
        return False
    for m in c.RESIDENCY_RX.finditer(text):
        if not c.RESIDENCY_OK_RX.search(text[max(0, m.start() - 60): m.end() + 120]):
            return True
    return False


def no_support(text):
    """The text says outright that there is no relocation or visa support."""
    return bool(c.NO_SUPPORT_RX.search(text))


def keyword_match(job):
    relaxed = geo.company_key(job.company) in geo.rules().priority_companies
    return role_ok(job.title, job.description, relaxed)


def location_verdict(job):
    """'ok' | 'check' (remote, but tied to a country/city) | None (does not fit)."""
    loc = job.location or ""
    text = f"{loc} {job.title}"
    if c.HOME_RX.search(text):
        return "ok"
    if not (job.remote or c.REMOTE_RX.search(text)):
        return None
    # "Remote - CA", "U.S. Anywhere", "NAMER" — tied to the USA unless Europe is next to it
    if c.US_BOUND_RX.search(text) and not c.LOCATION_EUROPE_RX.search(loc):
        return None if c.NON_EU_RX.search(loc) else "check"
    if c.BROAD_RX.search(loc) or c.TITLE_BROAD_RX.search(job.title):
        return "ok"
    if not c.LOCATION_NOISE_RX.sub("", loc):
        return "ok"  # just "Remote", no region
    if c.NON_EU_RX.search(loc):
        return None
    return "check"


def language_mismatch(job):
    """The name of a language that looks primary instead of Python, or ''."""
    return language_mismatch_text(job.title, job.description)


def _reject(job, reason, quote=""):
    """Stores the reject reason (for the "Rejected by filters" section) and returns False."""
    job.reject, job.reject_quote = reason, quote
    return False


def apply(job):
    """True if the vacancy passes; also sets the marks.

    If the vacancy fits by role but is dropped by another rule, the reason stays in job.reject.
    """
    if not job.url or not keyword_match(job):
        return False
    if not full_time(job.title, job.employment, job.description):
        m = (c.NOT_FULL_TIME_RX.search(f"{job.title} {job.employment}") or c.HOURLY_PAY_RX.search(job.description)
             or c.NOT_A_JOB_TEXT_RX.search(job.description) or c.NOT_A_JOB_TITLE_RX.search(job.title))
        return _reject(job, "not full-time: part-time / freelance / hourly or piecework pay", m.group() if m else "")
    verdict = location_verdict(job)
    if verdict != "ok":
        if no_support(job.description):
            return _reject(job, "location by the source label: not remote and not the home country", job.location[:80])
        # tied to a country: possibly relocation / remote in the target country
        is_remote = bool(job.remote or c.REMOTE_RX.search(f"{job.location} {job.title}"))
        in_target = settings.get().relocation_enabled and geo.classify(job, is_remote)
        if not in_target and (verdict is None or not settings.get().country_bound_remote):
            return _reject(job, "location by the source label: not remote and not the home country", job.location[:80])
    job.location_check = verdict == "check"
    full_text = job.source not in c.PREVIEW_ONLY_SOURCES
    verify.check(job, job.description, full_text)
    if job.reject:
        return False
    job.lang_marks, job.lang_required = language.analyze(job.title, job.description)
    job.mismatch_lang = language_mismatch(job)
    job.language_mismatch_warning = bool(job.mismatch_lang)
    return True
