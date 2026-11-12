"""Reaching a real person about a vacancy: direct contacts from the text, LinkedIn people search, drafts.

The script sends nothing and does not visit LinkedIn: it only finds contacts in the vacancy text,
builds people-search links (the user opens them) and drafts of short messages —
to a recruiter and to a company engineer asking for a referral — which the user copies and sends by hand.
"""
from __future__ import annotations

import re
from urllib.parse import quote

# limit of a note to a LinkedIn connection request — 300 characters
NOTE_LIMIT = 300


def _outreach_settings() -> tuple[str, str]:
    """(who you are, where you are) for drafts: settings.toml [outreach], by default from [home]."""
    from . import config, geo  # geo reads settings.toml on import

    cfg = geo.S.get("outreach", {})
    pitch = cfg.get("pitch") or "Backend Engineer with Python and PostgreSQL experience"
    based = cfg.get("based_in") or (f"Based in {config.HOME_COUNTRY}." if config.HOME_COUNTRY else "")
    return pitch, based

# skills already named in the first sentence of the message
_BASE_SKILLS = {"python", "postgresql", "sql"}

_PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="


def real_company(company: str) -> str:
    """Company name, or an empty string for the placeholders "—" and "Fintech (company not named)"."""
    company = (company or "").strip()
    return "" if company in ("", "—") or "not named" in company else company


def search_links(company: str) -> list[tuple[str, str]]:
    """Links to search the company's people on LinkedIn: [(label, url)]; no company — empty.

    Args:
        company: company name as in the vacancy.

    Returns:
        A search for recruiters and a search for engineering managers.
    """
    company = real_company(company)
    if not company:
        return []
    return [
        ("recruiter", _PEOPLE_SEARCH + quote(f"{company} recruiter")),
        ("manager", _PEOPLE_SEARCH + quote(f"{company} engineering manager")),
        ("engineer for a referral", _PEOPLE_SEARCH + quote(f"{company} backend engineer")),
    ]


_EMAIL_RX = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?![\w-])", re.I)
_TG_LINK_RX = re.compile(r"(?:https?://)?t\.me/([A-Za-z][\w]{3,})(?![\w/])")
_TG_HANDLE_RX = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_]{3,})\b(?!\.\w)")  # not "@company.com"
_LINKEDIN_RX = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.I)
# addresses nobody writes to about a vacancy: mailing lists, lawyers, support, privacy
_SKIP_EMAIL_RX = re.compile(
    r"^(?:no-?reply|do-?not-?reply|privacy|gdpr|dpo|legal|support|help|abuse|security|billing|accounts?|"
    r"unsubscribe|notifications?)@|accommodat|accessib|privacy|compliance|\beeo\b|ethics|whistle", re.I)
_TELEGRAM_WORD_RX = re.compile(r"telegram|телеграм|\btg\b|тг\b", re.I)
# @mentions that are not contacts: service names, domains, our source channels
_SKIP_HANDLE_RX = re.compile(r"^(?:gmail|outlook|yahoo|mail|example|channel|admin)$", re.I)


def contacts(text: str, channel: str = "") -> list[tuple[str, str, str]]:
    """Direct contacts from the vacancy text: [(kind, what to show, link)] without repeats.

    Kinds: email, telegram, linkedin. Service addresses (noreply, privacy, support…) and a mention
    of the source channel itself do not count as contacts.

    Args:
        text: text of the vacancy or post.
        channel: Telegram channel the post came from (its @mention is not a contact).
    """
    found: dict[str, tuple[str, str, str]] = {}
    for email in _EMAIL_RX.findall(text or ""):
        if not _SKIP_EMAIL_RX.search(email):
            found.setdefault(email.lower(), ("email", email, f"mailto:{email}"))
    emails_text = " ".join(found)
    # @handle is Telegram only in a post from Telegram or next to the word "Telegram": in ATS texts
    # the at sign usually means Instagram / X or a mail domain
    handles = list(_TG_LINK_RX.findall(text or ""))
    for m in _TG_HANDLE_RX.finditer(text or ""):
        near = (text or "")[max(0, m.start() - 60): m.end() + 60]
        if channel or _TELEGRAM_WORD_RX.search(near):
            handles.append(m.group(1))
    for handle in handles:
        low = handle.lower()
        if low == channel.lower() or _SKIP_HANDLE_RX.match(handle) or f"@{low}." in emails_text:
            continue
        found.setdefault(f"tg:{low}", ("telegram", f"@{handle}", f"https://t.me/{handle}"))
    for url in _LINKEDIN_RX.findall(text or ""):
        href = url if url.startswith("http") else f"https://{url}"
        found.setdefault(href.lower().rstrip("/"), ("linkedin", url.split("/in/")[-1].strip("/"), href))
    return list(found.values())


def telegram_contact(job) -> str:
    """Link to the vacancy author in Telegram if the post gave a @handle; otherwise an empty string."""
    handle = getattr(job, "contact", "").lstrip("@")
    return f"https://t.me/{handle}" if handle else ""


def _hook(job) -> str:
    """Two or three matched key technologies of the vacancy beyond Python and PostgreSQL.

    Skills the vacancy matched the profile on (matched_skills) go first, then the rest.
    """
    have = [s for s in getattr(job, "cv_core_have", []) if s.lower() not in _BASE_SKILLS]
    matched = {s.lower() for s in getattr(job, "matched_skills", [])}
    skills = sorted(have, key=lambda s: s.lower() not in matched)[:3]
    if not skills:
        return ""
    listed = skills[0] if len(skills) == 1 else ", ".join(skills[:-1]) + f" and {skills[-1]}"
    return f", hands-on with {listed}"


def draft(job) -> str:
    """A message draft in English, at most NOTE_LIMIT characters.

    If the full version does not fit, the list of technologies is removed first,
    then the vacancy title is replaced with a generic "a backend engineer".

    Args:
        job: vacancy (Job or Telegram vacancy) with company, title and cv_core_have.

    Returns:
        Text to send by hand.
    """
    company = real_company(job.company) or "your company"
    title = " ".join((job.title or "").split())
    pitch, based = _outreach_settings()
    tail = " ".join(filter(None, [based, "Would you be the right person to talk to about it? Thanks!"]))
    role = f"the {title} position" if title else "a backend engineer"
    variants = [(role, _hook(job)), (role, ""), ("a backend engineer", "")]
    for role, hook in variants:
        text = (f"Hi! I noticed {company} is hiring for {role}. "
                f"I'm a {pitch}{hook}. {tail}")
        if len(text) <= NOTE_LIMIT:
            return text
    return text[:NOTE_LIMIT - 1].rstrip() + "…"


def referral_draft(job) -> str:
    """A referral request draft to a company engineer (not a recruiter), at most NOTE_LIMIT.

    Short, with a ready "package": who you are, which role, what you will send — so it is easy to answer.
    """
    company = real_company(job.company) or "your company"
    title = " ".join((job.title or "").split())
    pitch, based = _outreach_settings()
    tail = " ".join(filter(None, [based, "Would you be open to referring me? "
                                         "I'll send my CV and a 2-line summary to make it easy. Thanks!"]))
    variants = [(f"the {title} role" if title else "a backend role", _hook(job)),
                (f"the {title} role" if title else "a backend role", ""), ("a backend role", "")]
    for role, hook in variants:
        text = (f"Hi! I'm applying for {role} at {company}. "
                f"{pitch}{hook}. {tail}")
        if len(text) <= NOTE_LIMIT:
            return text
    return text[:NOTE_LIMIT - 1].rstrip() + "…"
