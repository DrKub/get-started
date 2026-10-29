"""Reproducible analysis for the validator ordering mainnet runs.

The implementation deliberately uses only the Python standard library. Exact
permutation p-values enumerate every ordering (the supplied ladders have at
most six rungs). Bootstrap intervals resample whole validator-run ladders so
the pair comparisons within one ladder are not treated as independent.
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from itertools import combinations, permutations
from pathlib import Path
from statistics import mean, median
from typing import Iterable

LADDERS = ("conflict", "nonconflict")
OUTPUT_FIELDS = (
    "run",
    "identity",
    "client",
    "version",
    "ladder",
    "slot",
    "n",
    "tau_b",
    "exact_p_value",
    "priority_pairs",
    "arrival_pairs",
    "pairs",
    "priority_pair_share",
    "arrival_pair_share",
    "call",
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def kendall_tau_b(xs: list[float], ys: list[float]) -> float | None:
    """Return Kendall tau-b, including the standard correction for ties."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    concordant = discordant = ties_x = ties_y = 0
    for i, j in combinations(range(len(xs)), 2):
        dx, dy = xs[i] - xs[j], ys[i] - ys[j]
        if dx == 0 and dy == 0:
            continue
        if dx == 0:
            ties_x += 1
        elif dy == 0:
            ties_y += 1
        elif dx * dy > 0:
            concordant += 1
        else:
            discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + ties_x)
        * (concordant + discordant + ties_y)
    )
    return None if denominator == 0 else (concordant - discordant) / denominator


def exact_directional_p_value(fees: list[int], indexes: list[int]) -> float | None:
    """Exact one-sided permutation probability in the observed direction."""
    observed = kendall_tau_b(fees, indexes)
    if observed is None:
        return None
    if len(indexes) > 9:
        raise ValueError("exact permutation enumeration is limited to 9 rungs")
    extreme = total = 0
    for permuted in permutations(indexes):
        total += 1
        tau = kendall_tau_b(fees, list(permuted))
        if tau is None:
            continue
        if (
            (observed < 0 and tau <= observed)
            or (observed > 0 and tau >= observed)
            or (observed == 0 and tau == 0)
        ):
            extreme += 1
    return extreme / total


def pair_counts(rows: list[dict]) -> tuple[int, int, int]:
    """Count fee-priority and send-arrival agreements within one ladder."""
    priority = arrival = pairs = 0
    for left, right in combinations(rows, 2):
        earlier, later = (
            (left, right)
            if left["block_index"] < right["block_index"]
            else (right, left)
        )
        pairs += 1
        priority += earlier["priority_fee_lamports"] > later["priority_fee_lamports"]
        arrival += earlier["send_index"] < later["send_index"]
    return priority, arrival, pairs


def classify(
    n: int,
    tau: float | None,
    p_value: float | None,
    priority_share: float | None,
    arrival_share: float | None,
) -> str:
    """Apply the pre-registered decision rule from docs/METHODOLOGY.md."""
    if n < 5 or tau is None or p_value is None:
        return "inconclusive"
    if math.isclose(tau, -1.0):
        return "strict_priority"
    if tau <= -0.6 and p_value <= 0.05 and (priority_share or 0) > 0.5:
        return "priority"
    if tau >= 0.6 and p_value <= 0.05 and (arrival_share or 0) > 0.5:
        return "arrival"
    return "no_rule_detected"


def _validator_map(run_dir: Path) -> dict[str, dict]:
    return {row["identity"]: row for row in json.loads((run_dir / "validators.json").read_text())}


