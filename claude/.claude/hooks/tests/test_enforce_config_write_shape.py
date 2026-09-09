"""Tests for enforce-config-write-shape.sh.

Covers this hook's own unique deny logic — the Write/Edit/MultiEdit path
match, the Bash name-scan for _config_set, the Bash redirect/utility-write
scan, and each arm's resolution-failure behavior. The shared _lib_shape_match/
_lib_redirect_candidates engine's own nocasematch/`-ef`-inode-identity/
config-dir-aware edge cases are regression-tested both directly in
test_lib.py and black-box in test_enforce_marker_script_shape.py — this file
does not re-prove that engine from scratch.
"""
from __future__ import annotations

import os
import shutil
import textwrap

import pytest
from helpers import HOOKS_DIR, bash_input, edit_input, multiedit_input, run_hook, run_hook_reason, write_input

ENFORCE_CONFIG_WRITE_SHAPE_HOOK = HOOKS_DIR / "enforce-config-write-shape.sh"


class TestWriteEditMultiEditArm:
    @pytest.mark.parametrize("tool_input_builder", [write_input, edit_input, multiedit_input])
    def test_every_file_write_tool_is_covered(self, tmp_path, tool_input_builder):
        """Write, Edit, and MultiEdit all reach the same state, so all three are gated."""
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                tool_input_builder(str(home / ".claude" / "claude-config.toml")),
                home=home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("tool_input_builder", [write_input, edit_input, multiedit_input])
    def test_write_to_unrelated_file_allowed(self, tmp_path, tool_input_builder):
        """Mirrors test_every_file_write_tool_is_covered's parametrization --
        the allow path is tool-type-agnostic the same way the deny path is,
        so an Edit/MultiEdit-specific allow-path regression is caught here
        too, not just Write's."""
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                tool_input_builder(str(home / ".claude" / "some-other-file.md")),
                home=home,
            )
            == "allow"
        )

    def test_write_denied_under_config_dir_with_no_dotclaude_segment(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                write_input(str(config_dir / "claude-config.toml")),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_write_denied_when_config_dir_unresolvable(self, tmp_path):
        """CLAUDE_CONFIG_DIR set to a relative value makes _lib_config_dir()
        fail — the arm must deny rather than silently allow, matching
        enforce-marker-script-shape.sh's identical fail-closed posture on
        the same resolver failure."""
        home = tmp_path / "home"
        home.mkdir()
        target = tmp_path / "somewhere" / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                write_input(str(target)),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": "relative-profile"},
            )
            == "deny"
        )

    def test_deny_reason_names_the_state_file(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        reason = run_hook_reason(
            ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
            write_input(str(home / ".claude" / "claude-config.toml")),
            home=home,
        )
        assert reason is not None
        assert "claude-config.toml" in reason


class TestBashNameScanArm:
    def test_command_invoking_config_set_denied(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input("source ~/.claude/hooks/_config.sh && _config_set autonomous_shipping true"),
                home=home,
            )
            == "deny"
        )

    def test_command_mentioning_config_set_as_a_quoted_word_denied(self, tmp_path):
        """The name-scan runs against the quote-stripped command text, so a
        quote-split evasion of the literal token still matches."""
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input('"_conf""ig_set" worktree_required true'),
                home=home,
            )
            == "deny"
        )

    def test_unrelated_command_allowed(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert run_hook(ENFORCE_CONFIG_WRITE_SHAPE_HOOK, bash_input("git status"), home=home) == "allow"

    def test_grep_searching_for_config_set_is_also_denied(self, tmp_path):
        """The name-scan is a bare substring test, not a positional-argument-
        grammar parser (this file's header/the hook's own header explains
        why) -- so a plain `grep` search for the literal `_config_set`
        substring is denied too, even though it invokes nothing. Deliberate
        over-blocking, documented here as the current, accepted trade-off
        rather than a bug to fix."""
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input("grep -rn '_config_set' claude/.claude/hooks/_config.sh"),
                home=home,
            )
            == "deny"
        )


