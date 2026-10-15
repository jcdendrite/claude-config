# Gate-hook SIGPIPE follow-up

## Context

Goal: close, in every remaining hook, the large-input SIGPIPE fail-open that #1240 fixed in `require-stow-reminder.sh` and `deny-escaped-backticks-in-pr-body.sh`, and add a lint so the shape cannot return.

Ask: "Let's go with your recommendation" — accepted via the recommendation "gate-hook PR first, then P1 of #1236 as a separate PR". The engineer then delegated the two open scope decisions: plugin-hook sites in this PR ("Ask the architect") and the xargs-tokenizer fix ("Ask the architect - one spawn please with the prior"). The scope items below are the session's own drafting from the #1240 review findings.

Why now: under `set -o pipefail`, `printf ... | grep -q` returns 141 when grep exits on its first match while the writer is still writing a large multi-line input. Hooks that test that status read it as no match, so a match-to-deny gate fails open on a large honest input such as a big heredoc body. #1240 fixed two hooks and left the sibling sites.

Intended outcome: every remaining site reads a here-string, each reachable site has a regression test that is red at the merge-base, and the last commit adds a lint over all hooks with no exemption list.

## Approach

Every hook line that pipes into a grep that can stop reading early becomes a here-string read, in the main hooks and in the plugin hooks. Grep flags, pattern bytes, and wrapper prefixes stay exactly the same. Each site that a large multi-line input can reach gets one regression test that is red at the merge-base. The last commit adds a Layer-1 lint in `test_hook_alignment.py`. It forbids the pipe form across every hook and every `_lib.sh`, with no exemption list, and replaces the advisory header comments that #1240 added.

This copies #1240's shape on purpose. Nine shipped gate matches already read here-strings (row 12), so the lint enforces one form and never has to judge between two. The xargs-tokenizer fail-open (handoff item 2) is a different mechanism with a different fix. It is deferred to its own PR across all six extractors (M10).

Alternatives weighed:
- **`printf | grep -c … >/dev/null`.** `-c` reads to end of input, so there is no SIGPIPE and no temp file. Set aside: it gives the lint a second accepted shape, and a later edit that drops `-c` or adds `-q` would silently bring the hazard back.
- **bash `=~` or `case`.** Rejected, as in #1240's plan. `^` and `$` anchor the whole string instead of each line, and `-i` needs `nocasematch` toggling under bash 3.2.
- **`set +o pipefail` around each match, or dropping `pipefail`.** Rejected, as in #1240's plan.
- **An exemption dict for the bounded sites.** Rejected; see M3.
- **`git diff --cached --quiet -- <path>` at `guard-settings-session-keys.sh:88`.** Lighter, but it turns today's substring match into an exact pathspec match. That changes a verdict in an edge case outside this PR.
- **The xargs fix in this PR.** Denying on any nonzero tokenizer exit, as drafted, would false-deny every multi-line inline body (row 14). A narrower raw-text recovery is deferred; see M10.

### Assumption ledger

**Root:** Under `set -o pipefail`, a pipe into a grep that stops early (`-q`/`--quiet`/`--silent`, `-l`, `-L`, `-m`) returns 141 when grep exits at its first match while the writer is still writing a multi-line input larger than the pipe capacity. Every hook line that tests that pipeline's status reads the 141 as no match. #1240 closed four such sites in two hooks. Fourteen reachable lines remain (row 3), and no mechanical check stops the shape from coming back.

**Givens:**
- G1. A reader that exits early fails a writer that is still writing, and `pipefail` turns that failure into the pipeline's status. Reason: bash and the kernel's pipe implementation impose this.
- G2. Hooks cannot bound the size or line count of tool input (`command`, `file_path`, Write `content`) or of git's staged-path listing. Reason: the agent and the repository state own those inputs.
- G3. Hooks must run under macOS `/bin/bash` 3.2 as well as bash 5.x. Reason: consumer machines impose it, and the repo enforces it (`_lib.sh:531-533` cites `test_no_bash4_constructs.py`).

**Mechanisms:**
- **M1.** Use a here-string at every line in row 1, in both the main hooks and the plugins. Grep flags, pattern bytes, and any `_lib_capped`/`LC_ALL=C` prefix are unchanged. `deny-private-project-refs.sh:358-359` and `:361-362` each collapse to one line. anchors: root, row1, row6, row7, row8
- **M2.** At `guard-settings-session-keys.sh:88`, capture the output of `_lib_capped git … diff --cached --name-only` into a variable, then run `grep -qF` over it through a here-string. This keeps the substring match. It also keeps the hook's fail-open posture explicitly: a nonzero git status (including a `timeout` kill) exits 0 before the match, so a partial capture is never scanned. A test pins that allow path. anchors: row3, row9
- **M3.** Convert the bounded sites too (row 4), so the lint ships with no exemption dict. The bare-jq lint's exemption dict is the precedent for the other option. Here it would need 19 entries across 8 files plus stale-entry assertions. It would also leave a site unguarded once a later edit removes its single-line guard, such as the `[[ "$TRIMMED" != *$'\n'* ]]` check. anchors: row4, row6, row12
- **M4.** Add one regression test per reachable arm, built on the shared tail with the match on the first line. Each test must be red at the merge-base (Verification step 1). Bounded sites get no new test, because a test there cannot be red at the merge-base. anchors: row3, row5, row20
- **M5.** Hoist `LARGE_MULTILINE_TAIL` and its size assert into `claude/.claude/tests/helpers.py`. The three existing copies and every new test import it from there. Once about a dozen tests share one size invariant, the constant is shared knowledge, not DAMP scaffolding. #1240's plan deferred this hoist to this PR. anchors: row10, row11
- **M6.** Add a Layer-1 lint `test_no_pipe_into_early_exit_grep` to `test_hook_alignment.py`. It is parametrized over `ALL_HOOKS_AND_LIBS` with no exemption dict, mirrors `test_no_gnu_backslash_s_regex_extension`, has a positive/negative meta-test and a blind-spot pin, and is the last commit. Lighter enforcement fails for these reasons:
  - #1240's header comments alone are advisory, and that is how 29 main-hook lines survived.
  - ShellCheck has no user-defined check mechanism [unverified].
  - A one-off grep in Verification gives no protection against recurrence.

  anchors: root, row2, row12, row22
