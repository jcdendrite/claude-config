#!/bin/bash
# Auto-allow known safe (read-only / low-risk) bash commands.
# Commands not matched here fall through to the default "ask" prompt.

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

allow() {
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
  exit 0
}

# --- Reject multi-line commands ---
# Newlines in the command string bypass grep-based checks (grep is line-based).
if [[ "$COMMAND" == *$'\n'* ]]; then
  exit 0
fi

# --- Reject compound/chained commands and shell expansion ---
# Metacharacters could chain dangerous commands after a safe-looking prefix,
# or expand variables containing secrets. Fall through to "ask".
if printf '%s\n' "$COMMAND" | grep -qE '[;|&`]|>[[:space:]]*|<[[:space:]]*|\$'; then
  exit 0
fi

# --- Reject commands with environment variable prefixes ---
# KEY=VALUE prefixes can override LD_PRELOAD, PATH, etc. to hijack safe commands.
if printf '%s\n' "$COMMAND" | grep -qE '^[A-Za-z_][A-Za-z_0-9]*='; then
  exit 0
fi

# --- Safelist: read-only and low-risk commands ---

# Extract the first token as the base command
BASE_CMD=$(printf '%s\n' "$COMMAND" | awk '{print $1}' | sed 's|.*/||')

# Read-only git subcommands — reject dangerous flags
if printf '%s\n' "$COMMAND" | grep -qE '^git (status|log|diff|show|stash list|describe|rev-parse|shortlog|ls-files|ls-tree)( |$)'; then
  # Block flags that write files or execute external programs (including abbreviations)
  if printf '%s\n' "$COMMAND" | grep -qiE '\-\-(out[a-z]*|ext-d[a-z]*|textc[a-z]*|exec[a-z]*|upload-p[a-z]*)'; then
    exit 0
  fi
  allow
fi
# git remote — only viewing, not add/remove/rename/set-url
if printf '%s\n' "$COMMAND" | grep -qE '^git remote( -v)?$'; then
  allow
fi
# git branch — only listing operations, not -d/-D/--delete/create
if printf '%s\n' "$COMMAND" | grep -qE '^git branch( -[avr]+)*( --(list|show-current|all|remotes))?$'; then
  allow
fi

# Filesystem reads
case "$BASE_CMD" in
  ls|pwd|wc|which|type|readlink|basename|dirname|realpath|tree)
    allow
    ;;
esac

# cat, head, tail, file, stat — block reads of sensitive paths
case "$BASE_CMD" in
  cat|head|tail|file|stat)
    if printf '%s\n' "$COMMAND" | grep -qiE '(\.ssh|\.aws|\.gnupg|\.env|\.npmrc|\.pypirc|\.netrc|\.docker|/etc/shadow|/etc/passwd|/proc/|credentials|secret|private|token|password|\.pem|\.key|id_rsa|id_ed25519)'; then
      exit 0
    fi
    allow
    ;;
esac

# Process / system inspection
case "$BASE_CMD" in
  date|uptime)
    allow
    ;;
esac

# Version checks — restricted to known safe binaries only
if printf '%s\n' "$COMMAND" | grep -qE '^(node|python[0-9.]*|ruby|go|cargo|rustc|gcc|g\+\+|clang|java|javac|dotnet|php|perl|git|docker|kubectl|terraform|helm|deno) (--version|-[vV])$'; then
  allow
fi

# Dev tool read-only queries — reject --registry/--index-url (SSRF)
if printf '%s\n' "$COMMAND" | grep -qE '^(npm list|npm ls|npm outdated|npm view|npm info)( |$)'; then
  if printf '%s\n' "$COMMAND" | grep -qiE '\-\-registry'; then
    exit 0
  fi
  allow
fi
if printf '%s\n' "$COMMAND" | grep -qE '^(pip list|pip show|pip freeze|pip check)( |$)'; then
  if printf '%s\n' "$COMMAND" | grep -qiE '\-\-(index-url|extra-index-url)'; then
    exit 0
  fi
  allow
fi

# cargo, go — all excluded. cargo metadata/tree may trigger build.rs;
# go list/env/doc may trigger module downloads or write persistent config.

# Test runners — all excluded.
# Build commands — all excluded.

# --- Default: fall through to normal permission prompt ---
exit 0
