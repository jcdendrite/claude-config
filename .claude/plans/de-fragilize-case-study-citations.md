# Plan: De-fragilize source citations in the worktree-enforcement case study (GH-346)

## Context

**Goal:** make `docs/case-studies/worktree-enforcement.md` cite the hook scripts it analyzes via stable anchors instead of line numbers, re-sync the verbatim quotes that have already drifted, and add a test that fails CI when those anchors or quotes drift — so citations can no longer go stale silently.

The case study cites specific line ranges into two hook scripts (e.g. "lines 65–69", "lines 146–155", "lines 230 and 235"). Any non-trivial edit to those scripts desyncs the citations silently. This is not hypothetical — an audit found two distinct, already-live failures:

1. **Wrong line numbers.** "Line 235" is cited for a deny message but is actually `exit 0`; "detection logic (lines 57–69)" in the file-writes hook actually points at the `$HOME/.claude` path-exemption region (detection is at ~99–111); "string at line 153" for the gate's deny output is a comment (the `emit_deny` is at line 148).
2. **A drifted "verbatim" quote.** The Live-demo block claims to capture the gate's deny string verbatim, but it predates the machine-level sentinel addition (commit `cd3f44d`, PR #402). The doc says *"This repo has opted into worktree discipline (.claude/worktree-required is committed)"*; the current gate `emit_deny` (`require-worktree-for-git-writes.sh:148`) says *"This is a repo where worktree discipline is active (repo-level .claude/worktree-required committed, or your machine-level ~/.claude/worktree-required). To exempt this repo from machine-level enforcement, add .claude/worktree-optout."* The "verbatim" capture is a misquote today.

The doc is linked from active documentation (`docs/case-studies.md`; `review-vs-babysitting.md` calls it a "companion case study"), so a reader who follows a citation to verify a claim can land on unrelated code, or trust a misquote, without realizing the doc is stale.

## Approach

**Three-part fix: stable anchors (issue Option 1) + re-sync drifted quotes + a drift-enforcement test (issue Option 3).**

