# Per-repo opt-in for deny-private-project-refs

## Context

Goal: let a repo other than claude-config opt in to `deny-private-project-refs.sh` with a committed `.claude/private-projects-optin` marker, so the whole hook (tracker IDs, the private-projects blocklist and the structural shapes) runs there, while repos without the marker stay silent.

Ask: "I don't want the private projects check to run on every repo that is worked on by a user who has stowed claude-config, just ones taht are opted in" — scope of the opt-in accepted via label "Whole hook (Recommended)".

Before this change the hook exits at `claude/.claude/hooks/deny-private-project-refs.sh:392-401` (pre-change) unless `remote.origin.url` contains `claude-config`, before every scan. A sibling session working in a private repo that must keep a private project's proprietary information out of tracked files and PR text needs these checks there, and asked for the change through this session.

## Approach

The hook keeps its claude-config URL test unchanged and permanently. It also gates any repo whose current worktree has `.claude/private-projects-optin` at its toplevel, running the whole hook there. In such a repo, each of the three content-match denies (the tracker-ID deny, the structural-detector deny, and the blocklist deny) gains a note that names the marker and the correct remedy. The tracker deny also swaps its `OSS_ALLOWLIST` lead-in for "Rewrite", and the staged-diff scan covers every path. Repos with neither stay silent. The staged-diff read hardening (plain-output flags, `-c diff.relative=false`, and a fail-closed exit check) sits outside the scope `if`, so it applies in every gated repo, claude-config and its forks included. The accepted gaps (activation window, further activation shapes, uncapped staged-diff read, binary and `-diff` files, blocklist echo) are recorded in the hook header's Known gaps, and the activation window is summarized in the docs.

Code at `claude/.claude/hooks/deny-private-project-refs.sh`:

```bash
# (pre-change L398-401 becomes)
OPTIN_DENY_NOTE=""
TRACKER_REMEDY_LEAD="If the match is an open-source reference or technical constant not on the allowlist, add the prefix to the OSS_ALLOWLIST variable in ~/.claude/hooks/deny-private-project-refs.sh. Otherwise rewrite"
STAGED_DIFF_PATHSPEC=':(top,exclude)claude/.claude/hooks/tests/**'
REMOTE_URL=$(git config --get remote.origin.url 2>/dev/null)
if [[ "$REMOTE_URL" != *claude-config* ]]; then
  [[ -f "$REPO_ROOT/.claude/private-projects-optin" ]] || exit 0
  OPTIN_DENY_NOTE=" This repo opted in to this gate via .claude/private-projects-optin, so content that matches here (its own tracker IDs, structural shapes, and names on the user's private-projects.md blocklist) denies by design. Reword the content; if it cannot be reworded, stop and ask the human. Do not delete or edit the marker, retarget the command with git -C, edit the user's private-projects.md blocklist, or add this repo's own tracker prefix to OSS_ALLOWLIST in deny-private-project-refs.sh, which ships in the public claude-config repo."
  TRACKER_REMEDY_LEAD="Rewrite"
  STAGED_DIFF_PATHSPEC=':(top)'
fi
```

Each of the three content-match `emit_deny` calls appends `${OPTIN_DENY_NOTE}` to its reason string, placed before the `chain_split_hint_if_chained` suffix. The tracker deny's remedy sentence reads `${TRACKER_REMEDY_LEAD} the commit message / staged content / ...`. The staged-diff read (the `STAGED_DIFF=` assignment) becomes `git -c diff.relative=false diff --cached --no-color --no-ext-diff --no-textconv -- "$STAGED_DIFF_PATHSPEC"`, the plain-output flag set that `deny-pii-in-commits.sh` already uses, and a nonzero exit denies with an infrastructure message that carries no `OPTIN_DENY_NOTE`. Infrastructure denies (parse failure, unreadable body file, and the other fail-closed sites) keep their text, because "reword the content" would be wrong advice there. The read hardening applies in both arms, because the flags, `-c diff.relative=false`, and the exit check sit outside the scope `if`; in claude-config a git error that previously read as an empty diff and allowed now denies, and a `color.ui=always` config no longer hides a token. When the URL test arms the gate, the note stays empty, the lead-in keeps its original text, and the pathspec keeps its exclusion, so claude-config's deny text and pathspec do not change. With the default lead-in, the tracker deny's text is byte-identical to the pre-change text.

Alternatives weighed:
- **Move claude-config onto the marker and drop the URL test.** Rejected, and the URL test is permanent. The origin URL lives in the shared git config, so today claude-config is gated in every worktree, branch, and checkout. Moving it onto the marker would create the activation window (row 19) in a repo that currently has full coverage. For an opted-in repo the comparison runs the other way: today it has no coverage at all, and the window is the cost of using a committed marker, which the engineer accepted (row 19). The two choices apply one standard: never introduce a window where coverage is complete today. A fork can never be guaranteed to carry the marker, so no migration end state exists.
- **Close the window by also reading the marker at `origin/<default>`** (the `_lib_verification_cache_sentinel_present` mechanics). This would cover pre-marker branches, sparse checkouts, and a locally deleted marker. Rejected because:
  - The engineer chose "Accept + record" (row 19).
  - It adds a capped git call to every gated fire in every unmarked repo of every stow consumer.
  - It depends on `origin/HEAD` being set and fetched.
