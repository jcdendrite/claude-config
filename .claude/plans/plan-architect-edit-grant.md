# Let plan-architect write its own plan sections

## Context

Goal: give `plan-architect` the ability to write its Approach, Critical files, Verification, and Out of scope sections directly into the plan file, instead of returning text for the Sonnet main session to re-emit verbatim.

Ask: "plan-architect has to write the plan anyway and if it just feeds it inline to the main session I really do not see the benefit." — accepted via label "Hook path-scope (Recommended)" for how the grant is constrained.

Why now: `plan-architect` was made read-only at creation (#731) on least-privilege grounds (`docs/design-decisions/plan-it-step5-pinned-opus-dispatch.md` §30). Its rejected-alternatives list never weighed granting `Edit`. The main session re-emitting Opus's output through its own `Edit`/`Write` call is a transcription step the design otherwise tries to avoid (§30: "rewriting Opus's output on the way to disk would reintroduce the Sonnet-quality loss").

Intended outcome: `plan-architect` holds `Edit` only (no `Write`, `Bash`, `Skill`), constrained to plan files by a hook keyed on the agent type, with `MODE=consult` never editing and the return-text path kept as the fallback.

## Approach

`plan-architect` gets `Edit` and writes its four sections into the plan file itself. Every plan now ends with the same four headings, in this order: `## Approach`, `## Critical files`, `## Verification` and `## Out of scope`. The agent owns everything from `## Approach` to the end of the file. When it cannot write, it returns that same region as text, and `/plan-it` Step 5 splices the text in place. A new gate lets a plan-writer agent `Edit` only an existing plan file directly under the repository's `.claude/plans/`. Step 5 hashes everything above `## Approach` before every dispatch, after every return and after any splice, and it stops if that text changed.

**Decision: one agent, not two (row 48).** The engineer chose one agent, with consult mode limited by its charter only (M6).
- Three of the four prescribed consult call sites pass a plan path, and so does the engineer-asked consult (row 32).
- At those three sites the plan is already committed. A consult-mode `Edit` there re-arms the plan-review gate, so the session's next non-plan write is denied (row 9).
- The gate limits such an `Edit` to repository plan files, and git recovers a committed one.
- A split would remove this residual. But it adds a second agent description to every stow consumer's sessions, which is the cost §37 rejected (row 33).

**One control per exposure.**
- The gate keeps the agent's writes inside repository plan files wherever the gate runs (M2). Row 71 gives the two windows where it does not run.
- Step 5's Context hash runs before every dispatch, after every return and after any splice. It catches any change above `## Approach`, whether the agent or the splice made it (M7). `/plan-review`'s quote-provenance check does not cover unquoted Context text (row 34).
- The consult charter keeps consult mode from editing (M6).
- The no-gate-release roster is derived from the plan-writer registry, so marker state stays guarded without a coupling tripwire (M4).

**Splice rule.** The session splices returned text only when the return carries all four headings as heading lines, in order (rows 57, 58). A heading line is a line-start `## ` line outside fenced code. The fixed headings make this a parse, not a judgment. A successful return carries no headings, so a summary in the return cannot overwrite edits that landed unless the agent violates the charter. The splice is one `Edit` from the file's `## Approach` heading to its end. Every byte above the heading stays untouched, and no whole-file `Write` is used. At first authoring, the agent's single `Edit` and its fallback return cover the same region, `## Approach` heading included. Either path therefore leaves the same file.

**Legacy plans.** Some plans committed before this change have no `## Approach` heading. Others carry heading lines in their region beyond the four (row 76). Neither kind can pass the Check, so Step 5 treats it as a failed Check before any dispatch and asks the engineer. It invents no migration (M7).

**Alternatives set aside.**
- **Status quo (return text, session re-emits).** Fails the Ask (row 1).
- **Granting `Write`.** A whole-file write reproduces Context, `Ask:` line included. The engineer agreed to `Edit` only (row 3).
- **A region rule in the gate,** denying any `Edit` that starts above `## Approach`.
  - It prevents what the Context hash detects, but it needs byte-offset matching over `old_string` in bash and a test arm for each branch.
  - Detection suffices, because Step 5 stops before `/plan-review` runs against altered Context.
  - Excluded under the trimmed design (rows 49-51).
- **Confining a plan-writer to the plan file its dispatch named.** The hook cannot see the prompt (row 8), and `/code-review`'s revision re-dispatch targets a committed plan. An edit to a committed sibling plan re-arms the plan-review gate (row 9). An edit to an untracked sibling plan is a known gap recorded in the gate header.
- **A status-line return protocol, with a tripwire test keeping its literals in step.** The return's headings and the file's state already decide the outcome (rows 17, 57). Excluded under the trimmed design (row 51).
- **A JSON return, or optional trailing sections found by heading detection.** The engineer kept markdown with fixed headings (row 57). With optional sections, the end of the agent's region would be a judgment call.
- **Further checks:** a `git status` comparison across `.claude/plans/`, a composition test of the whole hook group, and a multi-check post-pull probe. All three are excluded under the trimmed design (row 51). The gate header and the decision file record the residuals they covered.
- **One `Edit` per section at first authoring.** A failure mid-dispatch would leave a half-written file. One `Edit` over the scaffold region lands all or nothing (row 15).
- **An arm inside `deny-reviewer-tree-mutation.sh`.** It saves one hook process per call (row 41). But it puts a second invariant into a gate named for review-only agents. The engineer chose the dedicated gate (row 23).
- **A dispatch-time consult latch.** It needs per-dispatch state and a correlation key that nobody has verified exists (rows 8, 46).
- **Lighter primitives for the gate:** see M2.

**Deployment.**
- `install.sh` creates `~/.claude` before it stows. Each entry of `claude/.claude/`, including `hooks/` and `agents/`, therefore becomes one symlink into the main checkout. `git pull` then delivers the agent change, the gate and its registration together, and no re-stow is needed (G2, row 19).
- On an install where `~/.claude/hooks` already existed as a real directory at the first stow, each file is linked individually. There the pull leaves the new gate unlinked while the grant is live (row 47).
  - Marker and ledger state stay guarded (M4). Otherwise the agent's `Edit` is unconfined, forged-anchor targets included (row 71).
  - The engineer accepted this, with a post-pull probe that only reports and never blocks (rows 30, 60, M11). The probe observes only the machine it runs on.
- Neither the CHANGELOG entry nor the PR body carries an `install.sh` or re-stow instruction (rows 24, 31, 61).

**PR-body constraint.** `require-stow-reminder.sh` denies `gh pr create` until the body mentions `install.sh` or `stow`. Its deny message offers a re-stow instruction as the fix (row 25). This plan does not follow that instruction. The body carries one factual sentence scoped to the standard layout instead (M10).

**Root:** `/plan-it` Step 5 returns finished Opus plan prose, and the Sonnet session must re-emit it through its own `Edit`/`Write`. That transcription step costs a re-emission and risks drift. `plan-architect` should land its own sections. Its new write capability should reach only the repository's plan files, and a change to the Context a plan is reviewed against should stop the run.

**Givens:**
- G1 — Agent-frontmatter `hooks:` do not fire for Agent-spawned subagents. Any per-agent gate must therefore be a `settings.json` hook that filters on `.agent_type` itself. The vendor imposes this. `[verified: docs/case-studies/check-runner.md:15, CLI-verified 2026-05-14; not re-run this session]`
- G2 — Claude Code loads user-level hooks and agents through the config dir, and `settings.json` registers each hook as `~/.claude/hooks/<name>.sh`.
  - `install.sh` runs `mkdir -p "$HOME/.claude"` before stowing, "so stow links their contents entry by entry". The `claude` package targets `$HOME`, and `claude-skills` targets `~/.claude`.
  - Each entry of `claude/.claude/` therefore becomes a single directory symlink, and a new file inside an existing entry goes live on `git pull` in the main checkout. Only a new top-level entry under `claude/.claude/` needs a re-stow.
  - A per-file-link layout typically arises when a consumer had already created `~/.claude/hooks` or `~/.claude/agents` as a real directory before the first stow (row 47).
  - Claude Code's config-dir loading and GNU Stow's folding impose this. A consumer's install history is outside this plan's reach.
  - `[verified: settings.json:343-382; install.sh:46-51; stow-packages.sh:24-27; test_stow_packages.py:166-191, which folds skills under a pre-created ~/.claude with a real stow binary; CHANGELOG.md:41-42 for the per-file case]`
  - A reviewer's `ls -ld` on the engineer's machine found the folded layout.

**Assumptions:**
1. `[engineer-verified: "if it just feeds it inline to the main session I really do not see the benefit"]` Return-then-re-emit is not acceptable as the primary path. `plan-architect` writes the sections itself.
2. `[engineer-verified: "Hook path-scope (Recommended)"]` A hook scopes where `plan-architect` may write.
3. `[engineer-verified: "I agree"]` This answered the main session's Edit-only proposal: `plan-architect` gains `Edit` only, with no `Write`, `Bash` or `Skill`.
4. `[unverified]` The following are main-session proposals, not the engineer's words:
   - The option description under row 2's label: a deny keyed on `.agent_type` and limited to `.claude/plans/`, the exact-tools pin replaced by a behavioral test, consult mode left to the charter, and one added hook.
   - The separate proposal of a charter rule limiting `Edit` to `MODE=plan-sections`, with return text kept as the plan-mode fallback.
   - This plan adopts them on rows 6, 9, 10, 15 and 16. It departs from "replacing the exact-tools pin" on row 13.
5. `[verified: _lib.sh:1312-1336; require-plan-review.sh:192-206]` `_lib_is_repo_plan_file` accepts only a direct child of `<repo>/.claude/plans` ending `.md`/`.txt`, after both paths are normalized with `_lib_realpath_m`. `require-plan-review.sh` uses this predicate for its exemption. The gate reuses that check as its path rule and adds an existence check, which the exemption does not need.
6. `[verified: deny-reviewer-tree-mutation.sh:24-31,163-172; _lib.sh:513]` `.agent_type` names the subagent type in the PreToolUse payload. `_lib_parse_tool_input_or_deny` parses it fail-closed. It already keys the one existing agent-scoped write gate.
7. `[verified: claude-skills/skills/agent-review/REFERENCES.md:12]` The documented subagent `tools` field is an allowlist of tool names. No path-specifier form is documented for it.
8. `[verified: _lib.sh:513; settings.json:393-410; log-reviewer-round.sh:123-130]` The parsed `Edit` payload carries tool name, command, cwd, session id, file path, agent type and agent id. It does not carry the dispatch prompt. A PreToolUse hook on `Agent|Task` does see `.tool_input.prompt` at dispatch time, and `log-reviewer-round.sh` already reads its first line after dispatch. A dispatch-time consult latch is therefore possible. Row 46 gives its cost.
9. `[verified: require-plan-review.sh:4-6,26-33,263-269; claude-skills/skills/plan-it/SKILL.md:126-129]` An edit to a plan file makes the file active and changes the plan-set hash.
   - If the plan was committed and clean, that re-arms the plan-review gate, and the session's next non-plan write is denied. The deny text does not name who made the edit.
   - Step 7 commits the plan before implementation, so this holds at the three consult sites that pass a plan path (row 32).
   - If the plan is already active, as in a consult the engineer asks for mid-planning, a consult-mode `Edit` produces no new signal. It lands in a plan that Step 6's `/plan-review` is about to review.
10. `[verified: log-reviewer-round.sh:20-24,123-130]` The consult latch checks that the prompt's first line is exactly `MODE=plan-sections`, so the plan file path goes on line 2.
11. `[verified: docs/design-decisions/plan-review-gate-disarms-on-empty-active-plan-set.md:27-31; test_agent_roster.py:834-861]` The forged-anchor residual names "a Write/Edit-only actor".
    - Its Low rating rests on every writer without `Bash` being confined.
    - The pinning test checks `Write` only, so `plan-architect` (`Edit`, no `Bash`) would pass it unconfined.
    - Here the new gate confines it wherever the gate runs, because its path check rejects `MERGE_HEAD` and `refs/remotes/...` targets. Row 71 gives the two windows where it does not run.
    - The gate's tier carries `untrusted-input` (row 45), so citing it as the control for a route only steered input takes is consistent with `docs/hooks.md:53`.
12. `[verified: _lib.sh:3490-3533; docs/hooks.md:250-254; enforce-marker-script-shape.sh:1-20]` `enforce-marker-script-shape.sh` (`cooperative, untrusted-input, irreversible`) denies marker-state writes only from `_LIB_NO_GATE_RELEASE_AGENTS`, and `plan-architect` is not on that list today. `plan-architect` holds no `Skill` and no `Task`, so it qualifies on the roster's tool-absence ground.
13. `[verified: test_agent_roster.py:664-680,765-777]` The exact-tools pin is `plan-architect`'s only tool guard today. Roster enrollment brings in the roster-wide `Skill`/`Task`-absence tests. The pin is updated rather than replaced, so it still keeps `Write` and `Bash` out.
14. `[verified: claude-skills/skills/plan-it/SKILL.md:58]` Revision re-dispatches already pass the plan file path, and `plan-architect` already holds `Read`. The re-dispatch triggers are at lines 60-63, and "read-only and idempotent" is at line 65.
15. `[unverified]` One `Edit` call applies wholly or not at all.
    - The only corroboration is local: the Edit tool's four observed failure messages are all pre-write validation errors (`transcript-analysis.py:1845-1851`).
    - The claim matters only at first authoring, where one `Edit` replaces the scaffold region.
    - A revision is several `Edit`s. Its fallback returns all four sections in full, and the session's splice overwrites a partly applied revision.
    - The one-retry rule inherits this flag at first authoring.
16. `[unverified]` How harness plan mode treats a subagent `Edit`. Not load-bearing:
    - The harness plan file sits outside `<repo>/.claude/plans/`, so the gate denies a plan-writer `Edit` to it.
    - The charter's skip-`Edit` rule (M5) does not exclude that file on the default config dir. Plan mode's default plan path there is under `~/.claude/plans/`, directly under a `.claude/plans/` directory (row 72). The agent then meets the gate's deny on its `Edit` and on its one retry, and it returns its sections.
    - This plan records that double deny as accepted in the decision file rather than changing the prompt contract. The cost is two denied calls per plan-mode dispatch, not a failure.
    - A plan that Step 1's recovery branch writes to the repo path is written or returned like any other.
17. `[unverified]` Whether the session's next `Edit` errors on a file a child changed since the session last read it. Not load-bearing: Step 5 has the session `Read` the plan file after every dispatch.
18. `[unverified]` Whether `Edit` can create a file. Not load-bearing: the gate denies a plan-writer `Edit` whose target does not exist.
19. `[engineer-verified: "But no restow is needed!!! Git pull handles it perfectly."]` This answered the gate-home question. A `git pull` of the merged change delivers the new gate file with its `settings.json` registration and the charter edit, so the CHANGELOG entry carries no migration line (G2). The post-pull probe only reports, so nothing in this plan asks a consumer to re-stow (row 60).
20. `[verified: test_hook_alignment.py:135-142,1631-1700,1708-1776; docs/hooks.md:49-93]` A `deny-*` file must declare `hook-class: gate`, put its tier line on line 3, and appear in both the docs/hooks.md tier table and the Gate hooks bullets. `_UNTRUSTED_INPUT_FLOOR` and `_IRREVERSIBLE_FLOOR` are one-sided, so adding to them is optional, but adding keeps the documented counts accurate.
21. `[verified: claude/.claude/CLAUDE.md:178; claude-skills/skills/plan-it/SKILL.md:49,58,60-65; claude-skills/skills/code-review/SKILL.md:370; claude/.claude/agents/plan-architect.md:3,9-14,35-40; docs/hooks.md:199,250,254; _lib.sh:3464,3468; test_lib.py:6372; test_agent_roster.py:676-680,765-777; test_enforce_marker_script_shape.py:1654-1655; test_hook_alignment.py:1691,1708,1752; docs/cost-levers-considered.md:434]` These are the sites whose description of current behavior becomes false.
    - `code-review/SKILL.md:370` restates the revision re-dispatch's purpose ("to insert the returned sections") instead of deferring to `plan-it` Step 5.
    - `docs/hooks.md:254`'s "every agent in the set carries the `Write` tool" is already false for `Explore` and `Plan`, so it gets reworded, not merely extended. `test_enforce_marker_script_shape.py:1655` makes the same claim without backticks.
    - `docs/hooks.md:199` and `:250` both describe the roster as "the review-only personas plus `code-writer`".
    - `_lib.sh:3464` and `:3468` count three `_lib_list_contains` call sites, and so does `test_lib.py:6372`'s docstring ("The three call sites rely on ..."). The plan-writer predicate adds a fourth.
    - `docs/cost-levers-considered.md:434` is not edited (row 29).
22. `[verified: test_log_reviewer_round.py:581-589]` The `## MODE=plan-sections` heading in `plan-architect.md` is a test tripwire and must stay.
23. `[engineer-verified: "New dedicated gate (Recommended)"]` The gate is its own new file, `deny-plan-writer-off-plan-writes.sh`, not an arm inside `deny-reviewer-tree-mutation.sh`.
24. `[engineer-verified: "putting an unnecessary install.sh instruction in the PR description"]` The engineer named this as part of the mistake. The PR description carries no `install.sh` or re-stow instruction.
25. `[verified: require-stow-reminder.sh:29-37,96,131,221,240; require-plan-review.sh:72; test_hook_alignment.py:2477; Grep of claude/.claude/hooks for "stow does not relink a new file" → 21 files]`
    - `require-stow-reminder.sh` fires on any file added under `claude/.claude/`, nested ones included. It denies `gh pr create`, and any `gh pr edit` that changes the body, until the body, title, a body file or a `--fill` commit message contains `install.sh` or `stow` (case-insensitive substring).
    - Its header and deny message say, without qualification, that `git pull` does not link a new file nested in an existing `~/.claude` directory. That holds for a new top-level entry under `claude/.claude/` and for a per-file-link install. It does not hold for a new file inside an existing entry on the standard layout (G2). The message's example fix is "Post-merge: run `./install.sh` to pick up this change."
    - The bootstrap deny message in 21 hook files says "run ./install.sh to pick up hook files this update added (stow does not relink a new file into an existing directory until it is re-run)". The new gate must not copy it. The alignment test pins only the substring `could not source _lib.sh`.
26. `[engineer-verified: "Marker-guard enrollment"]` Selected, with the other three, when the engineer was asked which items the Ask did not call for should stay in scope. Keeps M4's roster enrollment (by derivation), its `_lib.sh` header edit, and the `test_lib.py` and `test_enforce_marker_script_shape.py` cases.
27. `[engineer-verified: "Roster confinement tests"]` Selected in the same answer. Keeps the roster-wide confinement test (M8). The excluded registry-member test (row 51) would have asserted that each registry member's agent file declares `Edit` and no `Bash`. It is not added. The `test_lib.py` predicate test, which checks `_lib_is_plan_writer_agent` against each registry member, is a different test and stays.
28. `[engineer-verified: "Supersession notes"]` Selected in the same answer. Keeps the partial-supersession lines on existing design-decision records. A third line, on §37, falls under the same label, and `/plan-review` may confirm it.
29. `[engineer-verified: "Stale-claim edits"]` Selected in the same answer. Keeps the `claude/.claude/CLAUDE.md:178` and `code-review/SKILL.md:370` edits and the further stale-claim sites in row 21. `docs/cost-levers-considered.md:434` is deliberately not edited, even though the label covers it on its face. That exclusion comes from the trimmed design (row 51), not from the engineer, so it goes to the engineer (Out of scope).
30. `[engineer-verified: "Accept, record, add probe (Recommended)"]` This answered the unfolded-install question. It keeps the dedicated gate and the no-re-stow stance, records the unfolded layout as an accepted residual, and adds a post-pull probe. Row 60 sets how the probe behaves.
31. `[unverified]` These items are the main session's description of row 30's option. They are its proposal, not the engineer's words:
    - Record the unfolded install in the decision file, with the fail-open direction stated.
    - Add model-free post-pull checks (`test -x` on the installed hook, a synthetic deny payload piped to it, `jq` over `settings.json`) with an explicit revert or abort rule.
    - Put no install or re-stow text in the CHANGELOG or PR body.
    - This plan adopts the first and third items (M9, M10). The checks became one `test -e` that reports and never blocks (rows 51, 60, M11).
    - It reads "no install or re-stow text" as "no instruction", because `require-stow-reminder.sh` requires the body to mention `install.sh` or `stow` (row 25).
32. `[verified: claude-skills/skills/code-review/SKILL.md:315,364,378; claude-skills/skills/ready-for-review/SKILL.md:86; claude/.claude/CLAUDE.md:178; claude-skills/skills/plan-it/SKILL.md:126-129]` Four prescribed consult call sites exist.
    - Three pass a plan path: `/code-review`'s new-primitive route (:364), its contradiction route by reference to :364 (:378), and `/ready-for-review`'s Cap consult (:86). The Cap consult fires on a dirty pass once two or more records are newer than the newest clean one.
    - The round-cap consult (:315) passes none.
    - The engineer-asked consult is told to name the plan file (`CLAUDE.md:178`).
    - For a plan made by `/plan-it`, all three plan-path sites run after Step 7 committed it.
33. `[verified: docs/design-decisions/plan-architect-consult-mode.md:5,9]` §37 widened `plan-architect` instead of adding a second agent, because every agent description loads into every session's base context. Its falsification metric reads Opus spend per agent type from `subagent-mix`.
34. `[verified: claude-skills/skills/plan-review/SKILL.md:96,114]` `/plan-review` sizes the plan against the Context's `Ask:` line. Its quote-provenance check covers added or changed quotes on the `Ask:` line and in `[engineer-verified]` rows. It does not cover unquoted Context text.
35. `[verified: claude/.claude/scripts/tests/test_transcript_denials.py:291-344,601-720; claude/.claude/scripts/transcript_analysis/denials.py:128-163; require-plan-review.sh:54-63]` A gate built on `require-plan-review.sh`'s bootstrap declares `DENY_GATE_LABEL`.
    - `test_bootstrap_fallback_hooks_matches_every_hook_declaring_deny_gate_label` then requires an appended `_BOOTSTRAP_FALLBACK_HOOKS` row. The first 24 rows are pinned unedited.
    - Clause (b) requires the label in `_DENIAL_HOOK_LABELS`.
    - Each deny literal may carry at most one cause marker and no hand-written "Blocked by" text.
36. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:493-574]` `test_gate_hook_registered_in_pretooluse_matcher` proves only that a gate is wired into at least one matcher. `_pretooluse_matcher_groups_for` and `_matchers_spanning_edit_write_multiedit` already exist, and line 574 holds a single-hook pin of the same shape.
37. `[verified: _lib.sh:164-215]` `_lib_realpath_m` calls `realpath -m`, then `grealpath -m`, then a loop that calls `realpath` on the nearest existing ancestor. Without `-s`, GNU realpath resolves symlinks in existing components. A symlink under `.claude/plans/` that points elsewhere therefore resolves outside the directory and is denied. The real residuals are these:
    - The target can be swapped between this hook's check and the tool's write.
    - A hard link that already exists passes the check.
    - A false deny occurs when `.claude/plans` itself is a symlink.
38. `[verified: _lib.sh:3513-3520; test_lib.py:1468-1477]` `_LIB_NO_GATE_RELEASE_AGENTS` is derived from `_LIB_REVIEW_ONLY_AGENTS` plus `code-writer`, and a test pins the subset by derivation.
39. `[verified: test_enforce_marker_script_shape.py:748-752,1651-1689]` Its end-to-end roster cases use four representative members, not `plan-architect`, and build `Write` payloads. The `TestGateReleaseAuthorityFileWrites` docstring says every roster agent carries `Write`.
40. `[verified: claude/.claude/tests/helpers.py:2213-2230; test_log_reviewer_round.py:562-578]` `CONSULT_CLASSIFICATION_TABLE` feeds both the bash latch test and the Python classifier test. Any text after `MODE=plan-sections` on line 1 makes the dispatch classify as a consult, which writes the per-branch consult latch.
41. `[verified: .claude/plans/hook-bootstrap-cost.md:9, a plan tracked in this worktree; claude/.claude/settings.json:343-382]` The `Edit|Write|MultiEdit` group holds seven hooks, none with an `if`.
    - Bootstrap plus early exit measured 17-19 ms per sibling hook process.
    - In that measurement, twelve Bash hooks summed about 372 ms of CPU against about 50 ms of wall-clock, so the hooks in a group run in parallel.
    - The new gate adds one such process to every call in every session.
42. `[verified: claude-skills/skills/agent-review/REFERENCES.md:15; docs/case-studies/check-runner.md:11-13; docs/cost-levers-considered.md:433]` `maxTurns` is "Maximum number of agentic turns before the subagent stops."
    - `check-runner` set it to 20, about twice its expected turn count, after a run whose `Write` had no path scope.
    - `maxTurns` truncates a dispatch rather than pricing it.
    - This plan found no measured turn count for `plan-architect`.
43. `[unverified]` Whether a parent session in `auto`, `acceptEdits` or `bypassPermissions` mode overrides a subagent's `permissionMode` frontmatter. The field and its values are documented (`agent-review/REFERENCES.md:16`), and `dontAsk` denies whatever would prompt (`docs/security-hardening.md:257`).
44. `[unverified]` Whether a session started before the pull can dispatch the new `plan-architect` definition while still running the hook registrations it loaded at start. If it can, the grant runs ungated in that session until restart. M4 still holds there, because its roster lives in `_lib.sh`, which each hook process sources fresh.
45. `[verified: docs/hooks.md:49,53,59,61-93, line 59 re-read and the tier table recounted row by row this session; test_hook_alignment.py:1708-1776]` Only a tier that carries `untrusted-input` may be cited as a control against steered input.
    - `deny-reviewer-tree-mutation.sh` sits at `cooperative, irreversible`, yet the forged-anchor record cites it the same way.
    - Both floors are one-sided.
    - Today's counts: 17 plain `cooperative`, 7 `cooperative, irreversible` and 7 `cooperative, untrusted-input, irreversible`. That is 14 `irreversible` gates and 31 gates in total. With the new gate: 17, 7 and 8, so 15 `irreversible` gates and 32 in total.
46. `[unverified]` Whether the `Agent` PreToolUse payload and the child's later `Edit` payload carry a shared key that ties a dispatch to its child's tool calls.
47. `[verified: CHANGELOG.md:41-42; install.sh:46-51]` If `~/.claude/hooks` already existed as a real directory at install time, stow links each file individually. A pull then leaves a new hook file unlinked while `settings.json` already names it. The repo's own note expects a hook error on each call and marks that "not verified against the harness".
48. `[engineer-verified: "One agent (Recommended)"]` Selected when asked whether `plan-architect` stays one agent or splits into a read-only consult agent and an `Edit`-holding plan-sections agent. One agent stands, and the consult residual is accepted as the Approach states it.
49. `[engineer-verified: "Ask the architect which are really needed and get back to me. These seem over engineered"]` The engineer asked which parts of the design are needed. The account that the remark addressed the region rule, the status-line protocol and the extra docs is the main session's.
50. `[engineer-verified: "Go ahead"]` This answered the main session's "Should I go ahead?", asked after it relayed the consult's verdict. It covers only adopting the trimmed design as the main session described it (row 51).
51. `[unverified]` The trimmed design is the main session's description of the consult's conclusions, not the engineer's words.
    - Gate: one fail-closed rule. Allow an `Edit` only to an existing file that `_lib_is_repo_plan_file` accepts after `_lib_realpath_m`, and deny every other `Edit` and every `Write`/`MultiEdit`.
    - Step 5: scaffold `## Approach` plus one placeholder line, and put the plan path on prompt line 2. Hash the file through `## Approach` before and after the dispatch, and stop on a mismatch. After the dispatch, `Read` the plan, then finish, insert the returned sections, or re-dispatch.
    - Error handling: re-`Read` and retry once, then return the sections as text.
    - Kept: the registry, the roster derivation, the marker-gate test, the exact-tools pin update, the `Edit`-without-`Bash` confinement test, the wiring pin and the denial-label registration. Also kept: the three supersession lines (§37 as one sentence), the `CLAUDE.md:178` and `code-review/SKILL.md:370` edits, the PR-body sentence, and a decision file whose known gaps live in the gate header.
    - Excluded: the region rule; the status-line protocol and its tripwire test; the `git status` comparison; a bound on failed-run re-dispatches; the composition, registry-member and disjointness tests; `CONSULT_CLASSIFICATION_TABLE` rows; `GIT_DIR` and `PATH`-stripped test cases; a `docs/cost-levers-considered.md` edit; a revert-PR branch. Post-pull checks beyond one `test -e` are also excluded.
    - This plan adopts that design, with two later replacements. The fixed-headings splice the engineer selected later replaces its insert rule. The four-heading scaffold of rows 57-58 replaces its one-placeholder scaffold. Its single `test -e` reports rather than aborts (row 60).
