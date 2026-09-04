# Wire `findings_path` into `/plan-review` and cite findings by path in consults

## Context

Give `/plan-review`'s reviewer findings an on-disk home, so a downstream
dispatch can cite them by path instead of transcribing them into a prompt. A
friction report from a live session is the trigger. As reported, consulting
`plan-architect` (`MODE=consult`) on a round of pending `/plan-review` fixes — the
report gives the count as 17, a figure from that session's own narration with no
in-repo artifact to check it against — forced the dispatch prompt to restate every
fix's claim text in full, because a fresh non-fork subagent carries no parent
context. Nothing in the design below turns on the exact count; it matters only that
the list was long enough that transcribing it was the costly part. The prompt ballooned, transcription risked dropping or
distorting a claim, and the agent re-read the plan file anyway to verify each one.
The report frames the cause as a limitation of fresh-agent dispatch, but the
evidence points somewhere narrower and fixable: `/plan-review` is the only review
gate wired into neither of this repo's two findings-durability mechanisms, so
there was nothing on disk to point at. Wiring it to `findings_path` was already
captured as a deliberate follow-up in
`.claude/plans/reviewer-file-output-canary.md:98-101`, and that same plan shipped
the enabling hook exemption. The intended outcome is that a `/plan-review`
reviewer writes findings to `agent-reviews/`, a re-review round and a consult both
cite those paths, and no hook, agent, or script changes to make it work.

This reduces the dispatch prompt to paths; it does not reproduce the parent's
synthesized, dispositioned fix list. `/plan-review`'s own Output-format render —
which dedupes across reviewers and produces the "N pending fixes" list a consult is
usually asked about — stays inline and is not wired here. A consult therefore cites
raw, undisposed per-reviewer files and re-derives the curation itself. That is the
engineer's explicit choice (ledger row 5), and re-derivation by an independent agent
is arguably the point of an outside consult, but the curation is relocated rather
than eliminated.

## Approach

Two edits, each at the file that already owns the contract it extends:

- **`plan-review/ROUTING.md`** gets the `findings_path:` wiring. It already owns every
  spawn-payload decision, and `require-routing-read.sh` gates the spawn on reading it.
- **`claude/.claude/CLAUDE.md`**'s Model & Effort Routing Opus bullet gets the
  pass-by-reference consult rule. It already owns the `MODE=consult` dispatch contract.

Both reuse the shipped `findings_path` mechanism unchanged. Nothing else needs to
change: `require-plan-review.sh` already exempts `agent-reviews/*`, and every reviewer
already carries the dormant `### File-based output` block. So this adds no hook, no
agent-body edit, and no script.

The six settled questions, in order:

