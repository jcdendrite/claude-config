# Replace the per-hook python3 failure list in docs/hooks.md

## Context

Goal: stop `docs/hooks.md` § "Python version" from hand-maintaining a per-hook list of what each stowed hook does when its `python3` call fails, because that list restated each hook's own behavior.
The list generated review-round churn on the merged Python-floor change: review rounds 17 to 26 produced nine deferred findings about it.
The round-26 row of that branch's review ledger records three architect consults calling the loop "prose churn".

Ask: "So you were saying that only number 1 and number 2 are worth doing. I agree." (number 1 was replacing the per-hook list in `docs/hooks.md`, which occupies lines 481-486; the handoff's 481-490 range also covers two pointer paragraphs that stay; number 2 was a GitHub issue consolidating the duplicated floor test helpers, which changes no file in this diff) and, on the proposed approach (replace the list with a pointer sentence, add a below-floor note to the header of `send-back-skipped-self-review.sh`, reword `agent_text` in `check-python-floor.sh` so it stops promising a list), "no that all sounds good, but you should run plan-it" — approach accepted via those words.

Evidence from exploration: `docs/hooks.md` lines 481-486 hold the list. `ask-new-dependency-disclosure.sh`, `require-worktree-for-git-writes.sh` and `nudge-error-mode-analysis.sh` already state their own `python3` failure behavior in header comments. `send-back-skipped-self-review.sh` (a `_lib_capped python3` call at line 78) states in its header that every failure allows, but never names `python3`. `check-python-floor.sh:46` (`agent_text`) says the doc "lists what each does if its helper fails"; a `git grep` found no test pinning that wording. `CHANGELOG.md` line 555 records the same promise as past behavior and is a preserved record.

## Approach

Replace the per-hook list at `docs/hooks.md:481-486` with one sentence: each other stowed hook that runs `python3` states in its header comment what it does when that call fails. The skill-management caveat stays as a second sentence. Three sites relied on the list, and the plan makes each true again:
- `send-back-skipped-self-review.sh`'s fail-posture line names `python3` (M2).
- `check-python-floor.sh`'s agent text keeps its tested citation but stops promising a list (M3).
- `docs/security-hardening.md:351` drops its clause about the list (M4).

Paths are relative to the repository root.

**Alternatives weighed and set aside:**
- **Keep the list and add a test tying each item to its hook's header.** That adds a test to maintain restated prose, and every fact still has two homes, which `claude/.claude/CLAUDE.md` § "Engineering Judgment" (single source of truth) calls a defect.
- **Generate the list from the headers.** A generator plus a freshness check is heavier than one sentence, and the doc would still restate each hook's behavior.
- **Delete the list with no pointer.** `check-python-floor.sh:46` and `docs/security-hardening.md:351` would then promise content that no longer exists, and a reader loses the route to each hook's behavior.
- **Name the hooks in the pointer sentence.** The name list is the hand-maintained part, and it would go stale without any signal when a new hook starts running `python3`.
- **For M2, a new header line stating allow-and-log with exit codes.** It would restate the design record's § "Log verdicts", which calls itself the single home of the log vocabulary (row 8).
- **For M2, no edit.** Line 11 already covers `python3` generically. The engineer accepted a header note (row 1), and naming `python3` lets a reader who follows M1's pointer find it.
- **For M3, drop the citation.** `test_check_python_floor.py:214` pins it (row 9), and the agent would lose its route to the section's "unsupported, not failing" framing (`docs/hooks.md:455`).
- **For M3, point the agent at the hook headers.** The agent's only duty is to report (`check-python-floor.sh:9`), so per-hook detail serves no step it takes.

**Root:** `docs/hooks.md` § "Python version" restates, in a hand-maintained list, what each other stowed hook does when its `python3` call fails, and that restatement drew repeated review churn (Context). This plan replaces the list with a pointer to each hook's own header and corrects the three texts that rely on the list (M1-M4).

**Givens:** None. Every condition this plan rests on lives in this repository and could be changed. Out of scope names the ones it leaves alone, with reasons.

**Rows:**
1. `[engineer-verified: "no that all sounds good, but you should run plan-it"]` This answers the session's proposal on the Ask line: replace the list with a pointer sentence, add a below-floor note to `send-back-skipped-self-review.sh`'s header, and reword `agent_text` so it stops promising a list. `anchors: root, M1, M2, M3`
2. `[engineer-verified: "So you were saying that only number 1 and number 2 are worth doing. I agree."]` Context identifies number 1 as replacing the list at `docs/hooks.md` lines 481-490. It does not say what number 2 is, so this plan implements only the approach row 1 accepts. `anchors: root`
3. `[unverified]` These are the session's dispatch details, not the engineer's words: keep the skill-management caveat, make sure `_lib_command_words`'s behavior has a home in `_lib.sh`, and add no test unless one pins the changed text. Each mechanism's prescribed wording is the architect's. `anchors: row1, M1, M2, M3, M4`
4. `[verified: docs/hooks.md:481-490]` `:481` introduces the list and carries the skill-management caveat, and `:482-486` hold five per-hook items. `:488` points to the reconciler's `lock_unavailable` row and `:490` disclaims scripts such as `marker.sh clear-stale`. Those two already point rather than restate, so they stay byte-for-byte. `anchors: M1`
5. `[verified: grep of non-comment python3 lines in claude/.claude/hooks/*.sh; grep of _lib_command_words callers]` These hooks run `python3`: `anchors: M1, M2`
   - directly: `require-worktree-for-git-writes.sh:285-295`, `ask-new-dependency-disclosure.sh:193-203`, `nudge-error-mode-analysis.sh:121-144`, `send-back-skipped-self-review.sh:78`, and `check-python-floor.sh` through `_config.sh:101`;
   - through `_lib_command_words` (`_lib.sh:2562`): `deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, `deny-escaped-backticks-in-pr-body.sh`;
   - every other non-comment match names `python3` only in a word list, regex or message (`deny-network-installs.sh:370-378`, `deny-invisible-commit-content.sh:280`, `deny-repo-relocation.sh:137`, `_lib.sh:2711`).
6. `[verified: claude/.claude/hooks/require-worktree-for-git-writes.sh:44-50; claude/.claude/hooks/ask-new-dependency-disclosure.sh:49-59; claude/.claude/hooks/nudge-error-mode-analysis.sh:41,120; claude/.claude/hooks/deny-pii-in-commits.sh:75-77; claude/.claude/hooks/deny-private-project-refs.sh:82; claude/.claude/hooks/deny-escaped-backticks-in-pr-body.sh:30]` Six of the seven hooks row 5 names besides the notice hook already state in their header what a failed `python3` call, or a nonzero `_lib_command_words` status, does. `anchors: M1`
7. `[verified: claude/.claude/hooks/_lib.sh:2541-2547,2591]` `_lib_command_words`'s header says the fallback tokenizer runs only when `xargs -n1` exits nonzero, a tokenizer that cannot run returns nonzero, and every caller must deny. `_lib_command_words_failure_clause` names `python3`. List item `:483` therefore already has its home, and `_lib.sh` needs no edit. `anchors: M1`
8. `[verified: claude/.claude/hooks/send-back-skipped-self-review.sh:11,21-23,78-83; docs/design-decisions/code-writer-self-review-send-back.md:88,107,119-122]` The header's fail-posture line already says every failure allows, and `:23` cites the design record's § "Log verdicts". That section calls itself the single home of the log vocabulary and reads `allowed-evaluator-exit=127` as no `python3` on PATH. Context's "documented only in the list" therefore holds for the `python3` naming, not for the allow-and-log behavior. `anchors: M2`
9. `[verified: claude/.claude/hooks/tests/test_check_python_floor.py:214; claude/.claude/hooks/tests/test_bash_floor.py:68,399-423]` One test pins the `docs/hooks.md § "Python version"` citation inside `agent_text`, and another pins that the heading exists. Nothing pins "lists what each". M3 keeps the citation, so no test changes. `anchors: M3`
10. `[verified: docs/security-hardening.md:351; docs/hooks.md:453,468,474-479]` `security-hardening.md:351` says the section states "what each other `python3` hook does in the same condition", which M1 makes false. Its other two claims, the working-directory premise and when the notice reports, stay true at the cited lines. `anchors: M4`
11. `[verified: git grep of "Python version", "lists what each" and "what each other" outside .claude/plans]` The other references stay accurate or are records: `anchors: M1, M4`
   - `README.md:117` is generic.
   - `CHANGELOG.md:555` is a preserved record.
   - `docs/hooks.md:204` cites the notice's conditions.
   - `check-python-floor.sh:13` cites the unverified delivery cases at `docs/hooks.md:470-471`.
12. `[unverified]` Hooks that reach `python3` only through a script they call were not traced: `activate-handoff-bypass.sh` calls `marker.sh`, `announce-branch-review-history.sh` calls `review-ledger.sh`, and `consume-durable-continuity-file-on-read.sh` calls `resume-context.sh`. The list already excluded such paths, and `docs/hooks.md:490`, which stays, disclaims scripts. `anchors: M1`

**Mechanisms:**

**M1 — the pointer sentence.** In `docs/hooks.md`, replace `:481-486` with the two lines below. `:487-490` stay byte-for-byte. `anchors: root, row1, row3, row4, row5, row6, row7, row11, row12`

```text
Each other stowed hook that runs `python3`, directly or through `_lib_command_words` in `claude/.claude/hooks/_lib.sh`, states in its header comment what it does when that `python3` call fails.
This does not cover the skill-management plugin's hooks.
```

**M2 — the send-back hook's header.** Replace `send-back-skipped-self-review.sh:11` with the line below, adding no new line. It carries no exit codes, because the design record's § "Log verdicts" owns them and `:23` already cites it. `anchors: row1, row3, row5, row8`

```text
# Fail posture: fail-open. Every failure allows, including a missing python3 or a nonzero evaluator exit, and only the once-per-cycle send-back blocks.
```

**M3 — the agent text.** In `check-python-floor.sh:46`, replace `, and docs/hooks.md § \"Python version\" in the claude-config repository lists what each does if its helper fails.` with `, as docs/hooks.md § \"Python version\" in the claude-config repository explains.` The section explains the unsupported framing at `docs/hooks.md:455`. `screen_text` and the rest of `agent_text` stay unchanged. `anchors: row1, row3, row9`

**M4 — the security-hardening pointer.** In `docs/security-hardening.md:351`, delete the span `` , what each other `python3` hook does in the same condition,`` and insert nothing. The existing ", and when" then supplies the conjunction, so the clause reads "states the unverified working-directory premise this rests on and when the SessionStart notice reports it." The citation stays on its line, per `.claude/rules/citation-grammar.md`, and the rest of the line is unchanged. `anchors: row3, row10`

## Critical files

- `docs/hooks.md`: M1, replacing `:481-486`.
- `docs/security-hardening.md`: M4, one clause on `:351`.
- `claude/.claude/hooks/check-python-floor.sh`: M3, one clause on `:46`.
- `claude/.claude/hooks/send-back-skipped-self-review.sh`: M2, line `:11`.
- `.claude/plans/hooks-doc-python-floor-list.md`: this plan, committed with the change.

Reuse: the hook headers row 6 cites and `_lib.sh:2541-2547` are the homes M1 points to, so no new text restates them. No test file changes (row 9).

Dispatch split: one `code-writer` dispatch covers all four files. They share one context, and each edit is a line or two.

Projected diff: about 14 lines (8 in `docs/hooks.md`, 2 in each other file).

## Verification

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. Its selection should include `claude/.claude/hooks/tests/test_check_python_floor.py`, which pins the citation (`:214`), and `claude/.claude/hooks/tests/test_bash_floor.py`, which pins the heading (`:419-423`). If either is missing from the selection, report that as a rule-table bug instead of widening the run (`CLAUDE.md` § "Commands"). Run with `-rs` and confirm `test_check_python_floor.py` reports no skips, because its citation test is skipped when `jq` is not on PATH.
2. `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` reports nothing for the two edited hooks.
3. `git grep -n "lists what each" -- . ':!CHANGELOG.md' ':!.claude/plans'` and `git grep -n "what each other" -- docs` both print nothing. `git grep -n "rests on and when" -- docs/security-hardening.md` prints line 351, and `git grep -n "and and" -- docs/security-hardening.md` prints nothing.
4. M1's claim holds: `git grep -nE '^#.*(python3|_lib_command_words)' -- claude/.claude/hooks/require-worktree-for-git-writes.sh claude/.claude/hooks/ask-new-dependency-disclosure.sh claude/.claude/hooks/nudge-error-mode-analysis.sh claude/.claude/hooks/send-back-skipped-self-review.sh claude/.claude/hooks/deny-pii-in-commits.sh claude/.claude/hooks/deny-private-project-refs.sh claude/.claude/hooks/deny-escaped-backticks-in-pr-body.sh` prints at least one line from each of the seven files. The set is complete when `git grep -lE '(_lib_capped(_for [0-9]+)? |command -v |\$\()python3|_lib_command_words|_python3_floor_status' -- 'claude/.claude/hooks/*.sh'` lists exactly those seven files plus `check-python-floor.sh`, `_lib.sh` and `_config.sh`, and nothing more. An extra file means M1's "each other stowed hook" claim needs that hook checked.
5. The diff touches only the files in Critical files.
6. At `/code-review`, `claude-hook-review:claude-hook-review` covers both edited hooks, `check-python-floor.sh` and `send-back-skipped-self-review.sh`.

## Out of scope

- **`CHANGELOG.md:555`.** It is a preserved record (`claude/.claude/CLAUDE.md` § "Working Style", Scope discipline Axis 3; `.claude/rules/changelog-entries.md`). It sits under `[Unreleased]`, so its "lists what each" pointer will read as stale at release. The plan adds no new entry, since no hook's behavior changes.
- **The helper-consolidation issue.** It is a separate follow-up, and this plan's text points at the hooks' current headers whatever that issue decides.
- **The other deferred findings from the Python-floor branch's review rounds 17-26 (Context).** This plan removes only the list.
- **A test that every hook running `python3` carries a header note.** It would guard M1's claim mechanically, but the engineer's accepted approach adds no test unless one pins the changed text (rows 1, 3, 9).
- **Failure notes in the skill-management plugin's hooks** (`require-skill-review.sh`, `provision-validator-venv.sh` run `python3`). Editing them needs a plugin version bump, which `require-plugin-version-bump.sh` enforces, and the caveat in M1 keeps them outside the claim.
- **Tracing hooks that reach `python3` through a script they call** (row 12). `docs/hooks.md:490` already disclaims scripts.
- **`docs/hooks.md:490`.** Its "this section does not list their below-floor behavior" now contrasts with a list that no longer exists. M1 keeps `:487-490` byte-for-byte, and the line breaks nothing, so tightening it is a separate edit.
- **`_lib.sh`.** Its `_lib_command_words` header already holds the behavior M1 points to (row 7).
