# Plan: review-pr finding hygiene, claim resolution, and base reads

## Context

Goal: make `/review-pr` produce author-facing reviews that resolve checkable claims before presenting, can read the base branch in diff-only mode, and follow posted-text hygiene rules.

Why now: two review sessions produced feedback. Their reviews shipped bare "unverified" labels, answerable Questions, over-tiered findings, and review-process wording in the posted body.

Intended outcome: `review-pr/SKILL.md` and the scripts it relies on change so those failures do not recur.

Ask: "I have feedback for the review-pr skill and this feedback might apply to the code-review skill as well. Please use the architect as first pass for AskUserQuestion while gathering information and clarification." Quoted from the engineer's session-B feedback: "For #9 can you check this yourself? Same with your questions - I feel like you should be able to answer these yourself." / "Make the necessary edits to your review, then have the architect review it, then present to me for final approval." Widened in Step 4 by: "One PR. We're working with cooperative agents and non-hostile repos. Please keep that in mind."

Provenance note: the eleven pitfalls and the two process notes were relayed by an agent from the engineer's feedback, not typed by the engineer. The session-B suggestions are agent-authored and unendorsed.

## Approach

`review-pr/SKILL.md` step 7 will:
- Resolve every answerable claim before tiering. Sources: the diff, base files read by object ID, bounded CI reads, and the issue the PR title or body links. Issue and CI text may add a finding or keep a claim open, and never closes one about code (row 2).
- Check each finding against the base for pre-existing and sibling-precedent status.
- Apply a posted-text hygiene list.
- Let the `plan-architect` consult's corrections change the drafted body only after the session verifies them, one Edit per finding, with a consult after every edit round. A finding derived from AUDIT_FINDING, and any blocker, is protected throughout step 7: no step drops, downgrades, or moves it out of the body.

The scripts gain base reads:
- `review-pr-diff.sh` fetches the base commit and prints it as a full-hex line. If that fetch fails it degrades with a fixed `BASE_UNAVAILABLE` token instead of stopping.
- `review-pr-checkout.sh` also prints its base commit as a full-hex line.

`review-pr-checkout.sh` also drops its blanket refusal of a non-default base. Instead it runs a fail-closed audit of every path where the PR head differs from the default branch. A trusted stacked PR whose delta audits clean gets a real worktree.

Alternatives weighed:
- **Base worktree for diff-only PRs.** This answers the engineer's "why would we write nothing to disk? Wouldn't that be easier?". An untrusted PR's head stays off disk, because the audit exists to keep PR-supplied `CLAUDE.md`, settings, and hooks out of any tree the harness reads. For the base, `git show`/`git grep` against the object ID give the same read access as a worktree, with no worktree to create, name, clean up, or audit. The cost: each read is a Bash call that may prompt, where a worktree would allow prompt-free `Read`.
- **Base-side delta, audited before the PR-ref fetch.** Rejected. It misses a file that was on the base when the PR forked and was later removed from the base: the head tree still holds that file, but neither list names it.
- **Dropping the audit for stacked bases.** Rejected. The skill already audits a trusted author's own files, and the base delta comes from the same trust class.
- **Script-produced consult diff, two-dot delta hardening, and head reads by object ID.** All moved to Out of scope.

**Ledger**

