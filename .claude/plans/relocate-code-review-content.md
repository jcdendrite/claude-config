# Make room in code-review/SKILL.md without raising its cap (GH-1079)

## Context

Goal: make room in `claude-skills/skills/code-review/SKILL.md` (511 lines against a 500-line cap; the cap hook denies growth, not the current size) so two compressed run-on paragraphs can be restored to explicit lists, without raising the cap.

Ask: GitHub issue #1079, "Relocate content out of code-review/SKILL.md instead of raising its 500-line cap" (relocation items 1-4; each can be its own change). Scope and destination were delegated: engineer answered "Ask the architect" and "Ask the architect." Re-scope of the issue's premise, answered via label "A: Shorten in place (Recommended)".

Why now: the cap is forcing run-on prose at the two-rule Always-spawn paragraph and in the `code-review-contradiction-route` region, and issues #1182 and #1187 both need line room in the same file.

## Approach

Restore both run-on paragraphs to lists by shortening `code-review/SKILL.md` in place, not by relocating content out of it. Every change to `code-review/SKILL.md` lands in one commit (Phase 1a) that leaves the file at no more than its current 511 lines. The commit-message recipe stays where it is, cut from 10 lines to 5, and four paragraphs that repeat or contradict rules the same file already states are deleted. `docs/skills.md` and the path-scoped skill-authoring rule each gain one line against flattening lists to fit a cap.

- **Scope** (delegated, "Ask the architect"): #1079 items 2 and 3 are in. Item 1 changes from relocation to in-place shortening. Item 4 is not selected.
- **Destination** (delegated, "Ask the architect."): there is no new destination. The recipe stays in `code-review/SKILL.md` § "Step — Record review completion", shortened. Moving it into the deny message of `enforce-marker-script-shape.sh` is rejected (rows 5–6).

**Re-scope of #1079's premise (resolved).** #1079 says to make room by relocating content. Its item 1 frees too few lines (row 3), and no other location reaches the block's reader before the message is written (rows 4–6). So funding item 2 needs a room source #1079 never names. Options were A (shorten in place), B (take item 4 now: move the Item ownership table, about 45 lines, to a runtime file; needs `check-skill-length.sh` changes and overrides #1079's ruling against runtime siblings), and C (item 3 only). The engineer selected "A: Shorten in place (Recommended)".

**Line budget for `code-review/SKILL.md`.** Line numbers are at HEAD `08fca56e`. Every edit shifts the lines after it, so locate each site by its text.
- C1, `:264` (the two Always-spawn rules): becomes a neutral lead line that adds no condition, a blank line, then one bullet per rule, with each rule's sentences kept verbatim. **+3**
- C2, `:377-378` (contradiction-route region): becomes 12 lines, split only at sentence boundaries and kept in order:
  - The start anchor.
  - A lead paragraph: the bold rule, the "Write `plan-architect — consult`" sentence, and the dispatch/relay sentence.
  - A blank line, then the site paragraph: "A site is…", "A finding's location is matched…", "A finding against a site an earlier verdict already settled…".
  - A blank line, then the standard paragraph, ending "…exactly one of the three verdicts per finding:" (the period becomes a colon).
  - A blank line, then three bullets: *Keep current text* (with its `--source` sentence), *Apply this round's fix*, and *Cannot choose*.
  - A blank line, then the no-explicit-verdict sentence with the end anchor inline, as today.

  **+10**
- S1, `:492-501` (commit-message block): becomes 5 lines.
  - One paragraph holds, in order:
    - The bold lead "**Authoring the commit message.**", unchanged.
    - The existing `-m` and `mktemp`/`Write`/`-F` recipe sentence, edited once to name the PII gate and the redaction gate (so "either gate" in the heredoc bullet keeps its referent) and to state that `-F` names a regular on-disk file, never `-`, `/dev/stdin` or `/dev/fd/*`, in any spelling.
    - The whole `:500` paragraph moved up verbatim: all three sentences, including the two that name the detectors and why they match the `-F` argument.
    - A closing "Never author the message either of these ways:".
  - A blank line.
  - The heredoc bullet and the `-m "$(cat …)"` bullet, both verbatim.
  - A blank line.

  The pseudo-file bullet (`:497`) is dropped, because its standalone guidance now lives in the recipe sentence's regular-file clause. All of these edits fit in the same paragraph line, so they cost no lines. **−5**
