"""Isolation for the review bench's local git calls (archive, add, commit, diff, show).
`isolated_git_environment` removes the engineer's system and global git config, system
and global attributes, and `GIT_*` variables from those calls. The repository a call
runs in still applies its own `.git/config`, `.git/info/attributes`, and tracked
`.gitattributes`. The miners' git calls do not use it, and a fetch there may need the
engineer's credential configuration.
"""
from __future__ import annotations

import os

# Appended to a `git diff` or `git show` argv. Each flag overrides one engineer setting
# that rewrites diff text: `diff.external` or `GIT_EXTERNAL_DIFF`, `diff.<driver>.textconv`,
# and `color.ui` or `color.diff`.
DIFF_TEXT_ARGS = ("--no-ext-diff", "--no-textconv", "--no-color")


def isolated_git_environment() -> dict[str, str]:
    """The process environment for a local git call, with:
    - every `GIT_*` variable removed, which covers the `git rev-parse --local-env-vars`
      set that redirects git to another repository, plus `GIT_EXTERNAL_DIFF` and
      `GIT_DIFF_OPTS`;
    - the global and system config files disabled (`GIT_CONFIG_GLOBAL`, which needs git
      2.32 or later and is ignored by an older git, and `GIT_CONFIG_NOSYSTEM`);
    - `core.attributesFile` pointed at the null device, since git otherwise reads
      `$XDG_CONFIG_HOME/git/attributes` (or `~/.config/git/attributes`) and a `filter` or
      `diff` driver named there would run;
    - the system attributes file disabled (`GIT_ATTR_NOSYSTEM`).
    A repository's own `.git/config`, `.git/info/attributes`, and tracked
    `.gitattributes` still apply."""
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",  # the system attributes file is root-owned, so its effect is not behaviorally testable
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.attributesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
    })
    return environment
