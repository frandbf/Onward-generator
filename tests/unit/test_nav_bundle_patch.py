"""The Quest bundle must not contain the Hunt baker object."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATCH = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "patch_content_builder.py"
_ADAPTER = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"


def _load():
    spec = importlib.util.spec_from_file_location("patch_content_builder", _PATCH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SDK = """\
                // Wipe existing stuff
                levelDescriptor.AI_NavBake = "";

                if (navMapBaker != null && navMapBaker.NavMap != null)
                {
                    levelDescriptor.AI_NavBake = navMapBaker.NavMap.GetSerializedVersion();
                }

                if (!Build(sceneData.TargetPlatform))
                {
                    return false;
                }
"""


def test_patch_drops_the_baker_from_the_bundle_and_restores_it() -> None:
    module = _load()
    patched = module.patch_text(SDK.replace("\n", "\r\n"))
    assert "\r\n" in patched
    assert "DestroyImmediate(navMapBaker.gameObject)" in patched
    assert 'IndexOf("\\"Nodes\\":[{", StringComparison.Ordinal)' in patched
    assert 'new GameObject("NavigationMap")' in patched
    assert "GetSerializedVersion()" in patched
    assert "levelDescriptor.AI_NavBake = navMapBaker.NavMap.GetSerializedVersion()" not in patched
    assert module.patch_text(patched) == patched


def test_patch_rejects_a_file_that_is_not_the_sdk() -> None:
    module = _load()
    with pytest.raises(ValueError, match="SDK v1.15"):
        module.patch_text(SDK + SDK)


def test_adapter_refuses_a_navigation_map_the_quest_cannot_use() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")
    for needle in (
        "NavigationMap has a missing script",
        "Navigation node {i} lists itself",
        "dropped {dropped}.",
        "one-way {reversed}",
        "Navigation walk does not end",
        "Navigation graph walk exceeded the node count",
        "MaxNavWalkDepth",
        "The scene step stops here.",
        "MinLookDirectionM",
        "loops on",
        "set.Loops = true",
        "points on walkable navmesh",
        "Return links restored",
        "Waypoint nodes anchored",
        "one-way links",
        "no return path",
        "dropped: no navmesh path",
        "dropped: no return path",
        "The grid hole is",
        "Hunt navigation checked",
        "Hunt roster restored",
        "StableHuntBots = 32",
        "LogNavIslands(origin);",
        "Navmesh islands: {areas.Count}",
    ):
        assert needle in source


def test_the_adapter_bakes_the_walkable_crossings() -> None:
    """ADR-015: the crossing slab's Unity name passes WalkableGround, which bakes "walkable" and refuses "carriageway"."""
    source = _ADAPTER.read_text(encoding="utf-8")
    assert 'name.Contains("carriageway")' in source and 'return name.Contains("walkable")' in source
    unity_name = "collider/walkable_crossing#collider".replace("/", "_")
    assert "walkable" in unity_name and "carriageway" not in unity_name
