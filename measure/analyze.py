"""Pairwise agreement between block order and a pre-registered rank.

Within one slot, every pair of our landed transactions is one trial.
Agreement is the fraction of pairs whose earlier block index also has the
better (lower) hypothesis rank. Ties in the hypothesis are skipped. A rate
near 1 supports the rule; a rate near 0.5 on a large pair count is noise.
Few pairs are not a verdict.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations


def _group_key(row: dict) -> tuple:
    return (row.get("slot"), row.get("identity"), row.get("experiment_id"), row.get("window_id"))


def pairwise_agreement(rows: list[dict], rank_field: str) -> dict:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("landed") is not True:
            continue
        if not isinstance(row.get("block_index"), int):
            continue
        if not isinstance(row.get(rank_field), int):
            continue
        groups[_group_key(row)].append(row)
    pairs = 0
    agree = 0
    ties = 0
    for group in groups.values():
        for left, right in combinations(group, 2):
            earlier, later = (left, right) if left["block_index"] < right["block_index"] else (right, left)
            if earlier["block_index"] == later["block_index"]:
                continue
            rank_earlier = earlier[rank_field]
            rank_later = later[rank_field]
            if rank_earlier == rank_later:
                ties += 1
                continue
            pairs += 1
            if rank_earlier < rank_later:
                agree += 1
    rate = (agree / pairs) if pairs else None
    return {"agree": agree, "pairs": pairs, "rate": rate, "rank_field": rank_field, "ties": ties}


def summarize_hypotheses(rows: list[dict]) -> dict:
    from measure.constants import HYPOTHESES

    reports = []
    for hypothesis in HYPOTHESES:
        for field in (hypothesis["rank_field"], hypothesis["arm_rank_field"]):
            reports.append(
                {
                    "hypothesis": hypothesis["id"],
                    "statement": hypothesis["statement"],
                    **pairwise_agreement(rows, field),
                }
            )
    return {"hypotheses": reports, "ladders": ladder_taus(rows)}


def ladder_taus(rows: list[dict]) -> list[dict]:
    """Kendall tau-b of priority fee against block index, per validator ladder.

    A priority rule produces tau near -1 (higher fee, earlier index).
    """
    from collections import defaultdict

    from measure.stats import kendall_tau_b, pairwise_fractions, permutation_p_value, slot_call

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("landed") is not True:
            continue
        ladder = row.get("ladder") or row.get("arm")
        if ladder not in ("conflict", "nonconflict"):
            continue
        if not isinstance(row.get("block_index"), int):
            continue
        if not isinstance(row.get("priority_fee_lamports"), int):
            continue
        groups[(row.get("identity"), ladder)].append(row)
    reports = []
    for (identity, ladder), group in sorted(groups.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        fees = [int(row["priority_fee_lamports"]) for row in group]
        indexes = [int(row["block_index"]) for row in group]
        sends = [int(row.get("send_index") or 0) for row in group]
        tau = kendall_tau_b([float(fee) for fee in fees], [float(index) for index in indexes])
        fractions = pairwise_fractions(fees, indexes, sends)
        p_value = permutation_p_value(fees, indexes)
        reports.append(
            {
                "block_indexes": indexes,
                "call": slot_call(
                    n_landed=len(group),
                    tau=tau,
                    p_perm=p_value,
                    priority_pairwise=fractions["priority_pairwise"],
                    send_pairwise=fractions["send_pairwise"],
                ),
                "fees": fees,
                "identity": identity,
                "ladder": ladder,
                "n": len(group),
                "p_perm": p_value,
                "priority_pairwise": fractions["priority_pairwise"],
                "send_pairwise": fractions["send_pairwise"],
                "tau_fee_block_index": tau,
            }
        )
    return reports
