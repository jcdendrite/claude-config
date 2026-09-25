# GH-1099: Reviewer scratch-execution rules

## Context

Goal: give the eight `Bash`-holding reviewer personas explicit scratch-execution rules, stop `ciso-reviewer` from carrying out attacks on the machine it runs on, and make the hook's denial text and its record of the gap accurate. File the structural fix as follow-up issues.

**The incident.** A `/code-review` re-review round dispatched a `ciso-reviewer` with closed questions that never asked it to run anything. On its own, it built a test harness in a `/tmp` scratch directory. It created a symlink there itself: a fake plugin-data venv interpreter pointing at the repo's real `.venv/bin/python3`. About three minutes later, a scratch script it had written ran `cat >` on that link. The write followed the symlink chain into a real system-installed Python interpreter and replaced it with a shim that re-executes itself. Every process that used that interpreter hung. The persona's own text had sanctioned the setup: "copy the file into `/tmp` and probe the copy there".

**Why the hook did not stop it.** `deny-reviewer-tree-mutation.sh` denied the first compound command that contained the `ln`, but only because of its `sed -i`; `ln` itself is not gated. The agent then moved the `mkdir` and `ln` into a script it wrote with the Write tool, and ran that script. The hook sees only top-level command strings. The hook's header conceded the `/tmp` link gap on the premise that "no cooperative reviewer performs by accident" the setup. This incident disproves that premise.

**Why this scope.** The engineer judged the structural control a big ticket: removing reviewer `Bash` and having the orchestrator write reviewer inputs. They agreed it is the only control that would have stopped the incident's write, and asked for it to be filed and planned for later. For now they asked for prose, or for a hook only if it does not compound defensive layers. No hook candidate met that bar (Approach). The churn analysis also came back: independent transcript analyses agree that pure in-diff scope drift explains only a small minority of later-round findings. The deferred ban's case therefore rests on containment alone, not on review churn.

**Why now.** It happened once, and the damage landed outside the repository, where no git operation can undo it. The persona text itself invited the setup, and nothing in the current design stops a recurrence.

Intended outcome:
- All eight `Bash`-holding personas carry an identical `## Scratch execution` section, and each persona's intro sentence points to it. Tests pin both.
- `ciso-reviewer` shows exploitability by tracing it and never carries out the attack.
- The hook's denial text no longer recommends `/tmp` copies. It names the link hazard and says not to retry a denied action through a script.
- The hook header and `docs/hooks.md` record the `/tmp` link gap as unwaived and tracked.
- The `claude-hook-review` dispatch wording is unambiguous.
- Three follow-up issues are filed: the sandbox spike, the ban, and containment for all agents.

This PR does not make the incident structurally impossible, because a reviewer that ignores prose can still do it. The ban issue owns that. The PR carries `Refs GH-1099`.

## Approach

Ship prose, not a new gate. Each persona gets an identical `## Scratch execution` section. It covers:
- Read before running anything.
- Prefer inline commands to scripts.
- Treat a hook denial as final.
- Work only in a fresh `mktemp` directory under `/tmp`.
- Write only to new names; never overwrite an existing path.
- Never create a link.
- Run no program that writes through `$HOME` or other environment-derived paths.
- Touch nothing outside the scratch directory.
- Record any check the rules forbid instead of running it.

`ciso-reviewer` is told never to carry out the attack it is testing for. The hook's shared denial text and its conceded-gaps header are rewritten to match. Tests pin every shared sentence. The structural fix goes into three issues: a time-boxed sandbox spike first, then the ban and all-agents containment, both gated on the spike.

**Alternatives set aside:**
- **The ban with inputs the orchestrator writes.** It is the only control that stops the incident's write (row 1). It is deferred to the ban issue, gated on spike A, because a working OS-level write boundary could reshape it.
- **The engineer's script-run ban hook (row 7),** which would deny reviewer runs of `.sh` and `.py` files. It fails for three reasons:
  - it would not have stopped the incident, because every step of the chain passes the `Bash` arm inline (row 18);
  - inline interpreter forms carry the same code with no file, and the hook already concedes nested shell boundaries as undecidable (row 19);
  - matching on extensions both misses scripts and over-denies legitimate ones (row 20).

  Closing the second gap needs another matcher layer, which fails the engineer's no-compounding bar (row 2).
