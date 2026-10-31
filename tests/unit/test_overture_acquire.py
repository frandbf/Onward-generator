"""``overture fetch`` against a synthetic local parquet: the S3 read is replaced by a local path (offline)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from onward_generator.maps.workspace import create_map
from onward_generator.osm.acquire import inclusion_bbox
from onward_generator.overture import acquire
from onward_generator.overture.acquire import (
    LicenseCount,
    OvertureError,
    attribution_lines,
    check_release,
    clip_query,
    fetch_map,
    fetch_theme,
    latest_release,
    license_counts,
    load_record,
    sidecar_path,
    theme_source,
    theme_target,
)
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON
from tests.overture_fixture import local_connect, place, source, write_places

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
BBOX = (37.98, -1.135, 37.99, -1.12)


def _places(tmp_path: Path) -> Path:
    return write_places(tmp_path / "remote" / "places.parquet", [
        place("b", 0.0, 0.0, sources=[source("Foursquare", "Apache-2.0")]),
        place("a", 10.0, 5.0, sources=[source("Instituto Geográfico Nacional (España)", None), source("meta", "X", "/properties/name")]),
        place("c", 5000.0, 0.0),  # 5 km east: outside BBOX
        place("d", -20.0, 3.0, sources=[source("Foursquare", "Apache-2.0")]),
    ])


def test_release_names_are_checked_before_they_reach_a_path() -> None:
    assert check_release("2026-10-21.0") == "2026-10-21.0"
    for bad in ("latest", "2026-10-21", "2026-10-21.0/../x", " 2026-10-21.0"):
        with pytest.raises(OvertureError, match="not an Overture release"):
            check_release(bad)
    assert theme_source("2026-10-21.0", "places") == "s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*.parquet"
    assert theme_source("2026-10-21.0", "buildings").endswith("/theme=buildings/type=building/*.parquet")


def test_latest_release_reads_the_stac_catalogue() -> None:
    seen: list[str] = []

    def stac(url: str) -> bytes:
        seen.append(url)
        return json.dumps({"latest": "2026-10-21.0", "links": []}).encode()

    assert latest_release(stac) == "2026-10-21.0"
    assert seen == ["https://stac.overturemaps.org/catalog.json"]
    with pytest.raises(OvertureError, match="names no latest release"):
        latest_release(lambda url: b'{"links": []}')
    with pytest.raises(OvertureError, match="not a STAC catalogue"):
        latest_release(lambda url: b"<html>")
    with pytest.raises(OvertureError, match="not a STAC catalogue"):
        latest_release(lambda url: b"[1]")
    with pytest.raises(OvertureError, match="not an Overture release"):
        latest_release(lambda url: b'{"latest": "../x"}')


def test_stac_fetch_asks_only_the_stac_host(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake(url: str, **kwargs: object) -> bytes:
        calls.append((url, kwargs))
        return b"{}"

    monkeypatch.setattr(acquire, "fetch_bytes", fake)
    assert acquire._stac_fetch("https://stac.overturemaps.org/catalog.json") == b"{}"
    (url, kwargs), = calls
    assert url == "https://stac.overturemaps.org/catalog.json"
    assert kwargs["allowed_hosts"] == frozenset({"stac.overturemaps.org"})
    assert kwargs["max_bytes"] == 8 * 1024 * 1024 and kwargs["timeout"] == 60.0
    assert kwargs["retry_429"] == 3 and kwargs["retry_transient"] == 3 and kwargs["error"] is OvertureError
    assert kwargs["headers"] == {"User-Agent": acquire.USER_AGENT, "Accept": "application/json"}


def test_clip_query_keeps_rows_whose_bbox_meets_the_map() -> None:
    query = clip_query("s3://b/x/*.parquet", (37.5, -1.25, 38.5, -1.0))
    assert query == (
        "SELECT * FROM read_parquet('s3://b/x/*.parquet', hive_partitioning = false)\n"
        "WHERE bbox.xmin <= -1.0000000 AND bbox.xmax >= -1.2500000 AND bbox.ymin <= 38.5000000 AND bbox.ymax >= 37.5000000\n"
        "ORDER BY id"
    )
    assert "'it''s'" in clip_query("it's", (0.0, 0.0, 1.0, 1.0))
    for bad in ((1.0, 0.0, 1.0, 2.0), (0.0, 2.0, 1.0, 2.0), (2.0, 0.0, 1.0, 1.0)):
        with pytest.raises(OvertureError, match="invalid bbox"):
            clip_query("x", bad)


def test_fetch_theme_clips_orders_and_records_licences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # a relative path that should have been the temporary file finds nothing here
    remote = _places(tmp_path)
    target = tmp_path / "raw" / "overture_places.parquet"
    record = fetch_theme("places", BBOX, target, release="2026-10-21.0", source=str(remote), connect=local_connect, now=NOW)
    ids = [r[0] for r in duckdb.connect().execute("SELECT id FROM read_parquet(?)", [str(target)]).fetchall()]
    assert ids == ["a", "b", "d"]
    assert record.rows == 3 and record.theme == "places" and record.type == "place"
    assert record.release == "2026-10-21.0" and record.source == str(remote) and record.retrieved_at == "2026-10-30T12:00:00+00:00"
    assert record.bbox == list(BBOX) and record.bbox_order == "south,west,north,east"
    assert record.provider == "overture-maps-foundation" and record.duckdb_version == duckdb.__version__
    assert record.path == str(target) and record.size_bytes == target.stat().st_size
    assert record.licenses == [
        {"dataset": "Foursquare", "license": "Apache-2.0", "records": 2, "license_origin": "record"},
        {"dataset": "Instituto Geográfico Nacional (España)", "license": "CC-BY-4.0", "records": 1,
         "license_origin": "overture_attribution_page"},
    ]
    assert record.attribution[0]["notice"].startswith("Copyright 2024 Foursquare Labs, Inc. All rights reserved.")
    assert record.attribution[1] == {"text": "Work derived from BTN 2024 ign.es; Overture Maps Foundation", "license": "CC-BY-4.0",
                                     "license_url": "https://creativecommons.org/licenses/by/4.0/"}
    assert json.loads(sidecar_path(target).read_text(encoding="utf-8")) == record.to_dict()
    assert load_record(target) == record
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    assert sorted(p.name for p in target.parent.iterdir()) == sorted([target.name, sidecar_path(target).name])


def test_the_file_is_world_readable_whatever_the_umask(tmp_path: Path) -> None:
    import os

    previous = os.umask(0o077)
    try:
        target = tmp_path / "raw" / "x.parquet"
        fetch_theme("places", BBOX, target, release="2026-10-21.0", source=str(_places(tmp_path)), connect=local_connect, now=NOW)
    finally:
        os.umask(previous)
    assert oct(target.stat().st_mode & 0o777) == "0o644"


def test_a_failure_after_the_copy_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(connection: object, parquet: Path) -> list:
        assert parquet.is_file()
        raise RuntimeError("licence read failed")

    monkeypatch.setattr(acquire, "license_counts", broken)
    with pytest.raises(RuntimeError, match="licence read failed"):
        fetch_theme("places", BBOX, tmp_path / "o" / "x.parquet", release="2026-10-21.0", source=str(_places(tmp_path)),
                    connect=local_connect, now=NOW)
    assert list((tmp_path / "o").iterdir()) == []


def test_a_stale_temporary_file_is_replaced(tmp_path: Path) -> None:
    target = tmp_path / "o" / "x.parquet"
    target.parent.mkdir()
    (target.parent / ".x.parquet.tmp").write_bytes(b"stale")
    record = fetch_theme("places", BBOX, target, release="2026-10-21.0", source=str(_places(tmp_path)), connect=local_connect, now=NOW)
    assert record.rows == 3 and sorted(p.name for p in target.parent.iterdir()) == ["x.parquet", "x.parquet.source.json"]


def test_load_record_refuses_a_changed_file_or_a_missing_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "raw" / "overture_places.parquet"
    fetch_theme("places", BBOX, target, release="2026-10-21.0", source=str(_places(tmp_path)), connect=local_connect, now=NOW)
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(OvertureError, match="does not match the sha256"):
        load_record(target)
    sidecar_path(target).unlink()
    with pytest.raises(OvertureError, match="missing provenance sidecar"):
        load_record(target)


def test_license_counts_fall_back_to_unknown() -> None:
    assert acquire._license_count("Somebody", None, 3) == LicenseCount("Somebody", "unknown", 3, "unknown")
    assert acquire._license_count("Somebody", "", 3).license_origin == "unknown"
    assert acquire._license_count("Google Open Buildings", None, 1) == LicenseCount("Google Open Buildings", "CC-BY-4.0", 1, "overture_attribution_page")
    assert acquire._license_count("meta", "CDLA-Permissive-2.0", 2) == LicenseCount("meta", "CDLA-Permissive-2.0", 2, "record")


def test_license_counts_use_only_the_whole_record_source(tmp_path: Path) -> None:
    path = write_places(tmp_path / "p.parquet", [
        place("x", 0, 0, sources=[source("meta", "CDLA-Permissive-2.0", "/properties/name"), source(None, None)]),  # type: ignore[arg-type]
    ])
    assert license_counts(duckdb.connect(), path) == [LicenseCount("unknown", "unknown", 1, "unknown")]


def test_attribution_lines_use_the_overture_wording() -> None:
    lines = attribution_lines([("meta", "CDLA-Permissive-2.0"), ("OpenStreetMap", "ODbL-1.0"), ("meta", "CDLA-Permissive-2.0"), ("New", "Odd")])
    assert lines == [
        {"text": "Data from New; Overture Maps Foundation", "license": "Odd", "license_url": ""},
        {"text": "© OpenStreetMap contributors, Overture Maps Foundation", "license": "ODbL-1.0",
         "license_url": "https://opendatacommons.org/licenses/odbl/1-0/"},
        {"text": "Data from meta; Overture Maps Foundation", "license": "CDLA-Permissive-2.0", "license_url": "https://cdla.dev/permissive-2-0/"},
    ]


class _Flaky:
    """A connection factory whose first ``failures`` COPY statements raise an HTTP error."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.closed = 0

    def __call__(self) -> duckdb.DuckDBPyConnection:
        real = duckdb.connect()
        outer = self

        class Wrapped:
            def execute(self, sql: str, *args: object) -> object:
                if sql.startswith("COPY") and outer.failures:
                    outer.failures -= 1
                    raise duckdb.HTTPException("HTTP 503")
                return real.execute(sql, *args)

            def close(self) -> None:
                outer.closed += 1
                real.close()

        return Wrapped()  # type: ignore[return-value]


