"""freehire.me — the main vacancy source (public JSON API without a key).

Docs: https://freehire.me/docs/api. Queries and common parameters live in
settings.toml ([freehire]). Limits kept here:

* at most 1 request per second (the shared Http client);
* responses are cached on disk (state/freehire_cache) for cache_hours hours;
* at most max_requests network requests per run;
* meta.ignored_params is checked in every response — if the API dropped a parameter,
  the run stops: otherwise a "wide" result would look like a normal one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config as c
from . import filters, geo, language, progress
from .http import FetchError, HttpClient
from .models import Job, html_to_text, parse_date

log = logging.getLogger(__name__)

BASE_URL = "https://freehire.me/api/v1"
CACHE_DIR = c.ROOT / "state" / "freehire_cache"
SAVED_FILE = c.ROOT / "state" / "freehire_jobs.json"

# freehire categories that count as engineering roles for the profile
ENGINEERING_CATEGORIES = {
    "backend", "software_engineering", "ai_engineering", "ml_ai", "fullstack",
    "architecture", "data_engineering",
}
ENRICHMENT_FIELDS = (
    "seniority", "category", "relocation", "visa_sponsorship", "experience_years_min",
    "english_level", "company_type", "employment_type",
    "salary_min", "salary_max", "salary_currency", "salary_period",
)
COUNTRY_BY_CODE = {
    "nl": "Netherlands", "es": "Spain", "de": "Germany", "pt": "Portugal", "cy": "Cyprus", "ge": "Georgia",
}


class IgnoredParamsError(Exception):
    """The API dropped one of the query parameters — the results cannot be treated as filtered."""


class BudgetExceeded(FetchError):
    """The limit of network requests to freehire per run is used up."""


def _clean_url(url: str) -> str:
    """Link to the original without the utm tags freehire adds."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _salary(enrichment: dict) -> str:
    low, high = enrichment.get("salary_min"), enrichment.get("salary_max")
    if not low and not high:
        return ""
    amount = f"{low:,}–{high:,}" if low and high and low != high else f"{low or high:,}"
    return f"{enrichment.get('salary_currency') or ''} {amount} / {enrichment.get('salary_period') or 'year'}".strip()


def to_job(raw: dict, query: str) -> Job:
    """API record -> Job; the raw freehire fields are kept in job.extra."""
    enrichment = raw.get("enrichment") or {}
    skills = raw.get("skills") or []
    countries = [x.lower() for x in raw.get("countries") or []]
    extra = {
        "public_slug": raw.get("public_slug"),
        "title": raw.get("title"),
        "company": raw.get("company"),
        "company_slug": raw.get("company_slug"),
        "location": raw.get("location"),
        "countries": countries,
        "regions": raw.get("regions") or [],
        "work_mode": raw.get("work_mode"),
        "skills": skills,
        "collections": raw.get("collections") or [],
        "url": raw.get("url"),
        "posted_at": raw.get("posted_at"),
        "created_at": raw.get("created_at"),
        "enrichment": {k: enrichment.get(k) for k in ENRICHMENT_FIELDS},
        "reality_class": (raw.get("reality") or {}).get("class"),
        "queries": [query],
        "full_description": len(raw.get("description") or "") > 1200,  # the search preview is cut to ~1000
    }
    location = raw.get("location") or ", ".join(x.upper() for x in countries)
    if raw.get("work_mode") in ("remote", "hybrid") and raw["work_mode"] not in location.lower():
        location = f"{location} ({raw['work_mode']})"
    return Job(
        company=raw.get("company") or "",
        title=raw.get("title") or "",
        location=location,
        url=_clean_url(raw.get("url") or ""),
        posted=parse_date(raw.get("posted_at") or raw.get("created_at")),
        # the search cuts the description — skills from the catalogue are appended so scoring sees them
        description=html_to_text(raw.get("description")) + "\nSkills: " + ", ".join(skills),
        remote=(raw.get("work_mode") == "remote") or None,
        employment=str(enrichment.get("employment_type") or ""),
        salary=_salary(enrichment),
        source="freehire",
        sources=["freehire"],
        extra=extra,
    )


def prefilter(job: Job, max_experience_years: int = 7) -> bool:
    """Cheap checks before requesting the full text. The reject reason stays in job.reject."""
    enrichment = job.extra["enrichment"]
    category = enrichment.get("category")
    engineering = category in ENGINEERING_CATEGORIES or (category is None and filters.title_candidate(job.title))
    if not engineering or c.ROLE_EXCLUDE_RX.search(job.title) or c.TITLE_EXCLUDE_RX.search(job.title):
        return False  # wrong role — not shown in "Rejected"
    experience = enrichment.get("experience_years_min") or 0
    if experience > max_experience_years:
        return filters._reject(job, f"experience from {experience} years (freehire field)")
    if not filters.full_time(job.title, job.employment, job.description):
        return filters._reject(job, "not full-time: part-time / freelance / hourly or piecework pay")
    return True


