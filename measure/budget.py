"""Default-plan fee and funding arithmetic.

Conflict probes are self-transfers from the funder, so they add no account.
Each non-conflict rung is its own fee payer, funded with rent that
``measure sweep`` returns. The net cost is fees only. Peak capital is that
net plus the temporary rent, and it has to fit in about 0.003 SOL.
"""

from __future__ import annotations

from measure.constants import (
    CU_LIMIT,
    DEFAULT_RENT_LAMPORTS,
    LADDER_PRIORITY_FEES,
    NET_BUDGET_LAMPORTS,
    PEAK_BUDGET_LAMPORTS,
    SIGNATURE_FEE_LAMPORTS,
    TRANSFER_LAMPORTS,
)
from measure.fees import cu_price_for_priority_fee, transaction_fee_lamports


def probe_fee(priority_fee: int, *, cu_limit: int = CU_LIMIT) -> int:
    price = cu_price_for_priority_fee(cu_limit, priority_fee)
    return transaction_fee_lamports(cu_limit, price)


def ladder_fee(rungs: tuple[int, ...] | list[int], *, cu_limit: int = CU_LIMIT) -> int:
    return sum(probe_fee(int(fee), cu_limit=cu_limit) for fee in rungs)


def account_counts(
    conflict_rungs: tuple[int, ...],
    nonconflict_rungs: tuple[int, ...],
) -> tuple[int, int]:
    """Return ``(ephemeral payers, extra recipients)``.

    Conflict write-locks the funder. Non-conflict rungs are self-transfers,
    so each payer is its own recipient and no second account is created.
    """
    del conflict_rungs
    return len(nonconflict_rungs), 0


def payer_deposit(
    priority_fee: int,
    *,
    n_leaders: int,
    rent_lamports: int,
    cu_limit: int = CU_LIMIT,
) -> int:
    """Lamports moved into one non-conflict payer. Sweep returns the rent."""
    probe_spend = n_leaders * (probe_fee(priority_fee, cu_limit=cu_limit) + TRANSFER_LAMPORTS)
    return rent_lamports + SIGNATURE_FEE_LAMPORTS + probe_spend


def projected_fee_lamports(
    *,
    n_leaders: int,
    conflict_rungs: tuple[int, ...] | None = None,
    nonconflict_rungs: tuple[int, ...] | None = None,
    rent_lamports: int = DEFAULT_RENT_LAMPORTS,
    cu_limit: int = CU_LIMIT,
) -> int:
    del rent_lamports
    conflict = tuple(LADDER_PRIORITY_FEES if conflict_rungs is None else conflict_rungs)
    nonconflict = tuple(LADDER_PRIORITY_FEES if nonconflict_rungs is None else nonconflict_rungs)
    probes = n_leaders * (
        ladder_fee(conflict, cu_limit=cu_limit) + ladder_fee(nonconflict, cu_limit=cu_limit)
    )
    payers, _recipients = account_counts(conflict, nonconflict)
    # One funding signature to create each payer, and one to sweep it back.
    return probes + payers * (2 * SIGNATURE_FEE_LAMPORTS)


def funding_principal_lamports(
    *,
    n_leaders: int,
    conflict_rungs: tuple[int, ...] | None = None,
    nonconflict_rungs: tuple[int, ...] | None = None,
    rent_lamports: int = DEFAULT_RENT_LAMPORTS,
    cu_limit: int = CU_LIMIT,
) -> int:
    """Rent and one-lamport self-transfers. Sweep returns both."""
    del cu_limit
    conflict = tuple(LADDER_PRIORITY_FEES if conflict_rungs is None else conflict_rungs)
    nonconflict = tuple(LADDER_PRIORITY_FEES if nonconflict_rungs is None else nonconflict_rungs)
    payers, _recipients = account_counts(conflict, nonconflict)
    return payers * rent_lamports + payers * n_leaders * TRANSFER_LAMPORTS


def projected_outlay_lamports(**kwargs) -> int:
    """Peak lamports that must sit in the funder: net fees plus temporary rent."""
    return projected_fee_lamports(**kwargs) + funding_principal_lamports(**kwargs)


def fit_rungs(
    *,
    n_leaders: int,
    cap_remaining: int,
    rent_lamports: int = DEFAULT_RENT_LAMPORTS,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Drop non-conflict rungs before touching the conflict ladder or the set size.

    The fee cap, the 0.0006 SOL net budget, and the 0.003 SOL peak budget
    all apply. Peak includes rent that sweep is expected to return.
    """
    conflict = tuple(LADDER_PRIORITY_FEES)
    nonconflict = list(LADDER_PRIORITY_FEES)
    while nonconflict:
        kwargs = {
            "n_leaders": n_leaders,
            "conflict_rungs": conflict,
            "nonconflict_rungs": tuple(nonconflict),
            "rent_lamports": rent_lamports,
        }
        fees = projected_fee_lamports(**kwargs)
        peak = projected_outlay_lamports(**kwargs)
        if fees <= cap_remaining and fees <= NET_BUDGET_LAMPORTS and peak <= PEAK_BUDGET_LAMPORTS:
            break
        nonconflict.pop()
    return conflict, tuple(nonconflict)
