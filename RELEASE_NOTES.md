# Release notes

Built from the git history automatically after a push to main (`python -m jobhunter.release_notes`). Newest changes first.

## 2026-11-19

### Docs: how the project is developed with Claude Code, CLAUDE.md with the working rules (#18)

- CLAUDE.md: the rules every Claude Code session gets — hard limits (nothing is sent out, no LinkedIn automation, robots.txt and 1 request per second via jobhunter/http.py), checks, tests without network and personal files, documentation in the same commit
- docs/claude-code.md: what lives where, what CLAUDE.md covers, the project skills, both hooks and how to bypass the protection, built-in skills and subagents, why there are so few hooks and skills
- docs: links from README.md and architecture.md; configuration.md names CLAUDE.md, the skills and the hooks

<sub>f25a6f0</sub>

### Claude Code hooks: ruff and mypy at the end of a turn, personal file protection, a READY / NOT READY report in /prep-commit (#17)

- Stop hook (.claude/hooks/stop_check.py): ruff check and mypy only on the changed and new .py files (git diff HEAD plus untracked), mypy within [tool.mypy] files. Errors go back to the agent, which fixes them before finishing the turn; no errors, no output. pytest is not run, it is too slow
- PreToolUse hook on Write/Edit (.claude/hooks/protect_files.py): writes into settings.toml, cv/base.toml, applications_manual.toml, profile.toml and companies.txt are denied with an explanation; the *.example.* files stay editable. If the user asked for the edit, the marker state/allow_protected_edit lifts the block for an hour
- The hook command finds the interpreter in .venv/bin (Linux, macOS) or .venv/Scripts (Windows)
- /prep-commit: the checks are a PASS/FAIL table (tests, ruff, mypy, a secrets scan, extra files in the diff) and a READY / NOT READY verdict; no message draft while NOT READY
- mypy also checks the hook scripts; tests/test_claude_hooks.py covers both hooks on a temporary git repo, without network
- docs: testing.md — the new test file

<sub>6a4acdf</sub>

### CV match: "except Django" and industry labels are not requirements, Russian scrapers, the employer instead of the career site (#16)

- cv_match: a technology after "except", "excluding", "other than", "not" (or Russian «кроме», «за исключением») in the same part of a sentence is not a requirement. Was: "any framework except django" put Django among the gaps and lowered Fit. Russian «не» and «без» are not negations: «не менее 3 лет Python» still requires Python
- cv_match: field labels "Industry: …", "Location: …" give no out-of-dictionary terms; industries (Sports, Betting, Gaming, Casino) are generic words. Was: "Industry: Sports Trading" added Sports to the missing skills
- cvgen: the scraping topic recognises Russian vacancies: «скраперы» / «скрапперы», «краулеры», «парсеры сайтов». Was: the Projects section did not appear although scrapers were a plus in the vacancy
- vacancy_lookup: when JobPosting names the career site ("Career Hub", "Careers") or is empty, the company comes from the domain: career.acme.inc → Acme
- tests: one test per bug, new tests/test_vacancy_lookup.py; CV topics in linear time on a 100 KB Russian word without spaces (the first version of the parser pattern took 18 s on it)
- docs: scoring.md — negation and field labels in Fit, cv.md — Russian Projects topics and the company by link, testing.md — the new test file

<sub>b770513</sub>

## 2026-11-18

### CV: a Projects section for pet projects, only for ATS and scraping vacancies (#15)

- cvgen: a [[projects]] section in the base; in the CV it goes after experience and only when the vacancy has a topic from the project's requires. New topic scraping: ATS, scrapers, crawlers, job boards. Other CVs, AI roles and the base CVs have no pet project, so it does not mix with work experience. The one-page variant keeps one project bullet
- cvgen: the AI thesis line is optional: without thesis_ai in the base an AI vacancy broke the build
- cv/base.example.toml: an example project; docs/cv.md: how the section is chosen
- tests/test_cvgen_report.py: when the section is there and when not; topic regexes stay linear on 100 KB

<sub>1ada2d6</sub>

### Report: numeric column headers sit above their numbers (#14)

- in the HTML report numbers in cells are right-aligned while headers were left-aligned: in the wide "Most missing skills" table the key-skill counts stood under the Nice-to-have header, and the "Key (not in the CV)" column looked empty. Headers of numeric columns are right-aligned now; the same for "Score" and "Raw score"
- tests/test_cvgen_report.py: in every report table a numeric column has a header with the same alignment

<sub>0bb11e5</sub>

### Bump the python group with 14 updates (#13)

