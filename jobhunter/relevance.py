"""Relevance of a vacancy to the profile from profile.toml (skills from the CV)."""
from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import config, geo
from .reasons import Mark, has_mark, mark_detail

if TYPE_CHECKING:
    from .models import Checked, Job
    from .telegram import TgJob

log = logging.getLogger(__name__)

_TITLE_PYTHON_RX = re.compile(r"python|django|fastapi|flask", re.I)
_TITLE_BACKEND_RX = re.compile(r"back[\s-]?end|б[эе]к[\s-]?енд", re.I)
_TITLE_DATABASE_RX = re.compile(r"postgres|database|\bdb\b|\bdbaas\b", re.I)
_TITLE_SENIOR_RX = re.compile(r"\bsenior\b|\bsr\.?\b|старш|сеньор", re.I)
_TITLE_PRODUCT_RX = re.compile(r"\bproduct\b|продукт", re.I)
_TITLE_PLATFORM_RX = re.compile(r"platform|infrastructure|\binfra\b|internal tools|платформ", re.I)
_OUTSOURCING_RX = re.compile(
    r"our clients?\b|for (?:a|one of our) client|outstaff\w*|outsourc\w*|staff augmentation|"
    r"consultancy|наш(?:его|их)? клиент|аутстаф\w*|аутсорс\w*",
    re.I,
)
_CYRILLIC_RX = re.compile(r"[а-яё]", re.I)
# Russian in the requirements or a Russian-speaking team (the russian_language adjustment in profile.toml)
_RU_SPEAKING_RX = re.compile(
    r"russian[\s-]speak\w*|native russian|fluent (?:in )?russian|russian (?:language )?(?:is )?(?:a )?(?:plus|must|required)|"
    r"(?:knowledge of|speak\w*) russian|русскоязычн\w+|русскоговорящ\w+|на русском языке",
    re.I,
)
_LETTER_RX = re.compile(r"[a-zа-яё]", re.I)


@dataclass(frozen=True)
class Skill:
    """A skill from the CV: name for the report, weight and pattern to search in the vacancy text."""

    name: str
    weight: int
    rx: re.Pattern[str]


# Skill edits from the web UI (the "Skills" page) — on top of profile.toml, so its comments stay untouched:
# {"skills": {name: {"weight": N} | {"disabled": true}}, "added": [{name, pattern, weight}]}
OVERRIDES_FILE = config.ROOT / "profile_overrides.json"


def read_overrides() -> dict[str, Any]:
    import json
    try:
        data = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"skills": data.get("skills", {}), "added": data.get("added", [])}


def write_overrides(data: dict[str, Any]) -> None:
    import json
    # encode before opening the file: on UnicodeEncodeError write_text would leave it empty and lose the edits
    OVERRIDES_FILE.write_bytes(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))


