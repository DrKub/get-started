"""Direct QUIC submit to a leader TPU.

Agave accepts client-opened unidirectional streams on ALPN ``solana-tpu``.
Each stream body is the raw signed transaction bytes. The server certificate
is self-signed, so verification is skipped. The client must still present
its own Ed25519 certificate. UDP ``tpu`` is not used.

A peer close with a non-zero error is a failed send, not a success.
"""

from __future__ import annotations

import asyncio
import ssl

from aioquic.asyncio.client import connect
from aioquic.quic.configuration import QuicConfiguration

from measure.errors import RpcError
from measure.tls_cert import ephemeral_ed25519_cert

ALPN_TPU = "solana-tpu"


class TpuSendError(RpcError):
    def __init__(self, message: str, *, transmitted: bool) -> None:
        super().__init__(message)
        self.transmitted = transmitted


def quic_configuration() -> QuicConfiguration:
    certificate, private_key = ephemeral_ed25519_cert()
    configuration = QuicConfiguration(is_client=True, alpn_protocols=[ALPN_TPU])
    configuration.verify_mode = ssl.CERT_NONE
    configuration.idle_timeout = 10.0
    configuration.certificate = certificate
    configuration.private_key = private_key
    configuration.certificate_chain = []
    return configuration


def peer_close_error(protocol) -> str | None:
    event = getattr(protocol._quic, "_close_event", None)
    if event is None or event.error_code in (0, None):
        return None
    reason = event.reason_phrase
    if isinstance(reason, bytes):
        reason = reason.decode("utf-8", "replace")
    return f"quic close 0x{int(event.error_code):x}: {reason}"


def parse_host_port(address: str) -> tuple[str, int]:
    if address.startswith("["):
        host, _, port = address[1:].partition("]:")
        if not host or not port:
            raise ValueError(f"bad TPU address {address}")
        return host, int(port)
    host, sep, port = address.rpartition(":")
    if not sep or not host or not port:
        raise ValueError(f"bad TPU address {address}")
    return host, int(port)


async def _send(host: str, port: int, payloads: list[bytes]) -> None:
    configuration = quic_configuration()
    transmitted = False
    async with connect(
        host,
        port,
        configuration=configuration,
        wait_connected=True,
    ) as protocol:
        await asyncio.sleep(0.05)
        early = peer_close_error(protocol)
        if early:
            raise TpuSendError(early, transmitted=False)
        for payload in payloads:
            _reader, writer = await asyncio.wait_for(
                protocol.create_stream(is_unidirectional=True),
                timeout=5,
            )
            writer.write(payload)
            writer.write_eof()
            await writer.drain()
            transmitted = True
        protocol.transmit()
        await asyncio.sleep(0.3)
        late = peer_close_error(protocol)
        if late:
            raise TpuSendError(late, transmitted=transmitted)
        protocol.close()


class TpuSender:
    def send_payloads(self, payloads: list[bytes], *, destination: str) -> None:
        if not payloads:
            return
        host, port = parse_host_port(destination)
        try:
            asyncio.run(asyncio.wait_for(_send(host, port, payloads), timeout=15))
        except TpuSendError:
            raise
        except Exception as exc:
            raise TpuSendError(
                f"TPU send to {destination} failed: {exc}",
                transmitted=False,
            ) from exc


class RpcSender:
    def __init__(self, rpc) -> None:
        self.rpc = rpc

    def send_payloads(self, payloads: list[bytes], *, destination: str) -> None:
        del destination
        for payload in payloads:
            self.rpc.send_transaction(payload)