52. `[verified: docs/worktree-bash-guard.md:21-27; _lib.sh:834,872; install.sh:12]` The Context-hash command, `sed -n '1,/^## Approach$/p' "<plan path>" | sha256sum`, carries no `$(...)`, no assignment, no `$CLAUDE_CONFIG_DIR`, no `$PPID`, no variable-built path and no `git` word. It therefore matches none of the guard's five documented trigger shapes.
    - `sha256sum` is an `install.sh` prerequisite and already a hard dependency of the plan-review gate.
    - The command is one statement, so the script-first convention for multi-step recipes does not apply.
53. `[unverified]` Whether the live guard admits that command. The guard's refusals do not reproduce on demand (`docs/worktree-bash-guard.md:116-130`), so reading the command text cannot settle it.
    - Step 5 treats a refused command like a mismatch, which fails closed.
    - Verification 10 observes the first real run.
    - If the guard refuses the command, the fix is the repo's script-first convention (`docs/worktree-bash-guard.md:85-114`).
54. `[verified: claude/.claude/CLAUDE.md:178; claude-skills/skills/plan-it/SKILL.md:53-54]` `plan-architect` reads files the dispatching session names, `agent-reviews/` findings files included, and a plan-sections prompt carries Step 3 findings and Step 4 answers. Its input therefore includes text the session did not author.
55. `[verified: claude/.claude/settings.json:343-382; docs/worktree-bash-guard.md:107-110]` `settings.json` names every hook in the group by the literal path `~/.claude/hooks/<name>.sh`. That path names the same file under every config dir. One `test -e` on it therefore tells whether the gate is reachable.
56. `[verified: docs/cost-levers-considered.md:430-434]` That table records a verdict on each lever from a vendor guide. Line 434's verdict ("Already implemented, under different names") does not rest on `plan-architect` being read-only.
57. `[engineer-verified: "Adopt fixed headings (Recommended)"]` The engineer selected this after asking whether the return format still held with optional headings. Every plan carries all four section headings, in order, ending the file.
58. `[unverified]` These items are the main session's description of row 57's option, drawn from an outside review the engineer requested. They are not the engineer's words:
    - Keep markdown, with no JSON.
    - Always write the four headings. `## Out of scope` reads `None observed.` when empty, and the heading is never omitted.
    - Delete `plan-it/SKILL.md:116`'s Effort-section clause, or state that Effort material sits above `## Approach`.
    - The agent's region runs from `## Approach` to the end of the file. `plan-architect.md`'s grammar names the four headings, always, in order, ending the file.
    - The scaffold writes all four headings, with `(pending: plan-architect)` under `## Approach`.
    - When the return carries all four headings, the fallback splice replaces `## Approach` to the end of the file with it verbatim. Any other return falls under the existing grammar re-dispatch.
    - The return-format and ledger-grammar question goes to Out of scope as a follow-up. Older committed plans without the headings stay as records.
    - This plan adopts all of it except the Effort deletion. It keeps the Effort clause and places Effort material above `## Approach` (row 70). `plan-architect.md` names the four headings by citing `plan-it/SKILL.md` (M5).
