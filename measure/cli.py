"""Command line. No arguments is a dry-run, same as ``run``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from measure.config import make_config
from measure.errors import ConfigError, MeasureError
from measure.fees import lamports_to_sol_str
from measure.keyinfo import describe_keypair
from measure.ledger import SpendLedger
from measure.pipeline import collect_from_sent, parse_block_file, run_measure


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--rpc-url", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure",
        description="Measure how Solana leaders order transactions inside a block.",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="select leaders and plan (default) or send (--live)")
    _add_output(run)
    run.add_argument(
        "--live",
        action="store_true",
        help="submit transactions. Same switch as MODE=live. Dry-run otherwise.",
    )
    run.add_argument("--collect", action="store_true", help="after a live send, fetch getBlock")
    run.add_argument("--fixture-dir", default=None, help="offline snapshot directory, no RPC")
    run.add_argument("--keypair", default=None, help="Solana CLI keypair JSON; overrides KEYPAIR_PATH")
    run.add_argument("--spend-cap-sol", default=None)
    run.add_argument("--lookahead", type=int, default=None)
    run.add_argument("--min-lead-slots", type=int, default=None)
    run.add_argument("--target-validators", type=int, default=None)
    run.add_argument("--experiment-id", default=None)

    key = sub.add_parser("key", help="print a keypair's public key and balance; do not send")
    key.add_argument("--keypair", default=None)
    key.add_argument("--rpc-url", default=None)

    ledger = sub.add_parser("ledger", help="show, reconcile, or reset the global fee ledger")
    ledger.add_argument("ledger_action", choices=("show", "reconcile", "reset"))
    ledger.add_argument("--attempts", default=None, help="attempts.jsonl to reconcile against")
    ledger.add_argument(
        "--rpc",
        action="store_true",
        help="keep only fees whose signatures have landed; requires RPC_URL",
    )
    ledger.add_argument("--rpc-url", default=None)
    ledger.add_argument(
        "--run-id",
        default=None,
        help="ledger run id when attempts.jsonl has no experiment_id",
    )
    ledger.add_argument("--yes", action="store_true", help="required for reset")
    ledger.add_argument("--data-dir", default=None)

    collect = sub.add_parser("collect", help="fetch getBlock for a previous live send")
    _add_output(collect)
    collect.add_argument("--from-sent", required=True)
    collect.add_argument("--plans", default=None)

    parse_block = sub.add_parser("parse-block", help="parse a saved getBlock JSON file")
    parse_block.add_argument("--file", required=True)
    parse_block.add_argument("--slot", required=True, type=int)
    parse_block.add_argument("--output-dir", default="data/raw")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["run", *argv]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except ConfigError as exc:
        logging.getLogger("measure").error("%s", exc)
        return 1
    except MeasureError as exc:
        logging.getLogger("measure").error("%s", exc)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "key":
        return _key(args)
    if args.command == "ledger":
        return _ledger(args)
    if args.command == "parse-block":
        return parse_block_file(args.file, args.slot, args.output_dir)
    if args.command == "collect":
        config = make_config(rpc_url=args.rpc_url, output_dir=args.output_dir)
        plans = Path(args.plans) if args.plans else None
        return collect_from_sent(config, Path(args.from_sent), plans)
    if args.collect and not args.live:
        config = make_config(rpc_url=args.rpc_url, output_dir=args.output_dir)
        latest = _latest_attempts(config.data_dir)
        if latest is None:
            raise ConfigError(f"no attempts.jsonl under {config.data_dir}/runs")
        return collect_from_sent(config, latest, None)
    config = make_config(
        rpc_url=args.rpc_url,
        spend_cap_sol=args.spend_cap_sol,
        lookahead_slots=args.lookahead,
        min_lead_slots=args.min_lead_slots,
        target_validators=args.target_validators,
        output_dir=args.output_dir,
        keypair_path=args.keypair,
        experiment_id=args.experiment_id,
        fixture_dir=args.fixture_dir,
        live=bool(args.live),
        collect=bool(args.collect),
    )
    return run_measure(config)


def _latest_attempts(data_dir: Path) -> Path | None:
    runs = data_dir / "runs"
    if not runs.is_dir():
        return None
    found = sorted(path for path in runs.glob("*/attempts.jsonl") if path.is_file())
    return found[-1] if found else None


def _ledger(args: argparse.Namespace) -> int:
    import os

    data_dir = Path(args.data_dir or os.environ.get("DATA_DIR") or "data")
    ledger = SpendLedger(data_dir / "spend.json", 50_000_000)
    if args.ledger_action == "show":
        print(f"spent_lamports: {ledger.spent_lamports}")
        print(f"cap_lamports: {ledger.cap_lamports}")
        print(f"path: {ledger.path}")
        return 0
    if args.ledger_action == "reset":
        if not args.yes:
            raise ConfigError("ledger reset requires --yes")
        before = ledger.spent_lamports
        ledger.reset(reason="reset")
        print(f"reset {before} lamports to 0 at {ledger.path}")
        return 0
    if not args.attempts:
        raise ConfigError("ledger reconcile requires --attempts")
    from measure.io_util import read_jsonl

    attempts_path = Path(args.attempts)
    rows = read_jsonl(attempts_path)
    if args.rpc:
        return _reconcile_on_chain(args, ledger, attempts_path, rows)
    total = ledger.reconcile(rows)
    print(
        f"reconciled to {total} lamports from submitted rows in {args.attempts}. "
        "Unlanded submissions stay reserved; pass --rpc to match the chain."
    )
    return 0


def _reconcile_on_chain(args, ledger, attempts_path: Path, rows: list[dict]) -> int:
    import json
    import os

    from measure.errors import RpcError
    from measure.ledger import group_attempts, landed_fee_lamports
    from measure.rpc import RpcClient

    manifest_id = None
    manifest_path = attempts_path.parent / "manifest.json"
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("experiment_id"):
            manifest_id = str(payload["experiment_id"])
    try:
        groups = group_attempts(rows, run_id=args.run_id, manifest_id=manifest_id)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if "" in groups:
        raise ConfigError("attempts have no experiment_id; pass --run-id")
    url = args.rpc_url or os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"
    rpc = RpcClient(url, timeout_s=20)
    prepared = []
    try:
        for run_id, group in groups.items():
            submitted = [row for row in group if row.get("submitted") and row.get("signature")]
            signatures = [str(row["signature"]) for row in submitted]
            statuses: list = []
            try:
                for start in range(0, len(signatures), 64):
                    statuses.extend(
                        rpc.get_signature_statuses(signatures[start : start + 64], search_history=True)
                    )
            except RpcError as exc:
                raise ConfigError(f"not reconciling; status read failed: {exc}") from exc
            if len(statuses) != len(submitted):
                raise ConfigError(
                    f"not reconciling; status count {len(statuses)} != {len(submitted)} signatures"
                )
            landed = landed_fee_lamports(submitted, statuses) if submitted else 0
            landed_n = sum(
                1
                for status in statuses
                if isinstance(status, dict)
                and not status.get("err")
                and status.get("confirmationStatus") in {"processed", "confirmed", "finalized"}
            )
            prepared.append((run_id, len(submitted), landed_n, landed))
    finally:
        rpc.close()
    for run_id, submitted_n, landed_n, landed in prepared:
        before = ledger.net_for_run(run_id)
        ledger.reconcile_run(run_id, landed)
        print(
            f"run {run_id}: {landed_n}/{submitted_n} submitted signatures landed; "
            f"fees {before} -> {landed}; ledger {ledger.spent_lamports}"
        )
    return 0


def _key(args: argparse.Namespace) -> int:
    import os

    path = args.keypair or os.environ.get("KEYPAIR_PATH")
    if not path:
        raise ConfigError("pass --keypair or set KEYPAIR_PATH")
    rpc = args.rpc_url or os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"
    info = describe_keypair(Path(path), rpc)
    print(f"pubkey: {info['pubkey']}")
    print(f"balance: {info['sol']} SOL ({info['lamports']} lamports)")
    print(lamports_to_sol_str(info["lamports"]))
    return 0
