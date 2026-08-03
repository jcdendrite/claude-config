# Automatic session titles from repo + branch

## Context

**Goal:** a session working on a feature branch automatically gets a distinctive
terminal title of the form `claude-config/GH-504`, derived from where the session
actually is rather than from what it happened to say first — and that title is correct
after a `/handoff` resume.

Today the title comes from Claude Code's auto-titler summarizing the first user
prompt. That is fine for ad-hoc sessions and fails completely for handoff resumes:
`resume-context.sh:167` execs

```
claude --append-system-prompt-file "$DEST" "Continue from the handoff/brief file loaded
into your system prompt. If it contains a task-list resume directive, ..."
```

The handoff body arrives via `--append-system-prompt-file`, so the only *user* prompt
is that fixed boilerplate. The auto-titler summarizes the boilerplate, and every
resumed session lands on the same string — confirmed across three unrelated
transcripts: `"Resume from handoff brief and track tasks"` (twice) and
`"Resume from handoff brief with task tracking"` (once). Sessions are consequently
indistinguishable in the tab bar and the `/resume` picker.

`/rename` fixes one session manually and is lost at the next launch. The intended
outcome is that nobody has to run it.

## Approach

Two coordinated changes:

**1. A `SessionStart` hook** emitting `hookSpecificOutput.sessionTitle` from git state:

```json
{"hookSpecificOutput": {"hookEventName": "SessionStart", "sessionTitle": "claude-config/GH-504"}}
```

**2. Make the handoff resume command cwd-explicit**, so the resumed session starts in
the worktree whose branch it is resuming.

### Why change 2 is required, not optional

An earlier revision of this plan claimed handoff persistence came "for free" because
`resume-context.sh` inherits the caller's cwd. It does inherit cwd — but this repo's
own handoff spec states the opposite of what that implies.
`claude/.claude/skills/handoff/SKILL.md:87`:

> the anchor is session-scoped and does not survive the session boundary, **so a
> resumed session starts in the main checkout**

`handoff/SKILL.md:100` (§7), `brief/SKILL.md` §7.5, `docs/scripts.md:89`, and
`README.md:397` all give the resume incantation with **no cwd instruction**. The
documented flow therefore puts the resumed session in the main checkout on the default
branch — where a cwd-derived title would be wrong for exactly the case that motivated
this work.

Making the resume command `cd <worktree> && resume-context …` fixes the title *and* an
independent ordering defect: today the resumed session starts in the main checkout and
must re-enter the worktree per §4, which happens well after SessionStart has fired.
Anchoring at launch is correct on its own merits — a change one level up that dissolves
the need for a workaround inside the hook. §4 already requires the author to record the
worktree path, so §7 consumes information the handoff already carries; no new author
work. The `cd` prefix is **conditional** — a handoff written from the main checkout has
no worktree to enter, and §7 must not template a path that does not exist.

### Title composition

| Situation | Title |
| --- | --- |
| Feature branch in a repo | `claude-config/GH-504` |
| Detached HEAD | `claude-config/@a1b2c3d` |
| On the repo's default branch | *(nothing emitted — auto-titler runs as today)* |
| Default branch **undeterminable** (no `origin`, or `origin/HEAD` unset) | *(nothing emitted)* |
| Bare repo | *(nothing emitted)* |
| `--separate-git-dir` repo | `claude-config/GH-504` (resolves correctly — the working tree itself is normal, not bare; see Repo component below) |
| Component fails the character allowlist | *(nothing emitted)* |
| Not a git repo | *(nothing emitted)* |

**Emitting nothing on the default branch is the key scoping decision.** `claude/` ships
to every stow user on `git pull`. A user without branch-per-worktree discipline would
otherwise get `myrepo/main` for *every* session in that repo — identical across all
concurrent tabs, strictly worse than the semantic AI titles they have today. Skipping
the default branch means this replaces the auto-title only where it adds information.

**Every uncertain case fails closed — emit nothing.** The degraded state is exactly
today's behavior, not a broken one. The alternative (emit when unsure) reproduces the
identical-tabs regression the skip exists to prevent.

### The character allowlist replaces four separate defenses

Both components must match `^[A-Za-z0-9._/@+-]+$`, matched **under `LC_ALL=C`
specifically** — not under whatever locale the shell inherits. Anything failing the
match (under `C`) emits nothing.

