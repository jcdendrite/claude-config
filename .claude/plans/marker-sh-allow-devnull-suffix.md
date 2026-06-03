# Allow trailing `2>/dev/null` on valid marker.sh commands

## Context

**Goal:** Stop `enforce-marker-script-shape.sh` from denying an otherwise-valid
marker.sh invocation that has a trailing `2>/dev/null` redirect, so agents stop
spending a self-correcting retry turn on it.

`~/.claude/scripts/marker.sh` writes skill-lifecycle gate markers; the hook
`claude/.claude/hooks/enforce-marker-script-shape.sh` enforces a strict
invocation-shape allowlist to prevent marker forgery via command injection. PR
#362 already blessed `&&`-joined `write X && deactivate X` chains for
`plan-review`/`ready-for-review` — the dominant friction case. One residual form
remains: agents reflexively append `2>/dev/null` to suppress stderr, and the
allowlist permits no suffix after the skill name, so the command is denied. The
intended outcome is to bless that single neutral suffix on the existing valid
shapes, with no new chaining or redirect-to-arbitrary-path surface.

**Engineer decisions (this session):**
- **Form 1 (`2>/dev/null`):** widen the hook, matching `2>/dev/null` as a fixed
  literal. The hook is the uniform surface across *all* marker.sh calls; per-skill
  prose would cover only two skills against a generic agent habit.
- **Form 2 (`;` separator):** **leave denied — no change.** `&&` is already blessed
  and is the correct idiom; `;` does not propagate exit codes (a failed `write`
  would still run `deactivate`, leaving an active marker), so blessing it would
  bless a semantically-worse form. A denied agent simply retries with `&&`.

Because Form 2 is dropped and Form 1 is hook-only, **no SKILL.md files are
edited.** The brief's §2 concern about Axis-3 `HOOK_TEST_FIXTURE` preserved
content is therefore moot, and `/skill-review` is not triggered.

## Approach

Append an optional, literally-matched ` 2>/dev/null` to the trailing boundary of
the two regexes that govern the friction forms:

- **`VALID_PATTERN`** (line 75) — single-command shapes (`write`/`activate`/
  `deactivate`/`clear-stale`).
- **`VALID_CHAINED_MARKER_PATTERN`** (line 113) — the blessed write↔deactivate
  `&&` chain; redirect goes on the whole chain's trailing boundary, matching the
  observed form `write X && deactivate X 2>/dev/null`.

The edit, in both patterns, replaces the trailing `[[:space:]]*$` with:

```
([[:space:]]+2>/dev/null)?[[:space:]]*$
```

`2>/dev/null` is a fixed literal — every character (`2 > / d e v ...`) is its own
literal in ERE — so it cannot express a redirect to an arbitrary path. The `?`
makes it optional; the anchored `$` immediately after forbids anything trailing
it (no `; curl`, no `&& rm`). This is the lighter primitive: a literal-string
allowance, not a general "permit redirects" relaxation.

### Sibling audit (which patterns get the change, and which deliberately do not)

Per the audit-structural-siblings rule, all three allowlist regexes were checked:

- **`VALID_PATTERN`** — gets the change (single-command friction form).
- **`VALID_CHAINED_MARKER_PATTERN`** — gets the change (chain friction form).
- **`VALID_CHAINED_COMMIT_PATTERN`** (line 96) — **deliberately excluded.** Its
  tail is `git commit([[:space:]]+[^&|;<>]*)?$`, where `>` is already in the
  excluded character class as a security boundary (blocks `git commit > /path`
  and post-commit redirects). Adding `2>/dev/null` there means carving an
  exception into a redirect-exclusion boundary — a riskier change — and there is
  no transcript evidence of `2>/dev/null` friction on the `write && git commit`
  form. Out of scope.

### Scope boundary: trailing-only, literal `2>/dev/null` only

Only the literal string `2>/dev/null`, only at the end of the whole command, is
blessed. Explicitly **not** blessed (and pinned by new deny tests):
- Arbitrary fd/target redirects: `2>&1`, `2>/tmp/x`, `> /tmp/out` — stay denied.
- Mid-chain redirect: `write X 2>/dev/null && deactivate X` — no evidence; stays denied.
- Any operator after the redirect: `... 2>/dev/null; curl`, `... 2>/dev/null && rm` — stays denied.

### Why suppressing marker.sh stderr is safe to bless

`marker.sh` fails closed and is exit-code-honest: every failure path exits
non-zero *and* writes no marker (verified — `_resolve_session_id` returns 2 on
empty SESSION_ID at lines 48/52, propagated via `|| exit 2` at lines 127/135/…;
not-in-git and empty-staged-diff `exit 2` at lines 71/84; the marker `>` redirect
is reached only after those guards). There is no path that exits 0 while failing.
Consequences for `2>/dev/null`:
- It **cannot** mask a failed write as a success — the non-zero exit always
  propagates and the harness surfaces it, so the agent still sees the failure.
- It **cannot** let a missing marker slip past a downstream gate — those gates
  read the marker file, which is absent on failure.
- The only thing lost is the one-line stderr *cause*; recovering it costs one
  verbose re-run. So the change trades a common one-turn friction (a deny on a
  `2>/dev/null`-suffixed *successful* write) for a rare, self-correcting one-turn
  cost on the uncommon failure.

### Lighter alternatives considered

