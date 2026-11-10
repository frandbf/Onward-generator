"""Download the Quaternius CC0 packs and write one catalogue per pack under ``assets``.

Sources (every pack page says CC0 1.0; the pack's own License file is checked before anything is written):

- Ultimate Home Interior, Ultimate Furniture and Furniture: OBJ + MTL from the Google Drive folder the pack page
  links. Flat Kd colours, no textures. Every kept row is ``placement: interior`` (shop and floor furnishing).
- Downtown City MegaKit [Standard] and Stylized Nature MegaKit [Standard]: the free zip from quaternius.itch.io.
  glTF with external PNGs. Only the city ``Prop_*`` models are kept: the rest is a building and road kit, and a
  kit building never replaces an OSM footprint. From nature only the opaque models are kept: the trees and plants
  use alpha-cutout leaves, which ``PP_Opaque_Simple_Lit`` cannot draw.

Each GLB is opaque, albedo only: an OBJ colour becomes a 4 px PNG, a glTF keeps its base colour image (at most
512 px on its long side) and drops normal, ORM and emissive maps. Files are metres and Y up, as authored.
Downloads stay in the gitignored ``cache/quaternius``. Run ``--download`` once, then without it to rebuild.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import pack_glb, scene_primitives
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.overgrow.manifest import check_manifest

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "assets"
CACHE = REPO / "cache" / "quaternius"
REVIEWED_ON = "2026-11-10"
FLAT_ALBEDO_PX = 4
MAX_TEXTURE_PX = 512
USER_AGENT = "onward-generator asset import"
CC0_SOURCE = "CC0 1.0"
SEMANTICS = {
    "origin": "estimated",
    "basis": "written by tools/import_quaternius.py from each file name and bounding box; no person reviewed a rendered view",
    "reviewed_on": REVIEWED_ON,
    "fields": ["description", "placement", "grouping", "condition", "tags"],
}


@dataclass(frozen=True, slots=True)
class DrivePack:
    catalogue: str
    prefix: str
    title: str
    page: str
    folder: str
    factor: float
    reference: str


@dataclass(frozen=True, slots=True)
class ItchPack:
    catalogue: str
    prefix: str
    title: str
    page: str
    game: str
    zip_name: str
    factor: float
    reference: str


DRIVE_PACKS = (
    # The OBJ units are not metres in the two larger packs: the factor maps the pack's door to 2.05 m, as for Kenney.
    DrivePack("quaternius_home_interior", "qt_home", "Ultimate Home Interior", "ultimatehomeinterior",
              "1SNK9PwPi8xqqxmpU5xEZeiQjB26C1oX6", 0.489, "Door_1 4.19 file units to 2.05 m"),
    DrivePack("quaternius_ultimate_furniture", "qt_ufurn", "Ultimate Furniture", "ultimatefurniture",
              "1n85oUi0RN5ZUXEIMKA-AnBsPErVXWcma", 0.665, "Door1 3.08 file units to 2.05 m"),
    DrivePack("quaternius_furniture", "qt_furn", "Furniture", "furniture", "1CLWStkb7cipC1ZdTunYJXKVqEVwtXWXK",
              1.0, "already metres: Chair 0.99 m, BookCase 1.53 m"),
)
CITY = ItchPack("quaternius_city_props", "qt_city", "Downtown City MegaKit [Standard]", "downtowncitymegakit",
                "downtown-city-megakit", "Downtown City MegaKit[Standard].zip", 1.0,
                "already metres: Prop_Bollard 0.89 m tall")
NATURE = ItchPack("quaternius_nature", "qt_nature", "Stylized Nature MegaKit [Standard]", "stylizednaturemegakit",
                  "stylized-nature-megakit", "Stylized Nature MegaKit[Standard].zip", 0.5,
                  "Grass_Common_Tall 1.87 file units to 0.94 m, Rock_Medium_1 3.2 to 1.6 m")

# Interior pieces the generator already builds itself, or that hang from a ceiling or a wall,
# which furnishing does not place: doors, windows, columns, curtains, mirrors and ceiling lights.
INTERIOR_SKIP = re.compile(r"^(Door|Window|Column|Curtains|Bathroom_Mirror|Light_Ceiling|Light_Chandelier)")

# City props, with the outdoor semantics overgrow reads: (category, role, placement, anchor, surfaces, description).
CITY_PROPS = {
    "Prop_ManholeCover": ("barriers", "manhole", "ground_cover", "flat", ["sidewalk", "carriageway", "plaza"],
                          "Round cast-iron manhole cover with an ornamental pattern, flush with the ground."),
    "Prop_Drain": ("barriers", "manhole", "ground_cover", "flat", ["curb", "carriageway"],
                   "Rectangular cast-iron storm drain grate set in the ground at the kerb."),
    "Prop_Planter_Single": ("furniture", "planter", "open_ground", "ground", ["sidewalk", "plaza", "courtyard"],
                            "Square concrete street planter with a metal rim, filled with soil."),
    "Prop_Bollard": ("barriers", "barrier", "kerb", "ground", ["curb", "sidewalk", "plaza"],
                     "Short dark metal bollard on a concrete foot, at the kerb."),
    "Prop_ACUnit": ("facade", "aircon", "wall_mounted", "facade", ["facade"],
                    "Boxy air-conditioning unit with a fan grille, hung on a facade."),
}


def _nature_semantics(stem: str) -> tuple[str, str, str, str, list[str], str, str] | None:
    """(category, role, placement, anchor, surfaces, condition, description) of an opaque nature model, or None."""
    words = _words(stem)
    open_ground = ["yard", "empty_lot", "park", "courtyard", "plaza"]
    if stem.startswith("DeadTree"):
        return ("vegetation", "tree", "open_ground", "ground", ["yard", "empty_lot", "park"], "damaged",
                "Leafless dead tree with a twisted trunk and bare branches, stylized low poly.")
    if stem.startswith("Rock_Medium"):
        return ("structures", "rock", "open_ground", "ground", open_ground, "intact",
                "Medium grey boulder with faceted sides, stylized low poly.")
    if stem.startswith("Pebble"):
        return ("structures", "debris", "ground_cover", "flat", open_ground, "intact",
                f"Small {words.split()[1].lower()} pebble lying on the ground, stylized low poly.")
    if stem.startswith("RockPath"):
        return ("structures", "path", "ground_cover", "flat", open_ground, "intact",
                f"Flat stepping stone for a garden path ({words}), stylized low poly.")
    if stem.startswith("Grass"):
        return ("vegetation", "plant", "ground_cover", "ground", ["yard", "empty_lot", "park"], "intact",
                f"Opaque clump of {words.lower()} blades, stylized low poly.")
    if stem.startswith("Mushroom"):
        return ("vegetation", "plant", "ground_cover", "ground", ["yard", "empty_lot", "park"], "intact",
                f"Small {words.lower()} growing from the ground, stylized low poly.")
    return None


# --- download ---------------------------------------------------------------------------------------------------


def _get(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310 - fixed https hosts above
        body: bytes = response.read()
        return body


def _drive_listing(folder: str) -> list[tuple[bool, str, str]]:
    html = _get(f"https://drive.google.com/embeddedfolderview?id={folder}").decode("utf-8", "replace")
    pattern = r'href="https://drive\.google\.com/(drive/folders|file/d)/([A-Za-z0-9_-]+)[^"]*"[^>]*>.*?flip-entry-title">([^<]+)<'
    return [(kind.startswith("drive"), ident, name.strip()) for kind, ident, name in re.findall(pattern, html, re.S)]


def download_drive(pack: DrivePack) -> None:
    """The pack's License file and its OBJ folder; FBX and Blend folders are skipped."""
    target = CACHE / pack.catalogue
    target.mkdir(parents=True, exist_ok=True)
    for is_dir, ident, name in _drive_listing(pack.folder):
        if is_dir and name == "OBJ":
            for sub_dir, file_id, file_name in _drive_listing(ident):
                if not sub_dir:
                    _drive_file(file_id, target / "OBJ" / file_name)
        elif not is_dir and name.lower().startswith("license"):
            _drive_file(ident, target / name)


