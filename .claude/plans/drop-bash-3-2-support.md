# Plan: drop macOS system bash 3.2 support

## Context

Goal: drop support for macOS system bash 3.2 so the repo's shell code can rely on a modern bash floor, removing a constraint that taxes every new script and hook.

Ask:
1. [engineer-verified: "i want to remove support for bash 3.2"]
2. [engineer-verified: "MacOS is still supported but will have to install later bash versions via homebrew. I'm pretty sure that [private provisioning repo] does that. And yes simplify existing code and unlock bash 4+ features"] The bracketed text replaces a repository name removed for redaction.
3. [engineer-verified: "supportijg that old version is causing needlessly extra complexity. im open ti an adversarial review of that"]

Why now: the engineer judges the 3.2 constraint to be "needlessly extra complexity". Intended outcome: macOS stays supported, with users installing a newer bash via Homebrew. The floor is enforced at runtime for every hook that sources a `_lib.sh`, for `install.sh`, and for 25 of the 34 scripts under `claude/.claude/scripts/`. For the rest it is documented only (row 84). This repo depends on no other repository, so README.md and install.sh state the prerequisite themselves.

Clarifying answers (engineer's words; option descriptions were the session's proposals):
- macOS handling: selected "Require Homebrew bash (Recommended)".
- Guard test: selected "Replace with version floor (Recommended)".
- Floor: "I think the latest version but I'm open to alternative rationales", then "Is 5.1 also supported on macOS with homebrew and wsl?", then selected "5.1 (Recommended)". After plan-review round 2, selected "5.2, CI unchanged (Recommended)".
- Plugins (round 1): selected "Include all four (Recommended)".
- Rewrite scope: "Not sure. Ask fable on this one. I'm inclined to keep things as they are and just reduce complexity for future implementation"
- `onFailure`: "Not sure, ask the architect. I'm totally fine raising the minimum Claude code version as well for simplicity"
- M3 scope (plan-review round 1): "Have the architect do an adversarial review against your proposal"
- Runtime guard timing (plan-review round 1): "Ask the architect"
- Plugins (plan-review round 1): selected "All four, major bump".
- CI parity: "Let's have CI match. Either raise to 5.2 or drop CI to 5.1"
- Narrowed scope (plan-review round 1 follow-up): asked "Does fable agree with that?", then selected "Adopt with Fable's changes (Recommended)".
- Plugins under the deferred design: asked "Does fable agree with doing that as a follow up?", then selected "Follow-up (Recommended)".
- Reversal: "Wait wait wait fable's rationale hinges on "gives existing users an install-time signal before the guard arrives"??? If so, that's not a good reason. I have like only [count omitted] users of this repo." Then selected "Yes, full removal (Recommended)". The bracketed text replaces a count removed for redaction.
- Runtime floor check (plan-review round 2): selected "Keep M2 (Recommended)".
- `onFailure` exit-0 audit (plan-review round 2): selected "Keep the audit (Recommended)".
- Redactor gap (plan-review round 2): selected "Make redactor fail loud (Recommended)".
- Quote check (plan-review round 2): selected "Yes, they stand".
- Plugin consumers (after plan-review round 2): selected "All among the [count omitted]". The bracketed text replaces a count removed for redaction.
- `require-ready-for-review.sh` fail-open reversal (M4): selected "Accept (Recommended)".
- New deliverables (the M9 `subprocess.run` wrapper, the `install.sh` floor test, `.claude/rules/shell-floor.md`): selected "Approve all (Recommended)".
- M6 comment sweep (plan-review round 3): selected "Keep the sweep (Recommended)".

