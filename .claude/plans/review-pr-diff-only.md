# GH-1236 P2 (docs part): review-pr diff-only fixes

## Context

Goal: stop `/review-pr`'s reviewer findings-file writes from failing silently, and remove the ambiguity in Step 2's exit-status wording, with prose-only edits that add no physical lines to `claude-skills/skills/review-pr/SKILL.md` (at its 200-line cap).

Ask: "yes reopn and tick P1 then plan-it on P2" — scope narrowed to the docs part of P2 via label "Two branches: docs first (Recommended)". On whether P2 should also add a mechanical `AUDIT_FINDING` check, the engineer said "I'm not sure. Ask the architect." and then "Have fable do an adversarial review on the architect's design".

Tracker #1236 collects `/review-pr` fixes. Its P2 group, "Diff-only mode" (from #1233 problem 1), has two items. P2.1: a reviewer's `Write` of its `findings_path` file is denied, and one reviewer reported a successful write that never happened. P2.2: Step 2's wording reads as contradictory. Line 58 says exit 3 from `review-pr-checkout.sh` means "switch to the diff-only path"; line 72, under the diff-only heading, says "Any non-zero exit status, including from a re-run, is final" without naming the script it governs.

Session findings, as evidence for the design:
- The denial comes from `claude/.claude/hooks/require-worktree-for-file-writes.sh`, which denies `Write` to a path in the main working tree of a repo with worktree discipline on, in any mode.
- A checkout-mode session standing in the main tree is denied the same way. `/code-review` (Step 5) and `/plan-review` (Step 3, via `plan-review/ROUTING.md:50-54`) both pass `findings_path`.
- A reviewer given no `findings_path` returns its findings inline (`code-review/SKILL.md` ~:304-306).
- `review-pr-diff.sh` exits only 0 or 2. `review-pr-checkout.sh` exits 3 for every refusal that names the diff script (its usage text lists the causes).
- An architect consult recommended also adding a mechanical `AUDIT_FINDING` disclosure check to the `marker.sh write review-pr` path. An adversarial read advised landing that check as its own PR. The engineer chose to plan the docs part first. The check is out of scope here and gets its own plan and branch.

Intended outcome: one sentence in `SKILL.md`'s standing-override paragraph telling the session to pass no `findings_path` to any reviewer that `/plan-review` or `/code-review` spawns; `SKILL.md:72` naming `review-pr-diff.sh` as the script whose exit status is final; the stale "too" in that sentence dropped; and any docs that state either contract updated to match.

## Approach

Add one sentence to the standing-override paragraph in `/review-pr` (`SKILL.md:9`) telling the session to pass no `findings_path` to any agent the overridden skills spawn. Every reviewer then returns its findings inline. No reviewer attempts a `Write` that a worktree gate can deny, and none can claim a write that never happened. Separately, rewrite `SKILL.md:72` so it names `review-pr-diff.sh` as the script whose non-zero exit is final, and drop the stale "too" from its 300-file sentence. Both edits extend existing physical lines, so `SKILL.md` stays at 200 lines. The reason for the inline choice goes in a new `review-pr/REFERENCES.md` section that the new sentence cites, and the existing review-pr tripwire test gains one parameter.

**Inline vs. a relocated findings file (an explicit choice).** Inline output is the reviewer contract's own documented off switch (row 6), so it needs no new path, cleanup, or gate change. The alternatives were weighed and set aside:
- **An absolute `/tmp` path.** By reading, both write gates admit it (row 10). It still needs a per-session directory step and replaces `/code-review`'s path template instead of switching the contract off. Third-party PR findings would outlive the session, because `review-pr-finish.sh` does not remove them. Making it remove them is a script change outside this docs-only branch (row 1). It also keeps the claimed-write failure shape.
- **A path under `<config-dir>/.review-pr-active.d/`**, beside the session's other files. `deny-reviewer-tree-mutation.sh:364` denies a reviewer `Write` there (row 11).
- **Exempting `agent-reviews/` in the main-tree gate.** Barred by G1.
- **Requiring `/review-pr` to run from a linked worktree.** This contradicts the main-tree use the skill endorses (row 4) and adds a worktree per review with no review benefit.
- **Pointing findings into the step-2 review worktree.** Diff-only mode has no worktree, so the rule would split by mode. The write would also land in the PR's own checkout, against the override's "change nothing".
- **A Step 5–only sentence.** It misses step 3's `/plan-review` arm, which passes paths the same way (`plan-review/ROUTING.md:50`). The standing override already reaches both steps and every skill they invoke.

