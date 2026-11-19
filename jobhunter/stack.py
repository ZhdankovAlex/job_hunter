"""Main language of a vacancy's stack: Python or another one (Go, Java, C++...) — by title and text."""
import re

from . import config as c


def _python_is_secondary(desc: str, match: re.Match[str]) -> bool:
    """Is Python mentioned in a nice-to-have context?"""
    line_start = desc.rfind("\n", 0, match.start()) + 1
    line_end = desc.find("\n", match.end())
    line = desc[line_start: line_end if line_end != -1 else len(desc)]
    if c.NICE_TO_HAVE_RX.search(line):
        return True
    before = desc[max(0, match.start() - 400): match.start()]
    nice = list(c.NICE_TO_HAVE_RX.finditer(before))
    if not nice:
        return False
    return not c.REQUIRED_RX.search(before, nice[-1].end())


_REQ_START_RX = re.compile(
    r"^\W*(?:requirements|qualifications|what we look for|what you(?:'ll)? (?:need|bring)|you (?:have|bring)|"
    r"must[- ]haves?|about you|who you are|требования|мы ждём|что мы ждём|ожидаем)\b.*$",
    re.I | re.M,
)
_REQ_END_RX = re.compile(
    r"^\W*(?:nice[- ]to[- ]have|bonus|plus|preferred|why |benefits|we offer|what we offer|perks|"
    r"будет плюсом|плюсом будет|условия|мы предлагаем|что мы предлагаем)\b.*$",
    re.I | re.M,
)


_PLUS_LINE_RX = re.compile(
    r"плюсом|будет плюс|преимуществ|nice[- ]to[- ]have|\bplus\b|\bbonus\b|preferred|desirable|advantage",
    re.I,
)


def _required_lang(desc: str) -> str:
    """Another language, if the requirements block names it and does not name Python, otherwise ''.

    Example: the tasks say "code in C++ and Python", but "What We Look For" names only C++.
    """
    start = _REQ_START_RX.search(desc)
    if not start:
        return ""
    rest = desc[start.end():]
    end = _REQ_END_RX.search(rest)
    section = rest[: end.start() if end else 2000]
    if c.PYTHON_RX.search(section):
        return ""
    # "Node.js or Rust is a plus" inside the requirements — not a required language
    section = "\n".join(line for line in section.splitlines() if not _PLUS_LINE_RX.search(line))
    if not section.strip():
        return ""
    # C++ is checked only here: ML vacancies often list it next to Python as a plus
    langs = {**c.OTHER_LANGS, "C++": c.TITLE_LANGS["C++"]}
    found = [name for name, rx in langs.items() if rx.search(section)]
    return found[0] if len(found) == 1 else ""


def language_mismatch_text(title: str, desc: str) -> str:
    """The same by title and text (for vacancies without a description field — Telegram)."""
    if c.PYTHON_RX.search(title):
        return ""
    for name, rx in c.TITLE_LANGS.items():
        if rx.search(title):
            return name
    required = _required_lang(desc)
    if required:
        return required
    counts = {name: len(rx.findall(desc)) for name, rx in c.OTHER_LANGS.items()}
    top = max(counts, key=counts.__getitem__)
    n = counts[top]
    if n == 0:
        return ""
    py = list(c.PYTHON_RX.finditer(desc))
    if not py:
        return top if n >= 2 else ""
    # "Python, Go, or Java" — Python is listed on a par with another language, this is not a "foreign" stack
    for m in py:
        start = max(desc.rfind(ch, 0, m.start()) for ch in "\n.;•") + 1
        ends = [i for i in (desc.find(ch, m.end()) for ch in "\n.;•") if i != -1]
        sentence = desc[start: min(ends) if ends else len(desc)]
        if c.OTHER_LANGS[top].search(sentence):
            return ""
    if n >= 3 and n >= 3 * len(py):
        return top
    if n >= len(py) and all(_python_is_secondary(desc, m) for m in py):
        return top
    return ""
