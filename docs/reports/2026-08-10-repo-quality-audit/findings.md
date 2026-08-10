# Repo quality audit — findings

**Date:** 2026-08-10
**Audit baseline:** `eb5eae2` (merge-base with `main`). Every `file:line` below
refers to that commit. Line anchors drift as the backlog lands — check the SHA
before treating a citation as stale.
**Repo state at baseline:** 456 tracked files, 519 commits since 2026-03-11,
39 hooks, 27 global skills + 7 plugin skills, 11 agents, 15 scripts, 5 plugins,
138 committed plan files.

The sweep itself ran against `914ccc9`; three commits landed on `main` during the
audit and the branch was rebased onto them. Every citation in a file those commits
touched was re-verified at `eb5eae2` — `nudge-handoff-near-context-cap.sh` had
grown and its line anchors moved, which is exactly the drift this pin exists to
make visible.

## Why this audit ran

Five months of PRs, each fixing a bug or adding a feature, each locally correct
and reviewed. What no single PR's review scope could ask is whether the
*population* still agrees with itself. This audit asked that question across six
domains, then had four specialist reviewers check the conclusions.

## Methodology

Six read-only auditors, one per domain (hooks, scripts, skills, instruction
surface, docs/repo-meta, tests/CI), each required to back any "this is the
convention" claim with a `grep` count over the whole population rather than a
single example. Four specialist reviewers (security, platform, testing, backend)
then reviewed the resulting plan against the code.

Every count in this report was re-derived at write time rather than quoted from
an auditor. That step earned its place: an earlier draft claimed three
repo-root idioms across 7 hooks; the verified figure is **at least five idioms
across 12 hooks**. Two other draft claims were also wrong and are corrected in
the findings below rather than carried forward.

## What is healthy

Stating this first because it constrains what the findings mean. The debt here
is structural drift, not rot.

| Signal | Result |
|---|---|
| `shellcheck` (74 files, repo-wide) | Clean, exit 0 |
| `ruff check claude/.claude/` | Clean |
| Collected tests | 4,868, zero collection errors |
| Hook test coverage | 39 of 39 hooks have a dedicated test file, including all 10 `deny-*` gates |
| Script test coverage | 14 of 15 scripts |
| Test quality | Real subprocess execution; `unittest.mock` in only 5 of ~90 files; no assertion-free files; every `skip` is a genuine platform guard |

The suite also carries a meta-test layer that is unusual for a repo this size:
`test_hook_alignment.py` enforces per-hook `# hook-class:` headers and
`docs/hooks.md` coverage, `test_doc_counts.py` pins documentation numeric claims
to ground truth, and `test_ci_path_filter.py` executes the CI workflow's own
shell body to verify its fail-open logic.

Mechanical defects are therefore not the problem. Everything below is structural.

## Findings

### 1. Four gate hooks call `timeout` without the guard written to protect them — HIGH

`claude/.claude/hooks/_lib.sh:29-35` defines `_lib_capped`, which checks
`command -v timeout` and falls back to an uncapped call when the binary is
absent. It is the established pattern across the hook layer. Thirteen call sites
in four `hook-class: gate` hooks bypass it:

| Hook | Unguarded sites | Fail-open path |
|---|---|---|
| `guard-settings-session-keys.sh` | `:66`, `:74`, `:80`, `:81`, `:84`, via a local `git_capped()` defined at `:29` | Empty `REPO_ROOT` → `exit 0` at `:66-68` |
| `require-worktree-for-git-writes.sh` | `:115`, `:135`, `:177`, `:283` | Empty `REPO_ROOT` → `exit 0` at `:115-118` |
| `check-claude-md-length.sh` | `:72`, `:73` | Pipeline yields no stdout, `awk 'END{print NR}'` prints `0`, so the growth check is always false |
| `check-skill-length.sh` | `:73`, `:74` | Same as above |

`_lib.sh:8` names `guard-settings-session-keys.sh`'s `git_capped()` as the
*precedent* for `_lib_capped` — the migration was never done.

Where `timeout` is unavailable, the call exits 127 and each hook reaches a path
that declines to act. No warning is emitted — contrast the deliberate stderr
warning on the jq-failure path at `guard-settings-session-keys.sh:127`. For
`require-worktree-for-git-writes.sh` this is contrary to its own documented intent
at `:38-40` ("a gate must fail closed on its own tooling"); because the check runs
first, it also short-circuits the correctly fail-closed logic at `:177` and `:283`.
For the two length hooks the effect is that a `CLAUDE.md` or `SKILL.md` can grow
past its documented cap unenforced.

