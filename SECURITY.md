# Security

## Scope

The hook system in `claude-config` protects against three failure modes:

1. **Committing project identifiers to a public repo** — the tracker-ID regex and private-projects blocklist run before every `git commit` and PR create/edit.
2. **Claiming work is done before tests pass or code review ran** — `require-code-review` and `require-ready-for-review` deny the commit or push until the review marker exists for the current staged state.
3. **Two concurrent Claude sessions racing on the same working tree** — `require-worktree-for-git-writes` denies write-path git operations unless the session is inside a linked worktree.

## Out of scope

The hook system does not protect against a skill or hook script itself being malicious — if an attacker can write to `claude/.claude/`, they can ship a hook that exfiltrates secrets before denying the command. It does not protect against an attacker with write access to `~/.claude/`: the marker directory, session files, and hook scripts all live there, and tampering with any of them can bypass or forge gate checks. It does not protect against a model that quotes sensitive tool output back to the user in chat — the hooks gate tool calls, not what the model says; if Claude reads a secret from an allowed path and repeats it in conversation, no hook fires.

## Reporting a vulnerability

To report a security vulnerability privately: [open a GitHub security advisory](https://github.com/jcdendrite/claude-config/security/advisories/new).

For non-security bugs, feature requests, or questions: [GitHub Issues](https://github.com/jcdendrite/claude-config/issues).
