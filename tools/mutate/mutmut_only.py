"""The mutmut CLI, mutating only the files in MUTMUT_ONLY_MUTATE (comma-separated globs).

mutmut 3.8 has no command-line flag for ``only_mutate``. Without it, ``mutmut run``
rewrites every file under src/ and traces all of them while it maps tests to
functions, which took most of the time when only a few files changed.

It also runs a mutant's tests in the order of a set. Most mutants die on the first
test that reaches them, so the fastest tests go first: a mutant a 0.1 s test kills
does not wait for a 20 s one. A survivor runs them all, whatever the order.
"""

import dataclasses
import os
import sys
from collections.abc import Iterable

from mutmut import configuration
from mutmut.__main__ import cli
from mutmut.runners.harness import PytestRunner
from mutmut.state import state

_run_tests = PytestRunner.run_tests


def fastest_first(tests: Iterable[str], durations: dict[str, float]) -> list[str]:
    """``tests`` by recorded duration, then name, so the order is the same on every run."""
    return sorted(tests, key=lambda test: (durations.get(test, 0.0), test))


def _run_fastest_first(self: PytestRunner, *, mutant_name: str | None, tests: Iterable[str]) -> int:
    return _run_tests(self, mutant_name=mutant_name, tests=fastest_first(tests, state().duration_by_test))


def main() -> int:
    only = [p for p in os.environ.get("MUTMUT_ONLY_MUTATE", "").split(",") if p]
    if only:
        # configuration.config() loads pyproject.toml once and caches it in configuration._config.
        configuration._config = dataclasses.replace(configuration.config(), only_mutate=only)
    PytestRunner.run_tests = _run_fastest_first  # type: ignore[method-assign]
    return cli()


if __name__ == "__main__":
    sys.exit(main())
