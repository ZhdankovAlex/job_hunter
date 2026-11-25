"""Editing the CV base (cv/base.toml) from the web UI: Summary with achievements, experience with bullets,
education, languages and publications.

Writes through tomlkit: the file is parsed together with comments and formatting, and only the values that
really changed are touched — comments, key order and the other sections stay as they were. Lists
([[summary.achievements]], [[experience]], [[experience.bullets]]) are matched by position: the i-th item of
the form edits the i-th block of the file, extra blocks are removed from the end, new ones are appended.

Keys the form does not show (industries, your own notes) come in `extra` and are written back as they were.
Before writing, the new text is parsed with tomllib and compared with what was meant to be written; the
encoding is checked before writing (write_bytes does not truncate the file until the bytes are ready). If there
is no cv/base.toml yet, the first edit creates it from base.example.toml (same as the Profile, see cv_profile).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, Comment, Table, Whitespace

from . import config, cvgen

ACHIEVEMENT_KEYS = ("text", "pin", "summary_for", "requires", "topics")
BULLET_KEYS = ("text", "pin", "generic", "requires", "topics")
JOB_KEYS = ("title", "company", "dates", "bullets_max", "intro", "tech", "tech_always", "bullets")
SUMMARY_KEYS = ("intro", "closing", "max_achievements", "achievements")


def path() -> Path:
    return cvgen.BASE_FILE


def _plain(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value


def _split(item: dict, keys: tuple[str, ...]) -> tuple[dict, dict]:
    return {k: v for k, v in item.items() if k in keys}, {k: v for k, v in item.items() if k not in keys}


def _achievement_out(a: dict) -> dict[str, Any]:
    known, extra = _split(a, ACHIEVEMENT_KEYS)
    return {"text": known.get("text", ""), "pin": bool(known.get("pin")), "topics": list(known.get("topics", [])),
            "summary_for": list(known.get("summary_for", [])), "requires": list(known.get("requires", [])),
            "extra": extra}


def _bullet_out(b: dict) -> dict[str, Any]:
    known, extra = _split(b, BULLET_KEYS)
    return {"text": known.get("text", ""), "pin": bool(known.get("pin")), "topics": list(known.get("topics", [])),
            "generic": list(known.get("generic", [])), "requires": list(known.get("requires", [])), "extra": extra}


def _job_out(j: dict) -> dict[str, Any]:
    known, extra = _split(j, JOB_KEYS)
    intro = known.get("intro", [])
    return {"title": known.get("title", ""), "company": known.get("company", ""), "dates": known.get("dates", ""),
            "bullets_max": int(known.get("bullets_max", 6)), "intro": [intro] if isinstance(intro, str) else list(intro),
            "tech": list(known.get("tech", [])), "tech_always": list(known.get("tech_always", [])),
            "bullets": [_bullet_out(b) for b in known.get("bullets", [])], "extra": extra}


def read_summary() -> dict[str, Any]:
    """[summary]: intro, closing, max_achievements and the [[summary.achievements]]."""
    summary = tomllib.loads(path().read_text(encoding="utf-8")).get("summary", {})
    return {"intro": summary.get("intro", ""), "closing": summary.get("closing", ""),
            "max_achievements": int(summary.get("max_achievements", 3)),
            "achievements": [_achievement_out(a) for a in summary.get("achievements", [])], "file": path().name}


def read_experience() -> dict[str, Any]:
    """[[experience]] — workplaces with their [[experience.bullets]]."""
    base = tomllib.loads(path().read_text(encoding="utf-8"))
    return {"jobs": [_job_out(j) for j in base.get("experience", [])], "file": path().name}


# --- writing -------------------------------------------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join(text.split())


def _strs(values: list[str]) -> list[str]:
    return [v for v in (_clean(x) for x in values) if v]


def _achievement_in(a: dict) -> dict[str, Any]:
    """An item to write: empty optional keys are not written (as in a base filled in by hand)."""
    out: dict[str, Any] = {"text": _clean(a["text"])}
    if a.get("pin"):
        out["pin"] = True
    for key in ("summary_for", "requires"):
        if values := _strs(a.get(key, [])):
            out[key] = values
    out["topics"] = _strs(a.get("topics", []))
    return {**out, **_extra(a, ACHIEVEMENT_KEYS)}


def _bullet_in(b: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"text": _clean(b["text"])}
    if b.get("pin"):
        out["pin"] = True
    for key in ("generic", "requires"):
        if values := _strs(b.get(key, [])):
            out[key] = values
    out["topics"] = _strs(b.get("topics", []))
    return {**out, **_extra(b, BULLET_KEYS)}


def _job_in(j: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"title": _clean(j["title"]), "company": _clean(j["company"]), "dates": _clean(j["dates"]),
                           "bullets_max": j["bullets_max"], "intro": _strs(j.get("intro", [])),
                           "tech": _strs(j.get("tech", []))}
    if always := _strs(j.get("tech_always", [])):
        out["tech_always"] = always
    return {**out, **_extra(j, JOB_KEYS), "bullets": [_bullet_in(b) for b in j["bullets"]]}


def _extra(item: dict, keys: tuple[str, ...]) -> dict[str, Any]:
    """Keys the form does not show; those that clash with form fields are ignored — the fields win."""
    return {k: v for k, v in item.get("extra", {}).items() if k not in keys}


def _take_trailer(table: Any, comments_only: bool = False) -> list[Any]:
    """Takes blank lines and comments off the end of a table. tomlkit puts there the comment that stands
    before the NEXT section in the file, and a new key would be appended after it — under someone else's header.
    comments_only — only starting from the first comment (a blank spacer line stays in place)."""
    body = table.value.body
    i = len(body)
    while i and body[i - 1][0] is None and isinstance(body[i - 1][1], (Comment, Whitespace)):
        i -= 1
    if comments_only:
        i = next((k for k in range(i, len(body)) if isinstance(body[k][1], Comment)), len(body))
    out = [item for _, item in body[i:]]
    del body[i:]  # a tail without keys: tomlkit's key indices do not shift
    return out


def _give(table: Any, items: list[Any]) -> None:
    table.value.body.extend((None, item) for item in items)


def _leaf(table: Any, nested: str | None) -> Any:
    """The last table in the file text: for a workplace — its last bullet."""
    sub = table.get(nested) if nested else None
    return sub[-1] if isinstance(sub, AoT) and len(sub) else table


def _sync_table(table: Any, new: dict[str, Any], nested: str | None = None) -> None:
    """Changes only what differs in the table; the `nested` key (bullets) is a list of tables, synced separately.
    The nested list is taken off for the edit: tomlkit keeps it as the last key of the table, and a new key
    after it would end up in the text under the last bullet."""
    changes = any(k not in new for k in table) or any(
        k != nested and (k not in table or _plain(table[k]) != v) for k, v in new.items())
    if changes:
        sub = None
        if nested is not None and nested in table:
            sub = table[nested]
            del table[nested]
        trailer = _take_trailer(table)
        for key in [k for k in table if k not in new]:
            del table[key]
        for key, value in new.items():
            if key != nested and (key not in table or _plain(table[key]) != value):
                table[key] = value
        _give(table, trailer)
        if sub is not None:
            table[nested] = sub
    if nested is not None:
        _sync_aot(table, nested, new[nested])


def _new_table(item: dict[str, Any], nested: str | None = None) -> Table:
    table = tomlkit.table()
    for key, value in item.items():
        if key != nested:
            table[key] = value
    table.add(tomlkit.nl())  # a blank line before the next header
    if nested is not None:
        aot = tomlkit.aot()
        for sub in item[nested]:
            aot.append(_new_table(sub))
        table[nested] = aot
    return table


def _sync_aot(parent: Any, key: str, items: list[dict[str, Any]], nested: str | None = None) -> None:
    """List of tables parent[key] -> items: by position, extra ones off the end, new ones appended. The comment
    before the next section of the file (hanging on the last item) moves to the new last item."""
    current = parent.get(key)
    if not isinstance(current, AoT):  # no key, or written as an inline array — build anew
        aot = tomlkit.aot()
        for item in items:
            aot.append(_new_table(item, nested))
        parent[key] = aot
        return
    moved = len(current) != len(items) and len(current) > 0
    comments = _take_trailer(_leaf(current[-1], nested), comments_only=True) if moved else []
    for i, item in enumerate(items[:len(current)]):
        _sync_table(current[i], item, nested)
    while len(current) > len(items):
        del current[len(current) - 1]
    for item in items[len(current):]:
        current.append(_new_table(item, nested))
    if comments and len(current):
        _give(_leaf(current[-1], nested), comments)


def _load() -> tuple[Any, str, Path]:
    src = path()
    raw = src.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return tomlkit.parse(raw.decode("utf-8").replace("\r\n", "\n")), newline, src


def write_text(src: Path, text: str, newline: str) -> Path:
    """Writes the new base text (already checked with tomllib) with the original line endings. If
    base.example.toml was read, cv/base.toml is written next to it, and from then on the generator, the market
    check and the skills read it."""
    try:
        data = text.replace("\n", newline).encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError("the text has broken characters (lone UTF-16 surrogates)") from e
    dst = src.with_name("base.toml") if src.name == "base.example.toml" else src
    dst.write_bytes(data)
    if dst != src:
        cvgen.BASE_FILE = config.CV_BASE_FILE = dst
    return dst


def _dump(doc: Any) -> str:
    text = tomlkit.dumps(doc)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # should not happen — but then the file is left alone
        raise ValueError(f"the base does not parse after the edit ({e}) — not written") from e
    return text


def save_summary(intro: str, closing: str, max_achievements: int, achievements: list[dict]) -> dict[str, Any]:
    """Writes [summary] and [[summary.achievements]]; returns what is read back.

    Raises:
        ValueError: the file reads differently after the edit, broken characters.
    """
    items = [_achievement_in(a) for a in achievements]
    doc, newline, src = _load()
    if "summary" not in doc:
        doc["summary"] = tomlkit.table()
    summary = doc["summary"]
    for key, value in (("intro", _clean(intro)), ("closing", _clean(closing)), ("max_achievements", max_achievements)):
        if key not in summary or _plain(summary[key]) != value:
            summary[key] = value
    _sync_aot(summary, "achievements", items)
    text = _dump(doc)
    got = tomllib.loads(text).get("summary", {})
    want = {"intro": _clean(intro), "closing": _clean(closing), "max_achievements": max_achievements,
            "achievements": items}
    if {k: got.get(k) for k in SUMMARY_KEYS} != want:
        raise ValueError("the base reads differently than intended after the edit — not written")
    write_text(src, text, newline)
    return read_summary()


def save_experience(jobs: list[dict]) -> dict[str, Any]:
    """Writes [[experience]] with bullets; returns what is read back.

    Raises:
        ValueError: the file reads differently after the edit, broken characters.
    """
    items = [_job_in(j) for j in jobs]
    doc, newline, src = _load()
    _sync_aot(doc, "experience", items, nested="bullets")
    text = _dump(doc)
    if tomllib.loads(text).get("experience") != items:
        raise ValueError("the base reads differently than intended after the edit — not written")
    write_text(src, text, newline)
    return read_experience()


def suggest_topics(text: str) -> list[str]:
    """Topics the generator would find in this text (the same regexes as for a vacancy text) — a hint in the
    editor: a bullet's topic adds a point when the vacancy has the same topic."""
    return sorted(name for name, rx in cvgen._TOPIC_RX.items() if rx.search(text))


