# GH-1236 P1: review-pr post path

## Context

Goal: make `/review-pr` able to post a review that contains blocking findings, and make it handle a PR the operator authored, without weakening the review itself.

Ask: "Ok then let’s plan-it 1236" — scope narrowed to the tracker's P1 group via label "P1 post path (Recommended)". Further scope set via labels "Review with --comment", "Defer P1.3 + pin (Recommended)", and "Drop it for now (Recommended)".

Tracker #1236 collects `/review-pr` fixes; its P1 group ("Post path", from #1233 problem 3) is the tracker's own first step. Two defects block the skill today. First, Step 7 (`claude-skills/skills/review-pr/SKILL.md:156`) withholds the completion marker while "unresolved blockers remain", Step 8 recommends `request-changes` for any blocking finding, and `review-pr-post.sh` refuses to post without the marker — so a literal reading makes every review with a blocker unpostable. Second, the skill assumes someone else authored the PR (`SKILL.md:3,7,9`), and on the operator's own PR GitHub does not allow approval and the post script has no check before it consumes the marker, so a GitHub rejection surfaces as "whether the review posted is unknown". #1259 and #1261 merged after the tracker was written; none of P1.0–P1.6 is addressed by them (re-derived from current text this session).

Intended outcome: self-authored mode defined in the skill, the Step 7 marker clause reworded so blocking findings are postable, one Step 8 route per situation (normal, own PR), and pre-consume checks in `review-pr-post.sh` that refuse `request-changes` on the operator's own PR with a definitive message.

## Approach

Blocking findings become postable through a rewrite of Step 7's marker-withholding clause. After the rewrite it withholds the marker only while the review is unfinished or after the head has moved. A blocking finding no longer withholds it. A new self-authored mode works like this:
- `review-pr-acquire.sh` reports `selfAuthored`. A shared pure helper computes it by comparing the PR author's login with the login `gh api user` returns. When the lookup fails or returns an unusable login, the field is `null` and acquire carries on, because the field is advice only.
- Step 8 sends the operator's own PR to `comment`, tells the human why, and the body opens with a line saying how many blocking findings it holds, since the verdict cannot carry that signal.
- For `request-changes`, `review-pr-post.sh` works out authorship again before it consumes the marker. On the operator's own PR, or when it cannot tell, it refuses with a fixed message, and the marker stays armed, so the same body can still post as `comment` after the human confirms that.

This follows the skill's existing trust pattern (row 10): the skill branches on acquire's document as advice, and the script that acts derives the answer again and enforces it. No session-typed command changes, so P1.4 holds (row 24).

