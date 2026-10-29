"""Deterministic offline cluster used by tests and --fixture-dir.

Pubkeys below are not keys. They were generated once and the secrets were
discarded. Do not send SOL to them.
"""

from __future__ import annotations

from measure.models import ClusterSnapshot

# Discarded-secret identities. Order is the family round-robin order.
IDENTITIES = {
    "firedancer": "8M5QUYts4PY375tKCoVDQexdzsS8Z4AdQbh6KKfNmvvi",
    "frankendancer": "FBPJPh9LLB7iM6HQ2szRRVGYy7LhL9k9yE2NfELeZCix",
    "harmonic-firedancer": "BeCfdcGdhagF1rhaJU3LqrraEEL2fyAC7rwXpRbh8zg3",
    "jito-solana": "5xfaRZhMoPpucP7jGhkhGGrPqrzGx9erv4ptJoLoSwzP",
    "agave": "FtzXCSAmp3opsN5Kx33ituWMZYRfaJMiPjhqmmgMcLAW",
    "harmonic-agave": "FF6JPF2j3QcW74nuS2jY72ekGDu92Fu4KS1hz7WXUFsU",
    "agave-bam": "5xiGtEHBS91B7HaKn65LDGTrC3UT2qWSxpfMPbYTrZTk",
    "no-tpu": "4AxF1acjRp5HFYcJmJWXxpwZ4LKieJzE3aVFyMnCR87n",
    "unknown": "CR6zTYJjfnetocFKNuujt1b2h1hcVuTJ7XCL75dPiYaW",
    "missing-client": "6UcH8HRhe6aj8du6iTWt5ucytPJN8GMpMhoprzVSobNy",
    "filler": "BR9xPHGbvx9B2e3cipXuVGep7Mg5sjfAqBD6yJnakhTS",
    "jito-duplicate": "3ZGJK6grfVYPbuBeUr1wQR3vBfiBii6RfTu5VApwkECX",
}

_CLIENTS = {
    "firedancer": ("Firedancer", "26.10.4"),
    "frankendancer": ("Frankendancer", "0.101.40300"),
    "harmonic-firedancer": ("HarmonicFiredancer", "26.10.4"),
    "jito-solana": ("JitoLabs", "4.3.2"),
    "agave": ("Agave", "4.3.2"),
    "harmonic-agave": ("HarmonicAgave", "4.3.2"),
    "agave-bam": ("AgaveBam", "4.3.2"),
    "jito-duplicate": ("JitoLabs", "4.3.2"),
}


def _node(pubkey: str, client_id: str | None, version: str | None, *, tpu: bool, octet: int) -> dict:
    address = f"203.0.113.{octet}"
    return {
        "clientId": client_id,
        "featureSet": 1000 + octet,
        "gossip": f"{address}:8001",
        "pubkey": pubkey,
        "tpu": None,
        "tpuQuic": f"{address}:8003" if tpu else None,
        "version": version,
    }


def build_offline_snapshot() -> ClusterSnapshot:
    """Six selectable families with three full windows each, plus decoys.

    A 20-slot filler prefix sits inside the default 16-slot lead time so the
    first real window is eligible. Decoys cover the version-string collision,
    a missing clientId, an unknown clientId, and a leader with no TPU address.
    """
    nodes = []
    octet = 10
    for family, (client_id, version) in _CLIENTS.items():
        nodes.append(_node(IDENTITIES[family], client_id, version, tpu=True, octet=octet))
        octet += 1
    nodes.append(_node(IDENTITIES["no-tpu"], "Firedancer", "26.10.5", tpu=False, octet=octet))
    octet += 1
    nodes.append(_node(IDENTITIES["unknown"], "Unknown(42)", "4.3.2", tpu=True, octet=octet))
    octet += 1
    nodes.append(_node(IDENTITIES["missing-client"], None, "4.3.2", tpu=True, octet=octet))

    leaders: list[str] = [IDENTITIES["filler"]] * 20
    # Priority order, three rotations each. agave-bam is present but is the
    # 7th family, so a 6-validator selection stops before it.
    for family in (
        "firedancer",
        "frankendancer",
        "harmonic-firedancer",
        "jito-solana",
        "agave",
        "harmonic-agave",
        "agave-bam",
        "no-tpu",
        "unknown",
        "missing-client",
        "jito-duplicate",
    ):
        leaders.extend([IDENTITIES[family]] * 12)
    return ClusterSnapshot(
        epoch=1071,
        schedule_start_slot=1_000_000,
        observed_slot=1_000_000,
        slot_index=1_000,
        slots_in_epoch=432_000,
        blockhash="11111111111111111111111111111111",
        block_time=1_761_800_000,
        fetched_at="2026-10-29T08:00:00Z",
        nodes=nodes,
        leaders=leaders,
        source="fixture",
    )
