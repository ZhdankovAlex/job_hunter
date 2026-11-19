"""Stop hook: ruff and mypy on the changed .py files; errors go back to the agent.

Changed means `git diff --name-only HEAD` plus untracked files (`git ls-files --others --exclude-standard`).
ruff checks all of them, mypy only those inside `[tool.mypy] files` from pyproject.toml (like a plain `mypy`
run). On errors the hook prints JSON {"decision": "block", "reason": ...}: Claude keeps going and fixes them.
No errors or no changed .py files — silence. pytest is not run (too slow).
A repeat stop in the same chain (stop_hook_active) is not blocked, so the hook cannot loop.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_OUTPUT = 4000  # characters per tool: a long dump would only eat the context


def _git_lines(root: Path, *args: str) -> list[str]:
    res = subprocess.run(["git", *args], cwd=root, capture_output=True, encoding="utf-8", errors="replace")
    return [s.strip() for s in res.stdout.splitlines() if s.strip()] if res.returncode == 0 else []


def changed_py_files(root: Path = ROOT) -> list[str]:
    names = _git_lines(root, "diff", "--name-only", "HEAD") + _git_lines(
        root, "ls-files", "--others", "--exclude-standard")
    seen: dict[str, None] = {}
    for n in names:
        if n.endswith(".py") and (root / n).is_file():
            seen.setdefault(n, None)
    return list(seen)


def mypy_targets(files: list[str], root: Path = ROOT) -> list[str]:
    """The files mypy checks by its config (a file or a folder from `[tool.mypy] files`)."""
    try:
        conf = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    entries = [e.rstrip("/") for e in conf.get("tool", {}).get("mypy", {}).get("files", [])]
    return [f for f in files if any(f == e or f.startswith(e + "/") for e in entries)]


def _run(cmd: list[str], root: Path) -> tuple[int, str]:
    res = subprocess.run(cmd, cwd=root, capture_output=True, encoding="utf-8", errors="replace")
    out = (res.stdout + res.stderr).strip()
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + "\n… (truncated)"
    return res.returncode, out


def check(root: Path = ROOT, python: str = sys.executable) -> str | None:
    """ruff/mypy errors as text, or None when everything is clean."""
    files = changed_py_files(root)
    if not files:
        return None
    problems = []
    code, out = _run([python, "-m", "ruff", "check", "--force-exclude", "--quiet", *files], root)
    if code != 0:
        problems.append(f"ruff check:\n{out}")
    targets = mypy_targets(files, root)
    if targets:
        code, out = _run([python, "-m", "mypy", *targets], root)
        if code != 0:
            problems.append(f"mypy:\n{out}")
    if not problems:
        return None
    return "stop_check hook: the changed files have errors, fix them before finishing.\n\n" + "\n\n".join(problems)


def main() -> int:
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        event = {}
    if event.get("stop_hook_active"):
        return 0
    reason = check()
    if reason:
        sys.stdout.buffer.write(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