```
Root: /review-pr's author-facing reviews ship claims the session could have checked (bare "unverified" labels, answerable Questions), tier pre-existing or precedent-following code as the PR's own defect, and leak review-process language. The skill never directs claim resolution against readable sources, diff-only mode has no base content to resolve against, and a trusted stacked PR is refused a checkout outright.
Givens: GitHub exposes the PR base commit as baseRefOid and serves `git fetch origin <sha>` for it — beyond reach: vendor behavior; review-pr-checkout.sh:229-242 and :376 already rely on both.
Givens: Bash calls to `git show`, `git grep`, `gh run`, and `gh issue` prompt unless the operator's own permission mode allows them — beyond reach: CLAUDE.md §Safety bars wildcards in permissions.allow, and an exact entry cannot match variable arguments.

Row 1 [assumption]: the engineer's stated environment [engineer-verified: "One PR. We're working with cooperative agents and non-hostile repos. Please keep that in mind."] — anchors: root
Row 2 [mechanism]: claim-resolution pass in step 7, before tiering — anchors: root — Every hedge in any finding is checked against the diff file, base content at the base commit, CI runs for headRefOid, and the linked issue. Linked-issue and CI text may add a finding or keep a claim open and never closes or downgrades a claim about code (live step 7 item 3). A claim that resolves becomes a finding citing its evidence, or is dropped. A protected finding (row 48) is never dropped by this pass; evidence against it goes beside it in step 8. A claim that stays open names what would settle it (CLAUDE.md §Working Style). The question tier keeps only what needs a person, a deployed system, or a source the session cannot read. A bare "unverified" never ships.
Row 3 [assumption]: the engineer wants checkable claims resolved by the session [engineer-verified: "For #9 can you check this yourself? Same with your questions - I feel like you should be able to answer these yourself."] — anchors: row2
Row 4 [assumption]: the engineer rejects a bare "unverified" label [engineer-verified: "What do you mean when you say "unverified" here? ... Can you check that?"] — anchors: row2
Row 5 [assumption]: the gh forms used here exist [verified: gh 2.101.0 man pages, not run — gh-run-list(1) :36-37 `--commit`, :52 `--json`, :56 `--limit`, :87 fields incl. databaseId, status, conclusion; gh-run-view(1) :56 `--log-failed`, :74 `-R`; gh-issue-view(1) :9 `{<number> | <url>}`, :48 fields incl. title, body, state] — anchors: row2
Row 6 [mechanism]: bounded CI reads — anchors: row2 — Commands:
  - `gh run list --commit <headRefOid> -R <owner>/<repo> --json databaseId,name,status,conclusion --limit 20`.
  - For a completed run whose conclusion is failure: `gh run view <databaseId> -R <owner>/<repo> --log-failed | tail -c 20000`.
  - To find a literal in that log: `gh run view <databaseId> -R <owner>/<repo> --log-failed | grep -n -F -e '<literal>' | head -n 40`, quoted per row 16.
  Each pipe hides gh's exit status, so empty output or a gh error line counts as a failed read. Any of these keeps the claim open: an in-progress run, an empty run list (external CI reports through commit statuses, so an empty list is not "no CI"), a failed read, or a list of exactly 20 runs (it may be truncated) against a "no failing run" claim. Logs are quoted by excerpt only. The 20000-byte bound sits under the Bash output truncation threshold [verified: _review-pr-lib.sh:78, which cites the 30,000-byte figure from subagent-delegation/REFERENCES.md; that file not reopened].
Row 7 [assumption]: a `gh run` read can fail under a fine-grained PAT the way statusCheckRollup does [unverified for gh run; the statusCheckRollup failure is verified: review-pr/REFERENCES.md:43-52] — anchors: row6
Row 8 [mechanism]: linked-issue read — anchors: row2 — The linked issue is a reference in the PR title or body that is a bare `#<n>`, this repo's own issue URL (host and `<owner>/<repo>` equal to the PR identity the step-2 script checked; any other URL stays unread and is named), or a key shaped like `[A-Z]{2,}-[0-9]+`. Read at most one, as data, for intent: a `#<n>` or own-repo URL wins over a key; among same-class references, the first in title-then-body order. A URL is never parsed for a key, so a key inside a tracker URL is unread and named. `#<n>` or that URL is read as `gh issue view <n> -R <owner>/<repo> --json title,body,state`, with a digits-only `<n>` and the owner/repo taken from the PR identity the step-2 script checked against origin. A key is read only from the non-GitHub tracker that the `CLAUDE.md` the session loaded at start names (never the PR's copy in the review worktree), or that the engineer names unprompted, and only after the engineer confirms the exact tracker and key, through a read-only MCP issue lookup found by role in the session's tool list. Only the bare number or key is passed, never a URL, which gh accepts and which could point the read at another repo or host [verified: gh-issue-view(1) :9]. Beyond that one read, nothing the PR names is fetched. A `CLAUDE.md` that declares GitHub as the tracker neither prompts nor reads a key. With no declared tracker the review does not prompt on a bare key match and leaves it unread, which keeps shapes like `UTF-8` from prompting; with a declared tracker the engineer's confirmation sees the exact key and rejects a false match. With no lookup, a declined or unanswered confirmation, or a failed read, the review works from the PR title and body and keeps dependent claims open, naming the bare identifier. Step 3 follows the same source: `/plan-review` runs only on a plan artifact in the PR's diff or its linked GitHub issue, and any other plan source (a gist, an outside document, a non-GitHub issue) stays unread, with the skip stated in step 8 outside the body. Step 7's secret-scrub item also keeps content and paraphrase from a linked issue read outside GitHub out of the body, and a finding whose only support is that read goes to the step 8 notes instead of the body, while a finding with independent code evidence cites only the key. A protected finding (row 48) always has code evidence, so it stays in the body and never moves to step 8 on account of this rule. The read's content is not passed to step 5 subagents. The reason:  that tracker's audience can differ from the PR's.
Row 9 [assumption]: the acquire document carries no linked-issue field, only the title and body (the acquire script fetches `title`, per review-pr-acquire.sh:100) [verified: review-pr/REFERENCES.md:10] — anchors: row8
Row 10 [mechanism]: scrub widening — anchors: row2 — Step 7's secret scrub covers CI logs, linked-issue text, and base content, beside the diff and PR text. The step-1 data list (SKILL.md:9) gains CI logs and base-branch content.
Row 11 [assumption]: the findings-body scan checks vendor-fixed credential shapes only, so it does not backstop row 10 for derived secrets; a full hex SHA in a citation does not trip it [verified: review-pr-scan-findings-body.sh:55] — anchors: row10
Row 12 [mechanism]: reviewer subagents keep their current inputs and are told to mark any claim they could not check, with what would settle it; the main session resolves those claims — anchors: row2 — plan-architect holds no Bash [verified: its own tool grant], and Step 3 research reports that many reviewer agents lack Bash.
Row 13 [mechanism]: base reads by object ID — anchors: row2 — review-pr-diff.sh reads baseRefOid in its single first `gh pr view --json headRefOid,baseRefOid` call and validates it with `review_pr_is_full_hex_oid`. It runs `git -C "$REPO_ROOT" -c core.hooksPath=/dev/null fetch origin "$BASE_REF_OID"` under a 30s cap and prints the OID as a full-hex stdout line after the diff path. review-pr-checkout.sh prints the base OID it already fetches as a full-hex line after its diff path. Over-powered check, against two lighter options: (a) the session runs `git fetch origin <oid>` itself from the acquire JSON — fails: the OID is model-copied and unvalidated, and the fetch has no hooksPath pin or cap; (b) `gh api repos/<owner>/<repo>/contents/<path>?ref=<oid>` per file — fails: one API call per file, base64 decoding, and no grep across the tree.
Row 14 [assumption]: the engineer chose a script fetch plus git show for base reads [engineer-verified: "Of these options I like script fetch and git show"] — anchors: row13
Row 15 [assumption]: `git show <rev>:<path>` prints a blob's plain contents, and `git grep` applies no textconv by default, so base reads run no configured driver from any tree [verified: git-show(1) 2.56 "For plain blobs, it shows the plain contents"; git-grep(1) "--no-textconv ... This is the default"; not run] — anchors: row13
Row 16 [mechanism]: prose quoting rule for base reads composed by the model — anchors: row13 — Every PR-derived string (path, pattern, log-filter literal) goes in single quotes as one argument, in fixed shapes: `git show '<baseRefOid>:<path>'` and `git grep -n -e '<pattern>' <baseRefOid> -- '<path>'`. A string containing `'`, a newline, or another control character is never passed to Bash, and the content it names is treated as unread. Fixed-shape alternatives, both heavier, not adopted: (a) a `review-pr-base-read.sh <path>` script — the path still reaches the script's argv through a model-written command line, so the quoting exposure moves rather than closes; (b) a Write-tool request file read by `git cat-file --batch` or `git grep -F -f` — this does close the exposure, but costs a Write and a scratch-file convention per read, which is out of proportion under row 1. Residual, named in REFERENCES.md Known gaps: the rule is prose, so a command that double-quotes a name would expand `$(...)`; the harness shows the full command before any call that is not allowlisted.
Row 17 [assumption]: inside single quotes the shell treats every character literally, and a single quote cannot occur inside them [unverified: POSIX Shell Command Language §2.2.2, from memory, not opened this session] — anchors: row16
Row 18 [mechanism]: review-pr-diff.sh degrades only the base — anchors: row13 — A missing, non-hex, or unfetchable baseRefOid still writes the diff and provenance and exits 0, prints only the diff path, and writes `review-pr-diff.sh: BASE_UNAVAILABLE <token>` to stderr. Tokens: missing, not-hex, fetch-failed, fetch-timed-out. No raw git output, which can carry remote URLs into a posted body. The base read is written `X=$(...) || X=""`. A missing headRefOid still exits 2. Reason: the diff-only path exists to keep every PR reviewable, and an operator whose gh works while git-over-SSH does not would otherwise lose it.
Row 19 [mechanism]: fail-closed head-side delta audit in review-pr-checkout.sh, only when base.ref differs from the default branch — anchors: root — Steps, after the fetched-SHA check and before the base fetch, diff file, and worktree:
  - Resolve MAIN_REPO_ROOT before this block (moved up from :360).
  - Validate with `git check-ref-format "refs/heads/<default>"`, not `--branch`. A rejection exits 2 with its own message, before any fetch.
  - Fetch with hooks disabled into `refs/review-pr/pr-<N>-default`, then resolve it once to DEFAULT_OID with `git rev-parse --verify --quiet "<ref>^{commit}"`.
  - Counting run first: `git -C "$MAIN_REPO_ROOT" diff --name-only -z --no-renames --no-ext-diff --no-textconv --ignore-submodules=none "$DEFAULT_OID...$FETCHED_SHA" -- | LC_ALL=C tr -cd '\000' | LC_ALL=C wc -c`. Its output passes through `review_pr_bare_count`, which trims leading and trailing whitespace and accepts only `^[0-9]+$`.
  - Name run second: the same git arguments piped to `review_pr_nul_names_to_json`.
  - Each git run is capped at 30s, and its status is read through pipefail. `review_pr_file_count_matches` checks the array length against the bare count.
  - A non-zero status, cap kill, non-integer count, or count mismatch exits 2 before the audit runs, with a message naming which check failed.
  - The audit runs through one script-local function shared with the PR-file audit (:296-314). The function takes its stop text and its no-verdict text as arguments, so the delta's no-verdict message never says "before any fetch of the PR's ref" or "the PR's file list" (:311).
  - A stop exits 3, naming review-pr-diff.sh, the base branch, and the matched paths, and never uses :306's "no refs/pull" wording. A clean result continues to the existing checkout.
  Over-powered check, against two lighter options: (a) keep the refusal and rely on row 13's base reads — fails row 20; (b) the base-side delta audited before the PR-ref fetch — fails on the reverted-base-file case in Approach.
