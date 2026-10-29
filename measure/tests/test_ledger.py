"""Cumulative and per-run spend caps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from measure.config import make_config
from measure.errors import ConfigError, SpendCapExceeded
from measure.ledger import SpendLedger

SPENT_BY_RUNS_3_AND_4 = 760_600
RUN_5_FEES = 180_186


def _ledger_after_runs_3_and_4(path: Path) -> None:
    ledger = SpendLedger(path, 50_000_000)
    ledger.reserve(SPENT_BY_RUNS_3_AND_4, run_id="earlier", seq=0, reason="signed")


def test_spend_cap_is_cumulative_so_a_small_value_blocks_a_small_run(tmp_path: Path):
    path = tmp_path / "spend.json"
    _ledger_after_runs_3_and_4(path)
    ledger = SpendLedger(path, 500_000, run_id="run5")
    assert ledger.remaining() < 0
    with pytest.raises(SpendCapExceeded, match="cumulative") as caught:
        ledger.ensure_fits(RUN_5_FEES)
    assert caught.value.scope == "cumulative"


def test_lowest_cumulative_cap_sticks_once_written(tmp_path: Path):
    path = tmp_path / "spend.json"
    SpendLedger(path, 1_000_000).reserve(10, run_id="a", seq=0, reason="signed")
    assert SpendLedger(path, 50_000_000).cap_lamports == 1_000_000


def test_run_cap_bounds_this_run_only(tmp_path: Path):
    path = tmp_path / "spend.json"
    _ledger_after_runs_3_and_4(path)
    ledger = SpendLedger(path, 50_000_000, run_cap_lamports=500_000, run_id="run5")
    assert ledger.remaining() == 500_000
    ledger.ensure_fits(RUN_5_FEES)
    ledger.reserve(RUN_5_FEES, run_id="run5", seq=0, reason="signed")
    ledger.reserve(5_000, run_id="run5:sweep", seq=1, reason="sweep")
    assert ledger.run_spent_lamports() == RUN_5_FEES + 5_000
    assert ledger.remaining() == 500_000 - RUN_5_FEES - 5_000
    with pytest.raises(SpendCapExceeded, match="per-run") as caught:
        ledger.ensure_fits(400_000)
    assert caught.value.scope == "run"
    assert ledger.spent_lamports == SPENT_BY_RUNS_3_AND_4 + RUN_5_FEES + 5_000


def test_run_cap_is_not_persisted_and_released_fees_come_back(tmp_path: Path):
    path = tmp_path / "spend.json"
    ledger = SpendLedger(path, 50_000_000, run_cap_lamports=10_000, run_id="r")
    ledger.reserve(10_000, run_id="r", seq=0, reason="signed")
    ledger.release(10_000, run_id="r", seq=0, reason="send_failed")
    ledger.ensure_fits(10_000)
    assert "run_cap_lamports" not in json.loads(path.read_text(encoding="utf-8"))
    assert SpendLedger(path, 50_000_000).run_cap_lamports is None


def test_run_cap_from_env_and_flag(monkeypatch):
    monkeypatch.setenv("RUN_SPEND_CAP_SOL", "0.0005")
    assert make_config().run_spend_cap_lamports == 500_000
    assert make_config(run_spend_cap_sol="0.0002").run_spend_cap_lamports == 200_000
    monkeypatch.delenv("RUN_SPEND_CAP_SOL")
    assert make_config().run_spend_cap_lamports is None
    with pytest.raises(ConfigError):
        make_config(run_spend_cap_sol="0.06")
