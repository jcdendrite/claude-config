# Cross-repo subagent cost attribution: two fixes

## Context

Fix two gaps surfaced by a root-cause investigation into a `cost --summary`
result that came back all-zero for a branch with real, merged work. Why now:
the investigation traced the symptom to two distinct, independently-fixable
issues — a latent hardcoded-path inconsistency in `pr-description`'s Cost
section, and a real branch-attribution gap in `transcript-analysis.py` that
the current docs describe as narrower than it actually is. Intended outcome:
`pr-description`'s Cost section resolves its script path the same
account-aware way it already resolves the disclosure-sentinel gate two lines
above it, and `docs/transcript-analysis.md` tells a reader plainly that an
all-zero `--branches` result for a subagent dispatched into a different,
already-existing repo is not proof of zero spend.

No code fix to `transcript-analysis.py` itself is proposed here — see "Out of
scope" for why.

## Approach

**Assumption ledger**

```
Root: pr-description's Cost section has a latent, currently-symptomless path
inconsistency, and transcript-analysis.py's docs understate a real,
reproducible branch-attribution gap for subagents dispatched cross-repo —
together these can make a genuine all-zero `cost --branches` result look
like proof of zero spend when it isn't.

Givens: each JSONL transcript record's gitBranch field is written by the
Claude Code harness at record-append time, not derived by
transcript-analysis.py — beyond reach: the harness, not this repo, owns
what gets stamped into that field per record.

Row 1 [mechanism]: reuse $config_dir (already resolved by the Cost
section's own gate, two code blocks above) as the script-path prefix,
instead of introducing a second, independent resolution — anchors: root —
matches the section's existing single-resolution structure; the two
lighter alternatives considered were (a) leave it hardcoded, rejected
because it is the exact inconsistency being fixed, and (b) re-derive
config_dir independently inside the second code block, rejected because it
duplicates the 4-line case statement for no behavioral gain when both
blocks already run as one gated, sequential instruction flow.
Row 2 [assumption]: both bash code blocks in the Cost section execute in
the same shell/session, so $config_dir set in the gate block is still in
scope when the second block runs [verified:
claude/.claude/skills/pr-description/SKILL.md:71-95 — the second block is
explicitly gated on the first block's `[ "$mode" = "dollars" ]` check
succeeding, i.e. the section is written as one sequential procedure] —
anchors: row1
Row 3 [mechanism]: append a caveat sentence to the existing "subagent
dispatched from another repo's session" bullet, plus a one-line forward
cross-reference from the cost section's "Worktree-isolated subagent
attribution" paragraph, instead of adding a new standalone doc section —
anchors: root — keeps one authoritative description of the gap (a new
section would duplicate it) while making it discoverable from both places
a reader lands on it.
Row 4 [assumption]: the exact harness-level reason a cross-repo-dispatched
subagent's gitBranch field doesn't reflect its own cwd's branch is not
confirmed by this session [unverified] — anchors: row3 — the doc wording
states the observed, reproducible consequence only (an all-zero --branches
result surviving a fully unscoped corpus read), not a causal mechanism.
Row 5 [assumption]: the reported symptom — a genuine branch with real,
merged work returning an all-zero cost --branches result — was
independently reproduced against real account data this session, not just
reported secondhand [engineer-verified] — anchors: root — corroborated by
direct corpus queries against the account in question in the same
investigating session; not re-derivable from this plan alone, since the
underlying data is a private client's transcripts this plan does not and
must not name.
Row 6 [mechanism]: keep $config_dir literal (unresolved) when the Cost
section's closing instruction echoes "the exact command" into a PR body,
resolving only $branch — anchors: row1 — surfaced by skill-review: turning
the query into a two-variable command made the existing "(branch filled
in)" wording ambiguous about $config_dir, and resolving it to its real
filesystem value on any non-default account is a home-rooted absolute
path — one of this repo's own deny-private-project-refs structural
detectors, which fires on the same gh pr create/edit calls this section
feeds. The lighter alternative (leave the wording as-is and rely on an
executing agent inferring the same scope) was rejected because it's the
exact ambiguity that produced the gap.
```

**Fix 1 — `pr-description`'s hardcoded script path.** The Cost section's
disclosure-sentinel gate resolves `config_dir` as `$CLAUDE_CONFIG_DIR` (if
absolute) else `$HOME/.claude`, then reads
`<config-dir>/pr-cost-disclosure`. Two code blocks later, the same section's
cost query hardcodes `~/.claude/scripts/transcript-analysis.py` instead of
reusing that already-resolved `$config_dir`. It happens not to matter
whenever `~/.claude/scripts/transcript-analysis.py` exists and is reachable
regardless of which account is active — but that's an accident of every
execution context observed so far, not a guarantee, and it's a plain
inconsistency against the gate's own account-aware resolution three lines
above it. Fix: reuse `$config_dir` in the query line. No alternative
mechanism was considered beyond reusing vs. re-deriving (see Row 1) — this is
a one-line, trivial-choice fix.

