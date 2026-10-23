"""Tests for _lib.sh shared helper library."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import (
    GITLINK_AFTER,
    GITLINK_BEFORE,
    HOOKS_DIR,
    assert_cap_engaged,
    build_path_without,
    cumulative_review_value,
    head_sha,
    init_git_repo,
    isolated_git_diff,
    reference_normalized_text,
    require_cumulative_review_value,
    require_normalized_cumulative_review_text,
    scaled_shim_sleep,
    set_gitlink,
    write_scaled_timeout_shim,
)

from .test_config_lib import _installed_utf8_locale_names

LIB_SH = HOOKS_DIR / "_lib.sh"

# _lib.sh's slow-path tail calls are hardcoded to `_lib_capped_for 2 tail
# ...` -- not overridable via env var, so this mirrors that literal rather
# than reading it from the shell source.
SLOW_PATH_TAIL_TIMEOUT_CAP_SECONDS = 2


def _run_lib_fn(fn_call: str) -> str:
    """Source _lib.sh in a bash subprocess and evaluate fn_call."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; {fn_call}'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _advance_offset(
    transcript: Path, offset: int, current_size: int, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_advance_offset_past_complete_lines."""
    return subprocess.run(
        [
            "bash", "-c",
            f'. "{LIB_SH}"; _lib_advance_offset_past_complete_lines "$1" "$2" "$3"',
            "_advance_offset", str(transcript), str(offset), str(current_size),
        ],
        capture_output=True,
        text=True,
        env=env if env is not None else os.environ,
    )


def _hash_diff_text(text: str) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_hash_diff_text against `text`, positional
    (not string-interpolated) so arbitrary diff text needs no shell quoting."""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_hash_diff_text "$1"', "_hash_diff_text", text],
        capture_output=True,
        text=True,
    )


def _active_plan_hash(
    repo: Path, base: str = "", env_overrides: dict | None = None
) -> str:
    """Shell out to the real _lib_active_plan_hash against `repo`. `base`
    defaults to "" (no trusted in-progress state)."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_active_plan_hash "$1" "$2"',
         "_active_plan_hash", str(repo), base],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env_overrides or {})},
    )
    return result.stdout.strip()


def _code_review_marker_value(repo: Path, base: str) -> str:
    """Shell out to the real _lib_code_review_marker_value against `repo`."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_code_review_marker_value "$1" "$2"',
         "_code_review_marker_value", str(repo), base],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _active_plan_files(
    repo: Path, base: str = "", env_overrides: dict | None = None
) -> subprocess.CompletedProcess:
    """Shell out to the real _lib_active_plan_files against `repo`, returning
    the raw CompletedProcess so callers can assert on exit status and stdout
    together. `base` defaults to "" (no trusted in-progress state)."""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_active_plan_files "$1" "$2"',
         "_active_plan_files", str(repo), base],
        capture_output=True,
        text=True,
        env={**os.environ, **(env_overrides or {})},
    )


