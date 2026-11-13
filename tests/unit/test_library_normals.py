"""Plan T3 (ADR-039): the library normal maps a scene lists, as materials apply binds them and export package writes them."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.export.library_normals import (
    MAX_PX,
    NORMAL_MAP_PREFIX,
    NORMAL_MAP_ROLE,
    NORMAL_MAPS_MAX_BYTES,
    LibraryNormalError,
    normal_map_files,
    scene_normal_maps,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.materials.apply import normal_map_bindings
from onward_generator.materials.library_plan import WIDE_LIBRARY_PATH, library_rows

WIDE = {row["id"]: row for row in library_rows(WIDE_LIBRARY_PATH)}


def _binding(library_id: str) -> dict[str, str]:
    normal = WIDE[library_id]["normal"]
    return {"library_id": library_id, "path": normal["path"], "sha256": normal["sha256"]}


def test_the_constants() -> None:
    assert (NORMAL_MAP_ROLE, NORMAL_MAP_PREFIX, MAX_PX, NORMAL_MAPS_MAX_BYTES) == ("normal_map", "normal_", 1024, 24 * 1024 * 1024)


def test_a_party_wall_and_a_tint_take_their_base_s_map_and_trim_and_bare_photographs_do_not() -> None:
    applied = [{"asset_id": "ph_clean_asphalt", "color_id": "carriageway"}, {"asset_id": "ph_yellow_plaster", "color_id": "plaster_ochre"},
               {"asset_id": "acg_concrete012", "color_id": "sidewalk"}]
    used = ["carriageway", "plaster_ochre", "party_plaster_ochre", "plaster_ochre__tint_ff0000", "sidewalk", "facade_trim_x", "glass_dark"]
    block = normal_map_bindings(applied, used, ["facade_trim_x"])
    assert block == {"enabled": True, "without": ["acg_concrete012"], "materials": {
        "carriageway": _binding("ph_clean_asphalt"), "party_plaster_ochre": _binding("ph_yellow_plaster"),
        "plaster_ochre": _binding("ph_yellow_plaster"), "plaster_ochre__tint_ff0000": _binding("ph_yellow_plaster")}}
    trimmed = normal_map_bindings([{"asset_id": "ph_clean_asphalt", "color_id": "facade_trim_x"}], ["facade_trim_x"], ["facade_trim_x"])
    assert trimmed["materials"] == {}  # a trim facade keeps ADR-033


def test_each_map_is_written_once_at_most_1024_px_from_the_checked_file(tmp_path: Path) -> None:
    pngs, block = normal_map_files({"a": _binding("ph_clean_asphalt"), "b": _binding("ph_clean_asphalt"), "c": _binding("ph_worn_asphalt")})
    assert sorted(pngs) == ["normal_ph_clean_asphalt.png", "normal_ph_worn_asphalt.png"]
    assert block["materials"] == {"a": "normal_ph_clean_asphalt.png", "b": "normal_ph_clean_asphalt.png",
                                  "c": "normal_ph_worn_asphalt.png"}
    assert [f["library_id"] for f in block["files"]] == ["ph_clean_asphalt", "ph_worn_asphalt"]
    assert block["astc6x6_bytes"] == sum(f["astc6x6_bytes"] for f in block["files"])
    big = tmp_path / "assets" / "big.jpg"
    big.parent.mkdir(parents=True)
    Image.new("RGB", (2048, 1024), (128, 128, 255)).save(big, quality=95)
    binding = {"library_id": "big", "path": "assets/big.jpg", "sha256": hashlib.sha256(big.read_bytes()).hexdigest()}
    pngs, block = normal_map_files({"x": binding}, repo=tmp_path)
    with Image.open(io.BytesIO(pngs["normal_big.png"])) as image:
        assert image.size == (1024, 512) and image.mode == "RGB"
    assert (block["files"][0]["width"], block["files"][0]["height"]) == (1024, 512)
    with pytest.raises(LibraryNormalError, match="x: normal map assets/big.jpg does not match the library hash"):
        normal_map_files({"x": {**binding, "sha256": "0" * 64}}, repo=tmp_path)
    with pytest.raises(LibraryNormalError, match="x: normal map assets/gone.jpg is missing"):
        normal_map_files({"x": {**binding, "path": "assets/gone.jpg"}}, repo=tmp_path)
    with pytest.raises(LibraryNormalError, match="x is both the lit_normal_material and a library normal-map material"):
        normal_map_files({"x": binding}, lit_normal_material="x", repo=tmp_path)
    assert normal_map_files({"x": binding}, lit_normal_material="y", repo=tmp_path)[1]["materials"] == {"x": "normal_big.png"}


def test_the_list_is_read_from_the_visual_glb() -> None:
    quad = Mesh(np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], float), np.array([[0, 1, 2]]))
    nodes = [Node("n", (Primitive(quad, "carriageway"),))]
    plain = build_glb(nodes, {"carriageway": Material((1, 1, 1, 1), 0.9)})
    assert scene_normal_maps(plain) == {}
    listed = build_glb(nodes, {"carriageway": Material((1, 1, 1, 1), 0.9)},
                       extras={"materials": {"normal_maps": {"carriageway": _binding("ph_clean_asphalt")}}})
    assert scene_normal_maps(listed) == {"carriageway": _binding("ph_clean_asphalt")}


def test_a_rebuilt_glb_carries_the_list_and_only_the_list() -> None:
    from onward_generator.export.library_normals import carried_normal_maps

    quad = Mesh(np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], float), np.array([[0, 1, 2]]))
    nodes = [Node("n", (Primitive(quad, "carriageway"),))]
    plain = build_glb(nodes, {"carriageway": Material((1, 1, 1, 1), 0.9)}, extras={"materials": {"applied": ["x"]}})
    assert carried_normal_maps(plain) == {}
    listed = build_glb(nodes, {"carriageway": Material((1, 1, 1, 1), 0.9)},
                       extras={"materials": {"applied": ["x"], "normal_maps": {"carriageway": _binding("ph_clean_asphalt")}}})
    assert carried_normal_maps(listed) == {"materials": {"normal_maps": {"carriageway": _binding("ph_clean_asphalt")}}}


def test_a_photograph_missing_from_the_catalogue_has_no_normal_map() -> None:
    block = normal_map_bindings([{"asset_id": "polyhaven_old_id", "color_id": "sidewalk"}], ["sidewalk"], [])
    assert block == {"enabled": True, "materials": {}, "without": ["polyhaven_old_id"]}


def test_a_grey_or_large_map_is_written_as_rgb_scaled_smoothly_and_the_limit_is_inclusive(tmp_path: Path,
                                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.export import library_normals
    from onward_generator.export.normal_map import astc6x6_bytes

    folder = tmp_path / "assets"
    folder.mkdir()
    rng = np.random.default_rng(3)
    Image.fromarray(rng.integers(0, 256, (600, 1400), dtype=np.uint8), "L").save(folder / "grey.png")
    data = (folder / "grey.png").read_bytes()
    binding = {"library_id": "grey", "path": "assets/grey.png", "sha256": hashlib.sha256(data).hexdigest()}
    pngs, block = normal_map_files({"x": binding}, repo=tmp_path)
    expected = Image.open(folder / "grey.png").convert("RGB")
    expected.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    with Image.open(io.BytesIO(pngs["normal_grey.png"])) as image:
        assert image.mode == "RGB" and image.size == (1024, 439) and np.array_equal(np.asarray(image), np.asarray(expected))
    monkeypatch.setattr(library_normals, "NORMAL_MAPS_MAX_BYTES", astc6x6_bytes(1024, 439))
    assert library_normals.normal_map_files({"x": binding}, repo=tmp_path)[1]["limit_bytes"] == astc6x6_bytes(1024, 439)


def test_the_materials_cli_says_none_for_an_empty_list(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                        tmp_path: Path) -> None:
    import onward_generator.materials.__main__ as cli

    report = {"applied": [], "skipped": [], "brick_nodes": [], "cobble_applied": False,
              "normal_maps": {"enabled": True, "materials": {}, "without": []}}
    monkeypatch.setattr(cli, "apply_materials", lambda *args, **kwargs: report)
    monkeypatch.setattr(cli, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "after_materials", lambda out: None)
    assert cli.main(["apply", "--base", str(tmp_path), "--out", str(tmp_path / "o"), "--normal-maps"]) == 0
    assert "normal maps: 0 materials (none); without one: none\n" in capsys.readouterr().out