- **M7.** Shrink the #1240 comments (`require-stow-reminder.sh:76-78`, `deny-escaped-backticks-in-pr-body.sh:38-40`) and `deny-private-project-refs.sh:789-793` to a one-line pointer to the lint. The lint's docstring becomes the single statement of the rule. anchors: row12
- **M8.** Add one new bullet at the top of `CHANGELOG.md`'s `### Fixed`. anchors: row16
- **M9.** The session sets patch bumps for plugin-semver, npm-semver, and lovable-cloud through `plugin-semver:plugin-semver`. anchors: row17, row22
- **M10.** Defer handoff item 2 to a dedicated PR covering all six xargs extractors, with a tracking issue filed only after the engineer approves. This PR adds no header Known-gaps entry for it, because recording a gap a cooperative agent can hit as debt needs the engineer to accept it as permanent (`docs/hooks.md` § "Threat-model tiers"). The deferral has four reasons:
  - The drafted rule false-denies multi-line inline values (row 14).
  - The safe direction has its own unreviewed residual.
  - The scope covers two `irreversible` gates.
  - The mechanism is unrelated to SIGPIPE.

  anchors: row13, row14, row23

**Rows:**
1. Inventory at merge-base `0aefc600`. [verified: Grep this session with a pattern that accepts env-assignment and `_lib_capped` prefixes and separated flags]
   - 29 code lines (30 grep calls) in 12 files under `claude/.claude/hooks/`:
     - `deny-credential-file-reads.sh:56`
     - `deny-credential-bash-reads.sh:76,85`
     - `deny-private-project-refs.sh:359,362,529`
     - `deny-invisible-commit-content.sh:282`
     - `enforce-marker-script-shape.sh:1006,1035,1065,1084`
     - `require-ready-for-review.sh:264,267,270,276,286,292,331,334`
     - `guard-settings-session-keys.sh:88`
     - `nudge-transcript-toolkit.sh:124`
     - `ask-review-permissions.sh:47,63,88`
     - `deny-no-op-dispatch.sh:127` (two greps on one line)
     - `set-session-title-from-branch.sh:139,154,155`
     - `_lib.sh:3419`
   - 4 lines in 4 plugin files (3 plugins):
     - `plugins/plugin-semver/hooks/require-plugin-version-bump.sh:91`
     - `plugins/npm-semver/hooks/require-npm-version-bump.sh:127`
     - `plugins/lovable-cloud/hooks/consume-migration-token.sh:36`
     - `plugins/lovable-cloud/hooks/validate-migration-filename.sh:76`
   - Lines `enforce-marker-script-shape.sh:724,926` and `deny-pii-in-commits.sh:576` are comments.
2. The handoff regex misses `deny-no-op-dispatch.sh:127` and `set-session-title-from-branch.sh:139,154,155`. It would also flag comment lines and `|| grep -q`. [verified: same Grep]
3. Reachable lines: the input can be multi-line and over 64 KiB under `pipefail`, and a miss changes what the hook does. [verified: reads of each site]
   - `deny-credential-bash-reads.sh:76,85`: a miss allows a command that names a credential path. A deny needs both lines to match.
   - `deny-credential-file-reads.sh:56`: a miss allows the Read. The gate decides before the Read runs. A real Read path is bounded by `PATH_MAX`, so this site is reachable only by a synthetic input. One rule classifies the file-path sites: this one, `consume-migration-token.sh:36`, and `validate-migration-filename.sh:76`. The conversions stay because the lint requires them.
   - `deny-private-project-refs.sh:358-362`: a miss leaves `IS_GH_API=0`, so a mutating `gh api` body is never scanned.
   - `deny-private-project-refs.sh:529`: a miss drops the chain-split tip from a deny that has already fired.
   - `deny-invisible-commit-content.sh:282`: the 141 falls into the "could not evaluate" deny at `:288-290`. The hook still denies, but with the wrong reason.
   - `enforce-marker-script-shape.sh:1006`: a miss lets a tilde-form traversal reach Stage 2's non-match `exit 0` at `:1020-1022`.
   - `require-ready-for-review.sh:276,292`: a miss keeps the delete-only or tags-only bypass even when a substitution is present.
   - `guard-settings-session-keys.sh:88`: a miss exits 0 before the key check.
   - `nudge-transcript-toolkit.sh:124`: a miss drops the nudge.
   - `require-plugin-version-bump.sh:91` and `require-npm-version-bump.sh:127`: a miss exits 0 before the version check.