1. **Wiring lands in `ROUTING.md` only.** `plan-review/SKILL.md:250` already
   delegates all spawn mechanics there ("Read `${CLAUDE_SKILL_DIR}/ROUTING.md` with
   the Read tool before any spawn decision"), and `require-routing-read.sh` blocks
   the spawn until that read happens — so ROUTING.md is the one surface guaranteed
   loaded at the moment the dispatch prompt is composed. On the single-source-of-truth
   question: the *operational recipe* is duplicated from `code-review/SKILL.md:293`
   in compressed form, and the *rationale* is not. That split is sanctioned rather
   than tolerated, and `docs/design-decisions.md` §4 is the dispositive citation —
   "When two skills need the same rule, the text is duplicated — not factored into a
   `_shared/` include or referenced via `@path` import." `ROUTING.md` and
   `code-review/SKILL.md` already duplicate the Invalid-skip-rationales list and the
   Reconciliation block on exactly these grounds. Two further citations agree and are
   secondary, not load-bearing: CLAUDE.md's SSOT exception (2) for instructional prose
   that must stand alone, and `plan-review/SKILL.md:104`'s own carve-out for "a rule
   restated at sites that must each stand alone." What ROUTING.md must **not** repeat
   is §12's measurements and reasoning;
   it cites `docs/design-decisions.md` §12 for those. Because neither the length gate
   nor the review-marker gate compares two files, a new cross-file token test pins
   the two copies together (row 10).

2. **`ROUTING.md:47`'s ≤2K cap is rescoped, not deleted.** Deleting it would be
   wrong: the inline fallback is live and reachable — `deny-reviewer-tree-mutation.sh`
   re-checks `git check-ignore` at write time, so in a consumer repo that does not
   ignore `agent-reviews/` every reviewer falls back to inline output. Leaving it
   unscoped is also wrong: the reviewer's own block says returning findings inline
   while `findings_path` is present "is a defect," so an unscoped cap ships a
   self-contradicting dispatch prompt. `code-review/SKILL.md:295` already solved this
   with "**Other** spawned specialists must return ≤2K tokens"; mirror that scoping
   exactly. The rewritten sentence must also carry forward the checklist-item keying,
   which `plan-review/SKILL.md:259` depends on — otherwise the format change silently
   drops finding-to-item attribution.

3. **`ROUTING.md:32`'s re-review transcription is in scope.** It is the same bug
   shape in the same file, and CLAUDE.md's "Audit structural siblings before scoping
   a fix narrowly" makes it part of this fix rather than adjacent work. It is also
   only fixable *because* round 1's findings now exist on disk, so it belongs to the
   `findings_path` arm the engineer authorized, not to a widening of it. One
   precision: "what's been applied" has no on-disk home — `marker.sh write
   plan-review` stores only a content hash of the plan set — so that half stays
   inline, and the rule must say so rather than implying both halves become paths.

4. **The pass-by-reference rule lives on a dispatcher-read surface, and only
   there.** `claude/.claude/agents/plan-architect.md` is the wrong home despite being
   the tempting one: an agent body is the *child's* system prompt, so a rule addressed
   to the dispatcher never reaches the dispatcher through it. The canonical site is
   `claude/.claude/CLAUDE.md`'s Opus bullet, which already carries "dispatch
   `plan-architect` with `MODE=consult` as the prompt's first line" — the reported
   friction happened in a session with no skill in the loop, so a rule placed only in
   `code-review/SKILL.md` would not have prevented it. `code-review/SKILL.md:340`
   stays untouched: its consult route carries exactly one finding, where transcription
   is cheap, and the file has ~30 lines of headroom worth protecting.

5. **`docs/design-decisions.md` §12 is extended, not supplemented with a new
   entry.** The mechanism is unchanged — same path template, same activation
   condition, same fallback. What changes is the dispatcher count and the addition of
   a second consumer (consult-by-reference), which is a fact about §12's mechanism
   rather than a new decision. A new numbered section would create the second home the
   SSOT rule forbids.

6. **Verification is `select-tests.py`**, detailed in its own section below.

**Rejected: `subagent_type: "fork"`.** Fork would dissolve the transcription problem
by inheriting the whole parent conversation, but that hands over an entire
conversation to solve a problem whose actual requirement is "hand over N file paths" —
the heavier primitive CLAUDE.md's own test warns against. Two grounds reject it, each
sufficient alone.

- **Independence.** Inheriting the parent's reasoning chain means the agent asked to
  check the parent's derivation independently would instead start from it. Fork removes
  the property the consult route exists to provide.
- **Unverifiability.** Fork's inheritance semantics are undocumented in this repo (G2),
  so a rule shipping to every stow consumer would rest on an `[unverified]` premise.

A commonly-cited third ground says fork runs on the parent's model and effort,
defeating `plan-architect`'s `model: opus` / `effort: xhigh` pins. That ground is not
independent — it is a conditional consequence of the same inheritance semantics G2
flags as unverified. State it as conditional ("if fork inherits model and effort, the
pins are defeated"), so an implementer meeting a counter-claim that the pins survive a
fork is not left defending a ground that was never established. The reporter's observation that "the agent re-read
the plan file anyway to verify each claim independently" describes the mechanism
working, not a cost to eliminate.

### Assumption ledger

**Root:** A `/plan-review` reviewer's findings never reach disk, so every downstream
consumer of those findings — a re-review spawn, a `plan-architect` consult — must
transcribe them into a prompt, which inflates the prompt and introduces a
transcription-fidelity risk the receiving agent then spends tokens re-verifying.

**Givens** (conditions this design treats as fixed and beyond its reach):

- **G1.** A fresh non-fork subagent starts with no parent conversation context. The
  harness's dispatch contract imposes this; no repository artifact can change it.
- **G2.** The semantics of the harness's `subagent_type: "fork"` — whether it honors
  an agent's `model`/`effort` pins, and what it inherits — are vendor-owned and
  documented nowhere in this repository. Settling them needs a first-party source this
  session cannot reach (`plan-architect` holds no `Bash` or web access), so the design
  declines to depend on the field.
- **G3.** A dispatching session cannot read its child's context, and a rule addressed
  to a dispatcher cannot be delivered through the child agent's system prompt. This is
  harness architecture and is load-bearing for the row-13 placement decision.

**Rows:**

1. `require-plan-review.sh` already exempts `<repo>/agent-reviews/*` by exact prefix
   match and returns `exit 0` before the hash computation, so no hook change is needed
   and an armed plan-review gate cannot block a reviewer's findings write.
   `[verified: claude/.claude/hooks/require-plan-review.sh:167-175]` — anchors: root
2. Every reviewer `/plan-review` spawns carries `Write` plus a `### File-based output`
   section that activates only when `findings_path` appears in the prompt, so reviewers
   dispatched without it keep returning inline.
   `[verified: claude/.claude/agents/staff-sdet.md:89-114; docs/design-decisions.md:184]` — anchors: row1
3. `ROUTING.md` is the only spawn-time-guaranteed surface: `plan-review/SKILL.md:250`
   delegates spawn mechanics to it and `require-routing-read.sh` gates the spawn on a
   Read of it. `[verified: claude/.claude/skills/plan-review/SKILL.md:250]` — anchors: root
4. Scope is "wire `findings_path` + dispatch rule"; extending `review-ledger.sh` to a
   `plan-review` gate and introducing a parent-written scratch fix-list are both
   declined. `[engineer-verified]` — anchors: root
5. A consult dispatch cites raw `agent-reviews/*.md` plus the plan file and lets
   `plan-architect` form its own view, accepting the loss of the parent's disposition
   synthesis. `[engineer-verified]` — anchors: row13
6. The ≤2K inline cap must be rescoped rather than removed, because the denied-write
   fallback is reachable in any consumer repo that does not ignore `agent-reviews/`.
   `[verified: claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py:136-152 (check-ignore conditionality); claude/.claude/skills/code-review/SKILL.md:295 (the "Other spawned specialists" scoping precedent)]` — anchors: row2
7. `ROUTING.md:32` requires "prior findings + what's been applied" in every re-review
   spawn, which is the reported bug's structural sibling in the same file.
   `[verified: claude/.claude/skills/plan-review/ROUTING.md:32]` — anchors: root
8. "What's been applied" has no on-disk artifact — `marker.sh write plan-review`
   stores only a content hash of the plan set — so it stays inline while prior findings
   become paths.
   `[verified: claude/.claude/skills/plan-review/SKILL.md:286-289 and its record-completion fixture]` — anchors: row7
9. Duplicating the operational recipe across two dispatcher surfaces is sanctioned;
   duplicating §12's rationale or measurements is not.
   `[verified: .claude/rules/skill-and-agent-self-review.md "No shared partials across skills"; claude/.claude/skills/plan-review/SKILL.md:104's own carve-out for "a rule restated at sites that must each stand alone"]` — anchors: row3
10. Drift between the two recipe copies is invisible to both existing gates — a length
    cap and a review marker each inspect one file — so a cross-file test is required.
    `test_reconciliation_block_consistency.py` is the in-repo precedent for exactly this,
    and its docstring states the gap in those terms.
    `[verified: claude/.claude/hooks/tests/test_reconciliation_block_consistency.py:1-13]` — anchors: row9
11. The new sync test belongs in `claude/.claude/skills/tests/test_skills.py`, not
    alongside the precedent in `hooks/tests/`: `select-tests.py` maps both
    `code-review/SKILL.md` and `plan-review/ROUTING.md` to `SKILLS_TESTS_DIR`, but maps
    `ROUTING.md` to nothing in `HOOKS_TESTS_DIR`, so a test placed there would not run on
    a ROUTING.md-only diff.
    `[verified: claude/.claude/scripts/select-tests.py:260-261, :331]` — anchors: row10
12. `claude/.claude/CLAUDE.md` is 178 lines against a 200-line cap, and
    `check-claude-md-length.sh` denies only when the staged file is over its limit *and*
    longer than the committed version — a short clause fits with margin. (The
    addition appends to the Opus bullet's existing single unwrapped physical line
    rather than adding a new one, so it costs the length gate's line count nothing.
    The true margin is therefore larger than a naive per-sentence estimate would
    suggest.)
    `[verified: line count of claude/.claude/CLAUDE.md; claude/.claude/hooks/check-claude-md-length.sh:63-74 and its policy comment]` — anchors: root
13. The pass-by-reference rule must sit on a dispatcher-read surface, and CLAUDE.md's
    Model & Effort Routing Opus bullet already owns the `MODE=consult` dispatch contract,
    making it the single home rather than a second one.
    `[verified: claude/.claude/CLAUDE.md, Model & Effort Routing "Opus:" bullet]` — anchors: G3
14. A project-layer `plan-review-*` skill may extend the reviewer table with an agent
    that lacks `Write` and the file-output block; passing such an agent a `findings_path`
    re-arms the heredoc-abort-on-large-findings failure the canary plan warned about, so
    the wiring paragraph must condition on the agent carrying the contract.
    `[verified: claude/.claude/skills/plan-review/ROUTING.md:45 (project-layer extension); .claude/plans/reviewer-file-output-canary.md:71-75 (the ordering hazard)]` — anchors: row2
15. `plan-review/SKILL.md:256`'s completeness cross-check reads "what each spawned
    reviewer actually returned," which becomes a pointer line once findings go to a file —
    the clause must name the findings file as the source or the check silently passes on an
    empty return. `[verified: claude/.claude/skills/plan-review/SKILL.md:256]` — anchors: row2
16. §12 is extended in place, and the edit must preserve the literal phrase matched by
    `r"All (\w+) reviewer agents write structured"`, which `test_doc_counts.py`
    cross-pins against README's reviewer count.
    `[verified: claude/.claude/hooks/tests/test_doc_counts.py:355-359]` — anchors: root
17. Citations to `docs/design-decisions.md` use the bare `§N` form; the quoted-heading
    form and its resolution test govern skill-to-skill citations, so `§12` needs no
    heading match. `[verified: claude/.claude/skills/tests/test_skills.py:2301,
    :2307 (the citation-with-target and bare-heading-citation regexes, both of which
    require a quoted heading string after §)]` — anchors: row9
18. **Over-powered-primitive check on the root fix.** The heavier candidate is
    `subagent_type: "fork"`; two lighter primitives exist and are why it is rejected.
    (a) **Pass a path** — the mechanism `plan-it/SKILL.md` Step 5 already uses for the
    plan file ("Pass the plan file's path on that re-dispatch rather than re-injecting
    the plan text inline"); it costs one line per file and preserves the model pin.
    (b) **Reuse `findings_path`** — already shipped, already hook-exempted, already
    carried by every reviewer; it makes the paths in (a) exist for plan-review. Fork
    fails on two independent grounds — independence loss and unverifiability under G2.
    Pin loss is a conditional consequence of the same unverified inheritance semantics,
    not a third independent ground; see Approach's rejection paragraph.
    `[verified: claude/.claude/skills/plan-it/SKILL.md:58; docs/design-decisions.md:184]` — anchors: root

## Critical files

Single `code-writer` dispatch. Do not split: the six files carry one interlocking
wording decision, and the sync test in file 5 pins tokens authored in files 1 and 3 —
splitting would force the same shared background into every prompt, which `plan-it`
Step 5 bars.

1. **`claude/.claude/skills/plan-review/ROUTING.md`** — three edits, all above the
   `## Reconciliation` heading at line 53 so the byte-consistency test at
   `claude/.claude/hooks/tests/test_reconciliation_block_consistency.py` is untouched.
   - *Line 32, spawn payload:* change `prior findings` to the prior round's
     findings-file paths, keeping `what's been applied` inline because no file holds it
     (row 8). Add the fallback: when a prior round wrote no findings file — an older
     round, a denied write, or a fresh worktree — pass those findings inline as today.
     Write this as **two sentences**, not one: line 32 is already a long compound
     sentence, and combining a conditional (findings: paths, with a three-trigger
     inline fallback) with an unconditional (what's-been-applied: always inline) in one
     sentence produces a run-on whose two halves are easy to mis-scope. One sentence
     governs prior findings and its fallback; one states that what's been applied stays
     inline, and why.
   - *New `HOOK_TEST_FIXTURE` fenced block* for the two mechanically-testable halves of
     the recipe: the idempotent grep-then-append to `info/exclude`, and the path-template
     derivation. Follow the existing convention in `plan-review/SKILL.md:17,40,279,286` —
     an HTML comment naming the fixture, stating that the test re-reads the recipe from
     this file, and forbidding duplication of the recipe elsewhere. This closes the gap
     `staff-sdet` raised: a non-idempotent append that re-adds `agent-reviews/` on every
     spawn, or an off-by-one in `cut -c1-20`, would otherwise ship silently and surface
     only at manual-smoke time. Note in the comment that this fixture is pytest-executed
     and never typed into an agent's Bash tool, matching the `declare-planmode-path`
     block's own exclusion note, so `test_skills.py`'s Trigger-A regression scan skips it.
   - *New paragraph immediately before line 47:* the `findings_path` wiring. It must
     carry all six of the following, and close by citing `docs/design-decisions.md` §12
     for the mechanism and rationale rather than restating them:
     1. The `agent-reviews/<agent-name>-<epoch>-<slug>.md` template, with the same
        `$(date +%s)` and `$(git rev-parse --abbrev-ref HEAD | tr '/' '-' | cut -c1-20)`
        derivations as `code-review/SKILL.md:293`.
     2. The idempotent grep-then-append of `agent-reviews/` to
        `$(git rev-parse --git-path info/exclude)` before the first spawn.
     3. The synchronous-spawn rule, because the read-back must run in the same turn.
        Use `code-review/SKILL.md:293`'s existing wording verbatim — "Spawn
        synchronously (not `run_in_background`)" — rather than a new phrasing, so the
        two files share one negation for the recipe-sync test to pin.
     4. The read-back itself: `## Recommendations` first, whole file when the count is
        non-zero.
     5. The inline-fallback branch when a reviewer reports a write failure.
     6. The row-14 condition that only reviewers carrying the file-based-output contract
        receive a `findings_path`.
   - *Line 47, the cap:* rescope to specialists spawned without a `findings_path` and to
     reviewers that fell back inline after a write failure, mirroring
     `code-review/SKILL.md:295`'s "Other spawned specialists" construction. Carry the
     checklist-item keying requirement into the file-based branch so item attribution
     survives (row 2 of the settled questions).
   - *Reuse:* copy the recipe wording from `code-review/SKILL.md:293` rather than
     re-deriving the shell expressions — the sync test in file 5 pins them.

2. **`claude/.claude/skills/plan-review/SKILL.md`** — one clause at line 256. The
   completeness cross-check must read against each reviewer's findings file where one was
   written, and against the inline return otherwise. No other edit; the three
   `HOOK_TEST_FIXTURE` blocks are Axis-3 preserved content this ticket does not scope.

3. **`claude/.claude/CLAUDE.md`** — one sentence appended to the Model & Effort Routing
   "Opus:" bullet, after "Relay what it returns rather than replacing its reasoning with
   your own." It directs the dispatcher to name the files a consult should read — e.g.
   the plan file, `agent-reviews/` findings files, the paths a fix would touch — instead of
   transcribing their contents, on the ground that `plan-architect` holds `Read` and forms
   its own view. Keep it to one sentence: this file loads every turn of every session.

4. **`docs/design-decisions.md`** — extend §12 (line 184) with one or two sentences:
   `/plan-review` is a second dispatcher wiring `findings_path`, and the on-disk findings
   are what let a `MODE=consult` dispatch cite paths rather than transcribe. Preserve the
   literal `All eight reviewer agents write structured` opening — `test_doc_counts.py`
   matches it by regex against README's count (row 16).

5. **`claude/.claude/skills/tests/test_skills.py`** — three new tests. All live in the
   skills tree rather than beside the `hooks/tests` precedent, because `select-tests.py`
   maps `ROUTING.md` to `SKILLS_TESTS_DIR` only (row 11).

   - *Recipe sync.* Assert each pinned token appears in both
     `claude/.claude/skills/code-review/SKILL.md` and
     `claude/.claude/skills/plan-review/ROUTING.md`. Pin the **derivation expressions
     verbatim**, not just the placeholder template: `$(date +%s)` and
     `$(git rev-parse --abbrev-ref HEAD | tr '/' '-' | cut -c1-20)`, plus
     `agent-reviews/<agent-name>-<epoch>-<slug>.md` and
     `git rev-parse --git-path info/exclude`. Critical files item 1 requires both files
     to use identical derivations, so a token set omitting them fails to pin the very
     requirement it exists for — a change to `date -u +%s` or `cut -c1-15` in one file
     would otherwise pass. For the background-spawn rule, pin the **negated phrase**
     actually used, not the bare `run_in_background` token: a future edit permitting
     background spawn still contains the bare substring and would pass while
     re-introducing the same-turn read-back race. Pin the literal
     `not \`run_in_background\``: item 1 directs `ROUTING.md` to reuse
     `code-review/SKILL.md:293`'s existing wording verbatim, so both files already carry
     that phrasing and `code-review/SKILL.md` needs no edit. Assert presence per token per file rather than
     block byte-equality — the paragraphs legitimately differ in surrounding context,
     which is why `TestFileBasedOutputBlockConsistency`'s byte-identical template does
     not transfer. Model the docstring on
     `claude/.claude/hooks/tests/test_reconciliation_block_consistency.py:1-41`; follow
     its marker discipline that each anchored string be unique to the rule it guards,
     not shared with adjacent prose.
   - *Reviewer-contract coverage.* Assert every agent named in `plan-review/ROUTING.md`'s
     reviewer table carries `Write` and a `### File-based output` section. Ledger row 14
     conditions the `findings_path` grant on that contract, but as prose it is backstopped
     only by `agent-review` checklist item 15 — a point-in-time authoring check, not a
     dispatch-time one. A reviewer lacking the contract that is nonetheless passed a
     `findings_path` re-arms the heredoc-abort failure, which for a security reviewer
     means silently lost findings. Reuse `test_agent_roster.py`'s frontmatter-parsing
     approach rather than re-deriving it.
   - *Fixture execution.* Execute the new `HOOK_TEST_FIXTURE` block from item 1 in a
     temporary git repo and assert the append is genuinely idempotent (running it twice
     leaves exactly one `agent-reviews/` line in `info/exclude`) and that the path
     template resolves to the documented shape. Seed the temporary repo with one commit
     (`git commit --allow-empty -m init`, mirroring `hooks/tests/conftest.py`'s
     `git_repo` fixture) before invoking the recipe. On an unborn `HEAD`, `git
     rev-parse --abbrev-ref HEAD` fails with exit 128 rather than resolving a branch
     name, so an unseeded repo would exercise the derivation's failure path instead of
     its documented shape. A loosely-written assertion could pass while proving
     nothing about the real slug derivation. `test_skills.py` has no `conftest.py` of
     its own (row 11 keeps this test out of `hooks/tests/`), so the seeding step must
     be written inline rather than inherited from a fixture. This is the mechanical
     half of what the plan otherwise leaves to manual smoke.
   - *Reuse:* the existing module-level path constants and helpers in `test_skills.py`;
     do not add a new `REPO_ROOT` derivation.

6. **`README.md`** — no change expected. Line 221's reviewer-roster sentence already
   describes file-based output generically ("spawned by `/plan-review` and
   `/code-review`"), so it stays accurate. Verify rather than assume: if the edit to §12
   changes any number `test_doc_counts.py` cross-pins, README moves in the same commit.

## Verification

```bash
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude/.claude/
```

The first is this repo's required agent-local command (README.md's Tests section; repo
`CLAUDE.md`'s Commands section bars a hand-widened full-suite run). It selects the union
of `claude/.claude/hooks/tests` and `claude/.claude/skills/tests` for this diff, because
`claude/.claude/CLAUDE.md` and `docs/design-decisions.md` each map to both
(`select-tests.py:338`, `:342`) — so every test that reads the edited skill files is
selected even though `plan-review/ROUTING.md` alone would select only the skills tree.
The second is required because file 5 adds Python. Paths are worktree-relative per
README.md line 533.

Three specific checks inside that run, each of which must be green rather than merely
collected:

- The recipe-sync test fails if either file drops one of the pinned tokens. State the
  guarantee at exactly that strength — it is a wiring-presence tripwire, not a
  behavioral equivalence check, and it does not catch divergence in step order,
  read-back ordering, or the write-failure fallback branch, none of which are tokenized.
- The reviewer-contract test fails when an agent in `ROUTING.md`'s table lacks `Write`
  or the `### File-based output` section.
- The fixture-execution test fails on a non-idempotent `info/exclude` append.
- `test_doc_counts.py` still resolves §12's `All eight reviewer agents write structured`
  phrase against README's reviewer count.
- `test_reconciliation_block_consistency.py` still passes. Its extractor is
  **heading-anchored, not line-anchored** — it locates `## Reconciliation` by exact
  heading text and bounds on the next `^## ` line. The real invariant is therefore "do
  not alter content between that heading and its terminator, and do not insert a new
  `## ` heading above it," which the planned edits satisfy; the "above line 53" framing
  elsewhere in this plan is a convenience, not the constraint.

Hook-enforced gates this diff must clear, in pipeline order: `/plan-review` on this plan;
`/skill-review` on the `plan-review/SKILL.md` and `ROUTING.md` diff
(`require-skill-review.sh`, in `plugins/skill-management/hooks/`, blocks the commit until
its marker is written); `/ai-instruction-and-memory-files` on the
`claude/.claude/CLAUDE.md` diff; `/code-review` over the whole diff before it goes
anywhere.

Manual smoke, since this repo has no eval harness for dispatch behavior: on a branch
carrying a `.claude/plans/` file, run `/plan-review` and confirm each spawned reviewer
writes to `agent-reviews/` and returns a pointer line rather than full inline findings,
that the armed plan-review gate does not block those writes, and that the rendered output
still keys every finding to its checklist item ID.

## Out of scope

- **Extending `review-ledger.sh` to accept a `plan-review` gate.** Explicitly declined by
  the engineer.
- **A parent-written scratch fix-list or disposition-ledger file for consult dispatches.**
  Explicitly declined by the engineer; the consult cites raw `agent-reviews/*.md` plus the
  plan file instead, which costs the parent's disposition synthesis by design.
- **`subagent_type: "fork"`.** Rejected on merits, not deferred — see ledger row 18 and
  given G2.
- **Consolidating the `findings_path` recipe into a shared `~/.claude/scripts/` helper.**
  This is the principled answer to the SSOT question and would also satisfy CLAUDE.md's
  script-first rule for multi-step Bash recipes, replacing both prose copies with one
  `Bash` call. It is out of scope here because it pulls in a new script, a
  `settings.json` allow-rule (the argument-taking form, per the existing
  `Bash(~/.claude/scripts/marker.sh …)` precedent), a `/review-permissions` pass, a
  shape-gate question, and an edit to the already-tight `code-review/SKILL.md` —
  disproportionate against eight duplicated lines guarded by a test. Deferring it has a
  named cost: the recipe embeds a bare `$(git ...)` substitution, which
  `docs/worktree-bash-guard.md:28` classifies as Trigger E ("a bare `$(git ...)`
  substitution, even with no assignment... Refused") — `$(date +%s)` is a separate,
  undocumented shape the doc's sweep never classifies. The repo-wide sweep at that doc's
  lines 66–74 covered only Triggers A and B, so `code-review/SKILL.md:293` was **missed,
  not exempted**, and this change copies the unremediated shape into a second file. It is
  not a live blocker — that doc's "Current status" section records zero refusals on
  re-test across all seven shapes, so the guard is not a stable static check — but the
  new paragraph inherits a shape this repo has already flagged, and `test_skills.py`'s
  Trigger-A fence scan gives it no coverage (it scans only ``` fences, while this recipe
  is inline prose, and its regex requires an unspaced `VAR=$(`). **Filing this follow-up
  is a deliverable of this change, not a suggestion:** open the issue in the same session
  that lands the PR, and reference it from the PR body, so the recommendation cannot
  evaporate on merge and leave a third future `findings_path` dispatcher facing the same
  choice with one more copy to reconcile.
- **Committing `agent-reviews/` findings files so they outlive the worktree.** In this
  repo's reach and deliberately declined: `deny-reviewer-tree-mutation.sh` permits the
  write only while `git check-ignore` confirms the directory is ignored, so un-ignoring it
  would break the write path the whole mechanism depends on.
- **Editing `claude/.claude/agents/plan-architect.md`.** Considered and declined. Its body
  is the child's system prompt, so it cannot carry a dispatcher-facing rule (G3); adding an
  agent-side echo of the CLAUDE.md rule would be the compounding-layer pattern CLAUDE.md's
  Working Style section names as a wrong-foundation tell.
- **Editing `code-review/SKILL.md:340`'s consult route.** Its route carries a single
  finding, where transcription costs little, and the file sits at 470 of a 500-line cap.
- **`select-tests.py` under-selection for `plan-review/ROUTING.md` — raised to the
  reviewer, not fixed here.** `CROSS_DOMAIN_EXCEPTIONS` has an entry routing
  `code-review/SKILL.md` to `HOOKS_TESTS_DIR` (`select-tests.py:331`) because
  `test_reconciliation_block_consistency.py` reads it, but no matching entry for
  `plan-review/ROUTING.md`, which that same test reads at line 51. A ROUTING.md-only diff
  therefore would not run the test guarding ROUTING.md's own Reconciliation block. This is
  pre-existing, does not affect this diff (whose CLAUDE.md and docs entries pull in the
  hooks tree anyway), and per repo `CLAUDE.md` is a bug in the rule table rather than a
  licence to widen a run by hand. **File it separately.**
- **Splitting `claude/.claude/CLAUDE.md`'s "Opus:" bullet.** Observed and deliberately not
  bundled. The bullet is 1,233 characters on one line and already carries roughly seven
  distinct rules, so appending an eighth compounds a real density cost that the line cap
  (178 of 200) does not measure. Splitting the `MODE=consult` content into a sibling
  bullet would improve discoverability at the same line budget. It is a restructuring of
  an always-loaded file that this change does not require, so it goes to the reviewer as
  an observation rather than into this diff, per CLAUDE.md §Working Style Axis 4.
- **A hook backstop for the post-spawn findings-file read-back.** Unlike the pre-spawn
  `ROUTING.md` read, which `require-routing-read.sh` enforces, nothing mechanically
  verifies the orchestrator actually read each findings file — a skipped `Read` yields a
  clean-looking verdict while findings sit unread on disk. This is a property of the
  shipped `findings_path` mechanism that `/code-review` has carried since it adopted the
  mechanism, not one this change introduces; `code-review/SKILL.md:293` states the
  read-back as prose and designates `agent-review` item 15 as "the enforcement point, not
  this paragraph." Closing it well needs a way to assert a rendered verdict traces to
  file content, which is a behavioral property no existing test shape reaches. Left
  as-is; the reviewer-contract test above closes the adjacent, mechanically-checkable half.
- **A combined-hooks test exercising `require-plan-review.sh` and
  `deny-reviewer-tree-mutation.sh` on the same call during an armed gate.** Each hook is
  covered in isolation today. `ciso-reviewer` confirmed no bypass exists — the
  `agent-reviews/*` exemption is a realpath-prefix match evaluated after the traversal
  guard, on a namespace disjoint from `.claude/plans/` — so this is a coverage gap, not a
  live defect. Worth filing alongside the extraction follow-up.
- **The consumer-repo exposure window for `agent-reviews/` files.** `claude-config` itself
  carries a committed `.gitignore:42` entry, but every other stow consumer relies on the
  lazily-provisioned, uncommitted `info/exclude` line, verified at write time by
  `git check-ignore`. That check fails closed, so a missing entry degrades to inline
  output rather than an unignored write. The residual risk — an entry lost later while
  stale findings files remain, followed by a `git add -A` — is pre-existing and already
  accepted for `/code-review`. This change does not alter the mechanism, but wiring a
  default-fire dispatcher raises how often those files exist in a consumer's tree.
  **Name it in the PR description** as a known, bounded tradeoff rather than letting it
  be inherited silently.
