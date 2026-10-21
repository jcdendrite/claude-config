# GH-1236 P2: review-pr reviewer findings files and diff-only wording

## Context
Goal: let the reviewers `/review-pr` spawns write their findings files from any tree the session stands in, including the main tree and diff-only mode. Each `findings_path` points into a fixed per-session directory under `/tmp`. The same branch also carries the already-committed Step 2 stop-rule wording fix at `SKILL.md:72`.

Ask: "yes reopn and tick P1 then plan-it on P2". Scope was first narrowed via label "Two branches: docs first (Recommended)". On whether P2 should also add a mechanical `AUDIT_FINDING` check, the engineer said "I'm not sure. Ask the architect." and then "Have fable do an adversarial review on the architect's design". After this branch shipped the inline design, the engineer said "Returning inline is always more expensive and less efficient. So what are the ways we can get this working within the work tree?", then "Fold that existing line fix into the plan-it session", then "you definitely need to re-plan option one inside 1289 otherwise it'll ship a regression".

Tracker #1236 collects `/review-pr` fixes. Its P2 group, "Diff-only mode" (from #1233 problem 1), has two items:
- P2.1: a reviewer's `Write` of its `findings_path` file is denied, and one reviewer reported a successful write that never happened. A second half, the `AUDIT_FINDING` disclosure check on `marker.sh write review-pr`, is out of scope here (row 4).
- P2.2: Step 2's wording read as contradictory. Line 58 says exit 3 from `review-pr-checkout.sh` means "switch to the diff-only path". Line 72 said "Any non-zero exit status, including from a re-run, is final" without naming the script it governs.

Session findings, as evidence for the design:
- `/code-review` and `/plan-review` give each reviewer a relative `findings_path` under `agent-reviews/`.
- A `/review-pr` session may stand in the main tree in either mode, and diff-only mode never has a review worktree. In that main tree, `claude/.claude/hooks/require-worktree-for-file-writes.sh` denies the reviewer's `Write`.
- PR #1289 as it stands tells the session to pass no `findings_path`. Every reviewer then returns its findings inline, which `/plan-review` caps at 2K tokens (whether `/code-review` caps it too is an inference, row 22). The engineer called that a regression, and this revision replaces it.
- `deny-reviewer-tree-mutation.sh` allows a reviewer `Write` only under a literal `/tmp/` path or a confirmed-ignored `agent-reviews/` path.
- An earlier read-only architect consult in this session endorsed "option 1", a per-session `/tmp` directory. This plan re-derives that design. It departs from the proposal in three ways: the script's name, how `SKILL.md` stays at 200 lines, and an added fallback and pre-merge probe.
- The `AUDIT_FINDING` disclosure check is out of scope here. It gets its own plan and branch.

Intended outcome:
- A new zero-argument script creates the session's reviewer findings directory.
- `SKILL.md`'s standing override swaps that directory in for `agent-reviews/` in every reviewer `findings_path`.
- `review-pr-finish.sh` removes the directory.
- `settings.json` allows the script by exact match, and `_lib.sh` holds one helper that names the directory.
- `SKILL.md:72` names `review-pr-diff.sh`.
- The docs, tests, CHANGELOG entry, and PR body describe the new contract.

## Approach

Each reviewer that `/review-pr`'s steps 3 and 5 spawn gets an absolute `findings_path` inside `/tmp/review-pr-reviewer-findings-<session id>`. A new zero-argument script creates that directory with `mkdir -p` after step 1 and prints its path. `review-pr-finish.sh` removes it on every exit path. A re-run prints the same directory. This replaces the branch's pass-no-`findings_path` override. The `SKILL.md:72` rewrite stays as committed.

This design assumes one user per machine (G4), so it carries no ownership, sticky-bit, random-suffix or file-mode machinery. `/tmp` is the one location both write gates already admit for a reviewer (row 9), so no gate changes. Alternatives set aside:
- **Pass no `findings_path` (the branch as it stands).** The engineer rejected it (row 2). Inline findings exist only in the session's context, and `/plan-review` caps them at 2K tokens (row 22).
- **A scratch linked worktree per session (option 2).** It adds a worktree per review. It also moves the session's anchor mid-session, which `CLAUDE.md` § "Agent Briefing" bars while a dispatched agent runs. It contradicts `SKILL.md:150` and `:196` too, which let the session stand in any tree.
- **Exempting an ignored `agent-reviews/` from the main-tree write gate (option 3).** The gate would then allow an input it denies at the merge-base, and `docs/hooks.md:26` defines that as a regression (G1). Further costs:
  - It loosens every agent's main-tree `Write`, not only reviewers'.
  - It adds a `git check-ignore` call to a hook that runs on every `Write`.
  - It leaves a third-party PR's findings in the main tree, with nothing to remove them.
- **Inline output with a raised budget.** It would be a `/review-pr`-only exception to `code-review/SKILL.md:306` and `plan-review/ROUTING.md:54`. Findings would still live only in context, so compaction loses them.
- **A path beside the session's other files in `<config-dir>/.review-pr-active.d/`.** `deny-reviewer-tree-mutation.sh:361` denies a reviewer `Write` there (G3). That directory also holds the findings body `marker.sh` hashes, which no reviewer may touch.
- **The step-2 review worktree.** Diff-only mode has none, and a write there lands in the PR's own checkout, against the override's "change nothing".
- **The harness's per-session scratch directory.** It would remove the script, the allow entry, the helpers and the guarded delete in one move. It stays set aside for mechanical reasons. On macOS its path starts `/private/tmp/`, which the reviewer gate's literal `/tmp/*` match (`deny-reviewer-tree-mutation.sh:288`) does not admit, and G3 bars changing the gate. Ordinary interactive sessions here do not carry one. Its lifecycle and subagent-write contract are unverified.