4. Bounded lines, which cannot SIGPIPE today. A single unbroken line never misses (row 5). [verified: reads]
   - `ask-review-permissions.sh` and `validate-migration-filename.sh` never set `pipefail`. `_lib.sh` has no top-level `set`.
   - `require-ready-for-review.sh:264,267,270,286,331,334`: `$fragment`/`$frag` is one line of the `read` loop at `:319-337`.
   - `enforce-marker-script-shape.sh:1035,1065,1084` are guarded by `[[ "$TRIMMED" != *$'\n'* ]]`.
   - `_lib.sh:3419`: `$base` is one `read` line.
   - `deny-no-op-dispatch.sh:127`: the prompt is under 600 characters (`:108`) and collapsed to one line (`:112`).
   - `set-session-title-from-branch.sh`: a small porcelain record and two name components.
   - `consume-migration-token.sh:36`: one file path.
5. A single unbroken line never misses. A multi-line input larger than the pipe capacity (64 KiB by default on Linux) with a first-line match misses. The 100 KiB assert floor is headroom over that capacity, not a measured threshold. The 320 KB, 32-line tail is about 3.1 times the floor and about 4.9 times the capacity. [verified: `.claude/plans/stow-reminder-sigpipe-fix.md` row 5; staff-sdet probe of the guard-settings shape: a 53 KB list never missed, a 91 KB list missed 10 of 10 runs]
6. Here-string input is byte-identical at `printf '%s\n'` sites. At `printf '%s'` sites it gains one trailing newline. That changes a verdict only for a pattern that can match an empty line, and none of the converted patterns can: [verified]
   - `_LIB_CREDENTIAL_PATH_REGEX` (`_lib.sh:3332`)
   - `_LIB_SSH_SAFE_BASENAME_REGEX` (`_lib.sh:3391`)
   - `(^|/)\.\.(/|$)` and the `VALID_*` patterns (`enforce-marker-script-shape.sh:1006,1027-1033,1063,1082`)
   - `&&|\|\|`
   - `NOOP_*_RE` (`deny-no-op-dispatch.sh:64,70`)
   - `ALLOWLIST_RE` (`set-session-title-from-branch.sh:153`)
7. A here-string adds one residual failure: if its temp file cannot be created (for example a full temp filesystem or exhausted file descriptors), grep never runs and the status is 1, read as no match. This matters on bash 3.2 through 5.0, where every here-string is a temp file, and on 5.1+ above pipe capacity. [verified: reviewers' reading of bash behavior and `docs/hooks.md:299`; not run on bash 3.2]
   - For input-derived strings the parse step fails first in the main gates: `_lib_parse_tool_input_or_deny` reads `COMMAND` and `FILE_PATH` through a here-string (`_lib.sh:543`) at `deny-credential-file-reads.sh:35`, `deny-credential-bash-reads.sh:55`, `deny-private-project-refs.sh:221`, `deny-invisible-commit-content.sh:185`, `enforce-marker-script-shape.sh:261`, `require-ready-for-review.sh:193`, and `deny-no-op-dispatch.sh:90`. When that redirect fails, the deny at `:547-549` is not reached: `_lib_parse_overflow` is never initialized, so under `set -u` the hook aborts with exit 127, a non-blocking exit that allows the call. [verified: reviewer probe on bash 5.2.21 with a file-descriptor limit]
   - Accepted exceptions, where the string is not one the parse carried:
     - `guard-settings-session-keys.sh:88` reads git's staged-name list.
     - `nudge-transcript-toolkit.sh:124` reads Write `content`.
     - The semver plugin gates have a parse with no here-string (`plugins/*/hooks/_lib.sh:71`).
     - The `-ne 1` branch at `deny-invisible-commit-content.sh:282` reads a redirect failure as no match.
     - `ask-review-permissions.sh` (lines 47, 63, 88) sets no `pipefail` and parses through pipes. It had no SIGPIPE hazard before and now depends on a creatable here-string temp file.
     - `plugins/lovable-cloud/hooks/validate-migration-filename.sh` (line 76) has the same shape and the same new dependency.
   - These hooks' posture on an environment failure is already fail-open or informational.
   - The PR body records this residual, and records that the new tests ran on bash 5.x only (CI is `ubuntu-24.04`, `tests.yml:25`); bash 3.2 is [unverified].
8. `_lib_capped` forwards `"$@"` to `_lib_capped_for` (`_lib.sh:49-51`). The current pipe form already relies on the wrapper passing stdin through, and a here-string uses the same stdin. [verified]
9. At `guard-settings-session-keys.sh:88` the writer is `timeout … git`, not printf. A staged-name list over the pipe capacity with `claude/.claude/settings.json` listed early reproduces the miss. [verified: staff-sdet probe in a scratch repo with `git update-index --index-info`: a 266 KB list missed 5 of 5 runs, a 91 KB list 10 of 10, a 53 KB list 0 of 10] Verification step 1 still confirms it in place.
10. `LARGE_MULTILINE_TAIL` has three copies: `test_require_stow_reminder.py:30`, `test_deny_escaped_backticks_in_pr_body.py:34`, and `test_enforce_marker_script_shape.py:113`. The third has no size assert. [verified]
11. `helpers.py` already holds shared module-level test constants that tests import by name: `TRAVERSAL_SESSION_ID` (`:734`), `CANARY_CONTENT` (`:741`), and `DEFAULT_TEST_SESSION_ID` (`:802`). It imports as a top-level module through `pyproject.toml`'s `pythonpath`. `conftest.py` is the home for fixtures and shims. [verified: helpers.py; `.claude/rules/test-tree-packaging.md`]
12. Layer-1 precedent in `test_hook_alignment.py`. [verified: `test_hook_alignment.py:1013-1338`; Grep for the here-string sites]
    - The detectors share `_strip_comment`.
    - A detector with no plugin-side hits runs over `ALL_HOOKS_AND_LIBS` with no exemption dict (`:1278-1306`).
    - Each detector has a positive/negative meta-test and a blind-spot pin.
    - Nine shipped gate matches already read here-strings: `deny-pii-in-commits.sh:584,607,623`, `deny-private-project-refs.sh:829,835`, `require-stow-reminder.sh:180,218,224`, and `deny-escaped-backticks-in-pr-body.sh:143`.
13. The `printf | xargs -n1 | awk` path extractor appears in six functions across three hooks. [verified]
    - `deny-escaped-backticks-in-pr-body.sh:92`
    - `deny-private-project-refs.sh:446,465,484,508`
    - `deny-pii-in-commits.sh:422`

    Only `deny-private-project-refs.sh`'s header mentions the failure as a known gap (`:134-135`, `:440-443`). That bullet came in with an unrelated feature PR after the tier-header commit and carries no human-acceptance rationale, so it does not count as accepted debt under `docs/hooks.md:20`. [verified: ciso-reviewer's reading of the commit history] `_lib.sh:1756` already fails closed when xargs fails (`:1787-1789`).
