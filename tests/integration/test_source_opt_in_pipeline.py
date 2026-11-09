"""Opt-in source plugins through run and source merge, with the real registry and config (ADR-018 amendment, plan D4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.maps.workspace import create_map
from onward_generator.pipeline_run import run_map
from onward_generator.sources import lidar, registry
from onward_generator.sources.registry import load_source_config
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

AMSTERDAM = (52.3702, 4.8952)
NEW = ("nl.ahn", "de.nrw_ndom", "es.madrid_lod2")


def _run(root: Path, latitude: float, longitude: float, sources: dict[str, bool] | None) -> list[list[str]]:
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    code = run_map(
        map_id=root.name, name=root.name, latitude=latitude, longitude=longitude, radius_m=150, margin_m=30,
        out=str(root), sources=sources, execute=execute, interactive=False, no_unity=True,
    )
    assert code == 0
    return seen


def _source_calls(seen: list[list[str]], plugin_id: str) -> list[str]:
    return [argv[1] for argv in seen if argv[:1] == ["source"] and plugin_id in argv]


def test_the_new_plugins_are_opt_in_and_the_old_ones_are_not() -> None:
    assert [p.id for p in registry.PLUGINS if p.opt_in] == list(NEW)
    config = load_source_config()
    for plugin in registry.PLUGINS:
        registry.check_licence(plugin, config)
    assert config.priority.index("fr.lidar_hd") < config.priority.index("nl.ahn") < config.priority.index("de.nrw_ndom") < config.priority.index("es.catastro")


def test_run_in_amsterdam_adds_ahn_only_when_switched_on(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = (tmp_path / "amsterdam").resolve()
    create_map(root, map_id="amsterdam", name="amsterdam", latitude=AMSTERDAM[0], longitude=AMSTERDAM[1], radius_m=150)
    assert _source_calls(_run(root, *AMSTERDAM, None), "nl.ahn") == []
    out = capsys.readouterr().out
    assert "sources  overture  (opt-in: nl.ahn)  (not here: es.catastro, es.pnoa_ndsm, fr.lidar_hd, de.nrw_ndom, es.madrid_lod2)\n" in out
    seen = _run(root, *AMSTERDAM, {"nl.ahn": True})
    assert _source_calls(seen, "nl.ahn") == ["fetch", "normalize"]
    assert "sources  nl.ahn, overture  (not here: es.catastro, es.pnoa_ndsm, fr.lidar_hd, de.nrw_ndom, es.madrid_lod2)\n" in capsys.readouterr().out
    heights = next(argv for argv in seen if argv[0] == "heights")
    assert heights[heights.index("--overrides") + 1].endswith("nl.ahn/heights_overrides.nl.ahn.json")
    assert json.loads((root / "map.json").read_text(encoding="utf-8"))["sources"] == {"nl.ahn": True}
    assert _source_calls(_run(root, *AMSTERDAM, {"nl.ahn": False}), "nl.ahn") == []
    assert "(off: nl.ahn)" in capsys.readouterr().out


def test_a_switch_outside_the_coverage_adds_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = (tmp_path / "murcia").resolve()
    create_map(root, map_id="murcia", name="murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    seen = _run(root, MURCIA_CENTER_LAT, MURCIA_CENTER_LON, {"nl.ahn": True, "de.nrw_ndom": True})
    assert _source_calls(seen, "nl.ahn") == [] and _source_calls(seen, "de.nrw_ndom") == []
    assert "sources  es.catastro, es.pnoa_ndsm, overture  (not here: fr.lidar_hd, nl.ahn, de.nrw_ndom, es.madrid_lod2)\n" in capsys.readouterr().out


def test_source_merge_ranks_ahn_above_catastro_and_keeps_its_licence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ahn = tmp_path / "ahn.json"
    cat = tmp_path / "cat.json"
    ahn.write_text(json.dumps({"version": "0.1.0", "overrides": [
        {"id": "osm-way-1", "height_m": 12.0, "source": "AHN DSM minus filled DTM 0.5 m (PDOK)", "license": "CC0-1.0", "confidence": 0.85},
    ]}), encoding="utf-8")
    cat.write_text(json.dumps({"version": "0.1.0", "overrides": [
        {"id": "osm-way-1", "levels": 3, "source": "catastro", "license": "CC-BY-4.0", "confidence": 0.85},
        {"id": "osm-way-2", "levels": 2, "source": "catastro", "license": "CC-BY-4.0", "confidence": 0.85},
    ]}), encoding="utf-8")
    out = tmp_path / "merged"
    assert main(["source", "merge", "--overrides", f"es.catastro={cat}", "--overrides", f"nl.ahn={ahn}", "--out", str(out)]) == 0
    merged = {e["id"]: e for e in json.loads((out / "heights_overrides.json").read_text(encoding="utf-8"))["overrides"]}
    assert merged["osm-way-1"]["height_m"] == 12.0 and merged["osm-way-1"]["levels"] == 3
    assert "CC0-1.0" in merged["osm-way-1"]["license"] and "AHN" in merged["osm-way-1"]["source"]
    assert merged["osm-way-2"]["license"] == "CC-BY-4.0"
    assert "won: nl.ahn 1, es.catastro 1" in capsys.readouterr().out


def test_source_fetch_stops_on_a_licence_off_the_allowlist_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lidar, "http_fetch", lambda plugin: pytest.fail("no request may start"))
    config = tmp_path / "sources.json"
    document = json.loads(registry.CONFIG_PATH.read_text(encoding="utf-8"))
    document["licence_allowlist"] = [spdx for spdx in document["licence_allowlist"] if spdx != "CC0-1.0"]
    config.write_text(json.dumps(document), encoding="utf-8")
    scene = tmp_path / "geo_scene.json"
    scene.write_text("{}", encoding="utf-8")
    assert main(["source", "--config", str(config), "fetch", "nl.ahn", "--scene", str(scene), "--out", str(tmp_path / "raw")]) == 1
    assert "nl.ahn: licence CC0-1.0 is not in licence_allowlist" in capsys.readouterr().err
    assert not (tmp_path / "raw").exists()


MADRID = (40.41690, -3.70350)


def test_run_in_madrid_adds_the_lod2_download_and_hands_detail_the_index_only_when_switched_on(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = (tmp_path / "madrid").resolve()
    create_map(root, map_id="madrid", name="madrid", latitude=MADRID[0], longitude=MADRID[1], radius_m=150)
    seen = _run(root, *MADRID, None)
    assert _source_calls(seen, "madrid-lod2") == []
    assert "--lod2-landmarks" not in next(argv for argv in seen if argv[0] == "detail")
    assert "(opt-in: es.madrid_lod2)" in capsys.readouterr().out
    seen = _run(root, *MADRID, {"es.madrid_lod2": True})
    calls = [argv for argv in seen if argv[:2] == ["source", "madrid-lod2"]]
    split = root / "data" / "processed" / "cadastre" / "geo_scene.json"
    index = root / "data" / "raw" / "madrid_lod2" / "lod2_index.json"
    assert calls == [["source", "madrid-lod2", "--scene", str(split), "--out", str(index.parent)]]
    names = [argv[0] if argv[0] != "source" else argv[1] for argv in seen]
    assert names.index("madrid-lod2") < names.index("heights")  # it reads the Catastro scene, before the heights
    detail = next(argv for argv in seen if argv[0] == "detail")
    assert detail[detail.index("--lod2-landmarks") + 1] == str(index)
    assert detail[detail.index("--scene") + 1] == str(split)
    assert "sources  es.catastro, es.pnoa_ndsm, es.madrid_lod2, overture" in capsys.readouterr().out


def test_a_newer_lod2_index_makes_detail_stale_only_with_the_switch(tmp_path: Path) -> None:
    import os

    from onward_generator.maps.workspace import store_sources
    from onward_generator.pipeline_run import landmark_mesh_plan, output_ready

    root = (tmp_path / "madrid").resolve()
    create_map(root, map_id="madrid", name="madrid", latitude=MADRID[0], longitude=MADRID[1], radius_m=150)
    processed = root / "data" / "processed"
    geo = processed / "geo" / "geo_scene.json"
    index = root / "data" / "raw" / "madrid_lod2" / "lod2_index.json"
    assert landmark_mesh_plan(root, geo) is None
    for name, when in (("scene3d/scene_visual.glb", 1), ("scene_detail/scene_visual.glb", 2), ("heights/heights.json", 0)):
        path = processed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1_000_000 + when, 1_000_000 + when))
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("{}", encoding="utf-8")
    os.utime(index, (1_000_003, 1_000_003))
    assert output_ready("detail", root)  # switched off: the index is not an input
    store_sources(root, {"es.madrid_lod2": True})
    assert landmark_mesh_plan(root, geo) == index
    assert not output_ready("detail", root) and output_ready("generate", root)
    os.utime(index, (1_000_000, 1_000_000))
    assert output_ready("detail", root)
    index.unlink()
    assert not output_ready("detail", root)  # switched on and no index yet
