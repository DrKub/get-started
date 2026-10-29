# Measurement design

This note is the contract for the docs/report agent. If a number here drifts from the code, trust `measure/constants.py`, `measure/fees.py`, and `measure/plan.py`.

The question, for each selected leader: which rule orders transactions inside its block, how strictly it holds, and whether any rule is visible at all. "No rule / it's noise" is an allowed result. Jito bundles and tips are out of scope. Every measurement in a live run is taken **after** the Alpenglow activation (epoch 1064, 2026-10-21 13:36 UTC, https://solana.com/upgrades/alpenglow). The pipeline refuses to measure when `getEpochInfo.epoch` is still below 1064, so a before-epoch and an after-epoch cannot be mixed in one successful run.

Alpenglow replaced Tower BFT voting. It did not change the SVM, transaction format, or fee formula. Ordering is still a property of the leader's scheduler, and that scheduler is client-specific. The run records the observed epoch, the activation constant, and `regime: "after"` in `manifest.json`.

## How to run

Dry-run is the default. It reads the cluster, selects leaders, builds unsigned messages, estimates fees, and writes `data/raw/`. It does not sign and it does not open a TPU connection.

```bash
docker compose up --build
# or, from a checkout with the dev requirements installed:
python -m measure run
```

Compose uses the host network. The process dials a public RPC and, in live mode, leader QUIC addresses; a bridge NAT drops or rewrites that path.

Offline, with the committed fixture (no RPC):

```bash
python -m measure run --fixture-dir measure/tests/fixtures/offline --experiment-id e1071-test
```

Live sending is a separate invocation. Both the flag and a key file are required. The spend cap is always on and cannot be set above 0.05 SOL. `LIVE=1` in the environment does nothing.

```bash
docker compose run --rm \
  -v "$KEYPAIR_PATH:/keys/id.json:ro" \
  -e KEYPAIR_PATH=/keys/id.json \
  measure run --live
```

After the windows have passed:

```bash
python -m measure collect \
  --from-sent data/raw/sent_transactions.jsonl \
  --plans data/raw/planned_transactions.jsonl
```

`run --live --collect` waits until the last targeted slot and then collects. Tests:

```bash
python -m pytest measure/tests
```

Exit codes: `0` success, `1` config/RPC/nothing sent, `2` fewer than 6 validators or 3 families, `3` plan or send would exceed the cap, `4` epoch is still before Alpenglow, `5` `--live` without a keypair file.

## Language and layout

Python 3.12. `solders` builds legacy messages and, only on the live path, signs them. `aioquic` is the TPU client. The standard library does JSON-RPC. Parsing tests use recorded `getBlock`-shaped fixtures, not `solana-test-validator`: a local validator has one client and empty blocks, which is the same reason the bounty says devnet cannot answer the question.

| Path | Role |
| --- | --- |
| `measure/plan.py` | Arms, fees, pre-registered ranks |
| `measure/select.py` | Family-diverse leader pick |
| `measure/txbuild.py` | Unsigned messages; `sign_transaction` |
| `measure/live.py` | Slot wait, cap, sign, send |
| `measure/tpu.py` | QUIC unidirectional streams, ALPN `solana-tpu` |
| `measure/collector.py` | `getBlock` positions, fees, CU price |
| `measure/analyze.py` | Pairwise agreement with each hypothesis |
| `data/raw/` | Run output (gitignored except `.gitkeep`) |
| `data/samples/` | Deterministic fixture output and schema |

## Validator selection

1. `getEpochInfo`, `getClusterNodes`, and paged `getSlotLeaders` (4000 slots per call, default lookahead 16000, never past the end of the current epoch).
2. After the leader fetch, `getSlot` again. A window is eligible only when it starts at least `MIN_LEAD_SLOTS` (default 16) after that fresher slot, is a full 4-slot rotation, and is not a leftover created by cutting the lookahead mid-rotation.
3. Join leader identity to `getClusterNodes` on pubkey. A leader is ineligible without a `clientId`, a version string, or `tpuQuic`. `Unknown(*)` client ids are dropped rather than treated as a family.
4. Round-robin across families in `FAMILY_PRIORITY` until 6 validators are chosen (configurable only upward). Within a family, prefer the identity with the most eligible windows, then the soonest window, then the pubkey. That biases toward leaders we can actually repeat, which on the current schedule are also the higher-stake members of each family.
5. Assign the earliest windows. Default demand is 2 TPU repeats and 1 RPC repeat. If a leader has only two windows, the assignment keeps one TPU and one RPC and records a shortfall. One window becomes TPU only.

`version` is stored and reported, but it is not the family key. On this cluster, `4.3.2` is advertised by Agave, JitoLabs, AgaveBam, HarmonicAgave, and Rakurai. `26.10.4` is advertised by Firedancer, HarmonicFiredancer, and FireBAM. `clientId` (added to `getClusterNodes` in Agave 4.x) is what separates those forks. A bare semver is classified as `semver-ambiguous` if someone tries to use the version string alone; selection never does that.

Family priority, first six that are actually scheduled:

1. Firedancer
2. Frankendancer (often absent from the leader schedule; skipped, not invented)
3. HarmonicFiredancer
4. JitoLabs (`jito-solana`)
5. Agave
6. HarmonicAgave
7. AgaveBam
8. FireBAM, Rakurai, Raiku, then any other named `clientId`

A probe during pipeline development (epoch 1071) found scheduled leaders for Firedancer, HarmonicFiredancer, JitoLabs, Agave, HarmonicAgave, AgaveBam, FireBAM, Rakurai, and Raiku, and no Frankendancer. The dry-run manifest in a given invocation is the set that run actually saw. Re-run rather than reuse a stale manifest: the schedule moves.

JitoLabs and AgaveBam are in scope as schedulers. We do not submit bundles, tips, or BAM packets. Their blocks may still contain those mechanisms; see "What this does not separate".

## Transactions

Every transaction is a legacy (not v1) message with three instructions:

1. `SetComputeUnitLimit`
2. `SetComputeUnitPrice`
3. SPL Memo v2 (`MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`), with the fee payer as a signer account

The memo is the on-chain tag:

```text
ib|1|{experiment_id}|{seq}|{arm}|{send_index}|{path}|p{priority_fee}|c{cu_price}|l{cu_limit}
```

`experiment_id` is `[A-Za-z0-9_-]+`. `seq` is unique inside the run. Judges can find our transactions with `getBlock` and this prefix, without trusting our index math.

The fee payer is writable, so every transaction in the run conflicts with the others on that account. Solana has no way to pay a fee without writing the payer. Distinct payers would remove the conflict, and they would also require funding several rent-exempt accounts. This design keeps one payer on purpose: the documented Agave rule is about priority under account conflicts (a lower-priority transaction must not take a lock a higher-priority one needs). Conflict-free parallel scheduling is not what these transactions measure.

Dry-run messages use the placeholder pubkey `8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u` unless `KEYPAIR_PATH` points at a real file, in which case only the pubkey is read. The placeholder secret was discarded when the address was generated. Do not fund it. Live mode rebuilds and signs with the mounted key and a fresh blockhash per slot group. The planned file stays unsigned; signatures exist only in `sent_transactions.jsonl`.

Signed transactions are checked to be at most 1232 bytes, the TPU stream window (`PACKET_DATA_SIZE`). A short memo plus compute-budget instructions lands near 300 bytes.

## Arms and pre-registered hypotheses

One leader window (4 slots) gets 12 transactions. Ranks are written into the plan **before** any send. Rank 0 means "this hypothesis says the transaction is included earliest". Ties break by ascending send index.

Hypothesis ids, also copied into the manifest:

| Id | Rank fields | Claim |
| --- | --- | --- |
| `cu_price` | `rank_cu_price`, `rank_arm_cu_price` | Higher compute-unit price is earlier |
| `priority_fee` | `rank_priority_fee`, `rank_arm_priority_fee` | Higher total prioritization fee is earlier |
| `send_order` | `rank_send_order`, `rank_arm_send_order` | Earlier send index is earlier |

`rank_*` is across the whole window, for the case where every arm collapses into one block. `rank_arm_*` is inside the arm group, for the case where the arms land in the slots they were aimed at.

**Ladder** (slot offset 0). Six transactions, CU limit 20,000, priority fees 0, 1,000, 5,000, 20,000, 50,000, 200,000 lamports. Sent **low fee first**. With a fixed CU limit, compute-unit price and total priority fee imply the same order, and that order is the reverse of send order. If the block follows send order, the leader did not apply the priority rule to this conflict set. If the block follows descending price, it did. The 0-fee rung may simply not be included; a missing rung is data, not a hole to impute.

**Arrival** (slot offset 2). Four transactions, identical CU limit and identical priority fee (10,000 lamports), sent in index order. Price and fee hypotheses tie, so only a tie-break (send order, or noise) can show up. `rank_arm_cu_price` then matches send order because the tie-break is send index. The interesting comparison is whether block order matches `send_index` across repeats.

**Metric** (slot offset 3). Two transactions:

| Arm | CU limit | Priority fee | CU price |
| --- | --- | --- | --- |
| `metric-price` | 20,000 | 40,000 | 2,000,000 |
| `metric-total` | 80,000 | 80,000 | 1,000,000 |

`metric-price` wins on CU price. `metric-total` wins on total priority fee. Even repeats send price first; odd repeats send total first. The two priority hypotheses do not flip when send order flips, so a leader that ignores send order and follows one metric looks the same on both repeats, while a leader that follows arrival flips with the repeat.

Both limits are far below a block's CU capacity, so the wide limit is unlikely to be delayed by the block CU budget. The base signature fee (5,000 lamports) does **not** flip this pair if the scheduler uses `(priority_fee + base_fee) / cu_limit`: that ratio still ranks `metric-price` first (2.25 vs 1.0625 lamports per CU). This arm does not separate pure CU price from `(priority + base) / CU`. State that limitation in the report. On the ladder, a fixed CU limit makes CU price, priority fee, and `(priority + base) / CU` the same order.

Slot offsets are clamped to 0, and `arms_collapsed` is set, when the window is shorter than 4 slots. Selection normally refuses partial windows, so collapse is a fallback.

`measure/analyze.py` scores landed pairs inside the same slot, identity, experiment, and window. Agreement is the fraction of pairs whose earlier block index also has the better hypothesis rank. Rate near 1 supports that rule. Rate near 0.5 on many pairs is noise. A handful of pairs is not a verdict; the report should show the pair counts.

## Fees and the cap

The chain charges:

```text
signatures * 5000 + (cu_limit * cu_price_micro_lamports) // 1_000_000
```

CU prices are chosen so the division is exact (`cu_price_for_priority_fee`). One full window is **496,000 lamports** (0.000496 SOL): ladder 306,000, arrival 60,000, metric 130,000. Six validators times three windows is **8,928,000 lamports (0.008928 SOL)** when nobody is short a window. Shortfalls cost less. The hard cap is **50,000,000 lamports (0.05 SOL)**. `SPEND_CAP_SOL` may be lower. It may not be higher. The default plan is checked against the cap before any live send; a plan that does not fit exits 3 with nothing signed.

Live reservation is per slot-group (one arm inside one window), all or nothing, and it happens before signing. A group that does not fit is not partially sent, and neither are later groups. Missed windows and leader mismatches are not charged. A send that is attempted is charged against the cap even if the QUIC write errors, because we cannot prove the datagram never left. The cap is an upper bound, not a refund ledger. Transactions that fail on chain still pay the fee if they were included; `execution_err` is recorded separately from `landed`.

## Path, timing, and competition

**TPU.** For `path=tpu`, the signed bytes are written to the target leader's `tpuQuic` as one unidirectional QUIC stream each, ALPN `solana-tpu`, server certificate not verified (validators present a self-signed cert). UDP `tpu` is not used; Agave 4 removed UDP ingest. The send is aimed at that leader rather than at whatever node a public RPC happens to forward to. Unstaked connections are stake-throttled. A burst of 4–6 small streams is within the usual unstaked budget in quiet conditions and can still be dropped when the leader is busy. That is an open risk, which is why the RPC repeat exists.

**RPC.** `sendTransaction` with `skipPreflight: true` and `maxRetries: 0`, and our client does not retry either. A retry would resubmit into a later leader and destroy the "which window" attribution. The RPC still chooses the forward path. The comparison arm is there to see whether inclusion and order change when we do not speak QUIC to the leader ourselves. It is not a second sample of the same path.

**Timing.** The runner polls `getSlot` (processed) and sends when the slot reaches `planned_slot - 1`, so the burst is at the start of the target slot rather than minutes ahead (a previous leader could include it) or late (the next leader). It then checks `getSlotLeader` for that slot and skips the group on a mismatch. Groups whose slot is already more than one slot behind are marked `missed_window` and not sent. Slot duration is not hardcoded.

**Our transactions versus each other.** One fee payer, so they conflict. Arms are aimed at different slots of the same rotation to reduce cross-arm coupling. If the leader holds them in one queue anyway, the window-level ranks are the pre-registered combined order. The collector groups by the slot they actually landed in.

**Other transactions.** We do not try to outbid the block. The largest priority fee in the plan is 200,000 lamports (0.0002 SOL). On a busy slot our whole set can sit at the tail. That still tests the rule among our conflicting transactions if the scheduler sorts its queue. It does not test whether we can buy the front of a block. `block_context.jsonl` stores every transaction's signature, index, fee, CU price, CU limit, CU consumed, error, and fee payer, so a later analysis can see whether the surrounding block is priority-sorted and whether a bundle-shaped gap sits on top of us. The primary evidence remains our tagged transactions. Full block bodies are not stored; the slot and signatures are enough for a judge to call `getBlock`.

**Repetition.** Two TPU windows and one RPC window per leader, when the lookahead contains them. One window cannot separate a rule from a scrambled slot. The report should not claim a client-wide rule from a single landed window.

**Noise.** The three hypotheses are fixed before the run. `hypothesis_agreement.json` is the scorer. Inconsistent repeats, or agreement that flips with send order on the metric arm, are evidence against a stable priority rule.

## What this does not separate

- Conflict-free ordering. One payer, so every one of our transactions writes the same account.
- Pure CU price versus `(priority_fee + base_fee) / cu_limit`. The metric gaps are large enough that the base fee does not change the winner. Say so.
- Bundle or BAM auction effects versus the non-bundle scheduler. We never send a bundle or a tip. We also cannot delete other people's bundles from a Jito or AgaveBam block. Neighbor rows show a displacement; they do not identify its cause.
- Entry / PoH batch versus block index. `getBlock` does not return entry boundaries (and Alpenglow removed tick entries from the shred stream). `entry_index` is null unless a response actually carries `entryIndex`. Block index is the position.
- Multi-region arrival. One sender. Send order is our order; arrival order at the leader adds path jitter. The arrival arm is how that jitter shows up.
- Every version inside a family. We pick one identity per family (the one we can repeat), not every patch release.
- Firedancer versus Frankendancer, when Frankendancer is not in the upcoming schedule. Absence is recorded by omission and by the exclusion counts, not filled with a guess.
- Inclusion policy versus ordering. A transaction that never lands has no order. The 0-fee ladder rung is the one most likely to show that.

## Collector

`getBlock` uses `encoding: jsonParsed`, `transactionDetails: full`, `rewards: false`, `commitment: confirmed`, `maxSupportedTransactionVersion: 1` (blocks already contain v1 transactions; asking only for version 0 makes the RPC reject the whole block). Compute-budget instructions are read both from the parsed form and from raw base58 data, because the public mainnet RPC often leaves them unparsed. Memo text is the parsed string, else decoded instruction data, else the program log line.

Output:

| File | Contents |
| --- | --- |
| `manifest.json` | Epoch, regime, targets, windows, exclusion counts, fee cap |
| `planned_transactions.jsonl` / `.csv` | One row per planned transaction, with ranks and the unsigned message |
| `run_summary.json` | Exit status, families, fee, signed and sent counts |
| `sent_transactions.jsonl` | Live only. Signature, slot at send, destination, errors |
| `block_observations.jsonl` / `.csv` | Our transactions: slot, block index, entry index, fee, CU fields, memo, whether the plan's hypotheses were attached |
| `block_context.jsonl` | One row per fetched block, compact rows for every transaction |
| `hypothesis_agreement.json` | Pairwise scores |

`landed: false` means the memo was not found in the fetched slots (the leader window plus two trailing slots). It does not mean the signature is unknown to the cluster forever; a judge can still look it up.

## Safety

- Default command is dry-run. Docker Compose does not pass `--live`.
- `--live` and `--fixture-dir` together are refused.
- Keys are read from `KEYPAIR_PATH` or `--keypair`. Nothing under `secrets/`, `keys/`, `id.json`, or `.env` is committed (see `.gitignore`).
- The cap is enforced in `resolve_spend_cap` and again in `SpendGuard.approve_batch` before each live group is signed.
- RPC URLs written to the manifest are stripped of userinfo and query strings.
- TPU destinations in the offline fixture are TEST-NET addresses (`203.0.113.0/24`), not mainnet hosts.

## Open risks

- Unstaked QUIC to Firedancer or Agave may drop or reorder the burst before the scheduler sees it. The RPC arm is the check, and it has its own forwarding confound.
- This session does not send, so the QUIC client is untested against a live leader. The wire format (raw signed transaction on one unidirectional stream, ALPN `solana-tpu`) matches Agave's streamer; a client that speaks a different QUIC dialect would show up as TPU inclusion failure with RPC inclusion success.
- Public RPC rate limits can slow `getSlotLeaders` or `getBlock`. Reads retry; `sendTransaction` does not.
- A slow RPC can burn the 16-slot lead, so the first window is skipped as missed. Re-run; do not send late on purpose.
- High-stake leaders are easier to repeat and are not a random sample of the stake distribution. The report should name the identities and not generalize to "all Firedancer" from one operator.
- 0.008928 SOL is the full default plan, not a prediction of how much will actually be debited. Unlanded attempts still count against the cap once sent. Landed failures still debit the fee payer.
