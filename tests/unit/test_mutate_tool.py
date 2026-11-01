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
    found = {f.mangled: (f.first_line, f.last_line, f.def_line) for f in mutate.functions(SOURCE, "src/onward_generator/m.py")}
    assert found == {"x_first": (4, 5, 4), "x_second": (8, 10, 9), "xǁBoxǁgrow": (16, 17, 16)}


def test_touched_selects_only_functions_with_changed_lines() -> None:
    picked = mutate.touched(SOURCE, "src/onward_generator/m.py", {8, 14})
    assert [f.pattern for f in picked] == ["onward_generator.m.x_second__mutmut_*"]
    assert picked[0].changed == frozenset({8})
    assert mutate.touched(SOURCE, "src/onward_generator/m.py", {1, 2, 14}) == []


# Shape of mutmut 3.8 generated code: the original and each mutant as separate functions.
MUTATED = """def x_area__mutmut_orig(w, h=1):
    side = w * h
    return side + 0


def x_area__mutmut_1(w, h=2):
    side = w * h
    return side + 0


def x_area__mutmut_2(w, h=1):
    side = w / h
    return side + 0


def x_area__mutmut_3(w, h=1):
    side = w * h
    return side - 0


def x_area__mutmut_4(w, h=1):
    side = None
    return side + 0
"""


def test_mutant_lines_maps_each_edit_to_the_original_file_line() -> None:
    # In the real file, ``def area`` is on line 20, so its body starts on 21.
    assert mutate.mutant_lines(MUTATED, "x_area", 20) == {1: 20, 2: 21, 3: 22, 4: 21}


def test_mutant_lines_without_an_original_maps_nothing() -> None:
    assert mutate.mutant_lines("def x_other__mutmut_1():\n    return 1\n", "x_area", 20) == {}


def test_on_changed_lines_keeps_only_mutants_on_changed_lines(tmp_path, monkeypatch) -> None:
    mutated = tmp_path / "mutants" / "src" / "onward_generator" / "m.py"
    mutated.parent.mkdir(parents=True)
    mutated.write_text(MUTATED, encoding="utf-8")
    monkeypatch.setattr(mutate, "REPO", tmp_path)
    target = mutate.Function("src/onward_generator/m.py", "onward_generator.m", "x_area", 20, 22, 20, frozenset({21}))
    assert mutate.on_changed_lines([target]) == ["onward_generator.m.x_area__mutmut_2", "onward_generator.m.x_area__mutmut_4"]


def test_statuses_filters_by_pattern() -> None:
    results = "    a.x_f__mutmut_1: killed\n    a.x_f__mutmut_2: survived\n    a.x_fg__mutmut_1: survived\n"
    assert mutate.statuses(results, ["a.x_f__mutmut_*"]) == {"a.x_f__mutmut_1": "killed", "a.x_f__mutmut_2": "survived"}


def test_clear_mutants_removes_a_stale_tree_and_accepts_none(tmp_path: Path) -> None:
    stale = tmp_path / "mutants" / "src" / "onward_generator"
    stale.mkdir(parents=True)
    (stale / "old.py").write_text("x = 1\n", encoding="utf-8")
    mutate.clear_mutants(tmp_path)
    assert not (tmp_path / "mutants").exists()
    mutate.clear_mutants(tmp_path)
    assert tmp_path.exists()


def test_only_mutate_lists_each_file_with_a_changed_function_once() -> None:
    def fn(path: str) -> object:
        return mutate.Function(path, mutate.module_name(path), "x_f", 1, 2, 1)

    targets = [fn("src/onward_generator/b.py"), fn("src/onward_generator/a.py"), fn("src/onward_generator/b.py")]
    assert mutate.only_mutate(targets) == "src/onward_generator/a.py,src/onward_generator/b.py"


def test_default_jobs_uses_every_core_in_ci_and_half_locally() -> None:
    assert mutate.default_jobs({"CI": "true"}, 4) == 4
    assert mutate.default_jobs({}, 20) == 10
    assert mutate.default_jobs({}, 1) == 1


def test_only_a_change_of_case_counts_as_only_case() -> None:
    assert mutate.only_case_differs('    return "foo"', '    return "FOO"')
    assert not mutate.only_case_differs('    return "foo"', '    return "XXfooXX"')
    assert not mutate.only_case_differs('    return "foo"', '    return "foo"')
    assert not mutate.only_case_differs("    if a < b:", "    if a <= b:")


CASE = """def x_name__mutmut_orig():
    return "foo"
def x_name__mutmut_1():
    return "XXfooXX"
def x_name__mutmut_2():
    return "FOO"
"""


def test_on_changed_lines_leaves_out_a_mutant_that_only_changes_case(tmp_path, monkeypatch) -> None:
    mutated = tmp_path / "mutants" / "src" / "onward_generator" / "m.py"
    mutated.parent.mkdir(parents=True)
    mutated.write_text(CASE, encoding="utf-8")
    monkeypatch.setattr(mutate, "REPO", tmp_path)
    target = mutate.Function("src/onward_generator/m.py", "onward_generator.m", "x_name", 1, 2, 1, frozenset({2}))
    assert mutate.on_changed_lines([target]) == ["onward_generator.m.x_name__mutmut_1"]
    assert mutate.mutant_edits(CASE, "x_name", 1)[2] == (2, '    return "foo"', '    return "FOO"')


def test_fastest_tests_run_first_and_ties_keep_a_stable_order() -> None:
    spec = importlib.util.spec_from_file_location("mutmut_only", ROOT / "tools" / "mutate" / "mutmut_only.py")
    assert spec and spec.loader
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    durations = {"slow": 20.0, "fast": 0.1, "tie_b": 1.0, "tie_a": 1.0}
    assert shim.fastest_first({"slow", "tie_b", "unknown", "fast", "tie_a"}, durations) == ["unknown", "fast", "tie_a", "tie_b", "slow"]
