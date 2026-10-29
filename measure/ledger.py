"""Global fee ledger at ``data/spend.json``.

A new run directory does not reset this file. Dry-run signatures are not
appended. Live reserves each fee before signing and writes the file
immediately, so a restart continues the same cap.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from measure.errors import SpendCapExceeded


class SpendLedger:
    def __init__(self, path: Path, cap_lamports: int) -> None:
        self.path = path
        self.cap_lamports = cap_lamports
        self.spent_lamports = 0
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.spent_lamports = int(payload.get("spent_lamports") or 0)
        self.entries = list(payload.get("entries") or [])
        stored_cap = payload.get("cap_lamports")
        if isinstance(stored_cap, int) and stored_cap > 0:
            self.cap_lamports = min(self.cap_lamports, stored_cap)

    def remaining(self) -> int:
        return self.cap_lamports - self.spent_lamports

    def ensure_fits(self, fee_lamports: int) -> None:
        if fee_lamports < 0:
            raise ValueError("fee must be non-negative")
        if self.spent_lamports + fee_lamports > self.cap_lamports:
            raise SpendCapExceeded(self.spent_lamports, fee_lamports, self.cap_lamports)

    def reserve(self, fee_lamports: int, *, run_id: str, seq: int, reason: str) -> None:
        """Count a signature that is about to be submitted. Persists before return."""
        self.ensure_fits(fee_lamports)
        self.spent_lamports += fee_lamports
        self._append(fee_lamports, run_id=run_id, seq=seq, reason=reason)

    def release(self, fee_lamports: int, *, run_id: str, seq: int, reason: str) -> None:
        """Give back a reservation when the bytes never left the process."""
        self.spent_lamports = max(0, self.spent_lamports - fee_lamports)
        self._append(-fee_lamports, run_id=run_id, seq=seq, reason=reason)

    def reset(self, *, reason: str = "reset") -> None:
        """Clear a phantom reservation. Requires an explicit operator call."""
        delta = -self.spent_lamports
        self.spent_lamports = 0
        self._append(delta, run_id="", seq=-1, reason=reason)

    def net_for_run(self, run_id: str) -> int:
        return sum(int(entry.get("lamports") or 0) for entry in self.entries if entry.get("run_id") == run_id)

    def reconcile_run(self, run_id: str, landed_lamports: int) -> int:
        """Move this run's net fees to ``landed_lamports``. Other runs stay.

        A global replace would wipe an earlier run's real spend. The delta
        is this run only: unlanded reservations come back, landed fees stay.
        """
        if not run_id:
            raise ValueError("reconcile_run requires a run id")
        if landed_lamports < 0:
            raise ValueError("landed lamports must be non-negative")
        delta = landed_lamports - self.net_for_run(run_id)
        if delta == 0:
            return self.spent_lamports
        self.spent_lamports += delta
        self._append(delta, run_id=run_id, seq=-1, reason="reconcile:landed")
        return self.spent_lamports

    def reconcile(self, attempts: list[dict], *, landed_signatures: set[str] | None = None) -> int:
        """Replace the running total with fees that were submitted, or that landed.

        ``landed_signatures`` is the chain's answer. Without it, only rows
        with ``submitted`` true count. A run that reserved every plan row
        and submitted none reconciles to zero.
        """
        if landed_signatures is None:
            total = sum(
                int(row.get("fee_lamports") or 0)
                for row in attempts
                if row.get("submitted") is True
            )
            basis = "submitted"
        else:
            total = sum(
                int(row.get("fee_lamports") or 0)
                for row in attempts
                if row.get("signature") in landed_signatures
            )
            basis = "landed"
        delta = total - self.spent_lamports
        self.spent_lamports = total
        self._append(delta, run_id="", seq=-1, reason=f"reconcile:{basis}")
        return total

    def _append(self, lamports: int, *, run_id: str, seq: int, reason: str) -> None:
        self.entries.append(
            {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lamports": lamports,
                "reason": reason,
                "run_id": run_id,
                "seq": seq,
            }
        )
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cap_lamports": self.cap_lamports,
            "entries": self.entries,
            "spent_lamports": self.spent_lamports,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


_LANDED_STATUS = {"processed", "confirmed", "finalized"}


def landed_fee_lamports(rows: list[dict], statuses: list) -> int:
    """Fees whose signature status is on chain. Null, error, and failed are zero."""
    total = 0
    for row, status in zip(rows, statuses):
        if not isinstance(status, dict) or status.get("err"):
            continue
        if status.get("confirmationStatus") not in _LANDED_STATUS:
            continue
        total += int(row.get("fee_lamports") or 0)
    return total


def group_attempts(
    rows: list[dict],
    *,
    run_id: str | None = None,
    manifest_id: str | None = None,
) -> dict[str, list[dict]]:
    """Map attempt rows to the ledger run id that reserved their fees."""
    if run_id:
        return {run_id: list(rows)}
    ids = {str(row["experiment_id"]) for row in rows if row.get("experiment_id")}
    if len(ids) > 1:
        groups: dict[str, list[dict]] = {}
        for row in rows:
            key = str(row.get("experiment_id") or "")
            groups.setdefault(key, []).append(row)
        return groups
    if len(ids) == 1:
        return {next(iter(ids)): list(rows)}
    if manifest_id:
        return {manifest_id: list(rows)}
    raise ValueError("attempts have no experiment_id; pass --run-id")
