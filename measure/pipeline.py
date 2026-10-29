"""Plan, write, and (only with --live) send."""

from __future__ import annotations

import logging
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

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
    ALPENGLOW_CUTOFF_SLOT,
    ALPENGLOW_SOURCE,
    BURST_OFFSET_SLOTS,
    CU_LIMIT,
    CU_LIMIT_PROBE,
    HARD_CAP_LAMPORTS,
    LADDER_PRIORITY_FEES,
    MAX_SUPPORTED_TRANSACTION_VERSION,
    QUIC_WARMUP_SLOTS,
    RETRIES_INSIDE_WINDOW,
    TRANSACTION_FORMAT,
)
from measure.directory import apply_directory, fetch_genesis_cert, fetch_validators_app, fetch_vote_accounts
from measure.errors import ConfigError, RpcError, SpendCapExceeded
from measure.fees import lamports_to_sol_str
from measure.io_util import dump_json, read_jsonl, redact_url, write_csv, write_jsonl
from measure.ledger import SpendLedger
from measure.models import ClusterSnapshot, ExperimentPlan, PlannedTx, Target
from measure.plan import build_experiment
from measure.rpc import RpcClient
from measure.snapshot_io import load_snapshot
from measure.txbuild import attach_unsigned_messages, load_keypair, sign_transaction

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


def _main_keypair(config: Config):
    from solders.keypair import Keypair

    if config.keypair_path is not None and config.keypair_path.is_file():
        keypair = load_keypair(config.keypair_path)
        source = "keypair"
        return keypair, source
    return Keypair(), "ephemeral-dry-run"


def _git_sha() -> str | None:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _attempt_row(tx: PlannedTx) -> dict:
    host, _, port = (tx.tpu_quic or "").rpartition(":")
    return {
        "block_index": None,
        "cu_limit": tx.cu_limit,
        "cu_price": tx.cu_price_micro_lamports,
        "error": None,
        "fee_lamports": tx.fee_lamports,
        "ladder": tx.ladder,
        "landed_slot": None,
        "leader_identity": tx.identity or None,
        "priority_fee_lamports": tx.priority_fee_lamports,
        "priority_score": tx.priority_score,
        "role": tx.role,
        "send_index": tx.send_index,
        "error": tx.error,
        "send_unix_ms": None,
        "seq": tx.seq,
        "signature": tx.signature,
        "signed": tx.signed,
        "submit_path": tx.submit_path,
        "submitted": tx.submitted,
        "target_slot": tx.planned_slot,
        "tpu_host": host or None,
        "tpu_port": int(port) if port.isdigit() else None,
    }


def _sign_plan(plan: ExperimentPlan, main_keypair, blockhash: str) -> None:
    signers = {str(main_keypair.pubkey()): main_keypair}
    signers.update(getattr(plan, "ephemeral_signers", {}))
    for tx in plan.txs:
        signer = signers.get(tx.payer)
        if signer is None:
            tx.signed = False
            tx.signature = None
            continue
        _raw, signature = sign_transaction(tx, signer, blockhash)
        del _raw
        tx.signature = signature
        tx.signed = True
        tx.submitted = False
        tx.sent = False
    if hasattr(plan, "ephemeral_signers"):
        del plan.ephemeral_signers


def _run_dir(config: Config) -> Path:
    if config.output_dir is not None:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        return config.output_dir
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.data_dir / "runs" / stamp
    suffix = 2
    while path.exists():
        path = config.data_dir / "runs" / f"{stamp}-{suffix}"
        suffix += 1
    path.mkdir(parents=True)
    return path


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
        "alpenglow_cutoff_slot": ALPENGLOW_CUTOFF_SLOT,
        "burst_offset_slots": BURST_OFFSET_SLOTS,
        "cu_limit": CU_LIMIT,
        "cu_limit_probe": CU_LIMIT_PROBE,
        "design": {
            "conflict_rungs": list(plan.conflict_rungs),
            "fee_rungs_lamports": list(LADDER_PRIORITY_FEES),
            "jito_bundles": "out_of_scope",
            "nonconflict_rungs": list(plan.nonconflict_rungs),
            "primary_evidence": "self_sent_signatures",
            "retries_inside_window": RETRIES_INSIDE_WINDOW,
            "submit_path": "tpu",
            "transmission_order": "lowest fee first",
        },
        "max_supported_transaction_version": MAX_SUPPORTED_TRANSACTION_VERSION,
        "mode": config.mode,
        "permutation_seed": 0,
        "quic_warmup_slots": QUIC_WARMUP_SLOTS,
        "transaction_format": TRANSACTION_FORMAT,
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
        mode = "DRY-RUN (signed in memory, nothing submitted)"
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


