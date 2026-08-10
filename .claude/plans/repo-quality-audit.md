# Repo quality audit — findings report and phased cleanup backlog

## Context

**Goal: publish a grounded, verified quality audit of this repo as a durable
document, plus a phased backlog that sequences the cleanup work — without
refactoring anything in this PR.**

The repo has grown to 453 tracked files over 516 commits in five months, almost
entirely through PRs that fixed a bug or added a feature. That growth pattern
produces a characteristic debt shape: each PR was locally correct and reviewed,
but nobody has since asked whether the *population* of hooks, skills, agents,
and docs still agrees with itself. Conventions drift arm-by-arm, facts get
restated in a second place and then diverge, and a module accretes subcommands
until it is the largest file in the repo.

The intended outcome is a decision-ready artifact: a findings document with
every claim tied to a file:line or a grep count, and a phased backlog the owner
can fund selectively. Nothing gets refactored here — this PR ships the report.

Six read-only auditors covered hooks, scripts, skills, the instruction surface,
docs/repo-meta, and tests/CI; four specialist reviewers then reviewed this plan.
Their high-value claims were re-verified directly before being written down —
including three that turned out to be **wrong in an earlier draft of this plan**
and are corrected below.

## Approach

### Chosen design

One PR that adds `docs/reports/2026-08-10-repo-quality-audit/findings.md` (the
full audit) and `docs/reports/README.md` (an index). The backlog lives as a
phased table inside `findings.md`. Follow-up work ships as separate, small PRs,
one phase at a time.

The report goes under `docs/reports/` because that directory already holds
exactly this artifact type — `docs/reports/2026-05-20-code-review-trend-audit/`
has a `plan.md` + `findings.md` pair. Adding an index there also closes an
existing finding: that 2026-05 report is referenced by nothing in the repo
(verified — no inbound link from README, CONTRIBUTING, or `docs/case-studies.md`,
whose index lists 6 case studies, none of them this report). The deliverable's
own placement repairs the orphan.

**The report pins the commit SHA it was derived from**, in its first line. Every
finding cites a `file:line`, and Phases 1–6 will invalidate those anchors as they
land. Without a SHA a reader cannot tell a stale citation from a wrong one.

**Alternatives set aside.** A single mega-PR applying every fix would violate
this repo's own "minimal, targeted changes" rule (root `CLAUDE.md`, Scope
discipline, Axis 4) and would be unreviewable — findings span hooks, Python,
skills, and docs with no shared review context. GitHub issues were set aside
because the repo has no visible issue-tracking convention, while `docs/reports/`
has a working precedent. `docs/case-studies/` was set aside because that format
holds empirical narratives about model behavior, not repo-health audits.

### Assumption ledger

**Root problem:** the repo's conventions, facts, and module boundaries have
drifted apart faster than any single PR's review scope could catch, and no
artifact currently records where.

**Givens** (conditions this plan treats as fixed and beyond its reach):

- `SKILL.md` has no include/import mechanism, so cross-skill duplication cannot
  be factored out — the harness's file format imposes this, not a repo choice.
  [verified: root `CLAUDE.md` "No shared partials across skills"; `docs/skills.md`]
- Stow consumers run on platforms this repo does not control, including stock
  macOS with no GNU coreutils and therefore no `timeout(1)`. The repo already
  encodes this reality in `_lib_capped`'s fallback, so the plan accepts it
  rather than assuming a uniform environment.
  [verified: `claude/.claude/hooks/_lib.sh:29-35`]

**Mechanisms:**

