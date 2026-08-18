# register-marketplace.sh: shared config-dir resolution + dedicated test file

## Context

**Goal:** make `claude/.claude/scripts/register-marketplace.sh` refuse a
relative `CLAUDE_CONFIG_DIR` by adopting the repo's shared
`_lib_config_dir()` helper, and give the script a dedicated test file by
relocating its existing whole-script tests out of a file named after
`install.sh`.

The script resolves its target profile inline at `:29`
(`SETTINGS_FILE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"`), while
`claude/.claude/hooks/_lib.sh:106` provides `_lib_config_dir()` for exactly
this and `claude/.claude/scripts/marker.sh:8,178` is the in-repo precedent for
sourcing and calling it. The gap is recorded as backlog phase 1a in
`docs/reports/2026-08-10-repo-quality-audit/findings.md:317`.

**Why now:** phase 1a is the audit's first independent item — no overlap with
any other backlog phase, so it can land alone.

**Intended outcome:** a relative `CLAUDE_CONFIG_DIR` aborts with an
explanatory message and a non-zero exit instead of silently provisioning
plugins into a cwd-dependent directory; register-marketplace's tests live in
`claude/.claude/scripts/tests/test_register_marketplace.py` alongside its
siblings.

### Two premises from the source brief that did not survive verification

Recorded here because the change was scoped against them and the scope moved.

1. **"The one script in the repo with no test file"** — true of file naming
   only. `claude/.claude/hooks/tests/test_install_sh_repo_relocation_support.py`
   already runs the script end to end behind a `claude` PATH shim:
   `TestSettingsFileResolution` (`:359-501`, five tests) and `TestSelfLocation`
   (`:504-571`, two tests) cover config-dir-unset, config-dir-set,
   settings.json-absent-exits-0, `enabledPlugins` install, unregistered-marketplace
   skip, symlink-resolved `REPO_DIR`, and the `marketplace.json` refusal. Writing
   a new file to that spec would duplicate seven tests. The real defect is
   filing, not coverage — hence relocation rather than authoring.

2. **Only one of `_lib_config_dir()`'s three behaviours is a benefit here.**
   Trailing-slash stripping is inert: the value is used solely to build
   `$SETTINGS_FILE`, and `dir//settings.json` resolves identically to
   `dir/settings.json`. The empty-`$HOME` guard converts a currently graceful
   `exit 0` ("nothing to register", since `/.claude/settings.json` does not
   exist) into an abort — and `install.sh:558` gates the call on
   `[ -f "$HOME/.claude/settings.json" ]`, so an empty `$HOME` never reaches
   the script through that path. Only the relative-path rejection changes
   anything worth changing.

## Approach

Source `_lib.sh` from `register-marketplace.sh` and replace the inline
resolution with a status-checked `_lib_config_dir()` call, mirroring
`marker.sh:178`'s fail-closed shape. Relocate the two whole-script test
classes into a new sibling-conforming test file and add the one genuinely new
case (relative value rejected). Update the `docs/scripts.md` bullet, which
documents the current resolution verbatim.

**This is a policy change, not a bug fix, and it is deliberate.** The `claude`
CLI itself accepts a relative `CLAUDE_CONFIG_DIR`, so the script will refuse an
input the tool it wraps accepts. The justification is the write/read
asymmetry: a relative value under a read tool is self-correcting — you read the
wrong file and notice — but this script *provisions*, printing `→ installing …`
and `✓` while the operator's real profile stays untouched. Being stricter than
the CLI is the point.

**It is an operational-safety guard, not a security control, and must not be
described as one.** The script's header (`:11-14`) already records the standing
decision that the resolved `settings.json` is trusted without an ownership
check, because anyone able to set `CLAUDE_CONFIG_DIR` and author that file could
run `claude plugin install` directly. The same actor reaches both the relative
and the absolute path, so no privilege boundary moves — what the rejection
closes is accidental cwd-dependent misprovisioning by the operator. Carry this
framing into the PR description; do not let the asymmetry argument above read as
a security win.

**Alternatives weighed.** An inline four-line absolute-path guard was set aside
by the engineer: it takes the CLI-divergence cost *and* a duplication cost,
leaving two shell implementations of the same rule free to drift, and
`_lib_config_dir`'s own doc comment (`_lib.sh:96-105`) warns about the
unchecked-command-substitution hazard a reimplementation invites. Dropping the
resolution change entirely was also weighed and set aside: it leaves the
cross-invocation footgun open, where two runs from different cwds provision two
different profiles with byte-identical success output.

