# Deterministic announcement of plan-review's closing plan path

## Context

Make the absolute plan-file path that `/plan-review` must restate verbatim in its closing "Output format" line reach the engineer's chat window deterministically, instead of depending on the reviewing model correctly recalling and re-stating a value it resolved earlier in a potentially long review. `claude-skills/skills/plan-review/SKILL.md` Step 1 resolves "the identified file" to an absolute path and says to "carry that exact string forward — Output format's closing line states it, not a value re-derived at write time" — an explicit acknowledgment, at authoring time, that this value is prone to drift across a review that may spawn multiple specialist reviewer subagents in between. This is the same shape of bug `claude/.claude/hooks/announce-resume-command.sh` (PR #1031) already fixed for `/handoff`'s and `/brief`'s continuity-file resume command: a deterministic, computable value that a skill instruction asks the model to recall and restate correctly, rather than a hook injecting it directly into the visible chat transcript. The engineer identified this as an analogous, still-open inconsistency immediately after that fix shipped and confirmed (via `AskUserQuestion`) that this specific closing line — the path, not the verdict, which requires genuine judgment and is out of scope — is the target. The intended outcome is a hook-backed mechanism, following the shipped precedent's structure (fail-silent `PostToolUse`, `systemMessage` + `hookSpecificOutput.additionalContext`), that makes the correct absolute plan path visible in chat independent of the model's own recall discipline.

## Approach

Record the plan path once, at the moment it is correct, and let a hook — not the model — put it on screen. `/plan-review` Step 1 already resolves the plan to an absolute path; it will now also write that string, with the Write tool, to a session-scoped sibling file `<config-dir>/.plan-review-active.d/<session-id>.reviewed-plan-path`, mirroring the `.planmode-path` sibling Step 0 already writes. A new fail-silent `PostToolUse` hook, `announce-plan-review-path.sh`, fires on exactly that Write, reads the path out of the call's own `tool_input.content` (never off disk), and emits it on both hook channels: `systemMessage` for the engineer's chat window and `hookSpecificOutput.additionalContext` telling the model to state it verbatim in the Output-format closing line. The sibling file gets no reader — it is the trigger artifact, not state — so `marker.sh deactivate plan-review` removes it and `clear-stale` exempts it from PID-liveness eviction.

**Assumption ledger**

**Root:** `/plan-review`'s closing "Output format" line restates an absolute plan path the model resolved at Step 1, many turns and possibly several specialist-subagent dispatches earlier; nothing but the model's own recall currently stands between that resolution and the string the engineer reads.