Alternatives set aside:
- **Session-typed lookup.** The session runs `gh api user --jq .login` and compares the result to step 1's `author.login`. This is lighter than an acquire change, but it breaks Step 1's contract that acquire derives everything later steps need from `gh` (row 9). It also leaves a case-insensitive comparison to model prose. anchors: row9
- **Stored gh login.** Read the login gh stored at `gh auth login` time (hosts.yml, via `gh config`/`gh auth status`). This costs no network call. But an environment `GH_TOKEN` takes precedence over stored credentials for authentication, so the stored login can name a different account from the one posting. [unverified: gh's environment help text, not re-read this session] anchors: row16
- **GraphQL `viewerDidAuthor` via `gh api graphql`.** One call, and GitHub answers the question itself. But `gh api graphql` sends a POST, which breaks acquire's rule against write-shaped `gh api` calls and fails conftest's read-only allowlist (row 28). This session also did not verify that the field exists. `gh api user` already has three uses in the repo. anchors: row13, row28
- **Prose-only route (no script check).** The "unknown whether posted" path stays reachable whenever the advice is wrong or the human picks `request-changes`. That fails P1.6. anchors: row5
- **Classify the failure after posting.** `gh` exit codes are generic (G2), and the marker is already gone by then. anchors: G2
- **Record `selfAuthored` in the completion marker.** This would change the marker format (`_lib.sh:729` reader, `marker.sh`, `tests/helpers.py`) for a value the post script must derive again anyway. A session-written claim does not bind the acting script (row 10). anchors: row10
- **Silently downgrade `request-changes` to `comment` in the script.** That changes the verdict the human approved.
- **Abort acquire when the lookup fails.** This would stop the whole skill, read-only reviews included, for any token that cannot read `GET /user` (row 14 is unverified for some token types). Row 11's abort rule covers fetches the skill needs for correctness, such as `authorAssociation`, which gates checkout trust. `selfAuthored` only picks a recommended verb, and the post script enforces the rule itself. anchors: row11, row14
- **Separate authorship lookups placed after the `headRefOid` check.** Two extra GETs between the head check and `unlink` would widen the window in which a push lands a review on code the session did not read, and nothing pins the review to a commit (row 3). anchors: row3, row29

No decision here is left open for the engineer.

### Assumption ledger

**Root:** `/review-pr` cannot post a review that holds a blocking finding. Step 7 withholds the completion marker "while unresolved blockers remain" (`SKILL.md:156`), and `review-pr-post.sh` refuses to post without that marker (`review-pr-post.sh:72-75`). On the operator's own PR, the skill recommends `request-changes` (`SKILL.md:160`). The post script then consumes the marker before a post that GitHub rejects, and the rejection reads as "whether the review posted is unknown" (`review-pr-post.sh:165`, `:195-197`).

**Givens:**
- G1. A `gh pr review` POST has no idempotency key. The marker is therefore consumed before the post, and any refusal that should leave the marker armed must run before `unlink`. Reason: GitHub's review API imposes it. [verified: `review-pr-post.sh:158-168` states and implements it; GitHub's docs were not re-read this session]
- G2. `gh` exit statuses do not distinguish an HTTP rejection from a network failure unless stderr is parsed. Reason: the gh CLI imposes it. [verified: `claude-skills/skills/review-pr/REFERENCES.md:59-61`]

**Mechanisms:**
- **M1. Shared helper.** Add `review_pr_authorship AUTHOR_LOGIN USER_JSON` to `_review-pr-lib.sh`. It prints one word and returns 0:
  - `unknown` when USER_JSON is not a JSON object, its `.login` is not a JSON string, or that string is not entirely letters, digits, `_` and `-`. The class is spelled out, not ranged, like `_REVIEW_PR_HEX_CHAR_REGEX`. `unknown` takes precedence over the author's value.
  - `self` when AUTHOR_LOGIN has that shape and the two logins are equal ignoring case. `_lib_case_insensitive_ne` returns 0 when they differ, so the test is spelled `if ! _lib_case_insensitive_ne …`; a bare call returning 1 would abort a `set -e` caller.
  - `other` in every remaining case, including an empty or non-login-shaped author (bot logins carry `/`).

  It reads USER_JSON through `printf '%s' … | _lib_jq`, as `review_pr_rest_changed_files` does. The extraction keeps a trailing newline in place (the `printf x` sentinel idiom, `review-pr-post.sh:130`), so `"operator\n"` reads `unknown`, not `operator`; AUTHOR_LOGIN gets the same check. Header updates: the first line names `review-pr-post.sh` as a sourcing script, the `_lib.sh` dependency list gains `_lib_case_insensitive_ne`, and a Provides line is added. Prescribed comment, one fact per line: `# Prints self when AUTHOR_LOGIN equals USER_JSON's .login (gh api user's response) ignoring case, else other.` and `# Prints unknown when USER_JSON is not a JSON object or its .login is not login-shaped; an empty or non-login-shaped AUTHOR_LOGIN is other.` anchors: row13, row17, row18, row19
