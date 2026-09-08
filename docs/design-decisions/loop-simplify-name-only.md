# `loop` and `simplify` flipped from `off` to `name-only`

*2026-06-17. Formerly `docs/design-decisions.md` §17.*

Before Claude Code v2.1.129, `skillOverrides: "off"` was the only way to exclude a bundled skill's description from the listing budget. Two bundled skills — `/loop` and `/simplify` — were set to `"off"` to avoid budget pressure while keeping them out of the always-loaded listing. Neither is central to this repo's review-pipeline workflow, but both have occasional on-demand utility: `/loop` for recurring-interval task automation, `/simplify` for ad-hoc code simplification when `/code-review`'s specialist routing is heavier than the task warrants.

`name-only` (shipped in v2.1.129) achieves the original goal at zero additional cost: the description stays out of budget, the skill remains slash-invocable and model-invokable by exact name, and no `"off"` entry silently blocks access. Both entries are moved from the `"off"` group to the `"name-only"` group in `settings.json`.

`skillOverrides` does not apply to plugin skills — plugin visibility is managed via the `/plugin` command and `enabledPlugins` in `settings.json`. This is why the two disabled official plugins (`claude-md-management`, `claude-code-setup`) remain in `enabledPlugins: false` rather than being handled via `skillOverrides`. Source: [Claude Code settings — skillOverrides](https://code.claude.com/docs/en/settings).
