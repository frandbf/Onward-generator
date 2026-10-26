"""A map folder is its own centre, name and area. Belluga is not involved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely import Point

from onward_generator.cli import main
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.projection import LocalFrame
from onward_generator.maps.workspace import MapError, create_map
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
    )
    assert document["name"] == "Plaza de prueba"
    assert document["seed"] == 7
    assert (root / "config" / "project.json").is_file()
    assert json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["map"]["title"] == "Plaza de prueba"
    assert json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))["build"]["lightmap_texels_per_m"] == 10.0
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
    assert "next: PYTHONPATH=src .venv/bin/python -m onward_generator geo " in shown


def _polygon(ring: tuple[tuple[float, float], ...]):
    from shapely import Polygon

    return Polygon(ring)
