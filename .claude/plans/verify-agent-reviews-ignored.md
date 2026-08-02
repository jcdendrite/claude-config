# Enforce agent-reviews/ ignore-effectiveness at the existing reviewer-write gate (GH-512)

## Context

**Goal:** make a reviewer agent's findings file un-committable in any repo a
stow consumer works in — enforced mechanically at the one hook that already
gates every reviewer write, not left to prose a skill must remember to run.

Reviewer agents dispatched with `findings_path:` write
`agent-reviews/<agent>-<epoch>-<slug>.md` into the tree under review. In
`claude-config` that path is covered by a committed `.gitignore` entry
(`.gitignore:19`). Every other repo relies on the fallback at
`code-review/SKILL.md:262` — append `agent-reviews/` to
`$(git rev-parse --git-dir)/info/exclude` — which is inert inside a linked
worktree: confirmed this session, `git rev-parse --git-dir` there resolves to
`.git/worktrees/<name>`, while `git rev-parse --git-path info/exclude`
resolves to the shared `.git/info/exclude` that `gitrepository-layout(5)`
documents git as actually reading.

**Why now:** GH-512 captured the diagnosis. Worktree enforcement makes the
broken condition the normal one, and the affected surface is every stow
consumer's private project repos, where a broad `git add -A` can sweep a
findings file into shared history.

**Outcome, and how this plan arrived here:** two `/plan-review` rounds already
ran against earlier drafts of this plan.

- **Round 1** proposed fixing the path expression and adding an install-time
  global git-excludes entry with a session-start drift advisory to cover users
  who hadn't reinstalled. Four specialists converged eight findings on that
  surface — a mis-scoped hash proxy, an `install.sh`-killing `set -e` bug, a
  symlink guard that misses real dotfiles-repo files, a permanent
  unsuppressable advisory — all from one root cause: verifying a *proxy* for
  the property instead of the property itself. Replaced with a
  `git check-ignore` verification at the point of use — no install.sh change,
  no new hook, no stamp file.
- **Round 2** reviewed that replacement and found it correct in substance but
  wrong in *placement*: putting the ensure/verify/choose sequence in
  `code-review/SKILL.md` prose leaves every **sibling** `findings_path:`
  dispatch site unprotected — confirmed `ready-for-review/SKILL.md:107`
  dispatches `skill-fidelity-reviewer` with `findings_path:` via the identical
  convention and would not inherit the fix. It also leaves the guarantee
  untested, since nothing mechanically re-asserts prose-level compliance. This
  is CLAUDE.md's own instruction, restated by the reviewer: *"audit structural
  siblings before scoping a fix narrowly … scope is set by the bug, not by
  where the symptom surfaced."*

**This version enforces the invariant at `deny-reviewer-tree-mutation.sh`** —
the hook that already gates every `Write`/`Edit`/`MultiEdit` from the closed
set of review-only agents, and already exempts `agent-reviews/*`
unconditionally (`:181`). One mechanical check there covers every current and
future `findings_path:` dispatch site, with no per-skill prose to keep in
sync. This also matches this repo's own stated doctrine: *"the answer is a
hook configured in `.claude/settings.json`, not a memory or a skill
instruction"* (repo-root `CLAUDE.md`, "Should this be a hook?").

## Approach

`git check-ignore` tells you, authoritatively, whether git will honor an
ignore for a given path — verified live against this session:

```
$ git check-ignore -q agent-reviews/probe.md; echo $?
0                                    # main tree of THIS repo — ignored
$ (cd .claude/worktrees/deny-credential-file-reads && git check-ignore -q agent-reviews/probe.md); echo $?
0                                    # linked worktree — also ignored (committed .gitignore covers it here)
```

`git-check-ignore(1)`: *"0 — One or more of the provided paths is ignored. 1 —
None of the provided paths are ignored. 128 — A fatal error was encountered."*
It needs no existing file at that path — both probes above ran against a path
that had never existed on disk.

