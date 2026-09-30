"""Tests for claude/.claude/scripts/review-ledger.sh."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    CLAUDE_DIR,
    SCRIPTS_DIR,
    SKILLS_DIR,
    TRAVERSAL_SESSION_ID,
    git_toplevel,
    plant_traversal_canary,
)
from transcript_analysis import author_outcome as ao

from .conftest import _dead_pid, _review_ledger_path, _seed_session

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


def _utf8_locale() -> str:
    """An installed UTF-8 locale name. Also imported by scripts/tests/test_review_ledger_lib.py."""
    try:
        available = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        available = ""
    available_names = {name.strip().lower() for name in available.splitlines()}
    for candidate in ("C.UTF-8", "C.utf8", "en_US.UTF-8", "en_US.utf8"):
        if candidate.lower() in available_names:
            return candidate
    message = "no UTF-8 locale installed"
    if os.environ.get("CI"):
        # A skipped run would drop the byte-versus-character discriminator unnoticed.
        pytest.fail(f"{message}; CI must provide one")
    pytest.skip(message)


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


def _popen_as_session(args: list[str], cwd, home, session_id: str) -> subprocess.Popen:
    """Start review-ledger.sh under a wrapper shell that registers itself as
    the Claude Code process for `session_id`, so several concurrent
    invocations resolve distinct session ids. The script's session lookup
    walks up from its parent, which is this wrapper."""
    wrapper = (
        'start=$(TZ=UTC LC_ALL=C ps -o lstart= -p "$$"); '
        'mkdir -p "$HOME/.claude/sessions"; '
        'printf "%s\\n%s\\n" "$1" "$start" > "$HOME/.claude/sessions/$$"; '
        'shift; bash "$0" "$@"; exit $?'
    )
    env = {**os.environ, "HOME": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.Popen(
        ["bash", "-c", wrapper, str(REVIEW_LEDGER_SCRIPT), session_id, *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _ledger_path(home: Path, repo: Path, session_id: str = SID) -> Path:
    return _review_ledger_path(home, repo, session_id)


def _repo_hash(repo: Path) -> str:
    return hashlib.sha256(git_toplevel(repo).encode()).hexdigest()


def _session_ledger_path(home: Path, repo: Path, session_id: str = SID) -> Path:
    """This worktree's session-keyed file, whatever scope HEAD currently
    resolves to."""
    return home / ".claude" / "review-narrative-ledger" / f"{_repo_hash(repo)}.{session_id}.jsonl"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _rename_branch(repo: Path, name: str) -> None:
    _git(repo, "branch", "-M", name)


def _show_header(result: subprocess.CompletedProcess) -> dict[str, str]:
    """The key=value pairs of `show`'s one-line stderr header."""
    header_lines = [line for line in result.stderr.splitlines() if line.startswith("review-ledger.sh: show ")]
    assert len(header_lines) == 1, f"expected exactly one show header on stderr, got: {result.stderr!r}"
    pairs = header_lines[0].removeprefix("review-ledger.sh: show ").split(" ")
    return dict(pair.split("=", 1) for pair in pairs)


def _shown_rows(result: subprocess.CompletedProcess) -> list[dict]:
    return [json.loads(line) for line in result.stdout.splitlines()]


# review-ledger.sh's per-field character caps: finding, rationale, source.
_ROW_FIELD_CAPS = (200, 300, 200)


def _row_envelope_bytes(session_id: str, **row_overrides) -> int:
    """Bytes of a built row with empty finding/rationale/source, in jq -c key
    order, for a round-1 ADDRESS append with no authoring flags. row_overrides
    replace fields, for a row of another disposition."""
    row = {
        "schema_version": 4, "round": 1, "finding": "", "disposition": "ADDRESS", "rationale": "",
        "source": "", "authoring_agent": "", "authoring_effort": "", "session_id": session_id,
        "decided_by": "", "engineer_quote": "", "enforcement_invariant": False, "carry_forward": False,
        "defer_criterion": "", "ref": "", "cited_line": "", "site_hash": "",
        "event_time": "2026-01-01T00:00:00Z", "id": "0" * 12,
    }
    row.update(row_overrides)
    return len(json.dumps(row, separators=(",", ":")).encode())


