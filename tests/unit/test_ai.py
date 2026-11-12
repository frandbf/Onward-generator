"""Phase 06: intent parsing, planning rules and the Ollama provider (no real model needed)."""

from __future__ import annotations

import argparse
import copy
import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from onward_generator.ai import __main__ as ai_main
from onward_generator.ai import providers
from onward_generator.ai.intent import IntentError, intent_schema, parse_intent
from onward_generator.ai.planner import (
    ALLOWED_CHANGES,
    PROTECTED_FIELDS,
    allowed_residential_levels,
    build_plan,
    guard,
    instruction_warnings,
    load_base,
    output_documents,
)
from onward_generator.ai.providers import (
    OllamaProvider,
    ProviderError,
    check_loopback,
    context_window,
    default_ollama_host,
)
from onward_generator.config.models import GeneratorConfig
from tests.conftest import MURCIA_CENTER

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "config" / "murcia-belluga.example.json"
EXAMPLE = ("Genera una zona urbana histórica con edificios de cuatro plantas, fachadas detalladas, interiores solo "
           "en dos edificios y prioridad máxima al rendimiento en Quest.")


def intent_doc(**params: Any) -> dict[str, Any]:
    p = {"facade_detail": None, "residential_levels": None, "interior_buildings": None, "performance_priority": None}
    p.update(params)
    return {"intent_version": "0.1.0", "parameters": p, "unsupported_requests": [], "summary": "test"}


def text(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False)


def codes(exc_or_diags: Any) -> list[str]:
    diags = exc_or_diags.diagnostics if hasattr(exc_or_diags, "diagnostics") else exc_or_diags
    return [d.code for d in diags]


@pytest.fixture(scope="module")
def base():  # type: ignore[no-untyped-def]
    return load_base(PROJECT)


def test_base_project_is_the_murcia_reference_point(base) -> None:  # type: ignore[no-untyped-def]
    cfg = GeneratorConfig.from_document(base.project.document, base.project.path)
    assert cfg.area.center == MURCIA_CENTER


# --- correct responses -----------------------------------------------------------------------------------------

def test_parse_correct_response() -> None:
    doc = intent_doc(facade_detail="high", residential_levels=4, interior_buildings=2, performance_priority="max")
    intent = parse_intent(text(doc))
    assert (intent.facade_detail, intent.residential_levels, intent.interior_buildings, intent.performance_priority) == (
        "high", 4, 2, "max")
    assert intent.requested == doc["parameters"]
    assert intent.document == doc


def test_whitespace_around_json_is_accepted() -> None:
    assert parse_intent("\n  " + text(intent_doc()) + "\n").requested == {}


def test_code_in_strings_is_data_and_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    payload = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    doc = intent_doc()
    doc["summary"] = payload
    doc["unsupported_requests"] = [{"request": payload[:200], "reason": "not_supported"}]
    intent = parse_intent(text(doc))
    assert intent.summary == payload
    assert not marker.exists()


# --- incomplete responses --------------------------------------------------------------------------------------

@pytest.mark.parametrize("drop", ["parameters", "summary", "unsupported_requests", "intent_version"])
def test_missing_top_level_field_is_incomplete(drop: str) -> None:
    doc = intent_doc()
    del doc[drop]
    with pytest.raises(IntentError) as exc:
        parse_intent(text(doc))
    assert codes(exc.value) == ["intent_required"]
    assert "incomplete response" in str(exc.value) and drop in str(exc.value)


def test_missing_parameter_is_incomplete() -> None:
    doc = intent_doc()
    del doc["parameters"]["residential_levels"]
    with pytest.raises(IntentError) as exc:
        parse_intent(text(doc))
    assert exc.value.diagnostics[0].path == "/parameters"
    assert "residential_levels" in str(exc.value)


def test_truncated_json_is_rejected() -> None:
    with pytest.raises(IntentError) as exc:
        parse_intent(text(intent_doc())[:-10])
    assert codes(exc.value) == ["invalid_json"]