- **A Write arm that checks for links** (`-L` on the target, or resolving it before the `/tmp/*` match). It covers only Write-tool writes, not the incident's `Bash` write inside a script (G1). Links on parent directories would need a component-by-component walk with a macOS `/tmp` carve-out (G3), which is another layer on top of the exemption (row 2).
- **`rm -f` before overwriting a scratch path.** Replaced by the new-name rule (row 38): deletion is destructive, and a fresh name removes the need to replace anything.
- **A link check before every scratch command** (`! find <dir> -type l -o -type f -links +1 | grep . && <command>`). Dropped (row 39): it fails open when `<dir>` is mistyped or missing, and it only guards against the agent breaking the no-link rule, so fixing it would add a layer on a layer.
- **`set -o noclobber` in the rules.** Its behavior on a link to an existing file is unverified (row 25), and it does not cover program writes or Write-tool writes.
- **Dropping the persona `/tmp` Write exemption now,** which was the prior revision's M2. The scratch rules depend on that exemption, so it moves to the ban issue (row 35).

**Dispatch split.** One `code-writer` dispatch (`model: sonnet`) covers every file. The pinned link sentence appears in the eight personas, the hook, and both test files, so splitting the work would restate the same text in every prompt. Issue filing runs inline first, because the hook header and `docs/hooks.md` cite the ban issue's number.

### Assumption ledger

**Root:** A `Bash`-holding reviewer persona can write to any file the user can write. It does this by creating a link under `/tmp` and writing through it, and neither the persona text nor the text-matching hook stops it. With the structural fix deferred (row 1), this plan lowers the chance that a cooperative reviewer does this, and records the gap accurately.

**Givens:**
- G1. A command-text hook sees only the top-level command string of each `Bash` call. It cannot see commands inside a script the agent then runs, because the vendor's PreToolUse payload carries only the tool input. `[verified: deny-reviewer-tree-mutation.sh:96-98, :103-105]`
- G2. Agent bodies load verbatim and have no include mechanism, so persona text that must be shared has to be duplicated. The vendor owns this. `[verified: test_agent_roster.py:302-305]`
- G3. On macOS, `/tmp` is a symlink to `/private/tmp`, so resolving a path before the `/tmp/*` match would false-deny there. The OS vendor owns this. `[verified: deny-reviewer-tree-mutation.sh:76-79, :106-111; not re-checked on a Mac]`
- G4. Changes under `claude/` reach every stow consumer on `git pull`. The install design sits outside this plan. `[verified: CLAUDE.md]`

**Rows:**
1. `[engineer-verified: "I think having the orchestrator-written diff, base, and history files is going to be a BIG ticket. I agree that's the only control that would stop the incident's write. However, I think that should be filed and planned for later."]`
2. `[engineer-verified: "try to come up with prose or a hook (only if it's possible to do that without compounding defensive layers)"]`
3. `[engineer-verified: "guides agents to confirm that writes only go to tmp and check for symbolic links in every command and in any scripts they write, and maybe we discourage them from writing scripts"]`
4. `[engineer-verified: "We can also consider giving extra guidance to ciso agent (since that was the agent that caused issues both times, right?) to not actually compromise the machine when testing resilience against adversarial behavior."]`
5. `[engineer-verified: "those prose rules make sense."]` The tag covers the session's earlier candidate rule set, not the revised set in M1.
6. The engineer approved the consult's revisions, which is the basis for M1. `[engineer-verified: "All the other changes/recommendations/etc look good to me."]` The section wording itself was re-derived after that approval, so the exact text is `[unverified]` as approved.
7. `[engineer-verified: "What if the hook prevents reviewer agents from running .sh or .py files and then in the hook denial message it reminds them not to run scripts but to run inline commands?"]` The tag covers a proposal. Rows 18–20 set it aside, and M3 keeps its "run inline commands" reminder.
8. `[engineer-verified: "Great idea. Do that."]` This settles replacing `SANCTIONED_ALTERNATIVE` so that it repeats the link rule.
9. `[unverified]` M3's exact text below matches what the engineer approved in row 8. The approval covered the idea, not a final string.
10. `[engineer-verified: "Per-lane + file list (Recommended)"]` This settles part of the ban issue's design.
11. `[engineer-verified: "Base + history files (Recommended)"]` This settles part of the ban issue's design.
12. `[unverified]` Rows 10 and 11 settle Open decisions 3 (diff input shape) and 5 (history access) of the preserved prior revision. The mapping is my reading.
13. `[engineer-verified: "needs to be made not ambiguous"]` This covers the `claude-hook-review` dispatch wording.
14. `[verified: the incident transcript, read during this session's consult; not reopened for this revision]` The agent created the link itself, about three minutes before writing through it.
15. `[verified: the prior revision's transcript sweeps; not re-run; qualitative]` Two findings follow:
    - `ciso-reviewer` was the agent in both the incident and an earlier near miss;
    - `staff-sdet` repeatedly created `.venv` links into `/tmp`.

    The scratch rules therefore go to all eight personas, and `ciso-reviewer` gets the extra guidance.