- **A random `mktemp` suffix, with an ownership-checked selector.** The suffix only defeated pre-creation of the name by another uid, which G4 rules out.
- **A recorded path in `<config-dir>/.review-pr-active.d/`.** It would add nothing but an artifact suffix that `TestReviewPrArtifactSuffixEnumerationsAgree`'s enumerations must carry.
- **The session building the path from `marker.sh resolve-session-id`.** The name would then live in `SKILL.md` prose and in finish, and the session would hand-copy a UUID, which `review-pr-findings-path.sh:2-4` exists to avoid.

**Assumption ledger**

Root: in diff-only mode, and from the main tree of a repo with worktree discipline on, `/review-pr`'s spawned reviewers get a relative `agent-reviews/` `findings_path` that the main-tree write gate denies. Their findings are then lost or reduced to an inline return, which `/plan-review` caps at 2K tokens (whether `/code-review` caps it too is an inference, row 22). The branch's current remedy forces every reviewer inline. Separately, `SKILL.md:72`'s stop rule must keep naming the script it governs.

Givens:
- **G1: the main-tree write gate is not relaxed for this.** `require-worktree-for-file-writes.sh` denies `Write` to a main-tree path in a repo with worktree discipline on (`:170-172`). Its header tiers it `cooperative, irreversible` (`:3`). `CLAUDE.md` § "Hook threat model" says a gate listing `irreversible` "is never relaxed on false-positive cost alone". `docs/hooks.md:26` counts an input the gate denied at the merge-base and allows afterwards as a regression. These are repo rules, outside this plan. `[verified: require-worktree-for-file-writes.sh:3, :170-172; CLAUDE.md § "Hook threat model"; docs/hooks.md:26]`
- **G2: `review-pr/SKILL.md` is at its 200-line cap.** `check-skill-length.sh` caps it at 200. It denies a commit that leaves the file over the limit and longer than its committed version. Raising the cap is a separate decision outside this plan. `[verified: check-skill-length.sh:6-8, :11, :111-119]`
- **G3: the reviewer write gate is not relaxed either.** `deny-reviewer-tree-mutation.sh` allows a review-only agent's `Write` only under a literal `/tmp/` path, or under an `agent-reviews/` path that `git check-ignore` confirms is ignored. It denies everything else (`:287-361`). It is also tiered `cooperative, irreversible` (`:3`), so G1's rule applies. `[verified: deny-reviewer-tree-mutation.sh:3, :287-361]`

- **G4: one user per machine.** `[engineer-verified: "Wrong threat model. There’s only one user on a machine that uses Claude-config. No hostile user situation"]` The plan therefore treats no file mode as a control. A same-user process, or another reviewer of the same round, can read and write the directory; that is accepted.

Rows:
1. This branch implements option 1 inside PR #1289 and ships no inline-only remedy. `anchors: root` `[engineer-verified: "you definitely need to re-plan option one inside 1289 otherwise it'll ship a regression"]`
2. Reviewer findings go to files, not inline returns. `anchors: root` `[engineer-verified: "Returning inline is always more expensive and less efficient."]`
3. The committed `SKILL.md:72` fix stays in this plan. `anchors: root` `[engineer-verified: "Fold that existing line fix into the plan-it session"]`
4. The `AUDIT_FINDING` check stays off this branch and gets its own plan and branch. `anchors: root` `[engineer-verified: "Two branches: docs first (Recommended)"]`
5. The scope this plan delivers beyond the Ask's docs wording is kept: the new script, the `_lib.sh` helpers, the `settings.json` allow entry, the finish-script cleanup, the test files, the `SKILL.md` line folding, the re-dispatch and script-failure fallback rule, and the pre-merge probe. The session's question asked "Keep all of it?" over a list the plan recalls from the session and does not quote; the option text was session-authored. The test files at that point were `test_lib.py`, a new script test and `test_review_pr_finish.py`. This revision also touches `test_skills.py`, `test_review_pr_post.py` and `test_deny_reviewer_tree_mutation.py`, which row 5c records. `anchors: row1` `[engineer-verified: "Keep all (Recommended)"]`
5a. Findings go under `/tmp`, outside every worktree, rather than in a scratch linked worktree. The engineer's Ask said "within the work tree"; this plan reads that as "working from any tree the session stands in, including a main tree", not as a requirement that the files live inside a worktree. The question put to the engineer named that tension. `anchors: row1` `[engineer-verified: "Accept /tmp (Recommended)"]`
5c. The `test_skills.py` param, the `test_review_pr_post.py` allowlist-tuple entry and a `test_deny_reviewer_tree_mutation.py` allow test for the new path shape are part of this change, along with the other files named in Critical files. The "Keep all" answer covered items the session listed, and these three follow from the pins the plan already named. `anchors: row5` `[unverified: the engineer's "Keep all" answer was read against a list that named three test files]`
5d. The rationale rests on completeness, not on token savings. `anchors: row2` `[engineer-verified: "Completeness, not savings (Recommended)"]`
5e. Orphaned findings directories get a Known-gap bullet, not a sweep in this change. `anchors: row15` `[engineer-verified: "Known gap only (Recommended)"]`
5f. The Known-gap bullet carries a manual removal command. `anchors: row5e` `[unverified: the command is the plan's addition to the engineer's label]`
5b. The four earlier quotes on the Ask line and in row 4 are the engineer's own words and label from the first part of this session. `anchors: root` `[engineer-verified: "Yes, all four"]`
6. Both dispatchers the override reaches pass a relative path:
   - `code-review/SKILL.md:300` says "pass `findings_path: agent-reviews/<agent-name>-<suffix>.md`".
   - `plan-review/ROUTING.md:50` uses the same template.
   - Each `Read`s the file when a reviewer reports a write (`code-review/SKILL.md:302`, `plan-review/ROUTING.md:52`).

   `anchors: root` `[verified: code-review/SKILL.md:300, :302; plan-review/ROUTING.md:50, :52]`
