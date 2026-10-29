"""Selection and the two ladders."""

from __future__ import annotations

from measure.budget import projected_outlay_lamports
from measure.constants import LADDER_PRIORITY_FEES, NET_BUDGET_LAMPORTS, PEAK_BUDGET_LAMPORTS
from measure.offline_snapshot import IDENTITIES, build_offline_snapshot
from measure.plan import build_experiment
from measure.select import ensure_stake_diversity


def _plan():
    return build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tail_is_complete=True,
        cap_remaining_lamports=50_000_000,
        main_payer="8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u",
    )


def test_selects_six_current_families_and_skips_frankendancer():
    plan = _plan()
    assert plan.selection_ok
    assert plan.regime == "after"
    families = [target.client_family for target in plan.targets]
    assert families == [
        "firedancer",
        "jito-solana",
        "agave",
        "harmonic-firedancer",
        "harmonic-agave",
        "agave-bam",
    ]
    assert "frankendancer" not in families
    assert IDENTITIES["unknown"] not in {target.identity for target in plan.targets}
    versions = {target.client_family: target.version for target in plan.targets}
    assert versions["firedancer"] == versions["harmonic-firedancer"] == "26.10.4"
    assert versions["agave"] == versions["jito-solana"] == "4.3.2"
    assert plan.projected_outlay_lamports == projected_outlay_lamports(
        n_leaders=6,
        conflict_rungs=plan.conflict_rungs,
        nonconflict_rungs=plan.nonconflict_rungs,
    )
    assert plan.total_fee_lamports <= NET_BUDGET_LAMPORTS
    assert plan.projected_outlay_lamports <= PEAK_BUDGET_LAMPORTS
    assert all(len(target.assigned) == 1 and target.assigned[0].path == "tpu" for target in plan.targets)


def test_ladders_are_low_fee_first_and_do_not_share_nonconflict_accounts():
    plan = _plan()
    conflict = [tx for tx in plan.txs if tx.ladder == "conflict" and tx.identity == plan.targets[0].identity]
    nonconflict = [
        tx for tx in plan.txs if tx.ladder == "nonconflict" and tx.identity == plan.targets[0].identity
    ]
    assert [tx.priority_fee_lamports for tx in conflict] == list(LADDER_PRIORITY_FEES)
    assert [tx.send_index for tx in conflict] == list(range(6))
    assert len({tx.payer for tx in conflict}) == 1
    assert len({tx.recipient for tx in conflict}) == 1
    assert conflict[0].payer == conflict[0].recipient
    assert len({tx.payer for tx in nonconflict}) == len(plan.nonconflict_rungs)
    assert {tx.payer for tx in nonconflict} == {tx.recipient for tx in nonconflict}
    assert {tx.payer for tx in nonconflict}.isdisjoint({tx.payer for tx in conflict})
    assert conflict[0].planned_slot != nonconflict[0].planned_slot
    assert all(tx.cu_limit == 1000 for tx in conflict + nonconflict)
    assert all(tx.transfer_lamports == 1 for tx in conflict + nonconflict)
    assert not any(tx.memo for tx in plan.txs)
    scores = [tx.priority_score for tx in conflict]
    assert scores == sorted(scores)
    assert len(set(scores)) == 6


def _plan_with_order(order, seed=None):
    return build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tail_is_complete=True,
        cap_remaining_lamports=50_000_000,
        main_payer="8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u",
        send_order=order,
        send_order_seed=seed,
    )


def test_default_send_order_is_ascending_and_matches_runs_1_to_4():
    plan = _plan()
    assert plan.send_order == "ascending"
    assert plan.send_order_seed is None


