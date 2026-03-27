#!/bin/bash
# Gate: require /code-review before git commit
INPUT=$(cat)

if ! echo "$INPUT" | grep -q '"git commit'; then
  exit 0  # not a commit, allow
fi

# It's a git commit — ask whether code review was done
echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Code review gate: Has /code-review been run on the changes being committed? If not, deny this commit and run /code-review first."}}'