14. xargs exits 1 on a newline inside a quoted argument and emits no tokens after the failure point. [verified: session probe this session. `printf '%s\n' 'cmd sub --body "line one' 'line two" --body-file /tmp/x' | xargs -n1; echo "exit=$?"` printed `cmd`, `sub`, an "unmatched double quote" error, `--body`, and `exit=1`. `--body-file` was never emitted.] So "a nonzero tokenizer exit denies" would deny every multi-line inline `--body`/`-m` value.
15. `docs/hooks.md` needs no edit. No entry mentions SIGPIPE or pipes into grep, and no tier line changes. Its one here-string mention (`:299`, status 2 also arises from a failed here-string redirect) is consistent with row 7. [verified: Grep of `docs/hooks.md`]
16. `CHANGELOG.md` has one `[Unreleased]` section (`:5`). #1240's bullet at `:256` records a past fix and stays untouched. The new bullet goes first under `### Fixed` (`:254`). [verified]
17. The only version field is in `plugin.json`: plugin-semver 1.1.7, npm-semver 1.0.7, lovable-cloud 3.2.8. `marketplace.json` has none. `require-plugin-version-bump.sh` blocks the commit until each touched plugin's version is strictly raised. Plugins have no CHANGELOG of their own. The root CHANGELOG records plugin bumps. [verified: Grep; `.claude/rules/review-pipeline-dispatch.md`; CHANGELOG.md:258]
18. The engineer approved the sequencing: this gate-hook PR first, then a separate P1 PR. [engineer-verified: "Let's go with your recommendation"] That quote covers only the sequencing.
19. GH-499 is closed. [engineer-verified: "I'm looking at gh-499 now and it seems that the issue was marked as closed, deemed as inappropriate given cooperative agents"] Treating the unmerged `GH-499/gate-effective-cwd` branch as abandoned is the session's inference. [unverified]
20. The worktree HEAD is `origin/main` `0aefc600` and the tree is clean, so the unedited hooks here are the merge-base hooks. [verified: session git status at dispatch]
21. It is not known whether the harness's `Bash(git commit *)` `if` filter on the semver plugin gates matches a multi-line command. Tests call the hook directly, so the fix is proven at the hook, not at dispatch. [unverified]
22. The engineer delegated D1 (the plugin sites in this PR). [engineer-verified: "Ask the architect"] That covers only the delegation. Including the plugin sites is the architect's choice.
23. The engineer delegated D2 (the xargs tokenizer). [engineer-verified: "Ask the architect - one spawn please with the prior"] That covers only the delegation and the single-pass request. Deferring the fix is the architect's choice.
24. Scope check at plan-review: the extra hook sites (`guard-settings-session-keys.sh`, `deny-no-op-dispatch.sh`, `set-session-title-from-branch.sh`), the shared test helper hoist, the lint, and the CHANGELOG bullet all stay. [engineer-verified: "I think all"] The quote answers which of those four groups should stay. Its follow-up question to the architect is answered by the architect's own plan, which already includes all four groups.
25. The sequencing quote in row 18 stands. [engineer-verified: "It is what I said"] The architect's plan carries the sequencing as given and does not dispute it.