Row 20 [assumption]: the engineer approved lifting the stacked-base refusal with the delta audit [engineer-verified: "Yes, lift it with the delta audit (Recommended)"], after earlier asking for a local worktree checkout [engineer-verified: "Why can't we actually check out the branch and make it into a worktree locally?"] — anchors: row19 — The session wrote the option description under the first label. The tag covers the label: lift the refusal, with the delta audit.
Row 21 [assumption]: my reading of row 1 — it keeps row 19 proportionate (reuse the audit and the exit-3 contract, no provenance or marker mode, no defense beyond the ref-name check and fail-closed status checks). It does not drop the audit, because the skill audits a trusted author's own files ("Never skip this reasoning on the strength of author standing") [verified: review-pr/SKILL.md:58; review-pr-checkout.sh:167-169] — anchors: row1
Row 22 [assumption]: the audit reads empty stdin as [] and [] as clean, so the delta must fail closed on tooling status, never on content [verified: audit-execution-surface.py:121; _review-pr-lib.sh:100] — anchors: row19
Row 23 [assumption]: `git diff A...B` lists changes from a common ancestor of A and B up to B [verified: git-diff(1) 2.56 :183-186]; `-z` prints names verbatim and NUL-terminated, while without it unusual names are quoted [verified: git-diff(1) :1718] — anchors: row19
Row 24 [assumption]: `jq -R -s` keeps NUL bytes, and `split("\u0000") | .[:-1]` recovers every name, including one holding a newline, with the trailing terminator's "" dropped [verified on jq 1.8.2 by both staff-platform-engineer and staff-sdet, per their round-1 findings files; other jq versions unverified] — anchors: row19 — The count check turns a jq that truncates at NUL into exit 2, not a clean verdict. No `--args` fallback: it hits ARG_MAX on a large delta.
Row 25 [assumption]: the PR-ref fetch before a delta stop lands only objects and a ref, with hooks disabled, and the ref is a residual like the existing refs/review-pr/pr-<N> [verified: review-pr-checkout.sh:319-323; review-pr/REFERENCES.md:316-324] — anchors: row19
Row 26 [assumption]: no PR that checks out today stops checking out. The delta runs only when base.ref differs from the default branch, and every such PR exits 3 today [verified: review-pr-checkout.sh:213-216]. The audit flags CLAUDE.md, settings, .gitattributes, and .gitmodules by path text [verified: audit-execution-surface.py:52-103] — anchors: row19
Row 27 [assumption]: a stacked PR's new exit 2 (fetch or diff failure) has no automatic diff-only fallback. SKILL.md, not script stderr, tells the human they may re-run or ask for diff-only, because exit-2 messages must never name review-pr-diff.sh [verified: test_review_pr_checkout.py:2202, :2257] — anchors: row19
Row 28 [assumption]: whether `git worktree add` honors submodule.recurse [unverified] — anchors: row19 — This applies to every checkout already; row 19 widens which PRs reach it but does not change it.
Row 29 [mechanism]: Step 6 says that on a stacked-base checkout, base-branch commits the review diff does not show can also change what the discovered command runs — anchors: row19
Row 30 [mechanism]: pre-existing and precedent check before tiering — anchors: root — A finding whose defect already exists at the base commit, and that the PR does not cause, activate, or newly reach, leaves the body for the step-8 notes. When base code in the same file or module uses the same pattern, the finding is tiered no higher than non-blocking and cites `<baseRefOid>:<path>:<line>`, unless causal reach applies. Neither move applies to a protected finding (row 48). It keeps its tier and its place in the body, and the base evidence goes beside it in step 8.
Row 31 [assumption]: the causal-reach wording mirrors /code-review's rule [verified: code-review/SKILL.md:38], restated in prose with a citation to `code-review/SKILL.md` § "Step 0.6 — Pre-judgment table check". No SCOPE anchor is added, because test_skills.py pins the exact anchor set [verified: claude-skills/skills/tests/test_skills.py:4566-4572] — anchors: row30
Row 32 [assumption]: over-tiering and pre-existing findings were among the engineer's corrections, as relayed by an agent rather than in the engineer's own words [unverified] — anchors: row30
Row 33 [mechanism]: step 7.1 merges findings that share one root cause into one finding naming every site — anchors: root — A merge that absorbs a protected finding (row 48) takes the highest tier among the merged findings and names the audit match.
Row 34 [mechanism]: posted-text hygiene list for the findings body — anchors: root — Observation first, then the requested change. Describe without judgment. Use the PR's own term for each concept. Omit failure modes a reader of the code already sees. No review-process words (no step numbers, agent names, "consult", or "unverified"). Exempt: the prefix, the trailer, the diff-only disclosure line, and the step-1 coverage statements. Non-actionable notes and a BASE_UNAVAILABLE statement go in step 8, outside the body.
Row 35 [assumption]: rows 30 and 34 derive from the relayed pitfalls; Session B's suggestions are agent-authored and unendorsed, adopted only where this ledger's reason holds on its own [unverified] — anchors: row34
Row 36 [mechanism]: consult on the drafted body, recorded through per-finding Edits — anchors: root — Order: Write the body to the fixed findings-body path, Read it (row 41), consult, then apply each correction as its own Edit on that file, one finding per Edit, never re-Writing the whole file after the consult. The transcript then holds the pre-consult Write, the consult dispatch, and every change as a visible old/new Edit. Rules:
  (a) A protected finding (row 48) keeps its tier and place whatever the consult says; step 8 shows the consult's objection beside it.
  (b) A correction is applied only when the session verifies it from a source it read itself; the consult's word alone never suffices.
  Cadence (row 49): any change to the body after a consult, including edits the human requests in step 8, gets another consult before the body is presented. The third consult since the last presentation is final. None of its corrections is applied; step 8 shows them as proposed edits, with the open disagreements named (row 39).
  Step 8 summarizes the Edits: each finding removed or downgraded, with its reason. Then the marker write and step 8.
  Two weaker records were considered: (i) cuts recalled from memory, with no per-finding Edit — fails: nothing in the transcript ties each cut to a visible change; (ii) SKILL.md:115's annotation-only consult — fails row 37. The heavier record (a script-produced diff) is out of scope by row 38.
