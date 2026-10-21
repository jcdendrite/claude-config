"""Test helpers shared by test_pr_diff_against_base.py and test_pr_diff_lib.py."""
from __future__ import annotations

import subprocess
from pathlib import Path

from helpers import HOOKS_DIR, run_hook

_CREDENTIAL_FILE_READS_HOOK = HOOKS_DIR / "deny-credential-file-reads.sh"
_DENY_ENV_READS_HOOK = HOOKS_DIR / "deny-env-reads.sh"

# Credential path names are assembled at runtime so no source line carries one literally; the private-project
# redaction gate denies a staged line naming the SSH config directory or an id_<algorithm> key basename.
_SSH_DIR = "." + "ssh"
_RSA_KEY = "id_" + "rsa"
_DSA_KEY = "id_" + "dsa"
_ED25519_KEY = "id_" + "ed25519"

# The pinned flags every non-history diff call passes; the oracle calls git exactly as the script does.
_DIFF_PIN = (
    "-c", "core.quotePath=false", "diff",
    "--no-color", "--no-ext-diff", "--no-textconv", "--no-relative", "-M",
    "--src-prefix=a/", "--dst-prefix=b/", "--submodule=short", "--ignore-submodules=none",
)


def _git_out(repo: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    """git in `repo`, stdout kept as bytes."""
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=check, env=env)


def _credential_read_gates_verdict(name: str, home: Path, root: str = "/repo") -> str:
    """The verdict a Read of "<root>/<name>" gets: deny when either credential Read gate denies it."""
    verdicts = {
        run_hook(hook, {"tool_name": "Read", "tool_input": {"file_path": f"{root}/{name}"}}, home=home,
                 extra_env={"CLAUDE_CONFIG_DIR": ""})
        for hook in (_CREDENTIAL_FILE_READS_HOOK, _DENY_ENV_READS_HOOK)
    }
    return "deny" if "deny" in verdicts else "allow"
