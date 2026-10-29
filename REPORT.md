# Inside the Block: how Solana validators order transactions

## Study header

| Field | Value |
| --- | --- |
| Report written (UTC date) | 2026-10-29 |
| Git commits measured | run 3 `9d482c8ff3e26b462263290dec7afbb3fe0eafab`; run 4 `164768b7b05f71c2c786d7fd05912bad58cf2896` |
| Network | mainnet |
| Alpenglow cutoff | slots ≥ 459,653,000 |
| Earliest / latest measured block time | 2026-10-29 09:42:01 / 10:10:04 UTC |
| Epochs touched | 1071 |
| `MODE` | live |
| Spend cap | 0.05 SOL hard maximum; lower run caps of 0.011 and 0.0035 SOL |
| Lamports signed | 760,600 in fees and sweep fees; run 3 also stranded 7,802,952 lamports of rent |
| Lamports observed landed (`meta.fee`) | 740,600 (420,372 run 3; 320,228 run 4); run 4's four 5,000-lamport sweep fees make its net 340,228 |
| RPC used | `mainnet.helius-rpc.com` (host only; no key retained) |
| Sending host | one vantage point; region not recorded in the supplied archive |
| Burst offset / QUIC warmup | 1 slot before target / 12 slots |
| Compute-unit limit | 1,000 |
| Fee rungs | 0, 1, 2, 4, 8, 16 lamports; run 4 non-conflict used 0, 1, 2, 4 |
| Transmission order | lowest fee first |
| `getAgGenesisCert` block slot | 459,652,999 in both manifests |

Every measured slot is 462,834,368 or later, hence after the cutoff. The two
runs landed all 84/84 and 64/64 signed transactions respectively. Of these,
132 were probes; the remainder were funding transactions. Run 3 transmitted
each probe as two duplicate TPU copies of the same signature. This is path
redundancy, not an independent observation.

## Analysis and decision rule

Tau-b is Kendall tau-b between priority fee and zero-based block index.
`−1` is perfect high-fee-first order; `+1` is perfect low-fee-first/send order.
The p-value is the exact one-sided probability, in the observed direction,
from enumeration of all `n!` block orders. Calls apply the pre-registered
minimum `n = 5`; therefore every four-rung run 4 non-conflict result is
`inconclusive`, even when its descriptive order is perfect.

Pooled shares count every within-ladder pair. Their 95% percentile intervals
resample complete validator-run ladders (10,000 replicates, seed `20261029`),
not dependent pairs.

| Scope | Ladder observations | Pairs | Fee-order share (95% CI) | Arrival-order share (95% CI) |
| --- | ---: | ---: | ---: | ---: |
| All | 24 | 306 | 0.735 (0.599–0.859) | 0.265 (0.141–0.401) |
| Conflict | 12 | 180 | 0.911 (0.822–0.983) | 0.089 (0.017–0.178) |
| Non-conflict | 12 | 126 | 0.484 (0.306–0.684) | 0.516 (0.316–0.694) |

These pooled values are descriptive: validators, clients, ladder lengths, and
blocks are not exchangeable random samples of mainnet.

## Per-validator results

Each identity contributed one conflict and one non-conflict slot per run:
four measured slots and 22/22 landed probe transactions. Because account keys
were removed with the raw blocks, this archive cannot establish that a slot
was non-contaminated. Confidence is consequently no higher than `limited`.

| Identity | Client / version | Stake (SOL) | Conflict call | Conflict tau-b by run | Non-conflict call | Non-conflict tau-b by run | Confidence |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| `7QQG…wLpE` | Agave 4.3.2 | 1,875,001.953 | `strict_priority` | −1.000, −1.000 | `arrival` (run 3); run 4 inconclusive | +1.000, +0.667 | limited |
| `9eGr…8FoY` | Firedancer 26.10.4 | 6,646,427.910 | `strict_priority` | −1.000, −1.000 | `strict_priority` (run 3); run 4 inconclusive | −1.000, −1.000 | limited |
| `HEL1…e2TU` | JitoLabs 4.3.2 | 16,665,444.377 | `strict_priority` | −1.000, −1.000 | `no_rule_detected` (run 3); run 4 inconclusive | +0.333, −0.333 | limited |
| `DRpb…21hy` | AgaveBam 4.4.0-rc.1 | 12,878,826.063 | `strict_priority` | −1.000, −1.000 | `no_rule_detected` (run 3); run 4 inconclusive | −0.467, −0.333 | limited |
| `Fd7b…69Nk` | HarmonicAgave 4.3.2 | 17,028,574.245 | `no_rule_detected` | −0.200, −0.333 | `no_rule_detected` (run 3); run 4 inconclusive | +0.333, +0.333 | limited |
| `JUPi…1h4b` | HarmonicFiredancer 26.10.4 | 9,705,355.838 | `mixed_across_slots` | −0.733, −0.600 | `no_rule_detected` (run 3); run 4 inconclusive | +0.333, 0.000 | limited |