59. `[verified: Grep of .claude/plans for headings containing "Effort" → 4 hits in 3 files (two code comments, a drafted-text heading and a model-and-effort-routing heading), none an effort-estimate section; claude-skills/skills/plan-review/SKILL.md:162]` No committed plan carries an Effort section. `/plan-review`'s B15 still checks such a section if a plan adds one. Placing an Effort section above `## Approach` therefore moves no committed plan's content.
60. `[engineer-verified: "Report only (Recommended)"]` This answered how the post-pull probe behaves. The probe reports and blocks nothing.
61. `[unverified]` These items are the main session's description of row 60's option, not the engineer's words:
    - The post-pull `test -e` prints whether the installed gate file exists and says why a hook error would appear.
    - It never blocks a dispatch, and there is no abort rule.
    - The unfolded install stays a recorded residual, with no install or re-stow text for consumers.
    - This plan adopts it as M11, with one change. The report names the ungated grant instead of asserting the hook error, which the repo has not verified (row 47).
62. `[verified: reproduced by two /plan-review reviewers this session]` On an unreadable path, `sed -n '1,/^## Approach$/p' "<path>" | sha256sum` prints sed's error on stderr, then the empty-input digest, which starts `e3b0c442`, and exits 0. Two matching digests therefore do not prove that anything was hashed. So Step 5 also stops on a digest starting `e3b0c442` and on any output besides the digest line (M7).
63. `[verified: _lib.sh:2013-2019; check-claude-md-length.sh:55,127]` The CLAUDE.md length gate denies a staged file only when its new size exceeds both the limit and its old size. The config-surface review measured `claude/.claude/CLAUDE.md` at 31,378 bytes, already over the 25,600-byte limit. A shorter file therefore cannot trip the gate, and the edit claims nothing more.
64. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:898-942,1803-1862]` `test_self_filtering_bash_gate_has_no_if_matcher` checks every PreToolUse entry of each hook in its set, whatever the matcher. An `Edit`-group gate therefore fits the set despite the set's name. `test_tier_rationale_hook_sentence_pinned` substring-matches the raw header text, so a pinned sentence must sit on one comment line.
65. `[verified: claude/.claude/hooks/tests/test_agent_roster.py:695-710,834-861]` `_declared_tools(name)` reads only `AGENTS_DIR/<name>.md` and asserts a non-empty `tools:` field. The `Write` confinement test iterates `TestAgentFrontmatter._AGENT_AND_PLUGIN_FILES`, plugin agents included, and parses `tools` inline with an empty fallback.
66. `[verified: claude-skills/skills/tests/test_skills.py:5399-5415,6318-6336]` `test_skills.py` already pins `plan-it/SKILL.md` text by substring. One test with one assertion can therefore pin the hash command and its stop literal there.
67. `[verified: claude/.claude/hooks/require-plan-review.sh:95; deny-reviewer-tree-mutation.sh:328-332]` `require-plan-review.sh` resolves the repo root with `_lib_capped git -C "$CWD" rev-parse --show-toplevel`. `deny-reviewer-tree-mutation.sh` runs its `git check-ignore` in a subshell that unsets `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE`.
68. `[verified: claude/.claude/hooks/enforce-marker-script-shape.sh:83-104; _lib.sh:3455-3470]` The sibling marker gate's header records its dependency on the harness's identity fields: a payload that lacks the field reads as the main session. A payload without `.agent_type` gives the new gate an empty agent type, which matches no registry entry, so the gate exits 0. Its header records this gap.
69. `[verified: ask-review-permissions.sh:1-45, as read by the platform reviewer this session]` That hook asks on any `Edit`/`Write`/`MultiEdit` to a `.claude/settings*.json` path, `claude/.claude/settings.json` included. The main session makes the registration edit itself, so no subagent meets the ask.
70. `[unverified]` The Effort clause is a main-session default for the engineer to confirm. It is not an engineer decision.
    - The engineer selected only the label "Adopt fixed headings (Recommended)" (row 57). That label chose neither branch of row 58's Effort item.
    - This plan takes the keep branch. `plan-it/SKILL.md:116`'s optional-Effort rule stays, amended so that an Effort section, if present, sits above `## Approach`.
    - `docs/design-decisions/effort-estimated-by-review-surface.md:7,11` describes that rule as live in `plan-it` Step 5, so keeping it leaves the record accurate and adds no file.
    - If the engineer picks deletion instead, that decision file needs a supersession line and an entry in Critical files.
