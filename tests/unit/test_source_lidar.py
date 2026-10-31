"""LiDAR height plugins: the shared fetch/normalize and es.pnoa_ndsm, with fake services (ADR-018)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import box
from shapely.geometry import mapping

from onward_generator.cli import main
from onward_generator.maps.workspace import create_map
from onward_generator.pipeline_run import plan_steps, source_plan
from onward_generator.sources import lidar
from onward_generator.sources.es.pnoa_ndsm import PnoaNdsmPlugin, asc_part
from onward_generator.sources.plugin import SourceError
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

ORIGIN = (664343.0, 4205697.5)  # on the 2.5 m grid, near Murcia
PNOA = PnoaNdsmPlugin()


def _feature(building_id: str, x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    return {"id": building_id, "kind": "building", "tags": {"building": "yes"}, "geometry": mapping(box(x0, y0, x1, y1))}


def _scene(*features: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": {"crs": "EPSG:25830", "coordinate_system": "local_grid", "unit": "m", "origin_projected": list(ORIGIN)},
        "features": list(features) or [
            _feature("osm-way-2", 0, 0, 20, 10),  # 18 m
            _feature("osm-way-1", 30, 0, 40, 10),  # nothing: 0 m cells
            _feature("osm-way-3", 0, -30, 10, -20),  # 150 m: above the heights bounds
            {"id": "osm-node-9", "kind": "poi", "tags": {}, "geometry": {"type": "Point", "coordinates": [1.0, 1.0]}},
        ],
    }


def _height(x: float, y: float) -> float:
    lx, ly = x - ORIGIN[0], y - ORIGIN[1]
    if 0 <= lx <= 20 and 0 <= ly <= 10:
        return 18.0
    if 0 <= lx <= 10 and -30 <= ly <= -20:
        return 150.0
    return 0.0


def _asc_for(url: str) -> bytes:
    """A WCS multipart reply holding the ASCII grid of the asked box, heights from ``_height``."""
    (x0, x1), (y0, y1) = (tuple(float(v) for v in re.search(rf"subset={axis}\(([^,]+),([^)]+)\)", url).groups()) for axis in "xy")
    columns, rows = round((x1 - x0) / 2.5), round((y1 - y0) / 2.5)
    values = [[_height(x0 + (c + 0.5) * 2.5, y1 - (r + 0.5) * 2.5) for c in range(columns)] for r in range(rows)]
    grid = "\n".join(" ".join(f"{v:.3f}" for v in row) for row in values)
    body = f"ncols {columns}\nnrows {rows}\nxllcorner {x0}\nyllcorner {y0}\ncellsize 2.5\n{grid}\n"
    return (
        "\r\n--wcs\r\nContent-Type: application/asc\r\nContent-ID: coverage/out.asc\r\n\r\n" + body
        + "\r\n--wcs\r\nContent-Type: application/octet-stream\r\n\r\nPROJCS[]\r\n--wcs--\r\n"
    ).encode()


class FakeService:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return _asc_for(url)


@pytest.fixture
def scene_file(tmp_path: Path) -> Path:
    path = tmp_path / "geo_scene.json"
    path.write_text(json.dumps(_scene()), encoding="utf-8")
    return path


def _fetched(tmp_path: Path, scene_file: Path) -> Path:
    raw = tmp_path / "raw" / "es.pnoa_ndsm"
    lidar.fetch_raster(PNOA, scene_file, raw, fetch=FakeService(), now=__import__("datetime").datetime(2026, 10, 30, tzinfo=__import__("datetime").UTC))
    return raw


# --- the plugin ------------------------------------------------------------------------------


def test_pnoa_plugin_identity_licence_and_coverage() -> None:
    assert PNOA.id == "es.pnoa_ndsm"
    assert PNOA.produces == frozenset({"height_m"})
    assert PNOA.licence.spdx == "CC-BY-4.0"
    assert PNOA.licence.attribution == "Obra derivada de MDSn 2008-2015 CC-BY 4.0 ign.es"
    assert PNOA.licence.url == "https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf"
    assert PNOA.source_label == "IGN MDSn 2.5 m building class (PNOA-LiDAR 1st coverage, 2008-2015)"
    assert PNOA.hosts == frozenset({"wcs-mds.idee.es"})
    assert PNOA.raster_crs == "EPSG:25830"
    assert PNOA.stage_names == ("pnoa_ndsm", "pnoa_heights")
    assert PNOA.covers(MURCIA_CENTER_LON, MURCIA_CENTER_LAT)
    assert PNOA.covers(-1.6432, 42.8125)  # Pamplona: the IGN covers Navarre
    assert PNOA.covers(2.65, 39.57)  # Palma
    assert PNOA.covers(-15.43, 28.12)  # Las Palmas
    assert not PNOA.covers(-5.32, 35.89)  # Ceuta: not in the MDSn
    assert not PNOA.covers(4.8357, 45.764)  # Lyon
    assert isinstance(PNOA, lidar.RasterHeights)


def test_pnoa_requests_snap_to_the_grid_and_tile_at_2500_m() -> None:
    (only,) = PNOA.requests((664300.4, 4205600.0, 664399.0, 4205701.0))
    assert only.bbox == (664300.0, 4205600.0, 664400.0, 4205702.5)
    assert (only.columns, only.rows) == (40, 41)
    assert only.url == (
        "https://wcs-mds.idee.es/mds?service=WCS&version=2.0.1&request=GetCoverage&CoverageId=mdsn_e025"
        "&format=application/asc&subset=x(664300.00,664400.00)&subset=y(4205600.00,4205702.50)"
        "&subsettingCRS=http://www.opengis.net/def/crs/EPSG/0/25830&outputCRS=http://www.opengis.net/def/crs/EPSG/0/25830"
    )
    many = PNOA.requests((0.0, 0.0, 2600.0, 10.0))
    assert [(r.columns, r.rows) for r in many] == [(1000, 4), (40, 4)]


def test_asc_part_unwraps_the_multipart_reply_or_takes_a_bare_grid() -> None:
    bare = b"ncols 1\nnrows 1\nxllcorner 0\nyllcorner 0\ncellsize 1\n5\n"
    assert asc_part(b"\n" + bare) == bare
    wrapped = b"--wcs\nContent-Type: application/asc\n\n" + bare + b"\n--wcs--\n"
    assert asc_part(wrapped) == bare.strip()
    crlf = b"\r\n--wcs\r\nContent-Type: text/xml\r\n\r\n<x/>\r\n--wcs\r\nContent-Type: application/asc\r\n\r\n" + bare + b"\r\n--wcs--"
    assert asc_part(crlf) == bare.strip()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<ExceptionReport>no</ExceptionReport>", "WCS response is neither a grid nor multipart: b'<ExceptionReport>no</ExceptionReport>'"),
        (b"--wcs\r\nContent-Type: text/xml\r\n\r\n<x/>\r\n--wcs--", "WCS multipart response has no application/asc part"),
        (b"--wcs\r\nContent-Type: application/asc no blank line", "WCS multipart response has no application/asc part"),
    ],
)
def test_asc_part_refuses_an_error_reply(body: bytes, message: str) -> None:
    with pytest.raises(SourceError) as caught:
        asc_part(body)
    assert str(caught.value) == message


def test_pnoa_decode_checks_the_grid_size() -> None:
    body = _asc_for("subset=x(0,5)&subset=y(0,2.5)")
    assert PNOA.decode(body, {"rows": 1, "columns": 2}).values.shape == (1, 2)
    with pytest.raises(SourceError) as caught:
        PNOA.decode(body, {"rows": 2, "columns": 2})
    assert str(caught.value) == "WCS grid is (1, 2), asked for (2, 2)"


def test_pnoa_metadata_and_date_name_the_first_coverage() -> None:
    meta = PNOA.metadata((0, 0, 1, 1), lambda url: pytest.fail("no request"))
    assert meta == {"series": "PNOA-LiDAR 1st coverage, 2008-2015", "coverage": "mdsn_e025", "service": "https://wcs-mds.idee.es/mds"}
    assert PNOA.data_date(-1.1, 37.9, meta) == "PNOA-LiDAR 1st coverage, 2008-2015"


# --- fetch -----------------------------------------------------------------------------------


def test_fetch_keeps_every_reply_and_a_sidecar(tmp_path: Path, scene_file: Path) -> None:
    service = FakeService()
    raw = tmp_path / "raw" / "es.pnoa_ndsm"
    record = lidar.fetch_raster(PNOA, scene_file, raw, fetch=service, now=__import__("datetime").datetime(2026, 10, 30, tzinfo=__import__("datetime").UTC))
    assert len(service.urls) == 1
    assert record["bbox"] == [ORIGIN[0] - 20, ORIGIN[1] - 50, ORIGIN[0] + 60, ORIGIN[1] + 30]
    assert record["retrieved_at"] == "2026-10-30T00:00:00+00:00"
    assert record["source"] == "es.pnoa_ndsm"
    assert record["license"] == "CC-BY-4.0"
    assert record["attribution"] == "Obra derivada de MDSn 2008-2015 CC-BY 4.0 ign.es"
    assert record["crs"] == "EPSG:25830"
    assert record["user_agent"].startswith("onward-generator/")
    assert record["metadata"]["series"] == "PNOA-LiDAR 1st coverage, 2008-2015"
    assert record["scene"]["path"] == str(scene_file)
    (tile,) = record["tiles"]
    assert tile["file"] == "tile_000.bin" and tile["url"] == service.urls[0]
    assert (raw / "tile_000.bin").read_bytes() == _asc_for(service.urls[0])
    assert tile["size_bytes"] == len(_asc_for(service.urls[0]))
    assert json.loads((tmp_path / "raw" / "es.pnoa_ndsm.source.json").read_text(encoding="utf-8")) == record
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        lidar.fetch_raster(PNOA, scene_file, raw, fetch=service)
    lidar.fetch_raster(PNOA, scene_file, raw, fetch=service, overwrite=True)
    assert sorted(p.name for p in raw.iterdir()) == ["tile_000.bin"]
    assert not list(raw.parent.glob(".es.pnoa_ndsm.*"))


def test_a_sidecar_alone_also_refuses_a_fetch_without_overwrite(tmp_path: Path, scene_file: Path) -> None:
    raw = tmp_path / "raw" / "es.pnoa_ndsm"
    raw.parent.mkdir()
    lidar.sidecar_path(raw).write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        lidar.fetch_raster(PNOA, scene_file, raw, fetch=FakeService())


def test_fetch_writes_nothing_when_a_reply_is_an_error_page(tmp_path: Path, scene_file: Path) -> None:
    raw = tmp_path / "raw" / "es.pnoa_ndsm"
    with pytest.raises(SourceError, match="neither a grid nor multipart"):
        lidar.fetch_raster(PNOA, scene_file, raw, fetch=lambda url: b"<html>busy</html>")
    assert not raw.parent.exists() or not any(raw.parent.iterdir())


def test_fetch_leaves_no_staging_directory_when_writing_fails(tmp_path: Path, scene_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "raw" / "es.pnoa_ndsm"

    def broken(self: Path, data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", broken)
    with pytest.raises(OSError, match="disk full"):
        lidar.fetch_raster(PNOA, scene_file, raw, fetch=FakeService())
    assert list((tmp_path / "raw").iterdir()) == []


@pytest.mark.parametrize(
    ("scene", "message"),
    [
        ({"schema": "other"}, "input is not a onward_generator.geo_scene"),
        ({**_scene(), "frame": {**_scene()["frame"], "unit": "ft"}}, "scene must be a local grid in metres"),
        ({**_scene(), "frame": {**_scene()["frame"], "coordinate_system": "wgs84"}}, "scene must be a local grid in metres"),
        ({**_scene(), "features": [{"id": "x", "kind": "poi", "geometry": {"type": "Point", "coordinates": [0, 0]}}]}, "scene has no buildings"),
        (_scene(_feature("osm-way-1", 0, 0, 5961, 10)), "scene box is 6001 x 50 m; limit is 6000 m a side"),
        (_scene(_feature("osm-way-1", 0, 0, 10, 5961)), "scene box is 50 x 6001 m; limit is 6000 m a side"),
    ],
)
def test_fetch_refuses_a_scene_it_cannot_use(tmp_path: Path, scene: dict[str, Any], message: str) -> None:
    path = tmp_path / "s.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    with pytest.raises(SourceError) as caught:
        lidar.fetch_raster(PNOA, path, tmp_path / "raw", fetch=FakeService())
    assert str(caught.value) == message


def test_fetch_refuses_a_missing_scene(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="^scene not found: "):
        lidar.fetch_raster(PNOA, tmp_path / "none.json", tmp_path / "raw", fetch=FakeService())


def test_a_box_of_exactly_the_limit_is_accepted() -> None:
    scene = _scene(_feature("osm-way-1", 0, 0, 5960, 5960))
    assert lidar.scene_bbox(scene, "EPSG:25830") == (ORIGIN[0] - 20, ORIGIN[1] - 20, ORIGIN[0] + 5980, ORIGIN[1] + 5980)


def test_footprints_move_to_the_raster_crs_and_skip_other_features() -> None:
    shapes = lidar.footprints(_scene(), "EPSG:25830")
    assert [i for i, _ in shapes] == ["osm-way-1", "osm-way-2", "osm-way-3"]
    assert shapes[1][1].bounds == (ORIGIN[0], ORIGIN[1], ORIGIN[0] + 20, ORIGIN[1] + 10)
    moved = lidar.footprints(_scene(), "EPSG:4326")
    assert moved[1][1].bounds[0] == pytest.approx(-1.1286, abs=0.01)
    assert moved[1][1].bounds[1] == pytest.approx(37.98, abs=0.01)


def test_to_wgs84_returns_a_degree_box() -> None:
    west, south, east, north = lidar.to_wgs84((664343.0, 4205697.5, 664443.0, 4205797.5), "EPSG:25830")
    assert west < east and south < north
    assert west == pytest.approx(-1.1286, abs=0.01) and south == pytest.approx(37.98, abs=0.01)


def test_http_fetch_uses_the_plugin_hosts_retries_and_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    pauses: list[float] = []

    def fake(url: str, **kwargs: Any) -> bytes:
        seen.update(kwargs, url=url)
        return b"ok"

    monkeypatch.setattr(lidar, "fetch_bytes", fake)
    monkeypatch.setattr(lidar.time, "sleep", pauses.append)
    assert lidar.http_fetch(PNOA, timeout=7.0, pause_s=0.5)("https://wcs-mds.idee.es/mds?x") == b"ok"
    assert seen == {
        "url": "https://wcs-mds.idee.es/mds?x", "allowed_hosts": frozenset({"wcs-mds.idee.es"}), "max_bytes": 64 * 1024 * 1024,
        "timeout": 7.0, "headers": {"User-Agent": lidar.USER_AGENT}, "retry_429": 3, "retry_transient": 3, "error": SourceError,
    }
    assert pauses == [0.5]
    lidar.http_fetch(PNOA)("https://wcs-mds.idee.es/mds?y")
    assert seen["timeout"] == 120.0 and pauses == [0.5, 1.0]


# --- normalize -------------------------------------------------------------------------------


def test_normalize_writes_a_height_per_measured_footprint_and_reports_every_one(tmp_path: Path, scene_file: Path) -> None:
    raw = _fetched(tmp_path, scene_file)
    out = tmp_path / "out"
    report = lidar.normalize_raster(PNOA, scene_file, raw, out)
    overrides = json.loads((out / "heights_overrides.es.pnoa_ndsm.json").read_text(encoding="utf-8"))
    assert overrides["version"] == "0.1.0"
    assert overrides["overrides"] == [{
        "id": "osm-way-2",
        "height_m": 18.0,
        "source": "IGN MDSn 2.5 m building class (PNOA-LiDAR 1st coverage, 2008-2015)",
        "license": "CC-BY-4.0",
        "retrieved_at": "2026-10-30T00:00:00+00:00",
        "confidence": 0.8,
        "note": "p50 of 28/28 cells, inner buffer 1 m; p25-p75 18.0-18.0 m; data PNOA-LiDAR 1st coverage, 2008-2015",
    }]
    assert json.loads((out / "es.pnoa_ndsm_report.json").read_text(encoding="utf-8")) == report
    assert report["schema"] == "onward_generator.source_heights_report"
    assert report["schema_version"] == "1.0.0"
    assert report["source"] == "es.pnoa_ndsm"
    assert report["attribution"] == "Obra derivada de MDSn 2008-2015 CC-BY 4.0 ign.es"
    assert report["license"] == "CC-BY-4.0" and report["provider"] == PNOA.source_label
    assert report["counts"] == {"buildings": 3, "measured": 1, "out_of_bounds": 1, "no_data": 1}
    assert report["height_range_m"] == [18.0, 18.0]
    assert report["rule"]["buffer_m"] == 1.0 and report["rule"]["min_cells"] == 3
    assert report["raw"]["path"] == str(raw) and report["raw"]["retrieved_at"] == "2026-10-30T00:00:00+00:00"
    assert len(report["raw"]["sidecar_sha256"]) == 64
    assert report["scene"]["path"] == str(scene_file)
    assert report["outputs"] == [str(out / "heights_overrides.es.pnoa_ndsm.json"), str(out / "es.pnoa_ndsm_report.json")]
    statuses = {b["id"]: (b["status"], b["height_m"]) for b in report["buildings"]}
    assert statuses == {"osm-way-1": ("no_data", None), "osm-way-2": ("measured", 18.0), "osm-way-3": ("out_of_bounds", 150.0)}
    assert report["buildings"][1]["date"] == "PNOA-LiDAR 1st coverage, 2008-2015"
    assert report["buildings"][1]["cells"] == 28 and report["buildings"][1]["valid"] == 28
    with pytest.raises(FileExistsError):
        lidar.normalize_raster(PNOA, scene_file, raw, out)
    lidar.normalize_raster(PNOA, scene_file, raw, out, overwrite=True)


def test_a_height_at_the_bounds_is_kept(tmp_path: Path) -> None:
    grid = lidar.Grid(np.full((40, 40), 120.0, dtype=np.float32), ORIGIN[0] - 20, ORIGIN[1] + 30, 2.5)
    results = lidar.measure(PNOA, _scene(_feature("osm-way-1", 0, 0, 20, 10)), grid, {}, (2.5, 120.0))
    assert results[0].status == "measured"
    low = lidar.Grid(np.full((40, 40), 2.5, dtype=np.float32), ORIGIN[0] - 20, ORIGIN[1] + 30, 2.5)
    assert lidar.measure(PNOA, _scene(_feature("osm-way-1", 0, 0, 20, 10)), low, {}, (2.5, 120.0))[0].status == "measured"
    assert lidar.measure(PNOA, _scene(_feature("osm-way-1", 0, 0, 20, 10)), low, {}, (2.6, 120.0))[0].status == "out_of_bounds"


def test_normalize_refuses_a_tampered_tile_a_missing_sidecar_and_another_plugin(tmp_path: Path, scene_file: Path) -> None:
    raw = _fetched(tmp_path, scene_file)
    side = lidar.sidecar_path(raw)
    record = json.loads(side.read_text(encoding="utf-8"))
    side.write_text(json.dumps({**record, "source": "fr.lidar_hd"}), encoding="utf-8")
    with pytest.raises(SourceError) as caught:
        lidar.normalize_raster(PNOA, scene_file, raw, tmp_path / "o")
    assert str(caught.value) == f"{side} was written by 'fr.lidar_hd', not es.pnoa_ndsm"
    side.write_text(json.dumps(record), encoding="utf-8")
    (raw / "tile_000.bin").write_bytes(b"x")
    with pytest.raises(SourceError) as caught:
        lidar.normalize_raster(PNOA, scene_file, raw, tmp_path / "o")
    assert str(caught.value) == f"{raw / 'tile_000.bin'} does not match the sha256 recorded in es.pnoa_ndsm.source.json"
    side.unlink()
    with pytest.raises(SourceError) as caught:
        lidar.normalize_raster(PNOA, scene_file, raw, tmp_path / "o")
    assert str(caught.value) == f"missing provenance sidecar {side}; download with `source fetch es.pnoa_ndsm`"
    assert not (tmp_path / "o").exists()


def test_normalize_refuses_a_scene_that_changes_while_read(tmp_path: Path, scene_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _fetched(tmp_path, scene_file)
    real = lidar.measure

    def edit(*args: Any) -> Any:
        scene_file.write_text(json.dumps(_scene(_feature("osm-way-7", 0, 0, 5, 5))), encoding="utf-8")
        return real(*args)

    monkeypatch.setattr(lidar, "measure", edit)
    with pytest.raises(SourceError, match="^scene changed while being read$"):
        lidar.normalize_raster(PNOA, scene_file, raw, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_normalize_refuses_to_write_over_the_scene(tmp_path: Path) -> None:
    out = tmp_path / "o"
    out.mkdir()
    scene = out / "es.pnoa_ndsm_report.json"
    scene.write_text(json.dumps(_scene()), encoding="utf-8")
    raw = _fetched(tmp_path, scene)
    with pytest.raises(ValueError, match="output would overwrite an input file"):
        lidar.normalize_raster(PNOA, scene, raw, out, overwrite=True)


# --- CLI and run -----------------------------------------------------------------------------


def test_cli_fetch_and_normalize(tmp_path: Path, scene_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(lidar, "http_fetch", lambda plugin: FakeService())
    raw = tmp_path / "raw" / "es.pnoa_ndsm"
    assert main(["source", "fetch", "es.pnoa_ndsm", "--scene", str(scene_file), "--out", str(raw)]) == 0
    printed = capsys.readouterr().out.splitlines()
    size = (raw / "tile_000.bin").stat().st_size
    assert printed[0] == f"downloaded {size} bytes in 1 tile(s), bbox {json.loads(lidar.sidecar_path(raw).read_text())['bbox']} (EPSG:25830); licence CC-BY-4.0"
    assert printed[1] == f"wrote {raw} and its .source.json sidecar"
    assert main(["source", "fetch", "es.pnoa_ndsm", "--scene", str(scene_file), "--out", str(raw)]) == 1
    assert main(["source", "fetch", "es.pnoa_ndsm", "--scene", str(scene_file), "--out", str(raw), "--overwrite"]) == 0
    capsys.readouterr()
    out = tmp_path / "o"
    assert main(["source", "normalize", "es.pnoa_ndsm", "--scene", str(scene_file), "--raw", str(raw), "--out", str(out)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "buildings=3 measured=1 no_data=1 out_of_bounds=1",
        f"wrote {out / 'heights_overrides.es.pnoa_ndsm.json'}",
        f"wrote {out / 'es.pnoa_ndsm_report.json'}",
    ]
    assert main(["source", "normalize", "es.pnoa_ndsm", "--scene", str(scene_file), "--raw", str(raw), "--out", str(out), "--overwrite"]) == 0


def test_cli_fetch_refuses_catastro_and_a_licence_off_the_allowlist(tmp_path: Path, scene_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["source", "fetch", "es.catastro", "--scene", str(scene_file), "--out", str(tmp_path / "r")]) == 1
    assert "es.catastro has its own commands (see docs/05-cadastre.md), not source fetch/normalize" in capsys.readouterr().err
    assert main(["source", "normalize", "es.catastro", "--scene", str(scene_file), "--raw", "x", "--out", str(tmp_path / "o")]) == 1
    assert "es.catastro has its own commands" in capsys.readouterr().err
    config = tmp_path / "sources.json"
    config.write_text(json.dumps({"schema": "onward_generator.sources", "licence_allowlist": ["ODbL-1.0"], "priority": ["es.pnoa_ndsm", "es.catastro"]}), encoding="utf-8")
    assert main(["source", "--config", str(config), "fetch", "es.pnoa_ndsm", "--scene", str(scene_file), "--out", str(tmp_path / "r")]) == 1
    assert "es.pnoa_ndsm: licence CC-BY-4.0 is not in licence_allowlist" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()


def test_source_parser_help_for_fetch_and_normalize() -> None:
    import argparse

    from onward_generator.sources.__main__ import build_parser

    subparsers = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    listed = {choice.dest: choice.help for choice in subparsers._choices_actions}
    assert listed["fetch"] == "download a raster plugin's data for the scene box into data/raw/<id> (network)"
    assert listed["normalize"] == "heights_overrides.<id>.json and a report from a fetched raster (offline)"
    fetch = {a.dest: (a.help, a.required) for a in subparsers.choices["fetch"]._actions}
    assert fetch["out"] == ("directory under data/raw/; the sidecar is <out>.source.json", True)
    assert fetch["scene"][1] is True
    normalize = {a.dest: a.required for a in subparsers.choices["normalize"]._actions}
    assert normalize["scene"] and normalize["raw"] and normalize["out"]


def test_run_in_spain_downloads_the_ndsm_after_the_split_and_merges_it_above_catastro(tmp_path: Path) -> None:
    root = (tmp_path / "murcia").resolve()
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    processed = root / "data" / "processed"
    stages, scene, overrides = source_plan(root, processed / "geo" / "geo_scene.json")
    assert [s.name for s in stages] == ["catastro", "cadastre", "pnoa_ndsm", "pnoa_heights", "overrides"]
    split = processed / "cadastre" / "geo_scene.json"
    raw = root / "data" / "raw" / "es.pnoa_ndsm"
    assert list(stages[2].argv) == ["source", "fetch", "es.pnoa_ndsm", "--scene", str(split), "--out", str(raw)]
    assert stages[2].download and stages[2].marker == lidar.sidecar_path(raw) and stages[2].inputs == (split,)
    out = processed / "es.pnoa_ndsm"
    assert list(stages[3].argv) == ["source", "normalize", "es.pnoa_ndsm", "--scene", str(split), "--raw", str(raw), "--out", str(out)]
    assert stages[3].marker == out / "heights_overrides.es.pnoa_ndsm.json"
    assert stages[3].inputs == (lidar.sidecar_path(raw), split)
    assert not stages[3].download
    assert scene == split and overrides == processed / "sources" / "heights_overrides.json"
    steps = dict(plan_steps(root))
    assert steps["heights"][steps["heights"].index("--overrides") + 1] == str(overrides)
