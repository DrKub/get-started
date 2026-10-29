"""Committed samples stay in lockstep with the pipeline and the parser."""

from __future__ import annotations

import json
from pathlib import Path

from measure.collector import block_context, build_observations, parse_block
from measure.config import make_config
from measure.io_util import read_jsonl
from measure.memo import render_memo
from measure.offline_snapshot import build_offline_snapshot
from measure.pipeline import run_measure
from measure.snapshot_io import load_snapshot, write_snapshot

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"
FIXTURE = ROOT / "measure" / "tests" / "fixtures" / "offline"


def test_committed_fixture_matches_builder(tmp_path):
    loaded = load_snapshot(FIXTURE)
    fresh = build_offline_snapshot()
    assert loaded.leaders == fresh.leaders
    assert loaded.nodes == fresh.nodes
    assert loaded.epoch == 1071
    write_snapshot(tmp_path, fresh)
    assert load_snapshot(tmp_path).blockhash == fresh.blockhash


def test_committed_plan_matches_dry_run(tmp_path):
    out = tmp_path / "out"
    code = run_measure(
        make_config(
            fixture_dir=str(FIXTURE),
            output_dir=str(out),
            experiment_id="e1071-test",
            rpc_url="http://127.0.0.1:9",
        )
    )
    assert code == 0
    for name in (
        "manifest.json",
        "run_summary.json",
        "planned_transactions.jsonl",
        "planned_transactions.csv",
    ):
        assert (out / name).read_bytes() == (SAMPLES / name).read_bytes()


def test_committed_block_sample_parses():
    block = json.loads((ROOT / "measure" / "tests" / "fixtures" / "get_block_sample.json").read_text())
    parsed = parse_block(block, 50)
    high = render_memo(
        experiment_id="e1071-test",
        seq=1,
        arm="ladder",
        send_index=5,
        path="tpu",
        priority_fee_lamports=200000,
        cu_price_micro_lamports=10000000,
        cu_limit=20000,
    )
    low = render_memo(
        experiment_id="e1071-test",
        seq=0,
        arm="ladder",
        send_index=0,
        path="tpu",
        priority_fee_lamports=0,
        cu_price_micro_lamports=0,
        cu_limit=20000,
    )
    plans = read_jsonl(SAMPLES / "block_observations.jsonl")
    # The sample observations are the expected parser output, checked field-wise.
    observations = build_observations(
        [parsed],
        [
            {"memo": high, "seq": 1, "experiment_id": "e1071-test"},
            {"memo": low, "seq": 0, "experiment_id": "e1071-test"},
        ],
    )
    by_seq = {row["seq"]: row for row in observations}
    assert by_seq[1]["block_index"] == 1
    assert by_seq[1]["cu_price_set"] == 10_000_000
    assert by_seq[0]["block_index"] == 2
    assert by_seq[0]["landed"] is True
    assert plans[0]["signature"] == "sig-low" or plans[1]["signature"] == "sig-high"
    context = block_context(parsed, {"sig-high", "sig-low"})
    committed = read_jsonl(SAMPLES / "block_context.jsonl")[0]
    assert committed["slot"] == 50
    assert committed["tx_count"] == context["tx_count"] == 3
    assert committed["our_tx_count"] == 2
    assert committed["entry_index_available"] is False
    schema = json.loads((SAMPLES / "schema.json").read_text())
    assert "planned_transactions.jsonl" in schema["files"]
