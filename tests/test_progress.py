"""Run state (jobhunter/progress.py): stages, skipping by the button, log warnings, history and ETA."""
import json
import logging
import os

import pytest

from jobhunter import progress


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Run files go to a temporary folder; after the test there is no current run and none of its log handlers."""
    monkeypatch.setattr(progress, "PROGRESS_FILE", tmp_path / "progress.json")
    monkeypatch.setattr(progress, "HISTORY_FILE", tmp_path / "progress_history.json")
    monkeypatch.setattr(progress, "CONTROL_FILE", tmp_path / "progress_control.json")
    monkeypatch.setattr(progress, "WRITE_EVERY", 0)  # every step writes the file, so the test sees it at once
    yield tmp_path
    progress._current = None
    root = logging.getLogger()
    for h in [h for h in root.handlers if isinstance(h, progress._ErrorCollector)]:
        root.removeHandler(h)


STAGES = [("fetch", "Sources"), ("filter", "Filters"), ("report", "Report")]


def test_stages_go_in_order_and_file_follows(state):
    p = progress.Pipeline("main", STAGES)
    progress.start("fetch", total=4)
    progress.step("Acme")
    progress.step("Globex", n=2)
    data = progress.read()
    assert data["stage"] == "fetch" and data["last_item"] == "Globex"
    assert data["stages"][0]["done"] == 3 and data["stages"][0]["total"] == 4

    progress.start("filter")  # the previous stage is closed as done, done = total
    progress.count(fetched=10, matched=3)
    progress.tally("rejects", "older than 30 days")
    progress.tally("rejects", "older than 30 days", 2)
    progress.skip("report", "turned off by a flag")
    p.finish()

    data = progress.read()
    assert data["status"] == progress.DONE
    assert [s["status"] for s in data["stages"]] == [progress.DONE, progress.DONE, progress.SKIPPED]
    assert data["stages"][0]["done"] == 4
    assert data["counters"] == {"fetched": 10, "matched": 3}
    assert data["tallies"] == {"rejects": {"older than 30 days": 3}}
    assert progress._current is None


def test_module_functions_without_run_do_nothing(state):
    progress.start("fetch")
    progress.step("x")
    progress.event("info", "x")
    assert progress.read() is None
    assert progress.skip_requested() is False and progress.was_skipped("fetch") is False


def test_failed_run_marks_stage_and_skips_history(state):
    p = progress.Pipeline("main", STAGES)
    progress.start("fetch")
    p.finish(ok=False)
    data = progress.read()
    assert data["status"] == progress.FAILED and data["stages"][0]["status"] == progress.FAILED
    assert progress.read_history() == {}


def test_tracked_marks_source_active_while_running(state):
    p = progress.Pipeline("main", STAGES)
    progress.start("fetch", total=1)
    seen = {}

    def work(x):
        seen["active"] = [a["item"] for a in p.snapshot()["active"]]
        return x * 2

    assert progress.tracked("Acme", work, 21) == 42
    assert seen["active"] == ["Acme"]
    snap = p.snapshot()
    assert snap["active"] == [] and snap["stages"][0]["done"] == 1


def test_skip_button_stops_only_skippable_stage_of_this_pid(state, monkeypatch):
    monkeypatch.setattr(progress, "CONTROL_EVERY", 0)
    p = progress.Pipeline("main", STAGES, skippable={"fetch"})
    progress.start("fetch", total=10)
    progress.step(n=3)
    assert progress.skip_requested() is False  # the button was not pressed yet

    progress.request_skip("fetch", pid=os.getpid() + 1)  # another run
    assert progress.skip_requested() is False

    progress.request_skip("fetch", pid=os.getpid())
    progress.request_skip("fetch", pid=os.getpid())  # a double click does not duplicate the stage
    assert json.loads(progress.CONTROL_FILE.read_text(encoding="utf-8"))["skip"] == ["fetch"]
    assert progress.skip_requested() is True
    assert progress.was_skipped("fetch")
    assert p._stage("fetch")["note"] == "skipped by hand at 3 of 10"

    progress.start("filter")  # a skipped stage is closed as skipped, not done
    assert p._stage("fetch")["status"] == progress.SKIPPED
    progress.request_skip("filter", pid=os.getpid())
    assert progress.skip_requested() is False  # filter is not declared skippable
    p.finish()
    assert "fetch" not in progress.read_history()["main"][0]["stages"]  # a skipped stage is not in the history


def test_new_run_ignores_skip_request_of_previous_one(state):
    progress.CONTROL_FILE.write_text(json.dumps({"pid": os.getpid(), "skip": ["fetch"]}), encoding="utf-8")
    progress.Pipeline("main", STAGES, skippable={"fetch"})
    assert not progress.CONTROL_FILE.exists()


def test_warnings_go_to_errors_with_group(state):
    p = progress.Pipeline("main", STAGES)
    log = logging.getLogger("jobhunter.test")
    log.warning("Acme: the board no longer exists")
    log.warning("example.com: HTTP 503")
    log.error("crashed")
    log.info("not collected: below WARNING")
    groups = [(e["message"], e["group"]) for e in p.errors]
    assert groups == [("Acme: the board no longer exists", "action"), ("example.com: HTTP 503", "site"),
                      ("crashed", "code")]


def test_second_run_in_same_process_keeps_first_warning(state):
    """The collector of the previous run removed itself from the logger while logging walked the handler list;
    logging then skipped the next handler, and the first warning of the second run was lost."""
    first = progress.Pipeline("main", STAGES)
    first.finish()
    second = progress.Pipeline("main", STAGES)
    logging.getLogger("jobhunter.test").warning("first warning")
    assert [e["message"] for e in second.errors] == ["first warning"]
    assert first.errors == []


def test_errors_and_events_are_capped(state, monkeypatch):
    monkeypatch.setattr(progress, "MAX_ERRORS", 3)
    monkeypatch.setattr(progress, "MAX_EVENTS", 2)
    p = progress.Pipeline("main", STAGES)
    for i in range(5):
        logging.getLogger("jobhunter.test").warning("w%d", i)
        progress.event("found", f"e{i}" + "x" * 400)
    assert [e["message"] for e in p.errors] == ["w2", "w3", "w4"]
    assert len(p.events) == 2 and p.events[-1]["text"].startswith("e4") and len(p.events[-1]["text"]) == 300


def test_history_keeps_last_runs_and_gives_eta(state, monkeypatch):
    monkeypatch.setattr(progress, "HISTORY_RUNS", 2)
    for _ in range(3):
        p = progress.Pipeline("main", STAGES)
        progress.start("fetch")
        progress.start("filter")
        p.finish()
    runs = progress.read_history()["main"]
    assert len(runs) == 2 and set(runs[0]["stages"]) == {"fetch", "filter"}

    history = {"main": [{"stages": {"fetch": 100, "filter": 10, "report": 5}},
                        {"stages": {"fetch": 300, "filter": 30, "report": 7}},
                        {"stages": {"fetch": 200, "filter": 20}}]}
    progress.HISTORY_FILE.write_text(json.dumps(history), encoding="utf-8")
    data = {"script": "main", "status": progress.RUNNING, "eta_stage": 40, "stages": [
        {"key": "fetch", "status": progress.DONE, "started": None},
        {"key": "filter", "status": progress.RUNNING, "started": None},
        {"key": "report", "status": progress.PENDING, "started": None},
    ]}
    # filter: the stage ETA is 40; report: the median of [5, 7] -> 7 (the upper of two)
    assert progress.eta_total(data) == 47
    assert progress.eta_total({**data, "status": progress.DONE}) is None
    assert progress.eta_total({**data, "script": "applications"}) is None


def test_broken_files_read_as_empty(state):
    progress.PROGRESS_FILE.write_text("{not json", encoding="utf-8")
    progress.HISTORY_FILE.write_text("", encoding="utf-8")
    assert progress.read() is None and progress.read_history() == {}


def test_render_and_status_line(state, monkeypatch):
    monkeypatch.setattr(progress, "_width", lambda: 200)
    p = progress.Pipeline("main", STAGES)
    progress.start("fetch", total=4)
    progress.step("Acme", n=2)
    progress.item_started("Globex")
    logging.getLogger("jobhunter.test").warning("example.com: HTTP 503")
    progress.count(fetched=7)
    data = p.snapshot()
    line = progress.status_line(data)
    assert line.startswith("[1/3] Sources") and "2/4 50%" in line and line.endswith("Globex")
    text = progress.render(data)
    assert "▶  1. Sources" in text and "Processing now:" in text and "Globex" in text
    assert "Counters: fetched 7" in text and "Warnings (1" in text

    p.finish()
    done = progress.read()
    assert progress.status_line(done).startswith("[main] done")
    assert "✔  1. Sources" in progress.render(done)


def test_watch_once_prints_state_and_marks_dead_run(state, capsys, monkeypatch):
    progress.watch(once=True)
    assert "No runs yet" in capsys.readouterr().out

    p = progress.Pipeline("main", STAGES)
    progress.start("fetch")
    monkeypatch.setattr(progress, "_alive", lambda pid: False)  # the process died without marking the end
    progress.watch(once=True)
    assert "interrupted" in capsys.readouterr().out
    assert progress.is_alive(p.snapshot()) is False


def test_alive_sees_own_process():
    assert progress._alive(os.getpid()) is True


def test_rotate_errors_log(tmp_path, monkeypatch):
    from jobhunter import config

    log, prev = tmp_path / "errors.log", tmp_path / "errors.prev.log"
    monkeypatch.setattr(config, "ERRORS_LOG", log)
    monkeypatch.setattr(config, "ERRORS_LOG_PREV", prev)
    monkeypatch.delenv(progress.CONTINUE_RUN_ENV, raising=False)
    progress.rotate_errors_log()  # no log yet: does nothing
    assert not prev.exists()

    log.write_text("previous run", encoding="utf-8")
    monkeypatch.setenv(progress.CONTINUE_RUN_ENV, "1")  # the second script of the same run appends
    progress.rotate_errors_log()
    assert log.exists() and not prev.exists()

    monkeypatch.delenv(progress.CONTINUE_RUN_ENV)
    progress.rotate_errors_log()
    assert not log.exists() and prev.read_text(encoding="utf-8") == "previous run"
