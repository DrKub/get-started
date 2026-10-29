"""Direct QUIC submit to a leader TPU.

Agave accepts client-opened unidirectional streams on ALPN ``solana-tpu``.
Each stream body is the raw signed transaction bytes. The server certificate
is self-signed, so verification is skipped. UDP ``tpu`` is not used.

Unstaked connections are throttled. This sender does not pretend otherwise;
the RPC arm is the comparison when QUIC drops the burst.
"""

from __future__ import annotations

import asyncio
import ssl

from aioquic.asyncio.client import connect
from aioquic.quic.configuration import QuicConfiguration

from measure.errors import RpcError

ALPN_TPU = "solana-tpu"


def quic_configuration() -> QuicConfiguration:
    configuration = QuicConfiguration(is_client=True, alpn_protocols=[ALPN_TPU])
    configuration.verify_mode = ssl.CERT_NONE
    configuration.idle_timeout = 10.0
    return configuration


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
    async with connect(
        host,
        port,
        configuration=configuration,
        wait_connected=True,
    ) as protocol:
        # The server grants unstaked stream credit after the handshake.
        await asyncio.sleep(0.05)
        for payload in payloads:
            _reader, writer = await asyncio.wait_for(
                protocol.create_stream(is_unidirectional=True),
                timeout=5,
            )
            writer.write(payload)
            writer.write_eof()
            await writer.drain()
        await asyncio.sleep(0.2)
        protocol.close()


class TpuSender:
    def send_payloads(self, payloads: list[bytes], *, destination: str) -> None:
        if not payloads:
            return
        host, port = parse_host_port(destination)
        try:
            asyncio.run(asyncio.wait_for(_send(host, port, payloads), timeout=20))
        except Exception as exc:
            raise RpcError(f"TPU send to {destination} failed: {exc}") from exc


class RpcSender:
    def __init__(self, rpc) -> None:
        self.rpc = rpc

    def send_payloads(self, payloads: list[bytes], *, destination: str) -> None:
        del destination
        for payload in payloads:
            self.rpc.send_transaction(payload)
