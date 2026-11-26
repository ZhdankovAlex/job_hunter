"""State files: atomic writes and broken JSON on input."""
import json
import os

import pytest

import main
from jobhunter import ats, config
from jobhunter.fsutil import atomic_write_text


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    """An interrupted write (here — a failing os.replace) keeps the old file intact and leaves no .tmp files."""
    target = tmp_path / "seen.json"
    atomic_write_text(target, '{"a": 1}')
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk gone")))
    with pytest.raises(OSError):
        atomic_write_text(target, '{"b": 2}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]


def test_broken_state_json_does_not_crash(tmp_path, monkeypatch):
    """Broken seen.json and ats_mapping.json — a warning and empty state, not a crashed run."""
    (tmp_path / "seen.json").write_text('{"a": ', encoding="utf-8")
    (tmp_path / "ats_mapping.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(config, "SEEN_FILE", tmp_path / "seen.json")
    assert main.load_seen() == {}
    resolver = ats.AtsClient(None,  # type: ignore[arg-type]
                             tmp_path / "ats_mapping.json")
    assert resolver.mapping == {}
    resolver.save()
    assert json.loads((tmp_path / "ats_mapping.json").read_text(encoding="utf-8")) == {}
