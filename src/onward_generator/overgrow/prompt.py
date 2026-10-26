"""English instructions for the placement model. Examples use abstract ids."""

from __future__ import annotations

import hashlib
import json

from onward_generator.overgrow.digest import Digest
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.plan import PLAN_VERSION

PROMPT_VERSION = "0.1.0"

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
        })
    rows.sort(key=lambda row: str(row["id"]))
    return rows


def instruction(brief: str, digest: Digest, props: list[Prop]) -> str:
    quoted = json.dumps(brief, ensure_ascii=False)
    digest_json = json.dumps(digest.to_dict(), sort_keys=True, ensure_ascii=False)
    library_json = json.dumps(library_rows(props), sort_keys=True, ensure_ascii=False)
    return f"Brief (quoted exactly):\n{quoted}\n\nDigest:\n{digest_json}\n\nLibrary:\n{library_json}\n"


def instruction_sha256(brief: str, digest: Digest, props: list[Prop]) -> str:
    payload = SYSTEM_PROMPT + "\n" + instruction(brief, digest, props)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
