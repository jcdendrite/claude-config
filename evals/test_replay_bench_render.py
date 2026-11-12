"""Tests for the one render point that keeps child-derived and agent-derived text off the terminal raw."""
from __future__ import annotations

import io

import pytest
from replay_bench import attempt, comment_metrics, tasks
from replay_bench.arms import BARE
from replay_bench.render import SafeStream, safe_text
from replay_bench.runner import execute_run
from test_replay_bench_runner_support import make_block, make_settings

HOSTILE = "bad\x1b[31m\nforged line end"


class TestSafeText:
    def test_printable_text_passes_unchanged(self):
        assert safe_text("plain 'quoted' text é") == "plain 'quoted' text é"

    def test_control_characters_and_line_separators_are_escaped(self):
        escaped = safe_text(HOSTILE)

        assert escaped == "bad\\x1b[31m\\nforged line\\u2028end"
        assert "\x1b" not in escaped and "\n" not in escaped

    def test_a_message_the_runner_composed_across_lines_can_keep_its_line_breaks_but_not_an_escape(self):
        assert safe_text("one\ntwo\x1b", keep_line_breaks=True) == "one\ntwo\\x1b"


class TestSafeStream:
    def test_hostile_text_is_escaped_while_newline_and_tab_are_kept(self):
        target = io.StringIO()

        SafeStream(target).write("ok\tline one\n\x1b]0;title\x07 line two\r\n")

        assert target.getvalue() == "ok\tline one\n\\x1b]0;title\\x07 line two\\r\n"

    def test_an_escape_character_alone_in_one_write_is_escaped(self):
        target = io.StringIO()
        stream = SafeStream(target)

        stream.write("\x1b")
        stream.write("[31m")

        assert target.getvalue() == "\\x1b[31m"

    def test_write_returns_the_length_of_the_text_it_was_given(self):
        assert SafeStream(io.StringIO()).write("a\x1bb") == 3

    def test_every_other_attribute_is_the_wrapped_streams(self):
        target = io.StringIO()

        SafeStream(target).flush()

        assert SafeStream(target).isatty() is False


class TestEverySinkUsesIt:
    def test_a_missing_detail_built_from_an_exception_message_is_escaped(self, world, monkeypatch):
        def failing_prune(_dest_dir):
            raise RuntimeError(HOSTILE)

        monkeypatch.setattr(tasks, "prune_instruction_files", failing_prune)
        launch_stub = world.launch()

        outcome = execute_run(make_settings(world, launch_stub), make_block(world), BARE.name, 0)

        assert outcome.record.missing_reason == attempt.REASON_FIXTURE_ERROR
        assert "\x1b" not in outcome.record.missing_detail and "\n" not in outcome.record.missing_detail
        assert "forged line" in outcome.record.missing_detail

    def test_the_classifier_names_an_agent_chosen_path_in_repr_form(self):
        with pytest.raises(ValueError, match=r"a\\x1bb\.py") as raised:
            comment_metrics.classify_file("a\x1bb.py", b"one line\n", [5])

        assert "\x1b" not in str(raised.value)
