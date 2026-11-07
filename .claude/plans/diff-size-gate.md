# Diff-size gate and ack script (GH-1364 piece 4, PR 2 of 3)

## Context

Goal: land the commit-time half of the diff-size prompt, so a commit that brings a branch's cumulative diff to 400 or more changed lines is denied until the engineer answers `Split now` or `Proceed anyway`.

Ask: GH-1364 Plan item 4, "Diff-size prompt. When a branch's diff crosses a fixed line threshold, the agent stops and asks the user via `AskUserQuestion`: split now, or proceed anyway." The engineer cut the design into three stacked PRs, answering "Three stacked PRs (Recommended)" (row 42 of `.claude/plans/diff-size-prompt.md`), and said "the plan probably should stay as the design of record" (row 64).

Why now: PR 1 is merged at `34a28ef7`. It shipped the shared helpers (`_lib_diff_size_gate_state`, `_lib_diff_size_measure`, `_lib_diff_size_latch_file`, the threshold and cap constants) and their tests, and nothing calls them yet. PR 2 supplies the callers: the gate hook (M1, M5, M6), `diff-size-ack.sh` and its `permissions.ask` entry (M2), and the registration and docs (M9). PR 3, the plan-time sentences, follows under its own plan.

Design of record: `.claude/plans/diff-size-prompt.md`. This plan cites its mechanisms by number and restates none. Where this plan departs from one, it names the departure and governs for PR 2.

Redaction: the plan and the PR may state only the bucket bounds 150, 400 and 800 and the direction "rising with PR size". No count taken from local transcripts appears here.

## Approach

