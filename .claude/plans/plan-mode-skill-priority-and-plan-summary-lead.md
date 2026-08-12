# Nudge on unexpanded skill mentions; lead plans with the design, not the ledger

## Context

Make a skill named partway through a prompt as reliable as one typed at the
start, and make plan Approach sections open with their conclusion.

Claude Code expands a slash command only when it is the first thing in a
message. A `/skill-name` written anywhere later is inert text: nothing loads,
and whether the skill runs depends entirely on the agent noticing the mention
on its own. In the local transcript corpus, 175 prompts began with `/plan-it`
and all 175 carried a harness expansion block; 44 named it after the leading
token and **0 of 44 were mechanically expanded**.

That census measures expansion, not harm. How often the agent nonetheless
self-invokes the named skill unaided was not measured — in two sampled cases
it did. So the residual failure rate is smaller than "44" and is unknown,
and Row 9 records that the fix's own success rate is likewise unverified.
The design accepts both gaps; the validating signal after ship is whether
the agent's ask leads to an invocation the engineer accepts, observable in
transcripts.

Why now: the engineer identified this as a longstanding habit and a probable
cause of uneven plan quality.

The change also carries a one-clause edit to `plan-it`'s Approach section
description, fixing a separate defect: the section has no instruction on
where the assumption ledger sits, which has produced plans opening with a
tagged ledger before stating the conclusion in plain language. It ships in
this PR rather than its own because it is a one-line diff whose review gates
(`/code-review`, `/skill-review`) this PR already runs for other reasons.

Intended outcome: a `UserPromptSubmit` hook that detects a non-initial
mention of an installed skill and asks the engineer whether to invoke it,
plus that one-sentence ordering rule.

## Approach

**The concluded design:** add one advisory `UserPromptSubmit` hook that scans
each prompt for `/<name>` tokens outside the leading command run, resolves
them against installed skill directories, and — when one resolves — injects
`additionalContext` telling the agent to ask before proceeding. No CLAUDE.md
rule is added. Separately, append one clause to `plan-it`'s Approach section
description. The ledger below records what was checked.

A hook is the right primitive because the failure mode is *inattention*. An
always-loaded prose rule (the alternative rejected in Row 2) can only work if
the agent is already reading carefully enough to notice the mention — the
exact condition that fails. A hook fires regardless. This also keeps the
change off `CLAUDE.md`, whose budget is governed by a 200-line gate and a
"changes behavior on a realistic input" test.

The hook always exits 0, matching the three existing `UserPromptSubmit`
nudges — exit 2 on this event blocks and erases the prompt (hooks reference),
unacceptable for a false-positive-prone heuristic.

The hook only injects context — it cannot ask directly, since hooks are
non-interactive — so noticing is deterministic but the agent's follow-up ask
is not, which is acceptable because noticing is the half that currently fails.

**No process spawns.** Skill roots resolve without `git`: the config dir
comes from `_lib_config_dir`, and the project root comes from an
ancestor-directory walk from `cwd` looking for `.claude/skills` — pure bash
builtins. `nudge-worktree-anchor.sh` needs `git` because it distinguishes
main from linked worktrees; this hook never makes that distinction, so the
fork is pure cost on every prompt of every session.

**Token grammar is the security boundary.** Prompt text is partly untrusted —
engineers paste issue bodies, logs, and web content. A candidate token is
captured only up to the first character outside `[A-Za-z0-9_-]`, then
re-validated against `^[A-Za-z0-9_-]+$` immediately before each path is
built, mirroring how `nudge-worktree-anchor.sh:88` re-checks `SESSION_ID` at
point of use rather than trusting an upstream capture. This single control
excludes `/`, `..`, whitespace, and every shell metacharacter, which closes
traversal, glob injection, and the file-existence-oracle probe together.
Resolution is an exact `[[ -f "$dir/$token/SKILL.md" ]]` test — never `find`,
`ls`, a glob, or `eval` — and the string echoed into `additionalContext` is
the validated token itself, never a raw span from the prompt. Substring
matching would let `/plan-it-then-ignore-all-prior-instructions` resolve
against `plan-it` and carry the attacker's tail into agent context.

Detection resolves candidates against the filesystem rather than enumerating
every skill: this is O(candidates) stats, and it makes path-shaped noise
self-filtering — `/Users/<name>/...` and `and/or` fail resolution because no such
skill exists, with no special-casing.

