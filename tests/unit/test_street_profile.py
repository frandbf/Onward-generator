"""Street profile (plan D2): enumerated ids, a strict parser, a whitelist guard, byte-for-byte replay.

Every provider here is a stub that counts its calls; no test needs a running model.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from onward_generator.ai import street_profile as sp
from onward_generator.ai.providers import OllamaProvider, ProviderError, ProviderResult
from onward_generator.ai.street_profile import (
    ALLOWED_POINTERS,
    CONDITION_GROUPS,
    EFFECTS,
    FIELDS,
    MAX_RESPONSE_BYTES,
    NOT_OFFERED_STYLES,
    PROFILE_ATTEMPTS,
    PROTECTED_POINTERS,
    RULES_PROFILE,
    Context,
    StreetProfile,
    StreetProfileError,
    field_ids,
    guard,
    load_record,
    parse_street_profile,
    profile_flags,
    provenance_for,
    record_path,
    report_block,
    resolve_street_profile,
    selection_for,
    selection_path,
    translate,
)
from onward_generator.maps.workspace import (
    create_map,
    load_map,
    store_brief,
    store_sightline_cover,
    store_street_profile,
)
from onward_generator.materials.trim_style import load_style_table
from onward_generator.overgrow.library_catalog import read_selection
from onward_generator.overgrow.moods import load_moods
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

LIBRARY = [
    ("barrel_burned", "burned"), ("bin_intact", "intact"), ("car_wrecked", "wrecked"),
    ("crate_worn", "worn"), ("fence_damaged", "damaged"), ("tree", ""),
]
NEUTRAL_REPLY = {"profile_version": "0.2.0", **dict.fromkeys(FIELDS, "map_default")}


def reply(summary: str | None = "A calm street.", **ids: str) -> str:
    document: dict[str, Any] = {**NEUTRAL_REPLY, **ids}
    if summary is not None:
        document["summary"] = summary
    return json.dumps(document)


class Stub:
    """Answers from a list; an exception in the list is raised instead. Counts every call."""

    name = "stub"

    def __init__(self, *replies: str | BaseException) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult:
        assert system == sp.SYSTEM_PROMPT and schema == sp.profile_schema()
        self.calls.append(instruction)
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ProviderResult(item, {"provider": "stub"})


class Refuse:
    name = "refuse"

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult:
        raise AssertionError("a replay must not call the provider")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "murcia"
    create_map(path, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    store_street_profile(path, True)
    monkeypatch.setattr(sp, "library_conditions", lambda _library: list(LIBRARY))
    return path


def _set_mood(root: Path, mood: str) -> None:
    document = load_map(root)
    document["street_mood"] = mood
    (root / "map.json").write_text(json.dumps(document))


def resolve(root: Path, provider: object) -> dict[str, Any]:
    return resolve_street_profile(root, provider=provider, library=None).record  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ the parser


@pytest.mark.parametrize(("field", "ident"), [(f, i) for f in FIELDS for i in field_ids(f)])
def test_parse_accepts_every_enumerated_id(field: str, ident: str) -> None:
    profile = parse_street_profile(reply(**{field: ident}))
    assert profile.ids() == {**dict.fromkeys(FIELDS, "map_default"), field: ident}
    assert profile.summary == "A calm street."
    assert parse_street_profile(reply(summary=None, **{field: ident})) == StreetProfile(**{**profile.ids()})


@pytest.mark.parametrize("extra", [{"radius_m": 300}, {"sources": ["osm"]}, {"buildings": []}])
def test_parse_rejects_unknown_field(extra: dict[str, Any]) -> None:
    with pytest.raises(StreetProfileError) as caught:
        parse_street_profile(json.dumps({**NEUTRAL_REPLY, **extra}))
    assert caught.value.code == "AI_PROFILE_SCHEMA" and "Additional properties" in caught.value.message


@pytest.mark.parametrize(("field", "value"), [
    ("dominant_typology", "gothic"), ("palette", "neon"), ("props_profile", "all"), ("abandonment", "years_50"),
    ("cover", "max"), ("abandonment", 30), ("cover", None), ("street_mood", "busy"), ("street_mood", "map default"),
    ("profile_version", "0.1.0"),  # a model's reply is never carried forward: only a stored one is
])
def test_parse_rejects_id_outside_enum(field: str, value: object) -> None:
    with pytest.raises(StreetProfileError) as caught:
        parse_street_profile(json.dumps({**NEUTRAL_REPLY, field: value}))
    assert caught.value.code == "AI_PROFILE_SCHEMA" and caught.value.message.startswith(f"/{field}: ")


@pytest.mark.parametrize("field", ["profile_version", *FIELDS])
def test_parse_rejects_missing_field(field: str) -> None:
    document = dict(NEUTRAL_REPLY)
    del document[field]
    with pytest.raises(StreetProfileError) as caught:
        parse_street_profile(json.dumps(document))
    assert caught.value.code == "AI_PROFILE_SCHEMA" and f"'{field}' is a required property" in caught.value.message


@pytest.mark.parametrize(("text", "code"), [
    ("", "AI_PROFILE_JSON"),
    ("   \n", "AI_PROFILE_JSON"),
    (reply() + " and more", "AI_PROFILE_JSON"),
    (reply().replace('"A calm street."', "NaN"), "AI_PROFILE_JSON"),
    ('{"cover": "off", "cover": "light"}', "AI_PROFILE_JSON"),
    ("```json\n" + reply() + "\n```", "AI_PROFILE_JSON"),
    ("[" + reply() + "]", "AI_PROFILE_SCHEMA"),
    ('"map_default"', "AI_PROFILE_SCHEMA"),
])
def test_parse_rejects_non_strict_json(text: str, code: str) -> None:
    with pytest.raises(StreetProfileError) as caught:
        parse_street_profile(text)
    assert caught.value.code == code
    assert str(caught.value).endswith(f"[{code}]")


def test_size_cap_boundary() -> None:
    body = reply()
    exact = body + " " * (MAX_RESPONSE_BYTES - len(body))
    assert len(exact.encode()) == MAX_RESPONSE_BYTES == 4096
    assert parse_street_profile(exact).ids() == RULES_PROFILE.ids()
    with pytest.raises(StreetProfileError) as caught:
        parse_street_profile(exact + " ")
    assert caught.value.code == "AI_PROFILE_SIZE"
    assert caught.value.message == "the reply is 4097 bytes; the limit is 4096"


def test_summary_over_200_chars_rejected() -> None:
    assert parse_street_profile(reply(summary="x" * 200)).summary == "x" * 200
    with pytest.raises(StreetProfileError) as caught:
        parse_street_profile(reply(summary="x" * 201))
    assert caught.value.code == "AI_PROFILE_SCHEMA"
    with pytest.raises(StreetProfileError):
        parse_street_profile(json.dumps({**NEUTRAL_REPLY, "summary": 3}))


def test_summary_is_never_read(root: Path) -> None:
    resolve(root, Stub(reply(summary="One.", abandonment="years_30", dominant_typology="anglo")))
    first = profile_flags(root)
    store_brief(root, "a new sentence, so the model is asked again")
    stub = Stub(reply(summary="Two, quite different.", abandonment="years_30", dominant_typology="anglo"))
    resolve(root, stub)
    assert len(stub.calls) == 1 and profile_flags(root) == first and first


@pytest.mark.parametrize("summary", ['__import__("os").system("x")', "$(rm -rf /)", "--profile 10; --facade-style x"])
def test_security_summary_never_in_argv(root: Path, summary: str) -> None:
    from onward_generator.pipeline_run import plan_steps

    store_sightline_cover(root, True)
    record = resolve(root, Stub(reply(summary=summary, dominant_typology="anglo", abandonment="years_10",
                                      props_profile="wrecked", cover="light")))
    assert record["summary"] == summary and record["summary_status"] == "unverified"
    argv = [arg for _name, step in plan_steps(root) for arg in step]
    assert all(summary not in arg for arg in argv)
    assert "--max-props" in argv and "--prefer" in argv


# ------------------------------------------------------------------------------------------------ the table


def test_effects_table_matches_schema() -> None:
    from_schema = {(f, i) for f in FIELDS for i in field_ids(f) if i != "map_default"}
    assert set(EFFECTS) == from_schema
    assert all(i == "map_default" or (f, i) in EFFECTS for f in FIELDS for i in field_ids(f))
    assert set(field_ids("dominant_typology")) - {"map_default"} == set(load_style_table().styles) - NOT_OFFERED_STYLES
    assert NOT_OFFERED_STYLES == {"arid"} and NOT_OFFERED_STYLES <= set(load_style_table().styles)
    assert set(field_ids("abandonment")) - {"map_default"} == {f"years_{y}" for y in (10, 30, 100)}
    for (field, ident), effect in EFFECTS.items():
        assert (effect.field, effect.id) == (field, ident)
        assert effect.adr.startswith("ADR-") and effect.effect
        assert effect.pointer in ALLOWED_POINTERS
    assert {e.adr for e in EFFECTS.values()} == {"ADR-020, ADR-027", "ADR-009", "ADR-010", "ADR-032", "ADR-044"}
    assert {(e.stage, e.flag) for e in EFFECTS.values()} == {
        ("materials", "--facade-style"), ("materials", "--repetition"), ("overgrow", "--prefer"),
        ("overgrow", "--profile"), ("overgrow", "--street-mood"), ("cover", "--max-props"),
    }
    assert set(field_ids("street_mood")) - {"map_default"} == set(sp.MOOD_IDS) == set(load_moods().moods)


def _expected(root: Path, field: str, ident: str) -> dict[str, list[str]]:
    record = str(record_path(root))
    if ident == "map_default":
        return {}
    if field == "dominant_typology":
        return {"materials": ["--facade-style", ident, "--street-profile", record]}
    if field == "palette":
        return {"materials": ["--repetition", "--cell-m", "100.0", "--street-profile", record]}
    if field == "props_profile":
        return {"overgrow": ["--prefer", str(selection_path(root)), "--street-profile", record]}
    if field == "abandonment":
        return {"overgrow": ["--profile", ident.removeprefix("years_"), "--street-profile", record]}
    if field == "street_mood":
        return {"overgrow": ["--street-mood", ident, "--street-profile", record]}
    return {"cover": ["--max-props", {"off": "0", "light": "8", "full": "24"}[ident], "--street-profile", record]}


@pytest.mark.parametrize(("field", "ident"), [(f, i) for f in FIELDS for i in field_ids(f)])
def test_profile_flags_table(root: Path, field: str, ident: str) -> None:
    store_sightline_cover(root, True)
    record = resolve(root, Stub(reply(**{field: ident})))
    assert record["origin"] == "ai_generated" and record["ids"][field] == ident
    assert profile_flags(root) == _expected(root, field, ident)
    if field == "props_profile" and ident != "map_default":
        assert read_selection(selection_path(root)) == selection_for(ident, LIBRARY)
    else:
        assert not selection_path(root).exists()


def test_profile_flags_are_empty_with_the_switch_off_or_without_a_root(root: Path) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    assert profile_flags(root)
    store_street_profile(root, False)
    assert profile_flags(root) == {}
    assert profile_flags(None) == {}


def test_a_missing_or_tampered_record_gives_no_flags_and_a_warning(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert profile_flags(root) == {}
    assert "warning: street profile not applied" in capsys.readouterr().err
    resolve(root, Stub(reply(abandonment="years_10")))
    path = record_path(root)
    document = json.loads(path.read_text())
    document["changes"][0]["pointer"] = "/area/radius_m"
    path.write_text(json.dumps(document))
    assert profile_flags(root) == {}
    assert "AI_PROFILE_PROTECTED" in capsys.readouterr().err


def test_cover_inactive_without_sightline_cover(root: Path) -> None:
    from onward_generator.pipeline_run import plan_steps

    record = resolve(root, Stub(reply(cover="light")))
    (change,) = record["changes"]
    assert (change["status"], change["reason"], change["before"], change["after"]) == ("skipped", "profile_inactive", 24, 24)
    assert profile_flags(root) == {}
    steps = plan_steps(root)
    assert "cover" not in dict(steps) and all("--max-props" not in argv for _n, argv in steps)


def test_typology_does_not_override_explicit_map_style(root: Path) -> None:
    from onward_generator.next_step import materials_command

    document = load_map(root)
    (root / "map.json").write_text(json.dumps({**document, "facades": {"style": "anglo"}}))
    record = resolve(root, Stub(reply(dominant_typology="mediterranean")))
    (change,) = record["changes"]
    assert (change["status"], change["reason"], change["before"]) == ("skipped", "user_setting", "anglo")
    argv = list(materials_command(root / "data" / "processed" / "scene_dress").argv)
    assert argv.count("--facade-style") == 1 and argv[argv.index("--facade-style") + 1] == "anglo"
    assert "--street-profile" not in argv


def test_palette_is_skipped_when_repetition_is_on_or_the_plan_is_not_calm(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.maps.workspace import store_repetition

    store_repetition(root, True)
    (change,) = resolve(root, Stub(reply(palette="extended_pools")))["changes"]
    assert (change["status"], change["reason"]) == ("skipped", "user_setting")
    store_repetition(root, False)
    monkeypatch.setattr(sp, "_calm_plan", lambda _root: False)
    (change,) = resolve(root, Stub(reply(palette="extended_pools")))["changes"]
    assert (change["status"], change["reason"]) == ("skipped", "texture_plan_not_calm")


def test_calm_plan_reads_the_map_texture_plan(root: Path) -> None:
    plan = root / "config" / "texture_plan.json"
    assert sp._calm_plan(root) is True
    plan.write_text("{not json")
    assert sp._calm_plan(root) is False
    plan.unlink()
    assert sp._calm_plan(root) is False


def test_a_user_prop_selection_wins_over_the_props_profile(root: Path) -> None:
    (root / "config" / "prop_selection.json").write_text(json.dumps({"ids": ["bin_intact"]}))
    (change,) = resolve(root, Stub(reply(props_profile="intact")))["changes"]
    # The user's selection keeps out the other props, so the profile adds no preference (its pointer stays None).
    assert (change["status"], change["reason"], change["before"]) == ("skipped", "user_setting", None)
    assert not selection_path(root).exists()


def test_selection_for_condition_groups(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert selection_for("intact", LIBRARY) == ["bin_intact", "tree"]
    assert selection_for("worn", LIBRARY) == ["crate_worn", "fence_damaged", "tree"]
    assert selection_for("wrecked", LIBRARY) == ["barrel_burned", "car_wrecked", "fence_damaged", "tree"]
    assert selection_for("map_default", LIBRARY) == []
    assert selection_for("intact", [("tree", "")]) == []
    assert dict(CONDITION_GROUPS) == {"intact": ("intact",), "worn": ("damaged", "worn"), "wrecked": ("burned", "damaged", "wrecked")}
    monkeypatch.setattr(sp, "library_conditions", lambda _library: [("bin_intact", "intact"), ("tree", "")])
    (change,) = resolve(root, Stub(reply(props_profile="wrecked")))["changes"]
    assert (change["status"], change["reason"]) == ("skipped", "props_profile_empty")
    assert profile_flags(root) == {} and not selection_path(root).exists()
    store_brief(root, "an intact street")  # new inputs, so the stored reply is not replayed
    resolve(root, Stub(reply(props_profile="intact")))
    assert read_selection(selection_path(root)) == ["bin_intact", "tree"]
    assert json.loads(selection_path(root).read_text())["schema"] == "onward_generator.prop_selection"
    store_brief(root, "no profile")
    resolve(root, Stub(reply()))
    assert not selection_path(root).exists()


def test_library_conditions_reads_the_catalogue(tmp_path: Path) -> None:
    assert sp.library_conditions(None) == []
    assert sp.library_conditions(tmp_path / "missing") == []


# ------------------------------------------------------------------------------------------------ the guard


def _before() -> dict[str, Any]:
    return {
        "area": {"radius_m": 150.0}, "center": {"latitude": 1.0}, "sources": {}, "export": {"sha256": "e"},
        "budgets": {"export_card_sha256": "e"}, "core": {"core_radius_m": None}, "buildings_sha256": "b",
        "sightline_cover": True,
        "materials": {"facade_style": "auto", "repetition": False},
        "overgrow": {"profile_years": 100, "selection": None, "prefer": None, "street_mood": None},
        "cover": {"max_props": 24},
    }


@pytest.mark.parametrize("pointer", PROTECTED_POINTERS)
def test_guard_refuses_protected_pointer(root: Path, monkeypatch: pytest.MonkeyPatch, pointer: str) -> None:
    real = sp.translate

    def leaky(profile: StreetProfile, before: Any, context: Context) -> Any:
        after, rows = real(profile, before, context)
        if profile != RULES_PROFILE:
            after[pointer.strip("/")] = "changed by a bug"
        return after, rows

    monkeypatch.setattr(sp, "translate", leaky)
    record = resolve(root, Stub(reply(abandonment="years_10")))
    assert record["origin"] == "rules" and record["provenance"] == "estimated"
    assert record["ids"] == RULES_PROFILE.ids() and record["changes"] == []
    assert record["reason"] == f"AI_PROFILE_PROTECTED: the profile would change {pointer}"
    assert [d["code"] for d in record["diagnostics"]] == ["AI_PROFILE_PROTECTED"]
    assert profile_flags(root) == {}


def test_guard_passes_each_allowed_pointer_and_the_sets_are_disjoint() -> None:
    before = _before()
    for pointer in ALLOWED_POINTERS:
        after = json.loads(json.dumps(before))
        top, key = pointer.strip("/").split("/")
        after[top][key] = "x"
        assert guard(before, after) == []
    assert not set(ALLOWED_POINTERS) & set(PROTECTED_POINTERS)
    assert not any(a.startswith(p + "/") for a in ALLOWED_POINTERS for p in PROTECTED_POINTERS)
    assert set(PROTECTED_POINTERS) == {f"/{key}" for key in _before() if key not in ("materials", "overgrow", "cover")}
    assert guard(before, {**before, "area": {"radius_m": 300.0}}) == ["/area/radius_m"]


def test_effective_settings_hold_the_protected_and_the_allowed_keys(root: Path) -> None:
    settings = sp.effective_settings(root)
    assert set(settings) == set(_before())
    assert settings["materials"] == {"facade_style": "auto", "repetition": False}
    assert settings["overgrow"] == {"profile_years": 100, "selection": None, "prefer": None, "street_mood": None}
    assert settings["cover"] == {"max_props": 24} and settings["sightline_cover"] is False
    assert settings["buildings_sha256"] is None and settings["export"]["sha256"]
    assert settings["area"] == {"radius_m": 150.0, "margin_m": 30.0, "crs": "EPSG:25830"}
    (root / "config" / "prop_selection.json").write_text(json.dumps({"ids": ["bin_intact"]}))
    assert sp.effective_settings(root)["overgrow"] == {"profile_years": 100, "selection": "config/prop_selection.json", "prefer": None,
                                                       "street_mood": None}
    _set_mood(root, "emptied")
    assert sp.effective_settings(root)["overgrow"]["street_mood"] == "emptied"


def test_translate_writes_only_applied_ids() -> None:
    profile = StreetProfile("anglo", "extended_pools", "wrecked", "years_30", "off", "lived")
    after, rows = translate(profile, _before(), Context(True, tuple(LIBRARY)))
    assert (after["materials"], after["overgrow"], after["cover"]) == (
        {"facade_style": "anglo", "repetition": True},
        {"profile_years": 30, "selection": None, "prefer": "config/street_profile_selection.json", "street_mood": "lived"},
        {"max_props": 0},
    )
    assert [row["field"] for row in rows] == list(FIELDS)
    assert all(row["status"] == "applied" and row["reason"] is None for row in rows)
    assert guard(_before(), after) == []
    after, rows = translate(RULES_PROFILE, _before(), Context(True, tuple(LIBRARY)))
    assert after == _before() and rows == []


def test_changes_record_fields(root: Path) -> None:
    store_sightline_cover(root, True)
    record = resolve(root, Stub(reply(dominant_typology="generic", abandonment="years_30", cover="light")))
    keys = {"field", "id", "pointer", "stage", "flag", "before", "after", "status", "reason", "effect", "adr", "origin"}
    assert [row["field"] for row in record["changes"]] == ["dominant_typology", "abandonment", "cover"]
    for row in record["changes"]:
        assert set(row) == keys and row["origin"] == "ai_generated"
    abandonment = record["changes"][1]
    assert (abandonment["before"], abandonment["after"], abandonment["flag"], abandonment["adr"]) == (100, 30, "--profile", "ADR-010")
    resolve(root, Refuse())
    assert {row["origin"] for row in load_record(record_path(root))["changes"]} == {"replay"}


# ------------------------------------------------------------------------------------------------ resolution


def test_retry_quotes_failure(root: Path) -> None:
    stub = Stub("not json at all", reply(abandonment="years_30"))
    record = resolve(root, stub)
    assert len(stub.calls) == PROFILE_ATTEMPTS == 2
    assert "rejected" not in stub.calls[0]
    assert "The previous reply was rejected:" in stub.calls[1] and "AI_PROFILE_JSON" in stub.calls[1]
    assert stub.calls[1].startswith(stub.calls[0])
    assert (record["origin"], record["attempts"], record["reason"]) == ("ai_generated", 2, None)
    assert [d["code"] for d in record["diagnostics"]] == ["AI_PROFILE_JSON"]
    assert record["provider"] == {"provider": "stub"} and record["provenance"] == "ai_generated"


def test_two_failures_fall_back_to_rules(root: Path) -> None:
    stub = Stub(reply(cover="max"), "{}")
    record = resolve(root, stub)
    assert len(stub.calls) == 2
    assert (record["origin"], record["provenance"], record["attempts"]) == ("rules", "estimated", 2)
    assert record["ids"] == RULES_PROFILE.ids() and record["changes"] == []
    assert record["reason"].endswith("[AI_PROFILE_SCHEMA]") and "'cover' is a required property" in record["reason"]
    assert record["raw"] == "{}" and record["provider"] is None


def _closed_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


@pytest.mark.parametrize("provider", [
    lambda: Stub(ProviderError("Ollama is down"), ProviderError("Ollama is down")),
    lambda: Stub(OSError("socket"), OSError("socket")),
    lambda: Stub(TypeError("bad"), ValueError("bad")),
    lambda: OllamaProvider(host=f"http://127.0.0.1:{_closed_port()}", timeout_s=2),
])
def test_provider_error_never_fails(root: Path, provider: Any) -> None:
    record = resolve(root, provider())
    assert record["origin"] == "rules" and record["attempts"] == 2 and record["reason"].endswith("[AI_PROFILE_PROVIDER]")
    assert [d["code"] for d in record["diagnostics"]] == ["AI_PROFILE_PROVIDER"] * 2


def test_replay_skips_provider(root: Path) -> None:
    first = resolve(root, Stub(reply(abandonment="years_10")))
    assert first["origin"] == "ai_generated"
    second = resolve(root, Refuse())
    bytes_second = record_path(root).read_bytes()
    third = resolve(root, Refuse())
    assert record_path(root).read_bytes() == bytes_second
    assert second == third and second["origin"] == "replay" and second["attempts"] == 0
    for key in ("raw", "raw_sha256", "schema_sha256", "prompt_sha256", "inputs_sha256", "ids", "provider"):
        assert second[key] == first[key]
    assert report_block(record_path(root)) == {
        "schema_version": "0.2.0", "raw_sha256": first["raw_sha256"], "ids": first["ids"], "provenance": "ai_generated",
    }


def _change_brief(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_brief(root, "an abandoned city")


def _change_schema(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = root / "schema.json"
    copy.write_text(sp.SCHEMA_PATH.read_text() + "\n")
    monkeypatch.setattr(sp, "SCHEMA_PATH", copy)


def _change_prompt(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "SYSTEM_PROMPT", sp.SYSTEM_PROMPT + " ")


def _change_library(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "library_conditions", lambda _library: [*LIBRARY[:-1], ("tree", "worn")])


def _change_cover(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_sightline_cover(root, True)


def _change_centre(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = load_map(root)
    document["center"] = {"latitude": MURCIA_CENTER_LAT + 0.0001, "longitude": MURCIA_CENTER_LON}
    (root / "map.json").write_text(json.dumps(document))


@pytest.mark.parametrize("change", [_change_brief, _change_schema, _change_prompt, _change_library, _change_cover, _change_centre])
def test_replay_invalidated_per_hash(root: Path, monkeypatch: pytest.MonkeyPatch, change: Any) -> None:
    first = resolve(root, Stub(reply(abandonment="years_10")))
    stored = record_path(root).read_bytes()
    change(root, monkeypatch)
    stub = Stub(reply(abandonment="years_30"))
    record = resolve(root, stub)
    assert len(stub.calls) == 1 and record["origin"] == "ai_generated" and record["ids"]["abandonment"] == "years_30"
    assert [d["code"] for d in record["diagnostics"]] == ["AI_PROFILE_STALE"]
    archived = record_path(root).with_name(f"street_profile.{first['raw_sha256'][:12]}.json")
    assert archived.read_bytes() == stored


def test_an_archive_is_never_overwritten(root: Path) -> None:
    first = resolve(root, Stub(reply(abandonment="years_10")))
    archived = record_path(root).with_name(f"street_profile.{first['raw_sha256'][:12]}.json")
    archived.write_text("kept")
    store_brief(root, "another sentence")
    resolve(root, Stub(reply(abandonment="years_10")))
    assert archived.read_text() == "kept"


def test_replay_rejects_rules_record(root: Path) -> None:
    resolve(root, Stub("x", "y"))
    stub = Stub(reply(cover="off"))
    record = resolve(root, stub)
    assert len(stub.calls) == 1 and record["origin"] == "ai_generated" and record["diagnostics"] == []
    assert not list(record_path(root).parent.glob("street_profile.*.json"))


def test_a_tampered_stored_reply_is_asked_again(root: Path) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    document = json.loads(record_path(root).read_text())
    document["raw"] = reply(abandonment="years_100")
    record_path(root).write_text(json.dumps(document))
    stub = Stub(reply(abandonment="years_30"))
    assert resolve(root, stub)["ids"]["abandonment"] == "years_30" and len(stub.calls) == 1
    record_path(root).write_text("{broken")
    assert resolve(root, Stub(reply()))["origin"] == "ai_generated"


def test_provenance_label_never_observed() -> None:
    assert provenance_for("rules") == "estimated"
    assert provenance_for("ai_generated") == provenance_for("replay") == "ai_generated"


def test_the_prompt_carries_the_lists_and_counts_only(root: Path) -> None:
    stub = Stub(reply())
    resolve(root, stub)
    (message,) = stub.calls
    assert '"intact": 1' in message and '"worn": 2' in message and '"wrecked": 3' in message
    assert "bin_intact" not in message and f"{MURCIA_CENTER_LAT}, {MURCIA_CENTER_LON}" in message
    for field in FIELDS:
        assert all(ident in sp.SYSTEM_PROMPT for ident in field_ids(field))


# ------------------------------------------------------------------------------------------------ records and stages


@pytest.mark.parametrize(("edit", "code"), [
    (lambda d: d.update(schema="other"), "AI_PROFILE_RECORD"),
    (lambda d: d["ids"].update(cover="max"), "AI_PROFILE_RECORD"),
    (lambda d: d["ids"].pop("cover"), "AI_PROFILE_RECORD"),
    (lambda d: d.update(provenance="observed"), "AI_PROFILE_RECORD"),
    (lambda d: d.update(origin="observed"), "AI_PROFILE_RECORD"),
    (lambda d: d.update(changes={}), "AI_PROFILE_RECORD"),
    (lambda d: d["changes"][0].update(id="years_30"), "AI_PROFILE_PROTECTED"),
    (lambda d: d["changes"][0].update(field="nothing"), "AI_PROFILE_PROTECTED"),
])
def test_load_record_checks_every_id_and_change(root: Path, edit: Any, code: str) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    path = record_path(root)
    document = json.loads(path.read_text())
    edit(document)
    path.write_text(json.dumps(document))
    with pytest.raises(StreetProfileError) as caught:
        report_block(path)
    assert caught.value.code == code


def test_load_record_refuses_missing_large_or_broken_files(tmp_path: Path) -> None:
    for name, body in (("missing.json", None), ("big.json", " " * (64 * 1024 + 1)), ("broken.json", "{")):
        path = tmp_path / name
        if body is not None:
            path.write_text(body)
        with pytest.raises(StreetProfileError) as caught:
            load_record(path)
        assert caught.value.code == "AI_PROFILE_RECORD"


def test_next_step_golden_off(tmp_path: Path, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.next_step import materials_command, overgrow_command

    plain = tmp_path / "plain"
    create_map(plain, map_id="plain", name="Plain", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)

    def argv(map_root: Path) -> tuple[list[str], list[str]]:
        signs = map_root / "data" / "processed" / "scene_signs"
        scene = map_root / "data" / "processed" / "geo" / "geo_scene.json"
        return (list(materials_command(map_root / "data" / "processed" / "scene_dress").argv),
                list(overgrow_command(signs, scene).argv))

    def strip(pair: tuple[list[str], list[str]], name: str) -> tuple[list[str], list[str]]:
        return ([a.replace(name, "X") for a in pair[0]], [a.replace(name, "X") for a in pair[1]])

    golden = strip(argv(plain), "plain")
    assert golden[1][golden[1].index("--profile") + 1] == "100"
    assert strip(argv(root), "murcia") == golden  # switch on, no record yet
    capsys.readouterr()
    resolve(root, Stub(reply()))
    assert strip(argv(root), "murcia") == golden  # every id map_default
    store_street_profile(plain, False)
    assert strip(argv(plain), "plain") == golden
    store_brief(root, "thirty years on")
    resolve(root, Stub(reply(abandonment="years_30", props_profile="worn", dominant_typology="generic")))
    materials, overgrow = argv(root)
    assert overgrow[overgrow.index("--profile") + 1] == "30" and overgrow.count("--profile") == 1
    assert overgrow[-2] == "--out" and overgrow[-4:-2] == ["--street-profile", str(record_path(root))]
    assert materials[-6:-2] == ["--facade-style", "generic", "--street-profile", str(record_path(root))]


def test_resolve_profile_runs_only_with_the_switch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.pipeline_run import resolve_profile

    store_street_profile(root, False)
    resolve_profile(root, Refuse())  # type: ignore[arg-type]
    assert not record_path(root).exists()
    store_street_profile(root, True)
    resolve_profile(root, Stub(reply(abandonment="years_10", cover="off")))  # type: ignore[arg-type]
    assert record_path(root).is_file()
    assert "street profile: ai_generated, abandonment=years_10, cover=off" in capsys.readouterr().out
    resolve_profile(root, Stub("bad", "bad"))  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "street profile: replay, abandonment=years_10, cover=off" in out
    store_brief(root, "changed")
    resolve_profile(root, Stub("bad", "bad"))  # type: ignore[arg-type]
    assert "street profile: rules, all map_default (" in capsys.readouterr().out


def test_stage_parsers_take_the_new_flags() -> None:
    from onward_generator.materials.__main__ import build_parser as materials_parser
    from onward_generator.overgrow.__main__ import build_parser as overgrow_parser
    from onward_generator.perf.__main__ import build_parser as perf_parser

    cover = ["cover", "--scene", "s", "--heights", "h", "--base", "b", "--detail", "d", "--layout", "l", "--out", "o"]
    assert perf_parser().parse_args(cover).max_props is None
    assert perf_parser().parse_args(cover).street_profile is None
    for count in (0, 8, 16, 24):
        assert perf_parser().parse_args([*cover, "--max-props", str(count)]).max_props == count
    for bad in ("9", "-1", "25"):
        with pytest.raises(SystemExit):
            perf_parser().parse_args([*cover, "--max-props", bad])
    assert perf_parser().parse_args([*cover, "--street-profile", "r.json"]).street_profile == "r.json"
    place = ["place", "--base", "b", "--scene", "s", "--out", "o"]
    assert overgrow_parser().parse_args(place).street_profile is None
    assert overgrow_parser().parse_args([*place, "--street-profile", "r.json"]).street_profile == "r.json"
    apply = ["apply", "--base", "b", "--out", "o"]
    assert materials_parser().parse_args(apply).street_profile is None
    assert materials_parser().parse_args([*apply, "--street-profile", "r.json"]).street_profile == "r.json"


def test_a_stage_given_a_bad_record_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.materials.__main__ import main as materials_main
    from onward_generator.overgrow.__main__ import main as overgrow_main

    record = tmp_path / "street_profile.json"
    record.write_text("{}")
    assert materials_main(["apply", "--base", str(tmp_path), "--out", str(tmp_path / "o"), "--street-profile", str(record)]) == 1
    assert "AI_PROFILE_RECORD" in capsys.readouterr().err
    assert overgrow_main(["place", "--base", str(tmp_path), "--scene", "s", "--out", str(tmp_path / "o"),
                          "--street-profile", str(record)]) == 1
    assert "AI_PROFILE_RECORD" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ pinned values

GOLDEN_EFFECTS = [
    ('dominant_typology', 'anglo', '/materials/facade_style', 'anglo', 'materials', '--facade-style', 'the map-wide facade atlas style is anglo; no building is reclassified', 'ADR-020, ADR-027'),
    ('dominant_typology', 'central_european', '/materials/facade_style', 'central_european', 'materials', '--facade-style', 'the map-wide facade atlas style is central_european; no building is reclassified', 'ADR-020, ADR-027'),
    ('dominant_typology', 'east_african', '/materials/facade_style', 'east_african', 'materials', '--facade-style', 'the map-wide facade atlas style is east_african; no building is reclassified', 'ADR-020, ADR-027'),
    ('dominant_typology', 'generic', '/materials/facade_style', 'generic', 'materials', '--facade-style', 'the map-wide facade atlas style is generic; no building is reclassified', 'ADR-020, ADR-027'),
    ('dominant_typology', 'haussmann', '/materials/facade_style', 'haussmann', 'materials', '--facade-style', 'the map-wide facade atlas style is haussmann; no building is reclassified', 'ADR-020, ADR-027'),
    ('dominant_typology', 'latin_american', '/materials/facade_style', 'latin_american', 'materials', '--facade-style', 'the map-wide facade atlas style is latin_american; no building is reclassified', 'ADR-020, ADR-027'),
    ('dominant_typology', 'mediterranean', '/materials/facade_style', 'mediterranean', 'materials', '--facade-style', 'the map-wide facade atlas style is mediterranean; no building is reclassified', 'ADR-020, ADR-027'),
    ('palette', 'extended_pools', '/materials/repetition', True, 'materials', '--repetition', 'the calm roof and paving pools and the per-cell ground phase', 'ADR-009'),
    ('props_profile', 'intact', '/overgrow/prefer', 'config/street_profile_selection.json', 'overgrow', '--prefer', 'overgrow offers and places first the library props whose condition is intact, or that have none; the others still stand', 'ADR-010'),
    ('props_profile', 'worn', '/overgrow/prefer', 'config/street_profile_selection.json', 'overgrow', '--prefer', 'overgrow offers and places first the library props whose condition is damaged, worn, or that have none; the others still stand', 'ADR-010'),
    ('props_profile', 'wrecked', '/overgrow/prefer', 'config/street_profile_selection.json', 'overgrow', '--prefer', 'overgrow offers and places first the library props whose condition is burned, damaged, wrecked, or that have none; the others still stand', 'ADR-010'),
    ('abandonment', 'years_10', '/overgrow/profile_years', 10, 'overgrow', '--profile', 'grass, vines and trees follow the 10-year profile', 'ADR-010'),
    ('abandonment', 'years_30', '/overgrow/profile_years', 30, 'overgrow', '--profile', 'grass, vines and trees follow the 30-year profile', 'ADR-010'),
    ('abandonment', 'years_100', '/overgrow/profile_years', 100, 'overgrow', '--profile', 'grass, vines and trees follow the 100-year profile', 'ADR-010'),
    ('cover', 'off', '/cover/max_props', 0, 'cover', '--max-props', 'sightline cover places at most 0 props', 'ADR-032'),
    ('cover', 'light', '/cover/max_props', 8, 'cover', '--max-props', 'sightline cover places at most 8 props', 'ADR-032'),
    ('cover', 'full', '/cover/max_props', 24, 'cover', '--max-props', 'sightline cover places at most 24 props', 'ADR-032'),
    ('street_mood', 'abandoned', '/overgrow/street_mood', 'abandoned', 'overgrow', '--street-mood', 'the street is dressed as abandoned: growth, wrecks and every prop condition, as without a mood', 'ADR-044'),
    ('street_mood', 'emptied', '/overgrow/street_mood', 'emptied', 'overgrow', '--street-mood', 'the street is dressed as emptied days ago: no growth, fewer wrecks, intact and worn props, no soot or bullet holes', 'ADR-044'),
    ('street_mood', 'lived', '/overgrow/street_mood', 'lived', 'overgrow', '--street-mood', 'the street is dressed as in daily use: intact props, café terraces and awnings, no growth, wrecks or wall wear', 'ADR-044'),
]


def test_effects_golden() -> None:
    assert [(e.field, e.id, e.pointer, e.value, e.stage, e.flag, e.effect, e.adr) for e in EFFECTS.values()] == GOLDEN_EFFECTS


def test_inputs_and_prompt_hashes_are_pinned() -> None:
    library = [("b", "worn"), ("a", "")]
    centre = {"latitude": 37.984071234, "longitude": -1.12863}
    # The styles enter the hash, so a stored profile goes stale when a style is added (haussmann, plan Q3; east_african,
    # 14 November 2026).
    assert sp.inputs_sha256("ñ calle", centre, library, True) == "8c43b25c38d3c6b523ed9e5d62fdd296ccce93b42869dc51410abe7f65c4e95e"
    assert sp.inputs_sha256("ñ calle", centre, list(reversed(library)), True) == sp.inputs_sha256("ñ calle", centre, library, True)
    assert sp.inputs_sha256("ñ calle", centre, library, False) != sp.inputs_sha256("ñ calle", centre, library, True)
    # The system prompt lists the styles too.
    assert sp.prompt_sha256("x") == "a1a5a9a0e7d0d0aa36d13aeec4b52423740b217419b2810ff503f795a37d8f11"  # profile 0.2.0, street_mood
    # The 0.1.0 prompt a stored reply was hashed with: the value this test pinned before street_mood (17 November 2026).
    assert sp.legacy_prompt_sha256("x") == "7c138214ac3b5c4469452c9d3b518ce305fdb226a849ca11604ca5f46f3b57af"
    assert (sp._round(1.123456), sp._round(2), sp._round(True), sp._round("1"), sp._round(None)) == (1.12346, 2.0, None, None, None)


def test_instruction_golden() -> None:
    text = sp.instruction("ñ", {"latitude": 37.984071234, "longitude": -1.1}, [("b", "worn"), ("a", ""), ("c", "damaged")])
    assert text == ('Brief (quoted exactly):\n"ñ"\n\nMap centre (WGS84): 37.98407, -1.1\n\n'
                    'Library props per props_profile: {"intact": 0, "worn": 2, "wrecked": 1}\n')


def test_parse_messages_are_exact() -> None:
    def message(text: str) -> str:
        with pytest.raises(StreetProfileError) as caught:
            parse_street_profile(text)
        return caught.value.message

    assert message(" ") == "the reply is empty"
    assert message("[]") == "the reply must be a JSON object, got list"
    assert message("{") .startswith("the reply is not strict JSON (") and message("{").endswith("; only one JSON object is accepted")
    two = message(json.dumps({**NEUTRAL_REPLY, "cover": "max", "palette": "neon"})).split("; ")
    assert len(two) == 2 and two[0].startswith("/cover: 'max' is not one of") and two[1].startswith("/palette: 'neon' is not one of")
    document = dict(NEUTRAL_REPLY)
    del document["cover"]
    assert message(json.dumps(document)) == "/: 'cover' is a required property"


def test_effective_settings_golden(root: Path) -> None:
    from onward_generator.io import sha256_file

    osm = root / "data" / "raw" / "murcia.osm"
    osm.parent.mkdir(parents=True, exist_ok=True)
    osm.write_text("<osm/>")
    document = load_map(root)
    document.update(sources={"es.catastro": False}, facades={"mode": "legacy"}, core_radius_m=60.0)
    (root / "map.json").write_text(json.dumps(document))
    card = sha256_file(root / "config" / "export.json")
    assert sp.effective_settings(root) == {
        "area": {"radius_m": 150.0, "margin_m": 30.0, "crs": "EPSG:25830"},
        "center": {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON},
        "sources": {"es.catastro": False},
        "export": {"sha256": card},
        "budgets": {"export_card_sha256": card},
        "core": {"core_radius_m": 60.0, "core": None},
        "buildings_sha256": sha256_file(osm),
        "sightline_cover": False,
        "materials": {"facade_style": "auto", "repetition": False},
        "overgrow": {"profile_years": 100, "selection": None, "prefer": None, "street_mood": None},
        "cover": {"max_props": 24},
    }
    del document["sources"]
    (root / "map.json").write_text(json.dumps(document))
    assert sp.effective_settings(root)["sources"] == {}


def test_translate_leaves_its_input_alone() -> None:
    before = _before()
    translate(StreetProfile("anglo", "extended_pools", "wrecked", "years_30", "off"), before, Context(True, tuple(LIBRARY)))
    assert before == _before()


def test_paths_are_fixed(root: Path) -> None:
    assert selection_path(root) == root / "config" / "street_profile_selection.json"
    assert record_path(root) == root / "config" / "street_profile.json"
    result = resolve_street_profile(root, provider=Stub(reply()), library=None)  # type: ignore[arg-type]
    assert result.path == record_path(root) and result.origin == "ai_generated"


def test_record_golden_keys_and_hashes(root: Path) -> None:
    record = resolve(root, Stub(ProviderError("Ollama is down"), ProviderError("Ollama is down")))
    assert list(record) == [
        "schema", "schema_version", "profile_version", "prompt_version", "origin", "provenance", "attempts", "reason",
        "diagnostics", "raw", "raw_sha256", "schema_sha256", "prompt_sha256", "inputs_sha256", "ids", "summary",
        "summary_status", "provider", "changes",
    ]
    assert (record["schema"], record["schema_version"], record["profile_version"], record["prompt_version"]) == (
        "onward_generator.street_profile_record", "0.2.0", "0.2.0", "0.2.0")
    assert record["raw"] == "" and record["raw_sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert record["diagnostics"] == [{"code": "AI_PROFILE_PROVIDER", "message": "Ollama is down"}] * 2
    assert record["reason"] == "Ollama is down [AI_PROFILE_PROVIDER]"
    centre = {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON}
    assert record["inputs_sha256"] == sp.inputs_sha256("", centre, LIBRARY, False)
    assert record["prompt_sha256"] == sp.prompt_sha256(sp.instruction("", centre, LIBRARY))
    assert record["schema_sha256"] == sp.schema_sha256()
    resolve(root, Stub(reply(abandonment="years_10")))
    replay = resolve(root, Refuse())
    assert replay["reason"] is None and replay["diagnostics"] == [] and replay["provider"] == {"provider": "stub"}


def test_the_instruction_sent_is_the_one_hashed(root: Path) -> None:
    store_brief(root, "una calle abandonada")
    stub = Stub(reply())
    record = resolve(root, stub)
    centre = {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON}
    assert stub.calls == [sp.instruction("una calle abandonada", centre, LIBRARY)]
    assert record["inputs_sha256"] == sp.inputs_sha256("una calle abandonada", centre, LIBRARY, False)


def test_the_retry_text_is_exact(root: Path) -> None:
    stub = Stub("[]", reply())
    resolve(root, stub)
    assert stub.calls[1] == (stub.calls[0] + "\nThe previous reply was rejected:\n"
                             "the reply must be a JSON object, got list [AI_PROFILE_SCHEMA]\n"
                             "Reply again with one JSON object. Use only the ids listed in the system message.\n")


def test_diagnostics_are_exact(root: Path) -> None:
    resolve(root, Stub("[]", reply(abandonment="years_10")))
    assert load_record(record_path(root))["diagnostics"] == [
        {"code": "AI_PROFILE_SCHEMA", "message": "the reply must be a JSON object, got list"}]
    store_brief(root, "other")
    assert resolve(root, Stub(reply()))["diagnostics"] == [
        {"code": "AI_PROFILE_STALE", "message": "the stored reply was made for other inputs; it was archived"}]


def test_two_protected_pointers_are_listed(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = sp.translate

    def leaky(profile: StreetProfile, before: Any, context: Context) -> Any:
        after, rows = real(profile, before, context)
        after["area"], after["sources"] = "a", "b"
        return after, rows

    monkeypatch.setattr(sp, "translate", leaky)
    record = resolve(root, Stub(reply(abandonment="years_10")))
    assert record["diagnostics"] == [{"code": "AI_PROFILE_PROTECTED", "message": "AI_PROFILE_PROTECTED: the profile would change /area, /sources"}]


def _pad(path: Path, size: int) -> None:
    text = path.read_text()
    path.write_text(text + " " * (size - len(text.encode())))
    assert path.stat().st_size == size


def test_records_at_the_size_limit_are_read(root: Path) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    _pad(record_path(root), sp.MAX_RECORD_BYTES)
    assert load_record(record_path(root))["ids"]["abandonment"] == "years_10"
    assert resolve(root, Refuse())["origin"] == "replay"
    _pad(record_path(root), sp.MAX_RECORD_BYTES + 1)
    stub = Stub(reply())
    assert resolve(root, stub)["origin"] == "ai_generated" and len(stub.calls) == 1


@pytest.mark.parametrize("stored", ["[]", '{"schema": "other"}', "\xff"])
def test_a_stored_file_that_is_not_a_record_is_asked_again(root: Path, stored: str) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    document = json.loads(record_path(root).read_text())
    if stored == '{"schema": "other"}':
        record_path(root).write_text(json.dumps({**document, "schema": "other"}))
    elif stored == "\xff":
        record_path(root).write_bytes(b"\xff")
    else:
        record_path(root).write_text(stored)
    stub = Stub(reply())
    assert resolve(root, stub)["diagnostics"] == [] and len(stub.calls) == 1


def test_a_stored_record_without_a_raw_string_is_asked_again(root: Path) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    document = json.loads(record_path(root).read_text())
    record_path(root).write_text(json.dumps({**document, "raw": None}))
    stub = Stub(reply())
    assert resolve(root, stub)["origin"] == "ai_generated" and len(stub.calls) == 1


def test_the_default_provider_is_loopback_ollama(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.ai import providers

    made: list[Stub] = []

    def ollama() -> Stub:
        made.append(Stub(reply(cover="off")))
        return made[-1]

    monkeypatch.setattr(providers, "OllamaProvider", ollama)
    record = resolve_street_profile(root).record
    assert len(made) == 1 and record["ids"]["cover"] == "off" and record["origin"] == "ai_generated"


def test_the_default_library_is_assets(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(sp, "library_conditions", lambda library: seen.append(library) or [])
    resolve_street_profile(root, provider=Stub(reply()))  # type: ignore[arg-type]
    assert seen == ["assets"]


def test_calm_plan_checks_a_library_plan_with_the_extended_pools(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.materials import library_plan

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(library_plan, "assert_library_entries", lambda document, **kwargs: calls.append(kwargs))
    plan = root / "config" / "texture_plan.json"
    assert json.loads(plan.read_text())["provider"] == "library"
    assert sp._calm_plan(root) is True and calls == [{"extended": True}]
    plan.write_text(json.dumps({"provider": "urls"}))
    assert sp._calm_plan(root) is True and len(calls) == 1

    def refuse(document: Any, **kwargs: Any) -> None:
        raise library_plan.PlanError("not calm")

    monkeypatch.setattr(library_plan, "assert_library_entries", refuse)
    plan.write_text(json.dumps({"provider": "library"}))
    assert sp._calm_plan(root) is False


def test_the_selection_file_bytes(root: Path) -> None:
    resolve(root, Stub(reply(props_profile="intact")))
    assert selection_path(root).read_text() == (
        '{\n  "schema": "onward_generator.prop_selection",\n  "schema_version": "0.1.0",\n  "ids": [\n    "bin_intact",\n    "tree"\n  ]\n}\n')


@pytest.mark.parametrize(("edit", "message"), [
    (lambda d: d.update(schema="other"), "not a onward_generator.street_profile_record 0.2.0 record"),
    (lambda d: d.update(schema_version="0.1.0"), "not a onward_generator.street_profile_record 0.2.0 record"),
    (lambda d: d["ids"].update(cover="max"), "ids must name one allowed id per field"),
    (lambda d: d.update(origin="rules"), "origin and provenance do not match"),
    (lambda d: d.update(changes=[1]), "changes must be a list of objects"),
    (lambda d: d["changes"][0].update(pointer="/area"), "change '/area' is not the effect of its id"),
])
def test_load_record_messages(root: Path, edit: Any, message: str) -> None:
    resolve(root, Stub(reply(abandonment="years_10")))
    path = record_path(root)
    document = json.loads(path.read_text())
    edit(document)
    path.write_text(json.dumps(document))
    with pytest.raises(StreetProfileError) as caught:
        load_record(path)
    assert caught.value.message == f"{path}: {message}"


def test_load_record_file_messages(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    with pytest.raises(StreetProfileError) as caught:
        load_record(path)
    assert caught.value.message == f"{path}: street profile record not found"
    path.write_text(" " * (64 * 1024 + 1))
    with pytest.raises(StreetProfileError) as caught:
        load_record(path)
    assert caught.value.message == f"{path}: the record is over 65536 bytes"
    path.write_text("{")
    with pytest.raises(StreetProfileError) as caught:
        load_record(path)
    assert caught.value.message.startswith(f"{path}: not strict JSON (")


def test_flags_follow_the_live_selection_and_cover_switch(root: Path) -> None:
    store_sightline_cover(root, True)
    resolve(root, Stub(reply(props_profile="wrecked", cover="light")))
    assert set(profile_flags(root)) == {"overgrow", "cover"}
    selection_path(root).unlink()
    assert set(profile_flags(root)) == {"cover"}
    store_sightline_cover(root, False)
    assert profile_flags(root) == {}


def test_describe_is_exact(root: Path) -> None:
    result = resolve_street_profile(root, provider=Stub(reply(cover="off")), library=None)  # type: ignore[arg-type]
    assert sp.describe(result) == "street profile: ai_generated, cover=off"
    store_brief(root, "x")
    result = resolve_street_profile(root, provider=Stub("[]", "[]"), library=None)  # type: ignore[arg-type]
    assert sp.describe(result) == ("street profile: rules, all map_default "
                                   "(the reply must be a JSON object, got list [AI_PROFILE_SCHEMA])")


def _run_map(root: Path, provider: Any, street_profile: bool = True, out: str | None = None) -> int:
    from onward_generator.pipeline_run import run_map

    return run_map(
        map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30,
        out=out or str(root), execute=lambda _argv: 2, interactive=False, no_unity=True,
        street_profile=street_profile, profile_provider=provider,
    )


def test_run_on_an_existing_map_stores_the_switch_and_resolves_with_the_given_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sp, "library_conditions", lambda _library: [])
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    assert _run_map(root, Refuse(), street_profile=False) == 2
    assert "street_profile" not in load_map(root) and not record_path(root).exists()
    assert _run_map(root, Stub(reply(abandonment="years_10"))) == 2
    assert load_map(root)["street_profile"] is True
    record = load_record(record_path(root))
    assert record["origin"] == "ai_generated" and record["provider"] == {"provider": "stub"}


def test_run_on_a_new_map_stores_the_switch_and_resolves_with_the_given_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sp, "library_conditions", lambda _library: [])
    from onward_generator.pipeline_run import run_map

    def execute(argv: list[str]) -> int:
        if argv[:2] == ["map", "create"]:
            create_map(Path("maps/murcia"), map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT,
                       longitude=MURCIA_CENTER_LON, radius_m=150)
            return 0
        return 2

    code = run_map(map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150,
                   margin_m=30, out="maps/murcia", execute=execute, interactive=False, no_unity=True,
                   street_profile=True, profile_provider=Stub(reply(cover="off")))  # type: ignore[arg-type]
    root = tmp_path / "maps" / "murcia"
    assert code == 2 and load_map(root)["street_profile"] is True
    record = load_record(record_path(root))
    assert record["origin"] == "ai_generated" and record["ids"]["cover"] == "off"


def test_the_run_command_passes_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.cli import main

    received: dict[str, object] = {}

    def fake_run_map(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("onward_generator.pipeline_run.run_map", fake_run_map)
    base = ["run", "--name", "Murcia", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON)]
    assert main(base) == 0 and received["street_profile"] is False
    assert main([*base, "--street-profile"]) == 0 and received["street_profile"] is True


def test_a_map_without_a_brief_hashes_an_empty_one(root: Path) -> None:
    document = load_map(root)
    del document["brief"]
    (root / "map.json").write_text(json.dumps(document))
    stub = Stub(reply())
    record = resolve(root, stub)
    centre = {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON}
    assert record["inputs_sha256"] == sp.inputs_sha256("", centre, LIBRARY, False)
    assert stub.calls == [sp.instruction("", centre, LIBRARY)] and '\n""\n' in stub.calls[0]


# ------------------------------------------------------------------------------------------------ street mood (plan increment 4)


def test_a_map_json_mood_wins_over_the_profile(root: Path) -> None:
    _set_mood(root, "emptied")
    record = resolve(root, Stub(reply(street_mood="lived")))
    assert record["ids"]["street_mood"] == "lived"
    assert [(row["status"], row["reason"], row["before"], row["after"]) for row in record["changes"]] == [
        ("skipped", "user_setting", "emptied", "emptied")]
    assert profile_flags(root) == {} and sp.applied_street_mood(root) is None


def test_applied_street_mood_reads_only_an_applied_row(root: Path) -> None:
    assert sp.applied_street_mood(None) is None and sp.applied_street_mood(root) is None  # no record yet
    resolve(root, Stub(reply(abandonment="years_10")))
    assert sp.applied_street_mood(root) is None  # a record without the field
    store_brief(root, "a busy shopping street")  # other inputs: the model is asked again
    resolve(root, Stub(reply(abandonment="years_10", street_mood="lived")))
    assert sp.applied_street_mood(str(root)) == "lived"
    store_street_profile(root, False)
    assert sp.applied_street_mood(root) is None
    store_street_profile(root, True)
    record_path(root).write_text("{broken")
    assert sp.applied_street_mood(root) is None


def test_the_legacy_schema_is_the_one_0_1_0_shipped() -> None:
    legacy = Path(__file__).resolve().parents[1] / "fixtures" / "street_profile" / "schema_0.1.0.json"
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == sp.LEGACY_SCHEMA_SHA256 != sp.schema_sha256()
    assert json.loads(legacy.read_text())["properties"]["profile_version"] == {"const": sp.LEGACY_PROFILE_VERSION}


LEGACY_REPLY = json.dumps({"profile_version": "0.1.0", "dominant_typology": "anglo", "palette": "map_default",
                           "props_profile": "map_default", "abandonment": "years_10", "cover": "map_default", "summary": "Old."})


def _legacy_record(root: Path, **edits: Any) -> dict[str, Any]:
    """The record profile 0.1.0 wrote for ``LEGACY_REPLY``: its raw reply and hashes, without street_mood."""
    first = resolve(root, Stub(reply(dominant_typology="anglo", abandonment="years_10")))
    centre = {"latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON}
    document = {
        **first, "schema_version": "0.1.0", "profile_version": "0.1.0", "prompt_version": "0.1.0",
        "raw": LEGACY_REPLY, "raw_sha256": sp._sha(LEGACY_REPLY), "schema_sha256": sp.LEGACY_SCHEMA_SHA256,
        "prompt_sha256": sp.legacy_prompt_sha256(sp.instruction("", centre, LIBRARY)),
        "ids": {key: value for key, value in first["ids"].items() if key != "street_mood"}, **edits,
    }
    record_path(root).write_text(json.dumps(document))
    return document


def test_a_stored_0_1_0_reply_is_carried_forward_without_asking(root: Path) -> None:
    legacy = _legacy_record(root)
    record = resolve(root, Refuse())
    assert record["origin"] == "replay" and record["attempts"] == 0 and record["reason"] is None
    assert record["diagnostics"] == [{"code": "AI_PROFILE_MIGRATED",
                                      "message": "the stored 0.1.0 reply was carried to 0.2.0 with street_mood map_default"}]
    assert record["ids"] == {"dominant_typology": "anglo", "palette": "map_default", "props_profile": "map_default",
                             "abandonment": "years_10", "cover": "map_default", "street_mood": "map_default"}
    assert record["summary"] == "Old." and record["raw"] == legacy["raw"] and record["raw_sha256"] == legacy["raw_sha256"]
    assert (record["schema_version"], record["profile_version"], record["schema_sha256"]) == ("0.2.0", "0.2.0", sp.schema_sha256())
    assert record["inputs_sha256"] == legacy["inputs_sha256"] and record["prompt_sha256"] != legacy["prompt_sha256"]
    assert [row["field"] for row in record["changes"]] == ["dominant_typology", "abandonment"]
    assert load_record(record_path(root))["ids"] == record["ids"]
    assert not list(record_path(root).parent.glob("street_profile.*.json"))  # nothing archived: the reply still stands
    again = resolve(root, Refuse())  # now a plain 0.2.0 replay of the same 0.1.0 text
    assert again["origin"] == "replay" and again["diagnostics"] == [] and again["ids"] == record["ids"]


@pytest.mark.parametrize("edits", [
    {"schema_sha256": "0" * 64}, {"prompt_sha256": "0" * 64}, {"inputs_sha256": "0" * 64}, {"profile_version": "0.2.0"},
    {"origin": "rules"},
], ids=["schema", "prompt", "inputs", "version", "rules"])
def test_a_0_1_0_record_that_does_not_match_is_asked_again(root: Path, edits: dict[str, Any]) -> None:
    _legacy_record(root, **edits)
    stub = Stub(reply(street_mood="lived"))
    record = resolve(root, stub)
    assert len(stub.calls) == 1 and record["origin"] == "ai_generated" and record["ids"]["street_mood"] == "lived"
    assert "AI_PROFILE_MIGRATED" not in {d["code"] for d in record["diagnostics"]}


def test_only_a_stored_reply_is_carried_forward() -> None:
    with pytest.raises(StreetProfileError, match="profile_version"):
        parse_street_profile(LEGACY_REPLY)
    carried = parse_street_profile(LEGACY_REPLY, stored=True)
    assert (carried.dominant_typology, carried.abandonment, carried.street_mood, carried.summary) == ("anglo", "years_10", "map_default", "Old.")
    assert parse_street_profile(reply(street_mood="lived"), stored=True).street_mood == "lived"
    with pytest.raises(StreetProfileError, match="street_mood"):  # a 0.2.0 reply without the field is not completed
        parse_street_profile(json.dumps({**NEUTRAL_REPLY, "street_mood": None}), stored=True)
