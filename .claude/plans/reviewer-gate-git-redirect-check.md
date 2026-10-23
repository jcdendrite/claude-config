# Reviewer gate: redirect check covers git commands; quoted-text false denies recorded as won't-fix

## Context

Goal: close a live false negative in `deny-reviewer-tree-mutation.sh`, where an allowed read-only git subcommand skips the raw-write redirect check, and record the decision not to chase quoted-trigger-word false positives with parser work.

Ask: "I agree with your recomendations" — accepting the session's own proposal (session wording, not the engineer's): "Close [the quoted-trigger-word false-deny issue] as won't-fix ... recorded somewhere durable" and "Plan the redirect-ordering fix as its own small PR now". Record home — accepted via label "Design-decision file (Recommended)" (session wording: a new `docs/design-decisions/<slug>.md` as the canonical record, linked from the reviewer gate's `docs/hooks.md` bullet). The new quoted-`>` over-deny on git fragments — accepted via label "Accept it (Recommended)" (session wording: accept as part of the quoted-text over-deny and pin it in a test as a known over-deny).

Problem: the gate's git arm (`claude/.claude/hooks/deny-reviewer-tree-mutation.sh` ~line 424) `continue`s past an allowed subcommand before the GH-751 raw-write-target loop (~lines 508-520) runs, so `git show HEAD:f > src/tracked.py` is allowed for a review-only agent while `cp /tmp/x src/tracked.py` denies. Found by an adversarial review this session and confirmed by reading the code.

Why now: the failure class (gates misparse read-only commands containing a trigger word inside quotes or heredoc text) was discussed with the engineer this session. The engineer's position: "It's the entire problem space of using gates with regex for quoted banned test without generating false positives or false negatives. It's a case of whack-a-mole." and "I don't think gates can stop reading command text altogether... I think that defeats the purpose of the gates." On write prevention: "In general without any context on what is being written I think preventing a write is important." On sandboxing: "adopting a sandbox is heavy and adds its own complexity. That's worth assessing, but it's important to indicate that it isn't just a simple replacement strategy".

Intended outcome: one small PR that (1) makes the redirect check run for every fragment, git included, with a regression test, and (2) records the won't-fix decision and its reasons in durable docs.

## Approach

Delete the git arm's `continue` (`claude/.claude/hooks/deny-reviewer-tree-mutation.sh:424`), so an allowed git fragment falls through to the in-place-edit and raw-write checks like every other fragment. New tests pin the new denies, the allows that must survive, and two accepted false-deny classes. A new design-decision file becomes the canonical won't-fix record for quoted-text false denies, and the hook header and the `docs/hooks.md` bullet point to it.

**Why deleting the `continue` is the right fix, not only the lightest one.** Every arm except the git arm either denies and exits or falls through (rows 1, 4). The git arm is the outlier. Its `continue` hides more than the redirect: `_lib_fragment_invokes_git` matches any word equal to `git` or ending in `/git`. So a non-git fragment that only mentions an allowed subcommand reaches the same `continue` and skips every later check. Examples are `sed -i 's/git log/x/' src/x` (row 3) and `black tools/git status.py` (row 32). Scope follows the bug, so the fix has to reopen every later check, not only the redirect loop. Falling through costs a real git command no new verdict except at its redirects: the later arms resolve the command word, and for a real git command that word is `git` (row 2).

**Alternatives set aside for the redirect fix:**
- **Call the raw-write loop from inside the git arm.** This extracts the loop into a function with a second call site. It closes the redirect hole but leaves rows 3 and 32 open, which is the sibling-arm bug shape.
- **Move the raw-write loop above the git arm.** It leaves the same row 3 and row 32 hole, and it reorders deny messages for no gain.
- **Make `_lib_fragment_invokes_git` resolve the command word.** That changes shared `_lib.sh` code that other gates call, and the any-word scan is deliberate (`sudo git`, `xargs git`, `bash -c "git …"`). `docs/hooks.md` § "Threat-model tiers" (the dependency invariant) bars loosening shared code a caller relies on. The concurrent branch also edits `_lib.sh` (G3).
- **Allow an argument-less git call such as `git --version`.** This would reopen `git -C "$(git rev-parse --show-toplevel)" checkout x` (row 24).

**Alternatives set aside for the quoted-text false deny.** The decision file records each one; this plan does not design them:
- **A shared quote-aware tokenizer.** Rows 18 and 19.
- **Reusing `parse-git-command.py`.** Row 25. The durable record does not cite it, because it was not traced.
- **Removing Bash from review-only personas.** It ends exposure only for this gate, and the personas' Scratch execution sections rely on Bash.
- **The sandbox.** Rows 15 and 16. The record presents it as an open assessment, stating what it closes and what it does not. It is neither rejected nor a drop-in replacement.

The record states that the deny reason already carries the Read/Grep/Glob recovery sentence (row 20).

**Root:** a review-only agent can run an in-place edit, or write a tracked file through `>`/`>>`, in any Bash fragment that the git arm reads as an allowed read-only git subcommand, because the arm's `continue` (hook:424) ends that fragment's checks. Separately, the decision not to chase quoted-text false denies has no durable record.

**Givens:**
- G1. A PreToolUse hook sees the Bash command as text before it runs, not what the command writes while it runs. Reason: the vendor imposes this hook contract. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:7-8`, "A text-matching hook can't see a write a script makes while it runs"]
- G2. Sandbox policy is session-wide, with no per-subagent setting. Reason: vendor-imposed. [verified, as a citation rather than a measurement: `docs/case-studies/claude-code-sandbox-write-boundary.md:182-194`, quoting `sandboxing.md` § "Scope"]
- G3. An unpushed concurrent branch edits `docs/hooks.md` (a new gate bullet, a tier-table row, tier counts) and `_lib.sh`. Reason: another branch owns those edits, so this plan stays off `_lib.sh`, the tier table, and the tier counts. [unverified: this session's exploration report]

**Assumptions:**
1. The git arm is the only arm that ends a fragment's checks early (`continue` at hook:424). The formatter (438-443), terraform (449-454), worktree-ensure (458-461), ruff (467-482), eslint (484-487), and sed/perl (489-494) arms each deny and exit or fall through to the raw-write loop (508-520). [verified: `deny-reviewer-tree-mutation.sh:412-529`]
2. The later arms match on the resolved command word (`_lib_fragment_invokes_tool` → `_lib_fragment_command_word`). So do the cp/mv/tee branches of `_fragment_raw_write_targets` (hook:249-268). A fragment whose command word is `git` matches none of them, so for a real git command falling through adds only the `>`/`>>` target check. [verified: `_lib.sh:1970-2024`; hook:222-270]
3. `_lib_fragment_invokes_git` matches any word equal to `git` or ending in `/git` (`_lib.sh:1661-1674`). The subcommand is the next non-flag word, cut at its first character outside `[A-Za-z0-9_-]` (`_lib.sh:1689-1726`). After quote stripping, `sed -i 's/git log/x/' src/x` has the word `s/git` followed by `log/x/`, which reads as the allowed `log`. It therefore reaches the `continue` and skips the `sed -i` arm today. [verified: the round-1 staff-sdet plan review executed it against a scratch copy of the hook: allow before the deletion, deny after]
4. Falling through can only add denies. Every later arm denies and exits or falls through, and the only exit after them is the allow at hook:530. No input denied at the merge-base becomes an allow, so the regression-only rule in `docs/hooks.md` § "Threat-model tiers" holds. [verified: hook:412-530]
5. Quote stripping deletes quote characters without masking the quoted text. So `git log --grep 'a > b'` yields the redirect target `b` and denies once git fragments reach the loop, and `grep 'a > b' src/x` already denies the same way. A heredoc body line containing `git <allowed> … > x` joins the same false-deny class. [verified: `_lib.sh:3378-3386` (`tr -d "\"'"`); hook:236-245]
6. `git status > /dev/null` stays allowed through the `/dev/null` exemption (hook:511). Its existing test is at `test_deny_reviewer_tree_mutation.py:662-665`. [verified]
7. Every persona's Scratch execution text already says `git show <ref>:<path> > <path>` is not exempt. The hook change brings the gate in line with that text, so no persona text changes. [verified: `test_agent_roster.py:1020-1025` (`SCRATCH_READ_ONLY_INSPECTION_SENTENCE`)]
8. The quoted-text false-deny class closes as won't-fix, and the redirect fix ships as its own small PR, separate from any parser work. [engineer-verified: "I agree with your recomendations"] The fix and the won't-fix record ship together in one PR. [engineer-verified: "One PR (Recommended)"]
9. Gates keep reading command text. [engineer-verified: "I don't think gates can stop reading command text altogether... I think that defeats the purpose of the gates."]
10. Denying a write whose content the gate cannot see is preferred over allowing it. [engineer-verified: "In general without any context on what is being written I think preventing a write is important."]
11. The quoted-`>` false deny on git fragments is accepted. [engineer-verified: "Accept it (Recommended)"]
12. A test pins that false deny as a known over-deny. [unverified: the session's own option description under row 11's label, not the engineer's words]
13. The record lives in a new design-decision file. [engineer-verified: "Design-decision file (Recommended)"]
14. The reviewer gate's `docs/hooks.md` bullet gains the first two sentences in Critical files item 4 (the check runs on git fragments, plus a link to the decision file). [engineer-verified: "Include (Recommended)"]
15. The record must say the sandbox is not a drop-in replacement. [engineer-verified: "adopting a sandbox is heavy and adds its own complexity. That's worth assessing, but it's important to indicate that it isn't just a simple replacement strategy"]
16. The record cites these sandbox facts from the case study and labels each one measured or cited. Every measurement ran on Linux, and macOS is untested (`:551-555`). [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md`]
    - Measured with the escape hatch off (Run A), closed: Bash writes through a symlink to a path outside the write set were blocked, including a write made from inside a running script (T1–T4, T3-RERUN; `:29-43`).
    - Measured, not closed: an append through a hard link created before the sandboxed command ran changed the outside file (T6; `:45-60`, `:374`).
    - Measured, one trial: bare `Bash` was granted and `sandbox.allowUnsandboxedCommands` left at its default `true`. Under those conditions one retry carrying `dangerouslyDisableSandbox: true`, which the prompt instructed, succeeded with no prompt and no signal (ESC1; `:196-211`). The setting can be turned off, and Run A ran with it off, but no trial retried under that setting (`:309-312`, `:350`).
    - Cited: subagents use the parent session's sandbox configuration, and subagent frontmatter has no sandbox field (`:182-194`).
    - Cited, not exercised: when the sandbox cannot start, commands run unsandboxed with only a warning unless `sandbox.failIfUnavailable` is set (`:107-118`).
    - Measured, bounded sample: `git worktree add <sibling>` run from inside a linked worktree failed (C4; `:119-133`). A literal `/tmp` write was denied (PC4; `:365`), which breaks the reviewer personas' `mktemp -d /tmp/…` scratch convention (`:159-169`).
