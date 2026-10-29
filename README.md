# Inside the block: how Solana validators order transactions

Measurement project for the Superteam Earn bounty [Inside the Block: How Solana Validators Order Transactions](https://earn.superteam.fun/listing/inside-the-block-how-solana-validators-order-transactions). For at least six mainnet validators spanning at least three client or fork versions, the experiment asks one question:

**By what rule does this leader order transactions inside the block it produces, how strictly does that rule hold, and is there a rule at all?**

The primary evidence is transactions this project sends into that leader's window. Other transactions in the same block are supplementary. Jito bundles and tips are out of scope. Every measurement used in the report is from Solana mainnet after the Alpenglow consensus handoff (slot 459,653,000, 21 October 2026). Localnet and devnet exist only so the pipeline can be built without spending SOL, and those runs are excluded from the report.

This repository's design and write-up live in `README.md` and `docs/`. The sending pipeline, `docker-compose.yml`, and `data/` are a separate component. Where this document names a flag, service, or path that the pipeline owns, it is marked `<!-- TODO: sync with measure/ -->`.

## Why this design

Agave's open-source scheduler does **not** publish a pure sort by compute-unit price. Since Agave v2.3 the default block-production method is `central-scheduler-greedy`. That scheduler pops a priority queue and schedules a transaction when its account locks can be taken immediately. The priority score is the leader's fee reward divided by the cost-model cost. The older prio-graph scheduler, which chains conflicting transactions, is no longer a supported `--block-production-method` as of Agave 4.1. Firedancer's pack tile ranks by reward over estimated compute and then paces block fill according to a `schedule_strategy` operators set themselves. Jito-Solana adds a separate bundle stage on top of a fork of this banking path.

A block index is the order transactions were recorded into the ledger. That order equals the scheduler's rank only under conditions the scheduler actually guarantees. The experiment is built around those conditions:

- A **conflict ladder** (every probe transaction writes the same accounts, only the fee changes, and they are sent in reverse priority order) tests the rule the scheduler claims to enforce: among transactions that conflict, higher priority runs first. On this ladder, "sorted by priority" and "sorted by send order" predict opposite rankings.
- A **non-conflict ladder** (disjoint writable accounts, same fee shape) tests whether ledger order is also a global priority sort. Agave's greedy scheduler does not promise that. A pack-style scheduler might.
- Probes are sent in one burst, aimed at the leader's own TPU, so the ladder is co-present in the buffer before the leader starts draining it. The lowest fee is transmitted first and the highest fee last. Kendall tau-b between fee and block index is then negative when the block followed the fee (higher fee, earlier index) and positive when the block followed send order.
- The call for each slot is pre-registered in [docs/METHODOLOGY.md](docs/METHODOLOGY.md). `strict_priority` requires tau-b = −1. A weaker `priority` call requires tau-b ≤ −0.6 and a permutation p-value ≤ 0.05. "No rule detected" and "inconclusive" are the required labels when the ranking does not clear that bar.

The full treatment of each judging consideration is below and, in more detail, in the methodology.

## Quickstart

Dry-run is the default. `docker compose up` reads the leader schedule, selects validators, builds and signs the ladders, checks the spend cap, and writes the plan under `data/`. It does not submit transactions.

<!-- TODO: sync with measure/ -->

```bash
docker compose up --build
```

<!-- TODO: sync with measure/ -->

Live sending is the same command with an explicit mode and a funded key mounted from the host. The process refuses to start a live run without `MODE=live`, and it refuses any cap above 0.05 SOL.

<!-- TODO: sync with measure/ -->

```bash
MODE=live \
SPEND_CAP_SOL=0.05 \
KEYPAIR_PATH=/absolute/path/to/id.json \
RPC_URL="https://your-mainnet-rpc" \
docker compose up --build
```

<!-- TODO: sync with measure/ -->

### Keys and environment

| Name | Required | Role |
| --- | --- | --- |
| `MODE` | no | `dry-run` (default) or `live`. Anything absent or unrecognised stays on dry-run. |
| `SPEND_CAP_SOL` | no | Default and hard maximum `0.05`. The process counts base fee plus priority fee of every signed transaction, persists the running total under `data/`, and will not sign a transaction that would cross the cap. A value above `0.05` is a startup error. |
| `KEYPAIR_PATH` | live only | Host path to the payer keypair. Compose mounts it read-only at `/keys/id.json` inside the container. The file is never copied into the image or the repo. Dry-run generates an ephemeral key if this path is unset. |
| `RPC_URL` | yes, for a real schedule | Mainnet RPC used for `getLeaderSchedule`, `getClusterNodes`, `getEpochInfo`, `getBlock`, and (when the RPC exposes it) `getAgGenesisCert`. A URL that embeds an API key stays in the environment, not in the repo. |
| `VALIDATORS_APP_TOKEN` | no | Optional token for `validators.app`, which supplies `software_client`, `software_version`, and `active_stake`. Without it the run falls back to RPC version strings and stake from `getVoteAccounts`, and records the weaker classification. |

<!-- TODO: sync with measure/ -->

Assumed compose shape, owned by the pipeline:

<!-- TODO: sync with measure/ -->

- Service name `measure`, build context `./measure`.
- Volume `${KEYPAIR_PATH}:/keys/id.json:ro` when `KEYPAIR_PATH` is set.
- Volume `./data:/data` for the run manifest, JSONL attempt log, raw `getBlock` bodies, and the spend ledger.
- No key, RPC credential, or validators.app token in a `COPY`, a committed `.env`, or a log line.

Do not commit `id.json`, `.env`, or `data/`. The pipeline's ignore rules have to enforce that; this document cannot by itself.

### What you should see

Dry-run prints the selected identities, the client label and version recorded for each, the upcoming leader slots, the ladder (compute-unit price, fee in lamports, send index), and the projected spend. Projected spend for the default ladders is far below 0.05 SOL; the cap is what stops a bug or a retry loop.

<!-- TODO: sync with measure/ -->

Live mode adds a signature per submitted transaction and, after the window, the slot and block index of each signature that landed. Those fields are the ones a judge re-checks with `getBlock`.

## How the design handles the required considerations

The methodology is the source of truth for definitions, formulas, and the pre-registered decision rule. This section is the map the bounty asks the README to carry.

### Leader schedule and leader types

The leader schedule is stake-weighted, recomputed once per epoch, and assigns four consecutive slots to each selected leader. At a 200 ms slot time that window is about 800 ms of wall clock. The run calls `getLeaderSchedule`, converts relative indexes with the epoch start slot, and submits only into windows whose identity is the validator under test.

"Leader type" here is the tuple recorded at selection time: identity, `software_client`, `software_version`, active stake, the validators.app `jito` flag, and whether the node is delinquent. The six validators must cover at least three distinct client or fork versions. Vanilla Agave, Jito-Solana, and Firedancer are different types. So are labels such as Harmonic or BAM when `software_client` says so. Stake is recorded because a large leader and a small one differ in hardware, order flow, and how much unstaked QUIC capacity they have, which changes whether our transactions arrive, not necessarily how they are ordered once buffered.

A transaction that lands in some other leader's slot is logged and excluded from that validator's rule call. Skipped slots are logged and are not treated as empty blocks.

### Path from this process to the executing leader

The planned path is a direct QUIC connection to the target leader's advertised TPU (`getClusterNodes`), opened before the burst. The Agave TPU buffers packets when the node is close to becoming leader, then the banking stage drains that buffer. Sigverify can drop packets on a published priority floor before they ever reach the scheduler. On a Jito validator the gossip TPU address may be a relayer rather than the validator process itself.

The run records the endpoint it dialed, the send timestamp, and the slot it was aiming at. It does not use a public RPC as the submit path: an RPC is an extra hop that can delay, retry, or reorder, and non-vote forwarding is not guaranteed on the way to the next leader. Landing rate is reported separately from order. A ladder that never lands supports an admission or path conclusion, not an ordering conclusion.

### Interaction of our own transactions

The conflict ladder shares one fee payer and one recipient, so the transactions lock the same writable accounts and the scheduler has to serialize them. The non-conflict ladder uses distinct payers and recipients, so they do not lock each other. Both ladders freeze every other byte that affects cost: one signature, the same compute-unit limit, a one-lamport transfer. Only the priority fee changes. That isolates the fee term in `priority = reward / cost`. The two ladders answer different questions and are never pooled into one tau.

### Competition inside the block

Foreign transactions can sit between ours, take compute budget, or lock an account we also lock. Kendall tau-b is computed on **our** landed signatures only, so a foreign transaction placed between two probes does not by itself change the statistic. The raw block is still saved. Each attempt records the gap to the next probe, the number of non-vote transactions in the slot, and whether any foreign transaction wrote an account on our conflict ladder. That last case flags the slot `contaminated`. The headline validator call drops contaminated slots. The report also shows the call with them included, so competition that actually reorders the ladder stays visible.

### Send order

Each ladder is submitted in a single burst, lowest fee first and highest fee last. Block index 0 is the first transaction in the block. Tau-b between fee and block index is −1 when every higher fee sits earlier, and +1 when the block reproduces send order. Fee and send index increase together, so the two taus share a sign. They are reported side by side and are not treated as independent evidence. The permutation and the send index of every signature are in the raw log.

### Noise versus a rule

Small ladders move tau a lot when a single pair flips. The decision rule is fixed before looking at mainnet results: a slot with fewer than five landed probes is inconclusive; `strict_priority` requires tau-b = −1; `priority` requires tau-b ≤ −0.6, `priority_pairwise` > 0.5, and a one-sided permutation p-value ≤ 0.05; the same thresholds with the sign flipped are `arrival`. Everything else in a powered slot is `no_rule_detected`. Validator-level language follows the slots, including disagreement across slots. Averaging taus is not the call.

## What this does not separate

These are limits of the design, not bugs to be papered over. Detail and citations are in [docs/METHODOLOGY.md](docs/METHODOLOGY.md#what-this-does-not-separate).

- Scheduler pop order and ledger record order, on the non-conflict ladder. Worker threads can finish in a different order than the scheduler queued them.
- Greedy versus prio-graph, on the conflict ladder alone. Both should serialize a pure conflict set by priority. The non-conflict ladder is what can tell them apart, and only as a hypothesis about ledger order.
- Operator flags the version string does not carry. Agave `scheduler-bindings` and Firedancer `schedule_strategy` (`perf`, `balanced`, or the deprecated `revenue`) are invisible. `software_client` is the stratification we actually have.
- Ingress admission and scheduling. The sigverify priority floor, unstaked QUIC limits, and a Jito relayer can drop or delay the ladder before the scheduler runs. Non-landing is not a sample of the ordering rule.
- Bundle insertion and the ordinary scheduler. We never send a bundle or a tip. A Jito block can still contain bundles, which hold account locks as a group. We do not claim to know which foreign transactions arrived as a bundle.
- The full reward/cost surface. The ladders vary the priority fee and hold the message shape fixed. They do not test whether a different compute limit or a different account set would reorder two transactions with the same fee.
- One sender, one network path. Leaders far from this host see the burst later in an 800 ms window. The design does not multi-home the sender.
- Alpenglow versus the scheduler. Votor finalizes blocks; it is not the ordering algorithm. Rotor was not part of the 21 October 2026 activation. There is no before/after comparison. Pre-handoff slots are discarded, not contrasted.

## Report and raw data

Copy [docs/REPORT_TEMPLATE.md](docs/REPORT_TEMPLATE.md) when a live run exists. Every measurement row needs a UTC date, and the report needs a "What went wrong" section even when the list is short. Raw `getBlock` bodies and the signature/slot log stay under `data/` so a judge can repeat the lookup.

<!-- TODO: sync with measure/ -->

The submission checklist maps each bounty requirement and each judging weight onto a file: [docs/SUBMISSION_CHECKLIST.md](docs/SUBMISSION_CHECKLIST.md).

## Doc map

| File | Contents |
| --- | --- |
| `README.md` | Question, quickstart, the six considerations, limits of the design |
| `docs/METHODOLOGY.md` | Scheduler background with sources, experimental design, decision rule |
| `docs/REPORT_TEMPLATE.md` | Per-validator table, measurement log, what went wrong, null results |
| `docs/SUBMISSION_CHECKLIST.md` | Requirement and judging-criterion coverage |

## Sources, short list

The methodology cites these in place. The load-bearing ones are:

- Agave priority formula, `P = R / (1 + C)` with a `1_000_000` multiplier: [transaction_priority.rs](https://github.com/anza-xyz/agave/blob/master/core/src/transaction_priority.rs)
- Greedy scheduler ("schedule in priority order, scheduling anything that can be immediately scheduled"): [greedy_scheduler.rs on v4.4.0](https://github.com/anza-xyz/agave/blob/v4.4.0/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs)
- Default and removal of the strict scheduler: [Agave v4.4 CHANGELOG](https://github.com/anza-xyz/agave/blob/v4.4/CHANGELOG.md) (v2.3.0 makes `central-scheduler-greedy` the default; v4.1.0 drops `--block-production-method central-scheduler`)
- Firedancer pack, priority `reward/compute`: [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c), [pack configuration](https://docs.firedancer.io/guide/configuring.html)
- Frankendancer support ended at the Alpenglow mainnet activation: [Firedancer FAQ](https://docs.firedancer.io/guide/faq.html)
- Jito bundles are a separate atomic path: [Low Latency Transaction Send](https://docs.jito.wtf/lowlatencytxnsend/), [bundle_consumer.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_consumer.rs)
- Alpenglow consensus from slot 459,653,000 on 21 October 2026: [Solana Alpenglow upgrade](https://solana.com/upgrades/alpenglow)
- 200 ms slots from epoch 1053 on 9 October 2026, four-slot leader windows unchanged: [Reduced slot times](https://solana.com/upgrades/reduced-slot-times)
