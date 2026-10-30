"""The quality gates themselves: suppressions carry reasons, and the gate guard sees gate edits."""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import tokenize
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("gate_guard", ROOT / "tools" / "quality" / "gate_guard.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)

NOQA = re.compile(r"#\s*noqa\b(?P<rest>.*)", re.IGNORECASE)
NOQA_WITH_REASON = re.compile(r"^: [A-Z]+[0-9]+(?:, ?[A-Z]+[0-9]+)* - \S")
NO_MUTATE = re.compile(r"#\s*pragma: no mutate(?P<rest>.*)")
# mutmut reads "no mutate: block|start|end" as a region marker, not a reason.
NO_MUTATE_WITH_REASON = re.compile(r"^: (?!(?:block|start|end)\b)\S")


def _comments() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for folder in ("src", "tests", "tools"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type == tokenize.COMMENT:
                    found.append((str(path.relative_to(ROOT)), token.start[0], token.string))
    return found


COMMENTS = _comments()


def test_every_noqa_names_its_codes_and_a_reason() -> None:
    bad = [f"{p}:{n}: {c}" for p, n, c in COMMENTS if (m := NOQA.search(c)) and not NOQA_WITH_REASON.match(m["rest"])]
    assert bad == [], "write '# noqa: CODE - reason':\n" + "\n".join(bad)


def test_every_no_mutate_pragma_has_a_reason() -> None:
    bad = [f"{p}:{n}: {c}" for p, n, c in COMMENTS if (m := NO_MUTATE.search(c)) and not NO_MUTATE_WITH_REASON.match(m["rest"])]
    assert bad == [], "write '# pragma: no mutate: <why the edit is not observable>':\n" + "\n".join(bad)


@pytest.mark.parametrize(
    ("comment", "ok"),
    [
        ("# noqa: E731 - a local helper", True),
        ("# noqa: F401, F811 - fixture", True),
        ("# noqa: E731", False),
        ("# noqa", False),
        ("# noqa: E731 (fixture)", False),
    ],
)
def test_noqa_reason_pattern(comment: str, ok: bool) -> None:
    match = NOQA.search(comment)
    assert match is not None
    assert bool(NOQA_WITH_REASON.match(match["rest"])) is ok


@pytest.mark.parametrize(
    ("comment", "ok"),
    [
        ("# pragma: no mutate: the dtype changes nothing", True),
        ("# pragma: no mutate", False),
        ("# pragma: no mutate: block", False),
        ("# pragma: no mutate: start", False),
        ("# pragma: no mutate: endpoint ordering is irrelevant", True),
    ],
)
def test_no_mutate_reason_pattern(comment: str, ok: bool) -> None:
    match = NO_MUTATE.search(comment)
    assert match is not None
    assert bool(NO_MUTATE_WITH_REASON.match(match["rest"])) is ok


PYPROJECT = """
[project]
name = "x"
[tool.ruff.lint]
select = ["E", "F"]
[tool.mypy]
files = ["src"]
"""


def _reader(files: dict[str, str]) -> Callable[[str], str]:
    return lambda path: files.get(path, "")


def test_guard_sees_gate_files_and_ignores_other_paths() -> None:
    changed = ["Taskfile.yml", ".github/workflows/ci.yml", "tools/mutate/run.py", "docs/07-detail.md", "src/onward_generator/io.py"]
    assert guard.gates_touched(changed, _reader({}), _reader({})) == ["Taskfile.yml", ".github/workflows/ci.yml", "tools/mutate/run.py"]


def test_guard_sees_only_the_gate_tables_of_pyproject() -> None:
    loosened = PYPROJECT.replace('select = ["E", "F"]', 'select = ["E"]')
    renamed = PYPROJECT.replace('name = "x"', 'name = "y"')
    base = _reader({"pyproject.toml": PYPROJECT})
    assert guard.gates_touched(["pyproject.toml"], base, _reader({"pyproject.toml": loosened})) == ["pyproject.toml [tool.ruff]"]
    assert guard.gates_touched(["pyproject.toml"], base, _reader({"pyproject.toml": renamed})) == []


def test_guard_flags_new_baseline_entries_but_not_removed_ones() -> None:
    old = "a.py:0: error: one  [x]\nb.py:0: error: two  [y]\n"
    grown = old + "c.py:0: error: three  [z]\n"
    shrunk = "a.py:0: error: one  [x]\n"
    base = _reader({"mypy-baseline.txt": old})
    assert guard.gates_touched(["mypy-baseline.txt"], base, _reader({"mypy-baseline.txt": grown})) == ["mypy-baseline.txt (new entries)"]
    assert guard.gates_touched(["mypy-baseline.txt"], base, _reader({"mypy-baseline.txt": shrunk})) == []
