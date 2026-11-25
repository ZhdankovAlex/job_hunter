"""Editing the Summary and experience in cv/base.toml from the web (tomlkit): only what changed changes, comments stay."""
import copy
import tomllib

import pytest
from fastapi.testclient import TestClient

from jobhunter import config, cv_base, cvgen

EXAMPLE = config.ROOT / "cv" / "base.example.toml"
PROJECTS_COMMENT = "# Pet projects: the Projects section"  # in the example it stands before [[projects]] — hangs on the last experience bullet
STORIES_COMMENT = "# --- Story bank"  # before [[stories]], after [languages]
THESIS = "Thesis: text classification with neural networks"  # thesis_ai in the example


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


def _save_all(summary, jobs):
    cv_base.save_summary(summary["intro"], summary["closing"], summary["max_achievements"], summary["achievements"])
    cv_base.save_experience(jobs)


def _bullet(text, **kw):
    return {"text": text, "pin": False, "topics": [], "generic": [], "requires": [], "extra": {}, **kw}


def test_save_without_changes_keeps_file_byte_for_byte(base):
    before = base.read_bytes()
    _save_all(cv_base.read_summary(), cv_base.read_experience()["jobs"])
    assert base.read_bytes() == before


def test_add_and_revert_returns_original_file(base):
    before = base.read_bytes()
    summary, jobs = cv_base.read_summary(), cv_base.read_experience()["jobs"]
    edited_summary, edited_jobs = copy.deepcopy(summary), copy.deepcopy(jobs)
    edited_summary["achievements"][-1]["pin"] = True
    edited_summary["achievements"].append({"text": "Shipped X.", "pin": False, "topics": ["api"], "summary_for": ["ai"],
                                           "requires": [], "extra": {}})
    edited_jobs[0]["bullets"][-1]["pin"] = True  # a new key on the bullet the [[projects]] comment hangs on
    edited_jobs[0]["bullets"].append(_bullet("**Did Y** in a week.", topics=["quality"]))
    _save_all(edited_summary, edited_jobs)

    text = base.read_text(encoding="utf-8")
    after = base.read_bytes()
    assert _same_newlines(before, after)
    # the comment before [[projects]] stayed before it and did not move under the new bullet
    assert text.index('text = "**Did Y** in a week."') < text.index(PROJECTS_COMMENT) < text.index("[[projects]]")
    assert text.index("pin = true", text.index("**Cut API p95")) < text.index(PROJECTS_COMMENT)
    data = tomllib.loads(text)
    assert data["summary"]["achievements"][-1] == {"text": "Shipped X.", "summary_for": ["ai"], "topics": ["api"]}
    assert data["headline"] == tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))["headline"]

    _save_all(summary, jobs)
    assert base.read_bytes() == before


def test_delete_last_bullet_keeps_comment_before_next_section(base):
    jobs = cv_base.read_experience()["jobs"]
    jobs[0]["bullets"] = jobs[0]["bullets"][:1]
    cv_base.save_experience(jobs)
    text = base.read_text(encoding="utf-8")
    assert "**Cut API p95" not in text
    assert text.index(PROJECTS_COMMENT) < text.index("[[projects]]")
    assert len(tomllib.loads(text)["experience"][0]["bullets"]) == 1


def test_new_job_and_reorder(base):
    jobs = cv_base.read_experience()["jobs"]
    jobs.insert(0, {"title": "Lead", "company": "Acme", "dates": "2025 – Present", "bullets_max": 3,
                    "intro": ["Small team."], "tech": ["Python"], "tech_always": ["Go"], "extra": {},
                    "bullets": [_bullet("Led things.", pin=True)]})
    cv_base.save_experience(jobs)
    data = tomllib.loads(base.read_text(encoding="utf-8"))
    assert [j["company"] for j in data["experience"]] == ["Acme", "Example Corp"]
    assert data["experience"][0]["tech_always"] == ["Go"] and data["experience"][0]["bullets"][0]["pin"] is True
    assert "projects" in data and data["projects"][0]["name"] == "job-hunter"


def test_extra_keys_survive_and_cannot_override_form_fields(base):
    summary = cv_base.read_summary()
    summary["achievements"][0]["extra"] = {"industries": ["fintech"], "text": "spoofed"}
    cv_base.save_summary(summary["intro"], summary["closing"], 3, summary["achievements"])
    first = cv_base.read_summary()["achievements"][0]
    assert first["extra"] == {"industries": ["fintech"]} and first["text"] != "spoofed"


