"""Placement plan: any geo scene in local metres, never a city-specific list."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

from onward_generator.ai.providers import ProviderError, ProviderResult, StaticProvider, context_window
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb
from onward_generator.generation.mesh import box
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.digest import DigestLimits, build_digest
from onward_generator.overgrow.fit import check_scene, write_report
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.place import overgrow
from onward_generator.overgrow.plan import bind_plan, parse_overgrow_plan, plan_schema, run_schema
from onward_generator.overgrow.prompt import SYSTEM_PROMPT, _row_tokens, library_rows, prompt_props
from onward_generator.overgrow.realize import Site, compose, fitted_props, realize
from onward_generator.overgrow.zone import default_block_zone
from tests.conftest import MURCIA_CENTER

FIXTURE = Path(__file__).parents[1] / "fixtures" / "overgrow" / "ambient_replay.json"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16


def _feature(kind: str, geometry: dict, tags: dict, feature_id: str) -> dict:
    return {
        "id": feature_id,
        "kind": kind,
        "tags": tags,
        "geometry": {**geometry, "unit": "m", "coordinate_system": "local_grid"},
    }


def _block_features() -> list[dict]:
    return [
        _feature(
            "highway",
            {"type": "LineString", "coordinates": [[-40, 0], [80, 0]]},
            {"highway": "residential"},
            "osm-way-street",
        ),
        _feature(
            "building",
            {"type": "Polygon", "coordinates": [[[0, 16], [10, 16], [10, 28], [0, 28], [0, 16]]]},
            {"building": "yes", "height": "12"},
            "osm-way-left",
        ),
        _feature(
            "building",
            {"type": "Polygon", "coordinates": [[[18, 16], [28, 16], [28, 28], [18, 28], [18, 16]]]},
            {"building": "yes", "height": "12"},
            "osm-way-right",
        ),
        _feature("poi", {"type": "Point", "coordinates": [5, 34]}, {"amenity": "bank"}, "osm-node-place"),
        _feature(
            "building",
            {"type": "Polygon", "coordinates": [[[40, 16], [46, 16], [46, 20], [40, 20], [40, 16]]]},
            {"building": "yes", "height": "1.5"},
            "osm-way-low",
        ),
    ]


def _triangle_features() -> list[dict]:
    return [
        _feature(
            "highway",
            {"type": "LineString", "coordinates": [[0, -8], [30, 6]]},
            {"highway": "residential"},
            "osm-way-diagonal",
        ),
        _feature(
            "building",
            {"type": "Polygon", "coordinates": [[[2, 2], [14, 2], [8, 12], [2, 2]]]},
            {"building": "yes", "building:levels": "3"},
            "osm-way-triangle",
        ),
        _feature(
            "area",
            {"type": "Polygon", "coordinates": [[[4, -6], [12, -6], [12, -2], [4, -2], [4, -6]]]},
            {"leisure": "park"},
            "osm-way-park",
        ),
    ]


def _scene(features: list[dict], *, longitude: float, latitude: float, crs: str) -> dict:
    return {
        "origin": {"longitude": longitude, "latitude": latitude},
        "frame": {"crs": crs},
        "features": features,
    }


def _prop(asset_id: str, mesh) -> Prop:
    spec = ASSETS_BY_ID[asset_id]
    uvs = np.zeros((mesh.vertex_count, 2), dtype=np.float32)
    return Prop(spec, mesh, uvs, PNG)


def _props() -> list[Prop]:
    return [
        _prop("large_iron_gate", box(-1.0, 1.0, -0.1, 0.1, 0.0, 1.2)),
        _prop("modular_fire_escape", box(-1.0, 1.0, -1.0, 1.0, 0.0, 3.0)),
        _prop("security_camera_02", box(-0.1, 0.1, -0.1, 0.1, 0.0, 0.2)),
        _prop("concrete_road_barrier", box(-0.6, 0.6, -0.2, 0.2, 0.0, 0.8)),
    ]


def _site(features: list[dict]) -> Site:
    from shapely import STRtree
    from shapely.geometry import shape
    from shapely.geometry.polygon import orient

    from onward_generator.overgrow.fit import SKIP_HIGHWAYS

    polygons = []
    lanes = []
    for feature in features:
        geom = shape({"type": feature["geometry"]["type"], "coordinates": feature["geometry"]["coordinates"]})
        if feature["kind"] == "building" and geom.geom_type == "Polygon":
            polygons.append(orient(geom, sign=1.0))
        highway = str(feature["tags"].get("highway") or "")
        if feature["kind"] == "highway" and highway not in SKIP_HIGHWAYS and geom.length >= 8:
            lanes.append(geom)
    buildings = STRtree(polygons or [])
    return Site(default_block_zone(), buildings, lanes, STRtree(lanes) if lanes else None, [])


def test_parser_rejects_a_plan_the_code_cannot_apply() -> None:
    try:
        parse_overgrow_plan('{"plan_version":"0.1.0","mood":"quiet","actions":[],"metres":4}')
    except ValueError as exc:
        assert "unknown field" in str(exc)
    else:
        raise AssertionError("extra field was accepted")
    wide = (
        '{"plan_version":"0.1.0","mood":"quiet","actions":[{"op":"span","asset_id":"fence_01",'
        '"from_id":"corner:a","to_id":"corner:b","passage_m":0.4}]}'
    )
    try:
        parse_overgrow_plan(wide)
    except ValueError as exc:
        assert "passage_m" in str(exc)
    else:
        raise AssertionError("passage_m below 1.2 was accepted")
    actions = ",".join(
        '{"op":"place","asset_id":"barrier_01","anchor_id":"lane:a","crosswise":false}' for _ in range(25)
    )
    try:
        parse_overgrow_plan('{"plan_version":"0.1.0","mood":"quiet","actions":[' + actions + "]}")
    except ValueError as exc:
        assert "range" in str(exc) or "maxItems" in str(exc) or "24" in str(exc)
    else:
        raise AssertionError("25 actions were accepted")
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"quiet","actions":[{"op":"span","asset_id":"yard_gate",'
        '"from_id":"corner:a","to_id":"corner:missing","passage_m":1.2}]}'
    )
    assert plan.actions[0].asset_id == "yard_gate"
    try:
        bind_plan(
            plan,
            asset_ids={"fence_01"},
            anchor_ids={"corner": {"corner:a"}, "facade": set(), "poi": set(), "lane": set()},
        )
    except ValueError as exc:
        text = str(exc)
        assert "yard_gate" in text and "corner:missing" in text
    else:
        raise AssertionError("unknown ids were accepted")


def test_digest_ignores_the_map_centre_and_reads_any_shape() -> None:
    block = _block_features()
    here = build_digest(_scene(block, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    elsewhere = build_digest(_scene(block, longitude=2.3522, latitude=48.8566, crs="EPSG:32631"))
    assert here.to_dict() == elsewhere.to_dict()
    assert here.sha256() == elsewhere.sha256()
    dumped = json.dumps(here.to_dict()).lower()
    assert "murcia" not in dumped and "25830" not in dumped
    other = build_digest(_scene(
        _triangle_features(), longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830",
    ))
    assert {item.id for item in here.facades} != {item.id for item in other.facades}
    assert any(item.tags == (("leisure", "park"),) for item in other.places)
    assert any(item.height_origin == "observed" and item.height_m == 9.0 for item in other.facades)
    assert all("osm-way-low" not in item.id for item in here.facades)
    assert any(item.id == "poi:osm-node-place" and item.tags == (("amenity", "bank"),) for item in here.places)
    capped = build_digest(
        _scene(block + _triangle_features(), longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"),
        limits=DigestLimits(facades=1, corners=1, gaps=1, streets=1, places=1, anchors=1),
    )
    assert len(capped.facades) == 1
    assert "Murcia" not in SYSTEM_PROMPT and "EPSG" not in SYSTEM_PROMPT


def test_span_repeats_a_fence_and_leaves_the_passage() -> None:
    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"gap","actions":[{"op":"span","asset_id":"large_iron_gate",'
        '"from_id":"corner:osm-way-left:0:1","to_id":"corner:osm-way-right:0:0","passage_m":1.2}]}'
    )
    dressing = realize(plan, digest, _props(), _site(features))
    copies = [row for row in dressing.records if row["op"] == "span"]
    assert len(copies) == 3
    far = max(row["position_m"][0] for row in copies) + 1.0
    assert far <= 18.0 - 1.2
    assert all(10.0 <= row["position_m"][0] <= 18.0 for row in copies)
    long_fence = [_prop("large_iron_gate", box(-3.5, 3.5, -0.1, 0.1, 0.0, 1.2))]
    dropped = realize(plan, digest, long_fence, _site(features))
    assert dropped.nodes == []
    assert dropped.rejects[0]["reason"] == "too_short"


def test_cover_stops_at_the_roof() -> None:
    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"wall","actions":[{"op":"cover","asset_id":"modular_fire_escape",'
        '"edge_id":"facade:osm-way-left:0:0"}]}'
    )
    dressing = realize(plan, digest, _props(), _site(features))
    rows = [row for row in dressing.records if row["op"] == "cover"]
    assert rows
    tops = [row["position_m"][2] + row["size_m"][2] for row in rows]
    assert max(tops) <= 12.0 + 1e-6
    assert min(row["position_m"][1] for row in rows) < 16.0
    assert all(row["fit_scale"] < 1.0 for row in rows)


def test_watch_uses_the_facade_that_faces_the_point() -> None:
    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"watch","actions":[{"op":"watch","asset_id":"security_camera_02",'
        '"poi_id":"poi:osm-node-place","count":2}]}'
    )
    dressing = realize(plan, digest, _props(), _site(features))
    cameras = [row for row in dressing.records if row["op"] == "watch"]
    assert len(cameras) == 2
    assert all(row["position_m"][1] > 28.0 for row in cameras)
    assert all(0.0 <= row["position_m"][0] <= 10.0 for row in cameras)
    assert all(abs(row["position_m"][2] - 3.1) < 1e-6 for row in cameras)


def test_place_turns_a_street_barrier_only_when_asked() -> None:
    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    site = _site(features)
    straight = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"curb","actions":[{"op":"place","asset_id":"concrete_road_barrier",'
        '"anchor_id":"lane:street:osm-way-street:0:p:0","crosswise":false}]}'
    )
    turned = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"curb","actions":[{"op":"place","asset_id":"concrete_road_barrier",'
        '"anchor_id":"lane:street:osm-way-street:0:p:0","crosswise":true}]}'
    )
    props = _props()
    flat = realize(straight, digest, props, site)
    cross = realize(turned, digest, props, site)
    assert flat.records[0]["crosswise"] is False
    assert cross.records[0]["crosswise"] is True
    assert abs(flat.nodes[0].matrix[0, 0] - 1.0) < 1e-6
    assert abs(cross.nodes[0].matrix[0, 0]) < 1e-6
    assert abs(cross.nodes[0].matrix[1, 0] - 1.0) < 1e-6


def _base(path: Path) -> None:
    path.mkdir()
    ground = box(0.0, 4.0, 0.0, 4.0, 0.0, 0.1)
    (path / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),), {"type": "surface"})],
        {"ground": Material((0.4, 0.4, 0.4, 1), 1)},
    ))
    (path / "scene_collision.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "collider"),), {"type": "collision"})],
        {"collider": Material((0.4, 0.4, 0.4, 1), 1)},
    ))
    (path / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-left", "type": "building", "source_ids": ["osm-way-left"]}],
    }), encoding="utf-8")


def _library(path: Path) -> None:
    path.mkdir()
    assets = []
    for prop in _props():
        file_name = f"{prop.spec.id}.glb"
        (path / file_name).write_bytes(build_glb(
            [Node(prop.spec.id, (Primitive(prop.mesh, "albedo", prop.uvs),), {})],
            {"albedo": Material((1, 1, 1, 1), 1)},
            repeat_images={"albedo": PNG},
        ))
        assets.append({"id": prop.spec.id, "file": file_name})
    (path / "manifest.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")


class _Down:
    name = "down"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, instruction: str, schema: dict) -> object:
        self.calls += 1
        raise ProviderError("Ollama is not running")


def test_replay_places_the_plan_and_fit_accepts_it(tmp_path: Path) -> None:
    scene_path = tmp_path / "geo_scene.json"
    scene_path.write_text(json.dumps(_scene(
        _block_features(), longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830",
    )), encoding="utf-8")
    base = tmp_path / "base"
    _base(base)
    library = tmp_path / "library"
    _library(library)
    out = tmp_path / "scene_overgrow"
    provider = StaticProvider("replay", FIXTURE.read_text(encoding="utf-8"), "fixture")
    report = overgrow(
        base, scene_path, out, library=library, brief="A quiet block with a watched point",
        ai=True, provider=provider,
    )
    assert report["ambient_mode"] == "ai"
    assert report["ambient_origin"] == "ai_generated"
    assert report["wrecks"] == 0
    assert report["compose_actions"] == 4
    assert report["moss"] == 0
    checked, problems = check_scene(out, scene_path)
    assert problems == []
    assert checked >= 4
    write_report(out / "fit.json", checked, "0" * 64)
    nodes, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    assert any(node.name.startswith("overgrow/prop/large_iron_gate/") for node in nodes)
    stored = json.loads((out / "ambient_plan.json").read_text(encoding="utf-8"))
    assert stored["origin"] == "ai_generated"
    fresh = tmp_path / "scene_rules"
    down = _Down()
    fallback = overgrow(
        base, scene_path, fresh, library=library, brief="A quiet block with a watched point",
        ai=True, provider=down,
    )
    assert down.calls == 2
    assert fallback["ambient_mode"] == "rules"
    assert fallback["compose_attempts"] == 2
    assert fallback["compose_fallback"]
    assert fallback["wrecks"] >= 1
    reused_provider = _Down()
    reused = overgrow(
        base, scene_path, out, library=library, brief="A quiet block with a watched point",
        ai=True, provider=reused_provider, overwrite=True,
    )
    assert reused_provider.calls == 0
    assert reused["ambient_origin"] == "replay"
    assert reused["wrecks"] == 0
    assert reused["compose_actions"] == 4


def test_a_failed_reply_is_tried_once_more_before_the_rules() -> None:
    features = _block_features()
    scene = _scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830")
    site = _site(features)
    props = _props()
    raw = FIXTURE.read_text(encoding="utf-8")

    class _Flaky:
        name = "flaky"

        def __init__(self, first: object) -> None:
            self.first = first
            self.calls = 0
            self.second = ""

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            self.calls += 1
            if self.calls == 1:
                if isinstance(self.first, BaseException):
                    raise self.first
                return ProviderResult(str(self.first), {})
            self.second = instruction
            return ProviderResult(raw, {})

    timed_out = _Flaky(ProviderError("timed out"))
    recovered = compose(scene, props, site, brief="A quiet block", provider=timed_out)
    assert timed_out.calls == 2
    assert recovered.ok is True
    assert recovered.origin == "ai_generated"
    assert recovered.record["attempts"] == 2
    assert "timed out" in timed_out.second

    invalid = _Flaky("{not json")
    fixed = compose(scene, props, site, brief="A quiet block", provider=invalid)
    assert invalid.calls == 2
    assert fixed.ok is True
    assert "not json" in invalid.second or "JSON" in invalid.second

    class _Down:
        name = "down"
        calls = 0

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            self.calls += 1
            raise ProviderError("Ollama is not running")

    down = _Down()
    fallback = compose(scene, props, site, brief="A quiet block", provider=down)
    assert down.calls == 2
    assert fallback.ok is False
    assert fallback.origin == "rules_fallback"
    assert fallback.record["attempts"] == 2


def _grouped(props: list[Prop], asset_id: str, grouping: str) -> list[Prop]:
    return [
        Prop(replace(prop.spec, grouping=grouping), prop.mesh, prop.uvs, prop.albedo) if prop.spec.id == asset_id else prop
        for prop in props
    ]


def test_span_needs_a_run_when_the_grouping_is_curated() -> None:
    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"gap","actions":[{"op":"span","asset_id":"large_iron_gate",'
        '"from_id":"corner:osm-way-left:0:1","to_id":"corner:osm-way-right:0:0","passage_m":1.2}]}'
    )
    for grouping in ("single", "cluster", "grid"):
        refused = realize(plan, digest, _grouped(_props(), "large_iron_gate", grouping), _site(features))
        assert refused.nodes == []
        assert refused.rejects == [{"op_index": 0, "asset_id": "large_iron_gate", "reason": "wrong_grouping"}]
    placed = realize(plan, digest, _grouped(_props(), "large_iron_gate", "run"), _site(features))
    assert len([row for row in placed.records if row["op"] == "span"]) == 3


def test_cover_needs_a_grid_when_the_grouping_is_curated() -> None:
    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"wall","actions":[{"op":"cover","asset_id":"modular_fire_escape",'
        '"edge_id":"facade:osm-way-left:0:0"}]}'
    )
    for grouping in ("single", "cluster", "run"):
        refused = realize(plan, digest, _grouped(_props(), "modular_fire_escape", grouping), _site(features))
        assert refused.nodes == []
        assert refused.rejects == [{"op_index": 0, "asset_id": "modular_fire_escape", "reason": "wrong_grouping"}]
    placed = realize(plan, digest, _grouped(_props(), "modular_fire_escape", "grid"), _site(features))
    assert [row for row in placed.records if row["op"] == "cover"]


# --- library budget (the prompt must fit whatever catalogues are installed) ----------------------------------

def _many(count: int, *, roles: tuple[str, ...] = ("barrier", "bench", "vehicle"), words: int = 40) -> list[Prop]:
    base = _props()[3]
    filler = " ".join(["worn"] * words)
    return [
        Prop(replace(base.spec, id=f"lib_{i:03d}", role=roles[i % len(roles)], grouping="single", tags=(),
                     description=f"Generic object {i}. {filler}"), base.mesh, base.uvs, base.albedo)
        for i in range(count)
    ]


def test_a_library_that_fits_is_offered_whole() -> None:
    props = _props()
    assert prompt_props(props, "A quiet block") == props
    rows = sum(_row_tokens(row) for row in library_rows(props))
    assert prompt_props(props, "", budget=rows) == props
    assert prompt_props(props, "", budget=rows - 1) != props


def test_a_large_library_is_cut_to_the_budget_one_role_at_a_time() -> None:
    props = _many(60)
    rows = {str(row["id"]): _row_tokens(row) for row in library_rows(props)}
    budget = sum(sorted(rows.values())[:7])
    chosen = prompt_props(props, "", budget=budget)
    assert 0 < len(chosen) < len(props)
    assert sum(rows[p.spec.id] for p in chosen) <= budget
    # Round robin over the roles: no role has two more than another, every role is offered.
    counts = Counter(p.spec.role for p in chosen)
    assert set(counts) == {"barrier", "bench", "vehicle"} and max(counts.values()) - min(counts.values()) <= 1
    # Same inputs, same subset, in the library's own order.
    assert prompt_props(props, "", budget=budget) == chosen
    assert [p.spec.id for p in chosen] == sorted(p.spec.id for p in chosen)
    # A row that does not fit ends the subset: nothing is offered with a budget below the first row.
    assert prompt_props(props, "", budget=min(rows.values()) - 1) == []


def test_runs_grids_and_brief_words_come_first_in_their_role() -> None:
    props = _many(30, roles=("barrier",))
    run = Prop(replace(props[5].spec, grouping="run"), props[5].mesh, props[5].uvs, props[5].albedo)
    grid = Prop(replace(props[6].spec, grouping="grid"), props[6].mesh, props[6].uvs, props[6].albedo)
    # The prop that comes last by the hash of its id: only the brief word can bring it forward.
    last = max(range(30), key=lambda i: "" if i in (5, 6) else hashlib.sha256(f"lib_{i:03d}".encode()).hexdigest())
    tagged = Prop(replace(props[last].spec, id="lib_007x", tags=("sandbags",)), props[last].mesh, props[last].uvs,
                  props[last].albedo)
    props = [tagged if i == last else run if i == 5 else grid if i == 6 else prop for i, prop in enumerate(props)]
    cost = {str(row["id"]): _row_tokens(row) for row in library_rows(props)}
    two = cost["lib_005"] + cost["lib_006"]
    first_two = prompt_props(props, "", budget=two)
    assert {p.spec.id for p in first_two} == {"lib_005", "lib_006"}
    first_three = prompt_props(props, "Stacked SANDBAGS and wire", budget=two + cost["lib_007x"])
    assert {p.spec.id for p in first_three} == {"lib_005", "lib_006", "lib_007x"}
    # Without the brief word the third is another one, chosen by the hash of its id.
    assert "lib_007x" not in {p.spec.id for p in prompt_props(props, "short", budget=two + cost["lib_007x"])}


def test_compose_offers_the_budgeted_library_and_records_it() -> None:
    features = _block_features()
    scene = _scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830")
    seen: list[str] = []

    class _Recorder:
        name = "recorder"

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            seen.append(instruction)
            context_window(system, instruction)
            raise ProviderError("no model")

    props = [*_props(), *_many(400, words=60)]
    result = compose(scene, props, _site(features), brief="A quiet block", provider=_Recorder())
    assert result.origin == "rules_fallback" and len(seen) == 2
    fitted = len(fitted_props(props))
    library = result.record["library"]
    assert library["fitted"] == fitted and 0 < library["offered"] < fitted
    assert result.reason == "no model"
    # Every offered id is in the prompt and nothing else of the library is.
    offered = {p.spec.id for p in prompt_props(fitted_props(props), "A quiet block")}
    assert len(offered) == library["offered"]
    assert all(f'"id": "{asset_id}"' in seen[0] for asset_id in offered)
    assert not any(f'"id": "lib_{i:03d}"' in seen[0] for i in range(400) if f"lib_{i:03d}" not in offered)
    small = compose(scene, _props(), _site(features), brief="A quiet block", provider=_Recorder())
    assert small.record["library"] == {"offered": len(fitted_props(_props())), "fitted": len(fitted_props(_props()))}


def test_the_run_schema_enumerates_this_runs_ids() -> None:
    anchors = {"corner": {"corner:b", "corner:a"}, "facade": {"facade:e"}, "poi": {"poi:p"}, "lane": {"lane:l"}}
    groupings = {"fence": "run", "escape": "grid", "camera": "single", "old": ""}
    schema = run_schema(anchors, groupings)
    Draft202012Validator.check_schema(schema)
    branches = {b["properties"]["op"]["const"]: b["properties"] for b in schema["properties"]["actions"]["items"]["oneOf"]}
    assert set(branches) == {"span", "cover", "watch", "place"}
    assert branches["span"]["asset_id"] == {"enum": ["fence", "old"]}
    assert branches["span"]["from_id"] == branches["span"]["to_id"] == {"enum": ["corner:a", "corner:b"]}
    assert branches["cover"]["asset_id"] == {"enum": ["escape", "old"]}
    assert branches["cover"]["edge_id"] == {"enum": ["facade:e"]}
    assert branches["watch"]["asset_id"] == {"enum": ["camera", "escape", "fence", "old"]}
    assert branches["watch"]["poi_id"] == {"enum": ["poi:p"]}
    assert branches["place"]["anchor_id"] == {"enum": ["lane:l"]}
    assert branches["place"]["crosswise"] == plan_schema()["properties"]["actions"]["items"]["oneOf"][3]["properties"]["crosswise"]
    # A gesture with nothing to name is left out; with nothing at all the plan may only be empty.
    some = run_schema({**anchors, "corner": set(), "poi": set()}, {"camera": "single"})
    ops = [b["properties"]["op"]["const"] for b in some["properties"]["actions"]["items"]["oneOf"]]
    assert ops == ["place"]
    none = run_schema({"corner": set(), "facade": set(), "poi": set(), "lane": set()}, groupings)
    assert none["properties"]["actions"] == {"type": "array", "maxItems": 0}
    assert run_schema(anchors, {})["properties"]["actions"] == {"type": "array", "maxItems": 0}
    # The plain schema is untouched.
    assert "enum" not in json.dumps(plan_schema())


def test_compose_sends_the_run_schema() -> None:
    features = _block_features()
    scene = _scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830")
    schemas: list[dict] = []

    class _Recorder:
        name = "recorder"

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            schemas.append(schema)
            raise ProviderError("no model")

    props = _props()
    compose(scene, props, _site(features), brief="A quiet block", provider=_Recorder())
    digest = build_digest(scene)
    expected = run_schema(digest.anchor_ids(), {p.spec.id: p.spec.grouping for p in fitted_props(props)})
    assert schemas == [expected, expected]
