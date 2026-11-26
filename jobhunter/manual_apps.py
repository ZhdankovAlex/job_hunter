"""New application events from the web UI -> applications_manual.toml.

The same file you keep by hand: the event is appended at the end as a separate [[application]] block
(several blocks of one company are merged on reading — mail.manual_events). Existing
lines and comments are not touched. The new text is checked with tomllib and encoded before writing: if
something is wrong, the file is left alone.
"""
from __future__ import annotations

import json
import tomllib
from datetime import datetime

from . import config
from .fsutil import atomic_write_text

KINDS = {"applied": "Applied", "interview": "Stage / interview", "rejected": "Rejected", "declined": "Declined",
         "offer": "Offer", "other": "Note"}


def _toml_str(value: str) -> str:
    """A TOML string: JSON escaping fits (\\", \\\\, \\n, \\uXXXX) — except DEL: JSON leaves \\x7f as is,
    TOML forbids it, and an event with that character was not written (found by schemathesis)."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def add_event(company: str, kind: str, note: str = "", url: str = "", source: str = "",
              when: datetime | None = None) -> dict:
    """Appends an application event.

    Args:
        company: company (as in the report — the event is merged with letters by it).
        kind: applied / interview / rejected / declined / offer / other.
        note: what happened, in your own words.
        url: link to the vacancy — reports will mark it "🔁 already applied".
        source: where the application went (company site, LinkedIn, Telegram…).
        when: event time; now by default.

    Returns:
        The written event.

    Raises:
        ValueError: unknown kind or empty company.
    """
    company = company.strip()
    if not company:
        raise ValueError("A company is required")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    when = (when or datetime.now()).replace(microsecond=0, tzinfo=None)
    fields = [f"date = {when.isoformat()}", f"kind = {_toml_str(kind)}", f"note = {_toml_str(note.strip())}"]
    if url.strip():
        fields.append(f"url = {_toml_str(url.strip())}")
    block = (f"\n[[application]]\ncompany = {_toml_str(company)}\n"
             f"source = {_toml_str(source.strip() or 'web UI')}\n"
             f"events = [\n  {{ {', '.join(fields)} }},\n]\n")
    path = config.MANUAL_APPLICATIONS_FILE
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    text = before.rstrip("\n") + "\n" + block
    # everything is checked BEFORE writing: write_text truncates the file first and encodes after — a lone
    # surrogate (\ud800 from JSON) failed on encoding after the truncation and wiped every application
    # (found by schemathesis)
    tomllib.loads(text)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError("the text has broken characters (lone UTF-16 surrogates)") from e
    atomic_write_text(path, text)
    return {"company": company, "kind": kind, "note": note.strip(), "url": url.strip(), "date": when.isoformat()}


def companies() -> list[str]:
    """Companies from applications_manual.toml and the mail cache — hints in the form (the web UI caches them)."""
    from . import mail

    names = set()
    if config.MANUAL_APPLICATIONS_FILE.exists():
        data = tomllib.loads(config.MANUAL_APPLICATIONS_FILE.read_text(encoding="utf-8"))
        names |= {e["company"] for e in data.get("application", [])}
    try:
        apps, _ = mail.summarize(mail.cached_messages())
        names |= {a.company for a in apps}
    except Exception:  # no mail cache — hints come only from the file
        pass
    return sorted(names, key=str.lower)
