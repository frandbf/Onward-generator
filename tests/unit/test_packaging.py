from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "onward_generator"
# Import name → distribution name, where they differ.
DISTRIBUTION = {"PIL": "pillow"}
# mutmut adds this import to the mutated copy it tests (task mutate); the package never imports it.
INJECTED = {"mutmut"}


def _declared() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0].lower() for spec in project["dependencies"]}


def _third_party_imports() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top != "onward_generator" and top not in sys.stdlib_module_names and top not in INJECTED:
                    found.setdefault(top, path)
    return found


def test_pyproject_declares_imported_third_party() -> None:
    declared = _declared()
    missing = {
        name: str(path.relative_to(ROOT))
        for name, path in _third_party_imports().items()
        if DISTRIBUTION.get(name, name).lower() not in declared
    }
    assert not missing, f"imported in src/ but not in pyproject dependencies: {missing}"


def test_pyproject_dependencies_match_lock() -> None:
    lock = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.split("==", 1)
            lock[name.strip().lower()] = version.strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for spec in project["dependencies"]:
        name, version = spec.split("==", 1)
        assert lock.get(name.lower()) == version, f"{spec} differs from requirements.lock"
