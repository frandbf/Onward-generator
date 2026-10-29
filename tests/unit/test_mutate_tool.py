from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("mutate_run", ROOT / "tools" / "mutate" / "run.py")
assert _spec and _spec.loader
mutate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mutate  # dataclasses look the module up while the class is built
_spec.loader.exec_module(mutate)

DIFF = """\
diff --git a/src/onward_generator/heights/estimator.py b/src/onward_generator/heights/estimator.py
--- a/src/onward_generator/heights/estimator.py
+++ b/src/onward_generator/heights/estimator.py
@@ -10,0 +11,2 @@ def a():
@@ -20 +22 @@ def b():
@@ -30,3 +33,0 @@ def c():
diff --git a/src/onward_generator/old.py b/src/onward_generator/old.py
--- a/src/onward_generator/old.py
+++ /dev/null
@@ -1,4 +0,0 @@
"""

SOURCE = """\
import functools


def first(x):
    return x + 1


@functools.cache
def second(x):
    return x * 2


class Box:
    size = 3

    def grow(self, n):
        return self.size + n
"""


def test_changed_lines_counts_additions_changes_and_deletions() -> None:
    lines = mutate.changed_lines(DIFF)
    assert lines == {"src/onward_generator/heights/estimator.py": {11, 12, 22, 33}}


def test_module_name_handles_packages() -> None:
    assert mutate.module_name("src/onward_generator/heights/estimator.py") == "onward_generator.heights.estimator"
    assert mutate.module_name("src/onward_generator/heights/__init__.py") == "onward_generator.heights"


def test_functions_use_mutmut_names_and_include_decorators() -> None:
    found = {f.mangled: (f.first_line, f.last_line) for f in mutate.functions(SOURCE, "m")}
    assert found == {"x_first": (4, 5), "x_second": (8, 10), "xǁBoxǁgrow": (16, 17)}


def test_touched_selects_only_functions_with_changed_lines() -> None:
    picked = mutate.touched(SOURCE, "src/onward_generator/m.py", {8, 14})
    assert [f.pattern for f in picked] == ["onward_generator.m.x_second__mutmut_*"]
    assert mutate.touched(SOURCE, "src/onward_generator/m.py", {1, 2, 14}) == []


def test_statuses_filters_by_pattern() -> None:
    results = "    a.x_f__mutmut_1: killed\n    a.x_f__mutmut_2: survived\n    a.x_fg__mutmut_1: survived\n"
    assert mutate.statuses(results, ["a.x_f__mutmut_*"]) == {"a.x_f__mutmut_1": "killed", "a.x_f__mutmut_2": "survived"}
