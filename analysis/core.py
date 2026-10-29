"""Reproducible analysis for the validator ordering mainnet runs.

The implementation deliberately uses only the Python standard library. Exact
permutation p-values enumerate every ordering (the supplied ladders have at
most six rungs). Bootstrap intervals resample whole validator-run ladders so
the pair comparisons within one ladder are not treated as independent.
"""

from __future__ import annotations

import csv
import gzip
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
    "send_order",
    "tau_fee_send",
    "tau_send_b",
    "target_slot",
    "slot_minus_target",
    "block_leader",
    "leader_matches",
    "ladder_writable_accounts",
    "foreign_writers",
    "contaminated",
)
ROLLUP_FIELDS = (
    "identity",
    "client",
    "version",
    "ladder",
    "slot_calls",
    "headline_call",
    "inclusive_call",
    "contaminated_slots",
    "inconclusive_slots",
)
SOLSCAN_TX = "https://solscan.io/tx/{}"
SOLSCAN_BLOCK = "https://solscan.io/block/{}"
EXPLORER_TX = "https://explorer.solana.com/tx/{}?cluster=mainnet"
EXPLORER_BLOCK = "https://explorer.solana.com/block/{}?cluster=mainnet"
# Programs every probe touches. Never writable, listed only so a malformed
# block cannot make them look like a shared write lock.
READONLY_PROGRAMS = frozenset(
    {
        "11111111111111111111111111111111",
        "ComputeBudget111111111111111111111111111111",
    }
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_raw_block(run_dir: Path, slot: int) -> dict | None:
    """Return the stored ``getBlock`` result for ``slot``, or None if absent."""
    for name in (f"{slot}.json.gz", f"{slot}.json"):
        path = run_dir / "blocks" / name
        if not path.exists():
            continue
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rt") as handle:
            body = json.load(handle)
        return body.get("result", body)
    return None


def block_leader(block: dict) -> str | None:
    """The identity that received this block's fee reward, i.e. its leader."""
    for reward in block.get("rewards") or []:
        if reward.get("rewardType") == "Fee":
            return reward.get("pubkey")
    return None


def _writable_keys(transaction: dict) -> set[str]:
    keys = transaction["transaction"]["accountKeys"]
    return {
        key["pubkey"]
        for key in keys
        if key.get("writable") and key["pubkey"] not in READONLY_PROGRAMS
    }


def raw_block_checks(
    block: dict, ladder: str, rows: list[dict], ours: set[str]
) -> dict:
    """Re-derive positions from the raw block and test for foreign write locks.

    The conflict ladder's lock set is the writable accounts every rung shares.
    The non-conflict ladder's lock set is the union of each rung's writable
    accounts; those must be pairwise disjoint or the ladder was not
    non-conflicting. A foreign transaction that writes any account in the lock
    set makes the slot ``contaminated``.
    """
    transactions = block["transactions"]
    position = {tx["transaction"]["signatures"][0]: i for i, tx in enumerate(transactions)}
    writable = {}
    for row in rows:
        index = position.get(row["signature"])
        if index is None or index != row["block_index"]:
            raise ValueError(
                f"raw block disagrees with block_index for {row['signature']}: {index}"
            )
        writable[row["signature"]] = _writable_keys(transactions[index])
    sets = list(writable.values())
    if ladder == "conflict":
        lock_set = set.intersection(*sets) if sets else set()
        if not lock_set:
            raise ValueError("conflict ladder rungs share no writable account")
    else:
        lock_set = set.union(*sets) if sets else set()
        if sum(len(item) for item in sets) != len(lock_set):
            raise ValueError("non-conflict ladder rungs share a writable account")
    foreign = 0
    for tx in transactions:
        if tx["transaction"]["signatures"][0] in ours:
            continue
        if _writable_keys(tx) & lock_set:
            foreign += 1
    return {
        "block_leader": block_leader(block),
        "raw_tx_count": len(transactions),
        "ladder_writable_accounts": sorted(lock_set),
        "foreign_writers": foreign,
        "contaminated": foreign > 0,
    }


def send_order_label(fees: list[int], sends: list[int]) -> str:
    tau = kendall_tau_b(fees, sends)
    if tau is not None and math.isclose(tau, 1.0):
        return "ascending"
    if tau is not None and math.isclose(tau, -1.0):
        return "descending"
    return "shuffled"


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
    tau_send: float | None = None,
    p_send: float | None = None,
) -> str:
    """Apply the pre-registered decision rule from docs/METHODOLOGY.md.

    ``tau`` and ``p_value`` are fee versus block index. The arrival test uses
    send index versus block index when given. On a lowest-fee-first ladder the
    two are the same statistic, which is exactly the pre-registered rule.
    """
    if n < 5 or tau is None or p_value is None:
        return "inconclusive"
    if math.isclose(tau, -1.0):
        return "strict_priority"
    if tau <= -0.6 and p_value <= 0.05 and (priority_share or 0) > 0.5:
        return "priority"
    arrival_tau = tau if tau_send is None else tau_send
    arrival_p = p_value if p_send is None else p_send
    if (
        arrival_tau is not None
        and arrival_p is not None
        and arrival_tau >= 0.6
        and arrival_p <= 0.05
        and (arrival_share or 0) > 0.5
    ):
        return "arrival"
    return "no_rule_detected"