@pytest.mark.parametrize("raw", ["", "   \n"])
def test_empty_response(raw: str) -> None:
    with pytest.raises(IntentError) as exc:
        parse_intent(raw)
    assert codes(exc.value) == ["empty_response"]


# --- invalid responses -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "```json\n" + text(intent_doc()) + "\n```",
    "Here is the configuration: " + text(intent_doc()),
    "{'intent_version': '0.1.0'}",
])
def test_prose_fences_and_python_literals_are_not_json(raw: str) -> None:
    with pytest.raises(IntentError) as exc:
        parse_intent(raw)
    assert codes(exc.value) == ["invalid_json"]
    assert "without prose or code fences" in str(exc.value)


def test_duplicate_keys_are_rejected() -> None:
    raw = text(intent_doc(residential_levels=4))[:-1] + ', "summary": "again"}'
    with pytest.raises(IntentError) as exc:
        parse_intent(raw)
    assert codes(exc.value) == ["invalid_json"]


def test_nan_is_rejected() -> None:
    raw = text(intent_doc(residential_levels=4)).replace('"residential_levels": 4', '"residential_levels": NaN')
    with pytest.raises(IntentError) as exc:
        parse_intent(raw)
    assert codes(exc.value) == ["invalid_json"]


@pytest.mark.parametrize("raw", ["[]", "42", '"text"', "null"])
def test_root_must_be_an_object(raw: str) -> None:
    with pytest.raises(IntentError) as exc:
        parse_intent(raw)
    assert codes(exc.value) == ["invalid_root"]


@pytest.mark.parametrize(("mutate", "code", "path"), [
    (lambda d: d["parameters"].update(residential_levels=60), "intent_maximum", "/parameters/residential_levels"),
    (lambda d: d["parameters"].update(residential_levels=0), "intent_minimum", "/parameters/residential_levels"),
    (lambda d: d["parameters"].update(interior_buildings=-1), "intent_minimum", "/parameters/interior_buildings"),
    (lambda d: d["parameters"].update(residential_levels=True), "intent_type", "/parameters/residential_levels"),
    (lambda d: d["parameters"].update(residential_levels=4.5), "intent_type", "/parameters/residential_levels"),
    (lambda d: d["parameters"].update(residential_levels="4"), "intent_type", "/parameters/residential_levels"),
    (lambda d: d["parameters"].update(facade_detail="ultra"), "intent_enum", "/parameters/facade_detail"),
    (lambda d: d["parameters"].update(performance_priority="min"), "intent_enum", "/parameters/performance_priority"),
    (lambda d: d.update(intent_version="9.9.9"), "intent_const", "/intent_version"),
    (lambda d: d.update(summary="x" * 601), "intent_maxLength", "/summary"),
    (lambda d: d["unsupported_requests"].append({"request": "x", "reason": "because"}), "intent_enum",
     "/unsupported_requests/0/reason"),
])
def test_values_outside_the_schema(mutate: Any, code: str, path: str) -> None:
    doc = intent_doc()
    mutate(doc)
    with pytest.raises(IntentError) as exc:
        parse_intent(text(doc))
    assert (exc.value.diagnostics[0].code, exc.value.diagnostics[0].path) == (code, path)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(area={"center": {"latitude": 40.4168, "longitude": -3.7038}}),
    lambda d: d["parameters"].update(boundary_file="madrid.geojson"),
    lambda d: d["parameters"].update(export={"enabled": True}),
    lambda d: d["parameters"].update(budget={"max_triangles": 10**7}),
    lambda d: d.update(code="import os"),
])
def test_protected_or_unknown_fields_cannot_be_expressed(mutate: Any) -> None:
    doc = intent_doc()
    mutate(doc)
    with pytest.raises(IntentError) as exc:
        parse_intent(text(doc))
    assert codes(exc.value) == ["intent_additionalProperties"]
    assert "unknown field" in str(exc.value)


