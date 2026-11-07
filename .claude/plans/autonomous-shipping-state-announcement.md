# Resolve autonomous-shipping state in code and state it to agents

## Context

Goal: stop agents from asking the engineer for permission to commit, push, or open a PR on machines where autonomous shipping is active, by having code resolve and state that fact instead of relying on the agent to recall and run a check.

Ask: "I'd combine them. I think you're indexing on the file fix going out faster. And I think this should all be fixed together. I think what you're going to find is it's part of a comprehensive whole. And I'd rather do it the right way. I don't want to just implement patches on patches." On CLAUDE.md: "we got to be very careful about what we add there, because again, that's loaded into every session, and there's already a lot on autonomous shipping. I think there might be some on context management as well. I'm open to it, but suspicious that that's the right altitude." On the Stop-hook miss: "yeah let's investigate the stop-hook miss as well"

Why now: a main interactive session, right after a compaction, committed locally and then ended its turn asking whether to push, without running `autonomous-shipping-active.sh` (which exits 0 on this machine). Three independent contributors were found, all in this repository's own tooling:

- The Stop hook that is meant to catch this stall (`advance-past-commit-stall.sh`) reads `last_assistant_message` with a single `IFS= read -r`, so a multi-line final turn is truncated to its first line and a closing question on a later line never reaches the fire predicate. Reproduced in isolation; the hook's test file has no multi-line case.
- The post-compaction SessionStart hook (`restore-authorization-boundary-on-compact.sh`) tells the agent the compaction summary is not engineer authorization and that actions with effects outside the repository need in-session confirmation. A session read that as covering a plain push or `gh pr create`. It states nothing about the resolved shipping state.
- No SessionStart hook announces the resolved shipping state on startup, resume, or clear. `CLAUDE.md`'s Shipping bullet is the only proactive rule, and it asks the agent to run the resolver script itself.

Intended outcome: one shared resolver-backed statement of the shipping state, delivered to the agent by every hook surface that currently shapes its commit/push/PR behavior, with the Stop hook repaired as the reactive backstop, and with any `CLAUDE.md` change justified against that file's load-every-session cost.

Out of scope for hooks: a prior engineer answer scoped to one branch being reused for another branch.

## Approach

One `_lib.sh` function, `_lib_autonomous_shipping_statement REPO_ROOT`, becomes the only home of the agent-facing text that states the resolved shipping state. It prints that text only when `_lib_autonomous_shipping_active` holds, and three hooks deliver it: a new `announce-autonomous-shipping.sh` on SessionStart `startup|resume|clear`, the existing compact hook inside its one message, and the Stop hook's block reason. The Stop hook also reads the whole multi-line final message and ends a sentence at a line break. `handoff` and `brief` each gain one exception sentence, so a continuity file stops filing a plain commit, push or PR under pending authorization where shipping is active. `CLAUDE.md` is not edited, and no config key is added.

**The statement.** The helper prints this text with no trailing newline:

```text
Autonomous shipping is active for this repository, as resolved by the same check `~/.claude/scripts/autonomous-shipping-active.sh` runs, when this message was generated. In this repository, the engineer's request to make a change is their authorization to commit it, push the current branch to its own remote branch, and open a PR for it, so do those without asking. This outranks a generic "not authorized" line in a compaction summary, handoff or brief file, or authorization reminder. It does not outrank a specific hold the engineer gave, such as "don't push until I check", even when that hold survives only as summary text, and an instruction the engineer gives in this session still wins. It covers only committing, a plain push of the current branch (never to the default branch), and opening a PR: merging stays human-only, and force-pushing and every other irreversible action still need the engineer's in-session confirmation. In a different repository, run that script there before relying on this.
```

The text restates `CLAUDE.md` § "Shipping" on purpose. Hook context has to stand alone after compaction, where re-injection of user-level `CLAUDE.md` is unconfirmed (row 11). That is the "instructional prose that must stand alone" exception to single source of truth (`claude/.claude/CLAUDE.md:25`). The text names the script as its source and does not tell the agent to skip `CLAUDE.md`'s verify step. An agent that follows the step runs the script once and gets exit 0. The failure fixed here is an agent that never thought to check and asked instead. The statement never contains the literal `gh pr merge`, matching the compact hook's pinned invariant (row 9).

**Per surface.**

