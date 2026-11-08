"""Building heights from a LiDAR height model (nDSM): the shared fetch and normalize of a raster plugin (ADR-018).

``fetch`` asks the plugin's service for the scene box, tile by tile, and keeps
every response body as received in ``data/raw/<id>/`` with a ``.source.json``
beside it (URLs, hashes, retrieval time, licence, the plugin's own metadata).
``normalize`` reads those bodies back, checks their hashes, and writes
``heights_overrides.<id>.json``: for each footprint of the scene, the median
of the cells inside it shrunk by an inner buffer (``raster.footprint_height``).
Only heights inside the ``heights`` bounds are written; every footprint, with
or without a height, is in ``<id>_report.json``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator import __version__
from onward_generator.contracts import is_schema
from onward_generator.geo.sources import sha256_file
from onward_generator.io import atomic_write_json, prepare_outputs
from onward_generator.net import fetch_bytes
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceError, SourceStage, SourceSteps
from onward_generator.sources.raster import BBox, FootprintHeight, Grid, footprint_height, mosaic

USER_AGENT = f"onward-generator/{__version__} (local research tool)"
MARGIN_M = 20.0
MAX_SIDE_M = 6000.0
"""The largest map (1500 m radius, 500 m margin) plus the margin, with room to spare."""
RETRIES = 3
MAX_BYTES = 64 * 1024 * 1024
OVERRIDES_VERSION = "0.1.0"
REPORT_SCHEMA = "onward_generator.source_heights_report"

Fetch = Callable[[str], bytes]


@dataclass(frozen=True, slots=True)
class HeightRule:
    """How a footprint's cells become one height. docs/04-heights.md#lidar."""

    buffer_m: float
    percentile: float
    min_cells: int
    min_valid_share: float
    min_value: float
    confidence: float


@dataclass(frozen=True, slots=True)
class TileRequest:
    url: str
    bbox: BBox
    columns: int
    rows: int


@runtime_checkable
class RasterHeights(Protocol):
    """A plugin whose data is a height-above-ground raster. It also satisfies ``SourcePlugin``."""

    @property
    def id(self) -> str: ...

    @property
    def licence(self) -> LicenceRecord: ...

    @property
    def produces(self) -> frozenset[Kind]: ...

    @property
    def stage_names(self) -> tuple[str, str]: ...

    @property
    def raster_crs(self) -> str: ...

    @property
    def hosts(self) -> frozenset[str]: ...

    @property
    def source_label(self) -> str: ...

    @property
    def rule(self) -> HeightRule: ...

    def covers(self, longitude: float, latitude: float) -> bool: ...

    def steps(self, root: Path, scene: Path) -> SourceSteps: ...

    def requests(self, bbox: BBox) -> list[TileRequest]:
        """The tile requests that cover ``bbox`` (metres in ``raster_crs``)."""
        ...

    def decode(self, body: bytes, request: Mapping[str, Any]) -> Grid:
        """The grid in one response body. ``request`` is the tile's entry in the sidecar."""
        ...

    def metadata(self, bbox_wgs84: BBox, fetch: Fetch) -> dict[str, Any]:
        """Extra facts kept in the sidecar, such as acquisition dates. ``{}`` when there are none."""
        ...

    def data_date(self, longitude: float, latitude: float, metadata: Mapping[str, Any]) -> str:
        """When the data at this point was acquired, as text for the override note and the report."""
        ...


def raster_steps(plugin: RasterHeights, root: Path, scene: Path) -> SourceSteps:
    """The two ``run`` stages of a raster plugin: ``source fetch`` (network) and ``source normalize``.

    With ``map.json`` ``"lidar_roofs": true`` (plan C3) the report is also handed to ``detail`` as ``roof_spread``.
    """
    from onward_generator.maps.workspace import lidar_roofs_enabled  # the workspace imports the registry, which imports the plugins
    from onward_generator.next_step import invoke, show

    raw = root / "data" / "raw" / plugin.id
    out = root / "data" / "processed" / plugin.id
    fetch = invoke("source", "fetch", plugin.id, "--scene", show(scene), "--out", show(raw)).argv
    normalize = invoke("source", "normalize", plugin.id, "--scene", show(scene), "--raw", show(raw), "--out", show(out)).argv
    overrides = out / overrides_name(plugin.id)
    fetch_name, normalize_name = plugin.stage_names
    return SourceSteps(
        stages=(
            SourceStage(fetch_name, fetch, sidecar_path(raw), (scene,), download=True),
            SourceStage(normalize_name, normalize, overrides, (sidecar_path(raw), scene)),
        ),
        overrides=overrides,
        roof_spread=out / report_name(plugin.id) if lidar_roofs_enabled(root) else None,
    )


def overrides_name(plugin_id: str) -> str:
    return f"heights_overrides.{plugin_id}.json"


def report_name(plugin_id: str) -> str:
    return f"{plugin_id}_report.json"


def sidecar_path(raw: Path) -> Path:
    return raw.with_name(raw.name + ".source.json")


