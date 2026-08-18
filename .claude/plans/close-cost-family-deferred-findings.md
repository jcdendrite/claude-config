# Close PR #692's deferred cost-family review findings

## Context

PR #692 (the transcript-analysis cost-family move/reconciliation) deferred
four review findings as out of scope — two `comment-discipline-reviewer`
docstring issues in `cost.py`, one `staff-backend-engineer` test-file
duplicate, and one `staff-backend-engineer` missing automated guard — each
tagged with a DEFER criterion ("orthogonal scope" or "gold-plating beyond
declared user surface") because closing them wasn't part of that PR's own
declared move/reconciliation scope. This plan closes all four now that
there's a branch whose whole purpose is to touch exactly this surface: tidy
the `GH-482`-referencing and multi-paragraph docstrings in `cost.py`, dedupe
`test_token_analyzer.py`'s redundant `_write_subagent_jsonl` copy onto the
shared `conftest.py` fixture, and add a lightweight pytest guard so
`cost.py`'s one deliberate reverse-import exception can't silently widen.

## Approach

Fix each finding at its narrowest correct site: rewrite the four flagged
docstrings in place (no behavior change), delete the redundant test helper
in favor of the already-consolidated `conftest.py` version, and add one new
AST-based pytest file that pins the import-direction exception to exactly
the one name it's documented to cover. No production logic changes anywhere
in this plan — every mechanism below is docs/tests-only.

### Assumption ledger

**Root problem:** PR #692 deferred four review findings (two
`comment-discipline-reviewer`, two `staff-backend-engineer`) as
orthogonal-scope/gold-plating; this plan closes all four without changing
`cost.py`'s runtime behavior.

This plan has no genuine givens — every condition it relies on is either a
verified fact cited at the mechanism that uses it, or a reachable-but-declined
scope boundary recorded in **Out of Scope** below, not an externally-fixed
constraint.

**Mechanisms:**

