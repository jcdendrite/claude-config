#!/usr/bin/env bash
# claude-auto.sh — launch Claude Code in auto mode on a model auto mode accepts.
#
# Resolves opusplan's plan/execution mismatch for auto mode, which needs one
# fixed session model — see docs/auto-mode.md's Requirements section. Select
# a model the same way you would with claude; with none named, this falls
# back to Sonnet, which auto mode accepts on every provider.
#
#   claude-auto                       # auto mode on Sonnet
#   claude-auto --model opus          # auto mode on Opus
#   claude-auto "summarize open PRs"  # positional prompt passes through
#
# A caller-supplied --model wins: injecting a second one would leave two
# --model flags on the command line and make the winner parser-dependent.
# Scanning stops at a literal `--`, since everything after it is positional
# text rather than a flag this wrapper should defer to.
model_args=(--model "${ANTHROPIC_MODEL:-sonnet}")
for arg in "$@"; do
  case "$arg" in
    --) break ;;
    --model | --model=*)
      model_args=()
      break
      ;;
  esac
done
exec claude "${model_args[@]}" --permission-mode auto "$@"
