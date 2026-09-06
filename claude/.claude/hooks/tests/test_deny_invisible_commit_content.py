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

import pytest
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
    # _mask_shell_quotes calls _lib_capped_for (defined in _lib.sh), so
    # _lib.sh must be sourced ahead of the extracted function body here too.
    lib_path = HOOKS_DIR / "_lib.sh"
    result = subprocess.run(
        ["bash", "-c", f'. "{lib_path}"; {match.group(0)}_mask_shell_quotes "$1"', "bash", text],
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

    def test_ansi_c_multi_char_escape_git_word_second_commit_allowed(self):
        """Documented residual (docs/security-hardening.md lines ~784-787):
        the ANSI-C multi-character escape `$'\\x67it'` (`\\x67` is `g`)
        contains a backslash, so `_mask_shell_quotes`'s single-safe-word
        exception regex (`^[A-Za-z0-9._/-]+$`) never matches it and the span
        stays blanked -- unlike the single-character-escape-free `$'git'`
        case above, which the masker's `$`-drop/unquote path does recognize.
        The blanked first fragment carries no visible `git` word, so arm 2
        never counts it toward the multi-commit total and this allows."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("$'\\x67it' commit -m x && git commit -m y"),
        ) == "allow"

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

    def test_multibyte_commit_message_mentioning_git_commit_as_text_allowed(self):
        """Non-ASCII content inside a quoted `-m` value must not desync
        `_mask_shell_quotes`'s per-character quote-state scan -- same allow
        outcome as the ASCII-equivalent
        test_commit_message_mentioning_git_commit_as_double_quoted_text_allowed."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "cafe -> \U0001F389 && git commit"'),
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
    # Wrapper/commit co-occurrence and quote-embedded decoy fragment       #
    # ------------------------------------------------------------------ #

    def test_leading_clean_commit_then_wrapped_add_and_commit_denied(self):
        """Confirmed bypass shape: a leading clean commit satisfies arm 1/
        arm 2's own single-commit count, and the real `git add secret.txt &&
        git commit -m y` runs invisibly inside `bash -c`. The wrapper/commit
        co-occurrence pre-check denies outright on the `bash -c` token
        alone, without needing to parse what runs inside it."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit --allow-empty -m "noop" && bash -c "git add secret.txt && git commit -m y"'),
        ) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param('sh -c "true" && git commit -m x', id="sh_dash_c"),
            pytest.param('zsh -c "true" && git commit -m x', id="zsh_dash_c"),
            pytest.param('ksh -c "true" && git commit -m x', id="ksh_dash_c"),
            pytest.param('dash -c "true" && git commit -m x', id="dash_dash_c"),
            pytest.param("eval true && git commit -m x", id="eval"),
            pytest.param("xargs true && git commit -m x", id="xargs"),
            pytest.param("source ./script.sh && git commit -m x", id="source"),
            pytest.param(". ./script.sh && git commit -m x", id="bare_dot_sourcing"),
            pytest.param("perl -e 'print 1' && git commit -m x", id="perl_dash_e"),
            pytest.param("python -c 'print(1)' && git commit -m x", id="python_dash_c"),
            pytest.param("python2 -c 'print 1' && git commit -m x", id="python2_dash_c"),
            pytest.param("python3 -c 'print(1)' && git commit -m x", id="python3_dash_c"),
            pytest.param("ruby -e 'puts 1' && git commit -m x", id="ruby_dash_e"),
            pytest.param("node -e 'console.log(1)' && git commit -m x", id="node_dash_e"),
        ],
    )
    def test_execution_wrapper_token_denied(self, command):
        """Each wrapper token in EXECUTION_WRAPPER_TOKEN_RE denies when it
        co-occurs with a git-commit-shaped fragment -- `bash -c` alone
        is already covered by test_bash_c_wrapped_chained_add_denied and
        test_leading_clean_commit_then_wrapped_add_and_commit_denied above."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input(command)) == "deny"

    def test_commit_message_mentioning_wrapper_token_as_text_denied(self):
        """Accepted over-deny cost of the wrapper/commit co-occurrence
        check: it does not parse quoting, so a plain, single commit whose
        own `-m` message merely mentions a wrapper token as ordinary text
        (documenting `xargs` usage here) still denies."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "document xargs usage in README"'),
        ) == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param('git commit -m "evaluate the new pricing model"', id="eval_substring_in_evaluate"),
            pytest.param('git commit -m "update resources and outsource docs"', id="source_substring_in_outsource"),
        ],
    )
    def test_wrapper_token_as_substring_of_unrelated_word_allowed(self, command):
        """The wrapper-token regex's word-boundary class correctly excludes
        a token embedded as a substring of an unrelated English word --
        "eval" inside "evaluate", "source" inside "resources" and
        "outsource" -- so these must not be mistaken for the bare `eval`/
        `source` tokens."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input(command)) == "allow"

    def test_hyphen_compound_word_denied_as_accepted_over_deny_cost(self):
        """Accepted over-deny cost documented in the header comment above:
        a hyphen-joined compound word ("re-source") reads as the bare word
        "source" under the wrapper-token regex's non-word-character
        boundary class, which treats a hyphen as a legitimate boundary."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "re-source the config"'),
        ) == "deny"

    def test_piped_xargs_commit_text_denied(self):
        """GH-783 Phase 2: the fast-reject's word-walk has no chain-position
        requirement -- it matches a bare `git` word followed, after any
        global git flags, by a `commit` word anywhere within a quote-
        stripped, operator-delimited fragment, not only at the start of a
        command or right after &&/;/|. Quote-stripping this printf argument
        exposes `git commit` as two separate words, so the fast-reject
        fires even though the text is piped into `xargs` as stdin data
        rather than chain-anchored, and the wrapper/commit co-occurrence
        check denies on the co-occurring `xargs` token. Contrast with
        test_perl_e_embedded_commit_text_allowed below, where the wrapper's
        own call syntax glues the quote to `git` with no word boundary."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('printf "%s" "git commit -m y" | xargs -0 sh -c'),
        ) == "deny"

    def test_quote_embedded_decoy_fragment_denied(self):
        """Confirmed bypass shape: quote-*stripping* (arm 1) turns the
        quoted literal text `"foo && git commit"` into a fake, syntactically
        real commit fragment that arm 1's ordered walk reaches first and
        stops at -- never inspecting the real `git add secret` mutation and
        the real trailing `git commit -m x`. Quote-*masking* correctly
        erases the decoy's content instead, so the ordered walk over masked
        fragments still reaches the real `git add secret` before the real
        commit and denies."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('echo "foo && git commit" && git add secret && git commit -m x'),
        ) == "deny"

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

    def test_dash_c_other_repo_commit_denied(self):
        """GH-783 Phase 2: `-C <other-repo>` no longer hides this commit from
        the hook. `_lib_extract_git_subcmd`'s global-flag skip list walks
        past `-C`'s value the same way it does for every git-fragment check
        in this file, so the worktree-target check above still reaches
        `-am`."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git -C /tmp/other-repo commit -am x"),
        ) == "deny"

    def test_dash_c_other_repo_commit_with_no_worktree_target_allowed(self):
        """Accepted, not an oversight: unlike test_dash_c_other_repo_commit_denied
        above, a `-C`-qualified commit with no `-a`/`--all`/pathspec still
        allows -- same as any commit with no worktree-target flag would. The
        fast-reject now correctly recognizes a `-C`-qualified commit instead
        of missing it outright, but recognizing it does not itself make a
        clean commit dangerous."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git -C /tmp/other-repo commit -m x"),
        ) == "allow"

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

    def test_dollar_paren_side_effect_execution_allowed(self):
        """Allowed (documented gap): a `$(...)` substitution inside a
        commit's own arguments executes before the commit and is not
        inspected -- here it genuinely runs `git add f` as a real side
        effect, not just mentions it as inert heredoc text (contrast with
        test_heredoc_message_mentioning_git_add_as_text_allowed above).
        Closing this would deny the standard heredoc-built-message idiom
        whenever the message text happens to mention a git command."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('git commit -m "$(git add f; echo x)"'),
        ) == "allow"

    def test_perl_e_embedded_commit_text_allowed(self):
        """Allowed (documented gap): unlike test_piped_xargs_commit_text_denied
        above, perl's own call syntax glues the opening quote directly
        against `system(` with no separating whitespace -- quote-stripping
        collapses `system('git` into the single word `system(git`, which
        never matches the fast-reject's bare `git` word-walk. The fragment
        is never recognized as invoking git at all, so the wrapper/commit
        co-occurrence check downstream of the fast-reject never runs
        either."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("perl -e \"system('git commit -m y')\""),
        ) == "allow"

    def test_python_c_embedded_commit_text_allowed(self):
        """Same glued-quote miss as test_perl_e_embedded_commit_text_allowed
        above, for `python -c`: quote-stripping collapses `os.system('git`
        into the single word `os.system(git`, which never matches the
        fast-reject's bare `git` word-walk."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("python -c \"import os; os.system('git commit -m y')\""),
        ) == "allow"

    def test_ruby_e_embedded_commit_text_allowed(self):
        """Same glued-quote miss as test_perl_e_embedded_commit_text_allowed
        above, for `ruby -e`: quote-stripping collapses `system('git` into
        the single word `system(git`, which never matches the fast-reject's
        bare `git` word-walk."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("ruby -e \"system('git commit -m y')\""),
        ) == "allow"

    def test_node_e_embedded_commit_text_allowed(self):
        """Same glued-quote miss as test_perl_e_embedded_commit_text_allowed
        above, for `node -e`: quote-stripping collapses `execSync('git`
        into the single word `execSync(git`, which never matches the
        fast-reject's bare `git` word-walk."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("node -e \"require('child_process').execSync('git commit -m y')\""),
        ) == "allow"

    def test_ifs_parameter_expansion_wrapper_bypass_allowed(self):
        """Allowed (documented gap): an unquoted `${IFS}` parameter
        expansion standing in for whitespace defeats both the wrapper-token
        regex and `_lib_fragment_invokes_git`'s word-split, since neither
        performs real shell tokenization. Quote-stripping this command
        yields the single glued word `bash${IFS}-c${IFS}git`, which none of
        the fast-reject, the wrapper/commit co-occurrence check, arm 1, or
        arm 2 recognizes as invoking `bash`/`git` -- even though the
        wrapped `git add secret && git commit -m y` actually runs at
        execution time."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('bash${IFS}-c${IFS}"git add secret && git commit -m y"'),
        ) == "allow"

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

    def test_quote_embedded_decoy_fragment_with_read_only_real_mutation_allowed(self):
        """Allow counterpart to test_quote_embedded_decoy_fragment_denied:
        same quote-embedded decoy shape, but the real chained subcommand is
        read-only (`git status`), so neither arm 1's fake-fragment walk nor
        arm 2's masked-fragment walk has anything to deny."""
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input('echo "foo && git commit" && git status && git commit -m x'),
        ) == "allow"

    # ------------------------------------------------------------------ #
    # Missing-binary fork points — each must fail closed (deny), not      #
    # silently allow an unscanned git commit                              #
    # ------------------------------------------------------------------ #

    def test_grep_absent_from_path_denied(self, tmp_path):
        """GH-783 Phase 2 swapped the fast-reject's own grep-based match for
        _lib_command_invokes_git_subcmd (sed/tr and a bash word-walk, no
        grep), but the wrapper/commit co-occurrence pre-check downstream of
        the fast-reject still runs its own `grep -qE` over the raw command
        text -- grep remains a real dependency of this hook via that
        pre-check, so a plain, otherwise-allowed `git commit -m x` still
        fails closed (denies) with grep absent from PATH."""
        farm_dir = tmp_path / "path-without-grep"
        farm_dir.mkdir()
        restricted_path = build_path_without("grep", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("git commit -m x"),
            extra_env={"PATH": restricted_path},
        ) == "deny"

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

    def test_non_commit_command_denied_when_sed_absent_from_path(self, tmp_path):
        """Pins the blast-radius widening documented in the header comment:
        COMMAND_UNQUOTED's quote-strip forks ahead of the fast-reject, so a
        missing sed denies every Bash call -- not just ones mentioning `git
        commit` -- unlike the pre-hoist behavior where a non-commit-shaped
        call like this one would never have reached a sed fork at all."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        assert run_hook(
            DENY_INVISIBLE_COMMIT_CONTENT_HOOK,
            bash_input("echo hi"),
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

    def test_wrapper_token_with_no_commit_fragment_allowed(self):
        """Pins the fast-reject's ordering guarantee: a wrapper token
        (`bash -c`) is present in the raw command text, but no git-commit-
        shaped fragment appears anywhere, so the fast-reject exits before
        the wrapper/commit co-occurrence check ever runs. A future
        reordering that moved the co-occurrence check ahead of the
        fast-reject would start denying every wrapper-token-containing
        command, including this one."""
        assert run_hook(DENY_INVISIBLE_COMMIT_CONTENT_HOOK, bash_input('bash -c "echo hello"')) == "allow"

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
