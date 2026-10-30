"""Download named Poly Haven models and write albedo-only GLBs.

Originals stay under the raw directory. The library directory receives the
normalized meshes and a manifest. Category index pages are not downloads.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from pathlib import Path

from onward_generator import __version__
from onward_generator.net import fetch_bytes
from onward_generator.overgrow.catalog import ALL_ASSETS, WEAR_NOTE
from onward_generator.overgrow.manifest import keep_semantics
from onward_generator.overgrow.normalize import gltf_to_local, normalize_arrays, write_normalized_glb

INFO_URL = "https://api.polyhaven.com/info/{id}"
FILES_URL = "https://api.polyhaven.com/files/{id}"
LICENSE_URL = "https://polyhaven.com/license"
PAGE_URL = "https://polyhaven.com/a/{id}"
USER_AGENT = f"onward-generator/{__version__} (local research tool)"
ALLOWED = frozenset({"api.polyhaven.com", "dl.polyhaven.org", "polyhaven.com", "www.polyhaven.com"})
MAX_BYTES = 260 * 1024 * 1024
TIMEOUT_S = 180.0
CC0 = frozenset({"cc0", "cc0 1.0", "cc0-1.0"})
VERIFIED_ON = "2026-10-19"


class FetchError(ValueError):
    """A download cannot be saved."""


Getter = Callable[[str], tuple[int, bytes]]
"""``url -> (http_status, body)``. The default is ``_get``; tests pass a fake."""


def fetch_library(
    raw_dir: str | Path,
    library_dir: str | Path,
    inventory_path: str | Path,
    *,
    get: Getter | None = None,
) -> dict:
    raw_root = Path(raw_dir).expanduser().resolve()
    library = Path(library_dir).expanduser().resolve()
    inventory = Path(inventory_path).expanduser().resolve()
    raw_root.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)
    get = get or _get
    license_status, license_body = get(LICENSE_URL)
    if license_status != 200 or b"CC0" not in license_body:
        raise FetchError(f"Poly Haven licence page HTTP {license_status}; refusing the batch")
    rows = []
    manifest_assets = []
    for spec in ALL_ASSETS:
        try:
            row, prefab = _one(spec, raw_root, library, get)
        except (FetchError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            row, prefab = {
                "name": spec.id,
                "source_url": PAGE_URL.format(id=spec.id),
                "category": spec.category,
                "intended_use": spec.use,
                "verification_date": VERIFIED_ON,
                "import_status": "failed",
                "error": str(exc),
                "placed": False,
            }, None
        rows.append(row)
        if prefab is not None:
            manifest_assets.append(prefab)
    document = {
        "schema": "onward_generator.abandoned_inventory",
        "schema_version": "0.1.0",
        "verified_on": VERIFIED_ON,
        "license_page": {"url": LICENSE_URL, "http_status": license_status, "mentions_cc0": True},
        "wear_materials": WEAR_NOTE,
        "catalog_indexes_not_downloaded": [
            "https://polyhaven.com/models/nature",
            "https://polyhaven.com/models/plants",
            "https://polyhaven.com/models/trees",
        ],
        "catalog_index_reason": (
            "Those URLs are category indexes, not files. Vegetation is the small "
            "checked set in this inventory (a few plants, one bush, one tree). "
            "No Poly Haven model id matched ivy or vine."
        ),
        "sketchfab_excluded": {
            "id": "abandoned-euro-car-raw-scan",
            "reason": "licence was not verified; the model was not downloaded",
        },
        "assets": rows,
    }
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    target = library / "manifest.json"
    previous = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None
    manifest = keep_semantics({
        "schema": "onward_generator.abandoned_prefabs",
        "schema_version": "0.1.0",
        "unit": "m",
        "collider": "none",
        "shader": "PP_Opaque_Simple_Lit albedo only",
        "assets": manifest_assets,
    }, previous)
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return document


def _one(spec, raw_root: Path, library: Path, get: Getter) -> tuple[dict, dict | None]:
    folder = raw_root / spec.id
    folder.mkdir(parents=True, exist_ok=True)
    if _local_ready(folder, spec.id):
        return _from_local(spec, folder, library)
    info_status, info_body = get(INFO_URL.format(id=spec.id))
    files_status, files_body = get(FILES_URL.format(id=spec.id))
    base = {
        "name": spec.id,
        "source_url": PAGE_URL.format(id=spec.id),
        "api_info_url": INFO_URL.format(id=spec.id),
        "api_files_url": FILES_URL.format(id=spec.id),
        "http_status": {"info": info_status, "files": files_status},
        "category": spec.category,
        "role": spec.role,
        "intended_use": spec.use,
        "verification_date": VERIFIED_ON,
        "format": "glTF 1k, normalized GLB",
        "lod": "single",
        "collider": "none",
        "import_status": "refused",
        "placed": False,
    }
    if info_status != 200 or files_status != 200:
        base["license"] = None
        base["license_evidence"] = f"info HTTP {info_status}, files HTTP {files_status}"
        return base, None
    info = json.loads(info_body.decode("utf-8"))
    files = json.loads(files_body.decode("utf-8"))
    (folder / "info.json").write_text(json.dumps(info), encoding="utf-8")
    (folder / "files.json").write_text(json.dumps(files), encoding="utf-8")
    license_name, evidence, ok = _license(info, spec.id)
    base["license"] = license_name
    base["license_evidence"] = evidence
    base["authors"] = list((info.get("authors") or {}).keys()) if isinstance(info.get("authors"), dict) else []
    base["polycount_before"] = info.get("polycount")
    base["api_dimensions_mm"] = info.get("dimensions")
    if not ok:
        return base, None
    entry = ((files.get("gltf") or {}).get("1k") or {}).get("gltf") or {}
    if not entry.get("url"):
        base["import_status"] = "no_gltf_1k"
        return base, None
    gltf_status, gltf_body = get(str(entry["url"]))
    base["http_status"]["gltf"] = gltf_status
    if gltf_status != 200:
        base["import_status"] = "gltf_failed"
        return base, None
    gltf_path = folder / f"{spec.id}.gltf"
    gltf_path.write_bytes(gltf_body)
    doc = json.loads(gltf_body.decode("utf-8"))
    include = entry.get("include") or {}
    bin_status = _save_uri(folder, include, _buffer_uri(doc), "bin", base, get)
    albedo_uri = _albedo_uri(doc)
    albedo_status = _save_uri(folder, include, albedo_uri, "albedo", base, get)
    for name in include:
        if "_diff_" in name and name != albedo_uri:
            _save_uri(folder, include, name, "albedo_extra", base, get)
    if bin_status != 200 or albedo_status != 200:
        base["import_status"] = "missing_bin_or_albedo"
        return base, None
    vertices, triangles, uvs, albedo = gltf_to_local(folder)
    normalized = normalize_arrays(
        vertices, triangles, uvs, albedo,
        anchor=spec.anchor, api_dimensions_mm=info.get("dimensions"),
    )
    if normalized.triangles_after <= 0:
        base["import_status"] = "empty_mesh"
        return base, None
    write_normalized_glb(library / f"{spec.id}.glb", spec.id, normalized)
    base.update({
        "import_status": "normalized",
        "format": "glTF 1k source, GLB albedo-only output",
        "scale": normalized.scale_note,
        "orientation": normalized.orientation,
        "pivot": "xz centre, minimum z on the ground",
        "polycount_before": int(info.get("polycount") or normalized.triangles_before),
        "polycount_measured_before": normalized.triangles_before,
        "polycount_after": normalized.triangles_after,
        "textures_kept": [f"baseColor {512}px RGB, alpha flattened"],
        "textures_dropped": ["normal", "roughness", "metallic", "ao", "arm"],
        "lod": "single shared mesh",
        "length_m": round(normalized.length_m, 3),
        "depth_m": round(normalized.depth_m, 3),
        "height_m": round(normalized.height_m, 3),
        "normalized_file": f"{spec.id}.glb",
    })
    prefab = {
        "id": spec.id,
        "file": f"{spec.id}.glb",
        "category": spec.category,
        "role": spec.role,
        "surfaces": list(spec.surfaces),
        "anchor": spec.anchor,
        "lane": spec.lane,
        "clearance_m": spec.clearance_m,
        "use": spec.use,
        "triangles": normalized.triangles_after,
        "length_m": round(normalized.length_m, 3),
        "depth_m": round(normalized.depth_m, 3),
        "height_m": round(normalized.height_m, 3),
        "collider": "none",
    }
    return base, prefab


def _local_ready(folder: Path, asset_id: str) -> bool:
    gltf_path = folder / f"{asset_id}.gltf"
    info_path = folder / "info.json"
    if not gltf_path.is_file() or not info_path.is_file():
        return False
    try:
        doc = json.loads(gltf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    bin_uri = _buffer_uri(doc)
    albedo_uri = _albedo_uri(doc)
    return bool(bin_uri and albedo_uri and (folder / bin_uri).is_file() and (folder / albedo_uri).is_file())


def _from_local(spec, folder: Path, library: Path) -> tuple[dict, dict | None]:
    info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    license_name, evidence, ok = _license(info, spec.id)
    base = {
        "name": spec.id,
        "source_url": PAGE_URL.format(id=spec.id),
        "api_info_url": INFO_URL.format(id=spec.id),
        "api_files_url": FILES_URL.format(id=spec.id),
        "http_status": {"info": 200, "files": 200, "gltf": 200, "bin": 200, "albedo": 200},
        "category": spec.category,
        "role": spec.role,
        "intended_use": spec.use,
        "verification_date": VERIFIED_ON,
        "license": license_name,
        "license_evidence": evidence + " Reused the download already on disk.",
        "authors": list((info.get("authors") or {}).keys()) if isinstance(info.get("authors"), dict) else [],
        "polycount_before": info.get("polycount"),
        "api_dimensions_mm": info.get("dimensions"),
        "collider": "none",
        "import_status": "refused",
        "placed": False,
    }
    if not ok:
        return base, None
    vertices, triangles, uvs, albedo = gltf_to_local(folder)
    normalized = normalize_arrays(
        vertices, triangles, uvs, albedo,
        anchor=spec.anchor, api_dimensions_mm=info.get("dimensions"),
    )
    if normalized.triangles_after <= 0:
        base["import_status"] = "empty_mesh"
        return base, None
    write_normalized_glb(library / f"{spec.id}.glb", spec.id, normalized)
    base.update(_normalized_fields(normalized, info))
    base["normalized_file"] = f"{spec.id}.glb"
    return base, _prefab(spec, normalized)


def _normalized_fields(normalized, info: dict) -> dict:
    return {
        "import_status": "normalized",
        "format": "glTF 1k source, GLB albedo-only output",
        "scale": normalized.scale_note,
        "orientation": normalized.orientation,
        "pivot": "xz centre, minimum z on the ground",
        "polycount_before": int(info.get("polycount") or normalized.triangles_before),
        "polycount_measured_before": normalized.triangles_before,
        "polycount_after": normalized.triangles_after,
        "textures_kept": ["baseColor 512px RGB, alpha flattened"],
        "textures_dropped": ["normal", "roughness", "metallic", "ao", "arm"],
        "lod": "single shared mesh",
        "length_m": round(normalized.length_m, 3),
        "depth_m": round(normalized.depth_m, 3),
        "height_m": round(normalized.height_m, 3),
        "normalized_file": None,
    }


def _prefab(spec, normalized) -> dict:
    return {
        "id": spec.id,
        "file": f"{spec.id}.glb",
        "category": spec.category,
        "role": spec.role,
        "surfaces": list(spec.surfaces),
        "anchor": spec.anchor,
        "lane": spec.lane,
        "clearance_m": spec.clearance_m,
        "use": spec.use,
        "triangles": normalized.triangles_after,
        "length_m": round(normalized.length_m, 3),
        "depth_m": round(normalized.depth_m, 3),
        "height_m": round(normalized.height_m, 3),
        "collider": "none",
    }


def _license(info: dict, asset_id: str) -> tuple[str | None, str, bool]:
    raw = info.get("license")
    if raw is None or str(raw).strip() == "":
        return "CC0", (
            f"{asset_id}: /info license field is omitted. "
            f"{LICENSE_URL} returned CC0 on {VERIFIED_ON}. An explicit non-CC0 value is refused."
        ), True
    if str(raw).strip().lower() in CC0:
        return "CC0", f"{asset_id}: /info license {raw!r}", True
    return str(raw), f"{asset_id}: license {raw!r} is not CC0", False


def _buffer_uri(doc: dict) -> str | None:
    buffers = doc.get("buffers") or [{}]
    return buffers[0].get("uri")


def _albedo_uri(doc: dict) -> str | None:
    textures = doc.get("textures") or []
    images = doc.get("images") or []
    for material in doc.get("materials") or []:
        texture = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if not texture:
            continue
        source = textures[int(texture["index"])].get("source")
        return images[int(source)].get("uri")
    return None


def _save_uri(folder: Path, include: dict, uri: str | None, label: str, row: dict, get: Getter) -> int:
    if not uri:
        row["http_status"][label] = 0
        return 0
    meta = include.get(uri) or next((value for key, value in include.items() if key.endswith(uri) or uri.endswith(key)), None)
    if not isinstance(meta, dict) or not meta.get("url"):
        row["http_status"][label] = 0
        return 0
    status, body = get(str(meta["url"]))
    row["http_status"][label] = status
    if status == 200:
        target = folder / uri
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return status


def _get(url: str) -> tuple[int, bytes]:
    """``(200, body)``, or ``(status, b"")`` for an HTTP error. Other failures raise ``FetchError``."""
    try:
        return 200, fetch_bytes(
            url,
            allowed_hosts=ALLOWED,
            max_bytes=MAX_BYTES,
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            error=FetchError,
        )
    except FetchError as exc:
        if isinstance(exc.__cause__, urllib.error.HTTPError):
            return int(exc.__cause__.code), b""
        raise
