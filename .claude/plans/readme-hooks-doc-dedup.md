# Re-altitude README Workflow section vs docs/skills.md (GH-416)

## Context

**Goal:** trim the README `### Workflow` section's per-skill bullets and hook table back to one-line role summaries + distinctive highlights (deferring accreted detail to its canonical home), and — folded in per the owner's call — backfill `docs/hooks.md` so it once again documents every hook in `claude/.claude/hooks/`, with a test to keep it that way.

**Why now:** surfaced during PR #413 review. The README Workflow section's `/skill-review` and `/agent-review` bullets have grown past role summaries into enforcement/plugin-provenance detail, and several hook-table rows have accreted implementation caveats (merge-base mechanics, token thresholds, friction-signal enumeration). This duplicates detail that already lives in `docs/skills.md`, `docs/hooks.md`, and the nudge-specific docs — duplicated prose drifts and a reader can't tell which copy is authoritative (single-source-of-truth, `claude/.claude/CLAUDE.md`). While auditing, discovery found `docs/hooks.md`'s opening claim ("Full descriptions for **every** hook in `claude/.claude/hooks/`") has itself drifted — four hooks that live there are undocumented.

**Intended outcome:** README stays the pipeline overview + gate-summary surface (broad principles + distinctive highlights); the deeper docs stay the thorough per-item detail, and `docs/hooks.md` is complete + enforced.

**Key discovery — the README trim is delete-duplicate, not move.** Every caveat the README sheds already exists in a canonical home, so nothing is relocated:
- `/skill-review` plugin provenance → `docs/skills.md` §Project-scoped plugins + README §Plugins (line 184)
- `require-plugin-version-bump.sh` merge-base rule + `plugin-semver` provenance → README §Plugins (line 189) + repo-root `CLAUDE.md` §Review pipeline
- `deny-pii-in-commits.sh` `exclude:` path + `check-branch-divergence.sh` → `docs/hooks.md` (L10, L29 — fully covered)
- `nudge-handoff-near-context-cap.sh` 120k threshold → `docs/handoff-nudge.md`; `nudge-error-mode-analysis.sh` friction/dormant-marker → `docs/error-mode-nudge.md` (both already linked inline in their own rows)

The `docs/skills.md` side already defers correctly (its line 3 links pipeline+hook-gating out to README; it does not restate the hook table), so no `docs/skills.md` edits are needed.

