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
| **Pipeline** | A live run: stages with bars, "processing now", sources in progress with seconds, ETA of the stage and the whole run, counters, "what we reject" and "where vacancies come from" charts, an event feed of the whole run with counters on its filters (found / rejected with a reason / source totals), warnings grouped as "🛠 needs action" (a board is gone, a company has no board, a list was cut by a limit), "🐞 code problems" (a response format changed, exceptions — with the traceback under "details") and "🌐 site failures" (HTTP errors, SSL, timeouts — usually pass by themselves). Buttons: run everything (like `run_daily.bat`), vacancies only, mail only, "⏹ Stop run" |
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

## How pages are built

Pages are built by the server, there is no separate frontend and no build step:

- **The same data for the page and the API.** Every page has a function that prepares its data
  (`companies_report`, `market_report`, `skills_payload`, `sources_payload`, `progress_payload`…). Its result goes
  into a Jinja2 template (`web/templates/<page>.html`), and the API returns the same result (`GET /api/companies`
  and so on). "The page says one thing, the API another" cannot happen.
- **Filters are URL parameters.** On "Companies" the industry and "only with vacancies" are
  `?industry=fintech&empty=0`. A click changes the address, the server returns an already filtered page, and the
  link can be saved. JavaScript (`web/static/`, no build) is used only where there is no other way: run progress
  over SSE, the CV, cover letter and event forms, skill edits, the collapsed panel and the theme.
- **One style base.** `web/static/tokens.css` holds the palette (light and dark theme), fonts, the size scale
  `--fs-xs` 12 · `--fs-sm` 13 · `--fs-md` 14 · `--fs-lg` 16 · `--fs-xl` 20 px, font weights, radii and the spacing
  scale `--sp-1`…`--sp-6` (4 · 8 · 12 · 16 · 24 · 32 px). The UI loads it before `app.css`, and the HTML pages in
  `reports/` (the report, "Applications") embed the same file into `<style>` in full (`html_report.TOKENS_CSS`), so
  they also open without the server. A font size in CSS is only a step of the scale, the palette lives only here:
  `tests/test_styles.py` checks this. The PDF CV (template styles in `cvgen.py`) is print layout in pt and is not
  part of the base. Reports built earlier keep their old styles until the next run.
- **One header and one rhythm.** A page starts with a header — the `ui.page_head` macro (`<header class="page-head">`):
  the `.page-title` row (h1 and, if any, toggles, filters, a `.count` counter) and the `.lead` under it, `--sp-2` apart.
  `main.page` is a column of blocks with a `--sp-4` gap and `--sp-5` padding; top-level blocks lose their own vertical
  margins (`.page > *` at the end of `app.css`), so "header → first block → next one" spacing is the same everywhere.
  "Pipeline" (the run card) and the pages in a frame (the report, "Applications" — they have their own h1 and `.sub`
  with the same spacing) keep their own layout. Checked by `tests/test_styles.py`.
- **Heavy computations are cached** in `state/web_cache.json` (see "Speed" above). Board links are not part of the
  key: a run rewrites `ats_mapping.json` all the time, so they are added on every request.
- **Responses and errors** — see "Responses" below.

## Design system: how to build a new page

Tokens are in `web/static/tokens.css`, components in `web/static/app.css` (the "components" section), markup in the
macros of `web/templates/_ui.html`. Do not invent sizes, colors or field styles on a page: if a new look is needed,
add a component or a token first, then the page. `tests/test_styles.py` checks the rules.

**Tokens.** Light and dark theme colors (`--bg`, `--card`, `--fg`, `--muted`, `--line`, `--chip`, `--accent`,
`--ok`, `--warn`, `--bad` and their `-soft` variants for backgrounds; text on an accent fill — `--on-accent`), fonts,
sizes `--fs-xs`…`--fs-xl`, spacing `--sp-1`…`--sp-6`, radii `--r-xs`…`--r-pill`, control height `--ctl-h` (34 px)
and `--ctl-h-sm` (26 px), shadows `--sh-1` (a card) and `--sh-2` (the selected tab), the focus ring `--focus`.

