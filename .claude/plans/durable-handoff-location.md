# Durable, self-consuming handoffs via an explicit resume command

## Context

`/handoff` and `/brief` write their continuity files to `/tmp/<slug>-handoff.md`
and `/tmp/<slug>-task.md`. `/tmp` does not survive a reboot — so a handoff
written right before an unplanned reboot is lost, which is the exact pain the
originating ticket (Todoist `6h5VcWj9GHpH699c`) names. Two constraints surfaced
while designing the fix and reshaped it:

1. **Durable ≠ hoard.** A naive "write to a durable folder" default makes the
   folder grow without bound, and most handoffs are transient (resumed once,
   then dead).
2. **No reliance on model behavior.** Any scheme where the resuming session must
   *remember to read and then delete/move* the file is too unreliable to build
   on — consumption has to be mechanical, not model-judgment.

Intended outcome: a handoff survives a reboot, is loaded into its resuming
session **without the model choosing to read a file**, and is **consumed
mechanically on resume** so the durable directory's steady state stays near
empty. The `skillOverrides: name-only` half of the ticket is already satisfied —
both skills are already `name-only` in `claude/.claude/settings.json`; no change
needed there.

## Approach

Split the file's life into two tiers, mirroring maildir's `new/` (pending) →
`cur/` (consumed) model, and make **resume an explicit command** so nothing
auto-claims anything:

- **Pending (written, not yet resumed):** `~/.claude/handoffs/<slug>-handoff.md`
  and `~/.claude/briefs/<slug>-task.md` — durable, survives reboot.
- **Resume:** the user runs one command, which (1) moves the file to a
  **per-user, owner-only temp path** — `mktemp` under
  `${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}` with a `<slug>-handoff.XXXXXX`
  name (slug prefix keeps it findable; the random suffix avoids collisions
  and a symlink-planted-path attack), then an explicit `chmod 600` on the
  moved file — empirically, `mv`'s same-filesystem rename(2) replaces the
  destination inode with the source's, discarding `mktemp`'s own 0600
  placeholder mode and inheriting whatever permissions the writing skill
  left on the source file instead; the explicit `chmod` after the move is
  what actually guarantees the owner-only property, not `mktemp` alone —
  which is the consume (a shell move, deterministic, no model action); then (2)
  launches a fresh interactive `claude` with the handoff loaded via
  `--append-system-prompt-file` (the load — harness-delivered into the system
  prompt, no model choice to read a file).

A single shared helper — `claude/.claude/scripts/resume-context.sh`
(stows to `~/.claude/scripts/`, where `marker.sh` already lives) — performs both
steps. Both skills' resume instructions defer to it, so the mv+launch logic has
one home. The helper takes an explicit file path (unambiguous, works for both
artifact kinds).

Why this shape satisfies every constraint:

- **Reboot loss → fixed:** the file is durable until the user resumes it.
- **Unbounded growth → fixed:** only *un-resumed* handoffs exist in the durable
  dir; resuming moves the file out; steady state ≈ 0. No retention window, no
  pruner.
- **Model reliability → fixed:** load is `--append-system-prompt-file`
  (harness-injected); consume is a shell `mv`. Zero model choice on either.
- **Order-dependence → absent:** nothing claims a pending handoff automatically.
  Only the session launched via `resume-context` loads anything; other sessions
  started in the same project (a frequent real workflow) are untouched.
- **Blast radius → contained:** no `SessionStart` hook, so nothing runs on any
  stow user's session-startup path; `resume-context.sh` itself only runs when
  the user explicitly invokes it. The one exception is the consume-on-read
  hook below, which *does* fire on every `Read` tool call for every stow user
  (global `PostToolUse` registration, not opt-in) — its own section addresses
  that blast radius directly, including a kill-switch.

Grounding for the two load-bearing mechanics (verified against primary sources
during design):

- `claude "query"` "Start[s an] interactive session with initial prompt"
  (Claude Code CLI reference), and `--append-system-prompt-file` "Load[s]
  additional system prompt text from a file and append[s] to the default
  prompt" — both harness-delivered, neither requires the model to read a file.

