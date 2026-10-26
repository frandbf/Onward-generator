"""Data acquisition: registration of local source files and their provenance.

This module never modifies, moves or rewrites a source file. It only reads it
to compute a content hash, so that later stages can prove the file is
unchanged. ``map create`` fetches the file through ``osm.acquire``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from onward_generator.domain.models import SourceDataset

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".osm"})
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class LicenseInfo:
    provider: str
    license: str
    license_url: str
    attribution: str


OPENSTREETMAP = LicenseInfo(
    provider="openstreetmap",
    license="ODbL-1.0",
    license_url="https://www.openstreetmap.org/copyright",
    attribution="© OpenStreetMap contributors",
)


class SourceError(ValueError):
    """Raised when a source file cannot be registered."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def register_local_source(
    path: str | Path,
    *,
    license_info: LicenseInfo = OPENSTREETMAP,
    retrieved_at: datetime | None = None,
    data_timestamp: str | None = None,
    now: datetime | None = None,
) -> SourceDataset:
    """Validate a local OSM file and record its provenance.

    ``retrieved_at`` is the date the data was downloaded. When it is not
    declared, the file modification time is used and flagged as such.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise SourceError(f"source file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise SourceError(
            f"unsupported source format {source.suffix!r}; supported: {sorted(SUPPORTED_SUFFIXES)}"
        )
    resolved = source.resolve()
    stat = resolved.stat()

    if retrieved_at is not None:
        if retrieved_at.tzinfo is None:
            raise SourceError("retrieved_at must be timezone-aware")
        retrieved, origin = retrieved_at, "declared"
    else:
        retrieved, origin = datetime.fromtimestamp(stat.st_mtime, tz=UTC), "file_mtime"

    return SourceDataset(
        provider=license_info.provider,
        license=license_info.license,
        license_url=license_info.license_url,
        attribution=license_info.attribution,
        path=str(resolved),
        sha256=sha256_file(resolved),
        size_bytes=stat.st_size,
        registered_at=now or datetime.now(tz=UTC),
        retrieved_at=retrieved,
        retrieved_at_origin=origin,
        data_timestamp=data_timestamp,
    )


def verify_unchanged(source: SourceDataset) -> None:
    """Fail if the file no longer matches the registered hash."""
    current = sha256_file(Path(source.path))
    if current != source.sha256:
        raise SourceError(
            f"source file changed since registration: {source.path} "
            f"(expected {source.sha256}, found {current})"
        )
