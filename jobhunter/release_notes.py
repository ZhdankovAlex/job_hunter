"""Release notes from the git history: what was added or fixed, and when.

Nothing to maintain separately: a change is described once — in the commit message (a title,
then "- ..." items). RELEASE_NOTES.md in the repository root is built from that.

A GitHub Action updates it after a push to main (.github/workflows/release-notes.yml) with a separate
commit — run git pull --rebase before the next push. The same by hand:

    python -m jobhunter.release_notes     # rewrite RELEASE_NOTES.md

Standard library only and no jobhunter.config (it reads settings.toml): the Action installs nothing.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTES_FILE = ROOT / "RELEASE_NOTES.md"
# title of the Action's own commit — it does not get into the notes
BOT_TITLE = "RELEASE_NOTES.md: update"
# field and record separators for git log: characters that never occur in a commit message
_FIELD, _RECORD = "\x1f", "\x1e"
_LOG_FORMAT = _FIELD.join(["%h", "%ad", "%s", "%b"]) + _RECORD
# trailer lines at the end of a message — not for the reader
_TRAILERS = ("co-authored-by:", "signed-off-by:")
# the first commits were written in Russian; the history is not rewritten, the notes use these messages instead
# (short sha -> title, then the body in the same format as a commit message)
REWORDED = {
    "1545e5b": """Tests and CI, applications, web UI speed, reasons for empty sources
Tests and checks
- tests/: 160 tests without network and personal files (~1.5 s): letters, application statuses and the funnel,
  Fit %, vacancy checks, location and salary ranges, ATS, freehire, Telegram, site parsing, CV and report,
  web pages; tests use settings.example.toml with a fictional home country
- .github/workflows/ci.yml: ruff check and pytest on every pull request and push to main;
  pyproject.toml, requirements-dev.txt
- JOBHUNTER_SETTINGS / JOBHUNTER_PROFILE / JOBHUNTER_CV_BASE: other settings, profile and CV base files;
  JOBHUNTER_NO_WARM: no background warm-up of the web UI cache

Fixes
- salary: "4.5-5.5k EUR", "€90 - €110K": the "k" of the second number applies to the first one too
- geo: "Remote from anywhere" allows working outside the country (but not "from anywhere in the US")
- config: part_time (freehire API)
- telegram: an extra space in "Remote , worldwide"
- cv_match: closure over loop variables; report: a stray f-prefix

Applications
- a "declined" event (withdrawn by the candidate) in applications_manual.toml and in the form on the Applications page
- applying again after a rejection: the status follows the new process, earlier stages do not count
- a series of stage letters that ended with a cancellation is not a stage; "Meeting cancelled" after a rejection
  does not change the status; events entered by hand are never dropped
- "Days of silence" only for live processes, no negative numbers for future events
- "may not be the right fit" in a confirmation is a caveat, not a rejection; typographic apostrophes in letters
- letters are parsed sentence by sentence instead of quadratic regexes: the Applications page opens in a fraction of a second
- event times use the computer's time zone; [mail].utc_offset_hours sets it explicitly

Sources and web UI
- jobhunter/coverage.py and the Sources page: why a source gave no vacancies (error, VERIFY, own page,
  board not found / empty, no vacancies, none matching in 14 days); the table is collapsed, filters by reason
- [career_pages]: the links setting (the start of vacancy URLs); pages from this section are read for staffing companies too
- calculation cache state/web_cache.json: "CV vs market" and "Skills" open at once; cv_match 2.5x faster

Docs
- docs/architecture.md (how the project works), docs/testing.md; README, web-ui, sources, applications,
  configuration; the Sources screenshot
""",
    "5516ca3": """Cover letters, stopping a run, application funnel, self-healing boards
- ats: Workable duplicates across countries merged, Rippling boards with job cards; a missing board
  (404/401/403/HTML) triggers a search for a new one ("the board moved") or "no board" with a recheck in 30 days;
  ats_mapping.json is saved by merging; jobs_for(quiet=...)
- companies: Cyrillic look-alikes of Latin letters in slug_candidates
- sites: career pages take the title from the URL for "Apply" buttons, a limit of 100, Python/backend first,
  an "opened N of M" warning; a Paychex adapter (iCIMS Jibe JSON, off by default)