**The locale pin on the match itself is load-bearing, not optional — verified twice
this session, independently, on real hardware.** POSIX bracket ranges (`A-Z`, `a-z`) are
collation-order matches, not codepoint matches, outside the `C` locale. Measured on
Debian glibc 2.41 / bash 5.2 under `LC_ALL=en_US.UTF-8`: `é` (`c3 a9`), `ü` (`c3 bc`), and
fullwidth `Ａ` (`ef bc a1`) **all match** `[A-Za-z]`. The same expression correctly
rejects all three under `LC_ALL=C` on the same machine, and rejects them under every
locale tested on macOS (BSD `regcomp` does not exhibit the same collation widening — a
platform difference, not a portable guarantee). Since `claude/` stows to Linux and WSL
users running glibc, the match must explicitly force `LC_ALL=C`; relying on ambient
locale would pass non-ASCII bytes through on exactly that audience.

This one rule (correctly locale-pinned) dissolves four findings that would otherwise
each need their own layer:

- **C1 control bytes.** An earlier revision asserted branch names cannot carry control
  bytes because `git check-ref-format` forbids them, and stripped only the directory
  component with `tr -d '[:cntrl:]'`. **That assertion is false — verified this session:**
  `check-ref-format` rejects `0x1b` (ESC) and `0x7f` (DEL) but **accepts `0x9b` (CSI) and
  `0x9c` (ST)**, and `LC_ALL=C tr -d '[:cntrl:]'` does not strip them (`a\x9bb\x9cc`
  survives as `a 233 b 234 c`). `0x9c` terminates an OSC title string on any terminal
  decoding 8-bit C1, making the remainder command stream. Reachable: `gh pr checkout <n>`
  against a hostile fork creates a local branch named from the fork's head ref. The
  `LC_ALL=C`-pinned allowlist rejects raw `0x9b`/`0x9c` and their UTF-8 encodings
  (`c2 9b`/`c2 9c`) — verified this session on both macOS and glibc.
- **`core.quotePath`.** Belt-and-braces, not load-bearing: the allowlist rejects a
  non-ASCII path whether or not it arrives C-quoted, so `-c core.quotePath=false` on the
  `worktree list` call is defensive but not depended on — do not cite it as the reason
  non-ASCII paths are handled.
- **Truncation.** With the match pinned to `LC_ALL=C`, an accepted value is guaranteed
  single-byte ASCII, so `${branch:0:32}` cannot cut mid-codepoint. The 32-char cap is a
  display heuristic for tab chrome with no vendor source; real branches here exceed it
  (`github-actions-comment-durability-standards`, 43). Two branches sharing a 32-char
  prefix truncate to the same title — a narrower version of the collision the
  default-branch skip exists to avoid; recorded at ledger row 14, not fixed.
- **Invalid UTF-8 basenames.** Rejected by the same rule.

Cost: a branch or directory with an emoji, accented character, or space gets no title
and keeps today's AI title (a directory containing a space, e.g. `~/Code/My Repo`, is
common on macOS and also avoids exercising word-splitting in the capture). Acceptable —
and strictly better than a mangled or injected one.

### Repo component

Basename of the **first entry** of `git -c core.quotePath=false worktree list --porcelain`,
which `git-worktree(1)` documents as always the main worktree: *"The main worktree is
listed first, followed by each of the linked worktrees."*

**Skip if that record carries the `bare` attribute.** Verified against the man page's own
porcelain example, whose first record is `worktree /path/to/bare-source` followed by
`bare`. Without this guard the bare-dotfiles pattern (`--git-dir=$HOME/.cfg
--work-tree=$HOME`) resolves the main worktree to `$HOME`, titling sessions with the
user's username — the exact outcome the not-a-git-repo skip was added to remove.

**`--separate-git-dir` is not skipped — it resolves correctly.** An earlier revision of
this plan grouped it with the bare-repo case; that was wrong, confirmed independently by
two reviewers against `git-worktree(1)`'s own attribute definition. `--separate-git-dir`
produces a repo with a normal working tree (`core.bare=false`); its first porcelain
record carries no `bare` attribute, so `worktree list` reports the correct working-tree
basename with no special-casing needed. The bare-attribute guard exists only for
genuinely bare repos.

