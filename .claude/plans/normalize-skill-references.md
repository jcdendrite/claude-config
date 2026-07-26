# Normalize SKILL.md vs REFERENCES.md citation placement

## Context

Make every skill in this repo place source material the same way: `SKILL.md`
carries the rule and the reasoning that lets the rule generalize; `REFERENCES.md`
carries the URLs, verbatim source excerpts, and the record of what was checked.

`claude/` is stowed into `$HOME`, so `SKILL.md` bodies load into real sessions on
every fire, while `REFERENCES.md` is never loaded at runtime. A URL and a
blockquote in a body are therefore paid for on every fire by a reader that cannot
click the link. Four skills currently break the split, and two of those duplicate
a URL already present in their sibling `REFERENCES.md` — a single-source-of-truth
defect under the "DRY governs knowledge" rule in `claude/.claude/CLAUDE.md`
§ Engineering Judgment.

Nothing asserts the convention today, so this cleanup would silently regress.
The intended outcome is four normalized skills plus a structural test that keeps
them normalized.

## Approach

**What moves and what stays.** The rule cuts both ways. Source material moves out:
URLs, verbatim blockquotes, and the considered-and-verified record.
Rule-generalizing rationale stays in — `skill-review` §4 lists "rationale that arms
Claude to judge edge cases not enumerated in the rule" as content that *passes* its
behavior test.

**Named authority is not provenance — it stays.** Review surfaced a distinction
this plan originally missed: an attribution like "Per semver.org 2.0.0" does two
separate jobs. One is provenance (which URL grounds this — belongs in
`REFERENCES.md`). The other is *pushback-resistance*: it is what the model has to
stand on when a user disputes a contestable claim mid-session, and
`REFERENCES.md` is not loaded at the moment the dispute happens. Stripping the
authority name leaves a factual claim indistinguishable from in-repo house style.

So the operative rule is narrower than "strip the attribution":

> **Drop the URL and the verbatim blockquote. Keep a bare authority name where the
> claim is contestable.**

A four-word tag carries no URL, costs almost nothing per fire, and passes the
enforcement test below unchanged (the test bans `https?://`, not the word
"semver.org"). This is *less* change than a mechanical strip-all-eight, and it is
the one place this plan knowingly diverges from the originating brief's "named
organization attribution belongs in REFERENCES.md" framing.

Which sites are contestable, and therefore keep a name:

| Site | Contestable? | Treatment |
|---|---|---|
| `ai-instruction` §1 — Claude Code reads CLAUDE.md not AGENTS.md | Yes — a user may assert the opposite from experience | Keep "Per Anthropic's Claude Code memory docs" |
| `npm-semver` — bump magnitude | Yes — "is this really major?" is the skill's core dispute | Keep "Per semver.org 2.0.0" on the table |
| `claude-hook-review` §4 — `.tool_name`/`.tool_input` always present | Yes — backs a fail-closed security judgment | Keep "Per Anthropic's PreToolUse hook contract" |
| `ai-instruction` §2 — 200-line cap, hooks are deterministic | No — the cap is hook-enforced; the hook claim describes mechanics | Bare prose |
| `ai-instruction` §5 — auto-memory role table | No — describes Claude Code's own mechanics | Bare prose |
| `branch-creation` — no type prefix | No — the rule ships with an explicit project-level override, so the model never has to win the argument | Bare prose (as approved) |

**Enforcement: a structural test with no allowlist.** A scan of all eight
`SKILL.md` files containing `http(s)://` shows a clean separator the originating
brief did not anticipate:

| | URL-bearing lines | Form |
|---|---|---|
| Genuine citations (fix these) | 10 lines / 8 edit sites across 4 files | bare markdown link `[text](url)` in body prose |
| Functional or illustrative (leave alone) | 9 lines across 4 files | inside a fenced code block, or inside an inline code span |

