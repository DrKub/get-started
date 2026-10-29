"""Mainnet JSON-RPC. Reads are retried. ``sendTransaction`` is not."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from measure.constants import USER_AGENT
from measure.errors import RpcError

_RETRY_HTTP = {429, 500, 502, 503, 504}
# getSlot / node-behind style codes that are worth one more try.
_RETRY_RPC = {-32004, -32005, -32016}


class RpcClient:
    def __init__(self, url: str, *, timeout_s: float = 60.0, backoff_s: float = 0.4) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.backoff_s = backoff_s

    def call(self, method: str, params: list | None = None, *, attempts: int = 4) -> object:
        body: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        payload = json.dumps(body).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(attempts):
            request = urllib.request.Request(
                self.url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in _RETRY_HTTP and attempt + 1 < attempts:
                    time.sleep(self.backoff_s * (2**attempt))
                    continue
                raise RpcError(f"{method} HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(self.backoff_s * (2**attempt))
                    continue
                raise RpcError(f"{method} failed: {exc}") from exc
            if "error" in decoded:
                error = decoded["error"]
                code = error.get("code") if isinstance(error, dict) else None
                if code in _RETRY_RPC and attempt + 1 < attempts:
                    time.sleep(self.backoff_s * (2**attempt))
                    continue
                raise RpcError(f"{method}: {error}")
            return decoded.get("result")
        raise RpcError(f"{method} failed: {last_error}")

    def get_slot(self) -> int:
        return int(self.call("getSlot", [{"commitment": "processed"}]))

    def get_slot_leader(self, slot: int) -> str:
        return str(self.call("getSlotLeader", [slot, {"commitment": "processed"}]))

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
