"""Acquisition of Catastro INSPIRE building parts (BU WFS). The only network code.

Downloads are explicit (CLI ``cadastre download``), bounded in area, written
atomically to a new file and described by a ``.source.json`` sidecar holding
the request, retrieval time, hash and licence. Nothing here is used by tests
that need the network.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onward_generator import __version__
from onward_generator.geo.sources import LicenseInfo, sha256_file
from onward_generator.net import fetch_bytes

WFS_BU_URL = "https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx"
WFS_HOSTS = frozenset({"ovc.catastro.meh.es"})
CRS = "EPSG:25830"
USER_AGENT = f"onward-generator/{__version__} (local research tool)"
MAX_AREA_KM2 = 1.0
MAX_BYTES = 64 * 1024 * 1024

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".source.json")


def validate_bbox(bbox: BBox, *, max_area_km2: float = MAX_AREA_KM2) -> BBox:
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    if not (xmin < xmax and ymin < ymax):
        raise CadastreError(f"invalid bbox {bbox}: min must be below max")
    if not (100_000 <= xmin <= 900_000 and 3_000_000 <= ymin <= 5_000_000):
        raise CadastreError(f"bbox {bbox} is not in projected EPSG:25830 metres (degrees given?)")
    area_km2 = (xmax - xmin) * (ymax - ymin) / 1e6
    if area_km2 > max_area_km2:
        raise CadastreError(f"bbox covers {area_km2:.2f} km²; limit is {max_area_km2} km²")
    return (xmin, ymin, xmax, ymax)


def building_parts_url(bbox: BBox) -> str:
    xmin, ymin, xmax, ymax = validate_bbox(bbox)
    query = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typenames": "bu:BuildingPart",
        "bbox": f"{xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}",
        "srsName": CRS,
    }
    return f"{WFS_BU_URL}?{urllib.parse.urlencode(query, safe=',:')}"


def bbox_from_scene(scene: Mapping[str, Any], margin_m: float) -> BBox:
    """Projected bbox of every building in a geo scene, grown by ``margin_m``."""
    frame = scene.get("frame") or {}
    if frame.get("crs") != CRS or frame.get("unit") != "m":
        raise CadastreError(f"scene frame must be {CRS} in metres")
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


def _http_fetch(url: str, timeout: float) -> bytes:
    return fetch_bytes(
        url,
        allowed_hosts=WFS_HOSTS,
        max_bytes=MAX_BYTES,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"},  # pragma: no mutate: only key case changes survive, urllib capitalizes header names; test_net_callers pins the rest
        error=CadastreError,
        error_body=True,
    )


def _check_payload(data: bytes) -> None:
    head = data[:4096]
    if b"<gml:FeatureCollection" not in head and b"FeatureCollection" not in head:
        snippet = head[:200].decode("latin-1", "replace")
        raise CadastreError(f"response is not a WFS FeatureCollection: {snippet!r}")
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise CadastreError("response contains DTD or entity declarations")


def download_building_parts(
    bbox: BBox,
    out_path: str | Path,
    *,
    overwrite: bool = False,
    timeout: float = 180.0,
    fetch: Fetcher = _http_fetch,
    now: datetime | None = None,
) -> DownloadRecord:
    """Download BuildingPart features in ``bbox`` to ``out_path`` (+ sidecar)."""
    target = Path(out_path).expanduser().resolve()
    if target.suffix.lower() != ".gml":
        raise CadastreError("output must be a .gml file")
    side = sidecar_path(target)
    if (target.exists() or side.exists()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite {target}")
    url = building_parts_url(bbox)
    retrieved = (now or datetime.now(tz=UTC)).isoformat(timespec="seconds")
    data = fetch(url, timeout)
    _check_payload(data)

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
        url=url,
        bbox=list(validate_bbox(bbox)),
        crs=CRS,
        retrieved_at=retrieved,
        sha256=sha256_file(target),
        size_bytes=target.stat().st_size,
        user_agent=USER_AGENT,
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
