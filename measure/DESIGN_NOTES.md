# Measurement pipeline contract

Python 3.12. The README has the operator view and `docs/METHODOLOGY.md` the design. This file records pipeline decisions and the debugging history behind them. The code is the source of truth for numbers.

## Environment

All variables, with defaults, are in the README's environment table; `measure/config.py` reads them. Points that are easy to miss:

- `MODE=live` and `--live` are aliases. Either one sends. Any other `MODE` value, or none, stays on dry-run. `LIVE=1` does nothing.
- `SPEND_CAP_SOL` is cumulative over `data/spend.json`, and above the 0.05 SOL hard cap it is a startup error. The ledger keeps the lowest value ever used. `RUN_SPEND_CAP_SOL` (`--run-spend-cap-sol`) is an optional per-run cap on top. It counts the run's reservations net of releases, plus entries tagged `<run>:sweep`, and it is not stored.
- `KEYPAIR_PATH` is mounted read-only at `/keys/id.json`. An unset dry-run uses an ephemeral key.
- `RPC_URL` is logged with userinfo and query stripped. `VALIDATORS_APP_TOKEN` is sent only as the `Token` header and never logged.
- `SEND_ORDER` (`ascending`, `descending`, `shuffled`), `SEND_ORDER_SEED`, `LADDERS` (`both`, `conflict`), `TPU_COPIES` (default 1), and `COLLECT` (`1`/`true`/`yes`) mirror the `run` flags `--send-order`, `--send-order-seed`, `--ladders`, `--tpu-copies`, and `--collect`.

## Commands

```bash
docker compose up --build                       # dry-run
python -m measure run                           # dry-run
python -m measure run --fixture-dir measure/tests/fixtures/offline --min-lead-slots 16   # offline
MODE=live KEYPAIR_PATH=/abs/id.json RPC_URL=https://keyed-rpc docker compose up --build
python -m measure key                           # pubkey and getBalance; does not sign or send
python -m measure ledger show | reconcile [--attempts f] [--rpc] | reset --yes
python -m measure collect --from-sent data/runs/<run>/attempts.jsonl
python -m measure sweep [--keys-dir data/keys/<run>]
python -m measure parse-block --file block.json --slot N
```

The Compose service is `measure`, with build context `./measure`, host network, and volume `./data:/data`. Bridge NAT does not reach leader QUIC reliably. The root `Dockerfile` remains for `docker build -f Dockerfile .`; Compose does not use it. The `report` service (`Dockerfile.report`) runs the offline analysis and figures with no network.

## Run directory

```text
data/runs/<UTC>/            live only, committed as evidence
  manifest.json  validators.json  attempts.jsonl  spend.json
  block_observations.jsonl  block_observations.csv  block_context.jsonl
  hypothesis_agreement.json  blocks/<slot>.json[.gz]  run.log  live-stderr.log
data/dry-runs/<UTC>/        dry-run plans, gitignored
data/keys/<UTC>/            probe payer keypairs, 0700/0600, gitignored
data/spend.json             global fee ledger, gitignored
```

A new run directory does not reset the ledger. Only a live signature reserves lamports, and it does that before the bytes are handed to QUIC. There is no retry inside a window. Dry-run signs and does not submit. It does not touch `data/spend.json`, and the signed bytes are discarded.

## What a run plans

Six identities, at least three `clientId` families, one direct-TPU window each. The selector goes round-robin across labels and adds one identity outside the ten largest by stake when one is scheduled; otherwise `notes` says so. Frankendancer is its own family at the back of the round-robin. With `VALIDATORS_APP_TOKEN`, `software_client` and `software_version` replace the RPC fields, and validators.app `active_stake` values of at least 1e12 are read as lamports, smaller values as SOL. Without the token, `getClusterNodes` `clientId` plus `getVoteAccounts` stake is the classification, and the manifest says `rpc`.

Two ladders, fees `0, 1, 2, 4, 8, 16` lamports, same 1,000 CU limit, one-lamport system transfer, no memo:

- **conflict** at slot offset 0: a self-transfer from the funder on every rung, so the funder is the one shared write lock
- **nonconflict** at slot offset 2: a fresh payer per rung, each self-transferring, bound to its rung's fee

The send order within a burst is `--send-order`. The default `ascending` sends the low fee first, and both live runs used it. `shuffled` draws a seeded permutation with |tau-b(fee, send)| ≤ 1/3 per ladder (`plan.send_permutation`). `descending` sends the high fee first. `--ladders conflict` drops the non-conflict ladder, and with it the funding, the rent, and the sweep.

