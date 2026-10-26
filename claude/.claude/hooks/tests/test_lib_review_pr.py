"""Unit tests for _lib.sh's _lib_review_pr_completion_marker_fields, _lib_parse_pr_identity
and the review-pr artifact helpers (_lib_review_pr_artifact_path, the provenance schema,
the worktree template, select-session-worktrees, the review-shaped worktree name and
_lib_review_pr_reviewer_findings_dir).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest

from ._lib_sh_helpers import _LIB_SH, _run_lib_call
from .test_config_lib import _locale_that_widens_ascii_ranges

# --- _lib_review_pr_completion_marker_fields -------------------------------
#
# review-pr-post.sh's only read of the completion marker /review-pr's
# `write review-pr` arm writes -- session-scoped (never a cross-session
# glob, unlike _lib_marker_value_present in test_lib_markers.py), since this authorizes a
# `gh pr review` POST rather than releasing a gate for a read.


def _review_pr_completion_marker_fields(
    config_dir: Path, repo_hash: str, session_id: str
) -> subprocess.CompletedProcess:
    argv = [
        "bash", "-c", f'. {_LIB_SH}; _lib_review_pr_completion_marker_fields "$@"', "bash",
        str(config_dir), repo_hash, session_id,
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _write_review_pr_marker(config_dir: Path, repo_hash: str, session_id: str, content: str) -> None:
    marker_dir = config_dir / "review-pr-markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / f"{repo_hash}.{session_id}").write_text(content)


def test_review_pr_completion_marker_fields_returns_the_four_stored_lines(tmp_path: Path) -> None:
    _write_review_pr_marker(tmp_path, "repohash", "session-a", "foo/bar#42\nabc123\ndef456\ncheckout\n")
    result = _review_pr_completion_marker_fields(tmp_path, "repohash", "session-a")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "foo/bar#42\nabc123\ndef456\ncheckout\n"


def test_review_pr_completion_marker_fields_tolerates_missing_trailing_newline(tmp_path: Path) -> None:
    _write_review_pr_marker(tmp_path, "repohash", "session-a", "foo/bar#42\nabc123\ndef456\ncheckout")
    result = _review_pr_completion_marker_fields(tmp_path, "repohash", "session-a")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "foo/bar#42\nabc123\ndef456\ncheckout\n"


def test_review_pr_completion_marker_fields_absent_marker_returns_1_with_no_output(
    tmp_path: Path,
) -> None:
    result = _review_pr_completion_marker_fields(tmp_path, "repohash", "session-a")
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("foo/bar#42\nabc123\ndef456\n", id="only_three_lines"),
        pytest.param("foo/bar#42\n\ndef456\ncheckout\n", id="empty_middle_line"),
        pytest.param("\nabc123\ndef456\ncheckout\n", id="empty_first_line"),
        pytest.param("foo/bar#42\nabc123\n\ncheckout\n", id="empty_third_line"),
        pytest.param("foo/bar#42\nabc123\ndef456\n\n", id="empty_fourth_line"),
        pytest.param("", id="empty_file"),
    ],
)
def test_review_pr_completion_marker_fields_malformed_content_returns_1_with_no_output(
    tmp_path: Path, content: str
) -> None:
    """A marker that doesn't parse into exactly four non-empty lines must
    never authorize a post on partial data -- each malformed shape below
    fails closed the same way an absent marker does."""
    _write_review_pr_marker(tmp_path, "repohash", "session-a", content)
    result = _review_pr_completion_marker_fields(tmp_path, "repohash", "session-a")
    assert result.returncode != 0
    assert result.stdout == ""


def test_review_pr_completion_marker_fields_wrong_session_returns_1(tmp_path: Path) -> None:
    """Session-scoped, unlike _lib_marker_value_present's cross-session glob
    in test_lib_markers.py: a marker written under one session must not authorize a read from
    another -- see test_other_sessions_marker_does_not_leak_bypass for the
    equivalent property on the active-bypass marker."""
    _write_review_pr_marker(tmp_path, "repohash", "session-a", "foo/bar#42\nabc123\ndef456\ncheckout\n")
    result = _review_pr_completion_marker_fields(tmp_path, "repohash", "session-b")
    assert result.returncode != 0
    assert result.stdout == ""


def test_review_pr_completion_marker_fields_wrong_repo_hash_returns_1(tmp_path: Path) -> None:
    _write_review_pr_marker(tmp_path, "repohash", "session-a", "foo/bar#42\nabc123\ndef456\ncheckout\n")
    result = _review_pr_completion_marker_fields(tmp_path, "otherrepohash", "session-a")
    assert result.returncode != 0
    assert result.stdout == ""


def test_review_pr_completion_marker_fields_rejects_traversal_session_id(tmp_path: Path) -> None:
    """Routed through _lib_valid_session_id_component before ever building
    the marker path -- a '../' session id must not escape review-pr-markers/.
    The canary is a valid marker the traversing id reaches, so only the guard
    can produce the refusal."""
    (tmp_path / "review-pr-markers" / "repohash.x").mkdir(parents=True)
    (tmp_path / "canary").write_text("foo/bar#42\nabc123\ndef456\ncheckout\n")
    result = _review_pr_completion_marker_fields(tmp_path, "repohash", "x/../../canary")
    assert result.returncode != 0
    assert result.stdout == ""


# --- _lib_parse_pr_identity -------------------------------------------------
#
# Every review-pr script that takes a PR identity calls this helper for its
# split and validation, so none carries its own copy.


def _parse_pr_identity(pr_identity: str) -> subprocess.CompletedProcess:
    argv = [
        "bash", "-c", f'. {_LIB_SH}; _lib_parse_pr_identity "$@"', "bash", pr_identity,
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def test_parse_pr_identity_returns_owner_repo_and_number(tmp_path: Path) -> None:
    result = _parse_pr_identity("foo/bar#42")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "foo/bar\n42\n"


@pytest.mark.parametrize(
    "pr_identity",
    [
        pytest.param("no-hash-or-slash", id="no_hash_or_slash"),
        pytest.param("foo/bar#NOTANUMBER", id="non_numeric_number"),
        pytest.param("#42", id="empty_owner_repo"),
        # A segment made entirely of '.'/'-' characters is a valid, nonempty
        # run under a naive [A-Za-z0-9._-]+ class, and turns a
        # `repos/$OWNER_REPO/...` gh api interpolation into a
        # path-traversal shape.
        pytest.param("../..#5", id="traversal_segments"),
        pytest.param("..#5", id="single_dot_segment"),
        # Each half independently: "../..#5" above has both halves bad, which
        # doesn't distinguish a regex that validates each half from one that
        # only checks the owner-side (or the whole string) loosely. A
        # legitimate owner paired with a traversal repo, and vice versa, pin
        # that both '/'-delimited segments are checked on their own.
        pytest.param("foo/..#5", id="repo_half_traversal_only"),
        pytest.param("../foo#5", id="owner_half_traversal_only"),
        # gh's `-R HOST/OWNER/REPO` would override a pinned GH_HOST, so a
        # host-qualified identity must never reach it.
        pytest.param("github.com/foo/bar#5", id="host_qualified"),
    ],
)
def test_parse_pr_identity_rejects_malformed_shapes(tmp_path: Path, pr_identity: str) -> None:
    result = _parse_pr_identity(pr_identity)
    assert result.returncode != 0
    assert result.stdout == ""


def test_parse_pr_identity_accepts_dot_and_hyphen_alongside_alnum(tmp_path: Path) -> None:
    """Bounds the owner/repo regex from the other side of the
    traversal-rejection cases above: a segment mixing '.'/'-' with at least
    one alphanumeric character (an ordinary GitHub owner/repo shape) must
    still pass."""
    result = _parse_pr_identity("my-org/my.repo#5")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "my-org/my.repo\n5\n"


class TestLibReviewPrArtifactPath:
    """Direct unit coverage for _lib_review_pr_artifact_path -- the one
    shared derivation of every review-pr session-scoped artifact path."""

    def test_builds_the_dotted_active_dir_path(self) -> None:
        result = _run_lib_call(
            '_lib_review_pr_artifact_path "/home/u/.claude" "sess-1" provenance', env=dict(os.environ)
        )
        assert result.returncode == 0
        assert result.stdout == "/home/u/.claude/.review-pr-active.d/sess-1.provenance"

    def test_suffix_carrying_its_own_dot_is_passed_through_unmodified(self) -> None:
        """SUFFIX carries no leading dot by convention, but "context.json"
        (an already-dotted suffix) must still round-trip unchanged -- this
        function does no dot-stripping or validation of its own."""
        result = _run_lib_call(
            '_lib_review_pr_artifact_path "/home/u/.claude" "sess-1" context.json', env=dict(os.environ)
        )
        assert result.returncode == 0
        assert result.stdout == "/home/u/.claude/.review-pr-active.d/sess-1.context.json"


class TestLibReviewPrProvenanceSchema:
    """Direct unit coverage for _lib_write_review_pr_provenance and
    _lib_review_pr_provenance_field -- the one writer and reader of the
    provenance schema. marker-clear-stale.py's own "pid=" read (Python, not
    this lib) follows the same schema."""

    def test_round_trips_every_written_field(self, tmp_path: Path) -> None:
        provenance = tmp_path / "sess.provenance"
        write = _run_lib_call(
            f'_lib_write_review_pr_provenance "{provenance}" '
            'pr_identity=foo/bar#42 head_ref_oid=abc123 pid=999 mode=checkout',
            env=dict(os.environ),
        )
        assert write.returncode == 0, write.stderr
        assert provenance.read_text().splitlines()[0] == "schema=1"

        for key, expected in (
            ("pr_identity", "foo/bar#42"),
            ("head_ref_oid", "abc123"),
            ("pid", "999"),
            ("mode", "checkout"),
        ):
            read = _run_lib_call(
                f'_lib_review_pr_provenance_field "{provenance}" {key}', env=dict(os.environ)
            )
            assert read.returncode == 0, read.stderr
            assert read.stdout == expected

    def test_unknown_key_returns_one_with_no_output(self, tmp_path: Path) -> None:
        provenance = tmp_path / "sess.provenance"
        _run_lib_call(
            f'_lib_write_review_pr_provenance "{provenance}" pr_identity=foo/bar#42',
            env=dict(os.environ),
        )
        result = _run_lib_call(
            f'_lib_review_pr_provenance_field "{provenance}" nonexistent_key', env=dict(os.environ)
        )
        assert result.returncode != 0
        assert result.stdout == ""

    def test_a_file_with_no_schema_header_is_rejected_even_with_a_matching_key_value_line(
        self, tmp_path: Path
    ) -> None:
        """A file without the `schema=1` header must fail closed rather than
        being partially read -- even when a line happens to look like a valid
        key=value pair."""
        provenance = tmp_path / "sess.provenance"
        provenance.write_text("pr_identity=foo/bar#42\nhead_ref_oid=abc123\n")
        result = _run_lib_call(
            f'_lib_review_pr_provenance_field "{provenance}" pr_identity', env=dict(os.environ)
        )
        assert result.returncode != 0
        assert result.stdout == ""

    def test_a_field_unknown_to_this_reader_does_not_break_reading_the_known_fields(
        self, tmp_path: Path
    ) -> None:
        """The schema is additive: a file carrying a key this call never asks
        for (e.g. fetched_sha) must not disturb any other field's read."""
        provenance = tmp_path / "sess.provenance"
        _run_lib_call(
            f'_lib_write_review_pr_provenance "{provenance}" '
            'pr_identity=foo/bar#42 head_ref_oid=abc123 pid=999 mode=checkout fetched_sha=deadbeef',
            env=dict(os.environ),
        )
        result = _run_lib_call(
            f'_lib_review_pr_provenance_field "{provenance}" mode', env=dict(os.environ)
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "checkout"

    def test_a_value_containing_equals_signs_round_trips_whole(self, tmp_path: Path) -> None:
        provenance = tmp_path / "sess.provenance"
        _run_lib_call(
            f'_lib_write_review_pr_provenance "{provenance}" pr_identity=foo/bar#42 head_ref_oid=a=b=c',
            env=dict(os.environ),
        )
        result = _run_lib_call(
            f'_lib_review_pr_provenance_field "{provenance}" head_ref_oid', env=dict(os.environ)
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "a=b=c"

    def test_a_key_that_is_a_prefix_of_another_key_reads_only_its_own_line(self, tmp_path: Path) -> None:
        """`pid` must not match the `pid_extra=` line that precedes its own."""
        provenance = tmp_path / "sess.provenance"
        _run_lib_call(
            f'_lib_write_review_pr_provenance "{provenance}" pid_extra=111 pid=999', env=dict(os.environ)
        )
        present = _run_lib_call(
            f'_lib_review_pr_provenance_field "{provenance}" pid', env=dict(os.environ)
        )
        assert present.returncode == 0, present.stderr
        assert present.stdout == "999"

        only_longer_key = tmp_path / "only-longer-key.provenance"
        _run_lib_call(
            f'_lib_write_review_pr_provenance "{only_longer_key}" pid_extra=111', env=dict(os.environ)
        )
        absent = _run_lib_call(
            f'_lib_review_pr_provenance_field "{only_longer_key}" pid', env=dict(os.environ)
        )
        assert absent.returncode != 0
        assert absent.stdout == ""

    def test_writer_returns_non_zero_when_the_destination_cannot_be_written(self, tmp_path: Path) -> None:
        missing_directory_destination = tmp_path / "absent-directory" / "sess.provenance"
        result = _run_lib_call(
            f'_lib_write_review_pr_provenance "{missing_directory_destination}" pr_identity=foo/bar#42',
            env=dict(os.environ),
        )
        assert result.returncode != 0
        assert not missing_directory_destination.exists()


class TestLibReviewPrWorktreeTemplate:
    """Direct unit coverage for _lib_review_pr_worktree_template -- the
    naming convention review-pr-checkout.sh creates worktrees under and
    _lib_review_pr_select_session_worktrees (below) discovers them by."""

    def test_builds_the_mktemp_template_from_session_and_pr_number(self) -> None:
        result = _run_lib_call(
            '_lib_review_pr_worktree_template "/repo" "sess-1" "42"', env=dict(os.environ)
        )
        assert result.returncode == 0
        assert result.stdout == "/repo/.claude/worktrees/review-pr-sess-1-42-XXXXXX"


_PORCELAIN_HEAD = "HEAD " + "a" * 40


def _porcelain_record(path: str, *extra_lines: str) -> str:
    """One `git worktree list --porcelain` record: a path line, then a HEAD
    line and `detached` unless `extra_lines` replaces them."""
    tail = extra_lines or (_PORCELAIN_HEAD, "detached")
    return "\n".join([f"worktree {path}", *tail]) + "\n"


# mktemp's random suffix differs per draw, so several draws make a selector
# class narrower than [A-Za-z0-9] fail with near certainty. A class of only
# lowercase letters and digits passes one draw with probability (36/62)^6,
# about 4%.
_TEMPLATE_CONTRACT_DRAWS = 20

# Every character class _lib_valid_session_id_component allows (upper, lower,
# digit, underscore, hyphen), with a hyphen at the end so the template's
# `-<number>-` joint follows a hyphen.
_WIDEST_VALID_SESSION_ID = "Az09_-Zz_09-"
_TEMPLATE_CONTRACT_SESSION_IDS = ["sess-1", _WIDEST_VALID_SESSION_ID]


class TestLibReviewPrSelectSessionWorktrees:
    """Direct unit coverage for _lib_review_pr_select_session_worktrees --
    the discovery function review-pr-finish.sh's sweep relies on. Fed
    synthetic porcelain text, so shapes real git cannot be coerced into
    emitting (a record missing HEAD, a truncated record) stay reachable."""

    MAIN = "/repo"
    SESSION = "sess-1"
    WORKTREES_DIR = "/repo/.claude/worktrees"

    def _select(self, porcelain: str, session_id: str | None = None) -> subprocess.CompletedProcess:
        session = self.SESSION if session_id is None else session_id
        return subprocess.run(
            [
                "bash", "-c",
                f'. {_LIB_SH}; _lib_review_pr_select_session_worktrees "$1" "$2" "$3"',
                "select", porcelain, self.MAIN, session,
            ],
            capture_output=True, text=True, check=False,
        )

    def test_selects_only_this_sessions_worktrees_from_a_mixed_listing(self) -> None:
        own_first = f"{self.WORKTREES_DIR}/review-pr-sess-1-42-aB3dE9"
        own_second = f"{self.WORKTREES_DIR}/review-pr-sess-1-7-Zz09Qq"
        porcelain = "\n".join([
            _porcelain_record(self.MAIN, _PORCELAIN_HEAD, "branch refs/heads/main"),
            _porcelain_record(own_first),
            _porcelain_record(f"{self.WORKTREES_DIR}/review-pr-sess-2-42-aB3dE9"),
            _porcelain_record(f"{self.WORKTREES_DIR}/review-pr-sess-1-extra-42-aB3dE9"),
            _porcelain_record(f"{self.WORKTREES_DIR}/review-pr-sess-1-42"),
            _porcelain_record(f"{self.WORKTREES_DIR}/feature-branch"),
            _porcelain_record("/elsewhere/review-pr-sess-1-42-aB3dE9"),
            _porcelain_record(f"{self.WORKTREES_DIR}/nested/review-pr-sess-1-42-aB3dE9"),
            _porcelain_record(own_second),
        ])
        result = self._select(porcelain)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [own_first, own_second]

    def test_no_matching_worktree_prints_nothing_and_succeeds(self) -> None:
        result = self._select(_porcelain_record(self.MAIN, _PORCELAIN_HEAD, "branch refs/heads/main"))
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_malformed_porcelain_neither_crashes_nor_hides_the_well_formed_matches(self) -> None:
        """A record missing its HEAD/detached lines, a `worktree` line with
        no path, a truncated `worktre` line, and a final record cut off
        mid-line must each be skipped or tolerated without disturbing the
        matches around them."""
        missing_tail = f"{self.WORKTREES_DIR}/review-pr-sess-1-1-AAAAAA"
        well_formed = f"{self.WORKTREES_DIR}/review-pr-sess-1-2-BBBBBB"
        porcelain = (
            f"worktree {missing_tail}\n\n"
            "worktree \n" f"{_PORCELAIN_HEAD}\n\n"
            f"worktre {self.WORKTREES_DIR}/review-pr-sess-1-3-CCCCCC\n\n"
            + _porcelain_record(well_formed)
            + "\n"
            f"worktree {self.WORKTREES_DIR}/review-pr-sess-1-4-DD"
        )
        result = self._select(porcelain)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [missing_tail, well_formed]

    def test_a_session_whose_id_extends_this_ones_with_a_digit_led_segment_is_not_selected(self) -> None:
        """`sess-1-2`'s worktree, `review-pr-sess-1-2-42-aB3dE9`, leaves the
        remainder `2-42-aB3dE9` after `sess-1`'s prefix. Only the strict
        six-alphanumeric tail stops that reading as digits `2`, then a
        suffix."""
        extended_session_worktree = f"{self.WORKTREES_DIR}/review-pr-sess-1-2-42-aB3dE9"
        porcelain = _porcelain_record(extended_session_worktree)

        for_sess_1 = self._select(porcelain, "sess-1")
        for_sess_1_2 = self._select(porcelain, "sess-1-2")

        assert for_sess_1.returncode == 0, for_sess_1.stderr
        assert for_sess_1.stdout == ""
        assert for_sess_1_2.returncode == 0, for_sess_1_2.stderr
        assert for_sess_1_2.stdout.splitlines() == [extended_session_worktree]

    @pytest.mark.parametrize(
        "worktree_name",
        [
            "review-pr-sess-1-42-aB3dE",
            "review-pr-sess-1-42-aB3dE9x",
            "review-pr-sess-1-42-aB3d_9",
            "review-pr-sess-1-42-aB3d.9",
            "review-pr-sess-1-42-aB3d 9",
            "review-pr-sess-1--aB3dE9",
        ],
        ids=["suffix-5", "suffix-7", "underscore-in-suffix", "dot-in-suffix", "space-in-suffix", "empty-number"],
    )
    def test_a_name_outside_the_strict_tail_shape_is_not_selected(self, worktree_name: str) -> None:
        result = self._select(_porcelain_record(f"{self.WORKTREES_DIR}/{worktree_name}"))
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "path",
        [
            "/repo/.claude/worktrees/../review-pr-sess-1-42-aB3dE9",
            "/repo/.claude/worktrees/review-pr-sess-1-42-aB3dE9/..",
            "/repo/.claude/worktrees/review-pr-sess-1-42-aB3dE9/../review-pr-sess-1-42-Zz09Qq",
        ],
        ids=["parent-first", "trailing-dotdot", "sibling-via-dotdot"],
    )
    def test_a_path_with_a_dotdot_segment_is_not_selected(self, path: str) -> None:
        result = self._select(_porcelain_record(path))
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    @pytest.mark.parametrize("session_id", _TEMPLATE_CONTRACT_SESSION_IDS)
    def test_every_directory_the_checkout_template_creates_is_selected(
        self, tmp_path: Path, session_id: str
    ) -> None:
        """Creator/discoverer contract: a real `mktemp -d` expansion of
        _lib_review_pr_worktree_template must match the selector's tail
        shape, so changing one side alone cannot orphan review worktrees."""
        main_root = tmp_path / "main"
        (main_root / ".claude" / "worktrees").mkdir(parents=True)
        script = textwrap.dedent(f"""\
            . {shlex.quote(str(_LIB_SH))}
            _lib_valid_session_id_component "$2" || exit 9
            draws=0
            for attempt in {{1..{_TEMPLATE_CONTRACT_DRAWS}}}; do
              created=$(mktemp -d "$(_lib_review_pr_worktree_template "$1" "$2" "$3")") || exit 10
              selected=$(_lib_review_pr_select_session_worktrees "worktree $created" "$1" "$2") || exit 11
              if [ "$selected" != "$created" ]; then
                printf 'created %s, selected [%s]\\n' "$created" "$selected" >&2
                exit 12
              fi
              draws=$((draws + 1))
            done
            printf '%s' "$draws"
        """)
        result = subprocess.run(
            ["bash", "-c", script, "contract", str(main_root), session_id, "42"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == str(_TEMPLATE_CONTRACT_DRAWS), "the loop must run every draw, not vacuously"

    @pytest.mark.parametrize("session_id", ["", "../evil", "a/b", "has space"])
    def test_invalid_session_id_fails_without_output(self, session_id: str) -> None:
        result = self._select(
            _porcelain_record(f"{self.WORKTREES_DIR}/review-pr-sess-1-42-aB3dE9"), session_id
        )
        assert result.returncode != 0
        assert result.stdout == ""


class TestLibReviewPrWorktreeNameIsReviewShaped:
    """_lib_review_pr_worktree_name_is_review_shaped decides which worktree
    directory names _lib_first_live_linked_worktree may skip, so it must accept
    every name the checkout template creates and nothing looser."""

    @staticmethod
    def _is_review_shaped(name: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_review_pr_worktree_name_is_review_shaped "$1"', "bash", name],
            capture_output=True, text=True, check=False,
        )

    @pytest.mark.parametrize(
        "name",
        [
            "review-pr-sess-1-42-abc123",
            "review-pr-0b5e0f6a-0a0d-4a39-9d7e-1f2f3a4b5c6d-7-aB3dE9",
            "review-pr-sess_1-1234-ABCDEF",
        ],
    )
    def test_accepts_a_name_the_checkout_template_can_create(self, name: str) -> None:
        assert self._is_review_shaped(name).returncode == 0

    @pytest.mark.parametrize(
        "name",
        [
            "review-pr-hardening",
            "review-pr-sess-1-42",
            "review-pr-sess-1-42-abc12",
            "review-pr-sess-1-42-abc1234",
            "review-pr-sess-1-x-abc123",
            "review-pr--42-abc123",
            "review-pr-sess-1-42-abc12!",
            "review-pr-sess.x-123-AbC123",  # '.' is outside the session-id class
            "xreview-pr-sess-1-42-abc123",
            "review-pr-sess-1-42-abc123-extra",
            "feature",
            "",
        ],
    )
    def test_rejects_a_name_the_checkout_template_never_creates(self, name: str) -> None:
        assert self._is_review_shaped(name).returncode != 0

    def test_rejects_a_non_ascii_letter_in_the_session_id_under_a_widening_locale(self) -> None:
        """Mirrors the session-id validator's locale test: a bracket range in
        the shared character class would accept `sess<e-acute>` where the
        collation puts accented letters inside `[A-Za-z]`."""
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_review_pr_worktree_name_is_review_shaped "$1"', "bash",
             "review-pr-sessé-42-abc123"],
            env={**os.environ, "LC_ALL": _locale_that_widens_ascii_ranges()},
            capture_output=True, text=True, check=False,
        )
        assert result.returncode != 0

    @pytest.mark.parametrize("session_id", _TEMPLATE_CONTRACT_SESSION_IDS)
    def test_accepts_every_directory_the_checkout_template_creates(
        self, tmp_path: Path, session_id: str
    ) -> None:
        """Creator/skip contract: a real `mktemp -d` expansion of
        _lib_review_pr_worktree_template satisfies the predicate, for the
        widest session id _lib_valid_session_id_component accepts as well as
        a typical one."""
        main_root = tmp_path / "main"
        (main_root / ".claude" / "worktrees").mkdir(parents=True)
        script = textwrap.dedent(f"""\
            . {shlex.quote(str(_LIB_SH))}
            _lib_valid_session_id_component "$2" || exit 9
            draws=0
            for attempt in {{1..{_TEMPLATE_CONTRACT_DRAWS}}}; do
              created=$(mktemp -d "$(_lib_review_pr_worktree_template "$1" "$2" "$3")") || exit 10
              _lib_review_pr_worktree_name_is_review_shaped "${{created##*/}}" || {{ printf '%s\\n' "$created" >&2; exit 12; }}
              draws=$((draws + 1))
            done
            printf '%s' "$draws"
        """)
        result = subprocess.run(
            ["bash", "-c", script, "contract", str(main_root), session_id, "42"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == str(_TEMPLATE_CONTRACT_DRAWS), "the loop must run every draw, not vacuously"


class TestLibReviewPrReviewerFindingsDir:
    """_lib_review_pr_reviewer_findings_dir names the directory both
    review-pr-reviewer-findings-dir.sh (creates it) and review-pr-finish.sh
    (removes it) use, so the two cannot derive the path differently."""

    @staticmethod
    def _findings_dir(session_id: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_review_pr_reviewer_findings_dir "$1"', "bash", session_id],
            env={**os.environ, **(extra_env or {})}, capture_output=True, text=True, check=False,
        )

    def test_prints_the_literal_tmp_path_even_when_tmpdir_points_elsewhere(self, tmp_path: Path) -> None:
        """A TMPDIR-derived root would leave the reviewers' findings_path outside
        the reviewer gate's /tmp allow; see the comment on
        _lib_review_pr_reviewer_findings_dir in _lib.sh."""
        result = self._findings_dir("sess-1", {"TMPDIR": str(tmp_path)})
        assert result.returncode == 0, result.stderr
        assert result.stdout == "/tmp/review-pr-reviewer-findings-sess-1\n"

    @pytest.mark.parametrize("session_id", ["", "../x"])
    def test_invalid_session_id_fails_without_output(self, session_id: str) -> None:
        result = self._findings_dir(session_id)
        assert result.returncode == 1
        assert result.stdout == ""
