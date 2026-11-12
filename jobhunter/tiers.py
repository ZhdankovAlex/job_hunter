"""Vacancy category — the first ranking key; within a category — freshness, then score.

1. Backend      — product backend / Python / Software Engineer
2. ML / AI      — ML, AI, LLM engineering, Data Scientist
3. DevOps / platform — DevOps, SRE, infrastructure, platform roles, "operations" by the text
4. Other        — full-stack, frontend and vacancies whose key skill is a stack not from the CV
                  (TypeScript, Node.js, JavaScript, React, Go, Java…), and data/analytics roles
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import config as c

BACKEND, ML, OPS, OTHER = 1, 2, 3, 4
NAMES = {BACKEND: "1 · Backend", ML: "2 · ML / AI", OPS: "3 · DevOps / platform", OTHER: "4 · Other"}

_FULLSTACK_RX = re.compile(r"full[\s-]?stack|front[\s-]?end|фулстек|фронтенд|\bweb developer\b", re.I)
_ML_RX = re.compile(r"\bml\b|\bai\b|\bllms?\b|machine learning|genai|data scientist|deep learning|\bnlp\b|computer vision", re.I)
_DATA_RX = re.compile(r"data engineer|analytics engineer|data analyst|\bbi\b|data platform", re.I)
_BACKEND_TITLE_RX = re.compile(r"back[\s-]?end|б[эе]к[\s-]?енд|python|django|fastapi", re.I)
# key stack missing from the CV: if it is among "missing", the vacancy is not on your stack
FOREIGN_STACK = {"TypeScript", "JavaScript", "Node.js", "React", "Vue", "Angular", "Go", "Java", "Kotlin",
                 "Ruby", "PHP", ".NET / C#", "Scala", "Rust", "Elixir"}


def tier(job) -> int:
    """Vacancy category by the title, the text (verify penalties) and matching against the CV."""
    title = job.title
    missing = set(getattr(job, "cv_missing", []))
    if _FULLSTACK_RX.search(title) or (missing & FOREIGN_STACK) or getattr(job, "mismatch_lang", ""):
        return OTHER
    if _BACKEND_TITLE_RX.search(title):
        return BACKEND  # "Backend Platform Engineer", "Python Developer (AI)" — still backend
    if c.OPS_ROLE_RX.search(title) or c.PLATFORM_TITLE_RX.search(title) or "ops_heavy" in getattr(job, "penalties", []):
        return OPS
    if _DATA_RX.search(title):
        return OTHER
    if _ML_RX.search(title):
        return ML
    return BACKEND


# freshness group borders, in days: yesterday's vacancy with skill gaps matters more than a July one
# with a full match — a fresh one has a better chance of an invitation
AGE_BUCKETS = (3, 7, 14, 30)
AGE_NAMES = ("up to 3 days", "up to a week", "up to 2 weeks", "up to a month", "older than a month")
UNKNOWN_AGE_BUCKET = 3  # publication date unknown — assume the vacancy is up to a month old


def age_bucket(job) -> int:
    """Freshness group number: 0 — published in the last 3 days, 4 — older than a month."""
    if not job.posted:
        return UNKNOWN_AGE_BUCKET
    posted = job.posted if job.posted.tzinfo else job.posted.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - posted).days
    return next((i for i, limit in enumerate(AGE_BUCKETS) if days <= limit), len(AGE_BUCKETS))


def sort_key(job) -> tuple:
    """Category, then a clearly low salary range (down), then freshness group, then score, then the exact date."""
    from . import salary

    return (
        getattr(job, "tier", OTHER), salary.below_min(job), age_bucket(job), -job.relevance,
        -(job.posted.timestamp() if job.posted else 0),
    )
