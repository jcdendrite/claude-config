#!/bin/bash
# Auto-allow known safe (read-only / low-risk) bash commands.
# Commands not matched here fall through to the default "ask" prompt.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

allow() {
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
}

# --- Safelist: read-only and low-risk commands ---

# Extract the first token (the base command, ignoring leading env vars / cd chains)
BASE_CMD=$(echo "$COMMAND" | sed 's/^[A-Z_]*=[^ ]* *//' | awk '{print $1}' | sed 's|.*/||')

# Read-only git subcommands
if echo "$COMMAND" | grep -qE '^git (status|log|diff|branch|show|remote|stash list|tag|describe|rev-parse|shortlog|ls-files|ls-tree)'; then
  allow
fi

# Filesystem reads
case "$BASE_CMD" in
  ls|pwd|cat|head|tail|wc|file|which|type|stat|readlink|basename|dirname|realpath|tree)
    allow
    ;;
esac

# Process / environment inspection
case "$BASE_CMD" in
  echo|env|printenv|whoami|hostname|date|uname|id|locale|uptime)
    allow
    ;;
esac

# Version checks (anything ending in --version or -v as sole arg)
if echo "$COMMAND" | grep -qE '^[a-z_-]+ (--version|-[vV])$'; then
  allow
fi

# Dev tool read-only queries
if echo "$COMMAND" | grep -qE '^(npm list|npm ls|npm outdated|npm view|npm info|npm config list)'; then
  allow
fi
if echo "$COMMAND" | grep -qE '^(pip list|pip show|pip freeze|pip check)'; then
  allow
fi
if echo "$COMMAND" | grep -qE '^(cargo metadata|cargo tree|cargo check)'; then
  allow
fi
if echo "$COMMAND" | grep -qE '^(go list|go env|go doc|go vet)'; then
  allow
fi

# Unit/component test runners — only auto-allow direct invocations of tools
# that overwhelmingly run against mocked dependencies. npm test/npm run test
# are excluded because they're indirection into arbitrary package.json scripts.
# Runners that commonly hit real dependencies (deno test, pytest, go test,
# cargo test, mvn test, gradle test) fall through to default "ask".
if echo "$COMMAND" | grep -qE '^(npx jest|npx vitest|npx mocha)'; then
  allow
fi

# Build commands
if echo "$COMMAND" | grep -qE '^(npm run build|npm run lint|npx tsc|make |make$|cargo build|cargo clippy|go build)'; then
  allow
fi

# --- Default: fall through to normal permission prompt ---
exit 0
