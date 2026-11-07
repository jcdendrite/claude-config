# Architecture-test library adoption declined

*2026-11-06.*

The question is whether an ArchUnit-style Python library (import-linter, pytest-archon, PyTestArch, or tach) should replace or supplement the hand-written import-boundary tests for the `transcript_analysis` package. It should not. A prototype of one library (PyTestArch) could express only deny-style rules reliably, and the repo's stated rules mix deny, allow, and per-name shapes. Adding a second mechanism plus a dependency does not remove the hand-written checks that matter. The other three libraries were read about, not run.

## Most stated rules are already enforced by hand

- `docs/transcript-analysis-architecture.md` states the package's import rules in prose: the shim imports the package and never the reverse (with a named exception list), `corpus`, `pricing`, and `render` stay free of named siblings, and `session_pricing` imports only `corpus` and `pricing` plus the standard library.
- `claude/.claude/scripts/tests/test_transcript_analysis_import_cycles.py` builds the package import graph from `ast`, skipping function bodies, and checks for cycles. It carries a module-discovery guard, a seeded-cycle test, and a test per import form. It also runs a fresh-interpreter import check.
- `test_transcript_session_pricing.py` pins `session_pricing`'s allow-style rule with a static check (`test_module_imports_only_corpus_and_pricing_from_the_package`) and a fresh-interpreter check (`TestImportPath`).
- `test_transcript_analysis_cost_import_direction.py` pins the shim/`cost.py` boundary down to individual names. `test_transcript_package_module_prefixes.py` pins the by-module import discipline.
- `.claude/plans/close-cost-family-deferred-findings.md` rejected an import-linter contract earlier, because its contracts work at module-path granularity and some of these rules are about names. That is the plan's assertion, not something this record reproduced.

**Pins that are incidental or missing.** `TestImportPath` asserts the exact set of modules loaded when `session_pricing` is imported and run, so a module-level or exercised-path edge out of `corpus` or `pricing` fails it. That pin is a side effect of `session_pricing`'s import promise, not a test of the `corpus`/`pricing` rules, and it misses function-body imports on unexercised branches. No test found pins the `render` rule or the package-to-shim direction beyond `cost.py`. Closing those gaps is a separate change, and exactly what the `test-conventions` guidance asks a plan to do.

## What a library could replace

Only deny-style rows such as "`corpus` must not import `scope`", per the PyTestArch prototype. The remaining checks stay hand-written: the cycle check, the by-module import form, the per-name shim boundary, and the standard-library-only import. The hand-written tests already disagree on what counts as an import (the cycle test skips function bodies; the `session_pricing` and cost-direction tests walk the whole tree). A library would add a second definition next to those, plus a dependency, without removing the code that matters.

## Prototype

One prototype, one tool, one version: PyTestArch 5.1.0 with networkx 3.7, installed in a scratch virtual environment on Python 3.14, run against this repo's own `transcript_analysis` package on 2026-11-06. Nothing was added to the repo.

- **Deny rules worked.** Rules for `corpus`, `pricing`, and `render` passed on the real code. Two deliberately wrong rules failed, so the graph was not vacuous.
- **Deny rules were sensitive.** On a scratch copy, a deny rule forbidding `session_pricing` from importing `scope` passed on the unmodified file. It then detected all five seeded forms: `from pkg import scope`, `from pkg.scope import X`, `import pkg.scope as s`, an import inside a function body, and an import under `TYPE_CHECKING`. `scope` is a submodule of the package root that `session_pricing` imports legitimately, so the pass is not an artifact of unrelated targets.
- **The allow rule did not work.** "`session_pricing` imports only `corpus` and `pricing`" failed on correct code, because `from transcript_analysis import corpus, pricing` was also recorded as an import of the package root. Adding the root to the allowed list made four seeded sibling imports pass, since submodules count as part of their parent.
- **Module names carry the scanned directory's name as a prefix** (`scripts.transcript_analysis.corpus`), and a name absent from the graph raises `ModuleUnknown` instead of passing.
- **It sees function-body imports and `TYPE_CHECKING` imports.** The existing cycle test already counts `TYPE_CHECKING` imports (it skips only function bodies), so only function-body imports differ. That wider view is a benefit for the deny rows but would change the cycle test's definition of an import.

## Documentation reading

The other three tools were assessed from their primary documentation on 2026-11-06 and not run. The claims below were not reproduced.

- import-linter declares contracts in configuration and runs as a command-line step. The documentation describes rules at module granularity.
- pytest-archon runs as ordinary pytest tests.
- tach is configured in `tach.toml`, runs as a command-line step, and does not document a standard-library-only assertion.

The record does not link the sources, so re-read each project's documentation before relying on a claim here.

## Reconsideration trigger

Revisit if any of these holds:

1. A second package gains stated layering rules, or the architecture doc's stated import rules grow past a handful.
2. A rule shape appears that the hand-written helper cannot express and a library can.
3. A library treats a package-root import as distinct from imports of its submodules, so that allow-style rules become usable.

The guidance that survives the decision lives in `test-conventions/SKILL.md` § "Stated boundary rules require a mechanical check" and is tool-agnostic.
