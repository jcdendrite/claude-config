# Stop sessions from giving unconditional `./install.sh` advice

## Context

Goal: stop agent sessions in this repo from telling the engineer to re-run `./install.sh` after a pull that adds a file under `claude/.claude/**` when no re-stow is needed on that machine, and state the real rule in one authoritative place.

Ask: "you're not the first session to suggest running install.sh for these types of changes", and "I think that anything in scripts is stowed...". After two fixes were offered: "none of your suggestions really seem like the right solution but you do understand the problem which is good."

Why now: it recurred in the session that shipped PR #1290. The audience is every stow consumer, not one machine.

## Approach

Fix the instruction that teaches sessions the wrong rule, and state the real rule once. The gate `require-stow-reminder.sh` currently tells sessions every new file needs `./install.sh`, with an unconditional PR-body template. Rewrite its text to defer to a new `README.md` § "Updating after git pull", which states the rule and gives a one-line existence check for a new file. No new script.

The gate keeps its trigger: any path added under `claude/.claude/`, or a change to `install.sh`. For consumers whose config dir holds a real directory, the PR body is the only notice they get. Past CHANGELOG entries and design-decision records stay as written. This PR's own entry shows the new shape: the condition comes first and the rule is not restated.

Alternatives set aside:
- **A note beside root `CLAUDE.md:56`, or dropping the conditional CHANGELOG re-stow entry.** The engineer said the prior session's suggestions were not the right solution (row 7), and the handoff names these two as those suggestions (row 8).
- **A read-only `restow-check.py` that walks ancestors and reports `LIVE`/`RESTOW`.** The engineer called it "WAY overengineered" (row 19). It also reaches a session only through the same README or deny prose it was meant to backstop, and it answers a question that checking the file itself answers directly.
- **Have the gate compute this machine's verdict.** The gate writes for the PR body's audience, which is every stow consumer. It also fires only when the body lacks the marker, so it is absent at most of the moments when a session gives advice.
- **Narrow the gate to new top-level entries and `install.sh` changes.** This drops the only notice that real-directory consumers get (row 2).
- **Normalize installs: fold real directories, or use `stow --no-folding` everywhere.** Either way, every consumer's layout changes. Stow cannot fold a directory that holds the consumer's own files, and the change does not reach other installers. `--no-folding` would also make every new file need a re-run, the opposite of what the engineer expects (row 9).
- **A path-scoped `.claude/rules/` instruction.** This is another standing prose layer. Revisit it only if the bad advice recurs after this lands.
- **A real-`stow` integration test pinning fold versus real-directory behavior.** Real stow tests exist elsewhere in the suite, but this change rewrites prose, not stow behavior. The stow manual's tree-folding section already settles the semantics (row 1).

### Assumption ledger

**Root:** Sessions in `claude-config` tell the engineer to re-run `./install.sh` after a pull that adds a file under `claude/.claude/**`. They do this even when the file's config-dir directory is a folded stow symlink and the file appears on `git pull`. Two things cause it:
- The gate `require-stow-reminder.sh`'s deny states the opposite rule unconditionally, and the CHANGELOG precedents read the same way. Whether the deny fired in the PR #1290 session is unestablished (row 11).
- The conditional rule has no current-behavior home outside CHANGELOG sub-bullets (row 13).