(No atomicity guarantee is claimed for the move: `~/.claude/handoffs` → the temp
dir routinely crosses filesystems — home disk to a tmpfs `/tmp` — where
`rename(2)` returns `EXDEV` and the move degrades to copy-then-unlink, not an
atomic rename. That is fine here: "one handoff, one session" makes a concurrent
resume a non-case, so the move only needs to **complete-or-abort before launch**,
which the script enforces — it does not need to be atomic. A CISO review round
asked for the partial-failure shape to be spelled out: if the copy phase of a
cross-filesystem move fails after `mktemp` already created the destination but
before the source is unlinked, the source is untouched — no data loss — and the
destination's mode is still closed by the script's own explicit `chmod 600`
regardless of how much content landed before the failure. (A platform-engineer
round against the finished implementation corrected an earlier, more precise-
sounding claim here that the destination is left as an *empty* file on this
failure path — GNU `mv`'s EXDEV fallback writes into the destination
progressively, so a failure partway through the copy can leave partial
content, not necessarily an empty file; the "no data loss, mode still closed"
properties hold either way.))

`mktemp`'s template form is grounded, not assumed: the script uses only the
bare positional form — `mktemp "$TMPDIR_ROOT/$(basename "$SRC").XXXXXX"`, an
absolute path ending in the required run of `X`s — with no `-p`/`--tmpdir`
flag. Verified against this machine's GNU coreutils `mktemp(1)` manual (`man
mktemp`): the plain `mktemp [OPTION]... [TEMPLATE]` form is the base
invocation, not a GNU-only extension; BSD/macOS `mktemp(1)` documents the
same positional-template usage. The GNU-only flags (`-p`, `--tmpdir`) are
deliberately not used, so this specific invocation is portable across both.

A CISO review round also flagged one exposure-surface note worth stating
plainly rather than leaving implicit: moving a file from `~/.claude/handoffs/`
(inside a `$HOME` directory tree, typically `700`/`755`) to
`${TMPDIR:-/tmp}` changes the *directory* trust boundary even though the file
itself stays 0600 — on a shared multi-user machine, `/tmp` is often
world-traversable (`1777`), so another local user can enumerate the
`<slug>-handoff.XXXXXX` filename (leaking the slug, e.g. a task or project
name) via `ls /tmp` even though they can't read the 0600 file's contents.
This is a filename/metadata leak, not a content leak. Worth one line in the
script's header comment so it's a documented, known property rather than an
implicit one. The content-exposure direction is a strict improvement either
way: the pre-fix `/tmp/<slug>-handoff.md` had no permission hardening at all,
so 0600 is strictly tighter than what shipped before this fix.

A second CISO round, run against the finished implementation, found the
filename leak above was actually fixable rather than a residual to accept:
`resume-context.sh`'s `mktemp` template uses a fixed, non-descriptive prefix
(not `$(basename "$SRC")`), so the temp filename itself carries no slug —
closing the metadata leak instead of just documenting it.

That same round found a second, more load-bearing gap: the write recipe
below only `chmod 700`s the *directory*, never the continuity file itself.
Directory-level `700` fully blocks another local account from resolving the
path at all — the file's own mode never mattered as long as the file stayed
under `~/.claude/handoffs/`/`~/.claude/briefs/`. But that made file-level
permissions a single point of reliance on the directory layer never being
bypassed (a copy elsewhere, a future recipe regression) — an unnecessary
gap given `resume-context.sh` already treats "chmod the file, don't just
rely on where it sits" as the standard for its own destination file. Fix:
the write recipe now `touch`es the target path and `chmod 600`s it *before*
the model's `Write` call populates it, so the file carries the same
independent, at-rest protection resume-context.sh's own destination already
had, rather than a second reflexive `chmod` bolted onto resume-context.sh
after the fact (that second layer was considered and rejected — once the
file is created 600, there is nothing left for a second downstream chmod to
close; adding one anyway would be hardening a gap that no longer exists).

The consume happens **at launch** (read path → move → launch with the moved
copy), not at session exit, so it does not depend on the resuming session ending
cleanly.

### A second resume path exists: same-session `/clear` + manual read

`resume-context.sh` only covers resume via a **new** `claude` process. In
practice a handoff is also resumed a second way: the writing session runs
`/clear` and the engineer then types "Read `<path>` and continue" as an
ordinary message in the *same* process — no new process, no
`resume-context.sh` invocation, no `--append-system-prompt-file`. The model
reads the file via the Read tool exactly as it did before this fix, and
nothing moves it out of the durable directory. Left alone, every
same-session resume would leave one more file behind, defeating the
"steady state ≈ 0" goal for that path even though the reboot-survival path
is fixed.

The fix is a **PostToolUse `Read` hook**,
`consume-durable-continuity-file-on-read.sh`, that fires whenever a `Read`
tool call's `file_path` matches `~/.claude/handoffs/*-handoff.md` or
`~/.claude/briefs/*-task.md`, and moves the file out via
`resume-context.sh --consume-only <path>` (the same mktemp-based move
`resume-context.sh` already performs, exposed as a mode that skips the
launch step). This closes the gap without launching anything — the
model's own `Read` tool call is the only trigger, and the hook is
mechanical (a shell `mv`), not a "the model must remember to clean up"
convention.

This is **not** the auto-claim `SessionStart` hook rejected above. That
design was rejected because it had to *guess which fresh session should
load which pending handoff* — order-dependent, and wrong whenever multiple
sessions start in the same project. This hook makes no such guess: it only
ever reacts to a `Read` call the model already made in direct response to
an explicit instruction the engineer typed, on the exact file that call
named. It adds no auto-loading and no session-start behavior; it only
prevents that one already-happening file read from leaving durable-dir
cruft behind.

Known accepted trade-off: this hook also fires on a plain inspection read
of a continuity file (checking whether an old handoff is still relevant,
say) — the file is moved out on that read too, even though nothing was
actually resumed. Inspecting a continuity file without consuming it needs
a shell `cat` via `!` instead of asking the model to `Read` it. Documented
in the hook's own header, not worked around — a PostToolUse hook cannot
distinguish "this Read was a resume" from "this Read was a peek" from the
tool call alone, and adding that distinction would mean tracking session
intent, which reintroduces the guessing problem this hook exists to avoid.

Two properties this hook needs that its cited precedent
(`consume-migration-token.sh`) doesn't, because that precedent ships inside
an opt-in *plugin* (installed per-project) while this hook ships in the
globally-stowed `claude/.claude/hooks/`, firing for every stow user
unconditionally (a platform-engineer review round caught this distinction —
the "Blast radius" bullet above undersold it by reasoning about the
rejected `SessionStart` design, not this hook):

- **A kill-switch**, mirroring `nudge-handoff-near-context-cap.sh`'s existing
  `~/.claude/.handoff-nudge-disabled` sentinel-file convention exactly: the
  hook checks for `~/.claude/.consume-durable-continuity-disabled` at entry
  and exits 0 immediately if present. On-by-default (no setup needed, same
  as today), but gives a user who dislikes the behavior — or hits an
  unanticipated interaction — a one-line local opt-out that doesn't require
  a PR against the shared, committed `settings.json`.
- **An explicit timeout on the move**, because the hook runs synchronously
  inside the `Read` tool call's `PostToolUse` phase: if `$HOME` is
  network-backed (NFS/CIFS home directories, common in managed/enterprise
  environments), a hung or slow mount would block the triggering `Read`
  call indefinitely with no guard. `claude-hook-review`'s own Section 7
  requires an explicit timeout on any external command touching a
  daemon/socket/network. Wrap the hook's call to `resume-context.sh
  --consume-only` with the same `command -v timeout` + `timeout 5` guard
  `_lib.sh`'s `_lib_jq`/`git_capped` helpers already use elsewhere in this
  repo (falls back to a bare, unguarded call on BSD/macOS systems lacking
  `timeout(1)` — a latency backstop, not a correctness boundary, matching
  the existing precedent's documented caveat).

### Lighter alternatives considered

- **Age-based pruning of a durable folder** (prune files older than N days, at
  write time or via a `SessionEnd` hook) — rejected: it manages unbounded growth
  with a time heuristic instead of eliminating it, can reap a handoff still in
  use, and (hook variant) couples retention to session lifecycle, which
  `SessionEnd` doesn't even fire reliably for `claude -p` per `docs/hooks.md`.
- **`SessionStart` hook auto-injecting a project-scoped pending handoff** —
  rejected: to auto-claim it must guess which fresh session is the intended
  continuation, which is order-dependent and misfires when multiple distinct
  sessions are started in one project; and it adds an always-on hook firing for
  every stow user (opt-in marker, blast radius) to buy only "resume looks like a
  plain `claude`." Once resume is explicit, the hook earns nothing.
- **Native `claude --continue` / `--resume`** — already survives reboot (session
  transcripts persist to `~/.claude/projects/<project>/<id>.jsonl`), but replays
  the **full raw history**, which is exactly what a handoff exists to avoid near
  the context cap. It complements, not replaces, curated handoffs; noted in Out
  of scope.

## Critical files

- **`claude/.claude/scripts/resume-context.sh`** (new; committed with the
  executable bit set — `git add --chmod=+x` — so the stow symlink is runnable
  on every user's machine; the plan's own "script is executable" test would
  otherwise fail at author time with no stated fix). `#!/usr/bin/env bash` +
  `set -euo pipefail`, matching `marker.sh`'s conventions but resolving `bash`
  via `PATH` rather than `#!/bin/bash` (macOS ships bash 3.2 at that literal
  path; the script uses no 4.x-only features, so env-resolution is strictly
  safer). Behavior:
  1. Require exactly one argument (the source file path); on missing/wrong
     argcount, print a usage error to **stderr** and exit non-zero without
     touching the filesystem or invoking the launcher.
  2. If the source file doesn't exist, print an error to stderr and exit
     non-zero — same "no side effects" guarantee.
  3. Move it to a fresh path under `${RESUME_CONTEXT_TMPDIR:-${TMPDIR:-/tmp}}`
     via `mktemp` (per-user — not a fixed `/tmp/<basename>`, which a
     platform review flagged as world-readable, collision-prone across
     concurrent resumes, and symlink-plantable in a shared `/tmp`), using a
     fixed, non-descriptive `mktemp` prefix rather than the source file's
     own basename — a second CISO round found the source's slug embedded
     in the temp filename otherwise leaks via `ls` on a shared,
     world-traversable `/tmp` even though the file's own 0600 mode still
     blocks content reads — then an
     explicit `chmod 600` on the moved file. **Empirically verified during
     implementation:** a same-filesystem `mv` performs a `rename(2)`, which
     replaces the destination inode (and its permissions) with the source's
     — `mktemp`'s own 0600 mode on the placeholder is silently discarded by
     the move, and the file ends up with whatever permissions the writing
     skill left on the source. The explicit `chmod 600` after the move is
     what actually enforces the owner-only guarantee; relying on `mktemp`
     alone does not. Check both the move's and the `chmod`'s exit status
     explicitly (under `set -e` a failure aborts before step 4 regardless,
     but the script names the failure mode in its error text rather than
     letting `set -e` produce a bare non-zero exit).
  4. Resolve the launcher via `command -v "${RESUME_CONTEXT_LAUNCHER:-claude}"`;
     if not found, error to stderr and exit non-zero **before** the move (or:
     accept that the move already happened but never launch — either is fine
     as long as the moved-but-unlaunched case still leaves the file findable
     and the failure visible, not silent).
  5. Launch with the moved path, fully quoted throughout (`"$1"`, the `mktemp`
     result, the launcher invocation) — no bare `$1`/`$src` expansions anywhere,
     and the launcher variable is resolved to a single executable path, never
     expanded as an unquoted command word (word-splitting on a value containing
     spaces is the concrete bug class this guards against).

  Two seams for testability, both env-var-overridable so tests never touch the
  real `claude` binary or the real shared `/tmp`:
  - `RESUME_CONTEXT_LAUNCHER` — path to the command to exec instead of `claude`.
    Tests point it at a stub that appends its received `"$@"` to a recorder
    file, so a test can assert the exact args built (not just "didn't throw").
  - `RESUME_CONTEXT_TMPDIR` — overrides the temp-dir root; tests point it at a
    pytest `tmp_path`, so runs never collide with each other or with a real
    `/tmp`.

  - **`--consume-only` mode:** `resume-context.sh --consume-only <path>` performs
    steps 1–3 above (argcount/missing-file checks, the mktemp-based move) and
    then exits — it never resolves or invokes a launcher. Added so
    `consume-durable-continuity-file-on-read.sh` (below) can reuse the exact
    same move logic instead of duplicating it; the launcher-resolution and
    exec steps are the only parts skipped in this mode.

  Known limitation to document in a header comment: a user who shell-*aliases*
  `claude` won't get that alias inside the script (aliases aren't inherited by
  non-interactive scripts) — `command -v` resolves the `claude` on `PATH`.
- **`claude/.local/bin/resume-context`** (new) — a thin wrapper mirroring the
  existing `claude/.local/bin/cleanup-merged-branches` pattern exactly
  (`#!/usr/bin/env bash` + `exec "$HOME/.claude/scripts/resume-context.sh"
  "$@"`), so `resume-context.sh` — a command the user is meant to type
  directly, unlike `marker.sh` which only skills invoke — gets the same
  `~/.local/bin/` PATH treatment README's "Scripts" section already documents
  for user-facing scripts. Without this, resume is stuck typing the full
  `~/.claude/scripts/resume-context.sh <path>` path every time.
- **`claude/.claude/skills/handoff/SKILL.md`** — write target `/tmp/<slug>-handoff.md`
  → `~/.claude/handoffs/<slug>-handoff.md` (frontmatter `description`, body,
  "Slug naming", pre-write checklist). Add a `mkdir -p ~/.claude/handoffs`
  step before the write. Rewrite §7 "Resume command" from "Read /tmp/<slug>…
  and continue." to `resume-context ~/.claude/handoffs/<slug>-handoff.md` (the
  `~/.local/bin/` wrapper name, not the full `~/.claude/scripts/` path — note
  it can be aliased further for convenience). A second CISO round (run
  against the finished implementation) found directory-level `chmod 700`
  alone leaves the file itself at the `Write` tool's default mode — add
  `touch <path>` + `chmod 600 <path>` to the same fenced recipe, *before*
  the model's `Write` call, so the file carries independent at-rest
  protection rather than relying solely on the directory layer.
- **`claude/.claude/skills/brief/SKILL.md`** — same edits for the
  `~/.claude/briefs/<slug>-task.md` path (frontmatter, body, `mkdir -p`, "When
  to use this vs `/plan-it`" line that currently calls the output "Ephemeral",
  "Slug naming"), including the same `touch` + `chmod 600` file-level fix.
  Brief has no resume-command section today; add one line giving
  the `resume-context` invocation for its path so its cold-start pickup is
  harness-driven too. Per the repo's "no shared partials across skills" rule the
  resume instruction is duplicated into each skill body intentionally — both
  simply reference the one shared script.
- **`docs/skills.md`** — `/handoff` and `/brief` catalog lines restate the
  `/tmp` paths; update both to the durable paths (skill body is canonical).
- **`docs/scripts.md`** — add an entry for `resume-context.sh`, matching the
  file's existing per-script description format (see the `marker.sh` and
  `cleanup-merged-branches.sh` entries for the shape): what it does, its two
  invocation modes (launch vs. `--consume-only`), the two env-var test seams,
  and the kill-switch sentinel path. This file is the canonical "full
  description of every script" README's "Scripts" section points to — a new
  script with no entry here is a real gap, not a stylistic nicety.
- **`README.md`** (~line 333, "Context management") — `/handoff` "write a
  structured resume file at `/tmp/<slug>-handoff.md`" → the durable path, and
  add a one-line mention of the `resume-context` resume command. Also add one
  bullet to the "Notable patterns" list (~line 66) for the new consume-on-read
  hook, mirroring the existing routing-read-gate bullet's one-line style and
  linking to `docs/hooks.md`.
- **`claude/.claude/hooks/consume-durable-continuity-file-on-read.sh`** (new).
  `# hook-class: informational` — PostToolUse, never denies (matches
  `log-routing-read.sh`'s and `plugins/lovable-cloud/hooks/consume-migration-token.sh`'s
  shape; the latter is the direct precedent for "PostToolUse hook consumes a
  one-shot file after a tool call touches it" — though see the two properties
  below that this hook needs and that plugin-scoped precedent doesn't, since
  this hook ships globally-stowed rather than opt-in-per-project). Behavior:
  1. Exit 0 immediately if `~/.claude/.consume-durable-continuity-disabled`
     exists (kill-switch, checked first, before any parsing).
  2. Defense-in-depth: filters `tool_name == "Read"` and
     `tool_input.file_path` against `"$HOME"/.claude/handoffs/*-handoff.md`
     and `"$HOME"/.claude/briefs/*-task.md` itself, not just via the
     `settings.json` matcher.
  3. On a match, calls `"$HOME/.claude/scripts/resume-context.sh"
     --consume-only "$FILE_PATH"` wrapped in a `command -v timeout` + `timeout
     5` guard (falls back to a bare, unguarded call when `timeout(1)` is
     absent — same BSD/macOS caveat as `_lib.sh`'s existing `_lib_jq`/
     `git_capped` wrappers), discarding its output and swallowing any failure
     (`|| true`) — fail-open, since a missed consume just leaves the file in
     place for next time, and a crashed or blocked consume-hook must never
     break the `Read` tool call it followed. An SDET round against the
     finished implementation found the `timeout 5` guard itself — the one
     property this wrapper exists to provide — had no test proving it
     actually bounds a hang, since there was no seam to inject a short
     timeout without a real multi-second sleep in the suite. Fix: the timeout
     duration is overridable via an env var (mirroring `resume-context.sh`'s
     own `RESUME_CONTEXT_*` seam convention), so a test can pair a short
     injected timeout with a stub `resume-context.sh` that sleeps past it and
     assert the hook still returns promptly.

  Always exits 0. Header documents: the known accepted trade-off (fires on
  inspection reads too, not just genuine resumes), the fail-open posture, the
  kill-switch sentinel path, the timeout rationale (network-backed `$HOME`
  risk), and the case-sensitive-glob limitation (a case-differing path that
  resolves to the same file on a case-insensitive filesystem like default
  macOS APFS silently won't match — low severity, since fail-open just skips
  the consume rather than corrupting anything).
- **`claude/.claude/settings.json`** — register the new hook under the existing
  `PostToolUse` → matcher `"Read"` entry, alongside `log-routing-read.sh`
  (`~/.claude/hooks/consume-durable-continuity-file-on-read.sh`, no
  `statusMessage` — no existing PostToolUse entry sets one).
- **`docs/hooks.md`** — add a bullet for the new hook under "Utility hooks",
  same style as the `log-routing-read.sh` entry. Also amend that entry's
  closing clause ("The only PostToolUse hook in this repo") — no longer
  accurate once this hook ships.
- **`claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py`**
  (new) — mirrors `test_log_routing_read.py`'s structure. An SDET review round
  flagged several cases the initial list omitted; the full case list is:
  - `Read` on a `handoffs/*-handoff.md` path consumes it (file gone after the
    hook runs); `Read` on a `briefs/*-task.md` path consumes it.
  - `Read` on an unrelated path (different directory entirely) is a no-op.
  - **Same-directory, wrong-suffix path** (e.g. `~/.claude/handoffs/notes.md`)
    is a no-op — a distinct boundary case from "unrelated path," so a future
    glob loosening that over-matches everything in the directory shows up as
    a failing test instead of passing silently.
  - Non-`Read` tool name is a no-op.
  - Missing `resume-context.sh` in the isolated `$HOME` (fail-open) is a
    no-op, file stays.
  - Kill-switch: `~/.claude/.consume-durable-continuity-disabled` present →
    no-op even on an otherwise-matching path.
  - **Double-consume / already-gone source**: call the hook a second time on
    a path whose file the first call already moved away — hook still exits
    `allow` (fail-open on the script's now-expected non-zero exit), not just
    when the script binary is entirely missing; these are two distinct
    failure modes and both need coverage.
  - **Symlinked path, documented as a known limitation, not silently
    unhandled**: a `Read` via a path that resolves into `~/.claude/handoffs/`
    through a symlink but doesn't textually match the glob is a no-op — this
    pins the hook's literal-path-only scope (matching `log-routing-read.sh`'s
    approach) as an intentional, tested boundary rather than an undiscovered
    gap.
  - **Regression-pinning for the accepted trade-off**: one test explicitly
    framed as "an inspection-only Read still consumes the file" (not just
    "any Read consumes"), so a later change narrowing the hook to skip
    inspection reads shows up as a failing regression test, not a silent
    behavior change nobody notices.
  - **Timeout actually bounds a hang**: using the new env-overridable timeout
    duration, pair a short injected timeout with a stub `resume-context.sh`
    that sleeps past it and assert the hook returns before the full sleep
    elapses (an SDET round found this — the wrapper's whole reason for
    existing — was otherwise untestable and untested).
  - **`timeout`-absent fallback branch**: shadow `timeout` off `PATH` (or
    stub it to `exit 127`) and assert the bare fallback call still fires the
    real consume — the same SDET round found this branch, documented as the
    BSD/macOS path, was dead to CI since `timeout` is present on every
    runner.
  - Hook always exits `allow`.

  Needs a small addition to `claude/.claude/tests/helpers.py` — an
  `install_resume_context_script(isolated_home)` helper that symlinks the real
  script into the isolated `$HOME/.claude/scripts/`, mirroring
  `run_skill_command`'s existing `marker.sh`/`_lib.sh` symlink pattern.
- **`claude/.claude/scripts/tests/test_resume_context.py`** (new) — standalone
  script-level tests, mirroring `test_cleanup_merged_branches.py`'s direct
  `subprocess.run` style (this script needs no `gh`/`git` stubbing, just the
  two documented env-var seams). Covers both invocation modes:
  - Launch mode: zero-args error, missing-source error, happy path with
    `RESUME_CONTEXT_LAUNCHER` pointed at a recorder stub and
    `RESUME_CONTEXT_TMPDIR` pointed at `tmp_path`, path-with-spaces case, and
    **`RESUME_CONTEXT_LAUNCHER` pointed at a nonexistent command** — the
    script's own step 4 explicitly defines this error branch
    ("error to stderr and exit non-zero before the move") but the initial
    test list never exercised it; an SDET review round caught the gap.
  - `--consume-only` mode: happy path moves the file and does **not** invoke
    the launcher (assert the recorder file is never created); **calling it
    twice on the same path** (second call after the first already moved the
    file) → non-zero exit, stderr message, no launcher invocation — the
    double-consume race the hook's own fail-open depends on being a clean,
    predictable failure mode (the safety property here is that `mv`'s
    atomicity makes a real race degrade to this same clean-failure case, not
    that concurrent resume attempts can't occur — a single user running two
    sessions against the same `$HOME` is a realistic scenario, so the
    deferred true-concurrency test is scoped on `mv` atomicity, not on usage
    patterns); **`--consume-only` with no resolvable launcher on `PATH`** —
    pins that consume-only mode is launcher-independent (the actual
    production condition when the hook invokes this mode), since every
    existing `--consume-only` test happened to supply a working launcher
    stub even though the code path never resolves one in this mode.
- **`claude/.claude/skills/tests/test_skills.py`** — add tests, mirroring the
  existing doc/skill-invariant style (`test_skill_overrides_documented_in_docs_skills_md`):
  - **Positive write-target invariant** (the durable guard): assert each skill's
    documented *write target* equals its durable path — `handoff/SKILL.md` writes
    to `~/.claude/handoffs/<slug>-handoff.md`, `brief/SKILL.md` to
    `~/.claude/briefs/<slug>-task.md`. Anchor to the write-target construct
    (frontmatter `description` path + the "write … at" body line), **not** a
    blanket "no `/tmp` anywhere in the file" negation — the skills now legitimately
    mention `/tmp` as the *consumed*-tier destination, so a blanket negation
    false-positives on correct content (an SDET review flagged this; it would
    force loosening the test until it guards nothing).
  - **The write-target invariant above is a text-match, not an execution
    test** — it confirms the SKILL.md *prose says* the durable path without
    ever running the `mkdir -p ~/.claude/handoffs && write` recipe, so an
    off-by-one in an interpolated slug variable, or prose that says the right
    thing while the actual recipe writes elsewhere, could slip through
    untested. A second SDET-flagged finding: tag each skill's write recipe as
    a `<!-- HOOK_TEST_FIXTURE: ... -->` block (the same convention
    `plan-review/SKILL.md` already uses for its own marker-script recipes,
    read via `helpers.py`'s `extract_skill_command`) and add a test that runs
    it through `run_skill_command` in an isolated `$HOME`, asserting the
    resulting file actually lands at the literal expected path. This closes
    the gap the prose-only assertion above leaves open. Once the recipe
    also `touch`es and `chmod 600`s the file itself (per the second CISO
    round above), extend this same execution test to assert the file's mode
    is `0600`, using the recipe's own literal placeholder filename (a fixed
    stand-in slug, not a real one — a real invocation substitutes its actual
    descriptive slug before running the recipe).
  - `resume-context.sh` exists and is executable — kept as its own test,
    separate from the doc-consistency check below (an SDET round found an
    earlier draft mixed a real invariant with a prose substring match in one
    test function; splitting them means a benign copy-edit to the resume
    instruction can't fail the executable-bit assertion, and vice versa).
    Both skills reference `resume-context ~/.claude/` literally — a
    doc-consistency check only, explicitly scoped as such (parallel to the
    existing frontmatter write-target check), not a proof that the
    documented invocation actually works end-to-end.
  - **Bash-level test of `resume-context.sh`** via both seams
    (`RESUME_CONTEXT_LAUNCHER` → a stub that records `"$@"`; `RESUME_CONTEXT_TMPDIR`
    → a pytest `tmp_path` so the real `/tmp` is never touched). Cases:
    (i) zero args → non-zero exit, stderr message, no move, launcher **not**
    invoked; (ii) missing source file → non-zero exit, stderr message, launcher
    **not** invoked; (iii) existing source → source file is *gone* AND a copy
    exists under the temp dir AND the launcher was invoked with
    `--append-system-prompt-file <moved-path>`; (iv) source path *containing a
    space* → still moved and launched correctly (proves quoting).
  - Note in the plan (not a durable test): the "does the write-target assertion
    fail against pre-fix content?" check is a one-time anti-tautology sanity step
    at authoring time, not an ongoing guard — the committed test only ever runs
    against post-fix files. The durable guard is the positive write-target
    invariant above.

No `skillOverrides` change (both skills already `name-only`). One
`settings.json` change: registering the new consume-on-read hook (above) under
the existing `PostToolUse` → `Read` matcher. One new hook. No new third-party
dependency.

## Verification

- **Smoke-test the load-bearing flag first — [verified]:** confirmed via
  `claude -p --append-system-prompt-file <file> "<prompt>"` that the flag
  loads file content into the system prompt (the model echoed content only
  present in the appended file). A platform-engineer review round noted the
  original plan didn't say whether this check happens before or after the
  tests are written — it happened first, this session, before any code —
  so the primary path (`--append-system-prompt-file`, not the positional-arg
  fallback) is the one to implement and test against; no ordering ambiguity
  remains.
- `../../../.venv/bin/pytest claude/.claude/` and
  `../../../.venv/bin/ruff check claude/.claude/` from the worktree.
- Manually confirm the *write-target* lines in `handoff/SKILL.md`,
  `brief/SKILL.md`, `docs/skills.md`, and `README.md` name the durable path,
  not `/tmp`. A blanket `/tmp` presence grep is deliberately **not** used —
  each file legitimately mentions `/tmp` once for the consumed tier, which is
  exactly why the automated test anchors to the write-target construct (above)
  rather than a presence/absence scan.
- New `test_skills.py` cases pass. One-time at authoring: confirm the
  write-target assertion actually fails against pre-fix content (anti-tautology
  check — teeth, not an ongoing guard).
- End-to-end by hand: write a handoff (dir auto-created), simulate a fresh
  shell, run `resume-context.sh <path>` → new session starts with the handoff
  in context; the source file is now absent from `~/.claude/handoffs/` and a
  0600 copy exists under the temp dir. Repeat for `/brief`.
- **Same-session path, end-to-end:** write a handoff, then `Read` it directly
  (simulating `/clear` + manual "Read `<path>` and continue") without ever
  invoking `resume-context.sh` — confirm `consume-durable-continuity-file-on-read.sh`
  still moves it out of `~/.claude/handoffs/` afterward. Repeat for `/brief`.
- New `test_consume_durable_continuity_file_on_read.py` and
  `test_resume_context.py` cases pass (both run under the same
  `../../../.venv/bin/pytest claude/.claude/` invocation already listed above —
  no separate command needed).
- Editing two `SKILL.md` files makes `/skill-review` hook-enforced on commit;
  the new shell script and the new hook make `/code-review` dispatch
  `staff-platform-engineer` (shell discipline / hook review via
  `claude-hook-review`). Run both before committing.

## Out of scope

- Todoist integration (linking a written handoff to a Todoist task) — the ticket
  itself flags it "needs more thought"; personal-workflow-specific, not part of
  this fix.
- Any `SessionStart`/`SessionEnd` hook, auto-injection, or age-based pruner —
  considered and rejected above, not deferred.
- Replacing `/handoff` with native `claude --continue`/`--resume` — that path
  serves full-history resume and is complementary; not touched here.
- A retention/cleanup policy for the consumed-tier copies under the temp dir —
  the OS's own temp reaping (reboot-clearing on Linux `/tmp`, per-boot `$TMPDIR`
  on macOS) is the policy; nothing added.
