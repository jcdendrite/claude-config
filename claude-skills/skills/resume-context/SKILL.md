---
name: resume-context
description: Resume a pending /handoff or /brief file inside this session, after /clear or in a new session started in its worktree; distinct from the shell resume-context command, which starts a new process.
argument-hint: "[<slug> | <file name> | <absolute path>]"
---

# Resume context

`/resume-context` loads a pending `/handoff` or `/brief` file into this session's conversation. The shell command `resume-context` is a different thing: it starts a new `claude` process, and it is the route for a file that belongs to another worktree.

0. Gate the run.

   - Run only when the engineer asked to resume in this session. Never run the skill on your own, including right after writing a handoff or brief.

   - If this conversation already loaded a file through this skill, run the script again only with an explicit argument. With no argument, tell the engineer and stop.

1. Run the script.

   - If `$ARGUMENTS` starts with `-` or contains any character outside `[A-Za-z0-9._/@+-]`, do not run the script; tell the engineer the accepted forms: a slug, a file name, or an absolute path.

   - Otherwise run `~/.claude/scripts/resume-context-same-session.sh $ARGUMENTS` as one Bash call.

   - Never `Read` a path under `<config-dir>/handoffs/` or `<config-dir>/briefs/`, because that `Read` consumes the file before any check runs.

2. On `LOAD:`, compare before you `Read`.

   - First compare `SESSION_TARGET:` with the worktree this session is anchored in: the working directory the environment context shows, or the `EnterWorktree` target. The script keys on the Bash `$PWD`, so an earlier `cd` into another worktree yields a verdict for that worktree. When `SESSION_TARGET:` is `none`, they match only if the anchored directory is not inside a linked worktree of any repository. If they differ, say so and stop before any `Read`, without acting on the file. Give the recovery text below.

   - Otherwise `Read` the `LOAD:` path in full. Tell the engineer which `SOURCE:` file was consumed and its `AGE:`.

   - Then check the target. When the script printed `FILE_TARGET: none` and the file's §4 header (§3 in a brief) names a directory other than this session's, say so and stop without acting on the file. Give the recovery text below.

   - Recovery text: the file is already consumed, so tell the engineer to `cd` to the directory named here and run `claude --append-system-prompt-file <LOAD path>`. Also tell them that `~/.claude/scripts/find-consumed-continuity-file.sh <slug>` finds the copy later. The directory is the `FILE_TARGET:` path when it is a path (on `LOAD:` it equals `SESSION_TARGET:`), otherwise the directory the file's §4 header (§3 in a brief) names if you read the file and it is an absolute path made only of `[A-Za-z0-9._/@+-]`. When neither gives such a directory, say the directory is unknown and must come from the session that wrote the file; name none.

   - Otherwise, continue from the file. Its artifact preamble governs.

   - Track its pending and in-progress task-list items from the file, not from memory. Use the session's task-list tool if one exists, otherwise track them inline.

   - Do not call `EnterWorktree`, and do not run the file's §7/§7.5 command. The script already matched the file to this session.

3. On `SHELL_COMMAND:`, relay the command and stop.

   - When `FILE_TARGET: none` appears, add that the file names no worktree, so the command must be run from the directory the file was written in.

   - When `SESSION_TARGET: unknown` appears, add that this session's worktree could not be determined (a path with a space or a failed git call), not that the file names another worktree.

   - When `FILE_TARGET: unknown` appears, add that the script could not resolve one directory from the file, so the printed command has none. The engineer should check the resume command in the file itself, outside this session because a `Read` here consumes it, and run that command in place of the printed one.

   - Relay the line verbatim as a command for a new terminal, not as a slash command. The file is still pending. Stop.

4. On `CANDIDATE:`, `MORE:`, `SKIPPED_UNPRINTABLE:` or `STOP_REASON:` lines, relay them and stop.

   - Suggest `/resume-context <file name>` only for a candidate whose `target` is the same path as `SESSION_TARGET:`, or is `none` when `SESSION_TARGET: none`, and never when either is `unknown`. Any other candidate needs the shell command on its `CANDIDATE:` line, which you relay as a command for a new terminal.

   - Add step 3's caveats: its `SESSION_TARGET: unknown` caveat once for the list when that line appears, and, for each candidate with `target none` or `target unknown`, the caveat for that `FILE_TARGET:` value beside its shell command.

   - Relay `SKIPPED_UNPRINTABLE:` whenever it appears, with or without candidates. It means n pending files under `<config-dir>/handoffs/` or `<config-dir>/briefs/` have names with a character outside `[A-Za-z0-9._/@+-]`, so the script does not print them. The engineer can resume one with the shell `resume-context` and its full path; do not list or name them yourself.

5. On any other output, or when no recognized line appears, report the output and stop.
