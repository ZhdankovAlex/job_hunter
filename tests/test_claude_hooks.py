"""Claude Code hooks from .claude/hooks: personal file protection (PreToolUse) and ruff/mypy at the end of a turn (Stop)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".claude" / "hooks"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"claude_hook_{name}", HOOKS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


protect = _load("protect_files")
stop = _load("stop_check")


# --- protect_files ---------------------------------------------------------------------------

def _event(path: str, cwd: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": cwd,
            "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


@pytest.mark.parametrize("rel", ["settings.toml", "cv/base.toml", "applications_manual.toml", "profile.toml",
                                 "companies.txt"])
def test_protected_files_denied(tmp_path, rel):
    out = protect.decide(_event(str(tmp_path / rel), str(tmp_path)), root=tmp_path)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "deny"
    assert rel in hso["permissionDecisionReason"] and protect.ALLOW_MARKER in hso["permissionDecisionReason"]


def test_relative_path_and_case_resolved(tmp_path):
    # a relative path is taken from cwd; on Windows the case does not matter
    assert protect.decide(_event("cv/base.toml", str(tmp_path)), root=tmp_path)
    if os.name == "nt":
        assert protect.decide(_event(str(tmp_path / "Settings.TOML"), "."), root=tmp_path)


@pytest.mark.parametrize("rel", ["settings.example.toml", "cv/base.example.toml", "profile.example.toml",
                                 "companies.example.txt", "docs/settings.toml", "jobhunter/http.py"])
def test_regular_files_pass(tmp_path, rel):
    assert protect.decide(_event(str(tmp_path / rel), str(tmp_path)), root=tmp_path) is None


def test_marker_allows_for_a_while(tmp_path):
    marker = tmp_path / protect.ALLOW_MARKER
    marker.parent.mkdir(parents=True)
    marker.touch()
    assert protect.decide(_event(str(tmp_path / "settings.toml"), str(tmp_path)), root=tmp_path) is None
    old = time.time() - protect.ALLOW_TTL - 10
    os.utime(marker, (old, old))
    assert not protect.allowed_by_marker(tmp_path)


def test_no_file_path_passes(tmp_path):
    assert protect.decide({"tool_name": "Write", "tool_input": {}}, root=tmp_path) is None


def test_protect_script_stdout_is_json():
    # the same path Claude Code takes: stdin -> script -> stdout
    event = _event(str(protect.ROOT / "settings.toml"), str(protect.ROOT))
    if protect.allowed_by_marker():
        pytest.skip("the allow marker is set in this checkout")
    res = subprocess.run([sys.executable, str(HOOKS / "protect_files.py")], input=json.dumps(event).encode(),
                         capture_output=True, check=True)
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok = subprocess.run([sys.executable, str(HOOKS / "protect_files.py")],
                        input=json.dumps(_event(str(protect.ROOT / "README.md"), ".")).encode(),
                        capture_output=True, check=True)
    assert ok.stdout == b""


# --- stop_check ------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=root,
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.mypy]\nfiles = ["pkg"]\n', encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("x: int = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_changed_files_tracked_and_untracked(repo):
    assert stop.changed_py_files(repo) == []
    (repo / "pkg" / "ok.py").write_text("x: int = 2\n", encoding="utf-8")
    (repo / "pkg" / "new.py").write_text("y = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("b\n", encoding="utf-8")
    (repo / "loose.py").write_text("z = 1\n", encoding="utf-8")
    assert sorted(stop.changed_py_files(repo)) == ["loose.py", "pkg/new.py", "pkg/ok.py"]
    # mypy gets only what is in [tool.mypy] files
    assert sorted(stop.mypy_targets(stop.changed_py_files(repo), repo)) == ["pkg/new.py", "pkg/ok.py"]


def test_clean_repo_is_silent(repo):
    assert stop.check(repo) is None
    (repo / "pkg" / "ok.py").write_text("x: int = 3\n", encoding="utf-8")
    assert stop.check(repo) is None


def test_ruff_and_mypy_errors_reported(repo):
    (repo / "pkg" / "ok.py").write_text("import os\nx: int = 'a'\n", encoding="utf-8")
    reason = stop.check(repo)
    assert reason and "ruff check" in reason and "F401" in reason
    assert "mypy" in reason and "incompatible types" in reason.lower()


def test_stop_hook_active_does_not_block():
    res = subprocess.run([sys.executable, str(HOOKS / "stop_check.py")],
                         input=json.dumps({"stop_hook_active": True}).encode(), capture_output=True, check=True)
    assert res.stdout == b""