def skill_table(path: Path = config.PROFILE_FILE) -> list[dict[str, Any]]:
    """All skills with edits applied: name, pattern, base_weight, weight, disabled, added (own, from the UI)."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    over = read_overrides()
    rows = []
    for item in [*data.get("skills", []), *({**a, "added": True} for a in over["added"])]:
        change = over["skills"].get(item["name"], {})
        base = int(item.get("weight", 1))
        rows.append({
            "name": item["name"], "pattern": item["pattern"], "base_weight": base,
            "weight": int(change.get("weight", base)), "disabled": bool(change.get("disabled")),
            "added": bool(item.get("added")),
        })
    return rows


class Profile:
    """Skills and score adjustments loaded from profile.toml (+ edits from profile_overrides.json)."""

    def __init__(self, path: Path = config.PROFILE_FILE) -> None:
        """Reads profile.toml and the edits; invalid patterns are skipped and written to errors.log."""
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        self.weights: dict[str, int] = data.get("scoring", {})
        self.skills: list[Skill] = []
        for item in skill_table(path):
            if item["disabled"]:
                continue
            try:
                rx = re.compile(item["pattern"], re.I)
            except re.error as e:
                log.warning("profile: invalid skill pattern %r (%s)", item["name"], e)
                continue
            self.skills.append(Skill(item["name"], item["weight"], rx))

    def _w(self, key: str) -> int:
        return int(self.weights.get(key, 0))

    def score(self, job: Checked, text: str, marks: list[str], location_points: int) -> tuple[int, list[str]]:
        """Relevance score and matched skills (by weight, descending).

        job — Job or TgJob; text — where to look for skills; marks — marks already set
        (language etc.); location_points — location adjustment.
        """
        title = job.title
        full = f"{title}\n{text}"
        matched = [s for s in self.skills if s.rx.search(full)]
        total = sum(s.weight for s in matched) + location_points

        for rx, key in (
            (_TITLE_PYTHON_RX, "title_python"),
            (_TITLE_BACKEND_RX, "title_backend"),
            (_TITLE_DATABASE_RX, "title_database"),
            (_TITLE_SENIOR_RX, "title_senior"),
            (_TITLE_PRODUCT_RX, "title_product"),
            (_TITLE_PLATFORM_RX, "title_platform"),
        ):
            if rx.search(title):
                total += self._w(key)
        if _OUTSOURCING_RX.search(text):
            total += self._w("outsourcing_text")

        total += getattr(job, "score", 0) * self._w("company_priority_per_point")
        letters = _LETTER_RX.findall(full)
        in_russian = bool(letters) and len(_CYRILLIC_RX.findall(full)) / len(letters) > 0.3
        if in_russian or has_mark(marks, Mark.RUSSIAN) or _RU_SPEAKING_RX.search(full):
            total += self._w("russian_language")
        has_python_title = bool(_TITLE_PYTHON_RX.search(title))
        mismatch = has_mark(marks, Mark.PYTHON_NOT_PRIMARY) or getattr(job, "language_mismatch_warning", False)
        go = (
            getattr(job, "mismatch_lang", "") == "Go"
            or mark_detail(marks, Mark.PYTHON_NOT_PRIMARY) == "Go"
            or (not has_python_title and bool(config.GO_REQUIRED_RX.search(text)))
        )
        if go:
            total += self._w("go_required")
        elif mismatch:
            total += self._w("language_mismatch")
        if config.JVM_SPRING_RX.search(full):
            total += self._w("jvm_spring")
        if config.YEARS_8_PLUS_RX.search(full):
            total += self._w("years_8_plus")
        if has_mark(marks, Mark.LOCAL_NICE):
            total += self._w("local_language_nice")
        if has_mark(marks, Mark.NOT_ENGLISH) and not in_russian:
            total += self._w("not_english")

        for penalty in getattr(job, "penalties", []):
            total += self._w(f"penalty_{penalty}")
        # match with the CV: a full one goes up, gaps and mismatches go down
        if getattr(job, "cv_full", False):
            total += self._w("cv_full_match")
        missing = len(getattr(job, "cv_missing", []))
        total += max(missing * self._w("cv_missing_each"), self._w("cv_missing_cap")) if missing else 0
        total += len(getattr(job, "cv_mismatch", [])) * self._w("cv_mismatch_each")

        extra = getattr(job, "extra", None) or {}
        enrichment = extra.get("enrichment") or {}
        if enrichment.get("relocation") == "supported":
            total += self._w("freehire_relocation")
        if enrichment.get("salary_currency") == "EUR":
            yearly = {"year": 1, "month": 12}.get(enrichment.get("salary_period") or "year", 0)
            top_salary = max(enrichment.get("salary_min") or 0, enrichment.get("salary_max") or 0)
            if top_salary * yearly >= 60000:
                total += self._w("salary_eur_60k")

        matched.sort(key=lambda s: -s.weight)
        return total, [s.name for s in matched]

    def base_score(self, job: Checked, text: str) -> tuple[int, list[str]]:
        """Score only by skills and role title — without penalties and location adjustments."""
        full = f"{job.title}\n{text}"
        matched = sorted((s for s in self.skills if s.rx.search(full)), key=lambda s: -s.weight)
        total = sum(s.weight for s in matched)
        for rx, key in (
            (_TITLE_PYTHON_RX, "title_python"), (_TITLE_BACKEND_RX, "title_backend"),
            (_TITLE_DATABASE_RX, "title_database"), (_TITLE_SENIOR_RX, "title_senior"),
        ):
            if rx.search(job.title):
                total += self._w(key)
        return total, [s.name for s in matched]

    def location_points(self, job: Job) -> int:
        """Location adjustment for a vacancy from an ATS/aggregator."""
        if job.reloc_type == geo.RELOCATION:
            return self._w("location_relocation")
        if job.reloc_type == geo.RESIDENTS_ONLY:
            return self._w("location_residents")
        if job.reloc_type:
            return self._w("location_country")
        if job.manual_check:
            return 0  # ❓ location not confirmed by the text
        return self._w("location_other_country") if job.location_check else self._w("location_remote_ok")

    def tg_location_points(self, job: TgJob) -> int:
        """Location adjustment for a vacancy from Telegram (by its label)."""
        return self._w("location_remote_ok") if job.label.startswith("✅") and not job.manual_check else 0
