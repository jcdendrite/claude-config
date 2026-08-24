# Selective local test runs by changed-file domain

## Context

Running the full pytest suite locally (117 test files, ~4,737 `def test_`
functions across hooks/, scripts/, skills/, and plugins/lovable-cloud/)
causes resource contention on the engineer's machine, even though CI runs
the whole suite unconditionally on every push once its own coarse gate
passes. The goal: a local-only, opt-in command that runs just the tests
relevant to what's actually changed, falling back to the full suite
whenever the mapping is uncertain — CI stays the correctness backstop, so
an occasional local false negative is recoverable, not silently wrong.

## Approach

A new script (`select-tests.py`) computes the changed-file set (merge-base
diff with `main`, unioned with the dirty working tree) and maps it through
a small, explicit, hand-authored rule table — one rule set per test domain
plus a short list of cross-domain exceptions — to a set of pytest target
paths, then execs `pytest` against just that set. Any changed file the
table doesn't recognize, or a change to the table/script itself, falls
back to the full suite. This is deliberately the same technique CI's own
`SKIP_REGEX` gate already uses (a hand-authored path→decision map,
fail-open on the unmatched case), applied at finer, per-domain grain
instead of whole-suite/skip.

**Root problem:** local pytest runs exercise the whole suite regardless of
diff scope, causing machine contention; CI already re-runs everything on
push, so the local run doesn't need to be exhaustive to remain safe.

**Givens:**
- CI's `.github/workflows/tests.yml` runs the full suite (once its own
  `SKIP_REGEX` gate passes) on every push and PR — this plan does not
  touch CI. [engineer-verified: framed by the user as the source of truth]

**Mechanism justifications** (`anchors: root` unless noted):

- **Hand-authored path→test-target rule table, not an automatic
  dependency-analysis tool.** [verified: Nx `ImplicitDependency` reference,
  Google TAP paper, pytest-testmon README/blog — see below]
  Two lighter/more-fitted alternatives were found and rejected before
  settling on this:
  1. **pytest-testmon** (coverage.py-based dependency tracking, would give
     finer per-test-function granularity automatically). Rejected:
     testmon.org's own docs state it does not track "static files (txt,
     xml, other project assets)" or "external services (reachable through
     network)" — its mechanism is coverage.py line-tracking of in-process
     Python execution only. This repo's suite predominantly exercises
     `.sh` hook scripts via `subprocess` and `SKILL.md` files via path
     read (73 hook test files, plus `scripts/tests/test_transcript_analysis.py`
     reading `skills/*/SKILL.md` by path) — exactly testmon's documented
     blind spot. It would silently under-select on the repo's single most
     common change type. Also a new third-party dependency requiring a
     first full run to seed `.testmondata`, for a tool whose accuracy
     guarantee doesn't hold here.
  2. **An automatically-derived project dependency graph** (Nx- or
     Bazel-style `deps`/`rdeps` graph walk). Rejected as over-powered: both
     tools solve N-package/N-target monorepos with an existing package
     manifest or `BUILD` file graph to derive from; this repo has ~4 real
     test domains and no existing project-graph concept, so building one
     is heavier machinery than the flat rule table it would replace.
     Notably, both tools *also* fall back to hand-authored edges for
     exactly the case this repo hits (see the Nx `ImplicitDependency`
     citation under External-pattern grounding below) — so even the
     heavier tools don't claim full automatic coverage here either;
     adopting one would mean building the graph machinery *and* still
     hand-authoring the same exceptions this plan writes directly.

- **Fail-open default (run everything) on any unmatched path or a change
  to the selection script/table itself.** [verified: CI's own
  `.github/workflows/tests.yml` `SKIP_REGEX` step comment: "everything
  else defaults to running the suite, so a new test-input path never
  needs this list updated to be covered"] Mirrors the existing CI gate's
  philosophy at finer grain — anchors: root.

- **Diff baseline = merge-base(HEAD, origin/main) ∪ working-tree dirty
  files.** [engineer-verified] Matches Nx's own `--base=main --head=HEAD`
  affected-computation model for the committed portion, extended to cover
  the pre-commit dev loop the user is actually optimizing (edits not yet
  committed). anchors: root.

**External-pattern grounding** (verbatim primary-source quotes):

- **Google's Test Automation Platform (TAP)** — Leong, Singh, Papadakis,
  Le Traon, Micco, *"Assessing Transition-based Test Selection Algorithms
  at Google"* (ICSE-SEIP 2019), §II: "At every code commit, TAP is
  responsible for identifying the set of affected tests - a subset of
  tests containing all tests possibly impacted by the commit. TAP
  produces this subset by evaluating whether the code commit modified
  files in the transitive closure of a test's dependencies." This is the
  general shape being reused: changed files → tests whose dependency set
  contains them. TAP computes the transitive closure via Google-scale
  static/dynamic analysis; this plan computes an equivalent closure via a
  manually curated table, appropriate at this repo's scale (no existing
  build-graph tooling to derive it from).
  - Caveat surfaced, not hidden: the same paper's §VI-F "shared
    directories" algorithm (score by common path-prefix length between a
    modified file and a test target) "performs similarly to random" in
    their evaluation. That result is for *ordering* an already-known
    affected set for prioritization (their TCP use case) — this plan uses
    directory grouping only for set *inclusion*, not ordering — but it's
    the reason the design doesn't lean on raw directory-adjacency alone
    and instead pairs it with explicit exception rules (next citation).
