"""The five report figures. Each function takes loaded runs and returns a matplotlib Figure."""

from __future__ import annotations

from collections import Counter

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.colors import TwoSlopeNorm
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

from . import style
from .load import Run, ladder_probes, ladder_summary, ladders_in, validators_in

LAMPORTS_PER_SOL = 1_000_000_000


def _version(runs: list[Run], identity: str) -> str | None:
    for run in runs:
        v = run.version_of(identity)
        if v:
            return v
    return None


def _fee_rungs(runs: list[Run], ladder: str | None = None) -> list[int]:
    return sorted(
        {p.priority_fee_lamports for r in runs for p in r.probes if ladder is None or p.ladder == ladder}
    )


def _call_text(summary: dict | None) -> str:
    if not summary or not summary.get("call"):
        return ""
    return style.CALL_LABELS.get(summary["call"], summary["call"].replace("_", " "))


def _tau_text(tau: float | None) -> str:
    return "n/a" if tau is None else f"{tau:+.2f}".replace("-", "\u2212")


def _empty(message: str):
    fig, ax = plt.subplots(figsize=(6, 2))
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", color=style.MUTED)
    return fig


def _fee_colorbar(fig, axes, rungs, cmap, norm, **kw):
    sm = ScalarMappable(norm=norm, cmap=cmap)
    cbar = fig.colorbar(sm, ax=axes, ticks=range(len(rungs)), **kw)
    cbar.ax.set_yticklabels([str(r) for r in rungs])
    cbar.set_label("priority fee (lamports)")
    cbar.outline.set_visible(False)
    return cbar


def slopegraph(runs: list[Run]):
    validators = validators_in(runs)
    ladders = ladders_in(runs)
    if not validators:
        return _empty("no landed probes")
    nrows, ncols = len(validators), len(ladders)
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(5.4 * ncols, 1.85 * nrows + 1.0), squeeze=False, constrained_layout=True
    )
    for c, ladder in enumerate(ladders):
        rungs = _fee_rungs(runs, ladder)
        xpos = {f: i for i, f in enumerate(rungs)}
        for r, (identity, client) in enumerate(validators):
            ax = axes[r][c]
            drawn = 0
            for i, run in enumerate(runs):
                probes = sorted(ladder_probes(run, identity, ladder), key=lambda p: p.priority_fee_lamports)
                if not probes:
                    continue
                base = min(p.block_index for p in probes)
                xs = [xpos[p.priority_fee_lamports] for p in probes]
                ys = [p.block_index - base for p in probes]
                ax.plot(
                    xs, ys, color=style.run_color(i), marker=style.run_marker(i), ms=5,
                    lw=1.8, mec="white", mew=0.6, zorder=3 + i,
                )
                s = ladder_summary(run, identity, ladder)
                call = _call_text(s)
                note = f"{run.label}  \u03c4 {_tau_text(s['tau'])}\n"
                note += f"{call} · " if call else ""
                note += f"idx {base}\u2013{max(p.block_index for p in probes)}"
                ax.text(1.03, 1.0 - drawn * 0.36, note, transform=ax.transAxes, va="top", ha="left",
                        fontsize=7.5, color=style.run_color(i), fontweight="bold", linespacing=1.3)
                drawn += 1
            ax.invert_yaxis()
            ax.set_xticks(range(len(rungs)), [str(f) for f in rungs])
            ax.set_xlim(-0.3, len(rungs) - 0.7)
            ax.margins(y=0.15)
            ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True, nbins=4))
            ax.set_title(
                f"{client} {_version(runs, identity) or ''} · {style.short_id(identity)} · {ladder}",
                loc="left", fontsize=9,
            )
            if c == 0:
                ax.set_ylabel("\u0394 block index\n(0 = earliest probe)")
            if r == nrows - 1:
                ax.set_xlabel("priority fee (lamports)")
            if not drawn:
                ax.text(0.5, 0.5, "no landed probes", transform=ax.transAxes, ha="center", color=style.MUTED)
    handles = [
        Line2D([], [], color=style.run_color(i), marker=style.run_marker(i), mec="white", lw=1.8,
               label=f"{run.label} ({run.name})")
        for i, run in enumerate(runs)
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))
    fig.suptitle(
        "Priority fee vs position in block, per validator and ladder\n"
        "up = included earlier; a line rising to the right means higher fees went first "
        "(\u03c4 = Kendall \u03c4-b of fee vs block index; \u22121 is strict priority)",
        fontsize=10, x=0.01, ha="left",
    )
    return fig


