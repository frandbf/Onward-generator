"""Source plugins: registry, configuration, switches, priority merge and run planning (ADR-018)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from onward_generator.cli import main
from onward_generator.maps.workspace import create_map, store_sources
from onward_generator.pipeline_run import PipelineError, _canon, _input_markers, _occupied, output_ready, plan_steps, source_plan
from onward_generator.sources.es.catastro import CatastroPlugin
from onward_generator.sources.merge import merge_overrides
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceError, SourceStage, SourceSteps
from onward_generator.sources.registry import (
    PLUGINS,
    SourceChoice,
    check_licence,
    choices,
    describe,
    load_source_config,
    plugin_by_id,
    source_config,
    switch,
)
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

ALLOWLIST = "sources config: licence_allowlist must be a list of SPDX ids"
PRIORITY = "sources config: priority must be a list of plugin ids"
OPEN = LicenceRecord("CC-BY-4.0", "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", "Someone", "attribute")


@dataclass(frozen=True)
class FakeHeights:
    """A plugin that covers everything east of ``west`` and writes height overrides."""

    id: str = "xx.lidar"
    licence: LicenceRecord = OPEN
    produces: frozenset[Kind] = frozenset({"height_m"})
    west: float = -180.0

    def covers(self, longitude: float, latitude: float) -> bool:
        return longitude >= self.west

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        raw = root / "data" / "raw" / f"{self.id}.bin"
        out = root / "data" / "processed" / self.id
        return SourceSteps(
            stages=(
                SourceStage(f"{self.id}.fetch", ("source", "fetch", self.id, "--out", str(raw)), raw, (scene,), download=True),
                SourceStage(f"{self.id}.heights", ("source", "normalize", self.id, "--scene", str(scene), "--out", str(out)), out / "o.json", (raw, scene)),
            ),
            overrides=out / "o.json",
        )


def _config(**changes: Any) -> dict[str, Any]:
    return {
        "schema": "onward_generator.sources",
        "licence_allowlist": ["CC-BY-4.0"],
        "priority": ["es.catastro"],
        **changes,
    }


def _murcia(tmp_path: Path) -> Path:
    root = (tmp_path / "murcia").resolve()
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    return root


@pytest.fixture
def two_plugins(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    plugins = (CatastroPlugin(), FakeHeights())
    monkeypatch.setattr("onward_generator.sources.registry.PLUGINS", plugins)
    monkeypatch.setattr(
        "onward_generator.pipeline_run.load_source_config",
        lambda: source_config(_config(priority=["xx.lidar", "es.catastro"])),
    )
    return plugins


# --- configuration ---------------------------------------------------------------------------


def test_the_repository_config_ranks_lidar_above_catastro_and_allows_the_plugin_licences() -> None:
    config = load_source_config()
    assert config.priority == ("es.pnoa_ndsm", "fr.lidar_hd", "es.catastro", "overture")
    assert config.licence_allowlist == frozenset({"CC-BY-4.0", "etalab-2.0", "ODbL-1.0"})
    for plugin in PLUGINS:
        check_licence(plugin, config)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "other"}, "schema must be onward_generator.sources"),
        ({"licence_allowlist": "CC-BY-4.0"}, ALLOWLIST),
        ({"licence_allowlist": [""]}, ALLOWLIST),
        ({"licence_allowlist": [3]}, ALLOWLIST),
        ({"priority": "es.catastro"}, PRIORITY),
        ({"priority": ["es.catastro", ""]}, PRIORITY),
        ({"priority": [None]}, PRIORITY),
        ({"priority": ["es.catastro", "es.catastro"]}, "sources config: priority names a plugin twice"),
        ({"priority": []}, "sources config: priority has no rank for es.catastro"),
    ],
)
def test_source_config_refuses_an_invalid_document(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(SourceError) as caught:
        source_config(_config(**changes))
    assert str(caught.value) == message.replace("schema must", "sources config: schema must")


def test_source_config_names_every_unranked_plugin(two_plugins: tuple[Any, ...]) -> None:
    with pytest.raises(SourceError) as caught:
        source_config(_config(priority=[]))
    assert str(caught.value) == "sources config: priority has no rank for es.catastro, xx.lidar"


def test_a_plugin_without_overrides_needs_no_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onward_generator.sources.registry.PLUGINS", (FakeHeights(produces=frozenset({"footprints"})),))
    assert source_config(_config(priority=[])).priority == ()


def test_check_licence_refuses_a_licence_off_the_allowlist() -> None:
    config = source_config(_config())
    check_licence(FakeHeights(), config)
    closed = FakeHeights(licence=LicenceRecord("LicenseRef-proprietary", "x", "x", "x", "x"))
    with pytest.raises(SourceError, match="xx.lidar: licence LicenseRef-proprietary is not in licence_allowlist"):
        check_licence(closed, config)


def test_plugin_by_id_finds_catastro_and_names_the_known_ids() -> None:
    assert plugin_by_id("es.catastro").id == "es.catastro"
    with pytest.raises(SourceError) as caught:
        plugin_by_id("es.nope")
    assert str(caught.value) == "unknown source 'es.nope'. Known: es.catastro"


def test_plugin_by_id_lists_every_known_id(two_plugins: tuple[Any, ...]) -> None:
    assert plugin_by_id("xx.lidar") is two_plugins[1]
    with pytest.raises(SourceError) as caught:
        plugin_by_id("es.nope")
    assert str(caught.value) == "unknown source 'es.nope'. Known: es.catastro, xx.lidar"


def test_catastro_plugin_writes_footprints_and_levels_under_its_cadastre_licence() -> None:
    plugin = CatastroPlugin()
    assert plugin.id == "es.catastro"
    assert plugin.produces == frozenset({"footprints", "levels"})
    assert plugin.licence.spdx == "CC-BY-4.0"
    assert plugin.licence.attribution == "Dirección General del Catastro, INSPIRE Buildings (BU)"
    assert "must not be redistributed" in plugin.licence.obligations
    assert plugin.covers(MURCIA_CENTER_LON, MURCIA_CENTER_LAT)
    assert not plugin.covers(-1.6432, 42.8125)  # Pamplona: foral cadastre
    assert not plugin.covers(4.8357, 45.7640)  # Lyon


# --- switches --------------------------------------------------------------------------------


def test_switch_reads_sources_then_the_older_catastro_key() -> None:
    assert switch({}, "es.catastro") is None
    assert switch({"catastro": False}, "es.catastro") is False
    assert switch({"catastro": False}, "xx.lidar") is None
    assert switch({"catastro": False, "sources": {"es.catastro": True}}, "es.catastro") is True
    assert switch({"sources": {"xx.lidar": 0}}, "xx.lidar") is False
    assert switch({"sources": None}, "xx.lidar") is None


def test_choices_and_describe_report_running_off_and_absent_plugins(two_plugins: tuple[Any, ...]) -> None:
    centre = {"center": {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON}}
    found = choices(centre)
    assert [(c.plugin.id, c.covers, c.switched_off, c.runs) for c in found] == [
        ("es.catastro", True, False, True), ("xx.lidar", True, False, True),
    ]
    assert describe(found) == "sources  es.catastro, xx.lidar"
    off = choices({**centre, "sources": {"xx.lidar": False}})
    assert describe(off) == "sources  es.catastro  (off: xx.lidar)"
    lyon = choices({"center": {"latitude": 45.764, "longitude": 4.8357}, "sources": {"xx.lidar": False}})
    assert describe(lyon) == "sources  none  (off: xx.lidar)  (not here: es.catastro)"
    assert describe([SourceChoice(FakeHeights(), False, True)]) == "sources  none  (not here: xx.lidar)"
    two = [SourceChoice(FakeHeights(id="a"), True, True), SourceChoice(FakeHeights(id="b"), True, True)]
    assert describe(two) == "sources  none  (off: a, b)"
    gone = [SourceChoice(FakeHeights(id="a"), False, False), SourceChoice(FakeHeights(id="b"), False, False)]
    assert describe(gone) == "sources  none  (not here: a, b)"


# --- merge -----------------------------------------------------------------------------------


def _doc(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"version": "0.1.0", "overrides": list(entries)}


LIDAR = {"id": "osm-way-1", "height_m": 18.4, "source": "lidar", "license": "CC-BY-4.0", "confidence": 0.9, "note": "p50"}
CAT = {"id": "osm-way-1", "levels": 6, "source": "catastro", "license": "CC-BY-4.0", "confidence": 0.85}


def test_merge_keeps_the_ranked_winner_and_borrows_levels_for_a_height() -> None:
    merged, counts = merge_overrides(
        {
            "es.catastro": _doc(CAT, {**CAT, "id": "osm-way-2"}),
            "xx.lidar": _doc(LIDAR, {**LIDAR, "id": "osm-way-3", "note": None}),
        },
        ["xx.lidar", "es.catastro"],
    )
    assert merged["version"] == "1.0.0"
    assert merged["overrides"] == [
        {**LIDAR, "levels": 6, "source": "lidar; levels: catastro", "note": "p50; levels 6 from catastro (CC-BY-4.0)"},
        CAT | {"id": "osm-way-2"},
        {**LIDAR, "id": "osm-way-3", "note": None},
    ]
    assert counts == {
        "priority": ["xx.lidar", "es.catastro"],
        "sources": [
            {"source": "xx.lidar", "entries": 2, "won": 2, "levels_lent": 0},
            {"source": "es.catastro", "entries": 2, "won": 1, "levels_lent": 1},
        ],
        "buildings": 3,
    }


def test_merge_names_both_licences_and_starts_a_note_when_they_differ() -> None:
    merged, _ = merge_overrides(
        {"a": _doc({**LIDAR, "license": "etalab-2.0", "note": ""}), "b": _doc(CAT)}, ["a", "b"],
    )
    assert merged["overrides"][0]["license"] == "etalab-2.0 + CC-BY-4.0"
    assert merged["overrides"][0]["note"] == "levels 6 from catastro (CC-BY-4.0)"


def test_merge_never_adds_a_lower_ranked_height_to_a_levels_winner() -> None:
    merged, counts = merge_overrides({"a": _doc(CAT), "b": _doc(LIDAR)}, ["a", "b"])
    assert merged["overrides"] == [CAT]
    assert counts["sources"][1] == {"source": "b", "entries": 1, "won": 0, "levels_lent": 0}


def test_merge_keeps_the_winner_levels_and_skips_a_donor_without_levels() -> None:
    both = {**LIDAR, "levels": 5}
    merged, _ = merge_overrides({"a": _doc(both), "b": _doc(CAT)}, ["a", "b"])
    assert merged["overrides"] == [both]
    plain = {**LIDAR, "source": "other", "license": "x"}
    merged, counts = merge_overrides({"a": _doc(LIDAR), "b": _doc(plain), "c": _doc(CAT)}, ["a", "b", "c"])
    assert merged["overrides"][0]["source"] == "lidar; levels: catastro"
    assert [c["levels_lent"] for c in counts["sources"]] == [0, 0, 1]


def test_merge_counts_every_building_a_source_lends_levels_to() -> None:
    _, counts = merge_overrides(
        {"a": _doc(LIDAR, {**LIDAR, "id": "osm-way-2"}), "b": _doc(CAT, {**CAT, "id": "osm-way-2"})}, ["a", "b"],
    )
    assert counts["sources"][1]["levels_lent"] == 2


def test_merge_refuses_an_unranked_source_and_a_duplicate_id() -> None:
    with pytest.raises(SourceError) as caught:
        merge_overrides({"a": _doc(CAT), "z": _doc(CAT), "y": _doc(CAT)}, ["a"])
    assert str(caught.value) == "overrides from y, z have no rank in config/sources.json priority"
    with pytest.raises(SourceError, match="a: duplicate override id osm-way-1"):
        merge_overrides({"a": _doc(CAT, CAT)}, ["a"])


# --- CLI -------------------------------------------------------------------------------------


def test_source_list_prints_rank_kinds_licence_and_coverage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["source", "list", "--lat", "42.8125", "--lon", "-1.6432"]) == 0
    assert capsys.readouterr().out == "es.catastro  rank 3  footprints,levels  CC-BY-4.0 (allowed)  not here\n"
    assert main(["source", "list", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON)]) == 0
    assert capsys.readouterr().out.endswith("(allowed)  covers\n")
    assert main(["source", "list", "--lat", str(MURCIA_CENTER_LAT)]) == 0
    assert capsys.readouterr().out.endswith("(allowed)\n")


def test_source_list_marks_an_unranked_plugin_and_a_licence_off_the_allowlist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "sources.json"
    config.write_text(json.dumps(_config(licence_allowlist=["ODbL-1.0"])), encoding="utf-8")
    assert main(["source", "--config", str(config), "list"]) == 0
    assert capsys.readouterr().out == "es.catastro  rank 1  footprints,levels  CC-BY-4.0 (NOT ALLOWED)\n"


def test_source_list_shows_a_dash_for_a_plugin_without_rank(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("onward_generator.sources.registry.PLUGINS", (FakeHeights(produces=frozenset({"footprints"})),))
    assert main(["source", "list"]) == 0
    assert capsys.readouterr().out == "xx.lidar  rank -  footprints  CC-BY-4.0 (allowed)\n"


def test_source_merge_writes_the_merged_overrides_and_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    catastro = tmp_path / "c.json"
    catastro.write_text(json.dumps(_doc(CAT)), encoding="utf-8")
    out = tmp_path / "merged"
    assert main(["source", "merge", "--overrides", f"es.catastro={catastro}", "--out", str(out)]) == 0
    merged = json.loads((out / "heights_overrides.json").read_text(encoding="utf-8"))
    assert merged["overrides"] == [CAT]
    report = json.loads((out / "sources_report.json").read_text(encoding="utf-8"))
    assert report["schema"] == "onward_generator.sources_report"
    assert report["schema_version"] == "1.0.0"
    assert report["inputs"][0]["source"] == "es.catastro"
    assert report["inputs"][0]["path"] == str(catastro)
    assert len(report["inputs"][0]["sha256"]) == 64
    assert report["sources"] == [{"source": "es.catastro", "entries": 1, "won": 1, "levels_lent": 0}]
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "buildings=1 won: es.catastro 1"
    assert printed[1:] == [f"wrote {out / 'heights_overrides.json'}", f"wrote {out / 'sources_report.json'}"]
    assert main(["source", "merge", "--overrides", f"es.catastro={catastro}", "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert main(["source", "merge", "--overrides", f"es.catastro={catastro}", "--out", str(out), "--overwrite"]) == 0


@pytest.mark.parametrize(
    ("value", "message"),
    [("es.catastro", "--overrides takes <plugin id>=<file>"), ("=x", "--overrides takes"), ("es.catastro=", "--overrides takes"), ("xx.no=f", "unknown source 'xx.no'")],
)
def test_source_merge_refuses_a_bad_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str], value: str, message: str) -> None:
    assert main(["source", "merge", "--overrides", value, "--out", str(tmp_path / "o")]) == 1
    assert message in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_source_cli_reports_usage_errors_with_exit_2(tmp_path: Path) -> None:
    assert main(["source"]) == 2
    assert main(["source", "--help"]) == 0
    assert main(["source", "list", "--lat", "north"]) == 2
    assert main(["source", "list", "--lon", "east"]) == 2
    assert main(["source", "merge", "--out", str(tmp_path / "o")]) == 2
    assert main(["source", "merge", "--overrides", "es.catastro=x"]) == 2


def test_source_parser_help() -> None:
    import argparse

    from onward_generator.sources.__main__ import build_parser

    parser = build_parser()
    assert parser.prog == "onward-generator source"
    assert parser.description == "optional data-source plugins (ADR-018)"
    helps = {action.dest: action.help for action in parser._actions}
    assert helps["config"] == "licence allowlist and priority (default config/sources.json)"
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    listed = {choice.dest: choice.help for choice in subparsers._choices_actions}
    assert listed == {
        "list": "registered plugins, rank, licence; with --lat/--lon, whether each covers that point",
        "merge": "merge plugin overrides by priority into one heights_overrides.json (offline)",
    }
    merge = {action.dest: action.help for action in subparsers.choices["merge"]._actions}
    assert merge["overrides"] == "<plugin id>=<heights_overrides file>; repeat per plugin"


def test_source_merge_refuses_to_write_over_an_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "o"
    out.mkdir()
    inside = out / "heights_overrides.json"
    inside.write_text(json.dumps(_doc(CAT)), encoding="utf-8")
    assert main(["source", "merge", "--overrides", f"es.catastro={inside}", "--out", str(out), "--overwrite"]) == 1
    assert "output would overwrite an input file" in capsys.readouterr().err


def test_source_merge_keeps_an_equals_sign_in_the_path_and_prints_every_winner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], two_plugins: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "onward_generator.sources.__main__.load_source_config",
        lambda path: source_config(_config(priority=["xx.lidar", "es.catastro"])),
    )
    catastro = tmp_path / "a=b.json"
    catastro.write_text(json.dumps(_doc(CAT)), encoding="utf-8")
    lidar = tmp_path / "l.json"
    lidar.write_text(json.dumps(_doc({**LIDAR, "id": "osm-way-9"})), encoding="utf-8")
    out = tmp_path / "o"
    assert main(["source", "merge", "--overrides", f"es.catastro={catastro}", "--overrides", f"xx.lidar={lidar}", "--out", str(out)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "buildings=2 won: xx.lidar 1, es.catastro 1"


# --- run planning ----------------------------------------------------------------------------


def test_catastro_alone_keeps_its_stages_and_overrides_without_a_merge(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    scene = root / "data" / "processed" / "geo" / "geo_scene.json"
    stages, final, overrides = source_plan(root, scene)
    assert [s.name for s in stages] == ["catastro", "cadastre"]
    assert stages[0].download and not stages[1].download
    assert stages[0].marker == root / "data" / "raw" / "catastro.gml"
    assert stages[0].inputs == (scene,)
    assert stages[1].inputs == (root / "data" / "raw" / "catastro.gml",)
    assert final == root / "data" / "processed" / "cadastre" / "geo_scene.json"
    assert overrides == root / "data" / "processed" / "cadastre" / "heights_overrides.catastro.json"


def test_two_override_plugins_hand_the_split_scene_on_and_merge_by_priority(tmp_path: Path, two_plugins: tuple[Any, ...]) -> None:
    root = _murcia(tmp_path)
    processed = root / "data" / "processed"
    stages, final, overrides = source_plan(root, processed / "geo" / "geo_scene.json")
    assert [s.name for s in stages] == ["catastro", "cadastre", "xx.lidar.fetch", "xx.lidar.heights", "overrides"]
    assert stages[3].argv[stages[3].argv.index("--scene") + 1] == str(processed / "cadastre" / "geo_scene.json")
    merge = stages[-1]
    assert list(merge.argv) == [
        "source", "merge",
        "--overrides", f"es.catastro={processed / 'cadastre' / 'heights_overrides.catastro.json'}",
        "--overrides", f"xx.lidar={processed / 'xx.lidar' / 'o.json'}",
        "--out", str(processed / "sources"),
    ]
    assert merge.marker == processed / "sources" / "heights_overrides.json"
    assert merge.inputs == (processed / "cadastre" / "heights_overrides.catastro.json", processed / "xx.lidar" / "o.json")
    assert final == processed / "cadastre" / "geo_scene.json"
    assert overrides == processed / "sources" / "heights_overrides.json"
    steps = dict(plan_steps(root))
    assert steps["heights"][steps["heights"].index("--overrides") + 1] == str(overrides)
    assert steps["heights"][steps["heights"].index("--scene") + 1] == str(final)


def test_a_switched_off_plugin_leaves_the_other_one_unmerged(tmp_path: Path, two_plugins: tuple[Any, ...]) -> None:
    root = _murcia(tmp_path)
    store_sources(root, {"es.catastro": False})
    scene = root / "data" / "processed" / "geo" / "geo_scene.json"
    stages, final, overrides = source_plan(root, scene)
    assert [s.name for s in stages] == ["xx.lidar.fetch", "xx.lidar.heights"]
    assert final == scene
    assert overrides == root / "data" / "processed" / "xx.lidar" / "o.json"


def test_a_plugin_licence_off_the_allowlist_stops_the_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed = FakeHeights(licence=LicenceRecord("LicenseRef-closed", "x", "x", "x", "x"))
    monkeypatch.setattr("onward_generator.sources.registry.PLUGINS", (closed,))
    monkeypatch.setattr("onward_generator.pipeline_run.load_source_config", lambda: source_config(_config(priority=["xx.lidar"])))
    root = _murcia(tmp_path)
    with pytest.raises(PipelineError, match="LicenseRef-closed is not in licence_allowlist"):
        plan_steps(root)
    assert _canon(root, "xx.lidar.fetch") is None


def test_plugin_stages_are_fresh_against_their_inputs_and_rerun_downloads_with_overwrite(tmp_path: Path, two_plugins: tuple[Any, ...]) -> None:
    import os

    root = _murcia(tmp_path)
    processed = root / "data" / "processed"
    raw = root / "data" / "raw" / "xx.lidar.bin"
    assert _canon(root, "xx.lidar.fetch") == raw
    assert _canon(root, "nothing") is None
    assert _input_markers(root, "xx.lidar.heights") == [raw, processed / "cadastre" / "geo_scene.json"]
    assert _input_markers(root, "heights") == [processed / "sources" / "heights_overrides.json"]
    assert not _occupied("xx.lidar.fetch", ["--out", str(raw)], root)
    raw.with_name(raw.name + ".source.json").write_text("{}", encoding="utf-8")
    assert _occupied("xx.lidar.fetch", ["--out", str(raw)], root)
    assert not _occupied("xx.lidar.heights", ["--out", str(processed / "xx.lidar")], root)
    for index, path in enumerate([processed / "cadastre" / "geo_scene.json", raw, processed / "xx.lidar" / "o.json"]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))
    assert output_ready("xx.lidar.heights", [], root)
    os.utime(raw, (2000, 2000))
    assert not output_ready("xx.lidar.heights", [], root)


def test_without_a_map_there_are_no_plugin_stages(tmp_path: Path) -> None:
    assert _canon(tmp_path, "catastro") is None
    assert _input_markers(tmp_path, "heights") == [tmp_path / "data" / "processed" / "geo" / "geo_scene.json"]
