"""Http: project rules — robots.txt, at most 1 request per second per host, retries, 429 and cancellation.

No network: the session is faked, time is a fake clock (time.monotonic and cancel.wait move it).
"""
import pytest
import requests

from jobhunter import config, http as http_mod


class Clock:
    """Fake clock: monotonic() is the current time, wait(d) "sleeps" d seconds; waits are recorded in .waits."""

    def __init__(self):
        self.now, self.waits, self.cancelled = 1000.0, [], False

    def monotonic(self):
        return self.now

    # the threading.Event interface that http.py uses
    def wait(self, delay):
        self.waits.append(delay)
        self.now += delay
        return self.cancelled

    def is_set(self):
        return self.cancelled


class Session:
    """A requests session without network: route(method, url) -> status code, (code, text, headers) or an exception."""

    def __init__(self, route):
        self.route, self.calls, self.kwargs = route, [], []

    def _answer(self, method, url):
        self.calls.append((method, url))
        outcome = self.route(method, url)
        if isinstance(outcome, Exception):
            raise outcome
        code, text, headers = outcome if isinstance(outcome, tuple) else (outcome, "", {})
        r = requests.Response()
        r.status_code, r._content, r.url = code, text.encode(), url
        r.headers.update(headers)
        return r

    def get(self, url, timeout=None):
        return self._answer("GET", url)

    def request(self, method, url, **kwargs):
        self.kwargs.append(kwargs)
        return self._answer(method, url)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(http_mod.time, "monotonic", c.monotonic)
    monkeypatch.setattr(http_mod, "cancel", c)
    return c


@pytest.fixture
def make(clock, monkeypatch):
    def make(route, robots_txt=200, min_interval=1.0):
        """robots_txt: status code or text (200) of the /robots.txt answer."""
        def full(method, url):
            if url.endswith("/robots.txt"):
                return (200, robots_txt, {}) if isinstance(robots_txt, str) else robots_txt
            return route(method, url)
        h = http_mod.Http(min_interval=min_interval)
        session = Session(full)
        monkeypatch.setattr(h, "_session", lambda: session)
        return h, session
    return make


def pages(session):
    return [u for _, u in session.calls if not u.endswith("/robots.txt")]


# --- parsing robots.txt ------------------------------------------------------------------------

ROBOTS = """
User-agent: *
Disallow: /private
Allow: /private/open
Disallow: /*.pdf$
Crawl-delay: 2

User-agent: otherbot
Disallow: /
"""


@pytest.mark.parametrize("path, allowed", [
    ("/jobs", True),
    ("/private/x", False),
    ("/private/open/1", True),  # a longer allow overrides disallow
    ("/files/cv.pdf", False),   # * and $
    ("/files/cv.pdf?x=1", True),  # $ is the end of the path, with a query it no longer matches
])
def test_robots_rules(path, allowed):
    robots = http_mod._Robots(ROBOTS, "job-hunter/1.0")
    assert robots.allowed(path) is allowed
    assert robots.crawl_delay == 2


def test_robots_own_group_wins_over_star():
    text = "User-agent: *\nDisallow:\n\nUser-agent: job-hunter\nUser-agent: other\nDisallow: /api\n"
    robots = http_mod._Robots(text, "mozilla job-hunter/1.0")
    assert not robots.allowed("/api/v1")
    assert http_mod._Robots(text, "somebot").allowed("/api/v1")  # an empty Disallow allows everything


def test_robots_bad_crawl_delay_and_comments():
    robots = http_mod._Robots("User-agent: * # all\nCrawl-delay: soon\nDisallow: /x # closed\nnot a rule\n", "ua")
    assert robots.crawl_delay == 0
    assert not robots.allowed("/x")


# --- robots.txt in requests ----------------------------------------------------------------------

def test_disallowed_url_is_not_requested(make):
    h, session = make(lambda m, u: 200, robots_txt="User-agent: *\nDisallow: /private\n")
    with pytest.raises(http_mod.RobotsDisallowed):
        h.get("https://site.example/private/jobs")
    assert pages(session) == []
    assert h.get("https://site.example/jobs").status_code == 200
    # robots.txt is read once per host
    assert [u for _, u in session.calls].count("https://site.example/robots.txt") == 1


