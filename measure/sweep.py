"""Return rent from non-conflict payers to the funder.

A sweep transaction is a plain transfer of ``balance - 5000`` signed by the
probe account. The fee is 5,000 lamports. What remains is the rent and the
one-lamport self-transfers, and it lands in the funder.
"""

from __future__ import annotations

import logging
from pathlib import Path

from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from measure.constants import SIGNATURE_FEE_LAMPORTS
from measure.errors import RpcError
from measure.keys import load_signers

logger = logging.getLogger("measure")


def sweep_lamports(balance: int, *, fee: int = SIGNATURE_FEE_LAMPORTS) -> int:
    """Lamports to transfer. Zero when the account cannot pay the fee."""
    if balance <= fee:
        return 0
    return balance - fee


def sweep_transaction(child: Keypair, funder: Pubkey, lamports: int, blockhash: str) -> bytes:
    signed = Transaction.new_signed_with_payer(
        [
            transfer(
                TransferParams(
                    from_pubkey=child.pubkey(),
                    to_pubkey=funder,
                    lamports=lamports,
                )
            )
        ],
        child.pubkey(),
        [child],
        Hash.from_string(blockhash),
    )
    return bytes(signed)


def sweep_directory(rpc, funder: Keypair, directory: Path, *, ledger=None, run_id: str = "") -> dict:
    """Close every saved probe account that still holds lamports.

    Returns counts. A failed send leaves the key file in place so a later
    ``measure sweep`` can retry. An empty account's key file is removed.
    """
    returned = 0
    closed = 0
    failed = 0
    for index, child in enumerate(load_signers(directory)):
        pubkey = str(child.pubkey())
        if pubkey == str(funder.pubkey()):
            continue
        try:
            balance = rpc.get_balance(pubkey)
        except RpcError as exc:
            logger.warning("sweep balance %s failed: %s", pubkey, exc)
            failed += 1
            continue
        amount = sweep_lamports(balance)
        path = directory / f"{pubkey}.json"
        if amount == 0:
            if balance == 0 and path.is_file():
                path.unlink()
            continue
        if ledger is not None:
            ledger.reserve(SIGNATURE_FEE_LAMPORTS, run_id=run_id, seq=10_000 + index, reason="sweep")
        try:
            blockhash = rpc.get_latest_blockhash()
            raw = sweep_transaction(child, funder.pubkey(), amount, blockhash)
            rpc.send_transaction(raw)
        except Exception as exc:
            logger.error("sweep %s failed: %s", pubkey, exc)
            if ledger is not None:
                ledger.release(
                    SIGNATURE_FEE_LAMPORTS,
                    run_id=run_id,
                    seq=10_000 + index,
                    reason="sweep_failed",
                )
            failed += 1
            continue
        returned += amount
        closed += 1
        logger.info("swept %s lamports from %s back to the funder", amount, pubkey)
    return {"closed": closed, "failed": failed, "returned_lamports": returned}
