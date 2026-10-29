"""Plan, write, and (only with --live) send."""

from __future__ import annotations

import logging
from dataclasses import asdict, fields
from datetime import datetime, timezone

from measure.analyze import summarize_hypotheses
from measure.collector import (
    block_context,
    build_observations,
    parse_block,
    slots_to_fetch,
)
from measure.config import Config
from measure.constants import (
    ALPENGLOW_ACTIVATION_EPOCH,
    ALPENGLOW_ACTIVATION_UTC,
    ALPENGLOW_SOURCE,
    HARD_CAP_LAMPORTS,
    HYPOTHESES,
)
from measure.errors import ConfigError, RpcError, SpendCapExceeded
from measure.fees import SpendGuard, lamports_to_sol_str
from measure.io_util import dump_json, read_jsonl, redact_url, write_csv, write_jsonl
from measure.models import ClusterSnapshot, ExperimentPlan, PlannedTx, Target
from measure.plan import build_experiment, window_fee_lamports
from measure.rpc import RpcClient
from measure.snapshot_io import load_snapshot
from measure.txbuild import attach_unsigned_messages, dry_run_payer, load_keypair

logger = logging.getLogger("measure")

PLANNED_FIELDS = [item.name for item in fields(PlannedTx)]
OBSERVATION_FIELDS = [
    "experiment_id",
    "seq",
    "landed",
    "slot",
    "block_index",
    "entry_index",
    "block_time",
    "signature",
    "identity",
    "client_id",
    "client_family",
    "version",
    "arm",
    "arm_group",
    "path",
    "repeat_index",
    "send_index",
    "window_id",
    "window_start_slot",
    "planned_slot",
    "memo",
    "fee_lamports",
    "priority_fee_lamports",
    "cu_price_micro_lamports",
    "cu_limit",
    "cu_price_set",
    "cu_limit_set",
    "cu_consumed",
    "cost_units",
    "execution_err",
    "fee_payer",
    "tx_version",
    "rank_cu_price",
    "rank_priority_fee",
    "rank_send_order",
    "rank_arm_cu_price",
    "rank_arm_priority_fee",
    "rank_arm_send_order",
]


def fetch_snapshot(rpc: RpcClient, config: Config) -> ClusterSnapshot:
    info = rpc.call("getEpochInfo", [{"commitment": "confirmed"}])
    if not isinstance(info, dict):
        raise RpcError("getEpochInfo returned no object")
    schedule_start = int(info["absoluteSlot"])
    slot_index = int(info["slotIndex"])
    slots_in_epoch = int(info["slotsInEpoch"])
    remaining = max(slots_in_epoch - slot_index, 0)
    lookahead = min(config.lookahead_slots, remaining)
    if lookahead < 4:
        raise RpcError("not enough slots left in the epoch to read a leader window")
    block_time = None
    try:
        raw_time = rpc.call("getBlockTime", [schedule_start])
        if isinstance(raw_time, int):
            block_time = raw_time
    except RpcError as exc:
        logger.warning("getBlockTime failed: %s", exc)
    nodes = rpc.call("getClusterNodes")
    if not isinstance(nodes, list):
        raise RpcError("getClusterNodes returned no list")
    leaders = rpc.get_slot_leaders_range(
        schedule_start, lookahead, page=config.leader_page
    )
    observed = rpc.get_slot()
    blockhash = rpc.get_latest_blockhash()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ClusterSnapshot(
        epoch=int(info["epoch"]),
        schedule_start_slot=schedule_start,
        observed_slot=max(observed, schedule_start),
        slot_index=slot_index,
        slots_in_epoch=slots_in_epoch,
        blockhash=blockhash,
        block_time=block_time,
        fetched_at=fetched_at,
        nodes=nodes,
        leaders=leaders,
        source="rpc",
    )


def _tail_is_complete(snapshot: ClusterSnapshot, config: Config) -> bool:
    if snapshot.source == "fixture":
        return True
    remaining = snapshot.slots_in_epoch - snapshot.slot_index
    return len(snapshot.leaders) >= remaining or len(snapshot.leaders) < config.lookahead_slots


def _payer_for(config: Config):
    if config.keypair_path is not None and config.keypair_path.is_file():
        pubkey = load_keypair(config.keypair_path).pubkey()
        source = "keypair-live" if config.live else "keypair-pubkey-only"
        return pubkey, source
    return dry_run_payer(), "dry-run-placeholder"


