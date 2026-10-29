"""One Overpass request for the square around a map centre.

``map create`` calls this after writing the folder. The bbox is the area of
interest plus its margin, measured in the map CRS and converted back to WGS84.
Degrees are never treated as metres. The ``.osm`` stays under ``data/raw/``
and is not committed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely import Polygon

from onward_generator import __version__
from onward_generator.geo.aoi import AoiError, load_aoi_geojson
from onward_generator.geo.clipping import ClipError, inclusion_zone
from onward_generator.geo.projection import LocalFrame, ProjectionError
from onward_generator.geo.sources import OPENSTREETMAP, sha256_file
from onward_generator.maps.workspace import MapError, load_map
from onward_generator.net import fetch_bytes

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_HOSTS = frozenset({"overpass-api.de"})
USER_AGENT = f"onward-generator/{__version__} (local map generator)"
MAX_BYTES = 32 * 1024 * 1024
MAX_SIDE_M = 4100.0
"""radius 1500 m plus margin 500 m, on both sides, with a little rounding."""

_OSM_BASE = re.compile(br'osm_base="([^"]+)"')


class OsmDownloadError(ValueError):
    """The map area cannot be fetched, or Overpass did not return OSM XML."""


BBox = tuple[float, float, float, float]
"""``(south, west, north, east)`` in WGS84 degrees. Overpass order."""


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    provider: str
    license: str
    license_url: str
    attribution: str
    endpoint: str
    query: str
    bbox: list[float]
    bbox_order: str
    center: dict[str, float]
    radius_m: float
    margin_m: float
    crs: str
    retrieved_at: str
    osm_base: str | None
    sha256: str
    size_bytes: int
    user_agent: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Fetcher = Callable[[str, str, float], bytes]


def osm_target(directory: str | Path) -> Path:
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    raw = document.get("paths", {}).get("raw", "data/raw")
    return root / raw / f"{document['id']}.osm"


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".source.json")


def inclusion_bbox(directory: str | Path) -> BBox:
    """WGS84 bbox of the playable square grown by the map margin, in metres."""
    root = Path(directory).expanduser().resolve()
    try:
        document = load_map(root)
    except MapError as exc:
        raise OsmDownloadError(str(exc)) from exc
    center = document["center"]
    longitude = float(center["longitude"])
    latitude = float(center["latitude"])
    margin_m = float(document["margin_m"])
    crs = str(document["crs"])
    try:
        area = load_aoi_geojson(root / document["paths"]["aoi"], margin_m=margin_m, crs=crs)
        frame = LocalFrame.create(longitude, latitude, crs)
        local = frame.to_local(Polygon(area.polygon))
        zone = inclusion_zone(local, margin_m)
    except (AoiError, ProjectionError, ClipError, KeyError, TypeError, ValueError) as exc:
        raise OsmDownloadError(str(exc)) from exc
    minx, miny, maxx, maxy = zone.bounds
    if (maxx - minx) > MAX_SIDE_M or (maxy - miny) > MAX_SIDE_M:
        raise OsmDownloadError(
            f"inclusion square is {maxx - minx:.0f} by {maxy - miny:.0f} m; limit is {MAX_SIDE_M:.0f} m"
        )
    geographic = frame.to_wgs84(zone)
    west, south, east, north = geographic.bounds
    if not (south < latitude < north and west < longitude < east):
        raise OsmDownloadError("inclusion bbox does not cover the map centre")
    return (float(south), float(west), float(north), float(east))


def overpass_query(bbox: BBox) -> str:
    south, west, north, east = bbox
    box = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    return (
        "[out:xml][timeout:90];\n"
        "(\n"
        f"  node({box});\n"
        f"  way({box});\n"
        f"  relation({box});\n"
        ");\n"
        "(._;>;);\n"
        "out meta;\n"
    )


def _http_fetch(endpoint: str, query: str, timeout: float) -> bytes:
    if endpoint != OVERPASS_URL:
        raise OsmDownloadError("refusing an Overpass endpoint other than overpass-api.de")
    return fetch_bytes(
        endpoint,
        allowed_hosts=OVERPASS_HOSTS,
        max_bytes=MAX_BYTES,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,  # pragma: no mutate: urllib capitalizes header names
            "Accept": "application/xml, text/xml",  # pragma: no mutate: urllib capitalizes header names
            "Content-Type": "application/x-www-form-urlencoded",  # pragma: no mutate: urllib capitalizes header names
        },
        data=urllib.parse.urlencode({"data": query}).encode(),
        error=OsmDownloadError,
        error_body=True,
    )


def _check_payload(data: bytes) -> str | None:
    if len(data) < 20:
        raise OsmDownloadError("empty OSM response")
    head = data.lstrip()[:8192]
    lowered = head.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise OsmDownloadError("response contains DTD or entity declarations")
    if not (head.startswith(b"<?xml") or head.startswith(b"<osm")):
        snippet = head[:180].decode("utf-8", "replace")
        raise OsmDownloadError(f"response is not OSM XML: {snippet!r}")
    if b"<remark>" in data:
        text = data.decode("utf-8", "replace")
        start = text.find("<remark>")
        end = text.find("</remark>")
        remark = text[start + len("<remark>") : end].strip() if 0 <= start < end else "overpass remark"
        if any(word in remark.lower() for word in ("error", "runtime", "timeout")):
            raise OsmDownloadError(f"Overpass refused the query: {remark[:300]}")
    match = _OSM_BASE.search(head)
    return match.group(1).decode("ascii") if match else None


def download_map_osm(
    directory: str | Path,
    *,
    overwrite: bool = False,
    timeout: float = 120.0,
    fetch: Fetcher = _http_fetch,
    now: datetime | None = None,
) -> DownloadRecord:
    """Fetch ``data/raw/<id>.osm`` for the centre and radius stored in the map folder."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    target = osm_target(root)
    side = sidecar_path(target)
    if (target.exists() or side.exists()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite {target}")
    bbox = inclusion_bbox(root)
    query = overpass_query(bbox)
    retrieved = (now or datetime.now(tz=UTC)).isoformat(timespec="seconds")
    data = fetch(OVERPASS_URL, query, timeout)
    osm_base = _check_payload(data)

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
    center = document["center"]
    record = DownloadRecord(
        provider=OPENSTREETMAP.provider,
        license=OPENSTREETMAP.license,
        license_url=OPENSTREETMAP.license_url,
        attribution=OPENSTREETMAP.attribution,
        endpoint=OVERPASS_URL,
        query=query,
        bbox=[bbox[0], bbox[1], bbox[2], bbox[3]],
        bbox_order="south,west,north,east",
        center={"latitude": float(center["latitude"]), "longitude": float(center["longitude"])},
        radius_m=float(document["radius_m"]),
        margin_m=float(document["margin_m"]),
        crs=str(document["crs"]),
        retrieved_at=retrieved,
        osm_base=osm_base,
        sha256=sha256_file(target),
        size_bytes=target.stat().st_size,
        user_agent=USER_AGENT,
        path=str(target),
    )
    side.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record
