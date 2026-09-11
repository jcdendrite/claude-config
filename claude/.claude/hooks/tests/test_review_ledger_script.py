"""Tests for claude/.claude/scripts/review-ledger.sh."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    SCRIPTS_DIR,
    TRAVERSAL_SESSION_ID,
    git_toplevel,
    plant_traversal_canary,
)
from transcript_analysis import author_outcome as ao

from .conftest import _dead_pid, _seed_session

REVIEW_LEDGER_SCRIPT = SCRIPTS_DIR / "review-ledger.sh"

SID = "test-session-abc"


def _run(
    args: list[str], cwd, home, extra_env: dict | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(REVIEW_LEDGER_SCRIPT)] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _popen(args: list[str], cwd, home) -> subprocess.Popen:
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.Popen(
        ["bash", str(REVIEW_LEDGER_SCRIPT)] + args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _ledger_path(home: Path, repo: Path, session_id: str = SID) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "review-narrative-ledger" / f"{repo_hash}.{session_id}.jsonl"


def _append_args(
    finding: str = "Missing error handling in foo()",
    disposition: str = "ADDRESS",
    rationale: str = "fixed inline",
    source: str | None = None,
    round: str | None = "1",
    authoring_agent: str | None = None,
    authoring_effort: str | None = None,
) -> list[str]:
    args = [
        "append",
        "code-review",
        "--finding",
        finding,
        "--disposition",
        disposition,
        "--rationale",
        rationale,
    ]
    if source is not None:
        args += ["--source", source]
    if round is not None:
        args += ["--round", round]
    if authoring_agent is not None:
        args += ["--authoring-agent", authoring_agent]
    if authoring_effort is not None:
        args += ["--authoring-effort", authoring_effort]
    return args


class TestReviewLedgerSessionMissing:
    """Mirrors test_marker_script.py's TestMarkerScriptSessionMissing: without
    a seeded session file, every session-scoped subcommand must exit 2 and
    write nothing."""

    @pytest.mark.parametrize("args", [_append_args(), ["show"]])
    def test_exits_2_when_session_file_missing(self, isolated_home, git_repo, args):
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"review-ledger.sh {' '.join(args)} should exit 2 when the session "
            f"file is absent, got {result.returncode}. stderr: {result.stderr!r}"
        )

    def test_no_ledger_written_when_session_file_missing(self, isolated_home, git_repo):
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        stray = list(ledger_dir.iterdir()) if ledger_dir.exists() else []
        assert stray == [], f"review-ledger.sh wrote a stray ledger file: {stray}"


class TestReviewLedgerSessionIdValidation:
    """Mirrors test_marker_script.py's TestMarkerScriptSessionIdValidation: a
    session file whose content is a path-escaping value must be rejected by
    the same chokepoint that rejects a missing session file."""

    @pytest.mark.parametrize("args", [_append_args(), ["show"]])
    def test_exits_2_for_path_escaping_session_id(self, isolated_home, git_repo, args):
        _seed_session(isolated_home, TRAVERSAL_SESSION_ID)
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"review-ledger.sh {' '.join(args)} should exit 2 for a "
            f"path-escaping session id, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )

    def test_no_stray_file_for_path_escaping_session_id(self, isolated_home, git_repo):
        _seed_session(isolated_home, TRAVERSAL_SESSION_ID)
        canary = plant_traversal_canary(isolated_home)

        _run(_append_args(), cwd=git_repo, home=isolated_home)

        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        stray = list(ledger_dir.iterdir()) if ledger_dir.exists() else []
        assert stray == [], (
            f"review-ledger.sh wrote a stray ledger file for a path-escaping "
            f"session id: {stray}"
        )
        assert canary.read_text() == CANARY_CONTENT, (
            "a path-escaping session id must not let 'append' touch a file "
            "outside the ledger directory"
        )


class TestReviewLedgerAppendHappyPath:
    def test_append_creates_ledger_with_expected_fields(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(source="foo.py:12"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        ledger = _ledger_path(isolated_home, git_repo)
        assert ledger.exists()
        lines = ledger.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        event_time = record.pop("event_time")
        assert record == {
            "schema_version": 2,
            "round": 1,
            "finding": "Missing error handling in foo()",
            "disposition": "ADDRESS",
            "rationale": "fixed inline",
            "source": "foo.py:12",
            "authoring_agent": "",
            "authoring_effort": "",
        }
        # UTC, second-resolution, Z-suffixed -- e.g. 2026-08-01T10:00:00Z.
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", event_time), event_time

    def test_append_defaults_source_to_n_a(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["source"] == "n/a"

    def test_repeat_identical_line_is_deduped(self, isolated_home, git_repo):
        """A retried /code-review round re-emitting an unchanged
        finding+disposition+rationale triple is a no-op, not a duplicate."""
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, f"identical repeat append must be a no-op, got: {lines}"

    def test_changed_disposition_is_a_new_line_not_deduped(self, isolated_home, git_repo):
        """The same finding tagged DEFER this round and ADDRESS next round is
        distinct history, not noise to suppress."""
        _seed_session(isolated_home, SID)
        _run(_append_args(disposition="DEFER", rationale="orthogonal scope"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(disposition="ADDRESS", rationale="fixed inline"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 2, (
            f"a changed disposition for the same finding must append a new "
            f"line rather than dedup, got: {lines}"
        )
        dispositions = {json.loads(line)["disposition"] for line in lines}
        assert dispositions == {"DEFER", "ADDRESS"}

    def test_repeat_identical_line_with_authoring_fields_is_deduped(self, isolated_home, git_repo):
        """authoring_agent/authoring_effort are round-stable, so an identical
        retry with the same values is still a no-op, not a duplicate --
        confirms `authoring_agent`/`authoring_effort` don't turn dedup's
        whole-line `grep -qFx` into a per-call-varying comparison."""
        _seed_session(isolated_home, SID)
        args = _append_args(authoring_agent="code-writer", authoring_effort="high")
        _run(args, cwd=git_repo, home=isolated_home)
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, f"identical repeat append must be a no-op, got: {lines}"

    def test_absent_authoring_flags_still_succeeds(self, isolated_home, git_repo):
        """An absent --authoring-agent/--authoring-effort must never abort
        the append -- only an invalid value does."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["authoring_agent"] == ""
        assert record["authoring_effort"] == ""

    def test_declared_authoring_fields_land_in_the_record(self, isolated_home, git_repo):
        """A non-default --authoring-agent/--authoring-effort pair must
        reach the persisted record verbatim, not get silently dropped by a
        `jq --arg` wiring mistake."""
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_agent="mixed", authoring_effort="xhigh"),
            cwd=git_repo,
            home=isolated_home,
        )
        assert result.returncode == 0, result.stderr
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["authoring_agent"] == "mixed"
        assert record["authoring_effort"] == "xhigh"

    def test_invalid_authoring_agent_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_agent="general-purpose"), cwd=git_repo, home=isolated_home
        )
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_invalid_authoring_effort_rejected(self, isolated_home, git_repo):
        """`max` is deliberately excluded from the effort enum (CLAUDE.md's
        "xhigh, not max") -- rejecting it is the signal that the routing
        rule changed, not a value to silently record."""
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_effort="max"), cwd=git_repo, home=isolated_home
        )
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_unknown_gate_argument_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "plan-review", "--finding", "x", "--disposition", "ADDRESS", "--rationale", "r"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2

    def test_invalid_disposition_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(disposition="MAYBE"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        ledger = _ledger_path(isolated_home, git_repo)
        assert not ledger.exists()

    def test_empty_finding_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(finding=""), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_missing_finding_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "ADDRESS", "--rationale", "r"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_empty_rationale_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(rationale=""), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_missing_rationale_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--finding", "x", "--disposition", "ADDRESS"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()


class TestReviewLedgerAuthoringAgentEnum:
    """The `--authoring-agent` enum review-ledger.sh validates against must
    equal the enum author_outcome.py's transcript-side classifier compares
    declared values against -- a drift here would let review-ledger.sh
    accept a value author_outcome.py silently never treats as consistent.

    Drives the actual CLI rather than scanning either file's source text: a
    source-scanning version of this test broke on a behavior-preserving
    shell case-pattern reorder even though the script's real behavior was
    unchanged (test-conventions §9)."""

    @pytest.mark.parametrize(
        "authoring_agent",
        [
            ao._AUTHORING_AGENT_CODE_WRITER,
            ao._AUTHORING_AGENT_INLINE,
            ao._AUTHORING_AGENT_MIXED,
            ao._AUTHORING_AGENT_UNKNOWN,
        ],
    )
    def test_each_author_outcome_constant_is_accepted(self, isolated_home, git_repo, authoring_agent):
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_agent=authoring_agent), cwd=git_repo, home=isolated_home
        )
        assert result.returncode == 0, (
            f"review-ledger.sh must accept --authoring-agent {authoring_agent!r} "
            f"(an author_outcome.py _AUTHORING_AGENT_* constant): {result.stderr}"
        )
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["authoring_agent"] == authoring_agent

    def test_value_outside_author_outcome_constants_is_rejected(self, isolated_home, git_repo):
        """A value not among author_outcome.py's own constants must be
        rejected -- accepting it would let review-ledger.sh log an
        authoring_agent author_outcome.py's classifier can never match."""
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_agent="not-a-real-agent"), cwd=git_repo, home=isolated_home
        )
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()