- **M2. acquire.** In `review-pr-acquire.sh`, after the `author_association` block (`:177-190`):
  - Run `_lib_gh "$GH_PR_REST_TIMEOUT_SECONDS" api user 2>/dev/null` on the ambient token, with no `--jq`.
  - Take the author from `printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.author.login // empty'`, then call M1.
  - `self` gives `true` and `other` gives `false`. A failed call (any non-zero status, timeouts included) or `unknown` gives `null`, and acquire carries on. gh's stderr is discarded, and neither the `/user` payload nor the operator's login reaches the document, stderr, or any file.
  - Add `--argjson selfAuthored` to the assembly (`:322-341`), placing `selfAuthored` after `authorAssociation`.
  - Add one usage-text bullet: `selfAuthored` is `true`, `false`, or `null` when the account `gh` runs as could not be read.
  - Prescribed comments, one per line: `# The ambient token is the account review-pr-post.sh posts as, so this lookup never uses CI_CHECKS_GH_TOKEN.` and `# A failed lookup yields null rather than aborting, because review-pr-post.sh re-derives authorship before it posts.`

  anchors: row9, row10, row11, row12, row14, row16, row21
- **M3. post script.** In `review-pr-post.sh`:
  - Source `_review-pr-lib.sh` after `_lib.sh` (`:47`).
  - For `request-changes` only, run `_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" api user 2>/dev/null` before the existing `headRefOid` fetch (`:151-156`). Test its exit status explicitly (`if ! USER_JSON=$(…); then … exit 2`), not with the `|| X=""` idiom at `:152`, because M1 reads an empty author as `other` and the idiom would fail open.
  - Widen the existing fetch to `--json headRefOid,author` with no `--jq`, and parse both fields locally with `_lib_jq` (`.headRefOid`, `.author.login // empty`). One call keeps the head recheck adjacent to the `unlink` at `:165`, and `comment` gains no new call. The existing head-mismatch and empty-head refusals are unchanged.
  - For `request-changes`, call M1 after the head check. `other` proceeds, including an empty author on a successful fetch.
  - `self` exits 2. The message says the PR was authored by the account gh posts as, that `/review-pr` posts only a comment review on the operator's own PR, that nothing was posted, that the completion marker was not consumed, and that the same body can post with the `comment` verb only after the human confirms that exact body for `comment`.
  - A failed `api user` call or `unknown` exits 2. The message says the script could not tell whether the account gh posts as authored the PR, that nothing was posted, and that the marker was not consumed.
  - Both messages are fixed text. They name no login and echo no gh output. They must not contain "approve" (row 23), "unknown", or "whether the review posted" (row 27).
  - `comment` makes no `api user` call and ignores the fetched author.
  - The usage text gains one sentence: for `request-changes`, the script also checks that the PR's author is not the account gh posts as, and a match or a failed lookup refuses before the marker is consumed. `(no gh call)` at `:22` becomes `(no gh pr review call)`.
  - Prescribed comment: `# The operator's own PR posts only as comment; refusing here keeps the marker armed for that post.`

  anchors: root, G1, row5, row10, row16, row22, row23, row25, row27, row29
- **M4. SKILL.md wording.** In-place edits at `SKILL.md:3, 7, 9, 31, 137, 156, 160, 186`, using the wording below. The net line change is zero: each edited paragraph is already a single line, and no list item is added. anchors: root, row1, row2, row5, row7, row8, row24
- **M5. Docs.** Edit `REFERENCES.md` (`:10`, one bullet in its Step 1 section, and the post-gate recovery bullets near `:553-565`), `docs/scripts.md:171`, `docs/skills.md:14` and `:76`, and `CHANGELOG.md` (one `### Changed` bullet under `[Unreleased]`). The post check list stays in the usage text only. anchors: row25, row26
- **M6. Tests.** A matrix for the lib helper, a shim branch plus tests for acquire, and shim branches plus one test per branch for the post script (Critical files has the details). anchors: row21, row22, row23

