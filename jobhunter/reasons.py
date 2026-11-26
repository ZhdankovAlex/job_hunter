"""Codes of reject reasons and vacancy marks.

Decisions (the group on the pipeline chart, the order in "Rejected", the score) are made by the code, not by
a substring of the text: the wording shown to a person can be edited without touching the logic. The reason
text with details (language, age, years of experience) is in job.reject, the code is in job.reject_code.
"""
from enum import StrEnum


class Reject(StrEnum):
    """Why a vacancy is hidden. The value is the code in state/ and logs; the human text is in job.reject."""

    NOT_FULL_TIME = "not_full_time"  # part-time, freelance, hourly or piecework pay
    SOURCE_LOCATION = "source_location"  # source label: not remote and not the home country
    RUSSIA_ONLY = "russia_only"
    AI_TRAINER = "ai_trainer"
    ONSITE = "onsite"  # an office outside the home country by the text
    COUNTRY_BOUND = "country_bound"  # you must live / have the right to work in another country
    MAIN_LANGUAGE = "main_language"  # the primary language is not Python (Go, Java, ...)
    JVM = "jvm"
    REQUIRED_NOT_PYTHON = "required_not_python"
    EXPERIENCE = "experience"
    TOO_OLD = "too_old"


# short group for the chart on the pipeline page
GROUPS: dict[Reject, str] = {
    Reject.SOURCE_LOCATION: "location", Reject.RUSSIA_ONLY: "location",
    Reject.ONSITE: "location", Reject.COUNTRY_BOUND: "location",
    Reject.MAIN_LANGUAGE: "other language", Reject.JVM: "other language",
    Reject.REQUIRED_NOT_PYTHON: "other language",
    Reject.NOT_FULL_TIME: "not full-time", Reject.AI_TRAINER: "not full-time",
    Reject.EXPERIENCE: "experience", Reject.TOO_OLD: "old",
}
# bulk reasons: a few examples each in "Rejected", after the rules by the text
BULK = {Reject.SOURCE_LOCATION, Reject.TOO_OLD}
# location by the text is the most important rule, it goes first in "Rejected"
LOCATION_TEXT = {Reject.ONSITE, Reject.COUNTRY_BOUND}


class Mark(StrEnum):
    """Marks the score depends on. The value is the text in the report; with a detail — "text (Go)"."""

    NOT_ENGLISH = "🗣️ vacancy not in English"
    LOCAL_MUST = "🚫 local language required (must-have)"
    LOCAL_NICE = "🗣️ local language is a plus"
    RUSSIAN = "🇷🇺 Russian — a plus/required"
    PYTHON_NOT_PRIMARY = "⚠️ Python not primary language"

    def detail(self, value: str) -> str:
        """A mark with a detail: Mark.PYTHON_NOT_PRIMARY.detail("Go") -> "⚠️ ... language (Go)"."""
        return f"{self.value} ({value})"


def has_mark(marks: list[str], mark: Mark) -> bool:
    """Whether the mark is present — by itself or with a detail in brackets."""
    return any(m == mark or m.startswith(f"{mark.value} (") for m in marks)


def mark_detail(marks: list[str], mark: Mark) -> str:
    """The mark's detail ("Go" from "... (Go)") or ''."""
    prefix = f"{mark.value} ("
    return next((m[len(prefix):-1] for m in marks if m.startswith(prefix) and m.endswith(")")), "")
