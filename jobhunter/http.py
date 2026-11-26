"""HTTP client: at most 1 request per second per host + robots.txt check."""
import contextlib
import ipaddress
import logging
import re
import socket
import threading
import time
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

import requests

from . import config

log = logging.getLogger(__name__)

# "Skip stage" on sources: requests of unfinished sources are cut at once, not after
# their long crawl (a career page — up to 100 vacancies at one per second). Set and cleared by main.py.
cancel = threading.Event()


class Cancelled(BaseException):
    """The request was cancelled with the button. BaseException so it is not caught as a source error (except
    FetchError / except Exception): the source just exits without marking the board broken. Caught by the pool."""


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class PrivateAddress(FetchError):
    """The link points to this computer or the local network — such links from the web UI are not read."""


def _resolve(host: str) -> list[str]:
    """All addresses of the host (getaddrinfo); no DNS answer — empty. Replaced in tests (conftest: no_dns)."""
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]
    except (OSError, UnicodeError):
        return []


def _is_local(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])  # fe80::1%eth0 — without the zone
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified


def check_public_url(url: str) -> None:
    """A link sent from the web UI does not point to loopback, link-local, LAN or 0.0.0.0.

    Every address of the host is checked: requests may connect to any of them. A host that does not resolve
    is let through — the request fails by itself. Vacancy sources don't need this: their addresses come from
    code and settings, not from a form.

    Raises:
        PrivateAddress: at least one address of the host is local.
    """
    host = urlsplit(url).hostname or ""
    bad = [a for a in _resolve(host) if _is_local(a)]
    if bad:
        raise PrivateAddress(f"{url}: address {bad[0]} is this computer or the local network, such links are not read")


class _Robots:
    """Minimal robots.txt parser with * and $ support (urllib cannot do them)."""

    def __init__(self, text: str, ua_token: str) -> None:
        self.rules: list[tuple[bool, str]] = []  # (allow: bool, pattern: str)
        self.crawl_delay = 0.0
        groups: dict[str, list[tuple[str, str]]] = {}
        agents: list[str] = []
        body: list[tuple[str, str]] = []
        in_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not in_agents:
                    agents, body = [], []
                agents.append(value.lower())
                groups[value.lower()] = body
                in_agents = True
            else:
                in_agents = False
                body.append((key, value))
        group = next((g for a, g in groups.items() if a != "*" and a in ua_token), None)
        if group is None:
            group = groups.get("*", [])
        for key, value in group:
            if key in ("allow", "disallow") and value:
                self.rules.append((key == "allow", value))
            elif key == "crawl-delay":
                with contextlib.suppress(ValueError):
                    self.crawl_delay = float(value)

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        rx = ".*".join(re.escape(p) for p in pattern.rstrip("$").split("*"))
        return re.match(rx + ("$" if anchored else ""), path) is not None

    def allowed(self, path: str) -> bool:
        best = (0, True)  # the longest rule wins, on a tie — allow
        for allow, pattern in self.rules:
            if self._matches(pattern, path):
                best = max(best, (len(pattern), allow))
        return best[1]


# request params (values: str / int / list) and headers
Params = dict[str, Any]
Headers = dict[str, str]