def tau_heatmap(runs: list[Run]):
    validators = validators_in(runs)
    ladders = ladders_in(runs)
    if not validators:
        return _empty("no landed probes")
    columns = [(ladder, run) for ladder in ladders for run in runs]
    data = np.full((len(validators), len(columns)), np.nan)
    notes = [["" for _ in columns] for _ in validators]
    for r, (identity, _) in enumerate(validators):
        for c, (ladder, run) in enumerate(columns):
            s = ladder_summary(run, identity, ladder)
            if not s or s["tau"] is None:
                notes[r][c] = "no data"
                continue
            data[r, c] = s["tau"]
            call = _call_text(s)
            notes[r][c] = f"{_tau_text(s['tau'])}\n{call}\nn={s['n']}" if call else f"{_tau_text(s['tau'])}\nn={s['n']}"

    fig, ax = plt.subplots(figsize=(1.35 * len(columns) + 3.2, 0.78 * len(validators) + 1.9),
                           constrained_layout=True)
    cmap = plt.get_cmap("RdBu_r").with_extremes(bad="#F2F2F2")
    norm = TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1)
    im = ax.imshow(np.ma.masked_invalid(data), cmap=cmap, norm=norm, aspect="auto")
    for r in range(len(validators)):
        for c in range(len(columns)):
            v = data[r, c]
            color = "white" if not np.isnan(v) and abs(v) > 0.55 else style.INK
            ax.text(c, r, notes[r][c], ha="center", va="center", fontsize=7.5, color=color, linespacing=1.25)
    ax.set_xticks(range(len(columns)), [f"{ladder}\n{run.label}" for ladder, run in columns])
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(validators)),
                  [style.validator_label(client, ident, _version(runs, ident)) for ident, client in validators])
    ax.tick_params(length=0)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(columns)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(validators)), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", length=0)
    for k in range(1, len(ladders)):
        ax.axvline(k * len(runs) - 0.5, color=style.INK, lw=1.5)
    cbar = fig.colorbar(im, ax=ax, shrink=0.85, ticks=[-1, -0.5, 0, 0.5, 1])
    cbar.set_label("Kendall \u03c4-b (fee vs block index)\n\u22121 higher fee earlier · +1 higher fee later")
    cbar.outline.set_visible(False)
    ax.set_title("Kendall \u03c4 by validator client, ladder and run", loc="left", pad=12)
    return fig


def _ladder_slot(probes) -> int:
    return Counter(p.slot for p in probes).most_common(1)[0][0]