`read-docx-comments`, `brief`, and `ready-for-review` hold their URLs inside fenced
blocks; `review-permissions:152` holds its attack payload inside an inline code
span. So the assertion is: **after stripping fenced code blocks and inline code
spans, a `SKILL.md` body contains no `https?://`.** Zero exemptions to maintain —
the brief's proposed four-file allowlist is unnecessary.

*Alternatives set aside.* Relying on the prose rule already in the root `CLAUDE.md`
alone: the four current violations are the evidence that prose without enforcement
regresses. Extending `skill-management`'s commit-time `validate_skill_structure.py`
instead of adding a pytest case: that validator ships to downstream repos as a
plugin hook, so a claude-config-internal placement convention would be imposed on
every consumer that installed the plugin for frontmatter validation. A dedicated
pre-commit hook: heavier than the task needs — `docs/skills.md` reserves hook
gating for always-loaded or dispatcher-routing surfaces, and this convention is
caught fine by the existing suite that already runs in CI.

*Why the stripping helper is hand-rolled.* A real CommonMark parser
(`markdown-it-py`, `mistune`) would handle both documented gaps for free, but
adding one means a new third-party dependency in `requirements-dev.txt`, which
`claude/.claude/CLAUDE.md` § Engineering Judgment gates on vulnerability-history
and maintenance-health research — disproportionate to a two-form strip over a
19-file corpus. Python's stdlib ships no Markdown parser. Record this rationale in
the commit message, per the same section's "justify the absence of a standard
alternative" rule for hand-rolled parsing.

