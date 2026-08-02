# Generalize test-conventions §8 and close the borrowed-shape review gap

## Context

**Goal: remove one private project's data-access mock shape from the global `test-conventions` skill body, and add the missing review class so the next instance of that defect is caught rather than shipped.**

`claude/.claude/skills/test-conventions/SKILL.md` is stowed to every consumer of this repo, and repo-root `CLAUDE.md` requires its body to read cleanly regardless of stack. Two subsections of §8 violate that: they state general-sounding principles in nouns that presuppose a table-oriented fluent query-builder database client. A project testing an HTTP client, a queue consumer, or a document store has no "table" and no "filter chain," so those lines do not parse for them.

The contamination is not recent. `git log -S` puts both subsections in `4ffce11` — the skill's **first** commit — so the private project's layer was extracted *later*, and the global body has carried the shape from day one.

Why now: this class of defect is structurally invisible to the current pipeline. `skill-review` item 12 (shipped in #418 to close exactly the platform-genericness gap) keys on **token identity** — a tool verb, a vendor/product name, a named team's practice — and is scoped to **diff hits**. "Table," "filter," and "chain" are common nouns, so there is no token to extract; and because the text has never appeared in a reviewed diff, item 12 has never looked at it and never will.

## Approach

Measure, fix the body, add the class that would have caught it. Three files, matching the approved scope.

### Step 0 — Read-only survey (do this first, no edits)

Read every `claude/.claude/skills/*/SKILL.md`, every `plugins/*/skills/**/SKILL.md`, and every `claude/.claude/agents/*.md` body, and record a **per-file verdict list** — file, verdict, and the triggering noun where the verdict is positive. Not a bare count: class (d) is semantic judgment (see the no-test section), so a scalar cannot be re-derived by the follow-up session that must size remediation from it.

Both extra corpora are in the population deliberately: the plugin-skill pathspec is what `marker.sh` already treats as in-corpus for `skill-review` itself, so a guard that covers it must be sized against it; the agent corpus funds step 2's deferral. **Fixes stay deferred**; this step only replaces ledger row 8's `[unverified]` with evidence. Record the list **in the tracked issue** filed per Out of scope, and summarize it in the PR body — the issue is the durable home, the PR body evaporates.

Rationale: the Context argument above is that a global skill's oldest content never enters a diff. Section-scoped extraction (step 2) narrows that window but does not close it — a section nobody edits is never audited. Shipping the guard while leaving the defect population unmeasured reproduces the invisibility this change exists to fix.

### Step 1 — Generalize `test-conventions` §8 (`claude/.claude/skills/test-conventions/SKILL.md`)

Keep both principles — they are real and worth stating — and strip the interface shape without flattening them into truisms. A rule so generic it prescribes nothing is a regression, not a fix; the capture categories that make these lines actionable (the addressed target, the selector, the assertion template) all survive in interface-neutral form.

| Line | Current | Replacement (literal target text) |
|---|---|---|
| `:169` | `- Document known limitations (e.g., "only supports single filter per chain")` | `- Document which call forms the double supports and which it does not (e.g., matches by URL prefix only; acknowledges one message at a time)` |
| `:173` | `When mocking a client that performs writes, record the mutations for assertion:` | `When mocking a dependency that performs writes, record the mutations for assertion:` |
| `:174` | `- Capture table name, operation type, payload, and filter values` | `- Capture the target it addresses (endpoint, topic, collection, table), the operation, the payload, and the arguments that select what it acts on` |
| `:175` | `- Let tests assert "this function called update on table X with payload Y"` | ``- Let tests assert "this function issued `<operation>` against `<target>` with payload `<payload>`"`` |

Four lines replaced one-for-one → **line-neutral** (ledger row 3 makes this load-bearing). `:172` is the `### Mutation recording (mocks)` heading and is **not** touched. In `:175` the placeholders are backticked so they do not read as markup.

**`:169` and `:174` are class-(d) hits in this very diff — item 12 requires each be justified as deliberate/illustrative, not silently passed:**
- `:169` — "matches by URL prefix only" presupposes URL-addressed dependencies in isolation. It is licensed because the generic capability is stated in the stem ("which call forms the double supports and which it does not") and it is **paired with a second example from a different interface** ("acknowledges one message at a time"). Two illustrations spanning two interfaces alongside a generic stem is illustrative use, not category anchoring.
- `:174` — the parenthetical `(endpoint, topic, collection, table)` contains `table`, the exact noun this change removes. Licensed on the same test, one step stronger: four illustrations spanning four interfaces (HTTP, pub/sub, document store, relational) alongside the generic stem "the target it addresses" is illustrative enumeration, not anchoring on any one of them.

Record both justifications in the PR body — a reviewer applying the new class-(d) test to this diff (verification step 1) will extract both and needs the justification on hand, not invented on the spot.

Two later §8/§9 items (`:185` framework mock accessors, `:199` import-form deviation) were checked and are **not** in scope: both are already framework-neutral in wording, and both were deliberately genericized from a different private project's lesson at authoring time.

### Step 2 — Add class (d) to `skill-review` item 12 (`plugins/skill-management/skills/skill-review/SKILL.md:180-181`)

Replace item 12 with the following. **Match the existing unwrapped long-line style of `:180-181`** — siblings 11 and 14 wrap at ~72 chars, and wrapping this text instead would push the file past the 200-line gate. Unwrapped it stays **two logical lines, so the file total is unchanged at 193.**

```
12. **Platform-genericness (enumerate-then-justify)** — before declaring the review complete, extract every hit in four classes from the sections the diff touches, not only its changed lines; do not rely on noticing during read-through: (a) a tool-invocation verb prescribed as a mandatory review-instruction step; (b) a vendor/product name anchoring a rule's category rather than illustrating the generic capability alongside it; (c) a source-material bias anchor — a named team's or org's practice cited as the reason a rule holds, not the rule's own rationale; (d) a borrowed interface shape — a rule whose nouns presuppose one system's call chain, record layout, or addressing scheme, with no vendor token present to give it away; read the rule against a codebase with a different interface and ask whether it still parses.
    Justify each hit inline as deliberate/illustrative, or move it to a `<skill>-<project>` layer; extraction is mandatory, the verdict per hit stays judgment. Record a hit on a line the diff did not change as a note for the PR reviewer: it is not an N row and does not hold the marker write. (Repo-specific; see CLAUDE.md "Global skill bodies stay platform-agnostic" and "abstract first.")
```

Two changes beyond adding (d), both load-bearing:

- **Scope widening** ("from the sections the diff touches, not only its changed lines") is what makes the class reach day-one content at all.
- **The unchanged-line disposition sentence is not optional.** Without it the widening converts an advisory into a commit blocker: `require-skill-review.sh` gates the commit on a clean review, item 12's two remedies both require *editing* the text, and the marker-write step at `:185-186` gates on "no N rows, no other blockers." A contributor making a one-line edit inside §7 would be blocked until they generalized day-one prose that `CLAUDE.md` Axis 1 tells them not to touch. The sentence names both escapes explicitly — not an N row, does not hold the marker — because either omission re-creates the block. Routing the hit to the PR reviewer is Axis 1's own bucket 3.

**Note for the implementer, not for the skill body:** class (d) is adjacent to but distinct from `CLAUDE.md`'s "structural fingerprints" redaction rule. That rule is about a shape **identifying** a project; class (d) is about a shape making a rule **inapplicable** elsewhere. §8's text identifies nothing, yet is still a defect. Do not paste this paragraph into either file.

**Scope note — why `agent-review` item 16 is not edited here.** `agent-review` carries item 16, which already mirrors item 12's classes (a) and (c) for agent bodies. A draft of this plan added class (d) there too, on the ground that item 16's vendor carve-out is written for `staff-*` personas and so does not cover `code-writer.md` or `skill-fidelity-reviewer.md`. That reasoning is sound as far as it goes, but item 16 is **whole-body scoped with an edit-only remedy** — it states agent bodies have no project layer to relocate content to — so class (d) there is a full-body audit that can only be discharged by editing, reaching the contributor through `/code-review`'s dispatch and `require-code-review.sh`. Porting it would need its own disposition clause, its own carve-out wording (`staff-*` **and** `ciso-reviewer`, matching item 15), and drafted text none of which the agent-corpus defect population justifies yet. **Step 0 measures that population instead**; a follow-up sized from real hits can design the item-16 change properly rather than bolting it on. The gap is stated, not silently claimed closed.

### No new test — precedent, not omission

No test is added, on the reasoning #418's plan recorded when it rejected a vendor-name denylist for this same item: **class (d) has no mechanical verdict** — whether a noun presupposes an interface is semantic judgment — and a test asserting item 12 "contains the right words" would be the `code-review` 9g source-scanning anti-pattern. This deviation from the repo's add-a-test convention goes in the PR body.

The scoping matters: the rationale covers class (d) specifically, **not** the whole change. One invariant this plan touches *is* mechanically testable — §N cross-reference integrity — and it is deferred on scope grounds, not on "no mechanical verdict" grounds. See Out of scope.

### Assumption ledger

**Root problem:** the global `test-conventions` body encodes one project's data-access interface as universal, and the pipeline class that should catch it keys on tokens and diffs, so it structurally cannot.

| # | Assumption / mechanism | Tag |
|---|---|---|
| 1 | §8's mock-shape text dates from the skill's first commit, so it has never been diff-reviewed | `[verified: git log -S "Mutation recording" / "single filter per chain" → 4ffce11]` |
| 2 | Item 12 is diff-scoped and token-identity-keyed; it cannot classify common-noun shape borrowing | `[verified: plugins/skill-management/skills/skill-review/SKILL.md:180-181]` |
| 3 | `test-conventions` is **199/200** lines; `check-skill-length.sh` denies only when the new count exceeds the limit **and** exceeds the old — so the §8 edit must stay line-neutral | `[verified: wc -l; check-skill-length.sh:63 (the 200 default inside limit_for), :73 (the two-part condition); settings.json:211]` |
| 4 | `skill-review` is **193/200** lines; the drafted item 12 stays two unwrapped logical lines, so the total is unchanged | `[verified: wc -l; :180-181 are unwrapped long lines]` |
| 5 | §8/§9 numbers are cited from `code-review` and `staff-sdet.md`, and **no test pins them** → do not renumber sections | `[verified: §8 ← code-review/SKILL.md:98,352 only; §9 ← code-review:94,96 and staff-sdet.md:32; §5/§6 ← staff-sdet.md:28,57 (`:30` cites test-evaluation §4, not this skill); test_skills.py:442 is the sole §-assertion and is a negative substring check]` |
| 6 | `REFERENCES.md` (43 lines, two H2 sections) carries **no source backing §8**, so no citation is invalidated by rewording | `[verified: REFERENCES.md — test-first discipline; regex-in-assertions]` |
| 7 | Editing a file under `plugins/` requires a version bump, hook-enforced by `require-plugin-version-bump.sh`; CI's `pytest claude/.claude/` does **not** cover `plugins/` | `[verified: .claude/rules/review-pipeline-dispatch.md; plugin.json 3.0.3; .github/workflows/tests.yml:138]` |
| 8 | The class-(d) defect population across the skill **and agent** corpora — **measured by step 0**, fixes deferred | `[unverified — step 0 resolves this before any edit]` |
| 9 | The interface-specific form removed from §8 survives in a reachable project layer, so no consumer loses guidance | `[verified: read-only check of that layer this session; the layer is not named here per this repo's redaction rules]` |
| 10 | Invoking `skill-management:skill-review` loads the **cached** plugin body (`plugins/cache/claude-config/skill-management/3.0.3/`), not the edited worktree file | `[verified: cache dir holds 2.4.1, 3.0.0, 3.0.2, 3.0.3; a 3.1.0 written this session will not exist there]` |
| 11 | Scope = §8 body fix + `skill-review` item 12, three files; §8 generalized in place rather than deleted; the other repo's layer out of scope but recorded | `[engineer-verified — a review round proposed widening this to `agent-review`; the widening was dropped for lack of a safe remedy, not because the underlying gap closed — see step 2's scope note and the tracked-issue requirement in Out of scope; the row stands as approved]` |

**Over-powered-primitive check (step 2's scope widening).** The heavier option is a full-body genericness audit on every `skill-review` run. Two lighter primitives were considered and rejected as *sole* fixes: (i) a one-time corpus sweep with no standing procedure change — does not prevent recurrence, since the next borrowed shape enters a file nobody re-sweeps; (ii) a token denylist — already rejected for this item in #418 for blocklist drift and absence of a mechanical verdict. **Section-scoped** extraction is the chosen middle: it audits the neighborhood the reviewer is already reading and no longer exempts day-one content. Its cost is bounded by the largest section in the covered corpus, not by file size — step 0 records that bound. *(anchors: root, row 2)*

## Critical files

- `claude/.claude/skills/test-conventions/SKILL.md` — §8 `Stub/mock fidelity` (`:167-170`) and `Mutation recording (mocks)` (`:172-175`). **Do not renumber §1–§9** (ledger row 5).
- `plugins/skill-management/skills/skill-review/SKILL.md` — item 12 (`:180-181`) only.
- `plugins/skill-management/.claude-plugin/plugin.json` — version bump; a new checklist class is a backward-compatible behavior addition, so **minor** (`3.0.3` → `3.1.0`), with `plugin-semver` as the authority. **Rollback is a forward bump** (`3.1.1` carrying the reverted body), not a version restore — a consumer who already ran `claude plugin update` holds 3.1.0 in cache and will never pull a lower published version.

**Model to follow:** item 12's existing enumerate-then-justify wording is itself modeled on `code-review` item 9d (`claude/.claude/skills/code-review/SKILL.md:89`) — the drafted class (d) matches that shape rather than inventing a new one.

**Canonical rules to cite, not restate:** repo-root `CLAUDE.md` "Global skill bodies stay platform-agnostic" and "When a skill is surfaced by real-world work, abstract first."

## Verification

1. **Dogfood — read the worktree text, not the plugin cache.** Apply the new class (d) to the item-12 diff and to the §8 diff by reading the edited worktree files directly. Invoking `skill-management:skill-review` loads the cached 3.0.3 body (ledger row 10), which still carries the old three-class item 12 — relying on it alone would ship class (d) having never been executed once.
2. **Length gate** — `test-conventions` stays at 199, `skill-review` at 193. Commit-blocking if either grows past 200.
3. **Plugin gate** — `plugin-semver` on the `skill-management` bump; `require-plugin-version-bump.sh` blocks the commit otherwise.
4. **Suite** — from the worktree, `../../../.venv/bin/pytest claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/` stay green. This is a **regression tripwire for unrelated structural invariants only**: no test asserts on §8 prose or item-12 prose, and `plugins/` is outside both the local path and CI's `pytest claude/.claude/` (ledger row 7). Steps 1 and 5 are the only real signal on both edits.
5. **Applicability read-through — both directions.** Read the rewritten §8 against two concrete artifacts on this filesystem: a module that wraps an HTTP client, and one that consumes a queue. Two criteria, both required, applied to `:169`, `:174`, and `:175` (`:173` is a noun swap in a lead-in sentence and is exempt — there is no per-line decision to name for it):
   - *Not over-specific:* every line parses for a dependency that is **neither** URL-addressed **nor** message-based — a filesystem double or an in-process library double. Keying this to "avoids tables and filter chains" would test only the shape being removed and pass trivially on the two interface nouns `:169` now introduces.
   - *Not a truism:* name the specific mock-design decision a reader makes differently because of each line. If the answer is the same across both readings and reduces to "write a good mock," the line failed in the other direction and must be re-tightened.

   The second criterion exists because the first cannot fail — a truism parses everywhere. Both must be answered in writing in the PR body, not asserted as done.
6. **`/code-review`** on the staged diff, which routes to `skill-review` and `plugin-semver` per file type.

## Out of scope

- **Class (d) on `agent-review` item 16.** Deliberately deferred with the gap stated — see step 2's scope note. **File it as a tracked issue before merge**, carrying step 0's per-file verdict list as sizing evidence and the carve-out requirement (`staff-*` **and** `ciso-reviewer`, matching item 15) as a design input — same reasoning as the §N deferral below: a plan-file note evaporates on merge, and a PR-body mention is not a durable home either.
- **A different repo's `test-conventions` project layer**, reported by another session as ~90% duplicate of the global skill with one net-new routing pointer, and carrying a proposed imperative that contradicts that repo's own committed editor rules. Real, but the edits land in a repo this branch cannot touch — hand it to a session anchored there. Nothing in this plan depends on it.
- **Fixing whatever step 0's survey finds.** Step 0 measures; remediation is a separate change sized from that evidence.
- **A test pinning `§N` cross-references.** `code-review` 9f/9g/9h and `staff-sdet.md` cite `test-conventions` §5/§6/§8/§9 with nothing guarding against renumbering; `test_doc_counts.py`'s registry is the same pattern one level over, so the machinery already exists. Unlike class (d), this invariant **does** have a mechanical verdict — it is deferred because this plan renumbers nothing, not because it is untestable. **File it as a tracked issue**; a prose note in a plan file evaporates on merge.