def _fields_for_row_of_bytes(row_bytes: int, session_id: str = SID, **row_overrides) -> tuple[str, str, str]:
    """(finding, rationale, third) within the per-field character caps whose
    built row is exactly `row_bytes` long. U+0001 costs six bytes in a jq
    string (\\u0001) and `x` one, so a mix reaches any size the caps allow.
    `third` is an ADDRESS row's source by default, and an engineer SETTLED's
    quote when row_overrides describe one (both cap at 200 characters)."""
    field_bytes = row_bytes - _row_envelope_bytes(session_id, **row_overrides)
    assert field_bytes > 0
    # The mix has to fit the total per-field character budget: 6k + m == field_bytes, k + m <= budget.
    control_count = max(0, -(-(field_bytes - sum(_ROW_FIELD_CAPS)) // 5))
    filler_count = field_bytes - 6 * control_count
    assert control_count + filler_count <= sum(_ROW_FIELD_CAPS), "row size unreachable within the field caps"
    text = "\x01" * control_count + "x" * filler_count
    finding_cap, rationale_cap, _third_cap = _ROW_FIELD_CAPS
    return text[:finding_cap], text[finding_cap:finding_cap + rationale_cap], text[finding_cap + rationale_cap:]


def _append_args(
    finding: str = "Missing error handling in foo()",
    disposition: str = "ADDRESS",
    rationale: str = "fixed inline",
    source: str | None = None,
    round: str | None = "1",
    authoring_agent: str | None = None,
    authoring_effort: str | None = None,
    decided_by: str | None = None,
    engineer_quote: str | None = None,
    defer_criterion: str | None = None,
    ref: str | None = None,
    cited_line: str | None = None,
    enforcement_invariant: bool = False,
    carry_forward: bool = False,
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
    optional_valued_flags = [
        ("--source", source),
        ("--round", round),
        ("--authoring-agent", authoring_agent),
        ("--authoring-effort", authoring_effort),
        ("--decided-by", decided_by),
        ("--engineer-quote", engineer_quote),
        ("--defer-criterion", defer_criterion),
        ("--ref", ref),
        ("--cited-line", cited_line),
    ]
    for flag, value in optional_valued_flags:
        if value is not None:
            args += [flag, value]
    if enforcement_invariant:
        args.append("--enforcement-invariant")
    if carry_forward:
        args.append("--carry-forward")
    return args


# A DEFER criterion and a range-form source are required, so every DEFER append
# in these tests goes through this builder.
_DEFER_CRITERION = "orthogonal-scope"
_DEFER_SOURCE = "file.txt:1-2"


def _defer_args(**overrides) -> list[str]:
    params = {
        "disposition": "DEFER", "source": _DEFER_SOURCE, "defer_criterion": _DEFER_CRITERION,
        "rationale": "orthogonal scope",
    }
    params.update(overrides)
    return _append_args(**params)


class TestReviewLedgerSessionMissing:
    """Mirrors test_marker_script.py's TestMarkerScriptSessionMissing: without
    a seeded session file, every session-scoped subcommand must exit 2 and
    write nothing."""

    @pytest.mark.parametrize("args", [_append_args(), ["show"], ["render"]])
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

    @pytest.mark.parametrize("args", [_append_args(), ["show"], ["render"]])
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
        id_ = record.pop("id")
        assert record == {
            "schema_version": 4,
            "round": 1,
            "finding": "Missing error handling in foo()",
            "disposition": "ADDRESS",
            "rationale": "fixed inline",
            "source": "foo.py:12",
            "authoring_agent": "",
            "authoring_effort": "",
            "session_id": SID,
            "decided_by": "",
            "engineer_quote": "",
            "enforcement_invariant": False,
            "carry_forward": False,
            "defer_criterion": "",
            "ref": "",
            "cited_line": "",
            "site_hash": "",
        }
        assert re.fullmatch(r"[0-9a-f]{12}", id_)
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
        _run(_defer_args(), cwd=git_repo, home=isolated_home)
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
    """Proves one direction only: each of author_outcome.py's own
    `_AUTHORING_AGENT_*` constants is accepted, and one arbitrary
    out-of-set string is rejected. It does not prove the reverse -- that
    review-ledger.sh's case-pattern enum accepts *only* those constants
    plus empty -- so a 5th literal added to the script's case pattern
    with no corresponding python constant would still pass every test
    in this class.

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


class TestReviewLedgerAuthoringEffortEnum:
    """Proves only that each case-pattern value is accepted and one bad
    value is rejected -- it does not prove the case pattern accepts *only*
    those four values (a 5th added literal would still pass). Drives the
    CLI rather than scanning source text, per test-conventions §9."""

    @pytest.mark.parametrize("authoring_effort", ["low", "medium", "high", "xhigh"])
    def test_each_case_pattern_value_is_accepted(self, isolated_home, git_repo, authoring_effort):
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_effort=authoring_effort), cwd=git_repo, home=isolated_home
        )
        assert result.returncode == 0, (
            f"review-ledger.sh must accept --authoring-effort {authoring_effort!r}: "
            f"{result.stderr}"
        )
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["authoring_effort"] == authoring_effort

    def test_value_outside_case_pattern_is_rejected(self, isolated_home, git_repo):
        """A value not among the case pattern's four levels must be
        rejected -- accepting it would let review-ledger.sh log an
        authoring_effort no routing rule can ever match."""
        _seed_session(isolated_home, SID)
        result = _run(
            _append_args(authoring_effort="not-a-real-effort"), cwd=git_repo, home=isolated_home
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
        assert "got '-1'" in result.stderr

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
        assert "must be ADDRESS, DEFER, SETTLED, or CLEAN" in result.stderr

    def test_clean_disposition_with_authoring_fields_lands_all_four(self, isolated_home, git_repo):
        """Production code (code-review/SKILL.md's Step 0.1 short-circuit)
        always pairs --disposition CLEAN with
        --authoring-agent/--authoring-effort. This confirms CLEAN's relaxed
        finding/rationale requirements don't block those two fields from
        landing in the record."""
        _seed_session(isolated_home, SID)
        args = [
            "append",
            "code-review",
            "--disposition",
            "CLEAN",
            "--round",
            "1",
            "--authoring-agent",
            "code-writer",
            "--authoring-effort",
            "high",
        ]
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        record = json.loads(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0])
        assert record["disposition"] == "CLEAN"
        assert record["round"] == 1
        assert record["authoring_agent"] == "code-writer"
        assert record["authoring_effort"] == "high"


class TestReviewLedgerRoundScopedDedup:
    def test_identical_finding_in_two_different_rounds_both_land(self, isolated_home, git_repo):
        """Two rounds raising a textually identical finding/disposition/
        rationale must not collapse into one ledger line under the
        round-scoped dedup key, since whole-line dedup alone would collapse
        them once event_time stopped being the only varying field."""
        _seed_session(isolated_home, SID)
        _run(_append_args(round="1"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(round="2"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 2, f"identical finding in a different round must not dedup, got: {lines}"
        rounds = {json.loads(line)["round"] for line in lines}
        assert rounds == {1, 2}

    def test_current_row_matching_an_existing_v2_row_on_every_other_field_lands_as_a_second_row(
        self, isolated_home, git_repo
    ):
        """A v2 row carries no session_id, so the session_id in the dedup key
        keeps a current-schema append from collapsing into it."""
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({
            "schema_version": 2, "round": 1, "finding": "Missing error handling in foo()",
            "disposition": "ADDRESS", "rationale": "fixed inline", "source": "n/a",
            "authoring_agent": "", "authoring_effort": "", "event_time": "2024-01-01T00:00:00Z",
        }) + "\n")

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert [row["schema_version"] for row in rows] == [2, 4]

    def test_retried_identical_call_within_same_round_still_dedups(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(round="1"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(round="1"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, f"identical retry within the same round must dedup, got: {lines}"

    def test_retried_identical_clean_call_within_same_round_still_dedups(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = ["append", "code-review", "--disposition", "CLEAN", "--round", "1"]
        _run(args, cwd=git_repo, home=isolated_home)
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, f"identical CLEAN retry within the same round must dedup, got: {lines}"

    def test_disposition_alone_discriminates_within_the_same_round(self, isolated_home, git_repo):
        """The shipped dedup filter projects `disposition` among its fields,
        so two appends differing only on that field -- same round, same
        finding, same rationale -- must land as two lines, not collapse to
        one. Guards against a future narrowing of the filter (e.g. down to
        just `{round}`) that would silently dedup every append after the
        first within a round."""
        _seed_session(isolated_home, SID)
        _run(_append_args(round="1", disposition="ADDRESS"), cwd=git_repo, home=isolated_home)
        result = _run(_defer_args(round="1", rationale="fixed inline"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 2, (
            f"a disposition-only difference within the same round must not dedup, got: {lines}"
        )
        dispositions = {json.loads(line)["disposition"] for line in lines}
        assert dispositions == {"ADDRESS", "DEFER"}

    def test_authoring_agent_alone_discriminates_within_the_same_round(self, isolated_home, git_repo):
        """Same guard as test_disposition_alone_discriminates_within_the_same_round,
        for authoring_agent, the field author_outcome.py's join depends on.
        Existing dedup tests vary only disposition, so a filter narrowing
        that drops authoring_agent would pass them all while silently
        merging appends from different agents."""
        _seed_session(isolated_home, SID)
        _run(_append_args(round="1", authoring_agent="code-writer"), cwd=git_repo, home=isolated_home)
        result = _run(_append_args(round="1", authoring_agent="inline"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 2, (
            f"an authoring_agent-only difference within the same round must not dedup, got: {lines}"
        )
        agents = {json.loads(line)["authoring_agent"] for line in lines}
        assert agents == {"code-writer", "inline"}


_LIB_APPEND_JSON_LINE_LOCKED_DEFINITION_RE = re.compile(
    r"^(function\s+)?_lib_append_json_line_locked\s*\(\)"
)


def _iter_lib_append_json_line_locked_call_sites(root: Path = CLAUDE_DIR):
    """Yields (path, dedup_filter_arg) for every _lib_append_json_line_locked
    call site under ROOT (CLAUDE_DIR by default, overridable for a
    synthetic-fixture test), so the DEDUP_KEY_JQ_FILTER static-literal
    invariant this test class pins is checked across claude/.claude/ rather
    than only against review-ledger.sh's own text."""
    # Matched anywhere on a line rather than only at its start, so a call
    # inside `if _lib_append_json_line_locked ...; then`, `x=$(_lib_append_json_line_locked
    # ...)`, or after a `;` is still caught. This deliberately doesn't
    # encode shell command-word grammar to detect those shapes.
    # The trailing whitespace lookahead excludes a bare mention like
    # `# _lib_append_json_line_locked, and ...` or `printf
    # '_lib_append_json_line_locked: dedup check failed...'`, where the
    # identifier is immediately followed by punctuation rather than an
    # argument.
    call_re = re.compile(r"(?<![A-Za-z0-9_])_lib_append_json_line_locked(?=\s)")
    for sh_file in sorted(root.rglob("*.sh")):
        source = sh_file.read_text()
        for match in call_re.finditer(source):
            line_start = source.rfind("\n", 0, match.start()) + 1
            line_end = source.find("\n", match.start())
            line = source[line_start : line_end if line_end != -1 else len(source)]
            # Excludes a #-prefixed usage-comment line (e.g. the
            # `# _lib_append_json_line_locked FILE LOCK_FILE LINE ...`
            # header comment in _lib.sh), which the lookahead above doesn't
            # catch since it's followed by whitespace like a real call.
            if line.lstrip().startswith("#"):
                continue
            # Excludes the function's own definition line
            # (`_lib_append_json_line_locked() {` or `function
            # _lib_append_json_line_locked() {`, with or without a space
            # before the parens).
            if _LIB_APPEND_JSON_LINE_LOCKED_DEFINITION_RE.match(line.lstrip()):
                continue
            rest = source[match.start() :]
            lines = rest.splitlines(keepends=True)
            statement_lines = [lines[0]]
            idx = 0
            while statement_lines[-1].rstrip("\n").rstrip().endswith("\\") and idx + 1 < len(lines):
                idx += 1
                statement_lines.append(lines[idx])
            statement = re.sub(r"\\\s*\n", " ", "".join(statement_lines))
            # posix=False preserves each token's quote characters, which the
            # single-quoted-token extraction below depends on — the default
            # posix=True strips quotes and would break this silently.
            try:
                tokens = shlex.split(statement, posix=False)
            except ValueError as e:
                raise ValueError(f"{sh_file}: could not tokenize statement: {statement!r}") from e
            assert len(tokens) >= 5, (
                f"{sh_file}: _lib_append_json_line_locked call has fewer than 4 arguments: {statement!r}"
            )
            # The dedup-filter argument is the only positional argument the
            # contract requires to be single-quoted, so it's identified by
            # that shape rather than a fixed token index. A fixed index
            # breaks when an earlier double-quoted argument contains a
            # backslash-escaped `"`, since shlex.split(posix=False) doesn't
            # process escapes inside double quotes and splits that argument
            # into two tokens, shifting every later index by one.
            quoted_tokens = [t for t in tokens[1:] if t.startswith("'") and t.endswith("'")]
            assert len(quoted_tokens) == 1, (
                f"{sh_file}: expected exactly one single-quoted argument (the dedup filter) "
                f"in _lib_append_json_line_locked call, found {len(quoted_tokens)}: {statement!r}"
            )
            yield sh_file, quoted_tokens[0]


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

    def test_every_repo_call_site_passes_a_single_quoted_literal_with_no_expansion(self):
        """Regression-guards the static-literal invariant across
        claude/.claude/, not just for review-ledger.sh: a future caller of
        this primitive under claude/.claude/ is bound by the same `_lib.sh`
        contract with no other enforcement.
        Source-scanning is accepted here per test-conventions §9's
        wiring-presence carve-out; it's secondary to the behavioral coverage
        below."""
        call_sites = list(_iter_lib_append_json_line_locked_call_sites())
        assert call_sites, "no _lib_append_json_line_locked call sites found under claude/.claude/"
        for sh_file, filter_arg in call_sites:
            assert filter_arg.startswith("'") and filter_arg.endswith("'"), (
                f"{sh_file}: dedup filter argument must be a single-quoted literal, got: {filter_arg!r}"
            )
            assert "$" not in filter_arg and "`" not in filter_arg, (
                f"{sh_file}: dedup filter argument must contain no variable expansion "
                f"or command substitution, got: {filter_arg!r}"
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

    def test_multibyte_utf8_finding_round_trips_and_dedups_unmodified(self, isolated_home, git_repo):
        """Mirrors test_jq_filter_special_chars_in_finding_round_trip_unmodified
        but for multi-byte UTF-8 content: an accented character, CJK, and an
        emoji, each spanning more than one UTF-8 byte. This asserts the
        --arg-bound value round-trips exactly and that the
        round-scoped dedup key still collapses an identical retry when the
        finding contains non-ASCII bytes."""
        _seed_session(isolated_home, SID)
        multibyte = "café 日本語 🎉"
        result = _run(
            _append_args(finding=multibyte, rationale=multibyte),
            cwd=git_repo,
            home=isolated_home,
        )
        assert result.returncode == 0, result.stderr
        retry = _run(
            _append_args(finding=multibyte, rationale=multibyte),
            cwd=git_repo,
            home=isolated_home,
        )
        assert retry.returncode == 0, retry.stderr
        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()
        assert len(lines) == 1, (
            f"identical retry of a non-ASCII finding within the same round must dedup, got: {lines}"
        )
        record = json.loads(lines[0])
        assert record["finding"] == multibyte
        assert record["rationale"] == multibyte


class TestIterLibAppendJsonLineLockedCallSitesShapes:
    """Unit-level coverage of the call-site iterator's own matching rules,
    using a synthetic fixture under tmp_path rather than scanning
    CLAUDE_DIR. The repo's only real call site (review-ledger.sh's own
    plain statement-start call) doesn't exercise the mid-statement/
    assignment/chained shapes below, so it can't validate them on its
    own."""

    def test_matches_if_test_assignment_and_mid_statement_call_shapes(self, tmp_path: Path) -> None:
        """Covers three call shapes that don't start a line: an
        `if`-condition call, a command-substitution assignment, and a call
        chained after `;`."""
        fixture = tmp_path / "synthetic.sh"
        fixture.write_text(
            "if _lib_append_json_line_locked \"$f\" \"$l\" \"$line\" '.finding' ; then\n"
            "  :\n"
            "fi\n"
            "result=$(_lib_append_json_line_locked \"$f\" \"$l\" \"$line\" '.finding' )\n"
            "true; _lib_append_json_line_locked \"$f\" \"$l\" \"$line\" '.finding'\n"
        )
        call_sites = list(_iter_lib_append_json_line_locked_call_sites(root=tmp_path))
        assert len(call_sites) == 3, call_sites
        assert all(filter_arg == "'.finding'" for _sh_file, filter_arg in call_sites)

    def test_definition_line_with_space_before_parens_is_excluded(self, tmp_path: Path) -> None:
        """A definition written `_lib_append_json_line_locked () {` (valid
        bash, a space before the parens) must not be reported as a call
        site. This proves the definition-line exclusion actually fires: the
        lookahead alone doesn't reject this line, since it's followed by
        whitespace just like a real call."""
        fixture = tmp_path / "synthetic.sh"
        fixture.write_text(
            "_lib_append_json_line_locked () {\n"
            "  echo unused\n"
            "}\n"
        )
        call_sites = list(_iter_lib_append_json_line_locked_call_sites(root=tmp_path))
        assert call_sites == []

    def test_escaped_quote_in_early_argument_does_not_shift_filter_extraction(self, tmp_path: Path) -> None:
        """A backslash-escaped `"` inside an early (non-filter) double-quoted
        argument makes shlex.split(posix=False) split that argument into two
        tokens, since posix=False does no escape processing inside double
        quotes. This shifts every later token's index by one, so a
        fixed-position extraction would misidentify the filter argument. The
        iterator must instead find the filter argument by its own
        single-quoted shape, which this escape can't disturb."""
        fixture = tmp_path / "synthetic.sh"
        fixture.write_text(
            '_lib_append_json_line_locked "$f" "l\\"iteral" "$line" \'.finding\'\n'
        )
        call_sites = list(_iter_lib_append_json_line_locked_call_sites(root=tmp_path))
        assert call_sites == [(fixture, "'.finding'")]


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


class TestReviewLedgerOldSentinelIgnored:
    def test_sentinel_file_no_longer_suppresses_append(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        (isolated_home / ".claude" / ".review-narrative-ledger-disabled").touch()

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert _ledger_path(isolated_home, git_repo).exists(), (
            "the retired .review-narrative-ledger-disabled sentinel must not stop an append"
        )


class TestReviewLedgerLedgerScope:
    """append's file is the branch's on a feature branch and the session's on
    a detached HEAD or the default branch. The resolver matrix lives in
    test_lib_reviewer_round_state.py; these pin the script's wiring to it."""

    def test_default_branch_append_writes_the_session_file_and_says_so(self, isolated_home, git_repo):
        _rename_branch(git_repo, "main")
        _seed_session(isolated_home, SID)

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert _session_ledger_path(isolated_home, git_repo).exists()
        assert "session-scoped" in result.stderr

    def test_feature_branch_append_writes_the_branch_file_without_the_session_note(
        self, isolated_home, git_repo
    ):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        branch_hash = hashlib.sha256(b"feature").hexdigest()
        branch_file = isolated_home / ".claude" / "review-narrative-ledger" / f"{_repo_hash(git_repo)}.{branch_hash}.jsonl"
        assert branch_file.exists()
        assert not _session_ledger_path(isolated_home, git_repo).exists()
        assert "session-scoped" not in result.stderr

    def test_a_later_session_on_the_branch_reads_the_earlier_sessions_rows(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, "session-a")
        _run(_append_args(finding="raised in session a", round="1"), cwd=git_repo, home=isolated_home)

        _seed_session(isolated_home, "session-b")
        _run(_append_args(finding="raised in session b", round="2"), cwd=git_repo, home=isolated_home)
        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        # Both rows can land in the same second, so their event_time may tie
        # and the order between them is not pinned.
        assert sorted((row["finding"], row["session_id"]) for row in _shown_rows(result)) == [
            ("raised in session a", "session-a"),
            ("raised in session b", "session-b"),
        ]
        assert _show_header(result)["scope"] == "branch"

    def test_identical_finding_from_two_sessions_on_one_branch_lands_twice(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        for session_id in ("session-a", "session-b"):
            _seed_session(isolated_home, session_id)
            result = _run(_append_args(), cwd=git_repo, home=isolated_home)
            assert result.returncode == 0, result.stderr

        lines = _ledger_path(isolated_home, git_repo).read_text().splitlines()

        assert [json.loads(line)["session_id"] for line in lines] == ["session-a", "session-b"]

    def test_detached_head_show_prints_each_row_once_and_hides_branch_rows(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_append_args(finding="branch row"), cwd=git_repo, home=isolated_home)
        _git(git_repo, "checkout", "-q", "--detach")
        _run(_append_args(finding="detached row"), cwd=git_repo, home=isolated_home)

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert [row["finding"] for row in _shown_rows(result)] == ["detached row"]
        assert _show_header(result)["scope"] == "session"

    def test_branch_scope_show_also_reads_this_sessions_detached_head_rows(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_append_args(finding="branch row"), cwd=git_repo, home=isolated_home)
        _git(git_repo, "checkout", "-q", "--detach")
        _run(_append_args(finding="detached row"), cwd=git_repo, home=isolated_home)
        _git(git_repo, "checkout", "-q", "feature")

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert sorted(row["finding"] for row in _shown_rows(result)) == ["branch row", "detached row"]


def _ledger_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _commit_file(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"edit {name}")


def _engineer_args(**overrides) -> list[str]:
    """An engineer SETTLED that can carry forward: range-form source, quote, flag."""
    params = {
        "disposition": "SETTLED", "decided_by": "engineer", "source": "file.txt:1-2",
        "engineer_quote": "keep it as written", "carry_forward": True, "rationale": "engineer kept the text",
        "finding": "wording of the first two lines",
    }
    params.update(overrides)
    return _append_args(**params)


class TestReviewLedgerSettledAndDeferAppend:
    """One subprocess case per branch of the new append paths. The flag and
    source input matrix lives in scripts/tests/test_review_ledger_lib.py."""

    def test_engineer_settled_lands_its_fields_and_prints_its_stored_quote(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)

        result = _run(_engineer_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        (row,) = _ledger_rows(_ledger_path(isolated_home, git_repo))
        assert (row["disposition"], row["decided_by"], row["engineer_quote"]) == ("SETTLED", "engineer", "keep it as written")
        assert (row["carry_forward"], row["enforcement_invariant"], row["source"]) == (True, False, "file.txt:1-2")
        assert re.fullmatch(r"[0-9a-f]{12}", row["site_hash"])
        assert re.fullmatch(r"[0-9a-f]{12}", row["id"])
        assert 'stored engineer quote: "keep it as written"' in result.stdout
        assert "carry-forward: yes" in result.stdout

    def test_plan_architect_settled_takes_no_quote_and_prints_no_stored_quote(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = _append_args(
            disposition="SETTLED", decided_by="plan-architect", source="file.txt:1-2", finding="f", rationale="r",
        )

        result = _run(args, cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert "stored engineer quote" not in result.stdout
        assert _ledger_rows(_ledger_path(isolated_home, git_repo))[0]["decided_by"] == "plan-architect"

    def test_defer_rejection_names_both_flags_and_all_five_criteria(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)

        result = _run(_append_args(disposition="DEFER"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "--source" in result.stderr and "--defer-criterion" in result.stderr
        for criterion in _DEFER_CRITERION_NAMES:
            assert criterion in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_carry_forward_with_a_path_only_source_is_rejected_with_nothing_written(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)

        result = _run(_engineer_args(source="file.txt"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "range-form" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    @pytest.mark.parametrize(
        ("overrides", "fragment"),
        [
            pytest.param({"source": "file.txt"}, "range-form", id="a path-only source"),
            pytest.param({"rationale": ""}, "--rationale", id="no rationale"),
        ],
    )
    def test_a_carry_needs_a_range_form_source_and_a_rationale(self, isolated_home, git_repo, overrides, fragment):
        _seed_session(isolated_home, SID)
        args = _append_args(
            **{
                "disposition": "SETTLED", "decided_by": "carry", "ref": "0" * 12, "cited_line": "file.txt:1",
                "source": "file.txt:1-2", "finding": "f", "rationale": "same defect", **overrides,
            },
        )

        result = _run(args, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert fragment in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_a_source_naming_a_missing_file_is_rejected_with_nothing_written(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)

        result = _run(_defer_args(source="absent.txt:1-2"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "does not exist" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_the_same_quote_in_another_round_lands_and_an_identical_retry_dedups(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)

        _run(_engineer_args(round="1"), cwd=git_repo, home=isolated_home)
        _run(_engineer_args(round="1"), cwd=git_repo, home=isolated_home)
        assert len(_ledger_rows(ledger)) == 1, "an identical retry must dedup"

        _run(_engineer_args(round="2"), cwd=git_repo, home=isolated_home)
        assert [row["round"] for row in _ledger_rows(ledger)] == [1, 2]

    def test_the_same_finding_and_round_from_two_sessions_both_land(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        for session_id in ("session-a", "session-b"):
            _seed_session(isolated_home, session_id)
            result = _run(_engineer_args(), cwd=git_repo, home=isolated_home)
            assert result.returncode == 0, result.stderr

        rows = _ledger_rows(_ledger_path(isolated_home, git_repo))

        assert [row["session_id"] for row in rows] == ["session-a", "session-b"]

    def test_a_row_with_every_free_text_field_at_its_cap_in_four_byte_characters_is_accepted(
        self, isolated_home, git_repo
    ):
        """finding, rationale, quote and source at their caps, four bytes a
        character. A source is a real path, and one path component holds at
        most 255 bytes, so the source spreads its emoji over four nested names."""
        _seed_session(isolated_home, SID)
        emoji = "\U0001F600"
        finding_cap, rationale_cap, source_cap = _ROW_FIELD_CAPS
        quote_cap = 200
        range_suffix = ":1-2"
        separator_count = 3
        name_chars = source_cap - len(range_suffix) - separator_count
        names = [emoji * count for count in (name_chars // 4 + 1, name_chars // 4, name_chars // 4, name_chars // 4)]
        assert sum(len(name) for name in names) == name_chars
        nested_dir = git_repo.joinpath(*names[:-1])
        nested_dir.mkdir(parents=True)
        (nested_dir / names[-1]).write_text("first\nsecond\n")
        source = "/".join(names) + range_suffix
        assert len(source) == source_cap
        args = _engineer_args(
            finding=emoji * finding_cap, rationale=emoji * rationale_cap,
            engineer_quote=emoji * quote_cap, source=source,
        )
        free_text_bytes = 4 * (finding_cap + rationale_cap + quote_cap) + len(source.encode())

        result = _run(args, cwd=git_repo, home=isolated_home, extra_env={"LC_ALL": _utf8_locale()})

        assert result.returncode == 0, result.stderr
        written_line = _ledger_path(isolated_home, git_repo).read_text().splitlines()[0]
        assert free_text_bytes < len(written_line.encode()) <= 4095

    @pytest.mark.parametrize(("final_row_bytes", "accepted"), [(4095, True), (4096, False)])
    def test_the_size_check_counts_the_final_line_including_id_and_site_hash(
        self, isolated_home, git_repo, final_row_bytes, accepted
    ):
        """The envelope the fields are sized against holds the 12-digit site_hash
        and id, so a row one byte over the bound only counts as over when the
        check runs on the final line."""
        _seed_session(isolated_home, SID)
        finding, rationale, quote = _fields_for_row_of_bytes(
            final_row_bytes, disposition="SETTLED", decided_by="engineer", carry_forward=True,
            source="file.txt:1-2", site_hash="0" * 12,
        )

        result = _run(
            _engineer_args(finding=finding, rationale=rationale, engineer_quote=quote),
            cwd=git_repo, home=isolated_home,
        )

        if accepted:
            assert result.returncode == 0, result.stderr
            assert len(_ledger_path(isolated_home, git_repo).read_text().splitlines()[0].encode()) == final_row_bytes
        else:
            assert result.returncode == 2
            assert f"is {final_row_bytes} bytes" in result.stderr
            assert not _ledger_path(isolated_home, git_repo).exists()

    def test_a_script_copied_without_its_lib_exits_2_naming_install(self, isolated_home, git_repo, tmp_path):
        scripts_copy = tmp_path / "copy" / "scripts"
        scripts_copy.mkdir(parents=True)
        shutil.copy(REVIEW_LEDGER_SCRIPT, scripts_copy / "review-ledger.sh")
        (tmp_path / "copy" / "hooks").symlink_to(CLAUDE_DIR / "hooks")
        _seed_session(isolated_home, SID)
        env = {**os.environ, "HOME": str(isolated_home)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        result = subprocess.run(
            ["bash", str(scripts_copy / "review-ledger.sh")] + _append_args(),
            cwd=git_repo, env=env, capture_output=True, text=True,
        )

        assert result.returncode == 2
        assert "./install.sh" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()


def _slug(phrase: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")


def _skill_defer_criterion_names() -> list[str]:
    """The five criteria of code-review/SKILL.md's closed DEFER list, each
    named by the slug of its bold lead phrase up to the first comma. Empty
    when the SKILL.md markers are gone, so a reworded skill fails the parity
    tests below instead of failing collection of this whole file."""
    skill_text = (SKILLS_DIR / "code-review" / "SKILL.md").read_text()
    try:
        section = skill_text.split("**DEFER criteria (closed list).**")[1].split("**Invalid DEFER rationales.**")[0]
    except IndexError:
        return []
    return [_slug(lead.split(",")[0]) for lead in re.findall(r"^\d\. \*\*(.+?)\*\*", section, re.MULTILINE)]


_DEFER_CRITERION_NAMES = _skill_defer_criterion_names()


class TestReviewLedgerEnumParity:
    """Drives the CLI rather than scanning the script's source, per
    test-conventions section 9."""

    def test_skill_md_lists_exactly_five_criteria(self):
        assert len(_DEFER_CRITERION_NAMES) == 5

    @pytest.mark.parametrize("criterion", _DEFER_CRITERION_NAMES)
    def test_each_skill_md_criterion_is_accepted(self, isolated_home, git_repo, criterion):
        _seed_session(isolated_home, SID)

        result = _run(_defer_args(defer_criterion=criterion), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr

    def test_a_criterion_outside_skill_md_is_rejected(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)

        result = _run(_defer_args(defer_criterion="not-a-criterion"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_the_names_the_script_lists_are_exactly_the_skill_md_names(self, isolated_home, git_repo):
        """Both directions: a sixth name in the script, or a stale old one, fails here."""
        _seed_session(isolated_home, SID)

        result = _run(_defer_args(defer_criterion="not-a-criterion"), cwd=git_repo, home=isolated_home)

        listing = result.stderr.split("one of: ", 1)[1].splitlines()[0]
        script_names = {name.strip() for name in listing.split(",")}
        assert script_names == set(_DEFER_CRITERION_NAMES)
        assert len(_DEFER_CRITERION_NAMES) == len(set(_DEFER_CRITERION_NAMES))

    def test_the_usage_text_lists_the_same_criteria_the_script_accepts(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        rejection = _run(_defer_args(defer_criterion="not-a-criterion"), cwd=git_repo, home=isolated_home)
        accepted_names = {name.strip() for name in rejection.stderr.split("one of: ", 1)[1].splitlines()[0].split(",")}

        usage = _run(["--help"], cwd=git_repo, home=isolated_home).stderr

        usage_listing = usage.split("--defer-criterion, one of:", 1)[1].split("SETTLED requires", 1)[0]
        usage_names = {name.strip().rstrip(".") for name in " ".join(usage_listing.split()).split(",")}
        assert usage_names == accepted_names

    def test_every_disposition_the_analysis_counts_is_accepted(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        address = _run(_append_args(disposition=ao._DISPOSITION_ADDRESS), cwd=git_repo, home=isolated_home)
        settled = _run(_engineer_args(disposition=ao._DISPOSITION_SETTLED), cwd=git_repo, home=isolated_home)

        assert address.returncode == 0, address.stderr
        assert settled.returncode == 0, settled.stderr


class TestReviewLedgerCarryAcrossSessions:
    """Session A decides, session B carries the decision on an unchanged block."""

    def _a_keeps_then_b_carries(self, isolated_home, git_repo) -> subprocess.CompletedProcess:
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, "session-a")
        a_result = _run(_engineer_args(round="1"), cwd=git_repo, home=isolated_home)
        assert a_result.returncode == 0, a_result.stderr
        (decision,) = _ledger_rows(_ledger_path(isolated_home, git_repo))
        _seed_session(isolated_home, "session-b")
        return _run(
            _append_args(
                disposition="SETTLED", decided_by="carry", ref=decision["id"], round="2",
                finding="the same wording defect, raised again", rationale="same failure mode as the kept finding",
                source="file.txt:1-2", cited_line="file.txt:2",
            ),
            cwd=git_repo, home=isolated_home,
        )

    def test_b_carries_a_decision_and_the_render_shows_a_words_with_b_row_beneath(self, isolated_home, git_repo):
        carry_result = self._a_keeps_then_b_carries(isolated_home, git_repo)

        assert carry_result.returncode == 0, carry_result.stderr
        assert "carry of decision" in carry_result.stdout
        assert 'engineer quote: "keep it as written"' in carry_result.stdout
        assert "reopen" in carry_result.stdout
        rows = _ledger_rows(_ledger_path(isolated_home, git_repo))
        assert [row["session_id"] for row in rows] == ["session-a", "session-b"]
        assert rows[1]["site_hash"] == rows[0]["site_hash"]

        render = _run(["render"], cwd=git_repo, home=isolated_home)

        assert render.returncode == 0, render.stderr
        table_lines = [line for line in render.stdout.splitlines() if line.startswith("| ")]
        decision_index = next(i for i, line in enumerate(table_lines) if "engineer: ` keep it as written `" in line)
        assert f"orchestrator-matched carry of {rows[0]['id']}" in table_lines[decision_index + 1]
        assert "session-a" not in render.stdout and "session-b" not in render.stdout
        assert rows[0]["site_hash"] not in render.stdout

    def test_an_intervening_commit_to_the_block_makes_b_carry_rejected(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, "session-a")
        _run(_engineer_args(round="1"), cwd=git_repo, home=isolated_home)
        (decision,) = _ledger_rows(_ledger_path(isolated_home, git_repo))
        _commit_file(git_repo, "file.txt", "first\nsecond, reworded\n")
        _seed_session(isolated_home, "session-b")

        result = _run(
            _append_args(
                disposition="SETTLED", decided_by="carry", ref=decision["id"], round="2",
                finding="again", rationale="same", source="file.txt:1-2", cited_line="file.txt:2",
            ),
            cwd=git_repo, home=isolated_home,
        )

        assert result.returncode == 2
        assert "no longer matches the decided block" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 1


class TestReviewLedgerRenderAndRefReadOnlyTheResolvedFile:
    """In branch scope `show` merges this worktree's session file, but `render`
    and a --ref read only the branch file: a session-file row is not one of the
    branch's decisions and must not reach a PR body."""

    def _session_only_decision(self, isolated_home, git_repo) -> dict:
        """Log a DEFER on a detached HEAD (into the session file) and return
        to the feature branch, where the branch file holds no such row."""
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _git(git_repo, "checkout", "-q", "--detach")
        result = _run(_defer_args(finding="a finding only the session file holds"), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        (decision,) = _ledger_rows(_session_ledger_path(isolated_home, git_repo))
        _git(git_repo, "checkout", "-q", "feature")
        return decision

    def test_show_merges_the_session_only_decision_but_render_ignores_it(self, isolated_home, git_repo):
        self._session_only_decision(isolated_home, git_repo)

        show = _run(["show"], cwd=git_repo, home=isolated_home)
        render = _run(["render"], cwd=git_repo, home=isolated_home)

        assert [row["finding"] for row in _shown_rows(show)] == ["a finding only the session file holds"]
        assert render.returncode == 0, render.stderr
        assert render.stdout == ""

    def test_a_ref_to_the_session_only_decision_is_rejected_as_not_in_the_branch_ledger(
        self, isolated_home, git_repo
    ):
        decision = self._session_only_decision(isolated_home, git_repo)

        result = _run(_append_args(ref=decision["id"]), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "not in this branch's ledger" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_in_session_scope_a_ref_reads_the_session_file_and_says_session(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_defer_args(), cwd=git_repo, home=isolated_home)
        (decision,) = _ledger_rows(_session_ledger_path(isolated_home, git_repo))

        accepted = _run(_append_args(ref=decision["id"]), cwd=git_repo, home=isolated_home)
        dangling = _run(_append_args(ref="0" * 12, finding="other"), cwd=git_repo, home=isolated_home)

        assert accepted.returncode == 0, accepted.stderr
        assert dangling.returncode == 2
        assert "not in this session's ledger" in dangling.stderr

    def test_render_skips_a_torn_line_and_still_renders_the_intact_rows(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_defer_args(finding="intact decision"), cwd=git_repo, home=isolated_home)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.write_text('{"schema_version":4,"round":2,"finding":"torn\n' + ledger.read_text())

        result = _run(["render"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert "intact decision" in result.stdout

    def test_render_out_writes_the_block_and_pr_json_reports_changed_then_unchanged(
        self, isolated_home, git_repo, tmp_path
    ):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_defer_args(), cwd=git_repo, home=isolated_home)
        out_path = git_repo / "agent-reviews" / "pr-body.md"
        pr_json = json.dumps({"body": "Existing description"})

        def render_into_out() -> subprocess.CompletedProcess:
            pr_json_file = tmp_path / "pr.json"
            pr_json_file.write_text(pr_json)
            return _run(["render", "--pr-json", str(pr_json_file), "--out", str(out_path)], cwd=git_repo, home=isolated_home)

        first = render_into_out()
        assert first.returncode == 0, first.stderr
        assert first.stdout.strip() == f"changed: {out_path}"
        assert out_path.read_text().startswith("Existing description\n\n<!-- code-review:deferred:start -->")

        pr_json = json.dumps({"body": out_path.read_text()})
        second = render_into_out()
        assert second.stdout.strip() == "unchanged"
        assert not out_path.exists()


def _logged_id(home: Path, repo: Path) -> str:
    """The id of the one row in the resolved ledger file."""
    (row,) = _ledger_rows(_ledger_path(home, repo))
    return row["id"]


class TestReviewLedgerRenderOutConfinement:
    """`render --out` deletes its target first, so it accepts only a regular
    file directly under the repo's own agent-reviews directory."""

    @pytest.fixture
    def logged_defer(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        result = _run(_defer_args(), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        (git_repo / "agent-reviews").mkdir()
        return git_repo

    @pytest.mark.parametrize("form", ["relative", "absolute"])
    def test_a_file_directly_under_agent_reviews_is_written(self, isolated_home, logged_defer, form):
        out = "agent-reviews/digest.md" if form == "relative" else str(logged_defer / "agent-reviews" / "digest.md")

        result = _run(["render", "--out", out], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert result.stdout == f"changed: {out}\n"
        assert (logged_defer / "agent-reviews" / "digest.md").read_text().startswith("<!-- code-review:deferred:start -->")

    @pytest.mark.parametrize(
        "out",
        [
            pytest.param("file.txt", id="a tracked file outside agent-reviews"),
            pytest.param("agent-reviews/../file.txt", id="a dot-dot segment back out"),
            pytest.param("agent-reviews/nested/digest.md", id="a subdirectory of agent-reviews"),
            pytest.param("agent-reviews", id="the agent-reviews directory itself"),
        ],
    )
    def test_a_target_outside_agent_reviews_is_rejected_and_nothing_is_deleted(self, isolated_home, logged_defer, out):
        tracked_before = (logged_defer / "file.txt").read_text()

        result = _run(["render", "--out", out], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 2
        assert "agent-reviews" in result.stderr
        assert (logged_defer / "file.txt").read_text() == tracked_before
        assert (logged_defer / "agent-reviews").is_dir()

    def test_an_absolute_target_outside_the_repo_is_rejected(self, isolated_home, logged_defer, tmp_path):
        outside = tmp_path / "outside.md"
        outside.write_text("keep me")

        result = _run(["render", "--out", str(outside)], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 2
        assert outside.read_text() == "keep me"

    def test_an_existing_directory_target_is_rejected_and_left_in_place(self, isolated_home, logged_defer):
        directory = logged_defer / "agent-reviews" / "adir"
        directory.mkdir()
        (directory / "inner.md").write_text("inner")

        result = _run(["render", "--out", "agent-reviews/adir"], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 2
        assert "not a regular file" in result.stderr
        assert (directory / "inner.md").read_text() == "inner"

    def test_a_symlink_target_is_rejected_and_its_referent_is_untouched(self, isolated_home, logged_defer):
        tracked_before = (logged_defer / "file.txt").read_text()
        link = logged_defer / "agent-reviews" / "link.md"
        link.symlink_to(logged_defer / "file.txt")

        result = _run(["render", "--out", "agent-reviews/link.md"], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 2
        assert "symlink" in result.stderr
        assert link.is_symlink()
        assert (logged_defer / "file.txt").read_text() == tracked_before

    def test_a_symlinked_agent_reviews_directory_is_rejected(self, isolated_home, logged_defer, tmp_path):
        (logged_defer / "agent-reviews").rmdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (logged_defer / "agent-reviews").symlink_to(elsewhere)

        result = _run(["render", "--out", "agent-reviews/digest.md"], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 2
        assert "real directory" in result.stderr
        assert list(elsewhere.iterdir()) == []

    @pytest.mark.parametrize("alias", ["same path", "hard link"])
    def test_the_pr_json_file_is_never_the_out_target(self, isolated_home, logged_defer, alias):
        pr_json = logged_defer / "agent-reviews" / "pr.json"
        pr_json.write_text(json.dumps({"body": "Existing description"}))
        out = "agent-reviews/pr.json"
        if alias == "hard link":
            os.link(pr_json, logged_defer / "agent-reviews" / "alias.md")
            out = "agent-reviews/alias.md"

        result = _run(["render", "--pr-json", "agent-reviews/pr.json", "--out", out], cwd=logged_defer, home=isolated_home)

        assert result.returncode == 2
        assert "same file" in result.stderr
        assert json.loads(pr_json.read_text()) == {"body": "Existing description"}

    def test_a_stale_out_file_is_removed_when_a_later_step_exits_2(self, isolated_home, git_repo):
        """No session is seeded, so the session lookup exits 2 after the arguments are checked."""
        (git_repo / "agent-reviews").mkdir()
        stale = git_repo / "agent-reviews" / "body.md"
        stale.write_text("previous round's body")

        result = _run(["render", "--out", "agent-reviews/body.md"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert not stale.exists()

    def test_a_stale_out_file_that_cannot_be_deleted_aborts_with_exit_1_and_no_changed_line(
        self, isolated_home, logged_defer
    ):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        agent_reviews = logged_defer / "agent-reviews"
        stale = agent_reviews / "body.md"
        stale.write_text("previous round's body")
        agent_reviews.chmod(0o500)
        try:
            result = _run(["render", "--out", "agent-reviews/body.md"], cwd=logged_defer, home=isolated_home)
        finally:
            agent_reviews.chmod(0o700)

        assert result.returncode == 1
        assert "could not delete" in result.stderr
        assert "changed:" not in result.stdout

    def test_a_delete_that_fails_aborts_with_exit_1_and_no_changed_line_even_as_root(self, isolated_home, logged_defer, tmp_path):
        stale = logged_defer / "agent-reviews" / "body.md"
        stale.write_text("previous round's body")
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        (fake_bin / "rm").write_text("#!/bin/bash\nexit 1\n")
        (fake_bin / "rm").chmod(0o755)

        result = _run(
            ["render", "--out", "agent-reviews/body.md"], cwd=logged_defer, home=isolated_home,
            extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert "could not delete" in result.stderr
        assert "changed:" not in result.stdout
        assert stale.read_text() == "previous round's body"

    @pytest.mark.parametrize("kind", ["missing file", "directory"])
    def test_a_pr_json_path_that_cannot_be_read_exits_1_and_leaves_no_out_file(self, isolated_home, logged_defer, kind):
        stale = logged_defer / "agent-reviews" / "body.md"
        stale.write_text("previous round's body")
        pr_json = logged_defer / "pr.json"
        if kind == "directory":
            pr_json.mkdir()

        result = _run(
            ["render", "--pr-json", "pr.json", "--out", "agent-reviews/body.md"],
            cwd=logged_defer, home=isolated_home,
        )

        assert result.returncode == 1
        assert "could not read --pr-json file" in result.stderr
        assert not stale.exists()


# The fields the append's dedup key names, and a value for each that differs from
# the engineer SETTLED row _engineer_args logs.
_DEDUP_KEY_CHANGES = {
    "round": 99, "finding": "other", "disposition": "DEFER", "rationale": "other", "source": "other.txt:1",
    "authoring_agent": "mixed", "authoring_effort": "high", "session_id": "other-session",
    "decided_by": "plan-architect", "engineer_quote": "other quote", "enforcement_invariant": True,
    "carry_forward": False, "defer_criterion": _DEFER_CRITERION, "ref": "f" * 12, "cited_line": "file.txt:1",
    "site_hash": "f" * 12,
}
# The fields the dedup key leaves out, because they vary on every attempt.
_DEDUP_KEY_EXEMPT_CHANGES = {"id": "f" * 12, "event_time": "2001-01-01T00:00:00Z", "schema_version": 3}


class TestReviewLedgerRefRetry:
    """A retry of a --ref append whose first attempt landed is a no-op. Any
    other append to a retired decision is still rejected."""

    def _log_decision(self, isolated_home, git_repo) -> str:
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        result = _run(_defer_args(), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        return _logged_id(isolated_home, git_repo)

    @pytest.mark.parametrize(
        "retirer_args",
        [
            pytest.param({"disposition": "ADDRESS", "rationale": "fixed inline"}, id="ADDRESS"),
            pytest.param(
                {"disposition": "DEFER", "source": "file.txt:1-2", "defer_criterion": "edge-case-below-current-scale"},
                id="fresh DEFER",
            ),
            pytest.param(
                {"disposition": "SETTLED", "decided_by": "plan-architect", "source": "file.txt:1-2"},
                id="consult SETTLED",
            ),
        ],
    )
    def test_an_identical_retry_exits_0_and_adds_no_row(self, isolated_home, git_repo, retirer_args):
        decision_id = self._log_decision(isolated_home, git_repo)
        ledger = _ledger_path(isolated_home, git_repo)
        args = _append_args(ref=decision_id, round="2", finding="the retiring row", **retirer_args)

        first = _run(args, cwd=git_repo, home=isolated_home)
        retry = _run(args, cwd=git_repo, home=isolated_home)

        assert first.returncode == 0, first.stderr
        assert retry.returncode == 0, retry.stderr
        assert len(_ledger_rows(ledger)) == 2

    def test_an_identical_retry_of_an_engineer_settled_exits_0_and_adds_no_row(self, isolated_home, git_repo):
        decision_id = self._log_decision(isolated_home, git_repo)
        args = _engineer_args(ref=decision_id, round="2", carry_forward=False)

        first = _run(args, cwd=git_repo, home=isolated_home)
        retry = _run(args, cwd=git_repo, home=isolated_home)

        assert (first.returncode, retry.returncode) == (0, 0), retry.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 2

    def test_a_different_row_naming_the_retired_decision_is_still_rejected(self, isolated_home, git_repo):
        decision_id = self._log_decision(isolated_home, git_repo)
        _run(_append_args(ref=decision_id, round="2", rationale="fixed inline"), cwd=git_repo, home=isolated_home)

        different = _append_args(ref=decision_id, round="2", rationale="fixed some other way")

        result = _run(different, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "no longer live" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 2

    def test_the_same_content_from_another_session_is_still_rejected(self, isolated_home, git_repo):
        decision_id = self._log_decision(isolated_home, git_repo)
        args = _append_args(ref=decision_id, round="2", rationale="fixed inline")
        _run(args, cwd=git_repo, home=isolated_home)
        _seed_session(isolated_home, "session-b")

        result = _run(args, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "no longer live" in result.stderr

    def test_a_carry_of_a_retired_decision_is_rejected(self, isolated_home, git_repo):
        decision_id = self._log_decision(isolated_home, git_repo)
        carry = _append_args(
            disposition="DEFER", decided_by="carry", ref=decision_id, round="2", finding="again", rationale="same",
            source="file.txt:1-2", cited_line="file.txt:1", defer_criterion=_DEFER_CRITERION,
        )
        _run(_append_args(ref=decision_id, round="2", rationale="fixed inline"), cwd=git_repo, home=isolated_home)

        result = _run(carry, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "no longer live" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 2

    def _retry_after_changing_one_field(self, isolated_home, git_repo, field: str, value) -> tuple:
        """Logs a DEFER and an engineer SETTLED that retires it, then rewrites the
        ledger to hold the DEFER, a different retirer that keeps it retired, and a
        copy of the first retirer that differs in `field` alone. Returns the result
        of repeating the first retirer's append, and the ledger path."""
        decision_id = self._log_decision(isolated_home, git_repo)
        ledger = _ledger_path(isolated_home, git_repo)
        retirer_args = _engineer_args(ref=decision_id, round="2")
        assert _run(retirer_args, cwd=git_repo, home=isolated_home).returncode == 0
        decision, retirer = _ledger_rows(ledger)
        other_retirer = {**retirer, "id": "e" * 12, "finding": "a different retiring row"}
        rewritten = [decision, other_retirer, {**retirer, field: value}]
        ledger.write_text("".join(json.dumps(row) + "\n" for row in rewritten))

        return _run(retirer_args, cwd=git_repo, home=isolated_home), ledger

    def test_the_dedup_key_field_tables_name_every_field_of_a_row_the_append_writes(self, isolated_home, git_repo):
        decision_id = self._log_decision(isolated_home, git_repo)
        assert _run(_engineer_args(ref=decision_id, round="2"), cwd=git_repo, home=isolated_home).returncode == 0

        _decision, retirer = _ledger_rows(_ledger_path(isolated_home, git_repo))

        assert set(retirer) == set(_DEDUP_KEY_CHANGES) | set(_DEDUP_KEY_EXEMPT_CHANGES)

    @pytest.mark.parametrize("field", sorted(_DEDUP_KEY_CHANGES))
    def test_a_retirer_differing_in_a_dedup_key_field_is_not_a_retry(self, isolated_home, git_repo, field):
        result, ledger = self._retry_after_changing_one_field(
            isolated_home, git_repo, field, _DEDUP_KEY_CHANGES[field],
        )

        assert result.returncode == 2, f"a retirer differing only in {field} must not be taken for a retry"
        assert "no longer live" in result.stderr
        assert len(_ledger_rows(ledger)) == 3

    @pytest.mark.parametrize("field", sorted(_DEDUP_KEY_EXEMPT_CHANGES))
    def test_a_retirer_differing_only_in_a_per_attempt_field_is_a_retry_that_adds_no_row(
        self, isolated_home, git_repo, field
    ):
        result, ledger = self._retry_after_changing_one_field(
            isolated_home, git_repo, field, _DEDUP_KEY_EXEMPT_CHANGES[field],
        )

        assert result.returncode == 0, result.stderr
        assert len(_ledger_rows(ledger)) == 3


# Appends that may not supersede an engineer decision: a DEFER and a consult SETTLED.
_NON_ENGINEER_SUCCESSORS = [
    pytest.param(
        {"disposition": "DEFER", "source": "file.txt:1-2", "defer_criterion": _DEFER_CRITERION}, id="DEFER",
    ),
    pytest.param(
        {"disposition": "SETTLED", "decided_by": "plan-architect", "source": "file.txt:1-2"}, id="consult SETTLED",
    ),
]


class TestReviewLedgerEngineerDecisionProtection:
    """An agent calling the CLI cannot overwrite an engineer's decision with a
    DEFER or a consult SETTLED, and an invariant decision keeps its label."""

    def _log_engineer_decision(self, isolated_home, git_repo, **overrides) -> str:
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        result = _run(_engineer_args(carry_forward=False, **overrides), cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        return _logged_id(isolated_home, git_repo)

    def test_enforcement_invariant_lands_in_the_stored_row_and_the_printed_note(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)

        result = _run(_engineer_args(carry_forward=False, enforcement_invariant=True), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        (row,) = _ledger_rows(_ledger_path(isolated_home, git_repo))
        assert (row["enforcement_invariant"], row["carry_forward"], row["decided_by"]) == (True, False, "engineer")
        assert "enforcement-invariant: asked again on every repeat" in result.stdout

    @pytest.mark.parametrize("successor_args", _NON_ENGINEER_SUCCESSORS)
    def test_a_live_engineer_decision_rejects_a_non_engineer_successor_with_no_row_added(
        self, isolated_home, git_repo, successor_args
    ):
        decision_id = self._log_engineer_decision(isolated_home, git_repo)
        successor = _append_args(ref=decision_id, round="2", finding="the successor", rationale="r", **successor_args)

        result = _run(successor, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "is an engineer's" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 1

    def test_an_unlabelled_engineer_settled_naming_a_live_invariant_decision_is_rejected(
        self, isolated_home, git_repo
    ):
        decision_id = self._log_engineer_decision(isolated_home, git_repo, enforcement_invariant=True)

        result = _run(
            _engineer_args(ref=decision_id, round="2", carry_forward=False), cwd=git_repo, home=isolated_home,
        )

        assert result.returncode == 2
        assert "--enforcement-invariant" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 1

    def test_a_labelled_engineer_settled_naming_a_live_invariant_decision_lands(self, isolated_home, git_repo):
        decision_id = self._log_engineer_decision(isolated_home, git_repo, enforcement_invariant=True)

        result = _run(
            _engineer_args(ref=decision_id, round="2", carry_forward=False, enforcement_invariant=True),
            cwd=git_repo, home=isolated_home,
        )

        assert result.returncode == 0, result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 2

    @pytest.mark.parametrize("successor_args", _NON_ENGINEER_SUCCESSORS)
    def test_an_engineer_decision_an_address_retired_rejects_a_non_engineer_successor_as_no_longer_live(
        self, isolated_home, git_repo, successor_args
    ):
        decision_id = self._log_engineer_decision(isolated_home, git_repo)
        retirer = _append_args(ref=decision_id, round="2", rationale="fixed inline")
        assert _run(retirer, cwd=git_repo, home=isolated_home).returncode == 0
        successor = _append_args(ref=decision_id, round="3", finding="the successor", rationale="r", **successor_args)

        result = _run(successor, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "no longer live" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 2


class TestReviewLedgerDedupKey:
    """Each test makes an existing row differ from the row the append builds in
    exactly one field, then appends that row again."""

    def _append_after_changing_one_field(self, isolated_home, git_repo, field: str, value) -> list[dict]:
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        assert _run(_engineer_args(), cwd=git_repo, home=isolated_home).returncode == 0
        (row,) = _ledger_rows(ledger)
        row[field] = value
        ledger.write_text(json.dumps(row) + "\n")

        retry = _run(_engineer_args(), cwd=git_repo, home=isolated_home)

        assert retry.returncode == 0, retry.stderr
        return _ledger_rows(ledger)

    @pytest.mark.parametrize("field", sorted(_DEDUP_KEY_CHANGES))
    def test_a_row_differing_in_a_key_field_is_a_new_row(self, isolated_home, git_repo, field):
        rows = self._append_after_changing_one_field(isolated_home, git_repo, field, _DEDUP_KEY_CHANGES[field])

        assert len(rows) == 2, f"a row differing only in {field} must not dedup"

    @pytest.mark.parametrize("field", sorted(_DEDUP_KEY_EXEMPT_CHANGES))
    def test_a_row_differing_only_in_a_per_attempt_field_dedups(self, isolated_home, git_repo, field):
        rows = self._append_after_changing_one_field(isolated_home, git_repo, field, _DEDUP_KEY_EXEMPT_CHANGES[field])

        assert len(rows) == 1, f"{field} varies on every attempt, so it must not be part of the dedup key"


def _decide_then_carry_as_another_session(isolated_home, git_repo, decision_args, carry_overrides):
    """Session A logs `decision_args` on a feature branch, then session B appends a
    DEFER carry of it, with `carry_overrides` replacing the carry's flags.
    Returns B's result and the decision's id."""
    _git(git_repo, "checkout", "-q", "-b", "feature")
    _seed_session(isolated_home, "session-a")
    assert _run(decision_args, cwd=git_repo, home=isolated_home).returncode == 0
    decision_id = _logged_id(isolated_home, git_repo)
    _seed_session(isolated_home, "session-b")
    carry = {
        "disposition": "DEFER", "decided_by": "carry", "ref": decision_id, "round": "2", "finding": "raised again",
        "rationale": "same failure mode", "source": "file.txt:1-2", "cited_line": "file.txt:1",
        "defer_criterion": _DEFER_CRITERION, **carry_overrides,
    }
    return _run(_append_args(**carry), cwd=git_repo, home=isolated_home), decision_id


class TestReviewLedgerCarryViaCli:
    def test_a_defer_carry_restating_the_criterion_is_accepted_and_prints_the_criterion_as_its_basis(
        self, isolated_home, git_repo
    ):
        result, decision_id = _decide_then_carry_as_another_session(
            isolated_home, git_repo, _defer_args(), {},
        )

        assert result.returncode == 0, result.stderr
        assert f"carry of decision {decision_id}" in result.stdout
        assert f"DEFER ({_DEFER_CRITERION})" in result.stdout
        decision, carry = _ledger_rows(_ledger_path(isolated_home, git_repo))
        assert (carry["decided_by"], carry["defer_criterion"], carry["ref"]) == ("carry", _DEFER_CRITERION, decision_id)
        assert carry["site_hash"] == decision["site_hash"]

    def test_a_defer_carry_with_another_criterion_is_rejected_with_nothing_written(self, isolated_home, git_repo):
        result, _decision_id = _decide_then_carry_as_another_session(
            isolated_home, git_repo, _defer_args(), {"defer_criterion": "edge-case-below-current-scale"},
        )

        assert result.returncode == 2
        assert "differs from the decision's" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 1

    def test_a_carry_of_an_engineer_decision_logged_without_carry_forward_is_rejected(self, isolated_home, git_repo):
        result, _decision_id = _decide_then_carry_as_another_session(
            isolated_home, git_repo, _engineer_args(carry_forward=False),
            {"disposition": "SETTLED", "defer_criterion": None},
        )

        assert result.returncode == 2
        assert "--carry-forward" in result.stderr
        assert len(_ledger_rows(_ledger_path(isolated_home, git_repo))) == 1


class TestReviewLedgerLocationStorage:
    def test_a_non_canonical_source_and_cited_line_are_stored_repo_relative(self, isolated_home, git_repo):
        repo_root = git_toplevel(git_repo)
        result, decision_id = _decide_then_carry_as_another_session(
            isolated_home, git_repo, _defer_args(source="./file.txt:1-2"),
            {"source": f"{repo_root}/file.txt:1-2", "cited_line": f"{repo_root}/./file.txt:2"},
        )

        assert result.returncode == 0, result.stderr
        decision, carry = _ledger_rows(_ledger_path(isolated_home, git_repo))
        assert decision["source"] == "file.txt:1-2"
        assert (carry["source"], carry["cited_line"]) == ("file.txt:1-2", "file.txt:2")
        assert decision_id == carry["ref"]

    def test_a_cited_line_of_exactly_the_cap_is_accepted_and_one_over_is_rejected(self, isolated_home, git_repo):
        long_name = "d" * 192 + ".txt"
        (git_repo / long_name).write_text("first\nsecond\n")
        cap_spec = f"{long_name}:1-2"
        assert len(cap_spec) == 200
        result, _decision_id = _decide_then_carry_as_another_session(
            isolated_home, git_repo, _defer_args(source=cap_spec),
            {"source": cap_spec, "cited_line": cap_spec},
        )
        over = _run(
            _append_args(
                disposition="DEFER", decided_by="carry", ref="0" * 12, finding="f", rationale="r", source=cap_spec,
                cited_line="e" + cap_spec, defer_criterion=_DEFER_CRITERION,
            ),
            cwd=git_repo, home=isolated_home,
        )

        assert result.returncode == 0, result.stderr
        assert over.returncode == 2
        assert "--cited-line exceeds 200 characters" in over.stderr

    @pytest.mark.parametrize("bad_path", ["a\tb.txt", "a\nb.txt"], ids=["tab", "newline"])
    def test_a_control_character_in_a_source_path_is_rejected_with_nothing_written(
        self, isolated_home, git_repo, bad_path
    ):
        _seed_session(isolated_home, SID)

        result = _run(_defer_args(source=f"{bad_path}:1"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "control character" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_a_source_that_is_a_symlink_out_of_the_repo_is_rejected_with_nothing_written(
        self, isolated_home, git_repo, tmp_path
    ):
        _seed_session(isolated_home, SID)
        outside = tmp_path / "outside.txt"
        outside.write_text("first\nsecond\n")
        (git_repo / "link.txt").symlink_to(outside)

        result = _run(_defer_args(source="link.txt:1-2"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "resolves outside the repository" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_a_source_that_is_a_symlink_to_a_file_in_the_repo_is_accepted(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        (git_repo / "alias.txt").symlink_to("file.txt")

        result = _run(_defer_args(source="alias.txt:1-2"), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr


class TestReviewLedgerCreationMode:
    def test_the_ledger_directory_is_created_0700_and_the_ledger_file_0600(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        ledger = _ledger_path(isolated_home, git_repo)
        assert stat.S_IMODE(ledger.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


class TestReviewLedgerRowSizeBound:
    """The built JSON line must fit the 4096-byte atomic-append bound:
    jq writes a control character with no short escape as six bytes, so the
    per-field character caps alone do not bound it."""

    _FIELD_CAPS = {"finding": 200, "rationale": 300, "source": 200}

    def test_control_characters_at_every_cap_are_rejected_with_nothing_written(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = _append_args(
            finding="\x01" * self._FIELD_CAPS["finding"],
            rationale="\x01" * self._FIELD_CAPS["rationale"],
            source="\x01" * self._FIELD_CAPS["source"],
        )

        result = _run(args, cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "4095-byte limit" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_row_of_exactly_4095_bytes_is_accepted(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        finding, rationale, source = _fields_for_row_of_bytes(4095)

        result = _run(_append_args(finding=finding, rationale=rationale, source=source), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        written_line = _ledger_path(isolated_home, git_repo).read_text().splitlines()[0]
        assert len(written_line.encode()) == 4095
        assert json.loads(written_line)["finding"] == finding

    def test_row_of_4096_bytes_is_rejected_naming_the_fields_to_shorten(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        finding, rationale, source = _fields_for_row_of_bytes(4096)

        result = _run(_append_args(finding=finding, rationale=rationale, source=source), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "is 4096 bytes" in result.stderr
        assert "4095-byte limit" in result.stderr
        for flag in ("--finding", "--rationale", "--source"):
            assert flag in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_unmeasurable_row_length_is_rejected_with_nothing_written(self, isolated_home, git_repo, tmp_path):
        """A `wc` that prints nothing must not read as "under the bound"."""
        fake_wc = tmp_path / "wc"
        fake_wc.write_text("#!/bin/bash\nexit 1\n")
        fake_wc.chmod(0o755)
        _seed_session(isolated_home, SID)

        result = _run(
            _append_args(), cwd=git_repo, home=isolated_home,
            extra_env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 2
        assert "could not measure the ledger line length" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()

    def test_near_cap_row_of_four_byte_characters_is_accepted_under_a_utf8_locale(
        self, isolated_home, git_repo
    ):
        """700 emoji, one per character slot: 2,800 bytes of field text, over
        four bytes a character but inside the 4,095-byte bound."""
        _seed_session(isolated_home, SID)
        emoji = "\U0001F600"
        args = _append_args(finding=emoji * 200, rationale=emoji * 300, source=emoji * 200)

        result = _run(args, cwd=git_repo, home=isolated_home, extra_env={"LC_ALL": _utf8_locale()})

        assert result.returncode == 0, result.stderr
        written_line = _ledger_path(isolated_home, git_repo).read_text().splitlines()[0]
        assert len(written_line.encode()) <= 4095
        assert json.loads(written_line)["finding"] == emoji * 200

    def test_max_cap_ascii_row_is_accepted(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        args = _append_args(
            finding="x" * self._FIELD_CAPS["finding"],
            rationale="x" * self._FIELD_CAPS["rationale"],
            source="x" * self._FIELD_CAPS["source"],
        )

        result = _run(args, cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert _ledger_path(isolated_home, git_repo).exists()

    def test_control_and_four_byte_characters_within_every_cap_are_rejected_by_byte_count(
        self, isolated_home, git_repo
    ):
        """600 control characters plus 100 emoji: 700 characters, inside
        every per-field cap under a UTF-8 locale, but about 4,000 bytes
        before the row's own envelope. A character count would accept it."""
        _seed_session(isolated_home, SID)
        args = _append_args(
            finding="\x01" * 200,
            rationale="\x01" * 300,
            source="\x01" * 100 + "\U0001F600" * 100,
        )

        result = _run(
            args, cwd=git_repo, home=isolated_home, extra_env={"LC_ALL": _utf8_locale()},
        )

        assert result.returncode == 2
        assert "4095-byte limit" in result.stderr
        assert not _ledger_path(isolated_home, git_repo).exists()


class TestReviewLedgerShow:
    def test_show_reports_absence_when_no_ledger(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        result = _run(["show"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert "no ledger" in result.stdout.lower()

    def test_show_header_has_the_documented_shape_when_no_ledger_exists(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert _show_header(result) == {
            "scope": "branch", "rows": "0", "oldest": "-", "newest": "-", "max_round": "0", "files": "",
        }

    def test_show_header_lists_both_files_and_counts_rows_across_them(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_append_args(finding="branch row", round="2"), cwd=git_repo, home=isolated_home)
        _git(git_repo, "checkout", "-q", "--detach")
        _run(_append_args(finding="detached row", round="3"), cwd=git_repo, home=isolated_home)
        _git(git_repo, "checkout", "-q", "feature")

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        header = _show_header(result)
        branch_file = _ledger_path(isolated_home, git_repo)
        assert header["files"] == f"{branch_file},{_session_ledger_path(isolated_home, git_repo)}"
        assert header["rows"] == "2"
        assert header["max_round"] == "3"

    def test_show_header_uses_stand_ins_for_rows_without_event_time_or_round(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"finding": "schema-v1 row"}) + "\n")

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        header = _show_header(result)
        assert (header["rows"], header["oldest"], header["newest"], header["max_round"]) == ("1", "-", "-", "0")

    def test_show_absence_message_names_the_scope_and_the_file(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert "no ledger for this branch" in result.stdout
        assert str(_ledger_path(isolated_home, git_repo)) in result.stdout

    def test_show_prints_ledger_contents(self, isolated_home, git_repo):
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)
        result = _run(["show"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        record = json.loads(result.stdout.splitlines()[0])
        assert record["finding"] == "Missing error handling in foo()"

    def test_show_header_reports_scope_files_row_count_time_span_and_max_round(
        self, isolated_home, git_repo
    ):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps({"round": 2, "finding": "middle", "event_time": "2024-03-01T00:00:00Z"}) + "\n"
            + json.dumps({"round": 4, "finding": "newest", "event_time": "2024-06-01T00:00:00Z"}) + "\n"
            + json.dumps({"round": 1, "finding": "oldest", "event_time": "2024-01-01T00:00:00Z"}) + "\n"
        )

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert _show_header(result) == {
            "scope": "branch",
            "rows": "3",
            "oldest": "2024-01-01T00:00:00Z",
            "newest": "2024-06-01T00:00:00Z",
            "max_round": "4",
            "files": str(ledger),
        }
        assert [row["finding"] for row in _shown_rows(result)] == ["oldest", "middle", "newest"]

    def test_show_reads_a_file_holding_schema_v2_and_current_rows_together(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps({
                "schema_version": 2, "round": 1, "finding": "v2 row", "disposition": "ADDRESS",
                "event_time": "2024-01-01T00:00:00Z",
            }) + "\n"
        )
        _run(_append_args(finding="current row", round="2"), cwd=git_repo, home=isolated_home)

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        rows = _shown_rows(result)
        assert [(row["schema_version"], row["finding"]) for row in rows] == [(2, "v2 row"), (4, "current row")]
        assert "session_id" not in rows[0]
        assert rows[1]["session_id"] == SID
        assert _show_header(result)["rows"] == "2"

    def test_show_merges_the_branch_and_session_files_in_event_time_order(self, isolated_home, git_repo):
        """In branch scope `show` reads the branch file and this worktree's
        own session file (rows appended while HEAD was detached), merged by
        event_time rather than by which file is read first."""
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        branch_file = _ledger_path(isolated_home, git_repo)
        session_file = _session_ledger_path(isolated_home, git_repo)
        branch_file.parent.mkdir(parents=True, exist_ok=True)
        branch_file.write_text(
            json.dumps({"finding": "branch-file-later", "event_time": "2024-06-01T00:00:00Z"}) + "\n"
        )
        session_file.write_text(
            json.dumps({"finding": "session-file-earlier", "event_time": "2024-01-01T00:00:00Z"}) + "\n"
        )

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert [row["finding"] for row in _shown_rows(result)] == [
            "session-file-earlier", "branch-file-later",
        ]

    def test_show_tags_each_row_with_the_repo_hash_of_the_file_it_came_from(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert [row["source_repo_hash"] for row in _shown_rows(result)] == [_repo_hash(git_repo)]

    def test_show_ignores_another_repo_hashs_file_for_the_same_session_id(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        (ledger_dir / ("0" * 64 + f".{SID}.jsonl")).write_text(
            json.dumps({"finding": "another worktree's row", "event_time": "2024-01-01T00:00:00Z"}) + "\n"
        )

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert "no ledger for this branch" in result.stdout

    def test_show_sorts_a_row_missing_event_time_first(self, isolated_home, git_repo):
        """A pre-existing schema-v1 row (no event_time field) sorts before
        any row carrying a real timestamp, via the `// ""` fallback --
        empty string sorts before any ISO-8601 string."""
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps({"finding": "has-timestamp", "event_time": "2024-01-01T00:00:00Z"})
            + "\n"
            + json.dumps({"finding": "schema-v1-no-timestamp"})
            + "\n"
        )

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        findings = [json.loads(line)["finding"] for line in result.stdout.splitlines()]
        assert findings == ["schema-v1-no-timestamp", "has-timestamp"]

    def test_show_skips_a_torn_line_and_still_prints_the_header_and_sorted_rows(self, isolated_home, git_repo):
        """A crash mid-write or an unlocked interleave leaves an undecodable
        line in a file that outlives its session. One such line must not
        suppress the header the round count depends on."""
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps({"round": 2, "finding": "later", "event_time": "2024-06-01T00:00:00Z"}) + "\n"
            + '{"round": 3, "finding": "torn mid-wri\n'
            + json.dumps({"round": 1, "finding": "earlier", "event_time": "2024-01-01T00:00:00Z"}) + "\n"
        )

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert [row["finding"] for row in _shown_rows(result)] == ["earlier", "later"]
        header = _show_header(result)
        assert (header["rows"], header["max_round"]) == ("2", "2")

    def test_show_keeps_every_intact_row_when_one_file_ends_in_a_torn_unterminated_line(
        self, isolated_home, git_repo
    ):
        """A torn write leaves the branch file ending mid-row with no newline.
        The session file's first row must still decode, not merge into that
        fragment, and its round must count toward max_round."""
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        branch_file = _ledger_path(isolated_home, git_repo)
        session_file = _session_ledger_path(isolated_home, git_repo)
        branch_file.parent.mkdir(parents=True, exist_ok=True)
        branch_file.write_text(
            json.dumps({"round": 1, "finding": "branch-intact", "event_time": "2024-01-01T00:00:00Z"}) + "\n"
            + '{"round": 9, "finding": "torn mid-wri'
        )
        session_file.write_text(
            json.dumps({"round": 4, "finding": "session-first", "event_time": "2024-02-01T00:00:00Z"}) + "\n"
            + json.dumps({"round": 2, "finding": "session-second", "event_time": "2024-03-01T00:00:00Z"}) + "\n"
        )

        result = _run(["show"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert [row["finding"] for row in _shown_rows(result)] == [
            "branch-intact", "session-first", "session-second",
        ]
        header = _show_header(result)
        assert (header["rows"], header["max_round"]) == ("3", "4")

    def test_show_outside_a_git_repository_exits_2_with_no_header_and_writes_nothing(
        self, isolated_home, tmp_path
    ):
        _seed_session(isolated_home, SID)
        outside_repo = tmp_path / "not-a-repo"
        outside_repo.mkdir()

        result = _run(["show"], cwd=outside_repo, home=isolated_home)

        assert result.returncode == 2
        assert "not inside a git repository" in result.stderr
        assert "show scope=" not in result.stderr
        assert not (isolated_home / ".claude" / "review-narrative-ledger").exists()

    def test_show_prints_rows_unsorted_with_no_header_and_exits_1_when_jq_fails(
        self, isolated_home, git_repo, tmp_path
    ):
        """`show` depends on jq to merge-sort rows. A `jq` shim on PATH ahead
        of the real one, that always exits nonzero, makes `_lib_jq` fail
        exactly like a missing jq would. `show` must still print every row,
        unsorted since the merge never ran, but emit no header and exit 1: a
        header would report a round count it could not compute. The
        file-vanishing-mid-read race for the shell side specifically is
        accepted as untested here: it has no monkeypatch seam in bash, so
        constructing it deterministically (a real concurrent delete
        mid-`_lib_jq` call) is impractical."""
        fake_jq = tmp_path / "jq"
        fake_jq.write_text("#!/bin/bash\nexit 1\n")
        fake_jq.chmod(0o755)
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        # Deliberately out of event_time order in the raw file -- since the
        # sort never runs, the fallback must reproduce this exact raw
        # (unsorted) order, not the event_time-sorted one the tests above pin.
        ledger.write_text(
            json.dumps({"finding": "written-first", "event_time": "2024-06-01T00:00:00Z"})
            + "\n"
            + json.dumps({"finding": "written-second", "event_time": "2024-01-01T00:00:00Z"})
            + "\n"
        )

        result = _run(
            ["show"], cwd=git_repo, home=isolated_home,
            extra_env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert "could not sort ledger rows for display" in result.stderr
        assert "review-ledger.sh: show " not in result.stderr
        findings = [json.loads(line)["finding"] for line in result.stdout.splitlines()]
        assert findings == ["written-first", "written-second"], (
            "show must still print every row via the raw cat fallback when "
            "jq fails, in on-disk (unsorted) order"
        )

    def test_show_exits_1_with_no_header_when_only_the_summary_jq_call_fails(
        self, isolated_home, git_repo, tmp_path
    ):
        """The sort succeeds but the summary pass (jq -s) fails: the header
        must not print `?` or a guessed 0 for the count and max round."""
        real_jq = subprocess.run(["which", "jq"], capture_output=True, text=True, check=True).stdout.strip()
        fake_jq = tmp_path / "jq"
        fake_jq.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do [ "$arg" = "-s" ] && exit 1; done\n'
            f'exec {shlex.quote(real_jq)} "$@"\n'
        )
        fake_jq.chmod(0o755)
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)

        result = _run(
            ["show"], cwd=git_repo, home=isolated_home,
            extra_env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert "could not summarize ledger rows for display" in result.stderr
        header_lines = [line for line in result.stderr.splitlines() if line.startswith("review-ledger.sh: show ")]
        assert header_lines == [], f"a failed summary must not print a header: {header_lines}"
        assert len(_shown_rows(result)) == 1


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

    def _append_near_limit_rows_from_distinct_sessions(
        self, isolated_home, git_repo, session_count: int
    ) -> tuple[list[str], list[str]]:
        """Runs one 4,095-byte append per session id, all at once, against
        the one branch file, and returns the file's lines and each
        appender's stderr."""
        procs = []
        for index in range(session_count):
            session_id = f"session-{index}"
            finding, rationale, source = _fields_for_row_of_bytes(4095, session_id)
            procs.append(_popen_as_session(
                _append_args(finding=finding, rationale=rationale, source=source),
                cwd=git_repo, home=isolated_home, session_id=session_id,
            ))
        stderrs = []
        for proc in procs:
            _stdout, stderr = proc.communicate(timeout=30)
            assert proc.returncode == 0, stderr
            stderrs.append(stderr)
        return _ledger_path(isolated_home, git_repo).read_text().splitlines(), stderrs

    def test_parallel_near_limit_appends_from_distinct_sessions_land_as_intact_rows(
        self, isolated_home, git_repo
    ):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        session_count = 6

        lines, _stderrs = self._append_near_limit_rows_from_distinct_sessions(
            isolated_home, git_repo, session_count,
        )

        assert sorted(json.loads(line)["session_id"] for line in lines) == sorted(
            f"session-{index}" for index in range(session_count)
        )

    def test_parallel_near_limit_appends_stay_intact_on_the_unlocked_fallback_path(
        self, isolated_home, git_repo, live_pid
    ):
        """A lock held by a live process makes every append exhaust its
        retries and write unlocked, so the rows race with no lock at all and
        rely on the single-write append alone."""
        _git(git_repo, "checkout", "-q", "-b", "feature")
        lock_file = _ledger_path(isolated_home, git_repo).with_suffix(".jsonl.lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(f"{live_pid}\n")
        session_count = 6

        lines, stderrs = self._append_near_limit_rows_from_distinct_sessions(
            isolated_home, git_repo, session_count,
        )

        assert all("proceeding unlocked" in stderr for stderr in stderrs), (
            f"every appender must have taken the unlocked fallback, got stderr: {stderrs}"
        )
        assert lock_file.read_text() == f"{live_pid}\n", "the live holder's lock file must be left intact"
        assert sorted(json.loads(line)["session_id"] for line in lines) == sorted(
            f"session-{index}" for index in range(session_count)
        )

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


class TestReviewLedgerAppendWriteFailure:
    def test_a_row_that_could_not_be_written_exits_2_and_says_so(self, isolated_home, git_repo):
        """A directory at the ledger path makes the append redirect fail even
        for root. The row was neither written nor deduplicated, so the
        script must not report success."""
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        ledger = _ledger_path(isolated_home, git_repo)
        ledger.mkdir(parents=True)

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 2
        assert "could not write the ledger row" in result.stderr
        assert ledger.is_dir()

    def test_a_deduplicated_row_still_exits_0(self, isolated_home, git_repo):
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)
        _run(_append_args(), cwd=git_repo, home=isolated_home)

        result = _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert len(_ledger_path(isolated_home, git_repo).read_text().splitlines()) == 1


class TestReviewLedgerResolverFailure:
    @pytest.mark.parametrize("args", [_append_args(), ["show"]], ids=["append", "show"])
    def test_a_git_failure_reading_head_exits_2_with_nothing_written(
        self, isolated_home, git_repo, tmp_path, args
    ):
        """A `git` shim that fails only `symbolic-ref` leaves the ledger
        location unknown. The script must abort rather than fall back to a
        session file, an empty path, or a stray `.lock` in the working
        directory."""
        real_git = shutil.which("git")
        fake_git = tmp_path / "git"
        fake_git.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do [ "$arg" = "symbolic-ref" ] && exit 128; done\n'
            f'exec {shlex.quote(real_git)} "$@"\n'
        )
        fake_git.chmod(0o755)
        _git(git_repo, "checkout", "-q", "-b", "feature")
        _seed_session(isolated_home, SID)

        result = _run(
            args, cwd=git_repo, home=isolated_home,
            extra_env={"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 2
        assert "git could not read HEAD" in result.stderr
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        assert not ledger_dir.exists() or list(ledger_dir.iterdir()) == []
        assert not (git_repo / ".lock").exists()


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

    def test_append_sweep_ignores_a_widened_cleanup_period_days(self, isolated_home, git_repo):
        """append's best-effort sweep always uses the fixed 30-day floor,
        never _ledger_sweep_window_days' dynamic settings.json read -- unlike
        clear-stale, which does widen with a custom cleanupPeriodDays (see
        TestReviewLedgerSweepWindowFromSettings below)."""
        (isolated_home / ".claude" / "settings.json").write_text(
            json.dumps({"cleanupPeriodDays": 60})
        )
        _seed_session(isolated_home, SID)
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        stale = ledger_dir / ("0" * 64 + ".other-session.jsonl")
        self._make_stale(stale)

        _run(_append_args(), cwd=git_repo, home=isolated_home)

        assert not stale.exists(), (
            "append's sweep must evict a 31-day-old file even when "
            "cleanupPeriodDays=60 would otherwise keep it fresh"
        )

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


class TestReviewLedgerSweepWindowFromSettings:
    """Covers GH-973: the sweep window derives from Claude Code's own
    cleanupPeriodDays setting, floored at 30 days, via clear-stale's
    --dry-run report. This is a thin integration test proving the
    resolved number is plumbed into `find -mtime +N` -- see
    TestLedgerSweepWindowDays in test_lib.py for the arithmetic itself
    (absent settings file, custom value, below-floor value, malformed
    input)."""

    def _plant(self, ledger_dir: Path, name: str, age_days: float) -> Path:
        path = ledger_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"finding":"x"}\n')
        age = time.time() - age_days * 24 * 60 * 60
        os.utime(path, (age, age))
        return path

    def test_custom_cleanup_period_days_widens_the_window(self, isolated_home, git_repo):
        (isolated_home / ".claude" / "settings.json").write_text(
            json.dumps({"cleanupPeriodDays": 60})
        )
        ledger_dir = isolated_home / ".claude" / "review-narrative-ledger"
        # 31 days old would already be stale under the default 30-day
        # window; a custom 60-day window must keep it fresh.
        still_fresh = self._plant(ledger_dir, ("3" * 64 + ".still-fresh.jsonl"), 31)
        stale = self._plant(ledger_dir, ("4" * 64 + ".stale.jsonl"), 62)

        result = _run(["clear-stale", "--dry-run"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        assert still_fresh.name not in result.stdout, (
            f"cleanupPeriodDays=60 must not evict a 31-day-old file: {result.stdout}"
        )
        assert stale.name in result.stdout