## Critical files

Four sequential `code-writer` dispatches do the work. The session commits after each one, following `/code-review`. Each dispatch's output is the next one's input: Phase 2's tests import the hoisted tail, and the lint only passes once every site is converted. Line numbers are merge-base (`0aefc600`) lines.

**Dispatch 1, commit 1: share the tail.** Verify with `../../../.venv/bin/pytest` on the three test files below. Each file's pass count must match before and after.
- `claude/.claude/tests/helpers.py`:
  - Add `LARGE_MULTILINE_TAIL = "\n" + "\n".join(["x" * 10_000] * 32)` next to the other shared constants.
  - No import-time assert: a failure there would break import for every test module in every domain.
  - Add one comment stating the durable facts, and point at no plan file:
    - The tail must be multi-line with a matching first line and exceed the 64 KiB pipe capacity (100 KiB gives headroom).
    - Keep the per-fragment scans fast by keeping the line count low (32 lines).
    - Do not use it on a path that reaches `_lib_mask_shell_quotes`, whose quadratic scan nears its 5 s cap on this size.
- New file `claude/.claude/tests/test_large_multiline_tail.py`: one test asserting the tail has more than one newline and is longer than 100 KiB. This carries the size invariant that the local asserts carried.
- In each file below, delete the local constant, its comment, and its assert, and import from `helpers`:
  - `claude/.claude/hooks/tests/test_require_stow_reminder.py`
  - `claude/.claude/hooks/tests/test_deny_escaped_backticks_in_pr_body.py`
  - `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`

**Dispatch 2, commit 2: main hooks.** Do Verification step 1 for the eight test files below, then step 4. Every hook line in row 1 becomes `grep <same flags> <same pattern> <<< "$<same variable>"`, keeping any `_lib_capped`/`LC_ALL=C` prefix. No hook gets a new comment.

Hook edits:
- `claude/.claude/hooks/deny-credential-bash-reads.sh`: lines 76 and 85.
- `claude/.claude/hooks/deny-credential-file-reads.sh`: line 56.
- `claude/.claude/hooks/deny-private-project-refs.sh`: lines 358-359 and 361-362, each collapsed to one line, and line 529.
- `claude/.claude/hooks/deny-invisible-commit-content.sh`: line 282. Keep the `WRAPPER_TOKEN_EXIT` capture and its `-ne 1` check.
- `claude/.claude/hooks/enforce-marker-script-shape.sh`: lines 1006, 1035, 1065, and 1084. Keep the `[[ … != *$'\n'* ]] &&` guards.
- `claude/.claude/hooks/require-ready-for-review.sh`: lines 264, 267, 270, 276, 286, 292, 331, and 334.
- `claude/.claude/hooks/guard-settings-session-keys.sh`: line 88, per M2.
- `claude/.claude/hooks/nudge-transcript-toolkit.sh`: line 124.
- `claude/.claude/hooks/ask-review-permissions.sh`: lines 47, 63, and 88.
- `claude/.claude/hooks/deny-no-op-dispatch.sh`: line 127, both greps.
- `claude/.claude/hooks/set-session-title-from-branch.sh`: lines 139, 154, and 155.
- `claude/.claude/hooks/_lib.sh`: line 3419. Every hook sources this file, and the edit does not change behavior (rows 4 and 6).

New tests. Each one appends the shared `LARGE_MULTILINE_TAIL` after a first line that matches. Every deny test uses `run_hook_reason` and asserts `reason is not None`, then a substring unique to that gate's match-arm deny and absent from every `could not …` infrastructure deny; the code-writer names the substring in its report. A bare `== "deny"` comparison, as at `test_require_ready_for_review.py:321-341`, is not used for the new tests. The merge-base outcome is in parentheses.
- `claude/.claude/hooks/tests/test_deny_credential_bash_reads.py`: `test_credential_path_on_first_line_of_large_multiline_command_denied`. A command that reads a credential path, plus the tail. Expect a deny with the credential-path reason (merge-base: allow).
- `claude/.claude/hooks/tests/test_deny_credential_file_reads.py`: `test_credential_path_on_first_line_of_large_multiline_file_path_denied`. `read_input` with a credential-shaped path that only the credential regex catches (a `.netrc` or `.env` name, not `.ssh`, which a second check also denies), plus the tail, under `isolated_home`. Expect a deny (merge-base: allow). A real Read path is bounded by `PATH_MAX`, so this test is synthetic; the conversion stays because the lint requires it.
- `claude/.claude/hooks/tests/test_deny_private_project_refs.py`:
  - `test_mutating_gh_api_flag_on_first_line_of_large_multiline_command_is_scanned`, parametrized over two cases. In one, an `-X POST` method flag is the only body-flag trigger. In the other, a `-f` field flag is. Each case puts a synthetic non-allowlisted tracker token in the command, written the way this file's existing tests write one. Expect a deny that names tracker-ID tokens (merge-base: allow).
  - `test_chain_tip_survives_large_multiline_command`. A `&&`-chained gated command with a tracker token on the first line. Expect the reason to contain "Tip: this command chains" (merge-base: a deny without the tip).
