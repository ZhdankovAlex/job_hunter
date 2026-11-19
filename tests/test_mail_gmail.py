"""Mail: message cache, sync freshness, the Gmail client (without network) and manual application events.

Parsing message text (classify, company_of) is in test_mail.py.
"""
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from jobhunter import config, mail


@pytest.fixture(autouse=True)
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAIL_CACHE_FILE", tmp_path / "state" / "mail_cache.json")
    monkeypatch.setattr(config, "MAIL_SYNC_FILE", tmp_path / "state" / "mail_sync.json")
    monkeypatch.setattr(config, "MANUAL_APPLICATIONS_FILE", tmp_path / "applications_manual.toml")
    return tmp_path


# --- cache and freshness --------------------------------------------------------------------------------

def test_cached_messages(files):
    assert mail.cached_messages() == []
    (files / "state").mkdir()
    config.MAIL_CACHE_FILE.write_text(json.dumps({"m1": {"id": "m1"}}), encoding="utf-8")
    assert mail.cached_messages() == [{"id": "m1"}]


def test_sync_state_and_stale_warning(monkeypatch):
    assert "has not been updated yet" in mail.stale_warning()
    mail.save_sync()
    assert mail.stale_warning() == ""
    mail.save_sync("token revoked")
    warning = mail.stale_warning()
    assert warning.startswith("Mail was not updated, data as of") and "token revoked" in warning
    state = json.loads(config.MAIL_SYNC_FILE.read_text(encoding="utf-8"))
    assert state["ok_at"]  # an error does not erase the time of the last success


def test_error_without_any_success():
    mail.save_sync("no network")
    assert "no fresh data: no network" in mail.stale_warning()


def test_old_sync_is_stale():
    old = (datetime.now(UTC) - timedelta(hours=mail.SYNC_STALE_HOURS + 1)).isoformat()
    config.MAIL_SYNC_FILE.parent.mkdir()
    config.MAIL_SYNC_FILE.write_text(json.dumps({"ok_at": old, "error": ""}), encoding="utf-8")
    assert mail.stale_warning().startswith("Mail has not been updated for a long time")


# --- Gmail client ------------------------------------------------------------------------------------

def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class FakeGmailSession:
    """AuthorizedSession without network: path (after /users/me/) -> response body or a function(params)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None):
        path = url.removeprefix(mail.API + "/")
        self.calls.append((path, params))
        answer = self.routes[path]
        body = answer(params) if callable(answer) else answer

        class R:
            def raise_for_status(self):
                if isinstance(body, Exception):
                    raise body

            def json(self):
                return body
        return R()


def gmail(routes):
    g = mail.Gmail.__new__(mail.Gmail)  # without AuthorizedSession and OAuth
    g.session, g._next, g.requests = FakeGmailSession(routes), 0.0, 0
    return g


MESSAGE = {
    "id": "m1", "threadId": "t1", "internalDate": "1763000000000",
    "payload": {
        "headers": [{"name": "From", "value": "Acme Talent <jobs@acme.example>"},
                    {"name": "Subject", "value": "Interview invitation"}],
        "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<p>HTML copy</p>")}},
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("Plain text: let's talk")}},
            ]},
            {"mimeType": "image/png", "body": {"data": b64("png")}},
        ],
    },
}


def test_gmail_throttles_one_request_per_second(monkeypatch):
    clock = {"now": 100.0}
    sleeps = []
    monkeypatch.setattr(mail.time, "monotonic", lambda: clock["now"])

    def sleep(d):
        sleeps.append(d)
        clock["now"] += d
    monkeypatch.setattr(mail.time, "sleep", sleep)
    g = gmail({"profile": {"emailAddress": "me@example.com"}})
    assert g.address() == "me@example.com"
    clock["now"] += 0.25
    g.address()
    assert sleeps == [pytest.approx(config.MIN_INTERVAL - 0.25)]
    assert g.requests == 2


def test_message_ids_follow_pages(monkeypatch):
    monkeypatch.setattr(mail.time, "sleep", lambda d: None)
    pages = {None: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"}, "p2": {"messages": [{"id": "c"}]}}
    g = gmail({"messages": lambda params: pages[params.get("pageToken")]})
    assert g.message_ids("newer_than:30d") == ["a", "b", "c"]
    assert g.session.calls[0][1]["q"] == "newer_than:30d"


def test_message_prefers_plain_text(monkeypatch):
    monkeypatch.setattr(mail.time, "sleep", lambda d: None)
    msg = gmail({"messages/m1": MESSAGE}).message("m1")
    assert msg == {"id": "m1", "thread": "t1", "date": 1763000000, "from": "Acme Talent <jobs@acme.example>",
                   "subject": "Interview invitation", "body": "Plain text: let's talk"}


def test_body_falls_back_to_html():
    payload = {"mimeType": "text/html", "body": {"data": b64("<div>Thanks for <b>applying</b></div>")}}
    assert mail._body_text(payload).split() == ["Thanks", "for", "applying"]
    assert mail._body_text({}) == ""


# --- fetch_messages ---------------------------------------------------------------------------------

class FakeGmail:
    def __init__(self, creds, ids=("old", "new1", "bad", "new2")):
        self.ids, self.read = list(ids), []

    def address(self):
        return "me@example.com"

    def message_ids(self, query):
        return self.ids

    def message(self, msg_id):
        self.read.append(msg_id)
        if msg_id == "bad":
            raise ValueError("broken MIME")
        return {"id": msg_id}


def test_fetch_reads_only_new_and_survives_broken_letter(monkeypatch, files):
    made = []
    monkeypatch.setattr(mail, "credentials", lambda: "creds")
    monkeypatch.setattr(mail, "Gmail", lambda creds: made.append(FakeGmail(creds)) or made[-1])
    (files / "state").mkdir()
    config.MAIL_CACHE_FILE.write_text(json.dumps({"old": {"id": "old", "cached": True}}), encoding="utf-8")

    messages, address = mail.fetch_messages()
    assert address == "me@example.com"
    assert made[0].read == ["new1", "bad", "new2"]  # cached messages are not read again
    assert [m["id"] for m in messages] == ["old", "new1", "new2"] and messages[0]["cached"]
    assert set(json.loads(config.MAIL_CACHE_FILE.read_text(encoding="utf-8"))) == {"old", "new1", "new2"}


def test_fetch_saves_cache_every_100_letters(monkeypatch, files):
    ids = [f"m{i}" for i in range(150)]
    writes = []
    monkeypatch.setattr(mail, "credentials", lambda: "creds")
    monkeypatch.setattr(mail, "Gmail", lambda creds: FakeGmail(creds, ids))
    (files / "state").mkdir()
    real_write = type(config.MAIL_CACHE_FILE).write_text

    def spy(self, text, **kw):
        writes.append(len(json.loads(text)))
        return real_write(self, text, **kw)
    monkeypatch.setattr(type(config.MAIL_CACHE_FILE), "write_text", spy)
    messages, _ = mail.fetch_messages()
    assert len(messages) == 150
    assert writes == [100, 150]  # intermediate saves — an interrupted sync does not start from scratch


def test_fetch_stops_on_skip_button(monkeypatch):
    monkeypatch.setattr(mail, "credentials", lambda: "creds")
    gm = FakeGmail("creds")
    monkeypatch.setattr(mail, "Gmail", lambda creds: gm)
    monkeypatch.setattr(mail.progress, "skip_requested", lambda: True)
    messages, _ = mail.fetch_messages()
    assert messages == [] and gm.read == []
    assert config.MAIL_CACHE_FILE.exists()


# --- manual events ----------------------------------------------------------------------------------

def test_manual_events_dates_and_unknown_kind(caplog):
    config.MANUAL_APPLICATIONS_FILE.write_text("""
