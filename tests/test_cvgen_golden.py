"""Golden output of cvgen.build_markdown on cv/base.example.toml: a refactoring must not change a single line.

Goldens are tests/fixtures/cv_golden/<case>.md (and .picks.json for the picked bullets). If the output changed
on purpose, rebuild them: JOBHUNTER_UPDATE_GOLDEN=1 pytest tests/test_cvgen_golden.py, and review the diff.
"""
import json
import os
from pathlib import Path

import pytest

from jobhunter import cvgen

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "cv_golden"
UPDATE = bool(os.environ.get("JOBHUNTER_UPDATE_GOLDEN"))

BACKEND_TEXT = ("We build payment services. Requirements: Python, FastAPI, PostgreSQL, Redis, Kafka, Docker, "
                "Kubernetes, AWS. Event-driven architecture, high load, microservices.")
AI_TEXT = ("Build LLM-powered agents: RAG, retrieval, embeddings, vector search, OpenAI API, LangChain. "
           "Python, FastAPI, PostgreSQL.")
SCRAPING_TEXT = "Web scraping and crawling platform: parsers, proxies, Python, asyncio, PostgreSQL."

CASES: dict[str, dict] = {
    "generic_backend": {"generic": "backend"},
    "generic_ai": {"generic": "ai"},
    "generic_backend_compact": {"generic": "backend", "compact": True},
    "vacancy_backend": {"company": "Acme", "title": "Senior Backend Engineer", "text": BACKEND_TEXT,
                        "url": "https://example.com/jobs/1", "fit": 82, "missing": ["Go", "Kafka"]},
    "vacancy_ai": {"company": "Globex", "title": "AI Engineer", "text": AI_TEXT},
    "vacancy_scraping_compact": {"company": "Acme", "title": "Python Developer", "text": SCRAPING_TEXT,
                                 "fit": 0, "compact": True},
}


def _build(case: dict) -> tuple[str, list]:
    base = cvgen.load_base()
    picks: list = []
    limits = cvgen.Limits(compact=True) if case.get("compact") else None
    md = cvgen.build_markdown(base, case.get("company", ""), case.get("title", ""), case.get("text", ""),
                              url=case.get("url", ""), fit=case.get("fit", -1), missing=case.get("missing"),
                              generic=case.get("generic"), limits=limits, picks=picks)
    return md, picks


@pytest.mark.parametrize("name", CASES)
def test_build_markdown_golden(name):
    md, picks = _build(CASES[name])
    md_file = GOLDEN_DIR / f"{name}.md"
    picks_file = GOLDEN_DIR / f"{name}.picks.json"
    picks_text = json.dumps(picks, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if UPDATE:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        md_file.write_text(md, encoding="utf-8", newline="\n")
        picks_file.write_text(picks_text, encoding="utf-8", newline="\n")
    assert md == md_file.read_text(encoding="utf-8")
    assert picks_text == picks_file.read_text(encoding="utf-8")