- `claude/.claude/hooks/tests/test_deny_invisible_commit_content.py`: `test_wrapper_token_on_first_line_of_large_multiline_commit_named_in_deny`. Take an existing wrapper co-occurrence command and add the tail. Expect the reason to contain "execution-wrapper token" and not "could not evaluate" (merge-base: the "could not evaluate … (exit 141)" deny). Do not assert on "141". Add a negative twin: a large multi-line benign commit command with no wrapper token must be allowed. This is the one converted site where the 141-to-1 status mapping decides the verdict, and the twin catches an accidental always-deny.
- `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`: `test_traversal_on_first_line_of_large_multiline_marker_command_denied`. `~/.claude/scripts/../scripts/marker.sh write code-review` plus the tail, run as the main session (no `agent_type`, since a gate-release agent hits an earlier deny at `:970-980`). Expect a deny with "path traversal" (merge-base: allow).
- `claude/.claude/hooks/tests/test_require_ready_for_review.py`: `test_bypass_push_with_substitution_in_large_multiline_command_still_gated`.
  - Parametrize over a colon-refspec push and a `--tags` push, each carrying a `$(…)` substitution.
  - Reuse the `isolated_home`, `repo_on_feature_branch`, and `fake_gh_pr_exists` fixtures, and mirror `:321-341`.
  - Expect a deny (merge-base: allow).
  - First confirm that the colon-refspec command without the tail denies at the merge-base. No existing test covers that arm.
- `claude/.claude/hooks/tests/test_guard_settings_session_keys.py`: `test_guarded_key_change_with_large_staged_path_list_denied`.
  - Reuse this file's existing guarded-key setup.
  - Add enough staged index entries under a prefix that sorts after `claude/` (for example `zz/`) that the name list is about 110-130 KB (roughly 1,500-1,800 paths), over the 64 KiB pipe capacity with margin.
  - Stage a single blob at many paths with `git update-index --index-info` [verified: staff-sdet probe, row 9].
  - Expect a deny that names the guarded key (merge-base: allow, per row 9).
  - Add a second test, `test_git_failure_on_staged_name_list_allows`, pinning the explicit allow path from M2 (git exits nonzero, hook exits 0). The `docs/hooks.md:31` rule treats an altered fail-open path with no test as a regression.
- `claude/.claude/hooks/tests/test_nudge_transcript_toolkit.py`: `test_glob_on_first_line_of_large_multiline_write_nudges`. `write_input` to a `.py` path under `tmp_path`, with content whose first line holds a `projects/*/*.jsonl` glob, plus the tail. Expect context naming `transcript-analysis.py` (merge-base: `None`). Use this test file's own local `_run_hook`, `_fired`, and `_additional_context` helpers, not `run_hook_context`.

`CHANGELOG.md` gets one bullet at the top of `### Fixed`:
- A lead clause: the remaining gate hooks no longer read an early match in a large multi-line input as no match.
- One short clause per main-hook verdict change in row 3.
- A closing clause: each match now reads a here-string, and the stowed main hooks are live on `git pull`.
- Scope every claim to grep-in-a-pipe SIGPIPE; do not say large multi-line input no longer fails open, because the xargs gap in Out of scope remains.
- Name only field-reachable verdict changes; omit `deny-credential-file-reads.sh:56`, whose path is bounded by `PATH_MAX`.
- No size figures, no tracker-shaped tokens, and no literal backslash-backtick pair.

**Dispatch 3, commit 3: plugin hooks.** Do Verification step 1 for the two test files below. Each hook line in row 1 becomes a here-string, as in Dispatch 2.
- Hook edits:
  - `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`: line 91.
  - `plugins/npm-semver/hooks/require-npm-version-bump.sh`: line 127.
  - `plugins/lovable-cloud/hooks/consume-migration-token.sh`: line 36. Keep its `2>/dev/null`.
  - `plugins/lovable-cloud/hooks/validate-migration-filename.sh`: line 76.
- Add `test_large_multiline_git_commit_still_gated` to both `claude/.claude/hooks/tests/test_require_plugin_version_bump.py` and `claude/.claude/hooks/tests/test_require_npm_version_bump.py`. Each test runs `git commit -m …` plus the tail over a staged plugin change with no version bump, reusing that file's existing deny setup. Expect a deny (merge-base: allow).
- Patch bumps in `plugins/plugin-semver/.claude-plugin/plugin.json`, `plugins/npm-semver/.claude-plugin/plugin.json`, and `plugins/lovable-cloud/.claude-plugin/plugin.json`. The session sets them through `plugin-semver:plugin-semver` after the dispatch returns.
- `CHANGELOG.md`: extend the Dispatch 2 bullet with the plugin-gate clause and the three bumps. State that plugin fixes reach consumers on `claude plugin update` after the bump, not on `git pull`.
- Commit 3 stages all three version bumps with the hook edits, so the bump gate passes.

