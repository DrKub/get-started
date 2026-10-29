# Submission checklist

Maps the Superteam listing [Inside the Block](https://earn.superteam.fun/listing/inside-the-block-how-solana-validators-order-transactions) onto this repository. "Docs" means this branch can satisfy the item. "Pipeline" means the item is only true once `measure/`, `docker-compose.yml`, and `data/` implement the contract in [README.md](../README.md) and [METHODOLOGY.md](METHODOLOGY.md). Those paths are owned by the measurement work and are out of scope for the documentation patch.

<!-- TODO: sync with measure/ -->

## Submission requirements

| Requirement | Where it is satisfied | Status |
| --- | --- | --- |
| Repository link, any language | This repo. Design in `README.md` and `docs/`. Sender in `measure/` when that work lands. | Docs: yes. Pipeline: pending. |
| One-command reproduce. Docker Compose expected. | `docker compose up --build` is defined as dry-run. | Pipeline. Contract in the README quickstart. |
| Keys only via volume or env, never in the repo | `KEYPAIR_PATH` mounted read-only at `/keys/id.json`. `RPC_URL` and `VALIDATORS_APP_TOKEN` are env only. | Pipeline must enforce ignore rules and the mount. Docs forbid committing them. |
| Dry-run by default. Live send only behind an explicit flag, with a spend cap. | `MODE` defaults to `dry-run`. Live requires `MODE=live`. Cap defaults to 0.05 SOL and the process must reject a higher cap. | Pipeline implements the check. Docs specify it, including the durable ledger under `data/`. |
| All raw data, signatures and slots, so judges can call `getBlock` | `data/runs/<UTC>/attempts.jsonl` and `blocks/<slot>.json`. Report tables quote the same fields. | Pipeline writes the files. [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) is the judge-facing index. |
| README: why this design, how each consideration is handled, what it does not separate | [README.md](../README.md) sections "Why this design", "How the design handles the required considerations", "What this does not separate". Long form in [METHODOLOGY.md](METHODOLOGY.md). | Docs. |
| Report with the date of every measurement, and a "What went wrong" section | [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) measurement log and "What went wrong". Dates are UTC dates of the sends. | Template is docs. Filled report waits on a live run. Do not submit the template with blank tables as if it were results. |

## Scope constraints

| Constraint | How the design meets it |
| --- | --- |
| Mainnet only for measurements | Report header fixes the network. Devnet and localnet are pipeline tests and are excluded from report tables. Methodology §2. |
| At least six validators | Selection procedure, methodology §8. The report keeps a row for every selected identity, including those never measured, so a shortfall is visible. |
| At least three client or fork versions | Stratify on validators.app `software_client` and `software_version`, or on the manifest's family labels when the API token is absent. Snapshot both at selection time. |
| About 0.05 SOL, enforced in code | `SPEND_CAP_SOL` default and hard maximum 0.05. Count base fee, priority fee, funding transfers, and retries before signing. Methodology §9.5. |
| Jito validators in scope. Bundles and tips out of scope. | No `sendBundle`, no tip accounts. Jito identities are a stratum. Ordinary probes only. Methodology §5. |
| Primary evidence is our own transactions | Tau and the calls use our signatures. Foreign transactions are covariates and a contamination flag. |
| Alpenglow: do not mix before and after 21 October 2026 | Slot cutoff ≥ 459,653,000. Pre-cutoff blocks cannot enter the report. Methodology §2. |

## Judging criteria

### Design validity, 35%

The experiment measures ledger order of our probes inside a known leader's block, against two pre-declared alternative orders (fee, and the reversed send order).

| Consideration the listing names | Where it is handled |
| --- | --- |
| Leader schedule and leader types | README "Leader schedule and leader types". Methodology §3 and §8. |
| Path from backend to the executing node, and everything in between | README "Path from this process to the executing leader". Methodology §4.6 and the path section of §10. |
| Interaction of our own transactions | Conflict ladder versus non-conflict ladder. README and methodology §9 and §10. |
| Competition inside the block | Pairwise tau on our signatures, gaps, contamination flag, two rollups. Methodology §10. |
| Send order | Low-fee-first burst. Sign of tau-b. Methodology §9.3. |
| Noise versus rule | Pre-registered calls, permutation p-value, `no_rule_detected` as a real outcome. Methodology §9.3 and §12. |

The "What this does not separate" sections (README and methodology §11) are part of validity. A design that claimed to isolate pack's `schedule_strategy`, or the priority floor, would be measuring something this path cannot see.

### Verifiability and reproducibility, 25%

| Judge action | What they use |
| --- | --- |
| Re-run the plan without spending | `docker compose up --build` |
| Re-run live, capped | `MODE=live` with `SPEND_CAP_SOL=0.05` and a mounted key |
| Check a claimed index | Signature and slot in the report, then `getBlock`, compared with `data/runs/.../blocks/<slot>.json` |
| Recompute a call | Per-slot table: `n`, tau-b, `priority_pairwise`, `send_pairwise`, `p_perm`. Rule in methodology §9.3. |
| See when it happened | Measurement log with a UTC date on every row |

<!-- TODO: sync with measure/ -->

Reproducibility of the numerical result is limited by mainnet: the same leader's next window is a different block. Reproducibility of the **procedure** is the requirement. The raw bodies make the **claim about a past slot** checkable without trusting our index.

### Analysis quality, 25%

| Expectation | Where it lives |
| --- | --- |
| Honesty about noise | Thresholds fixed in the methodology before the live sample. Report shows the statistics next to the label. |
| Null results allowed | Report section "Null results" gives the sentence to use, including what a null does not prove. |
| Limits stated | README and methodology §11. Report section "What this does not separate" asks which of those limits this run actually hit. |
| No mixed Alpenglow eras | Slot cutoff in the study header. |
| Dates on every measurement | Measurement log. |
| What went wrong | Its own section, with prompts that have to be answered from the log. |

### Engineering, 15%

| Expectation | Owner |
| --- | --- |
| Dry-run default | Pipeline, specified in the README. `docker compose up` must not submit. |
| Spend cap in code | Pipeline. Cap checked before each signature, persisted across restarts, hard maximum 0.05 SOL. |
| Logging | `run.log` plus `attempts.jsonl`. No secrets in either. |
| Containerization | Compose service `measure`, key via volume, config via env. |

<!-- TODO: sync with measure/ -->

This documentation patch does not claim the engineering points. It is the specification those points are reviewed against.

## Definition of done for a submission

1. `docker compose up --build` completes a mainnet dry-run: real schedule, transactions built, nothing sent, projected spend printed.
2. A live run with `MODE=live` has signed no more than 0.05 SOL, and the ledger file shows the total.
3. At least six identities and three client or fork versions are either measured or explicitly listed as missed in "What went wrong."
4. Every reported slot is ≥ 459,653,000, and the measurement log dates fall after the 21 October 2026 handoff.
5. `data/` contains signatures, slots, and raw `getBlock` bodies for every reported landing.
6. The report is a filled copy of the template, not the template.
7. No key material is in git history.

## TODO sync points

Search the repo for `TODO: sync with measure/`. As of this patch they sit on:

| Location | Assumption the pipeline must match or edit |
| --- | --- |
| README quickstart | Command is `docker compose up --build`. Dry-run needs no extra flag. |
| README live example | `MODE=live`, `SPEND_CAP_SOL=0.05`, `KEYPAIR_PATH` as a host path, `RPC_URL`. |
| README environment table | Those names, plus optional `VALIDATORS_APP_TOKEN`. In-container key path `/keys/id.json`, read-only. |
| README compose shape | Service `measure`, build `./measure`, data volume `./data:/data`. |
| README "What you should see" | Dry-run prints identities, versions, slots, ladder, projected spend. Live adds signature, slot, block index. |
| README raw data pointer | Run artifacts under `data/`. |
| Methodology §9.5 | Durable spend total under `data/`, survives restart, not reset by a new run directory. |
| Methodology §9.6 | `data/runs/<UTC>/` layout: `manifest.json`, `validators.json`, `spend.json`, `attempts.jsonl`, `blocks/<slot>.json`, `run.log`. |
| Report template, per-slot signatures | Same `blocks/<slot>.json` path. |
| This checklist | Compose command, cap ledger, and log paths above. |
