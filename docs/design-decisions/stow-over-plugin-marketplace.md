# Stow distribution over plugin marketplace

*Formerly `docs/design-decisions.md` §5.*

GNU Stow installs the config as symlinks from `claude/.claude/` into `~/.claude/`. A `git pull` updates the repo, and the symlinks already point into it — the installed state is always at HEAD with no reinstall step. New skills, hooks, and agents appear in `~/.claude/` the moment the pull lands. The tradeoff: this requires `stow`, a Unix-like system, and a manual `./install.sh` re-run when a new top-level child is added to `claude/.claude/` (Stow links each immediate child individually, so a brand-new subdirectory only appears in `~/.claude/` after re-linking). Windows is not supported.
