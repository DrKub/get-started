"""Unit tests for fee math and the hard cap."""

from __future__ import annotations

import pytest

from measure.constants import HARD_CAP_LAMPORTS, LADDER_CU_LIMIT, LADDER_PRIORITY_FEES
from measure.errors import ConfigError, SpendCapExceeded
from measure.fees import (
    SpendGuard,
    charged_priority_fee,
    cu_price_for_priority_fee,
    lamports_to_sol_str,
    parse_sol_to_lamports,
    resolve_spend_cap,
    transaction_fee_lamports,
)
from measure.plan import window_fee_lamports


def test_sol_parsing_and_hard_cap():
    assert parse_sol_to_lamports("0.05") == 50_000_000
    assert parse_sol_to_lamports("0.050000000") == HARD_CAP_LAMPORTS
    assert parse_sol_to_lamports("1") == 1_000_000_000
    assert parse_sol_to_lamports("0.000000001") == 1
    assert lamports_to_sol_str(50_000_000) == "0.050000000"
    assert resolve_spend_cap("0.05") == HARD_CAP_LAMPORTS
    with pytest.raises(ConfigError):
        resolve_spend_cap("0.06")
    with pytest.raises(ConfigError):
        resolve_spend_cap("0")
    with pytest.raises(ConfigError):
        parse_sol_to_lamports("0.0000000001")
    with pytest.raises(ConfigError):
        parse_sol_to_lamports("-0.01")


def test_priority_fee_is_exact_integer_division():
    for fee in LADDER_PRIORITY_FEES:
        price = cu_price_for_priority_fee(LADDER_CU_LIMIT, fee)
        assert charged_priority_fee(LADDER_CU_LIMIT, price) == fee
    assert charged_priority_fee(3, 500_000) == 1
    assert transaction_fee_lamports(20_000, 0) == 5_000
    assert window_fee_lamports() == 496_000


def test_spend_guard_is_all_or_nothing():
    guard = SpendGuard(10)
    guard.approve_batch([4, 6])
    assert guard.spent_lamports == 10
    with pytest.raises(SpendCapExceeded):
        guard.approve(1)
    assert guard.spent_lamports == 10
    fresh = SpendGuard(10)
    with pytest.raises(SpendCapExceeded):
        fresh.approve_batch([11])
    assert fresh.spent_lamports == 0
    with pytest.raises(ValueError):
        fresh.approve(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SpendGuard(HARD_CAP_LAMPORTS + 1)
