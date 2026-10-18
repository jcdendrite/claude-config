"""Test helpers shared by the move-fidelity self-tests (test_transcript_move_fidelity.py,
test_transcript_move_fidelity_parser.py, test_transcript_move_fidelity_test_ids.py)."""
import textwrap
from pathlib import Path

from ._move_fidelity import main


def _invoke(capsys, *argv) -> tuple[int, list[str], str]:
    code = main([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def _checks(lines: list[str]) -> list[str]:
    """Check names of the finding lines; the last output line is always the summary, and diff lines are indented."""
    return [line.split(":", 1)[0] for line in lines[:-1] if not line.startswith(" ")]


def _write(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(source))
    return path


def _write_capture(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir()
    for name, text in files.items():
        (directory / name).write_text(text)
    return directory
