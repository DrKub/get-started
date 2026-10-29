"""Experiment constants.

Fee numbers are lamports. Compute-unit prices are micro-lamports per CU.
Changing an arm changes the default-plan cost locked by ``measure/tests``.
"""

from __future__ import annotations

# Alpenglow activated at the start of epoch 1064.
# Source: https://solana.com/upgrades/alpenglow (updated October 2026).
ALPENGLOW_ACTIVATION_EPOCH = 1064
ALPENGLOW_ACTIVATION_UTC = "2026-10-21T13:36:00Z"
ALPENGLOW_SOURCE = "https://solana.com/upgrades/alpenglow"

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
DEFAULT_MIN_LEAD_SLOTS = 16
DEFAULT_TARGET_VALIDATORS = 6
DEFAULT_MIN_CLIENT_FAMILIES = 3
DEFAULT_TPU_REPEATS = 2
DEFAULT_RPC_REPEATS = 1
DEFAULT_LEADER_PAGE = 4_000
MAX_BLOCKS_TO_FETCH = 500

# Placeholder fee payer for unsigned dry-run messages. The secret was
# discarded at generation time; do not send SOL to this address.
DRY_RUN_PAYER = "8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u"

# Ladder: identical CU limit so compute-unit price and total priority fee
# rank the same way. Sent low-fee first, so send order is the reverse of
# both priority hypotheses.
LADDER_CU_LIMIT = 20_000
LADDER_PRIORITY_FEES = (0, 1_000, 5_000, 20_000, 50_000, 200_000)

# Equal priority, sent in seq order, to read the tie-break.
ARRIVAL_COUNT = 4
ARRIVAL_CU_LIMIT = 20_000
ARRIVAL_PRIORITY_FEE = 10_000

# Same-window contrast of "price per CU" against "total priority fee".
# metric-price: higher CU price, lower total fee.
# metric-total: lower CU price, higher total fee.
METRIC_PRICE_CU_LIMIT = 20_000
METRIC_PRICE_FEE = 40_000
METRIC_TOTAL_CU_LIMIT = 80_000
METRIC_TOTAL_FEE = 80_000

# Slot offsets inside a 4-slot rotation. Short windows collapse to offset 0.
LADDER_SLOT_OFFSET = 0
ARRIVAL_SLOT_OFFSET = 2
METRIC_SLOT_OFFSET = 3

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

# Round-robin order. Schedulers most likely to differ come first so a
# six-validator set is not six copies of the heaviest stake pool.
FAMILY_PRIORITY = (
    "firedancer",
    "frankendancer",
    "harmonic-firedancer",
    "jito-solana",
    "agave",
    "harmonic-agave",
    "agave-bam",
    "firebam",
    "rakurai",
    "raiku",
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
