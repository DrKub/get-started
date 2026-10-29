import hashlib
from pathlib import Path

import pytest

from viz.cli import main, render
from viz.figures import FIGURES, cost_steps
from viz.load import CLIENT_ORDER, ladder_summary, load_runs, validators_in

FIXTURES = Path(__file__).parent / "fixtures" / "runs"
RUN3, RUN4 = "20261029T094113Z", "20261029T100356Z"


@pytest.fixture(scope="module")
def runs():
    return load_runs(FIXTURES)


def _digests(paths):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_renders_every_figure_as_png_and_svg(tmp_path):
    written = render(FIXTURES, tmp_path)
    for stem in FIGURES:
        png, svg = tmp_path / f"{stem}.png", tmp_path / f"{stem}.svg"
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert svg.read_text().lstrip().startswith("<?xml")
    readme = (tmp_path / "README.md").read_text()
    assert all(f"**{stem}**" in readme for stem in FIGURES)
    assert len(written) == 2 * len(FIGURES) + 1


def test_output_is_byte_identical_across_renders(tmp_path):
    first = _digests(render(FIXTURES, tmp_path / "a"))
    second = _digests(render(FIXTURES, tmp_path / "b"))
    assert first == second


def test_run_labels_and_client_join(runs):
    assert [r.label for r in runs] == ["run 3", "run 4"]
    clients = [client for _, client in validators_in(runs)]
    assert clients == CLIENT_ORDER
    run3 = runs[0]
    assert all(p.client_id != "unknown" for p in run3.probes)


def test_computed_tau_matches_analysis_file(runs):
    checked = 0
    for run in runs:
        for row in run.ladders:
            s = ladder_summary(run, row["identity"], row["ladder"])
            assert s["tau_computed"] == pytest.approx(row["tau_fee_block_index"])
            checked += 1
    assert checked == 24


def test_cost_steps_match_spend_ledgers(runs):
    steps = cost_steps(runs)
    deltas = [(s["kind"], s["delta"]) for s in steps if s["kind"] != "subtotal"]
    assert deltas == [
        ("fee", 420_372),
        ("rent", 7_802_952),
        ("fee", 340_228),
        ("rent", 2_600_984),
        ("refund", -2_600_984),
    ]
    subtotals = [s for s in steps if s["kind"] == "subtotal"]
    assert [s["run_net"] for s in subtotals] == [8_223_324, 340_228]
    assert subtotals[-1]["total"] == 8_563_552


def test_cli_single_run_dir_with_label(tmp_path, capsys):
    out = tmp_path / "figs"
    rc = main(["--data-dir", str(FIXTURES / RUN4), "--out-dir", str(out), "--label", f"{RUN4}=fourth",
               "--format", "png", "--only", "fig2_tau_heatmap"])
    assert rc == 0
    assert (out / "fig2_tau_heatmap.png").exists()
    assert not (out / "fig2_tau_heatmap.svg").exists()
    assert "fourth" in (out / "README.md").read_text()


def test_missing_data_dir_fails_clearly(tmp_path):
    with pytest.raises(SystemExit, match="no run directories"):
        render(tmp_path / "nope", tmp_path / "out")
