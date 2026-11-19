"""Salaries by the vacancy country from levels.fyi: median and 25–75 percentiles of Software Engineer.

The source is the Markdown versions of pages levels.fyi serves to agents
(https://www.levels.fyi/t/software-engineer/locations/<country>.md, see their robots.txt and llms.txt):
the data may be read and quoted with a link, but not used to train models. Read through
jobhunter.http (robots.txt, 1 request/s), cached for CACHE_DAYS — about ten requests per run.

The numbers are total compensation (base + bonuses + stock) across all grades, reported by people;
senior is usually closer to the 75th percentile. Conversion to EUR uses the approximate rates from
salary.rates() (settings.toml → [salary].rates_to_eur).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from . import config, progress, salary
from .http import FetchError, HttpClient

log = logging.getLogger(__name__)

CACHE_FILE = config.ROOT / "state" / "levels_cache.json"
CACHE_DAYS = 7
BASE_URL = "https://www.levels.fyi/t/software-engineer/locations/{slug}"
EUROPE = "europe"

# levels.fyi page -> (label, signs in the vacancy location: country, cities, codes)
LOCATIONS = {
    "germany": ("Germany", r"germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|cologne|köln|\bDE\b"),
    "netherlands": ("Netherlands", r"netherlands|amsterdam|rotterdam|utrecht|eindhoven|the hague|\bNL\b"),
    "cyprus": ("Cyprus", r"cyprus|limassol|nicosia|larnaca|paphos"),
    "portugal": ("Portugal", r"portugal|lisbon|lisboa|porto\b"),
    "spain": ("Spain", r"spain|españa|madrid|barcelona|valencia|malaga|málaga"),
    "poland": ("Poland", r"poland|warsaw|warszawa|krakow|kraków|wroclaw|wrocław|gdansk|gdańsk|\bPL\b"),
    "united-kingdom": ("United Kingdom", r"united kingdom|\bUK\b|london|manchester|edinburgh|england"),
    "ireland": ("Ireland", r"ireland|dublin"),
    "france": ("France", r"france|paris|lyon"),
    "switzerland": ("Switzerland", r"switzerland|zurich|zürich|geneva|basel"),
    "austria": ("Austria", r"austria|vienna|wien"),
    "czech-republic": ("Czechia", r"czech|prague|praha|brno"),
    "estonia": ("Estonia", r"estonia|tallinn"),
    "lithuania": ("Lithuania", r"lithuania|vilnius"),
    "latvia": ("Latvia", r"latvia|riga\b"),
    "greece": ("Greece", r"greece|athens"),
    "serbia": ("Serbia", r"serbia|belgrade|beograd|novi sad|\bRS\b"),
    "georgia": ("Georgia", r"tbilisi|batumi|georgia \(country\)|sakartvelo"),
    "armenia": ("Armenia", r"armenia|yerevan"),
    "united-arab-emirates": ("UAE", r"united arab emirates|\bUAE\b|dubai|abu dhabi"),
    "israel": ("Israel", r"israel|tel aviv"),
    "united-states": ("USA", r"united states|\bUSA?\b|new york|san francisco|seattle|austin|boston|chicago"),
    "canada": ("Canada", r"canada|toronto|vancouver|montreal|montréal"),
}
_LOCATION_RX = {slug: re.compile(rx, re.I) for slug, (_, rx) in LOCATIONS.items()}
# remote without a country: the guide is the home country (settings.toml [home]) the candidate works from;
# if it is not in LOCATIONS — Europe as a whole
REMOTE_DEFAULT = next((slug for slug, rx in _LOCATION_RX.items() if config.HOME_COUNTRY and rx.search(config.HOME_COUNTRY)),
                      EUROPE)
_REMOTE_LABEL = f"{LOCATIONS[REMOTE_DEFAULT][0]} (remote)" if REMOTE_DEFAULT in LOCATIONS else "Europe (remote)"
_EUROPE_RX = re.compile(r"europe|\bEU\b|\bEMEA\b|\bCET\b", re.I)
_REMOTE_RX = re.compile(r"remote|worldwide|anywhere|global|удал", re.I)

# currencies missing from salary.DEFAULT_RATES (approximate, EUR per unit)
EXTRA_RATES = {"AED": 0.25, "CZK": 0.04, "RSD": 0.0085, "ILS": 0.25, "SEK": 0.087, "DKK": 0.134,
               "NOK": 0.085, "HUF": 0.0025, "GEL": 0.34, "AMD": 0.0024, "BGN": 0.51, "RON": 0.2}


def location_slug(location: str, reloc_country: str = "") -> tuple[str, str]:
    """(levels.fyi page, label) for the vacancy location; ("", "") — cannot tell.

    A country / city in the location — that country; "Remote Europe / EU / CET" — Europe as a whole;
    just remote / worldwide — the home country (REMOTE_DEFAULT): remote pay depends on where the worker lives.
    """
    text = f"{location} {reloc_country}"
    for slug, rx in _LOCATION_RX.items():
        if rx.search(text):
            return slug, LOCATIONS[slug][0]
    if _EUROPE_RX.search(text):
        return EUROPE, "Europe"
    if _REMOTE_RX.search(text) or not location.strip():
        return REMOTE_DEFAULT, _REMOTE_LABEL
    return "", ""


_NUMBER_RX = re.compile(r"([\d,.]+)\s*([KM])?", re.I)


def _amount(text: str) -> float | None:
    """"€71,199" / "CZK 1,026,759 per year" / "$62.8K" -> a number."""
    m = _NUMBER_RX.search(text.replace("\xa0", " "))
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value * {"K": 1e3, "M": 1e6}.get((m.group(2) or "").upper(), 1)


def parse(md: str) -> dict | None:
    """From a levels.fyi Markdown page: currency, median / percentiles per year, number of salaries."""
    currency = re.search(r"\*\*Currency:\*\*\s*([A-Z]{3})", md)
    median = re.search(r"Median Total Compensation:\s*([^\n]+)", md)
    quartiles = re.search(r"25th / 75th Percentile:\s*([^/\n]+)/([^\n]+)", md)
    sample = re.search(r"Sample Size:\s*([\d,]+)", md)
    if not (currency and median and quartiles and sample):
        return None
    median_value = _amount(median.group(1))
    # for some currencies the median is given per month, with "(… per year)" in brackets
    if median_value is not None and "per year)" in median.group(1):
        median_value *= 12
    return {
        "currency": currency.group(1), "median": median_value,
        "p25": _amount(quartiles.group(1)), "p75": _amount(quartiles.group(2)),
        "samples": int(sample.group(1).replace(",", "")),
    }


class Levels:
    """Cache of levels.fyi pages by country and the line for the report."""

    def __init__(self, http: HttpClient | None = None):
        self.http = http
        try:
            self.cache: dict = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.rates = {**EXTRA_RATES, **salary.rates()}

    def get(self, slug: str) -> dict | None:
        """Page data: from the cache if younger than CACHE_DAYS, otherwise a request (on failure — the old cache)."""
        entry = self.cache.get(slug)
        fresh = entry and (date.today() - date.fromisoformat(entry["fetched"])).days < CACHE_DAYS
        if fresh or self.http is None:
            return entry
        url = BASE_URL.format(slug=slug)
        try:
            progress.step(f"levels.fyi: {slug}")
            r = self.http.get(url + ".md")
            data = parse(r.text) if r.status_code == 200 else None
        except FetchError as e:
            log.warning("levels.fyi %s: %s — using the cache", slug, e)
            return entry
        if data is None:
            log.warning("levels.fyi %s: could not parse the page (HTTP %s) — using the cache", slug, r.status_code)
            return entry
        self.cache[slug] = {**data, "url": url, "fetched": date.today().isoformat()}
        return self.cache[slug]

    def save(self) -> None:
        CACHE_FILE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")

    def _eur_month(self, value: float | None, currency: str) -> float | None:
        rate = self.rates.get(currency)
        return None if value is None or rate is None else value * rate / 12

    def describe(self, location: str, reloc_country: str = "") -> tuple[str, str]:
        """(line, link): "levels.fyi, Cyprus: median ≈ €5.9K/mo, 25–75%: €4.6–7.2K (137 salaries)"."""
        slug, label = location_slug(location, reloc_country)
        data = self.get(slug) if slug else None
        if not data:
            return "", ""
        median, low, high = (self._eur_month(data[k], data["currency"]) for k in ("median", "p25", "p75"))
        if median is None:
            return f"levels.fyi, {label}: {data['currency']} {data['median']:,.0f}/yr", data["url"]
        k = lambda v: f"{v / 1000:.1f}K"
        spread = f", 25–75%: €{k(low)}–{k(high)}" if low and high else ""
        return f"levels.fyi, {label}: median ≈ €{k(median)}/mo{spread} ({data['samples']} salaries)", data["url"]
