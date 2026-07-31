# Guard against claude-config relocation breaking stow

## Context

Moving or renaming the claude-config checkout directory (e.g. `mv
~/External/claude-config ~/somewhere-else`) instantly breaks every stow
symlink under `~/.claude/` and `~/.local/bin/` at once, because stow's
symlinks encode the checkout's absolute-relative location — and since
`~/.claude/hooks/`, `~/.claude/skills/`, and even the `~/.local/bin/*`
wrapper scripts are themselves part of what breaks, there is currently no
in-repo mechanism left standing that can detect or explain the failure,
and no supported way to recover other than manually re-running `stow` by
hand. This repo has an established pattern of turning "the model/user
might forget" into "the harness makes it structurally impossible or loud"
(hooks + markers); we want that same treatment here: stop Claude Code
itself from performing the move, and give both Claude Code and a human a
single supported command for doing it — or recovering from it — safely.

## Approach

**Two independent guardrails for two different actors**, plus one
supporting fix:

1. A new PreToolUse `Bash` hook, `deny-repo-relocation.sh`, denies any
   `mv`/`rsync --remove-source-files`-shaped command whose resolved
   source path is the claude-config repo root or an ancestor of it, and
   points to the new relocation script instead. This stops **Claude Code
   itself** from ever performing an unsupported move.
2. A manifest file (`~/.claude-config-source`, one line, the absolute
   repo path) plus a new script, `relocate-claude-config`, give **both
   Claude Code and a human** a single supported path to (a) deliberately
   relocate the checkout, or (b) repair the symlinks after someone
   already moved it by hand, outside any Claude Code session, where the
   hook above has no reach.
3. `install.sh`'s marketplace-registration idempotency check is fixed to
   compare the recorded source path, not just the name — required for
   (2) to actually leave a working marketplace registration after a move.

### Why not a filesystem immutable flag (chflags/chattr)?

This was the first mechanism considered, since it's the closest thing to
literal, unconditional prevention, and it's the shape the user first
proposed ("a setting we can set on the file in install.sh"). It was
empirically tested this session and rejected as an install.sh default:

- **Not available cross-platform without violating this repo's own
  rules.** macOS's `chflags uchg` is owner-settable with no special
  privilege (confirmed via local `man chflags` / `chflags(2)`). Linux's
  equivalent, `chattr +i`, is **not** — man7.org's `chattr(1)` states
  "Only the superuser or a process possessing the `CAP_LINUX_IMMUTABLE`
  capability can set or clear this attribute" (confirmed via WebFetch
  this session). `install.sh` would need `sudo` to enable it on
  Linux/WSL, which this repo's own CLAUDE.md forbids running directly —
  so the mechanism would silently cover macOS only, or require a manual
  privileged step this repo doesn't otherwise ask for.
- **Real collateral damage even where it does work.** Tested directly
  (`chflags uchg` on a scratch directory, this session): the flag blocks
  not only rename/delete of the flagged directory itself but also
  **creating, removing, or renaming any entry directly inside it** —
  editing existing file *content* is unaffected, but a `git pull` or
  `git checkout` that adds/removes a top-level (repo-root) file would
  fail outright. Checking this repo's own history
  (`git log --diff-filter=AD --name-status`, this session) shows 10
  commits adding/removing a root-level file since 2026-03-11 — roughly
  one every 2-4 weeks. An install.sh default that intermittently breaks
  ordinary `git pull` for every stow user is exactly the kind of
  compounding, self-inflicted defensive layer this repo's engineering
  guidance warns against.

