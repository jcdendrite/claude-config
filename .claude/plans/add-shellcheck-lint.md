# Add ShellCheck as a contributor lint tool

## Context

**Goal:** lint every shell script in this repo with ShellCheck, run from the
existing contributor venv and enforced in CI alongside `ruff`.

The repo ships 55 tracked shell scripts — 30 Claude Code hooks, 11 plugin
hooks/libs, 6 executables under `claude/.local/bin/`, 4 scripts, both
installers, a statusline script, and a plugin helper. Several run destructive
git operations. None has ever been linted; `ruff` covers only the Python
under `claude/.claude/`.

**Why now:** `cleanup-merged-branches.sh` — an ~800-line script that runs
`git push origin --delete` across ~37 repos — shipped a quoting defect that
survived two rounds of specialist review. A backtick inside a double-quoted
string holding Python source was parsed by bash as command substitution,
silently deleting the text and emitting `syntax error: unexpected end of
file` on every call. Two humans and four reviewer agents read that diff and
missed it; a linter would not have.

The repo's own `claude/.claude/rules/shell-script-conventions.md` already
prescribes this: *"Run `shellcheck` (CI or pre-commit) — mechanically catches
the quoting, `set -e`, and portability issues below; the highest-leverage
single addition for a script-heavy repo."* This plan closes the gap between
that stated convention and the absence of any enforcement.

**Intended outcome:** `shellcheck` is a pinned dev dependency, the repo is
clean at ShellCheck's default severity, and CI fails on new findings.

## Approach

### Scope: all 55 tracked shell files

`git ls-files '*.sh'` returns 48. Seven more are shell with no extension
(`claude/.local/bin/*` ×6, `plugins/lovable-cloud/scripts/new-migration`) and
a `*.sh` glob misses them silently — the same limitation
`shell-script-conventions.md` names: *"The `**/*.sh` glob can't read the
shebang."* Discovery must be extension **or** shebang.

Repo-wide rather than mirroring the `ruff check claude/.claude/` boundary,
because the measured cost is small and the excluded trees are not low-risk:
`plugins/*/hooks/` are PreToolUse gates that can deny tool calls, and
`install.sh` runs on every contributor's machine.

**Measured baseline** — ShellCheck 0.11.0 (`shellcheck-py==0.11.0.1`),
`-x -P SCRIPTDIR`, all 55 files: **26 findings, 0 errors**, ~9–10s wall clock.
Independently reproduced by `staff-platform-engineer` (26 confirmed; an
earlier 29 was a measurement artifact).

| Min severity | Cumulative findings |
|---|---|
| `error` | 0 |
| `warning` | 12 |
| `info` | 21 |
| `style` (ShellCheck default) | 26 |

### Severity stays at ShellCheck's default (`style`)

Verified from the ShellCheck manpage: *"Valid values in order of severity are
error, warning, info and style. The default is style."*

