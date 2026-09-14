# `helpers.py` is not split by test size

*2026-09-10.*

Splitting `claude/.claude/tests/helpers.py` into small/medium/large modules, following Google's Test Sizes framing, was considered in `.claude/plans/test-suite-boundary-and-selective-ci.md` and rejected. See `ci-stays-an-unconditional-full-suite-backstop.md` for the companion decision this one was weighed alongside.

The fan-out argument is the one that holds regardless of anything else: `helpers.py` is imported by 95 test files across all four test domains (`hooks/tests/`, `scripts/tests/`, `skills/tests/`, `plugins/lovable-cloud/tests/`). Splitting it by size doesn't change that import graph — the narrowest honest rule for either resulting half still has to target all four domains' test directories, which is `FULL_SUITE_TARGETS` minus two files. Nothing is saved; both halves would fan out identically.

The `pytest --collect-only` structural check floated as the enabling argument for a split doesn't close the gap either, on its own terms. Collection imports each test module; it doesn't execute test bodies. A payload builder's contract is the dict it returns, so a changed key, default, or nested shape in a function like `bash_input` leaves every signature and name intact — it passes collection and fails only at run time, in whichever domain's tests consume that key. Collection catches a call-shape change only where the call sits at module scope (e.g. inside a `pytest.mark.parametrize` argument), which is a subset of the risk this check was meant to cover, not the whole of it.

Google's Test Sizes line — Small tests run in one thread and one process with no external resources; Medium and Large progressively relax that — does apply here, but as a description of this suite rather than a lever on it. The systems under test are shell scripts under `claude/.claude/hooks/` and CLI entry points under `claude/.claude/scripts/`, invoked by path as subprocesses; testing them end to end is the point, not an accident. A repo-wide grep for `run_hook|run_skill_command|subprocess\.run|subprocess\.check` across `**/tests/test_*.py` matches 120 files. There is no substantial Small tier to extract from `helpers.py` or from the suite it serves.

The same plan inverted the calculus for `GLOBAL_TRIGGER_PATHS` itself, without splitting anything. `helpers.py`'s own path matches `CLAUDE_TESTS_DIR`'s `DOMAIN_RULES` row directly, and also matches the cross-domain exception that adds `test_ticket_reference_discipline.py`. Without `GLOBAL_TRIGGER_PATHS`, that pair is `helpers.py`'s entire selection — a severe under-selection for a module 95 files import across four domains. `select_pytest_targets` checks `GLOBAL_TRIGGER_PATHS` before it runs the domain-matching loop, so `helpers.py` still short-circuits to the full suite today. `select-tests.py`'s own comment on the `GLOBAL_TRIGGER_PATHS` entry and `test_select_tests.py`'s `test_helpers_py_global_trigger_forces_full_suite` record this as a load-bearing regression guard rather than an already-provable fact.

Registering `small`/`medium`/`large` pytest markers per the same framing was declined alongside the split, for a reason independent of all of the above: it would edit `pyproject.toml` — a `GLOBAL_TRIGGER_PATH` in its own right — and annotate 100+ test files, while changing nothing about what CI or `select-tests.py` actually runs. The tier distinction is recorded here in prose instead of as a marker, since no mechanism currently reads a size tag to make a selection decision.

**Revisit** if any of:

- A majority of the suite stops spawning subprocesses via `run_hook`/`run_skill_command`, narrowing the case for testing `helpers.py`'s subprocess-spawning half end to end.
- The repo gains a content-hash-based test cache, which would make per-module selection a property of what a module's tests actually touch rather than of its declared domain.

## Sources

- Google Testing Blog, *Test Sizes* — https://testing.googleblog.com/2010/12/test-sizes.html — the origin of the Small/Medium/Large framing.
- Abseil, *Software Engineering at Google*, ch. 14 — https://abseil.io/resources/swe-book/html/ch14.html — "Small tests are restricted to one thread, one process, one machine."
- `.claude/plans/test-suite-boundary-and-selective-ci.md` — the Approach section's fan-out, collection-limit, and Test-Sizes findings, and the tier-marker decline in Out of scope.
- `.claude/plans/selective-test-runs.md` — the original `select-tests.py` design this plan builds on.
- `claude/.claude/scripts/select-tests.py` — `CLAUDE_TESTS_DIR`'s `DOMAIN_RULES` row, `GLOBAL_TRIGGER_PATHS`, and `select_pytest_targets`'s trigger-before-domain-matching order.
- `claude/.claude/tests/helpers.py` — the 95-importer module these findings concern.
- `ci-stays-an-unconditional-full-suite-backstop.md` — the companion decision on why `select-tests.py`'s rule table doesn't take CI's gating role either.