def prepare(job: Job) -> bool:
    """Location labelling and the text check. False — the vacancy is not taken (see job.reject)."""
    from . import verify

    extra = job.extra
    enrichment = extra["enrichment"]
    home = c.HOME_ISO.lower()  # a vacancy in the home country fits in any format
    force_ok = (bool(home) and home in extra["countries"]) or (bool(job.remote) and "global" in extra["regions"])
    verdict = "ok" if force_ok else filters.location_verdict(job)
    job.reloc_country = job.reloc_type = job.reloc_quote = ""
    if verdict != "ok":
        in_target = geo.RELOCATION_ENABLED and not filters.no_support(job.description) and geo.classify(job, bool(job.remote))
        supported = enrichment.get("relocation") == "supported" or enrichment.get("visa_sponsorship") is True
        if geo.RELOCATION_ENABLED and supported and geo.role_fits(job):
            country = (
                job.reloc_country
                or geo.detect_country(job.location, job.description)
                or next((COUNTRY_BY_CODE[x] for x in extra["countries"] if x in COUNTRY_BY_CODE
                         and COUNTRY_BY_CODE[x] in geo.COUNTRIES), "")
            )
            if country:
                job.reloc_country, job.reloc_type = country, geo.RELOCATION
                job.reloc_quote = job.reloc_quote or "freehire: relocation / visa supported"
                in_target = True
        if not in_target and (verdict is None or not geo.COUNTRY_BOUND_REMOTE):
            return filters._reject(job, "location by the source label: not remote and not the home country", job.location[:80])
    job.location_check = verdict == "check"
    verify.check(job, job.description, bool(extra.get("full_description")))
    if job.reject:
        return False
    job.lang_marks, job.lang_required = language.analyze(job.title, job.description)
    job.mismatch_lang = filters.language_mismatch(job)
    job.language_mismatch_warning = bool(job.mismatch_lang)
    return True