def _is_repo_plan_file(repo_root: Path, abs_path: Path) -> bool:
    """Shell out to the real _lib_is_repo_plan_file."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; _lib_is_repo_plan_file "$1" "$2"',
         "_is_repo_plan_file", str(repo_root), str(abs_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _find_case_insensitive_collation_locale() -> str | None:
    """Return an installed locale whose collation interleaves upper- and
    lowercase (so `sort` orders `B.md`/`a.md` differently than the C locale
    does), or None if the machine only has C/POSIX available."""
    try:
        installed = subprocess.run(
            ["locale", "-a"], capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None

    for candidate in installed:
        if candidate.lower().replace("-", "") in ("c", "posix", "c.utf8"):
            continue
        ordering = subprocess.run(
            ["bash", "-c", 'printf "B\\na\\n" | sort'],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": candidate},
        )
        if ordering.returncode == 0 and ordering.stdout == "a\nB\n":
            return candidate
    return None


class TestMarkerLibRepoHash:
    def test_known_path_matches_python_sha256(self):
        path = "/some/known/path"
        expected = hashlib.sha256(path.encode()).hexdigest()
        actual = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        assert actual == expected, (
            f"_marker_lib_repo_hash produced {actual!r}, expected {expected!r}"
        )

    def test_trailing_newline_produces_different_hash(self):
        # If printf '%s' ever starts emitting a trailing newline, the hash would
        # match sha256("/tmp/foo\n") instead of sha256("/tmp/foo"). Assert the
        # two are distinct so this test fails if the recipe regresses.
        path = "/tmp/foo"
        hash_without_newline = hashlib.sha256(path.encode()).hexdigest()
        hash_with_newline = hashlib.sha256((path + "\n").encode()).hexdigest()
        actual = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        assert actual == hash_without_newline, (
            f"_marker_lib_repo_hash produced {actual!r}, expected no-newline hash {hash_without_newline!r}"
        )
        assert actual != hash_with_newline, (
            "hash matched the newline-suffixed input — printf '%s' may be adding a trailing newline"
        )

    def test_matches_inline_recipe(self):
        # Both the library function and the original inline recipe must produce
        # the same hash for the same path. This detects recipe drift between
        # the library and any remaining inline usage.
        path = "/tmp/test-repo"
        from_lib = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        from_inline = subprocess.run(
            ["bash", "-c", f"printf '%s' '{path}' | sha256sum | awk '{{print $1}}'"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert from_lib == from_inline, (
            f"Library hash {from_lib!r} != inline recipe {from_inline!r}"
        )

    def test_sha256sum_failure_returns_nonzero_with_empty_stdout(self):
        """_marker_lib_repo_hash delegates to _lib_hash_diff_text, so a
        broken sha256sum must propagate as a failing exit status with empty
        stdout rather than fail open -- callers like
        pr-diff-against-base.sh run this unchecked under set -euo
        pipefail and rely on a nonzero exit to abort instead of silently
        continuing with an empty hash."""
        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; sha256sum() {{ :; }}; _marker_lib_repo_hash "/tmp/test-repo"'],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert result.stdout.strip() == ""


class TestLibHashDiffText:
    """Direct coverage for _lib_hash_diff_text -- the sha256 recipe
    _lib_cumulative_review_value hashes its normalized text through, so a
    read-side and write-side digest for the same text always agree by
    construction (see _lib.sh's header on byte-identical output across the
    read and write sides)."""

    def test_known_text_matches_python_sha256(self):
        text = "diff --git a/f b/f\n+line\n"
        expected = hashlib.sha256(text.encode()).hexdigest()
        result = _hash_diff_text(text)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    def test_empty_text_is_not_a_failure(self):
        """sha256 of an empty string is itself a valid, non-empty digest --
        TEXT emptiness is a business-rule concern for marker.sh's own [ -z ]
        precondition on the read subject text, not a failure this helper
        reports."""
        expected = hashlib.sha256(b"").hexdigest()
        result = _hash_diff_text("")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    def test_digest_emptiness_guard_exercised_directly(self):
        """The post-hash [ -n "$digest" ] guard, exercised without going
        through _lib_cumulative_diff_hash's own subprocess-produced diff --
        a broken sha256sum must exit nonzero with empty stdout rather than
        silently succeed."""
        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; sha256sum() {{ :; }}; _lib_hash_diff_text "some text"'],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert result.stdout.strip() == ""


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


BODY_LOOKALIKE_PATH = "body-lookalikes.txt"


def _body_lookalike_lines(tag: str) -> list[str]:
    """Content lines that read as diff structure once their `+`, `-` or space prefix is ignored."""
    return [
        f"@@ -{tag} +{tag} @@",
        f"index {tag}..{tag}9 100644",
        f"--- {tag}",
        f"+++ {tag}",
        f"diff --git {tag} {tag}",
    ]


@pytest.fixture(scope="module")
def entry_shapes_repo(tmp_path_factory) -> Path:
    """A repo whose HEAD~1..HEAD diff holds one entry per git diff-entry shape the cumulative-review normalizer meets.

    Paths are named for their shape. Rename sources and targets, and the created and deleted paths,
    share no content, so rename detection pairs only the two renames.
    """
    repo = init_git_repo(tmp_path_factory.mktemp("entry-shapes"))
    (repo / ".git" / "info" / "attributes").write_text("*.bin diff=hexdump\n")
    (repo / "text-edit.txt").write_text("alpha\nbeta\ngamma\n")
    (repo / "text-deleted.txt").write_text("one\ntwo\nthree\n")
    (repo / "mode-and-edit.sh").write_text("echo a\necho b\n")
    (repo / "mode-only.sh").write_text("echo only\n")
    (repo / "exec-edit.sh").write_text("echo x\necho y\n")
    (repo / "exec-edit.sh").chmod(0o755)
    os.symlink("target-a", repo / "symlink-edit")
    (repo / "binary-edit.dat").write_bytes(b"\x00old binary\x01")
    (repo / "binary-deleted.dat").write_bytes(b"\x00to delete\x02")
    (repo / "textconv.bin").write_bytes(b"\x00hex before\x01")
    (repo / "renamed-plain.txt").write_text("".join(f"plain line {n}\n" for n in range(20)))
    (repo / "renamed-edited.txt").write_text("".join(f"edited line {n}\n" for n in range(20)))
    (repo / BODY_LOOKALIKE_PATH).write_text(
        "\n".join(_body_lookalike_lines("7") + _body_lookalike_lines("5")) + "\n"
    )
    _commit_all(repo, "shape fixtures, before")
    set_gitlink(repo, "gitlink", GITLINK_BEFORE)
    subprocess.run(["git", "commit", "-q", "-m", "gitlink, before"], cwd=repo, check=True)

    (repo / "text-edit.txt").write_text("alpha\nBETA\ngamma\n")
    (repo / "text-created.txt").write_text("fresh\ncontent\n")
    (repo / "text-deleted.txt").unlink()
    (repo / "mode-and-edit.sh").write_text("echo a\necho c\n")
    (repo / "mode-and-edit.sh").chmod(0o755)
    (repo / "mode-only.sh").chmod(0o755)
    (repo / "exec-edit.sh").write_text("echo x\necho z\n")
    (repo / "symlink-edit").unlink()
    os.symlink("target-b", repo / "symlink-edit")
    os.symlink("target-c", repo / "symlink-created")
    (repo / "binary-edit.dat").write_bytes(b"\x00new binary\x03")
    (repo / "binary-created.dat").write_bytes(b"\x00created blob\x04\x05")
    (repo / "binary-deleted.dat").unlink()
    (repo / "textconv.bin").write_bytes(b"\x00hex after\x06")
    subprocess.run(["git", "mv", "renamed-plain.txt", "renamed-plain-moved.txt"], cwd=repo, check=True)
    subprocess.run(["git", "mv", "renamed-edited.txt", "renamed-edited-moved.txt"], cwd=repo, check=True)
    edited_lines = [f"edited line {n}\n" for n in range(20)]
    edited_lines[10] = "edited line ten, changed\n"
    (repo / "renamed-edited-moved.txt").write_text("".join(edited_lines))
    (repo / BODY_LOOKALIKE_PATH).write_text(
        "\n".join(_body_lookalike_lines("5") + _body_lookalike_lines("3")) + "\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    set_gitlink(repo, "gitlink", GITLINK_AFTER)
    subprocess.run(["git", "commit", "-q", "-m", "shape fixtures, after"], cwd=repo, check=True)
    return repo


# The textconv shapes come only from porcelain `git diff`,
# because `pr-diff-against-base.sh`'s `git diff-tree` never applies textconv.
TEXTCONV_CONFIG = ("diff.hexdump.textconv=od -A n -t x1",)
SUPPRESS_BLANK_EMPTY_CONFIG = ("diff.suppressBlankEmpty=true",)
# An `index` line that still carries its blob-id pair; the normalizer strips the pair from a text entry's line.
INDEX_LINE_WITH_BLOB_IDS = re.compile(rb"index [0-9a-f]+\.\.[0-9a-f]+")


def _diff_entries(diff: bytes) -> dict[bytes, list[bytes]]:
    """Split a raw diff into entries keyed by their `diff --git` line. Body lines never start at column 0 with `diff --git`."""
    entries: dict[bytes, list[bytes]] = {}
    current_entry: list[bytes] = []
    for line in diff.split(b"\n"):
        if line.startswith(b"diff --git "):
            current_entry = entries.setdefault(line, [])
        current_entry.append(line)
    return entries


def _entry_for_path(diff: bytes, path: str) -> list[bytes]:
    """The one entry whose `diff --git` line ends in the post-image path `b/<path>`."""
    matching = [lines for header, lines in _diff_entries(diff).items() if header.endswith(f" b/{path}".encode())]
    assert len(matching) == 1, f"expected exactly one entry for {path}, got {len(matching)}"
    return matching[0]


def _index_lines(entry: list[bytes]) -> list[bytes]:
    return [line for line in entry if line.startswith(b"index ")]


def _line_after_index(entry: list[bytes]) -> bytes | None:
    """The line directly after the entry's `index` line, or None when the index line is the entry's last line."""
    index_positions = [position for position, line in enumerate(entry) if line.startswith(b"index ")]
    assert len(index_positions) == 1, f"expected one index line, got {index_positions}"
    following = index_positions[0] + 1
    return entry[following] if following < len(entry) else None


def _lines_after_each_index_line(diff: bytes) -> list[bytes | None]:
    """For every column-0 `index` line of a raw diff, the line directly after it (None at end of input)."""
    lines = diff.split(b"\n")
    return [
        lines[position + 1] if position + 1 < len(lines) else None
        for position, line in enumerate(lines)
        if line.startswith(b"index ")
    ]


def _body_lines_with_unexpected_prefix(diff: bytes, *, blank_line_allowed: bool) -> list[bytes]:
    """Lines between a hunk header and the next `diff --git` whose first byte is not a space, `+`, `-` or `\\`.

    A later hunk header (`@@`) is allowed. An empty line is allowed only under `diff.suppressBlankEmpty`.
    """
    unexpected: list[bytes] = []
    inside_hunk = False
    for line in diff.split(b"\n"):
        if line.startswith(b"diff --git "):
            inside_hunk = False
        elif inside_hunk:
            has_body_prefix = line[:1] in (b" ", b"+", b"-", b"\\") or line.startswith(b"@@")
            if not (has_body_prefix or (blank_line_allowed and line == b"")):
                unexpected.append(line)
        elif line.startswith(b"@@ "):
            inside_hunk = True
    return unexpected


class TestGitDiffEntryShapes:
    """Pins the raw `git diff` output facts that `_lib_normalize_cumulative_review_text` relies on.

    Calls no `_lib.sh` helper: if one of these fails, the normalizer's `index` and hunk-header rules
    rest on a wrong picture of git's output, and the rules need a design change, not a regex tweak.
    """

    TEXT_ENTRY_PATHS = [
        "text-edit.txt",
        "text-created.txt",
        "text-deleted.txt",
        "mode-and-edit.sh",
        "exec-edit.sh",
        "symlink-edit",
        "symlink-created",
        "gitlink",
        "renamed-edited-moved.txt",
    ]

    @pytest.fixture(scope="class")
    def default_diff(self, entry_shapes_repo: Path) -> bytes:
        return isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD")

    @pytest.mark.parametrize("path", TEXT_ENTRY_PATHS)
    def test_text_entry_index_line_is_directly_followed_by_a_minus_header(self, default_diff, path):
        entry = _entry_for_path(default_diff, path)
        line_after_index = _line_after_index(entry)
        assert line_after_index is not None and line_after_index.startswith(b"--- "), entry

    def test_mode_change_with_content_change_index_line_carries_no_mode(self, default_diff):
        entry = _entry_for_path(default_diff, "mode-and-edit.sh")
        assert re.fullmatch(rb"index [0-9a-f]+\.\.[0-9a-f]+", _index_lines(entry)[0]), entry
        assert b"old mode 100644" in entry and b"new mode 100755" in entry

    @pytest.mark.parametrize(
        ("path", "mode"),
        [("text-edit.txt", b"100644"), ("exec-edit.sh", b"100755"), ("symlink-edit", b"120000")],
        ids=["regular-file", "executable-file", "symlink"],
    )
    def test_in_place_text_edit_index_line_ends_in_the_file_mode(self, default_diff, path, mode):
        entry = _entry_for_path(default_diff, path)
        assert re.fullmatch(rb"index [0-9a-f]+\.\.[0-9a-f]+ " + mode, _index_lines(entry)[0]), entry

    def test_created_text_file_index_line_carries_no_mode(self, default_diff):
        entry = _entry_for_path(default_diff, "text-created.txt")
        assert b"new file mode 100644" in entry
        assert re.fullmatch(rb"index [0-9a-f]+\.\.[0-9a-f]+", _index_lines(entry)[0]), entry

    def test_created_symlink_states_its_mode_on_a_new_file_mode_line_before_a_mode_less_index_line(self, default_diff):
        entry = _entry_for_path(default_diff, "symlink-created")
        assert b"new file mode 120000" in entry
        assert entry.index(b"new file mode 120000") < entry.index(_index_lines(entry)[0]), entry
        assert re.fullmatch(rb"index [0-9a-f]+\.\.[0-9a-f]+", _index_lines(entry)[0]), entry

    def test_gitlink_bump_keeps_the_minus_header_after_its_index_line(self, default_diff):
        entry = _entry_for_path(default_diff, "gitlink")
        assert re.fullmatch(rb"index [0-9a-f]+\.\.[0-9a-f]+ 160000", _index_lines(entry)[0]), entry
        line_after_index = _line_after_index(entry)
        assert line_after_index is not None and line_after_index.startswith(b"--- "), entry

    @pytest.mark.parametrize("path", ["binary-edit.dat", "binary-created.dat", "binary-deleted.dat"])
    def test_binary_entry_index_line_is_directly_followed_by_binary_files(self, default_diff, path):
        entry = _entry_for_path(default_diff, path)
        line_after_index = _line_after_index(entry)
        assert line_after_index is not None and line_after_index.startswith(b"Binary files "), entry

    @pytest.mark.parametrize("path", ["mode-only.sh", "renamed-plain-moved.txt"])
    def test_mode_only_change_and_exact_rename_have_no_index_line(self, default_diff, path):
        entry = _entry_for_path(default_diff, path)
        assert _index_lines(entry) == [], entry

    def test_rename_with_edit_lists_its_headers_in_order_before_the_minus_header(self, default_diff):
        entry = _entry_for_path(default_diff, "renamed-edited-moved.txt")
        header_prefixes = [b"similarity index ", b"rename from ", b"rename to ", b"index ", b"--- ", b"+++ ", b"@@ "]
        positions = [
            next(position for position, line in enumerate(entry) if line.startswith(prefix))
            for prefix in header_prefixes
        ]
        assert positions == sorted(positions), entry

    def test_textconv_binary_renders_a_text_body_headed_by_a_minus_header(self, entry_shapes_repo, default_diff):
        without_textconv = _line_after_index(_entry_for_path(default_diff, "textconv.bin"))
        assert without_textconv is not None and without_textconv.startswith(b"Binary files "), (
            "the same path must render as a plain binary entry when no textconv command is configured"
        )
        diff = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD", config=TEXTCONV_CONFIG)
        entry = _entry_for_path(diff, "textconv.bin")
        line_after_index = _line_after_index(entry)
        assert line_after_index is not None and line_after_index.startswith(b"--- "), entry

    @pytest.mark.parametrize(
        "revisions",
        [("HEAD~2", "HEAD~1"), ("HEAD~1", "HEAD")],
        ids=["empty-file-creation", "empty-file-deletion"],
    )
    def test_empty_file_index_line_is_followed_by_the_next_entry_or_end_of_input(self, empty_file_repo, revisions):
        """Three entries in path order: an empty file mid-diff, the text edit, then an empty file last."""
        diff = isolated_git_diff(empty_file_repo, *revisions)
        following_lines = _lines_after_each_index_line(diff)
        assert len(following_lines) == 3, diff
        mid_diff_empty_file, text_edit, last_empty_file = following_lines
        assert mid_diff_empty_file is not None and mid_diff_empty_file.startswith(b"diff --git "), diff
        assert text_edit is not None and text_edit.startswith(b"--- "), diff
        assert last_empty_file is None, diff

    @pytest.mark.parametrize(
        "config",
        [(), SUPPRESS_BLANK_EMPTY_CONFIG, TEXTCONV_CONFIG],
        ids=["default", "suppress-blank-empty", "textconv"],
    )
    def test_every_hunk_body_line_starts_with_a_diff_prefix_character(self, entry_shapes_repo, config):
        diff = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD", config=config)
        assert _body_lines_with_unexpected_prefix(diff, blank_line_allowed=config == SUPPRESS_BLANK_EMPTY_CONFIG) == []

    def test_suppress_blank_empty_prints_a_context_blank_line_as_an_empty_line(self, tmp_path):
        """The one body line without a prefix character, so the grammar pin above is not vacuous for it."""
        repo = init_git_repo(tmp_path / "blank-context")
        (repo / "blank.txt").write_text("a\n\nb\nc\n")
        _commit_all(repo, "before")
        (repo / "blank.txt").write_text("a\n\nb\nC\n")
        _commit_all(repo, "after")
        default_diff = isolated_git_diff(repo, "HEAD~1", "HEAD")
        suppressed_diff = isolated_git_diff(repo, "HEAD~1", "HEAD", config=SUPPRESS_BLANK_EMPTY_CONFIG)
        assert b"\n \n" in default_diff
        assert b"\n\n" in suppressed_diff
        assert _body_lines_with_unexpected_prefix(default_diff, blank_line_allowed=False) == []
        assert _body_lines_with_unexpected_prefix(suppressed_diff, blank_line_allowed=False) == [b""]

    def test_ambient_git_config_count_variables_do_not_change_the_helper_output(self, entry_shapes_repo, monkeypatch):
        """`diff.noprefix=true` through `GIT_CONFIG_COUNT` would drop the `a/` and `b/` path prefixes."""
        expected = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD")
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.noprefix")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
        assert b"diff --git a/" in expected
        assert isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD") == expected

    @pytest.mark.parametrize(
        ("variable", "value"),
        [("GIT_DIFF_OPTS", "-u0"), ("GIT_EXTERNAL_DIFF", "false")],
        ids=["git-diff-opts", "git-external-diff"],
    )
    def test_ambient_diff_variables_do_not_change_the_helper_output(
        self, entry_shapes_repo, monkeypatch, variable, value
    ):
        """`GIT_DIFF_OPTS=-u0` would drop every context line. `GIT_EXTERNAL_DIFF=false` would fail the text diff."""
        expected = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD")
        assert b"\n alpha\n" in expected
        monkeypatch.setenv(variable, value)
        assert isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD") == expected

    def test_content_lines_that_read_as_diff_structure_keep_a_prefix_character(self, entry_shapes_repo):
        diff = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD")
        entry = _entry_for_path(diff, BODY_LOOKALIKE_PATH)
        for prefix in (b"-", b" ", b"+"):
            for lookalike in _body_lookalike_lines("7" if prefix == b"-" else "5" if prefix == b" " else "3"):
                assert prefix + lookalike.encode() in entry, (prefix, lookalike)
        assert len(_index_lines(entry)) == 1
        assert _body_lines_with_unexpected_prefix(diff, blank_line_allowed=False) == []


def _commit_variant(repo: Path, base_sha: str, branch: str, *file_sets: dict[str, bytes]) -> bytes:
    """Commit each file set in order on a new `branch` forked from `base_sha`. Return the raw diff of the last commit."""
    subprocess.run(["git", "checkout", "-q", "-b", branch, base_sha], cwd=repo, check=True)
    for file_set in file_sets:
        for name, content in file_set.items():
            (repo / name).write_bytes(content)
        _commit_all(repo, f"{branch} step")
    return isolated_git_diff(repo, "HEAD~1", "HEAD")


def _repo_with_base_file(tmp_path: Path, name: str, content: bytes) -> tuple[Path, str]:
    repo = init_git_repo(tmp_path / "variants")
    (repo / name).write_bytes(content)
    _commit_all(repo, "base")
    return repo, head_sha(repo)


def _sha256_of(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


DEFAULT_ENTRY_BODY = (" ctx", "+added", " ctx2")


def _entry_pair(
    path: str = "f.txt",
    body: tuple[str, ...] = DEFAULT_ENTRY_BODY,
    *,
    header: str = "@@ -3,2 +3,3 @@ def func():",
    expected_header: str = "@@ -,2 +,3 @@",
    index_line: str = "index 1111111..2222222 100644",
    expected_index_line: str = "index .. 100644",
    pre_index_lines: tuple[str, ...] = (),
) -> tuple[bytes, bytes]:
    """`(raw, hand-derived normalized)` text of one text entry. The expected side is written out here, not computed by a rule."""
    entry_head = [f"diff --git a/{path} b/{path}", *pre_index_lines]
    entry_tail = [f"--- a/{path}", f"+++ b/{path}"]
    raw_lines = [*entry_head, index_line, *entry_tail, header, *body]
    expected_lines = [*entry_head, expected_index_line, *entry_tail, expected_header, *body]
    return "\n".join(raw_lines).encode("latin-1"), "\n".join(expected_lines).encode("latin-1")


def _rename_pair(source: str, target: str) -> tuple[bytes, bytes]:
    """A 100% rename entry: no rule touches it, so raw and normalized are the same text."""
    entry = "\n".join(
        [f"diff --git a/{source} b/{target}", "similarity index 100%", f"rename from {source}", f"rename to {target}"]
    ).encode()
    return entry, entry


def _concatenated(*pairs: tuple[bytes, bytes]) -> tuple[bytes, bytes]:
    return b"\n".join(raw for raw, _ in pairs), b"\n".join(expected for _, expected in pairs)


# Every rule and every pass-through class in one hand-written text.
ORACLE_RAW_TEXT = b"""\
diff --git a/text.txt b/text.txt
index 1111111..2222222 100644
--- a/text.txt
+++ b/text.txt
@@ -12,3 +12,4 @@ def func():
 ctx
