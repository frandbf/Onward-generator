"""Download the shop, furniture and food props and write one catalogue per source.

Kenney zips, saved by hand, and Poly Haven originals, downloaded here, stay in the gitignored ``cache/shop_assets``.
Each catalogue is a direct child of ``assets`` with its own ``manifest.json``.
Every row is ``placement: interior``: the library lists it and overgrow never
puts it on a street (docs/abandoned-assets.md). Run it again and the same
downloads give the same files; a new size is a new factor in ``PACKS``.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import pack_glb, read_glb, scene_primitives
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.fetch import _get, _one
from onward_generator.overgrow.manifest import check_manifest

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "assets"
CACHE = REPO / "cache" / "shop_assets"
REVIEWED_ON = "2026-11-02"
FLAT_ALBEDO_PX = 4
SEMANTICS = {
    "origin": "estimated",
    "basis": "written by tools/import_shop_assets.py from each file name and bounding box; no person reviewed a rendered view",
    "reviewed_on": REVIEWED_ON,
    "fields": ["description", "placement", "grouping", "condition", "tags"],
}


@dataclass(frozen=True, slots=True)
class Pack:
    catalogue: str
    prefix: str
    title: str
    page: str
    models: str
    role: str
    factor: float
    reference: str


PACKS = (
    Pack("kenney_mini_shop", "kenney_shop", "Mini Shop", "mini-shop", "Models/GLB format", "shop_fixture", 2.5,
         "mannequin 0.719 file units to 1.80 m"),
    Pack("kenney_furniture", "kenney_furniture", "Furniture Kit", "furniture-kit", "Models/GLTF format", "room_furniture", 2.03,
         "doorway 1.01 file units to 2.05 m"),
    Pack("kenney_food", "kenney_food", "Food Kit", "food-kit", "Models/GLB format", "food", 0.3,
         "banana 0.63 file units to 0.19 m"),
)

POLY_HAVEN = {
    "Shelf_01": ("shelf", "Worn wooden shelf unit with weathered paint, tall and narrow."),
    "wooden_display_shelves_01": ("shelf", "Wooden display shelves for a shop, open at the front."),
    "steel_frame_shelves_01": ("shelf", "Tall steel frame shelving with wooden boards, wide bay."),
    "steel_frame_shelves_02": ("shelf", "Tall steel frame shelving with wooden boards, narrow bay."),
    "CashRegister_01": ("cash_register", "Old mechanical cash register for a shop counter."),
    "wooden_crate_01": ("crate", "Low open wooden crate with slatted sides."),
    "standing_chalkboard_01": ("sign", "Standing folding chalkboard for a shop menu or price list."),
}


def unpack(pack: Pack) -> Path:
    """The pack's folder under the cache. Kenney answers scripts with 403, so the zip comes from its page by hand."""
    archive = CACHE / f"{pack.catalogue}.zip"
    if not archive.is_file():
        raise SystemExit(f"download https://kenney.nl/assets/{pack.page} and save the zip as {archive}")
    folder = CACHE / pack.catalogue
    with zipfile.ZipFile(archive) as opened:
        opened.extractall(folder)
    return folder


def bake_flat_albedos(doc: dict, blob: bytes) -> bytes:
    """Embed each untextured material's base colour as a small PNG: the library reads albedo images, not factors."""
    out = bytearray(blob)
    for material in doc.get("materials") or []:
        pbr = material.setdefault("pbrMetallicRoughness", {})
        if "baseColorTexture" in pbr:
            continue
        red, green, blue, *_ = pbr.pop("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        colour = tuple(round(255 * _srgb(channel)) for channel in (red, green, blue))
        buffer = io.BytesIO()
        Image.new("RGB", (FLAT_ALBEDO_PX, FLAT_ALBEDO_PX), colour).save(buffer, format="PNG")
        out += b"\x00" * (-len(out) % 4)
        doc.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": len(out), "byteLength": buffer.tell()})
        out += buffer.getvalue()
        doc.setdefault("images", []).append({"mimeType": "image/png", "bufferView": len(doc["bufferViews"]) - 1})
        doc.setdefault("textures", []).append({"source": len(doc["images"]) - 1})
        pbr["baseColorTexture"] = {"index": len(doc["textures"]) - 1}
    return bytes(out)


def _srgb(linear: float) -> float:
    """glTF factors are linear; an albedo image is sRGB."""
    value = min(max(float(linear), 0.0), 1.0)
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def scale_scene(doc: dict, factor: float) -> None:
    """Hang the scene from one scaled root. Children keep their own transforms."""
    scene = doc["scenes"][int(doc.get("scene", 0))]
    doc["nodes"].append({"name": "street_scale", "scale": [factor, factor, factor], "children": list(scene["nodes"])})
    scene["nodes"] = [len(doc["nodes"]) - 1]
    doc.setdefault("extras", {})["street_scale"] = factor


def normalized_kenney(data: bytes, factor: float) -> bytes:
    doc, blob = read_glb(data)
    blob = bake_flat_albedos(doc, blob) if not doc.get("images") else blob
    if doc.get("buffers"):
        doc["buffers"][0]["byteLength"] = len(blob) + (-len(blob) % 4)
    scale_scene(doc, factor)
    return pack_glb(doc, blob)


def box(data: bytes) -> tuple[int, float, float, float]:
    """Triangles, then length, depth and height as the library loads the file: Z up."""
    primitives = [p for p in scene_primitives(data) if p.mesh.triangle_count]
    vertices = np.concatenate([p.mesh.vertices for p in primitives])
    spans = vertices.max(axis=0) - vertices.min(axis=0)
    return sum(p.mesh.triangle_count for p in primitives), *(round(float(s), 3) for s in spans)


def _words(stem: str) -> str:
    return re.sub(r"[-_]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", stem)).lower().strip()


