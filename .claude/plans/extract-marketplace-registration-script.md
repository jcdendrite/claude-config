# Extract per-profile marketplace registration out of install.sh

## Context

Ship a standalone, profile-parameterized marketplace-registration script extracted
from `install.sh`, so a machine running multiple Claude Code account profiles
(each pointed at its own config directory via `CLAUDE_CONFIG_DIR`) can register
this repo's plugin marketplace for every profile individually, not just whichever
one happened to be active when `install.sh` last ran.

**Why now:** `install.sh` currently registers this repo's Claude Code plugin
marketplace (and installs the user-scope plugins enabled in `settings.json`) as
a single step tied to whichever config profile is active in the shell that runs
it. On a machine running multiple Claude Code account profiles, any profile
other than the one `install.sh` was run under ends up without the marketplace
registered — plugin/skill-update tooling that depends on the marketplace then
fails with "marketplace not configured" until someone manually re-runs
registration for that specific profile.

**Intended outcome:** a new standalone script,
`claude/.claude/scripts/register-marketplace.sh`, that resolves
`${CLAUDE_CONFIG_DIR:-$HOME}/.claude/settings.json`, is idempotent and
non-interactive, and can be invoked repeatedly — once per profile — by any
external per-profile provisioning tool on the machine. `install.sh` keeps
calling it once, bare (no `CLAUDE_CONFIG_DIR` set), for its own profile, so the
existing single-profile install path is behaviorally unchanged. Wiring an
external per-account loop to call this script is a separate, out-of-scope
follow-up in another repo — not touched here.

This repo is public and `claude/` installs to every contributor who runs
`install.sh` (`CLAUDE.md`: "Plans in this repo affect all stow users"). This
plan and the eventual PR are framed around "any stow consumer running multiple
Claude Code profiles," not a specific person's or organization's setup — no
sibling repo, external tool, or specific account/profile name is named
anywhere in committed prose.

## Approach

**Root problem:** marketplace registration is welded to `install.sh`'s
single-profile, interactive-adjacent install flow, so it cannot be re-run
per-profile without re-running all of `install.sh` (stow, chmod hardening,
shell-rc edits) for every profile on a machine that only needs it once.

**Mechanism 1 — new self-contained script, `CLAUDE_CONFIG_DIR`-parameterized.**
`anchors: root`. A profile is identified by `CLAUDE_CONFIG_DIR`; the lightest
mechanism that lets a caller target an arbitrary profile without re-running the
rest of `install.sh` is a script that reads that one env var and does only the
registration work. Two heavier alternatives considered and rejected:
  - Adding a `--profile <dir>` flag to `install.sh` itself and having it loop
    internally — rejected: still forces a full stow/chmod/shell-rc pass per
    profile (stow targets `$HOME`, which is shared across profiles on one
    machine — re-running it per profile is redundant work, not idempotent
    no-ops, since the chmod/shell-rc steps print warnings/backups each time).
  - A long-running daemon or Claude Code hook that syncs all profiles on
    schedule — rejected: install-time registration is a one-shot operation
    with no ongoing state to watch; a daemon is a wider-scope mechanism than
    a one-shot idempotent script solves for.

