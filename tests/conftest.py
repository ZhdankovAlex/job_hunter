"""Common test setup.

jobhunter modules read settings.toml on import. So that tests give the same result for everyone and in CI,
the example files from the repo are put in place before any import (no personal settings needed). Settings —
settings.example.toml with test values (home country, relocation countries), see _test_settings().
"""
import os
import socket
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from jobhunter.http import HttpClient

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_connect(self: socket.socket, address: Any) -> None:
    """Network in tests goes only through FakeHttp. Loopback is allowed: on Windows asyncio builds
    a socketpair by connecting to 127.0.0.1, without it no event loop starts."""
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in _LOOPBACK:
        raise RuntimeError(f"test went to the network: {address!r} — use FakeHttp")
    _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in _LOOPBACK:
        raise RuntimeError(f"test went to the network: {address!r} — use FakeHttp")
    return _real_connect_ex(self, address)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)



def _test_settings() -> Path:
    """settings.example.toml + values the tests rely on: home country (a made-up example —
    Poland), relocation countries, an excluded country, "Russia only", experience in the CV."""
    text = (ROOT / "settings.example.toml").read_text(encoding="utf-8")
    for old, new in [
        ('country = ""', 'country = "Poland"'),
        ('iso = ""', 'iso = "PL"'),
        ('places = []', 'places = ["Warsaw", "Krakow", "Польша", "Варшава"]'),  # Russian spellings for Telegram posts
        ('relocation_countries = ["Netherlands", "Germany", "Spain"]',
         'relocation_countries = ["Spain", "Netherlands", "Portugal", "Cyprus", "Georgia", "Germany"]'),
        ("exclude_countries = []", 'exclude_countries = ["India"]'),
        ("[places]\n", '[places]\nIndia = ["Bengaluru", "Bangalore", "Hyderabad", "Pune"]\n'
                       'Portugal = ["Lisbon", "Lisboa", "Porto"]\nCyprus = ["Limassol", "Nicosia", "Larnaca"]\n'
                       'Georgia = ["Tbilisi", "Batumi", "Kutaisi"]\n'),
        ("hide_russia_only = false", "hide_russia_only = true"),
        ("years_experience = 0", "years_experience = 6"),
    ]:
        assert old in text, f"settings.example.toml changed: \"{old}\" is missing"
        text = text.replace(old, new, 1)
    path = Path(tempfile.mkdtemp(prefix="jobhunter-tests-")) / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


os.environ["JOBHUNTER_SETTINGS"] = str(_test_settings())
os.environ["JOBHUNTER_PROFILE"] = str(ROOT / "profile.example.toml")
os.environ["JOBHUNTER_CV_BASE"] = str(ROOT / "cv" / "base.example.toml")
os.environ["JOBHUNTER_NO_WARM"] = "1"  # web.app does not compute the cache in the background on real data


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = ""):
        self.text, self.status_code, self.url = text, status_code, url

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class FakeHttp:
    """Instead of jobhunter.http.Http: address -> response text; requests are recorded in .calls. No network."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str, params: dict | None = None, timeout: float = 40,
            headers: dict | None = None) -> FakeResponse:
        self.calls.append(url)
        if url not in self.pages:
            return FakeResponse("", 404, url)
        return FakeResponse(self.pages[url], 200, url)


Answer = str | tuple[int, str] | None


class RouteHttp:
    """A fake for APIs with parameters and POST: route(method, url, params_or_json) picks the answer.

    route returns the text (200), (code, text) or None (404). Requests go to .calls: (method, url, params|json).
    """

    def __init__(self, route: Any):
        self.route = route
        self.calls: list[tuple[str, str, Any]] = []

    def _answer(self, method: str, url: str, data: Any) -> FakeResponse:
        self.calls.append((method, url, data))
        answer: Answer = self.route(method, url, data or {})
        if answer is None:
            return FakeResponse("", 404, url)
        if isinstance(answer, tuple):
            return FakeResponse(answer[1], answer[0], url)
        return FakeResponse(answer, 200, url)

    def get(self, url: str, params: dict | None = None, timeout: float = 40,
            headers: dict | None = None) -> FakeResponse:
        return self._answer("GET", url, params)

    def post(self, url: str, json: Any = None, timeout: float = 40,
             headers: dict | None = None) -> FakeResponse:
        return self._answer("POST", url, json)


if TYPE_CHECKING:  # mypy checks the fake against the sources' protocol: a diverging signature fails CI
    from jobhunter.http import HttpPostClient

    _fake_is_client: HttpClient = FakeHttp({})
    _route_is_client: HttpPostClient = RouteHttp(lambda *a: None)


@pytest.fixture
def now():
    return datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def at(now):
    """at(-3) — three days before the test's "now"."""
    return lambda days, hours=0: now + timedelta(days=days, hours=hours)


def fake_resolve(host: str) -> list[str]:
    """DNS in tests: an IP literal is itself, localhost — 127.0.0.1, *.lan — a LAN address, the rest — public."""
    import ipaddress

    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        pass
    if host == "localhost":
        return ["127.0.0.1", "::1"]
    if host.endswith(".lan"):
        return ["192.168.1.20"]
    return ["93.184.215.14"]


@pytest.fixture(autouse=True)
def no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "address is not in the local network" check (http.check_public_url) resolves the host — no DNS in tests."""
    from jobhunter import http

    monkeypatch.setattr(http, "_resolve", fake_resolve)