### Per-slot detail

`Contamination` is “unknown” because the no-block archive omits writable
account keys. Block transaction count and all positions were independently
checked against `block_context.jsonl`.

| Run | Client | Slot | Ladder | n | tau-b | Fee-pair share | Arrival-pair share | Exact p | Block tx | Call | Contamination |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 3 | Agave | 462834648 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 253 | `strict_priority` | unknown |
| 3 | Agave | 462834650 | non-conflict | 6 | +1.000 | 0.000 | 1.000 | 0.001389 | 204 | `arrival` | unknown |
| 3 | Firedancer | 462835056 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 222 | `strict_priority` | unknown |
| 3 | Firedancer | 462835057 | non-conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 236 | `strict_priority` | unknown |
| 3 | AgaveBam | 462834376 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 298 | `strict_priority` | unknown |
| 3 | AgaveBam | 462834378 | non-conflict | 6 | −0.467 | 0.733 | 0.267 | 0.136111 | 217 | `no_rule_detected` | unknown |
| 3 | HarmonicAgave | 462834368 | conflict | 6 | −0.200 | 0.600 | 0.400 | 0.359722 | 376 | `no_rule_detected` | unknown |
| 3 | HarmonicAgave | 462834370 | non-conflict | 6 | +0.333 | 0.333 | 0.667 | 0.234722 | 237 | `no_rule_detected` | unknown |
| 3 | JitoLabs | 462834404 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 141 | `strict_priority` | unknown |
| 3 | JitoLabs | 462834406 | non-conflict | 6 | +0.333 | 0.333 | 0.667 | 0.234722 | 201 | `no_rule_detected` | unknown |
| 3 | HarmonicFiredancer | 462834396 | conflict | 6 | −0.733 | 0.867 | 0.133 | 0.027778 | 257 | `priority` | unknown |
| 3 | HarmonicFiredancer | 462834398 | non-conflict | 6 | +0.333 | 0.333 | 0.667 | 0.234722 | 215 | `no_rule_detected` | unknown |
| 4 | Agave | 462842240 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 243 | `strict_priority` | unknown |
| 4 | Agave | 462842242 | non-conflict | 4 | +0.667 | 0.167 | 0.833 | 0.166667 | 202 | `inconclusive` | unknown |
| 4 | Firedancer | 462840780 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 221 | `strict_priority` | unknown |
| 4 | Firedancer | 462840782 | non-conflict | 4 | −1.000 | 1.000 | 0.000 | 0.041667 | 254 | `inconclusive` | unknown |
| 4 | AgaveBam | 462840768 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 233 | `strict_priority` | unknown |
| 4 | AgaveBam | 462840769 | non-conflict | 4 | −0.333 | 0.667 | 0.333 | 0.375000 | 179 | `inconclusive` | unknown |
| 4 | HarmonicAgave | 462840756 | conflict | 6 | −0.333 | 0.667 | 0.333 | 0.234722 | 242 | `no_rule_detected` | unknown |
| 4 | HarmonicAgave | 462840758 | non-conflict | 4 | +0.333 | 0.333 | 0.667 | 0.375000 | 228 | `inconclusive` | unknown |
| 4 | JitoLabs | 462840784 | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 160 | `strict_priority` | unknown |
| 4 | JitoLabs | 462840786 | non-conflict | 4 | −0.333 | 0.667 | 0.333 | 0.375000 | 175 | `inconclusive` | unknown |
| 4 | HarmonicFiredancer | 462840740 | conflict | 6 | −0.600 | 0.800 | 0.200 | 0.068056 | 431 | `no_rule_detected` | unknown |
| 4 | HarmonicFiredancer | 462840742 | non-conflict | 4 | 0.000 | 0.500 | 0.500 | 0.250000 | 197 | `inconclusive` | unknown |

