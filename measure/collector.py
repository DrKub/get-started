"""Parse ``getBlock`` (jsonParsed or raw) into positions and fee fields.

Standard ``getBlock`` does not return PoH/entry boundaries, including after
Alpenglow removed tick entries from the shred stream. ``entry_index`` is null
unless a future response carries ``entryIndex`` on the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from measure.base58 import b58decode
from measure.constants import COMPUTE_BUDGET_PROGRAM_ID, MEMO_PROGRAM_IDS
from measure.memo import parse_memo

_LIMIT_KEYS = ("units", "computeUnitLimit")
_PRICE_KEYS = ("microLamports", "microLamportsPerCu", "computeUnitPrice")


@dataclass
class ParsedTx:
    index: int
    signature: str | None
    fee: int | None
    cu_consumed: int | None
    cost_units: int | None
    execution_err: object
    cu_limit: int | None
    cu_price_micro_lamports: int | None
    memo: str | None
    fee_payer: str | None
    version: object
    entry_index: int | None
    program_ids: list[str] = field(default_factory=list)


@dataclass
class ParsedBlock:
    slot: int
    block_time: int | None
    block_height: int | None
    parent_slot: int | None
    blockhash: str | None
    transactions: list[ParsedTx]
    entry_index_available: bool


def _account_key(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("pubkey"), str):
        return item["pubkey"]
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _decode_compute_budget(data: bytes) -> tuple[str, int] | None:
    if not data:
        return None
    op = data[0]
    if op == 2 and len(data) >= 5:
        return "limit", int.from_bytes(data[1:5], "little")
    if op == 3 and len(data) >= 9:
        return "price", int.from_bytes(data[1:9], "little")
    return None


def _instruction_program(ix: dict, account_keys: list[str]) -> str | None:
    program_id = ix.get("programId")
    if isinstance(program_id, str):
        return program_id
    index = ix.get("programIdIndex")
    if isinstance(index, int) and 0 <= index < len(account_keys):
        return account_keys[index]
    return None


def _apply_parsed_compute(ix: dict, cu_limit: int | None, cu_price: int | None):
    parsed = ix.get("parsed")
    if not isinstance(parsed, dict):
        return cu_limit, cu_price
    kind = parsed.get("type")
    info = parsed.get("info") if isinstance(parsed.get("info"), dict) else {}
    if kind == "setComputeUnitLimit":
        for key in _LIMIT_KEYS:
            value = _as_int(info.get(key))
            if value is not None:
                return value, cu_price
    if kind == "setComputeUnitPrice":
        for key in _PRICE_KEYS:
            value = _as_int(info.get(key))
            if value is not None:
                return cu_limit, value
    return cu_limit, cu_price


def _memo_from_logs(logs: object) -> str | None:
    if not isinstance(logs, list):
        return None
    prefix = 'Program log: Memo (len '
    for line in logs:
        if not isinstance(line, str) or not line.startswith(prefix):
            continue
        marker = '): "'
        start = line.find(marker)
        if start == -1 or not line.endswith('"'):
            continue
        return line[start + len(marker) : -1]
    return None


def parse_transaction(tx: dict, index: int) -> ParsedTx:
    meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
    message = {}
    signatures: list = []
    body = tx.get("transaction")
    if isinstance(body, dict):
        message = body.get("message") if isinstance(body.get("message"), dict) else {}
        signatures = body.get("signatures") if isinstance(body.get("signatures"), list) else []
    account_items = message.get("accountKeys") if isinstance(message.get("accountKeys"), list) else []
    account_keys = [key for key in (_account_key(item) for item in account_items) if key]
    cu_limit = None
    cu_price = None
    memo = None
    program_ids: list[str] = []
    instructions = message.get("instructions") if isinstance(message.get("instructions"), list) else []
    for ix in instructions:
        if not isinstance(ix, dict):
            continue
        program_id = _instruction_program(ix, account_keys)
        if program_id:
            program_ids.append(program_id)
        if program_id == COMPUTE_BUDGET_PROGRAM_ID:
            cu_limit, cu_price = _apply_parsed_compute(ix, cu_limit, cu_price)
            data = ix.get("data")
            if isinstance(data, str):
                try:
                    decoded = _decode_compute_budget(b58decode(data))
                except ValueError:
                    decoded = None
                if decoded and decoded[0] == "limit" and cu_limit is None:
                    cu_limit = decoded[1]
                elif decoded and decoded[0] == "price" and cu_price is None:
                    cu_price = decoded[1]
        parsed = ix.get("parsed")
        if isinstance(parsed, str) and (program_id in MEMO_PROGRAM_IDS or ix.get("program") == "spl-memo"):
            memo = parsed
        elif program_id in MEMO_PROGRAM_IDS and isinstance(ix.get("data"), str) and memo is None:
            try:
                memo = b58decode(ix["data"]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                memo = None
    if memo is None:
        memo = _memo_from_logs(meta.get("logMessages"))
    entry = tx.get("entryIndex", tx.get("entry_index"))
    entry_index = entry if isinstance(entry, int) else None
    signature = signatures[0] if signatures and isinstance(signatures[0], str) else None
    return ParsedTx(
        index=index,
        signature=signature,
        fee=_as_int(meta.get("fee")),
        cu_consumed=_as_int(meta.get("computeUnitsConsumed")),
        cost_units=_as_int(meta.get("costUnits")),
        execution_err=meta.get("err"),
        cu_limit=cu_limit,
        cu_price_micro_lamports=cu_price,
        memo=memo,
        fee_payer=account_keys[0] if account_keys else None,
        version=tx.get("version"),
        entry_index=entry_index,
        program_ids=program_ids,
    )


def parse_block(result: dict, slot: int) -> ParsedBlock:
    raw_txs = result.get("transactions") if isinstance(result.get("transactions"), list) else []
    parsed: list[ParsedTx] = []
    for index, tx in enumerate(raw_txs):
        if isinstance(tx, dict):
            parsed.append(parse_transaction(tx, index))
    entry_available = any(tx.entry_index is not None for tx in parsed)
    return ParsedBlock(
        slot=slot,
        block_time=result.get("blockTime") if isinstance(result.get("blockTime"), int) else None,
        block_height=result.get("blockHeight") if isinstance(result.get("blockHeight"), int) else None,
        parent_slot=result.get("parentSlot") if isinstance(result.get("parentSlot"), int) else None,
        blockhash=result.get("blockhash") if isinstance(result.get("blockhash"), str) else None,
        transactions=parsed,
        entry_index_available=entry_available,
    )


def compact_tx(tx: ParsedTx, *, ours: bool) -> dict:
    memo = tx.memo if ours else None
    tag = parse_memo(tx.memo) if tx.memo else None
    return {
        "block_index": tx.index,
        "cost_units": tx.cost_units,
        "cu_consumed": tx.cu_consumed,
        "cu_limit": tx.cu_limit,
        "cu_price_micro_lamports": tx.cu_price_micro_lamports,
        "entry_index": tx.entry_index,
        "execution_err": tx.execution_err,
        "fee_lamports": tx.fee,
        "fee_payer": tx.fee_payer,
        "is_ours": ours or bool(tag),
        "memo": memo if (ours or tag) else None,
        "signature": tx.signature,
    }


def block_context(block: ParsedBlock, our_signatures: set[str]) -> dict:
    rows = []
    our_count = 0
    for tx in block.transactions:
        tag = parse_memo(tx.memo) if tx.memo else None
        ours = bool(tag) or (tx.signature in our_signatures if tx.signature else False)
        if ours:
            our_count += 1
        rows.append(compact_tx(tx, ours=ours))
    return {
        "block_height": block.block_height,
        "block_time": block.block_time,
        "blockhash": block.blockhash,
        "entry_index_available": block.entry_index_available,
        "our_tx_count": our_count,
        "parent_slot": block.parent_slot,
        "slot": block.slot,
        "transactions": rows,
        "tx_count": len(block.transactions),
    }


def _plan_fields(plan: dict) -> dict:
    keys = (
        "arm",
        "arm_group",
        "client_family",
        "client_id",
        "cu_limit",
        "cu_price_micro_lamports",
        "experiment_id",
        "identity",
        "memo",
        "path",
        "planned_slot",
        "priority_fee_lamports",
        "rank_arm_cu_price",
        "rank_arm_priority_fee",
        "rank_arm_send_order",
        "rank_cu_price",
        "rank_priority_fee",
        "rank_send_order",
        "repeat_index",
        "send_index",
        "seq",
        "version",
        "window_id",
        "window_start_slot",
    )
    return {key: plan.get(key) for key in keys}


def build_observations(
    blocks: list[ParsedBlock],
    plans: list[dict],
    signatures_by_seq: dict[int, str] | None = None,
) -> list[dict]:
    """Match our memos to plans. Plans that never appear are ``landed: false``."""
    by_memo = {plan["memo"]: plan for plan in plans if plan.get("memo")}
    by_sig: dict[str, dict] = {}
    if signatures_by_seq:
        for plan in plans:
            signature = signatures_by_seq.get(plan.get("seq"))
            if signature:
                by_sig[signature] = plan
    found: dict[int, dict] = {}
    extras: list[dict] = []
    for block in blocks:
        for tx in block.transactions:
            tag = parse_memo(tx.memo) if tx.memo else None
            plan = None
            if tx.memo and tx.memo in by_memo:
                plan = by_memo[tx.memo]
            elif tx.signature and tx.signature in by_sig:
                plan = by_sig[tx.signature]
            elif tag is None:
                continue
            base = _plan_fields(plan) if plan else {}
            if tag and not base:
                base = {
                    "arm": tag["arm"],
                    "cu_limit": tag["cu_limit"],
                    "cu_price_micro_lamports": tag["cu_price_micro_lamports"],
                    "experiment_id": tag["experiment_id"],
                    "memo": tx.memo,
                    "path": tag["path"],
                    "priority_fee_lamports": tag["priority_fee_lamports"],
                    "send_index": tag["send_index"],
                    "seq": tag["seq"],
                }
            row = {
                **base,
                "block_height": block.block_height,
                "block_index": tx.index,
                "block_time": block.block_time,
                "cost_units": tx.cost_units,
                "cu_consumed": tx.cu_consumed,
                "cu_limit_set": tx.cu_limit,
                "cu_price_set": tx.cu_price_micro_lamports,
                "entry_index": tx.entry_index,
                "execution_err": tx.execution_err,
                "fee_lamports": tx.fee,
                "fee_payer": tx.fee_payer,
                "landed": True,
                "signature": tx.signature,
                "slot": block.slot,
                "tx_version": tx.version,
            }
            seq = row.get("seq")
            if isinstance(seq, int):
                found[seq] = row
            else:
                extras.append(row)
    missing = []
    for plan in plans:
        seq = plan.get("seq")
        if isinstance(seq, int) and seq not in found:
            missing.append(
                {
                    **_plan_fields(plan),
                    "block_index": None,
                    "entry_index": None,
                    "execution_err": None,
                    "fee_lamports": None,
                    "landed": False,
                    "signature": (signatures_by_seq or {}).get(seq),
                    "slot": None,
                }
            )
    rows = [found[key] for key in sorted(found)] + missing + extras
    return rows


def slots_to_fetch(plans: list[dict]) -> list[int]:
    slots: set[int] = set()
    for plan in plans:
        start = plan.get("window_start_slot")
        length = plan.get("window_length") or 4
        if isinstance(start, int) and isinstance(length, int) and length > 0:
            for slot in range(start, start + length + 2):
                slots.add(slot)
        planned = plan.get("planned_slot")
        if isinstance(planned, int):
            slots.add(planned)
    return sorted(slots)
