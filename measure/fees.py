"""Fee arithmetic and the hard spend cap.

The runtime charges::

    signature_fee + (cu_limit * cu_price_micro_lamports) // 1_000_000

Estimates use that exact division, so the cap tracks what a landed
transaction debits. A transaction that never lands still counts once we
attempt to send it: the cap is an upper bound, not a refund ledger.
"""

from __future__ import annotations

import re

from measure.constants import (
    HARD_CAP_LAMPORTS,
    LAMPORTS_PER_SOL,
    MICROLAMPORTS_PER_LAMPORT,
    SIGNATURE_FEE_LAMPORTS,
)
from measure.errors import ConfigError, SpendCapExceeded

_SOL_RE = re.compile(r"\d+(\.\d+)?")


def parse_sol_to_lamports(value: str) -> int:
    text = str(value).strip()
    if not _SOL_RE.fullmatch(text):
        raise ConfigError(f"invalid SOL amount: {value!r}")
    whole, _, frac = text.partition(".")
    if len(frac) > 9:
        raise ConfigError("SOL amounts support at most 9 decimal places")
    frac = (frac or "").ljust(9, "0")
    return int(whole) * LAMPORTS_PER_SOL + int(frac)


def resolve_spend_cap(value: str) -> int:
    lamports = parse_sol_to_lamports(value)
    if lamports <= 0:
        raise ConfigError("spend cap must be positive")
    if lamports > HARD_CAP_LAMPORTS:
        raise ConfigError(
            "spend cap cannot exceed 0.05 SOL "
            f"({HARD_CAP_LAMPORTS} lamports); got {lamports}"
        )
    return lamports


def lamports_to_sol_str(lamports: int) -> str:
    sign = "-" if lamports < 0 else ""
    whole, frac = divmod(abs(lamports), LAMPORTS_PER_SOL)
    return f"{sign}{whole}.{frac:09d}"


def cu_price_for_priority_fee(cu_limit: int, priority_fee_lamports: int) -> int:
    """Micro-lamports per CU that charge exactly ``priority_fee_lamports``."""
    if cu_limit <= 0:
        raise ValueError("cu_limit must be positive")
    if priority_fee_lamports < 0:
        raise ValueError("priority fee must be non-negative")
    numerator = priority_fee_lamports * MICROLAMPORTS_PER_LAMPORT
    if numerator % cu_limit != 0:
        raise ValueError(
            f"priority fee {priority_fee_lamports} is not exact at CU limit {cu_limit}"
        )
    return numerator // cu_limit


def charged_priority_fee(cu_limit: int, cu_price_micro_lamports: int) -> int:
    if cu_limit < 0 or cu_price_micro_lamports < 0:
        raise ValueError("cu limit and price must be non-negative")
    return (cu_limit * cu_price_micro_lamports) // MICROLAMPORTS_PER_LAMPORT


def transaction_fee_lamports(
    cu_limit: int,
    cu_price_micro_lamports: int,
    *,
    signatures: int = 1,
) -> int:
    if signatures < 1:
        raise ValueError("at least one signature")
    return signatures * SIGNATURE_FEE_LAMPORTS + charged_priority_fee(
        cu_limit, cu_price_micro_lamports
    )


class SpendGuard:
    """Cumulative fee estimate. ``approve_batch`` is all-or-nothing."""

    def __init__(self, cap_lamports: int) -> None:
        if isinstance(cap_lamports, bool) or not isinstance(cap_lamports, int):
            raise ValueError("cap must be an int")
        if cap_lamports <= 0 or cap_lamports > HARD_CAP_LAMPORTS:
            raise ValueError(f"cap {cap_lamports} outside 1..{HARD_CAP_LAMPORTS}")
        self.cap_lamports = cap_lamports
        self.spent_lamports = 0

    def approve(self, fee_lamports: int) -> None:
        self.approve_batch([fee_lamports])

    def approve_batch(self, fees: list[int]) -> None:
        total = 0
        for fee in fees:
            if isinstance(fee, bool) or not isinstance(fee, int) or fee < 0:
                raise ValueError("fee must be a non-negative int")
            total += fee
        if self.spent_lamports + total > self.cap_lamports:
            raise SpendCapExceeded(self.spent_lamports, total, self.cap_lamports)
        self.spent_lamports += total
