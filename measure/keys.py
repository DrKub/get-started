"""Probe keypairs saved for sweep. Mode 0600, never committed."""

from __future__ import annotations

import json
import os
from pathlib import Path

from solders.keypair import Keypair


def key_dir(data_dir: Path, experiment_id: str) -> Path:
    return data_dir / "keys" / experiment_id


def write_keypair(directory: Path, keypair: Keypair) -> Path:
    """Write a Solana CLI keypair. The directory is 0700 and the file is 0600."""
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{keypair.pubkey()}.json"
    payload = json.dumps(list(bytes(keypair))).encode("ascii")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path


def write_signers(directory: Path, signers: dict[str, Keypair]) -> list[Path]:
    return [write_keypair(directory, keypair) for keypair in signers.values()]


def load_signers(directory: Path) -> list[Keypair]:
    from measure.txbuild import load_keypair

    if not directory.is_dir():
        return []
    keys = []
    for path in sorted(directory.glob("*.json")):
        keys.append(load_keypair(path))
    return keys