7. A `/review-pr` session may stand in the main tree in either mode. `SKILL.md:150` says "from any tree of the repo", and `:196` says "Run it from the main tree or from a worktree that is not a review worktree". Diff-only mode has "no checkout and no worktree" (`:64`). A relative `findings_path` from such a session lands in the main tree, where G1 denies it. `anchors: root` `[verified: review-pr/SKILL.md:64, :150, :196]`
8. Among skills reachable from steps 3 and 5, only `/code-review` and `/plan-review` hand out a `findings_path`. A grep of every `*.md` under `claude-skills/` and `plugins/` for `findings_path` finds seven files. `ready-for-review/SKILL.md:100` also hands one out, but neither skill invokes it. One override rule therefore covers every sibling arm. `anchors: row6` `[verified: grep this session]`
9. Both write gates admit a reviewer `Write` to `/tmp/<dir>/<file>.md`:
   - `deny-reviewer-tree-mutation.sh` exits 0 on `/tmp/*` after its `..` guard (`:287-288`). It matches the path as written (`:86`).
   - `require-worktree-for-file-writes.sh` exits 0 when the nearest existing ancestor is in no git repository (`:124-132`).
   - `require-plan-review.sh:192-209` scopes a path outside the repository out of its gate (`:161-163` is the comment that says so).
   - Existing tests pin the first two allows: `test_deny_reviewer_tree_mutation.py:93` and `:779` (a `/tmp/scratch/...` path is allowed), `:770` (a `/tmp/a..b.py` path is allowed), and `test_require_worktree_for_file_writes.py:114` (a path outside any repository is allowed).

   I read these lines and ran nothing. Verification step 6's probe covers only the harness permission layer. `anchors: root` `[verified: the test lines above exist; unverified: execution, which only the probe supplies]`
10. **Mechanism:** a new script, `claude/.claude/scripts/review-pr-reviewer-findings-dir.sh`, shaped like `review-pr-findings-path.sh`:
    - It takes zero arguments and exits 2 with usage on any.
    - It resolves the session id with `marker.sh resolve-session-id`, calls the helper in row 12, runs `mkdir -p` on the result and prints the path.
    - Exit 2 means usage only. Every other failure exits 1 with one stderr line naming the path or the failing step and nothing on stdout, so the session's inline fallback (row 23) triggers. It never prints through a failing command substitution.
    - `settings.json` allows it by exact match.

    The name says "reviewer-findings" because "findings" alone already names the step-7 body in this skill (`SKILL.md:127`, `review-pr-findings-path.sh`). `mkdir` stays in the script so an unwritable `/tmp` fails once there, not once per reviewer write. Lighter primitives checked:
    - Extending `review-pr-findings-path.sh` fails. It runs at step 7, after every reviewer has finished, and its any-argument-exits-2 contract (`review-pr-findings-path.sh:21-24`, pinned by `TestUsageErrors`) would break.
    - Having `review-pr-acquire.sh` create the directory fails. Its printed document carries PR-authored text, and its "any failure aborts" contract (`SKILL.md:39`) would turn a scratch-directory failure into an aborted review.

    `anchors: row9` `[verified: review-pr-findings-path.sh:2-7, :21-24; review-pr-acquire.sh:381-382; review-pr/SKILL.md:39, :127]`
11. The helper hard-codes `/tmp`, so `TMPDIR` never moves the directory out of the one root the reviewer gate admits. `anchors: row10` `[verified: deny-reviewer-tree-mutation.sh:288]`
12. **Mechanism:** `claude/.claude/hooks/_lib.sh` gains one helper, `_lib_review_pr_reviewer_findings_dir SESSION_ID`, placed after `_lib_review_pr_select_session_worktrees` (`:965-984`). It returns 1 and prints nothing unless `_lib_valid_session_id_component` accepts the id, else prints `/tmp/review-pr-reviewer-findings-<session id>`. The script and finish both call it. That replaces any second spelling of the name, which is the drift `:925-929` gives as the reason the worktree prefix helper exists. `_lib.sh:923` and `_lib_resume_context_index_dir` stay untouched. `anchors: row10` `[verified: _lib.sh:914-984; .claude/rules/bash-unit-test-seams.md]`
14. A re-run prints the same directory and `mkdir -p` succeeds on it. Findings file names carry `<epoch>-<slug>` (`findings-path-suffix.sh:37-43`), so files do not collide. `anchors: row10` `[verified: findings-path-suffix.sh:37-43]`
15. **Mechanism:** `review-pr-finish.sh` calls the helper right after the artifact removal (`:93-96`) and, when it prints a path, runs `rm -rf -- "$dir"` uncapped. `rm -rf` on a path with no trailing slash unlinks a symlink without following it, so no `-L` or `-d` guard is added. One existence test decides only whether to log "removed". The call sits behind `||`, so the script still exits 0 on every path past the argument check (`:23-25`, `:83-86`). The usage text adds the directory to the file-cleanup list (`:11-28`; `docs/scripts.md:181`). `anchors: row10` `[verified: review-pr-finish.sh:11-28, :83-110; docs/scripts.md:181]`
18b. Any reviewer of the same round, and any session of the same user, can read and write the directory, because the reviewer gate admits every `/tmp/*` path. G4 accepts that. REFERENCES records it as the canonical home, with a short CHANGELOG clause. `anchors: G4` `[verified: deny-reviewer-tree-mutation.sh:288]`
19. The harness lets a subagent `Write` an absolute path outside the project directory, and lets the session `Read` it, without a permission prompt. No Read hook matches `/tmp/*.md`. This is the plan's largest risk. If it fails, the worst case is one of two:
    - A permission prompt per reviewer.
    - A declined write, which the reviewer reports as a failure and which sends it to the inline fallback (`code-review/SKILL.md:302`).

    Read-side hooks that could match a `/tmp/*.md` path were checked (`deny-env-reads`, `deny-data-file-reads`, `deny-credential-file-reads`, `consume-durable-continuity-file-on-read`) and none does. Verification step 6 probes the permission layer before any implementation work starts. `anchors: row9` `[unverified]`
