# Make the plan-review routing gate's Read-tool requirement explicit

## Context

`/plan-review`'s specialist-spawn gate (`require-routing-read.sh`) only
credits a Read-tool invocation of `ROUTING.md` — `log-routing-read.sh`
matches strictly on `tool_name == "Read"` — but neither the skill's own
routing directive nor the hook's deny message says so, so a session that
inspects `ROUTING.md` via Bash (`cat`, `sed`, `grep`, …) gets denied
without knowing why its read didn't count. One session hit this twice in
a row before finding the fix: "The routing gate needs a Read-tool read of
ROUTING.md, and it re-arms per spawn round. I burned two spawn attempts on
that this session — a Bash sed read doesn't satisfy it." The fix is to
state the Read-tool requirement in both places a session encounters this
gate: the skill's own instruction to read `ROUTING.md`, and the hook's
deny message when that instruction was skipped or satisfied the wrong
way.

## Approach

Add one clause to each of the two texts a session sees at this gate — the
skill's routing-read directive and the hook's deny message — naming the
Read tool explicitly and naming Bash as the thing that doesn't count.
No mechanism change: the gate already works correctly (verified by the
existing `test_require_routing_read.py` suite); the failure mode is
purely that a session guesses wrong about *which* tool call satisfies it.

**Alternative considered:** teach `log-routing-read.sh` to also credit a
Bash command that reads `ROUTING.md` (`cat`/`sed`/`grep` matching the
path in `tool_input.command`). Rejected: parsing free-form Bash command
text for a file-path reference is spoofable and unreliable — a command
can reference the path without the model ever seeing the full current
file (`grep -c`, `head -1`, a path embedded in an unrelated string), so
crediting it would weaken the guarantee the marker exists to encode ("the
model saw the full current routing content before spawning"). The
Read tool is already available and free to call; the friction is a
knowledge gap about which tool to use, not a capability gap — a prose fix
closes it without loosening what the gate actually verifies. This also
matches what was asked: a wording fix in the skill and the hook message,
not a hook-logic change.

**Assumption ledger**

- Root problem: agents reading `ROUTING.md` via Bash instead of the Read
  tool get denied by `require-routing-read.sh` without knowing why,
  because neither the skill's directive nor the hook's deny message names
  the Read-tool requirement.
No genuine givens — both conditions this design leaves untouched
(`log-routing-read.sh`'s Read-only match, the marker's 60-minute freshness
window) are in-reach and the plan could change either; they're deliberate
declines, not fixed constraints, so their reasoning lives in **Out of
scope** below rather than here.

| Mechanism | Justification | anchors |
|---|---|---|
| Reword `plan-review/SKILL.md`'s routing-read directive to name the Read tool | The only place a session is told to read `ROUTING.md` before spawning; matches existing repo phrasing precedent ("read it with the Read tool") used in this same file's Step 2.5 and four sibling skills | root |
| Reword `require-routing-read.sh`'s deny message to name the Read tool | The only text a session sees at the moment of denial, when it's most likely to act on the correction immediately | root |
| Tighten `test_require_routing_read.py`'s existing deny-message test to also assert on "Read tool" | Prevents the reworded message from silently regressing to the old ambiguous form | row1, row2 |

## Critical files

- `claude/.claude/skills/plan-review/SKILL.md` — reword the "## Reviewer
  routing" section (currently: `` Read `${CLAUDE_SKILL_DIR}/ROUTING.md`
  before any spawn decision. ``) to say the read must go through the Read
  tool and that a Bash-based read doesn't satisfy the gate. Reuse this
  repo's existing phrasing precedent ("read it with the Read tool") rather
  than inventing new wording — see `code-review/SKILL.md:25`,
  `plan-it/SKILL.md:31`, `test-conventions/SKILL.md:15` for the pattern.
- `claude/.claude/hooks/require-routing-read.sh` — reword the
  `emit_deny` call (currently: `"Agent spawn blocked by plan-review
  routing gate: Read ~/.claude/skills/plan-review/ROUTING.md before
  spawning any specialist agent. ..."`) to state the Read-tool
  requirement and name Bash reads as insufficient, matching the
  explanatory style `deny-env-reads.sh` already uses for its own deny
  message (names the blocked path, states why, states the alternative).
- `claude/.claude/hooks/tests/test_require_routing_read.py` — extend
  `test_deny_message_names_routing_md` (or add a sibling assertion) to
  also check the new "Read tool" wording is present in the deny reason,
  so the fix has a regression check.

**Reuse opportunities:** no new mechanism, marker path, or hook logic —
this is a text-only change to two existing strings plus one test
assertion.

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_routing_read.py` —
  confirms the existing gate behavior is untouched and the new deny-message
  assertion passes.
- `../../../.venv/bin/ruff check claude/.claude/hooks/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
  the hook edit is a single-line string change but both linters are cheap
  to re-run.
- Manual read-through of the reworded `SKILL.md` section and hook message
  to confirm both name the Read tool and both stay one sentence per
  CLAUDE.md's comment/doc-prose conventions (no multi-paragraph rationale
  block).

## Out of scope

- Changing `log-routing-read.sh` to also credit Bash-based reads of
  `ROUTING.md` (the rejected alternative above; it matches only
  `tool_name == "Read"` — `[verified: claude/.claude/hooks/log-routing-read.sh:12]`).
- Changing the 60-minute routing-read marker freshness window
  (`require-routing-read.sh`'s `-mmin -60` check —
  `[verified: claude/.claude/hooks/require-routing-read.sh:64]`), or any
  other re-review/re-arm timing behavior — existing, deliberate design;
  the reported friction was about *which tool* satisfies the gate, not
  the window's length.
- Any change to `plan-it/SKILL.md` or `code-review/SKILL.md` — neither
  gates a `ROUTING.md`-style Read-tool requirement (`code-review`
  dispatches per file type with no equivalent hook-tracked Read
  precondition; confirmed by grep — only `plan-review` has this gate).
