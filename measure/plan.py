"""Turn a cluster snapshot into a pre-registered transaction plan.

Ranks are hypotheses written down before any send. ``rank_*`` is within the
whole leader window (the right comparison if every arm collapses into one
block). ``rank_arm_*`` is within the arm group (the right comparison when
ladder, arrival, and metric land in different slots). Rank 0 is expected
earliest. Ties break by ascending send index.
"""

from __future__ import annotations

import re

from measure.constants import (
    ALPENGLOW_ACTIVATION_EPOCH,
    ARRIVAL_COUNT,
    ARRIVAL_CU_LIMIT,
    ARRIVAL_PRIORITY_FEE,
    ARRIVAL_SLOT_OFFSET,
    LADDER_CU_LIMIT,
    LADDER_PRIORITY_FEES,
    LADDER_SLOT_OFFSET,
    METRIC_PRICE_CU_LIMIT,
    METRIC_PRICE_FEE,
    METRIC_SLOT_OFFSET,
    METRIC_TOTAL_CU_LIMIT,
    METRIC_TOTAL_FEE,
    ROTATION_SLOTS,
    SIGNATURE_FEE_LAMPORTS,
)
from measure.fees import cu_price_for_priority_fee, transaction_fee_lamports
from measure.models import (
    AssignedWindow,
    ClusterSnapshot,
    ExperimentPlan,
    PlannedTx,
    Target,
)
from measure.select import build_targets


def alpenglow_regime(epoch: int) -> str:
    return "after" if epoch >= ALPENGLOW_ACTIVATION_EPOCH else "before"


def window_fee_lamports() -> int:
    """Fee estimate for one full leader window (all three arms)."""
    total = 0
    for fee in LADDER_PRIORITY_FEES:
        price = cu_price_for_priority_fee(LADDER_CU_LIMIT, fee)
        total += transaction_fee_lamports(LADDER_CU_LIMIT, price)
    for _ in range(ARRIVAL_COUNT):
        price = cu_price_for_priority_fee(ARRIVAL_CU_LIMIT, ARRIVAL_PRIORITY_FEE)
        total += transaction_fee_lamports(ARRIVAL_CU_LIMIT, price)
    for limit, fee in (
        (METRIC_PRICE_CU_LIMIT, METRIC_PRICE_FEE),
        (METRIC_TOTAL_CU_LIMIT, METRIC_TOTAL_FEE),
    ):
        price = cu_price_for_priority_fee(limit, fee)
        total += transaction_fee_lamports(limit, price)
    return total


def default_experiment_id(snapshot: ClusterSnapshot) -> str:
    stamp = re.sub(r"[^0-9TZ]", "", snapshot.fetched_at)
    experiment_id = f"e{snapshot.epoch}-{stamp or 'na'}"
    return experiment_id


def _place(offset: int, length: int) -> tuple[int, bool]:
    if length >= ROTATION_SLOTS and offset < length:
        return offset, False
    return 0, True


def _rank_into(txs: list[PlannedTx], key, attr: str) -> None:
    ordered = sorted(txs, key=key)
    for index, tx in enumerate(ordered):
        setattr(tx, attr, index)


def _assign_ranks(txs: list[PlannedTx]) -> None:
    _rank_into(
        txs,
        lambda tx: (-tx.cu_price_micro_lamports, tx.send_index, tx.seq),
        "rank_cu_price",
    )
    _rank_into(
        txs,
        lambda tx: (-tx.priority_fee_lamports, tx.send_index, tx.seq),
        "rank_priority_fee",
    )
    _rank_into(txs, lambda tx: (tx.send_index, tx.seq), "rank_send_order")
    groups: dict[str, list[PlannedTx]] = {}
    for tx in txs:
        groups.setdefault(tx.arm_group, []).append(tx)
    for group in groups.values():
        _rank_into(
            group,
            lambda tx: (-tx.cu_price_micro_lamports, tx.send_index, tx.seq),
            "rank_arm_cu_price",
        )
        _rank_into(
            group,
            lambda tx: (-tx.priority_fee_lamports, tx.send_index, tx.seq),
            "rank_arm_priority_fee",
        )
        _rank_into(group, lambda tx: (tx.send_index, tx.seq), "rank_arm_send_order")


