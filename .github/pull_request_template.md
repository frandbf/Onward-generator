## Scope

<!-- What this change does, and what it deliberately leaves out. -->

## Checks

- [ ] `task ci` passes locally (lint, typecheck, shellcheck, actionlint, smoke, tests).
- [ ] `task mutate` passes: every surviving mutant has a test that kills it, or `# pragma: no mutate: <reason>` when the edit is not observable.
- [ ] This change does not edit both `src/` and a quality gate (`task guard`).
- [ ] Every new `noqa`, `type: ignore` or `pragma: no mutate` names its code and gives a reason.
- [ ] Documentation is updated when a contract or a behaviour changed.

## Results

<!-- Paste the real counts: tests passed, mutants killed/survived. -->
