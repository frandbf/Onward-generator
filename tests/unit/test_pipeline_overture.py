"""``run`` with the ``overture`` source plugin (ADR-021): fetch and match after the other plugins, their files feed heights and detail."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.maps.workspace import create_map, store_overture_tints, store_sources
from onward_generator.next_step import detail_command, heights_command, overture_fetch_command, overture_match_command
from onward_generator.pipeline_run import PipelineError, output_ready, plan_steps, prepare_from, third_party_plan
from onward_generator.run_log import StageReport, summarize
from onward_generator.sources.plugin import SourceError
from onward_generator.sources.registry import SourceConfig, check_secondary_rank, load_source_config, plugin_by_id
from onward_generator.sources.world.overture import OverturePlugin
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON


@pytest.fixture(autouse=True)
def catastro_and_overture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catastro and Overture as the plugins, like test_pipeline_run's catastro_only; the LiDAR plugins have their own tests."""
    from onward_generator.sources.es.catastro import CatastroPlugin

    monkeypatch.setattr("onward_generator.sources.registry.PLUGINS", (CatastroPlugin(), OverturePlugin()))


def _murcia(tmp_path: Path) -> Path:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    return root.resolve()


def _touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (when, when))


def test_the_plugin_covers_the_world_and_is_ranked_and_allowed() -> None:
    plugin = plugin_by_id("overture")
    assert isinstance(plugin, OverturePlugin) and plugin.produces == frozenset({"height_m", "levels", "use"})
    assert plugin.covers(-99.1617, 19.4195) and plugin.covers(180.0, -90.0) and plugin.covers(-180.0, 90.0)
    assert not plugin.covers(180.5, 0.0) and not plugin.covers(-180.5, 0.0) and not plugin.covers(0.0, 90.5) and not plugin.covers(0.0, -90.5)
    config = load_source_config()
    assert plugin.licence.spdx in config.licence_allowlist and "overture" in config.priority and "overture" in config.evidence
    assert {"CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0"} <= config.licence_allowlist
    assert plugin.licence.name == "Open Database License 1.0 (Overture buildings); places under each record's licence"
    assert plugin.licence.url == "https://docs.overturemaps.org/attribution/"
    assert plugin.licence.attribution == "Overture Maps Foundation, overturemaps.org"
    assert plugin.licence.obligations.startswith("Buildings: ODbL 1.0 (OpenStreetMap, Microsoft ML footprints) and CC BY 4.0")


