# Plan: state exactly what `/plan-it`'s push offer publishes

## Context

Goal: tighten the push-consent block under `plan-it/SKILL.md` Step 7 ("Sharing the plan") so the consent question states exactly what a push publishes.

Ask: "Go ahead w branch 2" (scope carried from the deferred findings of the earlier PR that added the block; the item list below is the session's reading of that table, not the engineer's words).

Why now: the block shipped with several Low findings deferred behind a freeze, and the engineer has reopened it.

Findings to resolve:

1. `git remote get-url --push <remote>` prints one URL, but `git push <remote>` publishes to every configured `pushurl`.
2. "The commits that push would publish" has no stated baseline.
3. `push.recurseSubmodules` can widen what a push publishes.
4. The question does not pin the pushed commit (the tip at execution time) or name the remote branch.
5. The offer has silent exits: a PR already exists, `gh browse` fails, the remote is unspecified.
6. "records an approval" in the `announce-approved-plan-path.sh` header reads stronger than the banner's "recorded".
7. Whether to add a test pinning the push-consent wording.

Engineer decisions this session (their words or selected labels):

- Items 1-4, 5 and 7: the label "Ask the architect", with the instruction "one spawn for all three questions".
- Item 6: the label "Yes, fix the comment (Recommended)", which includes the hook-header comment fix in this branch.
- Multi-URL remotes: the label "No, one URL is enough (Recommended)".

Session-verified probes (git 2.43.0, scratch repos):

- With two `pushurl` entries, `git remote get-url --push origin` printed only the first URL. `--push --all` printed both.
- `git push --dry-run --no-follow-tags origin feat` listed both destinations and the ref update for each.
- With `push.followTags=true`, `push.recurseSubmodules=on-demand` and a `remote.origin.push` refspec configured, `git push --no-follow-tags --no-recurse-submodules origin <sha>:refs/heads/feat` updated `refs/heads/feat` to exactly `<sha>` on both destinations. Neither received the annotated tag, the config refspec's ref, or a later commit.

## Approach

The push is offered only when the remote has exactly one push URL. The question names that URL, the branch tip's SHA, the remote branch and the commits since the remote's default branch, all read locally. The push then sends exactly that SHA to that branch, with tags, submodule recursion and configured push options switched off.

The rest of the change does three things. The offer says why it stopped or what failed, a test pins five bullets, and the announce hook's header stops saying that a marker write records an approval.

**Item 1: one push URL, or no offer.**

- The offer is asked only when `git remote get-url --push --all <remote>` prints exactly one URL (row 27). `git push <remote>` publishes to every URL that command prints. When no `pushurl` is set, that includes every `url` entry (G1, rows 4, 32).
- With zero URLs or several, the stop-condition bullet names the URL count and the engineer pushes by hand. A push to two URLs whose second is unreachable left the SHA on the first and exited 128 (row 31). Supporting several URLs would need per-destination reporting and host-qualified `gh` calls, and the engineer's answer makes both unnecessary.

**Item 4: the tip and the remote branch.**

- Before asking, the session resolves the tip once with `git rev-parse --verify refs/heads/<branch>`. A tag named like the branch shadows the bare name (row 30).
- The question names that `<sha>` and `refs/heads/<branch>`. The push is `git -c push.pushOption= push --no-follow-tags --no-recurse-submodules <remote> <sha>:refs/heads/<branch>`.
- `<sha>` is the literal hex the question named. A command substitution would resolve the tip again when the push runs.

**Item 3, and configuration that widens a push.**

- `--no-recurse-submodules` overrides `push.recurseSubmodules`. `submodule.recurse` reaches a push only through that variable (rows 8, 34).
- `-c push.pushOption=` clears configured push options for this one command. Without it, a configured option reached the remote (row 28). This is not one of the seven findings. A push option is server-side input the question never names, though, and the goal line requires the question to state exactly what a push publishes.
- `remote.<remote>.mirror=true` makes the pinned push fail (row 29), so the push-failure bullet covers it.
- `push.autoSetupRemote` applies only to a push with no refspec, so the stall hook's `@{u}` read is unchanged (rows 14, 35).

**Item 2: a stated baseline.**

- `<default-branch>` is the branch `git symbolic-ref refs/remotes/<remote>/HEAD` points to. The default-branch stop and the baseline the question names use it. The commit range itself uses `refs/remotes/<remote>/HEAD`.
- On a remote added with `git remote add`, that command fails (row 33). The default branch then cannot be determined, and that alone does not stop the offer. The default-branch stop is a backstop, because Step 1 already puts the plan on a feature branch (row 37).
- The list is `git log --oneline refs/remotes/<remote>/HEAD..<sha>`. The range is fully qualified, so a local branch named like the tracking ref cannot shadow it (the lookup order row 30 shows). Git follows the symbolic ref itself, so the branch name the remote chose never reaches a shell command (row 40).
- The question says the baseline is as of this clone's last fetch (G2). It gives the exact commit count and at most 10 subjects, then "N more". The 10 is a readability cap, not a sourced number.
- The question says the list is unknown and may be the branch's whole history in two cases: the default branch cannot be determined, or the range does not resolve. This follows the existing "visibility unknown, treat as public" rule. A lagging tracking ref errs toward naming too many commits (row 11).

**Which remote.** `<remote>` follows git's own push-remote order, falling back to `origin` (row 12).

**Item 5: say it instead of exiting silently.**

- One bullet: when the push URL count, a push URL path that is not exactly two segments, the default branch, an existing PR or a failed tip resolution stops the offer, the reply names which one. The "ask only when" rules themselves keep their meaning.
- Two bullets after the URL step:
  - If the push fails or a hook denies it, report the error or denial, give no URL, and make no further push attempt.
  - If `gh browse` fails, say the push landed on `refs/heads/<branch>` at `<remote>` and report gh's error in place of the URL.
