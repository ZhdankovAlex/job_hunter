"""Which sources give no vacancies to the report and why — for the "Sources" page.

Each company has one reason, by precedence (`REASONS`): request error → check by hand (VERIFY
in companies.txt) → own page without an ATS → board not found → empty board → no vacancies → there are vacancies,
but none passed the filters in the last `MATCH_DAYS` reports. Data: the result of the last run
(`state/sources_latest.json`), `ats_mapping.json`, `companies.txt` and the report archive `state/jobs/`.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import config
from .companies import load_companies
from .keys import company_key

SOURCES_FILE = config.ROOT / "state" / "sources_latest.json"
JOBS_DIR = config.ROOT / "state" / "jobs"
MATCH_DAYS = 14  # how many recent reports to look at for "none matching"

# key → (group title, hint text)
REASONS = {
    "error": ("Request error", "The source answered with an error in the last run"),
    "verify": ("Check by hand", "Marked VERIFY in companies.txt: the site is looked up by hand"),
    "manual": ("Own page without an ATS", "Marked by hand in ats_mapping.json: read it yourself or via [career_pages]"),
    "no_board": ("Board not found", "The board name fit no ATS; the next attempt is "
                                    f"{config.RECHECK_NOT_FOUND_DAYS} days after the check"),
    "empty_board": ("Empty board", "The ATS board was found, but it has no open vacancies"),
    "zero": ("No vacancies", "The source is read, but gave 0 vacancies in the last run"),
    "no_match": ("None matching", f"There are vacancies, but none got into the reports of the last {MATCH_DAYS} days"),
}
COMPANY_KINDS = ("ats", "career_page")  # aggregators and boards are not companies
_CHECKED = ("verify", "manual", "no_board", "empty_board")  # where the board check date means something


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def matched_companies(jobs_dir: Path = JOBS_DIR, days: int = MATCH_DAYS) -> tuple[set[str], int]:
    """Companies (company_key) with at least one vacancy in a report within `days` days; and the number of reports."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    keys, reports = set(), 0
    for path in sorted(jobs_dir.glob("*.json")):
        if path.stem < cutoff:
            continue
        reports += 1
        keys.update(company_key(j.get("company") or "") for j in _read_json(path, []))
    return keys, reports


def _verify_notes() -> dict[str, str]:
    return {company_key(c.name): c.note.removeprefix("VERIFY").lstrip(": ").strip()
            for c in load_companies() if c.note.startswith("VERIFY")}


def _reason(entry: dict, verify_note: str | None, matched: bool) -> tuple[str, str] | None:
    """(reason, details) or None if the company is read and gives matching vacancies."""
    if entry.get("error"):
        return "error", entry["error"]
    if verify_note is not None:
        return "verify", verify_note
    platform = entry.get("platform")
    if entry["kind"] == "ats" and not platform:
        if entry.get("manual"):
            return "manual", entry.get("note") or entry.get("from") or ""
        if entry.get("empty_board"):
            return "empty_board", ""  # the board itself is shown in the "ATS / board" column
        checked = entry.get("checked_at") or ""
        try:
            retry = date.fromisoformat(checked) + timedelta(days=config.RECHECK_NOT_FOUND_DAYS)
            return "no_board", f"next attempt — {retry.isoformat()}"
        except ValueError:
            return "no_board", ""
    if entry["jobs"] == 0:
        return "zero", ""  # the board and the number of vacancies are shown in the neighbouring columns
    if entry["kind"] in COMPANY_KINDS and not matched:  # an aggregator or board is not a company, not looked up in reports
        return "no_match", ""
    return None


def gaps(sources: dict | None = None, mapping: dict | None = None, matched: set[str] | None = None,
         verify: dict[str, str] | None = None) -> dict:
    """Sources without vacancies in the report: {groups: [{key, title, hint, count}], rows: [...], reports: N}.

    Companies (ATS, career pages) go through all checks, aggregators and boards — only error and zero.

    The arguments are for tests; by default everything is read from files.
    """
    sources = _read_json(SOURCES_FILE, {}) if sources is None else sources
    mapping = _read_json(config.MAPPING_FILE, {}) if mapping is None else mapping
    reports = None
    if matched is None:
        matched, reports = matched_companies()
    verify = _verify_notes() if verify is None else verify

    rows = []
    for name, entry in sources.items():
        company = entry.get("kind") in COMPANY_KINDS
        if company:
            entry = {**(mapping.get(name) or {}), **entry}  # career_page entries lack the mapping fields
        key = company_key(name)
        found = _reason(entry, verify.get(key) if company else None, key in matched)
        if found:
            reason, detail = found
            rows.append({"name": name, "reason": reason, "detail": detail,
                         "checked": entry.get("checked_at", "") if reason in _CHECKED else ""})
    order = list(REASONS)
    rows.sort(key=lambda r: (order.index(r["reason"]), r["name"].lower()))
    counts = {k: sum(r["reason"] == k for r in rows) for k in REASONS}
    groups = [{"key": k, "title": t, "hint": h, "count": counts[k]} for k, (t, h) in REASONS.items() if counts[k]]
    return {"groups": groups, "rows": rows, "reports": reports}