def analyze_run(run_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    observations = [
        row
        for row in read_jsonl(run_dir / "block_observations.jsonl")
        if row.get("landed") is True and row.get("ladder") in LADDERS
    ]
    contexts = {row["slot"]: row for row in read_jsonl(run_dir / "block_context.jsonl")}
    validators = _validator_map(run_dir)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in observations:
        groups[(row["identity"], row["ladder"], row["slot"])].append(row)

    statistics: list[dict] = []
    evidence: list[dict] = []
    positions: list[dict] = []
    for (identity, ladder, slot), rows in sorted(groups.items()):
        rows.sort(key=lambda row: row["send_index"])
        fees = [row["priority_fee_lamports"] for row in rows]
        indexes = [row["block_index"] for row in rows]
        if len(fees) != len(set(fees)):
            raise ValueError(f"{run_dir.name}/{identity}/{ladder}: duplicate fee rung")
        tau = kendall_tau_b(fees, indexes)
        p_value = exact_directional_p_value(fees, indexes)
        priority, arrival, pairs = pair_counts(rows)
        priority_share = priority / pairs if pairs else None
        arrival_share = arrival / pairs if pairs else None
        validator = validators[identity]
        record = {
            "run": run_dir.name,
            "identity": identity,
            "client": validator["client_id"],
            "version": validator["version"],
            "ladder": ladder,
            "slot": slot,
            "n": len(rows),
            "tau_b": tau,
            "exact_p_value": p_value,
            "priority_pairs": priority,
            "arrival_pairs": arrival,
            "pairs": pairs,
            "priority_pair_share": priority_share,
            "arrival_pair_share": arrival_share,
            "call": classify(
                len(rows), tau, p_value, priority_share, arrival_share
            ),
        }
        statistics.append(record)

        context = contexts.get(slot)
        if context is None:
            raise ValueError(f"{run_dir.name}: missing block_context for slot {slot}")
        context_by_signature = {
            tx["signature"]: tx for tx in context["transactions"]
        }
        for row in rows:
            transaction = context_by_signature.get(row["signature"])
            if transaction is None or transaction["block_index"] != row["block_index"]:
                raise ValueError(
                    f"{run_dir.name}: block_context mismatch for {row['signature']}"
                )
            evidence.append(
                {
                    "claim_id": f"{run_dir.name}:{identity}:{ladder}",
                    "run": run_dir.name,
                    "client": validator["client_id"],
                    "identity": identity,
                    "ladder": ladder,
                    "call": record["call"],
                    "slot": slot,
                    "signature": row["signature"],
                    "priority_fee_lamports": row["priority_fee_lamports"],
                    "send_index": row["send_index"],
                    "block_index": row["block_index"],
                    "explorer_url": (
                        f"https://explorer.solana.com/tx/{row['signature']}?cluster=mainnet"
                    ),
                }
            )

        ordered_indexes = sorted(indexes)
        index_gaps = [
            right - left for left, right in zip(ordered_indexes, ordered_indexes[1:])
        ]
        foreign_gaps = [gap - 1 for gap in index_gaps]
        denominator = max(context["tx_count"] - 1, 1)
        positions.append(
            {
                "run": run_dir.name,
                "identity": identity,
                "client": validator["client_id"],
                "ladder": ladder,
                "slot": slot,
                "block_tx_count": context["tx_count"],
                "first_block_index": ordered_indexes[0],
                "last_block_index": ordered_indexes[-1],
                "first_block_fraction": ordered_indexes[0] / denominator,
                "last_block_fraction": ordered_indexes[-1] / denominator,
                "span_indexes": ordered_indexes[-1] - ordered_indexes[0],
                "adjacent_index_gaps": index_gaps,
                "foreign_txs_between_rungs": foreign_gaps,
                "mean_foreign_txs_between_rungs": mean(foreign_gaps),
                "median_foreign_txs_between_rungs": median(foreign_gaps),
                "max_foreign_txs_between_rungs": max(foreign_gaps),
            }
        )
    return statistics, evidence, positions


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * probability)]


def pooled_pair_shares(
    statistics: list[dict], *, seed: int, replicates: int
) -> list[dict]:
    """Pool pair counts and cluster-bootstrap complete ladder observations."""
    rng = random.Random(seed)
    output = []
    scopes = [
        ("all", statistics),
        *[(ladder, [row for row in statistics if row["ladder"] == ladder]) for ladder in LADDERS],
    ]
    for scope, rows in scopes:
        priority = sum(row["priority_pairs"] for row in rows)
        arrival = sum(row["arrival_pairs"] for row in rows)
        pairs = sum(row["pairs"] for row in rows)
        priority_draws, arrival_draws = [], []
        for _ in range(replicates):
            sampled = [rng.choice(rows) for _ in rows]
            sampled_pairs = sum(row["pairs"] for row in sampled)
            priority_draws.append(
                sum(row["priority_pairs"] for row in sampled) / sampled_pairs
            )
            arrival_draws.append(
                sum(row["arrival_pairs"] for row in sampled) / sampled_pairs
            )
        output.append(
            {
                "scope": scope,
                "ladder_clusters": len(rows),
                "pairs": pairs,
                "priority_pairs": priority,
                "priority_pair_share": priority / pairs,
                "priority_ci_low": _percentile(priority_draws, 0.025),
                "priority_ci_high": _percentile(priority_draws, 0.975),
                "arrival_pairs": arrival,
                "arrival_pair_share": arrival / pairs,
                "arrival_ci_low": _percentile(arrival_draws, 0.025),
                "arrival_ci_high": _percentile(arrival_draws, 0.975),
                "bootstrap_unit": "validator-run ladder",
                "bootstrap_replicates": replicates,
                "bootstrap_seed": seed,
            }
        )
    return output


