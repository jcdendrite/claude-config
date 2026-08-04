# Gitignore Claude Code runtime state under claude/.claude/

## Context

`git status -uall` on the main tree currently shows ~20 untracked, not-yet-gitignored
paths under `claude/.claude/` (plus a stray `.DS_Store` at repo root). Because
`claude/.claude/` is a stow package that installs to `~/.claude/`, and stow
directory-folds `~/.claude` into a symlink of the package, every runtime write Claude
Code's CLI or this repo's own hooks/skills make against `~/.claude/...` lands
*physically inside the git-tracked tree*. None of this is committed today (confirmed
via `git ls-files` — none of these paths have ever been tracked), but nothing stops an
accidental `git add -A` from picking it up, and some of it (`history.jsonl`,
`.claude.json`, `projects/`) is genuinely sensitive: raw prompt history and full
session transcripts across every project this user has worked in, not just this repo.
The goal is to extend `.gitignore` to cover this state so it stops showing as noise in
`git status` and can't be accidentally committed.

## Approach

Add two new blocks to the existing root `.gitignore`, following its established
pattern (anchored `claude/.claude/...` paths, trailing `/` for directories, a comment
naming the writer and why it's excluded — see the existing "Review-gate marker state"
block for the model), plus one bare line for OS noise:

1. **`.DS_Store`** — unanchored, one line. macOS Finder metadata, unrelated to Claude
   Code; a single unanchored pattern covers all three observed locations (repo root,
   `claude/.claude/`, `claude/.claude/skills/`) without needing per-directory entries.
   Belongs with generic top-of-file entries (`__pycache__/`, `.pytest_cache/`,
   `.venv/`), not the Claude-specific blocks below.