def load_scene(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """A geo scene and its reference (path, SHA-256, version)."""
    if not path.is_file():
        raise SourceError(f"scene not found: {path}")
    scene = json.loads(path.read_bytes())
    if not is_schema(scene.get("schema"), "onward_generator.geo_scene"):
        raise SourceError("input is not a onward_generator.geo_scene")
    frame = scene.get("frame") or {}
    if frame.get("coordinate_system") != "local_grid" or frame.get("unit") != "m":
        raise SourceError("scene must be a local grid in metres")
    return scene, {"path": str(path), "sha256": sha256_file(path), "schema_version": scene.get("schema_version")}


def footprints(scene: Mapping[str, Any], crs: str) -> list[tuple[str, BaseGeometry]]:
    """Every building of the scene, by id, moved from the local grid to ``crs``."""
    frame = scene["frame"]
    e0, n0 = frame["origin_projected"]
    transformer = Transformer.from_crs(frame["crs"], crs, always_xy=True)

    def move(coords: np.ndarray) -> np.ndarray:
        return np.column_stack(transformer.transform(coords[:, 0] + e0, coords[:, 1] + n0))

    buildings = sorted((f for f in scene.get("features", []) if f.get("kind") == "building"), key=lambda f: f["id"])
    return [(feature["id"], shapely.transform(shape(feature["geometry"]), move)) for feature in buildings]


def scene_bbox(scene: Mapping[str, Any], crs: str, margin_m: float = MARGIN_M) -> BBox:
    """The box around every building plus ``margin_m``, in ``crs``."""
    shapes = footprints(scene, crs)
    if not shapes:
        raise SourceError("scene has no buildings")
    xmin = min(g.bounds[0] for _, g in shapes) - margin_m
    ymin = min(g.bounds[1] for _, g in shapes) - margin_m
    xmax = max(g.bounds[2] for _, g in shapes) + margin_m
    ymax = max(g.bounds[3] for _, g in shapes) + margin_m
    if xmax - xmin > MAX_SIDE_M or ymax - ymin > MAX_SIDE_M:
        raise SourceError(f"scene box is {xmax - xmin:.0f} x {ymax - ymin:.0f} m; limit is {MAX_SIDE_M:.0f} m a side")
    return (xmin, ymin, xmax, ymax)


def to_wgs84(bbox: BBox, crs: str) -> BBox:
    xmin, ymin, xmax, ymax = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform_bounds(*bbox)  # pragma: no mutate: only the code's case survives (pyproj ignores it); the axis order is held by the tests
    return (xmin, ymin, xmax, ymax)


def http_fetch(plugin: RasterHeights, *, timeout: float = 120.0, pause_s: float = 1.0) -> Fetch:
    """GET through ``net.fetch_bytes`` on the plugin's hosts, retried like Catastro, one request per ``pause_s``."""

    def fetch(url: str) -> bytes:
        body = fetch_bytes(
            url,
            allowed_hosts=plugin.hosts,
            max_bytes=MAX_BYTES,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            retry_429=RETRIES,
            retry_transient=RETRIES,
            error=SourceError,
        )
        time.sleep(pause_s)
        return body

    return fetch


def fetch_raster(
    plugin: RasterHeights,
    scene_path: Path,
    out: Path,
    *,
    overwrite: bool = False,
    fetch: Fetch | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Download the plugin's tiles for the scene box into ``out/`` and write ``out.source.json``."""
    scene, scene_ref = load_scene(scene_path)
    side = sidecar_path(out)
    if (out.exists() or side.exists()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite {out}")
    get = fetch or http_fetch(plugin)
    bbox = scene_bbox(scene, plugin.raster_crs)
    retrieved = (now or datetime.now(tz=UTC)).isoformat(timespec="seconds")
    metadata = plugin.metadata(to_wgs84(bbox, plugin.raster_crs), get)
    replies: list[tuple[dict[str, Any], bytes]] = []
    for index, request in enumerate(plugin.requests(bbox)):
        body = get(request.url)
        entry = {"file": f"tile_{index:03d}.bin", **asdict(request), "bbox": list(request.bbox)}
        plugin.decode(body, entry)  # refuse an error page before anything is written
        entry.update(sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
        replies.append((entry, body))
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{out.name}."))  # pragma: no mutate: only where the staging directory lives before the rename; the result is the same
    try:
        for entry, body in replies:
            (staging / entry["file"]).write_bytes(body)
        if out.exists():
            shutil.rmtree(out)
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging)
        raise
    record = {
        "source": plugin.id,
        "provider": plugin.source_label,
        "license": plugin.licence.spdx,
        "license_name": plugin.licence.name,
        "license_url": plugin.licence.url,
        "attribution": plugin.licence.attribution,
        "obligations": plugin.licence.obligations,
        "crs": plugin.raster_crs,
        "bbox": list(bbox),
        "scene": scene_ref,
        "retrieved_at": retrieved,
        "user_agent": USER_AGENT,
        "tiles": [entry for entry, _body in replies],
        "metadata": metadata,
    }
    atomic_write_json(side, record)
    return record


def load_raster(plugin: RasterHeights, raw: Path) -> tuple[dict[str, Any], Grid]:
    """The sidecar and the mosaic of its tiles, after checking every tile's SHA-256."""
    side = sidecar_path(raw)
    if not side.is_file():
        raise SourceError(f"missing provenance sidecar {side}; download with `source fetch {plugin.id}`")
    record = json.loads(side.read_bytes())
    if record.get("source") != plugin.id:
        raise SourceError(f"{side} was written by {record.get('source')!r}, not {plugin.id}")
    grids = []
    for entry in record["tiles"]:
        body = (raw / entry["file"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise SourceError(f"{raw / entry['file']} does not match the sha256 recorded in {side.name}")
        grids.append(plugin.decode(body, entry))
    return record, mosaic(grids)


@dataclass(frozen=True, slots=True)
class BuildingHeight:
    id: str
    status: str
    sample: FootprintHeight
    date: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "date": self.date, **asdict(self.sample)}


def measure(
    plugin: RasterHeights, scene: Mapping[str, Any], grid: Grid, metadata: Mapping[str, Any], bounds: tuple[float, float],
) -> list[BuildingHeight]:
    """One result per scene building: ``measured``, ``out_of_bounds`` or ``no_data``."""
    rule = plugin.rule
    to_degrees = Transformer.from_crs(plugin.raster_crs, "EPSG:4326", always_xy=True).transform  # pragma: no mutate: only the code's case survives (pyproj ignores it); the axis order is held by the date test
    results = []
    for building_id, footprint in footprints(scene, plugin.raster_crs):
        sample = footprint_height(
            grid, footprint, buffer_m=rule.buffer_m, percentile=rule.percentile, min_cells=rule.min_cells,
            min_valid_share=rule.min_valid_share, min_value=rule.min_value,
        )
        longitude, latitude = to_degrees(*footprint.representative_point().coords[0])
        if sample.height_m is None:
            status = "no_data"
        elif not bounds[0] <= sample.height_m <= bounds[1]:
            status = "out_of_bounds"
        else:
            status = "measured"
        results.append(BuildingHeight(building_id, status, sample, plugin.data_date(longitude, latitude, metadata)))
    return results


def overrides_document(plugin: RasterHeights, results: Sequence[BuildingHeight], record: Mapping[str, Any]) -> dict[str, Any]:
    """``heights_overrides.<id>.json``: a ``height_m`` per measured building."""
    rule = plugin.rule
    entries = []
    for result in results:
        if result.status != "measured":
            continue
        s = result.sample
        entries.append({
            "id": result.id,
            "height_m": s.height_m,
            "source": plugin.source_label,
            "license": plugin.licence.spdx,
            "retrieved_at": record["retrieved_at"],
            "confidence": rule.confidence,
            "note": (
                f"p{rule.percentile:g} of {s.valid}/{s.cells} cells, inner buffer {s.buffer_m:g} m; "
                f"p25-p75 {s.p25_m}-{s.p75_m} m; data {result.date}"
            ),
        })
    return {"version": OVERRIDES_VERSION, "overrides": entries}


def report_document(
    plugin: RasterHeights, results: Sequence[BuildingHeight], record: Mapping[str, Any], scene_ref: Mapping[str, Any], raw: Path,
) -> dict[str, Any]:
    counts = {status: sum(1 for r in results if r.status == status) for status in ("measured", "out_of_bounds", "no_data")}
    heights = sorted(r.sample.height_m for r in results if r.status == "measured" and r.sample.height_m is not None)
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": "1.0.0",
        "source": plugin.id,
        "provider": plugin.source_label,
        "license": plugin.licence.spdx,
        "attribution": plugin.licence.attribution,
        "raw": {"path": str(raw), "sidecar_sha256": sha256_file(sidecar_path(raw)), "retrieved_at": record["retrieved_at"]},
        "scene": dict(scene_ref),
        "rule": asdict(plugin.rule),
        "counts": {"buildings": len(results), **counts},
        "height_range_m": [heights[0], heights[-1]] if heights else None,
        "buildings": [r.to_dict() for r in results],
    }


def normalize_raster(plugin: RasterHeights, scene_path: Path, raw: Path, out: Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Write the overrides and the report for the scene from a fetched raster. Returns the report."""
    from onward_generator.heights import load_height_config

    scene, scene_ref = load_scene(scene_path)
    record, grid = load_raster(plugin, raw)
    config = load_height_config()
    results = measure(plugin, scene, grid, record["metadata"], (config.bounds_min_m, config.bounds_max_m))
    if sha256_file(scene_path) != scene_ref["sha256"]:
        raise SourceError("scene changed while being read")
    out_overrides, out_report = prepare_outputs(
        out, (overrides_name(plugin.id), report_name(plugin.id)), overwrite=overwrite, protected=(scene_path,),
    )
    report = {**report_document(plugin, results, record, scene_ref, raw), "outputs": [str(out_overrides), str(out_report)]}
    atomic_write_json(out_overrides, overrides_document(plugin, results, record))
    atomic_write_json(out_report, report)
    return report