1. **Replace every line-number citation with a stable anchor.** Two anchor kinds, both edit-resistant:
   - *Symbol anchors* — function names that exist verbatim in the scripts: `cwd_anchor_note_if_chained`, `command_chains_cd_then_git`, `git_C_note_if_present`, and the `--absolute-git-dir` vs `--git-common-dir` detection comparison.
   - *Verbatim-quote anchors* — the doc quotes exact strings out of the scripts (the cross-session-race header comment, the helper's self-correction text, the live-demo gate deny string). Those quotes are self-locating; a reader greps the text rather than chasing a line number.
   Fix the wrong line-number citations in the same pass (they cease to exist once line numbers are gone).

2. **Re-sync every verbatim quote against the current scripts before anchoring it.** Do not assume the existing quotes are accurate — at least one (the live-demo block) is already a misquote. For each `>`-quoted block in the doc, diff it against the live script string and update the doc to match exactly. This is in-scope, not scope creep: a stale quote is the same silent-drift defect class the ticket targets, just realized in quoted text instead of a line number.

3. **Add a pytest that greps each anchor and quote-slice against the live scripts and the doc.** For each registered anchor, assert it is present in both the referenced script and the doc (whitespace-normalized to absorb the doc's line-wrapping of quoted blocks). A future rename, reword, or doc rewrite breaks one side; CI fails loudly instead of the citation rotting in silence.

**Match-count semantics (resolves the unsound-presence risk).** Two anchor classes need different guards:
   - *Quote-slice anchors* — register a **distinctive** slice unique to one string (e.g. `"cannot determine the effective cwd at the time git runs"` for the gate, `"expected the inline cd to land you in the worktree"` for the helper — never shared boilerplate like `"Anchor cwd by running"`, which recurs 3× in the script and 4× in the doc). Assert **exactly one** match on the script side (mirrors `test_doc_counts.py`'s `_assert_exactly_one_match`): a reword changes the slice → count 0 → fail; accidental duplication → count >1 → fail.
   - *Symbol anchors* — function names legitimately recur (definition + call sites: `command_chains_cd_then_git` appears 3×). Assert **presence ≥ 1** on the script side, not exactly-one. A full rename removes the definition and all call sites together → count 0 → fail, which is the drift we guard. (Documented in the test so the next editor doesn't "tighten" it to exactly-one and break it.)
   On the **doc side**, assert presence ≥ 1 for both classes (the anchor is still cited).

**Rationale.** Anchors are the root-cause fix for line-number drift; re-syncing quotes fixes the drift that has already happened in quoted text; the test is the piece that converts *future* silent drift into a loud CI failure — which is precisely the issue's "goes stale **silently**" complaint. The test also follows the established repo pattern: `test_doc_counts.py` already audits doc claims against on-disk ground truth and `helpers.extract_skill_command` guards documented-snippet drift; this extends that registry pattern to case-study citations, which currently have no guard.

**Honest limit (stated, not hidden):** the test verifies anchor/quote *presence*, not the semantic accuracy of the surrounding prose. It catches a renamed function or a reworded deny string; it does not catch a claim like "fires before the persisted-cwd check" becoming false while the symbol name survives. This is an identifier/quote guard, not a prose-correctness guard — the right scope for the drift class the issue describes.

### Lighter alternatives considered

- **Anchors only, no test (Option 1 alone).** Lighter — a pure doc PR. Rejected as the sole fix: it does not close the "silently" part of the issue (a future rename still desyncs unnoticed), and the already-drifted live-demo quote shows anchors-without-enforcement is exactly how the current rot happened.
- **Move the doc to a dated `docs/case-studies/2026-Q2/` subdir and reframe as frozen history (Option 2).** Rejected: it does not fix the already-wrong citations or the misquote (a frozen-but-wrong quote is still wrong), it breaks the inbound "companion case study" link from `review-vs-babysitting.md`, and the doc is framed as a current-tense analytical reference, not a postmortem — only its 387-denial statistic is snapshot-dated.

## Critical files

**Modify — `docs/case-studies/worktree-enforcement.md`:**
- *Remove every `lines NN–MM` citation* and substitute symbol/quote anchors:
  - Body ¶ at PR #59 / PR #131 narration (¶ "Two related changes refined the response" / "PR #131 then added"): drop `lines 65–69`, `(lines 230 and 235)`, `(lines 146–155)`, `string at line 153`; cite `cwd_anchor_note_if_chained`, the `command_chains_cd_then_git` gate, and the deny paths by description.
  - Live-demo trailer (¶ "This is the deny string emitted by..."): drop `lines 146–155`; cite the gate by name.
  - **Sources** list — the two hook bullets and the PR #131 bullet: replace all parenthetical line ranges with header-comment / `--absolute-git-dir` vs `--git-common-dir` detection / named-helper / named-gate / `emit_deny` anchors.
  - README bullet: cite the two section titles without the soft "around line NN".
- *Re-sync the verbatim quotes to the current scripts:*
  - **Live-demo code block** (the captured deny output): update to match `require-worktree-for-git-writes.sh:148` exactly, including the machine-level-sentinel sentence and the `.claude/worktree-optout` exemption sentence now present in the script.
  - **Header-comment quote** (cross-session race) and **helper-text quote** (chained-`cd` self-correction): diff each against the current script string and correct any drift before anchoring.

**Create — `claude/.claude/hooks/tests/test_case_study_anchors.py`** (mirror `test_doc_counts.py` structure):
- A `NamedTuple` registry of anchors: `(label, doc_rel_path, script_rel_path, anchor_text, kind)` where `kind ∈ {symbol, quote}`. **One registry row per (citation, script)** — because `--absolute-git-dir`/`--git-common-dir` appear in *both* hooks and the doc cites detection in both, each gets its own row scoped to its script (resolves the both-scripts ambiguity).
- Anchors to register:
  - git-writes symbols: `cwd_anchor_note_if_chained`, `command_chains_cd_then_git`, `git_C_note_if_present`; detection `--absolute-git-dir` / `--git-common-dir`.
  - git-writes quote-slices: gate-deny slice (`"cannot determine the effective cwd at the time git runs"`), helper slice (`"expected the inline cd to land you in the worktree"`), header-comment race slice (`"silently wipes another's"`).
  - file-writes: detection `--absolute-git-dir` / `--git-common-dir` (own row); deny-text slice (`"Write the file at its worktree path instead"`, `require-worktree-for-file-writes.sh:115`).
  - (Confirm each slice's exact text and uniqueness at implementation time by grepping the live script.)
- One parametrized test (ids = `label`, so reordering can't silently test the wrong anchor): for `quote` kind assert exactly-one match in the script; for `symbol` kind assert ≥1; for both assert ≥1 in the doc — all after whitespace-normalization.

**Reuse (do not reimplement):**
- `claude/.claude/tests/helpers.py` — `CLAUDE_DIR`; derive `REPO_ROOT = CLAUDE_DIR.parent.parent` exactly as `test_doc_counts.py` does.
- `test_doc_counts.py` — copy its `_assert_exactly_one_match`, registry-as-NamedTuple, and parametrize-by-label shape rather than inventing a new one.

**No CI change needed:** `hooks/tests/` and `docs/` are already inside the `tests.yml` path-filter REGEX (extended for docs in GH-347), so a doc-only or test-only change still triggers the suite.

## Verification

1. `.venv/bin/pytest claude/.claude/hooks/tests/test_case_study_anchors.py -v` — new test passes against the anchored, re-synced doc and the current scripts.
2. **Drift proof — perturb one anchor of each kind independently, confirm the test fails, then revert:**
   - a symbol rename in the script (e.g. `cwd_anchor_note_if_chained`) → symbol/script assertion fails;
   - a reword of a registered quote-slice in the script → quote/script exactly-one assertion fails;
   - deletion of an anchor from the doc → doc-side assertion fails.
   A guard that can't be made to fail on each path isn't a guard for that path.
3. `.venv/bin/pytest claude/.claude/` — full suite green (no regression in `test_doc_counts.py` or other doc audits).
4. `.venv/bin/ruff check claude/.claude/` — lint clean on the new test.
5. Manual: grep the edited doc for `line` to confirm no residual `lines NN–MM` citations remain (prose uses of "line" excepted).

## Out of scope

- **README section-title citations** (the "around line NN" → section-title change is in scope, but *enforcing* README anchors in the test is not). README citations are doc→README, not the doc→script drift class this test guards, and section headings are low-drift. If README-anchor enforcement is wanted, raise it separately.
- Auditing line-number citations in *other* case studies (`check-runner.md`, `effort-estimation-review-surface.md`, `review-vs-babysitting.md`) — if they share the pattern, raise separately rather than expanding this ticket.
- Generalizing the anchor test into a repo-wide "every doc citation must be an anchor" linter — the registry covers this one doc; a broad linter is a larger design with its own tradeoffs.
