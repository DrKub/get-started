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
    if message.get("method") == "slotNotification":
        result = (message.get("params") or {}).get("result") or {}
        slot = result.get("slot") if isinstance(result, dict) else None
        return slot if isinstance(slot, int) else None
    result = message.get("result")
    if isinstance(result, int):
        return result
    if isinstance(result, dict) and isinstance(result.get("slot"), int):
        return result["slot"]
    return None


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

    def __init__(self, rpc, rpc_url: str) -> None:
        self.rpc = rpc
        self.rpc_url = rpc_url
        self.slot = 0
        self._ws: _WsClient | None = None
        self.source = "http"

    def start(self) -> None:
        try:
            ws = _WsClient(http_to_ws(self.rpc_url), timeout=5)
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
                if slot is not None and slot >= self.slot:
                    self.slot = slot
                    return slot
        slot = int(self.rpc.get_slot(timeout_s=timeout_s))
        if slot > self.slot:
            self.slot = slot
        return self.slot or slot

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
