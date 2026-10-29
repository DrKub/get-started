"""Shared records. JSON at the edges is plain dicts produced from these."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClusterSnapshot:
    epoch: int
    schedule_start_slot: int
    observed_slot: int
    slot_index: int
    slots_in_epoch: int
    blockhash: str
    block_time: int | None
    fetched_at: str
    nodes: list[dict]
    leaders: list[str]
    source: str


@dataclass(frozen=True)
class LeaderWindow:
    identity: str
    start_slot: int
    length: int
    partial: bool


@dataclass(frozen=True)
class AssignedWindow:
    start_slot: int
    length: int
    path: str
    repeat_index: int
    partial: bool


@dataclass
class Target:
    identity: str
    client_id: str
    client_family: str
    version: str
    feature_set: int | None
    tpu_quic: str
    gossip: str | None
    windows_available: int
    assigned: list[AssignedWindow]
    vote_account: str | None = None
    active_stake_lamports: int = 0
    jito: bool | None = None
    classification_source: str = "rpc"
    outside_top10_stake: bool = False


@dataclass
class PlannedTx:
    experiment_id: str
    seq: int
    arm: str
    arm_group: str
    path: str
    repeat_index: int
    identity: str
    client_id: str
    client_family: str
    version: str
    window_id: str
    window_start_slot: int
    window_length: int
    planned_slot: int
    slot_offset: int
    arms_collapsed: bool
    send_index: int
    cu_limit: int
    cu_price_micro_lamports: int
    priority_fee_lamports: int
    signature_fee_lamports: int
    fee_lamports: int
    tpu_quic: str
    rank_cu_price: int = -1
    rank_priority_fee: int = -1
    rank_send_order: int = -1
    rank_arm_cu_price: int = -1
    rank_arm_priority_fee: int = -1
    rank_arm_send_order: int = -1
    memo: str = ""
    payer: str = ""
    payer_source: str = ""
    recipient: str = ""
    transfer_lamports: int = 0
    ladder: str = ""
    role: str = "probe"
    priority_score: int = 0
    recent_blockhash: str = ""
    unsigned_message_base64: str = ""
    signature: str | None = None
    signed: bool = False
    sent: bool = False
    submitted: bool = False
    submit_path: str = ""
    error: str | None = None


@dataclass
class ExperimentPlan:
    experiment_id: str
    targets: list[Target]
    txs: list[PlannedTx]
    exclusion_counts: dict[str, int]
    notes: list[str]
    selection_ok: bool
    selection_reason: str | None
    regime: str
    regime_ok: bool
    total_fee_lamports: int
    funding_principal_lamports: int
    projected_outlay_lamports: int
    conflict_rungs: tuple[int, ...]
    nonconflict_rungs: tuple[int, ...]
    snapshot: ClusterSnapshot
    warnings: list[str] = field(default_factory=list)
