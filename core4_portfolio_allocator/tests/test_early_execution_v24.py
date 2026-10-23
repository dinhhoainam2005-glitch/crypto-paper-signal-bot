from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_early_execution_overlay_v24 import (  # noqa: E402
    CONFIGURATIONS,
    viability_checks,
)
from audit_early_execution_tranche_v24 import aggregate_clusters  # noqa: E402


class V24ProtocolTests(unittest.TestCase):
    def test_configuration_registry_is_finite_and_unique(self) -> None:
        names = [configuration.name for configuration in CONFIGURATIONS]
        self.assertEqual(len(names), 4)
        self.assertEqual(len(names), len(set(names)))

    def test_cluster_aggregation_combines_early_and_daily_tranches(self) -> None:
        trades = pd.DataFrame(
            [
                {
                    "cluster_id": "CORE:BTCUSDT:LONG:2024-01-01",
                    "sleeve": "CORE",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "entry_time": "2024-01-01T12:00:00Z",
                    "exit_time": "2024-01-02T00:00:00Z",
                    "net_pnl": 0.002,
                    "initial_risk_cash": 0.001,
                    "tranche": "EARLY",
                    "confirmed": True,
                },
                {
                    "cluster_id": "CORE:BTCUSDT:LONG:2024-01-01",
                    "sleeve": "CORE",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "entry_time": "2024-01-02T00:00:00Z",
                    "exit_time": "2024-01-03T00:00:00Z",
                    "net_pnl": -0.001,
                    "initial_risk_cash": 0.001,
                    "tranche": "DAILY",
                    "confirmed": True,
                },
            ]
        )
        clusters = aggregate_clusters(trades)
        self.assertEqual(len(clusters), 1)
        self.assertAlmostEqual(float(clusters.iloc[0]["realized_r"]), 0.5)
        self.assertEqual(int(clusters.iloc[0]["tranches"]), 2)
        self.assertTrue(bool(clusters.iloc[0]["had_early"]))

    def test_negative_opportunity_fails_viability(self) -> None:
        weak = {
            "early_events": 100,
            "precision": 0.80,
            "coverage": 0.80,
            "median_lead_hours": 12.0,
            "mean_confirmed_advantage_bps": 100.0,
            "mean_opportunity_benefit_bps": -1.0,
        }
        self.assertFalse(all(viability_checks(weak, weak).values()))


if __name__ == "__main__":
    unittest.main()
