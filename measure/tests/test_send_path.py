"""Certificate, slot decision, ledger repair, and attempt collection."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from measure.collector import slots_to_fetch
from measure.ledger import SpendLedger
from measure.slots import send_decision, slot_from_message
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
    assert slot_from_message({"result": 7}) == 7
    assert slot_from_message({"method": "slotNotification", "params": {}}) is None


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