Chosen over `git rev-parse --path-format=absolute --git-common-dir` +
`basename(dirname(…))`, which requires git ≥ 2.31, yields the *parent* directory's name
for bare and `--separate-git-dir` layouts (both wrong — this was the actual defect in
that alternative, not a defect this design carries over), and inside a submodule returns
`<super>/.git/modules/<name>` — the literal title `modules/<branch>`.

**Accepted gap:** the porcelain call omits `-z`, so a main-worktree path containing a
newline truncates at the first line break, which could pass the allowlist while naming
the wrong directory. `git-worktree(1)` recommends `-z` for exactly this case, but its
non-`-z` form predates the git 2.31 floor this design otherwise avoids requiring, and a
newline-bearing repo *directory* path is a rare cohort. Not fixed; named here rather than
silently dropped.

### No PR number, no `gh`

An earlier revision appended ` #<pr>` via `gh pr view`. Dropped by engineer decision
after three reviewers flagged distinct failure modes: a credential-touching subprocess in
a hook (no hook invokes `gh` today), nondeterministic output (a cold `gh` routinely
exceeds a 2s bound, so the same branch titles `#541` one session and not the next —
corroding the stable identity the design exists to provide), and `timeout`/`gtimeout`
portability. The PR is not lost: `statusline-command.sh:12-14` already renders a live,
clickable, review-state-colored PR link from the harness's own statusline payload. The
SessionStart payload carries no `pr` field (schema: `session_id`, `transcript_path`,
`cwd`, `hook_event_name`, `source`, `model`), so a hook could only have shelled out.

The hook is therefore pure-git: no network, no third-party binary, no timeout wrapper,
no credential surface.

### Hook mechanics

- **Self-filter on `source`.** Read stdin as `INPUT=$(cat 2>/dev/null)` — the shape
  `capture-session-id.sh:31` and `session-marker-dashboard.sh:22` already use, both of
  which fire on `startup` concurrently, so multiple stdin-reading SessionStart hooks are
  production-proven. Then `jq -r '.source // empty'` and
  `[[ "$SOURCE" == "startup" ]] || exit 0`. Do not rely on the settings.json matcher
  alone (CLAUDE.md, "Hook defense-in-depth"): the behavioral contract is that `/clear`,
  `/compact`, `--resume`, and `--fork-session` leave a manual `/rename` intact, so a
  matcher edit would silently clobber user-set titles. Do **not** use `read -t` — it is
  line-oriented and truncates JSON exceeding the pipe buffer.
- **Run git against the payload's `.cwd`**, not process cwd — it is authoritative and the
  payload is already being parsed. This must be tested directly: a case where process
  cwd is the main checkout but payload `.cwd` is a linked worktree, asserting the title
  reflects the worktree's branch — a hook that silently reads process cwd instead passes
  every other case in this plan.
- **Gate order is cheapest-first**, since most sessions emit nothing, and both kill
  switches have an explicit position — the per-repo one cannot move earlier than where
  its input becomes available: `.source` filter → machine-global kill-switch file test →
  `rev-parse --git-dir` → `symbolic-ref HEAD` → default-branch gate → `worktree list`
  (which resolves the main worktree root) → per-repo kill-switch file test against that
  root. Stating this explicitly matters: an implementer optimizing "cheap check first"
  without the ordering constraint could reach for a cwd-relative per-repo check before
  `worktree list` resolves — the exact defect the per-repo switch's resolution rule
  (below) exists to prevent. The linked-worktree kill-switch test case must assert on
  emitted output, not on a file-stat count, so that regression is caught behaviorally.
- **Default-branch detection** uses `git symbolic-ref -q refs/remotes/origin/HEAD`.
  Note `check-branch-divergence.sh:50` exits on detached HEAD — do **not** copy that gate
  verbatim, it would kill a supported case.
- **Registered as a fourth `SessionStart` group**, `matcher: "startup"`, matching the
  one-hook-per-group convention. This is *not* a mitigation for output collision — the
  docs state all matching hooks run in parallel regardless of grouping. The real
  unverified risk is that `check-branch-divergence.sh` is also `matcher: "startup"` and
  emits `additionalContext` while this emits `sessionTitle`; whether the harness merges
  two hooks' `hookSpecificOutput` objects is undocumented (row 2b). The spike covers it.
