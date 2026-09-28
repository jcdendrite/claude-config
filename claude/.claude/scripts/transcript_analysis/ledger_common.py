"""Recording primitives shared by the cost-ledger and pr-cost ledgers: the generated per-config-dir machine identity, the
git-tracked destination check, the machine-label format, the merge-conflict markers both parsers refuse, and the local-lock
timing both --record paths use."""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from _config_dir import config_dir

# Real git conflict markers are exactly these 7-character prefixes (each
# followed by a ref name on <<<<<<</>>>>>>> or nothing on =======).
_COST_LEDGER_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


# Short, lowercase-alphanumeric, no spaces or unicode -- wide enough for
# "m1"/"laptop2", narrow enough that a hostname or username can't be
# expressed in it (see docs/cost-ledger.md). \Z (not $) so a trailing
# newline doesn't slip past the anchor.
_MACHINE_LABEL_RE = re.compile(r"^[a-z0-9]{1,8}\Z")


# Local-lock convenience bound, not a protocol-grounded value -- this guards
# an interactive CLI's own read-check-write window against another local
# --record, not a network call, so no vendor timeout spec applies. 30s
# comfortably exceeds a single --record's read-check-write step (parse,
# upsert, temp-write, atomic rename) while still surfacing a wedged or
# long-running concurrent recorder within one interactive command.
_COST_LEDGER_LOCK_TIMEOUT_S = 30.0
_COST_LEDGER_LOCK_POLL_INTERVAL_S = 0.1


_MACHINE_IDENTITY_FILENAME = "machine-id"
# Deliberately narrower than _MACHINE_LABEL_RE, since secrets.token_hex(4)
# can only produce exactly eight lowercase hex characters. A hand-written
# value that is well-formed under the wider regex (e.g. "acme1") is refused
# here rather than silently adopted.
#
# \Z (not $), matching _MACHINE_LABEL_RE's own anchor, so a trailing newline
# doesn't slip past it.
_MACHINE_IDENTITY_RE = re.compile(r"^[0-9a-f]{8}\Z")


def _machine_identity_path(config_dir_override: Path | None = None) -> Path:
    """Path to the generated, per-config-dir machine identity file.
    Mirrors _pr_cost_ledger_path's own override-parameter shape. Resolved
    through the module-global config_dir binding only, deliberately never
    through COST_LEDGER_PATH/PR_COST_LEDGER_PATH. Either override may point
    at a shared or synced location distinct from this machine's own config
    directory, so an identity resolved there would be shared by every
    machine writing to it."""
    return (config_dir_override or config_dir()) / _MACHINE_IDENTITY_FILENAME


def _read_machine_identity_or_refuse(subcommand: str, path: Path, location_label: str) -> str:
    """Read and validate an already-existing machine identity file, exiting
    1 unless its content matches _MACHINE_IDENTITY_RE exactly. Refuses on:

    - a missing file, including a dangling symlink
    - an unreadable file
    - an empty file
    - hand-written content that doesn't match the regex

    Never echoes the resolved path, matching this module's other
    home-rooted-path redaction discipline. `location_label` names the
    identity file's location in the refusal message instead. Deleting the
    file mints a new identity, under which existing rows read as a
    different machine. The refusal message states that instead of
    auto-healing the file."""
    try:
        # errors="replace" (not read_text()'s strict decode), so non-UTF-8
        # bytes fail _MACHINE_IDENTITY_RE's match below and refuse cleanly
        # rather than raising UnicodeDecodeError uncaught.
        # Assumes path is a regular file or a symlink to one. A FIFO would
        # block indefinitely here. This is accepted, because triggering it
        # needs a planted special file in a config dir the attacker already
        # writes to.
        raw = path.read_bytes()
    except OSError:
        raw = None
    stripped = raw.decode("utf-8", errors="replace").strip() if raw is not None else None
    if stripped is None or not _MACHINE_IDENTITY_RE.match(stripped):
        print(
            f"{subcommand}: {location_label} does not hold a well-formed"
            " machine identity -- delete it to mint a new one (existing rows will then read as a"
            " different machine)",
            file=sys.stderr,
        )
        sys.exit(1)
    return stripped


