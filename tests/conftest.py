from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

# A developer's own Asset Store catalogues (assets/asset_store_*, gitignored, ADR-035) must not change any test:
# set before any test module loads a configuration or a library at import time.
os.environ["ONWARD_LOCAL_CATALOGUES"] = "0"
# The semantic recipes (ADR-036) follow the described library; tests pin the hand-written sets. test_recipes turns them on.
os.environ["ONWARD_SEMANTIC_SETS"] = "0"

from onward_generator.domain.models import AreaOfInterest, SourceDataset
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.pipeline import GeoResult, run_geo_pipeline
from onward_generator.geo.sources import LicenseInfo, register_local_source

FIXTURES = Path(__file__).parent / "fixtures" / "geo"
OSM_FIXTURE = FIXTURES / "synthetic_murcia.osm"
AOI_FIXTURE = FIXTURES / "synthetic_aoi.geojson"
MURCIA_CENTER_LAT = 37.98407
MURCIA_CENTER_LON = -1.12863
MURCIA_CENTER = (MURCIA_CENTER_LON, MURCIA_CENTER_LAT)
"""Murcia city-centre test point, (lon, lat). Every test location derives from it."""
ORIGIN = MURCIA_CENTER
FIXED_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

SYNTHETIC_LICENSE = LicenseInfo(
    provider="synthetic-fixture",
    license="CC0-1.0",
    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
    attribution="Synthetic test data, Onward map generator project",
)


@pytest.fixture(autouse=True)
def _no_tile_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read or write the user's tile cache. A test that wants one sets its own directory."""
    monkeypatch.setenv("ONWARD_GENERATOR_NO_TILE_CACHE", "1")


@pytest.fixture(autouse=True)
def _no_ollama_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never reach the Ollama that tools/docker/run.sh names on macOS (ONWARD_OLLAMA_HOST); a test sets its own."""
    monkeypatch.delenv("ONWARD_OLLAMA_HOST", raising=False)


@pytest.fixture
def without_overture(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry without the world-wide ``overture`` plugin (ADR-021), for tests that pin the national plugins' stages."""
    from onward_generator.sources import registry
    from onward_generator.sources.world.overture import OverturePlugin

    monkeypatch.setattr(registry, "PLUGINS", tuple(p for p in registry.PLUGINS if not isinstance(p, OverturePlugin)))


@pytest.fixture
def synthetic_source() -> SourceDataset:
    return register_local_source(
        OSM_FIXTURE,
        license_info=SYNTHETIC_LICENSE,
        retrieved_at=FIXED_NOW,
        data_timestamp="2026-10-18T00:00:00Z",
        now=FIXED_NOW,
    )


@pytest.fixture
def synthetic_aoi() -> AreaOfInterest:
    return load_aoi_geojson(AOI_FIXTURE)


@pytest.fixture
def pipeline_result(synthetic_source: SourceDataset, synthetic_aoi: AreaOfInterest) -> GeoResult:
    return run_geo_pipeline(synthetic_source, synthetic_aoi, origin=ORIGIN)


def _detail_inputs(root: Path, buildings: object = None, pois: object = None, relations: object = None) -> tuple[Path, Path, Path]:
    from onward_generator.generation.__main__ import main as generate_main
    from onward_generator.geo.pipeline import write_outputs
    from onward_generator.heights import run_heights, write_height_outputs
    from tests.detail_fixture import write_block_osm

    extra = {} if buildings is None else {"buildings": buildings, "pois": pois, "relations": relations or {}}
    osm = write_block_osm(root / "block.osm", ORIGIN, **extra)  # type: ignore[arg-type]
    source = register_local_source(osm, license_info=SYNTHETIC_LICENSE, retrieved_at=FIXED_NOW,
                                   data_timestamp="2026-10-18T00:00:00Z", now=FIXED_NOW)
    result = run_geo_pipeline(source, load_aoi_geojson(AOI_FIXTURE), origin=ORIGIN)
    scene, _ = write_outputs(result, root / "geo")
    heights, _ = write_height_outputs(run_heights(scene), root / "heights")
    base = root / "scene3d"
    assert generate_main(["--scene", str(scene), "--heights", str(heights), "--out", str(base), "--no-preview"]) == 0
    return scene, heights, base


@pytest.fixture(scope="session")
def detail_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """Synthetic street block → geo → heights → validated phase 03 base: (scene, heights, base_dir)."""
    return _detail_inputs(tmp_path_factory.mktemp("detail"))


@pytest.fixture(scope="session")
def row_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """Six touching buildings with school, bank and shop POIs (``ROW_BUILDINGS``): (scene, heights, base_dir)."""
    from tests.detail_fixture import ROW_BUILDINGS, ROW_POIS

    return _detail_inputs(tmp_path_factory.mktemp("row"), ROW_BUILDINGS, ROW_POIS)


@pytest.fixture(scope="session")
def residential_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    """The street block without its bakery: 311, 312 and 313 all take the stair interior. (scene, heights, base_dir)."""
    from tests.detail_fixture import BUILDINGS, POIS

    return _detail_inputs(tmp_path_factory.mktemp("residential"), BUILDINGS, {k: v for k, v in POIS.items() if k != 401})
