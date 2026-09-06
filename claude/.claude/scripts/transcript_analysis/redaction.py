"""Project-label pseudonymization -- the redact map, corpus fingerprint, and
session/branch/subagent-type label assignment. No dependency on any cmd_*
subcommand.

Reads scope.PROJECTS_DIR and scope._redaction_ordinals by attribute access on
the imported `scope` module, never by name-import -- see scope.py's own
top-of-file comment for why.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from transcript_analysis import scope
from transcript_analysis.corpus import iter_sessions
from transcript_analysis.render import _sanitize_table_cell


def _derive_proj_label(jsonl: Path) -> str:
    """Derive a short project label from a jsonl path, matching token-analyzer.py's convention."""
    return jsonl.parent.name.lstrip("-").replace("-", "/", 2).split("/", 2)[-1]


# iter_sessions documents this repo's own worktree naming: a linked worktree's
# project-dir slug is the main dir's slug with --claude-worktrees-<branch>
# appended, which _derive_proj_label carries through unchanged onto its output.
_WORKTREE_SUFFIX_RE = re.compile(r"--claude-worktrees-.+$")


def _project_family(raw_proj_label: str) -> str:
    """Collapse a _derive_proj_label output to its base-repo "family" key.

    One repo's main checkout and every linked worktree derive to distinct
    labels (repo, repo--claude-worktrees-branch-a, ...) that would otherwise
    fragment --by-project's per-project rows across branches of the same repo.

    Matches on the literal substring alone — a project whose own name happens
    to contain "--claude-worktrees-" would have that trailing portion
    stripped and merged into a false family. Below current scale to guard
    against; re-evaluate if --by-project output ever shows an unexpected
    merge.
    """
    return _WORKTREE_SUFFIX_RE.sub("", raw_proj_label)


_REDACT_MAP_MISS_TOKEN = "private-project-unmapped"

# A cost redact-map key is either a plain raw label (single-root reports,
# audit-routing) or a (root_index, raw_label) pair (cost's multi-root
# --config-dir reports) — see _build_redact_map.
_RedactMapKey = str | tuple[int, str]


def _redact_proj_label(proj_label: _RedactMapKey, redact_map: dict[_RedactMapKey, str]) -> str:
    """Apply the redact map to a project label, preserving 'claude-config' as-is.

    proj_label may be a (root_index, raw_label) pair for a multi-root cost
    report (see _build_redact_map); claude-config still passes through
    unredacted regardless of which root it was found under.

    A map miss returns a fixed opaque token rather than the raw label — the
    map is only ever built from a full-corpus scan (_build_redact_map), so a
    miss means the caller passed an incomplete map, and falling back to the
    raw name would silently defeat --redact.
    """
    raw_label = proj_label[1] if isinstance(proj_label, tuple) else proj_label
    if raw_label == "claude-config":
        return raw_label
    return redact_map.get(proj_label, _REDACT_MAP_MISS_TOKEN)


def _sorted_distinct_proj_labels(root: Path) -> list[str]:
    """Distinct project labels found under one root, sorted for deterministic
    ordinal assignment — the per-root scan _build_redact_map shares across its
    single- and multi-root branches.

    Scans via iter_sessions(root, "*"), ignoring any caller's own --projects
    filter, so a project always binds to the same placeholder whether it was
    found by a narrowed cost run or a full audit-routing run — a narrower scan
    would let the same label mean two different projects across two published
    outputs. iter_sessions (not a raw glob) is used because it already
    excludes zero-record transcripts; a raw glob would not, and that
    difference would shift every subsequent private-project-N index.

    include_subagents=True so a project whose only priced content lives in a
    subagent transcript still gets a stable placeholder — cost's own session
    scan is itself subagent-inclusive, and a mismatch here is a redact-map
    miss on that project's session row.
    """
    labels: list[str] = []
    for jsonl, _records in iter_sessions(root, "*", include_subagents=True):
        label = _derive_proj_label(jsonl)
        if label not in labels:
            labels.append(label)
    labels.sort()
    return labels


