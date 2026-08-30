# Reduce full-suite pytest fallback in favor of select-tests.py

## Context

Sessions in this repo too often fall back to running the full pytest suite
locally instead of using `select-tests.py` exclusively, and "there is a gap
in select-tests.py" has become an accepted excuse rather than a bug report
against select-tests.py itself. This matters now because a transcript audit
across ~90 days of sessions found the pattern is systemic, not incidental:
of 9 real raw-pytest-bypass cases found, 8 never even evaluated
select-tests.py, and of select-tests.py's own fallback firings, the same
`docs/*.md` gap recurred across 6+ branches in the last 3 days alone — with
two sessions misdiagnosing the cause to the user because the tool's own
diagnostic message doesn't name the offending path. The intended outcome is
to close the concrete, evidenced gaps found (both in select-tests.py's
domain-rule table and in the upstream places that prescribe raw pytest
before select-tests.py is ever consulted) so that domain-scoped test runs
become the exclusive default for local development, with the few genuinely
legitimate full-suite cases named explicitly rather than re-litigated per
session.

## Clarifying decisions (Step 4)

- **Fix scope:** both select-tests.py's own rule table AND the upstream
  authoring gaps that bypass it entirely (plan-authored Verification
  sections, illustrative examples in other skills, permission-allow
  friction).
- **docs/ and repo-root gap fix:** a blanket low-maintenance rule (new
  general "no known test dependency -> no tests" rule for `docs/` and
  similar repo-root paths), matching the existing `CHANGELOG.md`/
  `.claude/plans/` precedent — not an invasive repo-wide extension of the
  completeness test.
- **Cross-plugin hooks/skills/agents exceptions:** generalize the
  lovable-cloud-only cross-domain exceptions to cover every plugin
  (`npm-semver`, `claude-hook-review`, `plugin-semver` included), since the
  underlying test dependency (`plugins/*/hooks/*.sh` etc. globs) already
  applies to all of them.
- **Legitimate-cases documentation:** yes — write a short canonical list of
  genuinely legitimate full-suite cases (e.g. select-tests.py self-change,
  a PR-description whole-repo Test-Plan-accuracy claim) so sessions stop
  re-litigating the question each time.

## Evidence gathered (Step 3)

### A. select-tests.py rule-table coverage audit

File: `claude/.claude/scripts/select-tests.py` (all line numbers as of this
branch's base, `origin/main`).

1. **Completeness test is scoped too narrowly.**
   `claude/.claude/scripts/tests/test_select_tests.py:462-482`,
   `test_every_real_top_level_claude_dir_is_mapped_or_allowlisted`, only
   audits subdirectories of `claude/.claude/` against `MAPPED_TOP_LEVEL_DIRS`
   / `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS` (`select-tests.py:93-103`). It
   never audits repo-root entries (`docs/`, `plugins/`, `evals/`, repo-root
   `scripts/`, top-level files), nor does it check for a
   correctly-registered predicate (only for an unnamed directory).

2. **`docs/*.md` is the dominant gap.** Only two exact-match exceptions
   exist: `TRANSCRIPT_ANALYSIS_DOC_MD` (empty target) and
   `TRANSCRIPT_ANALYSIS_ARCHITECTURE_DOC_MD` (`SCRIPTS_TESTS_DIR` target,
   because `test_transcript_analysis_architecture_doc.py` reads it by path).
   Every other file directly under `docs/` (`design-decisions.md`,
   `hooks.md`, `skills.md`, `case-studies.md`, `cost-levers-considered.md`,
   `handoff-nudge.md`, `memory-audit-nudge.md`, ~15 more, plus
   `docs/case-studies/` and `docs/reports/` subdirectories) has no rule and
   falls to `unmatched-path`.

3. **Repo-root files/dirs with no rule at all:** `README.md`, `CLAUDE.md`
   (repo-root, distinct from `claude/.claude/CLAUDE.md`), `install.sh`,
   `install-dev.sh`, `.gitignore`, `LICENSE`, `CODE_OF_CONDUCT.md`,
   `CONTRIBUTING.md`, `SECURITY.md`, `requirements-dev.txt`, `.shellcheckrc`,
   `.claude-plugin/marketplace.json`, `.github/` (`dependabot.yml`,
   `workflows/tests.yml`), repo-root `scripts/list-shell-files.sh` (distinct
   directory from `claude/.claude/scripts/`, referenced by name in CI's
   `SHELL_REGEX`, `.github/workflows/tests.yml:117`), and this repo's own
   project-scoped `.claude/rules/*.md`, `.claude/skills/**`,
   `.claude/settings.json`, `.claude/worktree-required`. Also three files
   directly in `claude/.claude/` itself: `CLAUDE.md`, `settings.json`,
   `statusline-command.sh`.

4. **`evals/` mostly unmapped.** Only `evals/run_skill_evals.py` exact-match
   (`SKILL_EVALS_RUNNER`, `.py`-scoped) is covered. `evals/README.md`,
   `evals/measure_subagent_model_resolution.py` and its own test file
   `evals/test_measure_subagent_model_resolution.py`, and `evals/fixtures/**`
   are unmatched.

5. **Skills domain doesn't cover its own test tree — confirmed live bug.**
   `_is_skill_md_change` (`select-tests.py:127-128`) only matches files
   literally named `SKILL.md`. `claude/.claude/skills/tests/test_skills.py`
   itself — the skills domain's own test file — matches no rule and falls
   to `unmatched-path` (transcript case B7 below). Contrast with the hooks
   and scripts domains, which use blanket `_is_under()` rules that already
   include their own test directories.

6. **Cross-plugin exceptions scoped only to lovable-cloud.**
   `_is_lovable_cloud_hooks_change`, `_is_lovable_cloud_skills_or_agents_change`
   (`select-tests.py:147-152`) exist because `test_hook_alignment.py`,
   `test_lib.py`, `test_skills.py`, and `test_agent_roster.py` glob
   `plugins/*/hooks/*.sh`, `plugins/*/skills/*/SKILL.md`,
   `plugins/*/agents/*.md` generically — i.e. the test dependency applies to
   *every* plugin, not just lovable-cloud. Existing test cases
   (`claude/.claude/scripts/tests/test_select_tests.py:206,219,229,239`)
   currently assert `npm-semver`, `claude-hook-review`, and `plugin-semver`
   hooks/skills changes fall open to full-suite — confirmed as the narrow
   (not generalized) current behavior.

7. **Diagnostic message doesn't name the offending path.**
   `select-tests.py:402`: `print(f"select-tests: running the full suite
   ({selection.reason})", ...)` — prints only the reason code
   (`unmatched-path`, `global-trigger`, etc.), never which path(s) triggered
   it. `select_pytest_targets` (`select-tests.py:256-285`) already knows
   the exact non-matching path when it returns early on `not matched`
   (line 283) but discards it.

8. **Fail-open design philosophy is intentional and documented**, and
   mirrors CI's own `SKIP_REGEX` gate (a short denylist of excusable paths,
   `.github/workflows/tests.yml:89-107`) — see
   `.claude/plans/selective-test-runs.md` (still present in the repo,
   canonical rationale doc cited by `select-tests.py:4-5`) and
   `README.md:524-530` (the only in-repo human-facing description of the
   mechanism). The most recent narrowing of the fallback is recorded in
   `CHANGELOG.md:12`: `.claude/plans/`, `CHANGELOG.md`, and
   `docs/transcript-analysis.md` were moved from unmatched-path to an
   explicit empty-target rule on the stated criterion "no test in the suite
   reads any of these by path or subprocess" — the same criterion the new
   `docs/` blanket rule should apply.

