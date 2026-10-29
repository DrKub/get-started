"""Dry-run pipeline never signs, and the live loop obeys the cap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from measure.config import make_config
from measure.live import execute_live
from measure.offline_snapshot import build_offline_snapshot
from measure.pipeline import run_measure
from measure.snapshot_io import write_snapshot
from measure.tpu import parse_host_port, quic_configuration
from measure.txbuild import sign_transaction
import measure.txbuild as txbuild
from solders.keypair import Keypair
from solders.transaction import Transaction


class _Rpc:
    def __init__(self, leader: str, blockhash: str):
        self.leader = leader
        self.blockhash = blockhash

    def get_slot_leader(self, slot: int) -> str:
        del slot
        return self.leader

    def get_latest_blockhash(self) -> str:
        return self.blockhash


class _Sender:
    def __init__(self):
        self.calls = []

    def send_payloads(self, payloads, *, destination: str) -> None:
        self.calls.append((destination, list(payloads)))


def _config(tmp_path: Path, **kwargs):
    fixture = tmp_path / "fixture"
    write_snapshot(fixture, build_offline_snapshot())
    return make_config(
        fixture_dir=str(fixture),
        output_dir=str(tmp_path / "out"),
        experiment_id="e1071-test",
        rpc_url="http://127.0.0.1:9",
        **kwargs,
    )


def test_dry_run_writes_plan_and_does_not_sign(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("dry-run tried to sign")

    monkeypatch.setattr(txbuild, "sign_transaction", boom)
    code = run_measure(_config(tmp_path))
    assert code == 0
    out = tmp_path / "out"
    summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert summary["dry_run"] is True
    assert summary["signed_count"] == 0
    assert summary["sent_count"] == 0
    assert summary["aborted"] is None
    assert manifest["selection_ok"] is True
    assert manifest["alpenglow"]["regime"] == "after"
    assert manifest["spend"]["planned_fee_lamports"] == 8_928_000
    assert manifest["spend"]["planned_fee_lamports"] <= manifest["spend"]["hard_cap_lamports"]
    assert len(manifest["client_families"]) >= 3
    rows = (out / "planned_transactions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 216
    first = json.loads(rows[0])
    assert first["signed"] is False
    assert first["sent"] is False
    assert first["payer_source"] == "dry-run-placeholder"
    assert first["memo"].startswith("ib|1|e1071-test|")
    assert first["memo"].encode() in __import__("base64").b64decode(first["unsigned_message_base64"])
    assert not (out / "sent_transactions.jsonl").exists()


def test_spend_cap_aborts_before_any_send(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("capped run tried to sign")

    monkeypatch.setattr(txbuild, "sign_transaction", boom)
    code = run_measure(_config(tmp_path, spend_cap_sol="0.000000001"))
    assert code == 3
    summary = json.loads((tmp_path / "out" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["aborted"] == "spend_cap"
    assert summary["sent_count"] == 0


def test_before_alpenglow_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(txbuild, "sign_transaction", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("sign")))
    snapshot = build_offline_snapshot()
    snapshot.epoch = 1000
    fixture = tmp_path / "fixture"
    write_snapshot(fixture, snapshot)
    config = make_config(
        fixture_dir=str(fixture),
        output_dir=str(tmp_path / "out"),
        experiment_id="e1000-test",
        rpc_url="http://127.0.0.1:9",
    )
    assert run_measure(config) == 4
    summary = json.loads((tmp_path / "out" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["aborted"] == "regime"
    assert summary["regime"] == "before"


def test_env_cannot_enable_live(monkeypatch):
    monkeypatch.setenv("LIVE", "1")
    monkeypatch.setenv("ALLOW_LIVE", "1")
    config = make_config()
    assert config.live is False


def test_signed_transaction_fits_tpu_window():
    from measure.plan import build_experiment

    plan = build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tpu_repeats=2,
        rpc_repeats=1,
        tail_is_complete=True,
    )
    keypair = Keypair()
    raw, signature = sign_transaction(plan.txs[0], keypair, plan.snapshot.blockhash)
    parsed = Transaction.from_bytes(raw)
    assert parsed.is_signed()
    assert str(parsed.signatures[0]) == signature
    assert len(raw) <= 1232


def test_live_sends_a_fitting_group_and_stops_on_the_cap(monkeypatch):
    from measure.plan import build_experiment

    plan = build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tpu_repeats=1,
        rpc_repeats=1,
        tail_is_complete=True,
    )
    # One window, two slot groups: ladder then arrival.
    identity = plan.targets[0].identity
    window = plan.targets[0].assigned[0].start_slot
    plan.txs = [
        tx
        for tx in plan.txs
        if tx.identity == identity and tx.window_start_slot == window and tx.arm in {"ladder", "arrival"}
    ]
    ladder_fee = sum(tx.fee_lamports for tx in plan.txs if tx.arm == "ladder")
    monkeypatch.setattr("measure.live.wait_for_slot", lambda rpc, target, **kwargs: target)
    sender = _Sender()
    rpc_sender = _Sender()
    keypair = Keypair()
    result = execute_live(
        plan,
        keypair=keypair,
        rpc=_Rpc(identity, plan.snapshot.blockhash),
        tpu_sender=sender,
        rpc_sender=rpc_sender,
        cap_lamports=ladder_fee,
        rpc_destination="rpc.example",
    )
    assert result.aborted_reason == "spend_cap"
    assert result.sent_count == 6
    assert len(sender.calls) == 1
    assert rpc_sender.calls == []
    assert result.spent_lamports == ladder_fee
    assert all(row["sent"] for row in result.rows if row["arm"] == "ladder")
    assert all(row["error"] == "spend_cap" for row in result.rows if row["arm"] == "arrival")


def test_missed_window_is_not_charged(monkeypatch):
    from measure.plan import build_experiment

    plan = build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tpu_repeats=1,
        rpc_repeats=1,
        tail_is_complete=True,
    )
    plan.txs = plan.txs[:1]
    monkeypatch.setattr("measure.live.wait_for_slot", lambda rpc, target, **kwargs: target + 10)
    sender = _Sender()
    result = execute_live(
        plan,
        keypair=Keypair(),
        rpc=_Rpc(plan.txs[0].identity, plan.snapshot.blockhash),
        tpu_sender=sender,
        rpc_sender=_Sender(),
        cap_lamports=50_000_000,
        rpc_destination="rpc.example",
    )
    assert sender.calls == []
    assert result.sent_count == 0
    assert result.spent_lamports == 0
    assert result.rows[0]["error"] == "missed_window"


def test_quic_config_and_host_parse():
    import ssl

    configuration = quic_configuration()
    assert configuration.is_client
    assert configuration.alpn_protocols == ["solana-tpu"]
    assert configuration.verify_mode == ssl.CERT_NONE
    assert parse_host_port("203.0.113.10:8003") == ("203.0.113.10", 8003)
    assert parse_host_port("[2001:db8::1]:8003") == ("2001:db8::1", 8003)


def test_cli_fixture_ignores_dead_rpc(tmp_path):
    from measure.cli import main

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