def _drive_file(file_id: str, path: Path) -> None:
    if path.is_file() and path.stat().st_size:
        return
    data = _get(f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t")
    if data[:15].lower().startswith(b"<!doctype html"):
        raise SystemExit(f"{path.name}: Google Drive answered with a page, not the file")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, data)
    time.sleep(0.2)


def download_itch(pack: ItchPack) -> None:
    """The free [Standard] zip of a name-your-own-price itch.io page, as its "No thanks, just take me to the downloads" link."""
    archive = CACHE / pack.zip_name
    if archive.is_file():
        return
    cookies: dict[str, str] = {}

    def call(url: str, form: dict[str, str] | None = None) -> bytes:
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
        request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form else None,
                                         headers={"User-Agent": USER_AGENT, **headers})
        with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310 - fixed https hosts above
            for header in response.headers.get_all("Set-Cookie") or []:
                key, _, rest = header.partition("=")
                cookies[key] = rest.split(";", 1)[0]
            body: bytes = response.read()
            return body

    base = f"https://quaternius.itch.io/{pack.game}"
    token = _csrf(call(base))
    url = json.loads(call(f"{base}/download_url", {"csrf_token": token}))["url"]
    page = call(url).decode("utf-8", "replace")
    key = url.rsplit("/download/", 1)[1]
    uploads = re.findall(r'data-upload_id="(\d+)".*?title="([^"]+)"', page, re.S)
    upload = next((ident for ident, name in uploads if name == pack.zip_name), None)
    if upload is None:
        raise SystemExit(f"{pack.game}: no upload named {pack.zip_name!r} on the download page")
    signed = json.loads(call(f"{base}/file/{upload}?source=game_download&key={key}", {"csrf_token": _csrf(page.encode())}))["url"]
    archive.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(archive, _get(signed))