16. `[verified: transcript analysis this session, per the dispatch summary; qualitative per docs/private-project-redaction.md]` Pure in-diff scope drift explains a small minority of later-round findings.
17. `[verified: ciso-reviewer.md:9; the seven staff-*.md intro sentences]` Every persona's intro sentence sanctions copying files into `/tmp` and running them there.
18. `[verified: deny-reviewer-tree-mutation.sh:223-226, :475-487]` The `Bash` arm has no `ln` matcher, and it allows a `>` redirect whose target text starts with `/tmp/` (`:483`). Every step of the incident's chain therefore passes inline.
19. `[verified: deny-reviewer-tree-mutation.sh:99-105]` A command reached through a nested shell boundary (`bash -c "..."`) is undecidable for the hook. So is code handed to an interpreter inline, whether by `-c`, `-e`, or a heredoc.
20. `[reasoned]` Matching on extensions misses a script run under another name (`bash exp`, a shebang file, `source`). It also denies a project's own read-only `.sh` and `.py` tooling.
21. `[verified: deny-reviewer-tree-mutation.sh:252-262, :196-244]` Both arms match write targets as literal text. They neither expand variables nor resolve links, so a write to `"$d/x"` denies even when `$d` is under `/tmp`.
22. `[verified: CPython 3.12 Lib/venv/__init__.py:30-32, :293-302, :541-546 on this machine]` On a non-Windows system, `python -m venv` defaults to symlinking the environment's interpreter to the base interpreter.
23. `[verified: resume-context.sh:267-268; .claude/plans/GH-477-pr-description-authoring.md:287-290, which quotes macOS mktemp(1); not re-read on a Mac]` The positional-template form of `mktemp` is portable across GNU and BSD/macOS. On macOS, a bare `mktemp -d` behaves like `-t tmp`. The rule passes an explicit `/tmp/...XXXXXX` template, so it does not depend on where a bare call lands, and the path matches the Write arm's literal `/tmp/*` exemption (`:262`).
24. `[unverified]` Whether GNU and BSD `cp -R` copy symlinks as links. No rule depends on this, because the rule bans copying directory trees outright.
25. `[unverified]` How noclobber behaves for `>` onto a link to an existing file, and how it behaves under zsh. No rule uses noclobber.
26. `[verified: plan-review staff-platform-engineer reproduction on GNU find; macOS smoke test on BSD find, bash and zsh, this session]` The guard form `! find <dir> ... | grep . && <command>` runs `<command>` when `<dir>` does not exist, because `find` prints its error to stderr and `grep .` sees no input. `pipefail` does not change the outcome.
27. `[reasoned]` A hard link from `/tmp` to a user-owned file is possible where `/tmp` shares a volume with that file. The no-link rule covers hard links whether or not that holds.
28. `[verified: docs/hooks.md:18, :69]` An elevated row's Why cell quotes the header sentence it rests on. The current cell quotes the premise this plan removes.
29. `[verified: test_deny_reviewer_tree_mutation.py:22; test_agent_roster.py:298-390, :864-911]` Three test precedents exist:
    - the hook test already imports from `test_agent_roster`;
    - the byte-identical `### File-based output` test uses the first sorted entry as its canonical copy;
    - the `ciso-reviewer` pins use one constant per sentence.