def manifest_dict(plan: ExperimentPlan, config: Config) -> dict:
    snapshot = plan.snapshot
    families = []
    for target in plan.targets:
        if target.client_family not in families:
            families.append(target.client_family)
    return {
        "alpenglow": {
            "activation_epoch": ALPENGLOW_ACTIVATION_EPOCH,
            "activation_utc": ALPENGLOW_ACTIVATION_UTC,
            "observed_epoch": snapshot.epoch,
            "regime": plan.regime,
            "regime_ok": plan.regime_ok,
            "source": ALPENGLOW_SOURCE,
        },
        "block_time": snapshot.block_time,
        "blockhash": snapshot.blockhash,
        "client_families": families,
        "client_ids": [target.client_id for target in plan.targets],
        "design": {
            "hypotheses": list(HYPOTHESES),
            "jito_bundles": "out_of_scope",
            "per_window_fee_lamports": window_fee_lamports(),
            "primary_evidence": "self_sent_transactions",
            "write_lock": "single_fee_payer",
        },
        "epoch": snapshot.epoch,
        "exclusion_counts": plan.exclusion_counts,
        "experiment_id": plan.experiment_id,
        "fetched_at": snapshot.fetched_at,
        "lookahead_slots": len(snapshot.leaders),
        "min_lead_slots": config.min_lead_slots,
        "notes": plan.notes,
        "observed_slot": snapshot.observed_slot,
        "regime": plan.regime,
        "rpc_url": None if snapshot.source == "fixture" else redact_url(config.rpc_url),
        "schedule_start_slot": snapshot.schedule_start_slot,
        "selection_ok": plan.selection_ok,
        "selection_reason": plan.selection_reason,
        "slot_index": snapshot.slot_index,
        "slots_in_epoch": snapshot.slots_in_epoch,
        "source": snapshot.source,
        "spend": {
            "cap_lamports": config.spend_cap_lamports,
            "cap_sol": lamports_to_sol_str(config.spend_cap_lamports),
            "hard_cap_lamports": HARD_CAP_LAMPORTS,
            "planned_fee_lamports": plan.total_fee_lamports,
            "planned_fee_sol": lamports_to_sol_str(plan.total_fee_lamports),
        },
        "targets": [_target_dict(target) for target in plan.targets],
        "version_strings": [target.version for target in plan.targets],
        "warnings": plan.warnings,
    }


def _target_dict(target: Target) -> dict:
    payload = asdict(target)
    payload["assigned_windows"] = payload.pop("assigned")
    return payload


def _summary(
    plan: ExperimentPlan,
    config: Config,
    *,
    aborted: str | None,
    exit_code: int,
    signed_count: int = 0,
    sent_count: int = 0,
    spent_lamports: int = 0,
    extra: dict | None = None,
) -> dict:
    families = []
    for target in plan.targets:
        if target.client_family not in families:
            families.append(target.client_family)
    payload = {
        "aborted": aborted,
        "client_families": families,
        "client_ids": [target.client_id for target in plan.targets],
        "dry_run": not config.live,
        "exit_code": exit_code,
        "experiment_id": plan.experiment_id,
        "live": config.live,
        "notes": plan.notes,
        "planned_fee_lamports": plan.total_fee_lamports,
        "planned_fee_sol": lamports_to_sol_str(plan.total_fee_lamports),
        "planned_transactions": len(plan.txs),
        "regime": plan.regime,
        "regime_ok": plan.regime_ok,
        "selection_ok": plan.selection_ok,
        "selection_reason": plan.selection_reason,
        "sent_count": sent_count,
        "signed_count": signed_count,
        "source": plan.snapshot.source,
        "spend_cap_lamports": config.spend_cap_lamports,
        "spend_cap_sol": lamports_to_sol_str(config.spend_cap_lamports),
        "spent_estimate_lamports": spent_lamports,
        "target_validators": len(plan.targets),
        "targets_preview": [
            {
                "client_family": target.client_family,
                "client_id": target.client_id,
                "identity": target.identity,
                "version": target.version,
                "windows": [
                    {
                        "path": window.path,
                        "repeat_index": window.repeat_index,
                        "start_slot": window.start_slot,
                    }
                    for window in target.assigned
                ],
            }
            for target in plan.targets
        ],
        "version_strings": [target.version for target in plan.targets],
        "warnings": plan.warnings,
    }
    if extra:
        payload.update(extra)
    return payload


