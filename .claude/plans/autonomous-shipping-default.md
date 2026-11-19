# Make autonomous shipping the only mode

## Context

Goal: make autonomous shipping (commit, push the feature branch, open the PR without pausing) unconditional, and delete the machinery that supports running without it.

Ask: "I'd like to make autonomous shipping the default and remove the complexity around allowing both modes. Autonomous shipping has been working pretty well, and after all, the toolkit this repo defines is a workflow enforcement layer, so autonomous shipping (going all the way through to a PR) is safe. There's no need for engineers to read diffs with this toolkit." Widened later: "the goal of the stop hook is not to remind the agent to ship but remind that it shouldn't ask the user for permission to continue when it reaches a \"stopping point\". The toolkit tells the agents when to surface questions to the user." — accepted via label "Add it, architect's wording (Recommended)"

Why now: autonomous shipping is opt-in per machine (`autonomous_shipping` config key) with a per-repo opt-out file, so a resolver script, a config-key row, an install prompt, a SessionStart announcement, and a body of two-mode tests exist only to decide which mode applies. The engineer reports the mode has worked well and calls the toolkit "a workflow enforcement layer, so autonomous shipping (going all the way through to a PR) is safe". The plan does not rest the safety case on the review gates. For a default-branch push the push hook misses, remote branch protection is the only barrier (row 31); M6 lists what it does not cover.

Intended outcome:
- `claude/.claude/CLAUDE.md`'s Shipping rule applies unconditionally.
- The resolver, the config key, the repo opt-out file and the announce hook are removed.
- Hooks that branched on the mode emit static text.
- Stale docs and tests are updated.

Engineer decisions this session, as the selected labels:
- Escape hatch: "None; delete both (Recommended)".
- Announce hook: "Delete it (Recommended)".
- Legacy state: "Silently ignore (Recommended)".
- A later answer narrowed the legacy-state decision, on whether a leftover config row may warn: "Accept the warning".

Scope notes from exploration (evidence, not conclusions), each naming something that stays:
- `require-diff-size-prompt.sh` is mode-independent.
- `commit_stall_block` is the Stop hook's own kill switch.
- `deny-default-branch-push.sh` is mode-independent.
- The `_lib_autonomous_shipping_root_for_cwd` HOME-ancestor guard is a safety check, not a mode toggle.

## Approach

The Shipping rule in `claude/.claude/CLAUDE.md` becomes unconditional, and everything that existed only to pick a mode is deleted.
- Deletions: the resolver script, the two mode helpers, the schema row with its install prompt, the per-repo optout and the announce hook go (M1).
- Shipping rule: CLAUDE.md's Shipping bullet states the push scope, the irreversible-action limit, the staging limit and the in-session hold, and the statement defers to it (M2, M5).
- Stop hook: it fires in any repo the existing HOME-ancestor root guard accepts, consults no switch except `commit_stall_block`, and tells the agent not to ask permission to continue at a natural stopping point unless CLAUDE.md, the skill it follows or a stated hold requires the question (M3, row 48).
- Compact hook: it appends a static statement for any root the guard accepts (M2, M3).
- Leftover state: optout and sentinel files stay silent because nothing reads them, and a leftover `autonomous_shipping` state-file row warns on stderr until the adopter deletes it, as the CHANGELOG says (rows 3-4).
- Docs: they disclose what the remaining guards and the Stop reason do not check (M6).

Alternatives weighed:
- **Flip `autonomous_shipping`'s default to `true` and keep the machinery.** Rejected: row 1 deletes the key, and the Ask names the resolver, union and two-mode tests as the complexity to remove.
- **Keep `_lib_autonomous_shipping_active` as an always-true stub.** Rejected: an indirection with no decision behind it.
- **Drop the statement from the compact hook too, since CLAUDE.md now carries the rule.** Rejected: the boundary text would then outrank a push or PR after compaction (row 10).
- **Keep the statement in the Stop reason and drop only the recipe.** Rejected: the reason carries no boundary text for the statement to outrank, unlike the compact hook (row 10), and the Ask's widening says the hook's goal "is not to remind the agent to ship".
- **Name the grant's actions in the reason, or keep the hook silent when the final sentence names a target outside the grant.** Rejected: the engineer accepted the reason as M3 quotes it (row 48), and a fire predicate cannot enumerate every action outside the grant. The reason's carve-out tells the agent to route such a question back (not measured, row 50), Dispatch 1 pins that the hook fires on it, and the docs state the gap (row 49).
- **Add whole-word `main`, `master`, `deploy` and `production` to `EXCLUDE_RE`, so the two pinned shapes fail silent.** Rejected: the engineer chose to keep the disclosed gap (row 63).
- **Restore "never stage-all" and the fork limit to the reason.** Rejected: the reason reaches the agent at most once per prompt, while CLAUDE.md is always loaded. The staging limit moves into the Shipping bullet (rows 51, 62), and Agent Core already carries the fork rule (row 52).
- **Measure the reason's carve-out with an eval case.** Rejected: beyond the Ask. The docs and the PR body state that neither direction is measured (rows 45, 50).
- **Observe row 18 with a probe line in the engineer's config file.** Rejected: the engineer dropped that step (row 56). No row, mechanism or doc branches on its result, and the CHANGELOG already says the warning may appear in session output (row 18).
- **Copy `check_ignore`, `assert_ignored_by_repo_gitignore` and the recording-shim helper into the test files that need them.** Rejected: `test-conventions/SKILL.md` § "6. Test data" calls a copied named helper a defect, so all three move to `helpers.py` (Reuse).
- **Drop the root guard along with the switches.** Rejected: the Stop hook would force a commit of a HOME-rooted work tree from any non-repo directory under HOME (row 7).
- **Skip a retired key in both config readers, so a leftover row is silent.** Rejected: row 4 accepts the warning, so a constant and a branch in each reader, plus a parity assertion, would buy nothing.
- **Have `migrate-legacy-config.sh` strip the row.** Rejected: a consumer runs the new readers after `git pull` without running migration (G2), and row 3 rules out a cleanup step.
- **Keep the limits only in the statement.** Rejected: with the announce hook gone, a fresh session never sees the statement, so no always-loaded text would state the push scope (row 28).
- **State the limits in both CLAUDE.md and the statement.** Rejected: CLAUDE.md is always loaded, so a second copy is a single-source-of-truth defect with no named exception. Row 29 records the post-compaction premise this rests on.
- **Restate "merge stays human-only" in the Shipping bullet.** Rejected: Agent Core already states it in always-loaded text, with a test pin (row 27).
- **Keep the compact hook silent on a relative `CLAUDE_CONFIG_DIR`.** Rejected: the statement path would need a config read again, though the rule it restates applies with no config at all (row 35).

### Assumption ledger

**Root:** The machinery Context's "Why now" lists, plus sentinel fast paths in two hooks, exists only to decide which shipping mode applies, and the engineer wants one mode, always on.

**Givens:**
- G1. A Stop hook's `{"decision":"block"}` forces the turn to continue, and a SessionStart hook's `additionalContext` injects text. Reason: Claude Code's hook contract imposes both.
- G2. `claude/.claude/**` goes live on `git pull` with no guaranteed re-run of `install.sh` or `migrate-legacy-config.sh` (`config-keys.psv:70-73`, `docs/config-file.md:133-140`). Reason: the repo's stow distribution model, which this plan does not change.
- G3. Each stow consumer's `claude-config.toml`, legacy sentinel files and repo optout files are that machine's or repo's own state. Reason: their owners hold them, and this repo writes the state file only through its two sanctioned writers (`docs/config-file.md:212-228`).

**Mechanisms:**
- **M1. Delete the mode machinery.** Delete each of these:
  - `claude/.claude/scripts/autonomous-shipping-active.sh`.
  - `claude/.claude/hooks/announce-autonomous-shipping.sh` and its `settings.json` registration.
  - `_lib_autonomous_shipping_sentinel_present` and `_lib_autonomous_shipping_active` (`_lib.sh:3348-3398`).
  - The `autonomous_shipping` row (`config-keys.psv:92`).
  - The optout row in `install.sh`'s `REPO_MARKER_INVENTORY` (`:645`).
  - `autonomous_shipping`'s entry and safe-value arm in `migrate-legacy-config.sh` (`:35`, `:50`, `:57`).
  - The tests whose subject is one of these. Tests that only borrowed the key for another property are re-pointed instead (rows 38-41).

  Add no cleanup step and no retired-key skip in either config reader (rows 3-4).

  anchors: root, row1, row2, row3, row4, row19, row38, row39, row40, row41
- **M2. Static statement that defers its limits.** `_lib_autonomous_shipping_statement` takes no argument and has no arity guard, as `_lib_permission_prompt_tracking_active` (`_lib.sh:3453-3455`) has none. It always prints the text. Edit the text in three places:
  - Replace everything from the opening through `In this repository, the engineer'\''s` with `Autonomous shipping is active for this repository: the engineer'\''s`.
  - Replace the two sentences from ` It covers only committing,` through `and does not ship.` with ` The scope and limits CLAUDE.md sets for shipping still apply.`
  - Delete the final sentence, ` In a different repository, run that script there before relying on this.`

  Three more edits:
  - Delete the `# shellcheck disable=SC2016` line above the `printf`, because no backtick is left in the text.
  - The function's header becomes one line: `# Prints the agent-facing shipping statement.`
  - Its one remaining caller, `restore-authorization-boundary-on-compact.sh:72`, drops the argument.

  `AUTONOMOUS_SHIPPING_STATEMENT_LEAD` (`helpers.py:999`) still matches the opening. M3 deletes the Stop hook's call (`advance-past-commit-stall.sh:205`). The deleted span takes the fork sentence with it, and Agent Core keeps that rule (row 52).

  anchors: row10, row27, row28, row29, row42, row44, row52
