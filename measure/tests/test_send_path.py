"""Certificate, slot decision, ledger repair, and attempt collection."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from measure.collector import slots_to_fetch
from measure.config import make_config
from measure.errors import ConfigError, RpcError
from measure.ledger import SpendLedger, group_attempts, landed_fee_lamports
from measure.models import PlannedTx
from measure.pipeline import _attempt_row, _write_collection, describe_submit
from measure.rpc import retry_delay
from measure.slots import (
    block_fetch_action,
    burst_lead_slots,
    fanout_identities,
    is_cluster_slot,
    send_decision,
    slot_from_message,
)
from measure.tpu import quic_configuration


def test_quic_configuration_presents_an_ed25519_client_cert():
    configuration = quic_configuration()
    assert configuration.certificate is not None
    assert configuration.private_key is not None
    public = configuration.private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    assert len(public) == 32
    assert public in configuration.certificate.public_bytes(serialization.Encoding.DER)
    assert configuration.alpn_protocols == ["solana-tpu"]


def test_send_decision_keeps_the_whole_leader_window():
    assert send_decision(90, target_slot=100, window_start=100, window_length=4, burst_offset=1) == "wait"
    assert send_decision(99, target_slot=100, window_start=100, window_length=4, burst_offset=1) == "send"
    assert send_decision(102, target_slot=100, window_start=100, window_length=4, burst_offset=1) == "send"
    assert send_decision(104, target_slot=100, window_start=100, window_length=4, burst_offset=1) == "skip"


def test_slot_notification_parser():
    assert slot_from_message(
        {"method": "slotNotification", "params": {"result": {"slot": 42, "parent": 41}}}
    ) == 42
    # The subscribe ack is a small integer. Treating it as a slot asks for epoch 0.
    assert slot_from_message({"jsonrpc": "2.0", "result": 0, "id": 1}) is None
    assert slot_from_message({"result": 7}) is None
    assert slot_from_message({"method": "slotNotification", "params": {}}) is None
    assert is_cluster_slot(0) is False
    assert is_cluster_slot(462827851) is True


def test_burst_lead_and_funding_fanout():
    # 1.7s handshake / 215 ms slot, rounded up, is 8 slots.
    assert burst_lead_slots(1.7) == 8
    assert burst_lead_slots(0.4) == 2
    assert burst_lead_slots(0) == 1
    assert fanout_identities(["A", "A", "A", "A", "B", "B", "C", "D"]) == ["B", "C", "D"]
    assert fanout_identities(["A", "B"]) == ["B"]
    assert fanout_identities(["only-current"]) == []


def test_block_fetch_action_does_not_treat_slot_429_as_http_429():
    assert block_fetch_action("getBlock: {'code': -32007, 'message': 'Slot 429 was skipped'}") == "skip"
    assert block_fetch_action("getBlock HTTP 429") == "retry"
    assert block_fetch_action("getBlock: {'code': -32005, 'message': 'too many requests'}") == "retry"
    assert block_fetch_action("getBlock: {'code': -32004, 'message': 'Block not available for slot 5'}") == "wait"
    assert retry_delay(0, jitter=0) == 0.25
    assert retry_delay(5, jitter=0) == 8.0
    assert retry_delay(9, jitter=0) == 8.0


def _planned(**overrides) -> PlannedTx:
    fields = dict(
        experiment_id="e",
        seq=0,
        arm="a",
        arm_group="g",
        path="tpu",
        repeat_index=0,
        identity="id",
        client_id="agave",
        client_family="agave",
        version="4",
        window_id="w",
        window_start_slot=100,
        window_length=4,
        planned_slot=100,
        slot_offset=0,
        arms_collapsed=False,
        send_index=0,
        cu_limit=1000,
        cu_price_micro_lamports=0,
        priority_fee_lamports=0,
        signature_fee_lamports=5000,
        fee_lamports=5000,
        tpu_quic="9.9.9.9:8003",
    )
    fields.update(overrides)
    return PlannedTx(**fields)


def test_attempt_row_records_the_socket_that_was_written():
    path, destinations, actual = describe_submit(True, ["1.2.3.4:8003", "5.6.7.8:9000"])
    assert path == "rpc+tpu-fanout"
    assert actual == "1.2.3.4:8003"
    tx = _planned(tpu_quic="9.9.9.9:8003", destinations=destinations, submit_path=path, send_unix_ms=123)
    row = _attempt_row(tx)
    assert row["tpu_host"] == "1.2.3.4"
    assert row["tpu_port"] == 8003
    assert row["destinations"] == destinations
    assert row["send_unix_ms"] == 123
    rpc_only = _planned(destinations=["rpc"], tpu_quic="", submit_path="rpc")
    assert _attempt_row(rpc_only)["tpu_host"] is None


def test_reconcile_run_drops_unlanded_funding_and_keeps_other_runs(tmp_path: Path):
    ledger = SpendLedger(tmp_path / "spend.json", 50_000_000)
    ledger.reserve(10_000, run_id="earlier", seq=0, reason="signed")
    for seq in range(12):
        ledger.reserve(5000, run_id="e1071-20261029T091844Z", seq=seq, reason="signed")
    assert ledger.spent_lamports == 70_000
    rows = [
        {"signature": f"sig{seq}", "fee_lamports": 5000, "submitted": True}
        for seq in range(12)
    ]
    assert landed_fee_lamports(rows, [None] * 12) == 0
    rows[0]["fee_lamports"] = 5000
    statuses = [None] * 12
    statuses[3] = {"err": None, "confirmationStatus": "confirmed"}
    assert landed_fee_lamports(rows, statuses) == 5000
    ledger.reconcile_run("e1071-20261029T091844Z", 0)
    assert ledger.spent_lamports == 10_000
    assert ledger.net_for_run("e1071-20261029T091844Z") == 0
    assert ledger.net_for_run("earlier") == 10_000
    # A second pass with nothing newly landed does not move the earlier run.
    assert ledger.reconcile_run("e1071-20261029T091844Z", 0) == 10_000
    groups = group_attempts(rows, manifest_id="e1071-20261029T091844Z")
    assert list(groups) == ["e1071-20261029T091844Z"]


def test_ws_url_is_separate_from_rpc_url(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WS_URL", "wss://keyed.example/ws?api-key=secret")
    config = make_config(data_dir=str(tmp_path))
    assert config.ws_url == "wss://keyed.example/ws?api-key=secret"
    monkeypatch.setenv("WS_URL", "https://not-a-socket")
    try:
        make_config(data_dir=str(tmp_path))
    except ConfigError as exc:
        assert "WS_URL" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_auto_reconcile_keeps_reservations_when_status_is_unknown(tmp_path: Path):
    from measure.pipeline import _reconcile_submitted

    ledger = SpendLedger(tmp_path / "spend.json", 50_000_000)
    ledger.reserve(60_000, run_id="e", seq=0, reason="signed")
    tx = _planned(experiment_id="e", signature="sig", submitted=True, signed=True, fee_lamports=60_000)

    class Plan:
        experiment_id = "e"
        txs = [tx]

    class FailRpc:
        def get_signature_statuses(self, signatures, search_history=False):
            del signatures, search_history
            raise RpcError("getSignatureStatuses HTTP 429")

    _reconcile_submitted(FailRpc(), Plan(), ledger)
    assert ledger.spent_lamports == 60_000

    class MissRpc:
        def get_signature_statuses(self, signatures, search_history=False):
            del signatures, search_history
            return [None]

    _reconcile_submitted(MissRpc(), Plan(), ledger)
    assert ledger.spent_lamports == 0


def test_collect_skips_and_retries_without_stopping(tmp_path: Path):
    class Rpc:
        def __init__(self):
            self.n: dict[int, int] = {}

        def get_slot(self, timeout_s: float = 5):
            del timeout_s
            return 50

        def get_block(self, slot: int):
            self.n[slot] = self.n.get(slot, 0) + 1
            if slot == 10:
                raise RpcError("getBlock: {'code': -32007, 'message': 'Slot 429 was skipped'}")
            if slot == 11 and self.n[slot] == 1:
                raise RpcError("getBlock HTTP 429")
            if slot == 12 and self.n[slot] == 1:
                raise RpcError("getBlock: {'code': -32004, 'message': 'Block not available for slot 12'}")
            if slot == 13:
                raise RpcError("getBlock: boom")
            return {"transactions": [], "blockTime": 1, "parentSlot": slot - 1, "blockHeight": 1}

    out = tmp_path / "out"
    _write_collection(
        Rpc(),
        [10, 11, 12, 13, 14],
        [],
        [],
        out,
        sleep=lambda _delay: None,
        deadline_s=30,
        max_attempts=3,
    )
    assert (out / "blocks" / "10.skipped.json").is_file()
    assert (out / "blocks" / "11.json").is_file()
    assert (out / "blocks" / "12.json").is_file()
    assert (out / "blocks" / "13.unavailable.json").is_file()
    assert (out / "blocks" / "14.json").is_file()
    assert (out / "block_observations.jsonl").is_file()


def test_ledger_reconcile_drops_unsent_reservations(tmp_path: Path):
    ledger = SpendLedger(tmp_path / "spend.json", 50_000_000)
    for seq in range(84):
        ledger.reserve(5000, run_id="e", seq=seq, reason="signed")
    assert ledger.spent_lamports == 84 * 5000
    attempts = [
        {"seq": seq, "fee_lamports": 5000, "submitted": False, "signature": f"sig{seq}"}
        for seq in range(84)
    ]
    attempts[24]["submitted"] = True
    total = ledger.reconcile(attempts)
    assert total == 5000
    assert SpendLedger(tmp_path / "spend.json", 50_000_000).spent_lamports == 5000
    landed = ledger.reconcile(attempts, landed_signatures=set())
    assert landed == 0
    ledger.reserve(1000, run_id="e", seq=1, reason="signed")
    ledger.reset(reason="reset")
    assert ledger.spent_lamports == 0
    assert json.loads((tmp_path / "spend.json").read_text())["spent_lamports"] == 0


def test_collect_reads_attempt_target_slots():
    slots = slots_to_fetch(
        [
            {"target_slot": 100, "signature": "abc", "submitted": True},
            {"planned_slot": 200, "window_start_slot": 200, "window_length": 4},
        ]
    )
    assert 100 in slots and 103 in slots
    assert 200 in slots and 205 in slots
