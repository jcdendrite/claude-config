"""Tests for _config_dir.py's config_dir() and declared_transcript_roots() branching."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from _config_dir import (  # noqa: E402
    TRANSCRIPT_CONFIG_DIRS_LABEL,
    config_dir,
    declared_roots_file,
    declared_roots_file_state,
    declared_transcript_roots,
)


def test_returns_claude_config_dir_when_set_absolute(tmp_path, monkeypatch):
    override = tmp_path / "custom-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(override))
    assert config_dir() == override


def test_falls_back_to_home_claude_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".claude"


def test_relative_claude_config_dir_raises_value_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/path")
    with pytest.raises(ValueError, match="must be an absolute path"):
        config_dir()


def test_empty_string_claude_config_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".claude"


# ---------------------------------------------------------------------------
# declared_transcript_roots()
# ---------------------------------------------------------------------------
# conftest.py's autouse _isolate_transcript_corpus_lookups fixture already
# points TRANSCRIPT_CONFIG_DIRS_FILE at a nonexistent path; every test below
# that wants a real file overrides it with its own tmp_path location.


def _make_declared_root(base: Path, name: str) -> Path:
    """Build one valid declared-root candidate: a directory with its own projects/ subdir."""
    root = base / name
    (root / "projects").mkdir(parents=True)
    return root


def test_returns_empty_list_when_roots_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
    assert declared_transcript_roots() == []


def test_blank_lines_and_leading_hash_comments_are_skipped(monkeypatch, tmp_path):
    root = _make_declared_root(tmp_path, "acct-a")
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"\n# a comment\n{root}\n\n   \n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [root]


def test_path_containing_hash_not_truncated(monkeypatch, tmp_path):
    """A '#' that is not the first character of the trimmed line is part of
    the path, not a comment marker -- mid-line splitting would truncate it."""
    root = _make_declared_root(tmp_path, "acct#42")
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{root}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [root]


def test_crlf_line_endings_handled(monkeypatch, tmp_path):
    root = _make_declared_root(tmp_path, "acct-crlf")
    roots_file = tmp_path / "roots"
    roots_file.write_bytes(f"{root}\r\n".encode())
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [root]


def test_whitespace_padding_trimmed(monkeypatch, tmp_path):
    root = _make_declared_root(tmp_path, "acct-padded")
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"  \t{root}\t  \n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [root]


def test_bare_tilde_expands_to_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    roots_file = tmp_path / "roots"
    roots_file.write_text("~\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [home]


def test_tilde_slash_prefix_expands_via_literal_home_substitution(monkeypatch, tmp_path):
    """A leading '~/' expands via a literal $HOME prefix substitution, not
    Path.expanduser() -- an '~otheruser/...' line (untested here directly,
    but the same code path) must never resolve through the passwd database."""
    home = tmp_path / "home"
    (home / "nested" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    roots_file = tmp_path / "roots"
    roots_file.write_text("~/nested\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [home / "nested"]


def test_tilde_other_user_prefix_is_not_expanded_via_passwd_database(monkeypatch, tmp_path, capsys):
    """A '~otheruser/...' line is never routed through Path.expanduser()
    (which would resolve it via the passwd database) -- the literal string
    '~otheruser/foo' is simply never a real directory, so it's skipped via
    the standard invalid-root warning like any other bad line."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    roots_file = tmp_path / "roots"
    roots_file.write_text("~otheruser/foo\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == []
    err = capsys.readouterr().err
    assert "declared root 1" in err


def test_relative_path_line_skipped_with_index_only_warning(monkeypatch, tmp_path, capsys):
    """A relative-path line (no leading '/', '~', or '~/') would otherwise
    resolve against the process's CWD at invocation time, making the same
    line silently present or absent depending on which directory the tool is
    run from -- skipped instead, matching config_dir()'s own absolute-path
    requirement."""
    roots_file = tmp_path / "roots"
    roots_file.write_text("relative/path/to/account\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == []
    err = capsys.readouterr().err
    assert "declared root 1" in err


def test_non_directory_path_skipped_with_index_only_warning(monkeypatch, tmp_path, capsys):
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("x")
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{not_a_dir}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == []
    err = capsys.readouterr().err
    assert "declared root 1" in err
    assert str(not_a_dir) not in err


def test_directory_lacking_projects_subdir_skipped_with_index_only_warning(monkeypatch, tmp_path, capsys):
    bare_dir = tmp_path / "bare-account"
    bare_dir.mkdir()
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{bare_dir}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == []
    err = capsys.readouterr().err
    assert "declared root 1" in err
    assert str(bare_dir) not in err


def test_unreadable_root_raises_oserror_during_validation_and_is_skipped(monkeypatch, tmp_path, capsys):
    """A root directory that cannot be traversed raises OSError while
    checking for its own projects/ subdirectory, not just a plain "missing
    path" case. Skipped the same way, by index only.

    Simulated via a targeted Path.is_dir monkeypatch, not chmod(0o000) --
    chmod-based permission denial silently degrades to a different code path
    (is_dir() just returns False rather than raising) under a root-executing
    test runner, since permission bits don't restrict root."""
    unreadable_root = tmp_path / "unreadable-account"
    unreadable_root.mkdir()
    real_is_dir = Path.is_dir

    def fake_is_dir(self, **kwargs):
        if self == unreadable_root:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_dir(self, **kwargs)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{unreadable_root}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == []

    err = capsys.readouterr().err
    assert "declared root 1" in err
    assert str(unreadable_root) not in err


def test_invalid_utf8_bytes_do_not_raise(monkeypatch, tmp_path):
    """The roots file is read with errors="replace" -- an unhandled
    UnicodeDecodeError here would crash every invocation, since this runs on
    the default path of every subcommand."""
    root = _make_declared_root(tmp_path, "acct-valid")
    roots_file = tmp_path / "roots"
    roots_file.write_bytes(f"{root}\n".encode() + b"\xff\xfe not valid utf-8\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [root]


def test_dedup_by_resolved_real_path(monkeypatch, tmp_path):
    root = _make_declared_root(tmp_path, "acct-dup")
    alias = tmp_path / "acct-dup-symlink"
    alias.symlink_to(root)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{root}\n{alias}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_transcript_roots() == [root]


def test_reads_default_path_derived_from_home_when_env_var_unset(monkeypatch, tmp_path):
    """Without TRANSCRIPT_CONFIG_DIRS_FILE set (the suite-wide autouse
    fixture in conftest.py always pins it, so this is the only test that
    exercises the real default), declared_transcript_roots() must read
    $HOME/.claude/transcript-config-dirs specifically."""
    monkeypatch.delenv("TRANSCRIPT_CONFIG_DIRS_FILE", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    root = _make_declared_root(tmp_path, "acct-default-path")
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "transcript-config-dirs").write_text(f"{root}\n")
    assert declared_transcript_roots() == [root]


# ---------------------------------------------------------------------------
# declared_roots_file() / declared_roots_file_state() / TRANSCRIPT_CONFIG_DIRS_LABEL
# ---------------------------------------------------------------------------


def test_declared_roots_file_honors_seam_when_set(monkeypatch, tmp_path):
    seam_path = tmp_path / "seam-roots-file"
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(seam_path))
    assert declared_roots_file() == seam_path


def test_declared_roots_file_falls_back_to_home_claude_path_when_seam_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("TRANSCRIPT_CONFIG_DIRS_FILE", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    assert declared_roots_file() == home / ".claude" / "transcript-config-dirs"


def test_label_stays_fixed_literal_when_seam_points_elsewhere(monkeypatch, tmp_path):
    """TRANSCRIPT_CONFIG_DIRS_LABEL names where an operator would declare a
    root -- it must not reflect a test's TRANSCRIPT_CONFIG_DIRS_FILE
    redirection, unlike declared_roots_file() above."""
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "elsewhere-entirely"))
    assert TRANSCRIPT_CONFIG_DIRS_LABEL == "~/.claude/transcript-config-dirs"


def test_file_state_is_absent_when_roots_file_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "does-not-exist"))
    assert declared_roots_file_state() == "absent"


def test_file_state_is_unreadable_when_read_raises_oserror(monkeypatch, tmp_path):
    """A directory at the roots-file path exists but raises IsADirectoryError
    (an OSError subclass) on read_text() -- simulates an unreadable file
    without chmod, which silently degrades under a root-executing test
    runner (see test_unreadable_root_raises_oserror_during_validation_and_is_skipped
    above for the same rationale)."""
    roots_file_as_dir = tmp_path / "roots-is-a-directory"
    roots_file_as_dir.mkdir()
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file_as_dir))
    assert declared_roots_file_state() == "unreadable"


@pytest.mark.parametrize(
    "content",
    [
        "acct-a\n",
        "",
        "# just a comment\n\n   \n",
    ],
    ids=["valid-content", "empty-file", "comments-only"],
)
def test_file_state_is_present_regardless_of_content(monkeypatch, tmp_path, content):
    """The "present" state means the file was read successfully -- it says
    nothing about whether that content later parses into any usable root."""
    roots_file = tmp_path / "roots"
    roots_file.write_text(content)
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    assert declared_roots_file_state() == "present"
