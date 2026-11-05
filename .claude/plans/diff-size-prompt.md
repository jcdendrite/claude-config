# Diff-size prompt (GH-1364 piece 4)

## Context

Goal: stop a branch from growing past the diff a reviewer can cover in one pass, by making the agent ask the user whether to split once the branch's cumulative diff crosses a fixed line threshold.

Why now: larger PRs take more `/code-review` rounds, and each round reads a bigger diff. Nothing in the repo measures or limits diff size. A grep of `claude/`, `claude-skills/`, `docs/`, `.github/` and `scripts/` found no existing diff-size check, hook or prompt. Pieces 1 and 2 of GH-1364 are merged, and piece 3 was dropped as already covered. Piece 4 supplies the measured size signal.

Ask: GH-1364 Plan item 4, "Diff-size prompt. When a branch's diff crosses a fixed line threshold, the agent stops and asks the user via `AskUserQuestion`: split now, or proceed anyway."

The Ask, in the issue's terms:
- The count is the cumulative diff `/ready-for-review` reviews, with every line counted except those under the repo-root `.claude/plans/`. The engineer carved that path out of the issue's "no exclusions" (see the decisions below).
- The prompt fires at two checkpoints: once per plan at plan review against the projected size, and once per branch at the first commit that reaches the threshold against the actual diff. These are two distinct prompts, and a plan-time answer does not suppress the commit-time one.
- The threshold is fixed and not configurable.

