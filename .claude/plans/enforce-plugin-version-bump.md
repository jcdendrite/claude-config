# Plan: hook-enforce plugin version bumps

## Context

**Problem.** When a file inside a plugin directory changes, the plugin's `version` in its
`.claude-plugin/plugin.json` must be bumped (plugin-semver rule). Today the only safeguard is one
discretionary prose line in `code-review/SKILL.md:177` ("…invoke `plugin-semver` for version-bump
discipline"). It fires only if the model (a) runs `/code-review`, (b) reaches the Claude Code config
domain, and (c) remembers to invoke the skill. There is no mechanical backstop.

**Why now.** The safeguard has leaked repeatedly. Git audit of merged history:

| Plugin | Current version | Version last bumped | Files changed since, un-bumped |
|---|---|---|---|
| skill-management | 2.3.2 | #401 | #402 (`_lib.sh` hook behavior), #418 (skill-review SKILL.md) |
| claude-hook-review | 1.1.1 | #253 | #355 (SKILL.md rewrite, +41/−11) |
| plugin-semver | 1.0.1 | #356 | — (in sync) |
| lovable-cloud | 3.2.1 | in sync | — |

Three version-less behavior changes across two plugins. Systemic, not a one-off.

**Root cause (error-mode / root-cause lens).** A mechanical, diff-derivable invariant was delegated
to model discretion. The error mode is *silent under-versioning*: no signal at commit time,
discovered only by manual audit. **Fix class:** convert the discretionary prose rule into a
mechanical commit-time gate, as the sibling review gates back their skills.

**Intended outcome.** A `git commit` that stages any change under a plugin directory is blocked
unless that plugin's `version` has been raised on the branch. Magnitude (patch/minor/major) stays
human/skill judgment — the hook enforces only that a bump *exists* and *increases*.

## Approach

### The right primitive — structural check, not a marker gate

Whether a version bumped is **deterministically computable from the diff** — unlike code/skill
*review*, a subjective judgment that is the only reason those gates need a marker (`marker.sh` write
+ `permissions.allow` entry + session keying + anti-forgery). This hook needs none of that. It
belongs to the `guard-settings-session-keys.sh` class: inspect staged content, deny structurally.

*Lighter-alternatives check:* no marker file, no `marker.sh` invocation, no `permissions.allow`
rule, no active-bypass — none needed because the invariant is read directly off the diff.

### Plugin detection — walk up to the nearest `.claude-plugin/plugin.json`

**(Revised per review — was a `plugins/*/` glob.)** For each changed path, ascend its ancestor
directories; a directory is a **plugin root** if `.claude-plugin/plugin.json` exists there in the
BASE tree or the index (`git cat-file -e`, so added/removed plugins resolve). This matches
plugin-semver's own definition of a plugin ("tree contains `.claude-plugin/plugin.json`") and is
**layout-agnostic** — the hook ships with the plugin to arbitrary consumer repos whose plugins need
not live under `plugins/`. A hardcoded `plugins/*/` glob would silently allow un-bumped changes in
any repo with a different layout — the exact silent-under-versioning failure the hook exists to
prevent. Changed files with no plugin-root ancestor (a stray `plugins/README.md`, non-plugin code)
are ignored.

### Comparison baseline — vs. merge-base with the default branch

Compare each affected plugin's version at the **index** against its version at
**`merge-base(HEAD, origin/<default>)`**, not against HEAD, so **one** bump anywhere on the branch
satisfies the gate (per-commit-vs-HEAD would force a bump on every iterative commit — rejected).

`BASE` resolution (reusing `check-branch-divergence.sh`'s `git symbolic-ref refs/remotes/origin/HEAD`
pattern):
1. `origin/HEAD` resolvable → `BASE = git merge-base HEAD origin/<default>`.
2. Else a local `main`/`master` ref exists and differs from HEAD → merge-base against it.
3. Else → `BASE = HEAD` (degraded per-commit mode).

**Known limitation (documented per review):** `origin/HEAD` is a symref set only by `clone` /
explicit `remote set-head`; repos lacking it fall to step 2, and repos with neither fall to step 3.
In degraded mode, an iterative branch that already bumped in an earlier commit is **falsely denied**
on a later plugin-touching commit (index version == HEAD version). Step 2 (local default ref) covers
the common no-`origin/HEAD` case; the residual degraded-mode false-denial is stated as a known
limitation in the hook's header comment, not silently shipped. No network needed — merge-base uses
the local ref.

### Detection logic

1. Gate on `git commit` — both `hooks.json` `if: Bash(git commit *)` **and** an internal
   `grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'` (defense in depth per CLAUDE.md "hooks filter
   their own input"). Guard `TOOL_NAME != Bash → exit 0`; not in a repo → exit 0. `set -uo pipefail`
   deliberately (interacts with the `git show` handling below).
2. Affected plugin roots = walk-up (above) over
   `git diff --cached --name-only "$BASE" -- .` (all branch commits + staged vs BASE).
   **Any file counts** (confirmed) — REFERENCES.md and plugin-local `tests/` included; no exclusion
   list. Over-approximates toward a cheap spurious patch bump, away from the costly missed bump.
3. For each affected plugin root `r`:
   - Read each side into a variable and **branch on `git show`'s own exit** (not the pipe's — a
     `… | jq` pipe captures jq's exit, masking a missing file):
     `git show "$BASE:$r/.claude-plugin/plugin.json"` and `git show ":$r/.claude-plugin/plugin.json"`.
   - BASE `show` fails (new plugin) → allow. Index `show` fails (plugin removed) → allow.
   - Parse `version` via jq **fail-closed**: if the staged `plugin.json` is unparseable or has no
     `version` key (jq errors or yields `null`/empty) → **deny** ("can't verify a bump"), not allow.
   - Require **strict increase** via a portable dotted-numeric compare (a `version_strictly_greater`
     helper splitting on `.` and comparing fields numerically) — **not `sort -V`**, a GNU-only
     extension absent on stock BSD/macOS `sort` (same portability class the repo already codes around
     in `_lib` and `check-branch-divergence.sh`). Deny if `idx_ver` is not strictly greater than
     `base_ver`, or if either side is non-numeric/malformed (fail-closed).
4. Deny message names the plugin root, current version, and `plugin.json` path, pointing at the
   `plugin-semver` skill for magnitude.

### Home, wiring & activation

- Hook: `plugins/plugin-semver/hooks/require-plugin-version-bump.sh` — co-located with the skill it
  enforces, mirroring `require-skill-review.sh` in the skill-management plugin. Ships enforcement to
  anyone who installs plugin-semver at project scope.
- New `plugins/plugin-semver/hooks/hooks.json` — single PreToolUse/Bash entry, `if:
  Bash(git commit *)`, `statusMessage`, invoked via `${CLAUDE_PLUGIN_ROOT}` (mirror
  skill-management's `hooks.json`, minus its SessionStart venv hook). `plugin.json` needs no `hooks`
  key — hooks auto-discover from `hooks/hooks.json`.
- **`_lib.sh` is mandatory, not optional (corrected per review).** `test_hook_alignment.py` requires
  every `require-*.sh` gate to define `emit_deny()` **and** source `_lib.sh` in that order
  (`test_emit_deny_defined_before_lib_source`) and to deny on a missing `_lib.sh`
  (`test_missing_lib_sh_denied`) — inlining the parse would fail both. A project-scope install won't
  have the stowed `~/.claude/hooks/_lib.sh`, so the plugin must be self-contained. **Ship a
  co-located `plugins/plugin-semver/hooks/_lib.sh` copy** (accepted plugin-self-containment
  duplication, as skill-management already does) and add the byte-identical **drift guard** test
  (the analog of `test_require_skill_review.py::test_plugin_lib_sh_matches_stowed_lib_sh`).
- **Activation boundary (corrected — the dogfood claim was false).** Marketplace plugins install as
  **version-pinned cached copies** at `~/.claude/plugins/cache/claude-config/<name>/<version>/`, not
  live references to the working tree. The active `plugin-semver/1.0.1/` cache has no `hooks/` dir;
  hooks load from the cache (skill-management's gate runs from its `2.3.2/hooks/`). Consequences:
  - The PR that adds this hook is **not** gated by it, and `git pull` alone does **not** activate it
    (the "live on pull" note applies only to stowed `claude/.claude/**`, not cached `plugins/**`).
  - Activation is an explicit plugin update materializing `1.1.0/` into the cache — run `install.sh`
    or `claude/.claude/scripts/update-claude-config-plugins.sh` after merge. From the next session
    on it gates exactly as skill-management's cached gate does. State this activation step in the PR
    description; the first *protected* commits are future ones (the goal is preventing future
    misses).
- Adding a hook to plugin-semver is a backward-compatible addition → bump **plugin-semver 1.0.1 →
  1.1.0** in this PR.

### Existing-debt remediation (distinct commit in this PR — confirmed)

- **skill-management 2.3.2 → 2.4.0** (minor: #402 added a hook behavior — backward-compatible
  addition — dominates #418's patch-level skill sharpening).
- **claude-hook-review 1.1.1 → 2.0.0** (**major** — corrected per reviewer input). #355's SKILL.md
  rewrite (59d27e7) made the canonical `_lib_parse_tool_input_or_deny` pattern and a `# hook-class:`
  header **mandatory** on every gate hook, explicitly superseding the old inline `JQ_EXIT` pattern
  ("New gate hooks must follow the canonical pattern in Section 4… rather than reimplementing this
  inline form"). Any hook that passed review under the prior SKILL.md now fails under the new one —
  that is plugin-semver's own definition of major ("an existing skill's guidance changes the outcome
  for anyone relying on the prior version"). Implementer: confirm by reading 59d27e7; magnitude
  rationale goes in the commit message.

### Documentation

- README "Workflow" hooks table — add a one-line row for the new commit-gate (one-liner per "match
  doc granularity to surface"; `test_doc_counts.py` counts reviewer agents + skillOverrides only,
  not hook rows, so no count assertion to satisfy).
- Repo-root `CLAUDE.md` "Review pipeline" — note plugin-file changes are now hook-enforced for the
  version bump, alongside the SKILL.md→skill-review (hook-enforced) / agent→agent-review
  (not-enforced) lines; include the activation-via-plugin-update caveat.
- *Optional, minimal:* append "(hook-enforced)" to `code-review/SKILL.md:177`. Editing that SKILL.md
  triggers `/skill-review` (stowed skill, no version bump).

## Critical files

- **Create** `plugins/plugin-semver/hooks/require-plugin-version-bump.sh` — reuse the skeleton of
  `plugins/skill-management/hooks/require-skill-review.sh` (define `emit_deny` → source `_lib.sh` →
  `_lib_parse_tool_input_or_deny` → git-commit grep gate → `TOOL_NAME` guard → `set -uo pipefail`),
  replacing marker logic with the walk-up detection + version-diff check above. `# hook-class: gate`
  on line 2; filename `require-*.sh` auto-enrolls it in `test_hook_alignment.py` Layer-2 tests.
- **Create** `plugins/plugin-semver/hooks/hooks.json` — mirror
  `plugins/skill-management/hooks/hooks.json`.
- **Create** `plugins/plugin-semver/hooks/_lib.sh` — co-located byte-identical copy of the stowed
  `_lib.sh` (mandatory; see wiring).
- **Modify** `plugins/plugin-semver/.claude-plugin/plugin.json` — version → 1.1.0.
- **Modify** `plugins/skill-management/.claude-plugin/plugin.json` — version → 2.4.0 *(debt commit)*.
- **Modify** `plugins/claude-hook-review/.claude-plugin/plugin.json` — version → 2.0.0 *(debt
  commit; confirm against 59d27e7)*.
- **Create** `claude/.claude/hooks/tests/test_require_plugin_version_bump.py` — mirror
  `test_require_skill_review.py`; import from `claude/.claude/tests/helpers.py`; compute the plugin
  hook path relative to `HOOKS_DIR` as the sibling test does; reuse the `bare_remote` /
  `feature_clone` fixture model from `test_check_branch_divergence.py` for merge-base cases.
- **Modify** `README.md` and repo-root `CLAUDE.md` — doc rows above.
- **Reuse:** `check-branch-divergence.sh` default-branch resolution + its `bare_remote`/
  `feature_clone` test fixtures; `_lib.sh` parse helpers; `require-skill-review.sh` skeleton +
  `test_plugin_lib_sh_matches_stowed_lib_sh` drift-guard pattern; `guard-settings-session-keys.sh`
  as the structural-deny reference shape.

## Verification

**Unit tests** (`test_require_plugin_version_bump.py`) — the matrix must exercise **all three BASE
branches and the fail-closed paths**, not just the happy case:

- *Bump semantics (degraded BASE=HEAD is fine for these):* plugin file changed, no bump → **deny**;
  version raised → **allow**; version lowered → **deny**; version equal → **deny**; two plugins
  changed, only one bumped → **deny**; REFERENCES.md-only change, no bump → **deny** (confirms "any
  file counts"); pure version-only bump commit → **allow**.
- *Fail-closed inputs (added per review):* staged `plugin.json` malformed/unparseable → **deny**;
  present but **no `version` key** (jq → `null`) → **deny**; non-semver value (`v1.0`, `1.0`,
  prerelease) → **deny** (portable compare rejects non-numeric).
- *Plugin detection (added per review):* new plugin added → **allow**; plugin dir deleted →
  **allow**; stray `plugins/README.md` (no plugin-root ancestor) → **allow**; nested path
  (`…/foo/skills/bar/SKILL.md`) maps to plugin `foo`.
- *Command/tool gates:* non-plugin file → **allow**; non-commit command → **allow**; non-Bash tool →
  **allow**; outside repo → **allow**; empty staged diff → **allow**; `git commit --amend` with a
  staged plugin change → assert the intended decision (added per review); chained `git add … &&
  git commit` with/without bump → deny/allow.
- *Merge-base resolution (the load-bearing case — must be discriminating):* build on a
  `feature_clone`/`bare_remote` fixture with a resolvable `origin/HEAD`; bump in an earlier branch
  commit, later commit edits again with no further bump → **allow**. Structure it so degraded
  BASE=HEAD would yield the **opposite** decision (deny), or add an explicit probe asserting BASE
  resolved to the merge-base — otherwise the merge-base code ships untested behind the degraded
  fallback. Add one case per resolution branch: `origin/HEAD` present; local `main` differs from
  HEAD; neither → degraded.

**Alignment:** `test_hook_alignment.py` **auto-discovers** the new hook via
`(_REPO_ROOT/"plugins").glob("*/hooks")` + `*.sh` (there is no registry to edit — corrected
phrasing). The `require-*.sh` filename imposes: `# hook-class: gate` header, `emit_deny` defined
before the `_lib.sh` source line, and Layer-2 deny behavior on malformed JSON / empty stdin /
non-object `.tool_input` / missing `_lib.sh`. Add the `_lib.sh` byte-identical drift-guard test.

**Run** (from the worktree): `../../../.venv/bin/pytest claude/.claude/` and
`../../../.venv/bin/ruff check claude/.claude/`.

**Manual smoke:** pipe a crafted PreToolUse JSON (`git commit -m x`) into the hook with a staged
plugin edit and no bump → deny; add the bump → allow.

**Post-merge activation:** run `install.sh` / `update-claude-config-plugins.sh` to materialize
plugin-semver `1.1.0` into the plugin cache; confirm a subsequent session's un-bumped plugin commit
is denied. The gate does not protect this PR itself (see activation boundary).

## Out of scope

- **CI-level enforcement** of the invariant (a pytest failing when any plugin changed vs main
  without a bump). PreToolUse hooks only gate Claude sessions, not raw `git` / `--no-verify` — same
  limitation every sibling gate has, now compounded by the plugin-cache activation boundary. Parity
  with existing gates: hook-only now; note as possible future defense-in-depth.
- **marketplace.json edits** — no `.claude-plugin/plugin.json` ancestor, so the walk-up finds no
  plugin root; the hook does not gate them (the plugin-semver skill still advises on them).
- Magnitude *validation* (is patch vs minor correct?) — left to the `plugin-semver` skill and human
  review; the hook enforces existence + strict increase only.
