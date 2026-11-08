"""``detail --overture-tints``: the colours of ``overture match --tints`` are checked before they are read (plan C2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.detail.__main__ import load_tints, main
from onward_generator.detail.scene import DetailError
from tests.overture_fixture import BLOCK, frame, scene

LINE = {"text": "Esri Community Maps contributors; Overture Maps Foundation", "license": "CC-BY-4.0", "license_url": ""}


def _lend(**changes: Any) -> dict[str, Any]:
    return {"colour": "#c88c3c", "dataset": "Esri Community Maps", "license": "CC-BY-4.0", "origin": "observed", "attribution": LINE} | changes


def _entry(**changes: Any) -> dict[str, Any]:
    return {"building_id": "osm-way-1", "gers_id": "g", "iou": 0.9, "facade": _lend()} | changes


def _files(tmp_path: Path, tints: list[Any], **doc: Any) -> tuple[str, str]:
    scene_path = tmp_path / "geo_scene.json"
    scene_path.write_text(json.dumps(scene(BLOCK)), encoding="utf-8")
    path = tmp_path / "overture_tints.json"
    document = {"schema": "onward_generator.overture_tints", "schema_version": "0.1.0", "frame": frame().to_dict(), "tints": tints} | doc
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path), str(scene_path)


def test_no_path_reads_nothing_and_a_valid_file_reads_its_entries(tmp_path: Path) -> None:
    assert load_tints(None, "unused.json") is None
    entries = [_entry(), _entry(building_id="osm-way-2", facade=None) | {"roof": _lend(colour="#b43c28")}]
    entries[1].pop("facade")
    path, scene_path = _files(tmp_path, entries)
    assert load_tints(path, scene_path) == entries
    path, scene_path = _files(tmp_path, [])
    assert load_tints(path, scene_path) == []
    legacy, scene_path = _files(tmp_path, [_entry()], schema="murcia_generator.overture_tints")
    assert load_tints(legacy, scene_path) == [_entry()]


@pytest.mark.parametrize(("entry", "message"), [
    ("x", "an entry needs building_id, gers_id and iou"),
    (_entry(gers_id=7), "an entry needs building_id, gers_id and iou"),
    (_entry(iou="0.9"), "an entry needs building_id, gers_id and iou"),
    (_entry(building_id="osm-way-9"), "building 'osm-way-9' is not in the scene"),
    ({"building_id": "osm-way-1", "gers_id": "g", "iou": 1}, "building osm-way-1 lends no colour"),
    (_entry(facade="#fff"), "building osm-way-1: a colour needs colour, dataset, license, origin, attribution"),
    (_entry(facade=_lend(colour=None)), "building osm-way-1: a colour needs colour, dataset, license, origin, attribution"),
    (_entry(roof=_lend(attribution=None)), "building osm-way-1: a colour needs colour, dataset, license, origin, attribution"),
    (_entry(facade=_lend(origin="estimated")), "building osm-way-1: origin 'estimated' is not observed"),
    (_entry(roof=_lend(origin="ai_generated")), "building osm-way-1: origin 'ai_generated' is not observed"),
])
def test_a_malformed_entry_is_refused(tmp_path: Path, entry: Any, message: str) -> None:
    path, scene_path = _files(tmp_path, [entry])
    with pytest.raises(DetailError) as raised:
        load_tints(path, scene_path)
    assert str(raised.value) == f"{path}: {message}"


def test_another_schema_or_grid_or_a_missing_file_is_refused(tmp_path: Path) -> None:
    path, scene_path = _files(tmp_path, [_entry()], schema="onward_generator.overture_places")
    with pytest.raises(DetailError, match=r"is not onward_generator.overture_tints$"):
        load_tints(path, scene_path)
    for key, value in (("crs", "EPSG:32630"), ("origin_projected", [0.0, 0.0])):
        path, scene_path = _files(tmp_path, [_entry()], frame={**frame().to_dict(), key: value})
        with pytest.raises(DetailError) as raised:
            load_tints(path, scene_path)
        assert str(raised.value) == f"{path} was made for another local grid than {scene_path}"
    missing = str(tmp_path / "none.json")
    with pytest.raises(DetailError) as raised:
        load_tints(missing, scene_path)
    assert str(raised.value) == f"{missing} not found; rerun overture match --tints --overwrite"


def test_the_cli_exits_one_before_writing_anything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, scene_path = _files(tmp_path, [])
    missing = tmp_path / "none.json"
    out = tmp_path / "detail"
    args = ["--scene", scene_path, "--heights", str(tmp_path / "h.json"), "--base", str(tmp_path / "b"), "--no-preview",
            "--overture-tints", str(missing), "--out", str(out)]
    assert main(args) == 1
    assert capsys.readouterr().err == f"detail: {missing} not found; rerun overture match --tints --overwrite\n"
    assert not out.exists()


def test_a_scene_without_features_has_no_building_to_tint(tmp_path: Path) -> None:
    path, _ = _files(tmp_path, [])
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"frame": frame().to_dict()}), encoding="utf-8")
    assert load_tints(path, str(bare)) == []


def test_the_budget_core_reads_the_tints_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import onward_generator.detail.__main__ as cli

    path, scene_path = _files(tmp_path, [_entry()])
    seen: dict[str, Any] = {}

    def budget(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise DetailError("stop")

    monkeypatch.setattr(cli, "load_base", lambda *args: (None, {}))
    monkeypatch.setattr(cli, "detail_scene_budget", budget)
    args = ["--scene", scene_path, "--heights", str(tmp_path / "h.json"), "--base", str(tmp_path / "b"), "--no-preview",
            "--core-radius-m", "100", "--core-target-triangles", "20000", "--overture-tints", path, "--out", str(tmp_path / "d")]
    assert main(args) == 1
    assert capsys.readouterr().err == "detail: stop\n"
    assert seen["tints"] == [_entry()]
