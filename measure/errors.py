class MeasureError(Exception):
    """Base error for the measurement pipeline."""


class ConfigError(MeasureError):
    """Invalid configuration or CLI arguments."""


class RpcError(MeasureError):
    """JSON-RPC request failed."""


class SpendCapExceeded(MeasureError):
    """The next fee would push the estimated spend past the cap."""

    def __init__(self, spent: int, attempted: int, cap: int, *, scope: str = "cumulative") -> None:
        self.spent = spent
        self.attempted = attempted
        self.cap = cap
        self.scope = scope
        name = "per-run spend cap (RUN_SPEND_CAP_SOL)" if scope == "run" else (
            "cumulative spend cap (SPEND_CAP_SOL, all runs in data/spend.json)"
        )
        super().__init__(
            f"{name} exceeded: "
            f"spent {spent} + next {attempted} > cap {cap} lamports"
        )


class RegimeError(MeasureError):
    """The cluster is not in the measurement regime this run is built for."""
