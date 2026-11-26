"""Checking a vacancy by its full text: location, experience, stack, kind of work.

A source's location label is unreliable ("Spain only" with the text "In-person in our
Chennai office"), so the final status is set by the vacancy text:

* job.reject / job.reject_quote — the vacancy is hidden, the reason goes to the
  "Rejected by filters" section;
* job.manual_check — status ❓: the text is unavailable or the remote region is not confirmed;
  such vacancies do not get into "Top to review";
* job.penalties — penalties to the score (level, experience, role, language).
"""
from __future__ import annotations

import re

from . import config as c, geo, settings
from .models import Checked
from .reasons import Reject
from .stack import language_mismatch_text

MIN_TEXT_CHARS = 400  # shorter — we assume there is no vacancy text
ONSITE_REASON = f"on-site outside {c.HOME_COUNTRY}" if c.HOME_COUNTRY else "on-site"
# remote from Russia only ("на территории РФ") — like "US only" for Russian-language sources:
# settings.toml [filters].hide_russia_only = true hides such vacancies

_SENTENCE_RX = re.compile(r"[^\n.!?;•]+")
_SKILLS_LINE_RX = re.compile(r"\nSkills: .*$", re.S)

_CITIES = (
    "Chennai|Bangalore|Bengaluru|Mumbai|Hyderabad|Pune|Delhi|Gurgaon|Gurugram|Noida|Manila|Cebu|"
    "London|Manchester|Edinburgh|Dublin|Berlin|Munich|Hamburg|Frankfurt|Warsaw|Krakow|Kraków|Wroclaw|"
    "Paris|Amsterdam|Rotterdam|Madrid|Barcelona|Lisbon|Porto|Vienna|Zurich|Geneva|Stockholm|Copenhagen|"
    "Oslo|Helsinki|Tallinn|Riga|Vilnius|Prague|Budapest|Bucharest|Sofia|Athens|Istanbul|Tel Aviv|Dubai|"
    "Riyadh|Singapore|Hong Kong|Tokyo|Seoul|Sydney|Melbourne|Toronto|Vancouver|Montreal|New York|NYC|"
    "San Francisco|Seattle|Austin|Boston|Chicago|Denver|Los Angeles|Atlanta|Miami|Mexico City|"
    "São Paulo|Sao Paulo|Buenos Aires|Bogot[aá]|Cape Town|Lagos|Nairobi|Cairo|Limassol|Nicosia|Tbilisi|"
    "Yerevan|Almaty|Moscow|Kyiv|Minsk"
)
_COUNTRIES = (
    r"US|USA|U\.S\.A?\.?|United States|UK|U\.K\.|United Kingdom|EU|European Union|EEA|Canada|India|"
    "Germany|Poland|Spain|Portugal|Netherlands|France|Italy|Ireland|Switzerland|Austria|Sweden|"
    "Denmark|Norway|Finland|Estonia|Latvia|Lithuania|Czech(?:ia| Republic)|Hungary|Romania|Bulgaria|"
    "Greece|Turkey|Israel|UAE|Brazil|Mexico|Argentina|Colombia|Australia|New Zealand|Japan|Singapore|"
    "Philippines|Malaysia|LATAM|Latin America|North America|Americas|APAC|Asia|Africa|Cyprus|Georgia|"
    "Armenia|Kazakhstan|Ukraine"
)
_FOREIGN_PLACE_RX = re.compile(rf"\b(?:{_CITIES})\b|(?<![\w.])(?:{_COUNTRIES})(?![\w])")
_HOME_OR_OPEN_RX = re.compile(c.HOME_RX.pattern + r"|\beurope\b|\bemea\b|\bcet\b|anywhere|any country|worldwide", re.I)

