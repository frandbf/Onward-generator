"""Mutation-test the lines a change touched, with mutmut.

    python tools/mutate/run.py [--base REF] [--max N] [--whole-functions]

The diff is taken from the merge base of REF and HEAD to the working tree, so
uncommitted edits count. mutmut generates mutants for every function in
``src/onward_generator`` that contains an added or changed line; only the
mutants whose edit falls on one of those lines are run. ``--whole-functions``
runs every mutant of those functions instead, which also measures the code
around the change.

Exit 0: every mutant run was killed. 1: at least one survived. 2: the tool could not run.

A survivor means a behaviour no test holds. Write a test that kills it, or, when
the edit changes nothing a caller can see, end that line with
``# pragma: no mutate: <reason>``.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path("src")
PACKAGE = SOURCE_ROOT / "onward_generator"
MUTANTS = Path("mutants")
CLASS_SEPARATOR = "ǁ"  # mutmut's separator between class and method in mutant names
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
MUTANT_SUFFIX = re.compile(r"__mutmut_(\d+)$")
NO_MATCH = "onward_generator.__no_mutant_matches__*"  # generates mutants and coverage, runs none
FAILING = ("survived",)
SHIM = Path(__file__).with_name("mutmut_only.py")


@dataclass(frozen=True, slots=True)
class Function:
    path: str
    module: str
    mangled: str
    first_line: int
    last_line: int
    def_line: int
    changed: frozenset[int] = field(default_factory=frozenset)

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


def _defs(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Top-level functions and methods of top-level classes, named as mutmut names them."""
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((f"x_{node.name}", node))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((f"x{CLASS_SEPARATOR}{node.name}{CLASS_SEPARATOR}{item.name}", item))
    return found


def functions(source: str, path: str) -> list[Function]:
    found: list[Function] = []
    for mangled, node in _defs(ast.parse(source)):
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        found.append(Function(path, module_name(path), mangled, first, node.end_lineno or node.lineno, node.lineno))
    return found


def touched(source: str, path: str, lines: set[int]) -> list[Function]:
    result: list[Function] = []
    for f in functions(source, path):
        hit = frozenset(n for n in lines if f.first_line <= n <= f.last_line)
        if hit:
            result.append(Function(f.path, f.module, f.mangled, f.first_line, f.last_line, f.def_line, hit))
    return result


