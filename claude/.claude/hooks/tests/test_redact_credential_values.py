"""Tests for redact-credential-values.sh.

tool_response shapes: Bash ({"stdout","stderr","exit_code"}) and Read
({"file_path","file_contents"}) are confirmed against Anthropic's hooks
docs (code.claude.com/docs/en/hooks) and used verbatim below. WebFetch,
Grep, and Task carry no documented tool_response shape; the fixtures below
use a plausible-but-unconfirmed {"content": "..."} shape for those three,
noted at each site. The hook's own redaction is shape-agnostic (a jq `walk`
over every string leaf, regardless of which keys exist), which is exactly
what makes it safe to test an assumed shape here — a wrong guess about the
real field name would still exercise the same code path, since the walk
never looks for a specific key.

Synthetic values used in these tests — all invented, none a real secret:
  ghp_abcdefghijklmnopqrstuvwx1234              (GitHub classic PAT shape)
  github_pat_abcdefghijklmnopqrstuvwx1234        (GitHub fine-grained shape)
  -----BEGIN RSA PRIVATE KEY-----                (PEM header shape only)
  MIIEpAIBAAKCAQEAsecretkeybodyherethatisverylongandsecret
                                                 (invented base64-shaped PEM body, not a real key)
"""
from __future__ import annotations

import json
import subprocess

from helpers import HOOKS_DIR, NO_UPDATED_OUTPUT, run_hook_updated_output

REDACT_CREDENTIAL_VALUES_HOOK = HOOKS_DIR / "redact-credential-values.sh"

GHP_TOKEN = "ghp_abcdefghijklmnopqrstuvwx1234"
GITHUB_PAT_TOKEN = "github_pat_abcdefghijklmnopqrstuvwx1234"
PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
PEM_FOOTER = "-----END RSA PRIVATE KEY-----"
PEM_BODY = "MIIEpAIBAAKCAQEAsecretkeybodyherethatisverylongandsecret\nmorekeybodylines"
PEM_BLOCK = f"{PEM_HEADER}\n{PEM_BODY}\n{PEM_FOOTER}"
REDACTED = "[REDACTED-CREDENTIAL]"


def _posttooluse_input(tool_name: str, tool_response, session_id: str | None = None) -> dict:
    payload: dict = {"tool_name": tool_name, "tool_response": tool_response}
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


