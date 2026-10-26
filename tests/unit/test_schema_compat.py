"""Belluga documents keep the pre-rename schema id and must still load."""

from onward_generator.contracts import is_schema
from onward_generator.export.package import _check_layout


def test_legacy_schema_id_is_the_same_document():
    assert is_schema("onward_generator.geo_scene", "onward_generator.geo_scene")
    assert is_schema("murcia_generator.geo_scene", "onward_generator.geo_scene")
    assert not is_schema("murcia_generator.scene3d", "onward_generator.geo_scene")


def test_belluga_gameplay_layout_is_not_rejected_for_its_old_id():
    problems = _check_layout({"schema": "murcia_generator.gameplay_layout"})
    assert not any(problem.startswith("layout: schema must be") for problem in problems)
