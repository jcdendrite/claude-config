# Changelog

All notable changes to `claude-config` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`skill-management` self-provisions `pyyaml`** — plugin bumped to 2.1.0; a `SessionStart` hook installs `pyyaml` into a persistent venv at `${CLAUDE_PLUGIN_DATA}/venv` on first session and after manifest changes, and the commit-time hook prefers that venv's `python` (falling back to system `python3`). Consumers no longer need to run `python3 -m pip install pyyaml` after installing the plugin.
- **Structural SKILL.md validator** — `description + when_to_use ≤ 1536` chars and strict-YAML frontmatter checks shipped with the `skill-management` plugin; the existing commit-time hook now runs the validator on every staged SKILL.md (#290)
- **check-runner subagent** — Haiku agent for suite-level command dispatch; returns structured pass/fail verdicts and writes full output to a temp file so the parent context doesn't inhale raw output (#189)
- **Foundation-critique gates** — plan-review, plan-it, reviewer agents, and CLAUDE.md now include compounding-defensive-layers and wrong-foundation prompts to catch design tells early (#190)
- **Backtick escaping block** — `deny-escaped-backticks-in-pr-body.sh` blocks `gh pr create`/`gh pr edit` bodies that contain `\`` sequences, preventing shell-expansion surprises in heredoc-constructed PR bodies (#192)
- **`~/.local/bin` script wrappers** — `analyze-context`, `token-analyzer`, `cleanup-merged-branches`, and `marker` are now accessible as short-form commands without the full path (#184)
- **Proactive handoff + marker re-injection** — `session-marker-dashboard.sh` now fires on `compact` events (not only session start), restoring gate-marker state automatically after auto-compaction; CLAUDE.md prompts Claude to suggest `/handoff` at ~60% context (#172)
- **Ready-for-review active-marker ceiling** — 90-minute hard expiry on the bypass marker prevents stale bypasses from carrying across sessions unnoticed (#173)
- **Verify-class subagent routing** — `/ready-for-review` now dispatches `pytest`/`ruff`/`tsc`-class commands through the `check-runner` subagent so suite output doesn't expand the parent context window (#171)
- **Auto-discovery in cleanup-merged-branches** — script now discovers merged branches itself via `gh pr list`; no manual branch-name input required (#165)
- **`.env.example` reads allowed** — `deny-env-reads.sh` now permits `.env.example`, `.env.template`, and `.env.sample` reads while continuing to block all other `.env.*` variants (#175)
- **PATH-resolved check in review-permissions** — `/review-permissions` now verifies that referenced scripts resolve on PATH, not just that the allow-rule string looks correct (#186)

### Changed

- **Plugin renamed `skill-review` → `skill-management`** — bumped to 2.0.0 (breaking — downstream installers must `claude plugin uninstall skill-review && claude plugin install skill-management@claude-config --scope project`); between uninstall and install there is a brief window with no SKILL.md gate active, do not commit SKILL.md changes during that window (#290)
- **CI workflow renamed** — workflow display name changed from "Hook tests" to "Tests"; job id `tests` (the branch-protection check context) unchanged (#169)
- **Handoff format inlined** — `/handoff` format moved inline into the command file; dependency on `/compact` docs removed (#176)
- `_marker_lib_repo_hash` extracted as a shared helper across `require-*` hooks and `marker.sh`, eliminating duplicated hash-computation logic (#168)

### Fixed

- Two false-positives in `enforce-marker-script-shape.sh` that blocked legitimate `marker.sh` invocations with certain argument orderings (#187)
- `cleanup-merged-branches.sh` now skips locked worktrees rather than erroring; a follow-up fix adds unlock-and-remove for worktrees that can be released (#174, #177)
- Memory-write hook debounce replaced with active-marker bypass pattern, eliminating per-turn UUID thrash (#170)
