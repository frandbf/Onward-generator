"""Fail when one change both edits src/ and loosens a quality gate.

    python tools/quality/gate_guard.py [--base REF]

A gate is the configuration that decides whether code passes: the ruff, mypy,
mutmut and pytest settings in pyproject.toml, the Taskfile, the workflows, the
mutation and lint tooling, and new entries in mypy-baseline.txt. Change a gate in
its own pull request, so a reviewer sees it apart from the code it would let through.

Exit 0: no conflict. 1: the change touches both. 2: git failed.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = "src/"
GATE_FILES = (
    "Taskfile.yml",
    ".github/workflows/*",
    ".githooks/*",
    "tools/mutate/*",
    "tools/quality/*",
    "requirements-lint.lock",
    "requirements-mutate.lock",
    "tests/unit/test_quality_gates.py",
)
GATE_TABLES = ("ruff", "mypy", "mutmut", "pytest")
BASELINE = "mypy-baseline.txt"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout


def _show(ref: str, path: str) -> str:
    try:
        return _git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return ""


def tool_tables(pyproject: str) -> dict[str, object]:
    """The gate tables of ``[tool]`` in a pyproject.toml text."""
    tool = tomllib.loads(pyproject).get("tool", {}) if pyproject else {}
    return {name: tool.get(name) for name in GATE_TABLES}


def added_lines(old: str, new: str) -> set[str]:
    return set(new.splitlines()) - set(old.splitlines())


def gates_touched(changed: list[str], read_base: Callable[[str], str], read_head: Callable[[str], str]) -> list[str]:
    """Gates a change edits, given the changed paths and readers for each side's file text."""
    found = [path for path in changed if any(fnmatch.fnmatchcase(path, pattern) for pattern in GATE_FILES)]
    if "pyproject.toml" in changed:
        old, new = tool_tables(read_base("pyproject.toml")), tool_tables(read_head("pyproject.toml"))
        found += [f"pyproject.toml [tool.{name}]" for name in GATE_TABLES if old[name] != new[name]]
    if BASELINE in changed and added_lines(read_base(BASELINE), read_head(BASELINE)):
        found.append(f"{BASELINE} (new entries)")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=os.environ.get("GATE_BASE", "main"), help="git ref the change is compared with (default main)")
    args = parser.parse_args(argv)
    try:
        merge_base = _git("merge-base", args.base, "HEAD").strip()
        changed = _git("diff", "--name-only", merge_base, "HEAD").splitlines()
        gates = gates_touched(changed, lambda p: _show(merge_base, p), lambda p: _show("HEAD", p))
    except subprocess.CalledProcessError as error:
        print(f"gate-guard: git failed: {error.stderr.strip()}", file=sys.stderr)
        return 2
    source = [path for path in changed if path.startswith(SOURCE)]
    if source and gates:
        print("gate-guard: this change edits src/ and a quality gate. Split the gate change into its own pull request.")
        for gate in gates:
            print(f"  gate: {gate}")
        print(f"  and {len(source)} file(s) under {SOURCE}, e.g. {source[0]}")
        return 1
    print(f"gate-guard: ok ({len(source)} source file(s), {len(gates)} gate(s) changed since {args.base})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
