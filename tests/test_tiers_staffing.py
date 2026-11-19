"""Vacancy category (tiers) and the intermediaries blacklist (staffing)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jobhunter import tiers
from jobhunter.staffing import Blacklist


@pytest.mark.parametrize(("title", "extra", "expected"), [
    ("Senior Backend Engineer", {}, tiers.BACKEND),
    ("Python Developer (AI)", {}, tiers.BACKEND),  # python in the title beats AI
    ("Full-Stack Engineer", {}, tiers.OTHER),
    ("Software Engineer", {"cv_missing": ["TypeScript"]}, tiers.OTHER),  # key stack not from the CV
    ("Software Engineer", {"mismatch_lang": "Go"}, tiers.OTHER),
    ("Site Reliability Engineer", {}, tiers.OPS),
    ("Software Engineer", {"penalties": ["ops_heavy"]}, tiers.OPS),  # "operations" by the text
    ("Data Engineer", {}, tiers.OTHER),
    ("Machine Learning Engineer", {}, tiers.ML),
    ("Software Engineer", {}, tiers.BACKEND),
])
def test_tier(title: str, extra: dict, expected: int) -> None:
    assert tiers.tier(SimpleNamespace(title=title, **extra)) == expected


@pytest.fixture
def blacklist(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Blacklist:
    path = tmp_path / "staffing_blacklist.txt"
    path.write_text("# comment\n\nAcme Staffing\nXKTalent\nre: \\btalent\\b\nre: [broken\n", encoding="utf-8")
    bl = Blacklist(path)
    assert "invalid pattern" in caplog.text  # a broken pattern is skipped with a warning, the rest is read
    return bl


@pytest.mark.parametrize(("company", "text", "reason"), [
    ("Acme Staffing", "", "blacklist"),
    ("XKTalent Inc. - Rimutee", "", "blacklist"),  # the same intermediary under a longer name
    ("Best Talent Partners", "", "looks like an intermediary"),
    ("Product Co", "We are an outstaffing company", 'outsourcing by the text: "outstaffing"'),
    ("Product Co", "We serve our clients worldwide", ""),  # product B2B companies write "our clients" too
])
def test_reason(blacklist: Blacklist, company: str, text: str, reason: str) -> None:
    assert blacklist.reason(company, "Backend Engineer", text) == reason


def test_short_name_not_prefix_matched(tmp_path: Path) -> None:
    """A short name in the list does not catch everyone whose name starts with it."""
    path = tmp_path / "list.txt"
    path.write_text("Acme\n", encoding="utf-8")
    assert Blacklist(path).reason("Acme Robotics", "Backend Engineer") == ""


def test_missing_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bl = Blacklist(tmp_path / "nope.txt")
    assert not bl.names and not bl.patterns and "not found" in caplog.text
