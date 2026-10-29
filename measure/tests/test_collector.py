"""Block parsing and hypothesis scoring."""

from __future__ import annotations

from measure.analyze import pairwise_agreement, summarize_hypotheses
from measure.base58 import b58encode
from measure.collector import block_context, build_observations, parse_block
from measure.memo import render_memo
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price


def _ix(program_id: str, data: bytes, *, parsed=None, program=None) -> dict:
    row = {"programId": program_id, "data": b58encode(data), "accounts": []}
    if parsed is not None:
        row["parsed"] = parsed
    if program is not None:
        row["program"] = program
    return row


def _tx(signature: str, instructions: list, *, fee: int, err=None, logs=None, keys=None) -> dict:
    return {
        "meta": {
            "computeUnitsConsumed": 1500,
            "costUnits": 20000,
            "err": err,
            "fee": fee,
            "logMessages": logs or [],
        },
        "transaction": {
            "message": {
                "accountKeys": keys
                or [{"pubkey": "Payer111", "signer": True, "source": "transaction", "writable": True}],
                "instructions": instructions,
                "recentBlockhash": "11111111111111111111111111111111",
            },
            "signatures": [signature],
        },
        "version": 0,
    }


def _memo(text: str) -> dict:
    return _ix(
        "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
        text.encode(),
        parsed=text,
        program="spl-memo",
    )


def test_parse_block_positions_fees_and_missing_entries():
    high = render_memo(
        experiment_id="e1071-test",
        seq=1,
        arm="ladder",
        send_index=5,
        path="tpu",
        priority_fee_lamports=200_000,
        cu_price_micro_lamports=10_000_000,
        cu_limit=20_000,
    )
    low = render_memo(
        experiment_id="e1071-test",
        seq=0,
        arm="ladder",
        send_index=0,
        path="tpu",
        priority_fee_lamports=0,
        cu_price_micro_lamports=0,
        cu_limit=20_000,
    )
    limit = bytes(set_compute_unit_limit(20_000).data)
    high_price = bytes(set_compute_unit_price(10_000_000).data)
    low_price = bytes(set_compute_unit_price(0).data)
    block = parse_block(
        {
            "blockHeight": 10,
            "blockTime": 1_761_800_100,
            "blockhash": "abc",
            "parentSlot": 4,
            "transactions": [
                _tx("sig-other", [_ix("11111111111111111111111111111111", b"\x02\x00\x00\x00")], fee=5000),
                _tx("sig-high", [_ix("ComputeBudget111111111111111111111111111111", limit), _ix("ComputeBudget111111111111111111111111111111", high_price), _memo(high)], fee=205_000),
                "not-a-transaction",
                _tx("sig-low", [_ix("ComputeBudget111111111111111111111111111111", limit), _ix("ComputeBudget111111111111111111111111111111", low_price), _memo(low)], fee=5_000, err={"InstructionError": [2, "Custom"]}),
            ],
        },
        slot=5,
    )
    assert block.entry_index_available is False
    assert [tx.index for tx in block.transactions] == [0, 1, 3]
    assert block.transactions[1].cu_price_micro_lamports == 10_000_000
    assert block.transactions[1].cu_limit == 20_000
    assert block.transactions[1].memo == high
    assert block.transactions[1].fee_payer == "Payer111"
    plans = [
        {"memo": high, "seq": 1, "identity": "Validator", "experiment_id": "e1071-test", "window_id": "w", "arm": "ladder", "rank_cu_price": 0, "rank_priority_fee": 0, "rank_send_order": 1, "rank_arm_cu_price": 0, "rank_arm_priority_fee": 0, "rank_arm_send_order": 1},
        {"memo": low, "seq": 0, "identity": "Validator", "experiment_id": "e1071-test", "window_id": "w", "arm": "ladder", "rank_cu_price": 1, "rank_priority_fee": 1, "rank_send_order": 0, "rank_arm_cu_price": 1, "rank_arm_priority_fee": 1, "rank_arm_send_order": 0},
        {"memo": "ib|1|e1071-test|2|ladder|1|tpu|p1000|c50000|l20000", "seq": 2, "identity": "Validator", "experiment_id": "e1071-test", "window_id": "w", "arm": "ladder"},
    ]
    observations = build_observations([block], plans, {1: "sig-high", 0: "sig-low"})
    by_seq = {row["seq"]: row for row in observations}
    assert by_seq[1]["landed"] is True
    assert by_seq[1]["block_index"] == 1
    assert by_seq[1]["slot"] == 5
    assert by_seq[1]["entry_index"] is None
    assert by_seq[1]["fee_lamports"] == 205_000
    assert by_seq[0]["execution_err"] == {"InstructionError": [2, "Custom"]}
    assert by_seq[2]["landed"] is False
    context = block_context(block, {"sig-high", "sig-low"})
    assert context["tx_count"] == 3
    assert context["our_tx_count"] == 2
    assert context["entry_index_available"] is False
    # High price landed before low price: agrees with cu_price, disagrees with send order.
    price = pairwise_agreement(observations, "rank_cu_price")
    send = pairwise_agreement(observations, "rank_send_order")
    assert price["pairs"] == 1 and price["agree"] == 1
    assert send["pairs"] == 1 and send["agree"] == 0
    assert summarize_hypotheses(observations)["hypotheses"]


