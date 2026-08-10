#!/usr/bin/env python3
"""Diff dependency key sets between a package.json's pre-write and
post-write state, for ask-new-dependency-disclosure.sh.

Targets Python 3.9 explicitly — stock macOS `/usr/bin/python3` is 3.9.6,
with no `tomllib` and no `match` statement. No stdlib module added after
3.9, no PEP 604 `X | Y` union in runtime position (annotation-position
only, guarded by `from __future__ import annotations`). `ruff`'s
`target-version = "py312"` governs lint style repo-wide, not this file's
actual runtime floor — a 3.10+ construct here would lint clean and only
fail on a stow user's real interpreter, silently, since the calling hook
fails open on any helper error.

Reconstruction semantics (mirrors the table in
ask-new-dependency-disclosure.sh's header):
  Edit      — pre-state text, `old_string` -> `new_string`, honoring
              `replace_all`.
  MultiEdit — `edits` applied sequentially against a running buffer that
              starts as the pre-state text, each edit honoring its own
              `replace_all`.
  Write     — `content` verbatim as the post-state; pre-state is whatever
              the caller read from disk (empty string if the file didn't
              exist).
Dispatch between the three is by `tool_input` shape: an `edits` key means
MultiEdit, a `content` key means Write, otherwise Edit. A missing
`old_string`/`new_string` is treated as an empty string rather than
raising — PreToolUse fires prospectively on `tool_input`, before the real
tool call resolves, so this module always models edits assuming success.

Wire grammar (CLI wrapper stdout, on success):
    <marker-line>\\n<record>\\0<record>\\0...\\0<record>
`<marker-line>` is empty when nothing was elided by the 10-name cap, else
"…and N more". It comes FIRST and is newline-terminated, on its own line,
so the shell consumer reads it with one `read -r` before ever touching the
NUL-delimited stream that follows — keeping the two structurally distinct
even when a crafted dependency name is the literal marker text. `<record>`
is a sanitized
`name@constraint` pair; NUL-separated because a sanitized record can never
contain NUL (records are already newline-free, so NUL was free to choose
and can't collide with the marker line's own delimiter).

Exit 0 with the stdout above on success, including the empty-diff case
(marker line empty, zero records — just a single newline byte). Exit 1
with a diagnostic on stderr on any failure: a missing/non-dict
`tool_input`, an unreadable or unresolvable `file_path`, invalid JSON in
either the pre- or post-write manifest text, or a stdin payload over
`_MAX_STDIN_BYTES`. The caller (ask-new-dependency-disclosure.sh) treats
any nonzero exit as "could not determine the dependency delta" and asks
anyway with a generic reason — never silent-allow, and never a listing
built from a partial result, since nothing is written to stdout until the
full computation has succeeded.

A 5-second wall-clock budget (SIGALRM, POSIX-only — matches _lib_jq's and
require-plan-review.sh's 5s precedent for a local, non-network subprocess,
see _lib.sh) bounds the whole computation. This exists because
_lib_capped's `timeout` wrapper degrades to no cap at all without GNU
coreutils — exactly the stock-macOS environment this module already
targets — so the bound has to live here, independent of coreutils.
"""
from __future__ import annotations

import json
import signal
import sys
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple

# 2 MiB: a real package.json, even a several-hundred-dependency monorepo
# manifest, is well under 100 KB. This only bounds worst-case json.loads
# work against an adversarial payload; it is not expected to ever trip on
# real traffic.
_MAX_STDIN_BYTES = 2 * 1024 * 1024

# Per sanitized name/constraint field. No real npm identifier or semver
# range approaches this; it exists to bound the size of a single crafted
# field, not to accommodate a legitimate long one.
_MAX_RECORD_FIELD_CHARS = 512

_MAX_RECORDS = 10

# Matches _lib_jq's/_lib_capped's 5s precedent (_lib.sh) for a local,
# non-network subprocess — see module docstring.
_RUNTIME_BUDGET_SECONDS = 5

_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


class ManifestDeltaError(Exception):
    """Raised whenever the dependency delta cannot be computed: invalid
    JSON in either manifest state, a non-object manifest, or a
    structurally unexpected tool_input. The CLI wrapper's main() catches
    this (and any other exception) and exits 1 without writing any
    stdout — callers must never treat a computation failure as
    equivalent to a legitimate empty diff."""


class DependencyDelta(NamedTuple):
    """records: sorted, sanitized, capped `name@constraint` strings for
    dependencies present in the post-write manifest but not the pre-write
    one. elided_count: 0 when nothing was capped, else the number of
    additional new dependencies beyond `records` — kept out of `records`
    itself (not appended as a trailing entry) so a dependency literally
    named to match the cap-marker's text can never be read back as cap
    metadata."""

    records: list[str]
    elided_count: int


def _strip_control_and_ansi(value: str) -> str:
    """Strip C0 (0x00-0x1F) and C1 (0x7F-0x9F) control bytes, including
    ANSI escape sequences (which open with the C0 ESC byte), plus every
    Unicode "Cf" (Format) character -- the bidirectional-override/isolate
    controls and zero-width joiners the "Trojan Source" character set
    consists of -- from `value`. Applied to every package name and version
    constraint before either can reach a human-facing ask reason, so a
    crafted name cannot reorder or hide part of what the human reads."""
    return "".join(
        ch
        for ch in value
        if not (ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or unicodedata.category(ch) == "Cf")
    )


