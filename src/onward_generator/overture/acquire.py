"""Overture Maps places and buildings clipped to one map (ADR-021). The only Overture network code.

``overture fetch`` resolves the release from the Overture STAC catalogue, picks
from the release's STAC index (``collections.parquet``, one row per file with
its bbox) the few files whose bbox meets the map, then asks DuckDB to read only
those GeoParquet files on S3 with a bbox predicate on the ``bbox`` struct, so
only the row groups that touch the map are transferred. Without the index every
file of the theme would be opened: 512 building files, each footer read over
the network. Each
theme is written as a new parquet file under ``data/raw/`` with a
``.source.json`` sidecar: release, source, query, SHA-256, row count, the
licence of every record by dataset, and the attribution lines. Nothing is
overwritten without ``overwrite``. Rows are not changed: the parquet is the
source extract, and later stages read it without modifying it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from onward_generator import __version__
from onward_generator.geo.sources import sha256_file
from onward_generator.io import atomic_write_json
from onward_generator.maps.workspace import load_map
from onward_generator.net import fetch_bytes
from onward_generator.osm.acquire import OsmDownloadError, inclusion_bbox

PROVIDER = "overture-maps-foundation"
STAC_CATALOG_URL = "https://stac.overturemaps.org/catalog.json"
STAC_HOSTS = frozenset({"stac.overturemaps.org"})
S3_REGION = "us-west-2"
SOURCE_TEMPLATE = "s3://overturemaps-us-west-2/release/{release}/theme={theme}/type={type}/*.parquet"
FILE_PREFIX_TEMPLATE = "s3://overturemaps-us-west-2/release/{release}/theme={theme}/type={type}/"
STAC_INDEX_TEMPLATE = "https://stac.overturemaps.org/{release}/collections.parquet"
"""One row per release file: ``collection`` (the type), ``bbox`` and the S3 href under ``assets.aws.alternate.s3``."""
THEMES: Mapping[str, tuple[str, str]] = {"places": ("places", "place"), "buildings": ("buildings", "building")}
USER_AGENT = f"onward-generator/{__version__} (local map generator)"
STAC_MAX_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_S = 120
# Like Overpass and Catastro: a failed read is tried again three times, waits doubling from 8 s.
FETCH_RETRIES = 3
RETRY_DELAY_S = 8.0
_RELEASE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")

LICENSE_URLS: Mapping[str, str] = {
    "CDLA-Permissive-2.0": "https://cdla.dev/permissive-2-0/",
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "ODbL-1.0": "https://opendatacommons.org/licenses/odbl/1-0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
}
NOTICES: Mapping[str, str] = {
    "foursquare": "Copyright 2024 Foursquare Labs, Inc. All rights reserved. Licensed under the Apache License 2.0; see the NOTICE file of the Overture release.",
}
"""Extra text a dataset's licence asks to carry with the attribution, by lower-case dataset name."""
ATTRIBUTION_TEXT: Mapping[str, str] = {
    "OpenStreetMap": "© OpenStreetMap contributors, Overture Maps Foundation",
    "Microsoft ML Buildings": "Global ML Building Footprints, licensed by Microsoft; Overture Maps Foundation",
    "Esri Community Maps": "Esri Community Maps contributors; Overture Maps Foundation",
    "Google Open Buildings": "Google Open Buildings; Overture Maps Foundation",
    "Instituto Geográfico Nacional (España)": "Work derived from BTN 2024 ign.es; Overture Maps Foundation",
}
"""The wording https://docs.overturemaps.org/attribution/ gives a dataset (checked 2026-10-30). Others: ``Data from <dataset>``."""
DOCUMENTED_LICENSES: Mapping[str, str] = {
    "Instituto Geográfico Nacional (España)": "CC-BY-4.0",
    "Google Open Buildings": "CC-BY-4.0",
    "Esri Community Maps": "CC-BY-4.0",
}
"""Datasets whose records carry no licence, with the licence the Overture attribution page states (checked 2026-10-30)."""


class OvertureError(ValueError):
    """Invalid request, response or Overture file."""


BBox = tuple[float, float, float, float]
"""``(south, west, north, east)`` in WGS84 degrees, as ``osm.acquire.inclusion_bbox`` returns it."""


