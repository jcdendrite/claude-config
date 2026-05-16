# Plan: Make the private-project redaction hook name what it blocked

## Context

`deny-private-project-refs.sh` blocks `git commit` / `gh pr create` / `gh pr edit`
/ `gh api` when staged content contains an entry from
`~/.claude/private-projects.md`. The blocklist branch deliberately emits a
**generic** deny message that does **not** name which entry matched. The stated
rationale (hook header lines 101-106, README lines 350-352): echoing a
user-flagged name would "re-expose it in terminal output, CI logs, and Claude's
conversation context."

That rationale does not hold up:

- The matched token is, by definition, the user's own private-project name,
  already present in the content the agent staged. The hook still blocks the
  *commit* — the name is kept out of public git history — but the name is not a
  secret *to this user*: it sits in the staged file and in
  `~/.claude/private-projects.md`, which the agent can read directly. Naming it in
  the deny message discloses it to no principal who did not already hold it.
- Every surface the deny message reaches — the agent's conversation context, the
  local terminal, and the session transcript file under `~/.claude/projects/.../`
  — is owned by the same single user who owns the blocklist and authored the
  staged content. On a deny-then-retry-from-memory flow the agent may never run
  `git diff --cached`, so the deny message can be the *only* persisted copy of the
  name in that transcript — but that transcript is the same user's file. The
  change relocates the user's own private name into the user's own transcript; it
  creates no incremental exposure to a *new* party. (The original "re-expose it in
  ... Claude's conversation context" framing treated the user's own transcript as
  a surface to protect *from the user* — which is not a coherent threat model on
  this single-user-local-machine surface.)
- "CI logs" is not a meaningful surface for this hook — it is a local
  `PreToolUse` hook on `git`/`gh`; CI does not run Claude Code hooks. A developer
  manually pasting terminal output elsewhere is a human-mediated path that applies
  equally to ordinary `git diff` output and is outside the hook's control.
- The cost is high and real: sessions burn many tool calls bisecting the diff to
  rediscover a token the hook already identified. The example that prompted this
  showed an agent flailing through ~10 tool calls before a human had to point at
  the offending line (a private-project name, whole-word-matched inside a filesystem path).