**Givens:**
1. GNU Stow folds a target directory that does not exist when stow runs into one symlink into the package. It links entry by entry into a directory that already exists. The vendor imposes this behavior. [verified: install.sh:92-95 comment; stow(8) "tree folding" section per the platform reviewer; `ls -ld` on every config dir on the plan author's machine this session showed `scripts/`, `hooks/`, `agents/`, `skills/`, and `rules/` all folded]
2. Consumers' config dirs differ in fold state. Some hold a real `hooks/`, `scripts/` or `skills/` directory. Other installers stow into non-`$HOME` config dirs whose layout this repo does not control. Each consumer owns their own machine. [verified: CHANGELOG.md:410 (personal skills make `~/.claude/skills` real; predates the package split, see row 15); test_require_stow_reminder.py:126-129 (`_config.sh` exposed the real-`hooks/` case); stow-packages.sh:6-8]
3. An agent's ad-hoc Bash call should not reference `$CLAUDE_CONFIG_DIR`. A session on the default config dir runs a literal `ls ~/.claude/<path>`. A human terminal, or a session on another config dir, uses that dir's path. The harness owns the guard. [verified: docs/worktree-bash-guard.md:24 (trigger B recorded as refused); :116-134 (zero refusals on re-run, guard not reproducible on demand); the platform reviewer's own compound Bash call was refused this round]

**Rows:**

4. `require-stow-reminder.sh`'s deny says a new file "only appears there after re-running install.sh; git pull alone does not create the symlink". It offers the template "Post-merge: run `./install.sh` to pick up this change." [verified: require-stow-reminder.sh:242]
5. The hook's header premise is "there is no 'already-stowed parent covers it' case to filter out". That premise contradicts `install.sh`. `install.sh` pre-creates only `~/.claude` and `~/.local/bin`. A new top-level child of `claude/.claude/` is therefore never created by `git pull`, so it always needs a re-run. A new file nested under an already-folded directory arrives on pull. [verified: require-stow-reminder.sh:30-38; install.sh:91-96]
6. `docs/hooks.md:143` says the gate fires on a new *immediate child* of `claude/.claude/`. The hook actually fires on an added path at any depth. [verified: docs/hooks.md:143; require-stow-reminder.sh:133-162]
7. The prior session's two fixes are not the right solution. [engineer-verified: "none of your suggestions really seem like the right solution"]
8. Those two fixes were a "when is a re-stow needed" note beside root `CLAUDE.md:56`, and dropping the conditional re-stow entry from `CHANGELOG.md`. [unverified: relayed by the dispatching session from the handoff, not quoted]
9. The engineer expects a new file under `scripts/` to arrive on `git pull`. [engineer-verified: "I think that anything in scripts is stowed..."]
10. Sessions have repeatedly advised re-running `install.sh` for this kind of change. [engineer-verified: "you're not the first session to suggest running install.sh for these types of changes"]
11. It is not established whether the stow-reminder deny actually fired in the PR #1290 session. The session may instead have acted on its priors or on CHANGELOG precedent. The design does not depend on which: the deny text is wrong either way, and the README states the rule for both paths. Residual: nothing here proves a session reads the README, which is why a `.claude/rules/` file stays a recurrence fallback. [unverified]
12. Correct general prose lost to wrong specific prose. Root `CLAUDE.md:56-57` tells every session "no re-install needed", while the deny at `require-stow-reminder.sh:242` and the CHANGELOG precedents say to re-run. [verified: CLAUDE.md:56-57; require-stow-reminder.sh:242; CHANGELOG.md:356, :359]
13. The conditional rule lives mainly in CHANGELOG sub-bullets, in three phrasing families:
    - "only sufficient when ... tree-folded": CHANGELOG.md:158, :171, :373, :410, :422
    - "real directory": :58, :365, :366, :425
    - "not ... a folded directory symlink": :356, :359

    Outside CHANGELOG it appears in design-decision records (`docs/design-decisions/stow-over-plugin-marketplace.md:5` states the top-level rule; `review-orchestrator-routing-gate.md:39` and `review-orchestrator-gate-release-decoupling.md:74` state the conditional) and in `docs/scripts.md:130`, which states one script's failure mode and its fix rather than the general rule. All of those stay as written. No current-behavior doc states the general rule. [verified: grep and reads by the platform and test reviewers this round]
14. Those entries record past releases, so they stay as written. [verified: claude/.claude/CLAUDE.md § Scope discipline, Axis 3]
15. `install.sh` skips stowing `claude-skills` and exits with a message when `~/.claude/skills` is a real directory. There a new skill never arrives, and re-running does not help until the engineer moves that directory. [verified: install.sh:301-304, :405-406]
16. The README "Existing users" paragraph (`README.md:113` before this PR, `README.md:133` after) claimed `git pull` never materializes new wrappers. The PR drops that unconditional claim. The paragraph keeps the owner-only-permissions claim and points at "Updating after git pull" for when new wrappers need `./install.sh`. [verified: `git diff origin/main...HEAD -- README.md`]
17. An installer that stows into a non-default config dir maps the `.claude`-relative content of `claude/.claude/` and `claude-skills/` onto `<config-dir>/`. The CHANGELOG's "whichever installer stows the packages ... into the active config dir" wording assumes this. [unverified]
18. Root `CLAUDE.md:56-57` stays untouched in this PR. [engineer-verified: "Leave it untouched (Recommended)"] (the selected option label; the reasoning attached to that option was the planner's proposal)
19. The read-only checker script is too heavy for this problem. [engineer-verified: "I think the read-only script is WAY overengineered."]
20. Checking whether the new file itself exists in the config dir gives the right answer whatever the fold state, nested real directories included, so the README needs no ancestor-walk rule. `~/.local/bin` is pre-created real, so a new wrapper always reports missing until a re-run. [verified: platform reviewer's reading of install.sh:91 and stow-packages.sh rows]
21. The deny text is shared by all three REASON_DETAIL branches, and existing tests assert that the strings `adds new files` and `changes install.sh` appear only in the matching branch. New shared deny prose and the example body line must contain neither. [verified: test_require_stow_reminder.py:171-201, :336, :384, :409, :434]

**Mechanisms:**
- M1. `README.md` § "Updating after git pull" is the one current-behavior statement of the rule and carries the existence check. The gate, `docs/hooks.md`, and this PR's CHANGELOG entry all defer to it. anchors: row13
- M2. Rewrite the gate's header and deny explanation. Keep its trigger, `DENY_GATE_LABEL`, the `REASON_DETAIL` strings, and the marker match. anchors: row4, row5, row21
- M3. Make `docs/hooks.md:143` describe the any-depth trigger and defer the rule to README. anchors: row6

## Critical files

One `code-writer` dispatch covers every file. Every file needs the same background (the fold rule, the existence check, and the pinned-phrase constraint), so splitting the work would mean restating it in each prompt.

1. **`claude/.claude/hooks/require-stow-reminder.sh`** (modify)
   - **Header (:4-17 and :30-38):** remove the "Stow links each file individually" rationale and the "no 'already-stowed parent covers it' case" claim. Replace them with one-sentence facts:
     - The gate fires on any added path, because a new top-level entry always needs a re-run and a consumer whose config dir holds a directory as a real directory picks up a new file in it only after a re-stow.
     - README § Updating after git pull states the rule.
     - The trigger rationale appears once in the header, and the detection bullet says only that any depth trips the gate.
     - **Known gaps:** an edit to a file that `install.sh` copies, reads or runs is an `M`, so the `--diff-filter=A` diff misses it. The package list in `claude/.claude/scripts/stow-packages.sh` and `claude/.claude/scripts/relocate-claude-config.sh` are examples, not an exhaustive list. A PR making such an edit gets no reminder, and the change needs the installer.
   - **Deny text (:242):**
     - Keep `this PR $REASON_DETAIL.`
     - Replace the rest with: Whether a consumer must re-run `install.sh` after pulling depends on their config dir; README.md § Updating after git pull states the rule and the check.
     - Example body line, containing `install.sh`, which the marker needs, and neither `adds new files` nor `changes install.sh` (row 21): `Post-merge: after git pull, re-run ./install.sh if install.sh itself was modified or a new file is missing from your config dir (see README, Updating after git pull).`
     - No unescaped double quote, `$`, or backtick in the new text: the message sits in a bash double-quoted string under `set -u`, and the template is pasted into `--body "..."`.
     - Keep the existing sentence describing the marker match.
     - Keep each `§` citation on one line.
   - **Unchanged:**
     - The trigger (:133-168).
     - `DENY_GATE_LABEL`.
     - The `REASON_DETAIL` strings.
     - The marker regex.
     - The `_lib.sh` bootstrap message at :98.

2. **`claude/.claude/hooks/tests/test_require_stow_reminder.py`** (modify)
   - Reword the docstrings at :33-35, :46-49, :77-80, :126-129, and :140-141 that restate the false premise (stow links every file individually; files inside `skills/` do not trip the gate). The real reason: a consumer's config dir may hold that directory as a real directory, and a new top-level entry always needs a re-run.
   - Add tests, red first (run them before the hook edit and confirm they fail):
     - The README heading `Updating after git pull` resolves, using `normalize_heading` and `heading_texts` from the `helpers` module this file already imports, as `test_bash_floor.py` `TestFloorMessageCitationsResolve` does.
     - The example body line, used verbatim as a PR body, is accepted by the marker for both a new-top-level shape (`commit_new_toplevel_dir`) and a nested shape (`commit_inside_existing_toplevel`).
     - The deny reason does not contain `git pull alone does not create the symlink`.
     - A PR that only edits an existing stowed file, or only changes paths outside `claude/.claude/` and `install.sh`, is allowed without a marker.
     - `install.sh` is seeded on `main`, so install.sh tests exercise a modification.
     - The README-citation and example-line assertions run over every trigger shape: new top-level entry, nested, deeply nested, install.sh-only, and combined.

3. **`README.md`** (modify)
   - Add `### Updating after git pull` under `## Quickstart`, after line 90, and a TOC entry nested under Quickstart. It states these facts:
     - An edit to an existing file that Claude Code reads from the config dir takes effect on `git pull`, though a running session may need a restart.
     - Anything `install.sh` itself copies, links, registers or runs takes effect only when the installer runs, and so does a change to `install.sh`. The `relocate-claude-config` copy in `~/.local/bin`, a package added to the list in `claude/.claude/scripts/stow-packages.sh`, and marketplace and plugin registration are examples, not an exhaustive list.
     - A new file nested under a directory the config dir already links into this checkout is live after `git pull`. A new top-level entry directly under `claude/.claude/`, and any new wrapper under `claude/.local/bin/`, always needs the installer, because `install.sh` creates `~/.claude` and `~/.local/bin` as real directories.
     - Check a new file with `ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/<path under claude/.claude/>"`. For `claude-skills/skills/<x>` the path is `<config-dir>/skills/<x>`. For `claude/.local/bin/<x>` it is `~/.local/bin/<x>`.
     - For an agent Bash call, a one-line pointer to `docs/worktree-bash-guard.md` § "The fix: script-first, not prose-split", which owns the path guidance.
     - "No such file or directory" means re-run the installer. If `~/.claude/skills` is a real directory, `install.sh` stops with a message instead; follow that message.
     - "The installer" means `./install.sh` for `~/.claude`. For any other config dir, it means whichever installer stows the packages that `claude/.claude/scripts/stow-packages.sh` lists into that dir.
   - Edit the "Existing users" paragraph (`README.md:113`): drop the claim that `git pull` never materializes new wrappers, point at "Updating after git pull" (row 16), and scope the closing "Re-run it once" to applying the owner-only permissions.

4. **`docs/hooks.md`** (modify :143)
   - Describe the trigger correctly: any path added under `claude/.claude/` (a file at any depth), or an `install.sh` change.
   - Replace the "Reason:" restatement with a pointer to README § Updating after git pull and a mention of the deny's conditional template.
   - Keep the `claude-skills/` gap sentence and the line-start bullet `test_hook_alignment.py` requires.

5. **`docs/worktree-bash-guard.md`** (modify § "The fix: script-first, not prose-split" only)
   - Append to the ad-hoc-call paragraph: in place of `$CLAUDE_CONFIG_DIR`, write the active config dir's literal path. That path is `~/.claude` only on the default config dir. A hardcoded `~/.claude` suits a call to a tracked script or hook, but lists the wrong directory when the call checks what a non-default config dir itself contains. When the literal path is not known, report the check to the engineer instead of guessing `~/.claude`.
   - Leave the Site sweep table and Current status sections as written.

6. **`CHANGELOG.md`** (modify `## [Unreleased]`)
   - **Changed:** `require-stow-reminder.sh`'s deny text no longer says every new file needs `./install.sh`. It names README § Updating after git pull, and its example body line is conditional. The trigger is unchanged.

7. **`.claude/plans/stale-install-advice.md`**: this plan.

## Verification

1. Run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. It runs the selected pytest domains itself: a hook change maps to the whole hooks test tree, and README and `docs/` map to the hooks and skills trees. CHANGELOG maps to no tests.
2. Run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`, because the plan edits a `.py`.
3. Run `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
4. Replay the incident on this machine: `ls ~/.claude/scripts/transcript_analysis/session_pricing.py` succeeds, showing the README check reports a live file where the old advice said to re-run.
5. Sweep the whole false-premise family, not one sentence: `git grep -nEi "individually|alone (does not|doesn't|won't)|materializ|only appears"` over the hook, its tests, `docs/hooks.md`, and `README.md`. Every remaining hit must be a true statement (for example the "Updating after git pull" section on wrappers) or a record.

## Out of scope

- **A checker script.** Cut on the engineer's call (row 19).
- **Past CHANGELOG re-stow sub-bullets, design-decision records, and `docs/scripts.md:130`.** They record past releases or one script's failure mode (rows 13, 14). Dropping the conditional CHANGELOG entry is also one of the suggestions the engineer found not right (row 8).
- **Root `CLAUDE.md:56-57`.** Left untouched (row 18).
- **The "could not source _lib.sh; run ./install.sh ..." deny repeated across about ten hooks' `_lib.sh` bootstrap.** It fires only when `_lib.sh` cannot be sourced, and in that case re-running the installer is a correct step.
- **The exit-127 advice at `claude-skills/skills/branch-management/SKILL.md:100-101`.** A missing script means it is unlinked, so re-running the installer is correct.
- **The `--adopt` sentences in the README "Existing users" paragraph.** They describe a flag `install.sh` no longer passes (install.sh:335-336). This is real but unrelated; raise it to the reviewer.
- **The gate's possible rename blind spot.** It runs `--diff-filter=A` without `--no-renames`, so git's default rename detection may classify a moved file as `R` and skip it [unverified]. This is a separate trigger defect.
- **The gate's documented `claude-skills/` gap.** Its own header records it.
- **Deleted or renamed files** that leave dangling per-entry links in a real directory.
