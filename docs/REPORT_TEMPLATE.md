# Report template

Copy this file to the submission report after a live mainnet run. Leave the section headings in place. Delete the angle-bracket prompts and replace them with measured values. Do not invent rows. A cell with no measurement stays blank and the "What went wrong" section says why.

The decision rule, the sign of tau, and the words `strict_priority`, `priority`, `arrival`, `no_rule_detected`, `inconclusive`, `mixed_across_slots`, and `contaminated` are defined in [METHODOLOGY.md](METHODOLOGY.md). Do not relabel a slot because the story reads better.

Tau-b is Kendall tau-b between `priority_fee_lamports` and `block_index`, with index 0 first in the block. A priority rule pushes tau-b toward −1. With low-fee-first transmission (`SEND_ORDER=ascending`) a FIFO arrival rule pushes tau-b toward +1, and a last-in-first-out drain is indistinguishable from a priority rule. With `SEND_ORDER=shuffled`, read arrival from `tau_send_b` instead (see METHODOLOGY section 9.3.1). `priority_pairwise` is the fraction of pairs in which the higher fee has the smaller index, and it runs from 0 to 1.

## Study header

| Field | Value |
| --- | --- |
| Report written (UTC date) | |
| Git commit measured | |
| Network | mainnet |
| Alpenglow cutoff | slots ≥ 459,653,000 (consensus from 21 October 2026) |
| Earliest block time in this report (UTC) | |
| Latest block time in this report (UTC) | |
| Epochs touched | |
| `MODE` | live |
| Spend cap | 0.05 SOL |
| Lamports signed | |
| Lamports observed landed (sum of `meta.fee` for our signatures) | |
| Rent principal moved to probe payers, and lamports swept back | |
| RPC used | endpoint host only, no API key |
| Sending host | city or region, one vantage point |
| Burst offset before the target window | manifest value |
| Compute-unit limit, both ladders | |
| Fee rungs (lamports) | |
| Transmission order (`SEND_ORDER`, seed) | |
| Ladders (`LADDERS`) and `TPU_COPIES` | |
| `getAgGenesisCert` block slot, if the RPC returned one | |

Every slot cited below was read back with `getBlock` and is ≥ 459,653,000. Devnet and localnet runs are not in this report.

## Per-validator results

One row per identity that was selected. Identities that were selected and never reached a window stay in the table with `slots measured` = 0 and call `inconclusive`.

Headline calls use only non-contaminated slots. Inclusive calls use every slot that was not `inconclusive`.

| Identity | Client (`software_client`) | Version (`software_version`) | Stake (SOL) | Slots measured | Tx sent | Tx landed | Conflict call (headline) | Conflict tau-b (median slot) | Conflict `priority_pairwise` (median) | Non-conflict call (headline) | Non-conflict tau-b (median) | Inclusive conflict call | Confidence | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | | | | | | |

Client and version are the snapshot taken at selection, plus any disagreement with `getClusterNodes` called out in Notes. Stake is active stake at that same snapshot, not a later figure. Median tau is descriptive. The call comes from the rollup rule, not from the median.

Confidence is one of `high`, `limited`, `none`:

- `high`: at least two non-contaminated slots on that ladder, each with `n ≥ 5`, and the same call.
- `limited`: exactly one such slot, or `n` only barely 5.
- `none`: headline call is `inconclusive`.

`high` means the ranking repeated. It does not mean the operator's hidden flags were observed.

### Per-slot detail

Repeat this table per identity. One row per ladder per slot.

| UTC date | UTC time (send of first rung) | Slot | Leader identity (block signer) | Ladder | n landed | n sent | tau-b | priority_pairwise | send_pairwise | p_perm | Contaminated | Non-vote tx in block | Call |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | conflict or nonconflict | | | | | | | yes/no | | |

Signatures for that slot, in block order:

| block_index | signature | fee_lamports | cu_price | send_index | send_unix_ms |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

