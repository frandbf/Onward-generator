"""Acquisition of Catastro INSPIRE building parts (BU WFS). The only network code.

Downloads are explicit (CLI ``cadastre download``). A box larger than the
service limit is split into tiles of at most ``MAX_AREA_KM2``; the tiles are
merged into one GML, a part repeated by a later tile dropped by ``localId``.
The file is written atomically to a new path and described by a
``.source.json`` sidecar holding every request, the retrieval time, hashes
and the licence. Nothing here is used by tests that need the network.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from onward_generator import __version__
from onward_generator.geo.sources import LicenseInfo, sha256_file
from onward_generator.net import fetch_bytes

WFS_BU_URL = "https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx"
WFS_HOSTS = frozenset({"ovc.catastro.meh.es"})
CRS = "EPSG:25830"
"""Default request CRS (mainland Spain 6° W–0°). ``cadastre.zones`` picks the CRS for other zones."""
USER_AGENT = f"onward-generator/{__version__} (local research tool)"
MAX_AREA_KM2 = 1.0
TILE_SIDE_CM = 100_000
"""A tile is at most 1 km on a side, so at most ``MAX_AREA_KM2``. Tiles are cut on a 1 cm grid."""
MAX_TILES = 25
"""5 × 5 km: the largest map (1500 m radius, 500 m margin) plus the download margin."""
MAX_BYTES = 64 * 1024 * 1024
DOWNLOAD_MARGIN_M = 20.0
# Like Overpass: 429, 5xx and dropped connections are retried three times, waits doubling from 8 s.
CATASTRO_RETRIES = 3
PARTS_TYPE = "bu:BuildingPart"
BUILDINGS_TYPE = "bu:Building"
"""One feature per parcel: its predominant ``currentUse`` and its dwelling and unit counts (ADR-022)."""

CATASTRO_BU = LicenseInfo(
    provider="catastro-inspire-bu",
    license="CC-BY-4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    attribution="Dirección General del Catastro, INSPIRE Buildings (BU)",
)
LICENSE_NOTES = (
    "Catastro INSPIRE licence (v1.0, 2016): reuse, including commercial, only after transformation; "
    "the original data must not be redistributed; derived products must not be presented as "
    "'cartografía catastral' or 'información catastral'. "
    "https://www.catastro.hacienda.gob.es/webinspire/documentos/Licencia.pdf"
)


class CadastreError(ValueError):
    """Invalid request, response or cadastre file."""


BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    provider: str
    license: str
    license_url: str
    attribution: str
    license_notes: str
    url: str
    bbox: list[float]
    crs: str
    retrieved_at: str
    sha256: str
    size_bytes: int
    user_agent: str
    tiles: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".source.json")



def validate_bbox(bbox: BBox, *, max_area_km2: float = MAX_AREA_KM2) -> BBox:
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    if not (xmin < xmax and ymin < ymax):
        raise CadastreError(f"invalid bbox {bbox}: min must be below max")
    if not (100_000 <= xmin <= 900_000 and 3_000_000 <= ymin <= 5_000_000):
        raise CadastreError(f"bbox {bbox} is not in projected UTM metres over Spain (degrees given?)")
    area_km2 = (xmax - xmin) * (ymax - ymin) / 1e6
    if area_km2 > max_area_km2:
        raise CadastreError(f"bbox covers {area_km2:.2f} km²; limit is {max_area_km2} km²")
    return (xmin, ymin, xmax, ymax)


def buildings_path(gml: Path) -> Path:
    """Where ``cadastre download`` writes the ``bu:Building`` features beside the parts: ``<stem>_buildings.gml``."""
    return gml.with_name(f"{gml.stem}_buildings{gml.suffix}")


def building_parts_url(bbox: BBox, crs: str = CRS, typename: str = PARTS_TYPE) -> str:
    """The ``GetFeature`` URL of ``typename`` features (parts, or ``BUILDINGS_TYPE``) in ``bbox``."""
    xmin, ymin, xmax, ymax = validate_bbox(bbox)
    query = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typenames": typename,
        "bbox": f"{xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}",
        "srsName": crs,
    }
    return f"{WFS_BU_URL}?{urllib.parse.urlencode(query, safe=',:')}"


def _cm(low: float, high: float) -> tuple[int, int, int]:
    """``low`` down and ``high`` up to whole centimetres, and the number of tiles between them.

    round() first: 664243.98 * 100 is 66424397.99999999, which must stay 66424398 cm.
    """
    low_cm, high_cm = math.floor(round(low * 100, 3)), math.ceil(round(high * 100, 3))
    return low_cm, high_cm, math.ceil((high_cm - low_cm) / TILE_SIDE_CM)


def _cuts(low_cm: int, high_cm: int, count: int) -> list[float]:
    return [(low_cm + (high_cm - low_cm) * i // count) / 100 for i in range(count + 1)]


def split_bbox(bbox: BBox) -> list[BBox]:
    """Tiles of at most 1 km × 1 km that cover ``bbox`` exactly, south-west first, row by row.

    The box is first grown outwards to whole centimetres, the precision of the
    request URL. Neighbouring tiles share their edge, so nothing falls between them.
    """
    xmin, ymin, xmax, ymax = validate_bbox(bbox, max_area_km2=math.inf)
    x0, x1, columns = _cm(xmin, xmax)
    y0, y1, rows = _cm(ymin, ymax)
    if columns * rows > MAX_TILES:
        raise CadastreError(f"bbox {bbox} needs {columns * rows} tiles; limit is {MAX_TILES}")
    xs, ys = _cuts(x0, x1, columns), _cuts(y0, y1, rows)
    return [(west, south, east, north) for south, north in pairwise(ys) for west, east in pairwise(xs)]


def bbox_from_scene(scene: Mapping[str, Any], margin_m: float) -> BBox:
    """Bbox of every building in a geo scene, grown by ``margin_m``, in the scene CRS."""
    frame = scene.get("frame") or {}
    if frame.get("unit") != "m":
        raise CadastreError("scene frame must be in metres")
    e0, n0 = frame["origin_projected"]
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords: Any) -> None:
        if coords and isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for feature in scene.get("features", []):
        if feature.get("kind") == "building":
            walk(feature["geometry"]["coordinates"])
    if not xs:
        raise CadastreError("scene has no buildings")
    return (e0 + min(xs) - margin_m, n0 + min(ys) - margin_m, e0 + max(xs) + margin_m, n0 + max(ys) + margin_m)


Fetcher = Callable[[str, float], bytes]


def _http_fetch(url: str, timeout: float, sleep: Callable[[float], None] = time.sleep) -> bytes:
    return fetch_bytes(
        url,
        allowed_hosts=WFS_HOSTS,
        max_bytes=MAX_BYTES,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"},  # pragma: no mutate: only key case changes survive, urllib capitalizes header names; test_net_callers pins the rest
        retry_429=CATASTRO_RETRIES,
        retry_transient=CATASTRO_RETRIES,
        error=CadastreError,
        error_body=True,
        sleep=sleep,
    )


def _check_payload(data: bytes) -> None:
    head = data[:4096]
    if b"<gml:FeatureCollection" not in head and b"FeatureCollection" not in head:
        snippet = head[:200].decode("latin-1", "replace")
        raise CadastreError(f"response is not a WFS FeatureCollection: {snippet!r}")
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise CadastreError("response contains DTD or entity declarations")


_LOCAL_ID = "{urn:x-inspire:specification:gmlas:BaseTypes:3.2}localId"
_PREFIXES = {
    "gml": "http://www.opengis.net/gml/3.2",
    "base": "urn:x-inspire:specification:gmlas:BaseTypes:3.2",
    "bu-base": "http://inspire.jrc.ec.europa.eu/schemas/bu-base/3.0",
    "bu-core2d": "http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0",
    "bu-ext2d": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
"""Prefixes of the Catastro BU response, so the merged file reads like the original."""


def _member_id(member: ET.Element) -> str | None:
    found = member.find(f".//{_LOCAL_ID}")
    return None if found is None or not found.text else found.text.strip()


def merge_tiles(payloads: Sequence[bytes]) -> tuple[bytes, list[dict[str, int]]]:
    """One FeatureCollection from the tile responses, and per tile its members and the repeats dropped.

    The first tile is the base. A member of a later tile whose ``localId`` an
    earlier tile already holds is a part that crosses a tile edge; it is
    dropped. A single tile is returned byte for byte.
    """
    counts: list[dict[str, int]] = []
    roots = [ET.fromstring(data) for data in payloads]
    seen: set[str] = set()
    for index, root in enumerate(roots):
        kept: set[str] = set()
        dropped = 0
        for member in list(root):
            local_id = _member_id(member)
            if local_id in seen:
                dropped += 1
                continue
            if local_id is not None:
                kept.add(local_id)
            if index:
                roots[0].append(member)
        counts.append({"members": len(root), "repeats_dropped": dropped})
        seen |= kept
    if len(payloads) == 1:
        return payloads[0], counts
    for prefix, uri in _PREFIXES.items():
        ET.register_namespace(prefix, uri)
    return ET.tostring(roots[0], encoding="utf-8", xml_declaration=True), counts


def download_building_parts(
    bbox: BBox,
    out_path: str | Path,
    *,
    crs: str = CRS,
    overwrite: bool = False,
    timeout: float = 180.0,
    fetch: Fetcher = _http_fetch,
    now: datetime | None = None,
    typename: str = PARTS_TYPE,
) -> DownloadRecord:
    """Download ``typename`` features (BuildingPart by default) in ``bbox`` (metres in ``crs``) to ``out_path`` (+ sidecar).

    A box over ``MAX_AREA_KM2`` is fetched tile by tile (``split_bbox``) and merged.
    """
    target = Path(out_path).expanduser().resolve()
    if target.suffix.lower() != ".gml":
        raise CadastreError("output must be a .gml file")
    side = sidecar_path(target)
    if (target.exists() or side.exists()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite {target}")
    tiles = split_bbox(bbox)
    urls = [building_parts_url(tile, crs, typename) for tile in tiles]
    retrieved = (now or datetime.now(tz=UTC)).isoformat(timespec="seconds")
    payloads: list[bytes] = []
    for url in urls:
        payloads.append(fetch(url, timeout))
        _check_payload(payloads[-1])
    data, counts = merge_tiles(payloads)

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    record = DownloadRecord(
        provider=CATASTRO_BU.provider,
        license=CATASTRO_BU.license,
        license_url=CATASTRO_BU.license_url,
        attribution=CATASTRO_BU.attribution,
        license_notes=LICENSE_NOTES,
        url=urls[0] if len(urls) == 1 else WFS_BU_URL,
        bbox=[tiles[0][0], tiles[0][1], tiles[-1][2], tiles[-1][3]],
        crs=crs,
        retrieved_at=retrieved,
        sha256=sha256_file(target),
        size_bytes=target.stat().st_size,
        user_agent=USER_AGENT,
        tiles=[
            {
                "url": urls[i], "bbox": list(tiles[i]), "sha256": hashlib.sha256(payloads[i]).hexdigest(),
                "size_bytes": len(payloads[i]), **counts[i],
            }
            for i in range(len(tiles))
        ],
    )
    side.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def load_download_record(gml_path: Path) -> DownloadRecord:
    side = sidecar_path(gml_path)
    if not side.is_file():
        raise CadastreError(f"missing provenance sidecar {side}; download with `cadastre download`")
    record = DownloadRecord(**json.loads(side.read_text(encoding="utf-8")))
    if sha256_file(gml_path) != record.sha256:
        raise CadastreError(f"{gml_path} does not match the sha256 recorded in {side.name}")
    return record
