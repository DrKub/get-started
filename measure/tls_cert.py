"""Self-signed Ed25519 certificate for the TPU QUIC client.

Agave's TPU requires a client certificate (``client_auth_mandatory``) and
checks the TLS 1.3 CertificateVerify signature against the Ed25519 key in
that certificate. The X.509 signature itself is not verified. An empty
client certificate list is what produced Jito's close
``0x174 peer sent no certificates``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID


def ephemeral_ed25519_cert() -> tuple[x509.Certificate, ed25519.Ed25519PrivateKey]:
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Solana node")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, None)
    )
    return cert, key
