"""Command-line entry point: ``python3 -m analysis``."""

from __future__ import annotations

import argparse
from pathlib import Path

from .core import write_outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze validator ordering runs")
    parser.add_argument("--data", type=Path, default=Path("data/runs"))
    parser.add_argument("--output", type=Path, default=Path("analysis/output"))
    parser.add_argument("--bootstrap-seed", type=int, default=20261029)
    parser.add_argument("--bootstrap-replicates", type=int, default=10_000)
    args = parser.parse_args()
    result = write_outputs(
        args.data,
        args.output,
        seed=args.bootstrap_seed,
        replicates=args.bootstrap_replicates,
    )
    print(
        f"Analyzed {len(result['inputs'])} runs and "
        f"{len(result['ladder_statistics'])} validator-run ladders."
    )
    for row in result["pooled_pair_shares"]:
        print(
            f"{row['scope']}: fee-order {row['priority_pair_share']:.3f} "
            f"[{row['priority_ci_low']:.3f}, {row['priority_ci_high']:.3f}], "
            f"arrival-order {row['arrival_pair_share']:.3f} "
            f"[{row['arrival_ci_low']:.3f}, {row['arrival_ci_high']:.3f}]"
        )


if __name__ == "__main__":
    main()