**The guard itself is also weaker than a sibling wrapper.** `_lib_capped`
(`_lib.sh:30`) probes only for `timeout`. `check-branch-divergence.sh:61-71`
implements its own wrapper that probes `timeout` *then* `gtimeout`. Homebrew
installs GNU coreutils `g`-prefixed by default, so on such a machine `_lib_capped`
finds nothing and runs uncapped while `check-branch-divergence.sh` correctly caps.
Phase 2 should teach `_lib_capped` the `gtimeout` fallback, not merely route more
call sites into it — otherwise the fix inherits a narrower probe than the code it
replaces.

**Scope boundary.** The 13 sites above are the `hook-class: gate` population,
where the consequence is a gate not firing. Six further unguarded sites sit in
`informational` hooks — `nudge-error-mode-analysis.sh:152` and
`nudge-handoff-near-context-cap.sh:146,178,253,275,398` — where the consequence is
a lost advisory rather than a bypassed control. Worth fixing in the same pass; not
part of the HIGH severity claim.

Two sites match the selector "gate hook with an unguarded `timeout`" without
sharing the bug, and are excluded deliberately:

- `require-ready-for-review.sh:187-191` — the comment documents deliberate
  fail-open-on-network-failure, so a hanging `gh` cannot stall the tool call.
- `plugins/skill-management/hooks/require-skill-review.sh:202` — inside a block
  the file itself marks "Non-blocking — exits 0 regardless; hard enforcement is in
  pytest/CI" (`:180`), with `|| true` on the call. A missing `timeout` surfaces as
  a captured warning string, not a skipped enforcement.

**Reach.** `claude/` is stowed to every user who installs this repo. Stock macOS
ships no `timeout(1)` — it arrives with GNU coreutils. The `_lib_capped` fallback
exists precisely because the author knew this.

**Why no existing signal catches it.** CI is Linux-only and single-job
(`.github/workflows/tests.yml:25`, `ubuntu-24.04`), where coreutils is present —
so this defect class is structurally invisible to CI. Nor does any test stub a
`timeout`-absent `PATH` for any of the four hooks, though the precedent for exactly
that shape exists at `test_require_worktree_for_git_writes.py:665-692` (a
python3-absent fail-closed test).

**Remediation** is Phase 2 below. Note that extracting a `_lib_repo_root` helper
does *not* by itself close this finding: only 1 of the 13 sites
(`guard-settings-session-keys.sh:66`) is repo-root resolution. The others are
`git diff --cached`, `git show`, and a `python3` parser invocation
(`require-worktree-for-git-writes.sh:177`), which no repo-root helper would touch.
Phase 2 must therefore route **every** site in the table above through
`_lib_capped` as its own commit, with the helper extraction as a separate
concern. Regression tests stubbing a `timeout`-free `PATH` are part of that phase,
not optional to it.

### 2. CI never runs a shipped gate hook's tests — HIGH

`.github/workflows/tests.yml:141` runs `pytest claude/.claude/ -v` and `:145`
runs `ruff check claude/.claude/`. Neither path descends into `plugins/`.

`plugins/lovable-cloud/tests/` holds **33 collected tests** covering, among
others, `validate-migration-filename.sh` — a `# hook-class: gate` hook actively
registered as a `PreToolUse` matcher on `Write`
(`plugins/lovable-cloud/hooks/hooks.json:2-9`). A regression in that gate ships
green.

This is an inconsistency rather than a decision: the shellcheck step's own path
regex (`tests.yml:102`) explicitly includes `^plugins/lovable-cloud/scripts/`, so
the workflow already knows plugins exist. A companion plan file
(`.claude/plans/lovable-cloud-utc-migration-enforcement.md:83`) documents the
intended `pytest plugins/lovable-cloud/` command, which was never wired in.

Wiring it in is low-risk: `pytest plugins/` currently passes 33 of 33 in 5s and
`ruff check plugins/` is clean, both verified at the baseline commit. This is a
coverage gap being closed, not a batch of latent failures being exposed.

### 3. The workflow violates the repo's own rule for workflows — MEDIUM

