"""A cover letter for a vacancy — from the facts in cv/base.toml, invents nothing.

Like cvgen: achievements from [summary] and soft skills from [cover] are ranked against the vacancy text
(technologies, topics, text similarity), and the letter follows a template:
who you are as an engineer → how you can help (hard) → how you work (soft) → honestly, what you lack → "let's talk".

Styles:
- short — bulleted: a one-line intro, a "what I bring" list, a call to talk;
- full — in paragraphs: intro, achievements (as a list — CV bullets are written without "I"), stack and soft skills
  as a paragraph, what your experience lacks.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import cv_match, cvgen, settings

STYLES = ("short", "full")

# if base.toml has no [cover] — neutral wording (no facts that are not in the base)
DEFAULT_COVER = {
    "greeting": "Hi {company} team,",
    "opening": "I'm interested in the {title} role at {company}: it matches what I've been doing.",
    "lead_short": "What I can bring:",
    "lead_full": "Most relevant to your role:",
    "soft": [],
    "missing": "I haven't worked with {missing} in production yet and would be happy to ramp up on it.",
    "closing": "I'd be glad to have a call and discuss the role in more detail.",
    "sign": "Best regards,",
}


def _name(base: dict) -> str:
    return " ".join(w.capitalize() for w in base["name"].split())


def _ranked(items: list[dict], vac: cvgen.Vacancy) -> list[dict]:
    return sorted(items, key=lambda i: -vac.score(i["text"], i.get("topics", [])))


def build(company: str, title: str, text: str, style: str = "short") -> dict:
    """A letter for a vacancy: {text, style, fit, have, missing, picks}.

    Args:
        company, title, text: the vacancy (the fuller the text, the better the selection).
        style: short — bulleted, full — in paragraphs.
    """
    if style not in STYLES:
        raise ValueError(f"style: {style!r}, expected one of {STYLES}")
    base = cvgen.load_base()
    cover = {**DEFAULT_COVER, **base.get("cover", {})}
    vac = cvgen.Vacancy(title, text, base)
    holder = type("Job", (), {"title": title, "company": company, "mismatch_lang": ""})()
    cv_match.CvProfile(settings.get().cv.model_dump()).analyze(holder, text)
    fmt = {"company": company, "title": title}

    achievements = cvgen._pick([a for a in base["summary"]["achievements"] if cvgen._for_variant(a, None)],
                               vac, 3, 3 if style == "short" else 4)
    soft_items = [s if isinstance(s, dict) else {"text": s} for s in cover["soft"]]
    soft = [s for s in _ranked([s for s in soft_items if cvgen._allowed(s, vac)], vac)][:2 if style == "short" else 3]
    have = list(holder.cv_core_have)
    missing = list(holder.cv_core_missing)[:2]

    lines = [cover["greeting"].format(**fmt), ""]
    intro = cover.get("intro") or base["summary"]["intro"]
    if style == "short":
        lines += [f"{intro} {cover['opening'].format(**fmt)}", "", cover["lead_short"].format(**fmt)]
        lines += [f"- {a['text']}" for a in achievements]
        if have:
            lines.append(f"- Stack overlap with the role: {', '.join(have[:8])}.")
        lines += [f"- {s['text']}" for s in soft]
    else:
        lines += [f"{intro} {cover['opening'].format(**fmt)}", ""]
        # CV bullets are written without "I" — they read awkwardly in running text, so a list here too
        lines += [cover["lead_full"].format(**fmt), *(f"- {a['text']}" for a in achievements), ""]
        stack = f"My experience covers {', '.join(have[:8])} from your stack." if have else ""
        paragraph = " ".join(p for p in [stack, *(s["text"] for s in soft)] if p)
        if paragraph:
            lines += [paragraph, ""]
        if missing:
            text_missing = cover["missing"].format(missing=" and ".join(missing), **fmt)
            if len(missing) > 1:
                text_missing = text_missing.replace(" on it", " on them").replace(" with it", " with them")
            lines += [text_missing, ""]
        if lines[-1] == "":
            lines.pop()
    lines += ["", cover["closing"].format(**fmt), "", cover["sign"].format(**fmt), _name(base)]

    picks = [{"section": "Achievement", "text": a["text"], **vac.explain(a["text"], a.get("topics", []))}
             for a in achievements]
    picks += [{"section": "Soft skills", "text": s["text"], **vac.explain(s["text"], s.get("topics", []))}
              for s in soft]
    return {"company": company, "title": title, "style": style, "text": "\n".join(lines),
            "fit": holder.cv_fit, "have": have, "missing": list(holder.cv_core_missing), "picks": picks}


def save(result: dict, out_dir: Path | None = None) -> Path:
    """The letter goes to cv_out/<date>/<company>_<role>_cover_<style>.txt — next to the CV for the same vacancy."""
    out_dir = out_dir or cvgen.OUT_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{cvgen.slug(result['company'])}_{cvgen.slug(result['title'])}_cover_{result['style']}.txt"
    path.write_text(result["text"] + "\n", encoding="utf-8")
    return path
