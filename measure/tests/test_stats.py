"""Pre-registered slot calls."""

from measure.stats import kendall_tau_b, pairwise_fractions, permutation_p_value, slot_call


def test_priority_and_arrival_signs():
    fees = [0, 1, 2, 4, 8, 16]
    sends = [0, 1, 2, 3, 4, 5]
    priority_index = [5, 4, 3, 2, 1, 0]
    arrival_index = [0, 1, 2, 3, 4, 5]
    assert kendall_tau_b(fees, priority_index) == -1
    assert kendall_tau_b(sends, priority_index) == -1
    assert kendall_tau_b(fees, arrival_index) == 1
    priority = pairwise_fractions(fees, priority_index, sends)
    arrival = pairwise_fractions(fees, arrival_index, sends)
    assert priority["priority_pairwise"] == 1
    assert arrival["send_pairwise"] == 1
    assert permutation_p_value(fees, priority_index) == 1 / 720
    assert (
        slot_call(
            n_landed=6,
            tau=-1,
            p_perm=1 / 720,
            priority_pairwise=1,
            send_pairwise=0,
        )
        == "strict_priority"
    )
    assert (
        slot_call(
            n_landed=4,
            tau=-1,
            p_perm=0.01,
            priority_pairwise=1,
            send_pairwise=0,
        )
        == "inconclusive"
    )
    assert (
        slot_call(
            n_landed=6,
            tau=0.1,
            p_perm=0.4,
            priority_pairwise=0.5,
            send_pairwise=0.5,
        )
        == "no_rule_detected"
    )
