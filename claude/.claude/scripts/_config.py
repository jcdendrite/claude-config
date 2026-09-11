"""Shared config-key reader for Python scripts -- the Python-runtime
counterpart to claude/.claude/hooks/_config.sh, pinned to identical
behavior by test_config_parser_parity.py. config-keys.psv is the schema;
claude-config.toml is the state file. Imports config_dir from the existing
_config_dir.py (no rename, no import churn at transcript-analysis.py:33).

config_enabled()/config_value() return None on a config-dir resolution
failure (config_dir()'s ValueError, caught here rather than left to
propagate) -- this module's counterpart to _config.sh's exit code 2. A
genuinely unknown key (not in config-keys.psv) raises KeyError instead: no
legitimate caller in this repo reaches that path (every call site passes a
hardcoded literal key name), so a loud Python exception is preferable to a
third silent-fallback value that a caller might mistake for "disabled" or
"unresolvable".
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from _config_dir import config_dir

# Matches _config_dir.py's own explicit ASCII-only whitespace set -- never
# str.strip(), which also eats NBSP (U+00A0)/ideographic space (U+3000) and
# would silently accept a value _config.sh's [:space:]-based trim rejects.
_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"

_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "hooks" / "config-keys.psv"
_STATE_FILENAME = "claude-config.toml"


@dataclass(frozen=True)
class SchemaRow:
    """One config-keys.psv row -- see that file's header comment for what
    each column means."""

    key: str
    type: str
    default: str
    resolution: str
    legacy_probe_on_resolution_failure: bool
    legacy_import_locations: str
    legacy_filename: str
    legacy_polarity: str
    human_name: str
    docs_anchor: str
    prompt_description: str


def schema() -> dict[str, SchemaRow]:
    """Parse config-keys.psv into {key: SchemaRow}. Skips blank lines,
    leading-'#' comments, and a row whose parsed key field is itself empty
    (e.g. a stray leading pipe), matching _config.sh's _config_schema_field /
    _config_schema_known_keys's `case "$row_key" in ''|'#'*) continue ;;
    esac` / install.sh:479's own IFS='|' read -r idiom. A row with fewer
    than 11 fields is padded with empty strings, and one with more has its
    trailing pipes absorbed into the last field -- the same leniency bash's
    own `IFS='|' read -r` gives a schema row with the wrong field count. A
    duplicate key row keeps its first occurrence, matching
    _config_schema_field's own first-match `return 0` inside its read loop.

    Returns an empty dict, after printing a distinct stderr warning naming
    the schema file, when config-keys.psv itself is missing or unreadable --
    a partial stow-relink or interrupted `git pull` -- rather than letting
    an uncaught OSError propagate with no indication the real cause is
    infrastructure, not a caller's key name. A caller's own `schema()[key]`
    then raises the same KeyError an ordinary unknown key would, but the
    warning above already named the actual cause.
    """
    rows: dict[str, SchemaRow] = {}
    try:
        text = _SCHEMA_FILE.read_text(errors="replace")
    except OSError:
        print(f"_config.py: warning: schema file not found or unreadable: {_SCHEMA_FILE}", file=sys.stderr)
        return rows
    for raw_line in text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if not line or line.startswith("#"):
            continue
        fields = line.split("|", 10)
        while len(fields) < 11:
            fields.append("")
        (
            key,
            type_,
            default,
            resolution,
            legacy_probe,
            legacy_import,
            legacy_filename,
            legacy_polarity,
            human_name,
            docs_anchor,
            prompt_description,
        ) = fields
        if not key or key in rows:
            continue
        rows[key] = SchemaRow(
            key=key,
            type=type_,
            default=default,
            resolution=resolution,
            legacy_probe_on_resolution_failure=(legacy_probe == "true"),
            legacy_import_locations=legacy_import,
            legacy_filename=legacy_filename,
            legacy_polarity=legacy_polarity,
            human_name=human_name,
            docs_anchor=docs_anchor,
            prompt_description=prompt_description,
        )
    return rows


class _MalformedStateLine(Exception):
    """Raised by _parse_state_line for a non-blank, non-comment line that
    fails claude-config.toml's `key = value` grammar."""


def _parse_state_line(raw_line: str) -> tuple[str, str] | None:
    """Parse one claude-config.toml line, mirroring _config.sh's
    _config_line_key_value exactly: the value subset is exactly `true`,
    `false`, or a bare `[a-z0-9_-]+` token -- no quoting, no escapes, no
    arrays, no tables, so a TOML table/array/multi-line-string line always
    fails rather than being partially understood. A trailing CR (tolerating
    a CRLF line mixed into an otherwise-LF file) is stripped here, not by
    the caller -- both readers strip it in the same place.

    Returns None for a blank line or a full-line '#' comment (never
    warned). Raises _MalformedStateLine for anything else that fails the
    grammar -- the caller decides whether/how to warn (a hand-edit typo
    affects only that one key, not the whole document).

    The value is case-folded to lowercase before the subset check (this
    repo's own key names are already lowercase snake_case, so only the
    value needs folding), matching _config.sh's tr-based fold so a
    hand-authored `TRUE`/`True`/`FALSE` still resolves identically in both
    readers.
    """
    line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
    trimmed = line.strip(_ASCII_WHITESPACE)
    if not trimmed or trimmed.startswith("#"):
        return None
    if "=" not in trimmed:
        raise _MalformedStateLine(raw_line)
    key_part, _, value_part = trimmed.partition("=")
    key = key_part.strip(_ASCII_WHITESPACE)
    value = value_part.strip(_ASCII_WHITESPACE).lower()
    if not key or any(ch not in _KEY_CHARS for ch in key):
        raise _MalformedStateLine(raw_line)
    if not value or any(ch not in _VALUE_CHARS for ch in value):
        raise _MalformedStateLine(raw_line)
    return key, value


_KEY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_VALUE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _value_matches_schema_type(value: str, type_: str) -> bool:
    """True iff VALUE is a legitimate value for a `bool` or `enum:X`-typed
    key -- mirrors _config.sh's _config_read_key_from_file type-validation
    case statement exactly, using the same TYPE strings _config_set already
    validates against at write time."""
    if type_ == "bool":
        return value in ("true", "false")
    if type_.startswith("enum:"):
        return value in ("false", type_.removeprefix("enum:"))
    return True


def _read_key_from_file(key: str, type_: str, state_file: Path, known_keys: frozenset[str]) -> str | None:
    """KEY's value from STATE_FILE, or None if the file is absent or has no
    conforming row for KEY. A duplicate KEY row is not rejected -- the LAST
    occurrence wins, matching _config_set's own last-write-wins rewrite
    semantics. Every malformed line is warned once, to stderr, truncated to
    80 chars, mirroring _config_read_key_from_file exactly -- regardless of
    whether that line belongs to KEY or a different key, since this
    function already has to walk the whole file.

    A row for KEY whose value passes the generic grammar check but fails
    TYPE (KEY's own config-keys.psv `type`, e.g. a `bool` key's value being
    neither `true` nor `false`) is warned and skipped the same way, not
    returned as authoritative -- otherwise a hand-edit typo on a bool key
    would resolve as "enabled" under config_enabled's own
    any-value-but-false rule, the wrong direction for a key whose off-state
    contract is security-relevant.

    A line whose key is grammatically valid but not in KNOWN_KEYS (a case-
    or spelling-typo'd key) is a distinct case from a malformed line -- it
    parses cleanly and would otherwise sit silently ignored forever, since
    no lookup ever queries that exact misspelled string. Warned once, to
    stderr, truncated to 80 chars, mirroring
    _config_read_key_from_file's own distinct unrecognized-key warning.
    """
    try:
        text = state_file.read_text(errors="replace")
    except OSError:
        return None
    if text.startswith("\ufeff"):
        text = text[1:]
    result: str | None = None
    for raw_line in text.split("\n"):
        try:
            parsed = _parse_state_line(raw_line)
        except _MalformedStateLine:
            truncated = raw_line[:80]
            print(f"_config.py: warning: skipping malformed line in {state_file}: {truncated}", file=sys.stderr)
            continue
        if parsed is None:
            continue
        parsed_key, value = parsed
        if parsed_key not in known_keys:
            truncated = raw_line[:80]
            print(
                f"_config.py: warning: skipping line for unrecognized key in {state_file}: {truncated}",
                file=sys.stderr,
            )
            continue
        if parsed_key == key:
            if not _value_matches_schema_type(value, type_):
                truncated = raw_line[:80]
                print(f"_config.py: warning: skipping malformed line in {state_file}: {truncated}", file=sys.stderr)
                continue
            result = value
    return result


def _location_value(key: str, row: SchemaRow, directory: Path, known_keys: frozenset[str]) -> str:
    """KEY's effective value as seen from DIRECTORY alone -- mirrors
    _config.sh's _config_location_value exactly. DIRECTORY/claude-config.toml
    is checked first (any conforming row there for KEY is authoritative);
    only when KEY is entirely absent from that file does DIRECTORY's own
    legacy file get consulted (per KEY's legacy-polarity); only when that
    legacy file is also absent does the schema default apply.
    """
    state_file = directory / _STATE_FILENAME
    value = _read_key_from_file(key, row.type, state_file, known_keys)
    if value is not None:
        return value

    legacy_path = directory / row.legacy_filename
    if row.legacy_polarity == "presence-enables":
        return "true" if legacy_path.exists() else "false"
    if row.legacy_polarity == "presence-disables":
        return "false" if legacy_path.exists() else "true"
    if row.legacy_polarity == "content-matches":
        if legacy_path.exists():
            try:
                raw = legacy_path.read_text(errors="replace")
            except OSError:
                raw = ""
            # [:space:]-equivalent trim (ASCII whitespace, incl. CR), not a
            # [:blank:]-equivalent one -- install.sh:531-547 and
            # pr-cost-section.sh:20-26 diverge on this trim class.
            mode = raw.strip(_ASCII_WHITESPACE).lower()
            expected = row.type.removeprefix("enum:")
            if mode == expected:
                return expected
        return "false"
    if not row.legacy_polarity:
        # An empty legacy-polarity means KEY has no legacy file to protect.
        # Fall through to the schema default below with no warning.
        return row.default
    # A non-empty legacy-polarity value outside the three literals above is
    # config-keys.psv corruption, since this git-tracked, code-reviewed file
    # has no other writer.
    # Warn loudly rather than silently trusting the schema default below.
    # worktree_required's own default ("false") is the permissive direction
    # for this enforcement-critical key, so this case fails closed to
    # "true" instead.
    # The other four enforcement-critical keys' schema defaults are already
    # their own fail-closed direction, so they fall through unchanged.
    print(
        f"_config.py: warning: unrecognized legacy-polarity value for {key}: "
        f"{row.legacy_polarity} -- falling back to schema default",
        file=sys.stderr,
    )
    if key == "worktree_required":
        return "true"
    return row.default


def config_value(key: str, config_dir_override: Path | str | None = None) -> str | None:
    """KEY's effective value ("true", "false", or an enum literal), or None
    if the config dir could not be resolved and KEY's schema row does not
    authorize a raw $HOME/.claude fallback on that failure
    (legacy_probe_on_resolution_failure) -- this module's counterpart to
    _config.sh's exit code 2. Raises KeyError if KEY has no schema row (see
    module docstring).

    config_dir_override lets a caller that already has its own config dir
    (e.g. transcript-analysis.py's --all-accounts loop, one
    account_config_dir per iteration) skip re-resolving it and skip the
    config-dir-or-home union below entirely -- mirrors _config.sh's own
    CONFIG_DIR_OVERRIDE parameter. An empty-string override is treated the
    same as no override at all, matching _config.sh's `[ -n
    "$config_dir_override" ]` test -- but only for a `str` argument. A
    `Path("")` argument is NOT treated as "no override": pathlib normalizes
    `Path("")` to the truthy `PosixPath('.')`, so the `if config_dir_override`
    check above passes and resolves against cwd. Not currently reachable --
    no call site in this repo constructs a `Path("")` override -- but a
    future caller that does would silently get cwd instead of "no override".

    Union semantics: for a config-dir-or-home key, the value is
    OR'd across BOTH locations' own independently-resolved effective value
    (each going through its own state-file-then-legacy-then-default chain
    via _location_value) -- not "first location found wins." An explicit
    `false` row in the config dir's state file must not defeat a `true`
    produced by $HOME/.claude's legacy file.
    """
    all_rows = schema()
    row = all_rows[key]
    known_keys = frozenset(all_rows)
    override = str(config_dir_override) if config_dir_override else None

    primary_dir: Path | None
    if override is not None:
        primary_dir = Path(override)
    else:
        try:
            primary_dir = config_dir()
        except ValueError:
            primary_dir = None

    if primary_dir is not None:
        primary_value = _location_value(key, row, primary_dir, known_keys)
        home_env = os.environ.get("HOME")
        if override is None and row.resolution == "config-dir-or-home" and home_env:
            home_dir_str = home_env.rstrip("/") + "/.claude"
            if home_dir_str != str(primary_dir).rstrip("/"):
                home_value = _location_value(key, row, Path(home_dir_str), known_keys)
                if primary_value == "true" or home_value == "true":
                    return "true"
                return primary_value
        return primary_value

    # Primary resolution failed. Only worktree_required's row carries
    # legacy_probe_on_resolution_failure: true today.
    home_env = os.environ.get("HOME")
    if (
        override is None
        and row.resolution == "config-dir-or-home"
        and row.legacy_probe_on_resolution_failure
        and home_env
    ):
        return _location_value(key, row, Path(home_env.rstrip("/") + "/.claude"), known_keys)
    return None


def config_enabled(key: str, config_dir_override: Path | str | None = None) -> bool | None:
    """Boolean wrapper over config_value: True (enabled), False (disabled),
    None (config dir unresolvable -- see config_value's own docstring). Any
    resolved value other than the literal "false" counts as enabled -- an
    enum-typed key's only "off" value is "false", so e.g.
    pr_cost_disclosure resolving to "dollars" is enabled.
    """
    value = config_value(key, config_dir_override)
    if value is None:
        return None
    return value != "false"
