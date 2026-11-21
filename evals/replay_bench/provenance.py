"""The git state of the checkout the bench's own code runs from, recorded in a campaign's manifest and run records.

Nothing here halts a run: a probe that fails records None.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from review_bench import runner as review_runner
from review_bench.local_git import isolated_git_environment
from review_bench.runner import HarnessInvalidatedError

MANIFEST_COMMIT_KEY = "runner_commit"
MANIFEST_DIRTY_KEY = "runner_dirty"
# Both bench packages live under this top-level directory of the runner's checkout.
RUNNER_SOURCE_DIR = "evals"
# A full SHA-1 or SHA-256 object name.
_OBJECT_NAME = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def is_object_name(value: object) -> bool:
    return isinstance(value, str) and _OBJECT_NAME.fullmatch(value) is not None


def is_valid_commit_field(value: object) -> bool:
    """A stored `runner_commit`: a full hex object name, or None for an unreadable probe."""
    return value is None or is_object_name(value)


def is_valid_dirty_field(value: object) -> bool:
    """A stored `runner_dirty`: a bool, or None for an unreadable probe."""
    return value is None or isinstance(value, bool)


@dataclass(frozen=True)
class RunnerProvenance:
    """`commit` is the runner checkout's HEAD. `has_uncommitted_changes` is True when `git status` lists a tracked
    change or an untracked, unignored file under `evals/`. Each is None when its probe failed."""

    commit: str | None
    has_uncommitted_changes: bool | None

    def manifest_fields(self) -> dict:
        return {MANIFEST_COMMIT_KEY: self.commit, MANIFEST_DIRTY_KEY: self.has_uncommitted_changes}

    @classmethod
    def from_manifest(cls, manifest: Mapping) -> RunnerProvenance | None:
        """None for a manifest written before these fields existed. A field with an invalid value reads as None,
        which `describe` words as unreadable."""
        if MANIFEST_COMMIT_KEY not in manifest or MANIFEST_DIRTY_KEY not in manifest:
            return None
        commit, is_dirty = manifest[MANIFEST_COMMIT_KEY], manifest[MANIFEST_DIRTY_KEY]
        return cls(commit if is_valid_commit_field(commit) else None, is_dirty if is_valid_dirty_field(is_dirty) else None)


def describe(provenance: RunnerProvenance | None) -> str:
    """The clause for a `campaign <id>` line; None means the campaign recorded no provenance."""
    if provenance is None:
        return "runner commit not recorded"
    if provenance.commit is None:
        if provenance.has_uncommitted_changes is None:
            return "runner commit unreadable"
        qualifier = "with" if provenance.has_uncommitted_changes else "without"
        return f"runner commit unreadable, {qualifier} uncommitted changes under {RUNNER_SOURCE_DIR}/"
    if provenance.has_uncommitted_changes is None:
        return f"runner commit {provenance.commit}, uncommitted changes under {RUNNER_SOURCE_DIR}/ unreadable"
    suffix = f", with uncommitted changes under {RUNNER_SOURCE_DIR}/" if provenance.has_uncommitted_changes else ""
    return f"runner commit {provenance.commit}{suffix}"


# A reading with neither field says nothing about the runner: git could not produce it, or a record predates the fields.
NO_READING = RunnerProvenance(None, None)


def describe_campaign(manifest_reading: RunnerProvenance | None, record_readings: Iterable[RunnerProvenance]) -> str:
    """The clause for a campaign's summary line. It names every distinct reading across the manifest and the
    records, because a resume at another commit builds later blocks with other runner code than the manifest names.
    Records with no reading are listed as their own entry once any other reading exists, since their blocks ran under
    runner code the line cannot name."""
    record_counts = Counter(record_readings)
    unread_records = record_counts.pop(NO_READING, 0)
    distinct = set(record_counts)
    if manifest_reading not in (None, NO_READING):
        distinct.add(manifest_reading)
    if not distinct:
        return describe(manifest_reading)
    if len(distinct) == 1 and not unread_records:
        return describe(next(iter(distinct)))
    clauses = []
    for reading in distinct:
        sources = ["manifest"] if reading == manifest_reading else []
        if record_counts[reading]:
            sources.append(f"records: {record_counts[reading]}")
        clauses.append(f"{describe(reading)} ({', '.join(sources)})")
    if unread_records:
        clauses.append(f"no runner reading (records: {unread_records})")
    return f"runner readings differ across the campaign: {'; '.join(sorted(clauses))}"


def describe_resume(manifest_reading: RunnerProvenance | None, current_reading: RunnerProvenance) -> str:
    """The clause for a resume's `campaign <id>` line: the pinned reading, and this checkout's when it differs."""
    if manifest_reading is None or manifest_reading == current_reading:
        return describe(manifest_reading)
    return f"{describe(manifest_reading)} at the first start; this resume runs {describe(current_reading)}"


def read_runner_provenance(repo_root: Path | None = None) -> RunnerProvenance:
    """`repo_root` defaults to the review runner's own `REPO_ROOT`, the checkout this code was imported from, which
    is not the config checkout that `read_environment_record` probes."""
    root = review_runner.REPO_ROOT if repo_root is None else repo_root
    # A `GIT_DIR` or `GIT_WORK_TREE` in the caller's environment would point git at another repository.
    git_environment = isolated_git_environment()
    # An unreadable reading carries no cause, and includes a checkout that git refuses to read, such as one owned by another user.
    try:
        commit = review_runner._read_environment_field(
            ["git", "rev-parse", "HEAD"], field="runner commit", cwd=root, env=git_environment,
        )
    except HarnessInvalidatedError:
        commit = None
    try:
        # The git(1) manual documents `--no-optional-locks` as skipping optional operations that need locks.
        # `--untracked-files=all` overrides the checkout's own `status.showUntrackedFiles` (git-config(1)).
        status = review_runner._read_environment_field(
            [
                "git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", "--",
                RUNNER_SOURCE_DIR,
            ],
            field="runner status", cwd=root, env=git_environment,
        )
        has_uncommitted_changes: bool | None = bool(status)
    except HarnessInvalidatedError:
        has_uncommitted_changes = None
    return RunnerProvenance(commit if is_object_name(commit) else None, has_uncommitted_changes)