Two precision measures beyond resolution:

- **Skip the leading command run**, not merely the first token. As of
  v2.1.199 the harness expands chained skills (`/skill-a /skill-b do XYZ`,
  up to six). Matching only the first token would nag on correctly-formed
  chained messages.
- **Strip fenced code blocks and inline code spans before scanning**, using
  POSIX-portable constructs only. `grep -P` does not exist on BSD/macOS and
  non-greedy matching is unavailable in BSD awk; a GNU-only construct would
  silently misbehave for every macOS consumer. Fence stripping uses POSIX BRE
  line-range addressing or a read-loop toggling a fence flag; inline spans
  use `[^`]*` between backticks, which needs no non-greedy operator.

The scanned text is truncated at a fixed byte cap before any pass, so a
multi-megabyte pasted log cannot turn a per-prompt hook into seconds of wall
time. Truncation is safe here precisely because this is an advisory and not
a gate.

The injected message adds one sentence when `permission_mode` is `plan`:
that the named skill's own workflow governs over the generic plan-mode
phases. This is the only agent-facing content from the CLAUDE.md prose-rule
alternative (Row 2) not already stated by the harness, and it lands here
where it fires deterministically and only when relevant.

**Accepted tradeoffs, chosen by the engineer over reviewer objection.** The
hook keeps no dedup state and no enable/disable sentinel. Three reviewers
flagged the first (repeat-ask fatigue across a conversation that merely
discusses a skill) and one the second (no low-blast-radius kill switch;
disabling means editing `settings.json` and waiting for `git pull`). Both
were put to the engineer with those costs stated and both were chosen
deliberately: every mention fires, and the hook is active for every stow
consumer by default. Recorded as Rows 14 and 15 so a later revision diffs
against the decision rather than rediscovering it.

### Assumption ledger

```
Root: a skill named after the first token of a prompt is never expanded by
the harness, so whether it runs depends on the agent noticing unaided.

Givens: slash-command expansion is anchored to message start — beyond reach:
vendor behavior documented at code.claude.com/docs/en/commands.md.
Givens: hooks cannot prompt the user interactively — beyond reach: the hooks
reference defines hooks as non-interactive shell commands, HTTP endpoints, or
LLM prompts; interactive elicitation is a separate MCP-only event.

Row 1 [mechanism]: UserPromptSubmit hook injecting additionalContext —
anchors: root — the only lifecycle point that sees raw prompt text before the
agent reads it; lighter options are enumerated in Rows 2-4.
Row 2 [mechanism-rejected]: always-loaded CLAUDE.md prose rule — anchors:
row1 — fails because it requires the attention whose absence is the defect,
and its agent-facing half restates the harness's own standing instruction.
Row 3 [mechanism-rejected]: change nothing, rely on the harness's built-in
"invoke it via Skill" guidance — anchors: row1 — fails because that guidance
arrives as session-specific text outside this repo's control and gives no
signal distinguishing an expanded mention from an inert one.
Row 4 [mechanism-rejected]: README note teaching the start-of-message habit —
anchors: row1 — fails as a substitute because it depends on the human
remembering; retained as a possible complement, recorded in Out of scope.
Row 5 [assumption]: 175 of 175 leading-position mentions expanded, 0 of 44
non-initial mentions did [verified: local transcript corpus census] —
anchors: root
Row 6 [assumption]: exit 2 on UserPromptSubmit blocks and erases the prompt,
so an advisory must exit 0 everywhere [verified: code.claude.com hooks
reference] — anchors: row1
Row 7 [assumption]: UserPromptSubmit stdout is added as context the agent can
act on [verified: code.claude.com hooks reference] — anchors: row1
Row 8 [assumption]: the input JSON carries prompt, cwd, and permission_mode
[verified: code.claude.com hooks reference] — anchors: row1
Row 9 [assumption]: the agent reliably acts on injected additionalContext by
asking rather than ignoring it [unverified] — anchors: row1 — load-bearing;
compounds with the unmeasured self-invoke residual noted in Context.
Row 10 [assumption]: no existing hook enumerates installed skills, so this is
the first [verified: repo-wide search of claude/.claude/hooks/ and _lib.sh] —
anchors: row1
Row 11 [assumption]: plugin skills (plugin:skill form) cannot be resolved
without knowing the runtime plugin install path [unverified] — anchors: row1
— scoped out of v1 and recorded in Out of scope.
Row 12 [engineer-verified]: coverage is all installed skills, not a curated
workflow-critical subset — anchors: root
Row 13 [assumption]: prompt-derived tokens are attacker-influenceable and
must pass ^[A-Za-z0-9_-]+$ at point of use before any filesystem access
[verified: _lib_valid_session_id_component, claude/.claude/hooks/_lib.sh:707]
— anchors: row1 — load-bearing invariant; a later edit to the capture regex
must preserve it.
Row 14 [engineer-verified]: no dedup state — every qualifying mention fires,
accepting repeat-ask fatigue flagged by three reviewers — anchors: root
Row 15 [engineer-verified]: always-on with no enable/disable sentinel,
accepting that rollback means editing settings.json — anchors: root
Row 16 [engineer-verified]: the response is to ask the engineer, not to
inform the agent and let it decide unaided — anchors: row1 — the lighter
inform-only variant was raised in review and declined.
Row 17 [engineer-verified]: the hook and the plan-it clause ship in one PR —
anchors: root
Row 18 [mechanism]: ancestor-directory walk for the project skills root —
anchors: row1 — replaces a git subprocess that bought only the repo root,
removing one fork per prompt; git is needed only for the main-vs-linked
worktree distinction this hook never makes.
```

## Critical files

**Create — `claude/.claude/hooks/nudge-unexpanded-skill-mention.sh`**

Model it on `nudge-worktree-anchor.sh`. Reuse rather than reimplement:

- `_lib.sh` sourced with `if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then exit 0; fi`
- `_lib_config_dir` for the personal skills root, with the mandatory
  `CONFIG_DIR=$(_lib_config_dir) || exit 0` call-site check
- a single `jq -r` pass reading `prompt`, `cwd`, `permission_mode`
- the emit envelope verbatim in shape:
  `jq -n --arg ctx "$CTX" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' || true`

