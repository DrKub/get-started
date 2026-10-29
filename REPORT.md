# Inside the Block: how Solana validators order transactions

Report for the Superteam Ukraine bounty [Inside the Block](https://earn.superteam.fun/listing/inside-the-block-how-solana-validators-order-transactions). Every number here is produced by `python -m analysis && python -m viz` from the committed raw data in [`data/runs/`](data/runs). Every claim links to the signatures and slots behind it: per-slot rows link to Solscan blocks, and [`analysis/output/EVIDENCE.md`](analysis/output/EVIDENCE.md) lists all 132 probe signatures with Solscan and Explorer links.

## Summary

- **Six mainnet leaders, six client labels, two live runs, 132/132 probes landed.** The leaders were Agave 4.3.2, JitoLabs 4.3.2, AgaveBam 4.4.0-rc.1, HarmonicAgave 4.3.2, Firedancer 26.10.4, and HarmonicFiredancer 26.10.4. All slots are 462,834,368 or later, well after the Alpenglow cutoff of 459,653,000. No Jito bundles or tips were sent.
- **Conflict ladder (every probe write-locks the same account):** 164 of 180 within-ladder pairs (0.911, 95% CI 0.822–0.983) put the higher fee earlier. Agave, JitoLabs, AgaveBam, and Firedancer did this perfectly in both runs.
- **Non-conflict ladder (disjoint accounts):** 61 of 126 pairs (0.484, 95% CI 0.306–0.684). That is no better than a coin flip in aggregate. Firedancer was perfectly fee-ordered, while Agave reproduced send order.
- **The most important caveat.** In both runs the lowest fee was sent first, so fee rank and send rank are the same order. Every rung of a burst carries one millisecond timestamp, and arrival at the leader is not observed. "Fee order" and "FIFO arrival order" are therefore distinguishable only by direction (tau −1 versus +1). Any mechanism that drains a burst last-in-first-out predicts exactly what a fee sort predicts. The 0.911 is therefore also a 0.911 reverse-send-order share. See [Send order and fee order are collinear](#send-order-and-fee-order-are-collinear). The pipeline now has `--send-order shuffled` to break this; it has not been run on mainnet.
- **Small n.** There is one slot per validator, per ladder, per run, and every run-4 non-conflict ladder has four rungs, which is `inconclusive` under the pre-registered rule. Each validator therefore has at most one qualifying non-conflict slot.

## Study header

| Field | Value |
| --- | --- |
| Report written (UTC date) | 2026-10-29 |
| Network | mainnet-beta |
| Measurement dates (UTC) | 2026-10-29 09:42:01–09:44:26 (run 3), 10:04:41–10:10:04 (run 4), from `blockTime` |
| Slots | 462,834,151–462,835,057 (run 3), 462,840,537–462,842,433 (run 4, sweep included); epoch 1071 |
| Alpenglow cutoff | slot ≥ 459,653,000; `getAgGenesisCert` returned slot 459,652,999 in both manifests |
| Code measured | run 3 at `9d482c8`, run 4 at `164768b` (`manifest.json` → `git_sha`) |
| Run directories | [`data/runs/20261029T094113Z`](data/runs/20261029T094113Z) (run 3), [`data/runs/20261029T100356Z`](data/runs/20261029T100356Z) (run 4) |
| `MODE` | `live` (both) |
| Spend cap | 0.05 SOL hard maximum in code; run caps were 0.011 SOL (run 3) and 0.0035 SOL (run 4) |
| Funder (public key) | [`DK9sHVjV47u2trp7tVpAeCsgdaTcGyBh8WjZLUmCCRpM`](https://solscan.io/account/DK9sHVjV47u2trp7tVpAeCsgdaTcGyBh8WjZLUmCCRpM) |
| Client labels | `getClusterNodes` `clientId` (`classification_source: rpc`; no validators.app token) |
| Submit path | direct QUIC to the leader's `tpuQuic`; no RPC for probes; funding via RPC plus TPU fan-out |
| Burst timing | QUIC opened 12 slots ahead; burst written 1 slot before the target slot |
| Ladders | fees 0, 1, 2, 4, 8, 16 lamports at a 1,000 CU limit; run 4 non-conflict used 0, 1, 2, 4 |
| Send order | lowest fee first in both runs (`send_index` = fee rank) |
| TPU copies | 2 per probe in run 3 (code at `9d482c8`), 1 in run 4 |
| RPC | `mainnet.helius-rpc.com` for the runs (key not retained); `api.mainnet-beta.solana.com` for the read-only raw-block backfill |

## Design in one paragraph

Each leader gets two ladders in one four-slot window: a **conflict** ladder in the window's first slot and a **non-conflict** ladder two slots later. A ladder is six legacy transactions that differ only in priority fee. Each one is `SetComputeUnitLimit(1000)`, `SetComputeUnitPrice`, and a 1-lamport system transfer, and they carry no memo. The conflict ladder in run 4 is six **1-lamport self-transfers from the funder**, so every rung write-locks only the funder. In run 3 (older code) it was funder → one shared recipient, so every rung write-locked the funder and that recipient. The non-conflict ladder uses a **distinct ephemeral payer per rung** (a self-transfer in run 4; payer → its own recipient in run 3), so no two rungs share a writable account. The raw blocks confirm both lock patterns for every ladder (`ladder_writable_accounts` in [`ladder_statistics.csv`](analysis/output/ladder_statistics.csv)). The burst goes out over an already-open QUIC connection to the target leader's TPU, one slot before its window. The statistic is Kendall tau-b between priority fee and zero-based block index, using our landed signatures only.

## Send order and fee order are collinear

This is the main limit of runs 3 and 4, and a reader should weigh every "fee-ordered" result against it.

1. **Fee rank equals send rank.** In all 24 ladders `send_index` rises one-for-one with the priority fee: tau-b(fee, send index) = +1 (column `tau_fee_send`).
2. **Send time carries no extra information.** All rungs of a burst are written to one QUIC connection within the same millisecond. Every one of the 24 bursts has a single distinct `send_unix_ms`, and run 3 recorded no per-copy timestamps. Only the order is observed, not the spacing.
3. **Arrival at the leader is unobserved.** Each probe is its own unidirectional QUIC stream. The leader's TPU may read, verify, and buffer streams in a different order than they were written. Nothing in this data measures leader-side receive order.

What follows:

- A FIFO arrival rule predicts tau +1, and a fee-priority rule predicts tau −1. Those two can be told apart, but **only by the sign of tau**.
- Any process that reverses arrival also predicts tau −1, for example a stack-like drain, batches flushed newest-first, or a receive path that reverses stream order. **On these data "strict priority" cannot be separated from "reverse arrival".** The pooled conflict share is equally "0.911 of pairs in reverse send order".
- Agave's scheduler source documents a priority queue, and no LIFO mechanism is documented for any of these clients. That is prior knowledge, though, not evidence from this experiment, and it is not used to upgrade any call.
- A non-conflict tau near 0 cannot say whether the leader shuffled the burst or the network did.

**Fix for future runs (implemented, not run on mainnet).** `python -m measure run --send-order shuffled` (or `SEND_ORDER=shuffled`) writes each ladder in a seeded permutation, recorded in `manifest.json` (`design.send_order_seed`). It only accepts permutations with |tau-b(fee, send)| ≤ 1/3. A fee sort, FIFO arrival, and a LIFO drain then predict three clearly different block orders for the same burst. The analysis already computes tau-b against fee and against send index separately (`tau_b`, `tau_send_b`). The arrival call uses the send-index statistic, which is identical to the pre-registered rule on a lowest-fee-first ladder. `--send-order descending` is the mirror-image control. See [Proposed next run](#proposed-next-run).

## Analysis and decision rule

Tau-b is Kendall tau-b between priority fee and zero-based block index (votes included in the index). A value of −1 is perfect high-fee-first order. A value of +1 is perfect low-fee-first order, which is send order in these runs. The p-value is the exact one-sided probability in the observed direction, from enumerating all `n!` block orders. The pre-registered calls ([methodology §9.3](docs/METHODOLOGY.md#93-send-order)) require n ≥ 5. `strict_priority` needs tau = −1. `priority` needs tau ≤ −0.6, p ≤ 0.05, and fee-pair share > 0.5. `arrival` is the mirror image on send index. Everything else is `no_rule_detected`. The thresholds were not changed after seeing the data.

Pooled shares count every within-ladder pair. The 95% percentile intervals resample complete validator-run ladders (10,000 replicates, seed `20261029`), not dependent pairs. Source: [`pooled_pair_shares.csv`](analysis/output/pooled_pair_shares.csv).

| Scope | Ladder observations | Pairs | Fee-order share (95% CI) | Arrival-order share (95% CI) |
| --- | ---: | ---: | ---: | ---: |
| All | 24 | 306 | 0.735 (0.599–0.859) | 0.265 (0.141–0.401) |
| Conflict | 12 | 180 | 0.911 (0.822–0.983) | 0.089 (0.017–0.178) |
| Non-conflict | 12 | 126 | 0.484 (0.306–0.684) | 0.516 (0.316–0.694) |

In these runs "fee-order share" and "arrival-order share" are exact complements, because the two orders are reverses of each other. These pooled values are descriptive. Validators, clients, ladder lengths, and blocks are not exchangeable random samples of mainnet.

## Per-validator results

Rollups follow methodology §9.3. The headline rollup drops `inconclusive` and contaminated slots. No slot was contaminated, so the inclusive rollup is identical. Source: [`validator_rollups.csv`](analysis/output/validator_rollups.csv).

| Identity | Client / version | Stake (SOL) | Conflict call (slots) | Conflict tau-b by run | Non-conflict call (qualifying slots) | Non-conflict tau-b by run | Confidence |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| [`7QQG…wLpE`](https://solscan.io/account/7QQGNm3ptwinipDCyaCF7jY5katgmFUu1ieP2f7nwLpE) | Agave 4.3.2 | 1,875,001.953 | `strict_priority` (2) | [−1.000](analysis/output/EVIDENCE.md#20261029t094113z-agave-conflict), [−1.000](analysis/output/EVIDENCE.md#20261029t100356z-agave-conflict) | `arrival` (1; run 4 inconclusive) | [+1.000](analysis/output/EVIDENCE.md#20261029t094113z-agave-nonconflict), [+0.667](analysis/output/EVIDENCE.md#20261029t100356z-agave-nonconflict) | limited |
| [`HEL1…e2TU`](https://solscan.io/account/HEL1USMZKAL2odpNBj2oCjffnFGaYwmbGmyewGv1e2TU) | JitoLabs 4.3.2 | 16,665,444.377 | `strict_priority` (2) | [−1.000](analysis/output/EVIDENCE.md#20261029t094113z-jitolabs-conflict), [−1.000](analysis/output/EVIDENCE.md#20261029t100356z-jitolabs-conflict) | `no_rule_detected` (1) | [+0.333](analysis/output/EVIDENCE.md#20261029t094113z-jitolabs-nonconflict), [−0.333](analysis/output/EVIDENCE.md#20261029t100356z-jitolabs-nonconflict) | limited |
| [`DRpb…21hy`](https://solscan.io/account/DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy) | AgaveBam 4.4.0-rc.1 | 12,878,826.063 | `strict_priority` (2) | [−1.000](analysis/output/EVIDENCE.md#20261029t094113z-agavebam-conflict), [−1.000](analysis/output/EVIDENCE.md#20261029t100356z-agavebam-conflict) | `no_rule_detected` (1) | [−0.467](analysis/output/EVIDENCE.md#20261029t094113z-agavebam-nonconflict), [−0.333](analysis/output/EVIDENCE.md#20261029t100356z-agavebam-nonconflict) | limited |
| [`Fd7b…69Nk`](https://solscan.io/account/Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk) | HarmonicAgave 4.3.2 | 17,028,574.245 | `no_rule_detected` (2) | [−0.200](analysis/output/EVIDENCE.md#20261029t094113z-harmonicagave-conflict), [−0.333](analysis/output/EVIDENCE.md#20261029t100356z-harmonicagave-conflict) | `no_rule_detected` (1) | [+0.333](analysis/output/EVIDENCE.md#20261029t094113z-harmonicagave-nonconflict), [+0.333](analysis/output/EVIDENCE.md#20261029t100356z-harmonicagave-nonconflict) | limited |
| [`9eGr…8FoY`](https://solscan.io/account/9eGrDohdNTAo61DRHyfMuqKWXqYnA3i254Wiszxe8FoY) | Firedancer 26.10.4 | 6,646,427.910 | `strict_priority` (2) | [−1.000](analysis/output/EVIDENCE.md#20261029t094113z-firedancer-conflict), [−1.000](analysis/output/EVIDENCE.md#20261029t100356z-firedancer-conflict) | `strict_priority` (1; run 4 inconclusive) | [−1.000](analysis/output/EVIDENCE.md#20261029t094113z-firedancer-nonconflict), [−1.000](analysis/output/EVIDENCE.md#20261029t100356z-firedancer-nonconflict) | limited |
| [`JUPi…1h4b`](https://solscan.io/account/JUPiTERrZqgf1jUyR7dSkhMx4Kn2qJyekWsg3LT1h4b) | HarmonicFiredancer 26.10.4 | 9,705,355.838 | `mixed_across_slots` (2) | [−0.733](analysis/output/EVIDENCE.md#20261029t094113z-harmonicfiredancer-conflict), [−0.600](analysis/output/EVIDENCE.md#20261029t100356z-harmonicfiredancer-conflict) | `no_rule_detected` (1) | [+0.333](analysis/output/EVIDENCE.md#20261029t094113z-harmonicfiredancer-nonconflict), [0.000](analysis/output/EVIDENCE.md#20261029t100356z-harmonicfiredancer-nonconflict) | limited |

Confidence is `limited` everywhere. There are two slots per ladder at most, one qualifying non-conflict slot per validator, and fee/send collinearity (see above). Stake is `activatedStake` from `getVoteAccounts` at selection time. Only the Agave identity was outside the ten largest by stake.

![Kendall tau-b by validator, ladder and run](figures/fig2_tau_heatmap.png)

*Figure 2.* Kendall tau-b of fee against block index for every validator, ladder, and run, with the pre-registered call and n. Blue means higher fee earlier, which in these runs is also reverse send order.

### Per-slot detail

Every row is one ladder in one block. The slot links to Solscan, and "rungs" links to the six (or four) signatures in [`EVIDENCE.md`](analysis/output/EVIDENCE.md). The checks on the stored raw block [`data/runs/<run>/blocks/<slot>.json.gz`](data/runs/20261029T100356Z/blocks) are:

- the `Fee` reward recipient equals the target identity in all 24 blocks;
- every block index re-derives from the raw transaction list;
- the transaction count matches `block_context.jsonl`;
- **foreign writers** counts other transactions in that block that write any account our ladder locks (methodology "contamination"). It is 0 everywhere.

"Landed vs target" is the landed slot minus the planned slot. Two non-conflict ladders landed one slot early, still inside the same leader's window.

| Run | Client | Slot (Solscan) | Ladder | n | tau-b | Fee-pair share | Arrival-pair share | Exact p | Landed vs target | Foreign writers | Call | Evidence |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 3 | Agave | [462834648](https://solscan.io/block/462834648) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-agave-conflict) |
| 3 | Agave | [462834650](https://solscan.io/block/462834650) | non-conflict | 6 | +1.000 | 0.000 | 1.000 | 0.001389 | 0 | 0 | `arrival` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-agave-nonconflict) |
| 3 | JitoLabs | [462834404](https://solscan.io/block/462834404) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-jitolabs-conflict) |
| 3 | JitoLabs | [462834406](https://solscan.io/block/462834406) | non-conflict | 6 | +0.333 | 0.333 | 0.667 | 0.234722 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-jitolabs-nonconflict) |
| 3 | AgaveBam | [462834376](https://solscan.io/block/462834376) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-agavebam-conflict) |
| 3 | AgaveBam | [462834378](https://solscan.io/block/462834378) | non-conflict | 6 | −0.467 | 0.733 | 0.267 | 0.136111 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-agavebam-nonconflict) |
| 3 | HarmonicAgave | [462834368](https://solscan.io/block/462834368) | conflict | 6 | −0.200 | 0.600 | 0.400 | 0.359722 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-harmonicagave-conflict) |
| 3 | HarmonicAgave | [462834370](https://solscan.io/block/462834370) | non-conflict | 6 | +0.333 | 0.333 | 0.667 | 0.234722 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-harmonicagave-nonconflict) |
| 3 | Firedancer | [462835056](https://solscan.io/block/462835056) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-firedancer-conflict) |
| 3 | Firedancer | [462835057](https://solscan.io/block/462835057) | non-conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | −1 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-firedancer-nonconflict) |
| 3 | HarmonicFiredancer | [462834396](https://solscan.io/block/462834396) | conflict | 6 | −0.733 | 0.867 | 0.133 | 0.027778 | 0 | 0 | `priority` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-harmonicfiredancer-conflict) |
| 3 | HarmonicFiredancer | [462834398](https://solscan.io/block/462834398) | non-conflict | 6 | +0.333 | 0.333 | 0.667 | 0.234722 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t094113z-harmonicfiredancer-nonconflict) |
| 4 | Agave | [462842240](https://solscan.io/block/462842240) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-agave-conflict) |
| 4 | Agave | [462842242](https://solscan.io/block/462842242) | non-conflict | 4 | +0.667 | 0.167 | 0.833 | 0.166667 | 0 | 0 | `inconclusive` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-agave-nonconflict) |
| 4 | JitoLabs | [462840784](https://solscan.io/block/462840784) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-jitolabs-conflict) |
| 4 | JitoLabs | [462840786](https://solscan.io/block/462840786) | non-conflict | 4 | −0.333 | 0.667 | 0.333 | 0.375000 | 0 | 0 | `inconclusive` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-jitolabs-nonconflict) |
| 4 | AgaveBam | [462840768](https://solscan.io/block/462840768) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-agavebam-conflict) |
| 4 | AgaveBam | [462840769](https://solscan.io/block/462840769) | non-conflict | 4 | −0.333 | 0.667 | 0.333 | 0.375000 | −1 | 0 | `inconclusive` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-agavebam-nonconflict) |
| 4 | HarmonicAgave | [462840756](https://solscan.io/block/462840756) | conflict | 6 | −0.333 | 0.667 | 0.333 | 0.234722 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-harmonicagave-conflict) |
| 4 | HarmonicAgave | [462840758](https://solscan.io/block/462840758) | non-conflict | 4 | +0.333 | 0.333 | 0.667 | 0.375000 | 0 | 0 | `inconclusive` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-harmonicagave-nonconflict) |
| 4 | Firedancer | [462840780](https://solscan.io/block/462840780) | conflict | 6 | −1.000 | 1.000 | 0.000 | 0.001389 | 0 | 0 | `strict_priority` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-firedancer-conflict) |
| 4 | Firedancer | [462840782](https://solscan.io/block/462840782) | non-conflict | 4 | −1.000 | 1.000 | 0.000 | 0.041667 | 0 | 0 | `inconclusive` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-firedancer-nonconflict) |
| 4 | HarmonicFiredancer | [462840740](https://solscan.io/block/462840740) | conflict | 6 | −0.600 | 0.800 | 0.200 | 0.068056 | 0 | 0 | `no_rule_detected` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-harmonicfiredancer-conflict) |
| 4 | HarmonicFiredancer | [462840742](https://solscan.io/block/462840742) | non-conflict | 4 | 0.000 | 0.500 | 0.500 | 0.250000 | 0 | 0 | `inconclusive` | [rungs](analysis/output/EVIDENCE.md#20261029t100356z-harmonicfiredancer-nonconflict) |

Machine-readable source: [`ladder_statistics.csv`](analysis/output/ladder_statistics.csv). Every rung's signature, slot, fee, send index, block index, and explorer URL is in [`evidence_transactions.csv`](analysis/output/evidence_transactions.csv).

## Figures

All five are regenerated by `python -m viz` from `data/runs/` ([`figures/README.md`](figures/README.md)). SVG versions sit next to each PNG.

![Priority fee vs position in block, per validator and ladder](figures/fig1_slopegraph.png)

*Figure 1.* Priority fee against relative block index for each validator and ladder, one line per run. A line rising to the right means higher fees went first. That pattern is also what a reverse-arrival drain would draw.

![Where the probes sit in the block](figures/fig3_block_position.png)

*Figure 3.* Where the probes sit in the whole block (left) and among their neighbours (right).

![Send order vs position in block](figures/fig4_send_time.png)

*Figure 4.* Send index against relative block index for each burst. The x axis is order only, because all rungs in a burst share one millisecond timestamp. Since send index equals fee rank here, this figure is fig. 1 mirrored, not independent evidence.

![What the measurement cost](figures/fig5_cost_waterfall.png)

*Figure 5.* Cumulative mainnet cost: fees, run 3's stranded rent, and run 4's temporary rent that the sweep returned.

## Cross-run consistency

On the six-rung conflict ladder, five of six identities repeated the same pre-registered call. Agave, Firedancer, JitoLabs, and AgaveBam were perfectly fee-ordered (equivalently, perfectly reverse-send-ordered) in both runs. HarmonicAgave repeated `no_rule_detected`. HarmonicFiredancer moved from `priority` (p = 0.0278) to `no_rule_detected` (p = 0.0681), despite fee-order pair shares of 0.867 and 0.800. Its rollup is therefore `mixed_across_slots`, not evidence for a stable rule.

Run 3 and run 4 differ in more than time. Run 3 locked two accounts on the conflict ladder and wrote each burst twice. Run 4 locked one account and wrote each burst once. The four clients that were strict in both runs were strict under both variants.

Run 4 cannot confirm any non-conflict call because all six of its ladders have four rungs. Descriptively, Firedancer repeated perfect fee order and Agave again leaned toward send order. Those are consistency checks, not second qualifying samples. Source: [`cross_run_consistency.csv`](analysis/output/cross_run_consistency.csv).

## Position within the block

The first rung of a ladder sat between 3.6% and 89.3% of the way through its block, and the latest final rung was at 91.0%. The median first position was 20.0% overall, 12.4% for conflict ladders, and 30.6% for non-conflict ladders. The median block held 225 transactions.

Adjacent rungs usually stayed together. Between adjacent rungs the median number of foreign transactions was 0, the mean 1.13, and the maximum 18 (run 3, HarmonicAgave non-conflict). Conflict ladders had mean 1.08 and maximum 7; non-conflict ladders had mean 1.19 and maximum 18. These are covariates, not assigned treatments. Conflict ladders always went to the window's first slot and non-conflict ladders two slots later, so slot-in-window is confounded with ladder type. Source: [`block_positions.csv`](analysis/output/block_positions.csv).

## Measurement log

All times are UTC on 2026-10-29. Run 3 and run 4 rows come from `live-stderr.log` and `blockTime` in each run directory.

| UTC time | Action | Result |
| --- | --- | --- |
| before 09:17 | Run 1 (`20261029T085637Z`, not committed) | 12 of 13 groups skipped on slow slot polling; one Jito TPU closed with `0x174 peer sent no certificates`; 0 landed |
| 09:17–09:22 | Run 2 (`20261029T091750Z`, `20261029T091846Z`, not committed) | crash on the `slotSubscribe` ack; then all 12 funding txs dropped after the leader rotated during the handshake, and HTTP 429 on status reads; 0 landed |
| 09:41 | Run 3 funding | four `sendTransaction` HTTP 429; the TPU fan-out landed all 12 funding txs in slot 462,834,151 |
| 09:42:01–09:44:26 | Run 3 bursts | 72/72 probes landed in the 12 target windows; every burst written twice |
| 09:44 | Run 3 collection | observations and 40 block contexts; the 12 probe accounts' keys were not saved, so their rent could not be swept |
| 10:03–10:04 | Run 4 funding | four non-conflict payers funded in slot 462,840,537 |
| 10:04:41–10:10:04 | Run 4 bursts | 60/60 probes landed; each burst written once |
| 10:10:45 | Run 4 sweep | four accounts closed, 2,600,984 lamports returned ([1](https://solscan.io/tx/4iVa9FZjawh3F7j5J6JBCLHVNfZ4Pb7HzygDvHxTAKUMKsFcZxwZoC8zyikg91YkvQFA1HPFfPnn3XikikzXz1wk), [2](https://solscan.io/tx/B4WicopeDo99bABJkm3e6vHonojzxauAWmK1CbWKam7huaZi3xoXYsKAacxKqQBfuUozje6qNTYKsQNaCXNhJgz), [3](https://solscan.io/tx/3V1DfYv56Fs81MVs7YHauYtBSsUVxRzwsh4irxJyjkp8Th8kJALrRHtkV9VZaS8TaBdqdyCGfrcwzCBoTxdDEftL), [4](https://solscan.io/tx/vqaHC3ZWu6GaRa5niQBzfrRHeXunfpSkNhb888hcJqhxkrhtSTqx6RDfi6L8bSohNX3WGSGeksTD5jvHFp8K75c); slots 462,842,425–462,842,433) |
| after 10:30 | Read-only backfill (`python -m analysis.fetch_blocks`) | 24 raw `getBlock` bodies stored under `data/runs/*/blocks/`; no transaction sent |

## Cost

| Run | Landed fees (`meta.fee`) | Sweep fees | Rent left in probe accounts | Net cost |
| --- | ---: | ---: | ---: | ---: |
| 3 (`20261029T094113Z`) | 420,372 | 0 | 7,802,952 (12 accounts, not recoverable) | 8,223,324 lamports |
| 4 (`20261029T100356Z`) | 320,228 | 20,000 | 0 (2,600,984 swept back) | 340,228 lamports |
| **Total** | **740,600** | **20,000** | **7,802,952** | **8,563,552 lamports (0.008563552 SOL)** |

The total is 17% of the 0.05 SOL cap. The 0.05 SOL hard cap counts signed fees only (base fee plus priority fee, reserved before signing). Rent is capital, not fee, and the cap does not cover it. That is why run 3 could strand 0.0078 SOL while its fee ledger read 0.00042 SOL.

On-chain check (read-only `getMultipleAccounts`, 2026-10-29): the 12 run-3 probe accounts still hold exactly 7,802,952 lamports. Examples are [`87oi…A1Za`](https://solscan.io/account/87oictqa3Ckckg6udEkJY8iK4P68XRSXEfmAnC3sA1Za) (the conflict recipient), [`29fw…77vEo`](https://solscan.io/account/29fwv79DV8bce7EYk3e8xVbw6hGseMtGN8h1fXb77vEo), and [`6M4c…6ZWX`](https://solscan.io/account/6M4cvoXjN2CpyPKeT3Qw5zoBwxCjtB3dcW7eckzo6ZWX). The full list is the run-3 `ladder_writable_accounts`. The four run-4 payers hold 0, and each one's latest transaction is the sweep linked in the measurement log.

## What went wrong

**Design and analysis limits that affect the conclusions**

- **Fee order and send order were collinear.** Both runs sent the lowest fee first, and every rung of a burst carried one millisecond timestamp. Fee priority and reverse arrival make the same prediction, so the experiment separates fee from FIFO arrival only by the sign of tau. True arrival at the leader is unobserved. `--send-order shuffled` was added afterwards and has not been run.
- **n is small.** There is one slot per validator, per ladder, per run, and two runs. No multiple-comparison correction was applied across 24 ladders; the exact p-values describe each permutation, not a family-wise error rate.
- **Four-rung ladders cannot pass the rule.** Run 4's planner shortened the non-conflict ladder to four rungs to keep peak capital (net fees plus temporary rent) under 0.003 SOL. Four rungs can never reach n ≥ 5, so all six are `inconclusive`, even Firedancer's perfect −1.000 (p = 0.0417). This was a predictable consequence of the budget rule and should have been caught in the dry-run. With roughly 0.003 SOL left, a five-rung non-conflict ladder (five × 650,240 lamports rent) does not fit.
- **Two non-conflict ladders landed one slot before their target.** These were run 3 Firedancer (target 462,835,058, landed 462,835,057) and run 4 AgaveBam (target 462,840,770, landed 462,840,769). Both were still inside the same leader's window, which the raw block's fee recipient confirms, and both landed in a different slot from that leader's conflict ladder. They are kept. A burst sent one slot ahead can be picked up by the slot before the target, so slot-in-window is not fully controlled.
- **Run 3 sent duplicate TPU copies.** The code at `9d482c8` wrote every burst twice. Deduplication leaves one landed signature, so copies do not increase n. They may change delivery and buffer timing relative to run 4's single copy, so the two runs are not exact replicates.
- **The conflict ladder changed between runs.** Run 3 used funder → one shared recipient (two write locks). Run 4 used funder self-transfers (one write lock). Both are pure conflict chains, but this is a second difference between the runs.
- **Slot position is confounded with ladder type.** The conflict ladder always targeted the window's first slot and the non-conflict ladder the third.
- **Client labels come from `getClusterNodes` only.** No validators.app token was used, so `software_client` was not cross-checked, and operator flags (`schedule_strategy`, external schedulers) are unknown.
- **One vantage point.** There was one sending host and one network path. Its region was not recorded.

**Pipeline and operations**

- **Runs 1 and 2 landed zero transactions.** The failure chain was a missing QUIC client certificate, slot polling too slow for 200 ms slots, the `slotSubscribe` acknowledgement mistaken for a slot, funding handed to a leader that rotated out during the handshake, and public-RPC HTTP 429 on status reads. Their run directories are not committed because they contain no landed probes. The failures are described in [`measure/DESIGN_NOTES.md`](measure/DESIGN_NOTES.md).
- Run 3 still received four HTTP 429 responses on funding `sendTransaction`. The TPU fan-out landed the funding anyway.
- **Run 3 stranded 7,802,952 lamports of rent.** The code at `9d482c8` did not save the 12 probe-account keys. Commit `164768b` writes them to `DATA_DIR/keys/<experiment_id>/` (mode 0600, gitignored) and sweeps them after the run. Run 4 returned all 2,600,984 lamports.
- **`run --live --collect` ignored `--collect` at `9d482c8`.** Run 3's blocks were collected afterwards with `collect --from-sent`. Fixed in `164768b`. Compose now sets `COLLECT=1` so a Compose live run collects too.
- **The run-3 ledger undercounted fees.** The end-of-run reconcile wrote 360,310 lamports (status-based), but `meta.fee` sums to 420,372. From `164768b` the ledger reconciles to on-chain `meta.fee` (`ledger reconcile --rpc`). This report uses `meta.fee`.
- **Raw `getBlock` bodies were not saved by the runs.** Only `block_context.jsonl` was kept, without account keys, so contamination could not be tested at first. The 24 measured blocks were backfilled read-only after the fact (`transactionDetails: "accounts"`, gzipped). The raw evidence therefore comes from an RPC read at 10:3x UTC, not from the collection at measurement time. Positions agree exactly with the live collection.
- **Fig. 4 originally drew a synthetic 12 ms spacing per send index.** It now plots send index only and says so.
- The docs and pipeline drifted (README `TODO: sync` markers, methodology naming `getLeaderSchedule` and `blocks/<slot>.json`). They were reconciled in this integration.

**Not a problem, checked**

- 132/132 probes and 16/16 funding transactions landed; no probe landed under another leader, and no ladder lost a rung.
- No foreign transaction wrote any account our ladders locked (0 foreign writers in all 24 blocks).
- "HarmonicFiredancer is fee ordered" did not survive the pre-registered cross-run rule and is reported as `mixed_across_slots`.

## What this does not separate

| Limit | Hit here? | Evidence |
| --- | --- | --- |
| Fee priority vs reverse-arrival (LIFO) drain | **yes** | `tau_fee_send` = +1 in 24/24 ladders; one timestamp per burst |
| Leader receive order vs our send order | yes | one QUIC stream per probe; receive order never observed |
| Non-conflict scheduler rank vs worker completion/ledger order | yes | non-conflict patterns differ by client; the stage responsible is unobserved |
| Greedy vs prio-graph from a conflict ladder | yes | both produce the strict conflict results |
| Hidden scheduler flags or private patches | yes | labels are `clientId` + version only |
| Path vs scheduler | yes | one sender; run 3 two copies, run 4 one |
| Admission and non-landing | no, for probes | 132/132 landed |
| Jito bundle behaviour | not measured | ordinary TPU transactions only |
| Full priority formula (CU limit, accounts) | not measured | fee was the only varied term |
| Slot position in the window | partly | conflict always slot 0, non-conflict slot 2; two landed one slot early |
| Geography | yes | one vantage point |
| Pre-Alpenglow comparison | no, by construction | all slots ≥ 462,834,151 |
| Foreign writable-account contamination | **checked, none** | 0 foreign writers in 24 raw blocks |

## Proposed next run

This run has not been executed; nothing in this report comes from it.

```bash
MODE=live SEND_ORDER=shuffled LADDERS=conflict SPEND_CAP_SOL=0.0035 \
KEYPAIR_PATH=/abs/path/id.json RPC_URL=https://<keyed-rpc> docker compose up --build
```

- `SEND_ORDER=shuffled` writes each six-rung conflict ladder in a seeded permutation with |tau-b(fee, send)| ≤ 1/3, a different permutation per leader. A fee sort then predicts tau_fee = −1. FIFO arrival predicts tau_send = +1, and a LIFO drain predicts tau_send = −1. Each of those implies a tau_fee no further from 0 than 1/3. One six-rung slot can therefore reject "reverse arrival" at p = 1/720 if it shows tau_fee = −1.
- `LADDERS=conflict` drops the non-conflict ladder: no funding, no rent, nothing to sweep. The cost is 6 leaders × 6 rungs = 180,186 lamports (0.00018 SOL) of fees. A run-4-style plan with both ladders costs 340,228 lamports net, but needs about 0.0029 SOL of peak capital, almost all of the remaining budget. If its sweep failed, about 0.0026 SOL of rent would be stranded again.
- The non-conflict question needs at least five rungs (≥ 3.25 M lamports of temporary rent). It does not fit the remaining budget and should be stated as open.

## Conclusions

Read every "fee-first" below as "fee-first, or equivalently reverse-send-order-first": these runs cannot tell the two apart.

- **Agave 4.3.2:** the conflict ladder was in strict fee order in both six-rung slots. The qualifying non-conflict slot reproduced send order exactly (`arrival`), and run 4 leaned the same way with four rungs. This contrast between ladders is the one result that does not depend on the collinearity: the same burst shape gave tau −1 when the rungs conflicted and +1 when they did not. It is consistent with a scheduler that orders conflicting transactions by priority but does not sort the block globally, which is Agave's documented greedy design. It does not identify greedy over prio-graph.
- **Firedancer 26.10.4:** strict fee order on the conflict ladder twice. Non-conflict was also in perfect fee order in both runs, but only run 3 meets n ≥ 5. This is consistent with a ledger order that tracks priority even without conflicts; `schedule_strategy` is unknown.
- **JitoLabs 4.3.2** and **AgaveBam 4.4.0-rc.1:** strict fee order on the conflict ladder twice. The qualifying non-conflict slot was `no_rule_detected`. No bundle or tip path was tested.
- **HarmonicAgave 4.3.2:** neither conflict slot passed the fee or the arrival rule (`no_rule_detected` twice), and the qualifying non-conflict slot was also `no_rule_detected`. On these probes, at this n, on this path, no rule is visible. That does not prove the leader has no rule.
- **HarmonicFiredancer 26.10.4:** conflict results were mixed across slots (`priority`, then `no_rule_detected`). Its qualifying non-conflict slot was `no_rule_detected`.

Client labels are observational, not randomized treatments. The results do not prove which binary, private patch, or runtime flag produced a block.

## Reproduce

```bash
pip install -r viz/requirements.txt
python -m analysis && python -m viz        # offline, from data/runs/
python -m pytest -q measure analysis viz   # needs measure/requirements-dev.txt too
docker compose run --rm report             # the same two commands, network_mode: none
python -m analysis.fetch_blocks --force    # optional, read-only: re-download the raw blocks
```

Re-running the first command leaves `analysis/output/` and `figures/` byte-identical, and CI enforces this.
