"""Shared look: Okabe-Ito categorical colours, viridis for fees, deterministic output."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402

OKABE_ITO = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "vermillion": "#D55E00",
    "sky": "#56B4E9",
    "purple": "#CC79A7",
    "yellow": "#F0E442",
    "black": "#000000",
}

RUN_COLORS = [OKABE_ITO[k] for k in ("blue", "orange", "green", "purple", "sky", "vermillion")]
RUN_MARKERS = ["o", "s", "^", "D", "v", "P"]
LADDER_MARKERS = {"conflict": "o", "nonconflict": "s"}
LADDER_TITLES = {
    "conflict": "conflict ladder (one write-locked account)",
    "nonconflict": "non-conflict ladder",
}

OTHER_TX = "#BBBBBB"
INK = "#222222"
MUTED = "#666666"

CALL_LABELS = {
    "strict_priority": "strict priority",
    "priority": "priority",
    "arrival": "arrival",
    "no_rule_detected": "no rule",
    "inconclusive": "inconclusive",
}


def apply() -> None:
    plt.rcdefaults()
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.edgecolor": "#888888",
            "axes.labelcolor": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#E6E6E6",
            "grid.linewidth": 0.6,
            "xtick.color": "#444444",
            "ytick.color": "#444444",
            "legend.frameon": False,
            "legend.fontsize": 8,
            "figure.dpi": 100,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
            "savefig.facecolor": "white",
            "svg.hashsalt": "inside-the-block",
            "svg.fonttype": "path",
            "path.simplify": False,
        }
    )


def run_color(i: int) -> str:
    return RUN_COLORS[i % len(RUN_COLORS)]


def run_marker(i: int) -> str:
    return RUN_MARKERS[i % len(RUN_MARKERS)]


def fee_scale(rungs: list[int]):
    """Discrete viridis map with one colour per fee rung."""
    rungs = sorted(set(rungs))
    cmap = ListedColormap(plt.get_cmap("viridis")(
        [0.0] if len(rungs) == 1 else [i / (len(rungs) - 1) * 0.92 for i in range(len(rungs))]
    ))
    bounds = [i - 0.5 for i in range(len(rungs) + 1)]
    norm = BoundaryNorm(bounds, cmap.N)
    index = {r: i for i, r in enumerate(rungs)}
    return rungs, cmap, norm, index


def short_id(identity: str) -> str:
    return f"{identity[:4]}…{identity[-4:]}" if identity and len(identity) > 10 else identity or "?"


def validator_label(client: str, identity: str, version: str | None = None) -> str:
    head = f"{client} {version}" if version else client
    return f"{head}\n{short_id(identity)}"


def save(fig, out_dir: Path, stem: str, formats: tuple[str, ...]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fmt in formats:
        path = out_dir / f"{stem}.{fmt}"
        if fmt == "svg":
            metadata = {"Date": None, "Creator": None}
        elif fmt == "pdf":
            metadata = {"CreationDate": None, "ModDate": None, "Creator": None, "Producer": None}
        else:
            metadata = {"Software": None}
        fig.savefig(path, format=fmt, metadata=metadata)
        written.append(path)
    plt.close(fig)
    return written
