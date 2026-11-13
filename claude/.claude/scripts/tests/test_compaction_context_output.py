"""Tests for compaction-context.py's cap, drop order, truncation, transcript reading and output safety."""
import json
import os
import re
import sys

import pytest

from ._compaction_context_helpers import (
    PLAN_LINE_PREFIX,
    SKILL_PATH,
    at_checkout,
    block_for,
    builtin_command,
    compact_caveat,
    compact_summary,
    compaction_context,
    fenced_messages,
    headless_prompt,
    make_plan_checkout,
    pasted_prompt,
    quoted_messages,
    run_cli,
    sidechain,
    skill_dir,
    skill_load,
    text_outside_fences,
    user_prompt,
    write_transcript,
)

MESSAGE_CHAR_BUDGET = compaction_context.MESSAGE_CHAR_BUDGET
HALF_BUDGET = MESSAGE_CHAR_BUDGET // 2
SKILL_NAMES = ("oldest", "middle", "newest")


def _skills_and_plan_records(root, *, newest_prompt=None):
    """Three skills loaded oldest to newest, the first record carrying the checkout that names a plan file."""
    make_plan_checkout(root, "x")
    first, *rest = SKILL_NAMES
    records = [at_checkout(skill_load(skill_dir(first)), branch="feature/x", cwd=str(root))]
    records += [skill_load(skill_dir(name)) for name in rest]
    return records + ([user_prompt(newest_prompt)] if newest_prompt else [])


def _omitted_count(text_length: int) -> int:
    return text_length - 2 * HALF_BUDGET


def _shortest_cut_length() -> int:
    """The shortest text length for which the omission marker makes the cut text shorter than the text."""
    length = MESSAGE_CHAR_BUDGET + 1
    while 2 * HALF_BUDGET + len(compaction_context._omission_marker(_omitted_count(length))) >= length:
        length += 1
    return length


class TestCap:
    @pytest.mark.parametrize(
        "newest_prompt",
        [
            pytest.param(None, id="skills_and_plan_line_only"),
            pytest.param("日本語の指示" * 20, id="multi_byte_newest_prompt_counted_in_characters"),
        ],
    )
    def test_block_at_exactly_the_cap_is_unchanged_and_one_over_drops_the_oldest_skill(
        self, tmp_path, monkeypatch, newest_prompt
    ):
        records = _skills_and_plan_records(tmp_path / "root", newest_prompt=newest_prompt)
        full_block = block_for(tmp_path, records)
        if newest_prompt:
            assert len(full_block.encode("utf-8")) > len(full_block)

        monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", len(full_block))
        at_cap = block_for(tmp_path, records)
        monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", len(full_block) - 1)
        one_over = block_for(tmp_path, records)

        assert at_cap == full_block
        assert f"{skill_dir('oldest')}/SKILL.md" not in one_over
        assert f"{skill_dir('middle')}/SKILL.md" in one_over
        assert f"{skill_dir('newest')}/SKILL.md" in one_over
        plan_path = str(tmp_path / "root" / ".claude" / "plans" / "x.md")
        assert compaction_context.PLAN_LINE_TEMPLATE.format(path=plan_path) in one_over

    def test_block_stays_within_the_real_cap_and_keeps_the_newest_prompt(self, tmp_path):
        skills = [skill_load(skill_dir(f"{'long-name-' * 10}{index}")) for index in range(60)]
        records = [*skills, *(user_prompt(f"prompt {index} " + "word " * 290) for index in range(8))]

        block = block_for(tmp_path, records)

        assert len(block) <= compaction_context.BLOCK_CHAR_CAP
        assert quoted_messages(block)[-1].startswith("prompt 7 ")


