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
        """Count a signature that is about to be produced. Persists before return."""
        self.ensure_fits(fee_lamports)
        self.spent_lamports += fee_lamports
        self.entries.append(
            {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lamports": fee_lamports,
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
