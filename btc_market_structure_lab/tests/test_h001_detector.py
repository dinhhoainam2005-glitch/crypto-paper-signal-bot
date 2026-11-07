import json
import math
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from market_structure.h001_detector import (
    PendingCandidate,
    RollingQuantile,
    _evaluate_pending,
    detect,
    run_file,
)


def frame(index, mid, flow=0, binance_mid=None, bybit_mid=None):
    binance_mid = Decimal(str(mid if binance_mid is None else binance_mid))
    bybit_mid = Decimal(str(mid if bybit_mid is None else bybit_mid))
    return {
        "frame_end_ns": (index + 1) * 1_000_000_000,
        "consolidated_mid": str(mid),
        "binance_minus_bybit_bps": "0",
        "venues": {
            "binance": {
                "mid": str(binance_mid), "book_age_ms": 10,
                "net_aggressive_notional": str(flow), "imbalance_10bps": "0.1",
            },
            "bybit": {
                "mid": str(bybit_mid), "book_age_ms": 10,
                "net_aggressive_notional": "0", "imbalance_10bps": "0.1",
            },
        },
    }


class H001DetectorTests(unittest.TestCase):
    def test_rolling_quantile_matches_nearest_rank(self) -> None:
        rolling = RollingQuantile(Decimal("0.75"), 5)
        values = [Decimal(value) for value in (4, 1, 3, 2, 9, 8, 7)]
        active = []
        for value in values:
            rolling.add(value)
            active.append(value)
            active = active[-5:]
            expected = sorted(active)[math.ceil(0.75 * len(active)) - 1]
            self.assertEqual(rolling.value(), expected)

    def test_rolling_quantile_stays_bounded_across_many_expirations(self) -> None:
        rolling = RollingQuantile(Decimal("0.995"), 64)
        active = []
        for index in range(2_000):
            value = Decimal((index * 37) % 23)
            rolling.add(value)
            active.append(value)
            active = active[-64:]
            expected = sorted(active)[math.ceil(0.995 * len(active)) - 1]
            self.assertEqual(rolling.value(), expected)
        self.assertEqual(len(rolling), 64)
        self.assertEqual(len(rolling.order), 64)
        self.assertEqual(sum(len(block) for block in rolling.blocks), 64)

    def test_rejected_candidate_contains_no_outcome(self) -> None:
        frames = [frame(i, 100 + i, flow=100) for i in range(11)]
        frames.extend(frame(i, 110, flow=1000) for i in range(11, 16))
        pending = PendingCandidate(
            candidate_index=10,
            decision_index=15,
            impulse_sign=1,
            impulse_start_mid=Decimal("100"),
            extreme_mid=Decimal("110"),
            impulse_size=Decimal("10"),
            impulse_flow=Decimal("1000"),
            threshold_bps=Decimal("10"),
            impulse_bps=Decimal("1000"),
        )
        result = _evaluate_pending(pending, frames)
        self.assertFalse(result["accepted"])
        self.assertIn("INSUFFICIENT_RETRACE", result["rejection_reasons"])
        self.assertFalse(result["contains_outcomes"])
        self.assertEqual(result["sweep_extreme_mid"], "110")
        self.assertEqual(result["decision_mid"], "110")

    def test_short_capture_cannot_emit_before_warmup(self) -> None:
        frames = [frame(i, 100 + i / 1000, flow=1) for i in range(100)]
        candidates, manifest = detect(frames)
        self.assertEqual(candidates, [])
        self.assertFalse(manifest["baseline_ready"])

    def test_streaming_file_matches_in_memory_manifest(self) -> None:
        frames = [frame(i, 100 + i / 1000, flow=1) for i in range(100)]
        expected_candidates, expected_manifest = detect(frames)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "h001_frames.jsonl"
            ledger.write_text(
                "".join(json.dumps(item) + "\n" for item in frames),
                encoding="utf-8",
            )
            manifest_path = run_file(ledger)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidates_path = ledger.with_name("h001_candidates.jsonl")
            candidates = [
                json.loads(line)
                for line in candidates_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(candidates, expected_candidates)
        for key, value in expected_manifest.items():
            self.assertEqual(manifest[key], value)


if __name__ == "__main__":
    unittest.main()