71. `[verified: enforce-marker-script-shape.sh:440-456; require-plan-review.sh:206-208; docs/hooks.md:20; as read by the CISO reviewer this session]` The gate does not run in two windows: where its file is not linked (row 47), and in a session whose hook registrations predate it (row 44). In both windows:
    - Only the charter confines `plan-architect`'s `Edit`. Its reach is the whole tree, `MERGE_HEAD` and `refs/remotes/...` included.
    - `_agent_barred_from_matched_state` still bars roster members from marker and ledger paths, and `ask-review-permissions.sh` still asks on `settings*.json`.
    - `require-plan-review.sh` exempts targets outside the repository root. In a linked worktree both forged-anchor targets sit outside it, so that hook does not backstop the route.
    - `docs/hooks.md:20` makes a gate's own header the closed set of its accepted debt, so both windows go in the gate header.
72. `[verified: install.sh:91-93]` `install.sh` names `plans/` under the config dir as "plan-mode's default plan path". On the default config dir that path is `~/.claude/plans/<name>.md`, directly under a `.claude/plans/` directory.
73. `[verified: claude/.claude/tests/helpers.py:154-196,642-646; _lib.sh:406-432, as read by the staff-sdet reviewer this session]` `run_hook` maps an empty stdout with any exit other than 2 to allow, and otherwise reads the JSON decision.
    - A normal gate deny exits 0 with a JSON envelope on stdout and nothing on stderr. So "exit 0 and empty stderr" cannot tell an allow from a deny, but empty stdout can.
    - `read_input` takes no `agent_type` or `cwd`.
74. `[verified: claude-skills/skills/tests/test_skills.py:83-88]` `test_skills.py` reads agent files through `_agent_body`. The one planned test can therefore pin `plan-architect.md` text without touching another test file.
75. `[verified: docs/design-decisions/plan-it-step5-pinned-opus-dispatch.md:20,24]` §30 accepts two residual risks. The second says "a committed plan file can carry an entire section authored by a different agent, with no editorial pass by the session that reviews and commits it." The agent's direct write keeps that risk and changes its mechanism. The fallback path still inserts the text verbatim.
76. `[verified: .claude/plans/add-shellcheck-lint.md:31,282; .claude/plans/claude-md-agent-core.md:68,103-125; the count of committed plans with no Approach line is the staff-backend reviewer's, made this session]` Committed plans exist whose region below `## Approach` carries other `## ` lines. One has a `## Testing` heading line, and another has `## ` lines inside a fenced block. Some committed plans have no `## Approach` line at all (the reviewer counted nine). A revision of the first kind cannot pass the Check without a restructure the charter does not authorize.

**Mechanisms:**
- **M1 — `tools: Read, Grep, Glob, Edit`** in `plan-architect.md`. anchors: root, row1, row3.
- **M2 — `deny-plan-writer-off-plan-writes.sh`,** a dedicated PreToolUse gate in the `Edit|Write|MultiEdit` group, registered with no `if` by the main session (row 69).
  - It parses the payload first. An unparseable payload denies every caller, because the parse runs before the agent type is known.
  - It then exits 0 for any tool outside the group and for any caller outside the plan-writer registry. A payload without `.agent_type` counts as outside the registry (row 68).
  - For a plan-writer, it denies `Write` and `MultiEdit` outright.
  - It allows an `Edit` only when the target resolves through `_lib_realpath_m` to an existing file that `_lib_is_repo_plan_file` accepts for the repository root resolved from `.cwd`.
  - It denies every other `Edit`. It also denies an empty or relative path, an empty `.cwd`, and a failed `git` or realpath call.
  - Its header lists the two windows in which it does not run (row 71).
  - Tier: `cooperative, untrusted-input, irreversible`.
    - Why `untrusted-input`: the forged-anchor record will cite this gate as what confines `plan-architect` against hand-written `MERGE_HEAD` and `refs/remotes/...` targets. No cooperative agent takes that route, and only an `untrusted-input` tier may carry such a citation (rows 11, 45). The agent's input also includes text the session did not author (row 54).
    - Why `irreversible`: a bypass edits the dispatching session's uncommitted tree, which `docs/hooks.md:45` places in that tier. That is the same consequence class as `deny-reviewer-tree-mutation.sh`.
  - Lighter primitives, each set aside:
    - Agent-frontmatter `hooks:` never fire for a dispatched subagent (G1).
    - A path specifier in `tools:` is not documented for subagents (row 7).
    - `permissionMode: dontAsk` in the agent's frontmatter, plus an allow rule for the plans directory. The allow rule needs a wildcard, which the global `CLAUDE.md` bars from `permissions.allow`. It would also apply to every session, not to one agent. Whether the parent session's mode overrides the agent's is unverified (row 43).
    - A `permissions.deny` `Edit(...)` rule cannot key on agent identity, so it would bind the main session too.
    - An arm inside `deny-reviewer-tree-mutation.sh` is set aside on cohesion, and the engineer chose the dedicated gate (row 23).
    - A constraint enforced only by the charter fails row 2.
  - anchors: row2, row5, row6, row11, row23, row37, row45, row54, row67, row68, row69, row71.
