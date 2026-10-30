#!/usr/bin/env python3
"""Decide whether a `code-writer` return carries evidence of its self-review.

Co-located helper of send-back-skipped-self-review.sh, which runs it with the
hook payload on stdin and the arguments `<agent_id> <marker_dir> <log_path>`.
The evaluator reads the writer's own transcript and checks, with no git
invocation:

    R1  the path floor: reviewers that the changed paths, and the comment
        lines the writer's own edits added, require
    R2  when any path changed, at least one ledger name has a Read
    R3  every reviewer the ledger names has a Read
    R4  every R1 reviewer has a ledger line and a Read
    R5  a `git diff` or a Read of a changed path follows the last edit

stdout carries the hook envelope or nothing. Exit 0 means stdout is the hook's
output. Exit 1 means an evaluator error, after which the caller prints
nothing. The send-back is claimed once per cycle through a marker directory
(`<marker_dir>/<agent_id>`) shared by the PreToolUse `SubagentHandback` arm
and the SubagentStop arm.

Design record: docs/design-decisions/code-writer-self-review-send-back.md.
"""
import contextlib
import importlib.util
import json
import os
import re
import sys
import time
from collections import namedtuple

SELF_REVIEW_HEADING = "Self-review pass — mandatory before returning"
SKILL_REVIEWER = "ai-instruction-and-memory-files"

# Pinned to the reviewer names in code-writer.md's domain table by
# test_send_back_skipped_self_review.py.
ACCEPTED_REVIEWERS = (
    "ai-instruction-and-memory-files",
    "ciso-reviewer",
    "comment-discipline-reviewer",
    "staff-analytics-engineer",
    "staff-backend-engineer",
    "staff-data-engineer",
    "staff-frontend-engineer",
    "staff-platform-engineer",
    "staff-product-engineer",
    "staff-sdet",
)

INCOMPLETE_LABEL = "SELF-REVIEW INCOMPLETE"
SELF_REVIEW_LABEL = "**Self-review:**"
STILL_UNCERTAIN_LABEL = "**Still uncertain:**"

# Pinned to the Return format's Self-review bullet in code-writer.md by test_send_back_skipped_self_review.py.
LEDGER_CONTENT_REQUEST = "<each applied angle, its finding and what you fixed, or clean>"

# code-writer's dedicated editing tools; pinned to its frontmatter by test_send_back_skipped_self_review.py.
EDIT_TOOLS = ("Edit", "Write")

PARSE_GIT_COMMAND_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parse-git-command.py")

ToolCall = namedtuple("ToolCall", "name tool_input outcome")
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_PENDING = "pending"

LedgerResult = namedtuple("LedgerResult", "names line_count unrecognized_count")

MissingItem = namedtuple("MissingItem", "kind name need_read need_ledger reasons")
KIND_REVIEWER = "reviewer"
KIND_ANY_REVIEWER = "any-reviewer"
KIND_REREAD = "reread"


# --------------------------------------------------------------------------
# Reviewer files and the path floor
# --------------------------------------------------------------------------


def reviewer_suffix(reviewer_name):
    if reviewer_name == SKILL_REVIEWER:
        return f"/skills/{SKILL_REVIEWER}/SKILL.md"
    return f"/agents/{reviewer_name}.md"


def reviewer_read_path(reviewer_name):
    return "~/.claude" + reviewer_suffix(reviewer_name)


def reviewer_name_for_read_path(file_path):
    for reviewer_name in ACCEPTED_REVIEWERS:
        if file_path.endswith(reviewer_suffix(reviewer_name)):
            return reviewer_name
    return None


_TEST_DIRECTORY_SEGMENTS = ("tests", "test", "__tests__", "spec")
_SHELL_EXTENSIONS = (".sh", ".bash", ".zsh")
_IAC_EXTENSIONS = (".tf", ".tfvars")


def _has_consecutive_segments(segments, wanted):
    width = len(wanted)
    return any(tuple(segments[start:start + width]) == wanted for start in range(len(segments) - width + 1))


