"""Atomic writes of state files: an interruption mid-write leaves no empty or truncated file."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Writes to a temporary file next to path and swaps it in with a single os.replace.

    A plain write_text truncates the file first: stopping the run, a crash or a full disk at that moment
    left an empty seen.json / cv/base.toml. The temporary file sits in the same folder, otherwise replace
    is not atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))