- **M3 — `_LIB_PLAN_WRITER_AGENTS=(plan-architect)`** with `_lib_plan_writer_agents` and `_lib_is_plan_writer_agent`, in `_lib.sh` beside `_LIB_REVIEW_ONLY_AGENTS`. It is a closed enumeration. The gate reads it, so the gate embeds no agent-name literal. anchors: row6, row11.
- **M4 — Derive `_LIB_NO_GATE_RELEASE_AGENTS`** as the review-only roster plus the plan-writer registry plus `code-writer`. Membership in the plan-writer registry then implies roster membership by construction, and marker state stays under the `untrusted-input`-tier marker gate. anchors: row12, row13, row38.
- **M5 — Charter write rule,** in `plan-architect.md`'s `## MODE=plan-sections`.
  - Line 2 of the prompt is the plan file's absolute path. The agent `Read`s the file before editing.
  - If that path is not directly under a `.claude/plans/` directory, the agent makes no `Edit` and returns its sections. This rule does not exclude plan mode's default plan path (row 16).
  - The agent's region runs from `## Approach` to the end of the file. It never edits above that heading. The four headings are the region's only heading lines, in order, ending the file, and `## Out of scope` reads `None observed.` when empty.
  - First authoring: if the first non-blank line under `## Approach` is `(pending: plan-architect)`, one `Edit` replaces the whole scaffold region, from `## Approach` to the end of the file, with the four sections in full.
  - Revision: targeted `Edit`s inside the region. A heading missing from an older plan is added.
  - If an `Edit` fails, the agent re-`Read`s the file and retries once. If that also fails, it stops editing. It returns one line giving the reason, then the region in full, from `## Approach` through the end of `## Out of scope`, with nothing after.
  - When its edits land, its return carries no `## ` heading lines, only any open decision.
  - The charter cites `plan-it/SKILL.md` Step 5 for the placeholder, its first-non-blank-line definition and the four heading literals, and restates none of them. Step 5 is their single home.
  - anchors: root, row3, row10, row14, row15, row16, row18, row57, row58, row74.
- **M6 — Consult mode stays limited by the charter only.** `## MODE=consult` says "never call `Edit`, even when the prompt names a plan file." M8's `test_skills.py` test pins that sentence. anchors: row8, row9, row32, row48, row74.
- **M7 — Step 5 dispatch protocol,** in `plan-it/SKILL.md`.
  - Heading lines: every heading test below counts only line-start `## ` lines outside fenced code.
  - Precondition: the title and Context, `Ask:` line included, are already in the plan file. The hash range ends at the file's first line reading exactly `## Approach`, and the hash command cannot see fences. Context therefore never carries such a line, fenced or not, and quotes that heading inline in backticks.
  - Scaffold: at first authoring, the file holds only the title and Context and has no `## Approach` heading line. The session appends one blank line and then exactly these bytes: `## Approach\n\n(pending: plan-architect)\n\n## Critical files\n\n## Verification\n\n## Out of scope\n`. The placeholder is the first non-blank line under `## Approach`, and it reads exactly `(pending: plan-architect)`.
  - Legacy plans: a file with more than the title and Context but no `## Approach` heading line cannot pass the Check. Neither can one whose region carries a heading line other than the four. Step 5 treats either kind as a failed Check before any dispatch and asks the engineer through `AskUserQuestion`. It does not scaffold, dispatch or restructure (row 76).
  - Prompt: line 1 is `MODE=plan-sections`, and line 2 is the plan file's absolute path.
  - Baseline: the session runs `sed -n '1,/^## Approach$/p' "<plan path>" | sha256sum` immediately before each dispatch. That is after the scaffold and after any Context edit of its own.
  - Further runs: the session runs the same command on every return, failed and truncated returns included, before any splice or re-dispatch. It runs it again after any splice.
  - Stop condition: Step 5 stops and reports to the engineer in any of three cases. The two digests differ. Either digest starts `e3b0c442`, the digest of empty input, which an unreadable path also yields. Either run printed anything besides its digest line. Step 5's text carries the phrases "on every return" and "the two digests differ", which M8 pins.
  - Splice: the session splices only when the return carries all four headings as heading lines, in order.
    - It `Read`s the plan file. With one `Edit` whose `old_string` is the file's region, from its `## Approach` heading line to the end, it replaces that region with the return from its `## Approach` heading line on, verbatim.
    - Every byte above the heading stays untouched. The splice never uses a whole-file `Write`.
    - It then relays the return's reason line to the engineer.
  - Check: after any splice, the session re-runs the hash command and applies the stop condition to it. It then `Read`s the plan file. The dispatch is complete when the first non-blank line under `## Approach` is not `(pending: plan-architect)` and the only heading lines from `## Approach` on are the four headings, in order.
  - Re-dispatch triggers:
    - A return carrying some but not all four headings.
    - A file that fails the Check, legacy plans excepted.
    - A context-limit failure or tool error mid-dispatch, only when the Check then fails.
    - A near-empty return is the success shape, so it is never judged truncated.
  - The open-decision → `AskUserQuestion` → re-dispatch rule is unchanged.
  - "Read-only and idempotent" is replaced by: "A re-dispatch is always safe: `plan-architect` reads the plan file as it now stands, so a partial write is revised, not lost."
  - anchors: row14, row15, row17, row34, row52, row53, row57, row58, row62, row76.
- **M8 — Tests.**
  - The gate's matrix, with one case per branch plus a direct `src/x.py` deny case (Verification 2). Allow-shaped cases assert empty stdout (row 73).
  - A wiring pin on a matcher spanning `Edit`, `Write` and `MultiEdit` (row 36).
  - The gate added to the no-`if` pin set, and its steered-input sentence added to the tier-rationale pins (row 64).
  - Registry predicate and derivation-subset tests.
  - A `plan-architect` `Edit` case in the marker gate's file-write tests (row 39).
  - The exact-tools pin updated to `{Read, Grep, Glob, Edit}` (row 13).
  - A roster test confining every agent that declares `Edit` or `MultiEdit` without `Bash` to the review-only or plan-writer registry, over the `Write` test's corpus (rows 27, 65).
  - One `test_skills.py` test (rows 66, 74).
    - It pins Step 5's literals in `plan-it/SKILL.md`: the hash command, `e3b0c442`, "the two digests differ", "on every return", `(pending: plan-architect)` and the four heading literals.
    - It also pins `plan-architect.md`'s consult never-call-`Edit` sentence.
  - The denial-label registry rows (row 35).
  - anchors: row11, row13, row20, row22, row27, row35, row36, row39, row64, row65, row66, row73, row74.
- **M9 — Doc sync.**
  - The decision file, including the windows' consequence (row 71) and the plan-mode double deny (row 16).
  - The three supersession lines.
  - The CHANGELOG entry.
  - The stale-claim sites from row 21, except `docs/cost-levers-considered.md:434`.
  - anchors: row11, row16, row19, row21, row28, row29, row30, row32, row41, row63, row71, row75.
- **M10 — The PR body's one install-related sentence:** "No `install.sh` run or re-stow is needed on a standard install, where `~/.claude/hooks` is a single symlink into the checkout: `git pull` delivers the new hook file."
  - It satisfies `require-stow-reminder.sh`'s substring match on `gh pr create` and on every `gh pr edit` that changes the body.
  - It names only the standard layout, so it adds no instruction.
  - It stays true on the unfolded layout.
  - anchors: row19, row24, row25, row31.
- **M11 — Post-pull probe, report only.** One `test -e` on the installed gate path, which the engineer runs after merging and pulling.
  - It reports the result. It states that a missing gate file leaves the grant ungated, whether or not a hook error shows.
  - It blocks nothing. It is advisory and observes only the machine it runs on (Verification 9).
  - anchors: row30, row47, row55, row60, row61, row71.
- **M12 — Fixed plan grammar.**
  - `plan-it/SKILL.md`'s section list makes `## Out of scope` always present, reading `None observed.` when empty.
  - It states that sections 2-5 sit under those exact headings, in order, ending the file.
  - It keeps line 116's optional-Effort rule and adds that an Effort section, if present, sits above `## Approach` (row 70).
  - `plan-architect.md`'s grammar sentence says the four sections sit under the headings `plan-it/SKILL.md` names, always, in order, ending the file. It does not restate the heading literals.
  - anchors: row57, row58, row59, row70.

## Critical files

**Dispatch split.**
- The main session makes the `claude/.claude/settings.json` registration before Dispatch A (row 69). The registration is committed with Dispatch A's files, so a revert of A removes both.
- Two `code-writer` dispatches then run in sequence. A builds the gate mechanism. B writes the charter, skill prose and docs, and cites A's file and registry names.
- Both dispatch prompts point at this plan file rather than restating it. Both run in the feature worktree, without `isolation: "worktree"`. A's prompt says `settings.json` is already edited and must not be touched.
- A alone is inert, because no agent holds `Edit` yet. B's roster test reads A's registry.
- Land both in one PR. If they become separate commits, revert B before A.
- The PR body follows M10 and is not a file in this list. Its test plan lists Verification 9 and 10 as post-merge steps for the engineer.

**Main session, before Dispatch A.**
- `claude/.claude/settings.json` — register the gate in the `Edit|Write|MultiEdit` PreToolUse group (lines 343-382). Use no `if`, and set `"statusMessage": "Checking plan-writer scope..."`. `ask-review-permissions.sh` asks on this edit.

