# code-writer test-to-fit rule

## Context

`code-writer.md`'s Charter has no equivalent of the "test-to-fit is
forbidden" rule that governs every other check-fixing path in this repo
(`ready-for-review/SKILL.md`:65: "fix the code, not the test — unless the
product requirement genuinely changed"). This is a live gap: `code-writer.md`'s
Charter already permits it to "run narrowly-scoped, read-only checks on what
you changed" — and on any dispatch where that check comes back red, nothing
stops it from weakening an assertion to turn it green instead of diagnosing
which side (test or code) is actually wrong. The only existing coverage is
`staff-sdet.md`'s review angle, read only during `code-writer`'s own
self-review pass and only when the diff touches test code — reached after
the fact, not at the decision point. Fixing it now closes the gap at the
point the risk exists, not just downstream. Filed as GitHub issue #949.

## Approach

Add the rule in two places that serve two different readers: a Charter
bullet in `code-writer.md` so every dispatch carries it at the decision
point, and a one-clause citation of the existing rule in
`ready-for-review/SKILL.md`'s CI-watch sub-item 4 so the one dispatch whose
whole purpose is "make the red check green" restates the constraint the
dispatch prompt itself pulls against. The canonical statement stays where
it is — `ready-for-review/SKILL.md` § "2. Verification" line 65 — because
step 2's own check-fixing path is the parent session's inline edit (line 52
says so explicitly), so the rule cannot relocate into an agent body without
stranding its primary reader. Pin the agent-side copy with one small test
in `test_skills.py`, alongside the classes already pinning `code-writer`
body content.

**Root problem:** `code-writer` may run a check (Charter, `code-writer.md:23-28`)
and has no instruction covering what to do when it comes back red, so
weakening an assertion is an available way to turn it green; the only
existing coverage is `staff-sdet`'s review angle, reached after the fact
and only when the diff already touches test code.

**Givens**

- Agent bodies are unenforced prose — nothing in this repo can gate whether
  a dispatched agent honors a Charter bullet; the harness owns that. The
  mechanism available is instruction placement plus a presence test, not
  runtime enforcement.
- A dispatched `code-writer` loads no skill file it was not told to read —
  skill loading is harness-owned and lazy. Most `code-writer` dispatch
  sites (`code-review` Fix route, `respond-pr` batch, per-phase plan
  implementation) never touch `ready-for-review/SKILL.md`, so a
  pointer-only fix would reach almost none of them.

**Mechanisms**

1. **New Charter bullet in `code-writer.md`, inserted between the
   read-only-checks bullet (ends line 28) and the self-review-fix bullet
   (starts line 29).** It qualifies the checks bullet directly above it,
   so placement is adjacency, not taste. `anchors: root`.
   `[verified: claude/.claude/agents/code-writer.md:23-30]`

   Exact wording, matching the file's existing imperative-bullet voice and
   ~80-column wrap:

   ```
   - When a check you ran comes back red, fix the code, not the test — unless the
     dispatch prompt states the expected behavior itself changed. Loosening an
     assertion, marking a case skipped or expected-to-fail, or narrowing what a
     test covers in order to turn a check green is test-to-fit, not a fix. A red
     check you can attribute to neither your own diff nor the dispatch's stated
     task belongs in **Still uncertain** — report it, do not resolve it.
   ```

2. **The carve-out names who holds the authority: "unless the dispatch
   prompt states the expected behavior itself changed."** The canonical
   line says "unless the product requirement genuinely changed," which for
   an agent instructed to "implement exactly what the dispatch prompt
   specifies" (`code-writer.md:15`) would be a self-judged exception —
   precisely the loophole. Deliberate refinement, not a transcription slip.
   `anchors: row1`.
   `[verified: claude/.claude/agents/code-writer.md:15-17; claude-skills/skills/ready-for-review/SKILL.md:65]`

3. **Sentence 2 names three concrete shapes rather than restating sentence
   1.** "Fix the code, not the test" reads as barring test *edits*; the
   real failure modes include loosening a tolerance, skipping a case, and
   narrowing coverage. Terms are stack-neutral ("marking a case skipped or
   expected-to-fail", not `xfail`) because this agent installs to every
   stack. `anchors: row1`.
   `[verified: .claude/rules/skill-and-agent-self-review.md, "Global skill bodies stay platform-agnostic"]`

4. **Sentence 3 keys on attribution, and exempts the dispatch's stated
   task.** Without the exemption it would contradict CI-watch sub-item 4,
   where `code-writer` is dispatched precisely to fix a check in files it
   has not touched. It is also not a restatement of the line-29-30 bullet,
   which scopes *self-review findings* within files touched; this one
   disposes of *check results*, including in files untouched.
   `anchors: row1`. `[verified: claude/.claude/agents/code-writer.md:29-30, 132-136]`

5. **`ready-for-review/SKILL.md` CI-watch sub-item 4 cites the rule rather
   than restating it**, textually parallel to sub-item 3's existing relay
   to the `root-cause-analysis` dispatch. Replacement text for line 199:

   ```
   4. **Offer, don't act.** Report the diagnosis and offer a fix. Dispatch `code-writer` (`model: sonnet`) only on explicit user confirmation; without it, stop and do not re-offer — the diagnosis stays available if the user raises it again. That dispatch carries step 2's "Test-to-fit is forbidden" — a make-the-check-green prompt is the shape most likely to produce a weakened assertion.
   ```

   The relay goes in a new trailing sentence rather than inside the
   existing dispatch clause: inserting it mid-clause would put a nearer
   antecedent between "confirmation" and "without it." "step 2" means
   `## 2. Verification`, matching sub-item 3's own usage of that reference.
   `anchors: root`.
   `[verified: claude-skills/skills/ready-for-review/SKILL.md:198-199; heading list at 45, 174]`

6. **Duplication of the one-line rule across two files is accepted under
   CLAUDE.md's stand-alone-instructional-prose exception, not overlooked.**
   A pointer-only Charter bullet ("read `ready-for-review`'s step 2") would
   be the heavier primitive — a file read on every dispatch to deliver one
   sentence — and would fail for the majority of dispatch sites that have
   no reason to load that skill. `anchors: row1`.
   `[verified: CLAUDE.md § Engineering Judgment, "Single source of truth" named exceptions]`

7. **Structural-sibling audit found exactly one other site needing a
   relay, and it is already covered.** A repo-wide grep for `code-writer`
   across `claude-skills/skills/` returns dispatch sites at
   `code-review/SKILL.md:332` (Fix route, ADDRESS rows),
   `ready-for-review/SKILL.md:84` and `:102` (findings), `respond-pr/SKILL.md:53`
   (batch), `subagent-delegation/SKILL.md:158,172` (per-phase and
   per-round defaults), and CI-watch sub-item 4. Only sub-item 4 hands the
   agent a red check as the task itself; the Charter bullet covers the
   rest generically, so no per-site relay goes anywhere else.
   `anchors: row5`. `[verified: grep of code-writer across claude-skills/skills/, this session]`

8. **One new pinning test in `claude-skills/skills/tests/test_skills.py`**,
   as its own class next to `TestCodeWriterSelfReviewScope`, reusing the
   module-level `_agent_body` helper. Two asserts on stable literals —
   `"test-to-fit"` (case-insensitively) and `"fix the code, not the test"` —
   mirroring the two-assert shape of
   `test_code_writer_self_review_avoids_git_diff_head`. The second literal
   is verbatim-shared with `ready-for-review/SKILL.md:65`, which is what
   makes the accepted duplication mechanically detectable rather than a
   silent drift risk. Do **not** pin the full bullet:
   `TestCodeWriterSelfReviewScope`'s own docstring states the repo's
   convention that a copy-edit to surrounding prose must not fail a pin.
   Write the class docstring's cross-file reference in plain prose, not the
   `` `target` § "Heading" `` form — `test_skills.py`'s citation-resolution
   test scans SKILL.md/REFERENCES.md/ROUTING.md corpora, so a citation in a
   Python docstring is unverified by construction and would rot silently.
   `anchors: root`.
   `[verified: claude-skills/skills/tests/test_skills.py:444-467, 580-601; .claude/rules/citation-grammar.md]`

9. **No hook, no `maxTurns` cap, no third canonical home.** The lighter
   primitives were checked first and are sufficient: instruction placement
   reaches the decision point, and a presence test catches deletion. A
   hook that inspects a diff for test-weakening would have to distinguish
   a legitimate requirement change from test-to-fit — undecidable from
   diff text. Promoting the rule to global `CLAUDE.md` or a `.claude/rules/`
   file adds a context-budget line for one sentence that only one agent and
   one skill step need. `anchors: root`.
   `[verified: claude/.claude/hooks/tests/test_agent_roster.py — no test asserts agent-body content, so no roster/effort/model map changes]`

10. **`maxTurns` on `code-writer.md` stays out.**
    `[engineer-verified: the issue's own Scope note keeps this PR small and standalone]`
    `anchors: root`

**Removable seam:** row 8 is the one element beyond the issue's two edits.
Nothing else depends on it — drop it if the PR should match the issue
exactly. Recommendation is to keep it: this change is otherwise pure prose
in a lazily-loaded agent body with zero mechanical footprint, and the
existing `TestCodeWriterSelfReviewScope` class's own docstring states the
repo's reason for pinning exactly this kind of cross-file dependency
("prevent silent regression of the wiring").

## Critical files

All paths relative to the worktree root.

| File | Change |
|---|---|
| `claude/.claude/agents/code-writer.md` | Insert the row-1 Charter bullet between the read-only-checks bullet (ends line 28) and the self-review-fix bullet (starts line 29). No other edit. |
| `claude-skills/skills/ready-for-review/SKILL.md` | Replace line 199 with the row-5 text. In `## CI watch (out-of-band)`, sub-item 4 — not `## 4. Skill-procedural-fidelity review`. No line added or removed. |
| `claude-skills/skills/tests/test_skills.py` | Add `TestCodeWriterTestToFitRule` immediately after `TestCodeWriterSelfReviewScope` (ends line 601). |

**Reuse:** the module-level `_agent_body(name)` helper in `test_skills.py` —
call it directly as `TestCodeWriterSelfReviewScope._body` does at line 589;
do not add a reader or a fixture.

**Do not touch:** `claude/.claude/hooks/tests/test_agent_roster.py` (its
maps are frontmatter-keyed — `model`, `effort`, `tools` — none of which
change), and `ready-for-review/SKILL.md:65` (the canonical statement; a
reword there breaks sub-item 3's existing quoted relay too).

**Dispatch split: one `code-writer` dispatch, not two.** Per `plan-it`'s
"Name the dispatch split," splitting requires steps that partition into
non-overlapping file sets each specifiable without restating the other's
context. These three edits fail that test twice: the agent-file and
SKILL.md edits are two statements of one rule, so a split would restate the
exact wording in both prompts and invite divergence, and the test's
literals are copied from the agent-file edit. Total diff is roughly ten
lines.

## Verification

Run from the worktree root. `.venv` paths are worktree-relative per
README.md's Tests section.

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's
   documented scoped command; let it derive the set rather than naming
   pytest targets by hand. The `AGENTS_DIR` rule row selects
   `claude/.claude/hooks/tests` and `claude-skills/skills/tests`;
   `_is_test_source_change` adds `test_select_tests.py`; the
   `ready-for-review/SKILL.md` and SKILL.md rows widen it further.
   `[verified: claude/.claude/scripts/select-tests.py:466-485]`
2. `.venv/bin/ruff check claude/.claude/ claude-skills/` — the diff adds
   Python.
3. No shell file changes, so skip the ShellCheck command.
4. `/skill-review` on the diff — **hook-enforced**: `require-skill-review.sh`
   blocks `git commit` until the marker is written, because
   `ready-for-review/SKILL.md` is in the diff.
   `[verified: .claude/rules/review-pipeline-dispatch.md]`
5. `/agent-review` on the diff — required practice for
   `claude/.claude/agents/*.md`, invoked by `/code-review`'s dispatcher;
   not hook-enforced. Per `.claude/rules/skill-and-agent-self-review.md`,
   both reviews run against this diff before staging, since an edit can
   violate the rules the file itself enforces.
6. `/code-review` before the commit, then `/ready-for-review` before the
   push. `ready-for-review` step 2 does **run** for this diff — the
   `test_skills.py` change is executable code, so the markdown-only scope
   exception at lines 54-59 does not apply.

Manual check no test covers: re-read the edited `code-writer.md` Charter
top to bottom and confirm the new bullet does not read as a restatement of
the line-29-30 bullet beside it. Rows 4 and 9 argue it does not; the reader
is the check.

## Out of scope

- **A `maxTurns` cap on `code-writer.md`.** Named in the issue as a related
  observation and explicitly deferred there; it is a cost/runaway concern,
  not a correctness one, and belongs in its own change.
- **Guarding the quoted relay strings in CI-watch sub-items 3 and 4 against
  a reword of `## 2. Verification`'s bold lead-in.** Real exposure — both
  sub-items quote `"Test-to-fit is forbidden"` and neither the
  citation-grammar test nor any other test resolves that quotation to its
  target. Sub-item 3 already carries the identical exposure today and this
  PR does not add it, so a shared-literal pin covering both is a separate
  change.
- **Relaying the rule into `code-review`'s Fix route, `respond-pr`'s batch
  dispatch, or `subagent-delegation`'s per-round default.** Row 7's
  sibling audit found none of them hands `code-writer` a red check as the
  task; the Charter bullet covers them generically. Adding per-site relays
  would be the compounding-layers shape.
- **Promoting the rule to global `CLAUDE.md` or a `.claude/rules/` file as
  a third canonical home.** Rejected in row 9 — context budget for one
  sentence with two readers.
- **A hook that detects test-weakening mechanically.** Rejected in row 9 —
  undecidable from diff text.
- **Any change to `staff-sdet.md`'s review angle.** It remains the
  after-the-fact backstop; this change adds the decision-point rule
  without altering what the reviewer looks for.
