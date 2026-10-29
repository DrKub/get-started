"""Conflict and non-conflict ladders.

Both ladders use one signature, the same compute-unit limit, and a one-lamport
transfer. Only the priority fee changes. They are sent low-fee first, in
different slots of the same four-slot window. Probes carry no memo: the
signature recorded at send time is the identity inside the block.

The conflict ladder is a one-lamport self-transfer from the funder, so every
probe write-locks that one account. The non-conflict ladder uses a distinct
payer on every rung, also a self-transfer, and those accounts are swept back.
"""

from __future__ import annotations

import re

from solders.keypair import Keypair

from measure.budget import fit_rungs, funding_principal_lamports, payer_deposit, probe_fee
from measure.constants import (
    ALPENGLOW_ACTIVATION_EPOCH,
    ALPENGLOW_CUTOFF_SLOT,
    CONFLICT_SLOT_OFFSET,
    CU_LIMIT,
    DEFAULT_RENT_LAMPORTS,
    NONCONFLICT_SLOT_OFFSET,
    SIGNATURE_FEE_LAMPORTS,
    TRANSFER_LAMPORTS,
)
from measure.fees import cu_price_for_priority_fee, priority_score
from measure.models import (
    AssignedWindow,
    ClusterSnapshot,
    ExperimentPlan,
    PlannedTx,
    Target,
)
from measure.select import build_targets


def alpenglow_regime(snapshot: ClusterSnapshot) -> str:
    if snapshot.source == "fixture":
        return "after" if snapshot.epoch >= ALPENGLOW_ACTIVATION_EPOCH else "before"
    if snapshot.observed_slot >= ALPENGLOW_CUTOFF_SLOT:
        return "after"
    return "before"


def default_experiment_id(snapshot: ClusterSnapshot) -> str:
    stamp = re.sub(r"[^0-9TZ]", "", snapshot.fetched_at)
    return f"e{snapshot.epoch}-{stamp or 'na'}"


def _require_unique_scores(fees: tuple[int, ...]) -> dict[int, int]:
    scores = {fee: priority_score(CU_LIMIT, fee) for fee in fees}
    if len(set(scores.values())) != len(scores):
        raise RuntimeError(f"priority scores are not unique: {scores}")
    return scores


def _tx(
    *,
    experiment_id: str,
    seq: int,
    ladder: str,
    role: str,
    target: Target | None,
    window: AssignedWindow | None,
    planned_slot: int,
    slot_offset: int,
    send_index: int,
    priority_fee: int,
    payer: str,
    recipient: str,
    transfer: int,
    tpu_quic: str,
    scores: dict[int, int],
) -> PlannedTx:
    price = cu_price_for_priority_fee(CU_LIMIT, priority_fee) if role == "probe" else 0
    fee = probe_fee(priority_fee) if role == "probe" else SIGNATURE_FEE_LAMPORTS
    identity = target.identity if target else ""
    start = window.start_slot if window else planned_slot
    length = window.length if window else 1
    return PlannedTx(
        experiment_id=experiment_id,
        seq=seq,
        arm=ladder,
        arm_group=ladder,
        path="tpu",
        repeat_index=0,
        identity=identity,
        client_id=target.client_id if target else "",
        client_family=target.client_family if target else "",
        version=target.version if target else "",
        window_id=f"{identity}:{start}" if identity else f"funding:{planned_slot}",
        window_start_slot=start,
        window_length=length,
        planned_slot=planned_slot,
        slot_offset=slot_offset,
        arms_collapsed=False,
        send_index=send_index,
        cu_limit=CU_LIMIT,
        cu_price_micro_lamports=price,
        priority_fee_lamports=priority_fee,
        signature_fee_lamports=SIGNATURE_FEE_LAMPORTS,
        fee_lamports=fee,
        tpu_quic=tpu_quic,
        payer=payer,
        recipient=recipient,
        transfer_lamports=transfer,
        ladder=ladder,
        role=role,
        priority_score=scores.get(priority_fee, priority_score(CU_LIMIT, 0)) if role == "probe" else 0,
    )


