# Plan: machine-level default for worktree enforcement

## Context

Worktree enforcement today is **per-repo opt-in**: the two gate hooks
(`require-worktree-for-git-writes.sh`, `require-worktree-for-file-writes.sh`)
enforce only when `.claude/worktree-required` exists at the repo root. That
made sense when the friction was "one repo, one switch." It breaks down now
that the user runs Claude against **25+ client repos** across two engagements:
activation is per-repo, so every new clone is another manual marker drop (and
a forgotten one is a silent concurrent-session race waiting to happen).

The real defect is the **granularity of activation**, not the shipped default.
Flipping the shipped global default to on would change behavior for *every*
downstream stow user — including the solo/single-repo case the original design
(`docs/design-decisions.md` §7) deliberately protected — and would block their
drive-by edits to unrelated repos. The lighter primitive that solves the actual
pain is a **machine-level activation marker**: one file on the user's machine
flips *their* default to on, opt-out per repo, with the shipped default and all
downstream users unchanged.

**Primary-source grounding** (verified, this session):
- Anthropic, `code.claude.com/docs/en/worktrees.md` & `/agents.md`: worktrees are
  the recommended file-isolation mechanism for parallel sessions — *"Use them for
  sessions you run yourself"* — and the desktop app **auto-creates a worktree for
  every new session**. Strongly encouraged; lever is "do sessions touch the same files."
- Git, `git-scm.com/docs/git-worktree`: each worktree has its own `HEAD`/`index`;
  git refuses to check the same branch into two worktrees. Git documents the
  isolation mechanism but does not explicitly address process-level concurrency
  safety — so "worktrees are the git-sanctioned way to isolate concurrent work" is
  a well-grounded inference, not a verbatim assertion.

Outcome: the user sets one marker once and all their repos enforce; the shipped
default stays off; any repo can be exempted with a per-repo opt-out marker.

## Design

### Activation semantics (the core change)

Replace the single per-repo check in both hooks with three markers:

| Marker | Scope | Meaning |
|---|---|---|
| `<repo>/.claude/worktree-required` | repo (existing) | Hard requirement, travels via `git pull`. Cannot be locally opted out. |
| `~/.claude/worktree-required` | machine (new) | Personal default-on for every repo on this machine. |
| `<repo>/.claude/worktree-optout` | repo (new) | Exempts this repo from the **machine-level** default only. |

Gate predicate:

```
enforce = repo_required OR (user_required AND NOT repo_optout)
```

**Deviation from the option preview, flagged for review.** The preview showed
`(repo_marker OR user_marker) AND NOT repo_optout`, which would let a local
opt-out defeat a *committed, team-shared* requirement. That breaks the
team-enforcement guarantee — a committed `.claude/worktree-required` is a shared
decision in source control and an individual should not be able to silently
escape it with an untracked file. The refined predicate makes opt-out modulate
only the machine-level default. (This repo itself commits
`.claude/worktree-required`, so it stays enforced regardless of any opt-out —
the intended behavior.) If the user prefers opt-out to override everything, it
is a one-line change to the helper.

### Where the logic lives — shared helper in `_lib.sh`

Both hooks source `claude/.claude/hooks/_lib.sh` and currently duplicate the
single-line gate. Per "audit structural siblings" + DRY, add **one** helper so
the tri-marker logic has a single home:

```bash
# In _lib.sh — true (0) when worktree discipline is active for $1 (repo root).
_lib_worktree_enforcement_active() {
  local repo_root="$1"
  [ -n "$repo_root" ] || return 1                                    # degenerate: no repo, never enforce
  [ -f "$repo_root/.claude/worktree-required" ] && return 0          # committed requirement
  # Machine default, minus opt-out. The `[ -n "$home_norm" ]` guard is
  # load-bearing: an empty/unset $HOME would make the test below probe
  # `/.claude/worktree-required` and a stray root file could force-enforce
  # every repo. Mirrors require-worktree-for-file-writes.sh lines 71-72.
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] \
    && [ -f "$home_norm/.claude/worktree-required" ] \
    && [ ! -f "$repo_root/.claude/worktree-optout" ] && return 0
  return 1
}
```