+added
 ctx2
 ctx3
@@ -5 +5 @@
-old
+new
@@ -1,0 +2 @@ TEXT
+inserted
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,3 @@
+x
+y
+z
diff --git a/empty.txt b/empty.txt
new file mode 100644
index 0000000..e69de29
diff --git a/blob.bin b/blob.bin
index 4444444..5555555 100644
Binary files a/blob.bin and b/blob.bin differ
diff --git a/last-empty.txt b/last-empty.txt
new file mode 100644
index 0000000..e69de29
"""
ORACLE_EXPECTED_NORMALIZED = b"""\
diff --git a/text.txt b/text.txt
index .. 100644
--- a/text.txt
+++ b/text.txt
@@ -,3 +,4 @@
 ctx
+added
 ctx2
 ctx3
@@ - + @@
-old
+new
@@ -,0 + @@
+inserted
diff --git a/new.txt b/new.txt
new file mode 100644
index ..
--- /dev/null
+++ b/new.txt
@@ -,0 +,3 @@
+x
+y
+z
diff --git a/empty.txt b/empty.txt
new file mode 100644
index 0000000..e69de29
diff --git a/blob.bin b/blob.bin
index 4444444..5555555 100644
Binary files a/blob.bin and b/blob.bin differ
diff --git a/last-empty.txt b/last-empty.txt
new file mode 100644
index 0000000..e69de29
"""

# About 100 KiB holding one line of about 80 KiB followed by a short line.
OVERSIZED_LINE = "L" * 80_000
OVERSIZED_LINE_TEXT = "\n".join(
    [
        "diff --git a/big.txt b/big.txt",
        "index 1111111..2222222 100644",
        "--- a/big.txt",
        "+++ b/big.txt",
        "@@ -0,0 +1,502 @@",
        *["+" + "s" * 40] * 500,
        "+" + OVERSIZED_LINE,
        "+short",
    ]
).encode()


def _body_lines_that_read_as_structure(digit: str) -> list[tuple[str, tuple[str, ...]]]:
    """Body lines that match a rule's regex once their prefix is ignored.

    The index rows carry a one-character prefix. The header rows carry a one-character prefix and a
    three-character prefix.
    """
    # The prefix length is load-bearing.
    # Without the header regex's `^` anchor, the match starts after the prefix.
    # The end check then reads position `RLENGTH + 1` of the whole line.
    # A line is rewritten only when that position holds one of the header's three spaces.
    # For this header shape that happens at prefix lengths 3, 6 and 9.
    # The three-character prefix is the shortest such length, so a coincidence of length cannot hide the mutant.
    return [
        ("plus-prefixed-header", (f"+@@ -{digit} +1 @@",)),
        ("minus-prefixed-header", (f"-@@ -{digit} +1 @@",)),
        ("space-prefixed-header", (f" @@ -{digit} +1 @@",)),
        ("three-character-prefixed-header", (f"+ab@@ -{digit} +1 @@",)),
        ("plus-prefixed-index", (f"+index {digit}111111..2222222 100644", "--- foo")),
        ("minus-prefixed-index", (f"-index {digit}111111..2222222 100644", "--- foo")),
    ]


def _unrecognized_form_pairs(digit: str) -> list[tuple[str, tuple[bytes, bytes]]]:
    """Header and `index` forms the rules do not recognize, so their digits stay in the preimage."""
    combined_header = f"@@@ -{digit} -1 +1 @@@"
    glued_header = f"@@ -{digit} +1 @@x"
    extra_field_index = f"index {digit}111111..2222222 100644 extra"
    uppercase_index = f"index ABCDEF{digit}..2222222 100644"
    return [
        ("combined-diff-header", _entry_pair(header=combined_header, expected_header=combined_header)),
        ("glued-tail-header", _entry_pair(header=glued_header, expected_header=glued_header)),
        ("index-extra-field", _entry_pair(index_line=extra_field_index, expected_index_line=extra_field_index)),
        ("index-uppercase-hex", _entry_pair(index_line=uppercase_index, expected_index_line=uppercase_index)),
    ]


VALUE_DIFFERS_ROWS = {
    "reindented-added-line": (_entry_pair(), _entry_pair(body=(" ctx", "+  added", " ctx2"))),
    "tab-versus-spaces-on-added-line": (
        _entry_pair(body=(" ctx", "+\tadded", " ctx2")),
        _entry_pair(body=(" ctx", "+ added", " ctx2")),
    ),
    "deleted-line-that-reads-as-a-file-header": (
        _entry_pair(body=(" ctx", "--- x", " ctx2")),
        _entry_pair(body=(" ctx", "--- y", " ctx2")),
    ),
    "trailing-whitespace-on-added-line": (_entry_pair(), _entry_pair(body=(" ctx", "+added ", " ctx2"))),
    "crlf-ending-on-added-line": (_entry_pair(), _entry_pair(body=(" ctx", "+added\r", " ctx2"))),
    "same-hunk-in-another-path": (_entry_pair(path="a.txt"), _entry_pair(path="b.txt")),
    "added-line-with-and-without-final-newline": (
        _entry_pair(body=(" ctx", "+added"), header="@@ -3,1 +3,2 @@", expected_header="@@ -,1 +,2 @@"),
        _entry_pair(
            body=(" ctx", "+added", "\\ No newline at end of file"),
            header="@@ -3,1 +3,2 @@",
            expected_header="@@ -,1 +,2 @@",
        ),
    ),
    "second-file-varies-after-a-no-newline-marker": (
        _concatenated(
            _entry_pair(
                path="first.txt", body=("-old", "\\ No newline at end of file", "+new"),
                header="@@ -1 +1 @@", expected_header="@@ - + @@",
            ),
            _entry_pair(path="second.txt", body=(" ctx", "+added one", " ctx2")),
        ),
        _concatenated(
            _entry_pair(
                path="first.txt", body=("-old", "\\ No newline at end of file", "+new"),
                header="@@ -1 +1 @@", expected_header="@@ - + @@",
            ),
            _entry_pair(path="second.txt", body=(" ctx", "+added two", " ctx2")),
        ),
    ),
    "mode-change-with-content-change": (
        _entry_pair(index_line="index 1111111..2222222", expected_index_line="index .."),
        _entry_pair(
            index_line="index 1111111..2222222",
            expected_index_line="index ..",
            pre_index_lines=("old mode 100644", "new mode 100755"),
        ),
    ),
    "index-line-mode-100644-versus-100755": (
        _entry_pair(),
        _entry_pair(index_line="index 1111111..2222222 100755", expected_index_line="index .. 100755"),
    ),
    "symlink-versus-regular-file-with-the-same-body": (
        _entry_pair(
            index_line="index 1111111..2222222 120000", expected_index_line="index .. 120000",
            body=("-old", "\\ No newline at end of file", "+new", "\\ No newline at end of file"),
            header="@@ -1 +1 @@", expected_header="@@ - + @@",
        ),
        _entry_pair(
            body=("-old", "\\ No newline at end of file", "+new", "\\ No newline at end of file"),
            header="@@ -1 +1 @@", expected_header="@@ - + @@",
        ),
    ),
    "pure-rename-to-another-path": (_rename_pair("old.txt", "new.txt"), _rename_pair("old.txt", "other.txt")),
    **{
        f"{label}-with-a-digit-changed": (_entry_pair(body=body_before), _entry_pair(body=body_after))
        for (label, body_before), (_, body_after) in zip(
            _body_lines_that_read_as_structure("1"), _body_lines_that_read_as_structure("2"), strict=True
        )
    },
    **{
        f"{label}-with-a-digit-changed": (pair_before, pair_after)
        for (label, pair_before), (_, pair_after) in zip(
            _unrecognized_form_pairs("1"), _unrecognized_form_pairs("2"), strict=True
        )
    },
}

# Each pair differs only in what the header rule drops, so the normalized text is the same.
SAME_SITE_BODY = (" s1", " s2", " s3", "+X", " s4", " s5", " s6")
RELOCATION_EQUAL_ROWS = {
    "different-function-context-text": (
        _entry_pair(body=SAME_SITE_BODY, header="@@ -1,6 +1,7 @@ def first():", expected_header="@@ -,6 +,7 @@"),
        _entry_pair(body=SAME_SITE_BODY, header="@@ -8,6 +8,7 @@ def second():", expected_header="@@ -,6 +,7 @@"),
    ),
    "same-function-context-text": (
        _entry_pair(body=SAME_SITE_BODY, header="@@ -1,6 +1,7 @@ def first():", expected_header="@@ -,6 +,7 @@"),
        _entry_pair(body=SAME_SITE_BODY, header="@@ -8,6 +8,7 @@ def first():", expected_header="@@ -,6 +,7 @@"),
    ),
    "start-offsets-only-with-no-function-context-text": (
        _entry_pair(body=SAME_SITE_BODY, header="@@ -1,6 +1,7 @@", expected_header="@@ -,6 +,7 @@"),
        _entry_pair(body=SAME_SITE_BODY, header="@@ -15,6 +15,7 @@", expected_header="@@ -,6 +,7 @@"),
    ),
    "zero-context-added-line-at-any-position": (
        _entry_pair(body=("+X",), header="@@ -3,0 +4 @@", expected_header="@@ -,0 + @@"),
        _entry_pair(body=("+X",), header="@@ -9,0 +10 @@", expected_header="@@ -,0 + @@"),
    ),
    "moved-deletion": (
        _entry_pair(
            body=(" s1", " s2", " s3", "-X", " s4", " s5", " s6"), header="@@ -1,7 +1,6 @@",
            expected_header="@@ -,7 +,6 @@",
        ),
        _entry_pair(
            body=(" s1", " s2", " s3", "-X", " s4", " s5", " s6"), header="@@ -8,7 +8,6 @@",
            expected_header="@@ -,7 +,6 @@",
        ),
    ),
    "move-across-functions": (
        _entry_pair(body=SAME_SITE_BODY, header="@@ -1,6 +1,7 @@ def parse():", expected_header="@@ -,6 +,7 @@"),
        _entry_pair(body=SAME_SITE_BODY, header="@@ -40,6 +40,7 @@ class Writer:", expected_header="@@ -,6 +,7 @@"),
    ),
}


def _awk_implementations_on_this_machine() -> dict[str, str]:
    """Real path of each awk the matrix knows how to find, keyed by the real file's name."""
    implementations: dict[str, str] = {}
    for candidate in ("/usr/bin/awk", "mawk", "gawk", "original-awk", "nawk"):
        resolved = shutil.which(candidate)
        if resolved is None:
            continue
        real_path = os.path.realpath(resolved)
        implementations.setdefault(os.path.basename(real_path), real_path)
    return implementations