**The hook change.** `deny-reviewer-tree-mutation.sh`'s `Write|Edit|MultiEdit`
arm currently exempts any path matching `agent-reviews/*` or
`*/agent-reviews/*` unconditionally (`:167-181`). Insert one check between the
traversal guard and that exemption: `unset GIT_DIR GIT_WORK_TREE
GIT_INDEX_FILE` (mirroring `require-worktree-for-git-writes.sh:100` exactly —
without it, an inherited `GIT_DIR` could redirect the check to a different
repo's ignore rules than the one the write actually targets), then run
`cd "$CWD" 2>/dev/null && timeout 5 git check-ignore -q -- "$FILE_PATH"`.

**No `-C "$REPO_ROOT"`, no `rev-parse --show-toplevel` call at all.** `git -C
<path>` resolves relative pathspec arguments against `<path>`, not against the
shell's actual working directory — standard, long-documented git behavior. The
Write tool resolves a relative `file_path` against `.cwd`, not against the
repo root. Using `-C "$REPO_ROOT"` would therefore check a different path than
the one actually written whenever `.cwd` is a subdirectory of the repo root —
`cd "$CWD"` first puts the check in the exact same resolution frame as the
write, and removes a subprocess call from the PreToolUse path in the bargain.

- Exit 0 (ignored) → exempt, exactly as today.
- Exit 1 (not ignored), exit 128 (fatal — not a repo, bad path), a
  `timeout`-induced 124, or the `cd` itself failing → **deny**. The message
  distinguishes "not ignored — protection is not in place" (exit 1) from
  "could not confirm — exit `<code>`" (128/124/cd-failure) — both fail closed
  for the same reason: an unconfirmed invariant is not a satisfied one. The
  "not ignored" message must **not** suggest the reviewer fix the ignore state
  itself (must not repeat `$SANCTIONED_ALTERNATIVE`'s "the only sanctioned
  in-tree write is the findings file" framing, which reads as an invitation to
  `printf 'agent-reviews/\n' >> ...` — exactly the raw-Bash-redirect vector
  this hook's own header already documents as unguarded). State plainly: fall
  back to inline output; do not create or modify ignore rules yourself — that
  is the dispatching skill's job, not the reviewer's.

**No new fallback machinery is needed on denial.** Every reviewer agent's
existing `### File-based output` section already reads: *"If the Write call
fails, do not report success. Instead, state the failure explicitly and fall
back to the Inline output format"* (verified verbatim in
`staff-backend-engineer.md` and structurally identical across all nine
reviewer agents). A hook denial is a failed Write; the fallback a denial
triggers is the fallback that already exists for any other write failure. No
agent file changes.

**Skill prose keeps a lightweight, best-effort "ensure" step** — append
`agent-reviews/` to `$(git rev-parse --git-path info/exclude)` idempotently
before the first spawn — in both `code-review/SKILL.md` and
`ready-for-review/SKILL.md`. This is not the safety boundary (the hook is);
it exists only to raise the odds the hook's check passes, preserving the
file-based context savings instead of degrading to inline. Fixing `--git-dir`
→ `--git-path` here matters on its own even with the hook in place: it is what
makes the common case (protection already present) fast and correct rather
than relying on the deny-and-fall-back path every time.

Duplicating the one-sentence ensure step across both skill files, rather than
extracting it, follows this repo's explicit "no shared partials across
skills" convention (repo-root `CLAUDE.md`): SKILL.md has no `include`/`import`
mechanism, and cross-skill sharing is deliberately not done.

**Alternatives set aside**

- *Fix only the static path expression in `code-review/SKILL.md`, no hook
  change.* Round 2's finding directly: leaves `ready-for-review/SKILL.md:107`
  and any future `findings_path:` dispatcher unprotected, and the guarantee
  stays untestable (prose compliance vs. a mechanical assertion).
- *Move the append itself (not just the check) into the hook, so the hook is
  fully self-contained.* Rejected: a `hook-class: gate` file becomes a
  git-config-mutating side effect of evaluating an unrelated Write, on every
  single reviewer write rather than once per session before the first spawn,
  and introduces a redundant-append race across concurrent reviewer writes
  that the once-per-session skill-level append doesn't have.
- *Install-time global git-excludes entry.* Round 1's rejected design —
  heavier, and the session-start advisory it required to cover pre-existing
  installs is what generated most of Round 1's findings.
- *Writing findings outside the tree (`~/.claude/agent-reviews/`).* Set aside
  in the original design conversation: unbounded accumulation of large,
  short-lived files with no reaper, versus the current design's reaping via
  `git worktree remove` / `cleanup-merged-branches.sh` /
  `cleanup-idle-open-pr-worktrees.sh`.

### Assumption ledger

**Root problem:** a reviewer findings file can land untracked-but-uncommittable
only if `agent-reviews/` is actually ignored in the repo being written to; the
prior fallback used a static, sometimes-wrong path expression, checked nothing,
and lived only in the one skill that happened to author it.

**Mechanism:** a `git check-ignore` gate inside the existing
`deny-reviewer-tree-mutation.sh` `Write|Edit|MultiEdit` arm — anchors: root.
Lighter primitives considered: (a) fix only the static path expression and
trust it, in skill prose — fails the sibling-dispatch-site case (Round 2's
finding) and stays untestable; (b) keep the check in skill prose but enumerate
and duplicate it at every `findings_path:` site — heavier maintenance surface
than the hook (N skill files to keep in sync vs. one), and still untestable by
the same suite that already covers this hook's other exemptions.

**Rows**

1. Inside a linked worktree, `git rev-parse --git-path info/exclude` resolves
   to the common gitdir; `$(git rev-parse --git-dir)/info/exclude` does not.
   *[verified: ran both in `.claude/worktrees/deny-credential-file-reads`; five
   inert worktree-local `exclude` files present, each containing
   `agent-reviews/`]*
2. Git ignores a linked worktree's own `info/` directory. *[verified:
   `gitrepository-layout(5)` — "This directory is ignored if $GIT_COMMON_DIR is
   set and \"$GIT_COMMON_DIR/info\" will be used instead."]*
