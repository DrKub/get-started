"""Read measurement run directories into plain records for plotting."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_RUN_LABELS = {
    "20261029T094113Z": "run 3",
    "20261029T100356Z": "run 4",
}

CLIENT_ORDER = [
    "Agave",
    "JitoLabs",
    "AgaveBam",
    "HarmonicAgave",
    "Firedancer",
    "HarmonicFiredancer",
]

LADDER_ORDER = ["conflict", "nonconflict"]


@dataclass
class Probe:
    run: str
    ladder: str
    identity: str
    client_id: str
    signature: str
    slot: int
    target_slot: int | None
    block_index: int
    priority_fee_lamports: int
    cu_price_micro_lamports: int
    send_index: int
    send_unix_ms: int | None
    copy_send_unix_ms: list[int]

    @property
    def first_send_ms(self) -> int | None:
        if self.copy_send_unix_ms:
            return min(self.copy_send_unix_ms)
        return self.send_unix_ms


@dataclass
class Block:
    slot: int
    tx_count: int
    our_tx_count: int
    other_indexes: list[int]
    ours: dict[str, int]


@dataclass
class Run:
    name: str
    label: str
    path: Path
    probes: list[Probe]
    blocks: dict[int, Block]
    ladders: list[dict]
    spend: dict | None
    validators: dict[str, dict] = field(default_factory=dict)

    def client_of(self, identity: str) -> str:
        return (self.validators.get(identity) or {}).get("client_id") or "unknown"

    def version_of(self, identity: str) -> str | None:
        return (self.validators.get(identity) or {}).get("version")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _read_json(path: Path):
    if not path.exists():
        return None
    with path.open() as fh:
        return json.load(fh)


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _read_observations(run_dir: Path) -> list[dict]:
    jsonl = run_dir / "block_observations.jsonl"
    if jsonl.exists():
        return _read_jsonl(jsonl)
    rows = []
    with (run_dir / "block_observations.csv").open(newline="") as fh:
        for row in csv.DictReader(fh):
            row["landed"] = row.get("landed", "").lower() == "true"
            for key in ("slot", "block_index", "priority_fee_lamports", "cu_price_micro_lamports", "send_index"):
                row[key] = _int_or_none(row.get(key))
            rows.append(row)
    return rows


def is_run_dir(path: Path) -> bool:
    return path.is_dir() and (
        (path / "block_observations.jsonl").exists() or (path / "block_observations.csv").exists()
    )


def discover_runs(data_dir: Path) -> list[Path]:
    data_dir = Path(data_dir)
    if is_run_dir(data_dir):
        return [data_dir]
    if not data_dir.is_dir():
        return []
    return sorted(p for p in data_dir.iterdir() if is_run_dir(p))


def load_run(run_dir: Path, label: str | None = None) -> Run:
    run_dir = Path(run_dir)
    name = run_dir.name
    attempts = {}
    attempts_path = run_dir / "attempts.jsonl"
    if attempts_path.exists():
        attempts = {a["signature"]: a for a in _read_jsonl(attempts_path) if a.get("signature")}

    validators = {}
    for v in _read_json(run_dir / "validators.json") or []:
        validators[v["identity"]] = v
    manifest = _read_json(run_dir / "manifest.json") or {}
    for v in manifest.get("targets", []):
        validators.setdefault(v["identity"], v)

    probes = []
    for obs in _read_observations(run_dir):
        if obs.get("role") != "probe" or not obs.get("landed") or obs.get("block_index") is None:
            continue
        att = attempts.get(obs["signature"], {})
        identity = obs.get("identity") or att.get("leader_identity") or att.get("identity")
        client = obs.get("client_id") or (validators.get(identity) or {}).get("client_id") or "unknown"
        send_index = obs.get("send_index")
        if send_index is None:
            send_index = att.get("send_index")
        probes.append(
            Probe(
                run=name,
                ladder=obs["ladder"],
                identity=identity,
                client_id=client,
                signature=obs["signature"],
                slot=int(obs["slot"]),
                target_slot=_int_or_none(att.get("target_slot")),
                block_index=int(obs["block_index"]),
                priority_fee_lamports=int(obs.get("priority_fee_lamports") or 0),
                cu_price_micro_lamports=int(obs.get("cu_price_micro_lamports") or 0),
                send_index=int(send_index) if send_index is not None else 0,
                send_unix_ms=_int_or_none(att.get("send_unix_ms")),
                copy_send_unix_ms=[int(x) for x in att.get("copy_send_unix_ms") or []],
            )
        )

    blocks = {}
    context_path = run_dir / "block_context.jsonl"
    if context_path.exists():
        for blk in _read_jsonl(context_path):
            txs = blk.get("transactions") or []
            ours = {t["signature"]: int(t["block_index"]) for t in txs if t.get("is_ours")}
            others = [int(t["block_index"]) for t in txs if not t.get("is_ours")]
            blocks[int(blk["slot"])] = Block(
                slot=int(blk["slot"]),
                tx_count=int(blk.get("tx_count") or len(txs)),
                our_tx_count=int(blk.get("our_tx_count") or len(ours)),
                other_indexes=sorted(others),
                ours=ours,
            )

    agreement = _read_json(run_dir / "hypothesis_agreement.json") or {}
    return Run(
        name=name,
        label=label or KNOWN_RUN_LABELS.get(name, name),
        path=run_dir,
        probes=probes,
        blocks=blocks,
        ladders=agreement.get("ladders", []),
        spend=_read_json(run_dir / "spend.json"),
        validators=validators,
    )


def load_runs(data_dir: Path, labels: dict[str, str] | None = None) -> list[Run]:
    labels = labels or {}
    return [load_run(p, labels.get(p.name)) for p in discover_runs(data_dir)]


def kendall_tau_b(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = (xs[i] > xs[j]) - (xs[i] < xs[j])
            dy = (ys[i] > ys[j]) - (ys[i] < ys[j])
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx == dy:
                concordant += 1
            else:
                discordant += 1
    denom = ((concordant + discordant + ties_x) * (concordant + discordant + ties_y)) ** 0.5
    if denom == 0:
        return None
    return (concordant - discordant) / denom


def client_sort_key(client: str) -> tuple[int, str]:
    if client in CLIENT_ORDER:
        return (CLIENT_ORDER.index(client), client)
    return (len(CLIENT_ORDER), client)


def validators_in(runs: list[Run]) -> list[tuple[str, str]]:
    """(identity, client_id) pairs across all runs in a stable client order."""
    seen: dict[str, str] = {}
    for run in runs:
        for p in run.probes:
            if p.identity and p.identity not in seen:
                seen[p.identity] = p.client_id
    return sorted(seen.items(), key=lambda kv: (client_sort_key(kv[1]), kv[0]))


def ladders_in(runs: list[Run]) -> list[str]:
    found = {p.ladder for run in runs for p in run.probes}
    ordered = [lad for lad in LADDER_ORDER if lad in found]
    return ordered + sorted(found - set(ordered))


def ladder_probes(run: Run, identity: str, ladder: str) -> list[Probe]:
    return sorted(
        (p for p in run.probes if p.identity == identity and p.ladder == ladder),
        key=lambda p: p.send_index,
    )


def ladder_summary(run: Run, identity: str, ladder: str) -> dict | None:
    """Tau and call for one validator ladder; prefers the analysis file, else computes tau."""
    probes = ladder_probes(run, identity, ladder)
    computed = None
    if probes:
        computed = kendall_tau_b(
            [p.priority_fee_lamports for p in probes], [p.block_index for p in probes]
        )
    for row in run.ladders:
        if row.get("identity") == identity and row.get("ladder") == ladder:
            tau = row.get("tau_fee_block_index")
            return {
                "tau": computed if tau is None else tau,
                "tau_computed": computed,
                "call": row.get("call"),
                "n": row.get("n", len(probes)),
                "p_perm": row.get("p_perm"),
            }
    if not probes:
        return None
    return {"tau": computed, "tau_computed": computed, "call": None, "n": len(probes), "p_perm": None}
