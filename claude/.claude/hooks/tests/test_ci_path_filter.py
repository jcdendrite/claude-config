"""CI path-filter coverage: SKIP_REGEX stays a narrow, bounded deny-list.

The `detect` step in `.github/workflows/tests.yml` inverted from an
allow-list (`REGEX`, silently `changed=false` on any unnamed path — fail-open)
to a deny-list (`SKIP_REGEX`, `changed=true` unless every changed path
matches). `TestSkipRegexBounded` proves the pattern itself stays narrow.
`TestDetectStepFailOpenSetsChanged` proves the surrounding quantifier is
correct by executing the step's real shell body — a pattern-only check
cannot see a slip in "unless every changed path matches" (e.g. an any-match
instead of all-match implementation), which would silently reintroduce
fail-open skipping on a real mixed diff while every pattern-level assertion
stays green.

Run with: pytest claude/.claude/
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from helpers import (
    REPO_ROOT,
    init_ci_detect_step_test_repo,
    run_ci_detect_step,
)

_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "tests.yml"

_INTENDED_DENY_LIST = frozenset(
    {"LICENSE", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "SECURITY.md", "CONTRIBUTING.md"}
)

# A sample of the 13 paths the prior allow-list (REGEX) silently skipped —
# tests must never be skipped on any of these.
_PREVIOUSLY_MISSING_PATHS = (
    "install.sh",
    "evals/run_skill_evals.py",
    "claude/.claude/CLAUDE.md",
    ".claude/rules/skill-and-agent-self-review.md",
    "claude/.claude/rules/github-actions-workflows.md",
    "docs/rules-references.md",
    ".claude-plugin/marketplace.json",
    "claude/.claude/statusline-command.sh",
    ".shellcheckrc",
    "scripts/list-shell-files.sh",
)


def _skip_regex_pattern() -> str:
    workflow = _WORKFLOW_PATH.read_text()
    match = re.search(r"^\s*SKIP_REGEX='([^']+)'", workflow, re.MULTILINE)
    assert match, "SKIP_REGEX not found in tests.yml — did the gate move?"
    return match.group(1)


def _grep_e_matches(pattern: str, candidate: str) -> bool:
    """Match via the real `grep -E` binary rather than `re.compile` — see
    test_shellcheck.py's `TestCiGateCoversDiscovery._grep_e_matches` for why
    (CI evaluates the pattern with `grep -E`'s POSIX ERE dialect)."""
    result = subprocess.run(
        ["grep", "-E", pattern],
        input=candidate,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


class TestSkipRegexBounded:
    """Pattern-only checks against SKIP_REGEX, mirroring
    test_shellcheck.py's `TestCiGateCoversDiscovery` shape."""

    def test_deny_list_paths_match(self):
        pattern = _skip_regex_pattern()
        for path in sorted(_INTENDED_DENY_LIST):
            assert _grep_e_matches(pattern, path), (
                f"SKIP_REGEX must match {path} — it's part of the intended "
                "deny-list"
            )

    def test_previously_missing_paths_do_not_match(self):
        pattern = _skip_regex_pattern()
        wrongly_skipped = [
            p for p in _PREVIOUSLY_MISSING_PATHS if _grep_e_matches(pattern, p)
        ]
        assert not wrongly_skipped, (
            "SKIP_REGEX matches paths that tests actually read — these would "
            f"be silently skipped: {wrongly_skipped}"
        )

    def test_skip_regex_matches_exactly_the_intended_deny_list(self):
        """Bounds SKIP_REGEX against every tracked file, not a sample.

        The two tests above only ever probe 15 specific strings — a future
        edit that widens SKIP_REGEX (e.g. `CHANGELOG\\.md$` -> `.*\\.md$`)
        would pass both unchanged. This test catches that: it fails the
        moment SKIP_REGEX matches anything outside the intended 5-file set.
        """
        pattern = _skip_regex_pattern()
        matched = {p for p in _tracked_files() if _grep_e_matches(pattern, p)}
        assert matched == _INTENDED_DENY_LIST, (
            f"SKIP_REGEX matches {sorted(matched)}, expected exactly "
            f"{sorted(_INTENDED_DENY_LIST)} — a broadened deny-list "
            "silently reintroduces fail-open skipping"
        )


class TestDetectStepFailOpenSetsChanged:
    """Executes the `detect` step's real `run:` body against the `changed`
    output, reusing `test_shellcheck.py`'s extraction/execution helpers
    (hoisted to `helpers.py`, shared by both files) for the same step.
    `TestSkipRegexBounded` only sees SKIP_REGEX as a string; it cannot see
    the surrounding quantifier — "changed=true unless every changed path
    matches SKIP_REGEX" — which is the actual boolean this mechanism exists
    to get right.
    """

    def test_deny_listed_only_diff_sets_changed_false(self, tmp_path: Path):
        repo, base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"LICENSE": "MIT\n"}
        )
        outputs = run_ci_detect_step(repo, base_sha, head_sha)
        assert outputs.get("changed") == "false", (
            "Expected changed=false for a diff touching only deny-listed "
            f"paths; got outputs: {outputs}"
        )

    def test_mixed_diff_sets_changed_true(self, tmp_path: Path):
        repo, base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"LICENSE": "MIT\n", "claude/.claude/CLAUDE.md": "notes\n"}
        )
        outputs = run_ci_detect_step(repo, base_sha, head_sha)
        assert outputs.get("changed") == "true", (
            "Expected changed=true for a diff mixing a deny-listed path with "
            "a non-deny-listed path — this is the quantifier under test "
            f"('unless every changed path matches'); got outputs: {outputs}"
        )

    def test_non_deny_listed_only_diff_sets_changed_true(self, tmp_path: Path):
        repo, base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"claude/.claude/CLAUDE.md": "notes\n"}
        )
        outputs = run_ci_detect_step(repo, base_sha, head_sha)
        assert outputs.get("changed") == "true", (
            "Expected changed=true for a diff touching only a non-deny-listed "
            f"path; got outputs: {outputs}"
        )

    def test_empty_diff_sets_changed_false(self, tmp_path: Path):
        repo, _base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"claude/.claude/CLAUDE.md": "notes\n"}
        )
        outputs = run_ci_detect_step(repo, head_sha, head_sha)
        assert outputs.get("changed") == "false", (
            "Expected changed=false for an empty diff (BASE == HEAD), "
            f"deliberately, not merely absent; got outputs: {outputs}"
        )

    def test_zero_sha_base_sets_changed_true(self, tmp_path: Path):
        """The exact regression this PR's CI path-filter mechanism targets.

        The zero-SHA/unresolvable-BASE early-exit branch must set
        changed=true — it is the fail-open path for the pytest suite, not
        just for shellcheck (test_shellcheck.py's
        `test_zero_sha_base_sets_shell_changed_true` covers that output).
        """
        repo, _base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"README.md": "second\n"}
        )
        zero_sha = "0" * 40
        outputs = run_ci_detect_step(repo, zero_sha, head_sha)
        assert outputs.get("changed") == "true", (
            "changed must be 'true' when BASE is the zero SHA (the "
            f"fail-open path) — got {outputs.get('changed')!r}."
        )

    def test_deny_listed_multi_file_diff_sets_changed_false(self, tmp_path: Path):
        repo, base_sha, head_sha = init_ci_detect_step_test_repo(
            tmp_path, {"LICENSE": "MIT\n", "SECURITY.md": "policy\n"}
        )
        outputs = run_ci_detect_step(repo, base_sha, head_sha)
        assert outputs.get("changed") == "false", (
            "Expected changed=false for a diff touching only deny-listed "
            f"paths, even when there's more than one; got outputs: {outputs}"
        )