AWK_IMPLEMENTATIONS = _awk_implementations_on_this_machine()


@pytest.fixture
def empty_file_repo(tmp_path: Path) -> Path:
    """Empty files created in HEAD~1 and deleted in HEAD, each before and after a modified text file in path order.

    Creation and deletion are separate commits, because git pairs an empty file deleted with one created as a rename.
    """
    repo = init_git_repo(tmp_path / "empty-files")
    for name in ("a-deleted.empty", "z-deleted.empty"):
        (repo / name).write_text("")
    (repo / "m-middle.txt").write_text("one\ntwo\n")
    _commit_all(repo, "before")
    for name in ("a-created.empty", "z-created.empty"):
        (repo / name).write_text("")
    (repo / "m-middle.txt").write_text("one\nTWO\n")
    _commit_all(repo, "create empty files")
    for name in ("a-deleted.empty", "z-deleted.empty"):
        (repo / name).unlink()
    (repo / "m-middle.txt").write_text("one\ntwo\n")
    _commit_all(repo, "delete empty files")
    return repo


class TestLibCumulativeReviewValue:
    """Direct coverage for `_lib_normalize_cumulative_review_text` and `_lib_cumulative_review_value`.

    Real-git diffs come from `isolated_git_diff`. Hand-written texts carry explicit expected normalized text,
    so no expected value comes from the function under test.
    """

    def test_reference_implementation_reproduces_the_hand_derived_oracle(self):
        assert reference_normalized_text(ORACLE_RAW_TEXT) == ORACLE_EXPECTED_NORMALIZED.rstrip(b"\n")

    def test_normalizer_output_equals_the_hand_derived_oracle_text(self, tmp_path):
        """Both header counts survive, start offsets and function-context text do not,
        the text entries' `index` lines lose their blob ids and keep their mode,
        and the binary and empty-file `index` lines stay whole."""
        normalized = require_normalized_cumulative_review_text(ORACLE_RAW_TEXT.rstrip(b"\n"), tmp_path)
        assert normalized == ORACLE_EXPECTED_NORMALIZED.rstrip(b"\n")

    def test_value_equals_sha256_of_the_hand_derived_oracle_text(self, tmp_path):
        value = require_cumulative_review_value(ORACLE_RAW_TEXT.rstrip(b"\n"), tmp_path)
        assert value == _sha256_of(ORACLE_EXPECTED_NORMALIZED.rstrip(b"\n"))

    def test_empty_text_exits_one_with_empty_stdout(self, tmp_path):
        """Empty text is never hashed: exit 1 with empty stdout."""
        assert cumulative_review_value(b"", tmp_path) == (1, "")

    def test_rebase_shifting_hunk_offsets_and_blob_ids_keeps_the_value(self, tmp_path):
        """A base edit far above the appended line shifts the hunk's start offsets and the blob ids only."""
        base_lines = [f"line {n}\n" for n in range(1, 21)]
        repo, base_sha = _repo_with_base_file(tmp_path, "f.txt", "".join(base_lines).encode())
        appended = "".join(base_lines + ["appended\n"]).encode()
        direct_diff = _commit_variant(repo, base_sha, "direct", {"f.txt": appended})
        moved_base = "".join([f"top {n}\n" for n in range(4)] + base_lines).encode()
        shifted_diff = _commit_variant(
            repo, base_sha, "shifted", {"f.txt": moved_base}, {"f.txt": moved_base + b"appended\n"}
        )

        assert direct_diff != shifted_diff
        assert _sha256_of(direct_diff) != _sha256_of(shifted_diff)
        hunk_header = re.compile(rb"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@(?: .*)?$", re.MULTILINE)
        direct_header = hunk_header.search(direct_diff)
        shifted_header = hunk_header.search(shifted_diff)
        assert direct_header and shifted_header
        assert (direct_header.group(1), direct_header.group(3)) != (shifted_header.group(1), shifted_header.group(3))
        assert (direct_header.group(2), direct_header.group(4)) == (shifted_header.group(2), shifted_header.group(4))
        index_lines = re.compile(rb"^index .*$", re.MULTILINE)
        assert index_lines.findall(direct_diff) != index_lines.findall(shifted_diff)
        assert require_cumulative_review_value(direct_diff, tmp_path) == require_cumulative_review_value(shifted_diff, tmp_path)

    @pytest.mark.parametrize("pair_name", list(VALUE_DIFFERS_ROWS))
    def test_texts_that_differ_in_a_passed_through_byte_give_different_values(self, tmp_path, pair_name):
        """A byte the rules never rewrite is hashed: indentation, whitespace, CRLF, path, final newline, mode, rename."""
        (raw_before, expected_before), (raw_after, expected_after) = VALUE_DIFFERS_ROWS[pair_name]
        assert require_normalized_cumulative_review_text(raw_before, tmp_path) == expected_before
        assert require_normalized_cumulative_review_text(raw_after, tmp_path) == expected_after
        value_before = require_cumulative_review_value(raw_before, tmp_path)
        value_after = require_cumulative_review_value(raw_after, tmp_path)
        assert value_before == _sha256_of(expected_before)
        assert value_after == _sha256_of(expected_after)
        assert value_before != value_after

    def test_unrecognized_header_and_index_forms_stay_in_the_normalized_output_verbatim(self, tmp_path):
        """A form the rules do not recognize can only cause a miss."""
        for label, (raw, _expected) in _unrecognized_form_pairs("1"):
            offending_line = {
                "combined-diff-header": b"@@@ -1 -1 +1 @@@",
                "glued-tail-header": b"@@ -1 +1 @@x",
                "index-extra-field": b"index 1111111..2222222 100644 extra",
                "index-uppercase-hex": b"index ABCDEF1..2222222 100644",
            }[label]
            assert offending_line in require_normalized_cumulative_review_text(raw, tmp_path).split(b"\n"), label

    def test_binary_edits_with_different_contents_give_different_values(self, tmp_path):
        """A binary entry's `index` line is its only content identity."""
        repo, _first_commit = _repo_with_base_file(tmp_path, "x.bin", b"\x00base")
        (repo / "y.bin").write_bytes(b"\x00base")
        _commit_all(repo, "second path, same blob")
        base_sha = head_sha(repo)
        edit_one = _commit_variant(repo, base_sha, "one", {"x.bin": b"\x00one"})
        edit_two = _commit_variant(repo, base_sha, "two", {"x.bin": b"\x00two"})
        same_blob_other_path = _commit_variant(repo, base_sha, "other-path", {"y.bin": b"\x00one"})

        assert b"Binary files" in edit_one
        values = {require_cumulative_review_value(diff, tmp_path) for diff in (edit_one, edit_two, same_blob_other_path)}
        assert len(values) == 3

    def test_long_line_text_gives_a_stable_value_that_changes_with_its_last_byte(self, tmp_path):
        """About 100 KiB through the uncapped normalizer, with an 80 KiB line."""
        assert 100_000 < len(OVERSIZED_LINE_TEXT) < 110_000
        first_value = require_cumulative_review_value(OVERSIZED_LINE_TEXT, tmp_path)
        assert first_value == require_cumulative_review_value(OVERSIZED_LINE_TEXT, tmp_path)
        assert first_value == _sha256_of(reference_normalized_text(OVERSIZED_LINE_TEXT))
        long_line_end = OVERSIZED_LINE_TEXT.index(b"\n+short")
        changed_last_byte = OVERSIZED_LINE_TEXT[: long_line_end - 1] + b"M" + OVERSIZED_LINE_TEXT[long_line_end:]
        assert len(changed_last_byte) == len(OVERSIZED_LINE_TEXT)
        assert require_cumulative_review_value(changed_last_byte, tmp_path) != first_value

    @pytest.mark.parametrize(
        "config", [(), SUPPRESS_BLANK_EMPTY_CONFIG], ids=["default", "suppress-blank-empty"]
    )
    def test_change_confined_to_the_last_hunk_changes_the_value(self, tmp_path, config):
        """The first hunk holds a blank context line, which a truncating filter would stop at."""
        base_lines = ["a", "", "b", "c", "d", *[f"mid {n}" for n in range(30)], "e", "f", "g", "h", "i"]
        edited_first_hunk = ["a", "", "B", "c", "d", *base_lines[5:]]

        def with_last_hunk(replacement: str) -> bytes:
            lines = [*edited_first_hunk[:-2], replacement, edited_first_hunk[-1]]
            return ("\n".join(lines) + "\n").encode()

        repo, base_sha = _repo_with_base_file(tmp_path, "f.txt", ("\n".join(base_lines) + "\n").encode())
        diffs = []
        for branch, replacement in (("last-one", "H1"), ("last-two", "H2")):
            subprocess.run(["git", "checkout", "-q", "-b", branch, base_sha], cwd=repo, check=True)
            (repo / "f.txt").write_bytes(with_last_hunk(replacement))
            _commit_all(repo, branch)
            diffs.append(isolated_git_diff(repo, "HEAD~1", "HEAD", config=config))

        assert len(re.findall(rb"^@@ ", diffs[0], re.MULTILINE)) == 2, "the diff must hold two hunks"
        assert require_cumulative_review_value(diffs[0], tmp_path) != require_cumulative_review_value(diffs[1], tmp_path)

    def test_edit_relocated_to_a_site_with_identical_context_keeps_the_value(self, tmp_path):
        """The PR's added line moved between two stanzas that share their +-3 line context.

        This is a named residual: the marker cannot tell the two sites apart.
        """
        stanza = ["s1", "s2", "s3", "s4", "s5", "s6"]
        base_file = "\n".join([*stanza, "separator", *stanza]) + "\n"
        repo, base_sha = _repo_with_base_file(tmp_path, "f.txt", base_file.encode())
        first_site = "\n".join(["s1", "s2", "s3", "X", "s4", "s5", "s6", "separator", *stanza]) + "\n"
        second_site = "\n".join([*stanza, "separator", "s1", "s2", "s3", "X", "s4", "s5", "s6"]) + "\n"
        first_diff = _commit_variant(repo, base_sha, "first-site", {"f.txt": first_site.encode()})
        second_diff = _commit_variant(repo, base_sha, "second-site", {"f.txt": second_site.encode()})

        assert _sha256_of(first_diff) != _sha256_of(second_diff)
        assert require_cumulative_review_value(first_diff, tmp_path) == require_cumulative_review_value(second_diff, tmp_path)

    @pytest.mark.parametrize("pair_name", list(RELOCATION_EQUAL_ROWS))
    def test_edit_relocated_by_a_hand_written_variant_keeps_the_value(self, tmp_path, pair_name):
        """Pairs that differ only in what the header rule drops, with equal expected text."""
        (raw_before, expected_before), (raw_after, expected_after) = RELOCATION_EQUAL_ROWS[pair_name]
        assert expected_before == expected_after
        assert raw_before != raw_after
        value_before = require_cumulative_review_value(raw_before, tmp_path)
        value_after = require_cumulative_review_value(raw_after, tmp_path)
        assert value_before == value_after == _sha256_of(expected_before)

    @pytest.mark.parametrize(
        "index_line",
        ["index abcd..0123456789ab 100644", "index 0123456789ab..abcd 100644"],
        ids=["short-old-id-long-new-id", "long-old-id-short-new-id"],
    )
    def test_blob_ids_of_any_length_are_stripped_from_a_text_entry_index_line(self, tmp_path, index_line):
        """Git abbreviates blob ids to a length that varies with repository size and `core.abbrev`."""
        raw_other_length, expected_other_length = _entry_pair(index_line=index_line)
        raw_seven_digit, _ = _entry_pair()
        assert require_normalized_cumulative_review_text(raw_other_length, tmp_path) == expected_other_length
        assert require_cumulative_review_value(raw_other_length, tmp_path) == require_cumulative_review_value(
            raw_seven_digit, tmp_path
        )

    def test_edit_moved_past_another_hunk_of_the_same_file_changes_the_value(self, tmp_path):
        """Hunk order stays in the preimage, so a move past another PR hunk is not hidden."""
        first_hunk = ("@@ -1,6 +1,7 @@", "@@ -,6 +,7 @@", SAME_SITE_BODY)
        second_hunk = ("@@ -20,6 +21,7 @@", "@@ -,6 +,7 @@", (" t1", " t2", " t3", "+Y", " t4", " t5", " t6"))

        def two_hunk_text(*hunks) -> tuple[bytes, bytes]:
            raw_entry, expected_entry = _entry_pair(header=hunks[0][0], expected_header=hunks[0][1], body=hunks[0][2])
            extra_raw = b"".join(
                f"\n{header}\n".encode() + "\n".join(body).encode() for header, _, body in hunks[1:]
            )
            extra_expected = b"".join(
                f"\n{header}\n".encode() + "\n".join(body).encode() for _, header, body in hunks[1:]
            )
            return raw_entry + extra_raw, expected_entry + extra_expected

        raw_in_order, expected_in_order = two_hunk_text(first_hunk, second_hunk)
        raw_swapped, expected_swapped = two_hunk_text(second_hunk, first_hunk)
        assert expected_in_order != expected_swapped
        assert require_cumulative_review_value(raw_in_order, tmp_path) != require_cumulative_review_value(raw_swapped, tmp_path)

    def test_latin1_byte_change_on_a_body_line_changes_the_value_but_not_on_header_text(self, tmp_path):
        """Body bytes are hashed as bytes. Header text is dropped, so the same byte there is the intended residual."""
        body_before, _ = _entry_pair(body=(" ctx", "+caf\xe9", " ctx2"))
        body_after, _ = _entry_pair(body=(" ctx", "+caf\xe8", " ctx2"))
        header_before, _ = _entry_pair(header="@@ -3,2 +3,3 @@ caf\xe9")
        header_after, _ = _entry_pair(header="@@ -3,2 +3,3 @@ caf\xe8")

        assert require_cumulative_review_value(body_before, tmp_path) != require_cumulative_review_value(body_after, tmp_path)
        assert require_cumulative_review_value(header_before, tmp_path) == require_cumulative_review_value(header_after, tmp_path)

    def test_non_utf8_text_gives_the_same_value_under_c_and_under_a_utf8_locale(self, tmp_path):
        """The normalizer pins `LC_ALL=C` for awk, so an ambient UTF-8 locale cannot change its result."""
        utf8_locale_names = _installed_utf8_locale_names()
        if not utf8_locale_names:
            pytest.skip("no installed UTF-8 locale")
        raw, _expected = _entry_pair(body=(" ctx", "+caf\xe9", " ctx2"))
        under_c = require_cumulative_review_value(raw, tmp_path, env_overrides={"LC_ALL": "C"})
        under_utf8 = require_cumulative_review_value(raw, tmp_path, env_overrides={"LC_ALL": utf8_locale_names[0]})
        assert under_c == under_utf8 == _sha256_of(reference_normalized_text(raw))

    def test_awk_receives_lc_all_c_whatever_the_ambient_locale(self, tmp_path):
        """A shim that refuses the normalizer's awk call unless it runs under `LC_ALL=C`.

        Removing the pin makes the shim return 3, so the value helper exits 1 with empty stdout.
        """
        shim = (
            "awk() { case \"$*\" in *'@@'*) [ \"${LC_ALL-}\" = C ] || return 3 ;; esac; command awk \"$@\"; }"
        )
        raw, expected = _entry_pair()
        exit_status, value = cumulative_review_value(
            raw, tmp_path, env_overrides={"LC_ALL": "POSIX"}, shell_prelude=shim
        )
        assert (exit_status, value) == (0, _sha256_of(expected))

    def test_colored_diff_text_is_hashed_without_tolerance(self, tmp_path):
        """Colored header and body lines match no rule, so color changes the value and never errors."""
        plain_raw, _ = _entry_pair()
        shifted_raw, _ = _entry_pair(header="@@ -9,2 +9,3 @@ def func():")
        colored_raw = plain_raw.replace(b"@@ -3,2 +3,3 @@ def func():", b"\x1b[36m@@ -3,2 +3,3 @@\x1b[m def func():")
        colored_raw = colored_raw.replace(b"+added", b"\x1b[32m+added\x1b[m")
        colored_shifted_raw = shifted_raw.replace(
            b"@@ -9,2 +9,3 @@ def func():", b"\x1b[36m@@ -9,2 +9,3 @@\x1b[m def func():"
        ).replace(b"+added", b"\x1b[32m+added\x1b[m")
        assert colored_raw != plain_raw and colored_shifted_raw != shifted_raw

        colored_value = require_cumulative_review_value(colored_raw, tmp_path)
        colored_shifted_value = require_cumulative_review_value(colored_shifted_raw, tmp_path)
        assert colored_value != colored_shifted_value
        assert colored_value != require_cumulative_review_value(plain_raw, tmp_path)
        assert colored_shifted_value != require_cumulative_review_value(shifted_raw, tmp_path)

    @pytest.mark.parametrize(
        "failing_awk_shim",
        [
            "awk() { case \"$*\" in *'@@'*) return 3 ;; esac; command awk \"$@\"; }",
            "awk() { case \"$*\" in *'@@'*) cat >/dev/null; return 0 ;; esac; command awk \"$@\"; }",
            "awk() { case \"$*\" in *'@@'*) command awk \"$@\" | head -n 3; return 3 ;; esac; command awk \"$@\"; }",
        ],
        ids=["exit-3-no-output", "exit-0-no-output", "exit-3-after-partial-output"],
    )
    def test_normalizer_failure_exits_one_with_empty_stdout(self, tmp_path, failing_awk_shim):
        """A shim fails only the normalizer's awk call (the only one whose script mentions `@@`)."""
        raw, _expected = _entry_pair()
        assert cumulative_review_value(raw, tmp_path, shell_prelude=failing_awk_shim) == (1, "")

    @pytest.mark.parametrize("awk_name", list(AWK_IMPLEMENTATIONS) or [None])
    def test_awk_implementation_reproduces_the_oracle_and_the_edge_cases(self, tmp_path, awk_name):
        """Every awk found reproduces the oracle text, the long-line value and the structure-lookalike rows."""
        if awk_name is None:
            pytest.skip("no known awk implementation found on PATH")
        shim_dir = tmp_path / "awk-shim-dir"
        shim_dir.mkdir()
        (shim_dir / "awk").symlink_to(AWK_IMPLEMENTATIONS[awk_name])
        env_overrides = {"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}

        assert require_normalized_cumulative_review_text(ORACLE_RAW_TEXT.rstrip(b"\n"), tmp_path, env_overrides) == (
            ORACLE_EXPECTED_NORMALIZED.rstrip(b"\n")
        )
        assert require_cumulative_review_value(OVERSIZED_LINE_TEXT, tmp_path, env_overrides=env_overrides) == _sha256_of(
            reference_normalized_text(OVERSIZED_LINE_TEXT)
        )
        structure_lookalike_pairs = [
            pair
            for pair_name, pairs in VALUE_DIFFERS_ROWS.items()
            if pair_name.endswith("-with-a-digit-changed")
            for pair in pairs
        ]
        for raw, expected in structure_lookalike_pairs:
            assert require_normalized_cumulative_review_text(raw, tmp_path, env_overrides) == expected

    def test_usr_bin_awk_is_part_of_the_matrix_on_ci(self):
        """The matrix must not skip every case on CI."""
        if not os.environ.get("GITHUB_ACTIONS"):
            pytest.skip("only enforced on GitHub Actions")
        assert os.path.realpath("/usr/bin/awk") in AWK_IMPLEMENTATIONS.values()

    @pytest.mark.parametrize(
        "config",
        [(), SUPPRESS_BLANK_EMPTY_CONFIG, TEXTCONV_CONFIG],
        ids=["default", "suppress-blank-empty", "textconv"],
    )
    def test_normalizer_matches_the_reference_on_every_entry_shape(self, tmp_path, entry_shapes_repo, config):
        """The two rules applied to real git output of every shape the pins cover."""
        diff = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD", config=config)
        normalized = require_normalized_cumulative_review_text(diff, tmp_path)

        assert normalized == reference_normalized_text(diff)
        assert not re.search(rb"^@@ -[0-9]", normalized, re.MULTILINE)
        kept_index_lines = [line for line in normalized.split(b"\n") if INDEX_LINE_WITH_BLOB_IDS.match(line)]
        index_lines_before_non_minus_header = [
            following for following in _lines_after_each_index_line(diff)
            if following is None or not following.startswith(b"--- ")
        ]
        assert len(kept_index_lines) == len(index_lines_before_non_minus_header)
        assert len(kept_index_lines) < len(_lines_after_each_index_line(diff))

    def test_normalizer_keeps_an_interior_empty_context_line_under_suppress_blank_empty(self, tmp_path):
        """`entry_shapes_repo` holds no blank context line, so its suppress-blank-empty variant equals the default one."""
        repo = init_git_repo(tmp_path / "blank-context")
        (repo / "blank.txt").write_text("a\n\nb\nc\n")
        _commit_all(repo, "before")
        (repo / "blank.txt").write_text("a\n\nb\nC\n")
        _commit_all(repo, "after")
        diff = isolated_git_diff(repo, "HEAD~1", "HEAD", config=SUPPRESS_BLANK_EMPTY_CONFIG)
        assert b"\n\n" in diff

        normalized = require_normalized_cumulative_review_text(diff, tmp_path)

        assert normalized == reference_normalized_text(diff)
        assert b"\n\n" in normalized

    @pytest.mark.parametrize("revisions", [("HEAD~2", "HEAD~1"), ("HEAD~1", "HEAD")], ids=["creation", "deletion"])
    def test_normalizer_keeps_empty_file_index_lines_mid_diff_and_last(self, tmp_path, empty_file_repo, revisions):
        """The `index` line of an entry with no `---` line stays, even at end of input."""
        diff = isolated_git_diff(empty_file_repo, *revisions)
        normalized = require_normalized_cumulative_review_text(diff, tmp_path)

        assert normalized == reference_normalized_text(diff)
        assert len([line for line in normalized.split(b"\n") if INDEX_LINE_WITH_BLOB_IDS.match(line)]) == 2
        assert INDEX_LINE_WITH_BLOB_IDS.match(normalized.split(b"\n")[-1])

    def test_crlf_content_lines_keep_their_carriage_return(self, tmp_path):
        """No rule collapses whitespace, so a CRLF diff keeps its CR on every content line."""
        raw, _expected = _entry_pair(body=(" ctx\r", "+added\r", " ctx2\r"))
        assert b"\r" in raw
        normalized_lines = require_normalized_cumulative_review_text(raw, tmp_path).split(b"\n")
        content_lines = [line for line in normalized_lines if line[:1] in (b" ", b"+") and not line.startswith(b"+++")]
        assert len(content_lines) == 3
        assert all(line.endswith(b"\r") for line in content_lines)

    def test_entries_no_rule_touches_normalize_to_themselves(self, tmp_path, entry_shapes_repo):
        """Mode-only, 100% rename and binary entries pass through byte for byte."""
        diff = isolated_git_diff(entry_shapes_repo, "HEAD~1", "HEAD")
        untouched = b"\n".join(
            b"\n".join(_entry_for_path(diff, path))
            for path in ("binary-edit.dat", "mode-only.sh", "renamed-plain-moved.txt")
        )
        assert require_normalized_cumulative_review_text(untouched, tmp_path) == untouched

    def test_trailing_blank_context_lines_change_the_value_through_the_header_counts(self, tmp_path):
        """Under `diff.suppressBlankEmpty` the trailing blank context lines print as empty lines,
        and `$( )` strips them, so only the header counts record how many there were."""
        entry_head = (
            b"diff --git a/f.txt b/f.txt\nindex 1111111..2222222 100644\n--- a/f.txt\n+++ b/f.txt\n"
        )
        with_blank_context = entry_head + b"@@ -1,3 +1,4 @@\n a\n+b\n\n\n"
        without_blank_context = entry_head + b"@@ -1 +1,2 @@\n a\n+b\n"
        without_header_lines = [
            re.sub(rb"^@@ .*\n", b"", text, flags=re.MULTILINE).rstrip(b"\n")
            for text in (with_blank_context, without_blank_context)
        ]
        assert without_header_lines[0] == without_header_lines[1]
        value_with_blank_context = require_cumulative_review_value(with_blank_context, tmp_path)
        value_without_blank_context = require_cumulative_review_value(without_blank_context, tmp_path)
        assert value_with_blank_context != value_without_blank_context

    @pytest.mark.parametrize("quote_path", ["true", "false"])
    def test_multibyte_content_and_path_pass_through_and_changes_inside_them_change_the_value(
        self, tmp_path, quote_path
    ):
        """`core.quotePath=false` makes git print the path as raw UTF-8, not as octal-quoted ASCII."""
        config = (f"core.quotePath={quote_path}",)
        repo, base_sha = _repo_with_base_file(tmp_path, "p\u00e4th.txt", "h\u00e9llo\n".encode())
        (repo / "p\u00f6th.txt").write_text("h\u00e9llo\n")
        _commit_all(repo, "second multibyte path")
        base_sha = head_sha(repo)

        def diff_of(branch: str, name: str, content: str) -> bytes:
            subprocess.run(["git", "checkout", "-q", "-b", branch, base_sha], cwd=repo, check=True)
            (repo / name).write_text(content)
            _commit_all(repo, branch)
            return isolated_git_diff(repo, "HEAD~1", "HEAD", config=config)

        content_one = diff_of("content-one", "p\u00e4th.txt", "h\u00e9llo w\u00f6rld\n")
        content_two = diff_of("content-two", "p\u00e4th.txt", "h\u00e9llo w\u00f6rld!\n")
        other_path = diff_of("other-path", "p\u00f6th.txt", "h\u00e9llo w\u00f6rld\n")

        if quote_path == "false":
            assert "w\u00f6rld".encode() in content_one and "p\u00e4th".encode() in content_one
            assert require_normalized_cumulative_review_text(content_one, tmp_path) == reference_normalized_text(content_one)
            assert "w\u00f6rld".encode() in require_normalized_cumulative_review_text(content_one, tmp_path)
        values = {require_cumulative_review_value(diff, tmp_path) for diff in (content_one, content_two, other_path)}
        assert len(values) == 3

    def test_gitlink_bump_gives_a_different_value_from_a_bump_to_another_commit(self, tmp_path):
        """A gitlink entry is identified by its `Subproject commit` line, which passes through."""
        repo = init_git_repo(tmp_path / "gitlinks")
        (repo / "seed.txt").write_text("seed\n")
        _commit_all(repo, "seed")
        set_gitlink(repo, "gitlink", GITLINK_BEFORE)
        subprocess.run(["git", "commit", "-q", "-m", "gitlink before"], cwd=repo, check=True)
        base_sha = head_sha(repo)

        def bump_diff(branch: str, commit_sha: str) -> bytes:
            subprocess.run(["git", "checkout", "-q", "-b", branch, base_sha], cwd=repo, check=True)
            set_gitlink(repo, "gitlink", commit_sha)
            subprocess.run(["git", "commit", "-q", "-m", branch], cwd=repo, check=True)
            return isolated_git_diff(repo, "HEAD~1", "HEAD")

        bump_one = bump_diff("bump-one", GITLINK_AFTER)
        bump_two = bump_diff("bump-two", "3" * 40)
        assert b"Subproject commit" in bump_one
        assert require_cumulative_review_value(bump_one, tmp_path) != require_cumulative_review_value(bump_two, tmp_path)


class TestLibRepoRoot:
    """Direct coverage for _lib_repo_root -- the raw resolution recipe shared
    by marker.sh's _resolve_repo_root and pr-diff-against-base.sh --record,
    so both sides resolve a given tree to the identical REPO_ROOT string."""

    def test_matches_git_rev_parse_show_toplevel(self, tmp_path):
        repo = tmp_path / "repo-root-repo"
        init_git_repo(repo)
        expected = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        actual = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_repo_root'],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert actual == expected

    def test_fails_closed_outside_a_git_repository(self, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_repo_root'],
            cwd=outside,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert result.stdout == ""

    @pytest.mark.timing
    def test_hung_git_is_bounded_by_lib_capped(self, tmp_path):
        """A locked .git/index or a stale NFS mount can make `git
        rev-parse` block indefinitely -- _lib_repo_root must route through
        _lib_capped so callers (marker.sh's _resolve_repo_root,
        pr-diff-against-base.sh --record) fail fast instead of hanging for
        however long the harness's own outer Bash-tool timeout allows."""
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")

        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        write_scaled_timeout_shim(fake_bin)
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/bin/sh\n"
            # Well past _lib_capped's 5s cap -- avoids a race against
            # the cap firing at the same instant a shorter sleep would end.
            f'if [ "$1" = "rev-parse" ]; then sleep {scaled_shim_sleep(30)}; fi\n'
        )
        fake_git.chmod(0o755)

        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
        with assert_cap_engaged(fake_bin, production_cap=5):
            result = subprocess.run(
                ["bash", "-c", f'. "{LIB_SH}"; _lib_repo_root'],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                env=env,
            )

        assert result.returncode != 0


class TestLibMainRepoRoot:
    """Direct coverage for _lib_main_repo_root, which must resolve to the same
    main-tree path regardless of which worktree of the repo the caller is
    standing in."""

    def test_matches_lib_repo_root_from_the_main_tree(self, tmp_path):
        repo = tmp_path / "main-repo-root-repo"
        init_git_repo(repo)
        expected = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_repo_root'],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        actual = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_main_repo_root'],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout
        assert actual == expected

    def test_from_a_linked_worktree_returns_the_main_tree_root_not_the_worktree_path(self, tmp_path):
        """_lib_repo_root (--show-toplevel) returns the current worktree's
        own path when invoked from a linked worktree; _lib_main_repo_root
        must always return the main tree's path instead, so every consumer
        agrees on one root from any tree."""
        repo = tmp_path / "main-repo"
        init_git_repo(repo)
        (repo / "file.txt").write_text("first\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "feature"], cwd=repo, check=True)

        linked_worktree = tmp_path / "linked-worktree"
        subprocess.run(
            ["git", "worktree", "add", str(linked_worktree), "feature"], cwd=repo, check=True,
        )

        expected_main_root = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_main_repo_root'],
            cwd=linked_worktree, capture_output=True, text=True, check=True,
        )
        assert result.stdout == expected_main_root
        assert result.stdout != str(linked_worktree)

    def test_fails_closed_outside_a_git_repository(self, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_main_repo_root'],
            cwd=outside, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert result.stdout == ""

    @staticmethod
    def _path_with_git_that_echoes_the_unknown_path_format_flag(shim_dir: Path) -> str:
        """Prepend a `git` shim that behaves like git before 2.31: it prints
        --path-format=<mode> as an output line, then answers the rest of the
        command as usual."""
        shim_dir.mkdir()
        shim = shim_dir / "git"
        shim.write_text(
            "#!/bin/bash\n"
            'forwarded=()\n'
            'for arg in "$@"; do\n'
            '  case "$arg" in\n'
            '    --path-format=*) printf "%s\\n" "$arg" ;;\n'
            '    *) forwarded+=("$arg") ;;\n'
            '  esac\n'
            'done\n'
            f'exec "{shutil.which("git")}" "${{forwarded[@]}}"\n'
        )
        shim.chmod(0o755)
        return f"{shim_dir}{os.pathsep}{os.environ['PATH']}"

    @pytest.mark.parametrize("from_linked_worktree", [False, True])
    def test_fails_closed_when_git_echoes_the_unsupported_path_format_flag(
        self, tmp_path, from_linked_worktree
    ):
        repo = tmp_path / "old-git-repo"
        init_git_repo(repo)
        (repo / "file.txt").write_text("first\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        caller_cwd = repo
        if from_linked_worktree:
            subprocess.run(["git", "branch", "feature"], cwd=repo, check=True)
            caller_cwd = tmp_path / "old-git-linked-worktree"
            subprocess.run(
                ["git", "worktree", "add", str(caller_cwd), "feature"], cwd=repo, check=True,
            )
        env = {
            **os.environ,
            "PATH": self._path_with_git_that_echoes_the_unknown_path_format_flag(
                tmp_path / "old-git-shim"
            ),
        }

        result = subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_main_repo_root'],
            cwd=caller_cwd, capture_output=True, text=True, env=env,
        )

        assert result.returncode == 1
        assert result.stdout == ""


class TestLibReviewPrMarkerRepoHash:
    """Direct coverage for _lib_review_pr_marker_repo_hash -- the one key every
    review-pr completion-marker path derives from, which must not depend on
    which tree of the repo the caller stands in. `marker.sh status` is
    reachable only where _resolve_repo_root allows it: it exits 2 from the
    main tree of a worktree-enforced repo that has a live linked worktree."""

    @staticmethod
    def _repo_hash_from(cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'. "{LIB_SH}"; _lib_review_pr_marker_repo_hash'],
            cwd=cwd, capture_output=True, text=True,
        )

    def test_main_tree_and_linked_worktree_resolve_the_same_key_as_the_main_root_hash(self, tmp_path):
        repo = tmp_path / "marker-key-repo"
        init_git_repo(repo)
        (repo / "file.txt").write_text("first\n")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        linked_worktree = tmp_path / "marker-key-linked"
        subprocess.run(["git", "worktree", "add", "--detach", str(linked_worktree)], cwd=repo, check=True)
        subdirectory = repo / "sub"
        subdirectory.mkdir()

        main_root = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        expected = hashlib.sha256(main_root.encode()).hexdigest()

        for cwd in (repo, linked_worktree, subdirectory):
            result = self._repo_hash_from(cwd)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == expected, f"key differs when run from {cwd}"

    def test_fails_closed_outside_a_git_repository(self, tmp_path):
        outside = tmp_path / "marker-key-not-a-repo"
        outside.mkdir()
        result = self._repo_hash_from(outside)
        assert result.returncode != 0
        assert result.stdout == ""