def _tags(words: list[str]) -> list[str]:
    """The manifest's tag form: lowercase words, no digits."""
    return [word for word in words if re.fullmatch(r"[a-z][a-z_]{1,23}", word)][:4] or ["prop"]


def _row(asset_id: str, file: str, role: str, use: str, description: str, tags: list[str], measured: tuple[int, float, float, float]) -> dict:
    triangles, length, depth, height = measured
    return {
        "id": asset_id, "file": file, "category": "furniture", "role": role, "surfaces": ["interior"], "anchor": "ground",
        "lane": "forbid", "clearance_m": 0.0, "use": use, "description": description[:140], "placement": "interior",
        "grouping": "single", "condition": "intact", "tags": _tags(tags),
        "triangles": triangles, "length_m": length, "depth_m": depth, "height_m": height, "collider": "none",
    }


def _manifest(rows: list[dict], source: str) -> dict:
    return {
        "schema": "onward_generator.abandoned_prefabs", "schema_version": "0.2.0", "unit": "m", "collider": "none",
        "shader": "PP_Opaque_Simple_Lit albedo only", "source": source, "semantics": SEMANTICS, "assets": rows,
    }


def import_pack(pack: Pack) -> int:
    source = unpack(pack)
    if "CC0" not in (source / "License.txt").read_text(encoding="utf-8", errors="replace"):
        raise SystemExit(f"{pack.page}: License.txt does not say CC0")
    target = ASSETS / pack.catalogue
    (target / "Textures").mkdir(parents=True, exist_ok=True)
    models = source / pack.models
    rows = []
    for path in sorted(models.glob("*.glb")):
        data = normalized_kenney(path.read_bytes(), pack.factor)
        atomic_write_bytes(target / path.name, data)
        words = _words(path.stem)
        size = box(data)
        rows.append(_row(
            f"{pack.prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', path.stem)}", path.name, pack.role,
            f"{words}, inside a room. {pack.title}, Kenney.",
            f"Low-poly {words} from {pack.title}, clean flat colours, about {max(size[1:]):.2f} m on its longest side.",
            words.split()[:4], size,
        ))
    texture = models / "Textures" / "colormap.png"
    if texture.is_file():
        atomic_write_bytes(target / "Textures" / "colormap.png", texture.read_bytes())
    else:
        (target / "Textures").rmdir()
    atomic_write_bytes(target / "License.txt", (source / "License.txt").read_bytes())
    document = _manifest(rows, f"https://kenney.nl/assets/{pack.page}, CC0 1.0. street_scale {pack.factor:g}: {pack.reference}.")
    check_manifest(document, pack.catalogue)
    atomic_write_json(target / "manifest.json", document)
    return len(rows)


def import_poly_haven() -> int:
    target = ASSETS / "polyhaven_shop"
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    for asset_id, (role, description) in POLY_HAVEN.items():
        words = _words(asset_id)
        spec = AssetSpec(asset_id, "furniture", role, ("interior",), "ground", f"{words}, inside a shop. Poly Haven.")
        row, prefab = _one(spec, CACHE / "polyhaven", target, _get)
        if prefab is None:
            raise SystemExit(f"{asset_id}: {row.get('import_status')} {row.get('error', '')}")
        rows.append(_row(
            asset_id, prefab["file"], role, spec.use, description, words.split()[:4],
            (prefab["triangles"], prefab["length_m"], prefab["depth_m"], prefab["height_m"]),
        ))
    document = _manifest(rows, "https://polyhaven.com, CC0 1.0. Size from the /info dimensions.")
    check_manifest(document, target.name)
    atomic_write_json(target / "manifest.json", document)
    return len(rows)


def main() -> None:
    for pack in PACKS:
        print(pack.catalogue, import_pack(pack))
    print("polyhaven_shop", import_poly_haven())


if __name__ == "__main__":
    main()