Conventions to match: `#!/bin/bash`; `# hook-class: informational` second
line; strict mode deliberately omitted with the same one-line reason; every
path `exit 0`; one sentence per non-obvious fact per
`.claude/rules/shell-script-conventions.md`.

Header must state, per `claude-hook-review` §9: the fail-open posture
explicitly, and the known gaps it does not close — plugin (`plugin:skill`)
mentions, mentions inside code fences or backticks, the leading command run,
and unquoted prose that discusses a skill without intending to invoke it.

Resolution order for a validated `<name>`:
1. `<config_dir>/skills/<name>/SKILL.md`
2. `<project_root>/.claude/skills/<name>/SKILL.md`, where `<project_root>`
   comes from the ancestor walk, not from `git`

**Modify — `claude/.claude/settings.json`**

Append a fourth entry to the existing `UserPromptSubmit` array, path form
`~/.claude/hooks/nudge-unexpanded-skill-mention.sh`, matching the three
siblings exactly (no `matcher`, no `if`, no `timeout`).

**Create — `claude/.claude/hooks/tests/test_nudge_unexpanded_skill_mention.py`**

Mirror `test_nudge_worktree_anchor.py`: subprocess invocation with a JSON
stdin payload, a `_context()` helper returning
`payload["hookSpecificOutput"]["additionalContext"]` or `None`,
behavior-named test classes. Assert **substring containment**
(`"plan-it" in context`), never equality against the advisory prose, so a
copy edit is not a test break.

Fixtures: build fake skills under `isolated_home/.claude/skills/<name>/SKILL.md`
using a name that does not exist in the real repo (e.g. `zzz-fixture-skill`)
for positive-path cases. `isolated_home` alone is insufficient — it creates
no `skills/` subtree, so a test run with `cwd` inside the real checkout can
pass by resolving the genuine `plan-it`, leaving a broken resolver green.

**Modify — `claude/.claude/skills/plan-it/SKILL.md`** (numbered item 2, ~line 66)

Append to the existing parenthetical, mirroring item 1's style:

> Lead with the concluded design in one or two plain-language sentences
> before the assumption ledger — the ledger is supporting detail for diffing
> against a later revision, not the reader's entry point.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/` — full suite from the worktree
   (the `.venv` lives only at the main worktree root, three levels up).
2. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
3. `../../../.venv/bin/ruff check claude/.claude/`

