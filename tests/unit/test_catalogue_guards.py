"""Guards on the committed catalogues and on props placed by chance (15 November 2026).

A test once rewrote the Kenney manifest through a symlink and left its grey truck ``pinned_only``, so no map placed it
any more; and a 59 771-triangle helicopter wreck could be placed by chance on any map.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.overgrow.library import CHANCE_MAX_TRIANGLES, read_library
from tests.unit.test_prop_lod import _sphere

ASSETS = Path(__file__).resolve().parents[2] / "assets"
STORY_PROPS = {"uh60_black_hawk", "crashed_helicopter"}
"""The committed rows that only stand where a map's pinned_props.json pins them."""


def _committed() -> list[Path]:
    """The catalogues the repository ships: the local Asset Store ones (asset_store_*) are never committed."""
    return sorted(p for p in ASSETS.glob("*/manifest.json") if not p.parent.name.startswith("asset_store_"))


def test_only_the_story_props_are_pinned_only() -> None:
    pinned = {row["id"] for path in _committed() for row in json.loads(path.read_text(encoding="utf-8")).get("assets") or []
              if isinstance(row, dict) and row.get("placement") == "pinned_only"}
    assert pinned == STORY_PROPS


def test_the_kenney_truck_is_a_street_prop_again() -> None:
    rows = {row["id"]: row for row in json.loads((ASSETS / "kenney" / "manifest.json").read_text(encoding="utf-8"))["assets"]}
    assert rows["kenney_urban_truck_grey"]["placement"] == "street"


OFF_STREET = {"pinned_only", "interior", "kit_only"}
"""Rows the street library never places by chance: story props, interior furniture and kit parts."""


def test_no_committed_prop_placed_by_chance_is_over_the_cap() -> None:
    heavy = [(row["id"], row["triangles"]) for path in _committed() for row in json.loads(path.read_text(encoding="utf-8")).get("assets") or []
             if isinstance(row, dict) and row.get("placement") not in OFF_STREET and int(row.get("triangles") or 0) > CHANCE_MAX_TRIANGLES]
    assert heavy == []


def _heavy_car(root: Path, placement: str | None) -> int:
    mesh = _sphere(1.0, 70, 70)
    mesh = type(mesh)(mesh.vertices * np.array([2.2, 0.9, 0.7]) + np.array([0.0, 0.0, 0.7]), mesh.triangles)
    root.mkdir()
    (root / "heavy_car.glb").write_bytes(build_glb([Node("heavy_car", (Primitive(mesh, "heavy_car", np.zeros((mesh.vertex_count, 2), dtype=np.float32)),), {"type": "prop"})],
                                                   {"heavy_car": Material((1, 1, 1, 1), 0.9)}, preserve_uvs=True))
    row = {"id": "heavy_car", "file": "heavy_car.glb", "category": "vehicles", "role": "wreck", "surfaces": ["carriageway", "yard"],
           "anchor": "ground", "lane": "forbid", "clearance_m": 0.0, "use": "test"}
    if placement:
        row.update(description="A heavy test car.", placement=placement, grouping="single", condition="wrecked", tags=["car"])
    (root / "manifest.json").write_text(json.dumps({"assets": [row]}), encoding="utf-8")
    return mesh.triangle_count


@pytest.mark.parametrize("placement", [None, "open_ground"])
def test_a_prop_over_the_cap_is_refused_for_chance(tmp_path: Path, placement: str | None) -> None:
    triangles = _heavy_car(tmp_path / "lib", placement)
    assert triangles > CHANCE_MAX_TRIANGLES == 8000
    props, rejects = read_library(tmp_path / "lib")
    assert props == [] and [(r.asset_id, r.reason) for r in rejects] == [("heavy_car", "too_heavy_for_chance")]


def test_a_story_prop_over_the_cap_still_loads(tmp_path: Path) -> None:
    triangles = _heavy_car(tmp_path / "lib", "pinned_only")
    (prop,), rejects = read_library(tmp_path / "lib")
    assert rejects == [] and prop.triangles == triangles  # a vehicle keeps its whole mesh