class TestDropOrder:
    @pytest.mark.parametrize("retained_prompt_count", [compaction_context.PROMPT_LIMIT, compaction_context.PROMPT_LIMIT - 1])
    def test_cap_one_character_below_a_block_drops_only_its_oldest_prompt(self, tmp_path, monkeypatch, retained_prompt_count):
        prompts = [f"prompt {index} " + "z" * 100 for index in range(compaction_context.PROMPT_LIMIT)]
        records = [user_prompt(prompt) for prompt in prompts]
        dropped_count = compaction_context.PROMPT_LIMIT - retained_prompt_count
        block_to_undercut = block_for(tmp_path, records[dropped_count:])

        monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", len(block_to_undercut) - 1)
        block = block_for(tmp_path, records)

        assert quoted_messages(block) == prompts[dropped_count + 1:]

    def test_dropping_older_prompts_before_skills_keeps_newest_prompt_and_plan_line_at_every_cap(self, tmp_path, monkeypatch):
        records = _skills_and_plan_records(tmp_path / "root")
        records += [user_prompt(f"prompt {index} " + "y" * 400) for index in range(5)]
        full_block = block_for(tmp_path, records)
        skill_paths = [f"{skill_dir(name)}/SKILL.md" for name in reversed(SKILL_NAMES)]
        plan_line = compaction_context.PLAN_LINE_TEMPLATE.format(path=str(tmp_path / "root" / ".claude" / "plans" / "x.md"))

        for cap in range(len(full_block), 600, -53):
            monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", cap)
            block = block_for(tmp_path, records)
            messages = quoted_messages(block)
            kept_skills = [path for path in skill_paths if path in block]

            assert messages[-1].startswith("prompt 4 ")
            assert plan_line in block
            assert messages == [f"prompt {index} " + "y" * 400 for index in range(5)][-len(messages):]
            assert kept_skills == skill_paths[: len(kept_skills)]
            if len(kept_skills) < len(skill_paths):
                assert len(messages) == 1
            assert len(block) <= cap or (len(messages) == 1 and not kept_skills)


class TestHeadlessLabelUnderTheCap:
    def test_block_over_the_cap_keeps_a_headless_newest_prompt_labelled_and_drops_the_older_one_with_its_label(
        self, tmp_path, monkeypatch
    ):
        records = [headless_prompt("older headless " + "z" * 100), headless_prompt("newest headless " + "z" * 100)]
        block_of_newest_alone = block_for(tmp_path, records[1:])

        monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", len(block_of_newest_alone))
        block = block_for(tmp_path, records)

        assert block == block_of_newest_alone
        assert len(block) <= compaction_context.BLOCK_CHAR_CAP
        assert [(message.line_above, message.body) for message in fenced_messages(block)] == [
            (compaction_context.HEADLESS_LABEL, "newest headless " + "z" * 100)
        ]

    def test_label_line_counts_toward_the_cap_exactly(
        self, tmp_path, monkeypatch
    ):
        records = [user_prompt("older human " + "y" * 100), headless_prompt("newest headless " + "z" * 100)]
        full_block = block_for(tmp_path, records)

        monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", len(full_block))
        at_cap = block_for(tmp_path, records)
        monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", len(full_block) - 1)
        one_over = block_for(tmp_path, records)

        assert at_cap == full_block
        assert [message.body for message in fenced_messages(one_over)] == ["newest headless " + "z" * 100]
        assert len(one_over) <= len(full_block) - 1

    def test_label_never_outlives_its_message_at_any_cap(self, tmp_path, monkeypatch):
        records = [headless_prompt(f"headless {index} " + "w" * 300) for index in range(compaction_context.PROMPT_LIMIT)]
        full_block = block_for(tmp_path, records)
        kept_message_counts = set()

        for cap in range(len(full_block), 400, -97):
            monkeypatch.setattr(compaction_context, "BLOCK_CHAR_CAP", cap)
            block = block_for(tmp_path, records)
            kept_message_counts.add(len(fenced_messages(block)))

            assert text_outside_fences(block).count(compaction_context.HEADLESS_LABEL) == len(fenced_messages(block))
            assert all(message.line_above == compaction_context.HEADLESS_LABEL for message in fenced_messages(block))

        # A sweep that stopped trimming would pass the loop's assertions without ever dropping a message.
        assert len(kept_message_counts) >= 3