**Rows:**
1. Scope is #1236's P1 group. Rows 2–4 narrow it. [engineer-verified: "P1 post path (Recommended)"]
2. On the operator's own PR, the post stays a `gh pr review --comment` review, not a `gh pr comment` conversation comment. This label is the later, controlling answer, and it overrides the tracker's quoted "allowing comments not GitHub review". The existing `comment` verb already posts this shape, so no post verb is added. [engineer-verified: "Review with --comment"]
3. A moved head keeps today's abort-and-rerun behavior. There is no stale-post route and no `commit_id` pin. [engineer-verified: "Defer P1.3 + pin (Recommended)"]
4. There is no review-body length precheck. [engineer-verified: "Drop it for now (Recommended)"]
5. On the operator's own PR, the only post-path difference is that `request-changes` is refused before the marker is consumed. `comment` posts as it does today. [unverified: this is the session's own option description under row 2's label ("only forbid request-changes; no new post verb"), not the engineer's words. It matches #1236 P1.6's "refuse `request-changes` on an own PR with a clear message", read from the copy named in row 20]
6. GitHub rejects a `REQUEST_CHANGES` review from the PR's author. [unverified: reported by #1233 problem 3c and marked unverified in #1236. Step 3 evidence found that the REST create-review docs are silent on it] Nothing depends on this row: the refusal follows from row 5, and no message or durable text asserts GitHub's behavior.
7. Nothing defines "unresolved" in Step 7's clause, and elsewhere the skill treats blocking findings as ordinary postable content. [verified: `SKILL.md:156`, `:104`, `:160`]
8. Step 7's list item 3 already defines when a claim is finished: resolved with evidence, dropped, or left open with a note on what would settle it. The question tier is reserved for claims that need a person, a deployed system, or a source the session cannot read. The reworded clause reuses this test rather than defining a new one. [verified: `SKILL.md:110`]
9. Step 1's contract is that acquire self-derives everything the later steps need from `gh` in one call, so the model does not type out `gh` calls itself. [verified: `SKILL.md:19`; `review-pr-acquire.sh:2-4`]
10. The skill's precedent for a trust signal: the skill branches on acquire's document as advice, and the script that acts derives the value again and enforces it. [verified: `SKILL.md:43`]
11. acquire aborts when a fetch the skill branches on fails. It degrades only for context that nothing branches on. [verified: `review-pr-acquire.sh:119`; `:177-190` aborts on the `author_association` fetch] `selfAuthored` is a deliberate exception: the skill branches on it only to pick a recommended verb, and the post script re-derives and enforces it (M3), so M2 degrades it to `null` instead of aborting.
12. acquire's metadata call already fetches `author`. [verified: `review-pr-acquire.sh:100`; the shim models it at `test_review_pr_acquire.py:181`]
13. `gh api user` reading `.login` is the repo's established operator-identity lookup. It appears at three sites, and the grep found no other lookup shape. [verified: Grep this session: `claude-skills/skills/memory-store-audit/SKILL.md:126`, `evals/review_bench/mine_pr_comments.py:213-222` (login shape `[A-Za-z0-9_-]+` at `:74`), `claude-skills/skills/tests/test_skills.py:945`]
14. `GET /user` succeeds under every token type an operator runs `/review-pr` with: gh's OAuth token, a classic PAT, or a fine-grained PAT. [unverified; this operator's token verified: `gh api user --jq .login` printed one login this session] GitHub App installation tokens and Actions `GITHUB_TOKEN` are suspected to get 403 [unverified]. Not load-bearing for acquire, which degrades to `null` (M2). It is load-bearing for the post script: under a token that cannot read `GET /user`, every `request-changes` refuses (row 27), while `comment` is unaffected.
15. `gh api user` and `gh pr view -R <owner>/<repo>` resolve the same GitHub host. [unverified] If they differ, the `/user` call most likely fails (401/404) rather than returning a second host's login. acquire then reports `null`, and the post script refuses `request-changes` (row 27). A false `self` would need one login to name different people on two hosts, and its effect is comment-only, which is the safe direction.
16. The ambient token is the account `review-pr-post.sh` posts as. `CI_CHECKS_GH_TOKEN` reaches only acquire's check-status call. [verified: `review-pr-acquire.sh:139-145`; `review-pr-post.sh:185,188` pass no token override] The existing override tests assert that every acquire call other than the check-status call runs on the ambient token. [verified: `test_review_pr_acquire.py:803-809`]
17. GitHub compares logins case-insensitively. [verified for owner slugs, which are logins: `_lib.sh:770-775`] `_lib_case_insensitive_ne` (`_lib.sh:776-787`) is the existing comparison.
18. Every `claude/.claude/**/*.sh` file must avoid bash-4-only constructs, so the comparison uses `nocasematch` through `_lib_case_insensitive_ne`, never `${var,,}`. [verified: `claude/.claude/scripts/tests/test_no_bash4_constructs.py:40`]
19. `_review-pr-lib.sh` conventions: functions take their input as arguments, print their result, return 0 or 1, and make no `gh` call or filesystem access. A three-way result prints as a word. Character classes are spelled out, not ranged. [verified: `_review-pr-lib.sh:37-41`, `:125-126`, `review_pr_audit_verdict` `:169-185`, `review_pr_checks_token_source` `:262-268`]
20. The tracker text this plan cites was read from `/tmp/review-pr-master-issue.md`, a local copy of #1236's body. [verified: `diff` against `gh issue view 1236 --json body` this session differs only by a trailing newline]
21. acquire's tests require every `gh api` call to match `_KNOWN_API_ENDPOINT`, with no tokens beyond an optional `--paginate --jq <filter>`. Their shim answers any endpoint it does not list with the pulls REST payload. [verified: `conftest.py:303-336`; `test_review_pr_acquire.py:50-52`, `:212-270`, `:337`] So acquire calls a bare `gh api user` and parses it locally, and the shim needs a `user` branch ahead of that fallback.
22. The post tests' shim prints `pr_view_head_ref_oid` for every `gh pr view` call, and it fires its swap/remove side effects on every `gh pr view` call. [verified: `test_review_pr_post.py:159-170`] M3 keeps one `pr view` call, so the side effects stay single-fire, but its output becomes a JSON object carrying `headRefOid` and `author`, parsed by the script's real `_lib_jq`.
23. `test_approve_flag_absent_from_code` fails on any non-comment line that contains "approve" in any letter case. The two-invocation count matches only `pr review` calls, so it stays 2. [verified: `test_review_pr_post.py:240-251`]
24. The redaction hook inspects only typed Bash command text. This plan adds no session-typed command, and both post invocations keep the shape `review-pr-post.sh <verb> <owner>/<repo>#<number>`. [verified: Step 3 evidence for `deny-private-project-refs.sh:244-246`; `SKILL.md:173,177`]
25. The post script's usage text is the single home of its verification list. [verified: `docs/scripts.md:179`; `REFERENCES.md:548-549`]
26. CHANGELOG.md records notable changes, and recent `/review-pr` behavior changes have entries. [verified: `CHANGELOG.md:3`, `:22-30`]
27. A failed `api user` call or an `unknown` result refuses `request-changes` in the post script rather than letting it proceed. Failing open would reproduce the original defect (a definitive GitHub rejection reported as "whether the review posted is unknown") whenever the lookup fails on the operator's own PR. The cost is that a token which can never read `GET /user` cannot post `request-changes` at all (row 14); `comment` still posts. The session's default, not an engineer decision. The refusal messages exclude "unknown" and "whether the review posted" because `SKILL.md:188` keys its never-re-post rule on that text, and every refusal and post failure exits 2. [verified: `SKILL.md:188`; `review-pr-post.sh:195-197`]
28. acquire makes no write-shaped `gh api` call, and conftest's allowlist accepts a bare endpoint or `--paginate --jq <filter>` only. [verified: ciso-reviewer read of `review-pr-acquire.sh:5-6` and `conftest.py:328-335` this round]
29. Today the `headRefOid` recheck and the `unlink` are milliseconds apart, with no network call between them. [verified: `review-pr-post.sh:151-165`]