20. On macOS, `/tmp` is a symlink to `/private/tmp`, and `mkdir -p` prints the path as given. The harness passes `file_path` to hooks as written (`deny-reviewer-tree-mutation.sh:131-136` records that only for the current harness). No macOS host was available. `anchors: row11` `[unverified]`
21. Under Claude Code's sandbox, Bash cannot write a literal `/tmp` path, so the script's `mkdir` fails and row 24's fallback applies. `/review-pr` does not run sandboxed today in any case. `anchors: row24` `[verified: docs/case-studies/claude-code-sandbox-write-boundary.md:150-169, Linux only]`
22. The rationale is completeness and persistence, not token savings:
    - A `/plan-review` reviewer given a `findings_path` falls outside the 2K-token inline budget (`plan-review/ROUTING.md:54`). Whether the same cap binds `/code-review`'s reviewers is an inference from `code-review/SKILL.md:304-306`.
    - A reviewer that found nothing returns a one-line pointer.
    - Files persist through context compaction, and a re-run prints the same directory. Persistence ends at finish, which deletes it.
    - The session still reads the full file when the count is non-zero (`code-review/SKILL.md:302`, `ROUTING.md:52`), so files do not shrink context for reviewers that found something. The 86-92% figure in `docs/design-decisions/reviewer-findings-path-output.md:5` measures the returned message only.

    `anchors: row5d` `[verified: plan-review/ROUTING.md:52, :54; code-review/SKILL.md:302; docs/design-decisions/reviewer-findings-path-output.md:5]`
23. **Mechanism:** `SKILL.md:9` keeps every sentence except its no-`findings_path` one (the no-fix and no-marker overrides and the report-only control stay). That one sentence is replaced with: "After step 1 and before any reviewer spawns, run the script below once. In every `findings_path` that `/code-review` or `/plan-review` gives a reviewer, replace the leading `agent-reviews` with the directory it prints, so each file is `<printed directory>/<agent-name>-<suffix>.md` (`REFERENCES.md` § "Why spawned reviewers write findings to a temporary directory"). If the script exits non-zero, give reviewers no `findings_path` and tell the human once, quoting the script's stderr, that reviewers will return inline. `Read` only the path you handed that reviewer. A reviewer that reports a write whose file is missing, or reports a different path, has not reviewed: re-dispatch it once with no `findings_path` and tell the human, naming any path it reported."
    - Rewriting the dispatcher's own path, instead of handing every reviewer one, keeps `ROUTING.md:52`'s rule that a reviewer lacking the file contract gets none.
    - The dispatchers' own text stays unchanged. The exception lives in the caller's override, as the no-fix and no-marker overrides already on `:9` do.

    `anchors: row7` `[verified: review-pr/SKILL.md:9; plan-review/ROUTING.md:52]`
24. The script-failure fallback is the reviewers' documented inline contract (`code-review/SKILL.md:304`). It applies only when the script fails. One example is an operator whose `scripts/` is a real directory and who has not yet re-stowed (row 34). `anchors: row23` `[verified: code-review/SKILL.md:304; CHANGELOG.md:58]`
25. The re-dispatch clause covers P2.1's reported claimed write. Neither dispatcher handles a reported write with no file behind it (`code-review/SKILL.md:302`, `ROUTING.md:52`). The report comes from the tracker and was not reproduced. `anchors: root` `[unverified]`
26. **Mechanism: SKILL.md stays at 200 lines.** `test_zero_argument_allow_entry_matches_every_literal_skill_md_invocation` needs every name in `ZERO_ARGUMENT_ALLOWED_SCRIPT_NAMES` to appear in `SKILL.md` as a line that is exactly `~/.claude/scripts/<name>`. Naming the script inline in prose fails it. The new script belongs in that tuple: its comment defines the set as review-pr scripts that `SKILL.md` invokes with no arguments and `settings.json` allows by exact match. So:
    - A three-line fence holding the bare path goes directly after `:9`, matching the paragraph-then-fence layout at `:127-130`. The blank `:10` stays before `## Step 1`.
    - `:190` absorbs `:194` ("It takes no argument.") and `:200` ("Run this before removing anything yourself."). It becomes "Then, on every exit path once step 1 has run — posted, declined, or aborted at any step, including a step 2 stop or a diff-only abort — and before removing anything yourself, run the script below with no argument:". The staged blob keeps its final newline.
    - `:194`, `:199`, and `:200` are deleted, so the net change is zero.
    - Folding keeps both facts. The cap rule in `.claude/rules/skill-and-agent-self-review.md` bars flattening a parallel list, and these sentences are not list items.

    `anchors: root` `[verified: test_review_pr_post.py:1198-1200, :1309-1321; review-pr/SKILL.md:127-131, :190-200; check-skill-length.sh:6-8]`