def _make(
    *,
    experiment_id: str,
    seq: int,
    arm: str,
    target: Target,
    window: AssignedWindow,
    send_index: int,
    cu_limit: int,
    priority_fee: int,
    slot_offset: int,
    arms_collapsed: bool,
) -> PlannedTx:
    cu_price = cu_price_for_priority_fee(cu_limit, priority_fee)
    fee = transaction_fee_lamports(cu_limit, cu_price)
    planned_slot = window.start_slot + slot_offset
    return PlannedTx(
        experiment_id=experiment_id,
        seq=seq,
        arm=arm,
        arm_group="metric" if arm.startswith("metric") else arm,
        path=window.path,
        repeat_index=window.repeat_index,
        identity=target.identity,
        client_id=target.client_id,
        client_family=target.client_family,
        version=target.version,
        window_id=f"{target.identity}:{window.start_slot}",
        window_start_slot=window.start_slot,
        window_length=window.length,
        planned_slot=planned_slot,
        slot_offset=slot_offset,
        arms_collapsed=arms_collapsed,
        send_index=send_index,
        cu_limit=cu_limit,
        cu_price_micro_lamports=cu_price,
        priority_fee_lamports=priority_fee,
        signature_fee_lamports=SIGNATURE_FEE_LAMPORTS,
        fee_lamports=fee,
        tpu_quic=target.tpu_quic,
    )


def txs_for_window(
    target: Target,
    window: AssignedWindow,
    *,
    experiment_id: str,
    seq_start: int,
) -> list[PlannedTx]:
    """One leader window: ladder, then arrival, then the metric pair.

    Ladder fees increase with send index, so a priority rule reverses send
    order. The metric pair is sent price-first on even repeats and total-first
    on odd repeats; the two priority hypotheses do not flip with that swap.
    """
    txs: list[PlannedTx] = []
    seq = seq_start
    send_index = 0
    collapsed_any = False

    def add(arm: str, cu_limit: int, priority_fee: int, offset: int) -> None:
        nonlocal seq, send_index, collapsed_any
        placed, collapsed = _place(offset, window.length)
        collapsed_any = collapsed_any or collapsed
        txs.append(
            _make(
                experiment_id=experiment_id,
                seq=seq,
                arm=arm,
                target=target,
                window=window,
                send_index=send_index,
                cu_limit=cu_limit,
                priority_fee=priority_fee,
                slot_offset=placed,
                arms_collapsed=collapsed,
            )
        )
        seq += 1
        send_index += 1

    for fee in LADDER_PRIORITY_FEES:
        add("ladder", LADDER_CU_LIMIT, fee, LADDER_SLOT_OFFSET)
    for _ in range(ARRIVAL_COUNT):
        add("arrival", ARRIVAL_CU_LIMIT, ARRIVAL_PRIORITY_FEE, ARRIVAL_SLOT_OFFSET)
    metric = [
        ("metric-price", METRIC_PRICE_CU_LIMIT, METRIC_PRICE_FEE),
        ("metric-total", METRIC_TOTAL_CU_LIMIT, METRIC_TOTAL_FEE),
    ]
    if window.repeat_index % 2 == 1:
        metric.reverse()
    for arm, limit, fee in metric:
        add(arm, limit, fee, METRIC_SLOT_OFFSET)
    if collapsed_any:
        for tx in txs:
            tx.arms_collapsed = True
    _assign_ranks(txs)
    return txs


def build_experiment(
    snapshot: ClusterSnapshot,
    *,
    experiment_id: str | None,
    min_lead_slots: int,
    target_validators: int,
    min_client_families: int,
    tpu_repeats: int,
    rpc_repeats: int,
    tail_is_complete: bool,
) -> ExperimentPlan:
    exp_id = experiment_id or default_experiment_id(snapshot)
    targets, excluded, notes, ok, reason = build_targets(
        snapshot,
        min_lead_slots=min_lead_slots,
        target_validators=target_validators,
        min_client_families=min_client_families,
        tpu_repeats=tpu_repeats,
        rpc_repeats=rpc_repeats,
        tail_is_complete=tail_is_complete,
    )
    txs: list[PlannedTx] = []
    seq = 0
    for target in targets:
        for window in target.assigned:
            batch = txs_for_window(
                target, window, experiment_id=exp_id, seq_start=seq
            )
            txs.extend(batch)
            seq += len(batch)
    regime = alpenglow_regime(snapshot.epoch)
    warnings: list[str] = []
    if regime != "after":
        warnings.append(
            f"epoch {snapshot.epoch} is before Alpenglow activation "
            f"epoch {ALPENGLOW_ACTIVATION_EPOCH}; this run will not measure"
        )
    return ExperimentPlan(
        experiment_id=exp_id,
        targets=targets,
        txs=txs,
        exclusion_counts=excluded,
        notes=notes,
        selection_ok=ok,
        selection_reason=reason,
        regime=regime,
        regime_ok=regime == "after",
        total_fee_lamports=sum(tx.fee_lamports for tx in txs),
        snapshot=snapshot,
        warnings=warnings,
    )
