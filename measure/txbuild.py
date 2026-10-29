"""Legacy transaction messages. Signing lives in ``measure.live`` only."""

from __future__ import annotations

import base64

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from measure.constants import DRY_RUN_PAYER, MEMO_PROGRAM_ID, PACKET_DATA_SIZE
from measure.memo import render_memo
from measure.models import PlannedTx

_MEMO_PROGRAM = Pubkey.from_string(MEMO_PROGRAM_ID)


def dry_run_payer() -> Pubkey:
    return Pubkey.from_string(DRY_RUN_PAYER)


def load_keypair(path) -> Keypair:
    """Load a Solana CLI keypair file (JSON array of 32 or 64 bytes)."""
    import json
    from pathlib import Path

    from measure.errors import ConfigError

    file_path = Path(path)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read keypair at {file_path}") from exc
    if not isinstance(data, list) or len(data) not in (32, 64):
        raise ConfigError("KEYPAIR_PATH must be a Solana CLI JSON keypair (32 or 64 bytes)")
    try:
        raw = bytes(int(item) & 0xFF for item in data)
    except (TypeError, ValueError) as exc:
        raise ConfigError("keypair JSON must be an array of bytes") from exc
    if len(raw) == 32:
        return Keypair.from_seed(raw)
    return Keypair.from_bytes(raw)


def memo_text(tx: PlannedTx) -> str:
    return render_memo(
        experiment_id=tx.experiment_id,
        seq=tx.seq,
        arm=tx.arm,
        send_index=tx.send_index,
        path=tx.path,
        priority_fee_lamports=tx.priority_fee_lamports,
        cu_price_micro_lamports=tx.cu_price_micro_lamports,
        cu_limit=tx.cu_limit,
    )


def build_instructions(tx: PlannedTx, payer: Pubkey) -> list[Instruction]:
    memo = memo_text(tx).encode("ascii")
    return [
        set_compute_unit_limit(tx.cu_limit),
        set_compute_unit_price(tx.cu_price_micro_lamports),
        Instruction(
            _MEMO_PROGRAM,
            memo,
            [AccountMeta(payer, True, True)],
        ),
    ]


def message_bytes(tx: PlannedTx, payer: Pubkey, blockhash: str) -> bytes:
    message = Message.new_with_blockhash(
        build_instructions(tx, payer),
        payer,
        Hash.from_string(blockhash),
    )
    return bytes(message)


def attach_unsigned_messages(
    txs: list[PlannedTx],
    *,
    payer: Pubkey,
    blockhash: str,
    payer_source: str,
) -> None:
    """Fill memo and unsigned message bytes. Does not sign."""
    payer_text = str(payer)
    for tx in txs:
        tx.payer = payer_text
        tx.payer_source = payer_source
        tx.recent_blockhash = blockhash
        tx.memo = memo_text(tx)
        tx.unsigned_message_base64 = base64.b64encode(
            message_bytes(tx, payer, blockhash)
        ).decode("ascii")
        tx.signed = False
        tx.sent = False


def sign_transaction(tx: PlannedTx, keypair: Keypair, blockhash: str) -> tuple[bytes, str]:
    """Sign one planned transaction. Live path only."""
    payer = keypair.pubkey()
    raw_tx = Transaction.new_signed_with_payer(
        build_instructions(tx, payer),
        payer,
        [keypair],
        Hash.from_string(blockhash),
    )
    raw = bytes(raw_tx)
    if not raw_tx.is_signed():
        raise RuntimeError("transaction did not sign")
    if len(raw) > PACKET_DATA_SIZE:
        raise RuntimeError(
            f"signed transaction is {len(raw)} bytes, over the {PACKET_DATA_SIZE}-byte "
            "TPU stream window"
        )
    return raw, str(raw_tx.signatures[0])
