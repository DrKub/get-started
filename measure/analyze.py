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
    return {"hypotheses": reports}
