"""Download CC-BY Sketchfab models that the review already marked usable.

The request goes through the Sketchfab Download API with the personal API
token in ``SKETCHFAB_API_TOKEN``. The token is not stored and is sent only to
``api.sketchfab.com``, never to the signed storage URL the API hands out.
Models the review marks unusable are never requested.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from onward_generator import __version__
from onward_generator.net import fetch_bytes
from onward_generator.overgrow.library_catalog import REVIEW_FILE
from onward_generator.overgrow.manifest import keep_semantics
from onward_generator.overgrow.normalize import gltf_to_local, materialize_images, normalize_arrays, write_normalized_glb

MAX_BYTES = 260 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
TOKEN_ENV = "SKETCHFAB_API_TOKEN"
API_HOSTS = frozenset({"api.sketchfab.com"})
DOWNLOAD_API = "https://api.sketchfab.com/v3/models/{uid}/download"
USER_AGENT = f"onward-generator/{__version__}"
SPECS: dict[str, dict] = {
    "97e3bdfe47ea40f49351debaa11cd1e2": {"id": "mustang_boss_429", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "abandoned muscle car at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "68f715c39c07464db59e099bb8dc2d4e": {"id": "rusty_abandoned_truck", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "rusty truck at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "d5307c411b2246d69d9b29c5880223ec": {"id": "abandoned_auto", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "abandoned car at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "efc4dfe2c7284a64bc9281eb4f61d402": {"id": "destroyed_car_03", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "destroyed sedan at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "ad16f7085cb047b494997cf9efed5059": {"id": "burned_police_cars", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "burned police car at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "db2df46c49f9441286c84c66208ef7ac": {"id": "abandoned_sedan_2", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "abandoned sedan at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "66ef51a84c9843dda53bf0b4b9020011": {"id": "crashed_abandoned_car", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "crashed car at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "6a2169dafc254f399387a679305bb1bf": {"id": "abandoned_sedan_1", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "abandoned sedan at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"},
    "36380c606bae46848237e9bbe073fa3b": {"id": "building_container_metal", "category": "structures", "role": "building", "surfaces": ["block_edge", "curb", "yard", "empty_lot", "plaza", "park"], "anchor": "ground", "use": "metal container building at a block edge, plaza, lot or curb"},
    "1ad46b04f23d43dbb2730c9435c1a7cc": {"id": "crashed_helicopter", "category": "vehicles", "role": "wreck", "surfaces": ["curb", "carriageway", "courtyard", "yard", "empty_lot", "plaza", "park"], "anchor": "ground", "use": "crashed helicopter in a street, plaza, park, yard or lot"},
    "642f7e533f3242bb9f43addea71efbc4": {"id": "forest_hut", "category": "structures", "role": "building", "surfaces": ["block_edge", "curb", "yard", "empty_lot", "plaza", "park"], "anchor": "ground", "use": "forest hut at a block edge, plaza, lot or curb"},
    "240baad0fd7b4924a5705950be7adee6": {"id": "gas_tanker", "category": "structures", "role": "tank", "surfaces": ["block_edge", "yard", "empty_lot", "plaza"], "anchor": "ground", "use": "bulk gas tank in a yard, lot or plaza"},
    "a624f8c74f2b4774b4436aaf093c1e01": {"id": "fire_escape_sf", "category": "facade", "role": "fire_escape", "surfaces": ["facade"], "anchor": "facade", "use": "fire escape against a wall"},
    "a1959927a98e4752bdb01b6e1f7f7d26": {"id": "abandoned_garage", "category": "structures", "role": "building", "surfaces": ["block_edge", "curb", "yard", "empty_lot", "plaza", "park"], "anchor": "ground", "use": "abandoned garage at a block edge, plaza, lot or curb"},
    "bda39e79dbfa4976b1791b6b1f58259c": {"id": "watchtower", "category": "structures", "role": "building", "surfaces": ["block_edge", "curb", "yard", "empty_lot", "plaza", "park"], "anchor": "ground", "use": "watchtower at a block edge, plaza, lot or curb"},
    "af50e8d36c7e481c8ddc375ce751c1d6": {"id": "road_closed_barrier", "category": "barriers", "role": "barrier", "surfaces": ["curb", "carriageway", "sidewalk", "plaza"], "anchor": "ground", "use": "road-closed barrier at the curb, across the street, on a sidewalk or in a plaza"},
    "f61862267fdf489386aeadae20754229": {"id": "barrier_low", "category": "barriers", "role": "barrier", "surfaces": ["curb", "carriageway", "sidewalk", "plaza"], "anchor": "ground", "use": "low barrier at the curb, across the street, on a sidewalk or in a plaza"},
    "82227d60e9484b958feb856a2c8f4d33": {"id": "fence_door_metal", "category": "structures", "role": "fence", "surfaces": ["block_edge", "carriageway"], "anchor": "ground", "use": "metal fence door at a block edge or across a street"},
    "5805213c2b54454fb22d8176c8741210": {"id": "warning_signboard", "category": "barriers", "role": "barrier", "surfaces": ["curb", "carriageway", "sidewalk", "plaza"], "anchor": "ground", "use": "warning sign at the curb, across the street, on a sidewalk or in a plaza"},
    "d0fbc8fecfbb40bf9029bc97b6e9e122": {"id": "scanned_plants_grass", "category": "vegetation", "role": "plant", "surfaces": ["sidewalk", "yard", "empty_lot"], "anchor": "ground", "use": "scanned plants and grass off the drive lane"},
    "68c9e77971874643bcef67dc1987a2b7": {"id": "jungle_ground_roots", "category": "vegetation", "role": "plant", "surfaces": ["yard", "empty_lot"], "anchor": "ground", "use": "rooted ground patch in a yard"},
    "3781a00ddb6146ceb51144d00349a240": {"id": "jungle_ground_water", "category": "vegetation", "role": "plant", "surfaces": ["yard", "empty_lot"], "anchor": "ground", "use": "wet ground patch in a yard"},
    "1ce5fa26a4ce49d6b170988dd4bc5ee2": {"id": "jungle_ground_tile", "category": "vegetation", "role": "plant", "surfaces": ["yard", "empty_lot"], "anchor": "ground", "use": "tileable jungle ground in a yard"},
    "73201f05280d48dcb10ed4ca362d69f8": {"id": "tree_low_poly", "category": "vegetation", "role": "tree", "surfaces": ["park", "plaza", "sidewalk", "courtyard", "yard", "empty_lot"], "anchor": "ground", "use": "low poly tree in a park, plaza, sidewalk, yard or lot", "clearance_m": 3.5},
}


class SketchfabError(ValueError):
    """A reviewed model could not be saved."""


def api_token(env: Mapping[str, str] | None = None) -> str:
    """The personal API token from ``SKETCHFAB_API_TOKEN``; ``env`` defaults to ``os.environ``."""
    environ = os.environ if env is None else env
    token = (environ.get(TOKEN_ENV) or "").strip()
    if not token:
        raise SketchfabError(f"set {TOKEN_ENV} to the API token from sketchfab.com, Settings > Password & API")
    return token


def fetch_sketchfab(raw_dir: str | Path, library_dir: str | Path, review_path: str | Path | None = None) -> dict:
    raw_root = Path(raw_dir).expanduser().resolve()
    library = Path(library_dir).expanduser().resolve()
    review_file = Path(review_path).expanduser() if review_path else REVIEW_FILE
    raw_root.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)
    document = json.loads(review_file.read_text(encoding="utf-8"))
    token = api_token()
    results = []
    for row in document.get("assets") or []:
        if row.get("usable") is not True:
            continue
        uid = str(row["uid"])
        spec = SPECS.get(uid)
        if spec is None:
            row["installed"] = False
            row["error"] = "usable model has no library id"
            line = f"FAIL {row.get('name')}: no library id"
            print(line, flush=True)
            results.append(line)
            continue
        glb = library / f"{spec['id']}.glb"
        if row.get("installed") is True and glb.is_file():
            line = f"SKIP {spec['id']}"
            print(line, flush=True)
            results.append(line)
            continue
        time.sleep(4)
        try:
            entry = _one(uid, spec, row, raw_root, library, token)
        except (SketchfabError, OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            row["installed"] = False
            row["id"] = spec["id"]
            row["error"] = str(exc)
            line = f"FAIL {spec['id']}: {exc}"
            print(line, flush=True)
            results.append(line)
            continue
        row.update(entry)
        line = f"OK {spec['id']} tris {entry['triangles_after']}"
        print(line, flush=True)
        results.append(line)
        review_file.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    review_file.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"results": results}


def _one(uid: str, spec: dict, row: dict, raw_root: Path, library: Path, token: str) -> dict:
    folder = raw_root / spec["id"]
    existing = next(folder.rglob("*.gltf"), None) if folder.exists() else None
    gltf_dir = existing.parent if existing is not None else _prepare_gltf(_download(uid, folder, token))
    if "KHR_draco_mesh_compression" in json.loads(next(gltf_dir.glob("*.gltf")).read_text(encoding="utf-8")).get("extensionsRequired", []):
        raise SketchfabError("glTF requires Draco; not imported")
    materialize_images(gltf_dir)
    vertices, triangles, uvs, albedo = gltf_to_local(gltf_dir)
    normalized = normalize_arrays(vertices, triangles, uvs, albedo, anchor=spec["anchor"], api_dimensions_mm=None)
    if normalized.mesh.triangle_count == 0:
        raise SketchfabError("normalized mesh has no triangles")
    write_normalized_glb(library / f"{spec['id']}.glb", spec["id"], normalized)
    _append_manifest(library, spec, row, normalized)
    return {
        "id": spec["id"],
        "installed": True,
        "normalized_file": f"{spec['id']}.glb",
        "triangles_before": normalized.triangles_before,
        "triangles_after": normalized.triangles_after,
        "length_m": round(normalized.length_m, 3),
        "depth_m": round(normalized.depth_m, 3),
        "height_m": round(normalized.height_m, 3),
        "error": None,
    }


def _download(uid: str, folder: Path, token: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    payload = _get_json(DOWNLOAD_API.format(uid=uid), token)
    kind, info = _pick(payload)
    size = int(info.get("size") or 0)
    if size > MAX_BYTES:
        raise SketchfabError(f"{kind} archive is {size} bytes, over {MAX_BYTES}")
    data = _read_bytes(str(info["url"]), {"User-Agent": USER_AGENT}, timeout=300)
    target = folder / f"download.{kind}.bin"
    target.write_bytes(data)
    return target


def _pick(payload: dict) -> tuple[str, dict]:
    """Prefer the glTF zip. Fall back to the largest GLB that still fits."""
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else payload
    found: list[tuple[str, dict]] = []
    for key in ("gltf", "glb"):
        items = latest.get(key) or []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                found.append((key, item))
    gltf = [(key, item) for key, item in found if key == "gltf" and int(item.get("size") or 0) <= MAX_BYTES]
    if gltf:
        return max(gltf, key=lambda pair: int(pair[1].get("size") or 0))
    glb = [(key, item) for key, item in found if key == "glb" and 0 < int(item.get("size") or 0) <= MAX_BYTES]
    textured = [pair for pair in glb if int(pair[1].get("textureMaxResolution") or 0) > 0]
    pool = textured or glb
    if not pool:
        raise SketchfabError("download listing has no gltf or glb under the size cap")
    return max(pool, key=lambda pair: int(pair[1].get("size") or 0))


def _read_bytes(url: str, headers: dict, timeout: int) -> bytes:
    """An archive from Sketchfab or the signed storage URL it hands out.

    The signed host is not fixed, so the allowlist is the URL's own host and
    only ``https`` is accepted.
    """
    host = urlparse(url).hostname or ""
    return fetch_bytes(
        url,
        allowed_hosts=frozenset({host}),
        max_bytes=MAX_BYTES,
        timeout=timeout,
        headers=headers,
        retry_429=5,
        retry_delay_s=12.0,
        error=SketchfabError,
    )


def _get_json(url: str, token: str) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    }
    body = fetch_bytes(
        url,
        allowed_hosts=API_HOSTS,
        max_bytes=MAX_JSON_BYTES,
        timeout=60,
        headers=headers,
        retry_429=5,
        retry_delay_s=8.0,
        error=SketchfabError,
    )
    return json.loads(body.decode())


def _prepare_gltf(archive: Path) -> Path:
    if archive.is_dir():
        return archive
    data = archive.read_bytes()
    folder = archive.parent / "unpacked"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    if data[:4] == b"glTF":
        _write_glb(data, folder)
        return folder
    if data[:2] == b"PK":
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                relative = Path(info.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                target = folder / relative
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(info))
        if not any(folder.rglob("*.gltf")) and not any(folder.rglob("*.glb")):
            raise SketchfabError("archive has no glTF")
        glb = next(folder.rglob("*.glb"), None)
        if glb is not None and not any(folder.rglob("*.gltf")):
            _write_glb(glb.read_bytes(), folder)
        return folder if any(folder.glob("*.gltf")) else next(folder.rglob("*.gltf")).parent
    raise SketchfabError("download is neither a GLB nor a zip")


def _write_glb(data: bytes, folder: Path) -> None:
    import struct
    if len(data) < 20 or data[:4] != b"glTF":
        raise SketchfabError("not a GLB")
    offset = 12
    json_chunk = bin_chunk = b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        offset += 8 + length
        if kind == 0x4E4F534A:
            json_chunk = chunk.rstrip(b" \x00")
        elif kind == 0x004E4942:
            bin_chunk = chunk
    doc = json.loads(json_chunk)
    (folder / "scene.bin").write_bytes(bin_chunk)
    if doc.get("buffers"):
        doc["buffers"][0]["uri"] = "scene.bin"
    (folder / "scene.gltf").write_text(json.dumps(doc), encoding="utf-8")


def _append_manifest(library: Path, spec: dict, row: dict, normalized) -> None:
    path = library / "manifest.json"
    document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "schema": "onward_generator.abandoned_prefabs",
        "schema_version": "0.1.0",
        "unit": "m",
        "collider": "none",
        "shader": "PP_Opaque_Simple_Lit albedo only",
        "assets": [],
    }
    assets = [item for item in document.get("assets") or [] if item.get("id") != spec["id"]]
    assets.append({
        "id": spec["id"],
        "file": f"{spec['id']}.glb",
        "category": spec["category"],
        "role": spec["role"],
        "surfaces": spec["surfaces"],
        "anchor": spec["anchor"],
        "lane": "forbid",
        "clearance_m": float(spec.get("clearance_m") or 0.0),
        "use": spec["use"],
        "triangles": normalized.triangles_after,
        "length_m": round(normalized.length_m, 3),
        "depth_m": round(normalized.depth_m, 3),
        "height_m": round(normalized.height_m, 3),
        "collider": "none",
        "license": "CC-BY-4.0",
        "license_url": row.get("license_url"),
        "author": row.get("author"),
        "source_url": row.get("url"),
        "source": "sketchfab",
    })
    document = keep_semantics({**document, "assets": assets}, document)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
