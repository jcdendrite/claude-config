"""Direct unit tests for _review-ledger-lib.sh.

Each case sources hooks/_lib.sh and then the lib under `set -u`, as
review-ledger.sh does, and calls one function. The flag, source, reference,
site-hash and render input matrices live here; test_review_ledger_script.py
keeps one subprocess case per branch of the script's own wiring.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import CLAUDE_DIR, SCRIPTS_DIR

# claude/.claude/ itself, so `hooks.tests...` below resolves, as in test_author_outcome.py.
sys.path.insert(0, str(CLAUDE_DIR))
from hooks.tests.test_review_ledger_script import (  # noqa: E402
    _DEDUP_KEY_CHANGES,
    _DEDUP_KEY_EXEMPT_CHANGES,
    _utf8_locale,
)

_HOOKS_LIB = CLAUDE_DIR / "hooks" / "_lib.sh"
_LIB = SCRIPTS_DIR / "_review-ledger-lib.sh"

_DELIM_START = "<!-- code-review:deferred:start -->"
_DELIM_END = "<!-- code-review:deferred:end -->"
_CRITERION = "orthogonal-scope"
_OTHER_CRITERION = "edge-case-below-current-scale"
_DECISION_ID = "aaaaaaaaaaaa"
_SUCCESSOR_ID = "bbbbbbbbbbbb"
_CARRY_ID = "cccccccccccc"
_BLOCK_TEXT = "def settled_block():\n    return 1\n"
_SESSION_ID_LIKE = "sess" + "-" + "0" * 8  # a stand-in id, built at runtime, never rendered


def _bash(
    body: str, *, stdin: str | None = None, env: dict | None = None, cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run `body` after sourcing the two libs under `set -u`. The process exit
    status is the last command's. Output is decoded without newline
    translation, so a carriage return in a rendered PR body survives."""
    script = f"set -u\n. {shlex.quote(str(_HOOKS_LIB))}\n. {shlex.quote(str(_LIB))}\n{body}\n"
    completed = subprocess.run(
        ["bash", "-c", script], capture_output=True, input=None if stdin is None else stdin.encode(),
        env={**os.environ, **(env or {})}, cwd=cwd, check=False,
    )
    return subprocess.CompletedProcess(
        completed.args, completed.returncode, completed.stdout.decode(), completed.stderr.decode(),
    )


def _call(function: str, *args: str, **kwargs) -> subprocess.CompletedProcess:
    return _bash(f"{function} " + " ".join(shlex.quote(arg) for arg in args), **kwargs)


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------


def _validate(
    disposition: str, decided_by: str = "", quote: str = "", invariant: str = "", carry_forward: str = "",
    criterion: str = "", ref: str = "", cited_line: str = "", source: str = "n/a", **kwargs,
) -> subprocess.CompletedProcess:
    return _call(
        "_review_ledger_validate_flags", disposition, decided_by, quote, invariant, carry_forward,
        criterion, ref, cited_line, source, **kwargs,
    )


_ENGINEER = {"decided_by": "engineer", "quote": "keep it", "source": "a.py:1-2"}
_CONSULT = {"decided_by": "plan-architect", "source": "a.py:1-2"}
_SETTLED_CARRY = {"decided_by": "carry", "ref": _DECISION_ID, "cited_line": "a.py:1", "source": "a.py:1-2"}
_DEFER_CARRY = {**_SETTLED_CARRY, "criterion": _CRITERION}


class TestValidateFlagsAccepts:
    @pytest.mark.parametrize(
        ("disposition", "flags"),
        [
            pytest.param("ADDRESS", {}, id="plain ADDRESS"),
            pytest.param("ADDRESS", {"ref": _DECISION_ID}, id="ADDRESS closing a decision"),
            pytest.param("CLEAN", {}, id="CLEAN"),
            pytest.param("DEFER", {"criterion": _CRITERION, "source": "a.py"}, id="DEFER with a path-only source"),
            pytest.param("DEFER", {"criterion": _CRITERION, "source": "a.py:3"}, id="DEFER with a range"),
            pytest.param("SETTLED", _CONSULT, id="consult SETTLED"),
            pytest.param("SETTLED", _ENGINEER, id="engineer SETTLED"),
            pytest.param("SETTLED", {**_ENGINEER, "carry_forward": "1"}, id="engineer SETTLED carry-forward"),
            pytest.param("SETTLED", {**_ENGINEER, "invariant": "1"}, id="engineer SETTLED invariant"),
            pytest.param("SETTLED", _SETTLED_CARRY, id="SETTLED carry"),
            pytest.param("DEFER", _DEFER_CARRY, id="DEFER carry"),
        ],
    )
    def test_accepted(self, disposition, flags):
        result = _validate(disposition, **flags)

        assert result.returncode == 0, result.stderr


class TestValidateFlagsRejects:
    @pytest.mark.parametrize(
        ("disposition", "flags", "fragment"),
        [
            pytest.param("SETTLED", {"source": "a.py:1"}, "--decided-by", id="SETTLED without --decided-by"),
            pytest.param("SETTLED", {"decided_by": "nobody", "source": "a.py:1"}, "nobody", id="SETTLED with an invalid decider"),
            pytest.param("SETTLED", {**_ENGINEER, "quote": ""}, "--engineer-quote", id="engineer with no quote"),
            pytest.param("SETTLED", {**_ENGINEER, "quote": "   "}, "--engineer-quote", id="engineer with a whitespace quote"),
            pytest.param(
                "SETTLED", {**_ENGINEER, "quote": "\t\n"}, "--engineer-quote",
                id="engineer with a tab-and-newline quote",
            ),
            pytest.param("SETTLED", {**_CONSULT, "quote": "q"}, "--engineer-quote", id="plan-architect with a quote"),
            pytest.param("SETTLED", {**_SETTLED_CARRY, "quote": "q"}, "--engineer-quote", id="carry with a quote"),
            pytest.param("ADDRESS", {"quote": "q"}, "--engineer-quote", id="quote on ADDRESS"),
            pytest.param(
                "DEFER", {"criterion": _CRITERION, "source": "a.py:1", "quote": "q"}, "--engineer-quote",
                id="quote on DEFER",
            ),
            pytest.param("CLEAN", {"quote": "q"}, "--engineer-quote", id="quote on CLEAN"),
            pytest.param("SETTLED", {**_ENGINEER, "quote": "x" * 201}, "200", id="a 201-character quote"),
            pytest.param("ADDRESS", {"invariant": "1"}, "--enforcement-invariant", id="invariant on ADDRESS"),
            pytest.param(
                "DEFER", {"criterion": _CRITERION, "source": "a.py:1", "invariant": "1"}, "--enforcement-invariant",
                id="invariant on DEFER",
            ),
            pytest.param("CLEAN", {"invariant": "1"}, "--enforcement-invariant", id="invariant on CLEAN"),
            pytest.param("SETTLED", {**_CONSULT, "invariant": "1"}, "--enforcement-invariant", id="invariant on a consult"),
            pytest.param("SETTLED", {**_SETTLED_CARRY, "invariant": "1"}, "--enforcement-invariant", id="invariant on a carry"),
            pytest.param("ADDRESS", {"carry_forward": "1"}, "--carry-forward", id="carry-forward on ADDRESS"),
            pytest.param(
                "DEFER", {"criterion": _CRITERION, "source": "a.py:1", "carry_forward": "1"}, "--carry-forward",
                id="carry-forward on DEFER",
            ),
            pytest.param("SETTLED", {**_CONSULT, "carry_forward": "1"}, "--carry-forward", id="carry-forward on a consult"),
            pytest.param("SETTLED", {**_SETTLED_CARRY, "carry_forward": "1"}, "--carry-forward", id="carry-forward on a carry"),
            pytest.param(
                "SETTLED", {**_ENGINEER, "carry_forward": "1", "invariant": "1"}, "--carry-forward",
                id="carry-forward beside the invariant label",
            ),
            pytest.param("ADDRESS", {"decided_by": "engineer"}, "--decided-by", id="decider on ADDRESS"),
            pytest.param("CLEAN", {"decided_by": "carry"}, "--decided-by", id="decider on CLEAN"),
            pytest.param(
                "DEFER", {"decided_by": "engineer", "criterion": _CRITERION, "source": "a.py:1"}, "--decided-by",
                id="engineer DEFER",
            ),
            pytest.param(
                "DEFER", {"decided_by": "plan-architect", "criterion": _CRITERION, "source": "a.py:1"}, "--decided-by",
                id="plan-architect DEFER",
            ),
            pytest.param("DEFER", {"criterion": _CRITERION}, "--source", id="DEFER with no source"),
            pytest.param("DEFER", {"criterion": _CRITERION, "source": ""}, "--source", id="DEFER with an empty source"),
            pytest.param("SETTLED", {"decided_by": "plan-architect"}, "--source", id="SETTLED with no source"),
            pytest.param(
                "SETTLED", {"decided_by": "plan-architect", "source": ""}, "--source",
                id="SETTLED with an empty source",
            ),
            pytest.param("DEFER", {"source": "a.py:1"}, "--defer-criterion", id="DEFER with no criterion"),
            pytest.param(
                "DEFER", {"criterion": "whatever", "source": "a.py:1"}, "--defer-criterion",
                id="DEFER with an unknown criterion",
            ),
            pytest.param("ADDRESS", {"criterion": _CRITERION}, "--defer-criterion", id="criterion on ADDRESS"),
            pytest.param(
                "SETTLED", {**_SETTLED_CARRY, "criterion": _CRITERION}, "--defer-criterion",
                id="SETTLED carry with a criterion",
            ),
            pytest.param("SETTLED", {**_SETTLED_CARRY, "ref": ""}, "--ref", id="carry without --ref"),
            pytest.param("SETTLED", {**_SETTLED_CARRY, "cited_line": ""}, "--cited-line", id="carry without --cited-line"),
            pytest.param("DEFER", {**_DEFER_CARRY, "criterion": ""}, "--defer-criterion", id="DEFER carry without a criterion"),
            pytest.param("ADDRESS", {"cited_line": "a.py:1"}, "--cited-line", id="cited line on ADDRESS"),
            pytest.param(
                "SETTLED", {**_ENGINEER, "cited_line": "a.py:1"}, "--cited-line",
                id="cited line on an engineer SETTLED",
            ),
            pytest.param("CLEAN", {"ref": _DECISION_ID}, "--ref", id="--ref on CLEAN"),
            pytest.param("ADDRESS", {"ref": "not-an-id"}, "--ref", id="a malformed --ref"),
            pytest.param("ADDRESS", {"ref": "a" * 11}, "--ref", id="a --ref of 11 hex digits"),
            pytest.param("ADDRESS", {"ref": "a" * 13}, "--ref", id="a --ref of 13 hex digits"),
            pytest.param("ADDRESS", {"ref": "A" * 12}, "--ref", id="a --ref of 12 uppercase hex digits"),
            pytest.param("ADDRESS", {"ref": "g" * 12}, "--ref", id="a --ref of 12 non-hex characters"),
        ],
    )
    def test_rejected(self, disposition, flags, fragment):
        result = _validate(disposition, **flags)

        assert result.returncode == 1
        assert fragment in result.stderr

    @pytest.mark.parametrize("source", ["", "n/a"])
    def test_a_defer_source_rejection_names_both_flags_and_all_five_criteria(self, source):
        result = _validate("DEFER", criterion=_CRITERION, source=source)

        assert result.returncode == 1
        assert "--source" in result.stderr and "--defer-criterion" in result.stderr
        for criterion in (
            "orthogonal-scope", "coordinated-multi-pr-effort", "gold-plating-beyond-declared-user-surface",
            "contract-pinned-at-another-layer", "edge-case-below-current-scale",
        ):
            assert criterion in result.stderr


