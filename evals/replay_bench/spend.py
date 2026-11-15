"""The campaign's spend file: one JSON line per launched attempt, which `run`'s stops sum."""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from review_bench import runner as review_runner


class SpendAppendError(RuntimeError):
    """`run` halts on this rather than continue uncharged."""


@dataclass(frozen=True)
class SpendLine:
    task_id: str
    arm: str
    run_index: int
    attempt: int
    account_key: str
    session_id: str
    cost_usd: float
    is_cap_charge: bool


@dataclass
class SpendTotals:
    total_usd: float = 0.0
    by_account_usd: dict[str, float] = field(default_factory=dict)
    by_task_usd: dict[str, float] = field(default_factory=dict)
    unparsed_lines: int = 0
    # Counted at the cap against every account, since a line that does not parse names no readable account.
    unparsed_usd: float = 0.0
    session_ids: set[str] = field(default_factory=set)

    def account_usd(self, account_key: str) -> float:
        return self.by_account_usd.get(account_key, 0.0) + self.unparsed_usd


def is_usable_cost(cost: object) -> bool:
    """NaN would make every stop comparison False, so it reads as no figure."""
    return isinstance(cost, int | float) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0


def _parse_line(text: str) -> SpendLine | None:
    try:
        data = json.loads(text)
        cost = data["cost_usd"]
        if not is_usable_cost(cost):
            return None
        return SpendLine(**{**data, "cost_usd": float(cost)})
    except (ValueError, KeyError, TypeError):
        return None


class SpendFile:
    """After a short write, the next append starts with a newline, so the torn line stays one unparsed line and
    never fuses with it."""

    def __init__(self, path: Path, *, budget_cap_usd: float) -> None:
        self.path = path
        self.budget_cap_usd = budget_cap_usd
        self._lock = threading.Lock()
        self._tail_is_torn = False

    def repair_torn_tail(self) -> None:
        review_runner._repair_torn_tail(self.path)

    def append(self, line: SpendLine) -> None:
        encoded = (json.dumps(asdict(line)) + "\n").encode()
        with self._lock:
            payload = b"\n" + encoded if self._tail_is_torn else encoded
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    written = os.write(descriptor, payload)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise SpendAppendError(f"could not append to the spend file: {review_runner._os_error_detail(exc)}") from None
            self._tail_is_torn = written != len(payload)
            if self._tail_is_torn:
                raise SpendAppendError("a short write left a torn line in the spend file")

    def totals(self) -> SpendTotals:
        totals = SpendTotals()
        if not self.path.exists():
            return totals
        for text in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not text.strip():
                continue
            line = _parse_line(text)
            if line is None:
                totals.unparsed_lines += 1
                totals.unparsed_usd += self.budget_cap_usd
                totals.total_usd += self.budget_cap_usd
                continue
            totals.total_usd += line.cost_usd
            totals.by_account_usd[line.account_key] = totals.by_account_usd.get(line.account_key, 0.0) + line.cost_usd
            totals.by_task_usd[line.task_id] = totals.by_task_usd.get(line.task_id, 0.0) + line.cost_usd
            totals.session_ids.add(line.session_id)
        return totals