**Dispatch 4, commit 4, the last commit: the lint.** Do Verification steps 2 and 4.
- `claude/.claude/hooks/tests/test_hook_alignment.py`:
  - **Detector:** `_pipe_into_early_exit_grep_hits(hook)`. It is line-based on `_strip_comment` output. It first joins a code line ending in `|` or `\` with the next line. It then flags this sequence:
    - a single `|`, never `||`;
    - then zero or more `NAME=value` assignments, `_lib_capped`, `_lib_capped_for <N>`, or `command`;
    - then `grep`, `egrep`, or `fgrep`;
    - with a `q`, `l`, `L`, or `m` letter in any short-flag bundle, or with `--quiet`, `--silent`, `--files-with-matches`, `--files-without-match`, or `--max-count`.
  - **Test:** `test_no_pipe_into_early_exit_grep`, parametrized over `ALL_HOOKS_AND_LIBS` with repo-relative ids and no exemption dict.
  - **Docstring:** the single statement of the rule. Under `set -o pipefail`, grep exiting at its first match while the writer still writes a large multi-line input fails the pipeline with 141. An `if`, `||`, or `&&` reads that as no match. Feed grep a here-string instead. The failure message names the offending lines and the here-string fix.
  - **Meta-test, positive fixtures (each must be flagged):**
    - `| grep -q`, `| grep -qE`, `| grep -Eq`, `| grep -E -q`
    - `| grep --quiet`, `| grep --silent`
    - `| egrep -q`, `| fgrep -q`
    - `| grep -l`, `| grep -L`, `| grep -m1`, `| grep --max-count=1`, `| grep --files-with-matches`, `| grep --files-without-match`
    - a three-line `|`-continuation chain
    - `| _lib_capped grep -qiE`, `| _lib_capped_for 5 grep -q`, `| LC_ALL=C grep -Eq`
    - a continuation line that starts with `|`
    - a line ending in `|` with grep on the next line
    - a non-printf writer: `git diff --name-only | grep -qF`
  - **Meta-test, negative controls (each must stay unflagged):**
    - a here-string `grep -q`
    - a here-string `_lib_capped grep -qiE`
    - `false || grep -q x f`
    - `| grep -c`, `| grep -o`, `| grep --line-buffered`, `| grep -E 'mail|local'` (letters in the pattern, not the flags)
    - a full-line comment containing `| grep -q`, and a same-line trailing comment containing `| grep -q`
    - a line ending in `||` followed by a `grep -q file` line
    - `grep -qFx -e "$v" -- "$f"`
  - **Blind-spot pin:** these shapes stay unflagged, each pinned by name: `| timeout 5 grep -q`, `| env X=1 grep -q`, `| xargs grep -ql`, `/usr/bin/grep`, `\grep`, `| { grep -q …; }`, a quiet flag passed in a variable, flags after the pattern (`grep "$p" -q`), GNU long-option abbreviations (`--quie`), grep reached through another wrapper function, and `| head -1`.
  - **`hooks/_config.sh`:** `ALL_HOOKS_AND_LIBS` excludes it (`_all_hook_files(include_lib=True)` still does). Today it has zero hits, but every hook sources it. Add it to the lint's parametrization, or, if that needs a larger change, state the exclusion in the docstring. The failure message also says "capture into a variable first" for a legitimate streaming writer.
  - **Docstring maintenance in the same file:**
    - Add a bullet to the module docstring's static-check list, and drop the list's stale count word ("Four further" introduces five bullets).
    - Note in the module docstring that the new check, like the backslash-s check, has no exemption dict.
    - `_all_hook_files`: `include_lib=True` is no longer used only by the backslash-s detector.
    - `_strip_comment`: it now serves four detectors.
    - `test_all_hooks_and_libs_includes_every_lib_sh`: it now protects both detectors' case counts.
- Replace these comment blocks with the single line `# grep -q reads a here-string, never a pipe: see test_no_pipe_into_early_exit_grep in hooks/tests/test_hook_alignment.py.`:
  - `claude/.claude/hooks/require-stow-reminder.sh`, lines 76-78
  - `claude/.claude/hooks/deny-escaped-backticks-in-pr-body.sh`, lines 38-40
  - `claude/.claude/hooks/deny-private-project-refs.sh`, lines 789-793

**Plan file:** `.claude/plans/gate-hook-sigpipe-followup.md`, committed by the session in commit 1 with Dispatch 1's changes.

**Reuse:**
- From `helpers.py`: `run_hook`, `run_hook_reason`, `run_hook_context`, `bash_input`, `read_input`, `write_input`, and the hoisted `LARGE_MULTILINE_TAIL`.
- Each test file's existing fixtures and deny setups.
- From `test_hook_alignment.py`: `_strip_comment`, `ALL_HOOKS_AND_LIBS`, `_REPO_ROOT`, and the meta-test and blind-spot-pin structure at `:1106-1151` and `:1309-1322`.

## Verification

Run every venv tool from the worktree root as `../../../.venv/bin/<tool>` (README.md:522, 546).

1. **Show that each new test fails without its fix, in place.** Row 20 makes HEAD the merge-base. For Dispatches 2 and 3:
   - Add the new tests first, leaving every hook unedited.
   - Run `../../../.venv/bin/pytest <the dispatch's test files> -k "<new test names>"`. Expect exactly the new tests to fail, each with the merge-base outcome given in Critical files.
   - Report each failure message, so a reader can see it is the stated miss and not an infrastructure deny.
   - Apply the hook edits and rerun. Every test in those files should pass.
   - In Dispatch 3, also run `../../../.venv/bin/pytest plugins/lovable-cloud/tests/test_consume_migration_token.py plugins/lovable-cloud/tests/test_validate_migration_filename.py` after the edits. Both files should pass.
   - If the guard-settings test is not red at the merge-base (row 9), report that and stop for the session's call. Do not grow the fixture past about 1 MB.
   - Before Dispatch 2's hook edits, for each converted bounded site (row 4), name one existing test that goes red when the here-string is replaced by `<<< ""`, and list any site with none.
   - Record per-test wall time (`--durations`) for the new 320 KB tests, each well under the 5 s `_lib_capped` cap.