@dataclass(frozen=True, slots=True)
class LicenseCount:
    dataset: str
    license: str
    records: int
    license_origin: str
    """``record`` when the Overture row states it, ``overture_attribution_page`` from ``DOCUMENTED_LICENSES``, else ``unknown``."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FetchRecord:
    provider: str
    theme: str
    type: str
    release: str
    source: str
    query: str
    bbox: list[float]
    bbox_order: str
    retrieved_at: str
    rows: int
    licenses: list[dict[str, Any]]
    attribution: list[dict[str, str]]
    sha256: str
    size_bytes: int
    duckdb_version: str
    path: str
    files: list[str] = field(default_factory=list)
    """The files read: those of the STAC index that meet the bbox, or empty when ``source`` itself was read."""
    file_selection: str = "unrecorded"
    """``stac_index``, ``glob`` (no index: every file of the theme), ``source`` (a given path), or ``unrecorded`` (a sidecar written before this field)."""
    files_in_theme: int | None = None
    """How many files the index lists for the theme; ``None`` without the index."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Connect = Callable[[], duckdb.DuckDBPyConnection]
Sleep = Callable[[float], None]


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".source.json")


def raw_dir(directory: str | Path) -> Path:
    """``data/raw`` of a map folder."""
    root = Path(directory).expanduser().resolve()
    return root / load_map(root)["paths"]["raw"]


def theme_target(directory: str | Path, theme: str) -> Path:
    """``data/raw/overture_<theme>.parquet`` of a map folder."""
    return raw_dir(directory) / f"overture_{theme}.parquet"


def check_release(release: str) -> str:
    if not _RELEASE.match(release):
        raise OvertureError(f"not an Overture release name: {release!r} (expected YYYY-MM-DD.N)")
    return release


def latest_release(fetch: Callable[[str], bytes] | None = None) -> str:
    """The release the Overture STAC catalogue marks as ``latest``."""
    data = (fetch or _stac_fetch)(STAC_CATALOG_URL)
    try:
        latest = json.loads(data).get("latest")
    except (ValueError, AttributeError) as exc:
        raise OvertureError(f"{STAC_CATALOG_URL} is not a STAC catalogue: {exc}") from exc
    if not isinstance(latest, str):
        raise OvertureError(f"{STAC_CATALOG_URL} names no latest release")
    return check_release(latest)


def _stac_fetch(url: str) -> bytes:
    return fetch_bytes(
        url,
        allowed_hosts=STAC_HOSTS,
        max_bytes=STAC_MAX_BYTES,
        timeout=60.0,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},  # pragma: no mutate: only key case changes survive, urllib capitalizes header names
        retry_429=FETCH_RETRIES,
        retry_transient=FETCH_RETRIES,
        error=OvertureError,
    )


def theme_source(release: str, theme: str) -> str:
    theme_dir, kind = THEMES[theme]
    return SOURCE_TEMPLATE.format(release=check_release(release), theme=theme_dir, type=kind)


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _check_bbox(bbox: BBox) -> BBox:
    south, west, north, east = bbox
    if not (south < north and west < east):
        raise OvertureError(f"invalid bbox {bbox}: south/west must be below north/east")
    return bbox


def clip_query(source: str | Sequence[str], bbox: BBox) -> str:
    """Every column of the rows whose bbox meets ``bbox``, ordered by id so the file is reproducible.

    ``source`` is one path or glob, or a list of files.
    """
    south, west, north, east = _check_bbox(bbox)
    read = _quote(source) if isinstance(source, str) else "[" + ", ".join(_quote(f) for f in source) + "]"
    return (
        f"SELECT * FROM read_parquet({read}, hive_partitioning = false)\n"
        f"WHERE bbox.xmin <= {east:.7f} AND bbox.xmax >= {west:.7f}"
        f" AND bbox.ymin <= {north:.7f} AND bbox.ymax >= {south:.7f}\n"
        "ORDER BY id"
    )


@dataclass(frozen=True, slots=True)
class FileSelection:
    files: list[str]
    """Files whose bbox meets the map, sorted; one file (read with no matching row) when none does."""
    in_theme: int


