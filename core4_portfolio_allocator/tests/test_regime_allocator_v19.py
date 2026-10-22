from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_regime_allocator_v19 import Policy, policy_allows, training_checks


class RegimeAllocatorV19Tests(unittest.TestCase):
    def test_policy_uses_causal_state_fields(self) -> None:
        date = pd.Timestamp("2024-01-01", tz="UTC")
        context = pd.DataFrame(
            {
                "side": ["LONG"],
                "slope": [1.0],
                "rv20": [0.8],
                "rv_median252": [0.5],
                "breadth": [3.0],
                "trend_aligned": [True],
                "high_vol": [True],
            },
            index=[date],
        )
        policy = Policy(
            "TEST", require_trend=True, volatility="HIGH", min_breadth=2
        )
        self.assertTrue(policy_allows(policy, context, date))
        context.loc[date, "breadth"] = 1.0
        self.assertFalse(policy_allows(policy, context, date))

    def test_training_checks_require_all_quality_dimensions(self) -> None:
        baseline = {
            "trades_per_week": 1.0,
            "profit_factor": 1.5,
            "total_return": 0.10,
            "sharpe": 1.0,
            "max_drawdown": -0.05,
        }
        candidate = {
            "trades_per_week": 1.1,
            "profit_factor": 1.5,
            "total_return": 0.10,
            "sharpe": 1.0,
            "max_drawdown": -0.05,
        }
        self.assertTrue(all(training_checks(baseline, candidate).values()))
        candidate["total_return"] = 0.09
        self.assertFalse(all(training_checks(baseline, candidate).values()))


if __name__ == "__main__":
    unittest.main()
