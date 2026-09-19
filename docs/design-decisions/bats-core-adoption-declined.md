# bats-core adoption declined

*2026-09-18.*

The shell surface is 25,445 tracked lines across 110 files, listed by `scripts/list-shell-files.sh`. It includes two shared libraries, `claude/.claude/hooks/_lib.sh` and `claude/.claude/hooks/_config.sh`. Every test of it runs through pytest. The question is whether bats-core should sit alongside that suite, on the hypothesis that it would give better coverage, better performance, or both. It should not. Each motivation fails against primary sources and this repo's own tree, and adoption would add a dependency this repo has no precedent for.

## White-box access already exists

The capability bats is proposed to add is sourcing a library and calling one function in isolation. The suite already works this way.

- The shape is `subprocess.run(["bash", "-c", f". {lib}; {call}"])`.
- 151 lines across 19 `*.py` files under `claude/.claude/hooks/tests/` contain `"bash", "-c"`.
- `claude/.claude/hooks/tests/test_lib.py` wraps the shape as `_run_lib_call`, which has 18 call sites.
- `test_lib.py` also wraps the variant that predefines `emit_deny` before sourcing as `_run_harness`.

## No shell file lacks a test reference for bats to fill

- 109 of the 110 tracked shell files have their basename mentioned in at least one test file's non-comment source.
- The exception is `scripts/dev/fork-topology-probe.sh`, which needs a person reading live Claude Code fork output, so no framework automates it.
- The suite carries no `xfail`, and no skip is annotated as a black-box limitation.
- The 109-of-110 figure measures reference, not execution. It shows that no file is unreferenced. It does not prove that each file is exercised.
- The white-box and performance arguments carry the decision independently of this figure.

The 103 functions in `_lib.sh` and `_config.sh` are a separate measure from file-level references.

- Every one of them has a reference within the liveness guard's scan scope: tracked shell and `.py` files, outside whole-line comments and `plugins/`.
- Five are referenced only from test files. Three are test seams: `_lib_review_only_agents`, `_lib_no_gate_release_agents`, and `_lib_reviewer_persona_agents`.
- The other two, `_lib_command_concludes_commit` and `_lib_command_concludes_marker_gated_commit`, are review-gate predicates with no production caller.
- The guard cannot tell a test seam from a gate predicate that nothing invokes.
- The guard does not track that count.

## The performance hypothesis points the wrong way

- A local observation over two full runs put `sys` time at roughly 85-90% of `user` time. No invocation is recorded and the source is not a tracked file, so the figure is directional and unreproduced. It points to a suite bound by fork and exec.
- Two causes contribute, and neither is reachable from a test framework.
- Contract-boundary tests exec a real process because the contract under test is the process boundary Claude Code invokes (stdin JSON, stdout JSON, exit code). That costs the same in any language.
- The larger function-level share execs `bash -c` because the functions are written in Bash.
- Rewriting 49 hooks in Python is disproportionate to a test-framework question.
- Hooks fire synchronously on every tool call, so a Python interpreter's cold start is an operational cost a shell script avoids.
- bats execs the same Bash artifacts and adds a subshell per `run`, so it removes no forks.
- The suite already runs `-n auto`, with a CI `timing` / `-n0` serial split.
- The ratio is directional, and the causes above carry the argument. Absolute wall-clock time is not cited, because it varies with machine load and CPU count.

## The dependency bar is repo precedent, not a rule

Nothing in `CLAUDE.md` forbids a mandatory non-pip system package. The precedent is what makes bats-core a new kind of dependency.

- bats-core ships no PyPI wheel.
- Its documented installs are a distro package, Homebrew, npm, a source clone, or Docker.
- Its parallelism additionally needs GNU parallel or shenwei356/rush, and it does not guarantee test ordering.
- `requirements-dev.txt` holds five pinned wheels, and ShellCheck arrives as the `shellcheck-py` wheel.
- The only non-pip CI install is `apt-get install -y stow direnv`.
- `.github/workflows/tests.yml` installs `stow` and `direnv` because tests exercise the real binaries rather than a stub. bats would be the first system package that is a test vehicle rather than a subject under test.

ShellCheck's `.bats` support is undocumented in `--shell`'s help output and carries several open false-positive issues. Adoption would mean unlinted test files or a growing per-file suppression list.

## Optional, degrades-gracefully adoption is declined too

The strongest shape installs bats unconditionally in CI through the existing apt step. The merge-gating signal stays uniform and only local runs vary, the same asymmetry the repo accepts for `stow` and `direnv`. It still buys nothing, since the white-box capability exists, no file lacks a test reference, and the performance case is negative. Optionality removes an objection to adoption. It does not supply a reason for it.

## Reconsideration trigger

Two independent axes. The capability axis needs all three conditions:

1. A specific named function in `_lib.sh` or `_config.sh` cannot be exercised from Python via `bash -c '. lib; fn'`, with the reason stated concretely. Today the count is zero.
2. bats-core becomes installable from `requirements-dev.txt` alone (a maintained PyPI wheel, on the `shellcheck-py` precedent), or a maintainer opens a new design-decision file proposing to widen the apt-get precedent to a test vehicle, and that file is reviewed and merged.
3. ShellCheck documents `.bats` in `--shell`'s help output and the open `.bats` false-positive issues close.

The performance axis has one condition: a profile of the CI `-m "not timing"` pass attributes the majority of its time to pytest's own per-test overhead rather than to subprocess fork and exec. A wall-clock threshold is deliberately not the trigger, because absolute time grows with test count and would fire for a cause bats cannot address.

If adoption is ever pursued, `claude/.claude/scripts/select-tests.py` needs to learn a second runner first. `select_pytest_targets` and `build_pytest_argv` only construct pytest argv, and an unmatched `.bats` path falls open to the full pytest suite without ever executing it. Issue #1043 tracks that work, gated on this trigger, and it closes unread if the trigger never fires.

## Related findings and guards

Two findings are orthogonal to the framework choice and have their own issues.

- #1042 evaluates decomposing `install.sh` behind a sourcing guard. Its 18 fixture-marker pairs exist because its top level mutates `$HOME`, which bats' `load` could not source safely either.
- #1041 tracks a shared white-box helper for the four distinct sourced-lib invocation shapes. It is one unit of work with migrating the call sites.

`_lib_reviewer_persona_agents` is an unconsumed test seam rather than dead code.

- It is the third member of a roster-accessor family. The other two, `_lib_review_only_agents` and `_lib_no_gate_release_agents`, are each called only from tests.
- It and `_lib_is_reviewer_persona`, which two live hooks call, both read the array `_LIB_REVIEWER_PERSONA_AGENTS`.
- `test_reviewer_persona_set_is_review_only_roster_minus_harness_builtins` in `test_lib.py` asserts the derivation of that array. `_lib_is_reviewer_persona` also has behavioral accept and reject tests in `test_lib.py`. The derivation test catches drift between the shell-side `Explore | Plan) continue` exclusion in `_lib.sh` and the Python-side `{"Explore", "Plan"}` literal.
- That test does not force a decision when a new harness built-in is added to `_LIB_REVIEW_ONLY_AGENTS`. The new name flows into both sides of the equality and is admitted silently.

`test_shell_lib_function_liveness.py` fails on any function defined in `_lib.sh` or `_config.sh` whose name has no reference within the guard's scan scope. It is a zero-reference tripwire, not a coverage guard and not a production-consumer check. Its module docstring states the scan rules and the residual cases it passes.

Scoped test runs do not select the guard when the only change is a Python file or test under `claude/.claude/scripts/`. Issue #1045 tracks that gap.