The cost accepted is the inline budget and the larger parent context (row 9).

**Step 2 wording.** `:58` governs `review-pr-checkout.sh`'s exits. `:72` is the only sentence in Step 2 with no subject script, so naming `review-pr-diff.sh` there removes the contradiction. `:60`'s "the script's exit 3" stays as is, because the same bold label names the checkout script. Exit-3 causes stay out of `SKILL.md`: the checkout script's usage text owns them (`review-pr-checkout.sh:103-107`), and `docs/scripts.md:173` already defers to it.

**Assumption ledger**

Root: `/review-pr` loses reviewer findings when a reviewer's `findings_path` write is denied, and once a reviewer falsely reported such a write as done. Separately, `SKILL.md:72`'s "Any non-zero exit status … is final" names no script, so it reads as overriding `:58`'s exit-3 switch to the diff-only path.

Givens:
- **G1 — the main-tree write gate cannot be relaxed for this.** `require-worktree-for-file-writes.sh` denies `Write` to a main-tree path in a repo with worktree discipline on (`:170-172`). Its header tiers it `cooperative, irreversible` (`:3`). `CLAUDE.md` § "Hook threat model" says a gate that lists `irreversible` "is never relaxed on false-positive cost alone." The repo rule owns this, not this plan. `[verified: require-worktree-for-file-writes.sh:3, :170-172; CLAUDE.md Hook threat model]`
- **G2 — `review-pr/SKILL.md` is at its line cap.** `check-skill-length.sh` caps it at 200 lines and denies a commit that grows a file past that. Raising the cap is a separate decision outside this plan. `[verified: check-skill-length.sh:6-8, :111-119]`