def write_plan_files(
    plan: ExperimentPlan,
    config: Config,
    summary: dict,
    run_dir: Path,
    *,
    ledger: SpendLedger,
    genesis: dict | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "blocks").mkdir(exist_ok=True)
    manifest = manifest_dict(plan, config)
    manifest["genesis_certificate"] = genesis
    manifest["git_sha"] = _git_sha()
    manifest["run_dir"] = str(run_dir)
    dump_json(run_dir / "manifest.json", manifest)
    dump_json(run_dir / "validators.json", [_target_dict(target) for target in plan.targets])
    dump_json(
        run_dir / "spend.json",
        {
            "cap_lamports": config.spend_cap_lamports,
            "counted_against_cap": False,
            "funding_principal_lamports": plan.funding_principal_lamports,
            "halt_reason": summary.get("aborted"),
            "ledger_path": str(ledger.path),
            "ledger_spent_before_lamports": ledger.spent_lamports,
            "planned_fee_lamports": plan.total_fee_lamports,
            "projected_outlay_lamports": plan.projected_outlay_lamports,
            "signed_lamports": summary.get("signed_count", 0) and plan.total_fee_lamports,
        },
    )
    write_jsonl(run_dir / "attempts.jsonl", [_attempt_row(tx) for tx in plan.txs])
    text = format_summary(summary)
    (run_dir / "run.log").write_text(text + "\n", encoding="utf-8")
    print(text)


def _prepare(config: Config, *, cap_remaining: int, rent_lamports: int) -> tuple[ExperimentPlan, object]:
    if config.fixture_dir is not None:
        snapshot = load_snapshot(config.fixture_dir)
    else:
        snapshot = fetch_snapshot(RpcClient(config.rpc_url, timeout_s=config.timeout_s), config)
        rpc = RpcClient(config.rpc_url, timeout_s=config.timeout_s)
        votes = fetch_vote_accounts(rpc)
        app_rows = None
        if config.validators_app_token:
            app_rows = fetch_validators_app(config.validators_app_token, timeout_s=config.timeout_s)
        apply_directory(snapshot.nodes, votes, app_rows)
    main, payer_source = _main_keypair(config)
    plan = build_experiment(
        snapshot,
        experiment_id=config.experiment_id,
        min_lead_slots=config.min_lead_slots,
        target_validators=config.target_validators,
        min_client_families=config.min_client_families,
        tail_is_complete=_tail_is_complete(snapshot, config),
        cap_remaining_lamports=cap_remaining,
        rent_lamports=rent_lamports,
        main_payer=str(main.pubkey()),
    )
    attach_unsigned_messages(plan.txs, blockhash=snapshot.blockhash, payer_source=payer_source)
    return plan, main


def run_measure(config: Config) -> int:
    if config.mode_warning:
        logger.warning("%s", config.mode_warning)
    ledger = SpendLedger(config.data_dir / "spend.json", config.spend_cap_lamports)
    rent = _rent_lamports(config)
    genesis = None
    if config.fixture_dir is None:
        try:
            genesis = fetch_genesis_cert(RpcClient(config.rpc_url, timeout_s=config.timeout_s))
        except RpcError as exc:
            genesis = {"available": False, "error": str(exc), "slot": None}
    plan, main_keypair = _prepare(config, cap_remaining=ledger.remaining(), rent_lamports=rent)
    run_dir = _run_dir(config)
    if not plan.regime_ok:
        summary = _summary(plan, config, aborted="regime", exit_code=4)
        write_plan_files(plan, config, summary, run_dir, ledger=ledger, genesis=genesis)
        return 4
    if not plan.selection_ok:
        summary = _summary(plan, config, aborted="selection", exit_code=2)
        write_plan_files(plan, config, summary, run_dir, ledger=ledger, genesis=genesis)
        return 2
    try:
        ledger.ensure_fits(plan.total_fee_lamports)
    except SpendCapExceeded as exc:
        summary = _summary(
            plan,
            config,
            aborted="spend_cap",
            exit_code=3,
            extra={"spend_cap_error": str(exc)},
        )
        write_plan_files(plan, config, summary, run_dir, ledger=ledger, genesis=genesis)
        return 3
    if not config.live:
        _sign_plan(plan, main_keypair, plan.snapshot.blockhash)
        del main_keypair
        summary = _summary(
            plan,
            config,
            aborted=None,
            exit_code=0,
            signed_count=sum(1 for tx in plan.txs if tx.signed),
        )
        write_plan_files(plan, config, summary, run_dir, ledger=ledger, genesis=genesis)
        return 0
    if config.keypair_path is None or not config.keypair_path.is_file():
        summary = _summary(plan, config, aborted="missing_keypair", exit_code=5)
        write_plan_files(plan, config, summary, run_dir, ledger=ledger, genesis=genesis)
        return 5
    return _run_live(plan, config, run_dir, ledger, genesis)


