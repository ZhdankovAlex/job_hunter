"""Vacancy score (relevance.Profile): skills, title adjustments, penalties, edits from the UI, location.

The profile is a small profile.toml of its own in a temp folder with "telling" weights: the total shows which
adjustments fired. The example from the repository is not used — its weights change with the search.
"""
import json
from types import SimpleNamespace

import pytest

from jobhunter import geo, relevance
from jobhunter.language import RUSSIAN_MARK

PROFILE = """
[scoring]
title_python = 100
title_backend = 200
title_senior = 400
outsourcing_text = -1000
russian_language = 3000
go_required = -10000
language_mismatch = -20000
jvm_spring = -40000
years_8_plus = -80000
cv_full_match = 7
cv_missing_each = -2
cv_missing_cap = -5
cv_mismatch_each = -11
salary_eur_60k = 13
freehire_relocation = 17
company_priority_per_point = 19
local_language_nice = -23
not_english = -27
penalty_onsite = -29
location_relocation = 31
location_residents = -37
location_country = 41
location_other_country = -43
location_remote_ok = 47

[[skills]]
name = "Python"
weight = 3
pattern = '\\bpython\\b'

[[skills]]
name = "Kafka"
weight = 2
pattern = 'kafka'

[[skills]]
name = "Redis"
weight = 1
pattern = 'redis'
"""


@pytest.fixture
def profile_path(tmp_path, monkeypatch):
    path = tmp_path / "profile.toml"
    path.write_text(PROFILE, encoding="utf-8")
    monkeypatch.setattr(relevance, "OVERRIDES_FILE", tmp_path / "profile_overrides.json")
    return path


@pytest.fixture
def profile(profile_path):
    return relevance.Profile(profile_path)


def job(title="Engineer", **attrs):
    return SimpleNamespace(title=title, **attrs)


def score(profile, title="Engineer", text="", marks=(), location=0, **attrs):
    return profile.score(job(title, **attrs), text, list(marks), location)


def test_skills_add_up_and_are_sorted_by_weight(profile):
    total, names = score(profile, text="We use REDIS, Kafka and Python")
    assert total == 6
    assert names == ["Python", "Kafka", "Redis"]


def test_skill_in_title_counts_once(profile):
    total, names = score(profile, title="Kafka Engineer", text="kafka, kafka, kafka")
    assert (total, names) == (2, ["Kafka"])


def test_title_bonuses_come_from_title_only(profile):
    assert score(profile, title="Senior Backend Python Developer")[0] == 3 + 100 + 200 + 400
    # "senior" and "backend" in the text, not in the title: no title adjustments, only the skill
    assert score(profile, title="Engineer", text="senior backend python")[0] == 3


def test_outsourcing_penalty_from_text(profile):
    assert score(profile, text="Work for one of our clients")[0] == -1000


def test_russian_language_by_share_or_mark(profile):
    assert score(profile, text="Мы ищем разработчика в команду")[0] == 3000
    assert score(profile, text="Mostly English text, немного")[0] == 0
    assert score(profile, text="English text", marks=[RUSSIAN_MARK])[0] == 3000


def test_go_beats_language_mismatch(profile):
    """Go has its own penalty, and then the general "Python not primary" is not added on top."""
    assert score(profile, text="Strong experience with Golang")[0] == -10000
    assert score(profile, marks=["⚠️ Python not primary"])[0] == -20000
    assert score(profile, text="Strong experience with Golang", marks=["⚠️ Python not primary"])[0] == -10000


def test_go_in_text_ignored_for_python_title(profile):
    total, _ = score(profile, title="Python Developer", text="Strong experience with Golang is a plus")
    assert total == 3 + 100  # the Python skill from the title + the title adjustment


def test_jvm_and_years_penalties(profile):
    assert score(profile, text="Spring Boot services")[0] == -40000
    assert score(profile, text="8+ years of experience")[0] == -80000


def test_language_marks(profile):
    assert score(profile, marks=["🗣️ local language is a plus"])[0] == -23
    assert score(profile, marks=["🌐 not in English"])[0] == -27
    assert score(profile, text="Мы ищем разработчика", marks=["🌐 not in English"])[0] == 3000  # Russian is fine


def test_cv_missing_is_capped(profile):
    assert score(profile, cv_missing=["Rust"])[0] == -2
    assert score(profile, cv_missing=["Rust", "Scala"])[0] == -4
    assert score(profile, cv_missing=["a", "b", "c", "d", "e"])[0] == -5  # not below cv_missing_cap
    assert score(profile, cv_full=True, cv_mismatch=["Go", "Java"])[0] == 7 - 22


def test_penalties_and_company_priority(profile):
    assert score(profile, penalties=["onsite", "unknown"])[0] == -29  # an unknown penalty weighs 0
    assert score(profile, score=2)[0] == 2 * 19  # priority from companies.txt


@pytest.mark.parametrize("enrichment, points", [
    ({"salary_currency": "EUR", "salary_max": 60000}, 13),  # no period — yearly
    ({"salary_currency": "EUR", "salary_period": "month", "salary_min": 5000}, 13),
    ({"salary_currency": "EUR", "salary_period": "month", "salary_max": 4999}, 0),
    ({"salary_currency": "USD", "salary_max": 200000}, 0),
    ({"salary_currency": "EUR", "salary_period": "hour", "salary_max": 100}, 0),
    ({"relocation": "supported"}, 17),
])
def test_freehire_enrichment(profile, enrichment, points):
    assert score(profile, extra={"enrichment": enrichment})[0] == points


def test_location_points_are_added(profile):
    assert score(profile, location=31)[0] == 31


@pytest.mark.parametrize("attrs, points", [
    ({"reloc_type": geo.RELOCATION}, 31),
    ({"reloc_type": geo.RESIDENTS_ONLY}, -37),
    ({"reloc_type": "Germany"}, 41),
    ({"reloc_type": "", "manual_check": True}, 0),
    ({"reloc_type": "", "manual_check": False, "location_check": True}, -43),
    ({"reloc_type": "", "manual_check": False, "location_check": False}, 47),
])
def test_location_points(profile, attrs, points):
    assert profile.location_points(job(**attrs)) == points


def test_base_score_ignores_penalties(profile):
    j = job("Senior Python Developer", penalties=["onsite"], cv_missing=["Rust"])
    assert profile.base_score(j, "Kafka. Work for one of our clients")[0] == 3 + 2 + 100 + 400


def test_overrides_change_weight_disable_and_add(profile_path):
    relevance.write_overrides({"skills": {"Kafka": {"weight": 10}, "Redis": {"disabled": True}},
                               "added": [{"name": "Rust", "pattern": "rust", "weight": 5}]})
    total, names = score(relevance.Profile(profile_path), text="python kafka redis rust")
    assert names == ["Kafka", "Rust", "Python"]
    assert total == 10 + 5 + 3


def test_bad_pattern_is_skipped(profile_path, caplog):
    relevance.write_overrides({"skills": {}, "added": [{"name": "Broken", "pattern": "(rust", "weight": 5}]})
    p = relevance.Profile(profile_path)
    assert [s.name for s in p.skills] == ["Python", "Kafka", "Redis"]
    assert "Broken" in caplog.text


def test_broken_overrides_file_is_ignored(profile_path):
    relevance.OVERRIDES_FILE.write_text("{not json", encoding="utf-8")
    assert len(relevance.Profile(profile_path).skills) == 3
    assert json.loads(json.dumps(relevance.read_overrides())) == {"skills": {}, "added": []}