[[application]]
company = "Acme"
source = "Telegram"
events = [
  { date = 2026-11-03, kind = "interview", note = "screen" },
  { date = 2026-11-05T15:30:00, kind = "interview" },
  { date = 2026-11-06T10:00:00Z, kind = "maybe", note = "typo" },
]
""", encoding="utf-8")
    events = mail.manual_events()
    assert [c for c, _ in events] == ["Acme"] * 3
    first, second, third = (e for _, e in events)
    assert first.when.hour == 12 and first.when.tzinfo is not None  # a date without time is noon
    assert second.when.tzinfo is not None and first.sender == "Telegram"
    assert third.kind == mail.OTHER and "unknown kind" in caplog.text

    apps, _ = mail.summarize([])
    assert apps[0].company == "Acme" and apps[0].stages == 2


# --- OAuth token -------------------------------------------------------------------------------------

class FakeCreds:
    def __init__(self, valid=True, expired=False, refresh_error=False):
        self.valid, self.expired, self.refresh_token = valid, expired, "rt"
        self.refresh_error = refresh_error

    def refresh(self, request):
        from google.auth.exceptions import RefreshError
        if self.refresh_error:
            raise RefreshError("invalid_grant")
        self.valid = True

    def to_json(self):
        return '{"token": "t"}'


@pytest.fixture
def oauth(files, monkeypatch):
    from google.oauth2.credentials import Credentials
    monkeypatch.setattr(config, "GMAIL_TOKEN_FILE", files / "state" / "gmail_token.json")
    monkeypatch.setattr(config, "GMAIL_CLIENT_FILE", files / "gmail_client.json")
    stored = {}
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda path, scopes: stored["creds"])

    def put(creds):
        config.GMAIL_TOKEN_FILE.parent.mkdir(exist_ok=True)
        config.GMAIL_TOKEN_FILE.write_text("{}", encoding="utf-8")
        stored["creds"] = creds
    return put


def test_valid_token_is_used_as_is(oauth):
    creds = FakeCreds()
    oauth(creds)
    assert mail.credentials() is creds
    assert config.GMAIL_TOKEN_FILE.read_text(encoding="utf-8") == "{}"  # not overwritten


def test_expired_token_is_refreshed_and_saved(oauth):
    creds = FakeCreds(valid=False, expired=True)
    oauth(creds)
    assert mail.credentials() is creds and creds.valid
    assert config.GMAIL_TOKEN_FILE.read_text(encoding="utf-8") == '{"token": "t"}'


def test_revoked_token_without_client_file_explains(oauth, caplog):
    oauth(FakeCreds(valid=False, expired=True, refresh_error=True))
    with pytest.raises(FileNotFoundError, match="OAuth"):
        mail.credentials()
    assert "a new sign-in is needed" in caplog.text


def test_revoked_token_opens_login_with_timeout(oauth, monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow
    oauth(FakeCreds(valid=False, expired=True, refresh_error=True))
    config.GMAIL_CLIENT_FILE.write_text("{}", encoding="utf-8")
    fresh, seen = FakeCreds(), {}

    class Flow:
        def run_local_server(self, **kw):
            seen.update(kw)
            return fresh
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda path, scopes: Flow())
    assert mail.credentials() is fresh
    assert seen["timeout_seconds"] == mail.LOGIN_TIMEOUT_SECONDS  # a scheduled run does not hang forever