def _is_test_code(path_segments, basename, extension):
    if basename.startswith("test_") or basename == "conftest.py":
        return True
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    if "." in basename and stem.endswith("_test"):
        return True
    if ".test." in basename or ".spec." in basename:
        return True
    return extension != ".md" and any(segment in _TEST_DIRECTORY_SEGMENTS for segment in path_segments[:-1])


def path_floor_reviewers(path):
    """`path` is repository-relative or absolute; every directory segment counts."""
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return {}
    basename = segments[-1]
    extension = os.path.splitext(basename)[1].lower()
    floor = {}
    if _is_test_code(segments, basename, extension):
        floor["staff-sdet"] = "changed paths include test code"
    is_ci_or_iac = (
        extension in _SHELL_EXTENSIONS
        or extension in _IAC_EXTENSIONS
        or _has_consecutive_segments(segments[:-1], (".github", "workflows"))
        or basename == "Dockerfile"
        or basename.startswith("Dockerfile.")
    )
    if is_ci_or_iac:
        floor["staff-platform-engineer"] = "changed paths include shell, CI or IaC files"
    if extension == ".md":
        floor["comment-discipline-reviewer"] = "changed paths include prose in a .md file"
        if basename in ("CLAUDE.md", "AGENTS.md") or _has_consecutive_segments(segments[:-1], (".claude", "rules")):
            floor[SKILL_REVIEWER] = "changed paths include an instruction or rule file"
    return floor


# --------------------------------------------------------------------------
# Comment lines in the text the writer added
# --------------------------------------------------------------------------

_HASH_EXTENSIONS = (".sh", ".bash", ".zsh", ".py", ".rb", ".pl", ".yml", ".yaml", ".toml")
_SLASH_EXTENSIONS = (
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".php", ".scala",
)
_PYTHON_DOCSTRING_DELIMITERS = ('"""', "'''")


def is_comment_line(path, line):
    basename = os.path.basename(path)
    extension = os.path.splitext(basename)[1].lower()
    stripped = line.lstrip()
    is_hash_family = (
        extension in _HASH_EXTENSIONS or basename == "Makefile" or basename.startswith("Dockerfile")
    )
    if is_hash_family and stripped.startswith("#") and not stripped.startswith("#!"):
        return True
    if extension == ".py" and stripped.startswith(_PYTHON_DOCSTRING_DELIMITERS):
        return True
    if extension in _SLASH_EXTENSIONS and stripped.startswith(("//", "/*")):
        return True
    return extension == ".sql" and stripped.startswith("--")


def added_lines(tool_name, tool_input):
    if not isinstance(tool_input, dict):
        return []
    if tool_name == "Write":
        content = tool_input.get("content")
        return content.split("\n") if isinstance(content, str) else []
    if tool_name == "Edit":
        new_string = tool_input.get("new_string")
        old_string = tool_input.get("old_string")
        if not isinstance(new_string, str):
            return []
        previous_lines = set(old_string.split("\n")) if isinstance(old_string, str) else set()
        return [line for line in new_string.split("\n") if line not in previous_lines]
    return []


# --------------------------------------------------------------------------
# The ledger in the report
# --------------------------------------------------------------------------

_LEDGER_LINE = re.compile(
    r"^[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:\*\*)?"
    r"(?P<tokens>`[^`\n]+`(?:(?:[ \t]*[,/][ \t]*|,[ \t]+and[ \t]+|[ \t]+and[ \t]+)`[^`\n]+`)*)"
    r"(?:\*\*)?[ \t]*:"
)
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_AGENT_FILE_TOKEN = re.compile(r"/agents/([^/]+)\.md$")


def _ledger_text(report):
    cleaned = report.replace("\r", "")
    label_at = cleaned.find(SELF_REVIEW_LABEL)
    if label_at < 0:
        return ""
    body = cleaned[label_at + len(SELF_REVIEW_LABEL):]
    end_at = body.find(STILL_UNCERTAIN_LABEL)
    return body if end_at < 0 else body[:end_at]


