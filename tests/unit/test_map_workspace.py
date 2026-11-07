"""A map folder is its own centre, name and area. Belluga is not involved."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from shapely import Point

from onward_generator.cli import main
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.projection import LocalFrame
from onward_generator.maps.workspace import (
    MapError,
    combine_cell_m,
    core_budget,
    create_map,
    gameplay_report_enabled,
    load_map,
    panoramax_atlas_enabled,
    source_enabled,
    store_gameplay_report,
    store_sources,
)
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON


def test_create_writes_an_isolated_square_around_the_centre(tmp_path: Path) -> None:
    root = tmp_path / "murcia_test"
    document = create_map(
        root,
        map_id="murcia_test",
        name="Plaza de prueba",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=200,
        seed=7,
        core_radius_m=0,
    )
    assert document["core_radius_m"] is None
    assert document["name"] == "Plaza de prueba"
    assert document["seed"] == 7
    assert document["panoramax"] is False
    assert (root / "config" / "project.json").is_file()
    assert json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["map"]["title"] == "Plaza de prueba"
    build = json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["build"]
    assert build["lightmap_texels_per_m"] == 10.0
    # Fog starts at the wall: radius plus margin from the centre (ADR-014).
    margin = document["margin_m"]
    assert (build["fog_enabled"], build["fog_start_m"], build["fog_end_m"], build["fog_color"]) == (
        True, 200 + margin, 200 + margin + 600.0, "#C8CDD2")
    # The card names the room-light cap, equal to the detail cap, so the adapter does not fall back to 24 (ADR-013).
    detail = json.loads((Path(__file__).parents[2] / "config" / "detail.default.json").read_text(encoding="utf-8"))
    assert (build["interior_lights_max"], build["interior_light_intensity"]) == (detail["interiors"]["lights_max"], 3.0)
    assert build["interior_lights_max"] == 160
    aoi = load_aoi_geojson(root / "reference" / "aoi.geojson")
    area = _polygon(aoi.polygon)
    assert area.covers(Point(MURCIA_CENTER_LON, MURCIA_CENTER_LAT))
    local = LocalFrame.create(MURCIA_CENTER_LON, MURCIA_CENTER_LAT).to_local(area)
    minx, miny, maxx, maxy = local.bounds
    assert maxx - minx == pytest.approx(400, abs=0.05)
    assert maxy - miny == pytest.approx(400, abs=0.05)


def test_create_stores_the_lightmap_resolution(tmp_path: Path) -> None:
    root = tmp_path / "murcia_test"
    create_map(
        root,
        map_id="murcia_test",
        name="Plaza de prueba",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=100,
        lightmap_texels_per_m=4,
    )
    assert json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["build"]["lightmap_texels_per_m"] == 4.0
    with pytest.raises(MapError, match="lightmap_texels_per_m"):
        create_map(
            tmp_path / "otro",
            map_id="otro",
            name="Otro",
            latitude=MURCIA_CENTER_LAT,
            longitude=MURCIA_CENTER_LON,
            radius_m=100,
            lightmap_texels_per_m=0,
        )


def test_two_maps_do_not_share_a_folder(tmp_path: Path) -> None:
    create_map(tmp_path / "a", map_id="mapa_a", name="A", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100)
    create_map(tmp_path / "b", map_id="mapa_b", name="B", latitude=37.98, longitude=-1.13, radius_m=120)
    assert json.loads((tmp_path / "a" / "map.json").read_text(encoding="utf-8"))["id"] == "mapa_a"
    assert json.loads((tmp_path / "b" / "map.json").read_text(encoding="utf-8"))["radius_m"] == 120


def test_create_uses_utm_when_the_centre_is_outside_spain(tmp_path: Path) -> None:
    lisbon = create_map(tmp_path / "lisboa", map_id="lisboa", name="Lisboa", latitude=38.72, longitude=-9.14, radius_m=100)
    assert lisbon["crs"] == "EPSG:32629"
    obelisk = create_map(
        tmp_path / "obelisco",
        map_id="obelisco",
        name="Obelisco",
        latitude=-34.60369254204782,
        longitude=-58.381647718021945,
        radius_m=400,
    )
    assert obelisk["crs"] == "EPSG:32721"
    aoi = load_aoi_geojson(tmp_path / "obelisco" / "reference" / "aoi.geojson", crs="EPSG:32721")
    area = _polygon(aoi.polygon)
    assert area.covers(Point(-58.381647718021945, -34.60369254204782))
    local = LocalFrame.create(-58.381647718021945, -34.60369254204782, "EPSG:32721").to_local(area)
    minx, miny, maxx, maxy = local.bounds
    assert maxx - minx == pytest.approx(800, abs=0.05)
    assert maxy - miny == pytest.approx(800, abs=0.05)


def test_create_refuses_to_replace_pipeline_output(tmp_path: Path) -> None:
    root = tmp_path / "murcia_test"
    create_map(root, map_id="murcia_test", name="Uno", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100)
    produced = root / "data" / "processed" / "geo_scene.json"
    produced.write_text("{}", encoding="utf-8")
    with pytest.raises(MapError, match="pipeline output"):
        create_map(root, map_id="murcia_test", name="Dos", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100, overwrite=True)


def test_slug_uses_the_card_name_for_the_folder() -> None:
    from onward_generator.maps.workspace import MapError, map_directory, slug_from_name

    assert slug_from_name("Apocalipsis Murciana") == "apocalipsis_murciana"
    assert map_directory("  Año  ñuñoa ") == Path("maps/ano_nunoa")
    with pytest.raises(MapError, match="map id"):
        slug_from_name("123")


def test_cli_create_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    def fake_download(directory: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(size_bytes=12, sha256="abc", path=str(Path(directory) / "data" / "raw" / "cartagena.osm"))

    monkeypatch.setattr("onward_generator.osm.acquire.download_map_osm", fake_download)
    import onward_generator.maps.__main__ as map_cli

    monkeypatch.setattr(map_cli, "download_map_osm", fake_download)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "maps" / "cartagena"
    assert main(["map", "create", "--name", "Cartagena", "--lat", "37.625", "--lon", "-0.986", "--radius-m", "300"]) == 0
    assert main(["map", "show", str(root)]) == 0
    shown = capsys.readouterr().out
    assert "Cartagena" in shown
    assert f"{root}/reference/aoi.geojson" in shown
    assert "--origin -0.986 37.625" in shown
    assert "--crs EPSG:25830" in shown
    assert "sha256 abc" in shown
    assert "next: .venv/bin/python -m onward_generator geo " in shown
    assert json.loads((root / "map.json").read_text(encoding="utf-8"))["core_radius_m"] == 150.0
    assert main(["map", "create", "--name", "Lorca", "--lat", "37.677", "--lon", "-1.700", "--radius-m", "300",
                 "--core-radius-m", "120"]) == 0
    assert json.loads((tmp_path / "maps" / "lorca" / "map.json").read_text(encoding="utf-8"))["core_radius_m"] == 120.0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["map", "create", "--help"])
    assert ("--core-radius-m CORE_RADIUS_M half-side of the playable core square, metres (default: 50 m inside the "
            "radius, at most 150; 0 or at least the radius plays the whole map; ADR-017)") in " ".join(capsys.readouterr().out.split())


def _polygon(ring: tuple[tuple[float, float], ...]):
    from shapely import Polygon

    return Polygon(ring)


def catastro_enabled(root: Path) -> bool:
    return source_enabled(root, "es.catastro")


def test_catastro_is_on_by_default_in_spain_and_follows_the_stored_switch(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    assert catastro_enabled(root) is True
    store_sources(root, {"es.catastro": False})
    assert json.loads((root / "map.json").read_text(encoding="utf-8"))["sources"] == {"es.catastro": False}
    assert catastro_enabled(root) is False
    store_sources(root, {"es.catastro": True})
    assert catastro_enabled(root) is True


def test_an_older_map_keeps_its_catastro_key_until_a_source_switch_replaces_it(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    (root / "map.json").write_text(json.dumps({**document, "catastro": False}), encoding="utf-8")
    assert catastro_enabled(root) is False
    store_sources(root, {"es.catastro": True})
    stored = json.loads((root / "map.json").read_text(encoding="utf-8"))
    assert "catastro" not in stored and stored["sources"] == {"es.catastro": True}
    assert catastro_enabled(root) is True


def test_store_sources_keeps_the_other_switches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.sources.es.catastro import CatastroPlugin

    @dataclass(frozen=True)
    class Other(CatastroPlugin):
        id: str = "xx.other"

    monkeypatch.setattr("onward_generator.sources.registry.PLUGINS", (CatastroPlugin(), Other()))
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    store_sources(root, {"xx.other": True})
    store_sources(root, {"es.catastro": False})
    assert json.loads((root / "map.json").read_text(encoding="utf-8"))["sources"] == {"es.catastro": False, "xx.other": True}


def test_store_sources_refuses_an_unknown_plugin_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    before = (root / "map.json").read_bytes()
    with pytest.raises(ValueError, match="unknown source 'xx.nothing'"):
        store_sources(root, {"xx.nothing": True})
    assert (root / "map.json").read_bytes() == before


def test_catastro_is_off_outside_spain_in_navarre_and_without_a_map(tmp_path: Path) -> None:
    abroad = tmp_path / "obelisco"
    create_map(abroad, map_id="obelisco", name="Obelisco", latitude=-34.6037, longitude=-58.3816, radius_m=400)
    assert catastro_enabled(abroad) is False
    pamplona = tmp_path / "pamplona"
    create_map(pamplona, map_id="pamplona", name="Pamplona", latitude=42.8125, longitude=-1.6432, radius_m=400)
    assert catastro_enabled(pamplona) is False  # foral cadastre: the DG Catastro WFS has nothing there
    assert catastro_enabled(tmp_path / "missing") is False


def test_catastro_is_on_for_large_maps_and_other_utm_zones(tmp_path: Path) -> None:
    large = tmp_path / "grande"
    create_map(large, map_id="grande", name="Grande", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=1500)
    assert catastro_enabled(large) is True  # tiled download
    santiago = tmp_path / "santiago"
    create_map(santiago, map_id="santiago", name="Santiago", latitude=42.8806, longitude=-8.5446, radius_m=400)
    document = json.loads((santiago / "map.json").read_text(encoding="utf-8"))
    assert document["crs"] == "EPSG:32629" and catastro_enabled(santiago) is True


def test_a_larger_map_gets_a_playable_core_and_fog_from_its_wall(tmp_path: Path) -> None:
    document = create_map(tmp_path / "grande", map_id="grande", name="Grande", latitude=MURCIA_CENTER_LAT,
                          longitude=MURCIA_CENTER_LON, radius_m=300)
    assert document["core_radius_m"] == 150.0
    build = json.loads((tmp_path / "grande" / "config" / "export.json").read_text(encoding="utf-8"))["build"]
    # The wall stands on the core edge (ADR-017), so the fog starts there.
    assert (build["fog_start_m"], build["fog_end_m"]) == (150.0, 750.0)


@pytest.mark.parametrize(("radius", "core", "stored"), [
    (300, 50, 50.0),
    (300, 299.5, 299.5),
    (300, 300, None),     # a core at the radius is the whole map
    (300, 400, None),
    (150, 150, None),     # the core a 150 m map had by default before 5 November 2026
    (300, 0, None),
])
def test_core_radius_is_stored_only_inside_the_map(tmp_path: Path, radius: float, core: float, stored: float | None) -> None:
    document = create_map(tmp_path / "m", map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                          radius_m=radius, core_radius_m=core)
    assert document["core_radius_m"] == stored


@pytest.mark.parametrize(("radius", "stored"), [
    (150, 100.0),  # Barcelona Sarria: 381 k triangles and 376 draws a view with the whole map played
    (200, 150.0),
    (300, 150.0),  # at most 150 m
    (100, 50.0),
    (99.9, None),  # a 49.9 m core is under the 50 m minimum: the whole map is played
    (50, None),
])
def test_without_a_core_a_map_keeps_a_50_m_scenic_ring(tmp_path: Path, radius: float, stored: float | None) -> None:
    document = create_map(tmp_path / "m", map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                          radius_m=radius)
    assert document["core_radius_m"] == (pytest.approx(stored) if stored is not None else None)


@pytest.mark.parametrize(("core", "message"), [(49.9, "^core_radius_m must be 0 or at least 50$"), (-10, "0 or at least 50"),
                                               (True, "^core_radius_m must be a number$"), ("150", "must be a number")])
def test_core_radius_refuses_a_bad_value(tmp_path: Path, core: object, message: str) -> None:
    with pytest.raises(MapError, match=message):
        create_map(tmp_path / "m", map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                   radius_m=300, core_radius_m=core)  # type: ignore[arg-type]
    assert not (tmp_path / "m").exists()


# The budget core (ADR-017 amendment, 7 November 2026). Without a target, map.json and export.json are the
# documents map create wrote before the budget mode existed (checked against fix/overrides-size, a57265a).
def _golden_map(radius: int, core: float) -> dict[str, object]:
    return {
        "schema": "onward_generator.map", "schema_version": "0.1.0", "id": f"golden_r{radius}", "name": f"Golden r{radius}",
        "description": (f"Golden r{radius}: generated map centred at 37.98407, -1.12863. Generic facades; not a faithful "
                        "reconstruction. Data: © OpenStreetMap contributors (ODbL-1.0)."),
        "brief": "", "ambient_ai": False, "panoramax": False, "time_of_day": "day", "storm": False,
        "center": {"latitude": 37.98407, "longitude": -1.12863}, "radius_m": float(radius), "margin_m": 30.0,
        "core_radius_m": core, "crs": "EPSG:25830", "seed": 3789291155,
        "paths": {"project": "config/project.json", "export": "config/export.json", "aoi": "reference/aoi.geojson",
                  "raw": "data/raw", "processed": "data/processed"},
    }


@pytest.mark.parametrize(("radius", "core"), [(150, 100.0), (300, 150.0)])
def test_without_a_target_map_json_and_export_json_are_unchanged(tmp_path: Path, radius: int, core: float) -> None:
    root = tmp_path / f"golden_r{radius}"
    document = create_map(root, map_id=f"golden_r{radius}", name=f"Golden r{radius}", latitude=MURCIA_CENTER_LAT,
                          longitude=MURCIA_CENTER_LON, radius_m=radius)
    stored = json.loads((root / "map.json").read_text(encoding="utf-8"))
    assert stored == document == _golden_map(radius, core)
    assert list(stored) == list(_golden_map(radius, core))
    build = json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["build"]
    assert build == {
        "combine_cell_m": 100.0, "static_batching": False, "occlusion_culling": False, "lightmap_texels_per_m": 10.0,
        "lightmap_max_size": 2048, "lightmap_directional": False, "fog_enabled": True, "fog_start_m": core,
        "fog_end_m": core + 600.0, "fog_color": "#C8CDD2", "interior_light_intensity": 3.0, "interior_lights_max": 160,
    }
    assert core_budget(stored) is None


@pytest.mark.parametrize(("radius", "core", "target", "cap"), [
    (300, None, 150_000, 250.0),     # the largest half-side keeps the 50 m ring
    (300, 120, 150_000, 120.0),      # an explicit core is the largest half-side
    (150, None, 1000, 100.0),
    (300, 250, 2_000_000, 250.0),
    (100, None, 5000, 50.0),
])
def test_a_target_writes_the_budget_core(tmp_path: Path, radius: float, core: float | None, target: int, cap: float) -> None:
    root = tmp_path / "m"
    document = create_map(root, map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                          radius_m=radius, core_radius_m=core, core_target_triangles=target)
    assert document["core"] == {"mode": "budget", "target_triangles": target, "max_half_side_m": cap}
    stored = json.loads((root / "map.json").read_text(encoding="utf-8"))
    assert stored == document
    assert list(stored).index("core") == list(stored).index("core_radius_m") + 1
    # core_radius_m and the fog are written as without a target.
    fixed = create_map(tmp_path / "f", map_id="f", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                       radius_m=radius, core_radius_m=core)
    assert {k: v for k, v in document.items() if k not in ("core", "id")} == {k: v for k, v in fixed.items() if k != "id"}
    assert (json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["build"]
            == json.loads((tmp_path / "f" / "config" / "export.json").read_text(encoding="utf-8"))["build"])
    assert core_budget(stored) == (cap, target)
    assert load_map(root) == stored


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"core_target_triangles": True}, "^target_triangles must be an integer from 1000 to 2000000, got True$"),
    ({"core_target_triangles": "150000"}, "got '150000'$"),
    ({"core_target_triangles": 150000.0}, r"got 150000\.0$"),
    ({"core_target_triangles": 999}, "got 999$"),
    ({"core_target_triangles": 2_000_001}, "got 2000001$"),
    ({"core_target_triangles": 5000, "radius_m": 90}, "^a budget core needs a playable core: core_radius_m 0, or a radius under 100 m, plays the whole map$"),
    ({"core_target_triangles": 5000, "core_radius_m": 0}, "^a budget core needs a playable core"),
    ({"core_target_triangles": 5000, "core_radius_m": 300}, "^a budget core needs a playable core"),
    ({"core_target_triangles": 5000, "core_radius_m": 250.5},
     r"^the largest core half-side must be between 50 and 250 m \(radius_m - 50\), got 250\.5$"),
    ({"core_target_triangles": 5000, "core_radius_m": 299}, "got 299$"),
])
def test_a_bad_budget_core_is_refused(tmp_path: Path, kwargs: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {"radius_m": 300, **kwargs}
    with pytest.raises(MapError, match=message):
        create_map(tmp_path / "m", map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, **arguments)  # type: ignore[arg-type]
    assert not (tmp_path / "m").exists()


@pytest.mark.parametrize(("block", "message"), [
    ({"mode": "polygon", "target_triangles": 150000, "max_half_side_m": 250.0}, '^map.json core mode must be "budget", got \'polygon\'$'),
    ({"mode": "budget", "target_triangles": 150000}, '^map.json "core" must be'),
    ({"mode": "budget", "target_triangles": 150000, "max_half_side_m": 250.0, "seed": "spawns"}, '^map.json "core" must be'),
    ("budget", '^map.json "core" must be'),
    ({"mode": "budget", "target_triangles": 500, "max_half_side_m": 250.0}, "got 500$"),
    ({"mode": "budget", "target_triangles": 150000, "max_half_side_m": 260.0}, r"got 260\.0$"),
    ({"mode": "budget", "target_triangles": 150000, "max_half_side_m": 49.0}, r"got 49\.0$"),
    ({"mode": "budget", "target_triangles": 150000, "max_half_side_m": "250"}, "got '250'$"),
])
def test_a_hand_edited_budget_core_is_refused_on_reading(tmp_path: Path, block: object, message: str) -> None:
    root = tmp_path / "m"
    create_map(root, map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=300,
               core_target_triangles=150_000)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document["core"] = block
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MapError, match=message):
        load_map(root)
    # A map without a playable core cannot keep a budget core either.
    document["core"] = {"mode": "budget", "target_triangles": 150000, "max_half_side_m": 250.0}
    document["core_radius_m"] = None
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MapError, match="^a budget core needs a playable core"):
        load_map(root)


def test_map_create_cli_stores_the_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from types import SimpleNamespace

    import onward_generator.maps.__main__ as map_cli

    monkeypatch.setattr(map_cli, "download_map_osm", lambda directory, overwrite=False: SimpleNamespace(
        size_bytes=1, sha256="abc", path=str(Path(directory) / "data" / "raw" / "x.osm")))
    monkeypatch.chdir(tmp_path)
    assert main(["map", "create", "--name", "Lorca", "--lat", "37.677", "--lon", "-1.700", "--radius-m", "300",
                 "--core-target-triangles", "150000"]) == 0
    stored = json.loads((tmp_path / "maps" / "lorca" / "map.json").read_text(encoding="utf-8"))
    assert stored["core"] == {"mode": "budget", "target_triangles": 150000, "max_half_side_m": 250.0}
    assert stored["core_radius_m"] == 150.0
    assert main(["map", "create", "--name", "Yecla", "--lat", "38.61", "--lon", "-1.11", "--radius-m", "300"]) == 0
    assert "core" not in json.loads((tmp_path / "maps" / "yecla" / "map.json").read_text(encoding="utf-8"))
    assert main(["map", "create", "--name", "Jumilla", "--lat", "38.47", "--lon", "-1.32", "--radius-m", "300",
                 "--core-target-triangles", "10"]) == 1
    assert "map: target_triangles must be an integer from 1000 to 2000000, got 10" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["map", "create", "--help"])
    shown = " ".join(capsys.readouterr().out.split()).replace("- ", "-")
    assert ("--core-target-triangles CORE_TARGET_TRIANGLES budget core (ADR-017 amendment): solve the core half-side for this "
            "many core visual triangles in detail, 1000 to 2000000; --core-radius-m, or 50 m inside the radius, is the largest "
            "half-side") in shown


def _atlas_map(tmp_path: Path, **values: object) -> Path:
    root = tmp_path / "atlas"
    if not (root / "map.json").is_file():
        create_map(root, map_id="atlas", name="Atlas", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document.update(values)
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def test_panoramax_atlas_is_off_without_the_key_and_on_only_for_true(tmp_path: Path) -> None:
    assert panoramax_atlas_enabled(tmp_path / "nowhere") is False
    root = _atlas_map(tmp_path)
    assert panoramax_atlas_enabled(root) is False
    assert "panoramax_atlas" not in load_map(root)
    for value, expected in ((False, False), (True, True)):
        _atlas_map(tmp_path, panoramax_atlas=value)
        assert panoramax_atlas_enabled(root) is expected


@pytest.mark.parametrize("value", ["yes", 1, 0, {}, None])
def test_panoramax_atlas_refuses_a_value_that_is_not_a_bool(tmp_path: Path, value: object) -> None:
    root = _atlas_map(tmp_path, panoramax_atlas=value)
    with pytest.raises(MapError, match='"panoramax_atlas" must be true or false'):
        panoramax_atlas_enabled(root)


def test_combine_cell_m_reads_the_export_card(tmp_path: Path) -> None:
    assert combine_cell_m(tmp_path / "nowhere") == 100.0
    root = _atlas_map(tmp_path)
    assert combine_cell_m(root) == 100.0
    card_path = root / "config" / "export.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    for value, expected in ((50, 50.0), (0, 100.0), (0.0, 100.0), (12.5, 12.5), (0.5, 0.5)):
        card["build"]["combine_cell_m"] = value
        card_path.write_text(json.dumps(card), encoding="utf-8")
        assert combine_cell_m(root) == expected
    del card["build"]["combine_cell_m"]
    card_path.write_text(json.dumps(card), encoding="utf-8")
    assert combine_cell_m(root) == 100.0
    card_path.write_text(json.dumps({"map": {}}), encoding="utf-8")
    assert combine_cell_m(root) == 100.0
    card_path.write_text("[]", encoding="utf-8")
    assert combine_cell_m(root) == 100.0
    card_path.unlink()
    assert combine_cell_m(root) == 100.0


@pytest.mark.parametrize("value", ["100", True, -5, None])
def test_combine_cell_m_refuses_a_value_that_is_not_metres(tmp_path: Path, value: object) -> None:
    root = _atlas_map(tmp_path)
    card_path = root / "config" / "export.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["build"]["combine_cell_m"] = value
    card_path.write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(MapError, match="combine_cell_m must be a number of metres"):
        combine_cell_m(root)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"build": {"combine_cell_m": 10', "not valid JSON"),
        (b"\xff\xfe not utf-8", "not valid JSON"),
        (b'{"build": [100]}', "build must be an object, got \\[100\\]"),
    ],
)
def test_combine_cell_m_refuses_a_card_that_is_not_json_or_has_no_build_object(
    tmp_path: Path, content: bytes, message: str
) -> None:
    root = _atlas_map(tmp_path)
    card_path = root / "config" / "export.json"
    card_path.write_bytes(content)
    with pytest.raises(MapError, match=f"^{re.escape(str(card_path))}: {message}"):
        combine_cell_m(root)


def test_combine_cell_m_follows_the_export_path_of_the_map(tmp_path: Path) -> None:
    root = _atlas_map(tmp_path)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document["paths"]["export"] = "config/other.json"
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "config" / "other.json").write_text(json.dumps({"build": {"combine_cell_m": 25}}), encoding="utf-8")
    assert combine_cell_m(root) == 25.0


def test_combine_cell_m_defaults_to_config_export_json(tmp_path: Path) -> None:
    root = _atlas_map(tmp_path)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    del document["paths"]["export"]
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "config" / "export.json").write_text(json.dumps({"build": {"combine_cell_m": 40}}), encoding="utf-8")
    assert combine_cell_m(root) == 40.0


def test_gameplay_report_is_off_without_the_key_and_stored_on_request(tmp_path: Path) -> None:
    assert gameplay_report_enabled(tmp_path / "nowhere") is False
    root = _atlas_map(tmp_path)
    assert "gameplay_report" not in load_map(root)
    assert gameplay_report_enabled(root) is False
    store_gameplay_report(root, True)
    assert load_map(root)["gameplay_report"] is True and gameplay_report_enabled(root) is True
    store_gameplay_report(root, False)
    assert load_map(root)["gameplay_report"] is False and gameplay_report_enabled(root) is False


@pytest.mark.parametrize("value", ["true", 1, 0, None, []])
def test_gameplay_report_refuses_a_value_that_is_not_a_bool(tmp_path: Path, value: object) -> None:
    root = _atlas_map(tmp_path, gameplay_report=value)
    with pytest.raises(MapError, match='"gameplay_report" must be true or false'):
        gameplay_report_enabled(root)
    with pytest.raises(MapError, match='"gameplay_report" must be true or false'):
        store_gameplay_report(root, value)  # type: ignore[arg-type]


def test_store_gameplay_report_needs_a_map(tmp_path: Path) -> None:
    with pytest.raises(MapError, match="not found"):
        store_gameplay_report(tmp_path / "nowhere", True)


def test_repetition_is_off_without_the_key_and_stored_on_request(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import repetition_enabled, store_repetition

    assert repetition_enabled(tmp_path / "nowhere") is False
    root = _atlas_map(tmp_path)
    assert "repetition" not in load_map(root)
    assert repetition_enabled(root) is False
    store_repetition(root, True)
    assert load_map(root)["repetition"] is True and repetition_enabled(root) is True
    store_repetition(root, False)
    assert load_map(root)["repetition"] is False and repetition_enabled(root) is False


@pytest.mark.parametrize("value", [1, "yes", None, 0, "true", []])
def test_repetition_refuses_a_value_that_is_not_a_bool(tmp_path: Path, value: object) -> None:
    from onward_generator.maps.workspace import repetition_enabled, store_repetition

    root = _atlas_map(tmp_path, repetition=value)
    with pytest.raises(MapError) as caught:
        repetition_enabled(root)
    assert str(caught.value) == f'map.json "repetition" must be true or false, got {value!r}'
    with pytest.raises(MapError) as stored:
        store_repetition(root, value)  # type: ignore[arg-type]
    assert str(stored.value) == f'"repetition" must be true or false, got {value!r}'


def test_store_repetition_needs_a_map(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import store_repetition

    with pytest.raises(MapError, match="not found"):
        store_repetition(tmp_path / "nowhere", True)


def test_refresh_with_repetition_picks_calm_roofs_and_paving(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import refresh_texture_plan, store_repetition
    from onward_generator.materials.library_plan import deterministic_choices, library_rows

    root = _atlas_map(tmp_path)
    seed = int(load_map(root).get("seed") or 0)
    rows = library_rows()

    def picks() -> dict[str, str]:
        plan = json.loads(refresh_texture_plan(root).read_text(encoding="utf-8"))
        return {row["id"]: row["library_id"] for row in plan["entries"]}

    assert picks() == deterministic_choices(rows, seed)
    store_repetition(root, True)
    assert picks() == deterministic_choices(rows, seed, extended=True)
    assert deterministic_choices(rows, seed, extended=True) != deterministic_choices(rows, seed)
