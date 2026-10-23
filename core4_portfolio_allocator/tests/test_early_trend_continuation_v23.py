from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_early_trend_continuation_v23 import (  # noqa: E402
    CANDIDATES,
    Candidate,
    raw_trigger,
    replacement_checks,
    selection_checks,
)


class V23ProtocolTests(unittest.TestCase):
    def test_candidate_registry_is_finite_and_unique(self) -> None:
        names = [candidate.name for candidate in CANDIDATES]
        self.assertEqual(len(names), 10)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual({candidate.confirmations for candidate in CANDIDATES}, {1, 2})

    def test_donchian_trigger_uses_completed_prior_bars(self) -> None:
        frame = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.5, 11.0],
                "high": [11.0, 11.0, 11.5, 12.5],
                "low": [9.0, 9.5, 10.0, 10.5],
                "close": [10.0, 10.5, 11.0, 12.0],
                "atr20": [1.0, 1.0, 1.0, 1.0],
                "high_6": [None, 11.0, 11.0, 11.5],
                "low_6": [None, 9.0, 9.0, 9.0],
            }
        )
        candidate = Candidate("TEST", "DONCHIAN", 6, 1)
        self.assertIsNone(raw_trigger(frame, 2, candidate))
        self.assertEqual(raw_trigger(frame, 3, candidate), "LONG")

    def test_weak_metrics_cannot_pass_selection_or_replacement(self) -> None:
        weak = {
            "trades": 200,
            "trades_per_week": 2.0,
            "win_rate": 0.40,
            "profit_factor": 0.90,
            "mean_r": -0.05,
            "sharpe": -0.20,
            "max_drawdown": -0.05,
        }
        self.assertFalse(all(selection_checks(weak, weak).values()))
        self.assertFalse(all(replacement_checks(weak, weak, weak).values()))


if __name__ == "__main__":
    unittest.main()