def test_query_counts_for_robots(make):
    h, session = make(lambda m, u: 200, robots_txt="User-agent: *\nDisallow: /*?page=\n")
    with pytest.raises(http_mod.RobotsDisallowed):
        h.get("https://site.example/jobs", params={"page": 2})
    assert pages(session) == []


def test_missing_robots_means_no_limits(make):
    h, _ = make(lambda m, u: 200, robots_txt=404)
    assert h.get("https://site.example/anything").status_code == 200


@pytest.mark.parametrize("robots_txt", [503, requests.ConnectionError("down")])
def test_unavailable_robots_skips_host(make, robots_txt):
    """5xx or a network error on robots.txt — the rules are unknown, so the host is not visited."""
    h, session = make(lambda m, u: 200, robots_txt=robots_txt)
    with pytest.raises(http_mod.RobotsDisallowed):
        h.get("https://site.example/jobs")
    assert pages(session) == []
    robots_tries = [u for _, u in session.calls if u.endswith("/robots.txt")]
    assert len(robots_tries) == (2 if isinstance(robots_txt, Exception) else 1)  # one retry on a network failure


# --- rate limit -------------------------------------------------------------------------------

def test_one_request_per_second_per_host(make, clock):
    h, _ = make(lambda m, u: 200, robots_txt=404)
    clock.waits.clear()
    for _ in range(3):
        h.get("https://a.example/jobs")
    h.get("https://b.example/jobs")
    # the first request to a waits after robots.txt, then one per second; host b does not wait for a (only for its robots)
    assert clock.waits == [1.0, 1.0, 1.0, 1.0]


def test_crawl_delay_and_host_interval_raise_the_limit(make, clock, monkeypatch):
    h, _ = make(lambda m, u: 200, robots_txt="User-agent: *\nCrawl-delay: 3\n")
    h.get("https://slow.example/1")
    clock.waits.clear()
    h.get("https://slow.example/2")
    assert clock.waits == [3.0]

    monkeypatch.setitem(config.HOST_INTERVALS, "slower.example", 5.0)
    h.get("https://slower.example/1")
    clock.waits.clear()
    h.get("https://slower.example/2")
    assert clock.waits == [5.0]


# --- retries, 429, 5xx ---------------------------------------------------------------------------

def test_server_error_is_retried_then_succeeds(make, clock):
    answers = iter([502, 503, 200])
    h, session = make(lambda m, u: next(answers), robots_txt=404)
    assert h.get("https://site.example/jobs").status_code == 200
    assert len(pages(session)) == 3
    assert 5 in clock.waits and 10 in clock.waits  # the pause grows: 5 s, 10 s


def test_retry_after_is_respected_and_capped(make, clock):
    h, _ = make(lambda m, u: (429, "", {"Retry-After": "120"}), robots_txt=404)
    with pytest.raises(http_mod.FetchError, match="HTTP 429"):
        h.get("https://site.example/jobs")
    assert clock.waits.count(60) == 3  # Retry-After 120 — wait at most a minute


def test_host_off_after_three_runs_of_429(make):
    h, session = make(lambda m, u: (429, "", {"Retry-After": "1"}), robots_txt=404)
    for _ in range(3):
        with pytest.raises(http_mod.FetchError, match="HTTP 429"):
            h.get("https://site.example/jobs")
    before = len(pages(session))
    with pytest.raises(http_mod.FetchError, match=r"turned off.*429"):
        h.get("https://site.example/jobs")
    assert len(pages(session)) == before


def test_404_is_returned_not_raised(make):
    h, session = make(lambda m, u: 404, robots_txt=404)
    assert h.get("https://site.example/gone").status_code == 404
    assert len(pages(session)) == 1


