# Reviewer personas hold no Bash

*2026-10-22.*

**Partly superseded (2026-10-23):** the hook now scans the `>`/`>>` targets of a git-led fragment, so the redirect half of the row-121 "Git-fragment scan" deferral no longer describes the hook. The hook header's Known gaps list what stays open.

GH-1208 removes `Bash` from the eight reviewer personas that held it, so none of the ten holds it, and confines each persona's `Write` to its own findings file. It folds #1103 (the fix), #1104 (containment for `code-writer`, the main session, `Explore` and `Plan`) and #1099 (the motivating incident, in which a reviewer's scratch script wrote through a link under `/tmp` onto a shared interpreter). The sandbox spike behind the decision is `docs/case-studies/claude-code-sandbox-write-boundary.md`: the sandbox blocks the incident's symlink shape but not a pre-created hard link, cannot be scoped per subagent, and has an escape hatch that a bare `Bash` grant auto-approves. This file records how each agent is contained and what the design leaves open. The full reasoning and the engineer's answers are in `.claude/plans/reviewer-containment.md`; the row numbers below cite its assumption ledger.

## How each agent is contained

- **The ten reviewers** (`ciso-reviewer`, the seven `staff-*` specialists, `comment-discipline-reviewer`, `skill-fidelity-reviewer`) hold exactly `Read`, `Grep`, `Glob` and `Write`, pinned by `test_agent_roster.py`. `deny-reviewer-tree-mutation.sh` confines their `Write`, `Edit` and `MultiEdit` to findings files named `<own agent name>-*.md`. The hook header defines which directories those files may sit in and lists the rule's Known gaps. The Write tool's refusal to write through a symlink is a vendor assumption from one Linux run of the case study (W1-RERUN), not something this repo pins.
- **`Explore`** is contained by its `tools:` line, `Read, Grep, Glob`, pinned in `test_agent_roster.py`.
- **`plan-architect`**'s `tools:` line is `Read, Grep, Glob, Edit`, pinned in `test_agent_roster.py` (`test_plan_architect_tools_are_exactly_read_grep_glob_edit`). `deny-plan-writer-off-plan-writes.sh` confines its `Edit` to existing plan files directly under the repository's `.claude/plans/`, within that hook header's Known gaps. The agent is listed in `_LIB_PLAN_WRITER_AGENTS` in `_lib.sh`.
- **`review-orchestrator`** holds `Bash` and sits outside the hook's roster. It runs the `pr-diff-against-base.sh --context-files` calls with listing numbers and no changed-path text in either command.
- **`general-purpose`** is a built-in with the full tool set, outside the roster and outside the follow-up issue.
- **`Plan`** is the only roster member that reaches the hook's Bash arm. That claim rests on the harness's own agent listing, which this repo cannot pin, and a consumer project's agent file with a roster persona's name overrides the shipped `tools:` line and can give that persona `Bash` again. The Bash arm and the three link tests protect only `Plan`. Its remaining gaps are owned by #1295.
- **`code-writer`** keeps `Bash` under its charter. The `Check:` route adds no new actor and no new gate bypass, and one new posture: a reviewer-described test runs against unfixed code, bounded by the effect-bound sentence in `code-writer.md` and by no hook, because the gates that fire on `code-writer`'s Bash calls read only top-level command strings (row 132).
- **The main session** keeps `Bash` under its charter and sits outside the hook.

## Decisions and the engineer's answers

Each answer quotes the label the engineer selected or the words they typed. The plan row it cites holds the selected label and, where the row has one, the plan's reading of the question, not the question text. The option text behind a label is the session's proposal, not the engineer's words.

- **Reduced inputs** (row 5): "Reduced inputs for those spawns are fine (Recommended)". `/review-pr` spawns, `comment-discipline-reviewer` and `skill-fidelity-reviewer` get no base and history files.
- **Accept `Plan`-reachable Bash-arm gaps** (row 26): "Accept Plan-reachable Bash-arm gaps as debt; re-point to follow-up (Recommended)". The hook's tracker pointer moves from #1103 to #1295, so under `docs/hooks.md` § "Threat-model tiers" every finding against the gate is regression-class. This label is the stop-and-ask record for findings against that gate.
- **Nested `agent-reviews/` depth** (row 28): "Accept nested depth as permanent debt (Recommended)", re-confirmed as "Keep, accept as debt (Recommended)" after the reach into `.claude/rules/`, `.claude/agents/` and `.claude/plans/` was named. When the reach into `.claude/commands/` was named, the answer was "Name it in the debt record (Recommended)". The Write arm keeps allowing an own-name write into an ignored `agent-reviews/` directory at any depth. The hook header's Known gaps state how far that reaches.
- **`/review-pr` findings directory under `/tmp`**: "Implement session-bound arm (Recommended)". The Write arm accepts a `Write` that creates the caller's own new `<agent>-*.md` file in this session's `/review-pr` findings directory, under the conditions the hook header's Known gaps list. Two gaps are accepted as debt and listed there:
  - The directory's owner and mode are not checked.
  - The arm assumes a subagent's PreToolUse payload carries the dispatching session's id, which no live probe has confirmed.