def test_a_failed_read_is_retried_with_doubling_waits(tmp_path: Path) -> None:
    remote = _places(tmp_path)
    waits: list[float] = []
    flaky = _Flaky(3)
    record = fetch_theme("places", BBOX, tmp_path / "out.parquet", release="2026-10-21.0", source=str(remote),
                         connect=flaky, now=NOW, sleep=waits.append)
    assert record.rows == 3 and waits == [8.0, 16.0, 32.0] and flaky.closed == 4


def test_a_read_that_keeps_failing_stops_after_three_retries_and_leaves_no_file(tmp_path: Path) -> None:
    remote = _places(tmp_path)
    waits: list[float] = []
    with pytest.raises(OvertureError, match="failed after 4 attempts: HTTP 503"):
        fetch_theme("places", BBOX, tmp_path / "out" / "x.parquet", release="2026-10-21.0", source=str(remote),
                    connect=_Flaky(4), now=NOW, sleep=waits.append)
    assert waits == [8.0, 16.0, 32.0]
    assert list((tmp_path / "out").iterdir()) == []


def test_a_query_error_is_not_retried(tmp_path: Path) -> None:
    remote = write_places(tmp_path / "p.parquet", [place("x", 0, 0)])
    duckdb.connect().execute(f"COPY (SELECT 1 AS id) TO '{remote}' (FORMAT parquet)")  # no bbox column
    waits: list[float] = []
    with pytest.raises(duckdb.BinderException):
        fetch_theme("places", BBOX, tmp_path / "o" / "x.parquet", release="2026-10-21.0", source=str(remote),
                    connect=local_connect, sleep=waits.append)
    assert waits == []
    assert list((tmp_path / "o").iterdir()) == []


