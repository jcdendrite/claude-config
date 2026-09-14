# PR cost block: deterministic render and a prominent caveat

## Context

Cut the recurrence of the `## Cost (list-price estimate)` block's fenced-render
defect, and make its list-price caveat visually prominent in a GitHub PR body.
The fencing half is a probability reduction, not a guarantee: the only consumer
of the rule is an agent reading prose, so the per-PR manual render check in
Verification is a permanent backstop rather than a one-time launch gate.
`claude-config` is a public repo whose `claude/` directory is stowed
into `~/.claude` for every user who runs `./install.sh`, and changes under
`claude/.claude/**` go live on `git pull` with no re-install. Its
`pr-description` skill embeds a machine-generated cost block into PR bodies,
delimited by `<!-- pr-cost:start -->` / `<!-- pr-cost:end -->`. Two defects
in that block need fixing now. First, it renders inconsistently: the same
generator produced a correct block in one PR and a mangled one in another,
because nothing specifies whether the embedded output may be wrapped in a
code fence. Second, the list-price caveat is easy to overlook, and should be
promoted to a prominent callout.

## Approach

Fix the render defect at its source — a positive, testable instruction in
`pr-description`'s Cost section that the script's stdout is embedded as raw
markdown and never inside a code fence, backed by a targeted `## Checks`
bullet that gives the same drafting pass a second look — and promote the
list-price caveat to
a GitHub-Flavored-Markdown `> [!IMPORTANT]` alert on the `--summary` render
path only, leaving the full report's plain caveat sentence untouched. The two
are one change because a GFM alert cannot be nested inside another element, so
the unfenced-embedding rule is a hard precondition for the alert rendering at
all.

### Assumption ledger

**Root problem.** The `## Cost (list-price estimate)` block is embedded into a
GitHub PR body by an LLM following prose that never states whether the stdout
may be code-fenced, so the same generator produces two different renders; and
its always-applicable list-price caveat sits as an ordinary sentence among
conditional banners, where a reader skims past it.

