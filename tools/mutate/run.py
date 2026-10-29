"""Mutation-test only the functions a change touched, with mutmut.

    python tools/mutate/run.py [--base REF] [--max N]

The diff is taken from the merge base of REF and HEAD to the working tree, so
uncommitted edits count. Every function in ``src/onward_generator`` that
contains an added or changed line is mutated; the rest of the package is not.

Exit 0: every mutant was killed. 1: at least one survived. 2: the tool could not run.

A survivor means a behaviour no test holds. Write a test that kills it, or, when
the edit changes nothing a caller can see, mark the line
``# pragma: no mutate: <reason>``.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path("src")
PACKAGE = SOURCE_ROOT / "onward_generator"
CLASS_SEPARATOR = "ǁ"  # mutmut's separator between class and method in mutant names
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
FAILING = ("survived",)


@dataclass(frozen=True, slots=True)
class Function:
    module: str
    mangled: str
    first_line: int
    last_line: int

    @property
    def pattern(self) -> str:
        return f"{self.module}.{self.mangled}__mutmut_*"


def changed_lines(diff: str) -> dict[str, set[int]]:
    """New-file line numbers touched by a ``git diff -U0``, per path.

    A pure deletion marks the line just before it, so the function that lost
    code still counts as changed.
    """
    lines: dict[str, set[int]] = {}
    current: set[int] | None = None
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[4:]
            current = None if path == "/dev/null" else lines.setdefault(path.removeprefix("b/"), set())
            continue
        match = HUNK.match(row)
        if match and current is not None:
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            current.update(range(start, start + count) if count else {max(start, 1)})
    return lines


def module_name(path: str) -> str:
    parts = list(Path(path).relative_to(SOURCE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def functions(source: str, module: str) -> list[Function]:
    """Top-level functions and methods of top-level classes, named as mutmut names them."""

    def span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int]:
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        return first, node.end_lineno or node.lineno

    found: list[Function] = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(Function(module, f"x_{node.name}", *span(node)))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mangled = f"x{CLASS_SEPARATOR}{node.name}{CLASS_SEPARATOR}{item.name}"
                    found.append(Function(module, mangled, *span(item)))
    return found


def touched(source: str, path: str, lines: set[int]) -> list[Function]:
    return [f for f in functions(source, module_name(path)) if any(f.first_line <= n <= f.last_line for n in lines)]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout


def changed_functions(base: str) -> list[Function]:
    merge_base = _git("merge-base", base, "HEAD").strip()
    per_file = changed_lines(_git("diff", "-U0", "--no-color", merge_base, "--", f"{PACKAGE}/*.py", f"{PACKAGE}/**/*.py"))
    for path in _git("ls-files", "--others", "--exclude-standard", "--", str(PACKAGE)).splitlines():
        if path.endswith(".py"):
            per_file[path] = set(range(1, len((REPO / path).read_text(encoding="utf-8").splitlines()) + 1))
    result: list[Function] = []
    for path, lines in sorted(per_file.items()):
        file = REPO / path
        if lines and file.is_file():
            result.extend(touched(file.read_text(encoding="utf-8"), path, lines))
    return result


def statuses(results: str, patterns: list[str]) -> dict[str, str]:
    """``mutmut results --all true`` rows (``name: status``) whose name matches a pattern."""
    found: dict[str, str] = {}
    for row in results.splitlines():
        name, sep, status = row.strip().rpartition(": ")
        if sep and any(fnmatch.fnmatchcase(name, p) for p in patterns):
            found[name] = status
    return found


def _summary(counts: Counter[str]) -> str:
    order = ["killed", "survived", "timeout", "no tests", "suspicious", "not checked", "skipped", "segfault"]
    keys = order + sorted(k for k in counts if k not in order)
    return ", ".join(f"{counts[k]} {k}" for k in keys if counts[k])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=os.environ.get("MUTATE_BASE", "main"), help="git ref the diff is taken against (default main)")
    parser.add_argument("--max", type=int, default=40, help="at most this many changed functions are mutated (default 40)")
    parser.add_argument("--show", type=int, default=10, help="print the diff of at most this many survivors (default 10)")
    args = parser.parse_args(argv)

    try:
        targets = changed_functions(args.base)
    except subprocess.CalledProcessError as error:
        print(f"mutate: git failed: {error.stderr.strip()}", file=sys.stderr)
        return 2
    if not targets:
        print(f"mutate: no function in {PACKAGE} changed since {args.base}")
        return 0
    if len(targets) > args.max:
        dropped = targets[args.max :]
        targets = targets[: args.max]
        print(f"mutate: {len(dropped)} changed functions over --max {args.max} are NOT mutated:")
        for f in dropped:
            print(f"  skipped {f.module}.{f.mangled}")
    patterns = [f.pattern for f in targets]
    print(f"mutate: {len(targets)} changed functions since {args.base}")

    run = subprocess.run(["mutmut", "run", *patterns], cwd=REPO, capture_output=True, text=True)
    if run.returncode != 0:
        print(run.stdout[-4000:], run.stderr[-4000:], sep="\n", file=sys.stderr)
        print(f"mutate: mutmut run failed with exit {run.returncode}", file=sys.stderr)
        return 2
    results = subprocess.run(["mutmut", "results", "--all", "true"], cwd=REPO, check=True, capture_output=True, text=True).stdout
    found = statuses(results, patterns)
    survivors = sorted(name for name, status in found.items() if status in FAILING)
    counts = Counter(found.values())

    for name in survivors[: args.show]:
        shown = subprocess.run(["mutmut", "show", name], cwd=REPO, capture_output=True, text=True)
        print(f"SURVIVED {name}\n{shown.stdout.rstrip()}\n")
    if len(survivors) > args.show:
        print(f"... and {len(survivors) - args.show} more survivors (mutmut results)")
    summary = _summary(counts) or "no mutants"
    print(f"mutate: {summary}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(f"### Mutation testing\n\n{len(targets)} changed functions since `{args.base}`: {summary}.\n\n")
            for name in survivors:
                out.write(f"- `{name}` survived\n")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
