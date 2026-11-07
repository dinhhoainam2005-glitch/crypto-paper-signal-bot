import json
import tempfile
import unittest
from array import array
from pathlib import Path

from market_structure.h001_evaluator import (
    FrameSeries,
    DAY_NS,
    _outcome_split,
    _quality_gate,
    _range_marked,
    assign_decile,
    evaluate_epoch,
    label_outcome,
    moving_block_bootstrap_ci,
    nearest_rank_deciles,
)


def _series(mids: list[float]) -> FrameSeries:
    return FrameSeries(
        end_ns=array("q", [(index + 1) * 1_000_000_000 for index in range(len(mids))]),
        mid=array("d", mids),
        funding=bytearray([3] * len(mids)),
        stale=bytearray(len(mids)),
    )


class H001EvaluatorTests(unittest.TestCase):
    def test_nearest_rank_deciles_and_assignment_are_deterministic(self) -> None:
        boundaries = nearest_rank_deciles(range(1, 101))
        self.assertEqual(boundaries, tuple(float(value) for value in range(10, 100, 10)))
        self.assertEqual(assign_decile(10.0, boundaries), 0)
        self.assertEqual(assign_decile(10.1, boundaries), 1)
        self.assertEqual(assign_decile(100.0, boundaries), 9)

    def test_outcome_is_strictly_post_decision_and_direction_signed(self) -> None:
        series = _series([100.0] + [100.0 + index * 0.01 for index in range(1, 121)])
        outcome = label_outcome(
            series,
            0,
            "UP",
            sweep_extreme_mid=100.5,
            impulse_direction="UP",
        )
        self.assertAlmostEqual(outcome["signed_return_10s_bps"], 10.0)
        self.assertAlmostEqual(outcome["signed_return_30s_bps"], 30.0)
        self.assertAlmostEqual(outcome["signed_return_120s_bps"], 120.0)
        self.assertAlmostEqual(outcome["mfe_120s_bps"], 120.0)
        self.assertAlmostEqual(outcome["mae_120s_bps"], 1.0)
        self.assertTrue(outcome["sweep_extreme_revisited"])

    def test_down_direction_symmetrically_signs_returns(self) -> None:
        series = _series([100.0] + [100.0 - index * 0.01 for index in range(1, 121)])
        outcome = label_outcome(series, 0, "DOWN")
        self.assertAlmostEqual(outcome["signed_return_30s_bps"], 30.0)
        self.assertAlmostEqual(outcome["mfe_120s_bps"], 120.0)

    def test_bootstrap_is_reproducible(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        first = moving_block_bootstrap_ci(values, samples=200, seed=7)
        second = moving_block_bootstrap_ci(values, samples=200, seed=7)
        self.assertEqual(first, second)
        self.assertLessEqual(first[0], sum(values) / len(values))
        self.assertGreaterEqual(first[1], sum(values) / len(values))

    def test_overlap_mask_checks_only_requested_window(self) -> None:
        mask = bytearray(20)
        mask[7] = 1
        self.assertFalse(_range_marked(mask, 1, 6))
        self.assertTrue(_range_marked(mask, 7, 10))

    def test_outcome_window_rejects_stale_or_cross_split_data(self) -> None:
        series = _series([100.0] * 200)
        self.assertEqual(_outcome_split(series, {0: "test"}, 0), "test")
        series.stale[50] = 1
        self.assertIsNone(_outcome_split(series, {0: "test"}, 0))

        boundary = _series([100.0] * 200)
        boundary.end_ns = array(
            "q",
            [DAY_NS - 60_000_000_000 + index * 1_000_000_000 for index in range(200)],
        )
        self.assertIsNone(
            _outcome_split(boundary, {0: "calibration", 1: "validation"}, 0)
        )

    def test_quality_gate_requires_clean_committed_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            record = {
                "status": "COMMITTED",
                "active_epoch": 2,
                "segment_id": "one",
                "epoch_segments_committed": 1,
                "epoch_frames": 10,
                "normalization_rejects": 0,
                "book_replay_passed": True,
                "book_replay_venues": {
                    "binance": {"stats": {"gaps": 0, "crossed_books": 0}},
                    "bybit": {"stats": {"gaps": 0, "crossed_books": 0}},
                },
            }
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            passed, details = _quality_gate(path, 2, 1, 10)
            self.assertTrue(passed)
            self.assertEqual(details["committed_segments"], 1)

    def test_short_epoch_releases_no_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames.jsonl"
            with frames.open("w", encoding="utf-8") as handle:
                for index in range(200):
                    handle.write(
                        json.dumps(
                            {
                                "frame_end_ns": (index + 1) * 1_000_000_000,
                                "consolidated_mid": "100",
                                "binance_funding_rate": "0.0001",
                                "venues": {
                                    "binance": {"book_age_ms": 10},
                                    "bybit": {"book_age_ms": 10},
                                },
                            }
                        )
                        + "\n"
                    )
            candidates = root / "candidates.jsonl"
            candidates.write_text("", encoding="utf-8")
            audit = root / "audit.jsonl"
            audit.write_text("", encoding="utf-8")
            report = evaluate_epoch(frames, candidates, audit, 1)
            self.assertEqual(report["status"], "INCONCLUSIVE_INSUFFICIENT_DAYS")
            self.assertFalse(report["sealed_outcomes_released"])


if __name__ == "__main__":
    unittest.main()
