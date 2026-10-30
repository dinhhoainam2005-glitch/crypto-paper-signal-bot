from __future__ import annotations

from datetime import datetime, timezone
import unittest

from research import r64b_frozen_mechanism_screen as r64b


def base_row():
    row = {
        "return_median_5m": 0.001, "return_median_15m": 0.002,
        "return_median_60m": 0.004, "return_median_240m": 0.01,
        "flow_median_5m": 0.2, "flow_median_15m": 0.2,
        "flow_long_votes_5m": 3, "flow_short_votes_5m": 0,
        "flow_long_votes_15m": 3, "flow_short_votes_15m": 0,
        "oi_change_median_15m": 0.001, "oi_change_median_60m": 0.003,
        "book_median_5m": 0.2, "book_median_15m": 0.2,
        "funding_median_60m": 0.000001,
        "spread_median_bps_5m": 1.0, "spread_median_bps_15m": 1.0,
        "binance_futures_liq_pressure_15m": 0.0,
        "bybit_liq_pressure_15m": 0.0,
        "hyperliquid_liq_pressure_15m": 0.0,
        "barrier_bps": 50, "round_trip_cost_bps": 10,
        "label": "LONG", "ambiguous_touch": 0, "terminal_return_bps": 20,
    }
    return row


class FrozenMechanismScreenTests(unittest.TestCase):
    def test_healthy_trend_long(self):
        candidate = r64b.healthy_trend(base_row())
        self.assertEqual(candidate["direction"], "LONG")

    def test_deleveraging_requires_liquidation_and_falling_oi(self):
        row = base_row()
        row["oi_change_median_15m"] = -0.01
        row["bybit_liq_pressure_15m"] = 0.02
        candidate = r64b.deleveraging_continuation(row)
        self.assertEqual(candidate["direction"], "LONG")

    def test_crowded_failure_reverses_direction(self):
        row = base_row()
        row["funding_median_60m"] = 0.00002
        row["flow_median_5m"] = -0.1
        row["book_median_5m"] = -0.1
        row["flow_long_votes_5m"] = 0
        row["flow_short_votes_5m"] = 3
        candidate = r64b.crowded_breakout_failure(row)
        self.assertEqual(candidate["direction"], "SHORT")

    def test_cost_and_ambiguous_touch_are_conservative(self):
        row = base_row()
        self.assertAlmostEqual(r64b.score_candidate(row, "LONG"), 0.8)
        row["ambiguous_touch"] = 1
        self.assertAlmostEqual(r64b.score_candidate(row, "LONG"), -1.2)

    def test_cooldown_removes_overlapping_same_coin_horizon(self):
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            {"symbol": "BTCUSDT", "timeframe": "1h", "entry_time": first},
            {"symbol": "BTCUSDT", "timeframe": "1h", "entry_time": first.replace(minute=15)},
            {"symbol": "ETHUSDT", "timeframe": "1h", "entry_time": first.replace(minute=15)},
        ]
        selected = r64b.apply_cooldown(rows)
        self.assertEqual(len(selected), 2)


if __name__ == "__main__":
    unittest.main()