3. `git check-ignore -q <path>` exits 0 (ignored), 1 (not ignored), or 128
   (fatal error), and requires no existing file at that path. *[verified:
   `git-check-ignore(1)` EXIT STATUS; ran the probe live against a nonexistent
   path in both the main tree and a linked worktree, both exit 0]*
4. `deny-reviewer-tree-mutation.sh`'s `Write|Edit|MultiEdit` arm already
   receives `.tool_input.file_path` and unconditionally exempts
   `agent-reviews/*` / `*/agent-reviews/*` at `:181`, with a traversal guard
   ahead of it at `:167-176`. *[verified: read `:161-184`]*
5. The PreToolUse payload carries `.cwd`, already used as the trusted
   repo-resolution input by five sibling hooks including
   `require-worktree-for-git-writes.sh:102`, which also `unset`s `GIT_DIR`
   `GIT_WORK_TREE` `GIT_INDEX_FILE` at `:100` immediately before reading it —
   without that unset, an inherited `GIT_DIR` redirects git commands to a
   different repo's state. `git -C <path>` resolves relative pathspec
   arguments against `<path>`, not the shell's actual cwd, so `cd "$CWD"` (not
   `-C` against a separately-resolved repo root) is what puts a check in the
   same frame the Write tool uses to resolve `file_path`. *[verified:
   `require-worktree-for-git-writes.sh:100,102`; `-C`'s path-resolution
   behavior is standard, long-documented git CLI semantics]*
6. `ready-for-review/SKILL.md:107` dispatches `skill-fidelity-reviewer` with
   `findings_path:` via "same convention as `/code-review`," and is the only
   other `findings_path:` dispatch site in the repo. *[verified: `git grep -n
   "findings_path:" -- 'claude/.claude/skills/*.md'` returns exactly these two
   lines]*
