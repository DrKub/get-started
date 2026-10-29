"""Rebuild viz/tests/fixtures/runs from full run directories.

    python -m viz.tests.make_fixtures data/runs/20261029T094113Z data/runs/20261029T100356Z

Keeps every probe record and only the blocks that contain our transactions, with
each transaction reduced to the fields the figures read.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "runs"
COPY = ["attempts.jsonl", "block_observations.jsonl", "hypothesis_agreement.json", "spend.json", "validators.json"]


def trim_block(block: dict) -> dict:
    txs = []
    for tx in block.get("transactions") or []:
        slim = {"block_index": tx["block_index"], "is_ours": bool(tx.get("is_ours"))}
        if slim["is_ours"]:
            slim["signature"] = tx["signature"]
        txs.append(slim)
    keep = {k: block[k] for k in ("slot", "tx_count", "our_tx_count") if k in block}
    keep["transactions"] = txs
    return keep


def build(run_dir: Path) -> Path:
    out = FIXTURE_ROOT / run_dir.name
    out.mkdir(parents=True, exist_ok=True)
    for name in COPY:
        if (run_dir / name).exists():
            shutil.copyfile(run_dir / name, out / name)
    with (run_dir / "block_context.jsonl").open() as src, (out / "block_context.jsonl").open("w") as dst:
        for line in src:
            block = json.loads(line)
            if block.get("our_tx_count"):
                dst.write(json.dumps(trim_block(block), separators=(",", ":")) + "\n")
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(build(Path(arg)))