- **Requested checks** (rows 119, 128, 141): "Lighter: fix writer tests first (Recommended)", then, after the engineer asked what the lighter design loses, "Keep the lighter design (Recommended)". No agent runs a check before disposition. A reviewer files a counted finding with a line that begins `Check:`, and the fix `code-writer` writes the failing test first. This reverses the earlier bounded-dispatch answers (rows 20, 22, 24), and `claude-skills/skills/code-review/SKILL.md` § "Requested checks" holds the outcome per cause.
- **Git-fragment scan** (row 121): "Defer to a follow-up issue (Recommended)". The redirect and `--output` scan for git commands is not in this change; #1295 scope item 1 owns it for `Plan`. #1103 said to fix it here, so this is a departure from #1103.
- **Retention sweep** (rows 122, 168): "Cut it (Recommended)", re-confirmed as "Keep it cut (Recommended)" after the session stated that only `SIGKILL` (or an untrapped signal) leaves the private `pr-diff-context.*` directory, outside the repository, mode 0700, unbounded and unswept. The script never deletes old lane files.
- **Follow-up issues** (row 126, 163): "Add it to the follow-up list (Recommended)" and "Ok file two issues, 1 and 3. 2 folds into 1." Filed as #1295 and #1296.
- **Probe on the installed git** (rows 120, 127): "Yes, probe first (Recommended)" and "Run on the installed git (Recommended)". The 2.31 minimum is not run.
- **Diff mechanism** (rows 125, 139): "Hybrid at 60 s (Recommended)" is superseded for the mechanism by "Sliced shared diff + Fable's refinements (Recommended)": one unrestricted `git diff` per round, header-checked against the index rows and cut into per-lane files by byte offset.
- **Lane transport** (rows 142, 146): "Omit gate-matching paths from argv (Recommended)" is superseded for the transport by "Yes, ordinals into a listing (Recommended)". No changed-path text appears in any command, and a hook denial of either call is a halt.
- **Where temps live** (row 154): "Outside the tree, beside the core temps (Recommended)".
- **Guard-glob withholding** (rows 155, 159): "Lift the composite predicate into _lib.sh (Recommended)" is superseded for the mechanism by "Yes, batch matcher, gate untouched (Recommended)".
- **No-test findings** (rows 156, 160): "Keep one outcome, state the cost (Recommended)" is superseded by "Yes, don't block on not-run (Recommended)". A finding with no test run is fixed by reading and does not stop. Only an unsupported report stops.
- **The mode's `unset`** (rows 157, 162): "Yes, create it in the script (Recommended)" and "Add to the unset (Recommended)".
- **Withholding scope** (row 167): "Drop the size rule (Recommended)".

## Departures from #1103

- No check is reproduced before disposition, and the fix writer's failing test is kept only when the claim is supported.
- A `Check:` line on a counted finding replaces an optional `## Requested checks` section (ratified by "sure, no further changes.", row 77).
- The Write arm gains an own-name rule, and credential-path and data-file withholding are behavior the ticket did not name. The size rule is dropped.
- "The `Bash` arm is unchanged" holds, while #1103's "Fix it here" for the git-fragment mis-parse is deferred to #1295 and scoped to `Plan`.

## Disposition of #1103's known hook gaps

