# Plan: show the approved plan's path deterministically, retire the plan-time draft PR

## Context

Goal: put the approved plan's absolute path in front of the engineer deterministically when `/plan-review` approves it, and retire the `plan-it/SKILL.md` instruction to open a draft PR at plan time, which the ready-for-review gate denies.

Ask: "I wanted the absolute path of the plan ready for me when it was approved by reviewer agents." / "All I want there is a way to see the plan so I can review it easily and give feedback - that's it." / "I only want the option to create a draft PR with the plan." / "I think we have to exclude the draft PR option entirely because it forces cumulative diff reviews earlier which is cost prohibitive. I'd like to record that in a durable location so neither my future self nor future agents relitigate it." — the last quote accepted via label "A is confirmed."

The engineer also said prose is not reliable enough for this, so delivery needs a hook, and asked that the `plan-it/SKILL.md` draft-PR conflict be taken in this plan.

## Approach

When `/plan-review` records an approval, `marker.sh write plan-review` prints the absolute path of every active plan file under `.claude/plans/` that the approval covers. A new PostToolUse hook, `announce-approved-plan-path.sh`, then shows those paths in the engineer's terminal as a `systemMessage`. `/plan-it` no longer opens a draft PR at plan time. Plans are shared by that path. For a design-doc or cross-team-contract plan, the session asks through `AskUserQuestion` whether to push the branch with no PR, pushes only on a yes, and gives the file's URL in its reply. A new decision record says why no PR is opened at plan time.

**What the engineer sees** (placeholder paths). In a main-session review the line appears beneath the `marker.sh` Bash call, prefixed `PostToolUse:Bash says:` (row 6):

- Normal: `Plan approved by /plan-review: /<repo>/.claude/plans/<slug>.md`. If more than one plan is covered, the paths are joined by `, `.
- Any path withheld: `Plan approved by /plan-review; a path is not shown (characters outside [A-Za-z0-9._/@+-]).` The message lists no paths in this case (row 37).
- Payload drift: `announce-approved-plan-path.sh: this Bash result carried no tool_response.stdout string, so the approved plan's path cannot be shown.` This line does not claim an approval.

**Where this departs from the session's design direction:**

1. **`systemMessage` only, with no `additionalContext`.** The model already receives `marker.sh`'s stdout as the Bash tool result (row 4), so `additionalContext` would repeat it. `announce-resume-command.sh` needs that second channel because a Write result does not carry the resume command. This case has no such gap.
2. **The trigger is wider than the exact pinned form.** It accepts every command shape the marker-shape gate allows that includes a `write plan-review` op. The shape gate allows `deactivate plan-review && write plan-review`, and SKILL.md puts those two fenced blocks next to each other. An exact-form trigger would fall silent on that chain, which row 3 rules out.
3. **The push is offered through `AskUserQuestion`.** Under autonomous shipping, a turn that ends with "want me to push…?" trips `advance-past-commit-stall.sh`. Its block reason tells the session to run `/ready-for-review` and open a PR, which is exactly the outcome this plan removes (row 21).
4. **The sharing paragraph moves from Step 6 to Step 7.** The push needs Step 7's commit, so the text now sits where the action happens.

**Alternatives set aside:**

- **Re-deriving the plan list inside the hook**, as the closed branch's cross-check did. It can differ from what the marker actually hashed, and the session's direction already rules out a derived list. Relaying `marker.sh`'s own output shows the set it enumerated right after hashing; a plan edited in the milliseconds between the two enumerations is announced but unhashed, and the gate recomputes the hash regardless, so only the announcement can be off.
- **The lighter "cumulative review only when not draft" gate tier.** The engineer confirmed option A only (row 18).
- The over-powered-primitive check below covers the rest.

**Assumption ledger**

**Root:** When `/plan-review` approves a plan, the engineer sees the plan's absolute path only if the model restates it. Separately, `plan-it/SKILL.md:124` tells the session to open a draft PR at plan time. Any open PR makes every later push need a `/ready-for-review` run at HEAD.