- **`announce-autonomous-shipping.sh`** (new, `# hook-class: informational`, modeled on `restore-authorization-boundary-on-compact.sh`). It runs these steps:
  1. Source `_lib.sh` and read stdin.
  2. Stop unless `_lib_autonomous_shipping_sentinel_present` holds. Non-adopting consumers then spawn no jq or git.
  3. Stop unless `.source` is a string equal to `startup`, `resume` or `clear`. `compact` belongs to the compact hook. `fork` and unknown sources stay silent, because a fork does not ship (`claude/.claude/CLAUDE.md:85`).
  4. Stop unless the payload's `.cwd` is a non-empty absolute string. There is no `$PWD` fallback, because a grant must fail closed. Resolve the repo root as `$(cd -- "$PAYLOAD_CWD" 2>/dev/null && _lib_repo_root)`. Stop when the root is empty or equals `$HOME`, as `bootstrap-project-plugins.sh` does (R6 of the CISO review).
  5. Emit the helper's output as SessionStart `additionalContext`, using the compact hook's `_lib_jq -n --arg ctx` shape.

  Every other path exits 0 silently.
- **`restore-authorization-boundary-on-compact.sh`.** The message is built from two independent parts and emitted when either part is non-empty.
  - The boundary text is included when `authorization_boundary_restore` resolves 0, 3 or 4, as today.
  - The statement is appended after one space when the sentinel fast path passes and the helper prints for the root resolved from `.cwd`, with the same absolute-`.cwd` and `$HOME` rules as the new hook. A shared root-resolution helper is not extracted: two callers resolve it in two lines each.
  - The kill switch therefore silences only the boundary text. The statement sits in the same message as the outside-repository clause it overrides.
- **`advance-past-commit-stall.sh`.**
  - **Capture.** The sixth read becomes `IFS= read -r -d '' LAST_ASSISTANT_MESSAGE`, which reads the rest of jq's output to EOF (row 8). Strip trailing whitespace (`"${msg%"${msg##*[![:space:]]}"}"`) before the emptiness check.
  - **Boundary.** `last_line="${LAST_ASSISTANT_MESSAGE##*$'\n'}"`, then `last_sentence="${last_line##*[.?!][[:space:]]}"`. The line step is what ends a sentence at an unpunctuated line break, which the widened split alone does not (SDET R1). The trailing-whitespace strip guarantees the last line is non-empty.
  - **Cost.** `##*$'\n'` over the whole message may be quadratic in bash. Verification step 6 times it on a large message before the work ships.
  - **Step 9.** It becomes `SHIPPING_STATEMENT=$(_lib_autonomous_shipping_statement "$REPO_ROOT") || exit 0`. `REASON` is the statement, one space, then: "Continue now: run /code-review, commit with path-scoped staging (never stage-all), run /ready-for-review, and open the PR. If you are genuinely blocked (a failing test you cannot fix, a design ambiguity with no defensible default), say what is blocked instead of asking permission to proceed with work that is already done. To disable this on this machine: touch ~/.claude/.commit-stall-block-disabled. To disable it for this repo: add .claude/autonomous-shipping-optout."
  - The old opening sentence and "Stop before merge — that stays human-only." are dropped, because the statement carries both. "For the rest of this session" becomes "on this machine", because the file persists across sessions.
  - **Comments.** Two one-line comments: "last_assistant_message stays the last field and is read to EOF, because it can span lines." and "8. Fire predicate on the final sentence: the last sentence of the last non-blank line." In the comments this diff touches, replace the stale `nudge-handoff-near-context-cap.sh:29-49` and `_lib.sh:193-200` citations with function names.
- **`handoff/SKILL.md` and `brief/SKILL.md`.** Append one sentence, on its existing line, to the "Underlying principle" paragraph (handoff `:118`, brief `:91`). For handoff:

> Exception: a commit, a plain `git push` of the current branch (never `--force`), and `gh pr create` belong in §3, not here, when `~/.claude/scripts/autonomous-shipping-active.sh` exits 0 in the resuming session's repository and the engineer has stated no hold on them.

  For brief the same sentence reads "belong in §6, not here". Handoff's pre-write checklist line `:177` restates the principle in a parenthetical that omits the exception. Replace that parenthetical with "(§3.5's, including its exception)". Keep the pinned phrases "can still belong in §3.5" and "not the underlying principle" (row 17). Every edit stays on an existing line, so handoff stays at its 200-line cap and brief stays at 190 lines (row 15).

**Stop-hook siblings need no change.** The other jq-fed `IFS= read -r` chains read only ids, enums and paths (row 7). `last_assistant_message` is the only free-text field read that way.

**`CLAUDE.md`: no edit, with the byte arithmetic.**
- The cap is 30,720 bytes and 200 lines. The file is reported at 30,640 bytes and 180 lines, which leaves 80 bytes (row 12).
- The smallest edit that would make the hook statement an authority beside the script is a clause such as ", or a SessionStart hook statement for this repo," on the line-177 verify bullet. That is about 50 bytes, most of the headroom, in a file every stow consumer loads every session.
- It also would not reach the incident's case, because re-injection of user-level `CLAUDE.md` after compaction is unconfirmed (row 11).
- The rule is already in `CLAUDE.md`. What failed was recall, and hook context fixes recall with no new always-loaded prose. This follows the engineer's caution (row 3).

