# Submission checklist

This maps the Superteam listing [Inside the Block](https://earn.superteam.fun/listing/inside-the-block-how-solana-validators-order-transactions) onto this repository, as of the integration branch. "Met" means a reviewer can check the item from a file or a command named in the row. "Partly" means the row says what is missing.

## Submission requirements

| Requirement | Where it is satisfied | Status |
| --- | --- | --- |
| Repository link, any language | This repo, Python 3.12. The sender is in `measure/`, the analysis in `analysis/`, the figures in `viz/`. | Met |
| One-command reproduction with Docker Compose | `docker compose up --build` is a mainnet dry-run. `docker compose --profile report run --rm report` rebuilds `analysis/output/` and `figures/` from committed data with `network_mode: none`. Without Docker, use `python -m analysis && python -m viz`. CI runs both and checks that `git diff` is empty. | Met |
| Keys only through a volume or env, never in the repo | `KEYPAIR_PATH` is mounted read-only at `/keys/id.json`. `RPC_URL` and `VALIDATORS_APP_TOKEN` come only from env. Probe keys go to `data/keys/` (0600, gitignored). [.gitignore](../.gitignore) covers `.env*`, `keys/`, `data/keys/`, `id.json`, and `*keypair*.json`. `.dockerignore` keeps `.env` and `data/` out of images. CI scans every commit for key material. | Met |
| Dry-run by default; live only behind an explicit switch, with a spend cap | `MODE` defaults to `dry-run`. Only `MODE=live` or `--live` sends. Dry-run output goes to `data/dry-runs/`. `SPEND_CAP_SOL` defaults to 0.05 and rejects anything higher. Fees are reserved in `data/spend.json` before signing. See [compose_dry_run.log](#evidence-produced-during-review). | Met |
| All raw data with signatures and slots | `data/runs/<run>/`: `attempts.jsonl` (every signature), `block_observations.*` (slot and index), `blocks/<slot>.json.gz` (`getBlock` bodies), manifest, and logs. [EVIDENCE.md](../analysis/output/EVIDENCE.md) links all 168 probe signatures to Solscan and Explorer. | Met. The raw blocks were backfilled read-only after the runs; see the report. |
| README: why this design, each consideration, what it does not separate | [README.md](../README.md): "Design", "The main limit", "What this does not separate". The long form is [METHODOLOGY.md](METHODOLOGY.md) sections 3–11. | Met |
| Report with a date on every measurement and a "What went wrong" section | [REPORT.md](../REPORT.md): the measurement log has UTC times on 29 October 2026, and "What went wrong" separates design limits from pipeline failures. | Met |

## Scope constraints

| Constraint | How it is met | Evidence |
| --- | --- | --- |
| Mainnet only | All three runs are mainnet; devnet was used only for the CU probe, which is marked `excluded_from_report`. | `manifest.json` `regime: after`, `genesis_certificate.matches_expected: true` |
| At least six validators | 6 identities. Each has a conflict ladder in all 3 runs and a non-conflict ladder in runs 3 and 4. Run 5 was conflict-only. | REPORT "Per-validator results" |
| At least three clients | 6 `clientId` labels (Agave, JitoLabs, AgaveBam, HarmonicAgave, Firedancer, HarmonicFiredancer) in 3 families (Agave, Jito, Firedancer). Labels come from RPC `clientId`, not validators.app. | `validators.json`, REPORT study header |
| About 0.05 SOL, enforced in code | Hard cap 0.05 SOL on fees, reserved before signing. `SPEND_CAP_SOL` is cumulative and sticky; `RUN_SPEND_CAP_SOL` bounds one run. Total spent was 8,743,738 lamports (0.0087 SOL), including 7,802,952 lamports of unswept run-3 rent. The funder holds 3,256,262. | REPORT "Cost", with account and sweep links |
| Jito validators in scope; bundles and tips out of scope | JitoLabs and AgaveBam leaders were measured with ordinary TPU transactions. There is no `sendBundle` and no tip transfer in the code. | `manifest.json` `jito_bundles: out_of_scope` |
| Primary evidence is our own transactions | Tau, calls, and pooled shares use only our 168 probe signatures. Foreign transactions are used only for the contamination count (0 in all 30). | `ladder_statistics.csv` |
| Every measurement after Alpenglow (slot ≥ 459,653,000) | All measured slots are between 462,834,368 and 462,857,824. The planner rejects earlier slots. | `evidence_transactions.csv`; `ALPENGLOW_CUTOFF_SLOT` in `measure/constants.py` |

## Judging criteria

### Design validity, 35%

| Consideration | Where it is handled | Honest status |
| --- | --- | --- |
| Leader schedule and leader types | `getSlotLeaders`, `clientId` labels, round-robin selection, and the producing leader confirmed from each block's `Fee` reward (30/30 match). Methodology sections 3 and 8. | Met |
| Path to the executing node | Direct QUIC to `tpuQuic`, with no RPC on the probe path. Endpoint, RTT, and copies are recorded. Methodology section 10. | Met. There is one vantage point. |
| Interaction of our own transactions | Conflict ladder (funder self-transfers, one shared lock) versus non-conflict ladder (one payer per rung). The lock sets are verified from raw blocks. | Met |
| Competition inside the block | Tau on our signatures only, gaps, and a foreign-writer count against each ladder's lock set. | Met (0 contaminated) |
| Send order | Runs 3 and 4 sent the lowest fee first (`tau_fee_send` = +1 on those 24 ladders). Run 5 shuffled the conflict ladder (`|tau_fee_send| ≤ 1/3`). Agave, JitoLabs, AgaveBam, and Firedancer each landed at tau(fee) = −1 with \|tau(send)\| ≤ 1/3. | **Met for those four clients on the conflict ladder**, one slot each. Still open on the non-conflict ladder and for both Harmonic clients. HarmonicFiredancer's shuffled slot was reversed and is `no_rule_detected`. See REPORT "Fee order and send order" and METHODOLOGY section 9.3.1. |
| Noise versus a rule | Pre-registered calls, exact permutation p, and a cluster bootstrap. `inconclusive` applies at n < 5, and `mixed_across_slots` across runs. | Met. n is small: one slot per validator per ladder per run, and run 4's non-conflict ladders had four rungs. |

### Verifiability and reproducibility, 25%

| Judge action | Command or file |
| --- | --- |
| Rebuild every number and figure offline | `python -m analysis && python -m viz`, then `git diff --exit-code analysis/output figures` |
| The same, in a container with no network | `docker compose --profile report run --rm report` |
| Re-run the plan without spending | `docker compose up --build` (mainnet read-only), or the offline fixture command in the README |
| Check a claimed block index | REPORT per-slot row, then the `EVIDENCE.md` anchor, then the Solscan or Explorer link, compared with `data/runs/<run>/blocks/<slot>.json.gz` |
| Recompute a call | `ladder_statistics.csv` (`n`, tau-b, pair shares, exact p), using the rule in METHODOLOGY section 9.3 |
| Check the cost | REPORT "Cost": `meta.fee` sums, sweep signatures, and stranded-account links |
| Run all tests | `pytest measure/tests analysis/tests viz/tests` (also in CI) |

### Analysis quality, 25%

| Expectation | Where |
| --- | --- |
| Honesty about noise | Statistics sit next to every label. The thresholds were fixed beforehand. Pooled shares carry bootstrap CIs. |
| Null results | Run 4's six non-conflict ladders are `inconclusive`, and several ladders are `no_rule_detected`. HarmonicFiredancer's conflict ladder is `mixed_across_slots`. Its reversed shuffled slot is reported as one anomaly, not an inverse rule. |
| Limits stated | REPORT "Fee order and send order", "HarmonicFiredancer in run 5", "What this does not separate", and "What went wrong". METHODOLOGY section 11. |
| No mixed Alpenglow eras | All slots are at least 462,834,368. |
| Dates on every measurement | REPORT measurement log |

### Engineering, 15%

| Expectation | Where |
| --- | --- |
| Dry-run default | Compose `MODE: ${MODE:-dry-run}`. CI asserts it from `docker compose config`. |
| Spend cap in code | `measure/ledger.py`: reserve before signing, cumulative and sticky (`SPEND_CAP_SOL`), optional per-run cap (`RUN_SPEND_CAP_SOL`), reconciled to `meta.fee`. |
| Tests | `measure/tests`, `analysis/tests`, `viz/tests`. CI runs them on every push. |
| CI | [.github/workflows/ci.yml](../.github/workflows/ci.yml): tests, offline reproduction with a diff check, an offline shuffled dry-run, key-material scans over history, and the Compose report service. |
| Logging | `run.log`, `live-stderr.log`, and `attempts.jsonl`. RPC keys are stripped. |
| Containerization | `measure` (host network, read-only key mount, non-root) and `report` (no network). |

## Known gaps, ranked by judging impact

1. **Small n** (analysis). One slot per validator, per ladder, per run. The shuffled confirmation is one slot per client. Run 4's four-rung non-conflict ladders cannot pass the rule.
2. **HarmonicFiredancer's reversed shuffled slot, and HarmonicAgave's three `no_rule_detected` conflict slots** (analysis). The reversal is one ladder (tau +0.867, call `no_rule_detected`). The two Harmonic `tpuQuic` endpoints share an IP. Hypotheses are in the report; none is identified.
3. **Non-conflict ladder still collinear with send order** (design). Runs 3 and 4 sent it lowest-fee-first. Run 5 did not send it. A five-rung shuffled non-conflict ladder does not fit the lamports left in the funder.
4. **Runs are not exact replicates, and raw blocks were backfilled** (verifiability). Runs differ in TPU copies (2, then 1), conflict lock count (2, then 1), and send order (ascending, then shuffled). The committed blocks are a later read-only `getBlock`. They agree with the live positions exactly.
5. **One vantage point; labels from `clientId` only** (design). No validators.app cross-check. Slot position in the window is confounded with ladder type. `--send-order descending` has not been run.

## Evidence produced during review

- A Compose dry-run against mainnet (read-only) signed 64 transactions and sent 0.
- An offline shuffled dry-run sent nothing, and its plan recorded `send_order: shuffled` with |tau(fee, send)| ≤ 1/3 per ladder.
- The offline report container reproduced `analysis/output/` and `figures/` byte-for-byte.
- A read-only on-chain check confirmed the 12 run-3 accounts hold 7,802,952 lamports and the 4 run-4 payers hold 0.
