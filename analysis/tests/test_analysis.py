import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analysis.core import (
    classify,
    exact_directional_p_value,
    kendall_tau_b,
    load_raw_block,
    normalize_block,
    pair_counts,
    pooled_pair_shares,
    raw_block_checks,
    rollup,
    send_order_label,
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

    def test_arrival_uses_send_rank_when_send_order_is_shuffled(self):
        fees = [4, 2, 0, 1, 16, 8]
        sends = list(range(6))
        indexes = [0, 1, 2, 3, 4, 5]
        tau = kendall_tau_b(fees, indexes)
        self.assertGreater(tau, -0.6)
        self.assertLess(tau, 0.6)
        self.assertEqual(
            classify(
                6,
                tau,
                exact_directional_p_value(fees, indexes),
                0.4,
                1.0,
                kendall_tau_b(sends, indexes),
                exact_directional_p_value(sends, indexes),
            ),
            "arrival",
        )
        self.assertEqual(send_order_label(fees, sends), "shuffled")
        self.assertEqual(send_order_label([0, 1, 2], [0, 1, 2]), "ascending")

    def test_rollup_follows_the_methodology_list(self):
        self.assertEqual(rollup(["strict_priority", "strict_priority"]), "strict_priority")
        self.assertEqual(rollup(["strict_priority", "priority"]), "priority")
        self.assertEqual(rollup(["priority", "no_rule_detected"]), "mixed_across_slots")
        self.assertEqual(rollup([]), "inconclusive")

    def test_raw_block_checks_flag_foreign_writers(self):
        def tx(signature, *writable):
            return {
                "transaction": {
                    "signatures": [signature],
                    "accountKeys": [
                        {"pubkey": key, "writable": True} for key in writable
                    ]
                    + [{"pubkey": "11111111111111111111111111111111", "writable": False}],
                }
            }

        block = {
            "rewards": [{"pubkey": "Leader", "rewardType": "Fee"}],
            "transactions": [
                tx("a", "Funder"),
                tx("foreign", "Funder", "Someone"),
                tx("b", "Funder"),
                tx("other", "Elsewhere"),
            ],
        }
        rows = [
            {"signature": "a", "block_index": 0},
            {"signature": "b", "block_index": 2},
        ]
        checks = raw_block_checks(block, "conflict", rows, {"a", "b"})
        self.assertEqual(checks["block_leader"], "Leader")
        self.assertEqual(checks["ladder_writable_accounts"], ["Funder"])
        self.assertEqual(checks["foreign_writers"], 1)
        self.assertTrue(checks["contaminated"])
        with self.assertRaises(ValueError):
            raw_block_checks(block, "nonconflict", rows, {"a", "b"})
        with self.assertRaises(ValueError):
            raw_block_checks(block, "conflict", [{"signature": "a", "block_index": 1}], {"a"})

    def test_full_and_compact_blocks_give_the_same_checks(self):
        def keys(*writable):
            return [{"pubkey": key, "signer": i == 0, "source": "transaction", "writable": True}
                    for i, key in enumerate(writable)] + [
                {"pubkey": "11111111111111111111111111111111", "signer": False,
                 "source": "transaction", "writable": False}
            ]

        def full_tx(signature, *writable):
            return {
                "meta": {"err": None, "fee": 5000, "logMessages": ["x" * 1000]},
                "transaction": {
                    "signatures": [signature],
                    "message": {"accountKeys": keys(*writable), "instructions": []},
                },
                "version": "legacy",
            }

        full = {
            "rewards": [{"pubkey": "Leader", "rewardType": "Fee"}],
            "transactions": [
                full_tx("a", "Funder"),
                full_tx("foreign", "Someone"),
                full_tx("b", "Funder"),
            ],
        }
        rows = [{"signature": "a", "block_index": 0}, {"signature": "b", "block_index": 2}]
        with TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "blocks").mkdir()
            (run_dir / "blocks" / "7.json").write_text(json.dumps({"result": full}))
            from_full = raw_block_checks(load_raw_block(run_dir, 7), "conflict", rows, {"a", "b"})

            from measure.collector import compact_block
            from measure.io_util import dump_json_gz

            (run_dir / "blocks" / "7.json").unlink()
            dump_json_gz(run_dir / "blocks" / "7.json.gz", compact_block(full))
            compact = load_raw_block(run_dir, 7)
            self.assertNotIn("logMessages", compact["transactions"][0]["meta"])
            from_compact = raw_block_checks(compact, "conflict", rows, {"a", "b"})
        self.assertEqual(from_full, from_compact)
        self.assertEqual(from_full["block_leader"], "Leader")
        self.assertEqual(from_full["ladder_writable_accounts"], ["Funder"])

    def test_raw_json_encoding_without_writable_flags_is_rejected(self):
        block = {"transactions": [{"transaction": {"signatures": ["a"],
                                                   "message": {"accountKeys": ["Funder"]}}}]}
        with self.assertRaises(ValueError):
            normalize_block(block)

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
            self.assertEqual(len(result["ladder_statistics"]), 30)
            evidence_lines = (
                Path(temporary) / "evidence_transactions.csv"
            ).read_text().splitlines()
            self.assertEqual(len(evidence_lines) - 1, 168)
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
            stats = result["ladder_statistics"]
            self.assertTrue(all(row["leader_matches"] is True for row in stats))
            self.assertTrue(all(row["contaminated"] is False for row in stats))
            run5 = "20261029T110321Z"
            ascending = [row for row in stats if row["run"] != run5]
            shuffled = [row for row in stats if row["run"] == run5]
            self.assertEqual({row["send_order"] for row in ascending}, {"ascending"})
            self.assertEqual({row["tau_fee_send"] for row in ascending}, {1.0})
            self.assertEqual(len(shuffled), 6)
            self.assertEqual({row["ladder"] for row in shuffled}, {"conflict"})
            self.assertTrue(all(abs(row["tau_fee_send"]) <= 1 / 3 + 1e-9 for row in shuffled))
            self.assertEqual(
                {row["client"] for row in shuffled if row["call"] == "strict_priority"},
                {"Agave", "Firedancer", "JitoLabs", "AgaveBam"},
            )
            inverse = [row for row in shuffled if row["client"] == "HarmonicFiredancer"][0]
            self.assertAlmostEqual(inverse["tau_b"], 13 / 15)
            self.assertEqual(inverse["call"], "no_rule_detected")
            early = sorted(
                (row["run"], row["client"]) for row in stats if row["slot_minus_target"] != 0
            )
            self.assertEqual(
                early,
                [("20261029T094113Z", "Firedancer"), ("20261029T100356Z", "AgaveBam")],
            )
            conflict_locks = {
                row["run"]: len(row["ladder_writable_accounts"])
                for row in stats
                if row["ladder"] == "conflict"
            }
            self.assertEqual(
                conflict_locks,
                {"20261029T094113Z": 2, "20261029T100356Z": 1, "20261029T110321Z": 1},
            )
            pooled = {row["scope"]: row for row in result["pooled_pair_shares"]}
            self.assertAlmostEqual(pooled["conflict"]["priority_pair_share"], 237 / 270)
            self.assertAlmostEqual(pooled["all"]["priority_pair_share"], 298 / 396)
            headline = {
                (row["client"], row["ladder"]): row["headline_call"]
                for row in result["validator_rollups"]
            }
            self.assertEqual(
                headline[("HarmonicFiredancer", "conflict")], "mixed_across_slots"
            )
            self.assertEqual(headline[("Agave", "nonconflict")], "arrival")
            self.assertTrue((Path(temporary) / "EVIDENCE.md").exists())


if __name__ == "__main__":
    unittest.main()
