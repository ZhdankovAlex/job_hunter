import re

from . import config
from .models import Company, norm_name

_GENERIC_SUFFIXES = {"labs", "ai", "com", "io", "org", "group", "technologies", "tech", "inc", "hq", "app"}


def _csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def load_companies(path=config.COMPANIES_FILE) -> list[Company]:
    """Company | priority | note | industries | brands — the fields after the name are optional."""
    companies: dict[str, Company] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        score = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        note = parts[2] if len(parts) > 2 else ""
        industries = _csv(parts[3]) if len(parts) > 3 else []
        brands = _csv(parts[4]) if len(parts) > 4 else []
        key = norm_name(name)
        # duplicates (a company in two groups of the list) — keep the higher priority, merge industries and brands
        old = companies.get(key)
        if old is None or score > old.score:
            companies[key] = Company(name, score, note, industries=industries, brands=brands)
        if old is not None:
            new = companies[key]
            new.industries = list(dict.fromkeys([*old.industries, *new.industries, *industries]))
            new.brands = list(dict.fromkeys([*old.brands, *new.brands, *brands]))
    return list(companies.values())


_CYRILLIC_LOOKALIKES = str.maketrans("аеорсухкмтвнАЕОРСУХКМТВН", "aeopcyxkmtbhAEOPCYXKMTBH")


def slug_candidates(name):
    """Heuristic slugs: 'Grafana Labs' -> grafanalabs, grafana-labs, grafana."""
    out = []
    base = re.sub(r"\(.*?\)", "", name)
    if re.search(r"[A-Za-z]", base):  # "Acme.аi" with a Cyrillic "а": Cyrillic look-alikes of Latin letters
        base = base.translate(_CYRILLIC_LOOKALIKES)
    for variant in base.split("/"):
        tokens = re.findall(r"[a-z0-9]+", variant.lower())
        if not tokens:
            continue
        out.append("".join(tokens))
        if len(tokens) > 1:
            out.append("-".join(tokens))
            core = list(tokens)
            while len(core) > 1 and core[-1] in _GENERIC_SUFFIXES:
                core.pop()
            if core != tokens and len("".join(core)) >= 3:
                out.append("".join(core))
    return list(dict.fromkeys(out))[:5]
