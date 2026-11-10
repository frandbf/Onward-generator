"""Static coverage tables of source plugins: read from the repository, never from the network (ADR-018 §1, plan D4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.sources import coverage
from onward_generator.sources.plugin import SourceError
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
HOLE = [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]]


def _table(tmp_path: Path, document: object, name: str = "t.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_table_covers_inside_and_edges_but_not_holes_or_outside(tmp_path: Path) -> None:
    path = _table(tmp_path, {"schema": coverage.SCHEMA, "polygons": [[SQUARE, HOLE]]})
    assert coverage.in_coverage(path, 1.0, 1.0)
    assert coverage.in_coverage(path, 10.0, 5.0)  # on the outer ring
    assert coverage.in_coverage(path, 4.0, 5.0)  # on the hole's ring
    assert not coverage.in_coverage(path, 5.0, 5.0)  # inside the hole
    assert not coverage.in_coverage(path, 10.5, 5.0)
    (polygon,) = coverage.load_coverage(path)
    assert len(polygon.interiors) == 1


def test_any_polygon_of_a_table_covers(tmp_path: Path) -> None:
    far = [[20.0, 20.0], [21.0, 20.0], [21.0, 21.0], [20.0, 20.0]]
    path = _table(tmp_path, {"schema": coverage.SCHEMA, "polygons": [[SQUARE], [far]]})
    assert coverage.in_coverage(path, 20.9, 20.5)
    assert coverage.in_coverage(path, 1.0, 1.0)
    assert not coverage.in_coverage(path, 15.0, 15.0)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"schema": "x", "polygons": [[SQUARE]]}, "t.json: schema must be onward_generator.source_coverage"),
        ({"polygons": [[SQUARE]]}, "t.json: schema must be onward_generator.source_coverage"),
        ({"schema": coverage.SCHEMA}, "t.json: polygons must be a non-empty list of rings"),
        ({"schema": coverage.SCHEMA, "polygons": []}, "t.json: polygons must be a non-empty list of rings"),
        ({"schema": coverage.SCHEMA, "polygons": {"a": 1}}, "t.json: polygons must be a non-empty list of rings"),
    ],
)
def test_a_table_of_another_shape_is_refused(tmp_path: Path, document: object, message: str) -> None:
    with pytest.raises(SourceError) as caught:
        coverage.load_coverage(_table(tmp_path, document))
    assert str(caught.value) == message


def test_the_config_dir_is_the_repository_config() -> None:
    assert coverage.CONFIG_DIR == Path(__file__).resolve().parents[2] / "config"
    assert (coverage.CONFIG_DIR / "sources.json").is_file()


def test_coverage_never_touches_the_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("coverage must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr("onward_generator.net.fetch_bytes", refuse)
    path = _table(tmp_path, {"schema": coverage.SCHEMA, "polygons": [[SQUARE]]})
    assert coverage.in_coverage(path, MURCIA_CENTER_LON % 10, MURCIA_CENTER_LAT % 10)
