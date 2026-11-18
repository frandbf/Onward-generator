"""A package with dust in the air (plan 2026-11-17-efectos-sdk, increment F; ADR-047)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.export.dust import mote_png
from onward_generator.export.package import ExportError, build_package, verify_package
from tests.conftest import FIXED_NOW
from tests.integration.test_export_package import EXPORT_PROJECT, GOLDEN_OPTIONS_SHA256, GOLDEN_PACKAGE_ID
from tests.integration.test_smoke_package import inputs  # noqa: F401 - the module fixture is shared

ROUTES = [{"points": [{"x": -60.0, "y": -60.0}, {"x": 0.0, "y": -60.0}]}, {"points": [{"x": 60.0, "y": 60.0}]}]


def _routed(layout: Path, tmp_path: Path) -> Path:
    document = json.loads(layout.read_bytes())
    document["routes"] = ROUTES
    copy = tmp_path / "layout.json"
    copy.write_text(json.dumps(document), encoding="utf-8")
    return copy


@pytest.mark.parametrize("choice", ["off", "auto"])
def test_a_map_that_does_not_get_dust_keeps_its_package(inputs: tuple[Path, Path], tmp_path: Path, choice: str) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    result = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, dust=choice)
    assert result.manifest["package_id"] == GOLDEN_PACKAGE_ID
    assert hashlib.sha256((result.directory / "adapter_options.json").read_bytes()).hexdigest() == GOLDEN_OPTIONS_SHA256
    assert not (result.directory / "dust_mote.png").exists()
    if choice == "off":
        assert "dust" not in result.manifest
    else:  # the fixture is Murcia: mediterranean, no preset
        assert result.manifest["dust"] == {"choice": "auto", "wanted": False, "style": "mediterranean", "preset": None, "volumes": 0,
                                           "origin": "generated"}


def test_light_dust_stands_volumes_on_the_routes_with_the_preset_s_colour(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    result = build_package(EXPORT_PROJECT, detail, _routed(layout, tmp_path), tmp_path / "pkg", now=FIXED_NOW, dust="light",
                           atmosphere="dusty")
    block = json.loads((result.directory / "adapter_options.json").read_bytes())["dust"]
    assert block["texture"] == "dust_mote.png" and block["color"] == "#C9B48E" and 0.0 <= block["heading_deg"] < 360.0
    assert sorted((v["x"], v["y"]) for v in block["volumes"]) == [(-60.0, -60.0), (0.0, -60.0), (60.0, 60.0)]
    assert result.manifest["dust"] == {"choice": "light", "wanted": True, "style": "mediterranean", "preset": "dusty", "volumes": 3,
                                       "origin": "generated"}
    listed = [f for f in result.manifest["files"] if f["role"] == "dust_texture"]
    assert [f["path"] for f in listed] == ["dust_mote.png"] and listed[0]["source"] == "generated"
    assert (result.directory / "dust_mote.png").read_bytes() == mote_png()
    assert verify_package(result.directory) == []


def test_auto_takes_an_arid_style_and_the_configured_colour(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    out = tmp_path / "pkg"
    assert main(["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(_routed(layout, tmp_path)),
                 "--out", str(out), "--dust", "auto", "--facade-style", "arid"]) == 0
    block = json.loads((out / "adapter_options.json").read_bytes())["dust"]
    assert block["color"] == "#C9B48E" and len(block["volumes"]) == 3


def test_wanted_dust_without_a_route_warns_and_writes_nothing(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    document = json.loads(layout.read_bytes())
    document.pop("routes", None)
    bare = tmp_path / "layout.json"
    bare.write_text(json.dumps(document), encoding="utf-8")
    result = build_package(EXPORT_PROJECT, detail, bare, tmp_path / "pkg", now=FIXED_NOW, dust="light")
    assert result.manifest["dust"]["volumes"] == 0 and not (result.directory / "dust_mote.png").exists()
    assert "dust: the layout has no AI route inside the core to stand a dust volume on" in result.manifest["warnings"]


def test_an_unknown_style_stops_the_export(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    with pytest.raises(ExportError) as caught:
        build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, dust="auto", facade_style="martian")
    assert caught.value.problems[0].startswith("facade_style: unknown facade style 'martian'")
    assert not (tmp_path / "pkg").exists()