def _csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise SystemExit("itch.io page has no csrf_token")
    return match.group(1).decode()


# --- OBJ to GLB -------------------------------------------------------------------------------------------------


def _srgb(linear: float) -> float:
    value = min(max(float(linear), 0.0), 1.0)
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def read_mtl(text: str) -> dict[str, tuple[float, float, float]]:
    """Kd of each material (linear, as Blender writes it)."""
    colours: dict[str, tuple[float, float, float]] = {}
    name = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "newmtl":
            name = " ".join(parts[1:])
            colours[name] = (0.8, 0.8, 0.8)
        elif parts[0] == "Kd" and name is not None:
            colours[name] = (float(parts[1]), float(parts[2]), float(parts[3]))
    return colours


def read_obj(text: str) -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
    """Triangles per material: a list of (3 corner positions, 3 corner normals). Faces are fanned."""
    positions: list[list[float]] = []
    normals: list[list[float]] = []
    groups: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    material = "default"
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        head = parts[0]
        if head == "v":
            positions.append([float(x) for x in parts[1:4]])
        elif head == "vn":
            normals.append([float(x) for x in parts[1:4]])
        elif head == "usemtl":
            material = " ".join(parts[1:])
        elif head == "f":
            corners = []
            for token in parts[1:]:
                fields = token.split("/")
                v = int(fields[0])
                n = int(fields[2]) if len(fields) > 2 and fields[2] else 0
                corners.append((v - 1 if v > 0 else len(positions) + v, (n - 1 if n > 0 else len(normals) + n) if n else None))
            for i in range(1, len(corners) - 1):
                tri = (corners[0], corners[i], corners[i + 1])
                p = np.array([positions[c[0]] for c in tri], dtype=np.float64)
                if all(c[1] is not None for c in tri):
                    nrm = np.array([normals[c[1]] for c in tri], dtype=np.float64)  # type: ignore[index]
                else:
                    face = np.cross(p[1] - p[0], p[2] - p[0])
                    length = np.linalg.norm(face)
                    nrm = np.tile(face / length if length else np.array([0.0, 1.0, 0.0]), (3, 1))
                groups.setdefault(material, []).append((p, nrm))
    return groups


