# `pyright-lsp` is installed per contributor, at local scope

*2026-10-15.*

`install-dev.sh` installs the plugin at local scope, and this repo's committed `.claude/settings.json` does not enable it. A project-scope entry would reach every stow consumer who runs `./install.sh` from a checkout, because `install.sh` installs every plugin that file enables. Local scope records the plugin only in the checkout's gitignored `.claude/settings.local.json`. The script never registers `claude-plugins-official`, because registration applies to the whole profile. The sibling decision [official-marketplace-not-preregistered.md](official-marketplace-not-preregistered.md) stopped this repo from adding that registration, while a profile that already had it keeps it until its owner removes it by hand. A contributor who registers it for this plugin takes on, across their whole profile, the session-start clone that the sibling decision describes. The script checks for `pyright-langserver` but never installs it, because the recommended installer, `pipx`, is not a declared dependency and installs outside `.venv`. Otherwise the script installs only `requirements-dev.txt`'s pinned dependencies. The sibling decision stands, because the stowed `claude/.claude/settings.json` still declares nothing from this marketplace.

See README's [Tests](../../README.md#tests) section for the install steps themselves.

## Sources

- https://code.claude.com/docs/en/plugins/install
- https://code.claude.com/docs/en/plugins/loading
- https://code.claude.com/docs/en/settings-reference
- https://code.claude.com/docs/en/settings
- `docs/design-decisions/official-marketplace-not-preregistered.md`
- `.claude/plans/lsp-tooling-evaluation.md`