1. Rewrite `_session_branch_index` and `_attributed_branch` docstrings in
   `cost.py`: replace the bare "GH-482[’s …] convention" phrasing with an
   explicit "see `cmd_review_trace`'s docstring" pointer, and
   collapse each from 2–3 blank-line-separated paragraphs into one tight
   paragraph, trimming narrative rationale while keeping every fact
   identified in exploration — the cross-boundary distinction from GH-482's
   own carry-forward, the nearest-preceding/fallback resolution rule with
   its mid-session-switch correctness property, and the `None`/`"?"`
   sentinel's two trigger conditions. `anchors: root`. Closes both
   `comment-discipline-reviewer` findings for these two functions.
   [verified: `cmd_review_trace`'s docstring, `transcript-analysis.py:1710-1716`,
   is the canonical, self-contained statement of GH-482's per-record
   carry-forward convention — `docs/transcript-analysis-architecture.md`
   contains zero mentions of GH-482, carry-forward, or the `"?"` sentinel
   (exploration agent's full-repo grep). Pointing at it rather than
   restating it follows CLAUDE.md's single-source-of-truth rule; restating
   it a third time (a fourth copy, alongside `cmd_review_trace`'s own and
   `cmd_judgment_pair`'s partial one) was considered and rejected in favor
   of the pointer, for the same reason.]
2. Rewrite `_cost_trend_report` and `compute_cost_trend_data`'s docstrings
   the same way — one tight paragraph each, narrative ("since a corpus
   only a few weeks deep would otherwise…"-style) trimmed, every
   independently load-bearing fact kept as a terse sentence: sidechain
   inclusion, partial-week labeling, unpriced-turn corpus-wide counting,
   `_resolve_cost_roots` vs. `_resolve_scan_roots` for `_cost_trend_report`;
   the absence-means-zero return contract and the `context_over` vs.
   `context_class_dollars` distinction for `compute_cost_trend_data`.
   `anchors: root`.
   - [verified: CLAUDE.md's own caveat clause, confirmed at `/plan-review`]
     Reframing "one line, not a paragraph" as "one sentence per
     independently load-bearing fact, no blank-line paragraph breaks" —
     rather than forcing all facts into a single sentence and dropping
     three of them — reads correctly against the rule's own caveat ("a
     compressed comment that drops the actual constraint is worse than a
     verbose one that keeps it"). No repo precedent exists either way for
     a docstring carrying multiple independent facts, so this is a
     judgment call, not a citable pattern — but it's the reading that
     avoids dropping load-bearing facts, which the rule itself prioritizes
     over line-count minimalism.
3. In `test_token_analyzer.py`: delete the local `_write_subagent_jsonl`
   definition (lines 459–463) and its now-unused `SUBAGENT_SUBDIR` import
   (line 10 — its only other use is inside the deleted function), replacing
   the call sites' dependency with `from conftest import
   _write_subagent_jsonl`, matching the import style
   `test_transcript_cost.py` and `test_transcript_analysis.py` already use.
   `anchors: root`. [verified: exploration agent confirmed the two
   `_write_subagent_jsonl` bodies and docstrings are byte-identical
   (whitespace-only difference in the signature line), and that
   `conftest.py`'s copy is already the consolidated target the other two
   files import from]
4. Add a new pytest file asserting `cost.py`'s import-direction exception
   can't silently widen: (a) parse `cost.py`'s AST and collect every
   top-level `FunctionDef`/`AsyncFunctionDef` name that is neither
   `_`-prefixed nor `cmd_`-prefixed, asserting the resulting **set** equals
   `{"compute_cost_trend_data"}`; (b) parse `transcript-analysis.py`'s AST,
   collect every `ast.alias.name` (the name **as exported by `cost.py`** —
   never `ast.alias.asname`, the local binding) imported from
   `transcript_analysis.cost` that is neither `_`-prefixed nor
   `cmd_`-prefixed, and assert that resulting **set** also equals
   `{"compute_cost_trend_data"}`. Using `asname` in (b) would break the
   guard against today's own correct code, since the one sanctioned import
   is already aliased (`compute_cost_trend_data as _compute_cost_trend_data`,
   `transcript-analysis.py:54`) — filtering on the alias would exclude it
   and either fail red immediately or silently compare an empty set,
   depending on whether (b) is written as set-equality (fails loud) or a
   per-element loop (vacuously passes on an empty collection; a for-loop
   implementation is therefore disallowed — use set-equality). `anchors:
   root`. [verified: exploration agent confirmed no import-direction test
   or lint exists today, ruff has no custom-AST-rule plugin API, and
   `test_transcript_analysis_architecture_doc.py` already establishes the
   doc-pinned-to-code-reality pattern this test follows]
   - The new test's module docstring states three things a future reader
     needs and this plan's exploration surfaced: (i) part (a) guards
     `cost.py`'s public *function* surface only, not top-level
     constants/classes — safe, since (b) independently checks the shim's
     actual imported names regardless of what kind of object each one
     denotes, so the reverse-import boundary itself stays covered either
     way; (ii) this guard pins the production import-direction exception
     only, not the separate whole-module `from transcript_analysis import
     ... cost` bind at `transcript-analysis.py:37` that test files already
     use as `_mod.cost.<name>` to reach `cost.py`'s private helpers for
     patching — that channel predates this guard, stays open by design, and
     restricting it would break legitimate existing test patterns; (iii)
     the hardcoded module path/name constants need updating once
     `cost-ledger`'s own migration phase lands (see Out of Scope) — a red
     run at that point means the exception was intentionally closed or
     widened, not that the guard broke.
   - The assertion failure messages themselves name both readings a
     contributor could be hitting: an unintended new name leaking backward
     across the boundary, or `cost-ledger`'s migration intentionally
     closing/widening the exception (in which case
     `docs/transcript-analysis-architecture.md`'s exception language needs
     a matching update).
   - Lighter primitives considered (over-powered-primitive check — the
     naive fix here is "add an import-linter config," a new third-party
     dependency with coarser granularity than the invariant needs):
     - Runtime module-introspection (`dir()`/`inspect.getmembers` on the
       imported `cost` module) — rejected as a full alternative: it can
       check `cost.py`'s own public surface, but can't distinguish, from
       the shim's already-populated namespace, which names were bound via
       the back-import vs. defined locally — it only covers half the
       invariant.
     - `import-linter` (or `grimp`) config contract — rejected: not
       currently declared in `requirements-dev.txt` (a new dependency,
       triggering CLAUDE.md's package-disclosure rule), and its contracts
       work at module-path granularity, not symbol granularity — it could
       allow the shim→`cost.py` edge without being able to restrict it to
       the single name `compute_cost_trend_data`, which is the actual
       invariant this finding asks to guard.
     - Chosen: AST-based pytest test — stdlib-only (`ast`), matches the
       existing architecture-doc test's own pattern, and is the only
       option with symbol-level granularity on both sides of the
       exception.

## Critical files

- `claude/.claude/scripts/transcript_analysis/cost.py` — docstring-only
  edits to `_session_branch_index`, `_attributed_branch`,
  `_cost_trend_report`, `compute_cost_trend_data`. No behavior change.
- `claude/.claude/scripts/tests/test_token_analyzer.py` — remove the
  duplicate `_write_subagent_jsonl` def and its now-unused `SUBAGENT_SUBDIR`
  import; add `from conftest import _write_subagent_jsonl`.
  - **Reuse:** `claude/.claude/scripts/tests/conftest.py`'s existing
    `_write_subagent_jsonl` (lines 34–40) — already the consolidated target
    for `test_transcript_cost.py` and `test_transcript_analysis.py`.
- `claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py`
  (new) — AST-based guard test.
  - **Reuse:** the `helpers.REPO_ROOT` fixture and the file-path constants
    style already established in
    `claude/.claude/scripts/tests/test_transcript_analysis_architecture_doc.py`.

## Verification

- Targeted: `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_token_analyzer.py claude/.claude/scripts/tests/test_transcript_analysis_cost_import_direction.py claude/.claude/scripts/tests/test_transcript_analysis_architecture_doc.py -v`
- Full suite: `../../../.venv/bin/pytest claude/.claude/`
- Lint: `../../../.venv/bin/ruff check claude/.claude/` — catches a forgotten
  unused-import (`SUBAGENT_SUBDIR`) if the dedup in mechanism 3 is
  incomplete.
- Manual red-state check for mechanism 4's guard, two variants (both
  required — production code's own sanctioned import is already aliased,
  so only testing the unaliased case wouldn't catch an
  `asname`-vs-`alias.name` implementation mistake): (1) temporarily add a
  second, unaliased name to the shim's back-import line (or a second
  public function to `cost.py`), confirm the new test fails, then revert;
  (2) temporarily add a second *public* function to `cost.py` and import it
  aliased to an underscore-prefixed local name (e.g. `as _sneaky`), confirm
  the new test still fails — reporting the real export name, not the
  private-looking local alias — then revert. (An already-`_`-prefixed name
  like `_cost_report` doesn't exercise this case: it's filtered out on
  `alias.name` too, so it produces no red state either way — the aliased
  variant needs a genuinely public source name to distinguish the two
  filtering choices.) Passing both proves the guard actually guards before
  it ships silently green.
- Docstring changes (mechanisms 1–2) are behavior-inert; no new test
  coverage is needed for them beyond the existing suite continuing to pass
  unchanged.

## Out of scope

- `test_token_analyzer.py`'s separate, distinct duplicate of `_write_jsonl`
  (not `_write_subagent_jsonl`) — a different helper the deferred finding
  didn't name. Flagged by exploration as a candidate follow-up, left alone
  here per Axis 1 (file/finding scope discipline).
- Closing `cost.py`'s import exception itself (removing the back-import) —
  a reachable change this plan deliberately declines: PR #692's own DEFER
  rationale scoped the ask to "an automated guard," not to finishing
  `cost-ledger`'s migration, and that migration is a separate, unscheduled
  phase (`docs/transcript-analysis-architecture.md:16-20`) with its own
  file surface this plan doesn't touch. This plan only guards against the
  exception silently widening in the meantime.
- Any change to `docs/transcript-analysis-architecture.md` — it already
  correctly documents both the exception's rules and the module list; no
  edit is needed there.