7. Every reviewer agent's `### File-based output` section already instructs
   falling back to inline output on a failed Write, with no distinction for
   *why* the write failed. *[verified: read the identical clause in
   `staff-backend-engineer.md:99-110`, structurally present in all nine
   reviewer agents per Step 3 grep]*
8. `require-plan-review.sh:232`'s `<repo>/agent-reviews/*` exemption is
   unaffected by this change — it gates the plan-review marker's own
   Edit/Write path, not the reviewer-dispatch hook. *[verified: read
   `:216-238`]*
9. Findings files are large and stop being useful at PR merge, so in-tree
   co-location plus worktree-scoped reaping is the desired lifecycle.
   *[engineer-verified]*
10. `deny-reviewer-tree-mutation.sh` currently does zero filesystem or git
    interaction — pure string/glob matching on `file_path`. Adding a `git
    check-ignore` subprocess call is a new capability class for this file, not
    an extension of an existing pattern. *[verified: read the full file;
    `require-worktree-for-git-writes.sh` is the sibling hook that already
    shells out to `git` from a resolved cwd, so the pattern exists in this
    hook family even though not in this specific file yet]*

## Critical files

**`claude/.claude/hooks/deny-reviewer-tree-mutation.sh`** — in the
`Write|Edit|MultiEdit` case arm (`:161-184`), after the traversal-guard case
statement and before the unconditional `agent-reviews/*` exemption: `unset
GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE` (mirror
`require-worktree-for-git-writes.sh:100` exactly), then for a path matching
`agent-reviews/*` / `*/agent-reviews/*`, run `cd "$CWD" 2>/dev/null && timeout
5 git check-ignore -q -- "$FILE_PATH"` — no `-C`, no separate `rev-parse
--show-toplevel` call; `cd` first puts the check in the same path-resolution
frame the Write tool itself uses. Branch on the result:
- 0 → `exit 0` (allow), unchanged from today.
- 1 → deny: *"'agent-reviews/' is not actually ignored in this repo —
  `findings_path` dispatch is unsafe here. Fall back to inline output; do not
  create or modify ignore rules yourself — that is the dispatching skill's
  job."* Deliberately does **not** reuse `$SANCTIONED_ALTERNATIVE` here: that
  text's "the only sanctioned in-tree write is the findings file" framing
  reads as an invitation to fix the ignore state directly (e.g. a raw
  `printf … >> .git/info/exclude`), which is exactly the unguarded
  raw-Bash-redirect vector this hook's own header already documents as a known
  gap.
- 128, a `timeout`-induced 124, or the `cd` itself failing → deny: *"could not
  confirm 'agent-reviews/' is ignored (exit `<code>`, not a git repo, or the
  check failed) — refusing to allow the write under an unconfirmed
  invariant."*

Update the header comment's "Known gaps" section: this hook now performs one
git subprocess call per reviewer write to `agent-reviews/*`, a change from its
prior pure-string-matching design — note the class change explicitly, per this
repo's comment convention of stating the one non-obvious constraint.

**`claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`** — the
existing `test_reviewer_write_to_findings_path_allowed` and
`test_reviewer_write_to_nested_agent_reviews_path_allowed` currently assert
unconditional allow with no git repo fixture; both must be updated to run
inside a scratch repo where the target path is actually ignored (or the test
harness's `run_hook` needs a `cwd=` parameter pointed at such a fixture — check
how the existing test module builds any git-repo fixtures elsewhere in this
suite, e.g. `test_require_worktree_for_git_writes.py`, and reuse that
convention rather than inventing a new one). Add:
- ignored path → allow (existing cases, now backed by a real ignored fixture)
- path present but **not** ignored (no `.gitignore` entry, no `info/exclude`
  entry) → deny
- non-agent-reviews path in a repo where it happens to be ignored anyway → deny
  unchanged (the exemption is still scoped to the `agent-reviews/` glob first)