def test_every_problem_is_reported() -> None:
    doc = intent_doc(residential_levels=99, facade_detail="ultra")
    del doc["summary"]
    with pytest.raises(IntentError) as exc:
        parse_intent(text(doc))
    assert sorted(codes(exc.value)) == ["intent_enum", "intent_maximum", "intent_required"]


def test_oversized_response() -> None:
    with pytest.raises(IntentError) as exc:
        parse_intent(" " * (64 * 1024 + 1))
    assert codes(exc.value) == ["response_too_large"]


def test_schema_has_no_protected_fields() -> None:
    props = set(intent_schema()["properties"]["parameters"]["properties"])
    assert props == {"facade_detail", "residential_levels", "interior_buildings", "performance_priority"}
    assert intent_schema()["additionalProperties"] is False


# --- planner ---------------------------------------------------------------------------------------------------

def plan_for(base, instruction: str = "", **params: Any):  # type: ignore[no-untyped-def]
    return build_plan(parse_intent(text(intent_doc(**params))), base, instruction)


def test_example_instruction_maps_without_geometry_changes(base) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, EXAMPLE, facade_detail="high", residential_levels=4, interior_buildings=2,
                    performance_priority="max")
    assert plan.valid
    assert {(c.document, c.path) for c in plan.changes} == {("project", "/quality/detail_level"),
                                                           ("project", "/generation/interiors"),
                                                           ("detail", "/interiors/max_buildings"),
                                                           ("detail", "/interiors/min_buildings")}
    assert all(c.effect == "recorded_only" or c.cost == "decrease" for c in plan.changes)
    assert (plan.documents["detail"]["interiors"]["min_buildings"], plan.documents["detail"]["interiors"]["max_buildings"]) == (2, 2)
    assert "instruction_mentions_unsupported" in codes(plan.diagnostics)
    assert plan.needs_confirmation


def test_default_levels_range_comes_from_the_heights_config(base) -> None:  # type: ignore[no-untyped-def]
    assert allowed_residential_levels(base.heights.document) == (3, 5)


@pytest.mark.parametrize("levels", [2, 6, 50])
def test_levels_outside_the_effective_range_are_errors(base, levels: int) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, residential_levels=levels)
    assert not plan.valid
    assert "levels_outside_allowed_range" in codes(plan.diagnostics)
    assert not plan.changes


def test_cost_increase_is_refused_under_max_priority(base) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, residential_levels=5, performance_priority="max")
    assert not plan.valid
    assert codes([d for d in plan.diagnostics if d.severity == "error"]) == ["exceeds_measured_configuration"]


@pytest.mark.parametrize("priority", ["balanced", None])
def test_cost_increase_is_a_warning_otherwise(base, priority: str | None) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, residential_levels=5, interior_buildings=10, performance_priority=priority)
    assert plan.valid and plan.needs_confirmation
    assert codes(plan.diagnostics).count("not_measured_on_device") == 1  # the storeys; a ceiling of 10 lowers the default 1000
    assert plan.documents["heights"]["typical_levels"]["residential_multi"] == 5
    assert plan.documents["detail"]["interiors"]["max_buildings"] == 10
    assert plan.documents["detail"]["interiors"]["min_buildings"] == 10
    assert base.detail.document["interiors"]["min_buildings"] == 1000


@pytest.mark.parametrize("ceiling", [1, 4, 5])
def test_a_ceiling_under_the_floor_lowers_the_floor(base, ceiling: int) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, interior_buildings=ceiling)
    assert plan.valid
    interiors = plan.documents["detail"]["interiors"]
    assert (interiors["min_buildings"], interiors["max_buildings"]) == (ceiling, ceiling)
    lowered = [c for c in plan.changes if c.path == "/interiors/min_buildings"]
    assert [c.to_dict() for c in lowered] == [{
        "document": "detail", "path": "/interiors/min_buildings", "before": 1000, "after": ceiling, "effect": "geometry",
        "cost": "decrease", "reason": "interior_buildings; the floor of the draw may not exceed the ceiling"}]