PR 2 adds the gate `require-diff-size-prompt.sh` and the ack script `diff-size-ack.sh` as the design of record specifies them (M1's hook steps, M2, M5, M6), registers both, and documents them (M9). Both call PR 1's `_lib_diff_size_gate_state`, so neither carries a read sequence of its own. Each mechanism below names where it departs from the design. The largest departure defers the pre-merge live observations to a follow-up (row 3).

Alternatives weighed for the choices this plan makes. The design's alternatives list stands for the rest.
- **An explicit `timeout` on the hook's settings entry.** Set aside. No PreToolUse gate entry sets one (row 7), and a literal there would have to track row 9's bound by hand. The documented default sits well above that bound (row 8), and Verification step 1 re-reads it before any code is written.
- **Barring every subagent from the ack inside the new gate.** Set aside. The gate already runs on every Bash call, so it can match the ack's command, which defeats the design's reason for leaving the ack open (design line 55). The plan therefore bars the roster agents and no wider (rows 11, 28). The bar is narrower than the marker-shape gate's, which scans the whole command text: it matches one spelling (row 36). A dispatcher can legitimately hand another subagent the whole commit flow, and M5's report-to-dispatcher clause covers that case.
- **A `review-permissions/REFERENCES.md` decision entry.** Not added. That section justifies entries that pre-authorize or split a command (row 14). The ack's entry is ask-only and pre-authorizes nothing, and the `docs/hooks.md` bullet states it (M9).
- **Recording the deferred observations in a new doc or an issue.** Set aside. This plan's Out of scope ships with the PR, and the `docs/hooks.md` bullet already states the ask's mode behavior as documented, not observed (P3).
- **Two `code-writer` dispatches.** Set aside. The header (M6) and the docs bullet (M9) need the same background, and the cross-tests read the hook, the script and `settings.json` together.

### Assumption ledger

"Row N" below is this plan's row. "Design row N" and M1-M9 are `.claude/plans/diff-size-prompt.md`'s.

**Root:** PR 1's helpers have no production caller, so no commit is yet stopped for its diff size (Context).

**Givens:**
- G1. A hook cannot call `AskUserQuestion`, so the deny hands the question to the agent (the design's G1). Reason: the harness's hook contract imposes it.
- G2. The harness sets the default hook timeout and what a timed-out hook does. This plan controls only whether the entry sets `timeout`. Reason: the vendor owns the hook runtime.

**Rows:**
1. The design file stays the design of record, and this plan cites it instead of restating it (design rows 64, 65). [engineer-verified: "the plan probably should stay as the design of record"]
2. This branch carries the second of three stacked PRs. [engineer-verified: "Three stacked PRs (Recommended)"]
3. The live check is deferred out of PR 2. [engineer-verified: "Defer live check (Recommended)"]
4. Row 3's option description is the dispatching session's proposal, not the engineer's words. It said PR 2 ships with the ask entry's behavior in auto mode and `bypassPermissions` documented, not observed, and that the per-spelling observations, control runs and `claude -p` run become a follow-up. It did not name the design's post-merge check, so this plan keeps that check in Verification as a non-gating step. [unverified]
5. PR 1 proceeded at about 850 changed lines. [engineer-verified: "Proceed at ~850 (Recommended)"]
6. The bar of keeping this PR well below PR 1's size is the dispatching session's relay, not a quote. [unverified]
7. No PreToolUse gate entry in `settings.json` sets `timeout`. The one PreToolUse `timeout` is on the non-gate `send-back-skipped-self-review.sh` entry. [verified: `settings.json:279-542`, `:539`]
8. The repo documents a 600-second default hook timeout, fetched 2026-10-17. It also says a gate registered with `onFailure: "block"` that times out blocks the call on Claude Code 2.1.295 or later. [unverified: `docs/hooks.md:403`, `:423` and `_lib.sh:87-95` state it; the hooks reference was not re-read for this plan]
9. Under a working `timeout`, the hook's worst-case wall time is the 102 seconds in `_lib_diff_size_gate_state`'s header plus two capped `jq` calls, the payload parse and the deny encoding, at 7 seconds each. That is about 116 seconds. [verified: `_lib.sh:4739-4741`, `:526`, `:428`, `:40`, `:70-72`]
10. With neither `timeout` nor `gtimeout` on `PATH`, `_lib_capped_for` runs every call uncapped, so only the harness's hook timeout bounds a stalled gate. [verified: `_lib.sh:132-139`, `:87-89`]
11. `enforce-marker-script-shape.sh` bars roster agents (`_LIB_NO_GATE_RELEASE_AGENTS`: the review-only agents, the plan-writer agents and `code-writer`) from marker and ledger state. It bars every other subagent from ledger state and from a `review-ledger.sh append` carrying `--engineer-quote`, "because only the main session holds the engineer's turn". It leaves marker state open to those subagents. Design line 55 names only the ledger bar. [verified: `enforce-marker-script-shape.sh:13-20`, `:435-448`; `_lib.sh:4540-4556`]
12. No roster agent is expected to reach the commit deny. `code-writer` never commits (design row 9), and plan-architect holds no Bash tool. Whether every review-only agent is kept from committing was not checked. [unverified: the review-only half]
13. An ask rule forces a prompt in every mode that can prompt, auto mode and `bypassPermissions` included. `dontAsk` denies it. The latch therefore records an engineer's approval of the bare command whoever calls it, wherever the prompt reaches the engineer. Whether a subagent's forced prompt reaches the engineer was not checked. [unverified: `docs/security-hardening.md:254-258` documents it from the permissions pages, unobserved]
14. `review-permissions/REFERENCES.md` § "Decisions on global allow list entries" records why an entry that pre-authorizes or splits a command passes the checklist. Its two cleanup-script entries justify an `allow`/`ask` split. [verified: `REFERENCES.md:3-45`]
15. `test_transcript_denials.py` fails when a hook declaring `DENY_GATE_LABEL` is missing from `_BOOTSTRAP_FALLBACK_HOOKS`, and `denials.py`'s `_DENIAL_HOOK_LABELS` holds one entry per hook label. The design names neither. [verified: `test_transcript_denials.py:283-338`; `denials.py:128-157`]
16. `_SELF_FILTERING_BASH_GATES` pins only that each listed gate's PreToolUse entries carry no `if`. [verified: `test_hook_alignment.py:872-910`]
17. The commit predicates treat a fragment as a git call when any word in it is `git`, so `FOO=1 git commit` is recognized. The count runs in the hook's own environment, so a `GIT_DIR`, `GIT_WORK_TREE` or `GIT_INDEX_FILE` prefix redirects only the commit. `deny-pii-in-commits.sh` lists the same gap. [verified: `_lib.sh:1970-1983`; `deny-pii-in-commits.sh:165-171`; `test_parse_git_command.py:266`]
18. `_lib_diff_size_measure` returns 1 whenever `_lib_git_inprogress_state` reports a paused operation. A `--continue` form, which git runs only while one is paused, is therefore recognized and never counted. [verified: `_lib.sh:4694-4695`; the paused-state precondition is git knowledge, not probed]
19. A stack key's value passes `_lib_stack_base_name_shape` before use. The default-branch arm returns `origin/HEAD`'s target with no shape check, and the deny text prints the base. [verified: `_lib.sh:1521-1553`, `:1349-1358`, `:1418-1424`]
20. The count exports `GIT_NO_LAZY_FETCH=1`, so in a partial clone a blob the count lacks fails the count on git that honors the variable. Older git may fetch it from the promisor remote under the 30-second cap. [verified: `_lib.sh:4711-4716`]
21. `_lib_diff_size_gate_state`'s header names its callers "the diff-size gate and its acknowledgment script", not by file. [verified: `_lib.sh:4724`]
22. `.gitignore` lists `claude/.claude/.architect-consult-latch.d/` at line 154, and `TestHookWrittenStateStaysOutOfGit` pins only the paths it lists. [verified: `.gitignore:154`; `test_record_session_config_keys.py:594-607`]
23. Outside tests and `.claude/plans/`, the diff-size helper names appear only in `_lib.sh` and in one comment in `deny-private-project-refs.sh`. [verified: a grep of this worktree]
24. PR 2 ships at the `Projected diff:` size despite its being over 400. [engineer-verified: "Proceed anyway"]
25. The engineer kept three extras that the `Ask:` line does not itself call for. [engineer-verified: "Cross-tests"] [engineer-verified: "README row + scripts.md"] [engineer-verified: "CHANGELOG bullet"]
26. The runbook subsection is not part of PR 2. [engineer-verified: "I don't think we neeed the runbook... if the hook has a bug, we file an issue... we shouldn't have to work around it."]
27. The engineer confirmed rows 1, 2 and 5's quotes, carried from the earlier session, as their own words. [engineer-verified: "Yes, those are mine"]
28. The new gate denies the bare ack command to a roster agent. [engineer-verified: "Bar roster agents (Recommended)"]
29. Row 28's option description is the dispatching session's proposal, not the engineer's words: the gate matches the bare ack command and denies when the caller is in `_LIB_NO_GATE_RELEASE_AGENTS`, other subagents stay unbarred, and it costs roughly 25 changed lines. [unverified]
30. The roster is `_LIB_NO_GATE_RELEASE_AGENTS` and the roster test is `_lib_is_no_gate_release_agent "$AGENT_TYPE"`, keyed on `agent_type`, as `enforce-marker-script-shape.sh` uses it. `AGENT_ID` is only the non-empty subagent discriminator and is not the roster key. [verified: `_lib.sh:4549-4556`, `:515-518`; `enforce-marker-script-shape.sh:445-448`]
31. `_lib_stack_base_name_shape` guards a stack key's value, and the default-branch arm has no such guard. A ref name can carry shell metacharacters, quotes, bidi controls and non-ASCII characters. [verified: `_lib.sh:1521-1553`, `:1568`, `:1349-1358`, `:4694-4700`]
32. A repo whose count always reaches the 30-second cap pays that wait on every commit and is never gated, because a cap kill makes the measure return 1 and nothing caches it. [verified: `_lib.sh:4716-4717`, `:4755`]
33. `denials.py` counts denies by `DENY_GATE_LABEL`, so a deny is visible in transcripts for as long as they are retained. [verified: `denials.py:128-157`]
34. PR 2 ships at about 740 changed lines, above the about 550 row 24 answered, because review findings added the roster bar, the base-name check, shared builders and test cases. [engineer-verified: "Proceed at ~740 (Recommended)"]
35. A second round of review added test cases, and PR 2 ships at about 810 changed lines. [engineer-verified: "Proceed at ~810 (Recommended)"]
36. The roster bar and the ask entry each cover one spelling of the ack command. The plan discloses that and keeps the plain `cooperative` tier. [engineer-verified: "Disclose single-spelling coverage (Recommended)"]
37. The cross-tree import of `_lib_sh_helpers.py` is declared in `select-tests.py`. [engineer-verified: "Declare the edge (Recommended)"]
38. The `docs/hooks.md` bullet keeps its one sentence on the terminal-run ack's config directory. [engineer-verified: "Keep the one sentence (Recommended)"]
39. Rows 35-38 quote option labels only. Their option descriptions are the dispatching session's proposals: about 810 lines with R10 and R11 included, about 10 further lines for the `select-tests.py` edge, and a Known gap plus a header sentence for the bar's scope. The `docs/hooks.md` sentence that running the ack tells an unreadable state from a below-threshold one (P3) is also the session's proposal, covered by no quoted answer. [unverified]
40. `select-tests.py` keeps hand-enumerated sets of cross-tree test imports, and `_lib_sh_helpers.py` is in neither. No test fails when a member is missing. [verified: `select-tests.py:209-216`, `:236-249`, `:280-285`, `:853`; `test_select_tests.py:1636-1700`, per the test review]
41. `bash_input` takes `agent_type` but no `agent_id`, so a payload carrying both is built by hand. [verified: `helpers.py:836-848`, per the test review]
42. Only the default-branch arm can deliver a hostile base to the hook or ack, because the stack-key arm is shape-checked inside `_lib_configured_stack_base` and `_lib_diff_size_measure` returns 1 on its status 2. The default arm needs `refs/remotes/origin/HEAD` as a symref to an existing `refs/remotes/origin/<name>` that shares history with HEAD. [verified: `_lib.sh:1349-1358`, `:1521-1538`, `:4694-4700`, `:4704`, per the test review]
43. The engineer chose the narrower of two ways to handle a CI failure of the ack's locale-pinned `%q` case. [engineer-verified: "Yeah, I think minimal makes sense."]
44. The engineer wants the existing shape check, placeholder rule and hostile-base matrices kept. [engineer-verified: "I would say let's keep it."]
45. The narrower way, as the session proposed it, drops only the locale-pinned `%q` case and its header and plan items. Hostile text in the ack's stderr line is outside the cooperative tier, so the ack makes no claim to neutralize it. [unverified]
46. The implementation was accepted past Verification 7's 900-line ceiling. [engineer-verified: "Proceed at ~1313"]

**Mechanisms:**
- **P1. The gate hook (M1's hook steps, M5, M6).** New executable `claude/.claude/hooks/require-diff-size-prompt.sh`, as M1, M5 and M6 specify. Additions:
  - A header line states that the settings entry has no `if` and the hook filters on tool name and command itself, as `enforce-marker-script-shape.sh:25-29` does.
  - The header carries the one-line comment naming its dependency on `docs/hooks.md`'s `deny-invisible-commit-content.sh` bullet (M9).
  - M6's failure-direction list gains one item: a harness hook timeout blocks the commit on Claude Code 2.1.295 or later, per `_lib_capped_for`'s header (row 8).
  - The header states the hook's wall-time bound as `_lib_diff_size_gate_state`'s plus two capped `_lib_jq` calls at 7 seconds each (row 9).
  - The header names that function's header as the owner of its figure and carries no literal sum. The writer re-derives the two `jq` figures from the calls written.
  - The header also states the per-Bash-call cost: one extra hook bootstrap on every Bash call before the `*git*` prefilter. It cites `.claude/plans/hook-bootstrap-cost.md` for the measured per-hook cost and takes no new measurement.
  - Between the tool-name check and the `*git*` prefilter, the hook denies the bare command `~/.claude/scripts/diff-size-ack.sh` when `_lib_is_no_gate_release_agent "$AGENT_TYPE"` succeeds, with or without an `AGENT_ID` (rows 28, 30). The reason says a review-only, plan-writer or `code-writer` agent cannot release the gate and reports to its dispatcher.
  - The match is exact equality of the whole command, after trimming leading and trailing whitespace, with `~/.claude/scripts/diff-size-ack.sh`. A command that merely mentions the path (`grep`, `cat`, `ls`), carries an argument, or uses another spelling is not matched.
  - It matches the one spelling the ack's ask entry matches, so another spelling falls to the mode's default handling (design row 56).
  - The bar sits before any repository read and never reads the latch or the repository, so it denies from a directory outside any repository.
  - The header states that the bar keeps roles consistent and is not a security control (row 36).
  - Before interpolating the base into the deny reason, the hook runs it through `_lib_stack_base_name_shape` (row 31). A base that fails prints as `the base branch` in place of its name, and the count and threshold still print. The placeholder replaces the whole `origin/<name>` token, so the reason never reads `origin/the base branch`. The writer reads the helper's contract at `_lib.sh:1561-1581`.
  - The hook and the ack each apply the shape check separately, because `_lib.sh` stays unchanged beyond the header line. A third consumer of `_lib_diff_size_gate_state`'s second line must apply it too. The two hostile-name matrices, the hook's and the ack's, exist to catch drift between the two copies.
  - M6's Known gaps gain five items:
    - With neither `timeout` nor `gtimeout` on `PATH`, every capped call runs uncapped, so a stalled git call holds the commit until the harness's hook timeout (row 10).
    - A `GIT_DIR`, `GIT_WORK_TREE` or `GIT_INDEX_FILE` prefix on the commit redirects only the commit, not the count (row 17).
    - The count matches the landed commit only for the shapes `deny-invisible-commit-content.sh` closes, so that hook's header Known gaps apply here too (design row 23).
    - In a partial clone, a blob the count lacks leaves the commit unmeasured on git that honors `GIT_NO_LAZY_FETCH`, and older git may fetch it under the count cap (row 20).
    - A base name that passes the shape check prints verbatim in the deny reason and in the ack's stdout, and the gate imposes no length bound on the printed name.
      - The check bounds the character set (`[A-Za-z0-9._/-]`, `_lib.sh:1563`) and nothing more.
      - The default-branch arm takes the name from `origin/HEAD`'s target with no shape check of its own (`_lib.sh:1355-1357`, row 42).
  - M6's item on the base ref name in the deny text is rewritten (rows 19, 31):
    - The hook and the ack shape-check the base before printing it.
    - A name that fails prints as a placeholder.
    - The "outside the cooperative tier" clause is dropped for it.
  - The header's tier line is `# tier-threat-model: cooperative`. The base-name shape check is hygiene on a name printed to the agent, and the header makes no claim of defending against steered input. The gate therefore does not take `untrusted-input` (`docs/hooks.md` § "Threat-model tiers", the tier definitions and the classification rule, `:13-14`, `:18`).
  - M6's Known gaps gain three more items:
    - A repo whose count always reaches the cap pays up to that wait on every commit and is never gated (row 32).
    - A roster agent is barred from the ack while any other subagent is not (row 28). This rewords M6's item "A subagent can run the ack script ... the deny text tells a subagent to report to its dispatcher instead of a bar" (`.claude/plans/diff-size-prompt.md:291`) to cover non-roster subagents only, so the header carries one item on who can release the gate, not two.
    - The roster bar and the ask entry each cover one spelling of the ack command. A roster agent running `bash ~/.claude/scripts/diff-size-ack.sh`, an absolute-path form or a `cd . &&` prefix passes the bar. Whether the ask entry matches any of those spellings is documented, not observed. Under `bypassPermissions` a spelling the entry misses writes the latch with no prompt, and under auto mode the classifier decides (row 36). Design row 56 is unverified, and the live check is deferred (row 3).
  - M6's trace gap is reworded: allows leave no trace, and a deny is countable from transcripts through `denials.py`'s label while they are retained (row 33). The label is shared, so the count mixes size denies with roster-bar denies.
  - `_lib.sh`'s `_lib_diff_size_gate_state` header names its two callers by file (row 21). No other `_lib.sh` line changes.

  anchors: root, G1, G2, row8, row9, row10, row17, row19, row20, row21, row28, row30, row31, row32, row33, row36, row42

- **P2. The ack script and its ask entry (M2).** New executable `claude/.claude/scripts/diff-size-ack.sh` and the `permissions.ask` entry, as M2 specifies, with no `permissions.allow` entry. Additions:
  - It sets `set -uo pipefail`, not `set -e`, because `_lib_diff_size_gate_state` returns 1, 2 and 3 as answers. It captures the status as `rc=0; out=$(...) || rc=$?`, as that function's header directs.
  - It installs one `EXIT` trap that removes the `mktemp` file, and clears the trap after the `mv`, so an interrupt between the two strands nothing in the latch directory (`claude/.claude/rules/shell-script-conventions.md`).
  - Its success lines name the config directory the latch went under and never the latch path, so M5's "never names the latch path" holds.
  - It shape-checks the base as P1 does before printing it or writing it to the latch line (row 31). A base that fails the check prints as `the base branch` on stdout. The placeholder replaces the whole `origin/<name>` token, so the ack never prints `origin/the base branch`. The latch line then reads `<UTC ISO-8601 time> <count> -`, with `-` in the base field and no `origin/` prefix.
  - Roster agents are barred by P1's hook branch, not by this script, which has no caller identity (row 28).

  anchors: root, row11, row12, row13, row14, row28, row31

- **P3. Registration and docs (M9's PR 2 part).** As M9 specifies, with these departures:
  - The `docs/hooks.md` bullet states the forced prompt in auto mode and `bypassPermissions` as documented, not observed, and points to `docs/security-hardening.md`'s documented ask-rule list. M9's clause "unless PR 2's live check has recorded it" is dropped (rows 3, 4).
  - The bullet's allow-conditions fact says a cap kill allows only where `timeout` or `gtimeout` is on `PATH` (row 10).
  - The gate joins the `docs/hooks.md:112` dependents line with a clause saying it recognizes the four `--continue` forms and allows them unmeasured (row 18).
  - `denials.py`'s `_DENIAL_HOOK_LABELS` gains `"diff-size"` after `"plan-writer-scope"`, and `test_transcript_denials.py`'s `_BOOTSTRAP_FALLBACK_HOOKS` gains the hook's row at its end (row 15).
  - The `docs/hooks.md` tier-table row is plain `cooperative`, and its Why cell quotes the header's "not a security control" sentence, as M9 specifies (`.claude/plans/diff-size-prompt.md:351`). The Why cell claims no defense against a hostile base name beyond the shape check. The count sentence at `docs/hooks.md:59` moves by one, recounted from the table.
  - `_SELF_FILTERING_BASH_GATES` gains `require-diff-size-prompt.sh` (row 16).
  - `TestHookWrittenStateStaysOutOfGit` gains `pytest.param(".diff-size-prompt-latch.d/state-key", id="diff-size-prompt-latch")` for M9's `.gitignore` line (row 22).
  - No runbook subsection is added under "Gate deadlock recovery" (row 26). This departs from M9's third `docs/hooks.md` item. M5's deny text drops its parenthetical "(M9's runbook, step 2)" and keeps the instruction that the engineer can run the ack from this directory in their own terminal. The `docs/hooks.md` bullet states the same one-line fact.
  - The `docs/hooks.md` bullet's ack fact adds one sentence: a terminal-run ack must use the session's `CLAUDE_CONFIG_DIR`, unset included, or it writes a latch the gate never reads. It points to the existing escape of committing from a terminal outside the session (`docs/hooks.md:483`). This keeps row 26 and carries the one fact M9's dropped step 2 held (row 38).
  - The same bullet adds one sentence: running the ack tells an unreadable state from a below-threshold one, and at or over the threshold the same run writes the latch.
  - The `docs/hooks.md` bullet and the README row carry M8's two exact phrases, so PR 3's pin needs no edit to these files: `brings the branch's diff against its base to 400 or more changed lines` in `docs/hooks.md`, and `brings the branch's diff to 400 or more changed lines` in `README.md`.
  - The CHANGELOG bullet adds a sentence that a real `~/.claude/hooks` directory and a real `~/.claude/scripts` directory, each the legacy per-file layout, need `./install.sh` after pulling, as the precedent bullets at `CHANGELOG.md:317` (hooks) and `CHANGELOG.md:196` (scripts) do. Design row 21's no-install claim stays for the symlinked layout of both.
  - `test_hook_alignment.py` bumps `_GATE_HOOK_COUNT` from 33 to 34 and `_SETTINGS_GATE_HANDLER_COUNT` from 34 to 35 (`:3462`, `:3465`).
  - `select-tests.py` gains `_lib_sh_helpers.py` in `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` and `test_diff_size_ack.py` in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, and `test_select_tests.py` gains one case pinning that an edit to `_lib_sh_helpers.py` selects the ack test (rows 37, 40).
  - The settings entry sets no `timeout`, and no `REFERENCES.md` entry is added (Approach).

  anchors: row3, row4, row7, row10, row14, row15, row16, row18, row22, row26, row37, row38, row40

- **P4. Tests, one case per branch (the design's PR 2 Out-of-scope items).**
  - Both new test files:
    - Pass one explicit `CLAUDE_CONFIG_DIR` under `tmp_path` to the hook and the ack, and derive every latch-path assertion from it, as `_gate_state` does at `test_lib_diff_size.py:500-514`. A premise check shows the seeded latch path equals the path the ack writes (the sibling tests' ambient config dir would pass vacuously or write under a developer's real one).
    - Run fixture building and every hook and ack subprocess under the isolated git environment of `test_lib_diff_size.py:37-41` (`GIT_CONFIG_GLOBAL` as `os.devnull`, `GIT_CONFIG_NOSYSTEM` as `1`, and `HOME` and `XDG_CONFIG_HOME` under `tmp_path`). The fixture is added to `_lib_sh_helpers.py` and left in place in `test_lib_diff_size.py` (Out of scope), and each new file applies it to every test. Neither test tree's `conftest.py` supplies this.
    - One premise check asserts those four values in the env the hook's and the ack's own git calls received. It reads that env through `_make_git_recording_argv` (`helpers.py:2555-2576`) with an `action` that writes only those four variables to the log, by name, not from `os.environ` in the test process, which would only prove `monkeypatch` works. The action never dumps the whole environment, because the subprocess env inherits the parent's ambient variables and a failed assertion prints the log.
    - The unset-`CLAUDE_CONFIG_DIR` case below uses `monkeypatch.delenv`, because `_build_subprocess_env` never removes a key, and the same recording, with the action also writing `CLAUDE_CONFIG_DIR` by name, asserts the variable is absent or empty in the ack's and the hook's env.
    - Take the clone and staging builders (`_lines`, `_stage_file`, `_commit_staged`, `_clone_with_trunk_files`, `_clone_with_staged_lines`, the latch-path helper, the linked-worktree builder and the default-branch-arm builder below) from `_lib_sh_helpers.py`, where this diff adds them once. `test_lib_diff_size.py` keeps its own copies for now (Out of scope).
  - `test_require_diff_size_prompt.py`, using `run_hook_reason` for any reason text:
    - A non-Bash call, a Bash call with no `git`, and `git status` are allowed, as one parametrized case.
    - `git commit -m x` and `FOO=1 git commit -m x` (row 17) at 437 staged lines are denied. The reason names 437, `origin/trunk`, the threshold, `Split now`, `Proceed anyway` and `~/.claude/scripts/diff-size-ack.sh`, and not the latch path. No `.diff-size-prompt-latch.d` exists afterward.
    - 399 lines is allowed and 400 is denied, as two cases.
    - Each case first asserts that the same fixture denies, then flips one toggle and asserts allow. The toggles are:
      - a detached HEAD (status 1);
      - a present latch file (status 3);
      - a `--continue` form during a paused merge at 437 lines;
      - a `PATH` without `sed` or `tr` (status 2 of the commit predicate).
    - A payload `cwd` in the clone is denied while the process cwd is outside any repository, and a payload with no `cwd` is denied from a process cwd inside the clone.
    - On the default-branch arm (row 42), a hostile base is built by `update-ref` of `refs/remotes/origin/<name>` to HEAD and a `symbolic-ref` of `refs/remotes/origin/HEAD` to it. One builder in `_lib_sh_helpers.py` does this, and the ack test also uses it (row 31):
      - Five parameters are denied: a name with a bidi control, a name with a Unicode tag character, a printable-metacharacter name such as `a$b`, a name with a leading `-` (shape status 1) and a name with an `origin/` prefix (shape status 3). The reason contains 437, the threshold and `the base branch`, and not the raw name or `origin/the base branch`.
      - The test source builds every hostile code point with `chr()` or a code-point escape and carries no literal bidi or tag character, as `test_lib_stack_base.py` does with `chr(0x202E)`.
      - The same builder with a clean name is the control, and its reason names `origin/<name>`.
      - The writer first runs the two ref commands in a scratch repository for all five names to confirm that git accepts each on this platform. A name git rejects is dropped from the parameters and reported.
    - The bare ack command is checked against the roster key, `agent_type`, and against the command's shape (rows 28, 30, 36). A payload carrying both `agent_type` and `agent_id` is built by hand (row 41):
      - It is denied once per roster group, with `agent_type` set to `ciso-reviewer` (`_LIB_REVIEW_ONLY_AGENTS`, `_lib.sh:4441`), `plan-architect` (`_LIB_PLAN_WRITER_AGENTS`, `_lib.sh:4491`) and `code-writer` (`_lib.sh:4540-4544`), each with and without an `agent_id`.
      - One further denial, without an `agent_id`, iterates every name that `_lib_no_gate_release_agents` prints, read through `_run_lib_call` and not retyped, so a hook with a hard-coded subset fails. `plan-architect` holds no Bash tool (row 12), so its case pins roster membership only.
      - It is denied from a process cwd and a payload cwd outside any repository, and the reason says the agent reports to its dispatcher.
      - It is allowed for `agent_type: general-purpose` with an `agent_id`.
      - It is allowed for `agent_id: code-writer` with no `agent_type`, which proves the key.
      - It is allowed to the main session.
      - For a `code-writer` agent, `grep -n diff-size-ack.sh docs/hooks.md`, `ls claude/.claude/scripts/diff-size-ack.sh` and `cat` of that path are allowed.
      - For a `code-writer` agent, the path with an argument is allowed, and the path with leading whitespace and the path with trailing whitespace are each denied.
    - One enumerator over the hooks of `settings.json`'s first PreToolUse `Bash` matcher group serves the next two cases.
      - It evaluates an entry's `if` filter only in the form `Bash(<prefix> *)`, and the test fails on any other `if` shape.
      - The test asserts the enumeration contains `require-diff-size-prompt.sh`, `require-code-review.sh`, `require-worktree-for-git-writes.sh`, `enforce-marker-script-shape.sh` and `deny-invisible-commit-content.sh` by name.
      - Every enumerated hook runs with both `HOME` and `CLAUDE_CONFIG_DIR` pinned under `tmp_path`.
    - Every hook in that enumeration allows the bare ack command from the main session.
    - In a linked worktree at 437 lines, the chain payload `~/.claude/scripts/marker.sh write code-review && git commit -m x` is run before and after the ack.
      - This gate flips from deny to allow, and every other hook in the enumeration allows both times.
      - The test is named for what it proves, that the gates accept the retried chain after the ack.
      - The test narrows the design's "land the commit".
      - It does not run the chain, which needs `marker.sh` installed under the test's home.
    - `deny-invisible-commit-content.sh` denies `git add work.txt && git commit -m x` when the index holds under 400 lines and `work.txt` would take it over (design row 23).
  - `test_diff_size_ack.py`:
    - Any argument exits 2 and writes nothing.
    - At 400 lines it exits 0 and writes one line. The test splits it on whitespace into three fields, parses the first as a UTC ISO-8601 time, and compares the other two to `400` and `origin/trunk` as values.
    - A present latch exits 0 and stays unchanged.
    - For the status-0 write and the status-3 already-recorded exit, stdout and stderr contain the config directory and not the latch path (P2). The directory-at-the-latch-path exit names the path on purpose and is excluded.
    - With `CLAUDE_CONFIG_DIR` unset and `HOME` pinned under `tmp_path`, the ack writes under `$HOME/.claude/.diff-size-prompt-latch.d/` and the hook then allows the same commit. This pins the `docs/hooks.md` claim about a terminal-run ack.
    - Below the threshold exits 1 with the below-threshold line, and a detached HEAD exits 1 with the unreadable-state line. Each writes nothing, and the two are told apart by their stderr.
    - A failing `mv` on `PATH`, built with `path_with_failing_tool_shim`, exits 3 and leaves no latch and no temp file. The case does not depend on file permissions, so it needs no root skip.
    - A file where the latch directory's parent should be makes `mkdir -p` fail, and the ack exits 3 with no latch.
    - A directory at the latch path exits 3 and stays empty.
    - A failing `find` on `PATH` still leaves the latch and exits 0.
    - The sweep deletes a stale latch of another branch and keeps a fresh one and the new latch, with ages set through `os.utime`, as `test_log_reviewer_round.py:828,850` does. The stale age is well past 30 days, not at the boundary.
    - On the default-branch arm, the shared hostile-base builder from the hook's tests gives the same five names as the hook's hostile-base cases, built the same way, with any name dropped there dropped here (row 31). The ack's stdout and its latch line contain none of the raw names, and the latch line reads `<time> <count> -`. Wherever the ack's stdout names the base, it is asserted not to contain `origin/the base branch`, and the clean-name control on the same arm asserts that stdout shows `origin/<name>`.
    - With an unusable stack-key value, the ack exits 1, writes no latch, and prints stderr containing `cannot be used as the stack base`, which only `_lib_print_unusable_stack_base` prints (`_lib.sh:1446`). The ack does not claim to neutralize hostile text in that line, which is outside the cooperative tier.
    - Under a `mktemp` shim that exits 1 unless an argument ends in `XXXXXX`, and otherwise execs the real one, the ack still writes the latch and exits 0. This is the behavioral form of the template assertion. The shim is hand-rolled in the test file, because `path_with_failing_tool_shim` fails on a matching argument and cannot build the inverse.
  - `test_hook_alignment.py` gains two ask-pin cases beside `:556-575`, modeled on `test_skills.py:2931-2943`. One asserts `Bash(~/.claude/scripts/diff-size-ack.sh)` is in `permissions.ask` of the stow source `claude/.claude/settings.json`. The other asserts no `permissions.allow` entry in any file of `_TREE_SETTINGS_PATHS` (`test_hook_alignment.py:243`) contains `diff-size-ack.sh`. Their docstrings say they pin the declaration, not the runtime prompt.

  anchors: root, row15, row16, row17, row22, row28, row30, row31, row36, row37, row41, row42

## Critical files

One `code-writer` dispatch carries the whole diff (Approach). It reads M1's hook steps, M2, M5, M6 and M9, and the Out of scope's PR 2 bullets of `.claude/plans/diff-size-prompt.md`, then this plan's P1-P4. Its checks are Verification steps 2 and 3. The `settings.json` edit prompts the engineer through the `Edit(//**/.claude/settings*.json)` ask rule (`settings.json:45`).

New:
- `claude/.claude/hooks/require-diff-size-prompt.sh` (P1). Reuse the bootstrap of `require-architect-consult.sh:41-63` and `_lib_diff_size_gate_state`, `_lib_command_concludes_commit` and `_lib_emit_deny`.
- `claude/.claude/scripts/diff-size-ack.sh` (P2). Reuse `marker.sh:7-8`'s source line, `_lib_diff_size_gate_state`, and `log-reviewer-round.sh:140-144`'s sweep.
- `claude/.claude/hooks/tests/test_require_diff_size_prompt.py` (P4). Reuse `run_hook`, `run_hook_reason`, `bash_input`, `bare_remote_with_default_branch`, `isolated_git_config_env`, `reviewer_round_state_key` and `_run_git` from `helpers`, and the builders from `_lib_sh_helpers.py`.
- `claude/.claude/scripts/tests/test_diff_size_ack.py` (P4). The same reuse, plus `path_with_failing_tool_shim` for the `find` and `mv` cases. The scripts tree imports the builders as `hooks.tests._lib_sh_helpers`, as it already imports other hooks-tree modules.

Modified:
- `claude/.claude/hooks/tests/_lib_sh_helpers.py`: gains the isolated git environment fixture and the clone, staging, latch-path, linked-worktree and default-branch-arm builders that the two new test files share, copied from `test_lib_diff_size.py:37-73`, `:517-524` and `:593-597` (P4). It holds what two or more test modules share (design row 57), and its module docstring is reworded to say so, because it now names only `test_lib*.py` files.
- `claude/.claude/scripts/select-tests.py` and `claude/.claude/scripts/tests/test_select_tests.py`: the two set members and one case (P3, row 37).
- `claude/.claude/hooks/_lib.sh`: the one header line (P1).
- `claude/.claude/settings.json`: the hook entry after `require-code-review.sh` (`:283-288`) and the ask entry after the cleanup entries (`:37-44`).
- `claude/.claude/hooks/tests/test_hook_alignment.py`: the two ask-pin cases, the `_SELF_FILTERING_BASH_GATES` member and the two gate-count bumps (P3, P4).
- `claude/.claude/hooks/tests/test_record_session_config_keys.py`: one parameter.
- `claude/.claude/scripts/transcript_analysis/denials.py`: one label.
- `claude/.claude/scripts/tests/test_transcript_denials.py`: one row.
- `.gitignore`: one line after `:154`.
- `docs/hooks.md`: the tier row after `require-code-review.sh`'s (`:82`), the count sentence (`:59`), the dependents line (`:112`), and the gate bullet after `:161-174`.
- `docs/scripts.md`: one bullet after `:123`.
- `README.md`: one Hook transitions row after `:191`.
- `CHANGELOG.md`: one bullet under `## [Unreleased]`'s `### Added` (`:513`).

Session-owned: `.claude/plans/diff-size-gate.md`, this plan.

Projected diff: about 820 changed lines (additions plus deletions) across the files above, leaving out `.claude/plans/`. That is over the 400 threshold. The engineer answered "Proceed anyway" at about 550 (row 24), "Proceed at ~740 (Recommended)" (row 34) and "Proceed at ~810 (Recommended)" (row 35). The 820 is the 810 plus about 10 lines for the `select-tests.py` edge chosen separately (row 37). The breakdown:
- The two test files: about 435.
- The hook: about 140, with its header about 75 of them, mostly M6's Known gaps.
- The ack: about 85.
- `_lib_sh_helpers.py`: about 65.
- `docs/hooks.md`: about 28.
- `test_hook_alignment.py`: about 33.
- The other eleven files: about 35.

## Verification

1. Before the dispatch, the parent re-reads the hooks reference at `https://code.claude.com/docs/en/hooks` through `verify-sources`: the "Common fields" table's `timeout` row and the Timeouts section. It confirms the default exceeds about 116 seconds (row 9) and that a timed-out gate with `onFailure: "block"` blocks the call. If either fails, the plan returns to `/plan-review` before any code is written.
2. The dispatch runs `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_require_diff_size_prompt.py claude/.claude/scripts/tests/test_diff_size_ack.py claude/.claude/hooks/tests/test_hook_alignment.py claude/.claude/hooks/tests/test_record_session_config_keys.py claude/.claude/scripts/tests/test_transcript_denials.py claude/.claude/hooks/tests/test_lib_diff_size.py claude/.claude/scripts/tests/test_select_tests.py`, and it passes. This list is a quick loop only. Step 4 is the full suite, because `.gitignore` maps to no test domain (`test_select_tests.py:1043,1062-1066`). Step 4 also reaches `test_skills.py`, which `settings.json` selects.
3. The dispatch runs `scripts/venv-run.sh ruff check` on the nine Python files the diff touches and `scripts/venv-run.sh shellcheck` on the two new scripts and `_lib.sh`, and both pass.
4. After staging the diff, the parent runs `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -n 2` in the background, and it passes.
5. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` passes with the new files staged.
6. With the new files staged, `git grep -n -E '_lib_(diff_size|sum_numstat)|_LIB_DIFF_SIZE' -- claude/ claude-skills/ plugins/ scripts/ ':!*/tests/*'` matches only `_lib.sh`, the comment in `deny-private-project-refs.sh` (row 23), `require-diff-size-prompt.sh` and `diff-size-ack.sh`. In the two new files it matches only `_lib_diff_size_gate_state` and `_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES`.
7. With the diff staged and before the PR is presented, the parent measures the staged changed lines outside `.claude/plans/` and compares them to the Projected diff's 820, which is row 35's 810 plus row 37's edge. A measured count above 900 goes back to the engineer, with the measured number, instead of shipping on the earlier answer. The 900 is a ceiling the session set, not a figure the engineer chose. The test review estimated 900 to 980 from per-function line density and measured nothing.
8. After merge, not gating it: in a fresh session on a scratch branch at 400 or more changed lines, a commit is denied, the agent asks through `AskUserQuestion`, the ack prompts, and the unchanged retry lands (design rows 37, 39; row 4).

## Out of scope

- PR 3, under its own plan: M7, M8 and the plan-time CHANGELOG bullet (the design's cut list).
- The deferred live observations, a follow-up under its own plan (rows 3, 4). This departs from the design's Out of scope bullet "Its Verification records, before merge" and from its `docs/security-hardening.md` addition. The follow-up carries:
  - one observation in auto mode and one in `bypassPermissions` for each spelling design row 56 lists, the bare ack included;
  - a control run per mode with the ask entry removed;
  - one `claude -p` run of a trivial script under an ask rule, with its result added beside `docs/security-hardening.md`'s Untested list;
  - how that run loads the branch's settings, the round-5 item this plan leaves to it.

  Until the follow-up lands, the `docs/hooks.md` bullet states that behavior as documented, not observed (P3). This Out of scope bullet is the tracking record, and it ships with the PR.
- An explicit hook `timeout`, barring subagents other than the roster from the ack, and a `review-permissions/REFERENCES.md` entry, for the reasons in the Approach.
- Moving `test_lib_diff_size.py` onto the shared builders, which would remove the copies PR 1 left in that file. It is a follow-up, so this diff does not churn a merged test file.
- A deadlock-recovery runbook for the gate (row 26). A hook bug is filed as an issue.
- Follow-ups from PR 1's review:
  - Other `symbolic-ref --short` readers, and the state key, read a branch as `heads/<name>` when a tag shares its name.
  - `deny-pii-in-commits.sh` does not unset the four pathspec variables.
  - `deny-private-project-refs.sh`'s `git diff --cached` runs with no timeout.
  - Decomposing the diff-size helpers, tracked in GH-1396.
- The design's remaining Out of scope items stand (`.claude/plans/diff-size-prompt.md:426-438`).