**Dispatch A — gate mechanism.** Before each verification run, `git add -N` each new file (Verification 1). Verify with `.venv/bin/python3 claude/.claude/scripts/select-tests.py` plus shellcheck.
- `claude/.claude/hooks/deny-plan-writer-off-plan-writes.sh` (new), implementing M2.
  - Line 2 is `# hook-class: gate`, and line 3 is `# tier-threat-model: cooperative, untrusted-input, irreversible`.
  - Header, one sentence per comment line:
    - "Confines a plan-writer agent's file writes to existing plan files directly under this repository's `.claude/plans/`."
    - "A plan-writer agent reads repository files that can carry content from outside the session, so steered input is in scope." This sentence stays on one line, because a substring test pins it.
    - "A bypass edits the dispatching session's uncommitted tree."
    - "An unparseable payload denies every caller, because the payload is parsed before the agent type is known."
    - "For a plan-writer, an empty or relative path, an empty cwd, a failed git or realpath call, or a missing target denies."
    - "Registered without an `if` condition, because `if` cannot read agent identity."
    - "This hook filters on tool name and `.agent_type` itself."
  - Known gaps, one item each. This is the canonical list. The decision file records that the gaps are accepted and cites this list. It adds only what lapses in the two windows where this hook does not run.
    - "Any plan file under `.claude/plans/` is writable, not only the one the dispatch named." "An edit to a committed plan re-arms the plan-review gate." "An edit to another untracked plan goes undetected and has no git recovery."
    - "Any part of an allowed plan file is writable." "This hook does not detect a change above `## Approach`." "The dispatching `/plan-it` session does."
    - "The dispatch mode is invisible to this hook, so it allows a consult-mode `Edit` to a plan file."
    - "The target can be swapped between this check and the tool's write."
    - "A hard link under `.claude/plans/` to a file elsewhere, created beforehand, passes the path check."
    - "A symlinked `.claude/plans` directory causes a false deny, and so does a nested-worktree plan reached from the main tree's cwd." "In either case the agent returns its sections as text."
    - "A payload without `.agent_type` reads as a non-plan-writer and passes." "`enforce-marker-script-shape.sh`'s header records the same dependency on the harness's identity fields."
    - "This hook does not run where its file is not linked into the config directory."
    - "A session that loaded its hook registrations before this hook was added may not run it."
    - "Writes by the main session, and by agents outside the plan-writer registry, are out of scope."
  - The header and comments carry no labels from this plan.
  - `DENY_GATE_LABEL="plan-writer-scope"`. The bootstrap `emit_deny` stub follows `require-plan-review.sh:56-76`. Its `_lib.sh` failure message is only `could not source _lib.sh.`, with no `install.sh` or re-stow remedy (row 25).
  - Order: `_lib_parse_tool_input_or_deny` first, then the tool-name filter, then `_lib_is_plan_writer_agent`. `test_blocks_when_jq_absent_with_valid_payload` requires the parse to come first.
  - Repo root: `_lib_capped git -C "$CWD" rev-parse --show-toplevel`, as at `require-plan-review.sh:95`. Run it in a command substitution that first unsets `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE`, the subshell shape `deny-reviewer-tree-mutation.sh:328-332` uses around its `git check-ignore`.
  - Path check: deny a path that does not start with `/`. Then apply `_lib_realpath_m` to the root and the target, test the resolved target with `[[ -f ]]`, and call `_lib_is_repo_plan_file`. This is the same sequence as `require-plan-review.sh:192-206`.
  - Deny literals carry at most one cause marker each and no "Blocked by" text (row 35). The main message reads: "a plan-writer agent may edit only an existing plan file directly under this repository's `.claude/plans/`; return your sections as text for the dispatching session to insert."
- `claude/.claude/hooks/_lib.sh`.
  - Before `_LIB_NO_GATE_RELEASE_AGENTS`, add `_LIB_PLAN_WRITER_AGENTS=(plan-architect)` with `_lib_plan_writer_agents` and `_lib_is_plan_writer_agent`.
    - Build them on `_lib_list_contains`, so empty input never matches.
    - Put a one-line definition above them: a plan-writer agent may edit plan files and nothing else.
  - Derive `_LIB_NO_GATE_RELEASE_AGENTS` from `"${_LIB_REVIEW_ONLY_AGENTS[@]}" "${_LIB_PLAN_WRITER_AGENTS[@]}" code-writer`. Revise its header comment (lines 3490-3516) so it names the plan-writers.
  - In `_lib_list_contains`'s comment (lines 3464 and 3468), drop the "three" count. "all three call sites' arrays" becomes "every call site's array", and "Shared by the three membership scans below" becomes "Shared by the membership scans below".
- `claude/.claude/hooks/tests/test_deny_plan_writer_off_plan_writes.py` (new) — Verification 2's matrix.
  - Structural template: `test_deny_reviewer_tree_mutation.py`, plus `run_hook` and the `edit_input`, `write_input`, `multiedit_input` and `read_input` builders in `claude/.claude/tests/helpers.py`.
  - The two allow cases and the three exit-0 cases each assert exit 0, empty stdout and empty stderr. Empty stdout is what separates an allow from a normal deny, which also exits 0 (row 73).
  - The `Read` case merges `agent_type="plan-architect"` into `read_input`'s payload, because `read_input` takes no agent type (row 73).
  - The fixture asserts that the scaffolded plan, `src/x.py` and the loose ref are files before any test runs.
  - The linked-worktree fixture copies `test_require_plan_review.py`'s sibling-worktree fixture (around lines 1803-1830).
  - The empty-`.cwd` and relative-path cases pass `cwd=<fixture worktree root>` to `run_hook`, so only their own guard can produce the deny.
- `claude/.claude/hooks/tests/test_lib.py`.
  - The predicate test: `_lib_is_plan_writer_agent` accepts every registry member. It rejects `""`, `plan-architect-x`, `PLAN-ARCHITECT`, `plan_architect`, `code-writer` and `general-purpose`. This is the only registry-member test this change adds.
  - A subset-by-derivation test mirroring lines 1468-1477.
  - `plan-architect` added to the member cases at line 1488.
  - Line 6372's docstring drops the count: "The three call sites rely on ..." becomes "The call sites rely on ...", matching `_lib.sh:3464`.
- `claude/.claude/hooks/tests/test_hook_alignment.py`.
  - Add `test_plan_writer_gate_wired_on_edit_write_multiedit` beside line 574's single-hook pin.
    - It asserts that `_matchers_spanning_edit_write_multiedit(_pretooluse_matcher_groups_for(<gate>))` is non-empty.
    - Its failure message names `Edit` as the confined agent's only write tool.
  - Add the gate to `_SELF_FILTERING_BASH_GATES` (line 905), with the one-line comment "An Edit|Write|MultiEdit gate that self-filters on tool name and `.agent_type`."
  - Add an entry to `_TIER_RATIONALE_HOOK_SENTENCES` (line 1803) holding the header's steered-input sentence.
  - Add the gate to `_UNTRUSTED_INPUT_FLOOR` and `_IRREVERSIBLE_FLOOR`.
  - Update the comments: "seven gates" becomes "eight gates" (line 1708), "14 gates" becomes "15 gates" (line 1752), and "31" becomes "32" (line 1691).
- `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`.
  - Add `test_plan_writer_marker_path_edit_denied` to `TestGateReleaseAuthorityFileWrites`. Parametrize it over the same six relative paths as `test_marker_path_write_denied`, and send `edit_input(..., agent_type="plan-architect")`. Do not add `plan-architect` to the shared `NO_GATE_RELEASE_AGENTS` list at line 752, which feeds the Bash-path cases.
  - Reword the docstring at lines 1654-1655 ("every agent ... carries the Write tool") to "agents in the set that hold `Write` or `Edit`".
- `claude/.claude/scripts/transcript_analysis/denials.py` — add `"plan-writer-scope",  # deny-plan-writer-off-plan-writes.sh` to the active block of `_DENIAL_HOOK_LABELS` (lines 128-155).
- `claude/.claude/scripts/tests/test_transcript_denials.py` — append the gate's row to `_BOOTSTRAP_FALLBACK_HOOKS` (after line 318). The first 24 rows stay unedited. No `_LIB_SOURCE_FIXTURES` row.
- `docs/hooks.md`.
  - A tier-table row quoting the header's steered-input sentence and its consequence sentence.
  - Line 59's counts become 17 plain, 7 `cooperative, irreversible` and 8 `cooperative, untrusted-input, irreversible`, with the gate added to the floor list. That makes 15 `irreversible` gates and 32 gates in total.
  - A Gate hooks bullet.
  - Lines 199 and 250: "the review-only personas plus `code-writer`" becomes "the review-only personas, the plan-writer agents (`plan-architect`) and `code-writer`".
  - Line 254 changes "every agent in the set carries the `Write` tool" to "agents in the set that hold `Write` or `Edit`".
- `README.md` — a row in the Hook → Gates → Cleared-by table, after line 174.
  - It says the gate blocks "`Write`/`MultiEdit`, or an `Edit` to anything but an existing `.claude/plans/` file, from a plan-writer agent (`plan-architect`)".
  - Cleared-by: "No clear — the agent returns its sections as text".

**Dispatch B — charter, prose, docs.** Verify with the same commands. `/code-review` routes this dispatch's files to `/skill-review`, `/agent-review` and `ai-instruction-and-memory-files`. No durable file it writes may carry this plan's labels (row numbers, M-numbers, G-numbers).
- `claude/.claude/agents/plan-architect.md`.
  - Frontmatter: `tools: Read, Grep, Glob, Edit`. In `description`, drop "Read-only", and say that mode (1) writes its sections into the plan file. Keep the description no longer than it is today.
  - Lines 9-14 say it holds `Edit` but no `Write`, `Bash` or `Skill`. They also say that only `MODE=plan-sections` uses `Edit`, and that a hook denies any `Edit` outside an existing plan file under the repository's `.claude/plans/`.
  - `## MODE=plan-sections` keeps its heading (row 22). Its grammar sentence (lines 35-37) says the four sections sit under the headings `plan-it/SKILL.md` names, always present, in order, ending the file. It carries M5. For the placeholder, its first-non-blank-line definition and the four heading literals, it cites `plan-it/SKILL.md` Step 5 and restates none of them. Lines 39-40's "inserts your return verbatim" now applies only when the agent returns its sections.
  - `## MODE=consult` gains "never call `Edit`, even when the prompt names a plan file."