class TestSanctionedCallerAllowPaths:
    """The two sanctioned writers (docs/config-file.md's "Writing a value")
    invoke `_config_set` from inside a sourced/executed script, never as a
    literal top-level Bash command token -- neither name-scan nor
    redirect-scan arm has anything to match in their own invocation text."""

    def test_install_sh_invocation_allowed(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert run_hook(ENFORCE_CONFIG_WRITE_SHAPE_HOOK, bash_input("./install.sh"), home=home) == "allow"

    def test_migrate_legacy_config_sh_invocation_allowed(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input("bash claude/.claude/scripts/migrate-legacy-config.sh"),
                home=home,
            )
            == "allow"
        )


class TestBashRedirectScanArm:
    def test_raw_append_to_state_file_denied(self, tmp_path):
        """Closes round 1's gap: a plain redirect reaching the state file
        with no _config_set substring anywhere in the command."""
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"printf 'autonomous_shipping = true\\n' >> {target}"),
                home=home,
            )
            == "deny"
        )

    def test_tee_to_state_file_denied(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"echo 'worktree_required = true' | tee {target}"),
                home=home,
            )
            == "deny"
        )

    def test_case_varied_write_utility_name_to_state_file_denied(self, tmp_path):
        """Case-folded utility name: on a case-insensitive-but-case-preserving
        filesystem (macOS APFS/HFS+, Windows NTFS), TEE opens the same `tee`
        binary a case-sensitive utility-name match would miss -- a
        case-sensitive match here would skip the redirect-target scan
        entirely rather than just failing to recognize this one utility."""
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"echo 'worktree_required = true' | TEE {target}"),
                home=home,
            )
            == "deny"
        )

    def test_cp_trailing_flag_after_target_denied(self, tmp_path):
        """A trailing flag after the true destination (`cp SRC DEST -v`)
        must still deny -- cp's own argument parser accepts a flag in this
        position and still performs the write against DEST, confirmed
        empirically against a real cp binary."""
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"cp /tmp/payload.toml {target} -v"),
                home=home,
            )
            == "deny"
        )

    def test_redirect_to_unrelated_file_allowed(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "some-other-file.md"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"echo hello >> {target}"),
                home=home,
            )
            == "allow"
        )

    def test_command_with_no_dotclaude_mention_at_all_allowed(self, tmp_path):
        """A command that never mentions '.claude' and targets a genuinely
        unrelated file is allowed -- the redirect scan runs unconditionally
        (no command-level pre-filter), so this is a plain non-match, not a
        skipped scan."""
        home = tmp_path / "home"
        home.mkdir()
        assert run_hook(ENFORCE_CONFIG_WRITE_SHAPE_HOOK, bash_input("echo hello >> /tmp/x.md"), home=home) == "allow"

    def test_redirect_denied_under_config_dir_with_no_dotclaude_segment(self, tmp_path):
        """Closes the command-level `.claude`-substring fast-reject bypass:
        a plain redirect append to claude-config.toml under a
        CLAUDE_CONFIG_DIR value that contains no `.claude` substring
        anywhere in the path must still be denied."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile"
        config_dir.mkdir()
        target = config_dir / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"printf 'worktree_required = false\\n' >> {target}"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_redirect_through_symlink_with_no_dotclaude_in_its_own_path_denied(self, tmp_path, monkeypatch):
        """Closes the per-candidate `.claude`-substring fast-reject bypass: a
        symlink whose own path text carries no `.claude` segment, but
        resolves (via `-ef` inode identity) into the real claude-config.toml,
        must still be denied. The real state file must already exist for
        this: unlike the prior realpath-based design, `-ef` requires both
        sides to stat successfully, so a dangling symlink to a not-yet-
        created state file is not (and cannot be) covered by this check —
        see enforce-config-write-shape.sh's own header for that residual.

        This test's `-ef` match depends on the config root resolving to
        this test's own $HOME/.claude, so an ambient CLAUDE_CONFIG_DIR
        must be cleared or it resolves elsewhere and the assertion fails —
        matching isolated_home's convention (conftest.py)."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude" / "claude-config.toml").touch()
        alias_dir = tmp_path / "aliasdir"
        alias_dir.mkdir()
        symlinked_path = alias_dir / "notclaudepath"
        symlinked_path.symlink_to(home / ".claude" / "claude-config.toml")
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"printf 'worktree_required = false\\n' >> {symlinked_path}"),
                home=home,
            )
            == "deny"
        )

    def test_redirect_denied_when_config_dir_unresolvable(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input("echo hi >> ~/.claude/claude-config.toml"),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": "relative-profile"},
            )
            == "deny"
        )

    def test_literal_claude_config_dir_variable_reference_denied(self, tmp_path):
        """A literal, unexpanded `$CLAUDE_CONFIG_DIR` reference in the
        command text (PreToolUse hands the hook raw, unexecuted command
        text, never shell-expanded) must still resolve and deny -- item 4's
        fork-free $HOME/$CLAUDE_CONFIG_DIR expansion in _lib_shape_match."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "profile-container"
        config_dir.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(
                    "printf 'worktree_required = false\\n' >> "
                    "$CLAUDE_CONFIG_DIR/claude-config.toml"
                ),
                home=home,
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "deny"
        )

    def test_hardlink_to_real_state_file_denied(self, tmp_path, monkeypatch):
        """A hardlink to the real, existing claude-config.toml denies even
        though its own path is unrelated to the config root -- Pass 3's bare
        candidate-to-real-target `-ef` comparison, a genuine new capability
        over the prior realpath-based design (which normalized paths, not
        inode identity, and so caught this shape differently).

        CLAUDE_CONFIG_DIR is cleared for the same reason as the symlink
        test above: this `-ef` match depends on the config root resolving
        to this test's own $HOME/.claude."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        real_state_file = home / ".claude" / "claude-config.toml"
        real_state_file.write_text("worktree_required = true\n")
        unrelated_dir = tmp_path / "unrelated"
        unrelated_dir.mkdir()
        hardlinked_path = unrelated_dir / "not-named-claude-config"
        os.link(real_state_file, hardlinked_path)
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"printf 'worktree_required = false\\n' >> {hardlinked_path}"),
                home=home,
            )
            == "deny"
        )

    def test_symlink_indirected_write_denied_as_the_11th_candidate(self, tmp_path, monkeypatch):
        """GH bypass repro: the prior realpath-budget design shape-tested
        only the raw tilde-expanded form (no realpath) past the first
        CONFIG_WRITE_REALPATH_BUDGET=10 candidates in one command -- a
        documented, narrow degrade an independent security review
        reproduced as a live bypass by padding a `tee` invocation with 10
        filler targets ahead of a symlink-indirected write to
        claude-config.toml as the 11th. The `-ef`-based redesign has no
        such budget (each candidate's cost is a handful of in-process stat
        calls, not a capped subprocess), so this must still deny.

        CLAUDE_CONFIG_DIR is cleared for the same reason as the symlink
        test above: this `-ef` match depends on the config root resolving
        to this test's own $HOME/.claude."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        real_state_file = home / ".claude" / "claude-config.toml"
        real_state_file.write_text("worktree_required = true\n")
        alias_dir = tmp_path / "aliasdir"
        alias_dir.mkdir()
        symlinked_path = alias_dir / "notclaudepath"
        symlinked_path.symlink_to(real_state_file)
        padding = " ".join(f"/tmp/config-shape-pad-{i}" for i in range(10))
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"tee {padding} {symlinked_path}"),
                home=home,
            )
            == "deny"
        )

    def test_no_write_construct_command_skips_fragment_splitting(self, tmp_path):
        """git status has no >-family redirect and invokes none of
        _LIB_WRITE_UTILITIES, so _lib_command_has_write_construct's
        fast-reject must skip _lib_split_fragments entirely -- not just
        happen to allow, which a command that merely fails to match
        claude-config.toml would also produce. Reuses the
        PATH-shim-records-invocation technique from
        test_require_plan_review.py's
        test_historical_plans_allows_and_skips_the_realpath_fast_path: the
        shim records only a sed invocation whose pattern contains `&&`, the
        substring unique to _lib_split_fragments's own first internal sed
        call, distinct from every other sed call this hook makes (the
        quote-strip's `-e`-flagged calls)."""
        home = tmp_path / "home"
        home.mkdir()
        real_sed = shutil.which("sed")
        assert real_sed, "test host must have a real sed binary on PATH"

        call_marker = tmp_path / "split-fragments-was-called"
        shim_dir = tmp_path / "sed-records-fragment-split-calls"
        shim_dir.mkdir()
        shim_script = textwrap.dedent(f"""\
            #!/bin/bash
            case "$2" in
              *'&&'*) touch "{call_marker}" ;;
            esac
            exec "{real_sed}" "$@"
        """)
        (shim_dir / "sed").write_text(shim_script)
        (shim_dir / "sed").chmod(0o755)

        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input("git status"),
                home=home,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "allow"
        )
        assert not call_marker.exists(), (
            "_lib_split_fragments must not run for a command with no write construct"
        )

    def test_reading_state_file_as_a_copy_source_denied_as_accepted_tradeoff(self, tmp_path):
        """Documents the accepted tradeoff this hook's own header discloses:
        widened candidate emission tests every word of a write-gated
        fragment, so a `cp`/`rsync` whose only claude-config.toml mention is
        the SOURCE argument (a read, e.g. backing the file up) is denied
        too, even though nothing is written to it. Expected-fail by design,
        not a bug -- a `cat`/`grep`/`less` read of the same source is
        unaffected, since neither invokes a recognized write utility."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        source = home / ".claude" / "claude-config.toml"
        source.write_text("worktree_required = true\n")
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"cp {source} /tmp/backup.toml"),
                home=home,
            )
            == "deny"
        )


class TestReAddedWriteUtilitiesDenyExplicitDestinations:
    """rsync/curl/scp/wget/openssl are in _LIB_WRITE_UTILITIES -- see that
    array's own header comment in _lib.sh for the membership criterion (a
    single-word command whose ordinary invocation writes a file the caller
    names as an explicit token). Each of these five names an explicit
    destination token in the commands below (rsync/scp positionally,
    curl -o/wget -O/openssl -out the same way dd's of= does), so each must
    deny like the existing cp/dd/sed deny-path tests above."""

    @pytest.mark.parametrize(
        "command_template",
        [
            "rsync /tmp/payload.toml {target}",
            "curl -s -o {target} https://example.invalid/payload",
            "scp /tmp/payload.toml localhost:{target}",
            "wget -O {target} https://example.invalid/payload",
            "openssl enc -out {target} -in /tmp/payload.toml",
        ],
    )
    def test_explicit_destination_token_denied(self, command_template, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "claude-config.toml"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(command_template.format(target=target)),
                home=home,
            )
            == "deny"
        )


class TestGluedShortFlagBypassClosed:
    """CRITICAL bypass (round 4 finding): _lib_fragment_candidates emits a
    glued short-option token (curl -so<path>, wget -O<path>/-qO<path> --
    flag and value in one word, no space or '=') verbatim. _lib_shape_match's
    pass-1 exact-match branch and passes 2-4's `-ef` calls all compare from
    the candidate's own position 0, so the glued flag prefix defeated every
    one of them whenever the resolved config dir has no literal '.claude'
    path segment -- this repo's own documented
    ~/.local/state/claude-accounts/<account>/ multi-account layout is
    exactly that shape. Only pass 1's wildcard branch happened to tolerate
    the prefix, and only because of its own leading '*'. Empirically
    confirmed against real curl 8.5.0 and wget binaries: both accept a
    glued short-option value with no separator; openssl's own option parser
    does not (verified: 'openssl rand -out/tmp/x 8' errors 'Unknown
    option'), but _lib_fragment_candidates emits every fragment word as a
    candidate regardless of what the specific utility's own parser would
    accept, so the shape-match fix must still close this word shape for
    openssl too. Parametrized across both a default (.claude-segment-
    present) and a .claude-segment-free CLAUDE_CONFIG_DIR to prove the fix
    isn't itself keyed off the '.claude' substring."""

    @pytest.fixture(params=[True, False], ids=["default-dotclaude-segment", "dotclaude-free-config-dir"])
    def config_dir_topology(self, request, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        if request.param:
            target = home / ".claude" / "claude-config.toml"
            extra_env = None
        else:
            config_dir = tmp_path / "profile"
            config_dir.mkdir()
            target = config_dir / "claude-config.toml"
            extra_env = {"CLAUDE_CONFIG_DIR": str(config_dir)}
        return home, target, extra_env

    @pytest.mark.parametrize(
        "command_template",
        [
            "curl -sSo{target} https://example.invalid/payload",
            "wget -O{target} https://example.invalid/payload",
            "wget -qO{target} https://example.invalid/payload",
            "openssl enc -out{target} -in /tmp/payload.toml",
        ],
        ids=["curl-sSo-glued", "wget-O-glued", "wget-qO-glued", "openssl-out-glued"],
    )
    def test_glued_flag_value_denied(self, command_template, config_dir_topology):
        home, target, extra_env = config_dir_topology
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(command_template.format(target=target)),
                home=home,
                extra_env=extra_env,
            )
            == "deny"
        )


class TestReAddedWriteUtilitiesAllowUnrelatedDestinations:
    """Mirrors TestReAddedWriteUtilitiesDenyExplicitDestinations' deny-path
    parametrization, pointed at a destination with no relationship to
    claude-config.toml -- cp/dd/sed already have this allow-path coverage
    (test_redirect_to_unrelated_file_allowed and the no-write-construct/
    read-source tests above); rsync/scp/openssl had none."""

    @pytest.mark.parametrize(
        "command_template",
        [
            "rsync /tmp/payload.toml {target}",
            "scp /tmp/payload.toml localhost:{target}",
            "openssl enc -out {target} -in /tmp/payload.toml",
        ],
        ids=["rsync", "scp", "openssl"],
    )
    def test_explicit_destination_token_to_unrelated_file_allowed(self, command_template, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        target = home / ".claude" / "some-other-file.md"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(command_template.format(target=target)),
                home=home,
            )
            == "allow"
        )