class TestValidateFlagsQuoteLength:
    def test_a_quote_of_exactly_200_characters_is_accepted(self):
        assert _validate("SETTLED", **{**_ENGINEER, "quote": "x" * 200}).returncode == 0

    def test_a_quote_of_201_characters_is_rejected_not_truncated(self):
        result = _validate("SETTLED", **{**_ENGINEER, "quote": "x" * 201})

        assert result.returncode == 1
        assert "exceeds 200 characters" in result.stderr

    def test_a_multibyte_quote_is_counted_in_characters_under_a_utf8_locale(self):
        emoji = "\U0001F600"
        env = {"LC_ALL": _utf8_locale()}

        accepted = _validate("SETTLED", **{**_ENGINEER, "quote": emoji * 200}, env=env)
        rejected = _validate("SETTLED", **{**_ENGINEER, "quote": emoji * 201}, env=env)

        assert accepted.returncode == 0, accepted.stderr
        assert rejected.returncode == 1


# ---------------------------------------------------------------------------
# Source and cited-line grammar
# ---------------------------------------------------------------------------

_REPO = "/srv/example-repo"


class TestNormalizeLocation:
    @pytest.mark.parametrize("spec", ["a.py:5", "a.py:5-9", "dir/a b.py:12", "a.py:123456789", "a.py:7-7", "a.py"])
    def test_accepted_and_returned_unchanged(self, spec):
        result = _call("_review_ledger_normalize_location", _REPO, spec)

        assert result.returncode == 0, result.stderr
        assert result.stdout == spec

    @pytest.mark.parametrize(
        "spec",
        [
            "a.py:0", "a.py:5-3", "a.py:08-09", "a.py:abc", "a.py:", "a.py:1-", "a.py:1234567890",
            "a.py:-3", "a:b.py", ":5", "a.py:1-2-3",
        ],
    )
    def test_rejected_grammar(self, spec):
        result = _call("_review_ledger_normalize_location", _REPO, spec)

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("review-ledger.sh: ")

    @pytest.mark.parametrize(
        "spec", ["../a.py:1", "dir/../a.py:1", "dir/..:1", "/etc/passwd:1", "/srv/example-repo-other/a.py:1"],
    )
    def test_rejects_a_dot_dot_segment_and_an_absolute_path_outside_the_repo(self, spec):
        result = _call("_review_ledger_normalize_location", _REPO, spec)

        assert result.returncode == 1

    @pytest.mark.parametrize("spec", ["dir//a.py:1", "./dir//a.py", "dir//a.py"])
    def test_rejects_an_empty_path_segment(self, spec):
        result = _call("_review_ledger_normalize_location", _REPO, spec)

        assert result.returncode == 1
        assert "empty or '..' path segment" in result.stderr

    @pytest.mark.parametrize("spec", ["a\tb.py:1", "a\nb.py:1", "a\x01b.py", "a\tb.py"])
    def test_rejects_a_control_character_in_the_path_naming_the_cause(self, spec):
        result = _call("_review_ledger_normalize_location", _REPO, spec)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "control character" in result.stderr

    def test_an_absolute_path_inside_the_repo_is_stored_repo_relative(self):
        result = _call("_review_ledger_normalize_location", _REPO, f"{_REPO}/dir/a.py:5-9")

        assert (result.returncode, result.stdout) == (0, "dir/a.py:5-9")

    def test_a_leading_dot_slash_is_dropped(self):
        result = _call("_review_ledger_normalize_location", _REPO, "./dir/a.py:5")

        assert (result.returncode, result.stdout) == (0, "dir/a.py:5")


