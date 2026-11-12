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
    MIN_ROLES,
    PICK_SCHEMA,
    PickError,
    parse_pick,
    pick_props,
    pick_schema,
)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
       b"\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")
BASE = ASSETS_BY_ID["concrete_road_barrier"]


def _props(roles: tuple[str, ...] = ("barrier", "bench", "barrel", "cone"), per_role: int = 3) -> list[Prop]:
    mesh = box(-0.3, 0.3, -0.2, 0.2, 0.0, 0.8)
    uvs = np.zeros((mesh.vertex_count, 2), dtype=np.float32)
    return [Prop(replace(BASE, id=f"{role}_{k}", role=role), mesh, uvs, PNG) for role in roles for k in range(per_role)]


def _reply(*ids: str, version: str = "0.1.0", mood: object = "A barricaded street.") -> str:
    return json.dumps({"pick_version": version, "mood": mood, "asset_ids": list(ids)})


def _offered(props: list[Prop]) -> dict[str, Prop]:
    return {p.spec.id: p for p in props}


def test_a_pick_names_six_to_twenty_offered_ids_of_three_roles() -> None:
    assert (MIN_PICK, MAX_PICK, MIN_ROLES) == (6, 20, 3)
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
        (_reply("barrier_0", "barrier_1", "barrier_2", "bench_0", "bench_1", "bench_2"), "at least 3 roles, not 2"),
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