### Assumption ledger

**Root problem.** `register-marketplace.sh:29` resolves `CLAUDE_CONFIG_DIR`
inline without the absolute-path guard every other config-dir consumer in this
repo applies, so a relative value silently provisions plugins into a
cwd-dependent directory while reporting success.

**Givens** — conditions this design treats as fixed because they lie beyond its
reach:

| # | Given | Reason it is beyond reach |
|---|---|---|
| G1 | The `claude` CLI accepts a relative `CLAUDE_CONFIG_DIR` and resolves it against the invoking cwd | Vendor-owned behaviour. `[verified: probe — `CLAUDE_CONFIG_DIR=relprof claude plugin marketplace list --json` from a scratch cwd exited 0 and created `./relprof/.claude.json` relative to that cwd]` |
| G2 | Official Claude Code docs do not state whether `CLAUDE_CONFIG_DIR` must be absolute | Vendor documentation gap; G1 is empirical, not documented. `[verified: code.claude.com/docs/en/claude-directory and /docs/en/settings fetched; neither specifies path-form requirements]` |
| G3 | On machines that already ran `install.sh`, every hook loads `_lib.sh` from the stowed `~/.claude/hooks/` tree, and no edit to this repo retroactively places a *new* sibling file there — those machines get it only when their operator re-runs `install.sh` | Already-installed state on other people's machines, not an artifact this repo can edit. `install.sh:72` stows entry by entry, so the gap is real rather than tree-folded away; the same constraint is already recorded for the `_lib.sh` split in `findings.md` ("Phase 3 must not create a new file"). `[verified: install.sh:72, findings.md Phase-3 constraint block]` |
| G4 | Rejecting a relative value is the desired behaviour despite G1 | `[engineer-verified]` — chosen this session over an inline guard and over dropping the change |

**Mechanisms.**

| Mechanism | Justification | Anchors |
|---|---|---|
| Source `claude/.claude/hooks/_lib.sh` into the script | Single authoritative home for config-dir resolution; `marker.sh:8` is the precedent for a `scripts/` file doing exactly this | `anchors: root` |
| Status-checked `config_dir=$(_lib_config_dir) \|\| { … exit 1; }` | `_lib.sh:96-105` states the call-site contract: bare interpolation inside a larger string silently collapses to a root-anchored path under `set -e`, because a failing *nested* command substitution does not abort | `anchors: root` |
| Relocate two test classes into a new `scripts/tests/` file | The existing coverage is filed under an `install.sh`-named file, making it undiscoverable; relocation fixes the filing without duplicating assertions | `anchors: row G4` |

**Over-powered-primitive check.** The mechanism is "pull 1,262 lines of hook
library into a 121-line provisioning script." Three lighter primitives exist
and each fails:

1. **Inline `case "$CLAUDE_CONFIG_DIR" in /*) ;; *) exit 1 ;; esac`** — the
   lightest possible primitive, and it does deliver the one wanted behaviour.
   Fails the repo's single-source-of-truth rule without a named exception, and
   invites reintroducing the command-substitution hazard `_lib.sh:96-105`
   documents. Explicitly weighed and rejected by the engineer this session.
   `anchors: row G4`
2. **Extract `_lib_config_dir()` into a small new sibling** (e.g.
   `hooks/_config_dir.sh`) sourced by both callers — would give the shared rule
   without the 1,262-line dependency. Note the obvious objection does *not*
   apply: this script would reach the sibling from the checkout via `$resolved`,
   exactly as it reaches `_lib.sh` (A1), so stow is irrelevant to *its* sourcing.
   It fails one layer up. Extraction means `_lib.sh` must itself source the new
   sibling, and every hook loads `_lib.sh` from `~/.claude/hooks/` — so on the
   already-installed machines of G3, `_lib.sh` would source a file that is not
   there and every hook in the repo would break until the operator re-ran
   `install.sh`. `anchors: row G3`
3. **Have the `claude` CLI reject it upstream** — the correct layer if it were
   ours. Fails on G1: not our code, and it currently accepts relative values.
   `anchors: row G1`

