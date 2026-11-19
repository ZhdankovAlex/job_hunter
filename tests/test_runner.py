"""Starting a run from the web UI: script queue, stop, scheduled run. Processes are fakes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi import HTTPException

from jobhunter import progress
from web import runner as runner_mod
from web.runner import Runner, daily_scheduler


class FakeProcess:
    """Instead of subprocess.Popen: records the command and environment, "exits" at once."""

    started: ClassVar[list[FakeProcess]] = []

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        self.args, self.env, self.pid = args, kwargs["env"], 4242
        FakeProcess.started.append(self)

    def wait(self) -> int:
        return 0

    def poll(self) -> int | None:
        return None  # for stop(): the process is still alive


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runner:
    FakeProcess.started = []
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(progress, "PROGRESS_FILE", tmp_path / "progress.json")  # no runs "yet"
    return Runner(tmp_path, tmp_path / "last_run.log")


def _finish(r: Runner) -> None:
    assert r.thread
    r.thread.join(5)


def test_start_runs_queue(runner: Runner) -> None:
    finished: list[bool] = []
    runner.on_finish = lambda: finished.append(True)
    queue = runner.start("all", ["telegram", "aggregators", "unknown"])
    assert queue == ["applications.py", "main.py --no-telegram --no-aggregators"]  # flags go to main.py only
    _finish(runner)
    first, second = FakeProcess.started
    assert first.args[0] == sys.executable  # no .venv in tmp — the current interpreter
    assert first.args[1:] == ["applications.py"]
    assert progress.CONTINUE_RUN_ENV not in first.env and second.env[progress.CONTINUE_RUN_ENV] == "1"
    assert runner.queue == [] and runner.process is None and finished == [True]
    log = runner.log.read_text(encoding="utf-8")
    assert "applications.py (started from the web UI)" in log and "main.py --no-telegram" in log


def test_start_skip_mail_drops_applications(runner: Runner) -> None:
    assert runner.start("all", ["mail"]) == ["main.py"]
    _finish(runner)


def test_start_nothing_to_run(runner: Runner) -> None:
    with pytest.raises(HTTPException) as e:
        runner.start("applications", ["mail"])
    assert e.value.status_code == 422


def test_start_while_busy(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "read", lambda: {"status": "running", "pid": 1})
    monkeypatch.setattr(progress, "is_alive", lambda data: True)  # a run from the terminal
    with pytest.raises(HTTPException) as e:
        runner.start("main", [])
    assert e.value.status_code == 409


def test_stop_flag_skips_rest_of_queue(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    def first_wait(self: FakeProcess) -> int:
        runner.stopping = True  # "Stop" pressed while the first script was running
        return 0

    monkeypatch.setattr(FakeProcess, "wait", first_wait)
    runner.start("all", [])
    _finish(runner)
    assert len(FakeProcess.started) == 1
    assert "stopped from the web UI" in runner.log.read_text(encoding="utf-8")


def test_stop_not_running(runner: Runner) -> None:
    with pytest.raises(HTTPException) as e:
        runner.stop()
    assert e.value.status_code == 409


def test_stop_own_process_windows(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    runner.process = FakeProcess(["python", "main.py"], env={})  # type: ignore[assignment]
    assert runner.stop() == 4242
    assert runner.stopping and calls == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_stop_terminal_run_posix(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run from the terminal (pid from progress.json); killpg failed — kill the process itself."""
    killed: list[tuple[str, int]] = []

    def killpg(pid: int, sig: int) -> None:
        raise OSError("no such group")

    monkeypatch.setattr(runner_mod.sys, "platform", "linux")
    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(("kill", pid)))
    monkeypatch.setattr(progress, "read", lambda: {"status": "running", "pid": 77})
    monkeypatch.setattr(progress, "is_alive", lambda data: True)
    assert runner.stop() == 77
    assert killed == [("kill", 77)]


class StopLoop(Exception):
    pass


def _schedule(runner: Runner, monkeypatch: pytest.MonkeyPatch, now: str) -> list[str]:
    """One turn of daily_scheduler at the moment now; -> which runs it started."""
    started: list[str] = []
    monkeypatch.setattr(runner, "start", lambda kind, skip: started.append(kind) or [])

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Clock:  # type: ignore[override]
            return cls.fromisoformat(now)

    def sleep(seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(runner_mod, "datetime", Clock)
    monkeypatch.setattr(time, "sleep", sleep)
    with pytest.raises(StopLoop):
        daily_scheduler(runner, "07:30", runner.root / "schedule.json")
    return started


def test_scheduler_runs_once_a_day(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    state = runner.root / "schedule.json"
    assert _schedule(runner, monkeypatch, "2026-11-19T07:00") == []  # too early
    assert _schedule(runner, monkeypatch, "2026-11-19T09:15") == ["all"]  # a missed time is caught up
    assert json.loads(state.read_text(encoding="utf-8")) == {"last": "2026-11-19"}
    assert _schedule(runner, monkeypatch, "2026-11-19T10:00") == []  # already ran today
    assert _schedule(runner, monkeypatch, "2026-11-20T07:30") == ["all"]


def test_scheduler_busy_start_retries(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    """start() refused (a run began between the check and the start) — the day is not marked."""
    state = runner.root / "schedule.json"
    state.write_text("{broken", encoding="utf-8")

    def busy(kind: str, skip: list[str]) -> list[str]:
        raise HTTPException(409)

    assert _schedule(runner, monkeypatch, "2026-11-19T08:00") == ["all"]  # a broken file — as if not run yet
    monkeypatch.setattr(runner, "start", busy)
    state.write_text("{}", encoding="utf-8")
    with pytest.raises(StopLoop):
        daily_scheduler(runner, "07:30", state)
    assert json.loads(state.read_text(encoding="utf-8")) == {}