| Mechanism | Justification | Anchors |
|---|---|---|
| Markdown report under `docs/reports/` | Lightest primitive that persists a finding set and survives PR-description loss. Rejected: GitHub issues (external tracker, no repo convention, findings need file:line context a comment box handles poorly); a generated dashboard/script (a build-and-maintain surface for a one-time audit). | root |
| Phased backlog as a table inside the report | Keeps sequencing next to the evidence that justifies it. Rejected: one plan file per phase (8 files for work that may not all be funded — premature); an external project board (leaves the repo, drifts from the evidence). | root |
| Phase 3 reorganizes **inside** `_lib.sh` rather than splitting out a new file | See "The design correction" below — the new-file split failed four independent checks. Rejected: new sibling file (breaks stow materialization + the test harness's hook glob); a `_shared/` directory (same stow problem, plus the repo bans that shape for skills). | row 10 |
| Test-file split *before* source split for `transcript-analysis.py` | Gives each extracted source module a matching test module to verify against. Rejected: extract-shared-helpers only (does not address 25 subcommands in one module); big-bang package split (no per-module verification surface until the end). | row: monolith |

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | Deliverable is a report + backlog; no refactoring in this PR. | `[engineer-verified]` |
| 2 | Cleanup is structural, zero observable behavior change. | `[engineer-verified]` |
| 3 | `transcript-analysis.py` decomposition is in scope to plan. | `[engineer-verified]` |
| 4 | `.claude/plans/` accumulation is intentional and stays. | `[verified: .gitignore:9-11]` — see "A correction to one premise" |
| 5 | Test coverage is near-complete, so cleanup can lean on the suite as a regression net. | `[verified: every hook has a dedicated test file; every script except register-marketplace.sh does; 4,868 tests collect with 0 errors]` |
| 6 | `shellcheck` and `ruff` are clean repo-wide, so remaining defects are structural rather than mechanical. | `[verified: run live by three independent agents; ruff selects only E,F,B,I,UP,SIM per pyproject.toml:6 — no ANN/C901/S, so "clean" is narrower than it sounds]` |
| 7 | The unguarded `timeout` call sites are latent on the owner's machine but live for stow consumers on stock macOS. | `[verified: command -v timeout → /usr/local/bin/timeout here, a Homebrew coreutils symlink, not a system binary]` |
| 8 | Four gate hooks fail **open** on missing `timeout`, not closed — a silent gate bypass, not a noisy failure. | `[verified: guard-settings-session-keys.sh:66-68 and require-worktree-for-git-writes.sh:115-118 exit 0 on empty REPO_ROOT; check-claude-md-length.sh:72-73 and check-skill-length.sh:73-74 read 0 lines from awk and never trip the cap]` |
| 9 | CI cannot catch the missing-`timeout` defect class at all. | `[verified: .github/workflows/tests.yml:25 runs ubuntu-24.04 only, single job, no matrix]` |
| 10 | A **new file** under `claude/.claude/hooks/` is not materialized for existing stow users by `git pull` alone — only `./install.sh` creates the symlink. | `[verified: install.sh:72 stows entry-by-entry; README.md:102 documents this exact counter-case for claude/.local/bin/ wrappers]` |

### The design correction: Phase 3 must not create a new file

An earlier draft proposed splitting `_lib.sh`'s credential/PII regex block into a
sibling `_lib_security_patterns.sh`. Four independent checks each found a
distinct failure on that one surface:

1. **Stow materialization.** `install.sh:72` stows entry-by-entry, so a new file
   gets no symlink in `~/.claude/hooks/` until a user re-runs `./install.sh`.
   Hooks source via `. "$(dirname "$0")/_lib.sh"`, resolved against the symlink's
   own directory. Every existing stow user who pulls without re-installing gets
   either silent loss of the redaction patterns or a hard deny on every gated
   commit. `README.md:102` documents this exact hazard class for `claude/.local/bin/`.
2. **Test-harness glob.** `test_hook_alignment.py:41-52` excludes the *exact*
   filename `_lib.sh`, not a `_lib*` prefix. A new `_lib_security_patterns.sh`
   is swept into `ALL_HOOKS` and fails `test_hook_class_header_present` (:228)
   and `test_hook_documented_in_hooks_md` (:110) — neither of which a helper
   library can satisfy honestly.
3. **Re-sourcing.** `test_lib.py` sources only the fixed `_lib.sh` path, so
   ~40 tests break unless `_lib.sh` internally re-sources the new file.
4. **Fail-open bootstrap.** All 7 consumers of the credential/PII block would
   need the stub-then-source-or-deny wrapper; a plain `source` that fails leaves
   the detection regexes unset — the one failure mode a redaction gate must never
   have.

Four defenses stacked to make one cohesion refactor work is the wrong-foundation
tell this repo's own CLAUDE.md names. **Phase 3 therefore reorganizes within
`_lib.sh`** — delimited sections and a header index — which dissolves all four at
once and still addresses the actual complaint (a 1,232-line file whose header
comment describes only one of its four concern groups). If a true split is ever
wanted, it needs its own plan with a re-stow rollout step, not a line item here.

### Two corrections to the earlier draft's own claims

- **The `_lib_repo_root` population was undercounted.** The draft said "3 idioms
  across 7 hooks." Verified actual: **12 hooks** use `git rev-parse --show-toplevel`,
  across **at least 5** idioms. `nudge-worktree-anchor.sh:103` already uses
  `_lib_capped git -C ... rev-parse --show-toplevel` — the exact end-state Phase 2
  proposes, so it is a template, not a call site to fix.
- **The `--config-dir` collision is already guarded.** The draft called it an
  unresolved semantic collision needing a decision. `main()` at
  `transcript-analysis.py:7798-7812` already detects the ambiguous combination and
  refuses with an actionable message and `sys.exit(2)`. It is a naming wart, not a
  live footgun. The real residual is that the guard's message hard-codes the
  string `transcript-analysis.py` (:7809), which would silently drift if Phase 4b
  moves the entry point — and no test pins that string.

### A correction to one premise

The engineer's answer floated pruning merged plan files, possibly via
`cleanup-merged-branches.sh`. The repo already documents the opposite policy:
`.gitignore:9-11` states `.claude/plans/` is *intentionally tracked* because
plan files ship with their PR, and a "supersede, never delete" rule is written
down — but only inside one of the 135 plan files it governs, which is why it
reads as undiscoverable rather than decided.

The plan therefore does **not** propose pruning. It proposes promoting the
existing policy to a discoverable home (`docs/design-decisions.md` or
`CONTRIBUTING.md`). If the engineer still wants pruning after seeing the
documented policy, that is a deliberate reversal to make explicitly.

### The one place "zero behavior change" needs an explicit carve-out

Thirteen call sites across **four** `hook-class: gate` hooks call `timeout`
without the `command -v timeout` guard `_lib_capped` provides:
`guard-settings-session-keys.sh` via a local `git_capped()` (:29, called at :66,
:74, :80, :81, :84); `require-worktree-for-git-writes.sh` (:115, :135, :177, :283);
`check-claude-md-length.sh` (:72, :73); and `check-skill-length.sh` (:73, :74).
An earlier draft named only the first two — the two length hooks are structural
siblings with the identical bug shape, surfaced by auditing the population rather
than the symptom.

This is worse than a robustness gap. On a box without `timeout` the call exits 127
and each hook reaches a path that declines to act: the first two capture an empty
`REPO_ROOT` and `exit 0`, while the two length hooks pipe no stdout into
`awk 'END{print NR}'`, read `0`, and never trip the growth check — so a `CLAUDE.md`
or `SKILL.md` can exceed its cap unenforced. For
`require-worktree-for-git-writes.sh` this directly contradicts its own stated
design intent at :38-40 ("a gate must fail closed on its own tooling"), and because
the check runs first it neutralizes the fail-closed logic further down at :177 and
:283. No warning is emitted in any of the four.

Two sites match the selector without sharing the bug and stay excluded:
`require-ready-for-review.sh:187-191` (comment documents deliberate
fail-open-on-network-failure so a hanging `gh` cannot stall the tool call), and
`plugins/skill-management/hooks/require-skill-review.sh:202` (inside a block the
file marks "Non-blocking — exits 0 regardless; hard enforcement is in pytest/CI"
at `:180`, with `|| true` on the call).

Fixing it changes behavior only on a platform where the gate is already bypassed,
and only from "silently off" to "on." This is the single deviation from the
zero-behavior-change constraint, called out here for the engineer to sanction.

### Release sequencing (binding on the report PR)

**Phase 2(a) must merge before `findings.md` does** — and 2(a) means all 13 sites,
not a subset. Merging only the `_lib_repo_root` extraction would touch two of them
while the published report claims the gap is closed; whoever lands 2(a) should
diff its changed lines against the 13 before releasing the report. Keeping 2(a) in
its own PR is what makes that diff checkable in one place.

This repo is public, and the section above describes a currently-live gate bypass
in enough detail to save a reader the trouble of deriving it from the source. The
threat model is narrow — these are local developer-workflow guardrails on the
engineer's own machine, not a network-reachable service, and the vulnerable shell
is already public and grep-able in this same repo — so this is a disclosure-hygiene
constraint, not an embargo.

The cost is one ordering decision rather than a content rewrite: land 2(a), then
publish the report describing the gap in the past tense with the fix commit SHA.
If 2(a) cannot land first, the report must reference the tracked fix instead of
describing the live bypass in the present tense.

This constraint binds `findings.md`, not this plan file — a plan is a working
document, and its "Out of scope" section deliberately records the state the work
starts from.

## Critical files

**Created by this PR:**

- `docs/reports/2026-08-10-repo-quality-audit/findings.md` — the audit, pinned to a commit SHA
- `docs/reports/README.md` — index covering this report and the 2026-05 one

**Referenced by the report (not modified in this PR).** Grouped by phase *label*
below, which is not the execution order — the backlog table in `findings.md` is
the single authority on sequencing, and it runs 1a → 2a → 2b → 6 → 3 → 5 → 4a →
4b. Phase 6 in particular sits fourth there, not last: a CI gap that lets a live
gate's tests go unrun is worth closing before any refactor leans on that CI.

*Phase 1a — independent, ships immediately:*
- `claude/.claude/scripts/register-marketplace.sh:29` — replace inline
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` with `_lib_config_dir()`, which adds the
  relative-path rejection guard the inline form lacks. **Reuse:** `_lib.sh:93-107`;
  `marker.sh:8,178` already does exactly this. Add `test_register_marketplace.py`
  with the fix — this is the only script in the repo with no test file.

*Phase 2 — two separate PRs, 2(a) first.* They are split rather than combined
because the publication constraint binds only on 2(a): sharing a PR would make the
small security fix hostage to 2(b)'s larger review (a new `_lib.sh` function, 12
call sites, three new parity test categories), and a GitHub PR merges atomically.
2(a) also stays a self-contained, bisectable diff that can be checked against the
13 sites without accounting for 2(b)'s edits.
- **2(a), the security fix.** Route all 13 unguarded `timeout` sites through
  `_lib_capped`: `guard-settings-session-keys.sh:66,74,80,81,84` (deleting the local
  `git_capped()` at `:29`), `require-worktree-for-git-writes.sh:115,135,177,283`,
  `check-claude-md-length.sh:72,73`, and `check-skill-length.sh:73,74`. Only one of
  those is repo-root resolution — the rest are `git diff --cached`, `git show`, and a
  `python3` parser call — so 2(b) does **not** subsume this, and an earlier draft
  wrongly claimed it did. Leave `require-ready-for-review.sh:191` alone: its comment
  documents deliberate fail-open-on-network-failure.
- **2(a) must also strengthen `_lib_capped` itself.** `_lib.sh:30` probes only for
  `timeout`, while `check-branch-divergence.sh:61-71` probes `timeout` then
  `gtimeout`. Homebrew installs GNU coreutils `g`-prefixed by default, so on such a
  machine `_lib_capped` runs uncapped where that sibling caps correctly. Add the
  `gtimeout` fallback to `_lib_capped` before routing more call sites into it, and
  fold `check-branch-divergence.sh`'s local wrapper into it afterwards.
- **Also in 2(a), at lower severity:** three unguarded sites in `informational`
  hooks — `nudge-error-mode-analysis.sh:152`,
  `nudge-handoff-near-context-cap.sh:104,199`. A lost advisory, not a bypassed
  control, but the same one-line fix.
- **2(b), the helper extraction.** Add `_lib_repo_root` to `_lib.sh` with the guard
  built in, and land it across the 12 `show-toplevel` call sites. **Reuse:**
  `nudge-worktree-anchor.sh:103` is the existing template. Sequenced after 2(a).
  It re-touches exactly two lines 2(a) already fixed —
  `guard-settings-session-keys.sh:66` and `require-worktree-for-git-writes.sh:115`,
  the only repo-root calls in their respective sets. That re-touch is expected;
  note it in the PR body so a reviewer diffing 2(b) alone does not read it as scope
  creep. 2(b) also picks up `check-claude-md-length.sh:58` and
  `check-skill-length.sh:57`, which today have no `timeout` wrapper at all and
  gain one for free from the helper.
- **Required new tests:** a unit test for `_lib_repo_root` in `test_lib.py`
  following the `_lib_config_dir` pattern (:976-1030), covering in-repo,
  in-linked-worktree, and outside-any-repo; plus a missing-`timeout` regression test
  for each of the four affected hooks, following the python3-absent precedent at
  `test_require_worktree_for_git_writes.py:665-692`. None of the four has one today.

*Phase 3 — `_lib.sh` cohesion, in-file only:*
- `claude/.claude/hooks/_lib.sh` (1,232 lines) — its header comment describes one
  charter but the file holds four concern groups (subprocess/marker primitives,
  shell-fragment tokenization, credential/PII regexes + redaction, closed-enumeration
  registries). Add delimited section banners and a header index. No new file.

*Phase 4a — split the test file (12,673 lines):*
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — split by subcommand
  group. Must first decide where the module-scope prelude lives (the
  `spec_from_file_location` loader at :19-23 plus shared helpers `_write_jsonl`,
  `_table_cols`, `_extract_grand_total` at :52-133) — a shared helper module or an
  expanded `conftest.py`, not duplicated per file.

*Phase 4b — split the source (7,823 lines, 25 subcommands, 144 functions):*
- **Target shape must be specified before this is fundable.** The entry point is
  a hard external contract: `docs/transcript-analysis.md:3` states "there is no
  `~/.local/bin/` wrapper," and consumers invoke the literal path — a hook
  (`nudge-error-mode-analysis.sh:152`), 5 SKILL.md files, an agent file
  (`skill-fidelity-reviewer.md:20`), 7+ doc files, and the test shim's
  `spec_from_file_location` against that exact filename. The viable shape is a
  thin `transcript-analysis.py` re-exporting a same-directory package, so the
  path, the `sys.path` sibling-import convention (`:27`), and every caller keep
  working unchanged.
- Hotspots to break up: `_cost_report` (392 lines, :4665), `cmd_review_trace`
  (374, :1599), `cmd_audit_routing` (300, :3943).
- Pin the guard message's hard-coded `transcript-analysis.py` string (:7809) with
  a test before moving anything.

*Phase 5 — instruction surface and docs consistency:*
- `README.md:211` — says "Two agent types ship in `claude/.claude/agents/`", then
  describes three categories; `Explore.md`, the 11th file, is never mentioned.
  **Also extend `test_doc_counts.py`** to pin this claim: it already pins the
  adjacent reviewer-subagent count (:257-263) against the authoritative roster,
  which is exactly why that number stayed correct while this one drifted.
- `claude/.claude/skills/sql-query-conventions/SKILL.md:104-119` — presents
  Postgres-specific EXPLAIN vocabulary (`Index Scan`, `Seq Scan`, `Hash Join`,
  `Nested Loop`, `ANALYZE <table>`) as generic, violating root `CLAUDE.md`'s
  "Global skill bodies stay platform-agnostic" rule. The same file's IN-list
  section (:64-72) already does this correctly with a per-backend table.
- `docs/hooks.md:3,9,67,109` — scopes itself to `claude/.claude/hooks/` but
  documents `require-skill-review.sh`, which lives in `plugins/skill-management/hooks/`.
- Three active hooks documented nowhere: `plugins/npm-semver/hooks/require-npm-version-bump.sh`,
  `plugins/lovable-cloud/hooks/validate-migration-filename.sh`, `consume-migration-token.sh`.
- `claude/.claude/agents/staff-backend-engineer.md:80,112` — Output-format sections
  in reverse order vs. the other 8 reviewer agents.
- `CLAUDE.md:143-148` — restates `claude/.claude/CLAUDE.md:93`'s secrets rule
  instead of deferring to it (no drift yet; the SSOT rule still applies).
- `claude/.claude/CLAUDE.md:75,78` — the same "resolution is measured unreliable"
  fact stated twice in Model Routing.
- `claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md` — restarts its
  heading-numbering scheme mid-file; at 192/200 lines it is the one skill showing
  a genuine accretion tell.
- `docs/design-decisions.md` or `CONTRIBUTING.md` — promote the `.claude/plans/`
  "supersede, never delete" policy to a discoverable home.

*Phase 6 — CI coverage gaps (new; not in the earlier draft):*
- `.github/workflows/tests.yml:141,145` — `pytest claude/.claude/` and
  `ruff check claude/.claude/` never descend into `plugins/`, so the 33 tests in
  `plugins/lovable-cloud/tests/` never run. They cover
  `validate-migration-filename.sh`, a live `PreToolUse` gate wired in
  `plugins/lovable-cloud/hooks/hooks.json:2-9`. A regression there ships green.
  The shellcheck step's own regex (:102) *does* include plugins, so the omission
  is an inconsistency rather than a decision.
- `.github/workflows/tests.yml:38` — `actions/checkout` without
  `persist-credentials: false`, which this repo's own
  `claude/.claude/rules/github-actions-workflows.md:31` requires. No later step
  performs authenticated git (verified: no `git push`, `git commit`, or
  `GITHUB_TOKEN` use anywhere in the workflow).
- **Land this as a draft PR and let CI run once before merging.** `pytest plugins/`
  passes 33/33 and `ruff check plugins/` is clean locally, but the local venv is
  Python 3.14 while CI's `setup-python` pins 3.12 — an untested combination. The
  risk is low (plain subprocess and tempfile assertions, no version-sensitive
  syntax) but a local pass is not evidence about the pinned interpreter.

**Headroom constraint for Phase 5:** `check-claude-md-length.sh` enforces a hard
200-line cap. Root `CLAUDE.md` is at 164; `claude/.claude/CLAUDE.md` at 124. Phase 5
edits to these files must be net-neutral or reducing.

## Verification

This PR adds only documentation, so verification is about the *claims*:

1. `../../../.venv/bin/pytest claude/.claude/ -q` — must pass unchanged.
2. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` and
   `../../../.venv/bin/ruff check claude/.claude/` — must stay clean.
3. **Every file:line in the report re-checked at write time**, and the report
   header pins the commit SHA those anchors refer to.
4. **Every count re-derived**, not quoted from an auditor. The earlier draft's
   "7 hooks" was wrong by 5; that is exactly the failure this step exists to catch.
5. `deny-private-project-refs.sh` fires on `git commit` — all paths repo-relative.
6. `/plan-review`, then `/code-review` before commit.

For the follow-up phases: each must leave the suite green. "No test edits" is
**not** the right invariant — it was wrong for the original Phase 3, and it wrongly
forbids *adding* tests for new code. The correct invariants are:

- Phases 1a, 2, 3, 5, 6: no edits to existing test **assertions**; new tests for
  new code are required, not merely allowed.
- Phase 4a: collected test **node IDs** must be set-equal before and after, via
  `pytest --collect-only -q` diffed as a set. A bare count is insufficient — it
  cannot detect a test dropped in one file and duplicated in another.

## Out of scope

- **Applying any fix.** This PR is the report. Phases 1a–6 are follow-up PRs.
- **Pruning `.claude/plans/`.** The repo documents the opposite policy.
- **Splitting `_lib.sh` into a new file.** See "The design correction." If ever
  wanted, it needs its own plan with a re-stow rollout step.
- **Renaming the `--config-dir` flags.** Already guarded at runtime; a naming
  wart, not a defect.
- **Raising the 200-line `CLAUDE.md` cap or the 200-line skill cap.** Seven skills
  sit at ≥95% of the skill cap. Whether the cap is calibrated correctly is a real
  question, but it is a policy change the engineer owns, not cleanup.
- **Widening the `ruff` rule set** (adding `ANN`, `C901`, `S`). Would surface real
  gaps — return-type annotation coverage ranges 50%–100% across the 4 Python
  scripts — but turning on new lint rules changes CI, not code.
- **Adding a macOS CI leg.** It would catch the missing-`timeout` class that
  Phase 2 fixes by hand, but a runner-matrix change is an infrastructure decision
  with its own cost, not cleanup.
- **The uncommitted `_lib.sh` edit in the main working tree** (an internal-hostname
  regex change, unrelated to this audit). Left untouched.
- **CHANGELOG backfill.** Whether the ~8 recent unlogged PRs warrant entries
  depends on an entry policy that does not exist yet; the report notes the gap and
  recommends stating one.
