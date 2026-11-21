# Re-link the personal `claude-skills` package when the checkout is relocated, with exit code 3 and a backup-dir guard

## Context

Goal: make `relocate-claude-config` re-link the personal `claude-skills` package and report follow-up failures with a new exit code, after linking the new checkout's `claude` package. The script names the one non-`claude` row itself, and a test pins it to `stow-packages.sh`. This plan is PR 1 of three. Re-pointing the declared accounts in `~/.claude/transcript-config-dirs` is PR 2, and `git worktree repair` is PR 3. The three together deliver the full goal in the Ask.

Ask: "The move must re-link every Claude config account on the machine programmatically, not by hand." (engineer, handoff goal), plus "Reuse transcript-config-dirs (Recommended)" and "Both packages, both paths (Recommended)" (option labels the engineer selected this session), plus "Architect's design, 2 PRs (Recommended)" (option label the engineer selected after an architect consult). The engineer then answered the size question for the revised plan with "Split now". After a second architect consult the engineer selected "Hardcode row + drift test (Recommended)", which supersedes the earlier row-validation, header-reword and trust-the-typed-path answers. Asked whether that label retires the name-validation, hooks-gate and trust-the-typed-path answers, the engineer selected "Yes, retire all three (Recommended)". After three plan-review rounds the engineer selected "Cut the README recipe (Recommended)" and, asked whether their own rename waits for PR 2 and PR 3, "Wait for PR 2 and PR 3". PR 1 covers the follow-up runner with exit code 3, the personal `claude-skills` re-link, the backup-dir guard and one CHANGELOG bullet. PR 2, the declared-account re-point, and PR 3, `git worktree repair` over the moved checkout's linked worktrees, each get their own plan and land after PR 1.

Why now: after the repo rename, the checkout will move from its old location to a new one. `relocate-claude-config` re-stows only `~/.claude` and `~/.local/bin`, and only the `claude` package. A secondary account's config dir keeps symlinks into the old path and breaks after `mv`, and `~/.claude/skills` (the `claude-skills` package) dangles. Any later stow run from the new location also treats those stale links as conflicts and aborts.

Intended outcome:

- The personal account also re-links `claude-skills` from a constant in the script, after quarantining its stale fold into a backup dir. A test fails when the constant differs from the non-`claude` rows `stow-packages.sh` prints.
- Declared accounts in `~/.claude/transcript-config-dirs` are not touched in this PR. PR 2 re-points their stale links, and the README lists them as manual until then.
- A failed follow-up exits with new code 3, and `--repair <new-path>` is the retry.
- The run executes nothing new from `<new-path>`.
- A failed row unit does not stop the run: `finish_relocation` still runs. The run then exits 3.
- Both modes now refuse a backup dir that exists and is a symlink or is not a directory before anything changes, where only `--repair` refused, and only a symlink. The cohort is users who symlinked the backup dir on purpose, such as to another volume, whom the primary form now blocks (M10).
- `--repair` now exits 1 before the `claude` stow when moving a dangling link into the backup dir fails, where it went on after a failed move. The cohort is users with an unwritable backup dir or an unrelated dangling link, such as a `projects` link to an unmounted disk, whose `~/.claude/hooks` stays unlinked until the backup dir is fixed (M1).
- The closing text and README list what the script does not do for the user (see Critical files, `README.md`).
- The README "Repo relocation" section, `docs/scripts.md` and `CHANGELOG.md` are updated.

## Approach

The design is the Intended outcome above. After the `claude` stow and before `finish_relocation`, both modes re-link each row of a constant in the script, `RELINK_PACKAGES`, which holds only `claude-skills` today (M2). Each row's unit refuses a symlinked target dir and a real directory at any of the package's top-level paths, moves a dangling link at one into the backup dir, then stows the package with no `--adopt` on its command line (M1). A failed unit does not stop the run, which ends with exit 3 and a `--repair` retry line (M8). A test fails when the constant differs from the non-`claude` rows `stow-packages.sh` prints (M2). Both modes refuse a backup dir that is a symlink or not a directory before anything changes (M10).

Mechanism numbers skip M3-M7, which moved to PR 2 (Out of scope), M12, the ordering check the engineer dropped (row53), and M13, the README's hand recipe for other accounts, which the engineer cut (row85). M2 names the hardcoded row, which replaced the package listing (row57, row58). The personal `claude` package keeps its unstow, `mv`, `stow --adopt` sequence (`relocate-claude-config.sh:428-441`). `--repair`'s depth-1 sweep (`:325-347`) keeps its reach and now moves each link through the per-link helper the row unit also calls (M1).

**Root problem.** `relocate-claude-config` re-links only the `claude` package into `$HOME`, so after the `mv` the `claude-skills` fold at `~/.claude/skills` still points at the old path, and a later stow from the new location refuses on that stale link.

