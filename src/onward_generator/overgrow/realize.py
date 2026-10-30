"""Turn a validated placement plan into copies. The model does not place meshes.

A span or a cover that cannot be finished is dropped whole. When the manifest
curates a grouping, a span needs a ``run`` and a cover needs a ``grid``. Copies still have
to leave 1.2 m of passage, stay out of buildings, and fit the triangle budget.
The library GLB is not rewritten.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString

from onward_generator.detail.modules import frame_matrix, yaw_matrix
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import transform
from onward_generator.overgrow.ambient import is_solid
from onward_generator.overgrow.catalog import FACADE_Z_M
from onward_generator.overgrow.constants import MAX_LIBRARY_TRIS
from onward_generator.overgrow.digest import Digest, Facade, build_digest
from onward_generator.overgrow.dressing import Dressing, block_reason, collider_mesh
from onward_generator.overgrow.fit import (
    FIRE_ESCAPE_DEPTH_M,
    apply_fit_scale,
    measure,
    object_reason,
    size_reason,
)
from onward_generator.overgrow.ground import RoadFloor
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.plan import (
    PLAN_VERSION,
    CoverAction,
    OvergrowPlan,
    PlaceAction,
    PlanError,
    SpanAction,
    WatchAction,
    bind_plan,
    parse_overgrow_plan,
)
from onward_generator.overgrow.prompt import PROMPT_VERSION, SYSTEM_PROMPT, instruction, instruction_sha256
from onward_generator.overgrow.zone import Zone

_CROSS_ROLES = frozenset({"wreck", "gate", "barrier"})
_RECORD_SCHEMA = "onward_generator.overgrow_ambient_plan"
PLAN_ATTEMPTS = 2


@dataclass
class Site:
    zone: Zone
    buildings: STRtree
    lanes: list[LineString]
    lane_tree: STRtree | None
    spawns: list[tuple[float, float]]
    budget: int = MAX_LIBRARY_TRIS
    floor: RoadFloor = field(default_factory=RoadFloor.empty)


@dataclass
class ComposeResult:
    ok: bool
    origin: str
    dressing: Dressing
    applied: int
    record: dict
    reason: str | None = None


def fitted_props(props: list[Prop]) -> list[Prop]:
    """Copies at street size. A mesh that still fails its size band is left out."""
    ready: list[Prop] = []
    for prop in props:
        mesh, scale = _street_mesh(prop)
        length_m, width_m, height_m = measure(mesh)
        if size_reason(prop.spec.role, length_m, width_m, height_m) is not None:
            continue
        if scale == 1.0:
            ready.append(prop)
        else:
            ready.append(replace(prop, mesh=mesh, fit_scale=scale))
    return ready


def _street_mesh(prop: Prop):
    """Uniform scale. A fire escape is pulled just inside the 1.6 m depth so fit accepts it."""
    length_m, width_m, height_m = measure(prop.mesh)
    if prop.spec.role == "fire_escape" and width_m > FIRE_ESCAPE_DEPTH_M:
        scale = (FIRE_ESCAPE_DEPTH_M - 1e-3) / width_m
        return transform(prop.mesh, np.diag([scale, scale, scale, 1.0])), scale
    return apply_fit_scale(prop.mesh, prop.spec.role)


def realize(plan: OvergrowPlan, digest: Digest, props: list[Prop], site: Site) -> Dressing:
    props = fitted_props(props)
    by_id = {prop.spec.id: prop for prop in props}
    result = Dressing()
    for index, action in enumerate(plan.actions):
        prop = by_id.get(action.asset_id)
        if prop is None:
            _drop(result, index, action.asset_id, "unknown_asset")
            continue
        if isinstance(action, SpanAction):
            reason = _span(action, digest, prop, site, result, index)
        elif isinstance(action, CoverAction):
            reason = _cover(action, digest, prop, site, result, index)
        elif isinstance(action, WatchAction):
            reason = _watch(action, digest, prop, site, result, index)
        else:
            reason = _place(action, digest, prop, site, result, index)
        if reason:
            _drop(result, index, action.asset_id, reason)
    return result


def compose(
    scene: Mapping[str, object],
    props: list[Prop],
    site: Site,
    *,
    brief: str,
    provider=None,
    replay: Mapping[str, object] | None = None,
) -> ComposeResult:
    """Ask for a plan, or reuse a stored reply with the same digest and prompt.

    A provider error or a plan that does not validate is tried once more. The
    second attempt quotes that failure. Rules are used only after both fail.
    """
    usable = fitted_props(props)
    digest = build_digest(scene)
    digest_hash = digest.sha256()
    prompt_hash = instruction_sha256(brief, digest, usable)
    asset_ids = {prop.spec.id for prop in usable}
    anchors = digest.anchor_ids()
    stored = _stored_reply(replay, digest_hash, prompt_hash)
    if stored is not None:
        try:
            plan = parse_overgrow_plan(stored)
            bind_plan(plan, asset_ids=asset_ids, anchor_ids=anchors)
        except PlanError:
            stored = None
        else:
            return _accepted(digest_hash, prompt_hash, "replay", stored, plan, digest, usable, site, attempts=0)
    last_error = ""
    last_text = ""
    for attempt in range(1, PLAN_ATTEMPTS + 1):
        text, provider_error = _ask(brief, digest, usable, provider, correction=last_error or None)
        last_text = text
        if provider_error:
            last_error = provider_error
            continue
        try:
            plan = parse_overgrow_plan(text)
            bind_plan(plan, asset_ids=asset_ids, anchor_ids=anchors)
        except PlanError as exc:
            last_error = str(exc)
            continue
        return _accepted(digest_hash, prompt_hash, "ai_generated", text, plan, digest, usable, site, attempts=attempt)
    record = _record(digest_hash, prompt_hash, "rules_fallback", last_text, last_error, 0, [], attempts=PLAN_ATTEMPTS)
    return ComposeResult(False, "rules_fallback", Dressing(), 0, record, last_error)


def _accepted(digest_hash, prompt_hash, origin, text, plan, digest, props, site, *, attempts: int) -> ComposeResult:
    dressing = realize(plan, digest, props, site)
    applied = len({row.get("op_index") for row in dressing.records})
    record = _record(
        digest_hash, prompt_hash, origin, text, None, applied,
        [dict(item) for item in dressing.rejects], attempts=attempts,
    )
    record["mood"] = plan.mood
    return ComposeResult(True, origin, dressing, applied, record, None)


def _stored_reply(replay: Mapping[str, object] | None, digest_hash: str, prompt_hash: str) -> str | None:
    if not isinstance(replay, Mapping):
        return None
    if replay.get("digest_sha256") != digest_hash or replay.get("instruction_sha256") != prompt_hash:
        return None
    if replay.get("origin") not in {"ai_generated", "replay"}:
        return None
    raw = replay.get("raw")
    return raw if isinstance(raw, str) and raw else None


def _ask(brief: str, digest: Digest, props: list[Prop], provider, *, correction: str | None) -> tuple[str, str | None]:
    message = instruction(brief, digest, props)
    if correction:
        message += (
            "\nThe previous reply was rejected:\n"
            f"{correction}\n"
            "Reply again with one JSON object. Use only ids from the digest and the library above.\n"
        )
    try:
        if provider is None:
            from onward_generator.ai.providers import OllamaProvider

            provider = OllamaProvider()
        result = provider.complete(SYSTEM_PROMPT, message, _schema())
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return "", str(exc)
    return result.text, None


def _schema() -> dict:
    from onward_generator.overgrow.plan import plan_schema

    return plan_schema()


def _record(digest_hash: str, prompt_hash: str, origin: str, raw: str, reason: str | None, applied: int, dropped: list, *, attempts: int) -> dict:
    return {
        "schema": _RECORD_SCHEMA,
        "plan_version": PLAN_VERSION,
        "prompt_version": PROMPT_VERSION,
        "digest_sha256": digest_hash,
        "instruction_sha256": prompt_hash,
        "origin": origin,
        "raw": raw,
        "reason": reason,
        "applied": applied,
        "dropped": dropped,
        "attempts": attempts,
    }


def _span(action: SpanAction, digest: Digest, prop: Prop, site: Site, result: Dressing, op_index: int) -> str | None:
    if prop.spec.anchor == "facade":
        return "wrong_anchor"
    if not _repeats_as(prop, "run"):
        return "wrong_grouping"
    start = digest.corner(action.from_id)
    end = digest.corner(action.to_id)
    if start is None or end is None:
        return "unknown_anchor"
    if start.id == end.id:
        return "zero_length"
    direction = np.array([end.x - start.x, end.y - start.y], dtype=np.float64)
    length = float(np.linalg.norm(direction))
    piece = prop.length_m
    if piece <= 0.05 or length <= action.passage_m:
        return "too_short"
    count = int((length - action.passage_m) // piece)
    if count < 1:
        return "too_short"
    if result.triangles + prop.triangles * count > site.budget:
        return "budget"
    direction /= length
    perpendicular = np.array([-direction[1], direction[0]])
    shift = prop.half_y + 0.05
    yaw = float(np.arctan2(direction[1], direction[0]))
    for sign in (1.0, -1.0, 0.0):
        offset = perpendicular * (0.0 if sign == 0.0 else sign * shift)
        centers = [
            np.array([start.x, start.y]) + direction * piece * (index + 0.5) + offset
            for index in range(count)
        ]
        matrices = [
            yaw_matrix(float(center[0]), float(center[1]), site.floor.z(float(center[0]), float(center[1])), yaw)
            for center in centers
        ]
        if all(_accepts(prop, matrix, site, allow_lane=False) is None for matrix in matrices):
            for matrix in matrices:
                _emit(prop, matrix, result, op="span", anchor_id=f"{start.id}->{end.id}", crosswise=False, op_index=op_index)
            return None
    return "does_not_fit"


def _cover(action: CoverAction, digest: Digest, prop: Prop, site: Site, result: Dressing, op_index: int) -> str | None:
    if prop.spec.anchor != "facade":
        return "wrong_anchor"
    if not _repeats_as(prop, "grid"):
        return "wrong_grouping"
    edge = digest.facade(action.edge_id)
    if edge is None:
        return "unknown_anchor"
    piece = prop.length_m
    storey = prop.height_m
    if piece <= 0.05 or storey <= 0.05:
        return "too_short"
    along_count = int(edge.length_m // piece)
    up_count = int(edge.height_m // storey)
    if along_count < 1:
        return "edge_too_short"
    if up_count < 1:
        return "taller_than_wall"
    if up_count * storey > edge.height_m + 1e-6:
        return "past_roof"
    total = along_count * up_count
    if result.triangles + prop.triangles * total > site.budget:
        return "budget"
    along = np.array(edge.along, dtype=np.float64)
    outward = np.array(edge.outward, dtype=np.float64)
    origin = np.array([edge.ax, edge.ay], dtype=np.float64)
    push = prop.half_y + 0.08
    matrices = []
    for row in range(up_count):
        for column in range(along_count):
            center = origin + along * piece * (column + 0.5) + outward * push
            matrices.append(frame_matrix(
                (float(center[0]), float(center[1]), row * storey),
                (-float(along[0]), -float(along[1]), 0.0),
                (float(outward[0]), float(outward[1]), 0.0),
            ))
    if any(_accepts(prop, matrix, site, allow_lane=False) is not None for matrix in matrices):
        return "does_not_fit"
    for matrix in matrices:
        _emit(prop, matrix, result, op="cover", anchor_id=edge.id, crosswise=False, op_index=op_index)
    return None


def _watch(action: WatchAction, digest: Digest, prop: Prop, site: Site, result: Dressing, op_index: int) -> str | None:
    if prop.spec.anchor != "facade":
        return "wrong_anchor"
    place = digest.place(action.poi_id)
    if place is None:
        return "unknown_anchor"
    edge = _facing(digest, place.x, place.y)
    if edge is None:
        return "no_facing_wall"
    z = float(FACADE_Z_M.get(prop.spec.role, 3.1))
    if z + prop.height_m > edge.height_m + 1e-6:
        return "taller_than_wall"
    piece = max(prop.length_m, 0.2)
    if edge.length_m + 1e-6 < piece:
        return "edge_too_short"
    if result.triangles + prop.triangles * action.count > site.budget:
        return "budget"
    along = np.array(edge.along, dtype=np.float64)
    outward = np.array(edge.outward, dtype=np.float64)
    origin = np.array([edge.ax, edge.ay], dtype=np.float64)
    push = prop.half_y + 0.08
    if action.count == 1:
        distances = [edge.length_m * 0.5]
    else:
        start = piece * 0.5
        end = edge.length_m - piece * 0.5
        step = (end - start) / (action.count - 1)
        distances = [start + index * step for index in range(action.count)]
    matrices = []
    for distance in distances:
        center = origin + along * distance + outward * push
        matrices.append(frame_matrix(
            (float(center[0]), float(center[1]), z),
            (-float(along[0]), -float(along[1]), 0.0),
            (float(outward[0]), float(outward[1]), 0.0),
        ))
    if any(_accepts(prop, matrix, site, allow_lane=False) is not None for matrix in matrices):
        return "does_not_fit"
    for matrix in matrices:
        _emit(prop, matrix, result, op="watch", anchor_id=edge.id, crosswise=False, op_index=op_index)
    return None


def _place(action: PlaceAction, digest: Digest, prop: Prop, site: Site, result: Dressing, op_index: int) -> str | None:
    if prop.spec.anchor == "facade":
        return "wrong_anchor"
    anchor = digest.anchor(action.anchor_id)
    if anchor is None:
        return "unknown_anchor"
    turn = 0.0
    crosswise = False
    if action.crosswise and anchor.on_street and prop.spec.role in _CROSS_ROLES:
        turn = float(np.pi / 2)
        crosswise = True
    if result.triangles + prop.triangles > site.budget:
        return "budget"
    matrix = yaw_matrix(anchor.x, anchor.y, site.floor.z(anchor.x, anchor.y), anchor.yaw + turn)
    reason = _accepts(prop, matrix, site, allow_lane=prop.spec.lane == "allow")
    if reason:
        return reason
    _emit(prop, matrix, result, op="place", anchor_id=anchor.id, crosswise=crosswise, op_index=op_index)
    return None


def _repeats_as(prop: Prop, grouping: str) -> bool:
    """A curated grouping must match the gesture. A row without one (older manifest) may repeat."""
    return prop.spec.grouping in ("", grouping)


def _facing(digest: Digest, x: float, y: float) -> Facade | None:
    best: Facade | None = None
    best_distance = float("inf")
    for edge in digest.facades:
        mid_x, mid_y = edge.mid
        toward = np.array([x - mid_x, y - mid_y], dtype=np.float64)
        if float(np.dot(toward, np.array(edge.outward))) <= 0.0:
            continue
        distance = float(np.dot(toward, toward))
        if distance < best_distance:
            best = edge
            best_distance = distance
    return best


def _accepts(prop: Prop, matrix: np.ndarray, site: Site, *, allow_lane: bool) -> str | None:
    x = float(matrix[0, 3])
    y = float(matrix[1, 3])
    point = block_reason(
        x, y,
        zone=site.zone, buildings=site.buildings, lane_tree=site.lane_tree, lanes=site.lanes,
        spawns=site.spawns, allow_lane=allow_lane,
    )
    if point:
        return point
    return object_reason(role=prop.spec.role, mesh=prop.mesh, matrix=matrix, buildings=site.buildings, lanes=site.lanes)


def _emit(prop: Prop, matrix: np.ndarray, result: Dressing, *, op: str, anchor_id: str, crosswise: bool, op_index: int) -> None:
    first = prop.spec.id not in set(result.placed_ids)
    result.nodes.append(Node(
        f"overgrow/prop/{prop.spec.id}/{len(result.nodes)}",
        prop.primitives() if first else (),
        {
            "type": "overgrowth",
            "origin": "ai_generated",
            "kind": prop.spec.category,
            "role": prop.spec.role,
            "asset_id": prop.spec.id,
            "op": op,
            "anchor_id": anchor_id,
            "fit_scale": round(prop.fit_scale, 4),
            "size_m": [round(prop.length_m, 3), round(prop.half_y * 2, 3), round(prop.height_m, 3)],
            "crosswise": crosswise,
            "op_index": op_index,
        },
        matrix,
        f"prop:{prop.spec.id}",
    ))
    result.images.update(prop.images())
    result.triangles += prop.triangles
    result.placed_ids.append(prop.spec.id)
    x, y, z = float(matrix[0, 3]), float(matrix[1, 3]), float(matrix[2, 3])
    solid = is_solid(prop.spec.role, prop.spec.category)
    result.records.append({
        "kind": prop.spec.category,
        "role": prop.spec.role,
        "origin": "ai_generated",
        "source_id": prop.spec.id,
        "asset_id": prop.spec.id,
        "op": op,
        "anchor_id": anchor_id,
        "position_m": [round(x, 3), round(y, 3), round(z, 3)],
        "fit_scale": round(prop.fit_scale, 4),
        "size_m": [round(prop.length_m, 3), round(prop.half_y * 2, 3), round(prop.height_m, 3)],
        "crosswise": crosswise,
        "solid": solid,
        "op_index": op_index,
    })
    if solid:
        result.colliders.append(Node(
            f"overgrow/collider/prop/{prop.spec.id}/{len(result.colliders)}",
            (Primitive(collider_mesh(prop.mesh), "collider"),),
            {"type": "collision", "origin": "ai_generated", "asset_id": prop.spec.id, "role": prop.spec.role},
            matrix,
        ))


def _drop(result: Dressing, index: int, asset_id: str, reason: str) -> None:
    result.reject_counts[reason] = result.reject_counts.get(reason, 0) + 1
    result.rejects.append({"op_index": index, "asset_id": asset_id, "reason": reason})


def load_replay(text: str) -> dict | None:
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None
