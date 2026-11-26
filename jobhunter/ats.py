"""Public JSON APIs of the ATS platforms + finding and caching company slugs."""
import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from . import config, settings
from .companies import slug_candidates
from .fsutil import atomic_write_text
from .http import FetchError, HttpClient
from .models import Company, Job, html_to_text, parse_date

log = logging.getLogger(__name__)


def _greenhouse(data: Any, company: str) -> Iterator[Job]:
    for j in data.get("jobs", []):
        yield Job(
            company=company,
            title=j.get("title", ""),
            location=(j.get("location") or {}).get("name") or "",
            url=j.get("absolute_url", ""),
            posted=parse_date(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(j.get("content")),
            source="greenhouse",
        )


def _lever(data: Any, company: str) -> Iterator[Job]:
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location") or ""]
        parts = [j.get("descriptionPlain") or ""]
        parts += [f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in j.get("lists") or []]
        parts.append(j.get("additionalPlain") or "")
        yield Job(
            company=company,
            title=j.get("text", ""),
            location="; ".join(filter(None, locations)),
            url=j.get("hostedUrl", ""),
            posted=parse_date(j.get("createdAt")),
            description="\n".join(parts),
            remote=(j.get("workplaceType") == "remote") or None,
            employment=cats.get("commitment") or "",
            source="lever",
        )


def _ashby(data: Any, company: str) -> Iterator[Job]:
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locations = [j.get("location") or ""]
        locations += [s.get("location", "") for s in j.get("secondaryLocations") or []]
        yield Job(
            company=company,
            title=j.get("title", ""),
            location="; ".join(filter(None, locations)),
            url=j.get("jobUrl", ""),
            posted=parse_date(j.get("publishedAt")),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            # isRemote is true for Hybrid too — workplaceType is more precise
            remote=(j["workplaceType"] == "Remote") if j.get("workplaceType") else j.get("isRemote"),
            employment=j.get("employmentType") or "",
            source="ashby",
        )


def _workable(data: Any, company: str) -> Iterator[Job]:
    # Workable returns a vacancy with several countries as a separate record per country (same url): merge them,
    # otherwise only the first country stays (while the fitting one may be the fifth record) and the vacancy is dropped
    merged: dict[str, dict[str, Any]] = {}
    for j in data.get("jobs", []):
        key = j.get("url") or j.get("shortlink") or j.get("shortcode") or j.get("title", "")
        place = ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))
        if key in merged:
            m = merged[key]
            m["places"].append(place)
            m["remote"] = m["remote"] or bool(j.get("telecommuting"))
        else:
            merged[key] = {"job": j, "places": [place], "remote": bool(j.get("telecommuting"))}
    for m in merged.values():
        j = m["job"]
        place = "; ".join(dict.fromkeys(filter(None, m["places"])))
        if m["remote"] and len(m["places"]) > 1:
            place += " (remote)"
        yield Job(
            company=company,
            title=j.get("title", ""),
            location=place,
            url=j.get("url") or j.get("shortlink") or "",
            posted=parse_date(j.get("published_on") or j.get("created_at")),
            description=html_to_text(j.get("description")),
            remote=m["remote"] or j.get("telecommuting"),
            employment=j.get("employment_type") or "",
            source="workable",
        )


def _teamtailor(xml_text: str, company: str) -> Iterator[Job]:
    """Public Teamtailor RSS feed: {slug}.teamtailor.com/jobs.rss"""
    for item in ET.fromstring(xml_text).iter("item"):
        places: list[str] = []
        for loc in item.findall(".//{*}location"):
            city, country = loc.findtext("{*}city") or "", loc.findtext("{*}country") or ""
            places.append(", ".join(filter(None, [city, country])) or loc.findtext("{*}name") or "")
        status = (item.findtext("remoteStatus") or "").lower()
        location = "; ".join(dict.fromkeys(filter(None, places)))
        if status in ("hybrid", "fully"):
            location += " (hybrid)" if status == "hybrid" else " (remote)"
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            posted = None
        yield Job(
            company=company,
            title=item.findtext("title") or "",
            location=location,
            url=item.findtext("link") or "",
            posted=posted,
            description=html_to_text(item.findtext("description")),
            remote=(status == "fully") or None,
            source="teamtailor",
        )