- S2, `:300-301`: delete. **−2**
- S3, `:434-435`: delete. **−2**
- S5, `:345-346`: delete. Move its "(see `docs/design-decisions.md` §16 for rationale)" pointer into `:343`, and carry `:345`'s "in-diff, tested code" scope qualifier into `:343` in the same sentence, at no line cost. **−2**
- S8, `:421-422`: fold `:422`'s two delimiter literals into `:420`'s sentence that introduces them. **−2**
- Net: **0**, so the file ends at the committed line count (511 at `08fca56e`). The cap gate allows that, because the file is not longer than its committed version.
- Reserve S6, `:302-303` (delete, **−2**): spend it only to bring a restructure that lands above the committed count back to at most that count. More than 2 lines above the committed count: stop and report.
- Acceptance is the gate's own criterion: the staged file's line count is at most the committed file's. The figure 511 holds only while `origin/main` stays at `08fca56e`, so the executor re-reads the committed count at dispatch time and again after any rebase (Verification item 1).
- Later edits to `code-review/SKILL.md` carry the same constraint. Any fix commit after Phase 1, a restored single site, or a rollback of one site must be net at most 0 against the new committed count, funded by a named deletion in the same commit. S6 is the first such offset, and spending it for a fix removes it from #1182's Phase 0. A whole-commit revert is length-neutral and needs no offset.

**Assumption ledger**

Root: `code-review/SKILL.md` is installed into every stow consumer's `~/.claude/skills/` and is 511 lines long. It cannot grow while it stays over its 500-line ratchet cap. Its two parallel-rule paragraphs (`:264`, `:377-378`) can become lists only if the same commit frees as many lines as the lists add, without moving guidance away from the reader it serves.

Givens:
- G1 — The cap gate's policy for this file is fixed:
  - The limit is 500 (`claude/.claude/hooks/check-skill-length.sh:113-114`).
  - The metric is lines, since `:137` passes no byte limit.
  - It denies a commit only when the staged count is over 500 **and** over the committed count (`claude/.claude/hooks/_lib.sh:1875-1878`, `:1995-2000`).

  Reason: #1079's design consult ruled out raising the cap or changing its metric, so changing either needs a decision outside this plan. `[verified: those lines; issue #1079 body]`