**Alternatives weighed.**
- **A `CLAUDE.md` addition.** Rejected above.
- **Compact hook plus Stop hook only, no startup announcement.** Rejected: `CLAUDE.md`'s recall-based rule is the failure on startup, resume and `/clear` too, a resumed session reads the same handoff or brief line, and the engineer proposed "a session start hook check all the config keys" (row 31). The incident observed was post-compaction, so a session seen asking at startup with the statement present is a revisit condition in the design record.
- **Fold the statement into `session-marker-dashboard.sh`, which already fires on all four sources.** Rejected: it mixes marker state with authorization. After compaction, the statement would also land in a second message beside the boundary text it contradicts.
- **Widen the compact hook's matcher to every source.** Rejected: its name, its "non-compact sources stay silent" test contract and its kill switch all assume compaction, and the switch would then silence the statement too.
- **A new hook on all four sources, with the compact hook untouched.** Rejected: after compaction the agent would get one message saying an outside-repository effect needs confirmation and a second message overriding it. A second layer that corrects the first is the shape the engineer ruled out (row 1).
- **`UserPromptSubmit` on every turn.** Rejected: always fresh, but it re-injects about 700 bytes on every prompt.
- **`PostToolUse` after `git commit`.** Rejected: it lands at the right moment for a push question, but it misses the stall that comes before any commit ("Want me to commit?") and it needs Bash command parsing.
- **A new config key.** Rejected: the statement's natural switches are `autonomous_shipping` itself and the repo optout. A separate key would add a schema row and docs for a state nobody needs, shipping on but unannounced.
- **A 0x1f-joined read for the Stop capture**, the `_lib_parse_tool_input_or_deny` precedent (`_lib.sh:526-552`). Rejected: it handles a newline in any field, but it adds invisible delimiter bytes and an overflow check for one free-text field that can simply be read last.
- **A separate `_lib_jq` call for the message.** Rejected: it is correct, but it adds a process to every Stop in an opted-in session.

### Assumption ledger

**Root:** On a machine where autonomous shipping is active, a main session can end its turn asking permission to commit, push or open a PR. Three causes compound:
- No code-resolved statement of the shipping state reaches the agent. Acting on the state depends on the agent recalling `CLAUDE.md`'s instruction to run a script (rows 12, 13).
- After compaction, and in handoff and brief files, the authorization text covers anything with effects outside the repository, which reads as covering a plain push or PR (rows 9, 15).
- The Stop hook meant to catch the resulting question misses multi-line final messages (rows 5, 6).

**Givens:**
- G1. SessionStart fires with `source` set to `startup`, `resume`, `clear` or `compact`, and its `additionalContext` enters the agent's context. Reason: this is Claude Code's hook contract, which the vendor owns (row 10).
- G2. The compaction summary's content, including any "not authorized" line it carries forward, is harness-generated. Reason: the vendor owns the summary template, so this repo can only add context beside it.
- G3. Hook-injected context is advisory, so the model may still ask. Reason: model behavior is outside this repo's control (row 21).
- G4. The Stop payload's `last_assistant_message` holds the whole final message, newlines included. Reason: the vendor owns the payload shape (row 5).

**Mechanisms:**
- **M1.** Add `_lib_autonomous_shipping_statement REPO_ROOT` to `_lib.sh`, directly after `_lib_autonomous_shipping_active` (`:2982`).
  - Wrong arity returns 1. Then `_lib_autonomous_shipping_active "$1" || return 1`, then `printf '%s'` the statement.
  - Header comment, one line: it prints the statement and returns 0 only when `_lib_autonomous_shipping_active` holds, and otherwise prints nothing and returns 1.
  - The single-quoted text holds literal backticks. It therefore carries `# shellcheck disable=SC2016` with the one-line reason the compact hook uses at `:54`.
  - Check and text sit in one function, so no caller can print the statement without the check passing.
  - Three hooks call it, which puts it in `_lib.sh` under the seams rule (row 18).

  Lighter primitives checked:
  - **A constant string in each hook:** three copies of the scope clause drift apart.
  - **A `--statement` flag on `autonomous-shipping-active.sh`:** each hook would spawn a script that re-sources `_lib.sh`, and that script is the agent's surface, not the hooks'.

  anchors: root, row13, row18
