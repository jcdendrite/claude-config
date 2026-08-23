#!/usr/bin/env bash
set -euo pipefail

# Reports whether autonomous shipping is active for the current repo.
# Exit 0 = active, non-zero = inactive or not inside a git repo.
# Delegates to _lib_autonomous_shipping_active so this check and CLAUDE.md's
# shipping-authorization prose stay backed by one implementation.
# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

REPO_ROOT=$(git rev-parse --show-toplevel) || {
  echo "autonomous-shipping-active.sh: not inside a git repository" >&2
  exit 1
}

_lib_autonomous_shipping_active "$REPO_ROOT"
