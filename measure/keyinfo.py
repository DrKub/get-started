"""Read a keypair's public key and balance. Does not sign or send."""

from __future__ import annotations

from pathlib import Path

from measure.rpc import RpcClient
from measure.txbuild import load_keypair


def describe_keypair(path: Path, rpc_url: str, *, timeout_s: float = 30.0) -> dict:
    keypair = load_keypair(path)
    pubkey = str(keypair.pubkey())
    # The secret stays in this process only long enough to derive the pubkey.
    del keypair
    result = RpcClient(rpc_url, timeout_s=timeout_s).call(
        "getBalance",
        [pubkey, {"commitment": "confirmed"}],
    )
    lamports = int(result["value"]) if isinstance(result, dict) else int(result)
    return {"lamports": lamports, "pubkey": pubkey, "sol": f"{lamports / 1_000_000_000:.9f}"}
