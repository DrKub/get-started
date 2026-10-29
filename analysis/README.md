# Reproducing the analysis

From the repository root, run:

```bash
python3 -m analysis
python3 -m pytest analysis/tests
```

The analyzer uses only Python's standard library and no network. It reads every
live run under `data/runs/`, meaning a `manifest.json` with `mode: live` and a
`block_observations.jsonl`. It writes these deterministic products to
`analysis/output/`:

- `ladder_statistics.csv`: validator × run × ladder tau-b, exact p-value,
  pair shares, and pre-registered call. It also has the send order,
  `tau_fee_send`, `tau_send_b`, target vs landed slot, block leader, the
  ladder's write-lock set, foreign writers, and `contaminated`.
- `validator_rollups.csv`: per validator and ladder, the cross-run rollup
  (`strict_priority`, `priority`, `arrival`, `no_rule_detected`,
  `mixed_across_slots`, `inconclusive`).
- `pooled_pair_shares.csv`: pooled fee-order and arrival-order pair shares with
  95% cluster-bootstrap percentile intervals.
- `cross_run_consistency.csv`: calls and descriptive effect sizes side by side.
- `block_positions.csv`: each ladder's absolute and fractional block position,
  span, and gaps between adjacent rungs.
- `evidence_transactions.csv`: every claim ID joined to its slot, signature,
  block index, fee, and Solscan and Explorer URLs.
- `EVIDENCE.md`: the same evidence as linked Markdown, one anchor per ladder
  (`#<run>-<client>-<ladder>`, lowercase). `REPORT.md` links to these anchors.
- `analysis.json`: the same results and method metadata in one machine-readable
  file.

Exact p-values enumerate all `n!` block orders in the direction of the observed
tau. The `arrival` call is tested against send order (`tau_send_b`), so it is
correct whether a ladder was sent ascending, descending, or shuffled. The
bootstrap resamples complete validator-run ladders (10,000 replicates, seed
`20261029`), because pair comparisons inside one ladder are dependent. Run 4's
four-rung non-conflict ladders remain `inconclusive` under the pre-registered
minimum of five landed rungs; their effect sizes are descriptive.

## Raw blocks and contamination

`data/runs/<run>/blocks/<slot>.json.gz` holds the `getBlock` body
(`transactionDetails: "accounts"`, `maxSupportedTransactionVersion: 1`) for each
measured slot. For each ladder, the analysis:

- re-derives every block index from that body and fails if it disagrees with
  the live observation or `block_context.jsonl`;
- reads the producing leader from the block's `Fee` reward;
- builds the ladder's write-lock set, which is the intersection of the rungs'
  writable keys for the conflict ladder and the union for the non-conflict
  ladder. It fails if the conflict rungs share nothing or the non-conflict
  rungs overlap;
- counts foreign transactions that write any account in that set.

All 30 ladders have a matching leader and zero foreign writers.

`load_raw_block` accepts the compact accounts layout (`transaction.accountKeys` as objects with `pubkey` and `writable`) and a full `jsonParsed` body (`transaction.message.accountKeys`). A raw `json` encoding, whose account keys are strings without writable flags, is rejected. The collector writes the compact layout as `blocks/<slot>.json.gz` (deterministic gzip). Runs 3, 4, and 5 were measured before that collector change; their committed blocks are the same layout, fetched afterwards.

The blocks were fetched after the runs, read-only, with:

```bash
python3 -m analysis.fetch_blocks [--rpc-url URL] [--force]
```

That command only reads. Its gzip output has a fixed header, so a refetch of the
same block produces the same bytes.
