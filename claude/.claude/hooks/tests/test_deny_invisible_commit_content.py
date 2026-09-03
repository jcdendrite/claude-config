"""Tests for deny-invisible-commit-content.sh.

This hook is purely text-based — it never shells out to `git` itself
(unlike deny-pii-in-commits.sh, which needs `git diff` for its scan) — so
these tests call `run_hook` with no `cwd`/`isolated_home`, matching
test_deny_reviewer_tree_mutation.py's convention for its own textual
git-subcommand tests.
"""
from __future__ import annotations

import json
import re
import subprocess

from helpers import HOOKS_DIR, bash_input, build_path_without, read_input, run_hook, run_hook_reason

DENY_INVISIBLE_COMMIT_CONTENT_HOOK = HOOKS_DIR / "deny-invisible-commit-content.sh"

# Matches _mask_shell_quotes's own definition, opening brace through the
# closing brace at column 0 — used to invoke it in isolation, since running
# the full hook blocks on stdin JSON and never exposes this internal
# function's raw output.
_MASK_SHELL_QUOTES_FUNCTION_RE = re.compile(r"_mask_shell_quotes\(\) \{.*?\n\}\n", re.DOTALL)


def _call_mask_shell_quotes(text: str) -> str:
    source = DENY_INVISIBLE_COMMIT_CONTENT_HOOK.read_text()
    match = _MASK_SHELL_QUOTES_FUNCTION_RE.search(source)
    assert match is not None, "could not locate _mask_shell_quotes's definition in the hook source"
    result = subprocess.run(
        ["bash", "-c", f'{match.group(0)}_mask_shell_quotes "$1"', "bash", text],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class TestDenyInvisibleCommitContent:
    # ------------------------------------------------------------------ #
    # Chained index mutation ahead of the commit fragment                 #
    # ------------------------------------------------------------------ #

    def test_chained_add_then_commit_denied(self):
        """`git add` staged after this hook's snapshot is invisible to the
        commit gates that read `git diff --cached` — the core TOCTOU bypass
        this hook closes."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git add f && git commit -m x")) == "deny"

    def test_semicolon_chained_add_then_commit_denied(self):
        """`;` separator, not just `&&`, must also be walked."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git add f ; git commit -m x")) == "deny"

    def test_quoted_git_word_chained_add_then_commit_denied(self):
        """GH-783: this is test_chained_add_then_commit_denied's own
        fixture with two quote characters added around the second `git`.
        Regression guard: the fast-reject grep must run against
        COMMAND_UNQUOTED so a quoted `git commit` in a chain is still
        caught."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input('git add f && "git" commit -m x')) == "deny"

    def test_bash_c_wrapped_chained_add_denied(self):
        """Required regression test: the raw command's first fragment word is
        `"git` (glued to the wrapping quote), which `_lib_fragment_invokes_git`
        would miss as a bare-word match. Quote-stripping before the fragment
        walk is what recognizes it."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('bash -c "git add f && git commit -m x"'),
        ) == "deny"

    def test_wrapped_dirty_commit_then_clean_direct_commit_denied(self):
        """Adversarial case for the arm 1/arm 2 fragment substitution: arm 2
        would capture the second, direct, `-a`-free commit as
        `DIRECT_MASKED_COMMIT_FRAGMENT`, but arm 1 stops at the first
        (wrapped, `-a`-carrying) commit fragment. Substituting arm 2's
        clean second invocation in for the first would wrongly turn this
        into an allow — the "direct invocation" guard must keep this
        fragment on its own quote-stripped text instead."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('bash -c "git commit -a -m x" && git commit -m y'),
        ) == "deny"

    def test_marker_chain_with_worktree_target_still_denied(self):
        """A sanctioned marker.sh chain does not exempt the commit fragment
        itself from the worktree-target check — `-am` here denies regardless
        of the chain."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("marker.sh write code-review && git commit -am x"),
        ) == "deny"

    def test_chained_add_then_amend_denied_regardless_of_amend(self):
        """The chained-add arm fires on the preceding fragment, independent of
        any flag on the commit fragment itself — including --amend."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git add f && git commit --amend --no-edit"),
        ) == "deny"

    def test_chained_mutation_deny_message_names_remedy(self):
        reason = run_hook_reason(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git add f && git commit -m x"))
        assert reason is not None
        assert "own Bash tool call" in reason
        assert "second" in reason

    # ------------------------------------------------------------------ #
    # More than one git-commit-invoking fragment in the same chain        #
    # ------------------------------------------------------------------ #

    def test_two_chained_commits_denied(self):
        """The core multi-commit bypass: arm 1 alone stops at the first
        commit fragment and would allow this — the second commit's
        `-a --amend` runs against a `git diff --cached` snapshot no gate
        ever re-checked. This second, independent count over the whole
        chain is what closes it."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x && git commit -a --amend --no-edit"),
        ) == "deny"

    def test_mutation_between_two_commits_denied(self):
        """A `git add` between two commit fragments is still denied even
        though it's not chained directly ahead of either commit — arm 1's
        ordered walk alone cannot see past the first commit fragment to
        find it."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x && git add secret && git commit --amend --no-edit"),
        ) == "deny"

    def test_two_commits_second_with_pathspec_denied(self):
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x && git commit -m y -- file.txt"),
        ) == "deny"

    def test_three_chained_commits_denied(self):
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x && git commit -m y && git commit -m z"),
        ) == "deny"

    def test_quoted_git_word_two_chained_commits_denied(self):
        """GH-783: a quoted `git` word on the first commit must still count
        toward arm 2's total. This input passes the fast-reject grep as-is
        (a bare, unquoted `git commit` is present in the second fragment)
        but would fail without the masker's single-safe-word unquoting:
        the masked first fragment's `git` word would stay blanked and the
        count would never reach 2. Isolates the masker fix from the
        fast-reject fix the test above proves."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('"git" commit -m a && git commit -m b'),
        ) == "deny"

    def test_ansi_c_quoted_git_word_two_chained_commits_denied(self):
        """GH-783: the ANSI-C-quote form of the git word (`$'git'`) must
        normalize the same way `_lib_strip_shell_quotes` already
        normalizes it for arm 1 (dropping the leading `$` along with the
        quote delimiters) — proving the masker's own `$`-drop logic
        actually fires, not just the unrelated stripper it's modeled on."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("$'git' commit -m x && git commit -m y"),
        ) == "deny"

    def test_mask_shell_quotes_ansi_c_multi_word_span_has_no_stray_dollar(self):
        """A multi-word ANSI-C-quoted span ($'fix && bar') falls into the
        blanking branch (its interior isn't a single safe word), which must
        trim the leading `$` the same way the single-safe-word unquoting
        branch already does — otherwise the blanked output is `$''`
        instead of `''`, an internal inconsistency with the masker's own
        documented "drops the leading $" rule."""
        assert _call_mask_shell_quotes("$'fix && bar'") == "''"

    def test_multi_commit_deny_message_names_invariant(self):
        reason = run_hook_reason(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x && git commit -a --amend --no-edit"),
        )
        assert reason is not None
        assert "own, separate Bash tool call" in reason
        assert "2" in reason

    def test_cross_quote_type_parity_denied(self):
        """A literal `"` embedded inside a real, valid single-quoted
        argument must stay masked as ordinary content, never paired across
        to an unrelated `"` inside a second single-quoted argument later in
        the chain. Two independent, quote-nesting-unaware sed passes (one
        per quote type) would delete the real `&&` and the entire second
        `git commit` invocation here; the single-pass, quote-state-tracking
        scan must not."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m 'x\"y' && git commit --amend --no-edit -m 'p\"q'"),
        ) == "deny"

    def test_unpaired_quote_leans_toward_deny(self):
        """An unpaired quote finds no closing match for the masking regex
        and is left unmasked, so the fragment count leans toward denying
        rather than silently allowing a second, hidden commit fragment."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "hello && git commit -a --amend'),
        ) == "deny"

    def test_commit_message_mentioning_git_commit_as_double_quoted_text_allowed(self):
        """The multi-commit count runs over quote-*masked* text specifically
        so a commit message that merely mentions "git commit" as literal
        text is not miscounted as a second invocation — the opposite
        normalization from arm 1's quote-stripping."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "fix && git commit"'),
        ) == "allow"

    def test_commit_message_mentioning_git_commit_as_single_quoted_text_allowed(self):
        """Same masking property as the double-quoted case above, for the
        other quote kind."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m 'fix && git commit'"),
        ) == "allow"

    def test_heredoc_message_mentioning_git_add_as_text_allowed(self):
        """Pins the false-positive cost of closing the header's documented
        `$(...)`-side-effect known gap: a `$(...)` substitution inside a
        commit's own arguments executes before the commit and is not
        inspected, because inspecting it would deny this exact standard
        heredoc-built-message idiom whenever the message text happens to
        mention a git command. Here "git add" is inert message text — the
        heredoc body is never executed as a command — and must stay
        allowed."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "$(cat <<\'EOF\'\ngit add\nEOF\n)"'),
        ) == "allow"

    def test_heredoc_message_mentioning_git_commit_as_text_allowed(self):
        """Same construction as the "git add" case above, but with "commit"
        as the mentioned word so this fixture actually exercises arm 2's
        masking fix — "git add" never trips arm 2's commit-count regardless
        of masking correctness, since "add" isn't the commit subcommand.
        The multi-line heredoc body, embedded inside a `$(...)` substitution
        that is itself inside the `-m` argument's double quotes, is exactly
        the shape `_mask_shell_quotes`'s single-pass scan must mask as one
        contiguous span rather than splitting at the embedded newline."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "$(cat <<\'EOF\'\ngit commit\nEOF\n)"'),
        ) == "allow"

    # ------------------------------------------------------------------ #
    # Commit forms that record working-tree content outside the index     #
    # ------------------------------------------------------------------ #

    def test_commit_all_short_flag_denied(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit -am x")) == "deny"

    def test_commit_all_multiword_message_denied(self):
        """A real `-a` bundled with a multi-word message must still deny —
        the arm 1/arm 2 fragment substitution that fixes the multi-word
        false-deny below must not accidentally swallow a genuine `-a`."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -am "fix a real bug"'),
        ) == "deny"

    def test_commit_all_unbundled_short_flag_denied(self):
        """`-a` as its own token, not bundled into `-am` above."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit -a -m x")) == "deny"

    def test_quoted_git_word_with_all_flag_denied(self):
        """GH-783: a single, unchained, quoted-`git` commit using `-a`
        proves arm 1's whole path denies once the fast-reject hoist onto
        COMMAND_UNQUOTED lets it through. Does not by itself isolate the
        masker fix's contribution — arm 1's commit_check_fragment defaults
        to the already quote-stripped `$fragment` regardless of the
        masker, so `-a` is visible either way; the allow test below (a
        multiword message, no `-a`) is what actually isolates the masker
        fix."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('"git" commit -a -m "fix the thing"'),
        ) == "deny"

    def test_commit_all_long_flag_denied(self):
        """The actual `--all` long-form spelling, distinct from the two `-a`
        short-flag shapes above — previously covered in this file's own
        suite only by proxy via a different hook's test of the shared
        `_lib_commit_fragment_has_worktree_target` helper."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit --all -m x")) == "deny"

    def test_pathspec_separator_denied(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit -m x -- file.txt")) == "deny"

    def test_bare_pathspec_argument_denied(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit -m x file.txt")) == "deny"

    def test_all_flag_amend_denied_regardless_of_amend(self):
        """The worktree-target arm is --amend-agnostic: -a still denies even
        combined with --amend, distinct from the bare-amend allow below."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -a --amend --no-edit"),
        ) == "deny"

    def test_worktree_target_deny_message_names_remedy(self):
        reason = run_hook_reason(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit -am x"))
        assert reason is not None
        assert "stage the changes explicitly" in reason.lower()
        assert "no -a/--all and no pathspec" in reason

    # ------------------------------------------------------------------ #
    # Allowed forms                                                       #
    # ------------------------------------------------------------------ #

    def test_plain_commit_allowed(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit -m x")) == "allow"

    def test_multiword_message_allowed(self):
        """Regression test for a confirmed false deny: arm 1's quote-
        stripped fragment turns a multi-word `-m` value into several
        separate words, and `_lib_commit_fragment_has_worktree_target`'s
        `xargs -n1` tokenizer only consumes one of them as `-m`'s value —
        the rest look like bare trailing pathspec arguments. Must allow."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "fix a real bug here"'),
        ) == "allow"

    def test_quoted_git_word_multiword_message_allowed(self):
        """The masker-fix isolation case: no -a/pathspec, multi-word -m
        message — if the masker leaves git/commit blanked, arm 1's
        worktree-target check falls back to the raw fragment and
        false-denies on the multi-word message (same bug class as
        test_multiword_message_allowed)."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('"git" commit -m "fix a real bug here"'),
        ) == "allow"

    def test_multiword_message_after_marker_chain_allowed(self):
        """Same false-deny fixture as above, chained after the sanctioned
        marker.sh write prefix — the fix must hold for the chained shape
        too, not just the bare single-command case."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('~/.claude/scripts/marker.sh write code-review && git commit -m "fix a real bug here"'),
        ) == "allow"

    def test_embedded_literal_newline_in_message_allowed(self):
        """A real embedded newline inside the `-m` value (not the two-
        character `\\n` escape) must not desync arm 1's fragment walk from
        arm 2's masked one: `_mask_shell_quotes` collapses the entire
        quoted span, newline included, before either arm splits on shell
        operators."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "Subject\n\nBody text."'),
        ) == "allow"

    def test_bare_amend_allowed(self):
        """Out of scope, deliberately: a bare `--amend` with no `-a` and no
        chained mutation is not a TOCTOU race on the empty-diff carve-out."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git commit --amend --no-edit")) == "allow"

    def test_readonly_subcommand_chained_before_commit_allowed(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git status && git commit -m x")) == "allow"

    def test_fetch_chained_before_commit_allowed(self):
        """`fetch` only updates remote-tracking refs, not the working tree or
        index — read-only under this hook's threat model."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git fetch && git commit -m x")) == "allow"

    def test_diff_cached_chained_before_commit_allowed(self):
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git diff --cached && git commit -m x"),
        ) == "allow"

    def test_sanctioned_marker_chain_allowed(self):
        """The sanctioned `marker.sh write <skill> && git commit` chain
        survives untouched by construction: no non-marker.sh git fragment,
        no worktree target on the commit fragment."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("~/.claude/scripts/marker.sh write code-review && git commit -m x"),
        ) == "allow"

    def test_trailing_mutation_in_commit_message_allowed(self):
        """A quote-stripped commit message containing `&&` can synthesize a
        trailing `git add` fragment — the ordering guard stops the walk at
        the first commit fragment, so this must not deny."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "fix && git add"'),
        ) == "allow"

    # ------------------------------------------------------------------ #
    # Missing-binary fork points — each must fail closed (deny), not      #
    # silently allow an unscanned git commit                              #
    # ------------------------------------------------------------------ #

    def test_grep_absent_from_path_does_not_affect_the_gate(self, tmp_path):
        """The fast-reject matches via _lib_command_invokes_git_subcmd
        (sed/tr and a bash word-walk); it does not use grep. A plain,
        otherwise-allowed `git commit -m x` (nothing that trips arm 1 or
        arm 2) still allows with grep absent from PATH. This confirms the
        gate's fail posture doesn't depend on grep being present on PATH.
        sed/tr remain a real dependency, since the fast-reject's own
        internal quote-strip needs them."""
        farm_dir = tmp_path / "path-without-grep"
        farm_dir.mkdir()
        restricted_path = build_path_without("grep", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x"),
            extra_env={"PATH": restricted_path},
        ) == "allow"

    def test_awk_absent_from_path_denied(self, tmp_path):
        """`_mask_shell_quotes`'s single-pass scan is the earliest awk fork
        this hook reaches, ahead of `_lib_commit_fragment_has_worktree_
        target`'s own awk use."""
        farm_dir = tmp_path / "path-without-awk"
        farm_dir.mkdir()
        restricted_path = build_path_without("awk", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x"),
            extra_env={"PATH": restricted_path},
        ) == "deny"

    def test_sed_absent_from_path_denied(self, tmp_path):
        """`_lib_strip_shell_quotes` computing COMMAND_UNQUOTED is the
        earliest sed fork this hook reaches — the fast-reject's own
        internal quote-strip (inside _lib_command_invokes_git_subcmd) also
        depends on sed, but COMMAND_UNQUOTED forks first — so a missing sed
        denies every Bash call, not only ones mentioning `git commit`."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x"),
            extra_env={"PATH": restricted_path},
        ) == "deny"

    def test_tr_absent_from_path_denied(self, tmp_path):
        """tr backs `_lib_strip_shell_quotes`, called first for
        COMMAND_UNQUOTED and again inside the fast-reject's own
        _lib_command_invokes_git_subcmd — so a missing tr denies every
        Bash call, not only ones mentioning `git commit`."""
        farm_dir = tmp_path / "path-without-tr"
        farm_dir.mkdir()
        restricted_path = build_path_without("tr", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x"),
            extra_env={"PATH": restricted_path},
        ) == "deny"

    def test_xargs_absent_from_path_denied(self, tmp_path):
        """xargs backs only `_lib_commit_fragment_has_worktree_target`'s
        tokenizing. That helper treats a missing xargs as "target found"
        (the safe direction for both its callers), so an otherwise-clean
        single commit denies instead of allowing through an unscanned
        worktree-target check."""
        farm_dir = tmp_path / "path-without-xargs"
        farm_dir.mkdir()
        restricted_path = build_path_without("xargs", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x"),
            extra_env={"PATH": restricted_path},
        ) == "deny"

    # ------------------------------------------------------------------ #
    # Dispatch — non-commit and non-Bash pass through                     #
    # ------------------------------------------------------------------ #

    def test_non_commit_bash_passthrough(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input("git status")) == "allow"

    def test_non_bash_tool_passthrough(self):
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, read_input("/tmp/f.txt")) == "allow"

    def test_non_string_command_field_handled(self):
        """A non-string `command` field is a malformed Bash tool call the
        Bash tool itself cannot execute — no git commit to gate, and the
        hook produces a clean allow rather than erroring under `set -u`."""
        payload = {"tool_name": "Bash", "tool_input": {"command": {"unexpected": "object"}}}
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, payload) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_INVISIBLE_COMMIT_CONTENT_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Assert the fail-closed path specifically fired — not some other deny.
        assert "could not parse" in payload["hookSpecificOutput"]["permissionDecisionReason"]
