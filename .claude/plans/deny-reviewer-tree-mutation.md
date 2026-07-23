# Stop review-only subagents from mutating the tree under review

## Context

**Goal:** make the eight `staff-*`/`ciso-reviewer` review agents (plus the
built-in `Explore`/`Plan`) structurally unable to modify a repository they are
reviewing, so a review pass can never leave — or commit — a change to the code
under review.

**Why now:** a `staff-platform-engineer` review ran `terraform fmt -diff
cloud_front.tf`. `-diff` prints a diff but does **not** imply read-only (that
needs `-check` or `-write=false`), so `fmt` rewrote the file in place. The agent
then read `git status`, saw the file as modified, concluded its own `fmt` run
was the change, and reverted with `git checkout -- cloud_front.tf`. That is a
review agent mutating and then un-mutating the tree under review — exactly the
class of action a reviewer must never take.

**Is it a one-off?** No. A scan of all 2,003 subagent transcripts on this
machine (join: parent `Agent` tool-use → `subagent_type`, matched to the
subagent log via `toolUseResult.agentId`) found repo-mutating actions across
**every** review-only persona. Confirmed distinct incidents:

| Incident | Agent | Action | Where | Outcome |
|---|---|---|---|---|
| terraform fmt | staff-platform-engineer | `terraform fmt` (no `-write=false`) rewrote `cloud_front.tf`, then `git checkout --` to revert | worktree | executed; self-reverted |
| mutation testing | staff-sdet | 5× `sed -i` / in-place python inserting `// MUTATED:` markers into `AuthContext.tsx` to neuter guards, ran vitest, restored from a `/tmp` backup | worktree | executed; mutate & restore were **separate tool calls** |
| probe test | staff-backend-engineer | Write-tool created `src/lib/__pii_probe__/pii_probe.test.ts`, ran jest, `rm -rf` | worktree | executed; untracked, cleaned |
| whole-tree checkout | staff-backend-engineer | `git checkout 567ff98 -- .` on another repo's **main** tree | cross-repo | **blocked** by existing worktree hook |
| whole-tree checkout | staff-sdet | `git checkout <branch> -- .` on another repo's **main** tree | cross-repo | **blocked** by existing worktree hook |

Two recurring beliefs licensed the mutations, per the agents' own transcript
text: *"empirical verification against the real thing outweighs review-only
scope"* and *"a self-caused mutation is fine as long as I clean up after
myself."* One agent copied the "write a throwaway test, delete it after"
technique verbatim out of a **sibling reviewer's findings file** — so the
pattern self-propagates between reviewers.

**Intended outcome:** the only mechanism that actually stopped a reviewer in the
data was the existing worktree hook, and it only fires on *cross-repo* targets —
every in-worktree mutation ran unimpeded. Close that gap: a review-only agent's
sole sanctioned write targets are its findings file (`agent-reviews/…`) and a
`/tmp` sandbox; any write into a git working tree (worktree or main) is denied.

## Approach

Three coordinated changes, in decreasing order of load-bearing weight:

### 1. New PreToolUse gate hook `deny-reviewer-tree-mutation.sh` (mechanical spine)

A single global hook, wired under both the `Bash` and `Edit|Write|MultiEdit`
matchers in `claude/.claude/settings.json`. Logic:

1. Parse stdin via `_lib_parse_tool_input_or_deny` (fail-closed on malformed
   input, same as every gate hook here). Read `agent_type` from the payload —
   `.agent_type` is a documented common PreToolUse field
   (code.claude.com/docs/en/hooks) and is **already consumed** by
   `nudge-error-mode-analysis.sh`/`nudge-handoff-near-context-cap.sh`, which
   confirms it carries the literal agent name in this Claude Code version.
2. **Fast common-path exit:** if `agent_type` is not in the closed review-only
   set, `exit 0` immediately. The hook does no work for the main session,
   `code-writer`, `general-purpose`, or any non-reviewer. This keeps it off the
   latency budget for the overwhelmingly common call.
