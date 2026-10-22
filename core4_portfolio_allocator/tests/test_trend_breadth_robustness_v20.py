from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_trend_breadth_robustness_v20 import (
    clustered_trade_bootstrap,
    profit_factor,
    scenario_checks,
)


class TrendBreadthRobustnessV20Tests(unittest.TestCase):
    def test_profit_factor(self) -> None:
        self.assertAlmostEqual(profit_factor(np.array([2.0, -1.0, 1.0])), 3.0)

    def test_scenario_checks(self) -> None:
        metrics = {"profit_factor": 1.3, "mean_r": 0.1, "max_drawdown": -0.07}
        self.assertTrue(all(scenario_checks(metrics).values()))
        metrics["profit_factor"] = 1.29
        self.assertFalse(all(scenario_checks(metrics).values()))

    def test_cluster_bootstrap_is_deterministic(self) -> None:
        trades = pd.DataFrame(
            {
                "entry_time": pd.to_datetime(
                    ["2024-01-01", "2024-01-01", "2024-01-02"], utc=True
                ),
                "realized_r": [1.0, -0.5, 0.5],
            }
        )
        first = clustered_trade_bootstrap(trades)
        second = clustered_trade_bootstrap(trades)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
