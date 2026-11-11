"""detail on the synthetic Murcia block: flat upper openings, their records and the triangles they save (ADR-025)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.scene import SCHEMA_VERSION
from onward_generator.generation import load_generation_config

GEN = load_generation_config()
CFG = load_detail_config()


def _detail(inputs: tuple[Path, Path, Path], relief_storeys: int):
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, replace(CFG, relief_storeys=relief_storeys))


def _buildings(d) -> dict[str, dict]:
    return {o.id: o.record for o in d.objects if o.type == "building" and "relief" in o.record["facade"]}


def test_flat_upper_openings_are_recorded_and_save_their_reveals(detail_inputs: tuple[Path, Path, Path]) -> None:
    flat, deep = _detail(detail_inputs, 2), _detail(detail_inputs, 1000)
    assert SCHEMA_VERSION == "0.4.0"
    assert flat.manifest()["schema_version"] == flat.report()["schema_version"] == "0.4.0"
    records, before = _buildings(flat), _buildings(deep)
    assert records and records.keys() == before.keys()
    for oid, record in records.items():
        relief = record["facade"]["relief"]
        openings = sum(n for kind, n in record["facade"]["openings"].items() if kind != "balcony")
        assert relief["relief_storeys"] == 2 and relief["flat_openings"] + relief["recessed_openings"] == openings, oid
        assert before[oid]["facade"]["relief"] == {"relief_storeys": 1000, "flat_openings": 0, "recessed_openings": openings}
        # The same openings are placed either way; only how they are drawn changes.
        assert record["facade"]["openings"] == before[oid]["facade"]["openings"]
        assert record["checks"]["facade_open_edges"] == 0
    total = sum(r["facade"]["relief"]["flat_openings"] for r in records.values())
    assert total > 0 and flat.stats["flat_openings"] == total and deep.stats["flat_openings"] == 0
    assert "facade_mesh_not_watertight" not in flat.report()["issues_by_code"]
    # The reveals a flat opening saves are in its wall's material since plan Q6, not in frame_stone.
    by_flat, by_deep = flat.stats["visual_triangles_by_material"], deep.stats["visual_triangles_by_material"]
    saved = {m: by_deep[m] - by_flat.get(m, 0) for m in by_deep if by_deep[m] != by_flat.get(m, 0)}
    assert "frame_stone" not in saved and all(n > 0 and n % 8 == 0 for n in saved.values()) and sum(saved.values()) == 8 * total
    assert flat.stats["visual_triangles"] == deep.stats["visual_triangles"] - 8 * total
    assert flat.stats["visual_triangles_by_material"]["glass_dark"] == deep.stats["visual_triangles_by_material"]["glass_dark"]
    assert flat.stats["collision_triangles"] == deep.stats["collision_triangles"]
    assert flat.stats["materials"] == deep.stats["materials"]
