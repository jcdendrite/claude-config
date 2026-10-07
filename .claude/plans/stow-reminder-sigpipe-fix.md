# Plan: stop PR-body gates from reading an early match in a large input as no match

## Context

Goal: stop `require-stow-reminder.sh` from falsely denying PRs whose `--body-file` is large and multi-line, and close the same SIGPIPE-under-pipefail hazard at the sibling body-scan sites, without adding a helper or any new mechanism.

Ask: "Let's go with your proposed plan." (engineer, replying to a roadmap whose PR A was described in the session's own wording as "the `require-stow-reminder.sh` SIGPIPE fix. It is small and independent"). On PR A's scope the engineer's typed answer was "Ask the architect" — the four-site boundary below is the architect consult's conclusion, not the engineer's decision.

Why now: a 38 KB PR body falsely denied by `require-stow-reminder.sh` during an earlier PR's merge gate (the first 20 KB of the same file did not reproduce). Landing that body needed a `gh api` PATCH workaround.

Problem: under `set -uo pipefail`, `printf '%s' "$X" | grep -q ...` returns pipeline status 141 when grep exits at the first match while printf is still writing; `if` reads that as no match. The failure needs multi-line input larger than the pipe capacity (64 KiB on Linux). Measured on bash 5.2.21 with GNU grep 3.11: no misses at 32 KB, about 60% misses at 64 KB, and every run missing from 100 KB. A single unbroken line never fails.

## Approach

Each of the four `printf … | grep -q` matches becomes `grep -q … <<< "$X"`. Every grep flag and pattern byte stays the same. Each hook also gets one durable comment, and each site gets one deterministic regression test that fails at the merge-base and passes on the branch. There is no helper, no change to matching semantics, and no change to a tier line or `docs/hooks.md`.

The exact replacements. Each keeps the existing line's indentation (`require-stow-reminder.sh:177` sits inside an `if` block, indented two spaces, and the other three are unindented):

`require-stow-reminder.sh:177`:

```bash
  if ! grep -qE '(--body([[:space:]=]|$)|--body-file|-F([[:space:]=]|$)|--template|-T([[:space:]=]|$))' <<< "$COMMAND"; then
```

`require-stow-reminder.sh:215`:

```bash
if grep -qE '(--fill(-first|-verbose)?|[[:space:]]-f([[:space:]]|$))' <<< "$COMMAND"; then
```

`require-stow-reminder.sh:221`:

```bash
if grep -qiE '(install\.sh|stow)' <<< "$SCAN_TARGET"; then
```

`deny-escaped-backticks-in-pr-body.sh:140`:

```bash
if grep -qF -- '\`' <<< "$SCAN_TARGET"; then
```

Add this comment once per hook, directly above `set -uo pipefail`, wrapped at about 80 columns as one comment block:

```bash
# Every grep -q in this file reads a here-string, never a pipe. Under
# pipefail, grep exiting at its first match while printf still writes a large
# multi-line input fails the pipeline, which reads as no match.
```

The comment goes at the `pipefail` line, not at each site. That line is the cause, and the rule covers the whole file. A per-site comment would put three copies of one fact into `require-stow-reminder.sh`. The follow-up PR's lint failure message becomes the canonical statement of the rule, and these two comments shrink to a pointer to it then.

Alternatives weighed:
- **bash `=~` or `case`**: rejected.
  - The `-i` at :221 would need `shopt -s nocasematch` toggling. `${var,,}` is bash 4+, and this repo targets macOS bash 3.2 (`_lib.sh:2159`).
  - `=~` anchors `^`/`$` to the whole string, not to each line, so every site's semantics would need a fresh review.
  - `case` alone would fit the fixed string at :140, but mixing primitives across the four sites gains nothing and gives the follow-up lint two shapes to accept.
- **`set +o pipefail` around each match**: rejected. It means four toggle pairs, and one missed restore silently changes every later pipeline.
- **Capture-then-test, as `_lib.sh:1742-1750` does**: rejected. It keeps the pipe and needs an `-o`/`-m` rewrite plus exit-status checks per site.
- **Dropping `pipefail` from both hooks**: rejected. It removes a guarantee from every future pipeline in each file to fix four lines.
- **A shared `_lib.sh` matcher**: rejected. A wrapper would only be grep with its arguments reordered, and it would touch a file every hook sources.

### Assumption ledger

**Root:** Under `set -uo pipefail`, a `printf | grep -q` match fails whenever grep exits at its first match while printf is still writing a large multi-line input. Four sites in two stowed PR-body gates read that failure as no match:
- Two false denies: `require-stow-reminder.sh:215` and `:221`.
- Two fail-opens: `require-stow-reminder.sh:177` and `deny-escaped-backticks-in-pr-body.sh:140`.

**Givens:**
- G1. Pipe semantics hold: a reader that exits early fails a writer that is still writing, and under `pipefail` that nonzero status becomes the pipeline's status. Reason: bash and the kernel's pipe implementation impose this.
- G2. The hooks cannot bound the size or line count of `tool_input.command` or of a referenced body file. Reason: the agent composing the Bash call and the PR body owns that input.

**Mechanisms:**
- M1. Here-string at all four sites, with grep flags and pattern bytes unchanged. Here-string is the lightest primitive that keeps grep's per-line ERE, `-i`, and `-F` semantics; the alternatives above fail as stated. anchors: root, row1, row2, row4, row6.
- M2. One one-line comment per hook, above `set -uo pipefail`. Until the follow-up lint lands, nothing else stops a future edit from reintroducing the pipe. anchors: row10.
- M3. One deterministic regression test per site, with the match on the first line of a 320 KB tail. Each test must fail at the merge-base (Verification step 1). anchors: root, row5, row8, row9.
- M4. Each test file defines its own copy of `LARGE_MULTILINE_TAIL`. This is DAMP test code, CLAUDE.md's named exception (1). The constant is not imported from another test module, and it is not hoisted into `helpers.py` in this change. anchors: row11.
- M5. One `### Fixed` bullet in `CHANGELOG.md`. Two stowed gates change consumer-visible verdicts, and that change goes live on `git pull`. anchors: row13.

**Rows:**
1. Both hooks run `set -uo pipefail`, without `-e`. [verified: require-stow-reminder.sh:76; deny-escaped-backticks-in-pr-body.sh:38]
2. What each site does on a missed match: [verified: require-stow-reminder.sh:176-179, 213-223, 240; deny-escaped-backticks-in-pr-body.sh:140-145]
   - :177 runs `exit 0` before any scan, so the gate is skipped for a body-changing `gh pr edit`.
   - :215 leaves commit messages out of `SCAN_TARGET`, so a reminder that exists only in a commit message (`--fill`) is missed and the gate falsely denies.
   - :221 falls through to `emit_deny`, a false deny.
   - :140 falls through to `exit 0`, so the escaped backtick is allowed.
3. These four are the only `grep -q` calls in either hook. The other pipelines (require-stow-reminder.sh:186-189 and :228; deny-escaped-backticks-in-pr-body.sh:89-94) have no reader that exits early, and nothing tests their exit status. [verified: full read of both hooks]
4. The here-string feeds grep the same bytes as today, plus at most one trailing newline. At :177 and :215 the input is identical, because both already pipe `printf '%s\n'`. At :221 and :140 the input gains one trailing newline. Neither pattern at those two sites anchors on `^`/`$` or can match an empty line. [verified: the four lines cited]
5. The failure needs multi-line input larger than the pipe capacity (64 KiB on Linux). The 32-line, roughly 320 KB tail is about five times past that, so it fails every run at the merge-base. [verified: plan-review probes, bash 5.2.21, GNU grep 3.11, 150 and 300 iterations per size: pipe form missed 0 of 150 at 4, 16 and 32 KB, 91 of 150 at 64 KB, and every run from 100 KB through 1 MB; here-string form missed 0 of 150 at every size; a single unbroken 320 KB line missed 0 of 50] The tail stays at 320 KB, and a later edit must not shrink it toward the Context paragraph's smaller sizes.
6. A here-string writer is never a member of the pipeline, so `pipefail` cannot see it, and grep's own exit status is the only status the `if` reads. On bash 5.2.21 a small here-string is written into a pipe by the process that then execs grep, and a large one goes through an unlinked temp file. [verified: plan-review `strace` probe, bash 5.2.21; bash 3.2 not tested, since no binary was available, and a macOS `/bin/bash` run of the probe would close it] Shipped gates already match with `grep … <<<`. [verified: deny-pii-in-commits.sh:584,607,623; deny-private-project-refs.sh:829,835]
7. If bash cannot create the here-string temp file (`/tmp` full or read-only, or a file-size rlimit), bash prints `cannot create temp file for here-document`, grep never runs, and the site reads no match. An unwritable `TMPDIR` alone does not trigger it, because bash 5.2.21 falls back to `/tmp`. The direction matches today's SIGPIPE miss at each site: :177 fails open, :215 and :221 falsely deny, and :140 allows. [verified: plan-review probe with `ulimit -f` in a scratch subshell, bash 5.2.21]
8. `_lib_command_invokes_tool_subcmd` (require-stow-reminder.sh:109,173) still recognizes the command on a 320 KB, 32-line input, so the :177 and :215 tests reach their target lines.
   - Its body contains no `grep -q` pipeline. [verified: _lib.sh:2202-2221]
   - The only `printf | grep -q` in `_lib.sh` is :3415, a single-line basename check outside these hooks' path. [verified: grep]
   - The same tail already drives a hook that uses this helper. [verified: test_enforce_marker_script_shape.py:2206-2231; enforce-marker-script-shape.sh:745]
   - Backstop: if detection failed, those two tests would not go red at the merge-base.
9. Naming trap. Three facts combine:
   - The marker check is a substring match for `stow`/`install.sh` over a scan target that includes the whole command. [verified: require-stow-reminder.sh:195,221]
   - `stow_repo` is `tmp_path / "stow-repo"`. [verified: test_require_stow_reminder.py:35]
   - pytest's `tmp_path` directory name embeds the test function name, sanitized (`\W` becomes `_`) and truncated to the first 30 characters. [verified: plan-review read of `_pytest/tmpdir.py:282-287`] Only the body-file test (:221) puts a `tmp_path` into the command string. `stow_repo` is used only as `cwd`.

   So a new test whose name has `stow` in its first 30 characters, or a body file placed under `stow_repo`, puts a marker into the command string. An allow would then no longer prove that the body-file or `--fill` path was read. `install.sh` cannot come from a name, because `.` becomes `_`. Every test name proposed below truncates to a prefix with no `stow`.
10. No mechanical check forbids `printf | grep -q` in hooks today. [verified: test_hook_alignment.py has no pipefail, here-string, or SIGPIPE check; its `grep -q` hits at :1053-1317 are unrelated fixture strings]
11. The precedent tail is `LARGE_MULTILINE_TAIL = "\n" + "\n".join(["x" * 10_000] * 32)`, and its comment says it must be multi-line. [verified: test_enforce_marker_script_shape.py:109-113]
12. `docs/hooks.md` needs no edit. Its two entries describe verdicts, not the matching mechanism. No tier line changes, so the count line and `test_doc_counts.py` are untouched. [verified: docs/hooks.md:70,87,140,142]
13. `CHANGELOG.md` keeps one `[Unreleased]` section. Its `### Fixed` list records gate-verdict fixes; the precedent is the `deny-network-installs.sh` false-deny entry. [verified: CHANGELOG.md:3,5,253,261] Judging that this change clears the file's "notable" bar is the plan-architect's call. The file was not in the dispatch's list. [unverified]
14. The engineer approved the roadmap. [engineer-verified: "Let's go with your proposed plan."] This covers only approval of the session's roadmap. The roadmap's description of PR A was the session's wording, not the engineer's.
15. The engineer delegated PR A's scope. [engineer-verified: "Ask the architect"] This covers only the delegation. The four-site boundary is the architect consult's conclusion, including deny-escaped-backticks-in-pr-body.sh:140, which the roadmap's PR A wording did not name. As an engineer decision on its own it is [unverified]; row 16 records the engineer's later answer on that site. The site facts behind it are re-checked in rows 2-3.
16. The engineer kept the sibling hook `deny-escaped-backticks-in-pr-body.sh:140` and its regression test in PR A. [engineer-verified: "Keep in PR A (Recommended)"] This covers only that site's inclusion in PR A, selected in answer to the question whether to keep it. It does not cover the follow-up gate-hook PR's contents.
17. The engineer kept the `CHANGELOG.md` `### Fixed` bullet in PR A. [engineer-verified: "Keep the bullet (Recommended)"] This covers only that bullet's inclusion, selected in answer to the question whether to keep it.

## Critical files

One `code-writer` dispatch covers the whole change in a single phase. All four sites share one background (rows 1-9), and splitting it would mean restating that background in every prompt. The dispatch also runs Verification steps 1-4 and reports each result.

- `claude/.claude/hooks/require-stow-reminder.sh`: the comment above line 76, plus the here-string rewrites at lines 177, 215, and 221.
- `claude/.claude/hooks/deny-escaped-backticks-in-pr-body.sh`: the comment above line 38, plus the here-string rewrite at line 140.
- `claude/.claude/hooks/tests/test_require_stow_reminder.py`:
  - Add a module-level `LARGE_MULTILINE_TAIL`, copied from row 11 with a one-line comment that it must be multi-line and large.
  - Add three tests to `TestRequireStowReminder`, two of them parametrized. Reuse the `stow_repo` fixture, `commit_new_toplevel_dir`, `run_hook`, `run_hook_reason`, and `bash_input`.
  - Every new test gets a one-line docstring naming the hazard, in this form: a first-line match in a large multi-line input must still be seen, because a pipe into `grep -q` returns 141 under `pipefail`.
  - Every deny assertion first asserts `reason is not None`, as `test_require_stow_reminder.py:176-178` does, so a missing deny fails with a readable message and not a `TypeError`.
  - Every allow assertion is `run_hook_reason(...) is None`, so a failure prints the deny text and an infrastructure deny (for example a jq timeout under load) is distinguishable from the false deny.
  - **`test_pr_edit_large_multiline_body_without_marker_denied`** covers :177.
    - Setup: `commit_new_toplevel_dir(stow_repo, "agents")`.
    - Command: `"gh pr edit 42 --body 'rewritten body, no marker'" + LARGE_MULTILINE_TAIL`.
    - Assert that the reason contains `"adds new files"`.
    - At the merge-base: allow.
  - **`test_fill_on_first_line_of_large_multiline_command`** covers :215, parametrized over the commit message.
    - Setup: mirror the commit in `test_fill_with_marker_in_commit_message_allowed`.
    - Command: `"gh pr create --fill" + LARGE_MULTILINE_TAIL`.
    - Case with the message `add agents (post-merge: run install.sh)`: assert allow. At the merge-base: deny.
    - Control case with the message `add agents`: assert deny with `"adds new files"` in the reason, at the merge-base and with the fix. It shows the allow case is not an unrelated fail-open on large commands.
  - **`test_large_multiline_body_file_with_marker_on_first_line`** covers :221, parametrized over the first line.
    - Setup: `commit_new_toplevel_dir`, then write `body = tmp_path / "body.md"` with the first line plus `LARGE_MULTILINE_TAIL`.
    - Command: `f"gh pr create --title T --body-file {body}"`.
    - Case with the first line `Post-merge: run ./install.sh.`: assert allow. At the merge-base: deny.
    - Control case with the first line `Adds the agents directory.`: assert deny with `"adds new files"` in the reason, at the merge-base and with the fix.
  - Per row 9, no new test name may have `stow` in its first 30 characters, and no body file may go under `stow_repo`.
- `claude/.claude/hooks/tests/test_deny_escaped_backticks_in_pr_body.py`:
  - Add the same module-level `LARGE_MULTILINE_TAIL`.
  - Add one test, **`test_large_multiline_body_file_with_escaped_backtick_on_first_line_is_denied`**, which covers :140. It gets the same one-line docstring and `reason is not None` guard as the stow tests.
    - Setup: write `tmp_path / "body.md"` with the first line from the existing test at `test_deny_escaped_backticks_in_pr_body.py:51-61` followed by `LARGE_MULTILINE_TAIL`.
    - Command: `f"gh pr create --body-file {body_file}"`.
    - Assert that the reason contains `"backslash-backtick"`, which pins the content deny rather than a fail-closed body-file deny.
    - At the merge-base: allow.
    - It needs no large-input control: a deny here can only come from the content match, and the test is red at the merge-base.
- `CHANGELOG.md`: one bullet at the top of `### Fixed` (line 253), in this gist:
  - Neither hook now reads an early match in a large multi-line input as no match.
  - `require-stow-reminder.sh` falsely denied a `--body-file` whose reminder sat near the top, and dropped `--fill` commit messages from its scan.
  - It also skipped the gate for a large multi-line body-changing `gh pr edit`.
  - `deny-escaped-backticks-in-pr-body.sh` let an escaped backtick near the top of a large body through.
  - Each match now reads a here-string, and both hooks are stowed, so the fix is live on `git pull`.

  Write "escaped backtick (backslash-backtick)" in words, not the literal two-character pair. A PR body that quotes the entry would otherwise trip the backtick gate. Include no size figures.
- `.claude/plans/stow-reminder-sigpipe-fix.md`: this plan, committed by the session.

## Verification

Run every venv tool from the worktree root with the README's worktree-relative path (`../../../.venv/bin/<tool>`): a linked worktree has no `.venv` of its own. [verified: README.md:305,522; the four tools exist at that path]

1. **Show each new test case fails without the fix, in place.** `HEAD` is the merge-base for the six planned files: `origin/main`'s one extra commit touches none of them. [verified: plan-review `git diff --stat` empty] So the unedited hooks in this worktree are the merge-base hooks, and no second worktree is needed. The `code-writer` works in this order:
   - Add the new tests and `LARGE_MULTILINE_TAIL` to both test files, leaving both hooks unedited.
   - Run `../../../.venv/bin/pytest` on the two test files. Expected: exactly the new non-control cases fail, each with the failure listed per test in Critical files (an unexpected deny where allow is asserted, an unexpected allow where a deny reason is asserted), and every control case and every pre-existing test passes.
   - Report each failing case's message, to show it fails for the stated reason and not for an infrastructure deny.
   - Apply the hook edits and the two comments.
   - Rerun the same command. Expected: every test in both files passes.
2. **Scoped suite on the branch:** `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Every selected test passes, including the new cases.
3. **Lint:**
   - `../../../.venv/bin/shellcheck claude/.claude/hooks/require-stow-reminder.sh claude/.claude/hooks/deny-escaped-backticks-in-pr-body.sh` is clean.
   - `../../../.venv/bin/ruff check claude/.claude/hooks/tests/test_require_stow_reminder.py claude/.claude/hooks/tests/test_deny_escaped_backticks_in_pr_body.py` is clean.
4. **No pipe remains:** grep both hooks for the ERE `\|[[:space:]]*grep -[A-Za-z]*q`. Expect zero matches.

## Out of scope

- **Follow-up PR, committed separately and not part of this change.** It covers the `printf | grep -q` sites that scan `$COMMAND` in gates tiered `irreversible`/`untrusted-input`. The list below was relayed from the consult and is a floor, not the inventory: a read-only scan in plan review found more pipe-fed `grep -q` sites, most of them bounded or single-line. The follow-up derives its site list from a repo scan:
  - `deny-credential-bash-reads.sh:76,85`
  - `enforce-marker-script-shape.sh:1006`
  - `deny-private-project-refs.sh:359-362`
  - `guard-settings-session-keys.sh:88`
  - `require-ready-for-review.sh:276,292`
  - the plugin gates `require-plugin-version-bump.sh:91` and `require-npm-version-bump.sh:127`
  - `deny-invisible-commit-content.sh:282` (affects message accuracy only)

  Under `docs/hooks.md`'s regression-only analysis, each site needs its own deny test. As its last commit, that PR lands a lint in `test_hook_alignment.py` Layer 1. The lint must classify `_lib.sh:3415`, which is a single-line basename check and not a hazard. Hoisting `LARGE_MULTILINE_TAIL` into the shared test helpers belongs there too, where most of its consumers will land.
- **Non-gate scripts, not in this change:** `ci-watch.sh:278`, `cleanup-merged-branches.sh:959`, and `register-marketplace.sh`.
- **Rejected here, not deferred:** a shared matcher helper, a `=~`/`case` rewrite, and any change to `pipefail`. Reasons are in the Approach.
- **Left alone deliberately:**
  - Both hooks' `# tier-threat-model: cooperative` lines and fail postures.
  - `docs/hooks.md` and its count line (row 12).
  - The other pipelines in both hooks (row 3).
