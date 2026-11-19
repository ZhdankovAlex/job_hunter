# CLAUDE.md — working rules for this project

How the project is developed with Claude Code (these rules, skills, hooks) — `docs/claude-code.md`.

## Hard rules
- Nothing goes out (emails, messages, forms) — drafts for the user only.
- LinkedIn is not automated in any way.
- Respect robots.txt; at most 1 request per second per host (`jobhunter/http.py` does this — go to the network
  only through it); bot protection (Cloudflare and the like) is never bypassed.
- Commit and push only when the user asks. Before a commit — the `/prep-commit` skill. Work goes through a short
  branch and a pull request; before pushing — `git pull --rebase` (a GitHub Action adds a "RELEASE_NOTES.md: update"
  commit to main).
- Personal data stays out of git: `settings.toml`, `companies.txt`, `profile.toml`, `cv/base.toml`,
  `applications_manual.toml`, `ats_mapping.json`, `state/`, `reports/` (see `.gitignore`). Examples live in `*.example.*`.
- A new vacancy source (site, board, ATS, channel) — by the `/new-source` skill.
- No invented facts in CVs and letters: only what is in `cv/base.toml` or what the user said.

## Environment and checks
- Project Python: `.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere.
- Before saying "done": `pytest -q`, `ruff check .`, `mypy` — all green. Annotate new functions.
- Hooks (`.claude/settings.json`, scripts in `.claude/hooks`): ruff + mypy on the changed files at the end of a
  turn; Write/Edit into personal files is blocked — bypass only when the user asked, marker `state/allow_protected_edit`.
- Web UI: `python -m web` (port 8765). `JOBHUNTER_NO_WARM=1` turns off cache warm-up at start (tests, one-off scripts).
- Gotcha: no Python strings with backslashes in a bash heredoc (`\b` becomes byte 0x08). A script that rewrites a
  file builds and encodes the whole text first, then opens the file: `open(p, "w")` truncates it at once.

## Tests
- Found a bug — a test that shows it first, then the fix.
- No network (`FakeHttp` from `tests/conftest.py`) and no personal files (conftest swaps in `*.example.*`).
  Everything the code writes to disk goes to `tmp_path` via `monkeypatch`. A new path constant an endpoint writes
  to — straight into the `writes` fixture in `tests/test_api_schema.py`, or schemathesis touches the real file.
- A new endpoint: `response_model` and `responses=errors(...)` with the codes it returns; schemathesis picks it
  up by itself (`tests/test_api_schema.py`).
- A regex over long text — check it on 50–100 KB without spaces (there were O(n²) cases of 9–13 s).

## Documentation (`docs/`, the map is the table in `README.md`)
- Something visible from outside changed (page, button, API, flag, setting, file in `state/`) — fix the right
  `docs/*.md` in the same commit; which one — the map in `/prep-commit`.
- Names of parameters, constants, files and numbers — from the code (grep), not from memory; measurements are real.
- Docs describe how things are now; the history of changes lives in commits and `RELEASE_NOTES.md`.
- Nothing personal in examples: neutral ones only (example.com, Acme).
