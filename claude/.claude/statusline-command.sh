#!/usr/bin/env bash

input=$(cat)

# Extract fields from JSON input
model=$(echo "$input" | jq -r '.model.display_name // "Unknown Model"')
cwd=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // ""')
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
total_cost=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')
rate_5h=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
rate_7d=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')

# ANSI color codes (dim-friendly)
RESET='\033[0m'
DIM='\033[2m'
CYAN='\033[36m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[34m'
MAGENTA='\033[35m'
RED='\033[31m'

# --- Context progress bar ---
build_bar() {
    local pct="${1:-0}"
    local width=10
    local filled=$(( pct * width / 100 ))
    local empty=$(( width - filled ))
    local bar=""
    for _ in $(seq 1 $filled); do bar="${bar}#"; done
    for _ in $(seq 1 $empty); do bar="${bar}-"; done
    echo "$bar"
}

if [ -n "$used_pct" ]; then
    used_int=$(printf "%.0f" "$used_pct")
    bar=$(build_bar "$used_int")
    # Color the bar based on usage
    if [ "$used_int" -ge 85 ]; then
        bar_color="$RED"
    elif [ "$used_int" -ge 60 ]; then
        bar_color="$YELLOW"
    else
        bar_color="$GREEN"
    fi
    ctx_display=$(printf "${bar_color}[${bar}]${RESET} ${used_int}%%")
else
    ctx_display=$(printf "${DIM}[----------] --%${RESET}")
fi

# --- Rate limit display (5h / 7d) ---
rate_color() {
    local pct="${1:-0}"
    if [ "$pct" -ge 85 ]; then echo "$RED"
    elif [ "$pct" -ge 60 ]; then echo "$YELLOW"
    else echo "$GREEN"
    fi
}

if [ -n "$rate_5h" ] || [ -n "$rate_7d" ]; then
    h_pct=$(printf "%.0f" "${rate_5h:-0}")
    d_pct=$(printf "%.0f" "${rate_7d:-0}")
    h_color=$(rate_color "$h_pct")
    d_color=$(rate_color "$d_pct")
    rate_display=$(printf "${h_color}5h:${h_pct}%%${RESET} ${d_color}7d:${d_pct}%%${RESET}")
else
    rate_display=$(printf '%b5h:--  7d:--%b' "$DIM" "$RESET")
fi

# --- Session cost ---
cost_display=$(printf "\$%.4f" "$total_cost")

# --- Dynamic truncation limits based on terminal width ---
# Fixed visible chars: model(~12) + separators(~8) + bar([10 wide]=15) + rates(12) + cost(7) ≈ 54
_terminal_cols=$(stty size </dev/tty 2>/dev/null | awk '{print $2}')
[[ "$_terminal_cols" =~ ^[0-9]+$ ]] || _terminal_cols=${COLUMNS:-80}
_available=$(( _terminal_cols - 54 ))
[ "$_available" -lt 20 ] && _available=20
max_path_len=$(( _available * 55 / 100 ))
_max_branch_name=$(( _available * 45 / 100 - 3 ))
[ "$max_path_len" -lt 8 ] && max_path_len=8
[ "$_max_branch_name" -lt 4 ] && _max_branch_name=4

# --- Git branch ---
git_branch=""
if [ -n "$cwd" ] && [ -d "$cwd" ]; then
    branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null)
    if [ -n "$branch" ]; then
        if [ "${#branch}" -gt "$_max_branch_name" ]; then
            branch="${branch:0:$((_max_branch_name - 1))}…"
        fi
        git_branch=$(printf " ${MAGENTA}(%s)${RESET}" "$branch")
    fi
fi

# --- Working directory (shorten home, truncate to fit terminal) ---
home_dir="$HOME"
short_cwd="${cwd/#$home_dir/~}"
if [ "${#short_cwd}" -gt "$max_path_len" ]; then
    short_cwd="…${short_cwd: -$((max_path_len - 1))}"
fi

# --- Assemble status line ---
echo -e "${CYAN}${model}${RESET}  ${ctx_display}  ${rate_display}  ${YELLOW}${cost_display}${RESET}  ${BLUE}${short_cwd}${RESET}${git_branch}"
