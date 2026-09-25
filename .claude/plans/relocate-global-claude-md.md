# Relocate restated content out of the global CLAUDE.md (GH-885)

## Context

Goal: shrink `claude/.claude/CLAUDE.md` toward its 25,600-byte ratchet by
removing content that another surface already delivers at the moment the
rule matters. The file is 183 lines / 32,019 bytes, 25% over the ratchet
(`check-claude-md-length.sh`'s `GLOBAL_CLAUDE_MD_BYTE_LIMIT`), and it loads
in every session for every stow consumer. Why now: GH-885 is the filed
follow-up to #919. Two in-place compression passes both fell short of their
targets (`docs/cost-levers-considered.md`, second-pass table), so
this pass relocates content instead of line-editing it. Intended outcome: a
strictly smaller file with no rule lost. Each removed passage is either a
restatement of a doc, or guidance a PreToolUse hook already delivers in its
deny message.

The engineer chose safe relocations only. Step 3's candidates suggested
about 3.9 KB. Verification in Step 5 found only about 421 bytes meet the
bar. The engineer then confirmed shipping that cut plus a recorded
third-pass entry, landing well short of 25,600, which the shrink-tolerant
ratchet permits.

## Approach

Delete three sentences from the Safety section's marker bullet (`claude/.claude/CLAUDE.md:20`, about 421 bytes). Another surface already covers each one when the rule applies: `docs/hooks.md` or the subagent denial message in `enforce-marker-script-shape.sh`. Leave every other Step 3 candidate unchanged. Checking showed that none of their destinations is loaded when their rule applies in every stow consumer session. Record the measured result and the reason each candidate stays in a new third-pass entry in `docs/cost-levers-considered.md`.

**Alternatives set aside.**
- **Relocating the Step 3 set as proposed (about 3.9 KB):** rows 9–13 show each larger candidate is deliberately always loaded, or has no copy loaded when its rule applies.
- **Copying the dropped text into `subagent-delegation`/`branch-management` or into a new path-scoped rule:** fails the row 3 test. Dispatch-time and Bash-time rules don't fire on a file read, and this would add another place the rule lives.
- **Compressing in place** (for example, merging :107 into :116): the engineer limited this pass to relocation.
- **Adding CLAUDE.md content to hook deny messages so more text can move later:** that creates a new enforcement surface rather than moving text to an existing one.

**Assumption ledger**

**Root:** `claude/.claude/CLAUDE.md` loads in every session for every stow consumer and is over its 25,600-byte limit. This pass removes only text whose behavior another surface already delivers when the rule applies, in every stow consumer session.

**Givens:**
- G1. Every session and every subagent except `Explore`/`Plan` loads the full CLAUDE.md hierarchy, with no per-agent exclusion. The vendor controls this. [verified: `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:27,41`]
- G2. A skill body enters context only once the skill is invoked. The available-skills listing carries only its description. The vendor controls this loading model. [verified: `docs/design-decisions/no-op-dispatch-guard.md:7`, a recurrence in a session that held only the description]
- G3. A path-scoped `.claude/rules/*.md` file loads when a matching file is read. A dispatch or a Bash call does not count as such a read. The vendor controls this. [verified: `declined-sessionstart-additionalcontext-injection.md:17-23`; `.claude/plans/trim-global-claude-md.md:20-23`]

**Rows:**
1. This session owns the trim, and no other effort competes with it. [engineer-verified]
2. Scope is safe relocations only. Model & Effort Routing and the delegation-decision bullets stay. The 25,600-byte limit is not raised, and landing short of it is acceptable. [engineer-verified]
3. **The test for moving text:** a passage may leave only if its destination is loaded when its rule applies, in every stow consumer session.
   - A hook message passes when the hook is registered in the stowed `claude/.claude/settings.json` and fires on every path the rule covers.
   - A skill body passes only when the triggering action itself loads that body (G2, G3).

   [verified: `declined-sessionstart-additionalcontext-injection.md:35` ranks "move dispatch mechanics into the dispatching skills" last because that covers prescribed dispatches only]
4. **Sentence B**, "Each review skill writes its own marker directory (…) when a review passes." (about 165 bytes), restates `docs/hooks.md:103-107`. Its practical effect survives in the same bullet: "Never write `<config-dir>/*-markers/*` by hand" and "run that skill". [verified: CLAUDE.md:20; docs/hooks.md:103-107]
5. **Sentence D**, "The guarded operation varies by skill and is not always the commit." (about 68 bytes), restates the per-gate operations documented at `docs/hooks.md:151,167,177`. The surviving sentence "Every denial names both the operation it blocked and the review skill to run" carries the action. [verified]
6. **Sentence F**, "`code-writer` and the reviewer agents cannot run review skills and are denied marker writes — when one hits a review gate, it reports the denial and the dispatching session resolves it." (about 188 bytes), reaches its reader when the rule applies.
   - `enforce-marker-script-shape.sh:153-157` defines `GATE_RELEASE_DENIAL_GUIDANCE`. It is appended to all three marker-write deny paths (:275, :492, :581).
   - Those denials fire for every agent in `_LIB_NO_GATE_RELEASE_AGENTS` (`_lib.sh:3012-3015`: the review-only personas plus `code-writer`).
   - The hook is registered at `settings.json:307,362`.
   - A subagent that hits a gate other than a marker write is covered by the surviving CLAUDE.md:117 ("Report the denial verbatim to whoever dispatched you, name what you could not reach, and stop"). `code-writer.md:21` also says it never commits.
   - The parent's choice of delegate survives at :20: "delegate it to a `general-purpose` subagent, which carries the `Skill` tool".

   [verified]
7. No test or live doc pins or cites B, D, or F. `claude-skills/skills/tests/test_skills.py:1426-1452` pins five phrases, all in text this plan keeps. A grep for all three sentences outside `.claude/plans/` finds them only at CLAUDE.md:20. [verified]
8. No pointer is needed. Each removed sentence has a surviving line that carries its action (rows 4–6). No heading changes, so every live "Safety" or "Agent Briefing" citation on Step 3's list stays valid. [verified]
9. **KEEP the no-op-dispatch bullet (:101-106).**
   - `deny-no-op-dispatch.sh` only catches prompts under 600 characters that match a fixed list of phrases (:50, :62, :68).
   - `docs/design-decisions/no-op-dispatch-hook-gate.md:21` says "The CLAUDE.md bullet remains the primary surface."
   - `no-op-dispatch-guard.md:7` records that putting the rule only in the skill body failed.
   - The hook's phrase list is sourced from this bullet: header comment at :54, test docstrings at `test_deny_no_op_dispatch.py:166,178`.

   [verified]
10. **KEEP the `isolation: "worktree"` bullet (:116).** Step 3 said it was "covered by `branch-management/SKILL.md:70-99`". That does not hold. Lines 89-97 cover only the anchor-hold rule, and they cite `claude/.claude/CLAUDE.md` § "Agent Briefing" as the canonical home for isolation. A grep for "ephemeral-isolation" outside `.claude/plans/` finds only CLAUDE.md and transcript-analysis code. `plan-it/SKILL.md:83` and `test_agent_roster.py:523` also cite this bullet. [verified]
11. **KEEP the anchor-hold bullet (:118).** `branch-management/SKILL.md:89-94` nearly duplicates it. But its preventive step, "Finish anchoring, then dispatch", matters when the session composes the dispatch. The bullet's own example is a dispatch batched in parallel with `Skill(branch-management)`, before that skill's body has loaded. `EnterWorktree` can also be called with no skill loaded. Moving this bullet would also split the :116/:118 topic across two canonical homes. [verified: CLAUDE.md:118; branch-management/SKILL.md:89-97] Whether the harness's `EnterWorktree` description warns about running dispatches is [unverified]. It could not reach the dispatch-composition moment either way.
12. **KEEP "Name every new package before it is fetched" (:6).**
    - `docs/security-hardening.md:279-288` records it as layer 1 of three. It is the only layer covering a bare restore, and there is "no deny-class backstop, by decision".
    - `ask-new-dependency-disclosure.sh:83-95` lists more paths it does not cover: Bash-written manifests, `npm pkg set`, lockfiles, and ecosystems outside its list.

    [verified]
13. **KEEP the rest of the marker bullet.**
    - Row 7's test pins hold it in place.
    - `enforce-marker-script-shape.sh:264` exits early for the main session, because an empty agent type never matches (`_lib.sh:3022-3027`).
    - The Bash path allows the valid `marker.sh write <kind>` form (:669-671).

    So only this prose stops the main session from forging a marker after a "ship it" instruction. [verified]
14. **KEEP the other Agent Briefing mechanics** (:107, :108, :115, :117, :119-126). All fail row 3.
    - Grepping `subagent-delegation` and `branch-management` for isolation, "Working directory", and ExitPlanMode found no copy of :107, :108, or :117. The same holds for :119-126: its guard is harness-level, not a repo hook.
    - :115 exists to avoid the enforcement hook's round-trip denial, so that denial cannot replace it.

    [verified] That no hook delivers :108 is [unverified]. That gap can only argue for keeping it.
15. **KEEP the other Safety mechanics** (:9-12, :21, :24). All fail row 3.
    - `deny-credential-bash-reads.sh:93` names only the `!` branch of :10-11, and :110 names neither branch.
    - No deny message names `marker.sh clear-stale` (grep of the hooks).
    - `ask-review-permissions.sh:33` points to `/review-permissions` without stating the glob rule, and never fires when the glob is recommended in chat.

    [verified] Whether `!`-escape commands reach any hook is [unverified]. That gap can only argue for keeping them.
16. The size check lets through a commit that shrinks the file even while it stays over the limit. [verified: `docs/cost-levers-considered.md:340`]
17. The result goes in a new dated entry right after `docs/cost-levers-considered.md:348`, the end of the "From `trim-global-claude-md.md`" section's second-pass table. Earlier rows stay untouched because they are dated records. [verified: cost-levers-considered.md:317-349]
18. `select-tests.py` maps `claude/.claude/CLAUDE.md` to both the hooks and skills test directories, so row 7's pins run (`select-tests.py:488`). `docs/` is covered by its docs-directory rule. [verified]
19. The engineer has further ideas for cutting CLAUDE.md. Nothing this PR writes may say the file cannot be cut further. "KEEP" verdicts above apply to this pass's relocation bar only. [engineer-verified]

**Mechanisms:**
- M1: delete B, D, and F from CLAUDE.md:20 and leave no pointer. anchors: row4, row5, row6, row7, row8
- M2: leave every other candidate byte-for-byte unchanged. anchors: row3, row9, row10, row11, row12, row13, row14, row15
- M3: record the measured bytes and the reasons each candidate stays as a third-pass entry. anchors: row17
- M4: one `code-writer` dispatch that edits CLAUDE.md, measures, then writes the entry, because the entry's figures depend on the edit. anchors: row17

No mechanism here is heavier than the task needs. Deleting text and appending a record are the lightest options available, and no hook, rule file, or skill edit is added.

## Critical files

1. **`claude/.claude/CLAUDE.md`, line 20 only.** Replace the whole bullet with exactly this text:

   ```markdown
   - Never write `<config-dir>/*-markers/*` by hand, regardless of account. Gates match on a marker's **content** — a hash of the exact state that was reviewed — not on the file's presence: once that state changes the stored hash stops matching and the gate denies until a fresh review is recorded, while a review still covering the current state keeps counting across sessions. Every denial names both the operation it blocked and the review skill to run — run that skill; if it is harness-blocked, delegate it to a `general-purpose` subagent, which carries the `Skill` tool. A general "ship it" instruction is not authorization to forge a marker.
   ```

   - Estimated cut: B about 165 bytes, D about 68, F about 188, total about −421 bytes. These are hand-counted; Verification measures the real figure.
   - The line count stays at 183. No heading changes and no pointer is added.
   - All five pinned phrases survive.

2. **`docs/cost-levers-considered.md`: insert after line 348**, before `## From \`disable-artifact-workflow-default.md\``. Fill each `<…>` from the Verification step 2 commands. Do not use any `§` character in this text.

   ```markdown
   **Third pass: `relocate-global-claude-md.md` (<commit date>).** This pass relocated instead of compressing. A passage left the global file only if another surface already delivers its behavior when the rule applies, in every stow consumer session. The file measured <L0> lines / <B0> bytes at this branch's merge-base (`git show <merge-base-sha>:claude/.claude/CLAUDE.md | wc -lc`) and landed at <L1> lines / <B1> bytes, <B1 − 25600> bytes over the ratchet.

   | Lever | Verdict | Measured reason |
   |---|---|---|
   | Relocate always-loaded content whose firing event another surface already covers | Adopted | <B0 − B1> bytes cut, all from the Safety section's marker bullet:<br>- Two sentences restated marker mechanics that `docs/hooks.md` documents.<br>- One sentence restated the guidance `enforce-marker-script-shape.sh` appends to every subagent marker-write denial.<br><br>Five candidates did not meet this pass's relocation bar and stay for now:<br>- No-op-dispatch bullet: `deny-no-op-dispatch.sh` reaches only short prompts matching a closed idiom list, and [`design-decisions/no-op-dispatch-hook-gate.md`](design-decisions/no-op-dispatch-hook-gate.md) keeps the bullet as the primary surface.<br>- `isolation: "worktree"` bullet: no other surface carries it, and `branch-management` cites it as canonical.<br>- Anchor-hold bullet: its preventive action fires while the session composes a dispatch, before `branch-management`'s copy can load.<br>- "Name every new package" bullet: `docs/security-hardening.md`'s manifest-edit disclosure section records it as the only layer covering a bare restore.<br>- The rest of the marker bullet: `test_skills.py` pins it, and no hook stops a main-session marker forge.<br><br>Content outside this pass's scope, such as Model & Effort Routing and the delegation-decision bullets, stays open for later passes using other levers. |
   ```

3. **`.claude/plans/relocate-global-claude-md.md`:** this plan, committed per `plan-it` Step 7.

**Reuse:**
- The measurement matches the second-pass entry's own form (`cost-levers-considered.md:333,342`).
- `docs/hooks.md`, `enforce-marker-script-shape.sh`, and every citing hook, test, and skill stay unedited.
- `plugins/skill-management` is untouched, so no version bump is needed.

**Dispatch split:** one `code-writer` dispatch covering items 1 and 2, in order:
1. Edit CLAUDE.md.
2. Run the Verification step 2 measurements.
3. Write the entry using those exact figures.

The dispatch's verification command is `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. The parent runs `/code-review`, including the audit below, and makes the commit, since `code-writer` does not commit.

## Verification

1. Run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`. It must pass, and its selection must include both the skills tests (`test_skills.py`'s `TestGlobalInstructionsDescribeMarkerGatesAsContentAddressed`) and the hooks tests (`test_check_claude_md_length.py`, `test_output_preferences_layering.py`, `test_doc_counts.py`). If the skills directory is missing from the selection, that is a bug in `select-tests.py`'s mapping table to report, not a reason to widen the run.
2. Measure. Each command is a single statement with no nested `$(...)`:
   - `git merge-base HEAD origin/main` gives `<sha>`.
   - `git show <sha>:claude/.claude/CLAUDE.md | wc -lc`
   - `wc -lc claude/.claude/CLAUDE.md`

   Expect the same line count and a drop of about 421 bytes (±10). Every number in the cost-levers entry must match these outputs exactly.
3. Run `git grep -n -e "writes its own marker directory" -e "guarded operation varies" -e "reports the denial and the dispatching session resolves" -- claude claude-skills docs plugins README.md`. It should return no matches.
4. `git diff --stat` against the merge-base should list only the three Critical files.
5. During `/code-review`, fill the compression-diff audit from `ai-instruction-and-memory-files` §1. Every row must be Y:

   | Removed/shortened text | Surviving line | Behavior-preserving? |
   |---|---|---|
   | "Each review skill writes its own marker directory (…) when a review passes." | CLAUDE.md:20 | Y — "Never write `<config-dir>/*-markers/*` by hand" / "run that skill"; mechanics at docs/hooks.md:103-107 |
   | "The guarded operation varies by skill and is not always the commit." | CLAUDE.md:20 | Y — "Every denial names both the operation it blocked and the review skill to run" |
   | "`code-writer` and the reviewer agents cannot run review skills … the dispatching session resolves it." | CLAUDE.md:20, :117; enforce-marker-script-shape.sh:155 | Y — "delegate it to a `general-purpose` subagent, which carries the `Skill` tool"; "Report the denial verbatim to whoever dispatched you, name what you could not reach, and stop"; hook: "Report the denial to the dispatching session instead…" |

6. The commit should pass `check-claude-md-length.sh`: the file is still over the limit but smaller than the version before it (row 16).
7. The PR body references GH-885 with `Refs #885`, not a closing keyword. The file still sits over 25,600 bytes, and the engineer plans further cuts (row 19), so the issue stays open.

## Out of scope

- **Raising `GLOBAL_CLAUDE_MD_BYTE_LIMIT`, or moving Model & Effort Routing or the delegation-decision bullets:** the engineer ruled these out.
- **Compressing in place**, such as merging :107 into :116 or deleting `branch-management/SKILL.md:89-94`'s copy of :118. The engineer limited scope to relocation, and the skill edit would also pull in `/skill-review`.
- **New enforcement that would make more relocations safe later.** Examples are the narrow `PreToolUse`-on-`Agent` validation hook listed in `declined-sessionstart-additionalcontext-injection.md`, or a "report this denial to your dispatcher" clause in `require-plan-review.sh`'s Write/Edit denial. Each adds a surface instead of moving text to an existing one.
- **Editing earlier records:** the `.claude/plans/` files Step 3 cited, and `docs/cost-levers-considered.md:333-348`, including its line-342 note that the trim was tracked in GH-885.