def _build_redact_map(roots: Sequence[Path] | None = None) -> dict[_RedactMapKey, str]:
    """Build the project-label -> opaque-token map shared by every --redact caller.

    --since never reaches this map and must not: it would change which
    sessions are found on a per-run basis, shifting every subsequent
    private-project-N index between two runs of the same corpus.

    This means --redact reads every project's transcript bytes off disk even
    under --this-repo, a considered tradeoff in tension with that flag's
    minimization intent elsewhere in this file, not an oversight.

    Ordinals are assigned sequentially over the sorted full-corpus label list,
    not the caller's --this-repo-scoped subset, so a printed private-project-N
    number is shaped by every other private project directory that exists
    locally and sorts before the in-scope one — a structural fingerprint of
    the operator's other projects that a --this-repo-scoped report does not
    otherwise disclose. Narrowing the scan to the caller's own scope would
    close this but breaks the cross-run label-stability guarantee above, so
    this function does not attempt it.

    roots defaults to (scope.PROJECTS_DIR,) — a single root (the default, or
    any caller passing exactly one, e.g. cmd_audit_routing) gets the original
    flat private-project-N map, unnamespaced by account. More than one root
    namespaces every label account-<K>/private-project-N, where <K> is the
    root's ordinal from scope._redaction_ordinals (resolved-path-sorted,
    stable across which profile is active) — never the config-dir path or
    its basename, which would leak the account/client identifier the
    directory name encodes. <N> restarts at 1 within each account's own scan.
    Labels (and the corpus fingerprint derived from this map) are not
    comparable across two report runs built from different declared-roots
    files: a changed root set can renumber every ordinal. Two runs from the
    *same* declared-roots file, differing only in which profile was active,
    assign the same ordinal to the same physical root and so remain
    comparable.
    """
    if roots is None:
        roots = (scope.PROJECTS_DIR,)

    redact_map: dict[_RedactMapKey, str] = {}

    if len(roots) <= 1:
        root = roots[0] if roots else scope.PROJECTS_DIR
        num_index = 1
        for label in _sorted_distinct_proj_labels(root):
            if label == "claude-config":
                redact_map[label] = label
            else:
                redact_map[label] = f"private-project-{num_index}"
                num_index += 1
        return redact_map

    ordinals = scope._redaction_ordinals(roots)
    for root in roots:
        ordinal = ordinals[root.resolve()]
        account_label = f"account-{ordinal}"
        num_index = 1
        for label in _sorted_distinct_proj_labels(root):
            key = (ordinal, label)
            if label == "claude-config":
                redact_map[key] = label
            else:
                redact_map[key] = f"{account_label}/private-project-{num_index}"
                num_index += 1
    return redact_map


def _corpus_fingerprint(redact_map: dict[_RedactMapKey, str]) -> str:
    """Short sha256 prefix of the sorted raw project-label set a redact map was
    built from — a same-corpus indicator only, not a security boundary (see
    _build_redact_map). Two report runs share a fingerprint only when their
    underlying project-label sets are identical; a differing fingerprint means
    ordinals are not comparable between them.
    """
    raw_labels = {key[1] if isinstance(key, tuple) else key for key in redact_map}
    return hashlib.sha256("\n".join(sorted(raw_labels)).encode()).hexdigest()[:12]


_REDACT_SESSION_MISS_TOKEN = "session-unmapped"


def _assign_session_redact_label(session_id: str, session_redact_map: dict[str, str]) -> None:
    """Assign session_id a stable opaque label the first time it's seen this run.

    Unlike project labels, session-id placeholders need no cross-run or
    cross-command stability — each command's own single pass over its corpus
    is the map's only writer, so assignment happens inline as sessions are
    discovered rather than needing a separate first pass.
    """
    if session_id not in session_redact_map:
        session_redact_map[session_id] = f"session-{len(session_redact_map) + 1}"


def _redact_session_id(session_id: str, session_redact_map: dict[str, str]) -> str:
    """Apply a run-scoped session-id redact map; fails closed to a fixed token on a miss."""
    return session_redact_map.get(session_id, _REDACT_SESSION_MISS_TOKEN)


def _assign_root_scoped_redact_label(
    kind: str, ordinal: int, value: str, redact_map: dict[tuple[int, str], str]
) -> str:
    """Assign one (root, value) pair a stable, account-namespaced opaque
    label the first time it's seen this run, and return it.

    `ordinal` must be looked up from scope._redaction_ordinals(roots), not a
    raw scan-order position (scope._root_index_for_path) — scan order puts
    the active profile first, so a position-based number would renumber the
    same physical account depending on which profile produced the report,
    the exact desync class scope._redaction_ordinals exists to prevent
    everywhere else in this file (_build_redact_map, cost's per-row key, its
    --by-project column). Generic across every value kind that needs this
    exact shape of redaction: neither _redact_proj_label nor
    _assign_session_redact_label covers gitBranch or subagent_type, and
    subagents'/subagent-mix's --config-dir multi-root reports need their own
    primitive so two accounts' identically-named value (e.g. both branch
    "main", or both subagent_type "staff-sdet") never collapse into one
    label or leak a raw value. Namespaced by account (account-<K>/<kind>-<N>,
    N restarting at 1 per account, tracked in `redact_map` which is scoped to
    one `kind` per caller so branch and subagent_type numbering never share a
    counter) rather than a single flat counter, mirroring _build_redact_map's
    account-<K>/private-project-N convention. Like _assign_session_redact_label,
    this label is stable only within one run, not across runs. subagent-mix's
    exact-cent Actual $/Counterfactual $ columns are a stronger cross-run
    correlation key against this label than the integer spawn counts already
    printed alongside it, though only within an already-DO_NOT_PUBLISH report.
    """
    key = (ordinal, value)
    if key not in redact_map:
        n = sum(1 for k in redact_map if k[0] == ordinal) + 1
        redact_map[key] = f"account-{ordinal}/{kind}-{n}"
    return redact_map[key]


def _root_scoped_display_label(
    kind: str, ordinal: int, value: str, redact_map: dict[tuple[int, str], str], *, disclose: bool
) -> str:
    """Return either a disclosed raw label or a redacted one for one
    (root, value) pair, sharing the account-<K>/ namespace format both paths
    use.

    disclose=True skips writing to redact_map so it can't inflate later
    placeholder numbers; disclose=False delegates entirely to
    _assign_root_scoped_redact_label.
    """
    if disclose:
        return f"account-{ordinal}/{_sanitize_table_cell(value)}"
    return _assign_root_scoped_redact_label(kind, ordinal, value, redact_map)