# ---------------------------------------------------------------------------
# Site hash
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    """A git repo whose working tree the site hash reads."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "feature")
    _git(root, "config", "user.email", "test@test.com")
    _git(root, "config", "user.name", "test")
    return root


def _write(repo: Path, name: str, content: str | bytes) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


def _site_hash(repo: Path, path: str, start: int, end: int) -> subprocess.CompletedProcess:
    return _call("_review_ledger_site_hash", str(repo), path, str(start), str(end))


def _hash_of(repo: Path, path: str, start: int, end: int) -> str:
    result = _site_hash(repo, path, start, end)
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestSiteHash:
    def test_is_the_first_12_hex_digits_of_the_sha256_of_the_range_text(self, repo):
        _write(repo, "a.txt", "one\ntwo\nthree\nfour\n")

        digest = _hash_of(repo, "a.txt", 2, 3)

        assert digest == hashlib.sha256(b"two\nthree").hexdigest()[:12]

    def test_an_edit_inside_the_range_changes_it_and_an_edit_outside_does_not(self, repo):
        _write(repo, "a.txt", "one\ntwo\nthree\nfour\n")
        before = _hash_of(repo, "a.txt", 2, 3)

        _write(repo, "a.txt", "ONE\ntwo\nthree\nfour\n")
        outside = _hash_of(repo, "a.txt", 2, 3)
        _write(repo, "a.txt", "one\ntwo\nthree, reworded\nfour\n")
        inside = _hash_of(repo, "a.txt", 2, 3)

        assert outside == before
        assert inside != before

    def test_an_edit_anywhere_in_a_single_long_line_changes_it(self, repo):
        line = "word " * 200
        _write(repo, "a.txt", f"{line}\n")
        before = _hash_of(repo, "a.txt", 1, 1)

        _write(repo, "a.txt", f"{line[:500]}X{line[501:]}\n")

        assert _hash_of(repo, "a.txt", 1, 1) != before

    def test_a_range_ending_at_an_unterminated_last_line_hashes_it(self, repo):
        _write(repo, "a.txt", "one\ntwo")

        assert _hash_of(repo, "a.txt", 2, 2) == hashlib.sha256(b"two").hexdigest()[:12]

    def test_a_range_one_line_past_an_unterminated_last_line_is_rejected(self, repo):
        _write(repo, "a.txt", "one\ntwo")

        result = _site_hash(repo, "a.txt", 2, 3)

        assert result.returncode == 1
        assert "past the end of the file" in result.stderr

    def test_a_range_starting_inside_the_file_and_ending_past_it_is_rejected(self, repo):
        _write(repo, "a.txt", "one\ntwo\nthree")

        result = _site_hash(repo, "a.txt", 2, 9)

        assert result.returncode == 1
        assert "past the end of the file" in result.stderr

    def test_a_newline_added_at_end_of_file_does_not_change_it(self, repo):
        _write(repo, "a.txt", "one\ntwo")
        before = _hash_of(repo, "a.txt", 1, 2)

        _write(repo, "a.txt", "one\ntwo\n")

        assert _hash_of(repo, "a.txt", 1, 2) == before

    def test_a_change_from_lf_to_crlf_changes_it(self, repo):
        _write(repo, "a.txt", b"one\ntwo\n")
        before = _hash_of(repo, "a.txt", 1, 2)

        _write(repo, "a.txt", b"one\r\ntwo\r\n")

        assert _hash_of(repo, "a.txt", 1, 2) != before

    @pytest.mark.parametrize("content", ["one\n\n   \n\t\nfour\n", "one\n\n\n\nfour\n"])
    def test_a_blank_only_range_is_rejected(self, repo, content):
        _write(repo, "a.txt", content)

        result = _site_hash(repo, "a.txt", 2, 4)

        assert result.returncode == 1
        assert "only whitespace" in result.stderr

    def test_a_missing_file_is_rejected(self, repo):
        result = _site_hash(repo, "absent.txt", 1, 1)

        assert result.returncode == 1
        assert "does not exist" in result.stderr

    def test_a_source_symlinked_to_a_file_outside_the_repo_is_rejected(self, repo, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("one\ntwo\n")
        (repo / "link.txt").symlink_to(outside)

        result = _site_hash(repo, "link.txt", 1, 2)

        assert result.returncode == 1
        assert "resolves outside the repository" in result.stderr
        assert result.stdout == ""

    def test_a_source_under_a_directory_symlinked_outside_the_repo_is_rejected(self, repo, tmp_path):
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "a.txt").write_text("one\ntwo\n")
        (repo / "linked").symlink_to(outside_dir)

        result = _site_hash(repo, "linked/a.txt", 1, 2)

        assert result.returncode == 1
        assert "resolves outside the repository" in result.stderr

    def test_a_chain_of_symlinks_ending_outside_the_repo_is_rejected(self, repo, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("one\ntwo\n")
        (repo / "first.txt").symlink_to(repo / "second.txt")
        (repo / "second.txt").symlink_to(outside)

        result = _site_hash(repo, "first.txt", 1, 2)

        assert result.returncode == 1
        assert "resolves outside the repository" in result.stderr

    def test_a_symlink_to_a_file_inside_the_repo_hashes_the_target_text(self, repo):
        _write(repo, "real/a.txt", "one\ntwo\n")
        (repo / "alias.txt").symlink_to("real/a.txt")

        assert _hash_of(repo, "alias.txt", 1, 2) == _hash_of(repo, "real/a.txt", 1, 2)

    def test_reads_the_working_tree_not_the_index(self, repo):
        _write(repo, "a.txt", "one\ntwo\n")
        _git(repo, "add", "a.txt")
        staged = _hash_of(repo, "a.txt", 1, 2)

        _write(repo, "a.txt", "one\ntwo, edited unstaged\n")

        assert _hash_of(repo, "a.txt", 1, 2) != staged

    def test_text_staged_at_decision_time_hashes_the_same_once_committed(self, repo):
        _write(repo, "a.txt", "one\ntwo\n")
        _git(repo, "add", "a.txt")
        staged = _hash_of(repo, "a.txt", 1, 2)

        _git(repo, "commit", "-q", "-m", "commit the staged text")

        assert _hash_of(repo, "a.txt", 1, 2) == staged

    def test_the_row_id_is_the_first_12_hex_digits_of_the_sha256_of_the_line(self):
        result = _call("_review_ledger_row_id", '{"a":1}')

        assert result.stdout == hashlib.sha256(b'{"a":1}').hexdigest()[:12]


# ---------------------------------------------------------------------------
# Reference and liveness
# ---------------------------------------------------------------------------


def _row(**fields) -> dict:
    row = {
        "schema_version": 4, "round": 1, "finding": "a finding", "disposition": "DEFER", "rationale": "why",
        "source": "a.py:1-2", "authoring_agent": "", "authoring_effort": "", "session_id": _SESSION_ID_LIKE,
        "decided_by": "", "engineer_quote": "", "enforcement_invariant": False, "carry_forward": False,
        "defer_criterion": "", "ref": "", "cited_line": "", "site_hash": "", "event_time": "2026-09-01T10:00:00Z",
    }
    row.update(fields)
    return row


def _defer(id_: str, **fields) -> dict:
    return _row(**{"id": id_, "defer_criterion": _CRITERION, "site_hash": "1" * 12, **fields})


def _engineer_decision(id_: str, **fields) -> dict:
    return _row(**{
        "id": id_, "disposition": "SETTLED", "decided_by": "engineer", "engineer_quote": "keep it",
        "site_hash": "2" * 12, **fields,
    })


def _consult_decision(id_: str, **fields) -> dict:
    return _row(**{"id": id_, "disposition": "SETTLED", "decided_by": "plan-architect", "site_hash": "3" * 12, **fields})


def _carry(id_: str, ref: str, disposition: str = "SETTLED", **fields) -> dict:
    return _row(**{
        "id": id_, "disposition": disposition, "decided_by": "carry", "ref": ref, "site_hash": "2" * 12,
        "cited_line": "a.py:1", **fields,
    })


def _address(id_: str, **fields) -> dict:
    return _row(**{"id": id_, "disposition": "ADDRESS", **fields})


def _write_ledger(path: Path, rows: list[dict], *, raw_lines: list[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row) for row in rows] + (raw_lines or [])
    path.write_text("".join(line + "\n" for line in lines))
    return path


@pytest.fixture
def ledger(tmp_path) -> Path:
    return tmp_path / "ledger" / "branch.jsonl"


def _check_ref(
    ledger: Path, disposition: str = "ADDRESS", decided_by: str = "", invariant: str = "", ref: str = _DECISION_ID,
) -> subprocess.CompletedProcess:
    return _call("_review_ledger_check_ref", "branch", str(ledger), disposition, decided_by, invariant, ref)


class TestCheckRef:
    def test_a_dangling_ref_is_rejected_naming_the_branch_ledger(self, ledger):
        _write_ledger(ledger, [_defer(_SUCCESSOR_ID)])

        result = _check_ref(ledger)

        assert result.returncode == 1
        assert "not in this branch's ledger" in result.stderr

    def test_a_ref_into_an_absent_ledger_file_is_dangling_not_an_error(self, ledger):
        result = _check_ref(ledger)

        assert result.returncode == 1
        assert "not in this branch's ledger" in result.stderr

    def test_a_dangling_ref_message_names_the_causes_beyond_another_sessions_file(self, ledger):
        _write_ledger(ledger, [_defer(_SUCCESSOR_ID)])

        message = _check_ref(ledger).stderr

        assert "session-scope file" in message
        assert "detached-HEAD or default-branch" in message
        assert "torn line" in message
        assert "swept ledger" in message
        assert "review-ledger.sh show" in message

    def test_the_session_noun_appears_in_session_scope(self, ledger):
        _write_ledger(ledger, [])

        result = _call("_review_ledger_check_ref", "session", str(ledger), "ADDRESS", "", "", _DECISION_ID)

        assert "not in this session's ledger" in result.stderr

    def test_a_ref_naming_a_carry_is_rejected_naming_the_carrys_decision(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, carry_forward=True), _carry(_CARRY_ID, _DECISION_ID)])

        result = _check_ref(ledger, ref=_CARRY_ID)

        assert result.returncode == 1
        assert "orchestrator carry" in result.stderr
        assert _DECISION_ID in result.stderr

    def test_a_ref_naming_an_address_row_is_rejected_as_not_a_decision(self, ledger):
        _write_ledger(ledger, [_address(_DECISION_ID)])

        result = _check_ref(ledger)

        assert result.returncode == 1
        assert "not a DEFER or SETTLED decision" in result.stderr

    @pytest.mark.parametrize(
        ("disposition", "decided_by"), [("DEFER", ""), ("SETTLED", "plan-architect")],
        ids=["DEFER", "consult SETTLED"],
    )
    def test_a_defer_or_consult_settled_cannot_supersede_an_engineer_decision(self, ledger, disposition, decided_by):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID)])

        result = _check_ref(ledger, disposition, decided_by)

        assert result.returncode == 1
        assert "only an ADDRESS or an engineer SETTLED" in result.stderr

    @pytest.mark.parametrize(("disposition", "decided_by"), [("ADDRESS", ""), ("SETTLED", "engineer")])
    def test_address_and_engineer_settled_may_supersede_an_engineer_decision(self, ledger, disposition, decided_by):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID)])

        assert _check_ref(ledger, disposition, decided_by).returncode == 0

    def test_an_engineer_settled_superseding_an_invariant_decision_needs_the_label(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, enforcement_invariant=True)])

        without_label = _check_ref(ledger, "SETTLED", "engineer", invariant="")
        with_label = _check_ref(ledger, "SETTLED", "engineer", invariant="1")

        assert without_label.returncode == 1
        assert "--enforcement-invariant" in without_label.stderr
        assert with_label.returncode == 0, with_label.stderr

    def test_an_address_superseding_an_invariant_decision_is_accepted(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, enforcement_invariant=True)])

        assert _check_ref(ledger, "ADDRESS").returncode == 0

    @pytest.mark.parametrize(
        ("successor", "label"),
        [
            pytest.param(_defer(_SUCCESSOR_ID, ref=_DECISION_ID), "a fresh DEFER", id="fresh DEFER"),
            pytest.param(
                _engineer_decision(_SUCCESSOR_ID, ref=_DECISION_ID), "a fresh engineer SETTLED", id="fresh engineer SETTLED",
            ),
        ],
    )
    def test_a_decision_superseded_by_a_fresh_decision_is_no_longer_live_and_names_its_successor(
        self, ledger, successor, label
    ):
        _write_ledger(ledger, [_defer(_DECISION_ID), successor])

        result = _check_ref(ledger)

        assert result.returncode == 1, label
        assert "no longer live" in result.stderr
        assert _SUCCESSOR_ID in result.stderr

    def test_an_address_ref_closes_a_decision(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])

        result = _check_ref(ledger, "DEFER")

        assert result.returncode == 1
        assert "ADDRESS" in result.stderr and _SUCCESSOR_ID in result.stderr

    def test_a_carry_row_does_not_retire_its_decision(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _carry(_CARRY_ID, _DECISION_ID, "DEFER")])

        assert _check_ref(ledger).returncode == 0

    def test_id_less_rows_from_older_schemas_are_not_referenceable(self, ledger):
        legacy = {key: value for key, value in _row(schema_version=3).items() if key != "id"}
        _write_ledger(ledger, [legacy])

        result = _check_ref(ledger)

        assert result.returncode == 1
        assert "not in this branch's ledger" in result.stderr

    def test_a_torn_line_is_skipped_and_the_intact_decision_is_still_found(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)], raw_lines=['{"id":"torn', "not json at all", "[1,2]"])

        assert _check_ref(ledger).returncode == 0

    def test_a_failing_jq_is_reported_as_an_unread_ledger(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        (fake_bin / "jq").write_text("#!/bin/bash\nexit 1\n")
        (fake_bin / "jq").chmod(0o755)

        result = _call(
            "_review_ledger_check_ref", "branch", str(ledger), "ADDRESS", "", "", _DECISION_ID,
            env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert "could not read the ledger" in result.stderr


def _retry_check(
    ledger: Path, retiring_row: dict, *, disposition: str = "ADDRESS", decided_by: str = "",
) -> subprocess.CompletedProcess:
    """check_ref with a retired --ref deferred, then check_not_retired on the row
    being appended, as review-ledger.sh chains them."""
    return _bash(
        f"_review_ledger_check_ref branch {shlex.quote(str(ledger))} {disposition} {shlex.quote(decided_by)} '' "
        f"{_DECISION_ID} 1 || exit 11\n"
        f"_review_ledger_check_not_retired {shlex.quote(str(ledger))} {shlex.quote(json.dumps(retiring_row))} {_DECISION_ID}"
    )


class TestCheckRefDefersRetiredDecisions:
    def test_a_retired_ref_passes_check_ref_when_the_caller_defers_it_and_records_the_retirer(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])

        result = _bash(
            f"_review_ledger_check_ref branch {shlex.quote(str(ledger))} ADDRESS '' '' {_DECISION_ID} 1 || exit 11\n"
            "printf '%s %s' \"$_REVIEW_LEDGER_REF_RETIRED_BY\" \"$_REVIEW_LEDGER_REF_RETIRED_DISP\""
        )

        assert (result.returncode, result.stdout) == (0, f"{_SUCCESSOR_ID} ADDRESS")

    def test_a_retired_ref_is_rejected_by_default(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])

        assert _check_ref(ledger).returncode == 1

    def test_a_carry_of_a_retired_decision_is_rejected_even_when_the_caller_defers(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])

        result = _bash(
            f"_review_ledger_check_ref branch {shlex.quote(str(ledger))} DEFER carry '' {_DECISION_ID} 1"
        )

        assert result.returncode == 1
        assert "no longer live" in result.stderr

    def test_a_live_ref_needs_no_retirer_and_passes_check_not_retired(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])

        assert _retry_check(ledger, _address("dddddddddddd", ref=_DECISION_ID)).returncode == 0


# A value for each dedup-key field that differs from the ADDRESS retirer the tests below build.
_RETIRER_KEY_FIELD_CHANGES = {
    "round": 2, "finding": "another finding", "disposition": "DEFER", "rationale": "a different reason",
    "source": "b.py:1", "authoring_agent": "mixed", "authoring_effort": "high", "session_id": "another-session",
    "decided_by": "plan-architect", "engineer_quote": "a quote", "enforcement_invariant": True,
    "carry_forward": True, "defer_criterion": _CRITERION, "ref": "f" * 12, "cited_line": "a.py:1",
    "site_hash": "9" * 12,
}


class TestCheckNotRetired:
    def test_the_fixture_row_has_exactly_the_keys_the_script_writes(self):
        assert set(_row(id=_DECISION_ID)) == set(_DEDUP_KEY_CHANGES) | set(_DEDUP_KEY_EXEMPT_CHANGES)

    def test_the_differing_field_cases_name_every_dedup_key_field(self):
        assert set(_RETIRER_KEY_FIELD_CHANGES) == set(_DEDUP_KEY_CHANGES)

    def test_a_row_identical_to_the_retirer_outside_id_time_and_schema_version_is_a_retry(self, ledger):
        retirer = _address(_SUCCESSOR_ID, ref=_DECISION_ID)
        _write_ledger(ledger, [_defer(_DECISION_ID), retirer])
        retry = {**retirer, "id": "dddddddddddd", "event_time": "2026-09-09T09:09:09Z", "schema_version": 5}

        assert _retry_check(ledger, retry).returncode == 0

    @pytest.mark.parametrize(("field", "value"), sorted(_RETIRER_KEY_FIELD_CHANGES.items()))
    def test_a_row_differing_from_the_retirer_in_one_key_field_is_rejected_naming_the_retirer(
        self, ledger, field, value
    ):
        retirer = _address(_SUCCESSOR_ID, ref=_DECISION_ID)
        _write_ledger(ledger, [_defer(_DECISION_ID), retirer])

        result = _retry_check(ledger, {**retirer, "id": "dddddddddddd", field: value})

        assert result.returncode == 1
        assert "no longer live" in result.stderr
        assert _SUCCESSOR_ID in result.stderr

    def test_the_retirer_of_an_engineer_decision_retries_when_it_repeats_itself(self, ledger):
        retirer = _address(_SUCCESSOR_ID, ref=_DECISION_ID)
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID), retirer])

        assert _retry_check(ledger, {**retirer, "id": "dddddddddddd"}).returncode == 0

    @pytest.mark.parametrize("invariant", [False, True], ids=["plain decision", "enforcement-invariant decision"])
    @pytest.mark.parametrize(
        ("successor", "disposition", "decided_by"),
        [
            pytest.param(_defer("dddddddddddd", ref=_DECISION_ID), "DEFER", "", id="a DEFER"),
            pytest.param(
                _consult_decision("dddddddddddd", ref=_DECISION_ID), "SETTLED", "plan-architect", id="a consult SETTLED",
            ),
            pytest.param(
                _engineer_decision("dddddddddddd", ref=_DECISION_ID), "SETTLED", "engineer",
                id="an unlabelled engineer SETTLED",
            ),
        ],
    )
    def test_an_engineer_decision_retired_by_an_address_rejects_any_other_successor(
        self, ledger, invariant, successor, disposition, decided_by
    ):
        """Pins that the retired path reports "no longer live" ahead of the
        successor rules. Each successor differs from the retirer in several key
        fields at once, so these cases do not isolate one; the
        _RETIRER_KEY_FIELD_CHANGES cases carry the single-field key-coverage
        guarantee."""
        decision = _engineer_decision(_DECISION_ID, enforcement_invariant=invariant)
        _write_ledger(ledger, [decision, _address(_SUCCESSOR_ID, ref=_DECISION_ID)])

        result = _retry_check(ledger, successor, disposition=disposition, decided_by=decided_by)

        assert result.returncode == 1, "the retired ref must reach check_not_retired, which rejects it"
        assert "no longer live" in result.stderr
        assert _SUCCESSOR_ID in result.stderr

    def test_a_carry_row_naming_the_decision_is_not_a_retirer_to_match(self, ledger):
        carry = _carry(_CARRY_ID, _DECISION_ID, "DEFER")
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID), carry])

        result = _retry_check(ledger, {**carry, "id": "dddddddddddd"})

        assert result.returncode == 1

    def test_an_unreadable_ledger_fails_closed(self, ledger, tmp_path):
        retirer = _address(_SUCCESSOR_ID, ref=_DECISION_ID)
        _write_ledger(ledger, [_defer(_DECISION_ID), retirer])
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        (fake_bin / "jq").write_text("#!/bin/bash\nexit 1\n")
        (fake_bin / "jq").chmod(0o755)
        script = (
            f"PATH={shlex.quote(str(fake_bin))}:$PATH\n"
            f"_REVIEW_LEDGER_REF_RETIRED_BY={_SUCCESSOR_ID}\n_REVIEW_LEDGER_REF_RETIRED_DISP=ADDRESS\n"
            f"_review_ledger_check_not_retired {shlex.quote(str(ledger))} {shlex.quote(json.dumps(retirer))} {_DECISION_ID}"
        )

        assert _bash(script).returncode == 1


class TestCheckRefForCarries:
    """A carry may reference only a live, carryable decision of its own disposition."""

    def test_a_live_defer_with_a_range_carries(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])

        assert _check_ref(ledger, "DEFER", "carry").returncode == 0

    def test_a_live_engineer_decision_logged_carry_forward_carries(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, carry_forward=True)])

        assert _check_ref(ledger, "SETTLED", "carry").returncode == 0

    def test_an_engineer_decision_without_carry_forward_never_carries(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID)])

        result = _check_ref(ledger, "SETTLED", "carry")

        assert result.returncode == 1
        assert "--carry-forward" in result.stderr

    def test_an_invariant_labelled_decision_never_carries(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, enforcement_invariant=True)])

        assert _check_ref(ledger, "SETTLED", "carry").returncode == 1

    def test_a_plan_architect_decision_never_carries(self, ledger):
        _write_ledger(ledger, [_consult_decision(_DECISION_ID)])

        result = _check_ref(ledger, "SETTLED", "carry")

        assert result.returncode == 1
        assert "plan-architect" in result.stderr

    def test_a_decision_with_a_path_only_source_never_carries(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID, source="a.py", site_hash="")])

        result = _check_ref(ledger, "DEFER", "carry")

        assert result.returncode == 1
        assert "path-only source" in result.stderr

    def test_a_superseded_or_closed_decision_never_carries(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])

        result = _check_ref(ledger, "DEFER", "carry")

        assert result.returncode == 1
        assert "no longer live" in result.stderr

    def test_a_defer_carry_of_a_settled_decision_is_rejected(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, carry_forward=True)])

        result = _check_ref(ledger, "DEFER", "carry")

        assert result.returncode == 1
        assert "must reference a DEFER decision" in result.stderr


# ---------------------------------------------------------------------------
# Carry acceptance: criterion, cited line, site hash
# ---------------------------------------------------------------------------


def _carry_check(
    repo: Path, ledger: Path, decision: dict, *, source: str, cited_line: str, disposition: str = "SETTLED",
    criterion: str = "",
) -> subprocess.CompletedProcess:
    """check_ref, then check_carry, as review-ledger.sh chains them."""
    _write_ledger(ledger, [decision])
    return _bash(
        f"_review_ledger_check_ref branch {shlex.quote(str(ledger))} {disposition} carry '' {_DECISION_ID} || exit 11\n"
        f"_review_ledger_check_carry {shlex.quote(str(repo))} {disposition} {shlex.quote(criterion)} "
        f"{shlex.quote(source)} {shlex.quote(cited_line)} \"$_REVIEW_LEDGER_REF_INFO\""
    )


def _settled_at(repo: Path, path: str, start: int, end: int, **fields) -> dict:
    """An engineer decision logged --carry-forward over lines start-end of path
    as they stand now."""
    return _engineer_decision(
        _DECISION_ID, carry_forward=True, source=f"{path}:{start}-{end}", site_hash=_hash_of(repo, path, start, end), **fields,
    )


class TestCheckCarry:
    def test_an_unchanged_block_at_the_same_range_with_the_cited_line_inside_is_accepted(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:2")

        assert result.returncode == 0, result.stderr
        assert result.stdout == decision["site_hash"]

    def test_a_cited_line_outside_the_carry_range_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT + "x = 3\n")
        decision = _settled_at(repo, "a.py", 1, 2)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:3")

        assert result.returncode == 1
        assert "not inside the carry's --source" in result.stderr

    def test_a_cited_span_wider_than_the_carry_range_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT + "x = 3\n")
        decision = _settled_at(repo, "a.py", 1, 2)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:2-3")

        assert result.returncode == 1
        assert "not inside the carry's --source" in result.stderr

    def test_a_cited_line_in_another_path_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        _write(repo, "b.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="b.py:1")

        assert result.returncode == 1
        assert "not inside the carry's --source" in result.stderr

    def test_a_carry_naming_the_decisions_old_range_while_the_cited_line_is_elsewhere_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "# new header\n# another\n" + _BLOCK_TEXT)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:4")

        assert result.returncode == 1
        assert "not inside the carry's --source" in result.stderr
        assert "no longer matches" not in result.stderr

    def test_an_edit_inside_the_range_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "def settled_block():\n    return 2\n")

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 1
        assert "no longer matches the decided block" in result.stderr

    def test_lines_inserted_above_are_accepted_when_the_carry_names_the_new_range(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "# new header\n# another\n" + _BLOCK_TEXT)

        result = _carry_check(repo, ledger, decision, source="a.py:3-4", cited_line="a.py:3")

        assert result.returncode == 0, result.stderr

    def test_the_same_insertion_naming_the_old_range_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "# new header\n# another\n" + _BLOCK_TEXT)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 1
        assert "no longer matches the decided block" in result.stderr

    def test_a_renamed_file_is_accepted_when_the_carry_names_the_new_path(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "renamed/b.py", _BLOCK_TEXT)
        (repo / "a.py").unlink()

        result = _carry_check(repo, ledger, decision, source="renamed/b.py:1-2", cited_line="renamed/b.py:1")

        assert result.returncode == 0, result.stderr

    def test_text_edited_unstaged_only_after_being_staged_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        _git(repo, "add", "a.py")
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "def settled_block():\n    return 99  # edited, never staged\n")

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 1
        assert "no longer matches the decided block" in result.stderr

    def test_text_staged_at_decision_time_then_committed_is_accepted(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        _git(repo, "add", "a.py")
        decision = _settled_at(repo, "a.py", 1, 2)
        _git(repo, "commit", "-q", "-m", "commit the decided text")

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 0, result.stderr

    def test_a_carry_range_ending_at_an_unterminated_last_line_is_accepted_and_one_line_past_is_rejected(
        self, repo, ledger
    ):
        _write(repo, "a.py", "first\nlast line, no newline")
        decision = _settled_at(repo, "a.py", 2, 2)

        at_end = _carry_check(repo, ledger, decision, source="a.py:2-2", cited_line="a.py:2")
        past_end = _carry_check(repo, ledger, decision, source="a.py:2-3", cited_line="a.py:2")

        assert at_end.returncode == 0, at_end.stderr
        assert past_end.returncode == 1

    def test_a_trailing_newline_added_at_end_of_file_is_accepted(self, repo, ledger):
        _write(repo, "a.py", "first\nlast line, no newline")
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "first\nlast line, no newline\n")

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:2")

        assert result.returncode == 0, result.stderr

    def test_a_change_from_lf_to_crlf_is_rejected(self, repo, ledger):
        _write(repo, "a.py", b"one\ntwo\n")
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", b"one\r\ntwo\r\n")

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 1
        assert "no longer matches the decided block" in result.stderr

    def test_a_carry_range_over_blank_lines_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        _write(repo, "a.py", "\n\n   \n")

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 1
        assert "only whitespace" in result.stderr

    def test_a_missing_file_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)
        (repo / "a.py").unlink()

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1")

        assert result.returncode == 1
        assert "does not exist" in result.stderr

    def test_a_path_only_cited_line_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)

        result = _carry_check(repo, ledger, decision, source="a.py:1-2", cited_line="a.py")

        assert result.returncode == 1
        assert "needs a line number" in result.stderr

    def test_a_path_only_carry_source_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _settled_at(repo, "a.py", 1, 2)

        result = _carry_check(repo, ledger, decision, source="a.py", cited_line="a.py:1")

        assert result.returncode == 1
        assert "range-form" in result.stderr

    def test_a_defer_carry_restating_the_decisions_criterion_is_accepted(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _defer(_DECISION_ID, source="a.py:1-2", site_hash=_hash_of(repo, "a.py", 1, 2))

        result = _carry_check(
            repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1", disposition="DEFER", criterion=_CRITERION,
        )

        assert result.returncode == 0, result.stderr

    def test_a_defer_carry_with_a_different_criterion_is_rejected(self, repo, ledger):
        _write(repo, "a.py", _BLOCK_TEXT)
        decision = _defer(_DECISION_ID, source="a.py:1-2", site_hash=_hash_of(repo, "a.py", 1, 2))

        result = _carry_check(
            repo, ledger, decision, source="a.py:1-2", cited_line="a.py:1", disposition="DEFER", criterion=_OTHER_CRITERION,
        )

        assert result.returncode == 1
        assert "differs from the decision's" in result.stderr


# ---------------------------------------------------------------------------
# Carry line
# ---------------------------------------------------------------------------


class TestCarryLine:
    def _info(self, ledger: Path, rows: list[dict], disposition: str = "SETTLED") -> str:
        _write_ledger(ledger, rows)
        result = _bash(
            f"_review_ledger_check_ref branch {shlex.quote(str(ledger))} {disposition} carry '' {_DECISION_ID} || exit 11\n"
            "printf '%s' \"$_REVIEW_LEDGER_REF_INFO\""
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_names_the_decision_its_date_round_quote_the_carried_finding_and_the_reopen_command(self, ledger):
        decision = _engineer_decision(
            _DECISION_ID, carry_forward=True, round=4, event_time="2026-09-02T08:00:00Z",
            engineer_quote="keep it as written",
        )
        info = self._info(ledger, [decision])

        result = _call("_review_ledger_carry_line", _DECISION_ID, "the carried finding", info)

        assert result.returncode == 0
        line = result.stdout
        for expected in (
            f"carry of decision {_DECISION_ID}", "decided 2026-09-02, round 4", 'engineer quote: "keep it as written"',
            'finding "the carried finding"', f"reopen {_DECISION_ID} by logging ADDRESS --ref {_DECISION_ID}",
        ):
            assert expected in line
        assert line.count("\n") == 1

    def test_a_defer_decision_reports_its_criterion_as_the_basis_and_no_quote(self, ledger):
        decision = _defer(_DECISION_ID, round=3, event_time="2026-09-05T08:00:00Z", engineer_quote="not shown")
        info = self._info(ledger, [decision], disposition="DEFER")

        result = _call("_review_ledger_carry_line", _DECISION_ID, "the carried finding", info)

        assert result.returncode == 0
        assert f"decided 2026-09-05, round 3; DEFER ({_CRITERION})" in result.stdout
        assert "engineer quote" not in result.stdout
        assert "not shown" not in result.stdout

    def test_a_control_character_in_the_stored_quote_reads_as_a_space(self, ledger):
        decision = _engineer_decision(_DECISION_ID, carry_forward=True, engineer_quote="two\nlines\x1fhere")
        info = self._info(ledger, [decision])

        result = _call("_review_ledger_carry_line", _DECISION_ID, "f", info)

        assert 'engineer quote: "two lines here"' in result.stdout
        assert result.stdout.count("\n") == 1


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _render(ledger: Path, *, pr_json: str = "", out: Path | None = None, stdin: str | None = None, env: dict | None = None):
    return _call("_review_ledger_render", str(ledger), pr_json, str(out) if out else "", stdin=stdin, env=env)


def _pr_json(body: str) -> str:
    return json.dumps({"body": body})


def _render_body(ledger: Path, body: str) -> subprocess.CompletedProcess:
    return _render(ledger, pr_json="-", stdin=_pr_json(body))


def _split_gfm_cells(table_row: str) -> list[str]:
    """Cells of one table row the way a parity-aware table splitter reads it: a
    pipe splits unless an odd run of backslashes directly precedes it.

    Known-unverified, and deliberately not decided here: a code-span quote holding
    `\\|` renders `\\\\|`, an even run. Under this splitter that pipe splits the
    cell, and under a splitter where any backslash escapes the pipe it does not.
    test_a_quote_with_a_backslash_before_a_pipe_renders_an_even_run_before_it pins the
    bytes. Checking it against GitHub's own renderer (`gh api /markdown`) settles it."""
    inner = table_row.strip()
    assert inner.startswith("|") and inner.endswith("|")
    cells, current = [], []
    backslash_run = 0
    for char in inner[1:-1]:
        if char == "|" and backslash_run % 2 == 0:
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        backslash_run = backslash_run + 1 if char == "\\" else 0
    cells.append("".join(current))
    return [cell.strip() for cell in cells]


