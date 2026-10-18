---
paths:
  - "**/*.sh"
  - "claude/.local/bin/*"
  - "plugins/lovable-cloud/scripts/*"
---

## Shell floor

- Bash-4+ constructs are allowed in this repo's shell files, and bash 3.2 portability is not a review finding.
- Shipped shell code targets the bash floor in `README.md` § "Requirements".
- `sort -V` and `--version-sort` stay banned, because BSD `sort` lacks them (a userland rule, not a bash-version one).
- The code above the floor check, and above a hook's `_lib.sh` bootstrap, must still parse on bash 3.2 (see `docs/hooks.md` § "Bash version").
- That applies to files that source `_config.sh` or a `_lib.sh`.
- Scripts and plugin files that reach no floor check are not bound by it.