### Prescribed SKILL.md wording

This is the target text, revised per this plan's `/skill-review` pass. Adjust it only as far as `/code-review`'s `/skill-review` requires, and keep the file at 200 lines or fewer. Every edit replaces or extends text within one existing line.

- `:3` becomes: `description: "Review a PR, yours or someone else's: audit for passive-execution risk before checkout, run /plan-review (only if a plan is linked) and /code-review, both under a no-fix/no-marker override, then post only on explicit approval."` The description already lacks `TRIGGER when:` / `DO NOT TRIGGER when:` blocks; that predates this plan and stays as is.
- `:7`: replace "Review a pull request someone else authored:" with "Review a pull request, whether authored by someone else or by the operator (the account `gh` runs as):". Keep the rest of the line. Then, after "This is the reviewer-side mirror of `/respond-pr`.", append: "**Self-authored mode** applies when step 1's document carries `selfAuthored: true` (advisory; the post script re-checks). It changes only step 8's post route and the body's opening line; every other step runs as written, with the same reviewers and depth."
- `:9`: replace "**This is code you do not own — report findings, change nothing, write no completion marker, edit no PR body.**" with "**In self-authored mode too, this is code you do not own: report findings, change nothing, write no completion marker, edit no PR body.**"
- `:31` becomes: "- `prIdentity`, `authorAssociation`, and `selfAuthored` (`true` when the operator authored the PR, `null` when that could not be read)."
- `:131`: append to the end of the line: "**When `selfAuthored` is `true`**, open the body, right after the prefix, with one line stating how many blocking findings it holds; omit that line when there are none."
- `:137` becomes: "- Use no review-process words: step numbers, agent names, \"consult\", \"unverified\", \"self-authored\"."
- `:156` becomes: "**Do not write this if:** item 3 of the list above left a claim open that this session could still settle from the diff file, base objects, or worktree, or the state just synthesized is not the state currently reviewed (`headRefOid` moved). A blocking finding is the review's result and never withholds the marker. Skipping it here just means step 8's post stays gated — say so explicitly."
- `:160`: replace "with an explicit recommendation: any blocking finding → `request-changes`; findings with no blockers → `comment`; needs-discussion → `comment`." with "with an explicit recommendation: when `selfAuthored` is `true`, `comment` whatever the findings, telling the human it is their own PR and `/review-pr` posts it as a comment review only; otherwise any blocking finding → `request-changes`, and anything else → `comment`." Keep the rest of the line, including the "**Never attempt to construct an `--approve` invocation**" sentence verbatim.
- `:186` becomes: "For `request-changes`, the post script also refuses, leaving the marker in place, when the operator authored the PR or when it cannot tell. That refusal is definitive, because nothing was posted. Tell the human. Post with `comment` instead only after they confirm that exact body for `comment`, and do not re-run `request-changes`. The script consumes the completion marker before the post call, success or failure, so a retry cannot double-post."

