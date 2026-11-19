# Developing with Claude Code

The project is developed together with Claude Code: the agent writes code, tests and docs, a person sets the
tasks and accepts the result. This page covers what the agent gets as input and what keeps it in check: rules,
skills, hooks. Everything lives in the repository and works in any session opened in the project root.

- [What lives where](#what-lives-where)
- [CLAUDE.md](#claudemd)
- [Project skills](#project-skills)
- [Hooks](#hooks)
- [Built-in skills and subagents](#built-in-skills-and-subagents)
- [Why it is set up this way](#why-it-is-set-up-this-way)

## What lives where

| Path | What | In git |
|---|---|---|
| `CLAUDE.md` | Rules for every session: hard limits, environment, checks, tests, documentation | yes |
| `.claude/skills/<name>/SKILL.md` | Project skills — checklists the agent opens when a task fits | yes |
| `.claude/settings.json` | Hooks (shared project settings) | yes |
| `.claude/hooks/*.py` | Hook scripts; tests in `tests/test_claude_hooks.py` | yes |
| `.claude/settings.local.json` and the rest of `.claude/` | Personal permissions, paths, preview config | no |

## CLAUDE.md

Read in full at the start of every session, so it is short: only what almost every task needs.
- **Hard rules**: nothing is sent out (drafts only), LinkedIn is not automated, the network only through
  `jobhunter/http.py` (robots.txt, at most 1 request per second per host, bot protection is never bypassed),
  commit and push only when asked, personal data stays out of git, CV and letter facts only from `cv/base.toml`.
- **Checks**: before "done" — `pytest -q`, `ruff check .`, `mypy`; environment gotchas.
- **Tests**: a bug gets a test first; no network (`FakeHttp`), no personal files; disk writes go to `tmp_path`.
- **Documentation**: something visible from outside changed — the right `docs/*.md` in the same commit; names
  and numbers come from the code.

Long procedures do not go into CLAUDE.md — they are skills.

## Project skills

A skill is a Markdown file with a checklist. Only its description (one line) sits in the context; the agent
reads the whole file when a task matches the description, or on the `/<name>` command.

| Skill | When | What is inside |
|---|---|---|
| `/prep-commit` | The work is done and a commit or a message draft is needed | Checks with a PASS/FAIL report and a READY / NOT READY verdict, what goes into the commit, the docs map, the message format (`RELEASE_NOTES.md` is built from it), branch → PR |
| `/new-source` | A new vacancy source: site, board, ATS, channel | robots.txt and bot-protection check, how to connect it, a fixture and a test via `FakeHttp`, `docs/sources.md` |

## Hooks

Hooks are run by Claude Code itself, not by the agent: a rule in CLAUDE.md can be forgotten, a hook cannot.
They are set in `.claude/settings.json`; the scripts are Python from `.venv` (the command finds the interpreter in
`.venv/bin` or `.venv/Scripts`).

| Hook | When | What it does |
|---|---|---|
| `stop_check.py` | `Stop` — the agent finishes a turn | `ruff check` and `mypy` only on the changed and new .py files (`git diff --name-only HEAD` plus untracked; mypy within `[tool.mypy] files`). Errors go back to the agent (`decision: block`), which fixes them in the same turn. No errors — silence. pytest is not run (too slow). A repeat stop in the same chain (`stop_hook_active`) is not blocked, so the hook cannot loop |
| `protect_files.py` | `PreToolUse` on `Write\|Edit\|MultiEdit` | Denies writes into `settings.toml`, `cv/base.toml`, `applications_manual.toml`, `profile.toml`, `companies.txt` (`permissionDecision: deny` with an explanation). `*.example.*` files stay editable |

Bypassing the protection when the user asked for the edit: the marker `state/allow_protected_edit` (`touch`), the
edit, then `rm`. The marker works for an hour (`ALLOW_TTL`). The hook does not see writes through Bash — it guards
against an accidental edit, not a deliberate one.

An edit to `.claude/settings.json` is picked up by an open session, no restart needed.

## Built-in skills and subagents

Built into Claude Code or from plugins — when the situation calls for it, not on every commit:
- `/code-review` — a review of the diff for bugs; `/simplify` — simplifications and repetition in the changed code;
- `/security-review` — before changes that touch the network, files or tokens;
- `/fewer-permission-prompts` — turn frequent safe commands into permissions;
- the `claude-code-guide` subagent — checks hooks, settings and skills against the Claude Code docs instead of
  memory; `Explore` — a wide code search when a conclusion is needed, not the files.

## Why it is set up this way

- **Few and narrow.** Ready-made bundles (dozens of hooks on every tool call, hundreds of skills) eat the context
  and the token limit. Here there are two hooks and two skills, each closing a mistake that already happened or
  would be costly: a red mypy in a commit, an edit of a personal file.
- **Rules in CLAUDE.md, procedures in skills.** CLAUDE.md is read in every session, a skill only when needed.
- **Checks by a script, not by a promise.** The Stop hook makes "all green before done" automatic for ruff and
  mypy; pytest stays in `/prep-commit` — it takes about 30 seconds, too much for every turn.
- **Hooks with tests.** Hook scripts are ordinary code: annotations, mypy, tests on a temporary git repo without
  network.
