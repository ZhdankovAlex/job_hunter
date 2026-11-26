"""Codes of reject reasons and marks: decisions go by the code, the shown text can change."""
import ast
from pathlib import Path
from types import SimpleNamespace

import main
from jobhunter import report, verify
from jobhunter.reasons import BULK, GROUPS, Mark, Reject, has_mark, mark_detail

ROOT = Path(__file__).resolve().parent.parent


def test_every_reject_code_has_group():
    assert set(GROUPS) == set(Reject)


def _reject_calls() -> list[tuple[str, int, ast.Call]]:
    calls = []
    for path in [*sorted((ROOT / "jobhunter").glob("*.py")), ROOT / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name in ("reject", "_reject"):
                    calls.append((path.name, node.lineno, node))
    return calls


def test_every_reject_passes_a_code():
    """Every reject in filters / verify / freehire / main passes a Reject.X code, not only the text."""
    calls = _reject_calls()
    assert len(calls) >= 12
    for name, line, call in calls:
        args = [a for a in call.args if not (isinstance(a, ast.Name) and a.id == "job")]
        first = args[0] if args else None
        assert (isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name)
                and first.value.id == "Reject"), f"{name}:{line}: reject without a Reject code"


def test_reason_group_by_code_not_text():
    job = SimpleNamespace(reject="the wording was changed", reject_code=Reject.ONSITE)
    assert main._reason_group(job) == "location"
    assert main._reason_group(SimpleNamespace(reject="", reject_code="")) == "wrong role"


def test_verify_sets_code():
    text = "This role is onsite in our Berlin office 5 days a week."
    job = SimpleNamespace(title="Backend Engineer", location="Berlin", url="", reject="", reject_quote="")
    verify.check(job, text, True)
    assert job.reject_code == Reject.ONSITE and job.reject == verify.ONSITE_REASON


def test_pick_rejected_by_code():
    """Bulk reasons go by the code: the text "older than 30 days" is not needed to count as bulk."""
    mk = lambda code, rel: SimpleNamespace(reject=f"text {code}", reject_code=code, relevance=rel)
    rows = [mk(Reject.TOO_OLD, 10) for _ in range(5)] + [mk(Reject.JVM, 1), mk(Reject.ONSITE, 0)]
    picked = report.pick_rejected(rows)
    assert [j.reject_code for j in picked[:2]] == [Reject.ONSITE, Reject.JVM]
    assert sum(j.reject_code in BULK for j in picked) == report.REJECTED_PER_BULK_REASON


def test_marks_with_detail():
    marks = ["🆕", Mark.PYTHON_NOT_PRIMARY.detail("Go"), Mark.LOCAL_NICE]
    assert has_mark(marks, Mark.PYTHON_NOT_PRIMARY) and has_mark(marks, Mark.LOCAL_NICE)
    assert not has_mark(marks, Mark.NOT_ENGLISH)
    assert mark_detail(marks, Mark.PYTHON_NOT_PRIMARY) == "Go"
