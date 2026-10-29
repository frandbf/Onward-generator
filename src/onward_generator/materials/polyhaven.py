"""Official Poly Haven API client. No HTML scraping.

Downloads are opt-in, limited to Diffuse 1k JPEG, cached under assets/textures/.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from onward_generator import __version__
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.net import fetch_bytes

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = REPO_ROOT / "assets" / "textures"
USER_AGENT = f"onward-generator/{__version__} (local research tool)"
INFO_URL = "https://api.polyhaven.com/info/{id}"
FILES_URL = "https://api.polyhaven.com/files/{id}"
ALLOWED_PAGE_HOSTS = frozenset({"polyhaven.com", "www.polyhaven.com"})
ALLOWED_FETCH_HOSTS = frozenset({"api.polyhaven.com", "dl.polyhaven.org"})
ALLOWED_LICENSES = frozenset({"cc0", "cc0 1.0", "cc0-1.0"})
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 30.0

Fetcher = Callable[[str], bytes]


class DownloadError(ValueError):
    """The Poly Haven URL, licence, or file is not acceptable."""


@dataclass(frozen=True, slots=True)
class TextureRecord:
    provider: str
    asset_id: str
    license: str
    license_url: str
    authors: tuple[str, ...]
    page_url: str
    file_url: str
    md5: str
    sha256: str
    size_bytes: int
    map: str
    resolution: str
    format: str
    path: str
    retrieved_at: str
    user_agent: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_polyhaven_id(url: str) -> str:
    """``https://polyhaven.com/a/<id>?utm=…`` → ``<id>``. Rejects non-page hosts."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_PAGE_HOSTS:
        raise DownloadError(f"not a Poly Haven asset page: {url}")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "a":
        raise DownloadError(f"Poly Haven URL must look like /a/<id>: {url}")
    asset_id = parts[1]
    if not asset_id.replace("_", "").isalnum() or not asset_id[0].isalpha():
        raise DownloadError(f"invalid Poly Haven id in {url}")
    return asset_id


def sidecar_path(jpeg: Path) -> Path:
    return jpeg.with_name(jpeg.name + ".source.json")


def cached_jpeg(cache_dir: Path, asset_id: str) -> Path:
    return Path(cache_dir) / asset_id / f"{asset_id}_diff_1k.jpg"


def default_fetch(url: str, *, timeout_s: float = TIMEOUT_S) -> bytes:
    return fetch_bytes(
        url,
        allowed_hosts=ALLOWED_FETCH_HOSTS,
        max_bytes=MAX_BYTES,
        timeout=timeout_s,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,image/jpeg"},
        error=DownloadError,
    )


def _get_json(url: str, fetch: Fetcher) -> dict[str, Any]:
    raw = fetch(url)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f"{url}: not JSON") from exc
    if not isinstance(doc, dict):
        raise DownloadError(f"{url}: JSON object required")
    return doc


def _authors(info: Mapping[str, Any]) -> tuple[str, ...]:
    raw = info.get("authors") or {}
    if isinstance(raw, Mapping):
        return tuple(str(name) for name in raw)
    if isinstance(raw, list):
        return tuple(str(name) for name in raw)
    return ()


def _license_ok(value: object) -> bool:
    return str(value or "").strip().lower() in ALLOWED_LICENSES


def _license_from_info(info: Mapping[str, Any], asset_id: str) -> str:
    """Poly Haven's public site licence is CC0.

    The ``/info/<id>`` payload used to include ``license: "CC0"``. As of 2026
    that key is omitted (``None``). An explicit non-CC0 value is still refused.
    A missing value is accepted only because this client talks solely to
    ``api.polyhaven.com`` and the published policy is CC0.
    """
    raw = info.get("license")
    if raw is None or str(raw).strip() == "":
        return "CC0"
    if _license_ok(raw):
        return "CC0"
    raise DownloadError(f"{asset_id}: license {raw!r} is not CC0; refusing to download")


