import pytest
from transcript_analysis import render

from .conftest import _TASK_NOTIFICATION_TEXT, _asst, _task_notification_msg, _tool_result, _user_msg


class TestHelpers:
    def test_fam_opus(self):
        assert render._fam("claude-opus-4-7") == "opus"

    def test_fam_sonnet(self):
        assert render._fam("claude-sonnet-4-6") == "sonnet"

    def test_fam_haiku(self):
        assert render._fam("claude-haiku-4-5") == "haiku"

    def test_fam_unknown_model(self):
        assert render._fam("gpt-4") == "other"

    def test_content_text_plain_string(self):
        assert render._content_text("hello world") == "hello world"

    def test_content_text_list_joins_text_blocks(self):
        content = [{"type": "text", "text": "hello"}, {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "world"}]
        assert render._content_text(content) == "hello world"

    def test_content_text_none_returns_empty(self):
        assert render._content_text(None) == ""

    def test_strip_task_notifications_multiline_envelope_removed(self):
        text = "<task-notification>\n<status>completed</status>\n<summary>done</summary>\n</task-notification>"
        assert render._strip_task_notifications(text) == " "

    def test_strip_task_notifications_two_envelopes_both_removed(self):
        text = "<task-notification>first</task-notification> and <task-notification>second</task-notification>"
        assert render._strip_task_notifications(text) == "  and  "

    def test_strip_task_notifications_adjacent_envelopes_both_removed(self):
        text = "<task-notification>a</task-notification><task-notification>b</task-notification>"
        assert render._strip_task_notifications(text) == "  "

    def test_strip_task_notifications_envelope_free_string_unchanged(self):
        text = "just a plain user prompt with no envelope"
        assert render._strip_task_notifications(text) == text

    def test_strip_does_not_weld_words_across_envelope_boundary(self):
        """Single-space substitution keeps 'try' and 'again' separated across a stripped envelope boundary."""
        text = "...try <task-notification>still failing</task-notification>again..."
        result = render._strip_task_notifications(text)
        assert "try again" not in result
        assert result == "...try  again..."

    def test_strip_task_notifications_unterminated_opener_left_in_place(self):
        text = "<task-notification><summary>no closing tag here"
        assert render._strip_task_notifications(text) == text

    def test_strip_task_notifications_unterminated_opener_preserves_trailing_turn_content(self):
        text = "<task-notification><summary>no closing tag here" + "\nuser: please retry the deploy"
        assert render._strip_task_notifications(text) == text

    def test_strip_task_notifications_orphan_closer_with_no_opener_left_in_place(self):
        text = "no opener here</task-notification>"
        assert render._strip_task_notifications(text) == text

    def test_strip_task_notifications_orphan_closer_adjacent_to_real_envelope(self):
        """A match requires the opener literal first, so the stray closer with no preceding
        opener is inert. Only the well-formed envelope after it is stripped to a single space."""
        text = "stray</task-notification> then <task-notification><summary>real</summary></task-notification> after"
        assert render._strip_task_notifications(text) == "stray</task-notification> then   after"

    def test_strip_task_notifications_nested_self_quoting_stops_at_first_closer(self):
        """A `<summary>` field quotes a full envelope inline. The non-greedy match stops at the
        first `</task-notification>` (the inner envelope's own closer), leaving the outer envelope's
        closing tags dangling in the remainder."""
        text = (
            "<task-notification><summary>Sample record: "
            "<task-notification><summary>inner</summary></task-notification>"
            "</summary></task-notification>"
        )
        assert render._strip_task_notifications(text) == " </summary></task-notification>"

    def test_strip_task_notifications_preserves_non_ascii_text_around_envelope(self):
        text = (
            "café 🎉 —before "
            "<task-notification><summary>結果: café</summary></task-notification>"
            " after— 北京 😀"
        )
        result = render._strip_task_notifications(text)
        assert result == "café 🎉 —before   after— 北京 😀"

    def test_strip_task_notifications_empty_string_unchanged(self):
        assert render._strip_task_notifications("") == ""

    def test_strip_task_notifications_case_sensitive_uppercase_unchanged(self):
        """Case-sensitive match is deliberate: an uppercase-tagged string is left in
        place rather than trading a hypothetical miss for a real over-strip of
        user-typed text."""
        text = "<TASK-NOTIFICATION><summary>shout-cased</summary></TASK-NOTIFICATION>"
        assert render._strip_task_notifications(text) == text


