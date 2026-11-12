"""The model picks the props, the rules place them (ADR-010 amendment of 12 November 2026)."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from onward_generator.ai.providers import ProviderError, ProviderResult, StaticProvider
from onward_generator.generation.mesh import box
from onward_generator.overgrow import pick as pick_module
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.pick import (
    MAX_PICK,
    MIN_PICK,
    MIN_SPOTS,
    PICK_SCHEMA,
    SYSTEM_PROMPT,
    PickError,
    instruction,
    instruction_sha256,
    parse_pick,
    pick_props,
    pick_schema,
)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
       b"\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")
BASE = ASSETS_BY_ID["concrete_road_barrier"]
# One placement rule (spot) per role: barriers_curb, furniture_off_lane, waste_ground, structures_edge.
CATEGORY = {"barrier": "barriers", "bench": "furniture", "barrel": "waste", "cone": "structures"}


def _props(roles: tuple[str, ...] = ("barrier", "bench", "barrel", "cone"), per_role: int = 3) -> list[Prop]:
    mesh = box(-0.3, 0.3, -0.2, 0.2, 0.0, 0.8)
    uvs = np.zeros((mesh.vertex_count, 2), dtype=np.float32)
    return [Prop(replace(BASE, id=f"{role}_{k}", role=role, category=CATEGORY.get(role, "waste")), mesh, uvs, PNG)
            for role in roles for k in range(per_role)]


def _reply(*ids: str, version: str = "0.1.0", mood: object = "A barricaded street.") -> str:
    return json.dumps({"pick_version": version, "mood": mood, "asset_ids": list(ids)})


def _offered(props: list[Prop]) -> dict[str, Prop]:
    return {p.spec.id: p for p in props}


def test_a_pick_names_six_to_twenty_offered_ids_of_three_spots() -> None:
    assert (MIN_PICK, MAX_PICK, MIN_SPOTS) == (6, 20, 3)
    offered = _offered(_props())
    ids = ("barrier_0", "barrier_1", "bench_0", "bench_1", "barrel_0", "cone_0")
    assert parse_pick(_reply(*ids), offered) == (ids, "A barricaded street.")
    for text, message in (
        ("{nope", "not JSON"),
        (_reply(*ids, version="0.2.0"), "pick_version"),
        ("[]", "pick_version"),
        (_reply(*ids, mood=" "), "mood"),
        (_reply(*ids, mood=3), "mood"),
        (json.dumps({"pick_version": "0.1.0", "mood": "m", "asset_ids": "barrier_0"}), "list of ids"),
        (json.dumps({"pick_version": "0.1.0", "mood": "m", "asset_ids": [1, 2]}), "list of ids"),
        (_reply(*ids[:5], "lamp_9"), "not in the library: lamp_9"),
        (_reply(*ids[:5], "barrier_0"), "chosen once"),
        (_reply(*ids[:5]), "from 6 to 20 ids, not 5"),
        (_reply("barrier_0", "barrier_1", "barrier_2", "bench_0", "bench_1", "bench_2"), "at least 3 spots, not 2"),
    ):
        with pytest.raises(PickError, match=message):
            parse_pick(text, offered)
    many = _offered(_props(roles=("barrier", "bench", "barrel", "cone", "crate", "pallet", "debris"), per_role=3))
    assert len(parse_pick(_reply(*list(many)[:20]), many)[0]) == 20
    with pytest.raises(PickError, match="not 21"):
        parse_pick(_reply(*list(many)[:21]), many)


def test_a_small_library_is_picked_whole() -> None:
    small = _offered(_props(roles=("barrier", "bench"), per_role=2))
    assert parse_pick(_reply(*small), small)[0] == tuple(small)
    with pytest.raises(PickError, match="from 4 to 20 ids, not 3"):
        parse_pick(_reply(*list(small)[:3]), small)
    schema = pick_schema(list(small), 4)
    assert schema["properties"]["asset_ids"]["minItems"] == 4 and schema["properties"]["asset_ids"]["items"]["enum"] == sorted(small)
    assert pick_schema(["b", "a"])["properties"]["asset_ids"] | {"items": None} == {
        "type": "array", "items": None, "uniqueItems": True, "minItems": MIN_PICK, "maxItems": MAX_PICK}


class _Replies:
    name = "replies"

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
        self.calls.append((instruction, schema))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return ProviderResult(str(answer), {})


def test_a_failed_pick_is_asked_again_quoting_the_failure_then_left_to_the_rules() -> None:
    props = _props()
    ids = ("barrier_0", "barrier_1", "bench_0", "bench_1", "barrel_0", "cone_0")
    second = _Replies(_reply(*ids[:5]), _reply(*ids))
    result = pick_props(props, brief="", provider=second)
    assert result.ok and result.origin == "ai_generated" and result.asset_ids == ids and result.reason is None
    assert "from 6 to 20 ids, not 5" in second.calls[1][0] and "previous reply was rejected" in second.calls[1][0]
    assert "previous reply" not in second.calls[0][0]
    assert second.calls[0][1]["properties"]["asset_ids"]["minItems"] == MIN_PICK
    record = result.record
    assert record["schema"] == PICK_SCHEMA and record["attempts"] == 2 and record["asset_ids"] == list(ids)
    assert record["mood"] == "A barricaded street." and record["library"] == {"offered": 12, "fitted": 12}
    down = _Replies(ProviderError("Ollama is not running"), ProviderError("Ollama is not running"))
    failed = pick_props(props, brief="", provider=down)
    assert not failed.ok and failed.origin == "rules_fallback" and failed.asset_ids == ()
    assert failed.reason == "Ollama is not running" and failed.record["attempts"] == 2 and failed.record["reason"] == failed.reason


def test_a_stored_pick_is_replayed_only_for_the_same_instruction() -> None:
    props = _props()
    ids = ("barrier_0", "barrier_1", "bench_0", "bench_1", "barrel_0", "cone_0")
    stored = pick_props(props, brief="", provider=StaticProvider("replay", _reply(*ids), "test")).record

    class _Never:
        name = "never"

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            raise AssertionError("a stored pick is replayed without a call")

    again = pick_props(props, brief="", provider=_Never(), replay=stored)
    assert again.origin == "replay" and again.asset_ids == ids and again.record["attempts"] == 0
    assert pick_props(props, brief="", provider=_Never(), replay=again.record).origin == "replay"
    for changed in ({"schema": "other"}, {"instruction_sha256": "0" * 64}, {"origin": "rules_fallback"}, {"raw": None},
                    {"raw": _reply(*ids[:2])}):
        asked = _Replies(_reply(*ids))
        assert pick_props(props, brief="", provider=asked, replay={**stored, **changed}).origin == "ai_generated"
        assert len(asked.calls) == 1
    asked = _Replies(_reply(*ids))
    assert pick_props(props, brief="a different brief", provider=asked, replay=stored).origin == "ai_generated"


def test_the_default_provider_waits_for_the_overgrow_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[float] = []

    class _Fake:
        name = "fake"

        def __init__(self, *, timeout_s: float) -> None:
            made.append(timeout_s)

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            raise ProviderError("offline")

    import onward_generator.ai.providers as providers

    monkeypatch.setattr(providers, "OllamaProvider", _Fake)
    assert not pick_props(_props(), brief="").ok
    assert made == [pick_module.OVERGROW_TIMEOUT_S] == [600.0]


def test_the_schema_is_whole() -> None:
    assert pick_schema(["b", "a"], 4) == {
        "type": "object",
        "additionalProperties": False,
        "required": ["pick_version", "mood", "asset_ids"],
        "properties": {
            "pick_version": {"const": "0.1.0"},
            "mood": {"type": "string", "minLength": 1, "maxLength": 240},
            "asset_ids": {"type": "array", "items": {"enum": ["a", "b"]}, "uniqueItems": True, "minItems": 4, "maxItems": MAX_PICK},
        },
    }


def test_the_instruction_quotes_the_brief_as_written_and_sorts_each_row() -> None:
    props = _props(roles=("barrier",), per_role=1)
    text = instruction("Ruelle à l'abandon", props)
    assert text.startswith('Brief (quoted exactly):\n"Ruelle à l\'abandon"\n\nLibrary:\n[{')
    row = text.split("Library:\n", 1)[1]
    assert row.index('"category"') < row.index('"id"') < row.index('"role"')
    assert json.loads(row) == [r | {"spot": "barriers_curb"} for r in json.loads(json.dumps(pick_module.library_rows(props)))]
    assert row.index('"role"') < row.index('"spot"') < row.index('"surfaces"')
    accented = [Prop(replace(props[0].spec, description="Barrière usée"), props[0].mesh, props[0].uvs, PNG)]
    assert "Barrière usée" in instruction("", accented)
    assert instruction_sha256("x", props) == __import__("hashlib").sha256((SYSTEM_PROMPT + "\n" + instruction("x", props)).encode()).hexdigest()


def test_the_reasons_a_pick_is_refused_are_named() -> None:
    offered = _offered(_props())
    ids = ["barrier_0", "barrier_1", "bench_0", "bench_1", "barrel_0", "cone_0"]
    for text, message in (
        (_reply(*ids, mood=""), "the reply needs a mood sentence"),
        (json.dumps({"pick_version": "0.1.0", "mood": "m", "asset_ids": "x"}), "asset_ids must be a list of ids"),
        (_reply(*ids[:4], "zz_1", "aa_2"), "ids that are not in the library: aa_2, zz_1"),
        (_reply(*ids[:5], ids[0]), "each id may be chosen once"),
    ):
        with pytest.raises(PickError) as error:
            parse_pick(text, offered)
        assert str(error.value) == message


def test_the_retry_repeats_the_instruction_and_the_records_are_whole() -> None:
    props = _props()
    ids = ("barrier_0", "barrier_1", "bench_0", "bench_1", "barrel_0", "cone_0")
    asked = _Replies("{bad", _reply(*ids))
    result = pick_props(props, brief="Calle cortada", provider=asked)
    usable = pick_module.prompt_props(pick_module.fitted_props(props), "Calle cortada")
    first, second = asked.calls[0][0], asked.calls[1][0]
    assert first == instruction("Calle cortada", usable) and second.startswith(first)
    assert '"Calle cortada"' in first
    assert result.record == {
        "schema": PICK_SCHEMA, "pick_version": "0.1.0", "instruction_sha256": instruction_sha256("Calle cortada", usable),
        "origin": "ai_generated", "raw": _reply(*ids), "reason": None, "asset_ids": list(ids), "mood": "A barricaded street.",
        "attempts": 2, "library": {"offered": 12, "fitted": 12},
    }
    replayed = pick_props(props, brief="Calle cortada", provider=_Replies(), replay=result.record)
    assert replayed.record == {**result.record, "origin": "replay", "attempts": 0}
    down = _Replies("{bad", "{worse")
    failed = pick_props(props, brief="Calle cortada", provider=down)
    assert failed.ok is False
    assert failed.record == {
        "schema": PICK_SCHEMA, "pick_version": "0.1.0", "instruction_sha256": instruction_sha256("Calle cortada", usable),
        "origin": "rules_fallback", "raw": "{worse", "reason": failed.reason, "asset_ids": [], "mood": None,
        "attempts": 2, "library": {"offered": 12, "fitted": 12},
    }
    assert failed.reason.startswith("the reply is not JSON")
    silent = pick_props(props, brief="b", provider=_Replies(ProviderError("x"), ProviderError("y")))
    assert silent.record["raw"] == "" and silent.reason == "y"


def test_the_model_gets_the_system_prompt_and_the_small_library_bound() -> None:
    small = _props(roles=("barrier", "bench"), per_role=2)

    class _Seen:
        name = "seen"
        system = ""
        schema: dict = {}

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            self.system, self.schema = system, schema
            return ProviderResult(_reply(*(p.spec.id for p in small)), {})

    seen = _Seen()
    assert pick_props(small, brief="", provider=seen).ok
    assert seen.system == SYSTEM_PROMPT and seen.schema["properties"]["asset_ids"]["minItems"] == 4


def test_a_large_library_is_cut_by_the_brief() -> None:
    many = _props(roles=("barrier", "bench", "barrel", "cone", "crate", "pallet", "debris"), per_role=40)
    calls: list[str] = []

    class _Seen:
        name = "seen"

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            calls.append(instruction)
            raise ProviderError("off")

    pick_props(many, brief="barrier", provider=_Seen())
    expected = pick_module.prompt_props(pick_module.fitted_props(many), "barrier")
    assert calls[0] == instruction("barrier", expected)


def test_only_props_the_rules_place_are_offered() -> None:
    """Rue Mouffetard: a traffic light and lamp posts were picked and never placed; a gift box stood on the sidewalk."""
    props = _props()
    lamp = Prop(replace(BASE, id="lamp_0", role="lamppost", category="barriers"), props[0].mesh, props[0].uvs, PNG)
    gift = Prop(replace(BASE, id="gift_0", role="giftbox", category="waste"), props[0].mesh, props[0].uvs, PNG)
    assert pick_module._spot(lamp) == "" and pick_module._spot(gift) == "waste_ground"
    assert [p.spec.id for p in pick_module.placeable([*props, lamp, gift])] == [p.spec.id for p in props]
    calls: list[str] = []

    class _Seen:
        name = "seen"

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            calls.append(instruction)
            raise ProviderError("off")

    result = pick_props([*props, lamp, gift], brief="", provider=_Seen())
    assert "lamp_0" not in calls[0] and "gift_0" not in calls[0] and result.record["library"] == {"offered": 12, "fitted": 14}
    # Three spots are needed even when the ids span four roles of one rule.
    waste = _offered(_props(roles=("crate", "pallet", "bag", "sack"), per_role=2) + _props(roles=("barrier", "bench"), per_role=1))
    with pytest.raises(PickError, match="at least 3 spots, not 1"):
        parse_pick(_reply("crate_0", "crate_1", "pallet_0", "pallet_1", "bag_0", "sack_0"), waste)
    assert parse_pick(_reply("crate_0", "crate_1", "pallet_0", "bag_0", "barrier_0", "bench_0"), waste)[0][-1] == "bench_0"


def test_a_spot_with_three_textured_props_offers_no_flat_one() -> None:
    import io

    from PIL import Image

    def png(px: int) -> bytes:
        out = io.BytesIO()
        Image.new("RGB", (px, px), (90, 90, 90)).save(out, format="PNG")
        return out.getvalue()

    assert pick_module.TEXTURED_PER_SPOT == 3
    mesh = box(-0.3, 0.3, -0.2, 0.2, 0.0, 0.8)
    full = np.tile(np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], dtype=np.float32), (8, 1))[:mesh.vertex_count]

    def prop(asset_id: str, category: str, textured: bool) -> Prop:
        return Prop(replace(BASE, id=asset_id, role="crate", category=category), mesh, full if textured else full / 512,
                    png(64) if textured else png(512))

    waste = [prop(f"photo_{k}", "waste", True) for k in range(3)] + [prop("flat_w", "waste", False)]
    furniture = [prop(f"photo_f{k}", "furniture", True) for k in range(2)] + [prop("flat_f", "furniture", False)]
    offered = [p.spec.id for p in pick_module.placeable([*waste, *furniture])]
    assert "flat_w" not in offered and "flat_f" in offered and len(offered) == 6