class Freehire:
    """freehire client with a disk cache and a per-run request limit."""

    def __init__(self, http: HttpClient, settings: dict) -> None:
        """settings — the [freehire] section of settings.toml."""
        self.http = http
        self.settings = settings
        self.max_requests = int(settings.get("max_requests", 20))
        self.cache_seconds = float(settings.get("cache_hours", 6)) * 3600
        self.requests = 0  # network requests per run
        self.cache_hits = 0
        self.totals: dict[str, int] = {}  # query -> meta.total
        self.query_params: dict[str, dict] = {}  # query -> parameters (for diagnostics)
        self.diagnosis: dict[str, str] = {}  # query with zero results -> what makes it zero

    def _get(self, path: str, params: dict) -> dict:
        """API response (from the cache or the network); checks the budget and meta.ignored_params."""
        key = hashlib.sha1(json.dumps([path, sorted(params.items())]).encode()).hexdigest()
        cache_file = CACHE_DIR / f"{key}.json"
        body = None
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - cached["fetched_at"] < self.cache_seconds:
                body = cached["body"]
                self.cache_hits += 1
        if body is None:
            if self.requests >= self.max_requests:
                raise BudgetExceeded(f"freehire: the limit of {self.max_requests} requests per run is used up")
            self.requests += 1
            r = self.http.get(BASE_URL + path, params=params)
            if r.status_code != 200:
                raise FetchError(f"freehire {path}: HTTP {r.status_code} {r.text[:200]}")
            try:
                body = r.json()
            except ValueError as e:
                raise FetchError(f"freehire {path}: not JSON ({e})")
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"fetched_at": time.time(), "body": body}), encoding="utf-8")
        ignored = (body.get("meta") or {}).get("ignored_params")
        if ignored:
            cache_file.unlink(missing_ok=True)  # such a response must not be cached
            raise IgnoredParamsError(
                f"freehire dropped query parameters {path}: {json.dumps(ignored, ensure_ascii=False)}. "
                f"The filter was not applied, the result is wider than intended — fix [freehire] in settings.toml."
            )
        return body

    def collect(self) -> list[Job]:
        """All queries from settings.toml with paging; duplicates between queries are merged."""
        common = {k: str(v) for k, v in (self.settings.get("common") or {}).items()}
        pages = int(self.settings.get("pages_per_query", 3))
        jobs: dict[str, Job] = {}
        try:
            for query in self.settings.get("queries", []):
                name = query["name"]
                if query.get("requires_relocation") and not geo.RELOCATION_ENABLED:
                    continue
                if progress.skip_requested():  # the "Skip" button in the web UI
                    break
                skip = ("name", "title", "requires_relocation")
                params = {**common, **{k: str(v) for k, v in query.items() if k not in skip}}
                params = {k: v for k, v in params.items() if v != ""}
                self.query_params[name] = dict(params)
                # /agent/jobs/search — the same search, but with the full vacancy text
                params.update(limit="100", sort="created_at", order="desc", description_format="text")
                for page in range(pages):
                    if progress.skip_requested():
                        break
                    progress.step(f"{name}, page {page + 1}")
                    body = self._get("/agent/jobs/search", {**params, "offset": str(page * 100)})
                    total = int((body.get("meta") or {}).get("total", 0))
                    self.totals[name] = total
                    for raw in body.get("data", []):
                        slug = raw.get("public_slug")
                        if slug in jobs:
                            jobs[slug].extra["queries"].append(name)
                        else:
                            jobs[slug] = to_job(raw, name)
                    if (page + 1) * 100 >= total:
                        break
        except FetchError as e:  # network / limit — work with what was received
            log.warning("%s", e)
        result = list(jobs.values())
        SAVED_FILE.parent.mkdir(exist_ok=True)
        SAVED_FILE.write_text(
            json.dumps([j.extra for j in result], ensure_ascii=False, indent=1), encoding="utf-8"
        )
        log.info("freehire: vacancies %d, network requests %d, from cache %d, by query: %s",
                 len(result), self.requests, self.cache_hits, self.totals)
        return result

    def fill_descriptions(self, jobs: list[Job]) -> int:
        """Full texts for the given vacancies via /agent/jobs/search. -> how many were received."""
        wanted = {j.extra["public_slug"]: j for j in jobs if j.extra.get("public_slug")}
        slugs = sorted({j.extra["company_slug"] for j in wanted.values() if j.extra.get("company_slug")})
        filled = 0
        try:
            for i in range(0, len(slugs), 10):
                body = self._get("/agent/jobs/search", {
                    "company_slug": ",".join(slugs[i:i + 10]),
                    "skills": "python",
                    "description_format": "text",
                    "limit": "100",
                    "sort": "created_at",
                    "order": "desc",
                })
                for raw in body.get("data", []):
                    job = wanted.get(raw.get("public_slug"))
                    if job and raw.get("description"):
                        job.description = raw["description"] + "\nSkills: " + ", ".join(job.extra["skills"])
                        job.extra["full_description"] = True
                        filled += 1
        except FetchError as e:
            log.warning("%s", e)
        return filled

    def diagnose_empty(self) -> None:
        """For queries with zero results finds the filter that makes them zero.

        Parameters are added one by one (limit=1) until total becomes 0; then the values
        are checked against /jobs/facets. The result goes to self.diagnosis and the report summary.
        """
        for name, total in self.totals.items():
            if total:
                continue
            params = self.query_params.get(name, {})
            # first what the query itself sets, then the common parameters
            order = sorted(params, key=lambda k: k in (self.settings.get("common") or {}))
            active: dict[str, str] = {}
            steps, culprit, facet_key = [], "", ""
            try:
                for key in order:
                    active[key] = params[key]
                    count = int((self._get("/jobs/search", {**active, "limit": "1"}).get("meta") or {}).get("total", 0))
                    steps.append(f"+{key}={params[key]} → {count}")
                    if count == 0:
                        culprit = key
                        break
                if culprit:
                    facet_key = culprit.removesuffix("_exclude")
                    rest = {k: v for k, v in active.items() if k != culprit}
                    facets = (self._get("/jobs/facets", rest).get("data") or {}).get("facets", {})
                    available = facets.get(facet_key)
                    hint = (
                        f"; available values of {facet_key} with the other filters: "
                        + (", ".join(f"{k} ({v})" for k, v in sorted(available.items(), key=lambda x: -x[1])[:6]) or "none")
                        if isinstance(available, dict) else ""
                    )
                    self.diagnosis[name] = f"zeroed by the filter {culprit}={params[culprit]}{hint}. Steps: " + "; ".join(steps)
                else:
                    self.diagnosis[name] = "no single filter zeroes the result. Steps: " + "; ".join(steps)
            except FetchError as e:
                self.diagnosis[name] = f"diagnostics not finished ({e}). Steps: " + "; ".join(steps)
            log.warning("freehire: query %s returned 0 — %s", name, self.diagnosis[name])
