# Plan: Migration-filename UTC enforcement in the lovable-cloud plugin

## Context

**Goal: make it structurally impossible for Claude to author a Supabase migration filename in non-UTC time, by shipping a generator + a marker-gated authoring hook in the `lovable-cloud` plugin.**

Lovable stamps migration filenames in UTC; Claude Code stamps in local (Pacific) time. Because Supabase applies migrations in **filename-lexical order**, a Claude-authored local-time filename can sort *earlier* than its true UTC authoring moment and invert apply order relative to a Lovable-authored sibling. (The same clock-domain mismatch also broke a downstream CI pairing gate, but that gate is being repaired independently in the consuming project to pair on AST identity + authorship rather than timestamp order — so the durable justification for this work is **apply-order correctness + filename consistency**, not the gate.)

This is cross-project Claude-Code tooling, so it belongs in the `lovable-cloud` plugin in `claude-config` (works for any session that loads the plugin), not as application code in any single consuming project. Scope of *this* plan is the `claude-config`/`lovable-cloud` side only; the CI gate fix and the UTC-convention prose for the consuming project are a separate effort and are **out of scope** here.

## Approach

A **generator script + marker handshake**, enforced by two plugin hooks:

1. **`new-migration <slug>` generator** — deterministically emits `$(date -u +%Y%m%d%H%M%S)_<sanitized-slug>.sql` to stdout, and writes a one-shot **token** keyed by that exact filename into a plugin-owned token directory. No LLM is involved in producing the timestamp.

2. **PreToolUse gate** (`validate-migration-filename.sh`) on the `Write` tool — for a `Write` whose `file_path` matches `supabase/migrations/<14digits>_*.sql`: allow if a token exists for that basename; allow unconditionally if the post-timestamp segment is UUID-shaped (Lovable emit); otherwise **deny** with a message telling Claude to run the generator. The token proves the filename came from `new-migration` (hence `date -u`) — the hook validates the *actual invariant* ("generator produced this name"), not a wall-clock proxy.

3. **PostToolUse consumer** (`consume-migration-token.sh`) on the `Write` tool — after a successful migration Write, removes the token so it authorizes exactly one write (one-shot consume).

**Why a marker handshake, not a wall-clock check.** The discarded alternative compared the filename timestamp to `date -u` at write time within a ±15-min window. That validates a proxy and is non-deterministic — its verdict depends on *when* the Write fires, so a long author-to-write gap produces a false block. The token handshake is deterministic and enforces the real invariant.

**Why a self-contained plugin token, not the global `marker.sh`.** `marker.sh` + `enforce-marker-script-shape.sh` are review-gate infrastructure: a single sanctioned writer, stowed to every clone, keyed `<repo-hash>.<session-id>` (one marker per session, content = a staged-diff hash). The migration need is plugin-local and **filename-keyed** (several per session). Bending the global writer to this shape would force changes to core stowed infra every clone inherits — an over-powered primitive for a plugin-local feature, and contrary to the repo's standalone-plugin rule. The plugin owns its own token mechanism; the generator and hooks agree by living in the same plugin and sharing a fixed `$HOME`-relative token path.

### Prerequisite — verify before building the consume hook

The one-shot lifecycle depends on PostToolUse semantics the author cannot observe from the plan. **Confirm via `verify-sources` against the Claude Code hooks docs, as implementation step 0, before writing `consume-migration-token.sh`:** that PostToolUse (a) fires after a `Write` tool call, (b) fires **only on success** (or exposes a `tool_response`/success field the hook can gate on), and (c) exposes the written path at `tool_input.file_path`. **Contingency:** if PostToolUse on `Write` does not expose a reliable success signal + file path, do **not** force the one-shot design — fall back to filename-keyed **age-prune** (generator prunes tokens older than ~24h on each run; no PostToolUse hook) and surface the change to the user. This avoids the failure mode where a transient/denied Write still consumes the token and false-denies re-authoring.

### Token mechanism details