class TestRedactCredentialValues:
    # ------------------------------------------------------------------ #
    # Bash — docs-confirmed {"stdout","stderr","exit_code"} shape          #
    # ------------------------------------------------------------------ #

    def test_bash_stdout_token_redacted(self):
        response = {"stdout": f"token={GHP_TOKEN}", "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result is not None
        assert result["stdout"] == f"token={REDACTED}"
        assert result["stderr"] == ""
        assert result["exit_code"] == 0

    def test_bash_no_credential_present_leaves_stdout_untouched(self):
        response = {"stdout": "hello world", "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        # No match: the hook still emits updatedToolOutput (a well-formed
        # structural pass-through), unmodified.
        assert result == response

    def test_bash_pem_header_in_stderr_redacted(self):
        response = {"stdout": "", "stderr": f"leaked: {PEM_HEADER}", "exit_code": 1}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result is not None
        assert PEM_HEADER not in result["stderr"]
        assert REDACTED in result["stderr"]

    def test_full_pem_block_body_redacted_not_only_header(self):
        """Required regression test: the redacted output must not contain the
        key BODY, not merely have its header line replaced. A hook that only
        strips the header line (the original, unfixed behavior) would still
        pass a test asserting `PEM_HEADER not in result` while leaving the
        actual secret bytes verbatim in updatedToolOutput — this test asserts
        on the body specifically so that regression can't slip back in."""
        response = {"stdout": f"preamble\n{PEM_BLOCK}\ntrailer", "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result is not None
        assert PEM_BODY not in result["stdout"]
        assert PEM_FOOTER not in result["stdout"]
        assert REDACTED in result["stdout"]
        assert result["stdout"] == f"preamble\n{REDACTED}\ntrailer"

    def test_pem_header_only_fragment_still_redacted(self):
        """A truncated/partial PEM block (header present, no footer yet —
        e.g. output cut off mid-key) falls back to the header-only match
        rather than passing through entirely unredacted."""
        response = {"stdout": f"{PEM_HEADER}\n{PEM_BODY[:20]}...truncated", "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result is not None
        assert PEM_HEADER not in result["stdout"]
        assert REDACTED in result["stdout"]

    # ------------------------------------------------------------------ #
    # Documented residual — value shapes with no vendor-fixed format      #
    # ------------------------------------------------------------------ #

    def test_netrc_password_shape_not_redacted(self):
        """Required regression test pinning a documented residual: this
        hook's value-shape coverage is limited to formats with a
        vendor-fixed shape (a GitHub token prefix, a PEM block). A
        .netrc-style plaintext password has no such fixed shape to match
        against, so it passes through completely unredacted — the
        credential-path gates (deny-credential-bash-reads.sh,
        deny-credential-file-reads.sh), not this hook, are what stop a
        .netrc file's content from entering context in the first place.
        Pinned so this isn't mistaken for an oversight later."""
        response = {
            "stdout": "machine github.com login myuser password sup3rSecr3tPassw0rd123\n",
            "stderr": "",
            "exit_code": 0,
        }
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result == response

    # ------------------------------------------------------------------ #
    # Read — docs-confirmed {"file_path","file_contents"} shape            #
    # ------------------------------------------------------------------ #

    def test_read_file_contents_token_redacted(self):
        response = {"file_path": "/tmp/notes.txt", "file_contents": f"secret {GITHUB_PAT_TOKEN} end"}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Read", response))
        assert result is not None
        assert result["file_path"] == "/tmp/notes.txt"
        assert result["file_contents"] == f"secret {REDACTED} end"

    # ------------------------------------------------------------------ #
    # WebFetch/Grep/Task — assumed {"content": "..."} shape, unconfirmed   #
    # ------------------------------------------------------------------ #

    def test_webfetch_assumed_shape_token_redacted(self):
        response = {"content": f"page mentions {GHP_TOKEN} inline"}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("WebFetch", response))
        assert result is not None
        assert result["content"] == f"page mentions {REDACTED} inline"

    def test_grep_assumed_shape_token_redacted(self):
        response = {"content": f"match.py:3:{GHP_TOKEN}"}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Grep", response))
        assert result is not None
        assert GHP_TOKEN not in result["content"]
        assert REDACTED in result["content"]

    def test_task_assumed_shape_token_redacted(self):
        response = {"content": f"subagent returned {GITHUB_PAT_TOKEN}"}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Task", response))
        assert result is not None
        assert GITHUB_PAT_TOKEN not in result["content"]
        assert REDACTED in result["content"]

    # ------------------------------------------------------------------ #
    # Shape-agnostic walk — nested structures redact at every string leaf  #
    # ------------------------------------------------------------------ #

    def test_nested_structure_redacted_at_every_leaf(self):
        """The walk-based design's whole point: it never assumes a specific
        key exists, so a deeply nested or list-shaped tool_response — the
        kind an undocumented WebFetch/Grep/Task shape could plausibly be —
        is still redacted correctly."""
        response = {"outer": {"list": [f"a {GHP_TOKEN} b", "clean", {"inner": PEM_HEADER}]}}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Task", response))
        assert result is not None
        assert result["outer"]["list"][0] == f"a {REDACTED} b"
        assert result["outer"]["list"][1] == "clean"
        assert result["outer"]["list"][2]["inner"] == REDACTED

    # ------------------------------------------------------------------ #
    # Defense-in-depth: only the five matcher-scoped tool names act        #
    # ------------------------------------------------------------------ #

    def test_unscoped_tool_name_passes_through_untouched(self):
        response = {"file_path": "/x", "content": f"token {GHP_TOKEN}"}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Write", response))
        assert result is NO_UPDATED_OUTPUT

    # ------------------------------------------------------------------ #
    # Size cap — over _LIB_SIZE_THRESHOLD_BYTES passes through unscanned   #
    # ------------------------------------------------------------------ #

    def test_oversized_tool_response_passes_through_unscanned(self):
        oversized_stdout = ("x" * (6 * 1024 * 1024)) + GHP_TOKEN
        response = {"stdout": oversized_stdout, "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        # Documented known gap: content over the cap is not redacted. No
        # updatedToolOutput is emitted for it at all (fail-open passthrough).
        assert result is NO_UPDATED_OUTPUT

    # ------------------------------------------------------------------ #
    # Fail-safe — malformed input passes through unmodified, never crashes #
    # ------------------------------------------------------------------ #

    def test_not_valid_json_passes_through(self):
        result = subprocess.run(
            [str(REDACT_CREDENTIAL_VALUES_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_empty_stdin_passes_through(self):
        result = subprocess.run(
            [str(REDACT_CREDENTIAL_VALUES_HOOK)],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_tool_response_bare_number_passes_through_unmodified(self):
        """'Malformed' here mainly means 'not a JSON object/array' — a bare
        scalar tool_response. The walk-based design handles this uniformly
        (a number's type is neither "string" nor an aggregate, so `walk`
        returns it untouched) rather than needing a dedicated non-object
        code path."""
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", 5))
        assert result == 5

    def test_tool_response_null_passes_through(self):
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", None))
        assert result is NO_UPDATED_OUTPUT

    def test_tool_response_missing_passes_through(self):
        payload = {"tool_name": "Bash"}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, payload)
        assert result is NO_UPDATED_OUTPUT

    # ------------------------------------------------------------------ #
    # Additional value patterns — credential-value-patterns.md             #
    # ------------------------------------------------------------------ #

    def test_additions_file_pattern_redacted(self, isolated_home):
        additions_file = isolated_home / ".claude" / "credential-value-patterns.md"
        additions_file.write_text("Internal deploy token: dpl_[A-Za-z0-9]{10,}\n")
        response = {"stdout": "token dpl_abcdefghijklmno here", "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result is not None
        assert "dpl_abcdefghijklmno" not in result["stdout"]
        assert REDACTED in result["stdout"]

    def test_additions_file_absent_builtin_still_applies(self, isolated_home):
        response = {"stdout": f"token={GHP_TOKEN}", "stderr": "", "exit_code": 0}
        result = run_hook_updated_output(REDACT_CREDENTIAL_VALUES_HOOK, _posttooluse_input("Bash", response))
        assert result is not None
        assert result["stdout"] == f"token={REDACTED}"

    def test_malformed_addition_line_skipped_builtin_and_other_additions_unaffected(self, isolated_home):
        """Required regression test: one unparseable regex in the additions
        file must not invalidate the whole combined pattern. Before this
        fix, a single bad user regex made the entire jq gsub call fail,
        silently disabling built-in GitHub-token/PEM redaction too, with the
        only trace being the generic fail-open path. Pins that (a) the
        built-in pattern still fires, (b) a later, valid addition on its own
        line still fires, and (c) a diagnostic naming the file and line
        number is written to stderr rather than the failure being silent."""
        additions_file = isolated_home / ".claude" / "credential-value-patterns.md"
        additions_file.write_text("Bad line: [unterminated(\nInternal deploy token: dpl_[A-Za-z0-9]{10,}\n")
        response = {
            "stdout": f"a={GHP_TOKEN} b=dpl_abcdefghijklmno",
            "stderr": "",
            "exit_code": 0,
        }
        result = subprocess.run(
            [str(REDACT_CREDENTIAL_VALUES_HOOK)],
            input=json.dumps(_posttooluse_input("Bash", response)),
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(result.stdout)
        updated = payload["hookSpecificOutput"]["updatedToolOutput"]
        assert GHP_TOKEN not in updated["stdout"]
        assert "dpl_abcdefghijklmno" not in updated["stdout"]
        assert updated["stdout"] == f"a={REDACTED} b={REDACTED}"
        assert "credential-value-patterns.md line 1" in result.stderr
        assert "unaffected" in result.stderr
