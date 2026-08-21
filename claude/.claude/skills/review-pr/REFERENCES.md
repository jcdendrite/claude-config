# References — review-pr

Not loaded at skill runtime. Consult when editing the skill or
`audit-execution-surface.py` to verify a `gh` field name, a truncation
caveat, or the execution-surface match list still holds.

## `gh` field reference (Step 1)

`gh pr view --json title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,files,changedFiles,commits,reviews,reviewDecision,mergeable,mergeStateStatus`

- **`authorAssociation` is not a valid `--json` field on `gh pr view`.**
  Including it makes the whole call error rather than degrade gracefully.
  Author association comes from a second call:
  `gh api repos/{owner}/{repo}/pulls/{number}`, whose REST payload exposes
  `author_association`.
- **`files` truncates silently at 100 entries, with no `--paginate`
  equivalent on `gh pr view`.** This is a security interaction, not a
  completeness nit: `files` is exactly what Step 2's passive-execution
  audit reads, so a `.mcp.json` at position 101 is invisible to the gate
  if `files` is trusted alone. Always request `changedFiles` too, compare
  it against `files`' length, and on any mismatch re-fetch the full list
  via `gh api repos/{owner}/{repo}/pulls/{number}/files --paginate`, which
  paginates correctly.
- **`commits` shares the same 100-entry truncation** as `files` — the same
  compare-and-repaginate discipline applies before trusting a commit
  count or list.
- **`mergeable` and `mergeStateStatus` are frequently `UNKNOWN`.** GitHub
  computes mergeability asynchronously; a first request routinely returns
  `UNKNOWN` with no signal that a retry would resolve it. Never branch a
  stop decision on these two fields.
- **`gh` exit codes are generic.** Distinguishing not-found from
  rate-limited from a network failure means parsing stderr text, which is
  version-fragile across `gh` releases. Prefer aborting on any non-zero
  exit over branching on a parsed failure cause.

## Execution-surface file list (Step 2 / `audit-execution-surface.py`)

The categories `_classify()` matches, and why each is a passive-execution
vector — fires with no explicit test run, on checkout or on the harness
loading a project directory:

| Pattern | Vector |
|---|---|
| `.gitattributes` (any depth) | git executes clean/smudge filter drivers named in it at checkout |
| `.githooks/**`, `.husky/**` | conventional `core.hooksPath` target directory names; a repo commonly points `core.hooksPath` at one of these via setup instructions or a tool (Husky), and git then executes any file placed under it at checkout. `core.hooksPath` itself is local git config, not something a PR's file list carries directly — this is a heuristic over common target-directory names, not an exhaustive read of the actual configured value. |
| `CLAUDE.md` (any path segment) | loaded as standing instructions by the reviewing harness when it works inside the checked-out tree |
| `.claude/settings.json` | configures hooks and permissions the harness applies |
| `.claude/hooks/**` | runs on every matching tool call the harness makes |
| `.claude/agents/**` | defines subagent behavior the harness may dispatch |
| `.mcp.json` | registers an MCP server the harness may launch |

Content-blind by design: the predicate takes a path list, not file
bodies, so a hit means "this path could be an execution-surface file,"
not "its content is malicious." Over-flagging is the accepted direction
— see the script's module docstring.

Every match folds case (`.MCP.json` matches the same as `.mcp.json`)
because a case-insensitive filesystem (macOS default, Windows) resolves
both to the same loaded file.

**Trust classification never removes a stop condition, only widens it.**
`authorAssociation` and `isCrossRepository` describe an account's
standing, not the provenance of the commits under review — a compromised
collaborator account still produces a same-repo, non-first-time-looking
PR that this audit must still flag on a hit. Cross-repo or
first-time-contributor status escalates the skill to stopping on *any*
diff at all; it is never a reason to skip running this predicate.