- **Read the marker at the main worktree root, or at both roots.** Rejected. Reading the main root only would leave a branch that adds the marker ungated until the main checkout pulls it. Reading both roots would close only the pre-marker-worktree slice of the window that row 19 already accepts.
- **Read the marker from the default-branch blob alone.** Rejected per row 6.
- **Use a machine-level list of opted-in repos.** Rejected because it does not travel with the repo. Neither does the gate itself (row 25), but the marker at least reaches every clone whose machine has the hook.
- **Documentation only for the deny-text problems (rows 12 and 22).** Rejected because the docs reach the human who opts in, while the blocked agent reads only the deny message.
- **Reword the tracker deny's `OSS_ALLOWLIST` advice for all repos.** Rejected because that advice is correct in claude-config.
- **Leave the `OSS_ALLOWLIST` advice in the marker arm and rely on the note alone to forbid it.** Rejected because the deny would then offer the remedy in one sentence and forbid it in the next. The marker arm sets `TRACKER_REMEDY_LEAD` to "Rewrite" and keeps the note's prohibition as the second closure of the misroute.
- **Keep the unconditional test-path exclusion in the staged-diff read.** Rejected because in a marker-armed repo that contains `claude/.claude/hooks/tests/`, staged content there would pass unscanned. The pathspec is per-arm (row 14).
- **Narrower scans in opted-in repos.** Ruled out by row 2.
- **Run everywhere with an opt-out.** Ruled out by row 1.

### Assumption ledger

**Root:** In any repo whose origin URL lacks `claude-config`, the hook exits before every scan (L398-401). A repo that must keep private-project content out of commits and PR/issue text therefore has no mechanical gate. The fix must let such a repo opt in without turning the gate on in every stow consumer's other repos.

**Givens:**
- The opting-in repo's owners commit and remove the marker. claude-config only reads it. Reason: another party owns that repo's tree.