- **Location:** `$HOME/.claude/lovable-cloud/migration-tokens/`. Deliberately **not** named `*-markers/` to stay clear of the review-marker family and the "never write `~/.claude/*-markers/*` by hand" rule.
- **Single source of the path (outage-prevention).** The token-dir expression is defined **once** in a shared sourced file (`plugins/lovable-cloud/lib/token-path.sh`) that both the generator and the hooks `source` — neither side hand-writes the path. Derive it from one literal `$HOME`-based expression with **no `realpath`, no `cd`, no symlink normalization**, so the generator's write path and the hook's read path are byte-identical. A drift of one character (trailing slash, normalization) would make every migration Write false-deny in an unbreakable loop (following the deny hint regenerates a token the hook still can't see) — so the cross-boundary path agreement is a first-class test (see Verification).
- **Key:** the sanitized basename (`<14utc>_<slug>.sql`). One token file per generated filename; existence = authorized.
- **Lifecycle:** one-shot — written by the generator, removed by the PostToolUse hook after the Write succeeds. (Orphaned tokens from a generated-but-never-written filename are rare and harmless given the unique UTC-second+slug key; a periodic sweep can be added later if they ever accumulate. Not built now.)
- **Generator side effect:** writing the token means the generator is **no longer side-effect-free** (Brief B's "writes no file" is overridden by this decision). It still does not write the migration `.sql` file — only the token.

### Lighter alternatives considered

- **No hook, generator + skill instruction only** — relies on Claude always calling the generator; nothing stops a hand-typed filename. Rejected: the enforcement layer is the point.
- **PreToolUse wall-clock ±15-min check (the original brief design)** — validates a proxy, non-deterministic, false-blocks on long author-to-write gaps. Rejected per the user's redirection.
- **Reuse global `marker.sh`** — couples plugin-local enforcement to stowed core infra all clones inherit and to its strict invocation-allowlist hook. Rejected as an over-powered primitive (see above).

### Activation

Plugin `hooks/hooks.json` is **auto-discovered** when the plugin is enabled (same as `skill-management`'s) — no per-project `settings.json` hook entry is required. Activation in a consuming project is simply enabling/loading the `lovable-cloud` plugin there. (This supersedes the brief's "add the hook entry in the consuming project's settings.json" step.)

## Critical files

All three scripts: `set -euo pipefail`, `LC_ALL=C` for any slug sanitization (locale-stable character classes), and **quote every path expansion** (`"$TOKEN_DIR/$basename"`) since the dir derives from `$HOME`.

**Create (plugin code):**
- `plugins/lovable-cloud/lib/token-path.sh` — defines the token-dir path expression **once**; sourced by the generator and both hooks (see Approach → single source of the path).
- `plugins/lovable-cloud/scripts/new-migration` — bash generator. `LC_ALL=C` sanitize slug (lowercase; spaces→hyphens; strip non-`[a-z0-9-]`; collapse and trim hyphens), emit `<date -u +%Y%m%d%H%M%S>_<slug>.sql` to stdout, write the token (key = full emitted basename). **Reject** a slug that sanitizes to empty (non-zero exit + message) — never emit a slugless `<ts>_.sql`. Sources `lib/token-path.sh`. Header comment with example: `new-migration "add co-parent index"` → `20260612191215_add-co-parent-index.sql`.
- `plugins/lovable-cloud/hooks/hooks.json` — wire PreToolUse (matcher `Write`) → `validate-migration-filename.sh` and PostToolUse (matcher `Write`) → `consume-migration-token.sh`, each via `"${CLAUDE_PLUGIN_ROOT}"/hooks/<script>.sh`, **re-exporting `CLAUDE_PLUGIN_ROOT` as a command-line prefix** (mirror `skill-management/hooks/hooks.json`) so the validator can interpolate it into the deny message.
- `plugins/lovable-cloud/hooks/validate-migration-filename.sh` — PreToolUse gate. Defense-in-depth: filter own `tool_name == Write` and the `supabase/migrations/<14digits>_*.sql` path; UUID-exempt (post-prefix segment matches the UUID regex); token check (key = full basename); `emit_deny` (JSON-on-stdout + `exit 0`) with the generator hint — interpolate the **resolved** `${CLAUDE_PLUGIN_ROOT}/scripts/new-migration` path, never the literal variable. Fail-closed on malformed stdin. Sources `lib/token-path.sh` + `_lib.sh`.
- `plugins/lovable-cloud/hooks/consume-migration-token.sh` — PostToolUse. Filter `tool_name == Write` + migration path; remove the token **only when the Write succeeded** (gate on the verified success field — a failed/denied Write must leave the token intact); `rm -f` (idempotent for the already-absent case); never blocks (`exit 0`). Sources `lib/token-path.sh`.
- `plugins/lovable-cloud/hooks/_lib.sh` — small shared lib (stdin parse + `emit_deny`) shared by the two hooks, mirroring the global `_lib.sh` define-`emit_deny`-before-source contract. Add it to the hook-alignment test scope (or a plugin-local equivalent) so the mirrored contract does not drift untested.

**Create (tests):**
- `plugins/lovable-cloud/tests/test_new_migration.py` — slug sanitization (messy→clean, no leading/trailing/double hyphens); output shape `<14digits>_<slug>.sql`; timestamp within a small window of `date -u`; token written at the emitted basename key. **Edge cases:** empty-sanitized slug → non-zero exit (no slugless name); unicode slug (`"café"`); over-length slug; same-second same-slug double-gen is idempotent (one token, no corruption).
- `plugins/lovable-cloud/tests/test_validate_migration_filename.py` — token present → allow; no token + non-UUID → deny; UUID-named → allow; non-migration Write → allow; Edit/MultiEdit/Bash → allow; malformed JSON → deny. **Boundary cases:** timestamp prefix of 13/14/15 digits and 14-digits-without-underscore (proves the trigger regex gates exactly real migrations, no off-by-one un-gating); UUID-exemption near-misses (**uppercase** hex, wrong segment lengths, UUID-with-suffix) → must **deny** (proves the allow-bypass is tight). Deny-message assertion: reason contains a resolved path ending in `scripts/new-migration`, **not** the literal `${CLAUDE_PLUGIN_ROOT}`.
- `plugins/lovable-cloud/tests/test_consume_migration_token.py` — assert the **on-disk side effect** (stat the token path), not a `run_hook` decision string — invoke the consume script directly. Cases: successful Write → token removed; non-migration path → unrelated tokens untouched; absent token → no-op; **failed/denied Write → token survives** (depends on the prerequisite-pinned success field).
- One integration test driving the allow path from the **generator's real emitted token** (never a hand-placed fixture token — this is the cross-boundary path-agreement contract test): generator → PreToolUse allow → PostToolUse consume → second PreToolUse for the same name now **denies** (proves one-shot *and* that the generator-written path == the hook-read path byte-for-byte).

**Modify:**
- `plugins/lovable-cloud/.claude-plugin/plugin.json` — version bump `3.1.0` → `3.2.0` (new feature; run `/plugin-semver`).
- `.claude-plugin/marketplace.json` — update the `lovable-cloud` description to mention migration-authoring enforcement if scope wording changes.
- `pyproject.toml` — add `plugins/lovable-cloud/tests` to `tool.pytest.ini_options.pythonpath` so tests can `from helpers import ...`; include a test that actually imports `helpers` from the new dir so the wiring is verified, not assumed.
- `claude/.claude/tests/helpers.py` — (a) add an `env`/`HOME` parameter to `run_hook`/`run_hook_reason` (currently they pass no `env=`, so hook subprocesses inherit the **real** `$HOME` and would touch real `~/.claude` instead of the `isolated_home` temp dir); (b) add a PostToolUse input builder that includes the prerequisite-pinned success field. Shared test infra — low-risk additive change.

**Reuse (do not reimplement):**
- `claude/.claude/tests/helpers.py` — `run_hook`, `run_hook_reason`, `write_input`, `edit_input`, `bash_input`, and the `isolated_home` fixture (see Modify above for the `$HOME`-wiring + PostToolUse-builder additions these need).
- Global hook conventions to mirror: `emit_deny` JSON shape and the define-before-source order from `claude/.claude/hooks/_lib.sh` / `require-worktree-for-file-writes.sh`; the malformed-stdin fail-closed test pattern.
- `plugins/skill-management/hooks/hooks.json` — the `${CLAUDE_PLUGIN_ROOT}` reference pattern and auto-discovery (no `plugin.json` `hooks` key).
- UUID regex (from the consuming project's `migration-cleanup-proof.sh`): `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`.

## Verification

0. **Prerequisite (verify-sources, before building the consume hook):** confirm against the Claude Code hooks docs that PostToolUse fires on `Write`, only on success (or exposes a gateable success field), and exposes `tool_input.file_path`; and that the PreToolUse `Write` matcher + JSON-deny-on-stdout works for a file-creation gate. If the PostToolUse success signal is unavailable, switch to the age-prune contingency (see Approach → Prerequisite) and tell the user.
1. **Generator:** `plugins/lovable-cloud/scripts/new-migration "add co-parent index"` prints `<14-digit-UTC>_add-co-parent-index.sql`; confirm a token appears under `$HOME/.claude/lovable-cloud/migration-tokens/` (isolated `$HOME`). Messy slug → sanitized cleanly; empty-sanitized slug → non-zero exit.
2. **PreToolUse gate** (via `run_hook` with isolated `$HOME`): generated filename with token → allow; same filename, no token → deny + resolved-path hint; UUID-named emit → allow; uppercase-UUID / UUID-with-suffix → deny; `src/components/Foo.tsx` → allow; 13/15-digit prefix → allow; Edit/Bash → allow; malformed stdin → deny.
3. **PostToolUse + one-shot (side-effect assertion):** drive the allow path from the **generator's real token**; after a successful Write the token file is gone (stat it); a failed/denied Write leaves it intact; re-running the gate for the same name now denies. Confirm a non-migration Write leaves unrelated tokens intact.
4. **Suite:** `../../../.venv/bin/pytest plugins/lovable-cloud/` and `../../../.venv/bin/ruff check plugins/lovable-cloud/` (run from a linked worktree per repo worktree enforcement; the `.venv` lives at the main-tree root, three levels up).
5. **Manual end-to-end:** in a scratch repo with the plugin loaded, ask Claude to author a migration; verify it calls `new-migration`, the Write is allowed, and a hand-typed non-UTC name is blocked with the hint.

## Out of scope

- The CI pairing-gate fix and the UTC-convention prose (AGENTS.md / migration-author skill) in the consuming project — a separate effort handed to that project's session.
- Per-project `settings.json` activation — plugin `hooks.json` is auto-discovered on plugin load.
- Bash/`mv`-based migration file creation and Edit/MultiEdit — the hook targets the `Write` creation path; Edit operates on an existing file whose name is already set.
- Age-based sweep of orphaned tokens — one-shot consume is the chosen lifecycle; age-prune is held in reserve as the Prerequisite contingency (if PostToolUse can't gate on Write success) and as a later add if orphans ever accumulate.