**Givens** (fixed, outside this design's reach):

- **G1 — the repo depends on no other repository, private ones included.** A repo rule owned by `CLAUDE.md`. So the README names no out-of-repo installer for the accounts it leaves manual. [verified: `CLAUDE.md` § "Working in this repo"]
- **G2 — a peer branch owns CI config, `CODE_OF_CONDUCT`/`CONTRIBUTING`/`SECURITY`, `claude/.claude/hooks/_lib.sh`, `deny-private-project-refs.sh`, `require-stow-reminder.sh` and their tests, and edits `README.md` only on URL lines.** Another party owns them, so this plan's README edit stays inside "Repo relocation". [unverified: relayed from the handoff by the dispatcher, not read this session]
- **G3 — GNU Stow semantics.** Vendor behavior, read in the installed Stow 2.4.1; rows 4, 5 and 22 state what this design relies on. [verified: GNU Stow 2.4.1 `Stow.pm` and its `stow` script]

**Rows:**

1. Today only the `claude` package is unstowed and re-stowed (`relocate-claude-config.sh:429, 435, 441, 473`), while `stow-packages.sh` lists a second package, `claude-skills`, targeting `~/.claude` (`stow-packages.sh:24-27`), so `~/.claude/skills` dangles after a move. [verified: both files]
2. `~/.claude/skills` is one folded link into `claude-skills/skills`: `install.sh` pre-creates only `~/.local/bin` and `~/.claude` before stowing, and exits 1 rather than stow `claude-skills` over a real `~/.claude/skills`. [verified: `install.sh:138, 143, 336-357, 445-465`]
3. `stow-packages.sh` is the package list's single source of truth. It exits 1 when a listed package directory is missing. It self-locates through `readlink -f "$0"`, so running it by its path inside a checkout lists that checkout's rows. Its header and its `docs/scripts.md` entry name two readers, `install.sh` and an installer for a non-`$HOME` config directory. [verified: `stow-packages.sh:4-8, 18-21, 29-34`; `docs/scripts.md:486`]
4. Stow counts an existing link as its own when the text is relative and, joined to the link's parent, falls under its stow path, or when it falls under a directory holding a `.stow` marker. It skips an owned link whose text is byte-identical. It replaces an owned link that does not resolve rather than refusing it. It treats a link it does not own as a conflict, so a stale link into the old checkout blocks a stow from the new one. The unit quarantines a dangling link before it stows, so the plan's behavior is the same either way. [verified: GNU Stow 2.4.1 `Stow.pm:531-601, 1017-1053, 1070-1080, 1110-1128`]
5. GNU Stow plans every link first and, on any conflict, prints "All operations aborted." and exits 1 with nothing changed, so a refused `claude` stow links nothing. The `claude` stow runs as a plain statement under `set -e`, so the script then exits before the first follow-up. [verified: Stow 2.4.1 `stow` script:479-497; `relocate-claude-config.sh:38, 441, 473, 517-521, 528-529`]
6. `install.sh` stows every non-`claude` row without `--adopt` and with a `.DS_Store` ignore, which it keeps in a shell variable inside its own stow block. It skips a row whose package field is empty, and it exits 1 when no row names `claude`. [verified: `install.sh:418-428, 445-461`]
7. All six existing `stow` calls pass the package name with no `--` before it. [verified: `relocate-claude-config.sh:429, 435, 441, 473`; `install.sh:451, 455`]
8. The script runs as a real-file copy at `~/.local/bin/relocate-claude-config`. Only `./install.sh` and the script's own `finish_relocation` refresh that copy. Its contract is not to depend on `~/.claude` being intact, so nothing it loads may come from beside `$0`. [verified: `relocate-claude-config.sh:1-14, 396-401`]
9. `finish_relocation` rewrites `~/.claude-config-source` to the new path, and `--repair` never reads that manifest, so a later `--repair` run has no record of the previous location. [verified: `relocate-claude-config.sh:396-401, 459-481`]
10. `--repair` checks that `$BACKUP_DIR` is not a symlink, then moves a dangling tree-folded `~/.claude` and every dangling direct child of `~/.claude` and `~/.local/bin` to `$BACKUP_DIR/<name>.$$` before re-stowing. Its child glob skips dot-prefixed entries. Primary mode does neither today. [verified: `relocate-claude-config.sh:283-347, 403-481`]
11. Those moves do not check whether `$BACKUP_DIR/<name>.$$` already exists. [verified: `relocate-claude-config.sh:308-310, 331-333`] `mv` onto a destination that is a symlink to a directory moves the source into that directory. No-clobber `-n` does not change that, and BSD `mv` lacks GNU's `-T`, which would. [unverified: traced from `mv`'s documented semantics by the ciso-reviewer and this plan, not run]
12. The `mv`-failure branch re-stows only `claude` at the old location. The unconditional `stow -D claude` before the `mv` aborts under `set -e` with nothing changed. [verified: `relocate-claude-config.sh:428-437`]
13. The existing relocate tests build a fake checkout with no `claude-skills/` and no `stow-packages.sh` (`test_relocate_claude_config.py:29-51`). `TestClaudeCliMissing` builds its own `PATH` from a fixed tool list that has no `env`, and asserts exit 0 (`:532-567`). Two tests read `<name>.<pid>` entries at the top of the backup dir (`:397-399, 428-432`). No test sources the script or asserts a quarantine message's text. [verified: `test_relocate_claude_config.py`]
14. `--repair` accepts `<new-path>` on `repo_looks_legitimate`'s one-file canary alone. Primary mode accepts a bare manifest when no live `~/.claude` probe exists. Both modes run `stow --adopt` from `<new-path>`, and the header names `<new-path>` as semi-trusted. So `<new-path>`'s `stow-packages.sh` is destination-controlled code, and its rows are destination-controlled data. [verified: `relocate-claude-config.sh:16-25, 77-79, 199-202, 441, 473`] Superseded by row 57 for its last sentence, since the run no longer runs or reads `<new-path>`'s `stow-packages.sh`. Row 18 and M2 still rely on its canary and `--adopt` facts.
15. Covered how `_resolve_live_claude_repo_dir` reads `~/.claude/hooks`, for the dropped hooks-resolve check. Superseded by row 57. [verified: `relocate-claude-config.sh:131-154, 199-202`]
16. The `claude` stow from `<new-path>` links that checkout's `settings.json`, hooks and `~/.local/bin` wrappers into `$HOME` before any follow-up runs. `finish_relocation` then installs `<new-path>`'s copy of this script into `~/.local/bin`. [verified: `claude/.claude/settings.json`; `relocate-claude-config.sh:400, 441, 473`] Row 70 relies on it.
17. Covered a real `~/.claude/hooks` holding per-file links, for the dropped hooks-resolve check. Superseded by row 57, and row 72 carries its Stow merge fact. [verified: GNU Stow 2.4.1 `Stow.pm:604-647`; `install.sh:337-357, 445-461`]
18. In `--repair`, a crafted destination that passes the canary can make `stow --adopt` adopt real files from `$HOME` into it, and this plan adds no check for that. [verified: the canary and the `--adopt` call sites, row 14] [unverified: that a crafted destination can adopt them is the consult's reading, not tested]
19. Bash ignores errexit for every command inside a function or subshell run as an `if` condition, even one that runs `set -e` itself. A function whose body is a subshell `( … )` runs in a child shell, so an `exit` in it, an unbound variable it trips and its `shopt` changes all end with it. [verified: `claude/.claude/rules/shell-script-conventions.md`'s `set -euo pipefail` bullet states the `if` case] [unverified: the rest, from the Bash manual, not read this session]
20. Covered `.` and `..` passing a plain-name rule, for the dropped row validation. Superseded by row 57. [unverified]
21. Script output can enter agent transcripts and terminals: the header names an agent as a possible caller. [verified: `relocate-claude-config.sh:16-19`] Bash 5.3's `printf '%q'` prints its argument in a form bash reads back, escaping ESC as `\E`, CR as `\r` and newline as `\n`. [verified: ciso-reviewer scratch run, bash 5.3.20, `C` and `en_US.UTF-8` locales] For a path holding a space and a single quote, Python's `shlex.split` reads `%q`'s form and a double-quoted form to the same words. It also expands no `$`, which bash expands inside double quotes, where `%q` prints `\$`. So only bash, reading the line, tells `%q` from double quoting. [verified: staff-sdet scratch run, bash 5.3.20; CI's bash 5.2 not run]
22. Stow reads options from `.stowrc` in its working directory and from `$HOME/.stowrc`, merged under the command line, and has no option to skip them. A boolean such as `--adopt` from a resource file has no command-line negation. When neither the command line nor a resource file gives `--dir`, `STOW_DIR` wins over the working directory. [verified: Stow 2.4.1 `stow` script:350-361, 517-545, 573-579, 629-634, 675-691]
23. `README.md` lists the `~/.local/bin` copy of `relocate-claude-config` among what only the installer produces, so a `git pull` alone does not refresh it. A relocation run also refreshes it (row 8). [verified: `README.md:101`, `relocate-claude-config.sh:396-401`]
24. Both stow packages are re-linked on both paths. [engineer-verified: "Both packages, both paths (Recommended)"]
25. "Both paths" means the personal `~/.claude` path and the dirs declared in the roots file. This is the plan's reading of the label, whose option description was the dispatcher's proposal. This plan covers the personal path, and PR 2 the declared dirs. [unverified]
26. The goal is programmatic re-linking of every account. [engineer-verified: "The move must re-link every Claude config account on the machine programmatically, not by hand."]
27. "Every account" in row 26 means every account declared in the roots file. [engineer-verified: "Declared accounts only (Recommended)"]
28. The engineer chose the two-PR design. [engineer-verified: "Architect's design, 2 PRs (Recommended)"]
29. That design re-points declared dirs' stale links in place, so a declared dir gets back its previous links and no entry tracked since. It drops the shared ignore-arg lib refactor and the `install.sh` edit. This is the dispatcher's summary of the consult the label selected, not the engineer's words. [unverified]
30. A new exit code reports a follow-up that needs attention. [engineer-verified: "New exit code 3"]
31. The change carries a changelog entry. [engineer-verified: "CHANGELOG.md entry"]
32. Untracked content nested inside a tracked package directory stays visible through a folded link, as it is today. [engineer-verified: "Keep as is (Recommended)"]
33. Handoff constraints: leave dirs with no links into the old checkout alone; no roots file means no change to any secondary dir; quarantine, don't delete. [unverified: relayed from the handoff, an agent-authored file, not read this session]
34. Earlier revisions of this plan, at a `Projected diff:` of about 580 and then about 760 lines, proceeded as one PR above the 400-line threshold. [engineer-verified: "Proceed anyway"] That answer covered those sizes only.
35. The engineer answered the size question for the about-1200-line revision by splitting the work. [engineer-verified: "Split now"]
36. The engineer confirmed the three-PR split. [engineer-verified: "Yes, three PRs"]
37. Which work each PR holds is the breakdown in Context, which is the dispatcher's relay, not the engineer's words. This plan reads row 36 as replacing row 28's PR count. [unverified]
38. The `stow` call for a listed package carries no `--` before the package name, and plain-name validation is the protection. [engineer-verified: "Drop it, keep plain-name validation"] Row 69 retires its validation half, since the package name is a constant. The call still carries no `--` (row7).
39. `register-marketplace` registers this repo's marketplace for the profile `CLAUDE_CONFIG_DIR` names. It re-registers a recorded path that differs from its own checkout, and it does nothing for a profile with no `settings.json`. [verified: `register-marketplace.sh:4-6, 53-57, 73-94`; `README.md:238`] A `settings.json` that is a stowed link into the old checkout fails the `-f` test the same way. [verified: `register-marketplace.sh:54-56`] Whether any secondary account holds a registration is not checked. [unverified]
40. The hooks-resolve gate and the package and target validation stay in PR 1. [engineer-verified: "Hooks gate and row validation"] Retired by row 69.
41. The `stow-packages.sh` header reword and its `docs/scripts.md` bullet stay in PR 1. [engineer-verified: "stow-packages.sh header and docs bullet"] Superseded by row 57. Both files are still edited, now to name this script's copy of the row.
42. Run in the main worktree with each linked worktree's new path, `git worktree repair` re-establishes the links in both directions when the main and linked worktrees both moved. The README's manual step relies on it. [verified: git-worktree man page, repair entry]
43. The run trusts the path the operator typed, and the plan, header and README say so. [engineer-verified: "Trust the typed path, say so (Recommended)"] Retired by row 69: the header and README gain no trust sentence, and PR 2's plan carries the trust statement (Out of scope).
44. Relayed the trust option's description, under which the destination's `stow-packages.sh` ran. Superseded by row 57. [unverified]
45. The engineer left the quarantine choice to the architect. [engineer-verified: "Ask the architect"]
46. The architect consult chose one per-link helper, extracted from the existing sweep's loop body, that the sweep and each listed row both call, writing `$BACKUP_DIR/<name>.$$` in both modes. This row is the session's relay of that consult, not the engineer's words. [unverified]
47. Covered how `IFS=$'\t' read -r` splits rows, for the dropped row reader. Superseded by row 57. [verified: staff-sdet and ciso-reviewer scratch runs, bash 5.3]
48. Covered validator locale behavior, dropped with row validation. Superseded by row 57. [verified: ciso-reviewer and staff-sdet scratch runs, bash 5.3.20]
49. Covered `env -i` environment semantics, for the dropped listing run. Superseded by row 57. [unverified]
50. Covered `stow -v` writing `LINK:` lines to stderr, for the dropped escaping of listed names. Superseded by row 57. [verified: staff-sdet scratch run, Stow 2.4.1]
51. `$$` in a subshell expands to the parent shell's pid, so the sweep and every row unit in one run share one `<name>.$$` namespace. [unverified: the Bash manual, not read this session]
52. Covered `find -L`'s listing of links that do not resolve, for the README recipe the engineer cut. Superseded by row 85. [unverified]
53. The hooks-resolve ordering check is dropped. [engineer-verified: "Drop the check (Recommended)"] Superseded by row 57: with no listing, nothing needs ordering.
54. Scoped row 53's answer to row 40's hooks gate. Superseded by row 57. [unverified]
55. The revision that drops the ordering check proceeds as one PR above the 400-line threshold. [engineer-verified: "Proceed anyway"] Superseded by row 57.
56. Read row 55's answer as covering a re-estimated size. Superseded by row 57. [unverified]
57. The engineer chose to hardcode the row, with a drift test, as PR 1's design. [engineer-verified: "Hardcode row + drift test (Recommended)"]
58. What that label selected is the architect consult's design, relayed by the session: a constant holding the one non-`claude` row, a per-row quarantine and plain stow, the exit-3 runner and its retry line. It drops the listing, row validation, `env -i`, the locale pin, `%q` escaping of listed names and the trust prose. This row is the session's relay of the consult and of the option's description, not the engineer's words. [unverified]
59. The engineer answered the size question for the revision estimated at about 850 lines by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covered that estimate only.
60. The engineer deferred the `STOW_DIR` and `.stowrc` `--dir` exposure to a follow-up. [engineer-verified: "Yes, defer to the follow-up (Recommended)"]
61. Which question row 60's label answered is the dispatcher's relay. With no listing step, the deferral now covers only the exposure every stow call here and in `install.sh` already shares (row22). [unverified]
62. `install.sh` names `claude-skills` literally, and this script names the `claude` package literally in all four of its `stow` calls. [verified: `install.sh:452`; `relocate-claude-config.sh:429, 435, 441, 473`]
63. A package new to `stow-packages.sh` reaches `$HOME` only through `./install.sh`, and the same run refreshes `~/.local/bin/relocate-claude-config`. So the installed copy's constant lags the checkout's list only while the new package is also unstowed. [verified: `install.sh:440-457, 480`; `README.md:101`]
64. The guard at `:528` runs `main` only when `BASH_SOURCE[0]` equals `$0`, so sourcing the script from `bash -c` defines its globals and functions without running `main`. Its top level reads `$HOME` under `set -u`, so the caller sets `HOME`. [verified: `relocate-claude-config.sh:38-42, 524-530`]
65. `claude-skills/` holds only `skills/` at its top level. [verified: a grep for any file under `claude-skills/` outside `skills/` found none]
66. Both callers of `_quarantine_dangling_children` put it on the left of `&&`, so errexit is already off inside it, and a failed `mv` there does not stop the run today. [verified: `relocate-claude-config.sh:337, 341`; `shell-script-conventions.md`'s `set -euo pipefail` bullet]
67. Without `--adopt`, stow refuses the whole invocation when a package path collides with a real file at the target. [verified: `install.sh:382-384`] With `--adopt`, stow moves that file into the package and links it in its place. [verified: staff-sdet scratch run, Stow 2.4.1: plain `stow` over a real `skills/example/SKILL.md` exits 1 and leaves the file, and `--adopt` exits 0, turns the file into a link and moves its content into the package]
68. A checkout with no `claude-skills/` directory makes the row's stow fail: Stow prints that the stow directory does not contain the package and exits 2. [verified: staff-sdet and staff-platform-engineer scratch runs, Stow 2.4.1] The run then exits 3 with `claude` linked, which a test pins in each mode.
69. The engineer answered the session's question whether "Hardcode row + drift test (Recommended)" retires the name-validation, hooks-gate and trust-the-typed-path answers. [engineer-verified: "Yes, retire all three (Recommended)"]
70. PR 1 leaves the run's trust root as it is. Both modes already run `stow --adopt` on `<new-path>`'s `claude` package and install `<new-path>`'s copy of this script (row14, row16). [verified: `relocate-claude-config.sh:400, 441, 473`] The one new stow from `<new-path>`, of `claude-skills`, gives a crafted destination nothing the `claude` stow's hooks do not already give it. [unverified: an architect consult's reading, relayed by the session; the ciso-reviewer's trace of the new stow site agrees]
71. After `install.sh` refuses a real `~/.claude/skills`, it has already stowed `claude`, and it exits 1 before it writes the manifest. A later primary run still finds the checkout, through the live `~/.claude/hooks` probe when no manifest exists, and reaches the row unit with `~/.claude/skills` still a real directory. [verified: `install.sh:449-465, 475`; `relocate-claude-config.sh:144-151, 199-207`]
72. Plain `stow` merges per-entry links into an existing real directory at a path the package would fold. `install.sh` refuses to create that state at `~/.claude/skills`, so its stow never merges links alongside real content there. [verified: GNU Stow 2.4.1 `Stow.pm:604-647`, read for row 17; `install.sh:332-335, 349-353, 452-453`]
73. `_ensure_backup_dir_is_not_a_symlink` tests only `-L`. So a regular file at `$BACKUP_DIR` passes it, and the quarantine's `mkdir -p` then fails. [verified: `relocate-claude-config.sh:283-288, 308`]
74. An unwritable real directory at `$BACKUP_DIR` passes that guard and makes the `mv` into it fail. The existing mv-failure test forces a failure the same way, with `chmod(0o555)`. [verified: `test_relocate_claude_config.py:296-306`] `mkdir -p` on that existing directory exits 0, and the `mv` into it exits 1. [verified: staff-sdet scratch run, macOS]
75. Under `set -u`, bash 5.3.20 expands `"${name[@]}"` of an unset array to nothing without failing. Of the two versions run, only bash 3.2.57 reports it unbound. [verified: staff-platform-engineer scratch run; CI's bash 5.2 not run] `declare -p` of an unset name fails. [unverified: the Bash manual, not read this session; Verification 3's rename mutation pins it]
76. A change to any `.sh` file under `claude/.claude/scripts/` selects `claude/.claude/hooks/tests/` and `claude-skills/skills/tests/` as well as `claude/.claude/scripts/tests/`. So a drift test in `test_stow_packages.py` runs when either script changes. [verified: `select-tests.py:34, 36, 53, 615-617, 716-718, 982`] That file already holds `_manifest_rows()` and the manifest's other consumer pins. [verified: `claude/.claude/hooks/tests/test_stow_packages.py:1-10, 59-67`]
77. Covered `find -H` and `mktemp -d`, for the README recipe the engineer cut. Superseded by row 85. [verified: staff-platform-engineer and ciso-reviewer scratch runs, bfs 4.1.1, for `find`] [unverified: `mktemp`]
78. `read_manifest` skips a `~/.claude-config-source` that is not a regular file, and `write_manifest` redirects into it. So with a directory there, a primary run resolves the checkout through the live probe and then aborts inside `finish_relocation`, after the move. [verified: `relocate-claude-config.sh:83-93, 179-207, 396-401`] [unverified: that the redirect onto a directory fails, not run; the follow-ups-first test fails loudly if it does not]
79. The engineer answered the size question for the revision estimated at about 440 changed lines by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covers that estimate only.
80. `_stow_migration_lib.sh` refuses only a symlink at the same backup path, and its comment calls `_ensure_backup_dir_is_not_a_symlink` an identical guard. Outside the script itself, only that comment names the function. [verified: `_stow_migration_lib.sh:96-103`; a repo grep] A regular file at that path already makes the lib's later `mkdir` fail loudly. [unverified: the staff-platform-engineer's reading, not run]
81. A dangling link at `~/.claude/skills` makes `stow claude-skills` exit 1 and print `existing target is not owned by stow: skills` and `All operations aborted.` on stderr. [verified: staff-sdet scratch run, Stow 2.4.1] A live link Stow does not own takes the same conflict path (row4). [unverified: the same text for a live link, not run]
82. The engineer answered the size question again, at about 460 changed lines, by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covers this estimate only.
83. With `~/.claude` a live symlink into `<new-path>/claude/.claude` (the legacy tree-folded form), the `--repair` sweep leaves it alone and the `claude` stow skips it as owned. The row unit's `stow -t "$HOME/.claude"` would then write into the checkout's `claude` package, because Stow canonicalizes `-t` with `chdir` and `getcwd`, which resolves the symlink. [verified: GNU Stow 2.4.1 `Stow.pm:197-212`, `Stow/Util.pm:226-235`, read this session; `relocate-claude-config.sh:72-74, 329-335`] [verified: staff-platform-engineer scratch run, Stow 2.4.1: with `home/.claude` a link to `<checkout>/claude/.claude`, `stow -t home/.claude claude-skills` exited 0 and created `skills -> ../../claude-skills/skills` inside the checkout's `claude/.claude/`] The ciso-reviewer traced the same path by reading the two Stow files and ran nothing. Primary mode does not reach the state, because its `stow -D claude` removes an owned fold first. [unverified: traced by both reviewers, not run]
84. The engineer answered the size question again, at about 510 changed lines, by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covers this estimate only.
85. The engineer chose to cut the README recipe. [engineer-verified: "Cut the README recipe (Recommended)"]
86. What that label selected is the session's proposal, relayed by the session. It replaces the README's multi-step recipe for other accounts with one or two sentences saying their config dirs and linked worktrees stay manual. It drops M13, its Verification step, the README's paragraph on a symlinked `~/.claude` and recipe-specific CHANGELOG text. The question, asked after several review rounds, and the option's description were the session's wording, not the engineer's words. [unverified]
87. The engineer answered the session's question whether their own rename of the checkout waits for PR 2 and PR 3. [engineer-verified: "Wait for PR 2 and PR 3"]
88. Today's README "Repo relocation" section says nothing about other accounts' config dirs or linked worktrees. [verified: `README.md:447-463`] So PR 1's short manual-steps bullets give a stow consumer who relocates before PR 2 and PR 3 land more than today, and no move on the engineer's machine uses them before then (row87). [unverified: this plan's reading of rows 85 and 87]
89. The engineer answered the size question again, at about 495 changed lines, by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covers this estimate only.
90. The engineer answered the README cause-list question by choosing to name the live legacy tree-folded `~/.claude` state in one clause. [engineer-verified: "Add one clause (Recommended)"]
91. The engineer answered the size question again, at about 509 changed lines, by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covers this estimate only.
92. The engineer chose to add the tree-folded `--repair` backup-dir guard case. [engineer-verified: "Add the case (Recommended)"]
93. The engineer chose the architect's one-line CHANGELOG Migration fix over keeping the three sub-bullets. [engineer-verified: "Architect's one-line fix (Recommended)"] The wording is the architect's consult's, relayed by the session.
94. The engineer answered the size question again, at about 525 changed lines, by proceeding as one PR. [engineer-verified: "Proceed anyway"] That answer covers this estimate only.

**Mechanism justifications:**

- **M1 — per row, refuse a symlinked target dir and a real directory, quarantine dangling top-level links, then stow without `--adopt`** (anchors: row1, row2, row4, row6, row7, row8, row10, row12, row13, row33, row45, row46, row65, row66, row67, row71, row72, row74, row81, row83).
  - `_relink_package <new-path> <package> <target-dir>` is a function whose body is a subshell `( … )`. Its one-line comment states the unit's contract: the stow runs only once every top-level path passed its check and every move succeeded. It runs three steps:
    1. A `<target-dir>` that is itself a symlink fails the unit with one `err`, on the same failure path as the real-directory refusal in step 2. The `err` names the state: `~/.claude` is a symlink into the checkout, the legacy tree-folded form that the `--repair` sweep leaves in place. It says stow would write through that link into the checkout (row83). It also says the resolution before the `--repair` retry: replace the link with a real directory, move into it the untracked content the folded directory holds, then run the `--repair` command the output ends with. Removing the link alone would strand that content inside the checkout's `claude/.claude` (row32).
    2. Under `nullglob` and `dotglob`, it takes each top-level entry of `<new-path>/<package>`, with `<path>` as `<target-dir>/<entry name>`:
       - A `<path>` that is a directory and not a symlink fails the unit with one `err`. The `err` says the package is not merged into real content, which the user moves aside before the `--repair` retry (row71, row72).
       - Any other `<path>` goes to `_quarantine_if_dangling`. A return of 1, meaning not dangling, is not a failure.
    3. `( cd -- "$new_path" && stow -v --ignore='(^|/)\.DS_Store$' -t "$target_dir" "$package" )`, with no `--adopt`, as `install.sh:455` stows every non-`claude` row (row6, row67).
  - `_quarantine_if_dangling <path>` is the loop body of `_quarantine_dangling_children` (`:307-312`), extracted, and that loop now calls it (row45, row46).
    - It returns 1 when the path is not a symlink whose target is missing.
    - Otherwise it calls `_ensure_backup_dir_is_usable` (M10) and prints today's `quarantining dangling` line. Then it runs `mkdir -p -- "$BACKUP_DIR" && mv -- <path> "${BACKUP_DIR:?}/<basename>.$$"` as one checked step, and returns 0 once that step succeeds.
    - The `<name>.$$` layout stays, so the two existing backup-dir readers stay green (row13).
    - Errexit is off at every call site (row66, M8), so the helper checks that step itself. On a failure it prints one `err`, `could not move <path> into $BACKUP_DIR`, and runs `exit 1`. That wording differs from `mv`'s own diagnostic, so a test can tell the two apart.
    - In the `--repair` sweep, that `exit 1` ends the run before the `claude` stow. A failed move is a reported failure, not a silent partial quarantine, so the abort stays. Today the sweep goes on after a failed move (row66).
    - The accepted cost is a failed move of an unrelated dangling link, such as a `projects` link to an unmounted disk. `--repair` then stops before it restores `~/.claude/hooks` until the backup dir is fixed.
    - In a row unit the `exit 1` ends only the unit (M8).
    - The quarantine-instead-of-delete sentence of the comment at `:290-296` moves with the loop body to the helper's comment.
    - The comment at `:296-298` says `_quarantine_dangling_children` echoes true or false on stdout. Its last command, `$found`, sets its status instead, and the comment is corrected to say so.
    - The tree-folded branch of `quarantine_dangling_claude_entries` (`:329-335`) is unchanged.
  - Staleness is "a symlink that does not resolve", the rule the `--repair` sweep applies at the same depth (row10). Such a link at a path the package links blocks the stow either way (row4), so moving it is the least the stow needs. The move keeps the link (row33).
  - The symlinked-target refusal covers the one state where `--repair`'s sweep leaves a live fold in place and the stow would write through it (row83). Primary mode unstows an owned fold first.
  - The real-directory refusal applies `install.sh`'s rule for `~/.claude/skills` (row72) to a state a failed install leaves reachable (row71).
    - The test is `-d` together with `! -L`, because `-d` alone is true for a live link to a directory.
    - A real file at a package path is not refused. It goes to the stow, which refuses it as a conflict (row67).
  - In `--repair`, the sweep has already moved a dangling `~/.claude/skills`, so the row unit's quarantine finds nothing there. It can act only on a dot-prefixed entry, which the sweep's glob skips (row10) and today's package does not have (row65).
  - Depth 1 covers today's only package, which installs as one folded link (row2, row65). A stale link nested inside a real directory makes the stow refuse, which is a reported failure (M8).
  - The `mv`-failure branch and the `claude` unstow stay exactly as they are, so an abort before the `mv` changes nothing and a failed `mv` has nothing new to restore (row12).
  - The package name is a constant (M2), so the `stow` call needs no validation. It carries no `--` before the package name, like all six existing `stow` calls (row7).
  - Set aside:
    - Calling `_quarantine_dangling_children "$HOME/.claude"` whole after the `mv`. It moves links the relocation did not break, such as a `projects` link to an unmounted disk.
    - Accepting a real `~/.claude/skills` and letting plain stow merge into it. That creates the state `install.sh` refuses (row72). The merged links also sit below depth 1, where the next relocation's quarantine cannot reach them, so that run's stow would refuse until they are cleaned by hand.
    - `--adopt`, which moves a real file into the checkout and which `install.sh` dropped (row67).
    - Re-pointing the fold in place, because stow also links a top-level entry tracked since the last install, as `install.sh` would.
    - A sourced `_<topic>-lib.sh` for the helper, which `shell-script-conventions.md` prefers. Nothing the installed copy loads may come from beside `$0` (row8).
    - Matching the link text against the package path for staleness. PR 2 needs that rule for declared dirs, but here it would only leave in place a dangling link that blocks the stow anyway.
- **M2 — hardcode the one non-`claude` row, pinned to `stow-packages.sh` by a test** (anchors: row3, row8, row9, row12, row14, row57, row58, row62, row63, row64, row68, row69, row75, row76).
  - A global after `BACKUP_DIR` (`:42`) holds `RELINK_PACKAGES=($'claude-skills\t.claude')`, in `stow-packages.sh`'s `<package>\t<target relative to $HOME>` row format. A one-line comment says a test in `test_stow_packages.py` keeps it equal to that script's rows other than `claude`.
  - The runner splits each row at its tab and passes `$HOME/<target>` to the row unit (M1).
  - Duplicating one row is `CLAUDE.md` § "Engineering Judgment"'s named exception (3), "a small duplicated value that beats a bad abstraction". Package names already appear as literals in `install.sh` and in this script's `stow` calls (row62).
  - The drift test sources the script behind its guard (row64), so a divergence fails CI instead of leaving a package unlinked.
  - It lives in `claude/.claude/hooks/tests/test_stow_packages.py` beside the manifest's other consumer pins, and it reuses that file's `_manifest_rows()`.
  - The scoped suite selects that file when either script changes (row76).
  - It asserts the array is declared through `declare -p`, because under bash 5.3 `set -u` does not fail on an unset array (row75). The assertion accepts any `declare -p` flags that include `a`, so a `readonly` or exported array still passes.
  - It also asserts that the manifest's `claude` row targets `.`. The script's four `claude` stow calls hardcode `$HOME` as their target, the other arm of the same duplication (row62).
  - The installed copy can lag the checkout, but a new package reaches `$HOME` only through `./install.sh`, which refreshes the copy in the same run (row63).
  - A checkout with no `claude-skills/` makes the row's stow fail, and the run exits 3 with `claude` linked (row68).
  - Set aside, each lighter mechanism anchored to the row it rests on:
    - Running `<new-path>`'s `stow-packages.sh`, as `install.sh:440-461` runs its own (row14). It runs destination code after the `claude` stow, so it needs a trust statement, a listing-failure branch and row validation, all for a two-row list.
    - Parsing `<new-path>`'s `stow-packages.sh` as text (row14). No code runs, but its rows are destination-controlled data that need the same validation. The parser also couples to the file's array syntax.
    - Listing from the current checkout before the `mv`, in primary mode only (row3, row9). It has the same trust root but adds a mode-specific branch. `--repair` would still have to list from `<new-path>`.
    - Unstowing `claude-skills` before the `mv` and re-stowing it after (row12). It adds a failure point before the move and a restore step to the `mv`-failure branch. `--repair` cannot unstow, so it still needs the sweep.
    - Running `stow-packages.sh` from beside `$0` or from `~/.claude/scripts`, which breaks row8's contract.
- **M8 — report, continue, exit 3** (anchors: root, row5, row9, row10, row19, row21, row30, row39, row42, row78, row85, row88).
  - Right after the `claude` stow and before `finish_relocation`, both modes call the runner as a plain statement. The runner returns 0, so it cannot skip `finish_relocation`.
  - The `claude` stow stays a plain statement before the runner, so a refused `claude` stow still exits 1 before any follow-up (row5).
  - The runner sets a global failed-package array empty. For each `RELINK_PACKAGES` row it prints one `linking <package> at <new-path>` line and runs `if ! _relink_package …; then`. A failed unit gets one `err` naming its package, and the package joins the array.
  - The unit is a function whose body is a subshell, so an `exit` or a `shopt` change inside it ends with it (row19). A failing unit never aborts the run and never reaches exit 1 after the checkout moved.
  - Errexit is off inside the unit (row19), so each step checks its own status (M1).
  - The follow-ups run before `finish_relocation`, so a `write_manifest` or `install` abort, such as on a full disk, still leaves every package linked. A test forces that abort with a directory at the manifest path (row78).
  - With no failure, the closing text is today's `Done.` text plus the manual-steps list, and the run exits 0.
  - On any failure, the closing text goes to stderr and the run exits 3. It holds, in order:
    - that the move or repair completed and the `claude` package is linked;
    - each failed package by name;
    - that the primary form must not be re-run, in the words `must not be re-run`;
    - today's reminders, in primary mode, and the manual-steps list;
    - that the `--repair` retry also moves every other dangling entry directly under `~/.claude` and `~/.local/bin` into the backup dir, not only those the move broke (row10);
    - one sentence before the retry line: resolve the named failure first, because the retry fails the same way until then. That covers a missing `claude-skills/`, where the checkout must be updated to a commit that has it, and the symlinked `~/.claude` that step 1's `err` explains (M1);
    - last, `relocate-claude-config --repair <path>`, with `--allow-outside-home` before the path only when this run passed it. The path is the canonical new path through `printf '%q'`, so the line runs as printed (row21).
  - The manual-steps list names other accounts' config dirs, including any listed in `~/.claude/transcript-config-dirs`, with their marketplace registration through `register-marketplace`. `register-marketplace` takes effect only once that account's `settings.json` resolves, and exits 0 with "nothing to register" otherwise (row39). The list says an account not re-linked loads none of this repo's settings, hooks or skills.
  - The list also names every linked worktree, through `git worktree repair`. It names anything outside this repo that names the old path, such as a scheduled job or a shell startup file. It points at README "Repo relocation".
  - Neither the list nor the README gives steps for re-linking another account, and neither names a PR. The PR description carries the trigger to delete both (row85, row88, Out of scope).
  - Set aside:
    - Running `finish_relocation` first. A full disk would then abort the run before any follow-up and leave `~/.claude/skills` dangling.
    - A runner that turns errexit back on with `set +e; ( set -e; unit ); set -e`. Errexit stays off whenever any caller up the stack is an `if`, `&&` or `||` condition (row19), so one later edit would disable it silently.
    - Aborting on the first failure, which leaves later rows unlinked for no reason.
    - Exit 0 with a warning, which hides a broken link.
    - Exit 1, documented as a failed precondition, which implies nothing changed (`relocate-claude-config.sh:32-36`).
- **M9 — `--repair` is the retry** (anchors: row4, row9, row10, row46).
  - The `claude` package and every row already stowed take Stow's skip path.
  - A stale link not yet quarantined still does not resolve, so the next run finds it.
  - Both modes quarantine through one helper, `_quarantine_if_dangling` (M1), so a retry writes the same backup layout as the run it repairs.
  - When nothing is left to move, the retry's sweep prints today's `no dangling stow-managed entries found to quarantine` line and writes no backup entry.
  - Neither step needs the previous location, which `--repair` does not know.
- **M10 — refuse an unusable `$BACKUP_DIR` before anything changes** (anchors: row10, row13, row73, row80).
  - `_ensure_backup_dir_is_not_a_symlink` becomes `_ensure_backup_dir_is_usable`. It keeps today's symlink refusal and its message, which holds `already exists as a symlink`, unchanged. It also exits 1 when `$BACKUP_DIR` exists and is not a directory (row73).
  - The non-directory refusal's message holds the fixed fragment `exists and is not a directory`, which no other message in the script holds. The script and `TestBackupDirSymlinkGuard` share it.
  - Its two existing call sites (`:301, :327`) keep it, so `--repair` gets the wider check too. `_quarantine_if_dangling` calls it before each move (M1).
  - Primary mode now also calls it first, before the first unstow, because primary mode now quarantines too (M1). Without that call, a symlink or file there would first surface inside a row unit after the `mv`, as exit 3 with the checkout moved.
  - A symlink made on purpose at the backup path, for example to another volume, now blocks primary mode, as it already blocked `--repair`. The README and CHANGELOG say so.
  - The order inside `relocate_repo` is M10, validate, resolve the current checkout, canary, so the existing tests that assert the resolve step's stderr stay green.
  - `_stow_migration_lib.sh`'s comment that names the old function as an identical guard is updated to the new name, without "identical". The lib's own guard stays symlink-only, because a file there already makes its `mkdir` fail (row80).
  - Set aside: following a symlink made on purpose. The guard exists to refuse a pre-planted one at a fixed, predictable path (`:280-282`), and the script cannot tell the two apart.
- **M11 — duplicate the `.DS_Store` ignore literal** (anchors: row6, row29).
  - M1 passes `--ignore=(^|/)\.DS_Store$` with a one-line comment naming `install.sh` as the home of its reason.
  - This is `CLAUDE.md` § "Engineering Judgment"'s named exception "a small duplicated value that beats a bad abstraction". The only shared home would be `_stow_migration_lib.sh`, the refactor the chosen design dropped (row29).
  - Set aside: omitting it, which lets a Finder-dropped `.DS_Store` at a package root be linked and collide with another package's (`install.sh:418-423`), refusing the stow.

## Critical files

Comments and docs added below state durable one-line facts only, with no M-numbers or other plan terms and no file:line cites, in the added docs and the CHANGELOG included. Comments, docs, the CHANGELOG and the PR body describe the `claude-skills` stow as one that passes no `--adopt`, never as one that cannot adopt, because a `.stowrc` can add it (row22).

One `code-writer` dispatch covers every file below. The tests exercise the script's new functions and the doc edits describe its new behavior, so a split would restate the same background in each prompt.

- **Modify `claude/.claude/scripts/relocate-claude-config.sh`.**
  - Header, exit codes:
    - `1` gains six sentences. It can also follow the primary form's `re-stowing at` line or `--repair`'s `stowing at` line, when the `claude` stow or the manifest or installed-copy write fails. The exit status is then the failing command's own, so it can be 1 or 2. It is 1 for a Stow conflict. It is 2 for a missing package directory, as Stow 2.4.1 was observed to return, and neither this script nor Stow defines or documents that code (row68). After `re-stowing at` the checkout has moved and `--repair <new-path>` is the retry. After `--repair`'s `stowing at`, the retry is re-running the same command, since only the primary form prints `re-stowing at`.
    - The table's `2  bad arguments` line gains one sentence: a failing `claude` stow or write after the move can also return 2 as its own status, as the `1` entry says.
    - Add `3` as three sentences. The checkout moved, or `--repair` linked the `claude` package, but linking a package named on stderr failed. Fix it, then run the `--repair` command the output ends with. A failing `claude` stow or write after the move can also return 3 as its own status, and then the output has no closing `--repair` line.
  - The header's threat-model paragraph is unchanged (row69, row70).
  - `RELINK_PACKAGES` after `BACKUP_DIR` (`:42`), with its one-line comment (M2).
  - `_ensure_backup_dir_is_not_a_symlink` (`:280-288`) renamed `_ensure_backup_dir_is_usable` and widened, with its comment and both call sites updated (M10).
  - `_quarantine_if_dangling`, extracted from `_quarantine_dangling_children`'s loop (`:307-312`), which then calls it, and the two comment edits at `:290-298` (M1).
  - New functions:
    - The row unit `_relink_package`, with its contract comment, its symlinked-target and real-directory refusals and its `.DS_Store` ignore (M1, M11).
    - The runner, which owns the failed-package array (M8).
    - The closing text for both outcomes, with the retry line (M8).
  - `relocate_repo`:
    - Call `_ensure_backup_dir_is_usable` first (M10).
    - Leave the unstow, `mv`, `mv`-failure restore and `claude` stow as they are.
    - After the `claude` stow, run the runner, then `finish_relocation`, then the closing text, and exit 3 when any package failed.
  - `repair_repo`: the same sequence after its `claude` stow.
  - Reuse `canonicalize_for_comparison` (`:116-124`) for the retry line's path, `BACKUP_DIR` (`:42`), `ALLOW_OUTSIDE_HOME` (`:485`), the `[relocate-claude-config]` stdout prefix and `err()`.
- **Modify `claude/.claude/scripts/tests/test_relocate_claude_config.py`.** Each new test is named for its scenario and outcome. Fixtures use synthetic names, and no real project, account or home path.
  - Fixtures and helpers:
    - `_make_repo` (`:29-51`) adds `claude-skills/skills/example/SKILL.md`, so every existing run also links `claude-skills` and still exits 0. No existing test asserts that `~/.claude/skills` is absent.
    - `_stow` (`:54-63`) gains a keyword opt-in that also stows `claude-skills` into `home/.claude` with plain `stow`.
    - A helper returns stderr's last non-empty line, because the closing text ends with a newline.
    - One builder sets up the real-directory failure. `~/.claude/skills/mine/SKILL.md` is real user content. The destination's name holds a space, a single quote, `$x` and one unpaired backtick, so a double-quoted print of it either expands `$x` or fails to parse (row21). The builder takes the mode and whether the destination lies inside the fake home, and it passes `--allow-outside-home` only when the destination does not. It also takes whether the destination is typed canonically, or with a `..` segment or a symlinked parent that resolves to the same path.
    - One `env` builder returns the environment for the fake home, the bin dir and the installed-copy dir. `_run_script` and the printed-retry run both call it, so the retry run cannot drift from it.
    - `TestClaudeCliMissing` needs no change, because its tool list (`:549`) already holds every external tool the new code calls.
  - `TestPersonalSkillsRelink`, the exit-0 paths:
    - Re-linking backs up the stale link, parametrized over primary mode and `--repair`, with `claude-skills` stowed at the old location through the opt-in and the `~/.claude/skills` link text read before the run:
      - The run exits 0, and `~/.claude/skills` resolves into the new checkout.
      - The backup dir holds one `skills.*` entry, a symlink whose text is the text read before the run.
      - Stdout names the quarantined `~/.claude/skills` path, and holds `transcript-config-dirs`, `register-marketplace`, `git worktree repair` and `Repo relocation`.
    - Primary mode leaves an unrelated dangling `~/.claude/<other>` link: the run exits 0, the link keeps its text, and the backup dir holds no entry for it.
  - `TestPersonalSkillsRelinkFailure`, the exit-3 paths:
    - A real `~/.claude/skills` is left unmerged, parametrized over both modes, from the builder with an inside-home destination.
      - The run exits 3, and stderr names `claude-skills` and `~/.claude/skills`.
      - `mine/SKILL.md` keeps its content, and no `example` entry appears beside `mine`.
      - `~/.claude/hooks` resolves into the new checkout, and the manifest names it.
    - The exit-3 closing text, from the builder in primary mode: stderr holds `must not be re-run`, the four manual-steps tokens above and the retry-reach token `also moves every other dangling entry`.
    - The retry line, from the builder in primary mode, parametrized over an inside-home destination run without the flag, an outside-home one run with it, and an inside-home one typed non-canonically (a `..` segment or a symlinked parent) run without the flag. Bash reads stderr's last non-empty line as words, through `bash -c 'eval "set -- $1"; printf "%s\0" "$@"' _ <line>`. The words are exactly `relocate-claude-config`, `--repair`, then `--allow-outside-home` only in the outside-home case, then the destination's resolved path, which differs from the typed one in the non-canonical case (row21).
    - The printed retry succeeds, with the same parametrization:
      - With `~/.claude/skills` moved aside, `bash -c <line>` runs the line in an environment that sets `HOME` to the fake home, from the shared `env` builder that `_run_script` also calls (`_run_script`'s own construction is at `test_relocate_claude_config.py:134-145`). It prepends `home/.local/bin` and the `claude` shim directory to `PATH`. The shim keeps the marketplace sync off a real `claude` CLI.
      - Every parametrization asserts against `home`, and none reads or writes the real home.
      - It exits 0, and `~/.claude/skills` resolves into the new checkout.
      - Stdout holds `no dangling stow-managed entries found to quarantine`, and the backup dir gains no entry (M9).
    - A file conflict keeps the user's file, in primary mode. The test adds a top-level `notes.md` to the old checkout's `claude-skills/`, and `~/.claude/notes.md` is a real file with user content. The run exits 3, and that file is still a regular file with its content. `--adopt` would have moved it into the package and linked it in its place, so this pins the missing `--adopt` (row67).
    - A live link at `~/.claude/skills` pointing at a directory outside the checkout (a link to a file would not exercise `! -L`, since `-d` is false for it) is left in place, in primary mode: exit 3, stderr names `claude-skills` and holds `existing target is not owned by stow`, the link still resolves where it did, and the backup dir holds no `skills.*` entry. The conflict text shows the link reached the stow rather than the real-directory refusal (row81).
    - A checkout with no `claude-skills/`, parametrized over both modes: exit 3, stderr names `claude-skills`, `~/.claude/hooks` resolves into the new checkout, the manifest names it, and stderr's last non-empty line starts `relocate-claude-config --repair` (row68).
    - A live tree-folded `~/.claude`, in `--repair` mode only: the fixture stows `claude` with `_stow_tree_folded` (`:66-74`) from the `--repair` destination itself, so `~/.claude` is a live link into that checkout. The run exits 3, stderr names `claude-skills` and holds one stable fragment of step 1's `err`, the words naming `~/.claude` as a symlink into the checkout, and no `skills` entry appears inside the checkout's `claude/.claude/` (row83).
    - A refused `claude` stow, parametrized over both modes, runs the follow-ups no earlier than the `claude` stow. The fixture stows `claude` at the old location with `_stow` and does not opt in to `claude-skills`, so `~/.claude/skills` is absent before the run, and the `--repair` sweep has no `skills` link to move. It replaces the stowed `~/.local/bin/some-wrapper` link with a live link to a foreign directory, which the `--repair` sweep leaves in place and which `--adopt` cannot take, since Stow refuses a link it does not own before it adopts anything (row4). The run exits non-zero, which M8 and row5 put at 1. Stdout has no `linking claude-skills` line, and `~/.claude/skills` is still absent. Neither the manifest nor `home/.local/bin/relocate-claude-config` exists (M8, row5).
  - `TestQuarantineMoveFailure`: `claude-skills` is stowed through the opt-in, and `$BACKUP_DIR` is an existing real directory made unwritable with `chmod(0o555)`, restored in `finally` (row74).
    - Primary mode fails the unit before its stow.
      - The run exits 3, stderr names `claude-skills`, and it holds the helper's `could not move` line naming `$BACKUP_DIR` but no `All operations aborted.`.
      - `~/.claude/skills` keeps the link text read before the run.
      - `~/.claude/hooks` resolves into the new checkout, and the manifest names it.
    - `--repair` stops before the `claude` stow.
      - The run exits 1, and stderr holds the helper's `could not move` line naming `$BACKUP_DIR` but no `All operations aborted.`.
      - `~/.claude/hooks` is still a symlink that does not resolve.
      - Neither the manifest nor `home/.local/bin/relocate-claude-config` exists.
  - `TestFollowUpsRunBeforeFinishRelocation`, parametrized over both modes, with `claude-skills` stowed through the opt-in and `~/.claude-config-source` a real directory: the run exits 1, and `~/.claude/skills` and `~/.claude/hooks` both resolve into the new checkout (row78). In primary mode the checkout has moved. In `--repair` mode it is already at the destination.
  - `TestRelocateMvFailureRecovery` (`:282-321`) stows `claude-skills` through the opt-in and also asserts that `~/.claude/skills` still resolves into the old checkout (row12).
  - `TestBackupDirSymlinkGuard` (`:356-377`) has its existing `--repair` guard test parametrized over a symlink and a regular file at `$BACKUP_DIR`, so the per-entry regular-file arm of each mode is pinned. It also gains a primary-mode case with the same two parameters (M10):
    - The run exits 1, and stderr holds the fragment for the case: `already exists as a symlink` for a symlink, `exists and is not a directory` for a regular file (M10).
    - The primary-mode guard fixture stows `claude-skills` through the `_stow` opt-in.
    - In the primary-mode arms (the symlink and regular-file parameters), the checkout has not moved, and `~/.claude/hooks` and `~/.claude/skills` still resolve into the old checkout.
    - In the symlink case, the symlink's target dir is empty.
    - In the `--repair` regular-file arm:
      - Stderr has no `could not move`, and stdout has no `quarantining dangling`, so the guard refused before the quarantine ran.
      - The dangling `~/.claude/hooks` link is still in place, and the regular file's content is unchanged.
      - Neither the manifest nor `home/.local/bin/relocate-claude-config` exists.
    - A tree-folded `--repair` case on the `_stow_tree_folded` fixture (`:66-74`), parametrized over a symlink and a regular file at `$BACKUP_DIR` (M10):
      - The run exits 1 with `already exists as a symlink` for a symlink or `exists and is not a directory` for a regular file, and the symlink's target dir is empty.
      - `~/.claude` is still the dangling link.
  - Reuse `_stow`, `_run_script` and `_claude_shim` (`:54-159`).
- **Modify `claude/.claude/hooks/tests/test_stow_packages.py`** — add the drift test, `TestRelocateRelinkPackagesMatchManifest` (M2, row76):
  - The module docstring's list of pins gains one item: `relocate-claude-config.sh`'s copy of the rows other than `claude` must equal the manifest's, and the `claude` row must target `.` as that script's `stow` calls assume. Its "two consumers" wording becomes "its consumers".
  - A module constant names the script as `SCRIPTS_DIR / "relocate-claude-config.sh"`.
  - The test runs `bash -c 'source "$1"; declare -p RELINK_PACKAGES; printf "%s\n" "${RELINK_PACKAGES[@]}"' _ <script>` with `HOME` set to a temp dir, and asserts exit 0 (row64, row75).
  - It asserts that the first line matches `declare -<flags> RELINK_PACKAGES=`, with `a` among the flags in any order.
  - It asserts that `_manifest_rows()` holds the row `claude` with target `.`. The failure message names the script's four `claude` stow calls (row62).
  - It asserts that the remaining lines equal `_manifest_rows()`'s rows other than `claude`, rejoined as `<package>\t<target>`. It splits stdout on `\n` and drops only the final empty string, so a blank line still counts. The failure message names both files.
- **Modify `claude/.claude/scripts/_stow_migration_lib.sh`** — the guard comment (`:96-102`) only. It names `_ensure_backup_dir_is_usable` and says the guard mirrors that function's symlink check, dropping both "identical"s (M10, row80).
- **Modify `claude/.claude/scripts/stow-packages.sh`** — the header comment (`:6-8`) only. It keeps naming `install.sh` and the downstream consumer as readers of the list, and adds one sentence: `relocate-claude-config.sh` keeps a copy of the rows other than `claude`, which a test keeps equal to this list (M2).
- **Modify `README.md`** — only inside `### Repo relocation`, item 2. The paragraph at `:459` says `--repair` quarantines an entry "left as a dangling symlink into the old location". Its sweep moves every dangling entry wherever it pointed (row10), so that clause loses "into the old location". One block follows that paragraph, in this order.
  - Run `./install.sh` after pulling and before relocating, because the installed copy is the one that runs (row23).
  - Both modes also re-link the `claude-skills` package at `~/.claude/skills`, with a stow that passes no `--adopt`. A link at one of its top-level paths that no longer resolves moves to `~/.claude-config-relocate-backup/<name>.<pid>` first, and the output names each move. A real directory at `~/.claude/skills` is left alone and reported, as `install.sh` does.
  - Exit 3 means the checkout has already moved, or `--repair` already linked the `claude` package, and re-linking a package named on stderr failed.
    - The common causes are something in the way at `~/.claude/skills`, which you move aside (a real file or directory, or a live symlink pointing outside the checkout), a checkout with no `claude-skills/` directory, and `--repair` run while `~/.claude` is a live symlink into the checkout. The output names the cause and, for the symlinked `~/.claude`, the fix; the README restates no steps for it (M1).
    - For a missing `claude-skills/`, update the checkout to a commit that has it. The retry exits 3 again until then.
    - Fix the cause, then run the `--repair` command the output ends with. That run moves every dangling entry `--repair` finds, as described above, not only those the move broke. The primary form must not be re-run.
  - Exit 1 from the primary form:
    - Before the `moving <old> -> <new>` line, a precondition failed and nothing changed.
    - After `re-stowing at <new-path>`, which prints only once the move succeeded, the checkout moved. The exit status is the failing command's own, as the status bullet below says. Fix the error, then run `relocate-claude-config --repair <new-path>`.
    - After `mv failed`, nothing moved. Fix the cause and re-run the primary form.
  - After an exit 1 from `--repair`, fix the error and re-run the same command.
  - A failed `claude` stow, or a failed manifest or installed-copy write, exits with the failing command's own status, so the status can be 1 or 2 (or any other the command returns, 3 included). Stow 2.4.1 was observed to return 1 for a conflict and 2 for a missing package directory. Neither code is defined by this script or documented by Stow, whose error is a bare die (row68). A status of 1 or 2 after `re-stowing at` means the checkout moved and the `--repair` command above is the retry, not that the arguments were bad. After `--repair`'s `stowing at`, the retry is re-running the same command.
  - Both modes exit 1 before changing anything when `~/.claude-config-relocate-backup` exists and is a symlink or is not a directory, a symlink made on purpose to another volume included.
  - The script does not do the following, which stay manual. These bullets state current behavior only: they give no steps for re-linking another account and name no PR or installer (G1, row85, row88).
    - Other accounts' config dirs, including any listed in `~/.claude/transcript-config-dirs`, keep their links into the old location. Until one is re-linked, a session under that account loads none of this repo's settings, hooks or skills. `CLAUDE_CONFIG_DIR=<dir> register-marketplace` re-points its marketplace registration. `register-marketplace` takes effect only once that account's `settings.json` resolves, and exits 0 with "nothing to register" otherwise (row39).
    - Every linked worktree, wherever it lives, still records the old location. Run `git worktree repair <path>…` in the moved checkout, naming each linked worktree's current path. One that lived under the checkout is now at `<new-path>/.claude/worktrees/<name>` (row42).
    - Anything outside this repo that names the old path, such as a scheduled job or a shell startup file, still names it.
- **Modify `docs/scripts.md`**:
  - The `relocate-claude-config.sh` bullet (`:462`): one sentence saying both modes also re-link `claude-skills` and exit 3 when that fails after the checkout moved, or after `--repair` linked `claude`, with details in README.
  - The `stow-packages.sh` bullet (`:486`): one sentence saying `relocate-claude-config.sh` keeps a copy of the rows other than `claude`, which a test keeps equal to this list.
- **Modify `CHANGELOG.md`** — one `### Changed` bullet under `[Unreleased]` (`CHANGELOG.md:5-7`):
  - `relocate-claude-config` now re-links `claude-skills` in both modes, after moving a dangling `~/.claude/skills` into its backup dir. That stow passes no `--adopt`, and it leaves a real `~/.claude/skills` directory unmerged, as `install.sh` does.
  - A new exit code 3 means the checkout moved, or `--repair` linked `claude`, and re-linking another package failed, with `--repair` as the retry. A run that exited 0 before can exit 3 now, `--repair` over a live symlinked `~/.claude` (the legacy tree-folded form) among them. The bullet gives no fix for that case, since the script's output carries it (M1).
  - Both modes now exit 1 before changing anything when `~/.claude-config-relocate-backup` exists and is a symlink or is not a directory. Before, only `--repair` refused, and only a symlink. A backup dir symlinked to another volume now blocks the primary form too.
  - `--repair` now exits 1 before it stows `claude` when moving a dangling link into the backup dir fails, including a link the `claude` package does not link, such as a `projects` link to an unmounted disk. `~/.claude/hooks` then stays unlinked until the backup dir is fixed.
  - Other accounts' config dirs and linked worktrees stay manual (README "Repo relocation").
  - One `**Migration:**` line in the file's existing single-line style: "if `~/.claude/skills` is a dangling symlink after a past relocation, run `relocate-claude-config --repair <new-path>` and then `./install.sh` from the checkout. Otherwise run `./install.sh` before your next relocation, because `git pull` does not refresh the installed copy" (row8, row23).

Projected diff: ~525 lines (relocate script ~140, its tests ~311, `test_stow_packages.py` ~28, `README.md` ~21, `CHANGELOG.md` ~12, `_stow_migration_lib.sh` ~6, `docs/scripts.md` ~4 and `stow-packages.sh` ~3). [unverified: an estimate from the files as read, not counted] This is above the 400-line threshold. Cutting the recipe removed README and CHANGELOG prose only, because its check ran in a scratch dir and added no committed test. The engineer answered the earlier about-495-line estimate by proceeding (row89), the engineer answered the about-509-line estimate by proceeding (row91), and this about-525-line estimate by proceeding (row94).

## Verification

1. **Scoped suite:** `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. For this diff it selects at least `claude/.claude/scripts/tests/`, `claude/.claude/hooks/tests/` and `claude-skills/skills/tests/` (row76). The `README.md`, `docs/scripts.md` and `CHANGELOG.md` edits may select more, which this plan did not trace.
   - Every pre-existing test in `test_relocate_claude_config.py` and `test_stow_packages.py` still passes with only the fixture changes above, except that `TestRelocateMvFailureRecovery` and `TestBackupDirSymlinkGuard` also gain assertions or parameters (row13, including `TestClaudeCliMissing`).
   - The drift test finds the constant equal to `stow-packages.sh`'s non-`claude` rows (M2).
   - Both modes link `~/.claude/skills` into the new checkout and keep the old link as `skills.*` in the backup dir (M1).
   - Three exit-3 cases assert that `claude` is linked and the manifest written: the real-directory, primary move-failure and no-`claude-skills` cases. Bash reads the real-directory case's last stderr line as the expected words, and running it through bash exits 0 (M1, M8, M9, row21).
2. **Lint:** `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` and `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
3. **Mutation checks, reverted before commit.** Each names the case that must fail:
   - Add `--adopt` to the row unit's `stow` call: the file-conflict case fails on its exit-3 assertion, because Stow then succeeds and the run exits 0. Its regular-file assertion is the data-preservation check (row67).
   - Delete the row unit's symlinked-target refusal: the live tree-folded `--repair` case fails on its exit-3 assertion (M1, row83).
   - Delete the row unit's real-directory refusal: the real-`~/.claude/skills` case fails (M1, row72).
   - Drop `! -L` from the real-directory test: the live-link case fails on its stow-conflict assertion (M1, row81).
   - Delete the row unit's quarantine call: the primary-mode re-link case fails (M1).
   - Replace the row unit's per-path quarantine with `_quarantine_dangling_children "$HOME/.claude"`: the unrelated-dangling-link case fails (M1).
   - Make `_quarantine_if_dangling` also move a link that resolves: the live-link case fails, and so does the existing sweep test `test_live_symlink_and_real_directory_left_untouched`. The live-link case is the unique pin of `! -L`, the one the "Drop `! -L`" mutation above targets (M1).
   - Make `_quarantine_if_dangling` return 0 after a failed move instead of exiting: both move-failure cases fail on their `All operations aborted.` assertion (M1). Separately, delete the helper's `err`: both fail on their `could not move` assertion.
   - Make the runner ignore a failed unit: every exit-3 case fails on its exit code, the no-`claude-skills` case among them (M8, row68).
   - Move the runner call before the `claude` stow, in each of `relocate_repo` and `repair_repo` in turn: the refused `claude` stow case fails on its `linking claude-skills` and `~/.claude/skills` assertions (M8, row5).
   - Make the runner exit 3 itself on a failure: the real-directory, primary move-failure and no-`claude-skills` cases fail on their manifest assertion (M8).
   - Swap the runner and `finish_relocation` in `relocate_repo`: the primary-mode follow-ups-first case fails (M8, row78).
   - Swap the two calls in `repair_repo`: the `--repair`-mode follow-ups-first case fails (M8, row78).
   - Delete the `must not be re-run` line: the exit-3 closing-text case fails (M8).
   - Always print `--allow-outside-home`, then separately never print it: the inside-home and the outside-home retry-line cases fail in turn (M8).
   - Print the retry path in double quotes instead of through `%q`: the retry-line case fails (M8, row21).
   - Print the typed path instead of the canonical one: the non-canonical retry-line case fails (M8).
   - Delete step 1's symlinked-target `err` while keeping the failure: the tree-folded `--repair` case fails on its fragment assertion (M1).
   - Delete the retry-reach sentence from the closing text: the exit-3 closing-text case fails (M8).
   - Delete the guard call at the top of `quarantine_dangling_claude_entries`: the tree-folded `--repair` guard case fails. Separately, delete the one at the top of `relocate_repo`: the primary-mode guard cases fail (M10).
   - Narrow `_ensure_backup_dir_is_usable` back to its symlink test: the regular-file guard case fails in each mode on its fragment assertion (M10).
   - Add a second row to `RELINK_PACKAGES`, then separately rename the array, then separately change the `claude` row's target in `stow-packages.sh`: the drift test fails each time (M2, row62, row75).
4. **CI Stow version:** read the Stow version CI's apt step installs from the PR's first CI run, and record it in the PR body. A CI-only failure in a stow-conflict or relink case is read first as a difference from the Stow 2.4.1 that G3 and rows 4, 5 and 22 were read in. [unverified: CI's Stow version]
5. **Installed copy (engineer, after merge):** run `./install.sh` from the checkout, then `cmp ~/.claude/scripts/relocate-claude-config.sh ~/.local/bin/relocate-claude-config` exits 0 (row8, row23).
6. **The real move is not part of this plan's verification.** The engineer's own rename waits for PR 2 and PR 3 (row87).

## Out of scope

- **Re-pointing declared accounts — PR 2**, with its own plan after this one lands. Every config dir listed in `~/.claude/transcript-config-dirs` keeps its stale links here. The roots reader, the walk, the staleness shape rule, the link-text builder, the ordinal map and their tests all move to PR 2, and the README lists those accounts as manual until it lands. PR 2 inherits these decisions:
  - Replace each stale link atomically. Record its old text in the backup, create the new link at a sibling temporary name with no force flag, re-check that the destination is still a link that does not resolve, then `mv -f` the temporary link over it. Remove the temporary link on any failure.
  - Name a declared dir in messages only by its ordinal, with the ordinal-to-path map written durably in the run's backup dir before any link changes.
  - Read the roots file only through `_config_dir.py`'s canonical parser, loaded from `<new-path>` with `python3 -I -B -c` and a constant source, with the scripts dir passed in `argv`.
  - Run through this plan's follow-up runner (M8), and take the run's backup dir from `mktemp -d`.
  - State which path the run trusts, and on what evidence. PR 2 runs `_config_dir.py` from `<new-path>`, so its plan, the header and the README need the trust statement that row 69 retired from this plan (row14, row70).
  - Entries tracked since a declared dir was last stowed stay unlinked there, because the re-point restores each dir's previous links (row29).
  - Keep the repo dependent on no other repository, so the roots file is PR 2's only contract (G1).
  - State in the header and README that a `--repair` run re-points every declared account's matching dangling links, and give PR 2's tests a crafted-destination `--repair` case with an allow and a deny assertion for that reach.
- **`git worktree repair` for linked worktrees that moved with the checkout — PR 3**, with its own plan. Plain `git worktree repair` in the main worktree reconnects only linked worktrees that did not move, and moved ones must be named by their new paths (row42). Per the dispatcher's relay of the consult, PR 3 finds them by parsing `git worktree list --porcelain -z`, which this plan has not checked. Until it lands, the README lists linked worktrees as manual.
- **The shared ignore-arg builder, `install.sh`, and `_stow_migration_lib.sh` beyond its guard comment** — the chosen design drops that refactor (row29). The lib's guard stays symlink-only (M10, row80).
- **Existing quarantine destinations.** `_quarantine_if_dangling` keeps today's `mv` to `$BACKUP_DIR/<name>.$$`, with no check that the destination already exists (row11). A second dangling link of one name in one `--repair` run replaces the first backup, as it does today. This goes to a follow-up issue the engineer files, which carries a same-basename replace case.
- **Stow resource files and `STOW_DIR`** (row22, row60, row61).
  - A `.stowrc` in `<new-path>` or `$HOME`, or a `STOW_DIR` in the environment, reaches every stow call here and in `install.sh`. A `.stowrc` holding `--adopt` makes the row stow adopt, and a `--dir` line points it at another stow directory. So the docs say the row stow passes no `--adopt`, not that it never adopts (Critical files).
  - Stow 2.4.1 has no option that skips resource files. The fix, for example `-d <new-path>` from a working directory outside it, changes every call together, so it goes to the follow-up the engineer deferred it to (row60).
  - No test pins it here, since that test would pin Stow's documented behavior that the fix then inverts.
- **The personal `claude` package's `stow --adopt` with no `--ignore` args** (`relocate-claude-config.sh:435, 441, 473`).
  - It diverges from `install.sh`, which dropped `--adopt` and ignores untracked entries.
  - With a package-side leftover such as `claude/.claude/plans`, it can adopt real files into the checkout.
  - A crafted destination can adopt real files from `$HOME` through it (row18). The follow-up issue for `--adopt` should name this case.
  - A post-`mv` `claude` stow failure, or a `finish_relocation` abort, still exits with the failing command's own status (1 for a Stow conflict, and 2 as Stow 2.4.1 was observed to return for a missing package directory) with the checkout moved and no retry line. The header and README now say so, and the follow-up issue covers both.
  - Pre-existing and outside the Ask. This case, the crafted-destination case (row18), existing quarantine destinations and the resource-file exposure above go to follow-up issues the engineer files, as do PR 2 and PR 3. This plan cites none of them, because none is filed yet. Once filed, PR 1's description names the PR 2 and PR 3 issues as the trigger to delete the README's manual-step bullets and the closing text's manual-steps list (M8), which go stale as PR 2 and PR 3 land. The tests asserting the `transcript-config-dirs`, `register-marketplace`, `git worktree repair` and `Repo relocation` tokens are edited in the same PR that deletes that text. PR 1's description also states that the engineer's own rename waits for PR 2 and PR 3 (row87).
- **Recording the CI Stow version in CI config.** CI config is peer-owned (G2), so the PR body records it (Verification 4).
- **Revert notes for PR 1's description.** After a revert, the installed copy at `~/.local/bin/relocate-claude-config` keeps the new behavior until `./install.sh` runs, so a revert PR carries the same `**Migration:**` line. PR 2 and PR 3 add follow-ups to PR 1's runner, so once either lands, reverting PR 1 means reverting them first.
- **Branches and edge cases accepted untested:**
  - The row unit's `dotglob` branch. Today's package has no dot-prefixed top-level entry (row65).
  - The `.DS_Store` ignore (M11). Deleting it changes behavior only once Finder drops a `.DS_Store` at the package root.
  - A second `--repair` after success, and a retry with two non-`claude` rows. Only one non-`claude` row exists (M2).
  - The sweep's live-link and real-directory cases are already tested (`test_relocate_claude_config.py:402-432`).
  - `_quarantine_if_dangling`'s per-move call to the backup-dir guard. Its call sites precede it in both modes (M10), so deleting it fails no test.
- **Untracked content nested inside a tracked package directory** stays visible through a folded link (row32).
- **Marketplace sync failures** stay warning-only with exit 0 (`relocate-claude-config.sh:355-394`); existing behavior, and `install.sh` re-registers.
- **The reach of `--repair`'s depth-1 sweep over the personal `~/.claude` and `~/.local/bin`** (`relocate-claude-config.sh:325-347`) is unchanged. Only its per-link step moves into the shared helper (M1).
- **Running `register-marketplace` for other accounts.** The README names the command (row39), and the script still syncs only the ambient account, as today.
- **A dry-run mode** — not asked for.
- **A warning when the running copy differs from the checkout's copy** — optional, not asked for.
- **Peer-owned files** (G2).