class TestLibActivePlanFiles:
    def test_git_enumeration_failure_fails_closed(self, tmp_path):
        """A failed `git ls-files` call must exit 1 with .claude/plans/
        itself named on stdout, not silently report an empty (clean) active
        set -- this function now backs both _lib_active_plan_hash and
        require-plan-review.sh's fast-path guard, so an undetected fail-open
        regression here would disarm both call sites at once. Mirrors
        test_failed_worktree_enumeration_fails_closed in
        test_require_plan_review.py, which pins the same fail-closed
        direction for a sibling git call."""
        repo = tmp_path / "enum-failure-repo"
        init_git_repo(repo)
        # A HEAD commit routes _lib_active_plan_files through its `git diff`
        # branch rather than its no-HEAD `git ls-files` fallback, so the stub
        # below exercises the `ls-files --others` (untracked-plans) call in
        # isolation rather than also tripping the fallback's own guard.
        (repo / "README.md").write_text("seed\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "active-plan.md").write_text("# active\n")

        real_git = shutil.which("git")
        assert real_git, "test host must have a real git binary on PATH"
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "git"
        stub.write_text(
            "#!/bin/bash\n"
            'for arg in "$@"; do\n'
            '  if [ "$arg" = "ls-files" ]; then exit 1; fi\n'
            "done\n"
            f'exec {real_git} "$@"\n'
        )
        stub.chmod(0o755)

        result = _active_plan_files(
            repo, env_overrides={"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
        )
        assert result.returncode == 1, (
            f"expected exit 1 on a failed git enumeration, got {result.returncode}"
        )
        assert result.stdout.strip() == str(plans_dir), (
            f"stdout must name .claude/plans/ on enumeration failure, got {result.stdout!r}"
        )

    def test_sort_failure_fails_closed(self, tmp_path):
        """A failed `sort -u` call merging the untracked- and modified-plan
        lists must exit 1 with .claude/plans/ itself named on stdout, not
        silently report an empty (clean) active set -- same fail-closed
        convention as test_git_enumeration_failure_fails_closed above, pinned
        for the sort step rather than the git enumeration it follows."""
        repo = tmp_path / "sort-failure-repo"
        init_git_repo(repo)
        (repo / "README.md").write_text("seed\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "active-plan.md").write_text("# active\n")

        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "sort"
        stub.write_text("#!/bin/bash\nexit 1\n")
        stub.chmod(0o755)

        result = _active_plan_files(
            repo, env_overrides={"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
        )
        assert result.returncode == 1, (
            f"expected exit 1 on a failed sort, got {result.returncode}"
        )
        assert result.stdout.strip() == str(plans_dir), (
            f"stdout must name .claude/plans/ on sort failure, got {result.stdout!r}"
        )


class TestLibActivePlanHash:
    """Tests for _lib_active_plan_hash (GH #466). Relational assertions
    only -- never a golden sha256 literal -- since the exact digest recipe
    is free to evolve as long as the write side and read side agree."""

    def test_empty_when_no_plans_dir(self, tmp_path):
        repo = tmp_path / "no-plans"
        init_git_repo(repo)
        assert _active_plan_hash(repo) == ""

    def test_empty_when_plans_dir_empty(self, tmp_path):
        repo = tmp_path / "empty-plans"
        init_git_repo(repo)
        (repo / ".claude" / "plans").mkdir(parents=True)
        assert _active_plan_hash(repo) == ""

    def test_empty_when_all_plans_committed_clean(self, tmp_path):
        repo = tmp_path / "clean-plans"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        subprocess.run(["git", "add", ".claude/plans/p.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "plan"], cwd=repo, check=True)
        assert _active_plan_hash(repo) == ""

    def test_empty_active_set_returns_empty_for_non_empty_base(self, tmp_path):
        """An empty active set disarms the gate for any BASE, not only the
        empty one: two distinct non-empty bases must both yield empty stdout,
        so the result cannot depend on which base was supplied."""
        repo = tmp_path / "clean-plans-with-base"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        subprocess.run(["git", "add", ".claude/plans/p.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "plan"], cwd=repo, check=True)
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head_tree != head_commit

        assert _active_plan_hash(repo, base=head_tree) == ""
        assert _active_plan_hash(repo, base=head_commit) == ""

    def test_nonempty_when_plan_active(self, tmp_path):
        repo = tmp_path / "active-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        assert _active_plan_hash(repo) != ""

    def test_stable_under_reordered_active_set(self, tmp_path):
        """The same two active plans, recreated in reverse filesystem order
        within the same repo, must still produce the same hash -- LC_ALL=C
        sort normalizes enumeration order regardless of on-disk creation
        order. Holds the repo path (and so every hashed path) constant so
        creation order is the only variable -- comparing across two
        different tmp_path repos would also vary the absolute path prefix
        embedded in the hash, confounding the assertion."""
        repo = tmp_path / "order-repo"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "aaa.md").write_text("first\n")
        (plans_dir / "zzz.md").write_text("second\n")
        forward_order_hash = _active_plan_hash(repo)

        for existing in plans_dir.iterdir():
            existing.unlink()
        (plans_dir / "zzz.md").write_text("second\n")
        (plans_dir / "aaa.md").write_text("first\n")
        reverse_order_hash = _active_plan_hash(repo)

        assert forward_order_hash == reverse_order_hash

    def test_content_edit_changes_hash(self, tmp_path):
        repo = tmp_path / "edit-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan v1\n")
        before = _active_plan_hash(repo)
        plan.write_text("# plan v2\n")
        after = _active_plan_hash(repo)
        assert before != after

    def test_active_set_change_changes_hash(self, tmp_path):
        repo = tmp_path / "add-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "a.md").write_text("plan a\n")
        before = _active_plan_hash(repo)
        (plans_dir / "b.md").write_text("plan b\n")
        after = _active_plan_hash(repo)
        assert before != after

    def test_spaces_in_filename_round_trips(self, tmp_path):
        """A plan filename containing spaces must produce a non-empty hash,
        and re-running against the unchanged file must reproduce it
        identically -- guards against unquoted word-splitting in the file
        enumeration loop."""
        repo = tmp_path / "spacey-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "my plan draft.md").write_text("# spacey plan\n")
        first = _active_plan_hash(repo)
        second = _active_plan_hash(repo)
        assert first != ""
        assert first == second

    def test_deleted_tracked_plan_disarms_rather_than_failing(self, tmp_path):
        """A committed plan deleted from the worktree shows up as modified
        vs HEAD but has no bytes left to hash. Counting it as active would
        make the hash unconditionally unobtainable -- denying every write
        forever instead of disarming, and with no file left for the user to
        repair."""
        repo = tmp_path / "deleted-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "plan"], cwd=repo, check=True)
        assert _active_plan_hash(repo) == "", "committed clean plan should not arm the gate"

        plan.unlink()
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'. "{LIB_SH}"; _lib_active_plan_hash "$1"',
                "_active_plan_hash",
                str(repo),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"deleting a plan must not be a hash failure, got exit {result.returncode} "
            f"stdout={result.stdout!r}"
        )
        assert result.stdout.strip() == "", "a deleted plan leaves nothing active to gate"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_plan_exits_nonzero_and_names_the_file(self, tmp_path):
        """An active-but-unhashable plan must be distinguishable from "no
        active plan". Both used to return empty with status 0, so every
        caller read the failure as "gate disarmed" and allowed. The exit
        status carries the distinction; stdout carries the offending path so
        the caller's deny message can point the user at it."""
        repo = tmp_path / "unreadable-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan\n")
        plan.chmod(0o000)
        try:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'. "{LIB_SH}"; _lib_active_plan_hash "$1"',
                    "_active_plan_hash",
                    str(repo),
                ],
                capture_output=True,
                text=True,
            )
        finally:
            plan.chmod(0o644)

        assert result.returncode == 1, (
            f"expected exit 1 for an unhashable active plan, got {result.returncode}"
        )
        assert result.stdout.strip() == str(plan), (
            f"stdout must name the offending plan file, got {result.stdout!r}"
        )

    def test_hash_is_invariant_under_ambient_locale(self, tmp_path):
        """`LC_ALL=C sort` in the enumeration is load-bearing, not cosmetic:
        marker.sh runs in the user's Bash-tool locale and the hook runs in
        the harness hook environment, so a bare `sort` honoring $LC_COLLATE
        would order a >=2-plan set differently on each side and wedge the
        gate into a permanent false-deny. The filenames must be a pair the
        two collations genuinely disagree on -- C sorts uppercase before
        lowercase, most UTF-8 collations interleave them -- since an
        all-lowercase pair sorts identically everywhere and would make this
        test vacuous."""
        alt_locale = _find_case_insensitive_collation_locale()
        if alt_locale is None:
            pytest.skip("no non-C collation locale installed to contrast against")

        repo = tmp_path / "locale-repo"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "B.md").write_text("upper\n")
        (plans_dir / "a.md").write_text("lower\n")

        assert _active_plan_hash(repo, env_overrides={"LC_ALL": "C"}) == _active_plan_hash(
            repo, env_overrides={"LC_ALL": alt_locale}
        )

    def test_non_ascii_filename_round_trips(self, tmp_path):
        """A plan filename containing non-ASCII characters must produce a
        non-empty hash, and re-running against the unchanged file must
        reproduce it identically -- guards against a byte-width assumption
        in the file enumeration or hashing path."""
        repo = tmp_path / "unicode-plan"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "plan-été-日本.md").write_text("# unicode plan\n")
        first = _active_plan_hash(repo)
        second = _active_plan_hash(repo)
        assert first != ""
        assert first == second


