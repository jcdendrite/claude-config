# Plan: Make claude-config's hooks/scripts honor `CLAUDE_CONFIG_DIR`

## Context

**Goal:** every hook and script under `claude/.claude/` that reads or writes
Claude Code account state must resolve the active config directory the same
way — `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` — instead of each hardcoding
`$HOME/.claude` (shell) or `Path.home() / ".claude"` (Python).

**Why now.** A session running under a secondary `CLAUDE_CONFIG_DIR` account
profile searched `~/.claude/projects` for a crashed session to resume, found
nothing, and nearly told the user their multi-day work under that profile
had no recoverable session — the work was real, just under a config
directory the search never looked at. That specific ad hoc search isn't in this repo, but the
investigation for this plan found the same bug shape, already shipped, in
~27 files: `claude/.claude/scripts/transcript-analysis.py` (and its two
siblings, `token-analyzer.py` / `analyze-context.py`) hardcode
`Path.home() / ".claude"` for the exact directory that search needed, and
`claude/.claude/hooks/nudge-error-mode-analysis.sh` invokes
`transcript-analysis.py` via a hardcoded `$HOME/.claude/scripts/...` path —
so the built-in friction-nudge hook has the identical wrong-tree bug for any
`CLAUDE_CONFIG_DIR` account today.

The investigation also found something more serious than a misdirected read:
the review-gate markers (`/code-review`, `/plan-review`, `/ready-for-review`)
are **cross-account bypassable**. `_marker_lib_repo_hash` (`_lib.sh:192-195`)
keys markers by `sha256(repo path)` only — no account/profile component —
and the read side (`_lib_marker_value_present`, `_lib.sh:222-263`) globs
across every `$SESSION_ID` for that hash, matching on content alone
(`require-code-review.sh:20-26` states this is deliberate, to let a resumed
*same-account* session reuse a review). Since marker directories live under
literal `$HOME/.claude/*-markers/` regardless of `CLAUDE_CONFIG_DIR`, two
different accounts sharing one `$HOME` (the normal multi-account setup) and
working in the same repo path can have one account's `/code-review` run
satisfy the other account's gate. Routing marker storage through the same
resolver this plan introduces closes this **conditionally**: it isolates two
accounts that are actually differentiated by distinct `CLAUDE_CONFIG_DIR`
values (the realistic case for a personal + work profile split). Two
accounts that both leave `CLAUDE_CONFIG_DIR` unset and share the default
`$HOME/.claude` remain exactly as bypassable as today — this plan does not
and cannot add account-identity partitioning on top of a shared default
directory. No bespoke fix is needed for the differentiated case; the
undifferentiated case is out of scope (see Out of scope).

**Intended outcome:** one canonical resolver per language
(`_lib_config_dir` in `_lib.sh` for shell, `config_dir()` in a new
`claude/.claude/scripts/_config_dir.py` for Python), every one of the ~27
affected files migrated to call it, and CLAUDE_CONFIG_DIR-set test cases
added to each file's existing test (every affected file already has one —
confirmed below, no new test scaffolding needed).

Scope decision (confirmed with the user): one comprehensive PR, not phased —
this is a single mechanical pattern applied everywhere, and CLAUDE.md's
audit-siblings rule ("apply to every affected site" once the fix is
identical) outweighs splitting an artificial "critical vs. not" line through
files that share one root cause.

## Approach

**Root problem:** every hook/script in this repo that reads or writes
Claude Code account state assumes that state lives at `$HOME/.claude`,
ignoring the first-party `CLAUDE_CONFIG_DIR` env var Claude Code itself
documents as relocating "the whole `~/.claude` directory, not just `$HOME`"
(`register-marketplace.sh:7`, `docs/scripts.md:79`, `README.md:184`).

**Chosen design — two resolvers, one per language, matching the existing
correct precedent:**