30. `[verified: grep of the eight persona files this session]` In every persona, `## How to work` is immediately followed by `## Shared ownership`.
31. `[verified: plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:162]` "Pass the hook script or diff" names neither a form (path, contents, or ref) nor a source. `[reasoned]` The clarified form works whether or not the spawned reviewer holds `Bash`, so it is independent of the ban and belongs in this PR.
32. `[verified: plugins/claude-hook-review/.claude-plugin/plugin.json:4; .claude/rules/review-pipeline-dispatch.md]` The plugin is at `2.4.0`, and a hook requires a strict version raise for any change under its directory.
33. `[verified: CLAUDE.md "Changes under claude/.claude/** go live on git pull"]` This PR's own reviews still load the pre-change persona bodies, so live behavior is checked after merge.
34. `[unverified]` The repo has `priority:high` and `priority:medium` labels.
35. `[verified: deny-reviewer-tree-mutation.sh:262; README.md:227; docs/design-decisions/plan-review-gate-disarms-on-empty-active-plan-set.md:31]` Personas keep `Bash` and the `/tmp` Write exemption. Hook behavior is unchanged, and those two docs stay accurate.
36. `[reasoned]` The PR uses "Refs GH-1099", not "Closes". GH-1099's goal of structural impossibility is not met by prose, and the ban issue carries it.
37. `[verified: CLAUDE.md "that coupling is theirs to maintain"]` A consuming repo's same-named persona override does not get the new section.
38. No `rm -f` before a write. Scratch writes go only to names not yet used in the fresh directory, and an existing path, including one of the agent's own, is never overwritten or replaced. `[engineer-verified: "rm is a destructive action and agents might remove a file that another agent is actively using, or it could accidentally remove something outside of /tmp. I think it's better to instruct checking for existence and then if it exists use a different path."]` The rule is worded as "use a name you have not used before" rather than as an existence test, because `[ -e ]` reports false for a dangling symlink. That wording and this rationale are the orchestrator's `[reasoned]`. Nothing is lost against `rm -f`: `rm` only ever removed the link, and the incident's write was an attempt to replace an existing path in place, which this rule forbids. One consequence: scratch files accumulate in `/tmp` until the OS cleans them, rather than being removed mid-session.
39. No per-command link check; the section keeps only the direct rules. `[engineer-verified: "Drop the per-command check (Recommended)"]` The option described dropping it because it fails open (row 26) and only guards against breaking the no-link rule. That description is the orchestrator's.
40. The post-merge sweep runs once, and re-running it is a step of the ban issue before that issue's design starts. Nothing else detects drift in between, and the PR body says so. `[engineer-verified: "One-shot + in ban issue (Recommended)"]`
41. `[verified: plan-review ciso-reviewer, this session]` A program run from scratch can write through `$HOME` or other environment-derived paths (`pip` to `~/.cache/pip`, `npm` to `~/.npm`, `git config --global`). No hook sees an interpreter-internal write, so the prose names the hazard explicitly.
42. `[verified: plan-review staff-platform-engineer, this session]` A bare `git diff -- <path>` compares the working tree with the index, so it prints nothing once the edit is staged. `git diff HEAD -- <path>` covers staged and unstaged changes.