2. **Claude Code CLI-managed state** (one comment block, one writer: the CLI itself,
   not this repo's hooks) — `claude/.claude/.claude.json`, `claude/.claude/backups/`,
   `claude/.claude/history.jsonl`, `claude/.claude/projects/`,
   `claude/.claude/sessions/`, `claude/.claude/shell-snapshots/`,
   `claude/.claude/file-history/`, `claude/.claude/paste-cache/`,
   `claude/.claude/plugins/`, `claude/.claude/tasks/`, `claude/.claude/telemetry/`,
   `claude/.claude/.last-cleanup`, `claude/.claude/.last-update-result.json`. The
   comment names the CLI as writer at the block level (matching the confirmed subset
   — `.claude.json`, `backups/`, `history.jsonl`, `projects/`, `sessions/`,
   `shell-snapshots/`, `tasks/`, `.last-cleanup`, `.last-update-result.json`, per the
   citations below) without asserting a specific mechanism for `paste-cache/`,
   `plugins/`, or `telemetry/` — those three are grouped in by directory-naming
   convention (cache/log-shaped, not package content) rather than a confirmed writer;
   see ledger row below.
   Cross-checked against `git ls-files` — none of these anchored paths collide with
   tracked content (in particular, the repo-root `plugins/` marketplace directory is
   real tracked content, but the pattern here is anchored to `claude/.claude/plugins/`
   specifically, so it does not touch it).

3. **This repo's own hook/skill-written state** (second comment block, writers are
   this repo's hooks/skills, mirroring the existing marker-state block) —
   `claude/.claude/.handoff-nudge-fired.d/`, `claude/.claude/.handoff-nudge.log`
   (written by `nudge-handoff-near-context-cap.sh`, cleaned by
   `cleanup-handoff-nudge-marker.sh`), `claude/.claude/.plan-review-routing-read.d/`
   (written by `log-routing-read.sh`, gated by `require-routing-read.sh`),
   `claude/.claude/.worktree-anchor-nudge.d/` (written by `nudge-worktree-anchor.sh`,
   cleaned by `cleanup-worktree-anchor-nudge-marker.sh`), `claude/.claude/briefs/`
   (written by the `/brief` skill), `claude/.claude/handoffs/` (written by the
   `/handoff` skill), `claude/.claude/error-mode-reports/` (written by
   `/error-mode-analysis` — already documented elsewhere in this repo as
   project-identifying content that must never reach a public repo), and
   `claude/.claude/cache/` (ad hoc cached command output — no writer found in this
   repo's hooks/skills; treated as per-machine scratch space regardless of origin).

**Alternative considered:** one flat undifferentiated list. Rejected — the existing
`.gitignore` consistently groups entries by *why* they're excluded and cites the
writer per group; a flat list would break that convention and make future additions
harder to place correctly.

**Assumption ledger**

- Root problem: untracked, sensitive, per-machine Claude Code runtime state is
  visible as `git status` noise and has no gitignore protection against accidental
  commit. `anchors: root`
- `claude/.claude/plugins/` (CLI plugin cache) does not collide with the tracked
  repo-root `plugins/` marketplace directory because the gitignore pattern is
  anchored to the full `claude/.claude/` prefix. `[verified: git ls-files shows no
  tracked paths under claude/.claude/plugins/, claude/.claude/tasks/,
  claude/.claude/cache/, claude/.claude/briefs/, claude/.claude/sessions/,
  claude/.claude/projects/]` anchors: row2,row3
- Every path in the two new blocks is CLI- or hook/skill-written runtime state, not
  something that should ship with the package. `[verified: general-purpose subagent
  cross-referenced each path against claude/.claude/hooks/*.sh,
  claude/.claude/skills/**/SKILL.md, docs/design-decisions.md:223 (which explicitly
  lists projects/, sessions/, .credentials.json, ide/, daemon/ as CLI-managed), and
  claude/.claude/cache/changelog.md (locally cached Claude Code changelog confirming
  backups/, shell-snapshots/, tasks/ retention behavior)]` anchors: row2,row3
- `claude/.claude/cache/` has no identified writer in this repo's own hooks/skills —
  treated as per-machine scratch space to ignore regardless of origin, since its
  observed contents (`changelog.md`, `my-closed-issues.json`) are plausibly
  GitHub-issue-derived and could reference private repos. `[unverified — writer
  unidentified]` anchors: row3
- `claude/.claude/paste-cache/`, `claude/.claude/plugins/`, and
  `claude/.claude/telemetry/` are grouped with the CLI-managed block by
  directory-naming convention (cache/log-shaped, not package content), but no direct
  citation (changelog or hook grep) confirms the CLI as writer the way it does for
  `backups/`, `shell-snapshots/`, and `tasks/`. The `.gitignore` comment for these
  three must not assert a specific writer it hasn't confirmed. `[unverified — writer
  inferred, not confirmed; flagged by /plan-review]` anchors: row2
- No `CLAUDE_CONFIG_DIR`-style redirection changes this for the default profile —
  `install.sh` runs a single `stow --adopt -t "$HOME" claude`, so all of this lands in
  one real `~/.claude` for this machine's default profile.
  `[verified: install.sh, README.md:184, docs/scripts.md:79]` anchors: root
- `claude/.claude/.claude.json.lock/` is a plausible sibling of `.claude.json` (same
  CLI-managed family) but was not observed in the current `git status -uall` output,
  so it is deliberately left out per this repo's scope-discipline rule against adding
  unobserved, speculative entries. `[engineer-verified: none — this is my own
  scope call, flagged here for the user to confirm]` anchors: row2

## Critical files

- `.gitignore` (repo root) — add the three items above. No other file needs to
  change; this is a pure gitignore addition, nothing to build or wire up.

## Verification

- `git status --porcelain=v1 -uall` (from the main tree, read-only) before and after:
  confirm every currently-listed untracked path under `claude/.claude/` disappears
  from the output, and confirm the repo-root `plugins/` directory and
  `claude/.claude/skills/` real skill content are still tracked/unaffected (`git
  ls-files -- plugins/ claude/.claude/skills/` unchanged).
  Note: `git status` inside *this* worktree will show none of this state (it's a
  fresh checkout with no runtime history yet) — verification must run against the
  main tree at `/Users/jared/MyCode/claude-config`.
- `git check-ignore -v claude/.claude/history.jsonl claude/.claude/.claude.json
  claude/.claude/projects/ claude/.claude/plugins/` — confirm each resolves to the
  newly-added `.gitignore` line, not a false match from an unrelated existing rule.
- Confirm `plugins/` (repo-root marketplace) is NOT matched by
  `git check-ignore -v plugins/claude-hook-review/.claude-plugin/plugin.json` (should
  print nothing / non-zero exit, i.e. not ignored).

## Out of scope

- No `git rm --cached` needed — `git ls-files` confirms none of these paths were ever
  committed, so this is a pure prevention fix, not a history cleanup.
- `claude/.claude/.claude.json.lock/` — left out per the assumption ledger; add later
  if it's actually observed.
- Deciding whether `claude/.claude/cache/` should exist at all, or what's writing it —
  flagged as unverified; out of scope for a gitignore-only change.
