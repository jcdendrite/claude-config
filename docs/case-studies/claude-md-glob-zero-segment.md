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