- Shell: `register-marketplace.sh:29` already does
  `SETTINGS_FILE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"`.
  Add `_lib_config_dir` to `claude/.claude/hooks/_lib.sh`, placed
  immediately after `_lib_realpath_m` (a fellow dependency-free path
  primitive, ahead of the higher-level gate functions that will consume it):

  ```bash
  _lib_config_dir() {
    if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
      case "$CLAUDE_CONFIG_DIR" in
        /*) ;;
        *) return 1 ;;  # relative values resolve differently per invocation
                        # cwd — the exact read/write path-mismatch bug this
                        # plan fixes, just triggered a different way.
      esac
      printf '%s\n' "${CLAUDE_CONFIG_DIR%/}"
      return 0
    fi
    local home_norm="${HOME%/}"
    [ -n "$home_norm" ] || return 1
    printf '%s\n' "$home_norm/.claude"
  }
  ```

  This mirrors the empty-`$HOME` guard already used by
  `_lib_worktree_enforcement_active` (`_lib.sh:576-580`) and
  `_lib_autonomous_shipping_active` (`_lib.sh:607-613`) — never silently
  resolve to a root-anchored path — expressed as "fail (empty stdout, exit
  1)" rather than a boolean, since callers need a value, not a predicate.
  `CLAUDE_CONFIG_DIR` must be absolute; a relative value is treated as
  unresolvable (return 1) rather than resolved against whatever the calling
  hook's cwd happens to be at invocation time — an unstated cwd dependency
  would reintroduce path-mismatch bugs of the same shape this plan closes.

  **Call-site contract (load-bearing — every caller must follow this):**
  bare interpolation, `"$(_lib_config_dir)/whatever"`, is unsafe — under
  `set -e`, a failing *nested* command substitution does not abort the
  script, so a resolver failure silently collapses to `"/whatever"`
  (root-anchored) rather than being caught. Every call site must capture
  and check the exit status first:
  ```bash
  config_dir=$(_lib_config_dir) || { <fail-open-or-deny per this caller's posture>; }
  ```
  and each caller's chosen posture (fail-open / fail-closed) is stated
  explicitly at that call site, not left to fall out of glob/mkdir
  behavior on an accidentally-root-anchored path. `require-memory-skill.sh`
  fails open (ledger row 6); `marker.sh` (write side) and
  `require-code-review.sh` / `require-plan-review.sh` /
  `require-ready-for-review.sh` (read side) fail **closed** — an
  unresolvable config dir must not silently satisfy a review gate, so a
  `_lib_config_dir` failure in any of these four is a hard `emit_deny` /
  write-abort, not a fall-through.

- Python: no shared Python module exists yet in this repo (only the shell
  side has `_lib.sh`); four independent `Path.home() / ".claude"` call sites
  do though (`transcript-analysis.py:23` and a second, unrelated one at
  `transcript-analysis.py:2665`; `token-analyzer.py:10`;
  `analyze-context.py:26`). That's past CLAUDE.md's "small duplicated
  value" exception — this is logic (env-var-present-else-fallback), not a
  value, and it already needs to run at 4 sites. New file
  `claude/.claude/scripts/_config_dir.py`:

  ```python
  """Shared Claude Code config-directory resolution for scripts/ tooling."""
  import os
  from pathlib import Path

  def config_dir() -> Path:
      """Return the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set (must be absolute), else ~/.claude."""
      override = os.environ.get("CLAUDE_CONFIG_DIR")
      if override:
          path = Path(override)
          if not path.is_absolute():
              raise ValueError(f"CLAUDE_CONFIG_DIR must be an absolute path, got: {override!r}")
          return path
      return Path.home() / ".claude"
  ```

  Mirrors the shell resolver's absolute-path requirement — a relative
  `CLAUDE_CONFIG_DIR` resolves differently depending on invocation cwd,
  reintroducing the exact bug class this plan closes.

  Each script keeps `PROJECTS_DIR = config_dir() / "projects"` etc. as a
  **module-level attribute computed at import time** — required because the
  existing tests monkeypatch it directly
  (`monkeypatch.setattr(_mod, "PROJECTS_DIR", ...)` in
  `tests/test_transcript_analysis.py:135-141` and
  `tests/test_token_analyzer.py:70-78`); a function called fresh per call
  site would silently break that seam.

  **Import mechanics (verified, not assumed):** `python3 script.py`
  (the real runtime path — `nudge-error-mode-analysis.sh` invokes
  `transcript-analysis.py` this way) puts the script's own directory on
  `sys.path[0]` automatically, so `from _config_dir import config_dir`
  resolves with no extra plumbing there. But the **test** loading path is
  different: `test_transcript_analysis.py`/`test_token_analyzer.py` load
  their target module via `importlib.util.spec_from_file_location(...)` +
  `exec_module(...)`, which does **not** add the script's directory to
  `sys.path` — `from _config_dir import config_dir` would raise
  `ModuleNotFoundError` at collection time without a fix. Both test files
  need `sys.path.insert(0, str(_SCRIPT.parent))` added before their
  `exec_module` call (see Critical Files).