**Givens** (conditions beyond this design's reach):

- **G1.** A `PostToolUse` hook fires only after its tool call has completed, so it cannot observe state that call destroyed. Harness contract — no artifact in this or any reachable repository changes it.
- **G2.** A hook reaches the engineer's terminal only through `systemMessage` and the model only through `hookSpecificOutput.additionalContext`. Harness contract — same reason.

**Mechanisms** (each anchored):

- **M1 — New hook `claude/.claude/hooks/announce-plan-review-path.sh`.** `anchors: root`. The only non-model route from a resolved value to the chat window is a hook emission (G2), and the precedent for exactly this bug shape already ships as `announce-resume-command.sh`.
- **M2 — Trigger is a skill-authored Write, not a Read of the plan file.** `anchors: row17`. Step 1's three resolution routes are not uniformly tool-anchored, so only a Write the skill itself performs fires on all three.
- **M3 — Sibling lives under `<config-dir>/.plan-review-active.d/`.** `anchors: row6`. That directory is already inside `enforce-marker-script-shape.sh`'s Write/Edit/MultiEdit denial for review-only personas, so the declaration inherits the same write authority the existing `.planmode-path` sibling has, with no new gate.
- **M4 — `marker.sh deactivate plan-review` removes the sibling; `clear-stale` exempts its suffix.** `anchors: row4`. A trigger artifact with no reader must not outlive the review that created it, and `clear-stale`'s PID-liveness test would misread a path as a dead marker.
- **M5 — `run_hook_raw` and `registered_hook_event_name` move into `claude/.claude/tests/helpers.py`.** `anchors: row12`. Two test files already carry hand-rolled copies; the new test file is the third, which is past CLAUDE.md §Engineering Judgment's "abstract into a shared helper once two or more share it" threshold.

**Over-powered-primitive check.** A new hook plus a new on-disk artifact plus a skill step plus two `marker.sh` edits is heavier than "print the path," so four lighter primitives from this repo's own hook/script system were weighed against **M1/M2**:

1. **`PostToolUse` `Read` hook on `*/.claude/plans/*.md`, gated on the live active marker** — the `log-routing-read.sh` shape, needing no skill change, no new artifact, and no cleanup. Fails because Step 1 route 3 ("a plan exists in the current conversation context") fires no Read at all and route 1 accepts an argument path anywhere on disk, so the hook would stay silent on precisely the two routes where the model's later recall is least anchored. `anchors: M2`
2. **Extend the already-registered `log-routing-read.sh` to also announce** — one fewer hook, one fewer settings.json entry. Fails because it fires on a `ROUTING.md` Read, a payload carrying no plan path at all, so it would have to read the path from somewhere else anyway and would fold two unrelated triggers into one hook. `anchors: M1`
3. **Have `marker.sh deactivate plan-review` print the recorded path to stdout — no hook, no settings.json change.** Fails on G1's consequence in reverse: `deactivate` runs *after* the Output-format section, so the authoritative string arrives after the model has already written the line that needed it, and Bash tool stdout is not the `systemMessage` channel. `anchors: M1`
4. **Have the hook recompute the path from `_lib_active_plan_files` rather than reading a recorded one** — no skill change, no sibling file, no cleanup. Fails because that helper returns the *active plan set* (zero, one, or N repo-relative paths under `.claude/plans/`), which is not what Step 1 resolved — route 1 can name a plan outside that directory entirely — so it reintroduces exactly the "value re-derived at write time" the existing Step 1 text already forbids. `anchors: M2`

**Assumption rows:**

1. `[verified: claude/.claude/hooks/announce-resume-command.sh:55-116]` The precedent's fail-silent skeleton — source `_lib.sh` or `exit 0`; `_lib_config_dir`; read stdin; filter `tool_name`; filter a config-dir-anchored path glob; `LC_ALL=C grep -Eqz` allowlist; single `_lib_jq -n --arg` emit; `exit 0` — is the template. The new hook drops the entire `git rev-parse` worktree block, because a Step-1-resolved path is already absolute and needs no `--cwd` derivation.
2. `[verified: claude/.claude/settings.json:461-473]` `announce-resume-command.sh` is registered under `PostToolUse` matcher `Edit|Write|MultiEdit`. The new hook gets its own group with `"matcher": "Write"` — the narrower registration for a hook that honors only `Write`, per CLAUDE.md §Safety's least-privilege default.
3. `[verified: claude-skills/skills/plan-review/SKILL.md:26-45]` Step 0 already writes a session-scoped sibling into `.plan-review-active.d/` with the **Write tool, not Bash**, resolving the session id via `~/.claude/scripts/marker.sh resolve-session-id`, and states its own rationale for that choice. Step 1's new declaration reuses that shape exactly and cites Step 0's rationale rather than restating it.
4. `[verified: claude/.claude/scripts/marker.sh:654-662, 711-721]` `deactivate plan-review` removes the PID marker plus three siblings; `clear-stale` exempts `*.planmode-path` by name from the `^[0-9]+$` PID test that would otherwise always misread it as dead. Both sites need the new suffix.
5. `[verified: claude/.claude/scripts/marker.sh:489-538, 800-813]` `write plan-review` and `status` read `.planmode-path` first and otherwise hash the whole active plan set via `_lib_active_plan_hash`. The new sibling is deliberately read by neither: a single declared path would narrow the completion marker's coverage below the plan set `require-plan-review.sh` gates against, which is a gate weakening, not a simplification.
6. `[verified: docs/hooks.md:86]` `enforce-marker-script-shape.sh` already denies `Write`/`Edit`/`MultiEdit` to any resolved path under `<config-dir>/.*-active.d/` from every agent type in `_LIB_NO_GATE_RELEASE_AGENTS`. The new sibling inherits that restriction with no hook change — and inherits its known consequence (row 13).
7. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:1-14, 1087-1121]` Layer 0 requires a per-hook list-item entry in `docs/hooks.md`; Layer 1 requires `# hook-class: <value>` within the first five lines. `informational` is the correct value — the hook never denies, and `announce-plan-review-path.sh` matches none of the `deny-`/`require-`/`enforce-`/`guard-`/`block-`/`check-*-guard` gate-naming prefixes.
8. `[verified: claude-skills/skills/tests/test_skills.py:1976, 2004-2014]` The Trigger-A fence scan exempts a fenced block whose nearest preceding non-blank line matches `<!-- (HOOK_TEST_FIXTURE|HOOK_SCRIPT_CONTENT_EXAMPLE):` — by marker presence, not by an id allowlist. The new fixture's `SESSION_ID=$(...)`-then-reference shape is therefore exempt as long as it carries its own marker comment.
9. `[verified: claude/.claude/hooks/tests/test_require_plan_review.py:2383-2445]` `TestPlanReviewSkillPlanModeFixture` is the structural sibling for the new fixture test, supplying `_seed_session`, `plan_review_repo`, `plan_review_home`, `run_skill_command`, and `extract_skill_command`.
10. `[verified: claude/.claude/hooks/tests/test_marker_script.py:567-629, 2247-2266]` `TestMarkerScriptClearStale` (567-629) has no eviction-*report*-content assertion for either sibling suffix, but `TestMarkerScriptPlanModeSibling::test_clear_stale_does_not_evict_a_live_sibling` (2247-2266) already asserts `.planmode-path` *survival* three ways (no/live/dead adjacent PID). The new `TestMarkerScriptClearStale` test adds the stdout-content angle for both suffixes — a narrower, genuinely net-new contribution, not first-ever coverage of the exemption.
11. `[verified: claude/.claude/scripts/select-tests.py:252-262]` `claude/.claude/tests/helpers.py` is a member of `GLOBAL_TRIGGER_PATHS`, so editing it makes `select-tests.py` select the full suite for this diff. This is CLAUDE.md's documented case 1, not a reason to skip M5.
12. `[verified: claude/.claude/tests/helpers.py:96-160, 242-282, 503-517]` `write_input` already accepts a `content` kwarg. `run_hook_advisory` returns a decision string and `run_hook_payload` returns only `hookSpecificOutput`, so neither can assert on `systemMessage` — which is why `_run_hook_raw` exists as a duplicated per-file helper in both `test_announce_resume_command.py` and `test_consume_durable_continuity_file_on_read.py` today.
13. `[verified: docs/hooks.md:86 + claude-skills/skills/plan-review/SKILL.md:35]` If `/plan-review` ever runs inside a `_LIB_NO_GATE_RELEASE_AGENTS` persona, Step 1's Write is denied and no announcement fires. This is identical, pre-existing exposure for the shipped `.planmode-path` write, not a regression this change introduces.
14. `[engineer-verified]` The mechanism is a fail-silent `PostToolUse` hook emitting `systemMessage` plus `hookSpecificOutput.additionalContext`, following the structure of the PR that shipped `announce-resume-command.sh`. Alternative 3 above is reachable and is declined for this reason plus its own timing defect; it is recorded in **Out of scope**.
15. `[engineer-verified]` Only the path is in scope. The verdict word requires genuine judgment and gets no mechanism.
16. `[unverified]` Whether the harness matches `PostToolUse`'s `matcher` field as a full match or a substring search against `tool_name`. Non-load-bearing either way: the hook filters `tool_name` itself, per this repo's CLAUDE.md hook defense-in-depth rule, so an over-broad matcher costs only a process spawn.
17. `[verified: claude-skills/skills/plan-review/SKILL.md:49-54]` Step 1's three resolution routes are not uniformly tool-anchored — routes 1 and 2 involve a Read, route 3 involves no tool call at all — which is what rules out every Read-anchored trigger.
18. `[unverified]` Whether a `Write` call's `tool_input.content` reaches the hook byte-identical to what the model supplied, with no harness-added trailing newline. The hook strips at most one trailing newline before the allowlist check, so both shapes announce and neither shape silently no-ops.