def mutant_lines(mutated_source: str, mangled: str, def_line: int) -> dict[int, int]:
    """Mutant number → line of the original file its edit falls on.

    mutmut writes the original function as ``<mangled>__mutmut_orig`` and each
    mutant as ``<mangled>__mutmut_<n>``: same text except the edit. The first
    line that differs, counted from the ``def`` line, is the mutated line.
    """
    text = mutated_source.splitlines()
    bodies: dict[str, list[str]] = {}
    for node in ast.parse(mutated_source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(f"{mangled}__mutmut_"):
            bodies[node.name] = text[node.lineno - 1 : node.end_lineno]
    original = bodies.pop(f"{mangled}__mutmut_orig", None)
    if original is None:
        return {}
    lines: dict[int, int] = {}
    for name, body in bodies.items():
        number = MUTANT_SUFFIX.search(name)
        if number is None or not body:
            continue
        # The def line carries the mutant's own name; give it the original's so only the edit differs.
        body = [body[0].replace(name, f"{mangled}__mutmut_orig", 1), *body[1:]]
        ops = difflib.SequenceMatcher(a=original, b=body, autojunk=False).get_opcodes()
        first = next((i1 for tag, i1, _i2, _j1, _j2 in ops if tag != "equal"), None)
        if first is not None:
            lines[int(number.group(1))] = def_line + first
    return lines


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


def on_changed_lines(targets: list[Function]) -> list[str]:
    """Names of the generated mutants whose edit falls on a changed line."""
    names: list[str] = []
    sources: dict[str, str] = {}
    for f in targets:
        mutated = REPO / MUTANTS / f.path
        if f.path not in sources:
            sources[f.path] = mutated.read_text(encoding="utf-8") if mutated.is_file() else ""
        if not sources[f.path]:
            continue
        for number, line in sorted(mutant_lines(sources[f.path], f.mangled, f.def_line).items()):
            if line in f.changed:
                names.append(f"{f.module}.{f.mangled}__mutmut_{number}")
    return names


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


def clear_mutants(root: Path = REPO) -> None:
    """Delete mutmut's ``mutants/`` tree. mutmut reuses a file it finds there even when
    the source changed since, so a stale tree generates fewer mutants than CI and maps
    them to the wrong lines."""
    shutil.rmtree(root / MUTANTS, ignore_errors=True)


def only_mutate(targets: list[Function]) -> str:
    """The files mutmut rewrites: those holding a changed function, as ``only_mutate`` globs."""
    return ",".join(sorted({f.path for f in targets}))


def default_jobs(env: Mapping[str, str] | None = None, cores: int | None = None) -> int:
    """Every core on a CI runner, half of them on a workstation that runs other work too."""
    env = os.environ if env is None else env
    cores = cores or os.cpu_count() or 2
    return cores if env.get("CI") else max(1, cores // 2)


def _mutmut(*args: str, only: str = "", reuse: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MUTMUT_ONLY_MUTATE": only, "MUTMUT_REUSE_MUTANTS": "1" if reuse else ""}
    return subprocess.run([sys.executable, str(SHIM), *args], cwd=REPO, capture_output=True, text=True, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=os.environ.get("MUTATE_BASE", "main"), help="git ref the diff is taken against (default main)")
    parser.add_argument("--max", type=int, default=40, help="at most this many changed functions are mutated (default 40)")
    parser.add_argument("--show", type=int, default=10, help="print the diff of at most this many survivors (default 10)")
    parser.add_argument("--jobs", type=int, default=default_jobs(), help="mutmut workers (default: every core in CI, half of them locally)")
    parser.add_argument("--whole-functions", action="store_true", help="run every mutant of the changed functions, not only those on changed lines")
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
    print(f"mutate: {len(targets)} changed functions since {args.base}")
    clear_mutants()

    only = only_mutate(targets)
    if args.whole_functions:
        selected = [f.pattern for f in targets]
    else:
        generate = _mutmut("run", "--max-children", str(args.jobs), NO_MATCH, only=only)
        # mutmut 3.8 generates every mutant and the coverage data, then asserts that the filter matched none.
        if generate.returncode != 0 and "nothing matches" not in generate.stderr:
            print(generate.stdout[-4000:], generate.stderr[-4000:], sep="\n", file=sys.stderr)
            print(f"mutate: mutmut could not generate mutants (exit {generate.returncode})", file=sys.stderr)
            return 2
        selected = on_changed_lines(targets)
        print(f"mutate: {len(selected)} mutants fall on changed lines")
        if not selected:
            return 0

    # The generate step above already made the mutants; this run only tests them.
    run = _mutmut("run", "--max-children", str(args.jobs), *selected, only=only, reuse=not args.whole_functions)
    if run.returncode != 0:
        print(run.stdout[-4000:], run.stderr[-4000:], sep="\n", file=sys.stderr)
        print(f"mutate: mutmut run failed with exit {run.returncode}", file=sys.stderr)
        return 2
    listing = _mutmut("results", "--all", "true")
    if listing.returncode != 0:
        print(listing.stderr[-4000:], file=sys.stderr)
        return 2
    found = statuses(listing.stdout, selected)
    survivors = sorted(name for name, status in found.items() if status in FAILING)
    counts = Counter(found.values())

    for name in survivors[: args.show]:
        print(f"SURVIVED {name}\n{_mutmut('show', name).stdout.rstrip()}\n")
    if len(survivors) > args.show:
        print(f"... and {len(survivors) - args.show} more survivors (mutmut results)")
    summary = _summary(counts) or "no mutants"
    print(f"mutate: {summary}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        scope = "every mutant of" if args.whole_functions else "mutants on the changed lines of"
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(f"### Mutation testing\n\n{scope} {len(targets)} changed functions since `{args.base}`: {summary}.\n\n")
            for name in survivors:
                out.write(f"- `{name}` survived\n")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