Decision: don't wire this into `install.sh` at all. Document `chflags
uchg <repo-root>` / `nouchg` in the README as an **optional, manual,
macOS-only** hardening step for users who want extra defense-in-depth
and accept the top-level-entry tradeoff — not a default, not something
any script toggles automatically.

### Threat framing, and where the real security investment goes

A specialist review round (below) correctly pushed back on framing this
purely as "careless human `mv`": Claude Code regularly ingests untrusted
content (web pages, tool output, MCP responses), so a manipulated agent
convinced to invoke the sanctioned escape hatch this hook itself points
to — `relocate-claude-config <new-path>` — with an attacker-influenced
`<new-path>` is a reachable actor this plan must account for, distinct
from a deliberate human argument.

That reframing changes where the design puts its effort. The hook
(`deny-repo-relocation.sh`) is, and remains, a **best-effort** guard
against the common literal-path case — it cannot see through shell
variables, command substitution, or a preceding `cd` in the same
command, and closing that fully would mean adopting the same
cwd-threading machinery `require-worktree-for-git-writes.sh` needed
`parse-git-command.py` for, which Step 4's over-powered-primitive check
weighs against for a hook whose job is catching the *common* case, not
serving as a hard security boundary (equivalent bypasses — `cp -r &&
rm -rf`, `python3 -c "os.rename(...)"`, a Finder drag — exist regardless
of how thorough the `mv`/`rsync` pattern match is, and none of them are
closable by a Bash-command-pattern hook). These are now explicit,
documented "known gaps" in the hook itself (see Critical files), not a
silent hole — matching this repo's existing precedent
(`deny-reviewer-tree-mutation.sh` documents comparable gaps rather than
claiming completeness).

The actual security boundary belongs on the **destination side** — the
one place `relocate-claude-config` is reachable by a semi-trusted caller
and where hardening is practical without heavier machinery: canonicalize
and validate `<new-path>` before using it (see the script's Critical
files entry below).

### Assumption ledger

**Root problem:** moving/renaming the claude-config checkout breaks
every stow symlink under `~/.claude/` and `~/.local/bin/` simultaneously,
with nothing left running inside that checkout able to detect, explain,
or recover from it.

| # | Assumption / mechanism | Tag |
|---|---|---|
| root | Repo relocation breaks stow symlinks repo-wide, with no current detection or recovery path | [engineer-verified] |
| row1 | `deny-repo-relocation.sh` (PreToolUse Bash hook) stops agent-initiated `mv`/`rsync --remove-source-files` targeting the repo root or an ancestor, self-locating the protected path via `readlink -f "$0"` on its own physical (post-symlink) path rather than `git rev-parse` on cwd, since the hook can fire from a Bash call made in any repo | [verified: this session — `readlink -f` works on both this machine's BSD `readlink` and GNU coreutils; existing pattern confirmed by reading `deny-reviewer-tree-mutation.sh` and `require-worktree-for-git-writes.sh` in full] |
| row1a | *Lighter primitive considered and rejected: immutable flag as the sole/default mechanism* — see "Why not a filesystem immutable flag" above | [verified: this session, `man chflags`, `chflags(2)`, WebFetch of man7.org `chattr(1)`, empirical test, `git log`] |
| row1b | *Lighter primitive considered and rejected: manifest + relocate script alone, no active hook* — insufficient by itself since it does nothing to stop or loudly catch the move in the moment an agent attempts it; the hook is the piece that makes the prevention structural rather than advisory, matching this repo's stated enforce-at-the-tool-call-boundary philosophy | [engineer-verified] |
| row2 | The manifest (`~/.claude-config-source`) and `relocate-claude-config` script are the supported path for both deliberate relocation and after-the-fact repair (human already moved it outside any Claude Code session) | [engineer-verified] |
| row2a | `relocate-claude-config` must be installed as a **real file copy** to `~/.local/bin/`, not a stow-managed symlink like every other `~/.local/bin/*` wrapper — because its entire purpose is to keep working when the exact symlink chain it repairs has already failed; a stow-managed wrapper would inherit the same failure it exists to fix | [engineer-verified] |
| row2b | Both a fresh per-entry-symlink `~/.claude` (current install.sh behavior) and a legacy tree-folded single-symlink `~/.claude` (an older stow run, before install.sh's `mkdir -p "$HOME/.claude"` guard existed) occur in the wild and both must be handled by the repair path | [verified: this session — `readlink`/`os.path.islink` probe of a live `~/.claude`, found the tree-folded form: `~/.claude -> External/claude-config/claude/.claude`] |
| row3 | After a move, the marketplace registration for this repo (a directory source, absolute path baked in at `add` time) goes stale, and `claude plugin marketplace update` cannot fix it — it re-fetches from the *existing* recorded source, it does not accept a new path. Only `remove` + `add` re-registers at the new path | [verified: this session, `claude plugin marketplace --help` / `remove --help` / `update --help`] |
| row3a | `install.sh`'s current marketplace-registration idempotency check (`grep -qFx "claude-config"` against the marketplace name) doesn't compare the recorded path — so simply re-running `install.sh` after a manual move reports "already registered" while the registration still points at the old, nonexistent directory. This needs fixing for (2) to actually leave a working marketplace registration | [verified: this session, read `install.sh` lines 70-75] |
| row3b | The correct field for a directory-source registration is `.path` (not `.repo`, which is github-source-only and already used by the separate `extraKnownMarketplaces` loop) — confirmed via a live `claude plugin marketplace list --json` probe on a directory-source entry. `$REPO_DIR` (install.sh line 16) is computed via `cd "$(dirname "$0")" && pwd`, not `pwd -P` — a symlink-adjacent invocation of `install.sh` could make `$REPO_DIR` byte-differ from the recorded `.path` even when they refer to the same directory, thrashing (remove+re-add) the registration every run. Fix: canonicalize both sides (`readlink -f`) before comparing, and switch `$REPO_DIR`'s own computation to `pwd -P` | [verified: this session, specialist review — live `claude plugin marketplace list --json` output captured] |
| row1c | The hook's source-argument resolution cannot see through shell variables, command substitution, or a preceding `cd` in the same command (fragment-splitting has no cwd-threading, matching the git-hook's own documented reason for needing heavier machinery it deliberately isn't adopting here). Fail-open (silently allow) is the correct policy for unresolvable sources, not fail-closed — fail-closed would deny the overwhelming majority of ordinary, repo-unrelated `mv`/`rsync` usage that happens to use a variable, defeating the "zero cost to normal workflows" design goal. This is a documented known gap, not a silent one | [engineer-verified — reasoned tradeoff, confirmed by specialist review round below] |
| row2c | The `--repair` mode's dangling-symlink check (`[ -L "$p" ] \&\& [ ! -e "$p" ]`) has a real, non-adversarial TOCTOU/false-positive path: a symlink pointing at a temporarily-unreachable target (unmounted volume, offline network share, mid-sync cloud-storage path) reads identically to a genuinely broken one. Fix: quarantine-move (`mv` into a timestamped backup directory) instead of deleting, so a wrongly-triggered repair is reversible rather than destructive, and require positive evidence (the new path's own canary check passing) before touching anything under `~/.claude` | [engineer-verified — specialist review round below, concrete failure scenario named] |
| row2d | `relocate-claude-config <new-path>` must treat `<new-path>` as a semi-trusted input (a prompt-injected agent could be steered to invoke the hook's own suggested escape hatch with an attacker-influenced destination), not only a deliberate human argument. Canonicalize it, reject a dangling-symlink destination in addition to the existing "refuse if it already exists" check, and pass `--` before positional arguments to `mv`/`stow`/`install` so a dash-prefixed path can't be parsed as a flag | [engineer-verified — specialist review round below] |

### Alternatives considered for the hook's command-parsing

Read `deny-reviewer-tree-mutation.sh` and `require-worktree-for-git-writes.sh`
in full this session (via a research subagent) to decide between a bash-only
hook (matching the former) or a new Python parser modeled on
`parse-git-command.py` (matching the latter, which exists specifically to
thread cwd through chained `cd`/`git -C` operators). `mv`/`rsync` command
detection doesn't share that shape — there's no cwd-threading problem, just
"does this fragment invoke `mv`/`rsync --remove-source-files`, and do any of
its source arguments resolve under the repo root." Writing a bash-only hook
reusing `_lib_split_fragments` is the lighter primitive; a new Python parser
would be adopting `parse-git-command.py`'s heavier machinery for a problem
that doesn't need it.

`deny-reviewer-tree-mutation.sh` currently has its own local
`_fragment_command_word`/`_fragment_invokes_tool`/`_fragment_has_token`
helpers for exactly this kind of "does this fragment invoke tool X"
check. The new hook needs the same check, so this is the point where two
call sites exist and promoting them into `_lib.sh` (as
`_lib_fragment_command_word` / `_lib_fragment_invokes_tool` /
`_lib_fragment_has_token`) is the DRY move rather than a third
copy-pasted implementation.

## Critical files

- **`claude/.claude/hooks/deny-repo-relocation.sh`** (new) — PreToolUse
  Bash hook, `# hook-class: gate`. Reuses `_lib_parse_tool_input_or_deny`
  and `_lib_split_fragments` from `_lib.sh`. For each fragment: checks
  (via the promoted `_lib_fragment_command_word`) whether it invokes
  `mv` or `rsync` with `--remove-source-files`; for a match, resolves
  every source (non-final, non-flag) positional argument with
  `readlink -f` (falling back to `cd "$(dirname ...)" && pwd -P` only
  when `readlink -f` itself is unavailable — `readlink -f` runs first)
  and compares against the repo root, itself resolved once via
  `readlink -f "$0"` on the hook's own physical path with the
  `/claude/.claude/hooks/deny-repo-relocation.sh` suffix stripped. Denies
  (via `_lib_emit_deny`) if the resolved source equals the repo root or
  is an ancestor of it; naming `relocate-claude-config` in the denial
  reason. When a source argument doesn't resolve at all (contains an
  unexpanded `$`/`` ` ``/`$(` — a variable or command substitution the
  hook can't evaluate, or the fragment was itself mangled by
  `_lib_split_fragments`'s unconditional split on `$(`), the hook
  **allows** (fails open) rather than denying — see the Approach
  section's threat-framing note for why fail-closed here would be
  wrong. The header carries an explicit "Known gaps" section (mirroring
  `deny-reviewer-tree-mutation.sh`'s convention) naming: variable/
  command-substitution-indirected sources, a preceding `cd` in the same
  command changing the effective relative-path base, and
  equivalent-relocation forms this pattern-match doesn't cover at all
  (`cp -r ... && rm -rf`, `python3 -c "os.rename(...)"`, GUI/Finder
  moves) — this hook is a best-effort guard against the common literal-
  path case, not a hard security boundary.
- **`claude/.claude/hooks/_lib.sh`** — promote
  `_fragment_command_word`/`_fragment_invokes_tool`/`_fragment_has_token`
  out of `deny-reviewer-tree-mutation.sh` into `_lib_`-prefixed shared
  functions, now that a second hook needs the identical check.
- **`claude/.claude/hooks/deny-reviewer-tree-mutation.sh`** — update its
  call sites to the promoted `_lib_*` functions; behavior must be
  unchanged (existing tests are the regression guard).
- **`claude/.claude/settings.json`** — register
  `~/.claude/hooks/deny-repo-relocation.sh` in the existing `PreToolUse`
  `Bash` matcher block (same stanza shape as the
  `require-worktree-for-git-writes.sh` / `deny-reviewer-tree-mutation.sh`
  entries at lines ~245/250). Always-on for every stow user, not gated
  behind an opt-in sentinel like `worktree-required` — there's no
  legitimate reason any user would want their own `~/.claude` unprotected
  against this, and it only fires on an actual repo-root move/rename, so
  it costs normal workflows nothing.
- **`claude/.claude/scripts/relocate-claude-config.sh`** (new) — the
  canonical source, stowed to `~/.claude/scripts/` like other utility
  scripts for discoverability/editability. `set -euo pipefail` so a
  failing step aborts immediately rather than continuing into
  marketplace/manifest steps against a half-linked `~/.claude` — the
  `--repair` mode doubles as crash recovery if a run fails partway, but
  only if the script actually stops instead of best-effort continuing.
  Structured as small, independently testable shell functions (resolve
  current repo dir, detect tree-folded vs. per-entry `~/.claude`, canary
  check, validate destination, read/write manifest, canonicalize a path
  for marketplace comparison) separate from the `stow`/`claude`
  call-outs — mirroring this repo's existing preference for testing
  logic at the cheapest layer that proves it (see Verification), rather
  than only exercising the whole script end-to-end against stubbed
  binaries.
  - **Destination validation (both modes, before anything else runs):**
    canonicalize `<new-path>`'s parent via `readlink -f`; refuse if
    `<new-path>` already exists as a real path OR is a dangling symlink
    (`[ -L ] && [ ! -e ]` — the existing "refuse if exists" check alone
    misses a dangling-symlink destination since `-e` is false for one);
    refuse by default if `<new-path>` resolves outside `$HOME` (a
    caller who genuinely wants to relocate outside `$HOME` passes an
    explicit `--allow-outside-home` flag); pass `--` before every
    positional path argument given to `mv`/`stow`/`install` so a
    dash-prefixed path can't be parsed as a flag. Per row2d, this is
    where the design's actual security investment goes — see the
    Approach section's threat-framing note.
  - `relocate-claude-config <new-path>` (repo hasn't moved yet): resolve
    the current repo dir from `~/.claude-config-source` (or, if absent,
    from a live `~/.claude` symlink — handling both the per-entry and
    tree-folded forms per row2b); sanity-check it looks like the repo
    (canary: `.claude-plugin/marketplace.json` present); `cd` there and
    `stow -D -t "$HOME" claude`; `mv` the directory; `cd "<new-path>"`,
    `mkdir -p "$HOME/.claude" "$HOME/.local/bin"` (same tree-fold guard
    install.sh already uses), `stow -v --adopt -t "$HOME" claude`;
    determine the marketplace's current registration state by comparing
    `readlink -f` of its recorded `.path` (from `claude plugin
    marketplace list --json`, per row3b — not `.repo`, which is
    github-source-only) against `readlink -f "$new_path"`: if already
    correct, no-op; if registered under a different path, `remove` then
    `add`; if not registered at all, `add` — three distinct idempotent
    states, not a blind remove-then-add every run; rewrite the
    manifest; re-copy itself to `~/.local/bin/relocate-claude-config`;
    print (a) a reminder that other open shells/sessions still have the
    old `cd` path and need to reopen or `cd` manually, and (b) a note
    that relocation should not be run while other Claude Code sessions
    are active — every hook under `~/.claude/hooks/` is briefly absent
    between the `stow -D` and `stow --adopt` steps, and any concurrent
    session firing a PreToolUse hook in that window gets a hard deny
    (fails closed, but visibly disruptive).
  - `relocate-claude-config --repair <new-path>` (repo was already moved
    outside Claude Code, `~/.claude` is dangling): skip the `stow -D`
    step (the old location is gone); run destination validation and the
    canary check on `<new-path>` FIRST — only proceed once there is
    positive evidence the new location is a legitimate claude-config
    checkout; then, for each `~/.claude` entry (or `~/.claude` itself,
    in the tree-folded case) that is a symlink whose target does not
    exist (`[ -L "$p" ] && [ ! -e "$p" ]`), **quarantine-move** it
    (`mv "$p" "$HOME/.claude-config-relocate-backup/$(basename "$p").$$"`)
    rather than deleting it — per row2c, a dangling-symlink check alone
    can false-positive on a temporarily-unreachable target (unmounted
    volume, offline network share, mid-sync cloud path), and a
    quarantine move makes that failure mode reversible instead of
    destructive; a live (non-broken) symlink or a real file/directory
    sitting where `~/.claude` should be is left untouched, never
    removed; then proceed with the same `mkdir -p` / `stow --adopt` /
    marketplace / manifest sequence as above.
  - Reason it cannot `source _lib.sh` or otherwise depend on
    `~/.claude/` being intact: per row2a, that dependency is exactly
    what it exists to repair.
- **`install.sh`**:
  - Line 16: switch `$REPO_DIR`'s computation from
    `cd "$(dirname "$0")" && pwd` to `cd "$(dirname "$0")" && pwd -P`
    (canonicalize symlinks) — required per row3b so a symlink-adjacent
    invocation of `install.sh` doesn't make `$REPO_DIR` byte-differ from
    the marketplace's recorded `.path`, which would otherwise thrash
    (remove+re-add) the registration on every run once the row3a fix
    below is in place.
  - Write/refresh `~/.claude-config-source` (single line, `$REPO_DIR`)
    idempotently near the existing `stow -v --adopt` call.
  - Fix the marketplace-registration idempotency check (currently
    `grep -qFx "claude-config"` against the marketplace *name* only) to
    also compare `readlink -f` of the registration's recorded `.path`
    field (from `claude plugin marketplace list --json` — not `.repo`,
    which is github-source-only, per row3b) against `$REPO_DIR`, so a
    stale post-move registration gets re-added instead of silently
    reported as "already registered" (row3a).
  - `install -m 755` (real copy, not `stow`) the canonical
    `claude/.claude/scripts/relocate-claude-config.sh` to
    `$HOME/.local/bin/relocate-claude-config` — mirroring the existing
    `mkdir -p "$HOME/.local/bin"` line's intent but deliberately not
    using the thin-wrapper-symlink pattern the other `claude/.local/bin/*`
    entries use, per row2a.
- **`claude/.claude/hooks/tests/test_deny_repo_relocation.py`** (new) —
  cases: `mv <repo-root> <elsewhere>` denied; `mv <ancestor-of-repo-root>
  <elsewhere>` denied; `mv <unrelated-dir> <elsewhere>` allowed; `mv
  <repo-root>/docs <repo-root>/documentation` (renaming a subdirectory,
  not the repo root itself) allowed; `rsync -a --remove-source-files
  <repo-root>/ <elsewhere>/` denied; plain `rsync -a <repo-root>/
  <elsewhere>/` (no source removal) allowed; a variable-indirected
  source (`REPO=<repo-root>; mv "$REPO" <elsewhere>`) and a `cd`-prefixed
  relative-path move (`cd <parent-of-repo-root> && mv <repo-basename>
  <elsewhere>`) both asserted **allowed** — documenting the accepted,
  known gap from row1c rather than leaving it unverified.
- **`claude/.claude/hooks/tests/test_lib.py`** — direct unit tests for
  the promoted `_lib_fragment_command_word`/`_lib_fragment_invokes_tool`/
  `_lib_fragment_has_token` functions, independent of either hook's call
  site — today they're covered only indirectly through
  `deny-reviewer-tree-mutation.sh`'s black-box tests, which stops being
  sufficient once a second hook depends on the same shared functions.
- **New test for the `install.sh` additions** (extend
  `claude/.claude/hooks/tests/test_install_sh_continuity_hardening.py`'s
  sibling pattern, or a new
  `test_install_sh_repo_relocation_support.py`) — extracts the new
  manifest-write and marketplace-idempotency blocks via
  `INSTALL_TEST_FIXTURE` markers (same technique as the existing
  continuity-hardening test) and runs them under an isolated `$HOME`;
  stub only `claude` on `PATH` (matching this suite's existing
  `test_update_claude_config_plugins.py` precedent), and include a case
  asserting the path comparison uses canonicalized (`readlink -f`) form
  on both sides, not raw string equality.
- **`claude/.claude/scripts/tests/test_relocate_claude_config.py`**
  (new) — exercises the primary and `--repair` flows against a fake
  repo dir and an isolated `$HOME`. Stubs only the `claude` CLI on
  `PATH` (matching existing precedent); runs the actual `stow`/`stow -D`
  calls for real rather than faking them — `stow`'s own symlink-adopt
  semantics are exactly what the script's correctness depends on, and a
  fake `stow` would let a wrong working-directory or `-t`/`-d` targeting
  pass silently (see the new CI step below). Named cases beyond the
  two end-to-end flows: the tree-folded single-symlink `~/.claude` and
  the fresh per-entry-symlink `~/.claude` as two distinct repair paths
  (row2b) — not just "both flows exercised" generically; the repair
  mode's quarantine-vs-touch invariant (a live non-broken symlink is
  left untouched, a real file/directory sitting at the target path is
  left untouched, only a confirmed-broken symlink gets quarantined);
  destination validation (dangling-symlink destination rejected,
  outside-`$HOME` destination rejected without `--allow-outside-home`,
  a dash-prefixed destination doesn't get parsed as a flag); and the
  manifest-missing-and-symlink-probe-also-fails case (no signal left to
  locate the repo — the script should error with an actionable message,
  not proceed on a guess).
- **`.github/workflows/tests.yml`** — add a `stow` install step (e.g.
  `apt-get install -y stow`) ahead of the pytest step, since
  `test_relocate_claude_config.py` now exercises real `stow` rather than
  a stub.
- **`README.md`** — new subsection under "Configuration" (sibling to
  "Worktree enforcement"), documenting: what breaks and why, the
  `deny-repo-relocation.sh` hook, the `relocate-claude-config` command
  (both modes), the manifest file, and the optional/manual
  `chflags uchg` hardening note (with its tradeoff spelled out, not
  silently omitted).
- **`docs/hooks.md`** — entry for `deny-repo-relocation.sh`.
- **`docs/scripts.md`** — entry for `relocate-claude-config.sh`.
- **`claude/.claude/skills/review-permissions/REFERENCES.md`** — no
  permissions.allow changes anticipated (the hook is a pure PreToolUse
  gate, the script is user-invoked directly, not through a bare-name
  allowlist entry unless the user wants one — out of scope unless asked).

## Verification

- `../../../.venv/bin/pytest claude/.claude/` (run from the worktree per
  this repo's three-levels-deep venv path) — new tests above plus full
  regression on `deny-reviewer-tree-mutation.sh`'s existing tests after
  the `_lib.sh` promotion.
- `../../../.venv/bin/ruff check claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 shellcheck` (via the repo's
  `.shellcheckrc` flags) on the new/changed shell and Python files.
- Manual end-to-end dry run in a throwaway directory: create a fake repo
  layout, `stow` it to a scratch `$HOME`, run `relocate-claude-config
  <new-path>`, confirm symlinks resolve correctly at the new location
  and the manifest/marketplace state updated; separately, manually `mv`
  the scratch repo without unstowing first and confirm
  `relocate-claude-config --repair <path>` recovers a working `~/.claude`;
  as part of the same dry run, place a live (non-broken) symlink and a
  real directory alongside the dangling ones under the scratch
  `~/.claude` and confirm `--repair` quarantines only the confirmed-
  broken entries, leaving the other two untouched.
- Confirm the hook denies `mv`/`rsync --remove-source-files` against
  this actual repo's own root from a live Claude Code session (in the
  worktree, targeting a scratch clone, not the real checkout) and allows
  an unrelated `mv` elsewhere.

## Out of scope

- Making the filesystem immutable-flag hardening automatic or
  cross-platform-equivalent — documented as an optional, manual,
  macOS-only step only (see rationale above).
- Protecting against the repo directory being *deleted* (`rm -rf`)
  rather than moved — the user's request and the observed failure mode
  are both about relocation; a deletion guardrail is a different problem
  (there's no "supported script" analog — deleting is not something you
  redo somewhere else) and would need its own design if wanted.
- Retroactively repairing this user's own already-tree-folded `~/.claude`
  as part of this change — noted as a discovered, pre-existing gap (row2b)
  that the new `--repair` mode happens to also fix going forward, but not
  something this plan runs proactively against the user's live machine.
- Closing every possible relocation command shape (`cp -r ... && rm -rf`,
  `python3 -c "os.rename(...)"`, `ditto` + delete, a GUI/Finder move) —
  the hook pattern-matches `mv`/`rsync --remove-source-files` only and
  documents the rest as accepted known gaps (row1c), consistent with
  `deny-reviewer-tree-mutation.sh`'s own precedent of naming gaps rather
  than claiming completeness. Closing all of them would mean tracing
  full command execution rather than pattern-matching Bash text — the
  heavier primitive this plan's Step 4 review explicitly weighed against
  adopting for a hook whose job is the common case, not a hard boundary.
