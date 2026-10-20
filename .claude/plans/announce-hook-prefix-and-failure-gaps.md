# Plan: one shared plan-review covered-path prefix, and accurate announce-hook Known gaps

## Context

Goal: make the plan-review covered-path prefix a single shared constant in `_lib.sh`, and make the announce hook's header Known gaps accurate to how the hook actually behaves, with the hook's tests and two docs brought in line.

Ask: the scope was carried from an earlier session's architect consult (consumed handoff, scope list at lines 59-68). The engineer chose it as Branch 1 per that handoff; there is no verbatim engineer quote of the scope itself, so it is cited to the handoff, not `[engineer-verified]`. Two optional items were delegated by the engineer in this session: on the real-marker pairing test, "Ask the architect"; on the explicit `exit 0`, "See prior response. Spawn once".

Why now: the announce hook shipped with the prefix duplicated as a literal in `marker.sh` and in the hook, each with a "Paired literal" comment, and its Known gaps mis-state failure behavior. A PostToolUse probe and the shipping PR's body hold the evidence.

## Approach

Define the covered-path prefix once, as `_LIB_PLAN_REVIEW_COVERED_PATH_PREFIX` in `claude/.claude/hooks/_lib.sh`. Both `marker.sh` and the announce hook already source that file. Rewrite the hook's Known gaps around a probed fact: a Bash command that exits non-zero fires `PostToolUseFailure`, not `PostToolUse`. Change the hook tests so they send only the payload shape the harness was seen to send.

**Decisions on the two delegated items:**
- **Pairing test: include it.** Add one real-marker pairing case run from a linked worktree under `.claude/worktrees/<name>/`. Today nothing ties what `marker.sh` actually prints there to what the hook accepts. That is also the layout where this repo's own approvals run (row 13).
- **Explicit `exit 0`: skip it.** The arm already returns 0 after a successful write, and tests already pin that (row 14). The arm's last executed command is not the `printf`; it is a `continue` on the empty line that `<<<` always appends.

**Corrections to the recovered scope:**
- **Item 1:** Both Python tests carry a comment that names `PLAN_REVIEW_COVERED_PATH_PREFIX in marker.sh`, and this change deletes that definition. Each comment needs a one-line edit, which adds `test_marker_script.py` to the file list (comment-only change).
- **Item 4:** `_lib_passes_path_char_allowlist` has two callers, not three. `_respond-pr-lib.sh:29` only mentions it in a comment (row 15).
- **Item 6 rests on a false premise.** The sentence's subject is "the session", meaning the model, not the engineer. The engineer seeing the banner confirms that it renders. It does not contradict the sentence. The edit makes the sentence unambiguous instead of retracting it (row 17).

This changes nothing a stow consumer can observe. The prefix value, the hook's output, and every exit status stay the same. No file is added, so `git pull` needs no re-stow.

Alternatives set aside:
- **Keep the two shell literals with paired comments:** that duplication is exactly the defect the Ask removes (CLAUDE.md §Engineering Judgment, single source of truth).
- **Add a grep-test asserting the literal appears in one shell file:** set aside. Any change to the value already fails the Python literals (row 7). A re-added duplicate with the same value breaks nothing at runtime.
- **Make the Python tests read the constant from `_lib.sh`:** set aside. That would remove the only check on the wire format that does not depend on the shell code.

**Root:** The plan-review covered-path prefix is defined twice in shell (`marker.sh:86`, `announce-approved-plan-path.sh:112`). Only "Paired literal" comments keep the two in step. The hook's Known gaps describe failed commands and the prefilter wrongly. The hook tests build a payload shape the harness never sends: an `exit_code` key, and a failed write reaching `PostToolUse`. Two docs are also off: one docstring lists the wrong consumers, and one design-decision sentence is ambiguous.