The residual cost of the chosen mechanism is measured, not assumed: sourcing
`_lib.sh` under `set -euo pipefail` completes cleanly with no side effects —
its only top-level statements are function definitions plus `_LIB_`-prefixed
regex and array assignments, none of which collide with any variable
`register-marketplace.sh` uses. `[verified: `bash -c 'set -euo pipefail; . claude/.claude/hooks/_lib.sh'` exits 0; top-level scan of `_lib.sh` shows assignments only]`

### Behavioural facts this design depends on

| # | Assumption | Tag |
|---|---|---|
| A1 | Both invocation paths reach a real `_lib.sh`: `install.sh:560` calls the script by repo path, and `~/.local/bin/register-marketplace` is a two-line `exec "$HOME/.claude/scripts/register-marketplace.sh" "$@"` wrapper through the stow symlink, so `readlink -f` on `$0` lands in the checkout either way | `[verified: install.sh:560, claude/.local/bin/register-marketplace]` |
| A2 | `.shellcheckrc` sets `external-sources=true` and `source-path=SCRIPTDIR`, so a `# shellcheck source=../hooks/_lib.sh` directive mirroring `marker.sh:7` resolves and lints | `[verified: .shellcheckrc]` |
| A3 | A non-zero exit propagates through `install.sh`'s plain `set -e` (`install.sh:2`), aborting the install at `:560` — before the project-scope plugin install (`:563+`) and `ensure_local_bin_on_path` (`:693+`) | `[verified: install.sh:2,560,563,693]` |
| A4 | Aborting the install is acceptable because `install.sh` is re-runnable: the operator unsets the bad variable and re-runs | `[engineer-verified]` |
| A5 | `scripts/tests/conftest.py:41` has an **autouse** fixture that `monkeypatch.setenv("CLAUDE_CONFIG_DIR", …)`, which would break the relocated "config dir unset" test — except `_run_register_marketplace_script` already does `env.pop("CLAUDE_CONFIG_DIR", None)` before building the subprocess env, so the relocated tests stay correct in the new directory | `[verified: scripts/tests/conftest.py:41, test_install_sh_repo_relocation_support.py:345-347,365]` |
| A6 | No meta-test enumerates or counts Python test files, so adding one file and shrinking another breaks nothing. The shell-file enumerators (`test_no_bash4_constructs.py:38-42`, `test_hook_alignment.py:38-49`, `test_lib.py:830-840`) all glob `*.sh` and exclude `tests/` paths | `[verified: repo-wide search for glob/rglob-based inventory assertions]` |
| A7 | No file under `docs/` names `test_install_sh_repo_relocation_support.py`, so the relocation strands no documentation reference | `[verified: grep across docs/]` |
| A8 | The relocated classes depend on no fixture from `hooks/tests/conftest.py` — every test signature is `(self, tmp_path: Path)` and each builds its `home`/`bin_dir` inline | `[verified: hooks/tests/conftest.py fixture list vs. test signatures at :359-571]` |

## Critical files

**`claude/.claude/scripts/register-marketplace.sh`** (modify)

- Add the source line **after** the `.claude-plugin/marketplace.json` guard at
  `:24-27`, not before it — a mislocated invocation then still fails with the
  script's own explanatory self-location error rather than a bare
  "No such file or directory" from `source`, and no `_lib.sh` from an
  unverified directory is ever executed. This ordering is load-bearing and
  nothing tests it, so mark it as such in a one-line comment above the source
  line; a future edit that hoists the source above the guard would otherwise
  pass every check.
- Source relative to the already-computed symlink-resolved `$resolved`
  (`:21`), not `$0`: `. "$(dirname -- "$resolved")/../hooks/_lib.sh"`, preceded
  by `# shellcheck source=../hooks/_lib.sh` (A2). `marker.sh:7-8` sources off
  `$0`; off `$resolved` is strictly more robust for a script whose normal
  invocation is through a symlink. The shellcheck directive is identical either
  way — shellcheck resolves `source=` statically against the script's own
  directory under `source-path=SCRIPTDIR`, never against `$resolved`'s runtime
  value, so the directive stays a literal `../hooks/_lib.sh`.
- Replace `:29` with a status-checked call in `marker.sh:178`'s shape:

  ```
  config_dir=$(_lib_config_dir) || { <message>; exit 1; }
  SETTINGS_FILE="$config_dir/settings.json"
  ```

  Never `SETTINGS_FILE="$(_lib_config_dir)/settings.json"` — see the mechanism
  table.
