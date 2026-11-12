"""The account's smoke-pass record, which `run` requires before any paid block on that account.

A record that is absent, unparseable, lacks a key, or whose account key does not hash to its file name reads
as absent, and so does one for another environment reading, model ID or arm set.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from pathlib import Path

from review_bench.defects import atomic_write_text
from review_bench.runner import EnvironmentRecord

SMOKE_PASSES_DIR_NAME = "smoke-passes"
_DIGEST_CHARS = 16


def account_digest(account_key: str) -> str:
    return hashlib.sha256(account_key.encode()).hexdigest()[:_DIGEST_CHARS]


def smoke_pass_path(local_dir: Path, account_key: str) -> Path:
    return local_dir / SMOKE_PASSES_DIR_NAME / f"{account_digest(account_key)}.json"


def write_smoke_pass(
    local_dir: Path, account_key: str, environment: EnvironmentRecord, model_id: str, arm_names: Collection[str],
) -> Path:
    path = smoke_pass_path(local_dir, account_key)
    atomic_write_text(path, json.dumps({
        "account_key": account_key, "cli_version": environment.cli_version,
        "ambient_config_commit": environment.ambient_config_commit, "model_id": model_id, "arms": sorted(arm_names),
    }, indent=2) + "\n")
    return path


def has_smoke_pass(
    local_dir: Path, account_key: str, environment: EnvironmentRecord, model_id: str, arm_names: Collection[str],
) -> bool:
    path = smoke_pass_path(local_dir, account_key)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        stored_key = record["account_key"]
        matches = (
            stored_key == account_key and account_digest(stored_key) == path.stem
            and record["cli_version"] == environment.cli_version
            and record["ambient_config_commit"] == environment.ambient_config_commit
            and record["model_id"] == model_id and set(arm_names) <= set(record["arms"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return matches