Adversarial review (subagent view, session-commissioned at the engineer's request): the premise holds in part. The cost is real but modest, and the 3.2 support was largely never verified on a real 3.2 binary. Much of the macOS burden is BSD userland and stays regardless. The main new risk is a GUI-launched Claude Code whose PATH lacks Homebrew, which `#!/usr/bin/env bash` would resolve to `/bin/bash` 3.2. Gates would then fail open unless a runtime guard catches it.

Plan-review round 1 returned Request changes. Five reviewers converged on the hook-side runtime guard with distinct failure modes, so the architect re-examined the guard's foundation (M8). Plan-review round 2 returned Request changes. Plan-review round 3 returned Approve with changes from all six reviewers, with no blocker. This revision applies its findings and the engineer's round-2 answers (rows 74-80) and later answers (rows 88-91).

## Approach

Bash 3.2 is dropped everywhere: hooks, plugin hooks, scripts and `install.sh`. Every shipped bash file moves to `#!/usr/bin/env bash` and may use bash 5.2. A version check sits at the very top of `_config.sh` and of each plugin's `_lib.sh`. On an older bash it makes every gate deny through its existing bootstrap. `onFailure: "block"` goes on every gate registration. A gate that cannot start, exits other than 0 or 2, prints invalid output or times out then blocks the call instead of allowing it. Users learn the cause in four ways:
- `./install.sh` prints a concrete, platform-specific fix (macOS or Linux, chosen by `uname -s`) below the floor. This is the primary help. On success it names the bash it used and warns that Claude Code's own PATH can differ.
- Plugin gate denies carry the literal fix.
- Below the floor, the credential redactor reports a hook error on each result instead of going silent.
- Users are told directly before merge.

(Paths are relative to the repository root.)

Alternatives weighed and set aside:
- **The round-2 deferred guard, as stage 1 of a staged rollout.** The engineer rejected the rationale behind it (row 51) and selected full removal (row 52).
- **`onFailure` alone, with no runtime version check.** This was the session's proposal (row 53). It misses bash-4 builtins and options that fail as a single command without ending the hook (row 61), so a gate could allow silently. A below-floor user would also see nothing until such code lands in a hook.
- **A runtime check alone, with no `onFailure`.** This leaves `env` finding no bash as a silent fail-open that the shebang change adds (ciso-reviewer S1, finding 2). It would also need a bash-3.2 lint over the region above every gate's bootstrap.
- **A new `_bash-floor-lib.sh` (round 2's home).** Under `claude/.claude/hooks/` it fails CI's hook sweep (row 47). Under `claude/.claude/scripts/` it would make hooks source a scripts file for the first time.
- **A per-gate deny channel naming the floor (round-1 M3).** It needs edits to about 30 bootstraps in about ten wordings. M3 reaches the cause without touching any bootstrap.
- **Running hooks as `[bash, hook]` in the test runners, so curated PATHs need no bash.** This drops the tests' coverage of shebangs and executable bits. The repo's precedent is to link bash into the curated directory instead: 19 test sites already do (row 59).
- **Floor 5.1 with CI moved to `ubuntu-22.04`.** The engineer selected 5.2 with CI unchanged (row 79), and that image is near end of life (row 35). **Floor 5.3** excludes CI's bash. **Floor 4.4** fails row 32.
- **A Claude Code version check in `install.sh`.** `install.sh` does not run on `git pull` (G5), and a fresh install already gets a current CLI.
- **Rewriting every 3.2 workaround.** This goes against row 8.
- **A session-start notice, or a visible failure in every non-gate hook.** The engineer dropped the notice (row 73). The engineer chose a visible failure for the credential redactor only (rows 76-77), the one non-gate hook that guards channels left open below the floor.
- **Patching the eight `run_hook*` runners for the never-ran and gate-exit checks.** That misses `_run_hook_raw` and about 105 direct exec sites (row 86). M9 wraps `subprocess.run` instead.

### Assumption ledger

**Root:** New shell code pays for macOS `/bin/bash` 3.2, because every shipped shell file has to run on it. This change sets a bash 5.2 floor for every shipped shell file and makes a below-floor or missing bash fail closed for gates. One check runs before any newer construct and turns a below-floor bash into a gate deny. It is reached by every hook that sources a `_lib.sh`, by `install.sh`, and by 25 of the 34 scripts under `claude/.claude/scripts/` (rows 18-19, 84). `onFailure: "block"` turns a gate that cannot start, aborts, is killed, prints invalid output or times out into a block.

**Givens:**
- G1. macOS's `/bin/bash` is 3.2, and that is what a `#!/bin/bash` shebang always runs there. *Reason: the OS vendor ships and protects that path (inventory subagent; restated at `_config.sh:22`).*
- G2. Claude Code runs a command hook through `sh -c` with the harness process's own environment, so `#!/usr/bin/env bash` resolves against the PATH Claude Code was started with. *Reason: vendor-defined hook execution (code.claude.com/docs/en/hooks, fetched by the session).*
- G3. On PreToolUse, exit 2 blocks. Any other non-zero exit is a non-blocking error and the tool call proceeds, unless the registration sets `onFailure: "block"` (row 13). *Reason: vendor hook protocol (same source).*
- G4. Plugins install and update independently of install.sh, through `claude plugin install/update`. *Reason: Claude Code's plugin mechanism (`docs/hooks.md:294-296`).*
- G5. `git pull` puts hook and script changes live with no install step, and `install.sh` runs only when a user runs it. *Reason: stow symlinks the checkout into `$HOME` (CLAUDE.md: changes under `claude/.claude/**` "go live on `git pull`"; README.md:113). Changing that is an install-architecture decision outside this plan.*

**Rows:**
1. `[engineer-verified: "MacOS is still supported but will have to install later bash versions via homebrew"]` macOS stays a supported platform. Its users install a newer bash via Homebrew. `anchors: root`
2. `[engineer-verified: "simplify existing code and unlock bash 4+ features"]` The change removes 3.2 guards and permits bash 4+ constructs in new code. `anchors: root`
3. `[engineer-verified: "Require Homebrew bash (Recommended)"]` This is the selected macOS-handling label. `anchors: root`
4. `[unverified]` The session wrote the description of row 3's option: env-bash shebangs, a documented prerequisite, and an install.sh check that fails fast. The plan implements all three, with env-bash shebangs on every shipped bash file (M1). `anchors: row3`
5. `[engineer-verified: "5.1 (Recommended)"]` The engineer first typed "I think the latest version but I'm open to alternative rationales", then asked "Is 5.1 also supported on macOS with homebrew and wsl?", then selected this label. Row 79 supersedes it: after plan-review round 2 the engineer selected "5.2, CI unchanged (Recommended)". `anchors: root`
6. `[engineer-verified: "Replace with version floor (Recommended)"]` This is the selected guard-test label. `anchors: row2`
7. `[unverified]` The session wrote that option's description: drop the bash-4 token checks, keep the `sort -V` check renamed, and add a shebang test. The plan implements it as described. The renamed home is a new `test_shell_dialect.py`, whose `sort -V` ban covers every tracked shell file (M5). `anchors: row6`
8. `[engineer-verified: "Not sure. Ask fable on this one. I'm inclined to keep things as they are and just reduce complexity for future implementation"]` The engineer delegated the rewrite scope and leans minimal. `anchors: row2`
9. `[unverified]` The plan adopts Fable's recommendation (a subagent's view) as the rewrite scope:
   - Rewrite all 12 `${arr[@]+"${arr[@]}"}` sites (row 10). That now includes `_worktree-lib.sh:158`, because every script that runs its functions reaches the floor check first (M2).
   - Leave count guards, parallel arrays, `tr` lowercasing, while-read loops, and `LC_ALL=C` pins as they are.

   The stale 3.2/4.x comment sweep (M6's comment rule) goes beyond Fable's list. The engineer kept it (row 91).

   `anchors: row8`
10. `[verified: grep \$\{[A-Za-z_][A-Za-z_0-9]*\[@\]\+ over the tree, plans excluded]` There are exactly 12 sites, all under `claude/.claude/scripts/`, and none in hooks, plugins, or install.sh:
    - `_worktree-lib.sh:158`
    - `worktree-removal-status.sh:210,247`
    - `cleanup-merged-branches.sh:224,232,1020`
    - `cleanup-idle-open-pr-worktrees.sh:332,335,338,373,377,381`

    `anchors: M6`
11. `[verified: staff-sdet's round-3 one-line runs on bash 5.2.21; not run on 4.4 or 3.2]` Under `set -u` on bash 5.2.21, `"${arr[@]}"` expands to nothing and exits 0 for an empty, unset, `local a`, `local -a a` or `declare -a a` array. The 12 rewrites are therefore safe at the floor without row 44's per-array check, and no test is needed for it. `anchors: M6`
12. `[engineer-verified: "Not sure, ask the architect. I'm totally fine raising the minimum Claude code version as well for simplicity"]` The engineer delegated the `onFailure` decision. `anchors: M4`
13. `[verified: code.claude.com/docs/en/hooks, fetched by the session and re-read by claude-hook-review in round 2; ciso-reviewer's round-2 read-only string inspection of the installed Claude Code 2.1.293-2.1.301 binaries]` What `onFailure` does:
    - It accepts `"continue"` (the default) or `"block"`. 2.1.295 is the first binary that handles it, and 2.1.294 has no handling.
    - These count as failure: the hook could not start, timed out, exited with a code other than 0 or 2 (even after printing allow JSON), or printed JSON that is invalid or fails validation. Plain-text stdout is not a failure.
    - `"block"` makes a failure count as exit 2.
    - It is ignored for async hooks and on Stop, SubagentStop, TaskCompleted and TeammateIdle.
    - Without it, a timed-out PreToolUse hook does not block the call.
    - The default command-hook timeout on PreToolUse is 600 s, and no gate sets its own.

    The repo has 0 uses today. One gate makes a network call (row 82). `anchors: M4`
14. `[verified: deny-credential-file-reads.sh:19-33; _lib.sh:24-26; test_hook_alignment.py:2678-2708,161]` Every gate defines an exit-2 `emit_deny` stub before running `. "${0%/*}/_lib.sh"`. `_lib.sh`'s first command returns 1 when `_config.sh` fails to source. `test_missing_lib_sh_denied` enforces the stub for all 31 `GATE_HOOKS`: 27 under `claude/.claude/hooks/` and 4 in plugins. `anchors: M2`
15. `[verified: grep "could not source _lib.sh"]` The bootstrap text appears in 30 shipped hooks: the 27 gates plus `capture-session-id.sh`, `record-session-end.sh`, and `ask-review-permissions.sh`. The 4 plugin gates use the shape `"Blocked by <label>: could not source _lib.sh."`. staff-platform-engineer found 10 of the 30 that differ from any single template. `anchors: M3`
16. `[verified: deny-credential-file-reads.sh:24]` The bootstrap sources `_lib.sh` with `2>/dev/null`, so anything `_config.sh` prints to stderr never reaches the deny. `anchors: M2, M3`
17. `[verified: transcript_analysis/denials.py:395-400; test_transcript_denials.py:259-319,389-400]` Transcript analysis classifies a denial as `lib-source` by the substring "could not source _lib.sh", and the tests drive and pin the bootstrap wording for 27 gates. `anchors: M3`
18. `[verified: _config.sh:2-12; install.sh:427-432; _lib.sh:24]` Who sources `_config.sh` today:
    - every hook, as `_lib.sh`'s first command;
    - install.sh, by repo-relative path before stow runs;
    - `config-get.sh` and `migrate-legacy-config.sh`.

    The file is kept free of dependencies on `_lib.sh`, so it is the first file every bash consumer loads. M2 puts the floor check at its top. `anchors: M2`
19. `[verified: grep of top-level source lines in claude/.claude/scripts/*.sh]` Twenty scripts source `hooks/_lib.sh` and two source `hooks/_config.sh`, so each reaches M2's check at that line. `worktree-removal-status.sh` and `cleanup-idle-open-pr-worktrees.sh` source only `_worktree-lib.sh`. `anchors: M2`
20. `[verified: README.md:96-104; install.sh:26-29]` On macOS, hooks already need Homebrew-supplied tools first on the harness PATH: python3 ≥ 3.11, resolved at execution time, and `sha256sum` from coreutils' gnubin. When the harness PATH has no bash-bearing directory, `env` finds no bash at all and exits 127 before any hook code runs (ciso-reviewer S1, finding 2). For gates, M4 turns that into a block. `anchors: M1, M4`
21. `[unverified]` A GUI-launched Claude Code on macOS may lack Homebrew's bin on its PATH. The adversarial subagent cites anthropics/claude-code#44649 and #46954, and the vendor docs say the desktop app imports the shell profile. Such a harness resolves `bash` to `/bin/bash` 3.2, so every gate denies, and the deny points at `./install.sh`. Run from a terminal whose bash meets the floor, install.sh passes. So M3's success line and the README, CHANGELOG and docs/hooks.md recovery text name this case. That same harness already resolves python3 below 3.11 (row 20). `anchors: M3, M4`
22. `[verified: grep of "/bin/bash" argv in tests]` Five call sites pin `/bin/bash` while sourcing `_lib.sh`: `test_lib_mask_shell_quotes.py:25`, `test_lib_pseudo_file_path.py:22`, and `test_lib_path_char_allowlist.py:65,151,169`. On macOS each would hit the floor check. `:80` and `:98` probe `/bin/bash` for the en_US tests that M9 deletes. `anchors: M9`
23. `[verified: test_shellcheck.py:5,31; select-tests.py:362-366,620-622]` `test_shellcheck.py` lives in `claude/.claude/hooks/tests/` and discovers files through `scripts/list-shell-files.sh`. Shell changes under `claude/.claude/scripts/` and `plugins/lovable-cloud/scripts/` select that directory for it. A lint beside it inherits that routing (M5). `anchors: M5`
24. `[verified: staff-sdet round-2 grep of the scripts/list-shell-files.sh corpus]` No tracked shell file uses `sort -V` or `--version-sort` on a non-comment line. The only mentions are two comment lines in plugin hooks. The new test's first green run re-confirms it. `anchors: M5`
25. `[verified: .claude/settings.json:2-6]` `skill-management` and `plugin-semver` are enabled at project scope in this repo. Row 81 records what is not known about other repositories. install.sh's check never reaches a plugin consumer in another repository (G4). `anchors: row54`
26. `[engineer-verified: "Include all four (Recommended)"]` Plugins are in scope. This round-1 answer is superseded by row 31. Row 52 records the current outcome. `anchors: row31`
27. `[verified: CHANGELOG.md:5,276-280]` CHANGELOG.md keeps an `[Unreleased]` section with `### Removed` entries that carry a "**Migration:**" note and the phrase "live on `git pull`". `anchors: M7`
28. `[verified: docs/hooks.md:284-290]` § "Gate deadlock recovery" documents the case where every gate blocks because a PATH tool is bad. M7 adds the below-floor bash and `onFailure` cases there. `anchors: M7`
29. Retired (round 3): no new deny text is written.
30. `[unverified]` Bash runs a script or a sourced file one command at a time, so a newer construct placed after the floor check is never parsed on 3.2. Compound commands and function bodies are parsed whole. staff-platform-engineer reproduced this on 5.x, not on 3.2. In round 2 the same reviewer showed on 5.2.21 that a syntax error or a missing command inside the floor block still makes a gate deny through its bootstrap. So a misparse on 3.2 would lose the message, not the deny. M2 puts the check before any other command, and Verification step 7 runs it on a real 3.2. `anchors: M2`
31. `[engineer-verified: "All four, major bump"]` This is the round-2 answer on plugins. The session reads it as: all four plugins move to the floor, each with a major plugin-semver bump. It was given beside the guard question that row 33 delegates. `anchors: M7, row52`
32. `[engineer-verified: "Let's have CI match. Either raise to 5.2 or drop CI to 5.1"]` The floor must equal the bash CI runs. Row 79 records which route the engineer chose. `anchors: M2`
33. `[engineer-verified: "Ask the architect"]` The engineer delegated when the runtime floor guard ships. The question stated that no hook needs bash > 3.2 today, and that shipping the guard now locks out stock-bash users and silences every non-gate hook below the floor, including the PreToolUse ask hooks (`ask-review-permissions.sh`, `ask-new-dependency-disclosure.sh`) and the credential redactor. The option descriptions were the session's. `anchors: M8`
34. `[verified: .github/workflows/tests.yml:25]` CI's one job runs on `ubuntu-24.04`, and its pytest steps use that image's bash. `[unverified]` That bash is 5.2.21 (per the adversarial subagent; not re-run this session). The CI-parity test checks it on the PR's CI run. `anchors: M2`
35. `[unverified]` The session's supporting facts for the floor, none checked this session. Row 79 records the engineer's decision.
    - Matching CI to 5.1 means running on the `ubuntu-22.04` image (bash 5.1, per the adversarial subagent). GitHub retires a hosted Ubuntu image once newer LTS images replace it, and Ubuntu 22.04's standard support ends in 2027. A 5.1 CI would need moving again soon, or a step that builds bash from source.
    - A 5.2 floor excludes the system bash of Ubuntu 22.04 (including WSL installs of it), Debian 11, and RHEL 9 (5.1, per the adversarial subagent). Under full removal, hooks on those hosts lock out unless a newer bash is installed. Homebrew also runs on Linux.
    - Homebrew's current bash is 5.3.
    - No current code depends on behavior that only 5.2 has (staff-platform-engineer checked `${var//pat/&}`).

    `anchors: M2`
36. `[verified: staff-product-engineer's grep of hooks, plugins, install.sh and scripts for mapfile, declare -A, ${x,,}, $EPOCHSECONDS, wait -n, coproc, ${x@Q}; this session's grep of claude/.claude/**/*.sh for case modification, @-transformations, EPOCHSECONDS/EPOCHREALTIME, declare/local/typeset -n, wait -n]` No shipped hook or plugin uses a bash-4+ construct outside comments. For a user at or above the floor, the only hook behavior this change alters is therefore M4's block-on-failure. `anchors: M8`
37. Retired (round 3): row 52 supersedes the two-stage reading of row 2.
38. Retired (round 3): no longer load-bearing. The two scripts that hooks execute, `marker.sh` and `resume-context.sh`, source `_lib.sh`, so they reach the check.
39. `[verified: grep for ^#!/usr/bin/env bash]` `claude/.claude/hooks/require-worktree-for-file-writes.sh` already uses `#!/usr/bin/env bash` at merge-base. M1 brings every other file to the same shebang, and M4 covers the can't-start case for every gate. `anchors: M1`
40. Retired (round 3): M5 no longer has call-site rules.
41. `[verified: grep of shebangs; scripts/tests/conftest.py:110-117; test_cleanup_idle_open_pr_worktrees.py:806-812]` The three worktree scripts already use `#!/usr/bin/env bash`. Their tests build any curated PATH from `shutil.which`/`which` on the caller's PATH. `anchors: M9`
42. `[verified: select-tests.py:557-559,757,766,780; test_select_tests.py:1147-1152; staff-sdet's round-2 import probe of select-tests.py]` A change to `claude/.claude/scripts/*.sh`, `claude/.claude/hooks/*.sh`, or `install.sh` selects `claude/.claude/hooks/tests/`. Every new test in this plan lives there except `claude/.claude/tests/test_shell_exec_guard.py` and the per-tree wiring tests (`claude/.claude/scripts/tests/` and `plugins/lovable-cloud/tests/` each get one, M9). `claude/.claude/tests/helpers.py` is a global trigger, so any phase that edits it runs the full suite. `anchors: M5, M9`
43. `[verified: install.sh:1-51,427-432]` `install.sh` runs, in order:
    1. `set -e` and a banner echo.
    2. The presence check (:10-20).
    3. The python floor (:25-36).
    4. It computes `REPO_DIR` and changes into it (:38-45).
    5. Its first mutation, a `mkdir` (:46).

    It sources `_config.sh` only later (:427-432). No test runs the whole of `install.sh` today. Every `test_install_sh_*` file extracts a marker-delimited block. `anchors: M2`
44. `[verified: staff-sdet, Answers 5; staff-platform-engineer, opening section]` Both reviewers read all 12 idiom sites. Each array is initialized with `=()` at its declaration, before its loop. `anchors: M6`
45. `[engineer-verified: "Follow-up (Recommended)"]` Plugins stay unchanged in this PR. The major bump and floor description move to the guard follow-up. Superseded by row 52: plugins move now, with row 31's major bump. `anchors: row52`
46. `[verified: staff-sdet's nine-file table (round 1) and round-2 read of test_require_skill_review.py:3211; grep of shebangs]` The nine curated-PATH test files staff-sdet found in round 1 run hooks or `relocate-claude-config.sh` with a PATH that has no bash. After M1's sweep, that gives 13 failing sites and 6 vacuously passing sites, because `run_hook*` maps exit 126/127 to "allow" (`claude/.claude/tests/helpers.py:211-212`). `test_require_skill_review.py:3211` is a seventh vacuous site. Phase 1a's temporary sweep re-derives the full set. `anchors: M9`
47. `[verified: .claude/rules/bash-unit-test-seams.md]` The repo's seam rule puts a helper in an existing sibling library whose domain covers it. Failing that, it goes in `_lib.sh` once two hooks need it, and otherwise in a new `_<topic>-lib.sh`. A new bare `_*-lib.sh` under `claude/.claude/hooks/` fails CI today, because `test_hook_alignment.py` sweeps it in as a hook. `anchors: M2`
48. Retired (round 3): no token ban remains, in hooks or elsewhere (M5).
49. `[engineer-verified: "Adopt with Fable's changes (Recommended)"]` The engineer accepted the narrowed scope (M8 deferral) together with Fable's six plan changes. The engineer asked "Does fable agree with that?" before selecting. Fable's view (a subagent's, not the engineer's) was "Partly": the deferral is sound on lockout and hardening cost, but this PR is stage 1 of a staged rollout and should say so. Superseded on the deferral point by row 52. `anchors: M8, row52`
50. Retired (round 3): the token ban is removed everywhere, so the trade it weighed no longer arises.
51. `[engineer-verified: "Wait wait wait fable's rationale hinges on "gives existing users an install-time signal before the guard arrives"??? If so, that's not a good reason. I have like only [count omitted] users of this repo."]` The engineer rejected the staged-rollout rationale and describes this repo's user base as small. The bracketed text in the quote replaces a count removed for redaction. `anchors: root, M8`
52. `[engineer-verified: "Yes, full removal (Recommended)"]` The engineer selected full removal. It supersedes row 49 on the deferral point, and row 45 on plugins. `anchors: root, M8`
53. `[unverified]` The session wrote that option's description: drop 3.2 everywhere, hooks and plugins included, using `onFailure: "block"` on gates plus a raised Claude Code minimum, instead of the deferred-guard plan. The plan adopts it. The runtime version check (M2) is outside that description, and row 74 records the engineer keeping it. `anchors: row52`
54. `[unverified]` This plan reads row 51 as follows: a small user base, each user told directly, makes lockout and migration cost near zero for stow users. Here "each user" means each recipient of the pre-merge message. Who installs the four hook-bearing plugins outside this repo is not named (row 81). The engineer selected "All among the [count omitted]" for whether those users are among that user base (row 88; the bracketed text replaces a count removed for redaction). The plan also keeps these signals for any consumer the user base does not cover:
    - the deny text's literal fix (M3);
    - the major bump and the plugin descriptions;
    - the floor sentences in README.md's plugin section and `docs/skills.md` (M7).

    The migration text reaches every recipient directly. `anchors: M7, M8`
55. `[verified: claude/.claude/settings.json:237-442; plugins/*/hooks/hooks.json; grep of "hook-class: gate"]` All 31 gates register only under PreToolUse. There are 32 entries in `settings.json`: 27 gates, five of them under two matchers each. Each plugin's `hooks.json` adds one. No gate sits on Stop, SubagentStop, TaskCompleted, or TeammateIdle, and no registration is async. The two Stop hooks (`advance-past-commit-stall.sh`, `nudge-handoff-near-context-cap.sh`) are not gates. So row 13's exclusions leave no gate uncovered. `anchors: M4`
56. `[verified: grep of the 31 gate files for exit with a literal status other than 0 or 2, and for bare exit/return]` No gate exits with a literal status other than 0 or 2. The one `exit 3` (`deny-reviewer-tree-mutation.sh:343`) ends a subshell whose status the hook branches on. `anchors: M4`
57. `[verified: staff-platform-engineer round-2 last-line grep of the 31 gates and _lib.sh:416-440; claude-hook-review round 2]` 16 of the 31 gates end with a column-0 `exit 0`: 14 stow gates and 2 plugin gates. The other 15 already exit 0 when execution runs off the end:
    - 9 end in `emit_deny`, whose `_lib_emit_deny` prints the JSON envelope and returns 0, exiting 2 only when jq is unavailable. They are `require-respond-pr.sh`, `enforce-marker-script-shape.sh` (its last line ends a multi-line deny string), `guard-settings-session-keys.sh`, `require-architect-consult.sh`, `require-routing-read.sh`, `require-memory-skill.sh`, `require-code-review.sh`, `require-plugin-version-bump.sh` and `require-skill-review.sh`.
    - 2 end in `_lib_staged_length_gate` (`_lib.sh:2326-2388`), which only returns 0.
    - 4 end in `fi` or `esac`. In `deny-reviewer-tree-mutation.sh` every arm exits explicitly. In `deny-private-project-refs.sh`, `require-plan-review.sh` and `require-ready-for-review.sh` the last branch is an `emit_deny`.

    No gate uses `set -e` or a non-literal `exit "$x"`. The appended `exit 0` lines therefore change no behavior today. They guard against a future gate whose last command can return non-zero. `anchors: M4`
58. `[verified: staff-sdet round 2, read of each site]` A grep for curated PATHs is not an enumeration. `"PATH":` appears in 78 test files, most of them using `build_path_without`'s full mirror, and the patterns miss `env["PATH"] =` and `monkeypatch.setenv`. Of the extra sites round 2 listed, these already carry bash and need no edit:
    - `test_deny_reviewer_tree_mutation.py:1020-1032`
    - `test_enforce_marker_script_shape.py:1381,1883`
    - `test_consume_durable_continuity_file_on_read.py:211,399`
    - `test_require_npm_version_bump.py:546`
    - `test_require_plugin_version_bump.py:436`
    - `test_require_skill_review.py:1404,1461,1500,1537,1598`

    `test_stow_packages.py:533`, `test_config_get.py:37,119`, `test_plan_push_preflight.py:1408` and `test_install_dev.py:68` are left to Phase 1a's temporary sweep, which replaces the grep as the enumeration for PATHs that hold no bash. It cannot see a PATH that holds a system directory: on Linux `/usr/bin/bash` exists, so such a site passes, and on macOS `/bin/bash` is 3.2, so it fails. `helpers.py`'s `_forced_fallback_path_env` (`{shim_dir}:/usr/bin:/bin`) and `test_config_get.py:37,119` (`/usr/bin:/bin`) are two known sites of that shape, both named in M9. Verification 7's Mac suite run looks for siblings. `anchors: M9`
59. `[verified: staff-sdet round 3, git grep of symlink_to(...bash...) in tests; this session recounted test_lib.py as 10]` 19 test sites already link the caller's `bash` into a curated directory inline (round 2's count of 22 used 13 for `test_lib.py`, which was wrong):
    - 10 in `test_lib.py`;
    - 5 in `test_require_skill_review.py` (:1404,1461,1500,1537,1598);
    - one each in `test_require_npm_version_bump.py:546`, `test_require_plugin_version_bump.py:436`, `test_hook_alignment.py:3037` and `test_ci_watch.py:340`.

    `test_nudge_handoff_near_context_cap.py:561` also links bash, but into a fake `claude`, not a curated PATH, so it is not counted. They stay inline (Out of scope). `link_bash_into` is idempotent, so it composes with them and with `build_path_without` mirrors, which already carry a `bash` link. `anchors: M9`
60. `[verified: helpers.py:172-214,203,239,271,304,346,387,457,499]` Eight `run_hook*` runners exec hooks by path, so the shebang picks the interpreter. `run_hook` maps empty stdout with any exit other than 2 to "allow". `anchors: M9`
61. `[verified: deny-credential-file-reads.sh:11; redact-credential-values.sh:8]` Hooks run under `set -uo pipefail` without `-e`, so a failing command mid-script does not end the hook. `[unverified]` On bash 3.2, a bash-4 builtin or option (`mapfile`, `readarray`, `declare -A`, `local -n`, `wait -n`) fails as one command and the hook continues. A bash-4 expansion (`${x,,}`, `${x@Q}`) aborts the hook. Fable verified on bash 5.x this session: a syntax error exits 2, which blocks on PreToolUse with or without `onFailure`. A missing builtin under `set -uo pipefail` prints "command not found", and the script continues to `exit 0`. `anchors: M2, M4`
62. Retired (round 4): the session-start notice is dropped (row 73).
63. `[verified: grep of plugins/*/hooks/*.sh]` The four plugin gates bootstrap with `if ! . <dir>/_lib.sh 2>/dev/null; then emit_deny "Blocked by <label>: could not source _lib.sh."`. Each plugin `_lib.sh`'s first command is a function definition. The plugin non-gates `consume-migration-token.sh` and `provision-validator-venv.sh` do not source `_lib.sh`. `anchors: M2, M3`
64. `[verified: README.md:102,113]` README's Claude Code floor is 2.1.218, for two skills, and install.sh does not check it. README tells existing users that `git pull` does not run install.sh's steps. `anchors: M4`
65. `[unverified]` How a Claude Code CLI older than 2.1.295 treats `onFailure` is inferred, not observed. In round 2, ciso-reviewer read 2.1.294's embedded command-hook schema. It is the same plain object as 2.1.301's minus `onFailure`, with no `.strict()` suffix. That suggests an older CLI strips the unknown key and keeps today's fail-open behavior. Whether the schema factory strips or rejects the key was not confirmed. If an older CLI rejects the key, the loss could be the whole of `claude/.claude/settings.json` (permission rules, `statusLine` and every hook), or a whole plugin `hooks.json`, not just the gate entries. Verification 7's probe settles this on the oldest CLI any user runs, and a settings error there is a stop-and-ask. Until then the controls are:
    - the README floor;
    - the CHANGELOG order (update Claude Code before pulling);
    - the engineer's own machines, checked in Verification 7. Other users get the migration text, and a user who has not replied with a `claude --version` counts as possibly below 2.1.295 (Verification 7).

    The probe's old-CLI arm covers three surfaces, not only a project `settings.json`: a user-scope scratch `CLAUDE_CONFIG_DIR` (the stowed `settings.json` is user scope), a project file, and a scratch local-marketplace plugin whose `hooks.json` carries the field.

    `anchors: M4`
66. `[verified: config-get.sh:2,10,39]` `config-get.sh` runs under `set -uo pipefail`, documents exit 3 as environment failure, and sources `_config.sh` with no status check. `anchors: M2`
67. `[verified: staff-sdet B10, citing bash's NEWS for 5.0; test_lib_path_char_allowlist.py:57-88,134-136]` The 10 en_US parametrizations run only on a bash below 5.0, because 5.0 turns `globasciiranges` on by default. At or above the floor they skip everywhere. `anchors: M9`
68. `[verified: staff-sdet, Answers 4; test_lib.py:4424-4443]` The `BASH_COMPAT=32` test reruns inputs that `test_matches_the_normpath_oracle` already covers at default compatibility. `anchors: M9`
69. `[verified: docs/scripts.md:136; docs/security-hardening.md:857-868; README.md:95]` Three prose passages are affected:
    - `docs/scripts.md:136` says nothing exercises macOS `/bin/bash` 3.2.
    - The PATH-trust bullet in `docs/security-hardening.md` does not name the interpreter.
    - README.md:95 says hooks use `#!/bin/bash`.

    `anchors: M7`
70. `[unverified]` On a Mac with Homebrew bash installed, the PATH order that already puts Homebrew's python3 ahead of `/usr/bin` (row 20) also puts Homebrew's bash ahead of `/bin`, because both link into Homebrew's one bin directory. `anchors: M1`
71. `[verified: staff-sdet round 2, importing select-tests.py and calling select_pytest_targets on hypothetical paths]` A change under `plugins/<name>/hooks/` (a `*.sh` file or `hooks.json`) selects `claude/.claude/hooks/tests/`. Verification 1 rechecks the printed selection. `anchors: M5, M9`
72. `[verified: ciso-reviewer's round-2 read of the Claude Code 2.1.301 binary, whose block reason is "[<hook>]: failed; blocking because onFailure is "block"" followed by the hook's trimmed stderr; claude-hook-review round 2, which found the hooks doc's example shows the same notice line followed by the script's error text]` Under `onFailure: "block"`, the blocked call's reason carries the failing hook's stderr. So `env: 'bash': No such file or directory`, or a crash's error line, reaches the agent. Verification 7's probe observes it once. `anchors: M3, M4`
73. `[engineer-verified: "Drop it (Recommended)"]` The session-start notice is dropped. The question offered keeping it to close the window where an agent uses ungated Grep or WebFetch before the first gate denies. The reasons were the session's: with a small user base told directly before merge, the first gate deny plus `./install.sh` is enough discovery. Dropping the notice also removes a non-gate hook region that would have to stay bash-3.2-clean with no lint. In round 2, ciso-reviewer showed that the question understated the residual. The window lasts the whole lockout. A non-gate hook that fails to source `_lib.sh` exits 0, which shows nothing. The corrected gap went to the engineer as a separate question (row 76). `anchors: M3`
74. `[engineer-verified: "Keep M2 (Recommended)"]` After plan-review round 2 the engineer kept the runtime floor check (M2). The option description was the session's. This row covers only the choice to keep M2. `anchors: M2`
75. `[engineer-verified: "Keep the audit (Recommended)"]` After plan-review round 2 the engineer kept the `onFailure` exit-0 audit (M4's three parts and Phase 1d). The option description was the session's. This row covers only the choice to keep it. `anchors: M4`
76. `[engineer-verified: "Make redactor fail loud (Recommended)"]` The engineer chose a visible failure for the credential redactor below the floor over accepting a silent residual. The question, in the session's wording, described the gap this way: below the floor the redactor exits 0 silently, and Grep and WebFetch have no gate. `anchors: M3`
77. `[unverified]` The session wrote that option's description: change only `redact-credential-values.sh`'s source-failure branch to emit a visible failure instead of a silent `exit 0`, with the session-start notice still dropped. The plan implements it as described (M3). It leaves `restore-authorization-boundary-on-compact.sh` and `advance-past-commit-stall.sh` silent (Out of scope). `anchors: row76`
78. `[verified: claude-hook-review's round-3 fetch of code.claude.com/docs/en/hooks, first 100k of 270k characters; not re-fetched this session]` On PostToolUse, a command hook that exits with a code other than 0 or 2, registered without `onFailure`, is a non-blocking error. The transcript shows a `<hook name> hook error` notice with `Failed with non-blocking status code:` and only the first line of stderr. So the redactor's key words go first on its line (M3). Exit 2 instead shows stderr to the model after the tool has run. `[unverified]` That the tool result still reaches the model is implied by the doc, not quoted. It is a premise for the engineer's row 76 choice, so Verification 7 probe C observes it and has a failure branch. `anchors: M3`
79. `[engineer-verified: "5.2, CI unchanged (Recommended)"]` The engineer set the floor at bash 5.2 and kept CI on `ubuntu-24.04`. That settles row 32's either/or and supersedes row 5. The option description was the session's. This row covers only the selected floor and the CI choice. `anchors: M2`
80. `[engineer-verified: "Yes, they stand"]` Asked after plan-review round 2 whether the ledger's quoted rows stand as recorded, the engineer confirmed them. That confirmation covers the quotes as they stood before the redaction edits (bracketed text marks each removed figure or name). The question's wording was the session's. `anchors: root`
81. `[unverified]` No repository other than this one is known to enable `skill-management` or `plugin-semver`, and no consumers of `npm-semver` or `lovable-cloud` are named. Three docs describe project-scope installs in downstream repositories: `docs/hooks.md:295`, README.md's plugin section, and `docs/skills.md:185-197`. So consumers outside the small user base may exist. `docs/skills.md:185-188` registers the marketplace from a local checkout, which suggests a consumer has a clone. `anchors: row54`
82. `[verified: require-ready-for-review.sh:381-392; README.md:99; grep of claude/.claude/hooks/*.sh and plugins/*/hooks/*.sh for gh, curl, wget, git fetch and git ls-remote]` One gate makes a network call: `require-ready-for-review.sh:388` runs `gh pr view` through `_lib_capped`.
    - With `timeout` or `gtimeout` on PATH, the call is capped at 5 s, and a timeout still allows (empty output, then `exit 0`).
    - With neither, the call runs to the 600 s harness timeout. Under M4 that now blocks `git push` and `gh pr ready` instead of allowing them. `gh pr create` never reaches the call: it sits inside `if ! $is_gh_pr_create` (:377-380 say it is skipped for `gh pr create`), and the commands that do reach it are `git push` on a non-default branch and any command text matching `gh pr ready` (header :85-89). This reverses the design the gate's header states at :52-55, :85-86 and :382-386.

    README.md:99 already requires a `-k`-capable `timeout` or `gtimeout`, so the uncapped case arises only on a machine that misses a stated requirement. The only other network call in hooks, `check-branch-divergence.sh:94`'s `git fetch`, is in an informational hook. `anchors: M4`
83. `[verified: grep "own hook timeout|does not block the tool call|doesn't block the tool call|fail-open timeout|timeout fails open" over shipped files]` These comment sites state the pre-M4 rule that a timed-out gate does not block:
    - `_lib.sh:35-37`;
    - `_lib.sh:86-92` (a dated vendor quote);
    - `plugins/skill-management/hooks/_lib.sh:76-78,99-101`;
    - `deny-pii-in-commits.sh:174-180` (a Known-gaps entry: "the commit then proceeds unscanned") and `:568`;
    - `enforce-marker-script-shape.sh:811-813`;
    - `require-ready-for-review.sh:382-386`, plus the header's fail-open lines at :52-55 (the "gh pr view fails ... fail-open" bullet) and :85-86 (the "fails, times out, or is missing ... released" bullet), which the grep pattern above does not find.

    M6's bash-version comment rule does not find them. The remaining hits make no block claim, or describe non-gates, or are historical records:
    - `plugins/{lovable-cloud,npm-semver,plugin-semver}/hooks/_lib.sh:13-15`;
    - `_lib.sh:4086-4089`;
    - `docs/handoff-nudge.md:141` and `docs/long-turn-nudge.md:25`;
    - `docs/design-decisions/**`.

    `anchors: M4, M6`
84. `[verified: grep of source lines in claude/.claude/scripts/*.sh; _direnv-lib.sh:17; worktree-ensure.sh:81; relocate-claude-config.sh:12]` Nine of the 34 non-library scripts under `claude/.claude/scripts/` source no library that reaches `_config.sh`:
    - `claude-auto.sh`
    - `claude-enable-tool.sh`
    - `findings-path-suffix.sh`
    - `relocate-claude-config.sh` (deliberately, per its header)
    - `respond-pr-safe-patch.sh` (its `_respond-pr-lib.sh` sources neither)
    - `review-pr-check-attribution.sh`
    - `skill-fidelity-report.sh`
    - `stow-packages.sh`
    - `update-claude-config-plugins.sh`

    The other 25 reach it: 20 via `_lib.sh`, `ci-watch.sh` via `_direnv-lib.sh`, 2 via `_config.sh`, and 2 that gain a source line (row 19). `anchors: M2`
85. `[verified: staff-sdet round-2 AST scan of test env dict literals; test_config_py.py:526-530]` Four test sites pass an `env` with no `PATH` key while running a shipped shell file:
    - `scripts/tests/test_config_py.py:526`
    - `scripts/tests/test_review_pr_findings_path.py:40`
    - `scripts/tests/test_review_pr_finish.py:181`
    - `scripts/tests/test_review_pr_finish.py:531`

    With no PATH, the child resolves `bash` through the default search path (`os.defpath` is `/bin:/usr/bin`). On macOS that is `/bin/bash` 3.2. So after M2 these sites fail there, while they pass on Linux CI. The scan misses envs built by spread or helper, so this is a lower bound. M9's guard catches the rest at run time. `anchors: M9`
86. `[verified: staff-platform-engineer round-2 grep \[str\([A-Za-z_]*HOOK\)\]; test_hook_alignment.py:2604-2627; grep "from subprocess import|import subprocess as" (no hits); grep of Popen( in tests]` Besides the eight runners, two other paths exec hooks: `_run_hook_raw` (14 uses, driving the 31-gate suites) and about 105 direct `subprocess.run([str(<X>_HOOK)])` sites, 25 of them in `test_deny_private_project_refs.py`. Every one calls `subprocess.run` by attribute, so wrapping it intercepts them all. No gate's own test file uses `Popen`. `_run_hook_raw` is five file-local helpers, not one: 14 uses in `test_hook_alignment.py` and 50 in four other files (staff-sdet round 3). That supports the single interception point. `anchors: M9`
87. `[verified: CLAUDE.md is 200 lines; check-claude-md-length.sh:127; _lib.sh:2316,2365]` `check-claude-md-length.sh` is a gate that denies a commit growing the repo's `CLAUDE.md` past 200 lines (`new > limit && new > old`). So any added line needs an unrelated deletion. `anchors: M7`
88. `[engineer-verified: "All among the [count omitted]"]` The engineer selected that label for the question of whether the four hook-bearing plugins' outside users are among the small user base. The bracketed text in the quote replaces a count removed for redaction. The question's wording and options were the session's. This row covers only that selection. It does not name those users or their Claude Code versions. `anchors: row54, row81`
89. `[engineer-verified: "Accept (Recommended)"]` The engineer accepted M4 reversing the documented fail-open of `require-ready-for-review.sh` on a machine lacking `timeout` and `gtimeout` (row 82): its uncapped `gh pr view` now blocks at the harness timeout. The option description was the session's. This row covers only that choice. `anchors: M4, row82`
90. `[engineer-verified: "Approve all (Recommended)"]` The engineer approved three deliverables: a single `subprocess.run` wrapper for M9, enabled from three conftests; an `install.sh` floor test that extracts a marked block; and a new `.claude/rules/shell-floor.md` instead of a `CLAUDE.md` line. The option descriptions were the session's. This row covers only that approval. `anchors: M7, M9, row87`
91. `[engineer-verified: "Keep the sweep (Recommended)"]` The engineer selected that label when asked whether to keep M6's stale 3.2/4.x comment sweep beyond the 12 idiom rewrites. The question and option text were the session's. This row covers only that selection. `anchors: M6, row8`

**Mechanisms:**
- **M1 — shebangs.** Every file in the `scripts/list-shell-files.sh` corpus whose line 1 names bash becomes `#!/usr/bin/env bash`. Paths with a `tests/` segment are excluded. That includes sourced libraries that carry a shebang (`_lib.sh`, `_config.sh`, the plugin `_lib.sh` copies), so one rule covers every file. Only line 1 changes, and file modes stay. `anchors: row4, row20, row39, row70`
  - Lighter alternatives fail:
    - Keeping `#!/bin/bash` can never reach a floor above 3.2 on macOS (G1).
    - Hardcoding `/opt/homebrew/bin/bash` breaks Intel macOS, Linux and WSL.
    - Writing `bash <path>` in each registration leaves scripts, and every test that execs by path, on the shebang.
- **M2 — floor check.** `_config.sh` starts, right after its header comment, with this block:
  - `_BASH_FLOOR_MAJOR=5` and `_BASH_FLOOR_MINOR=2`, assigned unconditionally, so a value inherited from the environment cannot change them.
  - `_bash_version_meets_floor MAJOR MINOR`. It is pure: it returns 0 only for numeric arguments at or above the floor, and 1 for empty or non-numeric input.
  - `if ! _bash_version_meets_floor "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"; then`, print the message to stderr and `return 1`.
  - The block parses and runs on bash 3.2. Nothing else in the file runs before it, so on an older bash the rest is never parsed (row 30).

  Each plugin `_lib.sh` starts with the same constants, function and `if`, before its first function. Its branch is `return 1` with no message: its only callers are gate bootstraps that discard stderr (rows 16, 63), and the deny text carries the fix (M3). Plugins install separately and must stand alone (G4). So a test pins the five copies' constants and `declare -f _bash_version_meets_floor` as identical. It also pins the call line, `if ! _bash_version_meets_floor "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"; then`, and the `return 1` after it, as exact normalized text (whitespace collapsed) in all five copies. Only a text pin can catch a call site that passes literal numbers or a parsed `$BASH_VERSION`: while CI's bash equals the floor, no behavioral test can tell those from `BASH_VERSINFO` (ciso-reviewer round 3). The message is not pinned across copies, because only `_config.sh` has one.

  Who reaches it (rows 18-19, 84):
  - every stow hook, via `_lib.sh`'s first command, and the four plugin gates, via their `_lib.sh`;
  - 25 of the 34 non-library scripts in `claude/.claude/scripts/`;
  - `install.sh`.

  New source lines go in `install.sh` (moved early), `worktree-removal-status.sh` and `cleanup-idle-open-pr-worktrees.sh`. `config-get.sh` gains `|| exit 3`, its documented code for an environment failure (row 66). Some files do not reach the check: the nine scripts in row 84, the plugin non-gates, `statusline-command.sh`, `scripts/` and `plugins/lovable-cloud/scripts/`. They get the shebang and the documented floor only (Out of scope).

  What a below-floor bash produces:
  - Gates deny through their existing bootstrap (row 14).
  - The redactor reports a hook error (M3).
  - Other hooks stop, as they already do on any `_lib.sh` failure.

  The message region uses single-quoted strings or a quoted heredoc only, so it needs no expansion beyond the floor constants and the `$BASH_VERSION` and `$BASH` values, and it parses on bash 3.2. The `uname -s` substitution sits in the `case` word and runs only inside the failing branch, so it is safe under `set -e`. A failing `uname` falls to the Other section.

  The message reads to a person at a terminal, because install.sh and scripts print it and the hook bootstraps discard it (row 16). It is the primary help a below-floor user gets, so it is as concrete as the python-floor message at `install.sh:24-34`. It lives once, in `_config.sh`'s `return 1` branch, and install.sh shows it by sourcing that file (row 18). The branch picks one platform section by `uname -s` (`Darwin` versus anything else) and prints only that section. The floor is built from the constants. Required content, not final prose:
  - Always, first line: the running `$BASH_VERSION` and `$BASH` are older than the floor.
  - Always: "Fix it from a terminal, not from inside Claude Code", and a pointer to README.md § "Requirements".
  - Darwin section:
    - `/bin/bash` is 3.2, so run `brew install bash`;
    - confirm with `"$(brew --prefix)/bin/bash" --version`;
    - re-run from the repo root with that bash first on PATH, `PATH="$(brew --prefix)/bin:$PATH" ./install.sh`, because the running bash may still be the old one first on PATH. This form lets install.sh and its children (`stow-packages.sh`, `migrate-legacy-config.sh`, `register-marketplace.sh`) resolve the same floor bash through `#!/usr/bin/env bash`;
    - put Homebrew's bin before `/bin` on the PATH Claude Code starts with, then restart Claude Code.
  - Other section (Linux, including WSL):
    - names the bash that is running, and says a distro-packaged bash on older LTS hosts is below the floor, worded as "e.g. Ubuntu 22.04, Debian 11, RHEL 9" and claiming no version beyond what row 35 records ([unverified], row 35);
    - install a bash at the floor or newer and put it first on PATH, by a newer distro release or backport package, Homebrew on Linux, or a source build;
    - re-run `./install.sh`, then restart Claude Code.

  The floor is 5.2, equal to CI's bash (rows 34, 79). The engineer selected it with CI unchanged (row 79). Hosts whose system bash is 5.1 need a newer bash before they pull (row 35). Verification 7 checks the engineer's own machines. Other users get the migration text and report their versions.

  `anchors: row14, row16, row18, row19, row30, row32, row34, row35, row47, row61, row63, row66, row74, row79, row84`
  - Over-powered check: M2 runs on every hook invocation, which is heavier than an install-time check. Lighter primitives fail:
    - An install.sh check alone never runs on `git pull` (G5).
    - `onFailure` alone (M4) catches crashes, but a bash-4 builtin or option fails as one command and the hook carries on (row 61), so a gate could allow silently. A below-floor user would also see nothing until such code lands.
    - A new `_bash-floor-lib.sh` fails CI's hook sweep (row 47).
    - A check in `_lib.sh` ahead of its `_config.sh` source misses install.sh and `config-get.sh`, which source `_config.sh` alone (row 18), so the floor would be defined twice.
- **M3 — cause discovery. No stow gate bootstrap changes.**
  - A stow gate's deny already says to run `./install.sh`, and below the floor in that terminal install.sh prints M2's platform-specific message, the user's primary help. 26 of the 27 stow gate denies say this. `enforce-marker-script-shape.sh:257` says only "could not source _lib.sh.", but every matcher it sits on also carries gates whose deny names `./install.sh`. Transcript analysis keeps classifying the deny as `lib-source` (row 17), whose remedy is that same `./install.sh`.
  - When the terminal's bash meets the floor, install.sh passes. It then prints the bash it used (`$BASH_VERSION`, `$BASH`) and says that hooks resolve bash from the PATH Claude Code starts with, which can differ from this terminal's. It points at `docs/hooks.md` § "Gate deadlock recovery". That covers row 21's case. README, CHANGELOG and docs/hooks.md name the same case (M7).
  - Each plugin gate's `could not source _lib.sh` text gains the literal fix, worded as the user's action in a separate terminal, because this deny text reaches the agent (M2's message does not, row 16) and an agent that reads an imperative may run `brew install bash` itself, which cannot fix Claude Code's PATH. The wording follows `_lib_emit_deny`'s precedent ("ask the user to install ... in a separate terminal"): a bash older than 5.2 first on PATH also causes this, and the user must install bash 5.2 or newer (macOS: `brew install bash`) in a separate terminal, put it first on the PATH Claude Code starts with, and restart Claude Code. Plugin consumers have no install.sh to route through. The text keeps the substring row 17 classifies on. A test pins that substring, the user-action framing (a key phrase such as "separate terminal", not the install command alone), and the number, which must equal that plugin `_lib.sh`'s constants.
  - `redact-credential-values.sh`'s source-failure branch (:14-16) prints one stderr line and exits 1 instead of exiting 0 silently (rows 76-78). The line begins with the literal "could not source _lib.sh" so it greps beside the gate denies, then says credential redaction is OFF for this result. It names both causes without a version number (a bash below this repo's floor first on Claude Code's PATH, or a stale stow link, which `./install.sh` fixes) and points at README.md § "Requirements". Exit 1 is a non-blocking error on PostToolUse (row 78), so the transcript shows a hook error carrying the first stderr line on each result left unredacted. The branch sits at the bootstrap and stays bash-3.2-parseable (a `printf` and an `exit`). Its comment is one line: "Exit 1, not 0, so the harness shows a hook error while redaction is off." Exit 2 was set aside. On PostToolUse it feeds stderr to the model as hook feedback, this repo reserves exit 2 for decisions, and the fix (PATH and a restart) is the user's.
  - The redactor's surrounding text changes in the same edit, so nothing contradicts the branch. Header lines 5-6 say the hook "Fails open on any parse/extraction failure"; they gain one fact: below the bash floor `_lib.sh` fails to source and the hook exits 1 with a stderr line, and the other failure paths still fail open. The comment above `INPUT=$(cat)` (:10-11, "exiting non-zero would break the triggering tool call") is scoped to malformed stdin. `docs/hooks.md`'s entry for this hook (the "Fails open on any parse failure" sentence) gets the same exception. `track-permission-prompts.sh:35` carries the same sentence for a Notification event, so it stays unless probe C contradicts it.
  - If probe C (Verification 7) shows no default-view notice, or a withheld result, the claim above is false. That is a stop-and-ask that reopens row 76, and the README, CHANGELOG and `docs/hooks.md` sentences are worded from the observed result, not from this text.
  - Under M4, a gate that cannot start blocks with the failure's own stderr (row 72).

  `anchors: row15, row16, row17, row21, row63, row72, row73, row76, row77, row78`
  - Residual: below the floor, every tool with no gate registration stays usable for the whole lockout (for example Grep, Glob, WebFetch, WebSearch, NotebookEdit and MCP tools), and the non-gate hooks other than the redactor stay silent. See Out of scope.
  - A lighter alternative fails. A per-gate deny channel is round-1 M3, with about 30 bootstraps in about ten wordings.
- **M4 — `onFailure: "block"`: adopted (rows 12, 75).** Every registration of a `hook-class: gate` hook gets the field: 32 entries in `claude/.claude/settings.json` and one in each plugin `hooks.json` (row 55). No other registration gets it, and an alignment test enforces both directions.

  Full effect: on Claude Code 2.1.295 or later, every gate outcome other than exit 0 or 2 with valid output now blocks the call. That covers a gate that:
  - cannot start, including `env` finding no bash (127);
  - aborts on an unset variable under `set -u`;
  - is killed by a signal, for example SIGPIPE or SIGKILL;
  - exits with a literal status other than 0 or 2;
  - prints JSON that is invalid or fails validation;
  - times out (600 s, row 13).

  The timeout case includes the one gate network call. Uncapped, `require-ready-for-review.sh:388` now blocks at the harness timeout instead of allowing (row 82). A failing command in the middle of a gate does not end it, because gates run without `set -e` (row 61), so the gate's own logic still handles that case.

  README's Claude Code minimum rises to 2.1.295 (rows 12-13). install.sh does not check it, because it does not run on pull (G5) and a fresh install gets a current CLI. Older-CLI behavior is inferred, not observed (row 65). Verification 7's probe settles it before merge, or the engineer accepts the arm unrun by name.

  `onFailure` blocks are probably not counted by transcript analysis's denial detection (Out of scope). The block reason's shape (row 72) matches none of `_HOOK_DENIAL_SIGNATURE`'s three alternatives by reading. Verification 7 checks it against a real record from probes A and B.

  The audit (row 75) has three parts:
  1. A static tripwire closes run-off-the-end for all 31 gates: every gate's last code line is a column-0 `exit 0`. All 31 already exit 0 when they run off the end (row 57), so the 15 appended lines are a regression guard, not a fix.
  2. The shell-exec guard's gate checks (M9) surface failures on tested paths. They fail any test where a gate exits other than 0 or 2, or prints an invalid PreToolUse envelope. The guard sits on `subprocess.run`, so it covers the eight `run_hook*` runners, `_run_hook_raw` and the direct exec sites alike (row 86). Phase 1d's run is the record, and any entry in it is a gate defect to stop and report.
  3. Failures on untested paths, can't-start and timeouts are blocked by design, which is what `onFailure` exists for. For irreversible gates that matches the repo's tier rule (ciso-reviewer, round 1). With a small user base, a false block is loud and recoverable (§ "Gate deadlock recovery"), unlike today's silent allow.

  `anchors: row12, row13, row55, row56, row57, row64, row65, row75, row82`
  - Over-powered check: `onFailure` is wider than the bash problem, because it changes crash handling for every gate. Lighter primitives fail:
    - Without it, the env-bash shebang adds a silent fail-open when `env` finds no bash (row 20). The tier rules do not let a change add one without a fix or an engineer decision, and the region above each gate's bootstrap would need a bash-3.2 lint.
    - Per-gate `trap … EXIT` handlers need 31 edits and cannot cover can't-start or timeout.
- **M5 — dialect lint.**
  - Delete `claude/.claude/scripts/tests/test_no_bash4_constructs.py`.
  - Add `claude/.claude/hooks/tests/test_shell_dialect.py` beside `test_shellcheck.py`. It uses the same discovery, `scripts/list-shell-files.sh`, and excludes paths with a `tests/` segment.
  - **Shebang rule.** If line 1 starts with `#!` and names bash, it must be exactly `#!/usr/bin/env bash`. The test reads line 1 before any comment filtering. Files with no shebang, or a non-bash one, are exempt.
  - **`sort -V` rule.** No `sort -V` or `--version-sort` on any non-comment line, including combined short flags (`-rV`, `-uV`) and the key-modifier form (`-k1,1V`), which the deleted test's regex missed. This is a BSD-userland rule, not a bash-version rule, and it carries no escape comment.
  - The predicates are pure functions of (relative path, text). A parametrized control table feeds them a violating and a clean sample for each rule, a shebang-less file, `#!/bin/sh`, a comment-only `sort -V` mention, and violating rows for `sort -rV`, `sort -uV` and `sort -k1,1V`.
  - A discovery sentinel asserts the corpus is non-empty and includes `install.sh` and one plugin hook.

  `anchors: row6, row7, row23, row24, row71`
- **M6 — simplification.**
  - Rewrite the 12 idiom sites to `"${arr[@]}"` (rows 9-11, 44).
  - **Comment rule, one rule for every shipped shell file.** Some comments cite bash 3.2, 4.x, "bash-4", or `test_no_bash4_constructs.py` as a live constraint. Delete such a comment only when this PR removes the code it explains. Where the code stays, the comment becomes the one line "redundant on the supported bash floor; kept to avoid churn", with no version number.
  - Keep `install.sh`'s record that a construct was verified on bash 3.2 and 5.3, because it records a past check.
  - **Timeout comments (row 83).** Each site that says a timed-out gate does not block gains one sentence: "Gate registrations set `onFailure: "block"`, so on Claude Code 2.1.295 or later a timed-out gate blocks the call instead."
    - The dated vendor quote at `_lib.sh:86-92` stays verbatim, because it quotes the default, and the sentence follows it.
    - `deny-pii-in-commits.sh:174-180`'s Known-gaps entry and `:568` are restated to match.
    - `require-ready-for-review.sh:382-386` says its uncapped `gh pr view` now blocks at the harness timeout (row 82). Its header's fail-open lines (:52-55 and :85-86) get the same exception: "except on a machine with neither `timeout` nor `gtimeout`, where it blocks at the harness timeout". The 5 s capped timeout still allows, so `:118-121` stays.
  - Locate the sites by grep, not by line number.

  `anchors: row9, row10, row11, row44, row83`
- **M7 — docs and migration.**
  - README.md states:
    - the floor, with the literal macOS fix and a non-Homebrew route;
    - the Claude Code minimum as one requirement;
    - the existing-user step, including the case where install.sh passes but gates still deny;
    - the floor in the plugin section.
  - CHANGELOG.md leads with what changes on pull. It gives migration steps before pulling, a recovery path after pulling, and the full `onFailure` effect.
  - `docs/hooks.md` gets § "Bash version", one sentence in § "Gate hooks" pointing a new-gate author at the `onFailure` registration and final `exit 0` rules (otherwise they meet them only as a failing alignment test), the redactor entry's exception (M3), and the deadlock-recovery additions. `docs/skills.md` gets the plugin floor sentence.
  - `docs/scripts.md` and `docs/security-hardening.md` get one-sentence corrections.
  - A path-scoped `.claude/rules/shell-floor.md` tells authors and reviewers of this repo's shell files four things: bash-4+ is allowed and 3.2 portability is not a finding, the floor lives in README § "Requirements", `sort -V` stays banned, and files that reach the floor check keep the code above it 3.2-parseable. It is a rule file, not a `CLAUDE.md` line, because `CLAUDE.md` is at its gated 200-line cap (row 87). `README.md`'s `.claude/rules/` list (Configuration files, ~:254) gains "the shell floor". Path-scoped rules load only when a matching file is opened, so a diff-only reviewer subagent may never load this one, and the global `staff-platform-engineer` agent still checks "bash 3.2 vs 5.x". The global agent is not edited. A stray 3.2 portability finding in this repo is dismissed on sight, and the PR body says so. `.claude/rules/review-pipeline-dispatch.md` carries no per-persona notes (grep), so it is not the place for that sentence.
  - Each plugin gets a major bump and a description stating the bash and Claude Code floors, with matching `marketplace.json` text.

  `anchors: row1, row2, row27, row28, row31, row54, row64, row69, row81, row87`
- **M8 — timing: hooks and plugins move now.** The round-2 deferral, decided under row 33's delegation, is withdrawn under rows 51-52. None of its four reasons survives:
  1. "Protects nothing on day one." The check's day-one effect, locking a below-floor user out at once, is now the intended migration signal for a small user base told directly (rows 51, 54).
  2. The hardening list collapses under M3, M4 and M9. See Out of scope for each item.
  3. The `onFailure` spike is answered by rows 55-57 and 65.
  4. The token-ban tripwire is no longer needed, because the check guards every gate and every stow hook.

  `anchors: row33, row36, row51, row52, row53, row54`
- **M9 — test harness.**
  - `helpers.py` gets `link_bash_into(bin_dir)`. It symlinks `shutil.which("bash")` into the directory and asserts that it resolves. It is idempotent: a no-op when `bin_dir/bash` already resolves to the same file, and an error when it resolves to a different one. Every curated-PATH site that Phase 1a's temporary sweep flags calls it, including:
    - `_forced_fallback_path_env`'s shim directory;
    - a bash-only directory prepended to `test_config_get.py`'s `/usr/bin:/bin`;
    - a bash-only directory in place of `test_provision_validator_venv.py`'s `/nonexistent`.

    The 19 existing inline links stay as they are (row 59).
  - **The shell-exec guard: one interception point.** `helpers.py` gets a pure function, `check_shell_exec(argv, env, cwd, completed)`, and a plain function, `install_shell_exec_guard(monkeypatch)`, which wraps `subprocess.run` and passes each completed call to `check_shell_exec`. Each conftest of `claude/.claude/hooks/tests/`, `claude/.claude/scripts/tests/` and `plugins/lovable-cloud/tests/` defines its own small autouse fixture that calls `install_shell_exec_guard`. Importing a fixture into a conftest instead would trip ruff F401 (unused import) under the repo's `E,F,B,I,UP,SIM` rule set, and a `# noqa: F401` would need a rationale. `pytest_plugins` is not an alternative, because pytest rejects it in a non-top-level conftest. The call uses the import, so no suppression is needed. Every hook exec in those trees goes through `subprocess.run` (row 86), so one check covers the eight runners, `_run_hook_raw` and the direct exec sites. The runners' own 126/127-to-"allow" mapping stays, because the guard raises before a runner reads the result. The target is argv[0] when it names a file, or argv[1] when argv[0]'s basename is `bash` (bare or absolute) and argv[1] names a file, resolved against `cwd`. A `bash -c ...` call has no target, so the shebang read is skipped and only check 2's bare-`bash` arm applies. The shebang read is on bytes, so `Path` or `bytes` argv elements, a binary argv[0] (ELF or Mach-O, as `shutil.which("bash")` yields) and bytes stderr neither raise nor skip silently. A guard-internal exception is never swallowed: the only raise is the dedicated error. A test that replaces `subprocess.run` with its own fake (about 40 sites in `test_transcript_*`) bypasses the wrapper, which is acceptable. Its error is a dedicated `AssertionError` subclass. It fails the test when:
    1. *Never ran:* the call exits 126 or 127, the target's line 1 is `#!/usr/bin/env bash`, and stderr's first line is `env`'s own error about `bash` (or stderr was not captured). The matcher keys on the `env:` prefix and the program name `bash`, not on quote characters, because the text differs by implementation and locale: GNU `env` prints curly quotes under a UTF-8 locale and ASCII quotes under the C locale, and BSD `env` on macOS prints `env: bash: No such file or directory` unquoted. A script's own "command not found" does not match.
    2. *No PATH:* an `env` is passed without a `PATH` key, and the target has an env-bash shebang or argv[0] is a bare `bash` (row 85).
    3. *Gate exit (Phase 1d):* `_hook_class` reads the target's header as `gate`, and the exit is not 0 or 2. The hooks doc counts a non-0/2 exit as a failure "even if it printed JSON that allows the action".
    4. *Gate output (Phase 1d):* a gate exits 0, and its stripped stdout starts with `{` but does not parse as a JSON object, or carries a `hookSpecificOutput.hookEventName` other than `"PreToolUse"`. The `permissionDecision` value is not checked, because the CLI's enum (four values in the installed 2.1.301, including `defer`, which no gate emits, per staff-platform-engineer's read-only string probe) is the harness's to validate, and a copy here would go stale. Plain-text stdout passes, because the CLI does not count it as a failure (row 13).

    `_hook_class` and `_HEADER_SCAN_LINE_COUNT` move from `test_hook_alignment.py` into `helpers.py`, so one parser serves the guard and the alignment tests. A sentinel pins that it finds all 31 gates.
  - A self-test, `claude/.claude/tests/test_shell_exec_guard.py`, drives all eight runners and a bare `subprocess.run` against tmp stubs, with a positive and a negative case for each check. `check_shell_exec` is pure, so a control table also feeds it fabricated `completed` objects: the `env` error in its GNU curly-quoted, GNU ASCII-quoted and BSD unquoted forms, and a localized message, each of which must raise for check 1. The negative row is a script's own "command not found" (127), which must not. Further rows cover `bytes` argv with `bytes` stderr, `Path` elements, a binary argv[0], `["bash", "-c", ...]`, and an absolute-path bash followed by a script, each asserting either no raise or the dedicated raise.
  - **Wiring tests, one per guarded tree.** The self-test sits in a fourth tree (`claude/.claude/tests/` has no conftest, and `.claude/rules/test-tree-packaging.md` forbids packaging it), so it cannot observe whether a conftest installs the guard. Each guarded tree therefore gets one small test, `test_shell_exec_guard_wired.py`, in `claude/.claude/hooks/tests/`, `claude/.claude/scripts/tests/` and `plugins/lovable-cloud/tests/`. It runs an env-bash stub under a bash-less PATH through a bare `subprocess.run` and asserts that the guard's error is raised there. To confirm it once built, delete one conftest's fixture on a scratch checkout: exactly one test must go red.
  - Each `/bin/bash` argv pin (row 22) becomes `shutil.which("bash")`, asserted to resolve, with no `/bin/bash` fallback. A bytes argv uses `os.fsencode`.
  - In `test_lib_path_char_allowlist.py`, delete the en_US parametrizations, their probe, `_bin_bash_version`, and the docstring's `/bin/bash` claims (row 67). `_lib.sh`'s `LC_ALL=C` pin stays (row 9).
  - Delete `test_lib.py`'s `BASH_COMPAT=32` test (row 68).

  `anchors: row22, row41, row46, row58, row59, row60, row67, row68, row85, row86`
  - Over-powered check: wrapping `subprocess.run` is wider than patching the runners, because it sees every subprocess call in three test trees. It only inspects results, and it raises on four narrow signatures. Lighter primitives fail:
    - Patching the eight runners misses `_run_hook_raw` and about 105 direct sites (row 86), so the gate audit would cover a minority of gate test paths.
    - Routing the runners and `_run_hook_raw` through one spawn function still misses the direct sites, unless about 105 call sites are edited.
    - Editing the curated PATH lists alone leaves the 126/127-to-"allow" mapping (row 60), so a site added later goes vacuous again.

## Critical files

There are five `code-writer` dispatches, run in order in the shared feature worktree. Each is one commit, and each phase's output is the next one's input:
- 1a makes the tests survive 1b's sweep, proven against a temporary sweep.
- 1c and 1d build on 1b's shebangs.
- Phase 2's docs cite names that 1c and 1d create.

After every dispatch, run these worktree-relative commands (README § Tests: the `.venv` lives only in the main worktree root, so a worktree uses `../../../.venv`):
- `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`
- `../../../.venv/bin/ruff check claude/.claude/ claude-skills/ plugins/`
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`

The ruff command includes `plugins/` because CI lints it, though `CLAUDE.md`'s documented command omits it. Phases 1a and 1d run the full suite, and CI records 12m45s for its two pytest passes on a hosted runner, so the dispatch prompt says to run `select-tests.py` there in the background and read the log, because the Bash tool's foreground cap is 600 s and a killed call loses the red/green record.

**Phase 1a — test harness, proven against a temporary sweep.** Stage nothing under `plugins/`. The version-bump gate has no `tests/` exemption, and the bump lands in 1b.
- `claude/.claude/tests/helpers.py`:
  - Add `link_bash_into`, idempotent, per M9.
  - `_forced_fallback_path_env` links bash into its shim directory.
  - Add `check_shell_exec` and `install_shell_exec_guard` with checks 1 and 2 (M9).
- `claude/.claude/hooks/tests/conftest.py` and `claude/.claude/scripts/tests/conftest.py`: each defines a small autouse fixture that calls `install_shell_exec_guard` (M9, which gives the F401 reason).
- `claude/.claude/hooks/tests/test_shell_exec_guard_wired.py` and `claude/.claude/scripts/tests/test_shell_exec_guard_wired.py` (new): the per-tree wiring tests (M9).
- `claude/.claude/tests/test_shell_exec_guard.py` (new), in the style of `test_git_repo_builders.py`. Drive all eight `run_hook*` runners and one bare `subprocess.run`:
  - An env-bash stub run under a PATH without bash raises the guard's error.
  - The same stub run with an `env` lacking `PATH` raises it.
  - The same stub with bash on PATH does not.
  - The control-table rows for `env`'s three error renderings and the argument shapes (M9).
  - `link_bash_into` called twice on one directory succeeds, and fails over a link to another file.
- Enumerate with the oracle, then fix:
  1. **Baseline.** With the guard installed and the flip off, run select-tests.py (full suite, background). Record its red tests. A pre-existing environmental red, such as a missing `direnv`, `stow` or GNU `timeout`, is then not mistaken for a site.
  2. Derive the flip list: corpus files (`scripts/list-shell-files.sh`, excluding paths with a `tests/` segment) whose line 1 is exactly `#!/bin/bash`. Expect 77. The list is not saved anywhere: the reversal in step 6 derives it again from `git diff --name-only` over the shell corpus, which is exactly the flipped set because step 5 edits only test files.
  3. Flip line 1 of exactly those files with `sed -i '1s|^#!/bin/bash$|#!/usr/bin/env bash|'`.
  4. Run select-tests.py (full suite, background). The sweep set is the red tests and guard failures here minus step 1's red tests. Both runs' red lists go in the dispatch return.
  5. Fix each site in the sweep set with `link_bash_into` on its curated directory, or by adding `PATH` to an `env` that lacks one. Rerun only the changed test files, at most three iterations, then one final full run. A site that still fails after the third iteration is a stop-and-report.
  6. **Reverse the flip. This is the dispatch's last action on every exit path, including stop-and-report and an interrupted run.** Reverse it on the files in `git diff --name-only` over the shell corpus. A clean `git status` for shipped shell files is a precondition for returning, and the dispatch's return says so.

  Step 4's sweep set is the enumeration for PATHs that hold no bash only (row 58). It cannot see a PATH that holds a system directory, which passes on Linux and fails on macOS, so Verification 7 adds a Mac suite run. These are the expected files, each edited only where the sweep set shows a site:
  - row 46's nine: `claude/.claude/hooks/tests/test_check_claude_md_length.py`, `test_check_skill_length.py`, `test_guard_settings_session_keys.py`, `test_nudge_error_mode_analysis.py`, `test_nudge_handoff_near_context_cap.py`, `test_require_worktree_for_git_writes.py`, `test_require_skill_review.py` (including :3211), `test_provision_validator_venv.py`, and `claude/.claude/scripts/tests/test_relocate_claude_config.py`;
  - `claude/.claude/scripts/tests/test_config_get.py`;
  - the no-PATH sites (row 85): `claude/.claude/scripts/tests/test_config_py.py`, `test_review_pr_findings_path.py`, `test_review_pr_finish.py`;
  - only if the sweep set shows them: `claude/.claude/hooks/tests/test_stow_packages.py`, `claude/.claude/scripts/tests/test_plan_push_preflight.py`, `claude/.claude/scripts/tests/test_install_dev.py`.

  If a site in the sweep set falls in any other file, including one under `plugins/`, stop and report it, after step 6.
- `claude/.claude/hooks/tests/test_lib_mask_shell_quotes.py:25`, `test_lib_pseudo_file_path.py:22`, and `test_lib_path_char_allowlist.py`: apply the pins and deletions per M9. Use `os.fsencode` for the one bytes-argv pin, `test_lib_path_char_allowlist.py:65` (`:80` and `:98` are deleted, and `:151` and `:169` take `str` argv with `text=True`).
- `claude/.claude/hooks/tests/test_lib.py`: delete the `BASH_COMPAT=32` test (:4424-4443).

**Phase 1b — shebang sweep, dialect lint, plugin versions.**
- `claude/.claude/hooks/tests/test_shell_dialect.py` (new), per M5. Run it red first. It must fail listing exactly the 77 files, which is the positive control.
- Line 1 of each listed file becomes `#!/usr/bin/env bash`, and nothing else changes. That covers `claude/.claude/hooks/`, `claude/.claude/scripts/`, `claude/.claude/statusline-command.sh` if listed, `plugins/*/hooks/`, `plugins/lovable-cloud/scripts/`, `scripts/`, `install.sh` and any other listed file. Then run the test green.
- A test this sweep newly turns red may get a `link_bash_into` call or a `PATH` key. That is the only permitted edit beyond line 1. For anything else, stop and report.
- `plugins/lovable-cloud/tests/conftest.py`: an autouse fixture calling `install_shell_exec_guard`, as in Phase 1a. `plugins/lovable-cloud/tests/test_shell_exec_guard_wired.py` (new): the wiring test (M9). Both land here, with the plugin version bump, because the version-bump gate has no `tests/` exemption.
- Delete `claude/.claude/scripts/tests/test_no_bash4_constructs.py`. Update the comments and docstrings that cite it, without changing any rule:
  - the sites in `claude/.claude/scripts/select-tests.py` and `claude/.claude/scripts/tests/test_select_tests.py`, located by `git grep test_no_bash4_constructs`, not by line number (the lines have drifted since the plan was first written). Nine sites across the repo cite it outside plans and design records, and the grep re-derives them;
  - `claude/.claude/scripts/tests/test_default_branch_resolution_is_shared.py:10`, which should state its scope rationale directly.
- `plugins/{skill-management,npm-semver,plugin-semver,lovable-cloud}/.claude-plugin/plugin.json`: a major version bump, through the `plugin-semver` skill, which is hook-enforced. Add one description sentence: needs bash 5.2 or newer first on PATH (macOS: `brew install bash`) and Claude Code 2.1.295 or newer. `require-plugin-version-bump.sh` compares against the merge-base, so this bump covers later phases' plugin edits.
- `.claude-plugin/marketplace.json`: the matching description text.

**Phase 1c — runtime floor check and cause discovery.**
- `claude/.claude/hooks/_config.sh`:
  - Add the M2 block, with its message (the `uname -s` platform sections, M2), before any other command.
  - Add one header line saying the file starts with the bash floor check.
  - Add the one-line comment "Everything down to this block's `return 1` must parse and run on bash 3.2."
- `plugins/*/hooks/_lib.sh` (4 files): the constants, the function and the `if` with a bare `return 1`, plus the same comment, before the first function.
- `install.sh` (the shebang is Phase 1b's; this phase moves the check early and surfaces the message):
  - Directly after the banner echo, add an `INSTALL_TEST_FIXTURE: bash-floor-check` block, in the shape of the existing markers. It holds:
    - the `REPO_DIR` comment, assignment and `cd`, moved from :38-45;
    - `# shellcheck source=claude/.claude/hooks/_config.sh`;
    - `. "$REPO_DIR/claude/.claude/hooks/_config.sh" || exit 1`, carrying over the reason from the comment at :427-430;
    - M3's success line.
  - install.sh adds no message text of its own. On a below-floor bash the sourced `_config.sh` prints M2's platform section and `|| exit 1` ends the run with status 1, which the new test pins.
  - Above the source line, add the one-line comment "This line and everything above it must parse and run on bash 3.2."
  - Delete :427-432.
- `claude/.claude/scripts/worktree-removal-status.sh` (:28) and `claude/.claude/scripts/cleanup-idle-open-pr-worktrees.sh` (:68): immediately after `set -euo pipefail`, source `"$(dirname "${BASH_SOURCE[0]}")/../hooks/_config.sh"` under a shellcheck directive.
- `claude/.claude/scripts/config-get.sh:39`: append `|| exit 3`.
- `plugins/skill-management/hooks/require-skill-review.sh`, `plugins/npm-semver/hooks/require-npm-version-bump.sh`, `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`, and `plugins/lovable-cloud/hooks/validate-migration-filename.sh`: add the M3 literal fix to the deny text, worded as the user's action in a separate terminal.
- `claude/.claude/hooks/redact-credential-values.sh`: the M3 source-failure branch (:14-16) and its comment, plus header lines 5-6 and the :10-11 comment, scoped as M3 says. `docs/hooks.md`'s entry for the hook is edited in Phase 2.
- `claude/.claude/hooks/tests/test_redact_credential_values.py`: one new case.
  - Copy the redactor into a tmp directory beside a `_lib.sh` stub that returns 1.
  - Feed a valid PostToolUse Grep payload.
  - Expect exit 1, empty stdout, and a first stderr line that begins "could not source _lib.sh" and contains "credential redaction is OFF".
- `claude/.claude/hooks/tests/test_bash_floor.py` (new). Source the files in a `bash` subprocess.
  - Predicate matrix, over all five copies: 3.2, 4.4, 5.0 and 5.1 return 1. 5.2, 5.3 and 6.0 return 0. Empty or non-numeric input returns 1.
  - Parity: the five copies' constants and `declare -f _bash_version_meets_floor` are identical (precedent: `test_require_skill_review.py:1769`). The call line, `if ! _bash_version_meets_floor "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"; then`, and the `return 1` after it are identical as exact normalized text (whitespace collapsed) in all five. The message is not compared.
  - Placement:
    - The first non-comment line of `_config.sh` and of each plugin `_lib.sh` is `_BASH_FLOOR_MAJOR=`.
    - `_lib.sh`'s first command sources `_config.sh`.
    - No other shipped shell file assigns `_BASH_FLOOR_` or defines `_bash_version_meets_floor`.
  - Below the floor: in a tmp copy of `_config.sh` with the constants rewritten to 99 and 0, sourcing returns 1. Stderr contains the running `$BASH_VERSION`, `$BASH`, `99.0`, and "terminal, not from inside Claude Code". The platform sections are asserted in the install.sh test below.
  - At the real floor: sourcing returns 0 with empty stderr.
  - Environment override: export `_BASH_FLOOR_MAJOR=99`, then source the real file. It returns 0.
  - CI parity: CI's bash major.minor equals the floor. Skip unless `GITHUB_ACTIONS` is set (precedent: `scripts/tests/conftest.py:105`). The failure message says a runner-image change must revisit the floor.
  - Callers, parametrized over `worktree-removal-status.sh`, `cleanup-idle-open-pr-worktrees.sh` and `config-get.sh`:
    - Copy only the script into a tmp `scripts/` directory, with a stub `hooks/_config.sh` beside it that prints a marker and returns 1.
    - Do not copy `_worktree-lib.sh`, so a source line moved below it fails with a different stderr.
    - Run the copy with `bash` from PATH. Expect stderr exactly equal to the marker, with exit 1 for the first two and exit 3 for `config-get.sh`.
- `claude/.claude/hooks/tests/test_hook_alignment.py`: add `test_below_floor_bash_denied` as a module-level function beside `test_swept_gate_hooks_exhaustive`, not inside `TestGateHookBehavior`. That class carries a 31-way `GATE_HOOKS` parametrize on every method, so a method placed there would run 31 times or conflict with its own five-chain parametrization. Parametrize it over five chains: `deny-credential-file-reads.sh` with `_lib.sh` and `_config.sh`, and each plugin gate with its `_lib.sh`. For each chain:
  - Copy the chain into a tmpdir. For `validate-migration-filename.sh`, set `CLAUDE_PLUGIN_ROOT` to the tmp plugin root.
  - Control: with the floor intact, a valid payload produces no "could not source _lib.sh", and the exit is 0 or 2.
  - Raise the floor in the copy to 99. Call `_assert_blocks(..., "could not source _lib.sh")` and require an exit of exactly 0 or 2.
  - Plugin arm: the deny also contains the user-action key phrase ("separate terminal"), `brew install bash`, and the major.minor read from that plugin `_lib.sh`'s constants.

  The other 26 stow gates share one `_lib.sh`-to-`_config.sh` chain, which `test_missing_lib_sh_denied` already covers for all 31.
- `claude/.claude/hooks/tests/test_install_sh_bash_floor.py` (new). It extracts the `bash-floor-check` block, as `test_install_sh_python_floor.py` does.
  - Run `bash -c <block> <tmp_repo>/install.sh` with these settings:
    - `cwd` is the tmp repo, so `$0`'s directory is the tmp repo;
    - `env` is `os.environ` minus `CLAUDE_CONFIG_DIR` and `BASH_ENV`, with `HOME` an empty tmp directory;
    - `timeout=10` (precedent: `test_install_sh_machine_level_opt_ins.py:384`).
  - Stub case: put a stub at the tmp repo's `claude/.claude/hooks/_config.sh` that prints a marker and returns 1, and assert the real file exists at that path. Expect exit 1 and stderr exactly the marker.
  - Real below-floor cases: copy the real `_config.sh` there with the floor rewritten to 99, and put a stub `uname` first on PATH (the stub dir ahead of the real PATH). Run it twice, once with the stub printing `Darwin` and once printing `Linux`. Each run expects exit 1 and `99.0`, the running `$BASH_VERSION` and README § "Requirements" on stderr, plus that branch's key phrases and none of the other branch's:
    - Both sections contain `./install.sh` and say "from the repo root".
    - Darwin: `brew install bash` and `--prefix`; each is a unique token that the Linux output must not contain.
    - Linux: `Homebrew on Linux` and `source build`; each is a unique token that the Darwin output must not contain.
    - A third run with a `uname` stub that exits 1 expects the Other (Linux) section.
  - Real at-floor case: copy the real `_config.sh` unmodified. Expect exit 0, with stdout naming the running `$BASH_VERSION` and saying Claude Code's PATH can differ.
  - Placement: in the real `install.sh`, only comments, blank lines, `set -e` and the banner `echo` come before the block's start marker.
  - `HOME` stays empty in every case.
- Plugin-gate tests that pin the bootstrap text, edited only where they pin it: `claude/.claude/hooks/tests/test_require_skill_review.py`, `test_require_npm_version_bump.py`, `test_require_plugin_version_bump.py`, and `plugins/lovable-cloud/tests/test_validate_migration_filename.py`.

**Phase 1d — `onFailure` and its audit.**
- `claude/.claude/settings.json`: add `"onFailure": "block"` to the 32 gate entries.
- `plugins/*/hooks/hooks.json` (4 files): add it to each gate entry.
- `claude/.claude/tests/helpers.py`: move `_hook_class` and `_HEADER_SCAN_LINE_COUNT` here, and add guard checks 3 and 4 (M9).
- Run select-tests.py, which runs the full suite, and record every failure of checks 3 and 4. That record is the audit's tested-path part. Expect it to be empty (rows 56-57). Any entry is a gate defect that `onFailure` would turn into a production block. Stop and report it with the gate, the test, and the exit status or stdout, and do not patch the gate in this phase.
- `claude/.claude/tests/test_shell_exec_guard.py`, the gate cases:
  - A gate stub that prints allow JSON and exits 1 raises.
  - The same stub exiting 0 or 2 does not.
  - A non-gate stub exiting 1 does not.
  - A gate exiting 0 with unparseable `{` stdout raises.
  - An envelope whose `hookEventName` is not PreToolUse raises.
  - A `permissionDecision` value the guard does not know does not raise (M9 check 4).
  - Plain-text stdout does not raise.
- Every gate file without a final column-0 `exit 0` (row 57): append one after the final command, not inside a trailing `fi`/`esac` block. That is 13 stow gates plus `plugins/plugin-semver/hooks/require-plugin-version-bump.sh` and `plugins/skill-management/hooks/require-skill-review.sh`. After a final `emit_deny`, the line is reachable, because `emit_deny` returns.
- `claude/.claude/hooks/tests/test_hook_alignment.py`:
  - Import `_hook_class` and `_HEADER_SCAN_LINE_COUNT` from helpers.
  - Sentinel: an independent regex over each hook's full text for a `# hook-class: gate` line finds exactly 31 files, and `_hook_class` returns `"gate"` for each.
  - Last-line test: every gate's last non-blank, non-comment line is exactly `exit 0`, at column 0.
  - Registration test, built on a pure function that is fed a control table and then the real files:
    - The walk is scoped to the `hooks` object in `settings.json` and in each plugin `hooks.json`. `settings.json`'s `statusLine.command` (`bash ~/.claude/statusline-command.sh`) sits outside it and is ignored, with no allowlist entry that could also hide a real unresolvable hook command.
    - It resolves every command inside `hooks` to a file, both `~/.claude/hooks/<x>.sh` and `"${CLAUDE_PLUGIN_ROOT}"/hooks/<x>.sh` after any environment-assignment prefixes. It fails on any command it cannot resolve.
    - Exactly the gate handlers carry `"onFailure": "block"`, checked handler by handler, so a gate under two matchers needs the field on both.
    - It counts 32 gate handlers in `settings.json` and 4 across the plugin `hooks.json` files.
    - Every gate handler sits under `PreToolUse` and is not `async`.
    - Control-table rows: a double-matcher gate with the field on one handler only, an async gate, a gate under Stop, a non-gate carrying the field, an unresolvable command inside `hooks` (must fail), and a statusLine-shaped command outside `hooks` (must be ignored).
- If a gate's last call returns non-zero on purpose, so an appended `exit 0` would change its behavior, stop and report it.

**Phase 2 — simplification, comments, and docs.**
- The idiom rewrites (row 10): `claude/.claude/scripts/_worktree-lib.sh`, `worktree-removal-status.sh`, `cleanup-merged-branches.sh`, and `cleanup-idle-open-pr-worktrees.sh`.
- The M6 comment sweep, locating sites by grep. Expected files:
  - `install.sh`;
  - `claude/.claude/hooks/`: `_config.sh`, `_lib.sh`, `require-worktree-for-git-writes.sh`, `advance-past-commit-stall.sh`, `deny-reviewer-tree-mutation.sh`, `announce-approved-plan-path.sh`, `enforce-marker-script-shape.sh`, `deny-escaped-backticks-in-pr-body.sh`, `deny-pii-in-commits.sh`, `require-plan-review.sh`;
  - `claude/.claude/scripts/`: `migrate-legacy-config.sh`, `marker.sh`, `cleanup-merged-branches.sh`;
  - plugins: `plugins/npm-semver/hooks/require-npm-version-bump.sh`, `plugins/plugin-semver/hooks/require-plugin-version-bump.sh`.
- The M6 timeout comments (row 83): `claude/.claude/hooks/_lib.sh`, `plugins/skill-management/hooks/_lib.sh`, `claude/.claude/hooks/deny-pii-in-commits.sh`, `claude/.claude/hooks/enforce-marker-script-shape.sh`, `claude/.claude/hooks/require-ready-for-review.sh`.
- `claude/.claude/scripts/tests/test_cleanup_merged_branches.py`: in `TestEnvrcCannotOverrideDryRun`'s docstring (:3027-3030), say the diagnostic varies by bash version, without naming 3.2.
- `.claude/rules/shell-floor.md` (new):
  - `paths:` covers `"**/*.sh"`, `"claude/.local/bin/*"` and `"plugins/lovable-cloud/scripts/*"`.
  - Body, one short section of four bullets, in this order:
    - Bash-4+ constructs are allowed, and bash 3.2 portability is not a review finding in this repo.
    - Shipped shell code targets the bash floor in `` `README.md` § "Requirements" ``. The rule does not restate the number, which has one home there (and in the `_BASH_FLOOR_` constants), so a floor bump leaves the rule true.
    - `sort -V` and `--version-sort` stay banned, because BSD `sort` lacks them (a userland rule, not a bash-version one). The rule does not say a test enforces it.
    - In a file that sources `_config.sh` or a `_lib.sh`, the code above the floor check, and above a hook's `_lib.sh` bootstrap, must still parse on bash 3.2, per `` `docs/hooks.md` § "Bash version" ``. The nine scripts of row 84 and the other files that reach no check are not bound by it.
  - Re-run `ai-instruction-and-memory-files` on the rule after these body edits (Verification 10).
- Two test docstrings state the 3.2 constraint as live and cannot discriminate at the floor, like the tests M9 deletes. Both tests stay, because they still assert their contracts. Reword each docstring to drop the 3.2 framing: `claude/.claude/scripts/tests/test_worktree_lib.py` (the empty-input test, "only discriminates on a system where bash 3.2 is first") and `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py` ("regression test for macOS system /bin/bash 3.2 treating `cd ''`..."). Locate by grep.
- `README.md`:
  - `:254` (Configuration files), the `.claude/rules/` bullet: add "the shell floor" to its "used here for" list. Phase 2 first checks that `test_hook_alignment.py` and the docs tests do not enumerate rule files.
  - `:95`, the Shell bullet:
    - bash 5.2 or newer, first on the PATH Claude Code starts with;
    - every hook, script and `install.sh` uses `#!/usr/bin/env bash`;
    - macOS `/bin/bash` is 3.2, so run `brew install bash` and put Homebrew's bin directory before `/bin`;
    - elsewhere, install bash 5.2 or newer by any means that puts it first on PATH (distro package, Homebrew on Linux, or a source build), naming no distribution unless Verification 9 confirms it;
    - below the floor, `install.sh` exits naming the bash it found, every gate denies, and the credential redactor reports a hook error instead of redacting;
    - CI runs bash 5.2.
  - `:102`, rewritten as one requirement: Claude Code 2.1.295 or newer, because every gate registration sets `onFailure: "block"`, which older versions do not implement. Drop the two skills' 2.1.218 explanation, which 2.1.295 subsumes. Drop the "not enforced by install.sh, since it would block installation" reasoning. Say instead that install.sh does not check the version because it does not run on `git pull`, so update Claude Code before pulling.
  - `:104`: `brew install bash coreutils`, and put Homebrew's bin directory before `/bin`.
  - `:113`: if gates deny with "could not source _lib.sh" after a pull, run `./install.sh` from a terminal. If it names a below-floor bash, fix that. If it passes, Claude Code was started with a different PATH than that terminal: start it from a shell where `command -v bash` is the new bash, per `` `docs/hooks.md` § "Gate deadlock recovery" ``.
  - Plugin section (~:211), one sentence: `skill-management`, `plugin-semver`, `npm-semver` and `lovable-cloud` ship gates that need the bash and Claude Code versions in [Requirements](#requirements), and below them those gates deny.
- `docs/skills.md:185`: one sentence. Plugins that ship hooks need the bash and Claude Code versions in `` `README.md` § "Requirements" ``.
- `CHANGELOG.md`, under `[Unreleased]`:
  - A `### Removed` entry. It leads with the effect on pull: hooks, plugins and scripts now need bash 5.2 or newer first on the PATH Claude Code starts with, live on `git pull`. Below the floor, every gate denies with "could not source _lib.sh", other hooks stop, and the credential redactor reports a hook error instead of redacting. **Migration:**
    - Before pulling, from a terminal: run `brew install bash` on macOS (elsewhere, install bash 5.2 or newer), update Claude Code to 2.1.295 or newer, and start Claude Code from a shell where `command -v bash` is the new bash.
    - Pull from a separate terminal, not from inside a Claude Code session, whatever bash that session started with. Hooks and scripts are read at execution time, so any other Claude Code session already open on a below-floor PATH, on this machine or under another account, denies every gated call after the pull until it is restarted from a shell where `command -v bash` is the new bash.
    - Then update the four hook-bearing plugins, each a new major version.
    - If you have already pulled and gates deny with "could not source _lib.sh", run `./install.sh` from a terminal. If it names a below-floor bash, fix that and restart Claude Code. If it passes, restart Claude Code from a shell where `command -v bash` is the new bash (`docs/hooks.md` § "Gate deadlock recovery").
  - Under the existing `### Changed` (:7), one entry: gate hooks now block the tool call when they cannot start, abort (for example on an unset variable), are killed, print invalid JSON, or time out, instead of allowing it. This needs Claude Code 2.1.295 or newer. One documented allow changes. On a machine without `timeout` or `gtimeout`, a hung `gh pr view` in `require-ready-for-review.sh` now blocks `git push` and `gh pr ready` at the harness timeout (`gh pr create` never reaches that call).
- `docs/hooks.md`:
  - Add § "Bash version" just before § "Gate deadlock recovery", one sentence per fact:
    - hooks resolve bash through `env` on the harness PATH, and README.md states the floor;
    - `_config.sh` checks it first and returns 1, so gates deny and every other hook that sources `_lib.sh` stops;
    - of those, `redact-credential-values.sh` exits 1 with a stderr line, so the transcript shows a hook error on each result it does not redact;
    - the others stop silently, including `restore-authorization-boundary-on-compact.sh` and `advance-past-commit-stall.sh`;
    - tools with no gate registration (for example Grep, Glob, WebFetch, WebSearch, NotebookEdit and MCP tools) stay usable, unredacted, for the whole lockout;
    - each plugin `_lib.sh` carries the same check;
    - code above that check, and above a hook's `_lib.sh` bootstrap, must parse and run on bash 3.2;
    - every gate registration sets `onFailure: "block"`, and every gate ends `exit 0`.
  - In § "Gate deadlock recovery", add one paragraph:
    - A below-floor bash, or a gate that cannot start, makes every gate block.
    - To recover, install a newer bash in a separate terminal, put it ahead of `/bin` on the PATH Claude Code starts with, and restart Claude Code. A headless run needs this done beforehand.
    - If `./install.sh` passes in a terminal but gates still deny, Claude Code was started with a different PATH. Start it from a shell where `command -v bash` is the new bash.
    - A gate that cannot start, aborts on an unset variable, is killed, prints invalid JSON, or times out blocks its own calls until a fix is pulled, or reverted from a separate terminal (M4's list; a failing command in the middle of a gate does not end it).
- `docs/hooks.md`'s `redact-credential-values.sh` entry: its "Fails open on any parse failure" sentence gains the source-failure exception (M3).
- `docs/scripts.md:136`: drop the bash 3.2 half of the sentence, keeping jq 1.6.
- `docs/security-hardening.md:857`: add one sentence. The trusted PATH binaries include `bash` itself, which every hook's shebang resolves through `env`.

**Not changed:** `claude/.claude/hooks/_lib.sh` code (comments only, in Phase 2), every stow gate bootstrap, `select-tests.py` rules, CI workflows, `pyproject.toml`, and `CLAUDE.md`.

## Verification

1. **After each dispatch:** run the three commands under Critical files. In Phase 1b's run, confirm that the printed selection for a plugin-hook change includes `claude/.claude/hooks/tests/` (row 71). If it does not, stop and raise it as a rule-table bug.
2. **Red-then-green records.** Report these in the dispatch returns and the PR body:
   - Phase 1a: the baseline run's red tests, then the sweep run's red tests and guard failures. The sweep set is the second minus the first, and it must equal the set of sites fixed. Then green with the sweep reversed. After the commit, `git status` and `git show --stat HEAD` show no shipped shell file. The sweep set covers PATHs that hold no bash only. Step 7 adds a Mac suite run for the rest.
   - Phase 1b: `test_shell_dialect.py` fails listing exactly 77 files, then passes.
   - Phase 1d: the record of guard checks 3 and 4 over every gate test path. It is expected empty, and any entry is a stop-and-report.
3. **Executable bits.** `git diff --summary origin/main...HEAD` lists no `mode change` lines.
4. **Residue after Phase 2.**
   - `git grep -nE 'bash[ -]?3|3\.2|bash[ -]?4|4\.4|test_no_bash4_constructs' -- '*.sh' install.sh 'claude/.local/bin/*' 'plugins/lovable-cloud/scripts/*' 'claude/.claude/scripts/*.py' 'claude/.claude/hooks/tests/*.py'`. The `scripts/*.py` pathspec crosses `/`, so it already reaches `scripts/tests/*.py`. The `hooks/tests` pathspec is new, so the `test_deny_reviewer_tree_mutation.py` docstring is covered. Only these hits remain:
     - `install.sh`'s bash 3.2/5.3 verification record;
     - `install.sh`'s new "must parse and run on bash 3.2" comment;
     - the floor-check comments in `_config.sh` and the four plugin `_lib.sh` copies;
     - unrelated numerals.
   - `git grep -nE "own hook timeout|does not block the tool|doesn't block the tool|fail-open timeout|timeout fails open" -- '*.sh'`. Each remaining hit's comment also states the `onFailure: "block"` behavior. The only exceptions:
     - `plugins/{lovable-cloud,npm-semver,plugin-semver}/hooks/_lib.sh:13-15` and `_lib.sh`'s PostToolUse lock comment near :4088, which make no block claim;
     - `require-ready-for-review.sh`'s capped-timeout lines.
5. **CI parity.** In the PR's CI log, the parity test in `test_bash_floor.py` shows PASSED, not SKIPPED.
6. **Full suite once.** CI runs the full suite on push. Locally, the scoped runs above suffice, and the phases that edit `helpers.py` already run the full suite.
7. **Engineer-run, before merge.** Record each result in the PR body as pass or fail, except where a step below says otherwise. Never record a raw `$PATH` or `$BASH`, a user count, per-user rows, an account count, or per-account rows; pool the Versions result into one statement.
   - **Versions.** On every machine and account where the engineer runs Claude Code:
     - `claude --version` must be 2.1.295 or newer;
     - in the shell that launches Claude Code, `env bash -c 'echo "$BASH_VERSION $BASH"'` must be 5.2 or newer.

     The bash check speaks only for the shell it ran in. Step 8's smoke test covers GUI and IDE launches. Fix any miss first. If a host cannot reach bash 5.2, stop and raise it, because that reopens row 79.
   - **`onFailure` probe.** Run probe C before the Phase 2 dispatch, because Phase 2's redactor wording depends on it. The session writes a script that builds scratch projects outside this repo, and the engineer runs each in an interactive session. Each project's `.claude/settings.json` registers one probe on `Read`:
     - A: a PreToolUse hook with `"onFailure": "block"` that prints a line to stderr and exits 1;
     - B: a PreToolUse hook with `"onFailure": "block"` whose shebang is `#!/usr/bin/env nonexistent-interpreter`;
     - C: a PostToolUse hook without the field that prints a line to stderr and exits 1, plus a `permissions.deny` rule for one dummy path as a whole-file canary.

     - D: a PreToolUse hook with no `onFailure`, shebang `#!/usr/bin/env bash`, that is not a gate and always allows. It writes `$BASH_VERSION`, `command -v bash` and `$PATH` to a fresh file under `/tmp`. The engineer opens project D from each launch route used (terminal, desktop app, IDE) and makes one tool call. Expected: 5.2 or newer on every route. The PR body records only pass or fail per route and the `$BASH_VERSION`, never the raw `$PATH`. This settles G2 and row 21 before the pull, and step 8 stays as the after-pull confirmation.

     Expected on the current CLI:
     - A and B block the Read, and the reason carries "blocking because onFailure is "block"" and the hook's stderr;
     - C lets the Read through and shows a hook-error notice carrying the stderr line;
     - the deny rule still denies its path.

     Probe C has a failure branch. A missing default-view notice, or a withheld or replaced result, is a stop-and-ask that reopens row 76, not a recorded "expected". The README, CHANGELOG and `docs/hooks.md` sentences about the redactor are worded from the observed result. The redactor header's and `track-permission-prompts.sh:35`'s "exiting non-zero would break the triggering tool call" are made consistent with it in the same change.

     Classifier check: feed the block record that A and B produced to `hook_denial_key`. If it returns a denial attributed to the hook, record that in the PR body. If it returns None, the Out-of-scope bullet on `onFailure` blocks stands (the extension needs a record shape observed here first, so it is not the cheap option).

     **Old-CLI arm.** Before sending the message, the engineer writes the recipient list in private notes, outside the repo and the PR. Replies are checked against that list. A recipient who has not replied is treated as possibly below 2.1.295, never as current. Merge waits for every recipient's reply, or the arm is accepted unrun with the engineer named in the PR body as having accepted it. The PR body states only that the arm was run, was accepted unrun, or was not needed, and carries no list, count or names. If any user reports, or might be on, a CLI below 2.1.295, run A and C on the oldest CLI the engineer can reach, against three surfaces: a user-scope scratch `CLAUDE_CONFIG_DIR`, a project `settings.json`, and a scratch local-marketplace plugin whose `hooks.json` carries the field. Expected: `/hooks` lists the hooks, the deny rule applies, and A does not block. A settings error, a skipped file or a missing rule on any surface is a stop-and-ask, because it would drop every hook and permission rule (or a whole plugin) on `git pull` or update. If every recipient has replied with a current CLI, record that this arm was not needed.
   - **Real 3.2, on one Mac, from the worktree:**
     - `/bin/bash -c '. ./claude/.claude/hooks/_config.sh; echo "status=$?"'` should print the floor message and `status=1`.
     - `printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | /bin/bash ./claude/.claude/hooks/deny-credential-file-reads.sh; echo "exit=$?"` should name "could not source _lib.sh" and print `exit=2`.
     - The same source check on `./plugins/npm-semver/hooks/_lib.sh` should print `status=1`.
     - `HOME="$(mktemp -d)" /bin/bash ./install.sh; echo "exit=$?"` should print the Darwin section of the floor message, naming `brew install bash`, the `--prefix` confirm command and the explicit `./install.sh` re-run, and none of the Linux section; then `exit=1`, with nothing created under the scratch `HOME`. `install.sh` is the surface a below-floor user meets first, and the suite's tests run its block on bash 5.x only. The scratch `HOME` keeps a misplaced block from touching the real one. The engineer reads the Darwin text on the Mac and judges that the steps work as written.
     - With Homebrew's bash installed, run `PATH="$(brew --prefix)/bin:$PATH" env bash --version` and `PATH="$(brew --prefix)/bin:$PATH" command -v bash` (read-only), then the message's re-run, `PATH="$(brew --prefix)/bin:$PATH" ./install.sh` from the repo root, expecting it to pass the floor check.
     - Engineer-run: on `/bin/bash` 3.2 with a stub `uname` printing `Linux` first on PATH, run `./install.sh` and read the Other section once.

     These are the only real-3.2 evidence.
   - **Mac suite run.** After Phase 1c, on the same Mac with Homebrew bash first on PATH, run `select-tests.py`. Expected green. A red is a site of the shape the Linux sweep cannot see (a PATH holding a system directory), so it is fixed with `link_bash_into`.
   - **Tell users.** Send the CHANGELOG migration text to the other users and to the plugin consumers (rows 81, 88). Ask each for their `claude --version`, which feeds the probe's old-CLI arm, and count a non-reply as unknown (see the arm above). Ask each to run step 8's smoke test after they pull.
8. **After merge (the engineer, and each user).** On each machine, pull from a separate terminal, not from inside a session. Then, for each way Claude Code is launched there (terminal, desktop app, IDE), start a fresh session that way and make one gated call, such as a Read of a harmless file. Expect no "could not source _lib.sh" deny. `/hooks` lists the gate registrations.
9. **Prose facts.** Before Phase 2 names any distribution's bash version, confirm it from that distribution's package index. Leave it out if unconfirmed.
10. **Reviews.**
    - `/code-review` runs on each commit.
    - `plugin-semver` is hook-enforced for the four plugins from Phase 1b on.
    - `ai-instruction-and-memory-files` runs on `.claude/rules/shell-floor.md`. The dispatcher invokes it, and no hook enforces it.
    - No SKILL.md or agent file changes, so no `/skill-review` or `/agent-review` runs.
11. **Rollback.** The PR lands as one squash-merged commit, and reverting it is live on `git pull` for stow users. Plugins roll forward only: a revert lowers their versions, which the version-bump gate rejects, so a plugin rollback ships as a new major version.

## Out of scope

- **Rewriting the other 3.2 workarounds** (count guards, parallel arrays, `tr` lowercasing, while-read loops, `LC_ALL=C` pins), and taking a census of them. This follows rows 8-9. M6's one-line comments mark them as redundant on the floor, so in-file cleanup can take them later.
- **A per-gate deny channel naming the floor (round-1 M3).** M3 reaches the cause through install.sh, the gate deny, and the plugin deny text.
- **A session-start notice (row 73), and a visible failure in non-gate hooks other than the redactor (row 77).** Below the floor, every tool with no gate registration stays usable for the whole lockout, until the user fixes PATH and restarts: for example Grep, Glob, WebFetch, WebSearch, NotebookEdit and MCP tools. The redactor now reports a hook error on each result it does not redact (M3). `restore-authorization-boundary-on-compact.sh` and `advance-past-commit-stall.sh` stay silent, and `restore-authorization-boundary-on-compact.sh` is advisory only (its header says it cannot block a tool call). While every Bash, Read, Edit, Write, MultiEdit, ExitPlanMode and Agent or Task call is denied, no commit or irreversible action can run through those gated tools, so their output has nothing to act on. That sentence covers the gated matchers only. An ungated MCP tool that can publish or merge remains reachable, and the plan accepts that residual for a small user base. The two ask hooks share the Edit|Write|MultiEdit matcher with five gates that deny at their bootstrap, so their silence changes nothing.
- **A bash-3.2 lint over the region above each gate's bootstrap (ciso-reviewer round 1).** M4 blocks a crash there, and anything that does not crash reaches M2's check. Row 61's syntax-error fact supports this. This reasoning holds only where M4 is honored, which needs Claude Code 2.1.295 or newer. On an older CLI, a future bash-4 construct there would fail open.
- **`onFailure` on non-gate hooks.** An informational hook has no deny path, so blocking on its failure gains no protection. For the two PreToolUse ask hooks, failing open is the defined posture, because an ask cannot deny.
- **A distinct transcript-analysis cause for a below-floor deny (staff-product-engineer round 1).** The `lib-source` remedy, `./install.sh`, now names the cause or the PATH mismatch.
- **Counting `onFailure` blocks in transcript analysis (staff-platform-engineer and staff-product-engineer, round 3).** The block reason (row 72) matches none of `_HOOK_DENIAL_SIGNATURE`'s three alternatives by reading, so `--deny-summary` probably does not count a gate that starts crashing or timing out after merge. Extending `denials.py` needs a test and its own cause kind, and the record shape the current CLI writes for such a block has not been observed, so the extension is not the cheap option. The substitute is to grep a transcript for the notice text `blocking because onFailure is "block"`. Verification 7's classifier check records whether the shape happens to match.
- **A Claude Code version check in `install.sh`** (M4).
- **A floor check in `install-dev.sh`.** Contributors see the floor message in test failures.
- **A CI leg at bash 5.1, or a macOS job running `/bin/bash` 3.2.** The floor equals CI (rows 32, 79). A macOS job would keep the 3.2-parseable regions honest, but it is optional. A floor block that misparses on 3.2 still makes gates deny (row 30), and Verification 7 supplies one-time 3.2 evidence.
- **A drift test for the floor numbers in README.md, CHANGELOG.md and plugin descriptions.** The python floor has the same shape and has none. The five copies' constants are pinned, and each plugin deny's number is pinned to its constants (Phase 1c).
- **Exit codes of the other bare `_lib.sh`/`_config.sh` sourcers below the floor.** They exit through `set -e` or fail toward off (ciso-reviewer round 1). `migrate-legacy-config.sh` exits 1 through `set -e`. Only `config-get.sh` documents a code for this case.
- **A runtime floor check in the files that source no library:** the nine scripts in row 84, the two plugin non-gates, `statusline-command.sh`, `scripts/`, and `plugins/lovable-cloud/scripts/`. No hook or plugin uses a bash-4+ construct today (row 36). A future construct in these files fails on an old bash with a visible syntax error or "command not found", outside any gate. A reach test beside `test_shell_dialect.py` could enforce reach later.
- **Converting the 19 existing inline bash links (row 59) to `link_bash_into`.** They already carry bash, and converting working test code is churn the Ask does not call for.
- **`Popen` and `shell=True` hook runs.** The shell-exec guard wraps `subprocess.run` only. No gate's own test file uses `Popen` (row 86), and Phase 1a's temporary sweep catches a red there.
- **`claude/.claude/agents/staff-platform-engineer.md:41`.** It is a global reviewer serving every repository, and its "bash 3.2 vs 5.x" check stays valid elsewhere. `.claude/rules/shell-floor.md` covers this repo.
- **The `#!/bin/bash` template in `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:39`.** It serves hooks in any consuming repository, whose floor this repo does not set. This repo's dialect test catches a hook copied from it.
- **Shell files under `tests/` paths, and `#!/bin/bash` stubs that tests write at runtime.** They never ship and do not source `_lib.sh`.
- **Measuring the extra `env` exec on each hook invocation.** It is one exec per hook.
- **Historical records:** past CHANGELOG entries, `.claude/plans/*`, and `docs/design-decisions/**`, including timeout statements there.
- **The statusline entry** (`settings.json:548`). It already resolves `bash` from PATH.
- **Machine-provisioning tooling outside this repo.** README.md and `install.sh` state the prerequisite themselves.
- **A follow-up issue.** Nothing is deferred that needs tracking.

**Not verified by the architect:**
- How a CLI older than 2.1.295 treats `onFailure` (row 65). It is inferred from ciso-reviewer's schema read, and Verification 7's probe settles it.
- That a conftest's autouse fixture installs the guard in its tree, including under `pytest-xdist` workers (M9). The self-test cannot show it, because it sits in a fourth tree. Each guarded tree's wiring test does, and deleting one conftest's fixture on a scratch checkout must turn exactly one test red.
- That the redactor's hook-error notice is visible in the default view and leaves the result intact (row 78). Verification 7's probe C settles it and has a failure branch.
- That CI's Lint step includes `plugins/` (staff-platform-engineer's read of the workflow).
- CI's exact bash version (row 34).
- GitHub's retirement timing for `ubuntu-22.04`, and the bash versions in row 35.
- Behavior on a real bash 3.2 (rows 30 and 61). Verification step 7 covers the check itself.
