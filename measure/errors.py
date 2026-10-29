class MeasureError(Exception):
    """Base error for the measurement pipeline."""


class ConfigError(MeasureError):
    """Invalid configuration or CLI arguments."""


class RpcError(MeasureError):
    """JSON-RPC request failed."""


class SpendCapExceeded(MeasureError):
    """The next fee would push the estimated spend past the cap."""

    def __init__(self, spent: int, attempted: int, cap: int) -> None:
        self.spent = spent
        self.attempted = attempted
        self.cap = cap
        super().__init__(
            "spend cap exceeded: "
            f"spent {spent} + next {attempted} > cap {cap} lamports"
        )


class RegimeError(MeasureError):
    """The cluster is not in the measurement regime this run is built for."""