def obj_to_glb(obj_text: str, mtl_text: str, name: str, factor: float = 1.0) -> bytes:
    colours = read_mtl(mtl_text)
    groups = {m: [(p * factor, n) for p, n in tris] for m, tris in read_obj(obj_text).items()}
    blob = bytearray()
    doc: dict = {"asset": {"version": "2.0", "generator": "tools/import_quaternius.py"}, "buffers": [], "bufferViews": [],
                 "accessors": [], "materials": [], "images": [], "textures": [], "meshes": [], "nodes": [],
                 "scenes": [], "scene": 0}

    def view(data: bytes) -> int:
        blob.extend(b"\x00" * (-len(blob) % 4))
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        return len(doc["bufferViews"]) - 1

    primitives = []
    for material, triangles in groups.items():
        p = np.concatenate([t[0] for t in triangles]).astype(np.float32)
        n = np.concatenate([t[1] for t in triangles]).astype(np.float32)
        uv = np.full((len(p), 2), 0.5, dtype=np.float32)
        idx = np.arange(len(p), dtype=np.uint32)
        acc = []
        for array, kind, extra in ((p, "VEC3", {"min": p.min(axis=0).tolist(), "max": p.max(axis=0).tolist()}),
                                   (n, "VEC3", {}), (uv, "VEC2", {})):
            doc["accessors"].append({"bufferView": view(array.tobytes()), "componentType": 5126, "count": len(array), "type": kind, **extra})
            acc.append(len(doc["accessors"]) - 1)
        doc["accessors"].append({"bufferView": view(idx.tobytes()), "componentType": 5125, "count": len(idx), "type": "SCALAR"})
        red, green, blue = colours.get(material, (0.8, 0.8, 0.8))
        png = io.BytesIO()
        Image.new("RGB", (FLAT_ALBEDO_PX, FLAT_ALBEDO_PX), tuple(round(255 * _srgb(c)) for c in (red, green, blue))).save(png, format="PNG")
        doc["images"].append({"mimeType": "image/png", "bufferView": view(png.getvalue())})
        doc["textures"].append({"source": len(doc["images"]) - 1})
        doc["materials"].append({"name": re.sub(r"[^A-Za-z0-9_]+", "_", material), "alphaMode": "OPAQUE", "doubleSided": False,
                                 "pbrMetallicRoughness": {"baseColorTexture": {"index": len(doc["textures"]) - 1},
                                                          "metallicFactor": 0.0, "roughnessFactor": 1.0}})
        primitives.append({"attributes": {"POSITION": acc[0], "NORMAL": acc[1], "TEXCOORD_0": acc[2]},
                           "indices": len(doc["accessors"]) - 1, "material": len(doc["materials"]) - 1, "mode": 4})
    doc["meshes"].append({"name": name, "primitives": primitives})
    doc["nodes"].append({"name": name, "mesh": 0})
    doc["scenes"].append({"nodes": [0]})
    blob.extend(b"\x00" * (-len(blob) % 4))
    doc["buffers"].append({"byteLength": len(blob)})
    return pack_glb(doc, bytes(blob))


# --- glTF to GLB ------------------------------------------------------------------------------------------------