class Response(Protocol):
    """What sources read from a response. Fits requests.Response and FakeResponse from the tests.

    Fields are read-only properties: requests.Response computes text, and a plain "text: str" in the protocol
    would require it to be assignable, so the real response would not fit the protocol.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def url(self) -> str: ...

    def json(self) -> Any: ...


@runtime_checkable
class HttpClient(Protocol):
    """HTTP client for sources: GET only. A structural type, no need to inherit from it.

    Anything with get() of this signature fits: Http below and FakeHttp in tests/conftest.py.
    """

    def get(self, url: str, params: Params | None = None, timeout: float = 40,
            headers: Headers | None = None) -> Response: ...


class HttpPostClient(HttpClient, Protocol):
    """A client that can POST too: for sites whose search is a POST request (Workday)."""

    def post(self, url: str, json: Any = None, timeout: float = 40,
             headers: Headers | None = None) -> Response: ...


_REDIRECT_CODES = {301, 302, 303, 307, 308}


class Http:
    # this many requests in a row ended with a timeout / network error — the host is down; a dead
    # host with 3 retries x 40 s per company can stretch a run to hours, so stop calling it
    UNREACHABLE_AFTER = 2
    # redirects in a row per request (as in browsers and RFC 9309 for robots.txt — 5); more — FetchError
    MAX_REDIRECTS = 5

    def __init__(self, min_interval: float = config.MIN_INTERVAL, public_only: bool = False) -> None:
        """public_only — for links from the web UI: every address, redirect steps included, passes check_public_url."""
        self.min_interval = min_interval
        self.public_only = public_only
        self._local = threading.local()
        self._guard = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._next_slot: dict[str, float] = {}
        self._robots: dict[str, _Robots | None] = {}
        self._rate_limited: dict[str, int] = {}  # host -> how many requests in a row ended with 429
        self._unreachable: dict[str, int] = {}  # host -> how many requests in a row hit a network error

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers["User-Agent"] = config.USER_AGENT
            s.headers["Accept"] = "application/json"
            self._local.session = s
        session: requests.Session = self._local.session
        return session

    def _host_lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _throttle(self, host: str) -> None:
        with self._host_lock(host):
            delay = self._next_slot.get(host, 0) - time.monotonic()
            if delay > 0 and cancel.wait(delay):
                raise Cancelled(host)
            robots = self._robots.get(host)
            interval = max(
                config.HOST_INTERVALS.get(host, self.min_interval),
                robots.crawl_delay if robots else 0,
            )
            self._next_slot[host] = time.monotonic() + interval

    def _load_robots(self, scheme: str, host: str) -> _Robots | None:
        r = None
        error: Exception | None = None
        for _ in range(2):  # one retry on a network failure
            self._throttle(host)
            try:
                r = self._session().get(f"{scheme}://{host}/robots.txt", timeout=20)
                break
            except requests.RequestException as e:
                error = e
        if r is None:
            log.warning("robots.txt unavailable for %s (%s) — host skipped", host, error)
            return None
        if r.status_code == 200:
            return _Robots(r.text, config.USER_AGENT.lower())
        if 400 <= r.status_code < 500:
            return _Robots("", "")  # no robots.txt — no restrictions (RFC 9309)
        log.warning("robots.txt %s returned %s — host skipped", host, r.status_code)
        return None

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        host = parts.netloc
        with self._guard:
            known = host in self._robots
        if not known:
            robots = self._load_robots(parts.scheme, host)
            with self._guard:
                self._robots.setdefault(host, robots)
        robots = self._robots[host]
        path = parts.path + ("?" + parts.query if parts.query else "")
        if robots is None or not robots.allowed(path):
            raise RobotsDisallowed(f"robots.txt disallows {url}")

    def get(self, url: str, params: Params | None = None, timeout: float = 40,
            headers: Headers | None = None) -> requests.Response:
        """GET with the limit and retries. Returns requests.Response (404 included).

        headers — extra headers (for example, Accept: text/html for sites that answer 404 to JSON).
        """
        return self._request("GET", url, params=params, timeout=timeout, headers=headers)

    def post(self, url: str, json: Any = None, timeout: float = 40,
             headers: Headers | None = None) -> requests.Response:
        """POST with a JSON body — a vacancy search the site makes with a POST request (Workday).

        Same robots.txt, rate limit and retries as get(); sends nothing but the search query.
        """
        return self._request("POST", url, json=json, timeout=timeout, headers=headers)

    def _request(self, method: str, url: str, params: Params | None = None, json: Any = None,
                 timeout: float = 40, headers: Headers | None = None) -> requests.Response:
        """A request that follows redirects by hand: every step goes through robots.txt and its host's rate limit.

        requests itself does not follow redirects (allow_redirects=False): otherwise a redirect target on
        another host would skip both robots.txt and the pause.
        """
        url = self._prepare_url(method, url, params)
        visited = {url}
        for _ in range(self.MAX_REDIRECTS):
            r = self._send(method, url, json, timeout, headers)
            location = r.headers.get("Location") if r.status_code in _REDIRECT_CODES else None
            if not location:
                return r
            if r.status_code == 303 or (r.status_code in (301, 302) and method == "POST"):
                method, json = "GET", None  # like browsers and requests: after 303 (and 301/302 on POST) — GET, no body
            url = self._prepare_url(method, urljoin(url, location))
            if url in visited:
                raise FetchError(f"{url}: redirect loop")
            visited.add(url)
        r = self._send(method, url, json, timeout, headers)
        if r.status_code in _REDIRECT_CODES and r.headers.get("Location"):
            raise FetchError(f"{url}: more than {self.MAX_REDIRECTS} redirects")
        return r

    @staticmethod
    def _prepare_url(method: str, url: str, params: Params | None = None) -> str:
        """URL with params; a bad link ("foo", ftp://, no host) — FetchError, not a requests exception."""
        try:
            prepared = requests.Request(method, url, params=params).prepare().url or url
        except (requests.RequestException, ValueError) as e:
            raise FetchError(f"{url!r}: invalid link ({e})") from e
        parts = urlsplit(prepared)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError(f"{url!r}: invalid link — needs http:// or https:// with a site address")
        return prepared

    def _send(self, method: str, url: str, json: Any, timeout: float,
              headers: Headers | None) -> requests.Response:
        """One step: robots.txt, disabled hosts, rate limit and retries on 429 / 5xx / network errors."""
        if self.public_only:
            check_public_url(url)  # before robots.txt: otherwise robots.txt would be fetched from a local address
        self._check_robots(url)
        host = urlsplit(url).netloc
        if self._rate_limited.get(host, 0) >= 3:
            # the host keeps answering 429 — leave it alone until the end of the run
            raise FetchError(f"{url}: host turned off until the next run (429)")
        if self._unreachable.get(host, 0) >= self.UNREACHABLE_AFTER:
            raise FetchError(f"{url}: host is not responding — skipped until the end of the run")
        last: str | None = None
        network_error = False
        for attempt in range(3):
            if cancel.is_set():
                raise Cancelled(url)
            self._throttle(host)
            try:
                r = self._session().request(method, url, json=json, timeout=timeout, headers=headers,
                                             allow_redirects=False)
            except requests.RequestException as e:
                last, network_error = str(e), True
                if self._unreachable.get(host, 0):
                    break  # the host already failed — 40 s retries only stretch the run
                continue
            network_error = False
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 5 * (attempt + 1)
                if cancel.wait(min(wait, 60)):
                    raise Cancelled(url)
                continue
            self._rate_limited[host] = 0
            self._unreachable[host] = 0
            return r
        if last == "HTTP 429":
            self._rate_limited[host] = self._rate_limited.get(host, 0) + 1
        if network_error:
            with self._guard:
                self._unreachable[host] = self._unreachable.get(host, 0) + 1
                down = self._unreachable[host] == self.UNREACHABLE_AFTER
            if down:
                log.warning("%s is not responding (%d requests in a row with a timeout or network error) — "
                            "requests to it are skipped until the end of the run", host, self.UNREACHABLE_AFTER)
        raise FetchError(f"{url}: {last}")
