"""Conclusions of "CV vs market" (market_check.advice): which advice and in which order for each report.

The advice texts are a golden file, tests/fixtures/market_advice.json: refactoring advice must not change them.
Changed on purpose — JOBHUNTER_UPDATE_GOLDEN=1 pytest tests/test_market_advice.py, and review the diff.
"""
import json
import os
from pathlib import Path

import pytest

from jobhunter import market_check

GOLDEN = Path(__file__).parent / "fixtures" / "market_advice.json"
UPDATE = bool(os.environ.get("JOBHUNTER_UPDATE_GOLDEN"))


def _title(name, share, in_headline=False):
    return {"name": name, "count": share, "share": share, "in_headline": in_headline}


def _kw(name, share, in_cv="", partial=""):
    return {"name": name, "count": share, "share": share, "in_cv": in_cv, "partial": partial}


CASES: dict[str, dict] = {
    "empty": {},
    "everything": {
        "titles": [_title("Software Engineer", 40), _title("Backend Engineer", 30, in_headline=True),
                   _title("Python Developer", 12), _title("AI Engineer", 20), _title("Full Stack", 15),
                   _title("Platform Engineer", 9)],
        "keywords": [_kw("Kafka", 30, "base"), _kw("Redis", 4, "base"), _kw("Go", 25), _kw("Rust", 9),
                     _kw("AWS", 40, "pdf", "S3 only"), _kw("GCP", 8, "pdf", "a little"), _kw("Python", 90, "pdf")],
        "hygiene": {"pages": {"Backend": 2, "AI": 1}, "stop_words": ["responsible for", "leveraged"],
                    "bullets": 10, "with_verb": 9, "with_numbers": 9,
                    "numbers_by_job": {"Globex": 6, "Acme": 4}, "long": ["a", "b"]},
    },
    "few_numbers": {
        "titles": [_title("AI Engineer", 25)],
        "hygiene": {"pages": {"Backend": 1}, "stop_words": [], "bullets": 10, "with_verb": 7, "with_numbers": 4,
                    "numbers_by_job": {"Globex": 3}, "long": []},
    },
    "clean": {
        "titles": [_title("Backend Engineer", 50, in_headline=True)],
        "keywords": [_kw("Python", 90, "pdf")],
        "hygiene": {"pages": {"Backend": 1}, "stop_words": [], "bullets": 10, "with_verb": 7, "with_numbers": 5,
                    "numbers_by_job": {"Globex": 4}, "long": []},
    },
    "no_bullets": {"hygiene": {"bullets": 0, "with_verb": 0, "with_numbers": 0}},
}


@pytest.fixture(scope="module")
def golden():
    if UPDATE:
        GOLDEN.write_text(json.dumps({name: market_check.advice(r) for name, r in CASES.items()},
                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CASES)
def test_advice_golden(name, golden):
    assert market_check.advice(CASES[name]) == golden[name]


def test_advice_order_and_count(golden):
    """The order is by usefulness: role titles, skills, then bullet hygiene."""
    first_words = [a.split()[0].rstrip(",:") for a in golden["everything"]]
    assert first_words == ["Your", "Frequent", "In", "Gaps", "Partly", "The", "Words", "All", "Numbers", "Long"]
    assert golden["few_numbers"][-1].startswith("Few bullets with a measurable result: 4 of 10")
    assert golden["clean"] == golden["empty"] == golden["no_bullets"] == []
