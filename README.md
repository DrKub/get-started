# Inside the block: how Solana validators order transactions

Measurement project for the Superteam Earn bounty [Inside the Block: How Solana Validators Order Transactions](https://earn.superteam.fun/listing/inside-the-block-how-solana-validators-order-transactions). For at least six mainnet validators spanning at least three client or fork versions, the experiment asks one question:

**By what rule does this leader order transactions inside the block it produces, how strictly does that rule hold, and is there a rule at all?**

The primary evidence is transactions this project sent into each leader's window. Other transactions in the same block are supplementary. Jito bundles and tips are out of scope. Every measurement in the report is from Solana mainnet after the Alpenglow consensus handoff (slot 459,653,000, 21 October 2026). The planner refuses earlier slots.

**Results:** [REPORT.md](REPORT.md). It covers two live runs, 6 validators across 6 client labels and 3 client families, 24 ladders, 132 of our own probe transactions, and 0.0086 SOL. Every claim links to signatures and slots in [analysis/output/EVIDENCE.md](analysis/output/EVIDENCE.md).

## Layout

| Path | Contents |
| --- | --- |
| `measure/` | Live pipeline: leader selection, ladder planning, signing, QUIC send, collection, fee ledger, rent sweep. [Design notes](measure/DESIGN_NOTES.md). |
| `analysis/` | Offline, standard library only. It reads `data/runs/`, computes tau-b, exact p-values, the bootstrap, raw-block checks, and writes `analysis/output/`. [README](analysis/README.md). |
| `viz/` | Offline figures from `analysis/output/` to `figures/`. |
| `data/runs/<run>/` | Committed raw evidence from the two mainnet runs (see [Raw data](#raw-data)). |
| `docs/METHODOLOGY.md` | Scheduler background, design, pre-registered decision rule, limits. |
| `docs/SUBMISSION_CHECKLIST.md` | Bounty requirements and judging weights mapped to files. |
| `REPORT.md` | Results, figures, cost, what went wrong. |

## Reproduce the report from committed data

No network, no key, no transaction:

```bash
pip install -r viz/requirements.txt        # matplotlib, numpy, pillow; analysis needs nothing
python -m analysis && python -m viz
git diff --exit-code analysis/output figures   # byte-identical to the committed outputs
```

The same thing in a container with networking disabled (`network_mode: none`):

```bash
docker compose --profile report run --rm report
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs every test, both reproductions with the diff check, an offline dry-run, and a key-material scan over the full git history.

## Run the pipeline

### Dry-run (default)

```bash
docker compose up --build
```

`MODE` defaults to `dry-run`. The container reads the cluster from mainnet RPC, selects validators, builds and signs both ladders in memory, checks the spend cap, prints the plan, and writes it under `data/dry-runs/<timestamp>/`. It submits nothing. Without a mounted key it signs with an ephemeral keypair. Dry-run output is gitignored and the analysis never reads it.

The same thing without Docker:

```bash
pip install -r measure/requirements.txt
python -m measure run                                  # dry-run against mainnet RPC (read-only)
python -m measure run --fixture-dir measure/tests/fixtures/offline --min-lead-slots 16   # fully offline
```

### Live

```bash
MODE=live \
KEYPAIR_PATH=/absolute/path/to/id.json \
RPC_URL="https://your-keyed-mainnet-rpc" \
SPEND_CAP_SOL=0.01 \
docker compose up --build
```

`MODE=live` is the same switch as `python -m measure run --live`. Live is opt-in: any other value, or none, stays on dry-run. The keypair is bind-mounted read-only at `/keys/id.json`. It is never copied into an image (`.dockerignore` excludes `.env`, `data/`, and keys), and it is never logged. RPC query strings are stripped from logs and manifests.

A live run:

1. funds one fresh payer per non-conflict rung (6 by default);
2. waits for the funding to land;
3. sends each ladder as one QUIC burst to the target leader's TPU one slot before its planned slot;
4. collects `getBlock` for the landed slots (`COLLECT=1` is the Compose default; `--collect` on the CLI);
5. reconciles the ledger to on-chain `meta.fee`;
6. sweeps the payer accounts back to the funder.

### Environment

All variables are read by `measure/config.py`. Compose passes each one through with the default shown.

| Name | Default | Role |
| --- | --- | --- |
| `MODE` | `dry-run` | `live` sends. Anything else is a dry-run. |
| `RPC_URL` | `https://api.mainnet-beta.solana.com` | JSON-RPC for `getSlotLeaders`, `getClusterNodes`, `getVoteAccounts`, `getEpochInfo`, `getLatestBlockhash`, `getSignatureStatuses`, `getTransaction`, `getBlock`. The public endpoint rate-limits collection; use a keyed URL for a live run. |
| `WS_URL` | derived from `RPC_URL` | `slotSubscribe` for the slot clock. |
| `SPEND_CAP_SOL` | `0.05` | Fee cap. Values above the hard cap of 0.05 SOL are a startup error. |
| `KEYPAIR_PATH` | unset | Host path to a Solana CLI `id.json`, mounted read-only. Required for live. |
| `VALIDATORS_APP_TOKEN` | unset | Optional. When set, validators.app `software_client` replaces RPC `clientId`. Both runs used `clientId`. |
| `LOOKAHEAD_SLOTS` | `16000` | How far ahead to scan the leader schedule. |
| `MIN_LEAD_SLOTS` | `150` | Minimum distance from now to a planned slot. Use `16` with the offline fixture. |
| `TARGET_VALIDATORS` | `6` | Leaders to select. |
| `MIN_CLIENT_FAMILIES` | `3` | Selection fails unless this many families (Agave, Jito, Firedancer) are covered. |
| `TPU_COPIES` | `1` | Copies of each burst written to the leader's TPU. Run 3 used 2; see the report. |
| `SEND_ORDER` | `ascending` | `ascending` (lowest fee first, as in both runs), `descending`, or `shuffled`. See [the collinearity limit](#the-main-limit-fee-order-and-send-order-are-collinear). |
| `SEND_ORDER_SEED` | CRC32 of the run id | Seed for `shuffled`. Recorded in the manifest. |
| `LADDERS` | `both` | `both`, or `conflict` (no payers to fund, no rent at risk). |
| `COLLECT` | `1` in Compose, off on the CLI | Fetch `getBlock` after a live send. |
| `DATA_DIR` | `data` (`/data` in Compose) | Output root. |

Copy [`.env.example`](.env.example) to `.env` to set these; `.env` is gitignored.

### Other commands

```bash
python -m measure key                                   # funder pubkey and balance; sends nothing
python -m measure ledger show                           # global fee ledger, data/spend.json
python -m measure ledger reconcile --rpc                # set the ledger to on-chain meta.fee
python -m measure ledger reset --yes
python -m measure collect --from-sent data/runs/<run>/attempts.jsonl   # getBlock for a previous live send
python -m measure sweep                                  # return every data/keys/<run>/ payer's lamports to the funder
python -m measure parse-block --file block.json --slot <slot>
python -m analysis.fetch_blocks                          # read-only: store getBlock bodies for the measured slots
```

## Design

### Probes

Every probe is a one-signature, 1,000-CU-limit transaction carrying a one-lamport self-transfer. Only the priority fee changes: 0, 1, 2, 4, 8, 16 lamports on top of the 5,000-lamport base fee. That isolates the fee term in Agave's `P = R / (1 + C)` and Firedancer's reward/compute.

- **Conflict ladder.** Each rung is a self-transfer from the funder, so every probe write-locks the same account and the scheduler must serialize them. No account is created.
- **Non-conflict ladder.** Each rung has its own fresh payer, which self-transfers, so no two probes share a writable account. Payers are funded with rent-exempt principal before the burst. Their keys are written to `data/keys/<run>/` (mode 0600, gitignored), and the lamports are swept back after the run.

Each ladder goes to one leader in one burst, aimed at that leader's QUIC TPU (`getClusterNodes`). The conflict ladder targets the first slot of the leader's four-slot window and the non-conflict ladder the third. No RPC is in the submit path. The run records the endpoint, send index, send timestamp, and target slot of every signature.

### Leader schedule and selection

`getSlotLeaders` gives the schedule from the current slot forward. Each leader is labelled with `clientId` from `getClusterNodes` (Agave, JitoLabs, AgaveBam, HarmonicAgave, Firedancer, HarmonicFiredancer). The selector picks round-robin across labels, preferring identities with more upcoming windows, then earlier ones. It stops at `TARGET_VALIDATORS` and fails unless `MIN_CLIENT_FAMILIES` families are covered. It also swaps in one identity outside the ten largest by stake when one is scheduled. The block's `Fee` reward recipient confirms afterwards that the intended leader produced the slot. It matched in all 24 measured slots.

### Statistic and decision rule

For each ladder, Kendall tau-b is computed between fee and block index over our landed probes only. A negative tau means higher fee earlier. The calls are pre-registered in [docs/METHODOLOGY.md](docs/METHODOLOGY.md):

| Call | Condition |
| --- | --- |
| `inconclusive` | fewer than 5 landed probes |
| `strict_priority` | tau-b = −1 |
| `priority` | tau-b ≤ −0.6, fee-pair share > 0.5, exact one-sided p ≤ 0.05 |
| `arrival` | the same thresholds against send order |
| `no_rule_detected` | anything else |

Pooled shares use a cluster bootstrap over whole ladders (10,000 replicates, seed 20261029). Validator rollups report `mixed_across_slots` when a validator's two runs disagree; taus are never averaged into a call.

### Competition inside the block

Tau is computed on our signatures, so a foreign transaction between two probes does not by itself change it. The analysis re-derives every block index from the stored raw block. It also counts foreign transactions that write any account our ladder locks; a ladder with any such writer would be flagged `contaminated`. The count was zero in all 24 ladders.

### The main limit: fee order and send order are collinear

Both runs sent every ladder lowest fee first, and all rungs of a burst share one millisecond send timestamp. Fee rank and send rank are therefore the same ranking. Arrival at the leader is not observed. "Ordered by fee" (tau −1) and "ordered by arrival" (tau +1) are distinguishable only by direction. Any mechanism that drains a burst last-in-first-out predicts exactly what a fee sort predicts. The report states every `strict_priority` call with this caveat.

`--send-order shuffled` (`SEND_ORDER=shuffled`) breaks the collinearity. Each ladder gets a seeded permutation whose Kendall tau against fee rank is within ±1/3. On such a ladder a fee sort, FIFO, and LIFO make three different predictions, and the analysis tests arrival against the recorded send order rather than fee order. It is implemented and tested offline, but **it has not been run on mainnet**. The report's [proposed next run](REPORT.md#proposed-next-run) is `SEND_ORDER=shuffled LADDERS=conflict`, which costs 180,186 lamports in fees and puts no rent at risk.

## What this does not separate

Details and citations are in [docs/METHODOLOGY.md](docs/METHODOLOGY.md#what-this-does-not-separate).

- Fee priority versus reverse arrival, in the committed data (see the collinearity limit).
- Scheduler pop order versus ledger record order, on the non-conflict ladder. Worker threads can finish out of queue order.
- Greedy versus prio-graph on the conflict ladder alone. Both serialize a pure conflict set by priority.
- Operator flags that the client label does not carry, such as Firedancer `schedule_strategy` and Agave scheduler bindings.
- Ingress and scheduling. QUIC admission, sigverify, and Jito relayers act before the scheduler, so non-landing is not a sample of the ordering rule.
- Bundles. We send none. A Jito block can still contain them.
- The full reward/cost surface. The compute limit and the account set are held fixed.
- One sender, one network path, one slot per validator per run.

## Raw data

`data/runs/<run>/` is committed for both live runs:

| File | Contents |
| --- | --- |
| `manifest.json` | Config (cap, TPU copies, send order, ladders), git SHA, targets, and every planned transaction with fee, send index, target slot, and signature. |
| `validators.json` | Selected identities with `clientId`, version, stake, and TPU address. |
| `attempts.jsonl` | One row per transmission: signature, send time, endpoint, result. |
| `block_observations.jsonl` / `.csv` | Landed slot and block index of each probe. |
| `block_context.jsonl` | Per-block transaction count and positions of our probes. |
| `blocks/<slot>.json.gz` | `getBlock` body (`transactionDetails: "accounts"`) for each measured slot, backfilled read-only after the runs. |
| `spend.json` | That run's ledger entries. |
| `run.log`, `live-stderr.log` | Pipeline logs, with RPC credentials removed. |

Not committed: `data/keys/` (probe keypairs), `data/spend.json` (global ledger), `data/dry-runs/`, `.env`, and any `id.json` or keypair file. See [.gitignore](.gitignore).

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
