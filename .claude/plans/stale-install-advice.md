# Stop sessions from giving unconditional `./install.sh` advice

## Context

Goal: stop agent sessions in this repo from telling the engineer to re-run `./install.sh` after a pull that adds a file under `claude/.claude/**` when no re-stow is needed on that machine, and state the real rule in one authoritative place.

Ask: "you're not the first session to suggest running install.sh for these types of changes", and "I think that anything in scripts is stowed...". After two fixes were offered: "none of your suggestions really seem like the right solution but you do understand the problem which is good."

Why now: it recurred in the session that shipped PR #1290. The audience is every stow consumer, not one machine.

## Approach

Fix the instruction that teaches sessions the wrong rule, and state the real rule once. The gate `require-stow-reminder.sh` currently tells sessions every new file needs `./install.sh`, with an unconditional PR-body template. Rewrite its text to defer to a new `README.md` § "Updating after git pull", which states the rule and gives a one-line existence check for a new file. No new script.

The gate's trigger narrows (row 22): it fires on an added path under `claude/.claude/` only when that path's first component, the top-level entry, does not exist on `main`, or on a change to `install.sh`. A file added under a top-level entry that `main` already has arrives on `git pull`, so it no longer trips the gate. The deny's example line states the condition for when a re-run applies. Past CHANGELOG entries and design-decision records stay as written. This PR's own entry shows the new shape: the condition comes first and the rule is not restated.

Alternatives set aside:
- **A note beside root `CLAUDE.md:56`, or dropping the conditional CHANGELOG re-stow entry.** The engineer said the prior session's suggestions were not the right solution (row 7), and the handoff names these two as those suggestions (row 8).
- **A read-only `restow-check.py` that walks ancestors and reports `LIVE`/`RESTOW`.** The engineer called it "WAY overengineered" (row 19). It also reaches a session only through the same README or deny prose it was meant to backstop, and it answers a question that checking the file itself answers directly.
- **Have the gate compute this machine's verdict.** The gate writes for the PR body's audience, which is every stow consumer. It also fires only when the body lacks the marker, so it is absent at most of the moments when a session gives advice.
- **Normalize installs with `stow --no-folding` everywhere.** Every consumer's layout changes, and the change does not reach other installers. `--no-folding` would also make every new file need a re-run, the opposite of what the engineer expects (row 9).
- **A path-scoped `.claude/rules/` instruction.** This is another standing prose layer. Revisit it only if the bad advice recurs after this lands.
- **A real-`stow` integration test pinning stow's folding behavior.** Real stow tests exist elsewhere in the suite, but this change rewrites prose, not stow behavior. The stow manual's tree-folding section already settles the semantics (row 1).

### Assumption ledger

**Root:** Sessions in `claude-config` tell the engineer to re-run `./install.sh` after a pull that adds a file under `claude/.claude/**`. They do this even when the file's config-dir directory is a folded stow symlink and the file appears on `git pull`. Two things cause it:
- The gate `require-stow-reminder.sh`'s deny states the opposite rule unconditionally, and the CHANGELOG precedents read the same way. Whether the deny fired in the PR #1290 session is unestablished (row 11).
- The conditional rule has no current-behavior home outside CHANGELOG sub-bullets (row 13).

