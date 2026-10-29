# Methodology

How this project decides, for a mainnet leader, whether transactions inside its block follow a fee rule, an arrival rule, or no rule we can see. The README is the operator-facing summary. This file is the design a judge can attack.

Sources were read on 29 October 2026. Version strings on mainnet move. The pipeline snapshots the strings it actually observes; the examples below are illustrations of diversity, not a target list.

## 1. Claim boundary

The claim is about **ledger order in blocks this leader produced**, among **transactions we sent**, after those transactions had a chance to sit in the leader's buffer together.

The claim is not:

- a measurement of Jito bundle auctions or tip size
- a measurement of how the leader orders vote transactions
- a comparison of mainnet before and after Alpenglow
- a claim about a validator's private configuration flags
- a claim about transactions that never landed

Primary evidence is our signatures. For each one the raw log stores the signature, the target slot, the landed slot, and the index in the `getBlock` transaction list. Judges re-derive the index with `getBlock`. Foreign transactions are stored in the raw block and used only as covariates (gaps, contamination, block fullness).

## 2. Measurement window

All report rows come from Solana mainnet slots at or after the Alpenglow consensus start.

| Event | When | Source |
| --- | --- | --- |
| Slot time reaches 200 ms | Start of epoch 1053, 9 October 2026, about 14:35 UTC | [Reduced slot times](https://solana.com/upgrades/reduced-slot-times) |
| Alpenglow feature gate | Start of epoch 1064, 21 October 2026, about 13:36 UTC, slot 459,648,000 | [Alpenglow](https://solana.com/upgrades/alpenglow) |
| Last TowerBFT block / genesis certificate | Slot 459,652,999 | same |
| Alpenglow consensus | Slot 459,653,000 onward | same |

The first 5,000 slots of epoch 1064 still ran TowerBFT, and the migration boundary produced vote-only blocks that were then dropped ([SIMD-0384](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0384-alpenglow-migration.md), [Anza's migration notes](https://www.anza.xyz/alpenglow)). Those slots are not mixed into the sample. The cutoff is the slot number, not the calendar date: a block at 22:00 UTC on 21 October is eligible only when its slot is at least 459,653,000.

Rotor, the dissemination half of Alpenglow, was not part of this activation ([Solana's upgrade page](https://solana.com/upgrades/alpenglow) describes Rotor as a later release). Ordering inside a block remains a leader-local decision. Votor finalizes the block the leader already ordered.

If the RPC implements `getAgGenesisCert` (Agave v4.3), the run stores the certificate and checks that `block.slot` is 459,652,999. The slot cutoff still applies when the method is missing.

Devnet, localnet, and any mainnet slot below the cutoff may appear in pipeline logs. They are tagged with their network and excluded from `docs/` report tables. The report's measurement log lists every mainnet slot the live process touched, including failures.

## 3. Leader schedule

At any moment the cluster expects one leader to produce ledger entries. Validators reject entries signed by anyone else. The schedule of identities is recomputed locally, once per epoch, from stake that is already final, one epoch in advance. The generation algorithm sorts the active set by stake and samples a stake-weighted order from a seed that is predictable and not grindable ([Anza, leader rotation](https://docs.anza.xyz/consensus/leader-rotation)).

The same leader is assigned four slots in a row:

```text
NUM_CONSECUTIVE_LEADER_SLOTS = 4
```

That constant is still 4 in the `solana-leader-schedule` crate ([lib.rs](https://docs.rs/solana-leader-schedule/latest/src/solana_leader_schedule/lib.rs.html)). SIMD-0525 shortened the slot from 400 ms to 200 ms and kept four consecutive slots, so a leader's turn is about 800 ms of wall clock rather than 1.6 s ([SIMD-0525](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md), [upgrade note](https://solana.com/upgrades/reduced-slot-times)). The pipeline confirms the live schedule actually groups the target identity into runs of four, and records the grouping it saw.

The pipeline reads the schedule with `getSlotLeaders(start, limit)`, which returns block-signing identities for absolute slots, paged from the current slot forward ([RPC](https://solana.com/docs/rpc/http/getslotleaders)). It does not use `getLeaderSchedule`, whose epoch-relative indexes need the epoch start slot. The identity is the block-signing key, not the vote account. Stake from `getVoteAccounts` joins on `nodePubkey`; joining stake directly on a vote-account pubkey would attach the wrong weight. After the run, the `Fee` reward recipient in each stored block confirms which identity actually produced the slot.

A leader may only produce entries during its slots. The next leader fills ticks if the previous leader's entries are missing or invalid. Fast leader handoff, documented on the Alpenglow upgrade page, lets a leader start building before the previous slot is fully settled. A burst aimed at the edge of a window can land in the next identity's block. Those signatures are attributed to the leader who actually produced the block, which means they do not count toward the intended target.

## 4. What "priority" means in Agave

### 4.1 Fee terms

A transaction fee has two parts ([Solana fee docs](https://solana.com/docs/core/fees)):

- Base fee: `lamports_per_signature` is 5,000 lamports per signature. Half is burned and half is deposited to the leader.
- Prioritization fee: `ceil(compute_unit_price * compute_unit_limit / 1_000_000)` lamports. All of it is deposited to the leader (SIMD-0096). `compute_unit_price` is micro-lamports per compute unit, set by `ComputeBudgetInstruction::SetComputeUnitPrice`.

The same ceiling formula is in Agave [`get_prioritization_fee`](https://github.com/anza-xyz/agave/blob/v3.1.8/compute-budget/src/compute_budget_limits.rs). SIMD-0385 (transaction v1) activated on mainnet on 15 September 2026 and can express the priority fee as an absolute lamport total in the message config ([fee docs](https://solana.com/docs/core/fees)). The ladder's controlled variable is that lamport total, however the transaction format spells it. Which format the pipeline builds is an open implementation choice; the analysis ranks by the fee it actually put on the wire.

Default compute limits are easy to get wrong. The per-instruction default is 200,000 compute units, the builtin allocation default is 3,000, and the transaction maximum is 1,400,000 ([same fee page](https://solana.com/docs/core/fees)). An unset limit makes a modest compute-unit price expensive. Every probe sets an explicit compute-unit limit, and every probe in a ladder uses the same limit.

### 4.2 Scheduler score

Agave does not sort on compute-unit price directly. The function used by the banking-stage queue and by the sigverify priority floor is ([transaction_priority.rs](https://github.com/anza-xyz/agave/blob/master/core/src/transaction_priority.rs), comment unchanged in substance since at least [v3.1.8](https://github.com/anza-xyz/agave/blob/v3.1.8/core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs)):

```text
cost   = CostModel cost of the executed transaction, summed
reward = leader deposit from calculate_reward_and_burn_fee_details
P      = (reward * 1_000_000) / (cost + 1)
```

The comment in source states the intent as `P = R / (1 + C)`. The multiplier keeps integer division from collapsing small fees. The `+ 1` avoids division by zero. Cost comes from the cost model (compute-unit limit, loaded account data, signature and write-lock costs), not from a raw user-supplied denominator.

Consequences for the ladder:

- With message shape held fixed, cost is constant, reward increases one-for-one with the priority fee, and the order of `P` is the order of the priority fee.
- A one-lamport reward step changes `P` whenever `cost + 1` is at most `1_000_000`, which it is for these probes. The pipeline still computes `P` for every rung and refuses a ladder whose integer scores are not unique.
- Two transactions with the same compute-unit price and different compute limits, or different account sets, can rank in the opposite order from their prices. This experiment does not explore that surface. See [What this does not separate](#what-this-does-not-separate).

### 4.3 Greedy scheduler, the Agave default

History of the flag, from the [v4.4 changelog](https://github.com/anza-xyz/agave/blob/v4.4/CHANGELOG.md):

| Version | Behavior |
| --- | --- |
| 1.18 | `central-scheduler` becomes the default. This is the prio-graph scheduler. |
| 2.2 | `central-scheduler-greedy` is added, described as a simplified scheduler with better performance than the stricter `central-scheduler`. |
| 2.3 | `central-scheduler-greedy` becomes the default. |
| 4.1 | `--block-production-method central-scheduler` is no longer supported. Passing it warns and runs the greedy scheduler. |

The struct comment on the v4.4.0 implementation states the rule ([greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.4.0/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs)):

> Dead-simple scheduler that is efficient and will attempt to schedule in priority order, scheduling anything that can be immediately scheduled, up to the limits.

Each pass pops the priority queue. A transaction whose account locks can be taken on a worker thread that still has room is handed to that thread as `ConsumeWork`. A transaction that conflicts with locks already held is counted as `UnschedulableConflicts` and skipped for this pass, and the scan continues into lower-priority transactions. The default config caps scheduled in-flight compute at `MAX_BLOCK_UNITS / 4` and scans up to 100,000 transactions per pass. Issue [anza-xyz/agave#11466](https://github.com/anza-xyz/agave/issues/11466) describes the related batch-flush behavior when a conflicting transaction cannot take its locks.

Account locks are the usual Solana locks, taken through `ThreadAwareAccountLocks`: a writable account is exclusive, a readonly account can be shared, and a write conflicts with a read or another write of the same account. The scheduler thread is the component that sees the whole queue. Worker threads execute what they are given and report completion so the locks can be released. Andrew Fitzgerald's write-up of the central scheduler is the design document for that split ([Banking Stage and Scheduler](https://apfitzge.github.io/posts/solana-scheduler/)). Anza's v1.18 post describes the same thread taking over the work that four banking threads used to do independently ([Introducing the Central Scheduler](https://www.anza.xyz/blog/introducing-the-central-scheduler-an-optional-feature-of-agave-v1-18)).

What greedy guarantees, and what it does not:

- If several conflicting transactions are already in the buffer, the highest-priority one can take the lock. The others cannot be scheduled until it finishes. Their **relative** order should follow `P`.
- A lower-priority transaction that does **not** conflict can be scheduled while a higher-priority one is stuck on a lock. Global ledger order is not a sort by `P`.
- Anything not yet in the buffer has no rank. A high-fee transaction that arrives after a conflicting low-fee one has been recorded cannot jump backward in the block.

### 4.4 Prio-graph, the stricter predecessor

The prio-graph scheduler inserts transactions, highest priority first, into a lazily built graph. An edge points at the next-higher-priority transaction that conflicts on an account. A look-ahead window tries to keep future conflicts on the same worker thread ([apfitzge](https://apfitzge.github.io/posts/solana-scheduler/), [Helius on v1.18](https://www.helius.dev/blog/all-you-need-to-know-about-solanas-v1-18-update)). Conflicting transactions are processed in priority order. Non-conflicting transactions still run in parallel, so their commit order is still not a total priority sort.

Agave 4.x will not run this scheduler through the old flag. A fork that froze an older banking stage, or an external scheduler attached through `scheduler-bindings`, might. The changelog entries for 4.1 and 4.4 both note that connecting external schedulers must track the bindings version. Validators.app client labels such as AgaveBam and HarmonicAgave are the public hint that "Agave" in the version string can still hide a different block builder. This design does not assume those labels share greedy's rule. Their rule is whatever their blocks show. No ordering behavior is invented for them from unread source trees.

### 4.5 Priority floor

Under saturation the central scheduler publishes the priority of the lowest transaction it is keeping, and sigverify drops non-vote packets at or below that floor before signature verification ([Agave PR #12263](https://github.com/anza-xyz/agave/pull/12263), merged June 2026). Simple votes are exempt. A cheap ladder during a busy slot can disappear in sigverify. That is admission, not an ordering sample. The live process does not raise fees to punch through the floor: doing so would spend the cap on a different question. The slot stays in the log as a non-landing.

### 4.6 Where the TPU sits around the scheduler

Anza's TPU description ([validator TPU](https://docs.anza.xyz/validator/tpu)):

1. QUIC streamer. Stream and packet-rate limits scale with the sender's stake. Over the limit, the server stops the stream.
2. Sigverify. Deduplicate, load-shed, drop bad signatures. This is where the priority floor runs.
3. Banking stage. Buffer while the node is close to its leader slots, then execute against the working bank.
4. Forwarding stage. Forward to the upcoming leader. The same page says non-vote transactions are forwarded only when the node has the stake-override option enabled. Vote traffic is always forwarded.
5. Broadcast. Entries become shreds.

Stake-weighted QoS assigns a stake-weighted share of leader TPU capacity (the Solana docs source describes the stake-weighted portion as 80 percent) ([stake-weighted QoS](https://github.com/solana-foundation/solana-com/blob/main/apps/docs/content/guides/advanced/stake-weighted-qos.mdx)). This process is unstaked. It uses whatever unstaked capacity the leader left, and it can be throttled. That biases **which** of our bursts land. It does not, by itself, reorder a burst that did land and was buffered together. The report states landing rate next to every tau so the bias stays visible.

## 5. Jito-Solana

Jito-Solana is a modified Agave validator. Two paths matter, and this experiment uses only one of them.

**Ordinary transactions.** They are ingested through the TPU and scheduled by whatever banking-stage scheduler that fork builds (greedy, on current Agave). The gossip TPU address of a Jito validator is often the Jito relayer, which proxies packets and can deliver them to the validator over gRPC. A third-party architecture write-up describes a hold on the order of 200 ms so the block engine can form bundles ([Eclipse, How Jito Works](https://www.eclipse.xyz/articles/how-jito-works--on-solana-deep-dive)). That number does **not** appear in the current [Jito low-latency transaction docs](https://docs.jito.wtf/lowlatencytxnsend/). The experiment does not assume a 200 ms hold. It records which slot inside the four-slot window each signature landed in. A systematic one-slot delay on Jito identities, relative to Agave identities sent the same way, is reported as a path effect.

**Bundles.** A bundle is up to five transactions, executed in listed order, atomically, all-or-nothing, in one slot. A tip to one of the tip accounts is required for the auction. The block engine runs parallel auctions on a 50 ms tick and ranks bundle orderings by tip per compute ([Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)). Inside the validator, `BundleStage` records the bundle to the ledger only after every transaction in it has executed, and it holds the account locks for the whole bundle so banking-stage transactions cannot commit in the middle ([bundle_consumer.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_consumer.rs), [bundle_account_locker.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_account_locker.rs)).

This project does not call `sendBundle`, does not pay a tip, and does not construct bundles. Jito leaders remain in scope because their ordinary scheduler is a fork-specific question. Bundles can still occupy the same block and can lock accounts our probes use. Contamination handling is in [Competition inside the block](#competition-inside-the-block).

## 6. Firedancer and Frankendancer

Firedancer is a separate validator. Its pack tile is the scheduler: it stores verified, deduplicated, address-resolved transactions and schedules them when the node is leader ([Firedancer tuning guide](https://docs.firedancer.io/guide/tuning.html), tile list includes `pack`).

On the `main` branch of `fd_pack.c` as retrieved 29 October 2026 ([source](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c)):

- The ordering key is `rewards / compute_est`, compared with a cross-multiply so the code never divides (`COMPARE_WORSE`).
- Signature fees are scaled by `FD_PACK_TXN_FEE_BURN_PCT / 100` and then priority rewards are added. That is the same shape as Agave's leader deposit when the burn constant matches the protocol's 50 percent base-fee burn. The numeric constant lives in a header this write-up did not pin to tag `v26.10.5`; the analysis ranks by the fee we set, and uses a recomputed `P` only as a check that the rungs are distinct.
- A large account-data allocation can reduce the reward term so the transaction sorts as if it cost more compute, without changing the cost used for block limits.
- `fd_pack_schedule_next_microblock` fills votes from the `pending_votes` treap first. It attempts a bundle only when that pass scheduled no votes. Remaining microblock space is filled from ordinary transactions.

Operators choose how eagerly pack fills the block ([configuring pack](https://docs.firedancer.io/guide/configuring.html)):

| `schedule_strategy` | Behavior |
| --- | --- |
| `balanced` | Default. Fill only fast enough to finish near the end of the slot, to leave room for higher fees that arrive during the slot. |
| `perf` | Fill immediately with the highest-paying transactions already waiting. |
| `revenue` | Documented on the same page as deprecated. Wait even longer. |

The strategy is not in the version string. `balanced` is the behavior we expect if the operator left the default, and it is also the behavior that gives a pre-window burst the best chance of being co-present before fill starts. `perf` can commit whatever was buffered at the start of the slot and ignore a late rung. We cannot tell which flag was set. Both still rank the transactions they do select by reward over compute. Whether that selection order survives execution and becomes ledger order is part of what the non-conflict ladder measures, not something the source comment proves. Bank tiles execute scheduled transactions; this write-up does not claim their completion order equals treap order.

Frankendancer was Firedancer's networking and pack path grafted onto the Agave runtime. The [Firedancer FAQ](https://docs.firedancer.io/guide/faq.html) says support for Frankendancer ended when Alpenglow activated on mainnet, and that the current recommended tag at the time of that page was `v26.10.5`. The older [Firedancer guide](https://docs.firedancer.io/guide/firedancer) still describes the hybrid; the FAQ is the post-activation statement. After the cutoff this project does not go looking for Frankendancer leaders. If a live version string still identifies itself as Frankendancer, it is recorded as its own client and is not pooled with Firedancer.

The [Firedancer WebSocket reference](https://docs.firedancer.io/api/websocket.html) lists a `votor` tile and a `rotor` tile in Alpenglow mode, and describes `pack` as the transaction scheduler in both modes. That is consistent with treating Alpenglow as consensus and dissemination, and pack as the ordering algorithm, on this client too.

## 7. What a block index is

`getBlock` returns the block's transactions in ledger order. The block index used everywhere below is the zero-based position in that array, votes included. A second index, counting only non-vote transactions, is stored as a covariate. Kendall tau-b on a subset depends only on the relative order of that subset, so votes inserted between our probes do not change the tau. They do change absolute indexes, which are what a judge sees in a block explorer, so both numbers are kept.

Our probes contain no memo. Identity inside the block is the signature logged at send time.

## 8. Validator selection

Constraints from the bounty:

- at least six mainnet identities
- at least three distinct client or fork versions, identified by version string or by validators.app at measurement time
- Jito-Solana validators are allowed; contacting the block engine is not

Selection procedure:

1. Pull the mainnet validator list. With `VALIDATORS_APP_TOKEN`, use `GET /api/v1/validators/mainnet.json` and keep `account` (identity), `vote_account`, `software_client`, `software_client_id`, `software_version`, `active_stake`, `jito`, `delinquent` ([API](https://www.validators.app/api-documentation)). Without the token, use `getClusterNodes` for the raw `version` string and `getVoteAccounts` for stake, and set `software_client` from a prefix table that the run writes into its own manifest. The prefix table is data, reviewed against the versions seen that day, not a table hardcoded from this document.
2. Drop delinquents.
3. Stratify by `software_client` (or by the manifest's family label). Aim for at least one vanilla Agave, one JitoLabs, and one Firedancer-family client, then fill to six or more with further distinct labels when the epoch schedule contains them. HarmonicFiredancer, FireBAM, AgaveBam, and vanilla Agave are different strata when the API says they are. On 29 October 2026 the public validators.app mainnet table showed labels including AgaveBam, JitoLabs, HarmonicFiredancer, HarmonicAgave, and FireBAM, on version strings such as 4.3.2, 4.4.0, 26.10.4, and 26.10.5. That sentence is a snapshot of diversity. The run uses whoever is scheduled.
4. Record active stake in SOL. Include at least one identity outside the ten largest by stake when one is scheduled inside the measurement session, so the sample is not only the largest operators' order flow. If none is, the report says so under what went wrong.
5. Resolve each identity's upcoming absolute slots. A selected identity whose window never arrives during the session is listed as not measured and does not count toward the six.

**As built** (`measure/select.py`): both live runs ran without `VALIDATORS_APP_TOKEN`, so the label is `getClusterNodes` `clientId` (Agave, JitoLabs, AgaveBam, HarmonicAgave, Firedancer, HarmonicFiredancer), and `measure/versions.py` maps it to a family (Agave, Jito, Firedancer). Delinquent and label-less nodes are dropped and counted. Candidates are picked round-robin across labels, preferring identities with more upcoming windows, then earlier ones. The selector fails unless `MIN_CLIENT_FAMILIES` (default 3) families are covered. It swaps in one identity outside the ten largest by stake when one is scheduled. Five of the six selected leaders were in the top ten by stake.

Also store `getClusterNodes` version and TPU QUIC address next to the label. Disagreement is logged in the measurement log and the report. The rule call uses the identity that signed the block, not the label we hoped for.

## 9. The two ladders

Each selected leader gets two ladders, sent in different slots of its window when both fit, otherwise in the next window of the same leader. They are never mixed in one correlation.

### 9.1 Conflict ladder (primary)

- One-lamport system transfer from the funder **to itself**. The funder is the fee payer and the only writable account, so no account is created and nothing needs sweeping.
- Same `SetComputeUnitLimit` (1,000 CU) on every rung, with `SetComputeUnitPrice` chosen so the priority fee is a whole number of lamports.
- Priority fee strictly increasing, including a zero-fee rung. Default shape: `0, 1, 2, 4, 8, 16` lamports, six transactions.
- Unique integer `P` across rungs, checked before send.
- Because the funder is writable in every transaction, the rungs form one conflict chain.

Run 3 (`9d482c8`) used an older form: funder to one shared recipient (`87oictqa3Ckckg6udEkJY8iK4P68XRSXEfmAnC3sA1Za`), so its conflict rungs share two writable accounts. Run 4 uses the self-transfer. The analysis derives the lock set from each stored block (the intersection of the rungs' writable keys) and checks foreign writers against it.

This is the test of "higher priority is recorded earlier among transactions that lock the same accounts."

### 9.2 Non-conflict ladder

- A fresh fee payer per rung, generated for the run, which self-transfers one lamport. Each payer is bound to its own rung's fee whatever the send order.
- The funder pays each payer its rent-exempt minimum plus that rung's fees in a separate funding transaction before the burst. The pipeline waits for funding to land. Funding fees count against the cap; the principal is temporary capital, not spend.
- Payer keys are written to `data/keys/<run>/` (directory 0700, files 0600, gitignored). After the run, `measure sweep` (also called automatically at the end of a live run) returns each balance to the funder, less one 5,000-lamport sweep fee.
- Same limits, same fee steps, same transfer amount.

Run 3 used a payer and a separate recipient per rung (12 accounts; the first recipient also served as the conflict ladder's recipient), and did not save the probe keys, so their rent (7,802,952 lamports) could not be swept. Run 4 uses the self-transfer form and was swept to zero. The report's cost section lists the accounts.
- No shared writable account. The system program and the compute-budget program are readonly and shared, which does not create a write conflict.

This is the test of whether ledger order is a global sort by priority. Agave's greedy scheduler can show no such sort here and still order the conflict ladder by fee. That pair of outcomes is a meaningful result.

<a id="93-send-order"></a>
### 9.3 Send order and the sign of tau

In both committed runs (`--send-order ascending`, the default) transmission order is the reverse of priority order: the lowest fee goes out first, the highest fee last. `send_index` 0 is the first packet on the wire. On this ladder `send_index` rises one-for-one with `priority_fee_lamports`. Sending the high fee first would make FIFO arrival and priority the same prediction, and the slot could not separate them. Section [9.3.1](#931-collinearity-of-fee-and-send-order) explains what ascending order still cannot separate.

`block_index` 0 is the first transaction in the block. The strictness metric is Kendall tau-b between `priority_fee_lamports` and `block_index`:

| Outcome | tau-b(fee, block_index) | Why |
| --- | --- | --- |
| Perfect priority rule | −1 | Higher fee has the smaller index. The two sequences run in opposite directions. |
| Perfect arrival rule, given low-fee-first | +1 | The low fee was sent first and appears first. |
| Noise | near 0 | The ranking is neither rule. |

Because fee and `send_index` are the same order, tau-b(fee, block_index) and tau-b(send_index, block_index) are the same number. They are one statistic seen twice, not two independent covariates. The analysis records `tau_fee_send` (tau between fee and send index) for every ladder, and it is +1 in all 24. The `arrival` call is always tested against send order (`tau_send_b`), so it remains correct for a shuffled ladder.

<a id="931-collinearity-of-fee-and-send-order"></a>
#### 9.3.1 Collinearity of fee and send order

On the committed data, fee rank and send rank are one ranking, and every rung of a burst carries the same `send_unix_ms`. The leader's receive order is not observed. Consequences:

- A tau of −1 says "the block reversed our send order", which is exactly what a fee sort predicts. It is equally what a last-in-first-out drain predicts, or a receive path that reverses stream order, or batches flushed newest-first. **On these data `strict_priority` and "reverse arrival" cannot be told apart.**
- A tau of +1 separates FIFO arrival from fee priority, but only by direction.
- Network reordering inside a burst is unmeasured, so a mid-range tau mixes reordering with scheduling.

The fix is to make send order orthogonal to fee order. `--send-order shuffled` (`SEND_ORDER=shuffled`) draws, for each ladder, a seeded permutation with |tau-b(fee rank, send rank)| ≤ 1/3 (`measure/plan.py`, `send_permutation`). The seed is `SEND_ORDER_SEED`, or CRC32 of the run id, recorded in the manifest. Each non-conflict payer is bound to its rung, so the permutation reorders whole rungs. On a shuffled six-rung ladder:

| Mechanism | Predicts |
| --- | --- |
| Fee priority | tau(fee, block) = −1 |
| FIFO arrival | tau(send, block) = +1, so \|tau(fee, block)\| ≤ 1/3 |
| LIFO drain | tau(send, block) = −1, so \|tau(fee, block)\| ≤ 1/3 |

A single slot with tau(fee, block) = −1 is then incompatible with either pure arrival rule, and has exact p = 1/720 under a random permutation. `--send-order descending` is also available: it makes fee and FIFO agree, which tests LIFO directly. Neither mode has been run on mainnet. The cheapest decisive run is `SEND_ORDER=shuffled LADDERS=conflict` (no funding, no rent; 180,186 lamports of fees for six leaders).

Two pairwise fractions are easier to read than the sign:

- `priority_pairwise`: fraction of rungs pairs in which the higher fee has the strictly smaller block index. A perfect priority rule scores 1.
- `send_pairwise`: fraction of pairs in which the earlier send has the strictly smaller block index. A perfect arrival rule scores 1.

On a reversed ladder with unique fees and a total order, `send_pairwise = 1 - priority_pairwise`.

Pre-registered call for one ladder in one slot, using only our transactions that landed in that slot:

- `n` is how many of those transactions landed.
- `tau` is tau-b(fee, block_index).
- `p_perm` is the one-sided permutation probability of a tau at least as far from 0 as the one observed, in the observed direction. Exact enumeration when `n <= 7`. Otherwise 5,000 Monte Carlo draws, seed stored in the manifest.

| Condition | Call |
| --- | --- |
| `n < 5` | `inconclusive` |
| `n >= 5` and `tau == -1` | `strict_priority` |
| `n >= 5`, `tau <= -0.6`, `p_perm <= 0.05`, and `priority_pairwise > 0.5` | `priority` |
| `n >= 5`, `tau >= +0.6`, `p_perm <= 0.05`, and `send_pairwise > 0.5` | `arrival` |
| otherwise | `no_rule_detected` |

A foreign transaction that writes an account on the conflict ladder sets `contaminated` on the slot. Contamination does not replace the call above. The headline validator rollup uses only slots with `contaminated = false`. The report also gives the rollup with those slots left in, because a foreign lock that flips our pairs is evidence about the rule under competition, and hiding it would bias the headline toward quiet blocks.

Validator rollup, per ladder type. Apply the list in order to the headline set (slots that are not `inconclusive` and not `contaminated`). Run the same list on every non-inconclusive slot, contaminated included, and publish that as the inclusive rollup.

- every call is `strict_priority` → `strict_priority`
- every call is `strict_priority` or `priority`, and at least one is `priority` → `priority`
- every call is `arrival` → `arrival`
- every call is `no_rule_detected` → `no_rule_detected`
- more than one of those labels is present → `mixed_across_slots`
- the set is empty → `inconclusive`

Conflict ladder and non-conflict ladder each get their own call. The validator summary states both. A reading guide:

| Conflict call | Non-conflict call | Reading |
| --- | --- | --- |
| `strict_priority` or `priority` | `no_rule_detected` | Consistent with Agave greedy (priority among conflicts, no global ledger sort). Also consistent with a total sort too weak for this sample to see. |
| `strict_priority` or `priority` | `strict_priority` or `priority` | Ledger order tracks the fee even without conflicts. Consistent with a total priority sort. Pack is a candidate explanation, not identified. |
| `arrival` | `arrival` | Buffer was drained in arrival order. The scheduler's priority rule is not what the block shows. |
| `no_rule_detected` | `no_rule_detected` | No rule detected beyond noise. Valid result. |
| `inconclusive` | anything | Do not promote the other ladder into a validator-wide rule. |

Ties in fee are a pipeline bug (rungs must be unique). Ties in block index cannot happen. Tau-b is still the statistic so a future tie is defined.

Multiple comparison: six validators times two ladders is twelve calls. The report does not Bonferroni-adjust the pre-registered per-slot test, and it says so. The p-value is a description of how unusual the ranking is under a random permutation of that slot, not a claim of a family-wise error rate across the study.

### 9.4 Burst timing

One burst per ladder. All rungs are signed before the window. The QUIC connection to the target leader is already open. The burst is submitted at the end of the previous leader's last slot and into the start of the target window, so the packets can sit in the "close to becoming leader" buffer described in the TPU docs. Drip-feeding across the 800 ms window would make arrival order the dominant mechanism even on a priority scheduler.

How many slots before the window the socket opens, and the exact send instant, are pipeline parameters. They are written into the manifest. The same values are used for every leader in a run. Changing them mid-study is a new run.

As built: the conflict ladder targets the first slot of the leader's four-slot window (`CONFLICT_SLOT_OFFSET = 0`) and the non-conflict ladder the third (`NONCONFLICT_SLOT_OFFSET = 2`). Each burst is written when the slot clock (`slotSubscribe`) reaches `target_slot − max(1, ⌈RTT / 0.215 s⌉)`, where RTT is the QUIC handshake time to that leader (`burst_offset_slots = 1` is the floor, recorded in the manifest). Every handshake in both runs took 0.006–0.172 s (`live-stderr.log`), so every burst went out one slot before its target. Each probe goes out on its own QUIC stream to the leader's `tpuQuic` address. `TPU_COPIES` (default 1) repeats the whole burst. Run 3 used 2 copies, and run 4 used 1. Two non-conflict bursts aimed at the window's third slot landed in its second: run 3 Firedancer and run 4 AgaveBam. Both are still the intended leader's blocks and are kept. Slot position within the window is therefore confounded with ladder type and is not fully controlled.

### 9.5 Spend cap

`0.05 SOL` = 50,000,000 lamports is the default and the maximum the process will accept.

The ledger is global: `data/spend.json` (`measure/ledger.py`), shared by every run and gitignored. `SPEND_CAP_SOL` is therefore a **cumulative** cap over all runs in that file, not a per-run budget. The lowest value ever used is written to the ledger and keeps applying, so a later, larger `SPEND_CAP_SOL` does not raise it. A separate, optional `RUN_SPEND_CAP_SOL` (`--run-spend-cap-sol`) bounds one run's own net fees, including its sweep, and is checked in addition to the cumulative cap. It is not persisted. Before run 5 the ledger already held 760,600 lamports, so `SPEND_CAP_SOL=0.0005` refused the run outright. It was run with a cumulative cap of 0.0012606 SOL instead, which allowed 500,000 lamports more. Each run also writes its own entries to `data/runs/<run>/spend.json`. A batch's fees are reserved in the ledger before that batch is signed, and a reservation that would cross the cap stops the run (exit code 3). Counted:

- base fee of 5,000 lamports per signature
- priority fee of that transaction
- funding transfers that set up non-conflict payers, and sweep transactions
- any retry that signs a new transaction

Not counted: dry-run signatures, RPC queries, and the **principal** moved into non-conflict payers. That principal is temporary capital, returned by the sweep. The cap is a fee cap, not a balance cap. Run 3 shows why that distinction matters: its fee ledger read 0.00042 SOL while 0.0078 SOL of principal was stranded. The planner therefore also holds the default plan to a net-fee budget of 600,000 lamports and a peak-capital budget (fees plus temporary rent) of 3,000,000 lamports (`measure/budget.py`), shortening the non-conflict ladder to fit. Run 4's ladders were shortened to four rungs this way.

After a live run, unlanded reservations are released and the run's total is replaced by the sum of on-chain `meta.fee` from `getTransaction` (`ledger reconcile --rpc` does the same by hand). A failed transaction still paid its fee, so it is counted.

Dry-run prints the planned fees, the temporary rent, and the peak. For the default six-leader plan with both ladders, fees are about 0.0003–0.0004 SOL. The cap exists so a retry loop, a wrong compute-unit limit, or a hand-edited fee cannot drain the account.

### 9.6 Raw record

Per run directory:

```text
data/runs/<UTC timestamp>/         live runs only; committed
  manifest.json                    mode, git sha, cap, RPC host (no key), cutoff slot, design (ladders,
                                   send_order, send_order_seed, transmission order), CU limit, targets,
                                   every planned transaction
  validators.json                  identity, clientId, version, stake, TPU address
  attempts.jsonl                   one object per transmission
  block_observations.jsonl / .csv  landed slot and block index per probe, from the live collection
  block_context.jsonl              per-block transaction count and our positions
  hypothesis_agreement.json        the pipeline's own first-pass calls
  blocks/<slot>.json.gz            getBlock body (transactionDetails "accounts") per measured slot
  spend.json                       this run's ledger entries
  run.log, live-stderr.log         logs, RPC keys stripped
data/dry-runs/<UTC timestamp>/     dry-run plans; gitignored, never read by the analysis
data/keys/<run>/                   probe payer keypairs, 0600; gitignored
data/spend.json                    global fee ledger; gitignored
```

Each `attempts.jsonl` object includes the `signature`, `identity`, `ladder` (`conflict`, `nonconflict`, or `funding`), `fee_lamports`, `priority_fee_lamports`, `cu_limit`, `cu_price`, `send_index`, `send_unix_ms`, `planned_slot`, the endpoint, the submit path, and any `error`.

The collector writes the full `getBlock` response to `blocks/<slot>.json`. For runs 3 and 4 those files were not archived. The committed `blocks/<slot>.json.gz` were fetched read-only afterwards by `python -m analysis.fetch_blocks`, using gzip with a fixed header so the bytes are reproducible. The analysis re-derives every block index and the transaction count from these files and fails if either disagrees with the live observations. It also records, per ladder, the producing leader (`Fee` reward recipient) and whether it matches the target, the ladder's lock set, and the number of foreign writers. A judge can diff them against a fresh `getBlock`.

## 10. Required considerations

### Leader schedule and leader types

Handled as in [§3](#3-leader-schedule) and [§8](#8-validator-selection).

The schedule tells us **who** is allowed to order transactions in a slot. The client label tells us **which published algorithm** is the prior for that leader. The prior is only a reading guide for the two calls in [§9.3](#93-send-order). A vanilla Agave 4.x node is expected to show priority on the conflict ladder and no global fee sort on the non-conflict ladder. A Firedancer node is expected to rank by reward over compute, with ledger order left as an empirical question, and with `schedule_strategy` unknown. A Jito node is expected to look like its underlying scheduler on our non-bundle probes, with extra contamination risk. Any other `software_client` has no prior.

Leader type also includes stake and delinquency, because those change the chance we enter the buffer. They are covariates, not extra ordering rules.

Deliberately not done: we do not weight the tau by stake, and we do not treat a high-stake leader as more informative about the code. Each identity is one implementation. Stake weighting would answer "what does a random transaction on the network experience," which is a different question.

### Path from the backend to the executing node

Handled as a direct QUIC burst to the leader TPU that will produce the block, after the connection is warm, with every hop we can name written down:

| Hop | What we know | What we record |
| --- | --- | --- |
| This process | Send order and timestamps | `send_index`, `send_unix_ms` |
| Host network and the path to the leader | One sender, one route, RTT unknown | endpoint dialed |
| QUIC streamer | Stake-scaled limits; we are unstaked | landing, or silence |
| Relayer, if the gossip TPU is not the validator | Possible extra delay on Jito | which slot of the four the tx landed in |
| Sigverify | Dedup, priority floor, signature check | non-landing during busy slots |
| Banking buffer | Held when the node is near its slots, then drained | burst timed at the window edge; manifest stores the offset |
| Scheduler and workers | [§4](#4-what-priority-means-in-agave) and [§6](#6-firedancer-and-frankendancer) | block index |
| Broadcast and RPC | `getBlock` must observe the produced block | raw body; a null `getBlock` is a skip or a lag, retried within the run and then logged |

Deliberately not done:

- No second path (no parallel RPC submit). Two paths would double-send or would confound path with order. One path means we cannot estimate the path's contribution by a crossover.
- No stake, and no `--staked-nodes-overrides` relationship with the leader. Buying stake to clear SWQoS would change the path and the budget.
- No measurement of leader-receive time. The gap between `send_unix_ms` and execution is unobserved. Co-presence in the buffer is inferred from the design of the burst plus the sign of tau, not from a trace on the validator.

### Interaction of our own transactions

The conflict ladder **uses** interaction as the instrument. Shared writable accounts are what makes the scheduler serialize the rungs, which is the only situation where greedy's "immediately schedulable" rule lines the rungs up by priority.

The non-conflict ladder **removes** that interaction so a global sort has to come from somewhere else (selection order into the microblock or the entry, or chance).

Practical couplings that are easy to miss, and how they are handled:

- A shared fee payer is itself a write lock. The non-conflict ladder therefore uses different payers. Their funding is outside the measured slot.
- Debiting the payer changes the balance the next conflict-ladder rung observes. The payer is funded for the sum of fees and transfer amounts before the burst, so a later rung does not fail for lack of lamports. A rung that fails anyway is a landed-or-not outcome (`error` set, `block_index` null) and drops `n`.
- Identical recent blockhashes are fine. Distinct signatures are what the ledger and dedup see. The process does not re-sign the same bytes and call it a new probe.
- The two ladders do not share a slot. A conflict rung and a non-conflict rung in one block would lock or not lock depending on which payers overlapped, and the slot would no longer be one treatment.

### Competition inside the block

Foreign transactions are the rest of the queue the scheduler actually saw. They matter in three ways, and the design treats those ways differently:

1. **Inserted between our rungs, without sharing our writable accounts.** They change absolute indexes and do not change pairwise order. Tau is unchanged. The gap size is still recorded, because a reader will want to know whether we measured a quiet block or a full one.
2. **Sharing a writable account with the conflict ladder.** They join our conflict chain. A higher-fee foreign transaction can legally sit between two rungs without flipping our pairs. A foreign transaction that flips our pairs is real evidence about the rule under competition, and it stays inside that slot's tau. The slot is flagged `contaminated`. The headline validator rollup leaves those slots out, so a clean-looking ranking that was actually produced under an outside lock is not described as a lab result. The report's second rollup leaves them in, so a flip caused by competition is not deleted.
3. **Taking the compute budget or the buffer so our rungs never land.** That reduces `n`. Below 5, the slot is inconclusive. It is not evidence of disorder.

Vote transactions are excluded from "foreign competition" covariates but included in the raw block and in `block_index`.

We do not try to reconstruct the scheduler queue. Transactions that were dropped before the block are invisible. The competition we can see is the competition that won a slot in the block.

### Send order

Send order is an alternative total order, built to be the reverse of fee order, and it is fully observed (`send_index`). The sign test in [§9.3](#93-send-order) is the separation.

What "reverse" cannot fix:

- If the network reorders the UDP/QUIC burst, the order the leader received is not `send_index`. A reordered burst looks like noise, or like a weak priority signal, depending on how it was shuffled. We do not observe receive order. The honest reading of a mid-range tau is "neither pure rule is visible," which is the `no_rule_detected` call.
- If some rungs are dropped, the landed subset of a reversed ladder is still reversed relative to fee. Missingness does not create a false priority signal by itself. It does reduce `n`.
- If the leader's buffer already contained a copy of a rung (a retry), dedup keeps one. Retries are new signatures only when intentionally used, they count against the cap, and they are separate rows. The default run does not retry inside the window.

### Noise versus a rule

Noise has a specific meaning here: the ranking of our landed rungs is about as consistent with a random permutation as with either pure rule.

The pre-registered thresholds are in [§9.3](#93-send-order). They are deliberately harsh at small `n`. At `n = 5`, a single adjacent inversion no longer has tau = −1, so the strict call fails, and the weaker `priority` call needs the permutation probability. That will label some real priority schedulers as `priority` rather than `strict_priority`, and it will label thin samples `inconclusive` instead of inventing a rule. That is the intended bias. The bounty treats a supported null result as a valid conclusion.

The report shows, for every slot, `n`, tau-b, `priority_pairwise`, `p_perm`, and the call. A reader who dislikes the thresholds can recompute the call from those columns without trusting our labels. The thresholds are not refit after seeing mainnet.

Slots are not averaged into one tau. A leader that is strict in a quiet slot and arrival-ordered in a busy slot is `mixed_across_slots`.

<a id="what-this-does-not-separate"></a>
## 11. What this does not separate

1. **Scheduler queue order versus ledger commit order,** for transactions that do not conflict. Workers, bank tiles, and entry packing can finish in a different order than the queue. The conflict ladder is the one case where the lock forces a sequence. The non-conflict ladder confounds "the scheduler did not rank them" with "the scheduler ranked them and the recorder permuted them."

2. **Greedy versus prio-graph,** if we only looked at the conflict ladder. Both should put a pure conflict chain in priority order. The non-conflict ladder is the contrast, and it is still confounded with (1).

3. **Code we can read versus the binary that produced the block.** `software_version` plus `software_client` is a label. It does not include `--block-production-method`, `scheduler-bindings`, Firedancer `schedule_strategy`, or a private patch. Two validators with the same label can disagree. Disagreement is reported as `mixed_across_slots` or as different validator calls, not smoothed away.

4. **Path versus scheduler.** Non-landing mixes unstaked QUIC throttling, the priority floor, relayer delay, a skipped slot, and a burst that missed the buffer. The analysis refuses to call that a rule. It also cannot say which hop dropped the packet.

5. **Bundle policy versus the ordinary scheduler.** We do not send bundles. We cannot prove a foreign transaction was or was not inside a bundle. Lock-holding by `BundleStage` can delay our conflict chain. The contamination flag catches foreign writes of our accounts. It does not catch a bundle that reordered other accounts around us.

6. **The full priority formula.** Only the fee changes. Cost, signatures, and account locks within a ladder stay fixed. A client that sorted on compute-unit price, on raw fee, or on Agave's `P` would look the same on this ladder. A client that sorted on tip size would look like noise or arrival, because our tip is zero.

7. **Geography.** One sending host. A leader an ocean away can receive the "pre-window" burst after the slot has started. That pushes a priority scheduler toward the arrival call. We do not have a second vantage point to subtract that delay.

8. **Alpenglow versus ordering.** Every row is post-handoff. The design has no pre-Alpenglow contrast, and it does not isolate fast leader handoff from ordinary slot timing. Handoff shows up as signatures landing on the next leader, which are excluded from the intended target.

9. **Vote ordering and the priority floor's vote exemption.** Votes are in the block index and out of the correlation. We do not claim a rule for them.

10. **Quiet slots versus busy slots as a controlled factor.** We do not schedule leaders by forecasted load. Fullness is a measured covariate. If every strict ranking happens to be a quiet block and every null happens to be a full one, the report says that, and the validator call stays `mixed_across_slots` when the slot calls differ.

## 12. Analysis workflow

After the live run:

1. Drop any slot `< 459,653,000`.
2. Join attempts to blocks on signature. Recompute `block_index` from the stored `getBlock` body. A mismatch with the live log is a bug, recorded in what went wrong, and the recomputed index wins.
3. Apply the slot rule in [§9.3](#93-send-order) separately for each ladder.
4. Roll up to a validator call per ladder.
5. Fill [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md). Dates are the UTC dates of the sends, not the date the report was written.
6. Leave the raw files in `data/`. The report quotes signatures and slots; it does not replace the raw files.

Steps 1–4 are `python -m analysis` (offline, standard library), which writes `analysis/output/`. That includes `EVIDENCE.md`, where every ladder has an anchor and every rung links to Solscan and Explorer. `python -m viz` then draws `figures/`. Both are deterministic, and CI checks that rerunning them on the committed `data/runs/` leaves `git diff` empty.

Null-result language, to be used when it matches the table: "On the conflict ladder, the landed order is consistent with a random permutation of the fees we set. That supports 'no rule detected' for these slots. It does not prove the leader has no scheduler. It proves that whatever the scheduler did is not visible on these probes, at this sample size, on this path."

## 13. Pipeline decisions, as built

Each question this document originally left to `measure/` has an answer in the code. Values are from the run-4 manifest unless noted.

1. **Names.** `MODE`, `RPC_URL`, `WS_URL`, `SPEND_CAP_SOL`, `KEYPAIR_PATH`, `VALIDATORS_APP_TOKEN`, `LOOKAHEAD_SLOTS`, `MIN_LEAD_SLOTS`, `TARGET_VALIDATORS`, `MIN_CLIENT_FAMILIES`, `TPU_COPIES`, `SEND_ORDER`, `SEND_ORDER_SEED`, `LADDERS`, `COLLECT`, `DATA_DIR`. The Compose service is `measure`, and live output goes to `data/runs/<UTC>/`. The README has the table.
2. **Transaction format.** Legacy messages with `SetComputeUnitLimit` and `SetComputeUnitPrice` (`transaction_format: legacy+compute-budget`). `getBlock` uses `maxSupportedTransactionVersion: 1`.
3. **Compute-unit limit.** 1,000 CU on every rung. A devnet probe at slot 516,359,079 consumed 450 CU, and that probe is recorded in the manifest as `excluded_from_report`.
4. **QUIC.** `aioquic` 1.7.0 with ALPN `solana-tpu` and a self-signed client certificate. Connections open 12 slots ahead (`quic_warmup_slots`), and the burst goes out at `target_slot − max(1, ⌈RTT / 0.215 s⌉)`, which was one slot in both runs (section 9.4).
5. **Version strings.** Not parsed. `clientId` from `getClusterNodes` is the label (`measure/versions.py`), and version strings are recorded as-is. Neither run used validators.app.
6. **Jito endpoint.** The dialed address is the gossip `tpuQuic`, recorded per attempt (`tpu_host`, `tpu_port`). No relayer hold is assumed.
7. **Cap ledger.** Global `data/spend.json`, durable across restarts and run directories. It is reconciled to on-chain `meta.fee` after a live run (section 9.5).
8. **Ladder length.** The conflict ladder has six rungs. The non-conflict ladder is shortened first to fit the budget, and run 4's had four. Four rungs can never reach `n ≥ 5`, so those slots are `inconclusive`.
9. **Retries.** None inside the window (`retries_inside_window: false`). `TPU_COPIES` re-sends the same signed bytes; it does not create new signatures.
10. **Genesis certificate.** `getAgGenesisCert` was available and matched slot 459,652,999 in run 4. The slot cutoff is enforced regardless.
11. **Container.** Runs as non-root uid 1000. The key mount is read-only, and no secrets are in the image. Dry-run is the Compose default. Logs go to stdout, `run.log`, and `live-stderr.log`. The `report` service has no network.
12. **Send order.** `ascending` (the default, used by both runs), `descending`, or `shuffled` (section [9.3.1](#931-collinearity-of-fee-and-send-order)). Recorded in the manifest's `design`.

## 14. Source list

Retrieved 29 October 2026.

- Superteam listing: https://earn.superteam.fun/listing/inside-the-block-how-solana-validators-order-transactions
- Agave priority: https://github.com/anza-xyz/agave/blob/master/core/src/transaction_priority.rs
- Agave priority, earlier location, same formula: https://github.com/anza-xyz/agave/blob/v3.1.8/core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs
- Greedy scheduler, v4.4.0: https://github.com/anza-xyz/agave/blob/v4.4.0/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs
- Changelog (greedy default in 2.3, strict flag removed in 4.1): https://github.com/anza-xyz/agave/blob/v4.4/CHANGELOG.md
- Priority floor: https://github.com/anza-xyz/agave/pull/12263
- Greedy batch flush discussion: https://github.com/anza-xyz/agave/issues/11466
- Central scheduler design: https://apfitzge.github.io/posts/solana-scheduler/
- Anza v1.18 scheduler post: https://www.anza.xyz/blog/introducing-the-central-scheduler-an-optional-feature-of-agave-v1-18
- Helius explainer of the same release: https://www.helius.dev/blog/all-you-need-to-know-about-solanas-v1-18-update
- Leader rotation: https://docs.anza.xyz/consensus/leader-rotation
- Consecutive leader slots: https://docs.rs/solana-leader-schedule/latest/src/solana_leader_schedule/lib.rs.html
- TPU stages: https://docs.anza.xyz/validator/tpu
- Stake-weighted QoS: https://github.com/solana-foundation/solana-com/blob/main/apps/docs/content/guides/advanced/stake-weighted-qos.mdx
- Fees: https://solana.com/docs/core/fees
- Prioritization fee implementation: https://github.com/anza-xyz/agave/blob/v3.1.8/compute-budget/src/compute_budget_limits.rs
- `getSlotLeaders`: https://solana.com/docs/rpc/http/getslotleaders
- Alpenglow: https://solana.com/upgrades/alpenglow
- Alpenglow migration: https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0384-alpenglow-migration.md
- Anza Alpenglow page: https://www.anza.xyz/alpenglow
- Reduced slot times: https://solana.com/upgrades/reduced-slot-times
- SIMD-0525: https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md
- Jito low-latency / bundles: https://docs.jito.wtf/lowlatencytxnsend/
- Jito bundle consumer: https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_consumer.rs
- Jito bundle account locks: https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_account_locker.rs
- Relayer timing, third-party, not used as an assumption: https://www.eclipse.xyz/articles/how-jito-works--on-solana-deep-dive
- Firedancer FAQ (Frankendancer end of support): https://docs.firedancer.io/guide/faq.html
- Firedancer architecture note: https://docs.firedancer.io/guide/firedancer
- Pack configuration: https://docs.firedancer.io/guide/configuring.html
- Pack source: https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c
- Firedancer tiles, including pack and votor: https://docs.firedancer.io/guide/tuning.html
- validators.app API (`software_client`, `software_version`, `active_stake`): https://www.validators.app/api-documentation