def format_summary(summary: dict) -> str:
    if summary["live"]:
        mode = "LIVE"
    else:
        mode = "DRY-RUN (nothing signed or sent)"
    lines = [
        mode,
        f"experiment: {summary['experiment_id']}",
        f"source: {summary['source']}",
        f"alpenglow regime: {summary['regime']} (ok={summary['regime_ok']})",
        (
            "planned fee: "
            f"{summary['planned_fee_sol']} SOL "
            f"({summary['planned_fee_lamports']} lamports) "
            f"/ cap {summary['spend_cap_sol']} SOL"
        ),
        (
            f"targets: {summary['target_validators']} "
            f"families: {', '.join(summary['client_families']) or '(none)'}"
        ),
    ]
    for target in summary.get("targets_preview") or []:
        windows = ", ".join(
            f"{item['path']}@{item['start_slot']}" for item in target.get("windows") or []
        )
        lines.append(
            f"  {target['identity']}  {target['client_id']} {target['version']}  {windows}"
        )
    lines.extend(
        [
            f"planned transactions: {summary['planned_transactions']}",
            f"signed: {summary['signed_count']}  sent: {summary['sent_count']}",
        ]
    )
    if summary.get("aborted"):
        lines.append(f"aborted: {summary['aborted']}")
    if summary.get("selection_reason"):
        lines.append(f"selection: {summary['selection_reason']}")
    for note in summary.get("notes") or []:
        lines.append(f"note: {note}")
    for warning in summary.get("warnings") or []:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def write_plan_files(plan: ExperimentPlan, config: Config, summary: dict) -> None:
    out = config.output_dir
    out.mkdir(parents=True, exist_ok=True)
    dump_json(out / "manifest.json", manifest_dict(plan, config))
    dump_json(out / "run_summary.json", summary)
    rows = [asdict(tx) for tx in plan.txs]
    write_jsonl(out / "planned_transactions.jsonl", rows)
    write_csv(out / "planned_transactions.csv", rows, PLANNED_FIELDS)


def _prepare(config: Config) -> ExperimentPlan:
    if config.fixture_dir is not None:
        snapshot = load_snapshot(config.fixture_dir)
    else:
        snapshot = fetch_snapshot(RpcClient(config.rpc_url, timeout_s=config.timeout_s), config)
    plan = build_experiment(
        snapshot,
        experiment_id=config.experiment_id,
        min_lead_slots=config.min_lead_slots,
        target_validators=config.target_validators,
        min_client_families=config.min_client_families,
        tpu_repeats=config.tpu_repeats,
        rpc_repeats=config.rpc_repeats,
        tail_is_complete=_tail_is_complete(snapshot, config),
    )
    payer, payer_source = _payer_for(config)
    attach_unsigned_messages(
        plan.txs,
        payer=payer,
        blockhash=snapshot.blockhash,
        payer_source=payer_source,
    )
    return plan


def run_measure(config: Config) -> int:
    plan = _prepare(config)
    if not plan.regime_ok:
        summary = _summary(plan, config, aborted="regime", exit_code=4)
        write_plan_files(plan, config, summary)
        print(format_summary(summary))
        return 4
    if not plan.selection_ok:
        summary = _summary(plan, config, aborted="selection", exit_code=2)
        write_plan_files(plan, config, summary)
        print(format_summary(summary))
        return 2
    guard = SpendGuard(config.spend_cap_lamports)
    try:
        guard.approve_batch([tx.fee_lamports for tx in plan.txs])
    except SpendCapExceeded as exc:
        summary = _summary(
            plan,
            config,
            aborted="spend_cap",
            exit_code=3,
            extra={"spend_cap_error": str(exc)},
        )
        write_plan_files(plan, config, summary)
        print(format_summary(summary))
        return 3
    if not config.live:
        summary = _summary(plan, config, aborted=None, exit_code=0)
        write_plan_files(plan, config, summary)
        print(format_summary(summary))
        return 0
    if config.keypair_path is None or not config.keypair_path.is_file():
        summary = _summary(plan, config, aborted="missing_keypair", exit_code=5)
        write_plan_files(plan, config, summary)
        print(format_summary(summary))
        return 5
    return _run_live(plan, config)