**Rows:**
1. A repo other than claude-config is gated only when it opts in. [engineer-verified: "I don't want the private projects check to run on every repo that is worked on by a user who has stowed claude-config, just ones taht are opted in"]
2. An opted-in repo gets the whole hook, with the cost in row 3 accepted. [engineer-verified: "Whole hook (Recommended)"] [engineer-verified: "You already asked me this and I already answered whole hook. Why are you asking again?"]
3. The cost of row 2 in an opted-in repo:
   - The repo's own tracker IDs deny: any off-allowlist token of that shape in the message, PR/issue text, or an added diff line.
   - Every structural detector runs over added diff lines and command text.
   - Long-hex hits include SHA-pinned workflow actions, image digests, lockfile hashes, full commit SHAs quoted in PR bodies, and UUID fixtures.
   - Loopback and private-range IPv4 addresses, internal-TLD hostnames, home-rooted paths, SSH key paths, and Slack-channel shapes also deny.
   - The repo has no per-path exclusion and no bypass. The claude-config-only test-path exclusion in the staged-diff read was unconditional before this change. The marker arm now sets `STAGED_DIFF_PATHSPEC=':(top)'`, which is what makes "no per-path exclusion" true (row 14).

   [verified: pre-change hook L422, L559, L565-567, L759-768, L798-843, and in the post-change hook the scope block's `STAGED_DIFF_PATHSPEC` assignments and the `STAGED_DIFF=` read] One claim is [unverified]: that a submodule pointer bump's gitlink line trips the long-hex detector. It was traced in review and not executed.
4. claude-config already carries its own built-in redaction. [engineer-verified: "there's also built-in ticket identifier redaction in claude-config"] Keeping its scope on the URL test is this plan's inference.
5. The marker is named `.claude/private-projects-optin`. [engineer-verified: "Use private-projects-optin (Recommended)"]
6. The default-branch-blob read exists because "a working-tree read would let the branch under review opt itself in". That rationale applies to a marker that relaxes a gate, not one that adds restriction. This marker adds restriction, with one disclosure side effect recorded in row 29: a repo-committed marker arms the blocklist echo. The working-tree-read conclusion survives, because a repo that wants to arm the echo commits the marker on its default branch too, so a blob read would not prevent it. [verified: `_lib.sh:1228-1229`]
7. `worktree-required` is read at `--show-toplevel` in 3 of 3 callers (`require-worktree-for-git-writes.sh:213`, `require-worktree-for-file-writes.sh:131`, `nudge-worktree-anchor.sh:110`). The one main-root read, at `set-session-title-from-branch.sh:147`, is a kill switch. [verified: those lines]
8. `REPO_ROOT` (L387) is already computed and is used only for the not-in-repo exit, so the marker test adds no git call. [verified: L387-401; grep]
9. `REPO_MARKER_INVENTORY` reports markers in claude-config's own checkout. A row for this marker would therefore misreport claude-config. [verified: `install.sh:44`, `:576-585`]
10. `opted_in_repo` is already a shared conftest fixture. [verified: `conftest.py:485-491`]
11. The blocklist is one file per config dir. Every entry applies in every opted-in repo, including an entry that names that repo's own project. [verified: L850-881]
12. Deny messages cite "repo CLAUDE.md section 'Redact private-project-identifying content'". That citation resolves only if the opted-in repo has such a section. [verified: grep of `CLAUDE.md` in the hook; most hits are deny strings, a few are comments]
13. Blocklist entries match via `grep -iw -F`. An entry equal to one word of the user's home-directory username therefore matches every appearance of that word in scanned content, including a username that contains it as a hyphen-delimited segment. [verified: L871; `test_deny_private_project_refs.py:1150` pins punctuation as a boundary]
14. The staged-diff exclusion `:(top,exclude)claude/.claude/hooks/tests/**` applied to every gated repo before this change. It is now claude-config-only: the marker arm sets `STAGED_DIFF_PATHSPEC=':(top)'`, so a marker-armed repo scans every path, including one that happens to contain `claude/.claude/hooks/tests/`. [verified: pre-change L559; post-change hook the `STAGED_DIFF_PATHSPEC` assignments and the `STAGED_DIFF=` read; pinned by `test_optin_marker_scans_hook_test_paths_that_claude_config_exempts`]
15. A hyphen is a word boundary for `grep -w`. The platform reviewer confirmed this on `/usr/bin/grep` (BSD), which the hook uses on macOS. GNU grep is unconfirmed until CI runs the new hyphen test. [verified for BSD grep; unverified for GNU grep, settled by CI]
16. A home-rooted path is denied by the structural scan (L819-843) before the blocklist scan runs (L845-889). Row 13's collision therefore adds denials only for the username's other appearances. [verified: L819-889]
17. The username-collision doc bullet and the hyphen-boundary test stay in this PR. [engineer-verified: "Keep both (Recommended)"]
18. The two quotes on rows 1 and 4 reached this session relayed by the sibling session, and the engineer confirmed them as their own. [engineer-verified: "Yes, both are mine"]
19. The marker-activation window is accepted and recorded in the hook header's Known gaps and in the docs. The question named these shapes: a pre-marker branch or worktree, a sparse checkout, a gitignored `.claude/`, a deleted marker, and the `-f` fail-open. [engineer-verified: "Accept + record (Recommended)"]
20. The platform reviewer named further shapes:
    - a detached HEAD, bisect step, or rebase stop at a pre-marker commit
    - `git stash -u` or `git clean` removing an uncommitted marker
    - a miscased marker name, which `-f` accepts on case-insensitive filesystems only

    All three are accepted and recorded with row 19's shapes. [engineer-verified: "Accept all three (Recommended)"]
21. A gap discovered later counts as recorded debt only after the human accepts it in a blocking stop-and-ask, and only when the commit message or PR body states the rationale. The gate header's Known gaps section is the recording site. [verified: `docs/hooks.md:20`, `:55`]
22. The tracker-ID deny tells the reader to add a prefix to `OSS_ALLOWLIST` in `~/.claude/hooks/deny-private-project-refs.sh`. That file is a stow symlink to a tracked file in the public repo. In the marker arm the deny drops that advice (`TRACKER_REMEDY_LEAD="Rewrite"`) and the note forbids the edit. [verified: pre-change L768; post-change hook the `TRACKER_REMEDY_LEAD` assignments, `OPTIN_DENY_NOTE`, and the tracker-ID deny; repo CLAUDE.md, stow `>>` footgun section]
23. The three content-match denies are the tracker-ID deny, the structural-detector deny, and the blocklist deny; the hook's other post-scope denies are infrastructure fail-closed paths. `OPTIN_DENY_NOTE` is appended at exactly those three sites. The staged hook has 23 post-scope deny sites: the 22 the product round-2 trace counted, plus the infrastructure deny for a failed staged-diff read, which carries no note because rewording content cannot fix it (pinned by `test_optin_marker_denies_when_staged_diff_cannot_be_read`). [verified: product round-2 trace of 22 post-scope deny sites; platform round-3 trace and staff-sdet re-review of the staged hook]
24. The fail-open test sets a regular file at `.claude`, so the stat fails with ENOTDIR. That needs no permission bits and runs as root. The rejected `chmod 0o000` design had precedent at `test_marker_script.py:706` (`skipif(os.geteuid() == 0, reason="root bypasses file permission bits")`). EACCES is deliberately not pinned: the test fixes one errno, while the Known-gaps bullet says "a filesystem error". [verified: `test_optin_marker_that_cannot_be_stat_fails_open`; `test_marker_script.py:706`]
25. The gate runs only for Claude Code Bash calls on machines where claude-config's hooks are installed. A plain-terminal or IDE commit is not gated, and the repo-owned backstop is a git-native pre-commit or server-side check. [verified: `docs/security-hardening.md:624-628`]
26. Plans under `.claude/plans/` are tracked and ship with the PR. [verified: `.gitignore:10`]
27. The two stale scope comments are fixed in this PR. [engineer-verified: "Fix in this PR (Recommended)"]
28. The staged diff is read without a size cap, and the blocklist loop spawns one `grep` per entry. Per the Claude Code hooks reference quoted at `_lib.sh:88-92`, a timed-out command hook "doesn't block the tool call", so a diff large enough to outrun the timeout commits unscanned. Only two quantities are unmeasured: the diff size that reaches the timeout, and the default timeout for a hook entry with no `timeout` key (`settings.json:252-255`). The gap is accepted and recorded in Known gaps. The quoted label is the engineer's only recorded answer; the timeout consequence above is this plan's reading of the `_lib.sh` quote, for the human to confirm. [engineer-verified: "Accept + record (Recommended)"]
29. In a marker-armed repo, the blocklist deny echoes the user's matching private-projects.md entries into the session; each echoed entry already appears whole-word in the gated content. Recorded in the hook header Known gaps. [engineer-verified: "Accept + record (Recommended)"]
30. The three stale skill-file scope statements are fixed in this PR. [engineer-verified: "Fix in this PR (Recommended)"]
31. Files git treats as binary, or marks `-diff` in `.gitattributes`, contribute no added lines to the staged diff and are not scanned; recorded in the hook header Known gaps and pinned by an allow-case test. [engineer-verified: "Accept + record (Recommended)"]

**Mechanisms:**
- **M1: scope test.** The URL arm is unchanged. A non-claude-config repo proceeds only if `[[ -f "$REPO_ROOT/.claude/private-projects-optin" ]]` is true, and the check sits before all scans. This is one `-f` call with no helper and no config key. anchors: row1, row2, row8
- **M2: working-tree read at `--show-toplevel`.** Recorded consequences: the gate is armed only while the file is present at that exact path in the current worktree's checked-out tree, and a filesystem error on `-f` reads as absent (fail-open). anchors: row6, row7, row19, row20
- **M3: claude-config keeps its URL test permanently.** anchors: root, row4
- **M4: no `REPO_MARKER_INVENTORY` row.** anchors: row9
- **M5: `OPTIN_DENY_NOTE` and `TRACKER_REMEDY_LEAD`, applied at the three content-match denies only when the marker alone arms the gate.** The note names the marker, says the repo's own IDs, structural shapes, and blocklist names deny by design, gives the stop-and-ask exit for unrewordable content, and forbids the marker-delete, `git -C`, blocklist-edit, and OSS_ALLOWLIST misroutes. In the marker arm the tracker deny also drops its `OSS_ALLOWLIST` lead-in for "Rewrite", so two mechanisms close the OSS_ALLOWLIST misroute. Lighter alternatives are rejected in Approach. anchors: row3, row12, row22, row23
- **M6: Known-gaps bullets in the header plus docs.** The bullets are the recording site for the activation window and its further shapes, the `-f` fail-open, the uncapped staged-diff read, the binary and `-diff` file bullet, the docs pointer bullet, and the blocklist echo. The docs give a one-sentence summary of the activation window and point to the header, and the docs Coverage paragraph holds the canonical list of surfaces the hook never scans. anchors: row19, row20, row21, row28, row29, row31
- **M7: docs.** Canonical home: `## Opting in another repository` in `docs/private-project-redaction.md`. The username caution goes in that doc's "What to put in the file (and what NOT to)" section, because it applies in every gated repo. anchors: row3, row11, row12, row13, row16, row25
- **M8: tests.** Each claim made in docs or code comments gets one pinned test. Comment claims about the sibling hooks' scope (items 7-8) are covered only indirectly, by this hook's scope tests. anchors: row3, row14, row15, row19, row24
- **M9: `STAGED_DIFF_PATHSPEC`.** The staged-diff read takes its pathspec from a variable: the claude-config arm keeps the hook-test exclusion, and the marker arm sets `:(top)` so every path is scanned. anchors: row3, row14

Comment discipline. Replace L392-397 with:

```bash
# Scope: claude-config (an origin URL containing `claude-config`, forks included) plus any repo with .claude/private-projects-optin at its toplevel.
# Repos with neither legitimately reference their own tracker IDs, so the gate stays silent there.
# `git config --get` prints nothing for a missing remote, so a no-remote repo falls through to the marker test.
# The marker is read from the working tree, not a committed ref, because it can only add restriction.
# A repo armed only by the marker gets OPTIN_DENY_NOTE appended to each content-match deny and loses the OSS_ALLOWLIST advice from the tracker deny.
# Only claude-config excludes its own hook tests from the staged-diff scan; a marker-armed repo scans every path.
# See `docs/private-project-redaction.md` § "Opting in another repository"
```

## Critical files

There is one `code-writer` dispatch, because the note text, the tests that assert it, and the docs that describe it depend on one another. Paths are relative to `<worktree-root>`. Every committed file, the plan included, must contain none of these: a home-rooted path, a private-range or loopback IPv4 literal, a hex run of 32 or more characters, or an off-allowlist tracker-shaped token. Write paths in the `/Users/<username>/` angle-bracket form, use `PROJ-<digits>` for tracker examples, and describe collisions by class rather than by literal example. Any hook line number in this plan that is not labelled post-change cites the pre-change hook; post-change sites are named by symbol or section.

1. `claude/.claude/hooks/deny-private-project-refs.sh`
   - L392-401 (pre-change): apply the M1 code and the comments above, including `TRACKER_REMEDY_LEAD` and `STAGED_DIFF_PATHSPEC`.
   - Staged-diff read (L559 pre-change; the `STAGED_DIFF=` assignment post-change): pass `"$STAGED_DIFF_PATHSPEC"` as the pathspec, with a one-line comment that `:(top)` alone matches every path (M9).
   - Same read: the hardening applies in every gated repo, claude-config and its forks included, because the flags and exit check sit outside the scope `if`. Run `git -c diff.relative=false diff --cached --no-color --no-ext-diff --no-textconv`, matching `deny-pii-in-commits.sh`, so `color.ui`, `diff.external`, and `diff.relative` config cannot make the `^\+` filter drop added lines. Capture the exit status and deny on nonzero with an `emit_deny` that names the failure and carries no `OPTIN_DENY_NOTE`, because it is infrastructure. The read stays uncapped (row 28).
   - Header Known gaps: add one bullet for files git classifies as binary or a `.gitattributes` rule marks `-diff`, which the flags do not close (row 31), and one pointer bullet to the docs Coverage paragraph, which is the canonical list of surfaces the hook never scans.
   - Header Scope bullet 3: say the user "creates" `~/.claude/private-projects.md`, so "opt in" in the header means only the marker.
   - L768, L831, L884 (pre-change; the tracker-ID, structural-detector, and blocklist denies): append `${OPTIN_DENY_NOTE}` to each content-match deny reason, before the `chain_split_hint_if_chained` suffix (M5). At L768 also replace the hard-coded `OSS_ALLOWLIST` lead-in with `${TRACKER_REMEDY_LEAD}`. No other deny site changes.
   - Header "Scope and limits" (L24-60): add one bullet stating the repo scope. The hook runs only in claude-config (origin URL contains `claude-config`) or in a repo with `.claude/private-projects-optin` at its toplevel, and is silent elsewhere. The `claude-hook-review` checklist requires the header to document what the hook deliberately does not gate. In the same section, change the private-projects.md bullet from "substring scan" to "whole-word scan" to match the `grep -iw -F` match.
   - Header Known gaps: add one bullet per fact.
     - In a repo armed only by `.claude/private-projects-optin`, the gate is armed only while that file is present at that exact path in the current worktree's checked-out tree at hook time.
     - Each of these leaves the gate silent:
       - a branch, detached HEAD, bisect step, or rebase stop at a commit that predates the marker
       - a sparse checkout that omits `.claude/`
       - a clone the marker never reached because `.claude/` is gitignored
       - an uncommitted marker removed by `git stash -u` or `git clean`
       - a miscased marker name on a case-sensitive filesystem
     - A filesystem error on the `-f` test reads as absent, which fails open.
     - The staged diff is read without a size cap, so a diff large enough to outrun the harness hook timeout commits unscanned (row 28).
     - In a marker-armed repo, the blocklist deny echoes the user's matching private-projects.md entries into that session, so any repo that commits the marker can surface them (row 29).
2. `claude/.claude/hooks/tests/test_deny_private_project_refs.py`
   - **New fixture `private_projects_optin_repo`.** Build it on `unrelated_remote_repo` (L55-66) and have it write an empty, untracked, unstaged regular file at `.claude/private-projects-optin`. Its docstring states two facts. First, the commit scan needs `git_repo`'s non-empty staged diff (L559-560). Second, the marker stays untracked so the tests pin the working-tree read, and a committed-ref read would fail them.
   - **New tests in the Scoping section, after L427.** Base the commands on L370 and flip its expected outcome to deny. Do not paste tracker literals into the plan or the docs.
     1. Parametrized "whole hook": tracker ID in `-m`, blocklist entry (template L945), private-range IPv4 literal (template L2304). Each case is denied, and its `run_hook_reason` names that scan (the token, the entry, or `IPv4 literal`), contains `.claude/private-projects-optin`, and contains the note-only phrases `stop and ask the human`, `Do not delete or edit the marker`, and `edit the user's private-projects.md blocklist`. Assert `reason is not None` first, so a red run fails at the assertion rather than with a TypeError.
     2. A 40-character hex token in a staged added line of `file.txt` is denied with the label `long hex identifier`.
     3. Clean commit is allowed. This is a labelled control: it passes before the change and shows the fixture alone causes no deny.
     4. cwd in a subdirectory, marker at the toplevel: denied.
     5. Plain `git_repo` (no remote) plus the marker: denied.
     6. `gh pr create --body`, mirroring L640's command: denied.
     7. Linked worktree on a branch whose commit carries the marker, with a change staged in the worktree, while the main checkout's working tree lacks the marker: denied. Use the `git worktree add -b` precedent at `conftest.py:558-562`.
     8. Marker committed on main, linked worktree on a pre-marker branch with a staged change: allowed. This pins the accepted window.
     9. `test_optin_marker_below_toplevel_does_not_arm_gate`, parametrized over cwd (`from_toplevel`, `from_marker_subdirectory`): marker only at `sub/.claude/private-projects-optin`, allowed. Running from the directory that holds the nested marker rejects a lookup that walks up from cwd.
     10. A directory at the marker path: allowed.
     11. `test_optin_marker_that_cannot_be_stat_fails_open`: replace `.claude` with a regular file so the stat fails with ENOTDIR, and assert allow. Then restore the marker and assert deny, as a positive control showing the allow came from the stat error. This needs no permission bits, so it also runs as root (row 24). EACCES is not pinned.
     12. A tracker deny in `claude_config_repo` whose reason lacks `.claude/private-projects-optin`, `stop and ask the human`, and `Do not delete or edit the marker`. A second test, `test_claude_config_repo_that_also_has_marker_gets_no_optin_note`, repeats this in a claude-config checkout that also carries the marker (item 17); both assert the `OSS_ALLOWLIST` advice is still present.
     13. `test_optin_repo_infrastructure_deny_carries_no_optin_note`, parametrized over `gh pr create --body-file`, `git commit -F`, and `gh issue create --body-file`, each pointing at a missing source file. The deny is a post-scope fail-closed infrastructure deny. Assert the deny fired (`reason is not None` plus the infrastructure text) and the note's phrases are absent. The test doubles as an arming proof, because it fails when the gate is silent. A malformed-input deny would not do: it fires before the scope block, so it is green before the change and under any note-routing mutation.
     14. `test_optin_tracker_deny_omits_oss_allowlist_advice`: the marker arm's tracker deny omits the `OSS_ALLOWLIST` remedy and "not on the allowlist" while the note still names `OSS_ALLOWLIST` to forbid it.
     15. `test_optin_marker_that_is_a_symlink_to_a_file_arms_gate`: pins the docs' symlink claim.
     16. `test_optin_marker_scans_hook_test_paths_that_claude_config_exempts`: a tracker token staged under `claude/.claude/hooks/tests/` denies in a marker-armed repo. It does not by itself pin the pathspec against an errored read, because an errored read now denies with the infrastructure message and this test asserts only `deny`. The pathspec is defended by the clean-commit control, the reason-asserting cases, `..._keeps_hook_test_path_exemption`, and the repo-root-from-subdirectory test (M9).
     17. `test_claude_config_repo_that_also_has_marker_gets_no_optin_note`: see item 12.
     18. `test_optin_marker_staged_diff_scan_ignores_diff_config`, parametrized over `color.ui=always`, `diff.external`, `diff.relative=true`, and a textconv driver that prints nothing (selected by a `.git/info/attributes` line): a staged tracker token in a marker-armed repo still denies from a subdirectory cwd with a clean `-m`. Without the plain-output flags every case is red.
     19. `test_optin_marker_denies_when_staged_diff_cannot_be_read`: a corrupt index makes the staged-diff read exit nonzero, and the deny names the failure and carries none of the note's phrases.
     20. `test_optin_marker_scans_repo_root_files_when_hook_runs_from_subdirectory`: a violating staged file at the repo root with a clean `-m` denies from a subdirectory cwd, which pins `:(top)` directly and not through the empty-diff early pass.
     21. `test_claude_config_repo_that_also_has_marker_keeps_hook_test_path_exemption`: a synthetic tracker token staged under the hook-tests path still allows in a claude-config checkout that carries the marker, which pins the URL-arm exemption.
     22. `test_claude_config_staged_diff_scan_ignores_diff_config`: `color.ui=always` in a claude-config checkout still denies a staged tracker token, which pins that the read hardening is not marker-conditional.
     23. `test_optin_marker_does_not_scan_files_marked_no_diff_known_gap`: a file marked `-diff` in `.gitattributes` that holds a synthetic tracker token is allowed in a marker-armed repo, which pins the accepted binary and `-diff` gap (row 31).
     24. Added assertions: the marker-armed tracker deny contains the composed "Rewrite the commit message / ..." sentence, the note's `git -C` clause is present in the marker arm and absent in claude-config, "not on the allowlist" has a positive anchor in the claude-config test, and `gh api --input <missing file>` joins the parametrized infrastructure-deny test.

     Tests 8-11 and 16 each use a tracker-ID command and a non-empty staged change, so their allow result cannot come from an empty scan target.
   - **Hyphen test next to L1150.** Add `test_blocklist_match_at_hyphen_boundary` in `claude_config_repo`, parametrized over both sides (`hyphen_after_entry`, `hyphen_before_entry`). Use `run_hook_reason` and assert that the blocklist tier fired and names the entry. The message must contain no `#`, hostname, hex run, home path, or digit-bearing uppercase token. Its docstring states the intent: block a future boundary regex that treats `-` as a word character.
   - **Docstrings to update** so they say the gate runs in claude-config or in an opted-in repo: L43-46, L57-60, the Scoping comment at L362-368, L641-643, and L1082-1086.
3. `docs/private-project-redaction.md`
   - **"The three scans" (near L35):** add one scope sentence pointing to the new section. Qualify the "always on" / "run unconditionally" wording at L37, L42, and L54 so it reads as "in every gated repo".
   - **New `## Opting in another repository`**, before L501, in this order:
     - **What denies there (row 3):** state it by class, and link § "The six structural detectors" rather than restating the table. State that there is no per-repo or per-path exclusion and no bypass, so content that cannot be reworded (such as a lockfile) has one remedy, removing the marker, which disarms every scan.
     - **Suitability:** a short paragraph. Opt in only a repo whose commits and PRs don't cite its own tracker IDs or carry hex digests, unless you accept rewording. It also says the tracker scan denies ordinary technical terms shaped like uppercase letters, a hyphen, and digits (cipher, encoding, and time-zone names) when their prefix is off the allowlist, and that those false positives go to the human to extend the allowlist upstream.
     - **Coverage:** this paragraph holds the canonical list of surfaces the hook never scans, and the hook header's Known gaps pointer bullet targets it. It says the marker is a per-machine control, not a repo-level one: it works only in Claude Code sessions on machines with claude-config's hooks installed, and the blocklist scan depends on each contributor's own file, per config dir. It names the wrapper-form, `$(...)`, `--fill`, and `git -C` or `cd` evasions and points to the header's Known gaps for them. It lists the unscanned surfaces (`gh pr comment`, `gh pr review`, `gh release create`, `gh gist create`, writing files, and any file git classifies as binary or a `.gitattributes` rule marks `-diff`), and says a later commit scans a written file's added lines, never its path. The backstop is a repo-owned pre-commit or server-side check; link `docs/security-hardening.md` § "Limitations".
     - **Opt in:** a recipe, modeled on README § "Activating enforcement on a repo", that writes a short self-describing comment into the marker and commits it. The comment must contain no tracker-shaped, hex, path, or IP content. Then check that `git ls-files .claude/private-projects-optin` lists it; if `.claude/` is gitignored, the marker needs `git add -f`, or it never reaches other clones. Presence alone counts. A regular file or a symlink to one counts; a directory does not.
     - **Activation:** the gate is armed only while the marker is in the checked-out tree of the worktree you work in. In plain words: a branch created before the marker commit stays ungated until it is rebased onto a commit that has the marker. The hook header's Known gaps lists the other shapes that leave it silent. A claude-config checkout without this change, or a revert of it, leaves committed markers silently inert. The positive signal is a content-match deny whose message names `.claude/private-projects-optin`.
     - **Remedies:** reword; if the content cannot be reworded, stop and ask the human. Never add the repo's own tracker prefix to `OSS_ALLOWLIST`, because it ships in the public repo. An open-source prefix that is a false positive goes to the human, who fixes it upstream in claude-config. In an opted-in repo, the deny message itself carries this note.
     - **Blocklist:** `<config-dir>/private-projects.md` is one user-wide file, and an entry naming the opted-in repo's own project blocks that repo from naming itself. The exits are not opting in, or rewording. Deleting or shortening the entry is not a remedy, because that also drops claude-config's protection. Link "What to put in the file" for entries that collide with your username.
     - **Why it fires:** a collaborator seeing these denies finds `.claude/private-projects-optin` at the toplevel, and the deny names it. The repo controls activation. Activation can only add denies, with one disclosure side effect: a blocklist deny echoes the user's matching `private-projects.md` entries into that session, so a repo that commits the marker can surface them there (row 29).
     - **Opt out:** `git rm` the marker and commit. The change reaches each checkout when it pulls or rebases. A local-only delete disarms that checkout too, but it is an unsupported shape recorded in Known gaps, not a per-user disable.
     - **Deny pointer (row 12):** add a section headed exactly "Redact private-project-identifying content" to the opted-in repo's own CLAUDE.md, ideally in the PR that adds the marker.
   - **"What to put in the file":** after L429-435, add a ❌ bullet stating that a hyphen is a word boundary, so an entry equal to one word of your home-directory username matches that username everywhere it appears. Home paths already deny via the structural detector, so the new denials land on remote URLs, handles, and author lines. Use a `<username>`-style placeholder, not a name.
   - **"For fork contributors" (L503):** add that claude-config and its forks need no marker, because the origin-URL test arms them.
   - **Heredoc header L388-389:** change it to "in claude-config, its forks, and repos opted in via `.claude/private-projects-optin`".
4. `docs/hooks.md`, the `deny-private-project-refs.sh` entry (L135 pre-change)
   - Add the scope clause.
   - Replace "a literal substring match" with "a case-insensitive whole-word literal match" (L871 pre-change).
   - Add clause (c) for the six structural shapes, so the entry lists all three scans.
   - Keep the line-start `- **`deny-private-project-refs.sh`**` prefix, which `test_hook_documented_in_hooks_md` needs.
5. `README.md` § "Private-project redaction" (L434-442 pre-change), three edits:
   - the "Three-tier redaction" summary bullet reads "always on in a gated repo"
   - tier-1 list item renamed "Tracker-ID and structural-shape scan" with the six shapes named
   - one added sentence: the hook runs in claude-config and its forks, and in any other repo that commits `.claude/private-projects-optin`; see the new doc section.
6. `CHANGELOG.md` `[Unreleased]`: add one entry under the Keep-a-Changelog heading that fits. It says a repo can now opt in to the redaction gate with `.claude/private-projects-optin`, that the change is live on `git pull` with no re-install, and that claude-config and its forks stay gated by origin URL. It does not say "nothing changes" in repos without the marker: the staged-diff read now ignores color, external-diff, textconv, and `diff.relative` config and denies on a `git diff` error in every gated repo, claude-config included, and the entry says so.

7. `claude/.claude/hooks/deny-pii-in-commits.sh`: comment-only fix. It says `deny-private-project-refs.sh` runs only in claude-config and in repos that commit `.claude/private-projects-optin`. anchors: row27
8. `claude/.claude/hooks/require-stow-reminder.sh`: comment-only fix. It says the hook fires only on the `claude-config` origin URL, the test `deny-private-project-refs.sh` also applies, without its marker arm. anchors: row27

9. `claude-skills/skills/error-mode-analysis/SKILL.md`: minimal edit to the hook-scope statement (~L103) so it says the hook runs in `claude-config` and in repos that commit `.claude/private-projects-optin`. anchors: row30
10. `claude-skills/skills/memory-store-audit/SKILL.md`: minimal edit to the "any other repo has no scan" sentence (~L187-190) with the same scope. anchors: row30
11. `claude-skills/skills/review-pr/REFERENCES.md`: minimal edit to the `origin.url` short-circuit bullet (~L440-441) with the same scope. anchors: row30

`install.sh` is deliberately left untouched (M4).

## Verification

From `<worktree-root>`:

1. **Red first.** Write the tests before editing the hook, then run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
   - The red-first run was executed against the unmodified hook, and the observed red and green sets are recorded in the PR body. The pre-change hook exits at the URL test, so every case that expects deny in a marker-armed repo is red at its `reason is not None` or `== "deny"` assertion, not in a fixture.
   - The diff-config cases of `test_optin_marker_staged_diff_scan_ignores_diff_config` are red until the staged-diff read carries the plain-output flags. `test_optin_marker_denies_when_staged_diff_cannot_be_read` is red until the read's exit status is checked, because an unchecked failure reads as an empty diff and allows.

   Then apply the hook change and rerun. Everything passes, including the existing silent-repo tests: `test_unrelated_remote_suspect_token_allowed`, `test_unrelated_remote_suspect_token_in_diff_allowed`, `test_gh_pr_unrelated_remote_allowed`, and `test_blocklist_unrelated_remote_short_circuits`. The `code-writer` return lists the observed red and green sets. The hyphen tests passing settle row 15 locally, and CI settles it on GNU grep.
2. `.venv/bin/ruff check claude/.claude/ claude-skills/`
3. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`
4. `git grep -l 'private-projects-optin' -- ':!.claude/plans'` lists exactly eleven files: the hook, its test file, `docs/private-project-redaction.md`, `docs/hooks.md`, `README.md`, `CHANGELOG.md`, `claude/.claude/hooks/deny-pii-in-commits.sh`, `claude/.claude/hooks/require-stow-reminder.sh`, and the three skill files in Critical files 9-11. Nothing in `install.sh`.
   Committing the two `SKILL.md` edits needs `/skill-review` first, which `require-skill-review.sh` enforces.
5. Committing the plan, docs, README, and CHANGELOG in claude-config runs this hook on them; each commit must pass without rewording around a deny.
6. The PR body states the rationale for each accepted and recorded gap, per `docs/hooks.md:20`: the activation window (row 19), the further activation shapes (row 20), the uncapped staged-diff read (row 28), the blocklist echo (row 29), and the binary and `-diff` file gap (row 31). The PR body and commit messages contain no real username, no real blocklist entry, and not the sibling repo's name.
7. **Handoff.** After merge and a pull in the main checkout, the sibling session commits the marker and the CLAUDE.md section in its repo. The first content-match deny there names `.claude/private-projects-optin`, which confirms the gate is armed. This PR's verification ends at step 5.

## Out of scope

- **Resolving scope from the command's target repo instead of the hook's cwd.** `git -C`, `cd`, or `-R` aimed at another checkout escapes the gate (the header Known gaps bullets on `git -C` and on the cwd-derived scope). Fixing that needs target-repo resolution for every gated surface. The marker test inherits the gap and adds no new one.
- **Closing the activation window.** The engineer accepted it (row 19). The rejected alternative is in Approach.
- **Rewording the hook's `CLAUDE.md` pointers.** The docs' Deny-pointer step, not the note, covers opted-in repos.
- **An arming-check recipe in the docs.** Every shape reviewed either collided with another gate (`require-ready-for-review.sh` denies a `gh pr create` mention, the worktree guard refuses `$CLAUDE_CONFIG_DIR` and `$(...)`, and this hook's own detectors match a home-rooted path), or misread a pre-scope fail-closed deny as "armed". The opt-in note on a real content-match deny is the positive signal instead.
- **The `*claude-config*` substring breadth.** The retained URL test also arms any repo whose origin URL merely contains that string. This is pre-existing and unchanged here.
- **A per-repo blocklist file (row 11)** is a follow-up. It needs a design for a repo-scoped list that is never committed.
- **A per-path exclusion or bypass for opted-in repos** is deliberately deferred. Whole hook stands (row 2). Content that cannot be reworded, such as a lockfile, has only one remedy, removing the marker, which disarms every scan. The docs say this.
- **Large-diff cost.** The uncapped staged-diff read is accepted (row 28) and recorded in the hook header's Known gaps; measurement remains a follow-up. The staged diff (the `STAGED_DIFF=` read) is uncapped. The blocklist loop spawns one `grep` per entry. Performance has been measured only up to 500 KB bodies. A timed-out hook does not block the tool call (`_lib.sh:88-92`), so a diff that outruns the timeout commits unscanned. What stays unmeasured is the diff size that reaches the timeout and the default timeout for a hook entry with no `timeout` key (`settings.json:252-255`). The follow-up measurement: a scratch repo with the marker, a 20-50 MB staged diff, and a 20-entry blocklist, timed against the harness timeout.
- **Header L28-31 and L154 (pre-change)** name `~/.claude/private-projects.md` where L850-853 prefers `$(_lib_config_dir)`. This is pre-existing, and those lines are otherwise untouched. The new docs write `<config-dir>/private-projects.md`.
- **Submodule behavior** is left out of the docs rather than tested.