def test_cost_decrease_is_allowed_under_max_but_still_confirmed(base) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, residential_levels=3, interior_buildings=0, performance_priority="max")
    assert plan.valid
    assert {c.cost for c in plan.changes if c.effect == "geometry"} == {"decrease"}
    assert plan.documents["detail"]["interiors"] == {**base.detail.document["interiors"], "max_buildings": 0,
                                                     "enabled": False}
    assert plan.needs_confirmation


def test_unchanged_values_are_not_changes(base) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, residential_levels=4, facade_detail="low")
    assert not plan.changes
    assert not plan.needs_confirmation


def test_empty_intent_changes_nothing(base) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base)
    assert plan.valid and not plan.changes and not plan.needs_confirmation
    assert codes(plan.diagnostics) == ["no_parameters"]
    assert plan.documents == {n: b.document for n, b in base.by_name().items()}


def test_base_documents_are_not_mutated(base) -> None:  # type: ignore[no-untyped-def]
    before = copy.deepcopy({n: b.document for n, b in base.by_name().items()})
    plan_for(base, residential_levels=5, interior_buildings=0, facade_detail="high")
    assert {n: b.document for n, b in base.by_name().items()} == before


def test_reported_protected_request_is_a_warning(base) -> None:  # type: ignore[no-untyped-def]
    doc = intent_doc()
    doc["unsupported_requests"] = [{"request": "move to Madrid", "reason": "protected_field"}]
    plan = build_plan(parse_intent(text(doc)), base)
    assert plan.valid and plan.needs_confirmation
    assert "protected_request_ignored" in codes(plan.diagnostics)


@pytest.mark.parametrize("pointer", sorted(f"{doc}:{p}" for doc, paths in PROTECTED_FIELDS.items() for p in paths))
def test_guard_catches_any_protected_change(base, pointer: str) -> None:  # type: ignore[no-untyped-def]
    doc, path = pointer.split(":", 1)
    docs = {n: copy.deepcopy(b.document) for n, b in base.by_name().items()}
    *parents, last = path.strip("/").split("/")
    node = docs[doc]
    for part in parents:
        node = node[part]
    node[last] = "tampered"
    found = guard(base, docs)
    assert found and all(d.code == "protected_change" for d in found)
    assert all(d.path.startswith(f"{doc}:{path}") for d in found)


def test_guard_allows_only_the_allowlist(base) -> None:  # type: ignore[no-untyped-def]
    docs = {n: copy.deepcopy(b.document) for n, b in base.by_name().items()}
    docs["project"]["quality"]["detail_level"] = "high"
    docs["heights"]["typical_levels"]["residential_multi"] = 5
    docs["detail"]["interiors"]["max_buildings"] = 0
    assert guard(base, docs) == []
    docs["project"]["area"]["center"]["latitude"] = MURCIA_CENTER[1] + 0.01
    assert codes(guard(base, docs)) == ["protected_change"]
    assert ALLOWED_CHANGES["project"].isdisjoint(PROTECTED_FIELDS["project"])


@pytest.mark.parametrize(("instruction", "topic"), [
    (f"Centra el mapa en {MURCIA_CENTER[1]:.5f}, {MURCIA_CENTER[0]:.5f}", "coordinates"),
    ("cambia la latitud", "coordinates"),
    ("usa Google como fuente de datos", "data sources"),
    ("use OSM instead of Catastro", "data sources"),
    ("exporta a FBX", "export adapter"),
    ("sube el presupuesto a 500 draw calls", "performance limits"),
])
def test_instruction_mentioning_protected_topics_warns(instruction: str, topic: str) -> None:
    found = instruction_warnings(instruction)
    assert any(d.code == "instruction_mentions_protected" and topic in d.message for d in found)


def test_performance_priority_is_not_a_protected_topic() -> None:
    assert [d.code for d in instruction_warnings("prioridad máxima al rendimiento en Quest")] == []


