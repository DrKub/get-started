"""Serialize cluster snapshots for --fixture-dir."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from measure.errors import ConfigError
from measure.models import ClusterSnapshot


def snapshot_to_dict(snapshot: ClusterSnapshot) -> dict:
    return asdict(snapshot)


def snapshot_from_dict(payload: dict) -> ClusterSnapshot:
    try:
        return ClusterSnapshot(
            epoch=int(payload["epoch"]),
            schedule_start_slot=int(payload["schedule_start_slot"]),
            observed_slot=int(payload["observed_slot"]),
            slot_index=int(payload["slot_index"]),
            slots_in_epoch=int(payload["slots_in_epoch"]),
            blockhash=str(payload["blockhash"]),
            block_time=payload.get("block_time"),
            fetched_at=str(payload["fetched_at"]),
            nodes=list(payload["nodes"]),
            leaders=list(payload["leaders"]),
            source=str(payload.get("source") or "fixture"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"fixture snapshot is invalid: {exc}") from exc


def write_snapshot(directory: Path, snapshot: ClusterSnapshot) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "snapshot.json").write_text(
        json.dumps(snapshot_to_dict(snapshot), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_snapshot(directory: Path) -> ClusterSnapshot:
    path = directory / "snapshot.json"
    if not path.is_file():
        raise ConfigError(f"fixture directory {directory} has no snapshot.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"cannot parse {path}") from exc
    return snapshot_from_dict(payload)
