# References — review-pr

Not loaded at skill runtime. Consult when editing the skill,
`review-pr-acquire.sh`/`review-pr-diff.sh`, or `audit-execution-surface.py`
to verify a `gh` field name, a truncation caveat, or the execution-surface
match list still holds.

## What `review-pr-acquire.sh` fetches (Step 1)

`gh pr view --json title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,files,changedFiles,commits,reviews,reviewDecision,mergeable,mergeStateStatus`, plus a second REST call, `gh pr checks`, and a paginated existing-reviews fetch — all inside the one script call, not typed out per-run.

- **`authorAssociation` is not a valid `--json` field on `gh pr view`.**
  Including it makes the whole call error rather than degrade gracefully.
  Author association comes from a second call:
  `gh api repos/{owner}/{repo}/pulls/{number}`, whose REST payload exposes
  `author_association` and, as a same-call bonus, the PR's own true
  `commits` integer (see the 100-entry note below).
- **`files` truncates silently at 100 entries, with no `--paginate`
  equivalent on `gh pr view`.** This is a security interaction, not a
  completeness nit: `files` is exactly what step 2's passive-execution
  audit reads (self-fetched independently there, never trusted from this
  step's own read), so a `.mcp.json` at position 101 would be invisible to
  the gate if `files` were trusted alone. The script always requests
  `changedFiles` too, compares it against `files`' length, and on any
  mismatch re-fetches the full list via
  `gh api repos/{owner}/{repo}/pulls/{number}/files --paginate`, which
  paginates correctly.
- **`commits` shares the same 100-entry truncation** as `files`. Reconcile
  it against the REST payload's own `.commits` integer (the true total)
  instead of a `changedCommits`-shaped field, since `gh pr view --json`
  exposes no such count directly.
- **`mergeable` and `mergeStateStatus` are frequently `UNKNOWN`.** GitHub
  computes mergeability asynchronously; a first request routinely returns
  `UNKNOWN` with no signal that a retry would resolve it. The skill never
  branches a stop decision on these two fields — this is guidance for how
  the model reasons about the script's JSON output, not fetch mechanics
  the script itself can enforce, so it stays as prose in SKILL.md.
- **`gh` exit codes are generic.** Distinguishing not-found from
  rate-limited from a network failure means parsing stderr text, which is
  version-fragile across `gh` releases. The script prefers aborting on any
  non-zero exit over branching on a parsed failure cause.

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
symlink — tree-entry mode `120000` in the tree, vs `100644`/`100755` for a
regular file. An innocuously-named symlink (e.g. `notes.txt`, pointing at
an absolute path into the operator's home directory holding local
credentials) checks out verbatim via `git worktree add` with
no target validation, and the reviewing agent's `Read` tool then
transparently returns the target's content, which could reach the posted
findings body. `review-pr-checkout.sh` runs `git ls-tree -r <FETCHED_SHA> --
<changed-file-paths>` — scoped to the PR's own changed files (the same
list already fetched for the audit above), never the whole tree — and
treats any `120000` entry among them as a stop condition, reported the same
way as an execution-surface hit (matched path + reason on stderr, non-zero
exit, no worktree left behind). Scoping to the changed-file list, not the
whole tree, is deliberate: a symlink already committed on the base branch
that this PR never touches is not this PR's own risk, and must not stop
every future review of the repo.

## The no-checkout path's reduced coverage (`review-pr-diff.sh`, Step 2)

`review-pr-diff.sh` is what a `FIRST_TIME_CONTRIBUTOR`/`NONE`/cross-repo PR
routes to once `review-pr-checkout.sh`'s trust block refuses it
unconditionally — the common case on a public repo, so this path exists
to keep that PR class reviewable rather than permanently locked out.
What it gives up against the checkout path, named explicitly rather than
left implicit:

- **No local checks (Step 6 is skipped entirely).** Nothing lands on disk,
  so there is no tree to run the project's test suite, linter, or build
  against. Any finding that would only surface by running code is missed.
- **No git-tracked-symlink scan.** A symlink appears only as a mode-`120000`
  line in the diff text, reviewable like any other line. (The scan exists
  to stop `git worktree add` from checking one out verbatim — impossible
  on this path, since nothing is checked out.)
- **A stop-shaped signal becomes a mandatory finding instead.** An
  `audit-execution-surface.py` hit halts `review-pr-checkout.sh` before any
  fetch. On this path, the same hit is reported on stderr as an
  `AUDIT_FINDING` line instead, and must be carried into the Step 5
  synthesized review as a blocking finding — there is no checkout for a
  hard stop to protect.
- **The diff itself may be truncated on an unusually large PR.** `gh pr
  diff`'s server-side truncation behavior on an unusually large PR is not
  confirmed against production. `review-pr-diff.sh` mitigates this with a
  heuristic: it compares the diff's own `diff --git` header count against
  the paginated file-list count and reports a mismatch on stderr — this is
  not a confirmed vendor limit.

## Why the findings-body declaration uses the Write tool, not Bash (Step 7)

A spawned review-only subagent carries no Write tool for this path, so requiring the Write tool makes the step un-completable from a subagent by construction, rather than by convention.

Passing the fixed path as a Bash argument would also put it in the process table and shell history, which the sibling-file design exists to avoid. The findings-body path comes from `review-pr-findings-path.sh`, not from a value the model transcribes, so nothing needs independent verification against `$CONFIG_DIR`/`$SESSION_ID`.

Accepted gap: the Write tool does not open with `O_NOFOLLOW`, so a pre-planted symlink at the fixed body path would be followed. The downstream read path's `O_NOFOLLOW`-guarded hash check (`_lib_sha256_no_follow`, `claude/.claude/hooks/_lib.sh`, called from `marker.sh write review-pr`) rejects the symlink, so the flow fails closed rather than posting unreviewed content. A second, broader accepted consequence of the same followed write: it can overwrite or create an arbitrary file the Claude Code process has write access to. That risk is distinct from, and wider than, the posting-side risk the read-path check above fully mitigates, and it rests on the same local-compromise prerequisite already accepted for that read-side gap.

**Trust classification never removes a stop condition, only widens it.**
`authorAssociation` and `isCrossRepository` describe an account's
standing, not the provenance of the commits under review — a compromised
collaborator account still produces a same-repo, non-first-time-looking
PR that this audit must still flag on a hit. Cross-repo or
first-time-contributor status escalates the skill to stopping on *any*
diff at all; it is never a reason to skip running this predicate.
`review-pr-checkout.sh` enforces this unconditionally on every invocation;
`review-pr-diff.sh` is the path that class routes to instead of becoming
unreviewable.