# "work from the office" phrases; "hybrid" — only as a work format, not "hybrid cloud"
_ONSITE_RX = re.compile(
    r"\bin[\s-]person\b|\bon[\s-]?site\b|\bin (?:the|our) (?:[\w-]+ ){0,2}offices?\b|\boffice[\s-]based\b|"
    r"\bdays? (?:a|per|each) week (?:in|at|from) (?:the|our) (?:[\w-]+ )?office|"
    r"\bdays? (?:in|at) (?:the|our) (?:[\w-]+ )?office\b|"
    r"\bhybrid (?:work\w*|schedule|set-?up|working model|work model|arrangement|policy|format|basis)\b|"
    r"\(hybrid\)|\bhybrid in\b|\bhybrid[,:]? (?:\d|two|three|four)\b",
    re.I,
)
_CITY_WORKPLACE_RX = re.compile(
    rf"(?:\b(?:in|at|from|out of) our (?P<a>{_CITIES}) (?:office|hub|hq)|"
    rf"\b(?:based|located|on[\s-]?site|in[\s-]person) (?:in|at) (?:the |our )?(?P<b>{_CITIES})\b|"
    rf"\b(?P<c>{_CITIES})[\s-]based (?:role|position|candidates?|engineers?|developers?)\b|"
    rf"\b(?:role|position|job) is (?:based |located )?in (?:the |our )?(?P<d>{_CITIES})\b)"
)
_ABOUT_YOU_RX = re.compile(r"\b(?:you|your|candidates?|role|position|must|required|expected|this job)\b", re.I)
# Softeners: the office is mentioned, but coming in is optional
_SOFTENER_RX = re.compile(
    r"optional|not (?:required|mandatory|necessary)|no (?:need|requirement|obligation|expectation)|"
    r"if you (?:want|prefer|like|wish|choose|live)|option(?:ally)? to|can also|(?:or|and) (?:fully |100% )?remote|"
    r"remote(?:ly)?(?:,| or| and|/)|remote[\s-]first|fully remote|100% remote|occasional\w*|"
    r"remote\s*\([^)]*\)\s*or|contractor arrangement|\bor\s+(?:[A-Z][\w-]*\s+){0,2}on-?site|"
    r"once (?:a|per|every) (?:quarter|year|month)|times? (?:a|per) year|off-?sites?|meet-?ups?|"
    r"team (?:gatherings?|events?|retreats?)|retreat|onboarding|are welcome|welcome to|depending on|"
    r"hybrid (?:or|/) remote|on-?site interviews?|on-?call|visit\w*|travel\w*|whether you|"
    r"face[\s-]to[\s-]face|in[\s-]person (?:connection|time|events?|meetings?|gatherings?)|get[\s-]togethers?|"
    r"summits?|conferences?|all[\s-]hands|company[\s-]wide|gym|lunch|perks?|benefits?|"
    r"\bmeet(?:ing|s)? in[\s-]person|\bmet in[\s-]person|interview\w*|video calls?|co-?working|"
    r"every (?:\w+ ){0,4}(?:months?|quarters?|years?)|quarterly|annual\w*",
    re.I,
)
_RESIDENCY_RX = re.compile(
    r"must (?:be (?:based|located|resident|residing)|reside|live)(?: in)?|right to work in|"
    r"eligible to work in|authori[sz]ed to work in|work authori[sz]ation (?:in|for)",
    re.I,
)
# The text confirms remote without a tie to a country
_REMOTE_ANYWHERE_RX = re.compile(
    r"work(?:ing)? from anywhere|anywhere in (?:the )?(?:world|europe|emea)|remote(?:ly)? (?:with)?in (?:europe|emea)|"
    r"across (?:europe|emea)|fully distributed|globally distributed|world[\s-]?wide|location[\s-]agnostic|"
    r"any (?:country|location|time ?zone)|european time ?zones?|\bcet\b|\bemea\b|remote[\s,-]+(?:europe|global)",
    re.I,
)

_YEARS_RX = re.compile(
    r"\b(\d{1,2})\s?\+\s*(?:years|yrs|лет)|\b(\d{1,2})\s+(?:or more|plus)\s+years|\bот\s+(\d{1,2})\s*\+?\s*лет|"
    r"(?:minimum|at least|over|more than)\s+(?:of\s+)?(\d{1,2})\s+years(?=['’]?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience)",
    re.I,
)
_EXPERIENCE_CONTEXT_RX = re.compile(
    r"experience|engineer|develop|software|programming|industry|back[\s-]?end|python|professional|опыт|"
    r"разработ|коммерческ|в роли|работы", re.I
)
_NOT_A_JOB_RX = re.compile(
    # "annotation" by itself is not data labelling (PDF annotations, type annotations in Python)
    r"\bai trainer\b|train AI systems|per[\s-]task\b|output[\s-]based|\bannotators?\b|"
    r"data annotation|annotation (?:tasks?|work|projects?|jobs?)|data label", re.I
)
HIDE_LANGUAGES = {"Go", "Java", "Kotlin", "Ruby", "PHP", "C#/.NET", ".NET", "Rust"}
_REQUIRED_LANG_RX = re.compile(
    r"(?:experience|proficien\w+|expert\w*|strong|fluen\w+|years|skills?|knowledge|written)[^.\n]{0,40}?"
    r"(?:\b[Gg]olang\b|\bGo\b(?!-to|\s+(?:to|live|beyond|above))|\bJava\b(?!\s?Script)|\bKotlin\b|"
    r"\bRuby\b|\bPHP\b|\bRust\b|\bC#|(?<![\w])\.NET\b|\bTypeScript\b|\bNode\.?js\b)"
)
_OPS_TEXT_RX = re.compile(
    r"terraform|ansible|pulumi|helm|kubernetes|\bk8s\b|on[\s-]call|incident|\bsre\b|devops|"
    r"infrastructure as code|\biac\b|ci/cd pipelines?|observability|monitoring|alerting|provisioning|"
    r"cloud infrastructure|networking|load balanc\w+|capacity planning|runbooks?|uptime|\bslos?\b",
    re.I,
)
_PRODUCT_TEXT_RX = re.compile(
    r"\bapis?\b|product|feature|customers?|users?|business logic|domain|backend services?|microservices?|"
    r"data model|database schema|endpoints?|integrations?|payments?|checkout|platform for (?:our )?customers",
    re.I,
)
_LEVEL_RX = re.compile(r"\b(?:staff|principal|head|director)\b", re.I)
_ROLE_PENALTY_RX = c.ROLE_PENALTY_RX
_CORE_TITLE_RX = re.compile(r"back[\s-]?end|software|б[эе]к[\s-]?енд", re.I)