@pytest.mark.parametrize("instruction", ["zona urbana histórica", "estilo barroco", "de noche con lluvia"])
def test_unsupported_wishes_warn_even_if_the_model_forgets(instruction: str) -> None:
    assert "instruction_mentions_unsupported" in codes(instruction_warnings(instruction))


def test_output_documents_keep_the_same_area_and_files(base, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    plan = plan_for(base, facade_detail="high")
    out = tmp_path / "deep" / "out"
    out.mkdir(parents=True)
    docs = output_documents(plan, base, out)
    original = GeneratorConfig.from_document(base.project.document, base.project.path)
    written = GeneratorConfig.from_document(docs["project"], out / "project.json")
    assert written.area == original.area
    assert (out / docs["project"]["$schema"]).resolve() == (PROJECT.parent / "generator.schema.json").resolve()
    assert docs["project"]["quality"]["detail_level"] == "high"


# --- providers -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("url", "expected"), [
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
    ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://127.0.0.1", "http://127.0.0.1:11434"),
    ("http://[::1]:8080", "http://[::1]:8080"),
])
def test_loopback_hosts_are_accepted(url: str, expected: str) -> None:
    assert check_loopback(url) == expected


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:11434", "http://192.168.1.10:11434", "http://10.0.0.1:11434", "http://api.openai.com",
    "http://example.com:11434", "http://user:pw@127.0.0.1:11434", "http://127.0.0.1:11434/api", "ftp://127.0.0.1",
    "127.0.0.1:11434",
])
def test_non_local_or_malformed_hosts_are_refused(url: str) -> None:
    with pytest.raises(ProviderError):
        check_loopback(url)


