"""Tests for ask-new-dependency-disclosure.sh and its helper,
parse-manifest-dependencies.py.

Three tiers, mirroring test_parse_git_command.py:
  1. Unit, against compute_new_dependency_names (pure, no I/O) — imported
     via importlib, since the helper's filename is not a valid Python
     identifier and this avoids a subprocess spawn per case.
  2. CLI wrapper (parse-manifest-dependencies.py's main()) — I/O cases
     only: symlink resolution, relative file_path, the wire-format
     contract.
  3. Hook subprocess (ask-new-dependency-disclosure.sh) — filter order,
     envelope integrity, fail-open/degraded-ask disposition.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    build_path_without,
    edit_input,
    multiedit_input,
    read_input,
    run_hook,
    run_hook_reason,
    write_input,
)

DISCLOSURE_HOOK = HOOKS_DIR / "ask-new-dependency-disclosure.sh"
PARSER_PATH = HOOKS_DIR / "parse-manifest-dependencies.py"

_spec = importlib.util.spec_from_file_location("parse_manifest_dependencies", PARSER_PATH)
_parse_manifest_dependencies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parse_manifest_dependencies)

compute_new_dependency_names = _parse_manifest_dependencies.compute_new_dependency_names
format_marker_line = _parse_manifest_dependencies.format_marker_line
ManifestDeltaError = _parse_manifest_dependencies.ManifestDeltaError
_MAX_RECORDS = _parse_manifest_dependencies._MAX_RECORDS
_MAX_STDIN_BYTES = _parse_manifest_dependencies._MAX_STDIN_BYTES
# Private reconstruction helper, imported for direct testing of the
# old_string/replace_all multiplicity dimension (see
# TestComputeNewDependencyNamesEditReconstruction below) — the observable
# effect on .records collapses True and False to the same output for any
# manifest shape where every occurrence contributes the same dependency
# name, so the reconstruction mechanism itself needs a direct assertion.
_reconstruct_post_text = _parse_manifest_dependencies._reconstruct_post_text


# ==========================================================================
# Tier 1 — compute_new_dependency_names (pure, no I/O)
# ==========================================================================


def _edit_tool_input(old_string=None, new_string=None, replace_all=None, file_path="/repo/package.json"):
    tool_input = {"file_path": file_path}
    if old_string is not None:
        tool_input["old_string"] = old_string
    if new_string is not None:
        tool_input["new_string"] = new_string
    if replace_all is not None:
        tool_input["replace_all"] = replace_all
    return tool_input


def _write_tool_input(content, file_path="/repo/package.json"):
    return {"file_path": file_path, "content": content}


def _multiedit_tool_input(edits, file_path="/repo/package.json"):
    return {"file_path": file_path, "edits": edits}


class TestComputeNewDependencyNamesEditReconstruction:
    def test_single_occurrence_replace_all_false_applies_once(self):
        pre = '{"dependencies":{}}'
        tool_input = _edit_tool_input('{"dependencies":{}}', '{"dependencies":{"lodash":"^4.0.0"}}', False)
        assert compute_new_dependency_names(pre, tool_input).records == ["lodash@^4.0.0"]

    def test_single_occurrence_replace_all_true_applies_once(self):
        """With only one occurrence present, replace_all=True behaves
        identically to replace_all=False."""
        pre = '{"dependencies":{}}'
        tool_input = _edit_tool_input('{"dependencies":{}}', '{"dependencies":{"lodash":"^4.0.0"}}', True)
        assert compute_new_dependency_names(pre, tool_input).records == ["lodash@^4.0.0"]

    def test_multiple_occurrences_replace_all_false_replaces_first_only(self):
        """The observable effect on .records collapses True and False to
        the same output here (the union dedups the same key regardless of
        how many occurrences were replaced), so this asserts the
        reconstruction mechanism directly rather than through the diff."""
        pre = 'AAAA{"dependencies":{}}AAAA'
        tool_input = _edit_tool_input("AAAA", "BBBB", False)
        assert _reconstruct_post_text(pre, tool_input) == 'BBBB{"dependencies":{}}AAAA'

    def test_multiple_occurrences_replace_all_true_replaces_every_occurrence(self):
        pre = 'AAAA{"dependencies":{}}AAAA'
        tool_input = _edit_tool_input("AAAA", "BBBB", True)
        assert _reconstruct_post_text(pre, tool_input) == 'BBBB{"dependencies":{}}BBBB'

    def test_old_string_absent_treated_as_empty(self):
        """A tool_input missing old_string entirely (not even an empty
        string) must not crash — PreToolUse fires prospectively on
        tool_input, so this module always models edits assuming success.
        Behaves the same as an explicit empty old_string: prepend."""
        pre = ""
        tool_input = {"file_path": "/repo/package.json", "new_string": '{"dependencies":{"lodash":"^4.0.0"}}'}
        delta = compute_new_dependency_names(pre, tool_input)
        assert delta.records == ["lodash@^4.0.0"]

    def test_empty_old_string_prepends(self):
        pre = ""
        tool_input = _edit_tool_input("", '{"dependencies":{"lodash":"^4.0.0"}}', False)
        assert compute_new_dependency_names(pre, tool_input).records == ["lodash@^4.0.0"]

    def test_empty_new_string_deletes(self):
        pre = '{"dependencies":{"lodash":"^4.0.0","left-pad":"^1.3.0"}}'
        tool_input = _edit_tool_input(',"left-pad":"^1.3.0"', "", False)
        delta = compute_new_dependency_names(pre, tool_input)
        assert delta.records == []  # a deletion never adds a new dependency

    def test_deletion_and_addition_in_one_edit(self):
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        tool_input = _edit_tool_input(
            '{"dependencies":{"lodash":"^4.0.0"}}', '{"dependencies":{"left-pad":"^1.3.0"}}', False
        )
        delta = compute_new_dependency_names(pre, tool_input)
        assert delta.records == ["left-pad@^1.3.0"]  # only the addition is "new"; the removal is not reported


class TestComputeNewDependencyNamesMultiEditRunningBuffer:
    def test_per_edit_replace_all_honored_independently(self):
        pre = '{"dependencies":{"a":"1"},"devDependencies":{"a":"1"}}'
        edits = [
            {"old_string": '"a"', "new_string": '"b"', "replace_all": True},
            {"old_string": '"b":"1"}}', "new_string": '"b":"1","c":"2"}}'},
        ]
        tool_input = _multiedit_tool_input(edits)
        delta = compute_new_dependency_names(pre, tool_input)
        assert delta.records == ["b@1", "c@2"]

    def test_multiedit_intermediate_dependency_correctly_nets_to_no_new_deps(self):
        """A MultiEdit whose intermediate step names a dependency that a
        later step in the same call removes again must net to an empty
        diff — the hook correctly does not fire. This also guards the
        buffer-simulation itself: a helper that (incorrectly) applies each
        edit against the original pre-state independently, rather than
        against the running buffer, would see the intermediate addition in
        isolation and wrongly report it as new."""
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        edits = [
            {
                "old_string": '{"dependencies":{"lodash":"^4.0.0"}}',
                "new_string": '{"dependencies":{"lodash":"^4.0.0","temp-pkg":"^1.0.0"}}',
            },
            {"old_string": ',"temp-pkg":"^1.0.0"', "new_string": ""},
        ]
        tool_input = _multiedit_tool_input(edits)
        delta = compute_new_dependency_names(pre, tool_input)
        assert delta.records == []
        assert delta.elided_count == 0


class TestComputeNewDependencyNamesWriteReconstruction:
    def test_content_verbatim_diffed_against_disk_pre_state(self):
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        tool_input = _write_tool_input('{"dependencies":{"lodash":"^4.0.0","left-pad":"^1.3.0"}}')
        assert compute_new_dependency_names(pre, tool_input).records == ["left-pad@^1.3.0"]

    def test_empty_pre_text_brand_new_manifest(self):
        tool_input = _write_tool_input('{"dependencies":{"lodash":"^4.0.0"}}')
        assert compute_new_dependency_names("", tool_input).records == ["lodash@^4.0.0"]


class TestComputeNewDependencyNamesDependencySectionUnion:
    def test_move_between_sections_does_not_fire(self):
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        tool_input = _write_tool_input('{"devDependencies":{"lodash":"^4.0.0"}}')
        assert compute_new_dependency_names(pre, tool_input).records == []

    def test_union_across_all_four_sections(self):
        pre = "{}"
        content = json.dumps(
            {
                "dependencies": {"a": "1"},
                "devDependencies": {"b": "1"},
                "peerDependencies": {"c": "1"},
                "optionalDependencies": {"d": "1"},
            }
        )
        delta = compute_new_dependency_names(pre, _write_tool_input(content))
        assert delta.records == ["a@1", "b@1", "c@1", "d@1"]


class TestComputeNewDependencyNamesMustNotFire:
    def test_version_bump_only(self):
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        tool_input = _write_tool_input('{"dependencies":{"lodash":"^4.1.0"}}')
        assert compute_new_dependency_names(pre, tool_input).records == []

    def test_scripts_key_edit_not_reported_as_a_dependency(self):
        """scripts is not one of the four dependency sections this hook
        diffs — deliberate non-coverage, not a correctness property (see
        docs/security-hardening.md's residuals list for
        scripts.preinstall/postinstall)."""
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        tool_input = _write_tool_input('{"dependencies":{"lodash":"^4.0.0"},"scripts":{"build":"tsc"}}')
        assert compute_new_dependency_names(pre, tool_input).records == []

    @pytest.mark.parametrize("field", ["overrides", "resolutions", "engines", "workspaces"])
    def test_other_top_level_fields_not_reported_as_dependencies(self, field):
        pre = '{"dependencies":{"lodash":"^4.0.0"}}'
        content = json.dumps({"dependencies": {"lodash": "^4.0.0"}, field: {"x": "1"}})
        assert compute_new_dependency_names(pre, _write_tool_input(content)).records == []


class TestComputeNewDependencyNamesParseFailures:
    def test_pre_invalid_post_valid_raises_rather_than_reporting_every_dep(self):
        """An agent repairing a broken manifest (pre-state invalid JSON,
        post-state valid) must not fire on every dependency in the repaired
        file — raising here is what routes the caller to a generic
        degraded-ask instead of a false-positive flood naming every
        pre-existing dependency as 'new'."""
        pre = "not valid json{{{"
        tool_input = _write_tool_input('{"dependencies":{"lodash":"^4.0.0","left-pad":"^1.3.0"}}')
        with pytest.raises(ManifestDeltaError):
            compute_new_dependency_names(pre, tool_input)

    def test_post_invalid_raises(self):
        pre = '{"dependencies":{}}'
        tool_input = _edit_tool_input('{"dependencies":{}}', "not valid json{{{", False)
        with pytest.raises(ManifestDeltaError):
            compute_new_dependency_names(pre, tool_input)

    def test_pre_state_non_object_json_raises(self):
        pre = "[]"
        tool_input = _write_tool_input('{"dependencies":{"lodash":"^4.0.0"}}')
        with pytest.raises(ManifestDeltaError):
            compute_new_dependency_names(pre, tool_input)


class TestComputeNewDependencyNamesEncodingEdgeCases:
    def test_crlf_line_endings_do_not_break_parsing(self):
        pre = '{\r\n  "dependencies": {}\r\n}\r\n'
        tool_input = _write_tool_input('{\r\n  "dependencies": {\r\n    "lodash": "^4.0.0"\r\n  }\r\n}\r\n')
        assert compute_new_dependency_names(pre, tool_input).records == ["lodash@^4.0.0"]

    def test_leading_bom_stripped_before_parsing(self):
        pre = "﻿" + '{"dependencies":{}}'
        tool_input = _write_tool_input("﻿" + '{"dependencies":{"lodash":"^4.0.0"}}')
        assert compute_new_dependency_names(pre, tool_input).records == ["lodash@^4.0.0"]


class TestComputeNewDependencyNamesRealisticPackageShapes:
    """A monorepo — this hook's target ecosystem, per the originating
    incident — routinely uses scoped names, git/URL constraints, and the
    workspace: protocol. Mechanical JSON-edit permutations alone don't
    exercise any of these."""

    def test_scoped_package_name(self):
        pre = "{}"
        tool_input = _write_tool_input(json.dumps({"dependencies": {"@scope/pkg": "^1.0.0"}}))
        assert compute_new_dependency_names(pre, tool_input).records == ["@scope/pkg@^1.0.0"]

    def test_git_url_version_constraint(self):
        pre = "{}"
        constraint = "git+https://github.com/example/repo.git#v1.0.0"
        tool_input = _write_tool_input(json.dumps({"dependencies": {"some-dep": constraint}}))
        assert compute_new_dependency_names(pre, tool_input).records == [f"some-dep@{constraint}"]

    def test_workspace_protocol_constraint(self):
        pre = "{}"
        tool_input = _write_tool_input(json.dumps({"dependencies": {"internal-pkg": "workspace:*"}}))
        assert compute_new_dependency_names(pre, tool_input).records == ["internal-pkg@workspace:*"]


class TestComputeNewDependencyNamesSanitizeSortCap:
    def test_control_bytes_and_ansi_stripped_from_name_and_constraint(self):
        hostile_name = "evil\x1b[31mname"
        hostile_constraint = "1.0.0\x07\x00bell-and-nul"
        pre = "{}"
        content = json.dumps({"dependencies": {hostile_name: hostile_constraint}})
        delta = compute_new_dependency_names(pre, _write_tool_input(content))
        assert len(delta.records) == 1
        assert "\x1b" not in delta.records[0]
        assert "\x07" not in delta.records[0]
        assert "\x00" not in delta.records[0]

    def test_bidi_override_and_zero_width_chars_stripped_from_name_and_constraint(self):
        """Trojan-Source-class characters (RLO, LRI/PDI isolates, ZWSP) must
        not survive into a record — all four are Unicode category Cf and can
        reorder or hide part of what the human reads in the ask reason."""
        hostile_name = "safe-‮gnp.sj⁦"  # RLO + LRI
        hostile_constraint = "1.0.0​zwsp"
        pre = "{}"
        content = json.dumps({"dependencies": {hostile_name: hostile_constraint}})
        delta = compute_new_dependency_names(pre, _write_tool_input(content))
        assert len(delta.records) == 1
        for forbidden in ("‮", "⁦", "​"):
            assert forbidden not in delta.records[0]
        assert delta.records[0] == "safe-gnp.sj@1.0.0zwsp"

    def test_records_sorted_by_name(self):
        pre = "{}"
        content = json.dumps({"dependencies": {"zeta": "1", "alpha": "1", "mu": "1"}})
        delta = compute_new_dependency_names(pre, _write_tool_input(content))
        assert delta.records == ["alpha@1", "mu@1", "zeta@1"]

    @pytest.mark.parametrize("count", [9, 10, 11])
    def test_cap_boundary(self, count):
        pre = "{}"
        deps = {f"pkg{i:02d}": "^1.0.0" for i in range(count)}
        content = json.dumps({"dependencies": deps})
        delta = compute_new_dependency_names(pre, _write_tool_input(content))
        assert len(delta.records) == min(count, _MAX_RECORDS)
        expect_elided = max(0, count - _MAX_RECORDS)
        assert delta.elided_count == expect_elided
        marker = format_marker_line(delta.elided_count)
        if count <= _MAX_RECORDS:
            assert marker == ""
        else:
            assert marker == f"…and {expect_elided} more"


class TestParseManifestDependenciesPythonFloor:
    def test_source_parses_under_python_3_9_syntax(self):
        """ruff's target-version=py312 governs lint style repo-wide, not
        this file's actual runtime floor (stock macOS /usr/bin/python3 is
        3.9.6). A 3.10+ construct would lint clean and only fail on a stow
        user's real interpreter, silently, since the hook fails open on any
        helper error — this closes that gap mechanically."""
        source = PARSER_PATH.read_text()
        ast.parse(source, feature_version=(3, 9))  # raises SyntaxError on a 3.10+-only construct


# ==========================================================================
# Tier 2 — CLI wrapper (parse-manifest-dependencies.py's main()), I/O only
# ==========================================================================


def _run_cli(payload: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(PARSER_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def _padded_payload_bytes(manifest_path: str, total_bytes: int) -> bytes:
    """Build a valid CLI payload (a no-op Edit against an existing,
    unrelated manifest — old_string not present, so the diff is empty and
    success is unambiguous) padded with an unread top-level "padding" key
    to exactly `total_bytes` on the wire, for the stdin byte-cap boundary
    tests."""
    base = {"tool_input": {"file_path": manifest_path, "old_string": "absent-marker", "new_string": "x"}}
    base_len = len(json.dumps(base).encode("utf-8"))
    # `{"padding":""}`'s own fixed overhead, added once padding is merged in.
    overhead = len(json.dumps({**base, "padding": ""}).encode("utf-8")) - base_len
    pad_len = total_bytes - base_len - overhead
    assert pad_len >= 0, f"total_bytes={total_bytes} too small for base payload of {base_len} bytes"
    payload = {**base, "padding": "x" * pad_len}
    encoded = json.dumps(payload).encode("utf-8")
    assert len(encoded) == total_bytes, f"padding math off: got {len(encoded)}, wanted {total_bytes}"
    return encoded


class TestCliWrapperIO:
    def test_stdin_at_byte_cap_not_rejected(self, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_text('{"dependencies":{}}')
        payload_bytes = _padded_payload_bytes(str(manifest), _MAX_STDIN_BYTES)
        result = subprocess.run(
            ["python3", str(PARSER_PATH)], input=payload_bytes, capture_output=True, check=False
        )
        # old_string "absent-marker" isn't present in the manifest, so the
        # edit is a no-op and the diff is empty -- success, not a crash.
        assert result.returncode == 0
        assert result.stdout == b"\n"

    def test_stdin_one_byte_over_cap_rejected(self, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_text('{"dependencies":{}}')
        payload_bytes = _padded_payload_bytes(str(manifest), _MAX_STDIN_BYTES + 1)
        result = subprocess.run(
            ["python3", str(PARSER_PATH)], input=payload_bytes, capture_output=True, check=False
        )
        assert result.returncode == 1
        assert result.stdout == b""

    def test_stdin_stdout_exit_code_contract_on_success(self, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_text('{"dependencies":{}}')
        payload = {
            "tool_input": {
                "file_path": str(manifest),
                "content": '{"dependencies":{"lodash":"^4.0.0"}}',
            }
        }
        result = _run_cli(payload)
        assert result.returncode == 0
        assert result.stdout == "\nlodash@^4.0.0"

    def test_main_writes_stdout_exactly_once_on_success(self, tmp_path, monkeypatch):
        """Pins the atomic-write invariant: main() builds the full
        wire-grammar output in memory before touching stdout at all, so a
        raise anywhere upstream cannot leave a marker-line-only or
        records-without-marker state on stdout -- the three-separate-writes
        shape this replaces was itself the risk, not just an under-tested
        edge case."""
        manifest = tmp_path / "package.json"
        manifest.write_text("{}")
        payload = json.dumps(
            {"tool_input": {"file_path": str(manifest), "content": '{"dependencies":{"lodash":"^4.0.0"}}'}}
        ).encode("utf-8")
        fake_stdin = type("FakeStdin", (), {"buffer": io.BytesIO(payload)})()
        monkeypatch.setattr(_parse_manifest_dependencies.sys, "stdin", fake_stdin)

        write_calls = []
        real_write = _parse_manifest_dependencies.sys.stdout.write

        def counting_write(s):
            write_calls.append(s)
            return real_write(s)

        monkeypatch.setattr(_parse_manifest_dependencies.sys.stdout, "write", counting_write)

        exit_code = _parse_manifest_dependencies.main()

        assert exit_code == 0
        assert len(write_calls) == 1
        assert write_calls[0] == "\nlodash@^4.0.0"

    def test_stdin_stdout_exit_code_contract_on_failure(self, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_text("not valid json{{{")
        payload = {"tool_input": {"file_path": str(manifest), "old_string": "x", "new_string": "y"}}
        result = _run_cli(payload)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr != ""

    def test_symlinked_manifest_resolves_through_the_link(self, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real_manifest = real_dir / "package.json"
        real_manifest.write_text('{"dependencies":{}}')
        link = tmp_path / "package.json"
        link.symlink_to(real_manifest)
        payload = {
            "tool_input": {
                "file_path": str(link),
                "content": '{"dependencies":{"lodash":"^4.0.0"}}',
            }
        }
        result = _run_cli(payload)
        assert result.returncode == 0
        assert result.stdout == "\nlodash@^4.0.0"

    def test_relative_file_path_resolved_against_payload_cwd(self, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_text('{"dependencies":{}}')
        payload = {
            "tool_input": {
                "file_path": "package.json",
                "content": '{"dependencies":{"lodash":"^4.0.0"}}',
            },
            "cwd": str(tmp_path),
        }
        result = _run_cli(payload)
        assert result.returncode == 0
        assert result.stdout == "\nlodash@^4.0.0"

    def test_nul_separates_multiple_records(self, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_text("{}")
        content = json.dumps({"dependencies": {"a": "1", "b": "2"}})
        payload = {"tool_input": {"file_path": str(manifest), "content": content}}
        result = _run_cli(payload)
        assert result.returncode == 0
        _marker_line, _sep, records_blob = result.stdout.partition("\n")
        assert "\x00" in records_blob
        assert records_blob.split("\x00") == ["a@1", "b@2"]

    def test_marker_line_is_a_distinct_field_from_a_colliding_record(self, tmp_path):
        """A dependency literally named to match the cap-marker's own text
        must not be misread as cap metadata: with exactly one new
        dependency, elided_count is 0, so the marker line is empty even
        though the sole record's own text collides with what a marker
        would say."""
        manifest = tmp_path / "package.json"
        manifest.write_text("{}")
        hostile_name = "…and 3 more"
        content = json.dumps({"dependencies": {hostile_name: "^1.0.0"}})
        payload = {"tool_input": {"file_path": str(manifest), "content": content}}
        result = _run_cli(payload)
        assert result.returncode == 0
        marker_line, _sep, records_blob = result.stdout.partition("\n")
        assert marker_line == ""
        assert records_blob == f"{hostile_name}@^1.0.0"


# ==========================================================================
# Tier 3 — hook subprocess (ask-new-dependency-disclosure.sh)
# ==========================================================================


def _run_disclosure(
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
    hook: Path = DISCLOSURE_HOOK,
) -> tuple[int, str, str]:
    """Raw runner returning (returncode, stdout, stderr) — NOT
    helpers.run_hook, which maps every returncode != 2 to "allow" and would
    therefore read a hook crash (nonzero exit, empty stdout) identically to
    a deliberate silent-allow disposition."""
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    if extra_env is not None:
        env.update(extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def test_run_disclosure_positive_control_detects_nonzero_exit(tmp_path):
    """Sanity-checks _run_disclosure itself: without this, a runner that
    always reported returncode 0 would make every deliberate-allow
    assertion below vacuously true."""
    always_fails = tmp_path / "always-fails.sh"
    always_fails.write_text("#!/bin/bash\nexit 1\n")
    always_fails.chmod(0o755)
    returncode, _stdout, _stderr = _run_disclosure({}, hook=always_fails)
    assert returncode == 1


def _package_json(tmp_path: Path, content: str, name: str = "package.json") -> Path:
    manifest = tmp_path / name
    manifest.write_text(content)
    return manifest


class TestHookFilterSteps:
    # ---- Step 1: tool_name ------------------------------------------------

    @pytest.mark.parametrize(
        "payload",
        [
            bash_input("npm install lodash"),
            read_input("/repo/package.json"),
        ],
        ids=["bash", "read"],
    )
    def test_non_edit_write_multiedit_tool_silent_allow(self, payload, isolated_home):
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        assert stdout == ""

    # ---- Step 2: file_path --------------------------------------------

    def test_empty_file_path_silent_allow(self, isolated_home):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "", "content": "{}"}}
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        assert stdout == ""

    # ---- Step 3: basename, case-sensitive ------------------------------

    @pytest.mark.parametrize("basename", ["Package.json", "Package.JSON", "package.JSON", "notpackage.json"])
    def test_non_lowercase_basename_silent_allow(self, basename, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{}}', name=basename)
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.0.0"}}')
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        assert stdout == ""

    # ---- Step 4: path-segment exclusion --------------------------------

    @pytest.mark.parametrize(
        "relative_dir",
        [
            "node_modules/some-pkg",
            "a/node_modules/some-pkg",
            "fixtures",
            "a/fixtures",
            "__fixtures__",
            "test-data",
        ],
    )
    def test_excluded_directory_silent_allow(self, relative_dir, isolated_home, tmp_path):
        target_dir = tmp_path / relative_dir
        target_dir.mkdir(parents=True)
        manifest = _package_json(target_dir, '{"dependencies":{}}')
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.0.0"}}')
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        assert stdout == ""

    @pytest.mark.parametrize(
        "relative_dir",
        ["my-fixtures-app", "node_modules_backup", "test-data-generator"],
    )
    def test_similarly_named_but_not_excluded_directory_still_evaluated(
        self, relative_dir, isolated_home, tmp_path
    ):
        """Precision control: the exclusion is a whole-path-segment match,
        not a substring match. A directory that merely contains the
        excluded word as part of a longer name must still be evaluated —
        an unintended-but-adjacent exclusion here would be the single
        largest silent bypass in this hook's design."""
        target_dir = tmp_path / relative_dir
        target_dir.mkdir(parents=True)
        manifest = _package_json(target_dir, '{"dependencies":{}}')
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.0.0"}}')
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "ask"

    # ---- Step 5: on-disk size guard ------------------------------------

    def test_on_disk_manifest_at_5mb_threshold_not_degraded(self, isolated_home, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_bytes(b" " * 5242880)
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        # Not over the threshold -- proceeds to steps 6/7, which fail open
        # on this non-JSON pre-state edit that itself has an empty diff
        # (old_string "x" not found in an all-space file, so the edit is a
        # no-op and the manifest still fails to parse as JSON either way).
        assert returncode == 0

    def test_on_disk_manifest_one_byte_over_5mb_threshold_degraded_ask(self, isolated_home, tmp_path):
        manifest = tmp_path / "package.json"
        manifest.write_bytes(b" " * 5242881)
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "dependency delta could not be determined" in reason

    # ---- Step 6: interpreter sanity probe ------------------------------

    def test_python3_absent_from_path_silent_allow(self, isolated_home, tmp_path):
        farm_dir = tmp_path / "path-farm"
        farm_dir.mkdir()
        restricted_path = build_path_without("python3", farm_dir)
        manifest = _package_json(tmp_path, '{"dependencies":{}}')
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.0.0"}}')
        returncode, stdout, _stderr = _run_disclosure(
            payload, home=isolated_home, extra_env={"PATH": restricted_path}
        )
        assert returncode == 0
        assert stdout == ""

    def test_python3_present_but_unusable_silent_allow_not_degraded(self, isolated_home, tmp_path):
        """The Xcode Command Line Tools shim shape: python3 is on PATH but
        `python3 -c ''` exits nonzero. This must resolve to silent allow,
        not degraded ask — the two are easy to conflate since both
        originate from "the helper didn't run"."""
        shim_dir = tmp_path / "shim"
        shim_dir.mkdir()
        shim = shim_dir / "python3"
        shim.write_text("#!/bin/bash\nexit 1\n")
        shim.chmod(0o755)
        broken_path = f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        manifest = _package_json(tmp_path, '{"dependencies":{}}')
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.0.0"}}')
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home, extra_env={"PATH": broken_path})
        assert returncode == 0
        assert stdout == ""

    def test_jq_absent_silent_allow(self, isolated_home, tmp_path):
        farm_dir = tmp_path / "path-farm"
        farm_dir.mkdir()
        restricted_path = build_path_without("jq", farm_dir)
        manifest = _package_json(tmp_path, '{"dependencies":{}}')
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.0.0"}}')
        returncode, stdout, _stderr = _run_disclosure(
            payload, home=isolated_home, extra_env={"PATH": restricted_path}
        )
        assert returncode == 0
        assert stdout == ""

    # ---- Step 7: helper spawn -------------------------------------------

    def test_helper_success_new_dependency_asks(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        payload = edit_input(
            str(manifest),
            old_string='{"dependencies":{"lodash":"^4.0.0"}}',
            new_string='{"dependencies":{"lodash":"^4.0.0","left-pad":"^1.3.0"}}',
        )
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "ask"

    def test_helper_success_empty_diff_silent_allow(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        payload = edit_input(
            str(manifest),
            old_string='{"dependencies":{"lodash":"^4.0.0"}}',
            new_string='{"dependencies":{"lodash":"^4.1.0"}}',
        )
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        assert stdout == ""

    def test_helper_failure_unparseable_pre_state_degraded_ask(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, "not valid json{{{")
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "dependency delta could not be determined" in reason


class TestHookMustNotFireCases:
    def test_version_bump_only_allowed(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        payload = write_input(str(manifest), content='{"dependencies":{"lodash":"^4.1.0"}}')
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "allow"

    def test_dependency_moved_between_sections_allowed(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        payload = write_input(str(manifest), content='{"devDependencies":{"lodash":"^4.0.0"}}')
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "allow"

    def test_wholesale_reformat_via_write_allowed(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        payload = write_input(str(manifest), content='{\n  "dependencies": {\n    "lodash": "^4.0.0"\n  }\n}\n')
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "allow"

    @pytest.mark.parametrize("field", ["scripts", "overrides", "resolutions", "engines", "workspaces"])
    def test_non_dependency_field_edit_allowed(self, field, isolated_home, tmp_path):
        """Not asserting broader coverage than the diff actually provides:
        scripts.preinstall/postinstall is deliberate non-coverage (see
        docs/security-hardening.md), not a claim these fields are safe."""
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        content = json.dumps({"dependencies": {"lodash": "^4.0.0"}, field: {"x": "1"}})
        payload = write_input(str(manifest), content=content)
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "allow"

    def test_multiedit_replace_all_net_zero_allowed(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"lodash":"^4.0.0"}}')
        edits = [
            {
                "old_string": '{"dependencies":{"lodash":"^4.0.0"}}',
                "new_string": '{"dependencies":{"lodash":"^4.0.0","temp-pkg":"^1.0.0"}}',
            },
            {"old_string": ',"temp-pkg":"^1.0.0"', "new_string": ""},
        ]
        payload = multiedit_input(str(manifest), edits=edits)
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "allow"

    def test_bare_bash_install_command_not_this_hooks_matcher(self, isolated_home):
        """The bare-restore surface is Layer 3's concern
        (deny-network-installs.sh), not this hook's — this hook only sees
        Edit/Write/MultiEdit."""
        returncode, stdout, _stderr = _run_disclosure(bash_input("npm install"), home=isolated_home)
        assert returncode == 0
        assert stdout == ""


class TestHookReasonContent:
    def test_reason_names_every_added_package_not_just_the_first(self, isolated_home, tmp_path):
        """A head-1 bug would pass a single-name check; this requires both
        added names to appear."""
        manifest = _package_json(tmp_path, "{}")
        content = json.dumps({"dependencies": {"lodash": "^4.0.0", "left-pad": "^1.3.0"}})
        payload = write_input(str(manifest), content=content)
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "lodash@^4.0.0" in reason
        assert "left-pad@^1.3.0" in reason

    def test_reason_does_not_name_pre_existing_dependencies_or_scripts_key(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{"existing-pkg":"^1.0.0"}}')
        content = json.dumps(
            {
                "dependencies": {"existing-pkg": "^1.0.0", "new-pkg": "^2.0.0"},
                "scripts": {"build": "tsc"},
            }
        )
        payload = write_input(str(manifest), content=content)
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "new-pkg@^2.0.0" in reason
        assert "existing-pkg" not in reason
        assert "scripts" not in reason

    def test_cap_marker_present_only_past_ten_new_dependencies(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, "{}")
        deps = {f"pkg{i:02d}": "^1.0.0" for i in range(12)}
        content = json.dumps({"dependencies": deps})
        payload = write_input(str(manifest), content=content)
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "…and 2 more" in reason


class TestHookDegradedAskDistinguishedFromOrdinaryAsk:
    """Each degraded case is paired with a same-fixture non-degraded case
    proving the ordinary path is still reachable — otherwise a helper that
    always degrades would pass every degraded-case test while the ordinary
    path silently never fires."""

    def test_oversized_manifest_degraded_reason_has_no_package_names(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, "{}")
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        manifest.write_bytes(b" " * 5242881)
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "dependency delta could not be determined" in reason
        assert "@" not in reason  # no name@constraint record leaked into a degraded reason

    def test_same_manifest_under_threshold_gets_the_ordinary_ask(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, '{"dependencies":{}}')
        payload = edit_input(
            str(manifest),
            old_string='{"dependencies":{}}',
            new_string='{"dependencies":{"lodash":"^4.0.0"}}',
        )
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "lodash@^4.0.0" in reason
        assert "dependency delta could not be determined" not in reason

    def test_malformed_pre_state_degraded_reason_has_no_package_names(self, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, "not valid json{{{")
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "dependency delta could not be determined" in reason
        assert "@" not in reason


class TestHookEnvelopeIntegrity:
    """Every interpolated value round-trips through the JSON envelope
    correctly, even when it is itself JSON-injection-shaped — this hook is
    the repo's first to interpolate untrusted content into a decision
    envelope."""

    @pytest.mark.parametrize(
        "hostile_name",
        [
            'evil"name',
            "evil\\name",
            'evil","permissionDecision":"allow',
        ],
        ids=["quote", "backslash", "injection-shaped"],
    )
    def test_hostile_dependency_name_round_trips_through_the_envelope(self, hostile_name, isolated_home, tmp_path):
        manifest = _package_json(tmp_path, "{}")
        content = json.dumps({"dependencies": {hostile_name: "^1.0.0"}})
        payload = write_input(str(manifest), content=content)
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        decoded = json.loads(stdout)  # raises if the envelope is not valid JSON
        assert decoded["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert hostile_name in decoded["hookSpecificOutput"]["permissionDecisionReason"]

    @pytest.mark.parametrize(
        "hostile_constraint",
        [
            'evil"constraint',
            "evil\\constraint",
            'evil","permissionDecision":"allow',
        ],
        ids=["quote", "backslash", "injection-shaped"],
    )
    def test_hostile_version_constraint_round_trips_through_the_envelope(
        self, hostile_constraint, isolated_home, tmp_path
    ):
        manifest = _package_json(tmp_path, "{}")
        content = json.dumps({"dependencies": {"some-pkg": hostile_constraint}})
        payload = write_input(str(manifest), content=content)
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        decoded = json.loads(stdout)
        assert decoded["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert hostile_constraint in decoded["hookSpecificOutput"]["permissionDecisionReason"]

    @pytest.mark.parametrize(
        "hostile_dirname",
        ['evil"dir', "evil\\dir", 'evil","permissionDecision":"allow'],
        ids=["quote", "backslash", "injection-shaped"],
    )
    def test_hostile_directory_name_on_degraded_ask_path_round_trips(self, hostile_dirname, isolated_home, tmp_path):
        target_dir = tmp_path / hostile_dirname
        target_dir.mkdir()
        manifest = _package_json(target_dir, "{}")
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        manifest.write_bytes(b" " * 5242881)  # forces the degraded-ask path
        returncode, stdout, _stderr = _run_disclosure(payload, home=isolated_home)
        assert returncode == 0
        decoded = json.loads(stdout)
        assert decoded["hookSpecificOutput"]["permissionDecision"] == "ask"

    def test_bidi_override_directory_name_stripped_on_degraded_ask_path(self, isolated_home, tmp_path):
        """The bash-side sanitizer (jq's \\p{Cf}) must strip the same
        Trojan-Source-class characters as the Python helper's unicodedata
        check -- this exercises the size-guard degraded-ask path, which
        runs before step 6's python3 probe and so cannot rely on the
        helper's own sanitizer."""
        target_dir = tmp_path / "safe-‮drive.sh"  # RLO
        target_dir.mkdir()
        manifest = _package_json(target_dir, "{}")
        payload = edit_input(str(manifest), old_string="x", new_string="y")
        manifest.write_bytes(b" " * 5242881)
        reason = run_hook_reason(DISCLOSURE_HOOK, payload, home=isolated_home)
        assert reason is not None
        assert "‮" not in reason


class TestHookSubagentBehavior:
    def test_code_writer_subagent_gets_an_identical_ask(self, isolated_home, tmp_path):
        """Resolves the assumption ledger's [unverified] subagent row for
        the hook's own behavior — whether the hook fires identically for a
        code-writer subagent's tool calls as for the top-level session.
        Whether the ask actually RENDERS to the human for a subagent stays
        Anthropic's contract, not something this hook controls."""
        manifest = _package_json(tmp_path, '{"dependencies":{}}')
        payload = edit_input(
            str(manifest),
            old_string='{"dependencies":{}}',
            new_string='{"dependencies":{"lodash":"^4.0.0"}}',
            agent_type="code-writer",
        )
        top_level_payload = edit_input(
            str(manifest),
            old_string='{"dependencies":{}}',
            new_string='{"dependencies":{"lodash":"^4.0.0"}}',
        )
        assert run_hook(DISCLOSURE_HOOK, payload, home=isolated_home) == "ask"
        assert run_hook(DISCLOSURE_HOOK, top_level_payload, home=isolated_home) == "ask"
