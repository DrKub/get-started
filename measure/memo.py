"""On-chain memo tags. Schema version 1, pipe-separated, ASCII."""

from __future__ import annotations

import re

_EXPERIMENT_ID = re.compile(r"[A-Za-z0-9_-]+")
_ARMS = frozenset({"ladder", "arrival", "metric-price", "metric-total"})
_PATHS = frozenset({"tpu", "rpc"})


def render_memo(
    *,
    experiment_id: str,
    seq: int,
    arm: str,
    send_index: int,
    path: str,
    priority_fee_lamports: int,
    cu_price_micro_lamports: int,
    cu_limit: int,
) -> str:
    if not _EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError(f"experiment id is not memo-safe: {experiment_id!r}")
    if arm not in _ARMS:
        raise ValueError(f"unknown arm {arm}")
    if path not in _PATHS:
        raise ValueError(f"unknown path {path}")
    return (
        f"ib|1|{experiment_id}|{seq}|{arm}|{send_index}|{path}"
        f"|p{priority_fee_lamports}|c{cu_price_micro_lamports}|l{cu_limit}"
    )


def _prefixed_int(token: str, prefix: str) -> int:
    if not token.startswith(prefix) or token == prefix:
        raise ValueError(token)
    return int(token[len(prefix) :])


def parse_memo(text: str) -> dict | None:
    """Return the tag fields, or None when ``text`` is not our schema."""
    if not text.startswith("ib|1|"):
        return None
    parts = text.split("|")
    if len(parts) != 10:
        return None
    experiment_id, seq_text, arm, send_text, path = parts[2:7]
    if not _EXPERIMENT_ID.fullmatch(experiment_id):
        return None
    if arm not in _ARMS or path not in _PATHS:
        return None
    try:
        seq = int(seq_text)
        send_index = int(send_text)
        priority_fee = _prefixed_int(parts[7], "p")
        cu_price = _prefixed_int(parts[8], "c")
        cu_limit = _prefixed_int(parts[9], "l")
    except ValueError:
        return None
    if min(seq, send_index, priority_fee, cu_price, cu_limit) < 0:
        return None
    return {
        "experiment_id": experiment_id,
        "seq": seq,
        "arm": arm,
        "send_index": send_index,
        "path": path,
        "priority_fee_lamports": priority_fee,
        "cu_price_micro_lamports": cu_price,
        "cu_limit": cu_limit,
    }
