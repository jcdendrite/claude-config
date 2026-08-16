# Decompose transcript-analysis.py into a package

## Context

**Goal: split `claude/.claude/scripts/transcript-analysis.py` (11,211 lines,
209 top-level definitions, 26 subcommands) and its 17,300-line test file into
a documented package, preserving the CLI surface byte-for-byte.**

Size is the visible problem, but it is not the only one. Two sibling
production scripts already reach past the file's boundary because there is no
smaller unit to import:

- `token-analyzer.py:24-30` and `analyze-context.py:41-47` each build an
  `importlib.util.spec_from_file_location` and `exec_module` the entire 11k-line
  CLI module — solely to call `_read_session_file`,
  `_dedup_turns_by_request_id`, `_resolve_scan_roots`, and
  `_print_resolved_scope`. Four underscore-prefixed helpers are a de facto
  public API reached through a private door.
- Having paid that cost, both scripts still re-implement helpers they could
  have imported: `_fam` (`token-analyzer.py:33-39`) and `_content_text`
  (`token-analyzer.py:47-52`, byte-for-byte identical to
  `transcript-analysis.py:101-106`).
- `evals/measure_subagent_model_resolution.py:18-20` deliberately does *not*
  import, and hand-mirrors the logic instead, pinning it with line-number
  citations that have **already drifted** (cites `_fam` at `:85`, now `:87`;
  `_index_subagent_dispatches` at `:3251`, now `:3260`;
  `_dispatch_usage_summary` at `:3363`, now `:3372`).

The same absence shows in the tests: the module is executed from scratch at
least four separate times across the suite, each via its own copy-pasted
loader (`test_transcript_analysis.py:22-26`, again at `:286-288`,
`test_context_composition.py:15-19`, plus the loads inside `token-analyzer.py`
and `analyze-context.py` that their own tests trigger).

Intended outcome: an importable unit per concern, the sibling scripts importing
real modules instead of exec'ing a CLI, a per-subcommand test file layout, and
a contributor-facing architecture doc whose accuracy is enforced by a test
rather than by discipline.

## Approach

Introduce `claude/.claude/scripts/transcript_analysis/` as a Python package.
`transcript-analysis.py` stays at its exact current path as a thin executable
shim so every caller keeps working. **Phase 1 moves only leaf modules — code
with no dependency on any `cmd_*` function** — and rewires the sibling scripts;
command modules and `cli.py` come in later PRs. Every module that reads a
reassignable global does so by attribute access (`scope.PROJECTS_DIR`), never
`from .scope import PROJECTS_DIR`, because the latter silently breaks the
suite's monkeypatch fixtures while every other test keeps passing.

### Assumption ledger

**Root problem:** one 11k-line module is the only importable unit, so sibling
scripts exec the whole CLI to reach four helpers, and 964 tests share one
17.3k-line file with no seam smaller than the whole module.