**The gate this change must clear.** The four skills being edited are themselves the
repo's authoring guardrails, and `/skill-review` §4's compression-diff audit demands
a surviving-line citation for every removed line. Under the original strip-all
design, one row scored **N** — `ai-instruction` §1's two "Confirming signals"
bullets, which had no surviving body line. That row is why the treatment above
changed: those bullets are corroborating rebuttal evidence ("zero AGENTS.md entries
in the changelog", "Claude Code absent from the agents.md tools list"), which is
rule-generalizing rationale, not provenance. They compress to one parenthetical
clause rather than being deleted.

A second row scored **N** for the same reason: `npm-semver`'s "worried about the
wrong things" line, whose edge-case heuristic no other line in that file restates.
It is likewise kept as bare prose (ledger row 16). Both N rows resolved the same
way — the audit finds rationale misfiled as provenance, and the fix is to keep the
rationale in the body without its citation, never to defend the N.

### Assumption ledger

```
Root: SKILL.md bodies load on every skill fire, so URLs and verbatim excerpts in a
body are paid for on every fire by a reader that cannot click them — and no
mechanism keeps them out.

Row 1 [mechanism]: at 8 edit sites across 4 SKILL.md bodies, drop the URL and the
verbatim blockquote while keeping a bare authority name where the claim is
contestable — anchors: root — removes the per-fire cost of the unusable part
without removing either the rule's why or its pushback-resistance.
Row 2 [mechanism]: create REFERENCES.md for branch-creation and npm-semver; extend
the two existing ones — anchors: row1 — the relocated URLs and verbatim quotes need
a destination or the relocation is a deletion.
Row 3 [mechanism]: pytest assertion that a SKILL.md body has no http(s):// outside
a fenced block or inline code span — anchors: root — prose alone already regressed
four times; this is the lightest mechanism that re-checks it.
Row 4 [mechanism]: patch-bump claude-hook-review and npm-semver plugin.json —
anchors: row1 — required by require-plugin-version-bump.sh, not a design choice.
Row 5 [assumption]: every leave-alone URL sits inside a fenced block or inline code
span and every genuine citation is a bare markdown link, so the test needs no
allowlist [verified: fence-state scan of all 8 SKILL.md files this session, plus an
independent reviewer's from-scratch prototype of the described algorithm
reproducing the same 10/9 partition] — anchors: row3
Row 6 [assumption]: the in-body URLs in ai-instruction-and-memory-files and
claude-hook-review are already present in their sibling REFERENCES.md, making those
two removals pure deduplication [verified:
claude/.claude/skills/ai-instruction-and-memory-files/REFERENCES.md:10,22,24,29 and
plugins/claude-hook-review/skills/claude-hook-review/REFERENCES.md:10] — anchors: row1
Row 7 [assumption]: branch-creation's type-prefix argument stands without the
Lullabot quote, because the quote ("Instead we rely on the ticket's type") restates
the body's own "the ticket system already carries the work type as a label"
[verified: claude/.claude/skills/branch-creation/SKILL.md:45-51, confirmed by an
independent reviewer quoting both] — anchors: row1
Row 8 [assumption]: npm-semver's semver.org MAJOR/MINOR/PATCH blockquote is fully
restated by the bump table below it, so removing the blockquote loses no
instruction [verified: SKILL.md:28-32 vs 40-44; an independent reviewer mapped all
three cases 1:1] — anchors: row1
Row 9 [assumption]: keeping a bare authority name at the three contestable sites is
required, because REFERENCES.md is not loaded at the moment a user disputes a claim
[verified: two independent reviewers converged on this from different angles —
pushback-resistance and the §4 behavior test] — anchors: row1 — this narrows the
originating brief's "named-organization attribution moves out" framing.
Row 10 [assumption]: require-plugin-version-bump.sh walks up from every changed
file to its plugin root and requires each root bumped versus merge-base, so one
commit touching two plugins needs two bumps [verified:
plugins/plugin-semver/hooks/require-plugin-version-bump.sh:117-163,203-224] —
anchors: row4
Row 11 [assumption]: that hook is live here because plugin-semver is registered in
project-scope enabledPlugins [verified: .claude/settings.json] — anchors: row4
Row 12 [assumption]: require-skill-review.sh hashes only the staged SKILL.md diff,
not the full staged diff, so the plugin.json bumps do not invalidate a
/skill-review marker written earlier in the sequence [verified:
plugins/skill-management/hooks/require-skill-review.sh:27-29,176] — anchors: row4
Row 13 [assumption]: patch is the correct bump magnitude for both plugins, since no
skill rule changes — only where its provenance is stored [unverified] — anchors:
row4 — resolved by invoking plugin-semver:plugin-semver at implementation time.
Row 14 [assumption]: strip both links and the Lullabot sentence in branch-creation
rather than re-deriving the argument; add the test in this PR with no allowlist;
ship as one PR [engineer-verified] — anchors: root
Row 15 [assumption]: no downstream consumer of the claude-hook-review or npm-semver
plugins depends on the removed body prose [unverified] — anchors: row1 — both are
project-scoped plugins in this repo's own marketplace with no known external
installs; the removed text is a URL and a redundant blockquote, not interface. This
is absence of evidence, not verified absence: the marketplace lets any repo install
at project scope, so the claim rests on the removed text being prose, not interface.
Row 16 [assumption]: npm-semver's "worried about the wrong things" quote carries an
edge-case heuristic that NO other line in that file restates — unlike the
MAJOR/MINOR/PATCH blockquote of row 8, whose content the bump table does restate
[verified: two independent reviewers converged on this site from different angles —
a product read on pushback-resistance, and a from-scratch skill-review §4
compression-diff audit that scored the row N] — anchors: row1 — so the heuristic is
kept as bare prose while the quote and URL leave. Row 8 verified only the
blockquote; it never covered this second removal.
Row 17 [assumption]: claude/.claude/skills/tests/test_skills.py already exists,
tracked, at 902 lines, and already defines the SKILLS_DIR and
test_trigger_cases_files_well_formed symbols this plan reuses [verified: git
ls-files --error-unmatch plus wc -l, this session] — anchors: row3 — so the
enforcement work is an append via Edit, never a Write.
Row 18 [assumption]: this repo's CHANGELOG records plugin version bumps for
consumers who pin [verified: CHANGELOG.md:13 skill-management 2.1.0 and :30 the
2.0.0 rename entry, both naming the version and the consumer impact] — anchors:
row4 — so the two bumps in row 4 need a changelog line.
```

## Critical files

### Preserved content — do not touch

`claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` lines 14 and 190
are `HOOK_TEST_FIXTURE` HTML comments that the hook-alignment suite re-reads in
place. No edit site below is near them; per `CLAUDE.md` § Working Style Axis 3
item 4 they stay byte-identical.

### Modify — SKILL.md bodies (8 edit sites, 10 URL-bearing lines)

- **`claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md`** — five sites.
  - L39-41 — drop the URL and the blockquote wrapper; state the claim as prose
    retaining the authority name: *Per Anthropic's Claude Code memory docs, Claude
    Code reads CLAUDE.md, not AGENTS.md. When a repo already uses AGENTS.md for
    other coding agents, create a CLAUDE.md that imports it so both tools read the
    same instructions without duplicating them.*
  - L43-45 — compress the `Confirming signals:` header and its two bullets into a
    single parenthetical clause appended to the above, keeping both corroborating
    facts and dropping both URLs: *(independently corroborated: zero AGENTS.md
    entries in the Claude Code changelog, and Claude Code is absent from agents.md's
    supported-tools list).* This converts the audit's one N row to a Y.
  - L79-80 — `([Claude Code — memory](url): "Longer files consume more context and
    reduce adherence")` → `— longer files consume more context and reduce adherence`.
  - L86 — `([Claude Code Best Practices](url): hooks "guarantee the action
    happens"))` → `; they guarantee the action happens`. Use a semicolon, not a
    second em-dash: the sentence already carries one before "prefer a hook."
  - L142-143 — drop the `From [Claude Code — memory](url):` attribution line; keep
    the CLAUDE.md-vs-auto-memory table verbatim.
  - L33-35's existing pointer ("URLs in co-located REFERENCES.md") stays as-is.
- **`claude/.claude/skills/branch-creation/SKILL.md`** L37-51 — apply the approved
  form: drop the Conventional Branch link markup, the Lullabot link, and the
  "documents the same tradeoff and the same conclusion" sentence with its quote. The
  two-condition argument and the project-level-override paragraph stay verbatim.
- **`plugins/claude-hook-review/skills/claude-hook-review/SKILL.md`** L74 — drop only
  the URL, keeping the authority name: *Per Anthropic's PreToolUse hook contract,
  `.tool_name` and `.tool_input` are always present on a real hook event; jq failure
  indicates malformed or spoofed input.*
- **`plugins/npm-semver/skills/npm-semver/SKILL.md`** L27-38 — drop the
  `From [semver.org 2.0.0](url):` line and its MAJOR/MINOR/PATCH blockquote (the
  table at L40-44 restates all three cases 1:1). Move the spec name to the head of
  the section, where one bare authority tag governs both the table and the
  public-API paragraph: *Per semver.org 2.0.0, determine the bump by backward
  compatibility against the package's declared public API, not by diff size.* At
  L34-38, drop the verbatim "worried about the wrong things" quote but keep its
  edge-case heuristic as bare prose: *Being unsure whether something belongs to the
  public API is itself the signal — the surface hasn't been declared yet, and no
  additional bump rule will resolve it.* That heuristic is the only line in the file
  arming a session to judge an ambiguous surface, and "is this really part of the
  public API?" is a more common dispute than the MAJOR/MINOR/PATCH split the table
  settles. One authority tag at the section head rather than two, so the spec name
  is not restated within ten lines of itself.

### Create — new REFERENCES.md files

Follow the shape of `claude/.claude/skills/plan-it/REFERENCES.md`: a `# References
— <skill>` heading, a one-line statement that the file is not loaded at runtime,
then per-source URL + what it grounds + verbatim quote.

- **`claude/.claude/skills/branch-creation/REFERENCES.md`** — Conventional Branch and
  the Lullabot ADR, with the Lullabot quote and a line recording that the skill
  reaches the same conclusion independently.
- **`plugins/npm-semver/skills/npm-semver/REFERENCES.md`** — semver.org 2.0.0 with
  both verbatim quotes (the MAJOR/MINOR/PATCH definition and the
  declared-public-API line).

### Modify — existing REFERENCES.md (relocation destinations)

A quote may only leave a body once `REFERENCES.md` carries it.

- **`claude/.claude/skills/ai-instruction-and-memory-files/REFERENCES.md`** — add the
  three verbatim quotes currently living only in the body: the
  CLAUDE.md-not-AGENTS.md paragraph, "Longer files consume more context and reduce
  adherence" (under the memory entry), and hooks "guarantee the action happens"
  (under the best-practices entry). All four URLs are already present.
- **`plugins/claude-hook-review/skills/claude-hook-review/REFERENCES.md`** — record,
  under the existing hooks-reference URL, that the PreToolUse contract specifies
  `.tool_name` and `.tool_input` as always present on a real hook event.

### Modify — enforcement (extend an existing file)

**`claude/.claude/skills/tests/test_skills.py`** already exists and is tracked at
902 lines, and already contains both `SKILLS_DIR` and
`test_trigger_cases_files_well_formed` that the *Reuse* bullet below cites. Read it
and append with `Edit`; a `Write` would silently destroy 902 lines of unrelated
skill-corpus coverage, and CI would surface that as a missing test rather than as a
loud failure at write time. Add a scan test plus parametrized unit tests for the
stripping helper.

- *Helper.* Replace fenced-code regions (` ``` ` / `~~~`, tracking the opening
  delimiter's char and length) with same-length blank runs so line numbers survive,
  then do the same for inline code spans (backtick-run regex with matched delimiter
  length), then scan for `https?://`.
- *Scan test.* Collect every violation across all skill roots and report `file:line`
  together rather than failing on the first. Failure message names `REFERENCES.md`
  as the destination.
- *Unit tests.* Parametrize the helper over both strip paths — a URL inside a fenced
  block, inside a `~~~` fence, inside an info-string fence (` ```python `), inside a
  single-backtick span, and inside a multi-backtick span all pass; a bare URL in
  prose and a URL in a markdown link both fail. This is what proves the algorithm
  bites; a corpus that happens to pass proves nothing, and the inline-span path
  guards `review-permissions` specifically. Two cases earn their place beyond the
  one-form-per-case set: the info-string fence, because ` ```python ` is the only
  fence shape actually present in the corpus and a helper that matches on
  "line is exactly the delimiter" would pass every other case while failing this
  one; and a **composed** case putting a fence, an inline span, and a genuine leaked
  markdown link in one string, because that is `ai-instruction-and-memory-files`'s
  real shape and a two-pass bug where fence-stripping runs past its own closing
  delimiter would otherwise surface only as an unattributed `file:line` from the
  corpus scan.
- *Documented gaps.* State in the test docstring that two CommonMark forms are not
  handled — 4-space indented code blocks, and inline spans crossing a newline. No
  file uses either with a URL, before or after this change's edits: the four edited
  bodies drop their URLs entirely, so the target state is checked, not just today's
  tree. Naming the gaps keeps a future contributor from debugging blind.
- *Frontmatter.* Strip the YAML frontmatter block before scanning, and say so — no
  current `description` holds a URL, but the invariant should be unambiguous.
- *Reuse.* `SKILLS_DIR` (L59) and the `repo_root = Path(__file__).resolve().parents[4]`
  + dual-glob idiom from `test_trigger_cases_files_well_formed` (L750-758), extended
  with the repo-root `.claude/skills/*/SKILL.md` root. Use direct single-level globs,
  never `rglob`/`**` — `.claude/worktrees/**` holds full repo copies with live URLs.

### Modify — docs and manifests

- **`docs/skills.md`** L118 — the "Co-located files come in two roles" bullet already
  defines `REFERENCES.md` as holding canonical URLs and key quotes. Add the two things
  it does not state: URLs inside fenced code blocks or inline code spans are
  functional or illustrative, not citations, and stay in the body; and a bare
  authority name may stay in the body where the claim is contestable. This is the
  only home for that nuance; the root `CLAUDE.md` sentence stays untouched.
- **`plugins/claude-hook-review/.claude-plugin/plugin.json`** — `2.0.0` → per
  `plugin-semver` (expected `2.0.1`).
- **`plugins/npm-semver/.claude-plugin/plugin.json`** — `1.0.1` → per `plugin-semver`
  (expected `1.0.2`).
- **`CHANGELOG.md`** — one `### Changed` bullet under `## [Unreleased]`, naming both
  new plugin versions. The repo's existing entries record plugin version bumps for
  downstream consumers who pin (`skill-management` 2.1.0 and the 2.0.0 rename entry
  both do), so a bump landing with no changelog line leaves a pinning consumer with
  no way to see what changed. State that the bumps are citation-placement only, with
  no rule change, so a consumer can decide not to re-pin.

## Verification

Run from the worktree; the contributor `.venv` lives at the main worktree root only,
exactly three levels up.

1. **Full suite** — `../../../.venv/bin/pytest claude/.claude/`. The new
   parametrized unit tests are what demonstrate the stripping helper bites on both
   paths; the corpus scan alone would not.
2. **Lint** — `../../../.venv/bin/ruff check claude/.claude/`. No shell files change,
   so ShellCheck is not needed.
3. **`/skill-review` on each of the four changed `SKILL.md` files.** Hook-enforced via
   `require-skill-review.sh`. Produce the compression-diff audit table (format in
   `ai-instruction-and-memory-files/SKILL.md` §2), citing a surviving line for every
   removed line. Every row should now score Y — the two rows that previously scored
   N are `ai-instruction` §1's `Confirming signals` bullets and `npm-semver`'s
   public-API heuristic, both now retained as bare prose. If any row still scores N,
   the compression was too aggressive — restore more of it rather than defending
   the N.
4. **`plugin-semver:plugin-semver`** for both version bumps — resolves ledger row 13.
5. **Frontmatter untouched.** No `name` or `description` changes in this PR, so no
   skill-trigger surface moves. Confirm via `git diff` before commit.
6. **`/code-review`, then `/ready-for-review`**, then open the PR ready (not draft).

Ordering note: `/skill-review` (step 3) may precede the version bumps (step 4)
because its marker hashes only the staged `SKILL.md` diff. `/code-review` hashes the
full staged diff and must therefore come after every other edit.

## Out of scope

- **The four leave-alone files.** The XML-namespace URIs in `read-docx-comments` are
  functional string literals; editing them breaks the parser. Likewise the
  attribution trailer in `ready-for-review`, the attack payload in
  `review-permissions` (inline code span opening at line 152), and the placeholder
  URLs in `brief`.
- **The eleven already-conforming skills.** No prose tidying while passing through.
- **Adding `REFERENCES.md` to skills with no source material** — including
  `plugin-semver`, whose body has no URLs.
- **Extending enforcement to agent files or runtime auxiliaries** (`ROUTING.md` and
  similar). They can accumulate the same drift, but this PR's stated scope is
  `SKILL.md`; widening it would expand the review surface without evidence of a live
  problem.
- **Trimming any skill body for length.** Bundling length work makes the
  behavioral-equivalence audit unreviewable.
- **The stale name-only count at `docs/skills.md:36`** ("Twelve" vs fourteen entries)
  — real, unrelated, covered by the existing `detect-stale-doc-counts` plan.
- **Merging the PR.** Requires explicit engineer authorization per this repo's
  `CLAUDE.md`.