def _map(tmp_path: Path) -> Path:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    return root


def test_fetch_map_writes_both_themes_for_the_map_bbox(tmp_path: Path) -> None:
    root = _map(tmp_path)
    remote = _places(tmp_path)
    waits: list[float] = []
    records = fetch_map(root, release="2026-10-21.0", sources={"places": str(remote), "buildings": str(remote)},
                        connect=_Flaky(1), now=NOW, stac=lambda url: pytest.fail("release given"), sleep=waits.append)
    assert waits == [8.0] and {r.retrieved_at for r in records} == {"2026-10-30T12:00:00+00:00"}
    assert {r.source for r in records} == {str(remote)}
    assert [r.theme for r in records] == ["places", "buildings"]
    assert [Path(r.path) for r in records] == [theme_target(root, "places"), theme_target(root, "buildings")]
    assert theme_target(root, "places") == root.resolve() / "data" / "raw" / "overture_places.parquet"
    assert all(r.bbox == list(inclusion_bbox(root)) for r in records)
    assert records[0].rows == 3  # c is 5 km away; the 150 m map plus margin keeps a, b and d


def test_fetch_map_resolves_the_latest_release_and_refuses_to_overwrite(tmp_path: Path) -> None:
    root = _map(tmp_path)
    remote = str(_places(tmp_path))
    stac = lambda url: b'{"latest": "2026-09-23.1"}'  # noqa: E731 - one-line fake
    first = fetch_map(root, sources={"places": remote, "buildings": remote}, connect=local_connect, stac=stac)
    assert {r.release for r in first} == {"2026-09-23.1"}
    with pytest.raises(FileExistsError) as caught:
        fetch_map(root, sources={"places": remote, "buildings": remote}, connect=lambda: pytest.fail("no request"), stac=stac)
    raw = root.resolve() / "data" / "raw"
    assert str(caught.value) == "refusing to overwrite " + ", ".join(
        str(raw / name) for name in ("overture_places.parquet", "overture_places.parquet.source.json",
                                     "overture_buildings.parquet", "overture_buildings.parquet.source.json"))
    sidecar_path(theme_target(root, "places")).unlink()
    theme_target(root, "places").unlink()
    with pytest.raises(FileExistsError, match="overture_buildings"):
        fetch_map(root, sources={"places": remote, "buildings": remote}, connect=lambda: pytest.fail("no request"), stac=stac)
    again = fetch_map(root, sources={"places": remote, "buildings": remote}, connect=local_connect, stac=stac, overwrite=True,
                      release="2026-10-21.0")
    assert {r.release for r in again} == {"2026-10-21.0"}


