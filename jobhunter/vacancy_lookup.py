"""A vacancy for a CV by link: from saved reports, and if it is not there — from the vacancy page.

The page is read through jobhunter.http (robots.txt, 1 request per second per host). The text comes from
the JobPosting markup (JSON-LD) most ATS and career sites serve, otherwise it is the whole
page text.
"""
from __future__ import annotations

import json
import re

from . import config
from .http import FetchError, Http
from .models import html_to_text
from .keys import norm_url

_JSONLD_RX = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


class VacancyNotFound(Exception):
    """The vacancy was found neither in the reports nor by the link."""


def archived(url: str, day: str = "") -> dict | None:
    """A vacancy from state/jobs/<date>.json (the given date first, then the others from newest to oldest)."""
    key = norm_url(url)
    files = sorted(config.JOBS_ARCHIVE_DIR.glob("*.json"), reverse=True)
    if day:
        files.sort(key=lambda p: p.stem != day)
    files.append(config.ROOT / "state" / "jobs_latest.json")
    for path in files:
        try:
            jobs = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for job in jobs:
            if job.get("url") and norm_url(job["url"]) == key:
                return {**job, "found_in": path.stem}
    return None


def _job_posting(raw: str) -> dict | None:
    """The first JobPosting from the page JSON-LD (inside @graph and lists too)."""
    for block in _JSONLD_RX.findall(raw):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack += node
            elif isinstance(node, dict):
                kind = node.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    return node
                stack += [v for v in node.values() if isinstance(v, (dict, list))]
    return None


def fetch(url: str, http: Http | None = None) -> dict:
    """A vacancy from the page: {url, company, title, text}. Company and title come from JobPosting or <title>.

    Raises:
        VacancyNotFound: the page is unavailable, closed by robots.txt or has too little text.
    """
    http = http or Http()
    try:
        r = http.get(url, timeout=25, headers={"Accept": "text/html"})
    except FetchError as e:
        raise VacancyNotFound(f"the page did not open: {e}") from e
    if r.status_code != 200:
        raise VacancyNotFound(f"the page answered HTTP {r.status_code}")
    posting = _job_posting(r.text)
    if posting:
        org = posting.get("hiringOrganization") or {}
        company = org.get("name", "") if isinstance(org, dict) else str(org)
        text = html_to_text(posting.get("description", ""))
        title = posting.get("title", "")
    else:
        visible = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", r.text)
        company, text = "", html_to_text(visible)
        title = html_to_text((_TITLE_RX.search(r.text) or [None, ""])[1])
    if len(text) < 300:  # a vacancy list, a sign-in page, or a vacancy drawn by a script
        raise VacancyNotFound("the page has almost no vacancy text — paste the text by hand")
    return {"url": url, "company": company.strip(), "title": title.strip(), "text": text, "found_in": "page"}
