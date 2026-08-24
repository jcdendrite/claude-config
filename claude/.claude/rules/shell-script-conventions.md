---
paths:
  - "**/*.sh"
  - "**/*.bash"
---

## Shell script conventions

Google Shell Style Guide (2026-07) grounds the quoting, `[[ ]]`, and
`set -e`/`(( ))` guidance below. `shellcheck`, `IFS= read -r`, `mktemp`
portability, and `trap` composition are general shell-scripting practice, not
tied to a specific citation.

- **These are bash conventions, not POSIX `sh`.** The `**/*.sh` glob matches
  by extension only — if the shebang is `#!/bin/sh` (POSIX/dash), `pipefail`,
  `[[ ... ]]`, and array expansions below are syntax errors, so check the
  shebang first.
- **`set -euo pipefail` at the top of bash scripts.** Standalone `(( expr ))`
  that evaluates to 0 trips `set -e` and aborts the script — guard with
  `(( count++ )) || true`. `-e` is also suppressed for commands inside an
  `if`/`&&`/`||` test — check exit codes explicitly there.
- **Run `shellcheck`** (CI or pre-commit) — mechanically catches the quoting,
  `set -e`, and portability issues below; the highest-leverage single addition
  for a script-heavy repo.
- **Quote every expansion:** `"$var"`, `"${arr[@]}"`, `"$(cmd)"`. Google:
  "Always quote strings containing variables, command substitutions, spaces or
  shell meta characters."
- **`[[ ... ]]` over `[ ... ]`.** Google: "`[[ … ]]` is preferred... it
  reduces errors as no pathname expansion or word splitting takes place
  between `[[` and `]]`."
- **`IFS= read -r` for line-reading loops** — a bare `read` strips leading/
  trailing whitespace and mangles backslash escapes.
- **`mktemp` for temp files, never a fixed path** (BSD/macOS `mktemp` needs a
  template argument, e.g. `mktemp -t name`; GNU doesn't). Clean up with a
  SINGLE `trap '...' EXIT` handler — a second `trap ... EXIT` silently
  overwrites the first, so compose multiple cleanup actions into one.
- **`local` for all function-scoped variables** — an unset `local` leaks into
  or collides with the caller's scope.
- **`"${VAR:?message}"` for required inputs** — fails loudly at the point of
  use instead of silently expanding to empty.
- **Match CLAUDE.md's comment-length convention in every `#` block.** State
  each non-obvious fact as one sentence, not a multi-sentence rationale — move
  elaboration or design rationale to `docs/`, cited by path, rather than
  inlining it in the comment.