- With one destination, the partial publish across destinations in row 31 cannot happen.

**Item 7: a narrow test.**

- Five Step 7 bullets are pinned with the existing right-bounded clause helper, in one dict:
  - the push command;
  - "No answer means no push.";
  - "Autonomous shipping does not waive the question.";
  - the exactly-one-push-URL bullet;
  - the `refs/heads/<branch>` tip bullet.
- These are the bullets whose loss would push without an answer, push when the remote has more than one destination, or push a commit other than the one named.
- They are wording tripwires. `/skill-review` stays the meaning-level backstop.
- The earlier Out of scope does not bind this choice (row 20).

**Item 6.** The hook header's lines 3-4 and its line-48 Known-gaps bullet are reworded so they claim only that a marker was recorded. Lines 37, 39 and 59 need no change (row 21).

**Changelog.** `CHANGELOG.md:44` says the offer is asked on any non-default branch with no PR, and that the question names the push URL, the commits and the visibility. After this change, the offer also requires exactly one push URL, and the question names the SHA, the remote branch and the baseline. Following the precedent at `CHANGELOG.md:11`, one new `[Unreleased]` entry supersedes it rather than editing it in place.

**Alternatives set aside:**

- **Supporting several push URLs.** That means naming every URL, running `gh` per repository, reporting each destination after a partial push, and host-qualifying `--repo`. The engineer does not need the offer there (row 27), and a partial push is real (row 31).
- **Dry-run output in the question, alone or alongside the local reads.** See the over-powered-primitive check below.
- **`git ls-remote --symref <remote> HEAD` or `git remote set-head <remote> --auto` for the default branch.** Each contacts the remote before consent (inferred, as in row 6). The visibility lookup contacts only `github.com` before consent (row 43), so these are set aside because the local symbolic-ref read gives the same answer without any request.
- **Guessing `main` or `master` when `symbolic-ref` fails**, as `_lib_default_branch_or_guess` does for the push gate (`require-ready-for-review.sh:365-372`). A guess can name an unrelated ref and undercount. The "unknown" wording fails safe.
- **`--push-option=<x>`, or editing `push.pushOption` in config.** `--push-option` sends an option of its own. A config edit persists into the engineer's other pushes. `-c` is the command-scope override.
- **`<sha> --not --remotes=<remote>` as the baseline.** It is tighter, but it can name too few commits (row 11).
- **Always `origin` as the remote.** In a fork workflow it pushes to the wrong repository unless the engineer types a different answer.
- **One stated line per silent exit.** One umbrella bullet says the same thing in one line.
- **No test, or a full-block pin.** See the M12 check below.

**Assumption ledger**

**Root:** `plan-it/SKILL.md` Step 7's push question can name less than the push publishes:

- one push URL, when the push goes to every push URL;
- a commit list with no baseline;
- no guard against submodule recursion or configured push options;
- a tip resolved only when the push runs;
- no named remote branch.

The skill never says which remote to use or where the default branch comes from. The offer also ends silently when it is skipped or when `gh browse` fails. Separately, `announce-approved-plan-path.sh`'s header says a marker write "records an approval", which claims more than the banner's "recorded".

