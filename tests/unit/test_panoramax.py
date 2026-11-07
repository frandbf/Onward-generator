"""Panoramax cache. The client is fake; these tests do not open a socket."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from onward_generator.maps.workspace import create_map
from onward_generator.panoramax.__main__ import run_apply
from onward_generator.panoramax.apply import _cached_pictures, listed_interiors
from onward_generator.panoramax.cache import DOWNLOAD_WORKERS, PanoramaxError, cache_directory, cache_pictures
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


def test_match_lists_the_exposed_faces_it_considered(tmp_path: Path) -> None:
    from shapely import Polygon, union_all

    from onward_generator.detail.config import load_detail_config
    from onward_generator.detail.facades import refine_exposure
    from onward_generator.geo.projection import LocalFrame
    from onward_generator.maps.workspace import load_map
    from onward_generator.panoramax.match import BuildingFootprint, match_facades

    root = _map(tmp_path)
    document = load_map(root)
    center = document["center"]
    frame = LocalFrame.create(float(center["longitude"]), float(center["latitude"]), str(document["crs"]))
    block = BuildingFootprint("block", "building", Polygon([(0, 10), (20.12345, 10), (20.12345, 30), (0, 30)]))
    east = BuildingFootprint("east", "building", Polygon([(20.12345, 10), (40, 10), (40, 30), (20.12345, 30)]))
    interior = BuildingFootprint("room", "building", Polygon([(50, 10), (70, 10), (70, 30), (50, 30)]))
    landmark = BuildingFootprint("tower", "landmark_part", Polygon([(80, 10), (90, 10), (90, 20), (80, 20)]))
    buildings = [block, east, interior, landmark]
    report = match_facades(buildings, [], frame=frame, interior_ids=["room"])
    expected = []
    for building in (block, east):
        others = union_all([item.polygon for item in buildings if item.id != building.id and item.kind == "building"])
        _refined, segments = refine_exposure(building.polygon, others, load_detail_config())
        expected.extend(
            {"building_id": building.id, "segment_index": segment.index, "length_m": round(segment.length, 3)}
            for segment in segments
            if segment.exposed and segment.ring == 0
        )
    assert report["faces"] == expected
    assert {row["building_id"] for row in report["faces"]} == {"block", "east"}
    # The party wall at x = 20.12345 is not a face: three exposed sides on each building, to the millimetre.
    assert sum(row["length_m"] for row in report["faces"] if row["building_id"] == "block") == pytest.approx(60.246, abs=1e-9)
    assert 20.123 in [row["length_m"] for row in report["faces"]]
    assert report["assignments"] == []
    assert len(report["skipped_faces"]) == len(report["faces"])


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


def _numbered(count: int) -> list[dict[str, Any]]:
    return [_feature(f"{index:08d}-0000-0000-0000-000000000000", sphere=True) for index in range(count)]


def _payload(url: str) -> bytes:
    return JPEG + url.encode("utf-8")


class _ParallelClient(_Client):
    """Holds each download until four are open, then lets the later pictures finish first."""

    def __init__(self, features: list[dict[str, Any]]) -> None:
        super().__init__(features)
        self.barrier = threading.Barrier(4, timeout=5)
        self.lock = threading.Lock()
        self.open = 0
        self.peak = 0

    def get_bytes(self, url: str) -> bytes:
        self.bytes_calls.append(url)
        with self.lock:
            self.open += 1
            self.peak = max(self.peak, self.open)
        self.barrier.wait()
        position = [feature["assets"]["sd"]["href"] for feature in self.features].index(url)
        time.sleep(0.02 * (len(self.features) - position))
        with self.lock:
            self.open -= 1
        return _payload(url)


def test_four_downloads_run_at_once_and_land_in_search_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalogue = tmp_path / "nested" / "catalogue"
    monkeypatch.setattr("onward_generator.panoramax.cache.catalogue_directory", lambda: catalogue)
    root = _map(tmp_path)
    features = _numbered(8)
    client = _ParallelClient(features)
    report = cache_pictures(root, client=client)
    assert client.peak == DOWNLOAD_WORKERS == 4
    assert report.kept == 8
    assert report.reused == 0
    ids = [feature["id"] for feature in features]
    index = root / "data" / "raw" / "panoramax"
    assert json.loads((index / "cache_report.json").read_text(encoding="utf-8"))["kept"] == ids
    assert [item["id"] for item in json.loads((index / "search.geojson").read_text(encoding="utf-8"))["features"]] == ids
    for feature in features:
        url = feature["assets"]["sd"]["href"]
        assert (catalogue / f"{feature['id']}.jpg").read_bytes() == _payload(url)
        sidecar = json.loads((catalogue / f"{feature['id']}.source.json").read_text(encoding="utf-8"))
        assert sidecar["sha256"] == hashlib.sha256(_payload(url)).hexdigest()
        assert sidecar["size_bytes"] == len(_payload(url))
        assert sidecar["url"] == url
        assert sidecar["picture_id"] == feature["id"]


class _FailingClient(_Client):
    """The second picture fails. The others answer slowly, so the failure arrives first."""

    def get_bytes(self, url: str) -> bytes:
        self.bytes_calls.append(url)
        position = [feature["assets"]["sd"]["href"] for feature in self.features].index(url)
        if position == 0:
            return _payload(url)
        if position == 1:
            time.sleep(0.05)
            raise OSError("connection reset")
        time.sleep(0.3)
        return _payload(url)


def test_a_failed_download_stops_the_queue_and_writes_no_index(tmp_path: Path, catalogue: Path) -> None:
    root = _map(tmp_path)
    features = _numbered(12)
    client = _FailingClient(features)
    with pytest.raises(PanoramaxError, match=f"could not download {features[1]['id']}: connection reset"):
        cache_pictures(root, client=client)
    assert len(client.bytes_calls) < len(features)
    assert (catalogue / f"{features[0]['id']}.jpg").is_file()
    assert not (catalogue / f"{features[1]['id']}.jpg").exists()
    assert not (catalogue / f"{features[2]['id']}.jpg").exists()
    assert not (root / "data" / "raw" / "panoramax" / "search.geojson").exists()


def test_a_later_run_downloads_only_the_new_picture_and_keeps_search_order(tmp_path: Path, catalogue: Path) -> None:
    root = _map(tmp_path)
    first, new, last = _numbered(3)
    assert cache_pictures(root, client=_Client([first, last])).reused == 0
    client = _Client([first, new, last])
    report = cache_pictures(root, client=client)
    assert report.reused == 2
    assert report.kept == 3
    assert client.bytes_calls == [new["assets"]["sd"]["href"]]
    kept = json.loads((root / "data" / "raw" / "panoramax" / "cache_report.json").read_text(encoding="utf-8"))["kept"]
    assert kept == [first["id"], new["id"], last["id"]]


def test_cached_pictures_are_listed_by_path_and_azimuth_without_decoding(tmp_path: Path, catalogue: Path) -> None:
    root = _map(tmp_path)
    turned, plain = _numbered(2)
    turned["properties"]["view:azimuth"] = 90
    cache_pictures(root, client=_Client([turned, plain]))
    listed = _cached_pictures(cache_directory(root))
    assert [(picture.id, path, azimuth) for picture, path, azimuth in listed] == [
        (turned["id"], catalogue / f"{turned['id']}.jpg", 90.0),
        (plain["id"], catalogue / f"{plain['id']}.jpg", 0.0),
    ]
    assert listed[0][0].author == "tester"


def test_shop_interiors_are_listed_with_the_stair_interiors() -> None:
    assert listed_interiors({"interiors": ["a", "b"], "shop_interiors": ["s"]}) == ["a", "b", "s"]
    assert listed_interiors({"shop_interiors": ["s"]}) == ["s"]
    assert listed_interiors({"interiors": ["a"], "shop_interiors": None}) == ["a"]
    assert listed_interiors({}) == []