def _reviewer_name_for_token(token):
    token = token.strip()
    agent_match = _AGENT_FILE_TOKEN.search(token)
    if agent_match:
        return agent_match.group(1)
    if token.endswith(f"/skills/{SKILL_REVIEWER}/SKILL.md"):
        return SKILL_REVIEWER
    return token


def parse_ledger(report):
    accepted_names = []
    unrecognized_names = set()
    line_count = 0
    for line in _ledger_text(report).split("\n"):
        match = _LEDGER_LINE.match(line)
        if not match:
            continue
        line_count += 1
        for token in _BACKTICKED.findall(match.group("tokens")):
            reviewer_name = _reviewer_name_for_token(token)
            if reviewer_name in ACCEPTED_REVIEWERS:
                if reviewer_name not in accepted_names:
                    accepted_names.append(reviewer_name)
            else:
                unrecognized_names.add(reviewer_name)
    return LedgerResult(accepted_names, line_count, len(unrecognized_names))


# --------------------------------------------------------------------------
# The writer's transcript
# --------------------------------------------------------------------------


def _content_blocks(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


TranscriptScan = namedtuple("TranscriptScan", "calls result_shape_unknown")


def _user_record_follows(transcript_lines, line_index):
    """`user` records carry tool results."""
    for raw_line in transcript_lines[line_index + 1:]:
        if '"user"' not in raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("type") == "user":
            return True
    return False


def extract_tool_calls(transcript_lines):
    return scan_transcript(transcript_lines).calls


def scan_transcript(transcript_lines):
    """A writer cannot issue a later batch without having received the earlier
    batch's results, and a result always answers a call, so a transcript that
    breaks either is in a changed shape, not showing writer behavior.

    The conditions are listed under `allowed-unparsed-transcript` in
    docs/design-decisions/code-writer-self-review-send-back.md.
    """
    tool_uses = {}
    tool_use_order = []
    results = {}
    first_tool_use_line_index = None
    for line_index, raw_line in enumerate(transcript_lines):
        if '"tool_use"' not in raw_line and '"tool_result"' not in raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        record_type = record.get("type")
        for block in _content_blocks(record):
            block_type = block.get("type")
            # Only assistant records add calls, so an `api-request-blob` copy never adds or reorders one.
            if block_type == "tool_use" and record_type == "assistant":
                tool_use_id = block.get("id")
                if isinstance(tool_use_id, str) and tool_use_id not in tool_uses:
                    tool_uses[tool_use_id] = block
                    tool_use_order.append(tool_use_id)
                    if first_tool_use_line_index is None:
                        first_tool_use_line_index = line_index
            elif block_type == "tool_result" and record_type != "api-request-blob":
                tool_use_id = block.get("tool_use_id")
                if isinstance(tool_use_id, str) and tool_use_id not in results:
                    results[tool_use_id] = block
    calls = []
    for tool_use_id in tool_use_order:
        block = tool_uses[tool_use_id]
        result = results.get(tool_use_id)
        if result is None:
            outcome = OUTCOME_PENDING
        elif result.get("is_error") is True:
            outcome = OUTCOME_FAILED
        else:
            outcome = OUTCOME_SUCCESS
        tool_input = block.get("input")
        calls.append(ToolCall(block.get("name"), tool_input if isinstance(tool_input, dict) else {}, outcome))
    results_pair_with_no_call = bool(results) and not any(tool_use_id in tool_uses for tool_use_id in results)
    results_missing_after_earlier_calls = (
        bool(calls)
        and not results
        and _user_record_follows(transcript_lines, first_tool_use_line_index)
    )
    return TranscriptScan(calls, results_pair_with_no_call or results_missing_after_earlier_calls)


def has_message_content_record(transcript_lines):
    """A transcript with no such record is in a shape the extractor does not know."""
    for raw_line in transcript_lines:
        try:
            record = json.loads(raw_line)
        except ValueError:
            continue
        message = record.get("message") if isinstance(record, dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            return True
    return False


def make_path_resolver():
    cache = {}

    def resolve(file_path):
        resolved = cache.get(file_path)
        if resolved is None:
            resolved = cache[file_path] = os.path.realpath(os.path.expanduser(file_path))
        return resolved

    return resolve


def _call_path(call):
    file_path = call.tool_input.get("file_path")
    return file_path if isinstance(file_path, str) and file_path else None


def make_repository_root_finder():
    """A `.git` file (linked worktree) counts like a `.git` directory."""
    cache = {}

    def find(directory):
        visited = []
        current = directory
        while True:
            if current in cache:
                root = cache[current]
                break
            visited.append(current)
            if os.path.lexists(os.path.join(current, ".git")):
                root = current
                break
            parent = os.path.dirname(current)
            if parent == current:
                root = None
                break
            current = parent
        for visited_directory in visited:
            cache[visited_directory] = root
        return root

    return find


def load_git_record_builder():
    spec = importlib.util.spec_from_file_location("parse_git_command", PARSE_GIT_COMMAND_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the git command parser at {PARSE_GIT_COMMAND_PATH}")
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_records, module.FIELD_SEP
    except Exception as error:
        raise ImportError(f"cannot load the git command parser at {PARSE_GIT_COMMAND_PATH}") from error


def runs_git_diff(command, build_records, field_separator):
    """The shared parser splits segments on shell operators but not on newlines,
    so backslash-newline continuations are joined and each remaining line is
    parsed on its own.
    """
    if not isinstance(command, str):
        return False
    joined_continuations = command.replace("\\\r\n", " ").replace("\\\n", " ")
    for command_line in joined_continuations.split("\n"):
        if not command_line.strip():
            continue
        for record in build_records(command_line):
            fields = record.split(field_separator)
            if len(fields) > 1 and fields[0] == "GIT" and fields[1] == "diff":
                return True
    return False


# --------------------------------------------------------------------------
# Assessment
# --------------------------------------------------------------------------

Assessment = namedtuple(
    "Assessment",
    "items failed calls edits changed dropped reads ledger_lines unrecognized ledger_names",
)


def _is_diff_reread(call, changed_paths, resolve, build_records, field_separator):
    if call.name == "Read":
        file_path = _call_path(call)
        return bool(file_path) and resolve(file_path) in changed_paths
    if call.name == "Bash":
        return runs_git_diff(call.tool_input.get("command"), build_records, field_separator)
    return False


def assess(calls, report, build_records, field_separator):
    ledger = parse_ledger(report)
    find_repository_root = make_repository_root_finder()
    resolve = make_path_resolver()
    edit_calls = []
    resolved_by_index = {}
    for index, call in enumerate(calls):
        if call.name not in EDIT_TOOLS or call.outcome != OUTCOME_SUCCESS:
            continue
        edited_file_path = _call_path(call)
        if edited_file_path is None:
            continue
        edit_calls.append((index, call))
        resolved_by_index[index] = resolve(edited_file_path)
    edited_paths = set(resolved_by_index.values())
    repository_root_by_path = {}
    for edited_path in edited_paths:
        if not os.path.exists(edited_path):
            continue
        root = find_repository_root(os.path.dirname(edited_path))
        if root is not None:
            repository_root_by_path[edited_path] = root
    changed_paths = set(repository_root_by_path)

    reviewers_read = set()
    for call in calls:
        if call.outcome != OUTCOME_SUCCESS or call.name != "Read":
            continue
        file_path = _call_path(call)
        if file_path is None:
            continue
        # The unresolved candidate matters only for a symlinked reviewer file whose target basename differs.
        # Stow installs do not produce such a symlink.
        for candidate in (os.path.expanduser(file_path), resolve(file_path)):
            reviewer_name = reviewer_name_for_read_path(candidate)
            if reviewer_name:
                reviewers_read.add(reviewer_name)

    floor = {}
    for changed_path in changed_paths:
        relative_path = os.path.relpath(changed_path, repository_root_by_path[changed_path])
        for reviewer_name, reason in path_floor_reviewers(relative_path).items():
            floor.setdefault(reviewer_name, reason)
    for index, call in edit_calls:
        resolved_path = resolved_by_index[index]
        if resolved_path not in changed_paths or "comment-discipline-reviewer" in floor:
            continue
        if any(is_comment_line(resolved_path, line) for line in added_lines(call.name, call.tool_input)):
            floor["comment-discipline-reviewer"] = "your edits added comment lines"

    ledger_names = set(ledger.names)
    failed = []
    r2_failed = bool(changed_paths) and not (ledger_names & reviewers_read)
    if r2_failed:
        failed.append("R2")
    names_missing_read = ledger_names - reviewers_read
    if names_missing_read:
        failed.append("R3")
    floor_names_missing = {
        name for name in floor if name not in reviewers_read or name not in ledger_names
    }
    if floor_names_missing:
        failed.append("R4")

    items = []
    for reviewer_name in sorted(names_missing_read | floor_names_missing):
        reasons = []
        if reviewer_name in names_missing_read:
            reasons.append("your ledger names it but no Read of its file was recorded")
        if reviewer_name in floor:
            reasons.append(floor[reviewer_name])
        items.append(
            MissingItem(
                KIND_REVIEWER, reviewer_name,
                reviewer_name not in reviewers_read, reviewer_name not in ledger_names, reasons,
            )
        )
    if r2_failed and not items:
        items.append(MissingItem(KIND_ANY_REVIEWER, None, True, True, ["your edits changed files"]))

    if changed_paths:
        last_edit_index = max(
            index for index, _edit in edit_calls if resolved_by_index[index] in changed_paths
        )
        reread_found = False
        for index in range(last_edit_index + 1, len(calls)):
            call = calls[index]
            if call.outcome != OUTCOME_SUCCESS:
                continue
            if _is_diff_reread(call, changed_paths, resolve, build_records, field_separator):
                reread_found = True
                break
        if not reread_found:
            failed.append("R5")
            items.append(MissingItem(KIND_REREAD, None, False, False, ["no diff re-read follows your last edit"]))

    return Assessment(
        items=items,
        failed=failed,
        calls=len(calls),
        edits=len(edit_calls),
        changed=len(changed_paths),
        dropped=len(edited_paths - changed_paths),
        reads=len(reviewers_read),
        ledger_lines=ledger.line_count,
        unrecognized=ledger.unrecognized_count,
        ledger_names=ledger.names,
    )


def item_label(item):
    if item.kind == KIND_REREAD:
        return "no diff re-read after the last edit"
    if item.kind == KIND_ANY_REVIEWER:
        return "no ledger line backed by a reviewer Read"
    parts = []
    if item.need_read:
        parts.append(f"no Read of {item.name}")
    if item.need_ledger:
        parts.append(f"no ledger line for {item.name}")
    return " and ".join(parts)


def items_summary(items):
    return "; ".join(item_label(item) for item in items)


def _item_instruction(item):
    if item.kind == KIND_REREAD:
        return (
            "- Re-read your changes after your last edit: run `git diff -- <paths>` with Bash, "
            "or use the Read tool on a file you changed."
        )
    reasons = "; ".join(item.reasons)
    if item.kind == KIND_ANY_REVIEWER:
        return (
            "- Read the reviewer file from the domain table that matches your diff with the Read tool "
            f"({reasons}), then add a ledger line for it: `<reviewer>`: {LEDGER_CONTENT_REQUEST}."
        )
    steps = []
    if item.need_read:
        steps.append(f"Read {reviewer_read_path(item.name)} with the Read tool")
    if item.need_ledger:
        steps.append(f"add the ledger line `{item.name}`: {LEDGER_CONTENT_REQUEST}")
    sentence = ", then ".join(steps)
    return f"- {sentence[0].upper()}{sentence[1:]} ({reasons})."


def build_reason(items, unrecognized_count, event):
    lines = [f'Your self-review is incomplete (`code-writer.md` § "{SELF_REVIEW_HEADING}"). Finish these items:']
    lines.extend(_item_instruction(item) for item in items)
    lines.append("Accepted ledger names: " + ", ".join(ACCEPTED_REVIEWERS) + ".")
    lines.append(f"Ledger names not recognized: {unrecognized_count}.")
    request = (
        "Then give the complete report again in the Return format (the One line summary, "
        "Self-review and Still uncertain included), because only the final report reaches the parent."
    )
    if event == "PreToolUse":
        lines.append(request + " Call SubagentHandback again with it.")
    else:
        lines.append(
            request + " Deliver it through SubagentHandback when you have that tool, and as your final message otherwise."
        )
        lines.append(
            "If you stop without finishing these items, start your report with the line "
            f"`{INCOMPLETE_LABEL}: {items_summary(items)}`."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Marker, log and envelopes
# --------------------------------------------------------------------------


def claim_marker(marker_dir, agent_id):
    """Claim the one send-back for this cycle. Returns None on success, else a verdict."""
    try:
        os.makedirs(marker_dir, exist_ok=True)
    except OSError:
        return "allowed-claim-failed"
    try:
        os.mkdir(os.path.join(marker_dir, agent_id))
    except FileExistsError:
        return "allowed-claim-raced"
    except OSError:
        return "allowed-claim-failed"
    return None


def marker_present(marker_dir, agent_id):
    return os.path.lexists(os.path.join(marker_dir, agent_id))


def remove_marker(marker_dir, agent_id):
    with contextlib.suppress(OSError):
        os.rmdir(os.path.join(marker_dir, agent_id))


def log_line(event, verdict, agent_id, assessment=None):
    fields = {
        "calls": "-", "edits": "-", "changed": "-", "dropped": "-",
        "reads": "-", "ledger": "-", "unrecognized": "-", "failed": "-", "reviewers": "-",
    }
    if assessment is not None:
        fields.update(
            calls=assessment.calls, edits=assessment.edits, changed=assessment.changed,
            dropped=assessment.dropped, reads=assessment.reads, ledger=assessment.ledger_lines,
            unrecognized=assessment.unrecognized,
            failed=",".join(assessment.failed) or "-",
            reviewers=",".join(assessment.ledger_names) or "-",
        )
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rendered_fields = " ".join(f"{key}={value}" for key, value in fields.items())
    return f"{timestamp} {event} {verdict} agent={agent_id} {rendered_fields}"


def append_log(log_path, line):
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except (OSError, ValueError):
        pass


def pretooluse_deny_envelope(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def pretooluse_allow_envelope(updated_input):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }


def subagent_stop_block_envelope(reason):
    return {"decision": "block", "reason": reason}


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

_AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def transcript_path_for(payload, event, agent_id):
    if event == "SubagentStop":
        candidate = payload.get("agent_transcript_path")
        return candidate if isinstance(candidate, str) and os.path.isfile(candidate) else None
    parent_transcript = payload.get("transcript_path")
    if not isinstance(parent_transcript, str) or not parent_transcript:
        return None
    base = parent_transcript[:-len(".jsonl")] if parent_transcript.endswith(".jsonl") else parent_transcript
    subagents_dir = os.path.join(base, "subagents")
    wanted_name = f"agent-{agent_id}.jsonl"
    derived = os.path.join(subagents_dir, wanted_name)
    if os.path.isfile(derived):
        return derived
    for directory, subdirectories, filenames in os.walk(subagents_dir):
        subdirectories.sort()
        if wanted_name in sorted(filenames):
            candidate = os.path.join(directory, wanted_name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _read_report(payload, event):
    if event == "PreToolUse":
        tool_input = payload.get("tool_input")
        report = tool_input.get("message") if isinstance(tool_input, dict) else None
    else:
        report = payload.get("last_assistant_message")
    return report if isinstance(report, str) else ""


def _report_is_labeled(report):
    for line in report.split("\n"):
        if line.strip():
            return line.lstrip().startswith(INCOMPLETE_LABEL)
    return False


def run(payload, agent_id, marker_dir, log_path, stdout):
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        if payload.get("tool_name") != "SubagentHandback":
            return
    elif event != "SubagentStop":
        return

    def record(verdict, assessment=None):
        append_log(log_path, log_line(event, verdict, agent_id, assessment))

    transcript_path = transcript_path_for(payload, event, agent_id)
    if transcript_path is None:
        record("allowed-no-transcript")
        return
    # Reading is unbounded by bytes, so the 5-second _lib_capped limit is the only bound.
    # An over-bound transcript fails open as a logged cap-kill exit.
    with open(transcript_path, encoding="utf-8", errors="replace") as transcript_file:
        raw_lines = list(transcript_file)
    scan = scan_transcript(raw_lines)
    calls = scan.calls
    envelope_unknown = not calls and any(line.strip() for line in raw_lines) and not has_message_content_record(raw_lines)
    if envelope_unknown or scan.result_shape_unknown:
        record("allowed-unparsed-transcript")
        return
    if event == "SubagentStop" and any(call.name == "SubagentHandback" for call in calls):
        record("not-owned")
        return

    report = _read_report(payload, event)
    build_records, field_separator = load_git_record_builder()
    assessment = assess(calls, report, build_records, field_separator)
    had_marker = marker_present(marker_dir, agent_id)

    if not assessment.items:
        remove_marker(marker_dir, agent_id)
        edits_without_repository = assessment.edits > 0 and assessment.changed == 0
        record("allowed-no-repo-path" if edits_without_repository else "clean", assessment)
        return

    if had_marker:
        remove_marker(marker_dir, agent_id)
        if event == "PreToolUse":
            tool_input = payload.get("tool_input")
            message = tool_input.get("message") if isinstance(tool_input, dict) else None
            if not isinstance(message, str):
                record("passed-unflaggable", assessment)
                return
            prefix = (
                f"{INCOMPLETE_LABEL} (hook-verified): {items_summary(assessment.items)}. "
                "Treat this diff as not self-reviewed.\n\n"
            )
            updated_input = dict(tool_input)
            updated_input["message"] = prefix + message
            stdout.write(json.dumps(pretooluse_allow_envelope(updated_input)))
            stdout.flush()
            record("passed-flagged", assessment)
            return
        record("passed-labeled" if _report_is_labeled(report) else "passed-unlabeled", assessment)
        return

    if event == "SubagentStop" and payload.get("stop_hook_active") is True:
        record("allowed-continuation", assessment)
        return

    reason = build_reason(assessment.items, assessment.unrecognized, event)
    envelope = (
        pretooluse_deny_envelope(reason) if event == "PreToolUse" else subagent_stop_block_envelope(reason)
    )
    rendered_envelope = json.dumps(envelope)
    claim_failure = claim_marker(marker_dir, agent_id)
    if claim_failure is not None:
        record(claim_failure, assessment)
        return
    try:
        stdout.write(rendered_envelope)
        stdout.flush()
    except Exception:
        remove_marker(marker_dir, agent_id)
        raise
    record("sent-back", assessment)


def main(argv=None, stdin=None, stdout=None):
    argv = sys.argv[1:] if argv is None else argv
    stdout = sys.stdout if stdout is None else stdout
    if len(argv) != 3 or not _AGENT_ID_PATTERN.fullmatch(argv[0]):
        return 1
    agent_id, marker_dir, log_path = argv
    event_for_log = "-"
    try:
        # The binary stream lets json.load detect UTF-8 itself, whatever the process locale decodes text stdin as.
        payload = json.load(getattr(sys.stdin, "buffer", sys.stdin) if stdin is None else stdin)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        event_for_log = event if event in ("PreToolUse", "SubagentStop") else "-"
        run(payload, agent_id, marker_dir, log_path, stdout)
    # A narrower except tuple cannot list every failure a malformed payload or transcript raises, and any escape must fail open.
    except Exception as error:
        append_log(
            log_path,
            log_line(event_for_log, f"allowed-evaluator-error={type(error).__name__}", agent_id),
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
