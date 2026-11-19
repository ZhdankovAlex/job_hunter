"""Career pages without a standard ATS: Aiven, Bolt, fin.dev, Gurtam, Paychex (example adapters).

These companies have no public JSON API, so the HTML of their own
pages is read (the Http client checks robots.txt, the limit is 1 request per second per host).
"""
from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator

from . import config as c, filters
from .http import FetchError, HttpClient
from .models import Job, html_to_text, parse_date

log = logging.getLogger(__name__)

_JSONLD_RX = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_NOISE_RX = re.compile(r"<(script|style|svg|nav|header|footer|noscript)\b.*?</\1>", re.S | re.I)


_HTML = {"Accept": "text/html,*/*;q=0.8"}  # some sites answer 404 to Accept: application/json


def _get(http: HttpClient, url: str) -> str:
    """Page HTML; any non-200 answer is a FetchError."""
    r = http.get(url, headers=_HTML)
    if r.status_code != 200:
        raise FetchError(f"{url}: HTTP {r.status_code}")
    return r.text


def _page_text(page: str) -> str:
    """Page text without scripts, navigation and footer."""
    return html_to_text(_NOISE_RX.sub(" ", page))


def _jsonld(page: str, kind: str) -> dict:
    """The first JSON-LD object of the page with the needed @type, or {}."""
    for raw in _JSONLD_RX.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict) and item.get("@type") == kind:
                return item
    return {}


def findev(http: HttpClient) -> Iterator[Job]:
    """fin.dev: the list is a JSON-LD ItemList, a vacancy is a JSON-LD JobPosting."""
    listing = _get(http, "https://fin.dev/career/open-positions/")
    urls = [e.get("url") for e in _jsonld(listing, "ItemList").get("itemListElement", [])]
    for url in filter(None, urls):
        posting = _jsonld(_get(http, url), "JobPosting")
        if not posting:
            log.warning("fin.dev: no JobPosting on the page %s", url)
            continue
        countries = [c.get("name", "") for c in posting.get("applicantLocationRequirements") or []]
        remote = posting.get("jobLocationType") == "TELECOMMUTE"
        yield Job(
            company="fin.dev",
            title=posting.get("title", ""),
            location=("Remote: " if remote else "") + ", ".join(filter(None, countries)),
            url=url,
            posted=parse_date(posting.get("datePosted")),
            description=html_to_text(posting.get("description")),
            remote=remote or None,
            employment=str(posting.get("employmentType") or ""),
            source="fin.dev",
        )


def gurtam(http: HttpClient) -> Iterator[Job]:
    """Gurtam: vacancy cards on /career, the text comes from the vacancy page."""
    listing = _get(http, "https://gurtam.com/career")
    cards = re.findall(r'<a\s+href="(/career/[^"#?]+)"([^>]*vacancy-card[^>]*)>(.*?)</a>', listing, re.S)
    for path, attrs, body in cards:
        m = re.search(r"<h3[^>]*>(.*?)</h3>", body, re.S)
        title = html_to_text(m.group(1)) if m else ""
        place = re.search(r'data-location="([^"]*)"', attrs)
        url = "https://gurtam.com" + path
        yield Job(
            company="Gurtam",
            title=title,
            location=html.unescape(place.group(1)) if place else "",
            url=url,
            description=_page_text(_get(http, url)),
            source="gurtam",
        )


def bolt(http: HttpClient) -> Iterator[Job]:
    """Bolt: a paged list; the vacancy page is read only for fitting titles."""
    seen: set[str] = set()
    for page_no in range(1, 16):
        page = _get(http, "https://bolt.eu/en/careers/positions/" + (f"?page={page_no}" if page_no > 1 else ""))
        items = page.split('data-testid="AccordionItem"')[1:]
        fresh = 0
        for chunk in items:
            chunk = re.sub(r"<(style|svg)\b.*?</\1>", "", chunk, flags=re.S)
            uid = re.search(r"/careers/positions/([0-9a-f-]{36})", chunk)
            labels = [html.unescape(t).strip() for t in re.findall(r">([^<>]{2,80})</(?:span|div|p)>", chunk)]
            if not uid or not labels or uid.group(1) in seen:
                continue
            seen.add(uid.group(1))
            fresh += 1
            title, location = labels[0], labels[2] if len(labels) > 2 else ""
            if not filters.title_candidate(title):
                continue
            url = f"https://bolt.eu/en/careers/positions/{uid.group(1)}/"
            text = _page_text(_get(http, url))
            workplace = re.search(r"WORKPLACE\s+([A-Za-z-]+)", text)
            kind = re.search(r"TYPE\s+([A-Za-z -]+?)\s*(?:\n|View all)", text)
            if workplace and workplace.group(1).lower() != "on-site":
                location = f"{location} ({workplace.group(1)})"
            yield Job(
                company="Bolt",
                title=title,
                location=location,
                url=url,
                description=text,
                remote=bool(workplace and workplace.group(1).lower() == "remote") or None,
                employment=kind.group(1) if kind else "",
                source="bolt",
            )
        if not fresh:
            break


