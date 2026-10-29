# Measurement pipeline contract

Python 3.12. This file is the pipeline side of the contract in the docs branch. Where the two disagree, the list at the bottom is what the docs should change. The code is the source of truth for numbers.

## Environment

| Name | Effect |
| --- | --- |
| `MODE` | `live` submits. Absent, `dry-run`, or any other value does not. Same switch as `--live`. |
| `--live` | CLI form of `MODE=live`. Either one is enough. Both together are fine. |
| `SPEND_CAP_SOL` | Default and hard maximum `0.05`. A larger value is a startup error. |
| `KEYPAIR_PATH` | Host path. Compose mounts it read-only at `/keys/id.json` and sets the in-container path to that mount. Unset dry-run uses an ephemeral key. |
| `RPC_URL` | Mainnet RPC. Logged with userinfo and query stripped. |
| `VALIDATORS_APP_TOKEN` | Optional. Sent only as the `Token` header to validators.app. Never logged. |
| `DATA_DIR` | Default `data`. Compose sets `/data`. |

`LIVE=1` does nothing. Dry-run signatures are not appended to the ledger.

## Commands

```bash
docker compose up --build
python -m measure run
```

```bash
MODE=live SPEND_CAP_SOL=0.05 KEYPAIR_PATH=/absolute/path/to/id.json \
  RPC_URL=https://your-mainnet-rpc docker compose up --build
```

```bash
python -m measure run --live
python -m measure key --keypair /absolute/path/to/id.json
```

`key` prints the public key and `getBalance`. It does not sign or send.

Compose service `measure`, build context `./measure`, host network, volume `./data:/data`.

## Run directory

```text
data/runs/<UTC>/
  manifest.json
  validators.json
  spend.json
  attempts.jsonl
  blocks/<slot>.json
  run.log
data/spend.json          # global fee ledger, not inside the run directory
```

`data/runs/` and `data/spend.json` are gitignored. A new run directory does not reset the ledger. Only a live signature reserves lamports, and it does that before the bytes are handed to QUIC. There is no retry inside a window.

## What a run plans

Six identities, at least three `clientId` / `software_client` families, one direct-TPU window each. Frankendancer is recorded if it is scheduled and is not preferred. When `VALIDATORS_APP_TOKEN` is set, `software_client` and `software_version` replace the RPC version fields. Without it, `getClusterNodes` `clientId` plus `getVoteAccounts` stake is the classification, and the manifest says `rpc`.

Two ladders, fees `0, 1, 2, 4, 8, 16` lamports, low fee first, same 1,000 CU limit, one-lamport system transfer, no memo:

- **conflict** at slot offset 0: one payer, one recipient
- **nonconflict** at slot offset 2: distinct payer and recipient per rung

Twelve probe accounts are funded once (rent plus the fees those accounts will pay). The conflict ladder reuses the first recipient. Funding is its own slot, before the measured slots, and is not part of either tau.

QUIC warmup is 2 slots before the target. The burst offset is 1 slot before the target. Both numbers are in `manifest.json` and are the same for every leader.

`getAgGenesisCert` is called on live RPC. The stored certificate slot is checked against 459,652,999. The measurement cutoff remains slot `459653000` even when the method is missing.

`getBlock` uses `maxSupportedTransactionVersion: 1`. Probes themselves are legacy transactions with `SetComputeUnitLimit` and `SetComputeUnitPrice`, not SIMD-0385 v1 messages. The controlled variable is the priority fee in lamports. Version 0 `getBlock` requests fail on current mainnet because blocks contain v1 transactions.

CU limit 1,000 was frozen from a devnet `simulateTransaction` on 2026-10-29 (slot 516359079, 450 CU consumed). That probe is not a measurement.

Priority score `P = (reward * 1_000_000) / (cost + 1)` is checked unique before a ladder is kept. Reward is half the base fee plus the whole priority fee. Cost is the fixed shape: 720 per signature, 300 per writable account, plus the CU limit.

Slot calls live in `measure/stats.py`: `strict_priority` at tau-b = −1, otherwise the thresholds in the methodology.

## Default cost

Conflict probes are one-lamport self-transfers from the funder, so they do not create an account. Each non-conflict rung is its own fee payer, also a self-transfer. Those keypairs are written to `DATA_DIR/keys/<experiment_id>/` mode 0600 (gitignored). A live run sweeps them back to the funder after the probes land; `python -m measure sweep` retries any that remain.

Rent used for the peak figure is the 2026-10-29 `getMinimumBalanceForRentExemption(0)` value, 650,240 lamports. The non-conflict ladder is shortened until:

- net fees (probes, funding, and the sweep) are at most **600,000 lamports (0.0006 SOL)**
- peak capital, net plus temporary rent, is at most **3,000,000 lamports (0.003 SOL)**

The conflict ladder stays at six rungs. A dry-run prints both numbers. The 0.05 SOL cap still counts fees only.

A fixture dry-run at rent 650,240 lamports keeps non-conflict fees `(0, 1, 2, 4)` and projects:

- net after sweep: **340,228 lamports (0.000340228 SOL)**
- peak, including temporary rent: **2,941,212 lamports (0.002941212 SOL)**
- temporary rent sweep returns: **2,600,984 lamports**

`run --live --collect` fetches blocks after the send, including slots reported by `getSignatureStatuses` so funding is not left on a planned slot it missed. `hypothesis_agreement.json` includes `ladders`: Kendall tau-b of priority fee against block index for each validator's conflict and non-conflict group. The ledger's end-of-run reconcile, and `ledger reconcile --rpc`, sum `getTransaction` `meta.fee`. Probe bursts are written once unless `--tpu-copies` is set; each copy's timestamp is `copy_send_unix_ms`.

## Live send, after the second 2026-10-29 miss (11:17–11:22 Kyiv)

Run `20261029T091750Z` crashed before any send: `slotSubscribe`'s acknowledgement is `{"result": <subscription id>}`, and that integer was used as a slot. `getSlotLeaders` then asked for epoch 0. Run `20261029T091846Z` got a clean QUIC close (`0x0`) and still measured nothing. Funding was handed to the leader of the slot where signing started. The handshake took about 1.7s, the leader rotated, and all 12 funding transactions were dropped. `getSignatureStatuses` and `getBlock` then returned HTTP 429. The 429 was treated as "not landed", the run aborted `funding_unconfirmed`, and `data/spend.json` kept a 60,000 lamport reservation (12 × 5,000) for transactions that are not on chain. `attempts.jsonl` recorded the planned funding `tpu_quic`, not the socket that was written. `collect` raised on the first skipped or not-yet-produced slot and stopped.

Fixes:

- Only `slotNotification` messages advance the clock, and only if the slot is at least 1,000,000. The subscribe ack is ignored.
- Funding goes out with `sendTransaction` (`skipPreflight`, `maxRetries` 0). The same bytes are also copied to the TPU of the next two or three leaders, not the leader who is about to rotate out. Probe bursts do not use RPC. Each probe connection is opened `QUIC_WARMUP_SLOTS` (12) before the window, the transactions are signed while that connection is open, and the burst is written twice to that leader only when the clock is `ceil(rtt / 0.215s)` slots before the target.
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

## Differences the docs should pick up

1. **`MODE=live` and `--live` are aliases.** The process does not require both. `MODE` other than `live` stays on dry-run even if someone expected a typo to fail closed. `--live` still sends if `MODE` is unrecognised.
2. **Build context is `./measure`.** The root `Dockerfile` remains so `docker build -f Dockerfile .` from the repo root still works. Compose does not use it.
3. **Host network.** Bridge NAT cannot reach leader QUIC reliably. The docs do not mention this.
4. **No RPC submit path.** The methodology says one path. An earlier pipeline draft had an RPC comparison arm. It is gone.
5. **Upcoming leaders come from `getSlotLeaders`**, not a full-epoch `getLeaderSchedule`. The four-slot grouping is checked on that lookahead (default 16,000 slots).
6. **Probe accounts are a shared pool of 12**, funded once, not six new payers per leader. Per-leader funding would push rent principal over the 0.012 SOL design budget. The cap still counts only fees.
7. **Non-conflict payers are ephemeral keypairs** generated for the process. Their secrets are not written to disk. A later live run creates its own. Dry-run signatures are of those keys (and of `KEYPAIR_PATH` when it is mounted) and the raw signed bytes are discarded.
8. **Dry-run does sign, and does not submit, and does not touch `data/spend.json`.** The docs' "not counted" sentence matches this.
9. **`key` subcommand** is extra. It is not in the docs.
10. **Frankendancer is not sought.** If `clientId` is Frankendancer and the identity is scheduled, it is its own family at the back of the round-robin.
11. **Stake outside the top ten** is attempted. If the lookahead has no such identity, the run still proceeds and `notes` says so. The docs ask for that sentence under what went wrong.
12. **validators.app `active_stake` unit** is inferred: values ≥ 1e12 are lamports, smaller values are SOL. `getVoteAccounts.activatedStake` is always lamports.
13. **Entry index** is still absent from `getBlock`. Tau uses block index.
14. **Devnet CU probe is a constant in the manifest**, not a call on every run, so a mainnet dry-run does not touch devnet.