**Alternatives considered:**
- *Python: duplicate the one-liner in each of the 3 scripts instead of a
  shared module.* Rejected — 4 call sites of the same branching logic (not
  a plain value) is past the DRY exception's bar, and per-script drift risk
  is real (one script handles an edge case the others don't, they silently
  disagree). See `verify-sources`/DRY rationale above.
- *Fold the Python resolver into `_lib.sh` somehow (e.g. shell wrapper the
  scripts call).* Rejected — these scripts already run standalone via
  `python3 script.py`; adding a shell shim only to reach a bash function
  is a heavier mechanism than a 6-line Python module in the same directory.
- *Fix only the `_lib.sh` functions and leave inline hardcodes across ~24
  hook files alone, reasoning callers "inherit" the fix.* Rejected — the
  investigation confirmed most callers **also** hardcode `$HOME/.claude`
  directly, independent of any `_lib.sh` call (e.g.
  `advance-past-commit-stall.sh` hardcodes it 5 more times beyond its one
  `_lib_autonomous_shipping_active` call; `marker.sh` — the write side of
  every review-gate marker — never calls `_lib.sh` at all for its paths).
  Fixing only `_lib.sh` would desync read/write pairs under
  `CLAUDE_CONFIG_DIR` rather than fix anything.

### Assumption ledger

**Root:** hooks/scripts hardcode `$HOME/.claude` instead of resolving
`CLAUDE_CONFIG_DIR`, causing wrong-directory reads and (for review-gate
markers specifically) a cross-account gate bypass.

