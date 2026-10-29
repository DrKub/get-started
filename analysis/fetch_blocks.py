"""Read-only backfill of raw ``getBlock`` bodies for the measured slots.

``python -m analysis.fetch_blocks [--data data/runs] [--rpc-url URL]``

For every slot that holds one of our landed probes, this stores the JSON-RPC
response of ``getBlock`` with ``transactionDetails: "accounts"`` under
``<run>/blocks/<slot>.json.gz``. That body carries, per transaction, the
signatures, every account key with its ``writable`` flag, ``meta.fee`` and
``meta.err``, plus the block ``rewards`` whose ``Fee`` recipient is the leader
that produced the block. It is the input for the offline contamination and
leader checks in ``analysis.core``. Nothing is signed or sent.

This is the only part of ``analysis`` that uses the network. Existing files
are kept unless ``--force`` is given.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .core import live_run_dirs, read_jsonl

PROBE_LADDERS = ("conflict", "nonconflict")


def probe_slots(run_dir: Path) -> list[int]:
    rows = read_jsonl(run_dir / "block_observations.jsonl")
    return sorted(
        {
            int(row["slot"])
            for row in rows
            if row.get("landed") is True and row.get("ladder") in PROBE_LADDERS
        }
    )


def _post(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def fetch_block(url: str, slot: int, *, attempts: int = 6, timeout: float = 60) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBlock",
        "params": [
            slot,
            {
                "encoding": "json",
                "maxSupportedTransactionVersion": 1,
                "transactionDetails": "accounts",
                "rewards": True,
                "commitment": "finalized",
            },
        ],
    }
    delay = 2.0
    for attempt in range(attempts):
        try:
            body = _post(url, payload, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if "result" in body and body["result"] is not None:
            return body
        if attempt == attempts - 1:
            raise RuntimeError(f"getBlock {slot}: {body.get('error')}")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"getBlock {slot}: no result")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data/runs"))
    parser.add_argument(
        "--rpc-url",
        default=os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    for run_dir in live_run_dirs(args.data):
        out = run_dir / "blocks"
        out.mkdir(exist_ok=True)
        for slot in probe_slots(run_dir):
            path = out / f"{slot}.json.gz"
            if path.exists() and not args.force:
                continue
            body = fetch_block(args.rpc_url, slot)
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            with path.open("wb") as handle:
                with gzip.GzipFile(
                    filename="", fileobj=handle, mode="wb", mtime=0, compresslevel=9
                ) as gz:
                    gz.write(raw)
            print(f"{run_dir.name}: wrote {path.name} ({path.stat().st_size:,} bytes)")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