def _table_rows(output: str) -> list[str]:
    return [
        line for line in output.splitlines()
        if line.startswith("| ") and not line.startswith("| ---") and "| Finding |" not in line
    ]


class TestRenderBlock:
    def test_one_row_per_live_decision_with_carry_rows_directly_beneath(self, ledger):
        _write_ledger(ledger, [
            _defer(_DECISION_ID, finding="deferred finding", event_time="2026-09-01T10:00:00Z", round=1),
            _engineer_decision(
                _SUCCESSOR_ID, carry_forward=True, finding="kept finding", event_time="2026-09-02T10:00:00Z", round=2,
            ),
            _carry(_CARRY_ID, _SUCCESSOR_ID, finding="carried finding", event_time="2026-09-04T10:00:00Z", round=5),
        ])

        result = _render(ledger)

        assert result.returncode == 0, result.stderr
        rows = _table_rows(result.stdout)
        assert [row.split(" | ")[0] for row in rows] == ["| deferred finding", "| kept finding", "| carried finding"]
        assert f"orchestrator-matched carry of {_SUCCESSOR_ID}" in rows[2]

    def test_each_decision_shows_its_own_date_and_round_and_each_carry_its_own(self, ledger):
        _write_ledger(ledger, [
            _engineer_decision(_DECISION_ID, carry_forward=True, event_time="2026-09-02T10:00:00Z", round=2),
            _carry(_CARRY_ID, _DECISION_ID, event_time="2026-09-04T10:00:00Z", round=5),
        ])

        rows = _table_rows(_render(ledger).stdout)

        assert _split_gfm_cells(rows[0])[4] == "2026-09-02 round 2"
        assert _split_gfm_cells(rows[1])[4] == "2026-09-04 round 5"

    def test_superseded_and_closed_decisions_drop_out_with_their_carries(self, ledger):
        _write_ledger(ledger, [
            _engineer_decision("111111111111", carry_forward=True, finding="superseded"),
            _carry("222222222222", "111111111111", finding="carry of superseded"),
            _engineer_decision("333333333333", ref="111111111111", carry_forward=True, finding="successor"),
            _defer("444444444444", finding="closed"),
            _address("555555555555", ref="444444444444"),
        ])

        output = _render(ledger).stdout

        assert [_split_gfm_cells(row)[0] for row in _table_rows(output)] == ["successor"]

    def test_a_carry_written_after_its_decisions_closing_row_does_not_render(self, ledger):
        _write_ledger(ledger, [
            _defer(_DECISION_ID, finding="closed decision"),
            _address(_SUCCESSOR_ID, ref=_DECISION_ID),
            _carry(_CARRY_ID, _DECISION_ID, "DEFER", finding="late carry"),
        ])

        result = _render(ledger)

        assert result.stdout == ""

    def test_id_less_rows_from_older_schemas_do_not_render(self, ledger):
        legacy_v3 = {key: value for key, value in _row(schema_version=3).items() if key != "id"}
        legacy_v2 = {
            "schema_version": 2, "round": 1, "finding": "v2", "disposition": "DEFER", "rationale": "r", "source": "a.py:1",
        }
        _write_ledger(ledger, [legacy_v3, legacy_v2])

        assert _render(ledger).stdout == ""

    def test_block_carries_the_delimiters_the_legend_and_the_invariant_heading(self, ledger):
        _write_ledger(ledger, [
            _defer(_DECISION_ID, finding="deferred"),
            _engineer_decision(_SUCCESSOR_ID, carry_forward=True, finding="kept"),
            _engineer_decision("999999999999", enforcement_invariant=True, finding="invariant"),
            _carry(_CARRY_ID, _SUCCESSOR_ID, finding="carried"),
        ])

        output = _render(ledger).stdout

        lines = output.splitlines()
        assert lines[0] == _DELIM_START and lines[-1] == _DELIM_END
        assert "## Deferred review findings" in lines
        assert "## Settled review findings" in lines
        assert "### Enforcement-invariant decisions (asked again on every repeat)" in lines
        assert sum(line.startswith("*Decided* is the UTC date and review round.") for line in lines) == 2
        invariant_heading = "### Enforcement-invariant decisions (asked again on every repeat)"
        assert lines.index(invariant_heading) > lines.index("## Settled review findings")
        invariant_rows = lines[lines.index(invariant_heading):]
        assert any("invariant" in row for row in _table_rows("\n".join(invariant_rows)))

    def test_a_blank_line_separates_each_table_from_the_next_heading_and_from_the_end_delimiter(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _engineer_decision(_SUCCESSOR_ID)])

        lines = _render(ledger).stdout.splitlines()

        assert lines[lines.index("## Settled review findings") - 1] == ""
        assert lines[-2] == ""

    def test_no_session_id_and_no_site_hash_appear_in_the_output(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _engineer_decision(_SUCCESSOR_ID, carry_forward=True)])

        output = _render(ledger).stdout

        assert _SESSION_ID_LIKE not in output
        assert "1" * 12 not in output and "2" * 12 not in output
        assert "session_id" not in output

    def test_the_quote_renders_as_a_code_span_with_a_fence_longer_than_its_longest_backtick_run(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, engineer_quote="use `a` and ``b`` here")])

        cell = _split_gfm_cells(_table_rows(_render(ledger).stdout)[0])[2]

        assert cell == "engineer: ``` use `a` and ``b`` here ```"

    def test_a_quote_with_no_backticks_uses_a_single_backtick_fence_padded_by_one_space(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, engineer_quote="plain words")])

        assert _split_gfm_cells(_table_rows(_render(ledger).stdout)[0])[2] == "engineer: ` plain words `"

    @pytest.mark.parametrize(
        ("row", "cell_count"),
        [
            pytest.param("| a | b |", 2, id="a bare pipe splits"),
            pytest.param("| a\\| b |", 1, id="one backslash escapes the pipe"),
            pytest.param("| a\\\\| b |", 2, id="two backslashes leave the pipe splitting"),
            pytest.param("| a\\\\\\| b |", 1, id="three backslashes escape the pipe"),
        ],
    )
    def test_the_splitter_used_below_counts_backslash_runs_by_parity(self, row, cell_count):
        assert len(_split_gfm_cells(row)) == cell_count

    @pytest.mark.parametrize("text", ["a|b", "a\\|b", "a\\\\|b", "trailing backslash\\", "\\", "|", "||"])
    def test_pipes_and_backslashes_in_plain_cells_keep_the_column_count(self, ledger, text):
        _write_ledger(ledger, [
            _engineer_decision(_DECISION_ID, finding=text, rationale=text, source=text),
            _defer(_SUCCESSOR_ID, finding=text, rationale=text, source=text),
        ])

        rows = _table_rows(_render(ledger).stdout)

        assert len(rows) == 2
        for row in rows:
            assert len(_split_gfm_cells(row)) == 6, row

    @pytest.mark.parametrize("text", ["a|b", "a\\\\|b", "trailing backslash\\", "\\", "|", "||"])
    def test_pipes_and_backslashes_in_a_code_span_keep_the_column_count(self, ledger, text):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, engineer_quote=text)])

        (row,) = _table_rows(_render(ledger).stdout)

        assert len(_split_gfm_cells(row)) == 6, row

    def test_a_quote_with_a_backslash_before_a_pipe_renders_an_even_run_before_it(self, ledger):
        """Known-unverified shape (see _split_gfm_cells): the bytes are pinned so a change is deliberate."""
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, engineer_quote="a\\|b")])

        (row,) = _table_rows(_render(ledger).stdout)

        assert "engineer: ` a\\\\|b `" in row

    @pytest.mark.parametrize("text", ["a|b", "a\\|b", "a\\\\|b", "\\|", "||", "x\\\\\\|y"])
    def test_every_pipe_in_a_plain_cell_follows_an_odd_backslash_run(self, ledger, text):
        """Model-independent: the cell is cut out by its known neighbours, with no splitter involved."""
        _write_ledger(ledger, [_defer(_DECISION_ID, finding=text, source="neighbour.py:1")])

        (row,) = _table_rows(_render(ledger).stdout)

        cell = row[2:row.index(" | neighbour.py:1 | ")]
        assert cell.count("|") == text.count("|")
        for pipe in re.finditer(r"\|", cell):
            run = len(cell[:pipe.start()]) - len(cell[:pipe.start()].rstrip("\\"))
            assert run % 2 == 1, f"pipe at {pipe.start()} in {cell!r} follows an even backslash run"

    def test_plain_cells_double_each_backslash_and_escape_each_pipe(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID, finding="a\\|b\\")])

        row = _table_rows(_render(ledger).stdout)[0]

        assert row.startswith("| a\\\\\\|b\\\\ | ")

    def test_a_code_span_keeps_backslashes_verbatim_and_escapes_only_the_pipe(self, ledger):
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, engineer_quote="a\\b|c")])

        assert _split_gfm_cells(_table_rows(_render(ledger).stdout)[0])[2] == "engineer: ` a\\b\\|c `"

    def test_a_newline_or_control_character_becomes_a_space(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID, finding="one\ntwo\rthree\x0bfour\x0cfive\x01six", rationale="a\tb")])

        rows = _table_rows(_render(ledger).stdout)

        assert len(rows) == 1
        assert _split_gfm_cells(rows[0])[0] == "one two three four five six"
        assert _split_gfm_cells(rows[0])[3] == "a b"

    def test_a_comment_opener_in_a_plain_cell_is_neutralized(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID, finding="see <!-- this")])

        output = _render(ledger).stdout

        assert "&lt;!-- this" in output
        assert output.count("<!--") == 2, "only the two block delimiters may open a comment"

    def test_a_quote_containing_the_end_delimiter_leaves_the_block_intact(self, ledger):
        quote = f"text {_DELIM_END} more"
        _write_ledger(ledger, [_engineer_decision(_DECISION_ID, engineer_quote=quote)])

        rendered = _render(ledger).stdout
        merged = _render_body(ledger, "Intro\n\n" + rendered).stdout

        assert merged == "Intro\n\n" + rendered


