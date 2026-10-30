from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from research import r63c_positioning_liquidation_collector as r63c


class PositioningLiquidationCollectorTests(unittest.TestCase):
    def test_binance_context_and_liquidation(self):
        context = r63c.parse_binance_context(
            {"symbol": "BTCUSDT", "markPrice": "100", "lastFundingRate": "0.001", "time": 1000},
            {"symbol": "BTCUSDT", "openInterest": "200", "time": 1001},
            1010,
        )
        self.assertEqual(context.open_interest, 200)
        event = r63c.parse_binance_liquidation({
            "data": {"e": "forceOrder", "E": 1000, "o": {
                "s": "BTCUSDT", "S": "SELL", "q": "2", "p": "100", "ap": "101", "T": 1000,
            }}
        }, 1010)[0]
        self.assertEqual(event.liquidated_side, "LONG")
        self.assertEqual(event.price, 101)

    def test_bybit_delta_state_and_liquidation(self):
        states = {}
        snapshot = r63c.parse_bybit({
            "topic": "tickers.BTCUSDT", "ts": 1000, "data": {
                "symbol": "BTCUSDT", "markPrice": "100", "openInterest": "200", "fundingRate": "0.001",
            }
        }, 1010, states)[0]
        self.assertEqual(snapshot.mark_price, 100)
        delta = r63c.parse_bybit({
            "topic": "tickers.BTCUSDT", "ts": 1020, "data": {"symbol": "BTCUSDT", "markPrice": "101"}
        }, 1030, states)[0]
        self.assertEqual(delta.open_interest, 200)
        liquidation = r63c.parse_bybit({
            "topic": "allLiquidation.BTCUSDT", "ts": 1040,
            "data": [{"T": 1040, "s": "BTCUSDT", "S": "Buy", "v": "2", "p": "99"}],
        }, 1050, states)[0]
        self.assertEqual(liquidation.liquidated_side, "LONG")

    def test_hyperliquid_context(self):
        event = r63c.parse_hyperliquid({
            "channel": "activeAssetCtx",
            "data": {"coin": "ETH", "ctx": {"markPx": "2000", "openInterest": "50", "funding": "-0.0001"}},
        }, 1000)[0]
        self.assertEqual(event.symbol, "ETHUSDT")
        self.assertEqual(event.funding_rate, -0.0001)

    def test_aggregation_and_duplicate_liquidation(self):
        aggregator = r63c.PositioningAggregator(("BTCUSDT",), grace_seconds=1)
        aggregator.add(r63c.ContextEvent("bybit", "BTCUSDT", 61_000, 61_010, 100, 200, 0.001))
        liquidation = r63c.LiquidationEvent("bybit", "BTCUSDT", 62_000, 62_010, "LONG", 99, 2, "same")
        self.assertTrue(aggregator.add(liquidation))
        self.assertFalse(aggregator.add(liquidation))
        rows = aggregator.pop_closed(122_000)
        self.assertEqual(rows[0]["complete"], 1)
        self.assertEqual(rows[0]["long_liq_notional"], 198)
        self.assertEqual(aggregator.duplicate_liquidations, 1)

    def test_store_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = r63c.PositioningStore(Path(directory) / "positioning.sqlite3")
            cell = r63c.MinutePositioning("bybit", "BTCUSDT", 60_000)
            cell.add(r63c.ContextEvent("bybit", "BTCUSDT", 61_000, 61_010, 100, 200, 0.001))
            store.write([cell.row()])
            replacement = r63c.MinutePositioning("bybit", "BTCUSDT", 60_000)
            replacement.add(r63c.ContextEvent("bybit", "BTCUSDT", 61_500, 61_510, 90, 300, 0.002))
            store.write([replacement.row()])
            mark = store.connection.execute("SELECT mark_close FROM minute_positioning").fetchone()[0]
            self.assertEqual(mark, 100)
            store.close()


if __name__ == "__main__":
    unittest.main()