27. **Mechanism: edits inside existing lines.**
    - `:78` reads "Under the standing override above, including its reviewer findings directory, give `/plan-review` the PR's plan, …". It drops "pass no `findings_path` and".
    - `:86` drops "and passing no `findings_path`" and adds "(including its reviewer findings directory)" after "under the standing override above".
    - `:96` reads "never `Read` a PR file by path (the findings file at the path you handed a reviewer is not one)". The dispatchers `Read` each findings file by path, that rule concerns PR contents, and the carve-out is bounded to the handed-out path so a reviewer-reported path is never followed.
    - `:197` adds "this session's reviewer findings directories" to what finish removes.

    `anchors: row23` `[verified: review-pr/SKILL.md:78, :86, :96, :197]`
28. `:72` stays as committed: "Any non-zero exit status from `review-pr-diff.sh`, including from a re-run, is final: report stderr and stop. Use no other acquisition route. `review-pr-diff.sh` refuses a PR over 300 changed files." Facts behind it:
    - The checkout script exits 3 only for a refusal that names `review-pr-diff.sh`.
    - `review-pr-diff.sh` has no `exit 3`, and its `set -euo pipefail` can surface other non-zero statuses, hence "Any non-zero".
    - Only `review-pr-diff.sh` caps a PR at 300 files (`REFERENCES.md:206-207`).
    - No test pins the line.

    `anchors: row3` `[verified: review-pr/SKILL.md:72; the four facts are carried from the prior draft's rows 12-14 and 19, not re-checked this session]`
29. The new allow entry does not trip `test_no_allow_entry_matches_a_prompt_gated_script_in_any_argument_form`. None of the four gated names is a substring of the new name, and the entry has no wildcard (`test_review_pr_post.py:1224-1240`). `anchors: row10` `[verified: test_review_pr_post.py:1192-1197, :1212-1240]`
30. **Mechanism: tests.** In `test_review_pr_skill_carries_retained_control`, the `no-findings-path` param (`test_skills.py:7899-7908`) becomes `reviewer-findings-dir`.
    - It pins "replace the leading `agent-reviews` with the directory it prints, so each file is `<printed directory>/<agent-name>-<suffix>.md`".
    - It uses `heading=None`, so the check runs file-wide after whitespace normalization (`:7920-7921`).
    - Its consequence text names the bug it guards: a reviewer spawned from the main tree, given a relative `findings_path`, has its `Write` denied by the worktree gate.
    - The docstring (`:7914-7918`) names the three controls by heading position, with no ordinals.

    Once the name joins `ZERO_ARGUMENT_ALLOWED_SCRIPT_NAMES`, row 26's test pins the bare script line. Together the two tests pin both the run step and the rewrite step.
    - A second param, `reviewer-findings-fallback`, pins "re-dispatch it once with no `findings_path`" file-wide with `heading=None`, since that clause is the only fail-closed rule for a reviewer with no retrievable findings. Its consequence text names the bug: a reviewer whose write never happened is counted as having reviewed, so its findings are silently absent from the review.
    - A third param, `reviewer-findings-handed-path`, pins "`Read` only the path you handed that reviewer" file-wide with `heading=None`, so a reviewer-reported path is never followed. Its consequence text names the bug: a steered reviewer's pointer line moves the session's `Read` to a file of its choosing.
    - The docstring's "names the three controls" becomes five.
    - A separate test asserts that `code-review/SKILL.md` and `plan-review/ROUTING.md` still contain `findings_path: agent-reviews/`, so a dispatcher reword that would make the override's "replace the leading `agent-reviews/`" a silent no-op fails visibly.
    - No param pins `:72`: it is a wording fix, not a retained control.
    - Prose-only, accepted: the script-failure fallback clause with its quoted-stderr notice, and the re-dispatch clause's "naming any path it reported" words. A reword that drops the fallback degrades to the dispatcher's relative path, which the main-tree gate denies, and the reviewer's inline fallback or the pinned re-dispatch clause then catches it.
    - Verification step 3's mutation checks are the accepted substitute for test-first ordering.

    `anchors: row23` `[verified: test_skills.py:7884-7928; test_review_pr_post.py:1200]`