A judge checks a row by calling `getBlock` on the slot, finding the signature, and confirming the index. The raw response used here is `data/runs/<run>/blocks/<slot>.json` from the collector, or `<slot>.json.gz` from `python -m analysis.fetch_blocks`. `analysis/output/EVIDENCE.md` already lists each ladder's signatures in block order with explorer links; link to its anchor rather than copying it.

## Measurement log

Every mainnet action, in order, including dry-runs that contacted mainnet and live attempts that failed. Date is the UTC calendar date of the action.

| UTC date | UTC time | Action | Slot or window | Identity | Result |
| --- | --- | --- | --- | --- | --- |
| | | select validators | | | clients and versions recorded |
| | | dry-run plan | | | projected lamports |
| | | live burst | | | signatures, or the error |
| | | getBlock | | | indexed, skipped, or RPC error |

If a day produced no successful landing, the day is still listed.

## What went wrong

Write this section after the tables, from the log, not from memory of the plan. Keep an item even when the fix was "accept the miss."

Use these prompts. Delete a prompt only by replacing it with the concrete event, or with "None observed" when the log was actually checked.

- Windows missed, skipped slots, and signatures that landed on a different leader than the one targeted.
- QUIC connection failures, throttling, and ladders with `n < 5`.
- Priority-floor or busy-block non-landings, and whether the fee was left unchanged on purpose.
- Version-string disagreements between validators.app and `getClusterNodes`.
- Contaminated conflict slots: which foreign signature touched our account.
- Cap halts, retries, and any signed transaction that was not in the original plan.
- RPC errors, `getBlock` nulls, and any index in the log that disagreed with the stored block body.
- Fewer than six measured identities, or fewer than three client or fork versions. State the gap directly.
- Anything that almost became a sentence in the conclusions and did not survive the decision rule.

## What this does not separate

Copy the findings that this particular run actually entangled, on top of the standing list in [METHODOLOGY.md](METHODOLOGY.md#what-this-does-not-separate). The standing list still applies when an item below says "not observed."

| Limit | Observed in this run? | Evidence |
| --- | --- | --- |
| Non-conflict ledger order confounds scheduler rank with worker completion order | | |
| Conflict ladder alone cannot tell greedy from prio-graph | | |
| `schedule_strategy` and `scheduler-bindings` are not in the version string | | |
| Non-landing mixes path, priority floor, and skipped slots | | |
| Foreign transactions may be Jito bundles; we did not send any | | |
| Fee was the only varied term in `reward / cost` | | |
| One sending host | | |
| No pre-Alpenglow rows, by construction | yes | cutoff slot 459,653,000 |
| Vote transactions not in the correlation | | |
| Quiet versus busy blocks were not an assigned treatment | | |

## Conclusions

Answer, per client or fork that actually entered the table:

1. Conflict-ladder call, and the strictness (tau-b and `priority_pairwise`).
2. Non-conflict-ladder call.
3. Whether that pair matches the reading guide in the methodology (greedy-like, total-sort-like, arrival, null, or mixed). Say "consistent with." The blocks do not identify a binary or a flag.
4. Sample size and confidence.

### Null results

If a ladder's call is `no_rule_detected`, use language of this shape and then the numbers:

> On the conflict ladder for `<identity>` (`<client>` `<version>`), `<n>` landed probes in slot `<slot>` on `<UTC date>` produced tau-b = `<tau>` (`priority_pairwise` = `<p>`, permutation p = `<p_perm>`). The pre-registered rule labels that ranking `no_rule_detected`. The ranking is consistent with noise at this sample size. It does not prove the leader runs no scheduler. It shows that neither a fee order nor the reversed send order is visible on these probes, on this path, in these slots.

If the call is `inconclusive`, say the sample was underpowered and do not borrow a rule from a different validator with the same client label.

If the call is `mixed_across_slots`, quote the disagreeing slots and their dates instead of averaging them.

### Scope reminder

Jito bundles and tips were not sent. Measurements before slot 459,653,000 were not used. The spend cap held at `<lamports signed>` of 50,000,000.