Run 3 (`9d482c8`) predates the self-transfer form. Its 12 probe accounts were funded once as a shared pool (six payers and six recipients), the conflict ladder reused the first recipient, and the keys were held only in memory, so the rent could not be swept. Run 4 (`164768b`) and later use the form above.

QUIC connections open `QUIC_WARMUP_SLOTS` (12) before the target. The burst fires at `target_slot − max(BURST_OFFSET_SLOTS, ceil(rtt / 0.215 s))`, which was one slot for every burst in runs 3 and 4. Both constants are in `manifest.json` and are the same for every leader.

`getAgGenesisCert` is called on live RPC. The stored certificate slot is checked against 459,652,999. The measurement cutoff remains slot `459653000` even when the method is missing.

`getBlock` uses `maxSupportedTransactionVersion: 1`. Probes themselves are legacy transactions with `SetComputeUnitLimit` and `SetComputeUnitPrice`, not SIMD-0385 v1 messages. The controlled variable is the priority fee in lamports. Version 0 `getBlock` requests fail on current mainnet because blocks contain v1 transactions. Entry index is absent from `getBlock`, so tau uses the block index.

The 1,000 CU limit was frozen from a devnet `simulateTransaction` on 2026-10-29 (slot 516359079, 450 CU consumed). That probe is a manifest constant, not a call on every run, and it is not a measurement.

Priority score `P = (reward * 1_000_000) / (cost + 1)` is checked unique before a ladder is kept. Reward is half the base fee plus the whole priority fee. Cost is the fixed shape: 720 per signature, 300 per writable account, plus the CU limit.

Slot calls live in `measure/stats.py` and `analysis/core.py`: `strict_priority` at tau-b = −1, otherwise the thresholds in the methodology. The arrival test uses tau-b against send order, so it stays correct for shuffled ladders.

## Default cost

Non-conflict payer keypairs are written to `DATA_DIR/keys/<experiment_id>/` with mode 0600 (gitignored). A live run sweeps them back to the funder after the probes land; `python -m measure sweep` retries any that remain.

Rent used for the peak figure is the 2026-10-29 `getMinimumBalanceForRentExemption(0)` value, 650,240 lamports. The non-conflict ladder is shortened until:

- net fees (probes, funding, and the sweep) are at most **600,000 lamports (0.0006 SOL)**
- peak capital, net plus temporary rent, is at most **3,000,000 lamports (0.003 SOL)**

The conflict ladder stays at six rungs. A dry-run prints both numbers. The 0.05 SOL cap still counts fees only.

A fixture dry-run at rent 650,240 lamports keeps non-conflict fees `(0, 1, 2, 4)` and projects:

- net after sweep: **340,228 lamports (0.000340228 SOL)**
- peak, including temporary rent: **2,941,212 lamports (0.002941212 SOL)**
- temporary rent returned by the sweep: **2,600,984 lamports**

Run 4 matched this exactly. With `--ladders conflict` the plan is 36 probes and 180,186 lamports of fees, with no rent.

`run --live --collect` (or `COLLECT=1`) fetches blocks after the send, including slots reported by `getSignatureStatuses`, so funding is not left on a planned slot it missed. The end-of-run ledger reconcile, and `ledger reconcile --rpc`, sum `getTransaction` `meta.fee`. Probe bursts are written once unless `--tpu-copies` is set, and each copy's timestamp is `copy_send_unix_ms`.

# History

The sections below describe failures in the first two live attempts and the fixes that followed. They are kept as the record behind "what went wrong" in the report. Some details here, such as writing every burst twice, were later changed.

## Live send, after the second 2026-10-29 miss (11:17–11:22 Kyiv)

Run `20261029T091750Z` crashed before any send: `slotSubscribe`'s acknowledgement is `{"result": <subscription id>}`, and that integer was used as a slot. `getSlotLeaders` then asked for epoch 0. Run `20261029T091846Z` got a clean QUIC close (`0x0`) and still measured nothing. Funding was handed to the leader of the slot where signing started. The handshake took about 1.7s, the leader rotated, and all 12 funding transactions were dropped. `getSignatureStatuses` and `getBlock` then returned HTTP 429. The 429 was treated as "not landed", the run aborted `funding_unconfirmed`, and `data/spend.json` kept a 60,000 lamport reservation (12 × 5,000) for transactions that are not on chain. `attempts.jsonl` recorded the planned funding `tpu_quic`, not the socket that was written. `collect` raised on the first skipped or not-yet-produced slot and stopped.