class TestRenderOutputFile:
    def test_digest_out_writes_the_block_and_reports_changed(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        out = tmp_path / "agent-reviews" / "digest.md"

        result = _render(ledger, out=out)

        assert result.returncode == 0, result.stderr
        assert result.stdout == f"changed: {out}\n"
        assert out.read_text().startswith(_DELIM_START)

    def test_digest_with_no_live_decision_writes_no_file_and_removes_a_stale_one(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])
        out = tmp_path / "digest.md"
        out.write_text("stale digest")

        result = _render(ledger, out=out)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "unchanged\n"
        assert not out.exists()

    def test_unchanged_output_prints_unchanged_and_writes_no_file(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        body = _render_body(ledger, "Intro").stdout
        out = tmp_path / "body.md"

        result = _render(ledger, pr_json="-", out=out, stdin=_pr_json(body))

        assert result.returncode == 0, result.stderr
        assert result.stdout == "unchanged\n"
        assert not out.exists()

    def test_changed_output_writes_the_file_atomically_and_leaves_no_temp_file(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        out = tmp_path / "out" / "body.md"

        result = _render(ledger, pr_json="-", out=out, stdin=_pr_json("Intro"))

        assert result.stdout == f"changed: {out}\n"
        assert out.read_text().startswith("Intro\n\n" + _DELIM_START)
        assert [entry.name for entry in out.parent.iterdir()] == ["body.md"]

    @pytest.mark.parametrize(
        ("stdin", "fragment"),
        [
            pytest.param("", "empty", id="zero bytes on stdin"),
            pytest.param("not json", "not the JSON", id="input that is not JSON"),
            pytest.param('["not","an","object"]', "not the JSON", id="input that is not an object"),
            pytest.param(_pr_json(_DELIM_START + "\nonly an opener\n"), "unpaired", id="an unpaired delimiter"),
            pytest.param(_pr_json(_DELIM_END + "\n" + _DELIM_START + "\n"), "unpaired", id="delimiters in the wrong order"),
            pytest.param(
                _pr_json(f"{_DELIM_START}\n{_DELIM_END}\n\n{_DELIM_START}\n{_DELIM_END}\n"), "repeated", id="two unfenced blocks",
            ),
        ],
    )
    def test_a_failed_render_exits_nonzero_and_leaves_no_out_file_even_when_one_existed(
        self, ledger, tmp_path, stdin, fragment
    ):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        out = tmp_path / "body.md"
        out.write_text("previous body file")

        result = _render(ledger, pr_json="-", out=out, stdin=stdin)

        assert result.returncode == 1
        assert fragment in result.stderr
        assert not out.exists()
        assert [entry.name for entry in tmp_path.iterdir() if entry.name.startswith("body.md")] == []

    def test_a_failed_ledger_read_exits_nonzero_with_no_file(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        (fake_bin / "jq").write_text("#!/bin/bash\nexit 1\n")
        (fake_bin / "jq").chmod(0o755)
        out = tmp_path / "body.md"

        result = _render(
            ledger, pr_json="-", out=out, stdin=_pr_json("Intro"),
            env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert "could not read the ledger" in result.stderr
        assert not out.exists()

    def test_an_empty_body_gets_the_block_appended(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])

        result = _render_body(ledger, "")

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith(_DELIM_START) and result.stdout.endswith(_DELIM_END + "\n")

    def test_a_null_body_is_treated_as_empty(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])

        result = _render(ledger, pr_json="-", stdin='{"body": null}')

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith(_DELIM_START)


def _check_out(repo: Path, out: str, *, pr_json: str = "", ledger_dir: Path | None = None, cwd: Path | None = None):
    return _call(
        "_review_ledger_check_out", str(repo), out, pr_json, str(ledger_dir or repo.parent / "ledger-dir"),
        cwd=cwd or repo,
    )


class TestCheckOut:
    @pytest.fixture
    def agent_reviews(self, repo) -> Path:
        directory = repo / "agent-reviews"
        directory.mkdir()
        return directory

    @pytest.mark.parametrize("form", ["relative", "dot-slash", "absolute"])
    def test_a_file_directly_under_agent_reviews_is_accepted(self, repo, agent_reviews, form):
        out = {
            "relative": "agent-reviews/body.md", "dot-slash": "./agent-reviews/body.md",
            "absolute": str(agent_reviews / "body.md"),
        }[form]

        assert _check_out(repo, out).returncode == 0

    def test_a_target_that_does_not_exist_yet_under_a_missing_agent_reviews_is_accepted(self, repo):
        assert _check_out(repo, "agent-reviews/body.md").returncode == 0

    def test_an_existing_regular_file_target_is_accepted(self, repo, agent_reviews):
        (agent_reviews / "body.md").write_text("stale")

        assert _check_out(repo, "agent-reviews/body.md").returncode == 0

    def test_a_relative_target_is_taken_from_the_working_directory(self, repo, agent_reviews):
        result = _check_out(repo, "agent-reviews/body.md", cwd=repo / "agent-reviews")

        assert result.returncode == 1
        assert "must be a file directly under" in result.stderr

    def test_a_rejected_relative_target_names_the_working_directory_it_was_resolved_from(self, repo, agent_reviews):
        result = _check_out(repo, "agent-reviews/body.md", cwd=agent_reviews)

        assert f"resolved from the working directory {agent_reviews.resolve()}" in result.stderr

    def test_a_rejected_relative_subdirectory_target_names_the_working_directory_it_was_resolved_from(
        self, repo, agent_reviews
    ):
        result = _check_out(repo, "sub/body.md", cwd=agent_reviews)

        assert result.returncode == 1
        assert "not in a subdirectory" in result.stderr
        assert f"resolved from the working directory {agent_reviews.resolve()}" in result.stderr

    def test_a_rejected_absolute_target_does_not_name_a_working_directory(self, repo, agent_reviews):
        result = _check_out(repo, "/etc/passwd", cwd=agent_reviews)

        assert result.returncode == 1
        assert "working directory" not in result.stderr

    @pytest.mark.parametrize(
        ("out", "fragment"),
        [
            pytest.param("tracked.txt", "directly under", id="a file outside agent-reviews"),
            pytest.param("agent-reviews/../tracked.txt", "'..'", id="a dot-dot segment"),
            pytest.param("agent-reviews//body.md", "empty or '..'", id="an empty segment"),
            pytest.param("agent-reviews/sub/body.md", "not in a subdirectory", id="a subdirectory"),
            pytest.param("agent-reviews/", "directly under", id="the directory itself"),
            pytest.param("agent-reviews/a\tb.md", "control character", id="a tab in the name"),
            pytest.param("/etc/passwd", "directly under", id="an absolute path outside the repo"),
        ],
    )
    def test_a_target_outside_the_confined_directory_is_rejected(self, repo, agent_reviews, out, fragment):
        result = _check_out(repo, out)

        assert result.returncode == 1
        assert fragment in result.stderr

    def test_an_existing_directory_is_rejected(self, repo, agent_reviews):
        (agent_reviews / "adir").mkdir()

        result = _check_out(repo, "agent-reviews/adir")

        assert result.returncode == 1
        assert "not a regular file" in result.stderr

    def test_a_symlink_is_rejected_even_when_dangling(self, repo, agent_reviews):
        (agent_reviews / "dangling.md").symlink_to(repo / "nowhere.txt")

        result = _check_out(repo, "agent-reviews/dangling.md")

        assert result.returncode == 1
        assert "symlink" in result.stderr

    def test_a_symlinked_agent_reviews_directory_is_rejected_even_when_it_points_inside_the_repo(self, repo):
        (repo / "real-dir").mkdir()
        (repo / "agent-reviews").symlink_to(repo / "real-dir")

        result = _check_out(repo, "agent-reviews/body.md")

        assert result.returncode == 1
        assert "real directory" in result.stderr

    def test_an_agent_reviews_that_is_a_file_is_rejected(self, repo):
        (repo / "agent-reviews").write_text("not a directory")

        assert _check_out(repo, "agent-reviews/body.md").returncode == 1

    def test_the_pr_json_path_is_rejected_as_the_target_and_another_pr_json_is_accepted(self, repo, agent_reviews):
        (agent_reviews / "pr.json").write_text("{}")
        (agent_reviews / "body.md").write_text("stale")

        same = _check_out(repo, "agent-reviews/pr.json", pr_json="agent-reviews/pr.json")
        other = _check_out(repo, "agent-reviews/body.md", pr_json="agent-reviews/pr.json")
        from_stdin = _check_out(repo, "agent-reviews/body.md", pr_json="-")

        assert same.returncode == 1 and "same file" in same.stderr
        assert other.returncode == 0, other.stderr
        assert from_stdin.returncode == 0, from_stdin.stderr

    def test_a_hard_link_to_the_pr_json_file_is_rejected(self, repo, agent_reviews):
        (agent_reviews / "pr.json").write_text("{}")
        os.link(agent_reviews / "pr.json", agent_reviews / "alias.md")

        result = _check_out(repo, "agent-reviews/alias.md", pr_json="agent-reviews/pr.json")

        assert result.returncode == 1
        assert "same file" in result.stderr

    def test_a_directory_at_or_under_the_ledger_directory_is_rejected(self, repo, agent_reviews):
        at = _check_out(repo, "agent-reviews/body.md", ledger_dir=agent_reviews)
        under = _check_out(repo, "agent-reviews/body.md", ledger_dir=repo)
        elsewhere = _check_out(repo, "agent-reviews/body.md", ledger_dir=repo.parent / "ledger-dir")

        assert at.returncode == 1 and "ledger directory" in at.stderr
        assert under.returncode == 1 and "ledger directory" in under.stderr
        assert elsewhere.returncode == 0, elsewhere.stderr


class TestRemoveOut:
    def test_an_absent_target_is_fine(self, tmp_path):
        assert _call("_review_ledger_remove_out", str(tmp_path / "body.md")).returncode == 0

    def test_a_regular_file_is_deleted(self, tmp_path):
        stale = tmp_path / "body.md"
        stale.write_text("stale")

        result = _call("_review_ledger_remove_out", str(stale))

        assert result.returncode == 0
        assert not stale.exists()

    def test_a_directory_is_refused_and_left_in_place(self, tmp_path):
        directory = tmp_path / "adir"
        directory.mkdir()

        result = _call("_review_ledger_remove_out", str(directory))

        assert result.returncode == 1
        assert "not a regular file" in result.stderr
        assert directory.is_dir()

    def test_a_symlink_is_refused_and_its_referent_is_untouched(self, tmp_path):
        referent = tmp_path / "referent.txt"
        referent.write_text("keep")
        link = tmp_path / "link.md"
        link.symlink_to(referent)

        result = _call("_review_ledger_remove_out", str(link))

        assert result.returncode == 1
        assert link.is_symlink() and referent.read_text() == "keep"

    def test_a_delete_that_fails_reports_the_stale_path_even_as_root(self, tmp_path):
        stale = tmp_path / "body.md"
        stale.write_text("stale")
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        (fake_bin / "rm").write_text("#!/bin/bash\nexit 1\n")
        (fake_bin / "rm").chmod(0o755)

        result = _call(
            "_review_ledger_remove_out", str(stale), env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )

        assert result.returncode == 1
        assert "could not delete the stale" in result.stderr
        assert stale.read_text() == "stale"

    def test_a_file_that_cannot_be_deleted_fails_with_the_stale_path_named(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        locked = tmp_path / "locked"
        locked.mkdir()
        stale = locked / "body.md"
        stale.write_text("stale")
        locked.chmod(0o500)
        try:
            result = _call("_review_ledger_remove_out", str(stale))
        finally:
            locked.chmod(0o700)

        assert result.returncode == 1
        assert "could not delete the stale" in result.stderr
        assert stale.read_text() == "stale"


class TestRenderPrJsonFile:
    @pytest.mark.parametrize("kind", ["missing file", "directory"])
    def test_a_pr_json_path_that_cannot_be_read_fails_and_removes_a_stale_out_file(self, ledger, tmp_path, kind):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        pr_json = tmp_path / "pr.json"
        if kind == "directory":
            pr_json.mkdir()
        out = tmp_path / "body.md"
        out.write_text("stale")

        result = _render(ledger, pr_json=str(pr_json), out=out)

        assert result.returncode == 1
        assert "could not read --pr-json file" in result.stderr
        assert not out.exists()

    def test_a_readable_pr_json_file_renders_into_the_body(self, ledger, tmp_path):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        pr_json = tmp_path / "pr.json"
        pr_json.write_text(_pr_json("Intro"))

        result = _render(ledger, pr_json=str(pr_json))

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("Intro\n\n" + _DELIM_START)


class TestRenderPrBody:
    def _block(self, ledger: Path) -> str:
        return _render(ledger).stdout

    @pytest.mark.parametrize(
        ("body", "separator"),
        [
            pytest.param("No trailing newline", "\n\n", id="no trailing newline"),
            pytest.param("Ends with a blank line\n\n", "", id="ends with two newlines"),
            pytest.param("Ends with one newline\n", "\n", id="ends with one newline"),
            pytest.param("Line one\r\nLine two\r\n", "\n", id="CRLF lines"),
            pytest.param("Unicode \U0001F600 and `code` and | pipes \\| and \ttabs\n", "\n", id="hostile text"),
        ],
    )
    def test_every_byte_outside_the_appended_block_is_identical(self, ledger, body, separator):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        block = self._block(ledger)

        merged = _render_body(ledger, body).stdout

        assert merged == body + separator + block

    @pytest.mark.parametrize(
        ("before", "line_end_and_rest"),
        [
            pytest.param("", ("\n", "Tail text"), id="block at the start"),
            pytest.param("Head text\r\n\r\n", ("\n", ""), id="block at the end with a final newline"),
            pytest.param("Head text\r\n\r\n", None, id="block at the end with no final newline"),
            pytest.param("Head\n", ("\n", "Tail with CRLF\r\nno final newline"), id="block in the middle"),
            pytest.param("Head\r\n", ("\r\n", "Tail\r\n\r\n"), id="CRLF around the block"),
        ],
    )
    def test_a_replaced_block_leaves_the_bytes_around_it_identical(self, ledger, before, line_end_and_rest):
        _write_ledger(ledger, [_defer(_DECISION_ID, finding="new finding")])
        old_block = f"{_DELIM_START}\nold content\n{_DELIM_END}"
        block = self._block(ledger)
        if line_end_and_rest is None:
            body, expected = before + old_block, before + block.removesuffix("\n")
        else:
            line_end, rest = line_end_and_rest
            body, expected = before + old_block + line_end + rest, before + block + rest

        merged = _render_body(ledger, body).stdout

        assert merged == expected

    def test_a_block_with_crlf_delimiter_lines_is_recognized_and_replaced(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID, finding="replacement")])
        body = f"Head\r\n{_DELIM_START}\r\nold\r\n{_DELIM_END}\r\nTail\r\n"

        merged = _render_body(ledger, body).stdout

        assert merged.startswith("Head\r\n" + _DELIM_START + "\n")
        assert merged.endswith(_DELIM_END + "\nTail\r\n")
        assert "replacement" in merged and "old" not in merged

    def test_a_row_whose_last_cell_is_not_a_ledger_id_is_kept_in_its_own_table(self, ledger):
        _write_ledger(ledger, [
            _defer(_DECISION_ID, finding="ledger defer"), _engineer_decision(_SUCCESSOR_ID, finding="ledger keep"),
        ])
        body = "\n".join([
            _DELIM_START,
            "## Deferred review findings",
            "| Finding | Source | DEFER criterion | Rationale |",
            "| --- | --- | --- | --- |",
            "| legacy defer | old.py:1 | orthogonal scope | legacy rationale |",
            "## Settled review findings",
            "| Finding | Source | Decided by | Rationale | Decided | Id |",
            "| --- | --- | --- | --- | --- | --- |",
            "| swept keep | old.py:2 | plan-architect | swept rationale | 2026-08-01 round 1 | deadbeef0000 |",
            _DELIM_END,
        ])

        merged = _render_body(ledger, body).stdout

        lines = merged.splitlines()
        settled_start = lines.index("## Settled review findings")
        deferred_rows = _table_rows("\n".join(lines[lines.index("## Deferred review findings"):settled_start]))
        settled_rows = _table_rows("\n".join(lines[settled_start:]))
        assert [_split_gfm_cells(row)[0] for row in deferred_rows] == ["ledger defer", "legacy defer"]
        assert [_split_gfm_cells(row)[0] for row in settled_rows] == ["ledger keep", "swept keep"]

    def test_a_kept_row_has_its_comment_opener_neutralized(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        body = "\n".join([
            _DELIM_START, "## Deferred review findings", "| Finding | Source | DEFER criterion | Rationale |",
            "| --- | --- | --- | --- |", "| hostile <!-- cell | x.py:1 | orthogonal scope | why |", _DELIM_END,
        ])

        merged = _render_body(ledger, body).stdout

        assert "hostile &lt;!-- cell" in merged
        assert merged.count("<!--") == 2

    def test_a_row_whose_decision_was_closed_is_dropped(self, ledger):
        _write_ledger(ledger, [
            _defer(_DECISION_ID, finding="closed one"), _address(_SUCCESSOR_ID, ref=_DECISION_ID),
            _defer("777777777777", finding="still live"),
        ])
        stale_block = "\n".join([
            _DELIM_START, "## Deferred review findings", "| Finding | Source | DEFER criterion | Rationale | Decided | Id |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| closed one | a.py:1-2 | {_CRITERION} | why | 2026-09-01 round 1 | {_DECISION_ID} |", _DELIM_END,
        ])

        merged = _render_body(ledger, stale_block + "\n").stdout

        assert "closed one" not in merged
        assert "still live" in merged

    def test_the_block_is_dropped_when_no_rows_remain(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID), _address(_SUCCESSOR_ID, ref=_DECISION_ID)])
        stale_block = "\n".join([
            _DELIM_START, "## Deferred review findings", "| Finding | Source | DEFER criterion | Rationale | Decided | Id |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| closed one | a.py:1-2 | {_CRITERION} | why | 2026-09-01 round 1 | {_DECISION_ID} |", _DELIM_END,
        ])

        merged = _render_body(ledger, "Head\n" + stale_block + "\nTail\n").stdout

        assert merged == "Head\nTail\n"

    def test_a_body_with_no_block_and_no_live_decision_is_returned_unchanged(self, ledger):
        _write_ledger(ledger, [])

        assert _render_body(ledger, "Just a description\n").stdout == "Just a description\n"

    def test_rendering_the_output_of_a_render_is_a_fixed_point_with_hostile_cells(self, ledger):
        hostile = "a|b \\| c \\\\| d `e` ``f`` <!-- g \U0001F600 h\\"
        _write_ledger(ledger, [
            _defer(_DECISION_ID, finding=hostile, rationale=hostile, source="a|b.py:1-2"),
            _engineer_decision(_SUCCESSOR_ID, carry_forward=True, finding=hostile, rationale=hostile, engineer_quote=hostile),
            _carry(_CARRY_ID, _SUCCESSOR_ID, finding=hostile, rationale=hostile),
            _engineer_decision("999999999999", enforcement_invariant=True, finding=hostile, engineer_quote=hostile),
        ])
        first = _render_body(ledger, "Intro text\n\nMore text\n").stdout

        second = _render_body(ledger, first).stdout

        assert second == first
        assert first.count(_DELIM_START) == 1

    def test_rendering_a_body_that_holds_a_legacy_row_is_also_a_fixed_point(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        body = "\n".join([
            "Intro", _DELIM_START, "## Deferred review findings", "| Finding | Source | DEFER criterion | Rationale |",
            "| --- | --- | --- | --- |", "| legacy | x.py:1 | orthogonal scope | why |", _DELIM_END, "",
        ])
        first = _render_body(ledger, body).stdout

        assert _render_body(ledger, first).stdout == first

    def test_a_fenced_delimiter_pair_with_no_real_block_leaves_the_fence_unchanged_and_appends_the_block(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        fenced = f"```\n{_DELIM_START}\n{_DELIM_END}\n```\n"

        merged = _render_body(ledger, "Example:\n" + fenced).stdout

        assert merged.startswith("Example:\n" + fenced)
        assert merged.count(_DELIM_START) == 2
        assert merged.endswith(_DELIM_END + "\n")

    def test_a_fenced_pair_beside_a_real_block_replaces_only_the_real_block(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID, finding="replacement")])
        fenced = f"~~~~\n{_DELIM_START}\nquoted\n{_DELIM_END}\n~~~~\n"
        body = f"{fenced}\n{_DELIM_START}\nold\n{_DELIM_END}\n"

        merged = _render_body(ledger, body).stdout

        assert merged.startswith(fenced + "\n" + _DELIM_START + "\n## Deferred")
        assert "quoted" in merged and "old" not in merged and "replacement" in merged

    def test_a_shorter_closing_fence_does_not_close_the_fence(self, ledger):
        _write_ledger(ledger, [_defer(_DECISION_ID)])
        body = f"````\n```\n{_DELIM_START}\n````\n{_DELIM_START}\n{_DELIM_END}\n"

        merged = _render_body(ledger, body).stdout

        assert merged.startswith(f"````\n```\n{_DELIM_START}\n````\n{_DELIM_START}\n## Deferred")
