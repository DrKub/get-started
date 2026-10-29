"""CLI and environment configuration.

``--live`` is the only switch that sends. Environment variables can point at
an RPC URL, a keypair file, and a lower spend cap. They cannot enable sending
and they cannot raise the cap above 0.05 SOL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from measure.constants import (
    DEFAULT_CAP_LAMPORTS,
    DEFAULT_LEADER_PAGE,
    DEFAULT_LOOKAHEAD_SLOTS,
    DEFAULT_MIN_CLIENT_FAMILIES,
    DEFAULT_MIN_LEAD_SLOTS,
    DEFAULT_RPC_REPEATS,
    DEFAULT_RPC_URL,
    DEFAULT_TARGET_VALIDATORS,
    DEFAULT_TPU_REPEATS,
)
from measure.errors import ConfigError
from measure.fees import lamports_to_sol_str, resolve_spend_cap


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _positive_int(name: str, text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {text!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} must be >= 0")
    return value


@dataclass(frozen=True)
class Config:
    rpc_url: str
    spend_cap_lamports: int
    lookahead_slots: int
    min_lead_slots: int
    target_validators: int
    min_client_families: int
    tpu_repeats: int
    rpc_repeats: int
    leader_page: int
    output_dir: Path
    keypair_path: Path | None
    live: bool
    experiment_id: str | None
    fixture_dir: Path | None
    collect: bool
    timeout_s: float

    @property
    def spend_cap_sol(self) -> str:
        return lamports_to_sol_str(self.spend_cap_lamports)


def config_from_env() -> dict:
    """Values that may come from the environment. ``live`` is intentionally absent."""
    return {
        "rpc_url": _env("RPC_URL") or DEFAULT_RPC_URL,
        "spend_cap_sol": _env("SPEND_CAP_SOL") or lamports_to_sol_str(DEFAULT_CAP_LAMPORTS),
        "lookahead_slots": _positive_int(
            "LOOKAHEAD_SLOTS", _env("LOOKAHEAD_SLOTS") or str(DEFAULT_LOOKAHEAD_SLOTS)
        ),
        "min_lead_slots": _positive_int(
            "MIN_LEAD_SLOTS", _env("MIN_LEAD_SLOTS") or str(DEFAULT_MIN_LEAD_SLOTS)
        ),
        "target_validators": _positive_int(
            "TARGET_VALIDATORS", _env("TARGET_VALIDATORS") or str(DEFAULT_TARGET_VALIDATORS)
        ),
        "min_client_families": _positive_int(
            "MIN_CLIENT_FAMILIES",
            _env("MIN_CLIENT_FAMILIES") or str(DEFAULT_MIN_CLIENT_FAMILIES),
        ),
        "tpu_repeats": _positive_int("TPU_REPEATS", _env("TPU_REPEATS") or str(DEFAULT_TPU_REPEATS)),
        "rpc_repeats": _positive_int("RPC_REPEATS", _env("RPC_REPEATS") or str(DEFAULT_RPC_REPEATS)),
        "leader_page": _positive_int("LEADER_PAGE", _env("LEADER_PAGE") or str(DEFAULT_LEADER_PAGE)),
        "output_dir": Path(_env("OUTPUT_DIR") or "data/raw"),
        "keypair_path": Path(_env("KEYPAIR_PATH")) if _env("KEYPAIR_PATH") else None,
        "experiment_id": _env("EXPERIMENT_ID"),
        "timeout_s": float(_env("RPC_TIMEOUT_S") or "60"),
    }


def make_config(
    *,
    rpc_url: str | None = None,
    spend_cap_sol: str | None = None,
    lookahead_slots: int | None = None,
    min_lead_slots: int | None = None,
    target_validators: int | None = None,
    min_client_families: int | None = None,
    tpu_repeats: int | None = None,
    rpc_repeats: int | None = None,
    leader_page: int | None = None,
    output_dir: str | None = None,
    keypair_path: str | None = None,
    experiment_id: str | None = None,
    fixture_dir: str | None = None,
    live: bool = False,
    collect: bool = False,
    timeout_s: float | None = None,
) -> Config:
    env = config_from_env()
    cap = resolve_spend_cap(spend_cap_sol or env["spend_cap_sol"])
    config = Config(
        rpc_url=rpc_url or env["rpc_url"],
        spend_cap_lamports=cap,
        lookahead_slots=env["lookahead_slots"] if lookahead_slots is None else lookahead_slots,
        min_lead_slots=env["min_lead_slots"] if min_lead_slots is None else min_lead_slots,
        target_validators=env["target_validators"] if target_validators is None else target_validators,
        min_client_families=(
            env["min_client_families"] if min_client_families is None else min_client_families
        ),
        tpu_repeats=env["tpu_repeats"] if tpu_repeats is None else tpu_repeats,
        rpc_repeats=env["rpc_repeats"] if rpc_repeats is None else rpc_repeats,
        leader_page=env["leader_page"] if leader_page is None else leader_page,
        output_dir=Path(output_dir) if output_dir else env["output_dir"],
        keypair_path=Path(keypair_path) if keypair_path else env["keypair_path"],
        live=live,
        experiment_id=experiment_id or env["experiment_id"],
        fixture_dir=Path(fixture_dir) if fixture_dir else None,
        collect=collect,
        timeout_s=timeout_s if timeout_s is not None else env["timeout_s"],
    )
    _validate(config)
    return config


def _validate(config: Config) -> None:
    if config.live and config.fixture_dir is not None:
        raise ConfigError("refusing --live together with --fixture-dir")
    if config.target_validators < 6:
        raise ConfigError("TARGET_VALIDATORS must be at least 6")
    if config.min_client_families < 3:
        raise ConfigError("MIN_CLIENT_FAMILIES must be at least 3")
    if config.tpu_repeats < 1:
        raise ConfigError("TPU_REPEATS must be at least 1")
    if config.rpc_repeats < 1:
        raise ConfigError("RPC_REPEATS must be at least 1")
    if config.lookahead_slots < 4:
        raise ConfigError("LOOKAHEAD_SLOTS must be at least 4")
    if config.min_lead_slots < 1:
        raise ConfigError("MIN_LEAD_SLOTS must be at least 1")
    if config.leader_page < 1 or config.leader_page > 5000:
        raise ConfigError("LEADER_PAGE must be between 1 and 5000")
    if config.spend_cap_lamports > DEFAULT_CAP_LAMPORTS:
        raise ConfigError("spend cap exceeds 0.05 SOL")
    if config.experiment_id is not None:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]+", config.experiment_id):
            raise ConfigError("EXPERIMENT_ID must match [A-Za-z0-9_-]+")
