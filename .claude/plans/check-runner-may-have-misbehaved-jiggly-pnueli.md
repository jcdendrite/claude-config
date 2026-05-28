# Dispatch-id–scoped spool identity for check-runner

## Context

A parent session in a downstream project dispatched `check-runner` against `npm run verify` and got back a return that violated the agent's charter on two counts:

1. The body was prose analysis ("This is the problem. The migration … has a table-level UPDATE grant that overwrites …"), not a per-command structured verdict (name / exit code / pass-fail / failure excerpt / spool path).
2. It included a **"Summary of Findings" + "The fix requires correcting both issues by: 1. … 2. …"** prescription — a direct violation of `claude/.claude/agents/check-runner.md` line 46: *"Do not interpret failures or recommend fixes — that is the parent's job."*

The agent *did* run the command (`toolStats.bashCount: 14`, `duration_ms: 159386` — consistent with a real suite run) and *did* write a spool file at the conventional path. It just did not return the spool path in its verdict and did not return a verdict in the chartered shape.

The parent's response was wrong on two counts:

- **Diagnosis:** "The check-runner analyzed instead of running" — declarative, but the agent had run the command; the parent inferred from the return shape alone, without ever checking for the spool file.
- **Action:** re-ran `npm run verify 2>&1 | tail -80` in the parent's own Bash tool — inhaling the very output check-runner exists to absorb. Conceded the agent had run the command only after the user pushed and asked for the temp file, then located it on first try at the conventional path.

