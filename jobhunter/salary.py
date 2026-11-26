"""Salary ranges: parsing the amounts stated in vacancies and an estimate for vacancies without a range.

The estimate is built from a growing base of vacancies with an explicitly stated range
(state/salary_samples.json): Telegram channels (youritjob, IT_iGaming, af_job_board and others)
and boards/aggregators where salary comes as a separate field. Everything is converted to EUR per month.
It is a market guide, not an offer from a particular company.
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import date, timedelta

from . import config

SAMPLES_FILE = config.ROOT / "state" / "salary_samples.json"
KEEP_DAYS = 180
MIN_SAMPLES = 4
MIN_SAMPLES_CIS = 3  # there are few ranges from Russian-speaking market channels
MARKETS = (("cis", "Russian-speaking market channels"), ("global", "global market, mostly US and EU"))
DEFAULT_RATES = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "PLN": 0.23, "CHF": 1.05, "CAD": 0.67, "AUD": 0.6, "RUB": 0.0095}

_CURRENCY_RX = re.compile(r"(\$|€|£|₽|\busd\b|\beur\b|\bgbp\b|\bpln\b|zł|\bchf\b|\bcad\b|\baud\b|\brub\b|руб)", re.I)
_CURRENCY = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "pln": "PLN",
             "zł": "PLN", "chf": "CHF", "cad": "CAD", "aud": "AUD", "₽": "RUB", "rub": "RUB", "руб": "RUB"}
_NUMBER_RX = re.compile(r"(\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d+)?)\s?([kкKК])?(?![\w%])")
_RANGE_GAP_RX = re.compile(r"\s*(?:-|–|—|to|до)\s*[$€£]?\s*", re.I)  # between the numbers of a range: "4.5 – €5.5k"
_YEAR_RX = re.compile(r"year|annual|annum|/\s?yr|\bp\.?a\.?\b|в год|год", re.I)
_HOUR_RX = re.compile(r"hour|/\s?hr?\b|/h\b|в час|час", re.I)
_DAY_RX = re.compile(r"\bday\b|/\s?day|в день", re.I)

# Role families — order matters: narrower ones first
_NOT_ENGINEERING_RX = re.compile(
    r"marketing|design(?:er)? engineer|advocate|solutions?|forward deployed|sales|support|customer|"
    r"growth engineer|founding|autonomy|system engineer|hardware|mechanical|electrical", re.I)

_FAMILIES = (
    ("management", re.compile(r"manager|director|\bhead\b|product owner|\bcto\b|\bcpo\b|менеджер|руководител", re.I)),
    ("qa", re.compile(r"\bqa\b|\btest|quality|тестировщик|sdet", re.I)),
    ("frontend", re.compile(r"front[\s-]?end|react|vue|angular|фронт", re.I)),
    ("mobile", re.compile(r"\bios\b|android|mobile|flutter", re.I)),
    ("devops", re.compile(r"devops|\bsre\b|site reliability|infrastructure|platform|dataops|cloud|sysadmin|системн", re.I)),
    ("data", re.compile(r"data (?:engineer|analyst|scientist)|analytics|analyst|аналитик", re.I)),
    ("ml", re.compile(r"\bml\b|\bai\b|machine learning|\bllm\b|deep learning", re.I)),
    ("fullstack", re.compile(r"full[\s-]?stack|фулстек", re.I)),
    ("backend", re.compile(
        r"back[\s-]?end|б[эе]к[\s-]?енд|python|django|java\b|kotlin|golang|\bgo\b|node|php|\.net|ruby|scala|"
        r"software (?:development )?(?:engineer|developer)|разработчик|программист", re.I)),
)
FAMILY_NAMES = {"backend": "backend", "fullstack": "fullstack", "ml": "ML/AI", "data": "data", "devops": "DevOps/platform",
                "mobile": "mobile", "frontend": "frontend", "qa": "QA", "management": "management"}
_SENIORITY = (
    ("lead", re.compile(r"\blead\b|staff|principal|architect|тимлид|\bhead\b", re.I)),
    ("senior", re.compile(r"senior|\bsr\.?\b|сеньор|старш", re.I)),
    ("middle", re.compile(r"middle|\bmid\b|мидл", re.I)),
    ("junior", re.compile(r"junior|\bjr\.?\b|intern|trainee|джун|стаж", re.I)),
)


def rates() -> dict[str, float]:
    """Rates to EUR: from settings.toml ([salary.rates_to_eur]) on top of the defaults."""
    from . import settings

    return {**DEFAULT_RATES, **settings.get().salary.rates_to_eur}


def min_monthly() -> int:
    """Money threshold, EUR/month ([salary] min_monthly_eur in settings.toml; 0 — off)."""
    from . import settings

    return int(settings.get().salary.min_monthly_eur)


def below_min(job) -> bool:
    """The range stated in the vacancy itself is fully below the threshold (upper bound < min_monthly_eur).

    Only an explicit range (parse): the base estimate and levels.fyi do not count — without a range a vacancy is not "cheap".
    """
    limit = min_monthly()
    stated = parse(getattr(job, "salary", "") or "") if limit else None
    return stated is not None and stated[1] < limit


def meets_min(job) -> bool:
    """The vacancy states a range, and its upper bound is not below min_monthly_eur. Without a range — False."""
    limit = min_monthly()
    stated = parse(getattr(job, "salary", "") or "") if limit else None
    return stated is not None and stated[1] >= limit


def family(title: str) -> str:
    if _NOT_ENGINEERING_RX.search(title):
        return ""
    return next((name for name, rx in _FAMILIES if rx.search(title)), "")


MONTHLY_BIG_CURRENCIES = {"RUB", "KZT"}

# required experience: "Опыт работы: 1–3 года", "3–6 лет", "более 6 лет", "от 5 лет", "5+ years"
_EXPERIENCE_RANGE_RX = re.compile(
    r"опыт\w*[^.\n]{0,40}?(?:от\s*)?(\d{1,2})\s*(?:[–—-]\s*(\d{1,2}))?\s*(?:\+\s*)?(?:года?|лет)|"
    r"(?:более|свыше)\s+(\d{1,2})\s+лет|"
    r"(\d{1,2})\s*(?:[–—-]\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs)(?:['’]?\s+of)?\s+(?:[\w-]+\s+){0,3}experience",
    re.I,
)
GRADE_NAMES = {"junior": "Junior", "middle": "Middle", "senior": "Senior", "lead": "Lead / Staff"}


def seniority(title: str) -> str:
    return next((name for name, rx in _SENIORITY if rx.search(title)), "")


def required_years(text: str) -> int | None:
    """Minimum required experience in years (first mention), or None."""
    m = _EXPERIENCE_RANGE_RX.search(text or "")
    if not m:
        return None
    value = next((g for g in m.groups() if g), None)
    return int(value) if value else None


def grade(title: str, text: str = "") -> str:
    """Vacancy grade: by the title, and if it has none — by the required experience.

    0–1 years — junior, 2–3 — middle, 4–7 — senior, 8+ — lead.
    """
    level = seniority(title)
    if level:
        return level
    years = required_years(text)
    if years is None:
        return ""
    return "junior" if years <= 1 else "middle" if years <= 3 else "senior" if years <= 7 else "lead"


def _number(raw: str, k: str | None) -> float:
    raw = raw.replace(" ", " ")
    if k:
        return float(raw.replace(" ", "").replace(",", ".")) * 1000
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", raw):
        return float(re.sub(r"[ .,]", "", raw))
    return float(raw.replace(",", "."))


def parse(text: str) -> tuple[int, int] | None:
    """Range from a salary string -> (from, to) in EUR per month; None if it cannot be parsed."""
    if not text:
        return None
    currency = _CURRENCY_RX.search(text)
    if not currency or _HOUR_RX.search(text) or _DAY_RX.search(text):
        return None
    rate = rates().get(_CURRENCY[currency.group(1).lower()], 0)
    matches = list(_NUMBER_RX.finditer(text))
    found = []
    for i, m in enumerate(matches):
        n, k = m.groups()
        nxt = matches[i + 1] if i + 1 < len(matches) else None
        # "4.5–5.5k EUR": "k" stands only at the second number of the range, but applies to both
        if not k and nxt and nxt.group(2) and _RANGE_GAP_RX.fullmatch(text[m.end():nxt.start()]):
            k = nxt.group(2)
        found.append((n, k))
    numbers = [_number(n, k) for n, k in found]
    numbers = [n for n in numbers if n >= 300]
    if not numbers or not rate:
        return None
    low, high = numbers[0], (numbers[1] if len(numbers) > 1 else numbers[0])
    if high < low:
        high = low
    code = _CURRENCY[currency.group(1).lower()]
    # in roubles and tenge a monthly salary above 25,000 is normal; yearly only with an explicit "в год / annual"
    yearly = bool(_YEAR_RX.search(text)) or (low >= 25000 and code not in MONTHLY_BIG_CURRENCIES)
    divisor = 12 if yearly else 1
    low, high = low * rate / divisor, high * rate / divisor
    if not 300 <= low <= 40000 or not 300 <= high <= 60000:
        return None
    return round(low), round(high)


def _fmt(low: int, high: int) -> str:
    def k(v):
        return f"{round(v / 100) * 100:,}".replace(",", " ")
    return f"€{k(low)}" if abs(high - low) < 100 else f"€{k(low)}–{k(high)}"


class SalaryBase:
    """Collected ranges and an estimate from them."""

    def __init__(self) -> None:
        """Reads the base from disk and drops entries older than KEEP_DAYS."""
        self.samples: dict[str, dict] = {}
        if SAMPLES_FILE.exists():
            self.samples = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self.samples = {k: v for k, v in self.samples.items() if v.get("date", "") >= cutoff}

    def add(self, key: str, title: str, salary_text: str, market: str, source: str, day: str) -> None:
        """Adds a vacancy with a stated range.

        market: 'cis' — Russian-speaking market channels (ranges usually monthly, after tax);
        'global' — international lists and boards (ranges more often yearly, US/EU).
        """
        parsed = parse(salary_text)
        fam = family(title)
        if not parsed or not fam or not key:
            return
        self.samples[key] = {
            "family": fam, "seniority": seniority(title), "low": parsed[0], "high": parsed[1],
            "market": market, "source": source, "date": day, "title": title[:90],
        }

    def save(self) -> None:
        SAMPLES_FILE.parent.mkdir(exist_ok=True)
        SAMPLES_FILE.write_text(json.dumps(self.samples, ensure_ascii=False, indent=0), encoding="utf-8")

    def _pool(self, fam: str, level: str, market: str) -> list[dict]:
        return [s for s in self.samples.values()
                if s["family"] == fam and s["market"] == market and (not level or s["seniority"] == level)]

    def describe(self, title: str, salary_text: str = "", level: str = "") -> str:
        """A line for the report: the stated range in EUR/month, or an estimate from the base ('' — too little data).

        level — vacancy grade (salary.grade: title or required experience).
        """
        stated = parse(salary_text)
        if stated:
            return f"{_fmt(*stated)}/mo (stated)"
        fam, level = family(title), level or seniority(title)
        if not fam:
            return salary_text.strip()
        # Russian-speaking market first (if there are ranges from [telegram].cis_channels), then global; same grade first
        for market, label in MARKETS:
            need = MIN_SAMPLES_CIS if market == "cis" else MIN_SAMPLES
            for lvl in ([level, ""] if level else [""]):
                pool = self._pool(fam, lvl, market)
                if len(pool) >= need:
                    mids = sorted((s["low"] + s["high"]) / 2 for s in pool)
                    q = statistics.quantiles(mids, n=4) if len(mids) >= 2 else [mids[0]] * 3
                    who = f"{lvl + ' ' if lvl else ''}{FAMILY_NAMES[fam]}"
                    return f"≈ {_fmt(round(q[0]), round(q[2]))}/mo (estimate: {who}, {len(pool)} vacancies, {label})"
        return salary_text.strip()

    def summary(self) -> list[str]:
        """Base summary for the report: median ranges by family and grade."""
        lines = []
        for market, label in MARKETS:
            groups: dict[tuple[str, str], list[dict]] = {}
            for s in self.samples.values():
                if s["market"] == market:
                    groups.setdefault((s["family"], s["seniority"] or "no grade"), []).append(s)
            parts = []
            for (fam, level), pool in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                if len(pool) < (MIN_SAMPLES_CIS if market == "cis" else MIN_SAMPLES) or fam not in ("backend", "fullstack", "ml", "data", "devops"):
                    continue
                low = statistics.median(s["low"] for s in pool)
                high = statistics.median(s["high"] for s in pool)
                parts.append(f"{level} {FAMILY_NAMES[fam]} {_fmt(round(low), round(high))} ({len(pool)})")
            if parts:
                lines.append(f"{label}: " + "; ".join(parts[:8]))
        return lines