- G2 — The commit gates leave message content delivered by expansion unscanned: `$(cat file)` and substitutions inside an unquoted-delimiter heredoc (`claude/.claude/hooks/deny-pii-in-commits.sh:115-118`). A quoted-delimiter heredoc body is literal text in the command string and is scanned. The attached short form (`-F-`, `-Fpath`) and long-option abbreviations (`--fil=-`) are also allowed unscanned (`claude/.claude/hooks/deny-pii-in-commits.sh:200-204`). Reason: those gates own these gaps as documented decisions, and closing them is a separate hook change. `[verified: those lines]`
- G3 — No runtime sibling file for `code-review` (#1079: "Do not … add a runtime sibling skill to buy room"). Reason: #1079's consult decided this, and reversing it is item 4's decision, outside this plan unless the engineer picks option B. `[verified: issue #1079 body]`

Rows:
1. `[verified: code-review/SKILL.md, 511 lines by ripgrep line count; :264; :377-378]` The two run-ons are `:264`, two Always-spawn rules on one line, and `:377-378`, a 14-sentence region on one line. #1079 cites `:266`, but the paragraph now sits at `:264`. anchors: root
2. `[verified: check-skill-length.sh:6-9; _lib.sh:1875-1878, :1995-2000]` Each commit is measured against HEAD. At 511, any net growth is denied. A trim committed on its own lowers the next commit's ceiling to the larger of 500 and the new HEAD. So the trims and the list restructuring must land in **one** commit. anchors: root
3. `[verified: arithmetic over rows 1–2 and :492-501]` Hypothesis test of #1079's item 1: relocating `:492-501` frees 8 lines with a one-line pointer left behind, or 10 with none. Item 2 costs +13, giving 516 or 514 lines, which the gate denies. Even the cheapest version breaks even only by cutting C2 to +7 and removing the recipe with no pointer. That leaves the region's seven-sentence lead paragraph in place. anchors: row2
4. `[verified: code-review/SKILL.md:3, :490]` The recipe's reader already finds it at the moment of need. This skill is the commit gate, and its Record-review-completion step prescribes the commit itself (`marker.sh write code-review && git commit …`, `:490`). So the committing session reads the recipe right before writing the message. anchors: root
5. `[verified: enforce-marker-script-shape.sh:4-8, :685, :711-721; G2's lines]` The session's proposed destination fails for three reasons:
   - That deny fires only on a malformed `marker.sh` chain, the hook's second job (`:8`).
   - No commit gate scans `-m "$(cat …)"` or an unquoted-heredoc substitution. For a standalone commit and for `$(cat …)`, the agents the never-list protects are the ones whose commit succeeds unscanned, and they never see a deny. A chained heredoc or `-F - <<EOF` does reach the hook's deny, whose message already names the fix (`enforce-marker-script-shape.sh:631-663`, `:685+`, `:719-721`).
   - A deny arrives only after the message is written.

   anchors: row4
6. `[verified: enforce-marker-script-shape.sh:3; code-review/SKILL.md:277, :286; docs/skills.md:143; .claude/rules/skill-and-agent-self-review.md:23; pr-description/SKILL.md:214-222]` The hook is tier `cooperative, untrusted-input, irreversible`. Even a message-only edit therefore draws `claude-hook-review` plus the security-controls row's `ciso-reviewer` and `staff-sdet` spawns, which is heavier than the problem. Lighter options, each weighed:
   - Path-scoped rule: fails, because no file read happens before a commit, and `:143` limits relocation to a file-read moment of need.
   - Co-located runtime file: fails, because rule `:23` bars it as cap relief and G3 applies.
   - Global `claude/.claude/CLAUDE.md`: fails, because it loads in every session to serve a commit-time recipe.
   - `pr-description/SKILL.md`: fails, because it loads when a PR is written, not per commit.
   - Shorten in place: chosen.

   anchors: row5
7. `[verified: deny-pii-in-commits.sh:200-204, :505-512; deny-private-project-refs.sh:558-560, :581-586]` S1 drops the standalone pseudo-file bullet and folds a regular-file clause into the recipe sentence. The PII gate denies the spaced spellings (`-F -`, `-F /dev/stdin`, `--file -`, `--file=-`) for every commit with a message naming the fix. The redaction gate denies them only for a non-empty staged diff and only in this repo. Neither gate recognizes the attached form (`-F-`) or `--fil=-`, so prose is the only layer there, and the folded clause keeps it. The heredoc and `$(cat …)` bullets stay, because nothing scans those shapes (row 5). anchors: row4
8. `[verified: enforce-marker-script-shape.sh:719-721]` That hook's pointer ("code-review's SKILL.md, \"Authoring the commit message\", gives why") stays true. S1 keeps the bold lead and both reasons, so no hook file changes. anchors: row7
9. `[verified: :300 vs :230-235, :253]` S2: `:300` repeats the Spawn-decisions requirement and its empty-rationale warning. anchors: row2
10. `[verified: :434 vs :266, :270; early-return text is present in 10 files under claude/.claude/agents/]` S3: "fires reviewers per file-path domain detection" contradicts `:266` and `:270`. Its self-scoping sentence repeats behavior each reviewer's own file already describes. anchors: row2
11. `[verified: :345 vs :343]` S5: `:345` repeats `:343`'s "ADDRESS is the default". Its §16 pointer is kept. anchors: row2
12. `[verified: :420-422]` S8: `:422` exists only to list the delimiters `:420` introduces. anchors: row2
13. `[verified: grep of every text file in the repo for distinctive phrases of :264, :300, :302, :345, :422, :434, :492-501, ROUTING.md:28, ROUTING.md:78 — no test hits]` No test pins any trimmed or restructured line except the contradiction-route region. The pin compares whitespace-collapsed text, so it guards content only, never list structure, and the restored lists need the structural checks in Verification item 5. The one non-test consumer of `:492` is the unpinned pointer in `enforce-marker-script-shape.sh:720-721`. anchors: row2
14. `[verified: claude-skills/skills/tests/test_skills.py:5575-5609, :5619-5630, :4544-4551, :2441-2447]` C2 keeps every sentence verbatim and in order. The pin compares text with whitespace collapsed, by exact equality, so its update is one period-to-colon change and three `- ` insertions. The anchor-name set does not change. anchors: root
15. `[verified: :316-319]` C2's verdict list mirrors the round-cap consult region's three-verdict list. anchors: row14
16. `[verified: test_skills.py:4287-4294]` C1 and its sibling in `ROUTING.md` each open with an unindented lead line. In `ROUTING.md` that line also ends `_invalid_skip_rationale_labels`' scan of the list just above it (`ROUTING.md:20-26`), and it keeps markdown from merging the two lists. anchors: root
17. `[verified: plan-review/ROUTING.md:28, :30, :78; 109 lines]` Sibling arms:
    - `ROUTING.md:28` has the same two-rule run-on.
    - `ROUTING.md:78` has the same "per touched domain" sentence, which contradicts `ROUTING.md:30`.

    Both get the identical fix, per CLAUDE.md's "Audit structural siblings". `ROUTING.md` is under no cap pressure (109 of 500 lines). anchors: row1