class TestTruncation:
    def test_message_of_exactly_the_budget_comes_out_unchanged(self, tmp_path):
        text = "x" * MESSAGE_CHAR_BUDGET

        assert quoted_messages(block_for(tmp_path, [user_prompt(text)])) == [text]

    def test_shortest_message_the_cut_shortens_is_cut_and_one_character_shorter_is_not(self, tmp_path):
        shortest = _shortest_cut_length()
        text = "a" * (HALF_BUDGET) + "b" * (shortest - 2 * HALF_BUDGET) + "c" * HALF_BUDGET
        shorter = text[:-1]

        cut = quoted_messages(block_for(tmp_path, [user_prompt(text)]))[0]
        uncut = quoted_messages(block_for(tmp_path, [user_prompt(shorter)]))[0]

        assert len(cut) < len(text)
        assert cut == text[:HALF_BUDGET] + compaction_context._omission_marker(_omitted_count(len(text))) + text[-HALF_BUDGET:]
        assert uncut == shorter

    def test_long_prompt_keeps_first_and_last_lines_and_names_the_omitted_count(self, tmp_path):
        text = "FIRST-SENTINEL\n" + "x" * 5_000 + "\nLAST-SENTINEL"

        quoted = quoted_messages(block_for(tmp_path, [user_prompt(text)]))[0]

        assert quoted.startswith("FIRST-SENTINEL")
        assert quoted.endswith("LAST-SENTINEL")
        assert compaction_context._omission_marker(_omitted_count(len(text))) in quoted

    def test_pasted_prompt_over_the_budget_keeps_both_wrapper_tags(self, tmp_path):
        record = pasted_prompt("p" * 3_000)
        text = record["message"]["content"]

        quoted = quoted_messages(block_for(tmp_path, [record]))[0]

        assert quoted.startswith('\n\n<pasted_content id="paste-1">')
        assert quoted.endswith('</pasted_content id="paste-1">')
        assert compaction_context._omission_marker(_omitted_count(len(text))) in quoted

    def test_tilde_run_split_by_the_cut_is_fenced_on_the_truncated_text(self, tmp_path):
        text = "a" * (HALF_BUDGET - 1) + "~" * 40 + "b" * 3_000

        block = block_for(tmp_path, [user_prompt(text)])

        assert "characters omitted" in block
        assert block.endswith("\n~~~\n")

    @pytest.mark.parametrize(
        ("text", "longest_tilde_run"),
        [
            pytest.param("intro\n~~~\nafter the line of tildes", 3, id="line_of_exactly_three_tildes"),
            pytest.param("intro\n~~~~\nafter the line of tildes", 4, id="line_of_four_tildes"),
            pytest.param("before ~~~ after", 3, id="mid_line_run"),
        ],
    )
    def test_message_holding_a_tilde_run_round_trips_inside_a_fence_longer_than_the_run(
        self, tmp_path, text, longest_tilde_run
    ):
        block = block_for(tmp_path, [user_prompt(text)])

        assert quoted_messages(block) == [text]
        assert block.endswith("\n" + "~" * (longest_tilde_run + 1) + "\n")

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("intro\n~~\x00~~\nafter the split run", id="tilde_run_split_by_nul"),
            pytest.param("a" * (HALF_BUDGET - 3) + "~~\x00~~~" + "b" * 3_000, id="tilde_run_split_by_nul_at_the_cut_boundary"),
        ],
    )
    def test_tilde_run_joined_by_stripping_nul_is_fenced_on_the_stripped_text(self, tmp_path, text):
        stripped = text.replace("\x00", "")
        expected_body = compaction_context._cut_to_budget(stripped)

        block = block_for(tmp_path, [user_prompt(text)])

        assert "\x00" not in block
        assert quoted_messages(block) == [expected_body]
        fence = block.rstrip("\n").rsplit("\n", 1)[-1]
        assert set(fence) == {"~"}
        assert len(fence) > max(len(run) for run in re.findall(r"~+", expected_body))