The complete evidence ledger is
[`analysis/output/evidence_transactions.csv`](analysis/output/evidence_transactions.csv).
For each of the 24 claim IDs it gives every transaction signature, slot, fee,
send index, block index, and a mainnet Explorer URL. It contains 132 probe
rows, so a judge can verify every rung rather than relying on a representative
signature. [`analysis/output/ladder_statistics.csv`](analysis/output/ladder_statistics.csv)
is the machine-readable source for the table above.

## Cross-run consistency

On the six-rung conflict ladder, five of six identities repeated the same
pre-registered call. Agave, Firedancer, JitoLabs, and AgaveBam were perfectly
fee-ordered in both runs. HarmonicAgave repeated `no_rule_detected`.
HarmonicFiredancer moved from `priority` (p = 0.0278) to
`no_rule_detected` (p = 0.0681), despite fee-order pair shares of 0.867 and
0.800. It is therefore `mixed_across_slots`, not evidence for a stable rule.

Run 4 cannot confirm any non-conflict call because all six ladders have only
four rungs. Descriptively, Firedancer repeated perfect fee order and Agave
again leaned toward arrival order. Those patterns are useful consistency
checks, not second qualifying samples. Full paired values are in
[`analysis/output/cross_run_consistency.csv`](analysis/output/cross_run_consistency.csv).

## Position within the block

The ladder's first transaction ranged from 3.6% to 89.3% through its block;
the latest final rung was at 91.0%. The median first position was 20.0%
overall, 12.4% for conflict, and 30.6% for non-conflict. Median block size was
225 transactions.

Adjacent rungs usually remained together: the median number of foreign
transactions between adjacent rungs was 0, the mean was 1.13, and the maximum
was 18. The maximum occurred in run 3's HarmonicAgave non-conflict ladder.
Conflict ladders had mean 1.08 and maximum 7; non-conflict ladders had mean
1.19 and maximum 18. These are covariates, not assigned treatments, so the
earlier median conflict position does not establish a scheduler effect.
Every ladder's index range and gap vector is in
[`analysis/output/block_positions.csv`](analysis/output/block_positions.csv).

## Measurement log

| UTC date/time | Action | Result |
| --- | --- | --- |
| Before runs 3–4 | Runs 1–2 live attempts | 0 landed; failures detailed below |
| 2026-10-29 09:41 | Run 3 funding | four public-RPC HTTP 429 responses; TPU fanout then landed funding |
| 2026-10-29 09:42–09:44 | Run 3 bursts | 84/84 total transactions landed; probes sent as duplicate copies |
| 2026-10-29 09:44 | Run 3 collection | observations and 40 block contexts collected; rent not recoverable |
| 2026-10-29 10:03 | Run 4 funding | four temporary non-conflict payers funded |
| 2026-10-29 10:04–10:10 | Run 4 bursts | 64/64 total transactions landed |
| 2026-10-29 10:10 | Run 4 collection and sweep | 2,600,984 lamports returned from four accounts; 20,000 lamports sweep fees |

## Cost

| Run | Probe/funding/sweep fees | Rent lost | Net cost |
| --- | ---: | ---: | ---: |
| 3 (`20261029T094113Z`) | 420,372 | 7,802,952 | 8,223,324 lamports |
| 4 (`20261029T100356Z`) | 340,228 | 0 (2,600,984 temporary rent swept back) | 340,228 lamports |
| **Total** | **760,600** | **7,802,952** | **8,563,552 lamports (0.008563552 SOL)** |

## What went wrong

- Runs 1–2 landed zero transactions. The failure chain included a missing QUIC
  client certificate, slot polling too slow for 200 ms slots, treating the
  WebSocket subscription acknowledgement as a slot notification, retaining a
  stale leader, and public-RPC HTTP 429 throttling.
- Run 3 still received four funding `sendTransaction` HTTP 429s before TPU
  fanout succeeded. All planned transactions eventually landed.
