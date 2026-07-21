# Plan: Document machine-level worktree gitignore (global git excludes)

## Context

**Goal: close the gap where machine-level worktree enforcement produces untracked `.claude/worktrees/` dirs in every repo, but only the *per-repo* activation path documents a matching gitignore step.**

Worktree enforcement can be activated two ways (README §"Worktree enforcement"):

- **Per-repo** (`.claude/worktree-required` committed): README already appends
  `.claude/worktrees/` to *that repo's* `.gitignore`. ✅ Covered.
- **Machine-level** (`touch ~/.claude/worktree-required`, README:272-286): has
  **no** gitignore step — yet this is the mode that makes `.claude/worktrees/`
  appear in *every* repo you open (design-decisions.md:35 cites 25+ repos as the
  reason machine-level exists). None of those repos get the ignore line unless
  hand-edited, so a `git add -A` in any of them can sweep live worktrees into a
  commit.

A `.gitignore` is repo-local — git only reads `.gitignore` inside the repo being
operated on, plus `.git/info/exclude`, plus the global `core.excludesFile`. There
is no "ambient" or stowed global `.gitignore`. The **only** machine-wide mechanism
is git's global excludes file, `~/.config/git/ignore`, which on this machine
already ignores the sibling artifact `**/.claude/settings.local.json` — the same
class of per-machine `.claude/` state. `.claude/worktrees/` belongs right beside it.

**Intended outcome:** the README machine-level activation subsection documents a
one-liner that adds `**/.claude/worktrees/` to `~/.config/git/ignore`, so opting
into machine-level enforcement always includes the ignore. Plus a local (non-repo)
action to add that line on this machine now.

## Approach

**Documentation, not automation.** `~/.config/git/ignore` is the git equivalent
of `.bashrc`: a personally-owned dotfile outside the stow package's `~/.claude/`
symlink namespace that governs every repo and every git tool. The installer is
symlink-only and cleanly reversible via `stow -D`; appending a line to a
hand-curated git-wide config file is not cleanly reversible and would write a rule
for a feature the user may never have opted into. This mirrors the existing
per-repo path, which documents a `.gitignore` append as a command rather than
performing it in `install.sh`.

*Alternatives set aside:* (a) `install.sh` idempotently appends the line — rejected
for the `.bashrc`-boundary and non-reversibility reasons above; (b) a stowed
`~/.claude/.gitignore` — rejected because git does not honor an ambient
`.gitignore` from another directory, so it would be inert.

### Change

Edit **README.md** §"Activate for all your repos (machine-level)" (~272-286).
After the `touch ~/.claude/worktree-required` step, add a short paragraph +
fenced command explaining that machine-level enforcement creates
`.claude/worktrees/` in every repo, so it should be paired with a global
git-excludes entry, and give the idempotent one-liner. **Derive the excludes path
rather than hardcoding `~/.config/git/ignore`** — that path is git's default only
when `core.excludesFile` is unset; a user who has set `core.excludesFile`
elsewhere (e.g. `~/.gitignore_global`) would otherwise write to a file git never
reads, a silent no-op that leaves the footgun open. Use:

```bash
f=$(git config --get core.excludesFile || echo ~/.config/git/ignore)
mkdir -p "$(dirname "$f")"
grep -qxF '**/.claude/worktrees/' "$f" 2>/dev/null \
  || echo '**/.claude/worktrees/' >> "$f"
```

Match the glob style already present in `~/.config/git/ignore`
(`**/.claude/settings.local.json`) — use `**/.claude/worktrees/`, not a
root-anchored form, since it must match at any repo depth. The `mkdir -p` line
guards a fresh machine that has no `~/.config/git/` directory yet.

Keep the prose at README altitude (one short paragraph + the command), matching the
per-repo subsection's tone. No PR-defined terminology; the text must stand alone.

### Local action (not a repo change)

On this machine, run the same derive-path one-liner to add
`**/.claude/worktrees/` to the global excludes file. `core.excludesFile` is unset
here (verified), so the target resolves to `~/.config/git/ignore`, which already
holds the sibling `**/.claude/settings.local.json`. This is a personal-config
edit, done outside the PR.

## Critical files

- **`README.md`** — machine-level activation subsection (~272-286). Only file
  changed in the PR. Reuse the exact command shape from the per-repo subsection
  (README:248) for consistency.

## Verification

- `sed -n '272,300p' README.md` — confirm the new step reads cleanly and the
  command is copy-pasteable.
- Dry-run the documented one-liner in a scratch `HOME`:
  `HOME=$(mktemp -d) bash -c 'mkdir -p "$HOME/.config/git"; grep -qxF ... || echo ... >> "$HOME/.config/git/ignore"; cat "$HOME/.config/git/ignore"'`
  — confirm idempotency (running twice yields one line) and correct content.
- In a throwaway repo under a machine-level-enforced `HOME`, create
  `.claude/worktrees/x` and confirm `git status --porcelain` does not list it once
  the global excludes line is present.
- Docs-only change: no pytest/ruff impact, but run `../../../.venv/bin/ruff check`
  from the worktree if any script is touched (none expected).

## Out of scope

- `install.sh` changes (rejected — see Approach).
- The per-repo activation subsection (already correct).
- `~/.config/git/ignore` on other machines / other stow users — closed by the
  documented one-liner, which each user runs when they opt into machine-level
  enforcement.
