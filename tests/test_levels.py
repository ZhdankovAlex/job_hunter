"""levels.fyi: country by location, Markdown page parsing, cache and the report line."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import FakeHttp

from jobhunter import levels
from jobhunter.http import FetchError

PAGE = """# Software Engineer Salary in Cyprus
**Currency:** EUR
- Median Total Compensation: €71,199
- 25th / 75th Percentile: €55.2K / €86.4K
- Sample Size: 1,137
"""
URL = levels.BASE_URL.format(slug="cyprus")


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "levels_cache.json"
    monkeypatch.setattr(levels, "CACHE_FILE", path)
    return path


@pytest.mark.parametrize(("location", "reloc", "slug"), [
    ("Limassol, Cyprus", "", "cyprus"),
    ("Berlin", "", "germany"),
    ("Remote", "Netherlands", "netherlands"),  # the relocation country counts too
    ("Remote, EMEA", "", levels.EUROPE),
    ("Remote", "", levels.REMOTE_DEFAULT),
    ("", "", levels.REMOTE_DEFAULT),  # no location — same as remote
    ("Singapore", "", ""),  # country not in LOCATIONS — cannot tell
])
def test_location_slug(location: str, reloc: str, slug: str) -> None:
    assert levels.location_slug(location, reloc)[0] == slug


@pytest.mark.parametrize(("text", "value"), [
    ("€71,199", 71199), ("$62.8K", 62800), ("CZK 1.2M per year", 1_200_000),    ("n/a", None), ("Not enough data.", None), ("—, —", None),  # a dot / comma without digits is not a number
])
def test_amount(text: str, value: float | None) -> None:
    assert levels._amount(text) == value


def test_parse() -> None:
    assert levels.parse(PAGE) == {"currency": "EUR", "median": 71199, "p25": 55200, "p75": 86400, "samples": 1137}


def test_parse_monthly_median_converted_to_year() -> None:
    page = PAGE.replace("€71,199", "RSD 300,000 (RSD 3,600,000 per year)")
    assert levels.parse(page)["median"] == 3_600_000  # type: ignore[index]


@pytest.mark.parametrize("page", [
    PAGE.replace("**Currency:** EUR", ""),  # a required field is missing
    PAGE.replace("€71,199", "Not enough data."),  # median without a number — no report line
])
def test_parse_incomplete_page(page: str) -> None:
    assert levels.parse(page) is None


def test_get_fetches_and_caches() -> None:
    http = FakeHttp({URL + ".md": PAGE})
    lv = levels.Levels(http)  # type: ignore[arg-type]
    data = lv.get("cyprus")
    assert data and data["url"] == URL and data["fetched"] == date.today().isoformat()
    lv.get("cyprus")
    assert http.calls == [URL + ".md"]  # second time — from the cache


def test_get_saves_and_reloads(cache: Path) -> None:
    lv = levels.Levels(FakeHttp({URL + ".md": PAGE}))  # type: ignore[arg-type]
    lv.get("cyprus")
    lv.save()
    assert levels.Levels(None).get("cyprus") == json.loads(cache.read_text(encoding="utf-8"))["cyprus"]


def _stale(cache: Path) -> dict:
    old = {"currency": "EUR", "median": 1, "p25": 1, "p75": 1, "samples": 1, "url": URL,
           "fetched": (date.today() - timedelta(days=levels.CACHE_DAYS)).isoformat()}
    cache.write_text(json.dumps({"cyprus": old}), encoding="utf-8")
    return old


def test_get_stale_cache_refetched(cache: Path) -> None:
    _stale(cache)
    data = levels.Levels(FakeHttp({URL + ".md": PAGE})).get("cyprus")  # type: ignore[arg-type]
    assert data and data["median"] == 71199


def test_get_without_http_returns_stale_cache(cache: Path) -> None:
    old = _stale(cache)
    assert levels.Levels(None).get("cyprus") == old


def test_get_broken_page_keeps_stale_cache(cache: Path, caplog: pytest.LogCaptureFixture) -> None:
    old = _stale(cache)
    assert levels.Levels(FakeHttp({})).get("cyprus") == old  # type: ignore[arg-type]  # 404
    assert "could not parse the page (HTTP 404)" in caplog.text


def test_get_fetch_error_keeps_stale_cache(cache: Path) -> None:
    class Failing:
        def get(self, url: str) -> None:
            raise FetchError("timeout")

    old = _stale(cache)
    assert levels.Levels(Failing()).get("cyprus") == old  # type: ignore[arg-type]


def test_broken_cache_file_ignored(cache: Path) -> None:
    cache.write_text("{not json", encoding="utf-8")
    assert levels.Levels(None).cache == {}


def test_describe() -> None:
    lv = levels.Levels(FakeHttp({URL + ".md": PAGE}))  # type: ignore[arg-type]
    text, url = lv.describe("Limassol")
    assert text == "levels.fyi, Cyprus: median ≈ €5.9K/mo, 25–75%: €4.6K–7.2K (1137 salaries)"
    assert url == URL


def test_describe_unknown_currency_shows_original() -> None:
    lv = levels.Levels(FakeHttp({URL + ".md": PAGE.replace("EUR", "XYZ")}))  # type: ignore[arg-type]
    assert lv.describe("Cyprus")[0] == "levels.fyi, Cyprus: XYZ 71,199/yr"


def test_describe_without_quartiles_rate() -> None:
    lv = levels.Levels(FakeHttp({URL + ".md": PAGE.replace("€55.2K / €86.4K", "— / —")}))  # type: ignore[arg-type]
    assert lv.describe("Cyprus")[0] == "levels.fyi, Cyprus: median ≈ €5.9K/mo (1137 salaries)"


@pytest.mark.parametrize("location", ["Singapore", "Cyprus"])
def test_describe_no_data(location: str) -> None:
    assert levels.Levels(None).describe(location) == ("", "")  # country unknown / no cache and no network