class TestReading:
    def test_missing_path_exits_nonzero_with_the_fixed_stderr_line_and_empty_stdout(self, tmp_path):
        result = run_cli(tmp_path / "absent.jsonl")

        assert result.returncode == 1
        assert result.stderr.decode() == "compaction-context: cannot read the transcript\n"
        assert result.stdout == b""

    def test_directory_path_takes_the_same_unreadable_exit(self, tmp_path):
        result = run_cli(tmp_path)

        assert result.returncode == 1
        assert result.stderr.decode() == "compaction-context: cannot read the transcript\n"
        assert result.stdout == b""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
    def test_mode_zero_file_takes_the_same_unreadable_exit(self, tmp_path):
        transcript = write_transcript(tmp_path, [user_prompt("hello")])
        transcript.chmod(0)

        result = run_cli(transcript)

        assert result.returncode == 1
        assert result.stderr.decode() == "compaction-context: cannot read the transcript\n"
        assert result.stdout == b""

    def test_missing_transcript_argument_exits_2_with_empty_stdout(self, capfd):
        with pytest.raises(SystemExit) as exit_info:
            compaction_context.main(["state"])

        assert exit_info.value.code == 2
        assert capfd.readouterr().out == ""

    def test_empty_file_gives_empty_stdout_and_exit_zero(self, tmp_path):
        transcript = tmp_path / "empty.jsonl"
        transcript.write_text("")

        result = run_cli(transcript)

        assert (result.returncode, result.stdout) == (0, b"")

    def test_transcript_whose_records_are_all_excluded_gives_empty_stdout_and_exit_zero(self, tmp_path):
        transcript = write_transcript(
            tmp_path,
            [sidechain(user_prompt("side")), compact_caveat(), compact_summary(), builtin_command("/compact")],
        )

        result = run_cli(transcript)

        assert (result.returncode, result.stdout) == (0, b"")

    def test_non_dict_json_line_and_truncated_final_line_leave_the_block_built_from_the_rest(self, tmp_path):
        transcript = tmp_path / "ragged.jsonl"
        lines = ["[1, 2]", "42", json.dumps(user_prompt("survivor")), '{"type": "user", "mess']
        transcript.write_text("\n".join(lines))

        assert quoted_messages(compaction_context.build_state_block(transcript)) == ["survivor"]


class TestOutputSafety:
    def test_partial_writes_are_continued_until_every_byte_is_written_to_stdout(self, monkeypatch):
        payload = b"0123456789abcdef"
        written = bytearray()
        file_descriptors = []

        def write_three_bytes_at_most(file_descriptor, data):
            chunk = bytes(data[:3])
            file_descriptors.append(file_descriptor)
            written.extend(chunk)
            return len(chunk)

        with monkeypatch.context() as patched:
            patched.setattr(compaction_context.os, "write", write_three_bytes_at_most)
            compaction_context._write_to_stdout(payload)

        assert bytes(written) == payload
        assert set(file_descriptors) == {1}
        assert len(file_descriptors) > 1

    def test_multi_byte_emoji_lone_surrogate_nul_and_ansi_give_utf8_stdout_without_nul(self, tmp_path):
        text = "héllo 日本 😀 \ud800 nul:\x00: \x1b[31mred\x1b[0m"
        transcript = write_transcript(tmp_path, [user_prompt(text)])

        result = run_cli(transcript)

        assert result.returncode == 0
        assert b"\x00" not in result.stdout
        decoded = result.stdout.decode("utf-8")
        assert "héllo 日本 😀 ? nul:: \x1b[31mred\x1b[0m" in decoded

    def test_state_mode_prints_the_block_for_a_readable_transcript(self, tmp_path):
        transcript = write_transcript(tmp_path, [skill_load(), user_prompt("hello")])

        result = run_cli(transcript)

        assert result.returncode == 0
        assert result.stdout.decode("utf-8") == block_for(tmp_path, [skill_load(), user_prompt("hello")])
        assert result.stdout.decode("utf-8").startswith(compaction_context.BLOCK_HEADER + "\n")


class TestBlockOrder:
    def test_sections_run_header_skills_plan_line_framing_then_messages_with_the_newest_prompt_last(self, tmp_path):
        root = tmp_path / "root"
        plan = make_plan_checkout(root, "x")
        records = [
            at_checkout(skill_load(), branch="feature/x", cwd=str(root)),
            user_prompt("older prompt"),
            user_prompt("newest prompt"),
        ]

        block = block_for(tmp_path, records)

        positions = [
            block.index(compaction_context.BLOCK_HEADER),
            block.index(compaction_context.REREAD_LINE),
            block.index(compaction_context.SKILLS_LABEL),
            block.index(f"- {SKILL_PATH}"),
            block.index(f"`{plan}`"),
            block.index(compaction_context.FRAMING_LINES[0]),
            block.index("older prompt"),
            block.index("newest prompt"),
        ]
        assert positions == sorted(positions)
        assert positions[0] == 0
        assert block.endswith("newest prompt\n~~~\n")


