"""Recording primitives shared by the cost-ledger and pr-cost ledgers: the generated per-config-dir machine identity, the
git-tracked destination check, the machine-label format, the merge-conflict markers both parsers refuse, and the local-lock
timing both --record paths use. Also the create-new file publish helpers: the fsynced temp-file-plus-hard-link core and the
two `--out` helpers (refusal before any work, publish last) that subcommands writing one owner-only file share."""
from __future__ import annotations

import contextlib
import enum
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


class _PublishOutcome(enum.Enum):
    """How _publish_new_file ended. The failures stay distinct so each caller keeps its own messages."""

    CREATED = "created"
    EXISTS = "exists"
    WRITE_FAILED = "write failed"
    LINK_FAILED = "link failed"


def _publish_new_file(path: Path, text: str, temp_prefix: str, temp_suffix: str) -> _PublishOutcome:
    """Create `path` holding `text`, never replacing an existing entry at `path`, a dangling symlink included.

    The text is written to a same-directory `mkstemp` file (mode `0600`),
    flushed and fsynced, and then hard-linked to `path`. A crash mid-write
    therefore leaves only a temp file, never a torn file at `path`, and
    `os.link` lets exactly one of several racing writers claim the name.
    `path` shares the temp file's inode, so it is `0600` with no separate
    chmod. The temp file is removed on every outcome.

    A failed `mkstemp`, write, flush or fsync is WRITE_FAILED. The OSError is
    not returned, since its text embeds the temp file's full path.
    """
    tmp_name: str | None = None
    try:
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=temp_prefix, suffix=temp_suffix)
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            return _PublishOutcome.WRITE_FAILED
        try:
            os.link(tmp_name, path)
        except FileExistsError:
            return _PublishOutcome.EXISTS
        except OSError:
            # Not narrowed to a missing or unwritable parent directory:
            # os.link also fails on EXDEV and ENOSPC.
            return _PublishOutcome.LINK_FAILED
        return _PublishOutcome.CREATED
    finally:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)


def _resolve_machine_identity(
    subcommand: str, config_dir_override: Path | None = None, location_label: str | None = None
) -> str:
    """Generates and persists a per-config-dir identity via
    secrets.token_hex(4) on first use, publishing it through
    _publish_new_file so a losing racer adopts the winner's value instead of
    a torn write (see docs/pr-cost.md's "Machine identity").
    """
    path = _machine_identity_path(config_dir_override)
    if location_label is None:
        location_label = f"~/.claude/{_MACHINE_IDENTITY_FILENAME}"
    if path.exists():
        return _read_machine_identity_or_refuse(subcommand, path, location_label)

    # 8 lowercase hex chars is a 32-bit collision budget (see docs/pr-cost.md's
    # "Refusals" section for the cloud-sync-duplication collision discussion).
    token = secrets.token_hex(4)
    outcome = _publish_new_file(path, token, f"{_MACHINE_IDENTITY_FILENAME}.", "")
    if outcome is _PublishOutcome.CREATED:
        return token
    if outcome is _PublishOutcome.EXISTS:
        return _read_machine_identity_or_refuse(subcommand, path, location_label)
    # Any other outcome fails closed. No OSError text: a real mkstemp/os.link
    # failure embeds the full temp-file path via exc.filename/filename2, which
    # sits inside this machine's own config dir.
    print(
        f"{subcommand}: could not create the machine identity file ({location_label})",
        file=sys.stderr,
    )
    sys.exit(1)


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


def _refuse_unusable_out_path(out: str, command: str) -> None:
    """Exit 2 unless `out` names a path that does not exist and sits outside any git working tree.

    Run before any work. `command` names the subcommand in each message.
    Only the operator's own literal `out` string is echoed, never the resolved
    path, which can embed a home-rooted engagement directory.
    """
    # Resolved (following any symlink at `out` itself) so the git-tree check
    # below sees where the path really points. For a dangling symlink,
    # resolve() still reports that the target doesn't exist.
    resolved_out = Path(out).resolve()
    # lexists() here buys nothing over exists(), since resolve() above
    # already followed every symlink. This check is a UX-only fast path.
    # The symlink defense is _publish_new_owner_file's os.link, which raises
    # FileExistsError on an existing destination rather than dereferencing it.
    if os.path.lexists(str(resolved_out)):
        print(
            f"{command}: --out {out!r} already exists -- refusing to overwrite; pass a new path",
            file=sys.stderr,
        )
        sys.exit(2)
    # resolved_out is re-resolved independently when _publish_new_owner_file
    # publishes. A symlink retargeted between the two is a known, accepted
    # TOCTOU window for this tool's single-operator threat model, not one this
    # check closes.
    if _ledger_path_is_git_tracked(resolved_out, command):
        print(
            f"{command}: --out {out!r} is inside a git working tree -- this repo (and every"
            " repo this subcommand might be run from) is potentially public; write outside any"
            " git working tree",
            file=sys.stderr,
        )
        sys.exit(2)


def _publish_new_owner_file(out: str, text: str, command: str) -> None:
    """Publish `text` at `out` as a new `0600` file through _publish_new_file, exiting 2 on any failure.

    Run last, after the work that produced `text`. `command` names the
    subcommand in each message and in the `.{command}-` temp-file prefix.
    """
    # The final path component is left exactly as named, unlike the resolved
    # path in _refuse_unusable_out_path, so a symlink there makes os.link fail
    # with FileExistsError instead of silently following the link. The parent
    # is still resolved, so a symlinked parent directory lands inside the same
    # target the git-tree check validated.
    open_path = Path(out).parent.resolve() / Path(out).name
    outcome = _publish_new_file(open_path, text, f".{command}-", ".tmp")
    if outcome is _PublishOutcome.CREATED:
        return
    if outcome is _PublishOutcome.EXISTS:
        print(
            f"{command}: --out {out!r} already exists -- refusing to overwrite; pass a new path",
            file=sys.stderr,
        )
        sys.exit(2)
    if outcome is _PublishOutcome.WRITE_FAILED:
        print(
            f"{command}: --out {out!r} could not be written -- pass a new path",
            file=sys.stderr,
        )
        sys.exit(2)
    # LINK_FAILED, and any outcome not named above, fails closed.
    print(
        f"{command}: --out {out!r} could not be published; pass a new path",
        file=sys.stderr,
    )
    sys.exit(2)