**Givens** (conditions beyond this design's reach):

- **G1.** `systemMessage` is the only hook output field that reaches the engineer's terminal, and no hook can rewrite text the model has already written. Claude Code's hook contract fixes this.
- **G2.** Bash tool stdout goes to the model as the tool result, not reliably to the engineer (row 4). The harness's rendering is vendor-owned.
- **G3.** Where a consumer's `~/.claude/hooks` is a real directory of per-file links, stow links a new hook file only when `./install.sh` is re-run, while a `settings.json` change goes live on `git pull`. Where `~/.claude/hooks` is one folded symlink into the checkout, the new file is live on pull. `CHANGELOG.md:198` states this conditional. The window is GNU Stow's mechanism, outside this change (row 31).

**Mechanisms** (each anchored):

- **M1 — `marker.sh write plan-review` prints `plan-review marker covers: <absolute path>`, one line per active plan file.** `anchors: row7`
  - It prints only in the repo arm, only after the marker write succeeds.
  - The repo arm re-runs `_lib_active_plan_files "$REPO_ROOT" "$PLAN_GATE_DIFF_BASE"`, with the same base the hash used, and joins each result onto `$REPO_ROOT/`.
  - A failed re-enumeration prints nothing and does not change the exit status.
  - The plan-mode arm is untouched (row 26).
- **M2 — New PostToolUse hook `claude/.claude/hooks/announce-approved-plan-path.sh`, matcher `Bash`.** It relays M1's lines as a `systemMessage`. `anchors: row3`
- **M3 — The hook emits `systemMessage` only.** `anchors: row4`
- **M4 — The trigger has two checks.** `anchors: row11`
  - `tool_name` is `Bash`.
  - `.tool_input.command` starts, after optional spaces or tabs, with the `~`-or-absolute `…/.claude/scripts/marker.sh` path and contains a `write plan-review` op. A command containing a newline never matches, because the shape gate's own trim does not strip a leading newline and so never shape-checks such a command.
  - The rest of the chain is not re-validated. `enforce-marker-script-shape.sh` already limits any single-line command starting with that path to allowed shapes. The hook header names that dependency.
- **M5 — Only lines starting with the exact M1 prefix are relayed, and each path must pass `_lib_passes_path_char_allowlist` and have the shape `marker.sh` produces: a `/.claude/plans/` segment and a `.md` suffix.** `anchors: row10`
- **M6 — A non-string `.tool_response.stdout` produces the drift line.** Zero prefixed lines produce silence. `anchors: row12`
- **M7 — A raw-stdin `case` check runs before `_lib.sh` is sourced.** Every ordinary Bash call exits there. `anchors: row33`
- **M8 — The hook is registered in its own new PostToolUse group with `"matcher": "Bash"`, with no `if` key and no `timeout`.** `anchors: row13`
- **M9 — `plan-it/SKILL.md` drops the l.124 draft-PR paragraph and gains a one-line "Sharing the plan" paragraph in Step 7.** `anchors: row19`
- **M10 — The push offer goes through `AskUserQuestion`.** `anchors: row21`
- **M11 — New decision record `docs/design-decisions/plans-shared-by-path-not-early-pr.md`.** `anchors: row18`
- **M12 — The hook is tested by subprocess only; no bash seam is extracted.** `anchors: row15`

**Over-powered-primitive check.** A hook on the `Bash` matcher spawns on every Bash call in every stow consumer's session, which is heavier than "print one line per approval". Five lighter options were weighed against M2:

1. **`marker.sh` stdout alone** (M1 with no hook). No registration and no per-call cost. It fails because the engineer did not see a plain Bash probe's output. `anchors: row4`
2. **Skill prose.** `plan-review/SKILL.md:277` already ends the review with the Step-1 absolute path. It fails because the engineer ruled prose out. `anchors: row3`
3. **A skill-frontmatter `hooks:` key on `plan-review`, scoped to the review run.** Genuinely lighter per call. Set aside for three reasons:
   - No `SKILL.md` in this repo declares `hooks:` (grep of `^hooks:` across `**/SKILL.md` finds none).
   - The closed branch's probe of that mechanism logged nothing under a delegated subagent.
   - It still needs the same script file, and it would add an unproven registration path to a hook-gated skill. `anchors: row6`
4. **Fold the relay into `redact-credential-values.sh`**, which already runs on PostToolUse `Bash`. No new process per call. Set aside because both outputs would share one JSON emit in a credential-redaction backstop, so a relay bug could drop that hook's `updatedToolOutput`. `anchors: row13`
5. **A harness `if` filter on the registration** (for example `Bash(*marker.sh write plan-review*)`), so the hook spawns only on matching calls. Genuinely lighter per call. Not adopted in this change: every `if` in `settings.json` today sits on a `PreToolUse` entry, so whether it applies to `PostToolUse` and matches `&&` chains and both path forms is unverified (row 40), and it would duplicate the in-hook match. The raw-stdin prefilter (M7) already settles ordinary calls cheaply. Revisit if the per-call cost measures badly. `anchors: row40`

**Assumption rows:**

1. `[verified: claude-skills/skills/plan-review/SKILL.md:277-293]` The review ends with the Step-1 absolute path, which the model restates in prose. `~/.claude/scripts/marker.sh write plan-review` (the `record-completion` fixture) runs only on **Approve** or **Approve with changes**, after the required changes are applied, and after `deactivate plan-review`.
2. `[engineer-verified: "I wanted the absolute path of the plan ready for me when it was approved by reviewer agents."]` The deliverable is the approved plan's absolute path, available at approval.
3. `[engineer-verified: "Prose is not good enough."]` Delivery must not depend on the model restating the path, so row 1's closing line does not count.
4. `[engineer-verified: "I have not seen" … "anywhere"]` A plain Bash `echo` probe's output did not reach the engineer. The probe's token is elided because it matches the tracker-ID redaction pattern. That the model did receive it is the session's own observation, not the engineer's claim.
5. `[engineer-verified: "we used a PostToolUse hook to make the resume command appear consistently and deterministically"]` A PostToolUse hook `systemMessage` already works for this engineer. `[verified: claude/.claude/hooks/announce-resume-command.sh:62-115]` That hook is the pattern to copy: three-line `_lib.sh` source, `_lib_jq`, a `_lib_jq -n --arg` emit, and silent exit 0 on failure.
6. `[unverified]` A PostToolUse `Bash` hook's `systemMessage` shows beneath the Bash call as `PostToolUse:Bash says: <text>`, and the model does not see it. In a delegated run it shows only in the subagent's own window. The claim is carried from an engineer-run spike recorded in a closed branch's plan (Claude Code 2.1.278), which no tracked file holds. The post-merge check in **Verification** is what verifies it.
7. `[engineer-verified: "I think it's a good idea but I don't know if the output of marker.sh reaches me."]` The engineer accepted the session's proposal that `marker.sh` print the path. The open question was reachability, which M2 answers.
8. `[verified: claude/.claude/scripts/marker.sh:467-516]`
   - The plan-mode arm hashes `$PLANMODE_TARGET`.
   - The repo arm hashes `_lib_active_plan_hash "$REPO_ROOT" "$PLAN_GATE_DIFF_BASE"`.
   - The shared marker write at 513-515 is the arm's last command, so a failed redirect is currently the script's exit status. Anything appended after it must not hide that status.
   - M1 adds output to the repo arm only (row 26).
9. `[verified: claude/.claude/hooks/_lib.sh:597-702]` `_lib_active_plan_files` prints repo-relative paths and exits 0; empty output means no active plan. On exit 1 it prints the plans directory itself, so a caller must discard that output.
10. `[verified: claude/.claude/hooks/_lib.sh:249-258]` `_lib_passes_path_char_allowlist` rejects empty input and any byte outside `[A-Za-z0-9._/@+-]`, under `LC_ALL=C`.
11. `[verified: claude/.claude/hooks/enforce-marker-script-shape.sh:602-676]` A command whose first token is the `~`- or absolute-path `marker.sh` must be one of three shapes, or it is denied:
    - one allowed shape;
    - an `&&` chain of allowed shapes, with an optional trailing ` 2>/dev/null`;
    - `write … && git commit`.

    `$HOME/…` and traversal forms are denied. Commands that do not start with the path fall through to `permissions.allow`.
12. `[unverified]` A Bash PostToolUse `tool_response.stdout` is a string holding the command's stdout.
    - Lead: `claude/.claude/hooks/tests/test_redact_credential_values.py:3,52` describes the shape as `{"stdout","stderr","exit_code"}`, "docs-confirmed". That is prose, not a behavior check, and no live hook reads the field.
    - M6 makes a wrong field name visible on the first approval instead of silent. **Verification** covers it before and after merge.
13. `[verified: claude/.claude/settings.json:443-506]` The PostToolUse groups are `Read`, `Bash|Read|WebFetch|Grep|Task` (redaction), `Agent|Task`, `Edit|Write|MultiEdit`, `AskUserQuestion`, and `Skill`.
    - None has a matcher of exactly `Bash`.
    - `announce-resume-command.sh` is registered with no `timeout` key.
14. `[verified: claude/.claude/hooks/tests/test_hook_alignment.py:152-176, 1300-1334]` Every hook needs its own `docs/hooks.md` bullet and a `# hook-class:` header. Only `gate` hooks need a tier line. Two further rules, which the hook-alignment tests enforce and the implementing session confirms by running them:
    - `_LIB_SOURCE_LINE_RE` accepts only the three-line source form.
    - Every `jq` must be `_lib_jq`, and bash `[[ =~ ]]` is outside `test_no_inline_command_matcher_regex`'s scope.
15. `[verified: .claude/rules/bash-unit-test-seams.md]` No valid seam exists for extracting the hook's matcher:
    - `_lib.sh` takes a helper only once two or more hooks need it.
    - A new `_*-lib.sh` under `claude/.claude/hooks/` fails CI.
16. `[verified: claude/.claude/hooks/require-ready-for-review.sh:365-392]` Three facts about when a push is gated:
    - A push of the default branch is exempt.
    - A push is ungated when `gh pr view --json number` returns nothing.
    - That check has no draft filter, so a draft PR gates pushes exactly as a ready PR does.
17. `[verified: claude/.claude/hooks/require-ready-for-review.sh:394-422]` With a PR on the branch, a push needs a `/ready-for-review` completion marker at HEAD. The deny text says that run includes a cumulative `/code-review` against the PR-vs-default-branch diff.
18. `[engineer-verified: "I think we have to exclude the draft PR option entirely because it forces cumulative diff reviews earlier which is cost prohibitive. I'd like to record that in a durable location so neither my future self nor future agents relitigate it."]` Confirmed by the engineer with "A is confirmed."
19. `[engineer-verified: "2. Confirmed"]` This confirms the session's item-2 proposal (session wording): for design-doc or cross-team-contract plans, keep the lead-time purpose but change the mechanism to "push the branch with no PR and share the plan file's URL".
20. `[engineer-verified: "Pushing on my yes is very convenient. I prefer that."]` The session pushes only on the engineer's yes.
21. `[verified: claude/.claude/hooks/advance-past-commit-stall.sh:144-149, 186-214, 230]` Under autonomous shipping, a turn whose last sentence matches `want me to|should I|…` together with `push`, with no exclusion word, blocks the turn from ending. It fires whenever work is pending, which includes a branch with no upstream after the plan commit. Its reason tells the session to run `/ready-for-review` and open the PR.
22. `[unverified]` An `AskUserQuestion` call is answered inside the turn, so the push question never becomes the turn-ending message the Stop hook reads. This is inferred from Stop firing at turn end. If it is wrong, the stall hook can still fire once for this question.
23. `[verified: session Step 3 exploration, local gh --help]` `gh browse <path> --branch <b> --no-browser` prints the file's URL.
24. `[verified: claude-skills/skills/ready-for-review/SKILL.md:151]` That line ("not `--draft`") conflicts with plan-it l.124 today. Removing l.124 resolves the conflict, and this plan leaves line 151 unchanged.
25. `[engineer-verified: "I think you should take it because it's adjacent to what you're already planning."]` The plan-it l.124 conflict is in scope here.
26. `[engineer-verified: "Drop it (Recommended)"]` `marker.sh`'s plan-mode arm is left untouched and prints no path, so an approval recorded in harness plan mode announces nothing. The label answered whether the plan-mode arm should also print its target. That option's description was the session's proposal.
27. `[engineer-verified: "Don't raise the plan mode bug. I don't use plan mode."]` The engineer does not use plan mode, and this plan raises no plan-mode issue.
28. `[verified: docs/skills.md:143]` The skill length cap is 200 lines, and `plan-it/SKILL.md` is 146 lines. M9's rewrite is net zero lines.
29. `[verified: claude-skills/skills/tests/test_skills.py:5193-5225]` Step 7's heading and its cannot-resolve fallback clause are pinned by tests. M9 inserts a paragraph and leaves both unchanged.
30. `[verified: .claude/rules/design-decisions.md; claude/.claude/hooks/tests/test_design_decision_files.py:69-84, 466]` A decision file needs a slug filename, exactly one H1, and an italic date line at line 3 with no `Formerly` clause. Its relative links must resolve to files that exist.
31. `[verified: claude/.claude/hooks/require-stow-reminder.sh:4-36]` A file added under `claude/.claude/` makes `gh pr create` require the PR body to mention `install.sh` or `stow`.
32. `[verified: README.md:158-184]` The README hook table is not exhaustive: `announce-resume-command.sh` has no row. So this change adds no README row.
33. `[unverified]` A raw-stdin `case` exit costs about 3 ms per call, against about 29 ms for sourcing `_lib.sh` and running two `_lib_jq` parses. This is an estimate from a microbenchmark on another hook body (bash 5.2, Linux), not a measurement of this hook, and says nothing about bash 3.2. The `case` uses a multi-star glob over the whole payload; its cost on a payload with many `marker.sh` mentions and no `plan-review` is unmeasured.
34. `[verified: claude/.claude/hooks/tests/test_marker_script.py:538-555, 2305-2333; claude/.claude/hooks/tests/conftest.py:42]` `marker.sh`'s tests live in `hooks/tests/`, and `_seed_session` is in `conftest.py`. The only empty-stdout assertions are the `resolve-session-id` tests. No test pins `write plan-review` as printing nothing.
35. `[engineer-verified: "In the session's reply (Recommended)"]` After a push, the plan file's URL reaches the engineer in the session's reply, taken from `gh browse … --no-browser` output. The hook is not widened to relay it. The label answered how the URL should reach the engineer. That option's description was the session's proposal.
36. `[engineer-verified: "Yes, one entry (Recommended)"]` One `CHANGELOG.md` `[Unreleased]` entry ships with this change.
37. `[engineer-verified: "Merge the two withheld forms (Recommended)"]` The hook emits one "a path is not shown" form for any withheld path, listing no paths, instead of two. The label answered whether to keep all four message forms. That option's description was the session's proposal.
38. `[engineer-verified: "Drop it (Recommended)"]` The plan-it paragraph and the decision record carry no amend-and-force-push rule. The label answered whether to keep that sentence. That option's description was the session's proposal.
39. `[engineer-verified: "Yes, my words (Recommended)"]` The first three `Ask:` quotes are the engineer's words, confirmed this session.
40. `[unverified]` A harness `if` filter applies to a `PostToolUse` hook entry, and matches `&&` chains and both the tilde and absolute path forms. Every `if` in `claude/.claude/settings.json` today (lines 240-297) is on a `PreToolUse` entry.

## Critical files

All paths are relative to the worktree root. There are two `code-writer` dispatches, run **in sequence**. The file sets don't overlap, but Dispatch B's decision record links to the hook file Dispatch A creates, and `test_relative_links_resolve_to_existing_files` fails until that file exists.

### Dispatch A — relay mechanism

**Modify `claude/.claude/scripts/marker.sh`** (`write plan-review` arm, 467-516):

- Add a module-level constant `PLAN_REVIEW_COVERED_PATH_PREFIX='plan-review marker covers: '` with a one-line paired-literal comment naming `announce-approved-plan-path.sh`.
- Hold the covered-path list as a newline-delimited string, never an array: an empty `"${arr[@]}"` aborts under `set -u` on bash 3.2, after the marker is already written. Set it empty before the plan-mode `if` and fill it only in the repo (`else`) branch:
  - call `_lib_active_plan_files "$REPO_ROOT" "$PLAN_GATE_DIFF_BASE"`;
  - discard its stdout on non-zero status;
  - otherwise join each path onto `$REPO_ROOT/`.

  Never abort on this step. Leave the plan-mode branch as it is.
- Change the shared marker write to `printf … > "$marker" || exit`. A bare `exit` keeps the failed redirect's own status (row 8).
- Then print one `"$PLAN_REVIEW_COVERED_PATH_PREFIX$path"` line per path in a `while IFS= read -r` loop fed by a here-string (`done <<< "$paths"`), not a pipe, which would run the loop in a subshell. The arm must still end with exit 0 after a successful write, so don't let a trailing `[ -n … ] && printf` leak status 1.
- Reuse `_lib_active_plan_files`. Add no `_lib.sh` helper.

**Modify `claude/.claude/hooks/tests/test_marker_script.py`.** Add tests next to `test_write_plan_review_stores_hash_not_literal`:

- One untracked plan: exit 0 (asserted explicitly), and stdout is exactly the prefix plus `git_toplevel(repo)` + `/.claude/plans/p.md`, followed by a newline.
- Two plans named so C order and locale order differ (for example `B.md` and `a.md`): two lines, in `LC_ALL=C` sort order.
- No `.claude/plans/`, and a plan that is committed and unmodified: exit 0 and empty stdout.
- The existing unreadable-plan abort test also asserts no prefix line.
- A failed marker write (unwritable marker directory, skipped when running as root, following the precedent near `test_marker_script.py:558-590`): non-zero exit and no prefix line.
- A declared plan-mode target plus an untracked repo plan: empty stdout.
- The printed paths, run through the file's existing plan-hash oracle, reproduce the marker file's content.

**Create `claude/.claude/hooks/announce-approved-plan-path.sh`** (mode 100755; the Write tool creates 0644, so set the mode and let the test execute the file directly):

- Line 2: `# hook-class: informational`. No tier line.
- Header comment, one fact per sentence:
  - what it relays and that it uses `systemMessage` only;
  - why not `additionalContext`: the Bash result already carries the stdout;
  - the trigger, and that it relies on `enforce-marker-script-shape.sh` for the rest of the command's shape;
  - the paired literal with `marker.sh`;
  - the fail posture: silent, exit 0 on every path;
  - the defense-in-depth line, which names the dependency on `enforce-marker-script-shape.sh` for the shape of single-line commands that start with the `marker.sh` path;
  - that the message claims only that a marker was recorded;
  - that this group runs in parallel with the redaction group that also matches `Bash`, with no ordering between them, and the two emit different fields;
  - the canonical `Known gaps:` list:
    - an approval whose active plan set is empty (the plan is committed and unmodified, or lives outside `.claude/plans/`) announces nothing;
    - an approval recorded in harness plan mode announces nothing;
    - a delegated review shows the line only in the subagent's window;
    - a withheld path is never shown, and any withheld path suppresses the whole list;
    - a hand-run `write plan-review` announces as an approval;
    - a home or repo path containing a space or other character outside the trigger's path class announces nothing;
    - a Bash output that mentions both `marker.sh` and `plan-review` passes the raw-stdin prefilter and pays the `jq` cost before the trigger rejects it;
    - whether PostToolUse fires on a non-zero exit is unverified, but `marker.sh` prints paths only after a successful write, so a failed write never announces a path.
- Body, in order:
  1. `set -uo pipefail`, then `INPUT=$(cat) || exit 0`.
  2. `case "$INPUT" in *marker.sh*plan-review*) ;; *) exit 0 ;; esac`, with a one-line comment that this check settles ordinary Bash calls before `_lib.sh` is sourced.
  3. The three-line `_lib.sh` source form from `announce-resume-command.sh:62-64`.
  4. `.tool_name` via `_lib_jq`, accepting only `Bash`. Every `_lib_jq` call in steps 4-7 ends `|| exit 0`, as `announce-resume-command.sh` does, so a `jq` failure under `pipefail` exits silently and never falls through.
  5. `.tool_input.command` via `_lib_jq`. A command containing a newline exits 0 silently. Otherwise test it with two `[[ =~ ]]` checks. Store each pattern in a variable and expand it unquoted, since a quoted pattern is a literal match (`advance-past-commit-stall.sh:151-153`). Use POSIX classes only:
     - `^[[:blank:]]*(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]`
     - `marker\.sh[[:space:]]+write[[:space:]]+plan-review([[:space:]]|&|$)` — the `&` covers the gate-allowed `plan-review&&…` spelling.
  6. Read the payload's stdout type via `_lib_jq`, for example `(.tool_response | if type == "object" then (.stdout | type) else "none" end)`, so a `tool_response` that is a bare string or array reaches the drift branch instead of a jq error. Anything but `string` emits the drift line.
  7. Read the stdout through `_lib_jq -r` and loop `while IFS= read -r` fed by a here-string, so the loop runs in the current shell and its accumulated state survives. Keep only lines that begin with the exact prefix, strip the prefix, and check each path with `_lib_passes_path_char_allowlist` and the M5 path shape. Note whether any path failed either. Build the list by string concatenation joined with `, `, not by array expansion: an empty `"${arr[@]}"` aborts under `set -u` on bash 3.2.
  8. Zero prefixed lines: exit 0 silently. Any path failed the allowlist: emit the single "a path is not shown" form and list no paths. Otherwise emit the normal form from **Approach**.
  9. Emit with `_lib_jq -n --arg msg … '{systemMessage: $msg}' 2>/dev/null || true`, then `exit 0`.

**Create `claude/.claude/hooks/tests/test_announce_approved_plan_path.py`.** Module-local helpers:

- a payload builder taking `tool_name`, `command`, and `tool_response`;
- a raw runner modeled on `test_announce_resume_command.py:41-59` that asserts `returncode == 0` itself;
- `_message(result)`, which asserts the output keys are exactly `{"systemMessage"}` (or that stdout is empty) and that the message fully matches `[ -~]*`.

Positive trigger commands start from `extract_skill_command(<plan-review SKILL.md>, "record-completion")`, never a hardcoded literal. Test groups:

1. **Registration:** `settings.json` registers the hook under `PostToolUse`, in a group whose matcher is exactly `Bash`, and nowhere under `PreToolUse`.
2. **Trigger positives:** the extracted tilde form, an absolute prefix, leading spaces before the path, `write plan-review 2>/dev/null` alone, `deactivate plan-review && write plan-review`, the same chain with a trailing ` 2>/dev/null`, `write plan-review&&…`, and `write plan-review && git commit -m x`.
3. **Trigger negatives**, each fed stdout that does carry a prefixed line, each expecting no output:
   - `tool_name` `Write` or `Read`;
   - `grep -rn "marker.sh write plan-review" docs/`;
   - `…marker.sh write code-review`;
   - `…marker.sh deactivate plan-review` alone.
4. **Relay:** non-prefixed lines (git commit output) are ignored, a prefix in mid-line is ignored, and two prefixed lines are joined.
5. **Withheld form:** one fail case (an invalid path plus a valid sibling) produces the "a path is not shown" line with no path bytes, and one pass case. The byte-class matrix itself lives in `test_lib_path_char_allowlist.py`. Also one case for a path outside `/.claude/plans/` or without a `.md` suffix, one for a prefix line with an empty path, and one end-to-end case with the repo under a directory whose name contains a space.
6. **Drift and silence:** `tool_response` with no `stdout`, a null `tool_response`, a bare-string `tool_response`, and an object-valued `stdout` each produce the drift line. `{"stdout": ""}` and a failed-write payload (`stdout` empty, non-empty `stderr`, non-zero `exit_code`) stay silent.
7. **End-to-end pairing:** in `git_repo` with `isolated_home` and a seeded session (conftest's `_seed_session`), run the real `marker.sh write plan-review`, put its stdout into the payload, and assert the message names `git_toplevel(repo)/.claude/plans/p.md`. This pins the paired prefix literal.
8. **Fail-open:** malformed JSON containing both prefilter tokens exits 0 with no output. A hook copy with no adjacent `_lib.sh` exits 0, paired with a positive control after `symlink_hooks_lib_chain`.
9. **Prefilter position:** an ordinary Bash payload never spawns `jq` (a `PATH` stub, following `test_announce_resume_command.py:445-464`), and a non-Bash `tool_name` carrying an otherwise-triggering command stays silent.

The hook runs as an executed file, not through `bash <path>`, so the exec bit is pinned. Import `_seed_session` as `from .conftest import _seed_session`; a bare `from conftest import` fails the packaging test.

*Reuse:* `isolated_home`, `git_repo`, `git_toplevel`, `extract_skill_command`, `install_marker_script`, `symlink_hooks_lib_chain`, and `_seed_session`.

**Modify `claude/.claude/settings.json`.** Add a new PostToolUse group `{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/.claude/hooks/announce-approved-plan-path.sh"}]}` after the redaction group. Add no `if` key and no `timeout`. `ask-review-permissions.sh` will ask on this edit.

**Modify `docs/hooks.md`.** Add a `## Utility hooks` bullet after `announce-resume-command.sh`, in the form ``- **`announce-approved-plan-path.sh`** (PostToolUse, `Bash`, informational) — …``. It should state:

- the trigger shapes, and that it fires after the command ran;
- that it relays `marker.sh`'s `plan-review marker covers:` lines;
- the message forms;
- `systemMessage` only, and why;
- the allowlist, with a pointer to `_lib.sh`;
- the drift line's purpose;
- the prefilter;
- never blocks;
- no kill switch other than removing its `settings.json` entry.

Point to the hook header for Known gaps rather than restating them.

**Modify `docs/scripts.md`.** Add one sentence to the `marker.sh` bullet (line 123): after a successful `write plan-review`, it prints `plan-review marker covers: <absolute path>` for each file in the active `.claude/plans/` set it hashed, which `announce-approved-plan-path.sh` relays (link `hooks.md`).

**Modify `CHANGELOG.md`** (engineer-confirmed, row 36). It sits in this dispatch because the consumer-visible surface (hook file, registration) is here. Add one `## [Unreleased]` → `### Changed` entry saying three things:

- `/plan-review` approval now shows the approved plan's absolute path, through `marker.sh`'s new output and `announce-approved-plan-path.sh`.
- `/plan-it` no longer opens a draft PR at plan time (link the decision record).
- Where `~/.claude/hooks` is a real directory rather than one symlink into the checkout, re-run `./install.sh` after pulling so the new hook file is linked. Use the wording `CHANGELOG.md:198` already uses for this conditional.

### Dispatch B — plan-it and decision record (after A lands)

**Modify `claude-skills/skills/plan-it/SKILL.md`.**

- Delete line 124 (the **Draft-PR handoff…** paragraph) and one adjacent blank line.
- In Step 7, between the "If it names none" paragraph (ending "…and there is none.") and "Then choose the session.", insert this as one unwrapped line plus a blank line, so the file stays at 146 lines:

  > **Sharing the plan.** Share the plan with the engineer by the absolute path `/plan-review` showed when it recorded approval; never open a PR, draft or ready, at plan time. If the plan adds a design document or defines a cross-team contract (a schema shape, enum, or API surface other teams or pipelines depend on), readers outside this session may need lead time: ask the engineer through `AskUserQuestion`, not a closing chat question, whether to push the branch with no PR. The question names the remote URL, whether that repository is public, and the commits the push would publish; no answer means no push. Ask only when the branch is not the default branch and `gh pr view` finds no PR for it (a non-zero exit counts as no PR), since a push to a branch with a PR needs `/ready-for-review` first. After a yes and the push, give the plan file's URL from `gh browse <repo-relative plan path> --branch <branch> --no-browser`, and give other readers that URL, not the local path.

- Leave Step 7's heading and fallback clause untouched (row 29).

**Create `docs/design-decisions/plans-shared-by-path-not-early-pr.md`.** Draft text below. It contains no cost figure and no prior-version narration, and its relative links resolve from `docs/design-decisions/`:

```markdown
# Plans are shared by path, not by a PR opened at plan time

*2026-09-30.*

`/plan-it` opens no pull request, draft or ready, when it produces a plan. The engineer reads the plan at its absolute path, which [`announce-approved-plan-path.sh`](../../claude/.claude/hooks/announce-approved-plan-path.sh) shows when `/plan-review` records approval. When readers outside the session need the plan before implementation starts — a new design document, or a contract other teams depend on — the session pushes the branch with no PR, only on the engineer's yes, and shares the plan file's URL.

**Why no PR at plan time.** [`require-ready-for-review.sh`](../../claude/.claude/hooks/require-ready-for-review.sh) gates every `git push` to a branch that has a PR, and it finds that PR with `gh pr view` without filtering out drafts, so a draft PR gates pushes exactly as a ready one does. A gated push needs a `/ready-for-review` run at the pushed HEAD, and that run includes a cumulative `/code-review` of the whole branch against the default branch. A PR opened at plan time therefore moves that review from hand-off to every implementation push. A push to a branch with no PR is not gated, so pushing the plan without one gives other readers the same file without the repeated review. The hook's own header lists the gated commands and their exemptions.

**Why the push is asked through `AskUserQuestion`.** Under autonomous shipping, [`advance-past-commit-stall.sh`](../../claude/.claude/hooks/advance-past-commit-stall.sh) treats a turn that ends by asking whether to push as a stall, and tells the session to continue through `/ready-for-review` to opening a PR, which is the outcome this decision rules out. A question asked through the tool is answered before the turn ends.

This decision covers plan time only. When `/ready-for-review` opens the PR, and in which state, is that skill's concern.
```

### Also in the diff

`.claude/plans/plan-share-links.md` (this plan).

## Verification

From the worktree root (a linked worktree has no `.venv`, so use the main checkout's):

```bash
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude/.claude/ claude-skills/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

These must go green by name:

- `test_announce_approved_plan_path.py`
- `test_marker_script.py`
- `test_hook_alignment.py`: docs coverage, `hook-class` header, `_lib.sh` source checks, bare `jq`, `\s`, and the inline matcher detector
- `test_enforce_marker_script_shape.py`
- `test_require_plan_review.py`
- `test_skills.py`: the plan-it Step 7 pin, citation grammar, and length
- `test_design_decision_files.py`: filename, H1, provenance line, and relative links

If `select-tests.py`'s selection leaves one of these out, report it as a rule-table bug in `select-tests.py`; don't widen the run by hand.

Checks no test covers:

- `git diff origin/main --stat` lists exactly the Critical files.
- `grep -n 'Draft-PR handoff' claude-skills/skills/plan-it/SKILL.md` prints nothing.
- `wc -l claude-skills/skills/plan-it/SKILL.md` prints 146.

Pre-merge evidence for row 12, which settles nothing on its own:

- Read the keys of one Bash `toolUseResult` from the implementing session's own transcript JSONL, e.g. `jq -c 'select(.toolUseResult.stdout? != null) | .toolUseResult | keys' <transcript>.jsonl | head -1`.
- Record the key list in the PR body.
- This shows the tool-result object's shape. It does not prove the hook payload's shape.
- Ask the engineer whether the earlier spike's hook stub logged its stdin. If it did, that log already holds a real PostToolUse `Bash` payload and settles row 12 before merge.

Reviews:

- `/code-review`. It requires `/skill-review` for `plan-it/SKILL.md`, and that requirement is hook-enforced.
- `claude-hook-review:claude-hook-review` on the new hook and its `settings.json` registration.

PR body, which is a requirement rather than a check:

- Mention `install.sh`, or `require-stow-reminder.sh` denies `gh pr create` (row 31). The same sentence tells consumers whose `~/.claude/hooks` is a real directory to re-run `./install.sh` after pulling, because until then the `settings.json` registration names a script that is not linked yet (G3). Carry over the `CHANGELOG.md:198` wording. Note that the Bash-matcher reachability in row 6 is carried from an earlier spike.
- State the known limit: an approval of a committed, unmodified plan announces nothing, and delegated reviews show the line only in the subagent's window.

Post-merge, run once by the engineer. `claude/` is stowed from the main checkout, so the hook does not fire in this worktree's sessions:

1. Run `git pull`, then `./install.sh`, and confirm `~/.claude/hooks/announce-approved-plan-path.sh` resolves and is executable.
2. Run one `/plan-review` to approval in the main session.
3. Check the outcome:
   - The `Plan approved by /plan-review: <path>` line names the reviewed plan: done.
   - The drift line appears: the Bash `tool_response` field name differs from row 12. Fix the hook's field name. Do not revert.
   - Nothing appears: read the transcript for the command the model actually ran, and check it against M4's two patterns before changing anything.
4. Stop rule: if one triage pass does not make the announcement appear, remove the hook's `settings.json` group and keep the rest. That group is the smallest rollback unit, live on `git pull` with no install step. The `marker.sh` output, the `plan-it` paragraph and the decision record are independent of it and harmless.
5. Row 22 is not exercised by this run. The first time a plan push is offered under autonomous shipping, watch whether `advance-past-commit-stall.sh` fires on the `AskUserQuestion` answer.

## Out of scope

- **Opening PRs as draft and having the engineer flip them to ready after approval.** Separate follow-up plan, whose Ask is the engineer's: "Open PRs as draft, and I flip them to ready once I've approved, so teammates know it's ready for review." It reverses `ready-for-review/SKILL.md:151`, which this change leaves untouched.
- **The lighter "cumulative review only when not draft" gate tier in `require-ready-for-review.sh`.** Not adopted; the engineer confirmed option A only (row 18).
- **Plan-mode behavior.** `marker.sh`'s plan-mode arm is untouched, so an approval recorded in harness plan mode announces nothing (rows 26 and 27).
- **Delivering the post-push URL through the hook.** Decided: the URL reaches the engineer in the session's reply (row 35).
- **Changing `advance-past-commit-stall.sh`'s phrasing match** so a closing push question stops tripping it. `AskUserQuestion` avoids the question instead of widening that hook's exclusions.
- **Announcing a plan when the approval covers an empty active set**, i.e. a committed, unmodified plan. Recorded as a Known gap in the hook header.
- **Extracting the trigger into `_lib.sh` or a `_*-lib.sh` sidecar.** No valid seam exists today (row 15).
- **A README hook-table row.** The table is not exhaustive (row 32).
- **A `test_skills.py` tripwire asserting `plan-it` carries no plan-time PR instruction.** The decision record is the durable location the engineer asked for, and a source-text tripwire risks scope growth. Add one only if the engineer asks for it.
- **A harness `if` filter on the hook registration.** Weighed as lighter option 5 and not adopted, because `if` on a `PostToolUse` entry is unverified (row 40).
- **The verdict word, and path announcements for other gate-releasing skills** (`/code-review`, `/ready-for-review`).