**Given** (fixed, outside this plan's reach):

- G1. pytest's default `prepend` import mode inserts each test module's
  containing directory into `sys.path` and imports the module **by basename**
  unless `__init__.py` files make it a package. That is pytest's own behavior,
  not something the suite can alter from within, and it is what makes test-file
  basenames a global namespace. `[verified: https://docs.pytest.org/en/stable/explanation/pythonpath.html — "The directory path containing each module will be inserted into the _beginning_ of sys.path if not already there, and then imported with the importlib.import_module function."]`

Two conditions that might read as givens are **not** — this repo owns every
artifact pinning them, so they are deliberate declines and live in
**Out of scope** with their reasons: the CLI surface, and the entrypoint
filename.

**Mechanisms:**

- **M1 — Package directory rather than flat `_`-prefixed sibling modules.**
  `anchors: root`. This is the heavier primitive; two lighter ones were
  weighed and rejected:
  - *Flat `_transcript_*.py` siblings*, matching the repo's only precedent
    (`_config_dir.py`, imported by 4 scripts). Rejected: it grows
    `claude/.claude/scripts/` from 15 to ~30 flat files in a directory stow
    exposes as `~/.claude/scripts/`, erasing the distinction between user
    entrypoints and internals, and it keeps every test file's
    `importlib`-by-path loader.
  - *Keep one file, extract only the four helpers the siblings need.* Rejected:
    it fixes the coupling defect but leaves both large files untouched, which
    is the request.
  - Chosen by the engineer this session. `[engineer-verified]`
- **M2 — Every module that reads a reassignable global reads it by attribute
  access on its owning module (`scope.PROJECTS_DIR`), and `main()` assigns
  `scope.PROJECTS_DIR = ...` rather than using `global`.** `anchors: row1`.
  Scope is **every module, not only command modules** — `redaction.py`'s
  `_build_redact_map` default parameter (`:4532`, `:4537`) is a cross-module
  reader with a live path through `cmd_user_input` (`:789`), which is *not* in
  `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR` (`:5266-5269`) and so is reachable via a
  top-level `--config-dir`. No lighter-primitive enumeration applies: plain
  attribute lookup *is* the lightest option — no accessor function, no injected
  context object, no removal of the global.
- **M3 — Add `claude/.claude/scripts` to pytest's `pythonpath` ini list.**
  `anchors: given G1`. Lighter alternative considered: leave the per-file
  `sys.path.insert` in place. Rejected because the list already exists in
  `pyproject.toml` for exactly this purpose (it is how `from helpers import ...`
  resolves today), so this reuses an established mechanism rather than adding
  one.
- **M4 — A test asserts the architecture doc lists exactly the modules on
  disk.** `anchors: root`. Lighter alternative considered: a prose note asking
  contributors to update the doc. Rejected — the already-drifted line-number
  citations in `evals/measure_subagent_model_resolution.py:18-20` are direct
  evidence that unenforced pointers into this file rot.
- **M5 — Staged PRs, leaf modules first.** `anchors: root`. Chosen by the
  engineer this session. `[engineer-verified]`

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | `from mod import NAME` binds a reference at import time, so rebinding `mod.NAME` later does not reach the importer; attribute access at call time does. This is why M2 is mandatory, not cosmetic. | `[verified: https://docs.python.org/3/reference/simple_stmts.html § "The import statement" — "a reference to that value is stored in the current namespace"; corroborated by https://docs.pytest.org/en/stable/how-to/monkeypatch.html — "if your module does `from os import getcwd`, patch `mymodule.getcwd` rather than `os.getcwd`"]` |
| 2 | The suite patches module globals heavily and would break silently under a naive split: 42 `setattr(_mod, "PROJECTS_DIR", ...)`, 32 on `config_dir`, 193 `monkeypatch.setattr` total; most flow through the `fake_projects` fixture, used ~1,129 times. | `[verified: grep counts over test_transcript_analysis.py]` |
| 3 | `PROJECTS_DIR` (`:39`) and `_usage_drift_warned` (`:5091`) are the **only** two mutable module globals reassigned via `global`. Every other module-level binding is a static lookup table, never mutated at runtime — no subscript assignment, no `lru_cache`/`functools.cache` anywhere in the file. | `[verified: staff-backend-engineer audit, corroborated by `grep -n "^\s*global " transcript-analysis.py` returning exactly `:5105` and `:11205`]` |
| 4 | Cross-module readers of `PROJECTS_DIR` are few and identified: `cmd_user_input:810` and `redaction.py`'s `:4532`/`:4537`. The reads at `:2525`, `:2617-2618`, `:2688` sit inside `_resolve_scan_roots`/`_resolve_project_scope`, which live in `scope.py` beside the global — same-module, unaffected. | `[verified: `grep -n "PROJECTS_DIR" transcript-analysis.py` this session]` |
| 5 | The `roots if roots is not None else (PROJECTS_DIR,)` fallback (10 sites) is **dead in normal CLI dispatch** — every `cmd_*` threads an explicit `roots=_resolve_scan_roots(args)`. The fallback's live consumers are tests. This shrinks the blast radius of M2 but does not remove the need for it. | `[verified: staff-backend-engineer traced cmd_cost:5514 → _cost_report:5557; docstring at :2654-2661 states the contract]` |
| 6 | **The hook-test sandbox needs no change.** CPython resolves the symlink when computing `sys.path[0]` for a directly-invoked script, so a shim symlinked into a sandbox still finds its sibling package in the real directory. This is why `from _config_dir import ...` already works there today despite `_config_dir.py` never being symlinked in. | `[verified: ran a symlinked entrypoint importing a sibling package on Python 3.14.6 this session — `sys.path[0]` resolved to the real directory and the import succeeded. CI runs 3.12 (.github/workflows/tests.yml:138), so the existing hook test passing on CI is the confirmation for that version.]` |
| 7 | Two sandbox helpers exist, not one — `_prepare_home:70-77` and `_prepare_config_dir:124-133`, structurally identical. Under assumption 6 neither needs fixing, but any future change to one must be applied to both. | `[verified: read both helpers this session]` |
| 8 | Adding files/subdirectories under `claude/.claude/scripts/` needs no stow change — `~/.claude/scripts` is already a single folded symlink into the checkout, on fresh installs too (`install.sh:29` creates only `$HOME/.claude`, never `scripts/`), and `.gitignore:1` covers `__pycache__/` at any depth. Stale `.pyc` files are not a hazard: CPython invalidates by source mtime+size, and an orphaned `.pyc` for a deleted source is never loaded. | `[verified: `ls -ld ~/.claude/scripts` → symlink into the checkout; `.gitignore:1`; install.sh:29,253]` |
| 9 | No `permissions.allow`/`deny` rule anywhere names this script. The only settings.json touchpoint is a `skillOverrides` entry for the *skill* of the same name. | `[verified: claude/.claude/settings.json, .claude/settings.json]` |
| 10 | No test enumerates, counts, or shebang-checks files under `scripts/`; `scripts/list-shell-files.sh` is shell-only and cannot see a new `.py`. New files trip no structural assertion. | `[verified: suite-wide grep for iterdir/glob/os.walk/st_mode]` |
| 11 | No `sys.modules` collision is possible: every loader (`test_transcript_analysis.py:23,286`, `test_context_composition.py:16`, `test_token_analyzer.py:13`, `test_analyze_context.py:12`, `token-analyzer.py:24-30`, `analyze-context.py:41-47`) uses `module_from_spec` + `exec_module` and **never** assigns `sys.modules[name]`, so the string `"transcript_analysis"` they pass never shadows the real package. | `[verified: staff-platform-engineer audit of all six loaders]` |
| 12 | Deduplicating `_fam` is behavior-preserving: `transcript-analysis.py:87` lowercases internally, `token-analyzer.py:33` does not, but its only call site (`:97`) already passes `.lower()`, so the internally-lowercasing version is a strict superset. `_content_text` is byte-for-byte identical between the two files. | `[verified: both call sites read this session]` |
| 13 | Package import adds negligible startup cost against the hook's budget: the current 11k-line file cold-starts in 0.36–0.39s with bytecode cache disabled, against the `_lib_capped_for 10` wrapper at `nudge-error-mode-analysis.sh:152` — over 9.5s of headroom. Splitting into ~6 small modules adds a few extra `stat`/compile calls, single-digit milliseconds. | `[verified: `/usr/bin/time -p python3 -B transcript-analysis.py --help`, 3 runs this session — 0.36s, 0.39s, 0.39s; cap line read directly]` |
| 14 | Splitting will not measurably speed the suite. Baseline this session: 1,141 tests pass in 48.8s under `-n auto`, with **no single test above 0.01s** — wall time is collection, per-worker module exec, and xdist overhead. The justification is reviewability and coupling, not speed. | `[verified: `pytest test_transcript_analysis.py test_context_composition.py -q --durations=0` run this session]` |
| 15 | Per-subcommand test files split cleanly: 27 of 29 groups are single-subcommand. The resistant part is `_UNCONDITIONAL_HEADER_CASES` (`:15799-15831`), a table over 23 of 30 `cmd_*` functions drawing args-factories from 16 scattered line ranges across ~15 groups. | `[verified: staff-sdet audit; table read at :15799-15831]` |
| 16 | Four helpers need promoting to `conftest.py`, not three: `fake_projects`, `fake_config_dir_factory`, `_table_cols` (159 uses), **and `cost_ledger_file`** (`:9831-9843`) — the last is injected into both cross-subcommand header tests (`:15855`, `:16037`) so `cmd_cost_ledger`'s real ledger path is never hit for any of the 23 parametrized rows. | `[verified: staff-sdet traced both injection sites]` |
| 17 | `test_pytest_collection_config.py` asserts `norecursedirs`, `addopts`, and timing-marker parity — it asserts **nothing** about `pythonpath` today. M3 therefore adds a new assertion rather than updating an existing one. | `[verified: staff-platform-engineer read the full file]` |
| 18 | There is **no** in-repo guidance on Python module decomposition, structure READMEs, or test-file splitting — in any skill, CLAUDE.md, rule, or doc. Counter-precedent exists: `claude/.claude/hooks/_lib.sh` is 1,478 lines and deliberately undecomposed, and `docs/design-decisions.md` §4 prefers duplication over shared partials. That §4 rationale is scoped to *skill prose* and its context-budget cost, which does not transfer to Python imports. | `[verified: repo-wide search of skills/, CLAUDE.md files, .claude/rules/, docs/]` |
| 19 | Redaction fails closed: `_redact_proj_label:4440-4455` and `_redact_session_id:4588-4590` both `.get(..., <miss-token>)`, so a stale or empty map emits `private-project-unmapped` / `session-unmapped`, never a raw name. `_redaction_ordinals:4480-4494` and `_corpus_fingerprint:4562-4570` are pure functions of their arguments with `sorted()` neutralizing set-iteration and `PYTHONHASHSEED` nondeterminism — so pseudonyms stay stable across the split. | `[verified: ciso-reviewer read all four functions]` |
| 20 | The multi-root `--no-redact` refusal is a duplicated inline guard across 8 subcommands (`:5629`, `:6081`, `:6492`, `:7019`, `:7488`, `:7912`, `:10298`, shared string at `:5339`), **not** a shared function. Phase 1 touches no `cmd_*` body, so it is unaffected now; later phases must keep each guard co-located with its command. | `[verified: ciso-reviewer enumerated all 8 sites]` |

## Phases

**Phase 1 — leaf modules only.** Create the package with `corpus.py`,
`scope.py`, `redaction.py`, `pricing.py`, `render.py`. These have **no
dependency on any `cmd_*` function**, so no circular import is possible.
`transcript-analysis.py` keeps `build_parser()`, `main()`, and every `cmd_*`,
and imports the new modules. Rewire the two sibling scripts, delete their two
duplicated helpers, add the pythonpath entry, and write the architecture doc
plus its drift test.

**Why `cli.py` is not in Phase 1:** `build_parser()` composes subparsers
against every `cmd_*`. While the commands still live in the shim, a `cli.py`
would have to import back into the shim — a circular import, or an unstated
composition pattern. Phase 1 stays strictly leafward; `cli.py` is created in
the final phase, once the last command group has moved.

**Phases 2..N — command groups**, largest first per assumption 15: cost family
(2,362 test lines), reviewer-yield (1,413), review-trace (1,380),
audit-routing (1,290), cost-ledger (1,128), then the remainder. Each PR moves
one group's production code *and* its test slice together.

**Final phase — `cli.py`.** `build_parser()` and `main()` move; the shim
reduces to a package import and a `__main__` guard.
`_UNCONDITIONAL_HEADER_CASES` and its two test classes **stay in the legacy
test file until every one of the ~15 groups they reference has moved**, then
relocate atomically in this phase. A contributor mid-series must not have to
improvise mixed imports from both the shrinking monolith and the growing
package.

## Critical files

### Create (Phase 1)

- `transcript_analysis/__init__.py`
- `transcript_analysis/corpus.py` — JSONL read/parse, `iter_sessions`, session
  partitioning, `_parse_ts` (`:339-492`)
- `transcript_analysis/scope.py` — `PROJECTS_DIR`, scan-root and project-scope
  resolution, resolved-scope header (`:2330-2768`, `:5266-5437`)
- `transcript_analysis/redaction.py` — redact map, ordinals, corpus
  fingerprint, session labels (`:4405-4627`)
- `transcript_analysis/pricing.py` — rate tables, `_price_turn`, token counts,
  context windows, `_dedup_turns_by_request_id` (`:4848-5256`)
- `transcript_analysis/render.py` — `_fmt_usd`, `_pct_of`, table printing,
  `_sanitize_table_cell`, markdown sample formatting (`:98-338`, `:1651-1658`)
- `docs/transcript-analysis-architecture.md` — module-by-module
  responsibilities; the contributor-facing counterpart to
  `docs/transcript-analysis.md`'s CLI reference
- A test asserting the doc's module list matches the package directory (M4)

### Modify

- `transcript-analysis.py` — imports the new modules; **path and filename
  unchanged**. In the final phase it reduces to a plain top-level package
  import plus an `if __name__ == "__main__":` guard — explicitly **not** the
  sibling scripts' `spec_from_file_location`/`sys.path.insert` pattern.
- `token-analyzer.py` — replace the loader (`:24-30`) with real imports; delete
  the duplicated `_fam` (`:33-39`) **and** `_content_text` (`:47-52`)
  (assumption 12)
- `analyze-context.py` — same loader replacement (`:41-47`)
- `tests/test_analyze_context.py`, `tests/test_token_analyzer.py` — retarget
  `_mod._transcript_analysis.PROJECTS_DIR` patches. **Re-grep rather than trust
  a line list**: the cited ranges are known non-exhaustive (e.g. a sixth
  patch site exists at `test_token_analyzer.py:716`, and
  `analyze-context.py` has call sites at `:292,301` beyond those first found).
- `tests/conftest.py` — receives `fake_projects`, `fake_config_dir_factory`,
  `_table_cols`, `cost_ledger_file` (assumption 16). `test_context_composition.py:23,36`
  currently carries its own duplicate copies of the first two — fold them into
  the promotion rather than leaving the drift in place.
- `pyproject.toml` — add `claude/.claude/scripts` to `pythonpath` (M3)
- `claude/.claude/tests/test_pytest_collection_config.py` — add a **new**
  `pythonpath` assertion (assumption 17)
- `test_transcript_analysis.py:23` — add a one-line comment that the
  `"transcript_analysis"` string is safe only because the loader never assigns
  `sys.modules[name]`; "fixing" it to the standard importlib recipe would
  shadow the real package for the rest of the session (assumption 11)
- `docs/scripts.md`, `README.md` — point at the new architecture doc
- `evals/measure_subagent_model_resolution.py:18-20,44-46` — convert the
  already-stale line-number citations to symbolic references

### Reuse rather than reimplement

- `_config_dir.py`'s `config_dir()` / `declared_transcript_roots()` — already
  the shared root-resolution primitive; `scope.py` builds on it.
- `conftest.py`'s existing record builders (`_asst`, `_user_msg`, `_bash_use`,
  `_tool_result`, `_agent_use`, `_write_jsonl`) — already shared; split test
  files import them unchanged.
