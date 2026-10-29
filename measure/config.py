"""CLI and environment configuration.

Sending requires an explicit switch: ``--live`` or ``MODE=live``. They are the
same gate. Any other ``MODE`` value, including an empty one, stays on dry-run.
The spend cap cannot be raised above 0.05 SOL.
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
    DEFAULT_RPC_URL,
    DEFAULT_SEND_ORDER,
    DEFAULT_TARGET_VALIDATORS,
    SEND_ORDERS,
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
    leader_page: int
    data_dir: Path
    output_dir: Path | None
    keypair_path: Path | None
    live: bool
    mode: str
    mode_warning: str | None
    experiment_id: str | None
    fixture_dir: Path | None
    collect: bool
    timeout_s: float
    validators_app_token: str | None
    ws_url: str | None = None
    tpu_copies: int = 1
    send_order: str = DEFAULT_SEND_ORDER
    send_order_seed: int | None = None
    ladders: str = "both"
    run_spend_cap_lamports: int | None = None

    @property
    def spend_cap_sol(self) -> str:
        return lamports_to_sol_str(self.spend_cap_lamports)


def config_from_env() -> dict:
    """Values that may come from the environment. ``live`` is intentionally absent."""
    return {
        "rpc_url": _env("RPC_URL") or DEFAULT_RPC_URL,
        "spend_cap_sol": _env("SPEND_CAP_SOL") or lamports_to_sol_str(DEFAULT_CAP_LAMPORTS),
        "run_spend_cap_sol": _env("RUN_SPEND_CAP_SOL"),
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
        "leader_page": _positive_int("LEADER_PAGE", _env("LEADER_PAGE") or str(DEFAULT_LEADER_PAGE)),
        "data_dir": Path(_env("DATA_DIR") or "data"),
        "output_dir": Path(_env("OUTPUT_DIR")) if _env("OUTPUT_DIR") else None,
        "keypair_path": Path(_env("KEYPAIR_PATH")) if _env("KEYPAIR_PATH") else None,
        "experiment_id": _env("EXPERIMENT_ID"),
        "timeout_s": float(_env("RPC_TIMEOUT_S") or "60"),
        "validators_app_token": _env("VALIDATORS_APP_TOKEN"),
        "mode": (_env("MODE") or "").lower(),
        "ws_url": _env("WS_URL"),
        "tpu_copies": _positive_int("TPU_COPIES", _env("TPU_COPIES") or "1"),
        "collect": (_env("COLLECT") or "").lower() in ("1", "true", "yes"),
        "send_order": (_env("SEND_ORDER") or DEFAULT_SEND_ORDER).lower(),
        "ladders": (_env("LADDERS") or "both").lower(),
        "send_order_seed": (
            _positive_int("SEND_ORDER_SEED", _env("SEND_ORDER_SEED"))
            if _env("SEND_ORDER_SEED")
            else None
        ),
    }


def resolve_mode(explicit_live: bool, mode: str) -> tuple[bool, str, str | None]:
    """Map ``MODE`` and ``--live`` onto one switch.

    ``MODE=live`` and ``--live`` both enable sending. An absent or
    unrecognised ``MODE`` stays on dry-run unless ``--live`` was passed.
    """
    warning = None
    recognized = mode in ("", "dry-run", "dry_run", "dryrun", "live")
    if mode and not recognized:
        warning = f"MODE={mode!r} is not recognised; staying on dry-run"
    live = explicit_live or mode == "live"
    if warning and explicit_live:
        live = True
        warning = f"MODE={mode!r} is not recognised; --live still enables sending"
    label = "live" if live else "dry-run"
    return live, label, warning


def make_config(
    *,
    rpc_url: str | None = None,
    spend_cap_sol: str | None = None,
    lookahead_slots: int | None = None,
    min_lead_slots: int | None = None,
    target_validators: int | None = None,
    min_client_families: int | None = None,
    leader_page: int | None = None,
    data_dir: str | None = None,
    output_dir: str | None = None,
    keypair_path: str | None = None,
    experiment_id: str | None = None,
    fixture_dir: str | None = None,
    live: bool = False,
    collect: bool = False,
    timeout_s: float | None = None,
    tpu_copies: int | None = None,
    send_order: str | None = None,
    send_order_seed: int | None = None,
    ladders: str | None = None,
    run_spend_cap_sol: str | None = None,
) -> Config:
    env = config_from_env()
    cap = resolve_spend_cap(spend_cap_sol or env["spend_cap_sol"])
    run_cap_text = run_spend_cap_sol or env["run_spend_cap_sol"]
    run_cap = resolve_spend_cap(run_cap_text) if run_cap_text else None
    enabled, mode_label, mode_warning = resolve_mode(live, env["mode"])
    config = Config(
        rpc_url=rpc_url or env["rpc_url"],
        spend_cap_lamports=cap,
        lookahead_slots=env["lookahead_slots"] if lookahead_slots is None else lookahead_slots,
        min_lead_slots=env["min_lead_slots"] if min_lead_slots is None else min_lead_slots,
        target_validators=env["target_validators"] if target_validators is None else target_validators,
        min_client_families=(
            env["min_client_families"] if min_client_families is None else min_client_families
        ),
        leader_page=env["leader_page"] if leader_page is None else leader_page,
        data_dir=Path(data_dir) if data_dir else env["data_dir"],
        output_dir=Path(output_dir) if output_dir else env["output_dir"],
        keypair_path=Path(keypair_path) if keypair_path else env["keypair_path"],
        live=enabled,
        mode=mode_label,
        mode_warning=mode_warning,
        experiment_id=experiment_id or env["experiment_id"],
        fixture_dir=Path(fixture_dir) if fixture_dir else None,
        collect=collect or env["collect"],
        timeout_s=timeout_s if timeout_s is not None else env["timeout_s"],
        validators_app_token=env["validators_app_token"],
        ws_url=env["ws_url"],
        tpu_copies=env["tpu_copies"] if tpu_copies is None else tpu_copies,
        send_order=(send_order or env["send_order"]).lower(),
        send_order_seed=env["send_order_seed"] if send_order_seed is None else send_order_seed,
        ladders=(ladders or env["ladders"]).lower(),
        run_spend_cap_lamports=run_cap,
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
    if config.lookahead_slots < 4:
        raise ConfigError("LOOKAHEAD_SLOTS must be at least 4")
    if config.min_lead_slots < 1:
        raise ConfigError("MIN_LEAD_SLOTS must be at least 1")
    if config.leader_page < 1 or config.leader_page > 5000:
        raise ConfigError("LEADER_PAGE must be between 1 and 5000")
    if config.spend_cap_lamports > DEFAULT_CAP_LAMPORTS:
        raise ConfigError("spend cap exceeds 0.05 SOL")
    if config.tpu_copies < 1 or config.tpu_copies > 4:
        raise ConfigError("TPU_COPIES must be between 1 and 4")
    if config.send_order not in SEND_ORDERS:
        raise ConfigError(f"SEND_ORDER must be one of {', '.join(SEND_ORDERS)}")
    if config.ladders not in ("both", "conflict"):
        raise ConfigError("LADDERS must be both or conflict")
    if config.ws_url is not None and not config.ws_url.startswith(("ws://", "wss://")):
        raise ConfigError("WS_URL must start with ws:// or wss://")
    if config.experiment_id is not None:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]+", config.experiment_id):
            raise ConfigError("EXPERIMENT_ID must match [A-Za-z0-9_-]+")