def test_shuffled_send_order_breaks_fee_send_collinearity():
    from measure.stats import kendall_tau_b

    plan = _plan_with_order("shuffled")
    assert plan.send_order_seed is not None
    assert _plan_with_order("shuffled").send_order_seed == plan.send_order_seed
    orders = set()
    for target in plan.targets:
        for ladder in ("conflict", "nonconflict"):
            txs = [tx for tx in plan.txs if tx.identity == target.identity and tx.ladder == ladder]
            txs.sort(key=lambda tx: tx.send_index)
            fees = [tx.priority_fee_lamports for tx in txs]
            assert sorted(fees) == sorted(
                plan.conflict_rungs if ladder == "conflict" else plan.nonconflict_rungs
            )
            assert [tx.send_index for tx in txs] == list(range(len(txs)))
            tau = kendall_tau_b(fees, [tx.send_index for tx in txs])
            assert abs(tau) <= 1 / 3 + 1e-9
            orders.add(tuple(fees))
            if ladder == "nonconflict":
                rung_payer = {
                    tx.priority_fee_lamports: tx.payer
                    for tx in plan.txs
                    if tx.ladder == "nonconflict" and tx.identity == plan.targets[0].identity
                }
                assert all(rung_payer[tx.priority_fee_lamports] == tx.payer for tx in txs)
    assert len(orders) > 2
    funding = {tx.recipient: tx.transfer_lamports for tx in plan.txs if tx.role == "funding"}
    ascending = _plan_with_order("ascending")
    assert sorted(funding.values()) == sorted(
        tx.transfer_lamports for tx in ascending.txs if tx.role == "funding"
    )
    assert plan.total_fee_lamports == ascending.total_fee_lamports


def test_conflict_only_plan_needs_no_funding_or_rent():
    plan = build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tail_is_complete=True,
        cap_remaining_lamports=50_000_000,
        main_payer="8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u",
        send_order="shuffled",
        ladders="conflict",
    )
    assert plan.nonconflict_rungs == ()
    assert {tx.ladder for tx in plan.txs} == {"conflict"}
    assert len(plan.txs) == 36
    assert plan.funding_principal_lamports == 0
    assert plan.total_fee_lamports == 180_186
    assert plan.projected_outlay_lamports == 180_186
    assert not getattr(plan, "ephemeral_signers", {})


def test_descending_send_order_is_highest_fee_first():
    plan = _plan_with_order("descending")
    target = plan.targets[0].identity
    conflict = sorted(
        (tx for tx in plan.txs if tx.identity == target and tx.ladder == "conflict"),
        key=lambda tx: tx.send_index,
    )
    assert [tx.priority_fee_lamports for tx in conflict] == list(reversed(LADDER_PRIORITY_FEES))


def test_send_order_is_validated(monkeypatch):
    import pytest

    from measure.config import make_config
    from measure.errors import ConfigError

    monkeypatch.setenv("SEND_ORDER", "random")
    with pytest.raises(ConfigError):
        make_config()
    monkeypatch.setenv("SEND_ORDER", "shuffled")
    monkeypatch.setenv("SEND_ORDER_SEED", "7")
    config = make_config()
    assert (config.send_order, config.send_order_seed) == ("shuffled", 7)
    assert make_config(send_order="ascending").send_order == "ascending"


def test_arrival_call_uses_send_rank_when_order_is_shuffled():
    from measure.stats import slot_call

    # Fees shuffled against send order; the block reproduces send order exactly.
    assert (
        slot_call(
            n_landed=6,
            tau=0.067,
            p_perm=0.5,
            priority_pairwise=0.47,
            send_pairwise=1.0,
            tau_send=1.0,
            p_send=1 / 720,
        )
        == "arrival"
    )


def test_nonconflict_shortens_before_the_conflict_ladder():
    plan = build_experiment(
        build_offline_snapshot(),
        experiment_id="e1071-test",
        min_lead_slots=16,
        target_validators=6,
        min_client_families=3,
        tail_is_complete=True,
        cap_remaining_lamports=250_000,
        main_payer="8E15T21wGpaMNdcReLFFrBHXCoZdaMJpNV89n1wYrp1u",
    )
    assert plan.conflict_rungs == LADDER_PRIORITY_FEES
    assert len(plan.nonconflict_rungs) < 6
    assert len(plan.targets) == 6


def test_stake_swap_keeps_three_families():
    def candidate(identity, family, stake):
        return {
            "identity": identity,
            "client_family": family,
            "stake_lamports": stake,
            "version": "1",
        }

    picked = [candidate(f"big{i}", f"fam{i}", 1_000_000 - i) for i in range(6)]
    extras = [candidate(f"also{i}", "fam0", 500_000 - i) for i in range(4)]
    outsider = candidate("small", "fam0", 10)
    pool = picked + extras + [outsider]
    updated, note = ensure_stake_diversity(picked, pool, min_families=3)
    assert any(item["identity"] == "small" for item in updated)
    assert note and "small" in note
    assert len({item["client_family"] for item in updated}) >= 3
