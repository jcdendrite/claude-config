# Are the `worktree-required` / error-mode markers a gitignore flaw?

## Context

**Goal:** Answer a design question — is it a *code flaw* that the worktree
and error-mode-analysis opt-in flags are not listed in `.gitignore`? — and,
if a real (small) gap exists, scope the fix.

**Why now.** A separate session hit confusion: *"Both this repo and the iac
repo show an untracked `.claude/worktree-required` file, and there's also a
machine-level `~/.claude/worktree-required` already active. Is the untracked
file intentional new repo policy, or should I remove it?"* That question
reads as "maybe the marker files are mis-managed," so the user wants a
careful verdict before treating it as a defect.

**Verdict up front: No, it is not a code flaw.** The premise conflates two
different markers with two different scope models, and in both cases the
current gitignore state is correct by design.

**Action (user-approved):** ship the one small, already-designed consistency
hardening described at the end — two belt-and-suspenders `.gitignore` entries.
It closes a deferral the worktree design record explicitly named, mirrors an
existing three-file guard, and carries near-zero risk. It does **not** change
the verdict; the answer is "not a flaw" with or without it. The rest of this
file is the analysis that supports the verdict.

## Finding 1 — the two flags are architecturally different

| | `worktree-required` | error-mode-analysis enable flag |
|---|---|---|
| Real path(s) | `<repo>/.claude/worktree-required` **and** `~/.claude/worktree-required` | `~/.claude/.error-mode-nudge-enabled` **only** |
| Scope model | dual: committed repo sentinel **+** machine sentinel **+** per-repo opt-out | machine-level opt-in only; no repo-relative arm |
| Read by | `_lib.sh:205-219` (`_lib_worktree_enforcement_active`) | `nudge-error-mode-analysis.sh:75` |
| Meant to be committed? | the **repo** form yes (that is its purpose); the **machine** form no | never — lives outside any repo |

Note the user's term `error-analysis-enabled` does not exist in the repo. The
actual marker is `~/.claude/.error-mode-nudge-enabled`. Enable/disable is
documented as `touch`/`rm` of that home-dir path (`docs/error-mode-nudge.md:7-21`,
`CONTRIBUTING.md:23-27`, `README.md:160`).

## Finding 2 — error-mode marker: nothing to gitignore

`~/.claude/.error-mode-nudge-enabled` lives in the **home directory**, not
inside any project working tree. It can never appear in the iac repo's (or
any repo's) `git status`, so there is no gitignore entry that would apply to
it. The hook's only gate is a single `[ -f "$HOME/.claude/.error-mode-nudge-enabled" ]`
check — no repo-relative fallback exists. **Not a flaw; not even applicable.**

## Finding 3 — `worktree-required`: tracked-ness is the feature, not a bug

The repo-level `.claude/worktree-required` is **deliberately tracked** in
this repo, and the root `.gitignore:5-7` documents exactly that:

> `.claude/worktree-required` is tracked too (activates worktree enforcement).

The three-marker design (`_lib.sh:194-200`) is intentional:
1. **Committed repo sentinel** `<repo>/.claude/worktree-required` — a hard,
   non-opt-outable requirement that travels to every collaborator via `git pull`.
   *Being committable is the entire point.*
2. **Machine sentinel** `~/.claude/worktree-required` — personal default-on
   for all repos on one machine; defeated by a per-repo opt-out.
3. **Per-repo opt-out** `.claude/worktree-optout`.

Adding `.claude/worktree-required` to a gitignore would **break** feature (1) —
a repo could no longer declare team-wide enforcement. So the flag is correctly
*not* gitignored.

## Finding 4 — the iac-repo confusion is a stray-file question, not a gitignore bug

An *untracked* `<repo>/.claude/worktree-required` sitting in the iac repo is
neither policy-yet nor noise-to-ignore — it is a file in limbo. Crucially, a
machine-level `~/.claude/worktree-required` was already active, so enforcement
was **already on** globally; the repo-level file added nothing locally. The
design already covers "personal, every repo" via the machine sentinel, so a
repo-level marker has **no legitimate personal use** — its only purpose is to
be committed as team policy. Correct resolutions for that session:
- **Commit it** → make iac's worktree enforcement a hard requirement for every
  collaborator (opt-out cannot defeat it). Choose this only if that is the
  intended iac team policy.
- **Remove it** → rely on the already-active machine sentinel. No behavior
  change locally.

Either way, no gitignore or hook change is implied.

## The action: close the deferred belt-and-suspenders gap

The root `.gitignore:21-33` already applies a **belt-and-suspenders** guard to
three user-local `~/.claude/` files, ignoring their stow-package copies so an
accidental in-tree creation can never be committed and shipped to every stow
user:

```
claude/.claude/private-projects.md
claude/.claude/pii-patterns.md
claude/.claude/data-file-read-guard.md
```

Two machine-level markers fit the identical risk profile but lack the guard:
- `claude/.claude/worktree-required` — if accidentally `touch`ed inside the
  stow tree and committed, maps to `~/.claude/worktree-required` for **every**
  stow user → force machine-wide worktree enforcement on all of them.
- `claude/.claude/.error-mode-nudge-enabled` — same accident → arms the nudge
  for every stow user.

Note this path is the **stow-package** path (`claude/.claude/...`), which is
distinct from the tracked repo-root `.claude/worktree-required`; adding it does
**not** un-track or affect claude-config's own committed enforcement marker.

This is **not a newly-invented concern.** The worktree design record already
named exactly this item and consciously deferred it —
`.claude/plans/machine-level-worktree-enforcement.md:188-191`:

> The repo must **not** add `claude/.claude/worktree-required` (that path stows
> to every user = the shipped-default flip we are avoiding). Optional defensive
> hardening: add `/claude/.claude/worktree-required` to `.gitignore` so a
> fat-fingered create in the package dir can't be committed.

That entry was left out of the shipped `.gitignore` as an accepted low-priority
deferral. The `.error-mode-nudge-enabled` counterpart is the same shape and
simply postdates that plan (it arrived with the error-mode-analysis work), so it
was never in that plan's scope. Implementing both now would close the deferral
and apply the existing belt-and-suspenders pattern consistently across all five
user-local `~/.claude/` files.

### Approach

Append two entries + a short rationale comment to the root `.gitignore`,
mirroring the existing belt-and-suspenders block:

```gitignore
# Machine-level opt-in markers belong at ~/.claude/ directly, created by hand
# (touch), never via the stow package. These entries stop an accidental in-tree
# copy from being committed and shipped to every stow user. See
# claude/.claude/hooks/_lib.sh (worktree) and docs/error-mode-nudge.md (nudge).
claude/.claude/worktree-required
claude/.claude/.error-mode-nudge-enabled
```

**Critical files:** `.gitignore` (repo root) — the only file changed.

**Verification:** `touch claude/.claude/worktree-required claude/.claude/.error-mode-nudge-enabled`
then `git status --porcelain` shows neither as untracked (both ignored);
`git check-ignore claude/.claude/worktree-required` prints the path. Confirm
the repo-root `.claude/worktree-required` stays tracked:
`git ls-files | grep worktree-required` still lists it. Clean up the touched files.

**Severity:** low. It guards a manual-mistake footgun, not a live bug — which
is why the answer to the original question stands independently: *not a flaw.*
The change is defensive hygiene, consistent with the existing three-file guard.

## Out of scope

- No hook, skill, or `_lib.sh` logic change — the resolution logic is correct.
- No change to the tracked repo-root `.claude/worktree-required` in this repo.
- The iac-repo stray file is that repo's decision (commit vs remove), not a
  claude-config change.
