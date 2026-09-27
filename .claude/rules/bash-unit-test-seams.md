---
paths:
  - "claude/.claude/hooks/**/*.sh"
  - "claude/.claude/scripts/**/*.sh"
---

## Bash unit-test seams

Where an extracted pure-bash helper belongs, in preference order:

1. Extend an existing sibling lib whose domain already covers it.
2. `claude/.claude/hooks/_lib.sh`, once two or more hooks need the helper.
3. A new `_<topic>-lib.sh` file beside the script, when neither above applies.

Already-seamed libs, as precedent:
- `claude/.claude/scripts/_worktree-lib.sh`
- `claude/.claude/scripts/_stow_migration_lib.sh`
- `claude/.claude/hooks/_lib.sh`, with six lib-level test files:
  `test_lib.py`, `test_lib_reviewer_round_state.py`,
  `test_lib_append_line_locked.py`, `test_lib_worktree_collision_guard.py`,
  `test_marker_lib.py`, `test_config_lib.py`.

**Naming: `_<topic>-lib.sh`.** `_stow_migration_lib.sh` is the older
spelling and is not being renamed.

**A new bare `_*-lib.sh` sidecar under `claude/.claude/hooks/` fails CI
today.** `claude/.claude/hooks/tests/test_hook_alignment.py`'s
`_all_hook_files()`/`_HELPER_LIBRARY_NAMES` excludes only two exact
filenames, so any other file there gets swept in as a hook. Land a
hook-side extraction inside the existing `_lib.sh` instead, until that
guard is widened.
