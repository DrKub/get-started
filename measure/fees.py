"""Fee arithmetic and the hard spend cap.

The runtime charges::

    signature_fee + ceil(cu_limit * cu_price_micro_lamports / 1_000_000)

Agave's ``get_prioritization_fee`` uses that ceiling. The ladder steps are
chosen so the division is exact and the ceiling does not add an extra lamport.
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
    micro = cu_limit * cu_price_micro_lamports
    if micro == 0:
        return 0
    return (micro + MICROLAMPORTS_PER_LAMPORT - 1) // MICROLAMPORTS_PER_LAMPORT


def leader_reward_lamports(priority_fee_lamports: int, *, signatures: int = 1) -> int:
    """Leader deposit: half the base fee, plus the whole priority fee."""
    base = signatures * SIGNATURE_FEE_LAMPORTS
    return base // 2 + priority_fee_lamports


def transaction_cost_units(cu_limit: int, *, signatures: int = 1, writable_accounts: int = 2) -> int:
    from measure.constants import SIGNATURE_COST, WRITE_LOCK_COST

    return signatures * SIGNATURE_COST + writable_accounts * WRITE_LOCK_COST + cu_limit


def priority_score(cu_limit: int, priority_fee_lamports: int) -> int:
    """Agave ``P = (reward * 1_000_000) / (cost + 1)`` for a fixed message shape."""
    reward = leader_reward_lamports(priority_fee_lamports)
    cost = transaction_cost_units(cu_limit)
    return (reward * MICROLAMPORTS_PER_LAMPORT) // (cost + 1)


def meta_fee(transaction: dict | None) -> int | None:
    """``meta.fee`` from getTransaction. A failed transaction still paid the fee.

    None means the signature was not found, or the payload had no fee.
    """
    if not isinstance(transaction, dict):
        return None
    meta = transaction.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("fee"), int):
        return None
    return int(meta["fee"])


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
