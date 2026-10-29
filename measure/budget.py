"""Default-plan fee and funding arithmetic.

The 0.05 SOL cap counts fees only: base, priority, and the fee on each
funding transfer. Rent left in probe accounts is still the operator's SOL.
``projected_outlay_lamports`` is fees plus that principal. The default plan
stays under ``DESIGN_BUDGET_LAMPORTS`` (0.012 SOL) on both numbers.
"""

from __future__ import annotations

from measure.constants import (
    CU_LIMIT,
    DEFAULT_RENT_LAMPORTS,
    LADDER_PRIORITY_FEES,
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
    """Return ``(nonconflict_payers, recipients)`` that must be funded."""
    payers = len(nonconflict_rungs)
    if nonconflict_rungs:
        # The conflict ladder reuses recipients[0].
        recipients = len(nonconflict_rungs)
    elif conflict_rungs:
        recipients = 1
    else:
        recipients = 0
    return payers, recipients


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
    payers, recipients = account_counts(conflict, nonconflict)
    return probes + (payers + recipients) * SIGNATURE_FEE_LAMPORTS


def funding_principal_lamports(
    *,
    n_leaders: int,
    conflict_rungs: tuple[int, ...] | None = None,
    nonconflict_rungs: tuple[int, ...] | None = None,
    rent_lamports: int = DEFAULT_RENT_LAMPORTS,
    cu_limit: int = CU_LIMIT,
) -> int:
    """Rent plus one-lamport transfers. Probe fees are not included."""
    del cu_limit
    conflict = tuple(LADDER_PRIORITY_FEES if conflict_rungs is None else conflict_rungs)
    nonconflict = tuple(LADDER_PRIORITY_FEES if nonconflict_rungs is None else nonconflict_rungs)
    payers, recipients = account_counts(conflict, nonconflict)
    transfers = n_leaders * (len(conflict) + len(nonconflict)) * TRANSFER_LAMPORTS
    return (payers + recipients) * rent_lamports + transfers


def projected_outlay_lamports(**kwargs) -> int:
    """Fees plus rent and the one-lamport transfers.

    Non-conflict probe fees are transferred into the child accounts and then
    paid from there, so they are counted once, inside the fee term.
    """
    return projected_fee_lamports(**kwargs) + funding_principal_lamports(**kwargs)


def fit_rungs(
    *,
    n_leaders: int,
    cap_remaining: int,
    rent_lamports: int = DEFAULT_RENT_LAMPORTS,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Drop non-conflict rungs before touching the conflict ladder or the set size."""
    conflict = tuple(LADDER_PRIORITY_FEES)
    nonconflict = list(LADDER_PRIORITY_FEES)
    while nonconflict and projected_fee_lamports(
        n_leaders=n_leaders,
        conflict_rungs=conflict,
        nonconflict_rungs=tuple(nonconflict),
        rent_lamports=rent_lamports,
    ) > cap_remaining:
        nonconflict.pop()
    return conflict, tuple(nonconflict)
