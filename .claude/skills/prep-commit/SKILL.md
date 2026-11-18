---
name: prep-commit
description: A checklist before a commit in job-hunter — checks, personal files, documentation and a commit message draft in .git/COMMIT_MSG_DRAFT in the project's style. Use when the work is done and a commit or a message draft is needed, and before any "commit / push".
---

# Preparing a commit (job-hunter)

Go step by step; whatever fails — fix it or tell the user, do not skip it silently.
Commit and push only if the user asked; otherwise stop at step 5 (the draft).

## 1. Checks
```bash
JOBHUNTER_NO_WARM=1 .venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy
```
All green. Platform-dependent code — also `mypy --platform linux` (CI runs on Linux).

## 2. What goes into the commit
- `git status --short` and `git diff --stat`: only the files of this task. Other people's edits are not
  included: `git add <files>` by name, not `git add -A`.
- Nothing personal: `settings.toml`, `companies.txt`, `profile.toml`, `cv/base.toml`, `cv/*.pdf`,
  `applications_manual.toml`, `ats_mapping.json`, `profile_overrides.json`, `state/`, `reports/`, `cv_out/`
  (see `.gitignore`). A new personal file appeared — add it to `.gitignore` first; examples go to `*.example.*`.
- No real data in the diff: recruiter names, letters, tokens, your own paths.
- Tests did not touch real files: after `pytest` `git status` is the same, and personal files keep their
  modification time.

## 3. Documentation
By the diff: something visible from outside changed — the right `docs/*.md` is fixed in the same commit. Map:
- pages and API — `web-ui.md`; runs and speed — `pipeline.md`; settings and files — `configuration.md`;
  tests and environment variables — `testing.md`; industries — `industry.md`; sources — `sources.md`.

Also:
- a new endpoint → the API list in `docs/web-ui.md`, `response_model` and `responses=errors(...)`; a new page →
  the page table there;
- a new test file → the table in `docs/testing.md`; a new environment variable → there too;
- a new setting or file in `state/` → `docs/configuration.md`;
- a new document → the table in `README.md` and "Where to look next" in `docs/architecture.md`;
- names and numbers in docs are checked against the code (grep);
- a renamed heading — anchor links to it still work (`grep -rn "#old-anchor" docs README.md`).

## 4. Commit message
`RELEASE_NOTES.md` is built from commit messages (`jobhunter/release_notes.py`): a person reads the title and the
`- …` items, so write for the reader, not "fixed a typo in app.py".
- In English, like the previous commits (`git log -5 --format=%B`).
- Title: what changed for the user, comma-separated, no full stop; "Topic: details" is fine.
- A blank line, then `- …` items: what and why; for a bug — what broke and why, in one line; a long item wraps
  with a two-space indent.
- Documentation — a separate item at the end: which docs and about what.
- The last line (after a blank one): `Co-Authored-By: …` if an assistant helped.

## 5. Draft
Write it to `.git/COMMIT_MSG_DRAFT` and tell the user which files go in.

## 6. If asked to commit / push
Work goes through a short branch and a pull request, not straight to `main`:
```bash
git switch -c fix/<topic>        # or feat/<topic>
git add <files from step 2>
git commit -F .git/COMMIT_MSG_DRAFT
git push -u origin fix/<topic>
```
After a merge to `main` a GitHub Action adds a "RELEASE_NOTES.md: update" commit — `git pull --rebase` on `main`
before the next branch. A hook failed — find the cause, do not use `--no-verify`.
