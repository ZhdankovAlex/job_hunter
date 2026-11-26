# Files and settings

[← README](../README.md)

## Entry points

| File | Purpose |
|---|---|
| `main.py` | Collect vacancies and build the report (see [pipeline.md](pipeline.md)) |
| `applications.py` | Application statuses from Gmail → `reports/applications.html` |
| `cvgen.py` | A CV for a vacancy from the command line: `--text/--company/--title` → MD, `--render` → PDF |
| `run_daily.bat` | Daily run on Windows: `applications.py`, then `main.py`, output to `last_run.log` |
| `web/` | Local web UI: `python -m web` |

## Settings (edited by hand)

| File | What is inside |
|---|---|
| `CLAUDE.md`, `.claude/` | Claude Code setup: rules, skills (`/new-source`, `/prep-commit`) and hooks (`.claude/settings.json`, `.claude/hooks/`) — see [claude-code.md](claude-code.md) |
| `requirements-lock.in`, `requirements-lock.txt` | Exact versions of all dependencies — CI installs them; `pip-compile` builds the `.txt` from the `.in` |
| `.github/workflows/` | CI (`ci.yml`) and rebuilding `RELEASE_NOTES.md` after a push to main (`release-notes.yml`) |
| `RELEASE_NOTES.md` | What changed and when — built automatically, not edited by hand |
| `settings.toml` | Almost everything: home country, locations and relocation, freehire queries, sources, Telegram, `[manual_jobs]`, mail company aliases, career pages, thresholds |
| `companies.txt` | Companies to check: `Company \| priority 0–3 \| note \| industries \| brands` ([industry.md](industry.md)) |
| `profile.toml` | Profile for the relevance score: skills with weights and patterns, adjustments |
| `cv/base.toml` | The CV base: experience, projects, skills, interview stories, cover letter template — every CV and letter is built from it |
| `applications_manual.toml` | Applications not from mail (Telegram, LinkedIn, calls): events per company |
| `staffing_blacklist.txt` | Staffing / outsourcing companies — moved to a separate report appendix |
| `ats_mapping.json` | Cache company → ATS and slug, created automatically; can be edited (see [sources.md](sources.md)) |
| `gmail_client.json` | Google OAuth client (a secret, in `.gitignore`) |

## Personal files and examples

Personal files never go to git (`.gitignore`): `settings.toml`, `companies.txt`, `profile.toml`,
`profile_overrides.json`, `staffing_blacklist.txt`, `applications_manual.toml`, `cv/base.toml`, `cv/*.pdf`,
`ats_mapping.json`, logs, `state/`, `reports/`, `cv_out/`, Google credentials.

The repository has examples: `settings.example.toml`, `companies.example.txt`, `profile.example.toml`,
`staffing_blacklist.example.txt`, `cv/base.example.toml`, `applications_manual.example.toml`. While your own
file does not exist, the program reads the example (`config.local_or_example`). To customise, copy the example
without `.example`:

```bash
copy settings.example.toml settings.toml
```

`applications_manual.toml` is created automatically by the first event added in the web UI.

## Key settings

```toml
[home]
country = "Portugal"          # vacancies here (office, hybrid, remote) count like worldwide remote
iso = "PT"                    # for country searches on Himalayas / freehire
places = ["Lisbon", "Porto"]  # cities and other spellings

relocation_countries = ["Netherlands", "Germany", "Spain"]
any_country = true            # relocation anywhere, but only when the employer pays for it

[filters]
hide_russia_only = false      # hide "remote only from Russia" vacancies (Russian-language sources)

[sources]
disabled = ["habr career", "hirehi", "aiven", "bolt", …]   # site adapters not to call

[telegram]
enabled = false
channels = ["pythonrabota", "job_python"]

[gamification]
weekly_goal = 8               # the weekly application goal on the Progress page
```

All keys are commented in `settings.example.toml`.

## Checking `settings.toml`

Keys, types and defaults of `settings.toml` are described by the model in `jobhunter/settings.py` (Pydantic,
`extra="forbid"`, `strict=True`). A typo in a key or a wrong type is an error with the key path, not a silent
default:

```bash
.venv\Scripts\python.exe -m jobhunter.settings --check
```

```text
Settings error: settings.toml:
  max_age_days: Input should be a valid integer
  freehire.max_reqests: unknown key (a typo?)
```

Without an argument it checks `config.SETTINGS_FILE` (`settings.toml`, or the example if there is none); a path
can be passed. Exit code: 0 — the file is fine, 1 — errors. Extra keys are allowed only in `[[freehire.queries]]`:
these are freehire API parameters, the API checks their names itself (`meta.ignored_params` stops the run).

