"""Scan-root and project-scope resolution -- PROJECTS_DIR and every function
that resolves which sessions a subcommand reads. No dependency on any
cmd_* subcommand.

PROJECTS_DIR is the one reassignable global this package exposes; every
reader outside this module must access it as `scope.PROJECTS_DIR` (attribute
access on this module), never `from transcript_analysis.scope import
PROJECTS_DIR` -- the latter binds a reference at import time that a later
`scope.PROJECTS_DIR = ...` reassignment (main()'s --config-dir handling, or a
test's monkeypatch.setattr(scope, "PROJECTS_DIR", ...)) would never reach.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from _config_dir import (
    TRANSCRIPT_CONFIG_DIRS_LABEL,
    config_dir,
    declared_roots_file_state,
    declared_transcript_roots,
)

from transcript_analysis.corpus import iter_sessions, read_session_file

PROJECTS_DIR = config_dir() / "projects"


def _path_to_project_slug(path: str) -> str:
    """Map an absolute path to Claude Code's project-directory slug.

    Claude Code names each project dir under ~/.claude/projects/ by taking the
    session's cwd and replacing every '/' and '.' with '-'. Verified against real
    dirs: /home/<user>/repo -> -home-<user>-repo;
    /home/<user>/repo/.claude/worktrees/b -> -home-<user>-repo--claude-worktrees-b.
    """
    return re.sub(r"[/.]", "-", path)


def _repo_scoped_project_slugs(command_label: str = "skill-invocation") -> list[str]:
    """Exact project-dir slugs for this repo's own worktrees (main + linked).

    This is the minimization control for any caller (`command_label`) that must
    scope a transcript read to this repo: output built from it is routinely
    quoted into public PR descriptions, and transcripts under
    ~/.claude/projects/ span every project on the machine. Scoping the read to
    this repository — by *identity*, via `git worktree list`, matched as exact dir
    names — guarantees no other project's skill names can enter the output. It is
    deliberately not a path glob: a `<slug>*`-style match scopes by where a dir
    sits in the path string, which a foreign repo cloned under this repo's
    worktrees/, a sibling `<repo>-fork`, or a lossy-slug collision all defeat. A
    session started in a repo *subdirectory* is a known exception this exactness
    does not cover: its project dir is slugged from that subdirectory path and
    is string-unequal to every worktree-root slug, so it is silently excluded —
    the documented prefix-glob fallback covers that case instead.

    Fails closed (SystemExit) rather than returning a machine-wide scope whenever
    the environment is not a recognizable git worktree of this repo — a silent
    fallback to "*" would reintroduce the cross-project read this exists to
    prevent.
    """
    try:
        # timeout guards the fail-closed posture: a hung local git (stale lock,
        # network-mounted .git) would otherwise block the whole CLI with no exit.
        # 10s is generous for a local `worktree list` (no network/credential work)
        # while still bounding a wedged invocation.
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(
            f"{command_label}: cannot determine repo scope (git worktree list failed: {exc}); "
            "refusing machine-wide scope",
            file=sys.stderr,
        )
        sys.exit(1)

    prefix = "worktree "
    worktree_paths = [
        line[len(prefix):] for line in proc.stdout.splitlines() if line.startswith(prefix)
    ]
    if not worktree_paths:
        print(
            f"{command_label}: git listed no worktrees; refusing machine-wide scope",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_worktrees = [Path(p).resolve() for p in worktree_paths]

    # Containment guard: cwd must sit at or under one of the resolved worktree
    # roots. Path-segment based, not str.startswith — a sibling sharing a bare
    # string prefix (<repo>-fork) must not be wrongly accepted as "within".
    cwd = Path(os.getcwd()).resolve()
    if not any(cwd == wt or wt in cwd.parents for wt in resolved_worktrees):
        print(
            f"{command_label}: working directory is not within the resolved repo worktrees; "
            "refusing to emit a scope that may not be this repo's",
            file=sys.stderr,
        )
        sys.exit(1)

    # Identity guard: containment alone is not posture-neutral — it would accept
    # a cwd whose own repo resolves elsewhere under an environment override (e.g.
    # GIT_WORK_TREE decoupling toplevel reporting from the worktree-list
    # enumeration) but which happens to sit under one of the paths above.
    # Confirm cwd's own git root is genuinely one of the worktrees: containment
    # then governs *where under the root* the caller stands, this governs
    # *which root* was resolved.
    try:
        # Same local-git timeout rationale as the `worktree list` call above:
        # no network/credential work, so 10s only bounds a wedged invocation.
        toplevel_proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(
            f"{command_label}: cannot determine repo identity (git rev-parse failed: {exc}); "
            "refusing machine-wide scope",
            file=sys.stderr,
        )
        sys.exit(1)

    cwd_repo_root = Path(toplevel_proc.stdout.strip()).resolve()
    if cwd_repo_root not in resolved_worktrees:
        print(
            f"{command_label}: working directory's repo root is not among the resolved worktrees; "
            "refusing to emit a scope that may not be this repo's",
            file=sys.stderr,
        )
        sys.exit(1)

    return [_path_to_project_slug(p) for p in worktree_paths]


def _dedup_new_project_dirs(candidates: Iterable[Path], visited_dirs: set[Path]) -> Iterator[Path]:
    """Yield each directory in `candidates` at most once, keyed on resolved
    real path, recording it into `visited_dirs` (mutated in place) as it's
    yielded.

    Shared across every multi-root project-dir scan in this file
    (`_iter_scoped_sessions`, `_iter_glob_scoped_sessions`,
    `_scan_root_transcripts`) — extracted after a cumulative review found
    three near-identical copies of this same five-line pattern. Pass one
    `visited_dirs` set across an entire multi-root loop (not a fresh one per
    root) so a project dir aliased, by symlink, to one already yielded from a
    prior root is caught too — not just two roots resolving to the same
    directory.
    """
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved_dir = candidate.resolve()
        if resolved_dir in visited_dirs:
            continue
        visited_dirs.add(resolved_dir)
        yield candidate


def _redaction_ordinals(roots: Sequence[Path]) -> dict[Path, int]:
    """Assign each root a stable 1-based ordinal ("account-N"), sorted by
    resolved path once here rather than by each caller's own list order.

    resolve_scan_roots' scan order puts the active profile first, so a
    position-based ordinal (each caller enumerating `roots` itself) would
    renumber every other declared root depending on which profile produced
    the report. Sorting once, here, and having every ordinal-assigning site
    -- redaction.py's _build_redact_map, cost's per-row redact key, and its
    --by-project account column -- look up the same dict keeps the same
    physical root at the same account-N regardless of scan order, and keeps
    all three sites from independently deriving (and risking desyncing) the
    same number. Lives in scope.py, not redaction.py, despite being
    redaction-adjacent: every other caller (_iter_scoped_sessions,
    _iter_glob_scoped_sessions) is a plain root-scan diagnostic with no
    redaction involved, and redaction.py already needs scope.PROJECTS_DIR, so
    keeping this one-directional (redaction.py -> scope.py) avoids a circular
    import between the two.
    """
    resolved = sorted({root.resolve() for root in roots})
    return {resolved_root: ordinal for ordinal, resolved_root in enumerate(resolved, start=1)}


def _iter_scoped_sessions(
    slugs: list[str], include_subagents: bool, roots: Sequence[Path] | None = None
):
    """Yield sessions from an explicit set of exact project-dir slugs.

    Matching is by identity, not location: enumerate the directory names under
    each root in `roots` (default: PROJECTS_DIR alone, so every caller other
    than cost's --config-dir is unaffected) and keep only those whose name is
    string-equal to one of the scoped slugs. This deliberately does NOT route
    the slug through Path.glob — a slug containing a glob metacharacter (a
    `*`/`?`/`[` in the machine's home or username path) would otherwise be
    interpreted as a wildcard and could widen the match beyond this repo's own
    worktrees. Visiting each directory at most once (by resolved real path,
    spanning every root — covers a root nested inside another, not just two
    identical roots) also makes double-counting impossible.

    A root that raises OSError while being listed (an unreadable directory,
    not merely a missing one — `root.is_dir()` above only requires the
    *parent* to be searchable) is reported to stderr and skipped, not
    propagated: this function has no `redact` parameter of its own (unlike
    cost's per-root diagnostic), so the reported detail always omits the raw
    path — an OSError's `strerror` (e.g. "Permission denied"), never
    `str(exc)`, which embeds the offending path.
    """
    if roots is None:
        roots = (PROJECTS_DIR,)
    wanted = set(slugs)
    visited_dirs: set[Path] = set()
    multi_root = len(roots) > 1
    # Ordinal, not scan-order index: the same physical root must read as the
    # same account-N here as in every other multi-root diagnostic in this
    # file (_cost_report's per-root summary, --by-project's account column).
    ordinals = _redaction_ordinals(roots) if multi_root else {}
    for root in roots:
        if multi_root:
            # No path in this line: neither function knows whether its caller
            # is a --redact context (cost's own per-root diagnostic handles
            # that separately) -- an unconditional raw config-dir path here
            # would leak account/client identity a redacted run must not print.
            print(f"scanning root {ordinals[root.resolve()]}/{len(roots)}...", file=sys.stderr)
        if not root.is_dir():
            continue
        try:
            candidates = [p for p in sorted(root.iterdir()) if p.name in wanted]
        except OSError as exc:
            root_label = f"account-{ordinals[root.resolve()]}" if multi_root else "the scope root"
            print(
                f"_iter_scoped_sessions: cannot scan {root_label}"
                f" ({exc.strerror or 'permission denied'}) — skipping",
                file=sys.stderr,
            )
            continue
        for project_dir in _dedup_new_project_dirs(candidates, visited_dirs):
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                records = read_session_file(jsonl, include_subagents)
                if records:
                    yield jsonl, records


def _iter_glob_scoped_sessions(
    roots: Sequence[Path], projects_glob: str, include_subagents: bool
) -> Iterator[tuple[Path, list[dict]]]:
    """Chain iter_sessions' glob match across more than one root.

    Only used when len(roots) > 1 (cost's --config-dir); the single-root case
    keeps calling iter_sessions directly so its documented flat-sort-over-
    full-paths ordering guarantee is untouched for every other caller. Dedups
    matched project directories by resolved real path, spanning all roots —
    covers a --config-dir root nested inside another root's tree, not just
    two roots that resolve to the same directory (already deduped earlier at
    the CLI boundary).
    """
    visited_dirs: set[Path] = set()
    multi_root = len(roots) > 1
    # Ordinal, not scan-order index: the same physical root must read as the
    # same account-N here as in every other multi-root diagnostic in this
    # file (_cost_report's per-root summary, --by-project's account column).
    ordinals = _redaction_ordinals(roots) if multi_root else {}
    for root in roots:
        if multi_root:
            # No path in this line: neither function knows whether its caller
            # is a --redact context (cost's own per-root diagnostic handles
            # that separately) -- an unconditional raw config-dir path here
            # would leak account/client identity a redacted run must not print.
            print(f"scanning root {ordinals[root.resolve()]}/{len(roots)}...", file=sys.stderr)
        for project_dir in _dedup_new_project_dirs(sorted(root.glob(projects_glob)), visited_dirs):
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                records = read_session_file(jsonl, include_subagents)
                if records:
                    yield jsonl, records


def resolve_scan_roots(parsed: argparse.Namespace) -> list[Path]:
    """Resolve one invocation's scan roots -- the single funnel every
    _resolve_project_scope caller threads `roots` through, replacing the
    default single-root PROJECTS_DIR scope everywhere except cost and
    context-distribution's own --config-dir extras (_resolve_cost_roots).

    An explicit top-level --config-dir overrides everything else, returning
    that one directory's projects/ subdirectory alone. Absent that, the base
    is PROJECTS_DIR (this module's own global -- still config_dir()/"projects"
    at import, still reassignable via monkeypatch.setattr(scope, "PROJECTS_DIR",
    ...)) plus each of declared_transcript_roots()'s own projects/
    subdirectory, deduped by resolved real path. PROJECTS_DIR is listed
    first, so the active profile is always scanned first -- the "active
    profile first" convention analyze-context.py's session lookup also
    relies on.

    config_dir is read via getattr, not attribute access, matching
    _resolve_project_scope's own rationale below: this file's many
    hand-built test `args` fixtures predate the top-level --config-dir flag,
    so its absence means "not passed," not a wiring bug.
    """
    config_dir_arg = getattr(parsed, "config_dir", None)
    if config_dir_arg:
        return [Path(config_dir_arg) / "projects"]

    roots = [PROJECTS_DIR]
    seen_resolved = {PROJECTS_DIR.resolve()}
    for declared_root in declared_transcript_roots():
        candidate = declared_root / "projects"
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        roots.append(candidate)
    return roots


def _resolve_project_scope(
    args: argparse.Namespace,
    subcommand: str,
    include_subagents: bool = False,
    roots: Sequence[Path] | None = None,
) -> tuple[Iterator[tuple[Path, list[dict]]], str]:
    """Resolve --projects/--this-repo into a fresh session iterator and a scope label.

    A plain function, not a generator: a generator would defer
    _repo_scoped_project_slugs' fail-closed sys.exit(1) to the caller's first
    next() instead of raising at scope-resolution time, unlike every other
    scope decision in this file. Reads args.this_repo unguarded so a subparser
    wired without _add_project_scope_args raises AttributeError rather than
    silently falling through to machine-wide scope. `subcommand` is the CLI
    subcommand name (e.g. "buckets"); it labels _repo_scoped_project_slugs'
    fail-closed messages and, uppercased, the caller's resolved-scope header.
    The resolved slug list is cached on `args` so a caller needing two
    independent iterators over the same scope triggers one `git worktree
    list` call, not two — no current caller does this, but the cache is
    correct if one starts to. This caching relies on main()'s
    single-Namespace-per-process, single-subcommand-dispatch invariant — an
    `args` object reused across two different subcommand invocations would
    silently reuse the first's resolved slugs instead of re-resolving for the
    second.

    `roots` defaults to (PROJECTS_DIR,) when a caller passes none, but every
    cmd_* entry point in this file now threads its own resolve_scan_roots(args)
    result through (cost, context-distribution, edit-format, subagents, and
    subagent-mix instead thread their own _resolve_cost_roots(args) result,
    which unions the same declared_transcript_roots() plus their own
    repeatable --config-dir extras), so the default only ever fires for a
    caller that predates that threading (this module's own single-root test
    fixtures). --this-repo and multi-root are no longer mutually exclusive: a
    populated ~/.claude/transcript-config-dirs makes --this-repo multi-root by
    default with no --config-dir flag at all, so the this_repo branch below
    unions across every root in `roots` via _iter_scoped_sessions' own
    basename match.

    Under an explicit top-level --config-dir (a different flag from cost's
    and context-distribution's own --config-dir above — see main()), zero of
    the resolved slugs matching an actual directory under ANY resolved root
    fails closed (sys.exit(1)) instead of returning an iterator that silently
    yields nothing — this is the original reported symptom (declaring no
    sessions exist for a container that has them), so an empty --this-repo
    scope here is far more likely a wrong --config-dir than a genuinely
    session-less repo. Without --config-dir, an empty scope stays silent,
    matching every other subcommand's long-standing behavior. config_dir is
    read via getattr (unlike this_repo above): it's a top-level parser flag
    rather than something _add_project_scope_args wires per subparser, and
    this file's many hand-built test `args` fixtures predate it, so its
    absence means "not passed" (the real default), not a wiring bug. Under
    resolve_scan_roots' own precedence (an explicit top-level --config-dir
    overrides declared roots entirely), that flag makes `roots` a
    single-element list equal to the reassigned PROJECTS_DIR, so this check
    checking every root in `roots` rather than PROJECTS_DIR alone is a
    robustness improvement against a future precedence change, not a fix for
    a divergence reachable today.
    """
    if roots is None:
        roots = (PROJECTS_DIR,)
    if args.this_repo:
        slugs = getattr(args, "_this_repo_slugs", None)
        if slugs is None:
            slugs = _repo_scoped_project_slugs(subcommand)
            args._this_repo_slugs = slugs
        config_dir_arg = getattr(args, "config_dir", None)
        if config_dir_arg and not any((root / slug).is_dir() for root in roots for slug in slugs):
            print(
                f"{subcommand}: --this-repo matched zero project directories under "
                f"--config-dir {config_dir_arg} (checked {len(slugs)} candidate worktree "
                "slug(s)) — refusing to report an empty scope silently; confirm "
                "--config-dir points at the account these sessions actually live in.",
                file=sys.stderr,
            )
            sys.exit(1)
        return (
            _iter_scoped_sessions(slugs, include_subagents, roots=roots),
            f"this repo ({len(slugs)} project dirs)",
        )
    glob = getattr(args, "projects", None) or "*"
    if len(roots) == 1:
        return iter_sessions(roots[0], glob, include_subagents=include_subagents), glob
    return _iter_glob_scoped_sessions(roots, glob, include_subagents), glob


def _root_count_desc(roots: Sequence[Path]) -> str:
    """Render the root-count clause of the resolved-scope header (e.g. "1 root
    (no ~/.claude/transcript-config-dirs declared)" or "3 roots").

    At one root, the wording branches on declared_roots_file_state(): "absent"
    is the only state where "no ... declared" is accurate. "unreadable" (a
    permissions problem, not a missing file) gets its own wording rather than
    folding into "present" -- collapsing the two would claim a failed read
    "declared" a root, masking exactly the problem the tri-state exists to
    surface. "present" (the file contributed a root, or contributed nothing
    because every entry failed validation) says "declared but contributed no
    additional root" -- claiming "no ... declared" about a file that exists
    would misrepresent it. The >1-root branch never names the file: extra
    roots may come from --config-dir, not only a declared-roots file.
    """
    if len(roots) != 1:
        return f"{len(roots)} roots"
    state = declared_roots_file_state()
    if state == "absent":
        return f"1 root (no {TRANSCRIPT_CONFIG_DIRS_LABEL} declared)"
    if state == "unreadable":
        return f"1 root ({TRANSCRIPT_CONFIG_DIRS_LABEL} present but unreadable)"
    return f"1 root ({TRANSCRIPT_CONFIG_DIRS_LABEL} declared but contributed no additional root)"


def _resolved_scope_header(subcommand: str, scope_label: str, roots: Sequence[Path]) -> str:
    """Build the one-line resolved-scope header text, shared by print_resolved_scope
    and any caller (e.g. judgment-pair's --out file) that needs the header written
    somewhere other than a live print call.

    `roots` is required, not defaulted: a call site that forgets to pass it is a
    TypeError at implementation and test time, not a silently-wrong "1 root" line
    printed while the subcommand actually scanned N. The root count is stated
    unconditionally -- even at one root -- so the header discloses scope in the
    exact zero-declared-roots state that produced the corpus-undercount this
    exists to prevent, not only once an operator has already declared a second
    account. A root skipped by index (a stale declared-roots line) is not named
    here; that detail is a separate stderr warning from declared_transcript_roots()
    itself, keeping this return value the single line judgment-pair's --out file
    contract requires.
    """
    return f"{subcommand.upper().replace('-', ' ')} SOURCES ({scope_label}; {_root_count_desc(roots)})"


def print_resolved_scope(subcommand: str, scope_label: str, roots: Sequence[Path], *, file=None) -> None:
    """Print the one-line resolved-scope header cmd_skill_invocation already uses,
    so machine-wide vs. --this-repo output is never scope-ambiguous. `file` defaults
    to stdout (resolved at call time, not import time — a `sys.stdout` default
    value would bind the stream object process startup captured, bypassing test
    capture and any later reassignment); audit-routing-samples routes it to stderr
    instead, since its stdout is a JSON (or curation-markdown) data stream a header
    line would corrupt."""
    print(_resolved_scope_header(subcommand, scope_label, roots), file=file or sys.stdout)


_DO_NOT_PUBLISH_BANNER = (
    "DO NOT PUBLISH — this output contains real project names and session IDs."
)

# Subcommands that resolve their own multi-root scan via their own
# subcommand-level --config-dir (_resolve_cost_roots) instead of the
# top-level --config-dir main() reassigns PROJECTS_DIR from — main() refuses
# the top-level flag outright for each of these, so the two same-named flags
# can never validate against two different accounts.
_SUBCOMMANDS_WITH_OWN_CONFIG_DIR = (
    "cost", "context-distribution", "context-composition", "edit-format", "read-scope",
    "cache-efficiency",
    "subagents", "subagent-mix", "cost-trend", "cache-rebuild", "plan-boundary",
    "instrument-authoring",
)


def _resolve_cost_roots(args: argparse.Namespace, subcommand: str = "cost") -> list[Path]:
    """Assemble a subcommand's scan roots from the default config dir, every
    declared_transcript_roots() entry, and any --config-dir extras, in that
    order, deduped by resolved path.

    Mirrors post-crash-sessions.py:1067-1111's --config-dir contract: exit 2
    on a --config-dir extra that is not a directory or lacks a projects/
    subdirectory. A declared_transcript_roots() entry never exits 2 on the
    same defect -- that function already validated and skipped it (see its
    own docstring) rather than exiting, since a stale roots-file line must not
    break every invocation the way a bad command-line flag should.
    --this-repo unions across every resolved root here, including
    --config-dir extras: _iter_scoped_sessions matches by basename, and
    _path_to_project_slug derives slugs from `git worktree list` alone, so
    one checkout's slugs are root-independent. --no-redact on more than one
    root would put one client's real project names into a report meant for
    another — refused here, exit 2, rather than silently scoping to the
    wrong thing. Returns each root's projects/ subdirectory, ready for
    _resolve_project_scope's roots parameter.

    `subcommand` labels the printed refusal messages (default "cost", cost's
    own long-standing call sites and tests); context-distribution passes its
    own name so a refusal is attributed to the subcommand the caller actually
    invoked, not always "cost".

    When `subcommand == "cost"` and args.summary is set, this returns
    [config_dir() / "projects"] alone, skipping the declared-roots union
    entirely -- --summary is a single-account, aggregate-only mode, and
    unioning declared_transcript_roots() here would publish another
    account's spend inside a PR authored under this one. Gated on
    `subcommand` too, not just the flag: this function is shared by every
    entry in _SUBCOMMANDS_WITH_OWN_CONFIG_DIR, and only cost's argparser
    defines --summary today, so a bare summary check would silently narrow
    a future subcommand that happens to add a same-named flag.
    """
    if subcommand == "cost" and bool(getattr(args, "summary", False)):
        return [config_dir() / "projects"]

    extra_config_dirs: list[str] = getattr(args, "extra_config_dirs", None) or []

    config_dirs: list[Path] = []
    seen_resolved: set[Path] = set()
    for candidate in (config_dir(), *declared_transcript_roots()):
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        config_dirs.append(candidate)
    for raw in extra_config_dirs:
        candidate = Path(raw)
        if not candidate.is_dir():
            print(f"{subcommand}: --config-dir {raw!r} is not a directory", file=sys.stderr)
            sys.exit(2)
        if not (candidate / "projects").is_dir():
            print(
                f"{subcommand}: --config-dir {raw!r} rejected: no projects/ subdirectory found",
                file=sys.stderr,
            )
            sys.exit(2)
        resolved = candidate.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        config_dirs.append(candidate)

    if len(config_dirs) > 1 and getattr(args, "no_redact", False):
        print(
            f"{subcommand}: --no-redact is refused when more than one root is in scope"
            " (--config-dir was given); drop --no-redact or scope to a single profile",
            file=sys.stderr,
        )
        sys.exit(2)

    return [d / "projects" for d in config_dirs]


def _scan_root_transcripts(root: Path, projects_glob: str, slugs: Sequence[str] | None = None) -> tuple[int, int]:
    """Count transcripts found vs. unreadable directly under one cost scan root.

    A dedicated readability probe (open, discard) rather than routing through
    read_session_file, which swallows OSError into an empty record list
    indistinguishable from a genuinely empty transcript — cmd_cost's per-root
    summary line needs the two counted separately.

    Raises PermissionError when `root` itself is not readable/searchable.
    This is an explicit os.access probe, not reliance on Path.glob to raise —
    verified empirically that glob silently swallows OSError while walking an
    unreadable directory and returns no matches rather than propagating, so a
    permission-restricted root would otherwise misreport as "0 transcripts
    found," the same shape as a genuinely empty scope. An unreadable project
    subdirectory nested under an otherwise-readable root is still silently
    absorbed into the transcript count (glob's real behavior) — this probe
    only covers the root itself, the realistic misconfigured-`--config-dir`
    case. Callers scanning multiple untrusted roots catch this per root so
    one bad root doesn't abort the whole report.

    `slugs`, when given, restricts the scan to those exact project-dir names
    (mirroring _iter_scoped_sessions' identity match, not a glob) instead of
    `projects_glob` — required for --this-repo, whose _projects_glob(args) is
    always "*" regardless of scope. Without this, the diagnostic line would
    report the whole config dir's transcript count under --this-repo, masking
    a genuinely-empty repo-scoped result behind an unrelated nonzero total —
    the exact silent-zero failure Step 8 exists to surface. Both branches dedup
    matched project dirs by resolved real path via _dedup_new_project_dirs, so
    a project dir aliased to another (by symlink, whether reached by slug or
    by glob) doesn't double-count in this diagnostic either.
    """
    if not os.access(root, os.R_OK | os.X_OK):
        raise PermissionError(errno.EACCES, "Permission denied", str(root))
    visited_dirs: set[Path] = set()
    candidates = (root / slug for slug in slugs) if slugs is not None else sorted(root.glob(projects_glob))
    jsonl_paths = [
        jsonl
        for proj_dir in _dedup_new_project_dirs(candidates, visited_dirs)
        for jsonl in proj_dir.glob("*.jsonl")
    ]
    skipped = 0
    for jsonl in jsonl_paths:
        try:
            with open(jsonl):
                pass
        except OSError:
            skipped += 1
    return len(jsonl_paths), skipped


def _root_index_for_path(jsonl: Path, resolved_roots: Sequence[Path]) -> int:
    """Return the 0-based index of the root under which jsonl was found.

    `resolved_roots` must already be resolved (real, symlink-free) paths —
    callers resolve the roots list once, outside cost's per-session loop,
    since this runs once per session and re-resolving every root on every
    call would be a per-element filesystem stat inside that loop.
    `jsonl` is always a file path, so a root can only ever be one of its
    ancestors, never equal to it — `resolved.parents` alone covers every case.

    Returns the FIRST matching root by list order when one declared root is a
    genuine filesystem descendant of another (not just a symlink alias back
    to it, which the dedup guards upstream already collapse to one root's
    worth of sessions) — attributing every session under the nested root to
    whichever sorts earlier, always the default config dir. Untested and
    low-realism given this repo's own per-account layout (sibling
    directories under `~/.config/claude-accounts/`, never nested); revisit if
    `--config-dir` is ever pointed at a directory nested inside another
    declared root.
    """
    resolved = jsonl.resolve()
    for idx, resolved_root in enumerate(resolved_roots):
        if resolved_root in resolved.parents:
            return idx
    # Deliberately omits the raw jsonl path: main() has no top-level exception
    # handler, so this message would otherwise reach stderr uncaught — the
    # same reasoning and fix already applied to the redact-map-miss assertion
    # a few lines below in the same loop (structural sibling, audited after a
    # cumulative-diff review caught the fix hadn't been applied here too).
    path_hash = hashlib.sha256(str(jsonl).encode()).hexdigest()[:12]
    raise AssertionError(
        f"cost: a session path (hash {path_hash}) matched no known scan root — roots list is"
        " out of sync with the session iterator (a symlinked project dir resolving outside"
        " every declared root is one way this can happen)"
    )