def block_position(runs: list[Run]):
    validators = validators_in(runs)
    ladders = ladders_in(runs)
    rows = []
    for identity, client in validators:
        group = []
        for ladder in ladders:
            for run in runs:
                probes = ladder_probes(run, identity, ladder)
                if probes:
                    group.append((identity, client, ladder, run, probes))
        if group:
            rows.append(group)
    if not rows:
        return _empty("no landed probes")
    flat = [row for group in rows for row in group]
    rungs, cmap, norm, rung_index = style.fee_scale(_fee_rungs(runs))

    span = max(max(p.block_index for p in probes) - min(p.block_index for p in probes)
               for *_, probes in flat)
    fig, (ax_full, ax_zoom) = plt.subplots(
        1, 2, figsize=(12.5, 0.36 * len(flat) + 1.8), sharey=True,
        gridspec_kw={"width_ratios": [2.3, 1]}, constrained_layout=True,
    )
    yticks, ylabels = [], []
    y = 0.0
    for gi, group in enumerate(rows):
        if gi:
            y += 0.6
            for ax in (ax_full, ax_zoom):
                ax.axhline(y - 0.8, color="#DDDDDD", lw=0.8)
        for identity, client, ladder, run, probes in group:
            slot = _ladder_slot(probes)
            block = run.blocks.get(slot)
            sigs = {p.signature for p in probes}
            base = min(p.block_index for p in probes)
            if block:
                ax_full.hlines(y, 0, block.tx_count - 1, color="#EEEEEE", lw=7, zorder=1)
                ax_full.vlines(block.other_indexes, y - 0.28, y + 0.28, color=style.OTHER_TX, lw=0.4, zorder=2)
                mine = [i for s, i in block.ours.items() if s not in sigs]
                ax_full.vlines(mine, y - 0.32, y + 0.32, color=style.MUTED, lw=1.0, zorder=2)
                ax_full.text(block.tx_count + 6, y, f"slot {slot} · {block.tx_count} tx",
                             va="center", fontsize=7, color=style.MUTED)
                near = [i - base for i in block.other_indexes if -3 <= i - base <= span + 3]
                ax_zoom.vlines(near, y - 0.28, y + 0.28, color=style.OTHER_TX, lw=1.2, zorder=2)
                near_mine = [i - base for i in mine if -3 <= i - base <= span + 3]
                ax_zoom.vlines(near_mine, y - 0.32, y + 0.32, color=style.MUTED, lw=1.6, zorder=2)
            colors = [cmap(norm(rung_index[p.priority_fee_lamports])) for p in probes]
            marker = style.LADDER_MARKERS.get(ladder, "o")
            ax_full.scatter([p.block_index for p in probes], [y] * len(probes), c=colors, s=26,
                            marker=marker, edgecolors=style.INK, linewidths=0.4, zorder=4)
            ax_zoom.scatter([p.block_index - base for p in probes], [y] * len(probes), c=colors, s=34,
                            marker=marker, edgecolors=style.INK, linewidths=0.4, zorder=4)
            yticks.append(y)
            ylabels.append(f"{client} · {ladder} · {run.label}")
            y += 1.0
    for ax in (ax_full, ax_zoom):
        ax.set_yticks(yticks, ylabels)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", length=0)
    ax_full.invert_yaxis()
    ax_full.set_xlim(left=-5)
    ax_full.set_xlabel("index in block (0 = first transaction)")
    ax_full.set_title("Where the probes sit in the whole block", loc="left")
    ax_zoom.set_xlim(-3.5, span + 3.5)
    ax_zoom.set_xlabel("positions after the earliest probe")
    ax_zoom.set_title("Close-up: probes and their neighbours", loc="left")
    legend = [
        Line2D([], [], color=style.OTHER_TX, lw=1.2, label="other transactions"),
        Line2D([], [], color=style.MUTED, lw=1.6, label="our other transactions"),
        Line2D([], [], ls="", marker="o", mfc="white", mec=style.INK, label="conflict probe"),
        Line2D([], [], ls="", marker="s", mfc="white", mec=style.INK, label="non-conflict probe"),
    ]
    _fee_colorbar(fig, [ax_full, ax_zoom], rungs, cmap, norm, shrink=0.5, pad=0.01)
    fig.legend(handles=legend, loc="outside lower center", ncol=len(legend), fontsize=7.5)
    return fig