def _addresses(*ips: str) -> Any:
    def fake(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        assert (host, port, args, kwargs) == ("host.docker.internal", 8080, (), {"proto": socket.IPPROTO_TCP})
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    return fake


@pytest.fixture
def in_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / ".dockerenv"
    marker.touch()
    monkeypatch.setattr(providers, "DOCKERENV", marker)


def test_the_container_host_is_pinned_to_its_private_address(in_container: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addresses("192.168.65.254", "192.168.65.254"))
    assert check_loopback("http://host.docker.internal:8080/") == "http://192.168.65.254:8080"


def test_the_container_host_takes_the_lowest_of_several_private_addresses(in_container: None,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addresses("192.168.65.254", "10.0.0.2"))
    assert check_loopback("http://host.docker.internal:8080") == "http://10.0.0.2:8080"


def test_the_container_host_keeps_the_default_port(in_container: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        assert (port, kwargs) == (11434, {"proto": socket.IPPROTO_TCP})
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.65.254", port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert check_loopback("http://host.docker.internal") == "http://192.168.65.254:11434"


@pytest.mark.parametrize(("ips", "shown"), [(("8.8.8.8",), "8.8.8.8"),
                                            (("8.8.8.8", "192.168.65.254"), "192.168.65.254, 8.8.8.8")])
def test_the_container_host_is_refused_when_it_resolves_to_a_public_address(
        in_container: None, monkeypatch: pytest.MonkeyPatch, ips: tuple[str, ...], shown: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addresses(*ips))
    with pytest.raises(ProviderError) as caught:
        check_loopback("http://host.docker.internal:8080")
    assert str(caught.value) == (f"refusing Ollama host 'host.docker.internal': it resolves to {shown}, "
                                 "not only to private addresses")


def test_the_container_host_is_refused_when_it_resolves_to_nothing(in_container: None,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addresses())
    with pytest.raises(ProviderError) as caught:
        check_loopback("http://host.docker.internal:8080")
    assert str(caught.value) == ("refusing Ollama host 'host.docker.internal': it resolves to nothing, "
                                 "not only to private addresses")


def test_the_container_host_is_refused_when_it_does_not_resolve(in_container: None,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(ProviderError) as caught:
        check_loopback("http://host.docker.internal:8080")
    assert str(caught.value) == "cannot resolve 'host.docker.internal' (no such host): it needs Docker Desktop"


def test_the_container_host_is_refused_outside_a_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "DOCKERENV", tmp_path / ".dockerenv")
    monkeypatch.setattr(socket, "getaddrinfo", _addresses("192.168.65.254"))
    with pytest.raises(ProviderError) as caught:
        check_loopback("http://host.docker.internal:8080")
    assert str(caught.value) == ("refusing Ollama host 'host.docker.internal' outside a container: "
                                 "on this machine use http://127.0.0.1:11434")


@pytest.mark.parametrize("url", ["http://docker.internal:11434", "http://host.docker.internal.example.com:11434",
                                 "https://host.docker.internal:11434", "http://gateway.docker.internal:11434"])
def test_only_the_exact_container_host_is_special(in_container: None, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addresses("192.168.65.254"))
    with pytest.raises(ProviderError, match="only loopback|must look like"):
        check_loopback(url)


def test_a_non_local_host_is_refused_with_the_reason() -> None:
    with pytest.raises(ProviderError) as caught:
        check_loopback("http://192.168.1.10:11434")
    assert str(caught.value) == ("refusing non-local Ollama host '192.168.1.10': only loopback addresses are allowed "
                                 "(external AI providers are disabled)")


def test_the_default_host_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONWARD_OLLAMA_HOST", raising=False)
    assert default_ollama_host() == "http://127.0.0.1:11434"
    assert OllamaProvider().host == "http://127.0.0.1:11434"
    monkeypatch.setenv("ONWARD_OLLAMA_HOST", "")
    assert default_ollama_host() == "http://127.0.0.1:11434"
    monkeypatch.setenv("ONWARD_OLLAMA_HOST", "http://[::1]:9999")
    assert default_ollama_host() == "http://[::1]:9999"
    assert OllamaProvider().host == "http://[::1]:9999"
    monkeypatch.setenv("ONWARD_OLLAMA_HOST", "http://example.com:11434")
    with pytest.raises(ProviderError, match="only loopback"):
        OllamaProvider()


def test_ai_plan_host_defaults_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    def host_action() -> argparse.Action:
        sub = next(a for a in ai_main.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        return next(a for a in sub.choices["plan"]._actions if a.dest == "host")
    assert host_action().default == "http://127.0.0.1:11434"
    assert host_action().help == "loopback only; default $ONWARD_OLLAMA_HOST or http://127.0.0.1:11434"
    monkeypatch.setenv("ONWARD_OLLAMA_HOST", "http://[::1]:9999")
    assert host_action().default == "http://[::1]:9999"


class FakeOllama(BaseHTTPRequestHandler):
    tags: dict[str, Any] = {}
    chat: dict[str, Any] | bytes = {}
    chat_status = 200
    requests: list[dict[str, Any]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler names it
        body = {"/api/version": {"version": "0.0.test"}, "/api/tags": self.tags}.get(self.path)
        self._send(200 if body is not None else 404, json.dumps(body or {"error": "not found"}).encode())

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler names it
        length = int(self.headers["Content-Length"])
        type(self).requests.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        chat = self.chat if isinstance(self.chat, bytes) else json.dumps(self.chat).encode()
        self._send(self.chat_status, chat)


@pytest.fixture
def fake_ollama() -> Iterator[tuple[str, type[FakeOllama]]]:
    handler = type("Handler", (FakeOllama,), {
        "tags": {"models": [{"name": "qwen3:14b", "model": "qwen3:14b", "digest": "abc123",
                             "details": {"parameter_size": "14.8B", "quantization_level": "Q4_K_M", "family": "qwen3"}}]},
        "chat": {"message": {"role": "assistant", "content": text(intent_doc(residential_levels=4))},
                 "done_reason": "stop", "eval_count": 42, "prompt_eval_count": 17, "total_duration": 123},
        "chat_status": 200,
        "requests": [],
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # shutdown() waits for serve_forever to poll again; the default 0.5 s poll cost each test half a second.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        server.server_close()


def test_ollama_provider_sends_schema_and_records_provenance(fake_ollama) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    result = OllamaProvider(host=host, seed=7, timeout_s=5).complete("SYSTEM", EXAMPLE, intent_schema())
    assert parse_intent(result.text).residential_levels == 4
    (req,) = handler.requests
    assert req["path"] == "/api/chat"
    body = req["body"]
    assert body["model"] == "qwen3:14b" and body["format"] == intent_schema()
    assert body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0, "seed": 7, "num_ctx": 8192}
    assert body["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": EXAMPLE}]
    p = result.provenance
    assert (p["model"], p["model_digest"], p["server_version"], p["quantization"]) == ("qwen3:14b", "abc123",
                                                                                      "0.0.test", "Q4_K_M")
    assert (p["eval_count"], p["prompt_eval_count"], p["done_reason"]) == (42, 17, "stop")
    assert p["options"]["num_ctx"] == 8192


def test_ollama_context_fits_a_long_prompt(fake_ollama) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    OllamaProvider(host=host, seed=7, timeout_s=5).complete("S" * 43_000, EXAMPLE, intent_schema())
    (req,) = handler.requests
    assert req["body"]["options"]["num_ctx"] == 20480


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (("",), 8192),
        (("a" * 12_288,), 8192),
        (("a" * 12_291,), 12288),
        (("a" * 6_000, "b" * 6_291), 12288),
        (("é" * 6_146,), 12288),
        (("a" * 86_016,), 32768),
    ],
)
def test_context_window_rounds_the_prompt_up(texts: tuple[str, ...], expected: int) -> None:
    assert context_window(*texts) == expected


def test_context_window_refuses_a_prompt_that_cannot_fit(fake_ollama) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ProviderError, match=r"^prompt needs about 32769 tokens of context; the limit is 32768$"):
        context_window("a" * 86_019)
    host, handler = fake_ollama
    with pytest.raises(ProviderError, match="limit is 32768"):
        OllamaProvider(host=host, timeout_s=5).complete("a" * 86_019, "", intent_schema())
    assert handler.requests == []


def test_missing_model_is_explained_and_not_downloaded(fake_ollama) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    with pytest.raises(ProviderError, match="not installed.*nothing is downloaded"):
        OllamaProvider(model="llama9:1t", host=host, timeout_s=5).complete("S", "I", intent_schema())
    assert handler.requests == []


def test_http_error_is_reported(fake_ollama) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    handler.chat_status, handler.chat = 500, {"error": "model crashed"}
    with pytest.raises(ProviderError, match="HTTP 500.*model crashed"):
        OllamaProvider(host=host, timeout_s=5).complete("S", "I", intent_schema())


def test_reply_without_content_is_reported(fake_ollama) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    handler.chat = {"message": {"role": "assistant"}}
    with pytest.raises(ProviderError, match="no message content"):
        OllamaProvider(host=host, timeout_s=5).complete("S", "I", intent_schema())


def test_oversized_reply_is_refused(fake_ollama, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    monkeypatch.setattr(providers, "MAX_HTTP_BYTES", 256)
    handler.chat = b"{" + b" " * 1024 + b"}"
    with pytest.raises(ProviderError, match="exceeds 256 bytes"):
        OllamaProvider(host=host, timeout_s=5).complete("S", "I", intent_schema())


def test_unreachable_ollama_suggests_the_no_ai_mode() -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(ProviderError, match="cannot reach Ollama.*--provider none"):
        OllamaProvider(host=f"http://127.0.0.1:{port}", timeout_s=2).complete("S", "I", intent_schema())


def test_remote_host_is_refused_before_any_request() -> None:
    with pytest.raises(ProviderError, match="refusing non-local"):
        OllamaProvider(host="http://8.8.8.8:11434")
