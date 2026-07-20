---
paths:
  - "**/*.sh"
  - "**/*.bash"
---

## Shell script conventions

Sources verified against the Google Shell Style Guide (2026-07) for quoting,
`[[ ]]`, and the `set -e`/`(( ))` caveat. `set -e`'s broader
if/`&&`/`||`-condition exemption is documented Bash-manual behavior, not a
Google-guide quote. `shellcheck`, `IFS= read -r`, `mktemp` portability, and
`trap` composition are well-established shell-scripting practices not pinned
to a fetched source this session — re-confirm at point of use if precision
matters.

- **These are bash conventions, not POSIX `sh`.** The `**/*.sh` glob can't
  read the shebang. Google's style guide declares "Bash is the only shell
  scripting language permitted for executables" for its own repos, so it never
  needs to hedge — but not every repo makes that declaration. If the script's
  shebang is `#!/bin/sh` (dash/POSIX), `pipefail`, `[[ ... ]]`, and array
  expansions below are syntax errors — check the shebang before applying them.
- **`set -euo pipefail` at the top of bash scripts.** Know the caveats:
  Google's guide warns a standalone `(( expr ))` returns a false exit status
  when `expr` evaluates to `0` — under `set -e` this **aborts the script
  immediately** at that line, not "continues past it." Google's own example:
  `set -e; i=0; (( i++ ))` "will cause the shell to exit" — the post-increment
  expression evaluates to the old value (`0`), so the command "fails" even
  though the increment worked. Guard arithmetic that can legitimately hit
  zero: `(( count++ )) || true` or `: $(( count++ ))`. Separately, `-e` is
  also suppressed for any command that is part of an `if`/`&&`/`||` test
  (documented Bash behavior, not this specific Google-guide passage) — don't
  treat `-e` as a substitute for checking exit codes at those sites.
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