def _turbo_stream(raw: str):
    """A React Router loader response (`*.data`, turbo-stream format): a flat array with references -> an object.

    An object {"_<i>": <j>}: the key is the string arr[i], the value is arr[j]; a list holds element indexes;
    negative indexes are special values (undefined, null…), here they are None.
    """
    arr = json.loads(raw.splitlines()[0])
    memo: dict[int, object] = {}

    def node(i):
        if not isinstance(i, int) or i < 0:
            return None
        if i not in memo:
            v = arr[i]
            if isinstance(v, list):
                memo[i] = [node(x) for x in v]
            elif isinstance(v, dict):
                memo[i] = {arr[int(k[1:])]: node(x) for k, x in v.items() if k.startswith("_")}
            else:
                memo[i] = v
        return memo[i]

    return node(0)


def aiven(http: HttpClient) -> Iterator[Job]:
    """Aiven: the site draws vacancies with a script, but the page data comes from the loader /careers/job.data
    (the public Greenhouse boards under their slug are closed). Text — from the page careers.aiven.io/openings/<id>
    (its /api/ is closed in robots.txt, vacancy pages are not); opened only for fitting titles."""
    r = http.get("https://aiven.io/careers/job.data")
    if r.status_code != 200:
        raise FetchError(f"aiven.io/careers/job.data: HTTP {r.status_code}")
    try:
        data = _turbo_stream(r.text)["routes/careers/job"]["data"]
    except (ValueError, KeyError, TypeError, IndexError) as e:
        raise FetchError(f"aiven.io: job.data format changed ({e})") from e
    if data.get("jobsUnavailable"):
        raise FetchError("aiven.io: vacancy list unavailable (jobsUnavailable)")
    for item in data.get("jobs") or []:
        title, url = item.get("title") or "", item.get("url") or ""
        if not url or not filters.title_candidate(title):
            continue
        location = item.get("location") or ""
        yield Job(
            company="Aiven",
            title=title,
            location=location,
            url=url,
            description=_page_text(_get(http, url)),
            remote=bool(re.search(r"remote", location, re.I)) or None,
            source="aiven",
        )


def paychex(http: HttpClient) -> Iterator[Job]:
    """Paychex: a career site on iCIMS Jibe, an open JSON /api/jobs with the vacancy text (robots.txt: crawl-delay 5).
    There are few vacancies (~60, mostly sales in the US) — all are read, the filters drop role and location."""
    seen, page = 0, 1
    while page <= 5:
        r = http.get("https://careers.paychex.com/api/jobs", params={"page": page, "limit": 100})
        if r.status_code != 200:
            raise FetchError(f"careers.paychex.com/api/jobs: HTTP {r.status_code}")
        data = r.json()
        items = data.get("jobs") or []
        for item in items:
            j = item.get("data") or {}
            if not filters.title_candidate(j.get("title") or ""):
                continue
            text = " ".join(html_to_text(j.get(k)) for k in ("description", "responsibilities", "qualifications") if j.get(k))
            yield Job(
                company="Paychex",
                title=j.get("title") or "",
                location=j.get("full_location") or ", ".join(filter(None, [j.get("city"), j.get("country")])),
                url=f"https://careers.paychex.com/careers/jobs/{j.get('req_id')}?lang=en-us",
                posted=parse_date(j.get("posted_date")),
                description=text,
                employment=(j.get("employment_type") or "").replace("_", " ").lower(),
                source="paychex",
            )
        seen += len(items)
        if not items or seen >= int(data.get("totalCount") or 0):
            break
        page += 1


SOURCES = {"aiven": aiven, "bolt": bolt, "fin.dev": findev, "gurtam": gurtam, "paychex": paychex}


