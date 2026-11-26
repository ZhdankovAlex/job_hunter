"""Cache of slow page computations: in memory and on disk (state/web_cache.json).

"Market" analyses all vacancies of a report, "Skills" searches every skill in their texts: after a server
restart the page opens at once. The key is the names and modification times of the input files; a file
changed — recompute.
"""
from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jobhunter import config
from jobhunter.fsutil import atomic_write_text

WEB_CACHE_FILE = config.ROOT / "state" / "web_cache.json"
_cache_lock = threading.Lock()
_memory_cache: dict[str, dict[str, Any]] = {}


_refreshing: dict[str, threading.Thread] = {}  # background recomputes of stale values: at most one per name


def cached(name: str, key: list[Any], compute: Callable[[], Any], stale_ok: bool = False) -> Any:
    """The cached value if the key matches; otherwise compute() and a write to the cache.

    stale_ok — for long computations whose inputs a run rewrites: with an old key it returns the previous
    value at once and runs compute() in the background (one thread per name). It waits only if there is no value yet.
    """
    key = json.loads(json.dumps(key, default=str))  # tuple -> list, Path -> str: comparable with what was read from disk
    with _cache_lock:
        if not _memory_cache and WEB_CACHE_FILE.exists():
            with contextlib.suppress(ValueError):
                _memory_cache.update(json.loads(WEB_CACHE_FILE.read_text(encoding="utf-8")))
        entry = _memory_cache.get(name)
        if entry and entry.get("key") == key:
            return entry["value"]
        if entry and stale_ok:
            running = _refreshing.get(name)
            if not (running and running.is_alive()):
                thread = threading.Thread(target=_refresh, args=(name, key, compute), daemon=True,
                                          name=f"cache-refresh-{name}")
                _refreshing[name] = thread
                thread.start()
            return entry["value"]
    value = compute()
    put(name, key, value)
    return value


def _refresh(name: str, key: list[Any], compute: Callable[[], Any]) -> None:
    try:
        put(name, key, compute())
    except Exception as e:  # the page stays on the previous value; the next request tries again
        import sys
        print(f"background cache recompute {name}: {e}", file=sys.stderr)


def wait_refresh(name: str, timeout: float | None = None) -> None:
    """Waits for the background recompute of name, if one is running (tests, warm-up after a run)."""
    thread = _refreshing.get(name)
    if thread:
        thread.join(timeout)


def put(name: str, key: list[Any], value: Any) -> None:
    """Writes a value to the cache (memory and disk) — for a computation that extends what is already saved."""
    key = json.loads(json.dumps(key, default=str))
    with _cache_lock:
        _memory_cache[name] = {"key": key, "value": value}
        WEB_CACHE_FILE.parent.mkdir(exist_ok=True)
        atomic_write_text(WEB_CACHE_FILE, json.dumps(_memory_cache, ensure_ascii=False, default=str))


def mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0
