# Evaluate installing a Python LSP for this repo

## Context

Enable `pyright-lsp` (Python code-intelligence) for claude-config itself, scoped to this repo only — not to fix stale `file:line` references (LSP doesn't touch that), but for its own real benefit: automatic post-edit diagnostics and precise navigation across the repo's 149 Python files (~110K lines, mostly hooks and their tests), which today get no diagnostics feedback beyond a manually-run `ruff check` / `pytest`.

The engineer's original framing was that installing a Python and shell LSP would make `file_path:line_number` references (as seen throughout the merged discovery-audit-remediation-plan, the PR referred to as "PR 742," which embeds 162 such references) resilient to drift during a large refactor. That specific premise doesn't hold — verified below — so this plan proceeds on the narrower, real benefit instead, per the engineer's explicit go-ahead after seeing that finding.

## Approach

Add `pyright-lsp@claude-plugins-official` to this repo's own `.claude/settings.json` (`enabledPlugins`, project scope), and document the companion binary install for contributors. Skip a shell LSP — none exists in Anthropic's official marketplace, and this repo's shell surface (76 files, ~17K lines, ~227 lines/file average) is small enough that grep-based search is already adequate. Skip pushing the plugin into the stow package (`claude/.claude/settings.json`) — the engineer confirmed this repo only, not every project on the machine.

**Root problem:** the engineer wants Claude Code sessions in this repo to be faster/cheaper, and hypothesized LSP as the mechanism, citing stale line references in large-refactor plans as the motivating symptom.

**Givens:**
- Claude Code's LSP integration is opt-in via official marketplace "Code Intelligence" plugins, each wrapping a separately-installed language-server binary — this is how Claude Code ships LSP support; not a constraint this plan can or should change. [verified: code.claude.com/docs/en/discover-plugins]
- No shell/bash entry exists in the official plugin table (C/C++, C#, Go, Java, Kotlin, Lua, PHP, Python, Rust, Swift, TypeScript only) — Anthropic's own catalog, outside this plan's reach to add to. [verified: code.claude.com/docs/en/discover-plugins]

**Mechanisms:**

- **`enabledPlugins["pyright-lsp@claude-plugins-official"] = true` in root `.claude/settings.json`.** [anchors: root] Lighter alternatives considered: (1) a bare `README.md` instruction telling contributors to run `/plugin install` themselves with no settings.json entry — rejected because it leaves the repo's own intent undeclared, so a fresh contributor has no signal this repo expects the plugin without reading prose; (2) `extraKnownMarketplaces` registration — unnecessary, `claude-plugins-official` auto-registers on first interactive Claude Code run, confirmed in the fetched doc text ("Claude Code adds the official Anthropic marketplace... automatically the first time you start it interactively"). [verified: code.claude.com/docs/en/discover-plugins] This declaration is also what `install.sh` itself consumes — see assumption 2.
- **`README.md` Requirements-section note** naming the plugin-install and binary-install commands. [anchors: root] `install.sh` (which contributors already re-run after every `git pull`, per its own documented "Existing users" note) auto-installs the declared plugin — see assumption 2 — but it does not install the `pyright-langserver` binary itself, so the README note remains necessary for that half regardless.

**Assumptions:**

| # | Claim | Tag |
|---|---|---|
| 1 | LSP diagnostics/navigation apply only to code the agent is actively editing or querying in a live session — nothing in Claude Code's LSP integration re-validates or updates `file:line` citations already embedded in previously-written, merged prose (e.g., a plan doc). | [verified: code.claude.com/docs/en/discover-plugins, code.claude.com/docs/en/common-workflows — neither documents any such re-validation behavior] |
| 2 | Whether the `enabledPlugins` declaration alone results in `pyright-lsp` actually getting installed for a contributor, without them separately running `/plugin install`. Resolved: yes, for any contributor who re-runs `./install.sh` — it reads `enabledPlugins` from this repo's own `.claude/settings.json` and runs `claude plugin install "$plugin" -s project` for every `true` entry, non-fatally, on every invocation. | [verified: install.sh:618-669] |
| 3 | `pyright-langserver` is installed via `pip install pyright` or `npm install -g pyright`. | [verified: code.claude.com/docs/en/plugins-reference] |
| 4 | This repo has no `PostToolUse` hook running `ruff`/`pytest` automatically after a Python edit today — `ruff check` and `pytest` are manual, allowlisted Bash commands only. | [verified: claude/.claude/settings.json permissions.allow list, and `PostToolUse` block contains no ruff/pytest entry] |
| 5 | Repo composition: 149 tracked `.py` files (~109,840 lines), 76 tracked `.sh` files (~17,263 lines), 347 tracked `.md` files (~80,871 lines). Python has fewer files than Markdown but more total lines. | [verified: `git ls-files` + `wc -l` run this session] |
| 6 | `requirements-dev.txt` pins every other dev dependency (`pytest==8.*`, `ruff==0.6.*`, `pyyaml==6.*`, `shellcheck-py==0.11.*`); this plan documents `pyright` unpinned (bare `pip install pyright`) and does not add it to that file. Deliberate, not an oversight: `pyright` never runs in CI or any blocking check — it's an interactive-only LSP backend, the same category as the README's other unpinned system tools (`stow`, `git`, `gh`, `jq`) rather than a CI-gating Python dependency. Version drift between contributors changes live-diagnostic behavior, not correctness of anything the repo verifies. | [verified: requirements-dev.txt (pinned entries), README.md:94 (unpinned `stow`/`git`/`gh`/`jq` precedent)] |

Assumption 2's resolution means the settings.json declaration and the README documentation are not redundant "belt and suspenders" against one unresolved question — they cover two different gaps (plugin declaration vs. binary installation), each necessary on its own.

**Alternatives considered:**
- **Do nothing / hold off** — rejected by the engineer once the stale-reference premise was separated from the real, independent Python-tooling benefit; see Context.
- **Author a custom LSP plugin wrapping `bash-language-server`** — rejected: no official-marketplace precedent to build on, extra plugin to maintain, and the shell surface here is small enough (76 files, ~227 lines/file avg) that grep already covers it; explicitly declined by the engineer.
- **Push the plugin into `claude/.claude/settings.json`** (the stow package, applies to every project on the machine) — rejected: the engineer's ask was "for this repo in particular," and confirmed project-scope-only when asked directly.

## Critical files

- **`.claude/settings.json`** (repo root) — add `"pyright-lsp@claude-plugins-official": true` to the existing `enabledPlugins` object, alongside `skill-management`, `claude-hook-review`, `plugin-semver`.
- **`README.md`** — add one bullet to the `## Requirements` section (after the existing `**Optional:** pytest...` bullet): `pyright-lsp` is installed automatically by `./install.sh` (already the documented post-`git pull` step) via its `enabledPlugins` handling, or manually with `claude plugin install pyright-lsp@claude-plugins-official --scope project`. Requires the `pyright-langserver` binary separately — prefer `python3 -m pip install --user pyright` over `npm install -g pyright`: Node/npm isn't otherwise a repo dependency, and a stock (non-Homebrew) global npm prefix on Linux/WSL2 is commonly root-owned, causing `EACCES`. Install to the user or global site, not inside `.venv` — a `.venv`-scoped install won't be on PATH for the process that launches Claude Code. On WSL2, confirm `which pyright-langserver` resolves to the Linux-side binary, not a Windows-side one on an inherited PATH.

**Reuse opportunities:** follow the exact `enabledPlugins` entry style and `claude plugin install ... --scope project` command format already used for `skill-management`/`claude-hook-review`/`plugin-semver` in both `.claude/settings.json` and `README.md`'s "Plugins (marketplace)" section — no new pattern needed.

**Out of scope for this plan (do not do as part of it):** actually running `./install.sh`/`claude plugin install` or `python3 -m pip install --user pyright` — those are software installs the engineer must run themselves (`CLAUDE.md` Safety section); this plan only declares the settings.json intent and documents the commands.

## Verification

1. `python3 -c "import json; json.load(open('.claude/settings.json'))"` — confirm the edited settings file is still valid JSON.
2. `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/` — confirm no regression (this change touches no Python source, only config/docs).
3. Manual, engineer-run: after pulling this branch, re-run `./install.sh` (or run `claude plugin install pyright-lsp@claude-plugins-official --scope project` manually) and `python3 -m pip install --user pyright`, then edit a `.py` file under `claude/.claude/hooks/` to introduce an undefined name and confirm Claude Code surfaces a diagnostic automatically (visible via **Ctrl+O**) without an explicit `ruff check` invocation. Separately, ask Claude to find all references to an existing hook helper function and confirm LSP-based find-references is used rather than a grep dispatch.

## Out of scope

- Fixing stale `file:line` references in already-written plan/PR prose — this is a documentation-practice question (e.g., whether to cite line numbers at all in content meant to survive a multi-phase refactor), not something LSP tooling reaches. Not addressed by this plan; flagged for the engineer to decide separately if still a concern.
- A shell/bash LSP — no official plugin exists; declined by the engineer.
- Enabling `pyright-lsp` at user or stow-package scope (every repo on the machine) — declined by the engineer; this repo only.