- Run 3 sent every probe twice over TPU. Deduplication produced one landed
  signature, so the copies do not increase sample size and may alter delivery
  probability relative to run 4's one-copy path.
- Run 3 stranded 7,802,952 lamports in rent-funded probe accounts. Those keys
  were unrecoverable. Run 4 added a sweep and returned all 2,600,984 lamports
  of temporary rent.
- The `--collect` flag was ignored by the pipeline. Collection still occurred
  in these archived runs, but the CLI behavior did not match the requested
  control.
- The durable ledger undercounted actual cost during reconciliation: run 3
  logged 360,310 landed lamports although `block_observations` sum to 420,372.
  The cost table uses observed transaction fees plus the known rent loss, not
  that ledger figure.
- Raw `getBlock` bodies are absent by design. `block_context` verifies
  signatures, block indexes, transaction counts, and gaps, but strips account
  keys. Foreign writable-account contamination therefore cannot be tested.
- No planned probe missed, no observed probe landed under a different leader,
  and no ladder had fewer landed rungs than sent. The only underpowered
  ladders were run 4's intentionally shortened four-rung non-conflict tests.
- The tempting statement “HarmonicFiredancer is fee ordered” did not survive
  the pre-registered cross-run rule. Likewise, run 4's perfect four-rung
  Firedancer non-conflict order remains `inconclusive`.

## What this does not separate

| Limit | Observed here? | Evidence |
| --- | --- | --- |
| Non-conflict scheduler rank vs worker completion/ledger order | yes | non-conflict patterns differ by client, but the stage responsible is unobserved |
| Greedy vs prio-graph from a conflict ladder | yes | both can produce the four repeated strict conflict results |
| Hidden scheduler flags or private patches | yes | version strings do not expose them |
| Path vs scheduler | yes | one sender; run 3 used duplicate copies, run 4 one copy |
| Admission and non-landing | no for probes in runs 3–4 | 132/132 probes landed |
| Jito bundle behavior | not measured | ordinary TPU transactions only; no bundles or tips sent |
| Full priority formula | not measured | fee was the only varied term |
| Geography | yes | one vantage point; region absent from archive |
| Pre-Alpenglow comparison | no, by construction | all slots ≥ 462,834,368 |
| Vote ordering | not measured | votes remain in absolute block indexes but not correlations |
| Quiet vs busy blocks as treatment | no | block size measured, not assigned |
| Foreign writable-account contamination | unknown | no raw blocks/account keys |

## Conclusions

- **Agave 4.3.2:** conflict order was strictly fee-first in both six-rung
  slots. Run 3 non-conflict order was strictly arrival-first; run 4 leaned the
  same way but had only four rungs. This pair is consistent with priority among
  conflicts without a global fee sort, but does not identify greedy scheduling.
- **Firedancer 26.10.4:** conflict order was strictly fee-first twice.
  Non-conflict was also perfectly fee-first in both runs, but only run 3 meets
  `n ≥ 5`. This is consistent with a total-priority-like ledger order in this
  sample; `schedule_strategy` is unknown.
- **JitoLabs 4.3.2:** conflict order was strictly fee-first twice.
  Non-conflict had `no_rule_detected` in run 3 and was underpowered in run 4.
  No bundle or tip path was tested.
- **AgaveBam 4.4.0-rc.1:** conflict order was strictly fee-first twice.
  Non-conflict had `no_rule_detected` in the qualifying run.
- **HarmonicAgave 4.3.2:** neither conflict slot passed the fee or arrival rule;
  both are `no_rule_detected`. Its one qualifying non-conflict slot was also
  `no_rule_detected`.
- **HarmonicFiredancer 26.10.4:** conflict results were mixed across slots
  (`priority`, then `no_rule_detected`). Its one qualifying non-conflict slot
  was `no_rule_detected`.

The study has one slot per validator per ladder per run, only two runs, and
only one qualifying non-conflict slot per validator because run 4 used four
rungs. Exact p-values describe each observed permutation; no
multiple-comparison correction was applied across 24 ladders. Client labels
are observational labels, not randomized treatments, and the results do not
prove which binary, private patch, or runtime flag produced a block.