def test_post_goes_through_same_rules(make):
    h, session = make(lambda m, u: 200, robots_txt="User-agent: *\nDisallow: /admin\n")
    assert h.post("https://site.example/wday/jobs", json={"q": "python"}).status_code == 200
    with pytest.raises(http_mod.RobotsDisallowed):
        h.post("https://site.example/admin/search", json={})
    assert session.calls[-1] == ("POST", "https://site.example/wday/jobs")


# --- cancellation --------------------------------------------------------------------------------------

def test_cancel_before_request(make, clock):
    h, session = make(lambda m, u: 200, robots_txt=404)
    h.get("https://site.example/1")
    clock.cancelled = True
    with pytest.raises(http_mod.Cancelled):
        h.get("https://site.example/2")
    assert len(pages(session)) == 1


def test_cancel_during_throttle_wait(make, clock):
    h, session = make(lambda m, u: 200, robots_txt=404)
    h.get("https://site.example/1")  # the next slot is in a second
    clock.cancelled = True
    with pytest.raises(http_mod.Cancelled):
        h._throttle("site.example")
    assert len(pages(session)) == 1


def test_cancel_during_retry_pause(make, clock):
    def route(m, u):
        clock.cancelled = True  # the button was pressed while the server answered 503
        return 503
    h, session = make(route, robots_txt=404)
    with pytest.raises(http_mod.Cancelled):
        h.get("https://site.example/jobs")
    assert len(pages(session)) == 1


def test_cancelled_is_not_a_source_error():
    """Cancellation must not be caught as a source failure (except Exception / except FetchError)."""
    assert not issubclass(http_mod.Cancelled, Exception)


def test_session_is_per_thread_with_project_user_agent():
    h = http_mod.Http()
    s = h._session()
    assert h._session() is s
    assert s.headers["User-Agent"] == config.USER_AGENT


# --- redirects: every step goes through robots.txt and its host's rate limit ----------------------

def _redirect(location, code=302):
    return (code, "", {"Location": location})


def test_requests_do_not_follow_redirects_themselves(make):
    h, session = make(lambda m, u: 200, robots_txt=404)
    h.get("https://site.example/jobs")
    assert session.kwargs[-1].get("allow_redirects") is False


def test_redirect_is_followed_manually(make):
    routes = {"https://a.example/old": _redirect("https://b.example/new"), "https://b.example/new": (200, "ok", {})}
    h, session = make(lambda m, u: routes[u], robots_txt=404)
    r = h.get("https://a.example/old")
    assert r.status_code == 200 and r.url == "https://b.example/new"
    # robots.txt of the new host is read before requesting it
    assert [u for _, u in session.calls] == ["https://a.example/robots.txt", "https://a.example/old",
                                             "https://b.example/robots.txt", "https://b.example/new"]


def test_redirect_to_disallowed_path_is_not_requested(make):
    def route(m, u):
        return _redirect("/private/x") if u == "https://site.example/jobs" else 200
    h, session = make(route, robots_txt="User-agent: *\nDisallow: /private\n")
    with pytest.raises(http_mod.RobotsDisallowed):
        h.get("https://site.example/jobs")
    assert pages(session) == ["https://site.example/jobs"]


def test_relative_location_and_throttle_for_redirect(make, clock):
    routes = {"https://site.example/a/1": _redirect("../b/2", 301), "https://site.example/b/2": 200}
    h, session = make(lambda m, u: routes[u], robots_txt=404)
    clock.waits.clear()
    assert h.get("https://site.example/a/1").status_code == 200
    assert pages(session) == ["https://site.example/a/1", "https://site.example/b/2"]
    assert clock.waits == [1.0, 1.0]  # after robots.txt and before the redirect step — a second per host


def test_redirect_loop_is_an_error(make):
    routes = {"https://site.example/a": _redirect("/b"), "https://site.example/b": _redirect("/a")}
    h, session = make(lambda m, u: routes[u], robots_txt=404)
    with pytest.raises(http_mod.FetchError, match="redirect loop"):
        h.get("https://site.example/a")
    assert len(pages(session)) == 2