- `.cwd` pointing outside any git repo → deny (128/`cd`-failure path)
- `GIT_DIR` set (in the hook's inherited environment) to point at a different,
  foreign repo → deny — regression test for the `unset` fix; without it this
  case would falsely allow by checking the wrong repo's ignore rules
- `.cwd` set to a subdirectory of the repo root, target repo has an unanchored
  `agent-reviews/` pattern → allow — regression test for using `cd "$CWD"`
  instead of `-C` against a separately-resolved root, which would check the
  wrong path-resolution frame and could false-deny
- decoy filename (`agent-reviews-notes.md`) → deny, unchanged from today

**`claude/.claude/skills/code-review/SKILL.md:262`** — replace "Before the
first spawn, add `agent-reviews/` to `$(git rev-parse --git-dir)/info/exclude`
idempotently (grep-check before appending) so findings files can't be
accidentally staged; cleanup is automatic with the worktree." with: "Before
the first spawn, add `agent-reviews/` to `$(git rev-parse --git-path
info/exclude)` idempotently (grep-check before appending); a reviewer's
findings-file write is safe only when `agent-reviews/` is actually ignored,
which `deny-reviewer-tree-mutation.sh` now confirms at write time — a denied
write falls back to each reviewer's documented inline output automatically."
Keep net length flat — `check-skill-length.sh` gates this file.

**`claude/.claude/skills/ready-for-review/SKILL.md:107`** — add the identical
one-sentence ensure step immediately before its `findings_path:` dispatch
line, duplicated deliberately per this repo's no-shared-partials convention.

**`.gitignore:16-19`** — rewrite the comment: this repo's committed entry is
belt-and-suspenders; the general-repo mechanism is the ensure step plus the
`deny-reviewer-tree-mutation.sh` check, not a bare path expression with no
verification.

**`docs/design-decisions.md:146`** — correct "The `agent-reviews/` directory is
gitignored and created on first write" (true only for this repo) to describe
the ensure/verify sequence and name `deny-reviewer-tree-mutation.sh` as the
enforcement point.

**`docs/hooks.md`** — update the existing `deny-reviewer-tree-mutation.sh`
entry to mention the new `check-ignore` gate on `agent-reviews/*` writes.

## Verification

```bash
../../../.venv/bin/pytest claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py
../../../.venv/bin/pytest claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

Live rehearsal, in a scratch repo with a linked worktree and **no** committed
`agent-reviews/` `.gitignore` entry:

1. From inside the worktree, simulate the hook's pre-fix behavior: confirm
   `git check-ignore -q agent-reviews/probe.md` exits 1 — reproducing the exact
   gap GH-512 reports.
2. Run the corrected ensure step by hand; confirm it appends to the shared
   gitdir's `info/exclude` (via `git rev-parse --git-path info/exclude`, not
   the inert worktree-local one), and confirm `check-ignore` then exits 0.
3. Invoke the updated hook directly with a `Write` payload targeting
   `agent-reviews/test-123-branch.md` and `.cwd` set to the scratch worktree,
   before and after step 2 — confirm deny then allow.
4. Dispatch a real reviewer with `findings_path:` from this repo (which has the
   committed entry) to confirm no behavior change in the already-working case.

## Out of scope

- An install-time global git-excludes entry, a session-start install-drift
  advisory, and any stamp/hash mechanism — Round 1's rejected design.
- Moving findings out of the repo tree, or into `.git/`.
- Any change to the nine reviewer agent files — their existing write-failure
  fallback already covers a hook denial with no modification.
- `require-plan-review.sh`'s `agent-reviews/*` exemption — unaffected, and a
  separate gate on a separate hook path.

**Review surface:** one hook file gains a single new conditional branch, its
test file gains fixture-backed coverage for both the allow and deny sides, and
two skill files gain one corrected sentence each. Risk concentrates entirely in
the hook's new `cd "$CWD" && git check-ignore` call — path-resolution-frame
correctness and exit-code handling are the two things the verification section
rehearses live rather than trusting the prose.