3. Review-only set (closed enumeration, mirroring `_LIB_READONLY_GIT_SUBCMDS`'s
   deliberate-not-accreted philosophy): `ciso-reviewer`,
   `staff-analytics-engineer`, `staff-backend-engineer`, `staff-data-engineer`,
   `staff-frontend-engineer`, `staff-platform-engineer`,
   `staff-product-engineer`, `staff-sdet`, `Explore`, `Plan`. Defined once as
   the single source of truth (a `_lib.sh` array parallel to
   `_LIB_READONLY_GIT_SUBCMDS`, so the hook and its test read the same list).
4. **File-write tools** (`Write`/`Edit`/`MultiEdit`): deny unless the resolved
   `file_path` is under `/tmp` **or** has an `agent-reviews` **path segment**
   (the sanctioned findings-file contract — `findings_path:
   agent-reviews/<agent>-<epoch>-<slug>.md`, written with the Write tool). Match
   `agent-reviews` as a `/`-delimited path component, not a substring, so a file
   literally named `agent-reviews-notes.md` is not exempted. This is a clean,
   complete gate because the tool carries an explicit `file_path`. `file_path` is
   extracted with a dedicated `jq` read (`_lib_parse_tool_input_or_deny` yields
   only `TOOL_NAME`/`COMMAND`), mirroring `require-worktree-for-file-writes.sh`.
   `NotebookEdit` is deliberately **excluded**: the `Edit|Write|MultiEdit`
   settings matcher does not wire it, and no review-only agent is granted it
   (`Explore`/`Plan` exclude it; the eight `staff-*`/`ciso` agents have only
   `Read, Grep, Glob, Bash, Write`) — handling it would be coverage that can
   never fire.
5. **Bash**: deny when the command
   - invokes a **git write** subcommand — reuse `_lib_split_fragments` +
     `_lib_fragment_invokes_git` + `_lib_extract_git_subcmd` +
     `_lib_readonly_git_subcmds` (all already in `_lib.sh`) to allow read-only
     git (`diff`, `log`, `status`, `show`, `grep`…) and deny writes
     (`checkout`, `reset`, `restore`, `stash`, `add`, `commit`, `apply`,
     `clean`, `rm`, `mv`, `revert`, `cherry-pick`, `rebase`, `merge`, `push`).
     Note: unlike `require-worktree-for-git-writes.sh`, a worktree cwd does
     **not** exempt the write — a reviewer never writes git state anywhere.
     This is the deliberate lighter primitive: because the verdict is
     cwd-independent, the hook needs only "does a fragment invoke a git-write
     subcommand," so it reuses the string-level `_lib` helpers and does **not**
     need `parse-git-command.py`'s cwd-threading or any `git rev-parse`.
   - matches a **closed in-place-edit family**: `terraform fmt`/`tofu fmt`
     *unless* `-check` or `-write=false` is present; `sed -i`/`perl -i`;
     `gofmt -w`; `prettier … --write`; `eslint … --fix`; `ruff format` /
     `ruff check … --fix`; `black`; `isort`; `rustfmt` *unless* `--check`.
     Each entry is grounded on the tool's own read-only flag, cited in the
     hook's header comment block (see **Grounding notes** under Critical files).