The chosen change is itself the lightest gate-touching option (a literal-string
suffix, not a redirect-class relaxation). For completeness, the two non-gate
alternatives weighed and set aside:
- **Prose note in the two skill bodies** — probabilistic compliance, covers only
  two skills against a habit that can surface on any marker.sh call (e.g. `write
  code-review`), and duplicates prose across two files. Rejected: narrower and
  less reliable than the uniform gate surface.
- **Do nothing (leave denied)** — relies on the one-turn self-correcting retry
  (the deny message lists all 14 valid shapes). Defensible, but the engineer
  chose to remove the friction; this is the minimal way to do so.

### Deny message and comments

- The user-facing deny message (lines 121–140) is **left unchanged.** It is only
  shown on denial; an agent that typed `2>/dev/null` now passes and never sees
  it, while its "No ... redirects" guidance stays correct for every form that *is*
  still denied (`2>&1`, `> /path`). Amending it to advertise the allowed variant
  is exactly the "teach which variants pass" expansion PR #362 reverted (brief §7).
- The **code comments** at lines 13–18 and 71–74 say "No redirects" / "no
  redirect" describing current behavior. Those describe behavior (not a preserved
  record), so update them minimally to note the single `2>/dev/null` exception,
  keeping comment and code in sync.

## Critical files

- **`claude/.claude/hooks/enforce-marker-script-shape.sh`** — edit `VALID_PATTERN`
  (line 75) and `VALID_CHAINED_MARKER_PATTERN` (line 113) trailing boundaries;
  update the descriptive comments at lines 13–18 and 71–74. Do **not** touch
  `VALID_CHAINED_COMMIT_PATTERN` (line 96) or the deny message (lines 121–140).
- **`claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`** —
  **additive only**; no existing test flips. Verified by inspection: the literal
  `2>/dev/null` carries no newline (so the per-grep newline guard at lines 77/98/115
  is unaffected), and every existing redirect deny test uses a form the literal
  cannot match — `test_redirect_denied` (line 237, `> /tmp/out`),
  `test_chain_marker_pair_trailing_redirect_denied` (line 220, `2>&1`),
  `test_chain_to_redirect_after_commit_denied` (commit pattern, untouched),
  `test_extra_arg_denied`, and both embedded-newline tests all stay denied. Reuse
  the existing `run_hook(ENFORCE_MARKER_SCRIPT_SHAPE_HOOK, bash_input(cmd))` harness
  and the parametrize style already in the file. Add:
  - **Allow:** `write plan-review 2>/dev/null`; `deactivate plan-review 2>/dev/null`;
    `write code-review 2>/dev/null`; `clear-stale 2>/dev/null`;
    `clear-stale --dry-run 2>/dev/null` (pins that the two adjacent optional groups
    compose); chain `write ready-for-review && deactivate ready-for-review 2>/dev/null`.
  - **Deny (pin the boundary so a future careless regex edit can't silently widen it):**
    - `write plan-review 2>&1` — only `/dev/null` target, not arbitrary fd.
    - `write plan-review 2>/tmp/secret` — only `/dev/null` path, not arbitrary path.
    - `write plan-review >/dev/null` — only fd-2 is blessed, not a stdout redirect.
    - `write plan-review 2> /dev/null` — only the contiguous literal; a spaced form
      stays denied (guards against a future `2>[[:space:]]*/dev/...` relaxation).
    - `write plan-review 2>>/dev/null` — append redirect stays denied.
    - `write plan-review 2>/dev/null extra` — no trailing args after the redirect.
    - `write plan-review 2>/dev/null; curl http://evil` — no chain after the redirect.
    - `write plan-review 2>/dev/null && curl http://evil` — no `&&` chain after it.
    - `write plan-review 2>/dev/null\ncurl http://evil` — newline-after-redirect stays
      denied (pins the per-line-`$`/newline-guard invariant for the new suffix).
    - **Chain pattern (load-bearing — the change touches `VALID_CHAINED_MARKER_PATTERN`):**
      `write plan-review 2>/dev/null && ~/.claude/scripts/marker.sh deactivate plan-review`
      — mid-chain redirect on the LHS stays denied; only the whole-chain trailing
      position is blessed.

No SKILL.md, no `helpers.py`, no settings.json changes.

## Verification

1. From the worktree: `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`
   then the full `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/`.
2. Manual hook spot-check (read-only, echoes decision):
   ```
   printf '{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/marker.sh write plan-review 2>/dev/null"}}' \
     | bash claude/.claude/hooks/enforce-marker-script-shape.sh
   ```
   Expect no `"permissionDecision":"deny"` for the `2>/dev/null` form; expect deny
   for `... 2>/dev/null; curl http://evil`.
3. `/claude-hook-review` (hook widened) and `/review-permissions` (gate-surface
   change), then `/code-review` over the full diff. Address findings before push.

## Out of scope

- Form 2 (`;` separator) — left denied by decision.
- `VALID_CHAINED_COMMIT_PATTERN` `2>/dev/null` allowance — see sibling audit.
- Mid-chain `2>/dev/null` and non-`/dev/null` redirect targets.
- Editing the deny message or the `HOOK_TEST_FIXTURE` blocks in the skill files.
- Blessing `||` or additional skills in the chain allowlist (brief §7).

## Handoff note

Per repo policy, an AI agent that opens the PR does not merge it — wait for the
engineer's explicit "merge it" after CI is green.