- `argparse`'s `set_defaults(func=...)` dispatch — already the file's shape and
  the documented pattern; keep it rather than building a registry.
  `[verified: https://docs.python.org/3/library/argparse.html § "Sub-commands" — "One particularly effective way of handling subcommands is to combine the use of the `add_subparsers()` method with calls to `set_defaults()`". The docs do not address composing `add_parser` calls from separate modules; that remains `[unverified]` and is why the final phase, not Phase 1, carries it.]`

### Naming

Rename the four helpers the siblings import to public names —
`read_session_file`, `dedup_turns_by_request_id`, `resolve_scan_roots`,
`print_resolved_scope`. The plan's stated root problem is a public API reached
through a private door; leaving the underscore prefix after promoting them onto
a package's public surface preserves exactly that signal mismatch. All four
call sites are already being rewritten, so this adds no new touch points.

New test files take a `test_transcript_*` prefix so basenames stay globally
unique under `prepend` import mode (G1). No collisions exist today —
`git ls-files | grep -E '/test_[^/]*\.py$' | xargs -n1 basename | sort | uniq -d`
returns empty — so the prefix preserves an invariant rather than fixing a break.

## Verification

1. **Baseline parity.** The pass count in assumption 14 must hold at every
   phase boundary, rising only as files split — never falling. Full suite:
   `../../../.venv/bin/pytest claude/.claude/` from the worktree.