def enabled_sources() -> list[str]:
    """Sources from SOURCES minus those turned off in settings.toml [sources].disabled."""
    from . import geo

    disabled = {n.lower() for n in geo.S.get("sources", {}).get("disabled", [])}
    return [name for name in SOURCES if name.lower() not in disabled]


def fetch(name: str, http: HttpClient) -> list[Job]:
    """Collects what it can; on an error returns what it already has and writes to errors.log."""
    jobs: list[Job] = []
    try:
        for job in SOURCES[name](http):
            jobs.append(job)
    except FetchError as e:
        log.warning("site %s: %s", name, e)
    return jobs


# --- Job boards: Remote Rocketship, Brenxor, justjoin.it, remote.com, Cryptocurrency Jobs ---

_NEXT_DATA_RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NEXT_F_RX = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


def _boards() -> dict:
    """The [boards] section of settings.toml."""
    from . import geo  # imported here: geo reads settings.toml on import

    return geo.S.get("boards", {})


def _next_payload(page: str) -> str:
    """The joined Next.js data stream (self.__next_f.push) of the page."""
    parts = _NEXT_F_RX.findall(page)
    try:
        return json.loads('"' + "".join(parts) + '"') if parts else ""
    except ValueError:
        return ""


def _array_after(payload: str, marker: str) -> list:
    """The JSON array that starts right after marker in the data stream."""
    start = payload.find(marker)
    if start < 0:
        return []
    try:
        return json.JSONDecoder().raw_decode(payload[payload.find("[", start):])[0]
    except ValueError:
        return []


def remoterocketship(http: HttpClient) -> Iterator[Job]:
    """Remote Rocketship: vacancies are in __NEXT_DATA__ of the search page; url is the original in the ATS."""
    cfg = _boards()
    base = cfg.get("remoterocketship_url", "")
    if not base:
        return
    for page_no in range(1, int(cfg.get("remoterocketship_pages", 5)) + 1):
        page = _get(http, f"{base}&page={page_no}")
        m = _NEXT_DATA_RX.search(page)
        if not m:
            raise FetchError("remoterocketship: no __NEXT_DATA__ on the page (the page format changed)")
        openings = json.loads(m.group(1))["props"]["pageProps"].get("initialJobOpenings") or []
        if not openings:
            break
        for j in openings:
            kind = (j.get("locationType") or "").lower()
            countries = j.get("locationCountries") or []
            place = ", ".join(filter(None, [j.get("locationCity"), j.get("location")]))
            if isinstance(countries, list) and countries:
                place = "; ".join(dict.fromkeys([place, *map(str, countries)]))
            salary = j.get("salaryRange")
            yield Job(
                company=(j.get("company") or {}).get("name") or "",
                title=j.get("roleTitle") or "",
                location=f"{place} ({kind})" if kind in ("remote", "hybrid") else place,
                url=j.get("url") or "",
                posted=parse_date(j.get("created_at")),
                description="\n".join(filter(None, [
                    j.get("twoLineJobDescriptionSummary") or j.get("jobDescriptionSummary"),
                    html_to_text(j.get("roleDescription")),
                    html_to_text(j.get("roleRequirements")),
                    "Skills: " + ", ".join(j.get("techStack") or []),
                ])),
                remote=(kind == "remote") or None,
                employment=j.get("employmentType") or "",
                salary=salary if isinstance(salary, str) else "",
                source="remoterocketship",
            )