The file is read on the first `settings.get()`, once per process; `settings.use(Settings(...))` puts ready settings
in place without a file. Modules take `settings.get().<field>` inside functions, so `use()` changes the behaviour
of all of them at once. `geo.rules()` builds the country patterns and company sets — once per settings object.
The exception is `[home]`: `config` reads it on import, because the home country is part of many patterns
compiled there and in `verify` / `telegram`.

## Code (`jobhunter/`)

| Module | What it does |
|---|---|
| `config.py` | Paths, home country, keywords, location and language regexes |
| `settings.py` | The `settings.toml` model: loading (`get`, `use`), the `--check` check |
| `geo.py` | Location and relocation rules; `rules()` — patterns and company sets from the settings |
| `http.py` | HTTP client: 1 request/s per host, robots.txt check |
| `release_notes.py` | `RELEASE_NOTES.md` from the git history (`python -m jobhunter.release_notes`) |
| `progress.py` | Run stages, `state/progress.json`, the terminal progress line, pipeline viewer |
| `companies.py` | Company list and ATS slug candidates |
| `ats.py` | Public JSON APIs of ATS boards (Greenhouse, Lever, Ashby, Workable, …) and slug discovery |
| `bigtech.py` | Career sites of large companies: Microsoft, Workday, Oracle, Amazon |
| `aggregators.py`, `sites.py` | Arbeitnow, Remote OK, Himalayas; job boards and company sites without an ATS |
| `freehire.py` | freehire.me |
| `telegram.py` | Telegram channels via the web preview, a parser per channel format |
| `filters.py`, `verify.py`, `language.py` | Selection: role, location, primary language, full-text checks |
| `reasons.py` | Codes of reject reasons (`Reject`) and marks (`Mark`): the group on the pipeline chart, the order in "Rejected" and the score go by the code, the shown text can be edited |
| `staffing.py` | Staffing / outsourcing blacklist |
| `keys.py` | Normalised keys: vacancy URL, IDs in a URL, company name, ATS board from a link |
| `applied.py` | Vacancies and companies you already applied to (`applications_manual.toml`) |
| `mail.py`, `applications_report.py` | Gmail (read-only), letter parsing, the applications page |
| `gamification.py` | The Progress page (`reports/progress.html`): rank and XP, streak, the next step of the week, wins, badges |
| `cv_match.py`, `relevance.py`, `tiers.py`, `salary.py`, `levels.py` | Fit % to the CV, score, category, grade, salary ranges, levels.fyi |
| `cvgen.py` | CV builder: picks bullets for a vacancy, one-page version, Markdown → HTML → PDF via headless Chrome |
| `cover_letter.py` | Cover letter for a vacancy from `cv/base.toml` (`[summary]`, `[cover]`) |
| `vacancy_lookup.py` | A vacancy by link: from saved reports or from the page (JobPosting) |
| `coverage.py` | Why a source gave no vacancies to the report — for the "Sources" page |
| `outreach.py` | LinkedIn people-search links, contacts from the text, message drafts |
| `interview_prep.py`, `market_check.py` | Interview preparation, CV vs market |
| `report.py`, `html_report.py` | Markdown and HTML reports |
| `models.py` | `Job`, `Company`, date and HTML parsing |
| `fsutil.py` | Atomic writes of state files (a temporary file + `os.replace`) |

## Data between runs (`state/`, in `.gitignore`)

State files and `cv/base.toml` are written atomically (`jobhunter/fsutil.py`: a temporary file next to it and
`os.replace`): stopping or crashing mid-write leaves the previous version, not an empty file. A broken
`seen.json` or `ats_mapping.json` does not fail the run: a warning in the log and empty state.

| File | What it keeps |
|---|---|
| `progress.json` | State of the current / last run |
| `progress_history.json` | Stage durations of recent runs — for the time-left estimate |
| `progress_control.json` | A request from the web UI to skip a stage of the running run |
| `jobs/<date>.json` | Vacancies of every report with text — for CVs from old reports |
| `jobs_latest.json` | Vacancies of the last report with text — for `cvgen.py` |
| `sources_latest.json` | How many vacancies every source gave in the last run, errors |
| `freehire_latest.json`, `freehire_jobs.json`, `freehire_cache/` | freehire queries and responses |
| `seen.json` | When a vacancy first appeared (the 🆕 mark) |
| `rejected_latest.tsv` | Everything the filters dropped, with the reason and a quote |
| `mail_cache.json`, `mail_sync.json`, `gmail_token.json` | Mail cache, last sync, Gmail token |
| `salary_samples.json`, `levels_cache.json` | Salary ranges base, levels.fyi cache |
| `web_cache.json` | Precomputed "CV vs market" and "Skills" pages of the web UI |
| `cv_text.json` | CV text for matching |

`reports/`, `cv_out/`, `errors.log`, `errors.prev.log`, `last_run.log` are run results; they can be deleted, the
next run rebuilds them.