- **M3. Hooks stop consulting a switch.**
  - `advance-past-commit-stall.sh`, one edit per item:
    - Delete step 3 (`:72-81`), and renumber the later steps with their in-file cross-references (`step 9`, `gate 5`).
    - Step 9 (`:198-205`) keeps `_lib_autonomous_shipping_root_for_cwd` and its `root-denied` log, because `REPO_ROOT` feeds the git checks. Delete the `SHIPPING_STATEMENT=$(...) || exit 0` line (`:205`), and drop "machine-anchored opt-in" from the step's comment (row 44).
    - Step 2's comment (`:59`) drops "independent of sentinel state".
    - `REASON` (`:256`): replace everything before `To disable this on this machine:` with the rewrite row 48 records, verbatim, and delete the last sentence (`To disable it for this repo: …`). The disable sentence stays unchanged and last. The line becomes exactly:

      ```bash
      REASON="Do not ask the engineer for permission to continue at a natural stopping point, unless CLAUDE.md, the skill you are following, or a hold the engineer stated requires the question. If one does, end your turn. Otherwise continue with your task's next step, say the task is complete, or say what is blocked. To disable this on this machine: printf 'commit_stall_block = false\n' >> \"$CONFIG_DIR/claude-config.toml\"."
      ```

    - The header (`:3-9`) says the hook blocks a main session's stop whose final sentence asks permission to commit, push or open a PR while work is pending, and that its reason tells the agent not to ask permission to continue at a stopping point. It fires in a repo the root guard accepts, with HEAD on a non-default branch.
    - The kill-switch paragraph (`:24-28`) names `commit_stall_block` only.
    - The known gaps (`:33-46`) gain two lines, each pointing at `docs/commit-stall-block.md`:
      - `commit_stall_block = false` is not honored while `config-keys.psv` is unreadable or missing the row (row 22).
      - The predicate does not read what the question targets, so from a feature branch a compound question or one naming the default branch fires (row 49).
  - `restore-authorization-boundary-on-compact.sh`, one edit per item:
    - Delete the sentinel guard (`:69`, `:74`) and its fast-path comment (`:66-67`), so every compaction reads `.cwd`, resolves the root, and appends the statement when the root is eligible.
    - The statement call (`:72`) takes no argument.
    - The header lines that tie the statement to the mode (`:5-6`, `:28`) say the kill switch silences only the boundary text and the statement depends only on the root. A relative `CLAUDE_CONFIG_DIR` therefore skips the boundary text and keeps the statement (row 35).
  - `.gitignore` ignores the Stop hook's log and state dir, which every consumer with `commit_stall_block` on now writes (row 37).

  anchors: row7, row8, row9, row10, row22, row23, row35, row36, row37, row44, row45, row48, row49, row50, row52, row58, row59, row60, row63
- **M4. Keep the root guard.** `_lib_autonomous_shipping_root_for_cwd` stays unchanged as the one eligibility rule both hooks share.

  anchors: row7, row30
- **M5. Instruction prose.** Replace `claude/.claude/CLAUDE.md:176-178` with exactly this text:

  ```markdown
  - **Autonomous shipping: a request to do work is the ask.** Some sessions carry a harness instruction of the form "Commit or push only when the user asks." Being asked to make the change is that ask: run `/code-review`, commit, run `/ready-for-review`, and open the PR without pausing for permission. Do not stop at a "natural stopping point".
    - The ask covers only committing, a plain push of the current branch (never to the default branch), and opening a PR. The ask does not extend to force-pushing or any other irreversible action.
    - Stage only the paths this task changed, never the whole tree (`git add -A`, `git add --all`, `git add .`).
    - A hold the engineer states in-session still wins.
    - Do not offer to show the diff first; the review surface is the PR, not a local working tree. Do not ask permission to proceed with work that is already done.
  ```

  With this text the file stays under its byte and line limits (row 57). Each part's disposition:
  - Added: the scope sub-bullet carries the push scope the statement drops (M2, rows 28, 42). Its second sentence says only that the ask does not extend further, because Engineering Judgment already requires confirming an irreversible action (row 55).
  - Added: the staging sub-bullet carries the limit the Stop reason drops (rows 46, 51). It states the property, and the three stage-all spellings are examples, not the rule (row 62).
  - Added: the hold sub-bullet (rows 28, 42).
  - Kept: the diff-offer sub-bullet, which carries no mode condition, while the Ask says engineers need not read diffs.
  - Dropped: the verify-via-script sub-bullet and the "A repo cannot switch this on" sentence, because no switch is left.
  - Not restated: merge, because Agent Core's `CLAUDE.md:85` already keeps it human-only for every agent (row 27), and "only" in the scope sentence excludes it.
  - Kept: the term "autonomous shipping" as the concept's name, which the statement lead, `plan-it/SKILL.md:172` and the docs still use.

  In `handoff/SKILL.md:118`, the continuity-file exception's first sentence becomes the text below. `brief/SKILL.md:91` gets the same sentence with `§6` in place of `§3`. The rest of each paragraph is unchanged, so "stated" matches its closing "A hold the engineer stated goes in §3.5". The change pre-authorizes a resumer for any such step filed with no stated hold (row 61).

  ```markdown
  Exception: a commit, a plain `git push`, and `gh pr create` belong in §3, not here, unless the engineer stated a hold on them.
  ```

  anchors: root, row1, row14, row20, row27, row28, row42, row46, row51, row55, row57, row61, row62
- **M6. Disclosures (rows 28, 42).** The docs state what the remaining guards do not check, each fact in one home:
  - `docs/security-hardening.md` holds the security facts in a new section, one per item:
    - Server-side branch protection is a precondition of unconditional shipping, not optional hardening.
    - The root guard checks the path's shape, not who owns the remote.
    - The root guard bounds only the two hooks.
    - The tracker-ID, structural and project-name blocklist scans run only in a gated repo, and are silent in every other repo.
    - The blocklist scan waits on a populated `private-projects.md`, and the PII scan waits on `pii-patterns.md`.
    - The credential-value scan is the only one that fires in every repo, and it covers `git commit` only.
    - A PR body can carry engineer quotes to whatever remote `origin` names.
  - `README.md` states the switch facts and links that section for the rest, one per item:
    - `commit_stall_block = false` is machine-wide, and no per-repo exemption remains.
    - CLAUDE.md tells the agent that a hold the engineer states in-session wins, and the Stop reason carves a stated hold out. Whether the agent honors a hold under the Stop hook is not measured (rows 45, 50); link `docs/commit-stall-block.md`.
    - Open sessions need a restart to read the new rule.
  - `docs/commit-stall-block.md` states what the Stop reason leaves unchecked: the question's target, the fork limit, and both unmeasured directions (rows 45, 49, 50, 52).
  - `CHANGELOG.md` records each item for the release, including the migration notes in rows 33-34 and 64.
  - Neither `README.md` nor `CHANGELOG.md` presents the review gates as the protection.

  anchors: row24, row28, row30, row31, row32, row33, row34, row42, row45, row49, row50, row52, row61, row64