### B. Transcript audit — historical full-suite fallback cases

Corpus: `transcript-analysis.py sessions --this-repo --paths
--include-subagents` (80 project dirs, 6 declared roots, 3,572 transcript
files), sessions dated 2026-08-06 through 2026-08-30.

**Category A — raw pytest invoked directly, select-tests.py never
consulted (8 of 9 real cases; provenance, not in-session judgment):**
- Dispatcher/coordinator-dictated commands (2 sessions)
- A `/plan-it`-authored plan's own Verification section prescribing raw
  `pytest claude/.claude/` (2 sessions, including a hooks-only diff that
  would have mapped cleanly to select-tests.py's hooks domain)
- Inherited handoff task-list wording ("run the full check suite")
- Self-directed "let's be thorough" / "final confirmation" defaults with no
  select-tests.py check
- One pre-`select-tests.py`-adoption `/ready-for-review` skill-text version
  (already fixed on current `main`)
- One case (`install.sh` in the diff) where select-tests.py's own logic
  would independently resolve to `unmatched-path` anyway — same outcome
  either way, a process-discipline gap rather than a coverage gap.

**Category A — the one structurally legitimate case found:** a
`pr-description`-driven whole-repo Test Plan accuracy claim, which caught a
real unrelated pre-existing failure (`test_shellcheck.py`) that a scoped
run would have missed. `pr-description/SKILL.md`'s current Test Plan
guidance (quoted below) does not address the scoped-vs-whole-repo
distinction either way.

**Category B — select-tests.py's own `unmatched-path` firings (6+
branches, 2026-08-28 through 2026-08-30 alone):** `docs/*.md` files
(`design-decisions.md`, `hooks.md`, `handoff-nudge.md`, `case-studies.md`,
`memory-audit-nudge.md`, etc.) bundled with in-scope hook/skill changes,
plus `README.md`, `install.sh`, `.gitignore`, `claude/.claude/settings.json`.
One case (B7) hit the skills-test-tree bug in A.5 above.

**Two live misdiagnosis cases:** two independent sessions told the user (or
themselves) the fallback was caused by `.claude/plans/*.md` — which is
actually routed to an empty target — because the tool's stderr names only
the reason code, not the offending path (see A.7 above). One of these was
directly contradicted by the same session's own earlier `git status
--porcelain` output.

### C. Upstream authoring-gap audit (where raw pytest gets prescribed
before select-tests.py is ever consulted)

1. **`claude/.claude/skills/plan-it/SKILL.md:105`** — the entire
   Verification-section instruction is the generic line `"4. **Verification**
   — how to test end-to-end"`. No default test command, no mention of
   `select-tests.py`. `claude/.claude/agents/plan-architect.md` (line 15)
   delegates the section's grammar entirely to this line and adds nothing
   itself — `grep -n "pytest\|select-tests\|full suite"` returns no hits in
   either file. This directly explains the plan-authored raw-pytest
   Verification-section cases in Category A above.