- **Fixed here:** the Write-arm's general `/tmp` exemption (a create-only, session-bound `/review-pr` findings case remains, recorded under Decisions), and the overclaim in the hook's "flip to deny" comment.
- **Retained, owned by #1295, reaching only `Plan` (with the qualifiers in the `Plan` bullet under "How each agent is contained"):**
  - the closed enumeration of write forms (`rm`, `ln`, the source of `mv`, interpreter writes, tools' output flags such as `git diff --output`, `sed -ni`, `--in-place`, a bare `&`, a redirect glued to the word before it, fd-numbered redirects);
  - the `/tmp` link gap and the writability of all of `/tmp`, including `/tmp/$VAR` and `$(cmd)` targets;
  - the leaf hard-link half of that gap (the symlink half rests on the Write tool's refusal);
  - the `Plan` denial-text premise;
  - the mis-parse false positives (a `grep` pattern holding `>>`, a `grep` alternation split into an `mv` fragment, `git --version`, quoted `git <word>` text in a `grep` pattern);
  - formatter check modes and a bare `git branch`, retained by design under the irreversible tier.
- The three link tests pin the accepted gap's current allow verdicts for `Plan` alone.

## Residuals

- Withholding fails open when a predicate's tool fails, and it does not cover a credential value inside a file that is not credential-named.
- Withholding copies the data-file name rules only. A non-data-named file over the size threshold with a small diff reaches the lane `.diff` unblocked, though a direct `Read` of it is denied. The hook's lane-size block covers only a lane file over the threshold.
- The three Read gates (`deny-env-reads.sh`, `deny-data-file-reads.sh`, `deny-credential-file-reads.sh`) match `Read` only, and no PreToolUse hook gates `Grep`. A persona's `Grep` of a withheld or credential path therefore meets no gate. The Input contract bars a targeted `Read` or `Grep` of a withheld path, but a tree-wide `Grep` can still match one. `redact-credential-values.sh` is wired on `Grep` results (the PostToolUse matcher in `claude/.claude/settings.json` lists `Bash|Read|WebFetch|Grep|Task`), and its limits are in `docs/security-hardening.md` § "Limitations". Whether the `settings.json` `Read` deny rules reach `Grep` is unverified. No `Grep` gate is added.
- A call ended by `SIGKILL` (or an untrapped signal) leaves a temp directory outside the repository that may hold withheld bytes, to be deleted by hand, and `SIGKILL` also leaves `agent-reviews/.<stem>.<kind>.*` lane temps. Nothing detects either.
- The ignore check proves the folder rule ignores each lane-file kind, not every stem (row 109).
- A script defect on an untested repository shape halts `/code-review` until fixed. The escape is a commit from the engineer's own terminal.
- CI is Linux-only, so BSD tools and byte-exact `head`/`tail` on macOS are unverified.
- The caps are the script's only time bound. A missing `timeout -k` voids them, and the uncapped local tools (`sha256sum`, `grep`, `head`, `tail`) void it on a network-mounted repository.
- `withheld` wins over `untracked` in the preamble, and the index has no per-path diff range (row 123).
- A reviewer cannot run a check itself. The `Check:` route is verified by text pins, two pre-merge rehearsals and one post-merge round, and no CI test runs a fix writer.
- Lane files are bounded per lane except the `.diff`, which is unbounded, and nothing sweeps them.
- The listing is uncapped, so above the harness's output limit the orchestrator reads the full output file.
- A wrong in-range listing number is detected only by the orchestrator's `LANE_ROW:` comparison.
- `/ready-for-review` still prints the whole cumulative diff into the session (#1296). The leak predates this change and the change leaves that skill unedited. That skill also leaves `pr-diff-against-base.sh`'s `--record` and `--diff-file` copies of the whole cumulative diff, withheld blocks included, under the config directory. Nothing in this change keeps a persona's `Glob` or `Read` from those copies. `docs/scripts.md` § "Script reference" (that script's entry) says when each is written and removed.

## Alternatives set aside

- **Ruled out by the ticket:** hook hardening, a hook backstop against persona `Bash`, prose-only rules, an allowlisted `Bash`, and inline orchestrator checks. Session-wide sandbox adoption has no per-subagent scope, a hard-link gap and an auto-approved escape hatch, so it moved to #1295.
- **Diff inputs:** handing every reviewer the one full diff (multiplied by the reviewer count); one script call per spawn (a permission prompt and a `gh pr view` call each); an orchestrator-written lanes manifest (`review-orchestrator` holds no `Write`); reusing the handed cumulative file whole or parsing its headers; a hand-written splitter over one whole patch; and one `git diff` per listed lane with per-path pathspecs.
- **Lane transport:** path text in argv with the orchestrator omitting gate-matching paths (a hand-copied list diverged from the gate's predicate), and a check digit or ordinal ranges.
- **Findings return:** inline return with the dispatcher persisting the file, set aside because every reviewer's findings would enter the parent context, the cost `reviewer-findings-path-output.md` records the `findings_path` mechanism exists to avoid. The Write arm's own-name rule confines the write instead.
- **Failure and checks:** an in-script whole-diff fallback, a fallback decision tree in `SKILL.md`, a call deadline inside the script, a bounded `code-writer` checks dispatch with a tree fingerprint, and requested checks as a separate uncounted section.
- **Smaller:** a batched `git check-ignore -z --stdin`; untracked files as diff blocks; a shared dotenv classifier or credential verdict called by the existing gates (parity tests tie the copies instead); per-lane time budgets; an index diff range; scanning git fragments in the hook; a retention sweep; and binding the Write arm to the repository root.
