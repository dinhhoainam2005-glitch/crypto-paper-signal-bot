from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from research import r63a_continuous_multivenue_collector as r63a


class ContinuousMultivenueCollectorTests(unittest.TestCase):
    def test_binance_normalization(self):
        aggregate = r63a.parse_binance({"data": {"e": "aggTrade", "s": "BTCUSDT", "T": 1000, "p": "100", "q": "2", "m": True, "a": 7}}, 1010)[0]
        self.assertEqual(aggregate.side, "SELL")
        self.assertEqual(aggregate.trade_id, "7")
        raw = r63a.parse_binance({"data": {"e": "trade", "s": "BTCUSDT", "T": 1000, "p": "100", "q": "2", "m": False, "t": 8}}, 1010)[0]
        self.assertEqual(raw.side, "BUY")
        self.assertEqual(raw.trade_id, "8")
        placeholder = {"data": {"e": "trade", "s": "BTCUSDT", "T": 1000, "p": "0", "q": "0", "X": "NA", "t": 9}}
        self.assertTrue(r63a.is_binance_trade_placeholder(placeholder))
        self.assertEqual(r63a.parse_binance(placeholder, 1010), [])
        quote = r63a.parse_binance({"data": {"e": "bookTicker", "s": "BTCUSDT", "E": 1000, "b": "99", "B": "2", "a": "101", "A": "3"}}, 1010)[0]
        self.assertEqual(quote.bid, 99)
        self.assertEqual(quote.ask, 101)

    def test_bybit_snapshot_and_delta(self):
        books = {}
        snapshot = {"topic": "orderbook.1.BTCUSDT", "type": "snapshot", "ts": 1000, "data": {"s": "BTCUSDT", "b": [["99", "2"]], "a": [["101", "3"]]}}
        first = r63a.parse_bybit(snapshot, 1010, books)[0]
        self.assertEqual(first.bid, 99)
        delta = {"topic": "orderbook.1.BTCUSDT", "type": "delta", "ts": 1020, "data": {"s": "BTCUSDT", "b": [["100", "4"]], "a": []}}
        second = r63a.parse_bybit(delta, 1030, books)[0]
        self.assertEqual(second.bid, 100)
        self.assertEqual(second.ask, 101)

    def test_hyperliquid_normalization(self):
        trade = r63a.parse_hyperliquid({"channel": "trades", "data": [{"coin": "ETH", "side": "B", "px": "2000", "sz": "1", "time": 1000, "tid": 9}]}, 1010)[0]
        self.assertEqual(trade.symbol, "ETHUSDT")
        self.assertEqual(trade.side, "BUY")
        quote = r63a.parse_hyperliquid({"channel": "l2Book", "data": {"coin": "ETH", "time": 1000, "levels": [[{"px": "1999", "sz": "2"}], [{"px": "2001", "sz": "3"}]]}}, 1010)[0]
        self.assertEqual(quote.ask, 2001)

    def test_quote_covered_zero_trade_minute_is_complete(self):
        aggregator = r63a.MinuteAggregator(("BTCUSDT",), grace_seconds=1)
        aggregator.add(r63a.QuoteEvent("bybit", "BTCUSDT", 61_000, 61_010, 99, 2, 101, 3))
        rows = aggregator.pop_closed(122_000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_count"], 0)
        self.assertEqual(rows[0]["complete"], 1)

    def test_trade_deduplication(self):
        aggregator = r63a.MinuteAggregator(("BTCUSDT",))
        event = r63a.TradeEvent("bybit", "BTCUSDT", 61_000, 61_010, "BUY", 100, 1, "same")
        self.assertTrue(aggregator.add(event))
        self.assertFalse(aggregator.add(event))
        self.assertEqual(aggregator.duplicates, 1)

    def test_ordering_is_checked_per_event_type(self):
        aggregator = r63a.MinuteAggregator(("BTCUSDT",))
        aggregator.add(r63a.QuoteEvent("bybit", "BTCUSDT", 62_000, 62_010, 99, 2, 101, 3))
        aggregator.add(r63a.TradeEvent("bybit", "BTCUSDT", 61_000, 62_020, "BUY", 100, 1, "one"))
        self.assertEqual(aggregator.out_of_order, 0)
        aggregator.add(r63a.TradeEvent("bybit", "BTCUSDT", 60_500, 62_030, "BUY", 100, 1, "two"))
        self.assertEqual(aggregator.out_of_order, 1)
        self.assertEqual(aggregator.out_of_order_by_stream["bybit:BTCUSDT:trade"], 1)

    def test_reconnect_replay_is_not_counted_as_late(self):
        aggregator = r63a.MinuteAggregator(("BTCUSDT",))
        aggregator.seed_flushed_through({("bybit", "BTCUSDT"): 60_000})
        aggregator.new_generation("bybit", started_ms=120_000)
        replay = r63a.TradeEvent("bybit", "BTCUSDT", 61_000, 121_000, "BUY", 100, 1, "replay")
        self.assertFalse(aggregator.add(replay))
        self.assertEqual(aggregator.replay_events, 1)
        self.assertEqual(aggregator.late_events, 0)
        aggregator.flushed_through[("bybit", "BTCUSDT")] = 120_000
        late = r63a.TradeEvent("bybit", "BTCUSDT", 120_500, 181_000, "BUY", 100, 1, "late")
        self.assertFalse(aggregator.add(late))
        self.assertEqual(aggregator.late_events, 1)

    def test_sqlite_primary_key_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = r63a.SQLiteMinuteStore(root / "flow.sqlite3")
            accumulator = r63a.MinuteAccumulator("bybit", "BTCUSDT", 60_000, 1)
            accumulator.add_quote(r63a.QuoteEvent("bybit", "BTCUSDT", 61_000, 61_010, 99, 2, 101, 3))
            store.write([accumulator.row(), accumulator.row()])
            self.assertEqual(store.count(), 1)
            store.close()
            status = r63a.AtomicStatus(root / "status.json")
            status.save({"ok": True})
            self.assertTrue(json.loads((root / "status.json").read_text())["ok"])

    def test_closed_rows_are_immutable_and_incomplete_rows_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector = r63a.ContinuousCollector(root, ("BTCUSDT",), grace_seconds=1)
            complete = r63a.MinuteAccumulator("bybit", "BTCUSDT", 60_000, 1)
            complete.add_quote(r63a.QuoteEvent("bybit", "BTCUSDT", 61_000, 61_010, 99, 2, 101, 3))
            collector.persist_closed([complete.row()])
            replacement = r63a.MinuteAccumulator("bybit", "BTCUSDT", 60_000, 2)
            replacement.add_quote(r63a.QuoteEvent("bybit", "BTCUSDT", 61_500, 61_510, 89, 2, 91, 3))
            incomplete = r63a.MinuteAccumulator("hyperliquid", "BTCUSDT", 60_000, 1)
            incomplete.add_trade(r63a.TradeEvent("hyperliquid", "BTCUSDT", 61_000, 61_010, "BUY", 100, 1, "one"))
            collector.persist_closed([replacement.row(), incomplete.row()])
            midpoint = collector.store.connection.execute(
                "SELECT midpoint_close FROM minute_flow WHERE venue='bybit' AND symbol='BTCUSDT'"
            ).fetchone()[0]
            self.assertEqual(midpoint, 100)
            self.assertEqual(collector.store.count(), 1)
            self.assertEqual(collector.incomplete_minutes_dropped, 1)
            collector.store.close()


if __name__ == "__main__":
    unittest.main()