def quote(text: str, start: int, end: int, max_words: int = 12) -> str:
    """A quote of at most max_words words around the fragment text[start:end]."""
    before = text[:start].split()[-(max_words // 3):]
    phrase = text[start:end].split()
    after = text[end:].split()[: max(0, max_words - len(before) - len(phrase))]
    return " ".join([*before, *phrase, *after])[:160]


def core_text(text: str) -> str:
    """Vacancy text without the skills line the script appends itself."""
    return _SKILLS_LINE_RX.sub("", text or "").strip()


def label_kind(location: str, title: str) -> str:
    """'home' (home country) | 'broad' (Worldwide/Europe/EMEA/CIS…) | 'bare' (just Remote or a country)."""
    if c.HOME_RX.search(f"{location} {title}"):
        return "home"
    if c.BROAD_RX.search(location) or c.TITLE_BROAD_RX.search(title):
        return "broad"
    return "bare"


def _onsite(text: str, home_label: bool) -> tuple[int, int] | None:
    """Position of a phrase about mandatory office work outside the home country, or None."""
    for m in _SENTENCE_RX.finditer(text):
        sentence, offset = m.group(), m.start()
        if c.HOME_RX.search(sentence) or _SOFTENER_RX.search(sentence):
            continue
        city = _CITY_WORKPLACE_RX.search(sentence)
        if city and _ABOUT_YOU_RX.search(sentence):
            return offset + city.start(), offset + city.end()
        hit = _ONSITE_RX.search(sentence)
        if not hit:
            continue
        # for a vacancy in the home country "on-site" is fine — only another city next to it is a problem
        if home_label and not _FOREIGN_PLACE_RX.search(sentence):
            continue
        return offset + hit.start(), offset + hit.end()
    return None


def _residency(text: str) -> tuple[int, int] | None:
    """"must be based in / right to work in <another country>", or None."""
    for m in _RESIDENCY_RX.finditer(text):
        window = text[m.end(): m.end() + 90]
        place = _FOREIGN_PLACE_RX.search(window)
        if place and not _HOME_OR_OPEN_RX.search(window):
            return m.start(), m.end() + place.end()
    return None


def _years(text: str) -> tuple[int, tuple[int, int]] | None:
    """The largest years-of-experience requirement and its position in the text."""
    best = None
    for m in _YEARS_RX.finditer(text):
        n = int(next(g for g in m.groups() if g))
        if n > 20 or not _EXPERIENCE_CONTEXT_RX.search(text[m.end(): m.end() + 50]):
            continue
        if best is None or n > best[0]:
            best = (n, (m.start(), m.end()))
    return best


def _required_language(title: str, text: str) -> tuple[int, int] | None:
    """Another language is stated as required, and Python is hardly mentioned in the text."""
    if c.PYTHON_RX.search(title) or len(c.PYTHON_RX.findall(text)) > 1:
        return None
    for m in _SENTENCE_RX.finditer(text):
        hit = _REQUIRED_LANG_RX.search(m.group())
        if hit and not c.PYTHON_RX.search(m.group()):
            return m.start() + hit.start(), m.start() + hit.end()
    return None


# remote / work from Russia only: "удалённо на территории РФ", "гражданство РФ" (Russian vacancy texts)
_RU_ONLY_RX = re.compile(
    r"(?:территори\w*|пределах|находит\w*|проживани\w*|гражданств\w*|резидент\w*|только из|только в)\s+"
    r"(?:в\s+)?(?:РФ|России|Российской|Москв|Санкт|СПб)",
    re.I,
)
ABROAD_OK_RX = re.compile(r"за пределами (?:РФ|России)|из любой (?:точки|страны)|не обязательно в (?:РФ|России)", re.I)


def ru_only(text: str) -> bool:
    """Work is possible only from Russia (and it does not say work from abroad is fine)."""
    return bool(_RU_ONLY_RX.search(text)) and not ABROAD_OK_RX.search(text)



def check(job: Checked, text: str, full_text: bool) -> None:
    """Sets job.reject / job.manual_check / job.penalties from the vacancy text.

    job — Job or TgJob; text — the description; full_text — the description was received in full
    (False for sources that give only a preview).
    """
    title, location = job.title, job.location or ""
    body = core_text(text)
    has_text = full_text and len(body) >= MIN_TEXT_CHARS
    kind = label_kind(location, title)
    job.penalties, job.manual_check = [], ""

    def reject(code: Reject, reason: str, span: tuple[int, int] | None = None, source: str = body) -> None:
        job.reject, job.reject_code = reason, code
        job.reject_quote = quote(source, *span) if span else ""

    # --- remote from Russia only (settings.toml [filters].hide_russia_only)
    m = _RU_ONLY_RX.search(body) if settings.get().filters.hide_russia_only else None
    if m and not ABROAD_OK_RX.search(body):
        return reject(Reject.RUSSIA_ONLY, "remote from Russia only", m.span())

    # --- kind of work
    m = _NOT_A_JOB_RX.search(f"{title}\n{body}")
    if m:
        return reject(Reject.AI_TRAINER, "AI trainer / piecework data labelling", m.span(), f"{title}\n{body}")

    # --- location by the text
    # a vacancy with relocation to the target country: an office there is expected, not a reason to reject
    relocation = getattr(job, "reloc_type", "") in (geo.RELOCATION, geo.UNCONFIRMED)
    span = None if relocation else _onsite(body, home_label=(kind == "home"))
    if span and settings.get().relocation_enabled:
        # "Cyprus — Onsite, employment contract with relocation support": an office in another country + a move
        # paid by the employer — a vacancy for 🌍, not a reject (even if the source labelled it remote).
        # The country is taken from the title first ("… - Relocation to Cyprus"), then from the text.
        reloc_quote = geo.relocation_quote(body)
        if reloc_quote and not any(rx.search(f"{location} {title}") for rx in geo.rules().exclude_rx):
            job.reloc_type, job.reloc_quote = geo.RELOCATION, reloc_quote
            job.reloc_country = geo.detect_country(title) or geo.detect_country(body[:3000]) or geo.OTHER
            relocation, span = True, None
    if span:
        return reject(Reject.ONSITE, ONSITE_REASON, span)
    span = None if relocation else _residency(body)
    if span and kind != "home":
        return reject(Reject.COUNTRY_BOUND, "tied to country: you must live / have the right to work in another country", span)

    # --- experience
    experience = (getattr(job, "extra", None) or {}).get("enrichment", {}).get("experience_years_min") or 0
    years = _years(body)
    # 8+ years is not a verdict: the vacancy stays, but with a penalty (lower in the lists, not in the top)
    if (years and years[0] >= 8) or experience >= 8:
        job.penalties.append("experience_8")
    elif experience == 7 or (years and years[0] == 7):
        job.penalties.append("experience_7")

    # --- stack
    lang = language_mismatch_text(title, body)
    if lang in HIDE_LANGUAGES:
        rx = c.TITLE_LANGS.get(lang) or c.OTHER_LANGS.get(lang)
        m = rx.search(f"{title}\n{body}") if rx else None
        return reject(Reject.MAIN_LANGUAGE, f"primary language — {lang}", m.span() if m else None, f"{title}\n{body}")
    m = c.JVM_SPRING_RX.search(body)
    if m:
        return reject(Reject.JVM, "JVM / Spring in the requirements", m.span())
    span = _required_language(title, body)
    if span:
        return reject(Reject.REQUIRED_NOT_PYTHON, "required language is not Python", span)
    if lang:
        job.penalties.append("language")

    # --- penalties for level and role
    if _LEVEL_RX.search(title):
        job.penalties.append("level")
    if _ROLE_PENALTY_RX.search(title) and not _CORE_TITLE_RX.search(title):
        job.penalties.append("role")
    # the title says "Software Engineer", but the text is about Terraform, Kubernetes, on-call and incidents
    ops = len(_OPS_TEXT_RX.findall(body))
    product = len(_PRODUCT_TEXT_RX.findall(body))
    if ops >= 6 and ops >= 2 * max(product, 1):
        job.penalties.append("ops_heavy")

    # --- status
    if not has_text:
        job.manual_check = "vacancy text unavailable"
    elif kind == "bare" and not getattr(job, "reloc_type", "") and not _REMOTE_ANYWHERE_RX.search(body):
        job.manual_check = "remote region stated neither in the label nor in the text"