def _resolve_machine_identity(
    subcommand: str, config_dir_override: Path | None = None, location_label: str | None = None
) -> str:
    """Generates and persists a per-config-dir identity via
    secrets.token_hex(4) on first use, publishing it through mkstemp+os.link
    so a losing racer adopts the winner's value instead of a torn write
    (see docs/pr-cost.md's "Machine identity").
    """
    path = _machine_identity_path(config_dir_override)
    if location_label is None:
        location_label = f"~/.claude/{_MACHINE_IDENTITY_FILENAME}"
    if path.exists():
        return _read_machine_identity_or_refuse(subcommand, path, location_label)

    # 8 lowercase hex chars is a 32-bit collision budget (see docs/pr-cost.md's
    # "Refusals" section for the cloud-sync-duplication collision discussion).
    token = secrets.token_hex(4)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{_MACHINE_IDENTITY_FILENAME}.")
        with os.fdopen(fd, "w") as f:
            f.write(token)
        try:
            os.link(tmp_name, path)
        except FileExistsError:
            return _read_machine_identity_or_refuse(subcommand, path, location_label)
    except OSError:
        # Not exc's str(): a real mkstemp/os.link failure embeds the full
        # temp-file path via exc.filename/filename2, which sits inside
        # this machine's own config dir.
        print(
            f"{subcommand}: could not create the machine identity file ({location_label})",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
    return token


def _warn_machine_identity_absent_from_ledger(subcommand: str, identity: str, rows: Sequence[dict]) -> None:
    """Print a one-time stderr notice when `rows` is non-empty and none of
    them carries `identity`. This is the expected split the first --record
    run under a freshly generated identity produces, since a generated
    identity is never adopted from an existing row. Fires at most once per
    ledger per --record run: once a row carrying `identity` lands, the
    guard no longer applies.
    """
    if not rows:
        return
    if any(row["machine"] == identity for row in rows):
        return
    print(
        f"{subcommand}: this machine's generated identity ({identity}) carries no rows yet, but"
        f" {len(rows)} existing row(s) do under a different machine value -- the next captured row"
        " for any PR/week already captured under that other value is expected to produce a second"
        " row, which is safe to sum (see docs/pr-cost.md). Rule out a synced or dotfile-tracked"
        " config directory before treating this as the ordinary upgrade case.",
        file=sys.stderr,
    )


def _ledger_path_is_git_tracked(ledger_path: Path, subcommand: str = "cost-ledger") -> bool:
    """Return True iff the nearest existing ancestor of ledger_path sits
    inside a git working tree -- scopes --record's multi-root refusal to
    paths git could actually commit/push, not every ledger destination.
    Fails closed (True) on any ambiguous result: a missing git binary, a
    timeout, or a non-zero exit that isn't git's clean "not a git
    repository" signal (a bare repository, for instance, exits 0 with
    stdout "false" -- tracked by git but not a work tree, so this returns
    False for it). `subcommand` labels this function's own stderr
    diagnostics (default "cost-ledger", its original caller); pr-cost passes
    its own name so a git-tracked check failure isn't misattributed."""
    ancestor = ledger_path.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    # Explicit env, not the inherited one: a GIT_DIR/GIT_WORK_TREE exported
    # in the caller's shell would otherwise redirect this check to an
    # unrelated repo; removing (not blanking) them restores git's normal
    # discovery. LC_ALL=C pins the fatal-error text checked below to stable
    # English regardless of the operator's locale.
    env = os.environ.copy()
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    env["LC_ALL"] = "C"
    try:
        # Same local-git timeout rationale as _repo_scoped_project_slugs's
        # git calls: no network/credential work, so 10s only bounds a
        # wedged invocation.
        # encoding/errors pinned explicitly: text=True alone decodes with the
        # parent process's own locale, not LC_ALL=C above (that only governs
        # what bytes git emits) -- under a narrow-locale parent, a non-ASCII
        # ancestor path embedded in git's stderr could otherwise raise
        # UnicodeDecodeError uncaught, defeating fail-closed.
        proc = subprocess.run(
            ["git", "-C", str(ancestor), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10, check=False, env=env,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        # Not exc's str(): TimeoutExpired renders the full argv, which
        # includes `ancestor` -- a home-rooted path this module otherwise
        # never echoes to stderr.
        print(f"{subcommand}: git-tracked check timed out", file=sys.stderr)
        return True
    except OSError as exc:
        print(f"{subcommand}: git-tracked check failed ({exc})", file=sys.stderr)
        return True
    if proc.returncode == 0:
        # git only ever emits "true"/"false" here on success; anything else
        # is treated as "false" rather than validated against that literal.
        return proc.stdout.strip() == "true"
    if "not a git repository" in proc.stderr:
        return False
    print(
        f"{subcommand}: git-tracked check exited {proc.returncode} unexpectedly",
        file=sys.stderr,
    )
    return True