**Givens** (fixed beyond this plan's reach):

- **G1.** The embedding step is executed by a model reading
  `pr-description/SKILL.md`, not by a script. No unit test in this repo can
  reach it — `claude/.claude/skills/tests/test_skills.py` can only assert on
  SKILL.md's source text. `[verified: claude/.claude/skills/pr-description/SKILL.md lines 66-92 carry no code path; TestPrDescriptionCostSectionWiring at test_skills.py:889-920 is substring-only by its own docstring]`
- **G2.** GitHub owns the alert grammar and its restrictions; this repo cannot
  vary them. Quoted verbatim from GitHub's "Basic writing and formatting
  syntax": "Alerts cannot be nested within other elements", "avoid placing
  alerts consecutively", "limit them to one or two per article to prevent
  overloading the reader", "Use alerts only when they are crucial for user
  success." `[verified: fetched from docs.github.com this session]`
- **G3.** The full-report render path has no automated markdown-embed consumer
  — it is run from a shell and its tables are fixed-width plaintext, so any
  publication of them requires a code fence to survive whitespace collapse.
  `[verified: cost.py:318-324, 335-338, 349-352 pad with format specs; docs/transcript-analysis.md:3 and :692 describe shell invocation and issue-pasting]`
- **G4.** `--summary` has exactly one machine consumer,
  `claude/.claude/scripts/pr-cost-section.sh:39`, whose stdout lands in a
  GitHub PR body. `[verified: pr-cost-section.sh:39,45]`

**Assumptions:**

- **row1.** The fence omission is original to the section, not a regression
  from the recent heading rename — so this is a specification gap to close,
  not a revert. `[verified: git show of 98cfd7d (PR #597) and 94a557a (PR #878)]`
- **row2.** A fenced cost block cannot survive a sync: `pr-description` lifts
  the delimited span out and regenerates it fresh every run, never reinserting
  it verbatim. Therefore a `## Checks` audit item for fencing could only ever
  fire against a block the same file's Cost section just authored.
  `[verified: pr-description/SKILL.md:68 and :109-112]`
- **row3.** The `<!-- pr-cost:start -->` / `<!-- pr-cost:end -->` delimiters
  are located by a model, not parsed by any script or hook in this repo.
  `[verified: repo-wide grep for the delimiter literals returns only SKILL.md, test_skills.py, tighten-prose/SKILL.md, and plan files — no parser]`
- **row4.** `tighten-prose` will not rewrite the alert text: the cost block is
  a named carve-out on both sides. `[verified: pr-description/SKILL.md:96; tighten-prose/SKILL.md:50]`
- **row5.** GFM lazy continuation folds a following unprefixed paragraph into a
  blockquote, so the alert must be terminated by a blank line. `--summary`
  already emits one, because the `Scope:` print begins with `\n`.
  `[verified: cost.py:809-813 prints f"\nScope: …"; lazy-continuation behavior is CommonMark blockquote semantics — [unverified] as a quoted GitHub source]`
- **row6.** `[!IMPORTANT]` is the correct type because the caveat is
  information a reader needs in order to interpret every figure in the block
  correctly, not a risk warning about an action the reader is about to take.
  GitHub's own one-line definition for the type: "Key information users need
  to know to achieve their goal." `[verified: docs.github.com "Basic writing and formatting syntax", Alerts section, fetched this session]`
- **row7.** A `docs/**` change is already mapped by the test selector — no
  rule-table gap to report. `[verified: select-tests.py:142 DOCS_DIR = "docs" and :458 mapping it to the hooks and skills test dirs]`
- **row8.** `fake_projects` is a shared fixture in
  `claude/.claude/scripts/tests/conftest.py:496`, so a new full-report test
  needs no new fixture and no git mocks. `[verified: conftest.py:495-502; used this way at test_transcript_cost.py:2496-2503]`
- **row9.** The existing summary pin at `test_transcript_cost.py:2459-2486` is
  fully self-contained (its own `monkeypatch`/`fake_run`, no class-level
  fixture), so relocating it to a new class is a safe cut-paste.
  `[verified: read of those lines]`
- **row10.** The disclosure sentinel stays opt-in and fail-closed; no polarity
  change is in this plan. `[engineer-verified]`
- **row11.** Per-account sentinel provisioning is solved in a separate private
  repo and needs no `claude-config` change, not even a README note.
  `[engineer-verified]`

**Mechanisms:**

- **M1 — the raw-markdown rule stated positively in `pr-description/SKILL.md`'s
  Cost section, plus a targeted `## Checks` "Flag and fix" bullet.**
  `anchors: root, row1`. The two sites are not a single-source-of-truth
  violation: the authoring instruction states the rule, and the Checks bullet
  is a second read with a different question against the draft the same pass
  just produced — the mechanism `## Checks` already relies on throughout. A
  model that fenced the block believed fencing was the correct way to embed
  shell output, so nothing about that choice reads as incoherent to the
  generic reader-coherence pass; only a specifically-named check has a real
  chance of catching it. This is the only enforcement surface for a defect G1
  puts beyond any test, so the second look earns its place. Set aside: adding
  it to the illustrative reader-coherence marker list (SKILL.md:121-129)
  instead — that list is deliberately about spans a cold reader would
  question, and a fenced table is a render defect, not an incoherent body.

- **M2 — a source-text pin in `test_skills.py`'s existing
  `TestPrDescriptionCostSectionWiring`.** `anchors: G1, M1`. This is the only
  enforcement a test can reach, and the convention-plus-test rule makes it
  non-optional. Over-powered-primitive check for the heavier alternative
  considered here: a **PreToolUse hook** on `gh pr create` / `gh pr edit
  --body-file` that reads the body file and denies when `<!-- pr-cost:start
  -->` sits inside a fence. It is genuinely feasible —
  `deny-private-project-refs.sh` already resolves `--body-file` statically on
  this exact boundary — and it is rejected. Two lighter primitives cover the
  need: (a) the SKILL.md rule itself, which the consumer is a cooperative
  agent already reading, and (b) the source-text pin above, which keeps the
  rule from being silently deleted. The hook ships to every stow user, fires
  on every PR create/edit, must fail open on any parse ambiguity or it blocks
  PR creation outright, and it would be defending a cosmetic render outcome
  rather than a disclosure or safety invariant — a privileged, always-on
  execution context bought for a formatting fix.

- **M3 — a derived module constant `_LIST_PRICE_CAVEAT_ALERT = f"> [!IMPORTANT]\n> {_LIST_PRICE_CAVEAT}"` in `cost.py`, printed by the
  `--summary` branch only.** `anchors: root, G2, G4`. The sentence keeps
  exactly one home; the alert wrapper is derived from it, so the two render
  paths cannot drift textually. Over-powered-primitive check for the alert
  itself, which is heavier than plain emphasis and is the repo's first use of
  GFM alert syntax — three lighter primitives, each failing: (a) **bolding the
  sentence** produces no visual container, so it is the same easy-to-skim
  paragraph the defect describes; (b) **an all-caps house banner**
  (`LIST PRICE — …`, matching `STALE PRICING` / `PRICING INTEGRITY` /
  `EXCLUDED SPEND`) makes the always-on caveat visually indistinguishable from
  three conditional error banners, inverting the ranking a reader needs; (c) a
  **bare blockquote** (`> Computed locally…` with no type marker) renders muted
  and indented in GitHub, lowering prominence rather than raising it. Also set
  aside: a `_print_list_price_caveat(*, markdown: bool)` helper mirroring the
  module's four existing `markdown=`-keyword renderers — those exist because
  table *structure* differs between paths; here the difference is a two-line
  wrapper, and the two emit sites are already structurally different (summary
  prints the caveat first; the full report prints a heading, then the caveat),
  so a shared printer would not collapse them.

- **M4 — the full-report path keeps the plain sentence.** `anchors: G3`. Its
  two consumers both defeat alert syntax: a terminal renders `> [!IMPORTANT]`
  as literal characters, and its fixed-width tables must be code-fenced to
  publish, where an alert renders as literal text as well. This divergence is
  the reason M3 is a second constant rather than an edit to the first.

- **M5 — the three conditional banners stay prose.** `anchors: G2`. Definite
  recommendation: do not convert them. All three can fire in one run and print
  directly beneath the caveat, which would produce up to four consecutive
  alerts — the exact case G2's "avoid placing alerts consecutively" and "limit
  them to one or two per article" name. They also already carry a deliberate
  loud shape (all-caps token, em-dash, fact, action) documented at
  `cost.py:356-357`, and converting them would fork three more strings across
  two render paths. **Accepted residual, stated rather than left implicit:**
  this makes the always-on caveat the block's visually loudest element while
  the three conditional data-integrity banners stay plain paragraphs beneath
  it — an inverted urgency ranking in the case where a banner actually fires,
  since a banner bears on whether the figures can be trusted at all and the
  caveat is a standing disclaimer. Accepted because G2's stacking limits are
  the binding constraint and the banners already carry their own loud shape.

- **M6 — the existing positional test is preserved and extended, not deleted,
  plus a new full-report counterpart.** `anchors: row5, row9, G3`. The
  invariant it encodes (caveat leads the block with no leading separator; no
  `## ` H2 collides with the wrapper heading) survives intact; the first
  assertion widens from one line to the alert's two lines plus its terminating
  blank line, which row5 makes load-bearing rather than cosmetic. The new
  counterpart pins the M4 divergence, which is otherwise invisible in the
  source and is exactly the shape a future contributor would "clean up" by
  unifying the two emit sites.

## Critical files

**Dispatch split: one `code-writer` dispatch, not two.** The natural seam
(skill file + its test / renderer + its tests + docs) fails `plan-it`'s own
split test: the coupling fact — the block must not be fenced, which is *why*
the alert renders — would have to be restated in both prompts, and each agent
could resolve the alert's exact byte shape differently with neither one's
self-review seeing the other's. The whole diff is six files and well under a
hundred lines. One dispatch, two commits: the incidental docstring fixes
(Critical files item 2) land first on their own, then the substantive change.
Verification command for that single dispatch:
`.venv/bin/python3 claude/.claude/scripts/select-tests.py` and
`.venv/bin/ruff check claude/.claude/`.

After the dispatch returns, the **parent** runs `/code-review` — it dispatches
`/skill-review`, whose marker is hook-enforced at `git commit` for any staged
`SKILL.md` (`.claude/rules/review-pipeline-dispatch.md`), and `code-writer`
cannot run review skills.

Modify:

1. **`claude/.claude/skills/pr-description/SKILL.md`** — two sites.

   In the Cost section, after "…never recompose, round, or re-narrate the
   figures.", add two sentences:

   > Embed it as raw markdown, never inside a code fence. A fenced block
   > renders its GFM alert and its `### Cost by …` tables as literal text
   > instead of a callout and tables.

   Say "GFM alert", not the literal `[!IMPORTANT]`: the type is defined once
   in `cost.py`, and naming it here would need a lockstep edit to stay true.
   Do not add a sentence to SKILL.md explaining that choice — it is authoring
   rationale for this commit, not an instruction, and fails the behavior test.

   In `## Checks`'s "Flag and fix" list (lines 138-158), add one bullet:

   > - **A code-fenced cost block.** The `<!-- pr-cost:start -->` span holds
   >   raw markdown; a fence around it renders the alert and tables as
   >   literal text.

   The two sites are the file's own established instruct-then-check shape, not
   novel duplication: line 64 instructs the attribution trailer and line 161
   checks it in this same "Flag and fix" list.

   **Length note for the reviewer:** this file is at 199 lines, so the
   addition crosses `skill-review`'s 200-line target by roughly six. Accepted
   — both sites are behavior-changing, and the target's own stated cliff is
   300. Do not pad the diff with unrelated trims to get back under it.

2. **`claude/.claude/skills/tests/test_skills.py`** — in
   `TestPrDescriptionCostSectionWiring` (line 889), add:

   ```python
   def test_declares_raw_markdown_not_code_fence(self):
       body = self._body()
       assert "raw markdown" in body
       assert "code fence" in body
   ```

   Two short, independent noun pins rather than one four-word prose clause.
   The class's three existing siblings all pin identifier-like tokens
   (a delimiter comment, a config filename, an exact heading), which
   `tighten-prose`'s syntactic carve-out protects verbatim; an ordinary
   declarative clause has no such protection, so a benign reword would break a
   single-clause pin with no behavior change. Splitting the pin also keeps a
   partial reword from dropping both halves silently.

   Reuse the class's existing `_body()` helper — no new fixture. Amend the
   class docstring's closing sentence to "…the delimiters, the script-call
   wiring, and the raw-markdown embedding rule are present in the skill body's
   source text."

   **Separate commit, not bundled:** two incidental accuracy fixes in that
   same docstring, both descriptions of current behavior rather than records —
   `` `## Cost` `` → `` `## Cost (list-price estimate)` ``, and rewriting the
   "this skill body used to inline directly now live in that script instead"
   clause to present tense (the repo bars "used to be X" framing in durable
   prose). Land these as their own commit ahead of the substantive change, so
   a later `git revert` of the alert design cannot silently reintroduce a
   docstring inaccuracy and a CLAUDE.md-barred framing along with it — a PR
   body's "Incidental edits" note does not travel with a revert commit. Note
   them under an "Incidental edits" heading in the PR body regardless.

3. **`claude/.claude/scripts/transcript_analysis/cost.py`** —
   - Amend the comment at lines 25-27 so it names both paths accurately: the
     full report prints the sentence directly, `--summary` prints it inside
     the alert constant. Keep the existing two-part-audience sentence
     unchanged.
   - Add immediately below `_LIST_PRICE_CAVEAT`:

     ```python
     # Must stay unfenced: --summary embeds this in a GitHub PR body where GFM
     # alerts render, and a GFM alert cannot nest inside another element.
     _LIST_PRICE_CAVEAT_ALERT = f"> [!IMPORTANT]\n> {_LIST_PRICE_CAVEAT}"
     ```

   - Line 807: `print(_LIST_PRICE_CAVEAT)` → `print(_LIST_PRICE_CAVEAT_ALERT)`.
     Extend the comment at 804-806 with one sentence: the blank line the
     `Scope:` print supplies terminates the alert, and without it GFM lazy
     continuation folds the `Scope:` line into the blockquote.
   - Line 816 (full-report branch): unchanged.

4. **`claude/.claude/scripts/tests/test_transcript_cost.py`** — add a class
   `TestListPriceCaveat` immediately after `TestCostSummary` (which ends at
   line 2487) holding both render-path pins. Move the existing test at
   2459-2486 into it, renamed
   `test_summary_caveat_alert_leads_block_and_no_markdown_heading_anywhere`,
   with its assertions replaced by:

   ```python
   lines = out.splitlines()
   assert lines[0] == "> [!IMPORTANT]"
   assert lines[1] == f"> {_mod.cost._LIST_PRICE_CAVEAT}"
   # The alert's two lines are immediately followed by a blank line in this
   # print sequence. GFM blockquote termination is assumed, not executed
   # against a markdown parser here.
   assert lines[2] == ""
   # "## " (an H2), not "### " (--summary's own sub-tables, which stay H3).
   assert not any(line.startswith("## ") for line in lines)
   ```

   The `lines[0]` literal is deliberate — comparing only against the constant
   would pass on a malformed alert. The `lines[2]` comment deliberately does
   not claim to verify GFM parsing: the blank line is emitted by the
   unmodified `Scope:` print, and what the assertion polices is its position,
   not its source.

   Add two counterparts in the same class, using the shared `fake_projects`
   fixture (row8) and the file's existing `_write_jsonl` / `_priced` /
   `_cost_args` helpers:

   ```python
   def test_full_report_caveat_is_a_plain_sentence_not_an_alert(self, fake_projects, capsys):
       _write_jsonl(fake_projects / "sess.jsonl", [_priced("claude-sonnet-5", input=1_000_000)])
       _mod._cost_report(_cost_args(), date(2026, 8, 2))
       lines = capsys.readouterr().out.splitlines()
       assert _mod.cost._LIST_PRICE_CAVEAT in lines
   ```

   Exact line membership, not `in out` substring containment: a substring
   check stays true when the caveat is wrapped as `> {caveat}`, so it would
   pass on a bare-blockquote regression — precisely the partial-unification
   shape M6 names as the live risk. Its docstring states the durable reason:
   the full report's tables are fixed-width plaintext, so publishing them
   requires a code fence, and a GFM alert inside a fence renders as literal
   text.

   Second counterpart — `--summary` with at least one banner firing, matching
   the corpus shape of the existing `test_summary_stale_pricing_banner_present_before_expiry`
   (~line 2245):

   ```python
   def test_summary_firing_banner_is_not_folded_into_the_alert(self, ...):
       # ... corpus that fires STALE PRICING under summary=True
       lines = capsys.readouterr().out.splitlines()
       banner = next(i for i, line in enumerate(lines) if line.startswith("STALE PRICING"))
       assert lines[banner - 1] == ""
       assert not lines[banner].startswith("> ")
   ```

   No test in the current suite exercises `--summary` plus a firing banner
   together: the two summary-mode banner tests assert substring presence with
   no positional check, and `test_composes_with_stale_pricing_and_excluded_spend`
   (line 2733) defaults `summary=False`, so it never reaches the alert. The
   manual render check has the same hole — neither of its scenarios fires a
   banner. Every banner today carries its own leading `\n`, so none can abut
   the alert; this pins that fact against a later reorder or a dropped
   "redundant-looking" newline, which would fold a banner into the blockquote
   in a real rendered PR body.

5. **`docs/transcript-analysis.md`** —
   - Regenerate the `--summary` sample (fence at 601-632): replace line 602
     with `> [!IMPORTANT]` and `> Computed locally — …bills.`; the blank line
     at 603 already terminates it. Nothing else in that sample changes.
   - The full-report sample (636-689) is unchanged — confirm, don't rewrite.
   - Add two sentences to the `--summary` bullet at line 588, split rather
     than chained: the list-price caveat leads this block as a GFM
     `> [!IMPORTANT]` alert, unlike the full report's plain sentence; and it
     renders correctly only when the consumer embeds the stdout unfenced —
     citing
     `` `claude/.claude/skills/pr-description/SKILL.md` § "Cost section" `` for
     that rule rather than restating it (the repo's citation convention; that
     heading exists at SKILL.md:66). Note that
     `test_skill_citations_resolve_to_real_headings` does **not** guard this
     one: its scanned corpus is every SKILL.md plus REFERENCES.md/ROUTING.md
     siblings inside skill directories, so a citation in `docs/` is unscanned.
     Write it in the conventional form for the reader's benefit, but do not
     record it as test-enforced.
   - Verify, don't edit, line 690: "…only the caveat sentence directly under
     the `## Cost report` heading" describes the full report and stays true
     under M4.

6. **`CHANGELOG.md`** — one entry under `[Unreleased]` → `### Changed` (line
   7). This is user-visible on `git pull` for every stow consumer with the
   sentinel set to `dollars`, so the entry states both facts a consumer needs,
   and carries a **Migration:** line matching the pattern the neighbouring
   "PR cost disclosure re-scoped…" entry already uses for a change with a
   transition window:
   - The next `/pr-description` sync renders the list-price caveat as a
     bordered `[!IMPORTANT]` callout instead of a plain sentence, on the
     `--summary` path only; the full report's terminal output is unchanged.
   - **Migration:** any PR body not yet resynced — open or already merged —
     keeps the plain-sentence rendering until its next sync. Nothing
     back-fills them.

**Not modified:** `claude/.claude/scripts/pr-cost-section.sh` (row3 — nothing
needs to move into the wrapper; leaving it alone also keeps its behavioral
suite and the shell-lint surface out of the diff), `README.md`,
`docs/hooks.md` (prose-only references with no sample block),
`claude/.claude/scripts/select-tests.py` (row7 — `docs/**` is already mapped).

## Verification

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's
   documented scoped command. Given this diff it should select the skills,
   scripts, and hooks test directories plus `test_transcript_analysis*.py`; if
   it selects a narrower set than that, report the discrepancy rather than
   widening the run by hand.
2. `.venv/bin/ruff check claude/.claude/` — `cost.py` and
   `test_transcript_cost.py` are Python.
3. **ShellCheck is not run.** No `.sh` file changes; running it would be scope
   the diff does not carry.
4. **Manual render check — required, since G1 puts the embedding step beyond
   any test.** Two parts:
   - **Local, pre-PR:** `~/.claude/scripts/pr-cost-section.sh | head -4` prints
     `> [!IMPORTANT]`, then `> Computed locally — …`, then an empty line, then
     the `Scope:` line. This requires the active account's
     `<config-dir>/pr-cost-disclosure` to read exactly `dollars`; on exit 1 the
     block is disabled and this half cannot run — say so rather than reporting
     it as passed, and rely on the second half.
   - **On the rendered PR page, after `/pr-description` syncs this branch's own
     body:** this half is a live-fire exercise of the disclosure gate, not a
     dry run — it publishes this branch's real session count, token volumes,
     and per-model dollar figures to a public page. That is the feature
     working as designed for an opted-in maintainer on this repo's own PR, and
     it opens no new path (row10 holds the gate unchanged), but the engineer
     running it should know it is real output. Confirm (a) the line
     immediately before `<!-- pr-cost:start -->`
     is not a ``` fence, (b) the caveat renders as a bordered callout with an
     icon, not as literal `> [!IMPORTANT]` text, (c) the three `### Cost by …`
     blocks render as real tables, not pipe-delimited text, and (d) exactly
     one `## Cost (list-price estimate)` H2 appears, with no second cost
     heading inside the block.

## Out of scope

- **Flipping the `pr-cost-disclosure` sentinel to opt-out / default-on.** The
  gate stays opt-in and fail-closed. The block's fields are not neutral —
  session count, priced-turn count, and per-model-ID dollars signal engagement
  scale, duration, and model mix (`README.md` § "PR cost disclosure"), and
  `claude/.claude/**` goes live on `git pull`, so default-on would publish
  them from every stow consumer's account with no action on their part.
  `[engineer-verified]`
- **Any `claude-config` change for per-account sentinel provisioning,
  including a README note.** Solved in a separate private repo via a
  per-account provisioning column; multi-account provisioning is a
  private-machine detail that does not belong in this public repo.
  `[engineer-verified]`
- **The `[:space:]` / `[:blank:]` grammar divergence between
  `install.sh`'s sentinel reporter and `pr-cost-section.sh`'s gate.** On a
  sentinel whose content is a blank line followed by `dollars`, the reporter
  prints ENABLED while the gate discloses nothing. Real, and fail-safe rather
  than fail-open: `[:blank:]` is a strict
  subset of `[:space:]` and `dollars` contains no whitespace, so gate-accepts
  always implies reporter-says-ENABLED, and the reverse (over-disclosing)
  input class does not exist. A third mechanism unrelated to rendering —
  tracked separately rather than folded into this diff. The separate fix
  carries a test pinning the invariant "no sentinel content exists where the
  gate discloses and the reporter labels it anything other than ENABLED,"
  since nothing enforces that containment today.
- **The full report's caveat on the documented public-cost-audit-issue paste
  path.** Checked, not assumed: `docs/transcript-analysis.md:692` scopes that
  workflow to "the redacted, aggregate-only **tables**," so the caveat
  sentence is not part of what a curator pastes. M4's decision therefore holds
  on this path. It also surfaces a pre-existing gap this plan does not open
  and does not close — curated issue tables carry no caveat at all.
- **Converting `STALE PRICING`, `PRICING INTEGRITY`, or `EXCLUDED SPEND` to
  alerts.** Rejected on GitHub's own consecutive-alert and one-or-two-per-
  article restrictions (M5). If a later change wants the loudest of the three
  promoted, it needs its own evidence about how often two banners co-fire.
- **An alert on the full-report render path.** Both of its consumers defeat
  alert syntax (M4).
- **A PreToolUse hook enforcing the unfenced embedding at `gh pr create` /
  `gh pr edit --body-file`.** Feasible and rejected as over-powered for a
  cosmetic render outcome (M2).
- **Moving the `<!-- pr-cost:start -->` / `<!-- pr-cost:end -->` delimiters or
  the `## Cost (list-price estimate)` heading into `pr-cost-section.sh`.** It
  would not prevent fencing — a model can fence a self-contained span just as
  easily — while expanding the wrapper's contract and churning its behavioral
  suite.
- **Back-filling already-merged PR bodies whose cost blocks are fenced.** Any
  live PR's block regenerates on its next `/pr-description` sync; editing a
  merged PR's body rewrites a historical record for no functional gain.
- **Re-flowing the rest of `TestPrDescriptionCostSectionWiring`'s docstring**
  beyond the two accuracy clauses named in Critical files item 2.
