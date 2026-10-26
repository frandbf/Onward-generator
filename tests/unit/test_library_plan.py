"""A map texture plan is chosen from the library and can be replaced."""

from __future__ import annotations

import json
from pathlib import Path

from onward_generator.ai.providers import StaticProvider
from onward_generator.maps.workspace import create_map, refresh_texture_plan
from onward_generator.materials.library_plan import select_texture_plan
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON


def test_a_rejected_model_reply_falls_back_to_the_library() -> None:
    plan = select_texture_plan(
        name="Murcia",
        map_id="murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        brief=None,
        seed=3,
        provider=StaticProvider("replay", '{"choices":[{"slot":"roofs","library_id":"acg_asphalt011"}]}', "test"),
    )
    assert plan["selector"] == "library"
    assert plan["provider"] == "library"
    roofs = next(row for row in plan["entries"] if row["id"] == "roofs")
    assert roofs["library_id"].startswith("acg_roofingt")


def test_refresh_replaces_only_the_texture_plan(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(
        root, map_id="murcia", name="Murcia",
        latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100, seed=4,
    )
    marker = root / "data" / "processed" / "keep.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")
    before = (root / "map.json").read_text(encoding="utf-8")
    reply = json.dumps({"choices": [
        {"slot": "historic_facades", "library_id": "acg_paintedplaster006"},
        {"slot": "ochre_facades", "library_id": "acg_paintedplaster010"},
        {"slot": "plain_facades", "library_id": "acg_paintedplaster014"},
        {"slot": "stone_monuments", "library_id": "acg_rock022"},
        {"slot": "streets_plazas", "library_id": "acg_pavingstones007"},
        {"slot": "roofs", "library_id": "acg_roofingtiles011b"},
        {"slot": "brick", "library_id": "acg_bricks001"},
        {"slot": "carriageway", "library_id": "acg_asphalt011"},
        {"slot": "sidewalks", "library_id": "acg_concrete012"},
        {"slot": "plain_pedestrian", "library_id": "acg_concrete047a"},
        {"slot": "flat_roofs", "library_id": "acg_concrete047b"},
        {"slot": "doors", "library_id": "kenney_fantasy_door_wood"},
    ]})
    path = refresh_texture_plan(root, texture_provider=StaticProvider("replay", reply, "test"))
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert plan["selector"] == "ollama"
    assert next(row["library_id"] for row in plan["entries"] if row["id"] == "roofs") == "acg_roofingtiles011b"
    assert (root / "map.json").read_text(encoding="utf-8") == before
    assert marker.read_text(encoding="utf-8") == "keep"
