# Release notes

Built from the git history automatically after a push to main (`python -m jobhunter.release_notes`). Newest changes first.

## 2026-11-18

### mypy as a profile skill and a CV technology, Claude Code skills for new sources and commits (#7)

- profile.example.toml: a "Static typing (mypy)" skill (weight 1) — mypy, pyright, type hints, static typing
- cv_match.TECH: mypy — without it the "mypy" line of the CV base never reached a CV built for a vacancy that asks for it (optional skills are picked by a TECH_RX match)
- .claude/skills/new-source: connecting a site, board, ATS or channel — robots.txt and bot protection, choosing the way (ATS, career_pages, manual_jobs, an adapter), a fixture and a FakeHttp test, docs/sources.md
- .claude/skills/prep-commit: checks, personal files, which docs to update, the commit message style that RELEASE_NOTES.md is built from; .gitignore keeps the rest of .claude/ out of git
- docs/configuration.md: .claude/skills/ as example Claude Code skills

<sub>a97c0b2</sub>

### Docs: run speed, how web pages are built (#6)

- docs/pipeline.md: "Run speed" — parallel hosts with a per-host limit, skipping unreachable hosts, not re-searching missing boards, caches between runs, the quadratic regexes that were fixed and the rule to test regexes on 50–100 KB without spaces
- docs/web-ui.md: "How pages are built" — the same data for the template and the API, filters as URL parameters, cached heavy computations, links to the response/error section
- docs/architecture.md: server-rendered pages, rows for a slow run/page and the API check
- README: doc map rows for web-ui, pipeline and testing

<sub>10e18c0</sub>

### Web API: schemathesis checks, response models, fixes for malformed input (#5)

- tests/test_api_schema.py: schemathesis over /openapi.json through ASGI. Reads: every GET except the SSE stream, /api/companies included. Writes: POST/PUT/DELETE in positive and negative modes; everything the endpoints write (profile_overrides.json, applications_manual.toml, reports/, cv_out/, last_run.log, progress.*, web_cache.json) goes to tmp; runs start no processes, vacancy lookup and PDF build are stubbed
- response_model for /api/market, /api/reports, /api/sources, GET /api/cv, /api/skills; error codes in OpenAPI via responses=errors(...) (400 / 404 / 409 / 422); a duplicate skill is now 409
- strict input: bool only true/false, a weight is not a bool, an event date only an ISO string
- a lone surrogate ("\ud800") in a note wiped applications_manual.toml: write_text truncated the file and then failed on encoding. The text is now checked before writing (profile_overrides.json too), and surrogates are rejected on input with a 422; the 422 response itself no longer fails to serialize them (was a 500)
- DEL (\x7f) in a note produced invalid TOML: json.dumps does not escape it
- cv_match: the link-stripping regex was O(n^2) on a long word without spaces (65 KB — 9 s), now per token; state/cv_text.json is written only when a new PDF was parsed
- 405 lists every allowed method: GET and POST on /api/cv and /api/skills are separate routes
- schemathesis in requirements-dev.txt, .hypothesis/ in .gitignore; docs: testing.md (schemathesis, running one endpoint), web-ui.md (response models and error codes)

<sub>6c9da6a</sub>

### Company industries and the Companies page (#4)

- jobhunter/industry.py: a vacancy's industry from the company name, title and description (fintech, trading, iGaming, crypto, adtech, devtools, security, e-commerce, travel, edtech, healthtech, gamedev, AI, B2B SaaS, Big Tech, outsourcing, otherwise "other"); weighted patterns with word boundaries against false positives, all patterns of an industry in one regex. Known companies win over the text: a few built-in examples, your own in [industry_companies] (settings.toml, empty by default) or the 4th field of companies.txt; the 5th field — brands, a vacancy under a brand counts for its owner.
- The run saves industries, remote and salary to the archive state/jobs/<date>.json; the HTML report shows the industry as a tag.
- /companies and GET /api/companies: the market per industry (vacancies, remote share, median salary, top technologies) and companies over all archive days with vacancies, last day, application status and board; your own companies without vacancies — to search by hand. All industries by default, ?industry= and "with vacancies only" filters. Cached by the archive, lists and applications.
- docs/industry.md, README (screenshot on demo data, docs table), web-ui, architecture, configuration.
- tests/test_industry.py: positives, false positives, companies.txt marks and brands, summary, page.

<sub>f42ecb9</sub>

### Release notes bot: two merges in a row no longer fail the run (#3)

- release-notes.yml: cancel-in-progress, the newest push supersedes the older run; a push rejected because main moved on exits cleanly, the run for the newer push rebuilds the notes from the whole history
- docs/testing.md: how the bot handles merges in a row

<sub>19b807f</sub>

### mypy in CI: type check on every PR, HttpClient protocol for sources (#2)

- mypy in requirements-dev.txt and [tool.mypy] in pyproject.toml: jobhunter, web, main.py, cvgen.py, tests/conftest.py, check_untyped_defs, pydantic plugin; a "Types (mypy)" step in ci.yml
- http: Response / HttpClient / HttpPostClient protocols (read-only properties, so requests.Response fits); sources take HttpClient, Workday takes HttpPostClient; FakeHttp is checked against the protocol
- cvgen.render: overloads, with pdf=True the PDF path is never None
- web: Runner.start takes a Sequence; CREATE_NEW_PROCESS_GROUP behind an if sys.platform check, so mypy --platform linux passes
- annotations for empty collections; a few real None and shadowing issues (salary bounds, the "market" variable in main.py, the bullets_rest limit in cvgen)
- docs/testing.md: mypy in CI and locally, the HttpClient protocol

<sub>6434636</sub>

### Applications: a typo date before 1970 no longer breaks the page (#1)

- mail.manual_events: datetime.astimezone() for 0001-01-01 raises OSError on Windows (no time zone data before 1970) and ValueError on Linux (shifts into year 0); the event now falls back to the current local offset instead of failing the whole report

<sub>132819e</sub>

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