2. **CLI golden-output diff — via real subprocess.** Capture `--help` for the
   top-level parser and all 26 subparsers, plus each subcommand's stdout
   against a fixed synthetic corpus, by invoking
   `subprocess.run(["python3", "transcript-analysis.py", ...])` — **not**
   in-process `build_parser()`. Only the subprocess form exercises the shim's
   bootstrap, which is the single path no existing test covers (all 964 tests
   call `_mod.cmd_*` with hand-built Namespaces; the 4 `main()` tests at
   `:319,337,365,403` still run in-process via `sys.argv` monkeypatch). The
   corpus must include a **multi-root** and a **zero-match** scenario — the two
   states most likely to regress silently under a scope refactor. `pr-link`
   needs a `gh` stub on `PATH` for this harness, since the in-process fake at
   `:15786` has no subprocess seam; if it is excluded instead, say so and drop
   the claim to 25 subcommands.
3. **Late-binding regression test — behavioral, not attribute equality.**
   Reassign `scope.PROJECTS_DIR` to a tmp dir holding one seeded session with a
   distinctive marker, then call a command function **from a different module**
   with no root override and assert the marker appears in `capsys` output. An
   attribute-equality check would not work: under the correct M2 design the
   command module never binds the name at all, so there is nothing to compare.
   Cover a **not-yet-migrated** function too, so a test patching the wrong
   module's global fails loudly instead of falling through to the real
   `config_dir()`.
