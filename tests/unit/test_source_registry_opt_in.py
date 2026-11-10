"""Opt-in source plugins: only a map that switches one on runs it (ADR-018 amendment, plan D4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from onward_generator.maps.workspace import create_map, source_enabled, store_sources
from onward_generator.pipeline_run import plan_steps, source_plan, third_party_plan
from onward_generator.sources import registry
from onward_generator.sources.es.catastro import CatastroPlugin
from onward_generator.sources.es.pnoa_ndsm import PnoaNdsmPlugin
from onward_generator.sources.fr.lidar_hd import LidarHdPlugin
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceStage, SourceSteps
from onward_generator.sources.registry import SourceChoice, choices, describe, source_config
from onward_generator.sources.world.overture import OverturePlugin
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

OPEN = LicenceRecord("CC0-1.0", "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/", "Someone", "none")
CENTRE = {"center": {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON}}
EXISTING = ("es.catastro", "es.pnoa_ndsm", "fr.lidar_hd", "overture")


@dataclass(frozen=True)
class Optional:
    """An opt-in plugin that covers everything east of ``west`` and writes height overrides."""

    id: str = "xx.optional"
    licence: LicenceRecord = OPEN
    produces: frozenset[Kind] = frozenset({"height_m"})
    opt_in: bool = True
    west: float = -180.0

    def covers(self, longitude: float, latitude: float) -> bool:
        return longitude >= self.west

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        out = root / "data" / "processed" / self.id
        raw = root / "data" / "raw" / self.id
        return SourceSteps(
            stages=(
                SourceStage(f"{self.id}.fetch", ("source", "fetch", self.id), raw, (scene,), download=True),
                SourceStage(f"{self.id}.heights", ("source", "normalize", self.id), out / "o.json", (raw, scene)),
            ),
            overrides=out / "o.json",
        )


@pytest.fixture
def with_optional(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    plugins = (CatastroPlugin(), Optional())
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config = {
        "schema": "onward_generator.sources",
        "licence_allowlist": ["CC-BY-4.0", "CC0-1.0"],
        "priority": ["xx.optional", "es.catastro"],
        "evidence": ["osm", "es.catastro"],
    }
    monkeypatch.setattr("onward_generator.pipeline_run.load_source_config", lambda: source_config(config))
    return plugins


def _murcia(tmp_path: Path) -> Path:
    root = (tmp_path / "murcia").resolve()
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    return root


def _row(choice: SourceChoice) -> tuple[str, bool, bool, bool, bool]:
    return (choice.plugin.id, choice.covers, choice.switched_off, choice.switched_on, choice.runs)


def test_an_opt_in_plugin_runs_only_when_switched_on(with_optional: tuple[Any, ...]) -> None:
    assert [_row(c) for c in choices(CENTRE)] == [
        ("es.catastro", True, False, False, True), ("xx.optional", True, False, False, False),
    ]
    on = choices({**CENTRE, "sources": {"xx.optional": True}})
    assert [_row(c) for c in on] == [("es.catastro", True, False, False, True), ("xx.optional", True, False, True, True)]
    off = choices({**CENTRE, "sources": {"xx.optional": False}})
    assert _row(off[1]) == ("xx.optional", True, True, False, False)


def test_a_switch_does_not_extend_an_opt_in_plugin_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "PLUGINS", (Optional(west=100.0),))
    (only,) = choices({**CENTRE, "sources": {"xx.optional": True}})
    assert _row(only) == ("xx.optional", False, False, True, False)
    assert describe([only]) == "sources  none  (not here: xx.optional)"


def test_runs_truth_table() -> None:
    plain, opt = Optional(opt_in=False), Optional()
    assert SourceChoice(plain, True, False).runs is True
    assert SourceChoice(plain, True, False, True).runs is True
    assert SourceChoice(plain, True, True).runs is False
    assert SourceChoice(plain, False, False, True).runs is False
    assert SourceChoice(opt, True, False).runs is False
    assert SourceChoice(opt, True, False, True).runs is True
    assert SourceChoice(opt, True, True, True).runs is False
    assert SourceChoice(opt, False, False, True).runs is False


def test_describe_names_a_covering_opt_in_plugin_without_a_switch(with_optional: tuple[Any, ...]) -> None:
    assert describe(choices(CENTRE)) == "sources  es.catastro  (opt-in: xx.optional)"
    assert describe(choices({**CENTRE, "sources": {"xx.optional": True}})) == "sources  es.catastro, xx.optional"
    assert describe(choices({**CENTRE, "sources": {"xx.optional": False}})) == "sources  es.catastro  (off: xx.optional)"
    two = [SourceChoice(Optional(id="a"), True, False), SourceChoice(Optional(id="b"), True, False), SourceChoice(Optional(id="c"), True, True)]
    assert describe(two) == "sources  none  (opt-in: a, b)  (off: c)"


def test_every_existing_plugin_stays_default_on() -> None:
    assert [p.id for p in registry.PLUGINS if not p.opt_in] == list(EXISTING)
    for plugin in (CatastroPlugin(), PnoaNdsmPlugin(), LidarHdPlugin(), OverturePlugin()):
        assert plugin.opt_in is False


def test_murcia_without_a_switch_runs_the_existing_plugins_and_no_opt_in_one() -> None:
    found = choices(CENTRE)
    assert [c.plugin.id for c in found if c.runs] == ["es.catastro", "es.pnoa_ndsm", "overture"]
    assert all(not c.runs for c in found if c.plugin.opt_in)


def test_source_enabled_follows_the_opt_in_switch(tmp_path: Path, with_optional: tuple[Any, ...]) -> None:
    root = _murcia(tmp_path)
    assert source_enabled(root, "es.catastro") is True
    assert source_enabled(root, "xx.optional") is False
    store_sources(root, {"xx.optional": True})
    assert source_enabled(root, "xx.optional") is True
    store_sources(root, {"xx.optional": False})
    assert source_enabled(root, "xx.optional") is False


def test_the_plan_adds_an_opt_in_plugin_and_the_merge_only_when_switched_on(tmp_path: Path, with_optional: tuple[Any, ...]) -> None:
    root = _murcia(tmp_path)
    geo = root / "data" / "processed" / "geo" / "geo_scene.json"
    split = root / "data" / "processed" / "cadastre" / "geo_scene.json"
    stages, scene, overrides = source_plan(root, geo)
    assert [s.name for s in stages] == ["catastro", "cadastre"]
    assert scene == split
    assert overrides == root / "data" / "processed" / "cadastre" / "heights_overrides.catastro.json"
    assert "xx.optional.fetch" not in dict(plan_steps(root))
    store_sources(root, {"xx.optional": True})
    stages, scene, overrides = source_plan(root, geo)
    assert [s.name for s in stages] == ["catastro", "cadastre", "xx.optional.fetch", "xx.optional.heights", "overrides"]
    assert overrides == root / "data" / "processed" / "sources" / "heights_overrides.json"
    assert "xx.optional.fetch" in dict(plan_steps(root))
    assert stages[-1].inputs == (
        root / "data" / "processed" / "cadastre" / "heights_overrides.catastro.json",
        root / "data" / "processed" / "xx.optional" / "o.json",
    )
    assert third_party_plan(root, geo) == (None, None, None, None)


def test_switch_coerces_a_non_boolean_value_with_bool() -> None:
    assert registry.switch({"sources": {"xx.optional": 1}}, "xx.optional") is True
    assert registry.switch({"sources": {"xx.optional": ""}}, "xx.optional") is False
    assert registry.switch({"sources": {"xx.optional": "no"}}, "xx.optional") is True


MADRID = {"center": {"latitude": 40.41690, "longitude": -3.70350}}


def test_madrid_lod2_runs_only_when_switched_on_and_only_in_madrid() -> None:
    found = {c.plugin.id: c for c in choices(MADRID)}
    assert _row(found["es.madrid_lod2"]) == ("es.madrid_lod2", True, False, False, False)
    assert describe(choices(MADRID)) == (
        "sources  es.catastro, es.pnoa_ndsm, overture  (opt-in: es.madrid_lod2)  (not here: fr.lidar_hd, nl.ahn, de.nrw_ndom)")
    on = {c.plugin.id: c for c in choices({**MADRID, "sources": {"es.madrid_lod2": True}})}
    assert on["es.madrid_lod2"].runs
    at_murcia = {c.plugin.id: c for c in choices({**CENTRE, "sources": {"es.madrid_lod2": True}})}
    assert _row(at_murcia["es.madrid_lod2"]) == ("es.madrid_lod2", False, False, True, False)
    assert "(not here: fr.lidar_hd, nl.ahn, de.nrw_ndom, es.madrid_lod2)" in describe(choices(CENTRE))


def test_madrid_lod2_needs_no_priority_rank() -> None:
    config = registry.load_source_config()
    assert "es.madrid_lod2" not in config.priority
    assert not registry.plugin_by_id("es.madrid_lod2").produces & registry.OVERRIDE_KINDS
    document = {"schema": "onward_generator.sources", "licence_allowlist": ["CC-BY-4.0"], "priority": list(config.priority),
                "evidence": list(config.evidence)}
    assert source_config(document).priority == config.priority


def test_madrid_lod2_never_imports_the_exporter() -> None:
    import ast

    from onward_generator.detail import lod2
    from onward_generator.sources.es import madrid_lod2

    for module in (madrid_lod2, lod2):
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert not {n for n in names if n.startswith(("onward_generator.export", "tools.unity"))}