def test_first_save_creates_base_from_example(tmp_path, monkeypatch):
    example = tmp_path / "cv" / "base.example.toml"
    example.parent.mkdir()
    example.write_bytes(EXAMPLE.read_bytes())
    monkeypatch.setattr(cvgen, "BASE_FILE", example)
    monkeypatch.setattr(config, "CV_BASE_FILE", example)
    summary = cv_base.read_summary()
    cv_base.save_summary("New intro.", summary["closing"], 2, summary["achievements"])
    assert example.read_bytes() == EXAMPLE.read_bytes()
    assert example.with_name("base.toml") == cvgen.BASE_FILE and cvgen.load_base()["summary"]["intro"] == "New intro."


def test_api_edits_reach_generated_cv(base):
    import web.app as web
    http = TestClient(web.app)
    page = http.get("/profile")
    assert page.status_code == 200 and all(f'data-pane="{p}"' in page.text for p in ("head", "summary", "exp", "edu"))

    summary = http.get("/api/cv-base/summary").json()
    summary.pop("file")
    summary["achievements"].insert(0, {"text": "Migrated billing to event sourcing.", "pin": True, "topics": ["distributed"],
                                       "summary_for": [], "requires": [], "extra": {}})
    assert http.put("/api/cv-base/summary", json=summary).status_code == 200

    exp = http.get("/api/cv-base/experience").json()
    exp["jobs"][0]["bullets"].insert(0, _bullet("**Built the payouts API** used by 40 partners.", pin=True))
    assert http.put("/api/cv-base/experience", json={"jobs": exp["jobs"]}).status_code == 200

    md = cvgen.build_markdown(cvgen.load_base(), "Acme", "Python Developer", "Python and PostgreSQL")
    assert "Migrated billing to event sourcing." in md and "**Built the payouts API** used by 40 partners." in md

    bad = http.put("/api/cv-base/experience", json={"jobs": [{**exp["jobs"][0], "bullets": []}]})
    assert bad.status_code == 422  # a workplace without bullets — the CV would not build


def test_topic_hints_use_generator_dictionary(base):
    import web.app as web
    http = TestClient(web.app)
    r = http.post("/api/cv-base/topics", json={"text": "Cut API p95 latency by fixing N+1 queries in PostgreSQL."})
    assert r.status_code == 200 and r.json()["topics"] == ["api", "performance", "sql"]
    assert http.post("/api/cv-base/topics", json={"text": ""}).json()["topics"] == []


def test_other_sections_keep_rest_of_file(base):
    before = base.read_bytes()
    other = cv_base.read_other()
    assert other["education"]["school"] == "Example University" and other["education"]["thesis_ai"] == THESIS
    cv_base.save_other(other["education"], other["languages"], other["publications"])
    assert base.read_bytes() == before  # no changes — byte for byte

    cv_base.save_other({**other["education"], "thesis_ai": "Thesis: retrieval for code search"},
                       "English — C1 · German — B1", "Paper at Example Conf 2024.")
    text = base.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["education"]["thesis_ai"] == "Thesis: retrieval for code search"
    assert data["languages"]["text"] == "English — C1 · German — B1"
    assert data["publications"]["text"] == "Paper at Example Conf 2024."
    # the comment before the story bank stayed before it, a new key did not land under someone else's header
    assert text.index("thesis_ai") < text.index("[languages]") < text.index(STORIES_COMMENT) < text.index("[[stories]]")

    md = cvgen.build_markdown(cvgen.load_base(), "Acme", "Backend Engineer", "Python, PostgreSQL")
    assert "Thesis: retrieval for code search" in md and "German — B1" in md  # the thesis is in a backend CV too
    assert "## Publications\nPaper at Example Conf 2024." in md

    cv_base.save_other({**other["education"], "thesis_ai": ""}, other["languages"], "")
    data = tomllib.loads(base.read_text(encoding="utf-8"))
    assert "thesis_ai" not in data["education"]  # an empty thesis removes the key
    md = cvgen.build_markdown(cvgen.load_base(), "Acme", "AI Engineer", "LLM, RAG, Python")
    assert "Thesis:" not in md and "## Publications" not in md  # empty publications — no empty header

    cv_base.save_other(other["education"], other["languages"], other["publications"])
    assert cv_base.read_other() == other
