"""API Pydantic models: request bodies (ApiModel — strict checks at the boundary), responses and errors."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ErrorDetail(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class Unprocessable(BaseModel):
    detail: list[ValidationIssue] | str = Field(
        description="A list of pydantic errors (the body does not match the schema) or a text to show in the UI "
                    "(bad regex, neither a link nor a vacancy text…)")


ERROR_TEXT = {400: "The request body could not be parsed (not JSON / not UTF-8)", 404: "Not found",
              409: "Conflicts with the current state: a run is / is not going on, the name is taken", 422: "Invalid data"}


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Endpoint error codes for OpenAPI: FastAPI itself describes only 422 and only as a list of pydantic errors,
    but endpoints also return their own 422 as a string (the UI shows it) — schemathesis checks both."""
    return {c: {"model": Unprocessable if c == 422 else ErrorDetail, "description": ERROR_TEXT[c]} for c in codes}


def _has_surrogate(value: object) -> bool:
    if isinstance(value, str):
        return any("\ud800" <= ch <= "\udfff" for ch in value)
    if isinstance(value, dict):
        return any(_has_surrogate(k) or _has_surrogate(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_surrogate(v) for v in value)
    return False


class ApiModel(BaseModel):
    """A request body. JSON allows "\\ud800", a lone surrogate: a string with it cannot be encoded to UTF-8, and
    writing it to a file failed (500) or, worse, truncated the file. Rejected on input: 422, like any bad input."""

    @model_validator(mode="before")
    @classmethod
    def no_surrogates(cls, data: object) -> object:
        if _has_surrogate(data):
            raise ValueError("the text has broken characters (lone UTF-16 surrogates)")
        return data


class RunRequest(ApiModel):
    """What to run."""
    kind: str = Field("all", description="all — mail, then vacancies (like run_daily.bat); main — only "
                                          "vacancies; applications — only mail", pattern="^(all|main|applications)$")
    skip: list[Literal["mail", "telegram", "freehire", "aggregators"]] = Field(
        default_factory=list, description="Stages turned off in advance: mail — do not run applications.py; "
                                          "telegram / freehire / aggregators — the --no-… flags of main.py")


class RunStarted(BaseModel):
    scripts: list[str] = Field(description="Commands in start order")


class Report(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    file: str = Field(description="File name: open /files/reports/<file>")
    size_kb: int
    built: str = Field(description="Build time, HH:MM")
    md: bool = Field(description="A Markdown version sits next to it")


class BuiltCv(BaseModel):
    name: str = Field(description="\"Company — Role\" from the first line of the .md, otherwise the file name")
    vacancy: str = Field(description="Link to the vacancy or ''")
    pdf: str
    md: str = Field(description="Link to the Markdown draft or ''")
    one_page: bool
    mtime: float
    built: str = Field(description="DD.MM HH:MM")


class Skill(BaseModel):
    name: str
    pattern: str = Field(description="Regular expression (case-insensitive)")
    base_weight: int = Field(description="Weight from profile.toml")
    weight: int = Field(description="Weight with the edit from profile_overrides.json")
    disabled: bool
    added: bool = Field(description="Your own skill, added from the UI")
    matches: int = Field(description="Fitting vacancies of the last report that mention the skill")
    share: int = Field(description="% of fitting vacancies")
    invalid: bool = Field(False, description="The pattern does not compile")


class SkillsReport(BaseModel):
    skills: list[Skill]
    relevant: int = Field(description="Fitting vacancies in the last report")
    day: str = Field(description="Day of the last report or ''")


class MarketFit(BaseModel):
    median: int | None = Field(description="Median CV-to-vacancy match, %; null — not measured")
    share80: int = Field(description="% of vacancies with a match of 80% or more")


class MarketKeyword(BaseModel):
    name: str
    count: int
    share: int = Field(description="% of fitting vacancies where the technology is a key one")
    in_cv: Literal["pdf", "base", ""] = Field(description="pdf — in the PDF CV, base — only in the base, '' — nowhere")
    partial: str = Field(description="A caveat: present, but partly (AWS: only S3) or ''")


class MarketTitle(BaseModel):
    name: str
    count: int
    share: int
    in_headline: bool = Field(description="The phrase is in the CV headline")


class MarketLevel(BaseModel):
    name: str
    count: int
    share: int


class MarketHygiene(BaseModel):
    stop_words: list[str] = Field(description="Filler words in experience bullets")
    numbers_by_job: dict[str, int] = Field(description="Company -> bullets with numbers")
    pages: dict[str, int] = Field(description="PDF -> pages")
    bullets: int
    with_numbers: int
    with_verb: int = Field(description="Bullets starting with an action verb")
    long: list[str] = Field(description="Too long bullets (the beginning)")
    avg_words: int
    no_numbers: list[str] = Field(description="Bullets without numbers (the beginning)")
    contacts: dict[str, bool] = Field(description="Contact -> found in the PDF")


class MarketReport(BaseModel):
    """Without vacancies in the archive — only day='', total=0, relevant=0 and advice."""
    day: str
    total: int = Field(description="Vacancies of the day")
    relevant: int = Field(description="Fitting by title and with a text")
    fit: MarketFit | None = None
    keywords: list[MarketKeyword] = []
    titles: list[MarketTitle] = []
    seniority: list[MarketLevel] = []
    headline: str = Field("", description="CV headlines joined with \" / \"")
    hygiene: MarketHygiene | None = None
    days: list[str] = Field([], description="Archive days, newest first")
    advice: list[str] = Field(description="Conclusions: what to fix in the CV")


class Source(BaseModel):
    """Common fields; the rest comes from state/sources_latest.json and ats_mapping.json as is (ATS: platform, slug…)."""
    model_config = ConfigDict(extra="allow")

    name: str
    kind: str = Field(description="ats / aggregator / site / career_page / …")
    jobs: int = Field(description="Vacancies the source gave")
    error: str = Field(description="Error or ''")
    reason: str = Field(description="Why there are no vacancies in the report (a gaps.groups key) or ''")
    detail: str
    checked: str = Field(description="When the board was checked, if it matters for the reason, or ''")
    platform: str | None = Field(None, description="ATS platform; null — board not found")
    slug: str | None = None


class SourceKind(BaseModel):
    sources: int
    jobs: int
    empty: int = Field(description="No vacancies and no error")
    errors: int


class FreehireQuery(BaseModel):
    name: str
    title: str
    params: dict[str, Any]
    total: int | None = Field(description="Vacancies on the site; null — before the first run")
    fetched: int | None = Field(description="Fetched; null — before the first run")
    skipped: bool = Field(description="The query is off (relocation, for example)")


class GapGroup(BaseModel):
    key: str
    title: str
    hint: str
    count: int


class GapRow(BaseModel):
    name: str
    reason: str
    detail: str
    checked: str


class Gaps(BaseModel):
    groups: list[GapGroup]
    rows: list[GapRow]
    reports: int | None = Field(description="Reports the companies were looked up in")


class SourcesReport(BaseModel):
    built: str = Field(description="Run time DD.MM HH:MM or ''")
    rows: list[Source]
    kinds: dict[str, SourceKind]
    telegram: list[str]
    freehire: list[FreehireQuery]
    gaps: Gaps


class SkipRequest(ApiModel):
    """Which stage of the running run to skip."""
    stage: str = Field(description="Stage key from /api/progress (stages[].key); the stage must be running and skippable")


class CompanyJob(BaseModel):
    title: str
    url: str
    day: str


class CompanyRow(BaseModel):
    name: str
    industries: list[str]
    by: str = Field(description="Where the industry comes from: `list` (known companies / companies.txt) or `vacancies`")
    vacancies: int = Field(description="Vacancies over all days, without repeats by title")
    last: str = Field(description="The last day with a vacancy or ''")
    status: str = Field(description="Application status or ''")
    board: str = Field(description="Link to the ATS board or ''")
    jobs: list[CompanyJob]


class SkillShare(BaseModel):
    name: str
    share: int = Field(description="% of the industry's vacancies")


class IndustryMarket(BaseModel):
    industry: str
    label: str
    vacancies: int
    remote_share: int | None = Field(description="% remote among vacancies with a known format; null — unknown")
    salaries: int = Field(description="Vacancies with a salary range")
    salary_median: float | None = Field(description="Median of the range middle, EUR/month; null — fewer than 3 ranges")
    skills: list[SkillShare]


class CompaniesReport(BaseModel):
    companies: list[CompanyRow]
    counts: dict[str, int] = Field(description="Companies per industry code")
    labels: dict[str, str] = Field(description="Industry code -> label")
    vacancies: int = Field(description="Archive vacancies without repeats")
    market: list[IndustryMarket]


class CvRequest(ApiModel):
    """Which vacancy to build a CV for: a link from a report, any link, or pasted text."""
    url: str = Field("", description="Link to the vacancy. First looked up in saved reports (state/jobs/), "
                                     "otherwise the page is read (robots.txt, 1 request/s)")
    date: str = Field("", description="Date YYYY-MM-DD of the report the button was clicked in — looked up there first")
    text: str = Field("", description="Vacancy text; if set, the link is not read")
    company: str = Field("", description="Company (refines or replaces the one found)")
    title: str = Field("", description="Vacancy title (refines or replaces the one found)")
    one_page: bool = Field(False, strict=True, description="One-page variant: shrinks until it fits")


class CvPick(BaseModel):
    section: str = Field(description="Summary or a company from the experience")
    text: str
    pin: bool = Field(description="The bullet is pinned in the base (present in every version)")
    techs: list[str] = Field(description="Vacancy technologies found in the bullet")
    topics: list[str] = Field(description="Vacancy topics shared with the bullet")
    similarity: float = Field(description="Text similarity of the bullet to the vacancy (TF-IDF, 0–1)")
    score: float = Field(description="Total score: technology 2, topic 1, similarity up to 4")


class CvResult(BaseModel):
    company: str
    title: str
    source: str = Field(description="Where the vacancy came from: report date, page — from the page, text — pasted")
    pdf: str = Field(description="Link to the PDF")
    md: str = Field(description="Link to the Markdown draft (it can be edited and rebuilt: cvgen.py --render)")
    pages: int
    one_page: bool
    fit: int = Field(description="Fit % of the CV to the vacancy")
    missing: list[str] = Field(description="Key vacancy skills missing from the CV")
    techs: list[str]
    topics: list[str]
    picks: list[CvPick] = Field(description="Picked bullets and why")


class CoverRequest(CvRequest):
    style: Literal["short", "full"] = Field("short", description="short — bulleted, full — in paragraphs")


class SkillChange(ApiModel):
    """A skill edit: weight and / or turning off. Applies from the next run."""
    weight: int | None = Field(None, ge=-30, le=30, description="New weight; negative is a penalty for the skill in a vacancy")
    disabled: bool | None = Field(None, strict=True, description="true — the skill does not count in the score")

    @field_validator("weight", mode="before")
    @classmethod
    def weight_not_bool(cls, value: object) -> object:
        return _not_bool(value)


def _not_bool(value: object) -> object:
    """A weight is a number. Pydantic in lax mode would take true as 1, and strict=True would reject 3.0 too (an
    integer by JSON Schema) — so only bool is cut off."""
    if isinstance(value, bool):
        raise ValueError("the weight is an integer, not true/false")
    return value


class NewSkill(ApiModel):
    """Your own skill: name, regular expression (case-insensitive) and weight."""
    name: str = Field(min_length=1, max_length=60)
    pattern: str = Field(min_length=1, description="Regular expression, searched in the vacancy title and text")
    weight: int = Field(2, ge=-30, le=30)

    @field_validator("weight", mode="before")
    @classmethod
    def weight_not_bool(cls, value: object) -> object:
        return _not_bool(value)


class ApplicationEvent(ApiModel):
    """An application event: written to applications_manual.toml, the applications page is rebuilt from the letter cache."""
    company: str = Field(min_length=1, description="Company — as in the report, the event is merged with letters by it")
    kind: Literal["applied", "interview", "rejected", "declined", "offer", "other"] = Field(
        description="applied — applied, interview — a stage, rejected — rejected, declined — declined by you, offer — offer, "
                    "other — a note")
    note: str = Field("", description="What happened")
    url: str = Field("", description="Link to the vacancy: reports will mark it \"🔁 already applied\"")
    source: str = Field("", description="Where: company site, LinkedIn, Telegram…")
    date: datetime | None = Field(None, description="Event time; now by default")

    @field_validator("date", mode="before")
    @classmethod
    def iso_date(cls, value: object) -> object:
        """Only an ISO string, as in the schema: pydantic would also take a number (Unix seconds) — "0" would become 1970."""
        if value is not None and not isinstance(value, str | datetime):
            raise ValueError("the date is an ISO 8601 string, e.g. 2026-11-18T10:00")
        return value
