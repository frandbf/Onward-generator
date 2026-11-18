"""map.json parapet_m: every roof parapet at the map's height (18 November 2026)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.detail.config import PARAPET_MAX_M, PARAPET_MIN_M, load_detail_config, with_parapet
from onward_generator.maps.workspace import MapError, create_map, parapet_height


def _map(tmp_path: Path, value: object | None) -> Path:
    root = tmp_path / "m"
    create_map(root, map_id="m", name="M", latitude=37.98407, longitude=-1.12863, radius_m=150)
    if value is not None:
        document = json.loads((root / "map.json").read_text(encoding="utf-8"))
        document["parapet_m"] = value
        (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def test_the_bounds() -> None:
    assert (PARAPET_MIN_M, PARAPET_MAX_M) == (0.5, 1.5)


@pytest.mark.parametrize(("value", "expected"), [(0.5, 0.5), (1, 1.0), (1.5, 1.5)])
def test_a_height_is_read(tmp_path: Path, value: float, expected: float) -> None:
    height = parapet_height(_map(tmp_path, value))
    assert height == expected and isinstance(height, float)


def test_no_key_keeps_the_templates(tmp_path: Path) -> None:
    assert parapet_height(_map(tmp_path, None)) is None


@pytest.mark.parametrize("value", [0.499, 1.501, True, "0.5", [0.5]])
def test_a_bad_height_is_refused(tmp_path: Path, value: object) -> None:
    with pytest.raises(MapError, match=rf'^map.json "parapet_m" must be a number from 0.5 to 1.5, got {value!r}$'.replace("[", r"\[")):
        parapet_height(_map(tmp_path, value))


def test_every_template_with_a_parapet_takes_the_height_and_the_others_keep_none() -> None:
    cfg = load_detail_config()
    assert {t.parapet_m for t in cfg.templates.values()} == {0.0, 0.7}
    low = with_parapet(cfg, 0.5)
    for name, template in cfg.templates.items():
        assert low.templates[name].parapet_m == (0.5 if template.parapet_m > 0 else 0.0), name
        assert low.templates[name].parapet_thickness_m == template.parapet_thickness_m
    assert with_parapet(cfg, PARAPET_MIN_M).templates.keys() == cfg.templates.keys()
    assert {t.parapet_m for t in with_parapet(cfg, PARAPET_MAX_M).templates.values()} == {0.0, 1.5}
    assert low.landmark_template == cfg.landmark_template and cfg.templates != low.templates  # a copy: cfg is not changed


@pytest.mark.parametrize("height", [0.499, 1.501])
def test_a_height_out_of_bounds_is_refused(height: float) -> None:
    with pytest.raises(ValueError, match=rf"^parapet height must be from 0.5 to 1.5 m, got {height}$"):
        with_parapet(load_detail_config(), height)
