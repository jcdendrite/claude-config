#!/bin/bash
# _capped-for-lib.sh — portable timeout/gtimeout wrapper for render-settings.sh
# invocations.
#
# Sourced by ensure-settings-render.sh and install.sh's render-settings-invoke
# marker block. Not executable on its own; source it, do not invoke it
# directly.
#
# _capped_for is an alias over hooks/_lib.sh's _lib_capped_for, which documents
# the probe order, -k escalation, and exit-status contract.
# The reverse dependency direction is not viable: marketplace plugins carry
# standalone _lib.sh copies with no scripts/ sibling to source.
# shellcheck source=../hooks/_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../hooks/_lib.sh"

_capped_for() {
  _lib_capped_for "$@"
}