2. **Check the lint's recall against the real inventory.** This check is throwaway and not committed. Run `_pipe_into_early_exit_grep_hits` over each file's merge-base text from `git show 0aefc600:<path>`, written to a temporary directory.
   - Expect exactly the 29 main-hook lines in 12 files and the 4 plugin lines in 4 files from row 1, and no comment line.
   - Run the same check over the branch. Expect zero hits.
3. **Phase 1 is pure refactoring:** the three test files' pass counts are identical before and after Dispatch 1.
4. **Scoped suite:** `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. It may widen itself to the full suite because `helpers.py` and `_lib.sh` changed. That is its decision to make.
5. **Lint:** `../../../.venv/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` are both clean.
6. **PR body content** (the session writes it through `/pr-description`):
   - The red-at-merge-base failure message for each new test, since the red state exists only in the session transcript.
   - The xargs gap (Out of scope) stated as a live, unrecorded finding at the three extractor hooks, with one blocking question to the engineer: schedule the fix, or accept it as permanent with a rationale.
   - The here-string residual from row 7, and that the new tests ran on bash 5.x only.
   - The commit that carries the plan file (commit 1).

## Out of scope

- **The xargs tokenizer fail-open (handoff item 2) is deferred to a dedicated PR and a tracking issue.** The issue is filed only after the engineer approves it.
  - **Scope of the follow-up:** all six path extractors that pipe the whole command through `xargs -n1` (row 13):
    - `deny-escaped-backticks-in-pr-body.sh` `extract_body_source_paths`
    - `deny-private-project-refs.sh` `extract_body_source_paths`, `extract_commit_message_source_paths`, `extract_gh_api_input_paths`, and `extract_gh_api_field_at_paths`
    - `deny-pii-in-commits.sh` `extract_commit_message_source_paths`
  - **Failure:** when xargs exits nonzero, on a newline inside a quoted argument (row 14) or on a token longer than its argument-line limit, every path after the failing token is lost. The file it names is never scanned. Example: `git commit -m "$(cat <<'EOF' … EOF)" && gh pr create --body-file pr.md`.
  - **Constraint for the follow-up:** a nonzero tokenizer exit must not deny on its own, because it fires on every multi-line inline `--body`/`-m` value.
  - **Candidate direction, not decided here:** when the tokenizer fails, recover candidate paths with a raw-text scan for the same flags, and scan each one that is a readable regular file. Decide separately what to do with a path the same command creates first.
  - **Not in the follow-up:** `_lib_commit_fragment_has_worktree_target` already fails closed on an xargs failure.
  - **No header record:** this PR adds no Known-gaps entry for this gap. For a shape a cooperative agent can hit, recording the gap as debt needs the engineer to accept it as permanent (`docs/hooks.md` § "Threat-model tiers").
- **Other early-exit readers are outside the lint.** These are `head`, `awk … exit`, and `sed q`. Every current hook site discards the pipeline's status, so a 141 cannot flip a verdict:
  - `deny-private-project-refs.sh:769,877`
  - `require-respond-pr.sh:327,329`
  - `nudge-unexpanded-skill-mention.sh:85` (`|| true`)
  - `_lib.sh:360-364`
  - `set-session-title-from-branch.sh:136`

  A lexical lint cannot tell a pipeline whose status is tested from one whose status is ignored.
- **`grep -c` and plain grep in a pipe** read to end of input, so neither is a hazard.
- **Non-hook scripts with the same shape:** `claude/.claude/scripts/ci-watch.sh:278`, `cleanup-merged-branches.sh:959,973`, `register-marketplace.sh:117,134,138`, and `install.sh:850`. They are outside the hook lint's scope, and their `pipefail` status was not checked.
- **Comments left in place:**
  - `deny-pii-in-commits.sh:575-580` restates the rule, but nothing else in this PR touches that file.
  - `enforce-marker-script-shape.sh:724,926` justify a pure-bash primitive at their own sites and are still accurate.
- **A `CLAUDE.md` bullet** paralleling "Hook regexes: POSIX ERE only" is not added. The lint's docstring and failure message state the rule where a contributor meets it.
- **`require-stow-reminder.sh` reportedly running for over 300 s on one very long command line.** The reported cause is a quadratic fragment scan, and it has not been reproduced. It is an observation only.
- **The `GH-499/gate-effective-cwd` branch** overlaps these files but is not planned around (row 19).
- **Whether the semver plugin gates' `if` filter dispatches on multi-line commands** (row 21).
- **Rejected outright, not deferred:** a `=~`/`case` rewrite, `pipefail` toggles, a shared `_lib.sh` matcher, `grep -c` as a second accepted form, an exemption dict for the bounded sites, and denying on a nonzero tokenizer exit by itself. The reasons are in Approach.