The engineer's selections this session are recorded, with their labels or typed words, in the Approach's assumption ledger, on every row tagged `[engineer-verified:`. The option descriptions behind each label are the session's proposals, not the engineer's words. This branch and plan carry PR 1 only (the Approach's cut list).

Redaction: the plan and the PR may state only the bucket bounds 150, 400 and 800 and the direction "rising with PR size". No count taken from local transcripts appears here.

## Approach

The design ships as three stacked PRs, and this branch carries PR 1 only (cut list below). PR 1's tests stay its helpers' only callers until PR 2 lands (row 44). The whole design, which PR 2 and PR 3 complete:
- A new commit gate, `require-diff-size-prompt.sh`, denies a commit that brings the branch's cumulative diff against `origin/<base>` to 400 or more changed lines, not counting files under the repository root's `.claude/plans/`.
- Its deny text has the agent ask the engineer, through `AskUserQuestion`, to split now or proceed anyway.
- On `Proceed anyway` the agent runs a new no-argument script, `~/.claude/scripts/diff-size-ack.sh`, which writes a per-branch latch the gate honors, then re-runs the denied command.
- At plan time, plan-architect ends Critical files with a `Projected diff:` line, and `/plan-review` asks the same question once per plan when that line states 400 or more.

The cut (rows 24, 42, 43), with each mechanism below naming its PR:
- **PR 1, this branch:** the threshold and count-cap constants, `_lib_sum_numstat_changed_lines` and `_lib_diff_size_measure` (M3, M4), `_lib_diff_size_latch_file` (M2), and `_lib_diff_size_gate_state`, the one read sequence the hook and the ack script share (M1). `test_lib_diff_size.py` carries the input matrix for three of the four functions, and `test_lib_reviewer_round_state.py`'s path-helper class takes the latch helper. `_gh_shim_dir` moves into `_lib_sh_helpers.py` so the new test can reuse it.
- **PR 2, stacked on PR 1:** the hook (M1, M5, M6), the ack script and its ask entry (M2), and M9's registration, docs, `.gitignore` line and the gate's CHANGELOG bullet. Its hook and script tests keep one case per branch.
- **PR 3, stacked on PR 2:** the plan-time sentences (M7), the threshold pin (M8), and a CHANGELOG bullet for the plan-time question.
- PR 2 and PR 3 each get their own plan, which cites this file's mechanisms as the design of record instead of restating them (rows 64, 65). Where a later plan departs from a mechanism here, it names the departure and governs for its PR. Neither PR edits this file's rows.

The cut departs in one place from the session's proposal behind row 42's label, besides PR 1's fixture move. The gate's CHANGELOG bullet, the `docs/scripts.md` bullet and the README row land in PR 2, not PR 3. PR 2 is the PR that changes every stow consumer's commits, and the changelog records "All notable changes" (`CHANGELOG.md:3`).

Alternatives weighed:
- **Allow with an `additionalContext` note** (`_lib_emit_allow_with_context`, `_lib.sh:461-468`). Rejected: the commit lands and the turn goes on, so nothing stops for the question the Ask says the agent "stops and asks".
- **The harness permission prompt, `permissionDecision: "ask"`** (`ask-new-dependency-disclosure.sh:164`). Rejected: it offers the engineer allow or deny of the command, not split or proceed, and the Ask names `AskUserQuestion`. As the permission on the ack script it is the chosen design (M2, row 67).
- **A `/code-review` step that measures and asks.** Set aside by the engineer's choice of a hook on commit (row 2).
- **Latching at the deny.** Rejected: when two gates deny one commit the agent sees one reason (row 11), so a latch written at the deny can be spent on a reason nobody saw.
- **An in-band retry token, `git -c <key>=proceed commit`.** Set aside by the engineer's choice of a standalone ack script (row 29).
- **A new `marker.sh` arm.** Rejected: `marker.sh` calls pass `enforce-marker-script-shape.sh`, a `cooperative, untrusted-input, irreversible` gate (`docs/hooks.md:79`), whose shape allowlist would need a new entry. A separate script stays outside that gate.
- **A PostToolUse recorder on `AskUserQuestion`.** Rejected for three costs:
  - It adds a second hook that must recognize the question by its text.
  - It would match option labels in a `tool_response` whose shape `nudge-answer-provenance.sh:7` does not rely on.
  - A label mismatch or a typed answer would leave the gate denying every commit, which brings back a deadlock-recovery entry and a kill switch.
- **No latch.** Rejected: the gate would deny every later commit on the branch, so the prompt would not fire once per branch.
- **Detecting size at edit time** (a PreToolUse deny or a PostToolUse note on Edit/Write/MultiEdit). Rejected for five reasons, from a consult (row 27):
  - A deny there shares the co-deny hiding of row 11.
  - A working-tree diff leaves out untracked files.
  - A `code-writer` subagent cannot ask.
  - A note stops nothing.
  - Pairing it with this gate makes two layers.
- **Measuring through `pr-diff-against-base.sh`**, whose base resolution calls `gh pr view`. Rejected for the network cost M4 states.
- **Refusing the ack to a subagent.** Not added. The hook receives the agent identity as `AGENT_ID` through the shared payload parse (`_lib.sh:471,545`), but only on the commit call, not on the ack's own Bash call, so barring the ack needs a hook that matches the ack's command. The nearest precedent, `enforce-marker-script-shape.sh:436-448`, bars a non-roster subagent from ledger state only and leaves marker-like state open to it. Leaving the ack open is a choice made on the `cooperative` tier, and the deny text tells a subagent to report to its dispatcher. M6 states the gap.
- **A kill-switch config key.** Not added. The Context reads the Ask as a fixed threshold that is not configurable, and the ack script already releases the gate once per branch. Adding a key would also edit the four pinned key-count claims (row 17).
- **Shipping the helpers inside PR 2**, so no helper waits for a production caller. Rejected: that makes two PRs, not the three of row 42, and moves the direct tests' input matrix into the PR that already carries the hook and the script.
- **The same read sequence written twice, in the hook and in the script, with a test that fails when they diverge.** Rejected: one function (M1) makes the two agree by construction, while a divergence test would pin only the inputs it builds.

### Assumption ledger

**Root:** A branch's cumulative diff can grow past what one review pass covers without anyone deciding it should, and nothing in the repo measures diff size or asks (Context).

**Givens:**
- G1. A hook cannot call `AskUserQuestion`. A PreToolUse hook can allow, deny, or hand the call to the harness permission prompt, so the question has to come from the agent the deny reaches. Reason: the harness's hook contract imposes it; `_lib.sh:443` and `ask-new-dependency-disclosure.sh:164` are the deny and ask envelopes this repo emits under it.

**Rows:**
1. The threshold is 400 changed lines. [engineer-verified: "400 lines (Recommended)"]
2. The implementation-time check is a hook on commit. [engineer-verified: "Hook on commit (Recommended)"]
3. At plan time, plan-architect states the estimate. [engineer-verified: "Architect states estimate (Recommended)"]
4. The question offers split and proceed only. [engineer-verified: "Split / proceed only (Recommended)"]
5. The option descriptions behind rows 1-4 are the session's proposals, not the engineer's words. This plan follows them except on one point: its latch records an approved run of the ack script, not the engineer's answer itself (M2). [unverified]
6. `review-cost-by-size` buckets merged PRs by additions plus deletions into half-open buckets with lower edges 0, 150, 400 and 800. A branch at 400 or more sits in the 400-799 bucket, so the gate fires at a count of 400 or more, not above 400. It takes each PR's additions and deletions as GitHub reports them, so its sizes include plan files. [verified: `review_cost_by_size.py:23-24`, `:134`]
7. `/ready-for-review` reviews plumbing `git diff-tree -r -p -M --ignore-submodules=none <merge-base> HEAD`, with no pathspec and the merge-base taken against `refs/remotes/origin/<base>`. The same merge-base diffed against the index is that diff as it will stand once this commit lands, within 2 lines per gitlink. The gate counts that diff minus the repository root's `.claude/plans/` (row 48). [verified: `pr-diff-against-base.sh:127`, `:155`]
8. That script resolves `<base>` as the stack key, then an open PR's base through `gh pr view`, then the default branch. The divergence hook already skips the PR read and measures through the default branch. [verified: `pr-diff-against-base.sh:90-125`; `branch-divergence-status.sh:9`]
9. `code-writer` never commits, because the parent owns git, so the commit deny normally reaches a session that can call `AskUserQuestion`. [verified: `code-writer.md:23`]
10. `_lib_command_concludes_commit` returns 0, 1 or 2 and matches `git commit` and every `--continue` form, including inside a bare-`&` piece. With `sed` or `tr` missing from `PATH` it returns 2. `_lib_git_inprogress_state` reports a paused rebase, merge, cherry-pick or revert from gitdir files. It takes the gitdir as an optional second argument and returns 2 when the gitdir does not resolve. [verified: `_lib.sh:2438-2504`, `_lib.sh:1635-1662`, `test_lib_commit_predicates.py:219-233`]
11. When two PreToolUse hooks deny the same Bash call, the agent sees one reason, not both. A probe in this session staged an unreviewed plan file and ran `git commit -a -m <msg>`. Both `deny-invisible-commit-content.sh` and `require-code-review.sh` deny that command. `deny-invisible-commit-content.sh` had denied the identical command a call earlier. The agent was shown only the code-review reason. The hooks reference documents parallel runs and `deny` precedence and is silent on combining reasons. The design does not depend on which reason is shown (M2). [verified: the probe; the hooks reference at code.claude.com/docs/en/hooks, read through a subagent]
12. The latch precedent is `log-reviewer-round.sh:140-144`: `mkdir -p`, `touch`, then a `find -mtime +30 -delete` sweep on the write path. `_lib_reviewer_round_state_key_for_branch` is the one home of the `<repo-hash>.<branch-hash>` formula for a caller that has already read the branch. `_lib_reviewer_round_state_key` reads the branch, calls it, and returns 1 on a detached HEAD. `_lib_architect_consult_latch_file` is the latch-path helper precedent, and the consult gate tests its latch with `-f`. [verified: `log-reviewer-round.sh:140-144`, `_lib.sh:4630-4637`, `_lib_reviewer_round_state_key` and `_lib_reviewer_round_state_key_for_branch` in `_lib.sh`, `require-architect-consult.sh:109`]
13. `git diff-index --cached --numstat -M --ignore-submodules=none <commit> --` prints one `<added>\t<deleted>\t<path>` row per changed path, and `-\t-` for a binary file. A rename with 3 edited lines prints `3\t3\t<old> => <new>`, and an empty diff prints nothing at exit 0. [verified: a git probe in review round 3]
14. Pinning a skill-body number to a hook literal has a precedent: `ai-instruction-and-memory-files/SKILL.md`'s CLAUDE.md line cap is pinned to `check-claude-md-length.sh`'s `limit_for` default. The `DocCountFact` driver runs `re.findall` with no flags and requires exactly one match per occurrence pattern in its file. [verified: `test_doc_counts.py:227-250`, `:546-559`, `:662-676`, `:713-716`]
15. `plan-it/SKILL.md` is 181 lines under the 200-line default cap, and `plan-review/SKILL.md` is 302 lines under its 500-line cap. [verified: `claude/.claude/hooks/check-skill-length.sh:130-141`; reads]
16. `/plan-review` Step 4 question 2 already asks the engineer one `AskUserQuestion` before any reviewer spawns, in its "Measure against the ask." paragraph. That paragraph records each answer on a ledger row quoting it, leaves off an item such a row covers, and returns Request changes for an unanswered or declined item. [verified: `plan-review/SKILL.md:89` for question 2, `:96` for the paragraph]
17. Config-key count claims are pinned in `install.sh`, `migrate-legacy-config.sh`, `docs/config-file.md` and `config-schema-audit.md`, so one new key edits all four. [verified: `test_doc_counts.py:613-651`]
18. Hook-written state directories under `claude/.claude/` are gitignored one per line. [verified: `.gitignore:148-169`]
19. The tier table gives plain `cooperative` to a gate whose header calls it "not a security control" and that allows on state failure. Its count sentence reads 18 plain `cooperative`, which matches its rows. [verified: `docs/hooks.md:59`, `:81`; row count]
20. A new gate registers with `"onFailure": "block"`, ends with a column-0 `exit 0`, and needs a `docs/hooks.md` list item. `test_hook_alignment.py` checks these, the tier header and table row, the PreToolUse wiring, and the payload-failure denies. [verified: `docs/hooks.md:178`; `test_hook_alignment.py:1-60`]
21. A new file under `claude/.claude/hooks/` or `claude/.claude/scripts/` reaches stow consumers on `git pull` without `./install.sh`, since `install.sh` links each entry of `claude/.claude/` and both directories already are entries. [verified: `README.md` § "Updating after git pull", `README.md:91`]
22. plan-architect takes its section grammar from `plan-it/SKILL.md` rather than restating it. [verified: `claude/.claude/agents/plan-architect.md` § "MODE=plan-sections", the charter this dispatch runs under]
23. `deny-invisible-commit-content.sh` denies a commit chained after `git add`, a `-a`/`--all` commit and a pathspec commit, so the index at PreToolUse time is what the commit records. [verified: `docs/hooks.md:100-109`]
24. The full design is cut across more than one PR. The question that drew this answer projected about 1,300 changed lines, a figure that counted this plan file. [engineer-verified: "Split now"]
25. The `test_doc_counts.py` threshold pin (M8) stays in the design, though the Ask does not itself call for it. [engineer-verified: "Keep doc-count pin"]
26. The `README.md` Hook transitions row (M9) stays in the design, though no test requires it and the Ask does not itself call for it. [engineer-verified: "Keep README row"]
27. Detecting size at edit time is worse than at commit time for this gate, for the reasons in the Approach's alternatives list. This is a `plan-architect` consult's reasoning, which read the hooks and `settings.json` but could not read the hooks reference. [unverified: the consult's reasoning; the untracked-file gap is from git knowledge]
28. The check runs at commit time and latches on an acknowledgment. The label says "token", but row 29's later choice makes the ack script the acknowledgment's carrier. [engineer-verified: "Ack-token latch, commit-time (Recommended)"]
29. The acknowledgment is a standalone script. [engineer-verified: "Standalone ack script (Recommended)"]
30. Row 29's option description is the dispatching session's, not the engineer's words: the deny writes nothing, and after `Proceed anyway` the agent runs the script in its own Bash call, like `marker.sh`, then retries the unchanged commit. [unverified]
31. The commit-time prompt fires whether or not the plan-time question was answered. [engineer-verified: "I think asking again at commit time makes sense actually."]
32. The deny text tells a session that cannot put the question to an engineer to stop and report. [engineer-verified: "Deny text says stop and report (Recommended)"]
33. `diff-size-ack.sh` is new code, and the only agent-run script that writes gate state today is `marker.sh`. [unverified: the dispatching session's claim; a grep of `claude/.claude/scripts/*.sh` for `latch` matched only `ci-watch.sh`, which was not read]
34. `review-permissions` asks a custom script's allow rule for three layers: exact-string allow entries, a dedicated `PreToolUse:Bash` shape hook, and the script's own argument validation. It adds: "For trivial scripts that call only absolute paths and do not exec untrusted input, layer 3 alone may be sufficient". The allow list names scripts in tilde form, and `test_hook_alignment.py` fails on a wildcard in it. [verified: `review-permissions/REFERENCES.md:84-101`, `settings.json:4-43`, `test_hook_alignment.py:685-714`]
35. `_lib_config_dir` returns an absolute `CLAUDE_CONFIG_DIR` and fails on a relative one. `marker.sh` resolves its state through it from the Bash tool, so a script there resolves the config dir a hook does when both environments carry the same `CLAUDE_CONFIG_DIR`. [verified: `_config.sh:110-120`, `test_enforce_marker_script_shape.py:1963-1969`; the matching environments are assumed, as `marker.sh` already assumes them]
36. `_lib_configured_stack_base STDERR_PREFIX REPO_ROOT` reads HEAD itself. It returns 0 with `<branch> <stack-base>`, 1 for no key or a detached HEAD, and 2 for an unusable key or a failed git read, after writing one stderr line. [verified: `_lib.sh:1469-1504`]
37. `/code-review` writes its marker standalone or chained only as `marker.sh write code-review && git commit …`. A denied Bash call runs no part of itself, so re-running it unchanged shows every sibling gate a shape it already judged. [verified: `code-review/SKILL.md:506`; the rest follows from G1's deny]
38. `advance-past-commit-stall.sh` fires on a main session whose last message asks permission to commit, push or open a PR while autonomous shipping is active. [verified: `advance-past-commit-stall.sh:3-7`]
39. A split-or-proceed question asked in prose rather than through `AskUserQuestion` can read to that hook as a commit-permission stall. [unverified: PR 2's post-merge live check checks it]
40. `/ready-for-review` step 1 sets `branch.<branch>.gh-merge-base` when the engineer says a branch is stacked, and `branch-management/SKILL.md` never names that key. [verified: `ready-for-review/SKILL.md:52`; a grep of `branch-management/SKILL.md`]
41. `git rev-parse --show-toplevel --absolute-git-dir` prints the two paths on two lines, in argument order. [unverified: PR 1's linked-worktree `MERGE_HEAD` case on `_lib_diff_size_gate_state` pins it]
42. The design ships as three stacked PRs. [engineer-verified: "Three stacked PRs (Recommended)"]
43. The PR order follows the design's reads: PR 2's hook and script call PR 1's helpers, and PR 3's pin reads PR 1's constant and PR 2's `docs/hooks.md` phrase (M8). [unverified]
44. A `_lib.sh` function referenced only from a test passes the dead-code tripwire. The tripwire counts any mention outside a comment line in the tracked shell and Python corpus, and it fails locally until the referencing file is staged. It matches only function definitions, so it never checks a variable. [verified: `test_shell_lib_function_liveness.py:3-8`, `:47-48`, `:69`]
45. `_lib.sh` already holds the branch-keyed state and latch path helpers, so it is the existing lib whose domain covers the new helpers, the first placement `.claude/rules/bash-unit-test-seams.md` prefers. [verified: `_lib.sh:4620-4637`; the rule file]
46. No rule, review skill, CI step or test asks for a CHANGELOG entry per PR, and `select-tests.py` maps `CHANGELOG.md` to no test. The file records "All notable changes". PR 1 adds functions that no hook, script or skill calls, so it adds no entry. [verified: `CHANGELOG.md:3`, `select-tests.py:628`; a grep for "changelog" across both rules directories, `.github/`, `code-review/SKILL.md`, `ready-for-review/SKILL.md` and `pr-description/REFERENCES.md`]
47. PR 1 proceeds at the projection the question showed: about 600 changed lines, about 290 of them this plan file, whose lines took it over 400. [engineer-verified: "Proceed anyway"]
48. Lines under the repository root's `.claude/plans/` do not count toward the threshold. [engineer-verified: "the lines of the plan shouldn't count toward the code"]
49. The carve-out takes the option the engineer selected. [engineer-verified: "Carve out plans (Recommended)"]
50. Row 49's option description is the dispatching session's, not the engineer's words: exclude only the repository root's `.claude/plans/` through a pathspec, and keep a limit on plan size as a separate follow-up. [unverified]
51. Two gates already scan a staged diff through a `:(top,exclude)` pathspec, and `deny-private-project-refs.sh` scans with that exclude alone. That gate notes that an inherited `GIT_LITERAL_PATHSPECS` reads the magic as a literal name and empties the diff at exit 0, and it unsets the four pathspec variables inside the command substitution. [verified: `deny-private-project-refs.sh:483`, `:616-619`; `deny-pii-in-commits.sh:424`]
52. `git diff-index` given only `':(top,exclude).claude/plans/'` counts every other path. A file moved across that boundary counts as a whole-file add or delete, because the pathspec drops the other side before rename detection pairs them. [verified: a git probe in review round 3]
53. The count call runs under a cap longer than `_lib_capped`'s 5 seconds. [engineer-verified: "Longer cap (Recommended)"]
54. `_lib_capped_for SECONDS CMD [ARGS...]` takes the cap first and requires a non-empty SECONDS. `review-pr-checkout.sh` caps its own git diff at 30 seconds, "a chosen ceiling, not a measured figure, and larger than _lib_capped's 5s default because the diff's cost grows with the PR's size". [verified: `_lib.sh:54-60`, `:129-140`; `review-pr-checkout.sh:435-439`] Whether a 30-second cap fires before the harness's own hook timeout rests on row 70. [unverified]
55. Every main-dir gate's missing-`_lib.sh` deny must carry the runbook sentence pointing to `docs/hooks.md` § "Gate deadlock recovery", except the two gates `_BARE_BOOTSTRAP_GATE_HOOKS` lists. `require-architect-consult.sh` uses that runbook-pointer form. [verified: `test_hook_alignment.py:2907-2919`, `:2978-2979`; `require-architect-consult.sh:45-61`]
56. The ask entry forces a prompt only for the spelling it matches. A spelling it does not match, such as `cd <dir> && ~/.claude/scripts/diff-size-ack.sh`, an absolute path, a `bash` wrapper or a `PATH=` prefix, gets the active mode's default handling: a prompt in default mode, the classifier in auto mode, and nothing in bypass mode (`docs/auto-mode.md:3-5`; `review-permissions/REFERENCES.md:18-21`). The repo records an ask rule's auto-mode behavior as documented (`docs/security-hardening.md:254-258`) and its `bypassPermissions` behavior as untested (`:261-263`). Whether the exact entry matches any of those spellings is not known. [unverified: PR 2's live check records it per spelling and per mode]
57. `_lib_sh_helpers.py` holds the helpers that two or more `test_lib*.py` files share, and an edit to `claude/.claude/tests/helpers.py` forces a full-suite run. [verified: `_lib_sh_helpers.py:1`; `select-tests.py:461-472`]
58. `reviewer_round_state_key` lives in `claude/.claude/tests/helpers.py`, which tests import as `from helpers import ...` through `pyproject.toml`'s `pythonpath`. It hashes `git_toplevel(repo)`, the root a hook's `rev-parse --show-toplevel` returns. [verified: `helpers.py:2208-2227`; `test_lib_reviewer_round_state.py:20-28`; `.claude/rules/test-tree-packaging.md`]
59. The sibling latch gate's runbook, `docs/hooks.md` § "Architect-consult gate denying again after a consult", gives the human's steps in order: make the state directories writable, retry, and only then the kill switch. [verified: `docs/hooks.md:501-509`]
60. The revision review round 3 read projected PR 1 at about 600 changed lines with this plan file left out, from the cases and the shared read sequence that revision added. Row 47's answer saw about 305 such lines, so it did not cover that size. The engineer was asked again for that projection. The whole design's size is not re-derived here, and PR 2's and PR 3's plans state their own. [engineer-verified: "Proceed anyway"]
61. `_lib_diff_size_gate_state` stays in PR 1, though the Ask does not itself call for it. [engineer-verified: "Keep gate-state function"]
62. The `_gh_shim_dir` move to `_lib_sh_helpers.py`, with the `test_lib_gh.py` edit, stays in PR 1, though the Ask does not itself call for it. [engineer-verified: "Keep gh-shim move"]
63. Row 53's option description is the session's proposal, not the engineer's words: run the count under a longer cap than `_lib_capped`'s 5 seconds, and list the cap kill that remains as a Known gap (M6). [unverified]
64. This file stays the design of record for PR 2 and PR 3. [engineer-verified: "the plan probably should stay as the design of record"]
65. M5-M9 keep their full text, and Critical files notes which mechanisms PR 1's writer reads. [engineer-verified: "Keep with note (Recommended)"]
66. Row 65's option description, as the question showed it, is the session's proposal, not the engineer's words: "Keeps the three-round-reviewed PR 2/PR 3 design intact as the design of record; adds one line to Critical files. The plan stays about 410 lines, but it is excluded from the count." [unverified]
67. The ack script is listed under `permissions.ask` with no `permissions.allow` entry. [engineer-verified: "List under permissions.ask (Recommended)"]
68. Row 67's option description is the session's proposal, not the engineer's words. It listed four effects:
    - The engineer's approval becomes the authorization.
    - Row 34's exemption analysis falls away.
    - The cost is one extra prompt per branch.
    - A headless session's unanswerable prompt gives the intended stop-and-report.

    For auto mode and `bypassPermissions`, the first effect is documented behavior, not observed (row 69). [unverified]
69. The plan's own addition, not shown to the engineer: a forced prompt on the bare ack in auto mode or `bypassPermissions` is documented, not observed (row 56), until PR 2's live check records it with the control run (Out of scope). The repo lists headless `-p` runs as untested (`docs/security-hardening.md:270`). The stop-and-report text in M5, not the prompt, is the headless mechanism. [unverified]
70. A hook entry with no `timeout` gets the harness's 600-second default. This is a repo paraphrase of the hooks reference, `docs/handoff-nudge.md:198`, which carries its own re-verify-by date of 2026-11-17, and `docs/hooks.md:423` (fetched 2026-10-17). `docs/hooks.md:403` says a gate that times out blocks the call. The hooks reference was not re-read for this plan. [unverified]
71. PR 1 ships at about 720 changed lines, above the about 600 row 60 answered, because the review rounds added test cases. [engineer-verified: "Proceed anyway at ~720 (Recommended)"]

**Mechanisms:**
- **M1. The gate's checks (`_lib_diff_size_gate_state` in PR 1, the hook in PR 2).**
  - New `claude/.claude/hooks/require-diff-size-prompt.sh`: line 2 `# hook-class: gate`, line 3 `# tier-threat-model: cooperative`, and `DENY_GATE_LABEL="diff-size"`.
  - It follows the runbook-pointer bootstrap form of `require-architect-consult.sh:41-63`, not the bare form (row 55). That form has four parts:
    - The `emit_deny` stub.
    - The `_lib.sh` source, whose failure deny keeps the sibling's sentence pointing to `docs/hooks.md` § "Gate deadlock recovery".
    - The `emit_deny` re-point.
    - The `_lib_parse_tool_input_or_deny` call.
  - The hook's checks run cheapest first, and every allow is `exit 0` with no output:
    1. Allow unless `TOOL_NAME` is `Bash`.
    2. Allow unless `[[ "$COMMAND" == *git* ]]`. This pure-bash test spares every other Bash call the commit parse's `sed` and `tr` forks.
    3. Allow unless `_lib_command_concludes_commit "$COMMAND"` returns 0. Status 2 allows too, under this gate's allow-on-state-failure posture.
    4. Run `_lib_diff_size_gate_state "$CWD" 2>/dev/null`, with `CWD` defaulting to `$PWD`. Allow on any nonzero status.
    5. `emit_deny` with M5's message, built from the function's second line, then the column-0 `exit 0`.
  - `_lib.sh` gains `_lib_diff_size_gate_state START_DIR` (PR 1), the one read sequence the hook and the ack script both run, so the two cannot disagree on the config dir, repository, branch, latch path or count. Its steps, in order:
    1. Resolve the config dir with `_lib_config_dir`. Return 1 on failure.
    2. Read `REPO_ROOT` and `GITDIR` from one `_lib_capped git -C "$START_DIR" rev-parse --show-toplevel --absolute-git-dir` (row 41). Return 1 on failure or an empty line.
    3. Read `BRANCH` from one `_lib_capped git -C "$REPO_ROOT" symbolic-ref -q --short HEAD`. Return 1 when it is empty.
    4. Compute the key with `_lib_reviewer_round_state_key_for_branch "$REPO_ROOT" "$BRANCH"`, then the latch path with `_lib_diff_size_latch_file` (M2). Return 1 when either fails.
    5. Return 3 when `[[ -f "$LATCH_FILE" ]]`. The latch test comes before the measurement because a latched branch is already at the threshold and would otherwise pay for a measurement on every later commit.
    6. Run `_lib_diff_size_measure "$REPO_ROOT" "$GITDIR" "$BRANCH"` (M3). Return 1 on any nonzero status.
    7. Return 2 when the count is below `_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES`.
    8. Print the latch path, then the measurement's `<count> <base>` line, and return 0.
  - Its own git reads discard their stderr, and the measurement's stderr reaches the caller.
  - Only status 0 prints, and its two lines are the only fields a caller reads. The latch path already carries the config dir, and its key hashes the repository root and the branch, so the test checks all three through it.
  - Its header lists the four statuses:
    - 0: an unlatched branch at or over the threshold.
    - 1: state that could not be read.
    - 2: a count below the threshold.
    - 3: a latched branch.
  - The header also says 1 is the only failure, and that 1 and 2 are distinct answers a caller words differently. In `_lib_git_inprogress_state` and `_lib_configured_stack_base` a 2 means unreadable state, so a caller written from that convention would misread this function.
  - The header also states the per-commit cost and the worst-case wall time, in the shape of `_lib_reviewer_round_state_value`'s count in its header comment. The cost is about 8 git processes in the common case, each under a `timeout` wrapper, plus two `sha256sum | awk` pipelines. The ceiling is roughly 100 seconds if every capped call stalled. The writer re-derives both figures from the calls it writes.

  anchors: root, row2, row10, row12, row20, row35, row41, row54, row55

- **M2. The latch, written by the ack script (the path helper in PR 1, the script and ask entry in PR 2).** The hook never writes it (Approach, latching at the deny).
  - `_lib.sh` gains `_lib_diff_size_latch_file CONFIG_DIR STATE_KEY` beside `_lib_architect_consult_latch_file`. It prints `$CONFIG_DIR/.diff-size-prompt-latch.d/$STATE_KEY`, with that helper's empty-argument contract. It takes the key and only joins paths, like both sibling path helpers (`_lib.sh:4620-4637`), because `_lib_diff_size_gate_state` already holds the branch and computes the key (M1).
  - New `claude/.claude/scripts/diff-size-ack.sh`, executable, sources `../hooks/_lib.sh` the way `marker.sh:7-8` does. It takes no arguments. Any argument exits 2 with a usage line and writes nothing, so the script never accepts a path.
  - It runs `_lib_diff_size_gate_state "$PWD"` and lets the function's stderr through. By status:
    - 0: it writes the latch, as the next three items state.
    - 1: it exits 1, saying nothing was recorded because the state could not be read here. It adds that if the gate denied this commit, the ack and the gate read different state, so the agent reports to the engineer.
    - 2: it exits 1, saying nothing was recorded because the count is below the threshold.
    - 3: it exits 0, saying the branch's acknowledgment is already recorded and the denied command can be re-run unchanged.
  - The write runs three steps:
    1. `mkdir -p` on the latch directory.
    2. Write one line, `<UTC ISO-8601 time> <count> origin/<base>`, to a file in that directory, made by `mktemp` with a template, since BSD `mktemp` needs one (`claude/.claude/rules/shell-script-conventions.md:33-34`).
    3. `mv -f` the temp file onto the latch path.

    A failure at any step removes the temp file, if one exists, and exits 3 with a line telling the agent to report it to the engineer, so a failed write leaves no file the gate honors.
  - After the move it checks `[[ -f "$latch_file" ]]`, the gate's own test. When a directory sits at the latch path, `mv -f` exits 0 and puts the temp file inside it, so this check fails. The script then removes the temp file from where the move put it and exits 3 with a line naming the latch path for the engineer to remove.
  - Once the check passes, it runs `find "$latch_dir" -maxdepth 1 -type f -mtime +30 -delete`, as `log-reviewer-round.sh:144` does, and ignores that command's status. It then exits 0, telling the agent to re-run the denied command unchanged.
  - The script measures again for two reasons. It refuses a latch the gate would never need, and its latch line records the size each `Proceed anyway` answered (M6).
  - `settings.json` `permissions.ask` gains the exact entry `"Bash(~/.claude/scripts/diff-size-ack.sh)"`, beside the `cleanup-merged-branches.sh` entries, and `permissions.allow` gets none (row 67). For the bare command the entry matches, the documentation says the harness prompt shows the engineer the command and forces a click before the release in auto mode and `bypassPermissions`. Neither is observed yet (row 69).
  - The script's argument check is the only other layer. A dedicated shape hook (layer 2 of row 34's pattern) is left out: the gate is `cooperative` and "not a security control" (M6), and the only accepted shape is the bare path.
  - A spelling the entry does not match gets the mode's default handling (row 56), so M5 names the bare path.

  anchors: root, row11, row12, row28, row29, row30, row33, row34, row35, row56, row67, row69

- **M3. The count (PR 1).** `_lib.sh` gains four more pieces beside the latch helper:
  - `_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES=400` on its own line, under a one-line comment naming `review_cost_by_size.py`'s `_SIZE_BUCKET_LOWER_EDGES` as the source of the edge. `_lib_diff_size_gate_state` compares against it (M1).
  - `_LIB_DIFF_SIZE_COUNT_CAP_SECONDS=30` on its own line, under a one-line comment calling it a chosen ceiling, not a measured figure, set above `_lib_capped`'s 5 seconds because the count's cost grows with the diff (rows 53, 54).
  - `_lib_sum_numstat_changed_lines`, a pure function that reads `--numstat` text on stdin and prints the result of `LC_ALL=C awk -F '\t' '$1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ { total += $1 + $2 } END { print total + 0 }'`. A binary file's `-\t-` row and any malformed row add zero.
  - `_lib_diff_size_measure REPO_ROOT GITDIR BRANCH`, which prints `<count> <base>` and returns 0. BRANCH must be the branch HEAD names. `_lib_configured_stack_base` reads HEAD itself (row 36). The merge-base reads HEAD. The base-equals-branch test reads BRANCH. Its one caller reads BRANCH from HEAD one call earlier (M1). It returns 1 with no stdout when any of these holds:
    - `_lib_git_inprogress_state "$REPO_ROOT" "$GITDIR"` returns 0 or 2.
    - The base does not resolve (M4) or equals `BRANCH`.
    - `_lib_capped git -C "$REPO_ROOT" merge-base "refs/remotes/origin/$BASE" HEAD` fails.
    - The count call fails, a cap kill included.

  The count call captures `_lib_capped_for "$_LIB_DIFF_SIZE_COUNT_CAP_SECONDS" git -C "$REPO_ROOT" diff-index --cached --numstat -M --ignore-submodules=none "$MERGE_BASE" -- ':(top,exclude).claude/plans/'` into a variable and checks its status before summing.
  - A pipeline would mask a failed git call, the hazard the "captured, not piped" comment in `_lib_reviewer_round_state_value` names.
  - Inside the command substitution, `unset GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS` runs first, as at `deny-private-project-refs.sh:618`, so an inherited variable cannot read the exclude as a literal name and zero the count (row 51).
  - It then runs `export GIT_NO_LAZY_FETCH=1`, which git.adoc documents as stopping the count from fetching a missing blob from a promisor remote. `git --no-lazy-fetch` is in the 2.45.0 release notes. Older git, including the 2.43 floor, ignores the variable. There only the count cap bounds the count, and only under GNU timeout (see `_lib_capped_for`'s header). `plan-push-preflight.py:170-171` sets it the same way. A test pins the export through a recording shim.
  - Plumbing `diff-index` follows `pr-diff-against-base.sh:145-146`'s reason for plumbing: porcelain diff config cannot change the result.
  - Every path counts except those under the repository root's `.claude/plans/` (rows 48, 52). The `:(top,exclude)` spelling is the one both existing exclude pathspecs use (row 51).
  - Counting the index is exact because of row 23.

  anchors: row6, row7, row10, row13, row23, row36, row44, row45, row48, row49, row51, row52, row53, row54, row70

- **M4. The base, with no network call (PR 1).** Inside `_lib_diff_size_measure`, `_lib_configured_stack_base "diff-size" "$REPO_ROOT"` decides the base:
  - Status 0: the base is its second field.
  - Status 1: the base is `_lib_default_branch_or_guess "$REPO_ROOT"`.
  - Any other status: the commit is not measured.

  The stack-base helper reads HEAD itself, so a measured commit pays two branch reads, not one (row 36). The `gh pr view` read is skipped. On every commit below the threshold it would add a network call capped at 10 seconds (`branch-divergence-status.sh:103-105`). It would change the base only for an open PR that targets a non-default branch with no stack key set. The overcount that leaves is a header Known gap (M6).
  anchors: row7, row8, row36

- **M5. The deny message (PR 2).** One `emit_deny` string, which `_lib_emit_deny` prefixes with `Blocked by diff-size gate:`. It opens with the measurement: once this commit lands, the branch's diff against `origin/<base>` holds `<count>` changed lines, additions plus deletions, not counting files under `.claude/plans/`, at or over the `<threshold>`-line threshold. It says the count is measured, not estimated. It then instructs, in substance:
  - Stop and ask the engineer through `AskUserQuestion`, in those terms, whether to split now or proceed anyway, with exactly the options `Split now` and `Proceed anyway`. The question may cite an answer the engineer gave at plan time. Do not retry before they answer.
  - On `Proceed anyway`, state the count and base in the message before the call, then run exactly `~/.claude/scripts/diff-size-ack.sh` as its own Bash call from this directory, in no other spelling. Then re-run the denied command unchanged, chain included, because no part of it ran (row 37).
  - If the same denial recurs after the ack ran, whatever its exit status, report it to the engineer instead of asking again.
  - If the ack call is denied or declined, treat it as `Split now`: stop, report, do not re-run the ack in any other spelling, do not release the gate by any other means, and do not retry the commit. The engineer can run the ack from this directory in their own terminal (M9's runbook, step 2).
  - On `Split now`, do not retry. Agree with the engineer which part of the work moves, and start that branch through the `branch-management` skill. If the engineer says the new branch stacks on this one, set `git config branch.<new-branch>.gh-merge-base <this-branch>`, the key `/ready-for-review` step 1 sets (row 40). This gate keeps denying commits here until the branch's count falls below the threshold or the engineer answers `Proceed anyway`.
  - A session that cannot put the question to an engineer, such as a headless or scheduled run, stops and reports the count and base instead of retrying.
  - A subagent reports this denial to its dispatcher instead.

  It never names the latch path. The gate reads no entrypoint variable, because the stop-and-report instruction is the whole of row 32's handling.
  anchors: row4, row9, row31, row32, row37, row40, G1

- **M6. The header (PR 2).** Three lines describe the gate:
  - It counts the cumulative diff `/ready-for-review` reviews, minus files under the repository root's `.claude/plans/`.
  - "This gate is a cost-and-quality interruption, not a security control."
  - It has no kill switch, because `diff-size-ack.sh` releases it once per branch.

  Failure direction, one item each:
  - Deny on payload failure, as every gate must.
  - Allow on every state failure, a cap kill on any git call included.
  - Deny again, writing nothing, until `diff-size-ack.sh` writes the branch's latch. A deny another gate's reason hid therefore repeats on the next attempt.

  Known gaps, one fact per item:
  - A branch based on a non-default branch with no stack key set counts against the default branch and overcounts, with or without an open PR (M4). One ack clears it.
  - A stack key naming a parent with no `origin/<parent>` ref leaves the branch unmeasured.
  - The count reads `origin/<base>` as last fetched.
  - A binary file counts zero lines.
  - A commit made while a merge, rebase, cherry-pick or revert is paused is not measured. The next ordinary commit is.
  - `git -C <dir> commit` and `cd <dir> && git commit` are measured in the payload cwd's repository, not `<dir>`.
  - The latch records an approved run of the exact bare ack command. The harness prompt shows the engineer the command, not their `AskUserQuestion` answer.
  - An ack spelling the ask entry does not match may reach the classifier or no prompt at all, so its latch records no human approval (row 56).
  - When the ack call is declined, or `dontAsk` mode denies it, the agent cannot release the gate and the engineer runs the ack in their own terminal.
  - A subagent can run the ack script. The gate is `cooperative`, so the deny text tells a subagent to report to its dispatcher instead of a bar (Approach).
  - In the documented flow the prompt comes after the `/code-review` round, because `/code-review` writes its marker in the commit chain (row 37).
  - A question asked in prose rather than through `AskUserQuestion` may be pushed past by `advance-past-commit-stall.sh` when autonomous shipping is active (row 39).
  - A headless or scheduled session stops at the deny and reports. The deny text is that mechanism; the ask prompt's headless behavior is untested (row 69).
  - A branch name reused in the same repository within 30 days inherits the old branch's latch.
  - A latch older than 30 days is swept only when some branch's ack runs, so a re-ask after 30 days is possible, not guaranteed.
  - The gate's denies and allows leave no trace. Only the ack's latch line does.
  - A count call the 30-second cap kills leaves the commit unmeasured, and the largest refactors are the likeliest to reach the cap. The next commit measures again.
  - The count runs below the diff `/ready-for-review` reviews by the lines of files under the repository root's `.claude/plans/`.
  - For the same reason it runs below the additions-plus-deletions size the 400 edge was taken from, which includes plan files.
  - Plans kept anywhere other than the repository root's `.claude/plans/` count.
  - A file moved into or out of that directory counts as a whole-file add or delete.
  - The latch is a plain file the gate honors, so a direct write releases the gate with no trace line and no measurement. The marker-shape guard (`enforce-marker-script-shape.sh:416,423`) covers `*-markers/*` and `.*-active.d/*`, not `*-latch.d`. The ack costs a prompt and a direct write costs none, so the latch is not tamper-evident, and the header and docs say so. This sits outside the cooperative tier.
  - The base ref name derives from remote or config state and appears in the deny text, so a hostile ref name reaches the agent as text. This sits outside the cooperative tier.

  anchors: row6, row8, row10, row11, row12, row19, row32, row37, row38, row39, row48, row52, row53, row56, row69

- **M7. The plan-time check (PR 3).** Two sentence edits, prescribed verbatim. Neither adds a line (row 15). No agent file changes, because plan-architect takes its section grammar from `plan-it/SKILL.md` (row 22).
  - `claude-skills/skills/plan-it/SKILL.md:135`, the Critical files item, gains at its end: "When it names at least one file, end the section with a `Projected diff:` line estimating the changed lines, additions plus deletions, across every file the diff will hold once implemented, leaving out files under `.claude/plans/`."
  - `claude-skills/skills/plan-review/SKILL.md:96`, the "Measure against the ask." paragraph under Step 4 question 2 (`:89`), gains this text at its end, after "An unanswered or declined item makes the verdict **Request changes**.":

    > When the plan's `Projected diff:` line states 400 or more changed lines, ask a separate question in that same `AskUserQuestion`, or alone when the list is empty, with exactly the options `Split now` and `Proceed anyway`. Word it "This plan projects about <N> changed lines (an estimate; threshold 400). Split into smaller PRs now, or proceed?". Ask it once per plan, since a ledger row recording either answer covers it on later rounds. Record `Split now` as the row "This plan is cut to a `Projected diff:` below 400 changed lines", tagged `[engineer-verified: "Split now"]`, and make the verdict **Request changes**. A plan whose Critical files names a file but carries no `Projected diff:` line makes the verdict **Request changes**.

  The `Split now` row is a claim, so on a later round the ordinary ledger cross-check flags a revision still at 400 or more. The question rides in the call whose timing "Once question 3 is clean" already sets.
  anchors: row3, row4, row15, row16, row22, row31, row48

- **M8. One threshold, pinned (PR 3).** `_lib.sh`'s `_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES` is the home of 400. `plan-review/SKILL.md`, the `docs/hooks.md` bullet and the README row restate it under CLAUDE.md's small-duplicated-value exception. The CHANGELOG bullets leave it out, because a changelog entry is a record that a later threshold change must not rewrite. A new `DocCountFact` in `test_doc_counts.py` pins each restatement. Its ground-truth function, modeled on `_count_claude_md_length_hook_limit`, runs `re.findall(r"^_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES=(\d+)$", text, re.MULTILINE)` over `_lib.sh` and asserts exactly one match. Each occurrence below sits on one source line and matches once in its file:
  - `claude-skills/skills/plan-review/SKILL.md`: `` `Projected diff:` line states (\d+) or more changed lines ``
  - `claude-skills/skills/plan-review/SKILL.md`: `an estimate; threshold (\d+)\)`
  - `claude-skills/skills/plan-review/SKILL.md`: `` cut to a `Projected diff:` below (\d+) changed lines ``
  - `docs/hooks.md`: `brings the branch's diff against its base to (\d+) or more changed lines`
  - `README.md`: `brings the branch's diff to (\d+) or more changed lines`

  anchors: row1, row14, row25

- **M9. Registration and docs (PR 2, except PR 3's CHANGELOG bullet).**
  - `settings.json`: one entry in the first PreToolUse `Bash` group (`settings.json:280-288`), after `require-code-review.sh`, with `"onFailure": "block"`, `"statusMessage": "Checking diff size..."` and no `if` condition, since the hook filters on tool name itself. M2 adds the ack's `permissions.ask` entry, and `permissions.allow` gets none (row 67).
  - `docs/hooks.md`: a Gate hooks bullet after `require-architect-consult.sh`'s, one fact per sentence. It states:
    - the trigger, with M8's phrase;
    - the count, as the cumulative diff `/ready-for-review` reviews minus files under the repository root's `.claude/plans/`;
    - the latch path `<config-dir>/.diff-size-prompt-latch.d/<repo-hash>.<branch-hash>`, which `diff-size-ack.sh` writes with one trace line;
    - the allow conditions;
    - that a branch already at or past the threshold when the gate is installed is asked at its next commit, in every repository;
    - that a headless or scheduled session stops at the deny and reports;
    - that the gate has no off switch, since the ack releases it once per branch;
    - that the ack is listed under `permissions.ask`, as the `cleanup-merged-branches.sh` bullet in `docs/scripts.md` states of its entry;
    - that the forced prompt in auto mode and `bypassPermissions` is documented, not observed, unless PR 2's live check has recorded it;
    - that the latch is not tamper-evident.
  - `docs/hooks.md`: the new gate named in the dependents list of the `deny-invisible-commit-content.sh` bullet under § Gate hooks, on the `git commit` and all four `--continue` forms line (`:112`). The gate counts the index at PreToolUse time, which is exact only because that gate denies a chained `git add`, a `-a` commit and a pathspec commit (row 23). The list's closing line (`:116`) says each dependent names the dependency in a one-line comment, so the new hook's header carries one pointing to that bullet.
  - `docs/hooks.md`: a `### Diff-size gate denying again after the ack` subsection under § "Gate deadlock recovery", after the architect-consult one and in its shape (row 59). Its steps are the human's to run or direct:
    0. If the ack call was declined or denied, no latch exists. Go to step 2.
    1. From a separate terminal, check that `<config-dir>/.diff-size-prompt-latch.d` is writable by your user, and restore write permission if it is not, as when the ack exited 3.
    2. From the branch's directory, in a terminal whose `CLAUDE_CONFIG_DIR` matches the session's, unset included, run `~/.claude/scripts/diff-size-ack.sh`, then tell the session to retry the commit. A terminal with a different `CLAUDE_CONFIG_DIR` writes a latch the session's gate never reads.

    The runbook gives no hand-written latch recipe. The ack is the only documented way to release the gate, and a recipe in a file the deny text points an agent to would route around the ask prompt (row 67). If the ack itself fails for a reason other than a non-writable directory, the engineer fixes the ack's cause.
  - `docs/hooks.md`: a tier-table row, `cooperative`, whose Why cell quotes M6's "not a security control" header line. The count sentence at `:59` moves from 18 to 19 plain `cooperative` after recounting the table.
  - `docs/scripts.md`: one bullet beside `marker.sh`'s (`docs/scripts.md:123`) that names `diff-size-ack.sh` and points to the `docs/hooks.md` entry. `README.md:278` sends readers to that file for every script.
  - `README.md`: a Hook transitions row whose Gates cell carries M8's phrase followed by "outside `.claude/plans/`", and whose Cleared-by cell names the engineer's `Proceed anyway` and `diff-size-ack.sh`.
  - `.gitignore`: `claude/.claude/.diff-size-prompt-latch.d/` beside `.architect-consult-latch.d/`.
  - `CHANGELOG.md`: PR 2 adds an `### Added` bullet under `## [Unreleased]` naming the gate and the ack script. It states the rollout effect: every in-flight branch already at or past the threshold is asked once at its next commit, in every repository, and a headless or scheduled session stops at the deny. PR 3 adds a bullet naming the plan-time question.
  - No `./install.sh` step (row 21).

  anchors: row12, row18, row19, row20, row21, row23, row26, row34, row59

## Critical files

This branch carries PR 1 only, as one `code-writer` dispatch. The Approach's cut list names what PR 2 and PR 3 carry, and Out of scope holds what their plans add beyond it. The writer reads M1's `_lib_diff_size_gate_state` steps and header, M2's first item, M3 and M4. The rest of M1 and M2, and all of M5-M9, are background for those functions' contracts (row 65). The dispatch's own checks are these three, and no others:
- `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_lib_diff_size.py claude/.claude/hooks/tests/test_lib_reviewer_round_state.py claude/.claude/hooks/tests/test_lib_gh.py`
- `scripts/venv-run.sh ruff check` on the four Python files below
- `scripts/venv-run.sh shellcheck claude/.claude/hooks/_lib.sh`

The writer does not stage (`code-writer.md:23`), and the dead-code tripwire reads its corpus from `git ls-files` (row 44), so the tripwire stays with the parent. Once the dispatch returns, the parent stages `test_lib_diff_size.py` and runs Verification steps 1 and 4.

- `claude/.claude/hooks/_lib.sh`: M3's two constants, `_lib_sum_numstat_changed_lines` and `_lib_diff_size_measure` with M4's base resolution inside it, M2's `_lib_diff_size_latch_file` beside `_lib_architect_consult_latch_file` (`_lib.sh:4630-4637`), and M1's `_lib_diff_size_gate_state` with its header. Reuse `_lib_config_dir`, `_lib_capped`, `_lib_capped_for`, `_lib_reviewer_round_state_key_for_branch`, `_lib_git_inprogress_state`, `_lib_configured_stack_base` and `_lib_default_branch_or_guess`.
- `claude/.claude/hooks/tests/_lib_sh_helpers.py`: gains `_gh_shim_dir`, its body moved unchanged from `test_lib_gh.py:18-40`, and the `import textwrap` that body needs. It goes here, not to `helpers.py`, because this module holds what two or more `test_lib*.py` files share, and a `helpers.py` edit would force a full-suite run (row 57).
- `claude/.claude/hooks/tests/test_lib_gh.py`: drops `_gh_shim_dir` and its now-unused `import textwrap` (`:7`). It imports `_gh_shim_dir` from `._lib_sh_helpers`, in isort order after `_LIB_SH` (`:13`).
- `claude/.claude/hooks/tests/test_lib_reviewer_round_state.py`: `TestLibBranchStatePathHelpers` (`:131-166`) takes `_lib_diff_size_latch_file` as one more case in each of its two parametrized tests.
  - The path test expects `/cfg/.diff-size-prompt-latch.d/repohash.branchhash`.
  - The empty-argument test adds the name to its function list.
  - The class docstring's first sentence, which names the gate and the recorder, widens to cover the new helper.
- `claude/.claude/hooks/tests/test_lib_diff_size.py` (new): the input matrix for the other three functions, one case per item below.
  - Conventions:
    - Each call sources `_lib.sh` through `_lib_sh_helpers._run_lib_call`.
    - The fixture builders take no env, so before building any fixture the test sets `helpers.isolated_git_config_env(home)`'s four variables with `monkeypatch.setenv`.
    - Every call passes `os.environ` with the case's own overrides merged over it, never the isolation dict alone, which would drop `PATH`.
    - Every `_lib_diff_size_gate_state` call sets an absolute `CLAUDE_CONFIG_DIR` under `tmp_path`, because a Claude session running the suite exports its own.
    - The bare `origin`'s default branch is `trunk`, so a hard-coded `main` base fails. The default-branch-guess case is the one exception and uses `main`. The guess probes only `main`, `master` and `develop`, so a `trunk` remote returns 1 by construction.
    - Deleted and added lines are textually distinct (`old-{i}`, `new-{i}`), and rename fixtures use a file of unique lines.
    - Each refusal list below is one `pytest.mark.parametrize` table over one shared builder.
    - Reuse, through `from helpers import ...` (row 58): `bare_remote_with_default_branch` with `branch="trunk"`, `push_conflicting_edit_to_origin`, `absolute_git_dir`, `git_toplevel`, `path_with_failing_tool_shim`, `build_path_without`, `_make_git_recording_argv`, `_run_git`, `isolated_git_config_env` and `reviewer_round_state_key`. From `._lib_sh_helpers`, reuse `_run_lib_call`, `_gh_shim_dir` and `_forge_state_marker`.
    - With `test_lib_reviewer_round_state.py`, the test names every new function on a non-comment line (row 44).
  - `_lib_sum_numstat_changed_lines`, from text, as one parametrized table:
    - Empty input prints 0.
    - One row prints the sum of its two columns.
    - Several rows print the sum of every row.
    - A binary `-\t-` row adds 0.
    - A rename row whose path reads `old => new` adds its two numbers.
    - A rename row whose path reads `{a => b}/c` adds its two numbers.
    - A row with a non-numeric column adds 0.
  - `_lib_diff_size_measure` counts, in real git against a bare `origin`:
    - 200 added plus 200 deleted prints `400 trunk`.
    - A branch with no changes prints `0 trunk`.
    - 300 committed lines plus 100 staged print 400 before the commit and 400 after it, and both equal the sum of `git diff-tree -r -M --numstat <merge-base> HEAD` taken after the commit.
    - One mixed fixture holds a deletion, a rename with 3 edited lines and a staged binary file. Before and after its commit it prints a hand-derived constant, which also equals that `diff-tree` sum. Without `-M` the rename would count the whole file twice, so the constant pins rename detection too.
    - A pure rename of a 300-line file counts 0.
    - One fixture holding staged lines, an unstaged edit and an untracked file counts only the staged lines.
  - `_lib_diff_size_measure` counts from the merge-base, not the base's tip: `origin/trunk` gains an unrelated commit of more than 400 lines after the branch point, through `push_conflicting_edit_to_origin` and a `git fetch origin`, and the count stays the branch's own.
  - `_lib_diff_size_measure` and the plan-file exclusion, on one fixture with a distinct line count per file: a root `.claude/plans/` file of 50 lines, `.claude/plans-old/x.md` of 3, `sub/.claude/plans/x.md` of 4 and a code file of 7. One call prints 14. A second parameter runs the same call with `GIT_LITERAL_PATHSPECS=1` in the environment and still prints 14.
  - `_lib_diff_size_measure` with a stack key naming a pushed parent with a slashed name, such as `feat/parent`:
    - It prints that parent as the base and leaves the parent's lines out.
    - The parent then gains a commit inside the test's clone: check out the parent, commit, `git push`, and check out the child again. The child's count stays the same. `push_conflicting_edit_to_origin` cannot make this commit, because it advances only the default branch.
  - `_lib_diff_size_measure` refusals, each one toggle on a fixture that prints `<count> <base>` untoggled, and each returning 1 with no stdout:
    - The stack key names a parent that was never pushed.
    - The stack key reads `origin/trunk`. The check that its one stderr line starts `diff-size: branch.feature.gh-merge-base is origin/trunk` is a standalone test that pins exactly one line, not a table row.
    - HEAD is on `trunk`, and `BRANCH` is `trunk`.
    - The `origin` remote is removed.
    - A merge is paused: `_forge_state_marker(absolute_git_dir(<clone>), "merge", <HEAD oid>)` writes a `MERGE_HEAD` marker into the gitdir.
    - `path_with_failing_tool_shim(bin_dir, "git", "diff-index")` is first on `PATH`.
    - `awk` is left off `PATH` through `build_path_without`, so the changed-line sum comes back empty. This is a standalone test, not a table row.
    - The gitdir is empty and a shim fails any `git` call whose argument is `--absolute-git-dir`, so the in-progress state is unreadable. The one call the shim hits is `git rev-parse --absolute-git-dir` inside `_lib_git_inprogress_state`. The untoggled premise runs the same call with the empty gitdir and no shim, and prints `5 trunk`. This is a standalone test, not a table row.
  - `_lib_diff_size_measure` with `origin/HEAD` removed from a remote whose default branch is `main` still prints `<count> main`, through the default-branch guess:
    - The case first asserts `refs/remotes/origin/HEAD` resolves.
    - It then removes the ref with `git remote set-head origin -d` and asserts the ref no longer resolves.
    - The call then prints `<count> main`.
  - `_lib_diff_size_measure` and the count cap, two cases:
    - A call that sources `_lib.sh` and prints `_LIB_DIFF_SIZE_COUNT_CAP_SECONDS` yields an integer above 5, `_lib_capped`'s cap. The case pins no exact value and scans no source text.
    - The call string sets the constant to 1 after sourcing. `_make_git_recording_argv`'s shim, with `REAL_GIT` set, is first on `PATH`, and its action runs `exec sleep 4` when the arguments hold `diff-index`. The call returns 1 with no stdout, and the shim's log holds a `diff-index` call, so the refusal came from the killed count and not from an earlier refusal. Without a cap the stalled call would print `0 trunk` at status 0. The case skips when neither `timeout` nor `gtimeout` is on `PATH`, because `_lib_capped_for` then runs the call uncapped. The skip uses the two-line guard at `conftest.py:310-311` and `test_deny_pii_in_commits.py:1426`. The shim must `exec`, because a forked `sleep` would hold the command substitution open past the cap (`_lib.sh:73-79`).
  - `_lib_diff_size_measure` and the lazy-fetch variable, one case, a shim-level unit over real git:
    - A shim built with `_make_git_recording_argv` logs `$GIT_NO_LAZY_FETCH` when the arguments hold `diff-index`.
    - The case sets `GIT_NO_LAZY_FETCH=0` in the ambient environment through `env_overrides`, so a logged `1` can only come from the export.
    - The call prints `<count> <base>` for a 5-line fixture, and the log is exactly `1`.
  - `_lib_diff_size_measure` and `gh`: `_gh_shim_dir`'s shim, first on `PATH`, records a call when `gh` runs in the same environment, and records none during a measured call (M4). `_gh_shim_dir` creates its directory without `exist_ok`, so the case calls it once per `tmp_path`.
  - `_lib_diff_size_gate_state`, in real git, each later case one toggle on the first case's fixture:
    - An unlatched branch at 400 returns 0. Its first line is exactly `<config-dir>/.diff-size-prompt-latch.d/<key>`, with `<key>` from `reviewer_round_state_key`, and its second is `400 trunk`. A second call, from a subdirectory of the repository, prints the same two lines.
    - Both calls of that case write nothing to stderr.
    - A branch at 399 returns 2 with no stdout.
    - A regular file at the latch path returns 3 with no stdout, even with the `diff-index` failing shim on `PATH`, so the latch test runs before the measurement.
    - Statuses 2 and 3 also write nothing to stderr.
    - A directory at the latch path reads as no latch and returns 0.
    - An empty start dir returns 1 with no stdout. The process cwd stays inside the repository, so only the empty-argument check can refuse.
    - An unusable stack-base key returns 1 with no stdout, and the measurement's `diff-size:` line is on stderr.
    - In a linked worktree it returns 0, then 1 with no stdout once `_forge_state_marker(absolute_git_dir(<worktree>), "merge", <HEAD oid>)` writes `MERGE_HEAD` into that worktree's gitdir. The first call's whole stdout is compared, latch path included.
  - `_lib_diff_size_gate_state` refusals, each one toggle on the first gate-state case's fixture, and each returning 1 with no stdout:
    - `sha256sum` is left off `PATH` through `build_path_without`, so the key does not compute.
    - `CLAUDE_CONFIG_DIR` is relative.
    - The start dir is outside any repository, and the call string's `cd "$repo" &&` puts the process cwd inside the baseline repository. With the process cwd outside any repository, a missing step-2 check would still return 1 at step 3, so the case would not pin step 2.
    - HEAD is detached.
    - The count call fails, through the `diff-index` failing shim.

Session-owned: `.claude/plans/diff-size-prompt.md`, this plan.

Projected diff: about 770 changed lines (additions plus deletions), measured from the implemented diff with this plan file left out, as M3's count leaves out the repository root's `.claude/plans/`. It is at or over the 400 threshold. `test_lib_diff_size.py` is about 590 of them, the `_lib.sh` additions about 110, the `_gh_shim_dir` move about 50, and the `test_lib_reviewer_round_state.py` cases about 12. It sits about 50 lines above the about 720 that row 71 answered, because later review rounds added test cases.

## Verification

These steps cover PR 1 only. PR 2's and PR 3's plans carry their own. The parent runs them after the `code-writer` dispatch returns.

1. Stage `test_lib_diff_size.py` first (row 44). Then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py -n 2` passes. The run includes `test_lib_diff_size.py`, `test_lib_reviewer_round_state.py`, `test_lib_gh.py` and `test_shell_lib_function_liveness.py`, which all live under the hooks test tree (`test_shell_lib_function_liveness.py:52`).
2. `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/` passes.
3. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` passes, which lints the `_lib.sh` edit.
4. `git grep -n -E '_lib_(diff_size|sum_numstat)|_LIB_DIFF_SIZE' -- claude/ claude-skills/ plugins/ scripts/` matches only `_lib.sh`, `test_lib_diff_size.py` and `test_lib_reviewer_round_state.py`, so no hook, script or skill runs the new code before PR 2 (row 46).

## Out of scope

- PR 2, a follow-up PR stacked on this branch, under its own plan (row 42). Beyond the Approach's cut list, it carries:
  - The ack's test covers M2's write order:
    - A failed write leaves no latch file and exits 3.
    - A directory at the latch path exits 3 with no file left inside it.
    - A failing sweep still exits 0.
  - It also asserts the ack's `mktemp` call carries a template, because BSD `mktemp` needs one (`claude/.claude/rules/shell-script-conventions.md:33-34`).
  - The ack's test also covers M2's no-argument rule: the ack with any argument exits 2 and writes nothing.
  - The hook's test keeps one case each for `_lib_diff_size_gate_state` statuses 1 and 2, both asserting allow, so the status numbering cannot drift silently.
  - The hook's test also asserts the deny writes no latch file or directory (M6).
  - Four cases cross the hook, the script and the other in-tree `Bash` hooks:
    - Every PreToolUse `Bash` hook in `settings.json` allows the bare ack command.
    - A payload `cwd` wins over the process cwd.
    - In a linked worktree, the deny, the ack and an unchanged retry of the `marker.sh write code-review && git commit` chain land the commit (row 37).
    - A chained `git add <files> && git commit`, where the files would take the count over the threshold and the index at hook time holds less, is denied by an in-tree `Bash` hook, so the count never reads the smaller index (row 23).
  - `test_record_session_config_keys.py:594-615`'s `TestHookWrittenStateStaysOutOfGit` gains a `.diff-size-prompt-latch.d/` parameter for M9's `.gitignore` line, since its list is not exhaustive and would miss the new directory silently.
  - A new case beside `test_hook_alignment.py:559` asserts `Bash(~/.claude/scripts/diff-size-ack.sh)` is in `permissions.ask`, and that no `permissions.allow` entry in any in-tree `settings*.json` contains `diff-size-ack.sh` in any spelling. Its docstring says it pins the declaration, not the runtime prompt. No existing test constrains Bash ask entries, and the existing wildcard pin (`test_hook_alignment.py:685-714`) does not cover this.
  - Its Verification records, before merge:
    - One observation of the prompt in auto mode and one in `bypassPermissions`, for each spelling row 56 lists, the bare ack entry included.
    - A control run per mode with the `ask` entry removed, which shows the prompt comes from the entry.
    - One `claude -p` run of a trivial script under an ask rule, added to `docs/security-hardening.md` beside its Untested list.
    - The results, in PR 2's description.
  - A default-mode observation is recorded only as confirmation that no `allow` rule matches (a command outside the allow list prompts there, `review-permissions/REFERENCES.md:80-82`), not as evidence for the entry.
  - PR 2 also adds a `review-permissions/REFERENCES.md` decision entry for the ack, or says why not.
  - PR 2's plan also settles these findings from plan-review round 5, which concern only PR 2:
    - How the pre-merge run loads the branch's settings, and which observations gate the merge.
    - The behavioral `mktemp` shim form for the template assertion.
    - The PreToolUse timeout default: re-read the hooks reference, then either set an explicit `timeout` on the entry or state the fail-closed timeout path in M6.
    - Adding `require-diff-size-prompt.sh` to `_SELF_FILTERING_BASH_GATES` (`test_hook_alignment.py:872-888`).
    - Wording the `docs/hooks.md:112` dependents entry as recognizing, not counting, the `--continue` forms.
  - Its Verification also carries a post-merge live check of the deny, the `AskUserQuestion` prompt, the ack and the retried commit chain (rows 37, 39).
  - The cross-test "every PreToolUse `Bash` hook allows the bare ack command" checks allow only. The prompting of prefixed and wrapped forms rests on the manual check above.
- PR 3, a follow-up PR stacked on PR 2, under its own plan: M7's `plan-it/SKILL.md` and `plan-review/SKILL.md` sentences, M8's `DocCountFact` in `test_doc_counts.py`, and its CHANGELOG bullet (M9). Its Verification carries the two skills' line caps (row 15).
- A gitlink case in `test_lib_diff_size.py`. A gitlink move counts 2 lines against a 400-line threshold, so row 7 states its parity within 2 lines per gitlink, and M3's `--ignore-submodules=none` stays unpinned.
- Cases for `GIT_GLOB_PATHSPECS`, `GIT_NOGLOB_PATHSPECS` and `GIT_ICASE_PATHSPECS`. For the `.claude/plans/` exclude, none changes the count except `GIT_ICASE_PATHSPECS`, which only widens the exclusion to case variants. The `GIT_LITERAL_PATHSPECS=1` parameter pins the `unset`.
- A second, higher threshold, relayed from GH-1364 as a follow-up.
- A limit on plan-file size, a follow-up separate from the higher threshold (row 50). Its cheap form is a second question in PR 3's `/plan-review` Step 4 against a pinned constant, not a `plan-shape.py` check.
- A kill-switch config key. The Approach's alternatives list gives the reason.
- Reading an open PR's base through `gh` at commit time. The overcount it would fix is a header Known gap (M4, M6).
- Measuring a commit made while a merge, rebase, cherry-pick or revert is paused.
- Suppressing the commit-time prompt after a plan-time answer (row 31).
- Reading the session's entrypoint to detect a headless run (row 32).
- Logging the gate's denies and allows (M6's trace gap).
- `plan-shape.py` checking for the `Projected diff:` line, whose grammar stays unpinned. `/plan-review` checks that the line is present (M7).
- Measuring at the start of `/code-review`, so the prompt precedes the review cost. It is a follow-up: `code-review/SKILL.md` has no line budget for a step, and a typed `/code-review` makes no Skill call.
- Renaming `_lib_reviewer_round_state_key` to a feature-neutral name now that a second feature keys on it. The rename touches `require-architect-consult.sh`, `log-reviewer-round.sh` and their tests.
