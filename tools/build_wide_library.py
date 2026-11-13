"""Write config/dress/wide_library.json: CC0 Poly Haven albedos for the map.json ``wide_library`` switch (plan T1).

Each row is a cached 1k Diffuse JPEG under assets/textures/<id>/ with its Poly Haven sidecar (licence, authors,
hashes). The family is set here, not guessed from the name, and every row passes its family's calm limit
(``library_plan.is_calm`` with the extended limits): ``tests/unit/test_wide_library.py`` checks the written file.

    uv run python tools/build_wide_library.py            # rewrite the manifest from the cached files

A new id is fetched with ``materials.polyhaven.ensure_diffuse_1k`` and its normal map with
``materials.polyhaven.ensure_map_1k(url, "nor_gl")`` (plan T3); a row records ``normal`` when that file is cached.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "config" / "dress" / "wide_library.json"

# id: (family, surface, note). Picked by eye from a contact sheet after the calm filter (13 November 2026):
# moss-covered, flat-colour, striped and big-crack images were left out although calm.
ROWS: dict[str, tuple[str, str, str]] = {
    "yellow_plaster": ("plaster", "wall", "Warm yellow render, fine trowel marks."),
    "clay_plaster": ("plaster", "wall", "Brown clay render, even."),
    "white_plaster_02": ("plaster", "wall", "Pale grey-white render with soft patches."),
    "brick_wall_02": ("brick", "wall", "Mixed red and grey brick, dark mortar."),
    "factory_brick": ("brick", "wall", "Even salmon brick, light mortar."),
    "red_bricks_04": ("brick", "wall", "Deep red brick, weathered."),
    "old_stone_wall": ("rock", "wall", "Rubble stone in light mortar."),
    "stone_brick_wall_001": ("rock", "wall", "Dark dressed stone blocks."),
    "rock_wall_11": ("paving", "ground", "Pale irregular flagstones seen from above."),
    "rectangular_paving": ("paving", "ground", "Brown rectangular setts in stretcher bond."),
    "patterned_paving": ("paving", "ground", "Two-tone grey setts in a basket pattern."),
    "concrete_floor_01": ("concrete", "ground", "Exposed-aggregate concrete."),
    "concrete_pavement": ("concrete", "ground", "Square concrete slabs, light."),
    "concrete_pavement_02": ("concrete", "ground", "Mixed-shade concrete slabs."),
    "brushed_concrete": ("concrete", "ground", "Brushed concrete with faint swirls."),
    "concrete_floor_worn_001": ("concrete", "ground", "Dark worn concrete."),
    "asphalt_01": ("asphalt", "ground", "Even dark-brown asphalt."),
    "asphalt_03": ("asphalt", "ground", "Fine brown asphalt."),
    "clean_asphalt": ("asphalt", "ground", "Clean blue-grey asphalt."),
    "worn_asphalt": ("asphalt", "ground", "Worn asphalt with exposed grit."),
    "clay_roof_tiles": ("roof", "roof", "Dark terracotta pantiles."),
    "clay_roof_tiles_03": ("roof", "roof", "Bright terracotta plain tiles."),
    "clay_roof_tiles_02": ("roof", "roof", "Orange terracotta tiles."),
    "grey_roof_tiles": ("roof", "roof", "Grey tiles with lichen."),
    "bark_brown_02": ("bark", "trunk", "Brown bark with moss."),
}


def row(asset_id: str, family: str, surface: str, note: str) -> dict[str, object]:
    image = REPO / "assets" / "textures" / asset_id / f"{asset_id}_diff_1k.jpg"
    side = json.loads(image.with_name(image.name + ".source.json").read_text(encoding="utf-8"))
    if str(side.get("license", "")).lower() not in {"cc0", "cc0 1.0", "cc0-1.0"}:
        raise SystemExit(f"{asset_id}: licence {side.get('license')!r} is not CC0")
    data = image.read_bytes()
    authors = ", ".join(side.get("authors") or ["Poly Haven"])
    return {
        "id": f"ph_{asset_id}", "name": asset_id, "family": family, "provider": "polyhaven", "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "credit": f"{asset_id} by {authors} from Poly Haven, licensed under CC0 1.0.",
        "source_url": f"https://polyhaven.com/a/{asset_id}", "surface": surface, "bound": False,
        "path": image.relative_to(REPO).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "note": f"Diffuse 1k. {note}",
        **normal_of(image),
    }


def normal_of(image: Path) -> dict[str, object]:
    """The row's ``normal`` (plan T3): the cached OpenGL normal map beside the albedo, when its CC0 sidecar is there."""
    normal = image.with_name(image.name.replace("_diff_1k", "_nor_gl_1k"))
    side = normal.with_name(normal.name + ".source.json")
    if not side.is_file():
        return {}
    doc = json.loads(side.read_text(encoding="utf-8"))
    if str(doc.get("license", "")).lower() not in {"cc0", "cc0 1.0", "cc0-1.0"} or doc.get("map") != "nor_gl":
        raise SystemExit(f"{normal.name}: not a CC0 nor_gl sidecar")
    data = normal.read_bytes()
    return {"normal": {"map": "nor_gl", "path": normal.relative_to(REPO).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
                       "bytes": len(data)}}


def main() -> None:
    document = {
        "schema": "onward_generator.imported_library", "schema_version": "0.1.0",
        "description": "Wide library (plan T1): CC0 Poly Haven albedos that join the texture pools only when a map's "
                       "map.json has \"wide_library\": true. The default pools and picks do not change.",
        "textures": [row(asset_id, *spec) for asset_id, spec in sorted(ROWS.items())],
    }
    OUT.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(REPO)}: {len(document['textures'])} textures")


if __name__ == "__main__":
    main()
