# Plan: detect stale hardcoded counts in README/docs

GH-347. Goal: stop doc figures that count repo content (e.g. "eight reviewer
personas", "Ten bundled skills disabled") from silently going stale.

## Context

Several docs embed numeric claims about repo content that drift when the repo
changes. A README figure off by an order of magnitude reads as careless even
when every other fact is correct. The issue surfaced from a 2026-05-27
internal-consistency audit. The issue proposes an *audit script*; the user
questioned when such a script would ever run.

Two findings reframe the issue's literal proposal:

1. **A standalone script has no trigger and rots.** The repo already runs a
   pytest suite in CI on every PR (`.github/workflows/tests.yml`) and ships a
   direct analog — `test_skill_overrides_documented_in_docs_skills_md`
   (`claude/.claude/skills/tests/test_skills.py:706`) reads `settings.json`,
   reads `docs/skills.md`, and asserts the doc matches reality. A test *is* the
   audit, and it runs automatically. (Decision: pytest test, not script, not a
   commit-gating hook — a hook only fires on an agent's `git commit` inside a
   Claude session, missing human commits and CI.)

2. **Most numeric claims are not mechanically derivable.** Exploration found the
   majority of doc numbers refer to in-prose enumerations ("eight-principle
   error-handling standard"), architectural constants ("three-tier redaction"),
   or point-in-time corpus snapshots ("946 hook denials"). The issue's blanket
   `\b\d+ (hooks|skills|...)\b` grep would flood with false positives and can't
   distinguish a disk-count claim from a prose constant. So the test locks only
   counts with a clean 1:1 disk source.

Intended outcome: a precise pytest test that fails when a *derivable* count in
the docs no longer matches disk, plus a one-time fix of the genuinely-stale
claims the audit found.

## Approach

A small, curated **registry** of derivable count facts. Each entry pairs a
ground-truth function (derives the count from disk) with one or more doc
*occurrences* (a file + a regex that captures the claimed number in its
sentence). The test parses each captured token (digit or English word) to an
int and asserts it equals the ground truth.

This is the `test_skill_overrides_documented_in_docs_skills_md` shape
generalized: derive truth from disk, pin to a specific doc sentence, assert
equality — no blanket surfacing.

**Coverage (unambiguously derivable only):**

- **Reviewer personas = 8.** Ground truth: `len(REVIEWER_AGENTS)` — the
  authoritative dispatched-reviewer set already maintained in
  `claude/.claude/hooks/tests/test_agent_roster.py` (guarded by its
  `test_no_uncategorized_agents`). **Reuse that constant; do not re-derive via a
  `staff-*/ciso-*` filename glob** — a glob is a second ground-truth that drifts
  if a non-dispatched `staff-*` file is ever added (the doc claim "personas
  spawned by the review skills" would stay true while the glob count rose). This
  is why the test lives in `hooks/tests/` (import access). Verify the exact
  symbol name at implementation. Occurrences: `README.md`
  ("eight stack-agnostic personas"), `docs/design-decisions.md`
  ("Eight stack-specific agents", "All eight reviewer agents", and the heading
  "## 3. Specialist reviewer roster (8 personas)" — digit form). Currently
  accurate — the test locks it. *Known limitation:* the README anchor link
  `#3-…-8-personas` also encodes the count; the test catches the heading digit
  but not the separate anchor, which a link-checker would own (out of scope).
- **Bundled skills off = 10.** Ground truth: `settings.json` `skillOverrides`
  entries equal to `"off"`. Occurrence: `docs/skills.md`
  ("Ten bundled skills are disabled … `skillOverrides: "off"`"). Currently
  accurate — the test locks it.

Deliberately **excluded** (chosen in planning), and *why each resists clean disk
ground truth* — not laziness, but the absence of a mechanical source:

- **Project-scoped plugin count.** "Three *workflow* plugins" cannot be derived
  from disk — nothing distinguishes the workflow plugins from `lovable-cloud`
  (a 4th, downstream-facing plugin) mechanically. Hand-fixed below, not tested.
- **`name-only` skill count.** `docs/skills.md:34` "Ten skills *in this repo* use
  `skillOverrides: name-only`" is **correct, not stale**: `settings.json` has 12
  `name-only` entries, but 2 are *bundled* skills (`/loop`, `/simplify`), leaving
  10 repo skills. The literal count (12) ≠ the claimed count (10) only because
  "in this repo" excludes bundled skills — a distinction with no on-disk marker.
  Locking it would require a hardcoded bundled-vs-repo list that itself drifts,
  so it stays excluded.

**Match-found + exactly-one-match guard.** For every occurrence the test asserts
the regex matches **exactly once** in its file (`len(re.findall(...)) == 1`) —
not merely ≥1, and not first-match-only. Zero matches catches a reworded
sentence (mirrors the "nothing escapes the roster" guard in
`test_agent_roster.py:120`); >1 match catches a relocated or duplicated count
sentence that would otherwise let the test silently validate the wrong instance.
Each occurrence regex must therefore anchor on the noun phrase adjacent to the
number (e.g. `(\w+) stack-agnostic personas`, `(\w+) bundled skills are
disabled`) so it is specific enough to match exactly once. The capturing group
is always `(\w+)` or `(\d+)` — never a hardcoded word like `(Ten)` — so a doc
update from "Ten" to "Eleven" is caught as a value mismatch, not an invisible
pattern-not-matching failure.

**English-number parsing.** A hardcoded `one..twenty` word→int dict in the test
module (no new dependency — CLAUDE.md grounding rule), keyed lowercase (token
`.lower()`-ed before lookup, so "Eight"/"eight" both resolve). Captured token is
parsed as a digit if numeric, else looked up; a lookup **miss raises** with a
message naming the unparseable token (rather than returning a sentinel that
produces a confusing "claimed None vs actual N").

### Hand-fix the two genuinely-stale claims (same PR)

Both in `docs/skills.md`, both required so the surrounding section is correct
(the test does not cover them, but leaving known-wrong prose defeats the issue's
"clean up current state" half):

- **`docs/skills.md:131`** — "All three plugins are enabled automatically … via
  `enabledPlugins`" is **flatly wrong**: `enabledPlugins` holds only the two
  *official* plugins (both `false`); project-scoped plugins install via
  `claude plugin install <name>@claude-config --scope project` (confirmed in
  the repo's own `CLAUDE.md`). Rewrite to the correct mechanism.
- **`docs/skills.md:123`** + table — reconcile the count with the **4**
  directories in `plugins/`: keep the "this repo's own workflow" framing for the
  three workflow plugins (`skill-management`, `claude-hook-review`,
  `plugin-semver`) but add a sentence acknowledging `lovable-cloud` as a fourth,
  downstream-facing plugin so "in `plugins/`" no longer reads as off-by-one.

**Not touched:** `docs/design-decisions.md:185` ("`/loop`,`/simplify` … set to
`off`"). It sits inside §17, a dated decision record describing the *past* state
before the v2.1.129 transition — accurate history, preserved-record prose under
CLAUDE.md scope Axis 3, not drift.

### CI change-detection gate must include the test's inputs

`tests.yml:58` runs the suite only when a changed path matches a regex. It
currently covers `claude/.claude/(hooks|skills|scripts|tests)/` etc. but **not**
the paths this test reads — so a doc-only PR (exactly when count drift lands)
would skip the test. Replace the `REGEX` with this exact dot-escaped, anchored
string (adds `agents` to the inner group; adds `settings.json`, `README.md`,
`docs/` as top-level arms — all dots escaped, `settings.json` is a file so no
trailing slash):

```
^(claude/\.claude/(hooks|skills|scripts|tests|agents)/|claude/\.claude/settings\.json|plugins/skill-management/|README\.md|docs/|\.github/workflows/tests\.yml|pyproject\.toml|install-dev\.sh|requirements-dev\.txt)
```

Update the explanatory comment at `tests.yml:55-57` to name the new inputs. The
job id `tests` (the branch-protection required-check context string, `:1-7`) is
**not** touched — only the `REGEX` and comment inside the step body change, so
branch protection is unaffected. The PR that edits the regex also re-runs the
suite on itself (`tests.yml` is already a matched path) — self-validating.

Tradeoffs (accepted):
- Most doc/README PRs now run the full ~2-min suite. That is the point — the
  skip-on-doc-only-PRs optimization was the exact hole that let count drift land
  untested.
- **New flake blast radius:** doc/README PRs become blockable by *any* flaky
  test in the suite, where before they skipped CI entirely. Acceptable for this
  repo's deterministic suite; noted so it isn't a surprise.

### Phase / merge ordering (hard constraint, not incidental bundling)

The doc fixes, the new test, and the regex extension **must land in one PR** (or
the regex strictly first). Two unsafe orderings ship dark coverage:
- **Test-first (regex not yet extended):** a doc-only count-drift PR matches no
  regex arm → suite skipped → the new test never runs → drift lands green.
- **Docs-first (regex not yet extended):** same hole — the doc PR that should be
  validated is the one that skips the suite.
The regex-extension-only PR is itself harmless (it only *widens* what runs), so
regex-first is the sole safe split; the single-PR plan satisfies this.

### Lighter alternatives considered

The chosen mechanism (a pytest assertion) is already the lightest primitive that
runs automatically; for completeness, the heavier options rejected:

- **Standalone `audit-doc-counts.sh`** (issue proposal #1/#2): no trigger; rots.
  The test is also ad-hoc runnable (`pytest -k doc_counts`), so it subsumes the
  script's only advantage with nothing to forget to run.
- **PreToolUse commit-gating hook** (issue proposal #3): fires only on an agent's
  `git commit` in a Claude session — misses human commits and CI, and is a more
  invasive mechanism than a content assertion the suite already enforces for all
  authors.

## Critical files

- **Create** `claude/.claude/hooks/tests/test_doc_counts.py` — the registry +
  test. Placed in `hooks/tests/` (not `tests/`) so it can import the
  authoritative reviewer set from its sibling `test_agent_roster.py`
  (single-source-of-truth for the persona count; see Coverage). Reuse
  `from helpers import CLAUDE_DIR` (`claude/.claude/tests/helpers.py:15`; on the
  pytest `pythonpath` per `pyproject.toml:8`); repo root =
  `CLAUDE_DIR.parent.parent`; read `settings.json` via
  `CLAUDE_DIR / "settings.json"`; read README/docs relative to repo root. Model
  the derive-then-assert shape on `test_skills.py:706`.
- **Modify** `docs/skills.md` — fix lines ~123 and ~131 (plugin count + the
  `enabledPlugins` mechanism error) per above.
- **Modify** `.github/workflows/tests.yml` — extend the `REGEX` at `:58` and the
  comment at `:55-57` to include `agents/`, `settings.json`, `README.md`,
  `docs/`.

**Reuse, do not reimplement:** `helpers.CLAUDE_DIR` for path anchoring; the
glob-and-count idiom from `test_plugin_manifests.py:26`; the
read-doc-text-and-assert idiom from `test_skills.py:706`; the roster-completeness
guard idea from `test_agent_roster.py:120`.

## Verification

1. `.venv/bin/pytest claude/.claude/hooks/tests/test_doc_counts.py -v` — passes
   against current docs (both counts are accurate today).
2. Negative — value mismatch: temporarily edit `docs/skills.md` "Ten" → "Nine"
   and rerun; the test fails naming file, claimed (9) vs actual (10). Revert.
3. Negative — match-found guard: temporarily reword the README persona sentence
   so the regex can't match; the zero-match assertion fails. Revert.
4. Negative — exactly-one-match guard: temporarily duplicate the "Ten bundled
   skills are disabled" sentence; the `len(findall)==1` assertion fails on the
   ambiguous match. Revert.
5. Full suite + lint: `.venv/bin/pytest claude/.claude/` and
   `.venv/bin/ruff check claude/.claude/` (run from a worktree via
   `../../../.venv/bin/...`).
6. CI gate: confirm a diff touching only `docs/skills.md` matches the extended
   `tests.yml` regex (e.g. `echo docs/skills.md | grep -E "$REGEX"`).

## Out of scope

- Blanket grep of every numeric claim (issue proposal #1) — false-positive flood.
- Non-derivable prose constants and historical snapshots
  (`docs/case-studies/*`, `docs/reports/*`, "three-tier", "eight-principle").
- The broader name-only count coverage (would require a hardcoded bundled-vs-repo
  skill list that itself drifts).
