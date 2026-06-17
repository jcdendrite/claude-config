# Flip two bundled skills from `off` to `name-only`

## Context

The goal is to make two Claude Code **bundled** skills — `loop` and `simplify` —
invokable by name again, without paying any description-budget cost.

These skills were set to `skillOverrides: "off"` in the stowed
`claude/.claude/settings.json` **before** Claude Code shipped the `name-only`
override value (v2.1.129). At the time, the only way to keep a low-value skill's
description out of the always-loaded listing budget was to disable it entirely
(`off`), which also removed it from the `/` menu and made it non-invokable. The
`name-only` value (installed version is 2.1.179) gives the missing middle state:
**invokable by exact name and present in the `/` menu, with the description
excluded from the listing budget**. Flipping `off → name-only` restores by-name
invokability at no budget cost, because a name-only description is never loaded.

Intended outcome: `loop` and `simplify` become invokable (`/loop`, `/simplify`)
while staying out of the description budget; the remaining ten bundled skills stay
`off`; the skill contract test and the two skill docs stay accurate and green.

### Scope was narrowed during planning

The original request named "loop and claude-md-management" and asked to consider
others. Investigation and primary-source verification reshaped the scope:

- **`claude-md-management` is a marketplace plugin, not a bundled skill.** It is
  disabled via `enabledPlugins`, a different mechanism than `skillOverrides`. The
  Claude Code settings doc states `skillOverrides` *"does not apply to plugin
  skills, which are managed through `/plugin`."*
  ([settings](https://code.claude.com/docs/en/settings)) There is **no name-only
  state for a plugin skill** — only fully enabled or disabled. The user chose to
  keep `claude-md-management` and the other disabled official plugin
  (`claude-code-setup`) **disabled**, since full-enable would load their skill
  descriptions into budget and auto-trigger (and `claude-md-improver` would dual-fire
  with this repo's own `ai-instruction-and-memory-files` skill).
- **`init`** was initially in scope only as a mis-mapping of "claude-md-management";
  the user dropped it. It stays `off`.
- **`simplify`** was added by the user from the remaining bundled `off` set.

So the achievable, in-scope change is exactly: `loop` and `simplify`, `off → name-only`.

## Approach

Change two string values in the `skillOverrides` block from `"off"` to
`"name-only"`, then bring the skill contract test and the two docs into line.

The crux is the test contract. `claude/.claude/skills/tests/test_skills.py` derives
its name-only skill set from settings (`_name_only_skills()` returns every key whose
value is `name-only`) and runs **three** per-skill contracts that each assume the
entry is backed by a repo/stowed `SKILL.md`: a repo `SKILL.md` exists
(`test_name_only_skill_has_skill_file`, line 301); the body does not carry
`disable-model-invocation: true` (`test_name_only_skill_does_not_carry_disable_flag`,
line 310); and any **bare** key (no `plugin:` prefix) resolves to a stowed `SKILL.md`,
else it is presumed a mis-keyed plugin skill
(`test_plugin_scoped_name_only_key_is_qualified`, line 320). Bundled skills have
**no repo `SKILL.md`** and are correctly bare-keyed (they are not plugin skills), so:

- `test_name_only_skill_has_skill_file` would fail (`.exists()` is False).
- `test_name_only_skill_does_not_carry_disable_flag` would *error*, not just fail —
  `_skill_description()` calls `read_text()` on a path that does not exist and raises
  `FileNotFoundError`.
- `test_plugin_scoped_name_only_key_is_qualified` would fail — its bare-key branch
  (lines 329–335) asserts `SKILLS_DIR/<name>/SKILL.md` exists, which is False for a
  bundled skill, producing the misleading "Plugin skills require the qualified
  'plugin:skill' key form" message.

Fix: introduce an explicit `BUILTIN_NAME_ONLY_SKILLS` allowlist (`{"loop",
"simplify"}`) and exempt those entries from all three repo-`SKILL.md`-dependent
contracts, while keeping the existing guarantee intact for repo skills. An explicit
allowlist (not "skip anything without a SKILL.md") preserves typo detection: a
misspelled repo skill name in settings still fails the contract because it is neither
in the allowlist nor backed by a `SKILL.md`.

### Why name-only, not removing the override (letting it default to `on`)

`on` would reload each skill's description into the listing budget **and** re-enable
description auto-trigger. The intent is by-name invocability only, with zero budget
cost — exactly `name-only`. This mirrors the established precedent for
`error-handling` (design-decisions §11). (Note: the `agent-review`/`skill-review`
precedent in §15 is only reliable for `agent-review`, a stowed user skill;
`skill-review` is a plugin skill, so its override is a no-op — see Out of scope.)

### Stow-distribution consideration

This `skillOverrides` block lives in the committed, stow-distributed
`claude/.claude/settings.json`, so the flip ships to every downstream clone. That is
appropriate: these are general-purpose bundled commands and `name-only` adds no
budget cost for anyone. On pre-v2.1.129 clients the override is silently ignored and
the skill falls back to `on` (description loaded) — harmless for bundled skills, and
already documented in `docs/skills.md`.

## Critical files

1. **`claude/.claude/settings.json`** — in `skillOverrides`, change `loop` and
   `simplify` from `"off"` to `"name-only"`. Relocate the two keys into the existing
   name-only group and keep the remaining `off` group (now ten entries) in its
   current alphabetical order, so each group stays internally consistent.
   Do **not** touch `enabledPlugins` (both official plugins stay `false`).

2. **`claude/.claude/skills/tests/test_skills.py`** —
   - Add `BUILTIN_NAME_ONLY_SKILLS: set[str] = {"loop", "simplify"}` near
     `COMMAND_SKILLS` (line ~193), with a docstring explaining these are bundled
     Claude Code skills set to name-only with no repo `SKILL.md` by design, and the
     accepted limitation that the test cannot introspect the binary to confirm a name
     is a genuine bundled skill (a typo'd bundled name in settings is silently ignored
     by Claude Code rather than erroring).
   - `test_name_only_skill_has_skill_file` (line ~301): exempt allowlist entries via
     an **in-body** `pytest.skip(...)` — **not** by filtering the parametrize source,
     so the built-ins still flow through `NAME_ONLY_SKILLS` and the set-equality test
     below sees them as live name-only entries. Update the assertion message to mention
     adding to `BUILTIN_NAME_ONLY_SKILLS` as a third resolution alongside "remove the
     entry / create the SKILL.md".
   - `test_name_only_skill_does_not_carry_disable_flag` (line ~310): in-body skip for
     allowlist entries (no frontmatter to read).
   - `test_plugin_scoped_name_only_key_is_qualified` (line ~320): add an in-body skip
     for allowlist entries, placed **after** the existing `":" in skill_name` skip and
     **before** the bare-key `stowed_path` assertion (line ~329). Bundled skills are
     legitimately bare-keyed with no stowed `SKILL.md`; without this skip the bare-key
     branch fails them with the spurious "Plugin skills require the qualified
     'plugin:skill' key form" message.
   - Add a single **set-equality** test, `test_builtin_name_only_allowlist_matches_settings`:
     assert `{n for n in _name_only_skills() if not _skill_file(n).exists()} == BUILTIN_NAME_ONLY_SKILLS`.
     This makes the allowlist self-maintaining — it fails loudly on any drift: a
     name-only bundled skill missing from the allowlist, a stale allowlist entry no
     longer name-only in settings, or an allowlist entry that is actually a repo skill
     (it would have a `SKILL.md`, so it would not appear in the computed set → mismatch).
   - Update the module docstring's name-only bullet (line ~8) to note the bundled sub-case.
   - **Verified-invariant note (no code needed):** built-ins never enter
     `_model_invokable_skills()` (lines ~152–183) — that function only iterates repo
     skill directories, and the two built-ins have none. The `budget_excluded`
     membership for them is a harmless no-op. This is the mechanism that prevents a
     `FileNotFoundError` cascade through the five other test classes that read each
     model-invokable skill's body. State this explicitly in the implementation so it is
     not re-derived.
   - **Reuse**, do not reimplement: `_name_only_skills()`, `_skill_file()`,
     `_settings_skill_overrides()` are already present and used as-is.

3. **`docs/skills.md`** —
   - Skill-override schema section (line ~47): add a one-line caveat to the
     `skillOverrides` reference that the setting does **not** apply to plugin skills
     (plugin visibility is managed via `/plugin` / `enabledPlugins`), citing the
     settings doc. This is the canonical home for the factual caveat.
   - "Bundled skills disabled by default" section (line ~58): change line 60 prose
     "Twelve bundled skills are disabled" → "Ten bundled skills are disabled", and add
     a sentence that two bundled skills (`loop`, `simplify`) are set to `name-only`
     instead — invokable by name with no description-budget cost.
   - Move the `/loop` (line 68) and `/simplify` (line 73) rows out of the disabled
     table into a short companion table listing the two bundled name-only skills with
     one-line rationale each (rationale shifts from "why disabled" to "kept invokable
     by name for occasional deliberate use"). **Each row must keep the exact
     `` | `/<name>` | `` cell format**, because `test_skill_overrides_documented_in_docs_skills_md`
     (test_skills.py line ~651) greps for the literal marker `` | `/loop` | `` /
     `` | `/simplify` | `` for every non-`on` override — the rows may move tables but
     must not lose that format. Also update that test's docstring (lines ~654–656),
     which says name-only entries are "repo skills available by name", to acknowledge
     the bundled name-only sub-case.

4. **`docs/design-decisions.md`** — add a dated `## 17.` entry (latest is §16)
   recording: (a) the `off → name-only` flip for `loop` and `simplify` and its
   pre-`name-only` history / zero-budget rationale; (b) the verified finding that
   `skillOverrides` does not apply to plugin skills, which is why the disabled official
   plugins (`claude-md-management`, `claude-code-setup`) could not be made name-only and
   stay disabled. Keep it concise and consistent with the §11/§15 precedent entries. Do
   **not** prescribe a `skill-review` remediation here — that is being handled separately.

## Verification

- `.venv/bin/pytest claude/.claude/skills/tests/test_skills.py` — the three name-only
  contracts (`test_name_only_skill_has_skill_file`,
  `test_name_only_skill_does_not_carry_disable_flag`,
  `test_plugin_scoped_name_only_key_is_qualified`) skip the two bundled skills and pass;
  the new set-equality test
  (`test_builtin_name_only_allowlist_matches_settings`) passes;
  `test_skill_overrides_documented_in_docs_skills_md` stays green (the two doc rows
  retain the `` | `/<name>` | `` format); no `FileNotFoundError` cascade through the
  model-invokable test classes. Run from a worktree as `../../../.venv/bin/pytest …`.
- `.venv/bin/ruff check claude/.claude/skills/tests/test_skills.py`.
- `jq .skillOverrides claude/.claude/settings.json` — confirm `loop`/`simplify` read
  `name-only` and the other ten read `off`; `jq .enabledPlugins …` — confirm both
  official plugins still read `false`.
- Manual (change is live via stow symlink, no reinstall): confirm `/loop` and
  `/simplify` appear in the `/` menu and are invokable, and that their descriptions are
  not in the always-loaded listing (no budget regression via `/doctor`).
- Sanity-check both docs render correctly and the bundled-skill counts/tables match
  settings.

## Out of scope

- **`claude-md-management` and `claude-code-setup` plugins** — name-only is impossible
  for plugin skills; the user chose to keep both `enabledPlugins: false`. No change.
- **`skill-review: name-only` no-op** — `skill-review` is a plugin skill, so its
  committed `skillOverrides` entry has no runtime effect and its description still
  loads. A separate session is investigating remediation; this plan does not touch it.
- **`init` and the other nine `off` bundled skills** (`claude-api`,
  `fewer-permission-prompts`, `keybindings-help`, `review`, `run`, `schedule`,
  `security-review`, `update-config`, `verify`) — stay off per the user's decision.
- The pre-existing working-tree modifications to both `settings.json` files (git status)
  are likely per-session `effortLevel`/`model` overrides — implementation starts from a
  clean main tip (via `branch-creation`) and must not commit those session overrides.
