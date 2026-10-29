"""Legacy transaction messages. Signing lives in ``measure.live`` only."""

from __future__ import annotations

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from measure.constants import DRY_RUN_PAYER, PACKET_DATA_SIZE
from measure.models import PlannedTx


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


def build_instructions(tx: PlannedTx, payer: Pubkey) -> list:
    recipient = Pubkey.from_string(tx.recipient)
    return [
        set_compute_unit_limit(tx.cu_limit),
        set_compute_unit_price(tx.cu_price_micro_lamports),
        transfer(
            TransferParams(
                from_pubkey=payer,
                to_pubkey=recipient,
                lamports=tx.transfer_lamports,
            )
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
    blockhash: str,
    payer_source: str,
) -> None:
    """Record the message that would be signed. Does not mark the tx submitted."""
    for tx in txs:
        tx.payer_source = payer_source
        tx.recent_blockhash = blockhash
        tx.unsigned_message_base64 = ""
        tx.signed = False
        tx.sent = False
        tx.submitted = False


def sign_transaction(tx: PlannedTx, keypair: Keypair, blockhash: str) -> tuple[bytes, str]:
    """Sign one planned transaction. Live path only."""
    payer = keypair.pubkey()
    if str(payer) != tx.payer:
        raise RuntimeError("signer does not match the planned fee payer")
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