**Rows:**
1. Both the per-repo `.claude/autonomous-shipping-optout` and the machine `autonomous_shipping` key are deleted, and nothing replaces them as a switch for the Shipping rule. [engineer-verified: "None; delete both (Recommended)"]
2. `announce-autonomous-shipping.sh` is deleted. [engineer-verified: "Delete it (Recommended)"]
3. Leftover legacy state (an `autonomous-shipping-required` file, a repo optout file, a `claude-config.toml` row) is ignored, with no cleanup step. Leftover optout and sentinel files stay silent. Row 4, a later answer, narrows "Silently" for the state-file row. [engineer-verified: "Silently ignore (Recommended)"]
4. A leftover `autonomous_shipping` state-file row is not silent: both readers print a stderr warning for it (row 6) until the adopter deletes it, and neither reader gains a retired-key skip. [engineer-verified: "Accept the warning"]
5. `install.sh`'s prompt writes `autonomous_shipping = true` on an opt-in (`:579-583`), and `false` when an enabled machine opts out (`:541-544`). Migration never writes this key's permissive value. It defers it for a hand-paste (`docs/config-file.md:162-174`). Every machine that accepted the prompt, or hand-pasted the value, therefore carries a row. [verified: install.sh, docs/config-file.md]
6. Each reader warns on stderr, "skipping line for unrecognized key", for every grammatically valid state-file line whose key has no schema row, each time it scans the file. Neither reader has a retired-key list today. After the row is deleted, a leftover line would warn on the first lookup of each key in every process, including each `config-get.sh` call and each `_config.py` caller. [verified: `_config.sh:327-331`, `:373-379`; `_config.py:241-271`; grep of both files for retired/deprecated: none]
7. `_lib_autonomous_shipping_root_for_cwd` reads no config and no optout. It rejects an empty or relative `.cwd`, a `.cwd` outside a repo, an empty or unresolvable HOME, and a root at or above HOME. [verified: `_lib.sh:3400-3429`]
8. `commit_stall_block` (default `true`) is the Stop hook's own kill switch and does not depend on the shipping key. [verified: `advance-past-commit-stall.sh:59-70`, `config-keys.psv:102`]
9. Today a machine without the sentinel exits before spawning jq. After M3, every Stop event spawns one jq for every stow consumer with `commit_stall_block` on, while git runs only after the fire regex matches. Every compaction also spawns jq and git. The only measured figure for this hook, ~33-35ms at `docs/config-file.md:308-315`, is for the call shape M3 removes. [verified: `advance-past-commit-stall.sh:72-112`, `:198-240`; `restore-authorization-boundary-on-compact.sh:63-74`; `docs/config-file.md:300-318`]
10. The boundary text requires in-session confirmation for any action with "effects observable outside this repository". That covers a push and a PR, so without the statement a compacted session reads the boundary text as overriding the Shipping rule. [verified: `restore-authorization-boundary-on-compact.sh:19-21`, `:58`]
11. The schema row count is pinned in five places: `install.sh` ("reports all 22"), `migrate-legacy-config.sh` ("all 22 keys"), and three `docs/config-file.md` sentences. Each becomes 21. `config-schema-audit.md`'s "Remaining 17 keys" stays 17, because the enforcement-critical set in `_MIGRATE_ENFORCEMENT_CRITICAL_KEYS` drops from five to four in the same change. [verified: `test_doc_counts.py:430-471`, `:672-713`; `migrate-legacy-config.sh:35`]
12. Every hook needs its own `docs/hooks.md` entry. No test asserts a hook count. The announce hook is named only in its own test, `test_autonomous_shipping_grant_boundary.py`, and `test_cursor_adapter_gate_matrix.py:534`, `:979`. [verified: `test_hook_alignment.py:4`, `:188-214`; grep of `claude/.claude/hooks/tests/` for `announce-autonomous-shipping`]
13. This repo carries no `.claude/autonomous-shipping-optout` to delete. [verified: Glob in the worktree, no match]
14. The resolver precondition is the continuity-file exception's only mode-dependent clause. The "no hold" and "never to the default branch" conditions stand without it. [verified: `handoff/SKILL.md:118`, `brief/SKILL.md:91`, `test_skills.py:2065-2095`]
15. "Autonomous shipping does not waive the question." governs a plan-time push with no PR, and it stays true once shipping is unconditional. [verified: `plan-it/SKILL.md:166-172`, `test_skills.py:5955`]
16. The explore-recall fixture's answer keys are checked against a checkout of its `pinned_commit`, so deleting the script does not invalidate its case A2. [verified: `evals/test_measure_explore_recall.py:299-318`, `evals/fixtures/explore-recall-cases.json:2`]
17. `.gitignore:90` keeps a leftover `autonomous-shipping-required` file from being staged where `~/.claude` folds into the checkout. Row 3 leaves such files on disk, so the line stays. [verified: `.gitignore:85-90`]
18. Claude Code does not show a hook's stderr to the agent on exit 0. This affects only how visible the warning row 4 accepts is from hooks. Scripts that call `config-get.sh` or `_config.py` through the Bash tool show stderr regardless. `record-session-config-keys.sh:5` silences its stderr to keep `_config.sh` warnings out of the transcript, which suggests some hook stderr can reach it. No Verification step observes this (row 56), and the CHANGELOG says the warning may appear in session output either way. [unverified]
19. The install prompt loop is driven by the schema's `prompt-description` column, so deleting the row removes the prompt with no `install.sh` code change. [verified: `install.sh:593-626`]
20. The diff-offer sub-bullet's proceed clause is pinned to Main session. Its bullet-placement substring is the old bold lead, which M5 changes. [verified: `test_global_claude_md_groups.py:46-47`, `:101-106`, `:366-374`]
21. `test_branch_records_lib.py`'s `autonomous_shipping` lines are other-key and synthetic-schema fixtures for a reader that never looks that key up. Its tests make no assertion about stderr on those lines. [verified: `test_branch_records_lib.py:378-405`, `:527-540`]
22. With `config-keys.psv` unreadable or missing `commit_stall_block`'s row, that key stays armed by design. An explicit `commit_stall_block = false` row is then not honored, and neither is the legacy file, whose name comes from the schema. Today the Stop hook is still silent end to end, but only because the sentinel fast path fails on the same broken schema. After M3 it fires there. The exit-3 and exit-4 case tests lose their sentinel stubs, gain a `false` row, and become the end-to-end proof, and `docs/commit-stall-block.md` states the gap. The fail-armed direction stays. [verified: `advance-past-commit-stall.sh:59-81`, `test_advance_past_commit_stall.py:455-565`, `config-keys.psv:102`]
23. On a degraded schema, the compact hook today drops the statement and keeps the boundary text. After M3 it keeps both. [verified: `test_restore_authorization_boundary_on_compact.py:337-367`]
24. Today the optout is the only way to exempt a cloned third-party repository. After row 1, the Shipping rule and the Stop hook apply there too. The docs keep this as a known gap. [verified: `docs/hooks.md:276`]
25. The plan ships as one PR at its projected ~1,950 changed lines, above the 400-line size threshold, with no split. [engineer-verified: "I had assumed the first option would be the recommended one by the architect. Let's go with taht one please."]
26. The plan still ships as one PR at the ~2,350 changed lines projected after the round-1 additions, up from row 25's ~1,950. [engineer-verified: "Proceed anyway"]
27. Agent Core already keeps merge human-only and tells a fork or subagent to return its work rather than ship, in always-loaded text pinned by a placement test. [verified: `claude/.claude/CLAUDE.md:85`; `test_global_claude_md_groups.py:93-97`]
28. The session proposed these scope calls as its own defaults, and row 42 records the engineer's acceptance: the push scope, the force-push confirmation and the in-session hold go into CLAUDE.md's Shipping bullet, the statement defers to CLAUDE.md, and README, CHANGELOG and `docs/security-hardening.md` carry M6's disclosures. The same proposal keeps an engineer-visible leftover-file notice and a Stop-hook carve-out for unowned remotes out of scope. [unverified]
29. CLAUDE.md stays in the agent's context after compaction, so a statement that defers to it still reaches the limits there. Anything resting on M2's deferral inherits this flag. [unverified]
30. The root guard checks only the path: it rejects a relative or non-repo `.cwd`, an unresolvable HOME, and a root at or above HOME, and it reads nothing about the remote. Only the Stop and compact hooks call it, so it bounds them and not the CLAUDE.md rule. [verified: `_lib.sh:3410-3429`; `advance-past-commit-stall.sh:201`; `restore-authorization-boundary-on-compact.sh:71`; `docs/hooks.md:258`]
31. `docs/security-hardening.md` § "Required server-side branch protection" names remote protection as the only barrier for the push gate's known gaps, and the file never mentions autonomous shipping. [verified: `docs/security-hardening.md:541-568`; grep of the repo for `autonomous[-_ ]shipping`, which lists no hit in that file]
32. The tracker-ID, structural and project-name blocklist scans run only where the origin URL contains `claude-config` or the repo commits `.claude/private-projects-optin`. They are silent in every other repo, so a PR or issue body there goes unscanned by that hook. Inside a gated repo the tracker-ID and structural scans need no file. Only the blocklist scan waits on `<config-dir>/private-projects.md` being populated. Populating `private-projects.md` is necessary for the blocklist scan but not sufficient outside a gated repo. The only scan that fires in every repo is the credential-value tier of `deny-pii-in-commits.sh`, and it covers `git commit` only. The PII tier of that hook waits on `<config-dir>/pii-patterns.md`. [verified: `claude/.claude/hooks/deny-private-project-refs.sh:25-29`, `:39-45`; `claude/.claude/hooks/deny-pii-in-commits.sh:9-16`; `docs/private-project-redaction.md` § "The three scans" and § "Opting in another repository"; `docs/security-hardening.md:408-417` and § "Arming the commit-scan hook"; repo `CLAUDE.md` § "Redact private-project-identifying content"]
33. A handoff or brief filed before this change can name the resumer's own `autonomous-shipping-active.sh` exit 0 as a filed step's precondition. After the pull, checking that precondition runs a missing script and gets a non-zero status, so the precondition is unmet. An unmet precondition takes the step out of the old exception, and the paragraph's underlying principle then puts it among the §3.5 actions that wait for the engineer. Row 64 records what the resumer does next. [verified: `handoff/SKILL.md:118`, `brief/SKILL.md:91`]
34. A re-run of `install.sh` is expected to leave dangling links for the two deleted files. A Cursor adapter's `hooks.json` is expected to keep the announce entry until `cursor-adapter.py check` reports it. A copied `settings.json` is expected to keep the announce registration, which is expected to log a non-blocking missing-file error. None of them is expected to block a session. [verified: `install.sh:451`, `:455` run `stow -v` without `-R`; `settings.json:183-192` carries no `onFailure`; `docs/hooks.md:482` says every gate registration sets `onFailure: "block"` so a gate that cannot start blocks; `claude/.claude/scripts/cursor-adapter/hook-shim.py:1462-1467` refuses a missing script and `:1614-1621` answers that refusal with `{}` for a fail-open entry] [unverified: that a failed start of a registration with no `onFailure` stays non-blocking, that the Cursor announce entry registers as fail-open, that `cursor-adapter.py check` reports a removed registration (`README.md:105` names only a changed registration, tool map or contract version), that stow leaves the dangling links, and that Claude Code logs a missing-file error for the copied registration]
35. With a relative `CLAUDE_CONFIG_DIR`, `_config_enabled` exits 2, so the compact hook skips the boundary text. After M3 its statement path reads no config, so it emits the statement alone for an eligible `.cwd`, where today the sentinel guard keeps it silent. The Stop hook still exits at step 1. [verified: `restore-authorization-boundary-on-compact.sh:47-61`, `:69`; `advance-past-commit-stall.sh:56`; `test_restore_authorization_boundary_on_compact.py:327-335`]
36. The compact hook's own `.cwd` type guard (`:70`) has its only test in the announce hook's test file, which M1 deletes. The compact test file has no git-absent case, and its only test of the empty-message exit (`:80`) rests on the optout. [verified: `test_restore_authorization_boundary_on_compact.py:53-374`, `:261-267`; `test_announce_autonomous_shipping.py:99-112`; `grep -n -i -E "non_string|bad_cwd|git_absent|no_git" test_restore_authorization_boundary_on_compact.py` lists one hit, `:143` (`test_non_string_source_produces_no_output`, which tests the `.source` field and not the `.cwd` guard); `restore-authorization-boundary-on-compact.sh:70`, `:80`]
37. The Stop hook writes `<config-dir>/.commit-stall-block.log` and `.commit-stall-block.d/`, and `.gitignore` covers neither. Under stow directory-fold such state lands inside the checkout, which `.gitignore:100-104` already guards against for marker state. After M3 every consumer with `commit_stall_block` on writes them, because `VERB_RE` matches closers like "let me know if". [verified: `advance-past-commit-stall.sh:114`, `:144`, `:164`, `:190-192`; `.gitignore:84-179`]
38. Two tests parse text this plan edits. `test_advance_past_commit_stall.py:817` splits `REASON` at the sentence M3 deletes. `test_migrate_legacy_config.py:800-801` searches the schema header for `The five enforcement-critical keys (`. [verified: both files]
39. Once the `autonomous_shipping` row goes, no real row combines `config-dir-or-home` with probe `false`. The union fold's fail-safe (`_config.sh:935-940`) and the home-probe conjunct (`:975`) then lose their only real-key tests. [verified: `config-keys.psv:91-112`; `_config.sh:935-940`, `:975`; `test_config_lib.py:432-445`, `:1024-1041`]
40. Once the row goes, `worktree_required` is the only promptable enforcement-critical key. Its permissive write is an `n` on a machine where it is enabled, so the pty canary's `y` path no longer writes a permissive value. [verified: `config-keys.psv:91-106`; `install.sh:532-591`; `test_install_sh_machine_level_opt_ins.py:392-424`]
41. Beyond row 11's row counts, prose and docstrings state the enforcement-critical count (five), the pre-migration promptable count (six) and derived counts ("other four", "four of the five", "all five", "these four") at the sites Dispatch 2 lists. [verified: multiline Grep of `docs/`, `claude/.claude/` and `install.sh` for those counts within two lines of `enforcement-critical`, `promptable` or `pre-migration`]
42. The engineer accepted row 28's proposed scope calls. [engineer-verified: "Those defaults look good"]
43. The engineer accepted the architect's earlier wording as the Stop-hook reason's text before its disable sentence. Superseded by row 48. [engineer-verified: "Add it, architect's wording (Recommended)"]
44. The reword needs no new fire logic. `SHIPPING_STATEMENT` is read only by `REASON`, which the hook builds after every gate and reads only to encode the block payload. Once M2 makes the statement unconditional, its `|| exit 0` gate no longer depends on any state, so deleting it removes no fire condition. `REPO_ROOT` still feeds the work-pending and HEAD checks, so the root guard stays. The exclusion regex keeps the hook off a final sentence naming merge or force-push, and the HEAD check keeps it silent while HEAD is on the default branch. Neither reads what the question targets (row 49). [verified: `advance-past-commit-stall.sh:164-167`, `:201-205`, `:233`, `:235-240`, `:256-263`]
45. The shorter reason moves a stalled agent past the stopping point as reliably as the explicit `/code-review`, commit, `/ready-for-review`, PR recipe it replaces. Not measured. [unverified]
46. No instruction file tells the agent never to stage all. Once "never stage-all" leaves the reason, it survives only in `docs/commit-stall-block.md:26`, which Dispatch 3 rewrites. `subagent-delegation/SKILL.md:197` prescribes literal-path staging only for its review-orchestrator loop, and `README.md:365` names a `git add -A` sweep as a risk, not a rule. [verified: case-insensitive Grep of `claude/.claude/CLAUDE.md`, the repo `CLAUDE.md`, every `SKILL.md`, `claude/.claude/agents/`, both rules dirs, `README.md` and `docs/*.md` for `stage-all`, `stage all`, `git add -A`, `path-scoped stag` and other staging phrasings]
47. Row 26's answer was given at ~2,350 changed lines, and the round-2 Stop-reason edits raised the projection to ~2,450. Row 53 records the engineer's answer at ~2,450. [verified: rows 26 and 53; the round-2 `Projected diff:` line, ~1,800 + ~470 + ~180]
48. The engineer accepted the session's proposed rewrite of the Stop-hook reason's text before its disable sentence. The session relayed to the engineer the text M3 quotes before `To disable this on this machine:`. [engineer-verified: "yes the rewrite sounds good."]
49. From a feature branch with work pending, the final sentences "Want me to commit this and deploy it?" and "Want me to commit this and push it to main?" each match `FIRE_RE` and nothing in `EXCLUDE_RE`, so the hook fires on both. Its default-branch silence keys on where HEAD is, not on what the question names. The reason's carve-out is the only instruction in the reason that routes either question back, by deferring to CLAUDE.md for it, and whether the agent follows it is not measured (row 50). The CLAUDE.md lines it points at are the Shipping bullet's scope sentence, which M5 adds (not yet in the tree), and Engineering Judgment's confirm-beyond-the-change bullet (`claude/.claude/CLAUDE.md:26`). [verified: hand trace of `advance-past-commit-stall.sh:161-167`, `:177-196`, `:233-240`; `claude/.claude/CLAUDE.md:26`]
50. An agent that receives the reason after a stated hold, after a force-push question, or when CLAUDE.md requires a confirmation still asks rather than continuing. Not measured. Dispatch 1's asserts pin only the carve-out's wording. [unverified]
51. The engineer accepted adding a staging sub-bullet to M5's Shipping bullet, so the staging limit the Stop reason drops (row 46) survives in always-loaded text. The question named the text ``Stage files by path, never with `git add -A`.``, which row 62 widens. [engineer-verified: "Include it (Recommended)"]
52. The fork rule leaves both the Stop reason and the statement, and stays in Agent Core's always-loaded `CLAUDE.md:85`, which a placement test pins. Gate 4 skips a Stop payload carrying `agent_type`, and whether a fork's payload carries it is unverified. A fork whose payload lacks it would receive the reason with no fork limit in the harness text. [verified: `claude/.claude/CLAUDE.md:85`; `test_global_claude_md_groups.py:93-97`; `advance-past-commit-stall.sh:116-127`; `_lib.sh:3438`]
53. The plan still ships as one PR at ~2,450 changed lines. [engineer-verified: "yes proceed."]
54. The projection is now ~2,675 changed lines, above the ~2,450 row 53's answer was given at. Rows 48 and 51 cover only the reason's wording and the staging sub-bullet. The rest of the growth traces to reviewer findings, and no engineer quote covers it: the three helpers moved into `helpers.py`, the spawn-budget tests, the ignore test, the relocated-config-dir scenario and the exclusion corpus rows. Rows 25, 26 and 53 record the engineer's one-PR answers at their own figures, ~1,950, ~2,350 and ~2,450, and none was given at ~2,675. [unverified]
55. Engineering Judgment's always-loaded bullet requires flagging and confirming any destructive or irreversible action, and a placement test pins it to Agent Core. [verified: `claude/.claude/CLAUDE.md:26`; `test_global_claude_md_groups.py:87`]
56. Verification drops its former step 7, the probe that would have observed row 18 by appending a line to the engineer's config file. [engineer-verified: "Drop step 7 (Recommended)"]
57. With M5's text, `claude/.claude/CLAUDE.md` comes to about 30,368 bytes and 182 lines, under its 30,720-byte and 200-line limits, with about 352 bytes to spare. Today the file is 30,640 bytes and 180 lines, and the `:176-178` block M5 replaces is 1,138 bytes. M5's text is 866 bytes on five lines: an earlier measurement gave 805 bytes for the text with the earlier staging line, and the widened staging line adds 61, at 111 bytes against 50. `check-claude-md-length.sh` re-checks the file at commit. [verified: `check-claude-md-length.sh:59`, `:123`, `:131`; `wc -lc claude/.claude/CLAUDE.md` gives 180 lines and 30,640 bytes; `sed -n 176,178p claude/.claude/CLAUDE.md | wc -c` gives 1,138; `wc -lc` of M5's fenced text, with its two-space indent stripped, gives 5 lines and 866 bytes, and its staging line is 111 bytes] [unverified: the 805-byte earlier measurement, the 50-byte earlier staging line and their 61-byte difference]
58. The Stop hook's jq-absent silent allow already has a test, `test_jq_absent_exits_zero_empty_stdout`, which Dispatch 1 moves onto `isolated_home`. [verified: `test_advance_past_commit_stall.py:1024-1035`]
59. `git check-ignore` matches a directory-only rule such as `claude/.claude/.architect-consult-latch.d/` against a file path inside the directory, but not against the bare directory path when the checkout holds no such directory. A test must therefore check created files, not top-level entries. [verified: `git check-ignore -v claude/.claude/.architect-consult-latch.d/x` matches `.gitignore:157`, while the same command on the bare directory path matches nothing in a checkout without that directory; `test_record_session_config_keys.py:597-615` checks a file inside each directory]
60. Sourcing `_lib.sh` and calling `_config_enabled` spawn neither jq nor git. After M3, a Stop run whose final sentence matches no fire pattern therefore spawns exactly one jq, the six-field read, and no git. The compact hook on a non-compact `.source` spawns one jq, the `.source` read, and no git. [verified: `advance-past-commit-stall.sh:48-196`; `restore-authorization-boundary-on-compact.sh:39-45`; Grep of `_config.sh` for jq or git calls, none; `test_announce_autonomous_shipping.py:248-261`, whose unarmed run records no spawn through `_lib.sh` and one `_config_enabled` call]
61. Before this change, a handoff or brief filed a commit, push or PR step under §3 or §6 only with the resumer's resolver exit as its precondition. After M5's exception, any such step filed with no stated hold is pre-authorized for whoever resumes the file. [verified: `handoff/SKILL.md:118`, `brief/SKILL.md:91`]
62. The engineer chose to widen the staging sub-bullet from the one spelling `git add -A` to the property it stands for. The text M5 quotes is the plan's own wording under that choice, and the label does not cover it word for word. [engineer-verified: "Widen to the property (Recommended)"]
63. `EXCLUDE_RE` stays unchanged, and row 49 and Out of scope keep disclosing the target gap. [engineer-verified: "Keep the disclosed gap (Recommended)"]
64. A resumer that finds a filed step's precondition unmet asks the engineer before that step rather than running it, which fails toward not shipping. Not observed. [unverified]

## Critical files

Line numbers below are merge-base lines. Once an earlier edit in the same file shifts them, locate each site by the text quoted beside it.

Run Dispatch 1, then 2, then 3, each through `code-writer`, with `/code-review` and a commit after each one.
- Dispatch 1 goes first, because it removes every reader of the `autonomous_shipping` key.
- Dispatch 2 goes second, because it deletes that key's schema row and changes every enforcement-critical count, which needs the readers gone.
- Dispatch 3 goes last. It shares no file with the other two, and its cost figure measures the Stop hook Dispatch 1 leaves.

Between commits, prose in a later dispatch's files can lag an earlier dispatch's change. The PR lands all three together.

**Before Dispatch 1:** the session commits this plan file (plan-it Step 7).

**Dispatch 1: hooks, lib, their tests and the security doc (M1, M2, M3, M4, M6).** Verify with Verification steps 1-4. In every file it edits, update any docstring or comment that names a retired function, key, fixture or deleted test.
- Delete `claude/.claude/scripts/autonomous-shipping-active.sh`, `claude/.claude/scripts/tests/test_autonomous_shipping_active.py`, `claude/.claude/hooks/announce-autonomous-shipping.sh` and `claude/.claude/hooks/tests/test_announce_autonomous_shipping.py`.
- `claude/.claude/settings.json`: delete the `startup|resume|clear` block at `:183-192`.
- `claude/.claude/hooks/_lib.sh`:
  - Delete `:3348-3398`.
  - Apply M2 to `:3431-3439`.
  - In `_lib_permission_prompt_tracking_active`'s comment, delete the sentence that compares it with `_lib_autonomous_shipping_active` (`:3447-3450`).
- `claude/.claude/hooks/advance-past-commit-stall.sh` and `claude/.claude/hooks/restore-authorization-boundary-on-compact.sh`: M3.
- `.gitignore`: add `claude/.claude/.commit-stall-block.d/` and `claude/.claude/.commit-stall-block.log` to the hook-state block at `:151-181`, after `.code-writer-self-review.log` (`:160`). `:90` stays.
- `claude/.claude/tests/helpers.py`: add three helpers, each moved rather than copied (Reuse):
  - `install_recording_shims`, from the announce test's `_install_recording_shims` (`test_announce_autonomous_shipping.py:215-231`), before that file is deleted. It makes `tmp_path/recording-shims` with a bare `mkdir()`, so each test calls it once.
  - `check_ignore` and `assert_ignored_by_repo_gitignore`, from `test_record_session_config_keys.py:553-567`.
- `claude/.claude/hooks/tests/test_record_session_config_keys.py`: delete the two definitions at `:553-567` and import both from `helpers`.
- `claude/.claude/hooks/tests/conftest.py`:
  - Delete `armed_home` (`:488-493`).
  - `shipping_statement` (`:510-520`) becomes a fixture on `isolated_home` that returns the static string from `run_autonomous_shipping_statement(isolated_home)`.
  - Its three remaining callers compare against that string instead of calling it with a root: `test_restore_authorization_boundary_on_compact.py:250` and `:259`, and the compact-only statement test in `test_autonomous_shipping_grant_boundary.py` (today `:295`). `test_advance_past_commit_stall.py:1184` stops using it, per that file's bullets below.
- `claude/.claude/hooks/tests/test_lib_config_gates.py`:
  - Delete `:94-115`, a comment and the unused `_autonomous_shipping_sentinel_present` helper.
  - Delete `:286-635`: the sentinel and active classes, their helpers `_autonomous_shipping_sentinel_present_status` and `_autonomous_shipping_active`, and their comments. Drop any import left unused.
  - The module docstring (`:1-3`) drops the two deleted functions. The comment above `TestAutonomousShippingStatement` (`:637-640`) drops the gate the function no longer has.
  - In `TestAutonomousShippingStatement`, call the function with no argument, on a home with no sentinel. Then:
    - Drop the four script-tied phrases: `autonomous-shipping-active.sh`, `exits 0 here`, `Re-run that script before pushing to confirm`, `In a different repository, run that script there before relying on this`.
    - Drop the five limit phrases: `never to the default branch`, `merging stays human-only`, `force-pushing`, `returns its work to its dispatcher and does not ship`, `every other irreversible action still need the engineer's in-session confirmation`.
    - Add `The scope and limits CLAUDE.md sets for shipping still apply`.
    - Assert `autonomous-shipping-active.sh` is absent. That assertion is one of Verification step 4's allowed hits.
  - Delete the three `test_silent_*` tests.
  - `TestAutonomousShippingRootForCwd` is unchanged.
- `claude/.claude/hooks/tests/test_autonomous_shipping_grant_boundary.py`:
  - Drop the announce hook, the module docstring's mention of it (`:1-7`), and the `statement_root` field.
  - Replace `armed_home` with `isolated_home` in `_build_scenario`, `test_statement_is_printed_only_for_an_eligible_root` and the new Stop test below.
  - Replace `_stop_statement` (`:256-274`) with `_stop_hook_fired`, which returns whether the hook blocked. `_stop_statement` slices the reason at `AUTONOMOUS_SHIPPING_STATEMENT_LEAD`, so once the lead is absent, `find`'s -1 makes it return the reason's last character. The edits that follow:
    - Drop `HOOK_STATEMENTS` (`:277-281`) and the `hook_name` parametrization.
    - `test_statement_is_printed_only_for_an_eligible_root` runs the compact hook alone.
    - A new `test_stop_hook_fires_only_for_an_eligible_root` asserts, over the same scenarios, that the Stop hook fires exactly when `scenario.granted`.
  - Drop the scenarios built on the sentinel, the TOML key or the optout.
  - Add three granted scenarios that pin row 3 at the hook level: a committed `.claude/autonomous-shipping-optout`, an `autonomous_shipping = false` row in the config file, and a leftover `autonomous-shipping-required` file in the config dir. None asserts on stderr, where the config row warns (row 4). Each plants its file or row on one line, and no scenario id, comment or docstring spells a retired name, so the file keeps exactly three Verification step 4 hits.
  - Add one granted scenario whose `env_overrides` (`:50`) sets `CLAUDE_CONFIG_DIR` to an existing absolute directory, so both hooks run under a relocated config dir.
  - The `empty-home` scenario keeps `CLAUDE_CONFIG_DIR` but writes no sentinel. Every other root-eligibility scenario stays.
- `claude/.claude/hooks/tests/test_advance_past_commit_stall.py`:
  - Replace `armed_home` with `isolated_home`.
  - Delete `repo_with_committed_optin`, `test_machine_file_present_no_optout_fires` (`:378`), `test_machine_file_present_repo_optout_silent`, `test_repo_committed_optin_with_machine_file_absent_stays_silent`, `test_legacy_home_claude_sentinel_fires_via_fast_path_union` and `test_unreadable_config_keys_psv_does_not_fire`.
  - `test_machine_file_absent_silent` (`:365`) becomes a test that the hook fires with no shipping configuration at all.
  - Drop the sentinel file from the two `CLAUDE_CONFIG_DIR` tests (`:421`, `:439`), and rename `test_config_dir_sentinel_fires` to say the hook fires under a relocated config dir.
  - In the exit-3 and exit-4 case tests (`:488`, `:533`), remove the `_lib_autonomous_shipping_sentinel_present` stubs and write a `commit_stall_block = false` row, so they pin that the row is not honored on a degraded schema (row 22).
  - `:817`: take the text after `To disable this on this machine: ` and drop its one trailing `.`, so the test still runs the emitted command as written (row 38). This holds after the reword, because the disable sentence stays last.
  - `:1170-1171`: replace the `/code-review` and `/ready-for-review` asserts with one assert that the reason starts with `Do not ask the engineer for permission to continue at a natural stopping point, unless CLAUDE.md, the skill you are following, or a hold the engineer stated requires the question.`
  - `:1174-1187`: rename the test to `test_reason_carries_no_shipping_statement` and drop its `shipping_statement` fixture. Then:
    - Assert that `AUTONOMOUS_SHIPPING_STATEMENT_LEAD`, imported from `helpers`, is not in the reason.
    - Keep the `"gh pr merge" not in result["reason"]` assert (`:1185`), the only guard that the reason never names a merge.
    - Drop the `/code-review` and `/ready-for-review` asserts (`:1186-1187`).
  - `:1190-1201`: rename the test to `test_reason_ends_the_turn_when_a_required_question_applies` and keep its docstring's first sentence only. Assert that the reason holds `If one does, end your turn.` and `Otherwise continue with your task's next step, say the task is complete, or say what is blocked.`
  - Add a test, parametrized over two final sentences, that the hook fires on `dirty_repo` for a question whose target lies outside the grant: `Want me to commit this and deploy it?` and `Want me to commit this and push it to main?` (row 49). Each run blocks, and its reason holds the carve-out sentence the `:1170` assert pins. The docstring says the predicate does not read the target, so the carve-out is the reason's only instruction routing it back, and it defers to CLAUDE.md for the routing; whether the agent follows it is not measured (rows 45, 50).
  - Add a spawn-budget test on `install_recording_shims`' `PATH` (row 60). A payload whose final sentence matches neither `VERB_RE` nor `FIRE_RE` logs exactly one `jq` and no `git`. Its positive control, a payload that fires and logs `git`, is a test of its own.
  - Add ten rows to `CORPUS` (`:153-220`), each expecting silence. Each final sentence matches `FIRE_RE` through a commit or push verb and carries exactly one `EXCLUDE_RE` alternative (`advance-past-commit-stall.sh:167`), so a row fails once its alternative is dropped:
    - `merge`: `Want me to commit this and merge it?`
    - `--force`: `Want me to push this with --force?`
    - `force-push`: `Want me to commit this and force-push it?`
    - `reset --hard`: `Want me to commit this after a reset --hard?`
    - `close the PR`: `Want me to push this and close the PR?`
    - `delete the branch`: `Want me to push this and delete the branch?`
    - `failing`: `Want me to commit the fix for the failing test?`
    - `failed`: `Want me to commit this even though the build failed?`
    - `error`: `Want me to commit the fix for this error?`
    - `blocked`: `Want me to push this while the release is blocked?`
  - Keep `:166`, and change its comment from "merge excluded" to say its sentence never matches `FIRE_RE`. The `merge` row above is the one that discriminates. `anyway` already has a row (`:168-170`), and the `ISSUE_QUOTE_QUESTION` rows are the fire-side controls.
  - Add a test that the hook's state stays out of git (rows 37, 59):
    - List every file under the config dir recursively before and after one firing run, and take the difference.
    - Assert the created set holds `.commit-stall-block.log` and the session's file under `.commit-stall-block.d/`, so the check cannot pass on an empty set.
    - Map each created file onto `claude/.claude/<relative path>`, and check it with `check_ignore` and `assert_ignored_by_repo_gitignore` under `git_isolation_env`'s environment.
    - Keep one negative control: `claude/.claude/.commit-stall-block`, a bare name, is not ignored, as `test_the_ignore_rule_does_not_reach_a_bare_name` checks for its own log.
  - Update the module docstring (`:3-10`), docstrings that cite step numbers or line ranges, and any docstring or comment that names a retired function, key, fixture or deleted test. The known sites of the last kind are `:7`, `:366`, `:457`, `:492`, `:503`, `:541` and `:573-575`.
- `claude/.claude/hooks/tests/test_restore_authorization_boundary_on_compact.py`:
  - Replace `armed_home` with `isolated_home`.
  - Delete the optout, unarmed-machine and sentinel-location tests (`:269`, `:290`, `:296`, `:312`). Delete the degraded-schema control (`:353`), which the inversion below makes redundant.
  - `:238`: take the expected boundary text from a run whose payload has no `.cwd`, not from an unarmed home.
  - `:261`: keep the empty-message exit tested, with the kill switch on and a non-repo `.cwd`.
  - `:327`: rename it, and assert the statement alone with no boundary text (row 35).
  - `:337-351`: invert so the statement survives (row 23). Its row-missing case removes `authorization_boundary_restore`, because `config_schema_text_without_key` cannot remove a row that no longer exists.
  - Add three cases with an eligible process cwd (row 36):
    - A number `.cwd` and an array `.cwd` each emit the boundary text and no statement. Their docstrings say they pin end behavior, not the `(.cwd | type) == "string"` guard (`:70`), because the root guard rejects both values with or without it.
    - With `git` removed from `PATH`, the hook still emits the boundary text.
  - Add a spawn-budget test on `install_recording_shims`' `PATH` (row 60). A non-compact `.source` logs exactly one `jq` and no `git`. Its positive control, a compact payload whose `.cwd` names `shipping_repo` and which logs `git`, is a test of its own.
  - Update the class docstring (`:228-229`) and the non-compact-sources docstring (`:371`).
- `claude/.claude/hooks/tests/test_cursor_adapter_gate_matrix.py`: delete the entries at `:534` and `:979`.
- `claude/.claude/hooks/tests/test_marker_lib.py:1940-1942`: drop the clause citing the deleted `test_inactive_on_wrong_arity`.
- `docs/hooks.md`:
  - `:244`: replace "opt-in" with "on by default", and replace the `_lib_autonomous_shipping_active` condition with the root guard. Drop the optout from the kill switches.
  - Delete the announce entry (`:256-277`) except the three items below, which move under the compact entry (`:278`):
    - The Text source fact (`:257`), now naming the compact hook as the statement's only printer.
    - The Repo resolution fact (`:258`), now naming the compact and Stop hooks.
    - The known gaps that still hold, reworded:
      - `claude --agent` main session, unchanged.
      - Fork: Agent Core's rule, not the statement, tells a fork or subagent to return its work.
      - Default branch: CLAUDE.md's Shipping bullet, not the statement's prose, carries the never-to-the-default-branch rule.
      - Third-party clones: no per-repo exemption remains (row 24). The root guard checks the path's shape, not the remote's owner (row 30). Link the new `docs/security-hardening.md` section rather than restating it.
  - In `:278`, drop the mode conditional, the sentence "The statement follows `autonomous_shipping` and the repo optout." and the phrase "the same shipping statement `announce-autonomous-shipping.sh` emits", which becomes "the shipping statement". Add that for a `.cwd` the root guard rejects, the hook gives the boundary text and no statement, while CLAUDE.md's Shipping rule still applies. `test_restore_authorization_boundary_on_compact.py:277` already pins that output.
  - `:400`: drop `/autonomous_shipping`.
- `docs/security-hardening.md`: add M6's section directly above `## Required server-side branch protection` (`:541`), with rows 30-32 as its sources. For the redaction hook's scope, the section cites `docs/private-project-redaction.md` § "Opting in another repository" rather than restating it.

**Dispatch 2: config schema, both config readers, installer, and the config tests and docs (M1).** Verify with Verification steps 1-5. In every file it edits, update any docstring or comment that names a retired function, key, fixture or deleted test.
- `claude/.claude/hooks/config-keys.psv`: delete row `:92`. Remove `autonomous_shipping` from the header at `:29-31`, `:37-41`, `:48-50` and `:86-90`. The header phrase becomes exactly `The four enforcement-critical keys (`, so it and the regex at `test_migrate_legacy_config.py:800` change together.
- `claude/.claude/hooks/_config.sh`:
  - Delete the clause at `:848-849`.
  - Drop the parenthetical at `:972`.
  - `:727`: "The other four enforcement-critical keys" becomes "The other three".
- `claude/.claude/scripts/_config.py:324`: "The other four enforcement-critical keys" becomes "The other three".
- `claude/.claude/scripts/config-get.sh:16-18`: drop "matching the phrasing CLAUDE.md already uses for autonomous-shipping-active.sh".
- `claude/.claude/scripts/migrate-legacy-config.sh`:
  - `:12-13`: 22 becomes 21.
  - `:30` and `:43`: five becomes four.
  - `:35`: remove the key.
  - `:50` and `:57`: delete.
- `install.sh`:
  - `:633` and `:742`: four repo markers become three.
  - `:645`: delete.
  - `:695-696`: 22 becomes 21, and only `worktree_required` is named.
- Tests. Re-point each real-schema use of `autonomous_shipping` to a surviving key that has the property under test, and drop docstring rationale that cites shipping's safe direction. Pick the replacement this way:
  - A default-`false` bool with no legacy probe: use `permission_prompt_tracking` (`config-keys.psv:93`). Wherever the test plants a legacy file, plant that key's own, `track-permission-prompts`.
  - A `config-dir-or-home` key with probe `false`: no surviving row has one. Build it in an isolated hooks dir by rewriting `permission_prompt_tracking`'s row, and run it through `_run_with_schema` (`test_config_lib.py:91`). Each of the two cases below gets a positive control in which the planted legacy file is honored when resolution succeeds (row 39):
    - A union-branch internal failure resolves to the schema default (today `:1025`).
    - A resolution failure returns 2 with the legacy file present at `$HOME/.claude` (today `:440`).
  - Any other test whose path a sibling already covers: delete it.
- Test files:
  - `claude/.claude/hooks/tests/test_config_lib.py`, `claude/.claude/scripts/tests/test_config_py.py` and `claude/.claude/scripts/tests/test_config_parser_parity.py`: the replacements above. Counts at `test_config_lib.py:636` and `test_config_py.py:419` become "three of the four", and at `test_config_lib.py:2003` "all four".
  - `claude/.claude/scripts/tests/test_migrate_legacy_config.py`: the `:40` and `:54` enforcement-critical tables and the examples at `:508-621`. Counts at `:758` and `:827` become "other three" and "these three", and at `:795-801` and `:835` "four", with the regex at `:800` matching the new header phrase (row 38).
  - `claude/.claude/hooks/tests/test_install_sh_machine_level_opt_ins.py`:
    - `:265-294`: drop the `autonomous-shipping-required` asserts.
    - Delete `test_autonomous_shipping_key_written_on_y`, and `:301`'s "four of the real promptable rows" becomes "three".
    - `:369`: five prompts.
    - `:392-424`: re-aim the canary at the permissive direction that remains, and retitle it to match. On a machine with `worktree_required = true`, a real-pty `n` writes `worktree_required = false` (row 40). The prompt-order comment (`:409-413`) drops `autonomous_shipping`. The exact-equality assert (`:418-424`) expects only the `worktree_required` line. The file then keeps no Verification step 4 hit.
  - `claude/.claude/hooks/tests/test_install_sh_sentinel_inventory.py`: `:206` four becomes three, and `:454-499` re-points to a surviving non-probing key.
  - `claude/.claude/hooks/tests/test_doc_counts.py:438`: "which five keys" becomes "which four keys".
  - One docstring line in each of two Dispatch-1 files: `test_lib_config_gates.py:123`'s "five enforcement-critical keys" becomes "four", and `test_advance_past_commit_stall.py`'s "matching the other four enforcement-critical keys' equivalent tests" (`:748`) becomes "other three".
- `claude/.claude/hooks/tests/config-schema-audit.md`:
  - `:3`: 22 becomes 21.
  - `:7-9` and `:136`: four enforcement-critical keys.
  - Delete the `### autonomous_shipping` section (`:62` to the next `###`).
  - Rewrite `:141-150`, because the exit-3/4 direction is now observable end to end (row 22). Rewrite `:170-173`, because the statement no longer follows a key.
  - `:183` and `:224`: six rows become five. `:244`: "sixth of the six" becomes "fifth of the five".
- `docs/config-file.md`:
  - `:6`, `:270`: drop "autonomous shipping".
  - `:22`: use a surviving key in the example.
  - `:61`: delete the table row.
  - `:83-84`, `:162-166`, `:217`, `:332`: four enforcement-critical keys.
  - `:112-127`, `:280`: `worktree_required` only.
  - `:148`, `:180`, `:265`: 22 becomes 21.
  - `:150`: the six keys `install.sh`'s pre-migration writer wrote become five.
  - `:215`: six promptable keys become five.
  - `:303`, `:308-318`: drop the `advance-past-commit-stall.sh` measurement bullet and its memo-hit sentence, which describe a call shape that no longer exists. Dispatch 3 records the new figure in `docs/commit-stall-block.md`.

**Dispatch 3: instruction prose and user docs (M5, M6).** Verify with Verification steps 1, 3, 4 and 6.
- `claude/.claude/CLAUDE.md:176-178`: M5's text.
- `claude/.claude/hooks/tests/test_global_claude_md_groups.py`:
  - `:102-106`: the `autonomous-shipping` placement's substring becomes exactly `**Autonomous shipping: a request to do work is the ask.**`.
  - Add five `"main"` placements, one per sentence below:
    - ``Being asked to make the change is that ask: run `/code-review`, commit, run `/ready-for-review`, and open the PR without pausing for permission.``, the review recipe the Stop reason no longer pins.
    - `The ask covers only committing, a plain push of the current branch (never to the default branch), and opening a PR.`
    - `The ask does not extend to force-pushing or any other irreversible action.`
    - ``Stage only the paths this task changed, never the whole tree (`git add -A`, `git add --all`, `git add .`).``
    - `A hold the engineer states in-session still wins.`
- `claude-skills/skills/handoff/SKILL.md:118`, `claude-skills/skills/brief/SKILL.md:91`: M5's exception.
- `claude-skills/skills/tests/test_skills.py`:
  - `:2065-2096`: rename both tests to drop `_when_shipping_is_active`, for example to `_with_no_hold`, and drop the resolver from their docstrings. Then:
    - Drop the `autonomous-shipping-active.sh`, `exit 0`, `precondition` and `resumer's own` asserts.
    - Replace the `only when` and `no hold` asserts, which the new sentence no longer holds, with one assert on `unless the engineer stated a hold`, which keeps the exception conditional.
    - Keep the `never to the default branch` and section-number asserts.
  - `:7431-7435`: name only `worktree_required`.
- `README.md`:
  - `:74`, `:521`: the opt-out never silences the shipping statement, with no mode conditional.
  - `:204`: drop "opt-in" and the conditional.
  - `:367-389`: rewrite the section per M6, one content point per item:
    - The agent commits, pushes the feature branch and opens the PR without pausing, and stops before merge.
    - The Stop hook force-continues a permission-question stall in an eligible repo on a non-default branch.
    - The compact hook restates the statement.
    - `commit_stall_block = false` turns off the Stop hook machine-wide, and no per-repo exemption remains.
    - No setting turns off the CLAUDE.md rule. CLAUDE.md tells the agent that a stated in-session hold wins, and the Stop reason carves a stated hold out. Whether the agent honors it under the Stop hook is not measured (rows 45, 50); link `docs/commit-stall-block.md`.
    - Open sessions need a restart after the pull.
    - A link to `docs/security-hardening.md`'s new section covers the rest.
    - Drop both snippets, the announce paragraph and the optout paragraph.
- `docs/commit-stall-block.md`:
  - Activation (`:3-9`): on by default for every stow consumer.
  - Delete the `:22` predicate bullet.
  - `:26`: describe the reason rather than quote it, so the hook stays its one home. One point per sentence:
    - The reason no longer opens with the statement or names a step sequence.
    - It tells the agent not to ask permission to continue at a natural stopping point unless CLAUDE.md, the skill it follows or a hold the engineer stated requires the question.
    - When one does, the reason tells the agent to end its turn. Otherwise it tells the agent to continue, say the task is complete, or say what is blocked.
    - It ends with the disable command.
    - CLAUDE.md's Shipping bullet carries the push scope, the staging limit and the hold, and Agent Core carries the fork rule.
  - Beside that description, state the two gaps the reason leaves:
    - The reason carries no fork limit. Gate 4 skips a payload carrying `agent_type`, and a fork whose Stop payload lacks it, which is unverified, receives the reason with only Agent Core's rule to stop it (row 52).
    - Neither direction is measured: whether the reason moves a stalled agent forward (row 45), and whether an agent with a stated hold, a force-push question or a CLAUDE.md-required confirmation still asks (row 50).
  - `:30`: drop the `autonomous_shipping` clause, and point at the degraded-schema limitation below for the one case the switch is not honored.
  - How to disable: drop the per-repo and "Everywhere" blocks (`:42-48`).
  - `:50`: the last sentence names CLAUDE.md's Shipping rule instead of the SessionStart statement.
  - `:72`: add that every consumer with the hook on now appends `phrasing-drift` lines, which grow the unrotated log.
  - `:76`: name only the compact hook's statement.
  - `:79`: add that this unbounded git path now runs for every stow consumer with `commit_stall_block` on, on turns whose final sentence matches the fire pattern, and that the Stop registration carries no `timeout` (`settings.json:294-301`).
  - Known limitations, one bullet each:
    - The degraded-schema gap (row 22).
    - The target gap: from a feature branch with work pending, a compound question or one naming the default branch fires, and the reason's only instruction to route it back is its carve-out, which defers to the Shipping bullet's scope sentence and `claude/.claude/CLAUDE.md:26`; whether the agent follows it is not measured (rows 45, 50). Name the two questions Dispatch 1's tests pin (row 49).
    - One measured per-Stop figure for the post-Dispatch-1 no-fire path against the <100ms budget (`docs/config-file.md:323`), taken as Verification step 6 specifies, with that step's idle-machine caveat beside it.
- `CHANGELOG.md`: add one `### Changed` entry under `[Unreleased]`, in the existing bold-lead plus sub-bullet shape. It covers:
  - What was removed.
  - The Stop hook now fires for every stow consumer with `commit_stall_block` on.
  - The Stop hook's reason no longer restates the shipping statement or a commit recipe. It tells the agent not to ask permission to continue at a natural stopping point unless CLAUDE.md, the skill it follows or a stated hold requires the question.
  - The reason no longer carries the fork limit, so a fork whose Stop payload lacks `agent_type` receives it with only Agent Core's rule to stop it. Link `docs/commit-stall-block.md`.
  - CLAUDE.md's Shipping bullet now says to stage only the paths the task changed, never the whole tree.
  - A commit, push or PR step that a handoff or brief files with no stated hold is now pre-authorized for whoever resumes it (row 61).
  - Leftover optout and sentinel files are ignored and may be deleted by hand. A leftover optout file no longer protects a repo.
  - M6's disclosures, one line each, linking `docs/security-hardening.md` for detail.
  - A **Migration:** sub-list, one item per point:
    - Delete any leftover `autonomous_shipping` row from `claude-config.toml`. Both config readers otherwise warn about it on stderr, and the warning may appear in session output (rows 4, 18).
    - `commit_stall_block = false` is the remaining switch, and it is machine-wide.
    - Nothing turns off the CLAUDE.md rule. CLAUDE.md tells the agent that a stated in-session hold wins, and the Stop reason carves a stated hold out. Whether the agent honors it under the Stop hook is not measured (rows 45, 50); link `docs/commit-stall-block.md`.
    - A handoff or brief filed before this change may name the retired resolver as a step's precondition. Checking it now runs a missing script and gets a non-zero status, so the precondition is unmet, and the resumer is expected to ask before that step, which would fail toward not shipping (rows 33, 64).
    - The dangling `~/.claude/hooks/announce-autonomous-shipping.sh` and `~/.claude/scripts/autonomous-shipping-active.sh` links are expected to be inert and are removable by hand (row 34).
    - Cursor-adapter users run `cursor-adapter.py check`, which is expected to report the stale announce entry, and follow its fix.
    - A copied, not stowed, `settings.json` is expected to need its announce block removed.
    - Restart open sessions.

**PR body** (no repository file; `/pr-description` writes it). Besides the summary, it states:
- Neither direction of the Stop reason is measured: moving a stalled agent forward (row 45) and still asking when a carve-out applies (row 50).
- Any commit, push or PR step a handoff or brief files with no stated hold is now pre-authorized for whoever resumes it (row 61).

Reuse:
- `run_autonomous_shipping_statement` and `AUTONOMOUS_SHIPPING_STATEMENT_LEAD` (`claude/.claude/tests/helpers.py:998-1016`) work unchanged with no argument, and `run_autonomous_shipping_root_for_cwd` (`:1019`) is untouched.
- `hook_beside_degraded_schema` and `config_schema_text_without_key` already build the degraded-schema fixtures, and `build_path_without` builds the git-absent `PATH`.
- `_run_with_schema` (`test_config_lib.py:91`) runs a synthetic schema. `_isolated_hooks_dir_with_legacy_polarity_override` (`:138`) is the model for rewriting one row.
- `check_ignore` and `assert_ignored_by_repo_gitignore` (`test_record_session_config_keys.py:553-567`) move to `helpers.py`, beside `git_isolation_env` (`helpers.py:3463`), and both test files import them, per `test-conventions/SKILL.md` § "6. Test data".
- `_install_recording_shims` (`test_announce_autonomous_shipping.py:215-231`) moves to `helpers.py` as `install_recording_shims` for the Stop and compact spawn tests, by the same rule.

Projected diff: ~2,675 lines, about 1,670 of them deletions: ~2,005 in Dispatch 1, ~470 in Dispatch 2, ~200 in Dispatch 3. Dispatch 1's ~25 lines over the previous ~1,980 are the ten corpus rows, the `:166` comment and the two positive-control tests split out. Rows 26 and 53 record the one-PR answers at ~2,350 and ~2,450, and row 54 the growth since, which no engineer answer covers.

## Verification

1. After each dispatch, `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes. It widens on its own when the diff calls for it, so the full suite is not run by hand.
2. After Dispatches 1 and 2, `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` passes.
3. After each dispatch, `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` passes.
4. After each dispatch, the retired-symbol check returns only the hits allowed at that point. The pattern spells out each retired name and matches the key and the fixture only as whole words, so no kept identifier can match it. A whole word can also follow an escaped `\n` inside a string literal, so the lead class accepts a literal backslash-n:

   ```bash
   git grep -nE 'autonomous-shipping-active|announce-autonomous-shipping|_autonomous_shipping_active|_autonomous_shipping_sentinel_present|autonomous-shipping-optout|autonomous-shipping-required|repo_with_committed_optin|(^|[^A-Za-z0-9_]|\\n)(armed_home|autonomous_shipping)([^A-Za-z0-9_]|$)' -- . ':!.claude/plans/' ':!CHANGELOG.md' ':!docs/design-decisions/' ':!evals/fixtures/'
   ```

   After Dispatch 3, the command above, run over the whole tree, returns exactly these eight lines:
   - `.gitignore:90`.
   - `test_branch_records_lib.py:385`, `:388` and `:533` (row 21).
   - In `test_autonomous_shipping_grant_boundary.py`, three lines: the one line each that plants the optout file, the config row and the sentinel file for Dispatch 1's three leftover-state scenarios. Scenario ids, comments and docstrings in that file spell no retired name. The count follows from those three plants, each a single line today (`:66`, `:155`, `:176`).
   - In `test_lib_config_gates.py`, the one assertion that `autonomous-shipping-active.sh` is absent from the statement.

   After Dispatches 1 and 2, the same pattern runs with the pathspecs after `--` replaced by the files that dispatch edits and keeps:
   - After Dispatch 1, it returns the five Dispatch 1 lines above: `.gitignore:90`, the three grant-boundary lines and the `test_lib_config_gates.py` assertion.
   - After Dispatch 2, it returns only the `test_lib_config_gates.py` assertion, which the scope reaches because Dispatch 2 edits one docstring line in that file.

   Before any dispatch, the same regex run through the Grep tool hits 37 files outside the excluded paths. Each is a file Critical files edits or deletes, or `test_branch_records_lib.py` (row 21). The hits include `test_branch_records_lib.py:385`, `:388` and `:533`, and `test_install_sh_machine_level_opt_ins.py:410` and `:418`, which Dispatch 2 edits.

   Two seeded checks pipe lines through `grep -E` with the same pattern:
   - `_lib_autonomous_shipping_statement`, `_lib_autonomous_shipping_root_for_cwd`, `run_autonomous_shipping_statement`, `run_autonomous_shipping_root_for_cwd`, `autonomous_shipping_no_waiver`, `test_autonomous_shipping_grant_boundary.py` and `unarmed_home`, one per line, print nothing.
   - The line `x\nautonomous_shipping = true`, printed with `printf '%s\n'` so its backslash stays literal, prints.
5. After Dispatch 2, a case-insensitive multiline search (the Grep tool with `multiline: true` and `-i`) over `docs/` without `docs/design-decisions/`, `claude/.claude/` and `install.sh` returns nothing for each stale-count shape:
   - `\bfive\b[^\n]*(\n[^\n]*)?enforcement-critical` and `enforcement-critical[^\n]*(\n[^\n]*)?\bfive\b`, since the enforcement-critical set is now four.
   - `\b(six|sixth)\b[^\n]*(\n[^\n]*)?(promptable|pre-migration)` and `(promptable|pre-migration)[^\n]*(\n[^\n]*)?\b(six|sixth)\b`, since the pre-migration promptable set is now five.
   - `\b(other|these) four\b[^\n]*(\n[^\n]*){0,2}enforcement` and `enforcement[^\n]*(\n[^\n]*){0,2}\b(other|these) four\b`, since "the other N" is now three.
6. After Dispatch 3, the per-Stop figure in `docs/commit-stall-block.md` is under 100ms. If it is not, the session stops and reports before committing. The figure comes from this run, so it measures the post-change no-fire path and not an early exit:
   - Run `bash <worktree>/claude/.claude/hooks/advance-past-commit-stall.sh`, the worktree copy, because `~/.claude/hooks` resolves to the main checkout's pre-change hook.
   - Point `CLAUDE_CONFIG_DIR` and `HOME` at a fresh temporary directory that exists and holds no `claude-config.toml`.
   - The payload carries a valid `session_id`, a `prompt_id` the hook has never recorded, a `permission_mode` other than `plan`, and no `agent_type`. Its `last_assistant_message` is non-empty, and its final sentence matches neither `VERB_RE` nor `FIRE_RE`, such as "Done."
   - A control run on the same setup, with the verb-only final sentence "Let me know if you have questions.", appends exactly one `phrasing-drift` line to the temporary `.commit-stall-block.log`. That shows the measured run reaches the regex step.
   - The recorded figure is the median of 40 runs, the iteration count `docs/config-file.md:300-301` uses. Beside it, the doc says whether the machine was otherwise idle, which that method assumes and a machine running parallel sessions may not be.
7. Before each commit, `/code-review` runs. Per `.claude/rules/review-pipeline-dispatch.md`, it also routes `/skill-review` for the two SKILL.md edits, which a hook enforces at commit, and `claude-hook-review` for `config-keys.psv`.

## Out of scope

- Preserved records stay as written (Axis 3):
  - `CHANGELOG.md`'s existing entries.
  - Every `docs/design-decisions/*.md`, including `autonomous-shipping-state-in-hook-context.md`, which `advance-past-commit-stall.sh:87`, `:149` still cite for the scan-window rationale.
  - Every plan under `.claude/plans/` other than this one.
  - `docs/cost-levers-considered.md:640`'s measurement narrative.
- `claude/.claude/hooks/require-diff-size-prompt.sh:102` and `claude/.claude/hooks/deny-default-branch-push.sh:6`: their wording stays true with the mode always on, and Axis 1 leaves both files untouched.
- `claude-skills/skills/plan-it/SKILL.md:172` (row 15) and `evals/fixtures/explore-recall-cases.json` (row 16).
- `evals/measure_main_session_rule_adherence.py:2643`: it still names the Shipping bullet by the term M5 keeps.
- `.gitignore:90` (row 17). Dispatch 1 adds two other lines to the file.
- `claude/.claude/scripts/tests/test_branch_records_lib.py` (row 21).
- `claude/.claude/tests/helpers.py` beyond the three helpers Dispatch 1 moves into it: its statement and root helpers work unchanged (see Reuse).
- Moving the other `git check-ignore` tests onto the moved helpers, such as `test_reconcile_project_plugins.py:2339` and `:2349`. They run `-q` with no rule-source check, and converging them is beyond the Ask.
- The `config-dir-or-home` resolution path with `legacy-probe-on-resolution-failure: false`, in `_config.sh` and `_config.py`. It loses its last real key, but removing that branch of the schema grammar is a refactor beyond the Ask. Dispatch 2 keeps it tested through a synthetic row (row 39).
- Per the scope calls rows 28 and 42 record: an engineer-visible notice shown while a leftover optout or sentinel file is present. It needs new hook code, and row 3's "Silently ignore" covers leftover files. Known gap: an engineer who relied on an optout gets no in-session signal, and the CHANGELOG is the only notice.
- Per the scope calls rows 28 and 42 record: a Stop-hook carve-out that does not force-continue a check-in about a remote the engineer does not own or an instruction the engineer did not give. It needs a new fire-predicate design, because the hook reads no ownership or provenance signal today. Known gap: such a check-in can be force-continued once per `prompt_id`.
- A Stop-hook fire predicate that reads what the question targets, or an `EXCLUDE_RE` extension for the two pinned shapes (row 63). The predicate cannot enumerate every action outside the grant, and row 48's reason tells the agent to route such a question through its carve-out. Known gap: from a feature branch with work pending, a compound question or one naming the default branch fires (row 49).
- A hung git call on the compact hook's statement path still cancels the boundary text through the 10 s timeout. The hook header already records the shared budget (`restore-authorization-boundary-on-compact.sh:22-23`). Bounding git on stock macOS needs a timeout that `_lib_capped` lacks there (`advance-past-commit-stall.sh:40-41`). Known gap: after M3 every consumer's compaction runs that git call.
- A `timeout` on the Stop hook's registration (`settings.json:294-301`). The Ask calls for no registration change, and a timeout value would need a cited source. Known gap: on turns whose final sentence matches the fire pattern, the Stop hook's unbounded git path now runs for every stow consumer with `commit_stall_block` on, and `docs/commit-stall-block.md:79` says so.
- A behavioral measurement of the Stop reason, such as an `evals/` case. Known gap: neither the continue direction (row 45) nor the carve-out direction (row 50) is measured, and `docs/commit-stall-block.md` and the PR body say so.
- Trimming the grant-boundary matrix's denied scenarios that repeat `TestAutonomousShippingRootForCwd` once per hook: a test-shape refactor beyond the Ask.
- Renaming `_lib_autonomous_shipping_root_for_cwd`, `_lib_autonomous_shipping_statement`, `AUTONOMOUS_SHIPPING_STATEMENT_LEAD` or `test_autonomous_shipping_grant_boundary.py`: "autonomous shipping" stays the concept's name (M5).
- Any install-time or migration step that deletes leftover sentinel files, optout files or config rows (row 3).
- Silencing a leftover `autonomous_shipping` state-file row. The stderr warning both config readers print is accepted (row 4), and the CHANGELOG tells adopters to delete the row.
- Moving `_lib_autonomous_shipping_statement` out of `_lib.sh` now that the compact hook is its only caller. Leaving it is fine: `TestAutonomousShippingStatement` and `run_autonomous_shipping_statement` test it through `_lib.sh`, and it sits beside the root guard both hooks still share.