def cross_run_consistency(statistics: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in statistics:
        grouped[(row["identity"], row["client"], row["ladder"])].append(row)
    output = []
    for (identity, client, ladder), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row["run"])
        eligible = [row for row in rows if row["n"] >= 5]
        if len(eligible) < 2:
            status = "insufficient_second_run"
        elif len({row["call"] for row in eligible}) == 1:
            status = "same_call"
        else:
            status = "different_call"
        output.append(
            {
                "identity": identity,
                "client": client,
                "ladder": ladder,
                "runs": [row["run"] for row in rows],
                "n": [row["n"] for row in rows],
                "calls": [row["call"] for row in rows],
                "tau_b": [row["tau_b"] for row in rows],
                "priority_pair_share": [
                    row["priority_pair_share"] for row in rows
                ],
                "consistency": status,
            }
        )
    return output


def position_summary(positions: list[dict]) -> list[dict]:
    output = []
    for ladder in ("all", *LADDERS):
        rows = positions if ladder == "all" else [
            row for row in positions if row["ladder"] == ladder
        ]
        all_gaps = [
            gap for row in rows for gap in row["foreign_txs_between_rungs"]
        ]
        output.append(
            {
                "scope": ladder,
                "ladders": len(rows),
                "min_first_block_fraction": min(
                    row["first_block_fraction"] for row in rows
                ),
                "median_first_block_fraction": median(
                    row["first_block_fraction"] for row in rows
                ),
                "max_last_block_fraction": max(
                    row["last_block_fraction"] for row in rows
                ),
                "median_block_tx_count": median(
                    row["block_tx_count"] for row in rows
                ),
                "median_foreign_txs_between_rungs": median(all_gaps),
                "mean_foreign_txs_between_rungs": mean(all_gaps),
                "max_foreign_txs_between_rungs": max(all_gaps),
            }
        )
    return output


def _write_csv(path: Path, rows: list[dict], fields: Iterable[str] | None = None) -> None:
    fieldnames = list(fields or rows[0].keys())
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, separators=(",", ":"))
                    if isinstance(value, list)
                    else value
                    for key, value in row.items()
                }
            )


def write_outputs(
    data_root: Path, output_dir: Path, *, seed: int = 20261029, replicates: int = 10_000
) -> dict:
    statistics: list[dict] = []
    evidence: list[dict] = []
    positions: list[dict] = []
    run_dirs = sorted(
        path for path in data_root.iterdir() if (path / "manifest.json").exists()
    )
    if not run_dirs:
        raise ValueError(f"no runs found under {data_root}")
    for run_dir in run_dirs:
        run_stats, run_evidence, run_positions = analyze_run(run_dir)
        statistics.extend(run_stats)
        evidence.extend(run_evidence)
        positions.extend(run_positions)

    pooled = pooled_pair_shares(statistics, seed=seed, replicates=replicates)
    consistency = cross_run_consistency(statistics)
    position_rollup = position_summary(positions)
    result = {
        "inputs": [run_dir.name for run_dir in run_dirs],
        "method": {
            "p_value": "exact one-sided permutation enumeration in observed direction",
            "bootstrap": "percentile CI resampling validator-run ladders",
            "bootstrap_seed": seed,
            "bootstrap_replicates": replicates,
        },
        "ladder_statistics": statistics,
        "pooled_pair_shares": pooled,
        "cross_run_consistency": consistency,
        "block_position_summary": position_rollup,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    _write_csv(output_dir / "ladder_statistics.csv", statistics, OUTPUT_FIELDS)
    _write_csv(output_dir / "pooled_pair_shares.csv", pooled)
    _write_csv(output_dir / "cross_run_consistency.csv", consistency)
    _write_csv(output_dir / "block_positions.csv", positions)
    _write_csv(output_dir / "evidence_transactions.csv", evidence)
    return result