Rows:
1. This branch ships the docs part of P2 only. The `AUDIT_FINDING` check is not here. `anchors: root` `[engineer-verified: "Two branches: docs first (Recommended)"]`
2. The option text under that label ("docs only, zero new SKILL.md lines … The AUDIT_FINDING check gets its own plan and branch next") is the session's proposal. Nothing rests on it alone, because G2 forces zero new lines independently. `anchors: row1` `[unverified]`
3. Both dispatchers the override reaches pass a relative path. `code-review/SKILL.md:300` says "When spawning any reviewer, pass `findings_path: agent-reviews/<agent-name>-<suffix>.md`", and `plan-review/ROUTING.md:50` uses the same template. `anchors: root` `[verified: code-review/SKILL.md:300; plan-review/ROUTING.md:50]`
4. A `/review-pr` session may stand in the main tree: `SKILL.md:150` says "from any tree of the repo", and `:196` says "Run it from the main tree or from a worktree that is not a review worktree". A relative `findings_path` from such a session lands in the main tree, where G1 denies it. `anchors: G1` `[verified: review-pr/SKILL.md:150, :196]`
5. The P2.1 denials came from G1's gate rather than `deny-reviewer-tree-mutation.sh`'s check-ignore arm (`:350`). This is tracker and session evidence, not re-run. The design does not depend on which gate denied, since no write is attempted. `anchors: root` `[unverified]`
6. The file contract is opt-in. Per `code-review/SKILL.md:304`, "It activates only when `findings_path` is present in the prompt, so reviewers not passed one continue to return findings inline." Each agent's section ends "When `findings_path` is absent, ignore this section and use the **Inline output** format." `anchors: root` `[verified: code-review/SKILL.md:304; claude/.claude/agents/ciso-reviewer.md:131]`
7. **Mechanism:** append one sentence to `SKILL.md:9`, after the bold report-only sentence: "Pass no `findings_path` to any reviewer that `/code-review` or `/plan-review` spawns, so each returns its findings inline (`REFERENCES.md` § "Why spawned reviewers return findings inline")." It names both dispatchers, so a session reading the dispatcher's own later `findings_path` instruction can tell this sentence applies to it. The existing pinned phrase "report findings, change nothing, write no completion marker, edit no PR body" stays intact. The rule is also restated at its two points of use, inside existing physical lines: a short clause at `SKILL.md:78` (the `/plan-review` invocation) and at `:86` (the `/code-review` invocation), each reading "pass no `findings_path` (standing override)". `anchors: row6` `[verified: review-pr/SKILL.md:9; test_skills.py:7802]`
8. The session obeys a caller override that contradicts `code-review/SKILL.md:300`'s literal instruction. This is the same reliance the existing no-fix and no-marker overrides at `:9` already carry. Row 14's tripwire pins only that the text is present, not that the session obeys it. `anchors: row7` `[unverified]`
9. **Cost of inline output.** A reviewer with no `findings_path` is held to ≤2K tokens, and the dispatcher puts that limit in its prompt. `plan-review/ROUTING.md:54` says so explicitly. I read `code-review/SKILL.md:306`'s "Other spawned specialists" as the same set. An inline return runs about 4,500–7,800 B per reviewer, against about 650 B for a pointer line (`docs/design-decisions/reviewer-findings-path-output.md:5`). Accepted for three reasons: step 7 synthesizes one tiered body anyway; a reviewer over budget drops lower-severity items with an explicit note; and the failing configuration already ends inline today through the denied-write fallback (`code-review/SKILL.md:304`). `anchors: row7` `[verified: files cited; the code-review:306 reading is an inference]`
10. Both write gates admit a reviewer `Write` under `/tmp`. `deny-reviewer-tree-mutation.sh:288` reads `/tmp/*) exit 0`, and `require-worktree-for-file-writes.sh:131-132` exits 0 outside any git repo. I read this, I did not run it, and it grounds only a rejected alternative. `review-pr-finish.sh` removes only provenance, the body, the diff, the context backstop, and the marker (`SKILL.md:197`). `anchors: row7` `[unverified: behavior read at those lines, not executed; SKILL.md:197 verified]`
11. `deny-reviewer-tree-mutation.sh:364` denies a reviewer `Write` "outside /tmp and outside an agent-reviews/ findings path", which rules out a path under the config directory. `anchors: row7` `[verified: deny-reviewer-tree-mutation.sh:288-364 by reading]`
12. `review-pr-checkout.sh` exits 3 only for a positive refusal that names `review-pr-diff.sh` (`:13-17`, `:103-107`). `review-pr-diff.sh` has no `exit 3`. Its explicit failure exits are all `exit 2`, and it runs under `set -euo pipefail` (`:10`), so an unguarded command failure can surface some other non-zero status. That is why "Any non-zero" stays rather than "exit 2". `anchors: root` `[verified: grep of exit statements in both scripts]`
13. **Mechanism:** `SKILL.md:72` becomes: "Any non-zero exit status from `review-pr-diff.sh`, including from a re-run, is final: report stderr and stop. Use no other acquisition route. `review-pr-diff.sh` refuses a PR over 300 changed files." `anchors: row12`
14. Only `review-pr-diff.sh` caps a PR at 300 files (`:174-175`). `REFERENCES.md:206-207` says "The 300-file precheck and the truncation caveat apply to this path only", and grepping `review-pr-checkout.sh` for `300` finds nothing. So "too" at `:72` implies a checkout cap that does not exist. `anchors: row13` `[verified]`
15. **Mechanism:** `test_review_pr_skill_carries_retained_control` (`test_skills.py:7798-7831`) gains a `pytest.param` with id `no-findings-path`. It pins "Pass no `findings_path` to any reviewer that `/code-review` or `/plan-review` spawns" with `heading=None`, so it is checked across the whole file like the standing-override param. The param carries its own failure message naming the bug it guards: a reviewer given a `findings_path` from the main tree has its findings-file `Write` denied. Its docstring (`:7816-7820`) is updated to name three controls and to describe each by its heading position, with no ordinals. No param is added for `:72`: a phrase pin on a wording fix guards no control, and the repo has no harness that tests how a session reads a sentence. `anchors: row7` `[verified: test_skills.py:7795-7831]`
16. The existing `test_skill_citations_resolve_to_real_headings` checks row 7's citation, so a renamed or missing `REFERENCES.md` heading fails CI. A bare `REFERENCES.md` target resolves as a sibling of the citing file (`test_skills.py:3625-3628`). The heading avoids backticks and parentheses so it normalizes cleanly. `anchors: row17` `[verified: test_skills.py:3590-3689]`
17. **Mechanism:** a new `REFERENCES.md` section, `## Why spawned reviewers return findings inline`, placed between `:387` and `## Known gaps and operator choices` (`:389`). `SKILL.md` has no line left for rationale (G2). `REFERENCES.md:383` ("Why the findings-body declaration uses the Write tool, not Bash (Step 7)") is the precedent for a review-pr "why" section. `anchors: G2` `[verified: REFERENCES.md:383-389]`
18. No other doc states either contract in a way this change makes false:
    - `docs/scripts.md:173` and `:175` already state each script's exit contract. The diff script's entry reads "Exits 2 on every refusal or failure, including a PR over 300 changed files".
    - `code-review/SKILL.md:300-304` and `plan-review/ROUTING.md:50-54` stay true for their own runs, because the exception lives in the caller's override.
    - `docs/skills.md:14`, `:41` and `:76` are one-line role rows.

    `anchors: root` `[verified]`
