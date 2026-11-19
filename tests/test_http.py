"""Http: a host that does not respond is skipped until the end of the run."""
import pytest
import requests

from jobhunter import http as http_mod


class _Session:
    """Fake session: an exception or a status code per URL; counts the calls."""

    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def request(self, method, url, **kwargs):
        self.calls.append(url)
        outcome = self.plan(url)
        if isinstance(outcome, Exception):
            raise outcome
        r = requests.Response()
        r.status_code = outcome
        return r


@pytest.fixture
def client(monkeypatch):
    def make(plan):
        h = http_mod.Http(min_interval=0)
        session = _Session(plan)
        monkeypatch.setattr(h, "_session", lambda: session)
        h._robots["api.lever.co"] = http_mod._Robots("", "")
        h._robots["ok.example"] = http_mod._Robots("", "")
        return h, session
    return make


def test_unreachable_host_is_skipped(client):
    h, session = client(lambda url: requests.ReadTimeout("read timed out"))
    for slug in ("a", "b"):
        with pytest.raises(http_mod.FetchError):
            h.get(f"https://api.lever.co/v0/postings/{slug}")
    # the first request makes 3 attempts, the second one (the host already failed) — one
    assert len(session.calls) == 4
    with pytest.raises(http_mod.FetchError, match="skipped until the end of the run"):
        h.get("https://api.lever.co/v0/postings/c")
    assert len(session.calls) == 4  # the host was not called again


def test_success_resets_and_other_hosts_unaffected(client):
    state = {"fail": True}
    h, _session = client(lambda url: requests.ConnectTimeout("x") if "lever" in url and state["fail"] else 200)
    with pytest.raises(http_mod.FetchError):
        h.get("https://api.lever.co/v0/postings/a")
    assert h.get("https://ok.example/jobs").status_code == 200
    state["fail"] = False
    assert h.get("https://api.lever.co/v0/postings/b").status_code == 200  # back up — the counter is reset
    state["fail"] = True
    with pytest.raises(http_mod.FetchError, match=r"timed out|ConnectTimeout|x"):
        h.get("https://api.lever.co/v0/postings/c")  # the first failure in a row again — not turned off yet