`claude/.claude/rules/github-actions-workflows.md:31` requires
`persist-credentials: false` on `actions/checkout` unless a later step performs
authenticated git. `.github/workflows/tests.yml:38` omits it, and no later step
in that workflow does authenticated git. The persisted token is unused attack
surface.

Otherwise the workflow's hygiene is good: actions pinned to full 40-character
SHAs, `permissions: {contents: read}`, a concurrency group with
`cancel-in-progress`, `timeout-minutes: 5` with a documented budget breakdown,
and a pinned `ubuntu-24.04` runner rather than `latest`.

### 4. `transcript-analysis.py` is a 7,823-line flat module — MEDIUM

25 `cmd_*` subcommands and 144 top-level definitions in one file, with a
12,673-line test file. These are the two largest files in the repo. Function-level
hotspots: `_cost_report` (392 lines, `:4665`), `cmd_review_trace` (374, `:1599`),
`cmd_audit_routing` (300, `:3943`).

**The entry point is a hard external contract**, which constrains any split.
`docs/transcript-analysis.md:3` states "Run it directly from the shell — there is
no `~/.local/bin/` wrapper," and consumers invoke the literal path: a hook
(`nudge-error-mode-analysis.sh:152`), five `SKILL.md` files, an agent file
(`skill-fidelity-reviewer.md:20`), seven or more doc files, and the test shim's
`importlib.util.spec_from_file_location` against that exact filename
(`test_transcript_analysis.py:19-23`). A package split must therefore keep
`transcript-analysis.py` as a real, executable, single-file entry point — a thin
re-export over a same-directory package — or every one of those consumers breaks.

**Correction to an earlier draft of this audit.** The `--config-dir` flag was
initially reported as an unresolved semantic collision, because the top-level flag
replaces the config dir while the same flag on four subcommands appends
(`action="append", dest="extra_config_dirs"`). That mechanical description is
accurate, but the severity was not: `main()` at `:7798-7812` already detects the
ambiguous combination and refuses it with an actionable message and `sys.exit(2)`.
It is a naming wart, not a live footgun. The real residual is that the guard's
message hard-codes the string `transcript-analysis.py` (`:7809`) and no test pins
it, so it would drift silently if the entry point ever moves.

### 5. Documentation drift — MEDIUM

Each verified against actual repo state:

| Claim | Location | Reality |
|---|---|---|
| "Two agent types ship in `claude/.claude/agents/`" | `README.md:211` | The section then describes three categories (8 reviewers, `skill-fidelity-reviewer`, `code-writer`); `Explore.md`, the 11th file, is never mentioned |
| Scope is "every hook in `claude/.claude/hooks/`" | `docs/hooks.md:3` | Documents `require-skill-review.sh` at `:9,67,109`, which lives in `plugins/skill-management/hooks/` |
| — | — | Three active hooks documented nowhere: `plugins/npm-semver/hooks/require-npm-version-bump.sh`, `plugins/lovable-cloud/hooks/validate-migration-filename.sh`, `consume-migration-token.sh` |

The README case is instructive about *why* it drifted. `test_doc_counts.py:257-263`
already pins the adjacent "**Reviewer subagents** — N stack-agnostic personas"
claim against the authoritative roster — and that number is still correct. The
"Two agent types" sentence one line earlier is pinned by nothing. The fix is to
extend the existing harness to cover it, not merely to correct the number.

`docs/reports/` was itself an orphan before this report: the 2026-05-20 audit is
referenced by no README, no CONTRIBUTING, and no entry in `docs/case-studies.md`.
The `README.md` added alongside this report closes that.

### 6. A globally-stowed skill teaches engine-specific behavior as generic — MEDIUM

