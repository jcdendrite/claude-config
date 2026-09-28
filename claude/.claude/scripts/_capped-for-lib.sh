#!/bin/bash
# _capped-for-lib.sh — portable timeout/gtimeout wrapper for render-settings.sh
# invocations.
#
# Sourced by ensure-settings-render.sh and install.sh's render-settings-invoke
# marker block. Not executable on its own; source it, do not invoke it
# directly.
#
# _capped_for is a thin alias over hooks/_lib.sh's _lib_capped_for -- see that
# function's own doc comment for the probe order, -k escalation, exit-status
# contract, and D-state/no-binary caveats. scripts/marker.sh already sources
# hooks/_lib.sh directly, the same cross-directory dependency as here.
# The reverse direction is not viable: marketplace plugins (e.g.
# plugins/skill-management/hooks/_lib.sh) carry standalone copies of this
# closure with no scripts/ sibling to source, and
# test_shared_closure_function_is_identical_across_stowed_and_plugin_lib pins
# _lib_capped_for's body byte-identical across every copy.
# shellcheck source=../hooks/_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../hooks/_lib.sh"

_capped_for() {
  _lib_capped_for "$@"
}