**Theme.** The theme button sits at the bottom of the left panel, above "Collapse": it cycles "system" → "light" →
"dark". The choice is stored in the browser (`localStorage`, key `theme`); "system" follows the OS setting. Without
a `data-theme` attribute on `<html>` the palette follows `prefers-color-scheme`; `data-theme="light"` and `"dark"`
are the user's choice and override the system. A script in `<head>` (`base.html`, and `html_report.THEME_JS` in the
pages of `reports/`) sets the attribute from `localStorage` before rendering, so the page does not flash. Reports and
"Applications" in a frame switch together with the UI (the `storage` event); opened from disk they always follow
the system. The dark palette is written twice in `tokens.css` — for `data-theme="dark"` and for the system dark
theme; add a new color to both blocks, `tests/test_styles.py` checks that they match.

**Components.**

| What | Class / macro | When |
|---|---|---|
| Button | `button`, `<a class="btn">` | One height `--ctl-h`. `.primary` — the main action (one per form or toolbar), `.danger` — stops or deletes, `.ghost` — secondary, no border, `.sm` — only actions in table and list rows |
| Toggle filters | `.toggles` with `button`, the selected one `.on` | A row of pills above a list or a table (on "Pipeline" `.feed-filter` looks the same) |
| Field | `input`, `select`, `textarea` | Shared styles, a form sets nothing of its own. A label above the field — `<label class="field">` (inside `.form` — a plain `<label>`), a checkbox with a label — `<label class="check">` |
| Form | `.form`, `ui.form_actions(status_id)` | A column of fields with a `--sp-3` step; field rows — `.row2`, `.row3` (a column on a narrow screen) |
| One-line form | `.form-inline` | A link or search and a button in one row |
| Page header | `ui.page_head(title, count, lead)` | The first row of every page except the pages in a frame and "Pipeline" |
| Toolbar | `.toolbar` | The strip above a report in a frame ("Reports", "Applications") |
| Card | `.card`, `ui.card_head(title, count)` | A page block; on the right of the card header — search or the card's actions |
| Table | `<div class="table-wrap"><table class="data">` | The wrapper scrolls the table on a narrow screen; numbers — `.num` |
| Chip, badge | `.chip` (`.on`), `.tag` (`.accent`, `.ok`, `.warn`, `.bad`) | A chip is a clickable filter or topic, a badge is a short mark next to text ("own", "draft") |
| Empty state | `ui.empty(text, hint)` | "No data" and what to do to get some |
| Status | `<span class="status">` | "Saving…", "✔ saved", "⚠️ error" next to a button; takes no space when empty |

**Where buttons go.**
- Whole-page actions (refresh, save) — on the right of the title row: the body of `{% call ui.page_head(...) %}`
  in `<div class="page-actions">`. Pages with a report in a frame — on the right of `.toolbar`.
- Form actions — at the bottom of the form, pushed right (`ui.form_actions`): the status, then the buttons, the
  main `.primary` one last. Build options (checkboxes, radios) — in `<span class="form-opts">` inside the same block,
  they go to the left.
- A one-line form — the button on the right in the same row (`.form-inline`).
- Card actions — on the right of its header (`ui.card_head`), actions of a list item or a table row — `.sm` at the
  end of the row.

**A new page.**

```jinja
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}Example · job-hunter{% endblock %}
{% block content %}
{% set lead %}What the page shows and where the data comes from — <code>state/example.json</code>.{% endset %}
{% call ui.page_head("Example", items|length, lead) %}
  <div class="page-actions"><button class="primary" id="refresh">↻ Refresh</button></div>
{% endcall %}

<form class="card form" id="example-form">
  <label>Name <input name="name" required></label>
  {% call ui.form_actions("example-status") %}<button class="primary" type="submit">Save</button>{% endcall %}
</form>

<section class="card">
  {% call ui.card_head("Records", items|length) %}<input id="q" type="search" placeholder="Search…">{% endcall %}
  {% if items %}<div class="table-wrap"><table class="data">…</table></div>
  {% else %}{{ ui.empty("No records yet", "Add the first one with the form above.") }}{% endif %}
</section>
{% endblock %}
```

Then add an item to `NAV` (`web/app.py`). Check it in a preview in the light and dark theme and on a narrow screen
(375 px): the page must not scroll horizontally.

The pages in `reports/` (the report, "Applications") embed the same tokens, and their filters, search field and
badges repeat the UI components (`html_report._CSS`), so they do not look foreign inside a frame. A new look in a
report — also only through tokens.

## Code and API

Code: `web/app.py` (FastAPI: pages and API), `web/models.py` (Pydantic models of requests and responses),
`web/runner.py` (starting runs and the schedule), `web/cache.py` (cache of slow pages), `web/templates/` (Jinja2), `web/static/` (CSS and JS without a build).
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