31. `test_skill_citations_resolve_to_real_headings` checks row 23's citation. A bare `REFERENCES.md` target resolves as a sibling of the citing file. The new heading uses plain words, with no backticks, slashes, or parentheses. `anchors: row32` `[verified: carried from the prior draft's row 16, test_skills.py:3590-3689]`
32. **Mechanism:** `REFERENCES.md`'s "Why spawned reviewers return findings inline" section (`:389-406`) is replaced by "Why spawned reviewers write findings to a temporary directory". A Known-gaps bullet goes after the abandoned-session bullet (`:436-444`). Per-account paths are written `<config-dir>/…`, never with a `~/.claude/` prefix (`TestPerAccountStatePathContract`, `test_skills.py:7088`). `anchors: row22` `[verified: REFERENCES.md:383-444; test_skills.py:7088]`
33. **Mechanism:** a `docs/scripts.md` bullet for the new script after `:177`, in the shape of the `review-pr-findings-path.sh` bullet. `anchors: row10` `[verified: docs/scripts.md:175-181]`
34. **Mechanism:** the `CHANGELOG.md` bullet at `:409` is rewritten in place. It is the first bullet under `### Fixed` (`:407`), inside `[Unreleased]` (`:5`). It keeps its Step 2 sentence and adds a re-stow instruction in the form `:355` uses for a new file. A `git pull` alone does not link a new script when the active config dir's `scripts/` is a real directory (`:58`, `:158`). `anchors: row10` `[verified: CHANGELOG.md:5, :58, :158, :355, :407-409]`
35. **PR #1289 artifacts:**
    - The rework lands as new commits on this branch. No history rewrite, no force-push.
    - After the last rework commit, `/pr-description` rewrites the body and the PR title, because the approach they describe changed (`CLAUDE.md` § "Shipping"). The title is checked against the merge method's use of it. The body references `#1236` with no closing keyword (no `Fixes`, `Closes` or `Resolves`), because the previous PR's `Fixes` closed the tracker while most of its items were open.
    - The branch's review-ledger decisions are not edited by hand. The next `/code-review` round renders them (`code-review/SKILL.md:300`) and dispositions any whose cited text this rework removes.
    - The body refresh lands before or with the first push of the rework, not after the last commit. The body still describes the inline design and says a `/tmp` path outlives the session, so it is false from that push on (`CLAUDE.md` § "Shipping").
    - Before each push, `/ready-for-review` runs, and open PR comments get a response before the PR is presented as ready.
    - After merge, with the engineer's go-ahead, the session edits `#1236`'s body. P1 was ticked in the prior session except P1.3 (not re-checked here). At merge, the edit ticks P2.2's `SKILL.md:72` half, adds the audit-stop-cause bullet, and adds one open bullet for each of these deferrals: claimed-write handling inside the dispatchers, deny tests for the two write gates, the orphan sweep, and the step-8 omission note with the fallback notice. Every other Out-of-scope bullet gets none. Only P2.1's `findings_path` tick waits for Verification step 7's report. The edit keeps P2.1's `AUDIT_FINDING` half open. P2.2's second half, listing the exit-3 causes in `SKILL.md`, is declined here because the checkout script's usage text owns them (`review-pr-checkout.sh:103-107`) and `SKILL.md:51` already lists the trust-class and cross-repo causes; the audit-stop cause is the only one absent, so the edit adds a bullet to the tracker rather than closing the item. P2.1's "name the denying hook" is satisfied by reading the hook source, not by re-running the incident. `[unverified: source reading in place of the tracker's "from evidence" is the plan's substitution; the engineer decides it at the tracker-edit go-ahead]`
    `anchors: root` `[unverified: the tracker's closing history is carried from the prior draft's row 21]` The next `/code-review` round dispositions any review-ledger row whose cited text this rework removes. The engineer is told which decisions this rework supersedes.
## Critical files

**Dispatch 1: script, helper, cleanup.** `code-writer`, run first.
- `claude/.claude/hooks/_lib.sh`: the row 12 helper, after `:984`. `:923` is untouched. One-line comment stating one fact.
- `claude/.claude/scripts/review-pr-reviewer-findings-dir.sh` (new, git mode 100755, `#!/usr/bin/env bash`), per row 10.
  - Copy `review-pr-findings-path.sh`'s usage, argument-check, and `_lib.sh` bootstrap layout (`:8-43`), so code above the source still parses on bash 3.2 (`.claude/rules/shell-floor.md`).
  - The usage text states that the script takes zero arguments, prints the path, prints the same path on a re-run, and that `review-pr-finish.sh` removes the directory.
- `claude/.claude/scripts/review-pr-finish.sh`, per row 15: the helper call and `rm -rf --` after `:96`, and the usage-text addition in `:11-28`.
- `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`: one allow test for a reviewer `Write` to `/tmp/review-pr-reviewer-findings-<sid>/<agent>.md`, with a docstring naming `/review-pr` as a consumer of the `/tmp` arm, so a later narrowing of that arm (GH-1103) fails visibly. Place it beside the existing `/tmp/scratch/...` allow tests (`:93, :779`).
- `claude/.claude/settings.json`: `"Bash(~/.claude/scripts/review-pr-reviewer-findings-dir.sh)"` after `:26`. `permissions.ask` carries `Edit(//**/.claude/settings*.json)` (`:44`), so the orchestrating session makes this one edit inline; the `code-writer` dispatch does not.
- `claude/.claude/hooks/tests/test_lib.py`: one class after `TestLibReviewPrWorktreeNameIsReviewShaped` (starts `:7906`; the next section marker is at `:7990`):
  - A valid id with `TMPDIR` exported elsewhere prints the exact `/tmp/review-pr-reviewer-findings-<sid>`.
  - An empty id and `../x` return 1 with empty stdout.
- `claude/.claude/scripts/tests/test_review_pr_reviewer_findings_dir.py` (new), modeled on `test_review_pr_findings_path.py`:
  - Any argument exits 2.
  - The happy path prints the exact path, the directory exists, and a second run prints the same path.
  - A regular file planted at the path makes `mkdir -p` fail: exit 1, empty stdout, stderr names the path.
  - A session-id failure exits 1.
  - Each test uses a `uuid4` session id, and a yield fixture removes the path. These tests need a writable, unsandboxed literal `/tmp` and fail with a message naming that precondition.
- `claude/.claude/scripts/tests/test_review_pr_finish.py`: a new class beside `TestArtifactAndMarkerRemoval` (`:203`):
  - The session's directory with a file inside is removed, and another session's directory survives.
  - Each test mints a `uuid4` session id and removes its directory in a finalizer, so parallel `-n auto` workers sharing the real `/tmp` never collide. The class's docstring states the writable, unsandboxed `/tmp` precondition.
  - An undeletable directory uses the existing `_rm_shim_that_fails_on_recursive_delete` (`:158`): exit 0, stderr names the path, and the artifacts are still removed.
  - The docstring of the worktree fallback test (`:330`) changes "finish's only `rm -rf`" to "the worktree sweep's only `rm -rf`".
