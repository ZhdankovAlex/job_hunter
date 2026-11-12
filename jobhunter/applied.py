"""Applications from applications_manual.toml: which vacancies and companies you already applied to."""
import logging
import re
import tomllib

from . import config
from .keys import norm_url, url_ids
from .models import norm_name

log = logging.getLogger(__name__)

_URL_RX = re.compile(r"https?://\S+")


class Applications:
    def __init__(self, rows):
        header = [h.strip().lower() for h in rows[0]] if rows else []

        def col(name, default):
            return next((i for i, h in enumerate(header) if h.startswith(name)), default)

        ci, si, ui = col("company", 0), col("status", 1), col("vacancy", 2)
        self.urls, self.ids, self.companies = {}, {}, {}  # link / vacancy id -> status
        self.count = 0
        for row in rows[1:]:
            row = row + [""] * (max(ci, si, ui) + 1 - len(row))
            company, status = row[ci].strip(), row[si].strip()
            if not company and not row[ui].strip():
                continue
            self.count += 1
            for url in _URL_RX.findall(row[ui]):
                self.urls[norm_url(url)] = status
                self.ids.update(dict.fromkeys(url_ids(url), status))
            if company:
                self.companies.setdefault(norm_name(company), set()).add(status or "no status")

    def job_status(self, job):
        """Status of the application to this vacancy; None — no applications to it."""
        status = self.urls.get(norm_url(job.url))
        if status is None:
            status = next((self.ids[i] for i in url_ids(job.url) if i in self.ids), None)
        return status

    def has_job(self, job):
        """The vacancy is already in progress: an application without an answer, or interviews are going on.

        After "Rejected" / "Ignored" / "Declined" the vacancy is shown again — you can apply once more.
        """
        status = self.job_status(job)
        return status is not None and not config.RETRY_STATUS_RX.search(status)

    def company_statuses(self, company):
        key = norm_name(company)
        for known, statuses in self.companies.items():
            short, long_ = sorted((key, known), key=len)
            if short == long_ or (len(short) >= 3 and long_.startswith(short)):
                return ", ".join(sorted(statuses))
        return ""


# event kind from applications_manual.toml -> status (RETRY_STATUS_RX understands "Rejected")
_KIND_STATUS = {"applied": "Applied", "interview": "Interview", "rejected": "Rejected", "offer": "Offer",
                "declined": "Declined", "other": "Applied"}


def _manual_rows() -> list[list[str]]:
    """Rows "company, status, link" from applications_manual.toml events that have a url."""
    if not config.MANUAL_APPLICATIONS_FILE.exists():
        return []
    data = tomllib.loads(config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8"))
    rows = []
    for entry in data.get("application", []):
        for event in entry.get("events", []):
            if event.get("url"):
                rows.append([entry["company"], _KIND_STATUS.get(event.get("kind", ""), "Applied"), event["url"]])
    return rows


def load_applications():
    """Applications from events with a url in applications_manual.toml -> Applications."""
    apps = Applications([["Company", "Status", "Vacancy"], *_manual_rows()])
    log.info("Applications: %d rows (applications_manual.toml)", apps.count)
    return apps