**Givens** (conditions beyond this design's reach):

- **G1.** `git push <remote>` publishes to every push URL configured on that remote, or to every `url` when no push URL is set. Git defines this behavior (`man git-push`, "REMOTES", "Named remote in configuration file": "Pushing to a remote affects all defined pushurls or all defined urls if no pushurls are defined"; rows 4, 32).
- **G2.** Remote-tracking refs change only when this clone fetches or pushes, so any local commit baseline can lag the remote. Git's ref model imposes this.
- **G3.** The host decides whether a repository is public, and whether `gh` can reach it at all. The skill can only ask, and fall back to "unknown, treated as public".

**Mechanisms** (each anchored):

- **M1 — The offer is asked only when `git remote get-url --push --all <remote>` prints exactly one URL.** Otherwise the stop-condition bullet names the URL count. `anchors: row27`
- **M2 — `<remote>` follows git's push-remote order: `branch.<branch>.pushRemote`, `remote.pushDefault`, `branch.<branch>.remote`, then `origin`.** `anchors: row12`
- **M3 — `<default-branch>` comes only from `git symbolic-ref refs/remotes/<remote>/HEAD`, for both the default-branch stop and the baseline the question names. The range itself uses `refs/remotes/<remote>/HEAD`.** When that command fails, the default branch cannot be determined. `anchors: row33`
- **M4 — `OWNER/REPO` comes only from a push-URL path of exactly two segments after removing a trailing `.git`, so the value cannot carry a host. `gh pr view` and `gh browse` use `--repo OWNER/REPO` on gh's default host. Visibility is checked with `gh repo view github.com/OWNER/REPO --json visibility` only when that URL's host is exactly `github.com`; any other host is reported as unknown and treated as public with no request.** `anchors: row43`
- **M5 — The tip is resolved once, before asking, with `git rev-parse --verify refs/heads/<branch>`, and named in the question with `refs/heads/<branch>`.** `anchors: row30`
- **M6 — The push is `<sha>:refs/heads/<branch>` with `--no-follow-tags`, and `<sha>` is the literal hex the question named.** `anchors: row9`
- **M7 — `-c push.pushOption=` precedes `push`.** `anchors: row28`
- **M8 — `--no-recurse-submodules` joins `--no-follow-tags`.** `anchors: row8`
- **M9 — The commit list uses the fully qualified range `refs/remotes/<remote>/HEAD..<sha>`.** The question states the baseline as of the last fetch, gives the exact count and caps subjects at 10 with "N more". When the default branch cannot be determined or the range does not resolve, it says the list is unknown. `anchors: row11`
- **M10 — One bullet: when the push URL count, a push URL path that is not exactly two segments, the default branch, an existing PR or a failed tip resolution stops the offer, the reply names which one.** `anchors: root`
- **M11 — Two bullets for a failed push and a failed `gh browse`.** `anchors: root`
- **M12 — A new `test_skills.py` class pins five Step 7 bullets with `_assert_pinned_clause_right_bounded`.** `anchors: row19`
- **M13 — The hook header's lines 3-4 and 48 are reworded so they claim only that a marker was recorded.** `anchors: row21`
- **M14 — One superseding `CHANGELOG.md` `[Unreleased]` entry.** `anchors: row22`

**Over-powered-primitive check (items 1-4).** The heavier candidate is `git push --dry-run` output in the question, alone or alongside the local reads. It contacts the destination with the engineer's credentials before any consent is given (inferred, row 6). Dry-run is set aside because local reads give the same facts and it adds a credentialed contact to the push destination. Four lighter primitives replace it:

1. **`get-url --push --all`.** A local config read that lists every destination the push reaches (rows 4, 32). Here it confirms there is exactly one. `anchors: row4`
2. **A pinned SHA from `refs/heads/<branch>` in an explicit refspec.** A local `rev-parse`. It makes the push itself unable to publish a later tip or another ref. Dry-run cannot do this, because the real push resolves `<branch>` again when it runs unless it is pinned anyway. `anchors: row9`
3. **`--no-recurse-submodules`.** It removes recursion. Dry-run would only display it (rows 7, 8). `anchors: row8`
4. **A local `git log` range.** For a branch the remote lacks, dry-run reports a ref update, not a commit list (row 6). Item 2 therefore needs a local range under either design. `anchors: row10`

Together these cover items 1-4. Dry-run alone settles only item 1. It would add nothing but a network contact before consent, and "both" would add that contact on top.

**Over-powered-primitive check (M12).** A test is heavier than review alone. Two lighter options:

- **(a) The hook-enforced `/skill-review` alone.** It fails because it is a reading judgment over a diff. A reword that drops one flag, or `--all`, or `refs/heads/`, can read as equivalent prose.
- **(b) Pin only the push command.** It fails because it leaves unpinned the bullets that decide whether any push happens, which destination it reaches, and which commit `<sha>` names. `anchors: row19`

**Assumption rows:**

1. `[engineer-verified: "Ask the architect"]` This design settles items 1-4, 5 and 7. The engineer's accompanying instruction was "one spawn for all three questions".
2. `[engineer-verified: "Yes, fix the comment (Recommended)"]` The hook-header comment fix (item 6) is in this branch. That option's description was the session's proposal.
3. `[unverified]` The option descriptions offered for items 1-4, 5 and 7 were the session's proposals, not the engineer's decisions:
   - dry-run output, `get-url --push --all`, or both;
   - a stated line per silent exit, or silence;
   - no test, or a narrow test.

   This design treats them as candidates only.
4. `[verified: session probe in Context, git 2.43.0]` On a remote with two `pushurl` entries:
   - `git remote get-url --push origin` printed only the first.
   - `--push --all` printed both.
   - A real push updated the branch on both.
5. `[verified: man git-remote, "get-url"]` `git remote get-url` expands `insteadOf` and `pushInsteadOf`, and lists only the first URL by default. `[verified: man git-config, "url.<base>.pushInsteadOf"]` An explicit `pushurl` makes git ignore `pushInsteadOf` for that remote. `[verified: session probe]` With `pushInsteadOf` set and two `pushurl` entries, `--push --all` printed the unrewritten URLs.
6. `[verified: session probe in Context]` `git push --dry-run --no-follow-tags origin feat` listed both destinations and the ref update for each. `[unverified]` To do that it contacts each destination, and for a branch the remote lacks it reports a new branch without listing commits. Inferred: a per-destination ref update needs that destination's refs.
7. `[verified: /usr/share/doc/git/RelNotes/2.7.0.txt:86-87, 2.39.0.txt:57-58, 2.11.1.txt:27-28]` The `push.recurseSubmodules` config makes every push recurse into submodules, and `on-demand` pushes them recursively. A dry-run propagates into submodules too, so dry-run output can show recursion but cannot prevent it.
8. `[verified: man git-push, "--no-recurse-submodules"]` A value of `no`, or `--no-recurse-submodules`, overrides the `push.recurseSubmodules` configuration variable.
9. `[verified: session probe in Context]` With `remote.origin.push` and `push.followTags` configured, `git push --no-follow-tags --no-recurse-submodules origin <sha>:refs/heads/feat` set `refs/heads/feat` to `<sha>` at both push URLs. Neither received the tag, the config refspec's ref, or a later commit.
10. `[verified: session probe]` `git log --oneline origin/main..<sha>` listed exactly the one commit reachable from `<sha>` and not from the tracking ref.
11. `[unverified]` When the tracking ref lags, or the remote branch already exists from an earlier push, the list names more commits than the push adds, never fewer. It names too few only in two cases: the remote's default branch was rewound behind the local ref, or the one push URL names a different repository than the fetch side. This is inferred from row 10 and G2. Two alternatives name too few more often:
    - `<sha> --not --remotes=<remote>`: a surviving tracking ref for a branch deleted on the remote hides commits the push publishes again.
    - `<remote>/<branch>..<sha>`: it does not resolve before the first push.
12. `[verified: man git-config, "branch.<name>.pushRemote" and "remote.pushDefault"]` For the current branch, `git push` picks its remote in this order: `branch.<name>.pushRemote`, `remote.pushDefault`, `branch.<name>.remote`, then `origin`.
13. `[verified: claude/.claude/hooks/_lib.sh:1689-1715; claude/.claude/hooks/require-ready-for-review.sh:254-326; existing cases at claude/.claude/hooks/tests/test_lib.py:3506 and claude/.claude/hooks/tests/test_require_ready_for_review.py:233]` The push gate still sees the pinned command as a publishing `git push`:
    - `_lib_git_argv_from_subcmd` skips `-c` and its value word, so `-c push.pushOption=` leaves `push` as the subcommand. The existing `git -c push.default=simple push origin feature` case shows the same shape.
    - `<sha>:refs/heads/<branch>` has no whitespace before its colon, so it never matches the delete-shape regex at :270.
    - `--no-follow-tags` does not match the `--tags` arm at :286, which needs `--tags` at a word start.

    This is verified by reading. No hook test runs this exact command.
14. `[verified: claude/.claude/hooks/advance-past-commit-stall.sh:188-212]` The stall hook reads `@{u}`. Neither the current push nor the pinned push passes `-u`, so its behavior is unchanged. The current form `git push --no-follow-tags <remote> <branch>` and the pinned form both name a refspec.
15. `[verified: claude-skills/skills/plan-it/SKILL.md:141-153]` The current block is a lead paragraph plus 11 bullets. The `:145` conditions carry over narrowed: the default-branch source changes and `gh pr view` now names the branch. The `:144` `gh` resolution carries over narrowed too: visibility is now a `gh repo view github.com/OWNER/REPO` made only for `github.com` push URLs. The `:148` unknown-visibility rule carries over narrowed as well: it now also fires for every non-`github.com` host without a request. Five other meanings carry over unchanged:
    - the `AskUserQuestion` channel (:143);
    - the `gh browse` URL step (:153);
    - userinfo stripping (:146);
    - no answer means no push (:149);
    - autonomous shipping does not waive the question (:152).
16. `[verified: .claude/plans/plan-share-links.md rows 20-22 and Approach item 3]` The consent channel is `AskUserQuestion`, and the push runs only on a yes. That plan records the engineer's "Pushing on my yes is very convenient. I prefer that." This plan keeps both.
17. `[verified: PR #1193 body, "Alternatives considered" and "Deferred review findings"]` The dry-run follow-up was "recalled from memory, not verified". Items 1-5 and 7 are the deferred rows there.
18. `[verified: docs/skills.md:146; claude/.claude/hooks/check-skill-length.sh:11, 118; wc -l]` The skill cap is 200 lines. `plan-it/SKILL.md` is 159 lines today. Replacing 11 bullets with 20 takes it to 168.
19. `[verified: claude-skills/skills/tests/test_skills.py:5290-5327, 5375-5396, 5429-5461; grep for no-follow-tags and get-url --push across the repo]` `_raw_heading_section_text` (:5290), `_assert_pinned_clause_right_bounded` (:5302) and `_PLAN_IT_STEP7_HEADING` (:5429) already exist. The model test `TestHandoffWarrantCheckCanonicalSection.test_pinned_branch_clause_matches_live_text` is at :5382-5396. Step 7's heading and fallback clause are pinned. No test reads the push bullets.
20. `[verified: .claude/plans/plan-share-links.md:350]` The earlier Out of scope declined a different test: a tripwire asserting that no plan-time PR instruction exists. It recorded that as the session's call ("Add one only if the engineer asks for it"), with no engineer quote. The engineer has now handed item 7 to this design (row 1), so that line does not decide it.
21. `[verified: claude/.claude/hooks/announce-approved-plan-path.sh:3-4, 22, 26, 37, 39, 48, 59; grep]` What the header says now:
    - Lines 3-4 say "shows the approved plan's absolute path … records an approval".
    - The banner (line 22) says "plan-review marker recorded for:".
    - Line 26 says the messages "claim only that a marker was recorded".
    - Line 48 says a hand-run write "announces as an approval".
    - Lines 37 and 39 describe a review approval that the hook fails to announce.
    - Line 59 says a "recorded for" line can appear with "no approval behind it".

    Only lines 3-4 and 48 credit the hook's own message with an approval. No test pins any of these lines.
22. `[verified: CHANGELOG.md:11, :44; claude-skills/skills/code-review/SKILL.md:77]` The `[Unreleased]` push-offer sub-bullet says the question "names the push URL, the commits the push would publish, and whether the repository is public". The repo supersedes an earlier claim with a new entry (:11) rather than editing it. `/code-review` flags changelog edits as preserved-record edits.
23. `[verified: grep of README.md, docs/*.md, .claude/rules/*.md]` No rule requires a changelog entry for every change. M14 exists only to keep :44 from going stale.
24. `[verified: docs/design-decisions/plans-shared-by-path-not-early-pr.md:5]` The decision record describes the push only as "pushes the branch with no PR, only on the engineer's yes, and shares the plan file's URL". This plan does not change it.
25. `[engineer-verified: "Keep the entry (Recommended)"]` `CHANGELOG.md` stays in the diff, answering the plan-review scope question about the superseding entry (row 22). The option description was the session's proposal.
26. `[engineer-verified: "Yes, I said it"]` The Ask line's "Go ahead w branch 2" is the engineer's own wording.
27. `[engineer-verified: "No, one URL is enough (Recommended)"]` The question was "Do you run /plan-it in any repo whose push remote has more than one push URL, and want the plan-time push offered there?". The engineer does not need the plan-time push offered on a push remote with more than one push URL. The option description was the session's proposal.
28. `[verified: session probes, git 2.43.0, scratch repos; one-off scripts /tmp/pushpin-probe2.sh and /tmp/pushpin-probe3.sh]` With `push.pushOption=mr.create` in config, the plain pinned push delivered the option to the remote: its hook saw 1 option. `git -c push.pushOption= push ...` delivered 0. `[verified: man git-push, "--push-option"]` "When no --push-option=<option> is given from the command line, the values of configuration variable push.pushOption are used instead."
29. `[verified: session probes as in row 28]` With `remote.origin.mirror=true`, a push with an explicit refspec fails with "--mirror can't be combined with refspecs".
30. `[verified: session probes as in row 28]` With a tag and a branch named alike, `git rev-parse --verify <name>` returned the tag's commit and warned "refname is ambiguous". `git rev-parse --verify refs/heads/<name>` returned the branch tip.
31. `[verified: session probes as in row 28]` With two push URLs and the second unreachable, the SHA landed on the first and git exited 128.
32. `[verified: session probes as in row 28]` With two `remote.origin.url` entries and no `pushurl`, `git remote get-url --push --all origin` printed both, and a push reached both.
33. `[verified: session probes as in row 28, git 2.43.0]` On a remote added by `git remote add`, `git symbolic-ref refs/remotes/origin/HEAD` fails with "is not a symbolic ref".
34. `[verified: man git-push, "push.recurseSubmodules"]` If `push.recurseSubmodules` is not set, "no" is used, unless `submodule.recurse` is set, in which case true means on-demand. `submodule.recurse` therefore reaches a push only through `push.recurseSubmodules`, which `--no-recurse-submodules` overrides (row 8).
35. `[verified: man git-push, "push.autoSetupRemote"]` The setting assumes `--set-upstream` "on default push when no upstream tracking exists for the current branch". `[unverified]` A push that names a refspec on the command line is not a default push, so the setting does not apply to the pinned command. Inferred from `man git-push`'s description of the default refspec, which applies only when the command line names none.
36. `[verified: claude/.claude/hooks/require-ready-for-review.sh:387-392]` The push gate runs `gh pr view` with no `--repo`, and gates the push only when that finds a PR. `[unverified]` In a fork workflow the PR lives in the base repository. `gh pr view --repo <fork>` may miss it while the gate's default resolution finds it. The offer can then be asked and the gate denies the push after a yes. The gate's `gh pr view` fails open on error or timeout (`require-ready-for-review.sh:376-392`) and infers the repository from the checkout's remotes, so this backstop holds only when that call succeeds. This plan leaves the lookup unchanged.
37. `[verified: claude-skills/skills/plan-it/SKILL.md:23, :141]` Step 1 moves a session that starts on the default branch onto a new branch. Step 7's sharing block applies only on the branch where the plan was committed. So the default-branch stop is a backstop. When the default branch cannot be determined, the question's `refs/heads/<branch>` stays the engineer's check.
38. `[engineer-verified: "Ask the architect"]` The plan-review round-2 question was whether to keep `-c push.pushOption=` in the pinned command, which is not one of the seven findings. The engineer's answer delegates the call. The architect's consult recommended keeping it, because it costs less than narrowing the "covers only" wording and a configured push option can make a host open a merge request. The architect's reasoning is the session's relay, not the engineer's words.
39. `[verified: installed gh 2.100.0 man pages gh-pr-view, gh-browse]` `gh pr view [<number> | <url> | <branch>]`, `-R, --repo [HOST/]OWNER/REPO`, `gh browse --branch` and `--no-browser` exist as the block uses them. The man pages do not say which host a value without a host goes to.
40. `[verified: session probe, git 2.43.0, scratch repos; one-off script /tmp/pushpin-probe4.sh]` With `refs/remotes/origin/HEAD` set to `refs/remotes/origin/main`, `git rev-list --count refs/remotes/origin/HEAD..<sha>` counted the one commit and `git log --oneline -n 10` over the same range listed it. With that ref absent (a remote added by `git remote add`), the same command exits 128 ("unknown revision"), which the "range does not resolve" bullet covers. The probe's third section tried a default branch named with shell metacharacters, but git rejected the name (it contained a space), so it did not exercise the case. The design does not depend on it, because the range never puts the remote-chosen name in a shell command.
41. `[verified: claude/.claude/scripts/transcript_analysis/gh_cli.py:298-305 docstring, an in-repo secondary source because the gh man pages are silent; session probe]` A bare `OWNER/REPO` given to `gh --repo` resolves against `GH_HOST`, or the default host when unset, whatever the checkout's remote. A same-named repository on that host therefore answers with no error. `gh repo view github.com/cli/cli --json visibility,url` returned `{"url":"https://github.com/cli/cli","visibility":"PUBLIC"}`, so `gh repo view` accepts a host-qualified positional argument. A host alias never reaches `gh repo view` now, because row 43 limits the lookup to `github.com`. `[verified: man gh-repo-view]` It has no `--repo` flag.
42. `[engineer-verified: "Ask the architect"]` The code-review round-1 questions (whether to apply the three CISO findings that change SKILL.md text, and whether to keep the `[HOST/]` deferral) were delegated with that label. The architect's consult chose: quoting rule widened inside the pinned push bullet, range via `refs/remotes/<remote>/HEAD`, a one-clause unknown-default-branch consequence, "make no other push attempt" after a denial, and a host-qualified visibility check with `--repo OWNER/REPO` kept for `gh pr view` and `gh browse`. Row 43 amends the "no other push attempt" wording and the host-qualified visibility check. The consult's reasoning is the session's relay, not the engineer's words.
43. `[engineer-verified: "Your suggestion sounds reasonable but check with the architect"]` The code-review round-2 closeout was delegated with those words, and the architect's consult chose a variation of the session's proposal: address the credential concern by looking up visibility only for `github.com` push URLs, defer pinning the range and visibility clauses, and fold in "no further push attempt", verbatim visibility and the docstring wording. The consult's reasoning is the session's relay. `[verified: strings in the installed gh binary's help text, read by the architect consult and again by the round-4 security review]` `GH_TOKEN` is used when a command targets github.com or a subdomain of ghe.com, and `GH_ENTERPRISE_TOKEN` is used when a command targets a GitHub Enterprise Server host, so a host-qualified lookup to any other host can carry the enterprise token. `[unverified]` Whether `gh` sends it to a host absent from its stored hosts, which is why the lookup is limited to `github.com` instead of filtered by stored hosts.

## Critical files

All paths are relative to the worktree root. There is **one `code-writer` dispatch** for all four edits. The test must match the final SKILL.md wording, and that one shared context would have to be repeated in any split.

**Modify `claude-skills/skills/plan-it/SKILL.md`.** Leave the lead paragraph at line 141 unchanged. Replace the 11 bullets at lines 143-153 with these 20, one unwrapped line each. That takes the file from 159 to 168 lines.

```markdown
- Ask the engineer through `AskUserQuestion`, not a closing chat question.
- `<remote>` is the first of `branch.<branch>.pushRemote`, `remote.pushDefault`, and `branch.<branch>.remote` that is set, else `origin`.
- `<default-branch>` is the text after `refs/remotes/<remote>/` in what `git symbolic-ref refs/remotes/<remote>/HEAD` prints. When that command fails, `<default-branch>` cannot be determined, and that alone does not stop the offer.
- Ask only when `git remote get-url --push --all <remote>` prints exactly one URL, since `git push <remote>` publishes to every URL it prints.
- Take `OWNER/REPO` only from a push-URL path of exactly two segments after removing a trailing `.git`. Resolve `gh pr view` and `gh browse` with `--repo OWNER/REPO`, which `gh` resolves on its default host. Check visibility only when that URL's host, without scheme, userinfo, or port, is exactly `github.com`, with `gh repo view github.com/OWNER/REPO --json visibility`, so no host named by the push URL other than `github.com` receives a `gh` request before the engineer answers.
- Ask only when the branch is not `<default-branch>` and `gh pr view <branch>` finds no PR for it (a non-zero exit counts as no PR), since a push to a branch with a PR needs `/ready-for-review` first.
- When the push URL count, a push URL path that is not exactly two segments, the default branch, an existing PR, or a failed tip resolution stops the offer, say which one in the reply instead of skipping the offer silently.
- The question names that URL with any userinfo (`user:token@`) stripped.
- Before asking, resolve the branch tip once with `git rev-parse --verify refs/heads/<branch>`, and name that `<sha>` and the remote branch `refs/heads/<branch>` in the question.
- The question names the baseline `refs/remotes/<remote>/<default-branch>` as of this clone's last fetch, gives the commit count from `git rev-list --count refs/remotes/<remote>/HEAD..<sha>`, and lists at most 10 subjects from `git log --oneline -n 10` over the same range, followed by "N more" when there are more. The range uses the symbolic ref `refs/remotes/<remote>/HEAD` so that `<default-branch>`, which the remote chose, never reaches the shell.
- When `<default-branch>` cannot be determined or that range does not resolve, the question says the list is unknown and may be the branch's whole history. When `<default-branch>` cannot be determined, it also says the default-branch check did not run, so `refs/heads/<branch>` may be the remote's default branch.
- The question gives the visibility `gh repo view` returns, verbatim.
- State the visibility as unknown, and treat it as public, when that URL's host is not `github.com`, or `gh repo view` exits non-zero or returns no visibility.
- No answer means no push.
- Push only after a yes, with exactly `git -c push.pushOption= push --no-follow-tags --no-recurse-submodules <remote> <sha>:refs/heads/<branch>`, where `<sha>` is the literal hex named in the question, never a command substitution. Escape every substituted value so the shell sees one literal word, in this command and in every other command in this list.
- A yes covers only that remote, that remote branch, and that SHA.
- Autonomous shipping does not waive the question.
- After a yes and the push, give the plan file's URL from `gh browse <repo-relative plan path> --branch <branch> --no-browser`, and give other readers that URL, not the local path.
- If the push fails or a hook denies it, report the error or denial, give no URL, and make no further push attempt.
- If `gh browse` fails, say the push landed on `refs/heads/<branch>` at `<remote>` and report gh's error in place of the URL.
```

Leave Step 7's heading and its cannot-resolve fallback clause untouched. Both are pinned (row 19).

**Modify `claude-skills/skills/tests/test_skills.py`.** Add the following directly after `TestPlanItStep7FallbackToJudgment` (about line 5461):

- A module-level dict `_PINNED_PLAN_IT_PUSH_CONSENT_CLAUSES` with five entries. Each holds the exact text of one bullet in the list above, without the `- ` marker:
  - `"exactly_one_push_url"`: the "Ask only when `git remote get-url --push --all <remote>` prints exactly one URL" bullet;
  - `"tip_pinned_to_refs_heads_sha"`: the "Before asking, resolve the branch tip once" bullet;
  - `"no_answer_no_push"`;
  - `"pinned_push_command"`: the "Push only after a yes" bullet;
  - `"autonomous_shipping_no_waiver"`.
- A comment above the dict, wrapped to two lines for the repo's line length: `# plan-it Step 7 push bullets whose loss would push without an answer, push when the remote has more than one destination, or push a commit other than the one named.`
- A class `TestPlanItStep7PushConsent` whose docstring names the guarded failure (a reword of plan-it Step 7 that drops a consent rule, the one-push-URL condition, the `refs/heads/` tip resolution, a flag of the pinned push command, or its escaping rule), says the pins check wording and the bullet's right edge and not runtime behavior, and says to update the constant on failure only if the new wording preserves the guarantee.
- One test in that class, parametrized over the dict's sorted keys and modeled on `TestHandoffWarrantCheckCanonicalSection.test_pinned_branch_clause_matches_live_text`:
  - `_raw_heading_section_text(_skill_file("plan-it"), _PLAN_IT_STEP7_HEADING)`;
  - whitespace-collapse the pinned text;
  - `_assert_pinned_clause_right_bounded` with a context string naming the key.
- *Reuse:* `_skill_file`, `_PLAN_IT_STEP7_HEADING`, `_raw_heading_section_text`, `_assert_pinned_clause_right_bounded`. Add no new helper, no left-edge check, and no hook test.

**Modify `claude/.claude/hooks/announce-approved-plan-path.sh`.** This is a comment-only change with no net line change. Keep line 2 (`# hook-class: informational`) as it is. Leave lines 37, 39 and 59 unchanged (row 21).

- Lines 3-4 become:
  ```bash
  # PostToolUse Bash hook: after `marker.sh write plan-review` records a marker,
  # shows the absolute path of each plan it covers in the engineer's terminal.
  ```
- Line 48 becomes:
  ```bash
  #   - A hand-run `write plan-review` shows the same line a review's approval does.
  ```

**Modify `CHANGELOG.md`.** Add one entry as the first bullet under `## [Unreleased]` → `### Changed` (row 22). Do not edit line 44.

```markdown
- **`/plan-it`'s push offer now pins what the push publishes, and says when it skips the offer or a step fails.** **Supersedes** the push-offer sub-bullet of the "`/plan-review` approval now shows the approved plan's absolute path" entry below.
  - The offer is made only when the remote has exactly one push URL. Otherwise the session says which condition stopped the offer.
  - The question names the push URL, the branch tip's SHA, the remote branch, and the repository's visibility. Visibility is looked up only for `github.com` push URLs; any other host is reported as unknown and treated as public, with no request made.
  - It gives the commit count and up to 10 subjects since the remote's default branch as of this clone's last fetch. When that baseline is unknown, the question says the list may be the branch's whole history.
  - The push is `git -c push.pushOption= push --no-follow-tags --no-recurse-submodules <remote> <sha>:refs/heads/<branch>`, so it sends that SHA to that branch with no tags, no submodule recursion, and no `push.pushOption` values.
  - The remote is the one `git push` would choose for the branch, falling back to `origin`.
  - When being on the default branch, the push URL count, a push URL path that is not `OWNER/REPO`, an existing PR, or a failed tip resolution stops the offer, the session says so in its reply. A failed or denied push is reported with no URL and no further push attempt, and a failed `gh browse` is reported.
```

**Also in the diff:** `.claude/plans/plan-it-push-consent-precision.md` (this plan).

**Dispatch verification command** (from the worktree root; a linked worktree has no `.venv`, so use the main checkout's): `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`

## Verification

**0. Rows 4, 5, 8, 9, 10, 12, 28-34, 40 and 41 are settled.** Before `/plan-review`, the session ran scratch-repo probes (git 2.43.0) and read the `git-config`, `git-push` and `git-remote` man pages. Each of those rows carries its source above. The probe scripts `/tmp/pushpin-probe2.sh`, `/tmp/pushpin-probe3.sh` and `/tmp/pushpin-probe4.sh` were one-offs. They have already run and are not repo deliverables, so the committed plan cites results the scripts can no longer reproduce once `/tmp` is cleaned. Row 35's inference stays flagged `[unverified]`.

**1. Test first.** Add the test before editing SKILL.md, then run `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k PushConsent`. Expect `exactly_one_push_url`, `pinned_push_command` and `tip_pinned_to_refs_heads_sha` to fail, and the other two to pass.

**2. Tests.** After all four edits, from the worktree root:

```bash
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude-skills/
../../../.venv/bin/shellcheck claude/.claude/hooks/announce-approved-plan-path.sh
```

These must go green by name:

- `test_skills.py`: all five parameters of `TestPlanItStep7PushConsent`, the existing `TestPlanItStep7FallbackToJudgment`, and the citation tests;
- `test_hook_alignment.py`: the hook header.

If `select-tests.py` leaves one out, report it as a rule-table bug in `select-tests.py`; don't widen the run by hand.

**3. Each pin bites.** Stage or commit the SKILL.md edit first. Then apply each mutation below to `claude-skills/skills/plan-it/SKILL.md` on its own. Run `../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k PushConsent`, expect only the named key to fail, and restore the line before the next one:

- `exactly_one_push_url`: change "exactly one URL" to "at least one URL".
- `tip_pinned_to_refs_heads_sha`: change `git rev-parse --verify refs/heads/<branch>` to `git rev-parse --verify <branch>`.
- `pinned_push_command`: delete `-c push.pushOption= ` from the push command.
- `no_answer_no_push`: delete the bullet.
- `autonomous_shipping_no_waiver`: append " Unless the engineer opted in." to the same line. This exercises the right-bound arm.

After the last restore, re-run `-k PushConsent` and expect all five green. Then `git diff --exit-code -- claude-skills/skills/plan-it/SKILL.md` must exit 0.

**4. Checks no test covers:**

- Before the commit, `git status --short` and `git diff --cached --stat` list exactly the Critical files. After the commit, `git diff origin/main...HEAD --stat` does.
- `wc -l claude-skills/skills/plan-it/SKILL.md` prints 168.
- `grep -n 'get-url --push <remote>' claude-skills/skills/plan-it/SKILL.md` prints nothing, so the single-URL form is gone.
- `grep -n -e 'records an approval' -e 'announces as an approval' claude/.claude/hooks/announce-approved-plan-path.sh` prints nothing.

**5. Reviews.** Run `/code-review`. It requires `/skill-review` for `plan-it/SKILL.md`, and that requirement is hook-enforced.

## Out of scope

- **Showing `git push --dry-run` output in the question.** The over-powered-primitive check set it aside: it adds a network contact before consent (inferred, row 6), and the local reads make it redundant.
- **Supporting a push remote with more than one push URL.** That covers naming every URL, `gh` calls per repository, and reporting each destination after a partial push. The engineer does not need it (row 27).
- **Host-qualifying `gh pr view` and `gh browse`.** The visibility check is host-qualified and limited to `github.com` (rows 41, 43) because a wrong answer is silent and lands on the risk the engineer consents to, and a request to any other host would carry a `gh` token. Qualifying the other two with the URL's host would fail for an SSH host alias and lose the share URL. A wrong "no PR" is backstopped by the push gate's own `gh pr view` without `--repo` (row 36), which holds only when that call succeeds. A wrong browse URL for a push URL on a non-default host points readers at a same-named repository on gh's default host with no gh error; the SKILL bullet says `gh` resolves those two calls on its default host, so the caveat is written down, and the share URL is accepted as a known limit for non-default-host push URLs.
- **Pinning the remaining consent-scope rules.** "A yes covers only…", the unknown-visibility rule, the unknown-default-branch consequence clause, "make no further push attempt", the `refs/remotes/<remote>/HEAD` range clause and the `github.com`-only visibility lookup are left to the hook-enforced `/skill-review` as the meaning-level backstop. The range clause's injection protection also rests on the pinned quoting rule. The five pins cover the bullets that decide whether, where and what a push sends.
- **Re-reading the push URL just before the push and validating `<remote>` (ciso F5).** The question and the push sit one turn apart in one session. A `<remote>` that is not a configured remote already fails the one-URL condition. Quoting substituted values is adopted: the push command bullet says every substituted value, in that command and in every other command in the list, is escaped so the shell sees one literal word, because a valid branch name can contain shell metacharacters, including a single quote. The remote-chosen default branch name is kept out of the shell entirely by ranging over `refs/remotes/<remote>/HEAD`.
- **Left-edge pins and an automated absence check for the single-URL form (ciso F8).** The five pins are wording tripwires. `/skill-review` is the meaning-level backstop for a prefix qualifier or a contradicting sibling bullet.
- **Stripping userinfo at the read, and treating `remote:` and `gh` error text as data (ciso F9).** The current block already reads the raw URL into tool output. The stripping rule governs what the question shows.
- **Pinning the userinfo-stripping bullet (ciso F8).** The raw URL already reaches the transcript through `get-url`'s tool output, so losing the stripping rule changes what the question shows, not what the push publishes. The five pins keep their stated selection rule: whether the question names the URL at all is left to review, since the pins cover the one-URL condition and the SHA, branch and flags the push sends.
- **Putting destinations before the commit list, and marking commit subjects as data.** With one URL and a 10-subject cap, the destination stays in view. The subjects are this branch's commits, or default-branch history when the tracking ref lags.
- **Probing `submodule.recurse` and `push.autoSetupRemote`.** The man text settles both (rows 34, 35).
- **Locating a fork workflow's PR in the base repository.** The gate's `gh pr view` without `--repo` at `require-ready-for-review.sh:388` is the backstop (row 36).
- **`pre-push` hooks and git-lfs.** The pinned push runs the repository's `pre-push` hook, which can upload objects or check the push. With a SHA source, the hook sees the hex instead of `refs/heads/<branch>` as its local ref. This is an accepted limit, because disabling hooks would also remove checks the engineer may rely on.
- **A hook backstop for the consent itself (ciso F10).** "No answer means no push" stays a single prose layer, as an accepted tradeoff. A later backstop would belong in a gate, not in more SKILL prose.
- **A per-remote push-option config key.** The session's search of the installed man pages found no documentation for one, so this plan neutralizes `push.pushOption` only.
- **A hook test for the exact pinned command shape.** Row 13 is verified by reading plus the existing `-c` cases. The gate matters only when a PR exists, which the offer excludes.
- **A push URL naming a different repository than the fetch side.** The commit list is measured against the fetch side's default branch. Git documents `pushurl` as the same repository reached by another transport (`/usr/share/doc/git/RelNotes/2.3.2.txt:40-42`), and the question names the one URL.
- **A rewound default branch on the remote.** The list can then name too few commits (row 11). That is accepted as rare.
- **A commit permalink (`gh browse --commit`) instead of `--branch`.** A branch URL lets readers see later pushes of the plan.
- **"approved plan" in `docs/hooks.md:187`.** The same sentence already says the hook "claims only that a marker was recorded". The engineer's label covers the hook-header comment.
- **The "approval" tokens at `announce-approved-plan-path.sh` lines 37, 39 and 59.** They were read and describe the review's outcome, not the hook's claim (row 21).
- **Editing `CHANGELOG.md:44` in place.** A new entry supersedes it instead (row 22).
- **Pinning the whole Step 7 block, or the remaining question-content bullets.** Only the five bullets that decide whether a push happens, where it goes and which commit it sends are pinned, so rewording the rest stays cheap.
