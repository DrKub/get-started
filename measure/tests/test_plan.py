"""Selection diversity and the pre-registered ordering hypotheses."""

from __future__ import annotations

from measure.offline_snapshot import IDENTITIES, build_offline_snapshot
from measure.plan import build_experiment


def _plan():
    return build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tpu_repeats=2,
        rpc_repeats=1,
        tail_is_complete=True,
    )


def test_selects_six_forks_and_does_not_merge_colliding_versions():
    plan = _plan()
    assert plan.selection_ok
    assert plan.regime == "after"
    families = [target.client_family for target in plan.targets]
    assert families == [
        "firedancer",
        "frankendancer",
        "harmonic-firedancer",
        "jito-solana",
        "agave",
        "harmonic-agave",
    ]
    versions = {target.client_family: target.version for target in plan.targets}
    assert versions["firedancer"] == versions["harmonic-firedancer"] == "26.10.4"
    assert versions["agave"] == versions["jito-solana"] == "4.3.2"
    assert versions["firedancer"] == "26.10.4"
    identities = {target.identity for target in plan.targets}
    assert IDENTITIES["agave-bam"] not in identities
    assert IDENTITIES["unknown"] not in identities
    assert IDENTITIES["no-tpu"] not in identities
    assert plan.exclusion_counts["missing_client_id"] >= 1
    assert plan.exclusion_counts["unknown_client_id"] >= 1
    assert plan.exclusion_counts["missing_tpu_quic"] >= 1
    assert plan.exclusion_counts["leader_not_in_cluster_nodes"] >= 1
    assert plan.total_fee_lamports == 6 * 3 * 496_000
    assert plan.total_fee_lamports < 50_000_000


def test_ladder_inverts_send_order_and_metric_arms_disagree():
    plan = _plan()
    first = next(target for target in plan.targets if target.client_family == "firedancer")
    even = [tx for tx in plan.txs if tx.identity == first.identity and tx.repeat_index == 0]
    odd = [tx for tx in plan.txs if tx.identity == first.identity and tx.repeat_index == 1]
    ladder = [tx for tx in even if tx.arm == "ladder"]
    assert [tx.priority_fee_lamports for tx in ladder] == sorted(
        tx.priority_fee_lamports for tx in ladder
    )
    assert ladder[0].rank_send_order < ladder[-1].rank_send_order
    assert ladder[0].rank_cu_price > ladder[-1].rank_cu_price
    assert ladder[0].fee_lamports == 5_000
    assert all(tx.slot_offset == 0 for tx in ladder)
    arrival = [tx for tx in even if tx.arm == "arrival"]
    assert len({tx.cu_price_micro_lamports for tx in arrival}) == 1
    assert [tx.rank_arm_cu_price for tx in arrival] == sorted(tx.rank_arm_cu_price for tx in arrival)
    assert all(tx.slot_offset == 2 for tx in arrival)

    def pair(rows):
        return (
            next(tx for tx in rows if tx.arm == "metric-price"),
            next(tx for tx in rows if tx.arm == "metric-total"),
        )

    even_price, even_total = pair(even)
    odd_price, odd_total = pair(odd)
    assert even_price.rank_cu_price < even_total.rank_cu_price
    assert even_total.rank_priority_fee < even_price.rank_priority_fee
    assert even_price.rank_send_order < even_total.rank_send_order
    assert odd_price.rank_cu_price < odd_total.rank_cu_price
    assert odd_total.rank_priority_fee < odd_price.rank_priority_fee
    assert odd_total.rank_send_order < odd_price.rank_send_order
    assert {tx.path for tx in even} == {"tpu"}
    rpc_rows = [tx for tx in plan.txs if tx.identity == first.identity and tx.repeat_index == 2]
    assert {tx.path for tx in rpc_rows} == {"rpc"}


def test_two_families_are_not_enough():
    snapshot = build_offline_snapshot()
    keep = {
        IDENTITIES["firedancer"],
        IDENTITIES["jito-solana"],
        IDENTITIES["filler"],
    }
    snapshot.leaders = [item for item in snapshot.leaders if item in keep]
    snapshot.nodes = [
        node
        for node in snapshot.nodes
        if node["pubkey"] in {IDENTITIES["firedancer"], IDENTITIES["jito-solana"]}
    ]
    plan = build_experiment(
        snapshot,
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tpu_repeats=2,
        rpc_repeats=1,
        tail_is_complete=True,
    )
    assert plan.selection_ok is False
    assert plan.selection_reason