18. Deleting quotes is also what catches `bash -c "git checkout -- x"` in this gate. It strips to `bash -c git checkout -- x`, and the any-word git scan reads `checkout`. A tokenizer that keeps the quoted text as one word would turn that deny into an allow. [verified: the round-1 staff-sdet review executed the deny; the tokenizer consequence follows from the exact-word match at `_lib.sh:1667`]
19. A stdlib-`shlex` tokenizer already in this repo lists its own residual over-denies and misses. [verified: `claude/.claude/hooks/split-command-words.py:30-59`]
20. Every deny reason in this gate carries "Use Read, Grep, or Glob for a read the hook misjudges." [verified: `test_agent_roster.py:1016` (`SCRATCH_READ_TOOL_SENTENCE`), asserted at `test_deny_reviewer_tree_mutation.py:114-132`]
21. Design-decision file rules:
    - An H1, a blank line, then an italic dated line 3.
    - A slug matching `^[a-z][a-z0-9-]*\.md$` that never changes after merge.
    - No index entry.
    - Every relative link resolves.

    [verified: `.claude/rules/design-decisions.md`; `test_design_decision_files.py:255-302, 455-483`]
22. Hook comments already cite `docs/design-decisions/<slug>.md` by path: 26 occurrences across 11 hook `.sh` files, for example `deny-no-op-dispatch.sh` and `guard-settings-session-keys.sh`. [verified: grep this session]
23. `CHANGELOG.md`'s `## [Unreleased]` has a `### Fixed` section at line 378. [verified]
24. `_lib_split_fragments` splits `git -C "$(git rev-parse --show-toplevel)" checkout x` at `$(` (`_lib.sh:1950`). That leaves the fragment `git -C`, whose empty subcommand denies, and the fragment `git rev-parse --show-toplevel) checkout x`, which reads as the allowed `rev-parse`. Allowing an empty subcommand would therefore allow the checkout. [unverified: traced by reading, not executed]
25. Reusing `parse-git-command.py` would miss `bash -c "git checkout x"`. [unverified: adversarial-review finding; shlex POSIX mode keeps the quoted string as one token, but the script's own handling was not traced]
26. Each allowed git fragment from a review-only agent now runs about 16 more subshell forks: one per `_lib_fragment_invokes_tool` call in the later arms, plus the raw-write process substitution with its cp/mv/tee lookups. Every non-git fragment already runs them. Other agents never reach this code, because the agent-type fast exit runs first (header:24-28). [verified: count by reading hook:438-520 and `_lib.sh:2010-2013`] [unverified: latency, which Verification step 3 measures]
27. The engineer delegated two scope calls to the architect. [engineer-verified: "Ask the architect"] The architect's answers, which the plan adopts, are its own judgment and not the engineer's [unverified]:
    - Keep the in-place-edit sibling coverage in scope: the same one-line deletion, plus `test_reviewer_in_place_edit_mentioning_git_subcommand_denied`.
    - Ship both the over-deny pin test and the CHANGELOG `### Fixed` entry. The precedent for pinning an accepted over-deny is `test_deny_reviewer_tree_mutation.py:401-412` and `TestPureFormatterCheckModesDenied` (:849-854). The precedent for CHANGELOG entries on verdict shifts is `CHANGELOG.md:383-384, 392`.
28. Falling through also makes `git diff >&2`, `git log > /dev/stderr`, and `git log > /dev/stdout` deny, because only `/dev/null` and `/tmp/*` are exempt (hook:510-517). That gives git fragments the verdict `echo x >&2` already gets. [verified: the round-1 staff-sdet review executed all three, allow before the deletion and deny after] This ships as a second accepted false-deny class beside quoted `>`. The hook header, `docs/hooks.md`, and the CHANGELOG state it, and a test pins it. [engineer-verified: "Accept + pin (Recommended)"]
40. The decision file's reconsideration trigger on the sandbox write set stays. [engineer-verified: "Keep it (Recommended)"]
41. The shorthand label used during planning is now an entry in the engineer's user-local, gitignored `~/.claude/private-projects.md`, so `deny-private-project-refs.sh` blocks it, as a case-insensitive whole word, in commits and PR bodies. Verification step 6 is the backstop. [engineer-verified: "You add it"; the session appended the entry, and `.gitignore:55` ignores `claude/.claude/private-projects.md`]
42. The case study pins its results to `claude` 2.1.283, cites vendor docs fetched 2026-09-25/26, and ran only cooperative-agent-shaped trials. The Write tool bypasses the sandbox but refuses symlinks on its own. [verified: `docs/case-studies/claude-code-sandbox-write-boundary.md:62-72, 542-545, 565-567, 583`]
29. The header's Known-gaps bullet at hook:128-130 calls `bash -c "git checkout"` "still undecidable", but it denies today: quote stripping yields `bash -c git checkout`, and the any-word git scan catches it. The shape that is actually allowed is `bash -c "sed -i s/a/b/ x"`, pinned at `test_deny_reviewer_tree_mutation.py:899-902`. [verified: the git half was executed by the round-1 staff-sdet review; the sed half is the existing pinned test]
30. The hook budget is under 100 ms per fire. [verified: `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` § "7. Performance budget" (:135)]
31. This branch edits no file the hook sources (`_lib.sh`, `_config.sh`, `config-keys.psv`). The hook timed before M1 therefore stands in for the merge-base hook. [verified: the Critical files list]
32. `black tools/git status.py` reaches the git arm's `continue` today: `tools/git` ends in `/git`, and the next word `status.py` reads as `status`, an allowed subcommand (`_lib.sh:1700-1725`, `:3873`). After M1 its command word resolves to `black` (`_lib.sh:1970-2002`), and the formatter arm denies. [unverified: traced by reading; Verification step 1 executes it]
33. `bash -c "git show HEAD:f > src/tracked.py"` quote-strips to `bash -c git show HEAD:f > src/tracked.py`. The git arm reads `show` and stops at the `continue` today. After M1 the command word `bash` matches no tool arm, and the raw-write loop reads the target `src/tracked.py`. [unverified: traced by reading hook:399-424 and 508-520; Verification step 1 executes it]
34. `git log -n 5 2>&1` and `git diff 2>/dev/null` stay allowed after M1. An fd-numbered word starts with a digit, so neither `>` arm of `_fragment_raw_write_targets` matches it (hook:235-246), and `_lib_split_fragments` splits on `&&`, not on a lone `&` (`_lib.sh:1950`). [verified: by reading; the round-1 staff-sdet review also executed `git diff 2>&1 | head` and `git diff 2>/dev/null` as allowed on a patched copy]
35. Verification step 6 reads only what the branch adds (`git diff origin/main...HEAD`, `git log origin/main..HEAD`) and the new PR body, so it prints nothing at the merge-base. [verified: both ranges are empty when HEAD is the merge-base]
36. `/pr-description` in author mode reports its body file on a `BODY_FILE: <path>` line. [verified: `claude-skills/skills/pr-description/SKILL.md:226-229`]
37. The contributor `.venv` exists only in the main worktree root. From this linked worktree the tools are therefore `../../../.venv/bin/pytest`, `ruff`, `python3`, and `shellcheck`, the last installed by `shellcheck-py`. [verified: README.md:306, 517, 523, 554; `requirements-dev.txt:5`]
38. `transcript-analysis.py review-trace --deny-summary` groups denials by gate, and this gate's label is `reviewer-tree-mutation`. [verified: `docs/transcript-analysis.md:491-505`; `claude/.claude/scripts/transcript_analysis/denials.py:142`]
39. This gate denies only the write shapes its header names. Any other write verb or form goes uninspected. [verified: hook:65-70]

**Mechanisms:**
- M1. Replace the `continue` at hook:424 with a two-line comment: `# An allowed git fragment falls through to the in-place-edit and` / `# raw-write checks below.` — anchors: root, row1, row2, row3, row4, row32. This is the lightest primitive available, a one-line deletion. Two options look lighter, a second call site for the raw-write loop and hoisting the loop, but each leaves rows 3 and 32 open.
- M2. Seven new test functions (17 cases) in `test_deny_reviewer_tree_mutation.py`; the specs are under Critical files item 2 — anchors: row3, row5, row6, row12, row18, row28, row32, row33, row34.
- M3. A new decision file, `docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md` — anchors: row8, row9, row10, row13, row15, row16, row18, row19, row20, row21, row38, row39.
- M4. Two hook-header edits and three sentences in the `docs/hooks.md` bullet — anchors: row5, row14, row22, row28, row29.
- M5. A `CHANGELOG.md` `### Fixed` entry — anchors: row23.

**Redaction, which binds every shipped artifact:** code, tests, docs, CHANGELOG, commit message, PR body, and this plan file. None carries the shorthand label used for this failure class during planning, and none states a count or rate of false denies (`docs/private-project-redaction.md` § "Publishing a tooling measurement" governs any figure). Describe the failure class generically. Durable text states the reasoning as the decision's own and does not quote this session.

## Critical files

One `code-writer` dispatch covers all five files. They share one background, the decision rationale, and splitting would make every prompt restate it. Inside the dispatch, work in this order:
1. Write the tests, and write the timing script from Verification step 3.
2. Confirm the tests fail as Verification step 1 expects.
3. Change the hook.
4. Confirm green, and run the timing script (Verification step 3).
5. Write the docs.
6. Write the CHANGELOG entry.

1. `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`
   - Line 424: M1.
   - After line 106, the end of the formatter check-mode false-deny bullet, add this Known-gaps bullet:
     ```
     #   - These read-only shapes deny as raw writes, an accepted false deny:
     #       - A `>` inside quoted text (`git log --grep 'a > b'`).
     #         docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md
     #         records why.
     #       - A redirect to `>&2`, `/dev/stderr`, or `/dev/stdout`, because the
     #         raw-write check exempts only `/dev/null` and `/tmp/*` targets.
     ```
   - In the Known-gaps bullet at lines 128-130, change the nested-shell example from `bash -c "git checkout"` to `bash -c "sed -i s/a/b/ x"`, the shape that is actually allowed (row 29). Rewrap if needed, and change nothing else in that bullet.
   - Leave `SANCTIONED_ALTERNATIVE`, the tier line, and the deny strings unchanged.
2. `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`. Reuse `bash_input`, `run_hook`, and `run_hook_reason` (which returns `None` on allow), and use `agent_type="staff-sdet"` throughout. Every reason-asserting test asserts `reason is not None` before its substring check, following the pattern at :963-965, so a red-phase allow fails as an assertion rather than a `TypeError`.
   - `TestBashGitWrites` (331):
     - `test_reviewer_nested_shell_git_write_denied`: the command `bash -c "git checkout -- x"`. The reason contains `"'git checkout' is not a read-only git subcommand"`. One-line comment: quote stripping exposes the git word inside `bash -c`.
   - `TestBashInPlaceEditFamily` (420):
     - `test_reviewer_in_place_edit_mentioning_git_subcommand_denied`, parametrized over (command, reason substring). One-line comment: a word ending in `/git` routes the fragment to the git arm, which must not end its checks.
       - `sed -i 's/git log/x/' src/x` → `"rewrites the file in place"` (id `sed-dash-i`).
       - `black tools/git status.py` → `"'black' reformats files"` (id `formatter`).
   - `TestRawWriteTargetGap` (627):
     - `test_reviewer_git_redirect_to_tracked_path_denied`, parametrized. Each reason contains `"writes to 'src/tracked.py', which is outside /tmp."`.
       - `git show HEAD:f > src/tracked.py` (id `spaced`).
       - `git log >> src/tracked.py` (id `append`).
       - `git log >src/tracked.py` (id `attached`).
       - `git log >>src/tracked.py` (id `attached-append`).
       - `bash -c "git show HEAD:f > src/tracked.py"` (id `nested-shell`).
     - `test_reviewer_git_redirect_to_tmp_allowed`: `git show HEAD:f > /tmp/scratch/f` gives `allow`.
     - `test_reviewer_git_redirect_of_stderr_allowed`, parametrized, each giving `allow`: `git log -n 5 2>&1` (id `stderr-to-stdout`) and `git diff 2>/dev/null` (id `stderr-to-dev-null`).
     - `test_reviewer_git_redirect_to_standard_stream_over_denied`, parametrized over (command, target). Each reason contains `f"writes to '{target}', which is outside /tmp."`. Docstring: an accepted false deny named in the hook header's Known gaps, because only `/dev/null` and `/tmp/*` targets are exempt.
       - `git diff >&2` → `&2` (id `fd-duplication`).
       - `git log > /dev/stderr` → `/dev/stderr` (id `dev-stderr`).
       - `git log > /dev/stdout` → `/dev/stdout` (id `dev-stdout`).
     - `test_reviewer_quoted_redirect_character_over_denied`, parametrized over (command, target), with the same reason assertion. Docstring: the false deny is accepted, and `docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md` records why.
       - `git log --grep 'a > b'` → `b` (id `git-fragment`).
       - `git grep -n "x >= 5" src` → `=` (id `git-comparison-operator`).
       - `grep 'a > b' src/x` → `b` (id `non-git-fragment`).
3. `docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md` (new). It opens with the H1 `# Command-text gates accept over-denying quoted text`, a blank line, then `*2026-10-18.*`. The sections follow, each claim with the source the file names. Every case-study citation uses the exact heading text, on one line.
   - **Opening paragraph.**
     - Gates on the Bash tool match command text.
     - Gates that run `_lib_strip_shell_quotes` delete quote characters before matching. A trigger word or `>` inside a quoted argument or heredoc body can therefore deny a read-only command. Example: `git log --grep 'a > b'` in `deny-reviewer-tree-mutation.sh` reads as a redirect to `b`.
     - The false deny is accepted. Gates keep reading command text, and no shared quote-aware tokenizer is built.
   - **Why the gates keep reading command text.**
     - A hook sees the command before it runs, not what it writes while running. Cite this with a relative link to `../case-studies/claude-code-sandbox-write-boundary.md`.
     - A gate denies a write shape it recognizes, such as a redirect, `cp`, or `tee`, even when it cannot see what the write contains.
     - A write shape a gate does not recognize is an accepted gap listed in that gate's header. For this gate that is the Known gaps section of `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`. Cite it; do not restate the gaps.
     - The reviewer gate is `irreversible`, and `docs/hooks.md` § "Threat-model tiers" holds that false-positive cost alone does not justify relaxing such a gate.
   - **Why not a quote-aware tokenizer.**
     - Deleting quotes is what exposes a nested git command to the reviewer gate's any-word git scan, so `bash -c "git checkout -- x"` denies.
     - A tokenizer that keeps the quoted text whole sees no `git` word, so ending the false deny opens a false allow.
     - Scope that claim to nested git commands: the command-word checks already allow `bash -c "sed -i s/a/b/ x"`.
     - `claude/.claude/hooks/split-command-words.py` uses stdlib `shlex`, and its module docstring still lists residual over-denies and misses.
     - Every gate sharing one tokenizer would share its gaps at once.
   - **Why removing Bash from review-only agents does not settle this.**
     - It ends the exposure for this gate only.
     - The eight reviewer personas' Scratch execution sections use Bash for read-only inspection and scratch runs.
     - The main session and the other agents still run Bash under the other gates.
     - Assert nothing about any issue's status.
   - **The sandbox is worth assessing but is not a drop-in replacement.** Open with the scope note (row 42): every measurement comes from one Linux spike pinned to `claude` 2.1.283, against vendor docs fetched 2026-09-25/26, using only cooperative-agent-shaped trials, and macOS is untested. Cite `` `docs/case-studies/claude-code-sandbox-write-boundary.md` § "Limits" ``. Then give row 16's facts, one sentence each, each labeled measured or cited. Every citation below uses the full grammar of `.claude/rules/citation-grammar.md`: the backticked target `docs/case-studies/claude-code-sandbox-write-boundary.md`, then `§`, then the exact heading, all on one line.
     - Closed, measured with the escape hatch off (Run A; cite `docs/case-studies/claude-code-sandbox-write-boundary.md` § "Method"): Bash writes through a symlink to a path outside the write set, including a write from inside a running script, which no command-text hook can see. The Write tool is outside the sandbox and refuses symlinks on its own. Cite § "Q1 — symlink and hard-link writes outside the allowed set".
     - Not closed, measured: a hard link created before the sandboxed command ran. Same section.
     - Escape hatch, measured in one trial: bare `Bash` granted, the setting at its default, and a retry the prompt instructed succeeded silently. The setting can be turned off, and no trial retried with it off. Cite § "Q3 — a subagent's policy" and § "Method".
     - Session-wide scope, cited: enabling the sandbox for reviewers enables it for every agent. Cite § "Q3 — a subagent's policy".
     - Fail-open when the sandbox cannot start, cited and not exercised. Cite § "Q2 — what breaks session-wide".
     - Compatibility cost, measured on a bounded sample: the sibling `git worktree add` failure and the literal-`/tmp` denial. Cite § "Q2 — what breaks session-wide".
     - Close with: adopting the sandbox is its own decision with its own companions (the escape hatch off, a control for pre-created hard links, compatibility settings). It is not a swap for these gates, and its assessment stays open.
   - **What a denied reviewer does.** The deny reason tells the reviewer to use Read, Grep, or Glob for a read the hook misjudges (`SANCTIONED_ALTERNATIVE` in the hook). The record states no figures.
   - **Reconsideration trigger.** Reopen if any of these holds:
     - Claude Code passes PreToolUse hooks a parsed form of the Bash command.
     - The sandbox gains a per-subagent policy, or its assessment finds that a filesystem write set can carry the write invariant of this gate's Bash arm for review-only agents (row 40).
     - This gate's denials, grouped under the `reviewer-tree-mutation` label by `transcript-analysis.py review-trace --deny-summary`, show reviewers repeatedly hitting this false deny. Any figure cited from that output follows `docs/private-project-redaction.md` § "Publishing a tooling measurement".
4. `docs/hooks.md`, line 150, the `deny-reviewer-tree-mutation.sh` bullet. After the sentence ending "a stricter default rather than an unchecked one.", insert: "The check runs on every fragment, a fragment read as an allowed read-only git command included (`git show <ref>:<path> > src/x` denies). A `>` inside quoted text reads as a redirect and denies, and [`docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md`](design-decisions/command-text-gates-accept-quoted-text-over-deny.md) records why that false deny is accepted. A redirect to `>&2`, `/dev/stderr`, or `/dev/stdout` also denies, because only `/dev/null` and `/tmp/` targets are exempt."
   - The first two sentences are row 14's; the third is anchored to row 28.
   - Do not touch the tier table, the tier counts, or other bullets (G3).
   - Line 150 is one physical line, so a concurrent edit to it conflicts textually. That conflict is accepted; the second branch to merge resolves it by hand.
5. `CHANGELOG.md`: a new first bullet under `### Fixed` (line 378). Suggested text:
   - Lead: "**`deny-reviewer-tree-mutation.sh` now runs its redirect and in-place-edit checks on a fragment it reads as an allowed read-only git command.**"
   - Body:
     - A review-only agent's `git show <ref>:<path> > src/x` now denies like `cp scratch src/x`.
     - So does a fragment that only mentions an allowed git subcommand, such as `sed -i 's/git log/x/' file`.
     - A `>` inside quoted text in a git command now denies too, as does a git redirect to `>&2`, `/dev/stderr`, or `/dev/stdout`. Other fragments already got these false denies. Link the decision file.
     - Redirect to a literal `/tmp/` path, or use Read, Grep, or Glob.
     - Live on `git pull`.
   - Neither this entry, the commit message, nor the PR body says the git arm's write hole is closed: `git diff --output=<path>` stays a header-listed gap (hook:66-70).

## Verification

Run every command from the worktree root (row 37).

1. **Red first.** With the tests added and the hook unchanged, run `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py -k "git_redirect or mentioning_git or nested_shell or quoted_redirect"`. It selects 17 cases.
   - **Expected failures (12),** each on `assert reason is not None`, because the verdict is allow:
     - all five `git_redirect_to_tracked_path_denied` cases;
     - both `in_place_edit_mentioning_git_subcommand_denied` cases;
     - all three `git_redirect_to_standard_stream_over_denied` cases;
     - the `git-fragment` and `git-comparison-operator` cases of `quoted_redirect_character_over_denied`.
   - **Expected passes (5):**
     - `nested_shell_git_write_denied`;
     - `git_redirect_to_tmp_allowed`;
     - both `git_redirect_of_stderr_allowed` cases;
     - the `non-git-fragment` case.
   - Any other result means row 3, 5, 18, 28, 32, 33, or 34 is wrong. Stop and report.
   - Run the step 3 timing script now, before M1.
2. **Green.** Apply M1 and rerun step 1's command. All 17 cases pass. Run the step 3 timing script again, in the same session on the same machine.
3. **Latency.**
   - In a `mktemp -d /tmp/hook-timing.XXXXXX` directory, write a stdlib-only Python script.
   - The script pipes a payload shaped like `bash_input` (`claude/.claude/tests/helpers.py:564-577`), with `agent_type` `staff-sdet`, into `claude/.claude/hooks/deny-reviewer-tree-mutation.sh` 21 times per command. It discards the first, cold run and prints the median and maximum wall time of the other 20. Wall time includes the hook's `_lib.sh` source and `jq` calls, which are the same before and after M1.
   - Commands:
     - A five-fragment git command: `git status && git diff --stat && git log -n 5 && git show HEAD --stat && git blame -L 1,5 README.md`.
     - A five-fragment non-git reference: `head -5 README.md && wc -l README.md && grep -n x README.md && tail -5 README.md && ls docs`. Its fragments already pay every later arm's forks before M1 (row 26).
     - A one-fragment git command: `git status`.
   - Run the script with `../../../.venv/bin/python3 -I <script path>`, before M1 (step 1) and after M1 (step 2). The hook before M1 stands in for the merge-base hook (row 31).
   - On one contributor machine, five-fragment commands measured above 100 ms through the unchanged hook during plan review. So the 100 ms budget (row 30) applies only to the one-fragment command, and M1's own cost is judged against the non-git reference.
   - Stop conditions, each judged on after-M1 medians. If one fires, rerun the script once. Stop only if it fires again.
     - The five-fragment git command's median exceeds the five-fragment non-git reference's median. After M1 a git fragment pays the same arms a non-git fragment pays, so the git command should cost no more.
     - The one-fragment `git status` median is 100 ms or more.
   - On a stop, keep M1 and the tests in the working tree, uncommitted. Report all six medians and make no further hook change. The engineer decides whether the per-fragment command-word fix (Out of scope) comes first.
4. **Scoped suite.** Run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Its stderr target line must include `claude/.claude/hooks/tests`. That directory holds:
   - `test_deny_reviewer_tree_mutation.py`, including the existing `git status > /dev/null` allow (row 6) and the `git log --grep black` allow (:809);
   - `test_design_decision_files.py`, which checks the new file's shape and relative links;
   - `test_hook_alignment.py`.
   - A skipped `test_shellcheck` is expected when no `shellcheck` binary is on PATH. Step 5 covers it.
   - No test checks headings that a decision file cites. For each `` `<file>` § "<heading>" `` citation in the new decision file, `grep -n -F` the heading text in that cited file and confirm it appears on a heading line. That covers the case study, `docs/hooks.md`, and `docs/private-project-redaction.md`.
5. **Lint.** Run `../../../.venv/bin/ruff check claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`, then `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
6. **Redaction backstop.** `deny-private-project-refs.sh` is the primary control (row 41). The session runs this check after the commit and after `/pr-description` reports its `BODY_FILE:` path (row 36), before the PR opens.
   - Run `git fetch origin main` first, so `origin/main` is current. Then `git diff --stat origin/main...HEAD` must succeed and list this branch's files, so a failed or empty range cannot read as a pass.
   - In a `mktemp -d /tmp/redaction-check.XXXXXX` directory, write a pattern file holding the shorthand label used for this failure class during planning. Put one literal form per line: as written, and with a space, a hyphen, and an underscore separator. The file stays outside the repo, so neither the plan nor the diff spells the label.
   - Guard against a check that passes by accident:
     - The pattern file must be non-empty.
     - Positive control: `printf 'x_%s_y\n' <the label> | grep -i -F -f <pattern file>` must print one line. This proves the pattern matches the label inside a snake_case identifier.
   - Run these four commands with `-i -F` and no `-w`, so a label inside a snake_case identifier is caught. Each must print nothing and exit 1. Exit 2 is an error, not a pass.
     - `git diff -U0 origin/main...HEAD | grep '^+' | grep -n -i -F -f <pattern file>`. This covers every added line, which includes the new decision file and this plan file in full.
     - `git log --format=%B origin/main..HEAD | grep -n -i -F -f <pattern file>`. This covers every commit message on the branch.
     - `grep -n -i -F -f <pattern file> <BODY_FILE path>`. This covers the PR body.
     - `printf '%s\n' '<PR title>' | grep -n -i -F -f <pattern file>`. This covers the PR title.
   - All four read only what the branch adds, so they pass at the merge-base (row 35).
   - No pattern catches a figure. The reviewer of the diff and the PR body checks any figure against `docs/private-project-redaction.md` § "Publishing a tooling measurement" by reading.

## Out of scope

- **Masking quoted text in `_lib_strip_shell_quotes`, or a shared tokenizer.** This is the decision being recorded (rows 9, 18, 19), and the function is shared `_lib.sh` code.
- **`git diff --output=<path>` and other output-flag writes.** This is the closed-enumeration gap already in the header (hook:66-70, row 39).
- **A target behind a bare `&` (GH-811), `&>`, and fd-numbered redirects.** These are existing header gaps (hook:71-83).
- **Exempting `>&N`, `/dev/stdout`, or `/dev/stderr` targets.** A blanket `>&*` exemption would open `>&word`, which writes a file named `word` when `word` is not a number. A narrower exemption is a separate relaxation with its own review, and nothing in this change calls for it (row 28).
- **Resolving the command word once per fragment.** This is the remedy if Verification step 3 stops. It changes every later arm's call shape and the raw-write helper's cp/mv/tee lookups, so it is its own change.
- **A git-fragment traversal case (`git show f > /tmp/../src/x`).** The traversal arm (hook:512) is shared by every fragment, and `test_reviewer_redirect_traversal_out_of_tmp_denied` (:667) already pins it.
- **Allowing an argument-less git call (`git --version`).** It would reopen the bypass in row 24.
- **Linking the decision file from other command-text gates.** For example, `deny-network-installs.sh`'s bullet at `docs/hooks.md:152` already states its own accepted over-deny. The selected record home scopes the link to the reviewer gate.
- **Persona bodies and `SANCTIONED_ALTERNATIVE`.** Both stay unchanged, because the persona text already matches the new behavior (row 7).
