"""Tests for replay_bench/arms. Every agent file and config dir is synthetic under `tmp_path`, so no case reads
this checkout's agents directory. No test launches `claude`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import measure_subagent_model_resolution as msmr
import pytest
from replay_bench import arms, launch

CODE_WRITER_TEXT = (
    "---\nname: code-writer\ndescription: Implements a change,\n  then reviews it\n  before returning.\n"
    "tools: Grep, Read, Edit\nmodel: opus\neffort: xhigh\n---\n\nThe code-writer body.\n\nA second paragraph.\n"
)


def write_rule(config_dir: Path, relative: str, text: str = "rule\n") -> Path:
    path = config_dir / "rules" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestBareWriterMarkdown:
    def test_it_copies_tools_model_and_effort_and_adds_its_own_name_and_omit_claude_md(self):
        markdown = arms.bare_writer_markdown(CODE_WRITER_TEXT)

        assert msmr.agent_frontmatter_tools(markdown) == frozenset({"Grep", "Read", "Edit"})
        assert msmr.agent_frontmatter_model(markdown) == "opus"
        assert arms.agent_frontmatter_effort(markdown) == "xhigh"
        frontmatter = msmr._frontmatter_block(markdown)
        assert "name: bare-writer" in frontmatter and "omitClaudeMd: true" in frontmatter
        assert arms.agent_body(markdown) == arms.BARE_WRITER_BODY

    def test_the_effort_reader_returns_nothing_for_a_frontmatter_without_the_key(self):
        assert arms.agent_frontmatter_effort("---\nname: x\nmodel: sonnet\n---\nbody\n") is None

    @pytest.mark.parametrize("missing", ["tools", "model", "effort"])
    def test_a_code_writer_file_without_a_copied_field_is_refused_naming_it(self, missing):
        text = "".join(line for line in CODE_WRITER_TEXT.splitlines(keepends=True) if not line.startswith(f"{missing}:"))
        with pytest.raises(arms.AgentConfigError, match=missing):
            arms.bare_writer_markdown(text)


class TestRegistry:
    def test_both_arms_expect_the_account_claude_md_and_allow_no_fixture_loads(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        for arm in arms.ARMS.values():
            expectation = arm.load_expectation(claude_md)
            assert expectation.expected_dispatcher_claude_md == claude_md
            assert expectation.allowed_fixture_loads == frozenset()
        assert arms.BARE.load_expectation(claude_md).excludes_toolkit
        assert not arms.TOOLKIT.load_expectation(claude_md).excludes_toolkit

    def test_bare_dispatches_bare_writer_and_toolkit_dispatches_code_writer(self):
        assert (arms.BARE.agent_name, arms.TOOLKIT.agent_name) == ("bare-writer", "code-writer")

    def test_toolkit_writes_no_settings_file(self, tmp_path):
        assert arms.TOOLKIT.settings_text(tmp_path) is None


class TestBodyCharacterCounts:
    def test_each_body_is_counted_after_the_frontmatter(self, tmp_path):
        code_writer = tmp_path / "code-writer.md"
        code_writer.write_text(CODE_WRITER_TEXT)
        bare_writer = arms.bare_writer_markdown(CODE_WRITER_TEXT)

        assert arms.agent_body_chars(code_writer.read_text()) == len("The code-writer body.\n\nA second paragraph.")
        assert arms.agent_body_chars(bare_writer) == len(arms.BARE_WRITER_BODY)

    def test_a_frontmatter_only_file_counts_zero(self):
        assert arms.agent_body_chars("---\nname: x\n---\n") == 0
        assert arms.agent_body_chars("---\nname: x\n---") == 0

    def test_the_count_comes_from_the_accounts_file_not_the_checkouts(self, tmp_path):
        account = tmp_path / "account"
        checkout = tmp_path / "checkout"
        for directory, body in ((account, "short"), (checkout, "a much longer body than the account's")):
            (directory / "agents").mkdir(parents=True)
            (directory / "agents" / "code-writer.md").write_text(f"---\nname: code-writer\n---\n{body}\n")

        assert arms.agent_body_chars(arms.code_writer_text(account)) == len("short")


def listed(config_dir: Path) -> list[str]:
    return arms.claude_md_excludes(config_dir)


class TestClaudeMdExcludes:
    def test_a_rules_directory_link_to_a_tree_with_a_nested_rule_lists_each_path_and_realpath(self, tmp_path):
        target_tree = tmp_path / "checkout" / "rules"
        write_rule(tmp_path / "checkout", "nested/deep.md")
        write_rule(tmp_path / "checkout", "top.md")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "rules").symlink_to(target_tree)
        (config_dir / "CLAUDE.md").write_text("claude md")

        entries = listed(config_dir)

        for relative in ("nested/deep.md", "top.md"):
            assert str(config_dir / "rules" / relative) in entries
            assert os.path.realpath(config_dir / "rules" / relative) in entries
        assert str(config_dir / "CLAUDE.md") not in entries

    def test_a_real_rules_directory_of_per_file_links_lists_each_link_path_and_realpath(self, tmp_path):
        stow_target = write_rule(tmp_path / "checkout", "a.md")
        config_dir = tmp_path / "config"
        (config_dir / "rules").mkdir(parents=True)
        (config_dir / "rules" / "a.md").symlink_to(stow_target)

        entries = listed(config_dir)

        assert str(config_dir / "rules" / "a.md") in entries and str(stow_target) in entries
        assert not any(character in entry for entry in entries for character in "*?[]{}")

    def test_only_md_files_are_listed(self, tmp_path):
        write_rule(tmp_path, "a.md")
        write_rule(tmp_path, "notes.txt")
        write_rule(tmp_path, "old.md.bak")

        assert [Path(entry).name for entry in listed(tmp_path)] == ["a.md"]

    def test_a_directory_link_loop_yields_a_finite_list(self, tmp_path):
        write_rule(tmp_path, "a.md")
        (tmp_path / "rules" / "loop").symlink_to(tmp_path / "rules")

        assert listed(tmp_path) == [str(tmp_path / "rules" / "a.md")]

    def test_a_rule_path_with_a_pattern_character_is_refused_naming_the_path(self, tmp_path):
        write_rule(tmp_path, "we*ird.md")

        with pytest.raises(launch.UnsafeConfigPathError, match=r"we\*ird"):
            listed(tmp_path)

    def test_the_settings_text_is_the_json_object_with_only_the_exclude_list(self, tmp_path):
        write_rule(tmp_path, "a.md")

        settings = json.loads(arms.bare_settings_text(tmp_path))

        assert settings == {"claudeMdExcludes": listed(tmp_path)}
        assert arms.BARE.settings_text(tmp_path) == arms.bare_settings_text(tmp_path)
