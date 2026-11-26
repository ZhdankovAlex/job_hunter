"""Relocation and remote tied to a country (countries and companies are in settings.toml)."""
import re
from dataclasses import dataclass

from . import config, settings
from .keys import company_key
from .models import Job

# Settings are not read on import: rules() builds the patterns and sets from them on the first call
# and rebuilds them when settings.use() puts other settings in place.
OTHER = "Other"

# Kinds in display order within a country: confirmed relocation first
RELOCATION = "relocation"
UNCONFIRMED = "relocation (unconfirmed)"
REMOTE_OK = "remote: work from outside the country allowed"
REMOTE_UNCLEAR = "remote: unclear if work from outside the country is allowed"
RESIDENTS_ONLY = "❌ residents only"
TYPE_ORDER = [RELOCATION, UNCONFIRMED, REMOTE_OK, REMOTE_UNCLEAR, RESIDENTS_ONLY]


def _names_rx(names: list[str]) -> re.Pattern[str]:
    names = sorted(names, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.I)


# Big companies (bigtech.py) rarely mention relocation in the vacancy text, although they usually offer it:
# their vacancies in these countries go to 🌍 as "relocation (unconfirmed)".
BIGTECH_SOURCES = {"microsoft", "workday", "oracle", "amazon"}


@dataclass(frozen=True)
class Rules:
    """What geo builds from the settings: country patterns and sets of company keys."""

    country_rx: dict[str, re.Pattern[str]]
    exclude_rx: list[re.Pattern[str]]
    relocation_companies: set[str]
    priority_companies: set[str]
    bigtech_hubs_rx: re.Pattern[str]
    # companies on regular ATS boards that get the bigtech rule (big trading firms and the like)
    bigtech_companies: set[str]
    referral_companies: set[str]


def _build(s: settings.Settings) -> Rules:
    def rx(c: str) -> re.Pattern[str]:
        return _names_rx([c, *s.places.get(c, [])])

    return Rules(
        country_rx={c: rx(c) for c in s.relocation_countries},
        exclude_rx=[rx(c) for c in s.exclude_countries],
        relocation_companies={company_key(n) for names in s.relocation_companies.values() for n in names},
        priority_companies={company_key(n) for n in s.priority_companies},
        bigtech_hubs_rx=_names_rx(s.bigtech.relocation_hubs or ["-"]),
        bigtech_companies={company_key(n) for n in s.bigtech.companies},
        referral_companies={company_key(n) for n in s.referral_companies},
    )


_rules: tuple[settings.Settings, Rules] | None = None


def rules() -> Rules:
    """Rules for the current settings.get(): built once per settings object."""
    global _rules
    s = settings.get()
    if _rules is None or _rules[0] is not s:
        _rules = (s, _build(s))
    return _rules[1]


# "Georgia" is a country, not a US state
_GEORGIA_CITY_RX = re.compile(r"tbilisi|batumi|kutaisi", re.I)
_GEORGIA_COUNTRY_CONTEXT_RX = re.compile(r"tbilisi|batumi|kutaisi|caucasus|\bGEL\b")
_US_CONTEXT_RX = re.compile(r"\bUSA?\b|U\.S\.|United States|Atlanta|\bGA\b")

_SENTENCE_SPLIT_RX = re.compile(r"[\n.!?;•]+")
# Only an offer of help: "relocation package", "visa sponsorship", "we sponsor visas".
# "eager to relocate", "relocation to Tokyo after onboarding" do not count.
_RELOCATION_RX = re.compile(
    r"relocation\W+(?:\w+\W+){0,2}?(?:support|package|assistance|bonus|allowance|budget|help|benefits?)|"
    r"(?:support|help|assist\w*|cover\w*|pay\w*)\W+(?:\w+\W+){0,3}?relocat\w+|"
    r"visa\W+(?:and\W+\w+\W+)?(?:sponsor\w*|support|assistance)|"
    r"sponsor\w*\W+(?:\w+\W+){0,3}?(?:visas?|work permits?)\b|"
    r"work permit\W+(?:sponsor\w*|support|assistance)|blue card|highly skilled migrant|"
    r"help you (?:to )?(?:move|relocate)|"
    r"помо\w+ с (?:переездом|релокацией|визой)|релокационн\w+ (?:пакет|бонус)|"
    r"визов\w+ поддержк\w+|оплат\w+ (?:переезд\w*|релокац\w+)",
    re.I,
)
# "we do not offer relocation" — a negation right before the phrase
_NEGATION_RX = re.compile(
    r"\b(?:no|not|unable|cannot|can't|don't|doesn't|won't|without|unfortunately)\b|\bне\b|\bбез\b", re.I
)
_NEGATION_AFTER_RX = re.compile(
    r"\W*(?:\w+\W+){0,3}?(?:not|never|unavailable|нет|не)\b", re.I
)
_RELOCATION_TARGET_RX = re.compile(r"\bto\s+(?:the\s+)?([A-Z][\w-]+(?:\s[A-Z][\w-]+)?)")
_ANYWHERE_RX = re.compile(
    # "Remote from anywhere" — yes; "from anywhere in the US" is remote tied to a country
    r"work from anywhere|remote from anywhere(?!\s+in\b)|anywhere in (?:the )?(?:world|europe|eu|emea)|"
    r"remote (?:with)?in (?:the )?(?:eu|emea|europe)|across (?:the )?(?:eu|emea|europe)|"
    r"any country|from any (?:location|country)|fully distributed",
    re.I,
)
_RESIDENT_RX = re.compile(
    r"must (?:be|reside|live) (?:\w+ )?(?:based|located|resident|residing)? ?in|based in {c}|"
    r"{c} only|only (?:in|from) {c}|right to work in|eligible to work in|"
    r"authori[sz]ed to work in|residen(?:t|ce|cy) (?:in|of)|valid work permit",
)
_SALARY_RX = re.compile(
    r"(?:[€$£]\s?\d{2,3}(?:[.,]\d{3})*\s?[kK]?\s?(?:[-–—]|to)\s?[€$£]?\s?\d{2,3}(?:[.,]\d{3})*\s?[kK]?"
    r"|\d{2,3}(?:[.,]\d{3})*\s?[kK]?\s?(?:[-–—]|to)\s?\d{2,3}(?:[.,]\d{3})*\s?[kK]?\s?(?:€|EUR|USD|GBP))"
)