**Givens** (fixed, beyond this design's reach):
- **G1.** A Bash command that exits non-zero fires `PostToolUseFailure`, not `PostToolUse`, and that payload has no `tool_response`. The Claude Code harness owns event dispatch. (row 1)
- **G2.** A `PostToolUse` Bash `tool_response` carries `stdout`, `stderr`, `interrupted`, `isImage`, and `noOutputExpected`, with no `exit_code`. The payload shape belongs to the vendor. (row 2)
- **G3.** A hook `systemMessage` reaches the engineer, not the model. Rendering belongs to the vendor. (row 3)

**Mechanisms** (each anchored):
- **M1:** add `_LIB_PLAN_REVIEW_COVERED_PATH_PREFIX` to `_lib.sh` directly after `_lib_active_plan_hash` (which ends at :1144), with a two-line comment. `anchors: row4`
- **M2:** `marker.sh` deletes its comment and definition (:84-87) and prints with the `_lib.sh` constant at :647. `anchors: row5`
- **M3:** the hook deletes its header "Paired literal" block (:18-20) and its local definition (:112). It matches and strips with the `_lib.sh` constant at :120 and :124. `anchors: row6`
- **M4:** both Python tests keep their literal, and each comment says why. `anchors: row7`
- **M5:** the three failure bullets become one bullet. It states the `PostToolUseFailure` fact and the resulting chain gap (row 8). A one-line `assert matchers_registering("PostToolUseFailure") == []` in the existing registration test pins the premise behind "never sees it". `anchors: row1`
- **M6:** the prefilter bullet changes to say the match is ordered, and it names the `jq` spawns. `anchors: row9`
- **M7:** a new Known-gaps bullet covers a failed second `_lib_active_plan_files` call. `anchors: row10`
- **M8:** five hand-built `exit_code` payloads switch to `_observed_tool_response`. The failed-write test becomes a test that the hook reads only stdout. `anchors: row2`
- **M9:** delete the bash 3.2 comment above `TestRelay`. `anchors: row12`
- **M10:** add the real-marker pairing case from a linked worktree. `anchors: row13`
- **M11:** no explicit `exit 0` in `marker.sh`. `anchors: row14`
- **M12:** the `test_lib_path_char_allowlist.py` docstring names both callers. `anchors: row15`
- **M13:** add a CHANGELOG `[Unreleased]` sub-bullet for the Step 7 push offer. `anchors: row16`
- **M14:** make the design-decision sentence unambiguous. `anchors: row17`
- **M15:** one `code-writer` dispatch covers all eight files. M1, M2, and M3 land in one commit: the hook and `marker.sh` abort under `set -u` if read against an `_lib.sh` without the constant, so a revert takes all three shell files together. The constant's name runs through `_lib.sh`, `marker.sh`, the hook, and both test comments. The Known gaps text and the test edits describe the same payload facts. The two doc edits are one sentence each, and a separate dispatch would cost more than it saves. `anchors: root`

**Over-powered-primitive check.** M1 is the only mechanism with wider scope: a new symbol in a library that every hook sources. Three lighter options were weighed:
1. **Keep both literals:** no shared symbol, but it keeps the duplication the root names. `anchors: row4`
2. **A new `_plan-review-lib.sh` sidecar next to the hook:** narrower than `_lib.sh`, but `.claude/rules/bash-unit-test-seams.md` says a new bare `_*-lib.sh` under `claude/.claude/hooks/` fails CI today. Both consumers would also need a second source line. `anchors: row4`
3. **Keep the definition in `marker.sh` and have the hook read it from there:** sourcing `marker.sh` would load about 1150 lines into a hook that runs on every Bash call. Grepping the value out of the script's text is the extraction pattern `shell-script-conventions.md` forbids. `anchors: row5`

So `_lib.sh` is the cheapest shared home. Both consumers already source it (rows 5 and 6), and it gains one plain assignment: no fork, no function.

**Assumption rows:**

1. `[verified: probe events, lines 1-2]`
   - `true` fired `PostToolUse` with a `tool_response`.
   - `false` (exit 1) fired `PostToolUseFailure` with `"error":"Exit code 1"` and no `tool_response`.
   - This was one probe on one harness version, for the bare commands `true` and `false` only. Other non-zero statuses, and the chain case (`marker.sh write plan-review && git commit` with the later command failing), are inferred from "the overall exit status decides the event", not run.
   - `[verified: claude/.claude/settings.json]` The hook is registered once, under `PostToolUse` matcher `Bash`, and the file has no `PostToolUseFailure` key. `test_announce_approved_plan_path.py:143-156` pins only `PostToolUse == ["Bash"]` and `PreToolUse == []`, so M5 adds the `PostToolUseFailure` assertion that makes the header's "never sees it" claim pinned.
2. `[verified: same probe, line 1]` The observed key set matches `_observed_tool_response` (`test_announce_approved_plan_path.py:102-111`). Neither has an `exit_code` key.
3. `[unverified]` A `systemMessage` reaches the engineer but not the model.
   - `docs/hooks.md:206` says "`systemMessage` reaches the user only". That is repo prose, not a harness run.
   - The banner rendering for the engineer is reported as engineer-confirmed by the consumed handoff, with no verbatim quote this session.
   - M14 keeps the existing claim rather than adding a new one, so nothing newly depends on this row.
4. `[verified: _lib.sh:166, :875, :886, :1930, among the other top-level _LIB_* plain assignments; :1009-1144]` Constants in `_lib.sh` are plain, unexported globals with a `#` block above. Each sits next to the helpers it serves. `_lib_active_plan_files` and `_lib_active_plan_hash` produce the set whose paths `marker.sh` prefixes.
5. `[verified: marker.sh:7-8, :10, :647]` `marker.sh` sources `_lib.sh` before `set -u`. The prefix is used in one place only.
6. `[verified: announce-approved-plan-path.sh:79-81, :120]` The hook sources `_lib.sh`, or exits 0 if it can't, before the first use. So the constant is always set when it is read.
   - `[verified: require-worktree-for-git-writes.sh:337; deny-private-project-refs.sh:804-808]` Hooks already read `_LIB_*` constants without a shellcheck directive, so this needs no new suppression.
7. `[verified: test_marker_script.py:51-52, :630-633, :654-659; test_announce_approved_plan_path.py:32-33, :55-56, :425-459]` Why the pairing stays safe:
   - With one shell definition, `marker.sh` and the hook cannot disagree.
   - The Python literals are the only check on the wire format. The marker tests assert exact stdout, and the hook tests build prefixed lines by hand, so any change to the `_lib.sh` value fails both files.
   - `TestPairingWithMarkerScript` no longer pins prefix agreement, because both scripts read one constant and its real-marker cases pass under any prefix value. It pins wiring (a misspelled constant name aborts `marker.sh` under `set -u` and trips the `returncode == 0` assertions) and the path shape the real marker prints. Its docstring at `test_announce_approved_plan_path.py:429-430` ("Pins the paired prefix literal") is therefore rewritten (Critical files).
   - The test literal falls under CLAUDE.md's DAMP-test-code exception, so it is not a defect.
8. `[verified: test_announce_approved_plan_path.py:190, :203-213]` `write_then_git_commit` is in `GATE_ALLOWED_SHAPE_PARAMS`, and the real shape gate allows it. So the shape gate allows a chain where the marker write runs before a command that can fail.
9. `[verified: announce-approved-plan-path.sh:74-77, :79, :83, :86, :95-96]` The prefilter glob `*marker.sh*plan-review*` is ordered. After it passes, the hook sources `_lib.sh` and runs up to two `jq` calls (tool_name, then command) before the trigger regexes reject.
10. `[verified: marker.sh:631-648; announce-approved-plan-path.sh:143]` Suppose the second `_lib_active_plan_files` call fails:
    - `COVERED_PLAN_PATHS` stays empty.
    - The marker write still runs.
    - Nothing is printed, and the arm exits 0.
    - The hook sees zero prefixed lines and exits silently.
    - This was verified by reading the code, not by executing the failure. No test pins it, because it would need a stateful git shim.
11. `[verified: announce-approved-plan-path.sh:2; test_hook_alignment.py:8-13; CLAUDE.md § "Hook threat model"]` The tier framework does not decide which of these gaps count as defects:
    - The hook is `hook-class: informational` and has no `# tier-threat-model:` line.
    - The tier line is required only for `hook-class: gate`.
    - Non-gate hooks get no waiver.

    The Known gaps are documented because the engineer sees this behavior. This change fixes none of them.
12. `[verified: announce-approved-plan-path.sh:113-141]` The hook builds its list by string concatenation and has no array. The deleted comment describes verifying a constraint the hook avoids, and no test there exercises bash 3.2. `[unverified]` Whether the macOS run it cites ever happened. The deletion does not depend on it. The hook's own comment near its list-building code keeps the constraint itself. The deleted comment also recorded that Linux CI does not exercise bash 3.2, and `test_no_bash4_constructs.py` would not catch a reintroduced empty array under `set -u`. The deletion is part of the recovered scope, so that caveat is accepted as lost.
13. `[engineer-verified: "Ask the architect"]` The engineer's answer to whether to include the optional pairing test. It states the question went to the architect and nothing about the outcome.
    - The architect's decision is to include it. That is the architect's output, not the engineer's, for these reasons:
    - `[verified: marker.sh:208-220; _lib.sh:573-578]` `marker.sh` roots plan paths at `git rev-parse --show-toplevel`. In a linked worktree under `.claude/worktrees/<name>/`, that gives a plan path with a second `.claude` segment.
    - `[verified: test_announce_approved_plan_path.py:425-459, :304-311]` Every existing real-marker case runs from a primary repo. The nested-worktree case feeds the hook a hand-built path.
    - `[verified: claude-config CLAUDE.md, "Worktree enforcement is active"]` This repo runs its approvals from that layout.
    - `[verified: test_marker_script.py:2293]` It costs one test and one inline `git worktree add -q -b`, which that test already does.
    - What the test adds is that `marker.sh` roots its printed path at the linked worktree's own toplevel and the hook relays it unwithheld. The hook's acceptance of a nested path alone is already pinned by a hand-built case (`test_announces_a_plan_path_inside_a_nested_worktree_verbatim`).
14. `[engineer-verified: "See prior response. Spawn once"]` The engineer's answer to whether to include the optional explicit `exit 0`. It states only that the prior response applies and that one agent is to be spawned.
    - `[unverified]` The session read that answer as delegating this decision to the same single architect dispatch as row 13. The words do not say so.
    - The architect's decision is to skip it. That is the architect's output, not the engineer's, for these reasons:
    - `[verified: marker.sh:637, :645-648]` Every covered path is stored with a trailing newline, and `<<<` adds one more. So the loop's last iteration always reads an empty line and runs `continue`. After a successful write, the loop and the arm return 0 whatever the `printf` calls returned.
    - `[verified: test_marker_script.py:630, :668; test_announce_approved_plan_path.py:139]` Tests already pin exit 0 for writes with one path and with zero paths. `_run_real_marker_write` asserts it on every pairing case, M10's included.
    - `[verified: marker.sh:541-543, :579-581, :659-661]` None of the other write arms ends in an explicit `exit 0`.
    - `[verified: marker.sh:642-644]` A failed marker write already exits non-zero through `|| exit` before the loop runs, so an `exit 0` would change no outcome.
15. `[verified: grep of _lib_passes_path_char_allowlist across the worktree]` The callers are `announce-resume-command.sh:83` and `:92`, and `announce-approved-plan-path.sh:125`. `_respond-pr-lib.sh:29` and `test_respond_pr_lib.py:230` only mention it.
16. `[verified: claude-skills/skills/plan-it/SKILL.md:141-153; CHANGELOG.md:40-45; select-tests.py:555]` The push offer has shipped and has no CHANGELOG line. `CHANGELOG.md` selects no tests. That no test checks CHANGELOG format comes from the exploration report; it was not reopened.
17. `[verified: docs/design-decisions/plans-shared-by-path-not-early-pr.md:5; announce-approved-plan-path.sh:39-45; shipping PR body]`
    - "not always shown" is accurate: an empty plan set, plan mode, a delegated review, and a withheld path all show nothing.
    - The shipping PR deferred a finding that the sentence states the banner's visibility as settled. The engineer's sighting (row 3) settles rendering for the engineer, not whether the model sees it.
    - The edit keeps both claims and says who the banner reaches.
18. `[verified: test_announce_approved_plan_path.py:19, :380]` The file already imports and calls `init_git_repo_with_commit`, and no `_init_repo` is left. The earlier branch's edit to this file is already on this branch's base, so the handoff's conflict concern no longer applies.

## Critical files

There is one `code-writer` dispatch covering every file below (M15). Its verification command is in **Verification**.

- **`claude/.claude/hooks/_lib.sh`:** insert after `_lib_active_plan_hash`'s closing brace (:1144), as a blank line followed by these three lines, so the existing blank line at :1145 separates them from `_lib_hash_diff_text`'s comment block:
  ```bash
  # Prefix marker.sh's `write plan-review` prints before each covered plan path, one line per path.
  # announce-approved-plan-path.sh relays only stdout lines that start with it.
  _LIB_PLAN_REVIEW_COVERED_PATH_PREFIX='plan-review marker covers: '
  ```
- **`claude/.claude/scripts/marker.sh`:**
  - Delete :84-87 (the comment, the definition, and the blank line after them).
  - At :647, print `"$_LIB_PLAN_REVIEW_COVERED_PATH_PREFIX"`.
  - Add no `exit 0` (M11).
- **`claude/.claude/hooks/announce-approved-plan-path.sh`:**
  - Delete header :18-20 (the "Paired literal" two-liner and the `#` line after it).
  - Delete :112.
  - Use `"$_LIB_PLAN_REVIEW_COVERED_PATH_PREFIX"` at :120 and :124.
  - Keep header line 5's prose about the line format.
  - Known gaps changes, with all other bullets unchanged:
    - Insert this after the plan-mode bullet (:42):
      ```
      #   - If marker.sh's second _lib_active_plan_files call, which lists the paths
      #     to print, fails after the hash succeeded, the marker is recorded and
      #     nothing is announced.
      ```
    - Replace the prefilter bullet (:53-55) with:
      ```
      #   - A raw payload whose text contains `marker.sh` and, after it, `plan-review`
      #     (in the command, cwd, transcript path, or output) passes the raw-stdin
      #     prefilter. It then pays the _lib.sh sourcing cost and up to two jq spawns
      #     before the trigger rejects it.
      ```
    - Replace the last three bullets (:59-64) with:
      ```
      #   - A Bash command that exits non-zero fires PostToolUseFailure, not
      #     PostToolUse (observed for exit status 1), so this hook never sees it.
      #     A chain the shape gate allows, such as
      #     `marker.sh write plan-review && git commit`, therefore records the
      #     marker and announces nothing when a later command in it fails.
      ```
- **`claude/.claude/hooks/tests/test_announce_approved_plan_path.py`:**
  - **:32 comment** becomes `# A literal rather than _lib.sh's _LIB_PLAN_REVIEW_COVERED_PATH_PREFIX, so a changed prefix fails here.`
  - **:265-266:** delete the bash 3.2 comment.
  - **:362:** use `_payload(_record_completion_command(), _observed_tool_response(_covered_stdout(hostile_path)))`. Keep `_run_hook_raw`, because that test asserts on raw stdout.
  - **:393:** the `object_without_stdout` param becomes `_observed_tool_response("")` with the `stdout` key dropped, for example `{key: value for key, value in _observed_tool_response("").items() if key != "stdout"}`. Keep the param id.
  - **`TestRegistration` (:143-156):** add `assert matchers_registering("PostToolUseFailure") == []` next to the existing `PostToolUse` and `PreToolUse` assertions.
  - **:429-430 docstring** of the real-marker pairing test: replace "Pins the paired prefix literal: marker.sh's real output, not a hand-built line, must be what the hook relays." with a docstring stating that the case pins wiring (both scripts resolve the shared constant) and the path shape the real marker prints, and that the Python `COVERED_PATH_PREFIX` literal is what pins the wire format.
  - **:413-422:** rewrite in place, in the same class, and drop the old inline comment:
    ```python
    def test_reads_stdout_only_ignoring_a_prefixed_line_in_stderr(self, isolated_home):
        tool_response = {**_observed_tool_response(""), "stderr": _covered_stdout(PLAN_PATH)}
        payload = _payload(_record_completion_command(), tool_response)

        assert _message(_run_hook_raw(payload, isolated_home)) is None
    ```
  - **:510** becomes `_observed_tool_response("total 0\n")`, and **:523** becomes `_observed_tool_response("")`.
  - **New test in `TestPairingWithMarkerScript`:**
    ```python
    def test_announces_the_plan_path_marker_sh_really_prints_from_a_nested_linked_worktree(
        self, isolated_home, git_repo
    ):
        """marker.sh roots the plan path it prints at a linked worktree's own
        toplevel, and the hook relays that path unwithheld."""
        worktree = git_repo / ".claude" / "worktrees" / "feature"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)], cwd=git_repo, check=True
        )
        marker_stdout = _run_real_marker_write(worktree, isolated_home)

        message = _announce(isolated_home, _record_completion_command(), marker_stdout)

        assert message == (
            f"plan-review marker recorded for: {git_toplevel(worktree)}/.claude/plans/p.md"
        )
    ```
  - Reuse `_observed_tool_response` (:102), `_payload` (:59), `_announce` (:114), `_run_real_marker_write` (:119), `git_toplevel` (helpers), and the `git_repo` fixture (`hooks/tests/conftest.py:465`). `subprocess` is already imported.
- **`claude/.claude/hooks/tests/test_marker_script.py`:** a comment-only edit at :51, with the same text as the :32 comment above.
- **`claude/.claude/hooks/tests/test_lib_path_char_allowlist.py`:** replace the module docstring (:1-10) with:
  ```
  """Unit tests for _lib.sh's _lib_passes_path_char_allowlist, the pure-bash
  byte-class gate its callers run on a path before putting it in their output:
  - announce-resume-command.sh, on FILE_PATH and a resolved worktree root,
    before interpolating either into its emitted resume-context command.
  - announce-approved-plan-path.sh, on each plan path it relays.

  These source _lib.sh directly and call the function through /bin/bash -- no
  hook invocation, no JSON payload -- mirroring test_lib_mask_shell_quotes.py.
  announce-resume-command.sh's own test file keeps one subprocess case per
  branch; the byte-class matrix lives here instead.
  """
  ```
- **`CHANGELOG.md`:** insert after :43, inside the existing `/plan-review` approval entry:
  `  - When a plan adds a design document or defines a contract other teams depend on, `/plan-it` asks through `AskUserQuestion` whether to push the branch with no PR. It asks only on a non-default branch with no PR. The question names the push URL, the commits the push would publish, and whether the repository is public. No answer means no push.`
- **`docs/design-decisions/plans-shared-by-path-not-early-pr.md`:** on line 5, replace `because the hook's banner is not always shown and the session cannot see it.` with `because the hook's banner is not always shown and reaches only the engineer, so the session cannot tell whether it appeared.`

Not touched: `docs/hooks.md:187` and `docs/scripts.md:123`. Both describe the printed line format and name no place where the prefix is defined.

## Verification

Run everything from the worktree root. The worktree has no `.venv`, so use the main checkout's (`<main-checkout>/.venv/bin/...`).

1. **Fast loop:** `<main-checkout>/.venv/bin/pytest claude/.claude/hooks/tests/test_announce_approved_plan_path.py claude/.claude/hooks/tests/test_marker_script.py claude/.claude/hooks/tests/test_lib_path_char_allowlist.py -q`. Everything should pass, including the new linked-worktree case.
2. **Negative control for row 7.** This is a scratch edit and is never staged.
   - First copy `_lib.sh` to a scratch file outside the repo. Then change one character of the `_lib.sh` value and re-run the first two files from step 1.
   - **Expected to fail:** the hand-built relay cases, such as `test_announces_for_every_gate_allowed_shape_containing_write_plan_review`; the exact-stdout marker cases, such as `test_write_plan_review_prints_absolute_path_of_untracked_plan`; and the two real-marker cases that assert on the Python literal, `test_withholds_the_real_marker_output_for_a_repo_under_a_directory_with_a_space` and `test_withholds_every_path_when_the_marker_really_prints_a_txt_plan_beside_an_md_plan`.
   - **Expected to pass:** `test_announces_the_absolute_plan_path_marker_sh_really_prints`, `test_announces_both_paths_when_the_marker_really_prints_two_md_plans`, and the new linked-worktree case. They pin wiring and path shape, not the prefix value (row 7).
   - Restore `_lib.sh` from the scratch copy, not with `git checkout`, which would also erase the M1 insertion. Confirm `diff` against the copy is empty and `git diff claude/.claude/hooks/_lib.sh` shows only the M1 insertion.
3. **Gate:** `<main-checkout>/.venv/bin/python3 claude/.claude/scripts/select-tests.py`. It resolves `pytest` as the sibling of `sys.executable` (select-tests.py:984-990).
   - **Expect a domain selection, not the full suite.** No changed path is in `GLOBAL_TRIGGER_PATHS`, which lists only `helpers.py`, `pyproject.toml`, and `select-tests.py` (select-tests.py:396-400).
   - **How the changed paths map:**
     - `_lib.sh`, the hook, and the three test files select `hooks/tests` (:547).
     - `_lib.sh` and the hook also select `scripts/tests` (:744) and the transcript-analysis targets (:709).
     - `marker.sh` selects `scripts/tests` (:548), plus `hooks/tests` and the skills tests (:735).
     - The test files also select `claude/.claude/tests` (:762) and the select-tests test (:763).
     - `docs/design-decisions/` selects the hooks and skills tests (:750).
     - `CHANGELOG.md` selects nothing (:555).
   - **The scoped run covers every consumer.** Every in-repo consumer of `_lib.sh` has its tests under `hooks/tests` or `scripts/tests`. Plugin hooks do not source the stowed `_lib.sh` (test_marker_script.py:53-55).
   - **No hand-run full suite is needed.** Neither of CLAUDE.md's two full-suite cases applies.
   - **Repo-wide checks ride in this selection:** `test_shellcheck.py`, `test_hook_alignment.py`, `test_doc_counts.py`, and `test_no_bash4_constructs.py`.
4. **Lint:** `<main-checkout>/.venv/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 <main-checkout>/.venv/bin/shellcheck`.
5. **Residue checks:**
   - `git grep -n -i "paired.*literal" -- claude/` prints nothing.
   - `git grep -n "plan-review marker covers: '" -- '*.sh'` prints only the `_lib.sh` line.
   - `git grep -n exit_code -- claude/.claude/hooks/tests/test_announce_approved_plan_path.py` prints nothing.

## Out of scope

- **Registering the hook under `PostToolUseFailure` to announce a failed chain.** That payload has no `tool_response` (row 1), so there is no structured stdout to relay. Whether its free-form `error` string carries the command's output was not probed. The chain gap is documented, not fixed.
- **A test pinning the ordered prefilter glob** (a payload with `plan-review` before `marker.sh` never spawns `jq`). The corrected Known-gaps wording rests on reading the glob, as the old wording did. A pin is a cheap follow-up.
- **The stdout-only payloads at `test_announce_approved_plan_path.py:409`, `:477`, and `:538`.** They are minimal shapes but not impossible ones, and they carry no `exit_code`.
- **`.claude/plans/plan-share-links.md:148` and `:156`.** They still describe the paired-literal design, but a committed plan is a preserved record.
- **Hook header line 5, `docs/hooks.md:187`, and `docs/scripts.md:123`.** They describe the printed line format, not where the prefix is defined.