def test_parsed_compute_budget_shape():
    block = parse_block(
        {
            "transactions": [
                _tx(
                    "sig",
                    [
                        {
                            "parsed": {"info": {"units": 20000}, "type": "setComputeUnitLimit"},
                            "program": "compute-budget",
                            "programId": "ComputeBudget111111111111111111111111111111",
                        },
                        {
                            "parsed": {
                                "info": {"microLamports": 123},
                                "type": "setComputeUnitPrice",
                            },
                            "program": "compute-budget",
                            "programId": "ComputeBudget111111111111111111111111111111",
                        },
                    ],
                    fee=5000,
                )
            ]
        },
        slot=1,
    )
    assert block.transactions[0].cu_limit == 20000
    assert block.transactions[0].cu_price_micro_lamports == 123


def test_compact_block_keeps_the_evidence_and_drops_the_bulk(tmp_path):
    import gzip
    import json
    from pathlib import Path

    from measure.collector import compact_block
    from measure.io_util import dump_json_gz

    full = json.loads(
        (Path(__file__).parent / "fixtures" / "get_block_sample.json").read_text(encoding="utf-8")
    )
    full = full.get("result", full)
    full["rewards"] = [{"pubkey": "Leader111", "rewardType": "Fee", "lamports": 1}]
    compact = compact_block(full)

    assert compact["rewards"] == full["rewards"]
    assert len(compact["transactions"]) == len(full["transactions"])
    for before, after in zip(full["transactions"], compact["transactions"]):
        assert after["transaction"]["signatures"] == before["transaction"]["signatures"]
        assert after["transaction"]["accountKeys"] == before["transaction"]["message"]["accountKeys"]
        assert after["meta"]["fee"] == before["meta"]["fee"]
        assert "logMessages" not in after["meta"]
        assert "message" not in after["transaction"]

    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    dump_json_gz(first, compact)
    dump_json_gz(second, compact_block(compact))
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(gzip.decompress(first.read_bytes())) == compact

    fee_payers = [tx.fee_payer for tx in parse_block(full, 1).transactions]
    assert [tx.fee_payer for tx in parse_block(compact, 1).transactions] == fee_payers


def test_compact_block_derives_writable_flags_from_raw_json_encoding():
    from measure.collector import compact_block

    raw = {
        "transactions": [
            {
                "meta": {
                    "fee": 5000,
                    "loadedAddresses": {"writable": ["LutW"], "readonly": ["LutR"]},
                },
                "transaction": {
                    "signatures": ["s"],
                    "message": {
                        "accountKeys": ["Payer", "CoSigner", "Dest", "Program"],
                        "header": {
                            "numRequiredSignatures": 2,
                            "numReadonlySignedAccounts": 1,
                            "numReadonlyUnsignedAccounts": 1,
                        },
                    },
                },
                "version": 0,
            }
        ]
    }
    keys = compact_block(raw)["transactions"][0]["transaction"]["accountKeys"]
    assert [(k["pubkey"], k["signer"], k["writable"]) for k in keys] == [
        ("Payer", True, True),
        ("CoSigner", True, False),
        ("Dest", False, True),
        ("Program", False, False),
        ("LutW", False, True),
        ("LutR", False, False),
    ]