- `docs/scripts.md`: row 33's bullet.

Dispatch 1 verification:
- `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. A `_lib.sh` change is expected to widen the selection; let it.
- `scripts/list-shell-files.sh | xargs -0 ../../../../.venv/bin/shellcheck` is clean (`git add` new files first).
- `../../../../.venv/bin/ruff check` on every test file this dispatch touches is clean.

**Dispatch 2: skill prose and pins.** `code-writer`, after dispatch 1 returns. Its brief needs the script's name, what it prints, that a re-run prints the same path, that finish removes the directory, and the probe's measured results (Verification step 6).
- `claude-skills/skills/review-pr/SKILL.md`: rows 23, 26, 27. Leave `:72` untouched.
- `claude-skills/skills/review-pr/REFERENCES.md`, per row 32. New section, one fact per sentence:
  - `/code-review` and `/plan-review` give each reviewer a relative `findings_path` under `agent-reviews/`, which resolves in the tree the session stands in.
  - A `/review-pr` session may stand in the main tree, and diff-only mode has no review worktree, where `require-worktree-for-file-writes.sh` denies that `Write`.
  - `review-pr-reviewer-findings-dir.sh` creates the session's directory and prints its path. The session puts that directory in place of `agent-reviews/` in each reviewer's `findings_path`.
  - The path starts with a literal `/tmp/`, because `deny-reviewer-tree-mutation.sh` allows a reviewer `Write` under `/tmp` as written, and `TMPDIR` can point elsewhere.
  - `/plan-review` does not hold a reviewer given a `findings_path` to its 2K-token inline budget. Whether `/code-review` caps its reviewers the same way is not established.
  - The session still reads a findings file in full when its count is non-zero, so files do not shrink context for reviewers that found something.
  - The design assumes one user per machine. Another reviewer of the same round, or another session of the same user, can read and write the directory.
  - A reviewer whose reported write has no file behind it, or whose reported path differs from the handed-out one, is re-dispatched once with no `findings_path`. The session `Read`s only the path it handed out.
  - A path under `<config-dir>/.review-pr-active.d/` fails, because `deny-reviewer-tree-mutation.sh` denies a reviewer `Write` there.
  - Exempting an ignored `agent-reviews/` in the main-tree gate fails. That gate is tiered `irreversible`.
  - Known-gaps bullet: **A session that ends without `review-pr-finish.sh` leaves its reviewer findings directory under `/tmp`.** The stale-marker sweep never reaches `/tmp`. The bullet gives one manual removal command: `find -H /tmp -maxdepth 1 -type d -name 'review-pr-reviewer-findings-*' -mmin +1440 -exec rm -rf -- {} +`. macOS path handling is unverified.
  - Use no PR-defined terms, no past-tense narration and no line-number citations (`CLAUDE.md` § "Durable text").
- `claude-skills/skills/tests/test_skills.py`: row 30.
- `claude/.claude/scripts/tests/test_review_pr_post.py`: add `"review-pr-reviewer-findings-dir.sh"` to `ZERO_ARGUMENT_ALLOWED_SCRIPT_NAMES` (`:1200`), in dispatch 2 because the test also needs the bare `SKILL.md` line.
- `CHANGELOG.md`, per row 34. The rewritten bullet says:
  - `/review-pr` now gives each reviewer a `findings_path` inside a per-session directory under `/tmp`, so findings files no longer land in the tree the session stands in.
  - From the main tree, `require-worktree-for-file-writes.sh` denied each reviewer's `agent-reviews/` findings-file `Write`.
  - The new zero-argument `review-pr-reviewer-findings-dir.sh` creates the directory, `settings.json` allows it, and `review-pr-finish.sh` removes it.
  - A reviewer that reports a write with no file behind it is re-dispatched once for inline findings. A failed script run falls back to inline findings.
  - Step 2's stop rule names `review-pr-diff.sh`.
  - Re-run whichever installer stows the packages `claude/.claude/scripts/stow-packages.sh` lists (`./install.sh` when the config dir is `~/.claude`) if the config dir's `scripts/` is a real directory. Until then `/review-pr` falls back to inline reviewer findings and says so.
  - The bullet drops "The change is live on `git pull` with no re-install", which is false once a new script ships.
  - Findings files live under `/tmp` and finish deletes them. The design assumes one user per machine.
  - The bullet states the permission mode the probe ran under and that other modes are untested.
  - macOS path handling is unverified.
  - Use no line-number citations.

Dispatch 2 verification: Verification steps 1-4, plus `../../../../.venv/bin/ruff check` on `test_skills.py` and `test_review_pr_post.py`.

- `.claude/plans/review-pr-diff-only.md`: this plan, committed per `plan-it` Step 7.

**Dispatch split.** The probe (Verification step 6) runs before dispatch 1: its observables decide whether the design ships and feed dispatch 2's copy. The dispatches run in sequence: dispatch 2's prose names dispatch 1's script, and its tuple entry needs dispatch 1's settings entry. After dispatch 2, the session runs `/code-review` once over the combined diff, plus `/skill-review` on the `SKILL.md` diff (`require-skill-review.sh` enforces it before commit).

**Reuse:** the session-id resolver, `_lib_valid_session_id_component`, the test shapes of `test_review_pr_findings_path.py`, and the existing tripwire, zero-argument, prompt-gated, and citation tests (rows 26, 29-31).

## Verification

The worktree has no `.venv`. Run every command from the worktree root with the main checkout's venv at `../../../../.venv/bin/`.

1. `wc -l claude-skills/skills/review-pr/SKILL.md` prints 200, and `git show :claude-skills/skills/review-pr/SKILL.md | awk 'END{print NR}'` prints 200 for the staged blob. `git add` new files before any run in this step, because `scripts/list-shell-files.sh` reads `git ls-files`.
2. `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. A path it cannot map is a rule-table bug, not a reason to widen the run by hand. The passing run includes `TestReviewPrArtifactSuffixEnumerationsAgree`, `test_scripts_are_executable`, all five params of `test_review_pr_skill_carries_retained_control`, every param of `test_zero_argument_allow_entry_matches_every_literal_skill_md_invocation`, `test_no_allow_entry_matches_a_prompt_gated_script_in_any_argument_form`, `test_skill_citations_resolve_to_real_headings`, `TestPerAccountStatePathContract`, `test_shell_lib_function_liveness.py`, and the new tests.
3. Mutation check, run in the worktree and then restored from copies kept outside the repo (not `git checkout` or `git stash`); confirm `git diff` shows only the intended change. Each mutation must fail a test:
   - Delete the rewrite sentence from `:9`.
   - Delete the bare script line.
   - Change one word of the new `REFERENCES.md` heading.
   - Delete the re-dispatch clause, or the handed-path clause.
   - Reword `findings_path: agent-reviews/` in one dispatcher.
   - Remove finish's call to the removal.
   - Drop the helper's session-id validation.
   - Derive the path from `${TMPDIR:-/tmp}`.
   - Swallow the script's `mkdir` failure.
   - Drop finish's `||` guard around the removal.