## Critical files

There are two sequential `code-writer` dispatches, and both ship in one PR. B describes A's field name and refusal behavior, so B runs after A. Neither half is meant to merge alone.

**Dispatch A (scripts and tests).** Verification steps 1–3.
- `claude/.claude/scripts/_review-pr-lib.sh`: M1 and its header lines.
- `claude/.claude/scripts/review-pr-acquire.sh`: M2.
- `claude/.claude/scripts/review-pr-post.sh`: M3.
- `claude/.claude/scripts/tests/test_review_pr_lib.py`: a `TestAuthorship` matrix. Every case exits 0.
  - `self`: logins equal, including when only the letter case differs.
  - `other`: different login, empty author, an author containing `/`, and an author with an embedded or trailing newline.
  - `unknown`: USER_JSON that is not JSON, is `[]` or `{}`, or has a `.login` that is null, a number, a boolean, an object, an array, empty, contains a space, or has an embedded or trailing newline.
  - Precedence: an `unknown` USER_JSON with an empty or odd author prints `unknown`.
- `claude/.claude/scripts/tests/test_review_pr_acquire.py`:
  - `_KNOWN_API_ENDPOINT` also fully matches `user`.
  - The shim gains `operator_login="operator"`, `api_user_exit_status=0`, `api_user_stderr=""`, and `pr_author_login="someone"`, which replaces the hard-coded value at `:181`. A `user` branch goes ahead of the REST fallback.
  - New tests:
    - The default document has `selfAuthored: false`.
    - `operator_login="SOMEONE"` gives `true`.
    - `api_user_exit_status` parametrized over 1 and 124 gives `selfAuthored: null` and exit 0.
    - An operator login that is not login-shaped gives `null` and exit 0.
    - A PR author that is not login-shaped (`app/some-bot`) gives `false` and exit 0.
    - A secret-shaped `api_user_stderr` and `/user` body field are absent from stdout, stderr and the context file, as `TestGhFailureNeverBypassesTheScrub` does for the REST call.
    - Exactly one `api user` call is logged, under the ambient credentials (`_credentials_by_call_kind`).