- **M2.** Add the SessionStart hook `announce-autonomous-shipping.sh` with matcher `startup|resume|clear`. Register it in `claude/.claude/settings.json` as its own group, directly before the compact hook's group. "Alternatives weighed" gives the lighter options and why each fails. anchors: root, row10, row11, row12, row27
- **M3.** The compact hook builds one message from the boundary text and the statement, and its kill switch covers only the boundary text. anchors: root, row1, row9
- **M4.** The Stop capture reads its final field to EOF with `read -r -d ''`. anchors: row5, row8
- **M5.** The Stop boundary takes the last sentence of the last non-blank line. anchors: row6, row23
- **M6.** The Stop reason opens with the shared statement. anchors: root, row13
- **M7.** One exception sentence goes into `handoff` §3.5 and `brief` §6.5, and the handoff checklist parenthetical defers to §3.5. anchors: row2, row15, row16, row17, row28
- **M8.** No `CLAUDE.md` edit and no new config key. anchors: row3, row11, row12
- **M9.** The sibling read chains stay as they are. anchors: row7
- **M10.** Docs and one design record follow the new behavior (Critical files lists each). anchors: root, row6, row12, row20

**Rows:**
1. The Stop-hook repair, the state announcement and the continuity-file change ship as one change designed as one whole. [engineer-verified: "I think this should all be fixed together. I think what you're going to find is it's part of a comprehensive whole. And I'd rather do it the right way. I don't want to just implement patches on patches."]
2. This PR edits the handoff skill. The label answered the question whether this PR should change the handoff skill so that a handoff stops writing a blanket "push and PR are not authorized" line where autonomous shipping is active. [engineer-verified: "Also edit handoff"]
3. `CLAUDE.md` additions face a high bar. The engineer stated caution, not a decision against any edit. [engineer-verified: "we got to be very careful about what we add there, because again, that's loaded into every session, and there's already a lot on autonomous shipping. I think there might be some on context management as well. I'm open to it, but suspicious that that's the right altitude."]
4. The Stop-hook miss is in scope. [engineer-verified: "yeah let's investigate the stop-hook miss as well"]
5. The Stop hook reads six fields with `IFS= read -r`, and `last_assistant_message` is the sixth, so only its first line survives. [verified: `advance-past-commit-stall.sh:88-99`] The incident's isolated reproduction is [unverified]: the Context reports it, and the architect has no Bash to re-run it.
6. The boundary defect and the documented contract:
   - `${LAST_ASSISTANT_MESSAGE##*[.?!] }` splits only where a space follows the punctuation. [verified: `advance-past-commit-stall.sh:142`]
   - The docs promise a final-sentence-only window. [verified: `docs/commit-stall-block.md:20, 50-53`]
   - No `CORPUS` message contains a newline. [verified: `test_advance_past_commit_stall.py:155-174`]
7. The sibling jq-fed read chains read only ids, enums and paths:
   - `activate-handoff-bypass.sh:33` reads skill, agent_type and session_id. [verified: Grep]
   - `nudge-error-mode-analysis.sh:80` reads session_id, agent_type, permission_mode and transcript_path. [verified: Grep]
   - `nudge-worktree-anchor.sh:83` reads session_id and cwd. [verified: Grep]
   - `nudge-unexpanded-skill-mention.sh:94` reads cwd and permission_mode. [verified: Grep]
   - `nudge-handoff-near-context-cap.sh` and `nudge-long-turn-subagent.sh` read no free text either. [unverified: discovery report; not opened]
8. `IFS= read -r -d ''` assigns everything up to EOF and returns nonzero there, which the group's existing `|| true` absorbs. [verified: `_lib.sh:532-545`]
9. The compact hook and its test contract:
   - Its text's clause "has effects observable outside this repository" literally covers a push. [verified: `restore-authorization-boundary-on-compact.sh:55`]
   - Its tests pin "not engineer authorization", five tokens, the absence of `gh pr merge`, and silence for non-compact sources. [verified: `test_restore_authorization_boundary_on_compact.py:34-40, 78, 147, 164-170`]
10. Source values, and the payload `.cwd` the hooks read:
    - The source values match the registered matcher `startup|clear|compact|resume`. [verified: `claude/.claude/settings.json:137`]
    - Injection after compaction is cited to the vendor hooks docs. [verified: `.claude/plans/post-compact-authorization-boundary.md` row 1]
    - SessionStart payloads carry `.cwd`. [verified: `docs/hooks.md:234` documents `set-session-title-from-branch.sh` reading it] Not checked against the vendor docs.
11. Re-injection of user-level `CLAUDE.md` after compaction is unconfirmed. [unverified: carried from `.claude/plans/post-compact-authorization-boundary.md` row 4, which rated it unconfirmed]
12. `CLAUDE.md`'s size and Shipping text:
    - The caps are 30,720 bytes and 200 lines. [verified: `check-claude-md-length.sh:59, 123`]
    - Current size is 30,640 bytes and 180 lines. [unverified: discovery report; Verification step 5 re-measures]
    - The Shipping bullets require the script "in the current turn". [verified: `claude/.claude/CLAUDE.md:176-177`]
