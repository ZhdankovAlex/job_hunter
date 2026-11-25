# Tests and checks

[← README](../README.md)

## How to run

```bash
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

```bash
.venv\Scripts\python.exe -m ruff check .
```

```bash
.venv\Scripts\python.exe -m mypy
```

```bash
.venv\Scripts\python.exe -m pytest
```

The tests take 10–20 seconds, almost all of it is schemathesis (see below). No network and no personal files are needed: before any module is imported,
`tests/conftest.py` points the program at the examples — `settings.example.toml` with a few test values (a
fictional home country, relocation countries), `profile.example.toml` and `cv/base.example.toml` — so the result
is the same on your machine and in CI. Tests do not change project files: paths to `state/`, `reports/`,
`cv_out/`, `profile_overrides.json`, `applications_manual.toml` are redirected to a temporary folder. To check:
after `pytest`, `git status` is clean.

## What is checked

| File | What |
|---|---|
| `tests/test_mail.py` | Letter type: application, stage, rejection, offer. Conditional phrases and caveats ("If your profile fits…", "may not be the right fit"), typographic apostrophes, quoted old letters, speed on a long letter, company from the sender and the subject, `[mail.sender_company]` |
| `tests/test_applications_report.py` | Application status and the funnel: a new application after a rejection, cancelled meetings, "declined", rejection after silence, events in the future |
| `tests/test_relevance.py` | The vacancy score: skills and their order by weight, title-only adjustments, outsourcing, Russian language, Go vs "Python not primary", JVM and 8+ years, CV gaps with a floor, freehire data (an EUR range from 60k), location, company priority, skill edits from the UI and broken patterns |
| `tests/test_cv_match.py` | Fit %: key and nice-to-have skills, "Python, Go or Java", years of experience, new terms, "except Django" and "Industry: …" labels are not requirements; the combined technology pattern answers the same as the separate ones |
| `tests/test_sources.py` | Parsing fin.dev (JSON-LD), career pages (the `links` setting, the title from `aria-label`), Teamtailor RSS — on saved pages from `tests/fixtures/`; `[manual_jobs]` entries with `company` / `location`; `Http` and `FakeHttp` fit the `HttpClient` protocol |
| `tests/test_filters.py` | Selection by role, employment type and the presence of a link |
| `tests/test_verify.py` | Full-text checks: on-site, country binding, JVM, Russia-only remote, AI trainer; 7+ and 8+ years are a penalty, not hiding; manual check |
| `tests/test_geo_salary.py` | Relocation and remote within a country (Georgia the country vs the US state, excluded countries, residents only, "remote from anywhere"); salary ranges ("4.5–5.5k EUR", yearly, hourly); grade |
| `tests/test_ats.py` | Greenhouse, Lever, Ashby, Workable, Recruitee, Breezy, Rippling responses; board discovery, empty board, Lever EU, Teamtailor on its own domain, "board gone / moved", saving `ats_mapping.json` without overwriting other edits; vacancy text from BambooHR / Rippling / Breezy pages (limit, broken JSON-LD) |
| `tests/test_freehire.py` | API record → vacancy (utm, salary, preview), prefilter (role, experience, `part_time`), relocation from freehire's markup |
| `tests/test_telegram.py` | Posts of every channel format (markup as on t.me), telegra.ph, reading a channel page, labels and filtering (relocation, hybrid, remote in another country); going through channels: a failing channel, forwarded and broken posts, duplicates, the aggregator link |
| `tests/test_cvgen_report.py` | A CV for a vacancy (headline for the title, one-page version, HTML); the report: sections and staffing in the appendix |
| `tests/test_vacancy_lookup.py` | A vacancy by link: the company from JobPosting, or from the domain when JobPosting names the career site ("Career Hub") |
| `tests/test_api_schema.py` | The whole web API against its OpenAPI schema (schemathesis): no 500s, responses and codes as in the schema, invalid input is rejected — see below |
| `tests/test_main_run.py` | The whole `main.run` without network: one company on Greenhouse via `FakeHttp`, the order of stages, reports and the vacancies JSON; a write outside `tmp_path` fails the test |
| `tests/test_web.py` | Web UI pages open on empty data; the "Skills" cache; reports are served with `Cache-Control: no-cache`; the standard page header and the empty state are rendered |
| `tests/test_styles.py` | One style base: in `app.css` and the reports' CSS a font size is only `var(--fs-*)`, only `tokens.css` defines the palette, every variable is defined, reports embed `tokens.css` in full, the UI loads it before `app.css`; every regular page starts with the `ui.page_head` macro, the block rhythm comes from the spacing scale; buttons and fields share the height `--ctl-h`, field styles live in one place, text on the accent is `--on-accent`, `style=` in templates is only a bar width or `--n`. Theme: the dark palette for `data-theme="dark"` and for the system match, the theme script sits in `<head>` of the UI and of the `reports/` pages before the styles |
| `tests/test_http.py` | A host that does not respond is skipped until the end of the run; success resets the counter |
| `tests/test_http_rules.py` | `Http` rules on a fake clock and session: robots.txt (`*`, `$`, own group, Crawl-delay; no file — allowed, 5xx and network errors — host skipped), 1 request per second per host, retries on 5xx, Retry-After capped at a minute, a host is turned off after three 429s, cancellation by the button |
| `tests/test_stack.py` | The main language of a vacancy: title, requirements block (C++ only there, a "nice to have" line does not count), Python alongside another language, Python only as a nice-to-have |
| `tests/test_freehire_client.py` | The freehire client: disk cache and its lifetime, request limit per run, HTTP error and non-JSON, `meta.ignored_params` stops the run, pages and duplicates across queries, full texts in batches of 10 companies, finding the filter that zeroes the result |
| `tests/test_mail_gmail.py` | Message cache and sync freshness, Gmail without network (1 request/s, `nextPageToken` pages, text/plain vs HTML), reading only new messages and intermediate cache saves, the OAuth token (refresh, revocation, sign-in with a timeout), manual events |
| `tests/test_claude_hooks.py` | Claude Code hooks (`.claude/hooks`): writes into personal files are denied, `*.example.*` files are not, the allow marker works for an hour; ruff and mypy only on the changed files of a temporary git repo, silence when there are no errors |
| `tests/test_release_notes.py` | `RELEASE_NOTES.md` from the git history: grouping by day, bot commits are skipped |
| `tests/test_progress.py` | Run state: stages in order, skipping a stage with the button (only this pid and only a skippable stage), log warnings with the action / code / site group and caps, a traceback for exceptions, a second run in the same process, stage-duration history and ETA, the status line and `watch`, `errors.log` rotation |
| `tests/test_sites_boards.py` | Boards and sites from `sites.py`: pagination and errors (Bolt: repeats and the text only for suitable titles) (a stub page instead of JSON keeps what was collected), Aiven turbo-stream, Habr Career SSR, the Next.js data stream (justjoin.it, remote.com), `__NEXT_DATA__`, JSON-LD (hirehi, `manual_jobs`), Cryptocurrency Jobs RSS, relocate.me, 01.tech |
| `tests/test_aggregators_bigtech.py` | Arbeitnow, Remote OK, Himalayas (pages, salary range, errors); Microsoft, Workday (search by POST), Oracle, Amazon: the order of opening vacancies (home country → remote → Europe → freshest), the details limit, duplicates, broken answers |
| `tests/test_runner.py` | Starting a run from the web UI (processes are fakes): script queue and `--no-*` flags, refusal while a run is going on, "Stop" (Windows and POSIX, a run from the terminal), the daily scheduled run |
| `tests/test_levels.py` | levels.fyi: country by location, Markdown page parsing (monthly median, "Not enough data"), cache for `CACHE_DAYS` and the old cache on failure, the report line |
| `tests/test_tiers_staffing.py` | Vacancy category (Backend / ML / DevOps / Other) and the intermediaries blacklist: names, prefixes, `re:` patterns, outsourcing by the text |

Letters and pages in the tests are made up, but they repeat wordings the program once got wrong. Found a new
bug — first add an example that shows it, then fix it.

## Property-based API check (schemathesis)

`tests/test_api_schema.py` does not list requests by hand. [schemathesis](https://github.com/schemathesis/schemathesis)
(on top of hypothesis) reads `/openapi.json` and generates requests itself: empty and very long strings, unicode,
extra fields, numbers at the `ge`/`le` bounds. The app is called directly through ASGI, no server is needed.
It checks that the server gives no 500, the response body matches `response_model`, the status code is in the schema.

- **Reads** (`test_read_only_api_matches_schema`): every GET except the endless SSE stream
  `/api/progress/stream`, `/api/companies` included. Data — a small vacancy archive, a report and a CV in a
  temp folder; the page cache `state/web_cache.json` is redirected there too.
- **Writes** (`test_write_api_rejects_bad_input`): POST/PUT/DELETE in two modes. Positive — bodies by the schema,
  the server must accept them. Negative — bodies against the schema (wrong type, out of bounds, an empty string
  with `min_length`), the server must reject them with a 4xx. Everything these endpoints write lives in the temp
  folder (the `writes` fixture). A run starts no scripts (`Runner._run` is a stub), and stop without a run
  answers 409 and ends nothing. Reading a vacancy by link is stubbed (always 404), building a PDF too (it needs
  Chrome, and CI has none).
- Some rules cannot be expressed in the schema: "a link **or** a vacancy text", "a mail-only run without mail has
  nothing to run", "pattern is a valid regex". For these POSTs the "a valid body is accepted" check is off, and the
  rules are covered by ordinary tests next to it.
- A new endpoint joins the check by itself. `test_*_endpoints_are_covered` make sure the important ones do not
  drop out of the selection by accident.

What schemathesis has already found (all fixed, each case has a test):

- a lone surrogate `\ud800` (valid JSON) in an application note wiped `applications_manual.toml`:
  `Path.write_text` truncates the file first and then fails on encoding. Now the text is checked before writing,
  and such strings are rejected on input with a 422;
- the same character turned a 422 into a 500: FastAPI echoes the invalid input in the response, and the response
  could not be encoded to UTF-8;
- `DEL` (`\x7f`) in a note gave invalid TOML: `json.dumps` does not escape this character;
- a quadratic regex in `cv_match`: the link filter backtracked from every position of a long word without spaces
  (65 KB — 9 s per vacancy); now it goes token by token;
- lax input types: `true` was taken as a weight of 1, `"yes"` as a bool, the number `0` as an event date in 1970;
- a 405 with an incomplete `Allow` header on paths where GET and POST are different routes (a Starlette quirk).

If the test fails, schemathesis prints a `curl` that reproduces the error. Long bodies are cut in it, so it is
easier to run one endpoint:

```bash
.venv\Scripts\python.exe -m pytest tests/test_api_schema.py -k "write_api and events"
```

Reads go the same way: `-k "read_only and companies"`.

## Writing a new test

- Network — through `FakeHttp` from `tests/conftest.py`: URL → response text, every request is recorded in `.calls`.
  When the answer depends on the query parameters or a POST is needed (paginated APIs, Workday) — `RouteHttp`:
  `route(method, url, params_or_json)` returns the text, `(code, text)` or `None` (404).
- Saved pages go to `tests/fixtures/` and are read with `fixture_text("name")`. Only anonymised ones: no real
  letters, recruiter names or addresses.
- Time — the `now` and `at(days)` fixtures, not `datetime.now()`.
- Data paths (`config.JOBS_ARCHIVE_DIR`, `config.MAIL_CACHE_FILE`, …) — through `monkeypatch` to `tmp_path`.
- A new endpoint — with `response_model` and `responses=errors(...)` listing the codes it returns; schemathesis
  picks it up. A new path constant an endpoint writes to — into the `writes` fixture in
  `tests/test_api_schema.py` at once, otherwise schemathesis touches the real file.
- A regex over long text — check it on 50–100 KB without spaces (there were O(n²) cases taking 9–13 s).

## CI

`.github/workflows/ci.yml`: on every pull request and push to `main` GitHub Actions installs the dependencies and
runs `ruff check .`, `mypy` and `pytest --cov`. Ruff (`[tool.ruff.lint]` in `pyproject.toml`) catches bugs — syntax,
pyflakes, bugbear (`E9`, `F`, `B`) — and keeps the code uniform: import order (`I`), modern Python 3.11 syntax (`UP`:
`datetime.UTC`, no quotes in annotations), redundant branches (`SIM`), traps such as mutable class attributes without
`ClassVar` (`RUF`). `ruff check . --fix` repairs almost all of it. `RUF001`–`RUF003` are off (Cyrillic, "—" and "…"
in texts are normal for the project), and so is `SIM905` (long word lists read better as `"a b c".split()` than as a
list of literals). The formatter (`ruff format`) is not enabled.

Coverage is measured for `jobhunter` and `web` (`[tool.coverage]` in `pyproject.toml`); the run fails below
`fail_under` (82%, just under the current total of 84.6%; `web/__main__.py`, the server launcher, is in `omit`). Raise the floor when tests are added. The per-module table is in the summary of every CI run
(Actions → the run → Summary); history, a per-file view and a coverage comment on each pull request are on
[Codecov](https://codecov.io/gh/ZhdankovAlex/job_hunter) (`codecov.yml`: the total may not drop by more than 1%;
the coverage of changed lines is shown but does not fail the PR). Locally, with the
list of uncovered lines:

```bash
.venv\Scripts\python.exe -m pytest --cov
```

`.pre-commit-config.yaml` runs ruff, mypy and basic file checks (TOML/YAML syntax, merge markers, trailing spaces)
before every commit; enable it once with `pre-commit install`. `.github/dependabot.yml` opens a weekly PR with
updated Python dependencies and a monthly one for GitHub Actions; CI checks each of them.

mypy checks `jobhunter`, `web`, `main.py`, `cvgen.py`, `tests/conftest.py` and `.claude/hooks`, including the bodies
of unannotated functions (`check_untyped_defs`); the settings are in `[tool.mypy]` in `pyproject.toml`. The key
modules — `jobhunter/http.py`, `models.py`, `stack.py`, `industry.py`, `companies.py` and all of `web/` — run in strict
mode (`[[tool.mypy.overrides]]`): every function is annotated (`disallow_untyped_defs`, `disallow_incomplete_defs`),
`dict` / `list` carry element types (`disallow_any_generics`), and no `Any` is returned from a function with a concrete
return type (`warn_return_any`). In `web/app.py`, endpoints without a response model of their own set
`response_model=None`: otherwise FastAPI would make the return annotation the response model and start validating
the response. CI runs on Linux, so
platform-dependent code (`subprocess.CREATE_NEW_PROCESS_GROUP` exists only on Windows) is checked locally with:

```bash
.venv\Scripts\python.exe -m mypy --platform linux
```

Sources take not a concrete `Http` but the `HttpClient` protocol (`jobhunter/http.py`): anything with a `get()` of
the same signature fits. `FakeHttp` in `tests/conftest.py` does not inherit from it, but mypy checks it against the
protocol: if the fake drifts from the real client, the type check fails, not a run in production.

`.github/workflows/release-notes.yml`: after a push to `main` rebuilds `RELEASE_NOTES.md` from the git history
and commits it back if it changed (title `RELEASE_NOTES.md: update`) — run `git pull --rebase` before the next push.
Two merges in a row do not fail it: a newer push cancels the older run, and a run whose push is rejected because
`main` moved on exits cleanly — the run for the newer push rebuilds the notes from the whole history.

## Environment variables

| Variable | What it does |
|---|---|
| `JOBHUNTER_SETTINGS` | Another settings file instead of `settings.toml` / `settings.example.toml` |
| `JOBHUNTER_PROFILE` | Another skills profile instead of `profile.toml` / `profile.example.toml` |
| `JOBHUNTER_CV_BASE` | Another CV base instead of `cv/base.toml` / `cv/base.example.toml` |
| `JOBHUNTER_NO_WARM` | The web UI does not precompute "CV vs market" and "Skills" in the background at start (tests need this) |