Two foundational guards dissolve the degenerate-input flips both plan reviewers
flagged: `[ -n "$repo_root" ]` (machine marker must never fire when not in a
repo — the SDET's outside-repo flip) and `[ -n "$home_norm" ]` + trailing-slash
normalization (the platform reviewer's empty-`$HOME` false-positive). `$HOME` is
available in both hooks (both keep it; only GIT_* are unset). The file-writes
hook already exempts `$HOME/.claude/*` (verified, lines 71-79), so creating/editing
the machine marker via the Write tool is never blocked.

**Preserve the existing early returns.** Both hooks already `exit 0` on empty
`$REPO_ROOT` *before* the gate line (git-writes 131-133, file-writes 92). The
implementer replaces only the gate line with
`_lib_worktree_enforcement_active "$REPO_ROOT" || exit 0` and must keep that
preceding early return — the helper's `[ -n "$repo_root" ]` guard is
defense-in-depth, not a license to drop it.

### Files to change

**Hook logic (core):**
- `claude/.claude/hooks/_lib.sh` — add `_lib_worktree_enforcement_active`.
- `claude/.claude/hooks/require-worktree-for-git-writes.sh:135-138` — replace the
  `[ ! -f "$REPO_ROOT/.claude/worktree-required" ]` gate with
  `_lib_worktree_enforcement_active "$REPO_ROOT" || exit 0`. Update header comment (lines 3-5).
- `claude/.claude/hooks/require-worktree-for-file-writes.sh:94-95` — same replacement.
  Update header comment (lines 3-6).
- Optional (I1, FYI): mirror the file-writes hook's "HOME is trusted from the OS
  session" rationale comment (lines 26-29) into the git-writes hook, so the
  HOME-is-trusted reasoning isn't single-sourced across the two sibling gates.

**Deny-message strings** (currently assert "(.claude/worktree-required is
committed)", now inaccurate when activation is machine-level):
- git-writes hook lines 147, 224, 229; file-writes hook lines 104, 113. Generalize
  to name both activation sources and point to the opt-out escape, e.g.: *"Worktree
  discipline is active for this repo (repo-level `.claude/worktree-required` or
  your machine-level `~/.claude/worktree-required`). To exempt this repo, add
  `.claude/worktree-optout`."* Keep concise — these strings are already long.
- **Preserve asserted interpolations and appends.** Existing tests assert on
  substrings the rewrite must NOT drop: file-writes `$REL_PATH` (`"src/main.sh"`)
  and `$TOOL_NAME` (`"Write"`) interpolation (lines 104, 113); git-writes parse-fail
  text `"could not determine the git subcommand"` (line 224) and the trailing
  `$(cwd_anchor_note_if_chained …)$(git_C_note_if_present …)` appends at 224/229
  that carry `"session-persisted"`, `"Anchor cwd"`, `"-C path"`. Change only the
  `(.claude/worktree-required is committed)` clause; leave interpolations and
  note-appends intact.

**Tests** (`claude/.claude/hooks/tests/`):
- `conftest.py` — add fixtures: `user_marker_home` (writes `~/.claude/worktree-required`
  into the sandboxed home, building on the existing `isolated_home`; **assert the
  marker file exists after writing it** so a wrong-path typo can't yield a silently-inert
  marker that passes enforcement tests for the wrong reason), and a repo fixture
  carrying `.claude/worktree-optout`.
- **Hermeticity (load-bearing — confirmed by SDET review):** the existing
  no-enforcement tests currently take *no* `$HOME` fixture, so they inherit the
  developer's real `~/.claude` and will FLIP to deny once the machine marker exists.
  Wire `isolated_home` (a sandboxed `$HOME` without the machine marker) into exactly:
  `test_no_sentinel_allows_commit`, `test_no_sentinel_allows_push`,
  `test_outside_git_repo_allowed`, `test_sentinel_as_directory_treated_as_unopted`
  (git-writes); `test_no_sentinel_allows_edit`, `test_no_sentinel_allows_write`,
  `test_non_git_path_allows_edit` (file-writes). Pin the invariant: "absent both
  markers, allow — independent of the developer's real `~/.claude`."
- New cases, the full truth table parametrized across **both** hooks:
  (a) machine-marker only → enforce; (b) machine-marker + opt-out → allow;
  (c) repo-marker + opt-out → still enforce (committed wins);
  (d) neither → allow; **(e) opt-out present but NO machine- and NO repo-marker →
  allow** (proves opt-out is an inert modulator, not a trigger — guards against a
  future inversion of the opt-out check);
  **(f) machine-marker active + command/file outside any git repo → allow** (the
  outside-repo path is now reachable with the marker set; proves the empty-`$REPO_ROOT`
  early return + helper guard hold).
- file-writes specifically: a case proving that under the **machine** marker, a write
  to a main-tree project file is denied while a write to `~/.claude/foo` stays allowed
  (the `$HOME/.claude/*` exemption must hold for machine-level activation, not only
  repo-level).

**Docs (single source of truth = `docs/design-decisions.md` §7; others defer):**
- `docs/design-decisions.md` §7 — extend (do not rewrite) to record the machine-level
  override and *why*: per-repo opt-in didn't scale to many-repo engagements; the
  shipped default stays off so downstream users are unaffected. This is the canonical record.
- `README.md` §"Worktree enforcement" (225, 229-265) — document the three markers;
  add an "Activate for all your repos (machine-level)" subsection (`touch ~/.claude/worktree-required`)
  and an "Exempt a repo" subsection (`.claude/worktree-optout`). Keep the existing
  per-repo recipe.
- `docs/hooks.md:17-18` — update the one-line descriptions ("opt-in per repo" →
  "opt-in per repo or per machine").
- `claude/.claude/CLAUDE.md:64` (Agent Briefing) — reword "worktree enforcement
  opt-in (`.claude/worktree-required` committed)" to acknowledge machine-level activation.
- The repo-root marker file `.claude/worktree-required`'s own header comment describes
  repo-level activation and stays accurate — no edit needed.
- `CONTRIBUTING.md` §"Worktrees" stays accurate (this repo enforces via the committed
  repo marker) — no edit needed; noted so it isn't mistaken for a missing-scope gap.
- `docs/case-studies/worktree-enforcement.md` — **preserve the existing "Why opt-in
  per-repo" analysis** (it is the record of reasoning at a point in time and remains
  valid for the *shipped* default). Add at most a one-line forward pointer to the
  machine-level override; do not rewrite the voice. (Axis-3 preserved-content care.)

**Explicitly NOT changed:**
- `install.sh` — must **not** create `~/.claude/worktree-required`; auto-creating it
  would force enforcement on every installer, defeating the off-by-default decision.
  Activation stays a deliberate manual `touch`.
- `claude/.claude/settings.json` — hooks already wired; no change.
- The repo must **not** add `claude/.claude/worktree-required` (that path stows to
  every user = the shipped-default flip we are avoiding). Optional defensive hardening:
  add `/claude/.claude/worktree-required` to `.gitignore` so a fat-fingered create in
  the package dir can't be committed.

### Stow-symlink safety check (verify before implementing)

The machine marker lives at `~/.claude/worktree-required`. Confirm `~/.claude` is a
**real directory** with selectively-symlinked files (it is — it holds local state:
`settings.local.json`, `projects/`, `todos/`, `private-projects.md`, `skill-test-cases/`),
not a stow directory-fold symlink to `claude/.claude/`. If it were a folded symlink,
`touch ~/.claude/worktree-required` would write through into the repo working tree and
stage it — the documented stow footgun. Given the coexisting local files, it is a real
directory; verify with `ls -ld ~/.claude` (expect `drwx`, not `lrwx`) at implementation time.

## Verification

1. **Unit suite** — from the worktree: `../../../.venv/bin/pytest claude/.claude/hooks/`
   (all existing + new cases green). Lint: `../../../.venv/bin/ruff check claude/.claude/`.
2. **Skill-review** — SKILL.md is not touched, but `/code-review` will dispatch
   hook review (`claude-hook-review`) for the hook edits; run it.
3. **Manual smoke test** (a throwaway git repo outside the stow tree, with a sandboxed
   `HOME` pointing at a temp dir so real state is untouched):
   - No markers → `git commit` allowed (baseline unchanged).
   - `touch $TMPHOME/.claude/worktree-required` → `git commit` from main tree **denied**;
     a `Write` to a main-tree file **denied**; both allowed inside a linked worktree.
   - Add `<repo>/.claude/worktree-optout` → both **allowed** again.
   - Add `<repo>/.claude/worktree-required` (committed) alongside the opt-out →
     **denied** again (committed requirement wins).
4. **On the user's real machine**, after merge: `touch ~/.claude/worktree-required`,
   then confirm a client repo denies a main-tree commit and that the unit suite still
   passes locally (proves the hermeticity audit held).