4. **Redaction invariant test.** Call `_build_redact_map()` with no `roots`
   argument after reassigning `scope.PROJECTS_DIR`, and assert the map reflects
   the reassigned root — pinning M2's extension to `redaction.py` (assumption
   4). Without it, `user-input --redact --config-dir` can build a map from the
   stale default root and apply the wrong account's pseudonym.
5. **Sibling-script coverage.** `test_token_analyzer.py` and
   `test_analyze_context.py` must pass without loosening an assertion — direct
   proof the loader replacement preserved behavior.
6. **Hook sandbox.** `test_nudge_error_mode_analysis.py` must pass on CI
   unchanged. Under assumption 6 it needs no edit; its passing on CI's Python
   3.12 *is* the verification that the symlink property holds there.
7. **Revert rehearsal.** At each phase boundary, `git revert` the phase's merge
   commit on a scratch branch and run the full suite. Baseline parity alone
   does not prove a staged PR is independently revertible — a conftest fixture
   promotion in PR N can leave PR N−1's revert uncollectable.
8. **Lint.** `../../../.venv/bin/ruff check claude/.claude/` — the repo sets
   `select = ["I"]` with no `known-first-party` config, so import
   classification for the new package is unproven until this runs.
9. **Doc-drift test** (M4) fails when a module is added without documenting it.

