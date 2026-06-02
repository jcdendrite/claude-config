# Plan: macOS bash 3.2 compatibility for two scripts + one hook

Closes #367.

## Context

Stock macOS ships **bash 3.2.57** at `/bin/bash` (frozen at the last GPLv2 release in 2007). Three shell files in this repo use bash-4.0+ features — `declare -A` associative arrays and `mapfile` — so they hard-error or silently no-op for macOS stow users. CI runs on Ubuntu (bash 5) and never caught it. Because `claude/` is stowed to every user who clones this repo, the macOS audience is real, not hypothetical.

The most insidious case is the hook `nudge-handoff-near-context-cap.sh`: its `2>/dev/null || true` fail-open guard swallows the `mapfile` error, so the handoff nudge **silently never fires** on macOS rather than erroring visibly.

Intended outcome: rewrite all three files to bash-3.2-portable constructs (no version-bump requirement, so they work out-of-the-box), and add a pytest regression guard so the constructs can't creep back in. Plan review surfaced a fourth macOS incompatibility of the same theme in a file already being rewritten — `sort -V` (a GNU-only flag) in `update-claude-config-plugins.sh` — folded in as an in-scope sibling fix so the script is actually correct on macOS, not merely non-crashing.

## Approach

Rewrite to portable constructs, preserving observable behavior exactly. The codebase already demonstrates the target pattern — `update-claude-config-plugins.sh` uses lockstep parallel indexed arrays (`OUTDATED_NAMES` + siblings, lines 151–155), and `cleanup-merged-branches.sh` already maintains an indexed companion (`MERGED_BRANCHES`, `SKIPPED_IN_USE`) alongside each associative map. So the rewrite mostly adds value-parallel arrays beside keys that are already tracked, plus a small linear-search helper for lookups.

**Substitution rules:**

- `mapfile -t arr < <(cmd)` → `arr=(); while IFS= read -r line; do arr+=("$line"); done < <(cmd)`
- `declare -A MAP` → a parallel indexed value array, keyed off the already-existing companion key array; lookups become a linear search with **exact-string** comparison (`[ "$x" = "$key" ]`, never `[[ … == … ]]` glob matching — branch-name keys may contain `/`).

Rationale for parallel-arrays-over-associative: it's already the house pattern in both scripts, N is tiny (handful of plugins / merged branches), keys can contain `/` (slash-safe under exact `=`), and it needs no bash version bump. Alternatives set aside: (a) requiring Homebrew bash 4+ — breaks the out-of-the-box stow contract, the explicit non-goal in the issue's root-cause note; (b) a `bash --version` guard that errors early — still leaves macOS users unable to run the scripts.

### File 1 — `claude/.claude/scripts/update-claude-config-plugins.sh`

Two maps keyed by plugin name. Replace `LATEST_VERSION` / `PLUGIN_DESCRIPTION` (decl 100–101) with three lockstep arrays mirroring the existing `OUTDATED_*` style, e.g. `PLUGIN_NAMES` / `PLUGIN_LATEST` / `PLUGIN_DESC`:
- Population (121–122): append to all three in lockstep.
- Emptiness check (137): change `[ "${#LATEST_VERSION[@]}" -eq 0 ]` → `[ "${#PLUGIN_NAMES[@]}" -eq 0 ]` (count the new key array, not a value array).
- Lookup at 164 (`LATEST_VERSION[$entry_name]`) and 227 (`PLUGIN_DESCRIPTION[$plugin_name]`): linear search over `PLUGIN_NAMES` returning the parallel slot. Extract one small `_lookup_by_name` helper (name array + value array + key → echo value or empty) so both call sites share it — preserves the existing `:-` empty-default semantics. Use exact `=` comparison (a plugin name could be a prefix of another).

**Sibling macOS fix — `sort -V` (line 175).** Same file, same macOS-portability theme as the issue but a distinct mechanism: `lower=$(printf '%s\n%s' "$installed_version" "$latest_version" | sort -V | head -1)` uses the GNU-coreutils `-V` flag, which BSD/macOS `sort` does not support — on macOS it errors or silently mis-sorts, producing a wrong "outdated" verdict. Replace the `sort -V | head -1` pipeline with a portable version comparison via `python3`, which this script **already depends on** (the installed-list parser at the bottom of the same loop shells to `python3`). Compare the two dotted-numeric versions as integer tuples and print the lower, e.g. a `python3 -c` that splits each on `.`, maps to `int`, and emits the smaller — matching `sort -V`'s ordering for the semver shapes these plugin versions take. Keep the surrounding `if [ "$lower" = "$installed_version" ]` outdated logic unchanged. Reuse the existing `python3` dependency rather than adding a tool or hand-rolling a bash version-compare. This is flagged as an in-scope sibling fix in the PR description (Incidental edits).