| # | Assumption | Tag | anchors |
|---|---|---|---|
| 1 | `CLAUDE_CONFIG_DIR`, when set, replaces the whole `~/.claude` tree (hooks/scripts/state), not just a state subdirectory | [verified: `docs/scripts.md:79`, `README.md:184`, `register-marketplace.sh:7`] | root |
| 2 | Lighter primitive #1 — "read `CLAUDE_CONFIG_DIR` inline at each site, no shared resolver" — rejected: ~50 shell call sites × one inline `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` each is exactly the duplicated-knowledge case CLAUDE.md's DRY rule flags, and the existing `_lib.sh` sourcing convention (already used by nearly every hook) is the established lighter-than-a-new-abstraction primitive for shared shell logic | [verified: `_lib.sh` sourced by every hook per Step 3 grep] | root |
| 3 | Lighter primitive #2 — "make `CLAUDE_CONFIG_DIR`-awareness a settings.json/hook-config toggle instead of code" — rejected: there's no settings.json field for path resolution today (grep of `docs/hooks.md` and `settings.json` schema shows none), and inventing one is a new configuration surface for what's a pure function of one already-standard env var | [verified: grep — no existing settings.json field for this] | root |
| 4 | `relocate-claude-config.sh` must stay `$HOME`-anchored, not be migrated | [verified: file's own header — "Deliberately does NOT source `_lib.sh` or otherwise depend on `~/.claude/` being intact"; every `stow -t` target in `install.sh`/this script is literal `$HOME`, never `CLAUDE_CONFIG_DIR`] | root |
| 5 | `require-worktree-for-file-writes.sh`'s `$HOME/.claude` exemption should match **both** the literal path (stow-fold detection, mechanism-bound to real `$HOME`) and the resolved config dir (harness-infrastructure category exemption) — union, not swap | [verified: file's own two-part rationale, `require-worktree-for-file-writes.sh:10-24`, quoted in Critical Files] | root |
| 6 | `require-memory-skill.sh` fails **open** (exit 0, no gate opinion) when `_lib_config_dir` can't resolve (i.e. empty `$HOME` and no `CLAUDE_CONFIG_DIR` — an abnormal environment), rather than fail-closed | [unverified — my judgment call, see Critical Files; mirrors the file's own existing fail-open precedent for an absent `SESSION_ID` at line 106-108, and a fail-closed reading here would block *every* Write/Edit/MultiEdit call, not just memory ones, whenever `$HOME` is merely unset] | root |
| 7 | Marker directories (`code-review-markers/`, `plan-review-markers/`, `ready-for-review-markers/`, `*-active.d/`) move under `$(_lib_config_dir)` instead of `$HOME/.claude` — this alone closes the cross-account bypass, no separate account-scoping mechanism needed | [verified: `_marker_lib_repo_hash`/`_lib_marker_value_present` logic read in full, `_lib.sh:192-195,222-263`; two accounts with different `CLAUDE_CONFIG_DIR` values get different resolved directories automatically] | root |
| 8 | `marker.sh:8`'s `. "$HOME/.claude/hooks/_lib.sh"` is a *different* bug (should be `dirname`-relative like every other hook), not a `CLAUDE_CONFIG_DIR` substitution — `marker.sh` runs from wherever it's actually installed, and sourcing its sibling `_lib.sh` should never depend on `$HOME` at all. **Not** `require-memory-skill.sh`'s literal `. "$(dirname "$0")/_lib.sh"` — that hook lives in `hooks/`, sibling to `_lib.sh`; `marker.sh` lives in `scripts/`, one directory over. Correct form: `. "$(dirname "$0")/../hooks/_lib.sh"` | [verified: `marker.sh`'s own `# shellcheck source=../hooks/_lib.sh` directive confirms the relative path; `require-memory-skill.sh:52`'s same-directory form does not apply here — copying it verbatim breaks `marker.sh` on every invocation, a total-outage regression for every review gate] | root |
| 9 | `nudge-error-mode-analysis.sh`'s hardcoded invocation path to `transcript-analysis.py` (`$HOME/.claude/scripts/transcript-analysis.py`) is a structural sibling of the Python `PROJECTS_DIR` bug — same root cause, shell caller instead of Python callee | [verified: `nudge-error-mode-analysis.sh` invocation line read in full] | anchors: root |
| 10 | Guard-config files (`credential-file-guard.md`, `pii-patterns.md`, `private-projects.md`, etc.) resolve as a union — `$(_lib_config_dir)` first, falling back to legacy `$HOME/.claude` — not a swap, else a `CLAUDE_CONFIG_DIR` user's already-armed guard silently goes dark post-fix | [plan-review finding: ciso-reviewer, round 1 — enforcement-invariant regression, fix-or-ask per this skill's own disposition rule; resolved by fix, not by asking, since the union pattern is already established at ledger row 5] | root |
| 11 | Every `_lib_config_dir` call site captures and checks its exit status explicitly rather than bare-interpolating `$(_lib_config_dir)`, because a failing nested command substitution does not abort under `set -e` and would otherwise silently collapse to a root-anchored path; `marker.sh` (write) and the three `require-*-review.sh` gates (read) fail **closed** on an unresolvable config dir, `require-memory-skill.sh` fails **open** (ledger row 6) | [plan-review finding: ciso-reviewer + staff-platform-engineer + claude-hook-review, round 1, independently convergent] | root |

## Critical files

### New resolvers

- **`claude/.claude/hooks/_lib.sh`** — add `_lib_config_dir` (placement: after `_lib_realpath_m`, before `_lib_emit_deny`). Also migrate its 3 existing functions that hardcode `$HOME/.claude` internally to call it:
  - `_lib_worktree_enforcement_active` (line ~582: `"$home_norm/.claude/worktree-required"`)
  - `_lib_autonomous_shipping_active` (line ~615: `"$home_norm/.claude/autonomous-shipping-required"`)
  - `_lib_active_bypass_marker_live` (line ~688: `"$HOME/.claude/$marker_dir_name/$session_id"`)
- **`claude/.claude/scripts/_config_dir.py`** (new) — `config_dir()` per Approach.

### Review-gate marker system (closes the cross-account bypass)