**Mechanisms:**
- **M1.** An identical `## Scratch execution` section in the eight personas, plus a pointer from each intro sentence. It is prose, the lightest primitive, and adds no gate. `anchors: row3`
- **M2.** The `ciso-reviewer` mandate changes from "demonstrate" to "trace, never carry out", with a pinned sentence. `anchors: row4`
- **M3.** A new `SANCTIONED_ALTERNATIVE` that carries the pinned link sentence and the no-retry reminder. It changes the text of an existing string and no logic. `anchors: row8`
- **M4.** The hook header `:72-81`, `docs/hooks.md:69`, and `README.md:174` record the gap as unwaived and tracked. `anchors: row14`
- **M5.** Tests pin M1–M3, and a characterization test pins the hook's current allow verdict for the incident's link-then-write sequence, so the header's gap description cannot go stale silently. `anchors: root`
- **M6.** The `claude-hook-review` `:162` wording change and a plugin version bump. `anchors: row13`
- **M7.** Three issues filed, none implemented. `anchors: row1`
- **M8.** A sweep after merge, because the prose is unenforced. It runs once; the ban issue re-runs it (row 40). `anchors: row40`

## Critical files

**Inline, before the dispatch.** The orchestrator does these steps itself.
1. Run `gh label list` (row 34). If there are no priority labels, state the priority in each issue body and ask the engineer before creating any labels.
2. File the issues in this order and record each number. The repo is public: describe the incident abstractly, and publish no counts, absolute paths, or session identifiers.
   - **Issue A: the sandbox spike.** Priority `priority:high`; it runs first and is time-boxed. Evaluate the Claude Code sandbox as an OS-level write boundary for agents that hold `Bash`. It exits when it answers three questions:
     - Is a write through a symlink whose target lies outside the allowed paths blocked on both macOS and Linux?
     - What breaks for the main session and for `code-writer` when the sandbox applies session-wide?
     - Can a subagent carry a narrower policy than its parent?

     State that it gates the ban issue and issue B.
   - **The ban issue.** Priority `priority:high`, gated on A. Seed its body from the preserved copy of this plan's prior revision (`/tmp/gh1099-drafts/ban-design-full-plan.md`). Put a status block on top with four items:
     - the two selected labels, verbatim, as settled design (rows 10–12);
     - the churn result (row 16), replacing that revision's "deferred until the churn data" framing;
     - a note that the persona `/tmp` Write exemption drop now lives in this issue;
     - a note that this PR's prose is the interim control;
     - a first step: re-run the post-merge transcript sweep before the design starts (row 40).

     Before filing, strip every quantitative sweep figure and keep sweep findings qualitative.
   - **Issue B: containment for all agents.** Priority `priority:medium`, blocked on A. It covers `code-writer`, the main session, `Explore`, and `Plan`, including `Plan`'s open `/tmp` link vector.

**One `code-writer` dispatch (`model: sonnet`).** Point the prompt at this plan file for every verbatim text, and give it the ban issue's number. Verify with `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.

- **The eight personas** (`ciso-reviewer.md` and the seven `staff-*.md` files under `claude/.claude/agents/`):
  - Replace the sentence that begins "The tree under review is read-only:" with the pointer text below. It sits at `:9`, or `:11` in `staff-product-engineer`. Leave the persona-specific sentences before it alone.
  - Insert the section text below, byte-identical, between `## How to work` and `## Shared ownership` (row 30).
  - Leave the frontmatter unchanged.
- **`ciso-reviewer.md` only:**
  - At `:9`, "you find attack paths and demonstrate exploitability, not assert it" becomes "you find attack paths and show exploitability by tracing it through the code, never by asserting it or carrying out the attack".
  - At `:52`, How-to-work item 2 becomes the text below.
- **`claude/.claude/hooks/deny-reviewer-tree-mutation.sh`:**
  - `:161`: replace `SANCTIONED_ALTERNATIVE` with the text below.
  - `:72-81`: replace that gap bullet with the bullets below.
  - Leave the tier line (`:3`) and all logic unchanged.