- `claude/.claude/hooks/tests/test_agent_roster.py`.
  - Rename `test_plan_architect_tools_are_exactly_read_grep_glob` to `..._read_grep_glob_edit`. Its docstring says what `Edit`, no `Write` and no `Bash` each rest on.
  - Rewrite the `TestNoGateReleaseRosterSync` docstring paragraph at lines 676-680. `plan-architect` is now a roster member, and its pin is no longer its sole guard.
  - Extract a module-level helper that parses a frontmatter path's `tools` with the empty fallback the `Write` test uses inline (lines 846-852). Point `test_write_without_bash_agents_are_review_only_confined` at it, keeping that test's name and assertion, because a decision file cites it by name.
  - Add `TestEditWithoutBashAgentsAreWriteScopeConfined::test_edit_without_bash_agents_are_write_scope_confined`. It iterates `TestAgentFrontmatter._AGENT_AND_PLUGIN_FILES` through the same helper. Every agent that declares `Edit` or `MultiEdit` without `Bash` must be in `_LIB_REVIEW_ONLY_AGENTS` or the plan-writer registry. The registry is read through `_lib_plan_writer_agents`, as `_review_only_agents()` reads its roster.
- `claude-skills/skills/plan-it/SKILL.md`. The file is 159 lines today, and it must stay under `check-skill-length.sh`'s 200-line default cap.
  - Line 49: replace "`plan-architect` writes nothing" with the real reason, which is that its edits must land in this worktree's plan file.
  - Add a dispatch-prompt bullet for the plan path on line 2.
  - Add these parts of M7:
    - The heading-line definition.
    - The precondition, including the rule that Context quotes `## Approach` only inline in backticks.
    - The scaffold bytes and the placeholder definition.
    - The legacy-plan rule, the baseline, the further runs and the stop condition.
    - It carries the placeholder literal, the four heading literals, the hash command, the `e3b0c442` literal and the phrases "on every return" and "the two digests differ".
  - Replace lines 58-65 with M7's splice, check and re-dispatch triggers. Keep the open-decision → `AskUserQuestion` → re-dispatch rule. Replace "read-only and idempotent" with M7's "A re-dispatch is always safe" sentence.
  - Lines 108-116 follow M12.
    - Item 5 becomes "**Out of scope** — always present; `None observed.` when empty".
    - Add one line stating that sections 2-5 sit under their exact headings, in order, ending the file.
    - Line 116 stays, amended to say that an Effort section, if present, sits above `## Approach` (row 70).
- `claude-skills/skills/tests/test_skills.py` — one test beside `TestPlanItStep7FallbackToJudgment`, in the shape of lines 6327-6336. Its docstring says it pins Step 5's Context-change check and the plan-grammar literals Step 5 is the single home of.
  - First assertion: every literal in one tuple is in `_skill_file("plan-it").read_text()`. The tuple holds `sed -n '1,/^## Approach$/p'`, `e3b0c442`, `the two digests differ`, `on every return`, `(pending: plan-architect)`, `## Approach`, `## Critical files`, `## Verification` and `## Out of scope`.
  - Second assertion: `_agent_body("plan-architect")` contains "never call `Edit`, even when the prompt names a plan file" (row 74).
- `claude-skills/skills/code-review/SKILL.md:370` — delete ", to insert the returned sections" so the line defers to `plan-it` Step 5. The line sits inside the `DISPOSITION_RULE:code-review-new-primitive-route` region, so leave both anchors untouched.
- `claude/.claude/CLAUDE.md:178` — change "(already read-only and Opus-pinned; no need to restate its charter)" to "(already Opus-pinned; no need to restate its charter)". The file is already over the byte limit, but the length gate denies only growth, so a 14-byte shrink cannot trip it (row 63).
- `docs/design-decisions/plan-architect-writes-its-own-plan-sections.md` (new).
  - Format: H1, then `*2026-10-05.*`, with no `Formerly §N`.
  - Cite §30 and §37 by link. State the benefit qualitatively, with no figure.
  - It carries no `install.sh` or re-stow instruction, no unconditional claim that `git pull` does not link a new file, and no migration wording.
  - Contents:
    - The decision.
    - Why `Edit` and not `Write`.
    - Why the gate checks the path only while `/plan-it` checks Context.
    - Why a dedicated gate.
    - Why one agent, on §37's description-cost ground.
    - The tier, and why it carries `untrusted-input`.
    - Roster enrollment by derivation.
    - Why every plan ends with the four fixed headings, and why the session splices only a return that carries all four.
  - Notes:
    - `plan-it/SKILL.md` Step 5 is the single home of `(pending: plan-architect)` and the four heading literals. `plan-architect.md` cites it, and a `test_skills.py` test pins the literals there.
    - The hash command is one statement with none of the worktree guard's trigger shapes, and `sha256sum` is an `install.sh` prerequisite. A digest starting `e3b0c442` means nothing was hashed.
  - Accepted residuals:
    - Consult mode is limited by its charter only. Three of the four prescribed consult call sites pass a plan path, as does the engineer-asked consult. A consult-mode `Edit` can reach Context and the `Ask:` line. A consult the engineer asks for mid-planning raises no signal: the plan is already active, so the plan-review gate does not re-arm, and the Context hash covers only plan-sections dispatches.
    - A session that puts the plan path on prompt line 1 makes the dispatch classify as a consult, which writes the per-branch consult latch.
    - The two windows in which the gate does not run, which its header lists. One is an install whose hooks directory is "a real directory of per-file links", where the pull leaves the gate unlinked. The other is the unverified case of a session whose hook registrations predate the gate.
      - State what lapses: `plan-architect` holds an `Edit` confined only by its charter, over the whole tree. Forged-anchor targets (`MERGE_HEAD`, `refs/remotes/...`) are included.
      - State what stays guarded: marker and ledger paths, through the roster derivation, and `settings*.json`, on which `ask-review-permissions.sh` asks.
      - The repo expects a hook error on each call in the unlinked case but has not verified it, so the grant may run ungated with no visible error.
    - A plan-mode dispatch's `Edit` to plan mode's default plan path is denied twice, on the `Edit` and on its one retry, before the agent returns its sections. These denies count under `plan-writer-scope`.
    - One more hook process per `Edit`/`Write`/`MultiEdit` call in every session. State it qualitatively: for any caller other than a plan-writer, the hook bootstraps and exits early. It runs in parallel with the group's other hooks, so it adds CPU time rather than wall-clock time. The committed plan `.claude/plans/hook-bootstrap-cost.md` records the measurement this rests on.
    - The live `.agent_type` wiring rests on vendor documentation (`agent-review/REFERENCES.md:10`) and the sibling gate's production use.
    - The gate's known gaps are accepted as its header lists them. Cite that list and do not repeat it. Inside the allowed set, an untracked plan file has no git recovery, so do not describe the gate as protecting the uncommitted tree in general.
  - Revisit triggers:
    - A gate deny on a repository plan file that `/plan-it` relays.
    - A plan-review deny right after a `MODE=consult` dispatch in `/code-review` or `/ready-for-review`.
    - A Context-hash stop.
    - A post-pull probe on the author's machine that reports the gate file missing. The probe observes no other install.
- `docs/design-decisions/plan-it-step5-pinned-opus-dispatch.md` — a `**Partially superseded by [...](plan-architect-writes-its-own-plan-sections.md) (2026-10-05):**` line under the provenance line.
  - It names two passages as no longer current, by content rather than line number: the `tools: Read, Grep, Glob` pin and the no-`Edit` paragraph.
  - It says the Opus pin and both residual risks stand.
  - For the second residual risk, it says the mechanism changed. The agent writes its sections directly by default, and the session inserts them verbatim only in the fallback.
  - It names what bounds that risk now: the Context hash, which covers text above `## Approach` only; the gate's path rule; and `/plan-review` (row 75).
- `docs/design-decisions/plan-architect-consult-mode.md` — a matching line, in one sentence: consult dispatches now reach an agent that holds `Edit`, which its consult charter forbids it to use, and the decision to widen one agent rather than add a second stands.
- `docs/design-decisions/plan-review-gate-disarms-on-empty-active-plan-set.md` — a matching line.
  - A third writer without `Bash`, `plan-architect`, now holds `Edit`.
  - Wherever `deny-plan-writer-off-plan-writes.sh` runs, the Low rating also rests on it, an `untrusted-input`-tier gate confining that writer to existing plan files.
  - Where the gate's file is not linked, or in a session whose hook registrations predate it, `plan-architect` is an unconfined `Edit`-only writer. The rating's premise that every writer without `Bash` is confined does not hold there (row 71).
  - `test_edit_without_bash_agents_are_write_scope_confined` and that gate's forged-anchor test pin the confinement.
- `CHANGELOG.md` — an `[Unreleased]` → `### Changed` entry.
  - It covers `plan-architect`'s `Edit` grant, the new gate and what it denies, Step 5's scaffold and Context hash, and the four fixed plan headings. It links to the decision file.
  - It has no migration, `install.sh` or re-stow line, and does not follow the shape of the lines 41-42 entry (G2, rows 19, 30, 60).

## Verification

1. **Stage, then run.** After each dispatch, `git add -N` every new file so that the `git ls-files`-based corpora (`scripts/list-shell-files.sh`, `test_shellcheck.py`, `test_shell_lib_function_liveness.py`) and `select-tests.py` see it. Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
2. **Gate matrix, fourteen cases,** in `test_deny_plan_writer_off_plan_writes.py`. The fixtures are a real temp git repo with a linked worktree, a scaffolded plan at `<worktree>/.claude/plans/p.md`, and `<worktree>/src/x.py`. The fixture asserts that the plan, `src/x.py` and the loose ref below are files before any test runs. The agent type is `plan-architect` unless noted.
   - **Allow** (exit 0, empty stdout, empty stderr):
     - A first-authoring `Edit` on the plan, with `.cwd` at the worktree root.
     - The same `Edit` with `.cwd` at `<worktree>/src` and the plan's absolute path.
   - **Deny:**
     - `Write` to the plan.
     - `MultiEdit` to the plan.
     - `Edit` to `<worktree>/src/x.py`.
     - `Edit` to a nonexistent `.claude/plans/new.md`.
     - `Edit` to `<git-common-dir>/refs/remotes/origin/main`, a loose ref the fixture creates, from the linked worktree's `.cwd`. The test's docstring names `plan-review-gate-disarms-on-empty-active-plan-set.md`.
     - `Edit` to `.claude/plans/link.md`, a symlink to `<worktree>/src/x.py`.
     - An empty `.cwd`, with the plan's absolute path and the hook process run at the worktree root.
     - A `.cwd` outside any repository.
     - A relative `file_path` naming the plan (`.claude/plans/p.md`), with `.cwd` and the hook process both at the worktree root.
   - **Exit 0** (exit 0, empty stdout, empty stderr):
     - `code-writer` editing `src/x.py`.
     - An empty agent type (the main session) editing the same file.
     - A `Read` payload on `src/x.py`, built with `read_input` and with `agent_type="plan-architect"` merged in.
