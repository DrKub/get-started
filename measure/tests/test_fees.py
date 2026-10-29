"""Fee ceiling, ladder budget, and the durable cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from measure.budget import projected_fee_lamports, projected_outlay_lamports
from measure.constants import CU_LIMIT, DESIGN_BUDGET_LAMPORTS, HARD_CAP_LAMPORTS, LADDER_PRIORITY_FEES
from measure.errors import ConfigError, SpendCapExceeded
from measure.fees import (
    SpendGuard,
    charged_priority_fee,
    cu_price_for_priority_fee,
    lamports_to_sol_str,
    parse_sol_to_lamports,
    priority_score,
    resolve_spend_cap,
)
from measure.ledger import SpendLedger


def test_sol_parsing_and_hard_cap():
    assert parse_sol_to_lamports("0.05") == 50_000_000
    assert parse_sol_to_lamports("0.012") == 12_000_000
    assert lamports_to_sol_str(50_000_000) == "0.050000000"
    assert resolve_spend_cap("0.05") == HARD_CAP_LAMPORTS
    with pytest.raises(ConfigError):
        resolve_spend_cap("0.06")
    with pytest.raises(ConfigError):
        resolve_spend_cap("0")


def test_priority_fee_uses_ceiling_and_scores_are_unique():
    for fee in LADDER_PRIORITY_FEES:
        price = cu_price_for_priority_fee(CU_LIMIT, fee)
        assert charged_priority_fee(CU_LIMIT, price) == fee
    assert charged_priority_fee(3, 500_000) == 2
    scores = [priority_score(CU_LIMIT, fee) for fee in LADDER_PRIORITY_FEES]
    assert len(scores) == len(set(scores))
    assert scores == sorted(scores)


def test_default_plan_stays_under_the_design_budget():
    fees = projected_fee_lamports(n_leaders=6)
    outlay = projected_outlay_lamports(n_leaders=6)
    assert fees < outlay < DESIGN_BUDGET_LAMPORTS
    assert outlay < HARD_CAP_LAMPORTS


def test_spend_guard_and_global_ledger(tmp_path: Path):
    guard = SpendGuard(10)
    guard.approve_batch([4, 6])
    with pytest.raises(SpendCapExceeded):
        guard.approve(1)
    assert guard.spent_lamports == 10
    path = tmp_path / "spend.json"
    first = SpendLedger(path, 50_000_000)
    first.reserve(5_000, run_id="e1", seq=0, reason="signed")
    second = SpendLedger(path, 50_000_000)
    assert second.spent_lamports == 5_000
    second.ensure_fits(5_000)
    with pytest.raises(SpendCapExceeded):
        SpendLedger(path, 6_000).reserve(2_000, run_id="e1", seq=1, reason="signed")
    assert SpendLedger(path, 50_000_000).spent_lamports == 5_000
