"""The profile for the CV header: name, contacts and links from cv/base.toml — reading and editing from the web UI.

In base.toml these are three top-level keys: name, contacts (one line "email · phone · location" — that is how
it goes under the name in the CV) and links ([label, url]). An edit replaces only the lines of these keys: the
rest of the file, comments and order are left alone. The new text is checked with tomllib and the encoding
before writing — as in manual_apps.

While there is no cv/base.toml of your own (base.example.toml is read), the first edit creates cv/base.toml from
the example: the example in git does not change, and the CV generator reads the new file right away.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import cv_base, cvgen

SEP = " · "
KEYS = ("name", "contacts", "links")
# The patterns also go into the API schema (web/models.py): \s, \S and \d cannot be used there — JSON Schema
# (ECMA), pydantic (Rust) and Python have different sets (\x85 is whitespace for Python and Rust but not for ECMA;
# schemathesis caught exactly that). WS is exactly what str.split() splits on: after _clean none is left in a value.
WS = r" \t\n\r\f\v\x1c-\x1f\x85\xa0  -     　"
EMAIL_PATTERN = rf"[^@{WS}]+@[^@{WS}]+"
PHONE_PATTERN = rf"\+?[{WS}().-]*([0-9][{WS}().-]*){{6,}}"
URL_PATTERN = rf"https?://[^{WS}]+"
NAME_PATTERN = rf"[^{WS}]"  # at least one non-space
EMAIL_RX, PHONE_RX, URL_RX = (re.compile(f"^{p}$") for p in (EMAIL_PATTERN, PHONE_PATTERN, URL_PATTERN))
KEY_RX = re.compile(r"^([A-Za-z0-9_-]+)\s*=")


def _toml_str(value: str) -> str:
    """A TOML string via JSON escaping; TOML forbids DEL, JSON leaves it as is (see manual_apps)."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _kind(part: str) -> str:
    if EMAIL_RX.match(part):
        return "email"
    if PHONE_RX.match(part):
        return "phone"
    return "location"


def split_contacts(contacts: str) -> dict[str, str]:
    """"email · phone · location" -> fields; anything that is not an email or a phone is location (joined by " · ")."""
    out: dict[str, list[str]] = {"email": [], "phone": [], "location": []}
    for part in (p.strip() for p in contacts.split("·")):
        if part:
            out[_kind(part)].append(part)
    return {k: SEP.join(v) for k, v in out.items()}


def join_contacts(old: str, email: str, phone: str, location: str) -> str:
    """Fields -> the contacts line in the previous order of parts; new parts go to the end (email, phone, location)."""
    values = {"email": email, "phone": phone, "location": location}
    order = [_kind(p.strip()) for p in old.split("·") if p.strip()]
    kinds = list(dict.fromkeys([*order, "email", "phone", "location"]))
    return SEP.join(values[k] for k in kinds if values[k])


def path() -> Path:
    return cvgen.BASE_FILE


def read() -> dict[str, Any]:
    """The profile from the CV base: name, email, phone, location, links ([{label, url}]) and the file it was read from."""
    base = tomllib.loads(path().read_text(encoding="utf-8"))
    return {"name": base.get("name", ""), **split_contacts(base.get("contacts", "")),
            "links": [{"label": label, "url": url} for label, url in base.get("links", [])],
            "file": path().name}


def _spans(lines: list[str]) -> tuple[dict[str, tuple[int, int]], int]:
    """Lines of the top-level keys: {key: (start, end)} and the line number of the first table ([headline]…).

    The end of a value is the first line at which the fragment parses with tomllib: that way the multi-line
    links array and triple-quoted strings need no hand-written parsing.
    """
    spans: dict[str, tuple[int, int]] = {}
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            i += 1
            continue
        m = KEY_RX.match(s)
        if not m:
            break  # [table] or [[array of tables]] — the keys below are not top-level any more
        for j in range(i + 1, len(lines) + 1):
            try:
                tomllib.loads("".join(lines[i:j]))
                break
            except tomllib.TOMLDecodeError:
                continue
        else:
            raise ValueError(f"cannot parse the value of key {m.group(1)} in {path().name}")
        spans[m.group(1)] = (i, j)
        i = j
    return spans, i


def _clean(value: str) -> str:
    """One line without line breaks: contacts and name go into the Markdown CV header line by line."""
    return " ".join(value.split())


def save(name: str, email: str, phone: str, location: str, links: list[dict[str, str]]) -> dict[str, Any]:
    """Writes the name, contacts and links to the CV base and returns the profile read back.

    Raises:
        ValueError: empty name, not an email, not a phone, a link that is not http(s), broken characters.
    """
    name, email, phone, location = (_clean(v) for v in (name, email, phone, location))
    if not name:
        raise ValueError("A name is required")
    if email and not EMAIL_RX.match(email):
        raise ValueError(f"Does not look like an email: {email}")
    if phone and _kind(phone) != "phone":
        raise ValueError(f"Does not look like a phone: {phone} — digits, spaces, + ( ) -")
    pairs = []
    for link in links:
        label, url = _clean(link.get("label", "")), link.get("url", "").strip()
        if not label and not url:
            continue
        if not URL_RX.match(url):
            raise ValueError(f"A link must start with http:// or https:// and have no spaces: {url or '(empty)'}")
        pairs.append((label or re.sub(r"^https?://(www\.)?", "", url).rstrip("/"), url))

    src = path()
    raw = src.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"  # a base.toml with CRLF stays CRLF
    text = raw.decode("utf-8").replace("\r\n", "\n")
    old = tomllib.loads(text)
    contacts = join_contacts(old.get("contacts", ""), email, phone, location)
    values = {
        "name": f"name = {_toml_str(name)}\n",
        "contacts": f"contacts = {_toml_str(contacts)}\n",
        "links": "links = [\n" + "".join(f"  [{_toml_str(lb)}, {_toml_str(u)}],\n" for lb, u in pairs) + "]\n"
                 if pairs else "links = []\n",
    }
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    spans, head_end = _spans(lines)
    # from the end — the line numbers above do not shift; a missing key is inserted before the first table
    inserts = sorted(((spans.get(k, (head_end, head_end)), values[k]) for k in reversed(KEYS)),
                     key=lambda x: x[0], reverse=True)
    for (start, end), new in inserts:
        lines[start:end] = [new]
    new_text = "".join(lines)

    # everything is checked BEFORE writing: write_text truncates the file first
    parsed = tomllib.loads(new_text)
    if (parsed.get("name"), parsed.get("contacts"), [tuple(p) for p in parsed.get("links", [])]) != (name, contacts, pairs):
        raise ValueError("the file reads differently than intended after the edit — not written")
    cv_base.write_text(src, new_text, newline)
    return read()
