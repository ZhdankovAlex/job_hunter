"""The settings.toml model (Pydantic): keys, types and defaults in one place.

A typo in a key or a wrong type is an error with the key path (`freehire.max_reqests`), not a silent default.
The file is read not on import but on the first `get()`, once per process; `use()` puts ready settings in place
(tests, saving from the browser later). Check a file: `python -m jobhunter.settings --check`.
"""
from __future__ import annotations

import argparse
import io
import sys
import tomllib
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Words = list[str]


class _Section(BaseModel):
    # strict: TOML already gives real types, so the string "60" instead of 60 is an error, not a silent cast
    model_config = ConfigDict(extra="forbid", strict=True)


class Home(_Section):
    country: str = ""
    iso: str = ""
    places: Words = []


class AtsOverride(_Section):
    greenhouse_board_token: str = ""


class Filters(_Section):
    hide_russia_only: bool = False


class Sources(_Section):
    disabled: Words = []


class FreehireQuery(BaseModel):
    """A freehire query. Besides name/title/requires_relocation, these are API parameters as is: the API checks
    their names itself (meta.ignored_params stops the run), so extra keys are allowed here."""

    model_config = ConfigDict(extra="allow", strict=True)
    name: str
    title: str = ""
    requires_relocation: bool = False

    def params(self) -> dict[str, Any]:
        """The query's API parameters (everything except name, title, requires_relocation)."""
        return dict(self.__pydantic_extra__ or {})


class Freehire(_Section):
    enabled: bool = True
    max_requests: int = 20
    cache_hours: float = 6
    pages_per_query: int = 3
    max_experience_years: int = 7
    descriptions_top: int = 200
    common: dict[str, str | int | bool] = {}
    queries: list[FreehireQuery] = []


class Boards(_Section):
    remoterocketship_url: str = ""
    remoterocketship_pages: int = 5
    brenxor_url: str = ""
    brenxor_pages: int = 4
    brenxor_max_details: int = 40
    justjoin_url: str = ""
    remotecom_query: str = ""
    remotecom_pages: int = 3


class Bigtech(_Section):
    queries: Words = ["python", "backend"]
    pages: int = 3
    details_per_company: int = 60
    companies: Words = []
    relocation_hubs: Words = []
    # [company, host before .myworkdayjobs.com, tenant, site]
    workday: list[Annotated[list[str], Field(min_length=4, max_length=4)]] = []


class ManualJob(_Section):
    """A vacancy on an intermediary's site: the company does not match the domain."""

    url: str
    company: str = ""
    location: str = ""


class ManualJobs(_Section):
    urls: list[str | ManualJob] = []


class CareerPage(_Section):
    url: str
    location: str = ""
    links: str = ""


class Telegram(_Section):
    enabled: bool = False
    channels: Words = []
    cis_channels: Words = []
    max_age_days: int = 60
    aggregator_hosts: Words = []


class Salary(_Section):
    min_monthly_eur: float = 0
    rates_to_eur: dict[str, float] = {}


class Cv(_Section):
    files: Words = []
    names: Words = []
    style: str = "classic"
    chrome: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    years_experience: int = 0
    english_level: str = ""
    has_masters_or_phd: bool = False
    auto_fit_min: int = 80
    auto_max: int = 40
    partial: dict[str, str] = {}


class Outreach(_Section):
    pitch: str = "Backend Engineer with Python and PostgreSQL experience"
    based_in: str = ""


class Gamification(_Section):
    weekly_goal: int = 8


class Mail(_Section):
    query: str = "newer_than:365d -in:sent -in:chats -in:spam -in:trash"
    timeout_days: int = 14
    ignore_senders: Words = []
    own_names: Words = []
    utc_offset_hours: float | None = None
    company_aliases: dict[str, str] = {}
    sender_company: dict[str, str] = {}


class Settings(_Section):
    relocation_countries: Words = []
    any_country: bool = False
    exclude_countries: Words = []
    referral_companies: Words = []
    blocked_urls: Words = []
    relocation_enabled: bool = True
    country_bound_remote: bool = True
    max_age_days: int = 30
    priority_companies: Words = []
    top_limit: int = 100
    top_per_company: int = 3
    must_report_companies: Words = []
    hide_previous_rejections: bool = False
    home: Home = Home()
    places: dict[str, Words] = {}
    relocation_companies: dict[str, Words] = {}
    ats_overrides: dict[str, AtsOverride] = {}
    filters: Filters = Filters()
    sources: Sources = Sources()
    freehire: Freehire = Freehire()
    boards: Boards = Boards()
    bigtech: Bigtech = Bigtech()
    manual_jobs: ManualJobs = ManualJobs()
    career_pages: dict[str, str | CareerPage] = {}
    industry_companies: dict[str, Words] = {}
    telegram: Telegram = Telegram()
    salary: Salary = Salary()
    cv: Cv = Cv()
    outreach: Outreach = Outreach()
    gamification: Gamification = Gamification()
    mail: Mail = Mail()


class SettingsError(ValueError):
    """The settings file cannot be read or does not fit the model; the text is one line per error, with the key path."""


def _describe(e: ValidationError) -> list[str]:
    lines = []
    for err in e.errors():
        loc = [str(p) for p in err["loc"]]
        # str | CareerPage: pydantic adds the branch name to the path, which is noise for a human
        loc = [p for p in loc if p not in ("str", "CareerPage", "ManualJob")]
        msg = "unknown key (a typo?)" if err["type"] == "extra_forbidden" else err["msg"]
        lines.append(f"{'.'.join(loc) or '<root>'}: {msg}")
    return lines


def parse(text: str, source: str = "settings.toml") -> Settings:
    """TOML text -> Settings; an error is a SettingsError with all problems at once."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{source}: not valid TOML: {e}")
    try:
        return Settings.model_validate(data)
    except ValidationError as e:
        raise SettingsError(f"{source}:\n" + "\n".join(f"  {line}" for line in _describe(e)))


def load(path: Path) -> Settings:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SettingsError(f"{path}: cannot be read: {e}")
    return parse(text, str(path))


_current: Settings | None = None


def get() -> Settings:
    """The process settings: config.SETTINGS_FILE is read on the first call, later calls return the same value."""
    global _current
    if _current is None:
        from . import config  # here: config itself reads [home] through get() on import

        _current = load(config.SETTINGS_FILE)
    return _current


def use(settings: Settings) -> None:
    """Put settings in place without reading the file. Modules read settings.get() when called, not on import,
    so the change applies to all of them at once."""
    global _current
    _current = settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobhunter.settings", description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="check the settings file and exit")
    ap.add_argument("path", nargs="?", type=Path, help="the file (by default settings.toml or the example)")
    args = ap.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):  # a Windows console not in UTF-8: keep non-ASCII text readable
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.check:
        ap.print_help()
        return 2
    if args.path:
        path = args.path
    else:
        from . import config

        path = config.SETTINGS_FILE
    try:
        load(path)
    except SettingsError as e:
        print(f"Settings error: {e}")
        return 1
    print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