class TestDirectoryDestinationVectorClosed:
    """A write-utility destination that IS the config root directory (a
    trailing-slash form, or a bare `-t DIR`/`--target-directory=DIR` value)
    has no leaf component naming `claude-config.toml` for `_lib_shape_match`'s
    other passes to compare against. cp/mv/install/rsync/scp's own
    basename-preservation semantics (writing DEST/basename(source)) would
    otherwise land the write on the protected file with zero gate output.
    Pass 2b (see `_lib.sh`) closes this by comparing the candidate directly
    against the resolved root, with no visibility into the source file's
    own name. This hook's own calling code additionally requires the
    command to name claude-config.toml before honoring a Pass 2b match:
    each deny-path command's source below is itself named claude-config.toml,
    matching the reviewer's own reproduction, while the allow-path test
    below writes an unrelated source into the same directory shape.
    CLAUDE_CONFIG_DIR is cleared for the same reason as the existing
    symlink/hardlink tests above: the `-ef` match depends on the config
    root resolving to this test's own $HOME/.claude."""

    @pytest.mark.parametrize(
        "command_template",
        [
            "cp {source} {target_dir}/",
            "mv {source} {target_dir}/",
            "install -m 644 {source} {target_dir}/",
            "rsync {source} {target_dir}/",
            "scp {source} {target_dir}/",
        ],
        ids=["cp", "mv", "install", "rsync", "scp"],
    )
    def test_trailing_slash_directory_destination_denied(self, command_template, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        source = tmp_path / "staging" / "claude-config.toml"
        source.parent.mkdir()
        source.write_text("worktree_required = true\n")
        target_dir = home / ".claude"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(command_template.format(source=source, target_dir=target_dir)),
                home=home,
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "command_template",
        [
            "cp -t {target_dir} {source}",
            "cp --target-directory={target_dir} {source}",
            "mv -t {target_dir} {source}",
            "install -m 644 -t {target_dir} {source}",
        ],
        ids=["cp-t", "cp-target-directory-eq", "mv-t", "install-t"],
    )
    def test_target_directory_flag_destination_denied(self, command_template, tmp_path, monkeypatch):
        """-t/--target-directory is a genuine cp/mv/install option -- rsync
        and scp have no equivalent flag (rsync's own -t means "preserve
        modification times"), so this vector is parametrized across only
        the three utilities that actually support it, unlike the
        trailing-slash vector above."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        source = tmp_path / "staging" / "claude-config.toml"
        source.parent.mkdir()
        source.write_text("worktree_required = true\n")
        target_dir = home / ".claude"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(command_template.format(source=source, target_dir=target_dir)),
                home=home,
            )
            == "deny"
        )

    def test_trailing_slash_directory_destination_to_unrelated_directory_allowed(self, tmp_path, monkeypatch):
        """Pass 2b's `-ef` comparison is scoped to the resolved config root
        specifically, not to "any directory destination" -- a trailing-slash
        write into a genuinely unrelated directory must stay allowed, or a
        future edit that loosens the new pass's own root comparison would
        over-deny with no test to catch it."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        unrelated_dir = tmp_path / "unrelated"
        unrelated_dir.mkdir()
        source = tmp_path / "staging" / "claude-config.toml"
        source.parent.mkdir()
        source.write_text("worktree_required = true\n")
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"cp {source} {unrelated_dir}/"),
                home=home,
            )
            == "allow"
        )

    def test_trailing_slash_directory_destination_of_unrelated_file_allowed(self, tmp_path, monkeypatch):
        """ciso-reviewer over-match finding: Pass 2b matches on the
        destination directory alone, so a write of a file NOT named
        claude-config.toml into the actual config root must stay allowed --
        denying it would widen this hook's blast radius from "one protected
        file" to "the entire config root directory," contradicting the
        hook's own single-file-scoped contract."""
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        source = tmp_path / "staging" / "some-other-file.md"
        source.parent.mkdir()
        source.write_text("unrelated content\n")
        target_dir = home / ".claude"
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(f"cp {source} {target_dir}/"),
                home=home,
            )
            == "allow"
        )


class TestCurlWgetImplicitDestinationResidual:
    """curl -O and a bare `wget URL` derive their write target from the URL
    or a server response rather than naming it as a literal token in the
    command -- the one residual _LIB_WRITE_UTILITIES's membership criterion
    excludes (see that array's own header comment in _lib.sh). Pins that
    this specific implicit-destination shape stays allowed, so a future
    attempt to close it reads as a deliberate mechanism change to this test,
    not an unnoticed capability drift -- distinct from the deny-path
    coverage above, which covers only the explicit-destination forms of the
    same five utilities."""

    @pytest.mark.parametrize(
        "command",
        [
            "curl -O https://example.invalid/claude-config.toml",
            "wget https://example.invalid/claude-config.toml",
        ],
    )
    def test_url_derived_destination_basename_allowed_residual(self, command, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert (
            run_hook(
                ENFORCE_CONFIG_WRITE_SHAPE_HOOK,
                bash_input(command),
                home=home,
            )
            == "allow"
        )


class TestNonMatchingToolNames:
    def test_read_tool_allowed(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(home / ".claude" / "claude-config.toml")}}
        assert run_hook(ENFORCE_CONFIG_WRITE_SHAPE_HOOK, payload, home=home) == "allow"
