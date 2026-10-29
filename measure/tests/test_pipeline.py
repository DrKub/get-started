"""Dry-run writes the run directory and does not submit or touch the ledger."""

from __future__ import annotations

import json
from pathlib import Path

from measure.cli import main
from measure.config import make_config
from measure.offline_snapshot import build_offline_snapshot
from measure.pipeline import run_measure
from measure.snapshot_io import write_snapshot
from measure.tpu import parse_host_port, quic_configuration
from solders.keypair import Keypair
from solders.transaction import Transaction

from measure.constants import BURST_OFFSET_SLOTS, CU_LIMIT, QUIC_WARMUP_SLOTS
from measure.plan import build_experiment
from measure.txbuild import sign_transaction


def _config(tmp_path: Path, **kwargs):
    fixture = tmp_path / "fixture"
    write_snapshot(fixture, build_offline_snapshot())
    return make_config(
        fixture_dir=str(fixture),
        output_dir=str(tmp_path / "out"),
        data_dir=str(tmp_path / "data"),
        experiment_id="e1071-test",
        rpc_url="http://127.0.0.1:9",
        min_lead_slots=16,
        **kwargs,
    )


def test_dry_run_layout_signs_without_submitting_or_charging(tmp_path, monkeypatch):
    monkeypatch.delenv("MODE", raising=False)
    code = run_measure(_config(tmp_path))
    assert code == 0
    out = tmp_path / "out"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    spend = json.loads((out / "spend.json").read_text(encoding="utf-8"))
    attempts = (out / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert manifest["mode"] == "dry-run"
    assert manifest["quic_warmup_slots"] == QUIC_WARMUP_SLOTS
    assert manifest["burst_offset_slots"] == BURST_OFFSET_SLOTS
    assert manifest["cu_limit"] == CU_LIMIT
    assert manifest["transaction_format"] == "legacy+compute-budget"
    assert manifest["max_supported_transaction_version"] == 1
    assert manifest["design"]["retries_inside_window"] is False
    assert manifest["design"]["submit_path"] == "tpu"
    assert len(json.loads((out / "validators.json").read_text(encoding="utf-8"))) == 6
    assert spend["counted_against_cap"] is False
    assert spend["planned_fee_lamports"] == manifest["spend"]["planned_fee_lamports"]
    assert spend["net_lamports"] <= 600_000
    assert spend["peak_lamports"] <= 3_000_000
    assert spend["projected_outlay_lamports"] == spend["peak_lamports"]
    assert not (tmp_path / "data" / "spend.json").exists()
    assert (out / "blocks").is_dir()
    assert (out / "run.log").read_text(encoding="utf-8").startswith("DRY-RUN")
    rows = [json.loads(line) for line in attempts]
    assert rows
    assert all(row["submitted"] is False and row["signature"] for row in rows)
    assert {row["ladder"] for row in rows} >= {"conflict", "nonconflict", "funding"}


def test_mode_live_is_the_same_switch_as_the_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "live")
    monkeypatch.setenv("LIVE", "1")
    config = make_config(data_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
    assert config.live is True
    assert config.mode == "live"
    monkeypatch.setenv("MODE", "sometimes")
    config = make_config(data_dir=str(tmp_path))
    assert config.live is False
    assert config.mode_warning
    monkeypatch.delenv("MODE")
    assert make_config(data_dir=str(tmp_path), live=True).live is True


def test_signed_probe_fits_the_tpu_window():
    plan = build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tail_is_complete=True,
        cap_remaining_lamports=50_000_000,
        main_payer=str(Keypair().pubkey()),
    )
    # Re-sign the conflict probes with a key that matches a freshly built payer.
    keypair = Keypair()
    probe = next(tx for tx in plan.txs if tx.ladder == "conflict")
    probe.payer = str(keypair.pubkey())
    raw, signature = sign_transaction(probe, keypair, plan.snapshot.blockhash)
    parsed = Transaction.from_bytes(raw)
    assert parsed.is_signed()
    assert str(parsed.signatures[0]) == signature
    assert len(raw) <= 1232


def test_quic_config_and_host_parse():
    import ssl

    configuration = quic_configuration()
    assert configuration.is_client
    assert configuration.alpn_protocols == ["solana-tpu"]
    assert configuration.verify_mode == ssl.CERT_NONE
    assert parse_host_port("203.0.113.10:8003") == ("203.0.113.10", 8003)
    assert parse_host_port("[2001:db8::1]:8003") == ("2001:db8::1", 8003)


def test_cli_fixture_ignores_dead_rpc(tmp_path, monkeypatch):
    monkeypatch.delenv("MODE", raising=False)
    fixture = tmp_path / "fixture"
    write_snapshot(fixture, build_offline_snapshot())
    code = main(
        [
            "run",
            "--fixture-dir",
            str(fixture),
            "--output-dir",
            str(tmp_path / "out"),
            "--rpc-url",
            "http://127.0.0.1:9",
            "--experiment-id",
            "e1071-test",
            "--min-lead-slots",
            "16",
        ]
    )
    assert code == 0
    code = main(["run", "--spend-cap-sol", "0.06", "--fixture-dir", str(fixture)])
    assert code == 1
    code = main(
        [
            "run",
            "--live",
            "--fixture-dir",
            str(fixture),
            "--keypair",
            str(tmp_path / "id.json"),
        ]
    )
    assert code == 1


def test_key_command_prints_pubkey_without_sending(tmp_path, monkeypatch):
    keypair = Keypair()
    path = tmp_path / "id.json"
    path.write_text(json.dumps(list(bytes(keypair))), encoding="utf-8")

    class _Rpc:
        def __init__(self, url, timeout_s=30):
            self.url = url

        def call(self, method, params=None, attempts=4):
            assert method == "getBalance"
            assert params[0] == str(keypair.pubkey())
            return {"value": 42}

    monkeypatch.setattr("measure.keyinfo.RpcClient", _Rpc)
    code = main(["key", "--keypair", str(path), "--rpc-url", "http://127.0.0.1:9"])
    assert code == 0
