# Same-session handoff resume and compaction as a handoff alternative

## Context

Goal: let a `/clear`ed session resume from a handoff file with one user-run skill, and make `/compact` a recommended alternative to a handoff when it is the cheaper way to shed context.

Ask: "Enable a cleared session to work with handoffs seamlessly. Currently I have to close a session and start a new one with the resume-context script. Ideally I could run a command/nameonly skill to do what resume-context did" and "Make compact an efficient option to use if the same files need to be opened by the main session and it would be more efficient to use compaction than clearing and resuming from handoff file" — "Those options aren't mutually exclusive". Scope as drafted by the session (the same-session resume skill, a compact-versus-handoff decision rule in the handoff skill and nudge hook, and the stale-marker fix) — accepted via "yes start plan-it with this scope."

Today a handoff ends with `resume-context <path>`, which moves the file to a temp path and execs a fresh `claude` process with it loaded via `--append-system-prompt-file`. A same-process path already half-exists: after `/clear`, typing "Read <path> and continue" fires `consume-durable-continuity-file-on-read.sh`, which moves the file the same way. What the same-process path lacks is discovery of which handoff to load, worktree handling from §4, and the task-list restore directive. Separately, the handoff skill's "is a handoff warranted?" step and the nudge hook's hard block only ever route to `/handoff`, even when a user-run `/compact` would keep the working set at lower cost.

The engineer noted that `resume-context` stays necessary when the next session needs a different worktree than its predecessor: "Resume-context will still need to be used regardless of the other two's existence if the next session needs to go into a different worktree than its predecessor".

Empirical findings from a throwaway probe (`/tmp/handoff-probe/probe.sh`, one run on a scratch repo, Claude Code version not recorded):

- `/clear` assigns a new `session_id` while the `claude` PID stays the same, and `capture-session-id.sh` rewrites `sessions/<PID>` with the new id.
- The worktree anchor (cwd) survives `/clear`.
- `EnterWorktree` mid-session reloaded neither the target's `CLAUDE.md` nor its project `.claude/settings.json` hooks, contrary to the worktrees doc's statement that the move "takes the session's working directory, write access, and project configuration such as `CLAUDE.md` and settings to that location."
- A `/clear` after `EnterWorktree` reloaded `CLAUDE.md` from the new worktree, but project hooks stayed bound to the launch directory.
- The probe sessions exposed no task-list tool, so handoff §2.6 is tracked inline.

## Approach

The plan adds a name-only `/resume-context` skill and one script. Together they resume a pending `/handoff` or `/brief` file inside the current process, either after `/clear` or in a new session started in the same worktree. When they cannot confirm that the file belongs to this worktree, they print the shell `resume-context` command instead. For compaction, the nudge's two messages give the agent docs-grounded criteria for recommending `/compact` or `/handoff`. They also give a timing sentence that the agent passes on to the engineer with a paste-ready `/compact <focus>` line. `--check` also stops counting a nudge that fired before a compaction, so a compacted session is no longer routed straight back to `/handoff`.

### Decisions (resolved)

