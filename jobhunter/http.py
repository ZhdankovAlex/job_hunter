"""HTTP client: at most 1 request per second per host + robots.txt check."""
import logging
import re
import threading
import time
from urllib.parse import urlsplit

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


class _Robots:
    """Minimal robots.txt parser with * and $ support (urllib cannot do them)."""

    def __init__(self, text, ua_token):
        self.rules = []  # (allow: bool, pattern: str)
        self.crawl_delay = 0.0
        groups, agents, body, in_agents = {}, [], [], False
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
                try:
                    self.crawl_delay = float(value)
                except ValueError:
                    pass

    @staticmethod
    def _matches(pattern, path):
        anchored = pattern.endswith("$")
        rx = ".*".join(re.escape(p) for p in pattern.rstrip("$").split("*"))
        return re.match(rx + ("$" if anchored else ""), path) is not None

    def allowed(self, path):
        best = (0, True)  # the longest rule wins, on a tie — allow
        for allow, pattern in self.rules:
            if self._matches(pattern, path):
                best = max(best, (len(pattern), allow))
        return best[1]


class Http:
    def __init__(self, min_interval=config.MIN_INTERVAL):
        self.min_interval = min_interval
        self._local = threading.local()
        self._guard = threading.Lock()
        self._host_locks = {}
        self._next_slot = {}
        self._robots = {}
        self._rate_limited = {}  # host -> how many requests in a row ended with 429

    def _session(self):
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers["User-Agent"] = config.USER_AGENT
            s.headers["Accept"] = "application/json"
            self._local.session = s
        return self._local.session

    def _host_lock(self, host):
        with self._guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _throttle(self, host):
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

    def _load_robots(self, scheme, host):
        r = None
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

    def _check_robots(self, url):
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

    def get(self, url, params=None, timeout=40, headers=None):
        """GET with the limit and retries. Returns requests.Response (404 included).

        headers — extra headers (for example, Accept: text/html for sites that answer 404 to JSON).
        """
        return self._request("GET", url, params=params, timeout=timeout, headers=headers)

    def post(self, url, json=None, timeout=40, headers=None):
        """POST with a JSON body — a vacancy search the site makes with a POST request (Workday).

        Same robots.txt, rate limit and retries as get(); sends nothing but the search query.
        """
        return self._request("POST", url, json=json, timeout=timeout, headers=headers)

    def _request(self, method, url, params=None, json=None, timeout=40, headers=None):
        url = requests.Request(method, url, params=params).prepare().url
        self._check_robots(url)
        host = urlsplit(url).netloc
        if self._rate_limited.get(host, 0) >= 3:
            # the host keeps answering 429 — leave it alone until the end of the run
            raise FetchError(f"{url}: host turned off until the next run (429)")
        last = None
        for attempt in range(3):
            if cancel.is_set():
                raise Cancelled(url)
            self._throttle(host)
            try:
                r = self._session().request(method, url, json=json, timeout=timeout, headers=headers)
            except requests.RequestException as e:
                last = str(e)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 5 * (attempt + 1)
                if cancel.wait(min(wait, 60)):
                    raise Cancelled(url)
                continue
            self._rate_limited[host] = 0
            return r
        if last == "HTTP 429":
            self._rate_limited[host] = self._rate_limited.get(host, 0) + 1
        raise FetchError(f"{url}: {last}")
