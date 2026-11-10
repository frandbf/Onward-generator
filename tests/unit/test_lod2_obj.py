"""Plan D6: reading the LoD2 OBJ files and their block ZIPs (``detail.lod2.parse_obj``, ``read_obj_zip``, ``load_index``)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from onward_generator.detail.lod2 import Lod2Error, Lod2Settings, load_index, parse_obj, read_obj_zip
from tests.lod2_fixture import HEADER, box_group, obj_text, obj_zip, write_index

ORIGIN = (440000.0, 4474000.0)
TRIANGLE = "v 1 2 3\nv 4 5 6\nv 7 8 10\nf 1 2 3\n"


def test_parse_reads_vertices_and_triangles_in_every_face_token_form() -> None:
    mesh = parse_obj("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1/1 2/2/2 3/3/3\nf 1//4 3//4 4//4\nf -4 -2 -1\n")
    assert mesh.vertices.tolist() == [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    assert mesh.vertices.dtype == np.float64
    assert [g.name for g in mesh.groups] == ["default"]
    assert mesh.groups[0].triangles.tolist() == [[0, 1, 2], [0, 2, 3], [0, 2, 3]]
    assert mesh.groups[0].triangles.dtype == np.int64
    assert mesh.crs is None


def test_parse_ignores_comments_blank_lines_and_unused_statements() -> None:
    text = "# a comment\n\n   \nmtllib materials.mtl\no thing\ng part\ns off\nvn 0 0 1\nvt 0 0\n" + TRIANGLE
    mesh = parse_obj(text)
    assert mesh.groups[0].triangles.tolist() == [[0, 1, 2]]
    assert mesh.vertices[2].tolist() == [7.0, 8.0, 10.0]


def test_usemtl_groups_split_the_triangles_in_file_order_and_merge_a_repeated_name() -> None:
    text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl b\nf 1 2 3\nusemtl a\nf 3 2 1\nusemtl b\nf 2 3 1\n"
    mesh = parse_obj(text)
    assert [g.name for g in mesh.groups] == ["b", "a"]
    assert mesh.groups[0].triangles.tolist() == [[0, 1, 2], [1, 2, 0]]
    assert mesh.groups[1].triangles.tolist() == [[2, 1, 0]]


def test_a_quad_is_fan_triangulated() -> None:
    mesh = parse_obj("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0 2 0\nf 1 2 3 4 5\n")
    assert mesh.groups[0].triangles.tolist() == [[0, 1, 2], [0, 2, 3], [0, 3, 4]]


def test_the_header_names_the_last_epsg_authority_and_crlf_lines_parse() -> None:
    mesh = parse_obj(HEADER.replace("{epsg}", "25830") + "\r\n" + TRIANGLE.replace("\n", " \r\n"))
    assert mesh.crs == "EPSG:25830"
    assert mesh.groups[0].triangles.tolist() == [[0, 1, 2]]
    assert parse_obj("# COORDINATE_SYSTEM: none\n" + TRIANGLE).crs is None


@pytest.mark.parametrize(("text", "message"), [
    ("v 1 2\nf 1 1 1\n", "OBJ line 1: a vertex needs x y z"),
    ("v 1 2 3 4\n", "OBJ line 1: a vertex needs x y z"),
    ("v a b c\n", "OBJ line 1: vertex"),
    ("v 1 nan 3\n", "OBJ line 1: vertex is not finite"),
    ("v 1 2 inf\n", "OBJ line 1: vertex is not finite"),
    ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n", "OBJ line 4: face index 0"),
    ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 4\n", "OBJ line 4: face index 4 is outside the 3 vertices"),
    ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 -4\n", "OBJ line 4: face index -4 is outside"),
    ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 x 3\n", "OBJ line 4: face index 'x' is not an integer"),
    ("v 0 0 0\nv 1 0 0\nf 1 2\n", "OBJ line 3: a face needs at least 3 vertices"),
    ("f 1 2 3\n", "OBJ line 1: a face before any vertex"),
    ("v 0 0 0\nl 1 2\n", "OBJ line 2: unknown directive 'l'"),
    ("v 0 0 0\nusemtl\n", "OBJ line 2: usemtl needs one name"),
    ("", "OBJ has no vertices"),
    ("# only a comment\n", "OBJ has no vertices"),
    ("v 0 0 0\nv 1 0 0\nv 0 1 0\n", "OBJ has no faces"),
])
def test_a_malformed_obj_is_refused_with_its_line(text: str, message: str) -> None:
    with pytest.raises(Lod2Error) as caught:
        parse_obj(text)
    assert str(caught.value).startswith(message)


def test_vertex_and_face_caps() -> None:
    parse_obj(TRIANGLE, max_vertices=3, max_faces=1)
    with pytest.raises(Lod2Error, match="more than 2 vertices"):
        parse_obj(TRIANGLE, max_vertices=2)
    with pytest.raises(Lod2Error, match="more than 1 triangles"):
        parse_obj("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n", max_faces=1)


def _zip(members: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


VALID = obj_text([("g1", box_group(0, 0, 10, 10, 12))], ORIGIN)


def test_read_obj_zip_parses_only_the_obj_and_dates_it() -> None:
    mesh, date = read_obj_zip(obj_zip(VALID))
    assert date == "2026-10-29"
    assert mesh.crs == "EPSG:25830"
    assert [g.name for g in mesh.groups] == ["g1"]
    assert len(mesh.groups[0].triangles) == 10
    assert mesh.vertices[:, 0].min() == ORIGIN[0]


@pytest.mark.parametrize(("body", "message"), [
    (_zip({}), "the ZIP holds 0 .obj files"),
    (_zip({"materials.mtl": "newmtl a"}), "the ZIP holds 0 .obj files"),
    (_zip({"a.obj": VALID, "b.OBJ": VALID}), "the ZIP holds 2 .obj files"),
    (_zip({"../evil.obj": VALID}), "ZIP member '../evil.obj' leaves the archive"),
    (_zip({"/abs.obj": VALID}), "ZIP member '/abs.obj' leaves the archive"),
    (_zip({"a\\..\\b.obj": VALID}), "leaves the archive"),
    (b"PK\x03\x04 not really a zip", "not a valid ZIP file"),
    (b"", "not a valid ZIP file"),
    (_zip({"a.obj": b"\xff\xfe\x00"}), "a.obj is not UTF-8 text"),
])
def test_read_obj_zip_refuses_a_bad_archive(body: bytes, message: str) -> None:
    with pytest.raises(Lod2Error, match=message.replace("\\", "\\\\").replace(".", r"\.").replace("(", r"\(")):
        read_obj_zip(body)


def test_read_obj_zip_refuses_an_oversized_member() -> None:
    body = obj_zip(VALID)
    size = len(VALID.encode())
    assert read_obj_zip(body, Lod2Settings(max_unzipped_bytes=size))[0].crs == "EPSG:25830"
    with pytest.raises(Lod2Error, match=f"is {size} bytes unzipped, over {size - 1}"):
        read_obj_zip(body, Lod2Settings(max_unzipped_bytes=size - 1))


def test_read_obj_zip_refuses_a_member_whose_declared_size_lies() -> None:
    body = bytearray(obj_zip(VALID))
    # The central directory's uncompressed size of the .obj (offset 24 in its header) understates the member.
    central = body.rfind(b"PK\x01\x02", 0, body.rfind(b"materials.mtl"))
    body[central + 24:central + 28] = (10).to_bytes(4, "little")
    with pytest.raises(Lod2Error):
        read_obj_zip(bytes(body), Lod2Settings(max_unzipped_bytes=100))


def test_read_obj_zip_caps_vertices_and_faces() -> None:
    with pytest.raises(Lod2Error, match="more than 7 vertices"):
        read_obj_zip(obj_zip(VALID), Lod2Settings(max_vertices=7))
    with pytest.raises(Lod2Error, match="more than 9 triangles"):
        read_obj_zip(obj_zip(VALID), Lod2Settings(max_faces=9))


@pytest.mark.parametrize("epsg", [25831, 4258, None])
def test_read_obj_zip_needs_the_25830_header(epsg: int | None) -> None:
    text = obj_text([("g1", box_group(0, 0, 10, 10, 12))], ORIGIN, epsg=epsg)
    named = f"EPSG:{epsg}" if epsg else "no CRS"
    with pytest.raises(Lod2Error, match=f"the COORDINATE_SYSTEM header names {named}, not EPSG:25830"):
        read_obj_zip(obj_zip(text))


def _index(tmp_path: Path, **changes: object) -> Path:
    return write_index(tmp_path / "madrid_lod2", {"M-000002": obj_zip(VALID), "M-000001": obj_zip(VALID)}, **changes)


def test_load_index_checks_every_block_and_sorts_them(tmp_path: Path) -> None:
    path = _index(tmp_path)
    index = load_index(path)
    assert [b.cod_manzan for b in index.blocks] == ["M-000001", "M-000002"]
    assert (index.source, index.license, index.crs, index.data_date) == ("es.madrid_lod2", "CC-BY-4.0", "EPSG:25830", "2026-07-23")
    assert index.attribution_line() == {"text": index.attribution, "license": "CC-BY-4.0",
                                        "license_url": "https://creativecommons.org/licenses/by/4.0/"}
    assert index.blocks[0].obj_date == "2026-10-29" and index.blocks[0].url.endswith("M-000001.zip")
    assert len(index.sha256) == 64 and index.path == str(path)
    # The index lists them in reverse: the order comes from the codes.
    doc = json.loads(path.read_bytes())
    doc["blocks"].reverse()
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert [b.cod_manzan for b in load_index(path).blocks] == ["M-000001", "M-000002"]


@pytest.mark.parametrize(("changes", "message"), [
    ({"schema": "onward_generator.other"}, "is not onward_generator.lod2_landmarks"),
    ({"license": "CC-BY-SA-4.0"}, "licence CC-BY-SA-4.0 is not CC-BY-4.0"),
    ({"crs": "EPSG:25831"}, "CRS EPSG:25831 is not EPSG:25830"),
    ({"attribution": ""}, "the index needs source, provider, license, attribution, data_date, crs"),
    ({"blocks": {}}, "blocks must be a list"),
    ({"blocks": [{"cod_manzan": "M-1"}]}, "a block needs cod_manzan, path, sha256 and url"),
    ({"blocks": [{"cod_manzan": "M-1", "path": "../M-000001.zip", "sha256": "0", "url": "u"}]}, "leaves the index folder"),
    ({"blocks": [{"cod_manzan": "M-1", "path": "M-9.zip", "sha256": "0", "url": "u"}]}, r"block M-1 is missing \(M-9.zip\)"),
    ({"blocks": [{"cod_manzan": "M-1", "path": "M-000001.zip", "sha256": "0" * 64, "url": "u"}]}, "block M-1 does not match its sha256"),
])
def test_load_index_refuses(tmp_path: Path, changes: dict[str, object], message: str) -> None:
    (tmp_path / "M-000001.zip").write_bytes(b"outside the folder")
    with pytest.raises(Lod2Error, match=message):
        load_index(_index(tmp_path, **changes))


def test_load_index_refuses_a_missing_file_non_json_and_a_bad_block(tmp_path: Path) -> None:
    with pytest.raises(Lod2Error, match="not found; rerun source madrid-lod2"):
        load_index(tmp_path / "nothing.json")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(Lod2Error, match="is not JSON"):
        load_index(tmp_path / "bad.json")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    with pytest.raises(Lod2Error, match="is not onward_generator.lod2_landmarks"):
        load_index(tmp_path / "list.json")
    path = write_index(tmp_path / "empty", {"M-000001": _zip({})})
    with pytest.raises(Lod2Error, match="block M-000001: the ZIP holds 0 .obj files"):
        load_index(path)
