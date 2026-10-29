"""Experiment constants.

Fee numbers are lamports. Compute-unit prices are micro-lamports per CU.
Changing an arm changes the default-plan cost locked by ``measure/tests``.
"""

from __future__ import annotations

# Alpenglow consensus starts at slot 459,653,000 (epoch 1064, 21 Oct 2026).
# The first 5,000 slots of the epoch were still Tower BFT. The slot is the cutoff.
# Source: https://solana.com/upgrades/alpenglow
ALPENGLOW_ACTIVATION_EPOCH = 1064
ALPENGLOW_ACTIVATION_UTC = "2026-10-21T13:36:00Z"
ALPENGLOW_SOURCE = "https://solana.com/upgrades/alpenglow"
ALPENGLOW_CUTOFF_SLOT = 459_653_000
ALPENGLOW_GENESIS_CERT_SLOT = 459_652_999

LAMPORTS_PER_SOL = 1_000_000_000
# The bounty requires the whole live run to fit in about 0.05 SOL.
# The cap is a ceiling, not a target: the default plan is well under it,
# and configuration cannot raise it.
HARD_CAP_LAMPORTS = 50_000_000
DEFAULT_CAP_LAMPORTS = HARD_CAP_LAMPORTS

SIGNATURE_FEE_LAMPORTS = 5_000
MICROLAMPORTS_PER_LAMPORT = 1_000_000

# Leader rotation length used by the stake-weighted schedule.
ROTATION_SLOTS = 4

DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
DEFAULT_LOOKAHEAD_SLOTS = 16_000
# Planning the live run takes around 15s. At ~215 ms/slot that is ~70 slots,
# so 16 slots of lead expired before the first send. 150 slots is ~30s.
DEFAULT_MIN_LEAD_SLOTS = 150
DEFAULT_TARGET_VALIDATORS = 6
DEFAULT_MIN_CLIENT_FAMILIES = 3
DEFAULT_LEADER_PAGE = 4_000
MAX_BLOCKS_TO_FETCH = 500
# Net fees after sweep must stay near 0.0006 SOL. Peak capital, including
# rent that sweep returns, must stay near 0.003 SOL so a burner that has
# already parked rent in unsaved accounts can still run.
NET_BUDGET_LAMPORTS = 600_000
PEAK_BUDGET_LAMPORTS = 3_000_000
# Kept as the historical fees-plus-abandoned-rent ceiling. New plans are
# held to NET_BUDGET_LAMPORTS and PEAK_BUDGET_LAMPORTS instead.
DESIGN_BUDGET_LAMPORTS = 12_000_000

# Frozen after a devnet simulateTransaction on 2026-10-29 (slot 516359079).
# A 1-lamport system transfer plus compute-budget instructions consumed 450 CU.
# Devnet is not a measurement and does not appear in report tables.
# 1000 divides 1_000_000, so the priority-fee steps below are exact.
CU_LIMIT = 1_000
CU_LIMIT_PROBE = {
    "excluded_from_report": True,
    "network": "devnet",
    "probed_at": "2026-10-29",
    "slot": 516359079,
    "units_consumed": 450,
}
# getBlock must ask for version 1. Mainnet blocks contain SIMD-0385 v1
# transactions; maxSupportedTransactionVersion 0 rejects the whole block.
# Our probes stay legacy + ComputeBudget. The ladder's variable is the
# priority fee in lamports, which both formats spell.
MAX_SUPPORTED_TRANSACTION_VERSION = 1
TRANSACTION_FORMAT = "legacy+compute-budget"

# Same values for every leader in a run. Changing them is a new run.
# Warm the QUIC connection this many slots before the target slot, and
# submit the burst when the clock reaches target_slot - BURST_OFFSET_SLOTS
# (the end of the previous leader's last slot).
# Open the leader QUIC connection this many slots before the burst.
# A handshake from the last live host took ~1.7s, about 8 slots.
QUIC_WARMUP_SLOTS = 12
BURST_OFFSET_SLOTS = 1
RETRIES_INSIDE_WINDOW = False

# Default six rungs, low fee first. Unique integer priority scores.
# Shorten the non-conflict ladder before the conflict ladder if the cap
# cannot hold both. Do not drop below six identities or three families.
LADDER_PRIORITY_FEES = (0, 1, 2, 4, 8, 16)
CONFLICT_SLOT_OFFSET = 0
NONCONFLICT_SLOT_OFFSET = 2
TRANSFER_LAMPORTS = 1
# Rent-exempt minimum for a 0-byte account on mainnet after the 2026
# rent cut. Live runs replace this with getMinimumBalanceForRentExemption.
DEFAULT_RENT_LAMPORTS = 650_240

# Agave cost-model terms used only to check that integer priority scores
# are unique. Signature 720, write lock 300, plus the requested CU limit.
SIGNATURE_COST = 720
WRITE_LOCK_COST = 300

# Placeholder listed in older notes. Dry-run now generates an ephemeral
# key when KEYPAIR_PATH is unset. Do not fund this address.
DRY_RUN_PAYER = "8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u"

MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
MEMO_PROGRAM_V1 = "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo"
COMPUTE_BUDGET_PROGRAM_ID = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM_IDS = frozenset({MEMO_PROGRAM_ID, MEMO_PROGRAM_V1})

# Packet data size the TPU stream window is sized for. Our transactions are
# legacy and must stay under this so an unstaked QUIC stream can carry them.
PACKET_DATA_SIZE = 1232

USER_AGENT = "solana-validator-order-measure/0.1 (research; dry-run default)"

# getClusterNodes clientId -> family. Version strings are not a family key:
# Agave, JitoLabs, AgaveBam, and HarmonicAgave have all advertised "4.3.2".
CLIENT_ID_FAMILY = {
    "Agave": "agave",
    "JitoLabs": "jito-solana",
    "AgaveBam": "agave-bam",
    "Firedancer": "firedancer",
    "Frankendancer": "frankendancer",
    "HarmonicAgave": "harmonic-agave",
    "HarmonicFiredancer": "harmonic-firedancer",
    "FireBAM": "firebam",
    "Rakurai": "rakurai",
    "Raiku": "raiku",
}

# Round-robin order. Frankendancer support ended at the Alpenglow
# handoff; if one is still scheduled it is its own family and is not
# preferred over a current client.
FAMILY_PRIORITY = (
    "firedancer",
    "jito-solana",
    "agave",
    "harmonic-firedancer",
    "harmonic-agave",
    "agave-bam",
    "firebam",
    "rakurai",
    "raiku",
    "frankendancer",
)

HYPOTHESES = (
    {
        "id": "cu_price",
        "rank_field": "rank_cu_price",
        "arm_rank_field": "rank_arm_cu_price",
        "statement": (
            "Higher compute-unit price is included earlier. "
            "Equal prices break by ascending send index."
        ),
    },
    {
        "id": "priority_fee",
        "rank_field": "rank_priority_fee",
        "arm_rank_field": "rank_arm_priority_fee",
        "statement": (
            "Higher total prioritization fee is included earlier. "
            "Equal fees break by ascending send index."
        ),
    },
    {
        "id": "send_order",
        "rank_field": "rank_send_order",
        "arm_rank_field": "rank_arm_send_order",
        "statement": "Earlier send index is included earlier.",
    },
)