- **`claude/.claude/hooks/tests/test_agent_roster.py`:** reuse the next-`## `-heading extraction approach of `_extract_input_contract_section`, plus `AGENTS_DIR` and `REVIEWER_AGENTS`.
  - Add module constants:
    - `SCRATCH_LINK_SENTENCE`, public because the hook test imports it, with the comment `# Pinned verbatim in each persona's Scratch execution section and in deny-reviewer-tree-mutation.sh's SANCTIONED_ALTERNATIVE.`;
    - `SCRATCH_NEW_NAME_SENTENCE`, public for the same reason, with the same comment;
    - `_SCRATCH_EXECUTION_POINTER`;
    - `_CISO_NO_LIVE_ATTACK_SENTENCE`, holding the pinned ciso sentence quoted below.
  - Add `TestScratchExecutionSection`. It extracts each persona's section from the `## Scratch execution` line up to, but not including, the next `## ` line, and asserts:
    - every `REVIEWER_AGENTS` section matches `REVIEWER_AGENTS[0]`'s byte for byte;
    - the section contains `SCRATCH_LINK_SENTENCE` and `SCRATCH_NEW_NAME_SENTENCE`;
    - every persona body contains `_SCRATCH_EXECUTION_POINTER`;
    - no `REVIEWER_AGENTS` body contains "copy the file into" or "rm -f".
  - Add `TestCisoReviewerNoLiveAttackPin` in the shape of `TestCisoReviewerSecurityBulletsPin`.
- **`claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py`:**
  - `:22`: import `SCRATCH_LINK_SENTENCE` and `SCRATCH_NEW_NAME_SENTENCE` alongside `CANARY_AGENTS`.
  - `:109-113`: parametrize over one Write-arm denial (the existing payload) and one `Bash`-arm denial (`sed -i s/a/b/ src/x`, `agent_type="staff-sdet"`). Assert that the reason contains `SCRATCH_LINK_SENTENCE`, `SCRATCH_NEW_NAME_SENTENCE` and `agent-reviews`, and does not contain "copy the file".
  - `:571-572`: the comment becomes `# Copying one file into a /tmp scratch directory is the persona scratch-execution workflow; this gate must never deny it.`
  - Add a characterization test for the incident's sequence as a reviewer persona (`agent_type="ciso-reviewer"`):
    - a Bash `ln -s <tracked file> /tmp/x` is allowed;
    - a Write to `/tmp/x` is allowed;
    - a Bash `cat > /tmp/x` is allowed.

    Comment it: `# Pins the accepted /tmp link gap described in the header's known-gaps list; a change here must update that entry.` (row 18).
- **`docs/hooks.md:69`:** the Why cell becomes the text below.
- **`README.md:174`:** the third cell becomes "No clear — confirm by reading and tracing; a persona's scratch work follows its `## Scratch execution` section".
- **`plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:162`:** replace the paragraph with the text below. `:171`'s "the same source" still reads correctly after the change.
- **`plugins/claude-hook-review/.claude-plugin/plugin.json`:** bump the version per `plugin-semver:plugin-semver`. A patch bump to `2.4.1` is expected, but the skill decides.

**Pointer text** (`_SCRATCH_EXECUTION_POINTER`):

```text
The tree under review is read-only: the only write you make into it is the `findings_path` file. Before you run anything, follow `## Scratch execution` below.
```

**Section text** (byte-identical in all eight). Review rounds rewrote parts of this text and of the pinned sentences and denial text below; the persona files and their tests are the authoritative wording, and the blocks in this plan are the initial draft:

```markdown
## Scratch execution

Confirm a claim by reading and tracing the code first. Run something only when tracing cannot settle the claim, and then follow every rule below.

- Prefer an inline command to a script. The review hook checks each command you run but cannot see inside a script, so every rule here also binds each line of any script you write.
- Treat a hook denial as final. Never retry the denied action through a script, another command form, or another tool.
- Work in one fresh directory created with `mktemp -d /tmp/<name>.XXXXXX`, where `<name>` is your own agent name. Spell its printed path out literally in every later command, because the review hook checks write targets as written.
- Write only to files you create inside that directory. Run a program only when you know every path it writes. Each must be inside that directory or a fresh temporary directory the program makes for itself. When you cannot tell, do not run it.
- Write each file under a name you have not used before in that directory. Never overwrite or replace an existing path, even one you created; write a new file under a new name instead.
- Never create a link. A write through a symlink or hard link changes the linked file, wherever it lives, so a /tmp path can still change a file outside /tmp. That rules out:
  - `ln` in any form;
  - a virtual environment, because `python -m venv` links its interpreter;
  - copying a directory tree, which can carry links along.

  Copy single regular files, and run a project's interpreter or tool by its real path instead of through a link.