def _recruitee(data: Any, company: str) -> Iterator[Job]:
    """Recruitee: {slug}.recruitee.com/api/offers/ — the full text is in the response."""
    for j in data.get("offers", []):
        place = ", ".join(filter(None, [j.get("city"), j.get("country")])) or j.get("location") or ""
        remote = bool(j.get("remote"))
        yield Job(
            company=company,
            title=j.get("title", ""),
            location=f"{place} (remote)" if remote and "remote" not in place.lower() else place,
            url=j.get("careers_url") or j.get("careers_apply_url") or "",
            posted=parse_date(j.get("published_at") or j.get("created_at")),
            description=html_to_text((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
            remote=remote or None,
            employment=j.get("employment_type_code") or "",
            source="recruitee",
        )


def _bamboohr(data: Any, company: str, slug: str = "") -> Iterator[Job]:
    """BambooHR: a list without text; the description is requested for fitting titles."""
    for j in data.get("result", []):
        loc = j.get("atsLocation") or j.get("location") or {}
        place = ", ".join(filter(None, [loc.get("city"), loc.get("state"), loc.get("country")]))
        status = j.get("employmentStatusLabel") or ""
        remote = bool(j.get("isRemote")) or "remote" in status.lower()
        yield Job(
            company=company,
            title=j.get("jobOpeningName", ""),
            location=(place + " (remote)").strip() if remote else place,
            url=f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
            description="",
            remote=remote or None,
            employment=status,
            source="bamboohr",
        )


def _breezy(data: Any, company: str) -> Iterator[Job]:
    """Breezy: {slug}.breezy.hr/json — a list without text; the description comes from the vacancy page."""
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        country = (loc.get("country") or {}).get("name") if isinstance(loc.get("country"), dict) else loc.get("country")
        place = loc.get("name") or ", ".join(filter(None, [loc.get("city"), country]))
        remote = bool(loc.get("is_remote"))
        yield Job(
            company=company,
            title=j.get("name", ""),
            location=f"{place} (remote)" if remote and "remote" not in (place or "").lower() else (place or ""),
            url=j.get("url", ""),
            posted=parse_date(j.get("published_date")),
            description="",
            remote=remote or None,
            employment=(j.get("type") or {}).get("name", "") if isinstance(j.get("type"), dict) else "",
            source="breezy",
        )


def _rippling(data: Any, company: str) -> Iterator[Job]:
    """Rippling ATS: a list without text, the vacancy repeats for every location (same uuid) — merged.
    Text and work format come from the vacancy card (_fill_details)."""
    merged: dict[str, dict[str, Any]] = {}
    for j in data if isinstance(data, list) else []:
        place = (j.get("workLocation") or {}).get("label") or ""
        m = merged.setdefault(j.get("uuid") or j.get("url") or j.get("name", ""), {"job": j, "places": []})
        m["places"].append(place)
    for m in merged.values():
        j = m["job"]
        location = "; ".join(dict.fromkeys(filter(None, m["places"])))
        yield Job(
            company=company,
            title=(j.get("name") or "").strip(),
            location=location,
            url=j.get("url") or "",
            remote=True if re.search(r"remote", location, re.I) else None,
            source="rippling",
        )


# platform -> (url, params, parser)
PLATFORMS: dict[str, tuple[str, dict[str, str] | None, Callable[..., Iterable[Job]]]] = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"}, _greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}", {"mode": "json"}, _lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", None, _ashby),
    "workable": ("https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"}, _workable),
    "teamtailor": ("https://{slug}.teamtailor.com/jobs.rss", None, _teamtailor),
    "recruitee": ("https://{slug}.recruitee.com/api/offers/", None, _recruitee),
    "bamboohr": ("https://{slug}.bamboohr.com/careers/list", None, _bamboohr),
    "breezy": ("https://{slug}.breezy.hr/json", None, _breezy),
    "rippling": ("https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs", None, _rippling),
}
DETAIL_FETCH_LIMIT = 8  # how many vacancy pages to open per company (BambooHR, Breezy, Rippling)
# Teamtailor has a separate host for every company, so the slug is not guessed automatically:
# such boards are added to ats_mapping.json by hand.
DISCOVERABLE = ("greenhouse", "lever", "ashby", "workable")