def test_too_many_redirects(make):
    def route(m, u):
        n = int(u.rsplit("/", 1)[1])
        return _redirect(f"/{n + 1}")
    h, session = make(route, robots_txt=404)
    with pytest.raises(http_mod.FetchError, match="more than 5 redirects"):
        h.get("https://site.example/0")
    assert len(pages(session)) == http_mod.Http.MAX_REDIRECTS + 1


@pytest.mark.parametrize(("code", "method"), [(301, "GET"), (302, "GET"), (303, "GET"), (307, "POST"), (308, "POST")])
def test_post_redirect_method(make, code, method):
    routes = {"https://site.example/search": _redirect("/search2", code), "https://site.example/search2": 200}
    h, session = make(lambda m, u: routes[u], robots_txt=404)
    h.post("https://site.example/search", json={"q": "python"})
    assert session.calls[-1] == (method, "https://site.example/search2")
    assert ("json" in session.kwargs[-1] and session.kwargs[-1]["json"] is not None) is (method == "POST")


def test_redirect_without_location_is_returned(make):
    h, _ = make(lambda m, u: 302, robots_txt=404)
    assert h.get("https://site.example/jobs").status_code == 302


# --- bad links: FetchError, not requests exceptions -----------------------------------------------

@pytest.mark.parametrize("url", ["foo", "ftp://site.example/x", "https://", "http://[bad"])
def test_bad_url_is_fetch_error(make, url):
    h, session = make(lambda m, u: 200, robots_txt=404)
    with pytest.raises(http_mod.FetchError, match="invalid link"):
        h.get(url)
    assert session.calls == []


# --- links from the web UI: public addresses only -------------------------------------------------

@pytest.mark.parametrize(("url", "private"), [
    ("http://127.0.0.1:8765/api/runs", True),
    ("http://localhost/x", True),
    ("http://169.254.169.254/latest/meta-data", True),  # link-local, cloud metadata
    ("http://10.1.2.3/", True),
    ("http://192.168.1.1/", True),
    ("http://router.lan/admin", True),
    ("http://0.0.0.0:8765/", True),
    ("http://[::1]/", True),
    ("http://[::ffff:127.0.0.1]/", True),  # IPv4 inside IPv6
    ("http://[fe80::1]/", True),
    ("https://jobs.example.com/1", False),
    ("http://8.8.8.8/", False),
])
def test_check_public_url(url, private):
    if private:
        with pytest.raises(http_mod.PrivateAddress, match="local network"):
            http_mod.check_public_url(url)
    else:
        http_mod.check_public_url(url)


def test_check_public_url_every_resolved_address(monkeypatch):
    """A host with one public and one private address is refused: requests may connect to either."""
    monkeypatch.setattr(http_mod, "_resolve", lambda host: ["93.184.215.14", "10.0.0.7"])
    with pytest.raises(http_mod.PrivateAddress):
        http_mod.check_public_url("https://mixed.example/")


def test_unresolvable_host_is_left_to_the_request(monkeypatch):
    monkeypatch.setattr(http_mod, "_resolve", lambda host: [])
    http_mod.check_public_url("https://no-such-host.example/")  # no DNS answer — the request itself fails


def test_public_only_blocks_redirect_into_lan(clock, monkeypatch):
    def route(m, u):
        return _redirect("http://192.168.1.1/admin") if u == "https://site.example/job" else 200
    session = Session(lambda m, u: 404 if u.endswith("/robots.txt") else route(m, u))
    h = http_mod.Http(min_interval=1.0, public_only=True)
    monkeypatch.setattr(h, "_session", lambda: session)
    with pytest.raises(http_mod.PrivateAddress):
        h.get("https://site.example/job")
    assert [u for _, u in session.calls] == ["https://site.example/robots.txt", "https://site.example/job"]


def test_sources_http_does_not_resolve(make, monkeypatch):
    """The plain Http (vacancy sources) does not check addresses and never touches DNS."""
    def no_resolve(host):
        raise AssertionError("Http() without public_only must not resolve")
    monkeypatch.setattr(http_mod, "_resolve", no_resolve)
    h, _ = make(lambda m, u: 200, robots_txt=404)
    assert h.get("https://site.example/jobs").status_code == 200
