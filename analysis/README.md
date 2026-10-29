# Reproducing the analysis

From the repository root, run:

```bash
python3 -m analysis
python3 -m unittest discover -s analysis/tests -v
```

The analyzer uses only Python's standard library. It discovers run directories
under `data/runs/` and writes these deterministic products to
`analysis/output/`:

- `ladder_statistics.csv`: validator × run × ladder tau-b, exact p-value,
  pair shares, and pre-registered call.
- `pooled_pair_shares.csv`: pooled fee-order and arrival-order pair shares with
  95% cluster-bootstrap percentile intervals.
- `cross_run_consistency.csv`: calls and descriptive effect sizes side by side.
- `block_positions.csv`: each ladder's absolute and fractional block position,
  span, and gaps between adjacent rungs.
- `evidence_transactions.csv`: every claim ID joined to its slot, signature,
  block index, fee, and mainnet explorer URL.
- `analysis.json`: the same results and method metadata in one machine-readable
  file.

Exact p-values enumerate all `n!` block orders in the direction of the observed
tau. The bootstrap resamples complete validator-run ladders (10,000 replicates,
seed `20261029`), because pair comparisons inside one ladder are dependent.
Run 4's four-rung non-conflict ladders remain `inconclusive` under the
pre-registered minimum of five landed rungs; their effect sizes are descriptive.

The no-block archive's `block_context.jsonl` contains transaction positions but
not writable account keys. It can verify positions and gaps, but cannot support
a contamination check.