The sibling tracker-ID branch (lines 490-495) already names matched tokens
(`${HIT_LIST}`) — so the "never name" behavior is an inconsistency, not a
coherent policy. This change brings the blocklist branch into line with it and
goes one step further (per the user's choice): it also quotes the offending
line(s), so the agent sees exactly where the token sits.

**Outcome:** the deny message names every matching blocklist entry and quotes the
offending line(s), so the agent can fix the content in one pass.

## Scope decision — tracker-ID branch left unchanged

Per the "audit structural siblings" heuristic, the tracker-ID branch was checked.
It already names matched tokens (`PROJ-123`-shaped, trivially greppable), so it
does **not** exhibit the flailing bug this change fixes. Adding line-context there
is scope creep and is deliberately excluded.

## Change

Files (all in repo root):

### 1. `claude/.claude/hooks/deny-private-project-refs.sh`

**Blocklist scan loop — lines 501-522.** Replace the early-`exit 0`-on-first-match
loop with accumulate-then-report:

- Iterate the whole blocklist (keep the existing CR-strip / whitespace-trim /
  blank-and-`#`-comment skipping at lines 502-509 unchanged).
- For each entry, capture matching lines with the same predicate already used,
  minus `-q`: `printf '%s' "$SCAN_TARGET" | grep -iw -F -- "$line" | head -3`
  (cap at 3 offending lines per entry). Offending lines are sourced **only** from
  `SCAN_TARGET` — the already-staged / already-authored content the hook scans —
  never from `private-projects.md` itself, so the quote can never widen to the
  blocklist file's own surrounding lines.
- A quoted line may carry adjacent content beyond the matched token (e.g. a
  hostname on the same line). This is accepted: the line is content the user
  themselves staged and can re-read; the cap below bounds volume, and quoting the
  line is what lets the agent locate the match in one pass.
- Truncate each captured line to ~200 chars (`${hit:0:200}…`) so a minified blob
  can't flood the message.
- Shell note for the implementer: `grep ... | head -3` under the hook's
  `set -o pipefail` can leave a non-zero pipe status when `head` closes the pipe
  early. The hook does **not** set `-e`, so a bare command-substitution assignment
  (`matched_lines=$(...)`) absorbs this harmlessly — do not wrap it in an `if`
  condition that would misread the SIGPIPE status as a real failure.
- Accumulate into a `blocklist_report` string: one block per matched entry —
  the entry name, then its (truncated, capped) offending line(s).
- After the loop, if `blocklist_report` is non-empty, call `emit_deny` once with a
  message that lists all matched entries + lines, retains the CLAUDE.md pointer,
  and keeps the `chain_split_hint_if_chained "$COMMAND"` suffix. Then `exit 0`.

`emit_deny` (lines 131-137) already JSON-escapes the entire reason via
`jq -Rs .`, so embedded newlines, quotes, and backslashes from diff content are
handled — no extra escaping needed.

**Header comment — lines 101-106.** Rewrite the "Invariant" block. New text states
the deny message names matched entries and quotes offending lines, and gives the
reason: the matched token is already present in the gated content the agent
staged and will read via `git diff --cached`, so naming it adds no exposure
beyond the diff itself while eliminating costly bisection.

**Header comment — lines 119-123.** Drop the clause asserting the
"matched entry is intentionally NOT named" invariant is "preserved." The chain
hint still uses `<name>` placeholders for its own `cd`-path examples — that is
fine and unrelated; only the invariant-justifying sentence is removed.

**Comment — lines 337-340** (`chain_split_hint_if_chained`). Drop the
"would risk re-exposing the matched blocklist entry (see header 'Invariant')"
rationale. `$COMMAND` is still not echoed — simply because it is not needed for
the hint, not because of a privacy invariant.

**Comment — lines 516-517.** Remove the "Generic message ... intentionally NOT
named" comment; replace with a one-line note describing the report shape.

### 2. `claude/.claude/hooks/tests/test_deny_private_project_refs.py`

Rewrite the five tests + section comment that assert non-naming
(Explore-confirmed locations):

- Section comment line 734 — `# Critical invariant: the deny message NEVER
  names the matched entry.` → describe the new naming behavior.
- `test_blocklist_deny_message_does_not_name_entry` (959-990) → rename to
  `test_blocklist_deny_message_names_matched_entry`; assert the entry
  (`Acme Corp`) **is** in `reason`, assert the offending line is quoted, drop the
  `"deliberately does not name which entry matched"` assertion, keep
  `"private-projects.md" in reason`.
- `test_git_commit_F_blocklist_match_denied_with_generic_message` (1114-1137) →
  assert the entry is named on the `-F` commit-message-file path.
- `test_gh_api_blocklist_match_in_input_file_denied_with_generic_message`
  (1354-1378) → assert the entry is named on the `gh api --input` path.
- `test_tracker_id_takes_priority_over_blocklist_match` (1541-1570) → tracker
  message still fires; blocklist branch is not reached, so its assertions stay
  valid as-is.
- `test_blocklist_chained_command_deny_includes_chain_hint` (1833-1852) → assert
  the entry is named **and** the chain hint is appended.

Add new tests:

- Multiple matching blocklist entries → all named in one deny message.
- Offending line is quoted verbatim in the deny message.
- A >200-char offending line is truncated.
- An entry appearing on >3 lines → at most 3 lines quoted.

### 3. `README.md` — lines 350-352

Rewrite the "Privacy of the deny message" subsection: the deny message names
matched entries and quotes offending lines. Rationale (use the accurate framing,
not "no exposure beyond the diff"): the matched token is the user's own private
name, already in the staged content and in the user-local blocklist; every
surface the message reaches — terminal, agent context, session transcript — is
owned by that same user, so naming it discloses nothing to a new party while
eliminating costly diff-bisection. Drop the "re-expose it in ... CI logs"
claim — CI does not run this hook. Consider renaming the subsection
(e.g. "What the deny message reports").

### 4. `docs/hooks.md` — line 9 (optional, light touch)

If the existing hook description would now be misleading, adjust one sentence.
No change if it does not mention message contents.

## Verification

1. `pytest claude/.claude/` — full suite green (dispatch via `check-runner`).
2. `ruff check claude/.claude/` — clean.
3. Manual smoke test in a scratch repo wired to this hook:
   - Add a synthetic entry to a test `private-projects.md`, stage a file
     containing it, attempt `git commit` → confirm the deny message names the
     entry and quotes the offending line.
   - Stage content matching two entries → confirm both are reported in one
     message.
   - Stage content where the entry sits on a >200-char line → confirm
     truncation.
4. Per repo CLAUDE.md "run the skill on its own diff": this is a hook change,
   so `/claude-hook-review` on the diff before PR handoff.