class TestPinnedValues:
    def test_omission_marker_wording_is_the_documented_cue(self):
        assert compaction_context._omission_marker(123) == "\n… [123 characters omitted] …\n"

    def test_prompt_limit_message_budget_and_label_are_the_documented_values(self):
        assert compaction_context.PROMPT_LIMIT == 5
        assert compaction_context.MESSAGE_CHAR_BUDGET == 1_500
        assert compaction_context.SKILLS_LABEL == "Skills loaded this session, most recent first:"
        assert compaction_context.BLOCK_HEADER == (
            "State block rebuilt from the session transcript after compaction, independent of the summary above."
        )


    def test_block_char_cap_is_the_size_the_survival_probe_verified(self):
        # docs/precompact-hook-behavior.md survival findings: 9,000 characters arrived intact, nothing above it was probed.
        assert compaction_context.BLOCK_CHAR_CAP == 9_000


class TestMalformedRecords:
    GOOD_PROMPT = "GOOD prompt after the malformed record"

    @pytest.mark.parametrize(
        "malformed_record",
        [
            pytest.param(
                skill_load(message={"role": "user", "content": [{"type": "text", "text": None}]}),
                id="meta_record_with_null_text_block",
            ),
            pytest.param(user_prompt([{"type": "text", "text": 5}], promptId="p-1"), id="list_content_with_integer_text_block"),
            pytest.param(user_prompt("x", message="a string message"), id="string_message"),
            pytest.param(user_prompt("x", message=["a", "list"]), id="list_message"),
            pytest.param(user_prompt("x", message={"content": 7}), id="integer_content"),
            pytest.param(user_prompt("x", origin=["human"]), id="list_origin"),
            pytest.param(headless_prompt("x", entrypoint={"nested": "dict"}), id="dict_entrypoint"),
            pytest.param(headless_prompt("x", entrypoint=["sdk-cli"]), id="list_entrypoint"),
            pytest.param(at_checkout(user_prompt("x"), branch=5, cwd=5), id="integer_branch_and_cwd"),
            pytest.param(at_checkout(user_prompt("x"), branch="feature/x", cwd=5), id="valid_branch_with_integer_cwd"),
            pytest.param(at_checkout(user_prompt("x"), branch="feature/x", cwd=["/tmp"]), id="valid_branch_with_list_cwd"),
            pytest.param(at_checkout(user_prompt("x"), branch="feature/a\x00b", cwd="/tmp/a\x00b"), id="nul_in_branch_and_cwd"),
            pytest.param(at_checkout(user_prompt("x"), branch="b" * 5_000, cwd="/" + "d" * 5_000), id="very_long_branch_and_cwd"),
        ],
    )
    def test_one_record_with_a_wrong_typed_field_costs_that_record_not_the_block(self, tmp_path, malformed_record):
        block = block_for(tmp_path, [malformed_record, user_prompt(self.GOOD_PROMPT)])

        assert quoted_messages(block)[-1] == self.GOOD_PROMPT

    def test_integer_branch_with_a_valid_checkout_that_has_a_plan_file_gives_no_plan_line_and_keeps_the_prompt(self, tmp_path):
        root = tmp_path / "root"
        make_plan_checkout(root, "x")
        records = [at_checkout(user_prompt(self.GOOD_PROMPT), branch=5, cwd=str(root))]

        block = block_for(tmp_path, records)

        assert PLAN_LINE_PREFIX not in block
        assert quoted_messages(block) == [self.GOOD_PROMPT]

    def test_line_that_is_not_valid_utf8_costs_that_line_not_the_block(self, tmp_path):
        transcript = tmp_path / "torn-line.jsonl"
        torn_line = b'{"type": "user", "text": "\xe6\x97'
        lines = [json.dumps(user_prompt("BEFORE")).encode(), torn_line, json.dumps(user_prompt("AFTER")).encode()]
        transcript.write_bytes(b"\n".join(lines) + b"\n")

        assert quoted_messages(compaction_context.build_state_block(transcript)) == ["BEFORE", "AFTER"]

    def test_raw_utf8_prompt_with_an_accent_a_non_bmp_character_and_u2028_is_quoted_byte_for_byte(self, tmp_path):
        text = "café 😀 line\u2028separator"
        transcript = write_transcript(tmp_path, [user_prompt("BEFORE"), user_prompt(text)], ensure_ascii=False)
        assert text.encode("utf-8") in transcript.read_bytes()

        block = compaction_context.build_state_block(transcript)

        assert quoted_messages(block) == ["BEFORE", text]

    def test_valid_json_line_holding_a_truncated_multi_byte_sequence_costs_that_record_and_keeps_raw_utf8_neighbours(
        self, tmp_path
    ):
        before_text, after_text = "café BEFORE", "日本 AFTER 😀"
        torn_record = json.dumps(user_prompt("TORN")).encode("utf-8").replace(b"TORN", b"caf\xc3")
        lines = [
            json.dumps(user_prompt(before_text), ensure_ascii=False).encode("utf-8"),
            torn_record,
            json.dumps(user_prompt(after_text), ensure_ascii=False).encode("utf-8"),
        ]
        transcript = tmp_path / "torn-record.jsonl"
        transcript.write_bytes(b"\n".join(lines) + b"\n")

        assert quoted_messages(compaction_context.build_state_block(transcript)) == [before_text, after_text]

    def test_json_integer_over_the_digit_limit_costs_that_line_not_the_block(self, tmp_path):
        # CPython's default 4300-digit int string-conversion limit makes json.loads raise ValueError for a 5000-digit
        # literal. The limit is pinned so a contributor's PYTHONINTMAXSTRDIGITS cannot change the outcome.
        previous_limit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            huge_integer_line = '{"type": "user", "count": ' + "9" * 5_000 + "}"
            with pytest.raises(ValueError):
                json.loads(huge_integer_line)
            lines = [json.dumps(user_prompt("BEFORE")), huge_integer_line, json.dumps(user_prompt("AFTER"))]
            transcript = tmp_path / "huge-integer.jsonl"
            transcript.write_text("\n".join(lines) + "\n")

            block = compaction_context.build_state_block(transcript)
        finally:
            sys.set_int_max_str_digits(previous_limit)

        assert quoted_messages(block) == ["BEFORE", "AFTER"]

    def test_line_nested_past_the_recursion_limit_costs_that_line_not_the_block(self, tmp_path):
        transcript = tmp_path / "deeply-nested.jsonl"
        deeply_nested_line = "[" * 200_000 + "]" * 200_000
        try:
            json.loads(deeply_nested_line)
        except RecursionError:
            pass
        else:
            pytest.skip("this interpreter parses a 200,000-deep array without raising")
        lines = [json.dumps(user_prompt("BEFORE")), deeply_nested_line, json.dumps(user_prompt("AFTER"))]
        transcript.write_text("\n".join(lines) + "\n")

        assert quoted_messages(compaction_context.build_state_block(transcript)) == ["BEFORE", "AFTER"]

    def test_block_written_to_a_pipe_whose_reader_closed_exits_3_with_one_stderr_line(self, tmp_path):
        transcript = write_transcript(tmp_path, [user_prompt("hello")])
        read_end, write_end = os.pipe()
        os.close(read_end)
        try:
            result = run_cli(transcript, stdout=write_end)
        finally:
            os.close(write_end)

        assert result.returncode == 3
        assert result.stderr.decode() == "compaction-context: internal error: BrokenPipeError\n"

    @pytest.mark.skipif(not os.path.exists("/dev/full"), reason="/dev/full is Linux-only")
    def test_block_written_to_a_full_device_exits_3_with_one_stderr_line(self, tmp_path):
        transcript = write_transcript(tmp_path, [user_prompt("hello")])
        with open("/dev/full", "wb") as full_device:
            result = run_cli(transcript, stdout=full_device)

        assert result.returncode == 3
        assert result.stderr.decode() == "compaction-context: internal error: OSError\n"

    def test_unexpected_internal_error_exits_with_its_own_code_and_leaks_no_message_text(self, tmp_path, monkeypatch, capfd):
        def raise_unexpected(transcript):
            raise RuntimeError("private message text")

        monkeypatch.setattr(compaction_context, "build_state_block", raise_unexpected)

        exit_code = compaction_context.main(["state", "--transcript", str(tmp_path / "session.jsonl")])

        captured = capfd.readouterr()
        assert exit_code == 3
        assert exit_code != compaction_context.EXIT_UNREADABLE_TRANSCRIPT
        assert captured.err == "compaction-context: internal error: RuntimeError\n"
        assert captured.out == ""
