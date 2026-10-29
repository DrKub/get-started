"""Kendall tau-b and the pre-registered slot call.

``block_index`` 0 is first in the block. Low-fee-first transmission makes a
priority rule produce tau-b near -1 and an arrival rule produce tau-b near +1.
"""

from __future__ import annotations

import math
from itertools import permutations


def kendall_tau_b(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    concordant = 0
    discordant = 0
    ties_x = 0
    ties_y = 0
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            dx = xs[i] - xs[j]
            dy = ys[i] - ys[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
                continue
            if dy == 0:
                ties_y += 1
                continue
            if dx * dy > 0:
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + ties_x) * (concordant + discordant + ties_y))
    if denom == 0:
        return None
    return (concordant - discordant) / denom


def pairwise_fractions(fees: list[int], block_indexes: list[int], send_indexes: list[int]) -> dict:
    priority = 0
    send = 0
    pairs = 0
    for i in range(len(fees)):
        for j in range(i + 1, len(fees)):
            if block_indexes[i] == block_indexes[j]:
                continue
            pairs += 1
            earlier, later = (i, j) if block_indexes[i] < block_indexes[j] else (j, i)
            if fees[earlier] > fees[later]:
                priority += 1
            if send_indexes[earlier] < send_indexes[later]:
                send += 1
    return {
        "pairs": pairs,
        "priority_pairwise": (priority / pairs) if pairs else None,
        "send_pairwise": (send / pairs) if pairs else None,
    }


def permutation_p_value(fees: list[int], block_indexes: list[int], *, seed: int = 0) -> float | None:
    """One-sided probability of a tau at least as far from 0 as observed."""
    observed = kendall_tau_b(fees, block_indexes)
    if observed is None:
        return None
    n = len(fees)
    if n > 7:
        return _monte_carlo(fees, block_indexes, observed, seed)
    as_extreme = 0
    total = 0
    for perm in permutations(block_indexes):
        total += 1
        tau = kendall_tau_b(fees, list(perm))
        if tau is None:
            continue
        if observed < 0 and tau <= observed:
            as_extreme += 1
        elif observed > 0 and tau >= observed:
            as_extreme += 1
        elif observed == 0 and tau == 0:
            as_extreme += 1
    return as_extreme / total if total else None


def _monte_carlo(fees, block_indexes, observed: float, seed: int) -> float:
    import random

    rng = random.Random(seed)
    draws = 5000
    as_extreme = 0
    for _ in range(draws):
        shuffled = list(block_indexes)
        rng.shuffle(shuffled)
        tau = kendall_tau_b(fees, shuffled)
        if tau is None:
            continue
        if observed < 0 and tau <= observed:
            as_extreme += 1
        elif observed > 0 and tau >= observed:
            as_extreme += 1
        elif observed == 0 and tau == 0:
            as_extreme += 1
    return as_extreme / draws


def slot_call(
    *,
    n_landed: int,
    tau: float | None,
    p_perm: float | None,
    priority_pairwise: float | None,
    send_pairwise: float | None,
    tau_send: float | None = None,
    p_send: float | None = None,
) -> str:
    """``tau``/``p_perm`` are fee vs block index. The arrival test uses send
    index vs block index when given; on a lowest-fee-first ladder they coincide."""
    if n_landed < 5 or tau is None or p_perm is None:
        return "inconclusive"
    if tau == -1:
        return "strict_priority"
    if (
        tau <= -0.6
        and p_perm <= 0.05
        and priority_pairwise is not None
        and priority_pairwise > 0.5
    ):
        return "priority"
    arrival_tau = tau if tau_send is None else tau_send
    arrival_p = p_perm if p_send is None else p_send
    if (
        arrival_tau is not None
        and arrival_p is not None
        and arrival_tau >= 0.6
        and arrival_p <= 0.05
        and send_pairwise is not None
        and send_pairwise > 0.5
    ):
        return "arrival"
    return "no_rule_detected"