def gltf_to_glb(path: Path, texture_dirs: list[Path], factor: float = 1.0) -> bytes:
    """Embed the buffers and the base colour images; drop every other map; make every material opaque.

    ``factor`` hangs the scene from one scaled root, as the Kenney import does.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if factor != 1.0:
        scene = doc["scenes"][int(doc.get("scene", 0))]
        doc["nodes"].append({"name": "street_scale", "scale": [factor] * 3, "children": list(scene["nodes"])})
        scene["nodes"] = [len(doc["nodes"]) - 1]
    blob = bytearray()
    offsets = []
    for buffer in doc.get("buffers") or []:
        blob.extend(b"\x00" * (-len(blob) % 4))
        offsets.append(len(blob))
        blob.extend((path.parent / urllib.parse.unquote(buffer["uri"])).read_bytes())
    for view in doc.get("bufferViews") or []:
        view["byteOffset"] = offsets[view.get("buffer", 0)] + view.get("byteOffset", 0)
        view["buffer"] = 0
    used: dict[int, int] = {}
    images: list[dict] = []
    textures: list[dict] = []
    for material in doc.get("materials") or []:
        pbr = material.setdefault("pbrMetallicRoughness", {})
        for key in ("normalTexture", "occlusionTexture", "emissiveTexture", "emissiveFactor"):
            material.pop(key, None)
        pbr.pop("metallicRoughnessTexture", None)
        pbr["metallicFactor"], pbr["roughnessFactor"] = 0.0, 1.0
        material["alphaMode"] = "OPAQUE"
        material.pop("alphaCutoff", None)
        base = pbr.get("baseColorTexture")
        if base is None:
            continue
        source = doc["textures"][base["index"]]["source"]
        if source not in used:
            image = doc["images"][source]
            data = _image_bytes(image, path.parent, texture_dirs, doc, blob)
            blob.extend(b"\x00" * (-len(blob) % 4))
            doc["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
            blob.extend(data)
            images.append({"mimeType": "image/png", "bufferView": len(doc["bufferViews"]) - 1})
            textures.append({"source": len(images) - 1})
            used[source] = len(textures) - 1
        pbr["baseColorTexture"] = {"index": used[source]}
    doc["images"], doc["textures"] = images, textures
    doc.pop("samplers", None)
    if not images:
        doc.pop("images")
        doc.pop("textures")
    blob.extend(b"\x00" * (-len(blob) % 4))
    doc["buffers"] = [{"byteLength": len(blob)}]
    return pack_glb(doc, bytes(blob))


def _image_bytes(image: dict, folder: Path, texture_dirs: list[Path], doc: dict, blob: bytearray) -> bytes:
    if "uri" in image:
        name = urllib.parse.unquote(image["uri"])
        candidates = [folder / name, *(d / Path(name).name for d in texture_dirs)]
        file = next((c for c in candidates if c.is_file()), None)
        if file is None:
            raise SystemExit(f"missing texture {name}")
        raw = file.read_bytes()
    else:
        view = doc["bufferViews"][image["bufferView"]]
        raw = bytes(blob[view["byteOffset"]:view["byteOffset"] + view["byteLength"]])
    picture = Image.open(io.BytesIO(raw)).convert("RGB")
    if max(picture.size) > MAX_TEXTURE_PX:
        picture.thumbnail((MAX_TEXTURE_PX, MAX_TEXTURE_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()


# --- catalogue rows ---------------------------------------------------------------------------------------------


def _words(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", stem))).strip()


def _tags(words: str) -> list[str]:
    found = [w for w in words.lower().split() if re.fullmatch(r"[a-z][a-z_]{1,23}", w)]
    return list(dict.fromkeys(found))[:4] or ["prop"]


def box(data: bytes) -> tuple[int, float, float, float]:
    """Triangles, then length, depth and height as the library loads the file: Z up."""
    primitives = [p for p in scene_primitives(data) if p.mesh.triangle_count]
    vertices = np.concatenate([p.mesh.vertices for p in primitives])
    spans = vertices.max(axis=0) - vertices.min(axis=0)
    return sum(p.mesh.triangle_count for p in primitives), *(round(float(s), 3) for s in spans)


def _sizes(measured: tuple[int, float, float, float]) -> dict:
    triangles, length, depth, height = measured
    return {"triangles": triangles, "length_m": length, "depth_m": depth, "height_m": height, "collider": "none"}


def interior_row(asset_id: str, file: str, stem: str, title: str, measured: tuple[int, float, float, float]) -> dict:
    words = _words(stem)
    size = max(measured[1:])
    return {
        "id": asset_id, "file": file, "category": "furniture", "role": "room_furniture", "surfaces": ["interior"],
        "anchor": "ground", "lane": "forbid", "clearance_m": 0.0, "use": f"{words.lower()}, inside a room. {title}, Quaternius.",
        "description": f"Low-poly {words.lower()} from {title}, flat colours, about {size:.2f} m on its longest side."[:140],
        "placement": "interior", "grouping": "single", "condition": "intact", "tags": _tags(words), **_sizes(measured),
    }


def outdoor_row(asset_id: str, file: str, semantics: tuple, title: str, measured: tuple[int, float, float, float]) -> dict:
    category, role, placement, anchor, surfaces, condition, description = semantics
    words = _words(Path(file).stem)
    return {
        "id": asset_id, "file": file, "category": category, "role": role, "surfaces": surfaces, "anchor": anchor,
        "lane": "forbid", "clearance_m": 0.0, "use": f"{words.lower()}. {title}, Quaternius.", "description": description[:140],
        "placement": placement, "grouping": "cluster" if placement == "ground_cover" else "single", "condition": condition,
        "tags": _tags(words), **_sizes(measured),
    }


def _manifest(rows: list[dict], source: str) -> dict:
    return {
        "schema": "onward_generator.abandoned_prefabs", "schema_version": "0.2.0", "unit": "m", "collider": "none",
        "shader": "PP_Opaque_Simple_Lit albedo only", "source": source, "semantics": SEMANTICS, "assets": rows,
    }


def _check_cc0(text: str, where: str) -> None:
    if "CC0" not in text and "creativecommons.org/publicdomain/zero" not in text:
        raise SystemExit(f"{where}: the licence file does not say CC0")


def _safe_id(prefix: str, stem: str) -> str:
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', stem).strip('_')}"


def _write(catalogue: str, rows: list[dict], files: dict[str, bytes], source: str, licence: bytes) -> int:
    target = ASSETS / catalogue
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        atomic_write_bytes(target / name, data)
    atomic_write_bytes(target / "License.txt", licence)
    document = _manifest(rows, source)
    check_manifest(document, catalogue)
    atomic_write_json(target / "manifest.json", document)
    return len(rows)


def import_drive(pack: DrivePack) -> int:
    folder = CACHE / pack.catalogue
    licence_files = sorted(folder.glob("License*"))
    licence = licence_files[0].read_bytes() if licence_files else b""
    page_says = (f"https://quaternius.com/packs/{pack.page}.html, CC0 1.0 (pack page). OBJ + MTL, Y up. "
                 f"street_scale {pack.factor:g}: {pack.reference}.")
    if licence:
        _check_cc0(licence.decode("utf-8", "replace"), pack.catalogue)
    else:
        # The Furniture folder ships no licence file; the pack page names CC0 1.0 and links the deed.
        licence = f"Quaternius, {pack.title}: CC0 1.0 Universal, https://creativecommons.org/publicdomain/zero/1.0/\nSource: https://quaternius.com/packs/{pack.page}.html\n".encode()
    rows, files = [], {}
    for obj in sorted((folder / "OBJ").glob("*.obj")):
        if INTERIOR_SKIP.match(obj.stem):
            continue
        mtl = obj.with_suffix(".mtl")
        data = obj_to_glb(obj.read_text(encoding="utf-8", errors="replace"),
                          mtl.read_text(encoding="utf-8", errors="replace") if mtl.is_file() else "", obj.stem, pack.factor)
        name = f"{obj.stem}.glb"
        files[name] = data
        rows.append(interior_row(_safe_id(pack.prefix, obj.stem), name, obj.stem, pack.title, box(data)))
    return _write(pack.catalogue, rows, files, page_says, licence)


def _unzip(pack: ItchPack) -> Path:
    archive = CACHE / pack.zip_name
    if not archive.is_file():
        raise SystemExit(f"{archive} is missing: run with --download")
    folder = CACHE / pack.catalogue
    if not folder.is_dir():
        with zipfile.ZipFile(archive) as opened:
            opened.extractall(folder)
    return folder


def import_city() -> int:
    folder = _unzip(CITY)
    licence = next(folder.rglob("License_Standard.txt")).read_bytes()
    _check_cc0(licence.decode("utf-8", "replace"), CITY.catalogue)
    gltf_dir = next(p for p in folder.rglob("*") if p.is_dir() and p.name.startswith("glTF"))
    rows, files = [], {}
    for stem, (category, role, placement, anchor, surfaces, description) in CITY_PROPS.items():
        data = gltf_to_glb(gltf_dir / f"{stem}.gltf", [folder / "Textures"])
        name = f"{stem}.glb"
        files[name] = data
        semantics = (category, role, placement, anchor, surfaces, "worn", description)
        rows.append(outdoor_row(_safe_id(CITY.prefix, stem), name, semantics, CITY.title, box(data)))
    source = f"https://quaternius.com/packs/{CITY.page}.html, CC0 1.0. Only the Prop_* models of the free Standard zip."
    return _write(CITY.catalogue, rows, files, source, licence)


def import_nature() -> int:
    folder = _unzip(NATURE)
    licence = next(folder.rglob("License_Standard.txt")).read_bytes()
    _check_cc0(licence.decode("utf-8", "replace"), NATURE.catalogue)
    rows, files = [], {}
    for gltf in sorted((folder / "glTF").glob("*.gltf")):
        doc = json.loads(gltf.read_text(encoding="utf-8"))
        if any(m.get("alphaMode", "OPAQUE") != "OPAQUE" for m in doc.get("materials") or []):
            continue
        semantics = _nature_semantics(gltf.stem)
        if semantics is None:
            continue
        data = gltf_to_glb(gltf, [folder / "Textures"], NATURE.factor)
        name = f"{gltf.stem}.glb"
        files[name] = data
        rows.append(outdoor_row(_safe_id(NATURE.prefix, gltf.stem), name, semantics, NATURE.title, box(data)))
    source = (f"https://quaternius.com/packs/{NATURE.page}.html, CC0 1.0. Opaque models of the free Standard zip only. "
              f"street_scale {NATURE.factor:g}: {NATURE.reference}.")
    return _write(NATURE.catalogue, rows, files, source, licence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="fetch the packs into cache/quaternius first")
    args = parser.parse_args(argv)
    if args.download:
        for drive_pack in DRIVE_PACKS:
            download_drive(drive_pack)
        for itch_pack in (CITY, NATURE):
            download_itch(itch_pack)
    for drive_pack in DRIVE_PACKS:
        print(f"{drive_pack.catalogue}: {import_drive(drive_pack)} rows")
    print(f"{CITY.catalogue}: {import_city()} rows")
    print(f"{NATURE.catalogue}: {import_nature()} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