- `claude/.claude/scripts/tests/test_review_pr_post.py`:
  - The shim gains `pr_author_login="someone"`, `operator_login="operator"`, `api_user_exit_status=0`, `api_user_body=None` (raw override), and `api_user_stderr=""`.
  - `pr view` prints a gh-shaped JSON object `{"headRefOid": …, "author": {"login": …}}`, so the script's own `_lib_jq` extraction runs in every test. `api user` prints `{"login": …}` unless `api_user_body` overrides it. Existing `pr_view_head_ref_oid` options keep working.
  - New tests. Each carries a one-line GH-1236 comment naming the defect it pins.
    - `request-changes` with author equal to operator (letter case differing): exits 2, stderr carries the own-PR phrase and the fresh-confirmation condition and not the could-not-tell phrase, no `pr review` call is made, and the marker still exists.
    - `request-changes` with `api user` failing (exit 1 and 124), and with `api user` exiting 0 on `{}`, a non-object, and `{"login": "bad login"}`: exits 2, stderr carries the could-not-tell phrase and not the own-PR phrase, no `pr review` call, and the marker still exists.
    - Every refusal's stderr excludes "approve", "unknown" and "whether the review posted", and excludes a secret-shaped `api_user_stderr`.
    - Refuse, then recover: a self-refused `request-changes` followed by `comment` in the same session posts exactly once, and the posted bytes equal the body file.
    - `comment` with author equal to operator and `api_user_exit_status=1`: posts once with `--comment`, exit 0, and the call log holds no `api user` call and exactly one `pr view` call.
    - `request-changes` on someone else's PR: the `api user` call precedes the `pr view` call, which precedes the `pr review` call. The new calls carry no method, field or input flag.
    - `request-changes` with an empty author on a successful fetch: proceeds and posts once.

**Dispatch B (skill and docs).** Verification steps 1, 4, 5 and 7.
- `claude-skills/skills/review-pr/SKILL.md`: M4.
- `claude-skills/skills/review-pr/REFERENCES.md`:
  - Line 10's call list gains "a `gh api user` call".
  - Add one bullet in the Step 1 section: "**`selfAuthored` comes from a `gh api user` call on the ambient token.** That token is the account `review-pr-post.sh` posts as, so the lookup never uses `CI_CHECKS_GH_TOKEN`. A failed lookup yields `null` rather than aborting. `review_pr_authorship` (`_review-pr-lib.sh`) owns the comparison. `review-pr-post.sh` repeats it for `request-changes` before it consumes the marker, so the document's value is advisory."
  - In the post-gate bullets near `:553-565`, add that the own-PR or could-not-tell refusal leaves the marker armed, so posting the same body as `comment` after the human confirms it is the recovery for that refusal only. The hand-post or fresh-run recovery stays for an unknown post outcome.
- `docs/scripts.md:171`: the acquire entry's fetch list gains "whether the account `gh` runs as authored the PR".
- `docs/skills.md:14` and `:76`: replace "a pull request the operator did not author" (or its wording there) with wording matching the new `SKILL.md:3` description, "a PR, yours or someone else's".
- `CHANGELOG.md`: one `### Changed` bullet under `[Unreleased]`. Do not edit earlier entries. It covers four points:
  - A blocking finding no longer withholds the marker. It is withheld only while a claim the session could still settle is open, or after the head moved.
  - acquire's new `selfAuthored` field, which is `null` when the lookup fails.
  - On the operator's own PR, step 8 routes to `comment` and the body opens with its blocking-finding count.
  - The post script refuses `request-changes` before consuming the marker on the operator's own PR or when it cannot tell.