def select_files(index: bytes, release: str, theme: str, bbox: BBox) -> FileSelection:
    """The files of ``theme`` in a release STAC index (``collections.parquet``) whose bbox meets ``bbox``.

    Every href must be a parquet file of that release, theme and type on the Overture bucket.
    """
    theme_dir, kind = THEMES[theme]
    south, west, north, east = _check_bbox(bbox)
    prefix = FILE_PREFIX_TEMPLATE.format(release=check_release(release), theme=theme_dir, type=kind)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "collections.parquet"  # pragma: no mutate: any name in this empty directory reads the same bytes
        path.write_bytes(index)
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                "SELECT assets.aws.alternate.s3.href,"  # pragma: no mutate: SQL keywords and names are case-insensitive
                f" bbox.xmin <= {east:.7f} AND bbox.xmax >= {west:.7f} AND bbox.ymin <= {north:.7f} AND bbox.ymax >= {south:.7f}"
                f" FROM read_parquet({_quote(str(path))}) WHERE collection = {_quote(kind)} ORDER BY 1"
            ).fetchall()
        except duckdb.Error as exc:
            raise OvertureError(f"the STAC index of {release} cannot be read: {exc}") from exc
        finally:
            connection.close()
    hrefs = [str(href) for href, _ in rows]
    if not hrefs:
        raise OvertureError(f"the STAC index of {release} lists no {kind} files under {prefix}")
    bad = [h for h in hrefs if not (h.startswith(prefix) and h.endswith(".parquet") and "/" not in h[len(prefix):])]
    if bad:
        raise OvertureError(f"the STAC index of {release} lists a {kind} file outside {prefix}: {bad[0]}")
    meeting = [str(href) for href, meets in rows if meets]
    # With no file near the map, read one file: the bbox predicate returns no row and the schema is kept.
    return FileSelection(files=meeting or hrefs[:1], in_theme=len(hrefs))


def stac_index(release: str, fetch: Callable[[str], bytes] | None = None) -> bytes:
    """The release's ``collections.parquet`` from the Overture STAC host."""
    return (fetch or _stac_fetch)(STAC_INDEX_TEMPLATE.format(release=check_release(release)))


def _remote_connection() -> duckdb.DuckDBPyConnection:
    """DuckDB with httpfs for anonymous S3 reads. The Docker image installs httpfs at build time."""
    connection = duckdb.connect()
    try:
        connection.execute("LOAD httpfs")
    except duckdb.Error:
        connection.execute("INSTALL httpfs")  # a signed DuckDB extension; outside the image only
        connection.execute("LOAD httpfs")
    connection.execute(f"SET s3_region = {_quote(S3_REGION)}")
    connection.execute(f"SET http_timeout = {HTTP_TIMEOUT_S}")
    connection.execute(f"SET http_retries = {FETCH_RETRIES}")
    return connection


def license_counts(connection: duckdb.DuckDBPyConnection, parquet: Path) -> list[LicenseCount]:
    """Records per (dataset, licence), from the whole-record source of each row (``property`` empty)."""
    rows = connection.execute(
        "SELECT s.dataset, s.license, count(*) FROM ("  # pragma: no mutate: SQL keywords are case-insensitive
        f" SELECT unnest(list_filter(sources, lambda x: coalesce(x.property, '') = '')) AS s FROM read_parquet({_quote(str(parquet))})"
        ") GROUP BY ALL ORDER BY 1, 2"  # pragma: no mutate: SQL keywords are case-insensitive
    ).fetchall()
    return [_license_count(str(dataset or "unknown"), lic, int(count)) for dataset, lic, count in rows]


def _license_count(dataset: str, stated: str | None, records: int) -> LicenseCount:
    if stated:
        return LicenseCount(dataset, stated, records, "record")
    documented = DOCUMENTED_LICENSES.get(dataset)
    if documented:
        return LicenseCount(dataset, documented, records, "overture_attribution_page")
    return LicenseCount(dataset, "unknown", records, "unknown")