**The four undocumented hooks** (disk vs `docs/hooks.md`, `_lib.sh` excluded as a shared library, `require-plugin-version-bump.sh` excluded as a plugin-dir hook outside `hooks.md`'s scope): `check-claude-md-length.sh` (gate), `nudge-handoff-near-context-cap.sh`, `nudge-error-mode-analysis.sh`, `cleanup-handoff-nudge-marker.sh` (advisory/utility).

## Approach

Four edit clusters across three files. README trims restate nothing; `docs/hooks.md` entries defer their deep mechanics to the nudge docs (matching hooks.md's existing deferral pattern — L9→private-project-redaction.md, L10/L11→security-hardening.md) rather than re-stating them.

**Alternatives set aside:**
- *Move README detail into the deeper docs.* Rejected — it's already there; moving would create a second copy, the exact defect being fixed.
- *Delete the `/skill-review` and `/agent-review` bullets.* Rejected — the hook-enforced-vs-not asymmetry is a distinctive feature README should keep; only the *why* defers down.
- *Restructure the hook table / skill list shape.* Out of scope.
- *Restate full nudge mechanics in `docs/hooks.md`.* Rejected — the nudge docs are the canonical deep home; hooks.md entries stay brief pointers to avoid re-introducing duplication.

### Edit 1 — README skill bullets (lines 137–138)

Trim to role-summary + distinctive highlight (keep the hook-enforced contrast per the owner's call).
- **`/skill-review` (137):** keep "behavioral-equivalence audit when a `SKILL.md` changes; **hook-enforced**." Drop the marker-mechanics parenthetical + the "Provided by the `skill-management@claude-config` plugin — see Project-scoped plugins" sentence.
- **`/agent-review` (138):** keep "same audit for agent files (`…/agents/*.md`); dispatcher-invoked by `/code-review`, **not** hook-enforced." Drop the "agent bodies are lazy-loaded / lower-blast-radius" rationale (in `docs/skills.md` L123).

### Edit 2 — README hook table rows (lines 145–161)

Trim overgrown rows to one-line role summaries; keep Gates/Cleared-by semantics and existing doc links.
- **`require-skill-review.sh` (149):** drop "(ships with `skill-management@claude-config` plugin)".
- **`require-plugin-version-bump.sh` (150):** shorten the merge-base mechanics to a one-line gate and drop "(ships with `plugin-semver@claude-config` plugin)"; full mechanics stay in README §Plugins L189 + `CLAUDE.md`.
- **`nudge-handoff-near-context-cap.sh` (158):** drop the "exceeds 120k" figure; keep "near the context cap; see [`docs/handoff-nudge.md`]".
- **`nudge-error-mode-analysis.sh` (160):** drop the friction-signal enumeration + the dormant-until-`~/.claude/.error-mode-nudge-enabled` clause; keep "(opt-in)" + the `docs/error-mode-nudge.md` link.
- **Borderline (tighten minimally):** `deny-pii-in-commits.sh` (152) and `check-branch-divergence.sh` (161) — both fully covered in `docs/hooks.md`. Rows 147, 148, 151, 153–157, 159 unchanged.

### Edit 3 — `docs/hooks.md` backfill (the four undocumented hooks)

- **`check-claude-md-length.sh`** → add to §Gate hooks next to `check-skill-length.sh` (L20). Behavior (from the script header + settings.json:201, confirmed): a wired `git commit` gate that blocks when a staged **`CLAUDE.md` or `AGENTS.md`** grows past its 200-line default limit; nudges toward trimming. Note it covers `AGENTS.md` too, unlike the skill-length gate.
- **`nudge-handoff-near-context-cap.sh`** → add to §Utility hooks: `(UserPromptSubmit, advisory)` one-shot `/handoff` suggestion as carried context nears the cap; opt-out via `~/.claude/.handoff-nudge-disabled`; **defer** the 120k/60% detail to `docs/handoff-nudge.md`.
- **`nudge-error-mode-analysis.sh`** → §Utility hooks: `(UserPromptSubmit, advisory, opt-in)` one-shot `/error-mode-analysis` suggestion when friction crosses a threshold; dormant unless `~/.claude/.error-mode-nudge-enabled` exists; **defer** threshold/signal detail to `docs/error-mode-nudge.md`.
- **`cleanup-handoff-nudge-marker.sh`** → §Utility hooks: `(SessionEnd)` destructor pair for `nudge-handoff-near-context-cap.sh`'s per-session fired-marker; brief, mirroring `cleanup-session-id.sh` (L26).

### Edit 4 — completeness test (enforce the convention that just drifted)

Add a parametrized test asserting every `*.sh` in `claude/.claude/hooks/` (excluding `_lib.sh`) is named in `docs/hooks.md`. Add it as a **new class in `test_hook_alignment.py`** — it reuses that file's `_MAIN_HOOKS_DIR` glob directly (scoped to the main hooks dir only; `docs/hooks.md` deliberately excludes `plugins/*/hooks/`, so do not include `_PLUGIN_HOOKS_DIRS`). Substring-match each `hook.name` against the doc text (backtick wrapping is irrelevant to the match; `check-claude-md-length.sh` will not false-match `check-skill-length.sh`).

## Critical files

- `README.md` — Edits 1–2. Clusters: lines 137–138 and 145–161.
- `docs/hooks.md` — Edit 3. §Gate hooks (near L20) + §Utility hooks (near L25–32).
- `claude/.claude/hooks/tests/test_hook_alignment.py` (or new `test_hooks_doc_coverage.py`) — Edit 4.

**Reuse / defer targets (confirmed to already hold the shed detail — no edits):**
- `docs/skills.md` L53, L123, L131 · `docs/hooks.md` L10, L29 · `docs/handoff-nudge.md` · `docs/error-mode-nudge.md` · README §Plugins L184/L189.
- `test_hook_alignment.py` `_all_hook_files()` / `_MAIN_HOOKS_DIR` — reuse the enumeration pattern for Edit 4.

## Verification

- `.venv/bin/pytest claude/.claude/hooks/tests/test_hook_alignment.py` — new completeness test passes (fails before Edit 3, passes after); existing header/gate checks stay green. Run from a worktree via `../../../.venv/bin/pytest`.
- `.venv/bin/pytest claude/.claude/hooks/tests/test_doc_counts.py` — no numeric count claim broken (edits touch no counted claim).
- `.venv/bin/pytest claude/.claude/` + `.venv/bin/ruff check claude/.claude/` — full suite green.
- Count-claim floor: `grep -nEi "(one|two|three|four|[0-9]+) hooks?" README.md docs/hooks.md` — confirm no "N hooks" total-count sentence exists that the backfill would falsify.
- Shed-phrase floor: `grep -n "120k\|merge-base\|skill-management@claude-config\|friction signals" README.md` — confirm the trimmed phrases are gone from the Workflow section (plugin-version-bump mechanics may legitimately remain in README §Plugins).
- Manual render check: README Workflow hook rows still name Gates + Cleared-by and their doc links resolve; the four new `docs/hooks.md` entries render in the right section with working defer-links.

## Out of scope

- `docs/skills.md` edits — it already defers correctly.
- Restructuring the hook table or the skill-bullet list shape.
- Documenting `require-plugin-version-bump.sh` in `docs/hooks.md` — it lives in `plugins/plugin-semver/hooks/`, outside that doc's stated scope; its home is README §Plugins + `CLAUDE.md`.
