"""Live send loop. Imported only after ``--live`` has been accepted.

Fees are reserved for a slot-group before any signature is produced. A group
that does not fit in the remaining cap is not partially sent. Missed windows
and leader mismatches are not charged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from measure.errors import SpendCapExceeded
from measure.fees import SpendGuard
from measure.models import ExperimentPlan, PlannedTx
from measure.txbuild import sign_transaction


@dataclass
class SlotGroup:
    planned_slot: int
    path: str
    identity: str
    destination: str
    txs: list[PlannedTx]


@dataclass
class LiveResult:
    rows: list[dict] = field(default_factory=list)
    spent_lamports: int = 0
    aborted_reason: str | None = None
    sent_count: int = 0
    signed_count: int = 0


def group_transactions(txs: list[PlannedTx], *, rpc_destination: str) -> list[SlotGroup]:
    grouped: dict[tuple, SlotGroup] = {}
    for tx in sorted(txs, key=lambda item: (item.planned_slot, item.send_index, item.seq)):
        destination = tx.tpu_quic if tx.path == "tpu" else rpc_destination
        key = (tx.planned_slot, tx.path, tx.identity, destination)
        if key not in grouped:
            grouped[key] = SlotGroup(
                planned_slot=tx.planned_slot,
                path=tx.path,
                identity=tx.identity,
                destination=destination,
                txs=[],
            )
        grouped[key].txs.append(tx)
    return [grouped[key] for key in sorted(grouped)]


def wait_for_slot(rpc, target_slot: int, *, sleep=time.sleep, poll_s: float = 0.4, max_polls: int = 200_000) -> int:
    for _ in range(max_polls):
        slot = int(rpc.get_slot())
        if slot >= target_slot:
            return slot
        sleep(poll_s)
    raise TimeoutError(f"timed out waiting for slot {target_slot}")


def _skipped(tx: PlannedTx, reason: str, slot_now: int | None) -> dict:
    return {
        "arm": tx.arm,
        "client_family": tx.client_family,
        "client_id": tx.client_id,
        "error": reason,
        "experiment_id": tx.experiment_id,
        "fee_lamports": tx.fee_lamports,
        "identity": tx.identity,
        "memo": tx.memo,
        "path": tx.path,
        "planned_slot": tx.planned_slot,
        "repeat_index": tx.repeat_index,
        "send_index": tx.send_index,
        "seq": tx.seq,
        "signature": None,
        "signed": False,
        "slot_sent_at": slot_now,
        "sent": False,
        "version": tx.version,
        "window_id": tx.window_id,
        "window_length": tx.window_length,
        "window_start_slot": tx.window_start_slot,
    }


def execute_live(
    plan: ExperimentPlan,
    *,
    keypair,
    rpc,
    tpu_sender,
    rpc_sender,
    cap_lamports: int,
    rpc_destination: str,
    sleep=time.sleep,
) -> LiveResult:
    result = LiveResult()
    guard = SpendGuard(cap_lamports)
    groups = group_transactions(plan.txs, rpc_destination=rpc_destination)
    for index, group in enumerate(groups):
        slot_now = wait_for_slot(rpc, max(group.planned_slot - 1, 0), sleep=sleep)
        if slot_now > group.planned_slot + 1:
            result.rows.extend(_skipped(tx, "missed_window", slot_now) for tx in group.txs)
            continue
        leader = rpc.get_slot_leader(group.planned_slot)
        if leader != group.identity:
            result.rows.extend(_skipped(tx, "leader_mismatch", slot_now) for tx in group.txs)
            continue
        fees = [tx.fee_lamports for tx in group.txs]
        try:
            guard.approve_batch(fees)
        except SpendCapExceeded:
            result.aborted_reason = "spend_cap"
            result.rows.extend(_skipped(tx, "spend_cap", slot_now) for tx in group.txs)
            for later in groups[index + 1 :]:
                result.rows.extend(_skipped(tx, "spend_cap", slot_now) for tx in later.txs)
            break
        blockhash = rpc.get_latest_blockhash()
        payloads: list[bytes] = []
        signatures: list[str] = []
        try:
            for tx in group.txs:
                raw, signature = sign_transaction(tx, keypair, blockhash)
                payloads.append(raw)
                signatures.append(signature)
                result.signed_count += 1
        except Exception as exc:
            result.rows.extend(
                {
                    **_skipped(tx, f"sign_failed: {exc}", slot_now),
                    "signed": False,
                    "sent": False,
                }
                for tx in group.txs
            )
            continue
        sender = tpu_sender if group.path == "tpu" else rpc_sender
        error = None
        sent = True
        try:
            sender.send_payloads(payloads, destination=group.destination)
        except Exception as exc:
            sent = False
            error = f"send_failed: {exc}"
        if sent:
            result.sent_count += len(group.txs)
        for tx, signature in zip(group.txs, signatures):
            result.rows.append(
                {
                    "arm": tx.arm,
                    "client_family": tx.client_family,
                    "client_id": tx.client_id,
                    "destination": group.destination,
                    "error": error,
                    "experiment_id": tx.experiment_id,
                    "fee_lamports": tx.fee_lamports,
                    "identity": tx.identity,
                    "memo": tx.memo,
                    "path": tx.path,
                    "planned_slot": tx.planned_slot,
                    "repeat_index": tx.repeat_index,
                    "send_index": tx.send_index,
                    "seq": tx.seq,
                    "signature": signature,
                    "signed": True,
                    "slot_sent_at": slot_now,
                    "sent": sent,
                    "version": tx.version,
                    "window_id": tx.window_id,
                    "window_length": tx.window_length,
                    "window_start_slot": tx.window_start_slot,
                }
            )
    result.spent_lamports = guard.spent_lamports
    result.rows.sort(key=lambda row: (row.get("seq") is None, row.get("seq")))
    return result