13. The resolver: `_lib_autonomous_shipping_active` requires the machine sentinel and the absence of `<repo>/.claude/autonomous-shipping-optout`, and the CLI wraps it. [verified: `_lib.sh:2959-2989`, `autonomous-shipping-active.sh:11-16`]
14. The optout is a committed repo marker, so linked worktrees share it. [verified: `.claude/plans/sentinel-config-migration.md:278`; `README.md:370-374` shows creating the file but not committing it] An untracked optout present in only one worktree is [unverified] and stays a known gap.
15. Skill sizes and edit sites:
    - `handoff/SKILL.md` ends at line 200, its cap. [verified: Read; `check-skill-length.sh:139-140` default 200]
    - `brief/SKILL.md` ends at line 190. [verified: Read]
    - The principle lines are handoff `:118` and brief `:91`. [verified: Read]
    - Brief's own example already files "Push, request re-review" under §6. [verified: `brief/SKILL.md:179`]
16. `check-handoff.py`'s §3.5 anchor shapes do not include a plain `git push`. [verified: `check-handoff.py:31-38`]
17. `test_skills.py` pins "can still belong in §3.5" and "not the underlying principle". [verified: `test_skills.py:1937-1938`]
18. Hook layout rules:
    - The gate filename prefixes do not include `announce-`. [verified: `test_hook_alignment.py:143-150`]
    - Every main-directory hook needs a line-start `- **`name`**` bullet. [verified: `test_hook_alignment.py:195`]
    - A helper two or more hooks need goes in `_lib.sh`, and a new hooks-side `_*-lib.sh` fails CI. [verified: `.claude/rules/bash-unit-test-seams.md`]
    - The repo-local `.claude/settings.json` registers no SessionStart hooks. [verified: Grep, no matches]
19. Test fixtures:
    - `isolated_home` sets `HOME` and deletes `CLAUDE_CONFIG_DIR`. [verified: `claude/.claude/hooks/tests/conftest.py:474-483`]
    - The Stop tests arm shipping by writing the legacy sentinel file. [verified: `test_advance_past_commit_stall.py:58-63`]
    - The existing compact tests therefore stay unarmed, and the fast path keeps them git-free.
