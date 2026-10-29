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
