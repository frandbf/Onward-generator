"""``onward-generator overture fetch|match`` command lines (offline)."""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import pytest

from onward_generator.cli import build_parser, main
from onward_generator.maps.workspace import create_map
from onward_generator.overture import __main__ as cli
from onward_generator.overture.acquire import FetchRecord, fetch_map
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON
from tests.overture_fixture import BLOCK, building, local_connect, place, scene, source, write_buildings, write_places


def _record(theme: str, rows: int, licenses: list[dict]) -> FetchRecord:
    return FetchRecord(
        provider="overture-maps-foundation", theme=theme, type=theme[:-1], release="2026-10-21.0", source="s3://x", query="q",
        bbox=[0, 0, 1, 1], bbox_order="south,west,north,east", retrieved_at="t", rows=rows, licenses=licenses, attribution=[],
        sha256="0" * 64, size_bytes=99, duckdb_version="1.5.6", path=f"/m/data/raw/overture_{theme}.parquet",
    )


def test_fetch_prints_how_many_files_it_read(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    record = dataclasses.replace(_record("buildings", 0, []), files=["s3://a", "s3://b"], files_in_theme=512)
    monkeypatch.setattr(cli, "fetch_map", lambda *a, **k: [record])
    assert cli.main(["fetch", "--map", "maps/x"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "overture buildings 2026-10-21.0: 0 rows, 99 bytes; licences none; files 2 of 512"


def test_fetch_prints_rows_and_licences(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict = {}

    def fake(directory: str, *, release: str | None, overwrite: bool, out: str | None) -> list[FetchRecord]:
        seen.update(directory=directory, release=release, overwrite=overwrite, out=out)
        return [_record("places", 3, [{"dataset": "meta", "license": "CDLA-Permissive-2.0", "records": 2},
                                      {"dataset": "AllThePlaces", "license": "CC0-1.0", "records": 1}]), _record("buildings", 0, [])]

    monkeypatch.setattr(cli, "fetch_map", fake)
    assert cli.main(["fetch", "--map", "maps/x", "--release", "2026-10-21.0", "--overwrite", "--out", "raw"]) == 0
    assert seen == {"directory": "maps/x", "release": "2026-10-21.0", "overwrite": True, "out": "raw"}
    assert capsys.readouterr().out.splitlines() == [
        "overture places 2026-10-21.0: 3 rows, 99 bytes; licences meta CDLA-Permissive-2.0 2, AllThePlaces CC0-1.0 1",
        "wrote /m/data/raw/overture_places.parquet",
        "overture buildings 2026-10-21.0: 0 rows, 99 bytes; licences none",
        "wrote /m/data/raw/overture_buildings.parquet",
    ]
    assert cli.main(["fetch", "--map", "maps/x"]) == 0
    assert seen == {"directory": "maps/x", "release": None, "overwrite": False, "out": None}


def test_fetch_failures_exit_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["fetch", "--map", str(tmp_path), "--release", "bad"]) == 1
    assert capsys.readouterr().err.startswith("overture: ")
    assert cli.main(["fetch"]) == 2


@pytest.fixture
def fetched(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "map"
    create_map(root, map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    places = write_places(tmp_path / "r" / "places.parquet", [place("p", 5, 5), place("q", 15, 5, confidence=0.1)])
    buildings = write_buildings(tmp_path / "r" / "buildings.parquet", [
        building("b", BLOCK["osm-way-1"], height=9.0, floors=3), building("c", BLOCK["osm-way-2"], height=None, floors=2,
                                                                         sources=[source("Microsoft ML Buildings", "ODbL-1.0")]),
    ])
    fetch_map(root, release="2026-10-21.0", sources={"places": str(places), "buildings": str(buildings)}, connect=local_connect)
    scene_path = tmp_path / "geo_scene.json"
    scene_path.write_text(json.dumps(scene(BLOCK)), encoding="utf-8")
    return root, scene_path


def test_match_writes_three_files_and_a_summary(fetched: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, scene_path = fetched
    out = tmp_path / "out"
    assert cli.main(["match", "--scene", str(scene_path), "--map", str(root), "--out", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "places=1 of 2 buildings matched=2 of 3 height=1 levels=2"
    assert lines[1:] == [f"wrote {out.resolve() / name}" for name in (cli.OVERRIDES_FILE, cli.PLACES_FILE, cli.REPORT_FILE)]
    report = json.loads((out / cli.REPORT_FILE).read_text(encoding="utf-8"))
    assert report["scene"]["path"] == str(scene_path.resolve()) and report["scene"]["schema_version"] == "0.2.0"
    assert len(report["scene"]["sha256"]) == 64
    assert report["buildings"]["counts"]["osm_buildings"] == 3
    assert json.loads((out / cli.PLACES_FILE).read_text(encoding="utf-8"))["counts"]["kept"] == 1
    assert len(json.loads((out / cli.OVERRIDES_FILE).read_text(encoding="utf-8"))["overrides"]) == 2
    assert cli.main(["match", "--scene", str(scene_path), "--map", str(root), "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert cli.main(["match", "--scene", str(scene_path), "--map", str(root), "--out", str(out), "--overwrite"]) == 0
    inside = out / cli.PLACES_FILE
    inside.write_text(scene_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.main(["match", "--scene", str(inside), "--map", str(root), "--out", str(out), "--overwrite"]) == 1
    assert "output would overwrite an input file" in capsys.readouterr().err


def test_match_refuses_bad_inputs(fetched: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, scene_path = fetched
    out = str(tmp_path / "out")
    assert cli.main(["match", "--scene", str(tmp_path / "none.json"), "--map", str(root), "--out", out]) == 1
    assert "scene not found" in capsys.readouterr().err
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"schema": "x"}), encoding="utf-8")
    assert cli.main(["match", "--scene", str(other), "--map", str(root), "--out", out]) == 1
    assert capsys.readouterr().err == "overture: input is not a onward_generator.geo_scene\n"
    bad_cfg = tmp_path / "cfg.json"
    bad_cfg.write_text("{}", encoding="utf-8")
    assert cli.main(["match", "--scene", str(scene_path), "--map", str(root), "--out", out, "--config", str(bad_cfg)]) == 1
    assert "cfg.json" in capsys.readouterr().err
    (root / "data" / "raw" / "overture_places.parquet.source.json").unlink()
    assert cli.main(["match", "--scene", str(scene_path), "--map", str(root), "--out", out]) == 1
    assert "missing provenance sidecar" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_match_stops_when_the_scene_changes_while_read(fetched: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    root, scene_path = fetched
    real = cli.match_buildings

    def touching(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        scene_path.write_text(scene_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return result

    monkeypatch.setattr(cli, "match_buildings", touching)
    assert cli.main(["match", "--scene", str(scene_path), "--map", str(root), "--out", str(tmp_path / "out")]) == 1
    assert capsys.readouterr().err == "overture: scene file changed while being read\n"


def _actions(parser: argparse.ArgumentParser) -> dict[str, tuple]:
    return {a.dest: (tuple(a.option_strings), a.default, a.required, a.help, a.nargs) for a in parser._actions if a.dest != "help"}


def test_the_overture_parser_is_described_as_documented() -> None:
    parser = cli.build_parser()
    assert parser.prog == "onward-generator overture"
    assert parser.description == "Overture Maps places and buildings (ADR-021)"
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert sub.required is True and sub.dest == "command"
    assert {c.dest: c.help for c in sub._choices_actions} == {
        "fetch": "clip Overture places and buildings to the map bbox (network)",
        "match": "Overture heights as overrides and places as POIs for a geo scene (offline)",
    }
    assert _actions(sub.choices["fetch"]) == {
        "map": (("--map",), None, True, "map directory containing map.json", None),
        "release": (("--release",), None, False, "Overture release, YYYY-MM-DD.N (default: the STAC catalogue's latest)", None),
        "out": (("--out",), None, False,
                "directory for overture_places.parquet and overture_buildings.parquet (default: the map's data/raw, where overture match reads them)", None),
        "overwrite": (("--overwrite",), False, False, "replace data/raw/overture_*.parquet", 0),
    }
    assert _actions(sub.choices["match"]) == {
        "scene": (("--scene",), None, True, "geo_scene.json (after cadastre split when Catastro is on)", None),
        "map": (("--map",), None, True, "map directory holding data/raw/overture_*.parquet", None),
        "out": (("--out",), None, True, "output directory", None),
        "config": (("--config",), None, False, "Overture configuration (default: config/overture.default.json)", None),
        "overwrite": (("--overwrite",), False, False, None, 0),
    }
    assert sub.choices["fetch"].get_default("handler") is cli._fetch
    assert sub.choices["match"].get_default("handler") is cli._match


def test_the_main_parser_lists_overture_and_the_new_flags() -> None:
    from onward_generator.detail.__main__ import build_parser as detail_parser

    main_parser = build_parser()
    sub = next(a for a in main_parser._actions if isinstance(a, argparse._SubParsersAction))
    assert {c.dest: c.help for c in sub._choices_actions}["overture"] == (
        "Overture Maps places and buildings: fetch | match (see: overture --help)"
    )
    assert _actions(sub.choices["heights"])["secondary_overrides"] == (
        ("--secondary-overrides",), None, False,
        "third-party overrides JSON (overture match), ranked below --overrides and an OSM height, above building:levels", None,
    )
    assert _actions(detail_parser())["places"] == (
        ("--places",), None, False, "overture_places.json from overture match: shop evidence ranked by config/sources.json evidence (ADR-021)", None,
    )


def test_the_overture_command_is_dispatched(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["overture", "--help"]) == 0
    assert "fetch" in capsys.readouterr().out
    assert main(["overture"]) == 2
    assert "overture" in build_parser().format_help()