def send_time(runs: list[Run]):
    validators = validators_in(runs)
    ladders = ladders_in(runs)
    if not validators:
        return _empty("no landed probes")
    rungs, cmap, norm, rung_index = style.fee_scale(_fee_rungs(runs))
    nrows, ncols = len(validators), len(runs)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.3 * ncols + 1.2, 1.9 * nrows + 1.0), squeeze=False,
                             constrained_layout=True)
    for r, (identity, client) in enumerate(validators):
        for c, run in enumerate(runs):
            ax = axes[r][c]
            bursts = [(lad, ladder_probes(run, identity, lad)) for lad in ladders]
            bursts = [(lad, ps) for lad, ps in bursts if ps and all(p.first_send_ms is not None for p in ps)]
            if not bursts:
                ax.text(0.5, 0.5, "no send times", transform=ax.transAxes, ha="center", color=style.MUTED)
                ax.set_xticks([])
                ax.set_yticks([])
                continue
            t0 = min(p.first_send_ms for _, ps in bursts for p in ps)
            ticks, tick_labels, offset_x = [], [], 0
            for ladder, probes in bursts:
                base = min(p.block_index for p in probes)
                xs = [offset_x + p.send_index for p in probes]
                ys = [p.block_index - base for p in probes]
                ticks += xs
                tick_labels += [str(p.send_index) for p in probes]
                burst_ms = min(p.first_send_ms for p in probes) - t0
                offset_x += max(p.send_index for p in probes) + 2
                ax.plot(xs, ys, color="#999999", lw=0.9, zorder=2)
                ax.scatter(xs, ys, c=[cmap(norm(rung_index[p.priority_fee_lamports])) for p in probes],
                           marker=style.LADDER_MARKERS.get(ladder, "o"), s=30, edgecolors=style.INK,
                           linewidths=0.4, zorder=3)
                slot = _ladder_slot(probes)
                target = probes[0].target_slot
                offset = ""
                if target is not None and slot != target:
                    d = slot - target
                    offset = f" · landed {abs(d)} slot{'s' if abs(d) > 1 else ''} {'early' if d < 0 else 'late'}"
                copies = [p.copy_send_unix_ms for p in probes if p.copy_send_unix_ms]
                spread = max((max(cs) - min(cs) for cs in copies), default=0)
                copy_note = f" · copies spread {spread} ms" if spread else ""
                ax.annotate(f"{ladder} @ +{burst_ms} ms{offset}{copy_note}", (min(xs), 0), xytext=(0, 6),
                            textcoords="offset points", fontsize=6.5, color=style.MUTED, va="bottom")
            ax.set_xticks(ticks, tick_labels, fontsize=7)
            ax.invert_yaxis()
            ax.margins(x=0.08, y=0.25)
            ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True, nbins=4))
            ax.set_title(f"{client} · {style.short_id(identity)} · {run.label}", loc="left", fontsize=9)
            if c == 0:
                ax.set_ylabel("\u0394 block index\n(0 = earliest probe)")
            if r == nrows - 1:
                ax.set_xlabel("send index within the burst (0 = first stream written)\n"
                              "+N ms = burst start after this leader's first burst")
    _fee_colorbar(fig, axes.ravel().tolist(), rungs, cmap, norm, shrink=0.35, pad=0.01)
    handles = [
        Line2D([], [], ls="", marker=style.LADDER_MARKERS[k], mfc="white", mec=style.INK, label=f"{k} probe")
        for k in ladders if k in style.LADDER_MARKERS
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles))
    fig.suptitle(
        "Send order vs position in block. Every rung of a burst carries the same millisecond timestamp, so "
        "only the order is observed, not the spacing.\nRuns 3-4 sent lowest fee first: send index = fee rank. "
        "Falling to the right = FIFO arrival order; rising = fee order or a LIFO drain (indistinguishable here).",
        fontsize=9.5, x=0.01, ha="left",
    )
    return fig


def cost_steps(runs: list[Run]) -> list[dict]:
    """Waterfall steps in lamports from each run's spend.json."""
    steps = []
    total = 0
    for run in runs:
        spend = run.spend
        if not spend:
            continue
        fees = int(spend.get("signed_lamports") or spend.get("planned_fee_lamports") or 0)
        principal = int(spend.get("funding_principal_lamports") or 0)
        recovered = int(spend.get("temporary_rent_lamports") or 0)
        steps.append({"label": f"{run.label}\nfees", "delta": fees, "kind": "fee"})
        if recovered:
            steps.append({"label": f"{run.label}\ntemporary rent", "delta": principal, "kind": "rent"})
            steps.append({"label": f"{run.label}\nsweep refund", "delta": -recovered, "kind": "refund"})
        elif principal:
            steps.append({"label": f"{run.label}\nrent not recovered", "delta": principal, "kind": "rent"})
        net = fees + principal - recovered
        if spend.get("net_lamports") is not None and int(spend["net_lamports"]) != net:
            raise ValueError(f"{run.name}: spend.json net_lamports {spend['net_lamports']} != computed {net}")
        total += net
        steps.append({"label": f"after {run.label}", "delta": 0, "kind": "subtotal", "total": total,
                      "run_net": net, "peak": spend.get("peak_lamports")})
    return steps