def test_fetch_map_writes_where_out_says(tmp_path: Path) -> None:
    root = _map(tmp_path)
    remote = str(_places(tmp_path))
    records = fetch_map(root, release="2026-10-21.0", sources={"places": remote, "buildings": remote}, connect=local_connect,
                        out=tmp_path / "elsewhere")
    assert [Path(r.path) for r in records] == [tmp_path.resolve() / "elsewhere" / "overture_places.parquet",
                                               tmp_path.resolve() / "elsewhere" / "overture_buildings.parquet"]
    assert not theme_target(root, "places").exists()


def test_fetch_map_needs_a_map_folder(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import MapError

    with pytest.raises(MapError):
        fetch_map(tmp_path, release="2026-10-21.0", connect=local_connect)


def test_fetch_map_reports_a_map_whose_area_cannot_be_read(tmp_path: Path) -> None:
    root = _map(tmp_path)
    (root / "reference" / "aoi.geojson").write_text("{}", encoding="utf-8")
    with pytest.raises(OvertureError) as caught:
        fetch_map(root, release="2026-10-21.0", connect=lambda: pytest.fail("no request"))
    from onward_generator.osm.acquire import OsmDownloadError

    with pytest.raises(OsmDownloadError) as expected:
        inclusion_bbox(root)
    assert str(caught.value) == str(expected.value) and str(caught.value)


def test_fetch_theme_defaults_to_the_release_on_s3_and_the_current_time(tmp_path: Path) -> None:
    seen: list[str] = []

    class Recording:
        def execute(self, sql: str, *args: object) -> None:
            seen.append(sql)
            raise duckdb.IOException("offline")

        def close(self) -> None:
            pass

    with pytest.raises(OvertureError):
        fetch_theme("buildings", BBOX, tmp_path / "a" / "b" / "x.parquet", release="2026-10-21.0", connect=Recording,  # type: ignore[arg-type]
                    sleep=lambda s: None)
    assert seen[0] == f"COPY ({clip_query(theme_source('2026-10-21.0', 'buildings'), BBOX)}) TO '{tmp_path / 'a' / 'b' / '.x.parquet.tmp'}' (FORMAT parquet, COMPRESSION zstd)"
    record = fetch_theme("places", BBOX, tmp_path / "c" / "d" / "x.parquet", release="2026-10-21.0", source=str(_places(tmp_path)),
                         connect=local_connect)
    assert record.retrieved_at.endswith("+00:00") and record.query == clip_query(str(_places(tmp_path)), BBOX)
    side = sidecar_path(tmp_path / "c" / "d" / "x.parquet").read_text(encoding="utf-8")
    assert side == json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n"


def test_remote_connection_loads_httpfs_and_sets_the_s3_region(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []

    class Fake:
        def __init__(self, missing: bool) -> None:
            self.missing = missing

        def execute(self, sql: str) -> None:
            statements.append(sql)
            if sql == "LOAD httpfs" and self.missing:
                self.missing = False
                raise duckdb.IOException("not installed")

    monkeypatch.setattr(acquire.duckdb, "connect", lambda: Fake(missing=False))
    acquire._remote_connection()
    assert statements == ["LOAD httpfs", "SET s3_region = 'us-west-2'", "SET http_timeout = 120", "SET http_retries = 3"]
    statements.clear()
    monkeypatch.setattr(acquire.duckdb, "connect", lambda: Fake(missing=True))
    acquire._remote_connection()
    assert statements[:3] == ["LOAD httpfs", "INSTALL httpfs", "LOAD httpfs"]
