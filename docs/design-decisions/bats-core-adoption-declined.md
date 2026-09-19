# bats-core adoption declined

*2026-09-18.*

The shell surface is 25,445 tracked lines across 110 files, listed by `scripts/list-shell-files.sh`. It includes two shared libraries, `claude/.claude/hooks/_lib.sh` and `claude/.claude/hooks/_config.sh`. Every test of it runs through pytest. The question is whether bats-core should sit alongside that suite, on the hypothesis that it would give better coverage, better performance, or both. It should not. Each motivation fails against primary sources and this repo's own tree.

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
- Some are referenced only from test files. Three of those are test seams: `_lib_review_only_agents`, `_lib_no_gate_release_agents`, and `_lib_reviewer_persona_agents`.
- Two functions, `_lib_command_concludes_commit` and `_lib_command_concludes_marker_gated_commit`, are review-gate predicates with no production caller. They are a known residual the guard does not track.
- The guard cannot tell a test seam from a gate predicate that nothing invokes.

## The performance hypothesis points the wrong way

- A local, unreproduced observation put `sys` time at roughly 85-90% of `user` time. It points to a suite bound by fork and exec.
- Two causes contribute, and neither is reachable from a test framework.
- Contract-boundary tests exec a real process because the contract under test is the process boundary Claude Code invokes (stdin JSON, stdout JSON, exit code). That costs the same in any language.
- The larger function-level share execs `bash -c` because the functions are written in Bash.
- Rewriting 49 hooks in Python is disproportionate to a test-framework question.
- Hooks fire synchronously on every tool call, so a Python interpreter's cold start may be why the hooks are Bash. That cost is unmeasured here, and the disproportion of the rewrite carries the argument on its own.
- bats execs the same Bash artifacts and adds a subshell per `run`, so it removes no forks.
- The suite already runs `-n auto`, with a CI `timing` / `-n0` serial split.
- The ratio is directional, and the causes above carry the argument. Absolute wall-clock time is not cited, because it varies with machine load and CPU count.

## The dependency bar is repo precedent, not a rule

Nothing in `CLAUDE.md` forbids a mandatory non-pip system package. The precedent is what makes bats-core a new kind of dependency.

- bats-core ships no PyPI wheel.
- Its installation reference lists a distro package, Homebrew, npm, a source clone, or Docker.
- Its tutorial presents a git submodule as the quick installation, and an action named `bats-core/bats-action` exists in the bats-core organization.
- The decision does not rest on the dependency ground. The white-box, coverage, and performance arguments carry it.
- Its parallelism additionally needs GNU parallel or shenwei356/rush, and it does not guarantee test ordering.
- `requirements-dev.txt` holds five pinned wheels, and ShellCheck arrives as the `shellcheck-py` wheel.
- The only non-pip CI install is `apt-get install -y stow direnv`.
- `.github/workflows/tests.yml` installs `stow` and `direnv` because tests exercise the real binaries rather than a stub. bats would be the first system package that is a test vehicle rather than a subject under test.

Sources, as of 2026-09-19: bats-core `docs/source/installation.rst`, `docs/source/tutorial.rst` (lines 16-38), and the usage documentation on parallel execution and ordering, all at github.com/bats-core/bats-core, and the action's repository at https://github.com/bats-core/bats-action. `pypi.org/pypi/bats-core/json` returns 404. `shellcheck --help` (0.11.0) lists `sh, bash, dash, ksh, busybox` for `--shell` and no bats dialect. The open ShellCheck `.bats` issues include github.com/koalaman/shellcheck/issues/2041, /3222, /2873, /3263, /3229, /3247, and /3509.

ShellCheck's `.bats` support is undocumented in `--shell`'s help output and carries several open false-positive issues. Adoption would mean unlinted test files or a growing per-file suppression list.

## Optional, degrades-gracefully adoption is declined too

The strongest shape installs bats unconditionally in CI through the existing apt step. The merge-gating signal stays uniform and only local runs vary, the same asymmetry the repo accepts for `stow` and `direnv`. It still buys nothing, since the white-box capability exists, no file lacks a test reference, and the performance case is negative. Optionality removes an objection to adoption. It does not supply a reason for it.

## Reconsideration trigger

Two independent axes. The capability axis needs all three conditions:

1. A specific named function in `_lib.sh` or `_config.sh` cannot be exercised from Python via `bash -c '. lib; fn'`, with the reason stated concretely. Today the count is zero.
2. bats-core becomes installable from `requirements-dev.txt` alone (a maintained PyPI wheel, on the `shellcheck-py` precedent, published by the upstream org or a named, vetted repackager; the PyPI name `bats` is an unrelated project, so a name match is not evidence), or a maintainer opens a new design-decision file proposing to widen the apt-get precedent to a test vehicle, and that file is reviewed and merged.
3. ShellCheck documents `.bats` in `--shell`'s help output and the open `.bats` false-positive issues close.

The performance axis has one condition: a profile of the CI `-m "not timing"` pass attributes the majority of its time to pytest's own per-test overhead rather than to subprocess fork and exec. A wall-clock threshold is deliberately not the trigger, because absolute time grows with test count and would fire for a cause bats cannot address.

If adoption is ever pursued, `claude/.claude/scripts/select-tests.py` needs to learn a second runner first. `select_pytest_targets` and `build_pytest_argv` only construct pytest argv, and an unmatched `.bats` path falls open to the full pytest suite without ever executing it. Issue #1043 tracks that work, gated on this trigger, and it closes unread if the trigger never fires.

## Related findings and guards

Two findings are orthogonal to the framework choice and have their own issues.

- #1042 evaluates decomposing `install.sh` behind a sourcing guard. Its 18 fixture-marker pairs exist because its top level mutates `$HOME`, which bats' `load` could not source safely either.
- #1041 tracks a shared white-box helper for the four distinct sourced-lib invocation shapes. It is one unit of work with migrating the call sites.

`test_reviewer_persona_set_is_review_only_roster_minus_harness_builtins` in `test_lib.py` guards the roster seam around `_lib_reviewer_persona_agents`. `test_shell_lib_function_liveness.py` is a zero-reference tripwire over the two shared libraries. Each docstring carries its mechanics and scope.

Scoped test runs select tests through `select-tests.py`'s rule table, and some `.py` edits outside `claude/.claude/hooks/` do not select the guard. The rule table is canonical for which ones, and issue #1045 tracks the gap.