def _sanitize_field(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return _strip_control_and_ansi(text)[:_MAX_RECORD_FIELD_CHARS]


def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith("\ufeff") else text


def _parse_manifest(text: str) -> dict:
    if text == "":
        return {}
    try:
        parsed = json.loads(_strip_bom(text))
    except json.JSONDecodeError as exc:
        raise ManifestDeltaError(f"manifest text is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ManifestDeltaError("manifest JSON is not an object")
    return parsed


def _dependency_map(manifest: dict) -> dict:
    """Union dependencies/devDependencies/peerDependencies/
    optionalDependencies into one {name: constraint} map — new-vs-existing
    is decided by name membership across the union, so a dependency moved
    between two of these sections in the same edit correctly does not
    read as new."""
    merged: dict = {}
    for field in _DEPENDENCY_FIELDS:
        section = manifest.get(field)
        if isinstance(section, dict):
            for name, constraint in section.items():
                if isinstance(name, str):
                    merged[name] = constraint
    return merged


def _apply_single_edit(buffer: str, old_string: str, new_string: str, replace_all: bool) -> str:
    if replace_all:
        return buffer.replace(old_string, new_string)
    return buffer.replace(old_string, new_string, 1)


def _reconstruct_post_text(pre_text: str, tool_input: dict) -> str:
    if "edits" in tool_input:
        edits = tool_input.get("edits") or []
        buffer = pre_text
        for edit in edits:
            if not isinstance(edit, dict):
                raise ManifestDeltaError("MultiEdit edits entry is not an object")
            old_string = edit.get("old_string") or ""
            new_string = edit.get("new_string") or ""
            replace_all = bool(edit.get("replace_all", False))
            buffer = _apply_single_edit(buffer, old_string, new_string, replace_all)
        return buffer
    if "content" in tool_input:
        content = tool_input.get("content")
        return content if isinstance(content, str) else ""
    old_string = tool_input.get("old_string") or ""
    new_string = tool_input.get("new_string") or ""
    replace_all = bool(tool_input.get("replace_all", False))
    return _apply_single_edit(pre_text, old_string, new_string, replace_all)


def compute_new_dependency_names(pre_text: str, tool_input: dict) -> DependencyDelta:
    """Pure — no I/O. Reconstructs the post-write manifest text from
    `tool_input` (see module docstring), diffs the pre- and post-write
    dependency-name sets over their four-section union, and returns the
    sorted, sanitized, capped result. Raises ManifestDeltaError on
    anything that isn't a well-formed manifest object at either state —
    callers must not treat that as an empty diff."""
    if not isinstance(tool_input, dict):
        raise ManifestDeltaError("tool_input is not an object")

    pre_manifest = _parse_manifest(pre_text)
    post_text = _reconstruct_post_text(pre_text, tool_input)
    post_manifest = _parse_manifest(post_text)

    pre_deps = _dependency_map(pre_manifest)
    post_deps = _dependency_map(post_manifest)
    new_names = sorted(name for name in post_deps if name not in pre_deps)

    records = [f"{_sanitize_field(name)}@{_sanitize_field(post_deps[name])}" for name in new_names]
    if len(records) > _MAX_RECORDS:
        return DependencyDelta(records=records[:_MAX_RECORDS], elided_count=len(records) - _MAX_RECORDS)
    return DependencyDelta(records=records, elided_count=0)


def format_marker_line(elided_count: int) -> str:
    if elided_count <= 0:
        return ""
    return f"…and {elided_count} more"


def _resolve_file_path(tool_input: dict, cwd: str) -> Path:
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise ManifestDeltaError("tool_input.file_path missing or not a string")
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(cwd) / path if cwd else path
    return path


def _read_pre_state(path: Path) -> str:
    # Resolves symlinks (strict=False: a not-yet-existing Write target is
    # legitimate and must not raise) so the read below targets the real
    # backing file, not the link.
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ManifestDeltaError(f"could not resolve file path: {exc}") from exc
    if not resolved.is_file():
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestDeltaError(f"could not read pre-state file: {exc}") from exc


def _alarm_handler(signum: int, frame: Any) -> None:
    raise ManifestDeltaError(f"computation exceeded the {_RUNTIME_BUDGET_SECONDS}s runtime budget")


def main() -> int:
    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        print(f"parse-manifest-dependencies.py: stdin exceeds the {_MAX_STDIN_BYTES}-byte cap", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ManifestDeltaError("hook payload is not an object")
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            raise ManifestDeltaError("tool_input missing or not an object")
        cwd = payload.get("cwd") or ""

        # SIGALRM is POSIX-only and absent on Windows — not reached today,
        # since every host this hook targets is macOS/Linux (see the
        # module docstring's stock-macOS framing).
        previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(_RUNTIME_BUDGET_SECONDS)
        try:
            file_path = _resolve_file_path(tool_input, cwd)
            pre_text = _read_pre_state(file_path)
            delta = compute_new_dependency_names(pre_text, tool_input)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
    except Exception as exc:
        # Any failure here must route the caller to a degraded ask, never
        # a silent allow — see module docstring's exit-code contract.
        print(f"parse-manifest-dependencies.py: {exc}", file=sys.stderr)
        return 1

    # Single write: the wire-grammar output is fully built in memory before
    # stdout is touched at all, so a raise anywhere upstream cannot leave a
    # marker-line-only or records-without-marker state on stdout.
    output = format_marker_line(delta.elided_count) + "\n" + "\0".join(delta.records)
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
