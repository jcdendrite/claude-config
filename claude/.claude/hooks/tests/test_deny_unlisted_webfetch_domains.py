"""Tests for deny-unlisted-webfetch-domains.sh.

Decision table: a listed domain always allows. An unlisted domain asks in
default/acceptEdits/plan mode, and denies outright in
auto/bypassPermissions/dontAsk — a hook-returned "ask" forcing a prompt
under auto/bypass is undocumented, so this hook uses deny there instead of
assuming a prompt appears. An absent, empty, or unrecognized permission_mode
fails closed to deny, never falls through to ask. File-absent is treated
identically to file-present-but-empty (every domain unlisted) — deliberately
inverting _lib_config_lines's usual "absent means no restriction" contract,
since this file grants reach rather than widening a deny.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time

import pytest
from helpers import HOOKS_DIR, agent_input, bash_input, build_path_without, run_hook, run_hook_reason, webfetch_input

DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK = HOOKS_DIR / "deny-unlisted-webfetch-domains.sh"


def _write_allowlist(home, *domains: str) -> None:
    (home / ".claude" / "webfetch-allowed-domains.md").write_text("\n".join(domains) + "\n")


class TestDenyUnlistedWebfetchDomains:
    # ------------------------------------------------------------------ #
    # Allow — listed domain, any permission mode                          #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", "auto", "bypassPermissions", "dontAsk", None])
    def test_listed_domain_allowed_in_every_mode(self, isolated_home, mode):
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://github.com/x", permission_mode=mode),
                home=isolated_home,
            )
            == "allow"
        )

    def test_wildcard_entry_matches_strict_subdomain(self, isolated_home):
        _write_allowlist(isolated_home, "*.github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://api.github.com/x", permission_mode="default"),
                home=isolated_home,
            )
            == "allow"
        )

    def test_wildcard_entry_does_not_match_bare_apex(self, isolated_home):
        """*.example.com matches only strict subdomains, matching the
        sandbox's own documented domain-pattern semantics — the bare apex
        needs its own separate allowlist entry."""
        _write_allowlist(isolated_home, "*.github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://github.com/x", permission_mode="default"),
                home=isolated_home,
            )
            == "ask"
        )

    def test_matching_is_case_insensitive(self, isolated_home):
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://GITHUB.com/x", permission_mode="default"),
                home=isolated_home,
            )
            == "allow"
        )

    def test_trailing_dot_hostname_matches(self, isolated_home):
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://github.com./x", permission_mode="default"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://notgithub.com/x",
            "https://evil.com/github.com",
            "https://github.com.evil.com/x",
        ],
    )
    def test_lookalike_domain_not_matched(self, isolated_home, url):
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input(url, permission_mode="default"),
                home=isolated_home,
            )
            == "ask"
        )

    # ------------------------------------------------------------------ #
    # URL-authority parsing — urlsplit, not a hand-rolled regex           #
    # ------------------------------------------------------------------ #

    def test_userinfo_prefixed_authority_resolves_to_real_host(self, isolated_home):
        """https://github.com@evil.com/x must resolve to evil.com via
        urlsplit().hostname, not to whatever substring precedes the @ —
        the bug a hand-rolled host-extraction regex had during design
        review, closed by shelling out to urllib.parse instead."""
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://github.com@evil.com/x", permission_mode="default"),
                home=isolated_home,
            )
            == "ask"
        )
        reason = run_hook_reason(
            DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
            webfetch_input("https://github.com@evil.com/x", permission_mode="default"),
            home=isolated_home,
        )
        assert reason is not None
        assert "evil.com" in reason
        assert "github.com" not in reason

    def test_port_in_authority_does_not_prevent_host_match(self, isolated_home):
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://github.com:443/x", permission_mode="default"),
                home=isolated_home,
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "about:blank",
            "data:text/html,x",
            "example.com",
            "",
            "file:///etc/passwd",
        ],
    )
    def test_unparseable_or_hostless_url_denied_not_crashed(self, isolated_home, url):
        """urlsplit(...).hostname returns None for these shapes — the fail-
        closed path must reach deny (not ask, and not a crash) regardless
        of permission_mode."""
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input(url, permission_mode="default"),
                home=isolated_home,
            )
            == "deny"
        )

    # ------------------------------------------------------------------ #
    # permission_mode axis                                                #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan"])
    def test_unlisted_domain_asks_in_interactive_modes(self, isolated_home, mode):
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://docs.python.org/x", permission_mode=mode),
                home=isolated_home,
            )
            == "ask"
        )

    @pytest.mark.parametrize("mode", ["auto", "bypassPermissions", "dontAsk"])
    def test_unlisted_domain_denies_in_unattended_modes(self, isolated_home, mode):
        """A hook-returned ask forcing a prompt under auto/bypassPermissions
        is undocumented, so this uses deny — guaranteed everywhere — rather
        than assume a prompt appears."""
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://docs.python.org/x", permission_mode=mode),
                home=isolated_home,
            )
            == "deny"
        )

    @pytest.mark.parametrize("mode", [None, "", "some-future-mode"])
    def test_unrecognized_permission_mode_fails_closed_to_deny(self, isolated_home, mode):
        """Absent, empty, or unrecognized permission_mode must reach deny,
        never fall through to ask — ask is only safe when the mode is a
        known interactive one."""
        _write_allowlist(isolated_home, "github.com")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://docs.python.org/x", permission_mode=mode),
                home=isolated_home,
            )
            == "deny"
        )

    # ------------------------------------------------------------------ #
    # File-absent handling — inverted _lib_config_lines contract          #
    # ------------------------------------------------------------------ #

    def test_missing_allowlist_file_denies_by_default_not_fail_open(self, isolated_home):
        """No ~/.claude/webfetch-allowed-domains.md at all must behave
        identically to an existing-but-empty file: every domain is
        unlisted. A silent allow-everything default would defeat the point
        of the hook for every consumer who never creates the file."""
        assert not (isolated_home / ".claude" / "webfetch-allowed-domains.md").exists()
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://docs.python.org/x", permission_mode="auto"),
                home=isolated_home,
            )
            == "deny"
        )

    def test_missing_allowlist_deny_reason_names_the_file_path(self, isolated_home):
        reason = run_hook_reason(
            DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
            webfetch_input("https://docs.python.org/x", permission_mode="auto"),
            home=isolated_home,
        )
        assert reason is not None
        assert "webfetch-allowed-domains.md" in reason

    def test_empty_allowlist_file_behaves_the_same_as_absent(self, isolated_home):
        (isolated_home / ".claude" / "webfetch-allowed-domains.md").write_text("")
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://docs.python.org/x", permission_mode="auto"),
                home=isolated_home,
            )
            == "deny"
        )

    def test_allowlist_comment_and_blank_lines_ignored(self, isolated_home):
        (isolated_home / ".claude" / "webfetch-allowed-domains.md").write_text(
            "# comment\n\ngithub.com\n\n# another comment\n"
        )
        assert (
            run_hook(
                DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
                webfetch_input("https://github.com/x", permission_mode="default"),
                home=isolated_home,
            )
            == "allow"
        )

    # ------------------------------------------------------------------ #
    # python3 dependency — hard requirement for this hook                 #
    # ------------------------------------------------------------------ #

    def test_python3_absent_denies_naming_python3(self, isolated_home, tmp_path):
        farm_dir = tmp_path / "path-farm"
        farm_dir.mkdir()
        path_without_python3 = build_path_without("python3", farm_dir)
        reason = run_hook_reason(
            DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
            webfetch_input("https://github.com/x", permission_mode="default"),
            home=isolated_home,
            extra_env={"PATH": path_without_python3},
        )
        assert reason is not None
        assert "python3" in reason

    def test_python3_hung_past_timeout_denies_naming_python3_not_misread_as_empty_host(self, isolated_home, tmp_path):
        """A timeout exit (124) must resolve to the python3-absent-style
        deny, not fall through and be misread as an empty-hostname URL —
        those are different failure modes with the same downstream shape
        (empty $HOST) unless the exit code is checked. Same skip guard and
        elapsed-time assertion as test_lib.py's test_hung_jq_denied_within_timeout:
        without GNU timeout, _lib_capped falls back to an uncapped call, and
        this test's fake python3 would then genuinely block for its full
        sleep duration instead of the hook's 5s cap firing."""
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
        farm_dir = tmp_path / "path-farm"
        farm_dir.mkdir()
        path_without_python3 = build_path_without("python3", farm_dir)
        hung_python3 = farm_dir / "python3"
        hung_python3.write_text("#!/bin/sh\nsleep 30\n")
        hung_python3.chmod(0o755)
        start = time.monotonic()
        reason = run_hook_reason(
            DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK,
            webfetch_input("https://github.com/x", permission_mode="default"),
            home=isolated_home,
            extra_env={"PATH": path_without_python3},
        )
        elapsed = time.monotonic() - start
        assert reason is not None
        assert "python3" in reason
        assert "empty" not in reason.lower()
        assert elapsed < 6, f"hung-python3 test took {elapsed:.1f}s — the 5s timeout did not fire in time"

    # ------------------------------------------------------------------ #
    # Non-WebFetch passthrough                                            #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize(
        "payload",
        [
            bash_input("curl https://example.com"),
            agent_input(),
        ],
    )
    def test_non_webfetch_tools_pass_through(self, isolated_home, payload):
        assert run_hook(DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK, payload, home=isolated_home) == "allow"

    # ------------------------------------------------------------------ #
    # Fail-closed on malformed JSON                                       #
    # ------------------------------------------------------------------ #

    def test_malformed_json_denied(self):
        result = subprocess.run(
            [str(DENY_UNLISTED_WEBFETCH_DOMAINS_HOOK)],
            input="not valid json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip()
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
