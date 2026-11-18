"""Cache of slow page computations: in memory and on disk (state/web_cache.json).

"Market" analyses all vacancies of a report, "Skills" searches every skill in their texts: after a server
restart the page opens at once. The key is the names and modification times of the input files; a file
changed — recompute.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jobhunter import config

WEB_CACHE_FILE = config.ROOT / "state" / "web_cache.json"
_cache_lock = threading.Lock()
_memory_cache: dict[str, dict] = {}


def cached(name: str, key: list, compute: Callable[[], Any]) -> Any:
    """The cached value if the key matches; otherwise compute() and a write to the cache."""
    key = json.loads(json.dumps(key, default=str))  # tuple -> list, Path -> str: comparable with what was read from disk
    with _cache_lock:
        if not _memory_cache and WEB_CACHE_FILE.exists():
            try:
                _memory_cache.update(json.loads(WEB_CACHE_FILE.read_text(encoding="utf-8")))
            except ValueError:
                pass
        entry = _memory_cache.get(name)
        if entry and entry.get("key") == key:
            return entry["value"]
    value = compute()
    put(name, key, value)
    return value


def put(name: str, key: list, value: Any) -> None:
    """Writes a value to the cache (memory and disk) — for a computation that extends what is already saved."""
    key = json.loads(json.dumps(key, default=str))
    with _cache_lock:
        _memory_cache[name] = {"key": key, "value": value}
        WEB_CACHE_FILE.parent.mkdir(exist_ok=True)
        WEB_CACHE_FILE.write_text(json.dumps(_memory_cache, ensure_ascii=False, default=str), encoding="utf-8")


def mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0
