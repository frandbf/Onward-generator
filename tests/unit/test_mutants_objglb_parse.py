"""OBJ and MTL parsing for the Asset Store converter: exact values, edge lines and the errors a caller sees."""

from __future__ import annotations

import numpy as np
import pytest

from onward_generator.assetstore.objglb import DEFAULT_KD, ObjError, parse_mtl, parse_obj, srgb

# ---- srgb -------------------------------------------------------------------------------------------------------------


def test_srgb_clamps_to_the_unit_range() -> None:
    assert srgb(-1.0) == 0.0
    assert srgb(2.0) == pytest.approx(1.0, rel=1e-12)


def test_srgb_is_linear_up_to_and_including_the_threshold() -> None:
    assert srgb(0.001) == pytest.approx(0.01292, rel=1e-12)
    assert srgb(0.0031308) == pytest.approx(12.92 * 0.0031308, rel=1e-12)


def test_srgb_follows_the_power_curve_above_the_threshold() -> None:
    assert srgb(0.5) == pytest.approx(0.7353569830524495, rel=1e-12)
    assert srgb(0.8) == pytest.approx(0.9063317533440594, rel=1e-12)


# ---- parse_mtl --------------------------------------------------------------------------------------------------------


def test_parse_mtl_ignores_lines_before_the_first_material() -> None:
    materials = parse_mtl("Kd 1 0 0\nmap_Kd early.png\nnewmtl a\nKd 0.1 0.2 0.3\n")
    assert materials == {"a": materials["a"]}
    assert materials["a"].kd == (0.1, 0.2, 0.3) and materials["a"].texture is None


def test_parse_mtl_reads_every_material_across_blank_lines_and_keeps_spaced_names() -> None:
    materials = parse_mtl("newmtl first one\nKd 0.1 0.2 0.3\n\nnewmtl second\n   \nKd 0.4 0.5 0.6\n")
    assert sorted(materials) == ["first one", "second"]
    assert materials["first one"].kd == (0.1, 0.2, 0.3)
    assert materials["second"].kd == (0.4, 0.5, 0.6)


def test_parse_mtl_defaults_and_ignores_other_statements() -> None:
    material = parse_mtl("newmtl a\nNs 10\nKd 1 1\nd 1\n")["a"]
    assert material.kd == DEFAULT_KD
    assert material.texture is None and material.scale == (1.0, 1.0) and material.offset == (0.0, 0.0)


def test_parse_mtl_keeps_a_texture_name_with_spaces() -> None:
    assert parse_mtl("newmtl a\nmap_Kd my tex.png\n")["a"].texture == "my tex.png"
    assert parse_mtl("newmtl a\nmap_Kd -o 0.25 0.5 0 my tex.png\n")["a"].texture == "my tex.png"


def test_parse_mtl_reads_an_option_flag_left_without_values_as_the_name() -> None:
    assert parse_mtl("newmtl a\nmap_Kd -s\n")["a"].texture == "-s"
    material = parse_mtl("newmtl a\nmap_Kd -s 2 3 1 -o\n")["a"]
    assert material.scale == (2.0, 3.0) and material.texture == "-o"


def test_parse_mtl_does_not_read_a_short_option_as_a_texture_name() -> None:
    with pytest.raises(ObjError, match="^map option -s needs two values$"):
        parse_mtl("newmtl a\nmap_Kd -s 2\n")


# ---- parse_obj --------------------------------------------------------------------------------------------------------

TRI = "v 0 0 0\nv 2 0 0\nv 0 2 0\n"


def test_faces_before_any_usemtl_go_to_the_default_material() -> None:
    assert list(parse_obj(TRI + "f 1 2 3\n")) == ["default"]


def test_usemtl_keeps_a_spaced_name_and_blank_lines_do_not_stop_parsing() -> None:
    groups = parse_obj(TRI + "\nusemtl brick wall\n\nf 1 2 3\n\nusemtl other\nf 3 2 1\n")
    assert sorted(groups) == ["brick wall", "other"]