- **stdout/stderr discipline.** Every external command captures via `$(…)` or redirects
  both streams to `/dev/null` — stdout is the JSON channel, and stderr surfaces under
  `--debug`. Exit 0 unconditionally. `set -uo pipefail` without `-e` (per
  `shell-script-conventions.md`; `-e` would abort mid-emit), with explicit `|| exit 0` on
  every git call as `check-branch-divergence.sh:48-57` does.
- **Detached HEAD uses `git rev-parse --short HEAD`**, not `git describe` — `describe`
  walks the tag namespace and `--dirty` refreshes the index.
- **Kill switches.** Machine-global `~/.claude/.session-title-disabled`, mirroring
  `nudge-handoff-near-context-cap.sh`'s `~/.claude/.handoff-nudge-disabled`; plus
  per-repo `<main-worktree-root>/.claude/session-title-disabled`. **Resolved against the
  main worktree root the hook already computes, never cwd** — an untracked sentinel in
  the main checkout is invisible from `.claude/worktrees/<branch>/`, i.e. absent in
  exactly the sessions that emit a title, and a cwd-relative lookup also misses when the
  session launches from a subdirectory.

**Not doing: redaction of private-project names from titles.** Raised in review on the
grounds that titles are enumerable (screen-share window pickers, `tmux ls`, WM taskbars).
Rejected: this repo's redaction discipline governs what is *committed to a public repo*,
not what appears on the engineer's own screen, where the window's contents are far more
revealing than its label and the statusline and shell prompt already show the branch.
Wiring a `private-projects.md` lookup into a title hook is the compounding-defensive-layer
shape CLAUDE.md warns against. The kill switches cover the deliberate case.

### Accepted costs

- **The feature silently never fires in repos with no `origin` or an unset
  `origin/HEAD`.** Only `git clone` and `git remote set-head` write
  `refs/remotes/origin/HEAD`; `git init` + `git remote add` + push does not. Fail-closed
  is the right direction (fail-open gives `myrepo/main` on every tab), and the remedy is
  one command — `git remote set-head origin -a`. Document it in `docs/hooks.md`.
- **A stale `origin/HEAD`** (repo renamed `master`→`main` without `set-head -a`) resolves
  successfully to the wrong default, so a session on `main` gets titled `repo/main`. Same
  one-command remedy.
- **A resume run from a *different* worktree produces a confidently wrong title** —
  `claude-config/<other-branch>` — the only path that actively misleads rather than
  degrading to silence. Running it from the main checkout or `~` degrades to no title.
- **Same-branch collision survives at the handoff moment.** The handing-off tab and the
  resumed tab share a title precisely when the user must tell live from stale. Acceptable
  because the old tab is meant to close, but it is a residual against the Context claim.
- **`--resume <name>` and picker searchability regress on long-lived branches.** Every
  session on a branch shares one title where an AI title was unique, and `/` is awkward
  to type. Mitigated by the default-branch skip (the highest-collision case).
- **`/rename` is no more durable than today.** This replaces the *need* for it on feature
  branches rather than making a manual title survive a relaunch. A per-branch
  stored-override seam was considered and rejected as a second naming mechanism for one
  outcome.
- **Silent no-op after a Claude Code upgrade.** If a future version stops honoring
  hook-set `sessionTitle`, the hook still exits 0 with valid JSON and every test passes.
  Degradation is harmless. Record the verified `claude --version` in the hook header and
  `docs/hooks.md`.

### Assumption ledger

**Root problem:** handoff-resumed sessions are mutually indistinguishable because the
auto-titler summarizes a fixed boilerplate prompt.

