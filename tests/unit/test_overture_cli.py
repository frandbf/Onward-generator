"""``onward-generator overture fetch`` command line (offline)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from onward_generator.cli import build_parser, main
from onward_generator.overture import __main__ as cli
from onward_generator.overture.acquire import FetchRecord


def _record(theme: str, rows: int, licenses: list[dict]) -> FetchRecord:
    return FetchRecord(
        provider="overture-maps-foundation", theme=theme, type=theme[:-1], release="2026-10-21.0", source="s3://x", query="q",
        bbox=[0, 0, 1, 1], bbox_order="south,west,north,east", retrieved_at="t", rows=rows, licenses=licenses, attribution=[],
        sha256="0" * 64, size_bytes=99, duckdb_version="1.5.6", path=f"/m/data/raw/overture_{theme}.parquet",
    )


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
    }
    assert _actions(sub.choices["fetch"]) == {
        "map": (("--map",), None, True, "map directory containing map.json", None),
        "release": (("--release",), None, False, "Overture release, YYYY-MM-DD.N (default: the STAC catalogue's latest)", None),
        "out": (("--out",), None, False,
                "directory for overture_places.parquet and overture_buildings.parquet (default: the map's data/raw, where overture match reads them)", None),
        "overwrite": (("--overwrite",), False, False, "replace data/raw/overture_*.parquet", 0),
    }
    assert sub.choices["fetch"].get_default("handler") is cli._fetch


def test_the_main_parser_lists_overture() -> None:
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert {c.dest: c.help for c in sub._choices_actions}["overture"] == (
        "Overture Maps places and buildings: fetch (see: overture --help)"
    )


def test_the_overture_command_is_dispatched(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["overture", "--help"]) == 0
    assert "fetch" in capsys.readouterr().out
    assert main(["overture"]) == 2
    assert "overture" in build_parser().format_help()
