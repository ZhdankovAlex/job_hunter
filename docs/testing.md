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

The tests take about a second. No network and no personal files are needed: before any module is imported,
`tests/conftest.py` points the program at the examples — `settings.example.toml` with a few test values (a
fictional home country, relocation countries), `profile.example.toml` and `cv/base.example.toml` — so the result
is the same on your machine and in CI. Tests do not touch `state/`: paths to data files are redirected to a
temporary folder.

## What is checked

| File | What |
|---|---|
| `tests/test_mail.py` | Letter type: application, stage, rejection, offer. Conditional phrases and caveats ("If your profile fits…", "may not be the right fit"), typographic apostrophes, quoted old letters, speed on a long letter, company from the sender and the subject, `[mail.sender_company]` |
| `tests/test_applications_report.py` | Application status and the funnel: a new application after a rejection, cancelled meetings, "declined", rejection after silence, events in the future |
| `tests/test_cv_match.py` | Fit %: key and nice-to-have skills, "Python, Go or Java", years of experience, new terms; the combined technology pattern answers the same as the separate ones |
| `tests/test_sources.py` | Parsing fin.dev (JSON-LD), career pages (the `links` setting, the title from `aria-label`), Teamtailor RSS — on saved pages from `tests/fixtures/`; `[manual_jobs]` entries with `company` / `location`; `Http` and `FakeHttp` fit the `HttpClient` protocol |
| `tests/test_filters.py` | Selection by role, employment type and the presence of a link |
| `tests/test_verify.py` | Full-text checks: on-site, country binding, JVM, Russia-only remote, AI trainer; 7+ and 8+ years are a penalty, not hiding; manual check |
| `tests/test_geo_salary.py` | Relocation and remote within a country (Georgia the country vs the US state, excluded countries, residents only, "remote from anywhere"); salary ranges ("4.5–5.5k EUR", yearly, hourly); grade |
| `tests/test_ats.py` | Greenhouse, Lever, Ashby, Workable, Recruitee, Breezy, Rippling responses; board discovery, empty board, Lever EU, Teamtailor on its own domain, "board gone / moved", saving `ats_mapping.json` without overwriting other edits |
| `tests/test_freehire.py` | API record → vacancy (utm, salary, preview), prefilter (role, experience, `part_time`), relocation from freehire's markup |
| `tests/test_telegram.py` | Posts of every channel format (markup as on t.me), telegra.ph, reading a channel page, labels and filtering |
| `tests/test_cvgen_report.py` | A CV for a vacancy (headline for the title, one-page version, HTML); the report: sections and staffing in the appendix |
| `tests/test_web.py` | Web UI pages open on empty data; the "Skills" cache; reports are served with `Cache-Control: no-cache` |
| `tests/test_http.py` | A host that does not respond is skipped until the end of the run; success resets the counter |
| `tests/test_release_notes.py` | `RELEASE_NOTES.md` from the git history: grouping by day, bot commits are skipped |

Letters and pages in the tests are made up, but they repeat wordings the program once got wrong. Found a new
bug — first add an example that shows it, then fix it.

## Writing a new test

- Network — through `FakeHttp` from `tests/conftest.py`: URL → response text, every request is recorded in `.calls`.
- Saved pages go to `tests/fixtures/` and are read with `fixture_text("name")`. Only anonymised ones: no real
  letters, recruiter names or addresses.
- Time — the `now` and `at(days)` fixtures, not `datetime.now()`.
- Data paths (`config.JOBS_ARCHIVE_DIR`, `config.MAIL_CACHE_FILE`, …) — through `monkeypatch` to `tmp_path`.

## CI

`.github/workflows/ci.yml`: on every pull request and push to `main` GitHub Actions installs the dependencies and
runs `ruff check .`, `mypy` and `pytest`. Ruff checks only what catches real bugs (syntax, pyflakes, bugbear), not
style: the settings are in `pyproject.toml`.

mypy checks `jobhunter`, `web`, `main.py`, `cvgen.py` and `tests/conftest.py`, including the bodies of unannotated
functions (`check_untyped_defs`); the settings are in `[tool.mypy]` in `pyproject.toml`. CI runs on Linux, so
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
