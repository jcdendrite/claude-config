# Hook diffs reach staff-platform-engineer

## Context

Goal: ensure a `.claude/hooks/*.sh` diff under review reliably gets a
platform-engineer operational review. Today it does not. A hook PR that shelled
out to `docker` passed `/code-review` without `staff-platform-engineer` ever
being consulted; a follow-up manual review then found a Medium-severity
unbounded-latency bug. The miss is structural, not a one-off.

Hook diffs fall through every routing layer of the `code-review` skill:
`code-review` classifies `.claude/hooks/*.sh` into the "Claude Code config"
domain (not "Infrastructure"), delegates hook review to the `claude-hook-review`
skill (SKILL.md line 147), and has no ripple-triage row that spawns
`staff-platform-engineer` for hooks — even though the Item-ownership table
(item 36) names `staff-platform-engineer` as primary owner of "Hook
correctness." The ownership is declared but never actuated, because the
delegate skill itself spawns nothing.

This plan closes that gap at the delegate (`claude-hook-review`) so a hook
review reaches `staff-platform-engineer` whether `claude-hook-review` is entered
via `code-review` or auto-triggered directly. It also records a decision on the
broader "Gap B" question — whether `code-review` Step 0 domain detection should
become content-aware — recommending deferral, with the rationale below.

## Approach

### Gap A — claude-hook-review spawns staff-platform-engineer (primary fix)

Add an operational-footprint escalation step to `claude-hook-review/SKILL.md`:
after the section-9 review checklist, the skill spawns `staff-platform-engineer`
synchronously with a hook-specific question (latency budget per fire,
unbounded/blocking external commands and missing timeouts, daemon/network/
process dependencies, failure modes when the gated tool is slow or absent),
reads the returned findings, and folds them into the review output.

**Fix site = `claude-hook-review`, not `code-review`.** `code-review` correctly
delegates hook review to `claude-hook-review`; placing the spawn at the delegate
means it fires on both entry paths — `code-review` → `claude-hook-review`, and
`claude-hook-review` auto-triggered directly ("designing a new hook"). A
`code-review` triage-table row would cover only the first path. It also avoids
editing `code-review`'s routing tables, which would itself trip that skill's
"Reshapes reviewer ownership" triage row and force a full multi-persona review
of this PR.

Mechanism notes (the brief asked these be confirmed):

- **No `allowed-tools` change.** `code-review/SKILL.md` carries
  `allowed-tools: Read, Grep, Glob, Bash` (no `Agent`) and spawns specialist
  subagents throughout its ripple-triage section — the direct, working
  precedent that a review skill with that exact allowlist spawns agents.
  `claude-hook-review` will mirror it: leave `allowed-tools` unchanged, do not
  add `Agent`. Adding `Agent` to `claude-hook-review` alone would diverge from
  the established pattern. The brief's hypothesis that `Agent` must be added to
  `allowed-tools` is superseded by the `code-review` precedent.
- **`user-invocable: false` is irrelevant to spawning.** Per
  `skill-review/SKILL.md`, `user-invocable: false` only hides a skill from the
  slash menu; it does not change the runtime tool context. The skill body runs
  inline in the main session, which holds the `Agent` tool.
  `lovable-cloud-migration-sync` is an existing skill that spawns agents.
- **No `findings_path`.** `code-review`'s file-based-findings canary is scoped
  to `staff-backend-engineer` alone. `staff-platform-engineer` returns inline
  structured findings; the spawn prompt states the ≤2K-token budget.

`ciso-reviewer` is item 36's co-owner. Keep that conditional, not mandatory:
the spawn step adds `ciso-reviewer` only when the hook itself gates a security
boundary (auth, secrets, env-var reads, private-data redaction) — consistent
with `code-review`'s existing "always spawn `ciso-reviewer` when the change
touches auth/secrets" rule. Most hooks are security gates, so this will fire
often in practice, but tying it to the boundary keeps a purely cosmetic hook
(e.g. a formatting reminder) from over-spawning.

Alternatives weighed and set aside: a `code-review` triage-table row (covers
only the via-`code-review` path, edits the ownership tables); folding the
operational checks into `claude-hook-review`'s deterministic checklist instead
of spawning (a static checklist cannot replicate `staff-platform-engineer`'s
holistic operational judgment — and is exactly what already failed); and
reclassifying `.sh` files in Step 0 (Gap B — broader, see below).