18. `[verified: claude/.claude/agents/code-writer.md:66-69]` `code-writer` stops on a pin edit or a trim made to fit a cap unless the dispatch prompt directs it. So dispatch A's prompt names each trim and the pin edit as plan-directed. anchors: row2
19. `[verified: .claude/rules/skill-and-agent-self-review.md:1-8 paths frontmatter; this dispatch received that rule after a Read of a matching SKILL.md]` Item 3 lands in two places:
    - `docs/skills.md` holds the canonical cap guidance (`:143`).
    - The rule file gets a one-line cited pointer.

    `docs/skills.md` is not loaded when someone edits a skill. The rule loads on any Read of a `SKILL.md`, including from a subagent such as `code-writer`. anchors: root
20. `[unverified]` #1079's "within ~25 lines of its cap" threshold is dropped. Nothing grounds the number, and the right action is the same at any distance from a binding cap. anchors: row19
21. `[verified: row 2 arithmetic]` While the file stays over 500, trimming beyond this commit's own growth frees nothing for later commits, because each later commit's ceiling becomes the new HEAD. Extra trims would also use up deletions #1182's Phase 0 could otherwise spend. So this plan trims only what item 2 needs and holds S6 back. anchors: row2
22. `[verified: docs/skills.md:145; docs/design-decisions/ready-for-review-fix-loop-convergence.md:96]` Item 4 is not selected by default:
    - It is #1079's own last resort.
    - It contradicts G3.
    - Plan-review's precedent moved the same table out and pairs it with `require-routing-read.sh` and `log-routing-read.sh`.

    `[unverified]` whether moving code-review's table would need such hooks as well. anchors: root