19. No test pins `SKILL.md:72`'s current wording. Grepping every `test*.py` for "non-zero exit", "acquisition route", "refused here", "is final", and "300 changed" finds no review-pr hit. `anchors: row13` `[verified]`
20. **Mechanism:** a `CHANGELOG.md` bullet under `### Fixed` (`:378`, under `[Unreleased]`). It states the operator-visible consequence: every spawned reviewer now returns its findings inline, within the 2K-token budget the dispatcher states, including from sessions where the file write used to succeed. `anchors: root` `[verified: CHANGELOG.md:5, :378]`
21. **Mechanism:** the PR body references `#1236` without a closing keyword (no `Fixes`, `Closes` or `Resolves`), because the merged previous PR's `Fixes` closed the tracker while most of its items were open. After merge, the session edits `#1236`'s body (with the engineer's go-ahead): tick P2.1's findings_path half and P2.2, keep P2.1's `AUDIT_FINDING` half open as its own item, and add one open bullet per deferral listed under Out of scope. `anchors: root` `[verified: #1236 closed at the previous PR's merge, observed this session]`

## Critical files

- `claude-skills/skills/review-pr/SKILL.md`
  - `:9`: append row 7's sentence after the closing `**` of the bold sentence.
  - `:78` and `:86`: add row 7's point-of-use clause inside the existing line.
  - `:72`: replace with row 13's text.
  - The physical line count must not change.
- `claude-skills/skills/review-pr/REFERENCES.md`: add the new section after `:387`. Content, one fact per sentence:
  - `/code-review` and `/plan-review` give each reviewer a relative `findings_path` under `agent-reviews/`, which resolves in the tree the session stands in.
  - A `/review-pr` session may stand in the main tree, where `require-worktree-for-file-writes.sh` denies that `Write` in a repo with worktree discipline on.
  - A denied reviewer is meant to fall back to inline output, but a reviewer can report a write that never happened, and its findings are then lost.
  - With no `findings_path`, no reviewer writes, and no findings file lands in a review worktree.
  - The cost is each reviewer's 2K-token inline budget, a larger session context, and no by-path citation of a findings file in the step-7 consult. It applies to every `/review-pr` session, including one where the file write would have succeeded.
  - Two other locations fail here. A path under `/tmp` passes both write gates, but `review-pr-finish.sh` does not remove it, so a PR's findings outlive the session, and a world-readable directory is open to a symlink planted in advance. A path beside the session's other `.review-pr-active.d/` files is denied, because `deny-reviewer-tree-mutation.sh` allows a reviewer `Write` only under `/tmp` or an `agent-reviews/` directory.
  - Name the per-account state path as `<config-dir>/.review-pr-active.d/`, never with a `~/.claude/` prefix (`TestPerAccountStatePathContract`).
  - No PR-defined terms and no past-tense narration (`CLAUDE.md` § "Durable text").
- `claude-skills/skills/tests/test_skills.py`: the new param and docstring update in `test_review_pr_skill_carries_retained_control` (`:7798-7820`), per row 15.
- `CHANGELOG.md`: a new first bullet under `### Fixed` (`:378`). It should say:
  - `/review-pr` now gives spawned reviewers no `findings_path`.
  - From the main tree of a repo with worktree discipline on, each reviewer's findings-file `Write` was denied, and a reviewer could report a write that never happened.
  - Reviewers spawned under the standing override return findings inline, within the dispatcher's 2K-token budget, including in sessions where the file write used to succeed (row 20).
  - Step 2's stop rule names `review-pr-diff.sh`, so it no longer reads as overriding the checkout script's exit 3.
  - The change is live on `git pull` with no re-install.
- `.claude/plans/review-pr-diff-only.md`: the plan itself, committed per `plan-it` Step 7.

**Reuse:**
- The reviewers' existing inline output format (row 6).
- The existing tripwire test (row 15).
- The existing citation-resolution test (row 16).

No new script, hook, or helper.

**Dispatch split:** one `code-writer` dispatch covering all four files. The new sentence's exact text feeds both the test phrase and the `REFERENCES.md` heading, so a split would restate that shared context in every prompt. After it returns, the session runs `/code-review`. Because `SKILL.md` is staged, that also requires `/skill-review`, which `require-skill-review.sh` enforces before commit (`.claude/rules/review-pipeline-dispatch.md`).

## Verification

The worktree has no `.venv`, so every command below uses the main checkout's venv at `../../../../.venv/bin/` (run from the worktree root).