def test_a_short_face_names_its_line() -> None:
    with pytest.raises(ObjError, match=r"^line 4: a face needs three corners$"):
        parse_obj(TRI + "f 1 2\n")


def test_vertices_normals_and_uvs_read_only_their_own_components() -> None:
    text = "v 0 0 0 1\nv 1 0 0 1\nv 0 1 0 1\nvn 0.6 0.8 0 9\nvt 0.25 0.75 0\nvt 0.5 0.125 0\nvt 1 1 0\nf 1/1/1 2/2/1 3/3/1\n"
    group = parse_obj(text)["default"]
    assert group.positions.tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    assert np.allclose(group.normals, [[0.6, 0.8, 0.0]] * 3)
    assert group.normals.shape == (3, 3)
    assert group.uvs.tolist() == [[0.25, 0.75], [0.5, 0.125], [1.0, 1.0]]


def test_groups_are_float32() -> None:
    group = parse_obj(TRI + "vn 0 0 1\nvt 0 0\nf 1/1/1 2/1/1 3/1/1\n")["default"]
    assert (group.positions.dtype, group.normals.dtype, group.uvs.dtype) == (np.float32, np.float32, np.float32)


def test_a_missing_normal_is_the_unit_face_normal() -> None:
    group = parse_obj(TRI + "f 1 2 3\n")["default"]
    assert group.normals.tolist() == [[0.0, 0.0, 1.0]] * 3


def test_a_degenerate_face_gets_an_up_normal() -> None:
    group = parse_obj("v 0 0 0\nv 1 0 0\nv 2 0 0\nf 1 2 3\n")["default"]
    assert group.normals.tolist() == [[0.0, 1.0, 0.0]] * 3


def test_uvs_come_from_the_uv_field_and_normals_from_the_normal_field() -> None:
    text = TRI + "vt 0.25 0.5\nvt 0.75 0.5\nvt 0.5 1\nvn 0 1 0\n"
    with_uv = parse_obj(text + "f 1/1 2/2 3/3\n")["default"]
    assert with_uv.uvs.tolist() == [[0.25, 0.5], [0.75, 0.5], [0.5, 1.0]]
    assert with_uv.normals.tolist() == [[0.0, 0.0, 1.0]] * 3
    with_normal = parse_obj(text + "f 1//1 2//1 3//1\n")["default"]
    assert with_normal.uvs.tolist() == [[0.5, 0.5]] * 3
    assert with_normal.normals.tolist() == [[0.0, 1.0, 0.0]] * 3
    both = parse_obj(text + "f 1/1/1 2/2/1 3/3/1\n")["default"]
    assert both.normals.tolist() == [[0.0, 1.0, 0.0]] * 3


def test_negative_indices_count_from_the_end() -> None:
    group = parse_obj("v 9 9 9\n" + TRI + "vt 0.25 0.5\nvn 0 1 0\nf -3/-1/-1 -2/-1/-1 -1/-1/-1\n")["default"]
    assert group.positions.tolist() == [[0, 0, 0], [2, 0, 0], [0, 2, 0]]
    assert group.uvs.tolist() == [[0.25, 0.5]] * 3


def test_an_index_one_past_the_end_is_out_of_range() -> None:
    with pytest.raises(ObjError, match=r"^face index 4 is out of range$"):
        parse_obj(TRI + "f 1 2 4\n")
    with pytest.raises(ObjError, match=r"^face index -4 is out of range$"):
        parse_obj(TRI + "f 1 2 -4\n")


def test_a_corner_without_a_vertex_is_named() -> None:
    with pytest.raises(ObjError, match=r"^face corner '/1' has no vertex$"):
        parse_obj(TRI + "vt 0 0\nf /1 2 3\n")


def test_a_map_option_with_its_two_values_and_no_name_is_read() -> None:
    material = parse_mtl("newmtl a\nmap_Kd -s 2 3\n")["a"]
    assert material.scale == (2.0, 3.0) and material.texture == ""