- main: Telegram posts without the full text fetch it from the company ATS board and are checked again;
  staffing companies are not crawled on career pages; "Skip stage" aborts running requests (http.cancel)
- verify: "annotation" on its own is not data labelling
- mail: the company from the calendar, subject templates, the letter text, iCIMS/Recruitee; [mail.sender_company];
  an InMail "JOB OFFER" and a process description in an application confirmation are not offers
- applications_report: the funnel applications -> screening -> technical -> final -> offer, a "Reached" column
- progress: errors.log holds only the current run, the previous one goes to errors.prev.log (JOBHUNTER_CONTINUE_RUN);
  warning groups action / code / site; the feed shows all run events
- web UI: warning groups and feed counters on Pipeline, a "Stop run" button (POST /api/runs/current/stop);
  Interview prep from pasted text; a full-width CV form; a new Cover letter page
  (jobhunter/cover_letter.py, POST /api/cover, the [cover] section in cv/base.example.toml)
- settings.example.toml: a freehire query example with visa_sponsorship, [mail.sender_company]
- docs: README, web-ui, cv (letters), pipeline, sources, applications, configuration; screenshots
  Cover letter, Applications, Pipeline
""",
    "4afc156": "First public version of job-hunter\n",
}


class GitUnavailable(RuntimeError):
    """git is not installed or the folder is not a repository (for example, a copy without .git in a container)."""


@dataclass
class Change:
    """One commit: short sha, date (YYYY-MM-DD), title, text paragraphs and list items."""

    sha: str
    date: str
    title: str
    text: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)


def parse_body(body: str) -> tuple[list[str], list[str]]:
    """Commit body -> (paragraphs, items). "- " starts an item, an indented line continues it."""
    text: list[str] = []
    items: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            text.append(" ".join(paragraph))
            paragraph.clear()

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.lower().startswith(_TRAILERS):
            continue
        elif line.startswith(("- ", "* ")):
            flush()
            items.append(line[2:].strip())
        elif raw.startswith((" ", "\t")) and items and not paragraph:
            items[-1] += " " + line
        else:
            paragraph.append(line)
    flush()
    return text, items


def parse_log(raw: str) -> list[Change]:
    """git log output in _LOG_FORMAT -> changes in the same order (newest first)."""
    changes = []
    for record in raw.split(_RECORD):
        if not record.strip():
            continue
        sha, date, title, body = (record.strip("\n").split(_FIELD) + ["", "", "", ""])[:4]
        if title.strip() == BOT_TITLE:
            continue
        if sha[:7] in REWORDED:
            title, _, body = REWORDED[sha[:7]].partition("\n")
        text, items = parse_body(body)
        changes.append(Change(sha=sha, date=date, title=title.strip(), text=text, items=items))
    return changes


def changes(limit: int | None = None) -> list[Change]:
    """Changes from the repository git log, newest first. Raises GitUnavailable."""
    args = ["git", "log", f"--format={_LOG_FORMAT}", "--date=short", "--no-merges"]
    if limit:
        args.append(f"-{limit}")
    try:
        out = subprocess.run(args, cwd=ROOT, capture_output=True, check=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise GitUnavailable(f"git history unavailable: {e}") from e
    return parse_log(out)


def by_date(items: list[Change]) -> list[tuple[str, list[Change]]]:
    """Changes grouped by day: [(date, [changes]), ...] — the order is kept."""
    return [(day, list(group)) for day, group in groupby(items, key=lambda c: c.date)]


def to_markdown(items: list[Change]) -> str:
    lines = ["# Release notes", "",
             "Built from the git history automatically after a push to main (`python -m jobhunter.release_notes`). "
             "Newest changes first.", ""]
    for day, group in by_date(items):
        lines += [f"## {day}", ""]
        for c in group:
            lines += [f"### {c.title}", ""]
            lines += [*(f"{p}\n" for p in c.text)]
            lines += [f"- {item}" for item in c.items]
            lines += [f"\n<sub>{c.sha}</sub>", ""]
    return "\n".join(lines).rstrip() + "\n"


def write() -> int:
    """Rewrites RELEASE_NOTES.md -> how many changes it holds."""
    items = changes()
    NOTES_FILE.write_text(to_markdown(items), encoding="utf-8", newline="\n")  # LF on Windows too, as in the Action
    return len(items)


if __name__ == "__main__":
    print(f"{NOTES_FILE.name}: {write()} changes")