### File 2 — `claude/.claude/scripts/cleanup-merged-branches.sh`

- `mapfile -t ALL_BRANCHES` (197): branch names have no spaces → `while IFS= read -r` append loop. Iteration sites (207, 220, 336, 586) unchanged.
- `MERGED_PR_INFO` + `TIER` (decl 202–203): add two value arrays parallel to the existing `MERGED_BRANCHES` (201), appended in lockstep at the two write sites (247–250, 256–258). Lookups at 274 (`TIER`), 319 (`MERGED_PR_INFO`), 364/366 (`TIER`) → linear search over `MERGED_BRANCHES`.
- `SKIPPED_IN_USE_REASON` (decl 401): add a value array parallel to the existing `SKIPPED_IN_USE` (400), written at 457/462, looked up at 570 → linear search over `SKIPPED_IN_USE`.
- Use exact `=` comparison in every linear search (branch names may contain `/`).
- No change to the `BASH_REMATCH` / `[[ … =~ ]]` blocks (444–445, 294–306) or the `${arr[@]+"…"}` empty-safe expansions (128, 565, 569) — all bash-3.2 compatible already.

### File 3 — `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`

Single `mapfile -t _FIELDS` (33). Per the issue, prefer direct sequential reads over an intermediate array for the 4-field case:

```bash
SESSION_ID=""; AGENT_TYPE=""; PERMISSION_MODE=""; TRANSCRIPT_PATH=""
{
  IFS= read -r SESSION_ID
  IFS= read -r AGENT_TYPE
  IFS= read -r PERMISSION_MODE
  IFS= read -r TRANSCRIPT_PATH
} < <(
  printf '%s\n' "$INPUT" \
    | jq -r '(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.transcript_path // "")' \
    2>/dev/null
) 2>/dev/null || true
```

Must preserve the fail-open `2>/dev/null || true` wrapper and empty-default semantics (a failed `read` leaves the pre-initialized empty value). Update the line-32 comment (currently references `mapfile`) to describe the `read` approach — otherwise the regression test's prose-safe match aside, the comment would be stale.

### Regression guard — new test