class TestReviewLedgerRoundValidation:
    def test_missing_round_rejected_with_exact_error_text(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round=None), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()
        assert "review-ledger.sh: --round <N> is required and missing.\n" in result.stderr
        assert "got '" not in result.stderr, "the missing-flag branch must not print a 'got' value"

    def test_non_numeric_round_rejected_with_offending_value_shown(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round="not-a-number"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()
        assert "got 'not-a-number'" in result.stderr

    def test_zero_round_rejected(self, isolated_home, git_repo):
        """0 is not a 1-based round number."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round="0"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()
        assert "got '0'" in result.stderr

    @pytest.mark.parametrize("round_value", ["00", "000"])
    def test_zero_padded_round_rejected(self, isolated_home, git_repo, round_value):
        """jq normalizes "00"/"000" to round:0 -- reject before that happens."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round=round_value), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()
        assert f"got '{round_value}'" in result.stderr

    def test_negative_round_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round="-1"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_valid_round_accepted_and_recorded(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round="3"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["round"] == 3

    def test_over_cap_round_rejected_with_no_partial_write(self, isolated_home, git_repo):
        """--round has no digit-count ceiling other than this cap -- a
        value beyond it must be rejected the same way an over-cap
        --finding/--rationale/--source is."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round="99999"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()
        assert "--round exceeds 4 digits" in result.stderr

    def test_at_cap_round_accepted(self, isolated_home, git_repo):
        """The boundary itself (exactly 4 digits) must not be rejected --
        only strictly-over-cap values are."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(round="9999"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr


class TestReviewLedgerCleanDisposition:
    def test_clean_disposition_accepted_without_finding_or_rationale(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "CLEAN", "--round", "1"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["disposition"] == "CLEAN"
        assert record["finding"] == ""
        assert record["rationale"] == ""

    def test_clean_disposition_rejects_a_present_finding(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "CLEAN", "--round", "1", "--finding", "x"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_clean_disposition_rejects_a_present_rationale(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "CLEAN", "--round", "1", "--rationale", "why"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_clean_disposition_rejects_a_present_source(self, isolated_home, git_repo):
        """CLEAN carries no per-finding location, so --source must be
        rejected the same way --finding/--rationale are."""
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "CLEAN", "--round", "1", "--source", "foo.py:12"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_address_disposition_still_requires_finding_and_rationale(self, isolated_home, git_repo):
        """CLEAN's relaxed requirements must not leak into ADDRESS|DEFER."""
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "ADDRESS", "--round", "1"]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_invalid_disposition_message_lists_clean(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(disposition="MAYBE"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert "must be ADDRESS, DEFER, or CLEAN" in result.stderr


class TestReviewLedgerRoundScopedDedup:
    def test_identical_finding_in_two_different_rounds_both_land(self, isolated_home, git_repo):
        """Two rounds raising a textually identical finding/disposition/
        rationale must not collapse into one ledger line under the
        round-scoped dedup key -- the bug this schema revision fixes, since
        whole-line dedup alone would have collapsed them once event_time
        stopped being the only varying field."""
        _seed_session(isolated_home, SID)
        _run(_append_args(round="1"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(round="2"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 2, f"identical finding in a different round must not dedup, got: {lines}"
        rounds = {json.loads(line)["round"] for line in lines}
        assert rounds == {1, 2}

    def test_retried_identical_call_within_same_round_still_dedups(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(round="1"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(round="1"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, f"identical retry within the same round must dedup, got: {lines}"


class TestReviewLedgerDedupFilterIsStaticLiteral:
    """_lib_append_json_line_locked's own docstring (_lib.sh) requires its
    DEDUP_KEY_JQ_FILTER argument to be a static, developer-authored jq
    literal, never derived from session- or user-controlled data, since it
    is spliced directly into the jq program text with no --arg/--argjson
    escaping. The behavioral test below pins the security property that
    invariant protects, by driving the CLI and confirming that
    user-controlled --finding/--rationale/--source values are bound via
    jq's --arg mechanism rather than spliced into any filter text. The
    source-scan test is a secondary tripwire on the static-literal call
    site itself, needed because a regression there away from a static
    literal produces no stdout/exit-code difference for the behavioral
    test to observe."""

    def test_call_site_passes_a_single_quoted_literal_with_no_expansion(self):
        source = REVIEW_LEDGER_SCRIPT.read_text()
        match = re.search(
            r"^\s*_lib_append_json_line_locked\b[^\n]*\n\s*(\S.*)$",
            source,
            re.MULTILINE,
        )
        assert match, "no _lib_append_json_line_locked call site found in review-ledger.sh"
        filter_arg = match.group(1).strip()
        assert filter_arg.startswith("'") and filter_arg.endswith("'"), (
            f"dedup filter argument must be a single-quoted literal, got: {filter_arg!r}"
        )
        assert "$" not in filter_arg, (
            f"dedup filter argument must contain no variable expansion, got: {filter_arg!r}"
        )

    def test_jq_filter_special_chars_in_finding_round_trip_unmodified(self, isolated_home, git_repo):
        """--finding/--rationale/--source are user-controlled and reach jq
        only through _lib_jq's --arg binding, never spliced into filter
        text. This drives the CLI with a value containing jq-filter syntax
        (quote, backslash, pipe, dot) and asserts it lands in the ledger
        row byte-for-byte. A naive string-splice would either break the
        filter (non-zero exit) or let the value's dots/pipes reshape the
        jq program instead of being treated as opaque string content."""
        _seed_session(isolated_home, SID)
        injected = 'has "quotes" \\ backslash | pipe .dot $var {brace}'
        result = _run(
            _append_args(finding=injected, rationale=injected, source=injected),
            cwd=git_repo,
            home=isolated_home,
        )
        assert result.returncode == 0, result.stderr
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["finding"] == injected
        assert record["rationale"] == injected
        assert record["source"] == injected


class TestReviewLedgerFieldCaps:
    def test_over_cap_finding_rejected_with_no_partial_write(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(finding="x" * 201), cwd=git_repo, home=isolated_home)
        assert result.returncode != 0
        ledger = _ledger_path(isolated_home, git_repo)
        assert not ledger.exists(), "an over-cap --finding must not create a ledger file"

    def test_over_cap_rationale_rejected_with_no_partial_write(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(rationale="x" * 301), cwd=git_repo, home=isolated_home)
        assert result.returncode != 0
        ledger = _ledger_path(isolated_home, git_repo)
        assert not ledger.exists(), "an over-cap --rationale must not create a ledger file"

    def test_over_cap_rejection_does_not_clobber_existing_ledger(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        good_content = _ledger_path(isolated_home, git_repo).read_text()

        _run(_append_args(finding="x" * 201), cwd=git_repo, home=isolated_home)

        assert _ledger_path(isolated_home, git_repo).read_text() == good_content, (
            "a rejected over-cap append must not alter an existing ledger file"
        )

    def test_at_cap_finding_accepted(self, isolated_home, git_repo):
        """The boundary itself (exactly 200 chars) must not be rejected —
        only strictly-over-cap values are."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(finding="x" * 200), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr

    def test_at_cap_rationale_accepted(self, isolated_home, git_repo):
        """The boundary itself (exactly 300 chars) must not be rejected —
        only strictly-over-cap values are."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(rationale="x" * 300), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr

    def test_over_cap_source_rejected_with_no_partial_write(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(_append_args(source="x" * 201), cwd=git_repo, home=isolated_home)
        assert result.returncode != 0
        ledger = _ledger_path(isolated_home, git_repo)
        assert not ledger.exists(), "an over-cap --source must not create a ledger file"

    def test_at_cap_source_accepted(self, isolated_home, git_repo):
        """The boundary itself (exactly 200 chars) must not be rejected —
        only strictly-over-cap values are."""
        _seed_session(isolated_home, SID)
        result = _run(_append_args(source="x" * 200), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr


class TestReviewLedgerKillSwitch:
    def test_kill_switch_suppresses_append(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        (isolated_home / ".claude" / ".review-narrative-ledger-disabled").touch()
        result = _run(_append_args(), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists(), (
            "append must be a silent no-op while the kill switch is present"
        )

    def test_kill_switch_does_not_suppress_show(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        (isolated_home / ".claude" / ".review-narrative-ledger-disabled").touch()

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert "Missing error handling in foo()" in result.stdout, (
            "show must still read a ledger written before the kill switch was set"
        )


class TestReviewLedgerShow:
    def test_show_reports_absence_when_no_ledger(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(["show"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert "no ledger" in result.stdout.lower()

    def test_show_prints_ledger_contents(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        result = _run(["show"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        record = json.loads(result.stdout.splitlines()[0])
        assert record["finding"] == "Missing error handling in foo()"


class TestReviewLedgerLocking:
    def test_lock_released_after_successful_append(self, isolated_home, git_repo):
        """An immediate second append must not have to work through bounded
        retries against a lock the first append left behind."""
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        lock_file = _ledger_path(isolated_home, git_repo).with_suffix(".jsonl.lock")
        assert not lock_file.exists(), "the lock file must be removed after a successful append"

    def test_concurrent_appends_with_identical_content_produce_no_corruption(
        self, isolated_home, git_repo
    ):
        """Two racing appends of the identical finding/disposition/rationale
        triple may produce a duplicate line (a low-consequence outcome) but
        must never corrupt the file — every resulting line must parse."""
        _seed_session(isolated_home, SID)
        procs = [_popen(_append_args(), cwd=git_repo, home=isolated_home) for _ in range(2)]
        for proc in procs:
            proc.communicate(timeout=10)

        ledger = _ledger_path(isolated_home, git_repo)
        lines = ledger.read_text().splitlines()
        assert len(lines) in (1, 2), f"expected 1 (deduped) or 2 (raced) lines, got: {lines}"
        for line in lines:
            record = json.loads(line)  # raises if a raced write corrupted the line
            assert record["finding"] == "Missing error handling in foo()"

    def test_concurrent_appends_with_distinct_content_both_land(self, isolated_home, git_repo):
        """A racing append that loses the lock must still fall through to an
        unlocked append rather than silently dropping the write."""
        _seed_session(isolated_home, SID)
        procs = [
            _popen(_append_args(finding="Finding A"), cwd=git_repo, home=isolated_home),
            _popen(_append_args(finding="Finding B"), cwd=git_repo, home=isolated_home),
        ]
        for proc in procs:
            proc.communicate(timeout=10)

        ledger = _ledger_path(isolated_home, git_repo)
        findings = {json.loads(line)["finding"] for line in ledger.read_text().splitlines()}
        assert findings == {"Finding A", "Finding B"}

    def test_lock_held_by_dead_pid_is_acquired_faster_than_a_live_lock(
        self, isolated_home, git_repo, live_pid
    ):
        """A lock file whose stored PID belongs to a dead process must be
        evicted and re-acquired on the very next attempt, not after
        exhausting all _LEDGER_LOCK_RETRIES-many sleeps. Compared against a
        live-lock control run in the same test (rather than a fixed
        wall-clock threshold), since absolute timing is too noisy under
        variable system/CI load — the dead-PID path skips every sleep the
        live-lock path is forced through, so the gap is large regardless of
        ambient load."""
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        lock_file = ledger.with_suffix(".jsonl.lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        lock_file.write_text(f"{_dead_pid()}\n")
        start = time.monotonic()
        result_dead = _run(_append_args(), cwd=git_repo, home=isolated_home, timeout=15)
        elapsed_dead_pid_lock = time.monotonic() - start
        assert result_dead.returncode == 0, result_dead.stderr
        assert ledger.exists()

        ledger.unlink()
        lock_file.write_text(f"{live_pid}\n")
        start = time.monotonic()
        result_live = _run(_append_args(), cwd=git_repo, home=isolated_home, timeout=15)
        elapsed_live_pid_lock = time.monotonic() - start
        assert result_live.returncode == 0, result_live.stderr

        assert elapsed_dead_pid_lock < elapsed_live_pid_lock, (
            f"dead-PID eviction ({elapsed_dead_pid_lock:.2f}s) should be "
            f"faster than exhausting every retry against a live lock "
            f"({elapsed_live_pid_lock:.2f}s) — a prompt eviction, not a wait"
        )

    def test_preexisting_live_lock_still_completes_append_within_bounded_time(
        self, isolated_home, git_repo, live_pid
    ):
        """A .lock file pre-created before the subprocess starts, and held
        by a still-live process, guarantees every acquisition attempt
        contends — deterministic, unlike the two-subprocess race tests
        above, which pass even with locking deleted entirely. Exercises the
        _LEDGER_LOCK_RETRIES-exhaustion -> unlocked-fallback path directly."""
        _seed_session(isolated_home, SID)
        lock_file = _ledger_path(isolated_home, git_repo).with_suffix(".jsonl.lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(f"{live_pid}\n")

        # timeout=15 is the hang-guard: a regression that made the fallthrough
        # block would raise TimeoutExpired here instead of hanging the suite,
        # the same pattern test_unwritable_log_dir_does_not_hang (hooks/tests/
        # test_require_code_review.py) uses for its own hang-risk assertion.
        result = _run(_append_args(), cwd=git_repo, home=isolated_home, timeout=15)

        assert result.returncode == 0, result.stderr
        ledger = _ledger_path(isolated_home, git_repo)
        assert ledger.exists()
        record = json.loads(ledger.read_text().splitlines()[0])
        assert record["finding"] == "Missing error handling in foo()"
        assert lock_file.exists(), (
            "a lock held by a still-live process is not this invocation's "
            "lock to remove"
        )


class TestReviewLedgerDirectoryCreationFailure:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unwritable_config_dir_exits_2_with_no_ledger(self, isolated_home, git_repo):
        """mkdir -p failing (permission denied, disk full, path occupied by
        a stale file) must fail loudly like every other fallible step in
        this script, not fall through to a silent exit 0 with nothing
        written."""
        _seed_session(isolated_home, SID)
        config_dir = isolated_home / ".claude"
        config_dir.chmod(0o555)
        try:
            result = _run(_append_args(), cwd=git_repo, home=isolated_home)
        finally:
            config_dir.chmod(0o755)
        assert result.returncode == 2
        assert not (isolated_home / ".claude" / "review-narrative-ledger").exists()


class TestReviewLedgerMultiByteBoundary:
    def test_multibyte_finding_near_cap_boundary_is_well_defined(self, isolated_home, git_repo):
        """${#FINDING} counts codepoints under a UTF-8 locale but bytes
        under C/POSIX (bash's ${#VAR} is locale-dependent). This computes
        the length the same way the script does — a bash ${#VAR} evaluated
        under this environment's own inherited locale — so the accept/reject
        assertion holds regardless of which locale the suite runs under,
        rather than assuming one."""
        _seed_session(isolated_home, SID)
        multibyte_finding = "é" * 100  # 100 codepoints, 200 UTF-8 bytes
        length = int(
            subprocess.run(
                ["bash", "-c", 'a="$1"; printf "%s" "${#a}"', "_", multibyte_finding],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        result = _run(_append_args(finding=multibyte_finding), cwd=git_repo, home=isolated_home)
        if length <= 200:
            assert result.returncode == 0, (
                f"computed length={length} <= the 200-char cap, expected accept: {result.stderr}"
            )
        else:
            assert result.returncode == 2, (
                f"computed length={length} > the 200-char cap, expected reject"
            )
            assert not _ledger_path(isolated_home, git_repo).exists()


class TestReviewLedgerMtimeSweep:
    def _make_stale(self, path: Path, content: str = '{"finding":"stale"}\n') -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        thirty_one_days_ago = time.time() - 31 * 24 * 60 * 60
        os.utime(path, (thirty_one_days_ago, thirty_one_days_ago))

    def test_append_sweeps_stale_jsonl_in_a_different_repo_hash(self, isolated_home, git_repo):
        """The sweep runs directory-wide, not scoped to the invoking
        session's own file — a rarely-appended-to repo's stale file would
        otherwise never get swept."""
        _seed_session(isolated_home, SID)
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        stale = ledger_dir / ("0" * 64 + ".other-session.jsonl")
        self._make_stale(stale)

        _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert not stale.exists(), (
            "append's directory-wide sweep must remove a stale .jsonl file "
            "under a different repo-hash"
        )

    def test_append_sweeps_stale_lock_files(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        self._make_stale(ledger_dir / ("0" * 64 + ".other-session.jsonl.lock"), content="")

        _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert not (ledger_dir / ("0" * 64 + ".other-session.jsonl.lock")).exists(), (
            "append's directory-wide sweep must remove a stale orphaned .lock file"
        )

    def test_append_does_not_sweep_fresh_files(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        fresh = ledger_dir / ("1" * 64 + ".fresh-session.jsonl")
        fresh.parent.mkdir(parents=True, exist_ok=True)
        fresh.write_text('{"finding":"fresh"}\n')

        _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert fresh.exists(), "append's sweep must not remove a file younger than 30 days"

    def test_clear_stale_dry_run_reports_without_removing(self, isolated_home, git_repo):
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        stale = ledger_dir / ("0" * 64 + ".other-session.jsonl")
        self._make_stale(stale)

        result = _run(["clear-stale", "--dry-run"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert "would evict" in result.stdout.lower()
        assert stale.exists(), "--dry-run must not remove anything"

    def test_clear_stale_real_removes(self, isolated_home, git_repo):
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        stale = ledger_dir / ("0" * 64 + ".other-session.jsonl")
        self._make_stale(stale)

        result = _run(["clear-stale"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert not stale.exists(), "clear-stale must remove a stale ledger file"

    def test_append_dedup_noop_refreshes_own_stale_mtime(self, isolated_home, git_repo):
        """A dedup no-op (identical finding/disposition/rationale/source
        already present) must still count as activity on this session's own
        ledger file — otherwise a long-running session (>30 days) whose
        last *actual* write predates today's dedup no-op would have its own
        active ledger deleted by the sweep this same invocation triggers."""
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        ledger = _ledger_path(isolated_home, git_repo)
        original_content = ledger.read_text()
        thirty_one_days_ago = time.time() - 31 * 24 * 60 * 60
        os.utime(ledger, (thirty_one_days_ago, thirty_one_days_ago))

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert ledger.exists(), (
            "a dedup no-op must refresh its own ledger file's mtime so the "
            "directory-wide sweep this same invocation triggers does not "
            "delete it"
        )
        assert ledger.read_text() == original_content, (
            "content must be unchanged by the dedup no-op"
        )
