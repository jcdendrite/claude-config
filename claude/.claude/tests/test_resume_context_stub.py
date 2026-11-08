"""Positive control for helpers.install_resume_context_stub, whose empty invocation log backs the deny-only
"no launcher ran" assertions on the announce hook's shell-route arms in the announce, glob-parity and slash-script tests."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from helpers import install_resume_context_stub, resume_context_stub_invocations


def test_stub_run_by_bare_name_records_its_argv_when_first_on_path(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PATH"] = f"{install_resume_context_stub(tmp_path)}:{env['PATH']}"

    subprocess.run(["resume-context", "--cwd", "/work/tree", "example-handoff.md"], env=env, check=True)

    assert resume_context_stub_invocations(tmp_path) == "--cwd /work/tree example-handoff.md\n"


def test_stub_log_is_empty_before_any_invocation(tmp_path: Path) -> None:
    install_resume_context_stub(tmp_path)

    assert resume_context_stub_invocations(tmp_path) == ""