New file `claude/.claude/scripts/tests/test_no_bash4_constructs.py`, modeled on `claude/.claude/hooks/tests/test_hook_alignment.py` (the only existing test that globs `.sh` files):
- `_REPO_ROOT = Path(__file__).resolve().parents[4]`.
- Enumerate `sorted((_REPO_ROOT / "claude" / ".claude").rglob("*.sh"))`, **excluding any path with a `/tests/` path segment** (match the segment `tests`, not the substring — a future `scripts/testsuite.sh` must not be silently excluded). Test fixtures may legitimately contain these constructs and are never executed as stowed user tooling; only shipped scripts/hooks are scanned. Scanning the whole `claude/.claude/` tree — not just `scripts/` — is required so the hook regression under `hooks/` is actually guarded.
- Parametrize over the file list with `ids=[p.name for p in …]`.
- **Vacuous-pass guard:** add a separate non-parametrized test asserting `len(discovered) >= SENTINEL` (a committed floor, e.g. the current count of in-scope `.sh` files). An empty parametrize list collects zero tests and reports green, so a `parents[4]` drift or mistyped root would silently stop guarding — the floor assertion makes that failure loud.
- **Matcher:** for each file, scan lines; skip lines whose first non-whitespace char is `#` (handles the one real false-positive — the hook's line-32 prose comment naming `mapfile`); on the remaining lines match `\bdeclare\s+-A\b` and `\b(mapfile|readarray)\b` as **whole words anywhere on the line** — do *not* anchor to `^\s*`, which would let `x=1; declare -A y` escape. Document in the test docstring that the matcher does not parse heredoc/string-literal bodies (the simplest robust scope; no such literal exists in the tree today) and that the test guards only those three named tokens — it is not a full bash-3.2 compatibility proof.

CI: `.github/workflows/tests.yml`'s path regex (line 58) already lists `scripts`, and the run invokes `pytest claude/.claude/` on the whole tree — the new test is auto-collected, no workflow edit needed.

### Hook fail-open behavioral test

The hook is the silent-failure case the issue calls "most insidious," so guard its rewritten parse with a behavioral test, not just the static grep. Add to the hook's existing test module (or a co-located `claude/.claude/hooks/tests/` test) a case that pipes empty and malformed JSON to `nudge-handoff-near-context-cap.sh` and asserts **fail-open** (exits 0, emits no blocking output) — reusing the Layer-2 invocation pattern already in `test_hook_alignment.py`. This exercises the four-`read` rewrite under the degenerate input that the original `mapfile` masked, on a path bash-5 CI does run.

The existing per-script suites already cover the linear-search replacements' realistic branches — `test_cleanup_merged_branches.py` fixtures use slash-containing branch names (`feat/foo`, `feat/alpha`) and multiple merged branches; `test_update_claude_config_plugins.py` uses multiple plugins, a locally-ahead (no-flag) case, and lookup-miss cases. So behavior-equivalence on bash-5 CI covers slash-keys, multi-entry iteration, and misses without new cases; no redundant lookup tests are added.

## Critical files

| File | Change |
|------|--------|
| `claude/.claude/scripts/update-claude-config-plugins.sh` | 2 `declare -A` → 3 lockstep parallel arrays + `_lookup_by_name` helper; **`sort -V` (175) → portable `python3` version-compare** |
| `claude/.claude/scripts/cleanup-merged-branches.sh` | 1 `mapfile` → read-loop; 3 `declare -A` → value arrays parallel to existing `MERGED_BRANCHES` / `SKIPPED_IN_USE` |
| `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` | 1 `mapfile` → 4 sequential `IFS= read -r`; preserve fail-open; update line-32 comment |
| `claude/.claude/scripts/tests/test_no_bash4_constructs.py` | **new** — regression grep, parametrized per `.sh` file, + vacuous-pass floor assertion |
| hook test (in `claude/.claude/hooks/tests/`) | **new case** — empty/malformed JSON → assert hook fail-open |

All file changes land in **one PR** (plan files included per the repo's plan-in-PR rule). The regression grep must land with-or-after the rewrites, never in an earlier standalone commit, or it fails on the still-present constructs.

**Reuse opportunities:**
- `update-claude-config-plugins.sh` lines 151–155 / 177–181 — copy the existing `OUTDATED_*` lockstep-array + `"${!arr[@]}"` iteration idiom for the new plugin arrays.
- `cleanup-merged-branches.sh` lines 201, 400 — `MERGED_BRANCHES` / `SKIPPED_IN_USE` already track the map keys in lockstep; reuse them as the search arrays.
- `claude/.claude/hooks/tests/test_hook_alignment.py` lines 28–44, 96–97 — copy the repo-root resolution, `.glob`/`rglob` enumeration, and `@pytest.mark.parametrize(... ids=…)` shape for the new test.

## Verification

1. **Logic-equivalence (Linux/bash 5):** `.venv/bin/pytest claude/.claude/scripts/tests/ claude/.claude/hooks/tests/` — the existing `test_update_claude_config_plugins.py`, `test_cleanup_merged_branches.py`, and the hook's tests run the rewritten code; all must still pass, proving the rewrite preserved behavior.
2. **Regression guard:** the new `test_no_bash4_constructs.py` passes after the rewrite, and (sanity) fails if reverted on any one file.
3. **Lint:** `.venv/bin/ruff check claude/.claude/`.
4. **Static portability check:** `bash -n` parses cleanly on each rewritten file; grep confirms zero `declare -A` / `mapfile` / `readarray` outside `tests/`; grep confirms no remaining `sort -V`.
5. **macOS bash 3.2 + BSD userland (user-side):** on stock macOS run the two scripts with `--dry-run` under `/bin/bash`, confirm the outdated-version comparison returns correct verdicts (the `sort -V` path), and confirm the handoff hook now fires near the context cap. Linux can't easily host bash 3.2 / BSD `sort`, so steps 1–4 are the gating checks and step 5 is the real-environment confirmation of both the bash-version and BSD-userland dimensions.

## Out of scope

- No bash version-bump requirement / Homebrew dependency (explicit non-goal).
- No changes to the `BASH_REMATCH`/`[[ =~ ]]` regex blocks — already 3.2-compatible.
- No broadening of the regression test to `plugins/` (none currently contain these constructs; can be added later if plugin shell scripts grow).
- **No bash-3.2 container in CI.** A real bash-3.2 (and BSD `sort`) CI job would catch the language/userland dimension automatically, but it's heavier than warranted for developer tooling. The static grep guards the named-construct creep risk; the BSD-userland dimension (e.g. future `sort -V` reintroduction) relies on the manual macOS step. Revisit only if these scripts grow or macOS regressions recur.
- **`sort -V` portable-compare edge cases.** The `python3` integer-tuple compare matches `sort -V` for the dotted-numeric semver these plugin versions use; it is not a general semver parser (no pre-release/build-metadata ordering). That's sufficient for the marketplace's version shapes and avoids a third-party dependency.