# --- education, languages, publications ----------------------------------------------------------------------

EDUCATION_KEYS = ("degree", "years", "school", "thesis_ai")


def read_other() -> dict[str, Any]:
    """[education], [languages], [publications] — at the bottom of every CV."""
    base = tomllib.loads(path().read_text(encoding="utf-8"))
    edu = base.get("education", {})
    return {"education": {k: edu.get(k, "") for k in EDUCATION_KEYS},
            "languages": base.get("languages", {}).get("text", ""),
            "publications": base.get("publications", {}).get("text", ""), "file": path().name}


def _set_keys(doc: Any, name: str, values: dict[str, str | None]) -> None:
    """Table doc[name]: a key with a value — write it, None — remove it; other keys are left alone.
    The tail (blank lines and the comment for the next section) is taken off and put back, so a new key
    does not land under someone else's header."""
    if name not in doc:
        doc[name] = tomlkit.table()
    table = doc[name]
    if all((k not in table) if v is None else (k in table and _plain(table[k]) == v) for k, v in values.items()):
        return
    trailer = _take_trailer(table)
    for key, value in values.items():
        if value is None:
            if key in table:
                del table[key]
        elif key not in table or _plain(table[key]) != value:
            table[key] = value
    _give(table, trailer)


def save_other(education: dict[str, str], languages: str, publications: str) -> dict[str, Any]:
    """Writes education, languages and publications; an empty thesis removes the thesis_ai key.

    Raises:
        ValueError: the file reads differently after the edit, broken characters.
    """
    edu = {k: _clean(education.get(k, "")) for k in EDUCATION_KEYS}
    doc, newline, src = _load()
    _set_keys(doc, "education", {**edu, "thesis_ai": edu["thesis_ai"] or None})
    _set_keys(doc, "languages", {"text": _clean(languages)})
    _set_keys(doc, "publications", {"text": _clean(publications)})
    text = _dump(doc)
    got = tomllib.loads(text)
    want_edu = {k: v for k, v in edu.items() if v or k != "thesis_ai"}
    if ({k: got["education"].get(k) for k in want_edu} != want_edu or ("thesis_ai" in got["education"]) != bool(edu["thesis_ai"])
            or got["languages"]["text"] != _clean(languages) or got["publications"]["text"] != _clean(publications)):
        raise ValueError("the base reads differently than intended after the edit — not written")
    write_text(src, text, newline)
    return read_other()
