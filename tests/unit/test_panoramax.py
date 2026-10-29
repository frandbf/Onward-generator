"""Panoramax cache. The client is fake; these tests do not open a socket."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.maps.workspace import create_map
from onward_generator.panoramax.__main__ import run_apply
from onward_generator.panoramax.cache import PanoramaxError, cache_pictures
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

SPHERE_ID = "11111111-1111-1111-1111-111111111111"
FLAT_ID = "22222222-2222-2222-2222-222222222222"
JPEG = b"\xff\xd8\xff\xd9"


def _feature(picture_id: str, *, sphere: bool) -> dict[str, Any]:
    interior: dict[str, Any] = {"field_of_view": 360} if sphere else {"focal_length": 5.0}
    return {
        "type": "Feature",
        "id": picture_id,
        "geometry": {"type": "Point", "coordinates": [MURCIA_CENTER_LON, MURCIA_CENTER_LAT]},
        "properties": {
            "license": "CC-BY-SA-4.0",
            "geovisio:producer": "tester",
            "pers:interior_orientation": interior,
            "exif": {"Exif.Image.Artist": "tester"},
        },
        "links": [{"rel": "license", "href": "https://spdx.org/licenses/CC-BY-SA-4.0.html"}],
        "assets": {
            "sd": {"href": f"https://example.test/{picture_id}.jpg", "type": "image/jpeg"},
        },
    }


class _Client:
    def __init__(self, features: list[dict[str, Any]], *, fail: bool = False) -> None:
        self.features = features
        self.fail = fail
        self.bytes_calls: list[str] = []
        self.json_calls: list[str] = []

    def get_json(self, url: str) -> dict[str, Any]:
        self.json_calls.append(url)
        if self.fail:
            raise PanoramaxError("offline")
        return {"type": "FeatureCollection", "features": self.features, "links": []}

    def get_bytes(self, url: str) -> bytes:
        self.bytes_calls.append(url)
        if self.fail:
            raise PanoramaxError("offline")
        return JPEG


def _map(tmp_path: Path, *, panoramax: bool = True) -> Path:
    return create_map(
        tmp_path / "murcia",
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=100,
        panoramax=panoramax,
    ) and tmp_path / "murcia"


@pytest.fixture
def catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "catalogue"
    monkeypatch.setattr("onward_generator.panoramax.cache.catalogue_directory", lambda: path)
    return path


def test_a_sphere_is_stored_and_a_flat_picture_is_not_downloaded(tmp_path: Path, catalogue: Path) -> None:
    root = _map(tmp_path)
    client = _Client([_feature(SPHERE_ID, sphere=True), _feature(FLAT_ID, sphere=False)])
    report = cache_pictures(root, client=client)
    assert report.kept == 1
    assert report.skipped_flat == 1
    jpeg = catalogue / f"{SPHERE_ID}.jpg"
    sidecar = json.loads((catalogue / f"{SPHERE_ID}.source.json").read_text(encoding="utf-8"))
    assert jpeg.read_bytes() == JPEG
    assert sidecar["sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert sidecar["provider"] == "panoramax"
    assert sidecar["license"] == "CC-BY-SA-4.0"
    assert sidecar["author"] == "tester"
    assert sidecar["url"].endswith(f"{SPHERE_ID}.jpg")
    assert sidecar["retrieved_at"]
    index = root / "data" / "raw" / "panoramax"
    assert (index / "search.geojson").is_file()
    assert not (index / f"{SPHERE_ID}.jpg").exists()
    assert not (catalogue / f"{FLAT_ID}.jpg").exists()
    assert client.bytes_calls == [f"https://example.test/{SPHERE_ID}.jpg"]
    cached = cache_pictures(root, client=client)
    assert cached.reused == 1
    assert client.bytes_calls == [f"https://example.test/{SPHERE_ID}.jpg"]


def test_a_second_map_reuses_the_shared_jpeg(tmp_path: Path, catalogue: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    create_map(
        first, map_id="first", name="First",
        latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100, panoramax=True,
    )
    create_map(
        second, map_id="second", name="Second",
        latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON + 0.0004, radius_m=100, panoramax=True,
    )
    client = _Client([_feature(SPHERE_ID, sphere=True)])
    assert cache_pictures(first, client=client).reused == 0
    assert cache_pictures(second, client=client).reused == 1
    assert client.bytes_calls == [f"https://example.test/{SPHERE_ID}.jpg"]
    assert (catalogue / f"{SPHERE_ID}.jpg").is_file()
    assert not (second / "data" / "raw" / "panoramax" / f"{SPHERE_ID}.jpg").exists()


def test_a_jpeg_left_in_the_map_moves_into_the_catalogue(tmp_path: Path, catalogue: Path) -> None:
    root = _map(tmp_path)
    index = root / "data" / "raw" / "panoramax"
    index.mkdir(parents=True)
    jpeg = index / f"{SPHERE_ID}.jpg"
    jpeg.write_bytes(JPEG)
    sidecar = {
        "provider": "panoramax",
        "license": "CC-BY-SA-4.0",
        "sha256": hashlib.sha256(JPEG).hexdigest(),
        "author": "tester",
        "picture_id": SPHERE_ID,
    }
    (index / f"{SPHERE_ID}.source.json").write_text(json.dumps(sidecar), encoding="utf-8")
    client = _Client([_feature(SPHERE_ID, sphere=True)])
    report = cache_pictures(root, client=client)
    assert report.reused == 1
    assert client.bytes_calls == []
    assert (catalogue / f"{SPHERE_ID}.jpg").read_bytes() == JPEG
    assert not jpeg.exists()
    assert (index / "search.geojson").is_file()


def test_a_sphere_in_front_of_a_facade_is_assigned_and_the_others_are_not(tmp_path: Path) -> None:
    from shapely import Point, Polygon

    from onward_generator.geo.projection import LocalFrame
    from onward_generator.maps.workspace import load_map
    from onward_generator.panoramax.match import BuildingFootprint, StreetPicture, match_facades

    root = _map(tmp_path)
    document = load_map(root)
    center = document["center"]
    frame = LocalFrame.create(float(center["longitude"]), float(center["latitude"]), str(document["crs"]))
    block = BuildingFootprint("block", "building", Polygon([(0, 10), (20, 10), (20, 30), (0, 30)]))
    north = BuildingFootprint("north", "building", Polygon([(0, 30), (20, 30), (20, 50), (0, 50)]))
    east = BuildingFootprint("east", "building", Polygon([(20, 10), (40, 10), (40, 30), (20, 30)]))
    west = BuildingFootprint("west", "building", Polygon([(-20, 10), (0, 10), (0, 30), (-20, 30)]))
    interior = BuildingFootprint("room", "building", Polygon([(50, 10), (70, 10), (70, 30), (50, 30)]))
    landmark = BuildingFootprint("tower", "landmark_part", Polygon([(80, 10), (90, 10), (90, 20), (80, 20)]))

    def picture(name: str, xy: tuple[float, float], accuracy: float | None) -> StreetPicture:
        geographic = frame.to_wgs84(Point(xy))
        return StreetPicture(name, float(geographic.x), float(geographic.y), accuracy, "tester", "CC-BY-SA-4.0")

    report = match_facades(
        [block, north, east, west, interior, landmark],
        [
            picture("front", (10, 2), 4.0),
            picture("farther", (10, -5), 4.0),
            picture("behind", (10, 40), 4.0),
            picture("inside", (10, 20), 4.0),
            picture("distant", (10, -30), 4.0),
            picture("loose", (10, 4), 20.0),
            picture("unspecified", (10, -12), None),
        ],
        frame=frame,
        interior_ids=["room"],
    )
    chosen = [row for row in report["assignments"] if row["building_id"] == "block"]
    front = [row for row in chosen if row["picture_id"] == "front"]
    assert len(front) == 1
    assert front[0]["s1_m"] - front[0]["s0_m"] < 20.0
    assert set(row["picture_id"] for row in chosen) <= {"front", "farther"}
    assert chosen[0]["origin"] == "observed"
    assert chosen[0]["accuracy"] == "recorded"
    alone = match_facades(
        [block, north, east, west],
        [picture("unspecified", (10, 2), None)],
        frame=frame,
        interior_ids=[],
    )
    unknown = [row for row in alone["assignments"] if row["building_id"] == "block"]
    assert unknown[0]["picture_id"] == "unspecified"
    assert unknown[0]["accuracy"] == "unknown"
    assert not any(row["picture_id"] in {"behind", "inside", "distant", "loose"} for row in report["assignments"])
    reasons = {row["building_id"]: row["reason"] for row in report["skipped_buildings"]}
    assert reasons["room"] == "interior"
    assert reasons["tower"] == "landmark"
    assert not (tmp_path / "scene_panoramax").exists()


def test_the_facing_wall_receives_the_painted_colour_and_the_opposite_wall_does_not() -> None:
    from PIL import Image

    from onward_generator.panoramax.project import direction_uv, project_facade

    camera = (0.0, 0.0, 1.5)
    facing = (0.0, 8.0, 3.0)
    opposite = (0.0, -8.0, 3.0)
    image = Image.new("RGB", (360, 180), (0, 0, 0))
    pixels = image.load()
    assert pixels is not None

    def paint(direction: tuple[float, float, float], colour: tuple[int, int, int]) -> None:
        u, v = direction_uv(
            direction[0] - camera[0],
            direction[1] - camera[1],
            direction[2] - camera[2],
            0.0,
        )
        cx = int(u * 360) % 360
        cy = min(179, max(0, int(round(v * 179))))
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                pixels[(cx + dx) % 360, min(179, max(0, cy + dy))] = colour

    paint(facing, (220, 20, 20))
    paint(opposite, (20, 20, 220))
    front = project_facade(
        image, camera_xyz=camera, azimuth_deg=0.0,
        wall_a=(-4.0, 8.0), wall_b=(4.0, 8.0), height_m=6.0,
    )
    back = project_facade(
        image, camera_xyz=camera, azimuth_deg=0.0,
        wall_a=(-4.0, -8.0), wall_b=(4.0, -8.0), height_m=6.0,
    )
    assert front.base_color == (1.0, 1.0, 1.0, 1.0)
    assert max(front.width, front.height) == 512
    front_px = Image.open(io.BytesIO(front.png)).convert("RGB").getpixel((front.width // 2, front.height // 2))
    back_px = Image.open(io.BytesIO(back.png)).convert("RGB").getpixel((back.width // 2, back.height // 2))
    assert front_px[0] > 180 and front_px[2] < 40
    assert back_px[2] > 180 and back_px[0] < 40


def test_materials_next_stays_on_signs_until_the_flag_is_on(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.maps.workspace import store_panoramax
    from onward_generator.materials.__main__ import main as materials_main

    def fake_apply(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"applied": [], "skipped": [], "brick_nodes": [], "cobble_applied": False}

    monkeypatch.setattr("onward_generator.materials.__main__.apply_materials", fake_apply)
    root = _map(tmp_path, panoramax=False)
    out = root / "data" / "processed" / "scene_materials"
    assert materials_main(["apply", "--base", str(out), "--out", str(out)]) == 0
    first = capsys.readouterr().out
    assert "signs place" in first
    assert "panoramax apply" not in first
    store_panoramax(root, True)
    assert materials_main(["apply", "--base", str(out), "--out", str(out)]) == 0
    second = capsys.readouterr().out
    assert "panoramax apply" in second
    assert "scene_panoramax" in second


def test_panoramax_apply_points_at_signs(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root = _map(tmp_path)
    base = root / "data" / "processed" / "scene_materials"
    out = root / "data" / "processed" / "scene_panoramax"
    monkeypatch.setattr("onward_generator.panoramax.__main__.cache_pictures", lambda *_a, **_k: type("Report", (), {"kept": 0})())
    monkeypatch.setattr("onward_generator.panoramax.__main__.apply_pictures", lambda *_a, **_k: {"assignments": []})
    assert run_apply(base, out) == 0
    printed = capsys.readouterr().out
    assert "signs place" in printed
    assert "scene_panoramax" in printed


def test_a_network_failure_does_not_create_the_output_directory(tmp_path: Path, catalogue: Path) -> None:
    root = _map(tmp_path)
    out = tmp_path / "scene_panoramax"
    code = run_apply(
        root / "data" / "processed" / "scene_materials",
        out,
        client=_Client([], fail=True),
    )
    assert code == 1
    assert not out.exists()
    assert not (root / "data" / "raw" / "panoramax").exists()
    assert not catalogue.exists()
