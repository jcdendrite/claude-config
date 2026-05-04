---
name: verification-before-completion
description: >
  Run verification commands before claiming work is done, tests pass,
  or a bug is fixed. Covers what counts as evidence, which claims
  require which commands, and the gap between "it should work" and
  "it does work."
  TRIGGER when: about to state that tests pass, a build succeeds,
  a bug is fixed, or work is complete; about to commit or open a PR;
  about to delegate to a sub-agent and hand off on the assumption work
  is finished.
  DO NOT TRIGGER when: verification commands have already run in this
  message and output was checked; the user is asking about strategy or
  design rather than work state.
user-invocable: false
---

# Verification Before Completion

## The rule

Run the verification command, read the full output, check the exit
code. Then state the result. In that order — not the other way around.

A claim made before running the command is a guess. Running the
command after the claim is rationalization. Neither substitutes for
evidence.

## What each claim requires

| Claim | Required evidence | Not sufficient |
|-------|------------------|----------------|
| Tests pass | Test command output showing zero failures | A prior run; "should pass"; the code looks correct |
| Build succeeds | Build command exits 0 | Linter passing; no type errors found |
| Linter clean | Linter output showing zero errors | Partial check on changed files only |
| Bug fixed | The original failure case now passes | Code changed; symptom not re-triggered |
| Regression test works | Red → green cycle observed (see TDD rules) | Test written but never run |
| Agent work complete | VCS diff shows expected changes; verification commands pass | Sub-agent reported success |
| Requirements met | Checklist against the spec, item by item | Tests pass, therefore done |

## Phrases that require verification first

Before writing any of the following, run the relevant command and
include its output:

- "tests pass" / "all passing" / "green"
- "build succeeds" / "compiles"
- "fixed" / "resolved" / "addressed"
- "works" / "working now" / "done"
- "complete" / "finished" / "ready"
- Any positive statement about work state

Paraphrases count. Implying completion without saying it counts.

## Common rationalizations — and why they fail

| Rationalization | Why it fails | Required action |
|---|---|---|
| "The change is trivial — just a rename" | Renames break callers, import paths, serialized keys. "Trivial" edits have caused production incidents. | Run the full test suite. |
| "I only changed a constant" | Constants set limits, defaults, and thresholds — changing one can alter behavior across the whole system. | Run tests and check every reference. |
| "Tests passed before my edit" | State changes between runs. The suite that passed 20 turns ago ran against different code. | Re-run the suite after every substantive edit. |
| "The logic is obviously correct" | Correctness by inspection has a systematic blind spot: the same reasoning gap that produced the bug obscures it on review. | Run the command. Obvious isn't evidence. |
| "Only one test is failing and it's unrelated" | Confirm it. "Unrelated" test failures are often symptoms of a shared dependency that the current edit broke. | Fix all failures or stash your changes and rerun to prove the failure predates your edit. |
| "I'll run tests before committing" | The claim happens now. The commit gate (claude-config's `code-review` hook) catches the commit, not the chat claim. | Run now, before stating the result. |

## Verifying sub-agent work

A sub-agent's success report is not evidence. Before claiming an
agent completed its task:

1. Check the VCS diff — did the expected files change?
2. Run the verification commands yourself.
3. State what the commands showed, not what the agent reported.

## When verification fails

If the command shows failures, state the actual output. Do not:

- Report partial pass counts as success
- State that "most tests pass"
- Attribute failures to environment without checking
- Re-run the command hoping for a different result

Fix the failures, then re-run the full command, then report the result.

## When verification cannot run

Some verification requires infrastructure, accounts, or conditions that aren't available in the current session. When that's the case:

- **State what's owed, not what's done.** Describe the change and name the verification step explicitly: "This change requires a full CI run — I cannot verify locally that the deploy pipeline succeeds."
- **Do not claim partial verification as full verification.** "Unit tests pass" is not "the feature works end-to-end."
- **Do not defer silently.** The user should never discover unverified claims only when something breaks.

Common cases:
| Scenario | What to say |
|---|---|
| CI-only checks (deploy pipeline, integration suite) | "Verified locally; CI must confirm deploy behavior before this ships." |
| Manual UI verification | "Logic tested via unit tests; visual/interaction behavior needs manual browser check." |
| Paid or rate-limited external APIs | "Mocked in tests; live call needs manual verification in staging." |
| Cross-service behavior | "Service A tested in isolation; end-to-end behavior through Service B unverified." |

## Scope: full suite, not just changed files

Running tests only on the files you changed is not verification that
the suite passes. Run the full suite. If that's too slow to run on
every change, note that explicitly and run the full suite before
committing.

## Relationship to the commit gate

claude-config's `code-review` PreToolUse hook blocks `git commit` until `/code-review` runs and produces a marker. That gate operates at the commit boundary: no commit lands without review.

This skill operates earlier, at the chat boundary: no completion *claim* is made without fresh evidence in the current message. The two gates are complementary and cover different surfaces — the hook can't catch a claim made 10 messages before the commit; this skill catches it there instead.
