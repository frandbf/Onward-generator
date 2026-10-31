"""Shared file output helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _default_file_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in chunks."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, hashlib.sha256).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes through a temporary file and rename it into place."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, _default_file_mode())
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a temporary file and rename it into place."""
    atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def prepare_outputs(directory: str | Path, names: tuple[str, ...], *, overwrite: bool, protected: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """Resolve output paths, refusing to clobber inputs or existing files."""
    base = Path(directory).expanduser().resolve()
    targets = tuple(base / name for name in names)
    if any(p.resolve() in targets for p in protected):
        raise ValueError("output would overwrite an input file")
    existing = [p for p in targets if p.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing outputs: {', '.join(map(str, existing))}")
    base.mkdir(parents=True, exist_ok=True)
    return targets