def _diffuse_1k_jpg(files: Mapping[str, Any]) -> tuple[str, str, int]:
    block = ((files.get("Diffuse") or {}).get("1k") or {}).get("jpg") or {}
    url = block.get("url")
    md5 = block.get("md5")
    size = int(block.get("size") or 0)
    if not url or not md5:
        raise DownloadError("Poly Haven files API has no Diffuse 1k jpg")
    return str(url), str(md5), size


def _load_sidecar(path: Path) -> dict[str, Any] | None:
    side = sidecar_path(path)
    if not path.is_file() or not side.is_file():
        return None
    try:
        return json.loads(side.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _record_from_sidecar(jpeg: Path, doc: Mapping[str, Any]) -> TextureRecord:
    return TextureRecord(
        provider=str(doc.get("provider") or "polyhaven"),
        asset_id=str(doc["asset_id"]),
        license=str(doc["license"]),
        license_url=str(doc.get("license_url") or "https://creativecommons.org/publicdomain/zero/1.0/"),
        authors=tuple(doc.get("authors") or ()),
        page_url=str(doc.get("page_url") or ""),
        file_url=str(doc.get("file_url") or ""),
        md5=str(doc["md5"]),
        sha256=str(doc["sha256"]),
        size_bytes=int(doc.get("size_bytes") or jpeg.stat().st_size),
        map=str(doc.get("map") or "Diffuse"),
        resolution=str(doc.get("resolution") or "1k"),
        format=str(doc.get("format") or "jpg"),
        path=str(jpeg),
        retrieved_at=str(doc.get("retrieved_at") or ""),
        user_agent=str(doc.get("user_agent") or USER_AGENT),
    )


def ensure_diffuse_1k(
    url: str,
    *,
    cache_dir: str | Path = DEFAULT_CACHE,
    download: bool = False,
    fetch: Fetcher | None = None,
) -> TextureRecord:
    """Resolve a page URL, require CC0, and return the cached 1k Diffuse JPEG.

    Without ``download``, a missing cache file is an error. The only network
    path is ``download=True`` (or a test ``fetch``).
    """
    asset_id = resolve_polyhaven_id(url)
    jpeg = cached_jpeg(Path(cache_dir), asset_id)
    sidecar = _load_sidecar(jpeg)
    if sidecar and jpeg.is_file():
        raw = jpeg.read_bytes()
        digest = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        sha = hashlib.sha256(raw).hexdigest()
        if digest != sidecar.get("md5"):
            raise DownloadError(f"{jpeg}: md5 does not match sidecar")
        if not _license_ok(sidecar.get("license")):
            raise DownloadError(f"{jpeg}: cached license is not CC0")
        if sidecar.get("sha256") and sidecar["sha256"] != sha:
            raise DownloadError(f"{jpeg}: sha256 does not match sidecar")
        return _record_from_sidecar(jpeg, sidecar)

    if not download:
        raise DownloadError(
            f"texture {asset_id} is not cached at {jpeg}; pass --download to fetch Diffuse 1k jpg"
        )

    getter = fetch or default_fetch
    info = _get_json(INFO_URL.format(id=asset_id), getter)
    license_name = _license_from_info(info, asset_id)
    files = _get_json(FILES_URL.format(id=asset_id), getter)
    file_url, md5, size = _diffuse_1k_jpg(files)
    payload = getter(file_url)
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    if digest != md5:
        raise DownloadError(f"{asset_id}: md5 mismatch (api {md5}, file {digest})")
    if size and len(payload) != size:
        raise DownloadError(f"{asset_id}: size mismatch (api {size}, file {len(payload)})")
    jpeg.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(jpeg, payload)
    record = TextureRecord(
        provider="polyhaven",
        asset_id=asset_id,
        license=license_name,
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        authors=_authors(info),
        page_url=f"https://polyhaven.com/a/{asset_id}",
        file_url=file_url,
        md5=md5,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        map="Diffuse",
        resolution="1k",
        format="jpg",
        path=str(jpeg),
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        user_agent=USER_AGENT,
    )
    atomic_write_json(sidecar_path(jpeg), record.to_dict())
    return record