`claude/.claude/skills/sql-query-conventions/SKILL.md:104-119` ("Verifying query
plans") presents `EXPLAIN`/`EXPLAIN ANALYZE` output vocabulary — `Index Scan`,
`Seq Scan`, `Hash Join`, `Nested Loop`, `ANALYZE <table>` — as universal. These
are PostgreSQL plan-node names, not shared across MySQL, SQL Server, or Oracle.
Root `CLAUDE.md` requires global skill bodies to stay platform-agnostic and names
this exact failure mode.

The same file already knows how to do this correctly: its IN-list section
(`:64-72`) tables per-backend behavior with explicit attribution. The fix is to
match the pattern the file already uses. Unlike `test-conventions` and
`code-review`, this skill has no project-layer escape hatch to push the specifics
into.

### 7. Duplication and convention drift — LOW to MEDIUM

- **Repo-root resolution.** 12 hooks call `git rev-parse --show-toplevel`, across
  at least five idioms (bare; `cd "$CWD" &&`; `cd "$CWD" && timeout 5`; `git -C`;
  locally-capped; `_lib_capped git -C`). No shared helper exists.
  `nudge-worktree-anchor.sh:103` already uses the target end-state and is a
  template rather than a call site to fix.
- **`_lib.sh` cohesion.** 1,232 lines spanning four concern groups (subprocess and
  marker primitives; shell-fragment tokenization; credential/PII regexes and
  redaction; closed-enumeration registries) under a header comment describing only
  the first.
- **Shared Python helper.** `_content_text()` is byte-identical in
  `token-analyzer.py:31-36` and `transcript-analysis.py:90-95`.
- **Argparse block.** `transcript-analysis.py` repeats an identical `--config-dir`
  `add_argument` block verbatim at `:7549`, `:7603`, `:7635`, `:7664`, immediately
  after calling `_add_project_scope_args()` — the helper built for that purpose.
- **Weaker config-dir resolution.** `register-marketplace.sh:29` resolves
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` inline, missing the relative-path
  rejection guard in `_lib_config_dir()` (`_lib.sh:93-107`). `marker.sh:8,178`
  already sources `_lib.sh` and calls it correctly. This is also the one script in
  the repo with no test file.
- **Agent-file drift.** `staff-backend-engineer.md:80,112` orders its
  Output-format sections File-based-then-Inline; the other eight reviewer agents
  use Inline-then-File-based. The 31-line File-based block is otherwise
  byte-identical across all of them.
- **Instruction-surface duplication.** `CLAUDE.md:147-151` restates
  `claude/.claude/CLAUDE.md:93`'s secrets rule rather than deferring to it (no
  drift yet, but the single-source rule applies). `claude/.claude/CLAUDE.md:75`
  and `:78` state the same model-resolution fact twice.
- **Skill heading scheme.** `ai-instruction-and-memory-files/SKILL.md` runs
  `Step 0/1` → `## 1.`–`## 5.` → `## Final step`; at 192 of its 200-line cap it is
  the one skill in the corpus showing a genuine accretion tell. Seven skills sit
  at ≥95% of that cap.

### 8. Not defects — recorded so they are not re-litigated

- **`.claude/plans/` accumulation is deliberate.** `.gitignore:9-11` states the
  directory is intentionally tracked because plan files ship with their PR. A
  "supersede, never delete" policy exists — but only inside one of the 138 files
  it governs, which is why it reads as undiscoverable rather than decided.
  Promoting that line to `docs/design-decisions.md` or `CONTRIBUTING.md` is the
  fix; pruning is not.
- **`evals/` is maintained, not vestigial.** `evals/README.md:10-19` justifies its
  CI exclusion on non-deterministic model-classification output.
- **Cross-skill duplication is intentional.** `SKILL.md` has no include mechanism;
  root `CLAUDE.md` requires duplication over shared partials. Only drift between
  copies is a defect, and the corpus is clean on that.
- **Plugin metadata is consistent.** All five `plugin.json` files carry identical
  field sets, and `marketplace.json` lists exactly the five plugins on disk.
- **Redaction is clean.** No home-rooted paths, private hostnames, or non-owner
  person names outside deliberately placeholder-shaped examples in
  `docs/private-project-redaction.md`.

## Backlog

Ordered by value per unit of risk. Each phase is an independent PR.

| Phase | Work | Why this order |
|---|---|---|
| **1a** | `register-marketplace.sh` → `_lib_config_dir()`, plus its first test file | Independent mechanism, no overlap with anything below |
| **2a** | Teach `_lib_capped` the `gtimeout` fallback, then route all 13 unguarded `timeout` sites in finding 1's table through it, with regression tests | Closes finding 1 and **blocks publication of this report** — see below. Kept to its own PR so the security fix is not hostage to 2b's larger review |
| **2b** | Extract `_lib_repo_root` and land it across the 12 `show-toplevel` call sites | Closes finding 7's idiom sprawl. Re-touches exactly two lines 2a already fixed (`guard-settings-session-keys.sh:66`, `require-worktree-for-git-writes.sh:115`) — expected, not scope creep. Also picks up `check-claude-md-length.sh:58` and `check-skill-length.sh:57`, which have no `timeout` wrapper at all today |
| **6** | Wire `plugins/` into CI's pytest and ruff steps; add `persist-credentials: false` | Findings 2 and 3; small diff, closes a silent-green gap. Land it as a draft PR and let CI run once before merging: the local pass above is on Python 3.14, while CI's `setup-python` pins 3.12 — an untested combination, low risk but unverified |
| **3** | Reorganize `_lib.sh` in-file: delimited sections and a header index | Finding 7. Explicitly **not** a file split — see below |
| **5** | Documentation and instruction-surface fixes; extend `test_doc_counts.py` | Findings 5, 6, 7; mostly mechanical |
| **4a** | Split `test_transcript_analysis.py` by subcommand group | Gives 4b a per-module verification surface |
| **4b** | Split the source behind a thin single-file entry point | Finding 4; largest and last |

### Two constraints the backlog carries

**Phase 3 must not create a new file.** Splitting `_lib.sh`'s security block into
a sibling was the original proposal. It fails four independent checks: `install.sh:72`
stows entry-by-entry, so a new file gets no symlink until a user re-runs
`install.sh` (`README.md:102` documents this hazard class), leaving pullers with
either lost redaction patterns or a hard deny on every gated commit;
`test_hook_alignment.py:41-52` excludes the *exact* name `_lib.sh`, so a
`_lib_security_patterns.sh` is swept into `ALL_HOOKS` and fails two per-file tests
a helper library cannot honestly satisfy; `test_lib.py` sources only the fixed
`_lib.sh` path, so ~40 tests break without internal re-sourcing; and all seven
consumers would need the stub-then-source-or-deny bootstrap, since a plain failed
source leaves detection regexes unset — the one failure mode a redaction gate must
never have. Four defenses stacked to make one cohesion refactor work is a
wrong-foundation signal, and in-file reorganization addresses the actual complaint
without any of them.

**Verification invariant.** "No test edits" is the wrong bar — it was false for the
original Phase 3 and it wrongly forbids adding tests for new code. Use instead:
no edits to existing test *assertions*, and new tests required for new code. For
Phase 4a specifically, collected test **node IDs** must be set-equal before and
after (`pytest --collect-only -q`, diffed as a set); a bare count cannot detect a
test dropped in one file and duplicated in another.

## Publication constraint

**Phase 2a must merge before this report does** — and 2a means all 13 sites in
finding 1's table, not a subset. A `_lib_repo_root` extraction alone touches two
of them; merging 2b and treating this constraint as satisfied would leave eleven
sites open while the published report claims the gap is closed. Whoever lands 2a
should diff its actual changed lines against that table before releasing this
report. Keeping 2a in its own PR is what makes that diff checkable in one place.

The document above describes gates that do not fire on a specific platform, in a
public repo, before the fix has landed.

The threat model is narrow and worth stating plainly rather than inflating: these
are local developer-workflow guardrails on an engineer's own machine, not a
network-reachable service, and the vulnerable shell is already public and
greppable in this same repo. Writing it down hands no one a capability they did
not already have. The realistic failure it enables is an agent's own git command
slipping past a guardrail it should have hit — a risk identical whether or not
this document exists.

What the sequencing buys is that the published artifact describes a closed gap
rather than a live one, at the cost of one ordering decision. If Phase 2 cannot
land first, finding 1 should be rewritten to reference the tracked fix rather than
describing present-tense behavior.

## What was not verified

- The full `pytest` suite was not run to completion during the audit; collection
  (4,868 tests, 0 errors), `shellcheck`, and `ruff` all completed clean, and a
  partial run reached roughly 780 tests with zero failures. CI's own documented
  budget (`tests.yml:26-35`) is the authority on full-suite timing.
- Branch coverage within each hook's test file was spot-checked, not exhaustively
  measured. "Has a dedicated test file" is not "is fully covered."
- The `timeout`-absent failure path was reasoned from source and from
  `_lib_capped`'s own documentation, not exercised on a coreutils-free macOS host.
- Whether the model actually respects the DO-NOT-TRIGGER handoffs between
  similarly-scoped skill pairs at dispatch time was not measured; only the
  description text was checked for conflicts.
- The 138 plan files were not each read in full; the "sequential, not duplicate"
  reading of filename clusters rests on sampled cross-references.
