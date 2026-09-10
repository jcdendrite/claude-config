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
| `CLAUDE.local.md` (any path segment) | concatenated into the same standing-instructions load as CLAUDE.md (`claude/.claude/rules/claude-md-conventions.md`'s precedence list) |
| `.claude/settings.json`, `.claude/settings.local.json` | configures hooks and permissions the harness applies |
| `.claude/hooks/**` | runs on every matching tool call the harness makes |
| `.claude/agents/**` | defines subagent behavior the harness may dispatch |
| `.claude/skills/**/SKILL.md` | loaded as skill instructions by the harness's project-level skill discovery |
| `.mcp.json` | registers an MCP server the harness may launch |
| `.gitmodules` | can point a submodule fetch/checkout at attacker-controlled content |

Out of scope, named explicitly rather than left implicit: the operator's
own editor/IDE auto-run configs (`.vscode/tasks.json` with
`runOn: folderOpen`, `.idea/` run configs) are outside the reviewing
harness's control surface, so this audit does not cover them.

Content-blind by design: the predicate takes a path list, not file
bodies, so a hit means "this path could be an execution-surface file,"
not "its content is malicious." Over-flagging is the accepted direction
— see the script's module docstring.

Every match folds case (`.MCP.json` matches the same as `.mcp.json`)
because a case-insensitive filesystem (macOS default, Windows) resolves
both to the same loaded file.

### Git-tracked symlinks (checked by `review-pr-checkout.sh`, not `_classify()`)

`_classify()` matches on path text alone, so it is blind to a git-tracked
symlink -- tree-entry mode `120000` in the tree, vs `100644`/`100755` for a
regular file. An innocuously-named symlink (e.g. `notes.txt`, pointing at
an absolute path into the operator's home directory holding local
credentials) checks out verbatim via `git worktree add` with
no target validation, and the reviewing agent's `Read` tool then
transparently returns the target's content, which could reach the posted
findings body. `review-pr-checkout.sh` runs `git ls-tree -r <FETCHED_SHA> --
<changed-file-paths>` -- scoped to the PR's own changed files (the same
list already fetched for the audit above), never the whole tree -- and
treats any `120000` entry among them as a stop condition, reported the same
way as an execution-surface hit (matched path + reason on stderr, non-zero
exit, no worktree left behind). Scoping to the changed-file list, not the
whole tree, is deliberate: a symlink already committed on the base branch
that this PR never touches is not this PR's own risk, and must not stop
every future review of the repo.

## Why the findings-body declaration uses the Write tool, not Bash (Step 7)

A spawned review-only subagent carries no Write tool for this path, so requiring the Write tool makes the step un-completable from a subagent by construction, rather than by convention.

Passing the fixed path as a Bash argument would also put it in the process table and shell history, which the sibling-file design exists to avoid.

**Trust classification never removes a stop condition, only widens it.**
`authorAssociation` and `isCrossRepository` describe an account's
standing, not the provenance of the commits under review — a compromised
collaborator account still produces a same-repo, non-first-time-looking
PR that this audit must still flag on a hit. Cross-repo or
first-time-contributor status escalates the skill to stopping on *any*
diff at all; it is never a reason to skip running this predicate.
