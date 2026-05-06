"""Tests for _lib.sh shared helper library."""
from __future__ import annotations

import hashlib
import subprocess

from helpers import HOOKS_DIR

LIB_SH = HOOKS_DIR / "_lib.sh"


def _run_lib_fn(fn_call: str) -> str:
    """Source _lib.sh in a bash subprocess and evaluate fn_call."""
    result = subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"; {fn_call}'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestMarkerLibRepoHash:
    def test_known_path_matches_python_sha256(self):
        path = "/some/known/path"
        expected = hashlib.sha256(path.encode()).hexdigest()
        actual = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        assert actual == expected, (
            f"_marker_lib_repo_hash produced {actual!r}, expected {expected!r}"
        )

    def test_no_trailing_newline_in_hash_input(self):
        # Verify that the hash equals the Python sha256 of the bare path bytes,
        # confirming printf '%s' does not add a trailing newline to the input.
        path = "/tmp/foo"
        expected = hashlib.sha256(path.encode()).hexdigest()
        actual = _run_lib_fn(f'_marker_lib_repo_hash "{path}"')
        assert actual == expected, (
            f"_marker_lib_repo_hash produced {actual!r}, expected {expected!r}"
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
