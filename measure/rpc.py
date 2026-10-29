"""Mainnet JSON-RPC. Reads are retried. ``sendTransaction`` is not."""

from __future__ import annotations

import http.client
import json
import random
import time
from urllib.parse import urlsplit

from measure.constants import USER_AGENT
from measure.errors import RpcError

_RETRY_HTTP = {429, 500, 502, 503, 504}


def retry_delay(attempt: int, *, jitter: float | None = None) -> float:
    """Exponential backoff capped at 8s, plus jitter so parallel readers don't line up."""
    base = min(8.0, 0.25 * (2 ** min(attempt, 5)))
    extra = random.uniform(0, 0.4) if jitter is None else jitter
    return base + extra
# getSlot / node-behind style codes that are worth one more try.
_RETRY_RPC = {-32004, -32005, -32016}


class RpcClient:
    """JSON-RPC over one keep-alive HTTPS connection.

    ``getSlot`` used to open a fresh TLS session every poll (~0.9s) and then
    sleep 0.4s, so one sample spanned several 215 ms slots. The socket
    timeout is enforced on every call so a stuck read cannot run for minutes.
    """

    def __init__(self, url: str, *, timeout_s: float = 20.0, backoff_s: float = 0.2, read_gap_s: float = 0.25) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.backoff_s = backoff_s
        self.read_gap_s = read_gap_s
        self._next_read = 0.0
        self._conn: http.client.HTTPConnection | None = None
        parts = urlsplit(url)
        self._scheme = parts.scheme or "https"
        self._host = parts.hostname or ""
        self._port = parts.port or (443 if self._scheme == "https" else 80)
        self._path = parts.path or "/"
        if parts.query:
            self._path += "?" + parts.query

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _connection(self, timeout_s: float) -> http.client.HTTPConnection:
        if self._conn is None:
            if self._scheme == "https":
                self._conn = http.client.HTTPSConnection(self._host, self._port, timeout=timeout_s)
            else:
                self._conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout_s)
        self._conn.timeout = timeout_s
        return self._conn

    def call(
        self,
        method: str,
        params: list | None = None,
        *,
        attempts: int = 4,
        timeout_s: float | None = None,
    ) -> object:
        body: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        payload = json.dumps(body).encode("utf-8")
        timeout = self.timeout_s if timeout_s is None else timeout_s
        last_error: Exception | None = None
        pace = method != "sendTransaction"
        for attempt in range(attempts):
            if pace:
                self._pace_read()
            conn = self._connection(timeout)
            try:
                conn.request(
                    "POST",
                    self._path,
                    body=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                        "Connection": "keep-alive",
                    },
                )
                response = conn.getresponse()
                raw = response.read()
                if response.status in _RETRY_HTTP:
                    last_error = RpcError(f"{method} HTTP {response.status}")
                    self.close()
                    if attempt + 1 < attempts:
                        time.sleep(retry_delay(attempt))
                        continue
                    raise last_error
                if response.status >= 400:
                    self.close()
                    raise RpcError(f"{method} HTTP {response.status}")
                decoded = json.loads(raw.decode("utf-8"))
            except RpcError:
                raise
            except (TimeoutError, json.JSONDecodeError, OSError, http.client.HTTPException) as exc:
                last_error = exc
                self.close()
                if attempt + 1 < attempts:
                    time.sleep(self.backoff_s * (2**attempt))
                    continue
                raise RpcError(f"{method} failed: {exc}") from exc
            if "error" in decoded:
                error = decoded["error"]
                code = error.get("code") if isinstance(error, dict) else None
                if code in _RETRY_RPC and attempt + 1 < attempts:
                    time.sleep(retry_delay(attempt))
                    continue
                raise RpcError(f"{method}: {error}")
            if pace:
                self._mark_read()
            return decoded.get("result")
        raise RpcError(f"{method} failed: {last_error}")

    def _pace_read(self) -> None:
        wait = self._next_read - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _mark_read(self) -> None:
        self._next_read = time.monotonic() + self.read_gap_s

    def get_slot(self, *, timeout_s: float = 2.0) -> int:
        return int(self.call("getSlot", [{"commitment": "processed"}], attempts=6, timeout_s=timeout_s))

    def get_signature_statuses(
        self,
        signatures: list[str],
        *,
        search_history: bool = False,
    ) -> list[dict | None]:
        result = self.call(
            "getSignatureStatuses",
            [signatures, {"searchTransactionHistory": search_history}],
            attempts=8,
            timeout_s=8,
        )
        if not isinstance(result, dict):
            raise RpcError("getSignatureStatuses returned no value")
        value = result.get("value")
        if not isinstance(value, list):
            raise RpcError("getSignatureStatuses missing value")
        return [item if isinstance(item, dict) else None for item in value]

    def get_slot_leader(self, slot: int | None = None) -> str:
        # Public RPCs differ: some take no slot, some reject a commitment
        # object. getSlotLeaders(slot, 1) is the form that stays stable.
        if slot is None:
            try:
                return str(self.call("getSlotLeader", [{"commitment": "processed"}], attempts=1, timeout_s=5))
            except RpcError:
                slot = self.get_slot()
        leaders = self.call("getSlotLeaders", [slot, 1], attempts=2, timeout_s=5)
        if not isinstance(leaders, list) or not leaders:
            raise RpcError(f"no leader for slot {slot}")
        return str(leaders[0])

    def get_latest_blockhash(self) -> str:
        result = self.call("getLatestBlockhash", [{"commitment": "confirmed"}])
        if not isinstance(result, dict):
            raise RpcError("getLatestBlockhash returned no value")
        value = result.get("value") or {}
        blockhash = value.get("blockhash")
        if not isinstance(blockhash, str):
            raise RpcError("getLatestBlockhash missing blockhash")
        return blockhash

    def get_slot_leaders_range(self, start: int, total: int, *, page: int = 4000) -> list[str]:
        leaders: list[str] = []
        while len(leaders) < total:
            count = min(page, total - len(leaders))
            chunk = self.call("getSlotLeaders", [start + len(leaders), count])
            if not isinstance(chunk, list) or len(chunk) != count:
                got = len(chunk) if isinstance(chunk, list) else type(chunk).__name__
                raise RpcError(f"getSlotLeaders returned {got}, expected {count}")
            leaders.extend(str(item) for item in chunk)
            if len(leaders) < total:
                time.sleep(0.15)
        return leaders

    def send_transaction(self, raw: bytes) -> str:
        import base64

        encoded = base64.b64encode(raw).decode("ascii")
        # One attempt. A retry can land the same signature in a later leader
        # window and erase the path we meant to measure. skipPreflight keeps
        # the send on the slot clock; maxRetries 0 stops the RPC from doing
        # its own delayed resubmit.
        result = self.call(
            "sendTransaction",
            [
                encoded,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 0,
                    "preflightCommitment": "processed",
                },
            ],
            attempts=1,
        )
        if not isinstance(result, str):
            raise RpcError(f"sendTransaction returned {result!r}")
        return result

    def get_block(self, slot: int) -> dict | None:
        result = self.call(
            "getBlock",
            [
                slot,
                {
                    "commitment": "confirmed",
                    "encoding": "jsonParsed",
                    "transactionDetails": "full",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 1,
                },
            ],
        )
        if result is None:
            return None
        if not isinstance(result, dict):
            raise RpcError(f"getBlock {slot} returned {type(result).__name__}")
        return result