def _rent_lamports(config: Config) -> int:
    from measure.constants import DEFAULT_RENT_LAMPORTS

    if config.fixture_dir is not None:
        return DEFAULT_RENT_LAMPORTS
    try:
        result = RpcClient(config.rpc_url, timeout_s=config.timeout_s).call(
            "getMinimumBalanceForRentExemption",
            [0],
        )
        if isinstance(result, int) and result > 0:
            return result
    except RpcError as exc:
        logger.warning("rent exemption lookup failed, using %s: %s", DEFAULT_RENT_LAMPORTS, exc)
    return DEFAULT_RENT_LAMPORTS


def _tpu_for(plan: ExperimentPlan, identity: str) -> str:
    for node in plan.snapshot.nodes:
        if node.get("pubkey") == identity and isinstance(node.get("tpuQuic"), str):
            return node["tpuQuic"]
    for target in plan.targets:
        if target.identity == identity:
            return target.tpu_quic
    return ""


def _reserve_batch(ledger: SpendLedger, batch: list[PlannedTx], run_id: str) -> None:
    ledger.ensure_fits(sum(tx.fee_lamports for tx in batch))
    for tx in batch:
        ledger.reserve(tx.fee_lamports, run_id=run_id, seq=tx.seq, reason="signed")


def _release_batch(ledger: SpendLedger, batch: list[PlannedTx], run_id: str, reason: str) -> None:
    for tx in batch:
        ledger.release(tx.fee_lamports, run_id=run_id, seq=tx.seq, reason=reason)


def _submit_batch(batch: list[PlannedTx], payloads: list[bytes], tpu: str, rpc: RpcClient) -> str:
    """Return the path that accepted the batch. Raise when neither path did."""
    from measure.tpu import TpuSendError, TpuSender

    tpu_error = ""
    if tpu:
        try:
            TpuSender().send_payloads(payloads, destination=tpu)
            return "tpu"
        except TpuSendError as exc:
            tpu_error = str(exc)
            logger.error("TPU send failed: %s", exc)
            if exc.transmitted:
                # The leader may have the bytes. Do not also send them via RPC.
                raise
        except Exception as exc:
            tpu_error = str(exc)
            logger.error("TPU send failed: %s", exc)
    try:
        for raw in payloads:
            rpc.send_transaction(raw)
        return "rpc-fallback"
    except Exception as exc:
        detail = f"tpu: {tpu_error}; rpc: {exc}" if tpu_error else str(exc)
        raise RpcError(detail) from exc


def _signatures_landed(rpc: RpcClient, signatures: list[str]) -> bool:
    if not signatures:
        return True
    try:
        statuses = rpc.get_signature_statuses(signatures)
    except RpcError as exc:
        logger.warning("signature status failed: %s", exc)
        return False
    for status in statuses:
        if not status or status.get("err"):
            return False
        if status.get("confirmationStatus") not in {"processed", "confirmed", "finalized"}:
            return False
    return True


def _run_live(plan: ExperimentPlan, config: Config, run_dir: Path, ledger: SpendLedger, genesis) -> int:
    """Fund first, wait until those signatures land, then send each probe window.

    The cap is reserved only for a batch that is about to be signed. A send
    that fails before the leader accepts it is released. There is no retry
    of the same group; RPC is a different path, recorded on the row.
    """
    from measure.slots import SlotFeed, send_decision

    keypair = load_keypair(config.keypair_path)
    signers = {str(keypair.pubkey()): keypair, **getattr(plan, "ephemeral_signers", {})}
    rpc = RpcClient(config.rpc_url, timeout_s=config.timeout_s)
    feed = SlotFeed(rpc, config.rpc_url)
    feed.start()
    logger.info("slot clock: %s", feed.source)
    aborted = None
    try:
        funding = [tx for tx in plan.txs if tx.role == "funding"]
        probes = [tx for tx in plan.txs if tx.role != "funding"]
        if funding:
            slot_now = feed.current()
            leader = rpc.get_slot_leader(slot_now)
            tpu = _tpu_for(plan, leader)
            if not _send_group(funding, plan, ledger, signers, rpc, tpu, slot_now):
                aborted = "funding_failed"
            else:
                deadline_slot = min(tx.planned_slot for tx in probes) - BURST_OFFSET_SLOTS if probes else slot_now + 40
                if not _wait_landed(rpc, feed, [tx.signature for tx in funding if tx.signature], deadline_slot):
                    aborted = "funding_unconfirmed"
                    for tx in probes:
                        tx.error = "funding_unconfirmed"
        if aborted is None:
            groups: dict[tuple, list[PlannedTx]] = {}
            for tx in probes:
                groups.setdefault((tx.planned_slot, tx.identity), []).append(tx)
            for key in sorted(groups):
                batch = groups[key]
                target = batch[0].planned_slot
                try:
                    slot_now = feed.wait_until(
                        max(target - BURST_OFFSET_SLOTS, 0),
                        timeout_s=max(30.0, (target - feed.slot) * 0.3 + 20),
                    )
                except TimeoutError:
                    aborted = "timeout"
                    for tx in batch:
                        tx.error = "timeout"
                    break
                decision = send_decision(
                    slot_now,
                    target_slot=target,
                    window_start=batch[0].window_start_slot,
                    window_length=batch[0].window_length,
                    burst_offset=BURST_OFFSET_SLOTS,
                )
                if decision == "skip":
                    for tx in batch:
                        tx.error = "missed_window"
                    logger.error("skipped slot %s at current slot %s", target, slot_now)
                    continue
                if not _send_group(batch, plan, ledger, signers, rpc, batch[0].tpu_quic, slot_now):
                    aborted = aborted or "send_failed"
    finally:
        feed.close()
        rpc.close()
        if hasattr(plan, "ephemeral_signers"):
            del plan.ephemeral_signers
        del signers, keypair
    submitted = sum(1 for tx in plan.txs if tx.submitted)
    exit_code = 3 if aborted == "spend_cap" else (0 if submitted and aborted is None else 1)
    summary = _summary(
        plan,
        config,
        aborted=aborted,
        exit_code=exit_code,
        signed_count=sum(1 for tx in plan.txs if tx.signed),
        sent_count=submitted,
        spent_lamports=ledger.spent_lamports,
    )
    write_plan_files(plan, config, summary, run_dir, ledger=ledger, genesis=genesis)
    return exit_code