2. **`claude/.claude/skills/subagent-delegation/SKILL.md:76-77`**, section
   "Heavy command output — run inline": `"**Enumerate check commands and
   run them one at a time** (e.g., \`pytest claude/.claude/\`, then \`ruff
   check claude/.claude/\`)..."` — the illustrative example itself models
   the raw full-suite command instead of `select-tests.py`. This section is
   reachable both generically and by name from
   `claude/.claude/skills/ready-for-review/SKILL.md`'s own Step 2 pointer
   ("**Run the checks inline** — per `subagent-delegation/SKILL.md` §
   'Heavy command output — run inline'").

3. **`claude/.claude/skills/ready-for-review/SKILL.md:43-50`**, Step 2: "If
   the repo's CLAUDE.md has a Testing or Verification section, use those
   commands. Otherwise inspect the config (`package.json`, `pyproject.toml`,
   `go.mod`, `Cargo.toml`, `Makefile`, CI workflows) to identify the
   project's test, lint, and typecheck commands." This repo's root
   `CLAUDE.md` has no heading literally named "Testing" or "Verification" —
   the select-tests.py guidance lives under `## Commands` instead
   (`grep -n "^#\|^##" CLAUDE.md` confirms no "Testing"/"Verification"
   heading exists). Reported as a structural fact; current sessions
   apparently still find and use select-tests.py in practice (per
   README.md:530 and CLAUDE.md:18-19 already prescribing it), but the
   skill's own heading-match condition does not literally match this repo's
   heading text.

4. **`.claude/settings.json:10`** and **`claude/.claude/settings.json:24`**
   (the stowed mirror, installs to every stow consumer's
   `~/.claude/settings.json`): `"Bash(pytest claude/.claude/)"` is a
   standing `permissions.allow` entry — the raw full-suite command runs
   with zero confirmation prompt. **No corresponding allow-rule exists for
   `select-tests.py`** (confirmed: `grep -n "pytest\|select-tests"` on both
   files returns no `select-tests.py` entry). This is a structural
   incentive toward exactly the bypass behavior the transcript audit found:
   the friction-free path is the one the plan is trying to discourage.

5. **`claude/.claude/skills/pr-description/SKILL.md:50-57`** — the Test
   Plan section instruction ("state what ran and what it produced, in past
   tense... never fabricated results") does not distinguish a scoped
   `select-tests.py` result from a whole-repo claim, and does not document
   the one legitimate full-suite exception the transcript audit found.

6. **`claude/.claude/agents/code-writer.md:23-28`** already explicitly
   forbids full-suite runs ("**Do not run the full suite.**") — confirmed
   correct, no fix needed here.

7. **`claude/.claude/skills/code-review/SKILL.md`,
   `claude/.claude/skills/handoff/SKILL.md`, and all `staff-*`/
   `ciso-reviewer` agent definitions** — zero mentions of `pytest` or
   `select-tests.py` at all; none of these currently instruct a dispatched
   reviewer to run any test command.

8. Every other `git grep` hit for raw `pytest claude/.claude` / `pytest
   plugins` across the repo is either: historical `.claude/plans/*.md`
   Verification-section records (208 hits, preserved-content per Axis 3, not
   live templates), historical `docs/reports/*` audit findings (preserved),
   a dated `CHANGELOG.md` entry (preserved), test-file docstrings
   documenting how to invoke that specific test file directly
   (`Run with: pytest claude/.claude/` — legitimate, not a full-suite
   prescription), `install.sh`/`install-dev.sh` post-setup contributor
   hints (a first-time setup context, not routine dev-loop guidance), a
   `pyproject.toml` comment, and one live doc table row
   (`docs/skills.md:92`) describing `/verify`'s scope mismatch versus how
   claude-config itself is tested.

## Approach

Two changes, sequenced: first close the evidenced coverage gaps in
`select-tests.py`'s rule table and make its fall-open message name the path
that caused it; then remove the upstream prose and permission asymmetries
that route sessions to raw `pytest` before `select-tests.py` is ever
consulted, and write down the two cases where a full-suite run is still
correct. The tool change is Python plus its own test file; the upstream
change is skill prose, `CLAUDE.md`, and two `settings.json` files.

### docs/ decision (resolved)

**The Step 4 `docs/` decision rested on a premise that does not hold, and the
engineer confirmed the correction below.** Step 4
chose "a blanket low-maintenance rule (new general *no known test dependency
-> no tests* rule for `docs/`…), matching the existing `CHANGELOG.md`/
`.claude/plans/` precedent." Four tests read `docs/` files by path with real
assertions on their content:

- `claude/.claude/hooks/tests/test_hook_alignment.py:99` — `_HOOKS_DOC = _REPO_ROOT / "docs" / "hooks.md"`
- `claude/.claude/hooks/tests/test_doc_counts.py:321,326,331,342,368,379,384,395` — reads `docs/design-decisions.md`, `docs/skills.md`, `docs/handoff-nudge.md` via `REPO_ROOT / rel_path`
- `claude/.claude/skills/tests/test_skills.py:1687` — `(repo_root / "docs/skills.md").read_text()`
- `claude/.claude/scripts/tests/test_transcript_analysis_architecture_doc.py:11` — already carries an exception
- **Found during plan review (`staff-sdet`), missed in the original gathering:**
  `claude/.claude/skills/tests/test_skills.py:2458-2520`,
  `test_doc_has_no_state_path`, parametrizes over `_all_doc_paths()` — every
  `docs/**/*.md` except `docs/reports/**`/`docs/case-studies/**`, plus
  `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `evals/README.md` — for a
  per-account state-path contract. This is a fifth, broader `docs/`
  dependency landing in `SKILLS_TESTS_DIR`, not `HOOKS_TESTS_DIR`. The docs
  blanket and `README_MD` targets below are corrected to include
  `SKILLS_TESTS_DIR` accordingly.

An empty-target rule for `docs/` would therefore silently skip tests that a
`docs/` edit can actually break — the one failure mode `select-tests.py`'s
fail-open design exists to prevent. The design below substitutes a blanket
`docs/** -> HOOKS_TESTS_DIR` rule, which preserves everything Step 4 actually
asked for (one rule, no per-file maintenance, ends the dominant
`unmatched-path` fallback) while staying correct. **Confirmed with the
engineer: the low-maintenance property was the actual intent, not
empty-target routing specifically** — this design proceeds.

The substitution costs one thing worth naming: because
`select_pytest_targets` unions across all matching rules rather than
first-match (`select-tests.py:271-285`), the blanket rule supersedes the
existing `TRANSCRIPT_ANALYSIS_DOC_MD -> ()` entry, so `docs/transcript-analysis.md`
goes from selecting nothing to selecting `HOOKS_TESTS_DIR`. That entry should
be deleted rather than left as a dead no-op.

### What the docs/ rule buys, in the evidenced cases

In every Category B firing the transcript audit found, the `docs/*.md` files
were *bundled with in-scope hook/skill changes* — which already select
`HOOKS_TESTS_DIR`. So for those exact diffs the new rule adds zero additional
tests and converts a whole-suite run into a hooks-only run. That is the
strongest available justification for the blanket target and it comes
directly from the transcript evidence, not from a hypothetical.

### Assumption ledger

**Root:** The full-suite fallback persists for two independent reasons — the
rule table does not cover paths real diffs touch, and upstream prose plus
permission configuration prescribe raw `pytest` before `select-tests.py` is
consulted. Closing only one leaves the other live.

**Givens:**

- **G1. `select-tests.py`'s fail-open-on-uncertainty design stays.** It mirrors
  CI's own `SKIP_REGEX` deny-list gate (`.github/workflows/tests.yml:85-89`),
  which this plan does not own; inverting it is a decision about CI's
  contract, not about the rule table.
- **G2. `claude/.claude/tests/` has no selectable target, so a file only its
  tests read must keep falling open.** `FULL_SUITE_TARGETS` and the
  domain-pair structure are the table's fixed shape; giving that directory a
  target is a separate design decision. This is why
  `claude/.claude/statusline-command.sh` gets no rule below.
- **G3. Every `SKILL.md` edit is gated at commit by `require-skill-review.sh`
  and `check-skill-length.sh`.** A hook owns this; the plan can only budget
  around it.
- **G4. Line caps are hook-imposed:** `CLAUDE.md`/`AGENTS.md` at 200
  (`check-claude-md-length.sh:15,69`), `SKILL.md` at 200 default with
  `pr-description` at 210 (`check-skill-length.sh:10-19,71-75`).

**Mechanisms:**

- **M1. Blanket `docs/** -> HOOKS_TESTS_DIR` cross-domain exception, plus one
  exact-match `docs/skills.md -> SKILLS_TESTS_DIR`.** *anchors: root* — the
  lighter primitives are (a) per-file exact-match constants for the four
  currently-read docs, rejected because a new `test_doc_counts.py` row for a
  fifth doc would silently under-select, and (b) leaving `docs/` unmatched,
  rejected because that is the status quo the evidence indicts.
- **M2. New `DOMAIN_RULES` entry `_is_under(p, SKILLS_TESTS_DIR) -> (SKILLS_TESTS_DIR,)`.**
  *anchors: row 4* — lighter alternative considered and rejected: broadening
  `_is_skill_md_change` to all of `SKILLS_DIR`, which would claim
  `REFERENCES.md`/`ROUTING.md` and break the deliberate fall-open pinned by
  `test_non_skill_md_file_under_skills_is_unmatched_and_falls_open`.
- **M3. Plugin-generic predicates replacing the three lovable-cloud-scoped
  hooks/skills/agents predicates.** *anchors: row 5* — lighter alternatives:
  (a) add three more plugin-specific predicates per plugin, rejected as O(plugins)
  maintenance for a dependency that is already glob-generic; (b) leave as-is,
  rejected because `test_hook_alignment.py:648` reads
  `plugins/skill-management/hooks/require-skill-review.sh` by exact path today.
- **M4. New `triggering_paths` field on `SelectionResult`, printed in the
  fall-open message.** *anchors: row 7* — lighter alternatives: (a) fold the
  path into the `reason` string, rejected because `reason` is a discriminator
  six tests match on exactly and CLAUDE.md bars overloading a discriminator
  with detail; (b) a separate `print` at the return site, rejected because
  `select_pytest_targets` is the pure, tested function and `main` owns I/O.
- **M5. One-line platform-agnostic amendment to `plan-it/SKILL.md`'s
  Verification bullet.** *anchors: row 8* — lighter alternatives: (a) a new
  `.claude/skills/plan-it-claude-config/SKILL.md` project layer, rejected
  because `plan-architect` authors this section and plan-it Step 5's dispatch
  prompt carries only Context and Step 3 findings, so a parent-loaded layer
  never reaches it; (b) editing `claude/.claude/agents/plan-architect.md`,
  rejected because it already delegates the grammar to plan-it's line
  (evidence C.1) and duplicating it there creates two homes for one rule.
- **M6. Delete the repo-specific command from `subagent-delegation/SKILL.md`'s
  example rather than substituting `select-tests.py`.** *anchors: row 9* —
  the lighter primitive *is* the fix: the defect is a global skill body
  naming this repo's paths, and `select-tests.py` would be equally
  repo-specific (worse — it does not exist in other repos).
- **M7. Remove `Bash(pytest claude/.claude/)` from both settings files and add
  exact-match `select-tests.py` allow entries to the project file only.**
  *anchors: row 11* — lighter alternatives: (a) add the `select-tests.py`
  entry and keep the pytest one, rejected because leaving both frictionless
  changes no incentive; (b) remove the pytest entry only, rejected because it
  leaves the asymmetry inverted rather than removed.
- **M8. The legitimate-cases list lives in repo-root `CLAUDE.md`'s Commands
  section.** *anchors: row 12* — lighter alternative: README.md's Tests
  section, rejected because `CLAUDE.md` already carries the "Agents: run
  `select-tests.py`, not the full suite" directive and the exception clause
  belongs with the rule it qualifies, not one indirection away.

**Rows:**

1. `docs/hooks.md`, `docs/design-decisions.md`, `docs/skills.md`, and
   `docs/handoff-nudge.md` are read by tests in `HOOKS_TESTS_DIR`;
   `docs/skills.md` is additionally read by a test in `SKILLS_TESTS_DIR`.
   `[verified: test_hook_alignment.py:99; test_doc_counts.py:321,326,331,342,368,379,384,395; test_skills.py:1687]`
2. `README.md` is read by `test_doc_counts.py` (`HOOKS_TESTS_DIR`) — rows
   `rel_path="README.md"` at lines 316 and 400 pin a reviewer-subagent count
   and a token cap. `[verified: test_doc_counts.py:316,400]`
3. `install.sh` is read by eleven `test_install_sh_*.py` modules in
   `HOOKS_TESTS_DIR` via `Path(__file__).resolve().parents[4] / "install.sh"`,
   and linted by `test_shellcheck.py` (same directory).
   `[verified: test_install_sh_contributor_intent_prompt.py:8; test_install_sh_stale_migration_copy_cleanup.py:15; test_shellcheck.py:31]`
4. `claude/.claude/skills/tests/test_skills.py` matches no rule today, because
   `_is_skill_md_change` requires the literal filename `SKILL.md`. Skills is
   the only domain arm with this bug — hooks, scripts, and lovable-cloud all
   use `_is_under()` blankets that already include their own test directories.
   `[verified: select-tests.py:127-128,173-177]`
5. The cross-plugin test globs are plugin-generic, and `skill-management`'s
   hook is already read by exact path today, so the narrow scoping is a live
   under-match rather than a latent one.
   `[verified: test_hook_alignment.py:42,648; test_agent_roster.py:383; test_skills.py:1990]`
6. `lovable-cloud` is the only plugin with a `tests/` directory, so a
   generalized hooks/skills/agents rule cannot under-select another plugin's
   own tests. This premise needs pinning by a test — if a second plugin gains
   `tests/`, the generalization silently starts under-selecting.
   `[verified: glob of plugins/*/tests/** returns only plugins/lovable-cloud/tests/]`
7. `select_pytest_targets` already holds the offending path at its early
   return and discards it; `main` prints only `selection.reason`.
   `[verified: select-tests.py:282-283,402]`
8. `plan-it/SKILL.md` is 141 lines against a 200 cap, so a three-line
   amendment fits. `[verified: line count of claude/.claude/skills/plan-it/SKILL.md]`
9. `subagent-delegation/SKILL.md` is 175/200 and `ready-for-review/SKILL.md`
   is 196/200. The ready-for-review fix must therefore be a same-line word
   insertion, not an added line.
   `[verified: line counts of both files]`
10. `ready-for-review`'s heading-match gap is real but **unfired** — no
    Category A case was attributed to it, and the single ready-for-review case
    found was a pre-adoption skill-text version already fixed on `main`. It is
    included only because the fix costs zero lines; it is the first item to
    drop if the reviewer wants the change narrower.
    `[verified: plan file evidence B and C.3]`
11. Removing an entry from `permissions.allow` actually reintroduces a prompt
    under this machine's permission mode. **Resolved during plan review:** no
    `Bash(*)` wildcard allow rule exists in either `.claude/settings.json` or
    `claude/.claude/settings.json`, and neither sets `permissions.defaultMode`
    — confirmed by inspecting both files' complete `allow`/`deny` lists.
    Removal does reintroduce a prompt for the bare command; not cosmetic.
    `[verified: .claude/settings.json, claude/.claude/settings.json full allow/deny lists]`
12. Repo-root `CLAUDE.md` is 141 lines against the 200 cap, leaving room for
    an ~8-line list. `[verified: line count of CLAUDE.md]`
13. `Bash(pytest claude/.claude/)` and `Bash(ruff check claude/.claude/)` in
    `claude/.claude/settings.json` are repo-specific paths shipped to every
    stow consumer's `~/.claude/settings.json`, where they can never match a
    command. `[verified: claude/.claude/settings.json:24-25; CLAUDE.md's settings-scoping rule]`
14. `claude/.claude/settings.json` has an uncommitted modification in the
    working tree at plan time (`git status` at session start, main checkout —
    not this worktree). The implementer must inspect it before editing that
    file in the main checkout, if relevant; this worktree's copy is clean.
    `[verified: git status --porcelain on main checkout at session start]`
15. `test_skills.py` reads `claude/.claude/settings.json` for the
    `skillOverrides`-to-`docs/skills.md` cross-check, making `SKILLS_TESTS_DIR`
    a second target for that path. **Resolved during plan review:** confirmed
    by direct grep — `test_skills.py:153,1686,1724` all read
    `claude/.claude/settings.json`. `[verified: test_skills.py:153,1686,1724]`
16. An xdist-worker or infra crash is a legitimate full-suite case.
    `[engineer-verified]` (named in the Step 5 dispatch prompt) — **but the
    plan's own transcript evidence records no such case**, and the correct
    recovery is a scope-preserving serial re-run
    (`select-tests.py -n0`, README.md:513) rather than widening to the full
    suite. **Resolved by the engineer: omit it from the canonical list** —
    naming it would re-legitimize the rationalization pattern this plan
    closes; the `-n0` retry stays available without a canonical-list entry.
17. Step 4's cross-plugin decision names "hooks/skills/agents" and not plugin
    manifests or plugin `scripts/`/`lib/` shell files. Both of those carry
    *documented deliberate* fall-opens (`select-tests.py:54-58` for the
    manifest; `select-tests.py:137-141` for skill-management's `scripts/`), so
    generalizing them would remove a designed safety property outside the
    stated scope. `[engineer-verified: Step 4 wording]` + `[verified: select-tests.py:54-58,137-141]`
18. `plugins/lovable-cloud/agents/` does not exist on disk, so the generalized
    agents predicate covers a not-yet-existing shape — matching the precedent
    already set by `test_lovable_cloud_agents_change_also_selects_skills_and_hooks_tests`.
    `[verified: glob of plugins/*/agents/* returns nothing]`

### Inclusion criterion for new rule-table entries

To keep the change bounded (Axis 4), a rule is added only where the path
**fired in the Category B transcript audit** or is one of the named A-section
bugs (A.5 skills test tree, A.6 cross-plugin, A.7 diagnostic). Evidence A.3
and A.4 name many other unmapped paths; those are recorded in **Out of scope**
with their audit results so a follow-up does not re-derive them. Adding
`LICENSE`/`CODE_OF_CONDUCT.md`/`SECURITY.md`/`CONTRIBUTING.md` empty-target
entries — permitted by CI's `SKIP_REGEX`, which is the repo's own record of
paths "confirmed unread by any test module" — is deliberately excluded: no
evidenced firing.

A useful constraint falls out of that and should be recorded in the file's
comments: **`select-tests.py`'s empty-target set must stay a subset of CI's
`SKIP_REGEX` set.** Anything CI declines to skip is not confirmed-unread, so
`select-tests.py` must not route it to `()`. This is what disqualifies
`.gitignore` (a Category B firing) from an empty-target rule despite no test
appearing to read it — the one-level-up fix is to add it to CI's deny-list
first, making CI the single source of truth, then mirror it here.

### Phase 1 — `select-tests.py` rule table and diagnostic

**New constants**, following the file's existing "constant + comment naming
the reading test" convention:

- `DOCS_DIR = "docs"` — comment cites `test_hook_alignment.py` (`docs/hooks.md`),
  `test_doc_counts.py` (three docs), `test_skills.py:1687` (`docs/skills.md`
  skillOverrides check), and `test_skills.py:2458-2520`
  (`test_doc_has_no_state_path`, which parametrizes over nearly every
  `docs/**/*.md` file for a state-path contract — found during plan review,
  see the corrected target below) — and states why a blanket beats per-file
  constants. No separate `SKILLS_DOC_MD` constant: the blanket's corrected
  target already includes `SKILLS_TESTS_DIR`, so a per-file exception is
  redundant.
- `README_MD = "README.md"` — comment cites `test_doc_counts.py` and
  `test_skills.py:2475` (`test_doc_has_no_state_path` also reads `README.md`).
- `INSTALL_SH = "install.sh"` — comment cites the `test_install_sh_*.py`
  family and `test_shellcheck.py`.
- `CLAUDE_SETTINGS_JSON = "claude/.claude/settings.json"` — comment cites
  `test_hook_alignment.py:151` and `test_doc_counts.py:114,134`, and now
  `test_skills.py:153,1686,1724` per row 15.
- `PLUGINS_DIR = "plugins"`.

**New predicate** replacing the three lovable-cloud-scoped ones, named for
what it matches rather than for a plugin:

```python
def _is_plugin_subpath(path: str, subdirectory: str) -> bool:
    parts = Path(path).parts
    return len(parts) > 3 and parts[0] == PLUGINS_DIR and parts[2] == subdirectory
```

with `_is_plugin_hooks_change`, `_is_plugin_skills_change`, and
`_is_plugin_agents_change` as its three call sites. `_is_lovable_cloud_shell_script_change`
and the `LOVABLE_CLOUD_PLUGIN_MANIFEST` exact-match entry stay exactly as they
are (row 17).

**`DOMAIN_RULES` gains** `(lambda p: _is_under(p, SKILLS_TESTS_DIR), (SKILLS_TESTS_DIR,))`
— a genuine domain pair, so it belongs in this table.

**`DOMAIN_RULES` loses** the `TRANSCRIPT_ANALYSIS_DOC_MD -> ()` entry and its
constant, superseded by the docs blanket under union semantics.

**`CROSS_DOMAIN_EXCEPTIONS` gains** — all four are cross-domain by the table's
own definition ("a source change that isn't under a domain's own directory but
still needs that domain's tests re-run"):

- `_is_under(p, DOCS_DIR) -> (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)` — **corrected
  during plan review** (see below); supersedes the `SKILLS_DOC_MD` exact-match
  entry, now redundant under union semantics.
- `p == README_MD -> (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)` — corrected, same
  reason.
- `p == INSTALL_SH -> (HOOKS_TESTS_DIR,)`
- `p == CLAUDE_SETTINGS_JSON -> (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)` (row 15)

and **replaces** `_is_lovable_cloud_hooks_change` /
`_is_lovable_cloud_skills_or_agents_change` / the `LOVABLE_CLOUD_AGENTS_DIR`
entry with:

- `_is_plugin_hooks_change -> (HOOKS_TESTS_DIR,)`
- `_is_plugin_skills_change -> (SKILLS_TESTS_DIR,)`
- `_is_plugin_agents_change -> (HOOKS_TESTS_DIR, SKILLS_TESTS_DIR)`

**Diagnostic fix.** `SelectionResult` gains a defaulted fourth field
`triggering_paths: tuple[str, ...] = ()`, populated on both path-bearing
fall-open reasons (`global-trigger` and `unmatched-path`) and left empty for
`empty-diff` and `git-unavailable`. `select_pytest_targets` changes from
returning on the first unmatched path to accumulating every unmatched path
before returning — same target selection, but the operator sees all offending
paths at once instead of discovering them one run at a time, which is the
shape the "same gap recurred across 6+ branches" evidence describes.
`main` becomes:

```
select-tests: running the full suite (unmatched-path: docs/hooks.md, README.md)
```

No cap on the printed list: the defect being fixed is withheld information, and
truncating reintroduces it.

**Comment corrections inside the scoped file** (Axis 2, code/description prose
not preserved records):

- `select-tests.py:101-103`'s claim that `claude/.claude/tests/` "reads no file
  outside the paths `GLOBAL_TRIGGER_PATHS` already covers" is false —
  `claude/.claude/tests/test_statusline_command.py:17` reads
  `claude/.claude/statusline-command.sh`, and `helpers.py:25,831` reads
  `.github/workflows/tests.yml`. Correct it to state that those reads exist and
  that the affected paths deliberately fall open because that directory has no
  selectable target (G2).
- `select-tests.py:218-222`'s "lovable-cloud is the only plugin whose own
  DOMAIN_RULES entry is broad enough…" paragraph describes the pre-generalization
  state and must be rewritten to describe the new plugin-generic rules.

### Phase 2 — upstream authoring surfaces

**`plan-it/SKILL.md:105`** — replace the bare bullet with a platform-agnostic
instruction to name the project's own documented test command scoped to the
diff, reaching for a whole-suite invocation only where the project documents
that as the command for the case. Deliberately names no tool: the same
sentence produces `select-tests.py` here and the right narrow command
elsewhere.

**`subagent-delegation/SKILL.md:76-77`** — drop the parenthetical
`(e.g., \`pytest claude/.claude/\`, then \`ruff check claude/.claude/\`)` in
favor of a generic enumeration (test, then lint, then typecheck). This both
removes the modeled raw-full-suite command and fixes a standing violation of
the repo's "global skill bodies stay platform-agnostic" rule
(`.claude/rules/skill-and-agent-self-review.md`).

**`ready-for-review/SKILL.md:45`** — broaden "a Testing or Verification
section" to include "Commands", as a same-line word insertion (row 9's budget).

**Repo-root `CLAUDE.md`, Commands section** — append the canonical list
immediately after the existing "Agents: run `select-tests.py`, not the full
suite" paragraph. Two entries, both traceable to evidence:

1. `select-tests.py` itself selected the full suite — a `GLOBAL_TRIGGER_PATHS`
   member or a fail-open path is in the diff. The agent still invokes
   `select-tests.py`; it widens on its own. (Grounded in
   `select-tests.py:111-120`.)
2. `/pr-description` needs a whole-repo Test Plan accuracy claim, where a
   scoped pass would overstate what was verified. (Grounded in the single
   structurally-legitimate Category A case, which caught a real unrelated
   `test_shellcheck.py` failure.)

Followed by the clause that is the actual behavioral change the Context asks
for: anything else — **including a path `select-tests.py` cannot map** — is a
bug in its rule table, not a licence to widen the run by hand. Row 16's
xdist entry is deliberately excluded from this list (engineer decision,
resolved above).

**Wording constraint (`ai-instruction-and-memory-files`, plan review):** state
entry 1 as the outcome only ("you don't need to run the full suite by hand
for this — `select-tests.py` already widens on its own"), not as a
re-explanation of `GLOBAL_TRIGGER_PATHS`/fail-open mechanics — that
explanation already lives in `README.md:524-530` and in `select-tests.py`'s
own comments. Restating the mechanism here would be the same fact held in a
third place with no reader benefit.

**`.claude/settings.json`** — remove `"Bash(pytest claude/.claude/)"`; add
exact-match entries for the two documented `select-tests.py` invocations
(repo-root and worktree-relative, per README.md:527,530). Exact-match only, no
globs, per CLAUDE.md's permission rule. Note that exact matching does not cover
passthrough args, so `-n0`/`-k` runs will still prompt — acceptable, since the
routine invocation is the bare one. Row 11 is resolved above: this removal
does reintroduce a prompt, so it carries real weight.

**Scope-delta disclosure (`ciso-reviewer`, plan review S5):** the exact-match
allow string names only `select-tests.py`, but the *execution* it
pre-approves is whatever `select_pytest_targets` resolves to at run time —
`FULL_SUITE_TARGETS = ("claude/.claude/", "plugins/")`
(`select-tests.py:109`). The prior frictionless rule
(`Bash(pytest claude/.claude/)`) never authorized silent execution of
`plugins/` test content; this one does, on every fall-open — which, per this
plan's own transcript evidence, is the routine case, not a tail risk.
Mitigating factor: `plugins/` is first-party, PR-gated repo content under the
same review pipeline as `claude/.claude/`, not third-party/untrusted code, so
this is a disclosed blast-radius delta rather than a live compromise path.
**Engineer accepted this delta** (plan review, enforcement-invariant
disposition): `plugins/` is first-party, PR-gated repo content at the same
trust level as `claude/.claude/` in practice. The PR body must name this
`claude/.claude/` → `claude/.claude/ + plugins/` frictionless-scope delta
explicitly, and the `/review-permissions` pass this diff already triggers
must confirm it against `plugins/`'s trust level, not only validate the
allow-string's exact-match shape — both required, not optional.

**`claude/.claude/settings.json`** — remove `"Bash(pytest claude/.claude/)"`
(row 13: a repo-specific path that can never match in a consumer's tree). The
sibling `"Bash(ruff check claude/.claude/)"` on line 25 is the identical defect;
recommend removing it in the same edit and recording it under "Incidental
edits" in the PR body, but surface it to the engineer rather than assuming.
No `select-tests.py` entry goes in this file — the path is meaningless outside
this repo.

## Critical files

**Reuse, not reimplementation:** every new rule uses the existing `_is_under`
helper and the existing constant-plus-citation-comment convention; the new
`_is_plugin_subpath` helper replaces three near-duplicate predicates rather
than adding a fourth. `TestRuleTablePathFidelity`'s `_all_targets()` helper
already covers every new directory target with no change.

### Dispatch 1 — rule table and diagnostic

Files:
- `claude/.claude/scripts/select-tests.py` — modify
- `claude/.claude/scripts/tests/test_select_tests.py` — modify

Test work in this dispatch:
- **Update four existing cases** whose expected behavior changes under the
  cross-plugin generalization: `test_skill_management_hooks_and_skills_change_is_unmatched_and_falls_open`
  (line 206), `test_npm_semver_hooks_and_skills_change_is_unmatched_and_falls_open`
  (219), `test_plugin_semver_hooks_and_skills_change_is_unmatched_and_falls_open`
  (229), `test_claude_hook_review_skills_change_is_unmatched_and_falls_open`
  (239). Each must be renamed off "falls_open" and rewritten to assert the
  selected domain, with a docstring citing the glob that justifies it.
- **Leave unchanged** `test_skill_management_scripts_shell_script_change_falls_open`
  (167) and `test_non_skill_md_file_under_skills_is_unmatched_and_falls_open`
  (97) — both pin deliberate fall-opens this change preserves (rows 17, M2).
- **Update** `test_transcript_analysis_doc_md_change_selects_no_tests` (346) to
  the docs blanket's corrected result `{HOOKS_TESTS_DIR, SKILLS_TESTS_DIR}`,
  and `test_transcript_analysis_architecture_doc_md_change_selects_scripts_tests`
  (354) to the union `{SCRIPTS_TESTS_DIR, HOOKS_TESTS_DIR, SKILLS_TESTS_DIR}`.
  (Corrected during plan review — see the `staff-sdet` finding above;
  `test_doc_has_no_state_path` reads both files.)
- **New cases:** docs blanket (an arbitrary `docs/*.md` asserting
  `{HOOKS_TESTS_DIR, SKILLS_TESTS_DIR}`, plus a `docs/reports/**` nested file
  asserting the blanket does not match it); `README.md` asserting the same
  corrected union;
  `install.sh`; `claude/.claude/settings.json`; a file under
  `claude/.claude/skills/tests/`; a non-lovable-cloud plugin `agents/*.md`;
  `triggering_paths` populated for both `unmatched-path` and `global-trigger`
  and empty for `empty-diff`; and a `main()`-level assertion that the printed
  stderr line names the offending path.
- **New fidelity guard** in `TestRuleTablePathFidelity` pinning row 6:
  `lovable-cloud` is the only plugin with a `tests/` directory. Failure message
  must say that a second plugin's `tests/` directory means the plugin-generic
  rules now under-select and the table needs an audit.

Verification: `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
(resolves to the full suite — `select-tests.py` is its own global trigger),
then `../../../.venv/bin/ruff check claude/.claude/`.

### Dispatch 2 — upstream authoring surfaces

Runs after Dispatch 1 lands in the worktree. Files:
- `claude/.claude/skills/plan-it/SKILL.md` — modify (Verification bullet)
- `claude/.claude/skills/subagent-delegation/SKILL.md` — modify (example)
- `claude/.claude/skills/ready-for-review/SKILL.md` — modify (heading list, same-line)
- `CLAUDE.md` — modify (Commands section, legitimate-cases list)
- `.claude/settings.json` — modify (permissions)
- `claude/.claude/settings.json` — modify (permissions; row 14's pre-existing
  main-checkout modification is not in this worktree, confirmed clean)

Sequenced rather than parallel: both dispatches share the one feature
worktree, and CLAUDE.md's "a path `select-tests.py` cannot map is a bug in its
rule table" clause reads as a promise the rule table should already keep.

Verification: same two commands. Three `SKILL.md` files change, so `/code-review`
will dispatch `/skill-review`, and `require-skill-review.sh` blocks the commit
until its marker is written. Two settings files change, so
`ask-review-permissions.sh` will ask for `/review-permissions`. Both are
expected, not failures.

## Verification

1. **Rule-table behavior, per evidenced case.** For each Category B firing,
   assert the new selection with `select_pytest_targets` directly rather than
   by running the tool: a `docs/*.md` file bundled with a hooks change now
   yields `{HOOKS_TESTS_DIR, TRANSCRIPT_ANALYSIS_TEST_GLOB}` instead of
   `FULL_SUITE_TARGETS`; `README.md` and `install.sh` yield
   `{HOOKS_TESTS_DIR}`; `claude/.claude/skills/tests/test_skills.py` yields
   `{SKILLS_TESTS_DIR}`; `plugins/npm-semver/hooks/require-npm-version-bump.sh`
   yields `{HOOKS_TESTS_DIR}`.
2. **Diagnostic message.** Run the tool on a working tree containing one
   deliberately unmapped path (e.g. `touch .gitignore`-equivalent edit) and
   confirm stderr reads `running the full suite (unmatched-path: .gitignore)`
   — the literal defect the two misdiagnosis cases produced. Confirm a
   `pyproject.toml` edit produces `(global-trigger: pyproject.toml)`.
3. **No under-selection introduced.** Run the full suite once on the final
   branch state (`../../../.venv/bin/pytest claude/.claude/ plugins/`) — this
   is legitimate-case 1: `select-tests.py` is in the diff, so the tool selects
   it anyway. Compare against the merge-base to confirm no new failure, per
   CLAUDE.md's prove-your-change-caused-it rule.
4. **Line caps hold.** After the Phase 2 edits, confirm `CLAUDE.md` <= 200,
   `plan-it/SKILL.md` <= 200, `subagent-delegation/SKILL.md` <= 200, and
   `ready-for-review/SKILL.md` <= 200. The last has four lines of headroom, so
   verify it before committing rather than discovering it at the hook.
5. **`test_doc_counts.py` still passes after the `CLAUDE.md` edit.** It pins
   numeric counts in `README.md` and `claude/.claude/CLAUDE.md`; neither is
   edited here, but the run confirms it.
6. **Permission change actually bites (row 11).** Resolved above — confirm in
   a fresh session that a bare `pytest claude/.claude/` now prompts, as a
   final sanity check before claiming this in the PR body.
7. **Shell lint unaffected:** `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
   — no shell files change, so this is a no-op confirmation.

## Out of scope

**Audited, deliberately no rule added** (evidence A.3/A.4 paths with no
Category B firing; each falls open safely today, and the audit result is
recorded here so a follow-up need not re-derive it):

- `evals/**` beyond the existing `run_skill_evals.py` entry — read by
  `test_trigger_detector.py:43` and `test_skills.py:2479` (`SKILLS_TESTS_DIR`).
- `.claude/rules/*.md` (repo-root) — read by `test_rules_frontmatter.py:29`
  (`SKILLS_TESTS_DIR`).
- `.claude/skills/**` (repo-root project skills) — globbed by
  `test_skills.py:1989` (`SKILLS_TESTS_DIR`).
- `.claude-plugin/marketplace.json` — read by `test_plugin_manifests.py:17`
  (`SKILLS_TESTS_DIR`).
- `.github/workflows/tests.yml` — read by `test_ci_path_filter.py:30`,
  `test_shellcheck.py:193`, and `helpers.py:25` (all reaching
  `HOOKS_TESTS_DIR`).
- `install-dev.sh` and `requirements-dev.txt` — read by `test_install_dev.py:44,92`
  (`SCRIPTS_TESTS_DIR`); `install-dev.sh` also linted by `test_shellcheck.py`.
- `.shellcheckrc` and `scripts/list-shell-files.sh` — read by
  `test_shellcheck.py:412,31` (`HOOKS_TESTS_DIR`).
- `claude/.claude/CLAUDE.md` — read by `test_doc_counts.py:169` and
  `test_skills.py:65` (`HOOKS_TESTS_DIR` + `SKILLS_TESTS_DIR`).
- `claude/.claude/statusline-command.sh` — **must keep falling open** (G2): its
  own test lives in `claude/.claude/tests/`, which has no selectable target.
- `.gitignore` and repo-root `CLAUDE.md` — no test read found, but CI's
  `SKIP_REGEX` does not list them, so the correct order is to add them to CI's
  deny-list first and mirror here second.
- `LICENSE`, `CODE_OF_CONDUCT.md` — eligible for empty-target rules under CI's
  `SKIP_REGEX`, excluded for want of an evidenced firing.
- `SECURITY.md`, `CONTRIBUTING.md` — **audit correction (`staff-sdet`,
  post-plan-review):** these are not, in fact, unread — both are in
  `test_skills.py:2458-2520`'s `_all_doc_paths()` corpus alongside `README.md`.
  No rule is added for them here regardless (they still safely fall open
  today under `unmatched-path`, and per this plan's own inclusion criterion, a
  rule is added only where a path fired in the Category B transcript audit —
  neither did), but the earlier "no evidenced firing" framing was also, on
  its own terms, an inaccurate no-test-reads-this claim; recorded here so a
  follow-up doesn't repeat it.

**Deliberate non-changes to `select-tests.py`:**

- Generalizing `LOVABLE_CLOUD_PLUGIN_MANIFEST` to `plugins/*/.claude-plugin/plugin.json`
  and generalizing plugin `scripts/`/`lib/` shell coverage — both would remove
  documented deliberate fall-opens outside Step 4's stated scope (row 17). The
  manifest case is worth a follow-up specifically because `plugin-semver`
  forces a version bump on every plugin change, so those paths appear in diffs
  often.
- Extending `test_every_real_top_level_claude_dir_is_mapped_or_allowlisted` to
  audit repo-root entries or to cross-check `MAPPED_TOP_LEVEL_DIRS` against a
  real predicate — explicitly declined at Step 4 as "an invasive repo-wide
  extension of the completeness test."
- Revisiting the `.claude/plans/ -> ()` and `CHANGELOG.md -> ()` empty-target
  rules in light of `test_nudge_transcript_toolkit.py:116`'s
  `REPO_ROOT.rglob("*.md")`, which reads eight `.claude/plans/*.md` files
  today. The dependency is content-insensitive in practice — the hook is
  silent on all markdown by extension, so adding one more matching file cannot
  flip the assertion, and only removing the last matching file in the repo
  could. The rules stand; only the docs blanket's comment needs to record why
  this corpus read is not a per-file dependency.

**Deliberate non-changes upstream:**

- `pr-description/SKILL.md` — the evidenced legitimate case is already answered
  by `CLAUDE.md`'s list, which a `pr-description` session loads; restating it
  in the skill would be a second home for one rule and a compounding layer.
  The skill is also at 198/210 lines, and the content is repo-specific, so it
  would have to go in a project layer regardless.
- `claude/.claude/agents/plan-architect.md` — already delegates the
  Verification-section grammar to `plan-it/SKILL.md`, so M5 reaches it without
  an edit.
- `code-review/SKILL.md`, `handoff/SKILL.md`, the `staff-*` and
  `ciso-reviewer` agents — evidence C.7 confirms none instructs a dispatched
  reviewer to run any test command, so there is nothing to fix.
- `code-writer.md` — evidence C.6 confirms it already forbids full-suite runs.
- A `.claude/skills/plan-it-claude-config/SKILL.md` project layer naming
  `select-tests.py` — the generic M5 amendment plus `CLAUDE.md`'s directive
  already cover this repo; adding a third statement is the compounding-layers
  tell.
- `README.md` — a one-line pointer to `CLAUDE.md`'s new list was considered and
  cut under Axis 4; README.md:524-530 already documents the mechanism and
  README is not the surface an agent reads at decision time.

**Raised to the reviewer, not fixed here:** `claude/.claude/skills/pr-description-claude-config/SKILL.md`
lives under the stow package, but `pr-description/SKILL.md:28-29` globs
`.claude/skills/pr-description-*/SKILL.md` **from the repo root** — the same
placement the `code-review-claude-config` and `plan-review-claude-config`
layers use. If that glob is the only load path, this repo's `pr-description`
layer never loads here. Not verified end-to-end and not in this plan's scope,
but it should be checked before anyone relies on a `pr-description` project
layer.
