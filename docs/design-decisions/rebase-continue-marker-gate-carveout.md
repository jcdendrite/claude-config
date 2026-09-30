# `git rebase --continue` skips the two review-marker gates

*2026-09-30.*

`require-code-review.sh` and `require-skill-review.sh` gate `git commit`, `git merge --continue`, `git cherry-pick --continue`, and `git revert --continue`. They do not gate `git rebase --continue`. The other five commit gates that read `git diff --cached` (`deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, `check-skill-length.sh`, `check-claude-md-length.sh`, `guard-settings-session-keys.sh`) gate all four `--continue` forms, rebase included. This file records why the marker gates differ, the anchor test the novel-content base rests on, and the residuals accepted along the way.

## Why `git rebase --continue` is not gated by the marker gates

Read this if a mid-rebase commit passed `/code-review` without a marker while an equivalent mid-merge commit did not, and you suspect a bug. It is deliberate.

`REBASE_HEAD` is the pre-rebase commit being replayed. Mid-rebase `HEAD` is the new base plus the commits replayed so far. In the ordinary case `REBASE_HEAD` therefore reaches neither trusted anchor `_lib_gate_diff_base` checks (see "The anchor-admissibility test" below), so the gate cannot isolate the conflict-resolution content and would fall back to a full HEAD-relative diff. Gating rebase through the marker gates would demand a full `/code-review`, with reviewer fan-out, at every conflicted step of a rebase. Most of that content already passed `require-code-review.sh` at its own original commit time.

That cost is judged higher than the value of gating a resolution diff no anchor can isolate. The carve-out trades marker-gate coverage for gate cost. It does not trade away coverage outright, because the content scanners, the two length gates, and `guard-settings-session-keys.sh` stay armed on `rebase --continue`.

The exemption criterion is recourse cost, not rebase-ness:

- The marker gates are exempted because their recourse is a review, which is the expensive thing being avoided mid-rebase.
- The five other gates are not exempted because their recourse is mechanical: remove the value and re-stage, shorten the file, unstage the key.
- The two content scanners are deliberately not narrowed. Their threat model is accidental leakage, and an accidental secret introduced during conflict resolution does not route around whichever gate is armed, it walks into it. Exempting them would reopen the always-on-scanner gap for a `--continue` form that is otherwise covered.

`_lib_command_concludes_marker_gated_commit` (narrow) and `_lib_command_concludes_commit` (broad) both delegate to one private shape matcher parameterized on the verb set. The rebase-membership decision is therefore written once. A rebase-specific `false` inside a single predicate would also disarm the scanners and length gates that call it, which is the security regression the scanners' exemption criterion forbids. One special case per gate would let seven call sites drift on rebase handling. The plugin copy of the narrow predicate is held byte-identical to the stowed one by a definition-equality test.

### What the carve-out leaves exposed

- **Resolution content is unreviewed at commit time.** The commits a rebase replays were reviewed at their original commit, but conflict-resolution content authored mid-rebase is not. The five armed gates catch a literal secret, a PII pattern, a private-project reference, an oversized file, or a session-key change. They do not catch an unreviewed logic change to a hook, a `SKILL.md` edit, or a `plugin.json` tamper. `/ready-for-review`'s cumulative-diff `/code-review` pass is a reviewer-agent read, not a mechanical scanner, and a `cumulative-review` marker cache hit skips it, so it does not backstop this uniformly.
- **The exemption is command-shaped, not state-shaped.** Only the string `git rebase --continue` is excluded. A bare `git commit` made mid-rebase still reaches `require-code-review.sh`, and still over-gates HEAD-relative because `REBASE_HEAD` still reaches no anchor. A state-shaped carve-out that suppresses the marker gates whenever `rebase-merge/` or `rebase-apply/` exists was declined: it would also exempt genuinely novel work staged and committed mid-rebase, which is the content the marker gates exist to catch.
- **The exemption covers more than conflicts.** The matcher is a string match with no conflict check behind it. An interactive rebase that marks a commit `edit` pauses with `rebase-merge/` and `REBASE_HEAD` present, conflict or not. Staging an unrelated file at that pause and running `git rebase --continue` folds it into the replayed commit, invisible to both marker gates. This pre-dates the carve-out, since `require-code-review.sh` never had `rebase --continue` awareness. The chained form `git add unrelated.txt && git rebase --continue` is denied by `deny-invisible-commit-content.sh`. The split form, `git add` and `git rebase --continue` as two separate Bash calls, is denied by no gate.
- **Rebase over-gating persists where rebase is still gated.** The consumers still armed on rebase, namely `_lib_active_plan_files`, `marker.sh status`, and a bare mid-rebase `git commit`, keep a HEAD-relative base because the anchor never resolves.

### The incentive gradient

Under the carve-out `git merge --continue` is marker-gated and `git rebase --continue` is not, for the same class of novel content. That gradient points at this repo's prescribed default: `git-feature-branch-sync/SKILL.md` prescribes rebase for personal feature branches. The alignment is coincidental, not a designed property of the carve-out. If that prescribed default ever changes to favor merge, re-run this cost/benefit call, because the alignment that makes the carve-out harmless today would no longer hold.

This explanation lives here and deliberately not in `git-feature-branch-sync/SKILL.md`. The sync skill is read at the point of choosing a sync strategy, where naming the asymmetry would hand an agent a documented route to the cheaper gate. This file is read at the point of confusion, which is the reader it is written for.

## Why `deny-invisible-commit-content.sh` takes the broad predicate

`deny-invisible-commit-content.sh` uses `_lib_command_concludes_commit`, not the marker-gated predicate, for the same recourse-cost reason that leaves the scanners armed on rebase. Its deny is mechanical (issue the mutation as its own Bash call), so no review cost is avoided by exempting rebase.

It is also the gate every other commit gate's empty-diff carve-out depends on (`docs/hooks.md`'s entry for it names them). A gate that reads `git diff --cached` at PreToolUse time treats an empty diff as "nothing to review", and this gate is what stops `git add secret && git <verb> --continue` from making that true while the commit that runs is not empty. Arming a gate on `--continue` without arming this one leaves that carve-out reachable with content it never saw. A future change that arms a new commit gate on a new command shape has to check this gate too.

Its commit-shape detection shares the `--continue` grammar with the gates that depend on it: the verb sets, and the abbreviation spellings of `--continue` that git accepts, live in `_LIB_CONTINUE_VERBS_ALL` and the fragment-level shape predicate that `_lib_command_concludes_commit_shape` also calls. That trades each consumer's independent detector for one shared definition. A future gap in the shared grammar, such as a missed verb or an abbreviation a later git version introduces, blinds every consumer gate at once, this gate included, instead of leaving one independent detector standing. A change to that shared matcher warrants correspondingly higher scrutiny than a single-hook regex edit.

The worktree-target check (`-a`, a `--` separator, or a bare pathspec) stays keyed on the literal `commit` token, because its awk grammar never leaves its initial state without reading that word. If a `--continue` form ever accepted `-a`, a `--` separator, or a pathspec, this check would under-deny, and closing it would take a second awk grammar rather than a second call site.

## The anchor-admissibility test

`_lib_gate_diff_base` excludes a parent's content from a marker's hash only when the OID the in-progress state names is an ancestor of `origin/<default>` or of `HEAD`. The recipe, the fallbacks, and the OID and output validation are in [`merge-tree-base-recipe-for-gate-diff-base.md`](merge-tree-base-recipe-for-gate-diff-base.md). This section states the test that selects the anchors, so a later proposal for a third anchor meets the test rather than the conclusion.

**Neither admitted anchor is unforgeable, and the design does not claim otherwise.** `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, and `refs/remotes/origin/<default>` are all plain local refs or gitdir files that ungated plumbing can write: `commit-tree`, `update-ref`, `git branch`, `git fetch` with an explicit destination refspec, `git symbolic-ref`, or a bare file write. That set is open, so no defense here rests on forgery being blocked. An honest `git fetch` does overwrite a forged remote-tracking ref back to the true tip, but nothing compels that fetch to run before the forgery is used.

An anchor is admissible only if it passes both legs:

- **Soundness.** When the anchor is reached honestly, with no forgery, does that imply the content already passed review?
- **Non-dominance.** Is forging the anchor for content of the agent's choosing cheaper than a bypass the design already accepts and does nothing to close? The baseline is not "cheaper than passing the gate", because that ceiling was never meaningful. Ungated plumbing already lets an agent reach a commit with zero gate firing.

The baseline bypass is the ungated clean merge: `commit-tree` a payload, `git branch` it, `git merge` it cleanly. No separate `git commit` call exists for any gate to see. See "Known gap: the ungated clean merge" in [`skill-review-gate-disarms-on-empty-base-relative-diff.md`](skill-review-gate-disarms-on-empty-base-relative-diff.md).

The two admitted anchors:

- **`HEAD`.** The test admits any OID that is an ancestor of `HEAD`, however it became one. Soundness is therefore bounded to the marker-gated commit shapes plus the accepted ungated routes, not to "reached from `HEAD` implies reviewed". The ungated routes are:
  - The baseline clean-merge bypass, and any plumbing that moves the branch ref without `git commit`.
  - The `git rebase --continue` carve-out above, including the `edit`-pause fold-in. A commit landed that way is `HEAD`-reachable and ran no marker gate.
  - Ordinary porcelain that makes foreign content `HEAD`-reachable with no marker: `git switch`, `git pull`, and a clean `git merge`.

  Forging `HEAD` onto a payload is landing the payload, so no cheaper dishonest route to the same outcome exists, and non-dominance holds. Content that reached `HEAD` through an ungated route and later becomes a state OID, such as through a revert of a revert or a cherry-pick re-application, is excluded from the review base without having been reviewed. That grants no leverage beyond the baseline bypass, because the conflicted operation that exploits it is dominated by the clean merge.
- **`origin/<default>`.** Soundness holds by workflow, not by blocked forgery. In this repo, which is squash-merge-only, the one legitimate path that moves the ref is the engineer's own PR merge. `block-gh-pr-merge.sh` blocks `gh pr merge` shapes. It does not block `gh api repos/OWNER/REPO/pulls/N/merge`, `eval`, or `bash -c` wrappers, which its header lists as known gaps tracked in GH-1077. In a repository with another merge workflow, soundness rests on whatever protects that repository's default branch from unreviewed content, which these hooks cannot verify. The `gh api` merge route is analysed with the direct push below. Non-dominance holds because smuggling a payload through a conflicted merge by forging this ref takes more steps than the clean-merge bypass, which needs no forgery, no engineered conflict, and leaves no marker. That comparison counts mechanical steps only, not audit-trail plausibility: an adversary optimizing for a review history that reads as genuine might still prefer the forged conflicted merge, which leaves a passed marker and a compliance-log line the clean-merge bypass does not.

The two rejected anchors, both proposed for the rebase case and both failing soundness:

- **`REBASE_HEAD` reachable from `ORIG_HEAD`.** `ORIG_HEAD` is a gitdir file any `printf` writes at zero cost, and nothing contradicts a fabricated value. Reaching it never implied passing review, honestly or otherwise.
- **`REBASE_HEAD` reachable from any `refs/remotes/*` ref.** `require-ready-for-review.sh` no-ops with no open PR, exits early on the default branch, and fails open when `gh` errors. Reaching a remote-tracking ref therefore never implied passing an automated review gate.

### Direct push to the default branch

`require-code-review.sh` matches only commit-concluding command shapes, so a commit built with `commit-tree` never reaches it. `require-ready-for-review.sh` is the only hook that gates a `git push` fragment, and it keys its default-branch bypass, its no-open-PR exit, and its marker check on the checked-out branch, not the push destination. Its header documents the push arm as a cooperative mistake-catcher, released by the bare fact that the active marker is live. Its tier line also lists `untrusted-input`, so the tier grants no waiver here. A `commit-tree`-built commit pushed with an explicit refspec to the remote's default branch, followed by a plain `git fetch`, makes `refs/remotes/origin/<default>` hold content the agent chose. Unlike a forged local ref, it survives the next fetch, because the remote now holds it.

The `gh api repos/OWNER/REPO/pulls/N/merge` route reaches the same outcome. It moves `origin/<default>` honestly, with real content and no forgery, and a plain fetch then makes the anchor hold content the agent chose. `block-gh-pr-merge.sh` does not inspect it, and its header lists it as a known gap.

Both residuals are accepted, not closed. Each is dominated by the clean-merge bypass, so the anchor grants no leverage that bypass did not already grant. The acceptance rests on that dominance, not on either gate's threat-model tier. Closing it locally would mean a new gate parsing `git push` refspecs and destinations, a redesign of `require-ready-for-review.sh`'s matcher and fail posture rather than a call-site sweep.

Two caveats limit how far the "loud push" reasoning carries:

- The verdict assumes `origin` names the shared repository. For a fork-based contributor `origin` is their own fork, pushing to its default branch is free and unobserved, and a later honest fetch confirms it. The verdict holds because non-dominance does not depend on visibility, but do not cite visibility as an independent reason this path is safe for every contributor.
- Server-side branch protection may refuse the push, but it is a per-repository setting these hooks cannot verify, and they run in whatever repository the contributor has open. Nothing here is grounded on it.

### Reopening dependency

Both admitted anchors' non-dominance leg rests on the clean-merge bypass, or on `bash -c`, `eval`, and git-alias wrapping, staying open and cheaper than any forgery. The `HEAD` anchor's soundness leg additionally depends on the `git rebase --continue` carve-out, including the `edit`-pause fold-in, because closing or widening it changes which content is `HEAD`-reachable without review. If any of these is closed or widened, re-run this analysis, including the two admitted anchors and the two rejected ones. Mid-rebase friction alone is not grounds for a new anchor. A candidate is admitted only if it passes both legs.

An anchor of the form "`REBASE_HEAD` is an OID for which a code-review marker already exists" would pass, since it reduces to the `HEAD` case. It does not work today, because markers store a staged-diff hash, not the commit OID they authorized. Designing that lookup is out of scope here.

## `merge-tree` writes loose objects that gc reclaims only eventually

`_lib_gate_diff_base` runs `git merge-tree --write-tree`, which writes the trial-merge tree, and any blobs and trees it needs, into the object database. Nothing references them, so they are unreachable from any ref or reflog. `_lib_capped`'s cap bounds one invocation's wall-clock time. It does not bound how many objects are written, and nothing in these hooks prunes what they write.

Reclamation is bounded and eventual in a default-configured repository:

- `gc.pruneExpire` defaults to a two-week grace period before `git gc` prunes unreachable objects.
- `gc.auto` defaults to about 6700 loose objects as the threshold at which the `git gc --auto` that ordinary git commands already run packs them.
- Setting `gc.auto` to 0 disables that packing and every other heuristic `git gc --auto` uses to decide whether there is work to do (`git help config`, `gc.auto`). In such a repository nothing reclaims these objects automatically.

Large repositories commonly set `gc.auto=0` because auto-gc is disruptive at their scale. Those are also the repositories where each trial merge writes the most objects, and where these hooks fire, since they run in whatever repository the contributor has open, not only this one. This design adds no other lever, so an operator of a repository with `gc.auto=0` should run `git gc` periodically. No per-merge or per-session object count has been measured, so "modest" describes the default configuration and is not a measurement.

Linked worktrees share one object database, so these objects are readable from every worktree of the repository until gc reclaims them. Nothing novel is exposed. `merge-tree` is invoked only with commit OIDs, never reads the index or working tree, and so never writes staged or uncommitted content. Every object derives from the input commits: `HEAD`, the state commit, and its merge base where the recipe passes one. The anchor test restricts the state commit to one reachable from `origin/<default>` or `HEAD`, so those inputs are already in the repository's history. Push and fetch transports send reachable objects only, so these unreachable objects do not leave the machine that way. A local-path clone, a copied `.git` directory, or a filesystem backup does carry them. The "Which tree a marker describes" paragraph in `docs/hooks.md`'s "Marker keying and gate-release authority" section states the same conclusion. See also "`merge-tree --write-tree` writes unreachable objects" in [`skill-review-gate-disarms-on-empty-base-relative-diff.md`](skill-review-gate-disarms-on-empty-base-relative-diff.md), where the revert pre-sample avoids the write entirely.

## `_lib_capped`'s cap is not a universal backstop

The 5s cap around each git call in base resolution is simultaneously the git-version fallback and the large-repository latency backstop. A `merge-tree` that exceeds it is killed, `_lib_gate_diff_base` returns status 2 with an empty stdout, and the caller consumes the empty base.

The latency half exists only where `timeout(1)` or `gtimeout(1)` is on PATH. `_lib_capped_for` runs the wrapped command uncapped otherwise, and `install.sh` warns about the missing binary at onboarding and continues rather than failing. Two consequences follow, and neither is corrected here:

- With neither binary, a `merge-tree` against a large repository stalls the tool call for as long as it takes, governed only by the harness's own hook timeout. That is the posture `_lib_jq`'s comment already records for a stalled `jq`, applied to a heavier command.
- With a binary present and a repository large enough to exceed 5s, the cap fires and the merger is charged for upstream's content exactly as with a HEAD-relative base.

Making `install.sh` fail instead of warn is declined. It would block onboarding on stock macOS without Homebrew coreutils, which is a change to install policy for every stow consumer that is out of proportion to a gate fix, and nothing re-checks PATH after install anyway.

The mitigation is diagnostic only. A status-2 fallback and a genuine no-state result both produce an empty base and the same over-gating deny, so `require-code-review.sh` and `require-skill-review.sh` name the undetermined base in the deny message they were already emitting. No log file, counter, or telemetry is added. The other consumers of the base (`marker.sh`, `_lib_active_plan_files`, `_lib_staged_length_gate`, `_lib_reviewer_round_state_value`) stay silent on status 2, because none is the surface a confused contributor reads.

## The round-3 consult gate stays disarmed for the duration of a rebase

`_lib_reviewer_round_state_key` keys on the branch name and returns empty on a detached HEAD, which is the state a rebase runs in. `require-architect-consult.sh` and `log-reviewer-round.sh` therefore take their allow and no-record paths throughout a rebase. The fail-open is deliberate: `require-architect-consult.sh` lists detached HEAD among its documented allow-on-state-failure cases. The design of that gate, including its per-branch state key, is in [`round3-plan-architect-consult-gate.md`](round3-plan-architect-consult-gate.md).

The carve-out does not widen the window. Because `require-code-review.sh` never fires on `git rebase --continue`, no `/code-review`, and no reviewer fan-out that would feed the round counter, is compelled mid-rebase where none was before. It also does not close it. `_lib_reviewer_round_state_value` routes its diff-hash half through the novel-content base, but the disarm lives in the key's branch half, which the base does not touch, so a reader should not assume the round-state machinery is rebase-correct. Fixing it means giving the key a rebase-aware branch source, such as `rebase-merge/head-name` or the pre-rebase branch. That is a change to the round-3 gate's design with its own fail-direction analysis, not a call-site sweep.

## The version-bump gates keep their narrow matcher

`require-plugin-version-bump.sh` and `require-npm-version-bump.sh` keep a bespoke `git commit` regex and their `hooks.json` `"if"` pre-filter. They stay blind to `git -c <key>=<value> commit` and to the `--continue` forms. Two reasons:

- Arming them would mean duplicating the shared matcher's dependency chain into two more trimmed plugin libs, each with its own definition-equality test.
- Arming them on `--continue` first requires deciding what "version strictly raised since the merge-base" means when the merge itself moved the merge-base.

The duplication cost of the two maintained copies of the matcher (stowed and `skill-management` plugin) is the price of keeping the plugin installable without this repo. It is guarded by byte-comparison of function bodies, not by shared differential fixtures, so a divergence fails CI on the body changing rather than on observed output changing.
