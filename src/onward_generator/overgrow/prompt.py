"""English instructions for the placement model. Examples use abstract ids."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from onward_generator.ai.providers import BYTES_PER_TOKEN
from onward_generator.overgrow.digest import Digest
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.plan import MAX_PER_ANCHOR, PLAN_VERSION

PROMPT_VERSION = "0.3.0"
# The library rows a prompt may carry, in the tokens ``ai.providers.context_window`` counts. With the system prompt
# (about 1 000) and the largest digest (about 10 000) the prompt stays near 25 000 of the 32 768 the provider allows,
# whatever catalogues are installed: 399 rows of the November 2026 library would need about 56 000.
LIBRARY_BUDGET_TOKENS = 14000
# A span needs a run and a cover a grid (``plan.bind_plan``): those rows go first in their role.
PATTERN_GROUPINGS = ("run", "grid")

SYSTEM_PROMPT = f"""You compose a handful of gestures for a 3D map. Reply with one JSON object and nothing else. No prose, no markdown, no code.

The user message has a brief in quotes, a digest of anchors in local metres, and the installed library. Use only ids from that digest and asset ids from that library. The same instructions apply to any city. Do not assume a country, a street name, or a coordinate.

Fields:
- plan_version: always "{PLAN_VERSION}".
- mood: one sentence.
- actions: from 0 to 24 objects. Each object is one of:
  - {{"op":"span","asset_id":"...","from_id":"...","to_id":"...","passage_m":1.2}}
    passage_m is a number from 1.2 to 2.4. Repeat a ground asset from one corner to another. The code keeps the passage clear.
  - {{"op":"cover","asset_id":"...","edge_id":"..."}}
    Repeat a facade asset along and up that wall. The code stops at the roof.
  - {{"op":"watch","asset_id":"...","poi_id":"...","count":2}}
    count is an integer from 1 to 3. Put that facade asset on the wall that faces the point.
  - {{"op":"place","asset_id":"...","anchor_id":"...","crosswise":false}}
    One copy on a lane anchor. crosswise true turns it 90 degrees only for a wreck, a gate, or a barrier that sits on a lane anchor.
    Use one lane anchor for at most {MAX_PER_ANCHOR} place actions; spread the copies over the anchors.

Library rows may carry curated hints. description says what the object is and its state. placement says where it belongs: street (may stand in the carriageway), kerb (sidewalk edge by the road), wall_foot (on the ground against a wall), wall_mounted (on a facade), boundary (along a lot or block edge), open_ground (yards, lots, plazas, parks), ground_cover (flat on the ground). grouping says how copies sit: run (end to end in a line), grid (along and up a wall), cluster (a few loose copies), single (one copy). The code refuses a span whose asset is not a run and a cover whose asset is not a grid. condition (intact, worn, damaged, wrecked, burned) and tags help match the brief.

Prefer a span, a cover, or a watch before scattering place actions. If the brief does not ask for chaos, do not span across a carriageway. Do not invent ids. Do not send matrices or metres except passage_m.

The ids in this example are illustrations. Replace them with ids from the user message. When the digest contains corner:a, corner:b, facade:e, poi:p, and lane:l, and the library contains fence_01, escape_01, camera_01, and barrier_01, a valid object is:
{{"plan_version":"{PLAN_VERSION}","mood":"A closed yard and a watched square.","actions":[{{"op":"span","asset_id":"fence_01","from_id":"corner:a","to_id":"corner:b","passage_m":1.2}},{{"op":"cover","asset_id":"escape_01","edge_id":"facade:e"}},{{"op":"watch","asset_id":"camera_01","poi_id":"poi:p","count":2}},{{"op":"place","asset_id":"barrier_01","anchor_id":"lane:l","crosswise":false}}]}}

Rejected: a span whose from_id is "12.0,4.0", an asset_id that is not in the library, or passage_m 0. That invents a coordinate or seals a street. Do not do that.
"""


def library_rows(props: list[Prop]) -> list[dict[str, object]]:
    rows = []
    for prop in props:
        rows.append({
            "id": prop.spec.id,
            "role": prop.spec.role,
            "category": prop.spec.category,
            "anchor": prop.spec.anchor,
            "surfaces": list(prop.spec.surfaces),
            "length_m": round(prop.length_m, 3),
            "width_m": round(prop.half_y * 2, 3),
            "height_m": round(prop.height_m, 3),
            **_hints(prop),
        })
    rows.sort(key=lambda row: str(row["id"]))
    return rows


def _hints(prop: Prop) -> dict[str, object]:
    """Curated fields of a 0.2.0 manifest row. An older row sends none."""
    found: dict[str, object] = {}
    for key in ("description", "placement", "grouping", "condition"):
        value = getattr(prop.spec, key)
        if value:
            found[key] = value
    if prop.spec.tags:
        found["tags"] = list(prop.spec.tags)
    return found


def _row_tokens(row: dict[str, object]) -> int:
    return len(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()) // BYTES_PER_TOKEN + 1


def prompt_props(props: list[Prop], brief: str, budget: int = LIBRARY_BUDGET_TOKENS) -> list[Prop]:
    """The props whose rows the prompt carries: all of them when they fit ``budget`` tokens, else a varied subset.

    The subset takes one prop of each role in turn (roles by name), so every kind of object stays offered. Within a
    role a run or a grid comes first, then a prop whose role, category, tags or description share a word of four
    letters or more with the brief, then the order of a hash of the id. Deterministic: the same props and brief give
    the same subset. The model may only use the ids it was sent."""
    rows = {str(row["id"]): row for row in library_rows(props)}
    if sum(_row_tokens(row) for row in rows.values()) <= budget:  # pragma: no mutate: at exactly the budget the subset below takes every row in the same order
        return list(props)
    words = set(re.findall(r"[a-z]{4,}", brief.lower()))

    def rank(prop: Prop) -> tuple[int, int, str]:
        spec = prop.spec
        text = " ".join((spec.role, spec.category, spec.description, *spec.tags)).lower()
        matched = bool(words & set(re.findall(r"[a-z]{4,}", text)))
        return (spec.grouping not in PATTERN_GROUPINGS, not matched, hashlib.sha256(spec.id.encode()).hexdigest())

    by_role: dict[str, list[Prop]] = defaultdict(list)
    for prop in props:
        by_role[prop.spec.role].append(prop)
    queues = [sorted(by_role[role], key=rank) for role in sorted(by_role)]
    chosen: set[str] = set()
    used = 0
    for turn in range(max(len(queue) for queue in queues)):
        for queue in queues:
            if turn >= len(queue):
                continue
            cost = _row_tokens(rows[queue[turn].spec.id])
            if used + cost > budget:
                return [prop for prop in props if prop.spec.id in chosen]
            used += cost
            chosen.add(queue[turn].spec.id)
    return [prop for prop in props if prop.spec.id in chosen]  # pragma: no mutate: unreachable, everything fit


def instruction(brief: str, digest: Digest, props: list[Prop]) -> str:
    quoted = json.dumps(brief, ensure_ascii=False)
    digest_json = json.dumps(digest.to_dict(), sort_keys=True, ensure_ascii=False)
    library_json = json.dumps(library_rows(props), sort_keys=True, ensure_ascii=False)
    return f"Brief (quoted exactly):\n{quoted}\n\nDigest:\n{digest_json}\n\nLibrary:\n{library_json}\n"


def instruction_sha256(brief: str, digest: Digest, props: list[Prop]) -> str:
    payload = SYSTEM_PROMPT + "\n" + instruction(brief, digest, props)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
