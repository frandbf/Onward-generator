"""Download Panoramax pictures once and share them across maps. Under development.

The HTTP client is injected. Tests pass a fake and do not open a socket.
Only pictures whose field of view is 360 degrees are stored. A flat picture
is recorded as ``skipped: flat`` and its JPEG is not requested. The JPEG and
its sidecar live in the repository catalogue ``cache/panoramax/``, keyed by
picture id. Each map keeps only the search index under ``data/raw/panoramax/``.
A file whose SHA-256 still matches its sidecar is not downloaded again. A
copy left inside a map is moved into the catalogue instead of fetched.
Missing JPEGs are fetched on up to ``DOWNLOAD_WORKERS`` threads and written
in search order.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from onward_generator import __version__
from onward_generator.geo.sources import sha256_file
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.maps.workspace import MapError, load_map
from onward_generator.osm.acquire import OsmDownloadError, inclusion_bbox

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = REPO_ROOT / "cache" / "panoramax"
META_SEARCH = "https://api.panoramax.xyz/api/search"
USER_AGENT = f"onward-generator/{__version__} (local map generator)"
PAGE_LIMIT = 100
MAX_PAGES = 20
LICENSE_URL = "https://spdx.org/licenses/CC-BY-SA-4.0.html"
# Picture downloads open at the same time. Pictures sit on volunteer-run
# instances; four stays below a browser's six connections per host.
DOWNLOAD_WORKERS = 4


class PanoramaxError(ValueError):
    """The picture cache cannot be written."""


class PanoramaxTransport(Protocol):
    def get_json(self, url: str) -> Mapping[str, Any]:
        """Return one JSON object. Raise ``PanoramaxError`` when the request fails."""

    def get_bytes(self, url: str) -> bytes:
        """Return the response body. Raise ``PanoramaxError`` when the request fails.

        Several threads call this at once, so it must be thread-safe.
        """


@dataclass(frozen=True, slots=True)
class CacheReport:
    kept: int
    skipped_flat: int
    reused: int
    directory: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "onward_generator.panoramax_cache",
            "schema_version": "0.1.0",
            "kept": self.kept,
            "skipped_flat": self.skipped_flat,
            "reused": self.reused,
            "directory": str(self.directory),
        }


class UrllibTransport:
    """The live client. Tests do not construct it."""

    def __init__(self, timeout_s: float = 60.0) -> None:
        self._timeout_s = timeout_s

    def get_json(self, url: str) -> Mapping[str, Any]:
        body = self.get_bytes(url)
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PanoramaxError(f"Panoramax did not return JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise PanoramaxError("Panoramax JSON must be an object")
        return data

    def get_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json, image/jpeg"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise PanoramaxError(f"Panoramax request failed: {exc}") from exc


def catalogue_directory() -> Path:
    """Shared JPEG catalogue. One picture id, one file, every map."""
    return CATALOGUE


def cache_directory(map_directory: str | Path) -> Path:
    """Per-map search index. The JPEGs are not stored here."""
    root = Path(map_directory).expanduser().resolve()
    try:
        document = load_map(root)
    except MapError as exc:
        raise PanoramaxError(str(exc)) from exc
    raw = document.get("paths", {}).get("raw", "data/raw")
    return root / raw / "panoramax"


def adopt_local_pictures(map_directory: str | Path) -> int:
    """Move hash-valid JPEGs out of one map index into the shared catalogue."""
    index = cache_directory(map_directory)
    if not index.is_dir():
        return 0
    moved = 0
    for jpeg in sorted(index.glob("*.jpg")):
        if _take_local(index, jpeg.stem):
            moved += 1
    return moved


def cache_pictures(map_directory: str | Path, *, client: PanoramaxTransport) -> CacheReport:
    """Search the map square. Store each 360 picture in the shared catalogue."""
    root = Path(map_directory).expanduser().resolve()
    try:
        south, west, north, east = inclusion_bbox(root)
    except OsmDownloadError as exc:
        raise PanoramaxError(str(exc)) from exc
    index = cache_directory(root)
    adopt_local_pictures(root)
    features = _search(client, west=west, south=south, east=east, north=north)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    missing: list[tuple[dict[str, Any], str]] = []
    reused = 0
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for feature in features:
        picture_id = str(feature.get("id") or "")
        if not picture_id:
            skipped.append({"id": "", "reason": "missing_id"})
            continue
        if not _is_sphere(feature):
            skipped.append({"id": picture_id, "reason": "flat"})
            continue
        asset_url = _asset_url(feature)
        if asset_url is None:
            skipped.append({"id": picture_id, "reason": "missing_asset"})
            continue
        jpeg_path, sidecar = _catalogue_paths(picture_id)
        kept.append(feature)
        if _hash_matches(jpeg_path, sidecar) or _take_local(index, picture_id):
            reused += 1
            continue
        missing.append((feature, asset_url))
    _download(client, missing, retrieved_at=now)
    index.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        index / "search.geojson",
        {"type": "FeatureCollection", "features": kept},
    )
    atomic_write_json(
        index / "cache_report.json",
        {
            "schema": "onward_generator.panoramax_cache",
            "schema_version": "0.1.0",
            "bbox": [west, south, east, north],
            "bbox_order": "west,south,east,north",
            "kept": [str(feature.get("id")) for feature in kept],
            "skipped": skipped,
            "reused": reused,
        },
    )
    return CacheReport(
        kept=len(kept),
        skipped_flat=sum(1 for row in skipped if row["reason"] == "flat"),
        reused=reused,
        directory=catalogue_directory(),
    )


def _download(client: PanoramaxTransport, missing: list[tuple[dict[str, Any], str]], *, retrieved_at: str) -> None:
    """Fetch the missing JPEGs on a few threads and store them in search order.

    At most ``DOWNLOAD_WORKERS`` requests are open at once. Each file is
    written by this thread, in the order of ``missing``, so the catalogue does
    not depend on which download finishes first. The first failure in that
    order is raised. Downloads not yet started are cancelled, and no file after
    the failed one is written.
    """
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        jobs = [(feature, url, pool.submit(_fetch, client, feature, url)) for feature, url in missing]
        try:
            for feature, url, future in jobs:
                _store(feature, url, future.result(), retrieved_at=retrieved_at)
        except BaseException:
            for _feature, _url, pending in jobs:
                pending.cancel()
            raise


def _fetch(client: PanoramaxTransport, feature: Mapping[str, Any], url: str) -> bytes:
    try:
        return client.get_bytes(url)
    except PanoramaxError:
        raise
    except OSError as exc:
        raise PanoramaxError(f"could not download {feature.get('id')}: {exc}") from exc


def _store(feature: Mapping[str, Any], url: str, payload: bytes, *, retrieved_at: str) -> None:
    jpeg_path, sidecar = _catalogue_paths(str(feature.get("id")))
    jpeg_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(jpeg_path, payload)
    digest = sha256_file(jpeg_path)
    record = _source_record(feature, url=url, sha256=digest, retrieved_at=retrieved_at, size_bytes=len(payload))
    atomic_write_json(sidecar, record)


def _search(
    client: PanoramaxTransport,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "limit": str(PAGE_LIMIT),
        "bbox": f"{west:.7f},{south:.7f},{east:.7f},{north:.7f}",
    })
    url: str | None = f"{META_SEARCH}?{query}"
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _page in range(MAX_PAGES):
        if url is None:
            break
        try:
            document = client.get_json(url)
        except PanoramaxError:
            raise
        except OSError as exc:
            raise PanoramaxError(f"Panoramax search failed: {exc}") from exc
        features = document.get("features")
        if not isinstance(features, list):
            raise PanoramaxError("Panoramax search did not return a feature list")
        for feature in features:
            if not isinstance(feature, dict):
                continue
            picture_id = str(feature.get("id") or "")
            if picture_id and picture_id in seen:
                continue
            if picture_id:
                seen.add(picture_id)
            found.append(feature)
        url = _next_link(document.get("links"))
    return found


def _next_link(links: object) -> str | None:
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") == "next" and link.get("href"):
            return str(link["href"])
    return None


def _is_sphere(feature: Mapping[str, Any]) -> bool:
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        return False
    interior = properties.get("pers:interior_orientation")
    if not isinstance(interior, Mapping):
        return False
    return interior.get("field_of_view") == 360


def _asset_url(feature: Mapping[str, Any]) -> str | None:
    assets = feature.get("assets")
    if not isinstance(assets, Mapping):
        return None
    for name in ("sd", "hd"):
        asset = assets.get(name)
        if isinstance(asset, Mapping) and asset.get("href"):
            return str(asset["href"])
    return None


def _catalogue_paths(picture_id: str) -> tuple[Path, Path]:
    root = catalogue_directory()
    return root / f"{picture_id}.jpg", root / f"{picture_id}.source.json"


def _take_local(index: Path, picture_id: str) -> bool:
    """Copy one valid map JPEG into the catalogue, then remove the map copy."""
    local_jpeg = index / f"{picture_id}.jpg"
    local_side = index / f"{picture_id}.source.json"
    if not _hash_matches(local_jpeg, local_side):
        return False
    dest_jpeg, dest_side = _catalogue_paths(picture_id)
    if _hash_matches(dest_jpeg, dest_side):
        if sha256_file(local_jpeg) == sha256_file(dest_jpeg):
            local_jpeg.unlink()
            local_side.unlink()
        return True
    dest_jpeg.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest_jpeg, local_jpeg.read_bytes())
    recorded = json.loads(local_side.read_text(encoding="utf-8"))
    if not isinstance(recorded, dict):
        return False
    atomic_write_json(dest_side, recorded)
    if not _hash_matches(dest_jpeg, dest_side):
        return False
    local_jpeg.unlink()
    local_side.unlink()
    return True


def _hash_matches(jpeg_path: Path, sidecar: Path) -> bool:
    if not jpeg_path.is_file() or not sidecar.is_file():
        return False
    try:
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(recorded, dict):
        return False
    digest = recorded.get("sha256")
    if not isinstance(digest, str) or not digest:
        return False
    return sha256_file(jpeg_path) == digest


def _author(feature: Mapping[str, Any]) -> str:
    properties = feature.get("properties")
    if isinstance(properties, Mapping):
        exif = properties.get("exif")
        if isinstance(exif, Mapping) and exif.get("Exif.Image.Artist"):
            return str(exif["Exif.Image.Artist"])
        producer = properties.get("geovisio:producer")
        if producer:
            return str(producer)
    providers = feature.get("providers")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, Mapping) and "producer" in (provider.get("roles") or []):
                name = provider.get("name")
                if name:
                    return str(name)
    return ""


def _license(feature: Mapping[str, Any]) -> tuple[str, str]:
    properties = feature.get("properties")
    name = "CC-BY-SA-4.0"
    url = LICENSE_URL
    if isinstance(properties, Mapping) and properties.get("license"):
        name = str(properties["license"])
    links = feature.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, Mapping) and link.get("rel") == "license" and link.get("href"):
                url = str(link["href"])
    return name, url


def _source_record(
    feature: Mapping[str, Any],
    *,
    url: str,
    sha256: str,
    retrieved_at: str,
    size_bytes: int,
) -> dict[str, Any]:
    license_name, license_url = _license(feature)
    author = _author(feature)
    return {
        "provider": "panoramax",
        "license": license_name,
        "license_url": license_url,
        "attribution": author or "Panoramax contributors",
        "url": url,
        "author": author,
        "picture_id": str(feature.get("id") or ""),
        "sha256": sha256,
        "size_bytes": size_bytes,
        "retrieved_at": retrieved_at,
        "field_of_view": 360,
        "view_azimuth": float((feature.get("properties") or {}).get("view:azimuth") or 0.0),
    }
