"""Tests for find-consumed-continuity-file.sh.

The index file this script reads is the one resume-context.sh's
record_consumed_destination appends to (see test_resume_context.py for the
writer side and the writer/reader end-to-end contract test). These tests
build the index directly, at the same
<tmpdir-root>/resume-context-index-$EUID/consumed.tsv path
_lib_resume_context_index_file formats, so the reader is exercised in
isolation from the writer.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "find-consumed-continuity-file.sh"


def _index_path(tmpdir_root: Path) -> Path:
    return tmpdir_root / f"resume-context-index-{os.geteuid()}" / "consumed.tsv"


def _write_index(tmpdir_root: Path, rows: list[tuple[str, str, str]]) -> Path:
    """Write rows directly to the index path, creating its 0700 parent
    directory -- bypassing resume-context.sh entirely, since these tests
    target the reader's own parsing/filtering contract."""
    index = _index_path(tmpdir_root)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.parent.chmod(0o700)
    index.write_text("".join(f"{stamp}\t{dest}\t{src}\n" for stamp, dest, src in rows))
    index.chmod(0o600)
    return index


def _run(args: list[str], tmpdir_root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["RESUME_CONTEXT_TMPDIR"] = str(tmpdir_root)
    return subprocess.run(
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_no_index_reports_distinct_diagnosis(tmp_path: Path) -> None:
    result = _run([], tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "no index found" in result.stderr


def test_index_file_planted_as_symlink_is_treated_as_no_index(tmp_path: Path) -> None:
    """The index *file* itself as a symlink -- distinct from the
    index-directory-as-symlink case _lib_resume_context_index_file guards
    against -- mirrors record_consumed_destination's own `[ -L "$index" ]`
    guard on the writer side. Refusing to read through it, rather than
    dereferencing and reading whatever it points to, is the fail-closed
    shape this script uses throughout."""
    index = _index_path(tmp_path)
    index.parent.mkdir(parents=True)
    index.parent.chmod(0o700)
    real_target = tmp_path / "attacker-controlled.tsv"
    real_target.write_text("2026-01-01T00:00:00Z\t/tmp/fake-dest\t/tmp/fake-src\n")
    index.symlink_to(real_target)

    result = _run([], tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "no index found" in result.stderr


def test_reader_returns_destination_for_slug_substring(tmp_path: Path) -> None:
    dest = tmp_path / "resume-context.abc123"
    dest.write_text("moved content\n")
    src = tmp_path / "originals" / "my-slug-handoff.md"
    _write_index(tmp_path, [("2026-01-01T00:00:00Z", str(dest), str(src))])

    result = _run(["my-slug"], tmp_path)

    assert result.returncode == 0
    stamp, printed_dest, printed_src = result.stdout.rstrip("\n").split("\t")
    assert printed_dest == str(dest)
    assert printed_src == str(src)
    assert f"reload with: claude --append-system-prompt-file {dest}" in result.stderr


def test_no_matching_slug_reports_distinct_diagnosis(tmp_path: Path) -> None:
    dest = tmp_path / "resume-context.abc123"
    dest.write_text("moved content\n")
    src = tmp_path / "originals" / "my-slug-handoff.md"
    _write_index(tmp_path, [("2026-01-01T00:00:00Z", str(dest), str(src))])

    result = _run(["nonexistent-slug"], tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "no row matched nonexistent-slug" in result.stderr


def test_cleaned_up_destination_reports_distinct_unrecoverable_diagnosis(tmp_path: Path) -> None:
    """The destination file is gone (already reaped by tmp cleanup) -- the
    row matches the slug but is filtered out, and the diagnosis must name
    this case distinctly from "no row matched" so a human knows the file
    existed and is gone, rather than never having matched at all."""
    dest = tmp_path / "resume-context.abc123"  # never created
    src = tmp_path / "originals" / "my-slug-handoff.md"
    _write_index(tmp_path, [("2026-01-01T00:00:00Z", str(dest), str(src))])

    result = _run(["my-slug"], tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "cleaned up" in result.stderr
    assert "unrecoverable" in result.stderr


def test_matches_source_field_only_not_timestamp(tmp_path: Path) -> None:
    """A slug-shaped substring that occurs only in the timestamp field must
    not match -- the reader matches the source field exclusively."""
    dest = tmp_path / "resume-context.abc123"
    dest.write_text("moved content\n")
    src = tmp_path / "originals" / "unrelated-handoff.md"
    _write_index(tmp_path, [("2026-01-01T00:00:00Z", str(dest), str(src))])

    result = _run(["2026-01-01"], tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "no row matched 2026-01-01" in result.stderr


# The destination's ownership check ([ -O "$dest" ], alongside the symlink
# check exercised below) has no dedicated test: the different-owner case
# can't be reproduced in single-uid CI, the same limitation
# TestLibResumeContextIndexFile's docstring in test_lib.py documents for
# the writer's structurally identical [ -O "$dir" ] guard. It's closed by
# the same POSIX argument there: ownership can't be forged without
# privilege an attacker doesn't have.
# test_o_operator_guards_both_the_writer_directory_and_the_reader_destination
# below pins the literal check in source instead, so a future edit
# weakening either guard fails loudly.
def test_skips_a_row_whose_destination_is_a_symlink(tmp_path: Path) -> None:
    """A symlinked destination is never printed, even if its target exists
    and is readable -- this is the integrity control on a surface whose
    output feeds `claude --append-system-prompt-file`."""
    real_target = tmp_path / "unrelated-target.txt"
    real_target.write_text("not a continuity file\n")
    symlinked_dest = tmp_path / "resume-context.symlinked"
    symlinked_dest.symlink_to(real_target)
    src = tmp_path / "originals" / "my-slug-handoff.md"
    _write_index(tmp_path, [("2026-01-01T00:00:00Z", str(symlinked_dest), str(src))])

    result = _run(["my-slug"], tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "cleaned up" in result.stderr


def test_no_argument_prints_every_live_row(tmp_path: Path) -> None:
    live_dest = tmp_path / "resume-context.live"
    live_dest.write_text("still here\n")
    gone_dest = tmp_path / "resume-context.gone"  # never created
    _write_index(
        tmp_path,
        [
            ("2026-01-01T00:00:00Z", str(gone_dest), str(tmp_path / "gone-handoff.md")),
            ("2026-01-02T00:00:00Z", str(live_dest), str(tmp_path / "live-handoff.md")),
        ],
    )

    result = _run([], tmp_path)

    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").splitlines()
    assert len(lines) == 1
    assert lines[0].split("\t")[1] == str(live_dest)


def test_multiple_live_rows_printed_in_append_order(tmp_path: Path) -> None:
    first_dest = tmp_path / "resume-context.first"
    first_dest.write_text("first\n")
    second_dest = tmp_path / "resume-context.second"
    second_dest.write_text("second\n")
    _write_index(
        tmp_path,
        [
            ("2026-01-01T00:00:00Z", str(first_dest), str(tmp_path / "first-handoff.md")),
            ("2026-01-02T00:00:00Z", str(second_dest), str(tmp_path / "second-handoff.md")),
        ],
    )

    result = _run([], tmp_path)

    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").splitlines()
    assert [line.split("\t")[1] for line in lines] == [str(first_dest), str(second_dest)]
    # The reload hint names the newest (last-printed) row, not the oldest.
    assert f"reload with: claude --append-system-prompt-file {second_dest}" in result.stderr


def test_skips_a_short_field_truncated_row_among_well_formed_rows(tmp_path: Path) -> None:
    """A row truncated mid-write (e.g. disk full) has fewer than 3
    tab-separated fields; the reader must skip it silently rather than
    crash or misparse a well-formed row before or after it."""
    first_dest = tmp_path / "resume-context.first"
    first_dest.write_text("first\n")
    second_dest = tmp_path / "resume-context.second"
    second_dest.write_text("second\n")
    index = _index_path(tmp_path)
    index.parent.mkdir(parents=True)
    index.parent.chmod(0o700)
    index.write_text(
        f"2026-01-01T00:00:00Z\t{first_dest}\t{tmp_path / 'first-handoff.md'}\n"
        "2026-01-02T00:00:00Z\n"  # truncated mid-write: only the stamp field survived
        f"2026-01-03T00:00:00Z\t{second_dest}\t{tmp_path / 'second-handoff.md'}\n"
    )
    index.chmod(0o600)

    result = _run([], tmp_path)

    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").splitlines()
    assert [line.split("\t")[1] for line in lines] == [str(first_dest), str(second_dest)]


def test_o_operator_guards_both_the_writer_directory_and_the_reader_destination() -> None:
    """Source-grep pin (ciso-reviewer finding): a future edit weakening
    either ownership guard -- e.g. swapping `-O` for `-w` or `-r` -- should
    fail this test loudly rather than regress silently."""
    lib_sh = Path(__file__).parent.parent.parent / "hooks" / "_lib.sh"
    assert '[ -O "$dir" ]' in lib_sh.read_text()
    assert '[ -O "$dest" ]' in _SCRIPT.read_text()
