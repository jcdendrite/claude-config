# shellcheck shell=bash
# Sourced by install-dev.sh.
# Not executable on its own; source it, do not invoke it directly.

# Prints the Python minor ("X.Y") that CI pins in the workflow file $1, from its quoted `python-version: 'X.Y'` lines.
# Returns 1 with an actionable "ERROR:" line on stderr when the file is unreadable, has no quoted pin, has more than one distinct pin, or the pin is not X.Y.
# An unquoted `python-version:` line is not a pin here, because YAML loads an unquoted 3.10 as the float 3.1.
read_ci_python_pin() {
  local workflow_file="$1" pin_values pin_form='^[0-9]+\.[0-9]+$'
  if [[ ! -r "$workflow_file" ]]; then
    echo "ERROR: cannot read $workflow_file for the CI Python pin. Run from the repo root of a full checkout." >&2
    return 1
  fi
  pin_values="$(sed -nE "s/^[[:space:]]*python-version:[[:space:]]*['\"]([^'\"]*)['\"].*$/\\1/p" "$workflow_file" | sort -u)"
  if [[ -z "$pin_values" ]]; then
    echo "ERROR: no quoted \`python-version: 'X.Y'\` line in $workflow_file; install-dev.sh reads the CI Python pin there." >&2
    return 1
  fi
  if [[ "$pin_values" == *$'\n'* ]]; then
    echo "ERROR: $workflow_file pins several Python versions ($(tr '\n' ' ' <<< "$pin_values")); pin one X.Y in every setup-python step." >&2
    return 1
  fi
  if ! [[ "$pin_values" =~ $pin_form ]]; then
    echo "ERROR: $workflow_file pins Python '$pin_values', which is not a minor version X.Y; install-dev.sh needs a python<X.Y> interpreter name." >&2
    return 1
  fi
  printf '%s\n' "$pin_values"
}