23. `[unverified]` The session proposed scope items 1–3; this was the session's proposal, not the engineer's choice. This plan keeps items 2 and 3 and converts item 1 to in-place shortening (rows 3–7). anchors: root
24. `[unverified]` The session proposed the deny-message destination; this was the session's proposal, not the engineer's choice. It is rejected (rows 5–6). anchors: row5
25. `[engineer-verified: "Ask the architect"]` The engineer delegated the choice of which #1079 items this plan covers. anchors: root
26. `[engineer-verified: "Ask the architect."]` The engineer delegated the choice of the commit-message recipe's destination. anchors: root
27. `[verified: select-tests.py rule table, traced by the staff-sdet and staff-platform-engineer reviews]` For Phase 1's paths, `select-tests.py` selects the whole skills, hooks and scripts test directories plus the transcript-analysis tests. For Phase 2's `docs/skills.md` and `.claude/rules/*.md`, it selects the hooks and skills test directories. anchors: root
28. `[engineer-verified: "A: Shorten in place (Recommended)"]` The engineer selected shortening in place as the route for the re-scope of #1079's premise. The option's description was the session's relay of the architect's proposal, not the engineer's words. anchors: root
29. `[engineer-verified: "Keep in scope"]` The engineer kept the `plan-review/ROUTING.md` edits (restructure `:28`, delete `:78-79`) in scope when asked whether to keep them. anchors: row17
30. `[engineer-verified: "Keep in scope"]` The engineer kept the `.claude/rules/skill-and-agent-self-review.md` pointer in scope when asked whether to keep it. anchors: row19
31. `[engineer-verified: "Keep in scope"]` The engineer kept the four paragraph deletions (S2, S3, S5, S8, with S6 as reserve) as the room source when asked whether to keep them. anchors: row2
32. `[engineer-verified: "Accept the change"]` The engineer accepted dropping #1079's "within ~25 lines of its cap" threshold from item 3's wording. anchors: row20

## Critical files

All paths are repo-relative.

Every `.venv` path below is the worktree-relative form (`../../../.venv/bin/...`), because a linked worktree does not inherit `.venv` (README.md Tests section).

**Phase 1a — dispatch A (`code-writer`, one commit).**
- `claude-skills/skills/code-review/SKILL.md`: apply C1, C2, S1, S2, S3, S5 and S8, plus S6 only as the reserve (Approach, "Line budget").
- `claude-skills/skills/tests/test_skills.py`: in `_PINNED_CONTRADICTION_ROUTE_CLAUSE` (`:5575-5609`), exactly four token changes: "per finding." to "per finding:", and `- ` inserted before each of *Keep current text*, *Apply this round's fix* and *Cannot choose*. Change nothing else, including the comment above it. The pin guards content only, not list structure.
- The dispatch prompt must say:
  - The plan directs each trim and exactly those four pin-token changes (row 18). If the pin test still fails after them, stop and report; make no further pin edit.
  - Sentences moved by C1 and C2 stay verbatim, apart from the added lead lines, the one colon and the list markers.
  - Both files land in one commit (row 2).
  - Read the committed line count first (`git show HEAD:claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'`) and stop and report if the staged count would exceed it after the reserve.
  - Before each edit, check whether the site is already in its end-state form, and report each site as done or pending when stopping. After any stop, the parent compares `git diff --stat` to this plan before re-dispatching.
  - Verification Bash calls must not contain the literal two-word commit command: review-only agents' Bash calls containing it are denied even inside a grep pattern, so those agents search with the Read or dedicated search tool instead. The parent's own commit step keeps its command.
- Verification command: Verification items 1, 2, 3 and 5 below.

**Phase 1b — dispatch A2 (`code-writer`, a separate commit in the same PR).**
- `claude-skills/skills/plan-review/ROUTING.md`:
  - Restructure `:28` into a neutral lead line, a blank line and two bullets, keeping ROUTING's own wording. The lead line is unindented and does not start with `- ` or `* `.
  - Delete `:78-79`.
- ROUTING.md is at 109 of 500 lines, so the cap gate does not constrain it, and nothing couples it to Phase 1a. A defect there reverts without touching the cap-critical commit.
- Sentences moved by the restructure stay verbatim, apart from the added lead line and the list markers.
- Verification command: Verification items 1, 2 and 5 below.