def test_the_plugin_stages_fetch_then_match_the_latest_scene(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    scene = root / "data" / "processed" / "cadastre" / "geo_scene.json"
    steps = OverturePlugin().steps(root, scene)
    fetch, match = steps.stages
    raw = root / "data" / "raw"
    out = root / "data" / "processed" / "overture"
    assert (fetch.name, fetch.argv, fetch.marker, fetch.inputs, fetch.download) == (
        "overture", overture_fetch_command(root).argv, raw / "overture_buildings.parquet", (), True)
    assert fetch.argv == ("overture", "fetch", "--map", str(root), "--out", str(raw))
    assert (match.name, match.argv, match.marker, match.inputs, match.download) == (
        "overture-match", overture_match_command(scene, root).argv, out / "overture_places.json", (scene, raw / "overture_buildings.parquet"), False)
    assert match.argv == ("overture", "match", "--scene", str(scene), "--map", str(root), "--out", str(out))
    assert steps.overrides is None and steps.scene is None
    assert steps.secondary_overrides == out / "heights_overrides.overture.json" and steps.places == out / "overture_places.json"
    assert steps.tints is None


def test_the_tints_switch_asks_the_match_for_colours_and_marks_the_stage_with_them(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    scene = root / "data" / "processed" / "cadastre" / "geo_scene.json"
    out = root / "data" / "processed" / "overture"
    store_overture_tints(root, True)
    steps = OverturePlugin().steps(root, scene)
    match = steps.stages[1]
    assert match.argv == ("overture", "match", "--scene", str(scene), "--map", str(root), "--out", str(out), "--tints")
    assert match.argv == overture_match_command(scene, root, tints=True).argv
    assert match.marker == out / "overture_tints.json" and steps.tints == out / "overture_tints.json"
    assert steps.places == out / "overture_places.json"


def test_with_the_tints_switch_detail_reads_the_colours(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    store_overture_tints(root, True)
    by_name = dict(plan_steps(root))
    processed = root / "data" / "processed"
    scene = processed / "cadastre" / "geo_scene.json"
    places = processed / "overture" / "overture_places.json"
    tints = processed / "overture" / "overture_tints.json"
    assert by_name["overture-match"][-1] == "--tints"
    assert by_name["detail"][by_name["detail"].index("--overture-tints") + 1] == str(tints)
    assert by_name["detail"][:-1] == list(detail_command(scene, processed / "heights" / "heights.json", processed / "scene3d", places, tints).argv)
    assert third_party_plan(root, processed / "geo" / "geo_scene.json")[2] == tints


def test_switching_tints_on_makes_the_match_and_what_follows_stale(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    processed = root / "data" / "processed"
    raw = root / "data" / "raw"
    now = (root / "map.json").stat().st_mtime
    _touch(processed / "geo" / "geo_scene.json", now)
    _touch(raw / "catastro.gml", now + 1)
    _touch(processed / "cadastre" / "geo_scene.json", now + 2)
    _touch(raw / "overture_buildings.parquet", now + 3)
    _touch(processed / "overture" / "overture_places.json", now + 4)
    _touch(processed / "heights" / "heights.json", now + 5)
    assert output_ready("overture-match", root) and output_ready("heights", root)
    store_overture_tints(root, True)
    assert not output_ready("overture-match", root)  # no overture_tints.json yet
    _touch(processed / "overture" / "overture_tints.json", now + 6)
    assert output_ready("overture-match", root) and not output_ready("heights", root)


def test_fetch_and_match_run_after_the_split_and_feed_heights_and_detail(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    steps = plan_steps(root)
    assert [name for name, _argv in steps][:6] == ["geo", "catastro", "cadastre", "overture", "overture-match", "heights"]
    by_name = dict(steps)
    processed = root / "data" / "processed"
    scene = processed / "cadastre" / "geo_scene.json"
    secondary = processed / "overture" / "heights_overrides.overture.json"
    places = processed / "overture" / "overture_places.json"
    assert by_name["overture-match"][by_name["overture-match"].index("--scene") + 1] == str(scene)
    assert by_name["heights"] == list(heights_command(scene, processed / "cadastre" / "heights_overrides.catastro.json", secondary).argv)
    assert by_name["detail"][by_name["detail"].index("--places") + 1] == str(places)
    assert by_name["detail"][:-1] == list(detail_command(scene, processed / "heights" / "heights.json", processed / "scene3d", places).argv)
    assert third_party_plan(root, processed / "geo" / "geo_scene.json") == (secondary, places, None)
    # Plan C2 is off by default: neither the match nor detail asks for colours.
    assert "--tints" not in by_name["overture-match"] and "--overture-tints" not in by_name["detail"]
    assert by_name["overture-match"] == list(overture_match_command(scene, root).argv)


def test_without_catastro_the_match_reads_the_geo_scene(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    store_sources(root, {"es.catastro": False})
    by_name = dict(plan_steps(root))
    scene = root / "data" / "processed" / "geo" / "geo_scene.json"
    assert by_name["overture-match"][by_name["overture-match"].index("--scene") + 1] == str(scene)
    assert "--overrides" not in by_name["heights"] and "--secondary-overrides" in by_name["heights"]


def test_no_source_overture_leaves_heights_and_detail_as_before(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    store_sources(root, {"overture": False})
    by_name = dict(plan_steps(root))
    assert "overture" not in by_name and "overture-match" not in by_name
    assert "--secondary-overrides" not in by_name["heights"] and "--places" not in by_name["detail"]
    assert third_party_plan(root, root / "data" / "processed" / "geo" / "geo_scene.json") == (None, None, None)
    store_overture_tints(root, True)  # the colour switch alone adds no stage and no flag
    by_name = dict(plan_steps(root))
    assert "overture-match" not in by_name and "--overture-tints" not in by_name["detail"]
    assert third_party_plan(root, root / "data" / "processed" / "geo" / "geo_scene.json") == (None, None, None)


def test_a_priority_that_ranks_overture_above_catastro_stops_the_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    upside_down = SourceConfig(load_source_config().licence_allowlist, ("overture", "es.catastro"), ("osm", "es.catastro", "overture"))
    monkeypatch.setattr("onward_generator.pipeline_run.load_source_config", lambda: upside_down)
    with pytest.raises(PipelineError) as raised:
        plan_steps(root)
    assert str(raised.value) == (
        "sources config: priority ranks overture above es.catastro, but heights reads the secondary overrides of overture below theirs"
    )
    store_sources(root, {"es.catastro": False})  # nothing authoritative to rank below
    assert "--secondary-overrides" in dict(plan_steps(root))["heights"]


def test_the_secondary_rank_names_every_source_ranked_below() -> None:
    config = SourceConfig(frozenset(), ("c", "a", "b"), ())
    check_secondary_rank(["a", "b"], [], config)
    check_secondary_rank(["c"], ["a"], config)
    check_secondary_rank(["a"], ["a"], config)  # one plugin writing both kinds is not ranked above itself
    with pytest.raises(SourceError, match=r"^sources config: priority ranks a above b, c, but"):
        check_secondary_rank(["c", "b"], ["a"], SourceConfig(frozenset(), ("a", "c", "b"), ()))
    with pytest.raises(SourceError, match="ranks c above a, b, but"):
        check_secondary_rank(["a", "b"], ["c"], config)


def test_heights_follows_the_match_and_the_match_follows_the_scene_and_the_fetch(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    processed = root / "data" / "processed"
    raw = root / "data" / "raw"
    now = (root / "map.json").stat().st_mtime
    _touch(processed / "geo" / "geo_scene.json", now)
    _touch(raw / "catastro.gml", now + 1)
    _touch(processed / "cadastre" / "geo_scene.json", now + 2)
    _touch(raw / "overture_buildings.parquet", now + 3)
    _touch(processed / "overture" / "overture_places.json", now + 4)
    _touch(processed / "heights" / "heights.json", now + 5)
    assert all(output_ready(name, root) for name in ("overture", "overture-match", "heights"))
    _touch(processed / "overture" / "overture_places.json", now + 6)
    assert not output_ready("heights", root)
    _touch(raw / "overture_buildings.parquet", now + 7)
    assert not output_ready("overture-match", root)


def test_a_rerun_fetches_again_with_overwrite(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    steps = plan_steps(root)
    assert dict(prepare_from(steps, "overture", root))["overture"][-1] == "--overwrite"  # data/raw holds the OSM extract
    (root / "data" / "processed" / "overture").mkdir(parents=True)
    assert dict(prepare_from(steps, "overture-match", root))["overture-match"][-1] == "--overwrite"


def test_run_turns_overture_off_with_no_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict = {}
    monkeypatch.setattr("onward_generator.pipeline_run.run_map", lambda **kwargs: seen.update(kwargs) or 0)
    monkeypatch.setattr("onward_generator.maps.workspace.map_directory", lambda name: tmp_path / "x")
    assert main(["run", "--name", "X", "--lat", "1", "--lon", "2", "--no-source", "overture"]) == 0
    assert seen["sources"] == {"overture": False}


def test_the_match_stage_reports_places_and_heights(tmp_path: Path) -> None:
    out = tmp_path / "overture"
    out.mkdir()
    (out / "overture_match_report.json").write_text(json.dumps({
        "places": {"counts": {"rows": 1200, "kept": 31}},
        "buildings": {"counts": {"matched": 1500, "osm_buildings": 2034}},
    }), encoding="utf-8")
    assert summarize("overture-match", ["overture", "match", "--out", str(out)]) == StageReport("places 31 of 1 200  heights 1 500 of 2 034")


def test_osm_and_heights_reach_their_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    monkeypatch.setattr("onward_generator.osm.__main__.main", lambda argv: seen.append(argv) or 0)
    assert main(["osm", "download", "--map", "x"]) == 0 and seen == [["download", "--map", "x"]]
    loaded: dict = {}

    def fake_load(path: object, *, overrides_path: object, secondary_overrides_path: object) -> object:
        loaded.update(path=path, overrides=overrides_path, secondary=secondary_overrides_path)
        raise ValueError("stop")

    monkeypatch.setattr("onward_generator.heights.load_height_config", fake_load)
    assert main(["heights", "--scene", "s", "--out", "o", "--overrides", "a.json", "--secondary-overrides", "b.json"]) == 1
    assert loaded == {"path": None, "overrides": "a.json", "secondary": "b.json"}