**Givens:**
1. GNU Stow folds a target directory that does not exist when stow runs into one symlink into the package. It links entry by entry into a directory that already exists. The vendor imposes this behavior. [verified: install.sh:92-95 comment; stow(8) "tree folding" section per the platform reviewer; `ls -ld` on every config dir on the plan author's machine this session showed `scripts/`, `hooks/`, `agents/`, `skills/`, and `rules/` all folded]
2. `install.sh` pre-creates only `~/.claude` and `~/.local/bin` as real directories. Stow folds every other target that does not exist (row 1), so `hooks/`, `scripts/` and `skills/` are links into this checkout. The PR owner's ruling is that `~/.claude/skills` is never a real directory in claude-config. Other installers stow into non-`$HOME` config dirs whose layout this repo does not control. [verified: install.sh:91-96; PR #1313 inline comment 4355402603; stow-packages.sh:6-8]
3. An agent's ad-hoc Bash call should not reference `$CLAUDE_CONFIG_DIR`. A session on the default config dir runs a literal `ls ~/.claude/<path>`. A human terminal, or a session on another config dir, uses that dir's path. The harness owns the guard. [verified: docs/worktree-bash-guard.md:24 (trigger B recorded as refused); :116-134 (zero refusals on re-run, guard not reproducible on demand); the platform reviewer's own compound Bash call was refused this round]

**Rows:**

4. `require-stow-reminder.sh`'s deny says a new file "only appears there after re-running install.sh; git pull alone does not create the symlink". It offers the template "Post-merge: run `./install.sh` to pick up this change." [verified: the `emit_deny` line at the end of require-stow-reminder.sh on `main`]
5. The hook's header premise is "there is no 'already-stowed parent covers it' case to filter out". That premise contradicts `install.sh`. `install.sh` pre-creates only `~/.claude` and `~/.local/bin`. A new top-level child of `claude/.claude/` is therefore never created by `git pull`, so it always needs a re-run. A new file nested under an already-folded directory arrives on pull. [verified: require-stow-reminder.sh:30-38; install.sh:91-96]
6. The `require-stow-reminder.sh` entry in `docs/hooks.md` says the gate fires on a new *immediate child* of `claude/.claude/`. The hook on `main` fires on an added path at any depth; row 22 narrows that. [verified: that entry and the `ADDED_PATHS` scan on `main`]
7. The prior session's two fixes are not the right solution. [engineer-verified: "none of your suggestions really seem like the right solution"]
8. Those two fixes were a "when is a re-stow needed" note beside root `CLAUDE.md:56`, and dropping the conditional re-stow entry from `CHANGELOG.md`. [unverified: relayed by the dispatching session from the handoff, not quoted]
9. The engineer expects a new file under `scripts/` to arrive on `git pull`. [engineer-verified: "I think that anything in scripts is stowed..."]
10. Sessions have repeatedly advised re-running `install.sh` for this kind of change. [engineer-verified: "you're not the first session to suggest running install.sh for these types of changes"]
11. It is not established whether the stow-reminder deny actually fired in the PR #1290 session. The session may instead have acted on its priors or on CHANGELOG precedent. The design does not depend on which: the deny text is wrong either way, and the README states the rule for both paths. Residual: nothing here proves a session reads the README, which is why a `.claude/rules/` file stays a recurrence fallback. [unverified]
12. Correct general prose lost to wrong specific prose. Root `CLAUDE.md:56-57` tells every session "no re-install needed", while the deny's `emit_deny` line in `require-stow-reminder.sh` and the CHANGELOG precedents say to re-run. [verified: CLAUDE.md:56-57; require-stow-reminder.sh:242; CHANGELOG.md:356, :359]
13. The conditional rule lives mainly in CHANGELOG sub-bullets (line numbers below are as of the branch cut and have since shifted; find them by phrase), in three phrasing families:
    - "only sufficient when ... tree-folded": CHANGELOG.md:158, :171, :373, :410, :422
    - "real directory": :58, :365, :366, :425
    - "not ... a folded directory symlink": :356, :359

    Outside CHANGELOG it appears in design-decision records (`docs/design-decisions/stow-over-plugin-marketplace.md:5` states the top-level rule; `review-orchestrator-routing-gate.md:39` and `review-orchestrator-gate-release-decoupling.md:74` state the conditional) and in `docs/scripts.md:130`, which states one script's failure mode and its fix rather than the general rule. All of those stay as written. No current-behavior doc states the general rule. [verified: grep and reads by the platform and test reviewers this round]
14. Those entries record past releases, so they stay as written. [verified: claude/.claude/CLAUDE.md § Scope discipline, Axis 3] Carve-out for row 7: `docs/design-decisions/git-native-commit-hooks-declined.md:126` describes how the current bootstrap message behaves, not a past event, so the Axis 3 decision test ("record, or describes current behavior") makes it fair game. The two records with the same phrasing stay untouched: `docs/design-decisions/claudemdexcludes-nested-discovery-duplicate.md:11` and `docs/reports/2026-08-10-repo-quality-audit/findings.md:369`.
15. `install.sh` treats a real `~/.claude/skills` as an error state to fix, not a supported layout. [verified: install.sh:301-304]
16. The README "Existing users" paragraph (`README.md:113` before this PR, `README.md:124` after) claimed `git pull` never materializes new wrappers. The PR drops that unconditional claim. The paragraph keeps the owner-only-permissions claim and points at "Updating after git pull" for when new wrappers need `./install.sh`. [verified: `git diff origin/main...HEAD -- README.md`]
17. An installer that stows into a non-default config dir maps the `.claude`-relative content of `claude/.claude/` and `claude-skills/` onto `<config-dir>/`. The CHANGELOG's "whichever installer stows the packages ... into the active config dir" wording assumes this. [unverified]
18. Root `CLAUDE.md:56-57` stays untouched in this PR. [engineer-verified: "Leave it untouched (Recommended)"] (the selected option label; the reasoning attached to that option was the planner's proposal)
19. The read-only checker script is too heavy for this problem. [engineer-verified: "I think the read-only script is WAY overengineered."]
20. Checking whether the new file itself exists in the config dir gives the right answer without regard to fold state, so the README needs no ancestor-walk rule. `~/.local/bin` is pre-created real, so a new wrapper always reports missing until a re-run. [verified: platform reviewer's reading of install.sh:91 and stow-packages.sh rows]
21. The deny text is shared by all three REASON_DETAIL branches, and existing tests assert that the strings `adds new files` and `changes install.sh` appear only in the matching branch. New shared deny prose and the example body line must contain neither. [verified: the REASON_DETAIL-branch and marker tests in test_require_stow_reminder.py, re-read by the SDET reviewer at HEAD]

22. The PR owner's ruling on the trigger: "let's make the hook snip nested adds for all the directories that install.sh stows. We don't have to support machines that haven't made the migration yet." Leaving the nested case in the trigger keeps "a class of false positives that are only mediated by probabilistic model judgment". The rule: an added path under `claude/.claude/` whose first component exists on `main` raises no reminder. A first component new on the branch still raises it, and so does any `install.sh` change. The existence test reads git (`git ls-tree` or `git cat-file -e main:claude/.claude/<first component>`), not a hardcoded directory list and not `install.sh`. A machine whose pre-existing real `hooks/` or `scripts/` directory never folded is unsupported, and the README `ls` check stays as its fallback. [engineer-verified, quoted; the earlier "trigger unchanged" scope was the plan's own and was never put to the engineer]
23. `claude/.local/bin` additions stay outside the trigger, and so does a second immediate child of `claude-skills/` beside `skills/` (known gaps, ledger 112dd2d0d41d). Skills nested under `claude-skills/skills/` fold and are live on pull. The engineer has not asked to change either gap. [verified: platform reviewer's reading of install.sh and stow-packages.sh; no engineer instruction either way]
24. The current scan reads `git diff --name-only` without `-z`, so a non-ASCII added path comes back git-quoted, its leading `"` defeats the prefix strip, and the path is dropped (ledger c0fc625eb5cd). The new first-component test must read NUL-delimited paths so a non-ASCII top-level entry still counts as new. [verified: platform reviewer's scratch-repo run; the test pin is still to be written]
25. A `$(...)` capture strips NUL bytes, so the existing `ADDED_PATHS=$(...)` plus `<<<` shape cannot carry `-z` output: the paths glue together and only the first path's first component is tested. The read must be a `while IFS= read -r -d ''` loop fed by process substitution or a temp file. The git probe runs once per distinct first component. The diff compares `main...HEAD` (the merge-base), and the probe reads the `main` tip. A tip that already has the entry means another PR carried the re-run. [verified: SDET reviewer ran the NUL strip on bash 5.3; platform reviewer ran the `-z` plus `read -d ''` loop on spaces, a newline and non-ASCII names]
26. Default rename detection reports a file moved into a new top-level entry as `R`, so `--diff-filter=A` lists nothing and no reminder fires. Real history (commit d3db8632, a file moved into the new top-level `tests/`) shows this. With `diff.renames=false` the same move lists the entry as added, so the verdict varies with a contributor's git config. Ledger row b3ddc46a7cef deferred it as "trigger diff unchanged by this PR", which stopped holding once this PR rewrote that diff line. The engineer chose to close it here: the diff takes `--no-renames`. [verified: SDET reviewer's read of d3db8632; platform reviewer's scratch-repo runs with default and disabled rename detection] [engineer-verified: "Add --no-renames"] (the selected option label; "(Recommended)" was the planner's suffix)

27. The PR owner answered the question whether to sweep the remaining wording: "This should be fixed for sure, yes." The engineer confirmed in chat: "I responded to your comment - you asked me if I wanted to fix more things, and I said yes." The quote supports sweeping the wording. The specific edits in row 7 (runbook-pointer wording in the bootstrap denies, doc reframing, record edit) are the planner's proposal. This reverses the earlier Out-of-scope decision that left the other hooks' bootstraps alone (previously settled, now reopened by the owner's answer). [engineer-verified: "This should be fixed for sure, yes."]

**Mechanisms:**
- M1. `README.md` § "Updating after git pull" is the one current-behavior statement of the rule and carries the existence check. The gate, `docs/hooks.md`, and this PR's CHANGELOG entry all defer to it. anchors: row13
- M2. Rewrite the gate's header and deny explanation, and narrow its trigger to new top-level entries plus `install.sh` changes. Keep `DENY_GATE_LABEL`, the `REASON_DETAIL` strings, the marker match, and the `_lib.sh` bootstrap deny. anchors: row4, row5, row21, row22, row24, row25, row26
- M3. Make the `require-stow-reminder.sh` entry in `docs/hooks.md` describe the new-top-level-entry trigger and defer the rule to README. anchors: row6, row22
- M4. Sweep the remaining sites that state the same premise so no gate gives conflicting guidance for a failed `_lib.sh` source. anchors: row27

## Critical files

One `code-writer` dispatch covers every file. Every file needs the same background (the fold rule, the existence check, and the pinned-phrase constraint), so splitting the work would mean restating it in each prompt.

1. **`claude/.claude/hooks/require-stow-reminder.sh`** (modify)
   - **Header (the opening Reason comment and the Scope bullet that says "Any depth trips the gate"):** remove the "Stow links each file individually" rationale and the "no 'already-stowed parent covers it' case" claim. Replace them with one-sentence facts:
     - The gate fires on an added path whose top-level entry under `claude/.claude/` is new relative to `main`, and on an `install.sh` change. A file nested under an existing top-level entry arrives on `git pull` and does not trip it.
     - README § Updating after git pull states the rule.
     - The trigger rationale appears once in the header, and the detection bullet says only what trips the gate.
     - **Known gaps:** an edit to a file that `install.sh` copies, reads or runs is an `M`, so the `--diff-filter=A` diff misses it. The package list in `claude/.claude/scripts/stow-packages.sh` and `claude/.claude/scripts/relocate-claude-config.sh` are examples, not an exhaustive list. A PR making such an edit gets no reminder, and the change needs the installer.
   - **Deny text (the final `emit_deny` call):**
     - Keep `this PR $REASON_DETAIL.`
     - Replace the rest with: Whether a consumer must re-run `install.sh` after pulling depends on what changed and on their config dir; README.md § Updating after git pull states the rule and the check.
     - Example body line, containing `install.sh`, which the marker needs, and neither `adds new files` nor `changes install.sh` (row 21): `Post-merge: after git pull, re-run ./install.sh if install.sh itself was modified or a new file is missing from your config dir (see README, Updating after git pull).`
     - No unescaped double quote, `$`, or backtick in the new text: the message sits in a bash double-quoted string under `set -u`, and the template is pasted into `--body "..."`.
     - Keep the existing sentence describing the marker match.
     - Keep each `§` citation on one line.
   - **Trigger (the `ADDED_PATHS` scan through the `NEW_ENTRIES` loop):** read added paths with `git diff -z --name-only --diff-filter=A main...HEAD -- claude/.claude/` through a `while IFS= read -r -d ''` loop, never a `$(...)` capture (rows 24, 25). For each path, take the first component after `claude/.claude/`. Probe each distinct first component once with `git cat-file -e main:claude/.claude/<first component>`, and keep it only when the probe fails. A path whose first component exists on `main` is dropped. `NEW_ENTRIES` lists the new top-level entries, deduplicated, not every added file. A new top-level file keeps its own name as the first component, so it still trips the gate. Add `--no-renames` to the diff (row 26), so a file moved into a new top-level entry lists as an add, and drop the header's rename Known-gaps line, and say in the header's detection bullet that a moved file counts as an added path. A failed probe counts as a new entry: state that in one header sentence. Reword the `REASON_DETAIL` strings only if the tests' pinned phrases (row 21) stay intact. Do not touch the `_lib.sh` bootstrap deny at :110.
   - **Unchanged:**
     - `DENY_GATE_LABEL`.
     - The `REASON_DETAIL` strings.
     - The marker regex.
     - The `_lib.sh` bootstrap deny. It already carries the runbook-pointer wording that row 7 copies into the other hooks.

2. **`claude/.claude/hooks/tests/test_require_stow_reminder.py`** (modify)
   - Reword the docstrings at :33-35, :46-49, :77-80, :126-129, and :140-141 that restate the false premise (stow links every file individually; files inside `skills/` do not trip the gate).
   - Flip the nested cases: `nested-in-existing-directory` and `deeply-nested-two-directories-down` leave `TRIGGER_SHAPES` and become allow tests (`commit_inside_existing_toplevel`, `commit_deeply_nested_file`). `test_new_file_inside_existing_toplevel_denied` flips to an allow test too. Red first: run the flipped allow tests before the hook edit and confirm they fail.
   - Every allow test also asserts a live deny control in the same fixture, because `run_hook` reads empty stdout with any exit code other than 2 as allow, so a crashed hook would pass an allow test. The control is a new top-level entry added in a second commit on the same fixture.
   - Add trigger tests (pins of the new behavior; they pass once the hook edit lands, except where a bullet says otherwise):
     - A nested add beside a new top-level entry in one PR is denied. The new entry's name sorts after the nested path's first component, which catches a `$(...)`-glued read (row 25). The deny names only the new entry.
     - Two new top-level entries, one of them also holding two files, are denied, and each entry is named once.
     - An `install.sh` change beside a nested add is denied with the install.sh-only reason.
     - A non-ASCII new top-level entry is denied. A non-ASCII directory seeded on `main` with a file added inside it is allowed. The fixture seeds that directory on `main` and sets `core.quotePath` and `diff.renames` repo-locally so ambient git config cannot flip the result.
     - A new top-level file (not a directory) is denied.
     - A file moved into a new top-level entry is denied, and the deny names that entry. The move is a `git mv` of a file seeded on `main` under `claude/.claude/`, content unchanged. A source outside the prefix, or edited content below the similarity threshold, lists as an add without the flag and leaves the test green. The fixture's repo-local `diff.renames=true` is what makes it fail without `--no-renames`. This test is red against the hook as it stands: run it before the one-token edit.
     - A file moved into an existing top-level entry is allowed, with the generic live deny control. It guards over-triggering only; it is not a pin on `--no-renames`.
     - `main` gains the top-level entry after the branch is cut, and the branch adds a file under the same entry: allowed, because the probe reads the `main` tip. The entry is absent at the merge-base, and `main` advances without resetting the branch onto it (`seed_path_on_main` resets the branch, so the test must not use it). The deny control is a different new entry. This test is green on arrival, so run it once against a merge-base-probe mutant of the hook and confirm it fails.
     - `main` deletes an existing top-level entry after the cut and the branch makes an unrelated commit: allowed, with the deny control. This pins the `main...HEAD` half of row 25, since a two-dot diff would list the deleted entry's files as added.
     - The dedup test commits its second file explicitly in its own fixture step, not through a later `git add .`.
   - Add the rest, red first (run them before the hook edit and confirm they fail):
     - The README heading `Updating after git pull` resolves, using `normalize_heading` and `heading_texts` from the `helpers` module this file already imports, as `test_bash_floor.py` `TestFloorMessageCitationsResolve` does.
     - The example body line, used verbatim as a PR body, is accepted by the marker for both a new-top-level shape (`commit_new_toplevel_dir`) and a nested shape (`commit_inside_existing_toplevel`).
     - The deny reason does not contain `git pull alone does not create the symlink`.
     - A PR that only edits an existing stowed file, or only changes paths outside `claude/.claude/` and `install.sh`, is allowed without a marker.
     - `install.sh` is seeded on `main`, so install.sh tests exercise a modification.
     - The README-citation and example-line assertions run over every shape that still denies: new top-level entry, install.sh-only, new top-level entry plus install.sh, and new top-level entry plus a nested add. The nested-only shapes are allow tests and carry no example-line check.

3. **`README.md`** (modify)
   - Add `### Updating after git pull` under `## Quickstart`, after line 90, and a TOC entry nested under Quickstart. The shipped section is four short paragraphs, cut at the PR owner's request; the per-path mapping, the `docs/worktree-bash-guard.md` pointer and the installer-definition bullets below did not ship. It states these facts:
     - An edit to an existing file that Claude Code reads from the config dir takes effect on `git pull`, though a running session may need a restart.
     - Anything `install.sh` itself copies, links, registers or runs takes effect only when the installer runs, and so does a change to `install.sh`. The `relocate-claude-config` copy in `~/.local/bin`, a package added to the list in `claude/.claude/scripts/stow-packages.sh`, and marketplace and plugin registration are examples, not an exhaustive list.
     - A new file nested under a directory the config dir already links into this checkout is live after `git pull`. A new top-level entry directly under `claude/.claude/`, and any new wrapper under `claude/.local/bin/`, always needs the installer, because `install.sh` creates `~/.claude` and `~/.local/bin` as real directories.
     - Check a new file with `ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/<path under claude/.claude/>"`. For `claude-skills/skills/<x>` the path is `<config-dir>/skills/<x>`. For `claude/.local/bin/<x>` it is `~/.local/bin/<x>`.
     - For an agent Bash call, a one-line pointer to `docs/worktree-bash-guard.md` § "The fix: script-first, not prose-split", which owns the path guidance.
     - "No such file or directory" means re-run the installer.
     - "The installer" means `./install.sh` for `~/.claude`. For any other config dir, it means whichever installer stows the packages that `claude/.claude/scripts/stow-packages.sh` lists into that dir.
   - Edit the "Existing users" paragraph (`README.md:113`): drop the claim that `git pull` never materializes new wrappers, point at "Updating after git pull" (row 16), and scope the closing "Re-run it once" to applying the owner-only permissions.

4. **`docs/hooks.md`** (modify the require-stow-reminder entry)
   - Describe the trigger correctly: an added path under `claude/.claude/` whose top-level entry is new relative to `main`, or an `install.sh` change.
   - Replace the "Reason:" restatement with a pointer to README § Updating after git pull and a mention of the deny's conditional template.
   - Keep the `claude-skills/` gap sentence and the line-start bullet `test_hook_alignment.py` requires.

5. **`docs/worktree-bash-guard.md`** (modify § "The fix: script-first, not prose-split" only)
   - Append to the ad-hoc-call paragraph: in place of `$CLAUDE_CONFIG_DIR`, write the active config dir's literal path. That path is `~/.claude` only on the default config dir. A hardcoded `~/.claude` suits a call to a tracked script or hook, but lists the wrong directory when the call checks what a non-default config dir itself contains. When the literal path is not known, report the check to the engineer instead of guessing `~/.claude`.
   - Leave the Site sweep table and Current status sections as written.

6. **`CHANGELOG.md`** (modify `## [Unreleased]`)
   - **Changed:** `require-stow-reminder.sh`'s deny text no longer says every new file needs `./install.sh`. It names README § Updating after git pull, and its example body line is conditional. The trigger narrows: a file added under a top-level entry that already exists on `main` no longer raises the reminder, and a file moved into a new top-level entry now does.

7. **Premise sweep** (modify; anchor row 27). Each site below states that stow does not relink a new file into an existing directory, or tells the agent to run the installer to pick up a pulled hook file.
   - **Edit:** each bootstrap deny becomes the sentence at `require-stow-reminder.sh:110` verbatim: `could not source _lib.sh. For the recovery, see docs/hooks.md § \"Gate deadlock recovery\" in the claude-config repository; the user, not the agent, runs ./install.sh from the claude-config checkout in a separate terminal only if that runbook says so.` It keeps the leading `could not source _lib.sh` (pinned by `test_hook_alignment.py` and the denial classifier) and any existing prefix, such as `file-writes arm. ` in `require-worktree-for-file-writes.sh`.
   - **21 hooks carrying the parenthetical** under `claude/.claude/hooks/`: `block-gh-pr-merge.sh`, `check-claude-md-length.sh`, `check-skill-length.sh`, `deny-credential-bash-reads.sh`, `deny-credential-file-reads.sh`, `deny-data-file-reads.sh`, `deny-env-reads.sh`, `deny-escaped-backticks-in-pr-body.sh`, `deny-invisible-commit-content.sh`, `deny-network-installs.sh`, `deny-no-op-dispatch.sh`, `guard-settings-session-keys.sh`, `require-architect-consult.sh`, `require-code-review.sh`, `require-memory-skill.sh`, `require-plan-review.sh`, `require-ready-for-review.sh`, `require-respond-pr.sh`, `require-review-orchestrator-routing.sh`, `require-routing-read.sh`, `require-worktree-for-file-writes.sh`.
   - **5 denying bootstraps without the parenthetical** (same premise: "run ./install.sh to pick up hook files this update added"): `deny-pii-in-commits.sh`, `deny-repo-relocation.sh`, `require-worktree-for-git-writes.sh`, `deny-reviewer-tree-mutation.sh`, `deny-private-project-refs.sh`.
   - `claude/.claude/scripts/review-ledger.sh`: the :12 header comment goes, and the :16 error message drops "to link it" and keeps its pointer to the installer for a missing sibling file.
   - `docs/design-decisions/git-native-commit-hooks-declined.md:126`: reword the sentence to describe the current runbook-pointer bootstrap message (row 14 carve-out).
   - `docs/hooks.md:287`: reframe "When the hooks and scripts directories are real directories" as the unsupported legacy layout (row 22). The recovery stays: the user runs `./install.sh`.
   - `docs/hooks.md:411-413`: keep the sentence that authorizes the user to run `./install.sh` for an unlinked `_lib.sh` or `_config.sh`, reframed as the unsupported legacy layout (row 22). The heading `Gate deadlock recovery` stays byte-identical.
   - **Named exceptions, untouched:** the fail-open stderr echoes in `capture-session-id.sh`, `record-session-end.sh` and `ask-review-permissions.sh` (no deny, the hook exits 0); `redact-credential-values.sh:17` and `worktree-ensure.sh`, which advise the installer for a stale link on a single-script failure like the exit-127 advice below; past CHANGELOG entries; other plans; `test_transcript_denials.py` fixtures, which copy historical text on purpose.
   - **Test pin:** extend the missing-`_lib.sh` alignment test in `claude/.claude/hooks/tests/test_hook_alignment.py` (parametrized over every hook). For the 26 swept hooks it asserts stderr or the deny reason cites `docs/hooks.md` § "Gate deadlock recovery", contains `the user, not the agent`, and omits `does not relink` and `pick up hook files`. The heading is resolved the way `TestFloorMessageCitationsResolve` does. The 26 bootstraps cannot source a shared helper, so this pin is what keeps the hand copies from drifting.

8. **`.claude/plans/stale-install-advice.md`**: this plan.

## Verification

1. Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It runs the selected pytest domains itself: a hook change maps to the whole hooks test tree, and README and `docs/` map to the hooks and skills trees. CHANGELOG maps to no tests.
2. Run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, because the plan edits a `.py`.
3. Run `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
4. Replay the incident on this machine: `ls ~/.claude/scripts/transcript_analysis/session_pricing.py` succeeds, showing the README check reports a live file where the old advice said to re-run. Then run the hook's own tests, which replay a nested add (allowed) and a new top-level entry (denied) end to end.
5. Sweep the whole false-premise family, not one sentence: `git grep -nEi "individually|alone (does not|doesn't|won't)|materializ|only appears|real director|relink|any depth|regardless of whether|adds any path|nested|rename"` over the hook, its tests, `docs/hooks.md`, and `README.md`. Every remaining hit must be a true statement (for example the "Updating after git pull" section on wrappers) or a record.
6. Sweep the sweep: `git grep -nE "does not relink|until it is re-run|Stow links a new file|pick up hook files" -- claude docs ':!CHANGELOG.md'` returns only the three fail-open stderr echoes row 7 names as exceptions and the two omit-assertions in `test_hook_alignment.py`.

## Out of scope

- **A checker script.** Cut on the engineer's call (row 19).
- **Past CHANGELOG re-stow sub-bullets, design-decision records, and `docs/scripts.md:130`.** They record past releases or one script's failure mode (rows 13, 14). The one exception is the descriptive sentence in `git-native-commit-hooks-declined.md:126` (row 14 carve-out). Dropping the conditional CHANGELOG entry is also one of the suggestions the engineer found not right (row 8).
- **Root `CLAUDE.md:56-57`.** Left untouched (row 18).
- **Hooks whose `_lib.sh` echo is fail-open or advises the installer for a single stale link.** Row 7 names them as exceptions.
- **The exit-127 advice at `claude-skills/skills/branch-management/SKILL.md:100-101`.** A missing script means it is unlinked, so re-running the installer is correct.
- **The `--adopt` sentences in the README "Existing users" paragraph.** They describe a flag `install.sh` no longer passes (install.sh:335-336). This is real but unrelated; raise it to the reviewer.
- **Top-level entry names with spaces.** `ENTRIES_HUMAN` splits the entry list on spaces, so such a name is garbled in the deny text. No tracked path under `claude/.claude/` has a space today.
- **The gate's documented `claude-skills/` gap.** Its own header records it.
- **The deleted side of a delete or rename**, which can leave dangling per-entry links.