3. **Counts.** No test pins `docs/hooks.md:59`, so recount the tier table by hand. Expect 17, 7 and 8, which makes 15 `irreversible` gates and 32 gates in total.
4. **Mutation check.** For each mutation below, apply it, run the named test, and revert it by hand. Do not revert with `git checkout`, which would clobber uncommitted work. Record each mutation and the test that failed in the PR description.
   - Drop the realpath normalization: the symlink case fails.
   - Drop the existence check: the nonexistent-target case fails.
   - Delete the empty-`.cwd` guard, then separately the leading-`/` guard: each one's case fails.
   - Replace the `_lib_is_repo_plan_file` call with the `[[ -f ]]` test alone: the `src/x.py`, symlink and `refs/remotes` cases fail.
   - Make the gate deny the valid scaffolded-plan `Edit`: both allow cases fail.
   - Delete the tool-name filter: the `Read` case fails.
   - Move the gate's `settings.json` entry into a PreToolUse group whose matcher lacks `Edit`: `test_plan_writer_gate_wired_on_edit_write_multiedit` fails, and the other wiring pins stay green.
   - Add an `if` key to the gate's entry: the gate's `test_self_filtering_bash_gate_has_no_if_matcher` case fails.
   - Remove `plan-architect` from `_LIB_PLAN_WRITER_AGENTS`: the roster confinement test and the gate's deny cases fail.
   - Add `Edit` without `Bash` to the `tools` of a plugin agent outside both rosters: `test_edit_without_bash_agents_are_write_scope_confined` fails, which shows its corpus includes plugin agents.
5. **Stale-claim sweep.** Run `grep -rnE` over `claude/`, `claude-skills/` and `docs/` for `read-only and idempotent|writes nothing|inserts your return verbatim|already read-only|Read-only Opus|read-only design-synthesis|read-only agent|sole guard|carries the .?Write|every agent in the set|review-only personas plus|three membership scans|three call sites|only if scope creep`. A remaining hit about `plan-architect`, the no-gate-release roster or the plan grammar may sit only in these places:
   - Past CHANGELOG entries.
   - Design-decision bodies other than the three supersession lines.
   - `docs/cost-levers-considered.md:434` (Out of scope).
6. **Plan-label sweep.** Run `git diff -U0 HEAD -- . ':(exclude).claude/plans' | grep -E '^\+.*\b(row ?[0-9]+|M[0-9]{1,2}|G[12])\b'`. Expect no plan labels in durable files, and inspect any hit by hand.
7. **No-install-instruction check.**
   - Run `git diff -U0 HEAD -- . ':(exclude).claude/plans' | grep -iE '^\+.*(install\.sh|stow)'` and read every hit. Expect no instruction to run `install.sh` or re-stow, and no restated unconditional claim that `git pull` does not link a new file. Factual mentions pass, such as the decision-file Note that `sha256sum` is an `install.sh` prerequisite or the filename `require-stow-reminder.sh`.
   - Before `gh pr create`, and before every `gh pr edit` that changes the body, confirm two things. No sentence in the body tells a reader to run `install.sh` or re-stow. No sentence restates, without qualification, the claim that `git pull` does not link a new file. The M10 sentence passes both.
8. **Review pipeline.** `/code-review` routes the files per `.claude/rules/review-pipeline-dispatch.md`. Also run `claude-hook-review:claude-hook-review` on the new gate.
9. **Post-merge probe (M11), advisory.** The engineer runs it after merging and pulling the merge into the main checkout. Merge is human-only, so the implementing session cannot run it, and the PR body's test plan lists it as a post-merge step. Run `test -e ~/.claude/hooks/deny-plan-writer-off-plan-writes.sh`. The engineer keeps the result, and nothing records it in the repo.
   - Exit 0: the gate file is linked on this machine.
   - Any other exit: this install links hook files one by one, and `settings.json` names a gate file that is not there. The grant then runs ungated, whether or not a hook error shows. The repo expects a hook error on each `Edit`/`Write`/`MultiEdit` call but has not verified it (row 47).
   - The probe blocks nothing and observes only the machine it runs on. No other stow consumer gets a signal from it.
10. **First real `/plan-it` run.** The engineer observes it, in a session started after the pull (row 44), and the PR body's test plan lists it. Confirm all of the following:
    - No gate deny occurred, and the return carries no `## ` heading lines.
    - The digests match the baseline, neither starts `e3b0c442`, and no run printed anything besides its digest line. Record at most each digest's first 8 characters anywhere durable.
    - The session's Step 5 `Read` finds that the first non-blank line under `## Approach` is not `(pending: plan-architect)`. The four headings are the only heading lines from `## Approach` on, in order, ending the file.

## Out of scope

- **Measuring the status-quo re-emission cost.** This change states its benefit qualitatively and claims no figure, so no measurement under `docs/private-project-redaction.md` § "Publishing a tooling measurement" is needed.
- **The return-format and ledger-grammar question.** It asks whether the plan grammar should be parser-grade rather than read by a model, given the main session's count that 29 of 419 committed plans follow `plan-it/REFERENCES.md:101-109`'s row form. It also asks whether a hook should check a plan's structural completeness. The engineer's selection sends this to a follow-up (rows 57, 58).
- **Committed plans without the four headings.** They are records of past changes and stay as written. A revision of one goes to the engineer under M7's legacy-plan rule, and this change adds no migration (row 76).
- **A cheaper splice for a revision fallback.** At revision, the splice's `Edit` quotes the whole old region and re-emits the new one, which costs more than today's insert. At first authoring the old region is the short scaffold.
- **A per-run `test -e` in Step 5, and a first-run check that tells a gate that allowed from a gate that never ran.** The engineer chose a post-pull probe that only reports (row 60). The live `.agent_type` wiring stays an accepted residual.
- **Hook-level enforcement of `MODE=consult`.** A dispatch-time latch is possible, but it costs per-dispatch state and needs a correlation key nobody has verified (rows 8, 46). The gate limits a consult-mode slip to plan files, and the decision file records the residual with a trigger.
- **`maxTurns` on `plan-architect`.** The field is documented, and the charter already bounds `Edit` retries at one (row 42). A value needs a measured expected turn count, as `check-runner`'s had. A cap set without that measurement would cut legitimate design runs short.
- **Allow-listing the harness plan-mode file path in the gate, or a prompt signal that skips the `Edit` in plan mode.** Plan-mode runs meet the gate's deny on the `Edit` and on its retry, then return their sections as text, so they keep today's re-emission (row 16). The decision file records the double deny as accepted. Row 1's objection does not distinguish plan mode, so raise this to the engineer.
- **Routing `/plan-review` findings back to `plan-architect`.** Step 6 is unchanged.
- **A `_EDIT_GOVERNANCE_PATTERNS` row in `transcript-analysis.py`.** It would only classify the gate's denials in one report, and no test requires it.
- **Transcript classification drift.** `corpus.py`, `audit_routing.py` and `reviewer_yield.py` count parent-transcript `Edit` blocks as parent code-writes. Moving plan authoring into the subagent transcript shifts those counts. No test breaks.
- **A `_LIB_SOURCE_FIXTURES` row for the gate.** No test requires one, and `deny-no-op-dispatch.sh` has none.
- **Past CHANGELOG entries (lines 41-42 and 168-183).** They are records of merged changes, read-only under Axis 3.
- **`log-reviewer-round.sh:144`'s `plan-architect` literal.** It keys the consult latch on `subagent_type`, which is a different question from write scope.
- **`enforce-marker-script-shape.sh:209-213`.** Its "plan-architect holds no Bash tool" sentence stays accurate.
- **`docs/cost-levers-considered.md:434`'s "Opus-pinned read-only agent".** This change deliberately leaves it unedited (row 51), and the table's verdict does not rest on that clause (row 56). The engineer's "Stale-claim edits" label covers the line on its face, so raise it to the engineer for a keep-or-edit call.
- **`docs/design-decisions/effort-estimated-by-review-surface.md`.** It stays accurate because `plan-it/SKILL.md:116`'s Effort rule stays (row 70). If the engineer picks deletion instead, it needs a supersession line.
- **`require-plan-review.sh`'s comment that `_lib_realpath_m` does not resolve symlinks** (near line 197). The comment is false for the reason row 37 gives, but the file is outside this change. Raise it to the reviewer.
- **The tier of `deny-reviewer-tree-mutation.sh` versus the forged-anchor record's citation of it.** This is a pre-existing mismatch of the same shape that row 45 resolves for the new gate. Raise it to the reviewer.
- **Renaming `deny-reviewer-tree-mutation.sh` into a generic per-agent write-scope gate.** That would rewrite a gate this change does not otherwise touch, along with every reference to its name.
- **Removing the unfolded-install window by moving the confinement into a hook that is already linked.** The engineer chose the dedicated gate and accepted the window (rows 23, 30).
- **Re-verifying G1, and probing session-start skew (row 44).** Each needs a throwaway live dispatch, and neither changes this design.
- **macOS realpath fallbacks.** CI runs Linux only, the same exposure the sibling gates have.
- **Making the claim in `require-stow-reminder.sh` and the 21 bootstrap messages conditional.** Raise this to the engineer as a follow-up.
  - Both say, without qualification, that `git pull` does not link a new file nested in an existing `~/.claude` directory. The claim holds for a new top-level entry under `claude/.claude/` and for a per-file-link install. It does not hold for a new file inside an existing entry on the standard layout (G2, row 25).
  - The hook's trigger, which fires on nested files too, stays correct for per-file-link installs. Only the claim needs a condition.
  - The engineer asked why agents keep adding a re-stow instruction ("You're not the first agent to make that mistake"). This plan's inference, not a measurement: the gate denies every PR that adds a hook until the body mentions `install.sh` or `stow`, and its deny message offers "Post-merge: run `./install.sh`" as the fix.