def attribution_lines(pairs: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """One line per (dataset, licence) in the wording of the Overture attribution page, plus any notice."""
    lines: list[dict[str, str]] = []
    for dataset, lic in sorted(set(pairs)):
        line = {"text": attribution_text(dataset), "license": lic, "license_url": LICENSE_URLS.get(lic, "")}
        notice = NOTICES.get(dataset.lower())
        if notice:
            line["notice"] = notice
        lines.append(line)
    return lines


def attribution_text(dataset: str) -> str:
    return ATTRIBUTION_TEXT.get(dataset, f"Data from {dataset}; Overture Maps Foundation")


def _copy(connect: Connect, query: str, tmp: Path, sleep: Sleep) -> duckdb.DuckDBPyConnection:
    delay = RETRY_DELAY_S
    attempt = 0
    while True:
        connection = connect()
        try:
            connection.execute(f"COPY ({query}) TO {_quote(str(tmp))} (FORMAT parquet, COMPRESSION zstd)")
            return connection
        except duckdb.IOException as exc:
            connection.close()
            if attempt >= FETCH_RETRIES:
                raise OvertureError(f"Overture read failed after {attempt + 1} attempts: {exc}") from exc
            attempt += 1
            sleep(delay)
            delay *= 2


def fetch_theme(
    theme: str,
    bbox: BBox,
    target: Path,
    *,
    release: str,
    source: str | None = None,
    selection: FileSelection | None = None,
    connect: Connect = _remote_connection,
    now: datetime | None = None,
    sleep: Sleep = time.sleep,
) -> FetchRecord:
    """Write the rows of one theme that meet ``bbox`` to ``target`` and its sidecar.

    ``source`` defaults to the release on S3; tests pass a local parquet. ``selection``
    (from the STAC index) reads only its files instead of every file of the theme.
    """
    theme_dir, kind = THEMES[theme]
    if source:
        query, mode = clip_query(source, bbox), "source"
        selection = None  # pragma: no mutate: only its truth is read below, and every empty value is false
    else:
        source = theme_source(release, theme)
        query = clip_query(selection.files if selection else source, bbox)
        mode = "stac_index" if selection else "glob"
    retrieved = (now or datetime.now(tz=UTC)).isoformat(timespec="seconds")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")  # pragma: no mutate: any name beside the target works; only the target and its sidecar remain
    tmp.unlink(missing_ok=True)
    try:
        connection = _copy(connect, query, tmp, sleep)
        try:
            rows = int(connection.execute(f"SELECT count(*) FROM read_parquet({_quote(str(tmp))})").fetchone()[0])  # type: ignore[index]
            counts = license_counts(connection, tmp)
        finally:
            connection.close()
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    record = FetchRecord(
        provider=PROVIDER,
        theme=theme_dir,
        type=kind,
        release=release,
        source=source,
        query=query,
        bbox=list(bbox),
        bbox_order="south,west,north,east",
        retrieved_at=retrieved,
        rows=rows,
        licenses=[c.to_dict() for c in counts],
        attribution=attribution_lines((c.dataset, c.license) for c in counts),
        sha256=sha256_file(target),
        size_bytes=target.stat().st_size,
        duckdb_version=duckdb.__version__,
        path=str(target),
        files=list(selection.files) if selection else [],
        file_selection=mode,
        files_in_theme=selection.in_theme if selection else None,
    )
    atomic_write_json(sidecar_path(target), record.to_dict())
    return record


def fetch_map(
    directory: str | Path,
    *,
    release: str | None = None,
    overwrite: bool = False,
    sources: Mapping[str, str] | None = None,
    out: str | Path | None = None,
    connect: Connect = _remote_connection,
    stac: Callable[[str], bytes] | None = None,
    now: datetime | None = None,
    sleep: Sleep = time.sleep,
) -> list[FetchRecord]:
    """Fetch places and buildings for a map folder into ``out`` (default its ``data/raw``).

    Refuses before any request when a file exists.
    """
    raw = raw_dir(directory) if out is None else Path(out).expanduser().resolve()
    targets = {theme: raw / f"overture_{theme}.parquet" for theme in THEMES}
    existing = [p for t in targets.values() for p in (t, sidecar_path(t)) if p.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite {', '.join(map(str, existing))}")
    try:
        bbox = inclusion_bbox(directory)
    except OsmDownloadError as exc:
        raise OvertureError(str(exc)) from exc
    chosen = check_release(release) if release else latest_release(stac)
    given = sources or {}
    index: bytes | None = None
    if any(theme not in given for theme in THEMES):
        try:
            index = stac_index(chosen, stac)
        except OvertureError as exc:
            print(f"overture: no STAC index for {chosen} ({exc}); reading every file of each theme, which is slow", file=sys.stderr)
    records = []
    for theme, target in targets.items():
        selection = select_files(index, chosen, theme, bbox) if index is not None and theme not in given else None
        records.append(fetch_theme(theme, bbox, target, release=chosen, source=given.get(theme), selection=selection,
                                   connect=connect, now=now, sleep=sleep))
    return records


def load_record(parquet: Path) -> FetchRecord:
    """The sidecar of a fetched file, after checking the file still has its recorded SHA-256."""
    side = sidecar_path(parquet)
    if not side.is_file():
        raise OvertureError(f"missing provenance sidecar {side}; fetch with `overture fetch`")
    record = FetchRecord(**json.loads(side.read_bytes()))
    if sha256_file(parquet) != record.sha256:
        raise OvertureError(f"{parquet} does not match the sha256 recorded in {side.name}")
    return record