Row 37 [assumption]: the engineer chose this consult role [engineer-verified: "Edits allowed, cuts disclosed (Recommended)"] — anchors: row36
Row 38 [assumption]: the engineer chose prose plus per-finding Edits over a script-produced diff [engineer-verified: "Prose + per-finding Edits (Recommended)"] — anchors: row36
Row 39 [assumption]: beyond the labels in rows 37, 38, and 49, these are session and reviewer proposals, not the engineer's words: the never-re-Write rule, the Read after Write, the step-8 summary, row 48's protected findings, rule (b), and the third-consult stop [unverified] — anchors: row36 — The stop exists because review after every round can loop when each consult proposes another change the session can verify. Counting from the last presentation keeps every edit the human requests under row 49's review. Showing the third consult's corrections unapplied keeps every presented body one the architect reviewed. The case against the snapshot script came from a model's second opinion.
Row 40 [assumption]: the engineer set the order edit → consult → present, including after step-8 feedback [engineer-verified: "Make the necessary edits to your review, then have the architect review it, then present to me for final approval."] — anchors: row36
Row 41 [assumption]: Edit fails unless the file was Read in the conversation [verified: docs/case-studies/hashline-edit-format.md:12, quoting the live Edit tool description "You must Read the file in this conversation before editing, or the call will fail"]; whether the session's own Write counts as that Read is [unverified] — anchors: row36 — Step 7 Reads the body after the Write and before the consult, so row 36 does not depend on it.
Row 42 [assumption]: Edit and Write share every hook matcher in the shipped settings, so an Edit on the findings-body file meets the same hooks the step-7 Write already passes [verified: claude/.claude/settings.json:348, :485] — anchors: row36
Row 43 [assumption]: residual — anchors: row36 — The record is the session's own transcript, and every edit is session-authored, since plan-architect holds no write tool [verified: its own tool grant]. For an honest mistake the backstops are marker.sh's body check, review-pr-post.sh's re-verification of the body hash, and the human's approval of the exact body [verified: review-pr/SKILL.md:129, :145-151].
Row 44 [mechanism]: pasteable fix only for a blocker whose review thread shows a failed earlier fix attempt — anchors: root — Drafted by the owning reviewer under `code-review/SKILL.md` § "Item ownership". Reviewed in the consult. Checked for same-file precedent and for what breaks if only half is applied. Stated as not run. Lighter options and why each fails: (a) the session drafts it — fails: an earlier fix already failed, which is the case the owner's checklist exists for; (b) prose only — fails: the thread shows prose did not suffice.
Row 45 [mechanism]: citations use PR-head line numbers (the worktree file in checkout mode; the hunk's `+` side in diff-only mode) or `<baseRefOid>:<path>:<line>`, never diff-file offsets — anchors: row2
Row 46 [assumption]: /code-review gets no edits [engineer-verified: "review-pr only (Recommended)"] — anchors: root
Row 47 [assumption]: SKILL.md is 200 lines against a 200-line cap, so an edit may not add a line. Paragraphs are single physical lines (:9, :96, :102, :115), so a sentence added to an existing paragraph costs no line, while a new paragraph costs two (text plus blank) [verified: Read of SKILL.md this session; claude/.claude/hooks/check-skill-length.sh:111-119] — anchors: root — The 167-line count and the :9, :96, :102, :115 refs are origin/main numbering; at the current head the file is 200 lines and those refs have moved. The earlier +23 projection and its 190-line target are superseded: the other text it counted is already in the 200 lines, and the 190 target was missed. The only rule now is the 200 cap.
Row 48 [mechanism]: protected findings, one step-7-level rule — anchors: root — A finding derived from an AUDIT_FINDING line, and any finding tiered blocker, is never dropped, downgraded, or moved out of the body by claim resolution (row 2), the pre-existing and precedent check (row 30), a root-cause merge (row 33), the consult (row 36), the question-tier filter (row 2), or the move of non-actionable notes to step 8 (row 34). When any of these finds evidence against a protected finding, the evidence goes beside it in step 8, and only the human may remove or downgrade it. Covers the case of an audit-flagged path that also exists at the base, where a pre-existing judgment would otherwise drop a mandatory finding the consult never sees (ciso-reviewer round-2 N1).
Row 49 [assumption]: the engineer chose a consult after every edit round [engineer-verified: "Re-consult after every edit round (Recommended)"] — anchors: row36 — This replaces the earlier "re-consult only after a round that changes a claim's substance" and agrees with row 40.
Row 50 [assumption]: count normalization and locale are required — anchors: row19 — `review_pr_file_count_matches` compares as strings [verified: _review-pr-lib.sh:117]. BSD `wc -c` left-pads its count [observed on macOS by ciso-reviewer and staff-platform-engineer, per their round-2 findings files; not run here]. BSD `tr` under a UTF-8 locale fails on invalid bytes, while `LC_ALL=C` counts them [observed by staff-platform-engineer, same file; not run here]. Linux CI uses GNU tools, so only the shim test in dispatch A catches padding off macOS.
Row 51 [assumption]: `jq -R` replaces invalid UTF-8 bytes with U+FFFD rather than failing, so the name count is kept and a `.claude/hooks/` prefix still matches [unverified] — anchors: row19 — Dispatch A's invalid-UTF-8 case expects exit 3. If CI's jq rejects the bytes instead (exit 2), dispatch A stops and reports rather than changing the expectation.
Row 52 [assumption]: the MCP issue read has no equivalent of gh's `-R` repo pin, so it trusts the operator's connector to point at the right project's workspace [unverified: no MCP issue tool was inspected] — anchors: row8 — The tracker is chosen from the main tree's `CLAUDE.md`, not the PR's copy in the review worktree (analogy: plan-it/SKILL.md Step 7, "see that project's `CLAUDE.md`, or ask the engineer if undocumented", which routes a plan out rather than reading an issue in), because a connected tool shows what the operator can read, not which tracker the repo uses. The PR author controls the key and so could pick a team's issue the repo does not use, so the engineer confirms the exact tracker and key before any non-GitHub read. A non-GitHub read's content or paraphrase stays out of the posted body, and a plan pass takes a plan only from the PR's diff or a GitHub issue. The cited Linear source describes a lookup as returning title, state, and URL, so a body or plan text from an MCP read is itself unverified. The `[A-Z]{2,}-[0-9]+` shape is the tracker-ID shape the redaction hook already uses [verified: docs/hooks.md:135]. The lookup is matched by role, never by tool name [verified: plugins/linear-formatting/skills/linear-formatting/SKILL.md:56-79].
Row 53 [assumption]: the engineer approved the architect's first design [engineer-verified: "Yes, this looks correct." — the reply to that design, which lacked the confirm-before-read step]. The confirm step was added after the security review, and the engineer chose "Confirm key with you (Recommended)" among three options for it [engineer-verified: that selected label]. The design text as a whole is session-authored — anchors: row52
```

## Critical files

There are three sequenced `code-writer` dispatches. Each lands as its own commit after `/code-review`, so `ciso-reviewer` sees dispatch A's audit change alone. From this worktree, venv tools are at `../../../.venv/bin/` (README.md:522, :546).

The plan file itself is `.claude/plans/review-pr-finding-hygiene.md`, committed at plan-it Step 7. No dispatch edits it.

**Dispatch A — stacked-base delta audit**
- `claude/.claude/scripts/_review-pr-lib.sh`:
  - Add `review_pr_is_full_hex_oid OID` (bash `=~ ^[0-9a-f]{40}([0-9a-f]{24})?$`).
  - Add `review_pr_nul_names_to_json`, which reads NUL-terminated names on stdin: `_lib_jq -R -s -c 'split("\u0000") | .[:-1]'`, returning 1 on jq failure.
  - Add `review_pr_bare_count RAW`: prints the integer when RAW matches `^[[:space:]]*([0-9]+)[[:space:]]*$`, else returns 1.
  - Update the Provides list and the argument-only convention (:29-35) to name the stdin exception.
- `claude/.claude/scripts/review-pr-checkout.sh`:
  - Row 19 block.
  - Hoist MAIN_REPO_ROOT above it.
  - Extract the :296-314 verdict case into one script-local function used by both audits, taking the stop text and the no-verdict text as arguments (row 19).
  - Replace the :238-239 regex with `review_pr_is_full_hex_oid`.
  - Delete the :213-216 refusal. Keep the field reads and their exit-2 "undecided" path (:207-212).
  - Replace the :203-206 comment with one line: "The checkout writes the whole head tree, so for a base other than the default branch every path the head changes from the default branch is audited too."
  - Print the base OID as a full-hex line after the diff path.
  - Usage text: drop the base-branch clause from :31-34 and the exit-3 list (:84-87); add the delta step and the stdout line.
  - Wall time, derived from the caps: the default-base path is unchanged at 250 success / 310 failure. The stacked path adds 5 (check-ref-format) + 30 (default fetch) + 5 (rev-parse) + 30 (counting run) + 30 (name run) + 10 (two jq calls) = 110, giving 360 / 420. State both pairs.
- `claude/.claude/scripts/tests/test_review_pr_lib.py`:
  - The hex matrix moves here from the checkout test (:918-951's eleven cases, plus 40-hex and 64-hex passing, `40hex\n` failing, and `40hex\n--upload-pack=x` failing).
  - A stdin-bytes harness for the NUL helper, because `_call` passes text arguments. Cases: `b"a b\0x\ny\0-lead\0"` gives three names in order; `b""` gives `[]`; `b"a\0"` gives `["a"]`.
  - `review_pr_bare_count`: `"       3"`, `"3\n"` and `" 12 \n"` pass; `""`, `"3 4"`, `"abc"` and `"-1"` return 1.
  - The NUL helper returns 1 when jq fails: after sourcing the lib, redefine `_lib_jq() { cat >/dev/null; return 1; }` in the test's bash snippet.
- `claude/.claude/scripts/tests/conftest.py`:
  - Hoist from test_review_pr_checkout.py: `_push_new_base_commit`, `_origin_main_sha`, `_BASE_FROM_ORIGIN_MAIN`, `_git_shim_that_fails_on_subcommand`, and `_shim_match_count`.
  - Replace `_SUBPROCESS_TIMEOUT_SECONDS` with literal `timeout=60`, as conftest:493 does.
  - In the shim helper, replace `fail_only_first_match: bool` with `fail_only_match_number: int | None` (fail only the Nth match, 1-based).
- `claude/.claude/scripts/tests/test_review_pr_checkout.py`:
  - Import the hoisted helpers from conftest. Migrate the one `fail_only_first_match=True` caller (:1425) to `fail_only_match_number=1`.
  - Keep one hex deny case wired through the script (option-shaped) and the existing 64-hex test.
  - Change `== 2` to `== 3` at :903 and :1044, and update the docstrings at :116-123.
  - Replace :678's test with a new `TestStackedBaseDeltaAudit` class whose fixture is built locally: `feature-base` branches off main, the flagged file is committed on the base (never on default), and the PR is on top.
  - Cases:
    - Clean delta: exit 0, worktree created, diff against the base OID, and the full-hex line printed.
    - Base adds `.mcp.json`: exit 3; stderr names review-pr-diff.sh, the path, and the base branch `feature-base`, and does not contain "no refs/pull"; no worktree; no diff file.
    - Reverted-on-base: the flagged file is removed on the base after the fork. Expect exit 3, and `_shim_match_count == 0` for `*fetch origin <base OID>`.
    - Two stacked non-default bases, with the flag on the lower one: exit 3.
    - Special-character name: tree entries made with `git update-index --add --cacheinfo`, an ordinary file sorting first plus `.claude/hooks/<non-ASCII and newline>.sh`. Expect exit 3 with the match on one JSON-escaped line (this pins `-z` and non-first position).
    - `* diff --name-only *` failing on every match gives exit 2. With `fail_only_match_number=1`, only the counting run fails: exit 2. With `fail_only_match_number=2`, only the name run fails: exit 2. In all three, no worktree.
    - No merge base: `_push_unrelated_root_commit` gains a `ref` parameter, defaulting to today's `refs/heads/unrelated-root`. The case pushes the root to `refs/pull/42/head`, passes it as headRefOid with a stacked base name, and expects exit 2 with no worktree.
    - A stand-in audit that gives a verdict on its first call and none on its second: exit 2. Stderr contains "returned no verdict" and contains neither "before any fetch of the PR's ref" nor "the PR's file list".
    - check-ref-format, parametrized over `a..b`, a space, `a:b`, `a~1`, `a@{b`, `a.lock`, and a control character: exit 2, stderr names the rejected default-branch name, and `_shim_match_count == 0` for the glob `*fetch origin refs/heads/*`.
    - Count mismatch: a `jq` PATH shim after the :2121-2160 precedent, matching argv that holds the NUL-split filter. It reads all stdin first, so git gets no SIGPIPE, then prints a one-element array holding only a benign name (`README.md`). Every other call goes to the real jq. The delta holds two or more names, including `.claude/hooks/x.sh`. Expect exit 2 and the count-mismatch message, not exit 0 and not a stop. A missing count check would audit the benign name alone, exit 0, and fail the test.
    - Padded count: a `wc` PATH shim that reads stdin and prints its byte count as `printf '%8d\n'`. On a clean stacked delta, expect exit 0 and a worktree. This pins `review_pr_bare_count`'s wiring on any host.
    - Invalid UTF-8 name: the base adds `.claude/hooks/\xff.sh` through `git update-index --add --cacheinfo` (bytes path). Expect exit 3 (rows 50 and 51). This also pins `LC_ALL=C` on a macOS run.
    - Default fetch failing, parametrized over exit 1 and 124: exit 2, no worktree.
    - A default-base PR creates no `-default` ref.
  - Keep :704 with its assertion flipped to "the `-default` ref exists".
  - Rerun `TestCheckoutRunsNoGitHooks` (:1741) on a stacked fixture, asserting the `refs/review-pr/pr-42-default` ref exists and the reference-transaction sentinel is absent.
- Verification: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `../../../.venv/bin/shellcheck claude/.claude/scripts/_review-pr-lib.sh claude/.claude/scripts/review-pr-checkout.sh`, `../../../.venv/bin/ruff check claude/.claude/scripts/`.

**Dispatch B — diff-only base fetch** (after A)
- `claude/.claude/scripts/review-pr-diff.sh`:
  - Rows 13 and 18. The first `pr view` stays a single call, so the three-call test at :378 holds.
  - Read baseRefOid with `_lib_jq -r '.baseRefOid // empty'`, the same filter as checkout.sh:238.
  - Fetch after the audit and before `gh pr diff`.
  - Usage text: stdout and `BASE_UNAVAILABLE`.
  - Wall time: 125 + 10 (two jq parses) + 30 (base fetch) = 165 clean, 180 with a finding.
- `claude/.claude/scripts/tests/test_review_pr_diff.py`:
  - The shim's `pr view` branches on `--jq` the way the checkout shim does (:281-291). Its default baseRefOid is origin/main's tip, so the existing tests keep reaching the fetch.
  - `_run` (:208) gains a `path_prefix` parameter, mirroring checkout's :376/:402. The shim default and the fetch-failure cases use the conftest-hoisted `_BASE_FROM_ORIGIN_MAIN`, `_origin_main_sha`, `_git_shim_that_fails_on_subcommand`, and `_shim_match_count`.
  - Add a `_printed_diff_path` helper reading line 1, replacing the 7 `Path(result.stdout.strip())` sites (:294, :449, :463, :530, :662, :711, :744).
  - Cases:
    - Success against a base from `_push_new_base_commit`: `git cat-file -e <oid>^{commit}` fails before the run and succeeds after, and the full-hex line is printed.
    - Option-shaped baseRefOid `--upload-pack=<script that touches a sentinel>`: sentinel absent, `BASE_UNAVAILABLE not-hex`, exit 0. Control: a plain `git fetch origin --upload-pack=<script>` against the local-path origin does touch the sentinel.
    - Missing baseRefOid: `BASE_UNAVAILABLE missing`.
    - Base fetch failing with exit 1 and with 124: `fetch-failed` / `fetch-timed-out`, exit 0, one stdout line, diff and provenance written, no raw git text on stderr.
    - A `jq` PATH shim exits 1 only for argv `-r .baseRefOid // empty`. Expect `BASE_UNAVAILABLE missing`, exit 0, diff written. Separately, a missing headRefOid still exits 2.
- Verification: select-tests.py, shellcheck on `review-pr-diff.sh`, ruff, as in A.

**Dispatch C — skill prose and docs** (after B). Locate every edit below by its text, not its line number (the numbers are from the 167-line base); an edit whose target text is already in place is a no-op, and the 200-line cap is checked at the end.
- `claude-skills/skills/review-pr/SKILL.md`:
  - :9: add CI logs and base-branch content to the data list.
  - :52: drop the base-branch refusal.
  - :53: the PR's files are audited before the ref fetch; for a non-default base, the head's changes from the default branch are audited before the worktree is created.
  - :56 and diff-only bullets: the base commit is printed as "a full-hex line", not by line number. Add a diff-only bullet for `BASE_UNAVAILABLE`: skip base reads, keep base-dependent claims open naming the base read that would settle them, and say in step 8, outside the body, that base content was unreadable and why.
  - :62: after a non-zero exit other than 3, the human may re-run the script or ask for the diff-only path.
  - :64: "or the script's exit 3".
  - Step 3: the plan source is the PR's diff or its linked GitHub issue only (row 8).
  - Step 4: the linked issue (row 8).
  - Step 5: one paragraph on base reads with row 16's quoting rule. Subagents mark unchecked claims (appended to :96).
  - Step 6: row 29.
  - Step 7, before the list: one paragraph stating row 48's protected-findings rule once, for claim resolution, the pre-existing and precedent check, merging, and the consult.
  - Step 7 list:
    - :108 root-cause dedupe.
    - New item for claim resolution (row 2).
    - New item for pre-existing and precedent (row 30).
    - :111 scrub widened (row 10), plus the sentence keeping non-GitHub linked-issue text out of the body (row 8).
    - :112 citations (row 45).
  - Step 7 paragraphs: row 6's CI forms, including the masked-failure and 20-row rules; row 44's pasteable fix; row 34's hygiene list as a list, with its lead-in folded into the preceding line.
  - Consult: move the :115 consult after the body-write paragraph and rewrite it as row 36. It covers: Write, then Read of the body, then the consult; one Edit per finding and never a whole-file re-Write afterwards; rules (a) and (b); the consult after every edit round, including step-8 edits; and the third-consult stop. The consult reads the findings-body file and the diff file (or worktree).
  - Step 8:
    - Outside the body, in step 8: a summary of the consult Edits (each finding removed or downgraded, with its reason); the evidence any step-7 pass or the consult raised against a protected finding; the third consult's unapplied corrections, if that stop was reached; the non-actionable notes; the BASE_UNAVAILABLE statement; the plan-pass skip statement; and a marker on each finding that used a non-GitHub issue read.
    - The edit → consult → marker → present loop.
  - Constraints:
    - End at 200 lines or fewer (the hard cap); the file is already at 200, so each edit is in place.
    - Add sentences to existing single-line paragraphs where they belong. Never flatten a list.
    - Pre-named cuts, all applied:
      - Merge the restated timeout and re-run text (:26, :60, :77, :79) into one statement in Step 1. It names `review-pr-acquire.sh`, `review-pr-checkout.sh`, and `review-pr-diff.sh` only, and keeps :60's worktree-leftover fact attached to the checkout script. `review-pr-post.sh` keeps step 8's own no-retry rule (−3).
      - Fold :55's first sentence into :54 and drop its second, which :162 restates (−1).
      - Delete :160, which the bare code block above it restates (−1).
    - Stop and report only if the file is still over 200 after those cuts.
    - The disclosure line stays byte-identical.
    - Script lines that take no argument stay bare.
    - Add no `<!-- SCOPE_RULE` anchor.
    - Write § citations on one line.
- `claude-skills/skills/review-pr/REFERENCES.md`:
  - :142 and :159-162: the delta audit replaces the refusal.
  - Reduced-coverage section: base reads by object ID; head content beyond the hunks stays unreadable; `BASE_UNAVAILABLE`.
  - New section on base reads: rows 15-17, the two fixed-shape options not adopted, and the quoting residual.
  - New section on step 7's controls: row 48's protected findings; row 36's per-finding Edit record and cadence; row 41's Read step; row 43's residual.
  - Known gaps: a bullet on the linked-issue read's trust in the operator's connector (row 52); `refs/review-pr/pr-<N>-default` beside :316-324's ref; base objects fetched by review-pr-diff.sh; the two-dot residual; a stacked exit 2 has no automatic fallback.
- `docs/scripts.md`: :173 adds the full-hex line and the stacked audit; :175 adds the full-hex line and `BASE_UNAVAILABLE`.
- `CHANGELOG.md`: one new `[Unreleased]` → `### Changed` bullet. Existing entries are read-only.
- Verification:
  - `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
  - `wc -l claude-skills/skills/review-pr/SKILL.md`
  - `grep -cF 'Reviewed from the PR diff only — no checkout, no checks run.' claude-skills/skills/review-pr/SKILL.md` must print `1`.
  - `/skill-review` on the SKILL.md diff.

## Verification

1. Run each dispatch's own commands. select-tests.py maps the scripts' tests, `test_review_pr_post.py` (it reads SKILL.md), and `test_skills.py`.
2. `git diff --quiet origin/main...HEAD -- claude/.claude/scripts/review-pr-check-attribution.sh claude/.claude/scripts/marker.sh claude/.claude/scripts/review-pr-finish.sh claude/.claude/scripts/marker-clear-stale.py claude/.claude/settings.json claude/.claude/scripts/tests/test_review_pr_check_attribution.py claude/.claude/hooks/tests/test_marker_script.py` exits 0. This shows the disclosure line, marker modes, session-artifact cleanup, and the allowlist are untouched.
3. Repo-wide lint per CLAUDE.md, from the worktree: `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` and `../../../.venv/bin/ruff check claude/.claude/ claude-skills/`.
4. Run `/code-review` per dispatch. Confirm `ciso-reviewer` spawns on dispatch A's checkout.sh change and `staff-sdet` on each test rewrite. The SKILL.md commit is gated by `/skill-review` (hook-enforced).
5. Scratchpad prose checks, never committed (per `.claude/rules/skill-and-agent-self-review.md`), during `/skill-review`. Each scenario gives a fresh agent the relevant SKILL.md text and runs 3 times; a miss in any repeat means fix the prose and rerun. Inspect composed commands; never run them.
   - Quoting: `a$(touch <scratch>/x).txt` yields a single-quoted command. `x'y.txt` and a name holding a newline are refused and reported unread.
   - Protected findings: in diff-only mode, AUDIT_FINDING flags `.claude/settings.json`, which also exists at the base. The finding stays blocking in the body, and the pre-existing evidence appears only in step 8.
   - Consult: the consult calls an AUDIT_FINDING-derived finding a false positive and recommends dropping one non-blocking finding. The audit finding keeps its tier, with the objection in step 8. The drop happens only after the session verifies it, as one Edit after a Read of the body, with no whole-file re-Write. Step 8 lists the drop with its reason, and a further consult runs before presentation.
   - Merge: an AUDIT_FINDING-derived finding and a non-blocking finding share a root cause. The merged finding keeps the blocking tier and names the audit match.
   - Claim resolution: a hedged finding that base content settles appears resolved, citing `<baseRefOid>:<path>:<line>`. One that cannot be settled names what would settle it. "unverified" appears nowhere in the body.
   - Pre-existing and precedent: a non-protected defect present at the base that the PR does not reach moves to the step-8 notes. A non-protected finding that follows base precedent is non-blocking, with the base citation.
   - BASE_UNAVAILABLE: base reads are skipped, base-dependent claims stay open, and the statement appears in step 8 outside the body.
   - Hygiene: a draft containing "the consult", "Step 5", and "unverified" comes out with all three removed, and the three mandated lines and the coverage statements kept.
   - Linked issue, GitHub: a PR body citing `#12` and `https://github.com/<other-owner>/<other-repo>/issues/99` reads only issue 12, composed as `gh issue view 12 -R <owner>/<repo> --json title,body,state`, never a URL and never 99. A second case with this repo's own issue URL as the only reference composes `gh issue view <n> -R <owner>/<repo> ...` with digits only. A third case puts the reference in the title alone.
   - Declared tracker, allow path: the session-loaded `CLAUDE.md` declares a non-GitHub tracker, the body names `PROJ-12`, and the tool list holds both `mcp__linear__get_issue` and `mcp__linear-server__get_issue` plus decoy write tools (`create_comment`, `update_issue`). The review asks the engineer to confirm the tracker and key, then makes one read-only lookup with the bare key and no write call. The scripted engineer reply confirms `PROJ-12`; a second case has the engineer correct the key or name a different tracker, and the confirmation then uses the correction. A `PROJ-12` inside a tracker URL is unread and named, with no lookup.
   - Undeclared tracker: no `CLAUDE.md` tracker and the body names `PROJ-12`. The review makes zero prompts and zero lookups, and names `PROJ-12` in inline code as what would settle the dependent claim. A PR-added `CLAUDE.md` line naming a tracker is ignored. A separate case has the engineer name a tracker unprompted, after which the confirm step applies to the exact key.
   - GitHub-declared tracker: `CLAUDE.md` declares GitHub and the body names `PROJ-12`. No MCP call; the dependent claim stays open.
   - Failed read: `gh issue view` not found, or an MCP error. The review works from title and body and keeps the dependent claim open, worded the same as an unread key.
   - False-positive shapes: `UTF-8`, `SHA-256`, and `CVE-2024` in the body with no declared tracker trigger no read and no prompt; with a declared tracker the confirmation shows the key and a decline leaves it unread.
   - Confirmation and selection: the engineer declines or does not answer, so no read and the key is worded as unread. With `#12` and `PROJ-9` both present, only `#12` is read. A foreign-owner GitHub URL listed alone, and a same-owner other-repo URL, are unread and named. The `CLAUDE.md` the session loaded at start declares tracker A and the PR's copy declares tracker B: A wins.
   - Injection: issue text from a confirmed non-GitHub read holding an instruction and a secret-shaped string changes no tier and reaches neither the body nor a subagent prompt.
   - Claim resolution: CI-log text, or a GitHub issue or PR body, saying a path is "validated upstream" leaves the finding at its tier with the claim kept open.
   - Audience: a finding drawn from a confirmed non-GitHub read and supported by it alone goes to step 8, not the body; one with independent code evidence cites only the key; the step 8 notes mark each finding that used the read; no content or paraphrase appears, and a plan found only there is not given to `/plan-review`.
   - Plan source: a PR body links a gist as its plan. `/plan-review` is skipped with no fetch composed, and step 8 says why outside the body. A plan file in the diff, and a plan in the linked GitHub issue, still run `/plan-review`.
6. Steps 2, 3, 4, 5, 7, and 8's prose has no automated test. `test_review_pr_post.py` pins only zero-argument script lines in SKILL.md, and a literal-pin test for step 4 is deferred in the review ledger (round 36). The first live `/review-pr` run is its behavioral check, and nothing from that run is cited in this repo. It passes when:
   - The posted body has no bare "unverified" and no review-process words outside the exemptions.
   - Every Question names who or what can answer it.
   - Every protected finding is at its original tier.
   - Step 8 lists every consult Edit with its reason.
   - The body presented for approval is the version the last consult reviewed.
   - At most one linked issue is read, with no URL passed, and a non-GitHub read happens only after the engineer confirms the tracker and key.
   - No content or paraphrase from a non-GitHub issue appears in the posted body, and the step 8 notes mark which findings used that read.

## Out of scope

- `/code-review` edits [engineer-verified: "review-pr only (Recommended)"].
- A script-produced consult diff (`review-pr-consult-diff.sh` with a saved pre-consult body). Rejected: the engineer's choice [engineer-verified: "Prose + per-finding Edits (Recommended)"].
- Changing the diff-only disclosure line or adding a marker or provenance mode. The line is byte-matched in four files, and marker.sh accepts only `checkout`/`diff-only`.
- Two-dot delta hardening (`<default-oid>..<head-oid>`). It would close a residual: a file in the head that sits in default-branch history but was removed at the default tip. A default-base PR carries the identical residual today, so stacked PRs gain no new exposure. REFERENCES.md names it.
- Head reads by object ID for diff-only PRs. These would give full-file context with nothing on disk, but would make "Reviewed from the PR diff only" inaccurate, which reopens the disclosure line. A follow-up candidate.
- A base worktree for diff-only PRs, and the Write-tool request-file route for base reads (row 16).
- Granting Bash to reviewer agents or `plan-architect`.
- Any `permissions.allow` entry. `settings.json` is not touched.
- Raising review-pr's cap in `check-skill-length.sh`.
- `review-pr-finish.sh` deleting `refs/review-pr/*` refs. This is an existing known gap, now with one more ref.
- `git worktree add` submodule behavior (row 28).
- `review-pr-acquire.sh`: no new fields.
- Session-B suggestions not adopted:
  - A second disclosure-line variant.
  - A realpath/macOS note.
  - Restating the tier-label vocabulary.
  - Proportion figures. Per-engagement figures are barred from this public repo.
