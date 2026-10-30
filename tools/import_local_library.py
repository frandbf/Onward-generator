"""Copy local CC0 downloads and write the imported library catalogue.

Tileable textures go to assets/textures. Kenney GLBs go to assets/kenney.
Albedo only. Normals, roughness and displacement stay in Downloads.
Kenney buildings are listed and marked so they cannot replace OSM footprints.
Nothing in the catalogue is bound to a texture plan.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEXTURES = Path("/Users/frandbf/Downloads/texturas")
OBJECTS = Path("/Users/frandbf/Downloads/objetos")
ASSETS = REPO / "assets"
KENNEY = REPO / "assets" / "kenney"
CATALOG = REPO / "config" / "dress" / "imported_library.json"
CC0 = "CC0-1.0"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
AMBIENT_URL = "https://docs.ambientcg.com/license/"
KENNEY_URL = "https://kenney.nl"

PACKS = {
    "kenney_city-kit-suburban_20": "suburban",
    "kenney_modular-buildings": "modular",
    "kenney_retro-urban-kit": "urban",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_to(source: Path, relative: str, root: Path = ASSETS, prefix: str = "assets") -> dict:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        "path": f"{prefix}/{relative}",
        "sha256": sha256(target),
        "bytes": target.stat().st_size,
    }


def ambient_surface(name: str) -> str:
    key = name.lower()
    if key.startswith(("paintedplaster", "bricks", "rock")):
        return "wall"
    if key.startswith("roofingtiles"):
        return "roof"
    if key.startswith("pavingstones"):
        return "sidewalk"
    if key.startswith(("concrete", "tiles")):
        return "floor"
    if key.startswith(("asphalt", "road")):
        return "street"
    if key.startswith("grate"):
        return "accessory"
    return "other"


def fantasy_surface(stem: str) -> str:
    if stem.startswith("floor_"):
        return "floor"
    if stem.startswith(("wall_", "roof_")):
        return "wall" if stem.startswith("wall_") else "roof"
    if stem.startswith("door_"):
        return "opening"
    if stem.startswith("road_"):
        return "street"
    return "other"


def object_role(stem: str) -> str:
    if "building" in stem or stem.startswith("cliff"):
        return "building"
    if any(token in stem for token in ("tree", "fence", "planter", "path", "driveway")):
        return "furniture"
    return "prop"


def ambient_textures() -> list[dict]:
    rows = []
    seen_hash: dict[str, str] = {}
    for folder in sorted(p for p in TEXTURES.iterdir() if p.is_dir() and p.name != "kenney_retro-textures-fantasy"):
        colors = list(folder.glob("*_Color.jpg"))
        if len(colors) != 1:
            raise SystemExit(f"{folder.name}: expected one Color map, found {len(colors)}")
        source = colors[0]
        digest = sha256(source)
        asset = folder.name.split("_", 1)[0].replace(" ", "")
        if asset.endswith("2") and not any(ch.isdigit() for ch in asset[-2:-1]):
            asset = asset[:-1]
        asset_id = "".join(ch for ch in asset if ch.isalnum())
        slug = f"acg_{asset_id.lower()}"
        if digest in seen_hash:
            continue
        seen_hash[digest] = slug
        copied = copy_to(source, f"textures/ambientcg/{slug}.jpg")
        note = "Albedo Color 1k. Normal, roughness and displacement were not copied."
        if asset_id.lower().startswith("road"):
            note += " Painted road sheet, not a seamless asphalt."
        rows.append({
            "id": slug,
            "name": asset_id,
            "provider": "ambientcg",
            "license": CC0,
            "license_url": AMBIENT_URL,
            "credit": f"Created using {asset_id} from ambientCG.com, licensed under CC0 1.0.",
            "source_url": f"https://ambientcg.com/a/{asset_id}",
            "surface": ambient_surface(asset_id),
            "bound": False,
            **copied,
            "note": note,
        })
    return rows


def fantasy_textures() -> list[dict]:
    folder = TEXTURES / "kenney_retro-textures-fantasy" / "PNG"
    rows = []
    for source in sorted(folder.glob("*.png")):
        slug = "kenney_fantasy_" + source.stem.replace("-", "_")
        copied = copy_to(source, f"textures/kenney/retro-textures-fantasy/{source.name}")
        rows.append({
            "id": slug,
            "name": source.stem,
            "provider": "kenney",
            "pack": "Retro Textures Fantasy 1.0",
            "license": CC0,
            "license_url": CC0_URL,
            "credit": "Kenney (www.kenney.nl)",
            "source_url": KENNEY_URL,
            "surface": fantasy_surface(source.stem),
            "bound": False,
            **copied,
            "note": "PNG tile. Alpha and overlays are not used by PP_Opaque_Simple_Lit.",
        })
    return rows


def kenney_objects() -> list[dict]:
    rows = []
    for folder_name, short in PACKS.items():
        folder = OBJECTS / folder_name
        license_src = folder / "License.txt"
        if "CC0" not in license_src.read_text(encoding="utf-8"):
            raise SystemExit(f"{folder_name}: licence file is not CC0")
        copy_to(license_src, f"{short}/License.txt", root=KENNEY, prefix="assets/kenney")
        textures = folder / "Models" / "GLB format" / "Textures"
        for source in sorted(textures.glob("*.png")):
            copy_to(source, f"{short}/Textures/{source.name}", root=KENNEY, prefix="assets/kenney")
        for source in sorted((folder / "Models" / "GLB format").glob("*.glb")):
            stem = source.stem
            if object_role(stem) == "building":
                continue
            slug = f"kenney_{short}_" + stem.replace("-", "_")
            copied = copy_to(source, f"{short}/{source.name}", root=KENNEY, prefix="assets/kenney")
            rows.append({
                "id": slug,
                "name": stem,
                "provider": "kenney",
                "pack": folder_name,
                "license": CC0,
                "license_url": CC0_URL,
                "credit": "Kenney (www.kenney.nl)",
                "source_url": KENNEY_URL,
                "role": object_role(stem),
                "replaces_osm": False,
                "placed": False,
                **copied,
                "note": "GLB plus the Textures PNG it references. FBX and OBJ copies were not imported. A building does not replace an OSM footprint.",
            })
    return rows


# Calm Poly Haven plasters already committed under assets/textures (CC0, each with
# its .source.json). They widen the plaster pool after the busy ambientCG plasters
# were dropped for repeating stains.
REPO_PLASTERS = ("painted_plaster_wall", "plastered_wall")


def repo_textures() -> list[dict]:
    rows = []
    for asset_id in REPO_PLASTERS:
        relative = f"textures/{asset_id}/{asset_id}_diff_1k.jpg"
        target = ASSETS / relative
        source = json.loads(target.with_name(target.name + ".source.json").read_text(encoding="utf-8"))
        if source.get("license") != "CC0":
            raise SystemExit(f"{asset_id}: licence {source.get('license')!r} is not CC0")
        rows.append({
            "id": f"ph_{asset_id}",
            "name": asset_id,
            "provider": "polyhaven",
            "license": CC0,
            "license_url": CC0_URL,
            "credit": f"{asset_id} by {', '.join(source['authors'])} from Poly Haven, licensed under CC0 1.0.",
            "source_url": source["page_url"],
            "surface": "wall",
            "bound": False,
            "path": f"assets/{relative}",
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
            "note": "Diffuse 1k, already in the repository. Calm enough for a wall slot.",
        })
    return rows


def main() -> None:
    textures = ambient_textures() + repo_textures() + fantasy_textures()
    objects = kenney_objects()
    ids = [row["id"] for row in textures + objects]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate catalogue id")
    document = {
        "schema": "onward_generator.imported_library",
        "schema_version": "0.1.0",
        "description": "Local CC0 downloads. Tileable textures in assets/textures. Kenney GLBs in assets/kenney, without building-role meshes, with each pack's Textures folder beside the GLBs. Not bound to a map texture plan and not placed.",
        "textures": textures,
        "objects": objects,
    }
    CATALOG.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"textures {len(textures)} objects {len(objects)} -> {CATALOG}")


if __name__ == "__main__":
    main()
