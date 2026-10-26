"""Phase 06 end to end: instruction → intent → confirmed, reproducible configuration consumed by the real stages.

Runs without a model (``--provider none`` / ``replay``) and with a fake local Ollama server; the base project is
the Murcia example centred on MURCIA_CENTER.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from onward_generator.cli import main
from onward_generator.config.models import GeneratorConfig
from tests.conftest import MURCIA_CENTER
from tests.unit.test_ai import EXAMPLE, fake_ollama, intent_doc  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[2]
CONFIGS = ("project.json", "heights.json", "detail.json", "intent.json")


def write_intent(path: Path, **params: Any) -> Path:
    path.write_text(json.dumps(intent_doc(**params)), encoding="utf-8")
    return path


def plan(*args: str) -> int:
    return main(["ai", "plan", *args])


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            if (hit := find(v, key)) is not None:
                return hit
    return None


def test_relevant_change_needs_confirmation_then_replays_identically(tmp_path: Path) -> None:
    intent = write_intent(tmp_path / "intent.json", residential_levels=5, interior_buildings=0,
                          performance_priority="balanced")
    out = tmp_path / "out"
    assert plan("--provider", "none", "--intent", str(intent), "--instruction", EXAMPLE, "--out", str(out)) == 3
    assert sorted(p.name for p in out.iterdir()) == ["proposal.json"]
    proposal = load(out / "proposal.json")
    assert proposal["status"] == "pending_confirmation" and proposal["confirmed"] is False
    assert {c["path"] for c in proposal["changes"] if c["effect"] == "geometry"} == {
        "/typical_levels/residential_multi", "/interiors/max_buildings", "/interiors/enabled"}

    assert plan("--provider", "replay", "--replay", str(out / "proposal.json"), "--confirm", "--out", str(out)) == 0
    run = load(out / "ai_run.json")
    assert run["status"] == "written" and run["confirmed"] is True
    assert run["replay_of"]["sha256"] == sha(out / "proposal.json")
    assert run["raw_response"] == proposal["raw_response"] and run["intent"] == proposal["intent"]
    assert run["outputs"] == {name: sha(out / name) for name in CONFIGS}
    assert run["intent_origin"]["parameters"] == "user_supplied"

    again = tmp_path / "again"
    assert plan("--provider", "replay", "--replay", str(out / "proposal.json"), "--confirm", "--out", str(again)) == 0
    for name in CONFIGS:
        assert (out / name).read_bytes() == (again / name).read_bytes(), name


def test_outputs_keep_the_area_and_validate(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "out"
    intent = write_intent(tmp_path / "intent.json", facade_detail="high")
    assert plan("--provider", "none", "--intent", str(intent), "--out", str(out)) == 0
    assert main(["validate-config", str(out / "project.json")]) == 0
    written = load(out / "project.json")
    cfg = GeneratorConfig.from_document(written, out / "project.json")
    assert cfg.area.center == MURCIA_CENTER
    base = load(REPO / "config" / "murcia-belluga.example.json")
    assert (out / written["area"]["boundary_file"]).resolve() == (
        REPO / "config" / base["area"]["boundary_file"]).resolve()
    for key in ("source", "export", "optimization", "project"):
        assert written[key] == base[key], key
    assert written["quality"]["detail_level"] == "high"


def test_generated_configs_drive_the_existing_stages(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    scene, heights, base = detail_inputs
    intent = write_intent(tmp_path / "intent.json", residential_levels=5, interior_buildings=0)
    out = tmp_path / "cfg"
    assert plan("--provider", "none", "--intent", str(intent), "--confirm", "--out", str(out)) == 0

    assert main(["heights", "--scene", str(scene), "--config", str(out / "heights.json"), "--out", str(tmp_path / "h")]) == 0
    report = load(tmp_path / "h" / "heights_report.json")
    assert find(report, "config")["sha256"] == sha(out / "heights.json")

    for name, cfg in (("default", None), ("ai", out / "detail.json")):
        args = ["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                "--out", str(tmp_path / name), "--no-preview"]
        assert main(args + (["--config", str(cfg)] if cfg else [])) == 0
    default_report, ai_report = (load(tmp_path / name / "detail_report.json") for name in ("default", "ai"))
    assert len(default_report["counts"]["interiors"]) == 2
    assert ai_report["counts"]["interiors"] == []
    assert ai_report["config"]["interiors"]["enabled"] is False


def test_no_relevant_change_is_written_without_confirmation(tmp_path: Path) -> None:
    intent = write_intent(tmp_path / "intent.json", facade_detail="high", residential_levels=4)
    assert plan("--provider", "none", "--intent", str(intent), "--out", str(tmp_path / "o")) == 0
    assert load(tmp_path / "o" / "ai_run.json")["needs_confirmation"] is False


@pytest.mark.parametrize(("content", "code"), [
    ("{\"intent_version\": \"0.1.0\"}", "intent_required"),
    ("```json\n{}\n```", "invalid_json"),
    (json.dumps(intent_doc(residential_levels=60)), "intent_maximum"),
    (json.dumps(intent_doc(residential_levels=30)), "levels_outside_allowed_range"),
    (json.dumps({**intent_doc(), "area": {"center": {"latitude": 40.4, "longitude": -3.7}}}), "intent_additionalProperties"),
    (json.dumps(intent_doc(residential_levels=5, performance_priority="max")), "exceeds_measured_configuration"),
])
def test_rejected_responses_write_only_the_log(tmp_path: Path, content: str, code: str) -> None:
    intent = tmp_path / "intent.json"
    intent.write_text(content, encoding="utf-8")
    out = tmp_path / "out"
    assert plan("--provider", "none", "--intent", str(intent), "--confirm", "--out", str(out)) == 1
    assert sorted(p.name for p in out.iterdir()) == ["rejected.json"]
    log = load(out / "rejected.json")
    assert log["status"] == "rejected" and log["outputs"] == {}
    assert code in [d["code"] for d in log["diagnostics"]]


def test_replay_refuses_a_changed_base(tmp_path: Path) -> None:
    heights = tmp_path / "heights.json"
    shutil.copy(REPO / "config" / "heights.default.json", heights)
    intent = write_intent(tmp_path / "intent.json", residential_levels=5)
    out = tmp_path / "out"
    assert plan("--provider", "none", "--intent", str(intent), "--heights-config", str(heights), "--out", str(out)) == 3
    doc = load(heights)
    doc["storey_height_m"]["residential_multi"] = 3.1
    heights.write_text(json.dumps(doc), encoding="utf-8")
    assert plan("--provider", "replay", "--replay", str(out / "proposal.json"), "--confirm", "--out", str(out)) == 1
    log = load(out / "rejected.json")
    assert [d["code"] for d in log["diagnostics"]] == ["replay_base_changed"]
    assert not (out / "heights.json").exists()


def test_replay_refuses_a_tampered_response(tmp_path: Path) -> None:
    intent = write_intent(tmp_path / "intent.json", residential_levels=5)
    out = tmp_path / "out"
    assert plan("--provider", "none", "--intent", str(intent), "--out", str(out)) == 3
    doc = load(out / "proposal.json")
    doc["raw_response"] = doc["raw_response"].replace('"residential_levels": 5', '"residential_levels": 3')
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    assert plan("--provider", "replay", "--replay", str(tampered), "--confirm", "--out", str(tmp_path / "t")) == 2
    assert not (tmp_path / "t").exists() or not any((tmp_path / "t").iterdir())


def test_existing_outputs_are_not_overwritten(tmp_path: Path) -> None:
    intent = write_intent(tmp_path / "intent.json", facade_detail="high")
    out = tmp_path / "out"
    assert plan("--provider", "none", "--intent", str(intent), "--out", str(out)) == 0
    before = sha(out / "project.json")
    assert plan("--provider", "none", "--intent", str(intent), "--out", str(out)) == 1
    assert sha(out / "project.json") == before
    assert plan("--provider", "none", "--intent", str(intent), "--out", str(out), "--overwrite") == 0


@pytest.mark.parametrize("args", [
    ["--provider", "none", "--out", "x"],
    ["--provider", "replay", "--out", "x"],
    ["--provider", "ollama", "--out", "x"],
    ["--provider", "ollama", "--instruction", "  ", "--out", "x"],
    ["--provider", "ollama", "--instruction", "a" * 4001, "--out", "x"],
    ["--provider", "bogus", "--out", "x"],
])
def test_usage_errors(args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert plan(*args) == 2
    assert not (tmp_path / "x").exists()


def test_remote_ollama_host_is_refused(tmp_path: Path) -> None:
    assert plan("--instruction", EXAMPLE, "--host", "http://203.0.113.5:11434", "--out", str(tmp_path / "o")) == 1


def test_ollama_run_records_model_and_parameters(tmp_path: Path, fake_ollama) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    host, handler = fake_ollama
    out = tmp_path / "out"
    assert plan("--instruction", EXAMPLE, "--host", host, "--seed", "3", "--out", str(out)) == 3
    log = load(out / "proposal.json")
    p = log["provider"]
    assert (p["provider"], p["model"], p["model_digest"], p["server_version"]) == ("ollama", "qwen3:14b", "abc123", "0.0.test")
    assert p["options"]["temperature"] == 0 and p["options"]["seed"] == 3
    assert log["instruction"] == EXAMPLE
    assert log["instruction_sha256"] == hashlib.sha256(EXAMPLE.encode()).hexdigest()
    assert log["prompt"]["version"] and log["prompt"]["system_sha256"]
    assert log["intent_origin"] == {"parameters": "ai_generated", "validated": True, "summary": "model text, not verified"}
    assert {"project", "heights", "detail"} == set(log["base"])

    assert plan("--provider", "replay", "--replay", str(out / "proposal.json"), "--confirm", "--out", str(out)) == 0
    assert len(handler.requests) == 1
    assert load(out / "ai_run.json")["intent_origin"]["parameters"] == "ai_generated"