So both actors failed. **Agent-side:** the haiku model returned prose + a prescription. Failure tails for 15 tests landed in its context (Incident 5's silent-on-success structural fix only suppresses the *passing*-run path), and given the tails, the haiku free-formed an explanation rather than the structured verdict. The prose rules on lines 33–46 of `check-runner.md` did not hold. **Parent-side:** when the verdict shape looked wrong, no operational rule named "verdict format off" as a legitimate spool-read trigger, and there was no unambiguous way for the parent to locate the spool without trusting the verdict text the parent was already doubting. The parent reached for re-execution.

The first draft of this plan added a parent-side rule that recovered the spool via `ls -t "${TMPDIR:-/tmp}/<slug>-*.txt" | head -1`. That re-introduces exactly the stale-glob failure mode Incident 4 banned for the agent: multiple sessions or worktrees can produce prior-session spools sharing the same slug prefix.

**Foundational fix: bind each dispatch's spool identity to a parent-supplied UUID.** The parent generates a unique dispatch-id per check-runner invocation, includes it in the dispatch prompt, the agent uses it as the spool filename prefix and echoes it back verbatim in the verdict. The parent can then (a) verify any returned spool path begins with the dispatch-id it emitted (defense against agent confabulation), and (b) locate the spool unambiguously via `ls "${TMPDIR:-/tmp}/<dispatch-id>"-*.txt` if the verdict drift omits the path — globally unique by UUID construction, so stale-glob collision is impossible. Re-dispatch becomes the rarely-needed last resort, not the routine recovery.

## Diagnosis (one sentence each)

- **Agent side:** the haiku still drifts to prose when failure tails are in context. The foundational agent-side fix (return-template constraint, JSON-shaped verdict, or removing failure tails from the agent's context) is deferred — but a UUID echo gives the parent a way to *verify* the spool the agent wrote even when the verdict prose drifts.
- **Parent side:** the doubt-the-verdict recovery primitive was missing. Add it, and make it deterministic (UUID lookup) rather than heuristic (slug glob).

## Recommended change — UUID-scoped dispatch contract

The change touches three sites in coordinated form. They ship together because they encode one contract; partial adoption breaks recovery.

### 1. `claude/.claude/agents/check-runner.md` — agent contract: dispatch-id + hook-enforced read restriction

Three edits inside the existing body (no new sections):

**Edit A — Working-directory section (current lines 13–14) adds a sibling paragraph after it:**

> **Dispatch ID.** The dispatch prompt specifies a `dispatch-id` token — a hex string the parent generates per invocation. Use it as the spool filename prefix on every command (see template below) and echo it back verbatim in the structured verdict as a top-level field. If the dispatch prompt does not include a `dispatch-id`, return immediately: overall verdict FAIL, message "no dispatch-id specified — re-dispatch with `dispatch-id: <uuid>` in the prompt." Do not generate one yourself; the parent must own it for verification to mean anything.

**Edit B — Per-command template at current line 24 changes from:**

```
EPOCH=$(date +%s%3N); SPOOL="${TMPDIR:-/tmp}/<slug>-${EPOCH}.txt"; echo "SPOOL:$SPOOL"; <command> > "$SPOOL" 2>&1; EXIT=$?; echo "EXIT:$EXIT"; if [ "$EXIT" -ne 0 ]; then tail -50 "$SPOOL"; fi
```

**To:**

```
EPOCH=$(date +%s%3N); SPOOL="${TMPDIR:-/tmp}/<dispatch-id>-<slug>-${EPOCH}.txt"; echo "SPOOL:$SPOOL"; <command> > "$SPOOL" 2>&1; EXIT=$?; echo "EXIT:$EXIT"; if [ "$EXIT" -ne 0 ]; then tail -50 "$SPOOL"; fi
```

Substitute the literal hex string from the dispatch prompt for `<dispatch-id>`. The structured-verdict requirements list (current lines 35–44) gains a sixth bullet: `- The dispatch-id (as received in the prompt, echoed verbatim).`

**Edit C — Replace the current line 33 paragraph ("Never read back the spool in a separate Bash call. Never locate a spool file with a glob or `ls` pattern...") with:**

> **Hook-enforced read restriction.** The PreToolUse(Bash) hook (`check-runner-bash-guard.sh`, wired via `settings.json` and scoped to `agent_type == "check-runner"`) denies any file-read command — `cat`, `head`, `tail` (except the per-command template shape), `less`, `more`, `view`, `grep`/`egrep`/`fgrep`, `awk`, `sed`, `rg`, `fd`, `find -print|-exec`. Your only legitimate Bash shapes are (1) `cd <absolute-path>` as the first call, and (2) the per-command template above. The hook treats anything else as out-of-charter and returns a HOOK_BLOCK verdict — which you must surface verbatim per the BLOCKED handling rules below. Never read back the spool in a separate Bash call (the hook would block it anyway); never locate a spool file with a glob or `ls` pattern.

### 2. `claude/.claude/skills/subagent-delegation/SKILL.md` — parent contract: emit dispatch-id and use it for verification

Two edits inside the existing check-runner section:

**Edit A — At the end of the existing "Heavy command output → check-runner" enumerated list of dispatch requirements (after the "Do not enumerate setup or state-mutating commands…" bullet, currently line 76):**

> - **Generate a dispatch-id per invocation and include it in the prompt** as `dispatch-id: <uuid>` (one line, hex token from `uuidgen` or equivalent). The agent uses it as the spool filename prefix and echoes it back in the verdict, so the parent can both verify the verdict's origin and locate the spool unambiguously if needed.

**Edit B — New named paragraph after "Reporting test counts" and before "A lock your session holds…" (currently between lines 93 and 95):**

> **Doubting the verdict.** If a check-runner return looks unusual — structure off, prose where a structured verdict was expected, or fix prescriptions the charter forbids — the recovery is to `cat` the spool, not re-run the command in the parent. The dispatch-id you emitted makes locating the spool deterministic: `ls "${TMPDIR:-/tmp}/<dispatch-id>"-*.txt` matches exactly the files this dispatch wrote, immune to the stale-glob collision the slug-only pattern in Incident 4 produced. Verify the agent's verdict echoed back the same dispatch-id you sent — a mismatch (or absence) is the unambiguous signal that the verdict text is not from your dispatch. Re-running the command in the parent inhales the output back into context, defeating the dispatch; the verdict you doubted often turns out to be the agent having partially completed its job.

### 3. `claude/.claude/skills/ready-for-review/SKILL.md` — dispatch recipe absorbs the change

Edit line 48's dispatch recipe to mention the dispatch-id:

> **Run the commands via the `Agent` tool with `subagent_type: check-runner`** — not inline Bash. Suite-level output displaces parent working state and invalidates the prompt cache. **Enumerate the exact command strings in the dispatch prompt, and include `dispatch-id: <uuid>` (one line, hex from `uuidgen` or equivalent) so the parent can verify the verdict's origin and locate the spool deterministically.** […rest of paragraph unchanged…] The subagent writes each command's full output to `${TMPDIR:-/tmp}/<dispatch-id>-<slug>-<epoch-ms>.txt` (slug as before), then returns: […unchanged…] If the parent needs more detail, Read the file — do not re-run.

(Surgical: just the dispatch-id mention in the rule prose and the spool path template; the rest of the line's prose stays.)

### 4. `claude/.claude/hooks/check-runner-bash-guard.sh` — restrict the agent to spool-only reads

Already wired via `claude/.claude/settings.json` (PreToolUse(Bash), scoped to `agent_type == "check-runner"` — the case study's Incident 1 "kept as reference implementation" text is outdated and a separate cleanup, not in scope here). Currently denies writes/mutations (git mutations, `<word> db reset|push|migrate|seed`, force-push, hard reset, rm -rf root/HOME/~, plus the project-layer extension file). It does **not** restrict reads.

The prose-drift in Incident 6 ("This is the problem … according to AGENTS.md G1 …") was fueled by the agent `cat`ing project files (AGENTS.md, migration SQL) the charter never authorized it to read. The agent's legitimate Bash calls are only:

1. `cd <absolute-path>` — the Working-directory anchor (current line 13).
2. The per-command template (current line 24) — emits SPOOL path, runs the command, captures exit code, on failure runs `tail -<N> "$SPOOL"`. The only file read is the spool itself, inside the same call that defined `$SPOOL`.

Extend the hook to deny free-standing file-read commands. Two parts:

**Edit A — New deny category in `GLOBAL_FILE_READ_PATTERNS` (separate array after `GLOBAL_DENY_PATTERNS`, so the deny message can identify file-read violations specifically):**

```bash
# File-read commands: check-runner's chartered Bash calls are `cd <path>`
# and the per-command template (check-runner.md). The template's only
# legitimate file read is `tail -<N> "$SPOOL"` on failure, inside the same
# call that defined $SPOOL. Free-standing reads of project files pull
# material into the agent's context that fuels prose-drift verdicts (see
# docs/case-studies/check-runner.md Incident 6). A positive-match carve-out
# for the template shape (tail -<N> "$SPOOL") is checked before this loop.
'\b(cat|head|less|more|view)\b'
'\b(grep|egrep|fgrep|awk|sed)\b'
'\b(rg|fd)\b'
'\btail\b'  # bare tail; allowed-shape carve-out below
'\bfind\b.*(-print|-exec)'
```

**Edit B — Positive-match carve-out BEFORE the deny loop (insert in the `while IFS= read -r fragment` loop, immediately after the empty-fragment check):**

```bash
  # Carve-out: per-command-template failure-excerpt shape.
  # `tail -<digits> "<single-token>"` where the token references SPOOL
  # (with or without quotes). The if-then-fi form yields `then tail ...`
  # after semicolon-splitting — the optional `then` keyword is accepted.
  # Authorized by check-runner.md line 24.
  if [[ "$fragment" =~ ^[[:space:]]*(then[[:space:]]+)?tail[[:space:]]+-[0-9]+[[:space:]]+(\"?\$SPOOL\"?)[[:space:]]*$ ]]; then
    continue
  fi
```

(Final regex shape to be confirmed against `_lib_split_fragments` semantics during implementation — verify a fragment containing exactly the failure-excerpt shape from line 24 matches this carve-out, and that no other fragment shape matches.)

### 5. `claude/.claude/hooks/tests/test_check_runner_bash_guard.py` — add coverage for the new deny patterns

Test cases:
- `cat /path/to/file` → denied (file-read)
- `head -10 README.md` → denied (file-read)
- `grep PATTERN file.py` → denied (file-read)
- `awk '/foo/' file` → denied (file-read)
- `sed -n '1,10p' file` → denied (file-read)
- `rg pattern src/` → denied (file-read)
- `find . -name '*.ts' -print` → denied (file-read with content intent)
- `tail -50 "$SPOOL"` → allowed (per-command-template carve-out, quoted)
- `tail -100 $SPOOL` → allowed (per-command-template carve-out, unquoted)
- `tail -20 README.md` → denied (tail, but not the carve-out shape)
- `cd /home/user/some-repo` → allowed (cwd anchor)
- Full per-command template from line 24 of check-runner.md → allowed end-to-end (verifies the carve-out works on real fragment-splitting output)

### 6. `docs/case-studies/check-runner.md` — Incident 6 records both halves of the fix

Insert after Incident 5 (current line 59) and before "Re-grounding" (current line 61). Follow the structural template (dated header, narrative paragraph, two-gap diagnosis, resolution paragraph, throughline closer). Strict redaction: no project name, branch name, migration filename, or edge-function name.

### What stays out

- **No verdict-shape constraint.** Even with reads denied, the agent still has failure tails in its context on a failing run and can still drift to prose. Constraining the return shape itself (JSON schema, fixed template, or removing failure tails the way Incident 5 removed pass tails) is a separate foundational change and belongs in its own plan. The read restriction reduces *severity* of drift (citations to project material disappear) but does not eliminate drift outright.
- **No CLAUDE.md edit.** The contract change is check-runner-scoped; generalizing the verdict-skepticism rule violates `feedback_no_overgeneralize.md`.
- **No retrofit of every doc mentioning check-runner.** Only the prescriptive dispatch and enforcement sites (`subagent-delegation/SKILL.md`, `ready-for-review/SKILL.md`, the hook, the agent file) get the contract update. Descriptive mentions (README, design-decisions.md, case-studies/check-runner.md outside Incident 6, hooks.md, transcript-analysis.md, etc.) describe behavior in general terms and do not break if they omit the dispatch-id or read-restriction details. Walking them all would expand scope without a correctness benefit.
- **No fallback path for "agent runs without dispatch-id".** Parent and agent ship together via stow; on `git pull` both update atomically. A fallback would create two code paths and dilute the verification value. Agent refuses dispatches without a dispatch-id (verdict FAIL with explicit message); parent skill mandates inclusion.

## Files to modify

1. `claude/.claude/agents/check-runner.md` — add Dispatch-ID paragraph; update per-command template (line 24); add dispatch-id bullet to structured-verdict requirements (lines 35–44); rewrite the existing "Never read back the spool" paragraph (line 33) as "Hook-enforced read restriction".
2. `claude/.claude/skills/subagent-delegation/SKILL.md` — add the dispatch-id requirement bullet to the check-runner dispatcher rule; insert the "Doubting the verdict" recovery paragraph.
3. `claude/.claude/skills/ready-for-review/SKILL.md` — update the line-48 dispatch recipe to include dispatch-id in the prompt and in the spool path template.
4. `claude/.claude/hooks/check-runner-bash-guard.sh` — add file-read deny patterns to `GLOBAL_FILE_READ_PATTERNS`; add the positive-match carve-out for `tail -<N> "$SPOOL"` shape at the top of the fragment loop.
5. `claude/.claude/hooks/tests/test_check_runner_bash_guard.py` — add the deny + carve-out test cases enumerated in §5 above.
6. `docs/case-studies/check-runner.md` — insert Incident 6 between Incident 5 and "Re-grounding."
7. `.claude/plans/check-runner-may-have-misbehaved-jiggly-pnueli.md` — copy this plan from `~/.claude/plans/` into the worktree's tracked `.claude/plans/` directory so it ships in the PR (this repo tracks plans per commit `4266653`). Do this in the implementation worktree before opening the PR.

## Verification

1. **Hook tests first** (smallest blast radius if wrong). Run `.venv/bin/pytest claude/.claude/hooks/tests/test_check_runner_bash_guard.py` after editing the hook + tests. Every new test case from §5 must pass, AND every pre-existing test case must still pass (no regressions on the git-allowlist, mutation-pattern, or project-layer arms).
2. **Skill behavior tests.** Invoke `/skill-review` on `subagent-delegation/SKILL.md` AND `ready-for-review/SKILL.md`. Invoke `/agent-review` on `check-runner.md`. Per `feedback_apply_behavior_test_beyond_trigger_list.md` and the CLAUDE.md "When editing a skill or agent, run the skill on its own diff" rule. Confirm: no length-budget regressions, no trigger-drift, edits read as operational guidance not narrative.
3. **Code review.** Run `/code-review` on the staged diff. It will dispatch `/skill-review`, `/agent-review`, and `/claude-hook-review` per the file types touched; doc-only edits (case study, plan) need no specialist.
4. **Full lint/tests.** `.venv/bin/ruff check claude/.claude/` and `.venv/bin/pytest claude/.claude/`.
5. **Hook smoke test against a real check-runner dispatch.** After install, dispatch check-runner with a deliberately-bad command shape (e.g. a probe that includes `cat .claude/agents/check-runner.md`) and confirm the hook returns HOOK_BLOCK with the expected stderr message. Also dispatch a normal `npm run verify`-style command and confirm the per-command template runs end-to-end (the carve-out doesn't false-deny the legitimate failure-excerpt shape).
6. **Redaction check.** Confirm `deny-private-project-refs.sh` passes on commit — Incident 6 prose uses generic terms only (no project, branch, migration, or function name).
7. **Manual contract-coherence read.** Read the modified files in sequence (`check-runner.md` → `subagent-delegation/SKILL.md` → `ready-for-review/SKILL.md` → `check-runner-bash-guard.sh` → case-study Incident 6) as if you were a contributor encountering check-runner for the first time. The dispatch-id contract should be unambiguous on every side; the read restriction should be discoverable from the agent body before you'd write a Bash call that triggers it.

**Enforcement coverage map.** Both new instruments have at least one machine-verifiable arm: the agent's refuse-without-dispatch-id check (runtime FAIL) and the hook's file-read deny (PreToolUse(Bash) decision, unit-tested). The parent-side "Doubting the verdict" recovery rule is the only unenforceable arm — no hook or test can observe parent reasoning. Acceptable as the unenforceable third leg; the other two foreclose most of the failure surface.

## Out-of-scope notes (raise, do not bundle)

- **Agent-side prose-drift fix.** Constrain the verdict return shape (JSON schema, fixed template, or remove failure tails from the agent's context analogous to Incident 5's silent-on-success). Genuinely a separate plan; bundling would expand scope and conflate the dispatch-identity instrument with the verdict-format instrument.
- **The parent's confidence-walking-back pattern** (declarative claim → walked back only under user pressure) is a sibling concern to "verify before asserting." Not warranted at n=1; if a second instance surfaces, consider a CLAUDE.md "verify before asserting subagent misbehavior" rule.
- **Descriptive doc retrofit.** Walking every passing mention of check-runner (README, design-decisions.md, hooks.md, transcript-analysis.md, etc.) to mention dispatch-id is out of scope. They describe check-runner's role in general terms and remain accurate without the dispatch-id detail. Touch them when their own contents next change.