**Phase 2 — dispatch B (`code-writer`, separate commit).**
- `docs/skills.md`: add this bullet directly after `:143`, verbatim or tightened without adding facts:
  `- **At a line cap, shorten by deleting restated text, never by flattening a list.** In a file written one paragraph per line, a parallel list costs more lines than the same items chained into one run-on paragraph. Free lines instead by deleting a paragraph whose rule is already stated canonically elsewhere in the same file. Count a gate as stating the rule only where it fail-closed denies the shape in every spelling the prose covers and its own header lists no known gap for it; otherwise the prose is the only layer and stays.`
- `.claude/rules/skill-and-agent-self-review.md`: add this paragraph after `:23`, inside "Skill and rule authoring conventions":
  `**At a line cap, delete text restated elsewhere in the same file — never flatten a parallel list into a run-on to fit.** See `docs/skills.md` § "Skill architecture notes".`
- Verification command: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`, then `grep -c '^## Skill architecture notes$' docs/skills.md` (expect 1, and the rule's cited heading text matches it exactly).
- The phases touch disjoint files. Run Phase 2 after Phase 1 by default, so each commit gets its own review round. Running them in parallel is safe only because the file sets don't overlap. Phase independence is a pre-merge property: if the PR squash-merges, main gets one commit, and independent post-merge revert of Phase 2 needs it in its own PR.

**Reuse:** C2 copies the round-cap consult region's verdict-list shape (`code-review/SKILL.md:316-319`). No new script, hook or helper.

**Deliberately untouched:**
- `claude/.claude/hooks/enforce-marker-script-shape.sh`
- `claude/.claude/hooks/check-skill-length.sh` and `claude/.claude/hooks/tests/test_check_skill_length.py`
- Every other hook

The plan file itself is exempt from this list.

## Verification

1. **Line counts** (Phase 1), with the gate's own counter:
   - Committed count: `git show HEAD:claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'` (511 at `08fca56e`; re-read after any rebase).
   - Staged count, after `git add`: `git show :claude-skills/skills/code-review/SKILL.md | awk 'END{print NR}'`. It must be at most the committed count. Use no `wc -l`: it disagrees with the gate when a file loses its final newline.
   - `git show :claude-skills/skills/plan-review/ROUTING.md | awk 'END{print NR}'` should print 110 for Phase 1b.
   - If the gate denies with "was 0", that is a HEAD-read timeout, not growth: retry the commit unchanged and do not trim.