1. Run `wc -l claude-skills/skills/review-pr/SKILL.md`. It prints the same count as the base (200). Confirm the staged blob too: `git show :claude-skills/skills/review-pr/SKILL.md | awk 'END{print NR}'` prints 200.
2. Run `../../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. Its selection must include the `claude-skills/skills/tests` directory, and it must pass, including all three params of `test_review_pr_skill_carries_retained_control`, `test_skill_citations_resolve_to_real_headings`, `test_every_citation_shaped_construct_is_extracted`, and `TestPerAccountStatePathContract`.
3. Mutation check, run in the worktree and then restored. Before mutating, copy `SKILL.md` and `REFERENCES.md` to a scratch directory outside the repo, and restore from those copies afterward, not with `git checkout` or `git stash`.
   - Delete row 7's sentence. `../../../../.venv/bin/pytest claude-skills/skills/tests/test_skills.py -k test_review_pr_skill_carries_retained_control` must fail on the `no-findings-path` param only.
   - Change one word of the `REFERENCES.md` heading. `-k test_skill_citations_resolve_to_real_headings` must fail.
   - Restore both files from the copies and confirm `git diff` is back to the intended change.
4. Run `grep -n 'refused here too' claude-skills/skills/review-pr/SKILL.md`. It prints nothing.
5. Run `../../../../.venv/bin/ruff check claude-skills/skills/tests/test_skills.py`. It is clean.
6. Run `/code-review`, plus `/skill-review` on the `SKILL.md` diff (hook-enforced), and re-run `/skill-review` after any later `SKILL.md` edit. The drafted text is two clauses and one rewritten sentence, so the plan-time `/skill-review` pass is deferred to this diff-time pass, which the commit hook enforces anyway.
7. Named manual check, run on the first real `/review-pr` after merge and reported in the PR thread (it needs a real PR, so it is not run before merge). Two observables: no `Agent` dispatch prompt in the session carries `findings_path`, and no new `agent-reviews/` file appears in the main tree during the run. Whether a live session obeys the override (row 8) has no automated check, because the repo has no harness for how a skill's prose is followed.

## Out of scope

- **The `AUDIT_FINDING` disclosure check on `marker.sh write review-pr`.** It gets its own plan and branch (row 1).
- **Claimed-write handling in the other dispatchers** (`code-review/SKILL.md:302`, `plan-review/ROUTING.md:52`). Both `Read` the findings file when a reviewer reports success, and say nothing about a missing file, so a false success report loses findings there too. Outside `/review-pr`, a write is denied only when `agent-reviews/` is not ignored or the session stands in a main tree. `findings-path-suffix.sh` adds the ignore entry before every round (`code-review/SKILL.md:300`). Default taken: not widened here, because Critical files is the change's file limit and adding these two skill files needs the engineer's answer. I recommend it as a follow-up item on #1236: when the `Read` finds no file, re-dispatch that reviewer without `findings_path`.
- **The review ledger under `/review-pr`.** `code-review/SKILL.md:409` appends dispositions "on every invocation", and `:300` renders the ledger and passes it to every spawn. `review-ledger.sh` scopes that ledger to the session's own branch, or to the session on the default branch (`review-ledger.sh:55-56`, `:106-107`). Under `/review-pr`, that may mix a third-party PR's dispositions into the operator's own branch ledger. I noticed this while reading and did not trace it end to end, so it should be filed separately.
- **Deny tests for the two write gates** (a main-tree `agent-reviews/*` `Write` against `require-worktree-for-file-writes.sh`; a reviewer `Write` under a `.review-pr-active.d/` path against `deny-reviewer-tree-mutation.sh`). The invariant is pinned only by reading; adding tests belongs to a hook-test follow-up.
- **A fail-closed accounting rule for a reviewer dispatched with no retrievable findings**, so a missing findings file is never read as "no findings". This covers the case where the session does not follow the new sentence. It belongs with the claimed-write item above.
- **Persistence of `findings-path-suffix.sh`'s `info/exclude` append and the ledger rows written during a `/review-pr` run.** It is the same persistence concern as the `/tmp` rejection and this change does not worsen it.
- **Each deferral above is recorded as an open bullet on tracker #1236 after merge (row 21).** None is filed as a separate issue unless the engineer asks.
- **`SKILL.md:60`'s "the script's exit 3".** Unchanged, because the same clause names the checkout script.
- **Listing exit-3 causes in `SKILL.md`.** The checkout script's usage text owns them (`review-pr-checkout.sh:103-107`).