**Mechanism 2 — location: `claude/.claude/scripts/register-marketplace.sh`
(stow-managed), not repo-root `scripts/` or a `~/.local/bin` real-file copy.**
`anchors: row1`. `[verified: claude/.claude/scripts/ dir listing this session]`
`CLAUDE_CONFIG_DIR` profiles share one `$HOME` on a given machine (it overrides
only where Claude Code's *own config* lives, not the user account or `$HOME`)
— so `install.sh` only needs to stow this repo into `$HOME` **once**; the
resulting `~/.claude/scripts/register-marketplace.sh` is then invocable
against *any* `CLAUDE_CONFIG_DIR` value, repeatedly. This matches the existing
convention for every other stow-managed executable in
`claude/.claude/scripts/` (`marker.sh`, `cleanup-merged-branches.sh`, etc.).
Two heavier alternatives considered and rejected:
  - `~/.local/bin/register-marketplace` as a **real file copy**, mirroring
    `relocate-claude-config.sh` — rejected: that script needs a real-file copy
    specifically because its job is repairing a *broken* stow chain, so it
    cannot itself depend on stow having succeeded. `register-marketplace.sh`
    has no such bootstrap requirement — it only ever runs after `install.sh`'s
    own `stow -v --adopt` has already succeeded (same precondition every other
    `claude/.claude/scripts/` entry relies on).
  - Repo-root `scripts/register-marketplace.sh` — rejected: repo-root
    `scripts/` today holds only `list-shell-files.sh`, a contributor/CI
    dev-tooling helper that is never stowed or invoked from an installed
    profile. A script meant to be invoked from an arbitrary machine profile
    belongs in the stow-managed tree, not the contributor-tooling tree.

**Mechanism 3 — self-locating `REPO_DIR`, not an inherited/passed parameter.**
`anchors: row1`. `[verified: install.sh:16-22, relocate-claude-config.sh:100-110 readlink -f pattern]`
The script needs its own absolute checkout path to register as a marketplace
source. Rather than requiring every caller (install.sh, or any future external
tool) to pass it, the script resolves its own location the same way
`install.sh` resolves `REPO_DIR` for itself (`pwd -P` canonicalization), plus
one extra step: because the script may be invoked via its stowed symlink
(`~/.claude/scripts/register-marketplace.sh` → the checkout), `$0` must first
be resolved through `readlink -f` before taking `dirname` of the result. This
is genuinely new logic, not a direct reuse of either cited precedent —
`install.sh`'s own `REPO_DIR` resolution is never exercised through a symlink
(install.sh is always run from a direct checkout path), and
`relocate-claude-config.sh`'s `_readlink_f` is a single-path canonicalizer,
never chained through multiple `dirname` hops. Treat it as new code requiring
its own test (see Critical files), not as inherited reliability.

**Exact formula** (avoids the "dirname N times" ambiguity — a script living
at `<repo>/claude/.claude/scripts/register-marketplace.sh` needs one `dirname`
to strip the filename, landing at `.../claude/.claude/scripts`, then three
more `..` hops to reach `<repo>`):

```bash
resolved="$(readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")"
REPO_DIR="$(cd -- "$(dirname -- "$resolved")/../../.." && pwd -P)"
```

**Legitimacy canary, matching `relocate-claude-config.sh`'s
`repo_looks_legitimate()` precedent:** after resolving `REPO_DIR`, verify
`[ -f "$REPO_DIR/.claude-plugin/marketplace.json" ]` before using it as the
marketplace source; if absent, fail loudly with a clear error rather than
silently registering the wrong directory. This is a fail-loud correctness
guard against a wrong dirname/symlink resolution (the same risk the "exact
formula" above targets), not a new defensive layer — `relocate-claude-config.sh`
pairs this exact canary with its own path-resolution logic for the same
reason.

Alternative rejected: requiring a `REPO_DIR` argument/env var from every
caller — rejected as a needless second parameter when self-location, paired
with the canary above, is reliable.

**Mechanism 4 — duplication vs. `relocate-claude-config.sh`'s
`sync_marketplace_registration()`: leave both, documented, not unified in
this PR.** `anchors: row1`. `[verified: relocate-claude-config.sh:349-394 read in full this session]`
`relocate-claude-config.sh` already contains a function that performs the same
three-branch idempotent add/remove-add/no-op self-registration check as
`install.sh`'s block. After this extraction, the codebase will have exactly
two implementations of that check (this new script's, and
`relocate-claude-config.sh`'s) instead of the current two
(`install.sh`'s inline copy, and `relocate-claude-config.sh`'s) — not a new
duplication, a pre-existing one that this PR does not resolve. Recommendation:
**do not unify them in this PR.** The two call sites have different failure
semantics — `relocate-claude-config.sh`'s copy is deliberately non-fatal
(marketplace sync is a best-effort step *after* the relocation itself already
succeeded; it warns and returns 0 if `claude` is absent), while this new
script's copy should propagate failure (marketplace registration *is* the
script's whole purpose, and `install.sh`'s own call today aborts on failure via
`set -e` — matching that is required for `install.sh`'s call to stay
behaviorally unchanged, per this PR's own goal). `relocate-claude-config.sh`
also explicitly documents that it "deliberately does NOT source `_lib.sh` or
otherwise depend on `~/.claude/` being intact" (relocate-claude-config.sh:12-14)
— sourcing or calling out to a second stow-managed script would be exactly the
kind of dependency that comment rules out for that file. Unifying cleanly
would mean either extracting a *third*, lower-level shared helper both scripts
call (a heavier abstraction than either currently needs, per CLAUDE.md's "a
small duplicated value can beat a bad abstraction built only to remove it"),
or making one script depend on the other (touching
`relocate-claude-config.sh`'s carefully-scoped dependency posture, which this
ticket does not require and Axis 1 file-boundary discipline argues against).
Flag this explicitly in the PR description as a known, pre-existing
duplication and a candidate for a **separate** follow-up PR — do not fold that
refactor into this diff.

**Mechanism 5 — style: match `claude/.claude/scripts/`'s dominant local
convention over the repo's style-guide default.** `anchors: row2`.
`[verified: 8-file style survey this session — 5/8 use #!/usr/bin/env bash;
5/6 standalone executables use set -euo pipefail; every file in the dir uses
[ ] almost exclusively despite shell-script-conventions.md preferring [[ ]]]`
Use `#!/usr/bin/env bash`, `set -euo pipefail`, and `[ ]` (not `[[ ]]`) to
match the file's closest sibling (`relocate-claude-config.sh`) and its
extraction source (`install.sh`, which also uses `[ ]` throughout) — the
documented style-guide preference for `[[ ]]` is a repo-wide default that
every actual script in this directory already deviates from; consistency with
neighbors wins here over the un-followed default.

**Mechanism 6 — `install.sh`'s remaining control flow: keep the outer
`if [ -f "$SETTINGS_FILE" ]` guard, narrow its body.** `anchors: root`.
`[verified: install.sh:143-263 read in full this session]` Today, a single
`if [ -f "$SETTINGS_FILE" ]; then ... fi` (lines 143-263) wraps ALL FOUR of:
self-registration (143-181), `extraKnownMarketplaces` loop (183-202),
user-scope plugin install (204-214), AND the project-scope plugin install
(217-262, explicitly out of scope for this PR — must stay in `install.sh`
unchanged). Extracting only the first three sub-blocks means `install.sh`'s
guard must keep wrapping the project-scope block, but its body shrinks to a
single call to the new script followed by the unchanged project-scope logic:

```bash
SETTINGS_FILE="$HOME/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
  "$REPO_DIR/claude/.claude/scripts/register-marketplace.sh"

  echo ""
  echo "=== Installing this repo's own project-scope plugins ==="
  PROJECT_SETTINGS_FILE="$REPO_DIR/.claude/settings.json"
  ... (lines 217-262, unchanged)
fi
```

This keeps `install.sh`'s own-profile behavior identical: if
`$HOME/.claude/settings.json` is absent, neither the new script nor the
project-scope block runs today, and that stays true. The `=== Registering
marketplaces ===` and `=== Installing plugins from enabledPlugins ===` echo
headers move into the new script (it owns its own CLI output for standalone
invocation); `install.sh` no longer prints them itself.

**Mechanism 7 — the new script's own settings-file existence check is a
second, independent guard, not a dependency on the caller's.** `anchors: row1,row6`.
Because the script must also work when invoked directly by an external
profile-provisioning tool (no `install.sh` guard wrapping it), it performs its
own `[ -f "$SETTINGS_FILE" ]` check internally and exits 0 with an explanatory
message if the target profile has no `settings.json` yet, rather than erroring.

**Assumption ledger — additional rows:**
- `[unverified]` The `claude` CLI's `plugin marketplace`/`plugin install`
  subcommands honor `CLAUDE_CONFIG_DIR` the same way Claude Code's core config
  loading does. Verified only that `CLAUDE_CONFIG_DIR` is documented as "a
  first-party Claude Code env var that relocates `~/.claude` paths" generally
  (README.md:180, referencing the Claude Code docs) and that the one existing
  in-repo usage (`statusline-command.sh`) reads `.claude.json` through it — not
  verified against the `claude` CLI's own source for the specific
  subcommands this script shells out to. Since the script only ever
  `export`s nothing and lets the env var inherit naturally into the `claude`
  subprocess (standard shell inheritance, not something this script controls),
  this is a reasonable reliance on documented behavior, not a new assumption
  this script introduces — call it out in the PR description so a reviewer
  with access to `claude` CLI internals can confirm.
- `[verified: this session, README.md:180 + statusline-command.sh:85-87]` The
  correct env-var-default pattern is `${CLAUDE_CONFIG_DIR:-$HOME}/.claude/...`
  (base defaults to `$HOME`, path appended outside the substitution) — not
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`, which is not equivalent when
  `CLAUDE_CONFIG_DIR` is set to a path not ending in `.claude`.
- `[engineer-verified]` Branch name `extract-marketplace-registration-script`
  (no `GH-<num>/` ticket prefix) is already decided — worktree and branch
  created from `origin/main` at `0e43b5e` before this plan was written.
- `[unverified, explicitly out of scope]` Whether the default/unnamed
  ("personal") profile should ever be swept by an external per-profile loop,
  or stay `install.sh`-only forever. This plan does not decide it — the new
  script is agnostic to who calls it; `install.sh`'s own call remains
  bare/unparameterized either way.
- `[verified: this session, reasoning below]` **Threat model —
  `CLAUDE_CONFIG_DIR`/target `settings.json` content is not independently
  verified as trusted before driving `claude plugin marketplace add` /
  `claude plugin install`.** `relocate-claude-config.sh` documents an explicit
  threat model for its own semi-trusted `<new-path>` argument (a
  prompt-injected local session could supply it); the same local-session
  vector applies here — a compromised session could set `CLAUDE_CONFIG_DIR`
  to an attacker-authored profile directory and have this script auto-install
  plugins from its `settings.json`. Decision: **no new ownership/trust check
  added**, because a session with enough local access to control
  `CLAUDE_CONFIG_DIR` and author a target `settings.json` already has
  everything needed to invoke `claude plugin install <malicious-plugin> -s
  user` directly — this script automates an action already reachable without
  it, rather than granting a new capability. `install.sh` carries the
  identical reliance today for `$HOME/.claude/settings.json` (no ownership
  check either). State this reasoning in the new script's header comment,
  mirroring `relocate-claude-config.sh`'s threat-model comment, so the
  decision is visible rather than implicit.

## Critical files

- **`claude/.claude/scripts/register-marketplace.sh`** (new) — the extracted
  script. Contains, in order: self-locating `REPO_DIR` resolution (the "exact
  formula" + legitimacy canary from Mechanism 3 — new logic, not a reuse of
  `install.sh:16-22` or `relocate-claude-config.sh`'s `_readlink_f`, both of
  which are single-hop canonicalizers never chained through symlink
  resolution the way this script needs),
  `SETTINGS_FILE="${CLAUDE_CONFIG_DIR:-$HOME}/.claude/settings.json"`,
  an early-exit guard if `$SETTINGS_FILE` is absent, then the three moved
  blocks from `install.sh` (self-registration, `extraKnownMarketplaces` loop,
  user-scope plugin install) verbatim except for the `SETTINGS_FILE`/`REPO_DIR`
  source change. Move the `# INSTALL_TEST_FIXTURE: repo-relocation-marketplace`
  markers here unchanged (keep them wrapping exactly the same
  self-registration logic they wrap today, so the existing test's
  marker-extraction contract is preserved).
- **`install.sh`** — replace lines 143-214 with the narrower guard shown in
  Mechanism 6 above (single call to the new script, in place of the
  self-registration + extraKnownMarketplaces + enabledPlugins-install logic).
  Lines 1-142 (deps check, `REPO_DIR` canonicalization, stow, manifest write,
  chmod hardening, machine-level opt-ins) and 217-410 (project-scope plugin
  install onward) stay untouched.
- **`claude/.claude/hooks/tests/test_install_sh_repo_relocation_support.py`**
  — reuse opportunity: keep `_extract_block`, `_run_block`, and the
  marketplace-shim helpers (`_make_claude_marketplace_shim`, `_read_log`,
  `_run_marketplace_block`) exactly as they are; only retarget which file
  they read from. Concretely: rename the `_INSTALL_SH` constant's marketplace
  use to a new `_REGISTER_MARKETPLACE_SH` path constant
  (`Path(__file__).resolve().parents[4] / "claude" / ".claude" / "scripts" / "register-marketplace.sh"`),
  parameterize `_extract_block(start_marker, end_marker, source_file)` to
  accept which file to read (default `_INSTALL_SH`, since the *manifest*
  fixture tests in this same file are unaffected and must keep reading
  `install.sh`), and pass `_REGISTER_MARKETPLACE_SH` explicitly from
  `_run_marketplace_block`. `_run_block`'s existing `REPO_DIR`/`HOME` env-var
  injection needs no change: the marker-delimited snippet itself only
  references `$REPO_DIR` (for the path comparison) and shells out to `claude`
  (shimmed) — it does not reference the new script's self-location logic,
  which lives outside the marker boundaries and is never part of the
  extracted-and-isolated snippet under test.
- **Add a new test** (same file or a new one alongside it) asserting the
  script resolves `$CLAUDE_CONFIG_DIR/.claude/settings.json` when set, and
  `$HOME/.claude/settings.json` when unset — run the whole script (not just
  the marker block) with the `claude` shim and a minimal `settings.json`
  planted at each location, asserting the shim only sees calls when the
  settings file exists at the expected path. This test invokes the script by
  its real worktree path (no symlink), so self-location trivially resolves to
  the real checkout — it validates settings-file resolution, not self-location
  (see next bullet for that).
- **Add a dedicated self-location test**, since no other planned test
  exercises the symlink-resolution path the real stow-managed invocation
  actually uses: in a temp directory, create a symlink to the new script
  (mirroring what `stow` produces at `~/.claude/scripts/register-marketplace.sh`),
  invoke the script through that symlink, and assert the resolved `REPO_DIR`
  (observable via the marketplace-add call's path argument, captured by the
  existing `claude` shim) equals the real checkout root — not the symlink's
  own directory. This is the test the "exact formula" and legitimacy canary
  in Mechanism 3 exist to be checked by.
- **No dedicated "genericness" test.** A grep-based denylist for
  account-provisioning-tool-specific terms was considered and rejected: this
  repo's own redaction rule means no such terms are ever named in-repo to
  derive a grounded denylist from, so any implemented check would either be
  decorative (never fails, since there's nothing real to match against) or
  require reaching for out-of-repo knowledge to populate it — which itself
  would violate the redaction rule the check is nominally enforcing. Rely on
  the existing `deny-private-project-refs.sh` commit/PR hook, which already
  enforces this repo-wide, as the actual enforcement mechanism.
- **`README.md`** (`### Plugins (marketplace)` section, ~lines 182-190) —
  document the new script as the supported per-profile registration
  entry point: `CLAUDE_CONFIG_DIR=<profile-dir> ~/.claude/scripts/register-marketplace.sh`,
  framed generically ("a machine running multiple Claude Code profiles can
  register the marketplace for each one"), no specific external tool named.
- **`docs/scripts.md`** — add a `register-marketplace.sh` entry alongside the
  other `claude/.claude/scripts/` full descriptions (same file documents
  `relocate-claude-config.sh` at ~line 71), following the existing bullet +
  fenced-usage-example format used by every other entry in that file. This
  file wasn't named in the original scope note but is the single authoritative
  home for full script descriptions per this repo's own doc split
  (`README.md` = overview tables, `docs/scripts.md` = full descriptions) —
  leaving it out would leave the doc catalog inconsistent for exactly the kind
  of script it exists to document. State explicitly that the script is safe to
  retry: each of the three registration sub-blocks (self-registration,
  `extraKnownMarketplaces`, `enabledPlugins` install) is independently
  idempotent, but the whole invocation aborts on the first failing step (`set
  -e`, matching `install.sh`'s existing behavior) with no partial-progress
  signal beyond its own stdout — a caller retrying a failed profile from the
  top is safe, not merely convenient.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` from the worktree — full hook
  test suite, including the retargeted repo-relocation test and the two new
  tests.
- `../../../.venv/bin/ruff check claude/.claude/` — no Python changes expected
  beyond the test file, but run per CLAUDE.md's standard command.
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — the
  new `*.sh` file is auto-discovered (git-tracked `*.sh` glob), no manifest
  edit needed.
- Manual smoke test: run `install.sh` end-to-end in a scratch `$HOME` (or
  confirm via the existing test harness's shim-based coverage) and confirm
  marketplace registration + user-scope plugin install output is unchanged
  from before the extraction, just now delegated to the new script's stdout.
- Manual smoke test: invoke
  `CLAUDE_CONFIG_DIR=/tmp/scratch-profile claude/.claude/scripts/register-marketplace.sh`
  against a scratch directory containing a minimal `settings.json`. Confirm
  profile isolation at the `claude` CLI level, not just the script's own exit
  code — diff `claude plugin marketplace list --json` output for the default
  profile before and after, asserting it is unchanged, since a scoping
  mismatch between the CLI's `list` and `add`/`remove` subcommands would
  otherwise pass a naive exit-code-only check while still leaking across
  profiles.

## Out of scope

- The external per-account provisioning tool's wiring to call this script
  once per profile inside its own loop — a separate, sequenced follow-up PR
  in another repo, not touched here.
- Unifying this script's registration logic with
  `relocate-claude-config.sh`'s `sync_marketplace_registration()` (Mechanism
  4) — flagged as a known pre-existing duplication, recommended as a
  candidate for its own follow-up PR.
- Deciding whether the default/personal profile is ever swept by an external
  loop.
- Any changes to `install.sh`'s stow, chmod-hardening, shell-rc-editing, or
  project-scope-plugin-install steps beyond the single new call replacing the
  extracted block.
- Renaming or restructuring other scripts or plugins in this repo.