Fixes:

- Only `slotNotification` messages advance the clock, and only if the slot is at least 1,000,000. The subscribe ack is ignored.
- Funding goes out with `sendTransaction` (`skipPreflight`, `maxRetries` 0). The same bytes are also copied to the TPU of the next two or three leaders, not the leader who is about to rotate out. Probe bursts do not use RPC. Each probe connection is opened `QUIC_WARMUP_SLOTS` (12) before the window, the transactions are signed while that connection is open, and the burst is written twice (run 3; later code writes it once unless `--tpu-copies` is set) to that leader only when the clock is `ceil(rtt / 0.215s)` slots before the target.
- Reads (`getSignatureStatuses`, `getBlock`, `getSlot`) retry HTTP 429 and the node-busy codes with exponential backoff and jitter, and reads on one connection are paced. A 429 does not abort the run inside that deadline; signature status stays "unknown" until the slot deadline plus 90 seconds. `RPC_URL` may carry a key in the query string (it is redacted in the manifest). `WS_URL` is optional and separate; otherwise the socket URL is derived from `RPC_URL`.
- Attempt rows store `destinations` and `send_unix_ms`. `tpu_host` is the first real `host:port` in `destinations`, which is a socket that accepted the bytes.
- `collect` skips a skipped slot, waits for a block that is not produced yet (up to a few minutes), retries 429, and always continues to the next slot.
- At the end of a live run the ledger moves that run's net reservation to the fees whose signatures landed. A failed status read does not change the file. `ledger reconcile --attempts <file> --rpc` does the same later. It uses `experiment_id` from the rows or, for this run, from the sibling `manifest.json`. It does not replace other runs' spend.

Clear the 60,000 lamport reservation before the next send. Without `--rpc`, reconcile keeps every `submitted: true` row, which is all 12 funding rows, so the reservation would stay.

```bash
RPC_URL='https://your-keyed-mainnet-rpc' \
  python -m measure ledger reconcile \
  --attempts data/runs/20261029T091846Z/attempts.jsonl \
  --rpc

RPC_URL='https://your-keyed-mainnet-rpc' \
WS_URL='wss://your-keyed-mainnet-rpc' \
KEYPAIR_PATH=/absolute/path/to/id.json \
SPEND_CAP_SOL=0.05 \
  python -m measure run --live --collect --min-lead-slots 150
```

If reconcile prints `not reconciling; status read failed`, the 60,000 lamports are still reserved. Retry with the keyed URL. Do not `ledger reset` unless you intend to drop the reservation without asking the chain.

`WS_URL` may be omitted when it is the `wss://` form of `RPC_URL`. Do not point probes at any leader except the one whose window is being measured.

## Live send, after the 2026-10-29 miss

The first live run reserved every planned fee, then skipped 12 of 13 groups because each `getSlot` opened a new TLS connection (~0.9s) and slept 0.4s. The one group that was handed to QUIC was a Jito leader, which closed with `0x174 peer sent no certificates`. Those six rows were still marked sent, and `submitted` in `attempts.jsonl` stayed false because the writer hardcoded it.

Fixes:

- The QUIC client presents an ephemeral Ed25519 certificate. A peer error close is a failed send. If nothing was written, the reservation is released.
- Slot updates come from `slotSubscribe` when the RPC allows it, otherwise from a keep-alive `getSlot` with a 2s socket timeout. A group is sendable until the leader window ends, not only for one slot after the target.
- Funding is submitted first, to the current leader, and probe groups wait until those signatures are at least `processed`. Default `MIN_LEAD_SLOTS` is 150.
- If TPU fails before transmitting, the same signed bytes go out once via `sendTransaction` (`skipPreflight`, `maxRetries` 0). The row records `tpu` or `rpc-fallback`.
- `attempts.jsonl` records `submitted`, `submit_path`, and `error`. `collect --from-sent` accepts that file (`target_slot` as well as `planned_slot`). `run --collect` without `--live` reads the newest run.
- `python -m measure ledger show`, `ledger reconcile --attempts <attempts.jsonl>`, and `ledger reset --yes`. Reconcile without `--rpc` keeps rows with `submitted: true`. The 420,372 lamport reservation from run `20261029T085637Z` was cleared that way before run 2 (those rows were not submitted). The next command is in the section above; run 2's 60,000 lamports need `--rpc` because those funding rows are marked submitted and did not land.
