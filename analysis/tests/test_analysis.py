from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analysis.core import (
    classify,
    exact_directional_p_value,
    kendall_tau_b,
    pair_counts,
    pooled_pair_shares,
    write_outputs,
)


class StatisticsTests(unittest.TestCase):
    def test_tau_and_exact_probability_for_strict_orders(self):
        fees = [0, 1, 2, 4, 8, 16]
        priority_indexes = [5, 4, 3, 2, 1, 0]
        arrival_indexes = [0, 1, 2, 3, 4, 5]
        self.assertEqual(kendall_tau_b(fees, priority_indexes), -1)
        self.assertEqual(kendall_tau_b(fees, arrival_indexes), 1)
        self.assertEqual(
            exact_directional_p_value(fees, priority_indexes), 1 / 720
        )
        self.assertEqual(
            exact_directional_p_value(fees, arrival_indexes), 1 / 720
        )

    def test_tau_b_corrects_for_ties(self):
        self.assertAlmostEqual(
            kendall_tau_b([1, 1, 2], [1, 2, 3]), 2 / (6 ** 0.5)
        )

    def test_pair_counts_separate_reversed_predictions(self):
        rows = [
            {
                "priority_fee_lamports": fee,
                "send_index": send,
                "block_index": block,
            }
            for fee, send, block in zip(
                [0, 1, 2, 4], range(4), [3, 2, 1, 0]
            )
        ]
        self.assertEqual(pair_counts(rows), (6, 0, 6))

    def test_four_rungs_are_inconclusive_even_when_strict(self):
        self.assertEqual(classify(4, -1, 1 / 24, 1, 0), "inconclusive")

    def test_bootstrap_is_repeatable_and_cluster_based(self):
        rows = [
            {
                "ladder": ladder,
                "priority_pairs": priority,
                "arrival_pairs": 10 - priority,
                "pairs": 10,
            }
            for ladder, priority in [
                ("conflict", 10),
                ("conflict", 8),
                ("nonconflict", 2),
                ("nonconflict", 4),
            ]
        ]
        first = pooled_pair_shares(rows, seed=7, replicates=100)
        second = pooled_pair_shares(rows, seed=7, replicates=100)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["bootstrap_unit"], "validator-run ladder")


class RealRunIntegrationTests(unittest.TestCase):
    def test_supplied_runs_generate_complete_evidence(self):
        data = Path(__file__).parents[2] / "data" / "runs"
        with TemporaryDirectory() as temporary:
            result = write_outputs(
                data, Path(temporary), seed=20261029, replicates=200
            )
            self.assertEqual(len(result["ladder_statistics"]), 24)
            evidence_lines = (
                Path(temporary) / "evidence_transactions.csv"
            ).read_text().splitlines()
            self.assertEqual(len(evidence_lines) - 1, 132)
            run4_nonconflict = [
                row
                for row in result["ladder_statistics"]
                if row["run"] == "20261029T100356Z"
                and row["ladder"] == "nonconflict"
            ]
            self.assertEqual({row["call"] for row in run4_nonconflict}, {"inconclusive"})
            strict_conflict_clients = {
                row["client"]
                for row in result["ladder_statistics"]
                if row["ladder"] == "conflict"
                and row["call"] == "strict_priority"
            }
            self.assertTrue(
                {"Agave", "Firedancer", "JitoLabs", "AgaveBam"}
                <= strict_conflict_clients
            )


if __name__ == "__main__":
    unittest.main()