Turning the query into a two-variable command (`$config_dir` and `$branch`,
where previously only `$branch` varied) exposed a second, smaller gap in the
same section's closing instruction: "embed stdout verbatim... followed by
the exact command (branch filled in)" (line 97) named only `branch` as the
thing to resolve, because that was previously the only variable. Left as-is,
an executing agent could plausibly resolve `$config_dir` to its literal
filesystem value when echoing the command into the PR body — on any account
other than the bare-personal default, that's a home-rooted absolute path,
one of this repo's own `deny-private-project-refs` structural detectors,
firing on the very `gh pr create`/`gh pr edit` calls this section feeds
(see Row 6). Fix: reword line 97 to state `$config_dir` stays literal in the
echoed command, only `$branch` is filled in.

**Fix 2 — `docs/transcript-analysis.md` understates the gap.** The existing
"subagent dispatched from another repo's session" bullet documents a
directory-*identity* gap (`--this-repo` can't find the subagent's transcript
file by worktree-list matching) and states a content-based fallback
(content-grep `*/subagents/*.jsonl`, or read each file's own `cwd` field).
That fallback recovers *scope* — which transcript file to read — but this
investigation found a deeper problem it doesn't cover: even once a
subagent's transcript is located by content rather than directory identity,
its own recorded `gitBranch` value is not reliable for **branch
attribution** when the subagent's cwd is a different, already-existing repo
than its dispatching session's. In the investigated case, a `--branches
<real-branch>` filter matched zero records for that subagent's real,
merged work even under a fully unscoped, all-projects, all-threads read of
the entire corpus — the documented fallback doesn't close this gap because
the field itself, not just its discoverability, is unreliable here. Fix:
extend the existing bullet with this caveat, and add a one-line forward
pointer from the `cost` section's adjacent "Worktree-isolated subagent
attribution" paragraph (which documents a related but distinct case —
`isolation: "worktree"`'s harness-generated branch name — and is where a
`cost`-focused reader is likeliest to land first).

Both fixes were scoped from the same investigation and land as one PR
because they were found together and are each single-paragraph/single-line
edits; splitting them into separate PRs would not reduce review surface
meaningfully.

**Structural-sibling check.** `claude/.claude/skills/ready-for-review/SKILL.md:95`
has the same bare `python3 ~/.claude/scripts/transcript-analysis.py`
invocation (for `skill-invocation`). It is not the same bug shape: that
call site has no nearby account-aware `$config_dir` resolution to be
inconsistent with, and `skill-invocation` is documented as always
repo-scoped, not account-scoped — there's no local inconsistency to fix.
Left unchanged.

## Critical files

- `claude/.claude/skills/pr-description/SKILL.md`:
  - Line 94 — change `python3 ~/.claude/scripts/transcript-analysis.py
    cost --this-repo --branches "$branch" --summary` to `python3
    "$config_dir/scripts/transcript-analysis.py" cost --this-repo
    --branches "$branch" --summary`. Reuse: `$config_dir`, already
    resolved at lines 78-81.
  - Line 97 — reword "embed stdout **verbatim** under `## Cost`, followed
    by the exact command (branch filled in)" to state explicitly that
    `$config_dir` is left literal in the echoed command and only
    `$branch` is resolved — closing the two-variable ambiguity Fix 1
    introduces (ledger Row 6).
- `docs/transcript-analysis.md`:
  - Line 30 (end of the "A subagent dispatched from another repo's
    session" bullet) — append the branch-attribution caveat sentence.
  - Line 571 (end of the "Worktree-isolated subagent attribution"
    paragraph) — append the one-line forward cross-reference.

## Verification

- Read-through: confirm the edited SKILL.md bash block is still valid bash
  (the only change is a variable substitution in an existing, already-quoted
  argument position), and that the reworded line 97 leaves no ambiguity
  about which of the command's two variables gets resolved before it's
  embedded in a PR body.
- Local smoke test of the fixed command shape against the operator's own
  personal account only (never another account's data):
  `CLAUDE_CONFIG_DIR="$HOME/.claude" python3
  "$HOME/.claude/scripts/transcript-analysis.py" cost --this-repo --summary`
  — confirms `$config_dir/scripts/...` resolves and executes cleanly, i.e.
  the fixed line's shape is not just syntactically valid but actually runs.
- `/code-review` — auto-dispatches `/skill-review` since a `SKILL.md` file
  changed (`.claude/rules/review-pipeline-dispatch.md`, hook-enforced via
  `require-skill-review.sh`).
- Redaction self-check on the doc prose before commit: no private
  project/org/branch names from the investigation appear anywhere in either
  diff (the repo's `deny-private-project-refs` hook also fires on
  commit/PR as a backstop).

## Out of scope

- **No code fix to `transcript-analysis.py` itself** (e.g. attempting to
  re-derive a cross-repo subagent's real branch from its own recorded
  tool-call `cwd` paths). The exact harness-level mechanism producing the
  gap is unconfirmed (ledger Row 4) — a code fix without confirming it
  first risks papering over the symptom rather than fixing it, and belongs
  in a follow-up investigation, not this doc/mechanical-fix PR.
- **Not filing an issue against Claude Code itself.** If the root cause is
  harness-level `gitBranch` stamping behavior for cross-repo subagent
  dispatch, that's outside this repo's control to fix directly.
- **No runtime warning/heuristic added to `cost --summary`'s own output**
  (e.g. detecting "this branch may have unattributed cross-repo subagent
  spend" and surfacing it inline). Worth considering as a follow-up, but a
  detection heuristic is nontrivial and wasn't designed this session.