**Reuse:**
- `_lib_gh` and `_lib_jq`.
- `_lib_case_insensitive_ne` (`_lib.sh:776`).
- acquire's `GH_PR_REST_TIMEOUT_SECONDS` (`:177`) and the post script's existing `GH_PR_VIEW_TIMEOUT_SECONDS` (`:151`), so no numeric literal is added.
- The `printf x` sentinel idiom (`review-pr-post.sh:130`) and the spelled-out character-class pattern (`_review-pr-lib.sh:125-126`).
- conftest's `_assert_gh_calls_are_read_only`, unchanged.
- `_credentials_by_call_kind` (`test_review_pr_acquire.py:803`) and `TestGhFailureNeverBypassesTheScrub` (`test_review_pr_acquire.py:1226-1241`) as the leak-test precedent.

**Not touched:** `conftest.py`, `tests/helpers.py`, `marker.sh`, `hooks/_lib.sh`, `require-respond-pr.sh`, `deny-private-project-refs.sh`, `docs/hooks.md`, and `settings.json`.

## Verification

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. It maps `review-pr/SKILL.md` to the scripts tests (`select-tests.py:166`).
2. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` is clean.
3. `.venv/bin/ruff check claude/.claude/ claude-skills/` is clean.
4. `wc -l claude-skills/skills/review-pr/SKILL.md` prints 200 or less. `check-skill-length.sh` denies the commit otherwise.
5. In `claude-skills/skills/review-pr/SKILL.md`:
   - `grep -n 'unresolved blockers'` prints nothing.
   - `grep -c 'selfAuthored'` prints at least 4.
   - `grep -c 'Never attempt to construct an `--approve` invocation'` prints 1.

   The Step 7 and Step 8 prose fix has no test of its own beyond these greps.
6. Done this session for this operator's token only: `gh api user --jq .login` printed one login. Row 14's other token types stay unverified.
7. `/code-review` runs `/skill-review` on the SKILL.md diff. `require-skill-review.sh` enforces this.
8. Optional, and the engineer's call because it writes to GitHub: on one of the operator's own open PRs, run `/review-pr` to step 8. Run the `request-changes` form once and confirm the own-PR refusal leaves the marker in place. Then post with `comment`. If the engineer also wants row 6 settled, a throwaway own PR with a hand-run `request-changes` review records GitHub's actual response.

## Out of scope

- **Raising `review-pr/SKILL.md`'s 200-line cap:** the file is exactly 200 lines, and the length ratchet denies a staged file over 200 lines that grew. The cap lives in this repo (`limit_for`, `check-skill-length.sh:111-120`), but `docs/skills.md:146` bars raising it for cap pressure alone, so M4 keeps the net line change at zero instead. [verified: Read of SKILL.md; `_lib.sh:2381-2384`] Every later tracker item that edits `SKILL.md` meets the same cap; line relief by deleting restated text is a tracker note, not this change.
- **P1.2's plain-comment post verb:** row 2.
- **P1.3's stale-post route and P4's `commit_id` pin:** row 3.
- **P1.6's review-body length precheck:** row 4.
- **Classifying other failed posts as definitive:** a GitHub rejection other than own-PR `request-changes` still prints "unknown" (G2).
- **A distinct exit status for the refusal:** every refusal exits 2 today. Telling the refusal apart from an unknown post outcome rests on message text (row 27), which the new tests pin.
- **Consolidating the 10-second GET timeout declarations:** there are six across the four review-pr scripts (`checkout:241`, `diff:85,161`, `post:151`, `acquire:99,177`). Merging them into one lib constant is a sibling refactor beyond this change.
- **`_lib_gh` running uncapped without `timeout`/`gtimeout`:** pre-existing for the post script's head fetch. M3 adds one more such call, the `api user` lookup, and a hang there happens before `unlink`, so the marker stays armed.
- **A self-marker carve-out in the standing override's "write no completion marker":** `SKILL.md:9`'s first sentence already limits the override to skills invoked from steps 3 and 5, and Step 7 writes this skill's own marker.
- **The rest of the tracker:** P2, P3, and the remaining P4 items.

## Effort

The review surface is 11 files in two domains: scripts with their tests, and skill and doc prose. Risk is concentrated in the post script: the order of the `api user` lookup, the widened head fetch and the `unlink`, and the explicit exit-status handling that keeps a failed lookup from reading as `other`.
