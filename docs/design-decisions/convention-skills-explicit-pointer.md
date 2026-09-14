# Convention skills wired by explicit pointer, not description-based auto-trigger

*Formerly `docs/design-decisions.md` §15.*

`test-conventions` and `sql-query-conventions` carry `user-invocable: false` and TRIGGER blocks, but across thousands of transcripts neither skill fired via description-based auto-trigger in practice. The trigger surface for each is too broad — any SELECT query, any test file — to scope reliably in a description, and description matching fires (or fails to fire) based on session context the author cannot observe.

The repair is explicit pointer wiring: every consumer that should consult the skill is told to `Read` the `~/.claude/skills/<skill>/SKILL.md` path directly (the `Read` tool expands `~`). This is the same pattern `staff-backend-engineer` uses for `error-handling` ([§11](code-writer-self-review.md)'s "reading the file directly, so its content enters the agent's own reasoning pass"). Consumers wired:

- **`code-writer`** (write-time): reads `test-conventions` when writing test code; reads `sql-query-conventions` when writing a read-path SELECT query.
- **`staff-sdet`** (reviewer): reads `test-conventions` before citing a §N section, which also runs the skill's Step 0 project-layer glob.
- **`staff-backend-engineer`** (reviewer): reads `sql-query-conventions` when evaluating pagination and read-path query design.
- **`code-review`** (dispatcher): inline pointer to invoke `test-conventions` on test-code changes and `sql-query-conventions` on performance-sensitive paths.

Because `Read`-based consumption never registers as a `Skill` invocation, a usage audit (e.g. `/doctor`) undercounts real consultation for every skill wired this way — `test-conventions`, `sql-query-conventions`, and `error-handling` (via `staff-backend-engineer`'s pointer above) alike. A low or zero invocation count for any of these three reflects this wiring mechanism, not disuse; whether a given skill's count lands at exactly zero additionally depends on whether it also has a broader `Skill`-invoke pathway (e.g. `code-review`'s dispatcher pointers) firing independently of the Read path.

Both skills are moved to `skillOverrides: name-only` following the `error-handling` precedent. The TRIGGER blocks and `user-invocable: false` frontmatter are kept: the test suite's `_specialist_skills()` discovery relies on `user-invocable: false` to determine which skills require TRIGGER discipline, and graceful degradation on older clients (pre-v2.1.129) means the description-based path is still available as a fallback.

The same principle was extended to `agent-review` — a dispatcher-reached reviewer skill that carries TRIGGER blocks but is always invoked by name from `/code-review` (SKILL.md:241), never by description auto-trigger. Moving it to `skillOverrides: name-only` freed its description from the always-loaded listing budget. The TRIGGER blocks are kept for graceful degradation on pre-v2.1.129 clients.

`skill-review` was a candidate for the same treatment, but it is plugin-scoped (`plugins/skill-management/`), and **plugin skills are categorically exempt from `skillOverrides`** — neither a bare key nor a qualified `plugin:skill` key takes effect (see [Override skill visibility from settings](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings)). Instead, `skill-review`'s description is minimized: the TRIGGER/DO-NOT-TRIGGER blocks are stripped — they were always-loaded permanent cost with zero routing value, since the skill is always dispatched by name from `/code-review` and the `require-skill-review` hook, never by description auto-trigger. `user-invocable: false` is kept. The asymmetry between `agent-review` (user-scope, name-only) and `skill-review` (plugin-scope, description minimized) reflects the user-scope vs plugin-scope difference.
