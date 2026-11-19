"""PreToolUse hook (Write|Edit): keeps the agent out of the owner's personal files.

Claude Code passes JSON on stdin (tool_name, tool_input.file_path, cwd). A protected path gets a JSON answer
with permissionDecision "deny" and a reason: Claude sees it and leaves the file alone. Otherwise the hook is
silent and exits 0.

If the user asked for the edit themselves: create the marker `state/allow_protected_edit` (`touch`), make the
edit and delete the marker. The marker works for ALLOW_TTL seconds.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# paths from the project root; only personal data, the *.example.* files stay editable
PROTECTED = ("settings.toml", "cv/base.toml", "applications_manual.toml", "profile.toml", "companies.txt")
ALLOW_MARKER = "state/allow_protected_edit"
ALLOW_TTL = 3600


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))


def protected_reason(file_path: str, cwd: str, root: Path = ROOT) -> str | None:
    """Why writing to file_path is not allowed, or None."""
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(cwd or root) / path
    target = _norm(path)
    for rel in PROTECTED:
        if target == _norm(root / rel):
            return f"{rel} is the owner's personal file"
    return None


def allowed_by_marker(root: Path = ROOT, now: float | None = None) -> bool:
    marker = root / ALLOW_MARKER
    try:
        age = (now if now is not None else time.time()) - marker.stat().st_mtime
    except OSError:
        return False
    return age < ALLOW_TTL


def decide(event: dict, root: Path = ROOT) -> dict | None:
    """The hook's answer (a dict for stdout) or None to let the call through."""
    file_path = (event.get("tool_input") or {}).get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    reason = protected_reason(file_path, event.get("cwd") or "", root)
    if reason is None or allowed_by_marker(root):
        return None
    text = (f"Blocked by the protect_files hook: {reason}. Do not edit it unless the user asked. "
            f"If the user asked: `touch {ALLOW_MARKER}` in the project root, make the edit, then "
            f"`rm {ALLOW_MARKER}` (the marker works for {ALLOW_TTL // 60} min).")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": text}}


def main() -> int:
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        return 0  # not our format: do not get in the way
    out = decide(event)
    if out is not None:
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