def cost_waterfall(runs: list[Run]):
    steps = cost_steps(runs)
    if not steps:
        return _empty("no spend.json in any run")
    colors = {"fee": style.OKABE_ITO["orange"], "rent": style.OKABE_ITO["vermillion"],
              "refund": style.OKABE_ITO["green"], "subtotal": style.OKABE_ITO["blue"]}
    fig, ax = plt.subplots(figsize=(1.25 * len(steps) + 2.2, 4.6), constrained_layout=True)
    cum = 0
    top = max(1, max(s.get("total", 0) for s in steps))
    prev_end = None
    running = 0
    for s in steps:
        running += s["delta"]
        top = max(top, running)
    for i, s in enumerate(steps):
        if s["kind"] == "subtotal":
            bottom, height = 0, s["total"]
            cum = s["total"]
            text = f"{s['total']:,}"
            sub = f"run net {s['run_net']:,}"
            if s.get("peak"):
                sub += f"\npeak {int(s['peak']):,}"
        else:
            bottom = cum if s["delta"] >= 0 else cum + s["delta"]
            height = abs(s["delta"])
            cum += s["delta"]
            text = f"{s['delta']:+,}"
            sub = None
        ax.bar(i, height, bottom=bottom, width=0.62, color=colors[s["kind"]], edgecolor="white", zorder=3)
        if prev_end is not None:
            ax.plot([i - 1 + 0.31, i - 0.31], [prev_end, prev_end], color="#999999", lw=0.8, ls=":", zorder=2)
        prev_end = cum
        ax.text(i, bottom + height + top * 0.012, text, ha="center", va="bottom", fontsize=7.5,
                color=style.INK, fontweight="bold" if s["kind"] == "subtotal" else "normal")
        if sub:
            ax.text(i, bottom + height + top * 0.06, sub, ha="center", va="bottom", fontsize=7,
                    color=style.MUTED)
    ax.set_xticks(range(len(steps)), [s["label"] for s in steps], fontsize=8)
    ax.set_ylim(0, top * 1.2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylabel("lamports (cumulative)")
    ax.grid(axis="x", visible=False)
    sec = ax.secondary_yaxis("right", functions=(lambda v: v / LAMPORTS_PER_SOL, lambda v: v * LAMPORTS_PER_SOL))
    sec.set_ylabel("SOL")
    sec.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.4f}"))
    legend = [plt.Rectangle((0, 0), 1, 1, color=colors[k], label=lab) for k, lab in
              [("fee", "transaction fees"), ("rent", "probe-account rent"), ("refund", "rent returned by sweep"),
               ("subtotal", "cumulative total")]]
    ax.legend(handles=legend, loc="upper left", fontsize=7.5)
    ax.set_title("What the measurement cost on mainnet", loc="left")
    return fig


FIGURES = {
    "fig1_slopegraph": (
        slopegraph,
        "Priority fee vs index in block for each validator and ladder, one line per run, with Kendall \u03c4 "
        "and the pre-registered call beside each panel.",
    ),
    "fig2_tau_heatmap": (
        tau_heatmap,
        "Kendall \u03c4-b of fee vs block index for every validator client, ladder and run "
        "(blue = higher fee earlier, red = higher fee later).",
    ),
    "fig3_block_position": (
        block_position,
        "Where our probes sit in the full block (left) and among their neighbours (right), coloured by "
        "priority fee.",
    ),
    "fig4_send_time": (
        send_time,
        "Send order vs position in block for each probe burst. All rungs of a burst share one send "
        "timestamp; in runs 3-4 send index equals fee rank, so this is not independent of fig1.",
    ),
    "fig5_cost_waterfall": (
        cost_waterfall,
        "Mainnet cost per run in lamports: fees, probe-account rent, and the rent the sweep returned.",
    ),
}
