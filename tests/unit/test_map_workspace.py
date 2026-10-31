"""A map folder is its own centre, name and area. Belluga is not involved."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from shapely import Point

from onward_generator.cli import main
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.projection import LocalFrame
from onward_generator.maps.workspace import MapError, create_map, source_enabled, store_sources
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
    assert ("--core-radius-m CORE_RADIUS_M half-side of the playable core square, metres (default 150; 0 or at least "
            "the radius plays the whole map; ADR-017)") in " ".join(capsys.readouterr().out.split())


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
    (150, 150, None),     # the default core on a 150 m map
    (300, 0, None),
])
def test_core_radius_is_stored_only_inside_the_map(tmp_path: Path, radius: float, core: float, stored: float | None) -> None:
    document = create_map(tmp_path / "m", map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                          radius_m=radius, core_radius_m=core)
    assert document["core_radius_m"] == stored


@pytest.mark.parametrize(("core", "message"), [(49.9, "^core_radius_m must be 0 or at least 50$"), (-10, "0 or at least 50"),
                                               (True, "^core_radius_m must be a number$"), ("150", "must be a number")])
def test_core_radius_refuses_a_bad_value(tmp_path: Path, core: object, message: str) -> None:
    with pytest.raises(MapError, match=message):
        create_map(tmp_path / "m", map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                   radius_m=300, core_radius_m=core)  # type: ignore[arg-type]
    assert not (tmp_path / "m").exists()
