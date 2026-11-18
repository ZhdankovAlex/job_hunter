# Web UI

[← README](../README.md)

```bash
.venv\Scripts\python.exe -m web
```

Opens `http://127.0.0.1:8765/` in the browser (`--port N`, `--no-browser`). The server listens on the local
address only: reports contain mail, applications and contacts. API docs — `http://127.0.0.1:8765/docs`.
The interface is in Russian.

| Page | What is there |
|---|---|
| **Pipeline** | A live run: stages with bars, "processing now", sources in progress with seconds, ETA of the stage and the whole run, counters, "what we reject" and "where vacancies come from" charts, an event feed of the whole run with counters on its filters (found / rejected with a reason / source totals), warnings grouped as "🛠 needs action" (a board is gone, a company has no board, a list was cut by a limit), "🐞 code problems" (a response format changed, exceptions) and "🌐 site failures" (HTTP errors, SSL, timeouts — usually pass by themselves). Buttons: run everything (like `run_daily.bat`), vacancies only, mail only, "⏹ Stop run" |
| **Reports** | All `reports/jobs_*.html` by date; any opens inside the page, with a link to the Markdown |
| **Applications** | `reports/applications.html`, an "update mail" button and a "＋ Event" form (applied / stage / rejected / offer / note) — appends a block to `applications_manual.toml` and rebuilds the page from the mail cache at once. The "✅ applied" button in the report opens the form pre-filled; the vacancy link is remembered and later reports mark it "🔁" |
| **CV** | A CV for any vacancy: by link (from a report or any) or by text, full or one page; "why these bullets"; the list of built CVs (see [cv.md](cv.md)). Until there is a result, the form takes the whole width |
| **Cover letter** | A cover letter for a vacancy ("✉️" in the report, a link or pasted text): "short, bulleted" or "full, in paragraphs"; an editable text with a copy button, "why these points", which key skills you lack (see [cv.md](cv.md#cover-letters)) |
| **Interview prep** | For a vacancy ("🎤" in the report, a link, or pasted text for a vacancy without a link): tell-me-about-yourself in English, requirements with proving bullets, gaps, stories for behavioural questions, an HR checklist with the levels.fyi median, questions to the company |
| **CV vs market** | A local ATS checker over the day's vacancies: market technologies and where they are in your CV, vacancy titles vs your headline, bullet hygiene, conclusions |
| **Companies** | Companies by industry over all archive days: the market per industry (vacancies, remote share, median salary, top technologies), then companies with vacancies, last day, application status and board; your own companies without vacancies — to search by hand. Filter `?industry=` (all by default), "with vacancies only" (see [industry.md](industry.md)) |
| **Skills** | Profile skills with weights and the share of vacancies: on / off, weight (negative is a penalty), your own skills; edits go to `profile_overrides.json` on top of `profile.toml` and apply from the next run |
| **Sources** | Every source of the last run: kind, ATS and slug, how many vacancies, error; **why a source gave no vacancies to the report** (`jobhunter/coverage.py`): request error, marked VERIFY in `companies.txt`, own page without an ATS, board not found (with the date of the next attempt), empty board, no vacancies, none matching in the reports of the last 14 days. The table of all sources is collapsed by default (the state is remembered); filters by kind and by reason, search; the "… without vacancies in the report" badge opens the table with that filter. Telegram channels; freehire queries with name, parameters and counts |

**Skipping stages.** In advance — "no: mail / Telegram / freehire / aggregators" checkboxes next to the run
buttons (mail — do not run `applications.py`, the rest are `--no-…` flags for `main.py`; the checkboxes are
remembered in the browser). On the fly — the "⏭ Skip stage" button for the freehire, sources, Telegram, CV and
mail stages: the UI writes a request to `state/progress_control.json`, the stage loop checks it between steps
(`progress.skip_requested()`) and stops within a second or two; what was collected goes on, the stage is marked
"skipped by hand at N of M". On the sources stage the button also aborts requests that are already running
(`http.cancel`), so a long career page does not hold the run.

**Stopping a run.** "⏹ Stop run" (`POST /api/runs/current/stop`) ends the running script together with its
child processes and does not start the next script in the queue (in "run everything" — mail, then vacancies).
What this run collected is lost; the reports of the previous run stay.

A run started from the UI is a child process of the server in its own process group: Ctrl+C in the server window
does not stop it, but **restarting or closing the server interrupts a running run** — restart the server when
nothing is running. Only one run at a time — the UI knows it even if the run was started from a terminal or a
scheduler (checked via `state/progress.json` and whether the process is alive). Output goes to `last_run.log`.

**Speed.** "CV vs market", "Companies" and "Skills" are computed in the background when the server starts and after each
run and kept in `state/web_cache.json` (keyed by the modification time of vacancies, CVs and letters): after a
restart every tab opens in a fraction of a second. In "Skills" the cache is per pattern: changing a weight does not
recompute, a new skill is counted alone. `JOBHUNTER_NO_WARM=1` turns the background computation off.

Code: `web/app.py` (FastAPI: pages and API), `web/templates/` (Jinja2), `web/static/` (CSS and JS without a build).
API: `GET /api/progress`, `GET /api/progress/stream` (SSE), `POST /api/runs`, `POST /api/runs/current/skip`,
`POST /api/runs/current/stop`, `GET /api/reports`, `GET /api/sources`, `POST /api/cv`, `GET /api/cv`, `POST /api/cover`,
`GET /api/market`, `GET /api/companies`, `GET|POST /api/skills`, `PUT|DELETE /api/skills/{name}`, `POST /api/applications/events`, `POST /api/prep` — described in `/docs`.

**Responses.** Read endpoints have a `response_model` (Pydantic): the structure is visible in `/docs`, and
schemathesis checks real responses against it ([testing.md](testing.md#property-based-api-check-schemathesis)).
`/api/progress` has no model on purpose: `progress.json` is written by another process, and its fields change
together with the stages. Error codes are in the schema too: 400 — the body could not be parsed, 404 — not found
(a vacancy, a skill), 409 — conflicts with the current state (a run is already / is not going on, a skill with
this name exists), 405 — with the full `Allow` header. In a 422 `detail` is either a list of Pydantic errors (the
body does not match the schema) or a string the UI shows as is (bad regex, neither a link nor a vacancy text).
Input types are strict: a bool is only `true`/`false`, a weight is an integer, an event date is an ISO string.
Strings with broken characters (lone UTF-16 surrogates) are rejected on input: they cannot be written to a file.