class TestSanitizeTableCell:
    """Direct unit coverage for _sanitize_table_cell — the defense-in-depth
    control-character strip every --deny-summary table cell passes through.
    No CLI-layer test can reach this with live malicious input today, since
    every caller already passes an allowlist-bounded label (_DENIAL_HOOK_LABELS,
    _DENIAL_COMMAND_SUBCOMMANDS, _FRICTION_KINDS) — this pins the helper's own
    behavior directly instead."""

    def test_strips_esc_byte(self):
        """Only the ESC control byte itself is stripped, not surrounding
        printable characters — e.g. an ANSI escape sequence's bracket/digit
        payload is left in place, just no longer interpretable as an escape."""
        assert render._sanitize_table_cell("git\x1b[31mcommit") == "git[31mcommit"

    def test_strips_null_and_del_bytes(self):
        assert render._sanitize_table_cell("a\x00b\x7fc") == "abc"

    def test_plain_label_unchanged(self):
        assert render._sanitize_table_cell("git commit") == "git commit"

    def test_empty_string_unchanged(self):
        assert render._sanitize_table_cell("") == ""


class TestIsFreshUserPrompt:
    def test_plain_text_user_record_returns_true(self):
        rec = _user_msg("Please fix that", branch="main")
        assert render._is_fresh_user_prompt(rec) is True

    def test_tool_result_bearing_record_returns_false(self):
        rec = _user_msg([_tool_result("t1", "some result")], branch="main")
        assert render._is_fresh_user_prompt(rec) is False

    def test_ismeta_true_returns_false(self):
        rec = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "injected meta"},
            "gitBranch": "main",
        }
        assert render._is_fresh_user_prompt(rec) is False

    def test_issidechain_true_returns_false(self):
        rec = {
            "type": "user",
            "isSidechain": True,
            "message": {"content": "sidechain msg"},
            "gitBranch": "main",
        }
        assert render._is_fresh_user_prompt(rec) is False

    def test_iscompactsummary_true_returns_false(self):
        rec = {
            "type": "user",
            "isCompactSummary": True,
            "message": {"content": "summary"},
            "gitBranch": "main",
        }
        assert render._is_fresh_user_prompt(rec) is False

    def test_empty_text_content_returns_false(self):
        rec = _user_msg("", branch="main")
        assert render._is_fresh_user_prompt(rec) is False

    def test_assistant_record_returns_false(self):
        rec = _asst("claude-sonnet-4-6", branch="main")
        assert render._is_fresh_user_prompt(rec) is False

    def test_task_notification_only_record_returns_false(self):
        """GH-1312: a harness-delivered task notification is not the human speaking."""
        rec = _task_notification_msg()
        assert render._is_fresh_user_prompt(rec) is False

    def test_human_text_beside_a_task_notification_returns_true(self):
        rec = _user_msg(f"{_TASK_NOTIFICATION_TEXT}\nplease also rerun the lint step")
        assert render._is_fresh_user_prompt(rec) is True

    def test_unterminated_task_notification_returns_true(self):
        """An envelope with no closing tag is left in place by the strip, so the record stays fresh."""
        rec = _user_msg("<task-notification>\n<summary>no closing tag here")
        assert render._is_fresh_user_prompt(rec) is True

    def test_self_quoting_task_notification_returns_true(self):
        """Known limitation: the strip stops at the first closer, so the outer envelope's tail survives as text.

        A check keyed on the record's `origin` would flip this on purpose.
        """
        rec = _user_msg(
            "<task-notification><summary>Sample record: "
            "<task-notification><summary>inner</summary></task-notification>"
            "</summary></task-notification>"
        )
        assert render._is_fresh_user_prompt(rec) is True


class TestIsTaskNotification:
    @pytest.mark.parametrize(
        "rec",
        [
            pytest.param(_task_notification_msg(), id="string-envelope"),
            pytest.param(
                _user_msg(f"{_TASK_NOTIFICATION_TEXT}\n\n  {_TASK_NOTIFICATION_TEXT}"),
                id="two-envelopes-with-whitespace-between",
            ),
            pytest.param(
                _user_msg([{"type": "text", "text": _TASK_NOTIFICATION_TEXT}]),
                id="envelope-in-list-text-block",
            ),
        ],
    )
    def test_envelope_only_record_returns_true(self, rec):
        assert render._is_task_notification(rec) is True

    @pytest.mark.parametrize(
        "rec",
        [
            pytest.param(_user_msg("Please fix that"), id="plain-prompt"),
            pytest.param(
                _user_msg(f"{_TASK_NOTIFICATION_TEXT}\nplease also rerun the lint step"),
                id="human-text-beside-envelope",
            ),
            pytest.param(
                _user_msg("<task-notification>\n<summary>no closing tag here"),
                id="unterminated-envelope",
            ),
            pytest.param(
                {**_task_notification_msg(), "isSidechain": True},
                id="sidechain-envelope",
            ),
            pytest.param(
                {**_task_notification_msg(), "isMeta": True},
                id="meta-envelope",
            ),
            pytest.param(
                _user_msg([_tool_result("t1", _TASK_NOTIFICATION_TEXT)]),
                id="tool-result-record",
            ),
            pytest.param(_user_msg("   "), id="blank-text-record"),
            pytest.param(_asst("claude-sonnet-4-6"), id="assistant-record"),
        ],
    )
    def test_record_that_is_not_envelope_only_main_thread_user_text_returns_false(self, rec):
        assert render._is_task_notification(rec) is False
