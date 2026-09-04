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

**Limit.** Every trial ran in `-p` (non-interactive) mode. The two run
modes were not shown to load instructions identically, so this result is
not shown to transfer to an interactive session. If a rule is ever observed
not loading on a repo-root file read in an interactive session, re-run this
check in that mode before looking elsewhere.

## Sources

- `InstructionsLoaded` hook, filtered on `load_reason: path_glob_match` —
  the instrument used for all five trials.
- Five one-shot `-p` sessions against this repo's own stowed rules
  (`shell-script-conventions.md`, `claude/.claude/rules/`), run 2026-09-03.