Keeping the default is a deliberate choice, not an omission. The motivating
bug is caught as **SC2006** (*"Use `$(...)` notation instead of legacy
backticks"*), which ShellCheck classifies as **style**. Reproduced directly:

```bash
python3 -c "
# use the `foo` helper and $(bar) below
"
# -> SC2006 (style)
```

Raising the floor to `-S warning` would drop 14 of 26 findings and silence
the exact defect class that motivated adopting the tool. No `-S` flag is set.

### Configuration lives in a repo-root `.shellcheckrc`

```
external-sources=true
source-path=SCRIPTDIR
```

Equivalent to `-x -P SCRIPTDIR`; the manpage documents both as `.shellcheckrc`
directives. Verified: it drops SC1091 from 26 findings to 4, and resolves
relative to the *file under test*, not the caller's cwd — confirmed working
with cwd at `/`, so CI, an editor integration, and an ad-hoc
`shellcheck path/to/one.sh` all behave identically with no flags.

This is the single-source-of-truth choice: flags repeated across the CI step,
the root `CLAUDE.md` command block, and contributor muscle memory would drift.

### CI: a step in the existing `tests` job, not a new job

Mirrors how `ruff check` is already wired (`.github/workflows/tests.yml:102`)
— a step inside the single `tests` job, gated by a detect output.

**Alternative set aside: a separate `shellcheck` job with its own path
filter.** Not expressible as described. Per GitHub's workflow-syntax
reference, `paths`/`paths-ignore` are valid only under `on.<push|
pull_request>`; a job cannot carry its own path filter. Achieving it needs a
second workflow file, which walks into a documented failure mode:

> "If a workflow is skipped due to path filtering, branch filtering or a
> commit message, then checks associated with that workflow will remain in a
> 'Pending' state. A pull request that requires those checks to be successful
> will be blocked from merging."
> — GitHub Docs, *Skipping workflow runs*

A path-filtered required check blocks every PR that does **not** touch shell.
This repo already avoids that trap: `tests` has no `on: paths` filter and
instead detects changed paths *inside* the job with step-level `if:`. That is
the documented workaround, and it is why `ruff` is a step. Branch protection
currently lists exactly one context (`required_status_checks.contexts:
["tests"]`), so a second job would also be advisory-only until manually
registered — the footgun the workflow header comment already warns about.

**Gating: a second detect output.** The existing `REGEX` encodes
"directories containing test files or read by tests" and is documented as
such. Widening it would conflate two questions and run the ~90s pytest suite
on unrelated plugin-hook shell edits. Instead the detect step emits a second
output, `shell_changed`:

```
SHELL_REGEX='(\.sh$|^claude/\.local/bin/|^plugins/lovable-cloud/scripts/|^\.shellcheckrc$|^scripts/list-shell-files\.sh$)'
```

`.shellcheckrc` must be in the pattern — editing it changes findings without
touching any `.sh` file.

**Toolchain gating is the subtle part.** `setup-python` (line 87) and
`Install dependencies` (line 91) are currently gated on `changed == 'true'`,
and `pip install -r requirements-dev.txt` is the only thing that puts
`shellcheck` on PATH. Roughly half the 55 discovered files are *outside* the
existing `REGEX` — all of `plugins/lovable-cloud|npm-semver|plugin-semver/`,
`claude/.local/bin/`, `install.sh`, `claude/.claude/statusline-command.sh`.
A PR touching only those yields `shell_changed=true, changed=false`, and the
shellcheck step would fail with `shellcheck: command not found`. Both steps
must therefore be gated on:

```yaml
if: steps.detect.outputs.changed == 'true' || steps.detect.outputs.shell_changed == 'true'
```

This is a blocker, not a polish item — it breaks on the first PR that touches
a plugin hook, which is precisely the risk class motivating repo-wide scope.

**Both detect-step exit paths must emit `shell_changed`.** The step returns
early when `BASE` is the zero SHA or is unresolvable locally, emitting
`changed=true` and failing open into the test suite. A `shell_changed`
computed only at the end of the step is therefore *absent* on that path, not
`false` — and an absent output never equals `'true'`, so the shellcheck step
silently skips while CI reports green. The early return must set
`shell_changed=true` alongside `changed=true`, so the fail-open posture is
identical for both gates. This is the structural-sibling case: a new output
added downstream of a pre-existing early return inherits nothing from it.

**`timeout-minutes: 2` → `3`.** Recent runs consume 78–99s of the 120s budget
(pytest alone is 72–92s). Adding ~10s risks intermittent timeouts. Pre-existing
tightness the new step surfaces rather than causes.

**PATH parity check.** `ubuntu-24.04` runner images may ship a system
`shellcheck` at `/usr/bin`. The step must print `which shellcheck &&
shellcheck --version` so a PATH-resolution surprise is visible in the log
rather than silently linting with an unpinned version.

### Discovery: one production script, independently re-checked in the test

Discovery lives in **`scripts/list-shell-files.sh`** (repo root, outside the
`claude/` stow package — it is repo tooling, not user config). It emits
NUL-separated paths: `git ls-files -z`, take `*.sh` by name, take the rest by
shebang match. The pattern must handle all three forms present
(`#!/usr/bin/env bash`, `#!/bin/bash`, `#!/bin/sh`); an `env`-only pattern
silently drops `plugins/lovable-cloud/scripts/new-migration`.

An explicit hardcoded list in CI would convert "is everything linted?" into
"did the author remember?" — the wrong shape for a lint gate. A single script
invoked by both CI and the test avoids the earlier draft's hand-wave that
"bash consumes a Python helper," which is not a thing bash can do.

ShellCheck exits `3` ("invoked with bad syntax") on an empty file list rather
than passing vacuously, so a discovery regression fails the step. That exit
code must not be swallowed with `|| true`.

**Known residual gap (document in the PR, do not paper over):** discovery is
content-based (shebang) while the CI gate is necessarily path-based. A new
extensionless script in a directory not yet in `SHELL_REGEX` will not trip
`shell_changed`, so it goes unlinted until some other matching file changes.
The plan is emphatic about failing loud elsewhere; this asymmetry deserves one
honest sentence rather than silence.

## Critical files

**Create**

- `.shellcheckrc` (repo root) — the two directives above.
- `scripts/list-shell-files.sh` — discovery, NUL-separated output. Invoked by
  the CI step and by the tests. Self-linting (it is itself a `.sh` file).
- `claude/.claude/hooks/tests/test_shellcheck.py` — see Testing below.

**Modify**

- `requirements-dev.txt` — add `shellcheck-py==0.11.*`, matching the existing
  wildcard-minor style (`pytest==8.*`, `ruff==0.6.*`). The `X.Y.Z.N` scheme is
  upstream ShellCheck `X.Y.Z` plus a packaging revision, so `0.11.*` pins the
  upstream minor and floats packaging fixes.
- `install-dev.sh` — extend `check_venv_healthy()` (line ~57) to probe
  `shellcheck --version`, and add shellcheck to the version line and the
  `Run lint:` hint. **Reuse:** the single `check_venv_healthy` definition is
  called by both the detect and verify sites, so one edit correctly forces
  heal-and-recreate for contributors whose `.venv` predates this change. No
  change needed to the `pip install -r` step.
- `.github/workflows/tests.yml` — `shell_changed` output; `setup-python` and
  `Install dependencies` re-gated on `changed || shell_changed`; a
  `Shellcheck` step after `ruff check`; `timeout-minutes: 3`. Do **not**
  rename the `tests` job (breaks the required-check context).
- `claude/.claude/scripts/tests/test_install_dev.py` — add a case covering the
  new health probe (healthy only when shellcheck present; heal-and-recreate
  when absent). This file already exercises `check_venv_healthy()`.
- Root `CLAUDE.md` "## Commands" — a `shellcheck` line beside `pytest`/`ruff`.
  Command only; no flags (they live in `.shellcheckrc`).
- `README.md` — only if it enumerates dev tooling; check before editing.
- **Four `.claude-plugin/plugin.json` version bumps** — see below.

### Plugin version bumps are mandatory, not optional

The lint fixes touch files inside four plugin directories:
`plugins/lovable-cloud/` (`hooks/_lib.sh`, `hooks/validate-migration-filename.sh`,
`lib/token-path.sh`, `scripts/new-migration`), `plugins/npm-semver/`,
`plugins/plugin-semver/`, and `plugins/skill-management/` (each
`hooks/_lib.sh`, plus the two `require-*-version-bump.sh` SC2181 fixes).

Per `.claude/rules/review-pipeline-dispatch.md`, any file under a plugin
directory triggers `plugin-semver`, which is **hook-enforced**:
`require-plugin-version-bump.sh` blocks `git commit` unless the plugin's
`version` was strictly raised since the branch's merge-base. Without these
bumps the commit is refused outright.

Current versions — bump each per the `plugin-semver` skill (these are
lint-only fixes with no behavior change, so patch):

| Plugin | Current |
|---|---|
| `lovable-cloud` | 3.2.1 |
| `npm-semver` | 1.0.0 |
| `plugin-semver` | 1.1.1 |
| `skill-management` | 2.4.1 |

`plugins/claude-hook-review/` has no shell files and is not touched — leave
its version alone.

Note the ordering wrinkle: this PR edits `plugin-semver`'s own
`require-plugin-version-bump.sh` (SC2181), so it is the gate and the subject.
Run `/plugin-semver` at implementation time rather than hand-editing versions.

### Fixing the 26 findings

**Read the surrounding code before applying any ShellCheck suggestion.** The
first draft of this plan graded three findings from the message text alone and
got one exactly backwards — see SC2254 below. ShellCheck's suggested remedy is
a hypothesis about intent, not a verdict.

| Code | n | Disposition |
|---|---|---|
| SC2034 `COMMAND appears unused` in 5 `_lib.sh` copies | 5 | **Suppress.** Verified: `_lib.sh` sets `$COMMAND` for 10+ sourcing hooks ShellCheck cannot see. `# shellcheck disable=SC2034` with a rationale naming the specific assertion — "set for hook scripts that source this file and reference `$COMMAND`" — not a bare disable, so a future editor of one diverging copy has something concrete to re-verify. Applied to all 5 sibling copies. |
| SC1091 unresolvable `source` | 4 | **Fix** via `# shellcheck source=` directives (`marker.sh` sources through `$HOME`; the lovable-cloud files use plugin-root-relative paths). Real coverage gain — an unfollowed source means ShellCheck is blind to that file. |
| SC2254 in `deny-data-file-reads.sh:135` | 1 | **Suppress — do NOT apply the suggested quoting.** `$line` is an intentional user-authored glob from `~/.claude/data-file-read-guard.md`; lines 131-133 document that `**` collapses to `*` to match at any depth. Quoting it forces literal matching and silently breaks every wildcard rule in every user's guard file — a false negative on a deny gate. The bypass here would be *introduced by the fix*, not present in the code. Suppress with that rationale, and say so explicitly in the PR so a later reviewer doesn't "helpfully" quote it. |
| SC2181 `$?` instead of `if ! cmd` | 4 | **Fix.** Two sibling pairs in `require-npm-version-bump.sh` and `require-plugin-version-bump.sh`. |
| SC2016 single-quoted `$` | 4 | **Inspect each.** Intentional in help/message text → suppress with rationale; a real missed expansion → fix. |
| SC2034 unused `BOLD`, `i`, `MIGRATION_TOKEN_DIR` | 3 | **Inspect.** Dead → delete; consumed by a sourcer → suppress. |
| SC2207 in `require-worktree-for-git-writes.sh:182` | 1 | **Fix** with a `while IFS= read -r` loop over a process substitution — **not** `mapfile`, which is bash-4+ and violates this repo's tested bash-3.2/macOS compatibility guard (`claude/.claude/scripts/tests/test_no_bash4_constructs.py`). This plan originally specified `mapfile`; that was wrong and the guard caught it. Verified latent-but-unreachable today: the source is a static 47-token allowlist in `_lib.sh` with no whitespace or glob metacharacters. Cheap insurance if that list ever becomes externally sourced — not a bypass fix, and comments must not describe it as one. |
| SC1007 ×2 in `check-branch-divergence.sh:81` | 2 | **Fix cosmetically** to `SSH_ASKPASS='' GIT_ASKPASS=''` — semantically identical, purely reader clarity. These are deliberate empty-string prefix assignments (documented lines 74-77) suppressing credential prompts, not typos. Nothing is silently empty. This hook is a SessionStart advisory that "never blocks, never acts" — not a gate. |
| SC2143, SC2059 | 2 | **Fix** individually. |

No finding in this set is a live security defect. The PR should say that
plainly rather than implying the lint sweep uncovered a vulnerability.

**Commit separation.** Land the CI wiring and the behavior-affecting fixes as
separate commits within the one PR, so a targeted `git revert` of the gate (if
it misbehaves post-merge) does not also revert the hook edits.

## Testing

Four tests in `claude/.claude/hooks/tests/test_shellcheck.py`, plus one in the
existing `test_install_dev.py`. Existing pattern to follow:
`claude/.claude/hooks/tests/test_doc_counts.py` (already walks tracked repo
files); shared helpers at `claude/.claude/tests/helpers.py`.

1. **Discovery completeness — independently computed.** Must not recompute the
   expected set with the same regex as the implementation; that only proves
   determinism. Use a *deliberately looser* net (any `#!` line mentioning
   `sh|bash|dash|ksh|zsh`, applied to every tracked file) and assert discovery
   is not missing anything it finds. Pin further with a hardcoded frozenset of
   the 7 known extensionless paths asserted as a subset — a hardcoded list is
   correct *in a test* (it is the known-good universe) even though it is wrong
   in CI.
2. **Bash/Python differential.** Run `scripts/list-shell-files.sh` as a
   subprocess and compare its output to an independent Python reimplementation
   of discovery. This catches ERE-vs-`re` divergence directly, which a shared
   helper cannot, since the CI side is bash regardless.
3. **SC2006 regression canary.** Write the motivating construct to `tmp_path`
   and assert `shellcheck` exits nonzero and reports `SC2006`. The
   lint-everything test only proves today's tree is clean; it can never prove
   the gate would catch a reintroduction. A severity-floor change or
   `.shellcheckrc` misconfiguration would silently disarm the gate without
   this test.
4. **`.shellcheckrc` effectiveness.** Run `shellcheck` on a file with a
   `source` line with cwd set to `/`, and assert SC1091 does not fire —
   pinning both that the file exists and that it resolves file-relative.
5. **Detect-step control flow, executed — not pattern-matched.** Assertions
   against the extracted `SHELL_REGEX` prove only what the pattern matches;
   they cannot see a branch that returns before the pattern is ever evaluated,
   which is exactly the shape of the fail-open gap above. Parse the step's
   `run:` block out of the workflow with `pyyaml` (locating it by `id: detect`,
   not by regexing YAML), substitute the `${{ }}` expressions, and execute it
   under `bash` against a constructed throwaway git repo with `GITHUB_OUTPUT`
   pointed at a temp file. Assert `shell_changed=true` for both a resolvable
   base with a shell file changed **and** a zero-SHA base, and `false` for a
   resolvable base touching neither shell nor test paths.

Where a test re-derives a pattern the workflow feeds to `grep -E`, match with
`grep -E` itself rather than recompiling the string as a Python regex — the
same ERE-vs-`re` divergence named in test 2 applies to the CI gate's pattern,
and a dialect-only construct would silently reinterpret instead of erroring.

Plus **repo-clean**: `shellcheck` over the discovered set exits 0.

**The skip path must not silently green CI.** Guarding the shellcheck tests on
"binary absent" is right for a contributor who hasn't re-run `install-dev.sh`,
but in CI a failed `shellcheck-py` install would then skip rather than fail,
and CI reports green with zero linting performed — reproducing the
silent-success failure mode by another route. Assert the binary is present
when `CI` is set, so the skip path is unreachable there.

**Runtime:** the ~10s full lint runs unconditionally in the suite. `pyproject.toml`
defines no `markers` and the repo has no `slow` convention; `test_doc_counts.py`
already does an unconditional whole-repo walk. Accepting the fixed cost is
consistent with existing practice — deliberately not introducing this repo's
first marker convention as a side effect of adding a linter.

### Dependency provenance — `shellcheck-py`

Required by the global "Ground every choice" rule for new third-party
dependencies; this goes in the PR description.

- **Publisher:** the `shellcheck-py` GitHub org. PyPI metadata still names the
  original author, but recent commits are predominantly Anthony Sottile
  (`pre-commit`, `pyupgrade`), an active, recognized Python-tooling maintainer.
- **Binary provenance:** wheels are built in CI by the `setuptools-download`
  plugin, which fetches from `github.com/koalaman/shellcheck` releases with a
  **pinned `sha256` per platform** in `setup.cfg`. Installing a matching wheel
  performs no network fetch.
- **Versioning:** `0.11.0.1` = upstream ShellCheck `0.11.0` + packaging
  revision `1`. No lag against upstream.
- **Maintenance:** repo active (last push July 2026), 0 open issues, releases
  track upstream's ~annual cadence.
- **Vulnerabilities:** none found for `shellcheck-py` (PyPI) or upstream
  ShellCheck in OSV.dev, the GitHub Advisory Database (GraphQL + web search),
  or upstream's own security-advisories page.
- **Wheel coverage:** linux x86_64, macOS arm64 + x86_64, win_amd64 — covers
  the `ubuntu-24.04` runner and current contributor machines. **Gap:** no Linux
  aarch64 wheel, so an ARM64 Linux host falls back to the sdist and *does*
  fetch at install time. Note in the PR; affects no current machine.
- **Alternatives set aside:** `apt`/`brew` (versions drift between CI and
  contributor machines, and sudo was ruled out); the official Docker image
  (adds a container runtime for a linter); hand-rolled tarball download
  (re-implements the checksum-verified fetch this package already automates).

## Verification

From inside the worktree (the `.venv` is at the main tree root, three levels up):

```bash
../../../.venv/bin/pip install -r ../../../requirements-dev.txt
../../../.venv/bin/shellcheck --version                           # expect 0.11.0
../../../.venv/bin/pytest claude/.claude/ -q
../../../.venv/bin/ruff check claude/.claude/
```

Then:

1. **Clean tree:** discovery piped to `shellcheck` exits 0 over all 55 files.
2. **The `changed=false, shell_changed=true` path works.** The one thing most
   likely to break on first CI run. Verify on the PR by confirming the
   shellcheck step ran and found its binary; if this PR's own diff sets
   `changed=true` (it touches `requirements-dev.txt`), construct the case
   deliberately — a scratch branch touching only `plugins/npm-semver/hooks/*.sh`
   — rather than assuming it works.
3. **Guard-file globs still match.** After the SC2254 suppression, confirm a
   `**`-shaped line in `data-file-read-guard.md` still denies a nested path.
   This is the finding the first draft got backwards; verify behavior, not
   just that the lint passes.
4. **Empty-list safety:** `shellcheck` with no file arguments exits 3, not 0.
5. **Fresh-install path:** `./install-dev.sh` in a scratch clone provisions
   shellcheck and the health probe passes.

## Out of scope

- **`cleanup-branches-trust-reachability`** — an existing branch changing the
  same script (Tier-B auto-delete, removes the `--yes` prompt). Deliberately
  sequenced *after* this work. Do not touch, rebase, or merge it.
- **The deferred `git rev-parse` finding from #459** — already marked with an
  in-code comment; not a ShellCheck finding.
- **Refactoring the 5 near-duplicate `_lib.sh` copies.** ShellCheck flags the
  same SC2034 in all five, which is a tempting DRY signal, but the repo
  requires plugins to stand alone. Suppress uniformly; do not consolidate.
- **A `slow` pytest-marker convention** — considered and declined above.
- **Linting shell embedded in non-shell files** — heredocs in Python tests,
  `run:` blocks in the workflow. ShellCheck cannot see these; `actionlint` is
  the tool if ever wanted.
- **Formatting** (`shfmt`) — a different tool and a much larger diff.