- **`claude/.claude/scripts/marker.sh`** — every `$HOME/.claude/...` literal (lines ~42, 189-306: `sessions/`, `code-review-markers/`, `skill-review-markers/`, `plan-review-markers/`, `ready-for-review-markers/`, the four `.*-active.d/` dirs, the `clear-stale` sweep) → `$(_lib_config_dir)/...`. Also fix line 8's self-source per ledger row 8 — `dirname`-relative, not `$HOME`-based.
- **`claude/.claude/hooks/require-code-review.sh`** (line 107), **`require-plan-review.sh`** (line 157), **`require-ready-for-review.sh`** (line 206) — the `"$HOME/.claude/*-markers"` literal passed to `_lib_marker_value_present` → `"$(_lib_config_dir)/*-markers"`.

### `require-memory-skill.sh` (silently-dead gate under `CLAUDE_CONFIG_DIR`)

- Replace `REAL_HOME=$(_lib_realpath_m "$HOME")` with
  `REAL_CONFIG_DIR=$(_lib_realpath_m "$(_lib_config_dir)")`, and both match
  patterns (lines 83, 90: `$REAL_HOME/.claude/projects/...`) →
  `$REAL_CONFIG_DIR/projects/...`. Per ledger row 6, an unresolvable
  `_lib_config_dir` exits the whole hook with 0 (no candidate classified,
  fail open) rather than emit_deny.

### Session lookup

- **`claude/.claude/hooks/capture-session-id.sh`** (lines 61, 66, 76: `mkdir`, session-file write, active-dir scan) and **`cleanup-session-id.sh`** (line 46) → `$(_lib_config_dir)/sessions/...`.

### Transcript/analysis tooling (the originating incident)

- **`claude/.claude/scripts/transcript-analysis.py`** — `PROJECTS_DIR = Path.home() / ".claude" / "projects"` (line 23) and the independent `Path.home() / ".claude" / ".handoff-nudge.log"` (line 2665) → both via `config_dir()`.
- **`claude/.claude/scripts/token-analyzer.py`** (line 10) and **`analyze-context.py`** (line 26) → same.
- **`claude/.claude/hooks/nudge-error-mode-analysis.sh`** — its own kill-switch/marker/checkpoint/log paths (lines 75, 106, 139, 162) → `$(_lib_config_dir)/...`; **and** its hardcoded invocation of the script itself (line 143: `"$HOME/.claude/scripts/transcript-analysis.py"`) → `"$(_lib_config_dir)/scripts/transcript-analysis.py"` (ledger row 9 — env var propagation to the subprocess is automatic once the hook process itself has it set; this is a separate path bug, not an env-passthrough issue).

### Worktree/shipping sentinels — callers double-hardcoding beyond their `_lib.sh` call

- **`claude/.claude/hooks/require-worktree-for-git-writes.sh`**, **`nudge-worktree-anchor.sh`** (line 87: `STATE_DIR`) — route through `_lib_config_dir` alongside their existing `_lib_worktree_enforcement_active` call.
- **`claude/.claude/hooks/advance-past-commit-stall.sh`** (lines 45, 48, 57, 85, 110) and **`cleanup-commit-stall-marker.sh`** (line 29) — five independent hardcodes beyond the one `_lib_autonomous_shipping_active` call; all → `_lib_config_dir`.
- **`claude/.claude/hooks/require-worktree-for-file-writes.sh`** — per ledger row 5, this one is a **union**, not a swap: keep the existing literal `$HOME/.claude/*` match (needed for stow-fold detection — quoted rationale: *"Under stow directory-fold, `$HOME/.claude/` is a symlink to the package's `.claude/` directory... realpath cannot be used to detect this"*) and additionally exempt `$(_lib_config_dir)/*` (the harness-infrastructure category rationale: *"Claude Code's own infrastructure paths... are written by the harness and skills as normal operation, never as project feature work"* — true regardless of which directory currently holds that infrastructure).

### Active-bypass marker callers

- **`claude/.claude/hooks/require-routing-read.sh`** (lines 54, 58), **`log-routing-read.sh`** (lines 32, 35-36), **`session-marker-dashboard.sh`** (lines 59-61) → `_lib_config_dir`.

### Remaining kill-switch / nudge-marker / log sites