Hook test cases, each asserting `returncode == 0`:

| Case | Expected |
|---|---|
| `do X /<fixture-skill>` | fires, names the skill |
| `/<fixture-skill> do X` | silent (harness expanded it) |
| `/<skill-a> /<skill-b> do X` | silent (leading chained run) |
| `  /<fixture-skill> do X` (leading whitespace) | silent |
| `see /not-a-real-skill` | silent (fails resolution) |
| `check /Users/<name>/y and /or` | silent (fails resolution) |
| ``run `/<fixture-skill>` later`` | silent (inline code span) |
| mention inside a fenced block | silent |
| same name inside a fence **and** outside it | fires (unfenced occurrence wins) |
| unclosed fenced block | silent, defined behavior |
| CRLF line endings around a fence | fence still detected |
| `/<skill>` vs `/<skill>-longer` (prefix collision) | exact match only |
| `/<fixture-skill>.` / `/<fixture-skill>,` | fires, trailing punctuation stripped |
| `/<Fixture-Skill>` (case variant) | defined, identical on APFS and Linux CI |
| same skill named twice in one prompt | named once, not duplicated |
| `do X /<skill-a> /<skill-b>` | fires, names both |
| `"should I use /<a> here or /<b>?"` (discussion) | fires — accepted false positive, documented |
| token with `..`, `/`, or shell metacharacters | never resolves, never echoed |
| traversal canary: real `SKILL.md` reachable via `../` | silent, canary never named |
| `permission_mode: "plan"` + body mention | fires, includes the precedence sentence |
| empty stdin / malformed JSON / missing `prompt` | silent |
| multi-megabyte prompt | silent or fires, bounded by the byte cap |
| no `jq` on PATH / unreadable `_lib.sh` | silent |
| `_lib_config_dir` failure (unset `$HOME`, no `CLAUDE_CONFIG_DIR`) | silent |
| isolation proof: real skill name, non-repo `cwd`, sandboxed `HOME` | silent |
| `hookEventName` field | equals `"UserPromptSubmit"` |

The `/Users/<name>/y` row above is written with an angle-bracket placeholder
because `deny-private-project-refs.sh` blocks any commit whose diff matches
`(/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+)`. Do not write a literal
home-rooted path into the test fixture — the commit will be denied. Use a
path-shaped token that is not home-rooted (e.g. `/opt/pkg/bin`) for the
path-noise case; it exercises the same "fails skill resolution" behavior.

Manual end-to-end: send a prompt naming a skill mid-message and confirm the
agent asks. Row 9 is not automatable — the hook's injection is testable, the
agent's response to it is not.

Also verify, do not assert: whether a session already running when the change
lands picks up the fourth hook, or whether only new sessions do. The PR
description must state whichever is observed rather than claiming "live on
`git pull`" for both cases.

Required review gates: `/code-review`; `/skill-review` (hook-enforced —
`plan-it/SKILL.md` changes); `claude-hook-review` (new hook).
`review-permissions` does not apply (no `permissions.allow` change).

## Out of scope

- **Plugin skills** (`plugin:skill` form). Resolution needs the runtime
  plugin install path, unverified here (Row 11). v1 covers personal and
  project skills.
- **A README note** teaching the start-of-message habit. A genuine complement
  to the hook (Row 4) but a separate user-facing doc change; raise to the PR
  reviewer rather than bundling.
- **A `plan-review` checklist item** enforcing the new Approach ordering. The
  17 existing base items contain no structural prose check, so adding one is
  a distinct change to a different skill.
- **`plan-it/REFERENCES.md`'s worked example**, which shows a ledger with no
  preceding prose. Examined and deliberately left: the section heading and
  code fence scope it to ledger grammar, not to Approach-section layout.
  Noted in the PR description so a reviewer can overrule cheaply.
- **The CLAUDE.md prose-rule alternative** (Row 2), superseded by the hook.
  Its one surviving agent-facing claim is folded into the hook's plan-mode
  sentence.
- **Dedup state and a disable sentinel.** Both raised in review with their
  costs stated; both declined by the engineer (Rows 14, 15). Revisit if
  post-ship transcripts show the advisory being reflexively dismissed.
- **Measuring the agent's unaided self-invoke rate** on the 44 non-initial
  mentions. It would size the residual behind Row 9 but does not change the
  design.