| # | Assumption | Tag |
| --- | --- | --- |
| 1 | A hook-set `sessionTitle` drives the **terminal tab title**, not just `session_name` / the `/resume` picker. **Load-bearing; anchors: root.** | `[verified: spike, claude 2.1.220 — static-title hook launched via a fresh `claude "hello"` process; tab title read the spike's fixed placeholder string, screenshot captured]` |
| 2 | A hook-set `sessionTitle` is not overwritten by the auto-generated `ai-title`. **Load-bearing; anchors: root.** | `[verified: spike — title still read the spike's placeholder string after the first prompt completed; the session transcript (`~/.claude/projects/-Users-jared-MyCode-claude-config--claude-worktrees-spike-session-title/*.jsonl`) contains zero `ai-title` records, suggesting a hook-set title suppresses auto-title generation outright rather than racing it — a stronger result than "wins the race"]` |
| 2b | Two `startup` hooks emitting different `hookSpecificOutput` fields do not clobber each other. | `[verified: spike — worktree was 5 commits behind origin/main specifically to trigger check-branch-divergence.sh (also matcher "startup", global) alongside the spike hook; the same transcript grep matches its "behind origin"/"Trial merge" advisory text, confirming both hooks' output reached context in one session]` |
| 3 | `SessionStart` hooks can set the title via `hookSpecificOutput.sessionTitle`. | `[verified: changelog 2.1.152 line 1275; code.claude.com/docs/en/hooks]` |
| 4 | A fresh `claude … "prompt"` process reports `source: "startup"`. Note the cited source enumerates `startup`/`resume`/`clear`/`compact`/`fork`; the test matrix asserts behavior for the non-`startup` values, which the citation lists but does not individually confirm. | `[verified: code.claude.com/docs/en/hooks — "startup / New session"]` |
| 5 | Installed version 2.1.220 ≥ 2.1.152. | `[verified: claude --version]` |
| 6 | **Corrected from an earlier revision.** `resume-context.sh` execs a fresh process inheriting the caller's cwd — but the documented resume flow leaves that cwd as the **main checkout**, not the worktree. The earlier revision read this as "inherits the worktree's cwd," which the handoff spec contradicts. | `[verified: resume-context.sh:136-167; handoff/SKILL.md:87,100]` |
| 7 | The SessionStart payload carries no PR field. | `[verified: code.claude.com/docs/en/hooks input schema]` |
| 8a | `git worktree list --porcelain` lists the main worktree first. | `[verified: git-worktree(1) "list"]` |
| 8b | Its first record carries a `bare` attribute for a bare repo, so bare layouts must be skipped rather than trusted. `--separate-git-dir` is a separate case — see row 16, not skipped. | `[verified: git-worktree(1) porcelain example]` |
| 8c | The recipe yields the submodule's own directory name inside a submodule. | `[unverified]` — resolved by the submodule test case |
| 9 | **Corrected from an earlier revision.** Branch names *can* carry C1 control bytes: `check-ref-format` accepts `0x9b`/`0x9c`, and `tr -d '[:cntrl:]'` does not strip them. Hence the allowlist, not a strip. | `[verified: run this session against local git]` |
| 10 | Drop the PR segment; make the resume command cwd-explicit. | `[engineer-verified]` |
| 11 | Startup-only re-assert; **on-by-default, narrowed to feature branches inside a non-bare git repo**, with a machine-global and a per-repo kill switch. Reaffirmed after the narrowing. | `[engineer-verified]` |
| 12 | No title redaction; the repo's redaction discipline governs committed content, not local UI. (The title is later readable in `~/.claude/projects/**/*.jsonl` via the `ai-title`-adjacent session record, but the branch name is already present in that same file's `cwd`/`gitBranch` fields independent of this hook — no new exposure class. That directory's own lack of `.gitignore` coverage is the actual gap, tracked under Out of scope.) | `[engineer-verified]` |
| 13 | `refs/remotes/origin/HEAD` is resolvable. **Gates the default-branch skip**; false in `git init`-created repos. Fail-closed by decision. | `[unverified]` — resolved by test cases |
| 14 | 32-char branch cap. Display heuristic; no vendor source. | `[unverified]` |
| 15 | **Corrected from an earlier revision.** The allowlist regex must run under `LC_ALL=C` specifically. Under a UTF-8 locale, glibc's bracket-range matching (collation order, not codepoint) admits `é`/`ü`/fullwidth `Ａ` into `[A-Za-z]` — reproduced on Debian glibc 2.41. macOS `regcomp` does not exhibit this, so the earlier "allowlisted values are single-byte, no pin needed" claim held only on the platform it was tested on. | `[verified: reproduced independently on glibc 2.41/bash 5.2 and macOS bash 3.2/5.3, two reviewers]` |
| 16 | `--separate-git-dir` repos are **not** skipped by the `bare`-attribute guard — their working tree is normal, so `worktree list` resolves them correctly. An earlier revision grouped this with the bare-repo skip; wrong, confirmed against `git-worktree(1)`'s own attribute definition by two reviewers independently. | `[verified: git-worktree(1) — bare/detached are the only boolean attributes; a --separate-git-dir tree carries neither]` |

**Rows 1, 2, and 2b gate the design.** Step 1 resolves them before any other work.

## Critical files

**Spike (throwaway, not committed)**
- A hook echoing a static `sessionTitle` **and writing a sentinel file**, wired via
  `settings.local.json`, launched in a fresh terminal **on a branch behind `origin/main`**
  so `check-branch-divergence.sh` fires simultaneously (row 2b). The sentinel is the
  negative control: without it, "hook fired and the title stuck" and "hook silently
  no-op'd" are indistinguishable.

**Create**
- `claude/.claude/hooks/set-session-title-from-branch.sh`. Reuse from
  `check-branch-divergence.sh`: the `# hook-class: informational` line-2 tag, the
  skip-silent gate style (48-57), and the `jq -n --arg … '{hookSpecificOutput: {…}}'
  || true; exit 0` emit shape (120-122). Header records fail posture, the deliberate
  non-coverage (resume/clear/compact/fork; mid-session branch switches), and the verified
  `claude --version`.