def detect_country(location: str, description: str = "") -> str:
    """Target country by the vacancy location (city or country), or ''."""
    for country, rx in rules().country_rx.items():
        if not rx.search(location):
            continue
        if country == "Georgia" and not _GEORGIA_CITY_RX.search(location):
            if _US_CONTEXT_RX.search(location):
                continue
            if not _GEORGIA_COUNTRY_CONTEXT_RX.search(description) and _US_CONTEXT_RX.search(description):
                continue
        return country
    return ""


def relocation_quote(text: str, country: str = "", max_words: int = 12) -> str:
    """A quote (≤12 words around the phrase) offering relocation TO this country, or ''."""
    for m in _RELOCATION_RX.finditer(text):
        if _NEGATION_RX.search(text[max(0, m.start() - 45): m.start()]):
            continue
        if _NEGATION_AFTER_RX.match(text[m.end(): m.end() + 60]):
            continue
        # "relocation support to Tokyo" — relocation to another place
        target = _RELOCATION_TARGET_RX.match(text[m.end(): m.end() + 40].lstrip())
        country_rx = rules().country_rx
        if target and country in country_rx and not country_rx[country].search(target.group(1)):
            continue
        before = text[: m.start()].split()[-(max_words // 3):]
        phrase = m.group().split()
        after = text[m.end():].split()[: max(0, max_words - len(before) - len(phrase))]
        quote = " ".join([*before, *phrase, *after])
        return re.split(r"[\n•]", quote)[0].strip() if "\n" not in m.group() else quote
    return ""


def role_fits(job: Job) -> bool:
    """The 🌍 section — only engineering Python roles."""
    if config.RELOC_EXCLUDE_TITLE_RX.search(job.title):
        return False
    text = f"{job.title}\n{job.description}"
    if config.RELOC_NEEDS_PYTHON_TITLE_RX.search(job.title):
        return bool(config.PYTHON_RX.search(text))
    return bool(config.KEYWORD_RX.search(text))


def classify(job: Job, is_remote: bool) -> bool:
    """Fills job.reloc_* for a vacancy tied to a country. True — the vacancy is taken."""
    if not role_fits(job):
        return False
    country = detect_country(job.location, job.description)
    quote = relocation_quote(job.description, country)
    r = rules()
    bigtech = getattr(job, "source", "") in BIGTECH_SOURCES or company_key(job.company) in r.bigtech_companies
    bigtech_hub = bigtech and bool(r.bigtech_hubs_rx.search(job.location))
    if not country:
        if not settings.get().any_country or any(rx.search(job.location) for rx in r.exclude_rx):
            return False
        if quote:
            job.reloc_country, job.reloc_type, job.reloc_quote = OTHER, RELOCATION, quote
            return True
        if bigtech_hub:
            job.reloc_country, job.reloc_type, job.reloc_quote = OTHER, UNCONFIRMED, ""
            return True
        return False
    if quote:
        kind = RELOCATION
    elif is_remote:
        resident_rx = re.compile(_RESIDENT_RX.pattern.replace("{c}", re.escape(country)), re.I)
        if _ANYWHERE_RX.search(job.description):
            kind = REMOTE_OK
        elif resident_rx.search(job.description):
            kind = RESIDENTS_ONLY
        else:
            kind = REMOTE_UNCLEAR
    elif company_key(job.company) in r.relocation_companies or bigtech_hub:
        kind = UNCONFIRMED
    else:
        return False
    job.reloc_country, job.reloc_type, job.reloc_quote = country, kind, quote
    if not job.salary:
        m = _SALARY_RX.search(job.description)
        job.salary = m.group().strip() if m else ""
    return True