def rollup(calls: list[str]) -> str:
    """Validator call for one ladder type from its slot calls (methodology §9.3)."""
    if not calls:
        return "inconclusive"
    labels = set(calls)
    if labels == {"strict_priority"}:
        return "strict_priority"
    if labels <= {"strict_priority", "priority"}:
        return "priority"
    if labels == {"arrival"}:
        return "arrival"
    if labels == {"no_rule_detected"}:
        return "no_rule_detected"
    return "mixed_across_slots"


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
    attempts = {
        row["signature"]: row
        for row in read_jsonl(run_dir / "attempts.jsonl")
        if row.get("signature")
    }
    ours = {
        row["signature"]
        for row in read_jsonl(run_dir / "block_observations.jsonl")
        if row.get("signature")
    } | set(attempts)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in observations:
        groups[(row["identity"], row["ladder"], row["slot"])].append(row)

    statistics: list[dict] = []
    evidence: list[dict] = []
    positions: list[dict] = []
    for (identity, ladder, slot), rows in sorted(groups.items()):
        rows.sort(key=lambda row: row["send_index"])
        fees = [row["priority_fee_lamports"] for row in rows]
        sends = [row["send_index"] for row in rows]
        indexes = [row["block_index"] for row in rows]
        if len(fees) != len(set(fees)):
            raise ValueError(f"{run_dir.name}/{identity}/{ladder}: duplicate fee rung")
        tau = kendall_tau_b(fees, indexes)
        p_value = exact_directional_p_value(fees, indexes)
        tau_send = kendall_tau_b(sends, indexes)
        p_send = exact_directional_p_value(sends, indexes)
        priority, arrival, pairs = pair_counts(rows)
        priority_share = priority / pairs if pairs else None
        arrival_share = arrival / pairs if pairs else None
        validator = validators[identity]
        targets = {attempts[row["signature"]]["target_slot"] for row in rows}
        if len(targets) != 1:
            raise ValueError(f"{run_dir.name}/{identity}/{ladder}: rungs disagree on target slot")
        target_slot = targets.pop()
        block = load_raw_block(run_dir, slot)
        checks = (
            raw_block_checks(block, ladder, rows, ours)
            if block is not None
            else {
                "block_leader": None,
                "raw_tx_count": None,
                "ladder_writable_accounts": None,
                "foreign_writers": None,
                "contaminated": None,
            }
        )
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
                len(rows), tau, p_value, priority_share, arrival_share, tau_send, p_send
            ),
            "send_order": send_order_label(fees, sends),
            "tau_fee_send": kendall_tau_b(fees, sends),
            "tau_send_b": tau_send,
            "target_slot": target_slot,
            "slot_minus_target": slot - target_slot,
            "block_leader": checks["block_leader"],
            "leader_matches": (
                None if checks["block_leader"] is None else checks["block_leader"] == identity
            ),
            "ladder_writable_accounts": checks["ladder_writable_accounts"],
            "foreign_writers": checks["foreign_writers"],
            "contaminated": checks["contaminated"],
        }
        statistics.append(record)

        context = contexts.get(slot)
        if context is None:
            raise ValueError(f"{run_dir.name}: missing block_context for slot {slot}")
        if checks["raw_tx_count"] is not None and checks["raw_tx_count"] != context["tx_count"]:
            raise ValueError(f"{run_dir.name}: raw block {slot} tx count disagrees with block_context")
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
                    "explorer_url": EXPLORER_TX.format(row["signature"]),
                    "solscan_url": SOLSCAN_TX.format(row["signature"]),
                    "block_url": SOLSCAN_BLOCK.format(slot),
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