- `claude/.claude/hooks/tests/test_set_session_title_from_branch.py`, mirroring
  `test_check_branch_divergence.py`'s `_run_hook` + real-git-fixture structure.

**Modify**
- `claude/.claude/tests/helpers.py` — add `run_hook_session_start`, carrying the exit-0 /
  valid-JSON-or-empty / exact-key-set (`{hookEventName, sessionTitle}`) /
  `hookEventName == "SessionStart"` assertions and returning the title or `None`. A
  *helper*, not per-case prose: across ~20 cases one omission reintroduces the
  `sessionTittle`-typo hole this closes. Precedent and rationale: `run_hook_stop`
  (`helpers.py:240-247`). It must accept a `home` override — `_build_subprocess_env`
  (`helpers.py:66-86`) is the seam, and without it the kill-switch cases read the real
  `$HOME` and any machine with that sentinel turns the whole file vacuously green.
  Absent-binary cases use `build_path_without` (`helpers.py:715`), not
  `_make_timeout_free_path`'s hardcoded list — its own docstring names under-symlinking
  as a silent false pass. (Note: helpers live at `claude/.claude/tests/helpers.py`, not
  under `hooks/tests/`, and are imported as `from helpers import …`. `run_hook_stop` is
  defined at `:202`; its assertion battery runs `:240-247`.)
- `claude/.claude/settings.json` — fourth `SessionStart` group, `matcher: "startup"`,
  command `~/.claude/hooks/set-session-title-from-branch.sh`.
- `claude/.claude/skills/handoff/SKILL.md` §7, `claude/.claude/skills/brief/SKILL.md`
  §7.5, `docs/scripts.md:89`, **and `README.md:397`** — the conditional `cd` prefix. All
  four restate the same incantation; leaving README stale is both a DRY violation under
  this repo's single-source rule and the highest-traffic copy.
- `README.md` hook table (~line 169) and `docs/hooks.md` `## Utility hooks` — the latter
  must state the skip rules, both kill switches, and the `git remote set-head` remedy.
  `test_hook_alignment.py`'s `_MAIN_HOOKS` drives a `docs/hooks.md` coverage test, so the
  entry is mandatory.

No new top-level `claude/.claude/` entry, so `require-stow-reminder.sh` does not arm.

### Test cases