def _run_live(plan: ExperimentPlan, config: Config) -> int:
    # Imported here so a dry-run never needs to construct a sender.
    from measure.live import execute_live
    from measure.tpu import RpcSender, TpuSender

    keypair = load_keypair(config.keypair_path)
    rpc = RpcClient(config.rpc_url, timeout_s=config.timeout_s)
    destination = redact_url(config.rpc_url)
    result = execute_live(
        plan,
        keypair=keypair,
        rpc=rpc,
        tpu_sender=TpuSender(),
        rpc_sender=RpcSender(rpc),
        cap_lamports=config.spend_cap_lamports,
        rpc_destination=destination,
    )
    write_jsonl(config.output_dir / "sent_transactions.jsonl", result.rows)
    extra = {"live_aborted": result.aborted_reason}
    if config.collect and result.sent_count:
        try:
            _collect_live(rpc, plan, result.rows, config)
            extra["collect"] = "ok"
        except (RpcError, OSError, ValueError) as exc:
            logger.error("collect failed: %s", exc)
            extra["collect"] = f"failed: {exc}"
    exit_code = 0
    aborted = result.aborted_reason
    if result.aborted_reason == "spend_cap":
        exit_code = 3
    elif result.sent_count == 0:
        exit_code = 1
        aborted = aborted or "nothing_sent"
    summary = _summary(
        plan,
        config,
        aborted=aborted,
        exit_code=exit_code,
        signed_count=result.signed_count,
        sent_count=result.sent_count,
        spent_lamports=result.spent_lamports,
        extra=extra,
    )
    write_plan_files(plan, config, summary)
    print(format_summary(summary))
    return exit_code


def _collect_live(rpc: RpcClient, plan: ExperimentPlan, sent_rows: list[dict], config: Config) -> None:
    plans = [asdict(tx) for tx in plan.txs]
    slots = slots_to_fetch(plans)
    if not slots:
        return
    from measure.live import wait_for_slot

    wait_for_slot(rpc, max(slots))
    _write_collection(rpc, slots, plans, sent_rows, config)


def collect_from_sent(config: Config, sent_path, plans_path) -> int:
    sent_rows = read_jsonl(sent_path)
    plans = read_jsonl(plans_path) if plans_path else []
    slots = slots_to_fetch(plans or sent_rows)
    if len(slots) > 500:
        raise ConfigError(f"refusing to fetch {len(slots)} blocks")
    rpc = RpcClient(config.rpc_url, timeout_s=config.timeout_s)
    _write_collection(rpc, slots, plans, sent_rows, config)
    print(f"collected {len(slots)} slots into {config.output_dir}")
    return 0


def _write_collection(rpc, slots, plans, sent_rows, config: Config) -> None:
    from measure.constants import MAX_BLOCKS_TO_FETCH

    if len(slots) > MAX_BLOCKS_TO_FETCH:
        raise ConfigError(f"refusing to fetch {len(slots)} blocks")
    import time

    blocks = []
    for index, slot in enumerate(slots):
        result = rpc.get_block(slot)
        if result is not None:
            blocks.append(parse_block(result, slot))
        if index + 1 < len(slots):
            time.sleep(0.25)
    signatures = {
        row["seq"]: row["signature"]
        for row in sent_rows
        if isinstance(row.get("seq"), int) and isinstance(row.get("signature"), str)
    }
    observations = build_observations(blocks, plans, signatures)
    our_signatures = {sig for sig in signatures.values()}
    contexts = [block_context(block, our_signatures) for block in blocks]
    analysis = summarize_hypotheses(observations)
    out = config.output_dir
    write_jsonl(out / "block_observations.jsonl", observations)
    write_csv(out / "block_observations.csv", observations, OBSERVATION_FIELDS)
    write_jsonl(out / "block_context.jsonl", contexts)
    dump_json(out / "hypothesis_agreement.json", analysis)


def parse_block_file(path, slot: int, output_dir) -> int:
    import json
    from pathlib import Path

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise ConfigError("block file must be a getBlock result object")
    block = parse_block(payload, slot)
    observations = build_observations([block], [])
    context = block_context(block, set())
    out = Path(output_dir)
    write_jsonl(out / "block_observations.jsonl", observations)
    write_csv(out / "block_observations.csv", observations, OBSERVATION_FIELDS)
    write_jsonl(out / "block_context.jsonl", [context])
    print(
        f"slot {slot}: {context['tx_count']} transactions, "
        f"{context['our_tx_count']} tagged"
    )
    return 0
