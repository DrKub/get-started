"""When to send a group, and a slot clock that does not open a new TLS
connection for every sample.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit


def send_decision(
    slot_now: int,
    *,
    target_slot: int,
    window_start: int,
    window_length: int,
    burst_offset: int,
) -> str:
    """Return ``wait``, ``send``, or ``skip``.

    A group is still sendable for the whole leader window, not only for
    ``target_slot + 1``. One slow poll used to jump past that one-slot
    slack and skip a window the leader was still producing.
    """
    if window_length < 1:
        window_length = 1
    window_end = window_start + window_length
    if slot_now >= window_end:
        return "skip"
    if slot_now + burst_offset >= target_slot:
        return "send"
    return "wait"


def http_to_ws(url: str) -> str:
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url


def slot_from_message(message: dict) -> int | None:
    """Read a slot only from ``slotNotification``.

    The subscribe acknowledgement is ``{"result": <subscription id>}``.
    Treating that integer as a slot asked for the leader of epoch 0.
    """
    if message.get("method") != "slotNotification":
        return None
    result = (message.get("params") or {}).get("result") or {}
    if not isinstance(result, dict):
        return None
    slot = result.get("slot")
    if isinstance(slot, int) and slot > 0:
        return slot
    return None


def burst_lead_slots(rtt_s: float, *, slot_s: float = 0.215) -> int:
    """How many slots before the window to fire so the burst arrives inside it."""
    if slot_s <= 0:
        slot_s = 0.215
    if rtt_s <= 0:
        return 1
    return max(1, int((rtt_s / slot_s) + 0.999))


def block_fetch_action(message: str) -> str:
    """How collect should treat a getBlock error: retry, wait, skip, or fail.

    Skipped slots are checked before the digits ``429``, so a slot number
    cannot be mistaken for HTTP 429.
    """
    text = message.lower()
    if "skipped" in text or "-32007" in text or "-32009" in text:
        return "skip"
    if (
        "http 429" in text
        or "too many" in text
        or "rate limit" in text
        or "-32005" in text
    ):
        return "retry"
    if "not available" in text or "not yet" in text or "-32004" in text:
        return "wait"
    return "fail"


def is_cluster_slot(slot: int) -> bool:
    """False for a subscribe ack or any other small integer."""
    return slot >= 1_000_000


def fanout_identities(schedule: list[str], limit: int = 3) -> list[str]:
    """Distinct leaders after the current one.

    The leader of ``schedule[0]`` is about to rotate out, so funding copies
    go to the following identities only.
    """
    if len(schedule) < 2 or limit < 1:
        return []
    current = schedule[0]
    return unique_leaders([item for item in schedule[1:] if item != current], limit)


def unique_leaders(leaders: list[str], limit: int) -> list[str]:
    chosen: list[str] = []
    for identity in leaders:
        if identity in chosen:
            continue
        chosen.append(identity)
        if len(chosen) >= limit:
            break
    return chosen


class _WsClient:
    def __init__(self, url: str, *, timeout: float) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss"):
            raise ValueError(f"not a websocket url: {url}")
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        if parts.scheme == "wss":
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=host)
        raw.settimeout(timeout)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        raw.sendall(request.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = raw.recv(1)
            if not chunk:
                raise ConnectionError("websocket handshake closed")
            header += chunk
            if len(header) > 8192:
                raise ConnectionError("websocket handshake too long")
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket upgrade failed: {header[:120]!r}")
        self._sock = raw

    def send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        mask = os.urandom(4)
        header = bytes([0x81, 0x80 | len(body)])
        if len(body) > 125:
            raise ValueError("frame too large")
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(body))
        self._sock.sendall(header + mask + masked)

    def read_json(self, timeout: float) -> dict:
        self._sock.settimeout(timeout)
        first = self._recv(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv(8))[0]
        if length > 1_000_000:
            raise ConnectionError("websocket frame too large")
        payload = self._recv(length) if length else b""
        if opcode == 0x8:
            raise ConnectionError("websocket closed")
        if opcode == 0x9:
            # ping; caller can read again
            return {}
        if opcode != 0x1:
            return {}
        return json.loads(payload.decode("utf-8"))

    def _recv(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise ConnectionError("websocket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class SlotFeed:
    """Prefer ``slotSubscribe``. Fall back to a keep-alive ``getSlot``."""

    def __init__(self, rpc, rpc_url: str, ws_url: str | None = None) -> None:
        self.rpc = rpc
        self.rpc_url = rpc_url
        self._ws_url = ws_url or http_to_ws(rpc_url)
        self.slot = 0
        self._ws: _WsClient | None = None
        self.source = "http"

    def start(self) -> None:
        try:
            ws = _WsClient(self._ws_url, timeout=5)
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"})
            self._ws = ws
            self.source = "slotSubscribe"
        except Exception:
            self._ws = None
            self.source = "http"

    def current(self, *, timeout_s: float = 2.0) -> int:
        if self._ws is not None:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                try:
                    message = self._ws.read_json(timeout=max(0.2, deadline - time.monotonic()))
                except TimeoutError:
                    break
                except Exception:
                    self._ws.close()
                    self._ws = None
                    self.source = "http"
                    break
                slot = slot_from_message(message)
                # A subscribe ack is a small integer. Using it as a slot
                # asks getSlotLeaders for epoch 0.
                if slot is not None and is_cluster_slot(slot) and slot >= self.slot:
                    self.slot = slot
                    return slot
        return self._http_slot(timeout_s)

    def _http_slot(self, timeout_s: float) -> int:
        from measure.errors import RpcError
        from measure.rpc import retry_delay

        deadline = time.monotonic() + max(timeout_s, 20.0)
        attempt = 0
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                slot = int(self.rpc.get_slot(timeout_s=min(5.0, max(timeout_s, 2.0))))
            except RpcError as exc:
                last = exc
                text = str(exc).lower()
                retryable = "429" in text or "-32005" in text or "too many" in text
                if not retryable:
                    if self.slot:
                        return self.slot
                    raise
                time.sleep(retry_delay(attempt))
                attempt += 1
                continue
            if not is_cluster_slot(slot):
                last = RpcError(f"ignoring non-cluster slot {slot}")
                time.sleep(retry_delay(attempt))
                attempt += 1
                continue
            if slot > self.slot:
                self.slot = slot
            return slot
        if self.slot:
            return self.slot
        raise RpcError(f"getSlot failed: {last}")

    def wait_until(self, target: int, *, timeout_s: float) -> int:
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for slot {target}")
            slot = self.current(timeout_s=min(2.0, remaining))
            if slot >= target:
                return slot

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