Resolved by the engineer: D1 option A, confirmed via the selected label "Yes, nudge only" (row 49); the earlier reply "I think nudge makes sense. What does the architect think?" is history (the architect's recommendation was already A); D2 option A, via the selected label "No grant (Recommended)". The options below stay as the record of what was weighed.

**D1 — Where the compact rule lives.** This is a genuine decision for two reasons:
- The scope the engineer accepted names "a compact-versus-handoff decision rule in the handoff skill and nudge hook".
- The warrant step's only threshold-alone callers either argue for a handoff (`ready-for-review/SKILL.md:36`) or treat a declined write as a halt (`:37`) (row 17).

The options:
- **A (recommended; designed below).** The rule lives only in the nudge's two messages, as an offer of two exits.
  - `/handoff` is the default.
  - `/compact <focus>` applies when the engineer judges that the remaining work stays in this worktree and reopens files this session already holds.
  - The model always names both exits and composes the focus line. The engineer applies the condition (row 4).
  - `handoff/SKILL.md` is unchanged. An engineer who types `/handoff` after seeing both exits has already chosen, and the hard block names both exits itself.
- **B.** Also add a warrant-step branch that recommends `/compact` and declines to write on a threshold-alone run. It costs three things:
  - re-pinning `_PINNED_HANDOFF_WARRANT_CHECK_CLAUSES["fallback_to_judgment_and_closing"]` and adding a pinned fork clause (`test_skills.py:5469-5504`);
  - an edit with zero net lines in a file already at its 200-line cap (row 15);
  - keeping the decline from halting `/ready-for-review` (`:37`). That needs either an edit to `ready-for-review/SKILL.md`, which is outside the Ask, or a warrant step that recognizes its caller.

**D2 — Permission prompts.** This is a genuine decision because the Ask says "seamlessly", while the repo bars wildcard allow rules. An exact rule cannot match `/resume-context <slug>`'s argument form (row 46).
- **A (recommended).** Add no grant. Verification 7b records whether default mode prompts for the script call and for the `Read` of the temp path (row 27). Auto and bypass modes do not prompt `[unverified]` (row 27).
- **B.** Add an exact `permissions.allow` entry for the no-argument form only, routed through `/review-permissions`. It covers the auto-load path but not the slug form.
- **C.** Add a skill-frontmatter `allowed-tools` entry with a prefix pattern. No skill in the repo scopes Bash this way today, and the no-wildcards rule exists to keep prefix patterns out.

**D3 — The wording of the compaction criteria (resolved).** This is a genuine decision for two reasons. The engineer directed the rule's form (row 51) but not its words. The words are also pinned verbatim in the nudge hook's test file (and quoted in the docs, checked by a parity test), so changing them after dispatch two means editing every pin. The wording below is the accepted wording (row 52).
- **Criteria phrase** (agent-checkable): "the next step continues the current task without switching to another worktree or repository, it needs files already read or edited in this conversation, and Claude has not been corrected more than twice on the same issue"
- **Timing sentence** (for the engineer; the agent passes it on): "Run /compact now rather than after a break: once the prompt cache expires, /compact reprocesses the full history as uncached input, which is when it costs the most."
- **Resolved:** the engineer asked for the architect's review ("Ask the architect to review"), and the architect's consult rewrote the draft into the two strings above, adding a stated tie-break: recommend `/compact` only if all three clauses hold; otherwise, or if any is unclear, recommend `/handoff`. The engineer accepted it via the selected label "Accept architect wording (Recommended)" (row 52). Each clause traces to the engineer's direction (row 51) or a vendor quote (row 53), and none depends on the cache lifetime.
- **Alternative:** add "at most five such files" to the second clause. This follows the vendor's limit of five files re-attached after compaction (row 26). I set it aside because a handoff re-reads the same files from scratch, so a sixth file does not favor either exit.

D1 option A's sub-bullet "The engineer applies the condition (row 4)" is the record of what was weighed. Later, the engineer directed that the instruction be "actionable for agents" (row 51). Under that direction the agent applies the D3 criteria and recommends one exit. Both exits are still named, and only the nudge's two messages carry the rule (row 49).

### Same-session resume

**Skill: `claude-skills/skills/resume-context/SKILL.md`** (row 2a).
- **Registration.** It is registered as `"resume-context": "name-only"` in `claude/.claude/settings.json` `skillOverrides`. It carries no `disable-model-invocation` and no TRIGGER blocks (row 13).
- **Frontmatter.**
  - `name`.
  - A one-sentence `description`: "Resume a pending /handoff or /brief file inside this session, after /clear or in a new session started in its worktree; distinct from the shell resume-context command, which starts a new process." The description is loaded by clients older than v2.1.129, so it must not read as a trigger, and it contains no "when".
  - `argument-hint: "[<slug> | <file name> | <absolute path>]"`.

The body has these steps:

0. Run only when the engineer asked to resume in this session. Never run the skill on your own, including right after writing a handoff or brief (row 39). If this conversation already loaded a file through this skill, run the script again only with an explicit argument. With no argument, tell the engineer and stop.
1. If `$ARGUMENTS` starts with `-` or contains any character outside `[A-Za-z0-9._/@+-]`, do not run the script; tell the engineer the accepted forms. Otherwise run `~/.claude/scripts/resume-context-same-session.sh $ARGUMENTS` as one Bash call (row 40). Never `Read` a path under `<config-dir>/handoffs/` or `<config-dir>/briefs/`, because that `Read` consumes the file before any check runs (row 7).
2. On `LOAD:`, compare before reading.
   - First compare `SESSION_TARGET:` with the worktree this session is anchored in: the working directory the environment context shows, or the `EnterWorktree` target. The script keys on the Bash `$PWD`, so an earlier `cd` into another worktree yields a verdict for that worktree. When `SESSION_TARGET:` is `none`, they match only if the anchored directory is not inside a linked worktree of any repository. If they differ, say so and stop before any `Read`, without acting on the file, and give the recovery text.
   - Otherwise `Read` the `LOAD:` path in full. Tell the engineer which `SOURCE:` file was consumed and its `AGE:`.
   - Then check the target. When the script printed `FILE_TARGET: none` and the file's §4 header (§3 in a brief) names a directory other than this session's, say so and stop without acting on the file, and give the recovery text.
   - Recovery text: the file is already consumed, so tell the engineer to `cd` to the directory named here and run `claude --append-system-prompt-file <LOAD path>`. Also tell them that `~/.claude/scripts/find-consumed-continuity-file.sh <slug>` finds the copy later. The directory is the `FILE_TARGET:` path when it is a path (on `LOAD:` it equals `SESSION_TARGET:`), otherwise the directory the file's §4 header (§3 in a brief) names if the file was read and it is an absolute path made only of `[A-Za-z0-9._/@+-]`. When neither gives such a directory, the text says the directory is unknown and must come from the session that wrote the file, and names none.
   - Otherwise, continue from the file. Its artifact preamble governs.
   - Track its pending and in-progress task-list items from the file, not from memory. Use the session's task-list tool if one exists, otherwise track them inline. This is the directive the shell path passes at `resume-context.sh:380`.
   - Do not call `EnterWorktree`, and do not run the file's §7/§7.5 command. The script already matched the file to this session.
3. On `SHELL_COMMAND:`, relay the line verbatim as a command for a new terminal, not as a slash command. When `FILE_TARGET: none` appears, add that the file names no worktree, so the command must be run from the directory the file was written in. When `SESSION_TARGET: unknown` appears, add that this session's worktree could not be determined (a path with a space or a failed git call), not that the file names another worktree. When `FILE_TARGET: unknown` appears, add that the script could not resolve one directory from the file, so the printed command has none, and that the engineer should check the file's own resume command outside this session (a `Read` here consumes it) and run that command in place of the printed one. The file is still pending. Stop.
4. On `CANDIDATE:`, `MORE:`, `SKIPPED_UNPRINTABLE:` or `STOP_REASON:` lines, relay them and stop.
   - Relay `CANDIDATE:` lines with any `MORE:` lines.
   - Relay `SKIPPED_UNPRINTABLE:` whenever it appears. Say that n pending files under `<config-dir>/handoffs/` or `<config-dir>/briefs/` have names with a character outside `[A-Za-z0-9._/@+-]`, so the script does not print them, and that the engineer can resume one with the shell `resume-context` and its full path. Do not list or name them.
   - Suggest `/resume-context <file name>` only for a candidate whose `target` is the same path as `SESSION_TARGET:`, or is `none` when `SESSION_TARGET: none`, and never when either is `unknown`. Any other candidate needs the shell command on its `CANDIDATE:` line.
   - Give the list step 3's `SESSION_TARGET: unknown` caveat once when that line appears. Give a `target none` or `target unknown` candidate step 3's caveat for that `FILE_TARGET:` value beside its shell command.
   - Relay `STOP_REASON:`.
5. On any other output, or when no recognized line appears, report the output and stop.

One line of the body distinguishes `/resume-context` from the code-fenced shell `resume-context`.

**Script: `claude/.claude/scripts/resume-context-same-session.sh [<slug> | <file name> | <absolute path>]`.** It is bash with mode `100755`. It sources `../hooks/_lib.sh` and `./_worktree-lib.sh`. Its stdout is the token contract, and every line has the form `<KEY>: <value>`. It exits 0 on every path that printed its verdict (`LOAD`, `SHELL_COMMAND`, `CANDIDATE` or `STOP_REASON`, including `consume-failed`). A non-zero exit means it stopped before finishing. Nothing in the repo branches on the status. Another script's exit 1 (`marker.sh check`, `no-match`) was rendered as an error-styled block, so a uniform exit 0 keeps a verdict from rendering that way. How the model behaves after such a block was not observed (row 68). The failure-class verdicts reach the engineer through SKILL.md step 4's relay of `STOP_REASON:` (Alternatives set aside).

- **Setup.** Each failure here prints a `STOP_REASON` and moves nothing.
  - `CONFIG_DIR=$(_lib_config_dir)` (row 44). Failure → `config-dir-unresolved`.
  - The config dir fails `_lib_passes_path_char_allowlist` → `config-dir-unprintable`, naming the shell `resume-context` as the route.
  - More than one argument, or one that starts with `-` (including `--cwd`) → `argument-not-allowed`. The reason states the accepted forms and that the slash form takes no `--cwd`.
- **Pending set.** It holds the regular, non-symlink files that match `"$CONFIG_DIR"/handoffs/*-handoff.md` and `"$CONFIG_DIR"/briefs/*-task.md`.
  - A missing directory contributes nothing.
  - The glob skips names with a leading dot, which the hooks' `case` patterns do match (row 45). A test pins this difference.
  - Names that fail the allowlist are dropped before any newline-joined collection is built. They are counted in one `SKIPPED_UNPRINTABLE: <n>` line. That line is printed with the candidate list and beside `nothing-pending`, and never with `not-pending`, the two-match list, `SHELL_COMMAND:` or `LOAD:`. So a `/resume-context <slug>` run whose own file has an unprintable name, while a different printable file is pending, gets `not-pending` with no skipped count (an accepted edge, because writers produce allowlisted names).
- **Session target.** The script calls `_lib_linked_worktree_root "$PWD"` and maps the result to one of three states (rows 55, 56):
  - `linked`: exit 0 with a root.
  - `not-linked`: exit 0 with empty output. This covers a main checkout and a directory outside any repository.
  - `unknown`: exit 1.

  It prints `SESSION_TARGET: <path|none|unknown>` on every path that reaches a verdict.
- **File target.** `F` is `_lib_continuity_file_resume_cwd <file>`, with three values: empty (the file holds no `--cwd` text anywhere), one absolute path (usable), or `unknown` (the script could not resolve one directory from the file). The script prints `FILE_TARGET: none`, the path, or `unknown`.
- **Canonicalization.** A non-empty root and a non-empty `F` pass through `worktree_canon_path` before the comparison. §7's path is model-written (row 9), so it can name the same directory through a symlink.
- **Verdict.** `_lib_resume_context_verdict STATE ROOT F` prints `load` or `shell`:
  - `linked`: `load` if `F` equals the root, otherwise `shell`.
  - `not-linked`: `load` if `F` is empty, otherwise `shell`.
  - `unknown`: always `shell`.

  An `F` of `unknown` never equals a root and is never empty, so it gets `shell` in every state, and its `SHELL_COMMAND:` omits `--cwd`. A main-checkout file whose model-written §7 carries `--cwd <main root>` therefore gets `shell`, which is safe but not seamless (Out of scope, residuals).
- **No argument.**
  - Nothing pending (checked before any argument, so an explicit-argument run on an empty pending set stops here too) → `STOP_REASON: nothing-pending`, naming `find-consumed-continuity-file.sh`. When `SKIPPED_UNPRINTABLE` is non-zero, the sentence says no pending file has a name inside the printable allowlist, not that none exists.
  - Auto-load applies only to a `linked` session. A `not-linked` or `unknown` session has no target match, so it lists candidates, and the file name the announce gives is the argument to pass.
  - A target match is a pending file whose `F` is non-empty and equals a `linked` root. A fresh file is under 7 days old (`find -mtime -7` is true). A failing `find` reads as not fresh.
  - The script loads a file when exactly one fresh target match exists. Stale matches do not count toward "exactly one".
  - Otherwise it lists up to 5 pending files as `CANDIDATE:` lines. Target matches come first, then everything else, each group newest first. Each line carries the file name, an age bucket, `target <path|none|unknown>`, and the file's shell command. A final `MORE: <n>` line appears when more files are pending. A stale match is listed, not loaded.
  - **Parse bound.** A linked session parses every pending file once, because the target decides selection and order. A not-linked or unknown session sorts the pending set without parsing and parses only the at most 5 files it lists. Cache lookups total at most 5 times the pending count. Each parse runs a `grep` and a `tail`, and nothing sweeps pending files, so a linked no-argument run costs time proportional to the pending count (Out of scope, residuals).
- **Explicit argument.** The argument matches a pending file only by exact equality with its full path, its file name, `<arg>-handoff.md`, or `<arg>-task.md`. The script never builds a path from the argument and never matches it as a pattern.
  - No match among a non-empty pending set → `STOP_REASON: not-pending`, naming `find-consumed-continuity-file.sh <arg>` and the accepted forms.
  - Two matches (a handoff and a brief that share a slug) → a `CANDIDATE:` line for each, keyed by file name. Nothing moves.
  - One match → the verdict decides. On `shell`, the script prints `SESSION_TARGET:`, `FILE_TARGET: <path|none|unknown>` and `SHELL_COMMAND: resume-context [--cwd F] <file>`.
- **Load path.**
  - The script computes the age bucket from the pending path before the consume.
  - Before consuming, `_lib_resume_context_tmpdir_root` must be an absolute path that passes the allowlist. Otherwise the script prints `STOP_REASON: tmpdir-unprintable`, with the `resume-context [--cwd F] <file>` command to run for that file, and moves nothing. `mktemp`'s suffix is alphanumeric, so a passing root yields a printable destination.
  - The script runs `resume-context.sh --consume-only <file>` and captures its stdout and exit status separately. It finds that script as its sibling, or through the test-only `RESUME_CONTEXT_CONSUMER` override, which mirrors `RESUME_CONTEXT_LAUNCHER` at `resume-context.sh:38-42`. The call sets `RESUME_CONTEXT_EXACT_SOURCE=1`, so the consumer never falls back to the legacy `$HOME/.claude` copy of a file that vanished after enumeration.
  - The script accepts the result only when the exit is 0 and stdout is one line naming a regular, non-symlink file directly under the tmpdir root, owned by the caller with mode 0600. It then prints `LOAD: <dest>`, `SOURCE: <file>`, `AGE: <bucket>`, `SESSION_TARGET:` and `FILE_TARGET: <path|none>`, because `unknown` never loads. It writes `_lib_print_recovery_hint <dest>` to stderr and exits 0.
  - Any other result is a failure. If `<file>` still exists, the script prints `STOP_REASON: consume-failed`, with the shell command to run for that file, and the file stays pending. Otherwise it prints `STOP_REASON: consumed-elsewhere-or-partial`, naming `find-consumed-continuity-file.sh <slug>` (rows 41, 42). It never prints `LOAD:` after a failure and never tries another candidate. The consumer's stderr passes through, so a lost race can print one of its own messages (`source file not found`, `failed to set permissions` or `failed to move`) beside `consumed-elsewhere-or-partial`.
- **Age buckets.** The buckets are `<1d`, `1-7d` and `>7d`, computed with `find <file> -maxdepth 0 -mtime -1` and `-mtime -7` on the pending path. Both tests are positive, so a failing `find` reads as `>7d`. The script uses neither `stat` nor `date -r`.
- **BSD userland** `[unverified]`: no BSD userland was available, and CI runs only on `ubuntu-24.04`, so nothing here exercises it. The script follows these rules:
  - Newest-first ordering uses `ls -t --` over allowlisted paths, for ordering only. Its output is re-filtered to files that still exist, and the unsorted list stands in when none survive. That fallback loses newest-first order, trading order for completeness: with more than five pending files, the newest can then sit behind `MORE:`.
  - No `\s`.
  - The constructs it still relies on, and most at risk on those platforms, are the `ls -ld` mode text with a trailing ACL marker, `find -maxdepth 0 -mtime` and `tail -c N < file`.
  - The script never prints file content, and every path it prints passes the allowlist.

**Helpers in `claude/.claude/hooks/_lib.sh`**, beside the existing resume-context helpers (row 12).
- `_lib_linked_worktree_root DIR` is extracted from the inline block at `announce-resume-command.sh:85-96` on `main` (row 33). It has three outcomes, and it follows the precedent that exit 1 with empty stdout means "no result" (row 55):
  - Exit 0 with the root: DIR is in a linked worktree whose root passes `_lib_passes_path_char_allowlist`.
  - Exit 0 with empty output: git answered "not linked". Either the git-dir equals the common-dir, or `rev-parse --git-dir` exited 128, which is read as not a repository. 128 is also git's status for other fatal errors, so a dubious-ownership refusal, a working directory that no longer exists and a pruned worktree read the same way (row 56).
  - Exit 1 with empty output: unknown. This covers an empty DIR (no git runs), any other git status (a fired cap, a missing git, a later call failing), a git-dir or common-dir that is not a single-line absolute path (git older than 2.31, row 43; idiom at `_lib.sh:615-632`), and a linked root that fails the allowlist.
  - It keeps the exact call order and arguments of the original block (`:87-91` on `main`): `git -C DIR rev-parse --git-dir`, then `--absolute-git-dir`, then `--path-format=absolute --git-common-dir`, then `--show-toplevel`. Each call goes through `_lib_capped` (5s). The announce tests' git stub and timing shim match on those argument positions.
  - Callers capture it as `if root=$(…); then …; else …; fi`, which is safe under `set -euo pipefail`.
- `_lib_continuity_file_resume_cwd FILE` returns what FILE's own `resume-context` commands say about `--cwd`. An own command is a line holding a `resume-context` or `*/resume-context` word and a word naming FILE's basename anywhere on it. §7 and §7.5 are the last numbered sections (row 60). On an own line, a `--cwd TOKEN` must be followed directly by FILE's basename, or that line's token is `unknown`: `--cwd /x other.md file.md` is such a line, while `--cwd /x other.md` alone names no own file and is ignored. It has three answers, and "absent" requires positive evidence:
  - **Absent (empty).** `grep -q -F -e --cwd -- FILE` over the whole file, not piped, finds no match. This is the only answer that rests on that search. A grep status of 2 or more reads `unknown`.
  - **Usable (one absolute path).** The file holds `--cwd` text, and the own commands in the scanned tail all name that one path, with backticks and one trailing `/` stripped and the allowlist satisfied.
  - **`unknown` (`$_LIB_RESUME_CWD_UNUSABLE`).** Everything else: `--cwd=`, a missing, quoted, relative, `~`, non-allowlisted or space-split token (the word after the token must be FILE's basename), two disagreeing own tokens, `--cwd` text with no own command carrying it in the tail (a decorated line such as `**resume-context --cwd … file**`, `resume-context.sh`, or a command before the tail), and an unreadable file. An own command without `--cwd` neither overrides nor hides one with it, in either order.
  - It tokenizes only the last 8192 bytes, dropping the cut first line, and only lines containing `resume-context`. The whole-file `--cwd` search is linear in the file's size. A trailing CR is ignored and a backslash-continued command is joined.
  - It is in-memory splitting under `set -f` in a subshell, with `IFS` pinned. It contains no `grep -m … |` pipe, which the `_lib.sh` lint sweeps in `test_hook_alignment.py` reject.
  - It exits 0 in every case.
  - **Why a sentinel.** The `unknown` answer rides stdout, though the session state uses an exit status (Alternatives set aside). A status cannot ride the script's cache rows (`<path> <target>`) or the verdict's three string arguments, and a non-zero status would abort the `set -e` script. `unknown` cannot collide with a usable value, because every usable value is an absolute path.
- `_lib_resume_context_verdict STATE ROOT F` is the pure verdict described above. It exits 0, and its input matrix is unit-tested directly.

**Announce hook** (row 33).
- **Helper call.** The hook calls `_lib_linked_worktree_root "$PAYLOAD_CWD"` in place of the inline block. Exit 1 gives the bare command, as a failed git call does today. Its header's fail-posture list says so.
- **Verdict.** The hook reads the written file's own target with `_lib_continuity_file_resume_cwd`, using the raw token without canonicalization. It runs `_lib_resume_context_verdict` against its own measured state. It advertises `/resume-context` only on `load`. Raw equality implies canonical equality, so the announce can under-promise but never promises a load that the worktree verdict will refuse in the same worktree. The script can still stop a same-worktree load for `tmpdir-unprintable` or `consume-failed`, neither of which the hook checks. A `--cwd` spelled non-canonically is the known difference: the hook withholds the slash form while the script canonicalizes and loads. Symlink and dotted (`.` or `..` components) spellings are both tested (`test_announce_resume_command.py` for the hook, `test_resume_context_same_session.py` for the script).
- **`systemMessage` on `load`:** "Resume in this worktree -- after /clear, or in a new session started here: /resume-context <file name>. Resume in a new process (from any shell): `<cmd>`; change --cwd to resume in a different worktree". When the hook computed no `--cwd` (a session outside a linked worktree), "this worktree" becomes "this directory" and "(from any shell)" becomes "(run it from this directory)", because only `--cwd` pins where the new process starts; the "change --cwd" clause is dropped.
- **`systemMessage` otherwise:** "Resume (run in your shell): `<cmd>`. /resume-context cannot confirm this file belongs to this worktree, because <reason>." A session outside a linked worktree reads "this directory" in place of "this worktree", as the `load` message does.
  - **One rule for the command's `--cwd`, in both arms.** When the helper returned a usable `<F>` (non-empty, not `unknown`) that also passes the hook's own `_lib_passes_path_char_allowlist` re-check, the advertised command is `resume-context --cwd <F> <file>`. This is the command the script prints as `SHELL_COMMAND:` for the same file, except that the script spells `<F>` by its canonical path (symlinks and `.` and `..` components resolved) and the hook prints the file's raw token. The symlink and dotted spellings of that difference are tested. An `<F>` that fails the re-check reads as unusable. Without a usable `<F>`, the command keeps `--cwd <root>` (linked) or has no `--cwd` (outside a linked worktree).
  - A linked session whose file names a usable `<F>` other than the root: "its resume command targets `<F>`, not this worktree (`<root>`)".
  - A linked session with an absent, relative or unreadable target: "its resume command does not name this worktree (`<root>`) as an absolute --cwd".
  - A session outside a linked worktree, with a usable target `<F>`: "its resume command targets `<F>`, and this session is not in a linked worktree".
  - A session outside a linked worktree, with an unreadable target: "its resume target could not be resolved to one directory, and this session is not in a linked worktree". This wording holds for both causes of an unreadable target: an own `--cwd` that could not be read, and `--cwd` text elsewhere in a file whose own command carries none.
- **`systemMessage` on helper exit 1 (unknown session state):** "Resume (run in your shell): `<cmd>`. /resume-context cannot confirm this file belongs to this worktree, because this session's worktree could not be determined." The other reasons would be false here.
- **`additionalContext`** names the commands it advertised, with the scope "(in this worktree)" or "(in this directory)" after `/resume-context <file name>` on the `load` arm. It tells the model to relay them to the engineer and run none itself. It keeps the instruction to "use them verbatim instead of reconstructing them".
  - **Provenance.** By default it says the commands "were computed from the resolved config dir and the working directory of this session". When the command's `--cwd` is a usable file target, it instead says the commands were computed from the resolved config dir except that the `--cwd` directory comes from the written file's own resume command, passed the path-character allowlist, and was not measured by the hook. It then also carries the mismatch reason beside the command, so the model never presents a file-written directory as session-derived.
- **No `--cwd` text without a usable target outside a linked worktree.** A session outside a linked worktree with no usable `<F>` (an absent target, or one that could not be resolved) gets the bare form that the skills' templates prescribe, and neither message contains `--cwd` text. With a usable `<F>`, the `--cwd <F>` of the one rule above applies, so the command no longer depends on the directory the engineer runs it from and the message has no "run from `<F>`" clause.
- **File name, not slug.** Both messages use the file name, not the slug. A handoff and a brief can share a slug but never a file name (row 64).

**Kill switch.** The script consumes the file whether or not the `consume_durable_continuity` kill switch is set, as the shell `resume-context` does (row 29). The `Read` hook remains the backstop for a direct `Read`.

**Alternatives set aside:**
- **Status quo** ("Read <path> and continue" after `/clear`): it has no discovery and no worktree check, and the `Read` consumes the file before anything can stop it (row 7).
- **A grep recipe in the skill prose, then a `Read` of the canonical path:** it puts multi-step Bash in skill prose, which CLAUDE.md routes to a script. The consume would also depend on the hook's kill switch.
- **A new mode of `resume-context.sh`:** its grammar requires a path, and its launch path would carry discovery logic it never uses.
- **Skill dynamic-context injection (a `!` command in the body):**
  - It substitutes `$ARGUMENTS` raw into a shell line before the model sees it, so step 1's character check would be skipped.
  - Its permission semantics are unverified.
  - The `Read` of the temp path would still be needed.
- **Parsing §4's header:** that header is free-form prose (row 10).
- **Accepting the shell command's `--cwd` as a pasted slash argument:** the announce message now gives the exact slash form. The same flag would also mean "match" in the slash form but "launch in" for the shell command.
- **`EnterWorktree` on a mismatch:** ruled out by row 3 and G4.
- **A sidecar recording the writer's PID or worktree**, to find "this process's" file: it adds new state with its own cleanup, while §7 already tells files apart (Out of scope, row 9).
- **Reading empty helper output as "not linked":** a linked worktree whose path holds a space, or a git timeout, would then load a file with no `--cwd` into the wrong worktree.
- **A stdout sentinel such as `unknown` for the third state:** it mixes a token into a path-valued output. Exit 1 already means "no result" in `_lib_repo_root` and `_lib_main_repo_root` (row 55).
- **Explaining the refusal only in the script:** after `/clear`, the script cannot know the writer's worktree, so it cannot offer the writer's measured `--cwd` root. Only the announce hook has that root in hand, and it offers it only when the file names no usable target of its own.
- **Having the announce tell the model to `Edit` §7 when its `--cwd` disagrees:** it makes §7 partly mechanical (Out of scope, row 9), and it rests on unverified edit-after-write behavior.
- **A distinct non-zero exit for the failure-class `STOP_REASON`s** (`consume-failed`, `consumed-elsewhere-or-partial`), against a uniform exit 0: an error-styled block might fit those two, since the second means the file moved and no usable copy was reported. It lost because nothing in the repo branches on the status and SKILL.md keys on the output lines. The error-class verdicts reach the engineer through step 4's relay of `STOP_REASON:`, which is prose and is not tested beyond the key-name check (row 68; Out of scope).

### Compact as an alternative to handoff (D1 option A, D3)

**`--check` stops reporting a fire from before a compaction** (rows 34, 35, 59).
- **Shared reader.** The fire path's marker read (`nudge-handoff-near-context-cap.sh:568-578` on `main`, including its guard against malformed values) becomes one function, `read_fired_marker <path>`. It is defined beside `resolve_rearm_spacing`, above the `run_check_mode` call at `:487-489`. It reports one of three states: absent, malformed, or a value.
- **Malformed handling.** The fire path treats malformed as absent and fires. `--check` treats malformed as fired. A one-line comment on the function states that each side fails toward the nudge.
- **The rule.** `--check` reports `already_fired: true` when the marker exists and either of these holds:
  - its value is malformed;
  - the current estimate is at or above the value.
- **What a lower estimate means.** An estimate below the recorded value means the estimate fell below that fire's. Compaction is the verified cause (G3), and no other cause is verified (row 35).
- **Write behavior.** `--check` still writes nothing, so the fire path, its log, and the lag join are untouched (row 20).
- **Consumers.** The JSON shape is unchanged. `handoff/SKILL.md:23`, `ready-for-review/SKILL.md:35, :172` and `plan-it/SKILL.md:148` keep their reading rule, and they now read a compacted session as under threshold.
- **Canonical statement.** This changes the meaning of one field in the contract. `docs/handoff-nudge.md:131` is the canonical statement of that meaning. It will read: "the marker exists and the current estimate has not fallen below the estimate it recorded, or the marker is malformed."

**Nudge messages.** Only the two message strings change, not the fire logic.
- **Shared text.** Both messages carry D3's criteria phrase and timing sentence verbatim (row 52). Both state that only the user can run `/compact` (G1).
- **No `/clear` instruction.** Neither message tells the engineer how to resume after `/handoff`, and neither says "then /clear". The announce message after the write gives the resume route, and it differs by worktree (row 51).
- **Next fire.** Both state the next fire point `<ESTIMATE + REARM_SPACING>` (row 19) and what fires there: "Next hard block at" when that point is at or above the resolved `BLOCK_AT`, otherwise "Next nudge at". A hard block's own point is always at or above `BLOCK_AT`, so on `PostToolBatch` with no live `/handoff` marker the next fire blocks again (row 19). The point is computed once from the validated `ESTIMATE` and `REARM_SPACING`. The label is computed once, after `resolve_block_at`. The soft nudge passes them to `jq -n` as a new `--argjson` and `--arg`. The hard block's `printf` gains two `%s`.
- **Soft nudge (`additionalContext`):**
  - The opening `threshold (<N> tokens)` sentence stays.
  - If the current task is not close to done, the agent recommends one exit: `/compact` only if all of the criteria phrase's clauses hold; otherwise, or if any is unclear, `/handoff`. It names the other exit as the alternative.
  - The message always includes a ready-to-paste `/compact <focus>` on one line (row 38). The focus names the task, the next step, the skill in flight and its current step (row 66), files to re-read first, open decisions with the user's quoted words, and irreversible actions not yet authorized.
  - The agent passes on the timing sentence with it, plus the next fire point after a `/compact`.
  - The stale "captures state in a /tmp file" clause is dropped. The per-turn-cost sentence and the closing "If the task is nearly complete…" sentence stay.
  - No earlier "nearly complete" and no "25%" may appear (row 18).
- **Hard block (stderr).** It stops the loop before any model call, so no model composes a focus line (row 54). The `/compact` exit therefore names the form the engineer can type unaided:
  - `handoff-nudge hard-block point (HANDOFF_NUDGE_BLOCK_AT=%s)` stays on one unwrapped line.
  - "after %s ignored re-arms" becomes "after %s re-arms", because a compliant compaction still increments the counter.
  - Then: run `/handoff` now. If all of the criteria phrase's clauses hold, the user can run a bare `/compact` instead, and a focus line is optional (the tie-break's inverse: default `/handoff`). Then a conditional lead-in such as "If you choose /compact:" before the pinned timing sentence, outside the pinned string, so the message has one "now" command. Then the next fire point, worded "next hard block at": the marker now holds an estimate at or above `HANDOFF_NUDGE_BLOCK_AT` and a `/compact` does not reset it, so the next `PostToolBatch` fire blocks again (row 19).
  - It contains none of row 18's excluded phrases.
- **No cost comparison.** Neither string calls compaction cheaper than a handoff, because nothing measures that (row 25). The timing sentence compares `/compact` only with itself, as the docs do (row 53).

**Docs.** `docs/handoff-nudge.md` changes in eight places:
- `:18` describes the recommendation of one exit and points to the new section below.
- `:22` ("ignored re-arm") gains a clause that a re-arm after a compliant compaction also counts, pointing to the `"A /compact does not reset the nudge"` bullet under "Known limitations".
- `:34-38` adds `/compact` as a route out of a hard block, so "Three routes out" at `:34` becomes four. The same lines correct "the estimate only grows" and the stale `/tmp` description of `/handoff`.
- `:131` gets the new meaning of `already_fired`, and records that it changed with this PR's merge date, so a later audit of `handoff_signal_response.py:254` can split signal counts on that date (row 67).
- A new section, "Compaction as an alternative to /handoff", gives each criterion beside its literal vendor quote, plus the timing sentence beside its quote (rows 26, 53). Each quote carries its URL and heading, "fetched 2026-10-18" and "re-verify by 2027-01-18". The section states the five-file re-attach limit, and it states that the agent needs no knowledge of the cache lifetime.
- Two new bullets under Known limitations. The first is "A /compact does not reset the nudge.":
  - After a `/compact`, the push nudge stays silent until the estimate reaches the last fire plus `HANDOFF_NUDGE_REARM_SPACING`. That is about 235000 after a first fire at 155000, and at least 550000 after a hard block at 470000. The fire at the blocked estimate plus spacing is itself a hard block on `PostToolBatch`.
  - The `-ignored` count includes re-arms that follow a compliant compaction.
  - `--check` is unaffected.
  - The signal-response audits do not count `/compact`. The transcript's `isCompactSummary` records are the source a later audit would use to measure uptake, and whether they tell a user `/compact` from an auto-compaction is unverified (row 62).
- The second is "A long skill body can come back partial.":
  - A skill body over the 5,000-token re-injection cap (row 26) comes back partial after a compaction, while its active markers survive (G3).
  - The bullet cites the `wc -c` command and the 4-bytes-per-token estimate, not the byte counts of row 66. Of the workflow skills, `code-review`, `plan-review` and `ready-for-review` exceed the cap by that estimate. A `/compact` mid-flow in one of them can leave the model without later steps. The focus line names the skill in flight and its current step.
- The existing 200k-window bullet under Known limitations ("The hard block is unreachable on a 200k-window model") replaces "the advisory nudge re-arms indefinitely" with the fire count. At the default spacing a 200k-window session gets at most two advisory fires, at about 80000 and about 160000. The second fire's "Next nudge at" point (at least 240000) is past the window, so that nudge never arrives, whether or not the session compacts.
- The Log-location table's `nudged` row (`:158`) gains a clause on the `ignored=` count, pointing to the `"A /compact does not reset the nudge"` bullet under "Known limitations".

Every pointer to the first bullet (`:22`, `:38`, `:97`, `:158`) names it by its title, `the "A /compact does not reset the nudge" bullet under "Known limitations"`, so none can match the second bullet, whose body also says "after a compaction".

`docs/handoff-nudge.md` keeps the phrase `` `HANDOFF_NUDGE_BLOCK_AT` (default N) `` (row 18). `docs/hooks.md:175` and the README's "When to use which" rows defer to the new section instead of restating the criteria.

**Prior stance.** This narrows the given in `token-cost-reduction.md`, "Compaction is not an acceptable mechanism in this workflow", to the engineer's conditional Ask (row 32).
- A dated follow-up paragraph in `docs/cost-levers-considered.md` records the narrowing under that plan's section (row 58). The paragraph says four things:
  - the nudge now recommends `/compact` under the D3 criteria;
  - `CLAUDE.md` guidance stays rejected for the reason its row gives;
  - the compaction stance that row cites is narrowed;
  - the comparison of cost against a handoff is unmeasured (row 25).
- `.claude/plans/precompact-review-snapshot.md:48` restates a related given about `/clear`. This plan leaves `/clear` alone, and merged plans are read-only records, so that file is not edited.

**Alternatives set aside:**
- **A compact branch in the warrant step:** this is D1 option B.
- **Changing the consumers' reading rule**, so that `already_fired` with `over_threshold:false` means "nudged earlier": this edits `ready-for-review/SKILL.md` and `plan-it/SKILL.md`, which are outside the Ask, plus their pins (`test_skills.py:5785`, `:5837`, `:5895`). The problem lies in how one field is computed.
- **Resetting the fired marker on compaction**, through a SessionStart(compact) hook or shrink detection on the fire path: the hook would then re-fire at an estimate below an earlier fire's. The lag join's first-crossing rule maps that fire back to the pre-compaction turn, and `rearm-backtest`'s band simulation does not model the reset (row 20).
- **A `# Compact instructions` section in CLAUDE.md:** it is always loaded, and it also steers auto-compactions.
- **SessionStart(compact) injection:** it fires after the choice has been made.
- **Composing the focus line only after the engineer picks `/compact`:** the extra turn past the threshold re-reads the whole context (row 38).
- **Giving the agent the cache lifetime so it can judge timing:** unnecessary under any lifetime, because "now rather than after a break" and "once the prompt cache expires" hold whatever lifetime applies. The lifetime is selected by billing mode and by settings (`ENABLE_PROMPT_CACHING_1H`, `FORCE_PROMPT_CACHING_5M`, `promptCacheTtl`, per `docs/cost-levers-considered.md:32-50`), so naming one value would be wrong for some sessions.
- **Dropping `/compact` from the hard block:** that removes the exit at the depth where shedding context matters most, while a bare `/compact` is executable by the engineer (row 54).

### Predecessor active markers after `/clear`

Resolved: option A, via the engineer's selected label "A: pin, don't delete (Recommended)" (row 31). This was a genuine decision because the engineer asked for a fix ("I think the plan should still fix it"), while the evidence reads the leftover markers as inert and deliberately resumable.
- **Why pin, not evict:**
  - Every reader keys on the caller's current session id (row 21).
  - The markers become live again if the cleared session is resumed (row 23). `capture-session-id.sh:101-112` exists to provide that preservation.
  - `marker.sh clear-stale` evicts them after 60 idle minutes.
  - Eviction would have to skip review-pr artifacts and `.planmode-path` sidecars (row 22) and infer the predecessor id (row 24), for no observable benefit.
  - The real hazard is a future change that carries a predecessor's bypass over to the new id.
- **A (chosen).** Add a test asserting that `/clear` carries no predecessor marker over to the new id, in `test_capture_session_id.py`, and one sentence in `docs/hooks.md:173`. The sentence says that `/clear` leaves the ending session's active markers under its old id, where no gate reads them, until either `clear-stale` evicts them after 60 idle minutes or a resume of that session picks them up again.
- **B (record only).** Evict on `SessionStart` with `.source == "clear"`, before `capture-session-id.sh:96` overwrites the mapping, and never touch suffixed entries. Cost: a cleared session that is resumed later must re-run its skill.

### Assumption ledger

**Root problem:** a `/clear`ed session has no one-step way to resume from a pending handoff or brief in its own worktree. In the engineer's words: "Enable a cleared session to work with handoffs seamlessly. Currently I have to close a session and start a new one with the resume-context script." The context-shedding advice also never offers `/compact`. Separately, `--check` keeps reporting a session as past its threshold after a compaction, so a compacted session is routed back to `/handoff`.

**Givens:**
- **G1.** Claude has no tool binding for `/compact` or `/clear`; only the engineer can run either. The vendor imposes this. `[verified: docs/cost-levers-considered.md:330]`
- **G2.** `/clear` starts a new `session_id` in the same `claude` process and keeps the cwd. This is vendor behavior. `[verified: Context's probe, one run, version unrecorded; re-run in Verification 7a]`
- **G3.** Compaction keeps the `session_id`. This is vendor behavior. `[verified: docs/precompact-hook-behavior.md:51, :66]`
- **G4.** An in-process worktree move is partial: `EnterWorktree` reloads neither CLAUDE.md nor project hooks, and a later `/clear` reloads only CLAUDE.md. This is vendor behavior. `[verified: Context's probe, one run]`

| # | Assumption | Tag |
|---|---|---|
| 1 | The skill is named `resume-context` | `[engineer-verified: "How about resume-context? That uses the same naming as the script."]` |
| 2 | A command or name-only skill is the requested surface | `[engineer-verified: "Ideally I could run a command/nameonly skill to do what resume-context did"]` |
| 2a | The skill is registered name-only in `skillOverrides`, not user-only | `[engineer-verified: "Name-only"]` |
| 3 | A different target worktree still needs the shell `resume-context` | `[engineer-verified: "Resume-context will still need to be used regardless of the other two's existence if the next session needs to go into a different worktree than its predecessor"]` |
| 4 | Compaction is offered as an option. The shipped criteria (D3, rows 51-52) replace the quoted phrasing: the efficiency conjunct became the timing sentence, and the docs' "corrected more than twice" clause was added | `[engineer-verified: "Make compact an efficient option to use if the same files need to be opened by the main session and it would be more efficient to use compaction than clearing and resuming from handoff file"]` |
| 5 | Same-session resume and the compact option ship together | `[engineer-verified: "Those options aren't mutually exclusive"]` |
| 6 | `token-cost-reduction.md:173-174` records "Compaction is not an acceptable mechanism in this workflow". Its objection, the continuity of the review narrative (`:80-88`), is mitigated by the always-loaded instruction to run `review-ledger.sh render` after compaction mid-review | `[verified: token-cost-reduction.md:73-92, :173-174; claude/.claude/CLAUDE.md:124]` |
| 7 | A `Read` of `<config-dir>/handoffs/*-handoff.md` or `briefs/*-task.md` consumes the file. The `mktemp` destination matches neither glob | `[verified: consume-durable-continuity-file-on-read.sh:123-135; resume-context.sh:334]` |
| 8 | `--consume-only` is the single implementation of the move (move, index row, `chmod 600`), and its stdout is exactly the destination | `[verified: resume-context.sh:11-17, :73-75, :326-358]` |
| 9 | The authoring model writes §7 and §7.5 from the skill template ("If §4 named a worktree"). The announce hook writes nothing into the file. It builds its command from its own measured root, or from the file's own usable `--cwd` target when the file names one that differs from the session's. `check-handoff.py` checks only that §7 names its own file. So §7's `--cwd` can be absent, can name the directory through a symlink, or can carry `~` | `[verified: handoff/SKILL.md:133; brief/SKILL.md:97; announce-resume-command.sh:133-137, :169-185 (the root-built command and the file-target arm); check-handoff.py:173-184]` |
| 10 | §4's header is free-form prose | `[verified: handoff/SKILL.md:121]` |
| 11 | The linked-worktree predicate is written inline at six sites with no shared helper. `_lib_repo_root` and `_lib_main_repo_root` compute a different predicate | `[verified: grep git-common-dir across hooks/ and scripts/ (prior draft); _lib.sh:593-632]` |
| 12 | The resume-context helpers already in `_lib.sh` are used only by scripts | `[verified: grep of claude/.claude/scripts/*.sh — _lib_resume_context_tmpdir_root and _lib_print_recovery_hint appear in resume-context.sh, find-consumed-continuity-file.sh and resume-context-same-session.sh; _lib_resume_context_index_dir only in the first two]` |
| 13 | A name-only skill is excluded from `_model_invokable_skills()` through `skillOverrides`, so it needs no TRIGGER blocks. It needs a `` | `/<name>` | `` row in docs/skills.md. The setting requires v2.1.129+, and older clients load the description | `[verified: test_skills.py:177-213, :2881-2899; docs/skills.md:55, :94]` |
| 14 | test_doc_counts.py pins the name-only counts in docs/skills.md. Adding `resume-context` raises the pinned name-only total, the pinned no-TRIGGER count, and the workflow-utility count by one each; "The other seven" is unchanged. The workflow-utility count appears in the unpinned phrases at `:42` ("the N workflow-utility name-only skills") and `:55` ("M of the N workflow utilities"), which also change | `[verified: test_doc_counts.py:513-545; docs/skills.md:42, :55]` |
| 15 | `handoff/SKILL.md` is at its 200-line cap. Load-bearing only for D1 option B | `[verified: Read this session (prior draft); check-skill-length.sh:111-119]` |
| 16 | The warrant heading is cited verbatim elsewhere. Load-bearing only for D1 option B | `[verified: plan-it/SKILL.md:148; ready-for-review/SKILL.md:41]` |
| 17 | A §2 reason, an explicit engineer request, or the session ending already warrants a write. The warrant step's threshold-alone callers are `ready-for-review` Step 1, `plan-it` Step 7 and a model run driven by the hard block. `ready-for-review` argues that a handoff is right (`:36`) and treats a declined write as a halt (`:37`) | `[verified: handoff/SKILL.md:28; ready-for-review/SKILL.md:35-37; plan-it/SKILL.md:148]` |
| 18 | Each consumer of the nudge text constrains the rewrite (detail below) | `[verified: handoff_signal_response.py:41, :216-229; test_doc_counts.py:309, :388; test_nudge_handoff_near_context_cap.py:1451-1455, :2166, :2177-2193, :2217]`, plus `:2820` and `test_doc_counts.py:608` as reported by this session's platform review |
| 19 | The fired and ignored state is keyed by session id. A re-arm needs `ESTIMATE >= LAST_FIRED_AT + REARM_SPACING`. After a fire at estimate E, the next fire comes at E + `REARM_SPACING`. A fire at E ≥ `BLOCK_AT` therefore makes the next `PostToolBatch` fire a hard block, compaction or not, unless a live `/handoff` marker keeps it advisory | `[verified: nudge-handoff-near-context-cap.sh as staged by this PR: fired-marker read via read_fired_marker :178 and :615-616, re-arm check :617, hard-block condition :700-701, marker write in the block arm :714; the spacing is resolve_rearm_spacing]` |
| 20 | A reset of the fire path after compaction would fire below an earlier fire's estimate. The lag join's first-crossing rule would then pick the pre-compaction turn, and `rearm-backtest`'s simulation of bands does not model the reset | `[verified: transcript_analysis/handoff_nudge.py:351-398; rearm_backtest.py:181-246]` |
| 21 | Every reader of active markers keys on the caller's current session id. The rewrite loop touches only the new id. `clear-stale` evicts a marker whose PID is live once it has been idle 60 minutes | `[verified: _lib.sh:2759-2763; nudge-handoff-near-context-cap.sh:672-688; capture-session-id.sh:106-112; marker-clear-stale.py:237-248]` |
| 22 | `.*-active.d/` also holds review-pr artifacts and `.planmode-path` sidecars | `[verified: marker-clear-stale.py:29-40, :165-182]` |
| 23 | A cleared session that is resumed later re-presents its id. Proven for `claude --resume` in a new process; in-process `/resume` is `[unverified]` | `[verified: capture-session-id.sh:101-112]` |
| 24 | SubagentStart carries the parent's `session_id`. Load-bearing only for option B | `[unverified]` |
| 25 | Under the D3 criteria, compaction costs less than a handoff plus `/clear` plus resume. Nothing measures this, so the strings do not claim it | `[unverified]` |
| 26 | After compaction, Claude Code re-reads up to five files Claude read or edited, most recently modified first. A file over 5,000 tokens comes back as a path reference. Invoked skill bodies are re-injected within caps of 5,000 tokens per skill and 25,000 in total. The plan-mode plan, project-root CLAUDE.md and auto memory are re-injected from disk | `[verified: code.claude.com/docs/en/context-window § "What survives compaction", fetched 2026-10-18 by the session's verify-sources run; quoted in this dispatch, not re-fetched here]` |
| 27 | A `Read` of the temp path is denied by no hook (a Phase 1 test closes this half). Unknown: whether default mode prompts for the script call or the `Read`, and whether auto mode's classifier skips, prompts, or denies either | `[unverified]` |
| 28 | A name-only skill receives `$ARGUMENTS` | `[verified: pr-description/SKILL.md:75 under settings.json:95 name-only]`; live check in 7b |
| 29 | The shell `resume-context` has no kill switch | `[verified: docs/scripts.md:340]` |
| 30 | Nine bare `"resume-context" in` assertions in the announce tests become vacuous once the copy contains `/resume-context`. Several assertions require that the copy for a session outside a linked worktree contain no `--cwd`. The git stub accepts only the four `rev-parse` argv shapes | `[verified: reported by this session's staff-sdet review at test_announce_resume_command.py:69-105, :141-282, :362-365 (line numbers as of the review, before the edits this plan describes); not reopened here]` |
| 31 | Predecessor markers are pinned by a test, not evicted | `[engineer-verified: "A: pin, don't delete (Recommended)"]` |
| 32 | Row 6's earlier given on compaction is narrowed by row 4 | `[engineer-verified: "Yes, narrow it"]` |
| 33 | `announce-resume-command.sh` gets both the message change and the `_lib_linked_worktree_root` extraction | `[engineer-verified: "Keep both"]` |
| 34 | `already_fired` today means only that the marker exists. Three consumers read `over_threshold` or `already_fired` as "past threshold": `ready-for-review` Step 1, the handoff warrant step, and `plan-it` Step 7 through that step. A compacted session therefore reads as past threshold for the rest of its life | `[verified: nudge-handoff-near-context-cap.sh:424-425 (on `main`); handoff/SKILL.md:23; ready-for-review/SKILL.md:35, :172; plan-it/SKILL.md:148]` |
| 35 | Compaction drops the hook's estimate below an earlier turn's value in the same session. Without compaction or clearing of context, the latest turn never falls below an earlier fire's estimate, because each turn's input carries the prior output | Dip: `[verified: handoff_nudge.py:363-368]`; no drop otherwise: `[unverified]` |
| 36 | The first `--check` after `/compact` reads a turn from after the compaction | `[unverified]` (7e) |
| 37 | The stderr of the hard block reaches whoever reads PostToolBatch's exit-2 output, which may be the engineer, the model, or both. The wording addresses both | `[unverified]` (7e2) |
| 38 | Composing the focus line every time costs less than a round trip past the threshold. The round trip re-reads the whole context, by the nudge's own premise that per-turn cost rises with carried context | `[unverified]` |
| 39 | Name-only lets the model invoke the skill by name. Only step 0 and the announce wording keep it from invoking the skill on its own | `[verified: docs/skills.md:94]`; that the guard holds: `[unverified]` (7g; fallback row 57) |
| 40 | The charset `[A-Za-z0-9._/@+-]` contains no shell metacharacter or quote | `[verified: _lib.sh:321-330]` |
| 41 | `--consume-only` can exit 1 at two positions when `chmod` fails: before the move, from `chmod 600 -- "$SRC"` with the file still pending, and after the move, from the destination `chmod`, with the file already moved | `[verified: resume-context.sh:329-332, :352-355]` |
| 42 | Two concurrent consumes of one file have a single winner. The loser exits 1 without moving the file. Depending on when it arrives, it fails at the existence check (`source file not found`), at the source `chmod` (`failed to set permissions`), or at its own `mv` (`failed to move`). Only the last leaves an empty 0600 file from `mktemp`, because the first two fail before `mktemp` runs | `[verified: resume-context.sh:297-304, :329-339]`; the race ordering is read from the code, not run |
| 43 | Git older than 2.31 echoes `--path-format=absolute` as output and exits 0 | `[verified: _lib.sh:615-616]` |
| 44 | `_lib_config_dir` strips a trailing slash and refuses a relative `CLAUDE_CONFIG_DIR` | `[verified: _config.sh:94-124]` |
| 45 | A shell glob skips names with a leading dot, which the hooks' `case` patterns match | `[verified: announce-resume-command.sh:113-114]` |
| 46 | `permissions.allow` holds only exact-match entries, and the repo bars wildcards. An exact entry cannot match an argument form | `[verified: settings.json:3-33; CLAUDE.md "No wildcards in permissions.allow"]` |
| 47 | Pending files are never swept, so stale files accumulate | `[unverified]` (platform review's observation) |
| 48 | An edit to `settings.json` triggers `permissions.ask` | `[verified: settings.json:44]` |
| 49 | The compact rule lives in the nudge's two messages only (D1 option A); `handoff/SKILL.md` is unchanged, narrowing the accepted scope's "in the handoff skill and nudge hook" | `[engineer-verified: "Yes, nudge only"]` (confirmed after the architect's view was relayed) |
| 50 | No permission grant for `/resume-context` (D2 option A) | `[engineer-verified: "No grant (Recommended)"]` |
| 51 | The compaction wording is grounded in the vendor docs. It leaves out "then /clear", because a change of worktree may need a new session. It is made actionable for agents, because the docs' material is written for a human reader | `[engineer-verified: "I think docs-grounding wording is great but I'd leave out the "then /clear" bit right? Because if I need to change worktrees then I may need to restart a new session rather than clear." … "I think we need to edit this instruction so that it is actionable for agents. The primary source you cited provides good material but its audience is the human"]` |
| 52 | D3's criteria phrase, timing sentence and tie-break are the wording that ships | `[engineer-verified: "Accept architect wording (Recommended)"]` |
| 53 | The vendor docs say five things. `/compact` reads the conversation it summarizes, while `/clear` costs nothing. Compaction works when the discarded context is no longer needed. A mid-session `/compact` reads a warm cache, while after a break longer than the cache lifetime it "reprocesses the full history as uncached input" and "costs the most". The docs say to clear "when switching to unrelated work". The docs also say that a session corrected "more than twice on the same issue" should be cleared | `[verified: code.claude.com/docs/en/costs § "Why usage climbs in a long session"; code.claude.com/docs/en/prompt-caching § "Compacting the conversation"; code.claude.com/docs/en/context-window § "When your context fills up"; code.claude.com/docs/en/best-practices § "Course-correct early and often"; fetched 2026-10-18 by the session's verify-sources run; quoted in this dispatch, not re-fetched here]` |
| 54 | PostToolBatch exit 2 "Stops the agentic loop before the next model call", so no model composes a focus line at a hard block. A bare `/compact` is a valid form | `[verified: docs/handoff-nudge.md:22; .claude/plans/handoff-hard-block.md:28 quoting code.claude.com/docs/en/hooks]`; bare form: `[verified: row 53's prompt-caching quote, "run `/compact` at a natural break"]` |
| 55 | `_lib_repo_root` and `_lib_main_repo_root` signal "no result" with exit 1 and empty stdout. A caller that branches on `_lib_capped`'s exit status must fail closed on every status it does not recognise | `[verified: _lib.sh:602-603, :617-618, :117-120]` |
| 56 | `git rev-parse --git-dir` exits 128 outside a repository. 128 is also git's status for other fatal errors, so three causes read as "not linked": a dubious-ownership refusal, a working directory that no longer exists, and a linked worktree whose gitdir entry was pruned | Removed working directory: `[verified: git -C <nonexistent dir> rev-parse --git-dir printed "fatal: cannot change to …" and exited 128, run this session]`. Dubious ownership and pruned worktree: `[unverified]`. The outside-repo half is pinned by a `test_lib.py` case |
| 57 | `user-invocable-only` hides the skill and bars model invocation while keeping it typeable by the user | `[verified: docs/skills.md:95]` |
| 58 | The cost-lever register supersedes a row with a dated follow-up paragraph instead of editing it. Its row on `/compact` guidance gives the narrowed compaction stance as part of its reason | `[verified: docs/cost-levers-considered.md:12-15, :330]` |
| 59 | `--check` returns from `run_check_mode`, called at `:487-489`, before the fire path's top-level marker read at `:609-616` runs. A function it calls must be defined above `:487` | `[verified: nudge-handoff-near-context-cap.sh:344, :487-489, :609-616]` |
| 60 | §7 is a handoff's last numbered section and §7.5 a brief's | `[verified: handoff/SKILL.md:81-133; brief/SKILL.md:60-97]` |
| 61 | Four detector tests already run the hard block's previous wording through the detector | `[verified: test_transcript_handoff_signal_response_detection.py:24-41, five references to _handoff_hard_block_attachment()]` |
| 62 | A transcript marks a compaction with an `isCompactSummary` record. Whether that record tells a user `/compact` from an auto-compaction is unknown | Record: `[verified: handoff_nudge.py:363-368]`; distinction: `[unverified]` |
| 63 | `RESUME_CONTEXT_TMPDIR` relocates both the consume destination and the consumed-file index | `[verified: _lib.sh:4551-4559, :4587]` |
| 64 | A handoff and a brief can share a slug but never a file name, since their suffixes differ | `[verified: announce-resume-command.sh:114]` |
| 65 | Row 52's acceptance covers both reshapings of row 4's condition (efficiency conjunct → timing sentence; corrections clause added) | `[engineer-verified: "Yes, both covered"]` |
| 66 | Skill bodies larger than the 5,000-token post-compaction re-injection cap (row 26) come back partial. Measured with `wc -c`: `code-review` 82,585 bytes, `plan-review` 32,117, `ready-for-review` 25,163, `handoff` 19,108, `plan-it` 18,180, `brief` 11,915. At about 4 bytes per token (an estimate, not measured), that is roughly 20.6k, 8.0k, 6.3k, 4.8k, 4.5k and 3.0k tokens, so three exceed the cap | `[verified: wc -c this session]`; which end of a body survives: `[verified: code.claude.com/docs/en/context-window § "What survives compaction", fetched 2026-10-18, as quoted in docs/handoff-nudge.md: "Truncation keeps the start of the file"]`. Whether a compacted session behaves that way is observed in 7e.8. Superseded in review: `docs/handoff-nudge.md` no longer lists these byte counts, so they cannot drift there; it gives the `wc -c` command instead |
| 67 | `handoff_signal_response.py:254` counts a `--check` result as a signal when `over_threshold` or `already_fired` is true, so signal counts before and after this PR's merge date are not like-for-like | `[verified: handoff_signal_response.py:254, reported by this session's staff-product review]` |
| 68 | The script exits 0 on every verdict path. Another script's exit 1 (`marker.sh check`, `no-match`) was rendered by the Bash tool as an error-styled block. What the model does after such a block (relay the stdout verdict, retry, or call the run a failure) is not known, and nothing shows that a verdict-bearing non-zero exit would be mishandled | Render: `[verified: observed once, `marker.sh check` exiting 1 with `no-match`, as reported to this plan's review of the sync revision]`. Model behavior: `[unverified]` (7i) |

Row 18 detail: each consumer of the nudge text constrains the rewrite.
- The production detector matches the substring "handoff-nudge hard-block point".
- `test_doc_counts.py` parses `threshold \((\d+) tokens\)` from the soft nudge and `HANDOFF_NUDGE_BLOCK_AT=(\d+)\)` from the stderr of the hard block. It also matches `` `HANDOFF_NUDGE_BLOCK_AT` (default N) `` in docs/handoff-nudge.md.
- The stderr of the hard block must keep `/handoff` and `HANDOFF_NUDGE_BLOCK_AT=`.
- The stderr must not contain "genuinely almost done", "too aggressive for your workflow", or "judge that by what the remaining work costs".
- The soft nudge must not contain "25%".
- The soft nudge's first "nearly complete" must sit in the sentence that carries the cost qualifier.

Row 4 stays the Ask. The shipped criteria do not restate it:
- Row 51 is the engineer's direction on their form.
- Row 52 is the accepted wording.
- Row 53 is their documentary basis.

Row 4's first conjunct survives as the criterion about files already read. Its efficiency conjunct survives only as the timing sentence, which the docs ground. The comparative claim stays unmeasured (row 25). No surface calls the criteria "the engineer's condition".

### Mechanism justification

- **The `/resume-context` skill and a dedicated script** (`anchors: root`, `row2`, `row2a`, `row7`). Four lighter primitives were checked:
  - (a) The status-quo "Read and continue" has no discovery and consumes the file before any check (row 7).
  - (b) A grep recipe in the skill prose puts multi-step Bash in prose, and the consume would depend on the kill switch.
  - (c) A mode of `resume-context.sh` breaks its grammar, which requires a path.
  - (d) `!` injection substitutes `$ARGUMENTS` raw into a shell line (row 40).
- **Matching §7's canonicalized `--cwd` against the shared `_lib_linked_worktree_root`** (`anchors: row9`, `row10`, `row11`, `row33`, `row60`). Three lighter options were checked:
  - Parsing §4 fails because §4 is free-form.
  - Plain string equality never matches a symlinked spelling of a path the model wrote (row 9).
  - `_lib_repo_root` with `_lib_main_repo_root` computes a different predicate.
- **A third, unknown session state carried on the exit status** (`anchors: row55`, `row56`). Two lighter options were checked:
  - Reading empty output as "not linked" loads a file with no `--cwd` into a linked worktree whose path holds a space, or after a git timeout.
  - A stdout sentinel mixes a token into a path-valued output, while exit 1 already means "no result" in two `_lib.sh` helpers.
- **The announce hook runs the same verdict before advertising the slash form** (`anchors: row9`, `row33`). Two lighter options were checked:
  - Always advertising it promises a load the script refuses whenever the model left `--cwd` out of §7.
  - Explaining the refusal only in the script cannot offer the writer's measured worktree root, because after `/clear` only the announce hook ever had it. The hook offers that root only when the file names no usable target of its own.
- **File names, not slugs, in the announce and candidate lines** (`anchors: row64`). The lighter option is to keep slugs. It fails because a handoff and a brief sharing a slug make the suggested argument resolve to two files.
- **Consuming via `--consume-only`, then reading the temp path, with a check of the tmpdir root first and every failed consume mapped to a `STOP_REASON`** (`anchors: row7`, `row8`, `row29`, `row41`, `row42`). The lighter option is to let the `Read` hook consume the file. It fails twice:
  - That hook honors a kill switch an explicit resume should ignore.
  - It fires after the `Read`, when a mismatch can no longer be undone.
- **Name-only registration** (`anchors: row2a`, `row13`, `row39`, `row57`). The engineer chose name-only over `disable-model-invocation`. The guard against the model invoking the skill on its own is therefore prose: step 0 and the announce wording. 7g tests that guard live, and a failure goes back to the engineer with `user-invocable-only` as the alternative.
- **Auto-load without an argument only for a unique fresh target match** (`anchors: root`, `row47`). Two lighter rules were checked:
  - "Never auto-load" adds a listing round trip after every `/clear`, against "seamlessly".
  - A unique match alone auto-loads a stale file when a worktree path is reused. Counting stale matches toward uniqueness lets one abandoned file disable auto-load for a worktree.
  - The 7-day bound is a judgment value, not a measurement. An explicit argument still loads a file of any age.
- **A cap of 5 on candidates, target matches first** (`anchors: row47`). The cap bounds what is injected into a freshly cleared session. It is a display choice, not a measurement.
- **`already_fired` in `--check` that is aware of compaction** (`anchors: row34`, `row35`, `row20`, `row59`). Two options were checked:
  - A change to the consumers' reading rule touches three skill files and their pins, outside the Ask.
  - A reset of the marker on compaction breaks the lag join (row 20).
- **Agent-checkable criteria in the nudge only, plus a timing sentence for the engineer** (`anchors: row49`, `row51`, `row52`, `row53`, `row17`, `row25`, `row38`). Three options were checked:
  - A warrant-step branch halts `ready-for-review` (row 17; D1 option B).
  - CLAUDE.md compact instructions also steer auto-compactions.
  - SessionStart(compact) fires after the choice.

  The agent needs no cache lifetime, because the timing sentence holds under any lifetime.
- **A bare `/compact` in the hard block** (`anchors: row54`, `row37`). Two options were checked:
  - A focus line needs a model call, which the block prevents.
  - Dropping the exit removes it where context is deepest.
- **A stated next-fire point instead of a fire-path fix** (`anchors: row19`, `row20`). The fix to the fire path needs changes to the lag join and the backtest, both outside the Ask.
- **A dated follow-up in the cost-lever register** (`anchors: row58`, `row32`). The lighter option is to leave the register alone. It fails because the register is what a later cost audit reads, and it would still record compaction as closed.
- **A test and a doc sentence that pin predecessor markers, instead of eviction** (`anchors: row21`, `row22`, `row23`, `row31`).

## Critical files

There are two sequential `code-writer` dispatches, because Phases 1–3 share `docs/hooks.md` and Phases 1–2 share `README.md`.
- Dispatch one covers Phase 1.
- Before dispatch two, one gate must pass: the session puts row 53's and row 26's literal vendor quotes, with URLs and headings, into the prompt. D3 is settled (row 52) and is not a gate.
- Dispatch two covers Phases 2 and 3.

Each dispatch verifies with `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.

**Phase 1 — same-session resume**
- `claude-skills/skills/resume-context/SKILL.md` (new).
- `claude/.claude/scripts/resume-context-same-session.sh` (new; `git add --chmod=+x`).
- `claude/.claude/hooks/_lib.sh` — adds the three-outcome `_lib_linked_worktree_root`, plus `_lib_continuity_file_resume_cwd` and `_lib_resume_context_verdict STATE ROOT F`.
- `claude/.claude/scripts/_worktree-lib.sh` — header comment only, naming the new script among the files that source it.
- `claude/.claude/tests/helpers.py` — the one kind-aware continuity-file builder that the three test modules import: `continuity_resume_section(kind, path, resume_cwd)`, `write_continuity_body(path, body)` and `write_continuity_file(path, resume_cwd, *, kind="handoff")`. The linked-worktree builders are unchanged.
- `claude/.claude/hooks/announce-resume-command.sh`:
  - calls the helper in place of the inline block at `:85-96` on `main`;
  - runs the verdict on the written file before advertising `/resume-context <file name>`;
  - gets the new messages.
  - Its header changes in these places:
    - the fail-posture line;
    - a line saying it reads the written file's own resume command;
    - the list of the three reasons for a shell-only announce (with "this directory" for a session outside a linked worktree);
    - the rule that a usable file target becomes the shell command's `--cwd` in both mismatch arms;
    - the allowlist paragraph (the file's target is checked again before interpolation);
    - the note that a removed or git-refused worktree reads as "not in a linked worktree";
    - a "Known gaps" line saying that a worktree path with a space drops both `--cwd` and the slash form.
  - Its "Known gaps" also change in two ways:
    - they gain the raw-token comparison (a non-canonical spelling of this worktree, such as one through a symlink, gets the shell-only announce);
    - the same-character-set directive residual now covers the file's `--cwd` directory as well as the filename.
- `claude/.claude/settings.json` — adds `"resume-context": "name-only"` to `skillOverrides` (row 2a). This edit triggers the engineer's approval prompt (row 48).
- `claude/.claude/scripts/resume-context.sh`:
  - header comment `:13-17` naming the new caller of `--consume-only`;
  - the `RESUME_CONTEXT_EXACT_SOURCE` opt-out of the legacy-location fallback;
  - a `chmod 600 -- "$SRC"` (`:329`) after the symlink refusal and launcher check but before `mktemp` and `mv`, so no moment exists at which another uid could open the destination;
  - the shell `resume-context` path shares this edit: when that `chmod` fails it now exits 1 before consuming, with the file still pending. Before, its only exit 1 of this kind came after the move, from the destination `chmod` (`:352`), which stays.
- `claude/.claude/scripts/select-tests.py` — one `CROSS_DOMAIN_EXCEPTIONS` row sending `resume-context/SKILL.md` to `SCRIPTS_TESTS_DIR`, because the script's test reads that file by path (for the key names and the argument character set).
- `claude/.claude/scripts/tests/test_select_tests.py` — the table-completeness tests need two entries for that row:
  - `RESUME_CONTEXT_SKILL_MD` in `_EXACT_MATCH_LITERAL_PATH_CONSTANTS`;
  - the pair (`test_resume_context_same_session.py`, `resume-context/SKILL.md`) in `_KNOWN_CROSS_DOMAIN_READS`.
- `claude/.claude/scripts/tests/test_resume_context.py` — two consumer-layer tests of the source `chmod`, both through a PATH-shimmed command:
  - `test_source_is_owner_only_when_the_move_runs`:
    - a shimmed `mv` lists every non-option argument, so the source entry is captured wherever `--` sits;
    - the test asserts that source entry reads `-rw-------` at the instant `mv` runs, from a 0664 source.
  - `test_source_chmod_failure_exits_before_the_move_and_leaves_the_source_pending`: a shimmed `chmod` that fails when the source is an argument gives:
    - exit 1 and empty stdout;
    - a "failed to set permissions" message;
    - the source untouched (content and mode);
    - no `resume-context.*` file under the temp-dir root.
- `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh` — header comment only (`:13-15` and `:86-91`):
  - A direct `Read` is the backstop.
  - `/resume-context` consumes through `resume-context.sh` and reads a temp path the globs don't match.
- `claude-skills/skills/tests/test_skills.py`:
  - The output-key list has one home, `_EMITTED_KEYS` in `test_resume_context_same_session.py`, which also checks that the body names each key backticked.
  - Right-bounded pins of sixteen clauses:
    - step 0's run-only-when-asked;
    - step 0's no-argument re-run guard;
    - step 1's refuse-and-tell condition, including the literal bracket set;
    - step 1's never-`Read`;
    - step 2's `SESSION_TARGET:` comparison with the anchored worktree, including the `none` rule, which stops before any `Read`;
    - step 2's `Read` of the `LOAD:` path only after the comparison;
    - step 2's `FILE_TARGET: none` check-then-stop;
    - step 2's recovery text;
    - step 2's no-`EnterWorktree`/no-§7;
    - step 3's `FILE_TARGET: none` relay clause, which tells the engineer to run the command from the directory the file was written in, and which step 4 defers to for a `target none` candidate;
    - step 3's `SESSION_TARGET: unknown` relay clause;
    - step 3's `FILE_TARGET: unknown` relay clause, which names the engineer's own command as the one to run in place of the printed one;
    - step 3's relay-and-stop;
    - step 4's slash form only for a candidate whose `target` equals `SESSION_TARGET:`, or is `none` when `SESSION_TARGET: none`, never when either is `unknown`;
    - step 4's caveats: the `SESSION_TARGET: unknown` caveat once for the list, and the `target none` / `target unknown` caveat beside each shell command;
    - step 4's `SKIPPED_UNPRINTABLE:` relay, which gives the meaning and the shell `resume-context` route.
  - No change to `COMMAND_SKILLS`.
- `claude/.claude/hooks/tests/test_lib.py` — new classes beside its existing tests of the resume-context helpers.
  - Using this file instead of a new `test_lib_*.py` keeps the count of "six lib-level test files" in `.claude/rules/bash-unit-test-seams.md` true.
  - Each case runs under `set -euo pipefail` and records both stdout and exit status through `if out=$(…); then …; else rc=$?; fi`.
  - `_lib_linked_worktree_root`, asserting status and output:
    - an empty DIR gives exit 1, with a stub that records git calls and asserts none ran;
    - a directory outside any git repo gives exit 0 and empty output (row 56);
    - a main checkout, and a main checkout using `separate-git-dir`, give exit 0 and empty output;
    - a linked worktree and a subdirectory of one give exit 0 and the root;
    - a shim producing the pre-2.31 output shape and a linked root that fails the allowlist each give exit 1;
    - for each of the four `rev-parse` argv shapes (`--git-dir`, `--absolute-git-dir`, `--path-format=absolute --git-common-dir`, `--show-toplevel`):
      - a non-zero exit and empty output each give exit 1 with empty stdout, so the `--absolute-git-dir` leg cannot regress to "not linked";
      - a timeout does too (`git_timeout_shim`, `hooks/tests/conftest.py:261-324`, takes an arbitrary status and a `$N`-pinned predicate).
  - `_lib_continuity_file_resume_cwd`: fixtures copy the producers' real shapes.
    - §7 in backticks, with and without `--cwd`;
    - a brief's §7.5 under its "§7 Out of scope" heading;
    - `--cwd` text on a line naming another file, which reads `unknown`;
    - two own-basename commands with different `--cwd` values, which read `unknown`;
    - two agreeing own-basename commands, which read the path;
    - `--cwd /x other.md file.md` beside a valid own line, which reads `unknown` (an own line whose token is not followed by FILE's basename);
    - `--cwd /x other.md` beside a valid own line, which names no own file and is ignored;
    - a later own line without `--cwd` is ignored;
    - an earlier one does not hide a later one with it;
    - a trailing slash;
    - a `--cwd` token that would glob to one real allowlisted directory (`workdi?`) reads `unknown`;
    - a control: the spelled-out directory reads that path;
    - `--cwd` values of `.`, `../W`, `~`, `$(` and `;`, each reading `unknown`;
    - a `--cwd` value split by a space, `**resume-context --cwd … {file}**` and `resume-context.sh --cwd … {file}`, each reading `unknown`;
    - `--cwd` prose such as `yarn --cwd pkg`:
      - beside a bare §7, it reads `unknown`;
      - beside a `--cwd` §7, it reads that path;
    - a file longer than the scanned tail:
      - a bare own command in the tail with no `--cwd` text anywhere reads empty;
      - a `--cwd` command before the tail beside a bare own line in it reads `unknown`;
    - no match with no `--cwd` text, which returns 0 with empty output;
    - the whole-file `--cwd` search exiting with status 2 or more reads `unknown` and never the absent answer;
    - the search being unable to run reads `unknown` and never the absent answer;
    - a failing tail read does the same, whether or not the tail has already printed an own command;
    - a `not-linked` verdict on each of these is `shell`, with a no-override control reading empty and `load`. The ids are `search-exits-2`, `search-cannot-run`, `tail-fails`, `tail-fails-after-printing-an-own-command` and `no-override-names-no-worktree`, in `TestLibContinuityFileResumeCwd`, `test_only_a_whole_file_search_finding_no_cwd_text_reads_as_naming_no_worktree`.
  - `_lib_resume_context_verdict`: sixteen cases.
    - `linked` is crossed with `F` empty, equal, different or `unknown`, and with an empty root and `F` empty or non-empty.
    - `not-linked` is crossed with `F` empty, non-empty or `unknown`.
    - The `unknown` state is crossed with the same three `F` values, and must give `shell` for `F` empty.
    - One unrecognized state gives `shell`.
    - Three prefix shapes on a `linked` root each give `shell`:
      - an `F` nested inside the root;
      - an `F` that extends the root's last name component;
      - an `F` that is a prefix of the root's name.
  - The file-building fixtures use `write_continuity_body` from `claude/.claude/tests/helpers.py`.
  - Build linked worktrees from `git_repo` (`hooks/tests/conftest.py:483-491`) with `git worktree add`.
- `claude/.claude/scripts/tests/test_resume_context_same_session.py` (new).
  - **Shared runner.**
    - One runner builds the environment from scratch: `HOME`, `CLAUDE_CONFIG_DIR`, `RESUME_CONTEXT_TMPDIR` and `TMPDIR` under `tmp_path`, with only `PATH` carried over, so `CDPATH` is absent by construction.
    - The tmp root is created with `mkdir(mode=0o700)`, because under umask 002 an inherited mode would be 0775:
      - `_lib_resume_context_index_dir` would refuse that root and skip the index row;
      - the index-lookup test (`test_the_loaded_destination_is_owner_only_and_the_consume_is_in_the_production_index`) would find nothing.
    - The runner rejects any stdout line whose key is outside a module-level `_EMITTED_KEYS` tuple (the ten keys of the script's header contract).
    - One test reads `claude-skills/skills/resume-context/SKILL.md` and asserts that it names each key backticked.
  - **Decoy test.** One test points `CLAUDE_CONFIG_DIR` elsewhere and asserts that a decoy under a stand-in `$HOME/.claude/handoffs` stays untouched.
  - **Fixtures.** Worktrees come from `scripts/tests/conftest.py::_make_worktree` (`:1603`) and `helpers.init_git_repo_with_commit` (`claude/.claude/tests/helpers.py:1240`). Continuity files come from `helpers.continuity_resume_section` and `helpers.write_continuity_body`. Mtimes are set with `os.utime`, using values well inside each bucket (about 2 hours, 3 days, 10 days).
  - **One subprocess case per branch:**
    - No argument:
      - LOAD with decoys: another worktree's file, a file with no `--cwd`, and another slug all stay untouched; the file is moved and an index row written;
      - several fresh matches give candidates;
      - a sole match aged half a day under 7 days loads and one aged half a day over is listed;
      - `AGE` reads `<1d` at 0.5 days, `1-7d` at 1.5 and 6.5 days and `>7d` at 7.5 days;
      - a parse-count test with 12 pending files carrying `--cwd` counts 12 `tail` calls in a linked session and 5 elsewhere;
      - a stale match alone is listed, not loaded;
      - a `find` that always fails reads a target match as stale, so it is listed as `>7d` and not loaded;
      - a `QUOTING_STYLE` in the environment still lists every pending file;
      - a fresh match plus a stale match for the same worktree loads the fresh one;
      - no match prints `SESSION_TARGET:`;
      - past 5 files, with distinct mtimes:
        - target matches are listed first, then the newest others;
        - `MORE:` carries the right count;
      - a main checkout with one file that has no `--cwd` gives `CANDIDATE:` and no load;
      - nothing pending, with both directories missing and with only one present;
      - all pending names unprintable gives `nothing-pending` beside `SKIPPED_UNPRINTABLE:`, with a sentence that does not claim no pending file exists;
      - three tests with an `ls` shim that deletes a pending file mid-listing:
        - the rest keep newest-first order;
        - the only pending file vanishing stops as `nothing-pending`;
        - files vanishing below the cap leave no `MORE:` line.
      - The `ls` fallback that lists in enumeration order is not tested for order.
      - The test of a `QUOTING_STYLE` in the environment asserts only the set of names.
    - Explicit argument:
      - by slug, by file name, and by absolute path;
      - a file older than 10 days, named by slug, loads with `AGE: >7d`;
      - a slug that matches both a handoff and a brief gives two `CANDIDATE:` lines keyed by file name, with nothing moved;
      - arguments containing `*` or `/..`, starting with `-`, or two positional arguments (`slug extra`), each give a `STOP_REASON` with nothing moved;
      - an absolute path to a `-handoff.md` file outside `<config-dir>/handoffs` gives `not-pending`;
      - a slug that is a prefix of another slug does not match.
    - Verdicts and targets:
      - a file targeting another worktree gives `SHELL_COMMAND` and stays pending;
      - in a main checkout and in a linked worktree:
        - the decorated, long-file, unquoted-space and bare-own-line shapes each give `FILE_TARGET: unknown`, a `SHELL_COMMAND` without `--cwd`, and the file still pending;
        - the earlier-without, later-with order gives the shell command with that `--cwd`;
      - a main-checkout file with no `--cwd` loads from a main checkout and prints `FILE_TARGET: none` on the LOAD path;
      - a linked-worktree session naming a file with no `--cwd` gives `SHELL_COMMAND`, `SESSION_TARGET: <root>` and `FILE_TARGET: none`, and the file stays pending;
      - unknown session state: a linked worktree under a path containing a space, and a git shim exiting 1, each with a file that has no `--cwd`, give no `LOAD:`, `SESSION_TARGET: unknown`, and the file still pending;
      - a session in a directory outside any git repo and one in a subdirectory of a worktree each print a defined token;
      - a `--cwd` reached through a symlink still loads.
    - Config dir:
      - a non-default `CLAUDE_CONFIG_DIR` works;
      - a relative `CLAUDE_CONFIG_DIR` gives `config-dir-unresolved`;
      - a config dir containing a space gives `config-dir-unprintable`.
    - Pending set:
      - a name containing a space is counted in `SKIPPED_UNPRINTABLE`;
      - a name containing a newline followed by `LOAD: /tmp/forged` is dropped, counted once, and produces no forged line;
      - a symlink and a dotfile are both excluded.
    - Consume, through `RESUME_CONTEXT_CONSUMER` stubs:
      - a tmpdir containing a space gives `tmpdir-unprintable`, and the file stays pending;
      - exit 1 with the file untouched gives `consume-failed`, and the file stays pending;
      - a stub that moves the file and exits 1 gives `consumed-elsewhere-or-partial` with no `LOAD`;
      - exit 0 with an unusable destination gives no `LOAD:` and a `STOP_REASON` that matches the file's state:
        - each of nine shapes changes one property of an otherwise valid destination and runs with the file moved and untouched: empty stdout, two lines, a path outside the tmpdir root, a nonexistent path, a `..` segment, a nested subdirectory, a symlink, a group- and world-readable file, and a space in the file name;
        - `two-lines` also fails the directory rule, and `symlink` also fails the mode rule, because `ls -ld` reports a link as `l...`;
        - a valid stub destination loads as the positive control;
        - the `[ -O ]` ownership leg has no deny case;
      - a stop before the move (a tmpdir containing a space, or a consumer that exits 1) names the file and the shell command to run for it;
      - with `RESUME_CONTEXT_EXACT_SOURCE` set, a file that vanishes before the consume never loads the `$HOME/.claude` copy, and a control run that drops the variable does load it;
      - a load leaves the destination owner-only and the consume in the production index;
      - a second run after a load gives `not-pending` while another file is pending, and `nothing-pending` when none is.
    - Round trip: for a fixture file name containing dots and digits, run `announce-resume-command.sh` with a synthetic PostToolUse `Write` payload. Take the `/resume-context <file name>` it announces and assert that the script loads exactly that file. Also assert that every `CANDIDATE:` file name from a listing resolves to exactly that candidate.
    - Hook-versus-script parity: for a canonically spelled target `F`, the shell command the hook advertises equals the script's `SHELL_COMMAND:` for the same file. Two fixtures cover it: a linked session whose file names another worktree, and a main-checkout session whose file names a worktree. The symlink and dotted spellings are the tested known differences.
    - Every run: `_Sandbox.run` asserts on every subprocess call that the exit is 0 and that the run prints `LOAD:` or at least one of `STOP_REASON:`, `CANDIDATE:` and `SHELL_COMMAND:`, and not both or neither. The non-load check is any-of.
    - **Skill contract.** `SKILL.md` is read by path for three checks. The key names it backticks equal the keys in the script's header contract. The bracket set in its step 1 equals the script's allowlist, compared over printable ASCII with `_lib_passes_path_char_allowlist`. The script applies that allowlist to its argument, with `a:b` refused and `a+b` passed.
  - **Rows 7 and 27.** Feed a `Read` of a real `LOAD:` path to every hook `settings.json` registers on PreToolUse `Read` (read from `settings.json` at test time) and to `consume-durable-continuity-file-on-read.sh`. Assert no deny and no move.
    - `settings.json` registers the hooks as `~/.claude/hooks/<name>.sh` while the runner pins `HOME` under `tmp_path`, so executing the literal string would run nothing. Resolve each registered command to `HOOKS_DIR/<basename>` as `test_announce_resume_command.py:28-40` does, and assert the file exists.
    - Add a positive control per hook (the same hook denies a path it is built to deny). For the consume hook, the control is that a `Read` of a pending `handoffs/*-handoff.md` path does move it.
- `claude/.claude/hooks/tests/test_announce_resume_command.py`:
  - Its `_write_continuity_file` fixture wraps `helpers.write_continuity_file`, passing `kind="brief"` for a brief.
  - Replace the bare `"resume-context" in` checks with the full computed command (row 30).
  - `/resume-context <file name>` appears in both channels in three cases:
    - a linked worktree whose file carries the matching `--cwd`;
    - a main checkout with no `--cwd`;
    - a file name containing dots and digits.
  - The linked-worktree case also pins "(in this worktree)" and the session-measured provenance sentence in `additionalContext`, as the main-checkout case pins "(in this directory)".
  - The slash form is absent, with the shell command and the reason clause present, in three cases:
    - a linked worktree whose file has no `--cwd`, which keeps `--cwd <root>`;
    - a linked worktree whose file names another usable directory `F`, which gets `--cwd F` and the reason "targets `F`, not this worktree";
    - a main checkout whose file carries a usable `--cwd F`, which gets `--cwd F` with no "from `F`" clause.
  - For the two `--cwd F` cases, `additionalContext` carries the reason and says the `--cwd` directory comes from the written file's own resume command and was not measured by the hook. It does not say the commands were computed from the session's working directory.
  - A metacharacter-bearing `--cwd` in the file (`;`, `$(`, a leading `-`) is never interpolated in either session kind, and the announce falls back to the unresolved-target reason. A stand-in `_lib_continuity_file_resume_cwd` that wrongly returns such a path tests the hook's own allowlist re-check, with an allow twin that returns a clean path and sees it interpolated.
  - `additionalContext` carries the instruction to relay the commands and run none.
  - The load-arm copy for a session outside a linked worktree contains no `--cwd`. It reads "this directory" and "(run it from this directory)", never "from any shell". The not-confirmed copy without a usable target contains no `--cwd` either.
  - A file whose `--cwd` reaches this worktree through a symlink gets the shell-only announce. This pins the known difference from the script, which canonicalizes and loads.
  - A helper exit 1 falls back to the bare command, with no slash form, and its message carries the "could not be determined" reason, not the "does not name this worktree" reason. A missing payload cwd gives the same reason.
  - An unreadable `--cwd` (`~`, relative, `.`) gives the shell-only announce:
    - in a linked session, with the "does not name this worktree" reason;
    - in a session outside a linked worktree, with the "could not be resolved to one directory" reason, with no `--cwd` text and no word "different".
  - `--cwd` prose beside a bare §7 in a main checkout gives that same reason, which does not claim that the command names a directory.
  - A session outside a linked worktree reads "this directory", not "this worktree", in the not-confirmed sentence.
  - A directory outside any repository (git exit 128) advertises the slash form for a file that names no `--cwd`. This pins the accepted residual of row 56.
  - The tests driven by the stub (`:731`, `:755`) and the timing test keep passing. Update only their message-text assertions.
- `docs/skills.md`:
  - A new `/resume-context` bullet, which is the canonical home for the two resume paths. It:
    - states that a no-argument run auto-loads only in a linked worktree and lists candidates elsewhere, such as a main checkout, where the file name the announce gives is the argument;
    - also states that the skill path loads the file into the conversation, where a later auto-compaction can summarize it, rather than into the system prompt;
    - notes that after `EnterWorktree` plus `/clear`, project hooks stay bound to the launch directory (G4).
  - A `` | `/resume-context` | `` table row.
  - Counts updated per row 14, including `:41`'s list gaining the eleventh workflow utility and `:54`'s "ten of the eleven".
  - The `/handoff` and `/brief` bullets point to the new bullet.
- `docs/scripts.md`:
  - A new entry for the script. The script header is the home for the token keys and the exit rule, so the entry covers the `STOP_REASON:` codes, the three session states, candidate ordering and cap, the parse bound, the age buckets, the consume semantics, `RESUME_CONTEXT_CONSUMER` and the residuals.
  - The `resume-context.sh` entry's list of callers of `--consume-only`.
- `docs/hooks.md` — the `:182` (announce, which defers to the hook header for the advertise condition, the reasons for a shell-only announce and how its shell command relates to the script's `SHELL_COMMAND:`), `:185` (the worktree-path-with-a-space known-gaps sub-bullet), `:189` (the raw-token comparison sub-bullet), `:190` (the directive residual sub-bullet) and `:212` (consume) entries.
- `README.md` — `:74` (consume paths) and `:493` (resume sentence).

**Phase 2 — compact alternative**
- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`:
  - `read_fired_marker` beside `resolve_rearm_spacing`, above `:487` (row 59), with its one-line asymmetry comment;
  - the fire path's marker read (`:568-578` on `main`, now the call at `:615`) calling it;
  - `already_fired` in `--check` (`:424-425` on `main`, now `:457-462`);
  - the strings at `:670` and `:683` on `main` (now `:715` and `:730`), the next-fire value, the new `--argjson`, and the extra `%s`;
  - one header line under `--check`.
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`:
  - **Shared constants.** One module constant each for D3's criteria phrase, timing sentence and tie-break (quoted as a literal, in its soft form and the hard block's inverse form), used by both message tests. Each constant is asserted free of `%`, because the hard block's `printf` format and the soft nudge's `jq` literal must not receive one.
  - **Accepted properties pinned in both messages** (rows 51, 52; G1): the user-only statement for `/compact`; no `/clear` and no `/resume-context` anywhere in either string, not only the literal "then /clear"; the soft nudge carries the `/compact <focus>` placeholder form and the hard block the bare form.
  - **Next-fire label** (row 19): the label reads "next hard block at" in the hard block and in a soft nudge whose next point reaches `HANDOFF_NUDGE_BLOCK_AT` (boundary pinned at equality), and "next nudge at" otherwise.
  - **Doc parity.** A check in the style of `test_doc_counts.py:309` compares the D3 strings restated in `docs/handoff-nudge.md` with the live hook output. This is the second pin the D3 text refers to.
  - **`--check` tests:**
    - With marker N and an estimate below N and below threshold, both booleans are false.
    - With marker N and an estimate in `[threshold, N)`, `over_threshold` is true and `already_fired` false.
    - With an estimate equal to N, `already_fired` is true.
    - Malformed markers read as true, parametrized over empty, `0`, `abc`, `1e9`, a leading-zero value, a 10-digit value and a newline-less value (`read` exits 1 at EOF, so it reads as malformed). Valid-marker fixtures write the trailing newline (`f"{N}\n"`, as `test_nudge_handoff_near_context_cap.py:3523` does).
    - One test pins the asymmetry: a malformed marker makes the fire path fire and makes `--check` report `already_fired: true`.
    - A transcript seeded with a pre-compaction usage record, an `isCompactSummary` record, then a smaller usage record gives the smaller estimate.
    - The docstring of `test_already_fired_stays_true_even_when_due_for_rearm` drops "marker existence only".
  - **Soft nudge:** it names `/compact` and `/handoff`, carries both shared constants and the computed next fire, and contains no "then /clear" and no `/resume-context`.
  - **Hard block:** it names `/compact` and `/handoff`, carries both shared constants and the next fire, and contains no "ignored re-arms".
  - **Next fire:** asserted under a non-default `HANDOFF_NUDGE_REARM_SPACING`, and under a malformed override that falls back to 80000.
  - **Characterization tests**, each appending with `_append_to_transcript` (`:220`) instead of rewriting:
    1. Fire at E1, append a smaller estimate, and assert that an estimate at or above the threshold but below E1 + spacing gives no output, while one at E1 + spacing fires with `ignored=1` on its `nudged` log line.
    2. Hard-block variant: block at B, append a dip, then assert silence just below B + spacing and a block again at B + spacing on `PostToolBatch`, with the incremented count reaching the hard block's stderr.
- `claude/.claude/scripts/tests/test_transcript_handoff_nudge.py` (row 18). A contract test checks that the real hook's stderr from a hard block contains the imported `_HANDOFF_SIGNAL_HARD_BLOCK_TEXT`.
- `docs/handoff-nudge.md` — `:18`, `:22`, `:34-38`, `:131`, the new section on compaction with its dated quotes, and the two Known-limitations bullets.
- `docs/hooks.md` — `:175`, deferring to `docs/handoff-nudge.md`.
- `README.md` — the "When to use which" table at `:496-502`. Its `/compact` row defers to `docs/handoff-nudge.md` for the criteria. Its `/handoff` row says that the announce message names the resume route.
- `docs/cost-levers-considered.md` — one dated follow-up paragraph after the `token-cost-reduction.md` section's table (`:331`), per row 58. The row itself is not edited.

**Phase 3 — predecessor markers (option A)**
- `claude/.claude/hooks/tests/test_capture_session_id.py` — the test that no marker carries over to the new id.
  - The second invocation uses the same `CLAUDE_PID` with a new `session_id` and the real payload shape, `{"hook_event_name": "SessionStart", "source": "clear"}`.
  - Old markers sit in `.handoff-active.d/<old-id>` and `.ready-for-review-active.d/<old-id>` and hold a sentinel PID different from the live one.
  - The test asserts that the old markers are byte-identical, that no `.*-active.d/<new-id>` exists, and that line 1 of `sessions/<PID>` is the new id.
- `docs/hooks.md` — one sentence in `:173`.

**Phase dependency and rollback.** Phase 2's README rows name `/resume-context`, so reverting Phase 1 alone leaves docs pointing at a missing skill. The PR is therefore the revert unit. A revert needs no state cleanup: pending files, the consumed-file index and the nudge markers keep their existing formats.

**If D1 = B, add:** `claude-skills/skills/handoff/SKILL.md` (warrant section, zero net lines), and in `test_skills.py` a re-pin of `fallback_to_judgment_and_closing` plus a pinned fork clause. Also add either `ready-for-review/SKILL.md`, which is outside the Ask, or a warrant step that recognizes its caller.
**If D2 = B or C, add:** `claude/.claude/settings.json` `permissions.allow` via `/review-permissions`, or the frontmatter of `resume-context/SKILL.md`.

**Reuse rather than reimplement:**
- `resume-context.sh --consume-only`
- `_lib_config_dir`
- `_lib_passes_path_char_allowlist`
- `_lib_resume_context_tmpdir_root`
- `_lib_print_recovery_hint`
- `_lib_capped`
- `worktree_canon_path` (`scripts/_worktree-lib.sh:59-65`)
- the precedent for the main-checkout idiom and for exit 1 as "no result", `_lib_main_repo_root` and `_lib_repo_root`
- the `RESUME_CONTEXT_TMPDIR` test seam
- `_make_worktree` and `init_git_repo_with_commit`
- the existing `--check` test harness (`_seeded`, `_run_check`) and `_append_to_transcript`

**Deliberately unchanged:**
- `handoff/SKILL.md` (under D1 option A), `brief/SKILL.md`, `plan-it/SKILL.md` and `ready-for-review/SKILL.md`. Their reading rule for `--check` becomes correct after compaction without an edit.
- `transcript_analysis/handoff_signal_response.py`, which now correctly sees no signal from a `--check` run after compaction.
- `handoff_nudge.py` and `rearm_backtest.py`.
- The historical fixtures in `test_transcript_handoff_signal_response_detection.py` and `_handoff_nudge_helpers.py`. The detector's substring survives in both wordings, and four existing detector tests already run the old wording (row 61).

## Verification

1. Before dispatch two, check the quotes gate (D3 is settled, row 52).
   - The prompt carries the session's literal quotes from the four vendor pages (rows 26, 53), each with URL, heading, "fetched 2026-10-18" and "re-verify by 2027-01-18". After dispatch two, confirm that `docs/handoff-nudge.md` quotes them literally rather than paraphrasing.
2. After each dispatch, run `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
3. Run `.venv/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
4. Confirm `git ls-files -s claude/.claude/scripts/resume-context-same-session.sh` shows `100755`.
5. Run `/skill-review` on `resume-context/SKILL.md`; it is hook-enforced at commit.
6. Before merge, `~/.claude/scripts` and `~/.claude/skills` resolve into the main checkout. So run this worktree's script by absolute path. Set both `CLAUDE_CONFIG_DIR` and `RESUME_CONTEXT_TMPDIR` to scratch directories created with mode 0700, never the real ones (row 63). The tmp root must be mode 0700 itself: under umask 002 a plain `mkdir` gives 0775, `_lib_resume_context_index_dir` then skips the index row, and 6a's lookup finds nothing. The script keys on the Bash `$PWD`, so each step names its cwd.
   - Invocation, with `<scratch>` made by `mkdir -m 700`, `<scratch>/tmp` made by `mkdir -m 700` as well, `<scratch>/config/handoffs` created beneath it, and `<this-worktree>` this branch's checkout: `CLAUDE_CONFIG_DIR=<scratch>/config RESUME_CONTEXT_TMPDIR=<scratch>/tmp <this-worktree>/claude/.claude/scripts/resume-context-same-session.sh smoke-handoff.md`.
   - Fixture: `<scratch>/config/handoffs/smoke-handoff.md` whose last line is `` `resume-context --cwd <path> <scratch>/config/handoffs/smoke-handoff.md` ``, or the same line without `--cwd <path>`. Recreate it before each step.
   - a. Run from linked worktree A, with `<path>` set to A. Expect `LOAD:`, the file gone from `handoffs/`, and a row printed by `CLAUDE_CONFIG_DIR=<scratch>/config RESUME_CONTEXT_TMPDIR=<scratch>/tmp ~/.claude/scripts/find-consumed-continuity-file.sh smoke`.
   - b. Run from linked worktree A, with `<path>` set to another linked worktree B. Expect `SHELL_COMMAND:`, with the file still pending.
   - c. Run from the main checkout, with no `--cwd` in the fixture. Expect `LOAD:` and `FILE_TARGET: none`.
   - d. Run from linked worktree A, with no `--cwd` in the fixture. Expect `SHELL_COMMAND:` and `FILE_TARGET: none`, with the file still pending.
7. After merge, run these live checks. The PR body lists them as post-merge smoke checks. It also states that rows 36, 37 and 39 are unverified at merge. If row 36 proves false, the first `--check` after a compaction still reads stale, which is today's behavior. Record `claude --version`, each session's permission mode, and the results in a PR comment.
   - a. Re-run the probe (`/tmp/handoff-probe/probe.sh setup`, `snap`, `report`) and confirm G2 and G4. If the probe is gone, compare line 1 of `<config-dir>/sessions/<PID>` across a `/clear`.
   - b. In a linked worktree, run `/handoff`, `/clear`, then `/resume-context` with no argument.
     - Expect `LOAD:`, a successful `Read`, and no call to `EnterWorktree`.
     - Run this once in a session started in default permission mode, and once in the session's usual mode. Record any prompt or denial for the script call or the `Read` (rows 27, 28; D2).
     - If default mode prompts, report it to the engineer. D2 option B is the recorded alternative for the no-argument form.
   - c. In a main checkout, run `/handoff`, `/clear`, then `/resume-context`, and expect `CANDIDATE:`. Then run `/resume-context <file name>` and expect `LOAD:`.
   - d. Write a handoff from worktree A, then run `/resume-context <file name>` from worktree B. Expect `SHELL_COMMAND:`, with the file still pending.
   - e. Check compaction:
     1. In a first trial session, compact once and record the post-compaction estimate from `--check` as the floor.
     2. Start a session with `HANDOFF_NUDGE_ABS_CAP` above that floor and below the estimate at which you will compact. Record both numbers.
     3. Confirm that the nudge recommends one exit per the D3 criteria, names the other, and gives the focus line and the timing sentence.
     4. Run the pasted `/compact <focus>` right away.
     5. Ask the session to run `--check`. Record `estimate`, `over_threshold` and `already_fired`, and expect both booleans false (rows 35, 36).
     6. Confirm that the context-budget bullet of `/ready-for-review` Step 1 continues instead of deferring.
     7. Confirm that line 1 of `sessions/<PID>` is unchanged (G3).
     8. Repeat the compaction once mid-way through a skill whose body exceeds the 5,000-token cap (for example `ready-for-review`, row 66). Record which part of the body survives and whether the remaining steps are recoverable by re-invoking the skill.
   - e2. Set `HANDOFF_NUDGE_ABS_CAP`, `HANDOFF_NUDGE_REARM_SPACING` and `HANDOFF_NUDGE_BLOCK_AT` low enough that a hard block fires. Record whether the engineer, the model, or both see its stderr (row 37). Then run a bare `/compact` and confirm that the next `--check` reads under threshold.
   - f. After the `/compact` in 7e, count how many of the focus line's "files to re-read first" the session re-`Read`s. Record the count as an observation for row 25.
   - g. Name-only guard (row 39):
     1. After a `/handoff` write in a linked worktree, the model relays the announce and does not invoke `/resume-context`.
     2. In a new session started in a worktree with a fresh pending handoff for that worktree, the prompt "continue" does not invoke it.

     If either fails, report it to the engineer with `user-invocable-only` (row 57) as the alternative. Do not patch the prose.
   - h. Run `/resume-context` with an argument containing `;`. Expect step 1's refusal and no Bash call.
   - i. Run `/resume-context` in two cases, and record for each (observe, do not assert) whether the Bash result is normal-styled, whether the model relays a command verbatim as a terminal command, and whether it calls the run a failure or retries (row 68):
     1. No argument from a main checkout holding pending files, which gives a candidate list.
     2. A file that targets another worktree (7d), which gives a `SHELL_COMMAND:` redirect.

     The script exits 0 on every path that printed its verdict, as `--check` does, and unlike `plan-push-preflight.py`, which exits 0 when it reached a decision (ask or stop) but still exits 2 on a usage stop and 1 on an internal-error stop. Superseded in review: this step first recorded how the Bash tool renders non-zero exits, with exit 0 on every verdict path as the alternative. That alternative shipped.
   - j. In a session whose Bash `$PWD` was moved with `cd` into another worktree, run `/resume-context` and expect step 2 to stop on the `SESSION_TARGET:` mismatch.

## Out of scope

- **Resetting the fire path's fired and ignored state after compaction.** This would close the silent window the nudge now states. It would also change the lag join's first-crossing join and `rearm-backtest`'s simulation of bands (rows 19, 20), which are outside the Ask.
- **Offering compaction from `plan-it` Step 7 and `ready-for-review` Step 1.** Both still route to `/handoff`, and `ready-for-review:36` argues that a handoff is right for its own case.
- **Counting `/compact` as a response in `handoff-signal-response` or `handoff-record-conversion.sh`.** Audits made after this change will under-count compliant compactions. `docs/handoff-nudge.md` names `isCompactSummary` records as the source for a later measurement (row 62).
- **A measured comparison of the cost of compaction against a handoff plus resume** (row 25).
- **Switching worktrees in-process on a mismatch** (row 3, G4).
- **Moving the other five inline linked-worktree predicates onto the new helper.** Several of them sit in gates with threat-model tiers (row 11).
- **A retention sweep for pending files in `handoffs/` and `briefs/`** (row 47).
- **Making §7's `--cwd` mechanical,** for example by having the announce hook rewrite §7, record a sidecar, or ask the model to edit §7 (row 9).
- **Discovering legacy `$HOME/.claude` files from the new script.** Those files resume through the shell `resume-context`.
- **Changing the consume-on-`Read` behavior.**
- **SKILL.md steps 4 and 5 do not distinguish an incomplete run or the load-path `STOP_REASON` hints.** Step 5 gives a non-zero exit (the script stopped before finishing, possibly after printing part of a candidate list) no wording of its own, and step 4 gives the `STOP_REASON` sentences that carry a `resume-context` command no wording that says to present it as a terminal command. Both are accepted: the steps are a generic relay, and a mid-list abort is below current scale. No SKILL.md edit is made.
- **Editing `.claude/plans/precompact-review-snapshot.md` or `token-cost-reduction.md`.** Merged plans are read-only records. The narrowing is recorded in row 32 and in the cost-lever follow-up.
- **Reviewer suggestions not taken:**
  - `/resume-context` lines inside the §7 and §7.5 templates. Whoever reads the file has already resumed, and the human sees the announce message instead.
  - A usage hint in `resume-context.sh`.
  - A head-anchored line cap on the §7 parse. §7 is the file's last section, so a head cap would miss it. The helper bounds tokenizing with a tail byte window instead (the last 8192 bytes, `_LIB_RESUME_CWD_TAIL_BYTES`), because pending files are not assumed small. The whole-file `--cwd` search stays linear in the file's size.
  - Composing the focus line only on request (row 38).
  - A new test pinning the old hard-block wording against the detector. Four detector tests already do (row 61).
  - A wall-clock test for the no-argument scan. The per-state parse bound is pinned by the parse-count test instead.
- **FYI residuals:**
  - An empty 0600 `resume-context.XXXXXX` file is left behind only when a concurrent consume passes the source `chmod`, then loses the race at its own `mv` (row 42). A loser that arrives earlier fails before `mktemp` and leaves nothing.
  - On macOS without `timeout`/`gtimeout`, each capped git call prints "running git uncapped" to stderr, which the skill ignores.
  - The announce hook echoes the file name, and the file's own `--cwd` directory when it is usable, verbatim into `additionalContext`. A same-character-set English directive (an instruction spelled only from `[A-Za-z0-9._/@+-]`) in either passes the allowlist. This is an accepted residual, recorded in the hook header. The `--cwd` directory is labelled in `additionalContext` as taken from the file, not measured by the hook.
  - A dubious-ownership refusal, a removed working directory and a pruned worktree also exit 128, so each reads as "not linked" (row 56). Only the removed working directory was observed. A file with no `--cwd` could then load into such a session.
  - With no argument, one fresh file whose own text names this worktree loads without a further choice. That text is model-written and unauthenticated, so a file planted in the shared directory can be selected this way, and the loaded file's instructions then govern the session that ran the skill. Any writer running as the same uid can plant it, including a session steered by content it processed. An explicit argument avoids it. `docs/scripts.md` records it, and the script header does not. A decorated `--cwd` line (for example `**resume-context --cwd <other> file**`) beside a plain own command that names this worktree is ignored, so the file still loads.
  - A rewrite of a pending file between the parse and the move loads bytes the verdict never saw. It needs a same-uid writer, who can already plant a matching file.
  - A same-uid process can replace the moved destination between the script's acceptance check and the model's `Read`, and the model then reads the substituted content with instruction authority.
    - A same-uid writer can already plant a matching pending file, so this adds no new population.
    - The mode-0600 and `[ -O ]` check protects that window against other uids only on a sticky or private root.
    - On a temp root another uid can write without the sticky bit, that uid can instead swap the destination between the acceptance check (or `resume-context.sh`'s `mv`) and whatever reads it next. That reader is the model's `Read` for this script, or the engineer's `claude --append-system-prompt-file` reload for the shell `resume-context` and the `Read` hook, where the swapped text enters the system prompt.
    - That swap needs a non-default temp root from `RESUME_CONTEXT_TMPDIR` or `TMPDIR`, because the default `/tmp` is mode 1777.
    - On such a root the consumed-file index row is also skipped, so the recovery hint's `find-consumed-continuity-file.sh <slug>` lookup finds nothing.
    - Accepted as an edge case below current scale, for all three movers (this script, the shell `resume-context`, the `Read` hook).
  - The destination's `[ -O ]` ownership check has no deny test, because a foreign-owned fixture needs a second uid.
  - `chmod 600 -- "$SRC"` follows a symlink, and it runs after the `[ -L ]` check. A same-uid swap in that gap narrows the file the symlink points to. It needs a same-uid writer, who can already `chmod` that file.
  - A linked session's no-argument run parses every pending file (Script, No argument), and nothing sweeps pending files (row 47). One run of the helper over 200 pending files of 20 KB each took 1.4 s (about 7 ms per file) on this machine, so the accepted n is about 200 pending files. The figure covers the helper only, and one run is not a distribution. A retention sweep is the remedy past that and is out of scope.
  - Any `--cwd` text in a main-checkout file, even in prose such as `yarn --cwd pkg`, reads `unknown` and gets the shell route instead of a load (`test_cwd_prose_beside_a_bare_own_command_is_unusable`). That is the fail-closed direction and costs seamlessness only.
  - The skill's comparison of `SESSION_TARGET:` with the anchored worktree is prose, because no mechanical source for the anchor exists. Verification 7j tests one direction (an earlier `cd` into another worktree).
  - A main-checkout file whose §7 carries `--cwd <main root>` gets `SHELL_COMMAND` rather than a load. The verdict compares `F` only with a linked root.
  - A file with no `--cwd`, named explicitly from another project's main checkout, loads. Step 2's §4 check, which now runs before acting, catches the mismatch after the consume, and the recovery hint names the copy.
- **Justified skill-review hits (platform-genericness).** The `Read`, `Bash` and `EnterWorktree` verbs and the §4/§7/§7.5 references in the skill body are deliberate. The skill is coupled to this repo's handoff and brief artifacts and to the consume-on-`Read` hazard.
- **Declined: land the literal vendor quotes in the plan (platform FYI).** This edit has no access to the literal fetched text of rows 26 and 53, and inventing it would break the quote requirement. The quotes gate in Critical files and Verification 1 stays, and the session should add the quotes to the plan, for example in an appendix block, before any handoff or compaction ahead of dispatch two.
- **Noticed, not fixed (for the reviewer):**
  - `docs/handoff-nudge.md:206` says a halted `/handoff` marker is bounded only by PID liveness, but `_lib.sh:2761-2763` also evicts it after 60 idle minutes.
  - `README.md:491` gives the dashboard matcher as `startup|clear|compact`, but `settings.json:136` has `startup|clear|compact|resume`.