- Run no program that writes through your home directory or another environment-derived path, whatever directory you run it from. Package managers, build tools, and git's global configuration do this: `pip` writes `~/.cache/pip`, `npm` writes `~/.npm`, and `git config --global` writes `~/.gitconfig`.
- Never write to, replace, or reconfigure anything outside that directory: no interpreter, binary, installed package, shell, git, or Claude configuration, and no file in the tree under review.
- When a check needs something these rules forbid, do not run it. Record in your findings what you would run and what result would confirm the finding.
```

**Pinned link sentence** (`SCRATCH_LINK_SENTENCE`), which appears verbatim in the section above and in `SANCTIONED_ALTERNATIVE`:

```text
A write through a symlink or hard link changes the linked file, wherever it lives, so a /tmp path can still change a file outside /tmp.
```

**Pinned new-name sentence** (`SCRATCH_NEW_NAME_SENTENCE`), which appears verbatim in the section above and in `SANCTIONED_ALTERNATIVE`:

```text
Never overwrite or replace an existing path, even one you created; write a new file under a new name instead.
```

**`ciso-reviewer.md` How-to-work item 2:**

```markdown
2. Show exploitability by tracing it — don't assert it. Trace attacker-controlled input to the privileged operation and confirm each hop in the code. Never carry out the attack you are testing for — no exploit, payload, or attempt to evade a hook or gate — because a probe that succeeds compromises the machine you run on. Feeding a crafted input to the code under review and reading its verdict is tracing, and follows `## Scratch execution`. Executing what that input would do is the attack, and so is sending a real payload to a running copy of the service under review, even one you started in scratch. If you can't construct the path, say "potential finding, couldn't confirm exploitability."
```

**Pinned ciso sentence** (`_CISO_NO_LIVE_ATTACK_SENTENCE`):

```text
Never carry out the attack you are testing for — no exploit, payload, or attempt to evade a hook or gate — because a probe that succeeds compromises the machine you run on.
```

**`SANCTIONED_ALTERNATIVE`** (`:161`):

```bash
SANCTIONED_ALTERNATIVE="Reviewers are read-only on the tree under review. Do not retry a denied action through a script, another command form, or another tool. Confirm a claim by reading and tracing the code before running anything. Scratch work belongs only in a fresh directory you created under /tmp, holding only files you created there. Never overwrite or replace an existing path, even one you created; write a new file under a new name instead. A write through a symlink or hard link changes the linked file, wherever it lives, so a /tmp path can still change a file outside /tmp. The only sanctioned in-tree write is the findings file (agent-reviews/<agent>-<epoch>-<slug>.md, via the Write tool)."
```

**Hook header, replacing `:72-81`:**

```bash
#   - A symlink or hard link under /tmp launders the /tmp exemption, because
#     both arms match the literal `/tmp/*` text without resolving links.
#   - A write through such a link changes the linked file, which can be any
#     file the user can write, not only a tracked one.
#   - Resolving the path before the match would false-deny every legitimate
#     macOS /tmp write, because /tmp is a symlink to /private/tmp there (see
#     the macOS `/tmp` note below).
#   - A cooperative reviewer can create such a link and then write through it
#     without noticing, so this gap is not waived on cooperative grounds.
#   - Each Bash-holding persona's "## Scratch execution" section is the
#     current mitigation.
#   - GH-<ban> tracks the structural fix.
#   - A test in test_deny_reviewer_tree_mutation.py pins this gap's current
#     allow verdict.
```

**`docs/hooks.md:69` Why cell:**

```text
A bypass mutates the parent session's uncommitted tree, the same consequence class as the two worktree gates, and a write through a `/tmp` link can replace any file the user can write. Not `untrusted-input`: neither its own header nor another gate's header states that steered review-input content is part of what it defends against (the positive test, above). Its closed-enumeration gap (`rm`, interpreter-mediated writes) is accepted debt, like `deny-network-installs.sh`'s gaps. Its `/tmp` link gap is accepted debt tracked on GH-<ban>; its header states "A cooperative reviewer can create such a link and then write through it without noticing, so this gap is not waived on cooperative grounds."
```

**`claude-hook-review` `:162`:**

```markdown
After the section-9 checklist, spawn `staff-platform-engineer` synchronously. Pass the hook script's path. For a change to an existing hook, also pass its diff: the diff-file path you were handed, if any, otherwise the output of `git diff HEAD -- <hook path>` as literal text. Never pass a branch or range name, and never this review's output — each agent reads the source fresh. Ask it these questions:
```

At `:171`, "Pass the same source" becomes "Pass the same path and diff" (after the `:162` change, "source" would refer to both a path and a diff).

## Verification

- After the dispatch, run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, `.venv/bin/ruff check claude/.claude/ claude-skills/`, and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`. Reproduce any failure at the merge-base before treating it as in scope.
- **Regression-only rule** (`docs/hooks.md:26`). The hook's logic is unchanged, and only the `SANCTIONED_ALTERNATIVE` string and header comments change. Every existing allow and deny test passes unchanged apart from the reason assertions at `:109-113`. The commit message states two rationales:
  - why the "no cooperative reviewer performs by accident" premise was dropped;
  - why the header now carries the ban issue pointer.