def brenxor(http: HttpClient) -> Iterator[Job]:
    """Brenxor: the list is cards, the full text is a JSON-LD JobPosting on the vacancy page."""
    cfg = _boards()
    base = cfg.get("brenxor_url", "")
    if not base:
        return
    budget = int(cfg.get("brenxor_max_details", 40))
    seen: set[str] = set()
    for page_no in range(1, int(cfg.get("brenxor_pages", 4)) + 1):
        r = http.get(base + (f"?page={page_no}" if page_no > 1 else ""))
        if r.status_code == 404 and page_no > 1:
            break  # no more list pages
        if r.status_code != 200:
            raise FetchError(f"brenxor: HTTP {r.status_code}")
        cards = r.text.split('<a class="job-listing-details"')[1:]
        fresh = 0
        for card in cards:
            link = re.search(r'href="(https://brenxor\.com/job-details-\d+[^"]*)"', card)
            title = re.search(r'<h3 class="job-listing-title">(.*?)</h3>', card, re.S)
            if not link or not title or link.group(1) in seen:
                continue
            seen.add(link.group(1))
            fresh += 1
            title_text = html_to_text(title.group(1))
            if budget <= 0 or not filters.title_candidate(title_text):
                continue
            budget -= 1
            place = re.search(r'icon-material-outline-location-on"></i>(.*?)</li>', card, re.S)
            company = re.search(r'icon-material-outline-business"></i>(.*?)</a>', card, re.S)
            posting = _jsonld(_get(http, link.group(1)), "JobPosting")
            yield Job(
                company=(posting.get("hiringOrganization") or {}).get("name")
                or (html_to_text(company.group(1)) if company else ""),
                title=posting.get("title") or title_text,
                location=re.sub(r"\s*\+\d+ regions?\s*", "; ", " ".join(html_to_text(place.group(1)).split()))
                if place else "Remote",
                url=link.group(1),
                posted=parse_date(posting.get("datePosted")),
                description=html_to_text(posting.get("description")),
                remote=True,
                employment=str(posting.get("employmentType") or ""),
                source="brenxor",
            )
        if not fresh:
            break


def justjoin(http: HttpClient) -> Iterator[Job]:
    """justjoin.it: vacancies are in the data stream of the list page (no text — only title and skills)."""
    url = _boards().get("justjoin_url", "")
    if not url:
        return
    offers = _array_after(_next_payload(_get(http, url)), '"offers":[{"applyUrl"')
    if not offers:
        raise FetchError("justjoin.it: no vacancies found on the page (the page format changed)")
    for o in offers:
        cities = list(dict.fromkeys(
            filter(None, [o.get("city"), *(m.get("city") for m in o.get("multilocation") or [])])
        ))
        kind = o.get("workplaceType") or ""
        skills = [*(o.get("requiredSkills") or []), *(o.get("niceToHaveSkills") or [])]
        yield Job(
            company=o.get("companyName") or "",
            title=o.get("title") or "",
            location=", ".join(cities) + ", Poland" + (f" ({kind})" if kind in ("remote", "hybrid") else ""),
            url=f"https://justjoin.it/job-offer/{o.get('slug')}",
            posted=parse_date(o.get("publishedAt")),
            description=(o.get("title") or "") + "\nSkills: " + ", ".join(map(str, skills)),
            remote=(kind == "remote") or None,
            employment=o.get("workingTime") or "",
            source="justjoin.it",
        )


def remotecom(http: HttpClient) -> Iterator[Job]:
    """remote.com/jobs: search by query; the list has the title, company and hiring countries."""
    cfg = _boards()
    query = cfg.get("remotecom_query", "")
    if not query:
        return
    for page_no in range(1, int(cfg.get("remotecom_pages", 3)) + 1):
        url = f"https://remote.com/jobs/all?query={query}" + (f"&page={page_no}" if page_no > 1 else "")
        jobs = _array_after(_next_payload(_get(http, url)), '"jobs":[{"status"')
        if not jobs:
            break
        for j in jobs:
            hiring = j.get("hiringLocation") or {}
            places = [(loc.get("value") or {}).get("name", "") for loc in hiring.get("includedLocations") or []]
            remote = (j.get("workplaceLocation") or {}).get("type") == "remote"
            company = j.get("companyProfile") or {}
            location = ", ".join(filter(None, places)) or ("Remote" if remote else "")
            yield Job(
                company=company.get("name") or "",
                title=j.get("title") or "",
                location=f"Remote: {location}" if remote and places else location,
                url=f"https://remote.com/jobs/{company.get('slug')}/{j.get('slug')}",
                posted=parse_date(j.get("publishedAt")),
                description=(j.get("title") or "")
                + ("\nWe offer visa sponsorship." if j.get("visaSponsorshipOffered") else ""),
                remote=remote or None,
                employment=j.get("employmentType") or "",
                source="remote.com",
            )