20. Where `~/.claude/hooks` is a real directory, a new hook file is linked only after a re-stow, while `settings.json` already registers it. [verified: `CHANGELOG.md:525` carries the standard notice]
21. Agents heed the statement. [unverified: advisory context; G3]
22. Whether SessionStart sends `source: "fork"`, and whether its payload carries `agent_type` for a `claude --agent` session. [unverified] The source allow-list handles the first. The second stays a documented gap.
23. Bash expands `${var##*$'\n'}` as a newline pattern on the 5.2 floor. [unverified: bash semantics; the new `CORPUS` rows pin it]
24. A second `autonomous_shipping` lookup in one process is a memo hit, so the fast path plus the helper's own check costs one resolution. [verified: `docs/config-file.md:316`]
25. The peer branch GH-1397 also edits `CHANGELOG.md` [Unreleased]. [unverified: dispatch relay]
26. The option description shown with "Also edit handoff" was the session's own text, not the engineer's view. [unverified as the engineer's view] Its one load-bearing fact, that editing a SKILL.md brings in skill review, holds independently. [verified: `.claude/rules/review-pipeline-dispatch.md`, `require-skill-review.sh` enforcement]
27. A fork returns its work to its dispatcher and does not ship. [verified: `claude/.claude/CLAUDE.md:85`]
28. `brief` carries handoff's §3/§3.5 split and the identical principle wording. [verified: `brief/SKILL.md:75-91`] The engineer first answered only about handoff, then was asked whether to include brief. [engineer-verified: "Include brief (Recommended)"] The "(Recommended)" suffix is part of the label the engineer selected, not their own wording.
29. The design-decision record stays in this PR. The engineer selected the label for the question whether it belongs, among the Ask-vs-deliverables extras. [engineer-verified: "Design-decision record"]
30. The README sentence stays in this PR, selected in the same question. [engineer-verified: "README sentence"]
31. The engineer proposed a session-start hook in their first message. [engineer-verified: "a session start hook check all the config keys"] The quote states the proposal, not a decision on the hook's matcher or on which keys it covers.
32. Review-round corrections, each checked by the reviewer named:
    - The statement's precedence clause had to carve out an engineer's specific hold, drop "linked worktrees", bound the push, and fail closed on a missing `.cwd`. [verified: ciso-reviewer findings R1, R2, R4, R6]
    - The statement is 855 bytes. [verified: platform-engineer finding R9]
    - `${var##*$'\n'}` in a double-quoted assignment already ships at `_lib.sh:1857`, and the trailing-whitespace strip idiom at `_config.sh:152`. [verified: platform-engineer finding; replaces row 23's unverified tag]
    - The Stop hook sets neither `set -e` nor `pipefail`, and the `|| true` at `:99` binds to the whole brace group. [verified: platform-engineer finding]
    - `check-handoff.py`'s §3.5 anchors span `:31-46`. [verified: platform-engineer finding; row 16's range]
    - `README.md:73` and `:508` describe the compact hook's opt-out and become false under the two-part message. [verified: platform-engineer finding R4]
    - The widened `[.?!][[:space:]]` split alone already ends a sentence at ".\n", so only a row with an unpunctuated line before the ask pins the line step. [verified: sdet finding R1, bash probe]

## Critical files

**Dispatch A, `code-writer`: library, hooks, tests and docs.** It runs in parallel with Dispatch B, because their file sets are disjoint. Both share the parent's feature worktree and git index, so neither dispatch stages or commits. The parent commits with path-scoped staging after both return.

- `claude/.claude/hooks/_lib.sh` — add `_lib_autonomous_shipping_statement` (M1). Reuse `_lib_autonomous_shipping_active` (`:2982`).
- `claude/.claude/hooks/announce-autonomous-shipping.sh` — new (see Approach). Reuse `_lib_jq`, `_lib_repo_root` (`:604`), `_lib_autonomous_shipping_sentinel_present` (`:2959`) and the compact hook's emit shape (`:58-59`). Header lines, one fact each:
  - The event and matcher.
  - The settings matcher plus the in-script `.source` filter, per the repo's hook defense-in-depth rule.
  - Compaction is covered by `restore-authorization-boundary-on-compact.sh`.
  - Exit 0 always.
- `claude/.claude/hooks/restore-authorization-boundary-on-compact.sh` — two-part message (M3). Rewrite the header's kill-switch line and the `:39-46` exit-code comment as one-line facts:
  - Exit 1 or 2 skips the boundary text.
  - Exit 3 or 4 keeps it.
  - The statement follows `autonomous_shipping`.
- `claude/.claude/hooks/advance-past-commit-stall.sh` — capture, boundary, step 9 and `REASON` (M4–M6).
- `claude/.claude/settings.json` — register the new hook with `"matcher": "startup|resume|clear"`, directly before the `compact` group at `:182`.
- `claude/.claude/hooks/tests/test_lib_config_gates.py` — add `TestAutonomousShippingStatement`, modeled on `TestAutonomousShippingActive`, `:418-437`:
  - Armed with a repo: rc 0, and stdout carries "Autonomous shipping is active", "autonomous-shipping-active.sh", "without asking", "outranks", "specific hold", "never to the default branch", "merging stays human-only" and "force-pushing", and no `gh pr merge`.
  - Unarmed, armed with an optout, and with zero, empty or two arguments: rc 1 and empty stdout.
- `claude/.claude/hooks/tests/test_announce_autonomous_shipping.py` — new. Reuse `isolated_home` and `init_git_repo_with_commit`, and copy `armed_home`'s shape. Cases:
  - Armed, `startup`/`resume`/`clear`, payload `cwd` set to a repo while the process cwd is a non-repo tmp dir: valid SessionStart JSON whose `additionalContext` equals the helper's output.
  - Armed with `compact`, `fork`, a missing source or a non-string source: silent.
  - Unarmed, optout, or `cwd` outside a repo: silent. The outside-repo case runs with the process cwd inside an armed repo, so a `$PWD` fallback would fail it.
  - Missing, empty, non-string or relative `.cwd`: silent. `.cwd` resolving to a repo root equal to `$HOME`: silent.
  - A subdirectory `.cwd` and a linked-worktree `.cwd`: both emit.
  - Empty or malformed stdin, missing `jq`, or an unresolvable config dir: exit 0 and silent, in an armed environment so the case is not vacuous.
  - Non-adopting consumer (unarmed): no `git` or `jq` process runs. Assert with a PATH shim that records invocations.
  - `claude/.claude/settings.json` registers the hook once, with a matcher whose source set is disjoint from the compact group's.
- `claude/.claude/hooks/tests/test_restore_authorization_boundary_on_compact.py` — add these cases (existing tests stay unarmed and unchanged):
  - Armed `compact` with a repo `cwd`: the context holds "not engineer authorization" and "outranks", and still no `gh pr merge`.
  - Armed with the kill switch set: the context equals the helper's output exactly, with no boundary text.
  - Armed with an optout, a non-repo `cwd` or a missing `.cwd`: the context is the boundary text alone.
  - Kill switch set and statement part empty: silent.
  - Single-source pin: for the announce hook, the compact hook and the Stop hook, the emitted text contains the helper's output byte for byte, obtained by sourcing `_lib.sh` in the test. A hook holding an inlined copy fails.
  - Armed non-compact sources: still silent, so the new hook is the only emitter there.
- `claude/.claude/hooks/tests/test_advance_past_commit_stall.py` — add `CORPUS` rows, plus one fire test asserting the reason holds "merging stays human-only", "/code-review" and "/ready-for-review". The rows:
  - "Fixed the failing test\nWant me to commit this?" → fires. The earlier line has no punctuation and carries an exclusion word, so only the line step passes it.
  - "Fixed the failing test.\n\nWant me to commit this?" → fires (capture, and an exclusion word in an earlier paragraph).
  - A heading line, two bullet lines, a blank line, then "Should I push and open a PR?" → fires.
  - "Want me to commit this?\n" → fires (trailing newline).
  - "The tests are failing.\nWant me to commit anyway?" → silent ("anyway" in the final sentence).
  - "Want me to commit this?\n\nHere is the diff summary." → silent (the last line carries no ask).
  - The reason test also asserts the reason contains the helper's output byte for byte (single-source pin).
- `claude/.claude/hooks/tests/conftest.py` — move `armed_home` here from `test_advance_past_commit_stall.py:58-63`, so the Stop, compact and announce tests share one fixture (`test-conventions` §6). The Stop test file imports it from conftest instead.
- `claude/.claude/hooks/tests/config-schema-audit.md` — update the call-site bullet of the `autonomous_shipping` entry to name `_lib_autonomous_shipping_statement` and its three callers by name, and drop the stale `:67`. Update the call site and fail direction of the `authorization_boundary_restore` entry to the two-part message.
- `docs/hooks.md` — add a `- **`announce-autonomous-shipping.sh`**` bullet under Utility hooks, next to the compact hook's bullet. It covers:
  - What the hook emits and when.
  - That compaction belongs to the compact hook.
  - That it has no kill switch of its own: `autonomous_shipping` and the repo optout silence it.
  - Known gaps: a `claude --agent` main session receives the statement while the Stop hook skips it, and an untracked optout in a single worktree is missed.

  Update the compact hook's bullet at `:232` to say it carries the statement when shipping is active and that its kill switch silences only the boundary text.
- `docs/commit-stall-block.md`:
  - `:20`: define the final sentence as the last sentence of the last non-blank line.
  - `:24`: the reason now opens with the shared statement.
  - The `:48-53` tradeoff section: add that a line break ends a sentence.
  - `:69`: the silent-stall limitation now names the SessionStart statement beside `CLAUDE.md` as context-only defenses.
- `README.md`:
  - In "Autonomous shipping" (`:360`), add one sentence. It says that on an active machine, `announce-autonomous-shipping.sh` states the resolved state at startup, resume and `/clear`, and that the compact hook repeats it after compaction.
  - At `:73` and `:508`, correct the compact hook's opt-out description: its kill switch silences only the boundary text.
- `docs/design-decisions/autonomous-shipping-state-in-hook-context.md` — new. It carries the H1 title and the italic provenance line `*2026-11-05.*`, and it covers:
  - The decision, citing `claude/.claude/CLAUDE.md` § "Shipping" rather than restating it.
  - Why hook context instead of `CLAUDE.md` prose (rows 11, 12).
  - Why one helper with three callers, and why compaction composes one message.
  - The rejected alternatives above.
  - Why the continuity-file exception.
  - Revisit conditions: a session observed asking with the statement present, or vendor confirmation that user `CLAUDE.md` is re-injected after compaction.

  Describe the triggering incident generically, with no project identifiers.
- `CHANGELOG.md`, in the first `[Unreleased]` block (its `### Added`, `### Changed` and `### Fixed` headings, not the second `### Changed` further down):
  - Added: the new hook, with the standard re-stow notice (row 20).
  - Changed: the compact hook's two-part message and kill-switch scope; the handoff and brief exception.
  - Fixed: the Stop hook's multi-line miss.

  Expect a trivial rebase against GH-1397 (row 25).

**Dispatch B, `code-writer`: continuity skills.**
- `claude-skills/skills/handoff/SKILL.md` — the `:118` exception sentence and the `:177` parenthetical. No line is added.
- `claude-skills/skills/brief/SKILL.md` — the `:91` exception sentence. No line is added.
- `claude-skills/skills/tests/test_skills.py` — in `TestContinuityFileBucketCrosscheck`, pin `autonomous-shipping-active.sh` inside handoff's §3.5 section and inside brief's §6.5 section. Extract each section the way `test_restore_authorization_boundary_on_compact.py:155` does. Also pin "exits 0" and "no hold" in each section, and that handoff's `:177` checklist parenthetical defers to §3.5.

**Not touched:** `claude/.claude/CLAUDE.md`, the repo-local `.claude/settings.json`, `config-keys.psv`, `docs/config-file.md`, `autonomous-shipping-active.sh`, `check-handoff.py`, and the six sibling hooks.

## Verification

1. **Test first, Dispatch A.** Add the new `CORPUS` rows and the armed compact tests before the hook edits. Then run `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_advance_past_commit_stall.py claude/.claude/hooks/tests/test_restore_authorization_boundary_on_compact.py`. Before the edits these fail: the unpunctuated-line row, the "Fixed the failing test.\n\nWant me to commit this?" row, the bullet-list row, the silent "Want me to commit this?\n\nHere is the diff summary." row, the reason-content test and the armed compact tests. The trailing-newline and "anyway" rows already pass and are guards. After the edits, every test in both files passes. Mutation check: deleting the `last_line` step must fail the unpunctuated-line row.
2. **Scoped suite, after both dispatches.** `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. Its selection must include:
   - `test_hook_alignment.py`: header, doc bullet, `_lib.sh` sourcing.
   - `test_shell_lib_function_liveness.py`: the helper has callers.
   - `test_design_decision_files.py`.
   - `test_skills.py`.

   If it omits one of them for this diff, report that as a rule-table bug rather than running the full suite.
3. **Shellcheck**, from the worktree root: `scripts/venv-run.sh shellcheck claude/.claude/hooks/_lib.sh claude/.claude/hooks/announce-autonomous-shipping.sh claude/.claude/hooks/restore-authorization-boundary-on-compact.sh claude/.claude/hooks/advance-past-commit-stall.sh` reports nothing.
4. **Ruff** on the touched Python: `scripts/venv-run.sh ruff check claude/.claude/hooks/tests/test_lib_config_gates.py claude/.claude/hooks/tests/test_announce_autonomous_shipping.py claude/.claude/hooks/tests/test_restore_authorization_boundary_on_compact.py claude/.claude/hooks/tests/test_advance_past_commit_stall.py claude-skills/skills/tests/test_skills.py` reports nothing.
5. **Sizes.**
   - `wc -l claude-skills/skills/handoff/SKILL.md claude-skills/skills/brief/SKILL.md` prints 200 and 190.
   - `git diff --stat origin/main -- claude/.claude/CLAUDE.md` prints nothing.
   - `wc -c claude/.claude/CLAUDE.md` replaces row 12's reported figure in the PR body.
6. **Review pipeline.** `/code-review` covers the diff and dispatches `/skill-review` for both SKILL.md files, which `require-skill-review.sh` enforces at commit.
7. **Stop-hook cost.** Time the hook on a 200 KB multi-line final message in an armed session. It must finish within 100 ms of the same run on a one-line message. If it does not, replace the `##*$'\n'` expansion with `tail -n 1` over a here-string before shipping.
8. **Failures outside these tests.** Reproduce any other failing test at the merge-base before treating it as in scope (`claude/.claude/CLAUDE.md` § "Engineering Judgment").

## Out of scope

- **A prior engineer answer scoped to one branch, reused on another.** The Context places it out of scope for hooks.
- **The handoff and brief artifact preambles** (`handoff/SKILL.md:64-79`, `brief/SKILL.md:47-58`). They also define the boundary through "externally-observable side effects". Each is a verbatim block that `check-handoff.py` re-reads, and handoff sits at its 200-line cap. The resuming session's own statement outranks a preamble.
- **A `claude --agent` main session.** It receives the statement, while the Stop hook skips any payload carrying `agent_type`. Aligning the two needs `agent_type` from the SessionStart payload, whose presence is unverified (row 22). Recorded as a known gap in `docs/hooks.md`.
- **An untracked optout present in only one linked worktree.** The statement's worktree scope misses it (row 14). Recorded as a known gap.
- **Review findings recorded as known limits in `docs/hooks.md`, not fixed here.**
  - The statement is a session-start snapshot, so revoking `autonomous_shipping` mid-session is not seen until the next start.
  - A fork may hold the statement already from its parent's context.
  - The Stop reason's `.commit-stall-block-disabled` switch does not silence the SessionStart statement, and the compact hook's switch silences only the boundary text.
  - Moving the optout to a machine-side record keyed on the main worktree root would remove the untracked-optout gap, but it changes the optout's contract and belongs in its own plan.
- **Silent, non-question stalls.** The Stop hook still does not catch them. The statement is now a second proactive defense against them, and `docs/commit-stall-block.md` says so.
- **Moving the Stop fire predicate into `_lib.sh` for direct unit tests.** The existing test drives a corpus through the hook process. This change adds rows to that corpus and does not restructure it.
- **The six sibling read chains.** See M9.
- **`resume-context/SKILL.md`.** It carries no authorization text (discovery report; not opened).