# answer of a closed board: access denied / deleted, or an HTML page instead of JSON (BambooHR, Teamtailor)
_BOARD_CLOSED_RX = re.compile(r"HTTP (?:401|403|410)\b|(?:bamboohr|teamtailor)/\S+: could not parse")


class AtsClient:
    def __init__(self, http: HttpClient, mapping_path: Path = config.MAPPING_FILE) -> None:
        self.http = http
        self.path = mapping_path
        self.lock = threading.Lock()
        self.mapping: dict[str, dict[str, Any]] = {}
        self.failures: dict[str, str] = {}  # company -> why there are no vacancies today
        try:
            self.mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except ValueError:
            log.warning("%s is broken — company ATS will be detected again", mapping_path)
        self._loaded = json.loads(json.dumps(self.mapping))  # state at start — for saving by merge

    def save(self) -> None:
        """By merge: only entries changed by this process go to the file. Edits made in
        ats_mapping.json while the run was going (by hand or by another script) are not overwritten."""
        with self.lock:
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                current = {}  # a broken file gets overwritten with a whole one
            for name, entry in self.mapping.items():
                if self._loaded.get(name) != entry:
                    current[name] = entry
            self._loaded = json.loads(json.dumps(self.mapping))
            text = json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write_text(self.path, text)

    def _fetch(self, platform: str, slug: str, company: Company) -> list[Job] | None:
        """-> list of Job, or None if there is no such board (404)."""
        url, params, parser = PLATFORMS[platform]
        if platform == "teamtailor" and "." in slug:  # own domain: careers.example.com
            url = "https://{slug}/jobs.rss"
        r = self.http.get(url.format(slug=slug), params=params)
        if r.status_code == 404 and platform == "lever":
            # European Lever accounts (jobs.eu.lever.co) live on a separate host
            r = self.http.get(f"https://api.eu.lever.co/v0/postings/{slug}", params=params)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise FetchError(f"{platform}/{slug}: HTTP {r.status_code}")
        try:
            data = r.text if platform == "teamtailor" else r.json()
            jobs = list(parser(data, company.name, slug) if platform == "bamboohr" else parser(data, company.name))
        except (ValueError, AttributeError, TypeError, ET.ParseError) as e:
            raise FetchError(f"{platform}/{slug}: could not parse the response ({e})")
        for job in jobs:
            job.score, job.note = company.score, company.note
        if platform in ("bamboohr", "breezy", "rippling"):
            self._fill_details(platform, slug, jobs)
        return jobs

    def _fill_details(self, platform: str, slug: str, jobs: list[Job]) -> None:
        """Vacancy text for BambooHR/Breezy/Rippling — only for fitting titles, up to the limit."""
        from . import filters  # filters does not import ats, but the import stays next to its use

        candidates = [j for j in jobs if filters.title_candidate(j.title)][:DETAIL_FETCH_LIMIT]
        for job in candidates:
            try:
                if platform == "bamboohr":
                    r = self.http.get(f"https://{slug}.bamboohr.com/careers/{job.url.rsplit('/', 1)[-1]}/detail")
                    opening = (r.json().get("result") or {}).get("jobOpening") or {} if r.status_code == 200 else {}
                    job.description = html_to_text(opening.get("description"))
                    job.posted = job.posted or parse_date(opening.get("datePosted"))
                elif platform == "rippling":
                    uid = job.url.rstrip("/").rsplit("/", 1)[-1]
                    r = self.http.get(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uid}")
                    card = r.json() if r.status_code == 200 else {}
                    desc = card.get("description") or {}
                    job.description = html_to_text(" ".join(str(v) for v in desc.values()) if isinstance(desc, dict) else desc)
                    places = [p for p in card.get("workLocations") or [] if isinstance(p, str)]
                    if places:
                        job.location = "; ".join(dict.fromkeys(places))
                        job.remote = True if re.search(r"remote", job.location, re.I) else job.remote
                    job.employment = ((card.get("employmentType") or {}).get("id") or "") if isinstance(card.get("employmentType"), dict) else ""
                    job.posted = job.posted or parse_date(card.get("createdOn"))
                else:
                    r = self.http.get(job.url)
                    m = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', r.text, re.S) if r.status_code == 200 else None
                    posting = json.loads(m.group(1)) if m else {}
                    job.description = html_to_text(posting.get("description")) if isinstance(posting, dict) else ""
            except (FetchError, ValueError) as e:
                log.warning("%s: could not get the vacancy text %s (%s)", job.company, job.url, e)

    def _discover(self, company: Company) -> tuple[str | None, str | None, list[Job], bool]:
        """Tries platform × slug until the first board with vacancies.

        An empty board does not count as a find: Workable has such "blanks"
        for almost any name, and they would hide the real board forever.
        """
        empty_hit: str | None = None
        had_errors = False
        for slug in slug_candidates(company.name):
            for platform in DISCOVERABLE:
                try:
                    jobs = self._fetch(platform, slug, company)
                except FetchError as e:
                    had_errors = True
                    log.warning("%s: %s", company.name, e)
                    continue
                if jobs:
                    return platform, slug, jobs, had_errors
                if jobs is not None and empty_hit is None:
                    empty_hit = f"{platform}/{slug}"
        return None, empty_hit, [], had_errors

    def _board_gone(self, company: Company, board: str) -> list[Job]:
        """The board is gone: search once more and write ONE warning (moved / no board),
        so it does not repeat in every run. The next search is in RECHECK_NOT_FOUND_DAYS."""
        self.failures[company.name] = f"board {board} is gone"
        jobs = self.jobs_for(company, rediscover=True, quiet=True)
        new = self.mapping.get(company.name) or {}
        found = f"{new.get('platform')}/{new.get('slug')}" if new.get("platform") else ""
        if found and found != board:
            # fixed by itself — not a warning, but an entry in the run feed
            from . import progress
            log.info("%s: board %s is gone — moved to %s (%d vacancies)", company.name, board, found, len(jobs))
            progress.event("source", f"{company.name}: board moved {board} → {found}")
        elif not found:
            with self.lock:
                self.mapping[company.name] = {"platform": None, "checked_at": date.today().isoformat(), "gone": board}
            log.warning("%s: board %s is gone, no new one found — no board, next check in %d days",
                        company.name, board, config.RECHECK_NOT_FOUND_DAYS)
        return jobs or []

    def jobs_for(self, company: Company, rediscover: bool = False, quiet: bool = False) -> list[Job]:
        with self.lock:
            entry = None if rediscover else self.mapping.get(company.name)
        token = settings.get().ats_overrides.get(company.name, settings.AtsOverride()).greenhouse_board_token
        if token:  # set by hand in settings.toml
            entry = {"platform": "greenhouse", "slug": token.strip()}
        today = date.today()
        if entry and entry.get("platform") == "custom":
            return []  # the company has its own site — jobhunter/sites.py reads it

        if entry and entry.get("platform"):
            board = f"{entry['platform']}/{entry['slug']}"
            try:
                jobs = self._fetch(entry["platform"], entry["slug"], company)
            except FetchError as e:
                if not _BOARD_CLOSED_RX.search(str(e)):
                    log.warning("%s: %s", company.name, e)
                    self.failures[company.name] = f"request error: {e}"
                    return []
                jobs = None  # 401/403/410 or HTML instead of data (BambooHR, Teamtailor) — the board is closed
            if jobs is None:
                return self._board_gone(company, board)
            return jobs

        if entry and entry.get("manual"):
            return []  # marked by hand as "own page / check by hand" — the slug is not guessed
        checked = entry and (entry.get("checked_at") or entry.get("found_at"))
        if checked and (today - date.fromisoformat(checked)).days < config.RECHECK_NOT_FOUND_DAYS:
            return []

        platform, slug, jobs, had_errors = self._discover(company)
        if platform:
            log.info("%s -> %s/%s (%d vacancies)", company.name, platform, slug, len(jobs))
            new_entry: dict[str, Any] = {"platform": platform, "slug": slug, "found_at": today.isoformat()}
        elif had_errors:
            return []  # network failure — "not found" is not cached, try again tomorrow
        else:
            new_entry = {"platform": None, "checked_at": today.isoformat()}
            if slug:
                new_entry["empty_board"] = slug
            log.log(
                logging.INFO if quiet else logging.WARNING,
                "%s: slug not found on any ATS%s — skipped",
                company.name, f" (only an empty board {slug})" if slug else "",
            )
        with self.lock:
            self.mapping[company.name] = new_entry
        return jobs
