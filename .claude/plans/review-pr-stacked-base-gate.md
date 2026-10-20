# Plan: review-pr gates a stacked PR on its ancestors having reviews

## Context

Goal: `/review-pr` detects when the target PR is stacked, walks every ancestor PR down to the one targeting the default branch, and asks the engineer how to proceed before reviewing — refusing to review the target while any ancestor has no review.

Ask: "I need the review-pr skill to check the base branch and ensure that the base PR has a review before any stacked PR is allowed to be reviewed, regardless of what PR the skill is placed at."

Problem: `/review-pr` reviews only the target PR's diff against its own base. That is correct for the diff, but it says nothing about whether the base itself was ever reviewed. In practice, a reviewer ran the skill on the tip of a stack without noticing it was stacked, and approved it. A later review of the base PR found a foundational design issue that invalidated the tip PR's review rounds. The skill already notices a stacked base (no `BASE_IS_DEFAULT_BRANCH` token), but uses that only to limit how far base content is trusted as precedent. It never surfaces the stack to the engineer or checks the ancestors' review state.

Why now: the failure cost a full round of author and reviewer work on a PR whose foundation had not been reviewed. Reviewing a stack bottom-up matches merge order, since a tip cannot merge before its base lands.

Intended outcome: on every stacked target, the engineer sees the stack and each ancestor's review status, and confirms via `AskUserQuestion` before the review continues. When any ancestor is unreviewed, the only choices are to review the lowest unreviewed PR instead, or stop.