### Gap A, secondary — close the deterministic checklist gap in section 7

The motivating incident (unbounded `docker inspect` hanging the hook) also
slipped section 7 ("Performance budget"), which today names subprocess-spawn
cost and unbounded file I/O but not external commands that can block
indefinitely. Add one checklist line: external commands that contact a daemon,
socket, or network (`docker`, `systemctl`, `curl`, package managers) must run
under an explicit timeout, since the <100ms per-fire budget cannot be met if
the command hangs. This is a deterministic catch that does not depend on the
probabilistic agent spawn — the two are complementary halves of the same
incident's fix, kept in this PR rather than split into a one-line follow-up.

### Gap B decision — defer; do not make Step 0 content-aware

Recommendation: **defer.** Do not implement content-aware domain detection in
`code-review` Step 0 as part of this work.

The brief frames Step 0's glob-based detection as contradicting the triage
preamble's "routing keys on what the change does, not file types." It is better
read as two layers than a contradiction: Step 0 is a cheap glob first-pass
selecting which *checklist* applies; the ripple-triage table is the
content-aware layer selecting which *reviewers* escalate — and that preamble
sentence sits inside the triage section, describing the triage table
specifically. The concrete hole the incident exposed is a missing escalation
for hooks, which Gap A closes.

Making Step 0 parse file bodies (`.sh` containing `docker`/`systemctl`/`curl`
→ Infrastructure) is a heavier mechanism than the gap requires: it changes
domain classification for every stow user, adds content-scanning to every
review, and would mis-fire the Infrastructure checklist — items 15-19 are
GitHub-Actions-specific (concurrency groups, `run:` secret exposure, workflow
permissions) and none apply to a local hook script. The right response to a
future content-invisible gap is another targeted triage-table row, not a Step 0
rewrite. If the user wants Step 0 content-awareness pursued regardless, that is
a separate plan with its own `/plan-review`, per the brief — and this plan does
not block on it.

## Critical files

- `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` — add the
  operational-escalation section (spawn `staff-platform-engineer`, conditional
  `ciso-reviewer`); add the section-7 blocking-external-command line.
  Currently 112 lines; the 200-line skill-length gate has ample room.
- `plugins/claude-hook-review/.claude-plugin/plugin.json` — bump `version`
  `1.0.0` → `1.1.0` (additive capability, backward compatible) per the
  `plugin-semver` skill.
- `plugins/claude-hook-review/skills/claude-hook-review/REFERENCES.md` —
  record the motivating failure mode (a hook shelling to a daemon with no
  timeout; the platform persona never spawned) as edit-time rationale for the
  new section, abstracted per the repo's redaction rule. REFERENCES.md is the
  designated store for the why behind a skill's rules, so this is a definite
  step, not optional.

**Reuse:** the spawn step mirrors `code-review/SKILL.md`'s ripple-triage spawn
pattern — synchronous spawn, a specific question, a ≤2K-token inline findings
budget, mandatory read-back. No new mechanism is introduced.

**Not edited:** `claude/.claude/skills/code-review/SKILL.md` — its item-36
ownership row is already correct; Gap A actuates that row rather than changing
it.

## Verification

- `/skill-review` on the `claude-hook-review` SKILL.md change (hook-enforced —
  fires when a staged change includes a SKILL.md) and `/code-review`. The
  `plugin-semver` review fires on the plugin-directory edit.
- check-runner: `pytest claude/.claude/` and `ruff check claude/.claude/` —
  confirm no regressions. The change is prose in a plugin SKILL.md and no test
  reads that file, but the suite should stay green.
- Confirm `claude-hook-review/SKILL.md` stays ≤200 lines after the edit.
- Functional smoke test (manual — this repo has no `claude -p` CI harness):
  invoke `claude-hook-review` on a sample hook diff that shells to an external
  command, and confirm `staff-platform-engineer` is spawned and its findings
  surface. Probabilistic; run once to confirm the instruction fires.

## Out of scope

- Gap B implementation (content-aware Step 0). Deferred with rationale above;
  a separate plan if the user chooses to pursue it.
- `code-review/SKILL.md` routing tables — not edited.
- If verification ever shows the harness hard-enforces skill `allowed-tools`
  against the `Agent` tool, `code-review` would carry the same latent gap
  (spawns agents, omits `Agent` from its allowlist). Flag it separately; do not
  bundle a `code-review` allowlist change into this PR.
