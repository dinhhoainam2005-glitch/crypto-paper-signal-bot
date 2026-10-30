from __future__ import annotations

from datetime import datetime, timezone
import unittest

from research import r64a_positioning_flow_dataset as r64a


def synthetic_rows(count: int = 600, step: float = 0.0002):
    rows = []
    price = 100.0
    for index in range(count):
        open_price = price
        close_price = open_price * (1.0 + step)
        high = max(open_price, close_price) * 1.0001
        low = min(open_price, close_price) * 0.9999
        flow = {}
        positioning = {}
        for venue in r64a.VENUES:
            flow[venue] = {
                "trade_count": 10,
                "buy_notional": 600.0,
                "sell_notional": 400.0,
                "midpoint_open": open_price,
                "midpoint_high": high,
                "midpoint_low": low,
                "midpoint_close": close_price,
                "spread_bps": 1.0,
                "book_imbalance": 0.2,
            }
            positioning[venue] = {
                "mark_open": open_price,
                "mark_close": close_price,
                "oi_open": 1000.0 + index,
                "oi_close": 1001.0 + index,
                "funding": 0.0001,
                "long_liq_count": 0,
                "short_liq_count": 0,
                "long_liq_notional": 0.0,
                "short_liq_notional": 0.0,
            }
        rows.append({
            "symbol": "BTCUSDT",
            "minute_start_ms": index * r64a.MINUTE_MS,
            "minute_utc": datetime.fromtimestamp(index * 60, timezone.utc),
            "flow": flow,
            "positioning": positioning,
        })
        price = close_price
    return rows


class PositioningFlowDatasetTests(unittest.TestCase):
    def test_features_use_only_cutoff_history(self):
        rows = synthetic_rows()
        before = r64a.feature_snapshot(rows, 300)
        rows[301]["flow"]["binance-futures"]["buy_notional"] = 1e12
        after = r64a.feature_snapshot(rows, 300)
        self.assertEqual(before, after)
        self.assertGreater(before["oi_change_median_60m"], 0)
        self.assertEqual(before["hyperliquid_funding_hourly_60m"], 0.0001)
        self.assertEqual(before["bybit_funding_hourly_60m"], 0.0001 / 8.0)

    def test_uptrend_receives_long_label(self):
        rows = synthetic_rows(step=0.0005)
        label = r64a.directional_label(rows, 300, "1h")
        self.assertEqual(label["label"], "LONG")
        self.assertEqual(label["ambiguous_touch"], 0)

    def test_same_minute_double_touch_is_no_trade(self):
        rows = synthetic_rows(step=0.0)
        entry = rows[301]["flow"][r64a.REFERENCE_VENUE]["midpoint_open"]
        rows[301]["flow"][r64a.REFERENCE_VENUE]["midpoint_high"] = entry * 1.02
        rows[301]["flow"][r64a.REFERENCE_VENUE]["midpoint_low"] = entry * 0.98
        label = r64a.directional_label(rows, 300, "1h")
        self.assertEqual(label["label"], "NO_TRADE")
        self.assertEqual(label["ambiguous_touch"], 1)

    def test_dense_anchor_schedule(self):
        self.assertTrue(r64a.is_anchor(datetime(2026, 1, 1, 0, 14, tzinfo=timezone.utc), "1h"))
        self.assertFalse(r64a.is_anchor(datetime(2026, 1, 1, 0, 14, tzinfo=timezone.utc), "4h"))
        self.assertTrue(r64a.is_anchor(datetime(2026, 1, 1, 0, 59, tzinfo=timezone.utc), "4h"))


if __name__ == "__main__":
    unittest.main()