- **`claude/.claude/hooks/nudge-handoff-near-context-cap.sh`** (lines 76, 124-126), **`cleanup-handoff-nudge-marker.sh`** (line 25), **`cleanup-worktree-anchor-nudge-marker.sh`** (line 25), **`set-session-title-from-branch.sh`** (line 78), **`consume-durable-continuity-file-on-read.sh`** (lines 87, 98-99, 107 — including its own hardcoded invocation of `resume-context.sh`, same class of bug as row 9) → `_lib_config_dir`.

### User-config guard files (armed opt-in configs) — union, not swap

- **`claude/.claude/hooks/deny-credential-file-reads.sh`** (line 42), **`deny-credential-bash-reads.sh`** (line 57), **`deny-data-file-reads.sh`** (line 83), **`deny-pii-in-commits.sh`** (line 220), **`deny-private-project-refs.sh`** (line 670), **`redact-credential-values.sh`** (line 41) — each reads one `${HOME}/.claude/<name>.md` opt-in config file. **Not a mechanical swap to `$(_lib_config_dir)/<name>.md`**: a `CLAUDE_CONFIG_DIR` user who already armed a guard at the legacy `$HOME/.claude/<name>.md` location would silently lose that protection post-fix — a previously-armed credential/PII/private-project redaction control going dark with no signal is an enforcement-invariant regression, not an acceptable side effect. Fix: check **both** locations, same union pattern as the `require-worktree-for-file-writes.sh` exemption (ledger row 5) — `$(_lib_config_dir)/<name>.md` first, falling back to `${HOME}/.claude/<name>.md` if the resolved-config-dir copy doesn't exist. Either location arms the guard.

### Reuse opportunities

Every site above reuses `_lib_config_dir` / `config_dir()` — no new
per-file logic. `require-memory-skill.sh` reuses `_lib_realpath_m` (already
imported) for `REAL_CONFIG_DIR`, exactly as it already does for `REAL_HOME`.

## Verification

Every affected file already has a test file (confirmed — no coverage gap,
no new test files needed), but the existing test *infrastructure* needs
three additions before the new cases below can be written:

**Test-infrastructure additions (do these first):**

- **`claude/.claude/hooks/tests/conftest.py`** — `isolated_home` (lines
  14-23) sandboxes `$HOME` but never unsets `CLAUDE_CONFIG_DIR`. Once hooks
  honor it, any contributor running the suite with `CLAUDE_CONFIG_DIR` set
  in their own shell — the exact multi-account persona this plan targets —
  gets the *entire* hook suite silently reading/writing outside the
  sandbox. Add `monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)` to
  the fixture.
- **`claude/.claude/hooks/tests/helpers.py`** — `marker_path`/`write_marker`
  (lines 570-591), `plan_review_marker_path`/`write_plan_review_marker`
  (613-644), and `skill_review_marker_path`/`write_skill_review_marker`
  (647-674) are hardcoded to a single `home` param and can't express
  "write a marker under config-dir A, read under config-dir B" — required
  for the cross-account regression test below. Add an independent
  `config_dir` parameter (defaulting to `home / ".claude"` to preserve
  existing callers) to each of these six helpers.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** and
  **`test_token_analyzer.py`** — add `sys.path.insert(0, str(_SCRIPT.parent))`
  before each file's `exec_module` call, or add
  `claude/.claude/scripts` to `pytest.ini_options.pythonpath` — either
  closes the `ModuleNotFoundError` on `_config_dir` import (see Approach).

**New/extended test cases:**

- **Shell:** add `CLAUDE_CONFIG_DIR`-set cases to each hook's existing test
  in `claude/.claude/hooks/tests/`, reusing the (now `CLAUDE_CONFIG_DIR`-
  clean) `isolated_home` fixture, following the
  `env.pop("CLAUDE_CONFIG_DIR", None)` / `extra_env={"CLAUDE_CONFIG_DIR": ...}`
  pattern already proven in
  `hooks/tests/test_install_sh_repo_relocation_support.py:347,404`
  (`test_uses_config_dir_settings_when_set`,
  `test_uses_home_settings_when_config_dir_unset`). Add direct
  `_lib_config_dir` unit cases to `hooks/tests/test_lib.py`: the
  happy paths, a relative-value case asserting failure (return 1, no
  fallback to cwd-relative resolution), and an empty-string
  `CLAUDE_CONFIG_DIR=""` case asserting fallback to `$HOME/.claude`.
