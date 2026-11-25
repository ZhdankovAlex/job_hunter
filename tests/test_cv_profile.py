"""The Profile page: name, contacts and links in cv/base.toml — an edit does not touch the rest of the base."""
import tomllib

import pytest
from fastapi.testclient import TestClient

from jobhunter import config, cv_profile, cvgen

EXAMPLE = config.ROOT / "cv" / "base.example.toml"
AFTER_HEADER = "# Technologies always shown"  # the first comment after links in the example


# line endings are set explicitly: git keeps the example as is, a Windows checkout gives CRLF, Linux (CI) — LF;
# writing must keep both
@pytest.fixture(params=[b"\r\n", b"\n"], ids=["crlf", "lf"])
def base(request, tmp_path, monkeypatch):
    path = tmp_path / "cv" / "base.toml"
    path.parent.mkdir()
    path.write_bytes(EXAMPLE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", request.param))
    monkeypatch.setattr(cvgen, "BASE_FILE", path)
    monkeypatch.setattr(config, "CV_BASE_FILE", path)
    return path


def _same_newlines(before: bytes, after: bytes) -> bool:
    """Line endings after writing are the same as before: the whole file CRLF or the whole file LF."""
    crlf = b"\r\n" in before
    return after.count(b"\n") == after.count(b"\r\n") if crlf else b"\r" not in after


def test_read_splits_contacts(base):
    p = cv_profile.read()
    assert p["name"] == "JANE DOE"
    assert p["email"] == "jane.doe@example.com"
    assert p["phone"] == "+000 00 000 0000"
    assert p["location"] == "Central European Time (CET/CEST)"
    assert p["links"] == [{"label": "github.com/example", "url": "https://github.com/example"}]


def test_save_replaces_only_header_keys(base):
    before = base.read_bytes()
    cv_profile.save("JOHN ROE", "john@example.com", "+111 22 333 4444", "Springfield · CET",
                    [{"label": "github.com/roe", "url": "https://github.com/roe"},
                     {"label": "", "url": "https://dev.to/roe/"}, {"label": "", "url": ""}])
    after = base.read_bytes()
    assert _same_newlines(before, after)
    new, old = tomllib.loads(after.decode()), tomllib.loads(before.decode())
    assert new["name"] == "JOHN ROE"
    assert new["contacts"] == "john@example.com · +111 22 333 4444 · Springfield · CET"
    assert new["links"] == [["github.com/roe", "https://github.com/roe"], ["dev.to/roe", "https://dev.to/roe/"]]
    assert {k: v for k, v in new.items() if k not in cv_profile.KEYS} == \
        {k: v for k, v in old.items() if k not in cv_profile.KEYS}
    # comments and everything after the header — byte for byte
    tail = before[before.index(AFTER_HEADER.encode()):]
    assert after.endswith(tail)
    assert after.startswith(before[:before.index(b"name = ")])


def test_save_keeps_contacts_order_and_drops_empty(base):
    base.write_text(base.read_text(encoding="utf-8").replace(
        'contacts = "jane.doe@example.com · +000 00 000 0000 · Central European Time (CET/CEST)"',
        'contacts = "Shelbyville · +000 00 000 0000 · jane.doe@example.com"'), encoding="utf-8")
    p = cv_profile.save("JANE DOE", "new@example.com", "", "Springfield", [])
    assert tomllib.loads(base.read_text(encoding="utf-8"))["contacts"] == "Springfield · new@example.com"
    assert p["phone"] == "" and p["links"] == []


def test_save_adds_missing_keys_before_first_table(base):
    text = base.read_text(encoding="utf-8")
    start, end = text.index("name = "), text.index(AFTER_HEADER)
    base.write_text(text[:start] + text[end:], encoding="utf-8")
    cv_profile.save("JANE DOE", "a@example.com", "", "", [{"label": "x", "url": "https://x.example"}])
    new = tomllib.loads(base.read_text(encoding="utf-8"))
    assert (new["name"], new["contacts"], new["links"]) == ("JANE DOE", "a@example.com", [["x", "https://x.example"]])
    assert new["headline"]["backend"]


@pytest.mark.parametrize("kwargs, error", [
    ({"name": "  "}, "name"),
    ({"email": "not-an-email"}, "email"),
    ({"phone": "call me"}, "phone"),
    ({"links": [{"label": "x", "url": "ftp://x"}]}, "http"),
    ({"location": "\x7f\ud800"}, "broken"),
])
def test_save_rejects_bad_input_and_keeps_file(base, kwargs, error):
    before = base.read_bytes()
    args = {"name": "JANE", "email": "", "phone": "", "location": "", "links": [], **kwargs}
    with pytest.raises(ValueError, match=error):
        cv_profile.save(**args)
    assert base.read_bytes() == before


def test_first_save_creates_base_from_example(tmp_path, monkeypatch):
    example = tmp_path / "cv" / "base.example.toml"
    example.parent.mkdir()
    example.write_bytes(EXAMPLE.read_bytes())
    monkeypatch.setattr(cvgen, "BASE_FILE", example)
    monkeypatch.setattr(config, "CV_BASE_FILE", example)
    p = cv_profile.save("JOHN ROE", "", "", "", [])
    assert p["file"] == "base.toml" and example.read_bytes() == EXAMPLE.read_bytes()
    assert cvgen.BASE_FILE == config.CV_BASE_FILE == example.with_name("base.toml")
    assert cvgen.load_base()["name"] == "JOHN ROE"


def test_profile_page_and_api_reach_cv(base, monkeypatch):
    import web.app as web
    http = TestClient(web.app)
    assert 'href="/profile"' in http.get("/pipeline").text  # an item of the left panel
    page = http.get("/profile")
    assert page.status_code == 200 and "jane.doe@example.com" in page.text
    r = http.put("/api/profile", json={"name": "JOHN ROE", "email": "john@example.com", "phone": "",
                                       "location": "Springfield", "links": [{"label": "gh", "url": "https://github.com/roe"}]})
    assert r.status_code == 200 and r.json()["location"] == "Springfield"
    md = cvgen.build_markdown(cvgen.load_base(), "Acme", "Python Developer", "Python and PostgreSQL")
    assert "# JOHN ROE" in md and "john@example.com · Springfield" in md and "[gh](https://github.com/roe)" in md
    bad = http.put("/api/profile", json={"name": "X", "email": "nope"})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"] == ["body", "email"]  # the pattern is in the API schema