2. **Scoped tests:** run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` after each phase. Everything it selects must pass: for Phase 1 that is the whole skills, hooks and scripts test directories plus the transcript-analysis tests. These are the tests most sensitive to the change:
   - `TestCodeReviewContradictionRouteRegionPin`
   - `test_disposition_rule_anchors_present`
   - `test_invalid_skip_rationale_labels_match_across_review_skills`
   - `test_findings_path_recipe_tokens_present_in_code_review_and_plan_review`

   If the selection omits `test_skills.py`, that is a bug in `select-tests.py`'s rule table, per CLAUDE.md. It is not a reason to widen the run by hand.
3. **Lint:** `../../../.venv/bin/ruff check claude-skills/skills/tests/test_skills.py` (Phase 1a).
4. **Text checks.** Run each as a separate single-statement call. For a presence check, a printed count at or above the stated minimum with exit 0 is the pass. For an absence check, `grep -c` prints `0` and exits 1 on a pass and exits 0 on a regression, so read the printed number, not the exit code.
   - Presence, in `code-review/SKILL.md`:
     - `Authoring the commit message`: exactly 1, which keeps the hook pointer's target intact.
     - `command substitution`, `heredoc`, `$HOME`, `UUID` and `hex`: each at least 1 inside the commit-message block.
     - ``Always spawn `ciso-reviewer` ``: exactly 1.
     - `unless the change is declared dev-only`: exactly 1, inside the ciso bullet.
     - `never silently skip`: at least 1.
     - `code-review:deferred:start -->` and `code-review:deferred:end -->`: exactly 2 each, one in the folded `:420` sentence and one in `:426`.
   - Absence:
     - `file-path domain detection` in `code-review/SKILL.md`: 0.
     - `per touched domain` in `plan-review/ROUTING.md`: 0.
5. **Structure and content-preservation** (the pin cannot see these). Read-only checks, run once for Phase 1:
   - Line-anchored items exist, each count exactly 1: `^- \*Keep current text\*`, `^- \*Apply this round`, `^- \*Cannot choose\*`, and `^- Always spawn` bullets for `ciso-reviewer` and `staff-product-engineer` in each of `code-review/SKILL.md` and `plan-review/ROUTING.md`.
   - Each restructured block's lead line is unindented, does not start with `- ` or `* `, and is followed by a blank line before the first bullet.
   - The whitespace-collapsed text of the committed `:264` (from `git show HEAD:`) equals the new lead line plus the two bullets with `- ` stripped, modulo the lead line. Same comparison for the committed `ROUTING.md:28`, and for the committed contradiction-route region against the new one with only the list markers and the one colon normalised.
   - `git diff -U0` on `claude-skills/skills/tests/test_skills.py` shows exactly the four pin-token changes.
   - The committed-versus-new check on `:343` shows the §16 pointer and the "in-diff, tested code" qualifier present.
6. **Reviews:**
   - Run `/skill-review` first. It is hook-enforced for both `SKILL.md` and `ROUTING.md` (`claude/.claude/hooks/tests/test_reconciliation_block_consistency.py:7-8`), and its behavioral-equivalence audit covers the deletions.
   - Then run `/code-review`.
   - In Phase 2, `/code-review` dispatches `ai-instruction-and-memory-files` for the rule file.
   - Run all review gates, `/ready-for-review` included, against the staged diff before the Phase 1a commit, so no fix commit is expected: the cap gate leaves the commit no headroom (Approach, "Line budget").

## Out of scope

- **Room for #1182 and #1187.** The file ends at its committed count (511 at `08fca56e`), still over 500, so each of those changes still needs its own net-zero funding, or option B. Extra trims now would only use up deletions they could spend later (row 21).
- **Item 4** (moving the Item ownership table out): the engineer's call through the open decision, or #1182's Phase 0.
- **`:266`'s three-clause run-on**, "When you spawn: …; …; …": it is the same flattened-list shape but is not named by #1079. Restructuring it adds about 4 lines, which this commit can fund only by spending more deletions.
- **Unused trim candidates.** Both stay available to #1182's Phase 0:
  - `:308`: the docstrings at `claude/.claude/hooks/tests/test_agent_roster.py:3-4` and `:157` cite it as a rule "stated in code-review/SKILL.md".
  - `:141`: it repeats `:78`, `:285` and `:455`. The fixture docstring at `claude/.claude/hooks/tests/test_design_decision_files.py:678-681` describes "the real SKILL.md shape" as having that trailing paragraph.
- **Stale design record.** `docs/design-decisions/ready-for-review-fix-loop-convergence.md:96` says the file "sits at its 500-line cap with zero headroom" (it is 511) and prescribes item 4. Raise this with the reviewer rather than editing it here. Rewriting that record's prescription is the item-4 decision.
- **A test pinning the hook pointer's target.** `enforce-marker-script-shape.sh:720-721` names the "Authoring the commit message" section and no test pins it, unlike the round-cap consult pointer (`test_hook_alignment.py:465-490`). A one-assertion test is a follow-up, ideally before #1182's Phase 0 spends more deletions.
- **Commit gates and their messages.** The gaps in G2 and the deny text in `enforce-marker-script-shape.sh` are unchanged.
- **The cap itself.** Its value, metric and ratchet are unchanged, per the Ask.