- **Python:** new `claude/.claude/scripts/tests/test__config_dir.py` for
  `config_dir()`'s env-var/fallback/relative-path-rejection branching.
  Existing `test_transcript_analysis.py` / `test_token_analyzer.py` keep
  their `monkeypatch.setattr(_mod, "PROJECTS_DIR", ...)` fixtures
  unchanged (still valid since `PROJECTS_DIR` stays a module-level
  attribute, once the `sys.path` fix above lands); add one case per file
  asserting `CLAUDE_CONFIG_DIR` is honored when set.
- **Cross-account bypass regression test:** in `test_require_code_review.py`,
  `test_require_plan_review.py`, and `test_require_ready_for_review.py`
  (all three gates, not just code-review), using the extended `helpers.py`
  functions above, assert that a marker written under one
  `CLAUDE_CONFIG_DIR` value does **not** satisfy the gate when the
  session's `CLAUDE_CONFIG_DIR` is different — this is the test that
  proves the bypass this plan found is actually closed for the
  differentiated-profile case.
- **`require-worktree-for-file-writes.sh`** — add a case to
  `test_require_worktree_for_file_writes.py` setting `CLAUDE_CONFIG_DIR`
  to a directory outside `$HOME/.claude` and asserting a write under
  `$(_lib_config_dir)/plans/x.md` is exempted (the new union arm; existing
  cases all cover only the literal-`$HOME/.claude` arm today).
- **`require-memory-skill.sh`** — add a case to `test_require_memory_skill.py`
  pinning the fail-open judgment call from ledger row 6: `$HOME` empty and
  `CLAUDE_CONFIG_DIR` unset → the gate allows the write through (no
  candidate classified) rather than blocking it.
- **Resolver fail-closed posture** — add cases proving `marker.sh` (write)
  and each of `require-code-review.sh` / `require-plan-review.sh` /
  `require-ready-for-review.sh` (read) actually abort/deny — not silently
  no-op toward "gate satisfied" — when `_lib_config_dir` is forced to fail
  (e.g. `CLAUDE_CONFIG_DIR` set to a relative value in the test env).
- **Guard-config union** — add a case per `deny-*`/`redact-*` hook proving
  a guard armed at the *legacy* `$HOME/.claude/<name>.md` path still fires
  when `CLAUDE_CONFIG_DIR` is set to a directory with no copy of that file.
- **Full suite from the worktree** (per repo CLAUDE.md, `.venv` lives at the
  main worktree root only):
  ```bash
  ../../../.venv/bin/pytest claude/.claude/
  ../../../.venv/bin/ruff check claude/.claude/
  scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
  ```
- **Manual smoke:** run `CLAUDE_CONFIG_DIR=$(mktemp -d) claude/.claude/scripts/marker.sh write code-review` (or equivalent) and confirm the marker lands under that temp dir, not `$HOME/.claude`.

## Out of scope

- **`relocate-claude-config.sh`** — confirmed correctly `$HOME`-anchored (ledger row 4); no change.
- **Any settings.json schema change** — this is pure path-resolution logic, no new configuration surface (ledger row 3).
- **Multi-profile install tooling beyond what `register-marketplace.sh` already provides** — out of scope; this plan only fixes existing hooks/scripts to *respect* `CLAUDE_CONFIG_DIR` when it's already set by whatever mechanism the user uses to switch profiles.
- **Cross-account gate bypass when both accounts share the undifferentiated default (`CLAUDE_CONFIG_DIR` unset on both)** — per Context and the ledger's root-cause framing, this plan's fix only isolates accounts that are actually differentiated by distinct `CLAUDE_CONFIG_DIR` values. Adding account-identity partitioning on top of a shared, unconfigured default directory is a materially different mechanism (would need some other account-identity signal) and is not needed for the realistic multi-profile case this plan targets.
- **Per-profile guard-config duplication** — after this fix, each `CLAUDE_CONFIG_DIR` profile needs its own copy of `private-projects.md`/`pii-patterns.md`/etc. to get guard coverage equivalent to today's single shared file (mitigated for already-armed users by the legacy-path fallback in ledger row 10, but a *new* guard armed only under one profile doesn't automatically cover another). A maintenance cost, not a defect; no cross-profile config-sharing mechanism is proposed here.