def cryptocurrencyjobs(http: HttpClient) -> Iterator[Job]:
    """Cryptocurrency Jobs: a common RSS feed (latest vacancies of all categories)."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    try:
        root = ET.fromstring(_get(http, "https://cryptocurrencyjobs.co/index.xml"))
    except ET.ParseError as e:
        raise FetchError(f"cryptocurrencyjobs: could not parse RSS ({e})")
    for item in root.iter("item"):
        title, _, company = (item.findtext("title") or "").rpartition(" at ")
        text = html_to_text(item.findtext("description"))
        if re.search(r"no geographical restrictions|from anywhere", text, re.I):
            location = "Remote, Worldwide"
        elif re.search(r"remote", text, re.I):
            place = re.search(r"remote (?:in|from|within|across) ([^.]{3,60})", text, re.I)
            location = f"Remote - {place.group(1).strip()}" if place else "Remote"
        else:
            location = ""
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            posted = None
        yield Job(
            company=company.strip(),
            title=title.strip() or (item.findtext("title") or ""),
            location=location,
            url=item.findtext("link") or "",
            posted=posted,
            description=text,
            remote=bool(location) or None,
            employment="part-time" if re.search(r"part-time position", text, re.I) else "",
            source="cryptocurrencyjobs",
        )



def rivian(http: HttpClient) -> Iterator[Job]:
    """Rivian: a career site on iCIMS/Jibe returns vacancies as JSON (/api/jobs), the text is in the response."""
    seen = 0
    for page_no in range(1, 11):
        r = http.get("https://careers.rivian.com/api/jobs", params={"page": page_no, "limit": 100})
        if r.status_code != 200:
            raise FetchError(f"rivian: HTTP {r.status_code}")
        data = r.json()
        items = data.get("jobs") or []
        for item in items:
            j = item.get("data", item)
            place = ", ".join(filter(None, [j.get("city"), j.get("country")]))
            yield Job(
                company="Rivian",
                title=j.get("title") or "",
                location=place,
                url=f"https://careers.rivian.com/careers-home/jobs/{j.get('slug')}",
                posted=parse_date(j.get("posted_date")),
                description="\n".join(filter(None, [
                    html_to_text(j.get("description")), html_to_text(j.get("responsibilities")),
                    html_to_text(j.get("qualifications")),
                ])),
                employment=j.get("employment_type") or "",
                source="rivian",
            )
        seen += len(items)
        if not items or seen >= int(data.get("totalCount") or 0):
            break


_HABR = "https://career.habr.com"
_SSR_RX = re.compile(r'<script type="application/json" data-ssr-state="true">(.*?)</script>', re.S)
HABR_PYTHON_SKILL = 446  # skills[]=446 — Python
HABR_MAX_PAGES = 6


def habr_career(http: HttpClient) -> Iterator[Job]:
    """Habr Career (career.habr.com): remote vacancies with the Python skill, freshest first (data — the page SSR JSON)."""
    for page_no in range(1, HABR_MAX_PAGES + 1):
        page = _get(http, f"{_HABR}/vacancies?skills[]={HABR_PYTHON_SKILL}&remote=true&type=all"
                          f"&sort=date&page={page_no}")
        m = _SSR_RX.search(page)
        if not m:
            raise FetchError("habr career: no SSR data (the page format changed)")
        data = json.loads(html.unescape(m.group(1))).get("vacancies") or {}
        for item in data.get("list") or []:
            title = item.get("title") or ""
            if item.get("archived") or not filters.title_candidate(title) or c.TG_EXCLUDE_RX.search(title):
                continue
            url = _HABR + item["href"]
            try:
                detail = _get(http, url)
            except FetchError as e:
                log.warning("habr career %s: %s", url, e)
                detail = ""
            i = detail.find("vacancy-description__text")
            body = html_to_text(detail[detail.find(">", i) + 1: detail.find("basic-section", i)]) if i != -1 else ""
            places = [x["title"] for x in item.get("locations") or []]
            skills = ", ".join(x["title"] for x in item.get("skills") or [])
            salary = (item.get("salary") or {}).get("formatted") or ""
            yield Job(
                company=(item.get("company") or {}).get("title") or "—",
                title=title,
                location=", ".join(["Remote"] if item.get("remoteWork") else places),
                url=url,
                posted=parse_date((item.get("publishedDate") or {}).get("date")),
                description=f"{body}\nSkills: {skills}" if body else "",
                remote=bool(item.get("remoteWork")),
                employment=(item.get("employment") or "").replace("_", "-"),
                salary=salary,
                source="habr career",
            )
        meta = data.get("meta") or {}
        if page_no >= int(meta.get("totalPages") or 0):
            break


_ZERO_ONE = "https://01.tech"
_ZERO_ONE_ID_RX = re.compile(r'"([a-z0-9]{24})"')
_ZERO_ONE_FORMAT_RX = re.compile(r"\b(Remote|Hybrid|On site)\b")


def zero_one_tech(http: HttpClient) -> Iterator[Job]:
    """01.tech (iGaming): the list loads with a "Load more" button, but the ids of all vacancies are in the page data.

    Each one is opened by id; the title comes from <title>, the format (Remote / Hybrid / On site) from the header.
    The site does not name the office city, relocation comes after the probation period.
    """
    page = _get(http, f"{_ZERO_ONE}/vacancies")
    for vid in dict.fromkeys(_ZERO_ONE_ID_RX.findall(page)):
        url = f"{_ZERO_ONE}/vacancies/{vid}"
        try:
            detail = _get(http, url)
        except FetchError:
            continue  # not every id in the page data is a vacancy
        m = re.search(r"<title>\s*Vacancy:\s*(.*?)\s*</title>", detail, re.S)
        if not m:
            continue
        title = html.unescape(m.group(1)).strip()
        if not filters.title_candidate(title):
            continue
        text = _page_text(detail)
        fmt = _ZERO_ONE_FORMAT_RX.search(text[:600])
        work = fmt.group(1) if fmt else ""
        yield Job(
            company="01.tech", title=title, location="Remote" if work == "Remote" else work.lower(),
            url=url, description=text, remote=work == "Remote", source="01.tech",
        )


_RELOCATE_ME = "https://relocate.me"
_RELOCATE_LINK_RX = re.compile(r'<a[^>]*href="(/([a-z-]+)/([a-z-]+)/([a-z0-9-]+)/[a-z0-9-]+-\d+)"[^>]*>(.*?)</a>', re.S)
RELOCATE_ME_PAGES = 5


def relocate_me(http: HttpClient) -> Iterator[Job]:
    """relocate.me: vacancies with relocation and visa support; search for "python", link = /country/city/company/…"""
    seen = set()
    for page_no in range(1, RELOCATE_ME_PAGES + 1):
        page = _get(http, f"{_RELOCATE_ME}/international-jobs?query=python&page={page_no}")
        links = _RELOCATE_LINK_RX.findall(page)
        fresh = [x for x in links if x[0] not in seen]
        if not fresh:
            break
        for path, country, city, company, inner in fresh:
            seen.add(path)
            title = re.sub(r"\s+in\s+[A-Z][\w\s-]*$", "", html_to_text(inner).strip())
            if not filters.title_candidate(title):
                continue
            try:
                detail = _get(http, _RELOCATE_ME + path)
            except FetchError as e:
                log.warning("relocate.me %s: %s", path, e)
                continue
            place = ", ".join(w.replace("-", " ").title() for w in (city, country) if w != "remote") or "Remote"
            yield Job(
                company=company.replace("-", " ").title(), title=title, location=place,
                url=_RELOCATE_ME + path, description=_page_text(detail)[:20000],
                remote=country == "remote", source="relocate.me",
            )


def manual_jobs(http: HttpClient) -> Iterator[Job]:
    """Single vacancies from settings.toml [manual_jobs].urls — found by hand, opened on every run.

    Data: JSON-LD JobPosting, otherwise Next.js data (pageProps.position), otherwise
    <title> and the page text. The company comes from JSON-LD or from the domain (example.com -> Example). If the domain
    does not match the company (a job on an agency site), the entry is { url = "...", company = "..." }:
    the application status is matched by that company. location = "Remote" — when the page has none.
    """
    from urllib.parse import urlsplit

    from . import geo

    for entry in geo.S.get("manual_jobs", {}).get("urls", []):
        url, forced = (entry, "") if isinstance(entry, str) else (entry.get("url", ""), entry.get("company", ""))
        try:
            page = _get(http, url)
        except FetchError as e:
            log.warning("manual_jobs %s: %s", url, e)
            continue
        domain = urlsplit(url).netloc.removeprefix("www.").split(".")
        company = (domain[-2] if len(domain) >= 2 else domain[0]).capitalize()
        title, location, text, remote = "", "", "", None
        ld = _jsonld(page, "JobPosting")
        if ld:
            title = ld.get("title") or ""
            company = (ld.get("hiringOrganization") or {}).get("name") or company
            places = ld.get("jobLocation") or []
            places = places if isinstance(places, list) else [places]
            location = "; ".join(filter(None, (
                ", ".join(filter(None, [(p.get("address") or {}).get("addressLocality"),
                                        (p.get("address") or {}).get("addressCountry")]))
                for p in places if isinstance(p, dict))))
            if ld.get("jobLocationType") == "TELECOMMUTE":
                remote, location = True, "; ".join(filter(None, ["Remote", location]))
            text = html_to_text(html.unescape(ld.get("description") or ""))
        else:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
            position: dict = {}
            if m:
                try:
                    position = (json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("position") or {})
                except ValueError:
                    position = {}
            if position:
                title = position.get("text") or position.get("title") or ""
                names = [p.get("name", "") for p in position.get("locations") or [] if isinstance(p, dict)]
                location = "; ".join(names)
                remote = any(p.get("type") == "remote" for p in position.get("locations") or [] if isinstance(p, dict))
                text = html_to_text(position.get("description") or "")
            else:
                t = re.search(r"<title>(.*?)</title>", page, re.S)
                title = html.unescape(t.group(1)).split("|")[0].strip() if t else url
                # "Lead Engineer - Acme" on acme.example: the tail is the site name, not part of the title
                head, sep, tail = title.rpartition(" - ")
                if sep and tail.strip().lower() in ".".join(domain).lower():
                    title = head.strip()
                text = _page_text(page)
        # location from the entry — when the page has none (otherwise the filter drops the job as not remote)
        location = location or ("" if isinstance(entry, str) else entry.get("location", ""))
        remote = remote or (bool(re.search(r"remote", location, re.I)) or None)
        yield Job(company=forced or company, title=title, location=location, url=url, description=text[:20000],
                  remote=remote, source="manual")


_HIREHI = "https://hirehi.ru"
HIREHI_SECTIONS = ("/vacancies/python", "/vacancies/backend")


def hirehi(http: HttpClient) -> Iterator[Job]:
    """HireHi: the section list is a JSON-LD ItemList on the page, a vacancy is a JSON-LD JobPosting (API closed in robots.txt)."""
    seen = set()
    for section in HIREHI_SECTIONS:
        page = _get(http, _HIREHI + section)
        items: list = []
        for raw in _JSONLD_RX.findall(page):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                items = data.get("itemListElement") or []
        for item in items:
            url, title = item.get("url") or "", item.get("name") or ""
            if not url or url in seen or not filters.title_candidate(title):
                continue
            seen.add(url)
            try:
                ld = _jsonld(_get(http, url), "JobPosting")
            except FetchError as e:
                log.warning("hirehi %s: %s", url, e)
                continue
            if not ld:
                continue
            address = ((ld.get("jobLocation") or {}).get("address") or {}) if isinstance(ld.get("jobLocation"), dict) else {}
            place = ", ".join(filter(None, [address.get("addressLocality"), address.get("addressCountry")]))
            text = html_to_text(html.unescape(ld.get("description") or ""))
            remote = ld.get("jobLocationType") == "TELECOMMUTE" or bool(re.search(r"удал[её]нн|remote", place, re.I))
            pay = ld.get("baseSalary") or {}
            amount = (pay.get("value") or {}).get("value") if isinstance(pay.get("value"), dict) else pay.get("value")
            unit = ((pay.get("value") or {}).get("unitText") or "") if isinstance(pay.get("value"), dict) else ""
            salary = f"{amount} {pay.get('currency', '')}{' per year' if unit == 'YEAR' else ''}" if amount else ""
            yield Job(
                company=(ld.get("hiringOrganization") or {}).get("name") or "—", title=ld.get("title") or title,
                location=place or ("Remote" if remote else ""), url=url, posted=parse_date(ld.get("datePosted")),
                description=text, remote=remote or None, salary=salary,
                employment=(ld.get("employmentType") or "").replace("_", "-").lower(), source="hirehi",
            )


SOURCES.update({
    "hirehi": hirehi,
    "manual": manual_jobs,
    "relocate.me": relocate_me,
    "01.tech": zero_one_tech,
    "habr career": habr_career,
    "rivian": rivian,
    "remoterocketship": remoterocketship,
    "brenxor": brenxor,
    "justjoin.it": justjoin,
    "remote.com": remotecom,
    "cryptocurrencyjobs": cryptocurrencyjobs,
})


# --- Company career pages without a supported ATS (settings.toml [career_pages]) ------------

PAGE_STATUS: dict[str, dict] = {}  # company -> what could be read (for the availability report)
PAGE_DETAIL_LIMIT = 100  # a guard against a wrongly parsed page; the best-fitting go first
_ANCHOR_RX = re.compile(r'<a\s([^>]*?href="([^"#]+)"[^>]*)>(.*?)</a>', re.S | re.I)
_LABEL_RX = re.compile(r'(?:aria-label|title)="([^"]+)"', re.I)


# link text that is not a vacancy title; then the title comes from the address (/job/<slug>, /careers/<slug>)
_CORE_TITLE_RX = re.compile(r"python|django|fastapi|back[\s-]?end|бэкенд|бекенд", re.I)
_GENERIC_LINK_RX = re.compile(r"^(?:apply(?: now)?|learn more|read more|details|view(?: job| role)?|more|подробнее|откликнуться|смотреть)\W*$", re.I)
_JOB_SLUG_RX = re.compile(r"/(?:jobs?|careers?|positions?|vacanc(?:y|ies)|openings?)/([a-z0-9][a-z0-9_-]{3,90})/?(?:[?#]|$)", re.I)


def career_page(http: HttpClient, company, url: str, location: str = "", links: str = "") -> list[Job]:
    """Finds links to vacancies of fitting roles on a career page and reads their text.

    Works only for pages that serve the vacancy list in HTML; if the list
    is built by scripts, there will be no links — this shows in the availability report.
    location — the default label if the vacancy page names neither Remote nor the home country
    (for example, "Remote" for a company that works fully remotely).
    links — the start of vacancy addresses ("https://example.com/careers/") if the site menu has links
    with similar names that are not vacancies ("Hire Python Developers").
    """
    from urllib.parse import urljoin

    from . import config as c

    status = PAGE_STATUS.setdefault(company.name, {"links": 0, "jobs": 0, "error": ""})
    try:
        r = http.get(url, timeout=25, headers=_HTML)
    except FetchError as e:
        status["error"] = str(e)[:120]
        return []
    if r.status_code != 200:
        status["error"] = f"HTTP {r.status_code}"
        return []
    candidates: dict[str, str] = {}
    for attrs, href, inner in _ANCHOR_RX.findall(r.text):
        lines = [line.strip() for line in html_to_text(inner).split("\n") if line.strip()]
        label = _LABEL_RX.search(attrs)
        # a button link without text: the vacancy title is only in aria-label / title ("Title, Department")
        title = lines[0] if lines else (html.unescape(label.group(1)).split(",")[0].strip() if label else "")
        if _GENERIC_LINK_RX.match(title):
            # an "Apply" button with the title in a neighbouring block: take it from the address /job/senior-python-developer
            slug = _JOB_SLUG_RX.search(href)
            title = slug.group(1).replace("-", " ").replace("_", " ").strip().title() if slug else ""
        link = urljoin(r.url, href)
        if links and (not link.startswith(links) or link.rstrip("/") == links.rstrip("/")):
            continue
        if 5 <= len(title) <= 110 and filters.title_candidate(title):
            candidates.setdefault(link, title)
    status["links"] = len(candidates)
    # Python / backend first, then other engineering roles — a limit cut loses the least fitting ones
    ranked = sorted(candidates.items(), key=lambda kv: (0 if _CORE_TITLE_RX.search(kv[1]) else 1))
    if len(ranked) > PAGE_DETAIL_LIMIT:
        log.warning("%s: opened %d of %d fitting vacancies on the career page — the rest were skipped "
                    "(PAGE_DETAIL_LIMIT in sites.py)", company.name, PAGE_DETAIL_LIMIT, len(ranked))
    jobs: list[Job] = []
    for link, title in ranked[:PAGE_DETAIL_LIMIT]:
        try:
            page = http.get(link, timeout=25, headers=_HTML)
        except FetchError:
            continue
        if page.status_code != 200:
            continue
        text = _page_text(page.text)[:20000]
        head = text[:4000]
        places = []
        if c.HOME_COUNTRY and c.HOME_RX.search(head):
            places.append(c.HOME_COUNTRY)
        if c.REMOTE_RX.search(head):
            places.append("Remote")
        if not places and location:
            places.append(location)
        jobs.append(Job(
            company=company.name, title=title, location="; ".join(places), url=link,
            description=text, remote=bool(c.REMOTE_RX.search(" ".join(places))) or None,
            source="career-page", score=company.score, note=company.note,
        ))
    status["jobs"] = len(jobs)
    return jobs


# --- Big companies (jobhunter/bigtech.py): Microsoft, Workday sites, Oracle, Amazon ----
from .bigtech import SOURCES as BIGTECH_SOURCES

SOURCES.update(BIGTECH_SOURCES)