Feature branch; default branch ⇒ none; **no `origin` remote ⇒ none**; **`origin` present,
`origin/HEAD` unset ⇒ none**; **`origin/HEAD` stale** (points at a branch that exists but
isn't actually the remote default, e.g. after a `master`→`main` rename with no
`set-head -a`) **⇒ `<repo>/<current-branch>` emitted — asserted as the specific known-wrong
string, not just "some title"**; **`origin/HEAD` dangling** (points at a ref that does not
exist) **⇒ none**, since `symbolic-ref -q` on a dangling symref does not resolve to a
usable branch name and the two must not be conflated; not a repo ⇒ none; **bare repo ⇒
none**; **`--separate-git-dir` ⇒ `<repo>/<branch>`, resolves correctly** (not skipped —
row 16); detached HEAD; real `git worktree add` linked worktree ⇒ `<main-repo>/<branch>`;
submodule (row 8c); branch > 32 chars truncates **(pinned to `LC_ALL=C`, since row 15
means the pin — not the allowlist alone — is what makes this locale-independent)**;
**payload `.cwd` pointing at a linked worktree while process cwd is the main checkout ⇒
the worktree's branch, proving the hook reads `.cwd` and not process cwd**; **allowlist
positive boundary: a branch using every accepted punctuation character
(`release/v1.2.3+build_x.y-z`) ⇒ accepted unmodified**, so a future tightening of the
pattern cannot silently kill real branches without failing a test; **allowlist rejections
on the branch component** (not only the directory component, per row 15/9): raw `0x9b`,
raw `0x9c`, `U+009C`, an accented branch name (e.g. `café`) **matched under `LC_ALL=C` and
again under `LC_ALL=en_US.UTF-8` to prove the pin, not the locale, decides the outcome**;
and on the directory component: invalid-UTF-8 basename, non-ASCII basename, a basename
containing a space ⇒ none in every case; `source` **absent**, stdin **empty**, stdin
**non-JSON**, **unknown/future value**, plus `resume`/`fork`/`clear`/`compact` ⇒ none;
`jq` absent ⇒ exit 0 + empty stdout, **stated as targeting the `.source` filter branch**
(with one `jq` binary the emit-path failure is not constructible, which is itself worth
recording); both kill switches at their documented gate position, including **sentinel in
the main tree while the session runs from a linked worktree** (asserted on emitted
output, not a stat count) and **session launched from a subdirectory**.

**Spec note, not a test case:** a repo with both an undeterminable default branch (no
`origin`) and a detached HEAD is not exercised as a combination — the gate order runs
`symbolic-ref HEAD` before the default-branch gate, so detached HEAD takes the
`claude-config/@sha` path regardless of whether the default branch could be determined.
Worth one comment in the hook naming this precedence if the implementer finds it
non-obvious; not required as a separate test given the gate order already makes it
deterministic.

## Verification

1. **Spike first — gates everything.** Confirm: (a) the tab title changes; (b) it still
   reads that value after the first prompt completes — inspect the `ai-title` records in
   `~/.claude/projects/**/*.jsonl` rather than eyeballing the tab, with the sentinel file
   distinguishing "no override" from "hook never ran"; (c) `check-branch-divergence.sh`'s
   advisory still reaches context in the same session. If (a) fails →
   `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` plus a shell wrapper emitting OSC. If (b) fails
   → `--name` on `resume-context.sh:167` and re-scope. If (c) fails → merge both emissions
   into one hook. **Report the result with `claude --version` before writing hook code.**
2. `.venv/bin/pytest claude/.claude/` in full (from a worktree: `../../../.venv/bin/pytest`).
3. `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
4. **End-to-end handoff test — the acceptance criterion.** In a worktree: `/handoff`,
   then resume four ways:
   - `cd <worktree> && resume-context …` ⇒ `claude-config/<branch>` (documented flow)
   - from the main checkout ⇒ no title, auto-titler runs (degraded)
   - from `~` ⇒ no title, auto-titler runs (degraded)
   - from a *different* worktree ⇒ `claude-config/<other-branch>` (known-wrong, accepted)
5. Manual matrix: main checkout on `main` ⇒ auto-title unchanged; a feature worktree;
   a non-git directory; each kill switch.

## Out of scope

- `statusline-command.sh` — no `session_name` segment; it would restate the branch.
- Updating the title mid-session (branch switch, PR opened later).
- Making a manual `/rename` survive a relaunch.
- **Follow-up PR, surfaced by review but unrelated to this change:**
  `~/.claude/projects`, `telemetry`, `sessions`, `handoffs`, and `history.jsonl` are
  symlinked into this public repo's working tree with no `.gitignore` entries — they show
  as untracked, so `git add -A` would stage transcripts containing private branch names.