## Out of scope

- **Removing the `PROJECTS_DIR` global entirely** by threading roots
  explicitly. This is the correct end state: the global is load-bearing in
  three independent directions — a stale redact map can apply the wrong
  account's pseudonym (assumption 4), a test patch aimed at the wrong module
  silently no-ops rather than failing (assumption 2), and `main()`'s
  reassignment is the only reason M2 exists at all. Assumption 5 shows the
  surface is smaller than it looks, since the fallback is already dead in CLI
  dispatch. Excluded here because `main()`'s reassignment plus 42 test patch
  sites make it a behavior-change refactor, and it must not ride along with a
  diff whose value depends on being provably behavior-preserving.
  **Recommended as the immediate follow-on once the decomposition lands.**
- **A parametrized multi-root `--no-redact` refusal test** across all 8
  subcommands carrying the duplicated guard (assumption 20). Not a regression
  this plan introduces — the guard is already copy-pasted — but the later
  command-group phases should land it before splitting those `cmd_*` bodies,
  so a dropped guard fails CI rather than silently shipping a raw multi-account
  report.
- **Extracting `cost_ledger`'s sentinel and git-tracked checks.** When that
  command moves, the opt-in sentinel (`:8659-8671`) and the git-tracked refusal
  (`_ledger_path_is_git_tracked:8184-8238`, invoked `:8679`) must stay in the
  same function as the write, not hoist into a shared layer a future command
  could bypass. Preserve the fail-closed docstring at `:8188-8192` verbatim.
- **Any CLI surface change** — renaming subcommands, changing flags, or
  altering output shape. This repo owns every consumer, so it *could* change
  them; it declines because the value of this diff rests on being provably
  behavior-preserving, and which subcommands should exist is a product question
  this refactor has no standing to answer.
  `[verified: claude/.claude/skills/transcript-analysis/SKILL.md, claude/.claude/agents/skill-fidelity-reviewer.md:21,26]`
- **Moving or renaming the `transcript-analysis.py` entrypoint.** Also inside
  reach — the pinning sites are a hook and a skills test in this repo — but
  keeping the path makes every caller a no-op, which is the point of the shim.
  `[verified: claude/.claude/hooks/nudge-error-mode-analysis.sh:152-153, claude/.claude/skills/tests/test_skills.py:2101-2141]`
- **Adding `__init__.py` to `claude/.claude/scripts/tests/`.** In reach, but it
  would change every test module's name under G1 and break the existing
  `from conftest import ...` / `from helpers import ...` imports suite-wide.
- **Decomposing `claude/.claude/hooks/_lib.sh`** (1,478 lines) or any other
  large file. Scope is set by the request.
- **Establishing a repo-wide file-size rule.** Assumption 18 found a genuine
  guidance vacuum; this plan argues its own case and does not legislate for
  other files.
- **Closing the non-atomic-`git pull` window.** `~/.claude/scripts` is a live
  symlink into the checkout and `git pull` is not atomic across files, so a
  hook firing mid-pull can observe the new shim before the package directory
  lands, producing a transient `ModuleNotFoundError`. This narrows rather than
  creates an existing exposure of the folded-symlink delivery model — no lock
  exists for "hook runs mid-pull" today either. No fix is attempted here.