- The refusal message must name both failure causes the helper folds together
  (relative `CLAUDE_CONFIG_DIR`; unset/empty `$HOME` with no override), matching
  `marker.sh:180-183`. Single-quote it and add the same
  `# shellcheck disable=SC2016` rationale comment `marker.sh:179-181` carries,
  since `$HOME` and `$CLAUDE_CONFIG_DIR` appear as literal variable *names*.
  **Do not interpolate the offending value into the message** — name the
  variable, not its contents. Single-quoting delivers this as a side effect;
  stating it here means the constraint does not depend on the implementer
  noticing why `marker.sh` is single-quoted.
- **Reuse:** `_lib_config_dir()` (`_lib.sh:106`); the message and disable-comment
  shape from `marker.sh:178-184`.
- Leave the `readlink -f` walk and the `marketplace.json` guard alone — both are
  deliberate and documented.

**`claude/.claude/scripts/tests/test_register_marketplace.py`** (create)

Move `TestSettingsFileResolution` (`:359-501`) and `TestSelfLocation`
(`:504-571`) verbatim, together with the two helpers used *only* by them:
`_make_claude_full_shim` (`:281-339`) and `_run_register_marketplace_script`
(`:342-356`). Adapt for the new location:

- Script path per sibling convention (`test_update_claude_config_plugins.py:16`
  uses `Path(__file__).parent.parent / "<script>"`), and **keep `.resolve()`**.
  The `parents[3]` index that `TestSelfLocation` uses to derive `real_repo_dir`
  is unaffected by `.resolve()` — depth does not change absent a symlink in the
  chain. The actual reason is the string-equality assertion at `:533`,
  `_read_log(add_log) == [str(real_repo_dir)]`: the logged value comes from the
  script's own `readlink -f` canonicalization, so an unresolved `real_repo_dir`
  fails the comparison on any checkout reached through a symlink — a spurious
  red, not a missed regression. Still re-derive the index from the new
  expression rather than carrying `parents[3]` over on faith.
- Duplicate the three small module-level names used by *both* sides —
  `_BASH`, `_read_log`, and the script-path constant — rather than importing
  across test files. There is no cross-test-file import convention in this
  suite, and `claude/.claude/tests/helpers.py` carries repo/worktree scaffolding
  and `run_hook`, not these. Test code is DAMP, not DRY.
- Imports needed here: `json`, `os`, `subprocess`, `textwrap`, `shutil`,
  `pathlib.Path`.
- **New test — the load-bearing one:** a relative `CLAUDE_CONFIG_DIR` is
  refused. Assert non-zero exit, the explanatory message on stderr, and that
  the `claude` shim's add-log is **empty** — proving nothing was registered, not
  merely that an error printed.
- No new test for trailing-slash or empty-`$HOME` behaviour at this call site:
  both are inert here (see Context), so asserting them would test `_lib.sh`
  through a subprocess rather than test this script. Note the coverage is
  asymmetric and the plan does not claim otherwise: `test_lib.py:1167-1170`
  does cover empty-`$HOME` for `_lib_config_dir` directly, but `TestLibConfigDir`
  (`:1137-1170`) has **no** trailing-slash case for `CLAUDE_CONFIG_DIR` — the
  nearest thing, `:1295-1313`, pins `HOME="/"` through
  `_permission_prompt_tracking_active`, which is a different function and a
  different input. Adding that case to `test_lib.py` belongs with `_lib.sh`,
  not here; it is listed under Out of scope.

**`claude/.claude/hooks/tests/test_install_sh_repo_relocation_support.py`** (modify)

Delete the two classes, their two exclusive helpers, and the section-header
comment at `:270-278`. Every module-level import stays — including `stat`,
whose only use (`stat.S_IMODE`, `:116`) is inside the staying
`TestManifestAndSelfCopy`. Keep
`_REGISTER_MARKETPLACE_SH`, `_BASH`, and `_read_log`: all three are still used
by `_run_marketplace_block` (`:177-196`) backing the staying
`TestMarketplaceRegistrationIdempotency`. Confirm with a lint pass that no
import or constant is orphaned.

**`docs/scripts.md:90`** (modify) — its bullet states the inline resolution
verbatim; rewrite that clause to describe resolution through the shared helper
and the relative-value refusal. Keep the edit to that clause; the rest of the
bullet (self-location, idempotency, `set -euo pipefail`) is unaffected.