4. `grep -n 'findings_path' claude-skills/skills/review-pr/SKILL.md` matches only `:9`. `grep -n 'refused here too' claude-skills/skills/review-pr/SKILL.md` prints nothing.
5. Run `/code-review`, and `/skill-review` on the `SKILL.md` diff. Re-run `/skill-review` after any later `SKILL.md` edit.
6. **Probe (row 19), before Dispatch 1,** run from the committed plan with one reviewer dispatch:
   - Run `mkdir -p /tmp/review-pr-reviewer-findings-probe`.
   - Dispatch one reviewer (`staff-sdet`, `model: sonnet`) on a two-line synthetic diff with `findings_path: /tmp/review-pr-reviewer-findings-probe/staff-sdet-probe.md`.
   - `Read` the file, then remove the directory.
   - Report observables only (no reviewed repository, PR, or session id): whether a permission prompt appeared for the `Write`, and separately for the `Read`; whether the file exists and holds the reviewer's findings; whether any hook denied anything; and the permission mode the probing session ran under.

   A pass says nothing for other permission modes, and the report says so. If a per-reviewer prompt appears, stop and raise it to the engineer. A `Write(/tmp/...)` allow rule is barred (no wildcards in `permissions.allow`). The compliant exits are shipping with a CHANGELOG warning naming the permission mode, or abandoning the design.
7. **Post-merge check, on the first real `/review-pr` from the main tree of a repo with worktree discipline on.** Report observables only, with the host OS:
   - Reviewer prompts carry a `findings_path` under `/tmp/review-pr-reviewer-findings-<session id>/`.
   - No new `agent-reviews/<agent>-*.md` reviewer file appears in the main tree. The ledger render file still can, per Out of scope.
   - After `review-pr-finish.sh`, the directory is gone.

   No automated check covers whether a live session obeys the override.

## Out of scope

- **The `AUDIT_FINDING` disclosure check on `marker.sh write review-pr`.** It gets its own plan and branch (row 4).
- **Editing `code-review/SKILL.md:300` or `plan-review/ROUTING.md:50`.** The `/review-pr` exception lives in its own override (row 23).
- **The review-ledger render into the main tree's `agent-reviews/` (`_review-ledger-lib.sh:784-828`), and `findings-path-suffix.sh`'s `info/exclude` append.** Both are Bash writes that no file-write gate sees, and this change does not make either worse.
- **Claimed-write handling inside `/code-review` and `/plan-review` themselves**, beyond `/review-pr`'s re-dispatch clause.
- **Deny tests for the two write gates** (a main-tree `agent-reviews/*` `Write` against `require-worktree-for-file-writes.sh`, and a reviewer `Write` under `.review-pr-active.d/` against `deny-reviewer-tree-mutation.sh`).
- **A sweep for directories that crashed sessions leave behind.** A Known gap with a manual removal command, by the engineer's choice (row 5e). `marker-clear-stale.py` is the natural home for a later sweep.
- **GH-1103.** `deny-reviewer-tree-mutation.sh:84-98` documents a symlink and hard-link laundering gap in the `/tmp` arm. Every `/review-pr` reviewer write now depends on that arm, so a structural fix must account for it. The allow test in Critical files makes a narrowing fail visibly.
- **Surfacing a reviewer's omission note at step 8, and the fallback notice beyond the `SKILL.md:9` clause.**
- **Round-state accounting.** `log-reviewer-round.sh` has no `review-pr` exemption, so a `/review-pr` session's reviewer spawns and the one-time re-dispatch count toward the branch's round cap.
- **Pointing the step-7 consult at reviewer findings files.**
- **Running `/review-pr` under Claude Code's sandbox** (row 21).
- **`SKILL.md:60`'s "the script's exit 3", and listing exit-3 causes in `SKILL.md`,** which the checkout script's usage text owns (`review-pr-checkout.sh:103-107`).
- **Row 35's deferrals** become open bullets on tracker #1236 after merge. None is filed separately unless the engineer asks.
