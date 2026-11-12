"""Matching a vacancy against the CV (Fit %, key skills and nice-to-have). The CV is cv/base.example.toml."""
from types import SimpleNamespace

import pytest

from jobhunter import cv_match


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(cv_match, "CACHE_FILE", tmp_path / "cv_text.json")  # do not touch the owner's state/
    return cv_match.CvProfile({})


def job(title="Backend Engineer", company="Acme"):
    return SimpleNamespace(title=title, company=company, mismatch_lang="")


SENTENCES = [
    "Strong Python and PostgreSQL", "Experience with Kafka or RabbitMQ", "Golang services", "Go developer wanted",
    "We go to the office on Fridays", "JavaScript and TypeScript", "Java backend", "Kubernetes, Docker, Terraform",
    "React Native app", "Nothing technical here at all", "embeddings and vector search", "CI/CD with GitHub Actions",
]


@pytest.mark.parametrize("sentence", SENTENCES)
def test_combined_tech_regex_matches_individual_ones(sentence):
    """The prefilter with one shared pattern must answer the same as ~100 separate searches (Go — case-sensitive)."""
    individual = any(rx.search(sentence) for rx in cv_match.TECH_RX.values())
    assert bool(cv_match._TECH_ANY_RX.search(sentence)) == individual


def test_every_tech_name_is_found_by_combined_regex():
    for name, rx in cv_match.TECH_RX.items():
        sample = rx.pattern.replace("\\b", "").split("|")[0]
        if rx.search(sample):  # the pattern recognises itself in a simple form — the shared pattern must too
            assert cv_match._TECH_ANY_RX.search(sample), name


def test_core_and_nice_split(profile):
    holder = job()
    profile.analyze(holder, "Requirements: Python, PostgreSQL and Kubernetes.\nNice to have: Rust.")
    assert {"Python", "PostgreSQL"} <= set(holder.cv_core_have)
    assert "Kubernetes" in holder.cv_core_missing
    assert "Rust" in holder.cv_nice_missing and "Rust" not in holder.cv_core_missing
    assert 0 < holder.cv_fit < 100


def test_any_of_alternatives(profile):
    """"Python, Go or Java" — one of those listed is enough, the rest do not count as a gap."""
    holder = job()
    profile.analyze(holder, "You write Python, Golang or Java every day.")
    assert "Python" in holder.cv_core_have
    assert "Java" not in holder.cv_missing


def test_years_mismatch(profile):
    holder = job()
    profile.analyze(holder, "We need 9+ years of experience with Python.")
    assert any("9+ years of experience" in m for m in holder.cv_mismatch)
    assert not holder.cv_full


def test_unknown_term_from_requirement_line(profile):
    holder = job()
    profile.analyze(holder, "Python developer.\nRequirements:\n- Experience with Temporal workflows\n")
    assert "Temporal" in holder.cv_missing


def test_known_term_cache_is_consistent(profile):
    assert profile._known_term("Python") is True
    assert profile._known_term("Python") is True  # from the cache
    assert profile._known_term("Temporal") is False
