"""Command line. No arguments is a dry-run, same as ``run``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from measure.config import make_config
from measure.errors import ConfigError, MeasureError
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
    run.add_argument("--live", action="store_true", help="sign and send; dry-run otherwise")
    run.add_argument("--collect", action="store_true", help="after a live send, fetch getBlock")
    run.add_argument("--fixture-dir", default=None, help="offline snapshot directory, no RPC")
    run.add_argument("--keypair", default=None, help="Solana CLI keypair JSON; overrides KEYPAIR_PATH")
    run.add_argument("--spend-cap-sol", default=None)
    run.add_argument("--lookahead", type=int, default=None)
    run.add_argument("--min-lead-slots", type=int, default=None)
    run.add_argument("--target-validators", type=int, default=None)
    run.add_argument("--tpu-repeats", type=int, default=None)
    run.add_argument("--rpc-repeats", type=int, default=None)
    run.add_argument("--experiment-id", default=None)

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
    if args.command == "parse-block":
        return parse_block_file(args.file, args.slot, args.output_dir)
    if args.command == "collect":
        config = make_config(rpc_url=args.rpc_url, output_dir=args.output_dir)
        plans = Path(args.plans) if args.plans else None
        return collect_from_sent(config, Path(args.from_sent), plans)
    config = make_config(
        rpc_url=args.rpc_url,
        spend_cap_sol=args.spend_cap_sol,
        lookahead_slots=args.lookahead,
        min_lead_slots=args.min_lead_slots,
        target_validators=args.target_validators,
        tpu_repeats=args.tpu_repeats,
        rpc_repeats=args.rpc_repeats,
        output_dir=args.output_dir,
        keypair_path=args.keypair,
        experiment_id=args.experiment_id,
        fixture_dir=args.fixture_dir,
        live=bool(args.live),
        collect=bool(args.collect),
    )
    return run_measure(config)