def build_experiment(
    snapshot: ClusterSnapshot,
    *,
    experiment_id: str | None,
    min_lead_slots: int,
    target_validators: int,
    min_client_families: int,
    tail_is_complete: bool,
    cap_remaining_lamports: int,
    rent_lamports: int = DEFAULT_RENT_LAMPORTS,
    main_payer: str,
) -> ExperimentPlan:
    exp_id = experiment_id or default_experiment_id(snapshot)
    targets, excluded, notes, ok, reason = build_targets(
        snapshot,
        min_lead_slots=min_lead_slots,
        target_validators=target_validators,
        min_client_families=min_client_families,
        tail_is_complete=tail_is_complete,
    )
    conflict_rungs, nonconflict_rungs = fit_rungs(
        n_leaders=max(len(targets), 1),
        cap_remaining=cap_remaining_lamports,
        rent_lamports=rent_lamports,
    )
    if len(nonconflict_rungs) < len(conflict_rungs):
        notes.append(
            "shortened the non-conflict ladder to "
            f"{len(nonconflict_rungs)} rungs so net fees stay within 0.0006 SOL "
            "and peak capital, including rent that sweep returns, stays within 0.003 SOL"
        )
    scores = _require_unique_scores(tuple(dict.fromkeys(conflict_rungs + nonconflict_rungs)) or (0,))
    payer_keys = [Keypair() for _ in nonconflict_rungs]
    payers = [str(key.pubkey()) for key in payer_keys]
    signers = {pubkey: key for pubkey, key in zip(payers, payer_keys)}
    txs: list[PlannedTx] = []
    seq = 0
    warnings: list[str] = []
    regime = alpenglow_regime(snapshot)
    if regime != "after":
        warnings.append(
            f"observed slot {snapshot.observed_slot} is below the Alpenglow cutoff "
            f"{ALPENGLOW_CUTOFF_SLOT}"
        )

    funding_slot = _funding_slot(snapshot, targets, min_lead_slots)
    funding_tpu, funding_leader = _leader_tpu(snapshot, funding_slot)
    if funding_leader and not funding_tpu:
        warnings.append(f"funding slot {funding_slot} leader {funding_leader} has no tpuQuic")

    # Fund each non-conflict payer from the main account. Conflict probes
    # spend from that account directly and do not create another one.
    for index, (payer, fee) in enumerate(zip(payers, nonconflict_rungs)):
        amount = payer_deposit(fee, n_leaders=max(len(targets), 1), rent_lamports=rent_lamports)
        txs.append(
            _tx(
                experiment_id=exp_id,
                seq=seq,
                ladder="funding",
                role="funding",
                target=None,
                window=None,
                planned_slot=funding_slot,
                slot_offset=0,
                send_index=index,
                priority_fee=0,
                payer=main_payer,
                recipient=payer,
                transfer=amount,
                tpu_quic=funding_tpu,
                scores=scores,
            )
        )
        seq += 1

    for target in targets:
        window = target.assigned[0]
        for send_index, fee in enumerate(conflict_rungs):
            txs.append(
                _tx(
                    experiment_id=exp_id,
                    seq=seq,
                    ladder="conflict",
                    role="probe",
                    target=target,
                    window=window,
                    planned_slot=window.start_slot + CONFLICT_SLOT_OFFSET,
                    slot_offset=CONFLICT_SLOT_OFFSET,
                    send_index=send_index,
                    priority_fee=fee,
                    payer=main_payer,
                    recipient=main_payer,
                    transfer=TRANSFER_LAMPORTS,
                    tpu_quic=target.tpu_quic,
                    scores=scores,
                )
            )
            seq += 1
        for send_index, fee in enumerate(nonconflict_rungs):
            txs.append(
                _tx(
                    experiment_id=exp_id,
                    seq=seq,
                    ladder="nonconflict",
                    role="probe",
                    target=target,
                    window=window,
                    planned_slot=window.start_slot + NONCONFLICT_SLOT_OFFSET,
                    slot_offset=NONCONFLICT_SLOT_OFFSET,
                    send_index=send_index,
                    priority_fee=fee,
                    payer=payers[send_index],
                    recipient=payers[send_index],
                    transfer=TRANSFER_LAMPORTS,
                    tpu_quic=target.tpu_quic,
                    scores=scores,
                )
            )
            seq += 1

    n_leaders = len(targets)
    # Sweep fees are not transactions in the probe list. They still leave the funder.
    fee_total = sum(tx.fee_lamports for tx in txs) + len(nonconflict_rungs) * SIGNATURE_FEE_LAMPORTS
    principal = funding_principal_lamports(
        n_leaders=n_leaders,
        conflict_rungs=conflict_rungs,
        nonconflict_rungs=nonconflict_rungs,
        rent_lamports=rent_lamports,
    )
    plan = ExperimentPlan(
        experiment_id=exp_id,
        targets=targets,
        txs=txs,
        exclusion_counts=excluded,
        notes=notes,
        selection_ok=ok,
        selection_reason=reason,
        regime=regime,
        regime_ok=regime == "after",
        total_fee_lamports=fee_total,
        funding_principal_lamports=principal if ok else 0,
        projected_outlay_lamports=(fee_total + principal) if ok else fee_total,
        conflict_rungs=conflict_rungs,
        nonconflict_rungs=nonconflict_rungs,
        snapshot=snapshot,
        warnings=warnings,
    )
    # In-memory until a live run writes them under DATA_DIR/keys (mode 0600).
    plan.ephemeral_signers = signers  # type: ignore[attr-defined]
    return plan


def _funding_slot(snapshot: ClusterSnapshot, targets: list[Target], min_lead_slots: int) -> int:
    if not targets:
        return snapshot.observed_slot + min_lead_slots
    earliest = min(target.assigned[0].start_slot for target in targets)
    measured = set()
    for target in targets:
        start = target.assigned[0].start_slot
        measured.add(start + CONFLICT_SLOT_OFFSET)
        measured.add(start + NONCONFLICT_SLOT_OFFSET)
    candidate = earliest - 4
    floor = snapshot.observed_slot + min_lead_slots
    if candidate >= floor and candidate not in measured:
        return candidate
    # Walk the fetched schedule for the earliest eligible non-measurement slot.
    for offset, _leader in enumerate(snapshot.leaders):
        slot = snapshot.schedule_start_slot + offset
        if slot >= floor and slot not in measured:
            return slot
    return floor


def _leader_tpu(snapshot: ClusterSnapshot, slot: int) -> tuple[str, str]:
    index = slot - snapshot.schedule_start_slot
    if index < 0 or index >= len(snapshot.leaders):
        return "", ""
    identity = snapshot.leaders[index]
    for node in snapshot.nodes:
        if node.get("pubkey") == identity and isinstance(node.get("tpuQuic"), str):
            return node["tpuQuic"], identity
    return "", identity