Updates the requirements on [requests](https://github.com/psf/requests), [google-auth](https://github.com/googleapis/google-cloud-python), [pypdf](https://github.com/py-pdf/pypdf), [google-auth-oauthlib](https://github.com/googleapis/google-cloud-python), [fastapi](https://github.com/fastapi/fastapi), [uvicorn](https://github.com/Kludex/uvicorn), [jinja2](https://github.com/pallets/jinja), [pytest](https://github.com/pytest-dev/pytest), [pytest-cov](https://github.com/pytest-dev/pytest-cov), [pre-commit](https://github.com/pre-commit/pre-commit), [httpx](https://github.com/encode/httpx), [ruff](https://github.com/astral-sh/ruff), [mypy](https://github.com/python/mypy) and [schemathesis](https://github.com/schemathesis/schemathesis) to permit the latest version.

Updates `requests` to 2.34.2

Updates `google-auth` to 2.62.2

Updates `pypdf` to 7.1.0

Updates `google-auth-oauthlib` to 1.5.0

Updates `fastapi` to 0.163.1

Updates `uvicorn` to 0.54.0

Updates `jinja2` to 3.1.6

Updates `pytest` to 9.2.0

Updates `pytest-cov` to 7.1.0

Updates `pre-commit` to 4.7.0

Updates `httpx` to 0.28.1

Updates `ruff` to 0.17.5

Updates `mypy` to 2.4.1

Updates `schemathesis` to 4.33.4

--- updated-dependencies:

...

- [Release notes](https://github.com/psf/requests/releases)
- [Changelog](https://github.com/psf/requests/blob/main/HISTORY.md)
- [Commits](https://github.com/psf/requests/compare/v2.31.0...v2.34.2)
- [Release notes](https://github.com/googleapis/google-cloud-python/releases)
- [Changelog](https://github.com/googleapis/google-cloud-python/blob/main/packages/google-cloud-documentai/CHANGELOG.md)
- [Commits](https://github.com/googleapis/google-cloud-python/compare/google-auth-v2.48.0...google-auth-v2.62.2)
- [Release notes](https://github.com/py-pdf/pypdf/releases)
- [Changelog](https://github.com/py-pdf/pypdf/blob/main/CHANGELOG.md)
- [Commits](https://github.com/py-pdf/pypdf/compare/4.0.0...7.1.0)
- [Release notes](https://github.com/googleapis/google-cloud-python/releases)
- [Changelog](https://github.com/googleapis/google-cloud-python/blob/main/packages/gcp-sphinx-docfx-yaml/CHANGELOG.md)
- [Commits](https://github.com/googleapis/google-cloud-python/compare/google-auth-oauthlib-v1.2.4...google-auth-oauthlib-v1.5.0)
- [Release notes](https://github.com/fastapi/fastapi/releases)
- [Commits](https://github.com/fastapi/fastapi/compare/0.115.0...0.163.1)
- [Release notes](https://github.com/Kludex/uvicorn/releases)
- [Changelog](https://github.com/Kludex/uvicorn/blob/main/docs/release-notes.md)
- [Commits](https://github.com/Kludex/uvicorn/compare/0.32.0...0.54.0)
- [Release notes](https://github.com/pallets/jinja/releases)
- [Changelog](https://github.com/pallets/jinja/blob/main/CHANGES.rst)
- [Commits](https://github.com/pallets/jinja/compare/3.1.0...3.1.6)
- [Release notes](https://github.com/pytest-dev/pytest/releases)
- [Changelog](https://github.com/pytest-dev/pytest/blob/main/CHANGELOG.rst)
- [Commits](https://github.com/pytest-dev/pytest/compare/8.0.0...9.2.0)
- [Changelog](https://github.com/pytest-dev/pytest-cov/blob/master/CHANGELOG.rst)
- [Commits](https://github.com/pytest-dev/pytest-cov/compare/v5.0.0...v7.1.0)
- [Release notes](https://github.com/pre-commit/pre-commit/releases)
- [Changelog](https://github.com/pre-commit/pre-commit/blob/main/CHANGELOG.md)
- [Commits](https://github.com/pre-commit/pre-commit/compare/v3.7.0...v4.7.0)
- [Release notes](https://github.com/encode/httpx/releases)
- [Changelog](https://github.com/encode/httpx/blob/master/CHANGELOG.md)
- [Commits](https://github.com/encode/httpx/compare/0.27.0...0.28.1)
- [Release notes](https://github.com/astral-sh/ruff/releases)
- [Changelog](https://github.com/astral-sh/ruff/blob/main/CHANGELOG.md)
- [Commits](https://github.com/astral-sh/ruff/compare/0.6.0...0.17.5)
- [Changelog](https://github.com/python/mypy/blob/master/CHANGELOG.md)
- [Commits](https://github.com/python/mypy/compare/v1.11...v2.4.1)
- [Release notes](https://github.com/schemathesis/schemathesis/releases)
- [Changelog](https://github.com/schemathesis/schemathesis/blob/master/CHANGELOG.md)
- [Commits](https://github.com/schemathesis/schemathesis/compare/v4.0.0...v4.33.4)
- dependency-name: requests dependency-version: 2.34.2 dependency-type: direct:production dependency-group: python
- dependency-name: google-auth dependency-version: 2.62.2 dependency-type: direct:production dependency-group: python
- dependency-name: pypdf dependency-version: 7.1.0 dependency-type: direct:production dependency-group: python
- dependency-name: google-auth-oauthlib dependency-version: 1.5.0 dependency-type: direct:production dependency-group: python
- dependency-name: fastapi dependency-version: 0.163.1 dependency-type: direct:production dependency-group: python
- dependency-name: uvicorn dependency-version: 0.54.0 dependency-type: direct:production dependency-group: python
- dependency-name: jinja2 dependency-version: 3.1.6 dependency-type: direct:production dependency-group: python
- dependency-name: pytest dependency-version: 9.2.0 dependency-type: direct:development dependency-group: python
- dependency-name: pytest-cov dependency-version: 7.1.0 dependency-type: direct:development dependency-group: python
- dependency-name: pre-commit dependency-version: 4.7.0 dependency-type: direct:development dependency-group: python
- dependency-name: httpx dependency-version: 0.28.1 dependency-type: direct:development dependency-group: python
- dependency-name: ruff dependency-version: 0.17.5 dependency-type: direct:development dependency-group: python
- dependency-name: mypy dependency-version: 2.4.1 dependency-type: direct:development dependency-group: python
- dependency-name: schemathesis dependency-version: 4.33.4 dependency-type: direct:development dependency-group: python

<sub>21f3529</sub>

### Bump actions/setup-python from 5 to 7 (#12)

Bumps [actions/setup-python](https://github.com/actions/setup-python) from 5 to 7.

--- updated-dependencies:

...

- [Release notes](https://github.com/actions/setup-python/releases)
- [Commits](https://github.com/actions/setup-python/compare/v5...v7)
- dependency-name: actions/setup-python dependency-version: '7' dependency-type: direct:production update-type: version-update:semver-major

<sub>486cf79</sub>

### Bump actions/checkout from 4 to 7 (#11)

Bumps [actions/checkout](https://github.com/actions/checkout) from 4 to 7.

--- updated-dependencies:

...

- [Release notes](https://github.com/actions/checkout/releases)
- [Changelog](https://github.com/actions/checkout/blob/main/CHANGELOG.md)
- [Commits](https://github.com/actions/checkout/compare/v4...v7)
- dependency-name: actions/checkout dependency-version: '7' dependency-type: direct:production update-type: version-update:semver-major

<sub>fe1769c</sub>

### Design decisions in the docs, coverage floor in CI, pre-commit hooks and Dependabot (#10)

- docs/architecture.md: new section "Design decisions and trade-offs" — the HttpClient protocol and the FakeHttp checked by mypy, registries of sites and ATS, adapters as an anti-corruption layer over one Job, validation at the API boundary, one responsibility per web module; what is left out on purpose (no DB, no full DDD, global config paths) and the known debt (report renderers, sites/progress coverage)
- CI: pytest --cov; [tool.coverage] in pyproject.toml measures jobhunter and web, the run fails below 68% (now 69.8%)
- .pre-commit-config.yaml: ruff, mypy from the project's environment, TOML/YAML syntax, merge markers, trailing spaces and end of files (settings.example.toml lost a trailing blank line); ats_mapping.json is left alone, the program rewrites it
- .github/dependabot.yml: weekly grouped PR for pip, monthly for GitHub Actions
- README: CI, Python, mypy, ruff and license badges; a link to the design decisions; pre-commit install
- docs/testing.md: coverage, pre-commit and Dependabot; .gitignore: .coverage, htmlcov/
- requirements-dev.txt: pytest-cov, pre-commit

<sub>f554c5d</sub>

### Refactoring after the code review: web/app.py split into modules, main.run into stages, no import cycles (#9)

- web/app.py (1154 → 672 lines): Pydantic models moved to web/models.py, starting runs and the daily schedule to web/runner.py, the cache of slow pages to web/cache.py. Runner takes the project folder, the log and on_finish in its constructor, so a test swaps the log without patching a module global; "Skills" writes its cache through cache.put instead of by hand. The OpenAPI schema is unchanged (compared byte for byte before and after)
- main.run (cyclomatic complexity 119) is split into stage functions over a shared RunContext; the order of progress stages and the behaviour stay the same. New tests/test_main_run.py runs the whole pipeline without network (FakeHttp, one company on Greenhouse): stage order, reports and the vacancies JSON, any write outside tmp fails the test
- the "main language of the stack" check moved from filters to jobhunter/stack.py: the filters ↔ verify import cycle (worked around with a local import) is gone
- docs: architecture.md — web/ modules, stack, main.py stages; web-ui.md — files in web/; testing.md — the new run test

<sub>4eb1a8c</sub>

### Tests for the vacancy score: relevance coverage from 39% to 99% (#8)

- tests/test_relevance.py: Profile.score and location_points on a small profile.toml of its own with "telling" weights — the total shows which adjustment fired. Skills and their order by weight, title-only adjustments, outsourcing, Russian language (share of Cyrillic or a mark), Go vs "Python not primary" (they do not add up), JVM and 8+ years, language marks, CV gaps with the cv_missing_cap floor, a freehire EUR range from 60k (year / month / hour), company priority, every location branch, base_score without penalties, skill edits from the UI, a broken pattern and a broken overrides file
- the core of the score was only tested indirectly, through cv_match
- docs: testing.md — the new file in the table

<sub>59b9c97</sub>

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