**Duplication note.** In harness plan mode both `.planmode-path` and `.reviewed-plan-path` hold the same string. That is accepted rather than branched around: the two files have different contracts (`.planmode-path` is read by `marker.sh write plan-review` to pick the hash target; `.reviewed-plan-path` is read by nothing and exists only to carry a payload into a hook), and a conditional "skip this write when Step 0 already ran" in skill prose is a likelier source of model error than a duplicated path. This is CLAUDE.md §Engineering Judgment's named "small duplicated value beats a bad abstraction" exception.

## Critical files

Single `code-writer` dispatch — no split. The sibling-path literal `<config-dir>/.plan-review-active.d/<session-id>.reviewed-plan-path` has to agree byte-for-byte across the hook's path glob, the SKILL.md fixture, and both `marker.sh` arms, so any partition would have to restate that shared background in every prompt, which `plan-it` Step 5 names as the do-not-split condition.

**Create — `claude/.claude/hooks/announce-plan-review-path.sh`**

Line 2 is `# hook-class: informational`. Header comment states, one fact per sentence: what it announces and on which two channels; that it removes the dependency on the reviewing model recalling the Step-1 value; the fail-silent posture and the exhaustive list of fall-through cases; the allowlist's purpose as a structural filter only; and a `Known gaps:` trailer stating (a) `_lib_jq`'s `timeout`/`gtimeout` cap runs uncapped when neither binary is on `PATH` (a pre-existing `_lib.sh` gap, not one this hook introduces — `_lib.sh`'s own header already documents it for every caller), and (b) this hook must not gain a `_config_value`/`_config_enabled` call without re-evaluating its fail-silent posture, since those calls perform unwrapped filesystem I/O with no timeout backstop per `_config.sh`'s own header — the hook currently avoids this class entirely by calling only `_lib_config_dir` (pure env-var branching, no stat/read). Body, in order:

1. `. "${0%/*}/_lib.sh" 2>/dev/null` or `exit 0`; `CONFIG_DIR=$(_lib_config_dir) || exit 0`.
2. `INPUT=$(cat) || exit 0`; extract `.tool_name`; `case` accepting only `Write`, else `exit 0`.
3. Extract `.tool_input.file_path`; `case "$FILE_PATH" in "$CONFIG_DIR"/.plan-review-active.d/*.reviewed-plan-path) ;; *) exit 0 ;; esac`.
4. Extract `.tool_input.content`; `PLAN_PATH="${CONTENT%$'\n'}"` — strips at most one trailing newline, so an embedded newline still fails the allowlist below rather than being normalized away.
5. `case "$PLAN_PATH" in /*) ;; *) exit 0 ;; esac` — a non-absolute value means Step 1's resolution failed, and announcing it would mislead.
6. `printf '%s' "$PLAN_PATH" | LC_ALL=C grep -Eqz '^[A-Za-z0-9._/@+-]+$' || exit 0`.
7. Single `_lib_jq -n --arg path "$PLAN_PATH"` emitting `systemMessage: ("Plan under review: " + $path)` and `hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: ("The absolute path of the plan file /plan-review is reviewing is: " + $path + ". It was recorded from this session's own Step 1 resolution -- state it verbatim in the Output format closing line instead of re-deriving it.")}`, with `2>/dev/null || true`, then `exit 0`.

*Reuse:* `_lib_config_dir`, `_lib_jq` (mandatory — `test_hook_alignment.py` fails a bare `jq`), and `announce-resume-command.sh:78-84`'s allowlist regex plus its `-z` rationale comment. Do **not** copy its `git rev-parse` block, `_lib_capped` calls, or `PAYLOAD_CWD` handling: this hook makes no subprocess call other than `jq`.

**Modify — `claude/.claude/settings.json`**

Add a `PostToolUse` group `{"matcher": "Write", "hooks": [{"type": "command", "command": "~/.claude/hooks/announce-plan-review-path.sh"}]}`, placed immediately after the existing `Edit|Write|MultiEdit` group (lines 461-473) so the two announce hooks read as neighbors.

**Modify — `claude-skills/skills/plan-review/SKILL.md`, Step 1**

`SKILL.md` is already 289 lines, past the 200-line target and near the 300-line diminishing-returns mark, and this addition offers no offsetting trim. Accepted anyway: Step 0 already established this exact declare-a-sibling-path shape for `.planmode-path`, so Step 1's version is not new prose to justify from scratch, only the same load-bearing pattern applied to a second sibling — the alternative (a shared helper section) is `.claude/rules/skill-and-agent-self-review.md`'s named no-shared-partials constraint (`SKILL.md` has no `includes:`/`import:` field).

Keep the existing resolve-to-absolute sentence. Append a declaration sub-step after it, unconditional on plan mode:

- Resolve this session's id via `~/.claude/scripts/marker.sh resolve-session-id`.
- Write the resolved absolute path, with no trailing newline, to `<config-dir>/.plan-review-active.d/<the resolved id>.reviewed-plan-path`, using the Write tool and not Bash — see Step 0's "Why Write, not Bash" for the reason, which applies unchanged here. Do not restate it.
- If the session id does not resolve, or no absolute path could be resolved at all, skip the write and say so in the review output instead of writing a partial declaration.

Then a `<!-- HOOK_TEST_FIXTURE: declare-reviewed-plan-path — ... -->` comment immediately preceding a &#96;&#96;&#96;bash fence holding the executable equivalent, modelled line-for-line on `declare-planmode-path` (SKILL.md:40-45): `CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"`, `SESSION_ID=$(~/.claude/scripts/marker.sh resolve-session-id) || exit 1`, `printf '%s' "$REVIEWED_PLAN_PATH" > "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.reviewed-plan-path"`. The comment must carry the same "pytest-executed simulation, never typed into an agent's Bash tool" note the sibling fixture carries — that is what exempts the block from the Trigger-A scan (row 8).

*Reuse:* Step 0's sub-step 1-4 structure and its `marker.sh`-hardcoding note, both cited rather than copied.

**Modify — `claude/.claude/scripts/marker.sh`**

- `deactivate plan-review` arm (line 659 area): add `rm -f "$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.reviewed-plan-path"`.
- `clear-stale` loop (lines 719-721): widen the exemption to `*.planmode-path|*.reviewed-plan-path) continue ;;` in the single existing `case` arm, and update its comment to say these siblings hold declared paths rather than PIDs — one arm, not two, since both share the identical reason.
- `write plan-review` and `status` arms: **unchanged**. Add no read of the new sibling (row 5).

**Modify — `docs/hooks.md`**

New list item under "Utility hooks", immediately after the `announce-resume-command.sh` bullet (line 73), in that section's established `- **\`name.sh\`** (Event, Matcher, class) — prose` form with a `Known gaps:` trailer. Known gaps to state: the announcement lands at Step 1 rather than beside the verdict; a declaration write from a `_LIB_NO_GATE_RELEASE_AGENTS` persona is denied by `enforce-marker-script-shape.sh` and so announces nothing; a config dir or plan path containing a space suppresses the announcement entirely; the `_lib_jq` timeout cap runs uncapped when neither `timeout` nor `gtimeout` is on `PATH`, a pre-existing `_lib.sh`-wide exposure carried over here, not introduced by this hook. Prose summary, not a transcription of the hook's own header.

**Modify — `claude/.claude/tests/helpers.py`**

Add `run_hook_raw(hook, tool_input, home=None, extra_env=None) -> subprocess.CompletedProcess` and `registered_hook_event_name(hook_path, settings_path=...) -> str`, both with docstrings naming why they exist alongside `run_hook_advisory`/`run_hook_payload` (neither can assert on `systemMessage`).

*Reuse:* the existing module-private `_build_subprocess_env`.

**Modify — `claude/.claude/hooks/tests/test_announce_resume_command.py` and `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py`**

Mechanical: delete each file's `_run_hook_raw` and `_registered_post_tool_use_event_name` and import the shared helpers instead. No assertion changes.

**Create — `claude/.claude/hooks/tests/test_announce_plan_review_path.py`**

Mirrors `test_announce_resume_command.py`'s class layout and section-comment headings. Coverage groups:

- *Config-dir resolution:* a `CLAUDE_CONFIG_DIR` that is not `$HOME/.claude` is honored — the root-cause regression its sibling pins.
- *Happy path:* a well-formed `Write` announces on both channels — `systemMessage` contains the path, and `hookSpecificOutput.additionalContext` contains both the path and the literal instructional substring "state it verbatim in the Output format closing line" (not merely the path), so a future edit that keeps the path but weakens or drops the instruction fails this assertion, mirroring the precedent's own specific-substring pattern (`assert "resume-context" in payload["systemMessage"]`) rather than a generic presence check.
- *Emitted contract shape:* `hookEventName` equals `registered_hook_event_name(...)` derived from `settings.json`, never a hardcoded literal.
- *Tool filtering (defense-in-depth):* `Edit`, `MultiEdit`, and `Read` payloads against the same path each emit nothing.
- *Path filtering:* parametrized non-matching paths emit nothing — the bare `<session-id>` PID marker, the `<session-id>.planmode-path` sibling (which must **not** fire this hook), and a `*.reviewed-plan-path` file outside the resolved config dir.
- *Content gate:* parametrized rejects — a relative path, a path with an embedded space, empty content, and a missing `content` key — each emit nothing; one trailing newline still announces.
- *Injection:* content spelled `"/tmp/plan\n\nSENTINEL-INJECT\n\nx.md"` emits nothing, asserting both `result.stdout == ""` and `"SENTINEL-INJECT" not in result.stdout`.
- *No subprocess reach:* a stub `git` on `PATH` with a `touch marker` side effect is never invoked on any path through the hook, pinning that the sibling's worktree block was not copied in.
- *Fail-open:* empty stdin, malformed JSON, and a copy of the hook run with no adjacent `_lib.sh` each exit 0 with empty stdout.

*Reuse:* `helpers.write_input(..., content=...)`, `edit_input`, `multiedit_input`, `read_input`, `isolated_home`, and the new shared `run_hook_raw`/`registered_hook_event_name`.

**Modify — `claude/.claude/hooks/tests/test_require_plan_review.py`**

Add `TestPlanReviewSkillReviewedPlanPathFixture`, modelled on `TestPlanReviewSkillPlanModeFixture` (line 2383). It runs `extract_skill_command(PLAN_REVIEW_SKILL, "declare-reviewed-plan-path")` after the `activate-gate` fixture and asserts the sibling lands at `<home>/.claude/.plan-review-active.d/<sid>.reviewed-plan-path` holding the path verbatim with no trailing newline. No chained *gate-behavior* test — the new sibling has no gate semantics by design, unlike `.planmode-path`, which `marker.sh write plan-review` reads.

That sibling's actual consumer is the new hook, not `marker.sh` — so also add a second test in this class, modelled on `test_declared_planmode_path_chains_through_marker_write_and_gate` (lines 2422-2445): run the real `declare-reviewed-plan-path` fixture to produce the sibling file on disk, read its actual bytes back, construct a genuine `Write` `tool_input` from that content, feed it to `announce-plan-review-path.sh` via `run_hook_raw`, and assert the announcement fires with the expected path and instructional substring. This is the composed test that catches a trailing-newline or path-template disagreement between the skill fixture and the hook that three independently-mocked unit tests could each pass while the composed path fails — the exact byte-for-byte coupling risk this plan's own Critical-files preamble names.

*Reuse:* `_seed_session`, `plan_review_repo`, `plan_review_home`, `run_skill_command`, `extract_skill_command`, and (for the new composed test) `run_hook_raw` from `claude/.claude/tests/helpers.py` (see the `helpers.py` entry above).

**Modify — `claude/.claude/hooks/tests/test_marker_script.py`**

- Extend the `deactivate plan-review` coverage to assert the `.reviewed-plan-path` sibling is removed alongside the three existing removals.
- Add to `TestMarkerScriptClearStale` a test parametrized over `("planmode-path", "reviewed-plan-path")` asserting neither suffix appears in `clear-stale`'s stdout eviction report. This is *not* the first coverage of the `.planmode-path` exemption — `TestMarkerScriptPlanModeSibling::test_clear_stale_does_not_evict_a_live_sibling` (lines 2247-2266) already asserts the sibling survives `clear-stale`, three ways (no/live/dead adjacent PID). The new test's net-new contribution is narrower: `TestMarkerScriptClearStale` asserts on eviction-report *content* (what `clear-stale` prints), while the existing test asserts only on file survival — the new test adds the stdout-content angle for both suffixes, it does not add first-ever survival coverage (row 10 corrected below).
- Add to `TestMarkerScriptPlanModeSibling` a case pinning that a present `.reviewed-plan-path` with **no** `.planmode-path` leaves `write plan-review` on the `_lib_active_plan_hash` path — the regression that would otherwise silently narrow the completion marker later.

## Verification

From the worktree root:

```
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

`select-tests.py` will widen to the full suite on its own for this diff, because `claude/.claude/tests/helpers.py` is a `GLOBAL_TRIGGER_PATHS` member (row 11). That is the repo's documented case 1 for a full-suite run, so take `select-tests.py`'s selection as-is rather than scoping it by hand.

The specific checks that must go green, named so a partial pass is not mistaken for a complete one:

- `test_hook_alignment.py` — Layer 0 docs coverage for the new hook, and Layer 1's `hook-class` header and no-bare-`jq`/no-bare-`grep` shape checks.
- `test_announce_plan_review_path.py` in full, plus the two migrated sibling test files.
- `test_require_plan_review.py::TestPlanReviewSkillReviewedPlanPathFixture` and the whole pre-existing `TestPlanReviewSkillPlanModeFixture`, proving the new fixture did not disturb the shipped one.
- `test_marker_script.py::TestMarkerScriptClearStale` and `::TestMarkerScriptPlanModeSibling`.
- `test_skills.py::TestTriggerAFenceScan`, which is what proves the new fenced fixture is exempt rather than a new violation.

Per `.claude/rules/skill-and-agent-self-review.md`, run `/skill-review` on the `plan-review/SKILL.md` diff before staging — it is hook-enforced at commit time — and `claude-hook-review` on the new hook per `.claude/rules/review-pipeline-dispatch.md`.

Deployment note, not a check: `claude/` is stowed from the main checkout, so the new hook does not fire in this worktree's own sessions and only goes live for stow consumers after merge and `git pull`. Verify behavior through the test suite, not by invoking `/plan-review` on this branch.

## Out of scope

- **The verdict word.** Approve / Approve with changes / Request changes requires genuine judgment; no mechanism computes or forces it. Engineer-confirmed this session.
- **Announcing beside the verdict rather than at Step 1.** Reachable but deliberately declined here. The only deterministic end-of-review tool call is the Bash `marker.sh deactivate plan-review`, which runs *after* the Output-format section and destroys the very sibling an announcement would read (G1), so no `PostToolUse` anchor exists that both post-dates the verdict and pre-dates teardown. The shape that would work is a `PreToolUse` hook on that Bash call using `_lib_emit_allow_with_context` — a different hook class from the one named as the intended outcome, and a separate decision.
- **A script-only announcement via `marker.sh deactivate plan-review` stdout.** Reachable — it needs no hook and no `settings.json` change — and declined for two reasons: it lands after the closing line is already written, and the engineer named a hook-backed `systemMessage` + `additionalContext` mechanism as the intended outcome.
- **Giving the new sibling a reader.** `marker.sh write plan-review` and `status` keep reading `.planmode-path` and otherwise hashing the full active plan set. Wiring the new file into either would narrow the completion marker's coverage below what `require-plan-review.sh` gates against.
- **Unifying `.planmode-path` and `.reviewed-plan-path` into one sibling.** The two have different contracts and different readers; collapsing them is a `marker.sh` gate-semantics change, not an announcement change.
- **Route-3 plans with no file on disk.** A plan that exists only in conversation text has no absolute path to announce; Step 1 skips the declaration and says so. `plan-review`'s own frontmatter already scopes chat-level and `/tmp` drafts out of the skill.
- **Extending the same announcement to other gate-releasing skills** (`/code-review`, `/ready-for-review`). Same bug shape may exist there; not investigated, and not bundled.