- `git grep -n "copy the file" -- claude/.claude/ README.md` returns no hits.
- Run these review gates:
  - `/code-review`;
  - `/agent-review` on the eight persona files;
  - `claude-hook-review:claude-hook-review` on the hook change;
  - `/skill-review` on the plugin `SKILL.md` (hook-enforced);
  - `plugin-semver:plugin-semver` (hook-enforced);
  - `/ready-for-review`.

  These reviews load the pre-change persona bodies (row 33).
- The PR body carries `Refs GH-1099`, links the three issues, and names what every stow consumer will see:
  - personas follow the scratch-execution rules;
  - `staff-sdet` no longer runs the existing test suite, and `staff-platform-engineer` no longer runs formatters or linters on copies, whenever that needs a directory-tree copy or a virtual environment (both now banned) or writes outside scratch; they record the check instead;
  - scratch files accumulate in `/tmp` until the OS cleans them, because personas no longer overwrite or delete scratch paths;
  - `ciso-reviewer` traces attacks and never carries them out;
  - the hook's denial text has changed;
  - the `claude-hook-review` dispatch wording has changed;
  - until the ban lands, the one post-merge sweep is the only detection of drift from these rules.
- **After merge and pull,** once reviews have run on the new bodies, dispatch a `general-purpose` subagent (`model: sonnet`). It sweeps reviewer-persona transcripts from after the pull, scoped per `docs/private-project-redaction.md` § "Publishing a tooling measurement", looking for:
  - `ln` in any form;
  - venv creation;
  - directory-tree copies into `/tmp`;
  - writes outside a `/tmp` directory made by `mktemp`;
  - overwrites of an existing scratch path;
  - a denied action retried through a script;
  - `ciso-reviewer` executing a probe.

  It reports qualitatively. Any recurrence goes on the ban issue as evidence for its priority. Publish no counts. The ban issue re-runs the same sweep before its design starts (row 40).

## Out of scope

- The ban: removing reviewer `Bash`, inputs the orchestrator writes, and dropping the persona `/tmp` Write exemption. These belong to the ban issue (row 1).
- The sandbox, which belongs to issue A.
- Containment for `code-writer`, the main session, `Explore`, and `Plan`, which belongs to issue B.
- A hook that bans script runs or checks for links (Approach, alternatives).
- `skill-fidelity-reviewer` and `comment-discipline-reviewer`. They hold no `Bash`, so they get no scratch section.
- A consuming repo's same-named persona override (row 37).
- A design-decision file. The persona text, the hook header, and the issues carry the decision.
- `README.md:174`'s agent list, which omits the two non-specialist reviewers. It was noticed while passing through and is left unchanged.
- `SANCTIONED_ALTERNATIVE`'s scratch advice at the git-subcommand deny site (`:395`), where no scratch equivalent exists. The mismatch predates this change and the site's own text already says the ban applies anywhere.
- Stale `/tmp` links on existing machines. The prior revision's sweep found no live corruption; that was not re-run.
