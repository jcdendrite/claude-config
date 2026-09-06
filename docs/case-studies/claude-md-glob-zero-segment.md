# Zero-segment `**/` glob match: measured, not sourced

*Part of the [claude-config case studies](../case-studies.md).*

**The question.** Does a `**/`-led `paths:` glob (e.g. `**/CLAUDE.md`) also
match a root-level file with zero intermediate segments, or only a file
nested under at least one directory? `code.claude.com/docs/en/memory`'s
"Path-specific rules" section, the primary source `rule-authoring-conventions.md`
cites for the `paths:` dialect, does not say either way.

**Method.** Five one-shot sessions, each instrumented with an
`InstructionsLoaded` hook filtering on `load_reason: path_glob_match`,
probed which stowed rule files load on a given file read.

**Two positive controls ran first.**

- A depth-3 read (`claude/.claude/hooks/_lib.sh`) confirmed the instrument
  fires at depth > 0 and that `**/` traverses a dot-directory.
- A repo-root `CLAUDE.md` read confirmed depth-zero loading fires at all in
  this run mode.

Without the second control, a null result on the decisive trial below would
have been uninterpretable. It could mean either "`**/` doesn't match root"
or "nothing loads on a root-level read in this mode at all."

**The decisive trial.** Read repo-root `install.sh` against
`shell-script-conventions.md`, whose `paths` list is
`["**/*.sh", "**/*.bash"]` with no bare-basename entry. Nothing but a
`**/`-led glob could have matched this read. The hook reported a
`path_glob_match` load, confirming the zero-segment match.

**Replication.** The same result reproduced on `install-dev.sh`.

**Interactive-mode observation.** All five trials above ran in `-p`
(non-interactive) mode. A single follow-up observation in an ordinary
interactive session read repo-root `install.sh` and produced the
harness's own inline "Loaded `claude/.claude/rules/shell-script-conventions.md`"
notice immediately after the read completed. The `InstructionsLoaded` hook
did not log an entry in this session, for an undiagnosed reason — the
`settings.local.json` matcher entry produced no output. This observation
therefore rests on the harness's load-notice UI, not the instrumented hook
log the five `-p` trials used.

The notice's timing still pins the load reason to `path_glob_match`, the
only fit among the harness's five documented load reasons (`session_start`,
`nested_traversal`, `path_glob_match`, `include`, `compact`):

- It appeared inline after a specific file read rather than at session
  start, ruling out `session_start`.
- `nested_traversal` doesn't apply to `.claude/rules/*.md` glob rules at
  all — that load reason is specific to CLAUDE.md/AGENTS.md
  directory-nesting discovery, a different mechanism.
- `include` requires an explicit `@path` import directive, and nothing in
  this session used one.
- `compact` fires only on a context-compaction reload, which hadn't
  occurred yet in this fresh session.

**Limit.** The interactive-mode observation above is single-instance and
depends on the load-notice workaround described above. A future null
result from the same setup would be uninterpretable on its own: it could
mean `**/` doesn't match root in interactive mode, or that the hook still
isn't logging. Diagnose the hook first, then run the two positive controls
from the `-p` protocol above in interactive mode, before concluding
anything from a null result about the glob match itself.

## Sources

- `InstructionsLoaded` hook, filtered on `load_reason: path_glob_match` —
  the instrument used for all five `-p`-mode trials.
- Five one-shot `-p` sessions against this repo's own stowed rules
  (`shell-script-conventions.md`, `claude/.claude/rules/`), run 2026-09-03.
- One interactive-session observation via the harness's inline load
  notice (not the hook), same repo, same rule, 2026-09-03.

## Addendum: `claude-md-conventions.md`'s own globs, and a self-inflicted false negative

**The question.** The trials above establish that a `**/`-led glob matches
a zero-segment file in general. None tested `claude-md-conventions.md`
itself — the rule whose five globs this whole investigation exists to
support:

- `**/CLAUDE.md`
- `**/AGENTS.md`
- `**/CLAUDE.local.md`
- `**/.claude/CLAUDE.md`
- `**/.claude/AGENTS.md`

**A false negative, and its cause.** An uninstrumented interactive
session read `claude-md-conventions.md`'s own content directly, then read
four separate CLAUDE.md/AGENTS.md-family files:

- Global scope (`~/.claude/CLAUDE.md`).
- Project scope (repo-root `CLAUDE.md`).
- A nested file never preloaded at session start (`claude/.claude/CLAUDE.md`).
- A fresh `AGENTS.md` outside any repo.

None produced a load notice, against a working same-session positive
control: the earlier read of the rule file had correctly triggered two
*other* rules matching its own path. This looked like conclusive evidence
that CLAUDE.md/AGENTS.md-named reads never reach `path_glob_match` at
all.

It wasn't. The evidence is consistent with a different mechanism: once a
rule's own content has been pulled into context via `Read`, the harness
may not re-inject that rule later in the same session, regardless of
whether a subsequent file read would otherwise match its `paths:` glob.
The session's positive control and its four negative trials shared
exactly this read, so the correlation was perfect by construction. The
two rules that fired had never been read directly. The one that didn't
fire had been.

**Limit.** No trial here isolates this mechanism directly — a session
that reads a rule's content, then reads a second file that should
independently retrigger its `paths:` glob, to confirm zero re-injection
under controlled conditions, has not been run. Treat "the harness
suppresses re-injection of an already-read rule" as the explanation that
best fits this one session's correlation, not as a measured result.

**Pitfall, for future trials.** Never read the rule under test in a
`paths:` measurement session. Doing so silently disables the very signal
the trial is trying to observe, and produces a false negative
indistinguishable from "this glob never matches" without hook
instrumentation to catch it.

**The clean trial.** A fresh `-p` session, instrumented with the same
`InstructionsLoaded` hook as the five trials above, read a throwaway
`positive-control.sh` and a throwaway `AGENTS.md` — in a scratch directory
unrelated to any repo, and without reading `claude-md-conventions.md` or
any other rule file. The hook logged two `path_glob_match` entries:

```json
{"load_reason":"path_glob_match","file_path":".../shell-script-conventions.md","globs":["**/*.sh","**/*.bash"],"trigger_file_path":".../positive-control.sh"}
{"load_reason":"path_glob_match","file_path":".../claude-md-conventions.md","globs":["**/CLAUDE.md","**/AGENTS.md","**/CLAUDE.local.md","**/.claude/CLAUDE.md","**/.claude/AGENTS.md"],"trigger_file_path":".../AGENTS.md"}
```

`claude-md-conventions.md`'s glob fires correctly.

**Scope looks like it isn't the relevant axis.** The trigger file sat in
an arbitrary scratch directory under `/tmp` — neither the global
`~/.claude` tree nor any project root. The match fired anyway, consistent
with a plain path/basename glob that carries no concept of "global" vs.
"project" scope. This is a single scratch-directory trial, not replicated
against a second non-global, non-project location — treat "fires
identically wherever it sits" as the reading this one trial supports, not
as confirmed across every possible location.

### Sources (addendum)

- `InstructionsLoaded` hook log, one clean `-p`-mode trial against a
  scratch directory outside any repo, run 2026-09-05.
- The earlier same-session false negative: four uninstrumented
  interactive-mode reads (global CLAUDE.md, project-root CLAUDE.md, a
  nested non-preloaded CLAUDE.md, and a fresh AGENTS.md), same date.
