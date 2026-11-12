"""Run progress: pipeline stages, counters, the current source.

A script (main.py, applications.py) declares stages and marks steps; the state is written to
state/progress.json at most once every WRITE_EVERY seconds, and the terminal shows a line
with a progress bar. To watch progress from another window (for example, when the run was started
by the Task Scheduler):

    .venv\\Scripts\\python.exe -m jobhunter.progress          # refreshes once a second
    .venv\\Scripts\\python.exe -m jobhunter.progress --once   # one snapshot

Modules (telegram.py, cvgen.py, …) call module functions — step(), item_started() —
without passing an object: if there is no run with progress, the calls do nothing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime

from . import config

PROGRESS_FILE = config.ROOT / "state" / "progress.json"
WRITE_EVERY = 0.5  # seconds between file writes and redraws of the line
MAX_ERRORS = 2000  # run warnings and errors kept in progress.json (guard against the file growing)

# Warning groups — what to do with them:
#   action — a person must act: a board is gone/moved, a company has no board, cut by a limit, a file is not set;
#   code   — a code problem: a response format changed, an exception (ERROR) — fix in code;
#   site   — someone else's site failed: robots.txt unavailable, HTTP 4xx/5xx, SSL, timeout — usually passes by itself.
_ACTION_RX = re.compile(
    r"no longer exists|moved|no board|slug not found|opened \d+ of \d+|not set|not found —|"
    r"fix it|settings\.toml|company not identified", re.I)
_CODE_RX = re.compile(r"could not parse|format .{0,40}changed|traceback|exception", re.I)


def error_group(level: str, message: str) -> str:
    """action / code / site for a warning from the log."""
    if _ACTION_RX.search(message):
        return "action"
    if level in ("ERROR", "CRITICAL") or _CODE_RX.search(message):
        return "code"
    return "site"
MAX_EVENTS = 5000  # feed events per run (guard against the file growing, like MAX_ERRORS)
HISTORY_FILE = config.ROOT / "state" / "progress_history.json"  # stage durations of past runs
HISTORY_RUNS = 5
# requests from the web UI to the running run: {"pid": …, "skip": [stage keys]}
CONTROL_FILE = config.ROOT / "state" / "progress_control.json"
CONTROL_EVERY = 1.0  # seconds between reads of CONTROL_FILE
BAR_WIDTH = 24

PENDING, RUNNING, DONE, FAILED, SKIPPED = "pending", "running", "done", "failed", "skipped"
_ICONS = {PENDING: "·", RUNNING: "▶", DONE: "✔", FAILED: "✖", SKIPPED: "–"}

_current: "Pipeline | None" = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _bar(done: int, total: int) -> str:
    filled = int(BAR_WIDTH * done / total) if total else 0
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class Pipeline:
    """State of one run.

    Attributes:
        script: script name ("main", "applications").
        stages: stages in order: key, title, status, done/total, time.
        counters: final run counters (found, rejected, …).
        errors: latest warnings and errors from the log.
    """

    def __init__(self, script: str, stages: list[tuple[str, str]], skippable: set[str] = frozenset()):
        """Declares the stages and makes this run the current one.

        Args:
            script: script name for the header.
            stages: [(key, title)] in execution order.
            skippable: stages that can be skipped on the fly (a button in the web UI):
                their loops check skip_requested() between steps.
        """
        global _current
        self.script = script
        self.started = time.time()
        self.stages = [{"key": k, "title": t, "status": PENDING, "done": 0, "total": 0,
                        "started": None, "finished": None, "note": "", "skippable": k in skippable}
                       for k, t in stages]
        self._skip_keys: set[str] = set()
        self._control_read = 0.0
        try:  # requests made to a previous run do not apply to this one
            CONTROL_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self.counters: dict[str, int] = {}
        self.tallies: dict[str, dict[str, int]] = {}  # breakdowns: reject reasons, vacancies by source
        self.events: list[dict] = []  # run feed: what was found, what was rejected (all stages, up to MAX_EVENTS)
        self.errors: list[dict] = []
        self.active: dict[str, float] = {}  # sources being processed right now
        self.last_item = ""
        self.status = RUNNING
        self._lock = threading.RLock()
        self._written = 0.0
        self._tty = sys.stdout.isatty()
        _current = self
        logging.getLogger().addHandler(_ErrorCollector(self))
        self._flush(force=True)

    # --- stages -----------------------------------------------------------------------------

    def _stage(self, key: str) -> dict:
        return next(s for s in self.stages if s["key"] == key)

    def _running(self) -> dict | None:
        return next((s for s in self.stages if s["status"] == RUNNING), None)

    def start(self, key: str, total: int = 0, note: str = "") -> None:
        """Starts a stage; the previous unfinished stage counts as done."""
        with self._lock:
            prev = self._running()
            if prev:
                self._close(prev, SKIPPED if prev["key"] in self._skip_keys else DONE)
            stage = self._stage(key)
            stage.update(status=RUNNING, total=total, done=0, started=_now(), note=note)
            self.active.clear()
            self.last_item = ""
        self._flush(force=True)

    def skip(self, key: str, note: str = "") -> None:
        """The stage is not run (for example, turned off with --no-telegram)."""
        with self._lock:
            self._stage(key).update(status=SKIPPED, note=note)
        self._flush(force=True)

    def skip_requested(self) -> bool:
        """Whether the web UI asked to skip the current stage (reads CONTROL_FILE at most once a second).

        On True the stage loop stops; whatever was collected so far goes on down the pipeline.
        """
        with self._lock:
            stage = self._running()
            if stage is None or not stage["skippable"]:
                return False
            if stage["key"] in self._skip_keys:
                return True
            now = time.time()
            if now - self._control_read < CONTROL_EVERY:
                return False
            self._control_read = now
            try:
                control = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if control.get("pid") != os.getpid() or stage["key"] not in control.get("skip", []):
                return False
            self._skip_keys.add(stage["key"])
            stage["note"] = f"skipped by hand at {stage['done']}" + (f" of {stage['total']}" if stage["total"] else "")
        logging.getLogger(__name__).warning("Stage '%s' skipped with the web UI button", stage["title"])
        self.event("info", f"Stage '{stage['title']}' skipped by hand")
        return True

    def _close(self, stage: dict, status: str) -> None:
        stage["status"], stage["finished"] = status, _now()
        if stage["total"] and status == DONE:
            stage["done"] = stage["total"]

    def set_total(self, total: int) -> None:
        """How many steps the current stage has, if that became known after start()."""
        with self._lock:
            stage = self._running()
            if stage:
                stage["total"] = total
        self._flush()

    def step(self, item: str = "", n: int = 1) -> None:
        """A step of the current stage; item — what was just processed (a company, channel, letter)."""
        with self._lock:
            stage = self._running()
            if stage:
                stage["done"] += n
            if item:
                self.last_item = item
        self._flush()

    def item_started(self, item: str) -> None:
        """A source was taken into work (parallel processing — there can be several)."""
        with self._lock:
            self.active[item] = time.time()
        self._flush()

    def item_finished(self, item: str) -> None:
        with self._lock:
            self.active.pop(item, None)
        self.step(item)

    def count(self, **values: int) -> None:
        """Final counters: count(fetched=1200, matched=540)."""
        with self._lock:
            self.counters.update(values)
        self._flush()

    def tally(self, group: str, key: str, n: int = 1) -> None:
        """+n to the breakdown group[key]: tally("rejects", "older than 30 days")."""
        with self._lock:
            bucket = self.tallies.setdefault(group, {})
            bucket[key] = bucket.get(key, 0) + n
        self._flush()

    def event(self, kind: str, text: str) -> None:
        """A feed entry: kind — found / reject / source / mail / info."""
        with self._lock:
            self.events.append({"time": _now(), "kind": kind, "text": text[:300]})
            del self.events[:-MAX_EVENTS]
        self._flush()

    def finish(self, ok: bool = True) -> None:
        """End of the run: the current stage is closed, the file is written one last time."""
        global _current
        with self._lock:
            stage = self._running()
            if stage:
                self._close(stage, (SKIPPED if stage["key"] in self._skip_keys else DONE) if ok else FAILED)
            self.status = DONE if ok else FAILED
        self._flush(force=True)
        if ok:
            self._save_history()
        if self._tty:
            sys.stdout.write("\n")
            sys.stdout.flush()
        _current = None

    def _save_history(self) -> None:
        """Stage durations of a successful run — to estimate "until the end of the run" next time."""
        durations = {}
        for s in self.stages:
            if s["started"] and s["finished"] and s["status"] == DONE:
                spent = datetime.fromisoformat(s["finished"]) - datetime.fromisoformat(s["started"])
                durations[s["key"]] = int(spent.total_seconds())
        history = read_history()
        runs = history.setdefault(self.script, [])
        runs.append({"finished": _now(), "total": int(time.time() - self.started), "stages": durations})
        del runs[:-HISTORY_RUNS]
        try:
            HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # --- output -----------------------------------------------------------------------------

    def snapshot(self) -> dict:
        """State for progress.json: stages, current source, ETA, counters, errors."""
        with self._lock:
            stage = self._running()
            eta = None
            if stage and stage["total"] and stage["done"] and stage["started"]:
                spent = time.time() - datetime.fromisoformat(stage["started"]).timestamp()
                eta = int(spent / stage["done"] * (stage["total"] - stage["done"]))
            now = time.time()
            return {
                "script": self.script, "pid": os.getpid(), "status": self.status,
                "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                "updated": _now(), "elapsed": int(now - self.started),
                "stage": stage["key"] if stage else "", "eta_stage": eta,
                "last_item": self.last_item,
                "active": [{"item": k, "seconds": int(now - v)} for k, v in sorted(self.active.items(), key=lambda x: x[1])],
                "stages": [dict(s) for s in self.stages],
                "counters": dict(self.counters), "errors": list(self.errors),
                "tallies": {k: dict(v) for k, v in self.tallies.items()}, "events": list(self.events),
            }

    def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._written < WRITE_EVERY:
            return
        self._written = now
        data = self.snapshot()
        PROGRESS_FILE.parent.mkdir(exist_ok=True)
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        try:  # write through a temp file: a reader never sees half a JSON
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, PROGRESS_FILE)
        except OSError:
            pass  # the file is open in another process (Windows) — write on the next step
        if self._tty:
            self.draw()

    def draw(self) -> None:
        """Progress line at the bottom of the terminal; the log is printed above it (see ConsoleHandler)."""
        sys.stdout.write("\r" + status_line(self.snapshot()).ljust(_width()) + "\r")
        sys.stdout.flush()


def _width() -> int:
    return shutil.get_terminal_size((100, 20)).columns - 1


def status_line(data: dict) -> str:
    """One line: stage N/M, bar, done/total, ETA, current source."""
    stages = data["stages"]
    stage = next((s for s in stages if s["key"] == data["stage"]), None)
    if stage is None:
        return f"[{data['script']}] {data['status']} · {_duration(data['elapsed'])}"
    number = stages.index(stage) + 1
    line = f"[{number}/{len(stages)}] {stage['title']}"
    if stage["total"]:
        pct = 100 * stage["done"] // stage["total"]
        line += f" {_bar(stage['done'], stage['total'])} {stage['done']}/{stage['total']} {pct}%"
    if data.get("eta_stage") is not None:
        line += f" · ~{_duration(data['eta_stage'])}"
    current = data["active"][-1]["item"] if data["active"] else data["last_item"]
    if current:
        line += f" · {current}"
    return line[: _width()]


def render(data: dict) -> str:
    """Multi-line pipeline view for `python -m jobhunter.progress`."""
    lines = [f"{data['script']}.py — {data['status']} · started {data['started'][11:]} · "
             f"elapsed {_duration(data['elapsed'])} · updated {data['updated'][11:]}", ""]
    for n, s in enumerate(data["stages"], 1):
        line = f" {_ICONS[s['status']]} {n:>2}. {s['title']}"
        if s["total"]:
            line += f"  {_bar(s['done'], s['total'])} {s['done']}/{s['total']}"
        elif s["status"] == RUNNING and s["done"]:
            line += f"  {s['done']}"
        if s["started"] and s["finished"]:
            spent = datetime.fromisoformat(s["finished"]) - datetime.fromisoformat(s["started"])
            line += f"  ({_duration(spent.total_seconds())})"
        if s["note"]:
            line += f"  — {s['note']}"
        lines.append(line)
    if data["status"] == RUNNING:
        if data.get("eta_stage") is not None:
            lines += ["", f"Until the end of the stage ≈ {_duration(data['eta_stage'])}"]
        if data["active"]:
            lines += ["", "Processing now:"]
            lines += [f"   {a['item']} ({a['seconds']} s)" for a in data["active"][:10]]
        elif data["last_item"]:
            lines += ["", f"Last: {data['last_item']}"]
    if data["counters"]:
        lines += ["", "Counters: " + " · ".join(f"{k} {v}" for k, v in data["counters"].items())]
    if data["errors"]:
        lines += ["", f"Warnings ({len(data['errors'])}, latest):"]
        lines += [f"   {e['time'][11:]} {e['level']} {e['message'][:150]}" for e in data["errors"][-5:]]
    return "\n".join(lines)


CONTINUE_RUN_ENV = "JOBHUNTER_CONTINUE_RUN"  # second script of the same run (mail -> vacancies): do not rotate the log


def rotate_errors_log() -> None:
    """A new run: errors.log -> errors.prev.log (the previous one is overwritten), errors.log starts empty.

    The "everything" run is two scripts in a row; the second one starts with JOBHUNTER_CONTINUE_RUN=1 and appends
    to the same errors.log (the web UI and run_daily.bat set the variable themselves).
    """
    import os

    from . import config

    if os.environ.get(CONTINUE_RUN_ENV) or not config.ERRORS_LOG.exists():
        return
    try:
        os.replace(config.ERRORS_LOG, config.ERRORS_LOG_PREV)
    except OSError:  # another process holds the file — keep writing to the old one
        pass


class _ErrorCollector(logging.Handler):
    """Warnings and errors from the log go to progress.json (the latest MAX_ERRORS)."""

    def __init__(self, pipeline: Pipeline):
        super().__init__(logging.WARNING)
        self.pipeline = pipeline

    def emit(self, record: logging.LogRecord) -> None:
        p = self.pipeline
        if p is not _current:
            logging.getLogger().removeHandler(self)
            return
        with p._lock:
            message = record.getMessage()[:500]
            p.errors.append({"time": _now(), "level": record.levelname, "message": message,
                             "group": error_group(record.levelname, message)})
            del p.errors[:-MAX_ERRORS]


class ConsoleHandler(logging.StreamHandler):
    """Log to the terminal: clears the progress line, prints the record and draws the line again."""

    def emit(self, record: logging.LogRecord) -> None:
        p = _current
        if p is not None and p._tty:
            self.stream.write("\r" + " " * _width() + "\r")
        super().emit(record)
        if p is not None and p._tty:
            p.draw()


# --- module functions: work with the current run or do nothing ---------------------------

def start(key: str, total: int = 0, note: str = "") -> None:
    if _current:
        _current.start(key, total, note)


def skip(key: str, note: str = "") -> None:
    if _current:
        _current.skip(key, note)


def set_total(total: int) -> None:
    if _current:
        _current.set_total(total)


def step(item: str = "", n: int = 1) -> None:
    if _current:
        _current.step(item, n)


def item_started(item: str) -> None:
    if _current:
        _current.item_started(item)


def item_finished(item: str) -> None:
    if _current:
        _current.item_finished(item)


def count(**values: int) -> None:
    if _current:
        _current.count(**values)


def skip_requested() -> bool:
    """A request to skip the current stage; without an active run — False."""
    return bool(_current and _current.skip_requested())


def was_skipped(key: str) -> bool:
    """Whether the stage was skipped with the button in this run."""
    return bool(_current and key in _current._skip_keys)


def request_skip(stage: str, pid: int) -> None:
    """For the web UI: ask the run with this pid to skip the stage."""
    try:
        control = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        control = {}
    if control.get("pid") != pid:
        control = {"pid": pid, "skip": []}
    if stage not in control["skip"]:
        control["skip"].append(stage)
    CONTROL_FILE.write_text(json.dumps(control), encoding="utf-8")


def tally(group: str, key: str, n: int = 1) -> None:
    if _current:
        _current.tally(group, key, n)


def event(kind: str, text: str) -> None:
    if _current:
        _current.event(kind, text)


def tracked(name: str, fn, *args):
    """Calls fn(*args) marking the source as "in work" — for the thread pool."""
    item_started(name)
    try:
        return fn(*args)
    finally:
        item_finished(name)


def read_history() -> dict:
    """{script: [{finished, total, stages: {stage: seconds}}]} — the last HISTORY_RUNS runs."""
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def eta_total(data: dict) -> int | None:
    """Seconds until the end of the run: ETA of the current stage + median of past runs for the remaining stages."""
    runs = read_history().get(data["script"], [])
    if not runs or data["status"] != RUNNING:
        return None
    left = 0
    for s in data["stages"]:
        past = sorted(r["stages"][s["key"]] for r in runs if s["key"] in r["stages"])
        typical = past[len(past) // 2] if past else 0
        if s["status"] == PENDING:
            left += typical
        elif s["status"] == RUNNING:
            if data.get("eta_stage") is not None:
                left += data["eta_stage"]
            elif s["started"]:
                spent = time.time() - datetime.fromisoformat(s["started"]).timestamp()
                left += max(typical - int(spent), 0)
    return left


def is_alive(data: dict) -> bool:
    """Whether the run from progress.json is really going on (the process is alive)."""
    return data.get("status") == RUNNING and _alive(data["pid"])


def read() -> dict | None:
    """The last state from progress.json, or None if there were no runs yet."""
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    """Whether the run process is alive: a crashed script has no time to write the failed status."""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def watch(once: bool = False) -> None:
    """Redraws the pipeline from progress.json once a second until the run ends."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    while True:
        data = read()
        if data is None:
            print("No runs yet: there is no state/progress.json.")
            return
        if data["status"] == RUNNING and not _alive(data["pid"]):
            data["status"] = "interrupted (the process ended without marking the end)"
        text = render(data)
        if once or data["status"] != RUNNING:
            print(text)
            return
        os.system("cls" if sys.platform == "win32" else "clear")
        print(text)
        time.sleep(1)


if __name__ == "__main__":
    watch(once="--once" in sys.argv)