def _send_group(batch, plan, ledger, signers, rpc, tpu: str, slot_now: int) -> bool:
    try:
        _reserve_batch(ledger, batch, plan.experiment_id)
    except SpendCapExceeded:
        for tx in batch:
            tx.error = "spend_cap"
        return False
    blockhash = rpc.get_latest_blockhash()
    payloads = []
    try:
        for tx in batch:
            raw, signature = sign_transaction(tx, signers[tx.payer], blockhash)
            tx.signature = signature
            tx.signed = True
            payloads.append(raw)
        path = _submit_batch(batch, payloads, tpu, rpc)
    except Exception as exc:
        logger.error("batch seq %s not submitted: %s", batch[0].seq, exc)
        for tx in batch:
            tx.error = str(exc)
            tx.submitted = False
            tx.sent = False
        if not getattr(exc, "transmitted", False):
            _release_batch(ledger, batch, plan.experiment_id, "send_failed")
        return False
    finally:
        del payloads
    for tx in batch:
        tx.submit_path = path
        tx.path = path
        tx.submitted = True
        tx.sent = True
        tx.error = None
    logger.info("submitted %s txs via %s at slot %s", len(batch), path, slot_now)
    return True


def _wait_landed(rpc, feed, signatures: list[str | None], deadline_slot: int) -> bool:
    pending = [sig for sig in signatures if sig]
    if not pending:
        return False
    while True:
        if _signatures_landed(rpc, pending):
            return True
        if feed.slot >= deadline_slot:
            return False
        try:
            feed.wait_until(feed.slot + 1, timeout_s=5)
        except TimeoutError:
            return _signatures_landed(rpc, pending)


def _collect_live(rpc: RpcClient, plan: ExperimentPlan, sent_rows: list[dict], config: Config) -> None:
    plans = [asdict(tx) for tx in plan.txs]
    slots = slots_to_fetch(plans or sent_rows)
    if not slots:
        return
    out = config.output_dir or config.data_dir
    _write_collection(rpc, slots, plans, sent_rows, out)


def collect_from_sent(config: Config, sent_path, plans_path) -> int:
    sent_path = Path(sent_path)
    sent_rows = read_jsonl(sent_path)
    plans = read_jsonl(plans_path) if plans_path else sent_rows
    slots = slots_to_fetch(sent_rows)
    if len(slots) > 500:
        raise ConfigError(f"refusing to fetch {len(slots)} blocks")
    out = config.output_dir or sent_path.parent
    rpc = RpcClient(config.rpc_url, timeout_s=config.timeout_s)
    _write_collection(rpc, slots, plans, sent_rows, out)
    print(f"collected {len(slots)} slots into {out}")
    return 0


def _write_collection(rpc, slots, plans, sent_rows, out: Path) -> None:
    from measure.constants import MAX_BLOCKS_TO_FETCH

    if len(slots) > MAX_BLOCKS_TO_FETCH:
        raise ConfigError(f"refusing to fetch {len(slots)} blocks")
    import time

    blocks = []
    block_dir = out / "blocks"
    block_dir.mkdir(parents=True, exist_ok=True)
    for index, slot in enumerate(slots):
        result = rpc.get_block(slot)
        if result is not None:
            dump_json(block_dir / f"{slot}.json", result)
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