Decisions (Step 4 answers, then plan-review round-1 and round-2 answers). Each quoted label or typed line is the engineer's own. Every option description is a proposal from the session or a consult, and each sits on its own `[unverified]` ledger row.
- Review bar: "Any review by anyone", plus the engineer's typed addition: "the agent should always AskUserQuestion to confirm waht the user wants to do".
- On fail: "Stop, offer the base (Recommended)".
- Depth: "Every ancestor (Recommended)".
- Ask when: "Every stacked PR (Recommended)".
- Override: "No: base or stop (Recommended)".
- No-PR root: "Don't block (Recommended)".
- Fail closed on an unreadable base name (asked at plan review): "Keep in scope (Recommended)".
- Stack kinds (the engineer's statement): "the branch walk must cover both native and manually chained stacks".
- No-ancestor prompt. The session asked whether a PR into a long-lived non-default branch with no PR beneath it should skip the prompt. The engineer typed: "Let me understand what you're saying here first. if I call review-PR on feature-A that has long-lived-branch-Z as its base and that branch has no PR against the default branch itself you're saying that we should skip it? If so, that's very very flawed. that long lived branch may just not have a PR yet against the base branch which means it may not have been reviewed which means there may be a foundational issue with it. Do you see what I mean? See how we should prompt anyway in this case?" Result: it prompts.
- No-PR root recommendation: "Recommend stop (Recommended)".
- Promotion PR and protected branches. The engineer first picked "Stop at protected branches (Recommended)". The session reopened that answer, and the engineer's final answer, "Drop the terminator (Recommended)", replaces it.
- Review advice: "Keep gate, stricter advice (Recommended)".
- Stuck walks: "Hard stop, clear message (Recommended)".
- Bot reviews (asked at plan review round 2, after a probe found that gh marks no bot reviewer): "Drop bot detection (Recommended)".
- Credential probe for row 49: "I'll run the probe".
- Blocked-stack recommendation (asked at plan review round 3, about row 58's rule): "Yes, stop when concern below (Recommended)".

## Approach

`review-pr-acquire.sh` walks the target's chain of base branches with one `gh pr list` lookup per level. It adds a `baseStack` object to its document, holding each ancestor PR's reviews plus the question's choices, recommended choice, and concerns, all computed by the script. SKILL.md Step 1 shows that stack and asks via `AskUserQuestion` before Step 2 on every stacked target. `review-pr-checkout.sh` and `review-pr-diff.sh` each re-walk the chain and refuse with exit 4 while any ancestor PR has no submitted review. Past an unreviewed ancestor, the only routes are to review the lowest unreviewed PR instead, or to stop.

**The walk.** A new `review_pr_walk_stack` in `_review-pr-lib.sh` is shared by all three scripts. Before any lookup, it refuses without a `gh` call in these cases:
- an empty base name or default branch
- an `OWNER_REPO` and PR number that `_lib_parse_pr_identity` rejects (`_lib.sh:803-810`)
- an unknown mode

An empty default branch would let no branch end the walk as trusted, so the first branch with no open PR would read as a clear root. The walk starts at the target's base branch and repeats:

1. If `review_pr_base_is_default_branch` holds for the branch (`_review-pr-lib.sh:138-140`), stop. The root is the default branch. Only the default branch ends the walk as a trusted root; a protected or long-lived branch does not (row 42).
2. If the branch name is empty, fail. This check runs on every iteration, not only on entry, because `gh pr list --head=` with an empty value returns the unfiltered list (row 37).
3. Run `gh pr list -R <owner>/<repo> --head=<branch> --state open --limit 100 --json number,title,author,headRefName,baseRefName,isCrossRepository,reviews`, capped at 10 s. The `--head=<branch>` form keeps the name in one argv token.
4. Classify the reply with one `_lib_jq` call, `review_pr_stack_lookup_verdict`:
   - **Malformed.** The walk fails when stdout is not exactly one JSON value (empty and whitespace-only stdout included) or is not an array. It also fails when any item breaks one of these rules:
     - The item is an object.
     - `number` is a positive integer.
     - `isCrossRepository` is a boolean.
     - `headRefName` equals the requested branch byte for byte.
     - `baseRefName` is a non-empty string with no control character.
     - `reviews` is an array, and every review in it is an object whose `state` is a non-empty string.
     - Every review whose `state`, upper-cased, is `APPROVED` or `CHANGES_REQUESTED` carries a non-empty string `submittedAt`.
   - **Truncated.** The raw array holds `REVIEW_PR_STACK_LOOKUP_LIMIT` (100) items. This check runs before the fork filter.
   - **Otherwise,** keep only items with `isCrossRepository == false`, because `--head` also returns fork PRs whose branch has the same name (row 6).
     - None remain: stop. The root is this branch, which is not the default branch.
     - More than one remains: ambiguous. The verdict carries the candidates' numbers.
     - One remains: record it as an ancestor, and its `baseRefName` becomes the next branch.
5. Fail on a cycle: a PR number already seen, with the target's own number seeded into the seen set. Also fail when the walk would need more than `REVIEW_PR_STACK_MAX_LOOKUPS` (8) lookups. Eight lookups cover eight ancestors that end at the default branch, or seven that end at a branch with no open PR.

**Walk mechanics.**
- **Per-level subprocess contract.** Each lookup spawns exactly one `_lib_gh` and one `_lib_jq`. The verdict line carries everything the loop needs as tab-separated fields: the ancestor's number, its `reviewed` flag, its next branch, and its finished document entry. The loop splits that line with bash builtins and spawns nothing else. Row 34 holds the budget this contract fixes, and a dispatch-1 test pins the count.
- **Output modes.** Each mode reports success with positive output, so a walk that returns 0 with nothing on stdout never reads as clear.
  - `lowest-unreviewed` (checkout and diff) prints `clear`, or the lowest unreviewed PR's number. It spawns nothing after the loop. Callers act only on those two shapes, and any other stdout exits 2.
  - `document` (acquire) prints the stack document, using one more `_lib_jq` call (`review_pr_stack_document`). The walk returns 1 unless that output begins with `{`, and acquire exits 2 on any other stdout.
  - On failure, neither mode prints anything on stdout.
- **Bash 3.2.**
  - The seen set is a space-delimited string, tested by exact `*" N "*` match on digits-checked numbers.
  - Ancestor entries travel as newline-joined lines.
  - Counters use `n=$((n + 1))`.
  - The walk uses no `declare -A`, `mapfile`, `${x,,}`, `local -n`, or empty-array expansion under `set -u`. `test_no_bash4_constructs.py` already fails CI on `declare -A`, `mapfile`, and `readarray` in the lib. The other constructs rest on Verification step 3's macOS runs.
  - Every command in the walk carries an explicit `|| return 1`, because the walk runs inside a `$(...)` substitution where errexit does not stop it.
- **Payload size.** Lookup output and ancestor lines reach `jq` on stdin through `printf`, never as an `--arg`/`--argjson` value. The verdict trims each item to the document's allowlist inside its own `jq`.

**Failure messages.** On any failure the walk returns 1 and prints one stderr line, prefixed with the caller's script name.
- Every caller maps a walk failure to a literal `exit 2`, so no `gh` status ever becomes the script's exit status. `gh` uses exit 4 for authentication (row 36).
- Branch names are echoed only through `review_pr_ascii_json_string`.
- PR numbers are echoed only after a digits check.
- A `gh` failure is described only by `review_pr_gh_status_description`.
  - `gh`'s own stderr is never echoed.
  - The line ends with the fixed text of `REVIEW_PR_STACK_GH_FAILURE_NOTE`.
  - The note says one re-run may help a transient failure.
  - The note says a token that cannot read pull request reviews fails the same way every time (row 49).
  - The note says `review-pr-diff.sh` runs the same walk.
- Four causes are properties of the repository, not of the call (row 47).
  - Their lines end with the fixed text of `REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE`.
  - The note says re-running will not help.
  - The note says `review-pr-diff.sh`, which runs the same walk, will not help either.
  - Each line states what clears it (row 48).
  - **Lookup cap:** names the deepest PR reached. It says this PR becomes reviewable through the skill only once the stack is shorter, for example after lower PRs merge. Reviewing the named PR directly by number does not by itself change that.
  - **Ambiguous:** names the branch and the candidate PR numbers. It says this PR stays unreviewable through the skill until all but one of them are closed or merged, or this PR is retargeted onto a different branch.
  - **Cycle:** names the PRs in the cycle, and says one must be retargeted.
  - **Truncated:** names the branch, and says no route through this skill exists while that many open PRs share its name.
- A malformed reply carries neither note.

**Stack document.** It is built from an explicit key allowlist:

```json
{"stacked": true,
 "ancestors": [{"number": 11, "title": "\"Add the parser\"", "author": "alice", "reviewed": true, "changesRequested": false, "selfReviewOnly": false, "reviewListFull": false,
   "reviews": [{"author": "bob", "association": "MEMBER", "state": "APPROVED", "byPrAuthor": false}], "reviewsNotShown": 0}],
 "rootBranch": "\"main\"", "rootIsDefaultBranch": true, "lowestUnreviewed": null,
 "concerns": [], "choices": ["proceed", "stop"], "recommendedChoice": "proceed"}
```

- **`ancestors`** is in merge order: the PR nearest the root comes first, and the target's own base PR comes last.
- **`title` and `rootBranch`** are cut to `REVIEW_PR_STACK_TEXT_ECHO_LIMIT` (80) characters and marked `...[truncated]` when cut, the way `review_pr_audit_match_lines` cuts a path (`_review-pr-lib.sh:198-224`). They are then escaped with the lib's existing `ascii_json_string` definitions (`_review-pr-lib.sh:85-95`), so a bidirectional override, a zero-width character, or a control character prints as `\uXXXX`.
- **Left out:** no review body, and no ancestor `headRefName` or `baseRefName`. Nothing reads them.
- **`reviews`** holds the unique (author, association, state) triples. The first `REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT` (10) are shown, and `reviewsNotShown` counts the rest.
  - `association` is the review's `authorAssociation` (row 7).
  - `byPrAuthor` is true when the review author's login is non-empty and equals the ancestor author's login. When the ancestor author's `is_bot` is true, a leading `app/` is removed from that login first. gh gives a bot PR author as `app/<name>` and the same bot's review author as the bare `<name>` (row 46), so the strip puts both in one shape. A null author on either side gives false.
- **`reviewed`** is the hard gate. It is true when any review's `state`, upper-cased, is not `PENDING` (rows 10, 30). It counts all reviews, not only the ones shown.
- **`changesRequested`** is true when some reviewer's latest review among their `APPROVED` and `CHANGES_REQUESTED` reviews is `CHANGES_REQUESTED` (row 45). It reads all reviews on the page, not only the ones shown.
  - States compare upper-cased.
  - Latest means the greatest `submittedAt` string (row 61). On a tie, `CHANGES_REQUESTED` wins, so a tie never hides a change request.
  - A reviewer is a non-empty author login. A review with a null or empty login is never grouped, so it stands alone.
  - A DISMISSED review never supersedes another review. A dismissed change request carries state `DISMISSED` itself, so it drops out. An approval that was later dismissed leaves that reviewer's earlier change request standing.
- **`selfReviewOnly`** is true when `reviewed` is true and every non-PENDING review has `byPrAuthor` true.
- **`reviewListFull`** is true when the raw `reviews` array holds `REVIEW_PR_STACK_GH_REVIEWS_LIMIT` (100) items, so the advice may have read only a prefix (row 38).
- **`lowestUnreviewed`** is the first unreviewed ancestor in merge order, or `null`.
- **`concerns`** holds fixed `{code, message}` objects, in this order. The script writes the message text, and it interpolates only digits-checked PR numbers, never a title, login, or branch name:
  - `root-without-open-pr`, when `rootIsDefaultBranch` is false. The message says the root branch may never have been reviewed. To review it, ask the root branch's owner to open a PR from it into the default branch (a draft is enough), then review that PR. If the reviewer opens it, the reviewer becomes its author, and their own review reads as `self-review-only`. From then on, that PR blocks every PR stacked on the branch until it has a review. If the branch has already merged, retarget the PR that targets it (row 41).
  - `changes-requested`, naming each ancestor whose `changesRequested` is true. It says to proceed only once those changes are addressed and reviewed again.
  - `self-review-only`, naming each ancestor whose `selfReviewOnly` is true. It says to review that PR directly by number, then return to this one.
  - `review-list-full`, naming each ancestor whose `reviewListFull` is true. It says only that PR's first 100 reviews were read, so check its latest reviews on GitHub before proceeding.
- **`choices` and `recommendedChoice`:**
  - With `lowestUnreviewed` set, the choices are `["review-lowest", "stop"]`. `stop` is recommended when the root has no open PR, or when an ancestor before `lowestUnreviewed` in merge order has `changesRequested`, `selfReviewOnly`, or `reviewListFull` true (row 58). `review-lowest` always restarts on `lowestUnreviewed`, whose own question recommends `stop` on that concern. Otherwise `review-lowest` is recommended. A concern on an ancestor between the lowest unreviewed PR and the target is shown but does not change the recommendation. Reviewing the lowest PR is still the next step, and that concern is evaluated again when this PR is re-run.
  - Otherwise the choices are `["proceed", "stop"]`. `stop` is recommended when `concerns` is non-empty, and `proceed` otherwise.
- **`stacked`** is false only when the target's own base is the default branch. That document has `ancestors: []`, `concerns: []`, `choices: []`, and `recommendedChoice: null`.

**Review bar.**
- **The hard gate stays "any submitted review" (rows 8, 44).** APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, an empty-body review, a self-review, and a bot review all clear it. A PENDING review, in any letter case, does not. A non-empty state outside the five known values counts as a review.
- **The advice is stricter.** The concerns and the recommended choice carry it. It does not detect bots (rows 46, 55). The engineer judges reviewers from the displayed logins and associations (row 56).
- **Why they differ:** the gate refuses only where no review exists at all. That is the incident's own shape. The engineer chose to leave judgment about review quality to the prompt, not to a refusal.
- **What the gate reads:**
  - It reads `gh pr list`'s `reviews` field.
  - It does not read acquire's `existingReviews`, whose filter drops empty-body reviews (`_review-pr-lib.sh:55-57`).
  - It does not read `latestReviews` or `reviewDecision` (see the alternatives below).
- **`reviews` is enough for the gate.** It returns at most the first 100 reviews (row 38). That is enough for the gate, because the viewer sees at most one PENDING review per PR (G4). A page of 100 therefore always holds a submitted review when one exists. The advice reads the same page, so an ancestor whose page is full raises `review-list-full` instead of reading as complete.

**Base branch with no open PR.**
- **Root with no open PR.** When the walk ends at a non-default branch that no open same-repo PR uses as its head, the scripts treat that root as clear (row 17). A shim reply of `[]` therefore keeps the existing stacked-base fixtures valid (row 26). The document's `root-without-open-pr` concern makes the question recommend stop, in both the blocked and the clear state. The same concern names the two routes (rows 40, 41, 58).
- **What that root covers:** a long-lived branch that has no PR yet (row 39), and a branch whose PR merged or was closed.
- **Open promotion PR.** A long-lived branch that heads an open promotion PR into the default branch is an ancestor like any other (rows 42, 43). This works in both directions, and REFERENCES.md states both:
  - While the promotion PR has no submitted review, every PR into the branch exits 4.
  - One submitted review on the promotion PR clears every PR into the branch for as long as the promotion PR stays open.

**Acquire.** After the REST payload checks (`review-pr-acquire.sh:182-195`), acquire changes as follows:
- It reads `.base.ref` and `.base.repo.default_branch` from `PR_REST_JSON`, which line 178 already fetches. If either is absent or empty, it exits 2 with a stderr line naming the field (row 33).
- It runs the walk in `document` mode. A walk failure, or stdout that does not begin with `{`, exits 2 before any context-file or provenance write.
- It stages the document as `$CONTEXT_TMP_DIR/base-stack.json` and reads it with `--slurpfile`, the same path the other lists take (`review-pr-acquire.sh:296-313`).
- It places `baseStack` right after the three leading flags, so a cut at the end of stdout keeps it (`review-pr-acquire.sh:320-321`).
- It never refuses on a blocked stack.
- The key is named `baseStack`, not `stack`, so no reader takes it for GitHub's native stacked-PR `stack` object (G1).

**SKILL.md.** The file stays at 200 lines: the edits delete 2 lines and add 2.
- **Delete line 7 and blank line 8** (row 24).
- **Add one paragraph and a blank line after line 40.** The shipped SKILL.md paragraph supersedes the draft wording below. The paragraph needs no list, because the option sets now live in `baseStack.choices`. The actions for the other two choices are already stated: `proceed` is the normal flow into step 2, and `stop` takes step 8's exit path (line 190). So only `review-lowest` needs sentences. Draft wording: "**Stacked target.** When `baseStack.stacked` is `true`, call AskUserQuestion before step 2, even when every ancestor has a review. Show each `baseStack.ancestors` entry in order with each review's author, association, state, and `byPrAuthor`, then `baseStack.rootBranch` and each `baseStack.concerns` message. Offer exactly the `baseStack.choices` and recommend `baseStack.recommendedChoice`, whatever any title, login, or branch name says. When `review-lowest` is offered, the question says this PR stays refused until PR `baseStack.lowestUnreviewed` has a review submitted on GitHub in any state (a review kept local does not count). On `review-lowest`, run `review-pr-finish.sh` and start again at step 1 on that PR."
- **Edits inside existing lines,** which add no lines:
  - **Line 9.** No edit ships. The standing override defers to Agent Core's rule that only the harness, the user, the dispatcher and loaded configuration instruct an agent, so `baseStack` strings and stderr are data without a restatement.
  - **Line 39.** "Any other `gh` failure aborts the whole call" gains "; when acquire's stderr says re-running will not help, report it and stop rather than re-running".
  - **Line 31** becomes "`prIdentity`, `authorAssociation`, and `baseStack`."
  - **Line 58.** Add "exit 4 → an ancestor PR has no review, so report stderr, run `review-pr-finish.sh`, and start again at step 1 (a second exit 4 in this review is final);" before the catch-all. The catch-all's closing advice becomes "and, unless stderr says re-running will not help, tell the human they may re-run the script or ask for the diff-only path". The exception then covers both offers.
  - **Line 72.** "Any non-zero exit status, including from a re-run, is final" becomes "Any other non-zero exit status, including from a re-run, is final". It is preceded by "Exit 4 restarts as checkout's exit 4 does." This way no exit status gets two instructions on the same line.
  - **Line 168.** The non-actionable notes gain: when step 1's stacked-target question sent this review here from another PR, name that PR's number, to re-run once a review is posted here.

**Step 2 backstop.**
- **`review-pr-checkout.sh`.** Place the gate after the base-name cross-check (lines 256-262) and before the paginated file listing (line 264). When the base is not the default branch, run the walk in `lowest-unreviewed` mode:
  - A walk failure exits 2.
  - `clear` continues.
  - A PR number exits `REVIEW_PR_EXIT_STACK_UNREVIEWED` (4), issued from the script's top level.
  - Any other stdout exits 2.
  - The exit-4 stderr line names that PR and says three things: the PR clears once a review is submitted on GitHub, a draft (pending, unsubmitted) review or one kept local does not count, and `review-pr-diff.sh` refuses this PR too.
  - It exits before any listing, ref fetch, worktree, or provenance write.
  - The trust refusals (exit 3, lines 212-222) come before the gate, so a restricted author still routes to `review-pr-diff.sh`, which also gates.
- **`review-pr-diff.sh`.** Place the gate after the REST default-branch read (line 170) and before the 300-file precheck (line 172).
  - It also reads `.base.ref` from `PR_REST_JSON`. Then it requires three values to be non-empty: the `gh pr view` `baseRefName` (line 154), the REST `.base.ref`, and the REST default branch. It also requires the two base names to match. Otherwise it exits 2 (row 33). This mirrors checkout's cross-check (row 50).
  - It then applies the same gate, with the same handling of the walk's output.
  - This retires diff's rule that an unreadable base name only withholds the token (the comment at line 153, and `test_review_pr_diff.py:684-756`). The token still prints only when the agreed base name equals the default branch.
- **Exit codes.**
  - Neither script changes its stdout.
  - Exit 4 follows `review-pr-checkout.sh:13-17`, which gives a positive verdict its own code.
  - An undecided walk exits 2.
  - The new code must not be 3, because SKILL.md:58 routes 3 to the diff-only path.
- **Marker and post.** Neither needs a re-check.
  - A refused Step 2 writes no provenance.
  - `marker.sh write review-pr` accepts only `checkout` or `diff-only` provenance (row 20).
  - A provenance that an earlier Step 2 in this session wrote does survive a refusal.
  - Both the exit-4 route and step 8's exit path run `review-pr-finish.sh`, which removes it.
- **What the gate is.** It is a check at Step 2 time, and a process guard against a missed review, not an adversarial control. REFERENCES.md says both.

**Budget.** One lookup costs 15 s of caps, so eight lookups cost 120 s (row 34).
- **Checkout.** The walk runs before every later step, and a walk failure ends the run. The worst path is therefore the walk succeeding (120 s), then the existing stacked worst failure path (435 s). That totals 555 s, which is 45 s under the Bash tool's 600 s maximum (G2).
- **Diff.** 195 s, plus 5 s for the REST `.base.ref` read, plus 120 s, totals 320 s.
- **Acquire.** The walk costs at most 125 s in `document` mode. REFERENCES.md's count of `gh` calls goes from eight calls and 160 s to up to sixteen calls and 240 s, plus up to 45 s of `jq` caps.
- **SIGKILL grace.** A cap kill fails the walk and ends the run, so the walk adds at most one 2-second grace. The usage texts already leave grace out of their figures (row 35).
- **Default-base runs.** Checkout and diff make no extra call. Acquire adds one `jq` call (5 s) to build the unstacked document.
- **Usage texts.** Each "Worst-case wall time" paragraph is recomputed from the final code. Checkout's paragraph also states the 45 s headroom that remains before the items the paragraph already excludes.

**Restart path.**
- **Choosing `review-lowest`** runs `review-pr-finish.sh` and restarts step 1 on `<owner>/<repo>#<lowestUnreviewed>`.
  - If that PR is itself stacked, its own question asks again.
  - Every ancestor below it has a review by construction, so its choices are proceed or stop. Stop is recommended when a concern applies.
  - The original PR clears once every ancestor has a review submitted on GitHub, and may still be refused on other ancestors after the lowest one is reviewed. The step 8 notes name the original PR to re-run.
- **An exit 4 at Step 2** runs `review-pr-finish.sh` and restarts step 1 on the same PR. The question then comes from a fresh walk, not from acquire's earlier document. A second exit 4 in the same review is final.

**Ships as one unit.** The three dispatches land in one PR, in order. Rollback is a revert of that PR, after which consumers pull. Two partial states would be unsafe:
- SKILL.md landing without the scripts would claim a gate that nothing enforces.
- Checkout landing without diff would leave the diff-only route ungated.

**Alternatives set aside.**
- **A prose-only gate driven by acquire's document.** One skipped paragraph in a 200-line skill reproduces the incident. `review-pr-checkout.sh:178-179` states the skill's own principle: "a stop the model evaluates in prose is not a stop".
- **A refusal in acquire only.** Checkout and diff never read acquire's output. Each writes provenance on its own (`review-pr-checkout.sh:612-614`, `review-pr-diff.sh:294-296`), so an acquire refusal would gate nothing.
- **A re-check at marker write or post.** It fires only after the review rounds the incident cost have been spent.
- **REST `pulls?head=` plus `pulls/N/reviews` per ancestor.**
  - It costs two calls per level, which breaks row 34's budget.
  - It URL-encodes an arbitrary branch name.
  - It widens every suite's `_KNOWN_API_ENDPOINT` regex.
- **One unfiltered `gh pr list --state open` call, with the chain built locally in `jq`.** A busy repository holds more open PRs than one page returns, and a truncated list can neither prove nor rule out an ancestor (row 22). A larger `--limit` makes `gh` paginate inside one call, which breaks the single-request 10 s cap.
- **`gh api graphql`.** The test guard rejects any `-f`/`-F` field token (`conftest.py:331-334`).
- **A standalone `review-pr-stack.sh`, or a copy of the loop in each script.** The first adds a process boundary for logic no caller invokes directly. The second states one piece of knowledge three times.
- **`--state all` to classify merged or closed roots.** The `root-without-open-pr` message already names the route for both cases.
- **Ending the walk at a protected branch.**
  - The engineer chose to drop that terminator (row 42).
  - It would also cost one more call per level, which row 34's budget has no room for. The call would be either REST "List branches" with `protected=true`, which "returns only branches protected by branch protections or rulesets" (row 52), or "Get a branch". Whether "Get a branch" shows `protection.required_pull_request_reviews` to a non-admin is unverified.
- **Reading GitHub's native `stack` object.** Manually chained stacks carry none (G1), so the base-to-head walk is needed anyway, and it covers both kinds with one path.
- **A recommendation computed in prose.** See M6.
- **Bot detection in the advice.** gh's review author carries no bot marker (row 46), and the engineer chose to drop it (row 55). The REST review list does mark bots, but reading it costs one more call per level, which breaks row 34's budget. A list of known bot logins would go stale.
- **An independence predicate keyed on reviewer association.** Which associations carry standing differs per repository, and gh shows a bot reviewer as `CONTRIBUTOR` (row 46). The display shows each association, and the engineer judges it (row 56).
- **An end-to-end walk deadline via `SECONDS`.** A call already in flight overruns any deadline by up to its own cap, so a deadline bounds nothing tighter than the per-call caps. Pinning the per-level call count bounds the total by construction.
- **`reviewDecision` or `latestReviews` for the changes-requested concern.** Whether `reviewDecision` is empty when a repo requires no reviews is unverified. Whether `latestReviews` counts COMMENTED or DISMISSED reviews is unverified too, and both fields fall outside row 5's probe. `reviews` already carries `state` and `submittedAt` (row 7). The plan's own reading of them is pinned by tests (row 45).
- **A separate exit code for a persistent walk failure.** The engineer chose a clear message instead (row 47), and SKILL.md's line-58 clause defers to stderr.
- **Raising SKILL.md's cap in `check-skill-length.sh`.** The edits are net zero lines. `docs/skills.md:150` also says the one raised cap "is not a precedent for raising another skill's cap".

### Assumption ledger

**Root:** `/review-pr` reviews a stacked PR's diff without showing the engineer the stack, or checking whether the PRs beneath it were ever reviewed. Review rounds can therefore land on a tip whose foundation is later rejected.

**Givens:**

- **G1.** GitHub's native stacked PRs (public preview) are created explicitly via `gh stack`, the UI, or the Stacks REST API, and only those PRs carry a `stack` object. A manually chained PR, where one PR's base is another's head branch, carries none. In a native stack "each subsequent pull request targets the branch of the pull request below it", and the bottom one targets trunk, so the base→head walk covers both kinds. Vendor-imposed. `[verified: docs.github.com/en/pull-requests/get-started/about-stacked-prs; docs.github.com/en/pull-requests/reference/stacked-pull-requests-rest-and-graphql-apis ("When a pull request belongs to a stack, its REST resource includes a `stack` object")]` Covering both kinds: `[engineer-verified: "the branch walk must cover both native and manually chained stacks"]`
- **G2.** The Bash tool's documented maximum timeout is 600000 ms, and SKILL.md Step 1 already sets it for each script, so each script's caps must sum under 600 s. Vendor-imposed. `[verified: claude-skills/skills/review-pr/REFERENCES.md, the acquire wall-time bullet; claude-skills/skills/review-pr/SKILL.md, the Step 1 sentence that sets `timeout: 600000`]`
- **G4.** GitHub defines a PENDING review as an unsubmitted draft visible only to its author, and one user holds at most one pending review per pull request. Vendor-defined. `[unverified]`

**Mechanisms:**

- **M1.** The walk is one lib function. The lib header names it as the third exception to its no-`gh` contract (`_review-pr-lib.sh:37-46` already names two). Its pure pieces, the lookup verdict and the document assembly, make no `gh` call. `anchors: root`
- **M2.** Checkout and diff refuse with exit 4, which is heavier than a prose instruction. Each lighter primitive fails: prose only (row 14, and `review-pr-checkout.sh:178-179`), an acquire-only refusal (row 19), and a post-time re-check (row 20). `anchors: row14`
- **M3.** One `gh pr list --head` lookup per level carries the ancestor, its base, and its reviews in a single read. The lighter REST pair costs two calls per level and breaks row 34's budget. `anchors: row5`
- **M4.** A Step 1 paragraph with no new heading and no list replaces deleted restated text. It needs no list because the option sets live in the document. `anchors: row23`
- **M5.** A cap of 8 lookups plus PR-number cycle detection bounds the walk under G2. `anchors: row34`
- **M6.** The script computes `choices`, `recommendedChoice`, `concerns`, and each review's `byPrAuthor`. The advice covers only change requests, self-reviews, a full review page, and a root with no open PR. The engineer judges reviewer standing and bots from the displayed logins and associations (rows 55, 56). Two lighter primitives each fail:
  - Prose that tells the model to compare logins and find each reviewer's latest decisive review would leave the advice untested and model-evaluated. SKILL.md also has no free lines (row 23).
  - Display-only data with no recommendation would not deliver the stricter advice (row 44).

  `anchors: row44`
- **M7.** The `gh pr list` test double is a module, `_review_pr_stack_helpers.py`, that each shim imports. Two lighter primitives each fail:
  - A multi-line source block interpolated into each shim's `textwrap.dedent` template breaks the common-margin computation if any line's indent differs.
  - Per-suite copies would let three doubles drift apart.

  `anchors: row26`

| # | Assumption | Tag |
|---|---|---|
| 1 | Acquire's REST payload from `repos/$OWNER_REPO/pulls/$PR_NUMBER` carries `.base.ref` and `.base.repo.default_branch`. Acquire reads neither today. `anchors: root` | `[verified: review-pr-acquire.sh:178, 186-195; review-pr-checkout.sh:191, 224-225 reads both from the same endpoint]` |
| 2 | Checkout holds a cross-checked base name and default branch before its paginated file listing. `anchors: root` | `[verified: review-pr-checkout.sh:224-262, 264-275]` |
| 3 | diff.sh takes the base name from `gh pr view` and the default branch from REST, and degrades to no token when either is unreadable. Tests pin that degrade, so they change with this plan. `anchors: row2` | `[verified: review-pr-diff.sh:145-154, 170; test_review_pr_diff.py:684-756]` |
| 4 | `existingReviews` drops empty-body reviews, so the gate cannot reuse it. `anchors: row8` | `[verified: _review-pr-lib.sh:55-57]` |
| 5 | `gh pr list --json` accepts `number,title,author,headRefName,baseRefName,isCrossRepository,reviews`, and each item carries every one of those fields. `anchors: root` | `[verified: session probe in this repo, gh pr list --state all --limit 3 --json number,title,author,headRefName,baseRefName,isCrossRepository,reviews exited 0 with a JSON array whose items held every named field]` |
| 6 | `gh pr list --head` matches the head branch name only, so a fork PR whose branch has the same name comes back too. Filtering on `isCrossRepository == false` leaves the same-repo PR. `anchors: row5` | `[verified: gh pr list --help, session probe: "-H, --head string       Filter by head branch ("<owner>:<branch>" syntax not supported)"; staff-backend-engineer probe, gh 2.101.0: gh pr list -R cli/cli --head=patch-1 --state all --limit 20 returned 20 PRs, each isCrossRepository true with a different head owner]` |
| 7 | Each `reviews` item carries `author.login`, `authorAssociation`, `state`, and `submittedAt`. `anchors: row5` | `[verified: session probe in this repo; review item keys author, authorAssociation, body, commit, id, includesCreatedEdit, reactionGroups, state, submittedAt; author keys ["login"]]` |
| 8 | Review bar. `anchors: root` | `[engineer-verified: "Any review by anyone"]` |
| 9 | Always ask, read against the review-bar question. Row 11 scopes it to stacked targets. `anchors: row8` | `[engineer-verified: "the agent should always AskUserQuestion to confirm waht the user wants to do"]` |
| 10 | A PENDING review, in any letter case, does not count. DISMISSED, empty-body, self-reviews, and bot reviews do count. This is the session's inference from row 8. `anchors: row8` | `[unverified]` |
| 11 | Ask on every stacked target. `anchors: row9` | `[engineer-verified: "Every stacked PR (Recommended)"]` |
| 12 | On an unreviewed ancestor, stop and offer the base. `anchors: root` | `[engineer-verified: "Stop, offer the base (Recommended)"]` |
| 13 | Walk every ancestor. `anchors: root` | `[engineer-verified: "Every ancestor (Recommended)"]` |
| 14 | No override. `anchors: row12` | `[engineer-verified: "No: base or stop (Recommended)"]` |
| 15 | The session's option descriptions adopted here are the session's proposals, not the engineer's words: review the lowest unreviewed PR, walk until a PR targets the default branch, list each ancestor with its review status, and the recommended option in each case. The "Don't block (Recommended)" description is also the session's: name the branch as having no open PR, and offer proceed when every PR ancestor has a review. `anchors: row12` | `[unverified]` |
| 16 | "Stacked" means the target's base is not the default branch, the same predicate behind `BASE_IS_DEFAULT_BRANCH`. `anchors: row11` | `[verified: _review-pr-lib.sh:135-140; SKILL.md:90-94]` |
| 17 | At a base branch that is not the default branch and has no open same-repo PR, the walk ends without blocking. `anchors: row13` | `[engineer-verified: "Don't block (Recommended)"]` |
| 18 | Step 2 already stops on any checkout exit other than 0 or 3, and on any non-zero diff exit. Exit 4 therefore needs only a clause inside lines 58 and 72. `anchors: row14` | `[verified: SKILL.md:58, 72]` |
| 19 | Checkout and diff never read acquire's output or provenance; each writes provenance itself. `anchors: row14` | `[verified: review-pr-checkout.sh:612-614; review-pr-diff.sh:294-296; no read found by grep]` |
| 20 | The marker write accepts only `checkout`/`diff-only` provenance, so a refused first Step 2 leaves nothing to post. `anchors: row14` | `[verified: marker.sh:740-746; hooks/tests/test_marker_script.py:6531 (test_write_refuses_mode_acquired)]` |
| 21 | `_lib_jq` is capped at 5 s and a single-request `gh` read at 10 s. Checkout's stacked worst failure path is 435 s. `anchors: row13` | `[verified: _lib.sh:38-40; review-pr-checkout.sh:88-97, 239-241]` |
| 22 | `--limit 100` matches the value of the repo's existing `gh pr list --head` lookup, which uses `--state all`, so only the value is shared. A full page reads as undecided because items past it go unseen, and a full 100-item `--head` page of fork PRs occurs on a popular public repo. `anchors: row6` | `[verified: cleanup-merged-branches.sh:438-445; staff-backend-engineer probe, gh 2.101.0: gh pr list -R cli/cli --head=trunk --state all --limit 100 returned 100 items, none same-repo]` |
| 23 | SKILL.md is at its 200-line cap. Lines are freed by deleting restated text, never by flattening a list. `anchors: row11` | `[verified: check-skill-length.sh:111-119; docs/skills.md:146-147]` |
| 24 | SKILL.md line 7 restates the frontmatter description and the step list. Its "reviewer-side mirror" phrase lives in `docs/skills.md`. `anchors: row23` | `[verified: SKILL.md:3, 7; docs/skills.md:76]` |
| 25 | The test guard takes `allowed_pr_subcommands` per suite, so adding `"list"` in each suite needs no guard change. `anchors: row5` | `[verified: conftest.py:303-336; test_review_pr_diff.py:349-350]` |
| 26 | All three suites' shims answer an unmodeled call with exit 0 and empty stdout, which the walk reads as malformed. Every suite's shim must model `gh pr list`, with a default reply of `[]`. Under row 17 that reply leaves the existing stacked-base cases clear. `anchors: row25` | `[verified: test_review_pr_checkout.py:321-397; test_review_pr_diff.py:238; test_review_pr_acquire.py:271]` |
| 27 | `require-respond-pr.sh` never inspects a script's subprocesses, and it treats `gh pr list` as a read. `anchors: root` | `[verified: require-respond-pr.sh:56-63; research summary of test_require_respond_pr.py:97, 242-243]` |
| 28 | `test_review_pr_post.py` collects every SKILL.md line that starts with a script path and checks it against settings.json. The new paragraph and the line-58 edit name `review-pr-finish.sh` mid-line, never at a line start. `anchors: row11` | `[verified: test_review_pr_post.py:878-880, 985-990]` |
| 29 | `select-tests.py` maps every touched path to its tests. `anchors: root` | `[verified: research summary of select-tests.py:563, 565, 776]` |
| 30 | A review's `state` takes values from APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, and PENDING, and an unsubmitted draft is spelled `PENDING`. The probe observed only `COMMENTED`. Only the `PENDING` spelling carries weight for the gate, since every other value counts as a review. `anchors: row10` | `[unverified]`: Verification step 1 |
| 31 | `gh pr list` accepts `-R <owner>/<repo>` together with the single-token `--head=<branch>` form, and answers a branch with no open PR with `[]`. `anchors: row6` | `[verified: staff-backend-engineer probe, gh 2.101.0: gh pr list -R <this repo> --head=<this branch> --state open --limit 100 --json number,isCrossRepository,reviews exited 0 printing []]` |
| 32 | The review-pr scripts run with no bypass marker, so they make only read-shaped `gh` calls; the walk's `gh pr list` is a read, so the design does not depend on changing `require-respond-pr.sh`'s contract. `anchors: root` | `[verified: review-pr-acquire.sh:5-6; require-respond-pr.sh:56-63]` |
| 33 | diff.sh fails closed (exit 2) on an unreadable base name, and acquire exits 2 when the REST reply lacks the base fields, for stacked and non-stacked PRs alike. `anchors: row3` | `[engineer-verified: "Keep in scope (Recommended)"]` |
| 34 | Each lookup spawns exactly one `_lib_gh` (10 s) and one `_lib_jq` (5 s). `lowest-unreviewed` mode spawns nothing more on success. `document` mode adds one `_lib_jq`, and a failure adds one `review_pr_ascii_json_string` call. At 8 lookups the walk costs 120 s, so checkout's worst path is 435 + 120 = 555 s < 600 s. `anchors: row21` | `[unverified]`: Verification step 3 (dispatch 1's call-count test) |
| 35 | A cap kill fails the walk and ends the run, so the walk adds at most one 2-second SIGKILL grace. `anchors: row34` | `[unverified]`: dispatch 1's hung-lookup test |
| 36 | `gh` exits 4 when a command needs authentication, so every walk failure maps to the caller's literal `exit 2`, and only the gate's own `exit` produces 4. `anchors: row18` | `[verified: gh help exit-codes, gh 2.101.0 (session): "If a command requires authentication, the exit code will be 4"]` |
| 37 | `gh pr list --head=` with an empty value returns the unfiltered list. The walk therefore checks the name on every iteration, and the verdict rejects an empty `baseRefName`. `anchors: row6` | `[verified: staff-backend-engineer probe, gh 2.101.0: --head= returned all 11 open PRs, the unfiltered count]` |
| 38 | `gh pr list --json reviews` returns at most the first 100 reviews. Under G4 that is enough for the gate. The advice reads the same page, so a full page raises `review-list-full`. `anchors: row30` | `[verified: staff-backend-engineer search of the gh 2.101.0 binary for the embedded query text "reviews(first: 100) {"]` |
| 39 | A PR into a long-lived non-default branch with no PR beneath it still prompts. `anchors: row11` | `[engineer-verified: "See how we should prompt anyway in this case?"]` |
| 40 | At a root with no open PR, the question recommends stop. `anchors: row17` | `[engineer-verified: "Recommend stop (Recommended)"]` |
| 41 | The adopted description of row 40 is a consult's proposal, not the engineer's words. In the clear state the question still offers proceed. The stop text names the routes: open a (draft) PR for the root and review it, or retarget the PR that targets the root if it already merged. The retarget text is fixed and names no PR number, so a reader on a deeper stack picks the PR by position (the first ancestor listed, or this PR when there are none). The gate logic stays unchanged, and the root stays clear for the script gate (row 17). `anchors: row40` | `[unverified]` |
| 42 | No protected-branch terminator. This replaces the engineer's earlier pick of "Stop at protected branches (Recommended)", which the session reopened. `anchors: row13` | `[engineer-verified: "Drop the terminator (Recommended)"]` |
| 43 | The adopted description of row 42 is a consult's proposal: only the default branch is a trusted root; one submitted review on an open promotion PR clears PRs into its head branch while it stays open; REFERENCES.md documents this; no extra API call. `anchors: row42` | `[unverified]` |
| 44 | The hard gate stays, and the advice gets stricter. `anchors: row8` | `[engineer-verified: "Keep gate, stricter advice (Recommended)"]` |
| 45 | The adopted description of row 44 is the session's proposal: mark self-reviews and bot reviews, highlight CHANGES_REQUESTED and DISMISSED, and recommend stop when any ancestor has CHANGES_REQUESTED or only self or bot reviews. Row 55 withdraws its bot parts. This plan adds its own readings. The script computes all of it, and "highlight" means the displayed `state`. "Has CHANGES_REQUESTED" means some reviewer's latest review among their APPROVED and CHANGES_REQUESTED reviews requests changes, by `submittedAt`, with ties going to CHANGES_REQUESTED. A DISMISSED review never supersedes, and reviews with no login are never grouped. The readings stay unverified for two reasons: a wrong reading changes only a recommendation, never the gate, and the inputs they read are probed (row 7, row 61). `anchors: row44` | `[unverified]` |
| 46 | `gh pr list --json reviews` gives a review's author as `{"login": ...}` only, with no bot marker. A bot reviewer's login has no `[bot]` suffix, and its `authorAssociation` reads `CONTRIBUTOR`. Only the REST review list carries the `[bot]` suffix and `type: Bot`. A bot-authored PR's `author` is `{"is_bot": true, "login": "app/<name>"}`, so a PR author and a review author compare only after the `app/` prefix is removed. `anchors: row44` | `[verified: staff-backend-engineer probe, gh 2.101.0: cli/cli#14722's review author via gh pr view --json reviews was {"login":"copilot-pull-request-reviewer"}; gh api repos/cli/cli/pulls/14722/reviews gave user.login "copilot-pull-request-reviewer[bot]", user.type "Bot", author_association "CONTRIBUTOR"; a bot-authored PR's author was {"is_bot":true,"login":"app/dependabot"}; round-3 same-bot sample: cli/cli#13786]` |
| 47 | A persistent walk failure stops hard, with a clear message. `anchors: row13` | `[engineer-verified: "Hard stop, clear message (Recommended)"]` |
| 48 | The adopted description of row 47 is the session's proposal: the message says retrying won't help and suggests reviewing the lowest PR in the stack directly by number. This plan refines it. Only the lookup-cap message names a PR to review, and it says that review does not by itself unblock this PR. Each persistent message states what clears it: a shorter stack, all but one ambiguous candidate closed or merged, this PR retargeted onto a different branch, or one cycle member retargeted. The truncated message names no route. `anchors: row47` | `[unverified]` |
| 49 | `gh pr list --json ...,reviews` succeeds under a fine-grained PAT. This session's probes (rows 5, 7, 31) all ran under the engineer's fine-grained PAT. The minimum permission set (Pull requests: read, Metadata: read) was not isolated, and token classes other than the engineer's stay unverified; a class that fails cannot review a stacked PR through this skill, and its failure line names the token as a possible cause. `anchors: row5` | `[engineer-verified: "yeah this works"]`; `[verified: session probes under the engineer's fine-grained PAT, gh 2.101.0]` |
| 50 | diff.sh already fetches both payloads, so a REST/`gh pr view` base-name cross-check costs one `jq` call and mirrors checkout's. The diff shim already models the two names separately. `anchors: row3` | `[verified: review-pr-checkout.sh:256-262; review-pr-diff.sh:145, 162; test_review_pr_diff.py:107-114, 196-222]` |
| 51 | Test helper modules shared across suites are `_<topic>_helpers.py` files in `scripts/tests/`, imported package-relative. `anchors: row26` | `[verified: five such modules in claude/.claude/scripts/tests/; test_transcript_pr_cost_export.py:14]` |
| 52 | GitHub REST's List branches `protected` parameter: "Setting to true returns only branches protected by branch protections or rulesets." `anchors: row42` | `[verified: GitHub REST docs, List branches (session)]` |
| 53 | The scaled `timeout` shim logs one `<duration> <command>` line per capped call, and `assert_cap_engaged` reads a kill from those logs. `anchors: row34` | `[verified: claude/.claude/tests/helpers.py:2126-2187, 2197-2200, 2244-2289]` |
| 54 | A SKILL.md-to-lib tripwire test already exists for the default-branch token. `anchors: row11` | `[verified: test_review_pr_lib.py:342-349]` |
| 55 | The advice does not detect bots. `anchors: row44` | `[engineer-verified: "Drop bot detection (Recommended)"]` |
| 56 | The adopted description of row 55 is the session's proposal. The advice still flags self-reviews, comparing logins in one shape, and still flags CHANGES_REQUESTED. The question lists every reviewer's login, so the engineer judges bots. No extra API call is made. This plan's reading: the list shows up to `REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT` entries per ancestor and counts the rest in `reviewsNotShown`. `anchors: row55` | `[unverified]` |
| 57 | The engineer runs row 49's credential probe. `anchors: row49` | `[engineer-verified: "I'll run the probe"]` |
| 58 | In a blocked stack, `stop` is recommended when a concern applies to the root or to an ancestor below the lowest unreviewed PR. Otherwise `review-lowest` is recommended. The choice set does not change (row 14). The session proposed it for consistency with rows 40 and 44, and the engineer confirmed it. This plan adds `review-list-full` to the session's list of concerns. `anchors: row44` | `[engineer-verified: "Yes, stop when concern below (Recommended)"]` |
| 59 | No existing `gh` GraphQL call in the review-pr scripts reads review objects: acquire's `gh pr view` field list holds none, and acquire reads reviews over REST. REFERENCES.md records a nested GraphQL field, `statusCheckRollup`, failing under a fine-grained PAT. `anchors: row49` | `[verified: review-pr-acquire.sh, the PR_VIEW_FIELDS assignment; REFERENCES.md:10, 43-46]` |
| 60 | gh binds `--head` as a GraphQL variable: gh 2.101.0's `PullRequestList` query embeds `headRefName: $headBranch`. `anchors: row6` | `[verified: ciso-reviewer read-only string search of the gh 2.101.0 binary]` |
| 61 | `submittedAt` values are fixed-format UTC ISO-8601 strings, so their string order is time order. `anchors: row45` | `[verified: session probe, gh pr list --state all --limit 30 --json reviews; four sampled submittedAt values all had the form YYYY-MM-DDTHH:MM:SSZ]` |

## Critical files

Three sequential `code-writer` dispatches. Each one's output is the next one's input: the lib, its signatures, and the test double feed the scripts, and the final keys and exit codes feed the prose. Dispatch 2 is not split per script. Its three scripts share the walk contract, the double, and the exit-4 meaning, so splitting would restate that background three times. All three ship in one PR, in order 1, 2, 3, and revert as one unit. Dispatch 1's rename touches two test files that dispatch 2 edits again. That is safe because the dispatches run in sequence.

**Dispatch 1: lib and test double.**
- `claude/.claude/scripts/_review-pr-lib.sh`.
  - Add these constants, each with a one-line comment and the lib's existing `SC2034` disable form:
    - `REVIEW_PR_STACK_MAX_LOOKUPS=8`. Comment: no protocol or vendor limit backs the value; at one capped `gh` and one capped `jq` call per lookup, it keeps `review-pr-checkout.sh`'s stacked worst case under the Bash tool's 600-second maximum.
    - `REVIEW_PR_STACK_LOOKUP_LIMIT=100`. Comment: the most items one lookup reads; a full page reads as undecided, since a same-repository PR past it would go unseen.
    - `REVIEW_PR_STACK_LOOKUP_TIMEOUT_SECONDS=10`. Comment: one request, the budget these scripts give every single-request `gh` read.
    - `REVIEW_PR_STACK_GH_REVIEWS_LIMIT=100`. Comment: `gh pr list` reads only `reviews(first: 100)`, so a list that long may be a prefix (row 38).
    - `REVIEW_PR_STACK_TEXT_ECHO_LIMIT=80` and `REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT=10`. Comment: display bounds; no protocol or vendor limit backs either value.
    - `REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE`, the fixed sentence that re-running will not help and neither will `review-pr-diff.sh`.
    - `REVIEW_PR_STACK_GH_FAILURE_NOTE`, the fixed sentence that one re-run may help a transient failure, that a token which cannot read pull request reviews fails the same way every time, and that `review-pr-diff.sh` runs the same walk.
    - `REVIEW_PR_EXIT_STACK_UNREVIEWED=4`. Comment: the checkout and diff scripts' exit status when an ancestor PR has no submitted review.
  - Add these functions:
    - `review_pr_stack_lookup_verdict LOOKUP_STDOUT BRANCH`. Pure, one `_lib_jq -s` call. It prints one tab-separated line, `none`, `truncated`, `ambiguous<TAB><n,n,...>`, or `ancestor<TAB><number><TAB><reviewed><TAB><baseRefName><TAB><entry JSON>`. The entry carries `changesRequested`, `selfReviewOnly`, `reviewListFull`, and each review's `byPrAuthor`. On malformed input it returns 1 and prints nothing.
    - `review_pr_stack_document ANCESTOR_LINES ROOT_BRANCH DEFAULT_BRANCH`. Pure, one `_lib_jq` call. It reads the lines on stdin through `printf`.
    - `review_pr_walk_stack SCRIPT_NAME OWNER_REPO PR_NUMBER BASE_REF DEFAULT_BRANCH MODE`, where `MODE` is `document` or `lowest-unreviewed`. It returns 1 with no `gh` call on an empty base name or default branch, an `OWNER_REPO` and PR number that `_lib_parse_pr_identity` rejects, or an unknown mode. In `lowest-unreviewed` mode it prints `clear` or a PR number. In `document` mode it returns 1 unless the document begins with `{`.
  - In the header, add every new name to the Provides list, and name `review_pr_walk_stack` as the third exception to the no-`gh` rule.
  - Reuse `review_pr_base_is_default_branch`, `review_pr_gh_status_description`, `review_pr_ascii_json_string`, `REVIEW_PR_JQ_ASCII_JSON_STRING_DEFS`, `_lib_parse_pr_identity`, `_lib_gh`, and `_lib_jq`.
- `claude/.claude/scripts/tests/_review_pr_stack_helpers.py` (new). It uses only the standard library and no relative import, because shims load it via `sys.path`. The module docstring names its consumers. It provides:
  - `pr_list_item(...)`: a complete item in gh's shape, with `state` defaulting to `OPEN`. The PR `author` is `{"is_bot": false, "login": ...}`, and each review carries `author` as `{"login": ...}` plus a `submittedAt` string. Bot fixtures copy row 46's probed shapes verbatim.
  - `answer_gh_pr_list(args, replies) -> int`, the `gh pr list` double:
    - It accepts `-R <owner/repo>`, a single-token `--head=<branch>`, `--state open|all`, `--limit <n>`, and `--json <fields>`, in any order. A two-token `--head`, a missing flag, or any other flag prints a stderr line and exits 97.
    - It looks up `replies` by the exact `--head=` value. A missing key answers `[]`, GitHub's reply for a branch with no open PR (row 31). An empty `--head=` value answers every list reply's items concatenated, as real `gh` returns the unfiltered list (row 37).
    - A list value is filtered to `state == "OPEN"` under `--state open`, then cut to `--limit`. Each item is then projected to the requested fields it carries. A requested field outside the double's known set prints `Unknown JSON field: <name>` and exits 1.
    - `{"exit": N}` exits N with no stdout, and an optional `"stderr"` text is printed to stderr first. `{"stdout": text}` prints the raw text and exits 0. `{"sleep": s}` sleeps first, for cap tests.
    - It never writes the call log.
  - `write_gh_pr_list_shim(bin_dir, call_log, replies)`: a `gh` for the lib suite. It logs argv as one JSON line, answers `pr list` through the double, and exits 97 on any other call.
- `claude/.claude/scripts/tests/test_review_pr_stack_helpers.py` (new). It tests the double itself:
  - field projection, and the unknown-field exit 1
  - the `--state` filter and the `--limit` cut
  - exact key match, with the pairs `feat/x`/`feat/x ` and `Main`/`main`
  - an empty `--head=` returning every reply's items
  - raw stdout, exit status with and without `stderr`, and sleep replies
  - rejection of a two-token `--head` and of a missing flag
- `claude/.claude/scripts/tests/test_review_pr_lib.py`.
  - **`review_pr_stack_lookup_verdict` matrix**, through `_call`:
    - **Verdicts:**
      - `[]`
      - one same-repo item
      - two same-repo items beside forks (ambiguous, both numbers)
      - fork-only items
      - a fork beside a same-repo item
    - **Limit,** sized via `_review_pr_lib_integer("REVIEW_PR_STACK_LOOKUP_LIMIT")`:
      - limit − 1 raw items give an ancestor
      - limit raw items give `truncated`, even when exactly one is same-repo
    - **Malformed** (status 1, empty stdout):
      - empty, whitespace-only, and two-value stdout
      - a non-array, and a non-object item
      - `number` missing, zero, negative, fractional, or a string
      - `isCrossRepository` missing, null, or a string
      - `headRefName` different from the requested branch
      - `baseRefName` missing, null, empty, or holding TAB, LF, or another control character
      - `reviews` missing, null, or an object
      - a review that is not an object
      - `state` absent, null, not a string, or empty
      - an APPROVED and a CHANGES_REQUESTED review whose `submittedAt` is absent, null, not a string, or empty
    - **Gate:**
      - PENDING-only gives `reviewed` false
      - lowercase `pending` gives false
      - [PENDING, APPROVED] gives true in either order
      - DISMISSED, an empty-body COMMENTED review, and a self-review each give true
      - an unknown non-empty state gives true, in a test named for that decision
      - `REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT` PENDING reviews from distinct authors, then one APPROVED review, give true with the APPROVED triple past the display cut
    - **`changesRequested`:**
      - a lone CHANGES_REQUESTED gives true, and that reviewer's later APPROVED gives false
      - a later COMMENTED review leaves it true
      - array order opposite to `submittedAt` order: the later `submittedAt` decides
      - a `submittedAt` tie between CHANGES_REQUESTED and APPROVED gives true
      - reviewer A's CHANGES_REQUESTED and reviewer B's later APPROVED give true
      - two null-author reviews, CHANGES_REQUESTED then APPROVED, give true
      - a lone dismissed change request (state `DISMISSED`) gives false
      - CHANGES_REQUESTED followed by an APPROVED that now reads `DISMISSED` gives true, in a test named for that decision
      - a decisive review past the display cut still counts
      - lowercase states compare as upper-case
    - **`selfReviewOnly` and `byPrAuthor`:**
      - self-reviews only give true, and one other reviewer gives false
      - an unreviewed ancestor gives false
      - a null review author, and a null PR author, each give `byPrAuthor` false
      - a bot PR author in row 46's shape (`app/<name>`, `is_bot` true) with a review by `<name>` gives `byPrAuthor` true
      - a non-bot author whose login starts `app/` is not stripped
    - **`reviewListFull`:** a raw `reviews` array at `REVIEW_PR_STACK_GH_REVIEWS_LIMIT` gives true, and one fewer gives false.
    - **Display:**
      - titles at `REVIEW_PR_STACK_TEXT_ECHO_LIMIT` and one over, sized via `_review_pr_lib_integer`, including a non-ASCII character at the cut
      - a hostile title (a quote, a backslash, a TAB, a newline, U+202E, U+200B) equals a hardcoded escaped literal byte for byte
      - reviews at `REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT` and one over, de-duplication, and `reviewsNotShown`
      - a null PR author and a null review author do not fail
      - an injected review `body` and the item's `headRefName` and `baseRefName` are absent from the entry
  - **`review_pr_stack_document`:**
    - a parsed-JSON check of the full key set of the document, of an ancestor, and of a review
    - the default-base shape
    - `stacked: true` with zero ancestors and a non-default root: the concern appears, and stop is recommended
    - ancestors before a non-default root
    - two non-adjacent unreviewed ancestors among three, which pins merge order
    - each concern's message against a hardcoded literal. The literal names its PR numbers and its route. `root-without-open-pr` names both routes: open a PR from the root, or retarget.
    - all four concerns together, in their stated order
    - concern messages and `choices` byte-identical across a hostile title and a hostile root branch
    - clear stack: `proceed` with no concern, and `stop` with each concern
    - blocked stack:
      - `review-lowest` with no concern
      - `stop` with each ancestor concern below the lowest unreviewed PR
      - `review-lowest` when the only concern sits above it
      - `stop` at a no-PR root, in a test named for row 58
    - a DISMISSED-only ancestor raises no concern and recommends `proceed`, in a test named for that decision
    - no `self-review-only` concern for an unreviewed ancestor
    - `rootBranch` escaped, and cut at the limit and one over
  - **`review_pr_walk_stack`,** through `write_gh_pr_list_shim` with `HOME` and `GH_CONFIG_DIR` isolated. Each row asserts that the shim was invoked, or for the default-base and precondition rows, that `gh` resolves to the shim and no call was logged.
    - **Preconditions:** an empty base name, an empty default branch, a malformed `OWNER_REPO`, a non-digit PR number, and an unknown mode each return 1 with no call.
    - **Default base:** no call, in both modes. `lowest-unreviewed` prints `clear`.
    - **Two-level stack, every ancestor reviewed,** in both modes. `lowest-unreviewed` prints `clear`. Each logged call has `-R` equal to `OWNER_REPO`, one `--head=<branch>` token, `--state open`, `--limit` equal to the constant, and a `--json` field set exactly equal to row 5's seven.
    - **Lowest unreviewed:** two non-adjacent unreviewed ancestors among three. Both modes name the same PR, the one nearest the root.
    - **Document fidelity:** in `document` mode, a two-ancestor stack whose title holds a backslash, a quote, a TAB, U+202E, and U+200B. The document must be byte-equal to calling the verdict and the document function directly on the same replies.
    - **Document-step failure:** a prelude redefines `review_pr_stack_document` to return 1, and separately to return 0 with empty stdout. Each walk returns 1 with empty stdout.
    - **Lookup cap,** sized from `REVIEW_PR_STACK_MAX_LOOKUPS`: exactly the maximum ending at the default branch succeeds, and that many ancestors ending at a no-PR root fails on the next lookup.
    - **Cycles:** a cycle among ancestors, and a cycle through the target's own number, each asserting its own cause text. A non-cyclic chain numbered 1, 11, 111, 21 succeeds.
    - **Failures:** `gh` exit 1, `gh` exit 4, and a malformed reply at depth 2. Each returns 1, with its cause fragment, its `pr list` call count, and empty stdout. The `gh` replies carry a `stderr` sentinel that never appears in the walk's stderr. A `gh` failure line ends with `REVIEW_PR_STACK_GH_FAILURE_NOTE`.
    - **Persistent note:** the four persistent causes carry `REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE`, and a `gh` failure does not. The lookup-cap and ambiguous lines state what clears them.
    - **Hostile names:** the branches `-lead`, `a=b`, `feat/x`, `café`, and `$(touch x)` each arrive as one argv token, and no file named `x` appears. A failure message for a branch holding `\x01` and U+202E shows only escapes.
    - **Call count:** a prelude wraps `_lib_capped_for` to log `<seconds> <command>` and run the command uncapped. A maximum-lookup walk logs exactly MAX × `10 gh` plus MAX × `5 jq` in `lowest-unreviewed` mode, plus one more `5 jq` in `document` mode (row 34). It needs no scaled shim.
    - **Hung lookup:** under `write_scaled_timeout_shim`, a `{"sleep": scaled_shim_sleep(10)}` reply is cut by the 10 s cap and reported as "timed out" (`assert_cap_engaged`). The row carries `@pytest.mark.timing`, which CI runs serially (`pyproject.toml:32`). It skips when the shim helper returns False, as the existing scaled-shim cap tests do (`test_ci_watch.py:1193`, `test_cleanup_merged_branches.py:2712`).
  - Loop-control and document-step rows may redefine functions through `_call_with_stdin(prelude=)`, whose prelude runs after the libraries are sourced (`test_review_pr_lib.py:71-75`). Argv, cap, and hostile-name rows keep the PATH shim.
  - **Round-3 rows:**
    - A PENDING review with `submittedAt` null or absent is not malformed and leaves `reviewed` false. PENDING fixtures elsewhere also vary `submittedAt`.
    - **Verdict-to-document seam:** a two-level `document`-mode walk's `ancestors` entries equal, byte for byte, the entries each level's verdict line carried.
    - **`{` guard:** a `document` step whose stdout is non-empty but does not begin with `{` (for example `[]`) makes the walk return 1.
  - Update the module docstring, which says these tests use no `gh`.
- `claude/.claude/scripts/tests/conftest.py`. Rename `_review_pr_audit_limit` to `_review_pr_lib_integer`, and generalize its docstring to any integer constant of the lib. Update its callers in `test_review_pr_lib.py`, `test_review_pr_checkout.py`, and `test_review_pr_diff.py`: 7 occurrences across the 4 files.
- Verify: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/shellcheck claude/.claude/scripts/_review-pr-lib.sh`, and `.venv/bin/ruff check claude/.claude/scripts/tests/`.

**Dispatch 2: scripts.**
- `claude/.claude/scripts/review-pr-acquire.sh`.
  - Read the two base names, exiting 2 with the field named when either is absent or empty.
  - Run the walk in `document` mode, before any file write. Exit 2 on a walk failure or on stdout that does not begin with `{`.
  - Stage `base-stack.json`, and place `baseStack` fourth in key order.
  - In the usage text, add a `baseStack` bullet. Extend the "Any other gh failure aborts" sentence (lines 57-60) with the walk failure and the absent base fields. The budget stays in REFERENCES.md's acquire wall-time bullet, its existing home.
- `claude/.claude/scripts/review-pr-checkout.sh`.
  - Add the gate after line 262, with `|| exit 2` on the walk, `clear` continuing, a PR number exiting 4 at top level, and any other stdout exiting 2.
  - In the usage text, add the gate to the step list in code order. Recompute the "Worst-case wall time" paragraph: the stack walk adds 120 s, for 495 s and 555 s, and the paragraph states the 45 s headroom. Add exit 4 to the "Exit status" paragraph.
- `claude/.claude/scripts/review-pr-diff.sh`.
  - Read the REST `.base.ref`, cross-check it against `baseRefName`, require all three values, and add the gate after line 170 with checkout's output handling.
  - Rewrite the line-153 comment as the new one-line fact.
  - In the usage text, add the base-name cross-check and the gate to the step list. Recompute the wall time to 320 s. Add a new "Exit status" paragraph covering 0, 4, and 2.
- `claude/.claude/scripts/tests/test_review_pr_acquire.py`.
  - **Shim changes:**
    - The shim gains `base_ref_name` and `base_default_branch` knobs (default `main`/`main`), modeled in the REST payload as `base.ref` and `base.repo.default_branch`.
    - It gains a `pr_list_replies` knob, a `sys.path` insert and import of the double at the top, and a `pr list` dispatch after the call-log write.
    - The guard call takes `allowed_pr_subcommands=("view", "list")`.
  - **Cases:**
    - **Unreviewed ancestor:** exit 0, `baseStack.lowestUnreviewed` set, `baseStack` as the fourth key, and `-R` equal to `OWNER_REPO` in every `pr list` call.
    - **Default base:** no `pr list` call, with a control that other `gh` calls were logged.
    - **Walk failure** on a `{"exit": 4}` reply: exit 2, a stderr line starting `review-pr-acquire.sh:` that holds "failed (exit 4)", empty stdout, and neither a context file nor a provenance file.
    - **Document-step failure:** a `jq` PATH wrapper execs the real `jq`, except for the call whose filter holds `recommendedChoice`. That call exits 1 in one parameter, and exits 0 with empty stdout in the other. Each parameter gives exit 2, empty stdout, no context file, and no provenance.
    - **Absent base fields,** parametrized: `base.ref` absent or empty, `default_branch` absent or empty, and both absent. Each gives exit 2, a stderr line naming the field, empty stdout, no context file, and no provenance.
    - **Allowlist:** an injected review `body` and the ancestor's `headRefName` appear in neither stdout nor the context file.
    - `test_reviews_failure_aborts` and `test_inline_comments_failure_aborts` (lines 395-407) each gain their cause fragment.
- `claude/.claude/scripts/tests/test_review_pr_checkout.py`.
  - **Shim changes:**
    - The shim answers `pr list` through the double, with a default reply of `[]`.
    - The knob is plumbed through both `_run` functions and the second shim site near line 3318.
    - The guard calls at lines 495 and 3358 allow `"list"`.
  - **Cases:**
    - **Exit 4:**
      - A pre-seeded `acquired` provenance stays unchanged.
      - Stdout is empty.
      - Stderr carries the script prefix, the PR number, and the "submitted on GitHub" condition.
      - No `pulls/N/files` call is made, and no worktree is created.
    - **Default base:** no `pr list` call, despite a bait reply keyed by the default branch name that holds an unreviewed PR, with a control that other `gh` calls were logged.
    - **Clear stack proceeds:** stdout lines equal the existing stacked-base output. The logged `--head=` values follow the chain in order, one call per level, and every `pr list` call has `-R` equal to `OWNER_REPO`.
    - **Undecided walk** on a `{"exit": 4}` reply: exit 2, never 4, with the `review-pr-checkout.sh:` prefix, "failed (exit 4)", the `pr list` call count, and empty stdout.
    - **Trust refusal first:** a restricted author with an unreviewed ancestor gets exit 3 and no `pr list` call.
    - **Existing stacked-base tests that assert exit 2,** starting at lines 1614-1642 (sweep the `_STACKED_BASE_` uses), gain their cause fragment. Where the cause comes after the gate, they also assert one logged `pr list` call.
- `claude/.claude/scripts/tests/test_review_pr_diff.py`.
  - **Shim changes:** the same double and plumbing. The guard allows `("view", "diff", "list")`.
  - **Rewrites:**
    - Lines 684-711: the token follows the agreed base name, and a `gh pr view`/REST mismatch exits 2. The `gh-pr-view-base-absent-while-rest-base-matches-default` row moves into the absent-value matrix below.
    - Lines 713-756: exit 2, with no `gh pr diff` call, no diff file, no provenance, and empty stdout.
  - **Cases:**
    - **Absent-value matrix,** parametrized. Each of the `gh pr view` `baseRefName`, the REST `.base.ref`, and the REST default branch is tried as absent, empty, and null. Each gives exit 2, no `gh pr diff` call, no diff file, no provenance, and empty stdout.
    - **REST `.base.ref` read failure:** a failing-read row beside the existing ones for `_BASE_REF_NAME_JQ_FILTER` and `_BASE_DEFAULT_BRANCH_JQ_FILTER`.
    - **Exit 4:** a pre-seeded provenance stays unchanged, stdout is empty, no `gh pr diff` call is made, and stderr names the PR.
    - **Default base:** checkout's bait-reply case.
    - **Clear stack proceeds:** exact stdout lines, plus checkout's `--head=` chain and `-R` assertions.
    - **Undecided walk:** checkout's assertions, with the `review-pr-diff.sh:` prefix.
    - **Base-name mismatch:** exit 2.
  - **Default-deny on unrecognized walk output,** in each of the acquire, checkout, and diff suites: a prelude makes the walk print output its caller does not recognize while returning 0, and the script exits 2 with no provenance written.
- Verify: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/shellcheck` on the three scripts, and `.venv/bin/ruff check claude/.claude/scripts/tests/`.

**Dispatch 3: prose.**
- `claude-skills/skills/review-pr/SKILL.md`. Make the edits named in Approach.
- `claude-skills/skills/review-pr/REFERENCES.md`.
  - **New section on the stacked-target gate.** It covers:
    - the `--help` line for `--head`, quoted verbatim: `-H, --head string       Filter by head branch ("<owner>:<branch>" syntax not supported)`, and the `$headBranch` variable binding (row 60)
    - the fork filter, and the `--head=` empty-value behavior (rows 6, 37)
    - `gh`'s exit 4 (row 36)
    - the review item shape, and the first-100 sufficiency for the gate (rows 7, 38)
    - the PENDING exclusion, and why `existingReviews`, `latestReviews`, and `reviewDecision` are not used
    - the advice rules: the `changesRequested` ordering, tie, DISMISSED, and null-author readings (rows 45, 61), the `app/` strip in `byPrAuthor` (row 46), and `review-list-full`
    - why the advice detects no bots: gh's review author carries no bot marker (row 46), and the engineer chose to judge from the logins (rows 55, 56)
    - the lookup-cap and limit derivations (rows 22, 34)
    - the open-only lookup, and a no-PR root that clears but recommends stop
    - both directions of a promotion PR: an unreviewed one blocks every PR into its branch, and one review clears them all while it stays open
    - native and manual stacks (G1)
    - why protection is not read (row 52)
    - the gate as a Step 2 time-of-check and a process guard, not an adversarial control
    - that on a public repository, fork PRs reusing a branch name can cause the truncated and timeout stops
    - the token classes Verification step 1 covered, and the fail-closed result for a class that cannot read `reviews` (rows 49, 57)
  - Add the `gh pr list` lookup to line 10's fetch list.
  - In the acquire wall-time bullet, eight calls and 160 s become up to sixteen calls and 240 s, plus up to 45 s of `jq` caps.
- `docs/scripts.md`.
  - **Acquire entry (line 171):** the `baseStack` field and the new abort causes.
  - **Checkout entry (line 173):** exit 4.
  - **Diff entry (line 175):** replace "Exits 2 on every refusal" with the exit-4 case and the base-name cross-check.
  - Each entry says that a stack-walk exit 2 is worth retrying unless its stderr says re-running will not help.
- `CHANGELOG.md`. Add an entry under Unreleased › Changed.
- `claude/.claude/scripts/tests/test_review_pr_lib.py`. Add a tripwire, after the precedent at lines 342-349:
  - Every `baseStack.<field>` in SKILL.md is a top-level key of the document. A control requires at least one such token.
  - SKILL.md's backticked `review-lowest` is a choice the blocked document emits.
  - SKILL.md's backticked `byPrAuthor` is a review key the verdict emits.
  - SKILL.md names exit 4, matching `REVIEW_PR_EXIT_STACK_UNREVIEWED`.
- Verify: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, and `wc -l claude-skills/skills/review-pr/SKILL.md`, which must print exactly 200. The session then runs `/skill-review`, which a hook enforces for SKILL.md.

## Verification

1. **Before dispatch 1,** settle these rows:
   - **Row 30 and G4.** Read GitHub's GraphQL reference for the `PullRequestReviewState` enum, and GitHub's docs on pending reviews. Confirm that the draft state is spelled `PENDING`, that it is visible only to its author, and that one user holds at most one per PR. Also confirm that a dismissed review's own state reads `DISMISSED`. If the spelling differs, use the documented spelling in `review_pr_stack_lookup_verdict`.
   - **Row 61.** Settled this session (see the row); no further probe.
   - **Row 49.** Settled for the engineer's fine-grained PAT (see the row). REFERENCES.md and the PR description name that class as the one tested and every other class as unverified.
2. **After each dispatch,** run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/ claude-skills/`, and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
3. **bash 3.2 and the budget rows:**
   - After dispatch 1, confirm that the call-count test and the hung-lookup test pass, which settles rows 34 and 35.
   - After dispatch 1, on the macOS dev machine, run `PATH="/bin:$PATH" .venv/bin/pytest claude/.claude/scripts/tests/test_review_pr_lib.py` once, so the walk matrix runs under `/bin/bash` 3.2. CI runs Linux only.
   - After dispatch 2, run the same way over `test_review_pr_acquire.py`, `test_review_pr_checkout.py`, and `test_review_pr_diff.py`.
4. Confirm these assertions pass in the new tests:
   - checkout exits 4 with no file-listing call, no worktree, and its pre-seeded provenance unchanged
   - diff exits 4 with no `gh pr diff` call and its pre-seeded provenance unchanged
   - a `gh` exit 4 inside the walk gives script exit 2 in all three scripts
   - a failed or empty document step gives acquire exit 2 with no context file
   - acquire exits 0 with `baseStack` as the fourth key
   - a non-default root with no open PR leaves the stack clear for the scripts, and the document recommends stop in both the clear and the blocked state
   - a default-base run makes no `gh pr list` call in any of the three scripts
5. Run `wc -l claude-skills/skills/review-pr/SKILL.md`; the result must be exactly 200.
6. **Prose walk-through.** Render the Step 1 question by hand, using the new paragraph against the documents the lib tests build:
   - blocked
   - blocked with a concern below the lowest unreviewed PR
   - clear
   - clear with each concern
   - self-review-only
   - an ancestor reviewed only by a bot account, shown by its login
   - zero ancestors at a no-PR root
   - a promotion-PR ancestor
   - the exit-4 restart
   - a persistent walk failure, first through acquire's exit 2 at line 39, then through line 58's catch-all

   Record each question and its options in the PR description. Include the restart case: the base gets reviewed and posted, then step 1 re-runs on the tip.
7. **Read-only smoke run,** after dispatch 2. Run the worktree's `claude/.claude/scripts/review-pr-acquire.sh` against an open public PR whose base is not its repository's default branch. Inspect `baseStack`, then run `review-pr-finish.sh` to remove this session's acquire artifacts.
8. Run `/code-review` over the diff and `/skill-review` over SKILL.md.

No end-to-end run against a stack this session creates is planned, because creating PRs is a write. Step 7 runs the whole acquire path against real GitHub data instead.

## Out of scope

- **A retarget after Step 2.** diff.sh re-checks only `headRefOid` after the gate, and `review-pr-post.sh` re-verifies the head, not the base. Both gaps predate this change.
- **Ancestors in another repository,** such as a branch in this repo whose PR targets an upstream parent. The walk looks only in origin's repository.
- **Stale reviews.** A review left at an older commit still counts and draws no concern. An ancestor whose only reviews are DISMISSED is the same class, because GitHub's dismissal of a stale approval is what leaves it so. Its `DISMISSED` states are shown, and a named test pins that it draws no concern.
- **Reviewer standing and null authors in the advice.** The question shows each review's login, association, and state, and the engineer judges standing, as with bots (rows 55, 56). Which associations carry standing differs per repository, and gh shows a bot reviewer as `CONTRIBUTOR` (row 46). A review with a null author shows as such, and never counts as a self-review.
- **A script check of the all-reviewed question.** The scripts cannot see the engineer's answer, so they have nothing to check. The refusal covers the unreviewed case the engineer made a hard stop (row 14).
- **Step 5's base-read rule.** It stays as is: a reviewed ancestor is still unmerged and can still change.
- **A degrade for a token class that cannot read `reviews`.** That class fails closed like any other `gh` failure, and its line names the token as a possible cause. Row 49 is settled for the engineer's fine-grained PAT. Other classes stay unverified, and REFERENCES.md names the class that was tested.
- **A byte bound on the lookup's output.** The 10 s `gh` and 5 s `jq` caps already bound how long a large reply can take, and a cap kill fails closed. A byte limit would need a value that no vendor or protocol documents. REFERENCES.md states that outsiders can cause the truncated and timeout stops on a public repository.
- **Choosing which reviews the 10-entry display shows.** The display keeps first-seen order. Each concern message names its PR, and the engineer can read that PR's reviews on GitHub.
- **Keying SKILL.md line 58 to the note constant's exact text.** A branch name reaches stderr only inside JSON quotes. A false match would only drop a re-run suggestion, which is fail-safe.
- **Superseded: a length cap on branch names echoed in stderr.** The implementation caps every echoed branch name at `REVIEW_PR_STACK_TEXT_ECHO_LIMIT` (80 characters), escaped, alongside the document's title and root-branch caps.
- **A per-suite test that the generated shim compiles and answers.** The double's import runs at the top of every shim, so a broken double fails every case in the suite at once.
- **`pr list` rows in `test_assert_gh_calls_are_read_only.py`.** The guard's code does not change, and the suites exercise its per-suite `allowed_pr_subcommands` mechanism (row 25).
- **Naming the PR, the destination branch and the actor in the retarget route.** The fixed text "retarget the PR that targets it" leaves a deep-stack reader to infer which PR, onto which branch, and by whom. A message that interpolates the nearest-root ancestor's number and says "ask its author to retarget it onto the branch it merged into" is a copy follow-up with its own test.
- **A wall-time paragraph in acquire's usage text.** REFERENCES.md's acquire wall-time bullet is the home for acquire's budget, and it is updated there.
- **Running the suites under bash 3.2 in CI, or widening `test_no_bash4_constructs.py`'s pattern list.** CI runs only on Ubuntu, and the pattern file sits outside this plan's files. `${x,,}`, `local -n`, and empty-array expansion under `set -u` rest on Verification step 3's macOS runs.
- **A test tying usage-text wall times to the call-count test.** No usage-text wall time in this repo is test-pinned today.
- **The `/review-pr` summaries in `docs/skills.md:14` and `docs/skills.md:76`.** They remain accurate.
- **`REVIEW_PR_REVIEWS_JQ_FILTER`'s empty-body drop for `existingReviews`.**