def validator_rollups(statistics: list[dict]) -> list[dict]:
    """Headline (uncontaminated) and inclusive validator calls per ladder type."""
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in statistics:
        grouped[(row["identity"], row["client"], row["version"], row["ladder"])].append(row)
    output = []
    for (identity, client, version, ladder), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row["run"])
        powered = [row for row in rows if row["call"] != "inconclusive"]
        output.append(
            {
                "identity": identity,
                "client": client,
                "version": version,
                "ladder": ladder,
                "slot_calls": [row["call"] for row in rows],
                "headline_call": rollup(
                    [row["call"] for row in powered if row["contaminated"] is not True]
                ),
                "inclusive_call": rollup([row["call"] for row in powered]),
                "contaminated_slots": sum(row["contaminated"] is True for row in rows),
                "inconclusive_slots": len(rows) - len(powered),
            }
        )
    return output


def claim_anchor(run: str, client: str, ladder: str) -> str:
    return f"{run}-{client}-{ladder}".lower()


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:+.{digits}f}" if digits == 3 else f"{value:.{digits}f}"
    return str(value)


def evidence_markdown(statistics: list[dict], evidence: list[dict]) -> str:
    """One section per claim, every rung linked to Solscan and Explorer."""
    by_claim: dict[str, list[dict]] = defaultdict(list)
    for row in evidence:
        by_claim[row["claim_id"]].append(row)
    lines = [
        "# Evidence index",
        "",
        "Generated by `python -m analysis` from `data/runs/`. Do not edit by hand.",
        "",
        "One section per validator-run ladder. Every probe that entered a statistic is listed",
        "with its signature, the slot it landed in, its block index (zero-based position in",
        "`getBlock().transactions`, votes included), its priority fee, and its send index",
        "(0 = first stream written). The block leader is the `Fee` reward recipient in the",
        "stored raw block `data/runs/<run>/blocks/<slot>.json.gz`.",
        "",
    ]
    for stat in sorted(statistics, key=lambda r: (r["run"], r["client"], r["ladder"])):
        claim_id = f"{stat['run']}:{stat['identity']}:{stat['ladder']}"
        anchor = claim_anchor(stat["run"], stat["client"], stat["ladder"])
        slot = stat["slot"]
        lines += [
            f'<a id="{anchor}"></a>',
            "",
            f"## {stat['run']} · {stat['client']} {stat['version']} · {stat['ladder']}",
            "",
            f"- Leader identity: `{stat['identity']}`; block leader from rewards: "
            f"`{stat['block_leader'] or 'n/a'}`",
            f"- Slot: [{slot}]({SOLSCAN_BLOCK.format(slot)}) "
            f"([Explorer]({EXPLORER_BLOCK.format(slot)})); target slot {stat['target_slot']} "
            f"(landed {stat['slot_minus_target']:+d})",
            f"- n = {stat['n']}, tau-b(fee, block index) = {_fmt(stat['tau_b'])}, "
            f"exact one-sided p = {_fmt(stat['exact_p_value'], 6)}, call `{stat['call']}`",
            f"- Send order: {stat['send_order']} (tau-b(fee, send index) = "
            f"{_fmt(stat['tau_fee_send'])})",
            f"- Writable accounts this ladder locks: "
            + (
                ", ".join(f"`{key}`" for key in stat["ladder_writable_accounts"])
                if stat["ladder_writable_accounts"]
                else "n/a"
            )
            + f"; foreign transactions writing them: {_fmt(stat['foreign_writers'])}",
            "",
            "| send index | priority fee (lamports) | block index | signature |",
            "| ---: | ---: | ---: | --- |",
        ]
        for row in sorted(by_claim[claim_id], key=lambda r: r["send_index"]):
            sig = row["signature"]
            lines.append(
                f"| {row['send_index']} | {row['priority_fee_lamports']} | {row['block_index']} | "
                f"[`{sig[:16]}…`]({SOLSCAN_TX.format(sig)}) ([Explorer]({EXPLORER_TX.format(sig)})) |"
            )
        lines.append("")
    return "\n".join(lines)


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
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
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
    rollups = validator_rollups(statistics)
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
        "validator_rollups": rollups,
        "block_position_summary": position_rollup,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "EVIDENCE.md").write_text(evidence_markdown(statistics, evidence))
    _write_csv(output_dir / "ladder_statistics.csv", statistics, OUTPUT_FIELDS)
    _write_csv(output_dir / "validator_rollups.csv", rollups, ROLLUP_FIELDS)
    _write_csv(output_dir / "pooled_pair_shares.csv", pooled)
    _write_csv(output_dir / "cross_run_consistency.csv", consistency)
    _write_csv(output_dir / "block_positions.csv", positions)
    _write_csv(output_dir / "evidence_transactions.csv", evidence)
    return result