**`docs/reports/2026-08-10-repo-quality-audit/findings.md:317`** (modify,
confirm first) — annotate backlog row 1a with landed status, mirroring row 2a's
existing `— *landed in `484defb`*` form. This report already carries remediation
annotations (commit `70884af`, "Record remediation status on the merged audit
report"), so the table is a living surface, not a frozen record. **Leave the
finding prose at `:274-276` alone** — "the one script in the repo with no test
file" is now demonstrably wrong, but it records what the audit concluded on its
date, and dated report prose is read-only under the preserved-content rule.
Flag the inaccuracy in the PR description instead.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/ -q` — full suite from the
   worktree. The relocated tests must pass in their new directory *and* the
   shrunk file must still pass, which is what proves no shared helper was
   stranded.
   - **Red/green the one new test.** Run the relative-rejection test against
     the *unmodified* script before applying the script edit, and confirm it
     fails. It is not vacuous today — an unmodified script given a relative
     `CLAUDE_CONFIG_DIR` falls through to the "settings.json absent" path and
     exits **0**, so the empty add-log assertion passes on its own and only the
     non-zero-exit assertion distinguishes the two states. Without this step
     nothing catches a later weakening of that one assertion.
2. `../../../.venv/bin/ruff check claude/.claude/`
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
   confirms the `# shellcheck source=` directive resolves under
   `external-sources=true` (A2) rather than emitting SC1091.
4. **End-to-end against a throwaway profile, not the shim.** In a temp
   directory: (a) `CLAUDE_CONFIG_DIR=relative-value` → confirm non-zero exit,
   the explanatory message, and that nothing was registered; (b)
   `CLAUDE_CONFIG_DIR=<absolute temp dir containing settings.json>` → confirm
   normal operation. Never against the real `~/.claude`.
5. **Confirm the `install.sh` abort path is what A3/A4 describe** rather than
   assumed: run `install.sh`'s call shape with a relative `CLAUDE_CONFIG_DIR`
   exported and verify the non-zero exit propagates. Do not run the full
   `install.sh` against the real `$HOME`. **Extract the call shape from the real
   file** (`sed -n '558,560p' install.sh` piped into the throwaway harness), not
   by retyping it — no test invokes `install.sh` itself, so this probe is the
   only coverage of A3, and a hand-copied snippet would keep passing if a future
   edit wrapped the call in an `if` or `&&` and thereby made it `set -e`-exempt.
6. If any check fails, reproduce on a merge-base worktree before treating it as
   caused by this change.

## Out of scope

- **`install.sh`.** It could be taught to warn-and-continue on a config-dir
  refusal instead of aborting; the engineer chose abort (A4). No edit here.
- **A trailing-slash test for `_lib_config_dir` in `test_lib.py`.** Genuinely
  missing (`TestLibConfigDir`, `test_lib.py:1137-1170`, has no such case), but it
  is `_lib.sh`'s coverage gap, not this script's — the behaviour is inert at this
  call site. Belongs in a `_lib.sh`-scoped change.
- **`claude/.claude/scripts/relocate-claude-config.sh`.** Also resolves a config
  dir without `_lib.sh`; `docs/scripts.md` documents that as deliberate — it
  must survive a broken symlink chain it may itself be repairing.
- **`.github/workflows/tests.yml`.** Another session is concurrently wiring
  `plugins/` into CI. Editing it here would collide.
- **A repo-wide inline-config-dir sweep.** The audit already ran it;
  `register-marketplace.sh` was the finding.
- **Restructuring the script's self-location logic** — the `readlink -f` walk and
  `marketplace.json` guard are deliberate and documented.
- **The rest of the audit backlog** — `_lib_repo_root` extraction, the `_lib.sh`
  reorganization, doc/instruction-surface fixes, the `transcript-analysis.py`
  split.

## Review surface

Four files touched plus one created, spanning three domains: a shell
provisioning script that runs at install time, a Python test relocation, and
two documentation edits. Risk concentrates in one place — the script runs
during `./install.sh` on a fresh machine and now carries a new source
dependency, so A1 (both invocation paths reach a real `_lib.sh`) is the
assumption whose failure would be most expensive. The test relocation is
mechanical but wide; the lint and suite runs are what confirm nothing was
stranded.
