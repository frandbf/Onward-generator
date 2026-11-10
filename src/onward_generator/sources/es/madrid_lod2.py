"""``es.madrid_lod2``: observed LoD2 volumes of Madrid landmark buildings, opt-in (plan D6, ADR-008).

Dataset: the Ayuntamiento de Madrid "Modelo tridimensional de edificaciones de la cartografía base
municipal" (CC BY 4.0, datos.madrid.es package ``300722-0-ancho-viario-mapas``). The ArcGIS FeatureServer
``MANZANAS_PARA_DESCARGA`` lists one row per city block with ``COD_MANZAN`` and ``URL_OBJ``, a ZIP of one
FME OBJ in ETRS89 / UTM 30N (EPSG:25830) with absolute orthometric heights. This plugin asks the
FeatureServer for the blocks under the bounding box of each landmark candidate of the scene (the
``heights`` landmark rule, ``heights.classify``, unchanged), downloads those block ZIPs as received into
``data/raw/madrid_lod2/`` and writes ``lod2_index.json`` (schema ``onward_generator.lod2_landmarks``) with a
SHA-256 per block, plus ``madrid_lod2.source.json`` beside the folder. It writes no overrides and no scene;
``detail --lod2-landmarks`` reads the index. Opt-in: only ``run --source es.madrid_lod2`` turns it on.
docs/legal/data-sources.md#madrid-3d-building-model-esmadrid_lod2, docs/07-detail.md#lod2-landmarks.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from onward_generator.heights.classify import classify
from onward_generator.heights.config import HeightConfig, load_height_config
from onward_generator.io import atomic_write_json
from onward_generator.net import fetch_bytes
from onward_generator.sources.coverage import CONFIG_DIR, in_coverage
from onward_generator.sources.lidar import USER_AGENT, footprints, load_scene, sidecar_path
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceError, SourceStage, SourceSteps

PLUGIN_ID = "es.madrid_lod2"
FEATURE_SERVER = "https://services-eu1.arcgis.com/UrtTdLP2PYSxwtI6/arcgis/rest/services/MANZANAS_PARA_DESCARGA/FeatureServer/0"
QUERY_HOST = "services-eu1.arcgis.com"
OBJ_HOST = "geoportal.madrid.es"
"""The host of every ``URL_OBJ`` seen on 2026-11-09; a reply naming another host is refused."""
CRS = "EPSG:25830"
DATA_DATE = "2026-07-23"
"""``metadata_modified`` of the CKAN package on the check date (2026-11-09)."""
INDEX_SCHEMA = "onward_generator.lod2_landmarks"
INDEX_FILE = "lod2_index.json"
RAW_DIR = "madrid_lod2"
PROVIDER = "Ayuntamiento de Madrid, Modelo tridimensional de edificaciones de la cartografía base municipal (LoD2)"
ATTRIBUTION = (
    "Origen de los datos: Ayuntamiento de Madrid. Modelo tridimensional de edificaciones de la cartografía base municipal "
    f"(actualizado {DATA_DATE})"
)
MAX_BLOCKS = 40
"""Blocks one map may download; a landmark sits in one block, rarely two."""
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_REPLY_BYTES = 1024 * 1024
COVERAGE_PATH = CONFIG_DIR / "madrid_coverage.json"
LICENCE = LicenceRecord(
    spdx="CC-BY-4.0",
    name="Creative Commons Attribution 4.0 International (Ayuntamiento de Madrid)",
    url="https://creativecommons.org/licenses/by/4.0/",
    attribution=ATTRIBUTION,
    obligations=(
        "Credit 'Origen de los datos: Ayuntamiento de Madrid' with the date of the data, link the licence and "
        "indicate the changes: the LoD2 volumes are clipped to the OSM footprint, moved to the local grid and ground, "
        "and simplified. No endorsement by the Ayuntamiento is implied."
    ),
)

Fetch = Callable[[str, int], bytes]
"""GET a URL and return at most the given number of bytes."""


def block_query_url(bbox_wgs84: tuple[float, float, float, float]) -> str:
    """The FeatureServer query for the blocks that meet a WGS84 longitude/latitude box, sorted by ``COD_MANZAN``."""
    xmin, ymin, xmax, ymax = bbox_wgs84
    query = urllib.parse.urlencode([
        ("where", "1=1"),
        ("geometry", f"{xmin:.7f},{ymin:.7f},{xmax:.7f},{ymax:.7f}"),
        ("geometryType", "esriGeometryEnvelope"),
        ("inSR", "4326"),
        ("spatialRel", "esriSpatialRelIntersects"),
        ("outFields", "COD_MANZAN,URL_OBJ"),
        ("returnGeometry", "false"),
        ("orderByFields", "COD_MANZAN"),
        ("f", "json"),
    ])
    return f"{FEATURE_SERVER}/query?{query}"


def parse_block_reply(body: bytes, *, max_blocks: int = MAX_BLOCKS) -> dict[str, str]:
    """``{COD_MANZAN: URL_OBJ}`` of one FeatureServer reply. An error reply, a missing field or another host is refused."""
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"{PLUGIN_ID}: the block query did not return JSON") from exc
    if not isinstance(document, dict) or "error" in document:
        error = document.get("error") if isinstance(document, dict) else None
        raise SourceError(f"{PLUGIN_ID}: the block query failed: {error!r}")
    features = document.get("features")
    if not isinstance(features, list):
        raise SourceError(f"{PLUGIN_ID}: the block query reply has no features list")
    blocks: dict[str, str] = {}
    for feature in features:
        attributes = feature.get("attributes") if isinstance(feature, dict) else None
        code = attributes.get("COD_MANZAN") if isinstance(attributes, dict) else None
        url = attributes.get("URL_OBJ") if isinstance(attributes, dict) else None
        if not isinstance(code, str) or not code or not isinstance(url, str) or not url:
            raise SourceError(f"{PLUGIN_ID}: a block needs COD_MANZAN and URL_OBJ")
        if not code.replace("-", "").isalnum():
            raise SourceError(f"{PLUGIN_ID}: block code {code!r} is not a plain code")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != OBJ_HOST:
            raise SourceError(f"{PLUGIN_ID}: block {code} points at {parsed.hostname!r}, not {OBJ_HOST}")
        blocks[code] = url
    if len(blocks) > max_blocks:
        raise SourceError(f"{PLUGIN_ID}: {len(blocks)} blocks is over the limit of {max_blocks}")
    return dict(sorted(blocks.items()))


def candidate_ids(scene: Mapping[str, Any], config: HeightConfig | None = None) -> list[str]:
    """The buildings of the scene that ``heights`` flags as landmarks (``heights.classify``), sorted."""
    cfg = config or load_height_config()
    found = []
    for feature in scene.get("features", []):
        if feature.get("kind") != "building":
            continue
        tags = {str(k): str(v) for k, v in (feature.get("tags") or {}).items()}
        if classify(tags, cfg).landmark:
            found.append(str(feature["id"]))
    return sorted(found)


def retry_once(get: Fetch, url: str, max_bytes: int) -> bytes:
    """``get`` once more after a first failure; a second failure raises."""
    try:
        return get(url, max_bytes)
    except SourceError:
        return get(url, max_bytes)


def http_fetch(*, timeout: float = 120.0) -> Fetch:
    """GET through ``net.fetch_bytes`` on the FeatureServer and OBJ hosts only; ``retry_once`` adds the one retry."""

    def fetch(url: str, max_bytes: int) -> bytes:
        return fetch_bytes(
            url,
            allowed_hosts=frozenset({QUERY_HOST, OBJ_HOST}),
            max_bytes=max_bytes,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            error=SourceError,
        )

    return fetch


def fetch_lod2(
    scene_path: Path,
    out: Path,
    *,
    overwrite: bool = False,
    fetch: Fetch | None = None,
    now: datetime | None = None,
    max_blocks: int = MAX_BLOCKS,
) -> dict[str, Any]:
    """Download the block ZIPs under the scene's landmark candidates into ``out/`` with ``lod2_index.json``.

    Nothing is written until every download succeeded, so a failure leaves no index and no partial folder.
    """
    scene, scene_ref = load_scene(scene_path)
    side = sidecar_path(out)
    if (out.exists() or side.exists()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite {out}")
    if (scene.get("frame") or {}).get("crs") != CRS:
        raise SourceError(f"{PLUGIN_ID}: the scene frame must be {CRS}, the CRS of the OBJ files")
    get = fetch or http_fetch()
    retrieved = (now or datetime.now(tz=UTC)).isoformat(timespec="seconds")
    wanted = set(candidate_ids(scene))
    shapes = [(bid, geom) for bid, geom in footprints(scene, "EPSG:4326") if bid in wanted]
    blocks: dict[str, str] = {}
    for _bid, geom in shapes:
        xmin, ymin, xmax, ymax = geom.bounds
        blocks.update(parse_block_reply(retry_once(get, block_query_url((xmin, ymin, xmax, ymax)), MAX_REPLY_BYTES), max_blocks=max_blocks))
    if len(blocks) > max_blocks:
        raise SourceError(f"{PLUGIN_ID}: {len(blocks)} blocks is over the limit of {max_blocks}")
    bodies: list[tuple[dict[str, Any], bytes]] = []
    for code, url in sorted(blocks.items()):
        body = retry_once(get, url, MAX_ZIP_BYTES)
        if not body.startswith(b"PK"):
            raise SourceError(f"{PLUGIN_ID}: block {code} is not a ZIP file")
        bodies.append(({"cod_manzan": code, "path": f"{code}.zip", "sha256": hashlib.sha256(body).hexdigest(),
                        "size_bytes": len(body), "url": url}, body))
    index = {
        "schema": INDEX_SCHEMA,
        "schema_version": "1.0.0",
        "source": PLUGIN_ID,
        "provider": PROVIDER,
        "license": LICENCE.spdx,
        "attribution": ATTRIBUTION,
        "data_date": DATA_DATE,
        "crs": CRS,
        "scene": scene_ref,
        "candidates": sorted(wanted),
        "blocks": [entry for entry, _ in bodies],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{out.name}."))  # pragma: no mutate: only where the staging directory lives before the rename; the result is the same
    try:
        for entry, body in bodies:
            (staging / entry["path"]).write_bytes(body)
        atomic_write_json(staging / INDEX_FILE, index)
        if out.exists():
            shutil.rmtree(out)
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    record = {
        "source": PLUGIN_ID,
        "provider": PROVIDER,
        "license": LICENCE.spdx,
        "license_name": LICENCE.name,
        "license_url": LICENCE.url,
        "attribution": ATTRIBUTION,
        "obligations": LICENCE.obligations,
        "data_date": DATA_DATE,
        "crs": CRS,
        "scene": scene_ref,
        "retrieved_at": retrieved,
        "user_agent": USER_AGENT,
        "service": FEATURE_SERVER,
        "blocks": index["blocks"],
    }
    atomic_write_json(side, record)
    return index


@dataclass(frozen=True, slots=True)
class MadridLod2Plugin:
    id: str = PLUGIN_ID
    licence: LicenceRecord = LICENCE
    produces: frozenset[Kind] = frozenset({"landmark_mesh"})
    opt_in: bool = True

    def covers(self, longitude: float, latitude: float) -> bool:
        return in_coverage(COVERAGE_PATH, longitude, latitude)

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        from onward_generator.next_step import invoke, show  # next_step imports the workspace, which imports the registry

        raw = root / "data" / "raw" / RAW_DIR
        index = raw / INDEX_FILE
        argv = invoke("source", "madrid-lod2", "--scene", show(scene), "--out", show(raw)).argv
        return SourceSteps(stages=(SourceStage(RAW_DIR, argv, index, (scene,), download=True),), overrides=None, landmark_meshes=index)


def describe_blocks(index: Mapping[str, Any]) -> str:
    """``3 block(s) for 2 candidate(s)``: the line the ``source madrid-lod2`` command prints."""
    blocks: Sequence[Any] = index.get("blocks") or []
    candidates: Sequence[Any] = index.get("candidates") or []
    return f"{len(blocks)} block(s) for {len(candidates)} landmark candidate(s)"
