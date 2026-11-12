"""``onward-generator ai plan``: natural-language instruction → validated configuration (phase 06).

Exit codes: 0 written, 1 rejected or provider error, 2 usage, 3 confirmation required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onward_generator import __version__
from onward_generator.ai.intent import (
    INTENT_SCHEMA_PATH,
    MAX_RESPONSE_BYTES,
    IntentError,
    intent_schema,
    intent_schema_sha256,
    parse_intent,
)
from onward_generator.ai.planner import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    BaseConfigs,
    PlanError,
    build_plan,
    load_base,
    output_documents,
    system_prompt_sha256,
)
from onward_generator.ai.providers import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_TIMEOUT_S,
    OllamaProvider,
    Provider,
    ProviderError,
    StaticProvider,
    default_ollama_host,
)
from onward_generator.config.diagnostics import Diagnostic
from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.contracts import is_schema
from onward_generator.io import atomic_write_json, prepare_outputs

RUN_SCHEMA = "onward_generator.ai_run"
RUN_VERSION = "0.1.0"
MAX_INSTRUCTION_CHARS = 4000
CONFIG_OUTPUTS = ("project.json", "heights.json", "detail.json", "intent.json")
LOG_NAMES = {"written": "ai_run.json", "pending_confirmation": "proposal.json", "rejected": "rejected.json"}
EXIT = {"written": 0, "rejected": 1, "pending_confirmation": 3}
DEFAULT_PROJECT = Path(__file__).resolve().parents[3] / "config" / "murcia-belluga.example.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator ai", description="Phase 06: instruction → validated configuration")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="interpret an instruction and write validated configuration files")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--instruction", help="natural-language instruction")
    src.add_argument("--instruction-file", help="UTF-8 text file with the instruction")
    p.add_argument("--provider", choices=("ollama", "none", "replay"), default="ollama",
                   help="ollama: local model; none: no AI, --intent FILE; replay: re-apply a recorded run")
    p.add_argument("--intent", help="intent JSON written by hand (--provider none)")
    p.add_argument("--replay", help="ai_run.json / proposal.json / rejected.json to re-apply (--provider replay)")
    p.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    p.add_argument("--host", default=default_ollama_host(), help="loopback only; default $ONWARD_OLLAMA_HOST or http://127.0.0.1:11434")
    p.add_argument("--seed", type=int, default=0, help="Ollama sampling seed")
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--project-config", default=None, help=f"base project configuration (default: {DEFAULT_PROJECT.name})")
    p.add_argument("--heights-config", default=None, help="base heights configuration (default: config/heights.default.json)")
    p.add_argument("--detail-config", default=None, help="base detail configuration (default: config/detail.default.json)")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--confirm", action="store_true", help="accept relevant changes and write the configuration")
    p.add_argument("--overwrite", action="store_true")
    return parser


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: str, limit: int, what: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"{what} not found: {p}")
    raw = p.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"{what} is {len(raw)} bytes; limit is {limit}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{what} is not UTF-8: {exc}") from exc


def _instruction(args: argparse.Namespace) -> str | None:
    if args.instruction_file:
        text = _read_text(args.instruction_file, MAX_INSTRUCTION_CHARS * 4, "instruction file")
    else:
        text = args.instruction
    if text is None:
        return None
    text = text.strip()
    if not text:
        raise ValueError("the instruction is empty")
    if len(text) > MAX_INSTRUCTION_CHARS:
        raise ValueError(f"the instruction has {len(text)} characters; limit is {MAX_INSTRUCTION_CHARS}")
    return text


def _load_replay(path: str) -> tuple[dict[str, Any], Path]:
    try:
        doc, resolved = read_json_document(path)
    except ConfigLoadError as exc:
        raise ValueError(f"replay file: {exc}") from exc
    if not is_schema(doc.get("schema"), RUN_SCHEMA) or doc.get("schema_version") != RUN_VERSION:
        raise ValueError(f"replay file {resolved} is not a {RUN_SCHEMA} {RUN_VERSION} log")
    raw = doc.get("raw_response")
    if not isinstance(raw, str) or _sha(raw) != doc.get("raw_response_sha256"):
        raise ValueError(f"replay file {resolved}: raw_response is missing or does not match its sha256")
    if not isinstance(doc.get("instruction"), str):
        raise ValueError(f"replay file {resolved}: instruction is missing")
    return doc, resolved


def _replay_diagnostics(log: dict[str, Any], base: BaseConfigs) -> list[Diagnostic]:
    out = []
    for name, now in base.provenance().items():
        then = log.get("base", {}).get(name, {})
        if then.get("sha256") != now["sha256"]:
            out.append(Diagnostic("error", "replay_base_changed", f"{name}:",
                                  f"base {name} configuration changed since the recorded run ({now['path']}); run the instruction again"))
    if log.get("intent_schema_sha256") != intent_schema_sha256():
        out.append(Diagnostic("error", "replay_schema_changed", "", "config/intent.schema.json changed since the recorded run"))
    return out


def run(args: argparse.Namespace, *, now: datetime | None = None) -> int:
    instruction = _instruction(args)
    replay_log: dict[str, Any] | None = None
    replay_ref: dict[str, Any] | None = None
    provider: Provider
    if args.provider == "replay":
        if not args.replay or args.intent:
            raise ValueError("--provider replay needs --replay FILE (and no --intent)")
        replay_log, replay_path = _load_replay(args.replay)
        if instruction is not None and instruction != replay_log["instruction"]:
            raise ValueError("--instruction differs from the recorded instruction; omit it when replaying")
        instruction = replay_log["instruction"]
        provider = StaticProvider("replay", replay_log["raw_response"], str(replay_path))
        replay_ref = {"path": str(replay_path), "sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
                      "provider": replay_log.get("provider")}
        base_paths = replay_log.get("base", {})
        project = args.project_config or base_paths.get("project", {}).get("path")
        heights = args.heights_config or base_paths.get("heights", {}).get("path")
        detail = args.detail_config or base_paths.get("detail", {}).get("path")
    else:
        if args.replay:
            raise ValueError("--replay needs --provider replay")
        if instruction is None and args.provider == "ollama":
            raise ValueError("--provider ollama needs --instruction or --instruction-file")
        if args.provider == "none":
            if not args.intent:
                raise ValueError("--provider none needs --intent FILE (no AI is used)")
            provider = StaticProvider("none", _read_text(args.intent, MAX_RESPONSE_BYTES, "intent file"),
                                      str(Path(args.intent).expanduser().resolve()))
        else:
            if args.intent:
                raise ValueError("--intent needs --provider none")
            provider = OllamaProvider(model=args.model, host=args.host, seed=args.seed, timeout_s=args.timeout_s)
        project, heights, detail = args.project_config or DEFAULT_PROJECT, args.heights_config, args.detail_config
        instruction = instruction or ""

    out_dir = Path(args.out).expanduser().resolve()
    inputs = tuple(Path(p).expanduser().resolve() for p in (args.replay, args.intent, project, heights, detail) if p)
    names = (*CONFIG_OUTPUTS, *(n for n in LOG_NAMES.values() if out_dir / n not in inputs))
    targets = prepare_outputs(out_dir, names, overwrite=args.overwrite, protected=inputs)
    paths = dict(zip(names, targets, strict=True))

    base = load_base(project, heights, detail)
    log: dict[str, Any] = {
        "schema": RUN_SCHEMA, "schema_version": RUN_VERSION,
        "created_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "generator_version": __version__,
        "note": "The model's response is data: it was parsed as JSON and validated; nothing in it was executed.",
        "instruction": instruction, "instruction_sha256": _sha(instruction),
        "prompt": {"version": PROMPT_VERSION, "system_sha256": system_prompt_sha256(), "system": SYSTEM_PROMPT},
        "intent_schema": {"path": str(INTENT_SCHEMA_PATH)}, "intent_schema_sha256": intent_schema_sha256(),
        "base": base.provenance(), "replay_of": replay_ref,
    }
    diagnostics: list[Diagnostic] = _replay_diagnostics(replay_log, base) if replay_log else []
    changes: list[dict[str, Any]] = []
    docs: dict[str, Any] | None = None
    intent_doc: dict[str, Any] | None = None
    if not diagnostics:
        try:
            result = provider.complete(SYSTEM_PROMPT, instruction, intent_schema())
            log["provider"] = result.provenance
            log["raw_response"], log["raw_response_sha256"] = result.text, _sha(result.text)
            intent = parse_intent(result.text)
            intent_doc = intent.document
            plan = build_plan(intent, base, instruction)
            changes = [c.to_dict() for c in plan.changes]
            diagnostics = list(plan.diagnostics)
            if plan.valid:
                docs = output_documents(plan, base, out_dir)
        except ProviderError as exc:
            diagnostics = [Diagnostic("error", "provider_error", "", str(exc))]
        except (IntentError, PlanError) as exc:
            diagnostics += exc.diagnostics
        if replay_log is not None:
            log["provider"] = {"provider": "replay", "recorded": replay_log.get("provider")}

    errors = any(d.severity == "error" for d in diagnostics)
    needs_confirmation = any(c["effect"] == "geometry" for c in changes) or any(d.severity == "warning" for d in diagnostics)
    status = "rejected" if errors or docs is None else ("pending_confirmation" if needs_confirmation and not args.confirm else "written")
    if replay_log is not None:
        origin = (replay_log.get("intent_origin") or {}).get("parameters")
        if origin not in ("ai_generated", "user_supplied"):
            raise ValueError("replay file: intent_origin.parameters must be ai_generated or user_supplied")
    else:
        origin = "ai_generated" if args.provider == "ollama" else "user_supplied"
    log.update({"intent_origin": {"parameters": origin, "validated": intent_doc is not None,
                                  "summary": "model text, not verified" if origin == "ai_generated" else "user text"},
                "intent": intent_doc, "changes": changes, "diagnostics": [d.to_dict() for d in diagnostics],
                "needs_confirmation": needs_confirmation, "confirmed": bool(args.confirm and status == "written"),
                "status": status})

    if LOG_NAMES[status] not in paths:
        raise ValueError(f"the {status} log would overwrite the replayed file {out_dir / LOG_NAMES[status]}; use another --out")
    outputs: dict[str, str] = {}
    if status == "written" and docs is not None and intent_doc is not None:
        for name, doc in (("project.json", docs["project"]), ("heights.json", docs["heights"]),
                          ("detail.json", docs["detail"]), ("intent.json", intent_doc)):
            atomic_write_json(paths[name], doc)
            outputs[name] = hashlib.sha256(paths[name].read_bytes()).hexdigest()
    log["outputs"] = outputs
    log_path = paths[LOG_NAMES[status]]
    atomic_write_json(log_path, log)

    _report(status, changes, diagnostics, log_path, log)
    return EXIT[status]


def _report(status: str, changes: list[dict[str, Any]], diagnostics: list[Diagnostic], log_path: Path,
            log: dict[str, Any]) -> None:
    prov = log.get("provider") or {}
    model_info = prov.get("recorded") or prov
    model = model_info.get("model")
    digest = (model_info.get("model_digest") or "")[:12]
    print(f"provider: {prov.get('provider', '?')}" + (f", model {model} ({digest})" if model else ""))
    if changes:
        print("changes:")
        for c in changes:
            print(f"  {c['document']}:{c['path']}: {json.dumps(c['before'])} -> {json.dumps(c['after'])}  [{c['effect']}, cost {c['cost']}]")
    else:
        print("changes: none")
    order = {"error": 0, "warning": 1, "info": 2}
    for d in sorted(diagnostics, key=lambda d: order[d.severity]):
        print(f"  {d}")
    if status == "written":
        print(f"written: {', '.join(log['outputs'])} and {log_path.name} in {log_path.parent}")
    elif status == "pending_confirmation":
        print(f"nothing written: relevant changes need confirmation. Review {log_path}, then run:\n"
              f"  onward-generator ai plan --provider replay --replay {log_path} --confirm --out {log_path.parent}")
    else:
        print(f"rejected: nothing written; details in {log_path}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        return run(args)
    except (ValueError, FileExistsError, ProviderError) as exc:
        print(f"ai: {exc}", file=sys.stderr)
        return 1 if isinstance(exc, (FileExistsError, PlanError, ProviderError)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