Deny emits the standard `hookSpecificOutput.permissionDecision: deny` payload
and, critically, **names the sanctioned alternative** in the reason string
("reviewers are read-only on the tree under review; copy the file to `/tmp` and
mutate the copy there") so the agent redirects instead of improvising a
workaround — one blocked reviewer in the data invented a second workaround after
the first was denied.

**Rationale / alternatives weighed inline:** a global hook keyed on `agent_type`
was chosen over per-agent frontmatter `hooks:` blocks because `Explore` and
`Plan` are harness built-ins with no local file to carry a block, and their tool
grants already exclude `Write`/`Edit` — so only the Bash surface needs guarding
for them, which only a global hook reaches. `disallowedTools: Write` in
frontmatter was rejected: reviewers legitimately need `Write` for the findings
file, and it does nothing for the Bash vector (where every high-severity
incident actually occurred). Doing **both** a global hook and per-agent blocks
was rejected as compounding two layers on one mechanism (the
"compounding-defensive-layers" tell in CLAUDE.md §Working Style) with no gap
between them to justify the second.

#### Lighter alternatives considered

The hook is a broader primitive than "deny the Write tool," so per CLAUDE.md
§Engineering Judgment, lighter primitives from the Claude Code hook/agent system
were checked first:

- **Per-agent frontmatter `hooks.PreToolUse`** — lighter (fires only for that
  agent, no global surface). Rejected: cannot cover the built-in `Explore`/`Plan`
  (no file), and would duplicate one block across eight files that must stay in
  sync — the exact drift DRY warns against.
- **Frontmatter `disallowedTools`** — lightest (a static field, no script).
  Rejected: reviewers need `Write` for findings, so it can't be denied wholesale,
  and it is silent on Bash, which is where `terraform fmt`/`sed -i` live.
- **Prose-only ("You are read-only on the tree under review")** — no mechanism
  at all. Rejected as the spine: the agents already carry "You do not write
  code" and mutated anyway. Prose is retained as change #3, but as the
  explanation layer, not the enforcement layer.

### 2. Reviewer-agent prose clause (change #2 — tells the agent what to do instead)

Each of the eight reviewer agent files says only "You do not write code," which
is what got read as "don't author the fix" rather than "don't touch the tree."
`code-writer.md` already carries an explicit paragraph forbidding
state-mutating commands; mirror a reviewer-appropriate version into all eight:
the tree under review is read-only; to verify a claim empirically, copy the file
into `/tmp` and mutate the copy there; the only in-tree write is the
`findings_path` file. This is intentional duplication across the eight
independently-readable agent files (the repo's no-shared-partials rule for
skills/agents), not an extractable partial.

### 3. Test coverage (change #3 — locks the behavior)

New `test_deny_reviewer_tree_mutation.py` mirroring the existing per-hook test
style, exercising:
- reviewer + Write to a tracked path → deny; reviewer + Write to
  `agent-reviews/…` → allow; reviewer + Write to a decoy `agent-reviews-x.md`
  (substring, not a segment) → deny; reviewer + Write to `/tmp` → allow.
- `agent_type` absent / `main` / `code-writer` / `general-purpose` → allow
  (pass-through) — including a non-reviewer `code-writer` + `sed -i src/x.ts` →
  allow, proving the gate keys on agent identity, not the command.
- reviewer + `sed -i src/x.ts` → deny; reviewer + `terraform fmt x.tf` → deny;
  reviewer + `terraform fmt -check x.tf` → allow; reviewer +
  `terraform fmt -write=false x.tf` → allow.
- reviewer + `git checkout -- x` → deny; reviewer + `git diff` / `git status` /
  `git log` → allow; reviewer + `npx vitest run` → allow (running tests is
  read-only review work).
- One coverage case per built-in: `agent_type: Explore` and `agent_type: Plan`
  + `sed -i` → deny, locking in that the built-ins are covered by the same list.

The `bash_input` helper already accepts `agent_type`; `write_input`/`edit_input`
need an `agent_type` parameter added (small helper change in
`claude/.claude/tests/helpers.py`).

## Critical files

**Create:**
- `claude/.claude/hooks/deny-reviewer-tree-mutation.sh` — the gate hook.
- `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py` — tests.

**Modify:**
- `claude/.claude/hooks/_lib.sh` — add the review-only-agent name array as the
  single source of truth (parallel to `_LIB_READONLY_GIT_SUBCMDS`).
  **Reuse:** `_lib_parse_tool_input_or_deny`, `_lib_split_fragments`,
  `_lib_fragment_invokes_git`, `_lib_extract_git_subcmd`,
  `_lib_readonly_git_subcmds` — do **not** re-implement git parsing.
- `claude/.claude/settings.json` — wire the hook under the `Bash` and
  `Edit|Write|MultiEdit` PreToolUse matchers. (No `if` condition — the hook's
  own `agent_type` gate is the filter, and per repo rule hooks must filter their
  own input by tool name/matcher rather than lean solely on settings `if`.)
- All eight `claude/.claude/agents/{ciso-reviewer,staff-*}.md` — add the
  read-only-on-the-tree prose clause (change #2).
- `claude/.claude/tests/helpers.py` — add `agent_type` param to
  `write_input`/`edit_input`.
- `README` hooks table + any per-hook doc — add a one-line role-summary row for
  the new gate hook, matching the existing one-line-summary granularity. Before
  editing, check `test_doc_counts.py` for an asserted hook count that a new hook
  would break, and update the expected count in the same PR.

**Grounding notes:** put the in-place-edit family's per-tool read-only-flag
citations in the hook's own header comment block (a `# Known gaps` /
`# Grounding` section, matching the sibling worktree hooks) — **not** a
co-located `REFERENCES.md`. Hooks are flat `.sh` files; introducing a
`hooks/deny-reviewer-tree-mutation/` subdirectory would break the flat layout
and the `hooks/*.sh` globbing that `test_shellcheck.py` and the settings paths
assume.

**Consulted, not modified:** `code-writer.md` (prose template),
`require-worktree-for-git-writes.sh` (parser-reuse reference),
`code-review/SKILL.md` (findings-path contract that defines the exemption).

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py -q`
  — new suite green.
- `../../../.venv/bin/pytest claude/.claude/ -q` — full suite green (the
  `test_hook_alignment.py` and `test_agent_roster.py` meta-tests will assert the
  new hook follows the emit_deny/source contract and that agent-file edits stay
  well-formed; expect to satisfy, not skip, them).
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` — hook
  passes shell lint (`test_shellcheck.py` also enforces this).
- **Manual end-to-end smoke** (no `claude -p` CI harness exists here — per repo
  convention, manual): from a scratch git repo, pipe a crafted PreToolUse
  payload with `agent_type: staff-sdet` and a `sed -i` command into the hook;
  confirm `deny`. Repeat with `agent_type: Explore` (built-in coverage) and with
  `agent_type` absent (silent allow). This smoke test is also the empirical
  confirmation that a real Claude Code build populates `agent_type` with the
  literal agent name in the PreToolUse payload — the one external-behavior
  assumption the mechanical tests stub rather than prove.
- **Regression guard on the exemption:** a test proving a real
  `findings_path: agent-reviews/staff-sdet-….md` Write is allowed — if this hook
  ever blocked the findings file, it would break the entire review pipeline.

## Out of scope / accepted residual

- **Arbitrary Bash write-target resolution** (`cp scratch src/x`, `sed … >
  src/x`, `tee src/x`) is **not** mechanically gated. Proving where an arbitrary
  redirect or `cp` lands requires full shell-write-target analysis — the
  per-vector machinery CLAUDE.md §Working Style warns against building. The
  file-write **tools**, git writes, and the closed in-place-edit family cover
  every mutation vector seen in the transcript scan; the residual raw-Bash
  copy/redirect path is covered by the prose clause and the fail-closed
  principle that reviewers work in `/tmp`. This boundary is stated in the hook's
  "Known gaps" comment block, matching how the sibling worktree hooks document
  their own undecidable cases — not left implicit.
- **Retrofitting or rewriting the historical transcripts** — the incidents are a
  read-only diagnostic; nothing is edited in `~/.claude/projects`.
- **Changing the review pipeline's findings-file mechanism** — the
  `agent-reviews/` Write path stays exactly as-is; the hook exempts it.
