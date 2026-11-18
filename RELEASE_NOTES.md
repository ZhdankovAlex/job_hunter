# Release notes

Built from the git history automatically after a push to main (`python -m jobhunter.release_notes`). Newest changes first.

## 2026-11-18

### Skip unreachable hosts, no-cache reports, release notes from git history

- http: a host that times out or drops the connection on 2 requests in a row (Http.UNREACHABLE_AFTER) is skipped until the end of the run; after the first failure there are no more 40 s retries
- web: /files/reports/ is served with Cache-Control: no-cache, so the Applications page never shows a stale report
- [manual_jobs]: an entry can be { url, company, location } — company for a job on an agency site, location when the page has none; a " - <site name>" tail is cut from the <title>
- jobhunter/release_notes.py and .github/workflows/release-notes.yml: RELEASE_NOTES.md is rebuilt from the git history after every push to main and committed back by the bot ("RELEASE_NOTES.md: update")
- release_notes.REWORDED: English messages for the first three commits, which were written in Russian; the history itself is not rewritten
- tests: test_http.py, test_release_notes.py, no-cache and manual_jobs cases; docs updated

<sub>fd3a076</sub>

## 2026-11-13

### Remove BOM from cv/base.example.toml

tomllib cannot parse a file that starts with a BOM; CI failed in test_cvgen_report.


<sub>1fb4b71</sub>

### Translate the project to English

Web UI, CLI and log messages, docstrings, comments, tests, example configs and docs are now in English; screenshots retaken on demo data. Patterns that match Russian-language vacancies, letters and Telegram posts are kept. cv/base.example.toml gets thesis_ai, so the AI CV builds from the example.


<sub>11d0482</sub>

## 2026-11-12

### Translate the project to English

Web UI, CLI and log messages, docstrings, comments, tests, example configs and docs are now in English; screenshots retaken on demo data. Patterns that match Russian-language vacancies, letters and Telegram posts are kept, as are the Russian samples in the tests.


<sub>4d1c69d</sub>

### Tests and CI, applications, web UI speed, reasons for empty sources

Tests and checks

Fixes

Applications

Sources and web UI

Docs

- tests/: 160 tests without network and personal files (~1.5 s): letters, application statuses and the funnel, Fit %, vacancy checks, location and salary ranges, ATS, freehire, Telegram, site parsing, CV and report, web pages; tests use settings.example.toml with a fictional home country
- .github/workflows/ci.yml: ruff check and pytest on every pull request and push to main; pyproject.toml, requirements-dev.txt
- JOBHUNTER_SETTINGS / JOBHUNTER_PROFILE / JOBHUNTER_CV_BASE: other settings, profile and CV base files; JOBHUNTER_NO_WARM: no background warm-up of the web UI cache
- salary: "4.5-5.5k EUR", "€90 - €110K": the "k" of the second number applies to the first one too
- geo: "Remote from anywhere" allows working outside the country (but not "from anywhere in the US")
- config: part_time (freehire API)
- telegram: an extra space in "Remote , worldwide"
- cv_match: closure over loop variables; report: a stray f-prefix
- a "declined" event (withdrawn by the candidate) in applications_manual.toml and in the form on the Applications page
- applying again after a rejection: the status follows the new process, earlier stages do not count
- a series of stage letters that ended with a cancellation is not a stage; "Meeting cancelled" after a rejection does not change the status; events entered by hand are never dropped
- "Days of silence" only for live processes, no negative numbers for future events
- "may not be the right fit" in a confirmation is a caveat, not a rejection; typographic apostrophes in letters
- letters are parsed sentence by sentence instead of quadratic regexes: the Applications page opens in a fraction of a second
- event times use the computer's time zone; [mail].utc_offset_hours sets it explicitly
- jobhunter/coverage.py and the Sources page: why a source gave no vacancies (error, VERIFY, own page, board not found / empty, no vacancies, none matching in 14 days); the table is collapsed, filters by reason
- [career_pages]: the links setting (the start of vacancy URLs); pages from this section are read for staffing companies too
- calculation cache state/web_cache.json: "CV vs market" and "Skills" open at once; cv_match 2.5x faster
- docs/architecture.md (how the project works), docs/testing.md; README, web-ui, sources, applications, configuration; the Sources screenshot

<sub>1545e5b</sub>

## 2026-11-07

### Cover letters, stopping a run, application funnel, self-healing boards

- ats: Workable duplicates across countries merged, Rippling boards with job cards; a missing board (404/401/403/HTML) triggers a search for a new one ("the board moved") or "no board" with a recheck in 30 days; ats_mapping.json is saved by merging; jobs_for(quiet=...)
- companies: Cyrillic look-alikes of Latin letters in slug_candidates
- sites: career pages take the title from the URL for "Apply" buttons, a limit of 100, Python/backend first, an "opened N of M" warning; a Paychex adapter (iCIMS Jibe JSON, off by default)
- main: Telegram posts without the full text fetch it from the company ATS board and are checked again; staffing companies are not crawled on career pages; "Skip stage" aborts running requests (http.cancel)
- verify: "annotation" on its own is not data labelling
- mail: the company from the calendar, subject templates, the letter text, iCIMS/Recruitee; [mail.sender_company]; an InMail "JOB OFFER" and a process description in an application confirmation are not offers
- applications_report: the funnel applications -> screening -> technical -> final -> offer, a "Reached" column
- progress: errors.log holds only the current run, the previous one goes to errors.prev.log (JOBHUNTER_CONTINUE_RUN); warning groups action / code / site; the feed shows all run events
- web UI: warning groups and feed counters on Pipeline, a "Stop run" button (POST /api/runs/current/stop); Interview prep from pasted text; a full-width CV form; a new Cover letter page (jobhunter/cover_letter.py, POST /api/cover, the [cover] section in cv/base.example.toml)
- settings.example.toml: a freehire query example with visa_sponsorship, [mail.sender_company]
- docs: README, web-ui, cv (letters), pipeline, sources, applications, configuration; screenshots Cover letter, Applications, Pipeline

<sub>5516ca3</sub>

## 2026-11-06

### First public version of job-hunter


<sub>4afc156</sub>