- **Nx `ImplicitDependency`** (Nx devkit reference,
  nx.dev/docs/reference/devkit/ImplicitDependency): an implicit dependency
  is "a connection without an explicit reference in code" between two
  projects, and declaring one manually is "the best way to manually set
  up a dependency between two projects that Nx is not able to detect
  automatically." Directly analogous to
  `scripts/tests/test_transcript_analysis.py`'s shell-out into `hooks/`
  and path-reads into `skills/`: a real dependency with no import to
  auto-detect, requiring the same hand-declared edge Nx's own tooling
  falls back to.
- **pytest-testmon** (github.com/tarpas/pytest-testmon README;
  testmon.org): confirms the coverage.py-based mechanism and its
  documented non-coverage of "static files (txt, xml, other project
  assets)" and "external services" — see Mechanism justifications above.
- **pytest built-ins** (docs.pytest.org how-to/cache.html): `--lf`/
  `--last-failed` "only re-run the failures"; no first-party mechanism
  maps a changed *production* source file to affected tests. Confirms a
  new script is necessary regardless of which selection strategy is
  chosen — pytest itself has nothing to reuse here.

## Critical files

- **`claude/.claude/scripts/select-tests.py`** (new) — computes the
  changed-file set, applies the domain rule table, execs `pytest` against
  the resolved target paths (plus any passthrough CLI args). Reuse:
  none existing in-repo (explore confirmed no Makefile/justfile/pre-commit
  wrapper exists to extend).
  - Domain rules (from explore agent's directory/dependency audit):
    - `hooks/` ↔ `hooks/tests/`.
    - `scripts/` ↔ `scripts/tests/`, matched by directory rather than by
      the `transcript_*` filename-prefix subset, since a plain scripts/
      change has no reason to narrow further.
    - `skills/` (any `SKILL.md`) ↔ `skills/tests/`.
    - `plugins/lovable-cloud/` ↔ `plugins/lovable-cloud/tests/`.
  - Cross-domain exception rules:
    - A `hooks/` or `skills/` change also adds
      `claude/.claude/scripts/tests/test_transcript_analysis*.py` — that
      file shells into specific hook scripts and reads specific
      `SKILL.md` files by path (see Approach).
    - A change under `plugins/skill-management/scripts/` or
      `evals/run_skill_evals.py` also adds `claude/.claude/skills/tests/`.
    - A change to `plugins/lovable-cloud/.claude-plugin/plugin.json` also
      adds `claude/.claude/skills/tests/` — `test_plugin_manifests.py`
      globs every plugin's manifest by path.
    - A change under `plugins/lovable-cloud/hooks/` also adds
      `claude/.claude/hooks/tests/` — `test_hook_alignment.py` and
      `test_lib.py` both glob every plugin's `hooks/*.sh` by path.
    - A change under `plugins/lovable-cloud/skills/` or
      `plugins/lovable-cloud/agents/` also adds
      `claude/.claude/skills/tests/` — `test_skills.py` globs every
      plugin's `skills/*/SKILL.md`, `skills/**/REFERENCES.md`, and
      `agents/*.md` by path.
    - A change under `plugins/lovable-cloud/scripts/` or
      `plugins/lovable-cloud/lib/` also adds `claude/.claude/hooks/tests/`
      — `test_shellcheck.py` lints every tracked shell script in the repo,
      not only `claude/.claude/hooks/`.
    - A change to `claude/.claude/skills/handoff/SKILL.md` also adds
      `claude/.claude/scripts/tests/` — `test_check_handoff.py` reads that
      exact file by path.
  - Global triggers (any changed file among these → run the full suite,
    no domain narrowing): `claude/.claude/tests/helpers.py` (imported by
    hooks/, scripts/, skills/, and plugins/lovable-cloud/ test dirs),
    `pyproject.toml`, and `select-tests.py` itself (a changed, possibly
    broken selector can't be trusted to correctly select tests for
    itself).
  - Any changed path matching none of the above → run the full suite
    (fail-open, matching CI's own `SKIP_REGEX` philosophy).
- **`claude/.claude/scripts/tests/test_select_tests.py`** (new) — unit
  tests for the rule-table mapping function against a synthetic list of
  changed paths (no real git calls), plus git-backed smoke tests against
  throwaway fixture repos. Covers, per `/plan-review`'s `staff-sdet`
  pass:
  - Each domain rule, each cross-domain exception, and each global
    trigger individually.
  - **Multi-domain union**: a changed-set spanning two-plus domains
    selects the union of both target sets, not just one.
  - **Global-trigger precedence**: a changed-set mixing a global-trigger
    file with domain-matched files still falls back to the full suite —
    a matched domain rule must not suppress the global trigger.
  - **Unmatched-path fail-open** and the **empty-diff case** (nothing
    changed) — pin its behavior explicitly rather than leaving it
    undefined.
  - **argv construction**: given a known mapping result, the constructed
    `pytest` argv (including passthrough CLI args) is asserted directly,
    with the actual `pytest` subprocess call stubbed rather than shelled
    out for real.
  - **Rule-table path fidelity**: every literal target path in the rule
    table (`hooks/tests/`, `skills/tests/`, etc.) exists on disk —
    catches silent drift if a referenced directory is later renamed.
  - **Smoke tests against a throwaway fixture repo** with an `origin`
    remote and `main` ref configured (so `merge-base(HEAD, origin/main)`
    exercises the real codepath, not a local-only stand-in): one
    covering the committed-diff half of the baseline, one covering the
    working-tree-dirty half (an uncommitted file must be included in the
    computed changed-set — this is the half that makes the tool useful
    pre-commit), and one forcing the merge-base lookup to fail (no
    `origin/main`, detached HEAD) and asserting fail-open to the full
    suite rather than a silent bad selection or crash.
- **`README.md`** §Tests — document the new command as an *additional*,
  opt-in local convenience alongside (not replacing) the existing
  `.venv/bin/pytest claude/.claude/` full-run command.
- **`claude/.claude/skills/test-conventions/SKILL.md`** — fold one new
  bullet into the existing "Test double seams by dependency type" list
  in §3 "Design for testability" (not a new subsection — the file is
  already at its 200-line target; a new header adds unneeded length):
  when a test reaches outside its own domain via a non-import mechanism
  (subprocess call, direct file-path read), that edge is invisible to
  import-based or directory-based test-selection tooling — declare it
  explicitly wherever the codebase tracks domain→test mappings. Keep the
  body text itself vendor-neutral (no tool/vendor name) per `/skill-review`'s
  platform-genericness check — the citation lives in REFERENCES.md only.
- **`claude/.claude/skills/test-conventions/REFERENCES.md`** — new
  section citing Nx's `ImplicitDependency` (verbatim quote above) as the
  primary-source grounding for the new bullet.

## Verification

- `../../../.venv/bin/pytest claude/.claude/scripts/tests/test_select_tests.py -v`
  — new unit tests pass, including the multi-domain-union,
  global-trigger-precedence, empty-diff, argv-construction, rule-table
  path-fidelity, and git-fixture cases above.
- Manual spot-check (the automated tests above are the actual regression
  defense, this just confirms end-to-end wiring once): touch a file under
  `claude/.claude/hooks/` only, run `select-tests.py`, confirm it selects
  `hooks/tests/` + `test_transcript_analysis*.py` and not `skills/tests/`.
  Repeat for a `skills/*/SKILL.md`-only change and a `scripts/`-only
  change.
- Manually: touch `claude/.claude/tests/helpers.py`, confirm it falls back
  to the full suite.
- Manually: touch an unrecognized top-level file (e.g. a new file at repo
  root), confirm fail-open to the full suite.
- Full existing suite still green:
  `../../../.venv/bin/pytest claude/.claude/` and
  `../../../.venv/bin/ruff check claude/.claude/`.
- `/skill-review` on the `test-conventions` diff (hook-enforced on commit
  regardless).

## Out of scope

- Modifying CI's own gating (`.github/workflows/tests.yml`) — CI remains
  the unconditional full-suite backstop; this is a local-only convenience.
- Changing the documented full-suite command (`pytest claude/.claude/`,
  per root `CLAUDE.md` and `README.md` §Tests) — within this plan's own
  reach to change, but deliberately left alone: `select-tests.py` ships
  as a second, additive command so the existing onboarding path and any
  external scripts/muscle-memory referencing the documented command keep
  working unchanged.
- Adopting pytest-testmon or any automatic dependency-analysis tool — see
  Approach's rejection rationale.
- `test-evaluation` skill edit — the cross-domain-coupling lesson is
  authoring-time/preventive (test-conventions' lane), not a symptom
  test-evaluation's existing vocabulary (slow/flaky/wrong-pyramid-layer)
  covers; adding it there would duplicate the rule for no reader benefit.
