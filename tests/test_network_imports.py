"""Project rule: the network only through jobhunter/http.py (robots.txt, 1 request/s per host, retries).

The test walks the code by AST and looks for `import requests` / `from requests ...` outside http.py. Gmail
(mail.py) goes through google-auth: `google.auth.transport.requests` is a google-auth module, not the requests
package, so it is outside the rule and needs no exception (the Gmail API is not a job site, robots.txt does not
apply to it).
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"jobhunter/http.py"}
# requests and the wrappers that could reach the network bypassing Http
NETWORK_MODULES = {"requests", "urllib3", "httpx", "aiohttp", "urllib.request", "http.client"}


def _files() -> list[Path]:
    files = [*ROOT.glob("*.py"), *(ROOT / "jobhunter").rglob("*.py"), *(ROOT / "web").rglob("*.py")]
    return sorted(f for f in files if "__pycache__" not in f.parts)


def _network_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        found += [f"{n} (line {node.lineno})" for n in names
                  if any(n == m or n.startswith(m + ".") for m in NETWORK_MODULES)]
    return found


def test_files_are_found():
    rel = {f.relative_to(ROOT).as_posix() for f in _files()}
    assert rel >= ALLOWED and "web/app.py" in rel and "jobhunter/mail.py" in rel


@pytest.mark.parametrize("path", [f.relative_to(ROOT).as_posix() for f in _files()])
def test_network_only_through_http_module(path):
    if path in ALLOWED:
        return
    found = _network_imports((ROOT / path).read_text(encoding="utf-8"))
    assert not found, f"{path}: network only through jobhunter.http.Http, but here it imports {found}"


@pytest.mark.parametrize(("source", "bad"), [
    ("import requests", True),
    ("import requests.adapters as a", True),
    ("from requests import Session", True),
    ("from urllib.request import urlopen", True),
    ("def f():\n    import httpx", True),
    ("from google.auth.transport.requests import Request", False),
    ("from .http import Http", False),
    ("import requests_cache_like_name", False),
])
def test_detector(source, bad):
    assert bool(_network_imports(source)) is bad