class TestLibCodeReviewMarkerValueSentinelIsIntentionalDivergence:
    """Pins why _lib_code_review_marker_value keeps its empty-base sentinel
    while _lib_active_plan_hash does not. With a non-empty base,
    require-code-review.sh skips its empty-diff exit and consults the marker
    comparison, so the value is an authorization and must bind to the base's
    identity. The plan-review gate disarms on an empty active set and
    consults no marker at all, so a base-bound plan-review value would
    demand a review of nothing. Not an oversight to "fix" for symmetry."""

    def test_code_review_value_binds_to_base_where_plan_hash_disarms(self, tmp_path):
        repo = tmp_path / "sentinel-divergence"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        subprocess.run(["git", "add", ".claude/plans/p.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "plan"], cwd=repo, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        code_review_value = _code_review_marker_value(repo, base)
        plan_hash = _active_plan_hash(repo, base=base)

        independent_sentinel_oracle = hashlib.sha256(
            f"code-review-empty-base:{base}".encode()
        ).hexdigest()
        assert code_review_value == independent_sentinel_oracle
        assert plan_hash == ""


class TestLibAdvanceOffsetPastCompleteLines:
    """Unit tests for _lib_advance_offset_past_complete_lines -- shared by
    nudge-handoff-near-context-cap.sh and nudge-long-turn-subagent.sh for
    mid-write transcript safety when resuming an incremental scan from a
    stored byte offset. The two hooks' own tests cover that each wires this
    helper in; these tests cover the helper's own branch selection and
    boundary behavior."""

    def test_offset_already_equal_to_current_size_returns_offset_unchanged(self, tmp_path):
        """Zero new bytes since the last scan -- the boundary short-circuit
        ahead of both the fast and slow path."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, size, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(size)

    def test_fast_path_advances_to_current_size_when_file_ends_in_newline(self, tmp_path):
        """The file's last byte is a newline, so everything up to
        current_size is complete lines -- the one-`tail -c 1`-read fast
        path, the common case for a Claude Code transcript."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, 0, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(size)

    def test_slow_path_stops_before_incomplete_trailing_line(self, tmp_path):
        """The file currently ends mid-line (caught mid-write) -- the slow
        path must stop at the last complete line's newline, not at
        current_size."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\npartial-no-newline")
        complete_prefix_size = len("line one\nline two\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, 0, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(complete_prefix_size)

    def test_slow_path_from_nonzero_offset_counts_only_the_new_slice(self, tmp_path):
        """Resuming from a nonzero offset: only newlines in the unread slice
        (offset+1..current_size) count toward the returned boundary, not
        newlines before the offset."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\nline three\npartial")
        offset = len("line one\n")
        complete_prefix_size = len("line one\nline two\nline three\n")
        size = transcript.stat().st_size
        result = _advance_offset(transcript, offset, size)
        assert result.returncode == 0
        assert result.stdout.strip() == str(complete_prefix_size)

    @pytest.mark.timing
    def test_slow_path_tail_timeout_returns_offset_unchanged(self, tmp_path):
        """The slow path's `tail -c +N` calls are capped via
        _lib_capped_for(2) -- a stalled tail must not hang the scan, and per
        the function's own documented limitation must degrade to OFFSET
        unchanged rather than partial progress."""
        if not shutil.which("timeout") and not shutil.which("gtimeout"):
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\npartial-no-newline")
        real_tail = shutil.which("tail")
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        write_scaled_timeout_shim(stub_dir)
        stub = stub_dir / "tail"
        stub.write_text(
            '#!/bin/bash\n'
            'if [ "$1" = "-c" ] && [[ "$2" == +* ]]; then\n'
            f'  sleep {scaled_shim_sleep(10)}\n'
            'fi\n'
            f'exec {real_tail} "$@"\n'
        )
        stub.chmod(0o755)

        offset = 0
        size = transcript.stat().st_size
        with assert_cap_engaged(stub_dir, production_cap=SLOW_PATH_TAIL_TIMEOUT_CAP_SECONDS):
            result = _advance_offset(
                transcript, offset, size,
                env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
            )

        assert result.returncode == 0
        assert result.stdout.strip() == str(offset), "a timed-out slow-path scan must return OFFSET unchanged"

    def test_tail_absent_from_path_freezes_offset_at_current_size(self, tmp_path):
        """The function's own doc comment in _lib.sh documents this exact
        gap: total absence of `tail` from PATH makes the fast path's
        `tail -c 1` read return empty output (command-not-found, not a real
        last byte), which is indistinguishable from the file genuinely
        ending in a newline -- so the fast path fires and silently and
        permanently freezes the offset at CURRENT_SIZE, even though this
        transcript is caught mid-write with no trailing newline."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("line one\nline two\npartial-no-newline")
        size = transcript.stat().st_size
        farm_dir = tmp_path / "path-without-tail"
        farm_dir.mkdir()
        restricted_path = build_path_without("tail", farm_dir)
        result = _advance_offset(
            transcript, 0, size,
            env={**os.environ, "PATH": restricted_path},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == str(size)


class TestLibIsRepoPlanFile:
    """A relational drift test. _lib_is_repo_plan_file's contract is
    that it agrees with _lib_active_plan_hash on exactly the file set the
    hash covers -- an agreement property asserted only in a comment is one
    edit from being false."""

    def test_agrees_with_active_plan_hash_on_covered_file_set(self, tmp_path):
        repo = tmp_path / "drift-repo"
        init_git_repo(repo)
        plans_dir = repo / ".claude" / "plans"
        (plans_dir / "sub").mkdir(parents=True)
        candidates = {
            "a.md": plans_dir / "a.md",
            "b.txt": plans_dir / "b.txt",
            "c.rst": plans_dir / "c.rst",
            "sub/d.md": plans_dir / "sub" / "d.md",
        }
        for name, path in candidates.items():
            path.write_text(f"# {name}\n")

        baseline_hash = _active_plan_hash(repo)

        for name, path in candidates.items():
            content = path.read_text()
            path.unlink()
            hash_without_file = _active_plan_hash(repo)
            path.write_text(content)

            hash_changed = hash_without_file != baseline_hash
            predicate_result = _is_repo_plan_file(repo, path)
            assert predicate_result == hash_changed, (
                f"_lib_is_repo_plan_file disagreed with _lib_active_plan_hash "
                f"for {name}: predicate={predicate_result} "
                f"hash_changed={hash_changed}"
            )

    def test_inactive_on_wrong_arity(self, tmp_path: Path) -> None:
        """Extra/missing positional so $1 stays bound under set -u -- mirrors
        _lib_autonomous_shipping_active's test_inactive_on_wrong_arity in
        test_lib.py, which this function's own arity-guard comment says it
        copies the shape of. The extra-argument case uses a REPO_ROOT/ABS_PATH
        pair that would otherwise satisfy the function's own match (a real
        plan file directly under REPO_ROOT/.claude/plans), so the guard is
        the only thing standing between an extra, ignored argument and a
        false positive -- isolating the `[ "$#" -eq 2 ] || return 1` guard
        itself rather than a coincidental mismatch on placeholder args."""
        repo = tmp_path / "arity-repo"
        plans_dir = repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_file = plans_dir / "a.md"
        plan_file.write_text("# plan\n")

        for args in ([str(repo)], [str(repo), str(plan_file), "unexpected-extra-arg"]):
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'set -u; . "{LIB_SH}"; _lib_is_repo_plan_file "$@"',
                    "bash",
                    *args,
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, (
                f"_lib_is_repo_plan_file with {len(args)} args must return non-zero, "
                f"got {result.returncode}"
            )
