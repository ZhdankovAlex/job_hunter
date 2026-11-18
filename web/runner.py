"""Runs started from the web UI: a queue of scripts in a background thread and the daily scheduled run."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from jobhunter import progress


class Runner:
    """One run at a time: scripts one after another in a background thread, output goes to last_run.log."""

    SCRIPTS = {
        "all": ["applications.py", "main.py"],  # like run_daily.bat: mail first, then vacancies
        "main": ["main.py"],
        "applications": ["applications.py"],
    }
    # what can be turned off in advance -> a main.py flag; "mail" removes applications.py from the queue
    SKIP_FLAGS = {"telegram": "--no-telegram", "freehire": "--no-freehire", "aggregators": "--no-aggregators"}

    def __init__(self, root: Path, log: Path, on_finish: Callable[[], None] = lambda: None) -> None:
        """root — project folder (scripts' cwd), log — where output goes, on_finish — after the queue."""
        self.root, self.log, self.on_finish = root, log, on_finish
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.queue: list[str] = []
        self.stopping = False

    def busy(self) -> bool:
        """Whether a run is going on: started from here or from a terminal / the Task Scheduler."""
        if self.thread and self.thread.is_alive():
            return True
        data = progress.read()
        return bool(data and progress.is_alive(data))

    def start(self, kind: str, skip: Sequence[str]) -> list[str]:
        """Starts the script queue; skip — stages turned off in advance. -> commands without the interpreter."""
        with self.lock:
            if self.busy():
                raise HTTPException(409, "A run is already going on — wait for it to end")
            scripts = [s for s in self.SCRIPTS[kind] if not ("mail" in skip and s == "applications.py")]
            if not scripts:
                raise HTTPException(422, "Nothing to run: mail is turned off and there are no other scripts in the queue")
            flags = [self.SKIP_FLAGS[k] for k in skip if k in self.SKIP_FLAGS]
            commands = [[s, *flags] if s == "main.py" else [s] for s in scripts]
            self.queue = [" ".join(c) for c in commands]
            self.stopping = False
            self.thread = threading.Thread(target=self._run, args=(commands,), daemon=True)
            self.thread.start()
            return list(self.queue)

    def _run(self, commands: list[list[str]]) -> None:
        python = self.root / ".venv" / "Scripts" / "python.exe"
        python = python if python.exists() else Path(sys.executable)
        with self.log.open("a", encoding="utf-8") as log:
            for i, command in enumerate(commands):
                log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(command)} (started from the web UI) ===\n")
                log.flush()
                # a separate process group: Ctrl+C in the server window does not interrupt the running run.
                # DETACHED_PROCESS is not used: with it, runs started from some shells
                # silently ended halfway. Restarting the server still interrupts the run — see docs/web-ui.md.
                # if, not a conditional expression: mypy narrows sys.platform only in an if statement,
                # and CREATE_NEW_PROCESS_GROUP exists only on Windows (CI checks under Linux)
                if sys.platform == "win32":
                    detached = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    detached = 0
                self.process = subprocess.Popen(
                    [str(python), *command], cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", **({progress.CONTINUE_RUN_ENV: "1"} if i else {})},
                    creationflags=detached,
                    start_new_session=sys.platform != "win32",
                )
                self.process.wait()
                self.queue.pop(0)
                if self.stopping:  # "Stop run": the next scripts of the queue are not started
                    log.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} stopped from the web UI ===\n")
                    break
        self.queue = []
        self.process = None
        self.on_finish()  # a new report — recompute "Market" and "Skills" before anyone opens them

    def stop(self) -> int:
        """A full stop: the queue is cleared, the running script ends together with its child processes.

        Works for a run from a terminal / the Task Scheduler too (pid from progress.json). -> pid of the stopped one.
        What this run collected is not saved: the reports stay from the previous run.
        """
        with self.lock:
            data = progress.read()
            pid = self.process.pid if self.process and self.process.poll() is None else None
            if pid is None and data and progress.is_alive(data):
                pid = data["pid"]
            if pid is None:
                raise HTTPException(409, "No run is going on")
            self.stopping = True
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                try:
                    os.killpg(pid, signal.SIGTERM)  # start_new_session: group = pid
                except OSError:
                    os.kill(pid, signal.SIGTERM)
            return pid


def daily_scheduler(runner: Runner, at: str, state_file: Path) -> None:
    """Once a day at `at` (HH:MM, container time / TZ) — the "everything" run, like run_daily.bat.

    Turned on with the JOBHUNTER_DAILY_AT variable (in docker-compose.yml); usually not needed on Windows —
    there are the Task Scheduler and run_daily.bat. A missed time (the server was off) is caught up
    if there was no scheduled run today yet; if a run is already going on, it waits for it to end.
    """
    import time

    while True:
        now = datetime.now()
        try:
            last = json.loads(state_file.read_text(encoding="utf-8")).get("last", "")
        except (OSError, ValueError):
            last = ""
        if now.strftime("%H:%M") >= at and last != now.date().isoformat() and not runner.busy():
            try:
                runner.start("all", [])
                state_file.write_text(json.dumps({"last": now.date().isoformat()}), encoding="utf-8")
            except HTTPException:
                pass  # a run is already going on — try again in a minute
        time.sleep(60)
